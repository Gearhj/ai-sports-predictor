"""Append-only registry for governed open-data releases and physical evidence.

The registry is deliberately provider-free: it accepts validated acquisition
artifacts, but it cannot construct a transport or make a network request.  All
identities are content-derived from canonical JSON and exclude random database
primary keys so the same evidence has the same identity in every database.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.gen2.open_data_acquisition import (
    ArchiveFormat,
    DownloadArtifact,
    DownloadDisposition,
)
from app.models import (
    HistoricalSourceVersion,
    OpenDataDownloadManifest,
    OpenDataIdentityMapping,
    ProviderAccessAuthorization,
    SourceOwnerApproval,
)


OPEN_DATA_REGISTRY_SCHEMA_VERSION = "gen2_open_data_registry_v1"
LOCAL_PRIVATE_RESEARCH = "LOCAL_PRIVATE_RESEARCH"
RETROSPECTIVE_RECONSTRUCTION = "RETROSPECTIVE_RECONSTRUCTION"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{2,79}$")
_NAMESPACE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
_GENERIC_SOURCE_KEYS = frozenset(
    {"BASEBALL", "BASEBALL_DATA", "FREE_BASEBALL_DATA", "OPEN_DATA"}
)
_SOURCE_CLASSIFICATIONS = frozenset(
    {
        "OFFICIAL_PRIMARY",
        "LICENSED_MARKET",
        "PUBLIC_SECONDARY",
        "DERIVED_INTERNAL",
        "SYNTHETIC",
    }
)
_QUALITY_TIERS = frozenset({"A", "B", "C", "EXCLUDED"})
_PROVIDER_DECISIONS = frozenset({"AUTHORIZED", "DENIED", "UNRESOLVED"})
_ENTITY_TYPES = frozenset(
    {"person", "team", "franchise", "park", "game", "umpire"}
)
_CONFIDENCE_VALUES = frozenset({"EXACT", "HIGH", "UNRESOLVED", "CONFLICT"})
_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "signature",
    "signed_url",
    "token",
)


class OpenDataRegistryError(RuntimeError):
    """Base error for invalid, conflicting, or unlinked registry evidence."""


class OpenDataRegistryValidationError(OpenDataRegistryError, ValueError):
    """The proposed evidence cannot satisfy the immutable registry contract."""


class OpenDataRegistryConflictError(OpenDataRegistryError):
    """A natural identity already exists with different immutable evidence."""


@dataclass(frozen=True, slots=True)
class OpenDataSourceReleaseSpec:
    """Source-specific rights and timing policy for one immutable release."""

    source_key: str
    dataset_version: str
    provider_name: str
    effective_from: datetime
    policy_version: str
    documentation_uri: str
    rights_classification: str
    license_identity: str
    license_uri: str
    attribution_text: str
    availability_policy: Mapping[str, Any]
    source_classification: str = "PUBLIC_SECONDARY"
    quality_tier: str = "A"
    training_eligible: bool = True
    evaluation_eligible: bool = True
    governance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_source_key(self.source_key)
        _required_text(self.dataset_version, "dataset_version", maximum=80)
        _required_text(self.provider_name, "provider_name", maximum=120)
        _aware_utc(self.effective_from, "effective_from")
        _required_text(self.policy_version, "policy_version", maximum=40)
        _validate_https_uri(self.documentation_uri, "documentation_uri")
        _required_text(
            self.rights_classification, "rights_classification", maximum=160
        )
        _required_text(self.license_identity, "license_identity", maximum=160)
        _validate_https_uri(self.license_uri, "license_uri")
        _required_text(
            self.attribution_text,
            "attribution_text",
            maximum=8_000,
            allow_multiline=True,
        )
        _validate_json_mapping(self.availability_policy, "availability_policy", nonempty=True)
        _validate_json_mapping(self.governance, "governance")
        if self.source_classification not in _SOURCE_CLASSIFICATIONS:
            raise OpenDataRegistryValidationError("source_classification is invalid")
        if self.quality_tier not in _QUALITY_TIERS:
            raise OpenDataRegistryValidationError("quality_tier is invalid")
        if self.training_eligible and not self.evaluation_eligible:
            raise OpenDataRegistryValidationError(
                "Training eligibility requires evaluation eligibility"
            )
        if (self.training_eligible or self.evaluation_eligible) and (
            self.source_classification == "SYNTHETIC" or self.quality_tier == "EXCLUDED"
        ):
            raise OpenDataRegistryValidationError(
                "Synthetic or excluded evidence cannot be eligible"
            )
        for reserved in ("open_data_rights", "open_data_registry_schema_version"):
            if reserved in self.governance:
                raise OpenDataRegistryValidationError(
                    f"governance cannot override reserved key {reserved!r}"
                )

    @property
    def attribution_identity(self) -> str:
        return build_attribution_identity(
            source_key=self.source_key,
            license_identity=self.license_identity,
            attribution_text=self.attribution_text,
        )


@dataclass(frozen=True, slots=True)
class OpenDataOwnerDecisionSpec:
    """One source-specific project-owner decision."""

    source_key: str
    decided_by: str
    decided_at: datetime
    authorization_reference: str
    approved: bool = True
    expires_at: datetime | None = None
    conditions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_source_key(self.source_key)
        _required_text(self.decided_by, "decided_by", maximum=160)
        decided = _aware_utc(self.decided_at, "decided_at")
        _validate_https_uri(self.authorization_reference, "authorization_reference")
        _validate_json_mapping(self.conditions, "conditions")
        if self.expires_at is not None:
            expires = _aware_utc(self.expires_at, "expires_at")
            if expires <= decided:
                raise OpenDataRegistryValidationError(
                    "Owner decision expiry must be after decided_at"
                )

    @property
    def decision(self) -> str:
        return "APPROVED" if self.approved else "DENIED"


@dataclass(frozen=True, slots=True)
class OpenDataProviderDecisionSpec:
    """One source-specific official-download access decision."""

    source_key: str
    provider_name: str
    access_method: str
    decided_by: str
    decided_at: datetime
    terms_reference: str
    terms_checksum: str
    decision_basis: str
    decision: str = "AUTHORIZED"
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_source_key(self.source_key)
        _required_text(self.provider_name, "provider_name", maximum=120)
        _required_text(self.access_method, "access_method", maximum=80)
        _required_text(self.decided_by, "decided_by", maximum=160)
        decided = _aware_utc(self.decided_at, "decided_at")
        _validate_https_uri(self.terms_reference, "terms_reference")
        _validate_sha256(self.terms_checksum, "terms_checksum")
        _required_text(
            self.decision_basis,
            "decision_basis",
            maximum=20_000,
            allow_multiline=True,
        )
        if self.decision not in _PROVIDER_DECISIONS:
            raise OpenDataRegistryValidationError("Provider decision is invalid")
        if self.decision == "AUTHORIZED" and self.access_method == "UNSPECIFIED":
            raise OpenDataRegistryValidationError(
                "Authorized access requires an explicit access_method"
            )
        if self.expires_at is not None:
            expires = _aware_utc(self.expires_at, "expires_at")
            if expires <= decided:
                raise OpenDataRegistryValidationError(
                    "Provider decision expiry must be after decided_at"
                )

    @property
    def authorized(self) -> bool:
        return self.decision == "AUTHORIZED"


@dataclass(frozen=True, slots=True)
class OpenDataDownloadSpec:
    """Metadata not carried by :class:`DownloadArtifact`."""

    source_key: str
    source_release: str
    file_name: str

    def __post_init__(self) -> None:
        _validate_source_key(self.source_key)
        _required_text(self.source_release, "source_release", maximum=120)
        _validate_file_name(self.file_name)


@dataclass(frozen=True, slots=True)
class OpenDataIdentityMappingSpec:
    """One row-bound external-to-canonical identity assertion."""

    source_key: str
    entity_type: str
    source_namespace: str
    external_id: str
    canonical_namespace: str
    canonical_id: str | None
    source_row_number: int
    source_row_checksum: str
    confidence: str
    evidence_logical_uri: str
    valid_from: date | None = None
    valid_through: date | None = None

    def __post_init__(self) -> None:
        _validate_source_key(self.source_key)
        if self.entity_type not in _ENTITY_TYPES:
            raise OpenDataRegistryValidationError("entity_type is invalid")
        _validate_namespace(self.source_namespace, "source_namespace")
        _required_text(self.external_id, "external_id", maximum=255)
        _validate_namespace(self.canonical_namespace, "canonical_namespace")
        if self.canonical_id is not None:
            _required_text(self.canonical_id, "canonical_id", maximum=255)
        if (
            not isinstance(self.source_row_number, int)
            or isinstance(self.source_row_number, bool)
            or self.source_row_number < 1
        ):
            raise OpenDataRegistryValidationError("source_row_number must be positive")
        _validate_sha256(self.source_row_checksum, "source_row_checksum")
        if self.confidence not in _CONFIDENCE_VALUES:
            raise OpenDataRegistryValidationError("confidence is invalid")
        if self.confidence in {"EXACT", "HIGH"} and self.canonical_id is None:
            raise OpenDataRegistryValidationError(
                "Resolved mappings require a canonical_id"
            )
        if self.confidence in {"UNRESOLVED", "CONFLICT"} and self.canonical_id is not None:
            raise OpenDataRegistryValidationError(
                "Unresolved or conflicting mappings cannot assert a canonical_id"
            )
        _validate_logical_uri(self.evidence_logical_uri, "evidence_logical_uri")
        if (
            self.valid_from is not None
            and self.valid_through is not None
            and self.valid_through < self.valid_from
        ):
            raise OpenDataRegistryValidationError(
                "valid_through cannot precede valid_from"
            )


def canonical_sha256(value: Any) -> str:
    """Return a deterministic lowercase SHA-256 over canonical JSON."""

    normalized = _canonical_value(value, "identity")
    try:
        encoded = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:  # defensive; normalization is strict
        raise OpenDataRegistryValidationError(
            "Identity payload is not canonical JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def build_attribution_identity(
    *, source_key: str, license_identity: str, attribution_text: str
) -> str:
    _validate_source_key(source_key)
    _required_text(license_identity, "license_identity", maximum=160)
    _required_text(
        attribution_text,
        "attribution_text",
        maximum=8_000,
        allow_multiline=True,
    )
    return _record_identity(
        "OPEN_DATA_ATTRIBUTION",
        {
            "source_key": source_key,
            "license_identity": license_identity,
            "attribution_text": attribution_text,
        },
    )


def build_source_release_identity(spec: OpenDataSourceReleaseSpec) -> str:
    return _record_identity("OPEN_DATA_SOURCE_RELEASE", _source_release_fields(spec))


def build_owner_decision_identity(spec: OpenDataOwnerDecisionSpec) -> str:
    return _record_identity("OPEN_DATA_OWNER_DECISION", _owner_fields(spec))


def build_provider_decision_identity(
    owner_identity_checksum: str,
    spec: OpenDataProviderDecisionSpec,
) -> str:
    _validate_sha256(owner_identity_checksum, "owner_identity_checksum")
    return _record_identity(
        "OPEN_DATA_PROVIDER_DECISION",
        {
            **_provider_fields(spec),
            "owner_approval_identity_checksum": owner_identity_checksum,
        },
    )


def build_download_semantic_fingerprint(
    *,
    source_version_identity_checksum: str,
    spec: OpenDataDownloadSpec,
    artifact: DownloadArtifact,
) -> str:
    _validate_sha256(
        source_version_identity_checksum, "source_version_identity_checksum"
    )
    _validate_download_artifact(artifact)
    return _record_identity(
        "OPEN_DATA_DOWNLOAD_SEMANTIC_FINGERPRINT",
        {
            "source_version_identity_checksum": source_version_identity_checksum,
            "source_key": spec.source_key,
            "object_key": artifact.object_key,
            "source_release": spec.source_release,
            "upstream_revision": artifact.revision,
            "official_download_uri": artifact.sanitized_url,
            "file_name": spec.file_name,
            "mime_type": artifact.mime_type,
            "payload_sha256": artifact.sha256,
            "archive_sha256": (
                artifact.sha256
                if artifact.archive_format is not ArchiveFormat.NONE
                else None
            ),
            "content_length": artifact.content_length,
            "raw_logical_uri": artifact.content_logical_uri,
            "policy_identity_checksum": artifact.policy_identity_checksum,
            "authorization_identity_checksum": (
                artifact.authorization_identity_checksum
            ),
        },
    )


def build_download_manifest_identity(
    *,
    source_version_identity_checksum: str,
    spec: OpenDataDownloadSpec,
    artifact: DownloadArtifact,
    license_identity: str,
    attribution_identity: str,
) -> str:
    semantic = build_download_semantic_fingerprint(
        source_version_identity_checksum=source_version_identity_checksum,
        spec=spec,
        artifact=artifact,
    )
    _required_text(license_identity, "license_identity", maximum=160)
    _validate_sha256(attribution_identity, "attribution_identity")
    attempts, retry_count = _validated_attempts(artifact)
    fields = {
        "source_version_identity_checksum": source_version_identity_checksum,
        "source_key": spec.source_key,
        "object_key": artifact.object_key,
        "source_release": spec.source_release,
        "upstream_revision": artifact.revision,
        "official_download_uri": artifact.sanitized_url,
        "downloaded_at": _parse_aware_datetime(artifact.completed_at, "completed_at"),
        "http_status": artifact.http_status,
        "content_length": artifact.content_length,
        "file_name": spec.file_name,
        "mime_type": artifact.mime_type,
        "payload_sha256": artifact.sha256,
        "archive_sha256": (
            artifact.sha256 if artifact.archive_format is not ArchiveFormat.NONE else None
        ),
        "license_identity": license_identity,
        "attribution_identity": attribution_identity,
        "physical_attempt_count": len(attempts),
        "retry_count": retry_count,
        "raw_logical_uri": artifact.content_logical_uri,
        "semantic_fingerprint": semantic,
    }
    return _record_identity("OPEN_DATA_DOWNLOAD_MANIFEST", fields)


def build_identity_mapping_identity(
    *,
    source_version_identity_checksum: str,
    spec: OpenDataIdentityMappingSpec,
) -> str:
    _validate_sha256(
        source_version_identity_checksum, "source_version_identity_checksum"
    )
    return _record_identity(
        "OPEN_DATA_IDENTITY_MAPPING",
        {
            "source_version_identity_checksum": source_version_identity_checksum,
            **_identity_mapping_fields(spec),
        },
    )


class OpenDataRegistry:
    """Idempotently append immutable open-data evidence to a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def require_identity_mapping_source(
        self, source_version: HistoricalSourceVersion
    ) -> HistoricalSourceVersion:
        """Revalidate one persisted source before a set-based identity append."""

        return self._require_source_version(source_version)

    def ensure_source_release(
        self, spec: OpenDataSourceReleaseSpec
    ) -> HistoricalSourceVersion:
        fields = _source_release_fields(spec)
        identity = build_source_release_identity(spec)
        natural = self.session.scalar(
            select(HistoricalSourceVersion).where(
                HistoricalSourceVersion.source_key == spec.source_key,
                HistoricalSourceVersion.dataset_version == spec.dataset_version,
                HistoricalSourceVersion.source_mode == "LIVE",
            )
        )
        expected = {**fields, "identity_checksum": identity}
        if natural is not None:
            _assert_existing_matches(natural, expected, "source release")
            return natural
        collision = self.session.scalar(
            select(HistoricalSourceVersion).where(
                HistoricalSourceVersion.identity_checksum == identity
            )
        )
        if collision is not None:
            raise OpenDataRegistryConflictError(
                "Source-release identity is already bound to another natural key"
            )
        row = HistoricalSourceVersion(**expected)
        self.session.add(row)
        self.session.flush()
        return row

    def ensure_owner_decision(
        self, spec: OpenDataOwnerDecisionSpec
    ) -> SourceOwnerApproval:
        fields = _owner_fields(spec)
        identity = build_owner_decision_identity(spec)
        natural = self.session.scalar(
            select(SourceOwnerApproval).where(
                SourceOwnerApproval.source_key == spec.source_key,
                SourceOwnerApproval.approval_scope == LOCAL_PRIVATE_RESEARCH,
                SourceOwnerApproval.decided_by == spec.decided_by,
                SourceOwnerApproval.decided_at == _aware_utc(spec.decided_at, "decided_at"),
            )
        )
        expected = {**fields, "identity_checksum": identity}
        if natural is not None:
            _assert_existing_matches(natural, expected, "owner decision")
            return natural
        collision = self.session.scalar(
            select(SourceOwnerApproval).where(
                SourceOwnerApproval.identity_checksum == identity
            )
        )
        if collision is not None:
            raise OpenDataRegistryConflictError(
                "Owner-decision identity is already bound to another natural key"
            )
        row = SourceOwnerApproval(**expected)
        self.session.add(row)
        self.session.flush()
        return row

    def ensure_provider_decision(
        self,
        owner: SourceOwnerApproval,
        spec: OpenDataProviderDecisionSpec,
    ) -> ProviderAccessAuthorization:
        persisted_owner = self._require_owner(owner)
        if persisted_owner.source_key != spec.source_key:
            raise OpenDataRegistryValidationError(
                "Provider decision source_key does not match its owner decision"
            )
        provider_time = _aware_utc(spec.decided_at, "decided_at")
        owner_time = _stored_utc(persisted_owner.decided_at, "owner.decided_at")
        if provider_time < owner_time:
            raise OpenDataRegistryValidationError(
                "Provider decision cannot predate its owner decision"
            )
        if spec.authorized:
            if not persisted_owner.approved or persisted_owner.decision != "APPROVED":
                raise OpenDataRegistryValidationError(
                    "Authorized provider access requires an approved owner decision"
                )
            if (
                persisted_owner.expires_at is not None
                and provider_time >= _stored_utc(
                    persisted_owner.expires_at, "owner.expires_at"
                )
            ):
                raise OpenDataRegistryValidationError(
                    "Authorized provider decision is outside the owner window"
                )
        fields = _provider_fields(spec)
        identity = build_provider_decision_identity(
            persisted_owner.identity_checksum, spec
        )
        natural = self.session.scalar(
            select(ProviderAccessAuthorization).where(
                ProviderAccessAuthorization.owner_approval_id == persisted_owner.id,
                ProviderAccessAuthorization.source_key == spec.source_key,
                ProviderAccessAuthorization.access_method == spec.access_method,
                ProviderAccessAuthorization.decided_by == spec.decided_by,
                ProviderAccessAuthorization.decided_at == provider_time,
            )
        )
        expected = {
            "owner_approval_id": persisted_owner.id,
            **fields,
            "identity_checksum": identity,
        }
        if natural is not None:
            _assert_existing_matches(natural, expected, "provider decision")
            return natural
        collision = self.session.scalar(
            select(ProviderAccessAuthorization).where(
                ProviderAccessAuthorization.identity_checksum == identity
            )
        )
        if collision is not None:
            raise OpenDataRegistryConflictError(
                "Provider-decision identity is already bound to another natural key"
            )
        row = ProviderAccessAuthorization(**expected)
        self.session.add(row)
        self.session.flush()
        return row

    def ensure_source_decisions(
        self,
        owner_spec: OpenDataOwnerDecisionSpec,
        provider_spec: OpenDataProviderDecisionSpec,
    ) -> tuple[SourceOwnerApproval, ProviderAccessAuthorization]:
        if owner_spec.source_key != provider_spec.source_key:
            raise OpenDataRegistryValidationError(
                "Owner and provider decision source keys differ"
            )
        owner = self.ensure_owner_decision(owner_spec)
        provider = self.ensure_provider_decision(owner, provider_spec)
        return owner, provider

    def record_download(
        self,
        source_version: HistoricalSourceVersion,
        provider_decision: ProviderAccessAuthorization,
        artifact: DownloadArtifact,
        spec: OpenDataDownloadSpec,
    ) -> OpenDataDownloadManifest:
        source = self._require_source_version(source_version)
        provider = self._require_provider(provider_decision)
        if spec.source_key != source.source_key or artifact.source_key != source.source_key:
            raise OpenDataRegistryValidationError(
                "Download source_key does not exactly match the source release"
            )
        if spec.source_release != source.dataset_version:
            raise OpenDataRegistryValidationError(
                "Download source_release does not match dataset_version"
            )
        if provider.source_key != source.source_key:
            raise OpenDataRegistryValidationError(
                "Download provider decision belongs to another source"
            )
        owner = self.session.get(SourceOwnerApproval, provider.owner_approval_id)
        if owner is None or owner.source_key != source.source_key:
            raise OpenDataRegistryValidationError(
                "Download provider decision lacks an exact source-specific owner link"
            )
        if not owner.approved or owner.decision != "APPROVED":
            raise OpenDataRegistryValidationError(
                "A download artifact requires an approved source-specific owner decision"
            )
        if not provider.authorized or provider.decision != "AUTHORIZED":
            raise OpenDataRegistryValidationError(
                "A download artifact requires an authorized provider decision"
            )
        if artifact.authorization_identity_checksum != provider.identity_checksum:
            raise OpenDataRegistryValidationError(
                "Download artifact authorization identity does not match the decision"
            )
        _validate_download_artifact(artifact)
        download_started_at = _parse_aware_datetime(artifact.started_at, "started_at")
        downloaded_at = _parse_aware_datetime(artifact.completed_at, "completed_at")
        source_effective = _stored_utc(source.effective_from, "source.effective_from")
        if download_started_at < source_effective:
            raise OpenDataRegistryValidationError(
                "Download predates the source release's effective window"
            )
        if source.effective_to is not None and downloaded_at > _stored_utc(
            source.effective_to, "source.effective_to"
        ):
            raise OpenDataRegistryValidationError(
                "Download occurred after the source release's effective window"
            )
        owner_decided = _stored_utc(owner.decided_at, "owner.decided_at")
        provider_decided = _stored_utc(provider.decided_at, "provider.decided_at")
        if download_started_at < owner_decided or download_started_at < provider_decided:
            raise OpenDataRegistryValidationError(
                "Download predates its owner/provider authorization"
            )
        if owner.expires_at is not None and downloaded_at >= _stored_utc(
            owner.expires_at, "owner.expires_at"
        ):
            raise OpenDataRegistryValidationError(
                "Download occurred after the owner decision expired"
            )
        if provider.expires_at is not None and downloaded_at >= _stored_utc(
            provider.expires_at, "provider.expires_at"
        ):
            raise OpenDataRegistryValidationError(
                "Download occurred after the provider decision expired"
            )
        license_identity, attribution_identity = _source_rights(source)
        semantic = build_download_semantic_fingerprint(
            source_version_identity_checksum=source.identity_checksum,
            spec=spec,
            artifact=artifact,
        )
        attempts, retry_count = _validated_attempts(artifact)
        archive_sha = (
            artifact.sha256 if artifact.archive_format is not ArchiveFormat.NONE else None
        )
        identity = build_download_manifest_identity(
            source_version_identity_checksum=source.identity_checksum,
            spec=spec,
            artifact=artifact,
            license_identity=license_identity,
            attribution_identity=attribution_identity,
        )
        fields = {
            "source_version_id": source.id,
            "source_key": source.source_key,
            "object_key": artifact.object_key,
            "source_release": spec.source_release,
            "upstream_revision": artifact.revision,
            "official_download_uri": artifact.sanitized_url,
            "downloaded_at": downloaded_at,
            "http_status": artifact.http_status,
            "content_length": artifact.content_length,
            "file_name": spec.file_name,
            "mime_type": artifact.mime_type,
            "payload_sha256": artifact.sha256,
            "archive_sha256": archive_sha,
            "license_identity": license_identity,
            "attribution_identity": attribution_identity,
            "physical_attempt_count": len(attempts),
            "retry_count": retry_count,
            "raw_logical_uri": artifact.content_logical_uri,
            "semantic_fingerprint": semantic,
            "identity_checksum": identity,
        }
        natural = self.session.scalar(
            select(OpenDataDownloadManifest).where(
                OpenDataDownloadManifest.source_version_id == source.id,
                OpenDataDownloadManifest.object_key == artifact.object_key,
                OpenDataDownloadManifest.upstream_revision == artifact.revision,
            )
        )
        if natural is not None:
            if artifact.disposition is DownloadDisposition.UNCHANGED_BYTES:
                # The acquisition layer writes a separate physical observation
                # manifest for a re-fetch. The SQL row is intentionally one row
                # per immutable upstream revision, so only revision-stable
                # evidence must match here; first-download timing/accounting
                # remains frozen on the existing row.
                stable_fields = {
                    key: value
                    for key, value in fields.items()
                    if key
                    not in {
                        "downloaded_at",
                        "physical_attempt_count",
                        "retry_count",
                        "identity_checksum",
                    }
                }
                _assert_existing_matches(
                    natural, stable_fields, "download manifest revision"
                )
            else:
                _assert_existing_matches(natural, fields, "download manifest")
            return natural
        collision = self.session.scalar(
            select(OpenDataDownloadManifest).where(
                OpenDataDownloadManifest.identity_checksum == identity
            )
        )
        if collision is not None:
            raise OpenDataRegistryConflictError(
                "Download identity is already bound to another natural key"
            )
        row = OpenDataDownloadManifest(**fields)
        self.session.add(row)
        self.session.flush()
        return row

    def record_identity_mapping(
        self,
        source_version: HistoricalSourceVersion,
        spec: OpenDataIdentityMappingSpec,
    ) -> OpenDataIdentityMapping:
        source = self._require_source_version(source_version)
        if spec.source_key != source.source_key:
            raise OpenDataRegistryValidationError(
                "Identity mapping source_key does not match the source release"
            )
        _source_rights(source)
        identity = build_identity_mapping_identity(
            source_version_identity_checksum=source.identity_checksum,
            spec=spec,
        )
        fields = {
            "source_version_id": source.id,
            **_identity_mapping_fields(spec),
            "identity_checksum": identity,
        }
        natural = self.session.scalar(
            select(OpenDataIdentityMapping).where(
                OpenDataIdentityMapping.source_version_id == source.id,
                OpenDataIdentityMapping.entity_type == spec.entity_type,
                OpenDataIdentityMapping.source_namespace == spec.source_namespace,
                OpenDataIdentityMapping.external_id == spec.external_id,
                OpenDataIdentityMapping.source_row_number == spec.source_row_number,
            )
        )
        if natural is not None:
            _assert_existing_matches(natural, fields, "identity mapping")
            return natural
        collision = self.session.scalar(
            select(OpenDataIdentityMapping).where(
                OpenDataIdentityMapping.identity_checksum == identity
            )
        )
        if collision is not None:
            raise OpenDataRegistryConflictError(
                "Identity-mapping identity is already bound to another natural key"
            )
        row = OpenDataIdentityMapping(**fields)
        self.session.add(row)
        self.session.flush()
        return row

    def _require_source_version(
        self, source: HistoricalSourceVersion
    ) -> HistoricalSourceVersion:
        if source.id is None:
            raise OpenDataRegistryValidationError("Source release is not persisted")
        persisted = self.session.get(HistoricalSourceVersion, source.id)
        if persisted is None:
            raise OpenDataRegistryValidationError(
                "Source release does not belong to this registry session"
            )
        _validate_source_key(persisted.source_key)
        _validate_sha256(persisted.identity_checksum, "source identity_checksum")
        if (
            persisted.source_mode != "LIVE"
            or persisted.temporal_semantics != RETROSPECTIVE_RECONSTRUCTION
        ):
            raise OpenDataRegistryValidationError(
                "Open-data source release has invalid source/timing semantics"
            )
        _source_rights(persisted)
        return persisted

    def _require_owner(self, owner: SourceOwnerApproval) -> SourceOwnerApproval:
        if owner.id is None:
            raise OpenDataRegistryValidationError("Owner decision is not persisted")
        persisted = self.session.get(SourceOwnerApproval, owner.id)
        if persisted is None:
            raise OpenDataRegistryValidationError(
                "Owner decision does not belong to this registry session"
            )
        _validate_source_key(persisted.source_key)
        _validate_sha256(persisted.identity_checksum, "owner identity_checksum")
        return persisted

    def _require_provider(
        self, provider: ProviderAccessAuthorization
    ) -> ProviderAccessAuthorization:
        if provider.id is None:
            raise OpenDataRegistryValidationError("Provider decision is not persisted")
        persisted = self.session.get(ProviderAccessAuthorization, provider.id)
        if persisted is None:
            raise OpenDataRegistryValidationError(
                "Provider decision does not belong to this registry session"
            )
        _validate_source_key(persisted.source_key)
        _validate_sha256(persisted.identity_checksum, "provider identity_checksum")
        return persisted


