"""Forward $1,000 virtual-bankroll shadow experiment.

A genuine forward-looking research experiment: it freezes the exact governed
price behind each game-day decision, only ever creates a virtual position from a
FINAL_PREGAME QUALIFIED_BET, settles idempotently from real game finals, and
tracks the bankroll before/after every settlement. Nothing here is executable
and no price is fabricated — a missing governed quote is a real NO_BET at zero
stake.

The primary bankroll instrument is the full-game moneyline on the model-favored
side (the same conservative instrument the official-position path uses), sized by
the governed quarter-Kelly policy with 1% / 2% / 5% position / same-game / daily
caps. Which games are eligible is decided by the existing opportunity board:
a primary position is frozen only for a game the board rates QUALIFIED_BET.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bankroll.positions import _moneyline_candidates
from app.bankroll.service import (
    SettlementResult,
    StakePolicy,
    StakingStrategy,
    calculate_stake,
    settle_profit,
)
from app.bankroll.settlement import determine_result
from app.core.config import Settings
from app.markets.odds import decimal_to_american
from app.models import (
    ExperimentDecisionPhase,
    Forecast,
    Game,
    GameStatus,
    Market,
    MarketOutcome,
    OddsSnapshot,
    PositionResult,
    ShadowExperiment,
    ShadowExperimentDecision,
    ShadowExperimentSettlement,
)

FINAL_PREGAME_CUTOFF_MINUTES = 15
DECISION_POLICY = "AISP_SHADOW_BANKROLL_V1"
AISP_BASELINE_ENGINE = "AISP_BASELINE"
SUPPORTED_MARKETS = ["MONEYLINE"]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass
class _MoneylineBet:
    outcome: MarketOutcome
    snapshot: OddsSnapshot
    book_name: str
    selection: str
    model_probability: float
    no_vig_probability: float
    edge: float
    decimal_odds: float
    american_odds: int
    observed_at: datetime


class ShadowExperimentService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- experiment start authority --------------------------------------
    def active_experiment(self) -> ShadowExperiment | None:
        return self.session.scalar(
            select(ShadowExperiment).where(ShadowExperiment.is_active.is_(True))
            .order_by(ShadowExperiment.version.desc()).limit(1)
        )

    def ensure_started(
        self, *, start_date: date, now: datetime | None = None, code_commit: str | None = None,
    ) -> ShadowExperiment:
        """Create the immutable experiment start record once; idempotent.

        The start date, bankroll and policy are never rewritten — a later policy
        change must create a new version instead."""

        existing = self.active_experiment()
        if existing is not None:
            return existing
        now = now or datetime.now(UTC)
        next_version = (self.session.scalar(select(ShadowExperiment.version).order_by(ShadowExperiment.version.desc()).limit(1)) or 0) + 1
        experiment = ShadowExperiment(
            version=next_version,
            started_at=now,
            start_date=start_date,
            starting_bankroll=self.settings.shadow_virtual_bankroll,
            decision_policy=DECISION_POLICY,
            controlling_engine=AISP_BASELINE_ENGINE,
            supported_markets=list(SUPPORTED_MARKETS),
            kelly_fraction=self.settings.shadow_kelly_fraction,
            unit_percentage=self.settings.shadow_unit_percentage,
            max_position_percentage=self.settings.shadow_max_position_percentage,
            max_same_game_percentage=self.settings.shadow_max_same_game_percentage,
            max_daily_percentage=self.settings.shadow_max_daily_percentage,
            code_commit=code_commit,
            is_active=True,
        )
        self.session.add(experiment)
        self.session.flush()
        return experiment

    # -- bankroll accounting ---------------------------------------------
    def _settlements(self, experiment: ShadowExperiment) -> list[ShadowExperimentSettlement]:
        return list(self.session.scalars(
            select(ShadowExperimentSettlement)
            .join(ShadowExperimentDecision, ShadowExperimentDecision.id == ShadowExperimentSettlement.decision_id)
            .where(ShadowExperimentDecision.experiment_id == experiment.id)
            .order_by(ShadowExperimentSettlement.settled_at)
        ))

    def _realized_profit(self, experiment: ShadowExperiment) -> float:
        return sum(float(row.profit_loss) for row in self._settlements(experiment))

    def _reserved_open_stake(self, experiment: ShadowExperiment) -> float:
        settled_ids = {
            row.decision_id for row in self.session.scalars(
                select(ShadowExperimentSettlement).join(
                    ShadowExperimentDecision, ShadowExperimentDecision.id == ShadowExperimentSettlement.decision_id
                ).where(ShadowExperimentDecision.experiment_id == experiment.id)
            )
        }
        return sum(
            float(row.virtual_stake) for row in self.session.scalars(
                select(ShadowExperimentDecision).where(
                    ShadowExperimentDecision.experiment_id == experiment.id,
                    ShadowExperimentDecision.is_primary.is_(True),
                )
            ) if row.id not in settled_ids
        )

    def current_bankroll(self, experiment: ShadowExperiment) -> float:
        return float(experiment.starting_bankroll) + self._realized_profit(experiment)

    def _available_bankroll(self, experiment: ShadowExperiment) -> float:
        return max(0.0, self.current_bankroll(experiment) - self._reserved_open_stake(experiment))

    # -- freezing decisions ----------------------------------------------
    def freeze(
        self, event_date: date, *, phase: ExperimentDecisionPhase, now: datetime | None = None,
    ) -> dict[str, Any]:
        experiment = self.active_experiment()
        if experiment is None:
            return {"note": "no active experiment", "frozen": 0, "primary": 0}
        now = now or datetime.now(UTC)

        from app.services.read_service import ReadService

        read = ReadService(self.session, self.settings)
        games = read._live_games_for_date(event_date)
        if not games:
            return {"note": "no live games", "frozen": 0, "primary": 0}
        cards = [read.game_card(game) for game in games]
        forecasts = [read._latest_forecast(game.id) for game in games]
        state_by_game = self._game_states(read, games, cards, forecasts)

        policy = StakePolicy(
            strategy=StakingStrategy.FRACTIONAL_KELLY,
            kelly_fraction=experiment.kelly_fraction,
            maximum_stake_percentage=experiment.max_position_percentage,
        )
        bankroll = self._available_bankroll(experiment)
        same_game_cap = bankroll * experiment.max_same_game_percentage
        daily_cap = bankroll * experiment.max_daily_percentage
        daily_used = self._daily_primary_stake(experiment, event_date)

        frozen = 0
        primary_count = 0
        for game, card, forecast in zip(games, cards, forecasts):
            if forecast is None:
                continue  # no valid forecast yet — nothing to freeze at this phase
            if phase == ExperimentDecisionPhase.LINEUP_CONFIRMED and not self._lineup_confirmed(card):
                continue  # capture this phase only once the lineup is actually confirmed
            if phase == ExperimentDecisionPhase.FINAL_PREGAME and now > _aware(game.starts_at) - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES) and self._decision(experiment, game.id, phase) is None:
                continue  # missed the final-pregame window — never substitute a later price
            state = state_by_game.get(str(game.id), "NO_BET")
            bet = self._best_moneyline(game, forecast)
            existing = self._decision(experiment, game.id, phase)
            # Idempotency: MORNING / LINEUP_CONFIRMED capture the FIRST valid
            # snapshot; FINAL_PREGAME may update only until the cutoff, then freezes.
            if existing is not None and not self._may_update(existing, phase, game, now):
                continue

            eligible_primary = (
                phase == ExperimentDecisionPhase.FINAL_PREGAME
                and state == "QUALIFIED_BET"
                and bet is not None
                and bet.edge > 0
                and now <= _aware(game.starts_at) - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
            )
            stake = 0.0
            if eligible_primary:
                stake = calculate_stake(
                    bankroll, policy,
                    model_probability=bet.model_probability, decimal_odds=bet.decimal_odds,
                )
                stake = min(stake, same_game_cap, max(0.0, daily_cap - daily_used))
                stake = round(stake, 2)
                daily_used += stake
            is_primary = eligible_primary and stake > 0

            self._upsert_decision(
                experiment, game, forecast, phase, state, bet, stake, is_primary, now, existing,
            )
            frozen += 1
            if is_primary:
                primary_count += 1

        self.session.commit()
        return {"frozen": frozen, "primary": primary_count, "phase": phase.value, "games": len(games)}

    def _game_states(self, read: Any, games: list[Game], cards: list[Any], forecasts: list[Any]) -> dict[str, str]:
        """The authoritative per-game recommendation state from the opportunity board."""
        opportunities, _ = read._daily_opportunities(games, cards, forecasts)
        return {opp.game_id: opp.recommendation_state for opp in opportunities}

    def _daily_primary_stake(self, experiment: ShadowExperiment, event_date: date) -> float:
        return sum(
            float(row.virtual_stake) for row in self.session.scalars(
                select(ShadowExperimentDecision).where(
                    ShadowExperimentDecision.experiment_id == experiment.id,
                    ShadowExperimentDecision.slate_date == event_date,
                    ShadowExperimentDecision.is_primary.is_(True),
                )
            )
        )

    @staticmethod
    def _lineup_confirmed(card: Any) -> bool:
        away = card.away_lineup.status if getattr(card, "away_lineup", None) else None
        home = card.home_lineup.status if getattr(card, "home_lineup", None) else None
        return away == "CONFIRMED" and home == "CONFIRMED"

    def _may_update(self, existing: ShadowExperimentDecision, phase: ExperimentDecisionPhase, game: Game, now: datetime) -> bool:
        if phase != ExperimentDecisionPhase.FINAL_PREGAME:
            return False  # MORNING / LINEUP_CONFIRMED are captured once
        # Do not substitute a later or post-start price once the cutoff passes.
        return now <= _aware(game.starts_at) - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)

    def _decision(self, experiment: ShadowExperiment, game_id: Any, phase: ExperimentDecisionPhase) -> ShadowExperimentDecision | None:
        return self.session.scalar(select(ShadowExperimentDecision).where(
            ShadowExperimentDecision.experiment_id == experiment.id,
            ShadowExperimentDecision.game_id == game_id,
            ShadowExperimentDecision.market == "MONEYLINE",
            ShadowExperimentDecision.phase == phase,
        ))

    def _upsert_decision(
        self, experiment, game, forecast, phase, state, bet, stake, is_primary, now, existing,
    ) -> ShadowExperimentDecision:
        version = f"{forecast.lifecycle.value.upper()} · v{forecast.version}" if forecast else None
        values = dict(
            slate_date=game.starts_at.date() if game.starts_at else now.date(),
            sport="BASEBALL_MLB",
            market="MONEYLINE",
            period="Full game",
            selection=bet.selection if bet else "—",
            line=None,
            sportsbook=bet.book_name if bet else None,
            american_odds=bet.american_odds if bet else None,
            decimal_odds=bet.decimal_odds if bet else None,
            market_outcome_id=bet.outcome.id if bet else None,
            odds_snapshot_id=bet.snapshot.id if bet else None,
            quote_observed_at=bet.observed_at if bet else None,
            forecast_id=forecast.id if forecast else None,
            forecast_generated_at=forecast.generated_at if forecast else None,
            forecast_version=version,
            recommended_at=now,
            model_probability=bet.model_probability if bet else None,
            no_vig_probability=bet.no_vig_probability if bet else None,
            edge=bet.edge if bet else None,
            expected_value=(bet.model_probability * bet.decimal_odds - 1.0) if bet else None,
            readiness=forecast.readiness_score if forecast else None,
            uncertainty=forecast.uncertainty if forecast else None,
            recommendation=state if bet is not None else "NO_BET",
            virtual_stake=stake,
            is_primary=is_primary,
            source_mode=game.source_mode,
        )
        if existing is not None:
            for key, value in values.items():
                setattr(existing, key, value)
            return existing
        decision = ShadowExperimentDecision(
            experiment_id=experiment.id, game_id=game.id, phase=phase, **values,
        )
        self.session.add(decision)
        self.session.flush()
        return decision

    def _best_moneyline(self, game: Game, forecast: Forecast | None) -> _MoneylineBet | None:
        if forecast is None:
            return None
        best: _MoneylineBet | None = None
        for candidate in _moneyline_candidates(self.session, game.id):
            model_p = forecast.home_win_probability if candidate["is_home"] else forecast.away_win_probability
            no_vig = candidate["no_vig_probability"]
            edge = model_p - no_vig
            snapshot = candidate["snapshot"]
            if snapshot.decimal_odds is None:
                continue
            american = snapshot.american_odds if snapshot.american_odds is not None else decimal_to_american(snapshot.decimal_odds)
            bet = _MoneylineBet(
                outcome=candidate["outcome"], snapshot=snapshot, book_name=candidate["book"].name,
                selection=f"{candidate['outcome'].label} ML" if candidate["outcome"].label else "Moneyline",
                model_probability=model_p, no_vig_probability=no_vig, edge=edge,
                decimal_odds=snapshot.decimal_odds, american_odds=int(american),
                observed_at=_aware(snapshot.observed_at),
            )
            if best is None or bet.edge > best.edge:
                best = bet
        return best

    # -- settlement ------------------------------------------------------
    def settle(self, event_date: date | None = None, *, now: datetime | None = None) -> dict[str, Any]:
        experiment = self.active_experiment()
        if experiment is None:
            return {"note": "no active experiment", "settled": 0}
        now = now or datetime.now(UTC)
        settled_ids = {row.decision_id for row in self.session.scalars(
            select(ShadowExperimentSettlement).join(
                ShadowExperimentDecision, ShadowExperimentDecision.id == ShadowExperimentSettlement.decision_id
            ).where(ShadowExperimentDecision.experiment_id == experiment.id)
        )}
        primaries = list(self.session.scalars(
            select(ShadowExperimentDecision).where(
                ShadowExperimentDecision.experiment_id == experiment.id,
                ShadowExperimentDecision.is_primary.is_(True),
            ).order_by(ShadowExperimentDecision.recommended_at)
        ))
        running = self.current_bankroll(experiment)
        settled = 0
        for decision in primaries:
            if decision.id in settled_ids:
                continue
            game = self.session.get(Game, decision.game_id)
            if game is None or game.status != GameStatus.FINAL or game.home_score is None or game.away_score is None:
                continue
            outcome = self.session.get(MarketOutcome, decision.market_outcome_id) if decision.market_outcome_id else None
            market = self.session.get(Market, outcome.market_id) if outcome else None
            if outcome is None or market is None:
                continue
            result = determine_result(self.session, game, market, outcome)
            profit = settle_profit(float(decision.virtual_stake), decimal_odds=decision.decimal_odds, result=SettlementResult(result.value))
            before = running
            after = round(before + profit, 2)
            running = after
            closing = self._closing_snapshot(decision, game)
            clv, beat_close, closing_american, closing_decimal = self._closing_line_value(decision, closing)
            self.session.add(ShadowExperimentSettlement(
                decision_id=decision.id, result=result.value.upper(), profit_loss=round(profit, 2),
                bankroll_before=round(before, 2), bankroll_after=after,
                closing_american=closing_american, closing_decimal=closing_decimal,
                closing_line_value=clv, beat_close=beat_close, settled_at=now,
            ))
            settled += 1
        self.session.commit()
        return {"settled": settled, "current_bankroll": round(running, 2)}

    def _closing_snapshot(self, decision: ShadowExperimentDecision, game: Game) -> OddsSnapshot | None:
        """The last governed price for the same selection at/before first pitch."""
        if decision.market_outcome_id is None:
            return None
        first_pitch = _aware(game.starts_at)
        return self.session.scalar(
            select(OddsSnapshot).where(
                OddsSnapshot.market_outcome_id == decision.market_outcome_id,
                OddsSnapshot.observed_at <= first_pitch,
            ).order_by(OddsSnapshot.observed_at.desc()).limit(1)
        )

    # -- read/ledger aggregation -----------------------------------------
    def ledger_view(self, experiment: ShadowExperiment) -> dict[str, Any]:
        """Aggregate the experiment ledger for the bankroll + scorecard reads."""
        primaries = list(self.session.scalars(
            select(ShadowExperimentDecision).where(
                ShadowExperimentDecision.experiment_id == experiment.id,
                ShadowExperimentDecision.is_primary.is_(True),
            ).order_by(ShadowExperimentDecision.recommended_at)
        ))
        settlements = {row.decision_id: row for row in self.session.scalars(
            select(ShadowExperimentSettlement).join(
                ShadowExperimentDecision, ShadowExperimentDecision.id == ShadowExperimentSettlement.decision_id
            ).where(ShadowExperimentDecision.experiment_id == experiment.id)
        )}
        starting = float(experiment.starting_bankroll)
        balance = starting
        peak = starting
        max_dd = 0.0
        streak = 0
        longest_streak = 0
        wins = losses = pushes = voids = 0
        total_risked = 0.0
        clvs: list[float] = []
        beats = 0
        rows: list[dict[str, Any]] = []
        equity = [{"date": experiment.start_date.isoformat(), "bankroll": round(starting, 2), "benchmark": round(starting, 2)}]
        monthly: dict[str, dict[str, Any]] = {}
        breakdowns: dict[str, dict[str, dict[str, float]]] = {"tier": {}, "market": {}, "month": {}, "engine": {}, "sportsbook": {}}
        for decision in primaries:
            settlement = settlements.get(decision.id)
            stake = float(decision.virtual_stake)
            total_risked += stake
            result = settlement.result if settlement else "OPEN"
            profit = float(settlement.profit_loss) if settlement else 0.0
            if settlement is not None:
                balance = float(settlement.bankroll_after)
                peak = max(peak, balance)
                max_dd = max(max_dd, peak - balance)
                if result == "WIN":
                    wins += 1
                    streak = 0
                elif result == "LOSS":
                    losses += 1
                    streak += 1
                    longest_streak = max(longest_streak, streak)
                elif result == "PUSH":
                    pushes += 1
                else:
                    voids += 1
                if settlement.closing_line_value is not None:
                    clvs.append(float(settlement.closing_line_value))
                if settlement.beat_close:
                    beats += 1
                equity.append({"date": decision.slate_date.isoformat(), "bankroll": round(balance, 2), "benchmark": round(starting, 2)})
            month = decision.slate_date.strftime("%Y-%m")
            for dim, key in (("tier", decision.recommendation), ("market", decision.market), ("month", month),
                             ("engine", experiment.controlling_engine), ("sportsbook", decision.sportsbook or "—")):
                bucket = breakdowns[dim].setdefault(key, {"positions": 0, "settled": 0, "wins": 0, "losses": 0, "pushes": 0, "voids": 0, "profit_loss": 0.0, "staked": 0.0})
                bucket["positions"] += 1
                bucket["staked"] += stake
                if settlement is not None:
                    bucket["settled"] += 1
                    bucket["profit_loss"] += profit
                    bucket[{"WIN": "wins", "LOSS": "losses", "PUSH": "pushes", "VOID": "voids"}.get(result, "voids")] += 1
            mbucket = monthly.setdefault(month, {"month": month, "positions": 0, "wins": 0, "losses": 0, "pushes": 0, "profit_loss": 0.0, "stake": 0.0})
            mbucket["positions"] += 1
            mbucket["stake"] += stake
            mbucket["profit_loss"] += profit
            rows.append({
                "id": str(decision.id), "date": decision.slate_date, "game_id": str(decision.game_id),
                "selection": decision.selection, "market": decision.market.title(),
                "price": decision.american_odds or 0, "stake": round(stake, 2),
                "result": result, "profit_loss": round(profit, 2),
                "closing_price": settlement.closing_american if settlement else None,
                "clv": settlement.closing_line_value if settlement else None,
            })
        settled_count = len(settlements)
        net = balance - starting
        roi = (net / total_risked * 100) if total_risked else None
        slate_dates = set(self.session.scalars(select(ShadowExperimentDecision.slate_date).where(ShadowExperimentDecision.experiment_id == experiment.id)))
        primary_dates = {d.slate_date for d in primaries}
        no_bet_days = len(slate_dates - primary_dates)
        betting = {
            "starting_bankroll": round(starting, 2), "current_bankroll": round(balance, 2),
            "net_profit_loss": round(net, 2), "total_risked": round(total_risked, 2),
            "roi": round(roi, 2) if roi is not None else None,
            "yield_pct": round(roi, 2) if roi is not None else None,
            "positions": len(primaries), "settled": settled_count, "open": len(primaries) - settled_count,
            "wins": wins, "losses": losses, "pushes": pushes, "voids": voids,
            "record": f"{wins}–{losses}–{pushes}",
            "max_drawdown": round(max_dd, 2), "longest_losing_streak": longest_streak,
            "closing_line_value": round(sum(clvs) / len(clvs), 3) if clvs else None,
            "beat_close_percentage": round(beats / settled_count * 100, 1) if settled_count else None,
            "has_settled": settled_count > 0,
            "breakdowns": {
                dim: [
                    {"key": key, "positions": b["positions"], "settled": b["settled"],
                     "record": f"{b['wins']}–{b['losses']}–{b['pushes']}",
                     "profit_loss": round(b["profit_loss"], 2), "staked": round(b["staked"], 2),
                     "roi": round(b["profit_loss"] / b["staked"] * 100, 2) if b["staked"] and b["settled"] else None}
                    for key, b in sorted(dim_map.items())
                ]
                for dim, dim_map in breakdowns.items()
            },
        }
        monthly_rows = [{
            "month": item["month"], "positions": item["positions"],
            "record": f"{item['wins']}–{item['losses']}–{item['pushes']}",
            "profit_loss": round(item["profit_loss"], 2),
            "roi": item["profit_loss"] / item["stake"] * 100 if item["stake"] else 0,
        } for item in monthly.values()]
        return {
            "starting": starting, "current": round(balance, 2), "peak": round(peak, 2),
            "net": round(net, 2), "roi": roi or 0.0, "max_drawdown": round(max_dd, 2),
            "record": f"{wins}–{losses}–{pushes}", "positions": rows,
            "equity": equity, "monthly": monthly_rows, "betting": betting,
            "open_positions": len(primaries) - settled_count, "settled_positions": settled_count,
            "no_bet_days": no_bet_days,
        }

    def _closing_line_value(self, decision, closing):
        if closing is None or closing.decimal_odds is None or not decision.decimal_odds:
            return None, None, None, None
        closing_decimal = closing.decimal_odds
        closing_american = int(closing.american_odds) if closing.american_odds is not None else int(decimal_to_american(closing_decimal))
        # CLV: our locked decimal odds vs the closing decimal odds. A higher price
        # than the close is positive CLV — we beat the closing line.
        clv = round((decision.decimal_odds / closing_decimal - 1.0) * 100, 3)
        beat_close = decision.decimal_odds > closing_decimal
        return clv, beat_close, closing_american, closing_decimal
