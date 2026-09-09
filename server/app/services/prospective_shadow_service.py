"""Availability-event-triggered prospective rescoring for the shadow board.

Flow for one event (all persisted immutably, all virtual, none executable):

  persist event -> resolve affected game(s) -> snapshot pre-event quotes ->
  capture before-forecast/edge -> rescore the affected game with the EXISTING
  AISP pipeline (no new model) -> refresh odds -> snapshot post-event quotes ->
  recompute the decision -> persist before/after probabilities, quotes, edges,
  EV, book-moved, recommendation, virtual stake, readiness, uncertainty, timing.

Quotes are only ever snapshots of persisted sportsbook observations produced by
the existing refresh-odds job; no market price is fabricated.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Callable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import local_date_of, local_day_bounds
from app.forecast_exchange.pricing import (
    ModelProbability,
    Recommendation,
    ShadowPricingConfig,
    american_to_decimal,
    price_selection,
)
from app.bankroll.service import StakePolicy, StakingStrategy, calculate_stake
from app.markets.odds import no_vig_probabilities
from app.models import (
    AvailabilityEvent,
    ExternalIdMapping,
    Forecast,
    ForecastTrack,
    Game,
    GameTeam,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    ShadowQuoteCapture,
    ShadowRescore,
)
from app.prospective.contracts import (
    MANUAL_ADMIN_SOURCE,
    AvailabilityEventInput,
    AvailabilityEventV1,
)


class RescoreError(RuntimeError):
    """A prospective rescore could not be produced (e.g. no affected game)."""


class ForecastRescorer(Protocol):
    def rescore(self, game: Game) -> Forecast: ...


class PipelineForecastRescorer:
    """Rescore one game with the EXISTING AISP forecast pipeline (no new model)."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def rescore(self, game: Game) -> Forecast:
        from app.services.forecast_service import ForecastService

        # Point-in-time pregame cutoff. It must be before first pitch and not in
        # the future, so clamp to the earlier of "just before now" and "just
        # before first pitch" — valid for both current/future and replayed games.
        starts = _aware(game.starts_at)
        now = datetime.now(UTC)
        data_as_of = min(now - timedelta(seconds=1), starts - timedelta(minutes=1))
        return ForecastService(self.session, self.settings).generate_for_game(
            game, data_as_of=data_as_of, force_draft=True,
        )


@dataclass(frozen=True)
class TwoSidedQuote:
    book_home: str
    book_away: str
    home_american: int
    away_american: int
    home_decimal: float
    away_decimal: float
    no_vig_home: float
    no_vig_away: float
    observed_at: datetime

    def as_json(self) -> dict:
        return {
            "book_home": self.book_home, "book_away": self.book_away,
            "home_american": self.home_american, "away_american": self.away_american,
            "no_vig_home": round(self.no_vig_home, 6), "no_vig_away": round(self.no_vig_away, 6),
            "observed_at": self.observed_at.isoformat(),
        }