def _source_release_fields(spec: OpenDataSourceReleaseSpec) -> dict[str, Any]:
    rights = {
        "rights_classification": spec.rights_classification,
        "license_identity": spec.license_identity,
        "license_uri": spec.license_uri,
        "attribution_text": spec.attribution_text,
        "attribution_identity": spec.attribution_identity,
    }
    governance = {
        **_json_copy(spec.governance, "governance"),
        "open_data_registry_schema_version": OPEN_DATA_REGISTRY_SCHEMA_VERSION,
        "open_data_rights": rights,
    }
    return {
        "source_key": spec.source_key,
        "dataset_version": spec.dataset_version,
        "provider_name": spec.provider_name,
        "source_mode": "LIVE",
        "source_classification": spec.source_classification,
        "temporal_semantics": RETROSPECTIVE_RECONSTRUCTION,
        "quality_tier": spec.quality_tier,
        "training_eligible": spec.training_eligible,
        "evaluation_eligible": spec.evaluation_eligible,
        "availability_policy": _json_copy(
            spec.availability_policy, "availability_policy"
        ),
        "policy_version": spec.policy_version,
        "governance": governance,
        "documentation_uri": spec.documentation_uri,
        "effective_from": _aware_utc(spec.effective_from, "effective_from"),
        "effective_to": None,
        "supersedes_id": None,
    }


