"""Read-only pregame policy counterfactual over persisted live operating dates.

Reconstructs each slate strictly as-of the final-pregame decision cutoff from
immutable persisted rows (forecast versions, odds snapshots, starter/lineup
observations, availability events) and evaluates five policies side by side:

A. V1_STRICT_CONTROL              — the existing strict policy, unchanged
B. PROBABLE_STABLE_ONLY           — V1 with only the starter gate governed
C. LINEUP_PENDING_MONEYLINE_ONLY  — V1 with only the lineup gate governed
D. PRACTICAL_V2_COMBINED          — the full V2 challenger policy
E. EXPANDED_MARKET_SCOPE_READ_ONLY— market-scope counts only (no bankroll)

Guarantees: creates no position, mutates no historical decision, uses no
hindsight outcome in any qualification, and makes no profitability claim.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bankroll.service import StakePolicy, StakingStrategy, calculate_stake
from app.core.config import Settings
from app.core.time import local_date_of, local_day_bounds
from app.forecast_exchange.pricing import no_vig_two_sided
from app.models import (
    Forecast,
    ForecastTrack,
    Game,
    GameTeam,
    LineupObservation,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
)
from app.services.league_scope import MLB, in_league
from app.services.evidence_states import (
    STARTER_CONFIRMED,
    lineup_evidence,
    starter_evidence,
    LineupContext,
)
from app.services.practical_policy import (
    LINEUP_PENALTY_FRACTION,
    MIN_ADJUSTED_EDGE,
    PENALIZED_TIER_POSITION_CAP,
    EVIDENCE_STAKE_MULTIPLIER,
    STARTER_PENALTY_FRACTION,
)

COUNTERFACTUAL_VERSION = "AISP_POLICY_COUNTERFACTUAL_V1"
CUTOFF_MINUTES = 15
QUOTE_FRESHNESS_MINUTES = 240

POLICIES = (
    "V1_STRICT_CONTROL",
    "PROBABLE_STABLE_ONLY",
    "LINEUP_PENDING_MONEYLINE_ONLY",
    "PRACTICAL_V2_COMBINED",
)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class PolicyCounterfactualService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def eligible_dates(self) -> list[date]:
        """Live operating dates with at least one current-track forecast."""
        rows = self.session.execute(
            select(Game.starts_at)
            .join(Forecast, Forecast.game_id == Game.id)
            .where(Game.source_mode == "LIVE", Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT)
        )
        return sorted({
            local_date_of(_aware(row[0]), self.settings.display_timezone) for row in rows if row[0]
        })

    def run(self, dates: list[date] | None = None) -> dict[str, Any]:
        dates = [d for d in (dates or self.eligible_dates()) if d is not None]
        results = [self._evaluate_date(day) for day in dates]
        return {
            "version": COUNTERFACTUAL_VERSION,
            "read_only": True,
            "outcome_free": True,
            "note": (
                "No position was created, no historical decision was mutated, no game outcome "
                "entered any qualification, and no profitability is claimed."
            ),
            "dates": results,
            "totals": self._totals(results),
        }

    @staticmethod
    def _totals(results: list[dict[str, Any]]) -> dict[str, Any]:
        totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for day in results:
            for policy, block in day["policies"].items():
                for key in ("qualified", "adjusted_edge_passes", "theoretical_stake_total"):
                    totals[policy][key] += block.get(key, 0) or 0
        market_totals: dict[str, float] = defaultdict(float)
        for day in results:
            for key, value in day.get("expanded_market_scope", {}).items():
                if isinstance(value, (int, float)):
                    market_totals[key] += value
        return {
            "policies": {p: dict(v) for p, v in totals.items()},
            "expanded_market_scope": dict(market_totals),
        }

    # -- per-date evaluation ----------------------------------------------
    def _evaluate_date(self, day: date) -> dict[str, Any]:
        start, end = local_day_bounds(day, self.settings.display_timezone)
        games = list(self.session.scalars(select(Game).where(
            Game.starts_at >= start, Game.starts_at < end, Game.source_mode == "LIVE", in_league(MLB),
        ).order_by(Game.starts_at)))
        policy_blocks = {p: self._empty_policy_block(len(games)) for p in POLICIES}
        market_scope = {"games": len(games), "moneyline_priced": 0, "spread_priced": 0,
                        "total_priced": 0, "merit_pass_any_market": 0}
        for game in games:
            evidence = self._game_evidence(game)
            if evidence is None:
                for block in policy_blocks.values():
                    block["rejections"]["No canonical team identities"] += 1
                continue
            self._count_market_scope(game, evidence, market_scope)
            for policy in POLICIES:
                self._apply_policy(policy, policy_blocks[policy], evidence)
        for block in policy_blocks.values():
            block["rejections"] = dict(sorted(block["rejections"].items(), key=lambda kv: -kv[1])[:8])
        return {"date": day.isoformat(), "games": len(games),
                "policies": policy_blocks, "expanded_market_scope": market_scope}

    @staticmethod
    def _empty_policy_block(games: int) -> dict[str, Any]:
        return {
            "scheduled": games, "valid_forecast": 0, "fresh_complete_quote": 0,
            "starter_full_evidence": 0, "starter_probable_stable": 0,
            "lineup_states": defaultdict(int), "hard_blocks": 0,
            "adjusted_edge_passes": 0, "qualified": 0,
            "theoretical_stake_total": 0.0, "rejections": defaultdict(int),
        }

    # -- evidence reconstruction as-of the cutoff -------------------------
    def _game_evidence(self, game: Game) -> dict[str, Any] | None:
        starts = _aware(game.starts_at)
        cutoff = starts - timedelta(minutes=CUTOFF_MINUTES)
        home_gt = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
        away_gt = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(False)))
        if home_gt is None or away_gt is None:
            return None
        forecast = self.session.scalar(select(Forecast).where(
            Forecast.game_id == game.id,
            Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
            Forecast.generated_at <= cutoff,
        ).order_by(Forecast.generated_at.desc()))
        quotes = self._moneyline_quotes_asof(game, cutoff)
        starter_home = starter_evidence(
            self.session, game, home_gt.team_id, cutoff=cutoff,
            forecast_generated_at=forecast.generated_at if forecast else None,
        )
        starter_away = starter_evidence(
            self.session, game, away_gt.team_id, cutoff=cutoff,
            forecast_generated_at=forecast.generated_at if forecast else None,
        )
        lineup_home = self._latest_lineup(game, home_gt.team_id, cutoff)
        lineup_away = self._latest_lineup(game, away_gt.team_id, cutoff)
        return {
            "game": game, "cutoff": cutoff, "forecast": forecast,
            "home_gt": home_gt, "away_gt": away_gt,
            "starter_home": starter_home, "starter_away": starter_away,
            "lineup_home": lineup_home, "lineup_away": lineup_away,
            "quotes": quotes,
        }

    def _latest_lineup(self, game: Game, team_id: Any, cutoff: datetime) -> LineupObservation | None:
        return self.session.scalar(select(LineupObservation).where(
            LineupObservation.game_id == game.id,
            LineupObservation.team_id == team_id,
            LineupObservation.available_as_of <= cutoff,
        ).order_by(LineupObservation.available_as_of.desc()))

    def _snapshots_asof(self, game: Game, market_type: MarketType, cutoff: datetime):
        """Latest snapshot per (outcome, book) at/before the cutoff, fresh only."""
        fresh_floor = cutoff - timedelta(minutes=QUOTE_FRESHNESS_MINUTES)
        rows = list(self.session.execute(
            select(OddsSnapshot, MarketOutcome)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .where(
                Market.game_id == game.id,
                Market.market_type == market_type,
                OddsSnapshot.observed_at <= cutoff,
                OddsSnapshot.observed_at >= fresh_floor,
                OddsSnapshot.source_mode == game.source_mode,
            ).order_by(OddsSnapshot.observed_at.desc())
        ))
        latest: dict[tuple[Any, Any], tuple[OddsSnapshot, MarketOutcome]] = {}
        for snapshot, outcome in rows:
            latest.setdefault((outcome.id, snapshot.sportsbook_id), (snapshot, outcome))
        return latest

    def _moneyline_quotes_asof(self, game: Game, cutoff: datetime) -> list[dict[str, Any]]:
        """Two-sided same-book moneyline pairs as-of the cutoff."""
        latest = self._snapshots_asof(game, MarketType.MONEYLINE, cutoff)
        by_book: dict[Any, dict[Any, tuple[OddsSnapshot, MarketOutcome]]] = defaultdict(dict)
        for (outcome_id, book_id), pair in latest.items():
            by_book[book_id][outcome_id] = pair
        quotes = []
        for book_id, sides in by_book.items():
            if len(sides) != 2:
                continue
            (snap_a, out_a), (snap_b, out_b) = sides.values()
            if snap_a.american_odds is None or snap_b.american_odds is None:
                continue
            quotes.append({
                "book_id": book_id,
                "sides": [
                    {"outcome": out_a, "snapshot": snap_a},
                    {"outcome": out_b, "snapshot": snap_b},
                ],
            })
        return quotes

    def _count_market_scope(self, game: Game, evidence: dict[str, Any], scope: dict[str, Any]) -> None:
        cutoff = evidence["cutoff"]
        if evidence["quotes"]:
            scope["moneyline_priced"] += 1
        spread = self._snapshots_asof(game, MarketType.SPREAD, cutoff)
        total = self._snapshots_asof(game, MarketType.TOTAL, cutoff)
        if spread:
            scope["spread_priced"] += 1
        if total:
            scope["total_priced"] += 1
        best = self._best_side(evidence)
        if best is not None and best["raw_edge"] is not None and best["raw_edge"] > 0:
            scope["merit_pass_any_market"] += 1

    # -- side pricing ------------------------------------------------------
    def _best_side(self, evidence: dict[str, Any]) -> dict[str, Any] | None:
        forecast = evidence["forecast"]
        if forecast is None or not evidence["quotes"]:
            return None
        home_team_id = evidence["home_gt"].team_id
        best: dict[str, Any] | None = None
        for quote in evidence["quotes"]:
            sides = quote["sides"]
            for idx, side in enumerate(sides):
                other = sides[1 - idx]
                outcome: MarketOutcome = side["outcome"]
                snap: OddsSnapshot = side["snapshot"]
                is_home = outcome.participant_team_id == home_team_id
                model_p = float(forecast.home_win_probability if is_home else forecast.away_win_probability or 0)
                implied = float(snap.implied_probability or 0) or None
                other_implied = float(other["snapshot"].implied_probability or 0) or None
                if implied is None or other_implied is None or snap.decimal_odds is None:
                    continue
                no_vig = no_vig_two_sided(implied, other_implied)
                conservative = max(0.0, model_p - float(forecast.uncertainty or 0.0))
                entry = {
                    "model_p": model_p, "no_vig": no_vig,
                    "decimal_odds": float(snap.decimal_odds),
                    "raw_edge": model_p - no_vig,
                    "unc_adjusted_edge": conservative - no_vig,
                    "conservative": conservative,
                }
                if best is None or entry["unc_adjusted_edge"] > best["unc_adjusted_edge"]:
                    best = entry
        return best

    # -- policy application ------------------------------------------------
    def _apply_policy(self, policy: str, block: dict[str, Any], evidence: dict[str, Any]) -> None:
        forecast = evidence["forecast"]
        starter_home, starter_away = evidence["starter_home"], evidence["starter_away"]
        lineup_home, lineup_away = evidence["lineup_home"], evidence["lineup_away"]

        def reject(reason: str) -> None:
            block["hard_blocks"] += 1
            block["rejections"][reason] += 1

        if forecast is None:
            reject("Forecast unavailable at cutoff")
            return
        block["valid_forecast"] += 1
        best = self._best_side(evidence)
        if best is None:
            reject("No fresh complete two-sided moneyline quote at cutoff")
            return
        block["fresh_complete_quote"] += 1

        both_confirmed = starter_home.state == STARTER_CONFIRMED and starter_away.state == STARTER_CONFIRMED
        both_stable = starter_home.eligible and starter_away.eligible
        if both_confirmed:
            block["starter_full_evidence"] += 1
        elif both_stable:
            block["starter_probable_stable"] += 1
        lineup_present = lineup_home is not None and lineup_away is not None
        lineup_confirmed = (
            lineup_present
            and (lineup_home.lineup_status or "").upper() == "CONFIRMED" and bool(lineup_home.is_complete)
            and (lineup_away.lineup_status or "").upper() == "CONFIRMED" and bool(lineup_away.is_complete)
        )
        lineup_state = (
            "CONFIRMED_COMPLETE" if lineup_confirmed
            else "ADEQUATE_PARTIAL" if lineup_present
            else "PENDING"
        )
        block["lineup_states"][lineup_state] = block["lineup_states"].get(lineup_state, 0) + 1

        starter_gate_governed = policy in ("PROBABLE_STABLE_ONLY", "PRACTICAL_V2_COMBINED")
        lineup_gate_governed = policy in ("LINEUP_PENDING_MONEYLINE_ONLY", "PRACTICAL_V2_COMBINED")

        if starter_gate_governed:
            if not both_stable:
                reject("Starter evidence not CONFIRMED/PROBABLE_STABLE")
                return
        else:
            if not both_confirmed:
                reject("Starting pitcher not CONFIRMED")
                return
        if not lineup_gate_governed and not lineup_present:
            reject("Lineup missing")
            return
        if policy == "V1_STRICT_CONTROL" and (forecast.readiness_score or 0) < self.settings.opportunity_qualified_readiness:
            reject(f"Readiness below {self.settings.opportunity_qualified_readiness:.0f}")
            return
        if best["raw_edge"] >= self.settings.opportunity_extreme_disagreement_threshold:
            reject("Extreme model-market disagreement")
            return

        starter_penalty = STARTER_PENALTY_FRACTION if (starter_gate_governed and not both_confirmed) else 0.0
        lineup_penalty = LINEUP_PENALTY_FRACTION if (lineup_gate_governed and not lineup_present) else 0.0
        adjusted_p = max(0.0, best["conservative"] - starter_penalty - lineup_penalty)
        adjusted_edge = adjusted_p - best["no_vig"]
        adjusted_ev = adjusted_p * (best["decimal_odds"] - 1.0) - (1.0 - adjusted_p)
        if adjusted_edge < MIN_ADJUSTED_EDGE:
            reject("Adjusted edge below minimum after penalties")
            return
        block["adjusted_edge_passes"] += 1
        if adjusted_ev <= 0:
            reject("Adjusted EV not positive")
            return
        block["qualified"] += 1
        penalized = starter_penalty > 0 or lineup_penalty > 0
        cap = PENALIZED_TIER_POSITION_CAP if penalized else self.settings.shadow_max_position_percentage
        stake = calculate_stake(
            float(self.settings.shadow_virtual_bankroll),
            StakePolicy(
                strategy=StakingStrategy.FRACTIONAL_KELLY,
                kelly_fraction=self.settings.shadow_kelly_fraction,
                maximum_stake_percentage=cap,
            ),
            model_probability=adjusted_p,
            decimal_odds=best["decimal_odds"],
        ) * (EVIDENCE_STAKE_MULTIPLIER if penalized else 1.0)
        block["theoretical_stake_total"] = round(block["theoretical_stake_total"] + min(
            stake, float(self.settings.shadow_virtual_bankroll) * cap
        ), 2)


__all__ = ["PolicyCounterfactualService", "COUNTERFACTUAL_VERSION"]
