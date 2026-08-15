from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.gen2.fd1_pipeline import DOWNLOAD_CATALOG, FD1_SOURCE_POLICIES
from app.gen2.fd1_registry_sync import (
    FD1_ACQUIRED_SOURCE_KEYS,
    FD1_SOURCE_DEFINITIONS,
    FD1RegistrySyncError,
    FD1RegistrySyncPlan,
    bridge_artifact_for_registry,
    build_registry_sync_plan,
    dry_run_registry_sync,
    load_download_artifact,
    main,
    synchronize_registry,
)
from app.gen2.open_data_acquisition import (
    DOWNLOAD_MANIFEST_SCHEMA_VERSION,
    ArchiveFormat,
    AttemptAccounting,
    DownloadArtifact,
    DownloadDisposition,
)
from app.models import (
    HistoricalSourceVersion,
    OpenDataDownloadManifest,
    ProviderAccessAuthorization,
    SourceOwnerApproval,
)


SOURCE = "RETROSHEET_CSV_DOWNLOADS"
OBJECT = "retrosheet-notice"
STARTED = "2026-08-14T01:00:00.000000+00:00"
COMPLETED = "2026-08-14T01:00:01.000000+00:00"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _notice_artifact(content: bytes = b"test Retrosheet notice\n") -> DownloadArtifact:
    policy = FD1_SOURCE_POLICIES[SOURCE]
    digest = _sha(content)
    logical_root = "open-data/retrosheet-csv-downloads/retrosheet-notice"
    attempt = AttemptAccounting(
        attempt_number=1,
        started_at=STARTED,
        completed_at=COMPLETED,
        requested_range_start=0,
        status_code=200,
        response_content_length=len(content),
        received_bytes=len(content),
        outcome="SUCCEEDED",
    )
    return DownloadArtifact(
        schema_version=DOWNLOAD_MANIFEST_SCHEMA_VERSION,
        source_key=SOURCE,
        object_key=OBJECT,
        sanitized_url="https://www.retrosheet.org/notice.txt",
        policy_identity_checksum=policy.identity_checksum,
        authorization_identity_checksum=policy.authorization_identity_checksum or "",
        disposition=DownloadDisposition.CREATED_REVISION,
        revision=1,
        content_logical_uri=(
            f"{logical_root}/revisions/r000001-{digest}.blob"
        ),
        manifest_logical_uri=(
            f"{logical_root}/manifests/"
            "acquisition-2026-08-14T010001.000000_0000-"
            "0123456789abcdef0123456789abcdef.json"
        ),
        sha256=digest,
        content_length=len(content),
        mime_type="text/plain",
        archive_format=ArchiveFormat.NONE,
        http_status=200,
        started_at=STARTED,
        completed_at=COMPLETED,
        physical_attempt_count=1,
        transport_bytes_received=len(content),
        attempts=(attempt,),
    )


def _write_artifact(
    raw_root: Path,
    artifact: DownloadArtifact,
    *,
    content: bytes | None = None,
    payload: dict | None = None,
) -> Path:
    manifest = raw_root.joinpath(*Path(artifact.manifest_logical_uri).parts)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(payload if payload is not None else artifact.manifest(), sort_keys=True),
        encoding="utf-8",
    )
    if content is not None:
        blob = raw_root.joinpath(*Path(artifact.content_logical_uri).parts)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(content)
    return manifest


def _local_plan(tmp_path: Path) -> tuple[FD1RegistrySyncPlan, DownloadArtifact]:
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    content = b"test Retrosheet notice\n"
    artifact = _notice_artifact(content)
    _write_artifact(raw_root, artifact, content=content)
    return build_registry_sync_plan(raw_root=raw_root), artifact


def test_source_governance_is_exactly_the_five_acquired_catalog_sources() -> None:
    assert set(FD1_SOURCE_DEFINITIONS) == set(FD1_ACQUIRED_SOURCE_KEYS) == {
        "RETROSHEET_CSV_DOWNLOADS",
        "RETROSHEET_EVENT_ARCHIVES",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "CHADWICK_REGISTER_ARCHIVES",
        "SABR_LAHMAN_BOX_DOWNLOADS",
    }
    assert {item.request.source_key for item in DOWNLOAD_CATALOG} == set(
        FD1_ACQUIRED_SOURCE_KEYS
    )
    assert "NOAA_OPEN_DATA" not in FD1_SOURCE_DEFINITIONS


