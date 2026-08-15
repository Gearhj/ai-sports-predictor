from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from datetime import UTC, date, datetime
from types import MappingProxyType

import pytest
from sqlalchemy import func, select, text

import app.gen2.fd1_postcommit as fd1_postcommit_module
from app.gen2.fd1_identity import (
    GEN2_PRIMARY_POPULATION,
    LAHMAN_SOURCE_KEY,
    PlayerPriorPartitionBatch,
    RegistryIdentityMappingBatch,
    _identity_mapping_spec_payload,
)
from app.gen2.fd1_materializer import RETROSHEET_SOURCE_KEY, FamilyStagingPayload

from app.gen2.fd1_postcommit import (
    FD1_ACTUAL_OPERATOR_LOGICAL_PATH,
    FD1_POSTCOMMIT_SCHEMA_VERSION,
    FD1_POSTCOMMIT_STATUS,
    FD1PostcommitError,
    _audit_parquet,
    _audit_sql,
    _expected_partition_keys,
    _publish_json_evidence,
    _select_only,
    _validate_referenced_artifacts,
    build_actual_operator_code_revision,
    canonical_fd1_postcommit_checksum,
    project_committed_operator_closeout,
    verify_and_publish_fd1_postcommit,
)
from app.gen2.fd1_registry_sync import FD1_SOURCE_DEFINITIONS
from app.gen2.fd1_warehouse import (
    ContributorDownloadBinding,
    build_fd1_code_revision,
    publish_fd1_warehouse,
)
from app.gen2.fd1_readiness_closeout import (
    OPERATOR_SCHEMA_VERSION,
    REQUIRED_SOURCE_KEYS,
)
from app.gen2.open_data_acquisition import (
    DOWNLOAD_MANIFEST_SCHEMA_VERSION,
    EXTRACTION_MANIFEST_SCHEMA_VERSION,
    ArchiveFormat,
    AttemptAccounting,
    DownloadArtifact,
    DownloadDisposition,
    ExtractedMember,
    ExtractionArtifact,
)
from app.gen2.open_data_registry import (
    OpenDataDownloadSpec,
    OpenDataIdentityMappingSpec,
    OpenDataRegistry,
)
from app.gen2.parquet_store import HistoricalEventParquetStore
from app.historical.checkpoints import HistoricalCheckpoint, _with_identity
from app.models import (
    ExistingEvidenceDerivedUseDecision,
    HistoricalEventPartition,
    HistoryBackfillCheckpoint,
    IngestionRun,
    OpenDataIdentityMapping,
)


