"""Standalone validator for ``SPORTS_IDENTITY_BRIDGE_V1`` packages.

The validator has no database, provider, runtime-evidence, or publisher import
path.  Its only external authority is a fresh package-only scan of the frozen
parent exchange.  Every bridge claim is then reconstructed from the seven
portable package files.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from app.gen2.exchange.canonical_json import CanonicalJsonError
from app.gen2.exchange.filesystem import (
    ExchangePathError,
    assert_regular_independent_file,
    governed_root,
    iter_relative_files,
    resolve_under,
)
from app.gen2.exchange.identity_bridge import trust
from app.gen2.exchange.identity_bridge.canonical import (
    canonical_json_bytes,
    load_canonical_json,
    verify_bridge_manifest_identity,
)
from app.gen2.exchange.identity_bridge.contracts import (
    BridgeFile,
    SportsIdentityBridgeManifestV1,
)
from app.gen2.exchange.identity_bridge.package_metadata import (
    readme_bytes,
    validation_report_bytes,
)
from app.gen2.exchange.identity_bridge.parent import (
    ParentIdentityEvidence,
    ParentIdentityScanDeadline,
    ParentIdentityScanError,
    ParentScanProgress,
    scan_parent_identity,
)
from app.gen2.exchange.identity_bridge.revision import (
    IdentityBridgeRevisionError,
    extraction_code_revision,
    publishing_code_revision,
)
from app.gen2.exchange.identity_bridge.rights import (
    IdentityBridgeRightsError,
    REQUIRED_RIGHTS_IDS,
    validate_rights_artifacts,
)
from app.gen2.exchange.identity_bridge.rows import (
    IdentityBridgeRowError,
    ParquetArtifact,
    SUPPORTED_NAMESPACES,
    validate_alias_parquet,
    validate_dimension_parquet,
)
from app.gen2.exchange.identity_bridge.security import (
    IdentityBridgeSecurityError,
    validate_package_security,
)


VALIDATION_REPORT_VERSION = "SPORTS_IDENTITY_BRIDGE_VALIDATION_REPORT_V1"
MANIFEST_LOGICAL_URI = "sports-identity-bridge-manifest.json"
_BASE_FILE_INVENTORY = frozenset(
    {
        MANIFEST_LOGICAL_URI,
        *trust.REQUIRED_SEMANTIC_BRIDGE_FILES,
    }
)
_FINAL_FILE_INVENTORY = frozenset(
    {*_BASE_FILE_INVENTORY, *trust.REQUIRED_AUXILIARY_FILES}
)
_CONSUMER_JOIN_FIELDS = (
    ("bullpen_usage", "player_id"),
    ("pitch_sequence", "batter_id"),
    ("pitch_sequence", "pitcher_id"),
    ("plate_appearance", "batter_id"),
    ("plate_appearance", "pitcher_id"),
    ("player_game_batting", "player_id"),
    ("player_game_fielding", "player_id"),
    ("player_game_pitching", "player_id"),
    ("starter_usage", "player_id"),
)
_EXPECTED_CANONICAL_MAPPING_ROWS = 736_056
_EXPECTED_UNRESOLVED_ROWS = 1_187
_EXPECTED_AMBIGUOUS_ROWS = 16
_PROJECT_ROOT = Path(__file__).resolve().parents[5]


class IdentityBridgeValidationError(RuntimeError):
    """The bridge package is incomplete, corrupted, ambiguous, or non-portable."""


class IdentityBridgeValidationDeadline(IdentityBridgeValidationError):
    """Validation reached the caller's fail-closed runtime limit."""

    def __init__(self, progress: Mapping[str, Any]):
        super().__init__("Identity bridge validation reached its safe runtime bound")
        self.progress = dict(progress)


@dataclass(frozen=True, slots=True)
class IdentityBridgeValidationProgress:
    stage: str
    elapsed_seconds: float
    files_checked: int
    files_total: int
    bytes_checked: int
    bytes_total: int
    rows_checked: int
    rows_total: int
    current_file: str | None


@dataclass(frozen=True, slots=True)
class IdentityBridgeValidationResult:
    package_root: Path
    parent_package_root: Path
    manifest: SportsIdentityBridgeManifestV1
    report: dict[str, Any]
    report_bytes: bytes
    file_count: int
    total_bytes: int
    status: Literal["PASS"]
    elapsed_seconds: float


