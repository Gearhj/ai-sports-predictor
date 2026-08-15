"""Read-only post-commit proof for the Gate 3C-FD1 publication operator.

``publish_actual.py`` deliberately finalizes its immutable evidence before the
caller-owned SQL transaction is committed.  Its ledger must therefore continue
to say ``PENDING_CALLER_COMMIT``.  This module is the narrow second phase: it
binds the captured execute result to that exact ledger, re-hashes every operator
artifact, checks the committed SQL identities, validates every Parquet file in
the dedicated FD1 store, and projects a new content-addressed operator document
whose transaction is explicitly post-commit verified.

The verifier has no provider, transport, model, odds, or position capability.
Its database access is guarded to SELECT statements; it never calls commit,
flush, or a governed-store write method, and rolls back only to close its
read-only snapshot.  The only write performed by the public entry point is
immutable evidence beneath the caller-supplied runtime artifact directory.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, date, datetime
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.gen2.fd1_readiness_closeout import (
    FD1_ACTUAL_OPERATOR_LOGICAL_PATH,
    FD1_POSTCOMMIT_MODULE_LOGICAL_PATH,
    FD1_POSTCOMMIT_SCHEMA_VERSION,
    FD1_POSTCOMMIT_SCRIPT_LOGICAL_PATH,
    FD1_POSTCOMMIT_STATUS,
    OPERATOR_SCHEMA_VERSION,
    REQUIRED_SOURCE_KEYS,
    canonical_fd1_closeout_checksum,
)
from app.gen2.fd1_registry_sync import FD1_SOURCE_DEFINITIONS
from app.gen2.fd1_warehouse import (
    FD1_INGESTION_JOB,
    FD1_INGESTION_SOURCE,
    FD1_WAREHOUSE_SCHEMA_VERSION,
    build_fd1_code_revision,
)
from app.historical.checkpoints import HistoricalCheckpoint
from app.gen2.open_data_registry import (
    OpenDataIdentityMappingSpec,
    build_identity_mapping_identity,
    build_source_release_identity,
    canonical_sha256,
)
from app.gen2.parquet_store import (
    HistoricalEventParquetStore,
    HistoricalEventPartitionArtifact,
)
from app.models import (
    ExistingEvidenceDerivedUseDecision,
    HistoricalEventPartition,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    IngestionRun,
    OpenDataDownloadManifest,
    OpenDataIdentityMapping,
    RunStatus,
)


# Backwards-compatible public name retained for the ignored runtime wrapper and
# focused tests; the canonical path contract lives with readiness validation.
FD1_POSTCOMMIT_OPERATOR_LOGICAL_PATH = FD1_POSTCOMMIT_SCRIPT_LOGICAL_PATH
_PUBLICATION_NAMESPACE = UUID("418ebff6-c8f3-4a91-970d-7ec71c39c944")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRETRANSACTION_NAME = re.compile(
    r"^publish-actual-execute-pretransaction-closeout-"
    r"sha256-([0-9a-f]{64})\.json$"
)
_CONTENT_ADDRESSED_NAME = re.compile(
    r"^publish-actual-execute-(?:2025-retrosheet-source-evidence|"
    r"2025-schedule-fingerprint-bridge|"
    r"2025-asp-retrosheet-full-reconciliation)-"
    r"sha256-([0-9a-f]{64})\.(?:json|jsonl)$"
)
_POSTCOMMIT_NAME = re.compile(
    r"^publish-actual-execute-postcommit-closeout-"
    r"sha256-([0-9a-f]{64})\.json$"
)
_EXECUTE_RESULT_NAME = re.compile(
    r"^publish-actual-execute-result-sha256-([0-9a-f]{64})\.json$"
)
_REPARSE_POINT = 0x400


class FD1PostcommitError(RuntimeError):
    """The committed warehouse cannot be proven without guessing."""


def canonical_fd1_postcommit_checksum(value: Any) -> str:
    """Return the verifier's canonical JSON checksum."""

    return canonical_fd1_closeout_checksum(value)


def build_actual_operator_code_revision(operator_bytes: bytes) -> str:
    """Reproduce the ignored publication operator's combined code revision."""

    if not isinstance(operator_bytes, bytes) or not operator_bytes:
        raise FD1PostcommitError("publication operator bytes are absent")
    stable_revision = build_fd1_code_revision()
    digest = sha256()
    digest.update(b"stable-fd1-code-revision\0")
    digest.update(bytes.fromhex(stable_revision))
    digest.update(FD1_ACTUAL_OPERATOR_LOGICAL_PATH.encode("utf-8"))
    digest.update(b"\0")
    digest.update(len(operator_bytes).to_bytes(8, "big"))
    digest.update(operator_bytes)
    return digest.hexdigest()


def project_committed_operator_closeout(
    pretransaction: Mapping[str, Any],
    execute_result: Mapping[str, Any],
    *,
    postcommit_verification: Mapping[str, Any],
) -> dict[str, Any]:
    """Project, but never mutate, an exact committed operator document.

    File, code, SQL, and Parquet evidence must already have been verified by the
    caller.  This pure boundary makes the only allowed semantic change to the
    original operator ledger and revalidates all execute-result bindings.
    """

    ledger = _mapping(pretransaction, "pretransaction ledger")
    result = _mapping(execute_result, "execute result")
    proof = _mapping(postcommit_verification, "postcommit verification")
    _validate_pretransaction_ledger(ledger)
    _validate_execute_binding(ledger, result)
    if proof.get("schema_version") != FD1_POSTCOMMIT_SCHEMA_VERSION:
        raise FD1PostcommitError("postcommit verification schema is invalid")
    if proof.get("all_checks_passed") is not True:
        raise FD1PostcommitError("postcommit verification did not pass")
    declared = str(proof.get("derivation_checksum", ""))
    _require_sha256(declared, "postcommit derivation checksum")
    checksum_payload = dict(proof)
    checksum_payload.pop("derivation_checksum", None)
    if canonical_fd1_postcommit_checksum(checksum_payload) != declared:
        raise FD1PostcommitError("postcommit derivation checksum changed")
    _validate_postcommit_proof_binding(ledger, result, proof)

    committed = deepcopy(dict(ledger))
    committed["success_scope"] = "COMMITTED_PUBLICATION_AND_POSTCOMMIT_VERIFICATION"
    transaction = dict(_mapping(committed.get("transaction"), "transaction"))
    transaction.update(
        {
            "status_at_closeout": FD1_POSTCOMMIT_STATUS,
            "committed": True,
            "rolled_back": False,
            "committed_by_publication_function": False,
            "commit_observed_by_postcommit_verifier": True,
        }
    )
    committed["transaction"] = transaction
    committed["postcommit_verification"] = deepcopy(dict(proof))
    return committed


