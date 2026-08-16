"""Fail-closed loader for the finalized Gate 3C FD1 evidence set.

This module deliberately has no database, network, or provider dependency.  It
opens only the fixed project-relative artifacts named below and treats the
content-addressed bytes as the authority for exchange publication.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.gen2.fd1_recovery_manifest import (
    FD1RecoveryIntegrityError,
    RecoveryManifest,
    load_recovery_manifest,
)


CURATED_ROOT_URI = "data/curated/gen2-fd1"

RECOVERY_MANIFEST_URI = (
    ".runtime/gate3c-fd1/recovery/manifests/"
    "fd1-recovery-manifest-sha256-"
    "bbe93dae347dd0a02703d14dfb1b40b71979e8add8d8386d92880a54798143b7.json"
)
READINESS_URI = (
    ".runtime/gate3c-fd1/recovery/readiness/"
    "fd1-recovery-readiness-sha256-"
    "9b2b3b1d4c06f9abad8c2bcaff7aba87cff3a08624fe14e638da8d8d27ca2e76.json"
)
FINAL_RESULT_URI = (
    ".runtime/gate3c-fd1/recovery/reports/"
    "fd1-recovery-result-sha256-"
    "08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720.json"
)
ACTIVE_IDENTITY_URI = (
    ".runtime/gate3c-fd1/artifacts/"
    "active-identity-actual-sha256-"
    "0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07.json"
)
QUARANTINE_IMPACT_URI = (
    ".runtime/gate3c-fd1/recovery/reports/"
    "fd1-identity-quarantine-impact-sha256-"
    "cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b.json"
)

FINAL_CLOSEOUT_REPORT_URI = "docs/GATE_3C_FD1_FREE_BASEBALL_DATA_REPORT.md"
SOURCE_INVENTORY_URI = "docs/FREE_BASEBALL_DATA_SOURCE_INVENTORY.md"
ATTRIBUTION_URI = "docs/OPEN_DATA_ATTRIBUTION.md"

RECOVERY_ID = "92d6f851-815a-5a53-9b5d-6340549121c1"
RECOVERY_MANIFEST_CHECKSUM = (
    "f9e788df4f746fe749e3cd731576bab3d191bea7559eb0b6c75230149589535c"
)
RECOVERY_RESULT_CHECKSUM = (
    "f28a23bec7f29949a61e27d70ec407c86bc6c863eb6b0d5d19a0c15ace5facca"
)
WAREHOUSE_VALIDATION_CHECKSUM = (
    "30a72a1aa04e7251d384363d3490115d3135b8c691b7658aba6d73044eda301a"
)
ACTIVE_IDENTITY_DERIVATION_CHECKSUM = (
    "84b9e134b40d01143fc72d5fa30a87114c5db8d53845361826ce47dfed616a98"
)
QUARANTINE_ANALYSIS_CHECKSUM = (
    "48741790431aaba222fdb91d6fc1405479eb5a004dc1cf9f1fda42d84d6c8e5a"
)
READINESS_DECISION = "READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS"

EXPECTED_FILE_COUNT = 329
EXPECTED_ROW_COUNT = 3_738_096
EXPECTED_PARQUET_BYTES = 337_421_353
EXPECTED_POPULATIONS = {
    "strict_deployable": 1_460_755,
    "enhanced_research": 785_808,
    "prior_only": 1_491_533,
}

_GENERATION_REVISION = "8c8da2e335f6af77fd204cadc20180e6077ffeccab881ca60eed1ce4d73452b7"
_FINALIZER_REVISION = "e8ec15683f0a34696e6185fcb355a48b6bb7ef90f47268631bfe003aac0184db"
_READINESS_REVISION = "b1888fbb6aafafa3639cd0c0288c58ee7f213b43f5bc1551f6caeed1f5c56766"
_READINESS_CHECKSUM = "a917a281495989311898224747d4197b928e1a3da60065107afaa5ad58d8f1c1"
_WAREHOUSE_DERIVATION_CHECKSUM = (
    "7c04562ee46d5068392f2112fcd21d6ed8297d2c9ddd762aa74577a8e96b0b32"
)

_EXPECTED_FAMILIES = {
    "bullpen_usage": (36, 80_314, 9_902_702),
    "pitch_sequence": (36, 943_682, 80_347_024),
    "plate_appearance": (36, 913_349, 79_421_951),
    "play_transition": (36, 949_217, 88_260_346),
    "player_game_batting": (36, 356_498, 30_584_501),
    "player_game_fielding": (36, 332_159, 27_997_674),
    "player_game_pitching": (36, 104_610, 10_736_025),
    "player_prior": (5, 21_823, 2_512_357),
    "starter_usage": (36, 24_296, 4_558_900),
    "weather": (36, 12_148, 3_099_873),
}

_EXPECTED_SEASONS = {
    2021: {
        "population": "GEN2_PRIOR_ONLY_2021_2022",
        "valid_final_games": 2_429,
        "plate_appearances": 181_818,
        "transitions": 190_700,
        "identity_reference_count": 4_293_498,
        "identity_unique_player_count": 1_509,
        "strict_deployable_rows": 0,
        "enhanced_research_rows": 0,
        "prior_only_rows": 748_340,
    },
    2022: {
        "population": "GEN2_PRIOR_ONLY_2021_2022",
        "valid_final_games": 2_430,
        "plate_appearances": 182_052,
        "transitions": 187_969,
        "identity_reference_count": 4_224_978,
        "identity_unique_player_count": 1_495,
        "strict_deployable_rows": 0,
        "enhanced_research_rows": 0,
        "prior_only_rows": 743_193,
    },
    2023: {
        "population": "GEN2_PRIMARY_2023_2025",
        "valid_final_games": 2_430,
        "plate_appearances": 184_104,
        "transitions": 191_073,
        "identity_reference_count": 4_303_891,
        "identity_unique_player_count": 1_457,
        "strict_deployable_rows": 488_475,
        "enhanced_research_rows": 262_733,
        "prior_only_rows": 0,
    },
    2024: {
        "population": "GEN2_PRIMARY_2023_2025",
        "valid_final_games": 2_429,
        "plate_appearances": 182_449,
        "transitions": 189_536,
        "identity_reference_count": 4_260_240,
        "identity_unique_player_count": 1_454,
        "strict_deployable_rows": 485_300,
        "enhanced_research_rows": 261_238,
        "prior_only_rows": 0,
    },
    2025: {
        "population": "GEN2_PRIMARY_2023_2025",
        "valid_final_games": 2_430,
        "plate_appearances": 182_926,
        "transitions": 189_939,
        "identity_reference_count": 4_273_952,
        "identity_unique_player_count": 1_471,
        "strict_deployable_rows": 486_980,
        "enhanced_research_rows": 261_837,
        "prior_only_rows": 0,
    },
}

_EXPECTED_FULL_CHECKPOINTS = [
    "FILES_VALIDATED",
    "IDENTITY_STAGED",
    "IDENTITY_RECONCILED",
    "PARTITIONS_STAGED",
    "WAREHOUSE_VALIDATED",
    "FINALIZED",
]

_CONTENT_ADDRESS_PATTERN = re.compile(r"-sha256-([0-9a-f]{64})\.json\Z")


class FD1EvidenceError(RuntimeError):
    """The committed FD1 evidence set is missing or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class LocalArtifactIdentity:
    """Exact identity of one project-relative evidence artifact."""

    artifact_id: str
    logical_uri: str
    path: Path
    sha256: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class ReadinessDocumentBinding:
    """Comparison of a readiness-time document binding to its current bytes."""

    logical_uri: str
    readiness_sha256: str
    readiness_byte_count: int
    current_sha256: str
    current_byte_count: int
    matches_current: bool
    documented_historic_mismatch: bool