def test_loader_reconstructs_exact_artifact_and_dry_run_is_path_free(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    content = b"test Retrosheet notice\n"
    artifact = _notice_artifact(content)
    path = _write_artifact(raw_root, artifact, content=content)

    loaded = load_download_artifact(path, raw_root=raw_root)
    assert loaded == artifact
    assert loaded.manifest() == artifact.manifest()

    summary = dry_run_registry_sync(raw_root=raw_root)
    serialized = json.dumps(summary, sort_keys=True)
    assert summary["mode"] == "dry-run"
    assert summary["network_requests"] == 0
    assert summary["provider_transport_constructed"] is False
    assert summary["absolute_paths_emitted"] is False
    assert summary["manifest_observations"] == 1
    assert summary["immutable_download_rows"] == 1
    assert str(tmp_path) not in serialized


def test_registry_sync_bridges_both_identities_and_is_idempotent(
    tmp_path: Path, session
) -> None:
    plan, raw_artifact = _local_plan(tmp_path)
    definition = FD1_SOURCE_DEFINITIONS[SOURCE]

    first = synchronize_registry(session, plan=plan)
    assert first["database"]["created_rows"] == {
        "historical_source_versions": 1,
        "source_owner_approvals": 1,
        "provider_access_authorizations": 1,
        "open_data_download_manifests": 1,
    }
    source = session.scalar(select(HistoricalSourceVersion))
    owner = session.scalar(select(SourceOwnerApproval))
    provider = session.scalar(select(ProviderAccessAuthorization))
    download = session.scalar(select(OpenDataDownloadManifest))
    assert source is not None and owner is not None and provider is not None
    assert download is not None
    assert source.source_key == owner.source_key == provider.source_key == SOURCE
    assert provider.owner_approval_id == owner.id
    assert provider.identity_checksum == definition.provider_decision_identity
    bridge = source.governance["fd1_authorization_bridge"]
    assert bridge["acquisition_authorization_identity_checksum"] == (
        raw_artifact.authorization_identity_checksum
    )
    assert bridge["provider_decision_identity_checksum"] == provider.identity_checksum
    assert raw_artifact.authorization_identity_checksum != provider.identity_checksum
    assert download.source_version_id == source.id
    assert download.source_key == SOURCE
    assert download.raw_logical_uri == raw_artifact.content_logical_uri

    second = synchronize_registry(session, plan=plan)
    assert set(second["database"]["created_rows"].values()) == {0}
    assert session.scalar(select(func.count()).select_from(HistoricalSourceVersion)) == 1
    assert session.scalar(select(func.count()).select_from(SourceOwnerApproval)) == 1
    assert (
        session.scalar(select(func.count()).select_from(ProviderAccessAuthorization))
        == 1
    )
    assert (
        session.scalar(select(func.count()).select_from(OpenDataDownloadManifest)) == 1
    )


def test_bridge_rejects_relabeling_and_governance_tampering(tmp_path: Path) -> None:
    plan, artifact = _local_plan(tmp_path)
    assert plan.artifacts == (artifact,)
    definition = FD1_SOURCE_DEFINITIONS[SOURCE]
    governance = definition.release_spec().governance

    projected = bridge_artifact_for_registry(
        artifact,
        provider_identity_checksum=definition.provider_decision_identity,
        source_governance=governance,
    )
    assert projected is not artifact
    assert projected.authorization_identity_checksum == (
        definition.provider_decision_identity
    )
    assert artifact.authorization_identity_checksum == (
        FD1_SOURCE_POLICIES[SOURCE].authorization_identity_checksum
    )

    pre_relabelled = replace(
        artifact,
        authorization_identity_checksum=definition.provider_decision_identity,
    )
    with pytest.raises(FD1RegistrySyncError, match="frozen FD1 source policy"):
        bridge_artifact_for_registry(
            pre_relabelled,
            provider_identity_checksum=definition.provider_decision_identity,
            source_governance=governance,
        )

    altered_governance = dict(governance)
    altered_bridge = dict(altered_governance["fd1_authorization_bridge"])
    altered_bridge["provider_decision_identity_checksum"] = "f" * 64
    altered_governance["fd1_authorization_bridge"] = altered_bridge
    with pytest.raises(FD1RegistrySyncError, match="differs from the frozen bridge"):
        bridge_artifact_for_registry(
            artifact,
            provider_identity_checksum=definition.provider_decision_identity,
            source_governance=altered_governance,
        )

    with pytest.raises(FD1RegistrySyncError, match="not bound by source governance"):
        bridge_artifact_for_registry(
            artifact,
            provider_identity_checksum="f" * 64,
            source_governance=governance,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            {"policy_identity_checksum": "e" * 64},
            "authorization does not match",
        ),
        (
            {"authorization_identity_checksum": "e" * 64},
            "authorization does not match",
        ),
        (
            {"sanitized_url": "https://www.retrosheet.org/notice.txt?token=hidden"},
            "Secret-like manifest value",
        ),
        (
            {"content_logical_uri": "C:/private/raw/notice.txt"},
            "content URI is not content-addressed",
        ),
        (
            {"source_key": "NOAA_OPEN_DATA"},
            "not an acquired FD1 source",
        ),
    ],
)
def test_loader_fails_closed_on_policy_secret_path_and_unknown_source(
    tmp_path: Path, mutation: dict[str, object], message: str
) -> None:
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    content = b"test Retrosheet notice\n"
    artifact = _notice_artifact(content)
    payload = artifact.manifest()
    payload.update(mutation)
    path = _write_artifact(raw_root, artifact, content=content, payload=payload)
    with pytest.raises(FD1RegistrySyncError, match=message):
        load_download_artifact(path, raw_root=raw_root)