def _validate_postcommit_proof_binding(
    ledger: Mapping[str, Any],
    result: Mapping[str, Any],
    proof: Mapping[str, Any],
) -> None:
    pretransaction = _mapping(
        proof.get("pretransaction_ledger"), "verified pretransaction ledger"
    )
    if pretransaction.get("canonical_sha256") != canonical_fd1_postcommit_checksum(
        ledger
    ):
        raise FD1PostcommitError("postcommit proof is bound to another ledger")
    pretransaction_sha = _require_sha256(
        str(pretransaction.get("sha256", "")), "verified ledger checksum"
    )
    _positive_int(pretransaction.get("bytes"), "verified ledger bytes")
    pretransaction_path = PurePosixPath(
        _logical_uri(
            pretransaction.get("logical_path"), "verified ledger logical path"
        )
    )
    match = _PRETRANSACTION_NAME.fullmatch(pretransaction_path.name)
    if match is None or match.group(1) != pretransaction_sha:
        raise FD1PostcommitError("verified ledger path is not content-addressed")

    execute = _mapping(proof.get("execute_result"), "verified execute result")
    if execute.get("canonical_sha256") != canonical_fd1_postcommit_checksum(result):
        raise FD1PostcommitError("postcommit proof is bound to another execute result")
    execute_sha = _require_sha256(
        str(execute.get("sha256", "")), "verified execute-result checksum"
    )
    _positive_int(execute.get("bytes"), "verified execute-result bytes")
    execute_path = PurePosixPath(
        _logical_uri(execute.get("logical_path"), "verified execute-result path")
    )
    match = _EXECUTE_RESULT_NAME.fullmatch(execute_path.name)
    if match is None or match.group(1) != execute_sha:
        raise FD1PostcommitError("verified execute-result path is not content-addressed")

    code = _mapping(proof.get("operator_code"), "verified operator code")
    for key in ("sha256", "stable_code_revision", "combined_code_revision"):
        _require_sha256(str(code.get(key, "")), f"operator code {key}")
    _positive_int(code.get("bytes"), "operator code bytes")
    if (
        code.get("logical_path") != FD1_ACTUAL_OPERATOR_LOGICAL_PATH
        or code.get("combined_code_revision")
        != _mapping(ledger.get("publication"), "publication").get("code_revision")
    ):
        raise FD1PostcommitError("postcommit operator-code binding changed")
    verifier = _mapping(proof.get("verifier_code"), "verifier code")
    for key in ("module_sha256", "script_sha256"):
        _require_sha256(str(verifier.get(key, "")), f"verifier code {key}")
    for key in ("module_bytes", "script_bytes"):
        _positive_int(verifier.get(key), f"verifier code {key}")
    if verifier.get("script_logical_path") != FD1_POSTCOMMIT_OPERATOR_LOGICAL_PATH:
        raise FD1PostcommitError("postcommit verifier-script binding changed")
    if verifier.get("module_logical_path") != FD1_POSTCOMMIT_MODULE_LOGICAL_PATH:
        raise FD1PostcommitError("postcommit verifier-module binding changed")

    expected_artifacts = []
    for key in (
        "retrosheet_source_evidence",
        "schedule_fingerprint_bridge",
        "asp_retrosheet_full_reconciliation",
    ):
        source = _mapping(ledger.get(key), f"operator {key}")
        expected_artifacts.append(
            (
                key,
                source.get("logical_path"),
                source.get("sha256"),
                source.get("bytes"),
            )
        )
    observed_artifacts = []
    for raw in _sequence(proof.get("referenced_artifacts"), "verified artifacts"):
        row = _mapping(raw, "verified artifact")
        _require_sha256(str(row.get("sha256", "")), "artifact checksum")
        _require_sha256(
            str(row.get("canonical_sha256", "")), "artifact canonical checksum"
        )
        _positive_int(row.get("bytes"), "artifact bytes")
        observed_artifacts.append(
            (
                row.get("name"),
                row.get("logical_path"),
                row.get("sha256"),
                row.get("bytes"),
            )
        )
    if observed_artifacts != expected_artifacts:
        raise FD1PostcommitError("postcommit artifact binding changed")

    publication = _mapping(ledger.get("publication"), "publication")
    source_registry = _mapping(
        proof.get("source_registry"), "verified source registry"
    )
    registered_downloads = _sequence(
        ledger.get("registered_downloads_used"), "operator registered downloads"
    )
    if not registered_downloads or any(
        not isinstance(item, Mapping) for item in registered_downloads
    ):
        raise FD1PostcommitError("operator registered-download evidence is invalid")
    _require_sha256(
        str(source_registry.get("population_checksum", "")),
        "source-registry population checksum",
    )
    if (
        source_registry.get("source_version_count") != len(FD1_SOURCE_DEFINITIONS)
        or source_registry.get("registered_download_count")
        != len(registered_downloads)
        or source_registry.get("operator_contributor_source_keys")
        != sorted(REQUIRED_SOURCE_KEYS)
        or source_registry.get("run_governance_source_keys")
        != sorted(FD1_SOURCE_DEFINITIONS)
    ):
        raise FD1PostcommitError("postcommit source-registry proof changed")
    sql = _mapping(proof.get("sql"), "verified SQL")
    parquet = _mapping(proof.get("parquet"), "verified Parquet")
    for row, name in ((sql, "SQL"), (parquet, "Parquet")):
        _require_sha256(
            str(row.get("population_checksum", "")), f"{name} population checksum"
        )
    expected_partitions = _positive_int(
        publication.get("batches_seen"), "published batches"
    )
    expected_rows = _positive_int(
        publication.get("parquet_rows"), "published Parquet rows"
    )
    expected_mappings = _positive_int(
        publication.get("identity_mappings_seen"), "published identity mappings"
    )
    identity = _mapping(ledger.get("identity"), "identity")
    identity_batches = _positive_int(identity.get("batches"), "identity batches")
    expected_runs = expected_partitions + identity_batches
    if (
        sql.get("ingestion_run_count") != expected_runs
        or sql.get("partition_run_count") != expected_partitions
        or sql.get("identity_run_count") != identity_batches
        or sql.get("partition_count") != expected_partitions
        or sql.get("checkpoint_count") != expected_partitions
        or sql.get("partition_row_count") != expected_rows
        or sql.get("identity_mapping_count") != expected_mappings
        or parquet.get("partition_count") != expected_partitions
        or parquet.get("row_count") != expected_rows
        or parquet.get("all_sql_rows_validated") is not True
        or parquet.get("all_store_files_accounted_for") is not True
    ):
        raise FD1PostcommitError("postcommit SQL/Parquet counts changed")
    derived_use_decisions = _positive_int(
        sql.get("derived_use_decision_count"),
        "verified derived-use decision count",
    )
    derived_use_created = _nonnegative_int(
        publication.get("derived_use_rows_created"),
        "created derived-use decisions",
    )
    derived_use_observations = derived_use_created + _nonnegative_int(
        publication.get("derived_use_rows_reused"),
        "reused derived-use decisions",
    )
    if (
        derived_use_decisions > derived_use_observations
        or derived_use_created > derived_use_decisions
    ):
        raise FD1PostcommitError("postcommit derived-use counts changed")
    prohibited = _mapping(
        proof.get("prohibited_activity"), "postcommit prohibited activity"
    )
    zeroes = (
        "network_requests",
        "database_writes",
        "governed_parquet_writes",
        "raw_source_scans",
        "subprocesses",
    )
    if any(prohibited.get(key) != 0 for key in zeroes):
        raise FD1PostcommitError("postcommit proof reports prohibited activity")
    false_fields = (
        "provider_transport_constructed",
        "mlb_or_savant_automation",
        "predictive_model_fit_or_promotion",
        "odds_acquisition",
        "position_population",
    )
    if any(prohibited.get(key) is not False for key in false_fields):
        raise FD1PostcommitError("postcommit proof reports prohibited capability use")