@dataclass(frozen=True, slots=True)
class FD1AuthoritativeEvidence:
    """Validated, read-only inputs from which the FD1 exchange is published."""

    project_root: Path
    curated_root: Path
    recovery_manifest: RecoveryManifest
    recovery_manifest_payload: dict[str, Any]
    readiness: dict[str, Any]
    final_result: dict[str, Any]
    active_identity: dict[str, Any]
    quarantine_impact: dict[str, Any]
    artifacts: tuple[LocalArtifactIdentity, ...]
    closeout_documents: tuple[LocalArtifactIdentity, ...]
    readiness_source_inventory_binding: ReadinessDocumentBinding
    readiness_attribution_binding: ReadinessDocumentBinding

    def artifact(self, artifact_id: str) -> LocalArtifactIdentity:
        """Return one identity by stable loader-local identifier."""

        for item in (*self.artifacts, *self.closeout_documents):
            if item.artifact_id == artifact_id:
                return item
        raise KeyError(artifact_id)


@dataclass(frozen=True, slots=True)
class _ExpectedArtifact:
    artifact_id: str
    logical_uri: str
    sha256: str
    byte_count: int


_JSON_ARTIFACTS = (
    _ExpectedArtifact(
        "recovery_manifest",
        RECOVERY_MANIFEST_URI,
        "bbe93dae347dd0a02703d14dfb1b40b71979e8add8d8386d92880a54798143b7",
        1_483_359,
    ),
    _ExpectedArtifact(
        "readiness",
        READINESS_URI,
        "9b2b3b1d4c06f9abad8c2bcaff7aba87cff3a08624fe14e638da8d8d27ca2e76",
        90_754,
    ),
    _ExpectedArtifact(
        "final_result",
        FINAL_RESULT_URI,
        "08d2772e4009779d1d0c485d3a5a0dfefeaab3363dc39c5a8dd17903bacc1720",
        33_302,
    ),
    _ExpectedArtifact(
        "active_identity",
        ACTIVE_IDENTITY_URI,
        "0323c940e38303f9757a9fd2f6d69db5de5b8279381513d39ffcc3331fdddc07",
        54_046,
    ),
    _ExpectedArtifact(
        "quarantine_impact",
        QUARANTINE_IMPACT_URI,
        "cf238f0b16ba362af551cb4ac63fca19686f34d661647a6e0a4872090e2b1c4b",
        2_140_874,
    ),
)

