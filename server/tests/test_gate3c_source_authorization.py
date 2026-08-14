from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from app.gen2.source_authorization import (
    ExistingEvidenceDerivedUseDecision,
    LOCAL_PRIVATE_RESEARCH,
    OwnerApproval,
    ProviderAccessAuthorization,
    SourceAuthorizationError,
    adapter_construction_authorized,
    local_processing_authorized,
    require_adapter_construction,
    require_local_processing,
)


NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
MANIFEST = "2" * 64


def _owner(**overrides) -> OwnerApproval:
    values = {
        "source_key": "baseball-savant-statcast",
        "approved": True,
        "approval_scope": LOCAL_PRIVATE_RESEARCH,
        "decided_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=30),
    }
    values.update(overrides)
    return OwnerApproval(**values)


def _provider(**overrides) -> ProviderAccessAuthorization:
    values = {
        "source_key": "baseball-savant-statcast",
        "provider_name": "Baseball Savant / Statcast",
        "authorized": True,
        "access_method": "OFFICIAL_PUBLIC_EXPORT",
        "decided_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(days=7),
        "owner_approval_identity_checksum": _owner().identity_checksum,
    }
    values.update(overrides)
    return ProviderAccessAuthorization(**values)


def _derived(**overrides) -> ExistingEvidenceDerivedUseDecision:
    values = {
        "source_key": "baseball-savant-statcast",
        "evidence_manifest_checksum": MANIFEST,
        "allowed": True,
        "decided_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(days=7),
        "owner_approval_identity_checksum": _owner().identity_checksum,
    }
    values.update(overrides)
    return ExistingEvidenceDerivedUseDecision(**values)


def test_provider_access_and_existing_evidence_default_to_denied() -> None:
    owner = OwnerApproval(source_key="statcast")
    provider = ProviderAccessAuthorization(
        source_key="statcast",
        provider_name="Statcast",
    )
    derived = ExistingEvidenceDerivedUseDecision(source_key="statcast")

    assert owner.approved is False
    assert provider.authorized is False
    assert derived.allowed is False
    assert not adapter_construction_authorized(owner, provider, as_of=NOW)
    assert not local_processing_authorized(
        derived,
        owner_approval=owner,
        as_of=NOW,
        expected_source_key="statcast",
        expected_manifest_checksum=MANIFEST,
    )

    with pytest.raises(SourceAuthorizationError, match="owner"):
        require_adapter_construction(owner, provider, as_of=NOW)
    with pytest.raises(SourceAuthorizationError, match="owner"):
        require_local_processing(
            derived,
            owner_approval=owner,
            as_of=NOW,
            expected_source_key="statcast",
            expected_manifest_checksum=MANIFEST,
        )


def test_adapter_construction_requires_matching_active_owner_and_provider_decisions() -> None:
    grant = require_adapter_construction(_owner(), _provider(), as_of=NOW)
    assert grant.operation == "ADAPTER_CONSTRUCTION"
    assert grant.scope == LOCAL_PRIVATE_RESEARCH
    assert len(grant.identity_checksum) == 64

    with pytest.raises(SourceAuthorizationError, match="source identities differ"):
        require_adapter_construction(
            _owner(),
            _provider(source_key="different-source"),
            as_of=NOW,
        )
    with pytest.raises(SourceAuthorizationError, match="does not reference"):
        require_adapter_construction(
            _owner(),
            _provider(owner_approval_identity_checksum="9" * 64),
            as_of=NOW,
        )
    with pytest.raises(SourceAuthorizationError, match="expired"):
        require_adapter_construction(
            _owner(),
            _provider(
                decided_at=NOW - timedelta(days=2),
                expires_at=NOW - timedelta(seconds=1),
            ),
            as_of=NOW,
        )


def test_provider_denial_does_not_block_separately_allowed_local_derivation() -> None:
    denied_provider = ProviderAccessAuthorization(
        source_key="baseball-savant-statcast",
        provider_name="Baseball Savant / Statcast",
    )
    assert not adapter_construction_authorized(_owner(), denied_provider, as_of=NOW)

    grant = require_local_processing(
        _derived(),
        owner_approval=_owner(),
        as_of=NOW,
        expected_source_key="baseball-savant-statcast",
        expected_manifest_checksum=MANIFEST,
    )
    assert grant.operation == "LOCAL_DERIVED_PROCESSING"
    assert grant.source_key == "baseball-savant-statcast"
    assert grant.evidence_manifest_checksum == MANIFEST

    with pytest.raises(SourceAuthorizationError, match="manifest does not match"):
        require_local_processing(
            _derived(),
            owner_approval=_owner(),
            as_of=NOW,
            expected_source_key="baseball-savant-statcast",
            expected_manifest_checksum="8" * 64,
        )


def test_authorization_value_objects_are_immutable() -> None:
    owner = _owner()
    with pytest.raises(FrozenInstanceError):
        owner.approved = False  # type: ignore[misc]


def test_positive_decisions_require_timestamp_identity_and_owner_link() -> None:
    with pytest.raises(ValueError, match="decided_at"):
        OwnerApproval(source_key="statcast", approved=True)
    with pytest.raises(ValueError, match="owner link"):
        ProviderAccessAuthorization(
            source_key="statcast",
            provider_name="Statcast",
            authorized=True,
            decided_at=NOW,
            identity_checksum="5" * 64,
        )
    with pytest.raises(SourceAuthorizationError, match="owner approval evidence is absent"):
        require_local_processing(
            _derived(),
            as_of=NOW,
            expected_source_key="baseball-savant-statcast",
            expected_manifest_checksum=MANIFEST,
        )


def test_decision_identity_is_field_bound_and_exact_manifest_is_mandatory() -> None:
    with pytest.raises(ValueError, match="identity checksum does not match"):
        OwnerApproval(
            source_key="statcast",
            approved=True,
            decided_at=NOW,
            identity_checksum="f" * 64,
        )
    with pytest.raises(SourceAuthorizationError, match="exact source and evidence manifest"):
        require_local_processing(_derived(), owner_approval=_owner(), as_of=NOW)

    grant = require_local_processing(
        _derived(),
        owner_approval=_owner(),
        as_of=NOW,
        expected_source_key="baseball-savant-statcast",
        expected_manifest_checksum=MANIFEST,
    )
    with pytest.raises(SourceAuthorizationError, match="do not match"):
        replace(grant, source_key="retargeted-source").verify()

    string_owner = OwnerApproval(
        source_key="baseball-savant-statcast",
        approved="false",  # type: ignore[arg-type]
        decided_at=NOW - timedelta(days=1),
    )
    with pytest.raises(SourceAuthorizationError, match="project owner"):
        require_adapter_construction(string_owner, _provider(), as_of=NOW)