def verify_and_publish_fd1_postcommit(
    session: Session,
    *,
    project_root: Path,
    execute_result_path: Path,
    operator_script_path: Path,
    verifier_script_path: Path,
    output_directory: Path,
    expected_store_root: Path,
) -> tuple[Path, dict[str, Any]]:
    """Verify committed state and publish one immutable post-commit JSON proof."""

    root = _safe_directory(Path(project_root), Path(project_root), "project root")
    evidence_directory = _safe_directory(
        Path(output_directory), root, "postcommit output directory"
    )
    result_path = _safe_regular_file(
        Path(execute_result_path), root / ".runtime" / "gate3c-fd1", "execute result"
    )
    result_bytes = result_path.read_bytes()
    execute_result = _load_json_object(result_bytes, "execute result")
    ledger_logical = _logical_uri(execute_result.get("ledger"), "execute ledger")
    ledger_path = _safe_project_file(root, ledger_logical, "pretransaction ledger")
    ledger_bytes = ledger_path.read_bytes()
    ledger_sha = sha256(ledger_bytes).hexdigest()
    _validate_content_addressed_name(
        ledger_path, ledger_sha, _PRETRANSACTION_NAME, "pretransaction ledger"
    )
    pretransaction = _load_json_object(ledger_bytes, "pretransaction ledger")
    _validate_pretransaction_ledger(pretransaction)
    _validate_execute_binding(pretransaction, execute_result)

    script = _safe_regular_file(
        Path(operator_script_path), root / ".runtime" / "gate3c-fd1", "operator script"
    )
    if script.relative_to(root).as_posix() != FD1_ACTUAL_OPERATOR_LOGICAL_PATH:
        raise FD1PostcommitError("publication operator path changed")
    operator_bytes = script.read_bytes()
    operator_sha = sha256(operator_bytes).hexdigest()
    stable_revision = build_fd1_code_revision()
    combined_revision = build_actual_operator_code_revision(operator_bytes)
    publication = _mapping(pretransaction.get("publication"), "publication")
    if publication.get("code_revision") != combined_revision:
        raise FD1PostcommitError("publication code revision does not match current bytes")
    if execute_result.get("code_revision") != combined_revision:
        raise FD1PostcommitError("execute result code revision does not match current bytes")
    module_path = _safe_regular_file(Path(__file__), root / "server", "verifier module")
    if module_path.relative_to(root).as_posix() != FD1_POSTCOMMIT_MODULE_LOGICAL_PATH:
        raise FD1PostcommitError("postcommit verifier module path changed")
    verifier_script = _safe_regular_file(
        Path(verifier_script_path),
        root / ".runtime" / "gate3c-fd1",
        "verifier script",
    )
    if verifier_script.relative_to(root).as_posix() != FD1_POSTCOMMIT_OPERATOR_LOGICAL_PATH:
        raise FD1PostcommitError("postcommit verifier script path changed")
    module_bytes = module_path.read_bytes()
    verifier_script_bytes = verifier_script.read_bytes()

    artifact_evidence = _validate_referenced_artifacts(root, pretransaction)
    store_logical = _logical_uri(
        _mapping(pretransaction.get("store"), "store").get("logical_root"),
        "governed store root",
    )
    ledger_store_root = _safe_directory(
        root.joinpath(*PurePosixPath(store_logical).parts), root, "store root"
    )
    expected_root = _safe_directory(Path(expected_store_root), root, "expected store root")
    if ledger_store_root != expected_root:
        raise FD1PostcommitError("operator store root differs from the expected FD1 root")

    if session.in_transaction():
        raise FD1PostcommitError("postcommit verifier requires a fresh database transaction")
    if session.new or session.dirty or session.deleted:
        raise FD1PostcommitError("postcommit verifier requires a clean SQLAlchemy session")
    try:
        with _select_only(session), session.no_autoflush:
            if session.get_bind().dialect.name == "postgresql":
                session.execute(text("SET TRANSACTION READ ONLY"))
            source_summary, source_rows = _audit_sources(session, pretransaction)
            sql_summary, partitions = _audit_sql(
                session,
                pretransaction,
                source_rows=source_rows,
                combined_revision=combined_revision,
            )
            parquet_summary = _audit_parquet(
                ledger_store_root,
                partitions=partitions,
                source_rows=source_rows,
                pretransaction=pretransaction,
            )
    finally:
        # End the read-only snapshot without changing committed state.
        session.rollback()
    if session.new or session.dirty or session.deleted:
        raise FD1PostcommitError("postcommit verification changed SQLAlchemy state")

    proof: dict[str, Any] = {
        "schema_version": FD1_POSTCOMMIT_SCHEMA_VERSION,
        "all_checks_passed": True,
        "pretransaction_ledger": {
            "logical_path": ledger_logical,
            "sha256": ledger_sha,
            "canonical_sha256": canonical_fd1_postcommit_checksum(pretransaction),
            "bytes": len(ledger_bytes),
        },
        "execute_result": {
            "logical_path": (
                evidence_directory.relative_to(root).as_posix()
                + "/publish-actual-execute-result-sha256-"
                + sha256(result_bytes).hexdigest()
                + ".json"
            ),
            "sha256": sha256(result_bytes).hexdigest(),
            "canonical_sha256": canonical_fd1_postcommit_checksum(execute_result),
            "bytes": len(result_bytes),
        },
        "operator_code": {
            "logical_path": FD1_ACTUAL_OPERATOR_LOGICAL_PATH,
            "sha256": operator_sha,
            "bytes": len(operator_bytes),
            "stable_code_revision": stable_revision,
            "combined_code_revision": combined_revision,
        },
        "verifier_code": {
            "module_logical_path": module_path.relative_to(root).as_posix(),
            "module_sha256": sha256(module_bytes).hexdigest(),
            "module_bytes": len(module_bytes),
            "script_logical_path": FD1_POSTCOMMIT_OPERATOR_LOGICAL_PATH,
            "script_sha256": sha256(verifier_script_bytes).hexdigest(),
            "script_bytes": len(verifier_script_bytes),
        },
        "referenced_artifacts": artifact_evidence,
        "source_registry": source_summary,
        "sql": sql_summary,
        "parquet": parquet_summary,
        "prohibited_activity": {
            "network_requests": 0,
            "provider_transport_constructed": False,
            "mlb_or_savant_automation": False,
            "predictive_model_fit_or_promotion": False,
            "odds_acquisition": False,
            "position_population": False,
            "database_writes": 0,
            "governed_parquet_writes": 0,
            "raw_source_scans": 0,
            "subprocesses": 0,
        },
    }
    proof["derivation_checksum"] = canonical_fd1_postcommit_checksum(proof)
    committed = project_committed_operator_closeout(
        pretransaction,
        execute_result,
        postcommit_verification=proof,
    )
    _publish_bytes_evidence(
        result_bytes,
        output_directory=evidence_directory,
        project_root=root,
        filename=(
            "publish-actual-execute-result-sha256-"
            f"{sha256(result_bytes).hexdigest()}.json"
        ),
        pattern=_EXECUTE_RESULT_NAME,
        label="execute result evidence",
    )
    output = _publish_json_evidence(
        committed,
        output_directory=evidence_directory,
        project_root=root,
    )
    return output, committed


def _validate_pretransaction_ledger(ledger: Mapping[str, Any]) -> None:
    if ledger.get("schema_version") != OPERATOR_SCHEMA_VERSION:
        raise FD1PostcommitError("pretransaction operator schema is invalid")
    if (
        ledger.get("success") is not True
        or ledger.get("mode") != "execute"
        or ledger.get("sanitized") is not True
        or ledger.get("success_scope")
        != "PRETRANSACTION_VALIDATION_AND_ARTIFACT_FINALIZATION"
    ):
        raise FD1PostcommitError("pretransaction ledger is not a successful execute ledger")
    transaction = _mapping(ledger.get("transaction"), "pretransaction transaction")
    expected = {
        "single_publish_call": True,
        "status_at_closeout": "PENDING_CALLER_COMMIT",
        "committed": False,
        "rolled_back": False,
        "committed_by_publication_function": False,
        "final_result_reported_by_operator_stdout": True,
    }
    if any(transaction.get(key) != value for key, value in expected.items()):
        raise FD1PostcommitError("pretransaction status proof changed")
    prohibited = _mapping(ledger.get("prohibited_activity"), "prohibited activity")
    if prohibited.get("network_requests") != 0:
        raise FD1PostcommitError("pretransaction ledger reports network activity")
    for key in (
        "provider_transport_constructed",
        "mlb_or_savant_automation",
        "predictive_model_fit_or_promotion",
        "odds_acquisition",
        "position_population",
    ):
        if prohibited.get(key) is not False:
            raise FD1PostcommitError(f"pretransaction ledger reports prohibited {key}")


def _validate_execute_binding(
    ledger: Mapping[str, Any], result: Mapping[str, Any]
) -> None:
    if (
        result.get("success") is not True
        or result.get("mode") != "execute"
        or result.get("transaction_committed") is not True
        or result.get("transaction_rolled_back") is not False
        or result.get("artifacts_finalized_before_transaction_resolution") is not True
        or result.get("network_requests") != 0
    ):
        raise FD1PostcommitError("captured execute result does not prove a commit")
    publication = _mapping(ledger.get("publication"), "publication")
    for key in (
        "code_revision",
        "batches_seen",
        "parquet_rows",
        "identity_mappings_seen",
    ):
        if result.get(key) != publication.get(key):
            raise FD1PostcommitError(f"execute result differs from publication at {key}")
    if publication.get("mode") != "execute":
        raise FD1PostcommitError("publication summary is not execute mode")
    if result.get("park_factors_formable") is not True:
        raise FD1PostcommitError("execute result says park factors are not formable")
    park = _mapping(
        _mapping(ledger.get("readiness_evidence"), "readiness evidence").get(
            "prior_period_park_factor_formability"
        ),
        "park factor formability",
    )
    if result.get("park_factor_count") != park.get("factor_count"):
        raise FD1PostcommitError("execute result park-factor count changed")

    source_evidence = _mapping(
        ledger.get("retrosheet_source_evidence"), "source evidence"
    )
    if result.get("retrosheet_source_evidence") != source_evidence.get("logical_path"):
        raise FD1PostcommitError("execute result points at different source evidence")
    pairs = (
        ("schedule_fingerprint_bridge", "schedule_fingerprint_bridge"),
        (
            "asp_retrosheet_full_reconciliation",
            "asp_retrosheet_full_reconciliation",
        ),
    )
    for result_key, ledger_key in pairs:
        reported = _mapping(result.get(result_key), f"execute {result_key}")
        expected = _mapping(ledger.get(ledger_key), f"ledger {ledger_key}")
        for key in ("logical_path", "sha256", "bytes", "summary"):
            if reported.get(key) != expected.get(key):
                raise FD1PostcommitError(
                    f"execute result differs from {ledger_key} at {key}"
                )


