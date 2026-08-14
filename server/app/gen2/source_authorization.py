"""Fail-closed authorization gates for Gate 3C historical evidence.

The three decisions are deliberately independent:

* project-owner approval defines the permitted project scope;
* provider access authorization governs construction of code that can make a
  new provider request and defaults to denied;
* an existing-evidence decision governs provider-free local derivation from an
  identified archive or manifest.

This module performs no I/O and cannot make provider calls.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


LOCAL_PRIVATE_RESEARCH = "LOCAL_PRIVATE_RESEARCH"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GRANT_SENTINEL = object()


class SourceAuthorizationError(PermissionError):
    """Raised when a governed source operation is not explicitly authorized."""


@dataclass(frozen=True, slots=True)
class OwnerApproval:
    """Immutable in-memory projection of a project-owner approval record."""

    source_key: str
    approved: bool = False
    approval_scope: str = LOCAL_PRIVATE_RESEARCH
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    identity_checksum: str | None = None

    def __post_init__(self) -> None:
        _require_source(self.source_key)
        if self.approval_scope != LOCAL_PRIVATE_RESEARCH:
            raise ValueError("Owner approval scope must be LOCAL_PRIVATE_RESEARCH")
        _validate_window(self.decided_at, self.expires_at, "owner approval")
        _validate_optional_checksum(self.identity_checksum, "owner approval identity")
        if self.approved and self.decided_at is None:
            raise ValueError("Approved owner evidence requires decided_at")
        _bind_identity(
            self,
            {
                "record_type": "OWNER_APPROVAL",
                "source_key": self.source_key,
                "approved": self.approved,
                "approval_scope": self.approval_scope,
                "decided_at": self.decided_at,
                "expires_at": self.expires_at,
            },
            "owner approval",
        )


@dataclass(frozen=True, slots=True)
class ProviderAccessAuthorization:
    """Immutable provider-access decision; omission is an explicit denial."""

    source_key: str
    provider_name: str
    authorized: bool = False
    access_method: str = "UNSPECIFIED"
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    owner_approval_identity_checksum: str | None = None
    identity_checksum: str | None = None

    def __post_init__(self) -> None:
        _require_source(self.source_key)
        if not self.provider_name.strip():
            raise ValueError("Provider name is required")
        if not self.access_method.strip():
            raise ValueError("Provider access method is required")
        _validate_window(self.decided_at, self.expires_at, "provider authorization")
        _validate_optional_checksum(
            self.owner_approval_identity_checksum,
            "provider owner-approval identity",
        )
        _validate_optional_checksum(self.identity_checksum, "provider authorization identity")
        if self.authorized and (
            self.decided_at is None or self.owner_approval_identity_checksum is None
        ):
            raise ValueError(
                "Authorized provider evidence requires decided_at and owner link"
            )
        if self.authorized and self.access_method == "UNSPECIFIED":
            raise ValueError("Authorized provider access requires an explicit access method")
        _bind_identity(
            self,
            {
                "record_type": "PROVIDER_ACCESS_AUTHORIZATION",
                "source_key": self.source_key,
                "provider_name": self.provider_name,
                "authorized": self.authorized,
                "access_method": self.access_method,
                "decided_at": self.decided_at,
                "expires_at": self.expires_at,
                "owner_approval_identity_checksum": self.owner_approval_identity_checksum,
            },
            "provider authorization",
        )


@dataclass(frozen=True, slots=True)
class ExistingEvidenceDerivedUseDecision:
    """Immutable decision for local use of an exact existing-evidence manifest."""

    source_key: str
    evidence_manifest_checksum: str = ""
    allowed: bool = False
    use_scope: str = LOCAL_PRIVATE_RESEARCH
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    owner_approval_identity_checksum: str | None = None
    identity_checksum: str | None = None

    def __post_init__(self) -> None:
        _require_source(self.source_key)
        if self.use_scope != LOCAL_PRIVATE_RESEARCH:
            raise ValueError("Derived-use scope must be LOCAL_PRIVATE_RESEARCH")
        if self.evidence_manifest_checksum:
            _require_checksum(
                self.evidence_manifest_checksum,
                "existing-evidence manifest checksum",
            )
        elif self.allowed:
            raise ValueError("Allowed derived use requires an evidence manifest checksum")
        _validate_window(self.decided_at, self.expires_at, "derived-use decision")
        _validate_optional_checksum(
            self.owner_approval_identity_checksum,
            "derived-use owner-approval identity",
        )
        _validate_optional_checksum(self.identity_checksum, "derived-use identity")
        if self.allowed and (
            self.decided_at is None or self.owner_approval_identity_checksum is None
        ):
            raise ValueError(
                "Allowed derived use requires decided_at and owner link"
            )
        _bind_identity(
            self,
            {
                "record_type": "EXISTING_EVIDENCE_DERIVED_USE",
                "source_key": self.source_key,
                "evidence_manifest_checksum": self.evidence_manifest_checksum,
                "allowed": self.allowed,
                "use_scope": self.use_scope,
                "decided_at": self.decided_at,
                "expires_at": self.expires_at,
                "owner_approval_identity_checksum": self.owner_approval_identity_checksum,
            },
            "derived-use decision",
        )


@dataclass(frozen=True, slots=True)
class AuthorizationGrant:
    """Deterministic proof returned by a successful fail-closed gate."""

    operation: str
    source_key: str
    scope: str
    evidence_manifest_checksum: str | None
    evidence_bundle_checksum: str
    identity_checksum: str
    _proof: object = field(repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if self._proof is not _GRANT_SENTINEL:
            raise ValueError("Authorization grants can only be issued by a validated gate")

    def verify(self) -> None:
        if self._proof is not _GRANT_SENTINEL:
            raise SourceAuthorizationError("Authorization grant proof is invalid")
        _require_checksum(self.identity_checksum, "authorization grant identity")
        _require_checksum(self.evidence_bundle_checksum, "authorization evidence bundle")
        expected = _authorization_grant_identity(
            operation=self.operation,
            source_key=self.source_key,
            scope=self.scope,
            evidence_manifest_checksum=self.evidence_manifest_checksum,
            evidence_bundle_checksum=self.evidence_bundle_checksum,
        )
        if self.identity_checksum != expected:
            raise SourceAuthorizationError("Authorization grant fields do not match its identity")


def require_adapter_construction(
    owner_approval: OwnerApproval | object | None,
    provider_authorization: ProviderAccessAuthorization | object | None,
    *,
    as_of: datetime | None = None,
) -> AuthorizationGrant:
    """Require both owner approval and provider access before adapter creation."""

    now = _as_of(as_of)
    if not isinstance(owner_approval, OwnerApproval) or not isinstance(
        provider_authorization, ProviderAccessAuthorization
    ):
        raise SourceAuthorizationError(
            "Adapter construction requires validated immutable authorization records"
        )
    if owner_approval is None:
        raise SourceAuthorizationError("Adapter construction denied: owner approval is absent")
    if provider_authorization is None:
        raise SourceAuthorizationError(
            "Adapter construction denied: provider access authorization is absent"
        )
    owner_source = _required_attr(owner_approval, "source_key")
    provider_source = _required_attr(provider_authorization, "source_key")
    if owner_source != provider_source:
        raise SourceAuthorizationError(
            "Adapter construction denied: authorization source identities differ"
        )
    if _optional_attr(owner_approval, "approved", default=False) is not True:
        raise SourceAuthorizationError("Adapter construction denied by project owner")
    owner_scope = str(
        _optional_attr(
            owner_approval,
            "approval_scope",
            "scope",
            default="",
        )
    )
    if owner_scope != LOCAL_PRIVATE_RESEARCH:
        raise SourceAuthorizationError("Adapter construction denied outside approved scope")
    _require_active(owner_approval, now, "owner approval")
    _require_decision_identity(owner_approval, "owner approval")
    if _optional_attr(provider_authorization, "authorized", default=False) is not True:
        raise SourceAuthorizationError("Adapter construction denied by provider-access policy")
    _require_active(provider_authorization, now, "provider authorization")
    _require_decision_identity(provider_authorization, "provider authorization")
    _require_owner_identity_link(owner_approval, provider_authorization)
    return _grant(
        "ADAPTER_CONSTRUCTION",
        owner_source,
        owner_scope,
        owner_approval,
        provider_authorization,
    )


def require_local_processing(
    derived_use_decision: ExistingEvidenceDerivedUseDecision | object | None,
    *,
    owner_approval: OwnerApproval | object | None = None,
    as_of: datetime | None = None,
    expected_source_key: str | None = None,
    expected_manifest_checksum: str | None = None,
) -> AuthorizationGrant:
    """Authorize provider-free local processing only for an exact allowed manifest."""

    now = _as_of(as_of)
    if derived_use_decision is None:
        raise SourceAuthorizationError(
            "Local processing denied: existing-evidence derived-use decision is absent"
        )
    if owner_approval is None:
        raise SourceAuthorizationError(
            "Local processing denied: owner approval evidence is absent"
        )
    if not isinstance(derived_use_decision, ExistingEvidenceDerivedUseDecision) or not isinstance(
        owner_approval, OwnerApproval
    ):
        raise SourceAuthorizationError(
            "Local processing requires validated immutable authorization records"
        )
    source_key = _required_attr(derived_use_decision, "source_key")
    owner_source = _required_attr(owner_approval, "source_key")
    if owner_source != source_key:
        raise SourceAuthorizationError(
            "Local processing denied: owner and derived-use source identities differ"
        )
    if _optional_attr(owner_approval, "approved", default=False) is not True:
        raise SourceAuthorizationError("Local processing denied by project owner")
    owner_scope = str(
        _optional_attr(owner_approval, "approval_scope", "scope", default="")
    )
    if owner_scope != LOCAL_PRIVATE_RESEARCH:
        raise SourceAuthorizationError("Local processing denied outside owner-approved scope")
    _require_active(owner_approval, now, "owner approval")
    _require_decision_identity(owner_approval, "owner approval")
    if _optional_attr(derived_use_decision, "allowed", default=False) is not True:
        raise SourceAuthorizationError("Local processing denied by derived-use policy")
    scope = str(
        _optional_attr(
            derived_use_decision,
            "use_scope",
            "scope",
            default="",
        )
    )
    if scope != LOCAL_PRIVATE_RESEARCH:
        raise SourceAuthorizationError("Local processing denied outside approved scope")
    _require_active(derived_use_decision, now, "derived-use decision")
    _require_decision_identity(derived_use_decision, "derived-use decision")
    _require_owner_identity_link(owner_approval, derived_use_decision)
    manifest = _required_attr(derived_use_decision, "evidence_manifest_checksum")
    _require_checksum(manifest, "existing-evidence manifest checksum")
    if expected_source_key is None or expected_manifest_checksum is None:
        raise SourceAuthorizationError(
            "Local processing requires an exact source and evidence manifest"
        )
    if source_key != expected_source_key:
        raise SourceAuthorizationError("Local processing source does not match the decision")
    _require_checksum(expected_manifest_checksum, "expected manifest checksum")
    if manifest != expected_manifest_checksum:
        raise SourceAuthorizationError(
            "Local processing manifest does not match the derived-use decision"
        )
    return _grant(
        "LOCAL_DERIVED_PROCESSING",
        source_key,
        scope,
        derived_use_decision,
        evidence_manifest_checksum=manifest,
    )


def adapter_construction_authorized(
    owner_approval: OwnerApproval | object | None,
    provider_authorization: ProviderAccessAuthorization | object | None,
    *,
    as_of: datetime | None = None,
) -> bool:
    """Return a fail-closed boolean without weakening the raising gate."""

    try:
        require_adapter_construction(owner_approval, provider_authorization, as_of=as_of)
    except (SourceAuthorizationError, TypeError, ValueError):
        return False
    return True


def local_processing_authorized(
    derived_use_decision: ExistingEvidenceDerivedUseDecision | object | None,
    *,
    owner_approval: OwnerApproval | object | None = None,
    as_of: datetime | None = None,
    expected_source_key: str | None = None,
    expected_manifest_checksum: str | None = None,
) -> bool:
    """Return a fail-closed boolean for exact existing-evidence processing."""

    try:
        require_local_processing(
            derived_use_decision,
            owner_approval=owner_approval,
            as_of=as_of,
            expected_source_key=expected_source_key,
            expected_manifest_checksum=expected_manifest_checksum,
        )
    except (SourceAuthorizationError, TypeError, ValueError):
        return False
    return True


def _require_active(record: object, as_of: datetime, label: str) -> None:
    decided_at = _optional_attr(record, "decided_at", default=None)
    expires_at = _optional_attr(record, "expires_at", default=None)
    if decided_at is not None:
        decided = _aware(decided_at, f"{label} decided_at")
        if decided > as_of:
            raise SourceAuthorizationError(f"{label.capitalize()} is not yet effective")
    if expires_at is not None and _aware(expires_at, f"{label} expires_at") <= as_of:
        raise SourceAuthorizationError(f"{label.capitalize()} has expired")


def _require_owner_identity_link(owner: object, decision: object) -> None:
    expected = _optional_attr(
        decision,
        "owner_approval_identity_checksum",
        default=None,
    )
    if expected is not None:
        actual = _optional_attr(owner, "identity_checksum", default=None)
        if actual is None or str(actual) != str(expected):
            raise SourceAuthorizationError(
                "Provider authorization does not reference the supplied owner approval"
            )
        return
    linked_id = _optional_attr(decision, "owner_approval_id", default=None)
    if linked_id is not None:
        owner_id = _optional_attr(owner, "id", default=None)
        if owner_id is None or str(owner_id) != str(linked_id):
            raise SourceAuthorizationError(
                "Provider authorization does not reference the supplied owner approval"
            )
        return
    raise SourceAuthorizationError(
        "Authorization decision does not contain an immutable owner-approval link"
    )


def _require_decision_identity(record: object, label: str) -> None:
    decided_at = _optional_attr(record, "decided_at", default=None)
    if decided_at is None:
        raise SourceAuthorizationError(f"{label.capitalize()} has no decision timestamp")
    _aware(decided_at, f"{label} decided_at")
    identity = _required_attr(record, "identity_checksum")
    try:
        _require_checksum(identity, f"{label} identity")
    except ValueError as exc:
        raise SourceAuthorizationError(str(exc)) from exc


def _grant(
    operation: str,
    source_key: str,
    scope: str,
    *records: object,
    evidence_manifest_checksum: str | None = None,
) -> AuthorizationGrant:
    evidence = []
    for record in records:
        if hasattr(record, "__dataclass_fields__"):
            evidence.append(asdict(record))
        else:
            evidence.append(
                {
                    "type": type(record).__name__,
                    "identity_checksum": _optional_attr(
                        record, "identity_checksum", default=None
                    ),
                }
            )
    evidence_bundle_checksum = hashlib.sha256(
        json.dumps(
            evidence,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        ).encode("utf-8")
    ).hexdigest()
    identity_checksum = _authorization_grant_identity(
        operation=operation,
        source_key=source_key,
        scope=scope,
        evidence_manifest_checksum=evidence_manifest_checksum,
        evidence_bundle_checksum=evidence_bundle_checksum,
    )
    return AuthorizationGrant(
        operation=operation,
        source_key=source_key,
        scope=scope,
        evidence_manifest_checksum=evidence_manifest_checksum,
        evidence_bundle_checksum=evidence_bundle_checksum,
        identity_checksum=identity_checksum,
        _proof=_GRANT_SENTINEL,
    )


def _authorization_grant_identity(
    *,
    operation: str,
    source_key: str,
    scope: str,
    evidence_manifest_checksum: str | None,
    evidence_bundle_checksum: str,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "operation": operation,
                "source_key": source_key,
                "scope": scope,
                "evidence_manifest_checksum": evidence_manifest_checksum,
                "evidence_bundle_checksum": evidence_bundle_checksum,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _bind_identity(record: object, payload: object, label: str) -> None:
    expected = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        ).encode("utf-8")
    ).hexdigest()
    supplied = getattr(record, "identity_checksum")
    if supplied is None:
        object.__setattr__(record, "identity_checksum", expected)
    elif supplied != expected:
        raise ValueError(f"{label} identity checksum does not match its fields")


def _required_attr(record: object, name: str) -> str:
    value = _optional_attr(record, name, default=None)
    if value is None or not str(value).strip():
        raise SourceAuthorizationError(f"Authorization evidence is missing {name}")
    return str(value)


def _optional_attr(record: object, *names: str, default: Any) -> Any:
    for name in names:
        if hasattr(record, name):
            return getattr(record, name)
    return default


def _as_of(value: datetime | None) -> datetime:
    return datetime.now(UTC) if value is None else _aware(value, "authorization as_of")


def _aware(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_window(
    decided_at: datetime | None,
    expires_at: datetime | None,
    label: str,
) -> None:
    decided = _aware(decided_at, f"{label} decided_at") if decided_at else None
    expires = _aware(expires_at, f"{label} expires_at") if expires_at else None
    if expires is not None and decided is None:
        raise ValueError(f"{label} expiry requires decided_at")
    if decided is not None and expires is not None and expires <= decided:
        raise ValueError(f"{label} expiry must follow decided_at")


def _require_source(value: str) -> None:
    if not value or not value.strip():
        raise ValueError("Source key is required")


def _validate_optional_checksum(value: str | None, label: str) -> None:
    if value is not None:
        _require_checksum(value, label)


def _require_checksum(value: str, label: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return _aware(value, "authorization timestamp").isoformat()
    return str(value)


# Concise aliases used by callers that do not need the longer policy names.
DerivedUseDecision = ExistingEvidenceDerivedUseDecision
OwnerApprovalDecision = OwnerApproval
ProviderAuthorization = ProviderAccessAuthorization
DerivedUseAuthorization = ExistingEvidenceDerivedUseDecision
gate_adapter_construction = require_adapter_construction
gate_local_processing = require_local_processing
require_provider_adapter_authorization = require_adapter_construction
require_existing_evidence_use = require_local_processing


__all__ = [
    "AuthorizationGrant",
    "DerivedUseDecision",
    "DerivedUseAuthorization",
    "ExistingEvidenceDerivedUseDecision",
    "LOCAL_PRIVATE_RESEARCH",
    "OwnerApproval",
    "OwnerApprovalDecision",
    "ProviderAccessAuthorization",
    "ProviderAuthorization",
    "SourceAuthorizationError",
    "adapter_construction_authorized",
    "gate_adapter_construction",
    "gate_local_processing",
    "local_processing_authorized",
    "require_adapter_construction",
    "require_existing_evidence_use",
    "require_local_processing",
    "require_provider_adapter_authorization",
]