def _owner_fields(spec: OpenDataOwnerDecisionSpec) -> dict[str, Any]:
    return {
        "source_key": spec.source_key,
        "approval_scope": LOCAL_PRIVATE_RESEARCH,
        "decision": spec.decision,
        "approved": spec.approved,
        "decided_by": spec.decided_by,
        "decided_at": _aware_utc(spec.decided_at, "decided_at"),
        "expires_at": (
            _aware_utc(spec.expires_at, "expires_at")
            if spec.expires_at is not None
            else None
        ),
        "authorization_reference": spec.authorization_reference,
        "conditions": _json_copy(spec.conditions, "conditions") or None,
    }


def _provider_fields(spec: OpenDataProviderDecisionSpec) -> dict[str, Any]:
    return {
        "source_key": spec.source_key,
        "provider_name": spec.provider_name,
        "access_method": spec.access_method,
        "decision": spec.decision,
        "authorized": spec.authorized,
        "decided_by": spec.decided_by,
        "decided_at": _aware_utc(spec.decided_at, "decided_at"),
        "expires_at": (
            _aware_utc(spec.expires_at, "expires_at")
            if spec.expires_at is not None
            else None
        ),
        "terms_reference": spec.terms_reference,
        "terms_checksum": spec.terms_checksum,
        "decision_basis": spec.decision_basis,
    }