def _sha(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _json_sha(value: object) -> str:
    return _sha(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    )


def _register_download(
    session,
    *,
    source_key: str,
    object_key: str,
    payload_sha256: str,
    archive_format: ArchiveFormat,
):
    definition = FD1_SOURCE_DEFINITIONS[source_key]
    registry = OpenDataRegistry(session)
    source = registry.ensure_source_release(definition.release_spec())
    _owner, provider = registry.ensure_source_decisions(
        definition.owner_spec(), definition.provider_spec()
    )
    attempt = AttemptAccounting(
        attempt_number=1,
        started_at="2026-08-14T12:00:01+00:00",
        completed_at="2026-08-14T12:00:02+00:00",
        requested_range_start=0,
        status_code=200,
        response_content_length=12,
        received_bytes=12,
        outcome="SUCCEEDED",
    )
    artifact = DownloadArtifact(
        schema_version=DOWNLOAD_MANIFEST_SCHEMA_VERSION,
        source_key=source_key,
        object_key=object_key,
        sanitized_url="https://example.org/official/source.bin",
        policy_identity_checksum="b" * 64,
        authorization_identity_checksum=provider.identity_checksum,
        disposition=DownloadDisposition.CREATED_REVISION,
        revision=1,
        content_logical_uri=(
            f"open-data/{source_key.lower()}/{object_key}/r1.blob"
        ),
        manifest_logical_uri=(
            f"open-data/{source_key.lower()}/{object_key}/acquisition.json"
        ),
        sha256=payload_sha256,
        content_length=12,
        mime_type=(
            "application/zip"
            if archive_format is ArchiveFormat.ZIP
            else "text/csv"
        ),
        archive_format=archive_format,
        http_status=200,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
        physical_attempt_count=1,
        transport_bytes_received=12,
        attempts=(attempt,),
    )
    download = registry.record_download(
        source,
        provider,
        artifact,
        OpenDataDownloadSpec(
            source_key=source_key,
            source_release=definition.dataset_version,
            file_name=(
                "source.zip" if archive_format is ArchiveFormat.ZIP else "source.csv"
            ),
        ),
    )
    return source, download


def _extraction(download, member_sha: str) -> ExtractionArtifact:
    member = ExtractedMember(
        relative_path="gameinfo.csv", size_bytes=7, sha256=member_sha
    )
    semantic = {
        "schema_version": EXTRACTION_MANIFEST_SCHEMA_VERSION,
        "source_key": download.source_key,
        "object_key": download.object_key,
        "source_revision": download.upstream_revision,
        "archive_sha256": download.payload_sha256,
        "archive_format": ArchiveFormat.ZIP.value,
        "extraction_logical_uri": "open-data/retrosheet/extracted/r1",
        "manifest_logical_uri": (
            "open-data/retrosheet/extracted/r1/extraction-manifest.json"
        ),
        "extracted_at": "2026-08-14T12:00:03+00:00",
        "member_count": 1,
        "expanded_bytes": member.size_bytes,
        "members": [asdict(member)],
    }
    return ExtractionArtifact(
        **{
            key: value
            for key, value in semantic.items()
            if key not in {"archive_format", "members"}
        },
        archive_format=ArchiveFormat.ZIP,
        members=(member,),
        identity_checksum=_json_sha(semantic),
    )


def _retrosheet_batch(source_id, member_sha: str) -> FamilyStagingPayload:
    row = {
        "source_key": RETROSHEET_SOURCE_KEY,
        "source_version_id": str(source_id),
        "game_id": "retrosheet:game:AAA202406010",
        "game_date": "2024-06-01",
        "event_date": "2024-06-01",
        "season": 2024,
        "month": 6,
        "player_id": "retrosheet:player:player1",
        "actual_start_flag": 1,
        "source_filename": "gameinfo.csv",
        "source_row_number": 2,
        "source_row_checksum": "c" * 64,
        "source_file_sha256": member_sha,
    }
    row["row_checksum"] = _json_sha(row)
    schema = "retrosheet_fd1_starter_usage_v1"
    bundle = "d" * 64
    partition = _json_sha(
        {
            "family": "starter_usage",
            "season": 2024,
            "month": 6,
            "schema_version": schema,
            "source_bundle_checksum": bundle,
            "rows": [row],
        }
    )
    return FamilyStagingPayload(
        family="starter_usage",
        season=2024,
        month=6,
        schema_version=schema,
        publication_scope="COMPLETE_MONTH_PARTITION",
        rows=(MappingProxyType(row),),
        row_count=1,
        game_count=1,
        plate_appearance_count=0,
        player_count=1,
        min_event_date=date(2024, 6, 1),
        max_event_date=date(2024, 6, 1),
        source_file_checksums=MappingProxyType({"gameinfo.csv": member_sha}),
        source_bundle_checksum=bundle,
        partition_checksum=partition,
        leakage_policy="TEST_RETROSPECTIVE_ONLY",
        quality_report=MappingProxyType({"partial_month": False}),
    )


def _prior_batch(download) -> PlayerPriorPartitionBatch:
    row = MappingProxyType(
        {
            "event_family": "player_prior",
            "canonical_game_id": None,
            "canonical_player_id": "person-1",
            "player_id": None,
            "season": 2024,
            "official_date": "2024-12-31",
            "population": GEN2_PRIMARY_POPULATION,
            "source_key": LAHMAN_SOURCE_KEY,
        }
    )
    contributor = MappingProxyType(
        {
            "logical_uri": download.raw_logical_uri,
            "file_sha256": download.payload_sha256,
            "record_count": 1,
        }
    )
    return PlayerPriorPartitionBatch(
        event_family="player_prior",
        season=2024,
        month=12,
        population=GEN2_PRIMARY_POPULATION,
        batch_number=1,
        rows=(row,),
        row_count=1,
        player_count=1,
        min_official_date=date(2024, 12, 31),
        max_official_date=date(2024, 12, 31),
        raw_contributor_manifest=(contributor,),
        raw_contributor_checksum=_json_sha([dict(contributor)]),
        payload_checksum=_json_sha([dict(row)]),
    )


def _mapping_batch(source_key: str, evidence_uri: str) -> RegistryIdentityMappingBatch:
    spec = OpenDataIdentityMappingSpec(
        source_key=source_key,
        entity_type="person",
        source_namespace="retrosheet",
        external_id="player1",
        canonical_namespace="chadwick_uuid",
        canonical_id="00000000-0000-0000-0000-000000000001",
        source_row_number=2,
        source_row_checksum="e" * 64,
        confidence="EXACT",
        evidence_logical_uri=evidence_uri,
    )
    return RegistryIdentityMappingBatch(
        batch_number=1,
        specs=(spec,),
        batch_checksum=_json_sha([_identity_mapping_spec_payload(spec)]),
    )


def _download_ledger(download) -> dict[str, object]:
    return {
        "source_key": download.source_key,
        "object_key": download.object_key,
        "source_release": download.source_release,
        "upstream_revision": download.upstream_revision,
        "content_length": download.content_length,
        "payload_sha256": download.payload_sha256,
        "archive_sha256": download.archive_sha256,
        "raw_logical_uri": download.raw_logical_uri,
        "download_identity_checksum": download.identity_checksum,
    }


_PUBLICATION_MODELS = (
    IngestionRun,
    HistoryBackfillCheckpoint,
    HistoricalEventPartition,
    ExistingEvidenceDerivedUseDecision,
    OpenDataIdentityMapping,
)


def _database_counts(session) -> dict[str, int]:
    return {
        model.__tablename__: int(
            session.scalar(select(func.count()).select_from(model)) or 0
        )
        for model in _PUBLICATION_MODELS
    }


def _parquet_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, _sha(path.read_bytes()))
        for path in root.rglob("*.parquet")
    }


def _ledger() -> dict[str, object]:
    return {
        "schema_version": OPERATOR_SCHEMA_VERSION,
        "success": True,
        "success_scope": "PRETRANSACTION_VALIDATION_AND_ARTIFACT_FINALIZATION",
        "mode": "execute",
        "sanitized": True,
        "transaction": {
            "single_publish_call": True,
            "status_at_closeout": "PENDING_CALLER_COMMIT",
            "committed": False,
            "rolled_back": False,
            "committed_by_publication_function": False,
            "final_result_reported_by_operator_stdout": True,
        },
        "prohibited_activity": {
            "network_requests": 0,
            "provider_transport_constructed": False,
            "mlb_or_savant_automation": False,
            "predictive_model_fit_or_promotion": False,
            "odds_acquisition": False,
            "position_population": False,
        },
        "publication": {
            "mode": "execute",
            "code_revision": "a" * 64,
            "batches_seen": 1,
            "parquet_rows": 2,
            "identity_mappings_seen": 3,
            "derived_use_rows_created": 1,
            "derived_use_rows_reused": 0,
        },
        "identity": {"batches": 1},
        "readiness_evidence": {
            "prior_period_park_factor_formability": {
                "formable": True,
                "factor_count": 30,
            }
        },
        "registered_downloads_used": [
            {"source_key": f"SOURCE_{index}", "object_key": f"object-{index}"}
            for index in range(4)
        ],
        "retrosheet_source_evidence": {
            "logical_path": ".runtime/gate3c-fd1/artifacts/source.jsonl",
            "sha256": "b" * 64,
            "bytes": 10,
            "record_count": 1,
        },
        "schedule_fingerprint_bridge": {
            "logical_path": ".runtime/gate3c-fd1/artifacts/bridge.json",
            "sha256": "c" * 64,
            "bytes": 20,
            "summary": {"matched": 2_430},
        },
        "asp_retrosheet_full_reconciliation": {
            "logical_path": ".runtime/gate3c-fd1/artifacts/reconciliation.json",
            "sha256": "d" * 64,
            "bytes": 30,
            "summary": {"matched_game_count": 2_430},
        },
    }


def _execute_result() -> dict[str, object]:
    return {
        "success": True,
        "mode": "execute",
        "code_revision": "a" * 64,
        "batches_seen": 1,
        "parquet_rows": 2,
        "identity_mappings_seen": 3,
        "park_factors_formable": True,
        "park_factor_count": 30,
        "transaction_committed": True,
        "transaction_rolled_back": False,
        "artifacts_finalized_before_transaction_resolution": True,
        "network_requests": 0,
        "retrosheet_source_evidence": (
            ".runtime/gate3c-fd1/artifacts/source.jsonl"
        ),
        "schedule_fingerprint_bridge": {
            "logical_path": ".runtime/gate3c-fd1/artifacts/bridge.json",
            "sha256": "c" * 64,
            "bytes": 20,
            "summary": {"matched": 2_430},
        },
        "asp_retrosheet_full_reconciliation": {
            "logical_path": ".runtime/gate3c-fd1/artifacts/reconciliation.json",
            "sha256": "d" * 64,
            "bytes": 30,
            "summary": {"matched_game_count": 2_430},
        },
    }


def _proof(
    ledger: dict[str, object] | None = None,
    result: dict[str, object] | None = None,
) -> dict[str, object]:
    bound_ledger = ledger or _ledger()
    bound_result = result or _execute_result()
    value: dict[str, object] = {
        "schema_version": FD1_POSTCOMMIT_SCHEMA_VERSION,
        "all_checks_passed": True,
        "pretransaction_ledger": {
            "logical_path": (
                ".runtime/gate3c-fd1/artifacts/"
                "publish-actual-execute-pretransaction-closeout-"
                f"sha256-{'1' * 64}.json"
            ),
            "sha256": "1" * 64,
            "canonical_sha256": canonical_fd1_postcommit_checksum(bound_ledger),
            "bytes": 100,
        },
        "execute_result": {
            "logical_path": (
                ".runtime/gate3c-fd1/artifacts/"
                f"publish-actual-execute-result-sha256-{'2' * 64}.json"
            ),
            "sha256": "2" * 64,
            "canonical_sha256": canonical_fd1_postcommit_checksum(bound_result),
            "bytes": 100,
        },
        "operator_code": {
            "logical_path": FD1_ACTUAL_OPERATOR_LOGICAL_PATH,
            "sha256": "3" * 64,
            "bytes": 100,
            "stable_code_revision": "4" * 64,
            "combined_code_revision": "a" * 64,
        },
        "verifier_code": {
            "module_logical_path": "server/app/gen2/fd1_postcommit.py",
            "module_sha256": "5" * 64,
            "module_bytes": 100,
            "script_logical_path": ".runtime/gate3c-fd1/postcommit_verify.py",
            "script_sha256": "6" * 64,
            "script_bytes": 100,
        },
        "referenced_artifacts": [
            {
                "name": key,
                "logical_path": bound_ledger[key]["logical_path"],
                "sha256": bound_ledger[key]["sha256"],
                "canonical_sha256": str(index) * 64,
                "bytes": bound_ledger[key]["bytes"],
                "record_count": (
                    bound_ledger[key].get("record_count") if index == 7 else None
                ),
            }
            for index, key in enumerate(
                (
                    "retrosheet_source_evidence",
                    "schedule_fingerprint_bridge",
                    "asp_retrosheet_full_reconciliation",
                ),
                start=7,
            )
        ],
        "source_registry": {
            "source_version_count": 5,
            "registered_download_count": 4,
            "operator_contributor_source_keys": [
                "CHADWICK_REGISTER_ARCHIVES",
                "RETROSHEET_AUXILIARY_DOWNLOADS",
                "RETROSHEET_CSV_DOWNLOADS",
                "SABR_LAHMAN_BOX_DOWNLOADS",
            ],
            "run_governance_source_keys": [
                "CHADWICK_REGISTER_ARCHIVES",
                "RETROSHEET_AUXILIARY_DOWNLOADS",
                "RETROSHEET_CSV_DOWNLOADS",
                "RETROSHEET_EVENT_ARCHIVES",
                "SABR_LAHMAN_BOX_DOWNLOADS",
            ],
            "population_checksum": "d" * 64,
        },
        "sql": {
            "ingestion_run_count": 2,
            "partition_run_count": 1,
            "identity_run_count": 1,
            "checkpoint_count": 1,
            "partition_count": 1,
            "partition_row_count": 2,
            "identity_mapping_count": 3,
            "derived_use_decision_count": 1,
            "population_checksum": "e" * 64,
        },
        "parquet": {
            "partition_count": 1,
            "row_count": 2,
            "population_checksum": "f" * 64,
            "all_sql_rows_validated": True,
            "all_store_files_accounted_for": True,
        },
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
    value["derivation_checksum"] = canonical_fd1_postcommit_checksum(value)
    return value


def test_projection_preserves_pretransaction_evidence_and_marks_only_commit() -> None:
    ledger = _ledger()
    original = deepcopy(ledger)

    proof = _proof(ledger, _execute_result())
    committed = project_committed_operator_closeout(
        ledger,
        _execute_result(),
        postcommit_verification=proof,
    )

    assert ledger == original
    assert committed["schema_version"] == OPERATOR_SCHEMA_VERSION
    assert committed["success_scope"] == (
        "COMMITTED_PUBLICATION_AND_POSTCOMMIT_VERIFICATION"
    )
    assert committed["transaction"] == {
        **original["transaction"],
        "status_at_closeout": FD1_POSTCOMMIT_STATUS,
        "committed": True,
        "rolled_back": False,
        "committed_by_publication_function": False,
        "commit_observed_by_postcommit_verifier": True,
    }
    for key in original:
        if key not in {"success_scope", "transaction"}:
            assert committed[key] == original[key]
    assert committed["postcommit_verification"] == proof


@pytest.mark.parametrize(
    ("target", "key", "value"),
    (
        ("ledger_transaction", "status_at_closeout", "COMMITTED"),
        ("ledger_transaction", "committed", True),
        ("result", "transaction_committed", False),
        ("result", "code_revision", "9" * 64),
        ("result", "parquet_rows", 999),
    ),
)
def test_projection_rejects_cross_run_or_precommitted_inputs(
    target: str, key: str, value: object
) -> None:
    ledger = _ledger()
    result = _execute_result()
    if target == "ledger_transaction":
        ledger["transaction"][key] = value
    else:
        result[key] = value
    proof = _proof(ledger, result)
    with pytest.raises(FD1PostcommitError):
        project_committed_operator_closeout(
            ledger,
            result,
            postcommit_verification=proof,
        )


def test_projection_rejects_tampered_postcommit_derivation() -> None:
    proof = _proof()
    proof["sql"] = {"population_checksum": "0" * 64}
    with pytest.raises(FD1PostcommitError, match="derivation checksum"):
        project_committed_operator_closeout(
            _ledger(),
            _execute_result(),
            postcommit_verification=proof,
        )


def test_projection_rejects_self_consistent_proof_bound_to_another_run() -> None:
    proof = _proof()
    proof["pretransaction_ledger"]["canonical_sha256"] = "0" * 64
    proof.pop("derivation_checksum")
    proof["derivation_checksum"] = canonical_fd1_postcommit_checksum(proof)
    with pytest.raises(FD1PostcommitError, match="another ledger"):
        project_committed_operator_closeout(
            _ledger(),
            _execute_result(),
            postcommit_verification=proof,
        )


@pytest.mark.parametrize(
    ("section", "key", "value", "message"),
    (
        (
            "source_registry",
            "registered_download_count",
            3,
            "source-registry proof",
        ),
        (
            "verifier_code",
            "module_logical_path",
            "server/app/gen2/other.py",
            "verifier-module binding",
        ),
        ("sql", "ingestion_run_count", 99, "SQL/Parquet counts"),
        ("sql", "derived_use_decision_count", 2, "derived-use counts"),
    ),
)
def test_projection_rejects_self_consistent_internal_proof_mismatch(
    section: str,
    key: str,
    value: object,
    message: str,
) -> None:
    proof = _proof()
    proof[section][key] = value
    proof.pop("derivation_checksum")
    proof["derivation_checksum"] = canonical_fd1_postcommit_checksum(proof)
    with pytest.raises(FD1PostcommitError, match=message):
        project_committed_operator_closeout(
            _ledger(),
            _execute_result(),
            postcommit_verification=proof,
        )


def test_actual_operator_code_revision_matches_frozen_algorithm() -> None:
    payload = b"exact ignored operator bytes\r\n"
    expected = sha256()
    expected.update(b"stable-fd1-code-revision\0")
    expected.update(bytes.fromhex(build_fd1_code_revision()))
    expected.update(FD1_ACTUAL_OPERATOR_LOGICAL_PATH.encode("utf-8"))
    expected.update(b"\0")
    expected.update(len(payload).to_bytes(8, "big"))
    expected.update(payload)
    assert build_actual_operator_code_revision(payload) == expected.hexdigest()


def test_postcommit_canonical_hash_matches_checkpoint_identity_contract() -> None:
    checkpoint = HistoricalCheckpoint(
        checkpoint_id="11111111-1111-1111-1111-111111111111",
        ingestion_run_id="22222222-2222-2222-2222-222222222222",
        source_version_id="33333333-3333-3333-3333-333333333333",
        stream_key="fd1:weather",
        query_fingerprint="a" * 64,
        status="SUCCEEDED",
        requested_at=datetime(2026, 8, 14, 12, tzinfo=UTC),
        completed_at=datetime(2026, 8, 14, 12, tzinfo=UTC),
        season=2025,
        event_start_at=datetime(2025, 4, 1, tzinfo=UTC),
        event_end_at=datetime(2025, 4, 30, 23, 59, tzinfo=UTC),
        sequence=20250401,
        attempt_count=1,
        records_received=10,
        records_accepted=10,
        raw_references=("raw/gameinfo.csv",),
        raw_checksums=("b" * 64,),
        schema_version="gate3c_fd1_warehouse_v1",
        code_revision="c" * 64,
        quality_summary={"network_requests": 0},
    )
    assert _with_identity(checkpoint).identity_checksum == (
        canonical_fd1_postcommit_checksum(checkpoint)
    )


def _write_content_addressed(
    root: Path, *, stem: str, suffix: str, payload: bytes
) -> tuple[str, str, int]:
    digest = _sha(payload)
    path = root / f"publish-actual-execute-{stem}-sha256-{digest}.{suffix}"
    path.write_bytes(payload)
    return path.as_posix(), digest, len(payload)


def test_referenced_operator_artifacts_are_hash_and_summary_validated(
    artifact_root: Path,
) -> None:
    project = artifact_root
    artifacts = project / ".runtime" / "gate3c-fd1" / "artifacts"
    artifacts.mkdir(parents=True)
    source = b'{"game":"A"}\n'
    bridge_document = {"matched": 2_430, "team_matches": ["x"]}
    bridge = json.dumps(bridge_document, sort_keys=True).encode("utf-8")
    reconciliation_document = {"matched_game_count": 2_430, "games": ["x"]}
    reconciliation = json.dumps(
        reconciliation_document, sort_keys=True
    ).encode("utf-8")
    source_path, source_sha, source_bytes = _write_content_addressed(
        artifacts,
        stem="2025-retrosheet-source-evidence",
        suffix="jsonl",
        payload=source,
    )
    bridge_path, bridge_sha, bridge_bytes = _write_content_addressed(
        artifacts,
        stem="2025-schedule-fingerprint-bridge",
        suffix="json",
        payload=bridge,
    )
    reconciliation_path, reconciliation_sha, reconciliation_bytes = (
        _write_content_addressed(
            artifacts,
            stem="2025-asp-retrosheet-full-reconciliation",
            suffix="json",
            payload=reconciliation,
        )
    )
    ledger = _ledger()
    ledger["retrosheet_source_evidence"] = {
        "logical_path": Path(source_path).relative_to(project).as_posix(),
        "sha256": source_sha,
        "bytes": source_bytes,
        "record_count": 1,
    }
    ledger["schedule_fingerprint_bridge"] = {
        "logical_path": Path(bridge_path).relative_to(project).as_posix(),
        "sha256": bridge_sha,
        "bytes": bridge_bytes,
        "summary": {"matched": 2_430},
    }
    ledger["asp_retrosheet_full_reconciliation"] = {
        "logical_path": Path(reconciliation_path).relative_to(project).as_posix(),
        "sha256": reconciliation_sha,
        "bytes": reconciliation_bytes,
        "summary": {"matched_game_count": 2_430},
    }

    evidence = _validate_referenced_artifacts(project.resolve(), ledger)
    assert [row["name"] for row in evidence] == [
        "retrosheet_source_evidence",
        "schedule_fingerprint_bridge",
        "asp_retrosheet_full_reconciliation",
    ]
    assert all(len(row["canonical_sha256"]) == 64 for row in evidence)

    Path(bridge_path).write_bytes(bridge + b"\n")
    with pytest.raises(FD1PostcommitError, match="artifact bytes changed"):
        _validate_referenced_artifacts(project.resolve(), ledger)


def test_select_only_guard_rejects_dml(session) -> None:
    with _select_only(session):
        assert session.execute(text("SELECT 1")).scalar_one() == 1
        with pytest.raises(FD1PostcommitError, match="not read-only"):
            session.execute(text("CREATE TABLE forbidden (id INTEGER)"))
    session.rollback()


def test_partition_projection_reconciles_monthly_and_prior_rows() -> None:
    ledger = {
        "retrosheet_materialization": {
            "monthly": [
                {
                    "season": 2025,
                    "month": 4,
                    "family_rows": {"weather": 10},
                    "partition_checksums": {"weather": "a" * 64},
                }
            ]
        },
        "player_priors": {
            "batches": 1,
            "rows_by_season": {"2025": 3},
        },
        "publication": {"batches_seen": 2, "parquet_rows": 13},
    }
    assert _expected_partition_keys(ledger) == {
        (2025, 4, "weather"): {
            "row_count": 10,
            "partition_checksum": "a" * 64,
        },
        (2025, 12, "player_prior"): {
            "row_count": 3,
            "partition_checksum": None,
        },
    }
    ledger["publication"]["parquet_rows"] = 14
    with pytest.raises(FD1PostcommitError, match="parquet count"):
        _expected_partition_keys(ledger)


def test_empty_database_cannot_satisfy_positive_publication_counts(session) -> None:
    ledger = {
        "publication": {
            "partition_rows_created": 1,
            "partition_rows_reused": 0,
            "checkpoint_rows_created": 1,
            "checkpoint_rows_reused": 0,
            "ingestion_run_rows_created": 2,
            "ingestion_run_rows_reused": 0,
            "identity_mapping_rows_created": 1,
            "identity_mapping_rows_reused": 0,
            "batches_seen": 1,
            "parquet_rows": 1,
            "identity_mappings_seen": 1,
        },
        "identity": {"batches": 1, "mappings": 1},
    }
    with pytest.raises(FD1PostcommitError, match="ingestion-run count"):
        _audit_sql(
            session,
            ledger,
            source_rows={},
            combined_revision="a" * 64,
        )


def test_missing_parquet_population_is_rejected(artifact_root: Path) -> None:
    store = artifact_root / "store"
    store.mkdir()
    with pytest.raises(FD1PostcommitError, match="execute ledger"):
        _audit_parquet(
            store,
            partitions=(),
            source_rows={},
            pretransaction={
                "store": {"logical_root": "data/curated/gen2-fd1"},
                "publication": {"batches_seen": 1, "parquet_rows": 1},
            },
        )


def test_full_postcommit_verifier_accepts_committed_sql_and_parquet_population(
    session,
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = artifact_root.resolve()
    runtime = project / ".runtime" / "gate3c-fd1"
    artifacts = runtime / "artifacts"
    artifacts.mkdir(parents=True)
    operator_script = runtime / "publish_actual.py"
    operator_bytes = b'"""Bounded synthetic publication operator fixture."""\n'
    operator_script.write_bytes(operator_bytes)
    verifier_script = runtime / "postcommit_verify.py"
    verifier_script.write_bytes(b'"""Bounded synthetic verifier wrapper fixture."""\n')
    tracked_module = project / "server" / "app" / "gen2" / "fd1_postcommit.py"
    tracked_module.parent.mkdir(parents=True)
    tracked_module.write_bytes(Path(fd1_postcommit_module.__file__).read_bytes())
    monkeypatch.setattr(fd1_postcommit_module, "__file__", str(tracked_module))
    combined_revision = build_actual_operator_code_revision(operator_bytes)

    registry = OpenDataRegistry(session)
    source_rows = {}
    for source_key, definition in sorted(FD1_SOURCE_DEFINITIONS.items()):
        source_rows[source_key] = registry.ensure_source_release(
            definition.release_spec()
        )
        registry.ensure_source_decisions(
            definition.owner_spec(), definition.provider_spec()
        )

    retro_source, retro_download = _register_download(
        session,
        source_key=RETROSHEET_SOURCE_KEY,
        object_key="retrosheet-csv-through-2025",
        payload_sha256="1" * 64,
        archive_format=ArchiveFormat.ZIP,
    )
    _, auxiliary_download = _register_download(
        session,
        source_key="RETROSHEET_AUXILIARY_DOWNLOADS",
        object_key="retrosheet-team-and-park-auxiliary",
        payload_sha256="2" * 64,
        archive_format=ArchiveFormat.ZIP,
    )
    _, lahman_download = _register_download(
        session,
        source_key=LAHMAN_SOURCE_KEY,
        object_key="lahman-2025-batting-csv",
        payload_sha256="3" * 64,
        archive_format=ArchiveFormat.NONE,
    )
    _, chadwick_download = _register_download(
        session,
        source_key="CHADWICK_REGISTER_ARCHIVES",
        object_key="chadwick-register-archive",
        payload_sha256="4" * 64,
        archive_format=ArchiveFormat.ZIP,
    )
    member_sha = "5" * 64
    extraction = _extraction(retro_download, member_sha)
    retro_batch = _retrosheet_batch(retro_source.id, member_sha)
    prior_batch = _prior_batch(lahman_download)
    identity_batch = _mapping_batch(
        "CHADWICK_REGISTER_ARCHIVES", chadwick_download.raw_logical_uri
    )
    binding = ContributorDownloadBinding(
        file_sha256=member_sha,
        download_identity_checksum=retro_download.identity_checksum,
        extraction_identity_checksum=extraction.identity_checksum,
        member_relative_path="gameinfo.csv",
    )
    store_root = project / "data" / "curated" / "gen2-fd1"
    store = HistoricalEventParquetStore(
        store_root,
        maximum_store_bytes=1024**3,
        free_space=lambda _path: 200 * 1024**3,
    )
    execution_at = datetime(2026, 8, 14, 13, tzinfo=UTC)
    summary = publish_fd1_warehouse(
        session,
        store,
        retrosheet_batches=(retro_batch,),
        player_prior_batches=(prior_batch,),
        identity_mapping_batches=(identity_batch,),
        contributor_bindings=(binding,),
        extraction_artifacts=(extraction,),
        code_revision=combined_revision,
        execution_at=execution_at,
        allowed_store_roots=(project,),
    )
    session.commit()

    source_payload = b'{"game":"synthetic-2025-game"}\n'
    bridge_document = {"matched": 1, "synthetic": True}
    bridge_payload = json.dumps(bridge_document, sort_keys=True).encode("utf-8")
    reconciliation_document = {"matched_game_count": 1, "synthetic": True}
    reconciliation_payload = json.dumps(
        reconciliation_document, sort_keys=True
    ).encode("utf-8")
    source_path, source_sha, source_bytes = _write_content_addressed(
        artifacts,
        stem="2025-retrosheet-source-evidence",
        suffix="jsonl",
        payload=source_payload,
    )
    bridge_path, bridge_sha, bridge_bytes = _write_content_addressed(
        artifacts,
        stem="2025-schedule-fingerprint-bridge",
        suffix="json",
        payload=bridge_payload,
    )
    reconciliation_path, reconciliation_sha, reconciliation_bytes = (
        _write_content_addressed(
            artifacts,
            stem="2025-asp-retrosheet-full-reconciliation",
            suffix="json",
            payload=reconciliation_payload,
        )
    )

    downloads = (
        retro_download,
        auxiliary_download,
        lahman_download,
        chadwick_download,
    )
    ledger: dict[str, object] = {
        "schema_version": OPERATOR_SCHEMA_VERSION,
        "success": True,
        "success_scope": "PRETRANSACTION_VALIDATION_AND_ARTIFACT_FINALIZATION",
        "mode": "execute",
        "started_at": execution_at.isoformat(),
        "completed_at": execution_at.isoformat(),
        "sanitized": True,
        "transaction": {
            "single_publish_call": True,
            "status_at_closeout": "PENDING_CALLER_COMMIT",
            "committed": False,
            "rolled_back": False,
            "committed_by_publication_function": False,
            "final_result_reported_by_operator_stdout": True,
        },
        "prohibited_activity": {
            "network_requests": 0,
            "provider_transport_constructed": False,
            "mlb_or_savant_automation": False,
            "predictive_model_fit_or_promotion": False,
            "odds_acquisition": False,
            "position_population": False,
        },
        "store": {
            "logical_root": store_root.relative_to(project).as_posix(),
            "default_governed_limits": True,
            "dry_run_parquet_unchanged": False,
        },
        "source_versions": {
            key: {
                "id": str(source_rows[key].id),
                "dataset_version": source_rows[key].dataset_version,
                "identity_checksum": source_rows[key].identity_checksum,
                "source_mode": source_rows[key].source_mode,
            }
            for key in REQUIRED_SOURCE_KEYS
        },
        "registered_downloads_used": [
            _download_ledger(download)
            for download in sorted(
                downloads, key=lambda item: (item.source_key, item.object_key)
            )
        ],
        "publication": summary.as_dict(),
        "readiness_evidence": {
            "prior_period_park_factor_formability": {
                "formable": True,
                "factor_count": 1,
            }
        },
        "retrosheet_materialization": {
            "monthly": [
                {
                    "season": 2024,
                    "month": 6,
                    "family_rows": {"starter_usage": 1},
                    "partition_checksums": {
                        "starter_usage": retro_batch.partition_checksum
                    },
                }
            ]
        },
        "player_priors": {"batches": 1, "rows_by_season": {"2024": 1}},
        "identity": {
            "batches": 1,
            "mappings": 1,
            "by_source": {"CHADWICK_REGISTER_ARCHIVES": 1},
            "by_entity": {"person": 1},
            "by_confidence": {"EXACT": 1},
            "by_namespace": {"retrosheet": 1},
        },
        "retrosheet_source_evidence": {
            "logical_path": Path(source_path).relative_to(project).as_posix(),
            "sha256": source_sha,
            "bytes": source_bytes,
            "record_count": 1,
        },
        "schedule_fingerprint_bridge": {
            "logical_path": Path(bridge_path).relative_to(project).as_posix(),
            "sha256": bridge_sha,
            "bytes": bridge_bytes,
            "summary": {"matched": 1},
        },
        "asp_retrosheet_full_reconciliation": {
            "logical_path": (
                Path(reconciliation_path).relative_to(project).as_posix()
            ),
            "sha256": reconciliation_sha,
            "bytes": reconciliation_bytes,
            "summary": {"matched_game_count": 1},
        },
    }
    ledger_payload = (
        json.dumps(ledger, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    ledger_path, _ledger_sha, _ledger_bytes = _write_content_addressed(
        artifacts,
        stem="pretransaction-closeout",
        suffix="json",
        payload=ledger_payload,
    )
    execute_result = {
        "success": True,
        "mode": "execute",
        "ledger": Path(ledger_path).relative_to(project).as_posix(),
        "code_revision": combined_revision,
        "batches_seen": summary.batches_seen,
        "parquet_rows": summary.parquet_rows,
        "identity_mappings_seen": summary.identity_mappings_seen,
        "park_factors_formable": True,
        "park_factor_count": 1,
        "transaction_committed": True,
        "transaction_rolled_back": False,
        "artifacts_finalized_before_transaction_resolution": True,
        "network_requests": 0,
        "retrosheet_source_evidence": (
            ledger["retrosheet_source_evidence"]["logical_path"]
        ),
        "schedule_fingerprint_bridge": ledger["schedule_fingerprint_bridge"],
        "asp_retrosheet_full_reconciliation": ledger[
            "asp_retrosheet_full_reconciliation"
        ],
    }
    execute_result_path = runtime / "captured-execute-result.json"
    execute_result_path.write_bytes(
        json.dumps(execute_result, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )

    database_before = _database_counts(session)
    session.rollback()
    parquet_before = _parquet_snapshot(store_root)
    output, committed = verify_and_publish_fd1_postcommit(
        session,
        project_root=project,
        execute_result_path=execute_result_path,
        operator_script_path=operator_script,
        verifier_script_path=verifier_script,
        output_directory=artifacts,
        expected_store_root=store_root,
    )

    assert output.is_file()
    assert _sha(output.read_bytes()) in output.name
    assert committed["transaction"]["committed"] is True
    assert committed["transaction"]["status_at_closeout"] == FD1_POSTCOMMIT_STATUS
    proof = committed["postcommit_verification"]
    assert proof["all_checks_passed"] is True
    assert proof["sql"]["ingestion_run_count"] == 3
    assert proof["sql"]["partition_count"] == 2
    assert proof["sql"]["identity_mapping_count"] == 1
    assert proof["parquet"]["partition_count"] == 2
    assert proof["parquet"]["row_count"] == 2
    assert proof["source_registry"]["source_version_count"] == 5
    assert proof["source_registry"]["registered_download_count"] == 4
    assert _parquet_snapshot(store_root) == parquet_before
    assert _database_counts(session) == database_before
    session.rollback()


def test_content_addressed_postcommit_evidence_is_immutable_and_reusable(
    artifact_root: Path,
) -> None:
    output = artifact_root / "artifacts"
    output.mkdir()
    document = {"schema_version": OPERATOR_SCHEMA_VERSION, "committed": True}
    first = _publish_json_evidence(
        document,
        output_directory=output,
        project_root=artifact_root.resolve(),
    )
    second = _publish_json_evidence(
        document,
        output_directory=output,
        project_root=artifact_root.resolve(),
    )
    assert first == second
    assert _sha(first.read_bytes()) in first.name

    first.write_bytes(b"tampered")
    with pytest.raises(FD1PostcommitError, match="changed"):
        _publish_json_evidence(
            document,
            output_directory=output,
            project_root=artifact_root.resolve(),
        )
