"""Provider-free, read-only readiness closeout for a finalized FD1 recovery.

The original Gate 3C readiness adapter was bound to an execute/postcommit
publication ledger.  Recovery deliberately publishes from the already-created
Parquet corpus instead.  This module closes that gap without weakening the
readiness contract: it proves the recovery is FINALIZED, re-reads only the
manifest's exact 329 Parquet files, projects :class:`FD1ReadinessInput`, calls
``audit_fd1_readiness``, and publishes one immutable content-addressed result.

No provider is imported or constructed.  SQL is guarded by a read-only
transaction and a statement allow-list.  The only write is the requested JSON
evidence below the ignored recovery evidence directory.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from types import MappingProxyType
from typing import Any
from uuid import UUID

import polars as pl
from sqlalchemy import Engine, event, select, text

from app.gen2.fd1_identity import FD1_IDENTITY_SCHEMA_VERSION, ResolutionStatus, SourceFileEvidence
from app.gen2.fd1_identity_coverage import (
    ActiveIdentityCoverageReport,
    ActiveIdentityIssue,
    AffectedRole,
    ExactLinkRate,
    IdentityCoverageSlice,
    IdentityNamespace,
    IdentityRelation,
    NamespaceCoverage,
    RetrosheetIdentitySourceEvidence,
)
from app.gen2.fd1_materializer import MATERIALIZED_FAMILY_SCHEMA_VERSIONS
from app.gen2.fd1_readiness import (
    FD1ReadinessInput,
    FD1ReadinessResult,
    OutcomeCount,
    PA_OUTCOME_NAMES,
    PRIMARY_SEASONS,
    PRIOR_ONLY_SEASONS,
    PopulationSeparationSummary,
    ReadinessDecision,
    TemporalAuditSummary,
    TemporalFoldEvidence,
    TrainingEligibilitySummary,
    audit_fd1_readiness,
)
from app.gen2.fd1_readiness_closeout import (
    ACTIVE_SEASONS,
    EXPECTED_REGULAR_SEASON_GAMES,
    FD1ManagerCoverageEvidence,
    FD1SeasonWarehouseEvidence,
    FD1UmpireCoverageEvidence,
    FD1WarehouseCloseoutEvidence,
    OPERATOR_SCHEMA_VERSION,
    REQUIRED_SOURCE_KEYS,
    _asp_summary,
    _park_rows_by_target_season,
    _prior_rows_by_season,
    _season_summary,
    _source_summaries,
    _validate_active_identity_summary,
    _validate_identity_conflict_projection,
    _validate_reconciliation_operator_binding,
)
from app.gen2.fd1_reconciliation import (
    FIELD_FAMILIES,
    AspGameEvidence,
    ComparisonStatus,
    FieldComparison,
    GameReconciliation,
    ReconciliationResult,
    RetrosheetGameEvidence,
)
from app.gen2.fd1_recovery import RECOVERY_REPORT_SCHEMA_VERSION
from app.gen2.fd1_recovery_manifest import RecoveryFileEntry, RecoveryManifest, load_recovery_manifest
from app.gen2.fd1_recovery_models import (
    FD1RecoveryIdentityQuarantine,
    FD1RecoveryIdentityStage,
    FD1RecoveryManifestFile,
    FD1RecoveryPublicationStage,
    FD1RecoveryRun,
    FD1RecoveryStageCheckpoint,
    RECOVERY_STAGES,
)
from app.gen2.fd1_recovery_publication import (
    PublicationTarget,
    _classify_targets,
)
from app.gen2.fd1_recovery_validation import (
    EXPECTED_FULL_BYTE_COUNT,
    EXPECTED_FULL_FILE_COUNT,
    EXPECTED_FULL_ROW_COUNT,
    WAREHOUSE_VALIDATION_SCHEMA_VERSION,
)
from app.gen2.fd1_warehouse import canonical_sha256
from app.gen2.parquet_store import HistoricalEventPartitionArtifact
from app.models import HistoricalEventPartition, OpenDataIdentityMapping


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STORE_ROOT = PROJECT_ROOT / "data" / "curated" / "gen2-fd1"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / ".runtime" / "gate3c-fd1"
DEFAULT_ARTIFACT_ROOT = DEFAULT_RUNTIME_ROOT / "recovery" / "readiness"

FD1_RECOVERY_READINESS_SCHEMA_VERSION = "gate3c_fd1_recovery_readiness_v1"
FD1_RECOVERY_READINESS_CODE_REVISION_INPUTS = (
    "server/app/gen2/fd1_identity_coverage.py",
    "server/app/gen2/fd1_readiness.py",
    "server/app/gen2/fd1_readiness_closeout.py",
    "server/app/gen2/fd1_reconciliation.py",
    "server/app/gen2/fd1_recovery.py",
    "server/app/gen2/fd1_recovery_manifest.py",
    "server/app/gen2/fd1_recovery_models.py",
    "server/app/gen2/fd1_recovery_publication.py",
    "server/app/gen2/fd1_recovery_readiness.py",
    "server/app/gen2/fd1_recovery_validation.py",
    "server/app/gen2/parquet_store.py",
)
ACTIVE_IDENTITY_OPERATOR_SCHEMA_VERSION = "gate3c_fd1_active_identity_actual_operator_v1"
TIER_PROJECTION_POLICY = (
    "ONE_MANIFESTED_PARQUET_ROW_ONE_DISJOINT_UNIT; PRIOR_ONLY_PRECEDENCE; "
    "ACTUAL_LINEUP_STARTER_RELIEVER_PLAY_STATE_AND_UNTIMESTAMPED_WEATHER_"
    "ENHANCED_ONLY; REMAINING_TARGET_OR_LAGGABLE_ROWS_STRICT"
)
PLAYER_PRIOR_FAMILY = "player_prior"
RETROSHEET_SOURCE_KEY = "RETROSHEET_CSV_DOWNLOADS"
LAHMAN_SOURCE_KEY = "SABR_LAHMAN_BOX_DOWNLOADS"
GEN2_PRIMARY_POPULATION = "GEN2_PRIMARY_2023_2025"
GEN2_PRIOR_ONLY_POPULATION = "GEN2_PRIOR_ONLY_2021_2022"
KNOWLEDGE_CUTOFF = date(2026, 1, 1)

_SOURCE_FILENAME_BY_FAMILY: Mapping[str, str] = MappingProxyType(
    {
        "plate_appearance": "plays.csv",
        "play_transition": "plays.csv",
        "pitch_sequence": "plays.csv",
        "player_game_batting": "batting.csv",
        "player_game_pitching": "pitching.csv",
        "player_game_fielding": "fielding.csv",
        "starter_usage": "pitching.csv",
        "bullpen_usage": "pitching.csv",
        "weather": "gameinfo.csv",
    }
)
_UMPIRE_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "home": "umpire_home_id",
        "first_base": "umpire_first_base_id",
        "second_base": "umpire_second_base_id",
        "third_base": "umpire_third_base_id",
        "left_field": "umpire_left_field_id",
        "right_field": "umpire_right_field_id",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_PERSON_ID = re.compile(r"^retrosheet:player:[a-z-]{5}[0-9]{3}$")
_SOURCE_GAME_ID = re.compile(r"^retrosheet:game:[A-Z0-9]{3}[0-9]{9}$")
_RECOVERY_RESULT_NAME = re.compile(r"^fd1-recovery-result-sha256-([0-9a-f]{64})\.json$")
_CONTENT_ADDRESSED_JSON = re.compile(r"^.+-sha256-([0-9a-f]{64})\.json$")
_MUTATING_SQL = re.compile(
    r"\b(?:ALTER|ANALYZE|CALL|CLUSTER|COPY|CREATE|DELETE|DO|DROP|GRANT|INSERT|"
    r"LOCK|MERGE|REFRESH|REINDEX|REVOKE|TRUNCATE|UPDATE|VACUUM)\b"
)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class FD1RecoveryReadinessError(RuntimeError):
    """Finalized recovery evidence cannot support an exact readiness decision."""


@dataclass(frozen=True, slots=True)
class ArtifactEvidence:
    logical_uri: str
    sha256: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class GovernanceEvidence:
    inventory: ArtifactEvidence
    attribution: ArtifactEvidence
    authorization_recorded_source_keys: tuple[str, ...]
    use_classification_recorded_source_keys: tuple[str, ...]
    license_recorded_source_keys: tuple[str, ...]
    attribution_required_source_keys: tuple[str, ...]
    attribution_recorded_source_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecoveryDatabaseEvidence:
    backend: str
    transaction_read_only: bool
    transaction_rolled_back: bool
    read_statements: int
    rejected_statements: int
    checkpoint_count: int
    checkpoint_output_checksums: Mapping[str, str]
    canonical_counts: Mapping[str, int]
    identity_outcomes: Mapping[str, int]
    quarantine_rows: int
    resolved_mapping_rows: int


@dataclass(frozen=True, slots=True)
class ReadinessScanProgress:
    files_scanned: int
    files_total: int
    rows_scanned: int
    rows_total: int
    bytes_scanned: int
    bytes_total: int
    current_logical_uri: str | None


@dataclass(frozen=True, slots=True)
class WarehouseScan:
    evidence: FD1WarehouseCloseoutEvidence
    partition_counts: Mapping[str, int]
    row_counts: Mapping[str, int]
    schema_version_counts: Mapping[str, int]
    file_bytes: int
    validated_partition_count: int
    validated_row_count: int
    row_lineage_checked_count: int
    manager_lineage_checked_count: int
    umpire_lineage_checked_count: int


@dataclass(frozen=True, slots=True)
class FD1RecoveryReadinessResult:
    decision: str
    modeling_can_proceed: bool
    artifact: ArtifactEvidence
    artifact_reused: bool
    recovery_id: str
    manifest_checksum: str
    warehouse_derivation_checksum: str
    readiness_result_checksum: str
    partition_count: int
    row_count: int
    file_bytes: int
    provider_calls: int = 0
    network_requests: int = 0
    database_writes: int = 0
    governed_parquet_writes: int = 0
    raw_source_scans: int = 0

    def as_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(slots=True)
class _DatabaseBoundary:
    backend: str
    read_statements: int = 0
    rejected_statements: int = 0
    transaction_read_only: bool = False
    transaction_rolled_back: bool = False


@dataclass(slots=True)
class _SeasonAccumulator:
    season: int
    games: set[str]
    final_scores: dict[str, tuple[int, int, int]]
    pa_games: set[str]
    pa_dates: set[date]
    plate_appearances: int
    transition_games: set[str]
    transitions: int
    pitch_sequence_games: set[str]
    fielding_games: set[str]
    fielding_rows: int
    weather_games: set[str]
    fully_covered_weather_games: set[str]
    lineup_complete_teams: dict[str, set[str]]
    lineup_isolated_teams: dict[str, set[str]]
    historical_actual_lineup_rows: int
    isolated_enhanced_lineup_rows: int
    usage_sequences: dict[tuple[str, str], set[int]]
    starter_teams: dict[str, set[str]]
    starter_labeled_teams: dict[str, set[str]]
    starter_duration_rows: int
    historical_actual_starter_rows: int
    isolated_enhanced_starter_rows: int
    bullpen_rows: int
    manager_pairs: dict[str, set[tuple[str, str]]]
    manager_usage_rows: int
    manager_populated_usage_rows: int
    manager_lineage_rows: int
    manager_ids: set[str]
    umpire_plate_games: set[str]
    umpire_assignments: int
    umpire_ids: set[str]
    umpire_lineage_weather_rows: int
    taxonomy_terminal_events: int
    taxonomy_outcomes: Counter[str]
    unsafe_taxonomy_event_ids: set[str]
    tier_candidate_rows: int
    strict_rows: int
    enhanced_rows: int
    prior_only_rows: int
    chronology_checked_rows: int
    primary_pa_rows: int

    @classmethod
    def create(cls, season: int) -> _SeasonAccumulator:
        return cls(
            season=season,
            games=set(),
            final_scores={},
            pa_games=set(),
            pa_dates=set(),
            plate_appearances=0,
            transition_games=set(),
            transitions=0,
            pitch_sequence_games=set(),
            fielding_games=set(),
            fielding_rows=0,
            weather_games=set(),
            fully_covered_weather_games=set(),
            lineup_complete_teams=defaultdict(set),
            lineup_isolated_teams=defaultdict(set),
            historical_actual_lineup_rows=0,
            isolated_enhanced_lineup_rows=0,
            usage_sequences=defaultdict(set),
            starter_teams=defaultdict(set),
            starter_labeled_teams=defaultdict(set),
            starter_duration_rows=0,
            historical_actual_starter_rows=0,
            isolated_enhanced_starter_rows=0,
            bullpen_rows=0,
            manager_pairs=defaultdict(set),
            manager_usage_rows=0,
            manager_populated_usage_rows=0,
            manager_lineage_rows=0,
            manager_ids=set(),
            umpire_plate_games=set(),
            umpire_assignments=0,
            umpire_ids=set(),
            umpire_lineage_weather_rows=0,
            taxonomy_terminal_events=0,
            taxonomy_outcomes=Counter(),
            unsafe_taxonomy_event_ids=set(),
            tier_candidate_rows=0,
            strict_rows=0,
            enhanced_rows=0,
            prior_only_rows=0,
            chronology_checked_rows=0,
            primary_pa_rows=0,
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        items = [_plain(item) for item in value]
        return (
            sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
            if isinstance(value, (set, frozenset))
            else items
        )
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _checksum(value: Any) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FD1RecoveryReadinessError(f"{context} must be a mapping")
    return value


def _sequence(value: object, context: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise FD1RecoveryReadinessError(f"{context} must be a sequence")
    return value


def _mapping_sequence(value: object, context: str) -> tuple[Mapping[str, Any], ...]:
    return tuple(_mapping(item, context) for item in _sequence(value, context))


def _is_linklike(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _safe_file(path: Path, root: Path, context: str) -> Path:
    lexical_root = Path(root).absolute()
    lexical_path = Path(path).absolute()
    try:
        relative = lexical_path.relative_to(lexical_root)
        selected = lexical_path.resolve(strict=True)
        selected.relative_to(lexical_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise FD1RecoveryReadinessError(f"{context} escapes its governed root") from exc
    current = lexical_root
    if _is_linklike(current):
        raise FD1RecoveryReadinessError(f"{context} root is linked")
    for part in relative.parts:
        current = current / part
        if _is_linklike(current):
            raise FD1RecoveryReadinessError(f"{context} path contains a link")
    if _is_linklike(selected) or not selected.is_file():
        raise FD1RecoveryReadinessError(f"{context} is not a safe regular file")
    return selected


def _logical(path: Path, *, project_root: Path = PROJECT_ROOT) -> str:
    try:
        return path.resolve(strict=True).relative_to(project_root.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise FD1RecoveryReadinessError("evidence path escapes the repository") from exc


def _artifact(path: Path, *, project_root: Path = PROJECT_ROOT) -> ArtifactEvidence:
    selected = path.resolve(strict=True)
    return ArtifactEvidence(
        logical_uri=_logical(selected, project_root=project_root),
        sha256=_sha256_file(selected),
        byte_count=selected.stat().st_size,
    )


def _load_content_addressed_json(
    path: Path,
    *,
    root: Path,
    pattern: re.Pattern[str] = _CONTENT_ADDRESSED_JSON,
    context: str,
    project_root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], ArtifactEvidence]:
    selected = _safe_file(path, root, context)
    match = pattern.fullmatch(selected.name)
    if match is None:
        raise FD1RecoveryReadinessError(f"{context} is not content addressed")
    evidence = _artifact(selected, project_root=project_root)
    if match.group(1) != evidence.sha256:
        raise FD1RecoveryReadinessError(f"{context} filename checksum changed")
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FD1RecoveryReadinessError(f"{context} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise FD1RecoveryReadinessError(f"{context} must contain one JSON object")
    return value, evidence


def _load_recovery_result(
    path: Path,
    *,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    project_root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], ArtifactEvidence]:
    document, artifact = _load_content_addressed_json(
        path,
        root=runtime_root / "recovery" / "reports",
        pattern=_RECOVERY_RESULT_NAME,
        context="recovery result",
        project_root=project_root,
    )
    supplied = document.get("result_checksum")
    unsigned = dict(document)
    unsigned.pop("result_checksum", None)
    if supplied != _checksum(unsigned):
        raise FD1RecoveryReadinessError("recovery result checksum changed")
    if (
        document.get("schema_version") != RECOVERY_REPORT_SCHEMA_VERSION
        or document.get("mode") != "finalize"
        or document.get("status") != "FINALIZED"
        or document.get("last_completed_stage") != "FINALIZED"
        or document.get("idempotent_reuse") is not False
    ):
        raise FD1RecoveryReadinessError(
            "readiness requires the original successful FINALIZED recovery result"
        )
    for field_name in (
        "provider_calls",
        "network_requests",
        "provider_transports_constructed",
        "mapping_conflicts",
    ):
        if document.get(field_name) != 0:
            raise FD1RecoveryReadinessError(
                f"recovery result has nonzero {field_name}"
            )
    return document, artifact


def _identity_slice(raw: Mapping[str, Any]) -> IdentityCoverageSlice:
    return IdentityCoverageSlice(
        label=str(raw["label"]),
        population=str(raw["population"]),
        seasons=tuple(int(value) for value in _sequence(raw.get("seasons"), "identity seasons")),
        unique_retrosheet_players=int(raw["unique_retrosheet_players"]),
        reference_count=int(raw["reference_count"]),
        namespace_coverage=tuple(
            NamespaceCoverage(
                namespace=IdentityNamespace(str(row["namespace"])),
                unique_player_count=int(row["unique_player_count"]),
                exact_unique_player_count=int(row["exact_unique_player_count"]),
                conflict_unique_player_count=int(row["conflict_unique_player_count"]),
                unresolved_unique_player_count=int(row["unresolved_unique_player_count"]),
                reference_count=int(row["reference_count"]),
                exact_reference_count=int(row["exact_reference_count"]),
                conflict_reference_count=int(row["conflict_reference_count"]),
                unresolved_reference_count=int(row["unresolved_reference_count"]),
            )
            for row in _mapping_sequence(raw.get("namespace_coverage"), "namespace coverage")
        ),
        exact_link_rates=tuple(
            ExactLinkRate(
                relation=IdentityRelation(str(row["relation"])),
                source_unique_id_count=int(row["source_unique_id_count"]),
                exact_unique_link_count=int(row["exact_unique_link_count"]),
                conflict_unique_link_count=int(row["conflict_unique_link_count"]),
                unresolved_unique_link_count=int(row["unresolved_unique_link_count"]),
                source_reference_count=int(row["source_reference_count"]),
                exact_reference_count=int(row["exact_reference_count"]),
                conflict_reference_count=int(row["conflict_reference_count"]),
                unresolved_reference_count=int(row["unresolved_reference_count"]),
            )
            for row in _mapping_sequence(raw.get("exact_link_rates"), "identity link rates")
        ),
    )


def _identity_report(raw: Mapping[str, Any]) -> ActiveIdentityCoverageReport:
    report = ActiveIdentityCoverageReport(
        schema_version=str(raw["schema_version"]),
        source_release=str(raw["source_release"]),
        source_files=tuple(
            RetrosheetIdentitySourceEvidence(
                logical_table=str(row["logical_table"]),
                source_key=str(row["source_key"]),
                source_release=str(row["source_release"]),
                content_sha256=str(row["content_sha256"]),
                content_length=int(row["content_length"]),
                rows_scanned=int(row["rows_scanned"]),
                active_references=int(row["active_references"]),
            )
            for row in _mapping_sequence(raw.get("source_files"), "identity source files")
        ),
        crosswalk_source_files=tuple(
            SourceFileEvidence(
                source_key=str(row["source_key"]),
                source_release=str(row["source_release"]),
                logical_table=str(row["logical_table"]),
                content_sha256=str(row["content_sha256"]),
                content_length=int(row["content_length"]),
            )
            for row in _mapping_sequence(
                raw.get("crosswalk_source_files"), "identity crosswalk files"
            )
        ),
        season_coverage=tuple(
            _identity_slice(row)
            for row in _mapping_sequence(raw.get("season_coverage"), "identity season coverage")
        ),
        population_coverage=tuple(
            _identity_slice(row)
            for row in _mapping_sequence(
                raw.get("population_coverage"), "identity population coverage"
            )
        ),
        overall_coverage=_identity_slice(
            _mapping(raw.get("overall_coverage"), "overall identity coverage")
        ),
        affected_players=tuple(
            ActiveIdentityIssue(
                retrosheet_id=str(row["retrosheet_id"]),
                seasons=tuple(int(value) for value in row["seasons"]),
                reference_count=int(row["reference_count"]),
                retrosheet_to_chadwick_status=ResolutionStatus(
                    str(row["retrosheet_to_chadwick_status"])
                ),
                chadwick_person_status=ResolutionStatus(str(row["chadwick_person_status"])),
                mlbam_status=ResolutionStatus(str(row["mlbam_status"])),
                lahman_status=ResolutionStatus(str(row["lahman_status"])),
                canonical_chadwick_uuid=row.get("canonical_chadwick_uuid"),
                chadwick_person_id=row.get("chadwick_person_id"),
                mlbam_id=row.get("mlbam_id"),
                lahman_player_id=row.get("lahman_player_id"),
                roles=tuple(
                    AffectedRole(
                        role=str(role["role"]),
                        reference_count=int(role["reference_count"]),
                        affected_game_ids=tuple(
                            str(value) for value in role["affected_game_ids"]
                        ),
                    )
                    for role in row["roles"]
                ),
            )
            for row in _mapping_sequence(raw.get("affected_players"), "affected players")
        ),
        primary_threshold=float(raw["primary_threshold"]),
        primary_unique_meets_threshold=bool(raw["primary_unique_meets_threshold"]),
        primary_reference_meets_threshold=bool(raw["primary_reference_meets_threshold"]),
        primary_meets_threshold=bool(raw["primary_meets_threshold"]),
        name_based_match_count=int(raw["name_based_match_count"]),
        derivation_checksum=str(raw["derivation_checksum"]),
    )
    if _checksum(report.as_dict()) != _checksum(raw):
        raise FD1RecoveryReadinessError(
            "active-identity report contains unknown or changed fields"
        )
    return report


def _load_identity_artifact(
    path: Path,
    *,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    project_root: Path = PROJECT_ROOT,
) -> tuple[ActiveIdentityCoverageReport, ArtifactEvidence, Mapping[str, Any]]:
    document, artifact = _load_content_addressed_json(
        path,
        root=runtime_root / "artifacts",
        context="active-identity artifact",
        project_root=project_root,
    )
    if document.get("schema_version") != ACTIVE_IDENTITY_OPERATOR_SCHEMA_VERSION:
        raise FD1RecoveryReadinessError("active-identity operator schema changed")
    boundary = _mapping(document.get("execution_boundary"), "identity boundary")
    network = _mapping(boundary.get("network"), "identity network boundary")
    database = _mapping(boundary.get("database"), "identity database boundary")
    if (
        network.get("external_requests") != 0
        or network.get("blocked_external_attempts") != 0
        or boundary.get("provider_transports_constructed") != 0
        or boundary.get("database_mutations") != 0
        or boundary.get("name_based_matches") != 0
        or database.get("transaction_read_only") is not True
        or database.get("transaction_rolled_back") is not True
        or database.get("rejected_statements") != 0
    ):
        raise FD1RecoveryReadinessError("active-identity execution boundary is not clean")
    report = _identity_report(
        _mapping(
            document.get("active_identity_coverage_report"),
            "active-identity coverage report",
        )
    )
    return report, artifact, document


def _asp_game(raw: Mapping[str, Any]) -> AspGameEvidence:
    return AspGameEvidence(
        game_id=str(raw["game_id"]),
        official_date=date.fromisoformat(str(raw["official_date"])),
        scheduled_at=datetime.fromisoformat(str(raw["scheduled_at"])),
        home_team_id=str(raw["home_team_id"]),
        away_team_id=str(raw["away_team_id"]),
        home_score=int(raw["home_score"]),
        away_score=int(raw["away_score"]),
        source_record_id=str(raw["source_record_id"]),
        source_version_id=str(raw["source_version_id"]),
        game_number=(int(raw["game_number"]) if raw.get("game_number") is not None else None),
        doubleheader_id=raw.get("doubleheader_id"),
        venue_id=raw.get("venue_id"),
        home_starter_id=raw.get("home_starter_id"),
        away_starter_id=raw.get("away_starter_id"),
        home_lineup_ids=raw.get("home_lineup_ids"),
        away_lineup_ids=raw.get("away_lineup_ids"),
        batting_participant_ids=raw.get("batting_participant_ids"),
        pitching_participant_ids=raw.get("pitching_participant_ids"),
        player_participant_ids=raw.get("player_participant_ids"),
        team_external_ids=_mapping(raw.get("team_external_ids", {}), "team external IDs"),
        venue_external_ids=tuple(str(value) for value in raw.get("venue_external_ids", ())),
        source_mode=str(raw.get("source_mode", "LIVE")),
        outcome_status=str(raw.get("outcome_status", "FINAL")),
    )


def _retrosheet_game(raw: Mapping[str, Any]) -> RetrosheetGameEvidence:
    return RetrosheetGameEvidence(
        raw_game_id=str(raw["raw_game_id"]),
        source_game_id=str(raw["source_game_id"]),
        game_date=date.fromisoformat(str(raw["game_date"])),
        home_team_raw_id=str(raw["home_team_raw_id"]),
        away_team_raw_id=str(raw["away_team_raw_id"]),
        game_number=int(raw["game_number"]),
        home_score=(int(raw["home_score"]) if raw.get("home_score") is not None else None),
        away_score=(int(raw["away_score"]) if raw.get("away_score") is not None else None),
        source_game_number=(
            int(raw["source_game_number"])
            if raw.get("source_game_number") is not None
            else None
        ),
        doubleheader_id=raw.get("doubleheader_id"),
        park_raw_id=raw.get("park_raw_id"),
        home_starter_raw_id=raw.get("home_starter_raw_id"),
        away_starter_raw_id=raw.get("away_starter_raw_id"),
        home_lineup_raw_ids=raw.get("home_lineup_raw_ids"),
        away_lineup_raw_ids=raw.get("away_lineup_raw_ids"),
        batting_participant_raw_ids=raw.get("batting_participant_raw_ids"),
        pitching_participant_raw_ids=raw.get("pitching_participant_raw_ids"),
        player_participant_raw_ids=raw.get("player_participant_raw_ids"),
        game_type=str(raw.get("game_type", "regular")),
        score_evidence=raw.get("score_evidence"),
    )


def _reconciliation_result(raw: Mapping[str, Any]) -> ReconciliationResult:
    games: list[GameReconciliation] = []
    for item in _mapping_sequence(raw.get("games"), "reconciliation games"):
        comparisons_raw = _mapping(item.get("comparisons"), "game comparisons")
        comparisons = {
            family: FieldComparison(
                status=ComparisonStatus(str(comparisons_raw[family]["status"])),
                asp_value=comparisons_raw[family].get("asp"),
                retrosheet_value=comparisons_raw[family].get("retrosheet"),
                details=MappingProxyType(
                    dict(
                        _mapping(
                            comparisons_raw[family].get("details"),
                            f"{family} comparison details",
                        )
                    )
                ),
            )
            for family in FIELD_FAMILIES
        }
        games.append(
            GameReconciliation(
                asp=_asp_game(_mapping(item.get("asp"), "ASP game")),
                retrosheet=_retrosheet_game(
                    _mapping(item.get("retrosheet"), "Retrosheet game")
                ),
                mapping_method=str(item["mapping_method"]),
                mapped_retrosheet=MappingProxyType(
                    dict(_mapping(item.get("mapped_retrosheet"), "mapped game"))
                ),
                comparisons=MappingProxyType(comparisons),
            )
        )
    result = ReconciliationResult(
        target_season=int(raw["target_season"]),
        expected_target_games=int(raw["expected_target_games"]),
        asp_target_count=int(raw["asp_target_count"]),
        retrosheet_target_count=int(raw["retrosheet_target_count"]),
        matched_game_count=int(raw["matched_game_count"]),
        mapping_method_counts=MappingProxyType(
            {str(key): int(value) for key, value in raw["mapping_method_counts"].items()}
        ),
        family_counts=MappingProxyType(
            {
                family: MappingProxyType(
                    {
                        str(key): int(value)
                        for key, value in raw["family_counts"][family].items()
                    }
                )
                for family in FIELD_FAMILIES
            }
        ),
        games=tuple(games),
        unmatched_asp=tuple(
            _asp_game(row)
            for row in _mapping_sequence(raw.get("unmatched_asp"), "unmatched ASP games")
        ),
        unmatched_retrosheet=tuple(
            _retrosheet_game(row)
            for row in _mapping_sequence(
                raw.get("unmatched_retrosheet"), "unmatched Retrosheet games"
            )
        ),
        crosswalk_summary=MappingProxyType(
            dict(_mapping(raw.get("crosswalk_summary"), "crosswalk summary"))
        ),
    )
    if _checksum(result.as_dict(include_games=True)) != _checksum(raw):
        raise FD1RecoveryReadinessError(
            "reconciliation artifact contains unknown or changed fields"
        )
    return result


def _resolve_logical_file(
    logical_uri: str,
    *,
    root: Path,
    context: str,
) -> Path:
    if not isinstance(logical_uri, str) or "\\" in logical_uri:
        raise FD1RecoveryReadinessError(f"{context} logical URI is invalid")
    pure = PurePosixPath(logical_uri)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise FD1RecoveryReadinessError(f"{context} logical URI is unsafe")
    return _safe_file(root.joinpath(*pure.parts), root, context)


def _load_generation_ledger(
    manifest: RecoveryManifest,
    *,
    project_root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], ArtifactEvidence]:
    binding = manifest.generation_lineage.trusted_dry_run_ledger
    selected = _resolve_logical_file(
        binding.logical_uri, root=project_root, context="trusted dry-run ledger"
    )
    evidence = _artifact(selected, project_root=project_root)
    if evidence.sha256 != binding.sha256:
        raise FD1RecoveryReadinessError("trusted dry-run ledger checksum changed")
    try:
        document = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FD1RecoveryReadinessError("trusted dry-run ledger is unreadable") from exc
    if not isinstance(document, dict):
        raise FD1RecoveryReadinessError("trusted dry-run ledger must be an object")
    _validate_generation_ledger(document, manifest)
    return document, evidence


def _validate_generation_ledger(
    ledger: Mapping[str, Any], manifest: RecoveryManifest
) -> None:
    if (
        ledger.get("schema_version") != OPERATOR_SCHEMA_VERSION
        or ledger.get("schema_version")
        != manifest.generation_lineage.trusted_dry_run_ledger.schema_version
        or ledger.get("mode") != "dry-run"
        or ledger.get("success") is not True
        or ledger.get("sanitized") is not True
    ):
        raise FD1RecoveryReadinessError("trusted generation ledger is not a valid dry run")
    transaction = _mapping(ledger.get("transaction"), "generation transaction")
    if (
        transaction.get("committed") is not False
        or transaction.get("single_publish_call") is not True
        or transaction.get("status_at_closeout") != "PENDING_CALLER_ROLLBACK"
    ):
        raise FD1RecoveryReadinessError("generation transaction boundary changed")
    prohibited = _mapping(ledger.get("prohibited_activity"), "prohibited activity")
    if prohibited.get("network_requests") != 0 or any(
        prohibited.get(name) is not False
        for name in (
            "provider_transport_constructed",
            "mlb_or_savant_automation",
            "predictive_model_fit_or_promotion",
            "odds_acquisition",
            "position_population",
        )
    ):
        raise FD1RecoveryReadinessError("generation ledger reports prohibited activity")
    publication = _mapping(ledger.get("publication"), "generation publication")
    if (
        publication.get("mode") != "dry-run"
        or publication.get("network_requests") != 0
        or publication.get("provider_transport_constructed") is not False
        or publication.get("transaction_committed_by_function") is not False
        or publication.get("batches_seen") != manifest.aggregate.file_count
        or publication.get("parquet_rows") != manifest.aggregate.row_count
        or publication.get("identity_mappings_seen") != manifest.identity_spool.row_count
        or publication.get("code_revision")
        != manifest.generation_lineage.code_revision
    ):
        raise FD1RecoveryReadinessError("generation publication differs from the manifest")
    store = _mapping(ledger.get("store"), "generation store")
    if (
        store.get("logical_root") != "data/curated/gen2-fd1"
        or store.get("default_governed_limits") is not True
        or store.get("dry_run_parquet_unchanged") is not True
    ):
        raise FD1RecoveryReadinessError("generation store boundary changed")

    source_versions = _mapping(ledger.get("source_versions"), "source versions")
    declared_versions = {
        item.source_key: {
            "id": item.source_version_id,
            "dataset_version": item.dataset_version,
            "identity_checksum": item.identity_checksum,
            "source_mode": item.source_mode,
        }
        for item in manifest.generation_lineage.source_releases
    }
    if set(source_versions) != set(REQUIRED_SOURCE_KEYS):
        raise FD1RecoveryReadinessError("ledger source-version set is incomplete")
    if set(declared_versions) != {*REQUIRED_SOURCE_KEYS, "RETROSHEET_EVENT_ARCHIVES"}:
        raise FD1RecoveryReadinessError("source-version set differs from the manifest")
    for source_key in REQUIRED_SOURCE_KEYS:
        expected = declared_versions[source_key]
        observed = _mapping(source_versions[source_key], f"{source_key} source version")
        if any(str(observed.get(name)) != str(value) for name, value in expected.items()):
            raise FD1RecoveryReadinessError(
                f"{source_key} source version differs from the manifest"
            )

    downloads = {
        (str(row["source_key"]), str(row["object_key"])): row
        for row in _mapping_sequence(
            ledger.get("registered_downloads_used"), "registered downloads"
        )
    }
    archives = {
        (item.source_key, item.object_key): item
        for item in manifest.generation_lineage.source_archives
    }
    if not set(downloads) < set(archives):
        raise FD1RecoveryReadinessError("registered downloads differ from the manifest")
    if any(
        key[0] != "RETROSHEET_EVENT_ARCHIVES"
        for key in set(archives) - set(downloads)
    ):
        raise FD1RecoveryReadinessError("manifest has an unrecognized extra archive source")
    for key in downloads:
        archive = archives[key]
        row = downloads[key]
        expected = {
            "source_release": archive.source_release,
            "raw_logical_uri": archive.raw_logical_uri,
            "archive_sha256": archive.archive_sha256,
            "payload_sha256": archive.payload_sha256,
            "content_length": archive.content_length,
            "download_identity_checksum": archive.download_identity_checksum,
        }
        if any(row.get(name) != value for name, value in expected.items()):
            raise FD1RecoveryReadinessError("registered download lineage changed")


def _load_reconciliation(
    ledger: Mapping[str, Any],
    *,
    project_root: Path = PROJECT_ROOT,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
) -> tuple[ReconciliationResult, ArtifactEvidence]:
    declared = _mapping(
        ledger.get("asp_retrosheet_full_reconciliation"),
        "full reconciliation binding",
    )
    logical_uri = str(declared.get("logical_path", ""))
    selected = _resolve_logical_file(
        logical_uri, root=project_root, context="full 2025 reconciliation"
    )
    if runtime_root.resolve(strict=True) / "artifacts" not in selected.parents:
        raise FD1RecoveryReadinessError("reconciliation is outside the artifact root")
    evidence = _artifact(selected, project_root=project_root)
    if (
        evidence.logical_uri != logical_uri
        or evidence.sha256 != declared.get("sha256")
        or evidence.byte_count != declared.get("bytes")
    ):
        raise FD1RecoveryReadinessError("reconciliation differs from its ledger binding")
    match = _CONTENT_ADDRESSED_JSON.fullmatch(selected.name)
    if match is None or match.group(1) != evidence.sha256:
        raise FD1RecoveryReadinessError("reconciliation is not content addressed")
    try:
        document = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FD1RecoveryReadinessError("reconciliation is unreadable") from exc
    if not isinstance(document, dict):
        raise FD1RecoveryReadinessError("reconciliation must be a JSON object")
    result = _reconciliation_result(document)
    _validate_reconciliation_operator_binding(ledger, result)
    return result, evidence


def _governance_evidence(
    *, project_root: Path = PROJECT_ROOT
) -> GovernanceEvidence:
    docs_root = project_root / "docs"
    inventory_path = _safe_file(
        docs_root / "FREE_BASEBALL_DATA_SOURCE_INVENTORY.md",
        docs_root,
        "source inventory",
    )
    attribution_path = _safe_file(
        docs_root / "OPEN_DATA_ATTRIBUTION.md", docs_root, "attribution record"
    )
    inventory = inventory_path.read_text(encoding="utf-8")
    attribution = attribution_path.read_text(encoding="utf-8")
    for source_key in REQUIRED_SOURCE_KEYS:
        if source_key not in inventory or source_key not in attribution:
            raise FD1RecoveryReadinessError(
                f"{source_key}: governance or attribution record is absent"
            )
    return GovernanceEvidence(
        inventory=_artifact(inventory_path, project_root=project_root),
        attribution=_artifact(attribution_path, project_root=project_root),
        authorization_recorded_source_keys=REQUIRED_SOURCE_KEYS,
        use_classification_recorded_source_keys=REQUIRED_SOURCE_KEYS,
        license_recorded_source_keys=REQUIRED_SOURCE_KEYS,
        attribution_required_source_keys=REQUIRED_SOURCE_KEYS,
        attribution_recorded_source_keys=REQUIRED_SOURCE_KEYS,
    )


def _validate_full_manifest(manifest: RecoveryManifest) -> None:
    if (
        manifest.aggregate.file_count != EXPECTED_FULL_FILE_COUNT
        or manifest.aggregate.row_count != EXPECTED_FULL_ROW_COUNT
        or manifest.aggregate.byte_count != EXPECTED_FULL_BYTE_COUNT
        or len(manifest.files) != EXPECTED_FULL_FILE_COUNT
    ):
        raise FD1RecoveryReadinessError(
            "readiness requires the exact complete 329-file FD1 manifest"
        )
    if manifest.generation_lineage.interrupted_execution.final_execute_evidence_present:
        raise FD1RecoveryReadinessError("manifest interruption boundary changed")


def _validate_recovery_result_binding(
    result: Mapping[str, Any], manifest: RecoveryManifest
) -> None:
    expected = {
        "run_id": manifest.recovery_id,
        "recovery_manifest_checksum": manifest.manifest_checksum,
        "generation_code_revision": manifest.generation_lineage.code_revision,
        "finalizer_code_revision": manifest.finalizer_lineage.code_revision,
        "files_reused": manifest.aggregate.file_count,
        "bytes_reused": manifest.aggregate.byte_count,
        "parquet_rows": manifest.aggregate.row_count,
        "mappings_staged": manifest.identity_spool.row_count,
        "finalized_partitions": manifest.aggregate.file_count,
    }
    if any(result.get(name) != value for name, value in expected.items()):
        raise FD1RecoveryReadinessError("FINALIZED recovery result differs from manifest")


def _validate_warehouse_checkpoint(
    validation: Mapping[str, Any],
    *,
    manifest: RecoveryManifest,
    identity: ActiveIdentityCoverageReport,
    identity_artifact: ArtifactEvidence,
) -> None:
    supplied = validation.get("validation_checksum")
    unsigned = dict(validation)
    unsigned.pop("validation_checksum", None)
    if supplied != _checksum(unsigned):
        raise FD1RecoveryReadinessError("warehouse validation checksum changed")
    expected = {
        "schema_version": WAREHOUSE_VALIDATION_SCHEMA_VERSION,
        "mode": "FULL",
        "recovery_id": manifest.recovery_id,
        "manifest_checksum": manifest.manifest_checksum,
        "passed": True,
        "publishable": True,
        "population_complete": True,
        "manifest_file_count": manifest.aggregate.file_count,
        "file_count": manifest.aggregate.file_count,
        "partitions": manifest.aggregate.file_count,
        "row_count": manifest.aggregate.row_count,
        "byte_count": manifest.aggregate.byte_count,
    }
    if any(validation.get(name) != value for name, value in expected.items()):
        raise FD1RecoveryReadinessError("warehouse validation is not complete/publishable")
    checks = _mapping_sequence(validation.get("checks"), "warehouse checks")
    if not checks or any(row.get("status") != "PASS" for row in checks):
        raise FD1RecoveryReadinessError("warehouse validation contains a skipped check")
    active = _mapping(validation.get("active_identity"), "validation identity binding")
    if (
        active.get("passes") is not True
        or active.get("artifact_sha256") != identity_artifact.sha256
        or active.get("logical_uri") != identity_artifact.logical_uri
        or active.get("report_derivation_checksum") != identity.derivation_checksum
        or active.get("schema_version") != identity.schema_version
    ):
        raise FD1RecoveryReadinessError(
            "warehouse validation is bound to different identity evidence"
        )

    grouped: defaultdict[tuple[str, int], list[RecoveryFileEntry]] = defaultdict(list)
    for entry in manifest.files:
        grouped[(entry.family, entry.season)].append(entry)
    expected_totals = {
        key: (
            len(entries),
            sum(entry.row_count for entry in entries),
            sum(entry.size_bytes for entry in entries),
            min(entry.min_date for entry in entries),
            max(entry.max_date for entry in entries),
        )
        for key, entries in grouped.items()
    }
    observed_totals = {
        (str(row["family"]), int(row["season"])): (
            int(row["file_count"]),
            int(row["row_count"]),
            int(row["byte_count"]),
            str(row["min_date"]),
            str(row["max_date"]),
        )
        for row in _mapping_sequence(
            validation.get("family_season_totals"), "family/season validation totals"
        )
    }
    if observed_totals != expected_totals:
        raise FD1RecoveryReadinessError("warehouse family/season totals changed")


def _statement_kind(statement: str) -> str:
    normalized = " ".join(statement.strip().split()).upper()
    if normalized == "SET TRANSACTION READ ONLY":
        return "CONTROL"
    if (
        normalized.startswith(("SELECT ", "WITH ", "SHOW "))
        and ";" not in normalized.rstrip(";")
        and _MUTATING_SQL.search(normalized) is None
    ):
        return "READ"
    return "REJECT"


def _verify_manifest_rows(
    rows: Sequence[Mapping[str, Any]], manifest: RecoveryManifest
) -> None:
    if len(rows) != len(manifest.files):
        raise FD1RecoveryReadinessError("persisted manifest-file count changed")
    by_uri = {str(row["relative_path"]): row for row in rows}
    if set(by_uri) != {entry.logical_uri for entry in manifest.files}:
        raise FD1RecoveryReadinessError("persisted manifest-file URI set changed")
    for ordinal, entry in enumerate(manifest.files, start=1):
        row = by_uri[entry.logical_uri]
        expected = {
            "ordinal": ordinal,
            "size_bytes": entry.size_bytes,
            "mtime_ns": entry.mtime_ns,
            "file_sha256": entry.sha256,
            "schema_version": entry.declared_schema,
            "schema_fingerprint": entry.physical_schema_fingerprint,
            "row_count": entry.row_count,
            "min_event_date": entry.min_date,
            "max_event_date": entry.max_date,
            "event_family": entry.family,
            "season": entry.season,
            "month": entry.month,
            "partition_identity_checksum": entry.partition_identity_checksum,
        }
        actual = {
            name: (
                value.isoformat()
                if isinstance((value := row[name]), (date, datetime))
                else value
            )
            for name in expected
        }
        if actual != expected:
            raise FD1RecoveryReadinessError(
                f"persisted manifest file changed: {entry.logical_uri}"
            )


def _publication_target(row: Mapping[str, Any]) -> PublicationTarget:
    return PublicationTarget(
        object_kind=str(row["object_kind"]),
        target_id=str(row["target_id"]),
        natural_key_checksum=str(row["natural_key_checksum"]),
        semantic_identity_checksum=str(row["semantic_identity_checksum"]),
        target_payload=MappingProxyType(dict(row["target_payload"])),
        payload_checksum=str(row["payload_checksum"]),
    )


def _artifact_from_partition_target(
    target: PublicationTarget, entry: RecoveryFileEntry
) -> HistoricalEventPartitionArtifact:
    payload = dict(target.target_payload)
    allowed = {
        "id",
        "source_version_id",
        "checkpoint_id",
        "event_family",
        "season",
        "month",
        "logical_uri",
        "row_count",
        "game_count",
        "plate_appearance_count",
        "player_count",
        "min_event_date",
        "max_event_date",
        "min_event_at",
        "max_event_at",
        "storage_format",
        "compression",
        "schema_version",
        "schema_hash",
        "file_sha256",
        "file_size_bytes",
        "raw_contributor_manifest",
        "raw_contributor_checksum",
        "code_revision",
        "evidence_tier",
        "timing_classification",
        "identity_checksum",
    }
    if set(payload) != allowed:
        raise FD1RecoveryReadinessError("partition target payload shape changed")
    if str(payload.pop("id")) != target.target_id:
        raise FD1RecoveryReadinessError("partition target ID changed")
    governed_source_key = (
        LAHMAN_SOURCE_KEY
        if entry.family == PLAYER_PRIOR_FAMILY
        else RETROSHEET_SOURCE_KEY
    )
    artifact = HistoricalEventPartitionArtifact(
        source_key=governed_source_key,
        reused=True,
        raw_contributor_manifest=tuple(
            dict(item)
            for item in _mapping_sequence(
                payload.pop("raw_contributor_manifest"), "partition contributors"
            )
        ),
        **payload,
    )
    if target.semantic_identity_checksum != artifact.identity_checksum:
        raise FD1RecoveryReadinessError("partition target identity changed")
    # The manifest partition identity binds the immutable path-governance
    # subset, while the artifact identity additionally binds row counts,
    # dates, contributors, and file evidence.  They are deliberately distinct
    # domains.  The checks below independently recompute both the governance
    # and full artifact identities, then compare every manifest-bound field.
    expected_declared_schema = (
        FD1_IDENTITY_SCHEMA_VERSION
        if artifact.event_family == PLAYER_PRIOR_FAMILY
        else MATERIALIZED_FAMILY_SCHEMA_VERSIONS[artifact.event_family]
    )
    if artifact.schema_version != expected_declared_schema:
        raise FD1RecoveryReadinessError("partition declared schema changed")
    governance = {
        "source_key": artifact.source_key,
        "source_version_id": artifact.source_version_id,
        "checkpoint_id": artifact.checkpoint_id,
        "event_family": artifact.event_family,
        "season": artifact.season,
        "month": artifact.month,
        "schema_version": artifact.schema_version,
        "schema_hash": artifact.schema_hash,
        "raw_contributor_checksum": artifact.raw_contributor_checksum,
        "code_revision": artifact.code_revision,
        "evidence_tier": artifact.evidence_tier,
        "timing_classification": artifact.timing_classification,
    }
    semantic = {
        **governance,
        "logical_uri": artifact.logical_uri,
        "row_count": artifact.row_count,
        "game_count": artifact.game_count,
        "plate_appearance_count": artifact.plate_appearance_count,
        "player_count": artifact.player_count,
        "min_event_date": artifact.min_event_date,
        "max_event_date": artifact.max_event_date,
        "min_event_at": artifact.min_event_at,
        "max_event_at": artifact.max_event_at,
        "storage_format": artifact.storage_format,
        "compression": artifact.compression,
        "file_sha256": artifact.file_sha256,
        "file_size_bytes": artifact.file_size_bytes,
        "raw_contributor_manifest": artifact.raw_contributor_manifest,
    }
    if (
        canonical_sha256(governance) != entry.governance_checksum
        or canonical_sha256(artifact.raw_contributor_manifest)
        != artifact.raw_contributor_checksum
        or canonical_sha256(semantic) != artifact.identity_checksum
    ):
        raise FD1RecoveryReadinessError("partition canonical identity changed")
    expected = {
        "source_version_id": entry.source_version_id,
        "checkpoint_id": entry.checkpoint_id,
        "event_family": entry.family,
        "season": entry.season,
        "month": entry.month,
        "logical_uri": entry.logical_uri,
        "row_count": entry.row_count,
        "schema_version": entry.declared_schema,
        "schema_hash": entry.physical_schema_fingerprint,
        "file_sha256": entry.sha256,
        "file_size_bytes": entry.size_bytes,
    }
    if any(getattr(artifact, name) != value for name, value in expected.items()):
        raise FD1RecoveryReadinessError("canonical partition differs from manifest")
    return artifact


def _verify_recovery_database(
    engine: Engine,
    *,
    manifest: RecoveryManifest,
    recovery_result: Mapping[str, Any],
) -> tuple[RecoveryDatabaseEvidence, tuple[HistoricalEventPartitionArtifact, ...], Mapping[str, Any]]:
    if engine.dialect.name != "postgresql":
        raise FD1RecoveryReadinessError("recovery readiness requires PostgreSQL")
    boundary = _DatabaseBoundary(backend=engine.dialect.name)

    def enforce_read_only_sql(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        kind = _statement_kind(statement)
        if kind == "READ":
            boundary.read_statements += 1
            return
        if kind == "CONTROL":
            return
        boundary.rejected_statements += 1
        raise FD1RecoveryReadinessError(
            "non-read SQL is prohibited during recovery readiness"
        )

    event.listen(engine, "before_cursor_execute", enforce_read_only_sql)
    connection = None
    transaction = None
    try:
        connection = engine.connect()
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        boundary.transaction_read_only = True
        run = connection.execute(
            select(FD1RecoveryRun.__table__).where(
                FD1RecoveryRun.__table__.c.id == UUID(manifest.recovery_id)
            )
        ).mappings().one_or_none()
        if run is None:
            raise FD1RecoveryReadinessError("FINALIZED recovery run is absent")
        expected_run = {
            "manifest_checksum": manifest.manifest_checksum,
            "generation_code_revision": manifest.generation_lineage.code_revision,
            "finalizer_code_revision": manifest.finalizer_lineage.code_revision,
            "identity_spool_sha256": manifest.identity_spool.sha256,
            "identity_spool_row_count": manifest.identity_spool.row_count,
            "status": "FINALIZED",
            "last_completed_stage": "FINALIZED",
            "active_stage": None,
            "provider_calls": 0,
        }
        if any(run[name] != value for name, value in expected_run.items()):
            raise FD1RecoveryReadinessError("recovery run is not exact FINALIZED evidence")
        if run["finalized_at"] is None or run["lease_owner"] is not None:
            raise FD1RecoveryReadinessError("recovery run finalization/lease state changed")
        if run["rehearsal_succeeded_at"] is None:
            raise FD1RecoveryReadinessError("FINALIZED recovery lacks rehearsal evidence")

        checkpoints = list(
            connection.execute(
                select(FD1RecoveryStageCheckpoint.__table__)
                .where(
                    FD1RecoveryStageCheckpoint.__table__.c.run_id
                    == UUID(manifest.recovery_id),
                    FD1RecoveryStageCheckpoint.__table__.c.scope == "FULL",
                    FD1RecoveryStageCheckpoint.__table__.c.status == "SUCCEEDED",
                )
                .order_by(FD1RecoveryStageCheckpoint.__table__.c.stage_sequence)
            ).mappings()
        )
        if (
            len(checkpoints) != len(RECOVERY_STAGES)
            or tuple(row["stage_name"] for row in checkpoints) != RECOVERY_STAGES
            or tuple(int(row["stage_sequence"]) for row in checkpoints)
            != tuple(range(1, len(RECOVERY_STAGES) + 1))
        ):
            raise FD1RecoveryReadinessError("six ordered FULL checkpoints are not durable")
        for row in checkpoints:
            progress = _mapping(row["progress"], f"{row['stage_name']} checkpoint output")
            if row["output_checksum"] != _checksum(progress):
                raise FD1RecoveryReadinessError(
                    f"{row['stage_name']} checkpoint output checksum changed"
                )
        validation = dict(
            _mapping(checkpoints[-2]["progress"], "warehouse validation checkpoint")
        )
        finalized = _mapping(checkpoints[-1]["progress"], "FINALIZED checkpoint")
        if (
            finalized.get("manifest_checksum") != manifest.manifest_checksum
            or finalized.get("validation_checksum") != _checksum(validation)
            or finalized.get("provider_calls") != 0
            or finalized.get("identity_promotion", {}).get("blocking_conflicts") != 0
        ):
            raise FD1RecoveryReadinessError("FINALIZED checkpoint lineage changed")
        if dict(_mapping(recovery_result.get("validation"), "recovery validation")) != validation:
            raise FD1RecoveryReadinessError(
                "recovery result is not bound to the FULL validation checkpoint"
            )

        manifest_rows = list(
            connection.execute(
                select(FD1RecoveryManifestFile.__table__)
                .where(
                    FD1RecoveryManifestFile.__table__.c.run_id
                    == UUID(manifest.recovery_id)
                )
                .order_by(FD1RecoveryManifestFile.__table__.c.ordinal)
            ).mappings()
        )
        _verify_manifest_rows(manifest_rows, manifest)

        stage_rows = list(
            connection.execute(
                select(FD1RecoveryPublicationStage.__table__)
                .where(
                    FD1RecoveryPublicationStage.__table__.c.run_id
                    == UUID(manifest.recovery_id),
                    FD1RecoveryPublicationStage.__table__.c.scope == "FULL",
                )
                .order_by(FD1RecoveryPublicationStage.__table__.c.ordinal)
            ).mappings()
        )
        if not stage_rows or [int(row["ordinal"]) for row in stage_rows] != list(
            range(1, len(stage_rows) + 1)
        ):
            raise FD1RecoveryReadinessError("publication stage is absent or noncontiguous")
        if any(
            row["action"] not in {"INSERT", "REUSE"} or row["promoted_at"] is None
            for row in stage_rows
        ):
            raise FD1RecoveryReadinessError("publication stage is not fully promoted")
        targets = tuple(_publication_target(row) for row in stage_rows)
        if any(
            canonical_sha256(target.target_payload) != target.payload_checksum
            for target in targets
        ):
            raise FD1RecoveryReadinessError("publication target payload checksum changed")
        classifications = _classify_targets(connection, targets)
        if any(action != "REUSE" for action, _matched, _reason in classifications):
            raise FD1RecoveryReadinessError("a canonical publication target changed")
        canonical_counts = Counter(target.object_kind for target in targets)
        performance = _mapping(recovery_result.get("performance"), "recovery performance")
        publication = _mapping(performance.get("publication"), "publication performance")
        expected_counts = {
            "PARTITION": int(publication.get("partitions", -1)),
            "CHECKPOINT": int(publication.get("checkpoints", -1)),
            "INGESTION_RUN": int(publication.get("ingestion_runs", -1)),
            "DERIVED_USE_DECISION": int(
                publication.get("derived_use_decisions", -1)
            ),
        }
        if (
            expected_counts["PARTITION"] != manifest.aggregate.file_count
            or dict(canonical_counts) != expected_counts
            or int(publication.get("targets", -1)) != len(targets)
            or publication.get("scope") != "FULL"
            or publication.get("canonical_rows_written") != 0
        ):
            raise FD1RecoveryReadinessError("canonical publication SQL counts changed")

        partition_targets = {
            str(target.target_payload.get("logical_uri")): target
            for target in targets
            if target.object_kind == "PARTITION"
        }
        entries = {entry.logical_uri: entry for entry in manifest.files}
        if set(partition_targets) != set(entries):
            raise FD1RecoveryReadinessError("canonical partition URI set changed")
        artifacts = tuple(
            _artifact_from_partition_target(partition_targets[entry.logical_uri], entry)
            for entry in manifest.files
        )

        outcomes = {
            str(row["outcome"]): int(row["row_count"])
            for row in connection.execute(
                select(
                    FD1RecoveryIdentityStage.__table__.c.outcome,
                    text("COUNT(*) AS row_count"),
                )
                .where(
                    FD1RecoveryIdentityStage.__table__.c.run_id
                    == UUID(manifest.recovery_id),
                    FD1RecoveryIdentityStage.__table__.c.scope == "FULL",
                )
                .group_by(FD1RecoveryIdentityStage.__table__.c.outcome)
            ).mappings()
        }
        if sum(outcomes.values()) != manifest.identity_spool.row_count:
            raise FD1RecoveryReadinessError("identity stage population changed")
        if outcomes.get("PENDING", 0) or outcomes.get("CONFLICT", 0):
            raise FD1RecoveryReadinessError("identity stage has pending/blocking conflicts")
        reason_rows = list(
            connection.execute(
                select(
                    FD1RecoveryIdentityStage.__table__.c.reason_code,
                    text("COUNT(*) AS row_count"),
                )
                .where(
                    FD1RecoveryIdentityStage.__table__.c.run_id
                    == UUID(manifest.recovery_id),
                    FD1RecoveryIdentityStage.__table__.c.scope == "FULL",
                    FD1RecoveryIdentityStage.__table__.c.outcome == "UNRESOLVED",
                )
                .group_by(FD1RecoveryIdentityStage.__table__.c.reason_code)
            ).mappings()
        )
        source_conflicts = sum(
            int(row["row_count"])
            for row in reason_rows
            if row["reason_code"] == "SOURCE_DECLARED_CONFLICT_QUARANTINED"
        )
        unresolved = outcomes.get("UNRESOLVED", 0) - source_conflicts
        if unresolved < 0:
            raise FD1RecoveryReadinessError("identity unresolved counts are inconsistent")
        expected_result_counts = {
            "mappings_inserted": outcomes.get("INSERT", 0),
            "mappings_reused": outcomes.get("REUSE", 0),
            "unresolved_mappings": unresolved,
            "source_declared_conflicts": source_conflicts,
        }
        if any(recovery_result.get(name) != value for name, value in expected_result_counts.items()):
            raise FD1RecoveryReadinessError("recovery identity result counts changed")

        quarantine_rows = int(
            connection.execute(
                select(text("COUNT(*)")).select_from(FD1RecoveryIdentityQuarantine.__table__).where(
                    FD1RecoveryIdentityQuarantine.__table__.c.run_id
                    == UUID(manifest.recovery_id),
                    FD1RecoveryIdentityQuarantine.__table__.c.scope == "FULL",
                )
            ).scalar_one()
        )
        if quarantine_rows != outcomes.get("UNRESOLVED", 0):
            raise FD1RecoveryReadinessError("identity quarantine population changed")
        resolved_mapping_rows = int(
            connection.execute(
                select(text("COUNT(*)"))
                .select_from(
                    FD1RecoveryIdentityStage.__table__.join(
                        OpenDataIdentityMapping.__table__,
                        OpenDataIdentityMapping.__table__.c.identity_checksum
                        == FD1RecoveryIdentityStage.__table__.c.identity_checksum,
                    )
                )
                .where(
                    FD1RecoveryIdentityStage.__table__.c.run_id
                    == UUID(manifest.recovery_id),
                    FD1RecoveryIdentityStage.__table__.c.scope == "FULL",
                    FD1RecoveryIdentityStage.__table__.c.outcome.in_(("INSERT", "REUSE")),
                )
            ).scalar_one()
        )
        expected_resolved = outcomes.get("INSERT", 0) + outcomes.get("REUSE", 0)
        if resolved_mapping_rows != expected_resolved:
            raise FD1RecoveryReadinessError("canonical identity mappings changed")

        canonical_partition_rows = list(
            connection.execute(
                select(
                    HistoricalEventPartition.__table__.c.identity_checksum,
                    HistoricalEventPartition.__table__.c.row_count,
                ).where(
                    HistoricalEventPartition.__table__.c.identity_checksum.in_(
                        tuple(artifact.identity_checksum for artifact in artifacts)
                    )
                )
            ).mappings()
        )
        if (
            len(canonical_partition_rows) != manifest.aggregate.file_count
            or sum(int(row["row_count"]) for row in canonical_partition_rows)
            != manifest.aggregate.row_count
        ):
            raise FD1RecoveryReadinessError("canonical partition SQL counts changed")
    finally:
        if transaction is not None and transaction.is_active:
            transaction.rollback()
        boundary.transaction_rolled_back = True
        if connection is not None:
            connection.close()
        event.remove(engine, "before_cursor_execute", enforce_read_only_sql)

    return (
        RecoveryDatabaseEvidence(
            backend=boundary.backend,
            transaction_read_only=boundary.transaction_read_only,
            transaction_rolled_back=boundary.transaction_rolled_back,
            read_statements=boundary.read_statements,
            rejected_statements=boundary.rejected_statements,
            checkpoint_count=len(checkpoints),
            checkpoint_output_checksums=MappingProxyType(
                {
                    str(row["stage_name"]): str(row["output_checksum"])
                    for row in checkpoints
                }
            ),
            canonical_counts=MappingProxyType(dict(sorted(canonical_counts.items()))),
            identity_outcomes=MappingProxyType(dict(sorted(outcomes.items()))),
            quarantine_rows=quarantine_rows,
            resolved_mapping_rows=resolved_mapping_rows,
        ),
        artifacts,
        validation,
    )


def _require_columns(frame: pl.DataFrame, names: Sequence[str], context: str) -> None:
    missing = tuple(name for name in names if name not in frame.columns)
    if missing:
        raise FD1RecoveryReadinessError(
            f"{context} is missing columns: {','.join(missing)}"
        )


def _all(frame: pl.DataFrame, expression: pl.Expr) -> bool:
    if frame.height == 0:
        return True
    return bool(frame.select(expression.fill_null(False).all()).item())


def _count(frame: pl.DataFrame, expression: pl.Expr) -> int:
    if frame.height == 0:
        return 0
    return int(frame.select(expression.fill_null(False).sum()).item())


def _values(frame: pl.DataFrame, column: str) -> set[str]:
    return {
        str(value)
        for value in frame.get_column(column).drop_nulls().unique().to_list()
    }


def _resolve_manifest_path(store_root: Path, entry: RecoveryFileEntry) -> Path:
    root = Path(store_root).resolve(strict=True)
    pure = PurePosixPath(entry.logical_uri)
    if pure.is_absolute() or ".." in pure.parts:
        raise FD1RecoveryReadinessError("manifest Parquet path is unsafe")
    candidate = root.joinpath(*pure.parts)
    try:
        selected = candidate.resolve(strict=True)
        selected.relative_to(root)
    except (OSError, ValueError) as exc:
        raise FD1RecoveryReadinessError("manifest Parquet escapes the store") from exc
    current = root
    for part in pure.parts:
        current = current / part
        if _is_linklike(current):
            raise FD1RecoveryReadinessError("manifest Parquet path contains a link")
    info = selected.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size != entry.size_bytes
        or info.st_mtime_ns != entry.mtime_ns
        or selected.name != f"part-{entry.sha256}.parquet"
    ):
        raise FD1RecoveryReadinessError("manifest Parquet stat/name binding changed")
    return selected


def _validate_common_rows(
    artifact: HistoricalEventPartitionArtifact, frame: pl.DataFrame
) -> int:
    family = artifact.event_family
    if family == PLAYER_PRIOR_FAMILY:
        _require_columns(
            frame,
            (
                "event_family",
                "season",
                "official_date",
                "population",
                "eligible_from_season",
                "same_season_predictor_eligible",
                "source_key",
                "source_logical_uris",
                "source_file_sha256s",
                "source_row_numbers",
                "source_row_sha256s",
                "source_derivation_checksum",
            ),
            family,
        )
        expected_population = (
            GEN2_PRIOR_ONLY_POPULATION
            if artifact.season in PRIOR_ONLY_SEASONS
            else GEN2_PRIMARY_POPULATION
        )
        if not (
            _all(frame, pl.col("event_family") == PLAYER_PRIOR_FAMILY)
            and _all(frame, pl.col("season") == artifact.season)
            and _all(frame, pl.col("population") == expected_population)
            and _all(frame, pl.col("eligible_from_season") == artifact.season + 1)
            and _all(frame, pl.col("same_season_predictor_eligible") == False)  # noqa: E712
            and _all(frame, pl.col("source_key") == LAHMAN_SOURCE_KEY)
            and _all(
                frame,
                pl.col("source_derivation_checksum").str.contains(r"^[0-9a-f]{64}$"),
            )
        ):
            raise FD1RecoveryReadinessError(
                "player-prior temporal/population lineage changed"
            )
        contributor_hashes = {
            str(value["file_sha256"]) for value in artifact.raw_contributor_manifest
        }
        expected_date = date(artifact.season, 12, 31).isoformat()
        for row in frame.select(
            "official_date",
            "source_logical_uris",
            "source_file_sha256s",
            "source_row_numbers",
            "source_row_sha256s",
        ).iter_rows(named=True):
            uris = row["source_logical_uris"]
            hashes = row["source_file_sha256s"]
            numbers = row["source_row_numbers"]
            row_hashes = row["source_row_sha256s"]
            if (
                str(row["official_date"]) != expected_date
                or not isinstance(uris, list)
                or not isinstance(hashes, list)
                or not isinstance(numbers, list)
                or not isinstance(row_hashes, list)
                or len(hashes) != 1
                or str(hashes[0]) not in contributor_hashes
                or not (len(uris) == len(numbers) == len(row_hashes))
                or not uris
                or any(int(value) < 2 for value in numbers)
                or any(not _SHA256.fullmatch(str(value)) for value in row_hashes)
                or any(
                    not isinstance(value, str)
                    or "\\" in value
                    or PurePosixPath(value).is_absolute()
                    or ".." in PurePosixPath(value).parts
                    for value in uris
                )
            ):
                raise FD1RecoveryReadinessError(
                    "player-prior source-row lineage changed"
                )
        return frame.height

    _require_columns(
        frame,
        (
            "source_key",
            "source_version_id",
            "population_category",
            "game_id",
            "game_date",
            "event_date",
            "season",
            "month",
            "final_available_date",
            "future_evidence_allowed",
            "available_only_after_game_final",
            "source_filename",
            "source_row_number",
            "source_row_checksum",
            "source_file_sha256",
            "row_checksum",
        ),
        family,
    )
    expected_population = (
        GEN2_PRIOR_ONLY_POPULATION
        if artifact.season in PRIOR_ONLY_SEASONS
        else GEN2_PRIMARY_POPULATION
    )
    contributor_hashes = {
        str(value["file_sha256"]) for value in artifact.raw_contributor_manifest
    }
    if not (
        _all(frame, pl.col("source_key") == RETROSHEET_SOURCE_KEY)
        and _all(frame, pl.col("source_version_id") == artifact.source_version_id)
        and _all(frame, pl.col("population_category") == expected_population)
        and _all(frame, pl.col("season") == artifact.season)
        and _all(frame, pl.col("month") == artifact.month)
        and _all(frame, pl.col("future_evidence_allowed") == False)  # noqa: E712
        and _all(frame, pl.col("available_only_after_game_final") == True)  # noqa: E712
        and _all(frame, pl.col("source_filename") == _SOURCE_FILENAME_BY_FAMILY[family])
        and _all(frame, pl.col("source_row_number") >= 2)
        and _all(frame, pl.col("source_row_checksum").str.contains(r"^[0-9a-f]{64}$"))
        and _all(frame, pl.col("source_file_sha256").is_in(sorted(contributor_hashes)))
        and _all(frame, pl.col("row_checksum").str.contains(r"^[0-9a-f]{64}$"))
    ):
        raise FD1RecoveryReadinessError(f"{family} row/checksum lineage changed")
    for game_id in _values(frame, "game_id"):
        if not _SOURCE_GAME_ID.fullmatch(game_id):
            raise FD1RecoveryReadinessError(f"{family} has a noncanonical game ID")
    for raw_date in frame.get_column("game_date").unique().to_list():
        parsed = date.fromisoformat(str(raw_date))
        if parsed.year != artifact.season or parsed.month != artifact.month:
            raise FD1RecoveryReadinessError(f"{family} game date escapes partition")
    for game_value, final_value in frame.select(
        "game_date", "final_available_date"
    ).unique().iter_rows():
        game_date = date.fromisoformat(str(game_value))
        final_date = date.fromisoformat(str(final_value))
        if final_date < game_date or final_date >= KNOWLEDGE_CUTOFF:
            raise FD1RecoveryReadinessError(f"{family} final availability leaks")
    return frame.height


def _classify_tiers(
    artifact: HistoricalEventPartitionArtifact,
    frame: pl.DataFrame,
    season: _SeasonAccumulator,
) -> None:
    rows = frame.height
    season.tier_candidate_rows += rows
    if artifact.season in PRIOR_ONLY_SEASONS:
        season.prior_only_rows += rows
        return
    family = artifact.event_family
    if family in {"play_transition", "starter_usage", "bullpen_usage", "weather"}:
        season.enhanced_rows += rows
    elif family == "player_game_batting":
        enhanced = _count(frame, pl.col("actual_starting_lineup") == True)  # noqa: E712
        season.enhanced_rows += enhanced
        season.strict_rows += rows - enhanced
    elif family == "player_game_pitching":
        enhanced = _count(frame, pl.col("actual_start_flag") == 1)
        season.enhanced_rows += enhanced
        season.strict_rows += rows - enhanced
    else:
        season.strict_rows += rows


def _scan_plate_appearances(frame: pl.DataFrame, season: _SeasonAccumulator) -> None:
    _require_columns(
        frame, ("plate_appearance_id", "terminal_play_id", "outcome"), "plate appearance"
    )
    season.pa_games.update(_values(frame, "game_id"))
    season.pa_dates.update(
        date.fromisoformat(str(value))
        for value in frame.get_column("game_date").unique().to_list()
    )
    season.plate_appearances += frame.height
    season.taxonomy_terminal_events += frame.height
    if season.season in PRIMARY_SEASONS:
        season.primary_pa_rows += frame.height
    for outcome, count in frame.group_by("outcome").len().iter_rows():
        if outcome in PA_OUTCOME_NAMES:
            season.taxonomy_outcomes[str(outcome)] += int(count)
        else:
            invalid = frame.filter(pl.col("outcome") == outcome)
            season.unsafe_taxonomy_event_ids.update(_values(invalid, "terminal_play_id"))


def _scan_transitions(
    frame: pl.DataFrame,
    season: _SeasonAccumulator,
    cells: set[tuple[int, int, str]],
) -> None:
    _require_columns(
        frame,
        (
            "play_id",
            "play_number",
            "event_text",
            "outs_before",
            "outs_after",
            "base_mask_before",
            "base_mask_after",
            "visiting_score_before",
            "home_score_before",
            "visiting_score_after",
            "home_score_after",
            "runs_scored",
            "pa_outcome",
        ),
        "play transition",
    )
    terminal_np = (
        (pl.col("event_text") == "NP")
        & (pl.col("outs_before") == 3)
        & (pl.col("outs_after") == 3)
        & (pl.col("base_mask_before") == 0)
        & (pl.col("base_mask_after") == 0)
        & (pl.col("runs_scored") == 0)
    )
    valid = (
        (pl.col("outs_before").is_between(0, 2, closed="both") | terminal_np)
        & pl.col("outs_after").is_between(0, 3, closed="both")
        & pl.col("base_mask_before").is_between(0, 7, closed="both")
        & pl.col("base_mask_after").is_between(0, 7, closed="both")
        & (pl.col("visiting_score_before") >= 0)
        & (pl.col("home_score_before") >= 0)
        & (pl.col("visiting_score_after") >= pl.col("visiting_score_before"))
        & (pl.col("home_score_after") >= pl.col("home_score_before"))
        & (
            pl.col("runs_scored")
            == pl.col("visiting_score_after")
            + pl.col("home_score_after")
            - pl.col("visiting_score_before")
            - pl.col("home_score_before")
        )
    )
    if not _all(frame, valid):
        raise FD1RecoveryReadinessError("play-transition conservation failed")
    season.transition_games.update(_values(frame, "game_id"))
    season.transitions += frame.height
    terminal = frame.filter(pl.col("pa_outcome").is_not_null())
    for outs, mask, outcome in terminal.select(
        "outs_before", "base_mask_before", "pa_outcome"
    ).unique().iter_rows():
        if outcome not in PA_OUTCOME_NAMES:
            season.unsafe_taxonomy_event_ids.update(_values(terminal, "play_id"))
        elif season.season in PRIMARY_SEASONS:
            cells.add((int(outs), int(mask), str(outcome)))
    finals = (
        frame.sort(("game_id", "play_number"))
        .group_by("game_id", maintain_order=True)
        .agg(
            pl.col("play_number").last().alias("last_play"),
            pl.col("home_score_after").last().alias("home_score"),
            pl.col("visiting_score_after").last().alias("away_score"),
        )
    )
    for game_id, last_play, home_score, away_score in finals.iter_rows():
        value = (int(last_play), int(home_score), int(away_score))
        previous = season.final_scores.setdefault(str(game_id), value)
        if previous != value:
            raise FD1RecoveryReadinessError("one game has conflicting final states")


def _scan_batting(frame: pl.DataFrame, season: _SeasonAccumulator) -> None:
    _require_columns(
        frame,
        (
            "player_id",
            "team_id",
            "actual_starting_lineup",
            "actual_batting_order",
            "actual_fielding_positions",
            "actual_lineup_timing_risk",
            "actual_lineup_never_strict_pregame",
            "strict_same_game_pregame_eligible",
            "enhanced_research_eligible",
        ),
        "player-game batting",
    )
    starters = frame.filter(pl.col("actual_starting_lineup") == True)  # noqa: E712
    season.historical_actual_lineup_rows += starters.height
    isolated = starters.filter(
        (pl.col("actual_lineup_timing_risk") == "HISTORICAL_ACTUAL_LINEUP_TIMING_RISK")
        & (pl.col("actual_lineup_never_strict_pregame") == True)  # noqa: E712
        & (pl.col("strict_same_game_pregame_eligible") == False)  # noqa: E712
        & (pl.col("enhanced_research_eligible") == True)  # noqa: E712
    )
    season.isolated_enhanced_lineup_rows += isolated.height
    grouped = starters.group_by(("game_id", "team_id")).agg(
        pl.len().alias("rows"),
        pl.col("player_id").n_unique().alias("players"),
        pl.col("actual_batting_order").n_unique().alias("orders"),
        pl.col("actual_batting_order").min().alias("min_order"),
        pl.col("actual_batting_order").max().alias("max_order"),
        pl.col("actual_fielding_positions").list.len().min().alias("min_positions"),
    )
    for row in grouped.iter_rows(named=True):
        if (
            row["rows"],
            row["players"],
            row["orders"],
            row["min_order"],
            row["max_order"],
        ) == (9, 9, 9, 1, 9) and int(row["min_positions"] or 0) > 0:
            season.lineup_complete_teams[str(row["game_id"])].add(str(row["team_id"]))
    for game_id, team_id, count in isolated.group_by(("game_id", "team_id")).len().iter_rows():
        if int(count) == 9:
            season.lineup_isolated_teams[str(game_id)].add(str(team_id))


def _scan_usage(
    frame: pl.DataFrame,
    season: _SeasonAccumulator,
    *,
    starter: bool,
    contributor_hashes: set[str],
) -> int:
    _require_columns(
        frame,
        (
            "usage_id",
            "team_id",
            "appearance_sequence",
            "pitcher_role",
            "actual_starter_target",
            "participant_evidence",
            "timing_risk_label",
            "strict_same_game_pregame_eligible",
            "strict_prior_eligible_after_game_final",
            "enhanced_research_eligible",
            "manager_id",
            "retrosheet_manager_id",
            "manager_evidence_source_filename",
            "manager_evidence_source_row_number",
            "manager_evidence_source_row_checksum",
            "manager_evidence_source_file_sha256",
        ),
        "pitcher usage",
    )
    expected_role = "STARTER" if starter else "RELIEVER"
    expected_timing = (
        "HISTORICAL_ACTUAL_STARTER_TIMING_RISK"
        if starter
        else "HISTORICAL_ACTUAL_RELIEVER_USAGE_TIMING_RISK"
    )
    expected_evidence = (
        "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION"
        if starter
        else "ACTUAL_RELIEVER_HISTORICAL_RECONSTRUCTION"
    )
    if not (
        _all(frame, pl.col("pitcher_role") == expected_role)
        and _all(frame, pl.col("actual_starter_target") == starter)
        and _all(frame, pl.col("participant_evidence") == expected_evidence)
        and _all(frame, pl.col("timing_risk_label") == expected_timing)
        and _all(frame, pl.col("strict_same_game_pregame_eligible") == False)  # noqa: E712
        and _all(frame, pl.col("strict_prior_eligible_after_game_final") == True)  # noqa: E712
        and _all(frame, pl.col("enhanced_research_eligible") == True)  # noqa: E712
    ):
        raise FD1RecoveryReadinessError("pitcher-usage timing/role semantics changed")
    manager_lineage = (
        pl.col("manager_id").is_not_null()
        & pl.col("retrosheet_manager_id").is_not_null()
        & (pl.col("manager_evidence_source_filename") == "teamstats.csv")
        & (pl.col("manager_evidence_source_row_number") >= 2)
        & pl.col("manager_evidence_source_row_checksum").str.contains(r"^[0-9a-f]{64}$")
        & pl.col("manager_evidence_source_file_sha256").str.contains(r"^[0-9a-f]{64}$")
        & pl.col("manager_evidence_source_file_sha256").is_in(sorted(contributor_hashes))
    )
    populated = _count(frame, pl.col("manager_id").is_not_null())
    lineage_count = _count(frame, manager_lineage)
    season.manager_usage_rows += frame.height
    season.manager_populated_usage_rows += populated
    season.manager_lineage_rows += lineage_count
    season.manager_ids.update(_values(frame, "manager_id"))
    for manager_id in _values(frame, "manager_id"):
        if not _SOURCE_PERSON_ID.fullmatch(manager_id):
            raise FD1RecoveryReadinessError("pitcher usage has invalid manager identity")
    for game_id, team_id, manager_id, raw_manager_id, sequence in frame.select(
        "game_id",
        "team_id",
        "manager_id",
        "retrosheet_manager_id",
        "appearance_sequence",
    ).iter_rows():
        if manager_id != (
            f"retrosheet:player:{raw_manager_id}" if raw_manager_id else None
        ):
            raise FD1RecoveryReadinessError("manager normalized identity changed")
        if manager_id is not None:
            season.manager_pairs[str(game_id)].add((str(team_id), str(manager_id)))
        season.usage_sequences[(str(game_id), str(team_id))].add(int(sequence))
        if starter:
            season.starter_teams[str(game_id)].add(str(team_id))
            season.starter_labeled_teams[str(game_id)].add(str(team_id))
    if starter:
        season.historical_actual_starter_rows += frame.height
        season.isolated_enhanced_starter_rows += frame.height
        if "stat_p_ipouts" in frame.columns:
            season.starter_duration_rows += _count(
                frame,
                pl.col("stat_p_ipouts").is_not_null()
                & (pl.col("stat_p_ipouts") >= 0),
            )
    else:
        season.bullpen_rows += frame.height
    return lineage_count


def _scan_weather(frame: pl.DataFrame, season: _SeasonAccumulator) -> int:
    _require_columns(
        frame,
        (
            "weather_coverage",
            "weather_source",
            "noaa_gap_fill",
            "umpire_home_id",
            "umpire_first_base_id",
            "umpire_second_base_id",
            "umpire_third_base_id",
            "umpire_left_field_id",
            "umpire_right_field_id",
            "retrosheet_umpire_ids",
        ),
        "weather",
    )
    if not (
        _all(frame, pl.col("weather_source") == "RETROSHEET_OPEN_DATA")
        and _all(frame, pl.col("noaa_gap_fill") == False)  # noqa: E712
    ):
        raise FD1RecoveryReadinessError("weather source/gap-fill semantics changed")
    season.weather_games.update(_values(frame, "game_id"))
    complete = frame.filter(pl.col("weather_coverage") == "COMPLETE_RETROSHEET")
    season.fully_covered_weather_games.update(_values(complete, "game_id"))
    role_assignments = 0
    for row in frame.select(
        "game_id", "retrosheet_umpire_ids", *_UMPIRE_FIELDS.values()
    ).iter_rows(named=True):
        game_id = str(row["game_id"])
        raw_roles = row["retrosheet_umpire_ids"] or {}
        if not isinstance(raw_roles, Mapping):
            raise FD1RecoveryReadinessError("weather umpire evidence is malformed")
        for role, column in _UMPIRE_FIELDS.items():
            raw = raw_roles.get(role)
            normalized = row[column]
            token = str(raw).strip() if raw is not None else ""
            expected = None if token in {"", "(none)"} else f"retrosheet:player:{token}"
            if normalized != expected:
                raise FD1RecoveryReadinessError("umpire identity normalization changed")
            if normalized is not None:
                if not _SOURCE_PERSON_ID.fullmatch(str(normalized)):
                    raise FD1RecoveryReadinessError("weather has invalid umpire identity")
                season.umpire_ids.add(str(normalized))
                role_assignments += 1
        if row["umpire_home_id"] is not None:
            season.umpire_plate_games.add(game_id)
    season.umpire_assignments += role_assignments
    lineage_count = _count(
        frame,
        (pl.col("source_filename") == "gameinfo.csv")
        & (pl.col("source_row_number") >= 2)
        & pl.col("source_row_checksum").str.contains(r"^[0-9a-f]{64}$")
        & pl.col("source_file_sha256").str.contains(r"^[0-9a-f]{64}$"),
    )
    season.umpire_lineage_weather_rows += lineage_count
    return lineage_count


def _complete_usage_games(season: _SeasonAccumulator) -> set[str]:
    games: set[str] = set()
    for game_id in season.games:
        teams = {
            team_id
            for candidate_game, team_id in season.usage_sequences
            if candidate_game == game_id
        }
        if len(teams) != 2:
            continue
        valid = True
        for team_id in teams:
            sequence = sorted(season.usage_sequences[(game_id, team_id)])
            if not sequence or sequence != list(range(1, sequence[-1] + 1)):
                valid = False
                break
        if valid and season.starter_teams.get(game_id) == teams:
            games.add(game_id)
    return games


def _season_evidence(season: _SeasonAccumulator) -> FD1SeasonWarehouseEvidence:
    complete_lineups = {
        game_id
        for game_id, teams in season.lineup_complete_teams.items()
        if len(teams) == 2
    }
    isolated_lineups = {
        game_id
        for game_id, teams in season.lineup_isolated_teams.items()
        if len(teams) == 2 and game_id in complete_lineups
    }
    starter_games = {
        game_id for game_id, teams in season.starter_teams.items() if len(teams) == 2
    }
    labeled_starters = {
        game_id
        for game_id, teams in season.starter_labeled_teams.items()
        if len(teams) == 2 and game_id in starter_games
    }
    home_wins = sum(home > away for _, home, away in season.final_scores.values())
    away_wins = sum(away > home for _, home, away in season.final_scores.values())
    if any(home == away for _, home, away in season.final_scores.values()):
        raise FD1RecoveryReadinessError(f"{season.season}: final transitions contain ties")
    return FD1SeasonWarehouseEvidence(
        season=season.season,
        imported_regular_season_games=len(season.games),
        valid_final_games=len(season.final_scores),
        home_win_games=home_wins,
        away_win_games=away_wins,
        plate_appearance_games=len(season.pa_games),
        plate_appearances=season.plate_appearances,
        transition_games=len(season.transition_games),
        transitions=season.transitions,
        complete_lineup_games=len(complete_lineups),
        lineup_timing_risk_games=len(isolated_lineups),
        historical_actual_lineup_rows=season.historical_actual_lineup_rows,
        isolated_enhanced_lineup_rows=season.isolated_enhanced_lineup_rows,
        starter_games=len(starter_games),
        starter_historical_reconstruction_games=len(labeled_starters),
        starter_duration_rows=season.starter_duration_rows,
        historical_actual_starter_rows=season.historical_actual_starter_rows,
        isolated_enhanced_starter_rows=season.isolated_enhanced_starter_rows,
        bullpen_usage_games=len(_complete_usage_games(season)),
        bullpen_usage_rows=season.bullpen_rows,
        fielding_games=len(season.fielding_games),
        fielding_rows=season.fielding_rows,
        fully_covered_weather_games=len(season.fully_covered_weather_games),
        pitch_sequence_games=len(season.pitch_sequence_games),
        taxonomy_terminal_events=season.taxonomy_terminal_events,
        taxonomy_mapped_terminal_events=sum(season.taxonomy_outcomes.values()),
        taxonomy_outcome_counts=tuple(
            OutcomeCount(name, season.taxonomy_outcomes[name]) for name in PA_OUTCOME_NAMES
        ),
        tier_candidate_rows=season.tier_candidate_rows,
        tier_eligible_rows=season.strict_rows + season.enhanced_rows + season.prior_only_rows,
        strict_deployable_rows=season.strict_rows,
        enhanced_research_rows=season.enhanced_rows,
        prior_only_rows=season.prior_only_rows,
        unsafe_taxonomy_event_ids=tuple(sorted(season.unsafe_taxonomy_event_ids)),
    )


def _manager_evidence(season: _SeasonAccumulator) -> FD1ManagerCoverageEvidence:
    complete = {
        game_id
        for game_id, pairs in season.manager_pairs.items()
        if len({team for team, _manager in pairs}) == 2 and len(pairs) == 2
    }
    return FD1ManagerCoverageEvidence(
        season=season.season,
        regular_season_games=len(season.games),
        games_with_complete_manager_coverage=len(complete),
        manager_usage_rows=season.manager_usage_rows,
        manager_populated_usage_rows=season.manager_populated_usage_rows,
        teamstats_lineage_verified_usage_rows=season.manager_lineage_rows,
        unique_manager_ids=tuple(sorted(season.manager_ids)),
        missing_manager_game_ids=tuple(sorted(season.games - complete)),
    )


def _umpire_evidence(season: _SeasonAccumulator) -> FD1UmpireCoverageEvidence:
    return FD1UmpireCoverageEvidence(
        season=season.season,
        regular_season_games=len(season.games),
        weather_rows=len(season.weather_games),
        games_with_plate_umpire=len(season.umpire_plate_games),
        umpire_role_assignments=season.umpire_assignments,
        gameinfo_lineage_verified_weather_rows=season.umpire_lineage_weather_rows,
        unique_umpire_ids=tuple(sorted(season.umpire_ids)),
        missing_plate_umpire_game_ids=tuple(
            sorted(season.weather_games - season.umpire_plate_games)
        ),
    )


def _temporal_evidence(
    seasons: Mapping[int, _SeasonAccumulator]
) -> TemporalAuditSummary:
    for season in PRIMARY_SEASONS:
        if not seasons[season].pa_dates:
            raise FD1RecoveryReadinessError(f"{season}: temporal PA evidence is absent")
    return TemporalAuditSummary(
        chronology_checked_rows=sum(row.chronology_checked_rows for row in seasons.values()),
        folds=(
            TemporalFoldEvidence(
                fold_id="train-2023-validate-2024",
                training_end=max(seasons[2023].pa_dates),
                validation_start=min(seasons[2024].pa_dates),
                validation_end=max(seasons[2024].pa_dates),
                training_rows=seasons[2023].primary_pa_rows,
                validation_rows=seasons[2024].primary_pa_rows,
            ),
            TemporalFoldEvidence(
                fold_id="train-through-2024-validate-2025",
                training_end=max(seasons[2024].pa_dates),
                validation_start=min(seasons[2025].pa_dates),
                validation_end=max(seasons[2025].pa_dates),
                training_rows=seasons[2023].primary_pa_rows + seasons[2024].primary_pa_rows,
                validation_rows=seasons[2025].primary_pa_rows,
            ),
        ),
    )


def _verify_2025_scores(
    seasons: Mapping[int, _SeasonAccumulator], reconciliation: ReconciliationResult
) -> None:
    source_scores = {
        game.retrosheet.source_game_id: (
            game.retrosheet.home_score,
            game.retrosheet.away_score,
        )
        for game in reconciliation.games
    }
    if len(source_scores) != EXPECTED_REGULAR_SEASON_GAMES[2025]:
        raise FD1RecoveryReadinessError("2025 reconciliation score set is incomplete")
    for game_id, (_last, home, away) in seasons[2025].final_scores.items():
        if source_scores.get(game_id) != (home, away):
            raise FD1RecoveryReadinessError(
                "2025 Parquet final score differs from frozen reconciliation"
            )


def _scan_warehouse(
    manifest: RecoveryManifest,
    artifacts: Sequence[HistoricalEventPartitionArtifact],
    *,
    store_root: Path,
    ledger: Mapping[str, Any],
    identity: ActiveIdentityCoverageReport,
    reconciliation: ReconciliationResult,
    governance: GovernanceEvidence,
    progress_callback: Callable[[ReadinessScanProgress], None] | None = None,
) -> WarehouseScan:
    if len(artifacts) != len(manifest.files):
        raise FD1RecoveryReadinessError("canonical artifact count differs from manifest")
    seasons = {season: _SeasonAccumulator.create(season) for season in ACTIVE_SEASONS}
    transition_cells: set[tuple[int, int, str]] = set()
    partition_counts: Counter[str] = Counter()
    row_counts: Counter[str] = Counter()
    schema_versions: Counter[str] = Counter()
    lineage_rows = manager_lineage_rows = umpire_lineage_rows = 0
    rows_scanned = bytes_scanned = 0
    if progress_callback is not None:
        progress_callback(
            ReadinessScanProgress(
                files_scanned=0,
                files_total=len(manifest.files),
                rows_scanned=0,
                rows_total=manifest.aggregate.row_count,
                bytes_scanned=0,
                bytes_total=manifest.aggregate.byte_count,
                current_logical_uri=None,
            )
        )
    for index, (entry, artifact) in enumerate(
        zip(manifest.files, artifacts, strict=True), start=1
    ):
        path = _resolve_manifest_path(store_root, entry)
        try:
            actual_sha256 = _sha256_file(path)
        except OSError as exc:
            raise FD1RecoveryReadinessError(
                f"manifest Parquet is unreadable: {entry.logical_uri}"
            ) from exc
        if actual_sha256 != entry.sha256:
            raise FD1RecoveryReadinessError("manifest Parquet checksum changed")
        try:
            frame = pl.read_parquet(path)
        except Exception as exc:
            raise FD1RecoveryReadinessError(
                f"manifest Parquet is unreadable: {entry.logical_uri}"
            ) from exc
        if frame.height != entry.row_count:
            raise FD1RecoveryReadinessError("manifest Parquet row count changed")
        actual_schema = tuple((name, str(dtype)) for name, dtype in frame.schema.items())
        expected_schema = tuple((field.name, field.dtype) for field in entry.physical_schema)
        if actual_schema != expected_schema:
            raise FD1RecoveryReadinessError("manifest Parquet schema changed")
        lineage_rows += _validate_common_rows(artifact, frame)
        season = seasons[artifact.season]
        season.chronology_checked_rows += frame.height
        _classify_tiers(artifact, frame, season)
        family = artifact.event_family
        if family == "plate_appearance":
            _scan_plate_appearances(frame, season)
        elif family == "play_transition":
            _scan_transitions(frame, season, transition_cells)
        elif family == "pitch_sequence":
            season.pitch_sequence_games.update(_values(frame, "game_id"))
        elif family == "player_game_batting":
            _scan_batting(frame, season)
        elif family == "player_game_fielding":
            season.fielding_rows += frame.height
            season.fielding_games.update(_values(frame, "game_id"))
        elif family == "starter_usage":
            manager_lineage_rows += _scan_usage(
                frame,
                season,
                starter=True,
                contributor_hashes={
                    str(value["file_sha256"])
                    for value in artifact.raw_contributor_manifest
                },
            )
        elif family == "bullpen_usage":
            manager_lineage_rows += _scan_usage(
                frame,
                season,
                starter=False,
                contributor_hashes={
                    str(value["file_sha256"])
                    for value in artifact.raw_contributor_manifest
                },
            )
        elif family == "weather":
            umpire_lineage_rows += _scan_weather(frame, season)
            season.games.update(_values(frame, "game_id"))
        elif family not in {"player_game_pitching", PLAYER_PRIOR_FAMILY}:
            raise FD1RecoveryReadinessError("manifest contains an unknown FD1 family")
        partition_counts[family] += 1
        row_counts[family] += frame.height
        schema_versions[artifact.schema_version] += 1
        rows_scanned += frame.height
        bytes_scanned += entry.size_bytes
        del frame
        if progress_callback is not None:
            progress_callback(
                ReadinessScanProgress(
                    files_scanned=index,
                    files_total=len(manifest.files),
                    rows_scanned=rows_scanned,
                    rows_total=manifest.aggregate.row_count,
                    bytes_scanned=bytes_scanned,
                    bytes_total=manifest.aggregate.byte_count,
                    current_logical_uri=entry.logical_uri,
                )
            )
    if (
        rows_scanned != manifest.aggregate.row_count
        or bytes_scanned != manifest.aggregate.byte_count
    ):
        raise FD1RecoveryReadinessError("streamed Parquet population did not reconcile")
    _verify_2025_scores(seasons, reconciliation)
    warehouse = FD1WarehouseCloseoutEvidence(
        seasons=tuple(_season_evidence(seasons[season]) for season in ACTIVE_SEASONS),
        manager_coverage=tuple(
            _manager_evidence(seasons[season]) for season in ACTIVE_SEASONS
        ),
        umpire_coverage=tuple(
            _umpire_evidence(seasons[season]) for season in ACTIVE_SEASONS
        ),
        temporal=_temporal_evidence(seasons),
        transition_state_outcome_cells=len(transition_cells),
        operator_ledger_checksum=_checksum(ledger),
        identity_evidence_checksum=identity.derivation_checksum,
        reconciliation_result_checksum=_checksum(
            reconciliation.as_dict(include_games=True)
        ),
        authorization_recorded_source_keys=governance.authorization_recorded_source_keys,
        use_classification_recorded_source_keys=(
            governance.use_classification_recorded_source_keys
        ),
        license_recorded_source_keys=governance.license_recorded_source_keys,
        attribution_required_source_keys=governance.attribution_required_source_keys,
        attribution_recorded_source_keys=governance.attribution_recorded_source_keys,
    )
    return WarehouseScan(
        evidence=warehouse,
        partition_counts=MappingProxyType(dict(sorted(partition_counts.items()))),
        row_counts=MappingProxyType(dict(sorted(row_counts.items()))),
        schema_version_counts=MappingProxyType(dict(sorted(schema_versions.items()))),
        file_bytes=manifest.aggregate.byte_count,
        validated_partition_count=len(manifest.files),
        validated_row_count=rows_scanned,
        row_lineage_checked_count=lineage_rows,
        manager_lineage_checked_count=manager_lineage_rows,
        umpire_lineage_checked_count=umpire_lineage_rows,
    )


def _project_readiness_input(
    ledger: Mapping[str, Any],
    warehouse: FD1WarehouseCloseoutEvidence,
    identity: ActiveIdentityCoverageReport,
    reconciliation: ReconciliationResult,
) -> tuple[FD1ReadinessInput, FD1ReadinessResult]:
    if warehouse.operator_ledger_checksum != _checksum(ledger):
        raise FD1RecoveryReadinessError("warehouse is bound to a different ledger")
    if warehouse.identity_evidence_checksum != identity.derivation_checksum:
        raise FD1RecoveryReadinessError("warehouse is bound to different identity evidence")
    if warehouse.reconciliation_result_checksum != _checksum(
        reconciliation.as_dict(include_games=True)
    ):
        raise FD1RecoveryReadinessError("warehouse is bound to another reconciliation")
    _validate_active_identity_summary(identity, ledger)
    _validate_identity_conflict_projection(identity, ())
    sources = _source_summaries(ledger, warehouse)
    priors_by_season = _prior_rows_by_season(ledger)
    parks_by_season = _park_rows_by_target_season(ledger)
    seasons = tuple(
        _season_summary(
            row,
            identity,
            player_prior_rows=priors_by_season[row.season],
            park_factor_rows=parks_by_season.get(row.season, 0),
        )
        for row in warehouse.seasons
    )
    population = PopulationSeparationSummary(
        tier_candidate_row_count=sum(row.tier_candidate_rows for row in seasons),
        tier_eligible_row_count=sum(row.tier_eligible_rows for row in seasons),
        strict_row_count=sum(row.strict_deployable_rows for row in seasons),
        enhanced_row_count=sum(row.enhanced_research_rows for row in seasons),
        prior_only_row_count=sum(row.prior_only_rows for row in seasons),
        historical_actual_lineup_rows=sum(
            row.historical_actual_lineup_rows for row in warehouse.seasons
        ),
        isolated_enhanced_lineup_rows=sum(
            row.isolated_enhanced_lineup_rows for row in warehouse.seasons
        ),
        historical_actual_starter_rows=sum(
            row.historical_actual_starter_rows for row in warehouse.seasons
        ),
        isolated_enhanced_starter_rows=sum(
            row.isolated_enhanced_starter_rows for row in warehouse.seasons
        ),
        leakage=warehouse.tier_leakage,
    )
    primary = tuple(row for row in seasons if row.season in PRIMARY_SEASONS)
    training_outcomes: Counter[str] = Counter()
    for row in primary:
        training_outcomes.update(
            {item.outcome: item.count for item in row.taxonomy_outcome_counts}
        )
    training = TrainingEligibilitySummary(
        event_training_rows=sum(row.taxonomy_mapped_terminal_events for row in primary),
        event_outcome_counts=tuple(
            OutcomeCount(name, training_outcomes[name]) for name in PA_OUTCOME_NAMES
        ),
        transition_training_rows=sum(row.transitions for row in primary),
        transition_state_outcome_cells=warehouse.transition_state_outcome_cells,
        simulator_validation_games=sum(row.valid_final_games for row in primary),
        starter_duration_rows=sum(row.starter_duration_rows for row in primary),
        bullpen_usage_rows=sum(row.bullpen_usage_rows for row in primary),
        chronological_player_prior_rows=sum(
            priors_by_season[season] for season in (2022, 2023, 2024)
        ),
        prior_period_park_factor_rows=sum(parks_by_season.values()),
    )
    evidence = FD1ReadinessInput(
        sources=sources,
        seasons=seasons,
        asp_2025=_asp_summary(reconciliation),
        population_separation=population,
        temporal=warehouse.temporal,
        training=training,
        conservation_conflicts=warehouse.conservation_conflicts,
    )
    # Keep the exact pure audit as the only terminal decision authority.
    result = audit_fd1_readiness(evidence)
    return evidence, result


def _readiness_revision() -> str:
    digest = sha256()
    for logical_uri in sorted(FD1_RECOVERY_READINESS_CODE_REVISION_INPUTS):
        path = _safe_file(
            PROJECT_ROOT.joinpath(*PurePosixPath(logical_uri).parts),
            PROJECT_ROOT,
            logical_uri,
        )
        payload = path.read_bytes()
        digest.update(logical_uri.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _publish_json(
    document: Mapping[str, Any], *, artifact_root: Path = DEFAULT_ARTIFACT_ROOT
) -> tuple[ArtifactEvidence, bool]:
    artifact_root.mkdir(parents=True, exist_ok=True)
    if _is_linklike(artifact_root):
        raise FD1RecoveryReadinessError("readiness artifact root is linked")
    root = artifact_root.resolve(strict=True)
    payload = json.dumps(
        _plain(document), indent=2, sort_keys=True, ensure_ascii=False
    ).encode("utf-8") + b"\n"
    digest = sha256(payload).hexdigest()
    target = root / f"fd1-recovery-readiness-sha256-{digest}.json"
    pending: Path | None = None
    reused = False
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=".fd1-recovery-readiness-",
        suffix=".pending",
        dir=root,
        delete=False,
    )
    try:
        pending = Path(handle.name)
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if _is_linklike(pending) or pending.parent.resolve(strict=True) != root:
            raise FD1RecoveryReadinessError("readiness staging path is unsafe")
        if target.exists() or target.is_symlink():
            if _is_linklike(target) or target.read_bytes() != payload:
                raise FD1RecoveryReadinessError(
                    "existing readiness evidence has conflicting bytes"
                )
            reused = True
        else:
            try:
                os.link(pending, target)
            except FileExistsError:
                if _is_linklike(target) or target.read_bytes() != payload:
                    raise FD1RecoveryReadinessError(
                        "readiness evidence publication collided"
                    )
                reused = True
        if _sha256_file(target) != digest or target.stat().st_size != len(payload):
            raise FD1RecoveryReadinessError("published readiness evidence changed")
        logical_uri = (
            target.relative_to(PROJECT_ROOT).as_posix()
            if PROJECT_ROOT == target or PROJECT_ROOT in target.parents
            else target.as_posix()
        )
        return (
            ArtifactEvidence(
                logical_uri=logical_uri,
                sha256=digest,
                byte_count=len(payload),
            ),
            reused,
        )
    finally:
        if pending is not None and pending.exists() and not _is_linklike(pending):
            pending.unlink()


def run_fd1_recovery_readiness(
    engine: Engine,
    *,
    manifest_path: Path,
    recovery_result_path: Path,
    active_identity_artifact_path: Path,
    store_root: Path = DEFAULT_STORE_ROOT,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    project_root: Path = PROJECT_ROOT,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    progress_callback: Callable[[ReadinessScanProgress], None] | None = None,
) -> FD1RecoveryReadinessResult:
    """Produce the terminal readiness decision from finalized recovery evidence."""

    manifest_file = _safe_file(
        manifest_path, runtime_root / "recovery", "recovery manifest"
    )
    manifest = load_recovery_manifest(manifest_file)
    _validate_full_manifest(manifest)
    recovery_result, recovery_result_artifact = _load_recovery_result(
        recovery_result_path,
        runtime_root=runtime_root,
        project_root=project_root,
    )
    _validate_recovery_result_binding(recovery_result, manifest)
    ledger, ledger_artifact = _load_generation_ledger(
        manifest, project_root=project_root
    )
    identity, identity_artifact, _identity_document = _load_identity_artifact(
        active_identity_artifact_path,
        runtime_root=runtime_root,
        project_root=project_root,
    )
    _validate_active_identity_summary(identity, ledger)
    reconciliation, reconciliation_artifact = _load_reconciliation(
        ledger,
        project_root=project_root,
        runtime_root=runtime_root,
    )
    governance = _governance_evidence(project_root=project_root)
    database, partition_artifacts, validation = _verify_recovery_database(
        engine,
        manifest=manifest,
        recovery_result=recovery_result,
    )
    if (
        not database.transaction_read_only
        or not database.transaction_rolled_back
        or database.rejected_statements
    ):
        raise FD1RecoveryReadinessError("readiness SQL boundary is not clean")
    _validate_warehouse_checkpoint(
        validation,
        manifest=manifest,
        identity=identity,
        identity_artifact=identity_artifact,
    )
    scan = _scan_warehouse(
        manifest,
        partition_artifacts,
        store_root=store_root,
        ledger=ledger,
        identity=identity,
        reconciliation=reconciliation,
        governance=governance,
        progress_callback=progress_callback,
    )
    readiness_input, readiness_result = _project_readiness_input(
        ledger, scan.evidence, identity, reconciliation
    )
    readiness_result_payload = readiness_result.as_dict()
    result_checksum = _checksum(readiness_result_payload)
    document = {
        "schema_version": FD1_RECOVERY_READINESS_SCHEMA_VERSION,
        "operator_revision": _readiness_revision(),
        "decision": readiness_result.decision.value,
        "modeling_can_proceed": readiness_result.modeling_can_proceed,
        "terminal_decision_checksum": result_checksum,
        "recovery": {
            "recovery_id": manifest.recovery_id,
            "manifest_checksum": manifest.manifest_checksum,
            "generation_code_revision": manifest.generation_lineage.code_revision,
            "finalizer_code_revision": manifest.finalizer_lineage.code_revision,
            "result": asdict(recovery_result_artifact),
            "result_checksum": recovery_result["result_checksum"],
            "status": recovery_result["status"],
            "full_checkpoints": list(RECOVERY_STAGES),
            "warehouse_validation_checksum": validation["validation_checksum"],
            "active_identity_derivation_checksum": identity.derivation_checksum,
            "full_2025_reconciliation_checksum": reconciliation_artifact.sha256,
        },
        "source_artifacts": {
            "recovery_manifest": {
                "logical_uri": _logical(manifest_file, project_root=project_root),
                "manifest_checksum": manifest.manifest_checksum,
            },
            "trusted_dry_run_ledger": asdict(ledger_artifact),
            "active_identity": asdict(identity_artifact),
            "full_2025_reconciliation": asdict(reconciliation_artifact),
            "source_inventory": asdict(governance.inventory),
            "attribution": asdict(governance.attribution),
        },
        "execution_boundary": {
            "database": _plain(database),
            "provider_calls": 0,
            "network_requests": 0,
            "provider_transports_constructed": 0,
            "database_writes": 0,
            "governed_parquet_writes": 0,
            "raw_source_scans": 0,
            "manifest_list_only": True,
        },
        "population": {
            "partition_count": scan.validated_partition_count,
            "row_count": scan.validated_row_count,
            "file_bytes": scan.file_bytes,
            "partitions_by_family": dict(scan.partition_counts),
            "rows_by_family": dict(scan.row_counts),
            "schema_versions": dict(scan.schema_version_counts),
            "row_lineage_checked_count": scan.row_lineage_checked_count,
            "manager_lineage_checked_count": scan.manager_lineage_checked_count,
            "umpire_lineage_checked_count": scan.umpire_lineage_checked_count,
            "tier_projection_policy": TIER_PROJECTION_POLICY,
        },
        "warehouse_evidence": scan.evidence.as_dict(),
        "readiness_input": _plain(readiness_input),
        "readiness_result": readiness_result_payload,
    }
    output, reused = _publish_json(document, artifact_root=artifact_root)
    return FD1RecoveryReadinessResult(
        decision=readiness_result.decision.value,
        modeling_can_proceed=readiness_result.modeling_can_proceed,
        artifact=output,
        artifact_reused=reused,
        recovery_id=manifest.recovery_id,
        manifest_checksum=manifest.manifest_checksum,
        warehouse_derivation_checksum=scan.evidence.derivation_checksum,
        readiness_result_checksum=result_checksum,
        partition_count=scan.validated_partition_count,
        row_count=scan.validated_row_count,
        file_bytes=scan.file_bytes,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--recovery-result", type=Path, required=True)
    parser.add_argument("--active-identity", type=Path, required=True)
    parser.add_argument("--store-root", type=Path, default=DEFAULT_STORE_ROOT)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    return parser


def _sanitized_error(exc: BaseException) -> str:
    value = " ".join(str(exc).split())
    value = value.replace(str(PROJECT_ROOT.resolve()), "<project-root>")
    return value[:1000] or "recovery readiness closeout failed"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from app.db.session import engine

    def progress(row: ReadinessScanProgress) -> None:
        print(
            json.dumps(
                {"event": "fd1_recovery_readiness_progress", **asdict(row)},
                sort_keys=True,
            ),
            flush=True,
        )

    try:
        result = run_fd1_recovery_readiness(
            engine,
            manifest_path=args.manifest,
            recovery_result_path=args.recovery_result,
            active_identity_artifact_path=args.active_identity,
            store_root=args.store_root,
            artifact_root=args.artifact_root,
            progress_callback=progress,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "ready": False,
                    "error_type": type(exc).__name__,
                    "error": _sanitized_error(exc),
                    "provider_calls": 0,
                    "network_requests": 0,
                    "database_writes": 0,
                    "governed_parquet_writes": 0,
                    "raw_source_scans": 0,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps({"success": True, **result.as_dict()}, sort_keys=True))
    return (
        0
        if result.decision
        != ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE.value
        else 2
    )


__all__ = [
    "DEFAULT_ARTIFACT_ROOT",
    "DEFAULT_STORE_ROOT",
    "FD1_RECOVERY_READINESS_CODE_REVISION_INPUTS",
    "FD1_RECOVERY_READINESS_SCHEMA_VERSION",
    "FD1RecoveryReadinessError",
    "FD1RecoveryReadinessResult",
    "ReadinessScanProgress",
    "run_fd1_recovery_readiness",
]


if __name__ == "__main__":
    raise SystemExit(main())