def _identity_mapping_fields(spec: OpenDataIdentityMappingSpec) -> dict[str, Any]:
    return {
        "entity_type": spec.entity_type,
        "source_namespace": spec.source_namespace,
        "external_id": spec.external_id,
        "canonical_namespace": spec.canonical_namespace,
        "canonical_id": spec.canonical_id,
        "source_row_number": spec.source_row_number,
        "source_row_checksum": spec.source_row_checksum,
        "confidence": spec.confidence,
        "valid_from": spec.valid_from,
        "valid_through": spec.valid_through,
        "evidence_logical_uri": spec.evidence_logical_uri,
    }


def _source_rights(source: HistoricalSourceVersion) -> tuple[str, str]:
    governance = source.governance
    if not isinstance(governance, Mapping):
        raise OpenDataRegistryValidationError(
            "Source release lacks open-data governance"
        )
    rights = governance.get("open_data_rights")
    if not isinstance(rights, Mapping):
        raise OpenDataRegistryValidationError("Source release lacks open-data rights")
    rights_classification = _required_text(
        rights.get("rights_classification"),
        "rights_classification",
        maximum=160,
    )
    license_identity = _required_text(
        rights.get("license_identity"), "license_identity", maximum=160
    )
    license_uri = _required_text(rights.get("license_uri"), "license_uri", maximum=1000)
    _validate_https_uri(license_uri, "license_uri")
    attribution_text = _required_text(
        rights.get("attribution_text"),
        "attribution_text",
        maximum=8_000,
        allow_multiline=True,
    )
    attribution_identity = _required_text(
        rights.get("attribution_identity"), "attribution_identity", maximum=64
    )
    _validate_sha256(attribution_identity, "attribution_identity")
    expected = build_attribution_identity(
        source_key=source.source_key,
        license_identity=license_identity,
        attribution_text=attribution_text,
    )
    if attribution_identity != expected:
        raise OpenDataRegistryValidationError(
            "Source attribution identity does not match its text/license/source"
        )
    if not rights_classification:
        raise OpenDataRegistryValidationError("rights_classification is required")
    return license_identity, attribution_identity