def test_loader_rejects_duplicate_json_keys_and_tampered_blob(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    content = b"test Retrosheet notice\n"
    artifact = _notice_artifact(content)
    path = _write_artifact(raw_root, artifact, content=content)
    original = path.read_text(encoding="utf-8")
    path.write_text(
        '{"schema_version":"duplicate",' + original.lstrip()[1:],
        encoding="utf-8",
    )
    with pytest.raises(FD1RegistrySyncError, match="duplicate key"):
        load_download_artifact(path, raw_root=raw_root)

    path.write_text(json.dumps(artifact.manifest()), encoding="utf-8")
    blob = raw_root.joinpath(*Path(artifact.content_logical_uri).parts)
    blob.write_bytes(b"x" * artifact.content_length)
    with pytest.raises(FD1RegistrySyncError, match="checksum differs"):
        load_download_artifact(path, raw_root=raw_root)


def test_exact_pre_rm_sabr_policy_lineage_is_accepted_but_no_other_policy(
    tmp_path: Path, session
) -> None:
    source = "SABR_LAHMAN_BOX_DOWNLOADS"
    object_key = "lahman-2025-readme"
    current = FD1_SOURCE_POLICIES[source]
    predecessor = replace(
        current, allowed_query_keys=("file_id", "shared_name")
    )
    digest = "8f3d8f12b638887e4d182561a668e8103e11fbe1483019cae6d12a4c67cc3aab"
    logical = f"open-data/sabr-lahman-box-downloads/{object_key}"
    attempt = AttemptAccounting(
        attempt_number=1,
        started_at=STARTED,
        completed_at=COMPLETED,
        requested_range_start=0,
        status_code=200,
        response_content_length=47_982,
        received_bytes=47_982,
        outcome="SUCCEEDED",
    )
    artifact = DownloadArtifact(
        schema_version=DOWNLOAD_MANIFEST_SCHEMA_VERSION,
        source_key=source,
        object_key=object_key,
        sanitized_url=(
            "https://sabr.box.com/shared/static/"
            "qtgh1olzcaauz5x234wqx8huixizff8l.txt"
        ),
        policy_identity_checksum=predecessor.identity_checksum,
        authorization_identity_checksum=current.authorization_identity_checksum or "",
        disposition=DownloadDisposition.CREATED_REVISION,
        revision=1,
        content_logical_uri=f"{logical}/revisions/r000001-{digest}.blob",
        manifest_logical_uri=(
            f"{logical}/manifests/"
            "acquisition-2026-08-14T010001.000000_0000-"
            "fedcba9876543210fedcba9876543210.json"
        ),
        sha256=digest,
        content_length=47_982,
        mime_type="text/plain",
        archive_format=ArchiveFormat.NONE,
        http_status=200,
        started_at=STARTED,
        completed_at=COMPLETED,
        physical_attempt_count=1,
        transport_bytes_received=47_982,
        attempts=(attempt,),
    )
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    path = _write_artifact(raw_root, artifact)
    assert load_download_artifact(
        path, raw_root=raw_root, verify_content=False
    ) == artifact

    successor = replace(
        artifact,
        policy_identity_checksum=current.identity_checksum,
        disposition=DownloadDisposition.UNCHANGED_BYTES,
        started_at="2026-08-14T01:01:00.000000+00:00",
        completed_at="2026-08-14T01:01:01.000000+00:00",
        manifest_logical_uri=(
            f"{logical}/manifests/"
            "acquisition-2026-08-14T010101.000000_0000-"
            "0123456789abcdef0123456789abcdef.json"
        ),
    )
    plan = FD1RegistrySyncPlan(
        artifacts=(artifact, successor), content_verified=True
    )
    result = synchronize_registry(session, plan=plan)
    assert result["manifest_observations"] == 2
    assert result["immutable_download_rows"] == 1
    assert result["database"]["created_rows"]["open_data_download_manifests"] == 1
    persisted = session.scalar(select(OpenDataDownloadManifest))
    assert persisted is not None
    assert persisted.downloaded_at.replace(tzinfo=UTC) == datetime.fromisoformat(COMPLETED)

    payload = artifact.manifest()
    payload["policy_identity_checksum"] = "d" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FD1RegistrySyncError, match="authorization does not match"):
        load_download_artifact(path, raw_root=raw_root, verify_content=False)


def test_dry_run_cli_emits_no_physical_path(tmp_path: Path, capsys) -> None:
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    content = b"test Retrosheet notice\n"
    artifact = _notice_artifact(content)
    _write_artifact(raw_root, artifact, content=content)

    assert main(["dry-run", "--raw-root", str(raw_root)]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["mode"] == "dry-run"
    assert payload["network_requests"] == 0
    assert str(raw_root) not in output
