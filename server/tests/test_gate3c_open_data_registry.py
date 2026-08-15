from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.gen2.open_data_acquisition import (
    DOWNLOAD_MANIFEST_SCHEMA_VERSION,
    ArchiveFormat,
    AttemptAccounting,
    DownloadArtifact,
    DownloadDisposition,
)
from app.gen2.open_data_registry import (
    OpenDataDownloadSpec,
    OpenDataIdentityMappingSpec,
    OpenDataOwnerDecisionSpec,
    OpenDataProviderDecisionSpec,
    OpenDataRegistry,
    OpenDataRegistryConflictError,
    OpenDataRegistryValidationError,
    OpenDataSourceReleaseSpec,
    build_attribution_identity,
    build_download_manifest_identity,
    build_identity_mapping_identity,
    build_owner_decision_identity,
    build_provider_decision_identity,
    build_source_release_identity,
    canonical_sha256,
)
from app.models import (
    HistoricalSourceVersion,
    OpenDataDownloadManifest,
    OpenDataIdentityMapping,
    ProviderAccessAuthorization,
    SourceOwnerApproval,
)


SOURCE_KEY = "RETROSHEET_OPEN_DATA"
RELEASE = "through_2025"
DECIDED_AT = datetime(2026, 8, 14, 16, tzinfo=UTC)
RETROSHEET_NOTICE = (
    "The information used here was obtained free of\n"
    "charge from and is copyrighted by Retrosheet.  Interested\n"
    'parties may contact Retrosheet at "www.retrosheet.org".'
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _release_spec(
    *,
    source_key: str = SOURCE_KEY,
    dataset_version: str = RELEASE,
    attribution_text: str = RETROSHEET_NOTICE,
) -> OpenDataSourceReleaseSpec:
    return OpenDataSourceReleaseSpec(
        source_key=source_key,
        dataset_version=dataset_version,
        provider_name="Retrosheet",
        effective_from=DECIDED_AT,
        policy_version="gate3c-fd1-open-data-v1",
        documentation_uri="https://www.retrosheet.org/downloads/csvdownloads.html",
        rights_classification=(
            "FREE_DOWNLOAD_COMMERCIAL_DERIVED_USE_WITH_ATTRIBUTION"
        ),
        license_identity="RETROSHEET_OFFICIAL_USE_NOTICE",
        license_uri="https://www.retrosheet.org/notice.txt",
        attribution_text=attribution_text,
        availability_policy={
            "population": "RETROSPECTIVE_RECONSTRUCTION",
            "actual_lineup_timing_risk": True,
            "current_game_excluded_from_priors": True,
        },
        governance={
            "raw_publication": "PROHIBITED_BY_ASP_POLICY",
            "no_endorsement": True,
        },
    )


def _owner_spec(*, source_key: str = SOURCE_KEY) -> OpenDataOwnerDecisionSpec:
    return OpenDataOwnerDecisionSpec(
        source_key=source_key,
        decided_by="project-owner",
        decided_at=DECIDED_AT,
        authorization_reference="https://www.retrosheet.org/notice.txt",
        conditions={
            "official_source_only": True,
            "required_attribution": True,
        },
    )


def _provider_spec(
    *, source_key: str = SOURCE_KEY, provider_name: str = "Retrosheet"
) -> OpenDataProviderDecisionSpec:
    return OpenDataProviderDecisionSpec(
        source_key=source_key,
        provider_name=provider_name,
        access_method="OFFICIAL_HTTPS_DOWNLOAD",
        decided_by="project-owner",
        decided_at=DECIDED_AT + timedelta(minutes=1),
        terms_reference="https://www.retrosheet.org/notice.txt",
        terms_checksum=_sha("retrosheet-official-use-notice"),
        decision_basis=(
            "The independent publisher expressly distributes official downloads "
            "with required attribution."
        ),
    )


def _artifact(
    authorization_identity: str,
    *,
    source_key: str = SOURCE_KEY,
    sha256: str | None = None,
    attempt_number: int = 1,
    content_length: int = 12,
    url: str = "https://www.retrosheet.org/downloads/csvdownloads.zip",
) -> DownloadArtifact:
    started = "2026-08-14T16:05:00Z"
    completed = "2026-08-14T16:05:01Z"
    attempt = AttemptAccounting(
        attempt_number=attempt_number,
        started_at=started,
        completed_at=completed,
        requested_range_start=0,
        status_code=200,
        response_content_length=content_length,
        received_bytes=content_length,
        outcome="SUCCEEDED",
    )
    digest = sha256 or _sha("retrosheet-master-csv")
    return DownloadArtifact(
        schema_version=DOWNLOAD_MANIFEST_SCHEMA_VERSION,
        source_key=source_key,
        object_key="master-csv",
        sanitized_url=url,
        policy_identity_checksum=_sha("retrosheet-download-policy"),
        authorization_identity_checksum=authorization_identity,
        disposition=DownloadDisposition.CREATED_REVISION,
        revision=1,
        content_logical_uri=(
            "retrosheet/release-through-2025/master-csv/"
            f"revisions/r000001-{digest}.blob"
        ),
        manifest_logical_uri=(
            "retrosheet/release-through-2025/master-csv/manifests/"
            "acquisition-20260814T160501Z.json"
        ),
        sha256=digest,
        content_length=content_length,
        mime_type="application/zip",
        archive_format=ArchiveFormat.ZIP,
        http_status=200,
        started_at=started,
        completed_at=completed,
        physical_attempt_count=1,
        transport_bytes_received=content_length,
        attempts=(attempt,),
    )


def _download_spec(*, source_key: str = SOURCE_KEY) -> OpenDataDownloadSpec:
    return OpenDataDownloadSpec(
        source_key=source_key,
        source_release=RELEASE,
        file_name="csvdownloads.zip",
    )


def _mapping_spec(
    *,
    source_key: str = SOURCE_KEY,
    canonical_id: str | None = "chadwick:person:abc123",
    confidence: str = "EXACT",
) -> OpenDataIdentityMappingSpec:
    return OpenDataIdentityMappingSpec(
        source_key=source_key,
        entity_type="person",
        source_namespace="retrosheet",
        external_id="aaroh101",
        canonical_namespace="asp_person",
        canonical_id=canonical_id,
        source_row_number=42,
        source_row_checksum=_sha("allplayers-row-42"),
        confidence=confidence,
        evidence_logical_uri=(
            "retrosheet/release-through-2025/master-csv/"
            "extracted/allplayers.csv"
        ),
    )


def _registered_source_and_decisions(session):
    registry = OpenDataRegistry(session)
    source = registry.ensure_source_release(_release_spec())
    owner, provider = registry.ensure_source_decisions(_owner_spec(), _provider_spec())
    return registry, source, owner, provider


def test_identity_builders_are_canonical_timezone_stable_and_content_sensitive() -> None:
    ordered = _release_spec()
    eastern = timezone(timedelta(hours=-4))
    reordered = OpenDataSourceReleaseSpec(
        source_key=SOURCE_KEY,
        dataset_version=RELEASE,
        provider_name="Retrosheet",
        effective_from=datetime(2026, 8, 14, 12, tzinfo=eastern),
        policy_version="gate3c-fd1-open-data-v1",
        documentation_uri="https://www.retrosheet.org/downloads/csvdownloads.html",
        rights_classification=(
            "FREE_DOWNLOAD_COMMERCIAL_DERIVED_USE_WITH_ATTRIBUTION"
        ),
        license_identity="RETROSHEET_OFFICIAL_USE_NOTICE",
        license_uri="https://www.retrosheet.org/notice.txt",
        attribution_text=RETROSHEET_NOTICE,
        availability_policy={
            "current_game_excluded_from_priors": True,
            "actual_lineup_timing_risk": True,
            "population": "RETROSPECTIVE_RECONSTRUCTION",
        },
        governance={
            "no_endorsement": True,
            "raw_publication": "PROHIBITED_BY_ASP_POLICY",
        },
    )
    assert build_source_release_identity(ordered) == build_source_release_identity(
        reordered
    )
    assert canonical_sha256({"b": [2, 3], "a": 1}) == canonical_sha256(
        {"a": 1, "b": [2, 3]}
    )
    assert build_source_release_identity(
        _release_spec(attribution_text=f"{RETROSHEET_NOTICE} Supplemental notice.")
    ) != build_source_release_identity(ordered)
    assert ordered.attribution_identity == build_attribution_identity(
        source_key=SOURCE_KEY,
        license_identity="RETROSHEET_OFFICIAL_USE_NOTICE",
        attribution_text=RETROSHEET_NOTICE,
    )
    assert len(build_owner_decision_identity(_owner_spec())) == 64


def test_registry_persists_release_decisions_download_and_mapping_idempotently(
    session,
) -> None:
    registry, source, owner, provider = _registered_source_and_decisions(session)

    assert registry.ensure_source_release(_release_spec()).id == source.id
    same_owner, same_provider = registry.ensure_source_decisions(
        _owner_spec(), _provider_spec()
    )
    assert same_owner.id == owner.id
    assert same_provider.id == provider.id
    assert owner.source_key == provider.source_key == source.source_key
    assert provider.owner_approval_id == owner.id
    assert provider.identity_checksum == build_provider_decision_identity(
        owner.identity_checksum, _provider_spec()
    )

    artifact = _artifact(provider.identity_checksum)
    download_spec = _download_spec()
    download = registry.record_download(source, provider, artifact, download_spec)
    duplicate_download = registry.record_download(
        source, provider, artifact, download_spec
    )
    assert duplicate_download.id == download.id
    reused_attempt = replace(
        artifact.attempts[0],
        started_at="2026-08-14T17:05:00Z",
        completed_at="2026-08-14T17:05:01Z",
    )
    reused_artifact = replace(
        artifact,
        disposition=DownloadDisposition.UNCHANGED_BYTES,
        manifest_logical_uri=(
            "retrosheet/release-through-2025/master-csv/manifests/"
            "acquisition-20260814T170501Z.json"
        ),
        started_at=reused_attempt.started_at,
        completed_at=reused_attempt.completed_at,
        attempts=(reused_attempt,),
    )
    assert registry.record_download(
        source, provider, reused_artifact, download_spec
    ).id == download.id
    assert download.source_key == source.source_key
    assert download.source_release == source.dataset_version
    assert download.archive_sha256 == artifact.sha256
    assert download.payload_sha256 == artifact.sha256
    assert download.physical_attempt_count == 1
    assert download.retry_count == 0
    rights = source.governance["open_data_rights"]
    assert download.license_identity == rights["license_identity"]
    assert download.attribution_identity == rights["attribution_identity"]
    assert download.identity_checksum == build_download_manifest_identity(
        source_version_identity_checksum=source.identity_checksum,
        spec=download_spec,
        artifact=artifact,
        license_identity=rights["license_identity"],
        attribution_identity=rights["attribution_identity"],
    )

    mapping_spec = _mapping_spec()
    mapping = registry.record_identity_mapping(source, mapping_spec)
    duplicate_mapping = registry.record_identity_mapping(source, mapping_spec)
    assert duplicate_mapping.id == mapping.id
    assert mapping.identity_checksum == build_identity_mapping_identity(
        source_version_identity_checksum=source.identity_checksum,
        spec=mapping_spec,
    )

    session.commit()
    session.expire_all()
    registry = OpenDataRegistry(session)
    assert registry.ensure_source_release(_release_spec()).id == source.id
    reloaded_owner, reloaded_provider = registry.ensure_source_decisions(
        _owner_spec(), _provider_spec()
    )
    assert reloaded_owner.id == owner.id
    assert reloaded_provider.id == provider.id
    assert session.scalar(select(func.count()).select_from(HistoricalSourceVersion)) == 1
    assert session.scalar(select(func.count()).select_from(SourceOwnerApproval)) == 1
    assert (
        session.scalar(select(func.count()).select_from(ProviderAccessAuthorization))
        == 1
    )
    assert (
        session.scalar(select(func.count()).select_from(OpenDataDownloadManifest))
        == 1
    )
    assert (
        session.scalar(select(func.count()).select_from(OpenDataIdentityMapping))
        == 1
    )


def test_registry_rejects_cross_source_links_and_preserves_conflicting_rows(
    session,
) -> None:
    registry, source, owner, provider = _registered_source_and_decisions(session)
    artifact = _artifact(provider.identity_checksum)
    download = registry.record_download(source, provider, artifact, _download_spec())
    mapping = registry.record_identity_mapping(source, _mapping_spec())
    session.commit()

    changed_artifact = replace(artifact, sha256=_sha("changed-upstream-bytes"))
    with pytest.raises(
        OpenDataRegistryConflictError,
        match="Existing download manifest differs",
    ):
        registry.record_download(
            source, provider, changed_artifact, _download_spec()
        )
    assert download.payload_sha256 == artifact.sha256

    changed_mapping = _mapping_spec(canonical_id="chadwick:person:different")
    with pytest.raises(
        OpenDataRegistryConflictError,
        match="Existing identity mapping differs",
    ):
        registry.record_identity_mapping(source, changed_mapping)
    assert mapping.canonical_id == "chadwick:person:abc123"

    with pytest.raises(
        OpenDataRegistryConflictError,
        match="Existing source release differs",
    ):
        registry.ensure_source_release(
            _release_spec(attribution_text=f"{RETROSHEET_NOTICE}\nChanged")
        )
    assert (
        source.governance["open_data_rights"]["attribution_text"]
        == RETROSHEET_NOTICE
    )

    with pytest.raises(
        OpenDataRegistryConflictError,
        match="Existing owner decision differs",
    ):
        registry.ensure_owner_decision(
            replace(_owner_spec(), conditions={"changed_after_recording": True})
        )
    assert owner.conditions == {
        "official_source_only": True,
        "required_attribution": True,
    }
    with pytest.raises(
        OpenDataRegistryConflictError,
        match="Existing provider decision differs",
    ):
        registry.ensure_provider_decision(
            owner,
            replace(_provider_spec(), decision_basis="Changed after recording."),
        )
    assert provider.decision_basis.startswith("The independent publisher")

    with pytest.raises(OpenDataRegistryValidationError, match="does not match"):
        registry.record_identity_mapping(
            source,
            _mapping_spec(
                source_key="CHADWICK_REGISTER_OPEN_DATA",
                confidence="UNRESOLVED",
                canonical_id=None,
            ),
        )
    with pytest.raises(OpenDataRegistryValidationError, match="exactly match"):
        registry.record_download(
            source,
            provider,
            replace(artifact, source_key="CHADWICK_REGISTER_OPEN_DATA"),
            _download_spec(),
        )
    assert session.scalar(select(func.count()).select_from(OpenDataDownloadManifest)) == 1
    assert session.scalar(select(func.count()).select_from(OpenDataIdentityMapping)) == 1


def test_registry_validation_fails_closed_for_generic_sources_secrets_and_bad_evidence(
    session,
) -> None:
    with pytest.raises(OpenDataRegistryValidationError, match="source-specific"):
        _release_spec(source_key="OPEN_DATA")
    with pytest.raises(OpenDataRegistryValidationError, match="HTTPS"):
        replace(_release_spec(), license_uri="http://www.retrosheet.org/notice.txt")
    with pytest.raises(OpenDataRegistryValidationError, match="attribution_text"):
        _release_spec(attribution_text="")
    with pytest.raises(OpenDataRegistryValidationError, match="secret-like"):
        replace(
            _owner_spec(),
            authorization_reference="https://example.test/decision?token=secret-value",
        )
    with pytest.raises(OpenDataRegistryValidationError, match="lowercase SHA-256"):
        replace(_mapping_spec(), source_row_checksum="A" * 64)

    registry, source, _, provider = _registered_source_and_decisions(session)
    with pytest.raises(OpenDataRegistryValidationError, match="nonempty"):
        registry.record_download(
            source,
            provider,
            _artifact(provider.identity_checksum, content_length=0),
            _download_spec(),
        )
    with pytest.raises(OpenDataRegistryValidationError, match="contiguous"):
        registry.record_download(
            source,
            provider,
            _artifact(provider.identity_checksum, attempt_number=2),
            _download_spec(),
        )
    with pytest.raises(OpenDataRegistryValidationError, match="secret-like"):
        registry.record_download(
            source,
            provider,
            _artifact(
                provider.identity_checksum,
                url="https://www.retrosheet.org/downloads/file.zip?api_key=hidden",
            ),
            _download_spec(),
        )

    denied_owner = registry.ensure_owner_decision(
        replace(
            _owner_spec(source_key="LAHMAN_OPEN_DATA"),
            approved=False,
            decided_at=DECIDED_AT + timedelta(hours=1),
            authorization_reference="https://sabr.org/lahman-database/",
        )
    )
    with pytest.raises(OpenDataRegistryValidationError, match="approved owner"):
        registry.ensure_provider_decision(
            denied_owner,
            replace(
                _provider_spec(
                    source_key="LAHMAN_OPEN_DATA",
                    provider_name="SABR",
                ),
                decided_at=DECIDED_AT + timedelta(hours=1, minutes=1),
                terms_reference="https://sabr.org/lahman-database/",
            ),
        )

    assert session.scalar(select(func.count()).select_from(OpenDataDownloadManifest)) == 0