def _validated_attempts(artifact: DownloadArtifact) -> tuple[Sequence[Any], int]:
    attempts = artifact.attempts
    if artifact.physical_attempt_count != len(attempts) or not attempts:
        raise OpenDataRegistryValidationError("Download attempt count is inconsistent")
    if tuple(item.attempt_number for item in attempts) != tuple(
        range(1, len(attempts) + 1)
    ):
        raise OpenDataRegistryValidationError(
            "Download attempt numbers must be contiguous"
        )
    for index, attempt in enumerate(attempts):
        started = _parse_aware_datetime(attempt.started_at, "attempt.started_at")
        completed = _parse_aware_datetime(attempt.completed_at, "attempt.completed_at")
        if completed < started:
            raise OpenDataRegistryValidationError(
                "Download attempt completed before it started"
            )
        expected_outcome = "SUCCEEDED" if index == len(attempts) - 1 else "INTERRUPTED"
        if attempt.outcome != expected_outcome:
            raise OpenDataRegistryValidationError(
                "Only the final download attempt may succeed"
            )
    if attempts[-1].status_code != artifact.http_status:
        raise OpenDataRegistryValidationError(
            "Final attempt status does not match the download artifact"
        )
    artifact_started = _parse_aware_datetime(artifact.started_at, "started_at")
    artifact_completed = _parse_aware_datetime(artifact.completed_at, "completed_at")
    if artifact_started != _parse_aware_datetime(attempts[0].started_at, "started_at"):
        raise OpenDataRegistryValidationError(
            "Artifact start does not match first attempt"
        )
    if artifact_completed != _parse_aware_datetime(
        attempts[-1].completed_at, "completed_at"
    ):
        raise OpenDataRegistryValidationError(
            "Artifact completion does not match final attempt"
        )
    return attempts, len(attempts) - 1


def _validate_download_artifact(artifact: DownloadArtifact) -> None:
    if not isinstance(artifact, DownloadArtifact):
        raise OpenDataRegistryValidationError(
            "record_download requires a validated DownloadArtifact"
        )
    _validate_source_key(artifact.source_key)
    _required_text(artifact.object_key, "object_key", maximum=120)
    _validate_https_uri(artifact.sanitized_url, "official_download_uri")
    _validate_sha256(artifact.policy_identity_checksum, "policy_identity_checksum")
    _validate_sha256(
        artifact.authorization_identity_checksum,
        "authorization_identity_checksum",
    )
    _validate_sha256(artifact.sha256, "payload_sha256")
    _validate_logical_uri(artifact.content_logical_uri, "content_logical_uri")
    _validate_logical_uri(artifact.manifest_logical_uri, "manifest_logical_uri")
    if artifact.revision < 1:
        raise OpenDataRegistryValidationError("Download revision must be positive")
    if artifact.content_length <= 0:
        raise OpenDataRegistryValidationError("Downloaded content must be nonempty")
    if not 200 <= artifact.http_status <= 299:
        raise OpenDataRegistryValidationError("Download HTTP status must be successful")
    mime = _required_text(artifact.mime_type, "mime_type", maximum=160)
    if mime != mime.strip().lower() or ";" in mime:
        raise OpenDataRegistryValidationError("mime_type must be normalized")
    if not isinstance(artifact.disposition, DownloadDisposition):
        raise OpenDataRegistryValidationError("Download disposition is invalid")
    _validated_attempts(artifact)