def _validate_referenced_artifacts(
    project_root: Path, ledger: Mapping[str, Any]
) -> list[dict[str, Any]]:
    specifications = (
        (
            "retrosheet_source_evidence",
            "jsonl",
            "2025-retrosheet-source-evidence",
        ),
        (
            "schedule_fingerprint_bridge",
            "json",
            "2025-schedule-fingerprint-bridge",
        ),
        (
            "asp_retrosheet_full_reconciliation",
            "json",
            "2025-asp-retrosheet-full-reconciliation",
        ),
    )
    result: list[dict[str, Any]] = []
    for key, kind, artifact_name in specifications:
        declared = _mapping(ledger.get(key), f"{key} artifact")
        logical = _logical_uri(declared.get("logical_path"), f"{key} logical path")
        expected_sha = _require_sha256(str(declared.get("sha256", "")), f"{key} sha256")
        expected_bytes = _positive_int(declared.get("bytes"), f"{key} bytes")
        path = _safe_project_file(project_root, logical, key)
        payload_sha, payload_bytes = _hash_file(path)
        if payload_sha != expected_sha or payload_bytes != expected_bytes:
            raise FD1PostcommitError(f"{key} artifact bytes changed")
        _validate_content_addressed_name(path, payload_sha, _CONTENT_ADDRESSED_NAME, key)
        if path.name != (
            f"publish-actual-execute-{artifact_name}-"
            f"sha256-{payload_sha}.{kind}"
        ):
            raise FD1PostcommitError(f"{key} content-addressed name changed")
        if kind == "jsonl":
            records, canonical_checksum = _validate_jsonl(path)
            if records != _positive_int(declared.get("record_count"), "source record count"):
                raise FD1PostcommitError("source-evidence record count changed")
        else:
            document = _load_json_object(path.read_bytes(), key)
            canonical_checksum = canonical_fd1_postcommit_checksum(document)
            summary = _mapping(declared.get("summary"), f"{key} summary")
            if any(document.get(name) != value for name, value in summary.items()):
                raise FD1PostcommitError(f"{key} summary differs from artifact")
            records = None
        result.append(
            {
                "name": key,
                "logical_path": logical,
                "sha256": payload_sha,
                "canonical_sha256": canonical_checksum,
                "bytes": payload_bytes,
                "record_count": records,
            }
        )
    return result


def _audit_sources(
    session: Session, ledger: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[UUID, HistoricalSourceVersion]]:
    declared = _mapping(ledger.get("source_versions"), "source versions")
    if set(declared) != set(REQUIRED_SOURCE_KEYS):
        raise FD1PostcommitError("postcommit source-version set is not exact")
    ids: list[UUID] = []
    for key, raw in declared.items():
        row = _mapping(raw, f"source {key}")
        try:
            ids.append(UUID(str(row.get("id"))))
        except (TypeError, ValueError) as exc:
            raise FD1PostcommitError(f"source {key} has an invalid UUID") from exc
    contributor_rows = tuple(
        session.scalars(
            select(HistoricalSourceVersion)
            .where(HistoricalSourceVersion.id.in_(ids))
            .order_by(HistoricalSourceVersion.source_key)
        )
    )
    if len(contributor_rows) != len(declared):
        raise FD1PostcommitError("a committed source version is missing")
    contributor_by_key = {row.source_key: row for row in contributor_rows}
    if set(contributor_by_key) != set(declared) or len(contributor_by_key) != len(
        contributor_rows
    ):
        raise FD1PostcommitError("committed source versions are not one-to-one")
    for key, raw in declared.items():
        expected = _mapping(raw, f"source {key}")
        row = contributor_by_key[key]
        if (
            str(row.id) != expected.get("id")
            or row.dataset_version != expected.get("dataset_version")
            or row.identity_checksum != expected.get("identity_checksum")
            or row.source_mode != expected.get("source_mode")
        ):
            raise FD1PostcommitError(f"committed source {key} differs from the ledger")

    # Ingestion-run identity deliberately binds the complete five-source frozen
    # governance catalog, even though this operator consumes bytes from only four
    # of them.  Prove the fifth registry row independently instead of weakening
    # the persisted run metadata to the contributor subset.
    all_rows: list[HistoricalSourceVersion] = []
    for key, definition in sorted(FD1_SOURCE_DEFINITIONS.items()):
        expected_identity = build_source_release_identity(definition.release_spec())
        matches = tuple(
            session.scalars(
                select(HistoricalSourceVersion).where(
                    HistoricalSourceVersion.source_key == key,
                    HistoricalSourceVersion.dataset_version
                    == definition.dataset_version,
                    HistoricalSourceVersion.source_mode == "LIVE",
                )
            )
        )
        if len(matches) != 1 or matches[0].identity_checksum != expected_identity:
            raise FD1PostcommitError(
                f"frozen run-governance source {key} is missing or changed"
            )
        all_rows.append(matches[0])
    by_id = {row.id: row for row in all_rows}
    by_key = {row.source_key: row for row in all_rows}
    if len(by_id) != len(FD1_SOURCE_DEFINITIONS) or len(by_key) != len(by_id):
        raise FD1PostcommitError("run-governance source versions are not one-to-one")

    downloads = tuple(
        _mapping(item, "registered download")
        for item in _sequence(ledger.get("registered_downloads_used"), "downloads")
    )
    identities = [
        _require_sha256(
            str(item.get("download_identity_checksum", "")), "download identity"
        )
        for item in downloads
    ]
    persisted = tuple(
        session.scalars(
            select(OpenDataDownloadManifest)
            .where(OpenDataDownloadManifest.identity_checksum.in_(identities))
            .order_by(OpenDataDownloadManifest.identity_checksum)
        )
    )
    if len(persisted) != len(downloads) or len(set(identities)) != len(identities):
        raise FD1PostcommitError("registered download population changed")
    by_identity = {row.identity_checksum: row for row in persisted}
    for item in downloads:
        identity = str(item["download_identity_checksum"])
        row = by_identity.get(identity)
        if row is None:
            raise FD1PostcommitError("a registered download is missing")
        expected_source = by_key.get(str(item.get("source_key")))
        fields = {
            "source_version_id": expected_source.id if expected_source else None,
            "source_key": item.get("source_key"),
            "object_key": item.get("object_key"),
            "source_release": item.get("source_release"),
            "upstream_revision": item.get("upstream_revision"),
            "content_length": item.get("content_length"),
            "payload_sha256": item.get("payload_sha256"),
            "archive_sha256": item.get("archive_sha256"),
            "raw_logical_uri": item.get("raw_logical_uri"),
        }
        if any(getattr(row, name) != value for name, value in fields.items()):
            raise FD1PostcommitError("a registered download differs from the ledger")
    digest_rows = [
        {
            "source_key": row.source_key,
            "id": str(row.id),
            "identity_checksum": row.identity_checksum,
        }
        for row in all_rows
    ] + [
        {
            "source_key": row.source_key,
            "object_key": row.object_key,
            "identity_checksum": row.identity_checksum,
            "payload_sha256": row.payload_sha256,
        }
        for row in persisted
    ]
    return (
        {
            "source_version_count": len(all_rows),
            "registered_download_count": len(persisted),
            "operator_contributor_source_keys": sorted(declared),
            "run_governance_source_keys": sorted(by_key),
            "population_checksum": canonical_fd1_postcommit_checksum(digest_rows),
        },
        by_id,
    )


