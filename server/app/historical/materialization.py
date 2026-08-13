"""Provider-free persistence bridge for governed historical feature snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isclose, isfinite
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.historical.policy import (
    EvidenceClassification,
    EvidenceTier,
    ResearchProvenance,
    tier_for_classification,
)
from app.historical.snapshot import EvidenceRole, HistoricalSnapshot, canonical_json
from app.historical.temporal import as_utc, deterministic_cutoff
from app.models import (
    FeatureVersion,
    Game,
    GameFeatureSnapshot,
    GameFinalOutcome,
    GameTeam,
    HistoricalFeatureMaterialization,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    PlayerGameHistoryObservation,
    TeamGameHistoryObservation,
)


TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"
SOURCE_MODE = "LIVE"
DEFAULT_MATERIALIZER_VERSION = "gate3a_historical_materializer_v1"
_FORBIDDEN_TARGET_TOKENS = (
    "final_score",
    "home_score",
    "away_score",
    "home_win",
    "game_result",
    "winner",
)
_FORBIDDEN_TARGET_NAMES = {
    "home_runs",
    "away_runs",
    "run_differential",
    "total_runs",
}
_TIER_RISK = {"A": 0, "B": 1, "C": 2, "EXCLUDED": 3}
_CANONICAL_EVIDENCE_MODELS = {
    "team_game_history_observations": TeamGameHistoryObservation,
    "player_game_history_observations": PlayerGameHistoryObservation,
}


class HistoricalMaterializationError(ValueError):
    """Raised when historical semantics cannot be persisted honestly."""


@dataclass(frozen=True)
class HistoricalMaterializationResult:
    feature_snapshot: GameFeatureSnapshot
    materialization: HistoricalFeatureMaterialization
    reused_feature_snapshot: bool
    reused_materialization: bool


def persist_historical_snapshot(
    session: Session,
    *,
    game: Game,
    feature_version: FeatureVersion,
    checkpoint: HistoryBackfillCheckpoint,
    historical_snapshot: HistoricalSnapshot,
    source_versions: Iterable[HistoricalSourceVersion],
    code_revision: str,
    final_outcome: GameFinalOutcome | None = None,
    computed_at: datetime | None = None,
    materializer_version: str = DEFAULT_MATERIALIZER_VERSION,
) -> HistoricalMaterializationResult:
    """Persist or reuse the exact snapshot/materialization pair.

    The immutable feature checksum is the pure semantic checksum and therefore
    excludes computation time and persistence-row identifiers.  Computation
    time is recorded separately on both ORM rows.  A changed semantic payload
    sharing the same ORM semantic key fails instead of silently overwriting it.
    """

    _require_text("code_revision", code_revision)
    _require_text("materializer_version", materializer_version)
    _require_persisted(session, game, Game, "game")
    _require_persisted(session, feature_version, FeatureVersion, "feature_version")
    _require_persisted(session, checkpoint, HistoryBackfillCheckpoint, "checkpoint")
    _validate_pure_snapshot(historical_snapshot, game, feature_version)

    sources = _resolve_sources(session, tuple(source_versions), historical_snapshot)
    source_by_key = {source.source_key: source for source in sources}
    if checkpoint.status != "SUCCEEDED":
        raise HistoricalMaterializationError("Checkpoint must be a successful terminal result")
    if checkpoint.source_mode != SOURCE_MODE:
        raise HistoricalMaterializationError("Checkpoint source mode must be LIVE")
    if checkpoint.temporal_semantics != TEMPORAL_SEMANTICS:
        raise HistoricalMaterializationError("Checkpoint population is not reconstruction")
    if checkpoint.source_version_id not in {source.id for source in sources}:
        raise HistoricalMaterializationError("Checkpoint source is absent from the source bundle")

    generated_at = as_utc(computed_at or datetime.now(UTC))
    if not _is_sha256(checkpoint.identity_checksum):
        raise HistoricalMaterializationError("Checkpoint identity checksum is invalid")
    checkpoint_completed = _orm_utc(checkpoint.completed_at)
    if generated_at < checkpoint_completed:
        raise HistoricalMaterializationError("Computation time precedes checkpoint completion")
    if generated_at < historical_snapshot.cutoff:
        raise HistoricalMaterializationError("Computation time precedes the historical cutoff")

    governed_outcome = _validate_outcome(
        session,
        final_outcome,
        game=game,
        scheduled_at=historical_snapshot.scheduled_first_pitch,
        computed_at=generated_at,
    )
    source_bundle = _source_bundle(sources)
    evidence_manifest = _evidence_manifest(
        session,
        historical_snapshot,
        source_by_key,
        computed_at=generated_at,
    )
    semantic_feature_checksum = _semantic_feature_checksum(
        historical_snapshot,
        feature_version,
    )
    source_bundle_checksum = _checksum(source_bundle)
    evidence_manifest_checksum = _checksum(evidence_manifest)
    cutoff_policy = {
        "policy_version": historical_snapshot.cutoff_policy_version,
        "hours_before_first_pitch": 6,
        "scheduled_first_pitch": _iso(historical_snapshot.scheduled_first_pitch),
        "cutoff_at": _iso(historical_snapshot.cutoff),
        "event_boundary": "STRICTLY_BEFORE_CUTOFF",
        "eligibility_boundary": "AT_OR_BEFORE_CUTOFF",
    }
    quality_components = historical_snapshot.quality.to_dict()
    identity_checksum = _materialization_identity(
        historical_snapshot=historical_snapshot,
        semantic_feature_checksum=semantic_feature_checksum,
        game=game,
        feature_version=feature_version,
        checkpoint=checkpoint,
        final_outcome=governed_outcome,
        sources=sources,
        cutoff_policy=cutoff_policy,
        materializer_version=materializer_version,
        code_revision=code_revision,
    )

    by_identity = session.scalar(
        select(HistoricalFeatureMaterialization).where(
            HistoricalFeatureMaterialization.identity_checksum == identity_checksum
        )
    )
    if by_identity is not None:
        feature_snapshot = session.get(
            GameFeatureSnapshot,
            by_identity.game_feature_snapshot_id,
        )
        if feature_snapshot is None:
            raise HistoricalMaterializationError(
                "Existing materialization references a missing feature snapshot"
            )
        _assert_snapshot_matches(
            feature_snapshot,
            historical_snapshot=historical_snapshot,
            feature_version=feature_version,
            evidence_manifest=evidence_manifest,
            semantic_feature_checksum=semantic_feature_checksum,
        )
        _assert_materialization_matches(
            by_identity,
            game=game,
            feature_snapshot=feature_snapshot,
            feature_version=feature_version,
            checkpoint=checkpoint,
            final_outcome=governed_outcome,
            historical_snapshot=historical_snapshot,
            source_bundle=source_bundle,
            source_bundle_checksum=source_bundle_checksum,
            evidence_manifest=evidence_manifest,
            evidence_manifest_checksum=evidence_manifest_checksum,
            cutoff_policy=cutoff_policy,
            materializer_version=materializer_version,
            code_revision=code_revision,
        )
        return HistoricalMaterializationResult(
            feature_snapshot=feature_snapshot,
            materialization=by_identity,
            reused_feature_snapshot=True,
            reused_materialization=True,
        )

    semantic_collision = session.scalar(
        select(HistoricalFeatureMaterialization).where(
            HistoricalFeatureMaterialization.game_id == game.id,
            HistoricalFeatureMaterialization.cutoff_at == historical_snapshot.cutoff,
            HistoricalFeatureMaterialization.feature_version_id == feature_version.id,
            HistoricalFeatureMaterialization.cutoff_policy_version
            == historical_snapshot.cutoff_policy_version,
            HistoricalFeatureMaterialization.source_mode == SOURCE_MODE,
            HistoricalFeatureMaterialization.materializer_version == materializer_version,
        )
    )
    if semantic_collision is not None:
        if (
            governed_outcome is not None
            and semantic_collision.game_final_outcome_id is not None
            and semantic_collision.game_final_outcome_id != governed_outcome.id
        ):
            raise HistoricalMaterializationError(
                "Corrected final outcome cannot be rebound under the current one-to-one "
                "snapshot materialization schema; the stale row remains ineligible"
            )
        raise HistoricalMaterializationError(
            "Historical materialization semantic collision; bump the version for changed semantics"
        )

    feature_snapshot = session.scalar(
        select(GameFeatureSnapshot).where(
            GameFeatureSnapshot.checksum == semantic_feature_checksum
        )
    )
    reused_snapshot = feature_snapshot is not None
    if feature_snapshot is None:
        feature_snapshot = GameFeatureSnapshot(
            game_id=game.id,
            generated_at=generated_at,
            data_as_of=historical_snapshot.cutoff,
            feature_schema_version=historical_snapshot.feature_schema_version,
            feature_version_id=feature_version.id,
            game_state_observation_id=None,
            home_lineup_observation_id=None,
            away_lineup_observation_id=None,
            home_starter_observation_id=None,
            away_starter_observation_id=None,
            weather_snapshot_id=None,
            park_factor_version_id=None,
            home_rating_observation_id=None,
            away_rating_observation_id=None,
            bullpen_observation_ids=[],
            roster_observation_ids=[],
            statcast_observation_ids=[],
            market_snapshot_ids=[],
            source_evidence=evidence_manifest,
            feature_values=_feature_values(historical_snapshot),
            feature_completeness=_measured_completeness(historical_snapshot),
            missing_features=list(historical_snapshot.missing_fields),
            stale_features=[],
            provisional_features=list(historical_snapshot.provisional_fields),
            bayesian_metadata=_snapshot_metadata(historical_snapshot),
            checksum=semantic_feature_checksum,
            source_mode=SOURCE_MODE,
            frozen_at=generated_at,
        )
        session.add(feature_snapshot)
        session.flush()
    else:
        _assert_snapshot_matches(
            feature_snapshot,
            historical_snapshot=historical_snapshot,
            feature_version=feature_version,
            evidence_manifest=evidence_manifest,
            semantic_feature_checksum=semantic_feature_checksum,
        )

    eligibility_status = "ELIGIBLE" if governed_outcome is not None else "INSUFFICIENT_DATA"
    eligibility_reasons = [] if governed_outcome is not None else ["FINAL_OUTCOME_UNAVAILABLE"]
    materialization = HistoricalFeatureMaterialization(
        game_id=game.id,
        game_feature_snapshot_id=feature_snapshot.id,
        feature_version_id=feature_version.id,
        game_final_outcome_id=governed_outcome.id if governed_outcome else None,
        checkpoint_id=checkpoint.id,
        scheduled_at=historical_snapshot.scheduled_first_pitch,
        cutoff_at=historical_snapshot.cutoff,
        generated_at=feature_snapshot.generated_at,
        cutoff_policy_version=historical_snapshot.cutoff_policy_version,
        cutoff_policy=cutoff_policy,
        source_bundle=source_bundle,
        source_bundle_checksum=source_bundle_checksum,
        evidence_manifest=evidence_manifest,
        evidence_manifest_checksum=evidence_manifest_checksum,
        quality_tier=historical_snapshot.tier.value,
        quality_score=historical_snapshot.quality.score,
        quality_components=quality_components,
        materializer_version=materializer_version,
        code_revision=code_revision,
        eligibility_status=eligibility_status,
        eligibility_reasons=eligibility_reasons,
        training_eligible=governed_outcome is not None,
        source_mode=SOURCE_MODE,
        temporal_semantics=TEMPORAL_SEMANTICS,
        identity_checksum=identity_checksum,
    )
    session.add(materialization)
    session.flush()
    return HistoricalMaterializationResult(
        feature_snapshot=feature_snapshot,
        materialization=materialization,
        reused_feature_snapshot=reused_snapshot,
        reused_materialization=False,
    )


def _validate_pure_snapshot(
    snapshot: HistoricalSnapshot,
    game: Game,
    feature_version: FeatureVersion,
) -> None:
    if snapshot.provenance is not ResearchProvenance.HISTORICAL_RECONSTRUCTION:
        raise HistoricalMaterializationError("Pure snapshot provenance is not reconstruction")
    if snapshot.tier in {EvidenceTier.C, EvidenceTier.EXCLUDED}:
        raise HistoricalMaterializationError("Tier C/EXCLUDED cannot be historical training evidence")
    if snapshot.tier not in {EvidenceTier.A, EvidenceTier.B}:
        raise HistoricalMaterializationError("Unsupported historical quality tier")
    if snapshot.quality.tier is not snapshot.tier:
        raise HistoricalMaterializationError("Snapshot and quality tiers disagree")
    if (
        not isfinite(snapshot.quality.score)
        or not 0 < snapshot.quality.score <= 100
        or not isclose(
            snapshot.quality.score,
            snapshot.quality.score_fraction * 100,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    ):
        raise HistoricalMaterializationError("Historical quality score is invalid")
    if str(game.id) != snapshot.game_id:
        raise HistoricalMaterializationError("Pure snapshot game identity does not match canonical game")
    if game.source_mode != SOURCE_MODE:
        raise HistoricalMaterializationError("Canonical game must use LIVE source isolation")
    if _orm_utc(game.starts_at) != as_utc(snapshot.scheduled_first_pitch):
        raise HistoricalMaterializationError("Pure snapshot schedule does not match canonical game")
    if snapshot.cutoff != deterministic_cutoff(snapshot.scheduled_first_pitch):
        raise HistoricalMaterializationError("Historical cutoff is not exact T-6h")
    accepted_feature_identities = {
        str(feature_version.id),
        f"{feature_version.name}/{feature_version.version}",
        feature_version.schema_hash,
    }
    if snapshot.feature_version_id not in accepted_feature_identities:
        raise HistoricalMaterializationError("Pure snapshot feature version is not the supplied version")
    if _sha256_text(snapshot.canonical_payload_json) != snapshot.checksum.lower():
        raise HistoricalMaterializationError("Pure snapshot checksum does not match its payload")
    try:
        payload = json.loads(snapshot.canonical_payload_json)
    except (TypeError, ValueError) as exc:
        raise HistoricalMaterializationError("Pure snapshot payload is not valid JSON") from exc
    required_payload_keys = {
        "game_id",
        "scheduled_first_pitch",
        "cutoff",
        "cutoff_policy_version",
        "builder_version",
        "feature_schema_version",
        "feature_version_id",
        "provenance",
        "tier",
        "quality_policy",
        "quality",
        "features",
        "evidence",
        "missing_fields",
        "provisional_fields",
        "unavailable_families",
        "timing_risk_families",
    }
    if not isinstance(payload, dict) or set(payload) != required_payload_keys:
        raise HistoricalMaterializationError("Pure snapshot payload shape is invalid")
    expected = {
        "game_id": snapshot.game_id,
        "scheduled_first_pitch": _iso(snapshot.scheduled_first_pitch),
        "cutoff": _iso(snapshot.cutoff),
        "cutoff_policy_version": snapshot.cutoff_policy_version,
        "builder_version": snapshot.builder_version,
        "feature_schema_version": snapshot.feature_schema_version,
        "feature_version_id": snapshot.feature_version_id,
        "provenance": snapshot.provenance.value,
        "tier": snapshot.tier.value,
        "quality": snapshot.quality.to_dict(),
        "features": [_pure_feature(item) for item in snapshot.features],
        "evidence": [_pure_evidence(item) for item in snapshot.evidence],
        "missing_fields": list(snapshot.missing_fields),
        "provisional_fields": list(snapshot.provisional_fields),
        "unavailable_families": list(snapshot.unavailable_families),
        "timing_risk_families": list(snapshot.timing_risk_families),
    }
    for key, expected_value in expected.items():
        if canonical_json(payload.get(key)) != canonical_json(expected_value):
            raise HistoricalMaterializationError(f"Pure snapshot payload disagrees on {key}")
    names = [item.name for item in snapshot.features]
    if len(names) != len(set(names)):
        raise HistoricalMaterializationError("Pure snapshot contains duplicate feature names")
    if any(_contains_target_leakage(item.name, item.value) for item in snapshot.features):
        raise HistoricalMaterializationError("Target outcome leaked into historical feature names")
    evidence_families = {item.family for item in snapshot.evidence}
    for feature in snapshot.features:
        if feature.value is not None and feature.family not in evidence_families:
            raise HistoricalMaterializationError(
                f"Observed feature family lacks evidence: {feature.family}"
            )
    for evidence in snapshot.evidence:
        if evidence.role is EvidenceRole.TARGET_OUTCOME:
            raise HistoricalMaterializationError("Target outcome cannot enter feature evidence")
        if evidence.role is EvidenceRole.FEATURE and evidence.game_id == snapshot.game_id:
            raise HistoricalMaterializationError("Target-game feature evidence is forbidden")
        if as_utc(evidence.event_at) >= snapshot.cutoff:
            raise HistoricalMaterializationError("Future/current evidence entered the snapshot")
        if as_utc(evidence.eligible_at) > snapshot.cutoff:
            raise HistoricalMaterializationError("Evidence was not eligible by the cutoff")
        if evidence.classification is EvidenceClassification.FORWARD_ONLY:
            raise HistoricalMaterializationError("Forward-only evidence cannot be reconstructed")


def _resolve_sources(
    session: Session,
    candidates: tuple[HistoricalSourceVersion, ...],
    snapshot: HistoricalSnapshot,
) -> tuple[HistoricalSourceVersion, ...]:
    if not candidates:
        raise HistoricalMaterializationError("Historical source-version bundle is required")
    resolved: list[HistoricalSourceVersion] = []
    keys: set[str] = set()
    identities: set[str] = set()
    for candidate in candidates:
        _require_persisted(session, candidate, HistoricalSourceVersion, "source version")
        source = session.get(HistoricalSourceVersion, candidate.id)
        if source is None:
            raise HistoricalMaterializationError("Unknown historical source version")
        if source.source_key in keys or source.identity_checksum in identities:
            raise HistoricalMaterializationError("Duplicate historical source version")
        keys.add(source.source_key)
        identities.add(source.identity_checksum)
        if source.source_mode != SOURCE_MODE:
            raise HistoricalMaterializationError("Historical source bundle contains non-LIVE data")
        if source.temporal_semantics != TEMPORAL_SEMANTICS:
            raise HistoricalMaterializationError("Historical source bundle mixes populations")
        source_tier = str(source.quality_tier).upper()
        if source_tier not in {"A", "B"}:
            raise HistoricalMaterializationError("Tier C/EXCLUDED sources cannot be reconstructed")
        if not source.training_eligible or not source.evaluation_eligible:
            raise HistoricalMaterializationError("Historical source version is not eligible")
        if source.source_classification == "SYNTHETIC":
            raise HistoricalMaterializationError("Synthetic sources cannot enter historical training")
        if not _is_sha256(source.identity_checksum):
            raise HistoricalMaterializationError("Source-version identity checksum is invalid")
        if _TIER_RISK[source_tier] > _TIER_RISK[snapshot.tier.value]:
            raise HistoricalMaterializationError("Snapshot quality tier overstates its source")
        resolved.append(source)
    evidence_keys = {item.source for item in snapshot.evidence}
    if evidence_keys != keys:
        missing = sorted(evidence_keys - keys)
        unused = sorted(keys - evidence_keys)
        raise HistoricalMaterializationError(
            f"Source bundle does not exactly cover evidence; missing={missing}, unused={unused}"
        )
    return tuple(
        sorted(
            resolved,
            key=lambda item: (item.source_key, item.dataset_version, item.identity_checksum),
        )
    )


def _validate_outcome(
    session: Session,
    outcome: GameFinalOutcome | None,
    *,
    game: Game,
    scheduled_at: datetime,
    computed_at: datetime,
) -> GameFinalOutcome | None:
    if outcome is None:
        return None
    _require_persisted(session, outcome, GameFinalOutcome, "final outcome")
    governed = session.get(GameFinalOutcome, outcome.id)
    if governed is None:
        raise HistoricalMaterializationError("Unknown governed final outcome")
    if governed.game_id != game.id or _orm_utc(governed.scheduled_at) != as_utc(scheduled_at):
        raise HistoricalMaterializationError("Final outcome identity does not match the game")
    if governed.season_id != game.season_id:
        raise HistoricalMaterializationError("Final outcome season does not match the game")
    sides = list(session.scalars(select(GameTeam).where(GameTeam.game_id == game.id)))
    side_map = {bool(side.is_home): side.team_id for side in sides}
    if (
        len(sides) != 2
        or len(side_map) != 2
        or side_map.get(True) != governed.home_team_id
        or side_map.get(False) != governed.away_team_id
    ):
        raise HistoricalMaterializationError("Final outcome teams do not match canonical game sides")
    if game.venue_id is not None and governed.venue_id not in {None, game.venue_id}:
        raise HistoricalMaterializationError("Final outcome venue does not match the game")
    if governed.source_mode != SOURCE_MODE or governed.temporal_semantics != TEMPORAL_SEMANTICS:
        raise HistoricalMaterializationError("Final outcome violates source isolation")
    if governed.outcome_status != "FINAL" or not governed.official:
        raise HistoricalMaterializationError("Final outcome is not an official FINAL target")
    if governed.home_team_id == governed.away_team_id:
        raise HistoricalMaterializationError("Final outcome has invalid team identity")
    if _orm_utc(governed.final_at) < as_utc(scheduled_at):
        raise HistoricalMaterializationError("Final outcome precedes the scheduled game")
    if _orm_utc(governed.retrieved_at) > computed_at:
        raise HistoricalMaterializationError("Final outcome was not acquired by computation time")
    if not _is_sha256(governed.payload_checksum) or not _is_sha256(governed.identity_checksum):
        raise HistoricalMaterializationError("Final outcome checksum is invalid")
    outcome_source = session.get(HistoricalSourceVersion, governed.source_version_id)
    if (
        outcome_source is None
        or outcome_source.source_mode != SOURCE_MODE
        or outcome_source.temporal_semantics != TEMPORAL_SEMANTICS
        or not outcome_source.evaluation_eligible
        or outcome_source.source_classification == "SYNTHETIC"
    ):
        raise HistoricalMaterializationError("Final outcome source is not governed for evaluation")
    outcome_checkpoint = session.get(HistoryBackfillCheckpoint, governed.checkpoint_id)
    if (
        outcome_checkpoint is None
        or outcome_checkpoint.status != "SUCCEEDED"
        or outcome_checkpoint.source_version_id != governed.source_version_id
        or outcome_checkpoint.source_mode != SOURCE_MODE
        or outcome_checkpoint.temporal_semantics != TEMPORAL_SEMANTICS
    ):
        raise HistoricalMaterializationError("Final outcome checkpoint is not governed")
    latest = session.scalar(
        select(GameFinalOutcome).where(
            GameFinalOutcome.game_id == game.id,
            GameFinalOutcome.source_version_id == governed.source_version_id,
            GameFinalOutcome.source_mode == SOURCE_MODE,
            GameFinalOutcome.retrieved_at <= computed_at,
        ).order_by(GameFinalOutcome.revision.desc(), GameFinalOutcome.id.desc())
    )
    if latest is None or latest.id != governed.id:
        raise HistoricalMaterializationError(
            "Final outcome is not the latest governed revision at computation time"
        )
    return governed


def _source_bundle(sources: tuple[HistoricalSourceVersion, ...]) -> list[dict[str, Any]]:
    return [
        {
            "source_version_id": str(source.id),
            "source_key": source.source_key,
            "dataset_version": source.dataset_version,
            "provider_name": source.provider_name,
            "source_classification": source.source_classification,
            "source_mode": source.source_mode,
            "temporal_semantics": source.temporal_semantics,
            "quality_tier": source.quality_tier,
            "policy_version": source.policy_version,
            "identity_checksum": source.identity_checksum,
        }
        for source in sources
    ]


def _evidence_manifest(
    session: Session,
    snapshot: HistoricalSnapshot,
    source_by_key: dict[str, HistoricalSourceVersion],
    *,
    computed_at: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in snapshot.evidence:
        if item.role is not EvidenceRole.FEATURE:
            raise HistoricalMaterializationError(
                "Structural evidence has no supported immutable canonical table; "
                "target outcomes are never feature evidence"
            )
        source = source_by_key.get(item.source)
        if source is None:
            raise HistoricalMaterializationError(f"Unknown evidence source: {item.source}")
        try:
            canonical_id = UUID(item.evidence_id)
        except (TypeError, ValueError) as exc:
            raise HistoricalMaterializationError(
                "Historical feature evidence must use a canonical history-row UUID"
            ) from exc
        matches = [
            (table_name, candidate)
            for table_name, model in _CANONICAL_EVIDENCE_MODELS.items()
            if (candidate := session.get(model, canonical_id)) is not None
        ]
        if not matches:
            raise HistoricalMaterializationError(
                "Historical feature evidence references a nonexistent canonical history row"
            )
        if len(matches) != 1:
            raise HistoricalMaterializationError(
                "Historical feature evidence UUID is ambiguous across canonical history tables"
            )
        canonical_table, canonical = matches[0]
        if canonical.source_version_id != source.id:
            raise HistoricalMaterializationError("Canonical evidence source version mismatch")
        if canonical.source_record_id != item.source_record_id:
            raise HistoricalMaterializationError("Canonical evidence source record mismatch")
        if item.game_id is None or str(canonical.game_id) != str(item.game_id):
            raise HistoricalMaterializationError("Canonical evidence game identity mismatch")
        if str(canonical.game_id) == snapshot.game_id:
            raise HistoricalMaterializationError("Target-game canonical history cannot enter features")
        if _orm_utc(canonical.event_at) != as_utc(item.event_at):
            raise HistoricalMaterializationError("Canonical evidence event timestamp mismatch")
        if _orm_utc(canonical.eligible_at) != as_utc(item.eligible_at):
            raise HistoricalMaterializationError("Canonical evidence eligibility timestamp mismatch")
        if canonical.payload_checksum.lower() != item.raw_sha256.lower():
            raise HistoricalMaterializationError("Canonical evidence payload checksum mismatch")
        if canonical.evidence_classification != item.classification.value:
            raise HistoricalMaterializationError("Canonical evidence classification mismatch")
        expected_tier = tier_for_classification(item.classification).value
        if canonical.quality_tier != expected_tier:
            raise HistoricalMaterializationError("Canonical evidence quality tier mismatch")

        checkpoint = session.get(HistoryBackfillCheckpoint, canonical.checkpoint_id)
        if checkpoint is None:
            raise HistoricalMaterializationError("Canonical evidence checkpoint is missing")
        outcome = session.get(GameFinalOutcome, canonical.final_outcome_id)
        if outcome is None:
            raise HistoricalMaterializationError("Canonical evidence final-outcome lineage is missing")
        if (
            checkpoint.status != "SUCCEEDED"
            or checkpoint.source_version_id != canonical.source_version_id
            or checkpoint.source_mode != SOURCE_MODE
            or checkpoint.temporal_semantics != TEMPORAL_SEMANTICS
            or _orm_utc(checkpoint.completed_at) > computed_at
        ):
            raise HistoricalMaterializationError("Canonical evidence checkpoint lineage is invalid")
        if (
            outcome.game_id != canonical.game_id
            or outcome.source_version_id != canonical.source_version_id
            or outcome.checkpoint_id != canonical.checkpoint_id
            or outcome.source_mode != SOURCE_MODE
            or outcome.temporal_semantics != TEMPORAL_SEMANTICS
            or outcome.outcome_status != "FINAL"
            or not outcome.official
        ):
            raise HistoricalMaterializationError("Canonical evidence outcome lineage is invalid")
        event_at = _orm_utc(canonical.event_at)
        evidence_end_at = _orm_utc(canonical.evidence_end_at)
        eligible_at = _orm_utc(canonical.eligible_at)
        retrieved_at = _orm_utc(canonical.retrieved_at)
        if (
            evidence_end_at > event_at
            or event_at >= snapshot.cutoff
            or evidence_end_at >= snapshot.cutoff
            or eligible_at > snapshot.cutoff
            or eligible_at < evidence_end_at
            or retrieved_at < evidence_end_at
            or retrieved_at > computed_at
        ):
            raise HistoricalMaterializationError("Canonical evidence timestamps are not cutoff-safe")
        if not all(
            _is_sha256(value)
            for value in (
                canonical.payload_checksum,
                canonical.identity_checksum,
                checkpoint.identity_checksum,
                source.identity_checksum,
            )
        ):
            raise HistoricalMaterializationError("Canonical evidence lineage checksum is invalid")
        rows.append({
            "evidence_id": str(canonical.id),
            "canonical_table": canonical_table,
            "canonical_id": str(canonical.id),
            "canonical_identity_checksum": canonical.identity_checksum.lower(),
            "family": item.family,
            "source": item.source,
            "source_record_id": canonical.source_record_id,
            "source_version_id": str(source.id),
            "source_version_identity_checksum": source.identity_checksum,
            "checkpoint_id": str(checkpoint.id),
            "checkpoint_identity_checksum": checkpoint.identity_checksum.lower(),
            "source_mode": SOURCE_MODE,
            "temporal_semantics": TEMPORAL_SEMANTICS,
            "game_id": str(canonical.game_id),
            "final_outcome_id": str(canonical.final_outcome_id),
            "event_at": _iso(event_at),
            "evidence_end_at": _iso(evidence_end_at),
            "retrieved_at": _iso(retrieved_at),
            "eligible_at": _iso(eligible_at),
            "availability_basis": canonical.availability_basis,
            "stat_schema_version": canonical.stat_schema_version,
            "evidence_classification": canonical.evidence_classification,
            "quality_tier": canonical.quality_tier,
            "role": item.role.value,
            "raw_sha256": canonical.payload_checksum.lower(),
            "payload_checksum": canonical.payload_checksum.lower(),
        })
    result = sorted(
        rows,
        key=lambda item: (
            str(item["family"]),
            str(item["source"]),
            str(item["source_record_id"]),
            str(item["evidence_id"]),
        ),
    )
    reason = canonical_evidence_manifest_error(
        session,
        result,
        cutoff=snapshot.cutoff,
        target_game_id=snapshot.game_id,
        source_version_ids={str(source.id) for source in source_by_key.values()},
        computed_at=computed_at,
    )
    if reason is not None:
        raise HistoricalMaterializationError(reason)
    return result


def canonical_evidence_manifest_error(
    session: Session,
    raw_evidence: Iterable[dict[str, Any]],
    *,
    cutoff: datetime,
    target_game_id: str,
    source_version_ids: set[str],
    computed_at: datetime,
) -> str | None:
    """Re-resolve every manifest row against immutable canonical history.

    This is intentionally usable by both persistence and training.  A JSON
    manifest is provenance metadata, not authority: the referenced ORM row,
    source release, checkpoint, and final-outcome lineage remain authoritative.
    """

    boundary = as_utc(cutoff)
    computation_time = as_utc(computed_at)
    rows = tuple(raw_evidence or ())
    if not rows:
        return "Historical evidence manifest is empty"
    seen: set[tuple[str, str]] = set()
    for item in rows:
        if not isinstance(item, dict):
            return "Historical evidence manifest row is invalid"
        if str(item.get("role") or "").upper() != EvidenceRole.FEATURE.value:
            return "Historical structural evidence is not backed by a supported immutable table"
        table_name = str(item.get("canonical_table") or "")
        model = _CANONICAL_EVIDENCE_MODELS.get(table_name)
        if model is None:
            return "Historical evidence canonical table is unsupported"
        canonical_id_text = str(item.get("canonical_id") or "")
        try:
            canonical_id = UUID(canonical_id_text)
        except (TypeError, ValueError):
            return "Historical evidence canonical UUID is invalid"
        key = (table_name, canonical_id_text)
        if key in seen:
            return "Historical evidence canonical reference is duplicated"
        seen.add(key)
        if str(item.get("evidence_id") or "") != canonical_id_text:
            return "Historical evidence alias does not equal its canonical UUID"
        canonical = session.get(model, canonical_id)
        if canonical is None:
            return "Historical evidence canonical row does not exist"
        source_version_id = str(canonical.source_version_id)
        if source_version_id not in source_version_ids:
            return "Historical evidence source is absent from the governed bundle"
        source = session.get(HistoricalSourceVersion, canonical.source_version_id)
        checkpoint = session.get(HistoryBackfillCheckpoint, canonical.checkpoint_id)
        outcome = session.get(GameFinalOutcome, canonical.final_outcome_id)
        if source is None or checkpoint is None or outcome is None:
            return "Historical evidence canonical lineage is incomplete"
        expected_tier = tier_for_classification(
            EvidenceClassification(canonical.evidence_classification)
        ).value
        if canonical.quality_tier != expected_tier:
            return "Historical evidence canonical tier contradicts its classification"
        if (
            canonical.source_mode != SOURCE_MODE
            or canonical.temporal_semantics != TEMPORAL_SEMANTICS
            or source.source_mode != SOURCE_MODE
            or source.temporal_semantics != TEMPORAL_SEMANTICS
            or not source.training_eligible
            or not source.evaluation_eligible
        ):
            return "Historical evidence canonical source isolation failed"
        if (
            checkpoint.status != "SUCCEEDED"
            or checkpoint.source_version_id != canonical.source_version_id
            or checkpoint.source_mode != SOURCE_MODE
            or checkpoint.temporal_semantics != TEMPORAL_SEMANTICS
            or _orm_utc(checkpoint.completed_at) > computation_time
        ):
            return "Historical evidence canonical checkpoint lineage failed"
        if (
            outcome.game_id != canonical.game_id
            or outcome.source_version_id != canonical.source_version_id
            or outcome.checkpoint_id != canonical.checkpoint_id
            or outcome.source_mode != SOURCE_MODE
            or outcome.temporal_semantics != TEMPORAL_SEMANTICS
            or outcome.outcome_status != "FINAL"
            or not outcome.official
        ):
            return "Historical evidence canonical outcome lineage failed"
        event_at = _orm_utc(canonical.event_at)
        evidence_end_at = _orm_utc(canonical.evidence_end_at)
        eligible_at = _orm_utc(canonical.eligible_at)
        retrieved_at = _orm_utc(canonical.retrieved_at)
        if (
            str(canonical.game_id) == target_game_id
            or evidence_end_at > event_at
            or event_at >= boundary
            or evidence_end_at >= boundary
            or eligible_at > boundary
            or eligible_at < evidence_end_at
            or retrieved_at < evidence_end_at
            or retrieved_at > computation_time
        ):
            return "Historical evidence canonical timestamps are not cutoff-safe"
        expected = {
            "canonical_identity_checksum": canonical.identity_checksum.lower(),
            "source": source.source_key,
            "source_record_id": canonical.source_record_id,
            "source_version_id": source_version_id,
            "source_version_identity_checksum": source.identity_checksum,
            "checkpoint_id": str(checkpoint.id),
            "checkpoint_identity_checksum": checkpoint.identity_checksum.lower(),
            "source_mode": SOURCE_MODE,
            "temporal_semantics": TEMPORAL_SEMANTICS,
            "game_id": str(canonical.game_id),
            "final_outcome_id": str(canonical.final_outcome_id),
            "event_at": _iso(event_at),
            "evidence_end_at": _iso(evidence_end_at),
            "retrieved_at": _iso(retrieved_at),
            "eligible_at": _iso(eligible_at),
            "availability_basis": canonical.availability_basis,
            "stat_schema_version": canonical.stat_schema_version,
            "evidence_classification": canonical.evidence_classification,
            "quality_tier": canonical.quality_tier,
            "raw_sha256": canonical.payload_checksum.lower(),
            "payload_checksum": canonical.payload_checksum.lower(),
        }
        if any(str(item.get(name) or "") != str(value) for name, value in expected.items()):
            return "Historical evidence manifest does not match its canonical row"
        if not all(
            _is_sha256(value)
            for value in (
                canonical.payload_checksum,
                canonical.identity_checksum,
                checkpoint.identity_checksum,
                source.identity_checksum,
            )
        ):
            return "Historical evidence canonical checksum is invalid"
    return None


def _materialization_identity(
    *,
    historical_snapshot: HistoricalSnapshot,
    semantic_feature_checksum: str,
    game: Game,
    feature_version: FeatureVersion,
    checkpoint: HistoryBackfillCheckpoint,
    final_outcome: GameFinalOutcome | None,
    sources: tuple[HistoricalSourceVersion, ...],
    cutoff_policy: dict[str, Any],
    materializer_version: str,
    code_revision: str,
) -> str:
    """Build identity from semantic checksums, avoiding persistence UUIDs."""

    return _checksum({
        "semantic_feature_checksum": semantic_feature_checksum,
        "game_semantics": {
            "game_id": historical_snapshot.game_id,
            "scheduled_first_pitch": _iso(historical_snapshot.scheduled_first_pitch),
        },
        "feature_version": {
            "name": feature_version.name,
            "version": feature_version.version,
            "schema_hash": feature_version.schema_hash,
        },
        "checkpoint_identity_checksum": checkpoint.identity_checksum,
        "final_outcome_identity_checksum": (
            final_outcome.identity_checksum if final_outcome else None
        ),
        "source_version_identity_checksums": [
            source.identity_checksum for source in sources
        ],
        "cutoff_policy": cutoff_policy,
        "quality": historical_snapshot.quality.to_dict(),
        "materializer_version": materializer_version,
        "code_revision": code_revision,
    })


def _assert_snapshot_matches(
    persisted: GameFeatureSnapshot,
    *,
    historical_snapshot: HistoricalSnapshot,
    feature_version: FeatureVersion,
    evidence_manifest: list[dict[str, Any]],
    semantic_feature_checksum: str,
) -> None:
    expected = {
        "game_id": str(historical_snapshot.game_id),
        "data_as_of": _iso(historical_snapshot.cutoff),
        "feature_schema_version": historical_snapshot.feature_schema_version,
        "feature_version_id": str(feature_version.id),
        "source_mode": SOURCE_MODE,
        "source_evidence": evidence_manifest,
        "feature_values": _feature_values(historical_snapshot),
        "feature_completeness": _measured_completeness(historical_snapshot),
        "missing_features": list(historical_snapshot.missing_fields),
        "stale_features": [],
        "provisional_features": list(historical_snapshot.provisional_fields),
        "bayesian_metadata": _snapshot_metadata(historical_snapshot),
        "checksum": semantic_feature_checksum,
    }
    actual = {
        "game_id": str(persisted.game_id),
        "data_as_of": _iso_orm(persisted.data_as_of),
        "feature_schema_version": persisted.feature_schema_version,
        "feature_version_id": str(persisted.feature_version_id),
        "source_mode": persisted.source_mode,
        "source_evidence": persisted.source_evidence,
        "feature_values": persisted.feature_values,
        "feature_completeness": persisted.feature_completeness,
        "missing_features": persisted.missing_features,
        "stale_features": persisted.stale_features,
        "provisional_features": persisted.provisional_features,
        "bayesian_metadata": persisted.bayesian_metadata,
        "checksum": persisted.checksum,
    }
    if canonical_json(actual) != canonical_json(expected):
        raise HistoricalMaterializationError("Feature snapshot checksum collision")
    if any((
        persisted.game_state_observation_id,
        persisted.home_lineup_observation_id,
        persisted.away_lineup_observation_id,
        persisted.home_starter_observation_id,
        persisted.away_starter_observation_id,
        persisted.weather_snapshot_id,
        persisted.park_factor_version_id,
        persisted.home_rating_observation_id,
        persisted.away_rating_observation_id,
    )):
        raise HistoricalMaterializationError("Historical snapshot references operational evidence")
    if any((
        persisted.bullpen_observation_ids,
        persisted.roster_observation_ids,
        persisted.statcast_observation_ids,
        persisted.market_snapshot_ids,
    )):
        raise HistoricalMaterializationError("Historical snapshot contains operational reference IDs")


def _assert_materialization_matches(
    persisted: HistoricalFeatureMaterialization,
    **expected_values: Any,
) -> None:
    game: Game = expected_values["game"]
    feature_snapshot: GameFeatureSnapshot = expected_values["feature_snapshot"]
    feature_version: FeatureVersion = expected_values["feature_version"]
    checkpoint: HistoryBackfillCheckpoint = expected_values["checkpoint"]
    final_outcome: GameFinalOutcome | None = expected_values["final_outcome"]
    snapshot: HistoricalSnapshot = expected_values["historical_snapshot"]
    expected = {
        "game_id": str(game.id),
        "game_feature_snapshot_id": str(feature_snapshot.id),
        "feature_version_id": str(feature_version.id),
        "game_final_outcome_id": str(final_outcome.id) if final_outcome else None,
        "checkpoint_id": str(checkpoint.id),
        "scheduled_at": _iso(snapshot.scheduled_first_pitch),
        "cutoff_at": _iso(snapshot.cutoff),
        "cutoff_policy_version": snapshot.cutoff_policy_version,
        "cutoff_policy": expected_values["cutoff_policy"],
        "source_bundle": expected_values["source_bundle"],
        "source_bundle_checksum": expected_values["source_bundle_checksum"],
        "evidence_manifest": expected_values["evidence_manifest"],
        "evidence_manifest_checksum": expected_values["evidence_manifest_checksum"],
        "quality_tier": snapshot.tier.value,
        "quality_score": snapshot.quality.score,
        "quality_components": snapshot.quality.to_dict(),
        "materializer_version": expected_values["materializer_version"],
        "code_revision": expected_values["code_revision"],
        "eligibility_status": "ELIGIBLE" if final_outcome else "INSUFFICIENT_DATA",
        "eligibility_reasons": [] if final_outcome else ["FINAL_OUTCOME_UNAVAILABLE"],
        "training_eligible": final_outcome is not None,
        "source_mode": SOURCE_MODE,
        "temporal_semantics": TEMPORAL_SEMANTICS,
    }
    actual = {
        "game_id": str(persisted.game_id),
        "game_feature_snapshot_id": str(persisted.game_feature_snapshot_id),
        "feature_version_id": str(persisted.feature_version_id),
        "game_final_outcome_id": (
            str(persisted.game_final_outcome_id)
            if persisted.game_final_outcome_id
            else None
        ),
        "checkpoint_id": str(persisted.checkpoint_id),
        "scheduled_at": _iso_orm(persisted.scheduled_at),
        "cutoff_at": _iso_orm(persisted.cutoff_at),
        "cutoff_policy_version": persisted.cutoff_policy_version,
        "cutoff_policy": persisted.cutoff_policy,
        "source_bundle": persisted.source_bundle,
        "source_bundle_checksum": persisted.source_bundle_checksum,
        "evidence_manifest": persisted.evidence_manifest,
        "evidence_manifest_checksum": persisted.evidence_manifest_checksum,
        "quality_tier": persisted.quality_tier,
        "quality_score": persisted.quality_score,
        "quality_components": persisted.quality_components,
        "materializer_version": persisted.materializer_version,
        "code_revision": persisted.code_revision,
        "eligibility_status": persisted.eligibility_status,
        "eligibility_reasons": persisted.eligibility_reasons,
        "training_eligible": persisted.training_eligible,
        "source_mode": persisted.source_mode,
        "temporal_semantics": persisted.temporal_semantics,
    }
    if canonical_json(actual) != canonical_json(expected):
        raise HistoricalMaterializationError("Materialization identity collision")


def _feature_values(snapshot: HistoricalSnapshot) -> dict[str, Any]:
    return {item.name: _json_value(item.value) for item in snapshot.features}


def _semantic_feature_checksum(
    snapshot: HistoricalSnapshot,
    feature_version: FeatureVersion,
) -> str:
    """Normalize the persistence UUID out of the pure semantic payload."""

    payload = json.loads(snapshot.canonical_payload_json)
    payload["feature_version_id"] = {
        "name": feature_version.name,
        "version": feature_version.version,
        "schema_hash": feature_version.schema_hash,
    }
    return _checksum(payload)


def _measured_completeness(snapshot: HistoricalSnapshot) -> float:
    feature_values = _feature_values(snapshot)
    expected = set(feature_values) | set(snapshot.missing_fields)
    if not expected:
        return 0.0
    observed = sum(feature_values.get(name) is not None for name in expected)
    return observed / len(expected)


def _snapshot_metadata(snapshot: HistoricalSnapshot) -> dict[str, Any]:
    return {
        "historical_reconstruction": {
            "pure_snapshot_checksum": snapshot.checksum,
            "snapshot_method": snapshot.method,
            "builder_version": snapshot.builder_version,
            "cutoff_policy_version": snapshot.cutoff_policy_version,
            "quality": snapshot.quality.to_dict(),
            "unavailable_families": list(snapshot.unavailable_families),
            "timing_risk_families": list(snapshot.timing_risk_families),
        }
    }


def _pure_feature(item: Any) -> dict[str, Any]:
    return {"name": item.name, "family": item.family, "value": _json_value(item.value)}


def _pure_evidence(item: Any) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "family": item.family,
        "source": item.source,
        "source_record_id": item.source_record_id,
        "raw_sha256": item.raw_sha256.lower(),
        "game_id": item.game_id,
        "event_at": _iso(item.event_at),
        "eligible_at": _iso(item.eligible_at),
        "classification": item.classification.value,
        "tier": tier_for_classification(item.classification).value,
        "role": item.role.value,
    }


def _json_value(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _require_persisted(
    session: Session,
    value: Any,
    expected_type: type[Any],
    name: str,
) -> None:
    if not isinstance(value, expected_type) or getattr(value, "id", None) is None:
        raise HistoricalMaterializationError(f"Persisted {name} is required")
    persisted = session.get(expected_type, value.id)
    if persisted is None:
        raise HistoricalMaterializationError(f"Unknown {name}")


def _require_text(name: str, value: str) -> None:
    if not value:
        raise HistoricalMaterializationError(f"{name} is required")


def _iso(value: datetime) -> str:
    return as_utc(value).isoformat().replace("+00:00", "Z")


def _iso_orm(value: datetime) -> str:
    return _orm_utc(value).isoformat().replace("+00:00", "Z")


def _orm_utc(value: datetime) -> datetime:
    """Normalize ORM timestamps; SQLite drops timezone metadata in tests."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _contains_target_leakage(name: str, value: Any) -> bool:
    normalized = name.lower()
    if normalized in _FORBIDDEN_TARGET_NAMES or any(
        token in normalized for token in _FORBIDDEN_TARGET_TOKENS
    ):
        return True
    if isinstance(value, dict):
        return any(
            _contains_target_leakage(str(key), nested)
            for key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_target_leakage("", nested) for nested in value)
    return False


def _checksum(value: Any) -> str:
    return _sha256_text(canonical_json(value))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )
