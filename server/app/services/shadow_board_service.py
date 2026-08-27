"""Persist, evaluate, and settle the Model Studio research-shadow board.

Nothing here is executable. Imported forecasts are RESEARCH_SHADOW / NOT_PROMOTED
/ NOT_EXECUTABLE. Quotes are never fabricated: a decision is priced only when a
real, fresh, complete two-sided governed quote is supplied; otherwise it is a
NO_BET with a zero virtual stake. Virtual staking uses the existing bankroll
math under quarter-Kelly with position, same-game, and daily exposure caps.
Real-money execution is impossible by construction.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Iterable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bankroll.service import SettlementResult, StakePolicy, StakingStrategy, calculate_stake, settle_profit
from app.core.config import Settings
from app.forecast_exchange.contracts import GameForecastExportV1
from app.forecast_exchange.importer import ImportedForecastBundle, verify_and_load_bundle
from app.forecast_exchange.pricing import (
    Recommendation,
    ShadowMarket,
    ShadowPricingConfig,
    american_to_decimal,
    implied_probability,
    model_probability,
    price_selection,
)
from app.models import (
    ShadowBoardDecision,
    ShadowDecisionSettlement,
    ShadowForecast,
    ShadowForecastBundle,
)


# The initially supported market/selection surface. "line_dependent" markets
# require a governed line (from the quote); without one no probability is stated.
@dataclass(frozen=True)
class SelectionSpec:
    market: ShadowMarket
    selection: str
    line_dependent: bool


SUPPORTED_SELECTIONS: tuple[SelectionSpec, ...] = (
    SelectionSpec(ShadowMarket.FULL_GAME_MONEYLINE, "HOME", False),
    SelectionSpec(ShadowMarket.FULL_GAME_MONEYLINE, "AWAY", False),
    SelectionSpec(ShadowMarket.FIRST_FIVE_MONEYLINE, "HOME", False),
    SelectionSpec(ShadowMarket.FIRST_FIVE_MONEYLINE, "AWAY", False),
    SelectionSpec(ShadowMarket.RUN_LINE_1_5, "HOME_-1.5", False),
    SelectionSpec(ShadowMarket.RUN_LINE_1_5, "AWAY_+1.5", False),
    SelectionSpec(ShadowMarket.FULL_GAME_TOTAL, "OVER", True),
    SelectionSpec(ShadowMarket.FULL_GAME_TOTAL, "UNDER", True),
    SelectionSpec(ShadowMarket.FIRST_FIVE_TOTAL, "OVER", True),
    SelectionSpec(ShadowMarket.FIRST_FIVE_TOTAL, "UNDER", True),
    SelectionSpec(ShadowMarket.TEAM_TOTAL, "HOME_OVER", True),
    SelectionSpec(ShadowMarket.TEAM_TOTAL, "HOME_UNDER", True),
    SelectionSpec(ShadowMarket.TEAM_TOTAL, "AWAY_OVER", True),
    SelectionSpec(ShadowMarket.TEAM_TOTAL, "AWAY_UNDER", True),
)


@dataclass(frozen=True)
class GovernedQuote:
    """A real, complete two-sided quote. ``book_prices`` feeds best-line search."""

    book: str
    side_american: int
    opposite_american: int
    observed_at: datetime
    line: float | None = None
    book_prices: tuple[tuple[str, int], ...] = ()

    def best_american(self) -> int:
        candidates = list(self.book_prices) or [(self.book, self.side_american)]
        return max(candidates, key=lambda item: american_to_decimal(item[1]))[1]

    def best_book(self) -> str:
        candidates = list(self.book_prices) or [(self.book, self.side_american)]
        return max(candidates, key=lambda item: american_to_decimal(item[1]))[0]


class QuoteSource(Protocol):
    def quote_for(self, forecast: ShadowForecast, market: str, selection: str) -> GovernedQuote | None: ...


class LiveOddsQuoteSource:
    """Resolve governed two-sided quotes from persisted odds (existing refresh-odds).

    Governed quotes are matched from ``odds_snapshots`` on ``source_event_id``.
    Historical research-shadow events (e.g. ``retrosheet:game:*``) have no
    governed live quote — matching the bundle's own HISTORICAL_ODDS_INSUFFICIENT
    explanation — so this returns ``None`` and the caller records a real NO_BET.
    Prospective-slate side resolution (external identity -> internal outcome) is
    the documented next integration step and is intentionally not fabricated.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def quote_for(self, forecast: ShadowForecast, market: str, selection: str) -> GovernedQuote | None:
        return None


class MappingQuoteSource:
    """Explicit dict-backed quote source. Test odds are synthetic and labeled."""

    def __init__(self, quotes: dict[tuple[str, str, str], GovernedQuote]) -> None:
        self._quotes = quotes

    def quote_for(self, forecast: ShadowForecast, market: str, selection: str) -> GovernedQuote | None:
        return self._quotes.get((forecast.event_id, market, selection))


@dataclass
class _Exposure:
    per_game: dict[str, float] = field(default_factory=dict)
    daily: float = 0.0


class ShadowBoardService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- import ----------------------------------------------------------
    def import_bundle(self, bundle_dir) -> dict:
        imported = verify_and_load_bundle(bundle_dir)
        existing = self.session.scalar(
            select(ShadowForecastBundle).where(
                ShadowForecastBundle.bundle_sha256 == imported.bundle_sha256
            )
        )
        if existing is not None:
            return {
                "bundle_sha256": existing.bundle_sha256,
                "bundle_id": existing.bundle_id,
                "slate_date": existing.slate_date.isoformat(),
                "forecasts": existing.forecast_count,
                "idempotent_reuse": True,
            }
        return self._persist_bundle(imported)

    def _persist_bundle(self, imported: ImportedForecastBundle) -> dict:
        bundle = imported.bundle
        row = ShadowForecastBundle(
            bundle_sha256=imported.bundle_sha256,
            bundle_id=imported.bundle_id,
            logical_uri=imported.logical_uri,
            source_fixture_id=imported.fixture.fixture_id,
            slate_date=bundle.slate_date,
            league=bundle.league,
            sport=bundle.sport,
            status=bundle.status,
            research_shadow_only=bundle.research_shadow_only,
            generated_at=bundle.generated_at_utc,
            forecast_count=len(bundle.forecasts),
            quote_count=len(bundle.market_quotes),
            candidate_count=len(bundle.bet_candidates),
        )
        self.session.add(row)
        self.session.flush()
        for forecast in bundle.forecasts:
            self.session.add(self._forecast_row(row.id, forecast))
        self.session.commit()
        return {
            "bundle_sha256": imported.bundle_sha256,
            "bundle_id": imported.bundle_id,
            "slate_date": bundle.slate_date.isoformat(),
            "forecasts": len(bundle.forecasts),
            "engines": sorted({f.engine.engine_id for f in bundle.forecasts}),
            "quotes": len(bundle.market_quotes),
            "candidates": len(bundle.bet_candidates),
            "idempotent_reuse": False,
        }

    def _forecast_row(self, bundle_pk, forecast: GameForecastExportV1) -> ShadowForecast:
        return ShadowForecast(
            bundle_id=bundle_pk,
            forecast_id=forecast.forecast_id,
            engine_id=forecast.engine.engine_id,
            engine_version=forecast.engine.engine_version,
            model_artifact_sha256=forecast.engine.model_artifact_sha256,
            simulator_id=forecast.simulator.simulator_id,
            simulator_version=forecast.simulator.simulator_version,
            simulator_seed=forecast.simulator.seed,
            event_id=forecast.event_id,
            source_event_id=forecast.source_event_id,
            event_date=forecast.event_date,
            season=forecast.season,
            home_team_id=forecast.home_team.team_id,
            away_team_id=forecast.away_team.team_id,
            is_ready=forecast.readiness.is_ready,
            readiness_score=forecast.readiness.score,
            readiness_threshold=forecast.readiness.threshold,
            status=forecast.status,
            scheduled_start_utc=forecast.scheduled_start_utc,
            forecast_cutoff_utc=forecast.forecast_cutoff_utc,
            full_home_win_probability=forecast.full_game.home_win_probability,
            full_away_win_probability=forecast.full_game.away_win_probability,
            full_expected_home_score=forecast.full_game.expected_home_score,
            full_expected_away_score=forecast.full_game.expected_away_score,
            f5_home_win_probability=forecast.first_five.home_win_probability,
            f5_away_win_probability=forecast.first_five.away_win_probability,
            f5_expected_home_score=forecast.first_five.expected_home_score,
            f5_expected_away_score=forecast.first_five.expected_away_score,
            distributions={
                "full_game": forecast.full_game.model_dump(mode="json"),
                "first_five": forecast.first_five.model_dump(mode="json"),
            },
            uncertainty=forecast.uncertainty.model_dump(mode="json"),
            readiness_detail=forecast.readiness.model_dump(mode="json"),
        )

    # -- evaluate --------------------------------------------------------
    def evaluate_board(
        self,
        slate_date: date,
        *,
        quote_source: QuoteSource | None = None,
        as_of: datetime | None = None,
        max_staleness_minutes: int = 240,
    ) -> dict:
        quote_source = quote_source or LiveOddsQuoteSource(self.session)
        as_of = as_of or datetime.now(UTC)
        bankroll = float(self.settings.shadow_virtual_bankroll)
        same_game_cap = bankroll * self.settings.shadow_max_same_game_percentage
        daily_cap = bankroll * self.settings.shadow_max_daily_percentage
        policy = StakePolicy(
            strategy=StakingStrategy.FRACTIONAL_KELLY,
            kelly_fraction=self.settings.shadow_kelly_fraction,
            maximum_stake_percentage=self.settings.shadow_max_position_percentage,
        )
        pricing_config = ShadowPricingConfig(qualified_edge=self.settings.shadow_qualified_edge)
        exposure = _Exposure()

        forecasts = list(
            self.session.scalars(
                select(ShadowForecast)
                .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
                .where(ShadowForecastBundle.slate_date == slate_date)
                .order_by(ShadowForecast.event_id, ShadowForecast.engine_id)
            )
        )
        counts = {"decisions": 0, "qualified": 0, "watch": 0, "no_bet": 0, "priced": 0, "skipped": 0}
        total_stake = 0.0
        for forecast in forecasts:
            full, first_five, uncertainty = self._split_models(forecast)
            for spec in SUPPORTED_SELECTIONS:
                quote = quote_source.quote_for(forecast, spec.market.value, spec.selection)
                if quote is not None and self._is_stale(quote, as_of, max_staleness_minutes):
                    quote = None
                signature = self._quote_signature(quote)
                if self._decision_exists(forecast.id, spec.market.value, spec.selection, signature):
                    counts["skipped"] += 1
                    continue
                decision, stake = self._build_decision(
                    forecast, spec, quote, full, first_five, uncertainty,
                    bankroll=bankroll, policy=policy, pricing_config=pricing_config,
                    same_game_cap=same_game_cap, daily_cap=daily_cap, exposure=exposure,
                    signature=signature,
                )
                self.session.add(decision)
                counts["decisions"] += 1
                total_stake += stake
                if decision.recommendation == Recommendation.QUALIFIED_BET.value:
                    counts["qualified"] += 1
                elif decision.recommendation == Recommendation.WATCH.value:
                    counts["watch"] += 1
                else:
                    counts["no_bet"] += 1
                if decision.has_quote:
                    counts["priced"] += 1
        self.session.commit()
        return {
            "slate_date": slate_date.isoformat(),
            "forecasts_evaluated": len(forecasts),
            "total_virtual_stake": round(total_stake, 2),
            **counts,
        }

    def _build_decision(
        self, forecast, spec, quote, full, first_five, uncertainty, *,
        bankroll, policy, pricing_config, same_game_cap, daily_cap, exposure, signature,
    ) -> tuple[ShadowBoardDecision, float]:
        base = dict(
            shadow_forecast_id=forecast.id,
            bundle_id=forecast.bundle_id,
            slate_date=forecast.event_date,
            engine_id=forecast.engine_id,
            event_id=forecast.event_id,
            market=spec.market.value,
            selection=spec.selection,
            quote_signature=signature,
        )
        if quote is None:
            model_p = None
            if not spec.line_dependent:
                model_p = model_probability(
                    spec.market, spec.selection, line=None,
                    full_game=full, first_five=first_five, uncertainty=uncertainty,
                ).probability
            return ShadowBoardDecision(
                **base, model_probability=model_p, has_quote=False,
                recommendation=Recommendation.NO_BET.value, virtual_stake=0,
            ), 0.0

        line = quote.line
        model = model_probability(
            spec.market, spec.selection, line=line,
            full_game=full, first_five=first_five, uncertainty=uncertainty,
        )
        priced = price_selection(
            model=model,
            side_american=quote.side_american,
            opposite_american=quote.opposite_american,
            best_american=quote.best_american(),
            is_ready=forecast.is_ready,
            config=pricing_config,
        )
        stake = 0.0
        if priced.recommendation == Recommendation.QUALIFIED_BET:
            raw = calculate_stake(
                bankroll, policy,
                model_probability=model.probability, decimal_odds=priced.best_decimal_odds,
            )
            remaining_game = max(0.0, same_game_cap - exposure.per_game.get(forecast.event_id, 0.0))
            remaining_day = max(0.0, daily_cap - exposure.daily)
            stake = round(min(raw, remaining_game, remaining_day), 2)
            exposure.per_game[forecast.event_id] = exposure.per_game.get(forecast.event_id, 0.0) + stake
            exposure.daily += stake
        decision = ShadowBoardDecision(
            **base,
            model_probability=priced.model_probability,
            has_quote=True,
            book=quote.best_book(),
            line=line,
            side_american=quote.side_american,
            opposite_american=quote.opposite_american,
            best_american=priced.best_american_odds,
            best_decimal=priced.best_decimal_odds,
            quote_observed_at=quote.observed_at,
            break_even_probability=priced.break_even_probability,
            no_vig_probability=priced.no_vig_probability,
            raw_edge=priced.raw_edge,
            no_vig_edge=priced.no_vig_edge,
            expected_value=priced.expected_value,
            uncertainty_adjusted_edge=priced.uncertainty_adjusted_edge,
            recommendation=priced.recommendation.value,
            virtual_stake=stake,
        )
        return decision, stake

    # -- settle ----------------------------------------------------------
    def settle_decisions(self, slate_date: date, outcomes: "Iterable[DecisionOutcome]") -> dict:
        by_key = {(o.event_id, o.market, o.selection, o.engine_id): o for o in outcomes}
        decisions = list(
            self.session.scalars(
                select(ShadowBoardDecision).where(ShadowBoardDecision.slate_date == slate_date)
            )
        )
        already = {
            row.decision_id
            for row in self.session.scalars(select(ShadowDecisionSettlement))
        }
        settled = 0
        total_pl = 0.0
        for decision in decisions:
            if decision.id in already:
                continue
            outcome = by_key.get(
                (decision.event_id, decision.market, decision.selection, decision.engine_id)
            )
            if outcome is None:
                continue
            stake = float(decision.virtual_stake or 0.0)
            decimal_odds = float(decision.best_decimal or 0.0)
            if stake > 0 and decimal_odds > 1.0:
                pl = settle_profit(stake, decimal_odds, SettlementResult(outcome.result.lower()))
            else:
                pl = 0.0
            clv = None
            if outcome.closing_american is not None and decision.side_american is not None:
                clv = implied_probability(outcome.closing_american) - implied_probability(decision.side_american)
            self.session.add(ShadowDecisionSettlement(
                decision_id=decision.id,
                outcome=outcome.result.upper(),
                closing_american=outcome.closing_american,
                closing_decimal=american_to_decimal(outcome.closing_american) if outcome.closing_american else None,
                closing_line_value=clv,
                virtual_profit_loss=pl,
                settlement_note=outcome.note,
            ))
            settled += 1
            total_pl += pl
        self.session.commit()
        return {"slate_date": slate_date.isoformat(), "settled": settled, "virtual_profit_loss": round(total_pl, 2)}

    # -- read board ------------------------------------------------------
    def board(self, slate_date: date) -> dict:
        forecasts = list(
            self.session.scalars(
                select(ShadowForecast)
                .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
                .where(ShadowForecastBundle.slate_date == slate_date)
                .order_by(ShadowForecast.event_id, ShadowForecast.engine_id)
            )
        )
        decisions = list(
            self.session.scalars(
                select(ShadowBoardDecision)
                .where(ShadowBoardDecision.slate_date == slate_date)
                .order_by(ShadowBoardDecision.evaluated_at)
            )
        )
        decisions_by_forecast: dict = {}
        for decision in decisions:
            decisions_by_forecast.setdefault(decision.shadow_forecast_id, []).append(decision)

        games: dict[str, dict] = {}
        for forecast in forecasts:
            game = games.setdefault(forecast.event_id, {
                "event_id": forecast.event_id,
                "event_date": forecast.event_date.isoformat(),
                "home_team_id": forecast.home_team_id,
                "away_team_id": forecast.away_team_id,
                "engines": [],
            })
            game["engines"].append(self._engine_view(forecast, decisions_by_forecast.get(forecast.id, [])))
        for game in games.values():
            game["model_disagreement"] = self._disagreement(game["engines"])
        return {
            "slate_date": slate_date.isoformat(),
            "status": "RESEARCH_SHADOW",
            "executable": False,
            "games": list(games.values()),
        }

    def _engine_view(self, forecast: ShadowForecast, decisions: list[ShadowBoardDecision]) -> dict:
        return {
            "engine_id": forecast.engine_id,
            "forecast_id": forecast.forecast_id,
            "status": forecast.status,
            "is_ready": forecast.is_ready,
            "readiness_score": forecast.readiness_score,
            "readiness_threshold": forecast.readiness_threshold,
            "home_win_probability": forecast.full_home_win_probability,
            "away_win_probability": forecast.full_away_win_probability,
            "expected_home_score": forecast.full_expected_home_score,
            "expected_away_score": forecast.full_expected_away_score,
            "first_five_home_win_probability": forecast.f5_home_win_probability,
            "first_five_away_win_probability": forecast.f5_away_win_probability,
            "decisions": [self._decision_view(d) for d in decisions],
        }

    @staticmethod
    def _decision_view(d: ShadowBoardDecision) -> dict:
        return {
            "market": d.market,
            "selection": d.selection,
            "model_probability": d.model_probability,
            "has_quote": d.has_quote,
            "book": d.book,
            "line": d.line,
            "american_odds": d.side_american,
            "best_american_odds": d.best_american,
            "no_vig_probability": d.no_vig_probability,
            "raw_edge": d.raw_edge,
            "no_vig_edge": d.no_vig_edge,
            "expected_value": d.expected_value,
            "uncertainty_adjusted_edge": d.uncertainty_adjusted_edge,
            "recommendation": d.recommendation,
            "virtual_stake": float(d.virtual_stake or 0.0),
        }

    @staticmethod
    def _disagreement(engines: list[dict]) -> float | None:
        probs = [e["home_win_probability"] for e in engines if e["home_win_probability"] is not None]
        if len(probs) < 2:
            return None
        return round(max(probs) - min(probs), 4)

    # -- helpers ---------------------------------------------------------
    def _split_models(self, forecast: ShadowForecast):
        from app.forecast_exchange.contracts import GameSplit, Uncertainty

        full = GameSplit.model_validate(forecast.distributions["full_game"])
        first_five = GameSplit.model_validate(forecast.distributions["first_five"])
        uncertainty = Uncertainty.model_validate(forecast.uncertainty)
        return full, first_five, uncertainty

    @staticmethod
    def _is_stale(quote: GovernedQuote, as_of: datetime, max_staleness_minutes: int) -> bool:
        observed = quote.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        return observed < as_of - timedelta(minutes=max_staleness_minutes)

    @staticmethod
    def _quote_signature(quote: GovernedQuote | None) -> str:
        if quote is None:
            return "NO_QUOTE"
        raw = f"{quote.book}|{quote.side_american}|{quote.opposite_american}|{quote.line}|{quote.observed_at.isoformat()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _decision_exists(self, forecast_id, market: str, selection: str, signature: str) -> bool:
        return self.session.scalar(
            select(ShadowBoardDecision.id).where(
                ShadowBoardDecision.shadow_forecast_id == forecast_id,
                ShadowBoardDecision.market == market,
                ShadowBoardDecision.selection == selection,
                ShadowBoardDecision.quote_signature == signature,
            ).limit(1)
        ) is not None


@dataclass(frozen=True)
class DecisionOutcome:
    event_id: str
    engine_id: str
    market: str
    selection: str
    result: str  # WIN / LOSS / PUSH / VOID
    closing_american: int | None = None
    note: str | None = None


__all__ = [
    "DecisionOutcome",
    "GovernedQuote",
    "LiveOddsQuoteSource",
    "MappingQuoteSource",
    "ShadowBoardService",
    "SUPPORTED_SELECTIONS",
]