def _audit_sql(
    session: Session,
    ledger: Mapping[str, Any],
    *,
    source_rows: Mapping[UUID, HistoricalSourceVersion],
    combined_revision: str,
) -> tuple[dict[str, Any], tuple[HistoricalEventPartition, ...]]:
    publication = _mapping(ledger.get("publication"), "publication")
    identity = _mapping(ledger.get("identity"), "identity")
    expected_partitions = _sum_created_reused(
        publication, "partition_rows", "partition rows"
    )
    expected_checkpoints = _sum_created_reused(
        publication, "checkpoint_rows", "checkpoint rows"
    )
    expected_runs = _sum_created_reused(
        publication, "ingestion_run_rows", "ingestion runs"
    )
    expected_mappings = _sum_created_reused(
        publication, "identity_mapping_rows", "identity mapping rows"
    )
    expected_batches = _positive_int(publication.get("batches_seen"), "batches seen")
    expected_parquet_rows = _positive_int(publication.get("parquet_rows"), "parquet rows")
    expected_identity_count = _positive_int(
        publication.get("identity_mappings_seen"), "identity mappings seen"
    )
    identity_batches = _positive_int(identity.get("batches"), "identity batches")
    if (
        expected_partitions != expected_batches
        or expected_checkpoints != expected_batches
        or expected_runs != expected_batches + identity_batches
        or expected_mappings != expected_identity_count
        or identity.get("mappings") != expected_identity_count
    ):
        raise FD1PostcommitError("publication SQL counters do not reconcile")

    runs = tuple(
        row
        for row in session.scalars(
            select(IngestionRun)
            .where(
                IngestionRun.source == FD1_INGESTION_SOURCE,
                IngestionRun.job_name == FD1_INGESTION_JOB,
                IngestionRun.schema_version == FD1_WAREHOUSE_SCHEMA_VERSION,
            )
            .order_by(IngestionRun.id)
        )
        if isinstance(row.source_metadata, Mapping)
        and row.source_metadata.get("code_revision") == combined_revision
    )
    if len(runs) != expected_runs:
        raise FD1PostcommitError("committed FD1 ingestion-run count changed")
    source_identities = {
        row.source_key: row.identity_checksum for row in source_rows.values()
    }
    partition_runs: dict[UUID, IngestionRun] = {}
    identity_run_count = 0
    identity_run_records = 0
    run_digest = sha256()
    for row in runs:
        metadata = _mapping(row.source_metadata, "ingestion run metadata")
        if row.status != RunStatus.SUCCEEDED or row.records_processed <= 0:
            raise FD1PostcommitError("an FD1 ingestion run is not durably succeeded")
        if (
            metadata.get("source_version_identity_checksums")
            != dict(sorted(source_identities.items()))
            or metadata.get("provider_transport_constructed") is not False
            or metadata.get("network_requests") != 0
            or metadata.get("transaction_committed_by_operator") is not False
        ):
            raise FD1PostcommitError("an ingestion-run governance proof changed")
        unit_kind = str(metadata.get("unit_kind", ""))
        unit_checksum = _require_sha256(
            str(metadata.get("unit_identity_checksum", "")), "run unit identity"
        )
        publication_identity = canonical_sha256(
            {
                "schema_version": FD1_WAREHOUSE_SCHEMA_VERSION,
                "job_name": FD1_INGESTION_JOB,
                "code_revision": combined_revision,
                "unit_kind": unit_kind,
                "unit_identity_checksum": unit_checksum,
                "sources": dict(sorted(source_identities.items())),
            }
        )
        if (
            metadata.get("publication_identity_checksum") != publication_identity
            or row.id
            != uuid5(_PUBLICATION_NAMESPACE, f"ingestion:{publication_identity}")
        ):
            raise FD1PostcommitError("an ingestion-run identity is invalid")
        if unit_kind == "identity_mapping":
            identity_run_count += 1
            identity_run_records += row.records_processed
            result = _mapping(metadata.get("result"), "identity run result")
            if result.get("identity_mapping_batch_checksum") != unit_checksum:
                raise FD1PostcommitError("identity run result checksum changed")
        elif unit_kind.startswith("partition:"):
            partition_runs[row.id] = row
        else:
            raise FD1PostcommitError("an ingestion run has an unknown unit kind")
        _digest_record(
            run_digest,
            {
                "id": str(row.id),
                "records_processed": row.records_processed,
                "unit_kind": unit_kind,
                "unit_identity_checksum": unit_checksum,
                "publication_identity_checksum": publication_identity,
            },
        )
    if (
        len(partition_runs) != expected_partitions
        or identity_run_count != identity_batches
        or identity_run_records != expected_identity_count
    ):
        raise FD1PostcommitError("committed ingestion-run populations do not reconcile")

    checkpoints = tuple(
        session.scalars(
            select(HistoryBackfillCheckpoint)
            .where(HistoryBackfillCheckpoint.ingestion_run_id.in_(partition_runs))
            .order_by(HistoryBackfillCheckpoint.id)
        )
    )
    if len(checkpoints) != expected_checkpoints:
        raise FD1PostcommitError("committed checkpoint count changed")
    checkpoints_by_id = {row.id: row for row in checkpoints}
    if len(checkpoints_by_id) != len(checkpoints):
        raise FD1PostcommitError("committed checkpoints repeat an identity")
    observed_checkpoint_runs: set[UUID] = set()
    for row in checkpoints:
        run = partition_runs.get(row.ingestion_run_id)
        quality = _mapping(row.quality_summary, "checkpoint quality")
        if row.ingestion_run_id in observed_checkpoint_runs:
            raise FD1PostcommitError("a partition run has more than one checkpoint")
        observed_checkpoint_runs.add(row.ingestion_run_id)
        if (
            run is None
            or row.status != "SUCCEEDED"
            or row.code_revision != combined_revision
            or row.source_mode != "LIVE"
            or row.temporal_semantics != "RETROSPECTIVE_RECONSTRUCTION"
            or row.records_received != row.records_accepted
            or row.records_rejected != 0
            or row.duplicate_count != 0
            or row.attempt_count != 1
            or row.retry_count != 0
            or quality.get("provider_transport_constructed") is not False
            or quality.get("network_requests") != 0
            or quality.get("batch_checksum")
            != run.source_metadata.get("unit_identity_checksum")
        ):
            raise FD1PostcommitError("a committed checkpoint proof changed")
        partition_coordinate = row.sequence - row.season * 10_000
        checkpoint_month, batch_number = divmod(partition_coordinate, 100)
        if not 1 <= checkpoint_month <= 12 or batch_number <= 0:
            raise FD1PostcommitError("a committed checkpoint sequence is invalid")
        source = source_rows.get(row.source_version_id)
        if source is None:
            raise FD1PostcommitError("a checkpoint references another source")
        query = canonical_sha256(
            {
                "schema_version": FD1_WAREHOUSE_SCHEMA_VERSION,
                "source_version_identity_checksum": source.identity_checksum,
                "family": row.stream_key.removeprefix("fd1:"),
                "season": row.season,
                "month": checkpoint_month,
                "batch_number": batch_number,
                "batch_checksum": quality.get("batch_checksum"),
                "raw_contributor_checksum": quality.get(
                    "raw_contributor_checksum"
                ),
                "code_revision": combined_revision,
            }
        )
        if (
            row.query_fingerprint != query
            or row.id != uuid5(_PUBLICATION_NAMESPACE, f"checkpoint:{query}")
        ):
            raise FD1PostcommitError("a committed checkpoint identity is invalid")
        checkpoint_value = HistoricalCheckpoint(
            checkpoint_id=str(row.id),
            ingestion_run_id=str(row.ingestion_run_id),
            source_version_id=str(row.source_version_id),
            parent_checkpoint_id=(
                str(row.parent_checkpoint_id) if row.parent_checkpoint_id else None
            ),
            stream_key=row.stream_key,
            sequence=row.sequence,
            season=row.season,
            event_start_at=_aware_datetime(row.event_start_at),
            event_end_at=_aware_datetime(row.event_end_at),
            cursor_before=row.cursor_before,
            cursor_after=row.cursor_after,
            query_fingerprint=row.query_fingerprint,
            status=row.status,
            requested_at=_aware_datetime(row.requested_at),
            completed_at=_aware_datetime(row.completed_at),
            attempt_count=row.attempt_count,
            retry_count=row.retry_count,
            records_received=row.records_received,
            records_accepted=row.records_accepted,
            records_rejected=row.records_rejected,
            duplicate_count=row.duplicate_count,
            raw_references=tuple(row.raw_references or ()),
            raw_checksums=tuple(row.raw_checksums or ()),
            next_resume_at=_aware_datetime(row.next_resume_at),
            schema_version=row.schema_version,
            code_revision=row.code_revision,
            quality_summary=dict(quality),
            error_type=row.error_type,
            error_message=row.error_message,
            source_mode=row.source_mode,
            temporal_semantics=row.temporal_semantics,
            identity_checksum="",
        )
        if row.identity_checksum != canonical_fd1_postcommit_checksum(
            checkpoint_value
        ):
            raise FD1PostcommitError("a committed checkpoint checksum is invalid")
    if observed_checkpoint_runs != set(partition_runs):
        raise FD1PostcommitError("partition runs and checkpoints are not one-to-one")

    partitions = tuple(
        session.scalars(
            select(HistoricalEventPartition)
            .where(HistoricalEventPartition.checkpoint_id.in_(checkpoints_by_id))
            .order_by(HistoricalEventPartition.logical_uri)
        )
    )
    if len(partitions) != expected_partitions:
        raise FD1PostcommitError("committed partition metadata count changed")
    expected_partition_keys = _expected_partition_keys(ledger)
    observed_partition_keys: set[tuple[int, int, str]] = set()
    partition_rows = 0
    for row in partitions:
        checkpoint = checkpoints_by_id.get(row.checkpoint_id)
        run = partition_runs.get(checkpoint.ingestion_run_id) if checkpoint else None
        key = (row.season, row.month, row.event_family)
        if key in observed_partition_keys:
            raise FD1PostcommitError("committed partition key is duplicated")
        observed_partition_keys.add(key)
        expected = expected_partition_keys.get(key)
        if checkpoint is None or run is None:
            raise FD1PostcommitError("committed partition lineage changed")
        checkpoint_coordinate = checkpoint.sequence - checkpoint.season * 10_000
        checkpoint_month, checkpoint_batch = divmod(checkpoint_coordinate, 100)
        contributor_uris = tuple(
            str(item.get("logical_uri")) for item in row.raw_contributor_manifest
        )
        contributor_hashes = tuple(
            str(item.get("file_sha256")) for item in row.raw_contributor_manifest
        )
        if (
            row.code_revision != combined_revision
            or row.source_version_id not in source_rows
            or row.source_version_id != checkpoint.source_version_id
            or row.row_count != checkpoint.records_accepted
            or row.event_family != checkpoint.stream_key.removeprefix("fd1:")
            or row.month != checkpoint_month
            or checkpoint_batch <= 0
            or row.raw_contributor_checksum
            != checkpoint.quality_summary.get("raw_contributor_checksum")
            or contributor_uris != tuple(checkpoint.raw_references)
            or contributor_hashes != tuple(checkpoint.raw_checksums)
            or run.source_metadata.get("unit_kind")
            != f"partition:{row.event_family}"
            or row.identity_checksum is None
        ):
            raise FD1PostcommitError("committed partition lineage changed")
        run_result = _mapping(run.source_metadata.get("result"), "partition run result")
        result_checksum_field = (
            "payload_checksum"
            if row.event_family == "player_prior"
            else "partition_checksum"
        )
        if (
            run_result.get(result_checksum_field)
            != run.source_metadata.get("unit_identity_checksum")
            or run_result.get("event_family") != row.event_family
            or run_result.get("season") != row.season
            or run_result.get("month") != row.month
        ):
            raise FD1PostcommitError("partition ingestion-run result changed")
        if expected is None or expected["row_count"] != row.row_count:
            raise FD1PostcommitError("committed partition differs from operator materialization")
        expected_checksum = expected.get("partition_checksum")
        if expected_checksum is not None and (
            checkpoint.quality_summary.get("batch_checksum") != expected_checksum
        ):
            raise FD1PostcommitError("Retrosheet partition checksum changed")
        partition_rows += row.row_count
    if set(expected_partition_keys) != observed_partition_keys:
        raise FD1PostcommitError("committed partition population is incomplete")
    if partition_rows != expected_parquet_rows:
        raise FD1PostcommitError("committed partition rows do not match publication")

    mapping_summary = _audit_identity_mappings(
        session,
        ledger,
        source_rows=source_rows,
        expected_count=expected_identity_count,
    )
    decisions = tuple(
        session.scalars(
            select(ExistingEvidenceDerivedUseDecision)
            .where(
                ExistingEvidenceDerivedUseDecision.source_version_id.in_(source_rows)
            )
            .order_by(ExistingEvidenceDerivedUseDecision.identity_checksum)
        )
    )
    decisions_by_key = {
        (row.source_version_id, row.evidence_manifest_checksum): row for row in decisions
    }
    if len(decisions_by_key) != len(decisions):
        raise FD1PostcommitError("derived-use decisions repeat a manifest")
    for row in decisions:
        if (
            row.allowed is not True
            or row.decision != "ALLOWED"
            or row.use_scope != "LOCAL_PRIVATE_RESEARCH"
        ):
            raise FD1PostcommitError("a publication derived-use decision is not allowed")
        source = source_rows.get(row.source_version_id)
        if source is None or row.source_key != source.source_key:
            raise FD1PostcommitError("a derived-use decision references another source")
        fields = {
            "owner_approval_id": row.owner_approval_id,
            "source_version_id": row.source_version_id,
            "source_key": row.source_key,
            "evidence_manifest_checksum": row.evidence_manifest_checksum,
            "use_scope": row.use_scope,
            "decision": row.decision,
            "allowed": row.allowed,
            "decided_by": row.decided_by,
            "decided_at": _aware_datetime(row.decided_at),
            "expires_at": _aware_datetime(row.expires_at),
            "decision_basis": row.decision_basis,
        }
        decision_identity = canonical_sha256(
            {
                "record_type": "FD1_EXISTING_EVIDENCE_DERIVED_USE",
                "schema_version": FD1_WAREHOUSE_SCHEMA_VERSION,
                "source_version_identity_checksum": source.identity_checksum,
                **_plain(fields),
            }
        )
        if (
            row.identity_checksum != decision_identity
            or row.id
            != uuid5(_PUBLICATION_NAMESPACE, f"derived-use:{decision_identity}")
        ):
            raise FD1PostcommitError("a derived-use decision identity is invalid")
    for partition in partitions:
        if (
            partition.source_version_id,
            partition.raw_contributor_checksum,
        ) not in decisions_by_key:
            raise FD1PostcommitError("a partition lacks its exact derived-use decision")
    required_decision_sources = {
        source_rows_by_key.source_key
        for source_rows_by_key in source_rows.values()
        if source_rows_by_key.source_key in mapping_summary["by_source"]
    } | {
        source_rows[partition.source_version_id].source_key
        for partition in partitions
    }
    observed_decision_sources = {row.source_key for row in decisions}
    if not required_decision_sources <= observed_decision_sources:
        raise FD1PostcommitError("published work lacks source-level derived-use proof")
    derived_use_observations = _sum_created_reused(
        publication, "derived_use_rows", "derived-use rows"
    )
    if (
        derived_use_observations < len(decisions)
        or _nonnegative_int(
            publication.get("derived_use_rows_created"),
            "derived-use rows created",
        )
        > len(decisions)
    ):
        raise FD1PostcommitError("derived-use publication counters do not reconcile")

    sql_digest = sha256()
    sql_digest.update(run_digest.digest())
    for row in checkpoints:
        _digest_record(
            sql_digest,
            {
                "id": str(row.id),
                "identity_checksum": row.identity_checksum,
                "query_fingerprint": row.query_fingerprint,
                "records_accepted": row.records_accepted,
            },
        )
    for row in partitions:
        _digest_record(
            sql_digest,
            {
                "id": str(row.id),
                "identity_checksum": row.identity_checksum,
                "logical_uri": row.logical_uri,
                "file_sha256": row.file_sha256,
                "row_count": row.row_count,
            },
        )
    sql_digest.update(bytes.fromhex(mapping_summary["population_checksum"]))
    for row in decisions:
        _digest_record(
            sql_digest,
            {
                "identity_checksum": row.identity_checksum,
                "source_version_id": str(row.source_version_id),
                "evidence_manifest_checksum": row.evidence_manifest_checksum,
            },
        )
    return (
        {
            "ingestion_run_count": len(runs),
            "partition_run_count": len(partition_runs),
            "identity_run_count": identity_run_count,
            "checkpoint_count": len(checkpoints),
            "partition_count": len(partitions),
            "partition_row_count": partition_rows,
            "identity_mapping_count": expected_identity_count,
            "derived_use_decision_count": len(decisions),
            "identity": mapping_summary,
            "population_checksum": sql_digest.hexdigest(),
        },
        partitions,
    )