class ProspectiveShadowService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- public flow -----------------------------------------------------
    def process_event(
        self,
        event_input: AvailabilityEventInput,
        *,
        source: str = MANUAL_ADMIN_SOURCE,
        rescorer: ForecastRescorer | None = None,
        odds_refresher: Callable[[Game], None] | None = None,
        now: datetime | None = None,
    ) -> dict:
        now = now or datetime.now(UTC)
        rescorer = rescorer or PipelineForecastRescorer(self.session, self.settings)
        event = self._persist_event(event_input, source=source, received_at=now)
        games = self._affected_games(event)
        if not games:
            raise RescoreError("Availability event does not resolve to a current game")

        rescores: list[dict] = []
        for game in games:
            if self._rescore_exists(event.id, game.id):
                continue
            rescores.append(self._rescore_game(event, game, rescorer=rescorer, odds_refresher=odds_refresher))
        self.session.commit()
        return {
            "availability_event_id": str(event.id),
            "event_type": event.event_type,
            "source": event.source,
            "games_rescored": len(rescores),
            "rescores": rescores,
        }

    def _rescore_game(
        self, event: AvailabilityEvent, game: Game, *, rescorer: ForecastRescorer,
        odds_refresher: Callable[[Game], None] | None,
    ) -> dict:
        pre_quote = self._two_sided_moneyline(game)
        pre_quote_at = pre_quote.observed_at if pre_quote else None
        before = self._latest_forecast(game)
        before_decision = self._moneyline_decisions(before, pre_quote) if before else None

        rescore_started = datetime.now(UTC)
        after = rescorer.rescore(game)
        rescore_completed = datetime.now(UTC)

        # Refresh odds immediately after the rescore, then re-snapshot.
        if odds_refresher is not None:
            odds_refresher(game)
        post_quote = self._two_sided_moneyline(game)
        post_quote_at = post_quote.observed_at if post_quote else None
        after_decision = self._moneyline_decisions(after, post_quote)

        book_moved = bool(
            pre_quote and post_quote
            and (pre_quote.home_american != post_quote.home_american
                 or pre_quote.away_american != post_quote.away_american)
        )
        headline = max(after_decision.values(), key=lambda d: d["virtual_stake"]) if after_decision else None
        recommendation = headline["recommendation"] if headline else Recommendation.NO_BET.value
        virtual_stake = float(headline["virtual_stake"]) if headline else 0.0

        rescore = ShadowRescore(
            availability_event_id=event.id,
            game_id=game.id,
            slate_date=local_date_of(_aware(game.starts_at), self.settings.display_timezone),
            engine="AISP_BASELINE",
            market="FULL_GAME_MONEYLINE",
            before_forecast_id=before.id if before else None,
            after_forecast_id=after.id,
            before_home_win_probability=before.home_win_probability if before else None,
            after_home_win_probability=after.home_win_probability,
            before_readiness=before.readiness_score if before else None,
            after_readiness=after.readiness_score,
            before_uncertainty=before.uncertainty if before else None,
            after_uncertainty=after.uncertainty,
            pre_quote=pre_quote.as_json() if pre_quote else None,
            post_quote=post_quote.as_json() if post_quote else None,
            book_moved=book_moved,
            decisions={"before": before_decision, "after": after_decision},
            recommendation=recommendation,
            virtual_stake=virtual_stake,
            source_published_at=event.published_at,
            aisp_received_at=event.received_at,
            rescore_started_at=rescore_started,
            rescore_completed_at=rescore_completed,
            pre_event_quote_at=pre_quote_at,
            post_event_quote_at=post_quote_at,
            quote_timeline={
                "pre_event": pre_quote.as_json() if pre_quote else None,
                "post_event": post_quote.as_json() if post_quote else None,
                # Later captures are appended by capture_quote(); closing is set
                # when a closing observation exists. No value is fabricated.
                "t_plus_15s": None, "t_plus_60s": None, "t_plus_5m": None, "closing": None,
            },
        )
        self.session.add(rescore)
        self.session.flush()
        return {
            "shadow_rescore_id": str(rescore.id),
            "game_id": str(game.id),
            "book_moved": book_moved,
            "recommendation": recommendation,
            "virtual_stake": virtual_stake,
            "before_home_win_probability": rescore.before_home_win_probability,
            "after_home_win_probability": rescore.after_home_win_probability,
        }

    def capture_quote(self, rescore_id, slot: str, *, now: datetime | None = None) -> dict:
        """Append a later timeline quote (t_plus_15s/60s/5m/closing) from real odds.

        Captures are append-only rows in shadow_quote_captures so the immutable
        ShadowRescore is never mutated. A missing governed quote records null.
        """

        valid = {"t_plus_15s", "t_plus_60s", "t_plus_5m", "closing"}
        if slot not in valid:
            raise ValueError(f"Unknown quote slot: {slot}")
        rescore = self.session.get(ShadowRescore, rescore_id)
        if rescore is None:
            raise RescoreError("Rescore not found")
        existing = self.session.scalar(
            select(ShadowQuoteCapture).where(
                ShadowQuoteCapture.rescore_id == rescore.id, ShadowQuoteCapture.slot == slot)
        )
        if existing is not None:
            return {"rescore_id": str(rescore.id), "slot": slot,
                    "captured": existing.quote is not None, "idempotent": True}
        game = self.session.get(Game, rescore.game_id)
        quote = self._two_sided_moneyline(game, closing=slot == "closing")
        self.session.add(ShadowQuoteCapture(
            rescore_id=rescore.id, slot=slot,
            quote=quote.as_json() if quote else None,
            captured_at=now or datetime.now(UTC),
        ))
        self.session.commit()
        return {"rescore_id": str(rescore.id), "slot": slot, "captured": quote is not None}

    # -- board read ------------------------------------------------------
    def rescores(self, slate_date: date) -> dict:
        rows = list(
            self.session.scalars(
                select(ShadowRescore)
                .where(ShadowRescore.slate_date == slate_date)
                .order_by(ShadowRescore.rescore_completed_at)
            )
        )
        events = {e.id: e for e in self.session.scalars(select(AvailabilityEvent))}
        captures: dict = {}
        if rows:
            for cap in self.session.scalars(
                select(ShadowQuoteCapture).where(
                    ShadowQuoteCapture.rescore_id.in_([r.id for r in rows]))
            ):
                captures.setdefault(cap.rescore_id, {})[cap.slot] = cap.quote
        return {
            "slate_date": slate_date.isoformat(),
            "status": "RESEARCH_SHADOW",
            "executable": False,
            "rescores": [
                self._rescore_view(row, events.get(row.availability_event_id), captures.get(row.id, {}))
                for row in rows
            ],
        }

    @staticmethod
    def _rescore_view(row: ShadowRescore, event: AvailabilityEvent | None, captures: dict | None = None) -> dict:
        return {
            "id": str(row.id),
            "game_id": str(row.game_id),
            "engine": row.engine,
            "availability_change": {
                "event_type": event.event_type if event else None,
                "source": event.source if event else None,
                "player_name": event.player_name if event else None,
                "description": event.description if event else None,
            },
            "forecast_before": {
                "home_win_probability": row.before_home_win_probability,
                "readiness": row.before_readiness,
                "uncertainty": row.before_uncertainty,
            },
            "forecast_after": {
                "home_win_probability": row.after_home_win_probability,
                "readiness": row.after_readiness,
                "uncertainty": row.after_uncertainty,
            },
            "quote_before": row.pre_quote,
            "quote_after": row.post_quote,
            "book_moved": row.book_moved,
            "decisions": row.decisions,
            "recommendation": row.recommendation,
            "virtual_stake": float(row.virtual_stake or 0.0),
            "timing": {
                "source_published_at": _iso(row.source_published_at),
                "aisp_received_at": _iso(row.aisp_received_at),
                "rescore_started_at": _iso(row.rescore_started_at),
                "rescore_completed_at": _iso(row.rescore_completed_at),
                "pre_event_quote_at": _iso(row.pre_event_quote_at),
                "post_event_quote_at": _iso(row.post_event_quote_at),
            },
            "quote_timeline": {**(row.quote_timeline or {}), **(captures or {})},
            "speed_metrics": compute_speed_metrics(row, event),
        }

    # -- persistence + resolution ---------------------------------------
    def _persist_event(
        self, event_input: AvailabilityEventInput, *, source: str, received_at: datetime
    ) -> AvailabilityEvent:
        key = self._event_key(event_input)
        existing = self.session.scalar(
            select(AvailabilityEvent).where(
                AvailabilityEvent.source == source,
                AvailabilityEvent.source_event_key == key,
            )
        )
        if existing is not None:
            return existing
        # Validate against the closed contract before persisting.
        contract = AvailabilityEventV1(
            event_type=event_input.event_type,
            source=source,
            source_event_key=key,
            game_id=event_input.game_id,
            source_game_id=event_input.source_game_id,
            team_source_id=event_input.team_source_id,
            player_source_id=event_input.player_source_id,
            player_name=event_input.player_name,
            published_at=event_input.published_at,
            description=event_input.description,
            payload=event_input.payload,
        )
        game_uuid = self._resolve_internal_game_id(event_input)
        row = AvailabilityEvent(
            schema_version=contract.schema_version,
            event_type=contract.event_type.value,
            source=contract.source,
            source_event_key=contract.source_event_key,
            sport=contract.sport,
            league=contract.league,
            game_id=game_uuid,
            source_game_id=contract.source_game_id,
            team_source_id=contract.team_source_id,
            player_source_id=contract.player_source_id,
            player_name=contract.player_name,
            published_at=contract.published_at,
            received_at=received_at,
            description=contract.description,
            payload=contract.payload,
        )
        self.session.add(row)
        self.session.flush()
        return row

    @staticmethod
    def _event_key(event_input: AvailabilityEventInput) -> str:
        raw = "|".join(str(part) for part in (
            event_input.event_type.value,
            event_input.game_id, event_input.source_game_id,
            event_input.team_source_id, event_input.player_source_id,
            event_input.published_at.isoformat() if event_input.published_at else None,
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]

    def _resolve_internal_game_id(self, event_input: AvailabilityEventInput):
        if event_input.game_id:
            game = self.session.get(Game, _as_uuid(event_input.game_id))
            return game.id if game else None
        if event_input.source_game_id:
            mapping = self.session.scalar(
                select(ExternalIdMapping).where(
                    ExternalIdMapping.entity_type == "game",
                    ExternalIdMapping.external_id == event_input.source_game_id,
                )
            )
            return mapping.entity_id if mapping else None
        return None

    def _affected_games(self, event: AvailabilityEvent) -> list[Game]:
        if event.game_id:
            game = self.session.get(Game, event.game_id)
            return [game] if game else []
        if event.team_source_id:
            mapping = self.session.scalar(
                select(ExternalIdMapping).where(
                    ExternalIdMapping.entity_type == "team",
                    ExternalIdMapping.external_id == event.team_source_id,
                )
            )
            if mapping is None:
                return []
            slate_day = (event.published_at or event.received_at)
            bounds = local_day_bounds(
                local_date_of(_aware(slate_day), self.settings.display_timezone),
                self.settings.display_timezone,
            )
            return list(self.session.scalars(
                select(Game)
                .join(GameTeam, GameTeam.game_id == Game.id)
                .where(GameTeam.team_id == mapping.entity_id,
                       Game.starts_at >= bounds[0], Game.starts_at < bounds[1])
                .order_by(Game.starts_at)
            ))
        return []

    def _rescore_exists(self, event_id, game_id) -> bool:
        return self.session.scalar(
            select(ShadowRescore.id).where(
                ShadowRescore.availability_event_id == event_id,
                ShadowRescore.game_id == game_id,
            ).limit(1)
        ) is not None

    def _latest_forecast(self, game: Game) -> Forecast | None:
        return self.session.scalar(
            select(Forecast)
            .where(Forecast.game_id == game.id,
                   Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT)
            .order_by(Forecast.version.desc())
            .limit(1)
        )

    # -- quotes + decision ----------------------------------------------
    def _two_sided_moneyline(self, game: Game, *, closing: bool = False) -> TwoSidedQuote | None:
        market = self.session.scalar(
            select(Market).where(Market.game_id == game.id, Market.market_type == MarketType.MONEYLINE)
        )
        if market is None:
            return None
        outcomes = list(self.session.scalars(select(MarketOutcome).where(MarketOutcome.market_id == market.id)))
        home_team = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
        if home_team is None or len(outcomes) < 2:
            return None
        home_outcome = next((o for o in outcomes if o.participant_team_id == home_team.team_id), None)
        away_outcome = next((o for o in outcomes if o.id != (home_outcome.id if home_outcome else None)), None)
        if home_outcome is None or away_outcome is None:
            return None
        # The game's own mode decides the provider authority: a LIVE rescore is
        # a governed gate and reads governed providers only.
        home = self._best_snapshot(home_outcome.id, closing=closing, source_mode=game.source_mode)
        away = self._best_snapshot(away_outcome.id, closing=closing, source_mode=game.source_mode)
        if home is None or away is None or home.american_odds is None or away.american_odds is None:
            return None
        result = no_vig_probabilities(
            {"home": home.decimal_odds, "away": away.decimal_odds}, american=False
        )
        return TwoSidedQuote(
            book_home=str(home.source or home.sportsbook_id),
            book_away=str(away.source or away.sportsbook_id),
            home_american=int(home.american_odds), away_american=int(away.american_odds),
            home_decimal=home.decimal_odds, away_decimal=away.decimal_odds,
            no_vig_home=result.probabilities["home"], no_vig_away=result.probabilities["away"],
            observed_at=max(home.observed_at, away.observed_at),
        )

    def _best_snapshot(
        self, outcome_id, *, closing: bool, source_mode: str | None = "LIVE",
    ) -> OddsSnapshot | None:
        from app.data_sources.provider_registry import authorised_sources_for_mode

        stmt = select(OddsSnapshot).where(OddsSnapshot.market_outcome_id == outcome_id)
        # The rescore this feeds is a governed gate, so a LIVE quote must come
        # from a governed provider.
        authorised = authorised_sources_for_mode(source_mode)
        if authorised is not None:
            stmt = stmt.where(OddsSnapshot.source.in_(authorised))
        if closing:
            stmt = stmt.where(OddsSnapshot.is_closing.is_(True))
        # Best available price = highest decimal odds; ties broken by recency.
        return self.session.scalar(stmt.order_by(OddsSnapshot.decimal_odds.desc(), OddsSnapshot.observed_at.desc()).limit(1))

    def _moneyline_decisions(self, forecast: Forecast, quote: TwoSidedQuote | None) -> dict:
        is_ready = forecast.readiness_score >= self.settings.forecast_min_readiness
        config = ShadowPricingConfig(qualified_edge=self.settings.shadow_qualified_edge)
        policy = StakePolicy(
            strategy=StakingStrategy.FRACTIONAL_KELLY,
            kelly_fraction=self.settings.shadow_kelly_fraction,
            maximum_stake_percentage=self.settings.shadow_max_position_percentage,
        )
        bankroll = float(self.settings.shadow_virtual_bankroll)
        sides = {
            "HOME": (forecast.home_win_probability, "home", "away"),
            "AWAY": (forecast.away_win_probability, "away", "home"),
        }
        result: dict[str, dict] = {}
        for selection, (model_p, side, opp) in sides.items():
            if quote is None:
                result[selection] = {
                    "selection": selection, "model_probability": model_p, "has_quote": False,
                    "no_vig_probability": None, "edge": None, "expected_value": None,
                    "recommendation": Recommendation.NO_BET.value, "virtual_stake": 0.0,
                }
                continue
            side_american = quote.home_american if side == "home" else quote.away_american
            opp_american = quote.home_american if opp == "home" else quote.away_american
            priced = price_selection(
                model=ModelProbability(model_p, None, None),
                side_american=side_american, opposite_american=opp_american,
                best_american=side_american, is_ready=is_ready, config=config,
            )
            stake = 0.0
            if priced.recommendation == Recommendation.QUALIFIED_BET:
                stake = calculate_stake(bankroll, policy, model_probability=model_p, decimal_odds=priced.best_decimal_odds)
            result[selection] = {
                "selection": selection, "model_probability": model_p, "has_quote": True,
                "american_odds": side_american,
                "no_vig_probability": priced.no_vig_probability,
                "edge": priced.no_vig_edge, "expected_value": priced.expected_value,
                "recommendation": priced.recommendation.value, "virtual_stake": round(stake, 2),
            }
        return result


def compute_speed_metrics(rescore: "ShadowRescore", event: "AvailabilityEvent | None") -> dict:
    """Per-event latency and market-response evidence.

    Provider-detection latency is reported only for automatically detected
    (MLB_STATS_API) events; a MANUAL_ADMIN event is explicitly excluded so no
    speed edge is ever claimed from operator entry or a seeded quote.
    """
    from app.forecast_exchange.pricing import implied_probability
    from app.prospective.contracts import MLB_STATS_API_SOURCE

    provider = event is not None and event.source == MLB_STATS_API_SOURCE
    detection = _delta_seconds(event.published_at if event else None, rescore.aisp_received_at)
    processing = _delta_seconds(rescore.aisp_received_at, rescore.rescore_completed_at)
    forecast_change = (
        None if rescore.before_home_win_probability is None
        else round(rescore.after_home_win_probability - rescore.before_home_win_probability, 6)
    )
    quote_change = None
    favorable_survived = None
    if rescore.pre_quote and rescore.post_quote:
        quote_change = rescore.post_quote.get("home_american", 0) - rescore.pre_quote.get("home_american", 0)
        favorable_survived = rescore.pre_quote.get("home_american") == rescore.post_quote.get("home_american")
    timeline = rescore.quote_timeline or {}
    closing = timeline.get("closing")
    clv = None
    if closing and rescore.pre_quote:
        try:
            clv = round(implied_probability(int(closing["home_american"])) - implied_probability(int(rescore.pre_quote["home_american"])), 6)
        except (KeyError, TypeError, ValueError):
            clv = None
    return {
        "is_provider_event": provider,
        "provider_detection_latency_seconds": detection if provider else None,
        "aisp_processing_latency_seconds": processing,
        "forecast_probability_change": forecast_change,
        "quote_change_home_american": quote_change,
        "book_moved": rescore.book_moved,
        "favorable_quote_survived": favorable_survived,
        "closing_quote_available": closing is not None,
        "closing_line_value": clv,
        "note": None if provider else "MANUAL_ADMIN event — excluded from provider-latency evidence",
    }


def _delta_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((_aware(end) - _aware(start)).total_seconds(), 3)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value else None


def _as_uuid(value):
    import uuid
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


__all__ = [
    "ForecastRescorer",
    "PipelineForecastRescorer",
    "ProspectiveShadowService",
    "RescoreError",
    "TwoSidedQuote",
]