_CLOSEOUT_DOCUMENTS = (
    _ExpectedArtifact(
        "final_closeout_report",
        FINAL_CLOSEOUT_REPORT_URI,
        "955a89bc693c7726d09b0c0c3e893310ef0926b3a0e1176e751f05923e1d2a8c",
        50_688,
    ),
    _ExpectedArtifact(
        "source_inventory",
        SOURCE_INVENTORY_URI,
        "6af564e573b5ef3a1cc7c1b5d6250783a0c2b432a138e057089a3c42941c076f",
        28_216,
    ),
    _ExpectedArtifact(
        "attribution",
        ATTRIBUTION_URI,
        "40ceba36924e1ef83e48dd5cc6394e65996190688dc072b02632d60d442a9349",
        17_274,
    ),
)


def load_fd1_evidence(project_root: Path | None = None) -> FD1AuthoritativeEvidence:
    """Load and cross-check the one finalized FD1 evidence set.

    ``project_root`` exists for isolated verification and tests.  Every input
    beneath it is still selected by a module-owned, fixed relative path; the
    loader never discovers evidence or chooses a newest artifact.
    """

    root = (project_root or Path(__file__).resolve().parents[4]).resolve()
    if not root.is_dir():
        raise FD1EvidenceError("FD1 evidence project root is not a directory")

    curated_root = _fixed_path(root, CURATED_ROOT_URI)
    if not curated_root.is_dir():
        raise FD1EvidenceError("The authoritative FD1 curated root is missing")

    parsed: dict[str, dict[str, Any]] = {}
    artifact_identities: list[LocalArtifactIdentity] = []
    for expected in _JSON_ARTIFACTS:
        payload, identity = _load_json_artifact(root, expected)
        parsed[expected.artifact_id] = payload
        artifact_identities.append(identity)

    manifest_path = artifact_identities[0].path
    try:
        recovery_manifest = load_recovery_manifest(
            manifest_path,
            expected_file_count=EXPECTED_FILE_COUNT,
        )
    except (FD1RecoveryIntegrityError, OSError, TypeError, ValueError) as exc:
        raise FD1EvidenceError("The finalized recovery manifest is invalid") from exc
    if recovery_manifest.to_dict() != parsed["recovery_manifest"]:
        raise FD1EvidenceError("RecoveryManifest parsing changed the manifest payload")

    document_identities = tuple(
        _load_document_identity(root, expected) for expected in _CLOSEOUT_DOCUMENTS
    )
    documents = {item.artifact_id: item for item in document_identities}
    identities = {item.artifact_id: item for item in artifact_identities}

    readiness = parsed["readiness"]
    final_result = parsed["final_result"]
    active_identity = parsed["active_identity"]
    quarantine = parsed["quarantine_impact"]

    _validate_manifest(recovery_manifest)
    _validate_readiness(readiness, identities)
    _validate_final_result(final_result)
    _validate_active_identity(active_identity)
    _validate_quarantine(quarantine, identities)
    inventory_binding, attribution_binding = _validate_document_bindings(
        readiness,
        documents,
    )

    return FD1AuthoritativeEvidence(
        project_root=root,
        curated_root=curated_root,
        recovery_manifest=recovery_manifest,
        recovery_manifest_payload=parsed["recovery_manifest"],
        readiness=readiness,
        final_result=final_result,
        active_identity=active_identity,
        quarantine_impact=quarantine,
        artifacts=tuple(artifact_identities),
        closeout_documents=document_identities,
        readiness_source_inventory_binding=inventory_binding,
        readiness_attribution_binding=attribution_binding,
    )