def _audit_identity_mappings(
    session: Session,
    ledger: Mapping[str, Any],
    *,
    source_rows: Mapping[UUID, HistoricalSourceVersion],
    expected_count: int,
) -> dict[str, Any]:
    declared = _mapping(ledger.get("identity"), "identity")
    counters = {
        "by_source": Counter[str](),
        "by_entity": Counter[str](),
        "by_confidence": Counter[str](),
        "by_namespace": Counter[str](),
    }
    digest = sha256()
    count = 0
    statement = (
        select(OpenDataIdentityMapping)
        .where(OpenDataIdentityMapping.source_version_id.in_(source_rows))
        .order_by(OpenDataIdentityMapping.identity_checksum)
        .execution_options(yield_per=5_000)
    )
    for row in session.scalars(statement):
        source = source_rows.get(row.source_version_id)
        if source is None:
            raise FD1PostcommitError("an identity mapping references another source")
        try:
            spec = OpenDataIdentityMappingSpec(
                source_key=source.source_key,
                entity_type=row.entity_type,
                source_namespace=row.source_namespace,
                external_id=row.external_id,
                canonical_namespace=row.canonical_namespace,
                canonical_id=row.canonical_id,
                source_row_number=row.source_row_number,
                source_row_checksum=row.source_row_checksum,
                confidence=row.confidence,
                evidence_logical_uri=row.evidence_logical_uri,
                valid_from=row.valid_from,
                valid_through=row.valid_through,
            )
        except (TypeError, ValueError) as exc:
            raise FD1PostcommitError("a committed identity mapping is invalid") from exc
        expected_identity = build_identity_mapping_identity(
            source_version_identity_checksum=source.identity_checksum,
            spec=spec,
        )
        if row.identity_checksum != expected_identity:
            raise FD1PostcommitError("a committed identity mapping checksum changed")
        counters["by_source"][source.source_key] += 1
        counters["by_entity"][row.entity_type] += 1
        counters["by_confidence"][row.confidence] += 1
        counters["by_namespace"][row.source_namespace] += 1
        count += 1
        _digest_record(
            digest,
            {
                "identity_checksum": row.identity_checksum,
                "source_version_id": str(row.source_version_id),
                "entity_type": row.entity_type,
                "source_namespace": row.source_namespace,
                "external_id": row.external_id,
                "source_row_number": row.source_row_number,
            },
        )
    if count != expected_count:
        raise FD1PostcommitError("committed identity-mapping count changed")
    for name, counter in counters.items():
        expected = _integer_mapping(declared.get(name), f"identity {name}")
        if dict(sorted(counter.items())) != expected:
            raise FD1PostcommitError(f"committed identity distribution changed at {name}")
    return {
        "by_source": dict(sorted(counters["by_source"].items())),
        "by_entity": dict(sorted(counters["by_entity"].items())),
        "by_confidence": dict(sorted(counters["by_confidence"].items())),
        "by_namespace": dict(sorted(counters["by_namespace"].items())),
        "population_checksum": digest.hexdigest(),
    }


