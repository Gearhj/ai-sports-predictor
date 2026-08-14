"""Provider-free ORM and artifact integration for Gate 3A Stage A2.

This module is deliberately an offline boundary.  It reads only canonical A1
warehouse rows, converts their literal MLB box-score fields into the pure A2
engine contract, and persists compact immutable attestations and snapshots.
It imports no provider, market, estimator, or model-fitting implementation.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.historical.a2_reconstruction import (
    A2_BUILDER_VERSION,
    A2_CUTOFF_POLICY_VERSION,
    A2_LINEAGE_VERSION,
    A2ChronologicalReconstructor,
    A2EvidenceTier,
    A2FeatureRowDraft,
    A2FinalOutcomeInput,
    A2PlayerGameFactInput,
    A2PlayerRole,
    A2Population,
    A2ReconstructionConfig,
    A2TargetGameInput,
    A2TeamGameFactInput,
    A2VenueInput,
    feature_identity_for,
)
from app.historical.a2_schema import A2_SCHEMA, A2_SCHEMA_HASH, A2_SCHEMA_VERSION
from app.models import (
    ExternalIdMapping,
    FeatureVersion,
    Game,
    GameFeatureSnapshot,
    GameFinalOutcome,
    GameTeam,
    HistoricalEvidenceStateAttestation,
    HistoricalFeatureMaterialization,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    PlayerGameHistoryObservation,
    Season,
    TeamGameHistoryObservation,
    TrainingDatasetManifest,
    Venue,
)
from app.training.dataset import (
    DatasetBuildResult,
    TrainingDatasetArtifactStore,
    TrainingDatasetManifestData,
    TrainingRow,
)
from app.training.readiness import (
    DatasetReadinessPolicy,
    DatasetReadinessResult,
    REQUIRED_FEATURE_FAMILIES,
    audit_historical_dataset_readiness,
)


A2_FEATURE_VERSION_NAME = "mlb_historical_pregame_a2"
A2_MATERIALIZER_VERSION = A2_BUILDER_VERSION
A2_DATASET_POPULATION = "HISTORICAL_RECONSTRUCTION_A2"
A2_TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"
A2_SOURCE_MODE = "LIVE"
A2_TARGET = "multi_target_mlb_game"
A2_TARGET_DEFINITIONS = {
    "home_win": "1 when governed final home_score exceeds away_score; otherwise 0",
    "home_runs": "governed append-only final home score",
    "away_runs": "governed append-only final away score",
    "run_differential": "home_runs - away_runs",
    "total_runs": "home_runs + away_runs",
}
DEFAULT_RAIN_SHORTENED_GAME_PKS = frozenset(("777188", "777474"))
DEFAULT_A2_MIN_FREE_BYTES_AFTER_WRITE = 100 * 1024 * 1024 * 1024
_FORBIDDEN_FEATURE_TOKENS = (
    "final_score",
    "home_score",
    "away_score",
    "home_win",
    "winner",
    "game_result",
    "market",
    "odds",
    "sportsbook",
    "closing",
    "win_expectancy",
)
_CHECKSUM_CHARS = frozenset("0123456789abcdef")


class A2PipelineError(RuntimeError):
    """Raised when A2 integration cannot preserve governed identity."""


@dataclass(frozen=True, slots=True)
class A2PipelinePolicy:
    """Exact warehouse population contract for one A2 run."""

    season: int = 2025
    mapping_source: str = "MLB_STATS_API"
    expected_tier_a_count: int | None = 2428
    expected_tier_b_count: int | None = 2
    target_official_date_start: date | None = None
    target_official_date_end: date | None = None
    expected_selected_target_count: int | None = None
    rain_shortened_game_pks: frozenset[str] = DEFAULT_RAIN_SHORTENED_GAME_PKS
    require_exact_rain_set: bool = True

    def __post_init__(self) -> None:
        if self.season != 2025:
            raise ValueError("Gate 3A A2 is fixed to the exact 2025 MLB population")
        if not self.mapping_source.strip():
            raise ValueError("mapping_source is required")
        for name in (
            "expected_tier_a_count",
            "expected_tier_b_count",
            "expected_selected_target_count",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        start = self.target_official_date_start
        end = self.target_official_date_end
        if (start is None) != (end is None):
            raise ValueError("Both target official-date bounds must be supplied together")
        if start is not None:
            if start.year != self.season or end is None or end.year != self.season:
                raise ValueError("A2 target official-date bounds must remain inside 2025")
            if end < start:
                raise ValueError("target_official_date_end precedes its start")


@dataclass(frozen=True, slots=True)
class A2EvidenceReference:
    """Compact source/checkpoint bridge for one pure-engine record identity."""

    identity_checksum: str
    source_version_id: UUID
    checkpoint_id: UUID
    source_identity_checksum: str
    checkpoint_identity_checksum: str
    source_key: str
    dataset_version: str
    quality_tier: str
    evidence_classification: str
    component_identity_checksums: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class A2StructuralReference:
    """Content-addressed policy/structure used without a warehouse fact row."""

    identity_checksum: str
    kind: str
    definition: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class A2OrmInputBundle:
    population: A2Population
    season_id: UUID
    target_games: tuple[A2TargetGameInput, ...]
    final_outcomes: tuple[A2FinalOutcomeInput, ...]
    team_facts: tuple[A2TeamGameFactInput, ...]
    player_facts: tuple[A2PlayerGameFactInput, ...]
    venues: tuple[A2VenueInput, ...]
    target_outcome_ids: Mapping[str, UUID]
    target_checkpoint_ids: Mapping[str, UUID]
    target_game_pks: Mapping[str, str]
    evidence_references: Mapping[str, A2EvidenceReference]
    structural_references: Mapping[str, A2StructuralReference]
    target_references: Mapping[str, A2EvidenceReference]
    input_checksum: str


@dataclass(frozen=True, slots=True)
class A2MaterializationRunResult:
    population: A2Population
    feature_version_id: UUID
    target_count: int
    inserted_attestations: int
    reused_attestations: int
    inserted_snapshots: int
    reused_snapshots: int
    inserted_materializations: int
    reused_materializations: int
    committed_batches: int
    input_checksum: str


@dataclass(frozen=True, slots=True)
class A2DatasetPersistenceResult:
    population: A2Population
    build: DatasetBuildResult
    artifact: TrainingDatasetManifestData
    manifest_id: UUID
    readiness: DatasetReadinessResult


@dataclass(frozen=True, slots=True)
class A2PipelineRunResult:
    inputs: A2OrmInputBundle
    materialization: A2MaterializationRunResult
    dataset: A2DatasetPersistenceResult


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _checksum(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _dataset_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _CHECKSUM_CHARS for character in value)
    )


def _require_sha256(name: str, value: object) -> str:
    if not _is_sha256(value):
        raise A2PipelineError(f"{name} is not a lowercase SHA-256 checksum")
    return str(value)


def _tier(classification: str, quality_tier: str) -> A2EvidenceTier:
    expected = {
        "RECONSTRUCTABLE_POINT_IN_TIME": "A",
        "PARTIALLY_RECONSTRUCTABLE": "B",
    }.get(classification)
    if expected is None or quality_tier != expected:
        raise A2PipelineError(
            "Historical evidence classification and quality tier do not reconcile"
        )
    return A2EvidenceTier(quality_tier)


def _latest_revisions(
    rows: Sequence[Any],
    *,
    key,
    supersedes_attribute: str,
) -> tuple[Any, ...]:
    grouped: dict[object, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    latest: list[Any] = []
    for semantic_key, revisions in grouped.items():
        ordered = sorted(revisions, key=lambda item: (item.revision, str(item.id)))
        if [item.revision for item in ordered] != list(range(1, len(ordered) + 1)):
            raise A2PipelineError(
                f"Revision chain is incomplete or forked for {semantic_key!r}"
            )
        for index, row in enumerate(ordered):
            supersedes = getattr(row, supersedes_attribute)
            expected = None if index == 0 else ordered[index - 1].id
            if supersedes != expected:
                raise A2PipelineError(
                    f"Revision chain supersession is invalid for {semantic_key!r}"
                )
        latest.append(ordered[-1])
    return tuple(latest)


def _source_checkpoint(
    session: Session,
    row: Any,
    *,
    official_date: date,
    season: int,
    cache: dict[tuple[UUID, UUID], tuple[HistoricalSourceVersion, HistoryBackfillCheckpoint]],
) -> tuple[HistoricalSourceVersion, HistoryBackfillCheckpoint]:
    cache_key = (row.source_version_id, row.checkpoint_id)
    cached = cache.get(cache_key)
    if cached is None:
        source = session.get(HistoricalSourceVersion, row.source_version_id)
        checkpoint = session.get(HistoryBackfillCheckpoint, row.checkpoint_id)
        if source is None or checkpoint is None:
            raise A2PipelineError("Canonical evidence has missing source/checkpoint lineage")
        cache[cache_key] = (source, checkpoint)
    else:
        source, checkpoint = cached
    if (
        source.source_mode != A2_SOURCE_MODE
        or source.temporal_semantics != A2_TEMPORAL_SEMANTICS
        or source.source_classification != "OFFICIAL_PRIMARY"
        or not source.training_eligible
        or not source.evaluation_eligible
        or source.quality_tier not in {"A", "B"}
    ):
        raise A2PipelineError("Historical source version is not A2 eligible")
    governance = source.governance or {}
    if governance.get("approval_status") != "APPROVED":
        raise A2PipelineError("Historical source governance is not approved")
    if governance.get("population") != "HISTORICAL_RECONSTRUCTION":
        raise A2PipelineError("Historical source governance population is invalid")
    if (
        checkpoint.source_version_id != source.id
        or checkpoint.status != "SUCCEEDED"
        or checkpoint.source_mode != A2_SOURCE_MODE
        or checkpoint.temporal_semantics != A2_TEMPORAL_SEMANTICS
        or checkpoint.season not in {None, season}
    ):
        raise A2PipelineError("Historical checkpoint is not A2 eligible")
    for name, checksum in (
        ("source identity", source.identity_checksum),
        ("checkpoint identity", checkpoint.identity_checksum),
        ("checkpoint query", checkpoint.query_fingerprint),
    ):
        _require_sha256(name, checksum)
    for checksum in checkpoint.raw_checksums or []:
        _require_sha256("checkpoint raw", checksum)
    completed = _as_utc(checkpoint.completed_at)
    if _as_utc(source.effective_from) > completed:
        raise A2PipelineError("Checkpoint predates its governed source version")
    if source.effective_to is not None and _as_utc(source.effective_to) < completed:
        raise A2PipelineError("Checkpoint falls outside its governed source window")
    event_at = datetime.combine(official_date, time.min, tzinfo=UTC)
    if checkpoint.event_start_at is not None and _as_utc(checkpoint.event_start_at) > event_at:
        raise A2PipelineError("Checkpoint starts after its canonical official date")
    if checkpoint.event_end_at is not None:
        end_of_date = datetime.combine(official_date, time.max, tzinfo=UTC)
        if _as_utc(checkpoint.event_end_at) < end_of_date:
            raise A2PipelineError("Checkpoint ends before its canonical official date")
    return source, checkpoint


def _mapping_index(
    session: Session,
    *,
    source: str,
) -> dict[tuple[str, UUID], str]:
    result: dict[tuple[str, UUID], str] = {}
    rows = session.scalars(
        select(ExternalIdMapping).where(
            ExternalIdMapping.source == source,
            ExternalIdMapping.entity_type.in_(("game", "team", "player", "venue")),
        )
    )
    for row in rows:
        key = (row.entity_type, row.entity_id)
        if key in result:
            raise A2PipelineError(
                f"Canonical {row.entity_type} has multiple exact {source} mappings"
            )
        if not row.external_id or not row.external_id.strip():
            raise A2PipelineError("External mapping contains an empty identifier")
        result[key] = row.external_id
    return result


def _mapped(
    mappings: Mapping[tuple[str, UUID], str], entity_type: str, entity_id: UUID
) -> str:
    value = mappings.get((entity_type, entity_id))
    if value is None:
        raise A2PipelineError(
            f"Canonical {entity_type} {entity_id} has no exact MLB mapping"
        )
    return value


def _reference(
    *,
    identity_checksum: str,
    source: HistoricalSourceVersion,
    checkpoint: HistoryBackfillCheckpoint,
    quality_tier: str,
    evidence_classification: str,
    components: Iterable[str],
) -> A2EvidenceReference:
    component_tuple = tuple(sorted(components))
    for item in component_tuple:
        _require_sha256("canonical component identity", item)
    return A2EvidenceReference(
        identity_checksum=_require_sha256("A2 evidence identity", identity_checksum),
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        source_identity_checksum=_require_sha256(
            "source identity", source.identity_checksum
        ),
        checkpoint_identity_checksum=_require_sha256(
            "checkpoint identity", checkpoint.identity_checksum
        ),
        source_key=source.source_key,
        dataset_version=source.dataset_version,
        quality_tier=quality_tier,
        evidence_classification=evidence_classification,
        component_identity_checksums=component_tuple,
    )


def _literal_nonnegative_number(
    values: Mapping[str, Any], key: str, *, required: bool = False
) -> float | None:
    value = values.get(key)
    if value is None:
        if required:
            raise A2PipelineError(f"Required MLB statistic {key!r} is absent")
        return None
    if isinstance(value, bool):
        raise A2PipelineError(f"MLB statistic {key!r} cannot be boolean")
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise A2PipelineError(f"MLB statistic {key!r} is not numeric") from exc
    if not isfinite(converted) or converted < 0:
        raise A2PipelineError(f"MLB statistic {key!r} must be finite and non-negative")
    return converted


def _literal_nonnegative_int(
    values: Mapping[str, Any], key: str, *, required: bool = False
) -> int | None:
    converted = _literal_nonnegative_number(values, key, required=required)
    if converted is None:
        return None
    if not converted.is_integer():
        raise A2PipelineError(f"MLB count statistic {key!r} is not an integer")
    return int(converted)


def _literal_bool(values: Mapping[str, Any], key: str) -> bool:
    value = values.get(key, False)
    if not isinstance(value, bool):
        raise A2PipelineError(f"MLB identity field {key!r} must be boolean")
    return value


def _outs_recorded(pitching: Mapping[str, Any]) -> int | None:
    explicit = _literal_nonnegative_int(pitching, "outs")
    innings = pitching.get("inningsPitched")
    if explicit is not None:
        if innings is not None and _innings_to_outs(innings) != explicit:
            raise A2PipelineError("MLB outs and inningsPitched disagree")
        return explicit
    return _innings_to_outs(innings) if innings is not None else None


def _innings_to_outs(value: Any) -> int:
    text = str(value).strip()
    pieces = text.split(".")
    if len(pieces) > 2 or not pieces[0].isdigit():
        raise A2PipelineError("MLB inningsPitched is malformed")
    whole = int(pieces[0])
    partial = 0 if len(pieces) == 1 else int(pieces[1])
    if partial not in {0, 1, 2}:
        raise A2PipelineError("MLB inningsPitched partial inning must be 0, 1, or 2")
    return whole * 3 + partial


def _validate_common_row(row: Any, *, season_id: UUID) -> None:
    if (
        row.season_id != season_id
        or row.source_mode != A2_SOURCE_MODE
        or row.temporal_semantics != A2_TEMPORAL_SEMANTICS
    ):
        raise A2PipelineError("Historical row crosses season/source/temporal isolation")
    _require_sha256("payload checksum", row.payload_checksum)
    _require_sha256("identity checksum", row.identity_checksum)
    _tier(row.evidence_classification, row.quality_tier)
    if _as_utc(row.evidence_end_at) > _as_utc(row.event_at):
        raise A2PipelineError("Historical evidence_end_at exceeds event_at")
    if _as_utc(row.eligible_at) < _as_utc(row.evidence_end_at):
        raise A2PipelineError("Historical eligibility predates its evidence")


def load_a2_orm_inputs(
    session: Session,
    *,
    population: A2Population = A2Population.TIER_A_STRICT,
    policy: A2PipelinePolicy = A2PipelinePolicy(),
    config: A2ReconstructionConfig = A2ReconstructionConfig(),
) -> A2OrmInputBundle:
    """Load correction-latest exact-2025 canonical inputs without providers."""

    population = A2Population(population)
    year_start = date(policy.season, 1, 1)
    year_end = date(policy.season, 12, 31)
    all_outcome_rows = tuple(
        session.scalars(
            select(GameFinalOutcome)
            .where(
                GameFinalOutcome.official_date >= year_start,
                GameFinalOutcome.official_date <= year_end,
                GameFinalOutcome.source_mode == A2_SOURCE_MODE,
                GameFinalOutcome.temporal_semantics == A2_TEMPORAL_SEMANTICS,
                GameFinalOutcome.official.is_(True),
            )
            .order_by(
                GameFinalOutcome.game_id,
                GameFinalOutcome.source_version_id,
                GameFinalOutcome.revision,
            )
        )
    )
    latest_outcomes = _latest_revisions(
        all_outcome_rows,
        key=lambda row: (row.game_id, row.source_version_id, row.source_mode),
        supersedes_attribute="supersedes_outcome_id",
    )
    by_game: dict[UUID, GameFinalOutcome] = {}
    outcome_revision_ids_by_game: dict[UUID, set[UUID]] = defaultdict(set)
    for row in all_outcome_rows:
        outcome_revision_ids_by_game[row.game_id].add(row.id)
    for outcome in latest_outcomes:
        if outcome.game_id in by_game:
            raise A2PipelineError(
                "Multiple eligible correction-latest outcomes exist for one game"
            )
        by_game[outcome.game_id] = outcome
    if not by_game:
        raise A2PipelineError("No governed 2025 final outcomes are available")

    season_ids = {row.season_id for row in by_game.values()}
    if len(season_ids) != 1:
        raise A2PipelineError("Exact 2025 population resolves to multiple season rows")
    season_id = next(iter(season_ids))
    season_row = session.get(Season, season_id)
    if season_row is None or season_row.name != str(policy.season):
        raise A2PipelineError("Canonical season identity is not exact 2025")

    mappings = _mapping_index(session, source=policy.mapping_source)
    canonical_games = {
        row.id: row
        for row in session.scalars(select(Game).where(Game.id.in_(tuple(by_game))))
    }
    if set(canonical_games) != set(by_game):
        raise A2PipelineError("One or more outcomes point to missing canonical games")
    sides_by_game: dict[UUID, list[GameTeam]] = defaultdict(list)
    for side in session.scalars(
        select(GameTeam)
        .where(GameTeam.game_id.in_(tuple(by_game)))
        .order_by(GameTeam.game_id, GameTeam.is_home.desc(), GameTeam.id)
    ):
        sides_by_game[side.game_id].append(side)
    lineage_cache: dict[
        tuple[UUID, UUID], tuple[HistoricalSourceVersion, HistoryBackfillCheckpoint]
    ] = {}
    outcome_source_ids = {row.source_version_id for row in by_game.values()}
    outcome_checkpoint_ids = {row.checkpoint_id for row in by_game.values()}
    sources_by_id = {
        row.id: row
        for row in session.scalars(
            select(HistoricalSourceVersion).where(
                HistoricalSourceVersion.id.in_(tuple(outcome_source_ids))
            )
        )
    }
    checkpoints_by_id = {
        row.id: row
        for row in session.scalars(
            select(HistoryBackfillCheckpoint).where(
                HistoryBackfillCheckpoint.id.in_(tuple(outcome_checkpoint_ids))
            )
        )
    }
    if set(sources_by_id) != outcome_source_ids or set(checkpoints_by_id) != outcome_checkpoint_ids:
        raise A2PipelineError("Canonical outcome lineage is missing source/checkpoint rows")
    for outcome in by_game.values():
        lineage_cache[(outcome.source_version_id, outcome.checkpoint_id)] = (
            sources_by_id[outcome.source_version_id],
            checkpoints_by_id[outcome.checkpoint_id],
        )
    games: dict[UUID, Game] = {}
    target_outcomes: list[GameFinalOutcome] = []
    all_current_outcomes: list[GameFinalOutcome] = []
    target_game_pks: dict[str, str] = {}
    target_refs: dict[str, A2EvidenceReference] = {}
    evidence_refs: dict[str, A2EvidenceReference] = {}

    for outcome in by_game.values():
        if outcome.official_date is None or outcome.official_date.year != policy.season:
            raise A2PipelineError("Outcome lacks exact officialDate season scope")
        tier = _tier(outcome.evidence_classification, outcome.quality_tier)
        _require_sha256("outcome payload", outcome.payload_checksum)
        _require_sha256("outcome identity", outcome.identity_checksum)
        if outcome.outcome_status != "FINAL" or outcome.home_score == outcome.away_score:
            raise A2PipelineError("Canonical final outcome is not an official non-tie")
        if outcome.completion_time_basis not in {
            "AUTHORITATIVE_FINAL_AT",
            "DERIVED_LAST_EVENT_COMPLETION_PROXY",
        }:
            raise A2PipelineError("Outcome lacks a proven retrospective completion basis")
        if outcome.label_available_at < outcome.final_at:
            raise A2PipelineError("Outcome label availability predates final evidence")
        game = canonical_games[outcome.game_id]
        games[game.id] = game
        if (
            game.season_id != season_id
            or game.league_id != season_row.league_id
            or game.source_mode != A2_SOURCE_MODE
            or _as_utc(game.starts_at) != _as_utc(outcome.scheduled_at)
            or game.venue_id != outcome.venue_id
            or game.home_score != outcome.home_score
            or game.away_score != outcome.away_score
            or game.final_at is None
            or _as_utc(game.final_at) != _as_utc(outcome.final_at)
        ):
            raise A2PipelineError("Outcome and canonical game identities disagree")
        sides = tuple(sides_by_game.get(game.id, ()))
        home = [side for side in sides if side.is_home]
        away = [side for side in sides if not side.is_home]
        if (
            len(sides) != 2
            or len(home) != 1
            or len(away) != 1
            or home[0].team_id != outcome.home_team_id
            or away[0].team_id != outcome.away_team_id
            or home[0].score != outcome.home_score
            or away[0].score != outcome.away_score
        ):
            raise A2PipelineError("Canonical game does not have two exact outcome sides")
        game_pk = _mapped(mappings, "game", game.id)
        home_pk = _mapped(mappings, "team", outcome.home_team_id)
        away_pk = _mapped(mappings, "team", outcome.away_team_id)
        if outcome.source_record_id != game_pk:
            raise A2PipelineError("Outcome source record does not match exact game_pk mapping")
        source, checkpoint = _source_checkpoint(
            session,
            outcome,
            official_date=outcome.official_date,
            season=policy.season,
            cache=lineage_cache,
        )
        reference = _reference(
            identity_checksum=outcome.identity_checksum,
            source=source,
            checkpoint=checkpoint,
            quality_tier=outcome.quality_tier,
            evidence_classification=outcome.evidence_classification,
            components=(outcome.identity_checksum,),
        )
        evidence_refs[outcome.identity_checksum] = reference
        all_current_outcomes.append(outcome)
        in_target_window = (
            policy.target_official_date_start is None
            or (
                policy.target_official_date_start
                <= outcome.official_date
                <= policy.target_official_date_end
            )
        )
        selected_target = in_target_window and (
            tier is A2EvidenceTier.A
            if population is A2Population.TIER_A_STRICT
            else tier in {A2EvidenceTier.A, A2EvidenceTier.B}
        )
        if selected_target:
            target_outcomes.append(outcome)
            target_game_pks[str(game.id)] = game_pk
        # Referencing the exact mapped sides here makes missing/ambiguous team
        # mappings a hard loader failure even before team box-score conversion.
        if not home_pk or not away_pk:
            raise A2PipelineError("Mapped canonical sides are empty")

    strict_pks = {
        _mapped(mappings, "game", row.game_id)
        for row in all_current_outcomes
        if row.quality_tier == "A"
    }
    tier_b_pks = {
        _mapped(mappings, "game", row.game_id)
        for row in all_current_outcomes
        if row.quality_tier == "B"
    }
    if strict_pks & policy.rain_shortened_game_pks:
        raise A2PipelineError("Rain-shortened Tier B IDs leaked into strict Tier A")
    if policy.require_exact_rain_set and tier_b_pks != set(policy.rain_shortened_game_pks):
        raise A2PipelineError("Tier B population does not match the governed rain-game set")
    for name, observed, expected_inventory in (
        ("Tier A", len(strict_pks), policy.expected_tier_a_count),
        ("Tier B", len(tier_b_pks), policy.expected_tier_b_count),
    ):
        if expected_inventory is not None and observed != expected_inventory:
            raise A2PipelineError(
                f"Global {name} inventory expected {expected_inventory}, observed {observed}"
            )
    expected = policy.expected_selected_target_count
    if expected is None and policy.target_official_date_start is None:
        if population is A2Population.TIER_A_STRICT:
            expected = policy.expected_tier_a_count
        elif (
            policy.expected_tier_a_count is not None
            and policy.expected_tier_b_count is not None
        ):
            expected = policy.expected_tier_a_count + policy.expected_tier_b_count
    if expected is not None and len(target_outcomes) != expected:
        raise A2PipelineError(
            f"{population.value} target reconciliation expected {expected}, "
            f"observed {len(target_outcomes)}"
        )
    if not target_outcomes:
        raise A2PipelineError("A2 bounded target selector resolved to zero games")

    allowed_tiers = population.allowed_evidence_tiers
    outcome_inputs: list[A2FinalOutcomeInput] = []
    for outcome in all_current_outcomes:
        evidence_tier = A2EvidenceTier(outcome.quality_tier)
        if evidence_tier not in allowed_tiers:
            continue
        outcome_inputs.append(
            A2FinalOutcomeInput(
                game_id=str(outcome.game_id),
                season=policy.season,
                home_team_id=str(outcome.home_team_id),
                away_team_id=str(outcome.away_team_id),
                completed_at=_as_utc(outcome.final_at),
                # Replay eligibility is the proven historical completion time.
                # The 2026 acquisition/label timestamp remains target governance
                # and is enforced only by dataset training_cutoff admission.
                eligible_at=_as_utc(outcome.final_at),
                home_score=outcome.home_score,
                away_score=outcome.away_score,
                innings=outcome.innings_played,
                venue_id=str(outcome.venue_id) if outcome.venue_id else None,
                identity_checksum=outcome.identity_checksum,
                evidence_tier=evidence_tier,
                revision=outcome.revision,
                official_date=outcome.official_date,
            )
        )

    game_ids = tuple(by_game)
    team_rows = tuple(
        session.scalars(
            select(TeamGameHistoryObservation)
            .where(TeamGameHistoryObservation.game_id.in_(game_ids))
            .order_by(
                TeamGameHistoryObservation.game_id,
                TeamGameHistoryObservation.team_id,
                TeamGameHistoryObservation.source_record_id,
                TeamGameHistoryObservation.revision,
            )
        )
    )
    latest_team_rows = _latest_revisions(
        team_rows,
        key=lambda row: (
            row.game_id,
            row.team_id,
            row.source_version_id,
            row.source_mode,
            row.source_record_id,
        ),
        supersedes_attribute="supersedes_observation_id",
    )
    team_by_game: dict[UUID, list[TeamGameHistoryObservation]] = defaultdict(list)
    team_inputs: list[A2TeamGameFactInput] = []
    for row in latest_team_rows:
        outcome = by_game[row.game_id]
        _validate_common_row(row, season_id=season_id)
        if row.final_outcome_id not in outcome_revision_ids_by_game[row.game_id]:
            raise A2PipelineError("Team fact links to another game's outcome revision")
        if (
            row.source_version_id != outcome.source_version_id
            or row.checkpoint_id != outcome.checkpoint_id
        ):
            raise A2PipelineError("Team fact and outcome source/checkpoint lineage disagree")
        if row.team_id not in {outcome.home_team_id, outcome.away_team_id}:
            raise A2PipelineError("Team fact is not an exact game side")
        expected_opponent = (
            outcome.away_team_id
            if row.team_id == outcome.home_team_id
            else outcome.home_team_id
        )
        expected_is_home = row.team_id == outcome.home_team_id
        if (
            row.opponent_team_id != expected_opponent
            or row.is_home != expected_is_home
        ):
            raise A2PipelineError("Team fact opponent mapping is invalid")
        game_pk = _mapped(mappings, "game", row.game_id)
        team_pk = _mapped(mappings, "team", row.team_id)
        if row.source_record_id != f"{game_pk}:{team_pk}":
            raise A2PipelineError("Team fact source identity is not exact")
        source, checkpoint = _source_checkpoint(
            session,
            row,
            official_date=outcome.official_date,
            season=policy.season,
            cache=lineage_cache,
        )
        team_by_game[row.game_id].append(row)
        evidence_refs[row.identity_checksum] = _reference(
            identity_checksum=row.identity_checksum,
            source=source,
            checkpoint=checkpoint,
            quality_tier=row.quality_tier,
            evidence_classification=row.evidence_classification,
            components=(row.identity_checksum,),
        )
        if A2EvidenceTier(row.quality_tier) not in allowed_tiers:
            continue
        values = row.values if isinstance(row.values, dict) else {}
        batting = values.get("batting")
        pitching = values.get("pitching")
        if not isinstance(batting, dict) or not isinstance(pitching, dict):
            raise A2PipelineError("MLB team fact requires literal batting and pitching maps")
        runs_scored = float(
            _literal_nonnegative_number(batting, "runs", required=True)
        )
        runs_allowed = float(
            _literal_nonnegative_number(pitching, "runs", required=True)
        )
        expected_scores = (
            (outcome.home_score, outcome.away_score)
            if row.team_id == outcome.home_team_id
            else (outcome.away_score, outcome.home_score)
        )
        if (runs_scored, runs_allowed) != tuple(map(float, expected_scores)):
            raise A2PipelineError("Literal team runs do not reconcile to the final outcome")
        team_inputs.append(
            A2TeamGameFactInput(
                fact_id=str(row.id),
                game_id=str(row.game_id),
                season=policy.season,
                team_id=str(row.team_id),
                opponent_team_id=str(row.opponent_team_id),
                event_at=_as_utc(row.event_at),
                evidence_end_at=_as_utc(row.evidence_end_at),
                eligible_at=_as_utc(row.eligible_at),
                runs_scored=runs_scored,
                runs_allowed=runs_allowed,
                identity_checksum=row.identity_checksum,
                evidence_tier=A2EvidenceTier(row.quality_tier),
                revision=row.revision,
            )
        )
    for game_id, outcome in by_game.items():
        current = team_by_game.get(game_id, [])
        if len(current) != 2 or {row.team_id for row in current} != {
            outcome.home_team_id,
            outcome.away_team_id,
        }:
            raise A2PipelineError("Every final game must have exactly two latest team facts")

    player_rows = tuple(
        session.scalars(
            select(PlayerGameHistoryObservation)
            .where(PlayerGameHistoryObservation.game_id.in_(game_ids))
            .order_by(
                PlayerGameHistoryObservation.game_id,
                PlayerGameHistoryObservation.team_id,
                PlayerGameHistoryObservation.player_id,
                PlayerGameHistoryObservation.role,
                PlayerGameHistoryObservation.source_record_id,
                PlayerGameHistoryObservation.revision,
            )
        )
    )
    latest_player_rows = _latest_revisions(
        player_rows,
        key=lambda row: (
            row.game_id,
            row.player_id,
            row.team_id,
            row.role,
            row.source_version_id,
            row.source_mode,
            row.source_record_id,
        ),
        supersedes_attribute="supersedes_observation_id",
    )
    grouped_players: dict[
        tuple[UUID, UUID, UUID], list[PlayerGameHistoryObservation]
    ] = defaultdict(list)
    for row in latest_player_rows:
        outcome = by_game[row.game_id]
        _validate_common_row(row, season_id=season_id)
        if row.final_outcome_id not in outcome_revision_ids_by_game[row.game_id]:
            raise A2PipelineError("Player fact links to another game's outcome revision")
        if (
            row.source_version_id != outcome.source_version_id
            or row.checkpoint_id != outcome.checkpoint_id
        ):
            raise A2PipelineError(
                "Player fact and outcome source/checkpoint lineage disagree"
            )
        if row.team_id not in {outcome.home_team_id, outcome.away_team_id}:
            raise A2PipelineError("Player fact team is not an exact game side")
        expected_opponent = (
            outcome.away_team_id
            if row.team_id == outcome.home_team_id
            else outcome.home_team_id
        )
        if row.opponent_team_id != expected_opponent:
            raise A2PipelineError("Player fact opponent mapping is invalid")
        game_pk = _mapped(mappings, "game", row.game_id)
        team_pk = _mapped(mappings, "team", row.team_id)
        player_pk = _mapped(mappings, "player", row.player_id)
        expected_record = f"{game_pk}:{player_pk}:{team_pk}:{row.role}"
        if row.source_record_id != expected_record:
            raise A2PipelineError("Player fact source identity is not exact")
        _source_checkpoint(
            session,
            row,
            official_date=outcome.official_date,
            season=policy.season,
            cache=lineage_cache,
        )
        grouped_players[(row.game_id, row.team_id, row.player_id)].append(row)

    player_inputs: list[A2PlayerGameFactInput] = []
    for semantic_key, rows in sorted(
        grouped_players.items(), key=lambda item: tuple(str(value) for value in item[0])
    ):
        relevant = [row for row in rows if row.role in {"BATTER", "PITCHER"}]
        if not relevant:
            continue
        tiers = {row.quality_tier for row in relevant}
        sources = {row.source_version_id for row in relevant}
        checkpoints = {row.checkpoint_id for row in relevant}
        times = {
            (_as_utc(row.event_at), _as_utc(row.evidence_end_at), _as_utc(row.eligible_at))
            for row in relevant
        }
        if len(tiers) != 1 or len(sources) != 1 or len(checkpoints) != 1 or len(times) != 1:
            raise A2PipelineError("Merged batter/pitcher fact lineage does not reconcile")
        evidence_tier = A2EvidenceTier(next(iter(tiers)))
        if evidence_tier not in allowed_tiers:
            continue
        batter_rows = [row for row in relevant if row.role == "BATTER"]
        pitcher_rows = [row for row in relevant if row.role == "PITCHER"]
        if len(batter_rows) > 1 or len(pitcher_rows) > 1:
            raise A2PipelineError("Duplicate latest player role facts remain after resolution")
        batter_values = (batter_rows[0].values or {}) if batter_rows else {}
        pitcher_values = (pitcher_rows[0].values or {}) if pitcher_rows else {}
        batting = batter_values.get("batting")
        pitching = pitcher_values.get("pitching")
        if batting is not None and not isinstance(batting, dict):
            raise A2PipelineError("Literal MLB batting payload is not an object")
        if pitching is not None and not isinstance(pitching, dict):
            raise A2PipelineError("Literal MLB pitching payload is not an object")
        batting = batting or {}
        pitching = pitching or {}
        actual_starter = _literal_bool(pitcher_values, "actual_starting_pitcher")
        appeared = _literal_bool(pitcher_values, "appeared_as_pitcher")
        if pitcher_rows and not appeared:
            raise A2PipelineError("Pitcher role lacks exact pitcher participation")
        if actual_starter and (not appeared or pitcher_rows[0].started is not True):
            raise A2PipelineError("Actual starter lacks exact pitcher participation")
        role = (
            A2PlayerRole.STARTER
            if actual_starter
            else A2PlayerRole.RELIEVER
            if pitcher_rows
            else A2PlayerRole.BATTER
        )
        batting_order = batter_values.get("batting_order")
        if batting_order is not None:
            if isinstance(batting_order, bool) or not isinstance(batting_order, int):
                raise A2PipelineError("Literal MLB batting order is not an integer")
            if not batter_rows or batter_rows[0].started is not True:
                raise A2PipelineError("Historical batting order lacks exact started identity")
        component_ids = tuple(sorted(row.identity_checksum for row in relevant))
        merged_identity = _checksum(
            {"kind": "A2_PLAYER_GAME_FACT", "component_identities": component_ids}
        )
        first = relevant[0]
        source, checkpoint = lineage_cache[(first.source_version_id, first.checkpoint_id)]
        evidence_refs[merged_identity] = _reference(
            identity_checksum=merged_identity,
            source=source,
            checkpoint=checkpoint,
            quality_tier=first.quality_tier,
            evidence_classification=first.evidence_classification,
            components=component_ids,
        )
        player_inputs.append(
            A2PlayerGameFactInput(
                fact_id=f"a2:{semantic_key[0]}:{semantic_key[1]}:{semantic_key[2]}",
                game_id=str(first.game_id),
                season=policy.season,
                player_id=str(first.player_id),
                team_id=str(first.team_id),
                opponent_team_id=str(first.opponent_team_id),
                role=role,
                event_at=_as_utc(first.event_at),
                evidence_end_at=_as_utc(first.evidence_end_at),
                eligible_at=_as_utc(first.eligible_at),
                identity_checksum=merged_identity,
                batting_plate_appearances=_literal_nonnegative_int(
                    batting, "plateAppearances"
                ),
                batting_at_bats=_literal_nonnegative_int(batting, "atBats"),
                batting_hits=_literal_nonnegative_int(batting, "hits"),
                batting_walks=_literal_nonnegative_int(batting, "baseOnBalls"),
                batting_strikeouts=_literal_nonnegative_int(batting, "strikeOuts"),
                batting_total_bases=_literal_nonnegative_int(batting, "totalBases"),
                batting_home_runs=_literal_nonnegative_int(batting, "homeRuns"),
                pitching_batters_faced=_literal_nonnegative_int(
                    pitching, "battersFaced"
                ),
                pitching_hits_allowed=_literal_nonnegative_int(pitching, "hits"),
                pitching_walks_allowed=_literal_nonnegative_int(
                    pitching, "baseOnBalls"
                ),
                pitching_strikeouts=_literal_nonnegative_int(
                    pitching, "strikeOuts"
                ),
                pitching_home_runs_allowed=_literal_nonnegative_int(
                    pitching, "homeRuns"
                ),
                pitches=_literal_nonnegative_number(pitching, "numberOfPitches"),
                outs_recorded=_outs_recorded(pitching),
                earned_runs=_literal_nonnegative_number(pitching, "earnedRuns"),
                actual_starter=actual_starter,
                batting_order=batting_order,
                evidence_tier=evidence_tier,
                revision=max(row.revision for row in relevant),
                official_date=by_game[first.game_id].official_date,
            )
        )

    venue_inputs: list[A2VenueInput] = []
    seen_venues: set[UUID] = set()
    venue_ids = {game.venue_id for game in games.values() if game.venue_id is not None}
    canonical_venues = {
        row.id: row
        for row in session.scalars(select(Venue).where(Venue.id.in_(tuple(venue_ids))))
    }
    if set(canonical_venues) != venue_ids:
        raise A2PipelineError("One or more canonical game venues are missing")
    for game in games.values():
        if game.venue_id is None or game.venue_id in seen_venues:
            continue
        venue = canonical_venues[game.venue_id]
        venue_pk = _mapped(mappings, "venue", venue.id)
        venue_identity = _checksum(
            {
                "kind": "A2_VENUE_IDENTITY",
                "venue_id": str(venue.id),
                "external_id": venue_pk,
                "latitude": venue.latitude,
                "longitude": venue.longitude,
            }
        )
        venue_inputs.append(
            A2VenueInput(
                venue_id=str(venue.id),
                latitude=venue.latitude,
                longitude=venue.longitude,
                identity_checksum=venue_identity,
            )
        )
        seen_venues.add(venue.id)

    target_inputs: list[A2TargetGameInput] = []
    target_outcome_ids: dict[str, UUID] = {}
    target_checkpoint_ids: dict[str, UUID] = {}
    for outcome in sorted(
        target_outcomes, key=lambda item: (_as_utc(item.scheduled_at), str(item.game_id))
    ):
        game = games[outcome.game_id]
        game_pk = _mapped(mappings, "game", game.id)
        home_pk = _mapped(mappings, "team", outcome.home_team_id)
        away_pk = _mapped(mappings, "team", outcome.away_team_id)
        target_identity = _checksum(
            {
                "kind": "A2_TARGET_GAME_IDENTITY",
                "game_id": str(game.id),
                "game_pk": game_pk,
                "season_id": str(season_id),
                "official_date": outcome.official_date.isoformat(),
                "scheduled_first_pitch": _iso(game.starts_at),
                "home_team_id": str(outcome.home_team_id),
                "home_team_external_id": home_pk,
                "away_team_id": str(outcome.away_team_id),
                "away_team_external_id": away_pk,
                "venue_id": str(game.venue_id) if game.venue_id else None,
            }
        )
        target_input = A2TargetGameInput(
            game_id=str(game.id),
            season=policy.season,
            home_team_id=str(outcome.home_team_id),
            away_team_id=str(outcome.away_team_id),
            scheduled_first_pitch=_as_utc(game.starts_at),
            venue_id=str(game.venue_id) if game.venue_id else None,
            identity_checksum=target_identity,
            official_date=outcome.official_date,
        )
        target_inputs.append(target_input)
        source, checkpoint = lineage_cache[
            (outcome.source_version_id, outcome.checkpoint_id)
        ]
        target_refs[str(game.id)] = _reference(
            identity_checksum=target_identity,
            source=source,
            checkpoint=checkpoint,
            quality_tier=outcome.quality_tier,
            evidence_classification="TARGET_SCHEDULE_IDENTITY",
            components=(target_identity,),
        )
        target_outcome_ids[str(game.id)] = outcome.id
        target_checkpoint_ids[str(game.id)] = outcome.checkpoint_id

    structural_refs: dict[str, A2StructuralReference] = {
        config.league_prior_identity_checksum: A2StructuralReference(
            identity_checksum=config.league_prior_identity_checksum,
            kind="VERSIONED_LEAGUE_PRIOR_POLICY",
            definition={
                "league_prior_version": config.league_prior_version,
                "policy_identity_checksum": config.policy_identity_checksum,
            },
        )
    }
    for venue in venue_inputs:
        structural_refs[venue.identity_checksum] = A2StructuralReference(
            identity_checksum=venue.identity_checksum,
            kind="EXACT_CANONICAL_VENUE_IDENTITY",
            definition={
                "venue_id": venue.venue_id,
                "latitude": venue.latitude,
                "longitude": venue.longitude,
            },
        )
    for target in target_inputs:
        structural_refs[target.identity_checksum] = A2StructuralReference(
            identity_checksum=target.identity_checksum,
            kind="EXACT_TARGET_GAME_SCHEDULE_IDENTITY",
            definition={
                "game_id": target.game_id,
                "season": target.season,
                "official_date": (
                    target.official_date.isoformat()
                    if target.official_date is not None
                    else None
                ),
                "scheduled_first_pitch": _iso(target.scheduled_first_pitch),
                "home_team_id": target.home_team_id,
                "away_team_id": target.away_team_id,
                "venue_id": target.venue_id,
            },
        )

    input_identity = {
        "population": population.value,
        "season": policy.season,
        "targets": [item.identity_checksum for item in target_inputs],
        "outcomes": [item.identity_checksum for item in outcome_inputs],
        "teams": [item.identity_checksum for item in team_inputs],
        "players": [item.identity_checksum for item in player_inputs],
        "venues": [item.identity_checksum for item in venue_inputs],
        "structural": sorted(structural_refs),
    }
    return A2OrmInputBundle(
        population=population,
        season_id=season_id,
        target_games=tuple(target_inputs),
        final_outcomes=tuple(outcome_inputs),
        team_facts=tuple(team_inputs),
        player_facts=tuple(player_inputs),
        venues=tuple(sorted(venue_inputs, key=lambda item: item.venue_id)),
        target_outcome_ids=MappingProxyType(target_outcome_ids),
        target_checkpoint_ids=MappingProxyType(target_checkpoint_ids),
        target_game_pks=MappingProxyType(target_game_pks),
        evidence_references=MappingProxyType(evidence_refs),
        structural_references=MappingProxyType(structural_refs),
        target_references=MappingProxyType(target_refs),
        input_checksum=_checksum(input_identity),
    )


def _feature_definition(
    population: A2Population, config: A2ReconstructionConfig
) -> dict[str, object]:
    identity = feature_identity_for(population, config=config)
    return {
        "stage": "GATE_3A_A2",
        "feature_identity": identity.to_dict(),
        "schema": A2_SCHEMA.definition(),
        "reconstruction_config": config.definition(),
        "raw_value_names": list(A2_SCHEMA.value_names),
        "ordered_model_output_names": list(A2_SCHEMA.output_names),
        "missing_value_policy": "SCHEMA_NEUTRAL_VALUE_PLUS_EXPLICIT_BINARY_INDICATOR",
        "model_training_invoked": False,
    }


def ensure_a2_feature_version(
    session: Session,
    *,
    population: A2Population,
    config: A2ReconstructionConfig = A2ReconstructionConfig(),
) -> FeatureVersion:
    """Create or exactly revalidate the deterministic A2 FeatureVersion."""

    population = A2Population(population)
    identity = feature_identity_for(population, config=config)
    definition = _feature_definition(population, config)
    existing = session.scalar(
        select(FeatureVersion).where(
            FeatureVersion.name == A2_FEATURE_VERSION_NAME,
            FeatureVersion.version == identity.feature_version,
        )
    )
    if existing is not None:
        if existing.schema_hash != A2_SCHEMA_HASH or existing.definition != definition:
            raise A2PipelineError("Existing A2 FeatureVersion identity has drifted")
        return existing
    row = FeatureVersion(
        name=A2_FEATURE_VERSION_NAME,
        version=identity.feature_version,
        schema_hash=A2_SCHEMA_HASH,
        definition=definition,
    )
    session.add(row)
    session.flush()
    return row


def _source_bundle(
    row: A2FeatureRowDraft,
    inputs: A2OrmInputBundle,
) -> list[dict[str, object]]:
    contributing = row.transient_contributing_identity_checksums
    resolved = set(inputs.evidence_references) | set(inputs.structural_references)
    unresolved = sorted(contributing - resolved)
    if unresolved:
        raise A2PipelineError(
            "A2 compact lineage contains unresolved contributing identities: "
            + ", ".join(unresolved)
        )
    references = [
        inputs.evidence_references[checksum]
        for checksum in contributing
        if checksum in inputs.evidence_references
    ]
    target_reference = inputs.target_references[row.game_id]
    references.append(target_reference)
    grouped: dict[tuple[UUID, UUID], list[A2EvidenceReference]] = defaultdict(list)
    for reference in references:
        grouped[(reference.source_version_id, reference.checkpoint_id)].append(reference)
    bundle: list[dict[str, object]] = []
    for key, rows in sorted(grouped.items(), key=lambda item: tuple(map(str, item[0]))):
        components = sorted(
            {
                checksum
                for reference in rows
                for checksum in reference.component_identity_checksums
            }
        )
        first = rows[0]
        bundle.append(
            {
                "type": "CANONICAL_SOURCE_CHECKPOINT_BUNDLE",
                "source_version_id": str(key[0]),
                "source_identity_checksum": first.source_identity_checksum,
                "source_key": first.source_key,
                "dataset_version": first.dataset_version,
                "checkpoint_id": str(key[1]),
                "checkpoint_identity_checksum": first.checkpoint_identity_checksum,
                "canonical_component_count": len(components),
                "canonical_component_set_checksum": _checksum(
                    {"identity_checksums": components}
                ),
                "target_label_excluded_from_feature_state": True,
            }
        )
    for checksum in sorted(contributing & set(inputs.structural_references)):
        structural = inputs.structural_references[checksum]
        bundle.append(
            {
                "type": "STRUCTURAL_REFERENCE",
                "structural_identity_checksum": structural.identity_checksum,
                "structural_kind": structural.kind,
                "definition_checksum": _checksum(dict(structural.definition)),
                "definition": dict(structural.definition),
                "target_label_excluded_from_feature_state": True,
            }
        )
    bundle.sort(key=_canonical_json)
    return bundle


def _family_counts(row: A2FeatureRowDraft) -> dict[str, object]:
    values: dict[str, object] = {
        item.family: item.canonical_reference_count
        for item in row.compact_lineage.families
    }
    values.update(
        {
            "team_history": row.metadata.team_history_count,
            "player_history": row.metadata.player_history_count,
            "outcome_history": row.metadata.outcome_history_count,
            "target_context": row.metadata.target_context_count,
        }
    )
    return values


def _family_checksums(row: A2FeatureRowDraft) -> dict[str, str]:
    return {
        item.family: item.canonical_set_checksum
        for item in row.compact_lineage.families
    }


def _attestation_identity_payload(
    *,
    row: A2FeatureRowDraft,
    feature_version_id: UUID,
    checkpoint_id: UUID,
    source_bundle_checksum: str,
) -> dict[str, object]:
    return {
        "game_id": row.game_id,
        "feature_version_id": str(feature_version_id),
        "checkpoint_id": str(checkpoint_id),
        "cutoff_at": _iso(row.cutoff),
        "state_version": A2_LINEAGE_VERSION,
        "population": row.population.value,
        "canonical_set_checksum": row.compact_lineage.canonical_set_checksum,
        "feature_values_checksum": row.feature_values_checksum,
        "source_bundle_checksum": source_bundle_checksum,
        "outcome_history_count": row.metadata.outcome_history_count,
        "team_history_count": row.metadata.team_history_count,
        "player_history_count": row.metadata.player_history_count,
        "target_context_count": row.metadata.target_context_count,
        "target_excluded": row.metadata.target_excluded,
        "temporal_valid": row.metadata.temporal_valid,
    }


def _verify_attestation(
    attestation: HistoricalEvidenceStateAttestation,
    *,
    expected_identity: str,
    row: A2FeatureRowDraft,
    source_bundle_checksum: str,
) -> None:
    if (
        attestation.identity_checksum != expected_identity
        or str(attestation.game_id) != row.game_id
        or attestation.population != row.population.value
        or _as_utc(attestation.cutoff_at) != row.cutoff
        or attestation.canonical_set_checksum
        != row.compact_lineage.canonical_set_checksum
        or attestation.feature_values_checksum != row.feature_values_checksum
        or attestation.source_bundle_checksum != source_bundle_checksum
        or attestation.outcome_history_count != row.metadata.outcome_history_count
        or attestation.team_history_count != row.metadata.team_history_count
        or attestation.player_history_count != row.metadata.player_history_count
        or not attestation.target_excluded
        or not attestation.temporal_valid
    ):
        raise A2PipelineError("Existing A2 attestation failed immutable revalidation")


def _snapshot_metadata(
    row: A2FeatureRowDraft, attestation_identity: str
) -> dict[str, object]:
    return {
        "stage": "GATE_3A_A2",
        "builder_version": A2_BUILDER_VERSION,
        "lineage_version": A2_LINEAGE_VERSION,
        "population": row.population.value,
        "feature_identity": row.feature_identity.to_dict(),
        "attestation_identity_checksum": attestation_identity,
        "canonical_set_checksum": row.compact_lineage.canonical_set_checksum,
        "feature_values_checksum": row.feature_values_checksum,
        "missing_families": list(row.missing_families),
        "provisional_families": list(row.provisional_families),
        "timing_risk_families": list(row.timing_risk_families),
        "row_checksum": row.row_checksum,
    }


def _provisional_feature_names(row: A2FeatureRowDraft) -> list[str]:
    families = set(row.provisional_families)
    return [spec.name for spec in A2_SCHEMA.features if spec.family in families]


def _materialization_identity_payload(
    *,
    row: A2FeatureRowDraft,
    feature_version_id: UUID,
    outcome: GameFinalOutcome,
    checkpoint_id: UUID,
    attestation_identity: str,
    snapshot_checksum: str,
    source_bundle_checksum: str,
    evidence_manifest_checksum: str,
    code_revision: str,
) -> dict[str, object]:
    return {
        "game_id": row.game_id,
        "feature_version_id": str(feature_version_id),
        "outcome_identity_checksum": outcome.identity_checksum,
        "checkpoint_id": str(checkpoint_id),
        "cutoff_at": _iso(row.cutoff),
        "population": row.population.value,
        "attestation_identity_checksum": attestation_identity,
        "snapshot_checksum": snapshot_checksum,
        "source_bundle_checksum": source_bundle_checksum,
        "evidence_manifest_checksum": evidence_manifest_checksum,
        "materializer_version": A2_MATERIALIZER_VERSION,
        "code_revision": code_revision,
    }


def _persist_a2_row(
    session: Session,
    *,
    row: A2FeatureRowDraft,
    inputs: A2OrmInputBundle,
    feature_version: FeatureVersion,
    generated_at: datetime,
    code_revision: str,
) -> tuple[bool, bool, bool]:
    if row.population is not inputs.population:
        raise A2PipelineError("Engine row population differs from ORM input population")
    if not row.metadata.target_excluded or not row.metadata.temporal_valid:
        raise A2PipelineError("A2 row did not pass target-exclusion/temporal gates")
    if row.metadata.eligible_max_at is not None and row.metadata.eligible_max_at > row.cutoff:
        raise A2PipelineError("A2 row includes evidence after its cutoff")
    if generated_at < row.cutoff:
        raise A2PipelineError("A2 generation time precedes the historical cutoff")
    game_id = UUID(row.game_id)
    game = session.get(Game, game_id)
    outcome = session.get(GameFinalOutcome, inputs.target_outcome_ids[row.game_id])
    checkpoint = session.get(
        HistoryBackfillCheckpoint, inputs.target_checkpoint_ids[row.game_id]
    )
    if game is None or outcome is None or checkpoint is None:
        raise A2PipelineError("A2 target persistence lineage disappeared")
    if generated_at < _as_utc(checkpoint.completed_at):
        raise A2PipelineError("A2 generation time predates canonical checkpoint completion")
    if outcome.game_id != game.id or outcome.season_id != inputs.season_id:
        raise A2PipelineError("A2 target outcome identity changed before persistence")

    source_bundle = _source_bundle(row, inputs)
    source_bundle_checksum = _checksum(source_bundle)
    attestation_identity = _checksum(
        _attestation_identity_payload(
            row=row,
            feature_version_id=feature_version.id,
            checkpoint_id=checkpoint.id,
            source_bundle_checksum=source_bundle_checksum,
        )
    )
    attestation = session.scalar(
        select(HistoricalEvidenceStateAttestation).where(
            HistoricalEvidenceStateAttestation.identity_checksum == attestation_identity
        )
    )
    if attestation is None:
        attestation = session.scalar(
            select(HistoricalEvidenceStateAttestation).where(
                HistoricalEvidenceStateAttestation.game_id == game.id,
                HistoricalEvidenceStateAttestation.cutoff_at == row.cutoff,
                HistoricalEvidenceStateAttestation.feature_version_id
                == feature_version.id,
                HistoricalEvidenceStateAttestation.state_version == A2_LINEAGE_VERSION,
                HistoricalEvidenceStateAttestation.population == row.population.value,
                HistoricalEvidenceStateAttestation.source_mode == A2_SOURCE_MODE,
                HistoricalEvidenceStateAttestation.canonical_set_checksum
                == row.compact_lineage.canonical_set_checksum,
            )
        )
    reused_attestation = attestation is not None
    if attestation is not None:
        _verify_attestation(
            attestation,
            expected_identity=attestation_identity,
            row=row,
            source_bundle_checksum=source_bundle_checksum,
        )
    else:
        family_counts = _family_counts(row)
        attestation = HistoricalEvidenceStateAttestation(
            game_id=game.id,
            season_id=inputs.season_id,
            feature_version_id=feature_version.id,
            checkpoint_id=checkpoint.id,
            cutoff_at=row.cutoff,
            generated_at=generated_at,
            state_version=A2_LINEAGE_VERSION,
            population=row.population.value,
            outcome_history_count=row.metadata.outcome_history_count,
            team_history_count=row.metadata.team_history_count,
            player_history_count=row.metadata.player_history_count,
            statcast_event_count=0,
            evidence_start_at=row.metadata.evidence_start_at,
            evidence_end_at=row.metadata.evidence_end_at,
            eligible_max_at=row.metadata.eligible_max_at,
            family_counts=family_counts,
            family_checksums=_family_checksums(row),
            source_bundle=source_bundle,
            source_bundle_checksum=source_bundle_checksum,
            canonical_set_checksum=row.compact_lineage.canonical_set_checksum,
            feature_values_checksum=row.feature_values_checksum,
            target_excluded=True,
            temporal_valid=True,
            source_mode=A2_SOURCE_MODE,
            temporal_semantics=A2_TEMPORAL_SEMANTICS,
            identity_checksum=attestation_identity,
        )
        session.add(attestation)
        session.flush()

    feature_values = row.feature_values
    expected_feature_checksum = _checksum({"feature_items": row.feature_items})
    if expected_feature_checksum != row.feature_values_checksum:
        raise A2PipelineError("Pure-engine feature checksum failed revalidation")
    source_evidence = [
        {
            "type": "A2_COMPACT_EVIDENCE_STATE_ATTESTATION",
            "lineage_version": A2_LINEAGE_VERSION,
            "population": row.population.value,
            "attestation_identity_checksum": attestation_identity,
            "canonical_set_checksum": row.compact_lineage.canonical_set_checksum,
            "source_bundle_checksum": source_bundle_checksum,
            "outcome_history_count": row.metadata.outcome_history_count,
            "team_history_count": row.metadata.team_history_count,
            "player_history_count": row.metadata.player_history_count,
            "target_excluded": True,
            "temporal_valid": True,
        }
    ]
    completeness = (
        (len(A2_SCHEMA.value_names) - len(row.missing_feature_names))
        / len(A2_SCHEMA.value_names)
    )
    snapshot = session.scalar(
        select(GameFeatureSnapshot).where(GameFeatureSnapshot.checksum == row.row_checksum)
    )
    reused_snapshot = snapshot is not None
    if snapshot is not None:
        if (
            snapshot.game_id != game.id
            or snapshot.feature_version_id != feature_version.id
            or _as_utc(snapshot.data_as_of) != row.cutoff
            or snapshot.feature_schema_version != A2_SCHEMA_VERSION
            or snapshot.feature_values != feature_values
            or snapshot.source_evidence != source_evidence
            or snapshot.market_snapshot_ids
            or snapshot.statcast_observation_ids
        ):
            raise A2PipelineError("Existing A2 feature snapshot failed immutable revalidation")
    else:
        snapshot = GameFeatureSnapshot(
            game_id=game.id,
            generated_at=generated_at,
            data_as_of=row.cutoff,
            feature_schema_version=A2_SCHEMA_VERSION,
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
            source_evidence=source_evidence,
            feature_values=feature_values,
            feature_completeness=completeness,
            missing_features=list(row.missing_feature_names),
            stale_features=[],
            provisional_features=_provisional_feature_names(row),
            bayesian_metadata=_snapshot_metadata(row, attestation_identity),
            checksum=row.row_checksum,
            source_mode=A2_SOURCE_MODE,
            frozen_at=generated_at,
        )
        session.add(snapshot)
        session.flush()

    evidence_manifest = [
        {
            "attestation_identity_checksum": attestation_identity,
            "canonical_set_checksum": row.compact_lineage.canonical_set_checksum,
            "feature_values_checksum": row.feature_values_checksum,
            "lineage_version": A2_LINEAGE_VERSION,
            "target_excluded": True,
            "temporal_valid": True,
        }
    ]
    evidence_manifest_checksum = _checksum(evidence_manifest)
    materialization_identity = _checksum(
        _materialization_identity_payload(
            row=row,
            feature_version_id=feature_version.id,
            outcome=outcome,
            checkpoint_id=checkpoint.id,
            attestation_identity=attestation_identity,
            snapshot_checksum=snapshot.checksum,
            source_bundle_checksum=source_bundle_checksum,
            evidence_manifest_checksum=evidence_manifest_checksum,
            code_revision=code_revision,
        )
    )
    materialization = session.scalar(
        select(HistoricalFeatureMaterialization).where(
            HistoricalFeatureMaterialization.identity_checksum
            == materialization_identity
        )
    )
    if materialization is None:
        materialization = session.scalar(
            select(HistoricalFeatureMaterialization).where(
                HistoricalFeatureMaterialization.game_id == game.id,
                HistoricalFeatureMaterialization.cutoff_at == row.cutoff,
                HistoricalFeatureMaterialization.feature_version_id
                == feature_version.id,
                HistoricalFeatureMaterialization.cutoff_policy_version
                == A2_CUTOFF_POLICY_VERSION,
                HistoricalFeatureMaterialization.source_mode == A2_SOURCE_MODE,
                HistoricalFeatureMaterialization.materializer_version
                == A2_MATERIALIZER_VERSION,
                HistoricalFeatureMaterialization.evidence_manifest_checksum
                == evidence_manifest_checksum,
            )
        )
    reused_materialization = materialization is not None
    if materialization is not None:
        expected_training_eligible = (
            row.population is A2Population.TIER_A_STRICT
        )
        if (
            materialization.identity_checksum != materialization_identity
            or materialization.game_feature_snapshot_id != snapshot.id
            or materialization.game_final_outcome_id != outcome.id
            or materialization.evidence_manifest_checksum
            != evidence_manifest_checksum
            or materialization.source_bundle_checksum != source_bundle_checksum
            or materialization.training_eligible != expected_training_eligible
        ):
            raise A2PipelineError(
                "A2 materialization semantic collision indicates correction/tamper"
            )
    else:
        quality_tier = (
            "A" if row.population is A2Population.TIER_A_STRICT else "B"
        )
        training_eligible = row.population is A2Population.TIER_A_STRICT
        quality_score = completeness * 100.0
        if quality_score <= 0:
            raise A2PipelineError("A2 row has no measurable feature completeness")
        materialization = HistoricalFeatureMaterialization(
            game_id=game.id,
            game_feature_snapshot_id=snapshot.id,
            feature_version_id=feature_version.id,
            game_final_outcome_id=outcome.id,
            checkpoint_id=checkpoint.id,
            scheduled_at=row.scheduled_first_pitch,
            cutoff_at=row.cutoff,
            generated_at=generated_at,
            cutoff_policy_version=A2_CUTOFF_POLICY_VERSION,
            cutoff_policy={
                "policy_version": A2_CUTOFF_POLICY_VERSION,
                "hours_before_first_pitch": 6,
                "event_boundary": "STRICTLY_BEFORE_CUTOFF",
                "eligibility_boundary": "AT_OR_BEFORE_CUTOFF",
                "population": row.population.value,
            },
            source_bundle=source_bundle,
            source_bundle_checksum=source_bundle_checksum,
            evidence_manifest=evidence_manifest,
            evidence_manifest_checksum=evidence_manifest_checksum,
            quality_tier=quality_tier,
            quality_score=quality_score,
            quality_components={
                "policy": "A2_MEASURED_RAW_VALUE_COMPLETENESS_V1",
                "present_values": len(A2_SCHEMA.value_names)
                - len(row.missing_feature_names),
                "total_values": len(A2_SCHEMA.value_names),
                "team_history_count": row.metadata.team_history_count,
                "player_history_count": row.metadata.player_history_count,
                "outcome_history_count": row.metadata.outcome_history_count,
                "target_context_count": row.metadata.target_context_count,
            },
            materializer_version=A2_MATERIALIZER_VERSION,
            code_revision=code_revision,
            eligibility_status="ELIGIBLE",
            eligibility_reasons=(
                [] if training_eligible else ["RESEARCH_ONLY_TIER_B_ENHANCED"]
            ),
            training_eligible=training_eligible,
            source_mode=A2_SOURCE_MODE,
            temporal_semantics=A2_TEMPORAL_SEMANTICS,
            identity_checksum=materialization_identity,
        )
        session.add(materialization)
        session.flush()
    return reused_attestation, reused_snapshot, reused_materialization


def materialize_a2_population(
    session: Session,
    *,
    inputs: A2OrmInputBundle | None = None,
    population: A2Population = A2Population.TIER_A_STRICT,
    policy: A2PipelinePolicy = A2PipelinePolicy(),
    config: A2ReconstructionConfig = A2ReconstructionConfig(),
    generated_at: datetime | None = None,
    code_revision: str,
    batch_size: int = 100,
    commit_batches: bool = True,
) -> A2MaterializationRunResult:
    """Stream pure A2 drafts into idempotent committed ORM batches."""

    if not code_revision.strip():
        raise ValueError("code_revision is required")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    population = A2Population(population)
    bundle = inputs or load_a2_orm_inputs(
        session, population=population, policy=policy, config=config
    )
    if bundle.population is not population:
        raise A2PipelineError("Supplied A2 input bundle has another population")
    computed = _as_utc(generated_at or datetime.now(UTC))
    feature_version = ensure_a2_feature_version(
        session, population=population, config=config
    )
    session.flush()

    counts = Counter()
    committed_batches = 0
    processed = 0
    reconstructor = A2ChronologicalReconstructor(config)
    try:
        rows = reconstructor.iter_rows(
            target_games=bundle.target_games,
            final_outcomes=bundle.final_outcomes,
            team_facts=bundle.team_facts,
            player_facts=bundle.player_facts,
            venues=bundle.venues,
            population=population,
        )
        for row in rows:
            reused = _persist_a2_row(
                session,
                row=row,
                inputs=bundle,
                feature_version=feature_version,
                generated_at=computed,
                code_revision=code_revision,
            )
            for name, was_reused in zip(
                ("attestations", "snapshots", "materializations"), reused
            ):
                counts[f"reused_{name}" if was_reused else f"inserted_{name}"] += 1
            processed += 1
            if processed % batch_size == 0:
                if commit_batches:
                    session.commit()
                    committed_batches += 1
                else:
                    session.flush()
        if processed % batch_size:
            if commit_batches:
                session.commit()
                committed_batches += 1
            else:
                session.flush()
    except Exception:
        session.rollback()
        raise
    if processed != len(bundle.target_games):
        raise A2PipelineError("A2 engine output count does not reconcile to targets")
    return A2MaterializationRunResult(
        population=population,
        feature_version_id=feature_version.id,
        target_count=processed,
        inserted_attestations=counts["inserted_attestations"],
        reused_attestations=counts["reused_attestations"],
        inserted_snapshots=counts["inserted_snapshots"],
        reused_snapshots=counts["reused_snapshots"],
        inserted_materializations=counts["inserted_materializations"],
        reused_materializations=counts["reused_materializations"],
        committed_batches=committed_batches,
        input_checksum=bundle.input_checksum,
    )


def _model_ready_features(snapshot: GameFeatureSnapshot) -> dict[str, float]:
    if set(snapshot.feature_values) != set(A2_SCHEMA.value_names):
        raise A2PipelineError("A2 snapshot raw feature keys do not match the frozen schema")
    output: dict[str, float] = {}
    for spec in A2_SCHEMA.features:
        value = snapshot.feature_values[spec.name]
        missing = value is None
        converted = float(spec.neutral_value if missing else value)
        if not isfinite(converted):
            raise A2PipelineError("A2 model-ready feature is not finite")
        output[spec.name] = converted
        output[spec.missing_indicator_name] = 1.0 if missing else 0.0
    if tuple(output) != A2_SCHEMA.output_names:
        raise A2PipelineError("A2 model-ready feature order drifted")
    return output


def _raw_feature_coverage(rows: Sequence[TrainingRow], names: Sequence[str]) -> float:
    if not rows or not names:
        return 0.0
    present = sum(
        row.features.get(f"{name}__missing") == 0.0
        for row in rows
        for name in names
    )
    return present / (len(rows) * len(names))


def _raw_a2_family_coverage(rows: Sequence[TrainingRow]) -> dict[str, float]:
    return {
        family: _raw_feature_coverage(
            rows,
            [spec.name for spec in A2_SCHEMA.features if spec.family == family],
        )
        for family in A2_SCHEMA.families
    }


def _required_family_coverage(rows: Sequence[TrainingRow]) -> dict[str, float]:
    by_family = {
        family: [spec.name for spec in A2_SCHEMA.features if spec.family == family]
        for family in A2_SCHEMA.families
    }
    starter_names = [
        spec.name
        for spec in A2_SCHEMA.features
        if "_starter_" in spec.name
    ]
    lineup_names = [
        spec.name
        for spec in A2_SCHEMA.features
        if "_lineup_" in spec.name
    ]
    coverage = {
        "team_strength": _raw_feature_coverage(
            rows,
            by_family.get("team", [])
            + by_family.get("talent", [])
            + by_family.get("rating", []),
        ),
        "starting_pitcher": _raw_feature_coverage(rows, starter_names),
        "lineup": _raw_feature_coverage(rows, lineup_names),
        "bullpen": _raw_feature_coverage(rows, by_family.get("bullpen", [])),
        "statcast": 0.0,
        "recent_form": _raw_feature_coverage(rows, by_family.get("recent", [])),
        "park_weather": 0.0,
        "rest_travel": _raw_feature_coverage(rows, by_family.get("schedule", [])),
        "defense_baserunning": 0.0,
        "opponent_adjustment": 0.0,
    }
    if set(coverage) != set(REQUIRED_FEATURE_FAMILIES):
        raise A2PipelineError("Required Gate 3 feature-family mapping drifted")
    return {family: coverage[family] for family in REQUIRED_FEATURE_FAMILIES}


def _dataset_manifest_data(
    rows: Sequence[TrainingRow],
    *,
    exclusions: Sequence[dict[str, str]],
    population: A2Population,
    feature_version: FeatureVersion,
    training_cutoff: datetime,
) -> TrainingDatasetManifestData:
    row_dicts = [asdict(row) for row in rows]
    rows_checksum = sha256(
        b"\n".join(_dataset_bytes(row) for row in row_dicts)
    ).hexdigest()
    dates = [
        datetime.fromisoformat(
            row.scheduled_first_pitch.replace("Z", "+00:00")
        ).date()
        for row in rows
    ]
    reasons = Counter(item["reason"] for item in exclusions)
    tiers = sorted({str(row.historical_quality_tier) for row in rows})
    scores = [float(row.historical_quality_score or 0.0) for row in rows]
    observed_raw_feature_names = [
        spec.name
        for spec in A2_SCHEMA.features
        if any(spec.name not in set(row.missing_features) for row in rows)
    ]
    leakage_checks = {
        "one_row_per_game": len({row.game_id for row in rows}) == len(rows),
        "pregame_cutoff": all(row.data_as_of < row.scheduled_first_pitch for row in rows),
        "live_source_only": all(row.source_mode == A2_SOURCE_MODE for row in rows),
        "source_evidence_cutoff": all(
            bool(row.source_evidence)
            and row.source_evidence[0].get("target_excluded") is True
            and row.source_evidence[0].get("temporal_valid") is True
            for row in rows
        ),
        "historical_lineage_db_revalidated": all(
            _is_sha256(row.historical_materialization_identity_checksum)
            and _is_sha256(row.historical_outcome_identity_checksum)
            and _is_sha256(row.feature_snapshot_checksum)
            for row in rows
        ),
        "no_market_or_clv": all(
            row.market_home_probability is None
            and row.market_timestamp is None
            and row.market_books == 0
            and row.closing_home_probability is None
            and row.closing_timestamp is None
            and not any(
                token in name.lower()
                for name in row.features
                for token in _FORBIDDEN_FEATURE_TOKENS
            )
            for row in rows
        ),
        "no_target_named_features": all(
            not any(
                token in name.lower()
                for name in row.features
                for token in _FORBIDDEN_FEATURE_TOKENS[:6]
            )
            for row in rows
        ),
        "final_scores_targets_only": all(
            row.home_win == int(row.home_runs > row.away_runs)
            and row.run_differential == row.home_runs - row.away_runs
            and row.total_runs == row.home_runs + row.away_runs
            for row in rows
        ),
        "feature_schema_consistent": all(
            row.feature_schema_version == A2_SCHEMA_VERSION for row in rows
        ),
        "a2_output_order_exact": all(
            tuple(row.features) == A2_SCHEMA.output_names for row in rows
        ),
        "historical_population_only": all(
            row.temporal_semantics == A2_TEMPORAL_SEMANTICS
            and row.cutoff_policy_version == A2_CUTOFF_POLICY_VERSION
            for row in rows
        ),
        "historical_label_cutoff": all(
            row.label_available_at is not None
            and datetime.fromisoformat(row.label_available_at.replace("Z", "+00:00"))
            <= training_cutoff
            for row in rows
        ),
        "historical_tier_consistent": tiers
        == (["A"] if population is A2Population.TIER_A_STRICT else ["B"]),
    }
    identity = {
        "stage": "GATE_3A_A2",
        "population": population.value,
        "rows_checksum": rows_checksum,
        "row_count": len(rows),
        "excluded_row_count": len(exclusions),
        "exclusion_reasons": dict(sorted(reasons.items())),
        "feature_schema_version": A2_SCHEMA_VERSION,
        "feature_schema_hash": A2_SCHEMA_HASH,
        "feature_version_id": str(feature_version.id),
        "feature_names": list(A2_SCHEMA.output_names),
        "target_definitions": A2_TARGET_DEFINITIONS,
        "training_cutoff": _iso(training_cutoff),
        "leakage_checks": leakage_checks,
    }
    manifest_checksum = _checksum(identity)
    family_coverage = _required_family_coverage(rows)
    return TrainingDatasetManifestData(
        dataset_id=f"mlb-gate3-a2-{manifest_checksum[:16]}",
        created_at=_iso(datetime.now(UTC)),
        date_start=min(dates).isoformat() if dates else None,
        date_end=max(dates).isoformat() if dates else None,
        seasons=[2025] if rows else [],
        feature_schema_version=A2_SCHEMA_VERSION,
        feature_version=f"{feature_version.name}/{feature_version.version}",
        feature_version_id=str(feature_version.id),
        feature_names=list(A2_SCHEMA.output_names),
        row_count=len(rows),
        excluded_row_count=len(exclusions),
        exclusion_reasons=dict(sorted(reasons.items())),
        source_modes=[A2_SOURCE_MODE] if rows else [],
        rows_checksum=rows_checksum,
        checksum=manifest_checksum,
        target_definitions=dict(A2_TARGET_DEFINITIONS),
        training_cutoff=_iso(training_cutoff),
        leakage_checks=leakage_checks,
        feature_family_coverage=family_coverage,
        population=A2_DATASET_POPULATION,
        temporal_semantics=A2_TEMPORAL_SEMANTICS,
        cutoff_policy_version=A2_CUTOFF_POLICY_VERSION,
        quality_tiers=tiers,
        quality_score_summary=(
            {
                "minimum": min(scores),
                "maximum": max(scores),
                "mean": sum(scores) / len(scores),
            }
            if scores
            else None
        ),
        observed_feature_names=observed_raw_feature_names,
        frozen_output_names=list(A2_SCHEMA.output_names),
        feature_family_coverage_basis=(
            "GATE3_REQUIRED_FAMILIES_MAPPED_TO_EXPLICIT_A2_RAW_SUBSETS_V1"
        ),
    )


def _date_boundary(value: str | None, *, end: bool) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(
        date.fromisoformat(value), time.max if end else time.min, tzinfo=UTC
    )


def build_and_persist_a2_dataset(
    session: Session,
    *,
    artifact_root: Path,
    inputs: A2OrmInputBundle | None = None,
    population: A2Population = A2Population.TIER_A_STRICT,
    policy: A2PipelinePolicy = A2PipelinePolicy(),
    config: A2ReconstructionConfig = A2ReconstructionConfig(),
    training_cutoff: datetime,
    code_revision: str,
    readiness_policy: DatasetReadinessPolicy = DatasetReadinessPolicy(),
    maximum_artifact_bytes: int = 128 * 1024 * 1024,
    minimum_free_bytes_after_write: int = DEFAULT_A2_MIN_FREE_BYTES_AFTER_WRITE,
) -> A2DatasetPersistenceResult:
    """Build the immutable A2 dataset, artifact, ORM manifest, and audit result."""

    population = A2Population(population)
    bundle = inputs or load_a2_orm_inputs(
        session, population=population, policy=policy, config=config
    )
    if bundle.population is not population:
        raise A2PipelineError("Supplied A2 input bundle has another population")
    cutoff = _as_utc(training_cutoff)
    feature_version = ensure_a2_feature_version(
        session, population=population, config=config
    )
    rows: list[TrainingRow] = []
    exclusions: list[dict[str, str]] = []
    current_drafts = A2ChronologicalReconstructor(config).iter_rows(
        target_games=bundle.target_games,
        final_outcomes=bundle.final_outcomes,
        team_facts=bundle.team_facts,
        player_facts=bundle.player_facts,
        venues=bundle.venues,
        population=population,
    )
    for target, current_row in zip(bundle.target_games, current_drafts, strict=True):
        if current_row.game_id != target.game_id:
            raise A2PipelineError("A2 current-row order does not match target order")
        outcome = session.get(GameFinalOutcome, bundle.target_outcome_ids[target.game_id])
        if outcome is None:
            raise A2PipelineError("A2 target outcome disappeared during dataset build")
        checkpoint = session.get(
            HistoryBackfillCheckpoint, bundle.target_checkpoint_ids[target.game_id]
        )
        if checkpoint is None:
            raise A2PipelineError("A2 target checkpoint disappeared during dataset build")
        source_bundle = _source_bundle(current_row, bundle)
        source_bundle_checksum = _checksum(source_bundle)
        attestation_identity = _checksum(
            _attestation_identity_payload(
                row=current_row,
                feature_version_id=feature_version.id,
                checkpoint_id=checkpoint.id,
                source_bundle_checksum=source_bundle_checksum,
            )
        )
        attestation = session.scalar(
            select(HistoricalEvidenceStateAttestation).where(
                HistoricalEvidenceStateAttestation.identity_checksum
                == attestation_identity
            )
        )
        if attestation is None:
            raise A2PipelineError("Current A2 evidence-state attestation is missing")
        _verify_attestation(
            attestation,
            expected_identity=attestation_identity,
            row=current_row,
            source_bundle_checksum=source_bundle_checksum,
        )
        snapshot = session.scalar(
            select(GameFeatureSnapshot).where(
                GameFeatureSnapshot.checksum == current_row.row_checksum
            )
        )
        if snapshot is None:
            raise A2PipelineError("Current A2 materialization snapshot is missing")
        evidence_manifest = [
            {
                "attestation_identity_checksum": attestation_identity,
                "canonical_set_checksum": current_row.compact_lineage.canonical_set_checksum,
                "feature_values_checksum": current_row.feature_values_checksum,
                "lineage_version": A2_LINEAGE_VERSION,
                "target_excluded": True,
                "temporal_valid": True,
            }
        ]
        evidence_manifest_checksum = _checksum(evidence_manifest)
        materialization_identity = _checksum(
            _materialization_identity_payload(
                row=current_row,
                feature_version_id=feature_version.id,
                outcome=outcome,
                checkpoint_id=checkpoint.id,
                attestation_identity=attestation_identity,
                snapshot_checksum=snapshot.checksum,
                source_bundle_checksum=source_bundle_checksum,
                evidence_manifest_checksum=evidence_manifest_checksum,
                code_revision=code_revision,
            )
        )
        materialization = session.scalar(
            select(HistoricalFeatureMaterialization).where(
                HistoricalFeatureMaterialization.identity_checksum
                == materialization_identity
            )
        )
        if materialization is None:
            raise A2PipelineError("Current A2 materialization identity is missing")
        feature_items = tuple(
            (name, snapshot.feature_values.get(name)) for name in A2_SCHEMA.value_names
        )
        if _checksum({"feature_items": feature_items}) != attestation.feature_values_checksum:
            raise A2PipelineError("A2 snapshot values fail attestation checksum")
        expected_tier = "A" if population is A2Population.TIER_A_STRICT else "B"
        expected_training_eligible = population is A2Population.TIER_A_STRICT
        if (
            materialization.game_final_outcome_id != outcome.id
            or materialization.training_eligible != expected_training_eligible
            or materialization.eligibility_status != "ELIGIBLE"
            or materialization.quality_tier != expected_tier
            or snapshot.source_mode != A2_SOURCE_MODE
            or snapshot.market_snapshot_ids
            or snapshot.statcast_observation_ids
            or not attestation.target_excluded
            or not attestation.temporal_valid
        ):
            raise A2PipelineError("A2 dataset admission integrity failed")
        if _as_utc(outcome.label_available_at) > cutoff:
            raise A2PipelineError("A2 label is unavailable by the training cutoff")
        if target.scheduled_first_pitch >= cutoff:
            raise A2PipelineError("A2 target game is not before the training cutoff")
        source_evidence = [
            {
                "lineage_version": A2_LINEAGE_VERSION,
                "attestation_identity_checksum": attestation.identity_checksum,
                "canonical_set_checksum": attestation.canonical_set_checksum,
                "source_bundle_checksum": attestation.source_bundle_checksum,
                "outcome_history_count": attestation.outcome_history_count,
                "team_history_count": attestation.team_history_count,
                "player_history_count": attestation.player_history_count,
                "target_excluded": True,
                "temporal_valid": True,
            }
        ]
        home_runs = int(outcome.home_score)
        away_runs = int(outcome.away_score)
        rows.append(
            TrainingRow(
                game_id=target.game_id,
                scheduled_first_pitch=_iso(target.scheduled_first_pitch),
                data_as_of=_iso(target.cutoff),
                lifecycle="HISTORICAL_A2_T_MINUS_6H",
                feature_snapshot_id=str(snapshot.id),
                feature_snapshot_checksum=snapshot.checksum,
                feature_version_id=str(feature_version.id),
                feature_version=f"{feature_version.name}/{feature_version.version}",
                feature_schema_version=A2_SCHEMA_VERSION,
                source_mode=A2_SOURCE_MODE,
                source_evidence=source_evidence,
                home_team_id=str(outcome.home_team_id),
                away_team_id=str(outcome.away_team_id),
                features=_model_ready_features(snapshot),
                missing_features=sorted(str(value) for value in snapshot.missing_features),
                stale_features=[],
                provisional_features=sorted(
                    str(value) for value in snapshot.provisional_features
                ),
                feature_completeness=float(snapshot.feature_completeness),
                home_win=int(home_runs > away_runs),
                home_runs=home_runs,
                away_runs=away_runs,
                run_differential=home_runs - away_runs,
                total_runs=home_runs + away_runs,
                market_home_probability=None,
                market_timestamp=None,
                market_books=0,
                closing_home_probability=None,
                closing_timestamp=None,
                temporal_semantics=A2_TEMPORAL_SEMANTICS,
                historical_materialization_id=str(materialization.id),
                historical_materialization_identity_checksum=
                materialization.identity_checksum,
                historical_outcome_id=str(outcome.id),
                historical_outcome_identity_checksum=outcome.identity_checksum,
                historical_quality_tier=expected_tier,
                historical_quality_score=float(materialization.quality_score),
                cutoff_policy_version=A2_CUTOFF_POLICY_VERSION,
                materializer_version=A2_MATERIALIZER_VERSION,
                label_available_at=_iso(outcome.label_available_at),
            )
        )
    rows.sort(key=lambda item: (item.scheduled_first_pitch, item.game_id))
    manifest_data = _dataset_manifest_data(
        rows,
        exclusions=exclusions,
        population=population,
        feature_version=feature_version,
        training_cutoff=cutoff,
    )
    build = DatasetBuildResult(
        manifest=manifest_data,
        rows=tuple(rows),
        exclusions=tuple(exclusions),
    )
    if not all(manifest_data.leakage_checks.values()):
        raise A2PipelineError("A2 dataset leakage checks failed")
    readiness = audit_historical_dataset_readiness(
        manifest_data, build.rows, policy=readiness_policy
    )
    if (
        population is A2Population.TIER_B_ENHANCED
        and readiness.status != "NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT"
    ):
        raise A2PipelineError("Tier B enhanced research data cannot become Gate 3 ready")
    artifact = TrainingDatasetArtifactStore(
        artifact_root,
        maximum_artifact_bytes=maximum_artifact_bytes,
        minimum_free_bytes_after_write=minimum_free_bytes_after_write,
    ).save(build)
    existing = session.scalar(
        select(TrainingDatasetManifest).where(
            TrainingDatasetManifest.identity_checksum == artifact.checksum
        )
    )
    if existing is None:
        name = f"mlb_historical_a2_{population.value.lower()}"
        collision = session.scalar(
            select(TrainingDatasetManifest).where(
                TrainingDatasetManifest.name == name,
                TrainingDatasetManifest.version == artifact.dataset_id,
            )
        )
        if collision is not None:
            raise A2PipelineError("A2 TrainingDatasetManifest semantic collision")
        start = _date_boundary(artifact.date_start, end=False) or cutoff
        end = _date_boundary(artifact.date_end, end=True) or start
        attestation_ids = sorted(
            row.source_evidence[0]["attestation_identity_checksum"] for row in rows
        )
        existing = TrainingDatasetManifest(
            name=name,
            version=artifact.dataset_id,
            feature_version_id=feature_version.id,
            source_mode=A2_SOURCE_MODE,
            schema_version=A2_SCHEMA_VERSION,
            target=A2_TARGET,
            target_definition=artifact.target_definitions,
            cutoff_policy={
                "policy_version": A2_CUTOFF_POLICY_VERSION,
                "hours_before_first_pitch": 6,
                "population": population.value,
                "event_boundary": "STRICTLY_BEFORE_CUTOFF",
                "eligibility_boundary": "AT_OR_BEFORE_CUTOFF",
            },
            split_policy={
                "status": "GATE_3A_A2_READINESS_AUDIT_ONLY",
                "readiness": readiness.as_dict(),
                "model_training_invoked": False,
                "model_fitting_invoked": False,
            },
            feature_names=list(A2_SCHEMA.output_names),
            seasons=artifact.seasons,
            event_start_at=start,
            event_end_at=end,
            row_count=artifact.row_count,
            game_count=artifact.row_count,
            excluded_row_count=artifact.excluded_row_count,
            artifact_uri=artifact.artifact_uri or "",
            artifact_checksum=artifact.rows_checksum,
            source_evidence=[
                {
                    "lineage_version": A2_LINEAGE_VERSION,
                    "attestation_count": len(attestation_ids),
                    "attestation_identity_set_checksum": _checksum(
                        {"identity_checksums": attestation_ids}
                    ),
                    "input_checksum": bundle.input_checksum,
                }
            ],
            quality_summary={
                "stage": "GATE_3A_A2",
                "population": population.value,
                "schema_hash": A2_SCHEMA_HASH,
                "raw_value_count": len(A2_SCHEMA.value_names),
                "ordered_output_count": len(A2_SCHEMA.output_names),
                "quality_tiers": artifact.quality_tiers,
                "quality_score_summary": artifact.quality_score_summary,
                "feature_family_coverage": artifact.feature_family_coverage,
                "raw_a2_feature_family_coverage": _raw_a2_family_coverage(rows),
                "required_family_coverage_basis":
                artifact.feature_family_coverage_basis,
                "leakage_checks": artifact.leakage_checks,
                "readiness": readiness.as_dict(),
                "model_training_invoked": False,
                "model_fitting_invoked": False,
            },
            code_revision=code_revision,
            identity_checksum=artifact.checksum,
            built_at=_as_utc(
                datetime.fromisoformat(artifact.created_at.replace("Z", "+00:00"))
            ),
        )
        session.add(existing)
        session.flush()
    else:
        if (
            existing.feature_version_id != feature_version.id
            or existing.schema_version != A2_SCHEMA_VERSION
            or existing.artifact_checksum != artifact.rows_checksum
            or existing.row_count != len(rows)
            or existing.feature_names != list(A2_SCHEMA.output_names)
        ):
            raise A2PipelineError("Existing A2 dataset manifest failed revalidation")
    session.commit()
    return A2DatasetPersistenceResult(
        population=population,
        build=build,
        artifact=artifact,
        manifest_id=existing.id,
        readiness=readiness,
    )


def run_a2_pipeline(
    session: Session,
    *,
    artifact_root: Path,
    population: A2Population = A2Population.TIER_A_STRICT,
    policy: A2PipelinePolicy = A2PipelinePolicy(),
    config: A2ReconstructionConfig = A2ReconstructionConfig(),
    generated_at: datetime | None = None,
    training_cutoff: datetime,
    code_revision: str,
    batch_size: int = 100,
    readiness_policy: DatasetReadinessPolicy = DatasetReadinessPolicy(),
    maximum_artifact_bytes: int = 128 * 1024 * 1024,
    minimum_free_bytes_after_write: int = DEFAULT_A2_MIN_FREE_BYTES_AFTER_WRITE,
) -> A2PipelineRunResult:
    """End-to-end provider-free A2 materialization and audit-only dataset build."""

    population = A2Population(population)
    inputs = load_a2_orm_inputs(
        session, population=population, policy=policy, config=config
    )
    materialization = materialize_a2_population(
        session,
        inputs=inputs,
        population=population,
        policy=policy,
        config=config,
        generated_at=generated_at,
        code_revision=code_revision,
        batch_size=batch_size,
    )
    dataset = build_and_persist_a2_dataset(
        session,
        artifact_root=artifact_root,
        inputs=inputs,
        population=population,
        policy=policy,
        config=config,
        training_cutoff=training_cutoff,
        code_revision=code_revision,
        readiness_policy=readiness_policy,
        maximum_artifact_bytes=maximum_artifact_bytes,
        minimum_free_bytes_after_write=minimum_free_bytes_after_write,
    )
    return A2PipelineRunResult(
        inputs=inputs,
        materialization=materialization,
        dataset=dataset,
    )


__all__ = [
    "A2DatasetPersistenceResult",
    "A2EvidenceReference",
    "A2MaterializationRunResult",
    "A2OrmInputBundle",
    "A2PipelineError",
    "A2PipelinePolicy",
    "A2PipelineRunResult",
    "A2_DATASET_POPULATION",
    "A2_FEATURE_VERSION_NAME",
    "A2_MATERIALIZER_VERSION",
    "DEFAULT_RAIN_SHORTENED_GAME_PKS",
    "DEFAULT_A2_MIN_FREE_BYTES_AFTER_WRITE",
    "build_and_persist_a2_dataset",
    "ensure_a2_feature_version",
    "load_a2_orm_inputs",
    "materialize_a2_population",
    "run_a2_pipeline",
]
