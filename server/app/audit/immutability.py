from __future__ import annotations

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models import (
    AvailabilityEvent,
    BattingFeature,
    BullpenAvailabilityObservation,
    BullpenFeature,
    Forecast,
    ForecastComponent,
    ForecastMovementEvidence,
    ForecastStatus,
    GameFinalOutcome,
    GameContext,
    GameFeatureSnapshot,
    GameRefreshState,
    GameStateObservation,
    HistoricalEvidenceStateAttestation,
    HistoricalAcquisitionStage,
    HistoricalEventPartition,
    HistoricalFeatureMaterialization,
    HistoricalRequestAttempt,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    LineupEntry,
    LineupObservation,
    MarketComparison,
    ModelEvaluation,
    ModelVersion,
    OpenDataDownloadManifest,
    OpenDataIdentityMapping,
    OddsSnapshot,
    OfficialAspPosition,
    ParkFactorVersion,
    PitchEvent,
    PitchingFeature,
    PlayerGameHistoryObservation,
    PlayerGameStat,
    PlayerTalentEstimate,
    PlayerTeamHistory,
    PositionSettlement,
    PositionStatus,
    ProspectiveEvaluationClaim,
    ProspectiveEvaluationClaimBinding,
    ProspectiveEvaluationGameAssignment,
    ProspectiveEvaluationLabelAccess,
    ProspectiveEvaluationPlan,
    ProviderRequestLedger,
    ProviderAccessAuthorization,
    Roster,
    RosterAvailabilityObservation,
    ShadowBoardDecision,
    ShadowDecisionSettlement,
    ShadowForecast,
    ShadowForecastBundle,
    ShadowExperiment,
    ShadowExperimentDecision,
    ShadowQuoteCapture,
    ShadowRescore,
    SourceFreshnessPolicy,
    SourceOwnerApproval,
    StarterObservation,
    StatcastPlayerObservation,
    TeamFeature,
    TeamGameHistoryObservation,
    TeamGameStat,
    TeamRatingObservation,
    TrainingDatasetManifest,
    WeatherSnapshot,
    ExistingEvidenceDerivedUseDecision,
)


class ImmutableRecordError(RuntimeError):
    pass


_installed = False


_APPEND_ONLY_TYPES = (
    BattingFeature,
    BullpenAvailabilityObservation,
    BullpenFeature,
    ForecastMovementEvidence,
    GameFinalOutcome,
    GameContext,
    GameFeatureSnapshot,
    GameRefreshState,
    GameStateObservation,
    HistoricalEvidenceStateAttestation,
    HistoricalAcquisitionStage,
    HistoricalEventPartition,
    HistoricalFeatureMaterialization,
    HistoricalRequestAttempt,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    LineupEntry,
    LineupObservation,
    ModelEvaluation,
    OpenDataDownloadManifest,
    OpenDataIdentityMapping,
    ParkFactorVersion,
    PitchEvent,
    PitchingFeature,
    PlayerGameHistoryObservation,
    PlayerGameStat,
    PlayerTalentEstimate,
    PlayerTeamHistory,
    ProspectiveEvaluationClaim,
    ProspectiveEvaluationClaimBinding,
    ProspectiveEvaluationGameAssignment,
    ProspectiveEvaluationLabelAccess,
    ProspectiveEvaluationPlan,
    ProviderAccessAuthorization,
    ProviderRequestLedger,
    Roster,
    RosterAvailabilityObservation,
    AvailabilityEvent,
    ShadowBoardDecision,
    ShadowDecisionSettlement,
    ShadowForecast,
    ShadowForecastBundle,
    ShadowExperiment,
    ShadowExperimentDecision,
    ShadowQuoteCapture,
    ShadowRescore,
    SourceFreshnessPolicy,
    SourceOwnerApproval,
    StarterObservation,
    StatcastPlayerObservation,
    TeamFeature,
    TeamGameHistoryObservation,
    TeamGameStat,
    TeamRatingObservation,
    TrainingDatasetManifest,
    WeatherSnapshot,
    ExistingEvidenceDerivedUseDecision,
)