def _audit_parquet(
    store_root: Path,
    *,
    partitions: Sequence[HistoricalEventPartition],
    source_rows: Mapping[UUID, HistoricalSourceVersion],
    pretransaction: Mapping[str, Any],
) -> dict[str, Any]:
    store = HistoricalEventParquetStore(store_root)
    expected_paths: set[str] = set()
    family_partitions: Counter[str] = Counter()
    family_rows: Counter[str] = Counter()
    schema_versions: Counter[str] = Counter()
    total_bytes = 0
    digest = sha256()
    for row in partitions:
        source = source_rows.get(row.source_version_id)
        if source is None:
            raise FD1PostcommitError("partition source is outside the operator")
        artifact = _partition_artifact(row, source_key=source.source_key)
        try:
            path = store.validate(artifact)
        except (OSError, ValueError) as exc:
            raise FD1PostcommitError(
                f"Parquet validation failed for {row.logical_uri}"
            ) from exc
        logical = path.relative_to(store_root).as_posix()
        if logical != row.logical_uri or logical in expected_paths:
            raise FD1PostcommitError("Parquet logical path population changed")
        expected_paths.add(logical)
        family_partitions[row.event_family] += 1
        family_rows[row.event_family] += row.row_count
        schema_versions[row.schema_version] += 1
        total_bytes += row.file_size_bytes
        _digest_record(
            digest,
            {
                "logical_uri": logical,
                "identity_checksum": row.identity_checksum,
                "file_sha256": row.file_sha256,
                "file_size_bytes": row.file_size_bytes,
                "schema_hash": row.schema_hash,
                "row_count": row.row_count,
            },
        )
    actual_paths: set[str] = set()
    for path in store_root.rglob("*"):
        if path.is_dir():
            if _linklike(path):
                raise FD1PostcommitError("governed Parquet store contains a link-like directory")
            continue
        if _linklike(path) or not path.is_file() or path.suffix != ".parquet":
            raise FD1PostcommitError("governed Parquet store contains an unexpected file")
        actual_paths.add(path.relative_to(store_root).as_posix())
    if actual_paths != expected_paths:
        raise FD1PostcommitError("governed Parquet files and committed SQL differ")
    publication = _mapping(pretransaction.get("publication"), "publication")
    if (
        len(partitions) != publication.get("batches_seen")
        or sum(family_rows.values()) != publication.get("parquet_rows")
    ):
        raise FD1PostcommitError("Parquet population differs from the execute ledger")
    created_partitions = _nonnegative_int(
        publication.get("partition_rows_created"), "created partitions"
    )
    published_bytes = _nonnegative_int(
        publication.get("bytes_published"), "published bytes"
    )
    if (
        (created_partitions == len(partitions) and published_bytes != total_bytes)
        or (created_partitions == 0 and published_bytes != 0)
        or not 0 <= published_bytes <= total_bytes
    ):
        raise FD1PostcommitError("published Parquet byte accounting changed")
    return {
        "logical_root": _mapping(pretransaction.get("store"), "store").get(
            "logical_root"
        ),
        "partition_count": len(partitions),
        "row_count": sum(family_rows.values()),
        "file_bytes": total_bytes,
        "partitions_by_family": dict(sorted(family_partitions.items())),
        "rows_by_family": dict(sorted(family_rows.items())),
        "partitions_by_schema_version": dict(sorted(schema_versions.items())),
        "population_checksum": digest.hexdigest(),
        "all_sql_rows_validated": True,
        "all_store_files_accounted_for": True,
    }


def _partition_artifact(
    row: HistoricalEventPartition, *, source_key: str
) -> HistoricalEventPartitionArtifact:
    return HistoricalEventPartitionArtifact(
        source_key=source_key,
        source_version_id=str(row.source_version_id),
        checkpoint_id=str(row.checkpoint_id),
        event_family=row.event_family,
        season=row.season,
        month=row.month,
        logical_uri=row.logical_uri,
        row_count=row.row_count,
        game_count=row.game_count,
        plate_appearance_count=row.plate_appearance_count,
        player_count=row.player_count,
        min_event_date=_date_iso(row.min_event_date),
        max_event_date=_date_iso(row.max_event_date),
        min_event_at=_datetime_iso(row.min_event_at),
        max_event_at=_datetime_iso(row.max_event_at),
        storage_format=row.storage_format,
        compression=row.compression,
        schema_version=row.schema_version,
        schema_hash=row.schema_hash,
        file_sha256=row.file_sha256,
        file_size_bytes=row.file_size_bytes,
        raw_contributor_manifest=tuple(
            dict(item) for item in row.raw_contributor_manifest
        ),
        raw_contributor_checksum=row.raw_contributor_checksum,
        code_revision=row.code_revision,
        evidence_tier=row.evidence_tier,
        timing_classification=row.timing_classification,
        identity_checksum=row.identity_checksum,
        reused=False,
    )


def _expected_partition_keys(
    ledger: Mapping[str, Any]
) -> dict[tuple[int, int, str], dict[str, Any]]:
    result: dict[tuple[int, int, str], dict[str, Any]] = {}
    materialization = _mapping(
        ledger.get("retrosheet_materialization"), "materialization"
    )
    for raw in _sequence(materialization.get("monthly"), "materialization months"):
        row = _mapping(raw, "materialization month")
        season = _positive_int(row.get("season"), "materialization season")
        month = _positive_int(row.get("month"), "materialization month")
        family_rows = _integer_mapping(row.get("family_rows", {}), "monthly family rows")
        checksums = _mapping(row.get("partition_checksums"), "partition checksums")
        if set(family_rows) != set(checksums):
            raise FD1PostcommitError("monthly row and partition-checksum families differ")
        for family, count in family_rows.items():
            if count <= 0:
                raise FD1PostcommitError("published monthly family has no rows")
            key = (season, month, family)
            if key in result:
                raise FD1PostcommitError("operator repeats a monthly partition")
            result[key] = {
                "row_count": count,
                "partition_checksum": _require_sha256(
                    str(checksums[family]), "monthly partition checksum"
                ),
            }
    priors = _mapping(ledger.get("player_priors"), "player priors")
    prior_rows = _integer_mapping(priors.get("rows_by_season"), "prior rows by season")
    batches = _positive_int(priors.get("batches"), "prior batches")
    if batches != len(prior_rows):
        raise FD1PostcommitError("player priors are not one annual partition per season")
    for season, count in prior_rows.items():
        key = (int(season), 12, "player_prior")
        if key in result or count <= 0:
            raise FD1PostcommitError("player-prior partition population changed")
        result[key] = {"row_count": count, "partition_checksum": None}
    publication = _mapping(ledger.get("publication"), "publication")
    if len(result) != publication.get("batches_seen"):
        raise FD1PostcommitError("operator partition keys do not match batch count")
    if sum(item["row_count"] for item in result.values()) != publication.get(
        "parquet_rows"
    ):
        raise FD1PostcommitError("operator partition rows do not match parquet count")
    return result


