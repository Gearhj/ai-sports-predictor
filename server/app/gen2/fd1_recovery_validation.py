"""Bounded, file-list-driven validation for Gate 3C-FD1 recovery.

The validator consumes only the immutable files named by a
``RecoveryManifest``.  It never discovers source data, invokes a provider, or
writes Parquet.  Full validation enforces the exact interrupted population;
rehearsal validation is explicitly non-publishable and bounded by file count.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal

import polars as pl

from app.gen2.fd1_recovery_manifest import (
    EXPECTED_DECLARED_SCHEMAS,
    FD1RecoveryIntegrityError,
    RecoveryFileEntry,
    RecoveryManifest,
)


WAREHOUSE_VALIDATION_SCHEMA_VERSION = "gate3c_fd1_warehouse_validation_v1"
FULL_VALIDATION_MODE = "FULL"
REHEARSAL_VALIDATION_MODE = "REHEARSAL"
EXPECTED_FULL_FILE_COUNT = 329
EXPECTED_FULL_ROW_COUNT = 3_738_096
EXPECTED_FULL_BYTE_COUNT = 337_421_353
EXPECTED_SEASONS = frozenset(range(2021, 2026))
DEFAULT_MAX_REHEARSAL_FILES = 32
DEFAULT_MAX_ROWS_PER_FILE = 100_000

EXPECTED_FULL_FAMILY_ROWS: Mapping[str, int] = MappingProxyType(
    {
        "bullpen_usage": 80_314,
        "pitch_sequence": 943_682,
        "plate_appearance": 913_349,
        "play_transition": 949_217,
        "player_game_batting": 356_498,
        "player_game_fielding": 332_159,
        "player_game_pitching": 104_610,
        "player_prior": 21_823,
        "starter_usage": 24_296,
        "weather": 12_148,
    }
)
EXPECTED_FULL_FAMILY_FILES: Mapping[str, int] = MappingProxyType(
    {
        family: 5 if family == "player_prior" else 36
        for family in EXPECTED_FULL_FAMILY_ROWS
    }
)
EXPECTED_PHYSICAL_COLUMN_COUNTS: Mapping[str, int] = MappingProxyType(
    {
        "bullpen_usage": 82,
        "pitch_sequence": 64,
        "plate_appearance": 69,
        "play_transition": 81,
        "player_game_batting": 78,
        "player_game_fielding": 61,
        "player_game_pitching": 71,
        "player_prior": 28,
        "starter_usage": 82,
        "weather": 71,
    }
)

PA_OUTCOMES = (
    "STRIKEOUT",
    "WALK_HBP_CI",
    "HOME_RUN",
    "SINGLE",
    "DOUBLE",
    "TRIPLE",
    "BIP_OUT",
    "OTHER_REACH",
)
RETROSHEET_PA_CROSSWALK: Mapping[str, str] = MappingProxyType(
    {
        "single": "SINGLE",
        "double": "DOUBLE",
        "triple": "TRIPLE",
        "hr": "HOME_RUN",
        "sh": "BIP_OUT",
        "sf": "BIP_OUT",
        "hbp": "WALK_HBP_CI",
        "walk": "WALK_HBP_CI",
        "k": "STRIKEOUT",
        "xi": "WALK_HBP_CI",
        "roe": "OTHER_REACH",
        "fc": "OTHER_REACH",
        "othout": "BIP_OUT",
    }
)
MATERIALIZED_PA_TAXONOMY_VERSION = "retrosheet_to_mlb_gen2_pa_v1"

_SHA256 = re.compile(r"[0-9a-f]{64}")
_PROHIBITED_EVIDENCE = r"(?i)(DEMO|FABRICAT|SYNTHETIC|SPORTSBOOK|BETTING|MARKET)"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

_RETRO_COMMON_REQUIRED = frozenset(
    {
        "source_key",
        "source_mode",
        "source_version_id",
        "evidence_mode",
        "population_category",
        "strict_population",
        "game_id",
        "game_date",
        "event_date",
        "season",
        "month",
        "final_available_date",
        "historical_reconstruction",
        "available_only_after_game_final",
        "future_evidence_allowed",
        "strict_same_game_pregame_eligible",
        "enhanced_research_eligible",
        "source_filename",
        "source_row_number",
        "source_row_checksum",
        "source_file_sha256",
        "row_checksum",
    }
)
_REQUIRED_BY_FAMILY: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "plate_appearance": _RETRO_COMMON_REQUIRED
        | {
            "plate_appearance_id",
            "source_terminal_token",
            "outcome",
            "taxonomy_version",
        },
        "play_transition": _RETRO_COMMON_REQUIRED
        | {
            "play_id",
            "play_number",
            "inning",
            "top_bottom",
            "batting_team_id",
            "visiting_team_id",
            "home_team_id",
            "outs_before",
            "outs_after",
            "base_mask_before",
            "base_mask_after",
            "runs_scored",
            "automatic_runner_placement_before",
        },
        "pitch_sequence": _RETRO_COMMON_REQUIRED
        | {"pitch_sequence_id", "play_id", "source_pitch_sequence"},
        "player_game_batting": _RETRO_COMMON_REQUIRED
        | {"player_game_stat_id", "player_id", "stattype", "stat_semantics"},
        "player_game_pitching": _RETRO_COMMON_REQUIRED
        | {"player_game_stat_id", "player_id", "stattype", "stat_semantics"},
        "player_game_fielding": _RETRO_COMMON_REQUIRED
        | {"player_game_stat_id", "player_id", "stattype", "stat_semantics"},
        "starter_usage": _RETRO_COMMON_REQUIRED
        | {"usage_id", "player_id", "pitcher_role", "timing_risk_label"},
        "bullpen_usage": _RETRO_COMMON_REQUIRED
        | {"usage_id", "player_id", "pitcher_role", "timing_risk_label"},
        "weather": _RETRO_COMMON_REQUIRED
        | {"weather_id", "weather_source", "timing_risk_label"},
        "player_prior": frozenset(
            {
                "event_family",
                "canonical_player_id",
                "identity_resolution_status",
                "season",
                "official_date",
                "eligible_from_season",
                "same_season_predictor_eligible",
                "source_key",
                "source_releases",
                "source_logical_uris",
                "source_derivation_checksum",
                "timing_classification",
            }
        ),
    }
)


class FD1WarehouseValidationError(FD1RecoveryIntegrityError):
    """A frozen recovery file or its collective semantics fail closed."""


@dataclass(frozen=True, slots=True)
class ActiveIdentityValidationBinding:
    """Checksummed substitute for rescanning the active raw identity population."""

    logical_uri: str
    artifact_sha256: str
    report_derivation_checksum: str
    schema_version: str
    primary_threshold: float
    primary_reference_rate: float
    primary_unique_rate: float
    affected_player_count: int
    conflict_reference_count: int

    def __post_init__(self) -> None:
        pure = PurePosixPath(self.logical_uri)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or not self.logical_uri.startswith(".runtime/gate3c-fd1/artifacts/")
        ):
            raise ValueError("Active-identity report URI is outside the ignored artifact root")
        _sha256(self.artifact_sha256, "active-identity artifact")
        _sha256(self.report_derivation_checksum, "active-identity derivation")
        if not pure.name.endswith(f"sha256-{self.artifact_sha256}.json"):
            raise ValueError("Active-identity artifact name is not content addressed")
        if not self.schema_version.strip():
            raise ValueError("Active-identity schema version is required")
        if not 0.0 < self.primary_threshold <= 1.0:
            raise ValueError("Active-identity threshold must be in (0, 1]")
        for name, value in (
            ("primary_reference_rate", self.primary_reference_rate),
            ("primary_unique_rate", self.primary_unique_rate),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.affected_player_count < 0 or self.conflict_reference_count < 0:
            raise ValueError("Active-identity counts cannot be negative")

    @property
    def passes(self) -> bool:
        return (
            self.primary_reference_rate >= self.primary_threshold
            and self.primary_unique_rate >= self.primary_threshold
            and self.affected_player_count == 0
            and self.conflict_reference_count == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "affected_player_count": self.affected_player_count,
            "artifact_sha256": self.artifact_sha256,
            "conflict_reference_count": self.conflict_reference_count,
            "logical_uri": self.logical_uri,
            "passes": self.passes,
            "primary_reference_rate": self.primary_reference_rate,
            "primary_threshold": self.primary_threshold,
            "primary_unique_rate": self.primary_unique_rate,
            "report_derivation_checksum": self.report_derivation_checksum,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class FamilySeasonValidationTotal:
    family: str
    season: int
    file_count: int
    row_count: int
    byte_count: int
    min_date: str
    max_date: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "byte_count": self.byte_count,
            "family": self.family,
            "file_count": self.file_count,
            "max_date": self.max_date,
            "min_date": self.min_date,
            "row_count": self.row_count,
            "season": self.season,
        }


@dataclass(frozen=True, slots=True)
class WarehouseValidationProgress:
    files_validated: int
    files_total: int
    rows_validated: int
    rows_total: int
    bytes_validated: int
    bytes_total: int
    current_logical_uri: str | None


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    name: str
    status: Literal["PASS", "SKIPPED"]
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"detail": self.detail, "name": self.name, "status": self.status}


@dataclass(frozen=True, slots=True)
class WarehouseValidationMetrics:
    row_checksum_rows: int
    unique_row_checksums: int
    prohibited_evidence_rows: int
    future_evidence_rows: int
    timing_risk_rows: int
    strict_same_game_eligible_rows: int
    timing_risk_strict_overlap_rows: int
    transition_rows: int
    transition_games: int
    transition_adjacencies_checked: int
    automatic_runner_placements: int

    def to_dict(self) -> dict[str, int]:
        return {
            "automatic_runner_placements": self.automatic_runner_placements,
            "future_evidence_rows": self.future_evidence_rows,
            "prohibited_evidence_rows": self.prohibited_evidence_rows,
            "row_checksum_rows": self.row_checksum_rows,
            "strict_same_game_eligible_rows": self.strict_same_game_eligible_rows,
            "timing_risk_rows": self.timing_risk_rows,
            "timing_risk_strict_overlap_rows": self.timing_risk_strict_overlap_rows,
            "transition_adjacencies_checked": self.transition_adjacencies_checked,
            "transition_games": self.transition_games,
            "transition_rows": self.transition_rows,
            "unique_row_checksums": self.unique_row_checksums,
        }


@dataclass(frozen=True, slots=True)
class WarehouseValidationResult:
    schema_version: str
    mode: Literal["FULL", "REHEARSAL"]
    recovery_id: str
    manifest_checksum: str
    passed: bool
    publishable: bool
    population_complete: bool
    manifest_file_count: int
    file_count: int
    row_count: int
    byte_count: int
    family_season_totals: tuple[FamilySeasonValidationTotal, ...]
    family_schema_fingerprints: Mapping[str, tuple[str, ...]]
    pa_taxonomy_counts: Mapping[str, int]
    metrics: WarehouseValidationMetrics
    active_identity: ActiveIdentityValidationBinding | None
    checks: tuple[ValidationCheck, ...]
    validation_checksum: str

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "active_identity": (
                self.active_identity.to_dict() if self.active_identity is not None else None
            ),
            "byte_count": self.byte_count,
            "checks": [item.to_dict() for item in self.checks],
            "family_schema_fingerprints": {
                family: list(values)
                for family, values in sorted(self.family_schema_fingerprints.items())
            },
            "family_season_totals": [
                item.to_dict() for item in self.family_season_totals
            ],
            "file_count": self.file_count,
            "manifest_checksum": self.manifest_checksum,
            "manifest_file_count": self.manifest_file_count,
            "metrics": self.metrics.to_dict(),
            "mode": self.mode,
            "pa_taxonomy_counts": dict(self.pa_taxonomy_counts),
            "partitions": self.file_count,
            "passed": self.passed,
            "population_complete": self.population_complete,
            "publishable": self.publishable,
            "recovery_id": self.recovery_id,
            "row_count": self.row_count,
            "schema_version": self.schema_version,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "validation_checksum": self.validation_checksum}

    def as_dict(self) -> dict[str, Any]:
        return self.to_dict()

    @property
    def partitions(self) -> int:
        return self.file_count


@dataclass(frozen=True, slots=True)
class _FileMetrics:
    row_checksum_rows: int = 0
    unique_row_checksums: int = 0
    prohibited_evidence_rows: int = 0
    future_evidence_rows: int = 0
    timing_risk_rows: int = 0
    strict_same_game_eligible_rows: int = 0
    timing_risk_strict_overlap_rows: int = 0


@dataclass(frozen=True, slots=True)
class _TransitionMetrics:
    rows: int = 0
    games: int = 0
    adjacencies_checked: int = 0
    automatic_runner_placements: int = 0


def validate_recovery_warehouse(
    manifest: RecoveryManifest,
    *,
    store_root: Path,
    trusted_ledger_path: Path,
    active_identity_report: ActiveIdentityValidationBinding | Path | None,
    full: bool,
    progress_callback: Callable[[WarehouseValidationProgress], None] | None = None,
    entries: Sequence[RecoveryFileEntry] | None = None,
    max_rehearsal_files: int = DEFAULT_MAX_REHEARSAL_FILES,
    max_rows_per_file: int = DEFAULT_MAX_ROWS_PER_FILE,
) -> WarehouseValidationResult:
    """Validate the frozen warehouse collectively without loading it collectively."""

    if not isinstance(manifest, RecoveryManifest):
        raise TypeError("manifest must be a RecoveryManifest")
    if not isinstance(full, bool):
        raise TypeError("full must be a bool")
    mode: Literal["FULL", "REHEARSAL"] = (
        FULL_VALIDATION_MODE if full else REHEARSAL_VALIDATION_MODE
    )
    if max_rehearsal_files <= 0 or max_rows_per_file <= 0:
        raise ValueError("validation bounds must be positive")
    _validate_trusted_ledger(manifest, trusted_ledger_path)
    active_identity = _coerce_active_identity(active_identity_report)
    selected = _select_entries(
        manifest,
        manifest.files if full else (entries or _representative_entries(manifest)),
    )
    if full:
        if selected != manifest.files:
            raise FD1WarehouseValidationError(
                "FULL validation requires the manifest's complete explicit file list"
            )
        _validate_full_population(manifest)
        if active_identity is None or not active_identity.passes:
            raise FD1WarehouseValidationError(
                "FULL validation requires passing checksummed active-identity evidence"
            )
    elif len(selected) > max_rehearsal_files:
        raise FD1WarehouseValidationError(
            "REHEARSAL file selection exceeds its explicit bound"
        )

    _validate_schema_contract(manifest.files, full=full)
    root = _validated_root(store_root)
    bound_paths = {
        entry.logical_uri: _validate_file_binding(root, entry)
        for entry in manifest.files
    }
    total_rows = sum(entry.row_count for entry in selected)
    total_bytes = sum(entry.size_bytes for entry in selected)
    if progress_callback is not None:
        progress_callback(
            WarehouseValidationProgress(
                0, len(selected), 0, total_rows, 0, total_bytes, None
            )
        )

    file_totals: defaultdict[tuple[str, int], list[RecoveryFileEntry]] = defaultdict(list)
    schema_fingerprints: defaultdict[str, set[str]] = defaultdict(set)
    taxonomy: Counter[str] = Counter()
    token_pairs: Counter[tuple[str, str]] = Counter()
    file_metrics = Counter()
    transition_metrics = Counter()
    rows_validated = 0
    bytes_validated = 0

    release_by_source = {
        item.source_key.lower(): item.dataset_version
        for item in manifest.generation_lineage.source_releases
    }
    for index, entry in enumerate(selected, start=1):
        if entry.row_count > max_rows_per_file:
            raise FD1WarehouseValidationError(
                f"Manifest file exceeds bounded validation row limit: {entry.logical_uri}"
            )
        path = bound_paths[entry.logical_uri]
        metrics, outcomes, pairs = _validate_file_rows(
            path,
            entry,
            expected_release=release_by_source[entry.source_key],
        )
        file_metrics.update(
            {
                "row_checksum_rows": metrics.row_checksum_rows,
                "unique_row_checksums": metrics.unique_row_checksums,
                "prohibited_evidence_rows": metrics.prohibited_evidence_rows,
                "future_evidence_rows": metrics.future_evidence_rows,
                "timing_risk_rows": metrics.timing_risk_rows,
                "strict_same_game_eligible_rows": (
                    metrics.strict_same_game_eligible_rows
                ),
                "timing_risk_strict_overlap_rows": (
                    metrics.timing_risk_strict_overlap_rows
                ),
            }
        )
        taxonomy.update(outcomes)
        token_pairs.update(pairs)
        if entry.family == "play_transition":
            transitions = _validate_play_transitions(path)
            transition_metrics.update(
                {
                    "rows": transitions.rows,
                    "games": transitions.games,
                    "adjacencies_checked": transitions.adjacencies_checked,
                    "automatic_runner_placements": transitions.automatic_runner_placements,
                }
            )
        file_totals[(entry.family, entry.season)].append(entry)
        schema_fingerprints[entry.family].add(entry.physical_schema_fingerprint)
        rows_validated += entry.row_count
        bytes_validated += entry.size_bytes
        if progress_callback is not None:
            progress_callback(
                WarehouseValidationProgress(
                    index,
                    len(selected),
                    rows_validated,
                    total_rows,
                    bytes_validated,
                    total_bytes,
                    entry.logical_uri,
                )
            )

    _validate_taxonomy(taxonomy, token_pairs, full=full)
    if file_metrics["prohibited_evidence_rows"]:
        raise FD1WarehouseValidationError("Prohibited demo/market/fabricated evidence found")
    if file_metrics["future_evidence_rows"]:
        raise FD1WarehouseValidationError("Future evidence is enabled or date-invalid")
    if file_metrics["strict_same_game_eligible_rows"]:
        raise FD1WarehouseValidationError(
            "Retrospective FD1 rows entered the strict same-game pregame population"
        )
    if file_metrics["timing_risk_strict_overlap_rows"]:
        raise FD1WarehouseValidationError("Timing-risk and strict populations overlap")

    totals = tuple(
        FamilySeasonValidationTotal(
            family=family,
            season=season,
            file_count=len(items),
            row_count=sum(item.row_count for item in items),
            byte_count=sum(item.size_bytes for item in items),
            min_date=min(item.min_date for item in items),
            max_date=max(item.max_date for item in items),
        )
        for (family, season), items in sorted(file_totals.items())
    )
    metrics = WarehouseValidationMetrics(
        row_checksum_rows=file_metrics["row_checksum_rows"],
        unique_row_checksums=file_metrics["unique_row_checksums"],
        prohibited_evidence_rows=file_metrics["prohibited_evidence_rows"],
        future_evidence_rows=file_metrics["future_evidence_rows"],
        timing_risk_rows=file_metrics["timing_risk_rows"],
        strict_same_game_eligible_rows=file_metrics[
            "strict_same_game_eligible_rows"
        ],
        timing_risk_strict_overlap_rows=file_metrics[
            "timing_risk_strict_overlap_rows"
        ],
        transition_rows=transition_metrics["rows"],
        transition_games=transition_metrics["games"],
        transition_adjacencies_checked=transition_metrics[
            "adjacencies_checked"
        ],
        automatic_runner_placements=transition_metrics[
            "automatic_runner_placements"
        ],
    )
    checks = (
        ValidationCheck("manifest_file_bindings", "PASS", f"{len(selected)} exact files"),
        ValidationCheck("trusted_dry_run_ledger", "PASS", "content-addressed binding"),
        ValidationCheck("declared_schema_registry", "PASS", "10-family FD1 registry"),
        ValidationCheck(
            "collective_population",
            "PASS" if full else "SKIPPED",
            "exact 329-file population" if full else "bounded rehearsal subset",
        ),
        ValidationCheck("row_checksum_uniqueness", "PASS", "per-file uniqueness"),
        ValidationCheck("plate_appearance_taxonomy", "PASS", "exact eight-class domain"),
        ValidationCheck("transition_conservation", "PASS", "base/out/score continuity"),
        ValidationCheck("timing_risk_separation", "PASS", "zero strict overlap"),
        ValidationCheck(
            "active_identity_threshold",
            "PASS" if active_identity is not None else "SKIPPED",
            "checksummed report binding" if active_identity is not None else "not required",
        ),
    )
    unsigned = {
        "active_identity": active_identity.to_dict() if active_identity else None,
        "byte_count": total_bytes,
        "checks": [item.to_dict() for item in checks],
        "family_schema_fingerprints": {
            family: sorted(values) for family, values in sorted(schema_fingerprints.items())
        },
        "family_season_totals": [item.to_dict() for item in totals],
        "file_count": len(selected),
        "manifest_checksum": manifest.manifest_checksum,
        "manifest_file_count": len(manifest.files),
        "metrics": metrics.to_dict(),
        "mode": mode,
        "pa_taxonomy_counts": {name: taxonomy[name] for name in PA_OUTCOMES},
        "partitions": len(selected),
        "passed": True,
        "population_complete": full,
        "publishable": full,
        "recovery_id": manifest.recovery_id,
        "row_count": total_rows,
        "schema_version": WAREHOUSE_VALIDATION_SCHEMA_VERSION,
    }
    return WarehouseValidationResult(
        schema_version=WAREHOUSE_VALIDATION_SCHEMA_VERSION,
        mode=mode,
        recovery_id=manifest.recovery_id,
        manifest_checksum=manifest.manifest_checksum,
        passed=True,
        publishable=full,
        population_complete=full,
        manifest_file_count=len(manifest.files),
        file_count=len(selected),
        row_count=total_rows,
        byte_count=total_bytes,
        family_season_totals=totals,
        family_schema_fingerprints=MappingProxyType(
            {family: tuple(sorted(values)) for family, values in schema_fingerprints.items()}
        ),
        pa_taxonomy_counts=MappingProxyType(
            {name: taxonomy[name] for name in PA_OUTCOMES}
        ),
        metrics=metrics,
        active_identity=active_identity,
        checks=checks,
        validation_checksum=_canonical_checksum(unsigned),
    )


def _select_entries(
    manifest: RecoveryManifest,
    entries: Sequence[RecoveryFileEntry] | None,
) -> tuple[RecoveryFileEntry, ...]:
    selected = manifest.files if entries is None else tuple(entries)
    if not selected:
        raise FD1WarehouseValidationError("Validation file selection cannot be empty")
    by_uri = {entry.logical_uri: entry for entry in manifest.files}
    seen: set[str] = set()
    for entry in selected:
        if not isinstance(entry, RecoveryFileEntry):
            raise TypeError("entries must contain RecoveryFileEntry values")
        if entry.logical_uri in seen or by_uri.get(entry.logical_uri) != entry:
            raise FD1WarehouseValidationError(
                "Validation selection is duplicated or differs from the manifest"
            )
        seen.add(entry.logical_uri)
    return tuple(sorted(selected, key=lambda item: item.logical_uri))


def _representative_entries(
    manifest: RecoveryManifest,
) -> tuple[RecoveryFileEntry, ...]:
    """Choose a stable family-and-season rehearsal without directory discovery."""

    by_family: defaultdict[str, list[RecoveryFileEntry]] = defaultdict(list)
    for entry in manifest.files:
        by_family[entry.family].append(entry)
    selected: dict[str, RecoveryFileEntry] = {}
    for family, values in sorted(by_family.items()):
        ordered = sorted(values, key=lambda item: item.logical_uri)
        rank = int(
            hashlib.sha256(
                f"{manifest.manifest_checksum}:{family}".encode("ascii")
            ).hexdigest(),
            16,
        )
        chosen = ordered[rank % len(ordered)]
        selected[chosen.logical_uri] = chosen
    covered_seasons = {entry.season for entry in selected.values()}
    for season in sorted(EXPECTED_SEASONS - covered_seasons):
        candidates = [entry for entry in manifest.files if entry.season == season]
        if candidates:
            chosen = min(candidates, key=lambda item: item.logical_uri)
            selected[chosen.logical_uri] = chosen
    return tuple(sorted(selected.values(), key=lambda item: item.logical_uri))


def _validate_trusted_ledger(
    manifest: RecoveryManifest,
    trusted_ledger_path: Path,
) -> None:
    binding = manifest.generation_lineage.trusted_dry_run_ledger
    path = Path(trusted_ledger_path)
    if _linklike(path) or not path.is_file():
        raise FD1WarehouseValidationError("Trusted dry-run ledger is unavailable or linked")
    if not _path_has_logical_suffix(path, binding.logical_uri):
        raise FD1WarehouseValidationError("Trusted dry-run ledger path differs from manifest")
    try:
        encoded = path.read_bytes()
        payload = json.loads(encoded)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FD1WarehouseValidationError("Trusted dry-run ledger is unreadable") from exc
    if hashlib.sha256(encoded).hexdigest() != binding.sha256:
        raise FD1WarehouseValidationError("Trusted dry-run ledger checksum changed")
    if not isinstance(payload, dict) or payload.get("schema_version") != binding.schema_version:
        raise FD1WarehouseValidationError("Trusted dry-run ledger schema changed")


def _coerce_active_identity(
    value: ActiveIdentityValidationBinding | Path | None,
) -> ActiveIdentityValidationBinding | None:
    if value is None:
        return None
    if isinstance(value, ActiveIdentityValidationBinding):
        return value
    return _load_active_identity_binding(Path(value))


def _load_active_identity_binding(path: Path) -> ActiveIdentityValidationBinding:
    if _linklike(path) or not path.is_file():
        raise FD1WarehouseValidationError("Active-identity report is unavailable or linked")
    try:
        encoded = path.read_bytes()
        payload = json.loads(encoded)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FD1WarehouseValidationError("Active-identity report is unreadable") from exc
    if not isinstance(payload, dict):
        raise FD1WarehouseValidationError("Active-identity report must be a JSON object")

    # The small operator summary may point at the immutable content-addressed report.
    artifact = payload.get("artifact")
    artifact_sha = payload.get("artifact_sha256")
    if isinstance(artifact, str) and isinstance(artifact_sha, str):
        artifact_path = _resolve_sibling_logical_path(path, artifact)
        binding = _load_active_identity_binding(artifact_path)
        if binding.artifact_sha256 != artifact_sha:
            raise FD1WarehouseValidationError(
                "Active-identity summary and artifact checksums differ"
            )
        return binding

    digest = hashlib.sha256(encoded).hexdigest()
    expected_name = f"active-identity-actual-sha256-{digest}.json"
    if path.name != expected_name:
        raise FD1WarehouseValidationError(
            "Active-identity report is not at its content-addressed path"
        )
    coverage = _json_mapping(
        payload.get("active_identity_coverage_report"),
        "active-identity coverage",
    )
    decision = _json_mapping(payload.get("threshold_decision"), "threshold decision")
    populations = coverage.get("population_coverage")
    if not isinstance(populations, list):
        raise FD1WarehouseValidationError("Active-identity population coverage is absent")
    primary = next(
        (
            item
            for item in populations
            if isinstance(item, dict) and item.get("label") == "GEN2_PRIMARY_2023_2025"
        ),
        None,
    )
    if primary is None:
        raise FD1WarehouseValidationError("Active-identity primary population is absent")
    links = primary.get("exact_link_rates")
    if not isinstance(links, list) or not links or not all(
        isinstance(item, dict) for item in links
    ):
        raise FD1WarehouseValidationError("Active-identity link rates are malformed")
    try:
        reference_rate = min(float(item["exact_reference_rate"]) for item in links)
        unique_rate = min(float(item["exact_unique_rate"]) for item in links)
        conflicts = sum(int(item["conflict_reference_count"]) for item in links)
        threshold = float(decision["primary_threshold"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FD1WarehouseValidationError("Active-identity thresholds are malformed") from exc
    if any(
        decision.get(name) is not True
        for name in (
            "primary_meets_threshold",
            "primary_reference_meets_threshold",
            "primary_unique_meets_threshold",
        )
    ):
        raise FD1WarehouseValidationError("Active-identity threshold decision did not pass")
    affected = coverage.get("affected_players")
    if not isinstance(affected, list):
        raise FD1WarehouseValidationError("Active-identity affected-player set is malformed")
    derivation = coverage.get("derivation_checksum")
    schema_version = coverage.get("schema_version")
    if not isinstance(derivation, str) or not isinstance(schema_version, str):
        raise FD1WarehouseValidationError("Active-identity lineage is malformed")
    logical_uri = _logical_uri_from_runtime_path(path)
    return ActiveIdentityValidationBinding(
        logical_uri=logical_uri,
        artifact_sha256=digest,
        report_derivation_checksum=derivation,
        schema_version=schema_version,
        primary_threshold=threshold,
        primary_reference_rate=reference_rate,
        primary_unique_rate=unique_rate,
        affected_player_count=len(affected),
        conflict_reference_count=conflicts,
    )


def _path_has_logical_suffix(path: Path, logical_uri: str) -> bool:
    actual = path.resolve(strict=True).as_posix()
    expected = PurePosixPath(logical_uri).as_posix()
    return actual == expected or actual.endswith(f"/{expected}")


def _logical_uri_from_runtime_path(path: Path) -> str:
    parts = path.resolve(strict=True).parts
    try:
        index = parts.index(".runtime")
    except ValueError as exc:
        raise FD1WarehouseValidationError(
            "Active-identity artifact is outside the ignored runtime root"
        ) from exc
    return PurePosixPath(*parts[index:]).as_posix()


def _resolve_sibling_logical_path(summary_path: Path, logical_uri: str) -> Path:
    pure = PurePosixPath(logical_uri)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise FD1WarehouseValidationError("Active-identity summary artifact path is unsafe")
    resolved_summary = summary_path.resolve(strict=True)
    parts = resolved_summary.parts
    if ".runtime" not in parts:
        raise FD1WarehouseValidationError("Active-identity summary is outside runtime")
    root = Path(*parts[: parts.index(".runtime")])
    candidate = root.joinpath(*pure.parts).resolve(strict=True)
    try:
        candidate.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise FD1WarehouseValidationError("Active-identity artifact escapes project root") from exc
    return candidate


def _json_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FD1WarehouseValidationError(f"{label} must be a JSON object")
    return value


def _validate_full_population(manifest: RecoveryManifest) -> None:
    aggregate = manifest.aggregate
    if (
        aggregate.file_count != EXPECTED_FULL_FILE_COUNT
        or aggregate.row_count != EXPECTED_FULL_ROW_COUNT
        or aggregate.byte_count != EXPECTED_FULL_BYTE_COUNT
    ):
        raise FD1WarehouseValidationError(
            "Manifest does not match the exact interrupted FD1 population"
        )
    rows = {item.family: item.row_count for item in aggregate.families}
    files = {item.family: item.file_count for item in aggregate.families}
    if rows != dict(EXPECTED_FULL_FAMILY_ROWS) or files != dict(
        EXPECTED_FULL_FAMILY_FILES
    ):
        raise FD1WarehouseValidationError("FD1 family totals differ from the frozen population")
    seasons: defaultdict[str, set[int]] = defaultdict(set)
    for entry in manifest.files:
        seasons[entry.family].add(entry.season)
        if entry.family == "player_prior" and entry.month != 12:
            raise FD1WarehouseValidationError("Player-prior partitions must be annual December")
    if set(seasons) != set(EXPECTED_DECLARED_SCHEMAS) or any(
        values != EXPECTED_SEASONS for values in seasons.values()
    ):
        raise FD1WarehouseValidationError("Every FD1 family must cover seasons 2021-2025")


def _validate_schema_contract(
    entries: Sequence[RecoveryFileEntry], *, full: bool
) -> None:
    names_by_family: dict[str, tuple[str, ...]] = {}
    families: set[str] = set()
    for entry in entries:
        families.add(entry.family)
        if entry.declared_schema != EXPECTED_DECLARED_SCHEMAS.get(entry.family):
            raise FD1WarehouseValidationError("A declared schema is outside FD1 recovery")
        fields = tuple(field.name for field in entry.physical_schema)
        if len(fields) != EXPECTED_PHYSICAL_COLUMN_COUNTS[entry.family]:
            raise FD1WarehouseValidationError(
                f"{entry.family} physical column count differs from its frozen schema"
            )
        missing = _REQUIRED_BY_FAMILY[entry.family] - set(fields)
        if missing:
            raise FD1WarehouseValidationError(
                f"{entry.family} schema lacks required fields: {sorted(missing)}"
            )
        existing = names_by_family.setdefault(entry.family, fields)
        if fields != existing:
            raise FD1WarehouseValidationError(
                f"{entry.family} physical column order is inconsistent across files"
            )
    if full and families != set(EXPECTED_DECLARED_SCHEMAS):
        raise FD1WarehouseValidationError("FULL validation requires all ten FD1 families")


def _validated_root(store_root: Path) -> Path:
    try:
        root = Path(store_root).resolve(strict=True)
    except OSError as exc:
        raise FD1WarehouseValidationError("Parquet store root is unavailable") from exc
    if not root.is_dir() or _linklike(root):
        raise FD1WarehouseValidationError("Parquet store root is not a regular directory")
    return root


def _validate_file_binding(root: Path, entry: RecoveryFileEntry) -> Path:
    pure = PurePosixPath(entry.logical_uri)
    if pure.is_absolute() or ".." in pure.parts:
        raise FD1WarehouseValidationError("Manifest file path is unsafe")
    candidate = root.joinpath(*pure.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise FD1WarehouseValidationError("Manifest file escapes or is unavailable") from exc
    current = root
    for part in pure.parts:
        current = current / part
        if _linklike(current):
            raise FD1WarehouseValidationError("Manifest path contains a link/reparse point")
    try:
        file_stat = resolved.stat()
    except OSError as exc:
        raise FD1WarehouseValidationError("Manifest Parquet stat is unreadable") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise FD1WarehouseValidationError("Manifest path is not a regular file")
    if file_stat.st_size != entry.size_bytes or file_stat.st_mtime_ns != entry.mtime_ns:
        raise FD1WarehouseValidationError("Manifest Parquet stat binding changed")
    try:
        schema = pl.read_parquet_schema(resolved)
    except Exception as exc:
        raise FD1WarehouseValidationError("Manifest Parquet footer is unreadable") from exc
    actual_schema = tuple((name, str(dtype)) for name, dtype in schema.items())
    expected_schema = tuple((field.name, field.dtype) for field in entry.physical_schema)
    if actual_schema != expected_schema:
        raise FD1WarehouseValidationError("Manifest Parquet physical schema changed")
    if resolved.name != f"part-{entry.sha256}.parquet":
        raise FD1WarehouseValidationError("Manifest content-addressed file name changed")
    return resolved


def _validate_file_rows(
    path: Path,
    entry: RecoveryFileEntry,
    *,
    expected_release: str,
) -> tuple[_FileMetrics, Counter[str], Counter[tuple[str, str]]]:
    schema = pl.read_parquet_schema(path)
    date_column = "official_date" if entry.family == "player_prior" else "game_date"
    expressions: list[pl.Expr] = [
        pl.len().alias("row_count"),
        pl.col(date_column).min().alias("min_date"),
        pl.col(date_column).max().alias("max_date"),
        _mismatch("source_key", entry.source_key.upper()).alias("bad_source"),
        _mismatch("season", entry.season).alias("bad_season"),
    ]
    if entry.family == "player_prior":
        expressions.extend(
            [
                _mismatch("event_family", "player_prior").alias("bad_event_family"),
                _mismatch("official_date", f"{entry.season}-12-31").alias("bad_date"),
                _mismatch("eligible_from_season", entry.season + 1).alias(
                    "bad_eligible_season"
                ),
                pl.col("same_season_predictor_eligible")
                .ne(False)
                .fill_null(True)
                .sum()
                .alias("bad_same_season"),
                _mismatch("identity_resolution_status", "EXACT").alias(
                    "bad_identity_status"
                ),
                pl.col("canonical_player_id").is_null().sum().alias("bad_player_id"),
                (~pl.col("source_releases").list.contains(expected_release))
                .fill_null(True)
                .sum()
                .alias("bad_release"),
                _invalid_sha("source_derivation_checksum").alias("bad_derivation_sha"),
                pl.col("source_derivation_checksum")
                .n_unique()
                .alias("derivation_unique"),
            ]
        )
    else:
        expressions.extend(
            [
                _mismatch("source_version_id", entry.source_version_id).alias(
                    "bad_source_version"
                ),
                _mismatch("month", entry.month).alias("bad_month"),
                _mismatch("event_date", pl.col("game_date")).alias("bad_event_date"),
                _mismatch("historical_reconstruction", True).alias("bad_historical"),
                _mismatch("available_only_after_game_final", True).alias(
                    "bad_final_only"
                ),
                pl.col("future_evidence_allowed")
                .ne(False)
                .fill_null(True)
                .sum()
                .alias("future_evidence_rows"),
                pl.col("strict_same_game_pregame_eligible")
                .ne(False)
                .fill_null(True)
                .sum()
                .alias("strict_rows"),
                _invalid_sha("row_checksum").alias("bad_row_sha"),
                pl.col("row_checksum").n_unique().alias("row_checksum_unique"),
            ]
        )
        game_date = pl.col("game_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False)
        final_date = pl.col("final_available_date").str.strptime(
            pl.Date, "%Y-%m-%d", strict=False
        )
        expressions.append(
            (
                game_date.is_null()
                | final_date.is_null()
                | (final_date < game_date)
                | (final_date > pl.lit(date(2025, 12, 31)))
            )
            .fill_null(True)
            .sum()
            .alias("bad_final_date")
        )
        if "retrosheet_event_date" in schema:
            retro_date = pl.col("retrosheet_event_date").str.strptime(
                pl.Date, "%Y-%m-%d", strict=False
            )
            expressions.append(
                (
                    retro_date.is_null()
                    | (retro_date < game_date)
                    | (retro_date > pl.lit(date(2025, 12, 31)))
                )
                .fill_null(True)
                .sum()
                .alias("bad_retrosheet_date")
            )
    provenance = [
        name
        for name in (
            "source_key",
            "source_mode",
            "evidence_mode",
            "population_category",
            "strict_population",
            "weather_source",
            "timing_classification",
        )
        if name in schema
    ]
    expressions.append(
        pl.concat_str([pl.col(name).fill_null("") for name in provenance], separator="|")
        .str.contains(_PROHIBITED_EVIDENCE)
        .fill_null(False)
        .sum()
        .alias("prohibited_rows")
    )
    if "timing_risk_label" in schema:
        timing = pl.col("timing_risk_label").is_not_null()
        strict = pl.col("strict_same_game_pregame_eligible").fill_null(False)
        expressions.extend(
            [
                timing.sum().alias("timing_rows"),
                (timing & strict).sum().alias("timing_strict_overlap"),
            ]
        )
    try:
        result = (
            pl.scan_parquet(path, hive_partitioning=False)
            .select(expressions)
            .collect(engine="streaming")
            .row(0, named=True)
        )
    except Exception as exc:
        raise FD1WarehouseValidationError(
            f"Parquet row validation failed: {entry.logical_uri}"
        ) from exc
    if result["row_count"] != entry.row_count:
        raise FD1WarehouseValidationError("Manifest Parquet row count changed")
    if str(result["min_date"]) != entry.min_date or str(result["max_date"]) != entry.max_date:
        raise FD1WarehouseValidationError("Manifest Parquet date bounds changed")
    ignored = {
        "row_count",
        "min_date",
        "max_date",
        "row_checksum_unique",
        "derivation_unique",
        "future_evidence_rows",
        "strict_rows",
        "prohibited_rows",
        "timing_rows",
        "timing_strict_overlap",
    }
    violations = {
        name: int(value)
        for name, value in result.items()
        if name not in ignored and int(value or 0) != 0
    }
    unique_column = (
        "derivation_unique" if entry.family == "player_prior" else "row_checksum_unique"
    )
    if int(result[unique_column]) != entry.row_count:
        violations[f"{unique_column}_mismatch"] = (
            entry.row_count - int(result[unique_column])
        )
    if violations:
        raise FD1WarehouseValidationError(
            f"Parquet semantic binding failed for {entry.logical_uri}: {violations}"
        )

    outcomes: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    if entry.family == "plate_appearance":
        counts = (
            pl.scan_parquet(path, hive_partitioning=False)
            .group_by(["source_terminal_token", "outcome", "taxonomy_version"])
            .len()
            .collect(engine="streaming")
        )
        for row in counts.iter_rows(named=True):
            if row["taxonomy_version"] != MATERIALIZED_PA_TAXONOMY_VERSION:
                raise FD1WarehouseValidationError("Plate-appearance taxonomy version changed")
            token = str(row["source_terminal_token"])
            outcome = str(row["outcome"])
            count = int(row["len"])
            outcomes[outcome] += count
            pairs[(token, outcome)] += count
    return (
        _FileMetrics(
            row_checksum_rows=(0 if entry.family == "player_prior" else entry.row_count),
            unique_row_checksums=(
                0 if entry.family == "player_prior" else int(result["row_checksum_unique"])
            ),
            prohibited_evidence_rows=int(result["prohibited_rows"]),
            future_evidence_rows=int(result.get("future_evidence_rows", 0)),
            timing_risk_rows=int(result.get("timing_rows", 0)),
            strict_same_game_eligible_rows=int(result.get("strict_rows", 0)),
            timing_risk_strict_overlap_rows=int(result.get("timing_strict_overlap", 0)),
        ),
        outcomes,
        pairs,
    )


def _validate_taxonomy(
    counts: Counter[str],
    pairs: Counter[tuple[str, str]],
    *,
    full: bool,
) -> None:
    unknown = set(counts) - set(PA_OUTCOMES)
    bad_pairs = {
        pair: count
        for pair, count in pairs.items()
        if RETROSHEET_PA_CROSSWALK.get(pair[0]) != pair[1]
    }
    if unknown or bad_pairs:
        raise FD1WarehouseValidationError(
            f"Plate-appearance taxonomy changed: unknown={sorted(unknown)}, pairs={bad_pairs}"
        )
    if full and set(counts) != set(PA_OUTCOMES):
        raise FD1WarehouseValidationError(
            "Full plate-appearance population does not contain all eight classes"
        )


def _validate_play_transitions(path: Path) -> _TransitionMetrics:
    columns = [
        "game_id",
        "play_number",
        "half_play_number",
        "inning",
        "top_bottom",
        "batting_team_id",
        "visiting_team_id",
        "home_team_id",
        "event_text",
        "outs_before",
        "outs_after",
        "base_mask_before",
        "base_mask_after",
        "runner_on_first_before",
        "runner_on_second_before",
        "runner_on_third_before",
        "runner_on_first_after",
        "runner_on_second_after",
        "runner_on_third_after",
        "visiting_score_before",
        "home_score_before",
        "visiting_score_after",
        "home_score_after",
        "runs_scored",
        "automatic_runner_placement_before",
        "automatic_runner_placement_mode",
        "automatic_runner_base",
        "automatic_runner_player_id",
        "retrosheet_automatic_runner_id",
    ]
    try:
        frame = pl.read_parquet(
            path,
            columns=columns,
            hive_partitioning=False,
            low_memory=True,
        ).sort(["game_id", "play_number"])
    except Exception as exc:
        raise FD1WarehouseValidationError("Play-transition partition is unreadable") from exc
    same_game = pl.col("game_id") == pl.col("game_id").shift(1)
    same_half = (
        same_game
        & (pl.col("inning") == pl.col("inning").shift(1))
        & (pl.col("top_bottom") == pl.col("top_bottom").shift(1))
    )
    first_game = ~same_game.fill_null(False)
    auto = pl.col("automatic_runner_placement_before")
    visitor_batting = pl.col("batting_team_id") == pl.col("visiting_team_id")
    home_batting = pl.col("batting_team_id") == pl.col("home_team_id")
    before_mask = _runner_mask("before")
    after_mask = _runner_mask("after")
    duplicate_before = _duplicate_runners("before")
    duplicate_after = _duplicate_runners("after")
    score_delta = (
        pl.col("visiting_score_after")
        - pl.col("visiting_score_before")
        + pl.col("home_score_after")
        - pl.col("home_score_before")
    )
    visitor_score_bad = visitor_batting & (
        (pl.col("visiting_score_after") - pl.col("visiting_score_before") != pl.col("runs_scored"))
        | (pl.col("home_score_after") != pl.col("home_score_before"))
    )
    home_score_bad = home_batting & (
        (pl.col("home_score_after") - pl.col("home_score_before") != pl.col("runs_scored"))
        | (pl.col("visiting_score_after") != pl.col("visiting_score_before"))
    )
    terminal_np = (
        (pl.col("event_text") == "NP")
        & (pl.col("outs_before") == 3)
        & (pl.col("outs_after") == 3)
        & (pl.col("base_mask_before") == 0)
        & (pl.col("base_mask_after") == 0)
        & (pl.col("runs_scored") == 0)
    )
    violations: Mapping[str, pl.Expr] = {
        "outs_range": (~pl.col("outs_before").is_between(0, 2) & ~terminal_np)
        | ~pl.col("outs_after").is_between(0, 3),
        "base_mask_range": ~pl.col("base_mask_before").is_between(0, 7)
        | ~pl.col("base_mask_after").is_between(0, 7),
        "runner_mask": (before_mask != pl.col("base_mask_before"))
        | (after_mask != pl.col("base_mask_after")),
        "duplicate_runner": duplicate_before | duplicate_after,
        "third_out_bases": (pl.col("outs_after") == 3)
        & (pl.col("base_mask_after") != 0),
        "score_delta": (pl.col("runs_scored") < 0)
        | (score_delta != pl.col("runs_scored")),
        "batting_score": ~(visitor_batting | home_batting)
        | visitor_score_bad
        | home_score_bad,
        "play_order": (pl.col("play_number") <= 0)
        | (same_game & (pl.col("play_number") <= pl.col("play_number").shift(1))),
        "half_play_order": (pl.col("half_play_number") <= 0)
        | (
            same_half
            & (pl.col("half_play_number") <= pl.col("half_play_number").shift(1))
        ),
        "score_continuity": same_game
        & (
            (pl.col("visiting_score_before") != pl.col("visiting_score_after").shift(1))
            | (pl.col("home_score_before") != pl.col("home_score_after").shift(1))
        ),
        "outs_continuity": same_half
        & (pl.col("outs_before") != pl.col("outs_after").shift(1)),
        "base_continuity": same_half
        & ~auto
        & (pl.col("base_mask_before") != pl.col("base_mask_after").shift(1)),
        "half_reset": same_game
        & ~same_half
        & (
            (pl.col("outs_before") != 0)
            | (~auto & (pl.col("base_mask_before") != 0))
        ),
        "initial_state": first_game
        & (
            (pl.col("outs_before") != 0)
            | (pl.col("base_mask_before") != 0)
            | (pl.col("visiting_score_before") != 0)
            | (pl.col("home_score_before") != 0)
        ),
        "automatic_runner": auto
        & (
            (pl.col("automatic_runner_base") != 2)
            | ~pl.col("automatic_runner_placement_mode").is_in(
                ["DIRECT_FIRST_PLAY_OF_EXTRA_HALF", "AFTER_INITIAL_NP_BRIDGE"]
            )
            | pl.col("automatic_runner_player_id").is_null()
            | pl.col("retrosheet_automatic_runner_id").is_null()
            | (
                pl.col("runner_on_second_before")
                != pl.col("automatic_runner_player_id")
            )
        ),
        "spurious_automatic_runner": ~auto
        & (
            pl.col("automatic_runner_base").is_not_null()
            | pl.col("automatic_runner_placement_mode").is_not_null()
            | pl.col("automatic_runner_player_id").is_not_null()
            | pl.col("retrosheet_automatic_runner_id").is_not_null()
        ),
    }
    result = frame.select(
        [expression.fill_null(False).sum().alias(name) for name, expression in violations.items()]
    ).row(0, named=True)
    failed = {name: int(value) for name, value in result.items() if int(value) != 0}
    if failed:
        raise FD1WarehouseValidationError(
            f"Play-transition conservation failed for {path.name}: {failed}"
        )
    games = frame.get_column("game_id").n_unique()
    return _TransitionMetrics(
        rows=frame.height,
        games=games,
        adjacencies_checked=frame.height - games,
        automatic_runner_placements=int(
            frame.get_column("automatic_runner_placement_before").sum()
        ),
    )


def _runner_mask(suffix: str) -> pl.Expr:
    return sum(
        pl.col(f"runner_on_{base}_{suffix}").is_not_null().cast(pl.Int64) * bit
        for base, bit in (("first", 1), ("second", 2), ("third", 4))
    )


def _duplicate_runners(suffix: str) -> pl.Expr:
    first = pl.col(f"runner_on_first_{suffix}")
    second = pl.col(f"runner_on_second_{suffix}")
    third = pl.col(f"runner_on_third_{suffix}")
    return (
        (first.is_not_null() & second.is_not_null() & (first == second))
        | (first.is_not_null() & third.is_not_null() & (first == third))
        | (second.is_not_null() & third.is_not_null() & (second == third))
    )


def _mismatch(name: str, expected: object) -> pl.Expr:
    comparison = pl.col(name) != expected
    return comparison.fill_null(True).sum()


def _invalid_sha(name: str) -> pl.Expr:
    return (
        ~pl.col(name).str.contains(r"^[0-9a-f]{64}$")
    ).fill_null(True).sum()


def _linklike(path: Path) -> bool:
    try:
        item = os.lstat(path)
    except OSError:
        return True
    return stat.S_ISLNK(item.st_mode) or bool(
        getattr(item, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} checksum must be lowercase SHA-256")
    return value


def _canonical_checksum(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DEFAULT_MAX_REHEARSAL_FILES",
    "DEFAULT_MAX_ROWS_PER_FILE",
    "EXPECTED_FULL_BYTE_COUNT",
    "EXPECTED_FULL_FAMILY_FILES",
    "EXPECTED_FULL_FAMILY_ROWS",
    "EXPECTED_FULL_FILE_COUNT",
    "EXPECTED_FULL_ROW_COUNT",
    "FULL_VALIDATION_MODE",
    "MATERIALIZED_PA_TAXONOMY_VERSION",
    "PA_OUTCOMES",
    "REHEARSAL_VALIDATION_MODE",
    "WAREHOUSE_VALIDATION_SCHEMA_VERSION",
    "ActiveIdentityValidationBinding",
    "FD1WarehouseValidationError",
    "FamilySeasonValidationTotal",
    "ValidationCheck",
    "WarehouseValidationMetrics",
    "WarehouseValidationProgress",
    "WarehouseValidationResult",
    "validate_recovery_warehouse",
]