def _load_json_artifact(
    root: Path,
    expected: _ExpectedArtifact,
) -> tuple[dict[str, Any], LocalArtifactIdentity]:
    path = _fixed_path(root, expected.logical_uri)
    encoded = _read_exact_bytes(path, expected)
    match = _CONTENT_ADDRESS_PATTERN.search(path.name)
    if match is None or match.group(1) != expected.sha256:
        raise FD1EvidenceError(f"{expected.artifact_id} has an invalid content-addressed name")
    try:
        text = encoded.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FD1EvidenceError(f"{expected.artifact_id} is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise FD1EvidenceError(f"{expected.artifact_id} must contain a JSON object")
    return payload, _identity(expected, path)


def _load_document_identity(root: Path, expected: _ExpectedArtifact) -> LocalArtifactIdentity:
    path = _fixed_path(root, expected.logical_uri)
    encoded = _read_exact_bytes(path, expected)
    try:
        encoded.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise FD1EvidenceError(f"{expected.artifact_id} is not UTF-8") from exc
    return _identity(expected, path)


def _read_exact_bytes(path: Path, expected: _ExpectedArtifact) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise FD1EvidenceError(f"{expected.artifact_id} is not a regular local file")
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise FD1EvidenceError(f"{expected.artifact_id} cannot be read") from exc
    digest = hashlib.sha256(encoded).hexdigest()
    if digest != expected.sha256 or len(encoded) != expected.byte_count:
        raise FD1EvidenceError(f"{expected.artifact_id} byte identity is not authoritative")
    return encoded


def _identity(expected: _ExpectedArtifact, path: Path) -> LocalArtifactIdentity:
    return LocalArtifactIdentity(
        artifact_id=expected.artifact_id,
        logical_uri=expected.logical_uri,
        path=path,
        sha256=expected.sha256,
        byte_count=expected.byte_count,
    )


def _fixed_path(root: Path, logical_uri: str) -> Path:
    pure = PurePosixPath(logical_uri)
    if (
        pure.is_absolute()
        or not pure.parts
        or "\\" in logical_uri
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise FD1EvidenceError("A fixed evidence path is not root-confined")
    path = root.joinpath(*pure.parts)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise FD1EvidenceError("A fixed evidence path escapes the project root") from exc
    return path


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FD1EvidenceError(f"JSON evidence contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise FD1EvidenceError(f"JSON evidence contains forbidden constant {value}")


def _validate_manifest(manifest: RecoveryManifest) -> None:
    _require(manifest.recovery_id, RECOVERY_ID, "manifest recovery_id")
    _require(
        manifest.manifest_checksum,
        RECOVERY_MANIFEST_CHECKSUM,
        "manifest semantic checksum",
    )
    _require(
        manifest.generation_lineage.code_revision,
        _GENERATION_REVISION,
        "manifest generation revision",
    )
    _require(
        manifest.finalizer_lineage.code_revision,
        _FINALIZER_REVISION,
        "manifest finalizer revision",
    )
    _require(manifest.aggregate.file_count, EXPECTED_FILE_COUNT, "manifest file total")
    _require(manifest.aggregate.row_count, EXPECTED_ROW_COUNT, "manifest row total")
    _require(
        manifest.aggregate.byte_count,
        EXPECTED_PARQUET_BYTES,
        "manifest byte total",
    )
    actual_families = {
        item.family: (item.file_count, item.row_count, item.byte_count)
        for item in manifest.aggregate.families
    }
    _require(actual_families, _EXPECTED_FAMILIES, "manifest family totals")


def _validate_readiness(
    readiness: dict[str, Any],
    identities: dict[str, LocalArtifactIdentity],
) -> None:
    for path, expected in (
        (("schema_version",), "gate3c_fd1_recovery_readiness_v1"),
        (("decision",), READINESS_DECISION),
        (("modeling_can_proceed",), True),
        (("operator_revision",), _READINESS_REVISION),
        (("terminal_decision_checksum",), _READINESS_CHECKSUM),
        (("recovery", "recovery_id"), RECOVERY_ID),
        (("recovery", "status"), "FINALIZED"),
        (("recovery", "manifest_checksum"), RECOVERY_MANIFEST_CHECKSUM),
        (("recovery", "result_checksum"), RECOVERY_RESULT_CHECKSUM),
        (("recovery", "warehouse_validation_checksum"), WAREHOUSE_VALIDATION_CHECKSUM),
        (
            ("recovery", "active_identity_derivation_checksum"),
            ACTIVE_IDENTITY_DERIVATION_CHECKSUM,
        ),
        (("recovery", "generation_code_revision"), _GENERATION_REVISION),
        (("recovery", "finalizer_code_revision"), _FINALIZER_REVISION),
        (("recovery", "full_checkpoints"), _EXPECTED_FULL_CHECKPOINTS),
        (("readiness_result", "decision"), READINESS_DECISION),
        (("readiness_result", "modeling_can_proceed"), True),
        (
            ("warehouse_evidence", "derivation_checksum"),
            _WAREHOUSE_DERIVATION_CHECKSUM,
        ),
        (
            ("warehouse_evidence", "identity_evidence_checksum"),
            ACTIVE_IDENTITY_DERIVATION_CHECKSUM,
        ),
    ):
        _require_at(readiness, path, expected)

    population = _at(readiness, "population")
    for key, expected in (
        ("partition_count", EXPECTED_FILE_COUNT),
        ("row_count", EXPECTED_ROW_COUNT),
        ("row_lineage_checked_count", EXPECTED_ROW_COUNT),
        ("file_bytes", EXPECTED_PARQUET_BYTES),
    ):
        _require_at(population, (key,), expected, prefix="readiness.population")
    _require_at(
        population,
        ("partitions_by_family",),
        {family: totals[0] for family, totals in _EXPECTED_FAMILIES.items()},
        prefix="readiness.population",
    )
    _require_at(
        population,
        ("rows_by_family",),
        {family: totals[1] for family, totals in _EXPECTED_FAMILIES.items()},
        prefix="readiness.population",
    )

    separation = _at(readiness, "readiness_result", "population_separation")
    for key, expected in (
        ("strict_row_count", EXPECTED_POPULATIONS["strict_deployable"]),
        ("enhanced_row_count", EXPECTED_POPULATIONS["enhanced_research"]),
        ("prior_only_row_count", EXPECTED_POPULATIONS["prior_only"]),
        ("tier_candidate_row_count", EXPECTED_ROW_COUNT),
        ("tier_eligible_row_count", EXPECTED_ROW_COUNT),
        ("historical_actual_lineup_rows", 218_664),
        ("isolated_enhanced_lineup_rows", 218_664),
        ("historical_actual_starter_rows", 24_296),
        ("isolated_enhanced_starter_rows", 24_296),
        ("leakage", []),
    ):
        _require_at(separation, (key,), expected, prefix="readiness population separation")

    seasons = _at(readiness, "readiness_input", "seasons")
    if not isinstance(seasons, list) or [row.get("season") for row in seasons] != list(
        _EXPECTED_SEASONS
    ):
        raise FD1EvidenceError("Readiness seasons are incomplete or unordered")
    for row in seasons:
        season = row["season"]
        for key, expected in _EXPECTED_SEASONS[season].items():
            _require_at(row, (key,), expected, prefix=f"readiness season {season}")
        _require_at(
            row,
            ("resolved_identity_reference_count",),
            _EXPECTED_SEASONS[season]["identity_reference_count"],
            prefix=f"readiness season {season}",
        )
        _require_at(
            row,
            ("resolved_identity_unique_player_count",),
            _EXPECTED_SEASONS[season]["identity_unique_player_count"],
            prefix=f"readiness season {season}",
        )
        for empty_key in (
            "blocked_partition_ids",
            "unresolved_identity_ids",
            "unsafe_taxonomy_event_ids",
        ):
            _require_at(row, (empty_key,), [], prefix=f"readiness season {season}")

    for key in (
        "checksum_issues",
        "conservation_conflicts",
        "game_conflicts",
        "identity_conflicts",
        "integrity_failures",
    ):
        _require_at(readiness, ("readiness_result", key), [])

    for key in (
        "database_writes",
        "governed_parquet_writes",
        "network_requests",
        "provider_calls",
        "provider_transports_constructed",
        "raw_source_scans",
    ):
        _require_at(readiness, ("execution_boundary", key), 0)
    _require_at(readiness, ("execution_boundary", "manifest_list_only"), True)
    for path, expected in (
        (("execution_boundary", "database", "transaction_read_only"), True),
        (("execution_boundary", "database", "transaction_rolled_back"), True),
        (("execution_boundary", "database", "rejected_statements"), 0),
        (("execution_boundary", "database", "checkpoint_count"), 6),
        (("execution_boundary", "database", "quarantine_rows"), 1_203),
        (("execution_boundary", "database", "resolved_mapping_rows"), 736_056),
        (
            ("execution_boundary", "database", "identity_outcomes"),
            {"INSERT": 736_056, "UNRESOLVED": 1_203},
        ),
        (
            ("execution_boundary", "database", "canonical_counts"),
            {
                "CHECKPOINT": 329,
                "DERIVED_USE_DECISION": 62,
                "INGESTION_RUN": 404,
                "PARTITION": 329,
            },
        ),
    ):
        _require_at(readiness, path, expected)

    result_identity = identities["final_result"]
    for key, expected in (
        ("logical_uri", result_identity.logical_uri),
        ("sha256", result_identity.sha256),
        ("byte_count", result_identity.byte_count),
    ):
        _require_at(readiness, ("recovery", "result", key), expected)
    active_identity = identities["active_identity"]
    for key, expected in (
        ("logical_uri", active_identity.logical_uri),
        ("sha256", active_identity.sha256),
        ("byte_count", active_identity.byte_count),
    ):
        _require_at(readiness, ("source_artifacts", "active_identity", key), expected)
    _require_at(
        readiness,
        ("source_artifacts", "recovery_manifest", "logical_uri"),
        identities["recovery_manifest"].logical_uri,
    )
    _require_at(
        readiness,
        ("source_artifacts", "recovery_manifest", "manifest_checksum"),
        RECOVERY_MANIFEST_CHECKSUM,
    )


def _validate_final_result(result: dict[str, Any]) -> None:
    for path, expected in (
        (("schema_version",), "gate3c_fd1_recovery_result_v1"),
        (("run_id",), RECOVERY_ID),
        (("status",), "FINALIZED"),
        (("last_completed_stage",), "FINALIZED"),
        (("mode",), "finalize"),
        (("recovery_manifest_checksum",), RECOVERY_MANIFEST_CHECKSUM),
        (("result_checksum",), RECOVERY_RESULT_CHECKSUM),
        (("generation_code_revision",), _GENERATION_REVISION),
        (("finalizer_code_revision",), _FINALIZER_REVISION),
        (("files_reused",), EXPECTED_FILE_COUNT),
        (("finalized_partitions",), EXPECTED_FILE_COUNT),
        (("bytes_reused",), EXPECTED_PARQUET_BYTES),
        (("parquet_rows",), EXPECTED_ROW_COUNT),
        (("mappings_staged",), 737_259),
        (("mappings_inserted",), 736_056),
        (("mappings_reused",), 0),
        (("unresolved_mappings",), 1_187),
        (("source_declared_conflicts",), 16),
        (("mapping_conflicts",), 0),
        (("provider_calls",), 0),
        (("network_requests",), 0),
        (("provider_transports_constructed",), 0),
        (("validation", "schema_version"), "gate3c_fd1_warehouse_validation_v1"),
        (("validation", "validation_checksum"), WAREHOUSE_VALIDATION_CHECKSUM),
        (("validation", "recovery_id"), RECOVERY_ID),
        (("validation", "manifest_checksum"), RECOVERY_MANIFEST_CHECKSUM),
        (("validation", "file_count"), EXPECTED_FILE_COUNT),
        (("validation", "manifest_file_count"), EXPECTED_FILE_COUNT),
        (("validation", "partitions"), EXPECTED_FILE_COUNT),
        (("validation", "row_count"), EXPECTED_ROW_COUNT),
        (("validation", "byte_count"), EXPECTED_PARQUET_BYTES),
        (("validation", "passed"), True),
        (("validation", "population_complete"), True),
        (("validation", "publishable"), True),
    ):
        _require_at(result, path, expected)

    expected_metrics = {
        "automatic_runner_placements": 3_002,
        "future_evidence_rows": 0,
        "prohibited_evidence_rows": 0,
        "row_checksum_rows": 3_716_273,
        "strict_same_game_eligible_rows": 0,
        "timing_risk_rows": 1_979_324,
        "timing_risk_strict_overlap_rows": 0,
        "transition_adjacencies_checked": 937_069,
        "transition_games": 12_148,
        "transition_rows": 949_217,
        "unique_row_checksums": 3_716_273,
    }
    _require_at(result, ("validation", "metrics"), expected_metrics)
    checks = _at(result, "validation", "checks")
    expected_checks = [
        "manifest_file_bindings",
        "trusted_dry_run_ledger",
        "declared_schema_registry",
        "collective_population",
        "row_checksum_uniqueness",
        "plate_appearance_taxonomy",
        "transition_conservation",
        "timing_risk_separation",
        "active_identity_threshold",
    ]
    if not isinstance(checks, list) or [row.get("name") for row in checks] != expected_checks:
        raise FD1EvidenceError("Warehouse validation checks are incomplete or unordered")
    if any(row.get("status") != "PASS" for row in checks):
        raise FD1EvidenceError("A warehouse validation check did not pass")


def _validate_active_identity(identity: dict[str, Any]) -> None:
    for path, expected in (
        (("schema_version",), "gate3c_fd1_active_identity_actual_operator_v1"),
        (
            ("active_identity_coverage_report", "schema_version"),
            "gate3c_fd1_active_identity_coverage_v1",
        ),
        (
            ("active_identity_coverage_report", "derivation_checksum"),
            ACTIVE_IDENTITY_DERIVATION_CHECKSUM,
        ),
        (("active_identity_coverage_report", "primary_threshold"), 0.995),
        (("active_identity_coverage_report", "primary_meets_threshold"), True),
        (
            ("active_identity_coverage_report", "primary_reference_meets_threshold"),
            True,
        ),
        (("active_identity_coverage_report", "primary_unique_meets_threshold"), True),
        (("active_identity_coverage_report", "name_based_match_count"), 0),
        (("active_identity_coverage_report", "affected_players"), []),
        (
            ("active_identity_coverage_report", "overall_coverage", "reference_count"),
            21_356_559,
        ),
        (
            (
                "active_identity_coverage_report",
                "overall_coverage",
                "unique_retrosheet_players",
            ),
            2_693,
        ),
        (("execution_boundary", "database_mutations"), 0),
        (("execution_boundary", "name_based_matches"), 0),
        (("execution_boundary", "provider_transports_constructed"), 0),
        (("execution_boundary", "network", "external_requests"), 0),
        (("execution_boundary", "network", "blocked_external_attempts"), 0),
    ):
        _require_at(identity, path, expected)

    seasons = _at(identity, "active_identity_coverage_report", "season_coverage")
    if not isinstance(seasons, list) or [row.get("seasons") for row in seasons] != [
        [year] for year in _EXPECTED_SEASONS
    ]:
        raise FD1EvidenceError("Active identity seasons are incomplete or unordered")
    for row in seasons:
        season = row["seasons"][0]
        expected = _EXPECTED_SEASONS[season]
        _require_at(row, ("reference_count",), expected["identity_reference_count"])
        _require_at(
            row,
            ("unique_retrosheet_players",),
            expected["identity_unique_player_count"],
        )
        _validate_exact_identity_rates(row, f"active identity season {season}")
    _validate_exact_identity_rates(
        _at(identity, "active_identity_coverage_report", "overall_coverage"),
        "overall active identity",
    )


def _validate_exact_identity_rates(row: dict[str, Any], label: str) -> None:
    rates = _at(row, "exact_link_rates")
    if not isinstance(rates, list) or len(rates) != 3:
        raise FD1EvidenceError(f"{label} exact-link rates are incomplete")
    for rate in rates:
        for key, expected in (
            ("exact_reference_rate", 1.0),
            ("exact_unique_rate", 1.0),
            ("conflict_reference_count", 0),
            ("conflict_unique_link_count", 0),
            ("unresolved_reference_count", 0),
            ("unresolved_unique_link_count", 0),
        ):
            _require_at(rate, (key,), expected, prefix=label)


def _validate_quarantine(
    quarantine: dict[str, Any],
    identities: dict[str, LocalArtifactIdentity],
) -> None:
    for path, expected in (
        (("schema_version",), "gate3c_fd1_identity_quarantine_impact_v1"),
        (("analysis_identity_checksum",), QUARANTINE_ANALYSIS_CHECKSUM),
        (("run", "run_id"), RECOVERY_ID),
        (("run", "status"), "FINALIZED"),
        (("run", "last_completed_stage"), "FINALIZED"),
        (("run", "recovery_manifest_semantic_checksum"), RECOVERY_MANIFEST_CHECKSUM),
        (("run", "identity_spool_row_count"), 737_259),
        (("run", "provider_calls_persisted"), 0),
        (("summary", "staged_mapping_rows"), 737_259),
        (("summary", "canonical_mapping_rows_inserted"), 736_056),
        (("summary", "canonical_mapping_rows_reused"), 0),
        (("summary", "quarantine_rows"), 1_203),
        (("summary", "source_declared_unresolved_rows"), 1_187),
        (("summary", "source_declared_ambiguity_rows"), 16),
        (("summary", "blocking_conflicts"), 0),
        (("summary", "quarantined_rows_with_existing_mapping_collision"), 0),
        (("summary", "quarantined_target_mappings_present_in_canonical_table"), 0),
        (("manifest_dependency_scan", "files_scanned"), EXPECTED_FILE_COUNT),
        (("manifest_dependency_scan", "rows_scanned"), EXPECTED_ROW_COUNT),
        (("manifest_dependency_scan", "bytes_in_bound_manifest"), EXPECTED_PARQUET_BYTES),
        (("manifest_dependency_scan", "namespace_correct_match_rows"), 0),
        (("active_identity_evidence", "affected_players"), 0),
        (("active_identity_evidence", "name_based_match_count"), 0),
    ):
        _require_at(quarantine, path, expected)

    for key in (
        "affected_2023_2025_primary_rows",
        "dependent_games",
        "dependent_lineup_player_rows",
        "dependent_plate_appearances",
        "dependent_player_prior_rows",
        "dependent_reliever_appearances",
        "dependent_starters",
        "full_games_made_training_ineligible",
        "partitions_made_training_ineligible",
    ):
        _require_at(quarantine, ("dependent_impact", key), 0)
    for key in (
        "atlas_accesses",
        "database_writes",
        "names_read_or_inferred",
        "network_requests",
        "parquet_writes",
        "provider_calls",
        "raw_source_scans",
    ):
        _require_at(quarantine, ("method", key), 0)
    _require_at(quarantine, ("method", "database_transaction_read_only"), True)

    manifest_identity = identities["recovery_manifest"]
    result_identity = identities["final_result"]
    active_identity = identities["active_identity"]
    for prefix, identity in (
        (("inputs", "recovery_manifest"), manifest_identity),
        (("inputs", "finalized_recovery_evidence"), result_identity),
        (("inputs", "active_identity_artifact"), active_identity),
    ):
        _require_at(quarantine, (*prefix, "logical_path"), identity.logical_uri)
        _require_at(quarantine, (*prefix, "artifact_sha256"), identity.sha256)
        _require_at(quarantine, (*prefix, "artifact_bytes"), identity.byte_count)


def _validate_document_bindings(
    readiness: dict[str, Any],
    documents: dict[str, LocalArtifactIdentity],
) -> tuple[ReadinessDocumentBinding, ReadinessDocumentBinding]:
    inventory = _document_binding(
        _at(readiness, "source_artifacts", "source_inventory"),
        documents["source_inventory"],
        documented_historic_mismatch=True,
    )
    _require(
        inventory.readiness_sha256,
        "695562057c9786cf9eadb0ee232c7f966ab8da0fd601c94669ab7d9be890bdf1",
        "historic readiness source-inventory checksum",
    )
    _require(
        inventory.readiness_byte_count,
        28_135,
        "historic readiness source-inventory byte count",
    )
    if inventory.matches_current:
        raise FD1EvidenceError("The documented historic source-inventory mismatch disappeared")

    attribution = _document_binding(
        _at(readiness, "source_artifacts", "attribution"),
        documents["attribution"],
        documented_historic_mismatch=False,
    )
    if not attribution.matches_current:
        raise FD1EvidenceError("Readiness attribution binding does not match the current document")
    return inventory, attribution


def _document_binding(
    readiness_binding: dict[str, Any],
    current: LocalArtifactIdentity,
    *,
    documented_historic_mismatch: bool,
) -> ReadinessDocumentBinding:
    logical_uri = _at(readiness_binding, "logical_uri")
    readiness_sha256 = _at(readiness_binding, "sha256")
    readiness_byte_count = _at(readiness_binding, "byte_count")
    _require(logical_uri, current.logical_uri, f"{current.artifact_id} logical URI")
    return ReadinessDocumentBinding(
        logical_uri=logical_uri,
        readiness_sha256=readiness_sha256,
        readiness_byte_count=readiness_byte_count,
        current_sha256=current.sha256,
        current_byte_count=current.byte_count,
        matches_current=(
            readiness_sha256 == current.sha256
            and readiness_byte_count == current.byte_count
        ),
        documented_historic_mismatch=documented_historic_mismatch,
    )


def _at(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            joined = ".".join(path)
            raise FD1EvidenceError(f"Evidence is missing required field {joined}")
        current = current[key]
    return current


def _require_at(
    value: Any,
    path: tuple[str, ...],
    expected: Any,
    *,
    prefix: str = "evidence",
) -> None:
    _require(_at(value, *path), expected, f"{prefix}.{'.'.join(path)}")


def _require(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise FD1EvidenceError(f"{label} does not match finalized FD1 evidence")


__all__ = [
    "ACTIVE_IDENTITY_URI",
    "ATTRIBUTION_URI",
    "CURATED_ROOT_URI",
    "FD1AuthoritativeEvidence",
    "FD1EvidenceError",
    "FINAL_CLOSEOUT_REPORT_URI",
    "FINAL_RESULT_URI",
    "LocalArtifactIdentity",
    "QUARANTINE_IMPACT_URI",
    "READINESS_URI",
    "RECOVERY_MANIFEST_URI",
    "ReadinessDocumentBinding",
    "SOURCE_INVENTORY_URI",
    "load_fd1_evidence",
]