@contextmanager
def _select_only(session: Session) -> Iterable[None]:
    bind = session.get_bind()

    def guard(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        token = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        normalized = " ".join(statement.strip().upper().split())
        if token != "SELECT" and normalized != "SET TRANSACTION READ ONLY":
            raise FD1PostcommitError("postcommit database access is not read-only")

    event.listen(bind, "before_cursor_execute", guard)
    try:
        yield
    finally:
        event.remove(bind, "before_cursor_execute", guard)


def _publish_json_evidence(
    document: Mapping[str, Any], *, output_directory: Path, project_root: Path
) -> Path:
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    directory = _safe_directory(directory, project_root, "postcommit output directory")
    payload = (
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
        + b"\n"
    )
    digest = sha256(payload).hexdigest()
    final = directory / (
        "publish-actual-execute-postcommit-closeout-"
        f"sha256-{digest}.json"
    )
    if final.exists() or final.is_symlink():
        path = _safe_regular_file(final, directory, "existing postcommit evidence")
        actual_sha, actual_bytes = _hash_file(path)
        if actual_sha != digest or actual_bytes != len(payload):
            raise FD1PostcommitError("content-addressed postcommit evidence changed")
        return path
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=".postcommit-",
        suffix=".pending",
        dir=directory,
        delete=False,
    )
    pending = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if _linklike(pending) or not pending.is_file():
            raise FD1PostcommitError("postcommit staging path is unsafe")
        try:
            os.link(pending, final)
        except FileExistsError:
            pass
        path = _safe_regular_file(final, directory, "postcommit evidence")
        actual_sha, actual_bytes = _hash_file(path)
        if actual_sha != digest or actual_bytes != len(payload):
            raise FD1PostcommitError("published postcommit evidence changed")
        _validate_content_addressed_name(path, digest, _POSTCOMMIT_NAME, "postcommit evidence")
        return path
    finally:
        if pending.exists() and not pending.is_symlink():
            pending.unlink()


def _publish_bytes_evidence(
    payload: bytes,
    *,
    output_directory: Path,
    project_root: Path,
    filename: str,
    pattern: re.Pattern[str],
    label: str,
) -> Path:
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    directory = _safe_directory(directory, project_root, f"{label} directory")
    digest = sha256(payload).hexdigest()
    final = directory / filename
    _validate_content_addressed_name(final, digest, pattern, label)
    if final.exists() or final.is_symlink():
        path = _safe_regular_file(final, directory, label)
        actual_sha, actual_bytes = _hash_file(path)
        if actual_sha != digest or actual_bytes != len(payload):
            raise FD1PostcommitError(f"{label} changed")
        return path
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=".execute-result-",
        suffix=".pending",
        dir=directory,
        delete=False,
    )
    pending = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if _linklike(pending) or not pending.is_file():
            raise FD1PostcommitError(f"{label} staging path is unsafe")
        try:
            os.link(pending, final)
        except FileExistsError:
            pass
        path = _safe_regular_file(final, directory, label)
        actual_sha, actual_bytes = _hash_file(path)
        if actual_sha != digest or actual_bytes != len(payload):
            raise FD1PostcommitError(f"{label} changed after publication")
        return path
    finally:
        if pending.exists() and not pending.is_symlink():
            pending.unlink()


def _safe_project_file(root: Path, logical_uri: str, label: str) -> Path:
    return _safe_regular_file(
        root.joinpath(*PurePosixPath(logical_uri).parts), root, label
    )


def _safe_regular_file(path: Path, root: Path, label: str) -> Path:
    selected_root = Path(root).resolve(strict=True)
    selected = Path(path).resolve(strict=True)
    try:
        relative = selected.relative_to(selected_root)
    except ValueError as exc:
        raise FD1PostcommitError(f"{label} escapes its allowed root") from exc
    cursor = selected_root
    if _linklike(cursor):
        raise FD1PostcommitError(f"{label} root is link-like")
    for part in relative.parts:
        cursor = cursor / part
        if _linklike(cursor):
            raise FD1PostcommitError(f"{label} contains a link-like path")
    if not selected.is_file():
        raise FD1PostcommitError(f"{label} is not a regular file")
    return selected


def _safe_directory(path: Path, root: Path, label: str) -> Path:
    selected_root = Path(root).resolve(strict=True)
    selected = Path(path).resolve(strict=True)
    try:
        relative = selected.relative_to(selected_root)
    except ValueError as exc:
        raise FD1PostcommitError(f"{label} escapes its allowed root") from exc
    cursor = selected_root
    if _linklike(cursor):
        raise FD1PostcommitError(f"{label} root is link-like")
    for part in relative.parts:
        cursor = cursor / part
        if _linklike(cursor):
            raise FD1PostcommitError(f"{label} contains a link-like path")
    if not selected.is_dir():
        raise FD1PostcommitError(f"{label} is not a directory")
    return selected


def _validate_content_addressed_name(
    path: Path,
    checksum: str,
    pattern: re.Pattern[str],
    label: str,
) -> None:
    match = pattern.fullmatch(path.name)
    if match is None or match.group(1) != checksum:
        raise FD1PostcommitError(f"{label} filename is not content-addressed")


def _load_json_object(payload: bytes, label: str) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FD1PostcommitError(f"{label} contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8-sig"), object_pairs_hook=no_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FD1PostcommitError(f"{label} is not valid UTF-8 JSON") from exc
    return dict(_mapping(value, label))


def _validate_jsonl(path: Path) -> tuple[int, str]:
    count = 0
    digest = sha256()
    with path.open("rb") as stream:
        for raw in stream:
            if not raw.endswith(b"\n") or raw.strip() == b"":
                raise FD1PostcommitError("source evidence JSONL framing changed")
            document = _load_json_object(raw, "source evidence row")
            _digest_record(digest, document)
            count += 1
    return count, digest.hexdigest()


def _hash_file(path: Path) -> tuple[str, int]:
    digest = sha256()
    byte_count = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            byte_count += len(block)
    return digest.hexdigest(), byte_count


def _sum_created_reused(
    publication: Mapping[str, Any], prefix: str, label: str
) -> int:
    return _nonnegative_int(
        publication.get(f"{prefix}_created"), f"{label} created"
    ) + _nonnegative_int(
        publication.get(f"{prefix}_reused"), f"{label} reused"
    )


def _integer_mapping(value: object, label: str) -> dict[str, int]:
    raw = _mapping(value, label)
    result: dict[str, int] = {}
    for key, item in raw.items():
        if not isinstance(key, str) or not key:
            raise FD1PostcommitError(f"{label} has an invalid key")
        result[key] = _nonnegative_int(item, f"{label}.{key}")
    return dict(sorted(result.items()))


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FD1PostcommitError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise FD1PostcommitError(f"{label} must be an array")
    return value


def _logical_uri(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "://" in value:
        raise FD1PostcommitError(f"{label} must be a relative logical URI")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise FD1PostcommitError(f"{label} must be a relative logical URI")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FD1PostcommitError(f"{label} must be a nonnegative integer")
    return value


def _positive_int(value: object, label: str) -> int:
    result = _nonnegative_int(value, label)
    if result == 0:
        raise FD1PostcommitError(f"{label} must be positive")
    return result


def _require_sha256(value: str, label: str) -> str:
    if not _SHA256.fullmatch(value):
        raise FD1PostcommitError(f"{label} must be a lowercase SHA-256")
    return value


def _date_iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat()


def _aware_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _digest_record(digest: Any, value: Any) -> None:
    payload = _compact_json(value).encode("utf-8") + b"\n"
    digest.update(payload)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(item) for item in value), key=_compact_json)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _datetime_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return _plain(asdict(value))
    return value


def _compact_json(value: Any) -> str:
    return json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _linklike(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(details, "st_file_attributes", 0) & _REPARSE_POINT
    )


__all__ = [
    "FD1_ACTUAL_OPERATOR_LOGICAL_PATH",
    "FD1_POSTCOMMIT_OPERATOR_LOGICAL_PATH",
    "FD1_POSTCOMMIT_MODULE_LOGICAL_PATH",
    "FD1_POSTCOMMIT_SCHEMA_VERSION",
    "FD1_POSTCOMMIT_STATUS",
    "FD1PostcommitError",
    "build_actual_operator_code_revision",
    "canonical_fd1_postcommit_checksum",
    "project_committed_operator_closeout",
    "verify_and_publish_fd1_postcommit",
]
