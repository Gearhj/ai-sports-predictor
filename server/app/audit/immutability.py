from __future__ import annotations

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models import (
    BattingFeature,
    BullpenAvailabilityObservation,
    BullpenFeature,
    Forecast,
    ForecastComponent,
    ForecastMovementEvidence,
    ForecastStatus,
    GameContext,
    GameFeatureSnapshot,
    GameRefreshState,
    GameStateObservation,
    LineupEntry,
    LineupObservation,
    MarketComparison,
    OddsSnapshot,
    OfficialAspPosition,
    ParkFactorVersion,
    PitchEvent,
    PitchingFeature,
    PlayerGameStat,
    PlayerTalentEstimate,
    PlayerTeamHistory,
    PositionSettlement,
    PositionStatus,
    ProviderRequestLedger,
    Roster,
    RosterAvailabilityObservation,
    SourceFreshnessPolicy,
    StarterObservation,
    StatcastPlayerObservation,
    TeamFeature,
    TeamGameStat,
    TeamRatingObservation,
    WeatherSnapshot,
)


class ImmutableRecordError(RuntimeError):
    pass


_installed = False


_APPEND_ONLY_TYPES = (
    BattingFeature,
    BullpenAvailabilityObservation,
    BullpenFeature,
    ForecastMovementEvidence,
    GameContext,
    GameFeatureSnapshot,
    GameRefreshState,
    GameStateObservation,
    LineupEntry,
    LineupObservation,
    ParkFactorVersion,
    PitchEvent,
    PitchingFeature,
    PlayerGameStat,
    PlayerTalentEstimate,
    PlayerTeamHistory,
    ProviderRequestLedger,
    Roster,
    RosterAvailabilityObservation,
    SourceFreshnessPolicy,
    StarterObservation,
    StatcastPlayerObservation,
    TeamFeature,
    TeamGameStat,
    TeamRatingObservation,
    WeatherSnapshot,
)


def install_immutability_guards() -> None:
    """Install process-wide append-only protections once.

    Database roles/triggers remain recommended defense-in-depth for production;
    these guards protect every SQLAlchemy service path and are covered by tests.
    """
    global _installed
    if _installed:
        return

    @event.listens_for(Session, "before_flush")
    def prevent_mutation(session: Session, flush_context: object, instances: object) -> None:
        for record in session.deleted:
            if isinstance(record, OddsSnapshot):
                raise ImmutableRecordError("Odds snapshots are append-only")
            if isinstance(record, _APPEND_ONLY_TYPES):
                raise ImmutableRecordError(f"{type(record).__name__} records are append-only")
            if isinstance(record, Forecast) and record.status == ForecastStatus.PUBLISHED:
                raise ImmutableRecordError("Published forecasts cannot be deleted")
            if isinstance(record, OfficialAspPosition) and record.status == PositionStatus.SETTLED:
                raise ImmutableRecordError("Settled official positions cannot be deleted")
            if isinstance(record, PositionSettlement):
                raise ImmutableRecordError("Position settlement records are append-only")
            if isinstance(record, (ForecastComponent, MarketComparison)) and _published_parent(session, record):
                raise ImmutableRecordError("Published forecast aggregates cannot be deleted")
        for record in session.dirty:
            if isinstance(record, OddsSnapshot) and session.is_modified(record, include_collections=False):
                raise ImmutableRecordError("Odds snapshots are append-only")
            if isinstance(record, _APPEND_ONLY_TYPES) and session.is_modified(record, include_collections=False):
                raise ImmutableRecordError(f"{type(record).__name__} records are append-only")
            if isinstance(record, Forecast) and session.is_modified(record, include_collections=False):
                state = inspect(record)
                history = state.attrs.status.history
                was_published = (not history.has_changes() and record.status == ForecastStatus.PUBLISHED) or (
                    ForecastStatus.PUBLISHED in history.deleted
                )
                if was_published:
                    raise ImmutableRecordError("Published forecasts cannot be modified; create a new version")
            if isinstance(record, OfficialAspPosition) and session.is_modified(record, include_collections=False):
                state = inspect(record)
                status_history = state.attrs.status.history
                was_settled = (not status_history.has_changes() and record.status == PositionStatus.SETTLED) or (
                    PositionStatus.SETTLED in status_history.deleted
                )
                if was_settled:
                    raise ImmutableRecordError("Settled official positions cannot be modified")
            if isinstance(record, PositionSettlement) and session.is_modified(record, include_collections=False):
                raise ImmutableRecordError("Position settlement records are append-only")
            if isinstance(record, (ForecastComponent, MarketComparison)) and session.is_modified(record, include_collections=False) and _published_parent(session, record):
                raise ImmutableRecordError("Published forecast aggregates cannot be modified")

    _installed = True


def _published_parent(session: Session, record: ForecastComponent | MarketComparison) -> bool:
    forecast = session.get(Forecast, record.forecast_id)
    return bool(forecast and forecast.status == ForecastStatus.PUBLISHED)