def validate_identity_bridge(
    package_root: Path,
    parent_package_root: Path,
    *,
    require_auxiliary: bool = True,
    heartbeat_seconds: int = 30,
    max_runtime_minutes: float = 90,
    progress_callback: Callable[[IdentityBridgeValidationProgress], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> IdentityBridgeValidationResult:
    """Validate a bridge and freshly rescan its immutable parent package.

    ``require_auxiliary=False`` is crash-safe staging mode: the exact five base
    files are required and zero, one, or both known auxiliary files may already
    exist.  Any present auxiliary is verified.  ``True`` is final mode: the
    exact seven-file inventory is required.
    """

    if not isinstance(require_auxiliary, bool):
        raise ValueError("require_auxiliary must be Boolean")
    if isinstance(heartbeat_seconds, bool) or not 1 <= heartbeat_seconds <= 30:
        raise ValueError("heartbeat_seconds must be between 1 and 30")
    if (
        isinstance(max_runtime_minutes, bool)
        or not isinstance(max_runtime_minutes, (int, float))
        or max_runtime_minutes <= 0
        or (
            isinstance(max_runtime_minutes, float)
            and not math.isfinite(max_runtime_minutes)
        )
    ):
        raise ValueError("max_runtime_minutes must be positive and finite")

    try:
        root = governed_root(Path(package_root))
    except ExchangePathError as exc:
        raise IdentityBridgeValidationError("Bridge package root is unsafe") from exc
    started = clock()
    deadline = started + float(max_runtime_minutes) * 60
    last_emit = started - heartbeat_seconds
    files_checked = 0
    bytes_checked = 0
    rows_checked = 0
    rows_total = 0
    current_file: str | None = None
    files_total = (
        len(_FINAL_FILE_INVENTORY)
        if require_auxiliary
        else len(_BASE_FILE_INVENTORY)
    )
    bytes_total = 0

    def snapshot(stage: str) -> IdentityBridgeValidationProgress:
        return IdentityBridgeValidationProgress(
            stage=stage,
            elapsed_seconds=max(clock() - started, 0.0),
            files_checked=files_checked,
            files_total=files_total,
            bytes_checked=bytes_checked,
            bytes_total=bytes_total,
            rows_checked=rows_checked,
            rows_total=rows_total,
            current_file=current_file,
        )

    def emit(stage: str, *, force: bool = False) -> None:
        nonlocal last_emit
        now = clock()
        if now >= deadline:
            raise IdentityBridgeValidationDeadline(asdict(snapshot(stage)))
        if progress_callback is not None and (force or now - last_emit >= heartbeat_seconds):
            progress_callback(snapshot(stage))
            last_emit = now

    emit("INVENTORY", force=True)
    try:
        inventory = _exact_inventory(root, require_auxiliary=require_auxiliary)
    except ExchangePathError as exc:
        raise IdentityBridgeValidationError("Bridge file inventory is unsafe") from exc
    bytes_total = sum(info.st_size for _logical_uri, _path, info in inventory)
    files_total = len(inventory)

    emit("LOADING_MANIFEST", force=True)
    current_file = MANIFEST_LOGICAL_URI
    manifest_path = _inventory_path(inventory, MANIFEST_LOGICAL_URI)
    try:
        raw_manifest = load_canonical_json(manifest_path)
        if not isinstance(raw_manifest, dict):
            raise IdentityBridgeValidationError("Bridge manifest must be an object")
        manifest = SportsIdentityBridgeManifestV1.model_validate(raw_manifest)
        digest = verify_bridge_manifest_identity(raw_manifest)
        manifest_bytes = manifest_path.read_bytes()
        if manifest_bytes != canonical_json_bytes(
            manifest.model_dump(mode="json"), trailing_newline=True
        ):
            raise IdentityBridgeValidationError(
                "Bridge manifest bytes differ from the normalized contract"
            )
    except (CanonicalJsonError, ValueError) as exc:
        raise IdentityBridgeValidationError("Bridge manifest is invalid") from exc
    files_checked += 1
    bytes_checked += manifest_path.stat().st_size
    emit("MANIFEST_VALIDATED")

    emit("VALIDATING_SEMANTIC_POLICY", force=True)
    _validate_semantic_manifest_claims(manifest)
    emit("SEMANTIC_POLICY_VALIDATED")

    def parent_progress(progress: ParentScanProgress) -> None:
        if progress_callback is None:
            return
        progress_callback(
            IdentityBridgeValidationProgress(
                stage=f"RESCANNING_PARENT:{progress.stage}",
                elapsed_seconds=max(clock() - started, 0.0),
                files_checked=files_checked,
                files_total=files_total,
                bytes_checked=bytes_checked,
                bytes_total=bytes_total,
                rows_checked=rows_checked,
                rows_total=rows_total,
                current_file=progress.current_family,
            )
        )

    emit("RESCANNING_PARENT", force=True)
    remaining_minutes = max((deadline - clock()) / 60, 1e-9)
    try:
        parent = scan_parent_identity(
            Path(parent_package_root),
            heartbeat_seconds=heartbeat_seconds,
            max_runtime_minutes=remaining_minutes,
            progress_callback=parent_progress if progress_callback is not None else None,
            clock=clock,
        )
    except ParentIdentityScanDeadline as exc:
        raise IdentityBridgeValidationDeadline(exc.progress) from exc
    except ParentIdentityScanError as exc:
        raise IdentityBridgeValidationError("Frozen parent rescan failed") from exc
    emit("PARENT_RESCANNED", force=True)
    _validate_parent_binding(manifest, parent)

    declared_files = {row.logical_uri: row for row in manifest.bridge_files}
    emit("VALIDATING_SEMANTIC_FILES", force=True)
    alias_path = _inventory_path(inventory, "player-identity-aliases.parquet")
    dimension_path = _inventory_path(inventory, "active-player-dimension.parquet")
    try:
        current_file = "player-identity-aliases.parquet"
        alias_rows, alias_artifact = validate_alias_parquet(alias_path)
        _validate_parquet_declaration(
            declared_files[current_file], alias_artifact
        )
        files_checked += 1
        bytes_checked += alias_artifact.byte_count
        rows_checked += alias_artifact.row_count
        rows_total = manifest.totals.alias_rows + manifest.totals.active_player_rows
        emit("ALIASES_VALIDATED")

        current_file = "active-player-dimension.parquet"
        dimension_rows, dimension_artifact = validate_dimension_parquet(dimension_path)
        _validate_parquet_declaration(
            declared_files[current_file], dimension_artifact
        )
        files_checked += 1
        bytes_checked += dimension_artifact.byte_count
        rows_checked += dimension_artifact.row_count
        emit("DIMENSIONS_VALIDATED")
    except (IdentityBridgeRowError, KeyError) as exc:
        raise IdentityBridgeValidationError("Bridge Parquet authority changed") from exc

    semantic_payloads: dict[str, bytes] = {}
    for logical_uri in ("attribution.md", "source-rights.json"):
        current_file = logical_uri
        path = _inventory_path(inventory, logical_uri)
        try:
            assert_regular_independent_file(path)
            payload = path.read_bytes()
        except (OSError, ExchangePathError) as exc:
            raise IdentityBridgeValidationError(
                f"Bridge semantic file is unreadable: {logical_uri}"
            ) from exc
        _validate_byte_declaration(declared_files[logical_uri], payload)
        semantic_payloads[logical_uri] = payload
        files_checked += 1
        bytes_checked += len(payload)
        emit("SEMANTIC_FILE_VALIDATED")

    try:
        validate_rights_artifacts(
            parent.parent_root,
            actual_source_rights=semantic_payloads["source-rights.json"],
            actual_attribution=semantic_payloads["attribution.md"],
        )
    except IdentityBridgeRightsError as exc:
        raise IdentityBridgeValidationError(
            "Bridge rights/attribution differs from the parent"
        ) from exc

    emit("RECOMPUTING_JOINS", force=True)
    resolution_by_raw = _validate_parent_population(
        parent,
        alias_rows,
        dimension_rows,
    )
    _validate_namespace_inventory(manifest, alias_rows)
    _validate_source_release_binding(manifest, alias_rows)
    expected_contracts, expected_join_validation = _recompute_join_evidence(
        parent, resolution_by_raw
    )
    actual_contracts = [row.model_dump(mode="json") for row in manifest.join_contracts]
    actual_join_validation = [
        row.model_dump(mode="json") for row in manifest.join_validation
    ]
    if actual_contracts != expected_contracts:
        raise IdentityBridgeValidationError("Join contracts differ from the parent scan")
    if actual_join_validation != expected_join_validation:
        raise IdentityBridgeValidationError(
            "Join validation differs from the parent scan"
        )
    expected_y_minus_1 = _recompute_y_minus_1(parent, resolution_by_raw)
    actual_y_minus_1 = [
        row.model_dump(mode="json")
        for row in manifest.y_minus_1_prior_eligibility
    ]
    if actual_y_minus_1 != expected_y_minus_1:
        raise IdentityBridgeValidationError(
            "Y-minus-1 eligibility differs from the parent scan"
        )
    _validate_coverage(manifest, parent, dimension_rows)
    emit("JOINS_RECOMPUTED")

    current_file = None
    emit("SECURITY_SCAN", force=True)
    try:
        validate_package_security(
            root,
            ((logical_uri, path) for logical_uri, path, _info in inventory),
            row_payloads=(*alias_rows, *dimension_rows),
        )
    except IdentityBridgeSecurityError as exc:
        raise IdentityBridgeValidationError(
            "Bridge package failed the portable security scan"
        ) from exc
    _validate_manifest_limitations(manifest)
    emit("LIMITATIONS_VALIDATED")

    report = _validation_report(
        manifest=manifest,
        parent=parent,
        alias_artifact=alias_artifact,
        dimension_artifact=dimension_artifact,
        alias_rows=alias_rows,
        dimension_rows=dimension_rows,
        expected_join_validation=expected_join_validation,
        expected_y_minus_1=expected_y_minus_1,
        digest=digest,
    )
    report_payload = validation_report_bytes(report)

    inventory_names = {logical_uri for logical_uri, _path, _info in inventory}
    if inventory_names & set(trust.REQUIRED_AUXILIARY_FILES):
        emit("VALIDATING_AUXILIARY_FILES", force=True)
        if "validation-report.json" in inventory_names:
            current_file = "validation-report.json"
            report_path = _inventory_path(inventory, current_file)
            try:
                actual_report = report_path.read_bytes()
            except OSError as exc:
                raise IdentityBridgeValidationError(
                    "Validation report is unreadable"
                ) from exc
            if actual_report != report_payload:
                raise IdentityBridgeValidationError(
                    "Validation report bytes are not deterministic"
                )
            files_checked += 1
            bytes_checked += len(actual_report)
            emit("VALIDATION_REPORT_VALIDATED")

        if "README.md" in inventory_names:
            current_file = "README.md"
            readme_path = _inventory_path(inventory, current_file)
            try:
                actual_readme = readme_path.read_bytes()
            except OSError as exc:
                raise IdentityBridgeValidationError("Bridge README is unreadable") from exc
            if actual_readme != readme_bytes(manifest, report_payload):
                raise IdentityBridgeValidationError(
                    "Bridge README bytes are not deterministic"
                )
            files_checked += 1
            bytes_checked += len(actual_readme)
            emit("README_VALIDATED")

    if files_checked != len(inventory) or bytes_checked != bytes_total:
        raise IdentityBridgeValidationError(
            "Validated file/byte totals do not match the exact inventory"
        )
    current_file = None
    emit("PASS", force=True)
    return IdentityBridgeValidationResult(
        package_root=root,
        parent_package_root=parent.parent_root,
        manifest=manifest,
        report=report,
        report_bytes=report_payload,
        file_count=len(inventory),
        total_bytes=bytes_total,
        status="PASS",
        elapsed_seconds=max(clock() - started, 0.0),
    )


def _exact_inventory(
    root: Path,
    *,
    require_auxiliary: bool,
) -> tuple[tuple[str, Path, Any], ...]:
    rows: list[tuple[str, Path, Any]] = []
    actual: set[str] = set()
    for logical_uri, path in iter_relative_files(root):
        info = assert_regular_independent_file(path)
        actual.add(logical_uri)
        rows.append((logical_uri, path, info))
    expected = set(_FINAL_FILE_INVENTORY if require_auxiliary else _BASE_FILE_INVENTORY)
    valid = (
        actual == expected
        if require_auxiliary
        else set(_BASE_FILE_INVENTORY).issubset(actual)
        and actual.issubset(_FINAL_FILE_INVENTORY)
    )
    if not valid:
        missing = sorted(expected - actual)
        extra = sorted(actual - set(_FINAL_FILE_INVENTORY))
        raise IdentityBridgeValidationError(
            f"Bridge file inventory differs: missing={missing}, extra={extra}"
        )
    # V1 is deliberately a flat seven-file package.  Empty directories are not
    # invisible inventory and therefore fail closed as well.
    if any(not child.is_file() for child in root.iterdir()):
        raise IdentityBridgeValidationError("Bridge package contains a directory")
    return tuple(sorted(rows, key=lambda row: row[0]))


def _inventory_path(
    inventory: Sequence[tuple[str, Path, Any]], logical_uri: str
) -> Path:
    for candidate, path, _info in inventory:
        if candidate == logical_uri:
            return path
    raise IdentityBridgeValidationError(f"Required bridge file is absent: {logical_uri}")


def _validate_parent_binding(
    manifest: SportsIdentityBridgeManifestV1,
    parent: ParentIdentityEvidence,
) -> None:
    binding = manifest.parent_binding
    actual = (
        binding.parent_dataset_manifest_id,
        binding.parent_dataset_manifest_sha256,
        binding.parent_dataset_version_id,
        binding.parent_package_id,
        binding.parent_partition_authority_sha256,
        binding.plate_appearance_class_order,
    )
    expected = (
        parent.parent_manifest_id,
        parent.parent_manifest_sha256,
        parent.parent_dataset_version_id,
        parent.parent_package_id,
        parent.parent_partition_authority_sha256,
        parent.pa_class_order,
    )
    if actual != expected:
        raise IdentityBridgeValidationError(
            "Bridge parent binding differs from the fresh parent scan"
        )


def _validate_semantic_manifest_claims(
    manifest: SportsIdentityBridgeManifestV1,
) -> None:
    artifacts = tuple(
        (row.artifact_id, row.logical_uri, row.sha256, row.byte_count)
        for row in manifest.source_lineage.evidence_artifacts
    )
    if artifacts != trust.EXPECTED_EVIDENCE_ARTIFACTS:
        raise IdentityBridgeValidationError(
            "Manifest governed-evidence artifacts differ from frozen FD1"
        )

    try:
        expected_extraction_revision = extraction_code_revision(_PROJECT_ROOT)
        expected_publishing_revision = publishing_code_revision(_PROJECT_ROOT)
    except IdentityBridgeRevisionError as exc:
        raise IdentityBridgeValidationError(
            "Identity-bridge implementation revision cannot be reconstructed"
        ) from exc
    if manifest.source_lineage.extraction_revision != expected_extraction_revision:
        raise IdentityBridgeValidationError(
            "Manifest extraction revision differs from the package-only code authority"
        )
    if (
        manifest.source_lineage.publishing_code_revision
        != expected_publishing_revision
    ):
        raise IdentityBridgeValidationError(
            "Manifest publishing revision differs from the package-only code authority"
        )

    if tuple(manifest.source_rights.rights_control_ids) != REQUIRED_RIGHTS_IDS:
        raise IdentityBridgeValidationError(
            "Manifest rights-control IDs differ from the exact parent projection"
        )



def _validate_manifest_limitations(
    manifest: SportsIdentityBridgeManifestV1,
) -> None:
    limitations = tuple(
        (row.limitation_id, row.description, row.scope)
        for row in manifest.limitations
    )
    if limitations != trust.EXPECTED_LIMITATIONS:
        raise IdentityBridgeValidationError(
            "Manifest limitations differ from the exact V1 policy"
        )


def _validate_parquet_declaration(
    declared: BridgeFile,
    artifact: ParquetArtifact,
) -> None:
    actual = (
        artifact.logical_uri,
        artifact.file_sha256,
        artifact.byte_count,
        artifact.row_count,
        artifact.schema_fingerprint,
        artifact.row_authority_sha256,
    )
    expected = (
        declared.logical_uri,
        declared.sha256,
        declared.byte_count,
        declared.row_count,
        declared.physical_schema_fingerprint,
        declared.row_authority_sha256,
    )
    if actual != expected:
        raise IdentityBridgeValidationError(
            f"Parquet declaration changed: {artifact.logical_uri}"
        )


def _validate_byte_declaration(declared: BridgeFile, payload: bytes) -> None:
    if (
        declared.byte_count != len(payload)
        or declared.sha256 != hashlib.sha256(payload).hexdigest()
    ):
        raise IdentityBridgeValidationError(
            f"Semantic file declaration changed: {declared.logical_uri}"
        )


def _validate_parent_population(
    parent: ParentIdentityEvidence,
    alias_rows: Sequence[Mapping[str, Any]],
    dimension_rows: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    aliases_by_namespace: dict[str, dict[str, str]] = defaultdict(dict)
    namespaces_by_canonical: dict[str, set[str]] = defaultdict(set)
    for row in alias_rows:
        namespace = str(row["namespace"])
        source_id = str(row["source_player_id"])
        canonical = str(row["canonical_player_uuid"])
        aliases_by_namespace[namespace][source_id] = canonical
        namespaces_by_canonical[canonical].add(namespace)

    resolution_by_raw = aliases_by_namespace["RETROSHEET"]
    expected_raw = set(parent.all_retrosheet_player_ids)
    if set(resolution_by_raw) != expected_raw:
        raise IdentityBridgeValidationError(
            "Retrosheet aliases do not exactly cover every parent join ID"
        )
    if len(set(resolution_by_raw.values())) != len(resolution_by_raw):
        raise IdentityBridgeValidationError(
            "Parent Retrosheet IDs do not map one-to-one to canonical players"
        )

    dimensions = {str(row["canonical_player_uuid"]): row for row in dimension_rows}
    expected_dimension_ids = set(resolution_by_raw.values())
    if set(dimensions) != expected_dimension_ids:
        raise IdentityBridgeValidationError(
            "Active-player dimension differs from the parent Retrosheet population"
        )
    alias_canonical_ids = {
        str(row["canonical_player_uuid"]) for row in alias_rows
    }
    if not alias_canonical_ids.issubset(dimensions):
        raise IdentityBridgeValidationError("An alias lacks an active-player dimension")

    prior_ids = {row.canonical_player_id for row in parent.player_priors}
    if not prior_ids.issubset(dimensions):
        raise IdentityBridgeValidationError(
            "A parent player_prior canonical UUID lacks a dimension"
        )
    lahman_aliases = aliases_by_namespace["LAHMAN"]
    for prior in parent.player_priors:
        if lahman_aliases.get(prior.lahman_player_id) != prior.canonical_player_id:
            raise IdentityBridgeValidationError(
                "A parent player_prior Lahman identity differs from the bridge"
            )

    seasons_by_canonical: dict[str, set[int]] = defaultdict(set)
    player_by_canonical: dict[str, Any] = {}
    prior_rows: dict[str, list[Any]] = defaultdict(list)
    for player in parent.players:
        canonical = resolution_by_raw[player.retrosheet_player_id]
        if canonical in player_by_canonical:
            raise IdentityBridgeValidationError(
                "Multiple parent players map to one canonical dimension"
            )
        player_by_canonical[canonical] = player
        seasons_by_canonical[canonical].update(player.seasons)
    for prior in parent.player_priors:
        seasons_by_canonical[prior.canonical_player_id].add(prior.season)
        prior_rows[prior.canonical_player_id].append(prior)

    for alias in alias_rows:
        canonical = str(alias["canonical_player_uuid"])
        seasons = seasons_by_canonical.get(canonical, set())
        player = player_by_canonical.get(canonical)
        if not seasons or (
            int(alias["first_represented_season"]),
            int(alias["last_represented_season"]),
            bool(alias["primary_2023_2025"]),
        ) != (
            min(seasons),
            max(seasons),
            bool(player is not None and set(player.seasons) & {2023, 2024, 2025}),
        ):
            raise IdentityBridgeValidationError(
                "Alias represented-season context differs from the parent"
            )

    for canonical, actual in dimensions.items():
        player = player_by_canonical.get(canonical)
        seasons = seasons_by_canonical.get(canonical, set())
        if player is None or not seasons:
            raise IdentityBridgeValidationError(
                "Dimension player context is absent from the parent"
            )
        priors = prior_rows.get(canonical, [])
        eligible_seasons = [row.eligible_from_season for row in priors]
        namespaces = namespaces_by_canonical[canonical]
        other = (
            player.total_join_references
            - player.plate_appearance_batter_references
            - player.plate_appearance_pitcher_references
        )
        if other < 0:
            raise IdentityBridgeValidationError(
                "Parent player reference roles do not reconcile"
            )
        expected = {
            "canonical_player_uuid": canonical,
            "first_represented_season": min(seasons),
            "last_represented_season": max(seasons),
            "prior_history_2021_2022": any(
                row.season in {2021, 2022} for row in priors
            ),
            "primary_active_2023_2025": bool(
                set(player.seasons) & {2023, 2024, 2025}
            ),
            "has_retrosheet_alias": "RETROSHEET" in namespaces,
            "has_lahman_alias": "LAHMAN" in namespaces,
            "has_chadwick_alias": "CHADWICK" in namespaces,
            "has_mlbam_alias": "MLBAM" in namespaces,
            "has_baseball_reference_alias": "BASEBALL_REFERENCE" in namespaces,
            "has_player_prior": bool(priors),
            "minimum_player_prior_target_season": (
                min(eligible_seasons) if eligible_seasons else None
            ),
            "maximum_player_prior_target_season": (
                max(eligible_seasons) if eligible_seasons else None
            ),
            "batter_reference_count": player.plate_appearance_batter_references,
            "pitcher_reference_count": player.plate_appearance_pitcher_references,
            "other_player_reference_count": other,
            "player_prior_row_count": len(priors),
        }
        observed = {key: actual[key] for key in expected}
        if observed != expected:
            raise IdentityBridgeValidationError(
                "Active-player dimension differs from recomputed parent evidence"
            )
    return dict(resolution_by_raw)


def _namespace_inventory(
    alias_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for namespace in trust.ALLOWED_IDENTITY_NAMESPACES:
        rows = [row for row in alias_rows if row["namespace"] == namespace]
        if not rows:
            continue
        result.append(
            {
                "namespace": namespace,
                "source_keys": sorted({str(row["source_key"]) for row in rows}),
                "source_version_ids": sorted(
                    {str(row["source_version_id"]) for row in rows}
                ),
                "alias_rows": len(rows),
                "unique_source_ids": len(
                    {str(row["source_player_id"]) for row in rows}
                ),
                "canonical_players": len(
                    {str(row["canonical_player_uuid"]) for row in rows}
                ),
            }
        )
    return result


def _validate_namespace_inventory(
    manifest: SportsIdentityBridgeManifestV1,
    alias_rows: Sequence[Mapping[str, Any]],
) -> None:
    observed_namespaces = {str(row["namespace"]) for row in alias_rows}
    if observed_namespaces != set(trust.ALLOWED_IDENTITY_NAMESPACES):
        raise IdentityBridgeValidationError(
            "Bridge must contain all five governed identity namespaces"
        )
    if observed_namespaces != set(SUPPORTED_NAMESPACES):
        raise IdentityBridgeValidationError("Row/manifest namespace contracts differ")
    expected = _namespace_inventory(alias_rows)
    actual = [row.model_dump(mode="json") for row in manifest.identity_namespaces]
    if actual != expected:
        raise IdentityBridgeValidationError(
            "Manifest namespace inventory differs from alias rows"
        )


def _validate_source_release_binding(
    manifest: SportsIdentityBridgeManifestV1,
    alias_rows: Sequence[Mapping[str, Any]],
) -> None:
    ordered_releases = tuple(
        (row.source_key, row.source_version_id, row.identity_checksum)
        for row in manifest.source_lineage.source_releases
    )
    if ordered_releases != trust.EXPECTED_SOURCE_RELEASES:
        raise IdentityBridgeValidationError(
            "Manifest source-release authority differs from frozen FD1"
        )
    releases = set(ordered_releases)
    observed_alias_releases: set[tuple[str, str, str]] = set()
    for row in alias_rows:
        authority = (
            str(row["source_key"]),
            str(row["source_version_id"]),
            str(row["source_version_identity_sha256"]),
        )
        if authority not in releases:
            raise IdentityBridgeValidationError(
                "Alias source release is absent from manifest lineage"
            )
        observed_alias_releases.add(authority)
    if observed_alias_releases != set(trust.EXPECTED_ALIAS_SOURCE_RELEASES):
        raise IdentityBridgeValidationError(
            "Alias contributor releases differ from frozen FD1"
        )


def _recompute_join_evidence(
    parent: ParentIdentityEvidence,
    resolution_by_raw: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    field_evidence = {
        (row.family, row.field, row.season): row
        for row in parent.field_season_evidence
    }
    coverage = {
        (row.retrosheet_player_id, row.family): row
        for row in parent.other_family_coverage
    }
    for family, field in _CONSUMER_JOIN_FIELDS:
        evidence_rows = [
            field_evidence.get((family, field, season))
            for season in (2021, 2022, 2023, 2024, 2025)
        ]
        if any(row is None for row in evidence_rows):
            raise IdentityBridgeValidationError(
                f"Parent join evidence is incomplete for {family}.{field}"
            )
        seasons = tuple(
            season
            for season, row in zip(
                (2021, 2022, 2023, 2024, 2025), evidence_rows, strict=True
            )
            if row is not None and row.reference_count > 0
        )
        references = sum(
            field_evidence[(family, field, season)].reference_count
            for season in seasons
        )
        if family == "plate_appearance":
            raw_ids = {
                row.retrosheet_player_id
                for row in parent.plate_appearance_role_evidence
                if (field == "batter_id" and row.batter_references > 0)
                or (field == "pitcher_id" and row.pitcher_references > 0)
            }
        else:
            raw_ids = {
                raw_id
                for (raw_id, candidate_family), row in coverage.items()
                if candidate_family == family and field in row.fields
            }
        if (
            not seasons
            or references <= 0
            or not raw_ids
            or not raw_ids.issubset(resolution_by_raw)
        ):
            raise IdentityBridgeValidationError(
                f"Parent join scope differs for {family}.{field}"
            )
        contract_id = f"{family}.{field}.retrosheet-to-canonical"
        contracts.append(
            {
                "join_contract_id": contract_id,
                "source_family": family,
                "physical_field_name": field,
                "namespace": "RETROSHEET",
                "target_canonical_field": (
                    "player-identity-aliases.canonical_player_uuid"
                ),
                "join_type": "EXACT_PARENT_JOIN_KEY_TO_ALIAS",
                "null_policy": "REJECT_NULL_OR_BLANK",
                "duplicate_policy": "REQUIRE_ONE_TARGET_PER_NAMESPACE_SOURCE_ID",
                "unresolved_policy": "FAIL_CLOSED",
            }
        )
        validations.append(
            {
                "join_contract_id": contract_id,
                "seasons": list(seasons),
                "reference_count": references,
                "resolved_reference_count": references,
                "unresolved_reference_count": 0,
                "ambiguous_reference_count": 0,
                "multiply_mapped_reference_count": 0,
                "unique_source_ids": len(raw_ids),
                "unique_canonical_players": len(
                    {resolution_by_raw[value] for value in raw_ids}
                ),
                "mapping_rate": 1.0,
            }
        )

    prior_ids = {row.canonical_player_id for row in parent.player_priors}
    if not prior_ids:
        raise IdentityBridgeValidationError("Parent player_prior scope is empty")
    contract_id = "player_prior.canonical_player_id.canonical-to-dimension"
    contracts.append(
        {
            "join_contract_id": contract_id,
            "source_family": "player_prior",
            "physical_field_name": "canonical_player_id",
            "namespace": "CHADWICK",
            "target_canonical_field": (
                "active-player-dimension.canonical_player_uuid"
            ),
            "join_type": "EXACT_CANONICAL_UUID",
            "null_policy": "REJECT_NULL_OR_BLANK",
            "duplicate_policy": "ALLOW_MULTIPLE_PRIOR_ROWS_PER_CANONICAL_UUID",
            "unresolved_policy": "FAIL_CLOSED",
        }
    )
    validations.append(
        {
            "join_contract_id": contract_id,
            "seasons": sorted({row.season for row in parent.player_priors}),
            "reference_count": len(parent.player_priors),
            "resolved_reference_count": len(parent.player_priors),
            "unresolved_reference_count": 0,
            "ambiguous_reference_count": 0,
            "multiply_mapped_reference_count": 0,
            "unique_source_ids": len(prior_ids),
            "unique_canonical_players": len(prior_ids),
            "mapping_rate": 1.0,
        }
    )
    contracts.sort(key=lambda row: row["join_contract_id"])
    validations.sort(key=lambda row: row["join_contract_id"])
    return contracts, validations


def _recompute_y_minus_1(
    parent: ParentIdentityEvidence,
    resolution_by_raw: Mapping[str, str],
) -> list[dict[str, Any]]:
    prior_by_domain: dict[str, dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in parent.player_priors:
        prior_by_domain[row.domain][row.canonical_player_id].add(row.season)
    result: list[dict[str, Any]] = []
    for target_season in (2023, 2024, 2025):
        for role, field, domain in (
            ("BATTER", "batter_references", "BATTING"),
            ("PITCHER", "pitcher_references", "PITCHING"),
        ):
            total = 0
            eligible = 0
            for evidence in parent.plate_appearance_role_evidence:
                if evidence.season != target_season:
                    continue
                count = int(getattr(evidence, field))
                total += count
                canonical = resolution_by_raw[evidence.retrosheet_player_id]
                if any(
                    prior_season <= target_season - 1
                    for prior_season in prior_by_domain[domain].get(canonical, set())
                ):
                    eligible += count
            if total <= 0:
                raise IdentityBridgeValidationError(
                    f"Parent {role} role is empty for {target_season}"
                )
            result.append(
                {
                    "target_season": target_season,
                    "role": role,
                    "reference_rows": total,
                    "rows_with_eligible_prior": eligible,
                    "legitimate_missing_prior_rows": total - eligible,
                    "maximum_eligible_prior_season": target_season - 1,
                    "eligible_prior_rate": eligible / total,
                    "unresolved_identity_rows": 0,
                    "ambiguous_identity_rows": 0,
                    "multiply_mapped_identity_rows": 0,
                }
            )
    return result


def _validate_coverage(
    manifest: SportsIdentityBridgeManifestV1,
    parent: ParentIdentityEvidence,
    dimension_rows: Sequence[Mapping[str, Any]],
) -> None:
    coverage = manifest.coverage_evidence
    observed = parent.governed_reference_coverage
    primary_dimensions = sum(
        bool(row["primary_active_2023_2025"]) for row in dimension_rows
    )
    expected = {
        "canonical_identity_mapping_count": _EXPECTED_CANONICAL_MAPPING_ROWS,
        "active_unique_players_2021_2025": observed.expected_unique_player_count,
        "primary_unique_players_2023_2025": observed.expected_primary_unique_player_count,
        "portable_parent_unique_players_2021_2025": len(dimension_rows),
        "portable_parent_unique_players_2023_2025": primary_dimensions,
        "unrepresented_governed_players_2021_2025": (
            observed.expected_unique_player_count - len(dimension_rows)
        ),
        "unrepresented_governed_players_2023_2025": (
            observed.expected_primary_unique_player_count - primary_dimensions
        ),
        "active_player_references_2021_2025": observed.expected_reference_count,
        "primary_player_references_2023_2025": (
            observed.expected_primary_reference_count
        ),
        "portable_parent_player_references_2021_2025": observed.reference_count,
        "portable_parent_player_references_2023_2025": (
            observed.primary_reference_count
        ),
        "unresolved_quarantine_rows": _EXPECTED_UNRESOLVED_ROWS,
        "ambiguous_quarantine_rows": _EXPECTED_AMBIGUOUS_ROWS,
        "downstream_impacted_rows": 0,
    }
    if any(getattr(coverage, key) != value for key, value in expected.items()):
        raise IdentityBridgeValidationError(
            "Coverage evidence differs from the parent/governed anchors"
        )
    if manifest.source_lineage.canonical_identity_mapping_count != (
        _EXPECTED_CANONICAL_MAPPING_ROWS
    ):
        raise IdentityBridgeValidationError("Canonical mapping lineage count changed")


def _validation_report(
    *,
    manifest: SportsIdentityBridgeManifestV1,
    parent: ParentIdentityEvidence,
    alias_artifact: ParquetArtifact,
    dimension_artifact: ParquetArtifact,
    alias_rows: Sequence[Mapping[str, Any]],
    dimension_rows: Sequence[Mapping[str, Any]],
    expected_join_validation: Sequence[Mapping[str, Any]],
    expected_y_minus_1: Sequence[Mapping[str, Any]],
    digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": VALIDATION_REPORT_VERSION,
        "status": "PASS",
        "contract_name": manifest.contract_name,
        "contract_version": manifest.contract_version,
        "bridge_manifest_id": manifest.bridge_manifest_id,
        "bridge_manifest_sha256": digest,
        "bridge_package_id": manifest.bridge_package_id,
        "parent_dataset_manifest_sha256": parent.parent_manifest_sha256,
        "parent_partition_authority_sha256": (
            parent.parent_partition_authority_sha256
        ),
        "checks": {
            "alias_rows_canonically_sorted_and_hashed": True,
            "all_five_namespaces_present": True,
            "all_parent_player_prior_joins_exact": True,
            "all_parent_retrosheet_joins_exact": True,
            "canonical_manifest_identity": True,
            "dimension_rows_recomputed": True,
            "exact_final_file_inventory": True,
            "no_name_fuzzy_or_unresolved_aliases": True,
            "package_portable_security_scan": True,
            "parent_package_rescanned": True,
            "parquet_file_schema_and_row_authority": True,
            "rights_and_attribution_exact": True,
            "y_minus_1_recomputed": True,
        },
        "counts": {
            "active_player_rows": len(dimension_rows),
            "alias_rows": len(alias_rows),
            "final_package_files": trust.FINAL_PACKAGE_FILE_COUNT,
            "join_contracts": len(expected_join_validation),
            "join_references": sum(
                int(row["reference_count"]) for row in expected_join_validation
            ),
            "namespaces": len(manifest.identity_namespaces),
            "parent_player_prior_rows": len(parent.player_priors),
            "parent_retrosheet_players": len(parent.all_retrosheet_player_ids),
            "semantic_files": len(manifest.bridge_files),
            "y_minus_1_records": len(expected_y_minus_1),
        },
        "authorities": {
            "alias_file_sha256": alias_artifact.file_sha256,
            "alias_row_authority_sha256": alias_artifact.row_authority_sha256,
            "alias_schema_fingerprint": alias_artifact.schema_fingerprint,
            "dimension_file_sha256": dimension_artifact.file_sha256,
            "dimension_row_authority_sha256": (
                dimension_artifact.row_authority_sha256
            ),
            "dimension_schema_fingerprint": dimension_artifact.schema_fingerprint,
        },
        "identity_namespaces": _namespace_inventory(alias_rows),
        "join_validation": [dict(row) for row in expected_join_validation],
        "y_minus_1_prior_eligibility": [
            dict(row) for row in expected_y_minus_1
        ],
    }


def _self_check() -> None:
    if len(_FINAL_FILE_INVENTORY) != trust.FINAL_PACKAGE_FILE_COUNT:
        raise RuntimeError("Identity bridge final inventory anchor is inconsistent")
    if len(_BASE_FILE_INVENTORY) != 5:
        raise RuntimeError("Identity bridge staging inventory anchor is inconsistent")
    if set(SUPPORTED_NAMESPACES) != set(trust.ALLOWED_IDENTITY_NAMESPACES):
        raise RuntimeError("Identity bridge namespace anchors differ")
    if (
        _EXPECTED_UNRESOLVED_ROWS + _EXPECTED_AMBIGUOUS_ROWS != 1_203
        or _EXPECTED_CANONICAL_MAPPING_ROWS + 1_203 != 737_259
    ):
        raise RuntimeError("Identity bridge governed count anchors differ")
    if tuple(sorted(trust.EXPECTED_SOURCE_RELEASES)) != trust.EXPECTED_SOURCE_RELEASES:
        raise RuntimeError("Identity bridge source releases are not canonical")
    if not set(trust.EXPECTED_ALIAS_SOURCE_RELEASES) < set(
        trust.EXPECTED_SOURCE_RELEASES
    ):
        raise RuntimeError("Identity bridge alias source releases are invalid")
    if (
        tuple(row[0] for row in trust.EXPECTED_EVIDENCE_ARTIFACTS)
        != ("active_identity", "final_result", "quarantine_impact")
        or any(
            f"sha256-{sha256}.json" not in logical_uri or byte_count <= 0
            for _artifact_id, logical_uri, sha256, byte_count
            in trust.EXPECTED_EVIDENCE_ARTIFACTS
        )
    ):
        raise RuntimeError("Identity bridge evidence-artifact anchors are invalid")
    if (
        len(trust.EXPECTED_LIMITATIONS) != 5
        or tuple(row[0] for row in trust.EXPECTED_LIMITATIONS)
        != tuple(sorted(row[0] for row in trust.EXPECTED_LIMITATIONS))
        or any(row[2] != "IDENTITY_BRIDGE" for row in trust.EXPECTED_LIMITATIONS)
    ):
        raise RuntimeError("Identity bridge limitation anchors are invalid")
    if REQUIRED_RIGHTS_IDS != tuple(sorted(REQUIRED_RIGHTS_IDS)):
        raise RuntimeError("Identity bridge rights-control anchors are invalid")


_self_check()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the standalone package-only validator with machine-readable output."""

    parser = argparse.ArgumentParser(
        description="Validate a SPORTS_IDENTITY_BRIDGE_V1 package"
    )
    parser.add_argument("--bridge-package-root", type=Path, required=True)
    parser.add_argument("--parent-package-root", type=Path, required=True)
    parser.add_argument("--heartbeat-seconds", type=int, default=30)
    parser.add_argument("--max-runtime-minutes", type=float, default=90.0)
    parser.add_argument(
        "--allow-missing-auxiliary",
        action="store_false",
        dest="require_auxiliary",
        help=(
            "validate the five required staging files plus any already-present "
            "deterministic auxiliary files"
        ),
    )
    parser.set_defaults(require_auxiliary=True)
    arguments = parser.parse_args(argv)

    def progress(value: IdentityBridgeValidationProgress) -> None:
        payload = {"progress": asdict(value)}
        sys.stderr.buffer.write(
            canonical_json_bytes(payload, trailing_newline=True)
        )
        sys.stderr.buffer.flush()

    try:
        result = validate_identity_bridge(
            arguments.bridge_package_root,
            arguments.parent_package_root,
            require_auxiliary=arguments.require_auxiliary,
            heartbeat_seconds=arguments.heartbeat_seconds,
            max_runtime_minutes=arguments.max_runtime_minutes,
            progress_callback=progress,
        )
    except IdentityBridgeValidationDeadline as exc:
        sys.stderr.buffer.write(
            canonical_json_bytes(
                {"status": "DEADLINE", "progress": exc.progress},
                trailing_newline=True,
            )
        )
        return 75
    except (IdentityBridgeValidationError, ValueError, OSError) as exc:
        sys.stderr.buffer.write(
            canonical_json_bytes(
                {"status": "INVALID", "error": str(exc)},
                trailing_newline=True,
            )
        )
        return 2

    sys.stdout.buffer.write(
        canonical_json_bytes(
            {
                "status": result.status,
                "bridge_manifest_id": result.manifest.bridge_manifest_id,
                "bridge_manifest_sha256": result.manifest.bridge_manifest_sha256,
                "file_count": result.file_count,
                "total_bytes": result.total_bytes,
                "validation_report_sha256": hashlib.sha256(
                    result.report_bytes
                ).hexdigest(),
            },
            trailing_newline=True,
        )
    )
    return 0


__all__ = [
    "IdentityBridgeValidationDeadline",
    "IdentityBridgeValidationError",
    "IdentityBridgeValidationProgress",
    "IdentityBridgeValidationResult",
    "MANIFEST_LOGICAL_URI",
    "VALIDATION_REPORT_VERSION",
    "main",
    "validate_identity_bridge",
    "validation_report_bytes",
]


if __name__ == "__main__":
    raise SystemExit(main())