# Model identity, lineage, fitted-state, and promotion evidence are immutable
# once a registry row has been inserted. ``metrics`` is intentionally absent:
# the Phase 1 forward-performance recalculation still updates that legacy field.
# Governed offline evidence is stored separately in append-only ModelEvaluation
# rows and is therefore not weakened by that operational compatibility carveout.
_IMMUTABLE_MODEL_VERSION_FIELDS = frozenset({
    "name",
    "version",
    "sport_id",
    "feature_version_id",
    "algorithm",
    "status",
    "training_window",
    "hyperparameters",
    "artifact_uri",
    "artifact_checksum",
    "parameters",
    "trained_at",
    "target",
    "training_manifest_id",
    "validation_window",
    "holdout_window",
    "calibrator_uri",
    "calibrator_checksum",
    "ensemble_weights",
    "code_revision",
    "identity_checksum",
    "feature_names",
    "seed_policy",
    "target_definition",
    "promotion_decision",
    "promotion_reason",
})


_IMMUTABLE_FORECAST_FIELDS = frozenset({"forecast_track"})

_IMMUTABLE_POSITION_FIELDS = frozenset({
    "forecast_id",
    "market_outcome_id",
    "odds_snapshot_id",
    "placed_at",
    "stake",
    "decimal_odds",
    "strategy",
    "shadow_experiment_id",
    "shadow_decision_id",
    "bankroll_before_position",
    "recommendation_policy_version",
    "staking_policy_version",
})


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
            if isinstance(record, OfficialAspPosition) and (
                record.shadow_experiment_id is not None
                or record.status in {PositionStatus.SETTLED, PositionStatus.VOID}
            ):
                raise ImmutableRecordError("Experiment and settled official positions cannot be deleted")
            if isinstance(record, PositionSettlement):
                raise ImmutableRecordError("Position settlement records are append-only")
            if isinstance(record, ModelVersion):
                raise ImmutableRecordError("ModelVersion registry records cannot be deleted")
            if isinstance(record, (ForecastComponent, MarketComparison)) and _published_parent(session, record):
                raise ImmutableRecordError("Published forecast aggregates cannot be deleted")
        for record in session.dirty:
            if isinstance(record, OddsSnapshot) and session.is_modified(record, include_collections=False):
                raise ImmutableRecordError("Odds snapshots are append-only")
            if isinstance(record, _APPEND_ONLY_TYPES) and session.is_modified(record, include_collections=False):
                raise ImmutableRecordError(f"{type(record).__name__} records are append-only")
            if isinstance(record, ModelVersion) and session.is_modified(record, include_collections=False):
                state = inspect(record)
                changed = sorted(
                    field
                    for field in _IMMUTABLE_MODEL_VERSION_FIELDS
                    if state.attrs[field].history.has_changes()
                )
                if changed:
                    raise ImmutableRecordError(
                        "ModelVersion governance fields are immutable after creation: "
                        + ", ".join(changed)
                    )
            if isinstance(record, Forecast) and session.is_modified(record, include_collections=False):
                state = inspect(record)
                changed = sorted(
                    field
                    for field in _IMMUTABLE_FORECAST_FIELDS
                    if state.attrs[field].history.has_changes()
                )
                if changed:
                    raise ImmutableRecordError(
                        "Forecast track is immutable after creation; create a new track-scoped version"
                    )
                history = state.attrs.status.history
                was_published = (not history.has_changes() and record.status == ForecastStatus.PUBLISHED) or (
                    ForecastStatus.PUBLISHED in history.deleted
                )
                if was_published:
                    raise ImmutableRecordError("Published forecasts cannot be modified; create a new version")
            if isinstance(record, OfficialAspPosition) and session.is_modified(record, include_collections=False):
                state = inspect(record)
                changed = sorted(
                    field for field in _IMMUTABLE_POSITION_FIELDS
                    if state.attrs[field].history.has_changes()
                )
                if changed:
                    raise ImmutableRecordError(
                        "Official position evidence is immutable after creation: " + ", ".join(changed)
                    )
                status_history = state.attrs.status.history
                terminal = {PositionStatus.SETTLED, PositionStatus.VOID}
                was_settled = (not status_history.has_changes() and record.status in terminal) or (
                    any(value in terminal for value in status_history.deleted)
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