def _assert_existing_matches(row: Any, expected: Mapping[str, Any], label: str) -> None:
    mismatches: list[str] = []
    for field_name, expected_value in expected.items():
        actual_value = getattr(row, field_name)
        if _comparison_value(actual_value) != _comparison_value(expected_value):
            mismatches.append(field_name)
    if mismatches:
        joined = ", ".join(sorted(mismatches))
        raise OpenDataRegistryConflictError(
            f"Existing {label} differs in immutable fields: {joined}"
        )


def _comparison_value(value: Any) -> Any:
    # SQLite drops timezone metadata for ``DateTime(timezone=True)``. Registry
    # writes are normalized to UTC, so a naive value read back from SQLite is
    # the UTC database representation, not permission to accept naive inputs.
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return _canonical_value(value, "comparison")


def _record_identity(record_type: str, fields: Mapping[str, Any]) -> str:
    return canonical_sha256(
        {
            "schema_version": OPEN_DATA_REGISTRY_SCHEMA_VERSION,
            "record_type": record_type,
            "fields": fields,
        }
    )


def _canonical_value(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise OpenDataRegistryValidationError(f"{path} contains a nonfinite number")
        return value
    if isinstance(value, datetime):
        return _iso_utc(_aware_utc(value, path))
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _canonical_value(value.value, path)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise OpenDataRegistryValidationError(
                    f"{path} contains a non-string mapping key"
                )
            normalized[key] = _canonical_value(item, f"{path}.{key}")
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise OpenDataRegistryValidationError(
        f"{path} contains unsupported type {type(value).__name__}"
    )


def _json_copy(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    normalized = _canonical_value(value, field_name)
    if not isinstance(normalized, dict):  # pragma: no cover - Mapping guarantees this
        raise OpenDataRegistryValidationError(f"{field_name} must be a mapping")
    return normalized


def _validate_json_mapping(
    value: Mapping[str, Any], field_name: str, *, nonempty: bool = False
) -> None:
    if not isinstance(value, Mapping):
        raise OpenDataRegistryValidationError(f"{field_name} must be a mapping")
    if nonempty and not value:
        raise OpenDataRegistryValidationError(f"{field_name} cannot be empty")
    _canonical_value(value, field_name)


def _validate_source_key(value: str) -> None:
    if not isinstance(value, str) or not _SOURCE_KEY.fullmatch(value):
        raise OpenDataRegistryValidationError(
            "source_key must be an uppercase governed identifier"
        )
    if value in _GENERIC_SOURCE_KEYS:
        raise OpenDataRegistryValidationError(
            "A source-specific key is required; generic open/baseball approval is forbidden"
        )


def _validate_namespace(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _NAMESPACE.fullmatch(value):
        raise OpenDataRegistryValidationError(f"{field_name} is invalid")


def _required_text(
    value: Any,
    field_name: str,
    *,
    maximum: int,
    allow_multiline: bool = False,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OpenDataRegistryValidationError(f"{field_name} is required")
    if value != value.strip() or len(value) > maximum:
        raise OpenDataRegistryValidationError(
            f"{field_name} must be trimmed and at most {maximum} characters"
        )
    allowed_controls = {9, 10, 13} if allow_multiline else set()
    if any(
        ord(character) < 32 and ord(character) not in allowed_controls
        for character in value
    ):
        raise OpenDataRegistryValidationError(
            f"{field_name} cannot contain control characters"
        )
    return value


def _validate_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise OpenDataRegistryValidationError(
            f"{field_name} must be a lowercase SHA-256 digest"
        )


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OpenDataRegistryValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime, field_name: str) -> datetime:
    """Normalize a persisted timestamp, including SQLite's naive UTC reads."""

    if not isinstance(value, datetime):
        raise OpenDataRegistryValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_aware_datetime(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise OpenDataRegistryValidationError(f"{field_name} is required")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise OpenDataRegistryValidationError(
            f"{field_name} must be ISO-8601"
        ) from exc
    return _aware_utc(parsed, field_name)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _validate_https_uri(value: str, field_name: str) -> None:
    _required_text(value, field_name, maximum=1000)
    if "\\" in value:
        raise OpenDataRegistryValidationError(f"{field_name} cannot contain backslashes")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise OpenDataRegistryValidationError(
            f"{field_name} must be a credential-free HTTPS URI"
        )
    secret_values = [
        key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    if any(marker in item for item in secret_values for marker in _SECRET_MARKERS):
        raise OpenDataRegistryValidationError(
            f"{field_name} contains a secret-like marker"
        )


def _validate_logical_uri(value: str, field_name: str) -> None:
    _required_text(value, field_name, maximum=1000)
    lowered = value.lower()
    pure = PurePosixPath(value)
    if (
        value.startswith("/")
        or "\\" in value
        or "://" in value
        or "?" in value
        or "#" in value
        or ".." in value
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(marker in lowered for marker in _SECRET_MARKERS)
    ):
        raise OpenDataRegistryValidationError(
            f"{field_name} must be a secret-free relative logical URI"
        )


def _validate_file_name(value: str) -> None:
    _required_text(value, "file_name", maximum=255)
    if (
        value in {".", ".."}
        or "/" in value
        or "\\" in value
        or any(marker in value.lower() for marker in _SECRET_MARKERS)
    ):
        raise OpenDataRegistryValidationError("file_name is unsafe")


__all__ = [
    "OPEN_DATA_REGISTRY_SCHEMA_VERSION",
    "OpenDataDownloadSpec",
    "OpenDataIdentityMappingSpec",
    "OpenDataOwnerDecisionSpec",
    "OpenDataProviderDecisionSpec",
    "OpenDataRegistry",
    "OpenDataRegistryConflictError",
    "OpenDataRegistryError",
    "OpenDataRegistryValidationError",
    "OpenDataSourceReleaseSpec",
    "build_attribution_identity",
    "build_download_manifest_identity",
    "build_download_semantic_fingerprint",
    "build_identity_mapping_identity",
    "build_owner_decision_identity",
    "build_provider_decision_identity",
    "build_source_release_identity",
    "canonical_sha256",
]
