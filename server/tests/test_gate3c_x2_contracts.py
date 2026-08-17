from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.gen2.exchange.canonical_json import (
    CanonicalJsonError,
    canonical_json_bytes,
    load_canonical_json,
)
from app.gen2.exchange.identity_bridge import release_trust, trust
from app.gen2.exchange.identity_bridge.canonical import (
    apply_bridge_manifest_identity,
    verify_bridge_manifest_identity,
)
from app.gen2.exchange.identity_bridge.contracts import (
    SportsIdentityBridgeManifestV1,
)


_RELEASE_ANCHORS = (
    "EXPECTED_BRIDGE_MANIFEST_SHA256",
    "EXPECTED_ALIAS_AUTHORITY_SHA256",
    "EXPECTED_DIMENSION_AUTHORITY_SHA256",
)


@pytest.fixture(autouse=True)
def _isolate_synthetic_release_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthetic manifests are independent of a later frozen real release."""

    for module in (release_trust, trust):
        for name in _RELEASE_ANCHORS:
            monkeypatch.setattr(module, name, None)


def _sha(character: str) -> str:
    return character * 64


def _semantic_file(
    logical_uri: str,
    *,
    checksum: str,
    byte_count: int,
    row_count: int | None = None,
    schema: str | None = None,
    authority: str | None = None,
) -> dict[str, Any]:
    if logical_uri.endswith(".parquet"):
        media_type = "application/vnd.apache.parquet"
    elif logical_uri.endswith(".json"):
        media_type = "application/json"
    else:
        media_type = "text/markdown"
    return {
        "logical_uri": logical_uri,
        "media_type": media_type,
        "sha256": checksum,
        "byte_count": byte_count,
        "row_count": row_count,
        "physical_schema_fingerprint": schema,
        "row_authority_sha256": authority,
        "semantic_digest_member": True,
    }


def _manifest_payload() -> dict[str, Any]:
    bridge_files = [
        _semantic_file(
            "active-player-dimension.parquet",
            checksum=_sha("1"),
            byte_count=11,
            row_count=2,
            schema=_sha("2"),
            authority=_sha("b"),
        ),
        _semantic_file(
            "attribution.md",
            checksum=_sha("3"),
            byte_count=12,
        ),
        _semantic_file(
            "player-identity-aliases.parquet",
            checksum=_sha("4"),
            byte_count=13,
            row_count=2,
            schema=_sha("5"),
            authority=_sha("a"),
        ),
        _semantic_file(
            "source-rights.json",
            checksum=_sha("6"),
            byte_count=14,
        ),
    ]
    join_contracts = [
        {
            "join_contract_id": "plate_appearance_batter",
            "source_family": "plate_appearance",
            "physical_field_name": "batter_id",
            "namespace": "RETROSHEET",
            "target_canonical_field": "canonical_player_uuid",
            "join_type": "MANY_TO_ONE_EXACT",
            "null_policy": "REJECT",
            "duplicate_policy": "REJECT",
            "unresolved_policy": "REJECT",
        }
    ]
    join_validation = [
        {
            "join_contract_id": "plate_appearance_batter",
            "seasons": [2021, 2022, 2023, 2024, 2025],
            "reference_count": 10,
            "resolved_reference_count": 10,
            "unresolved_reference_count": 0,
            "ambiguous_reference_count": 0,
            "multiply_mapped_reference_count": 0,
            "unique_source_ids": 2,
            "unique_canonical_players": 2,
            "mapping_rate": 1.0,
        }
    ]
    eligibility = [
        {
            "target_season": season,
            "role": role,
            "reference_rows": 10,
            "rows_with_eligible_prior": 7,
            "legitimate_missing_prior_rows": 3,
            "maximum_eligible_prior_season": season - 1,
            "eligible_prior_rate": 0.7,
            "unresolved_identity_rows": 0,
            "ambiguous_identity_rows": 0,
            "multiply_mapped_identity_rows": 0,
        }
        for season in (2023, 2024, 2025)
        for role in ("BATTER", "PITCHER")
    ]
    return {
        "contract_name": trust.CONTRACT_NAME,
        "contract_version": trust.CONTRACT_VERSION,
        "canonicalization_version": "SPORTS_CANONICAL_JSON_V1",
        "bridge_manifest_id": "PENDING",
        "bridge_manifest_sha256": _sha("0"),
        "bridge_package_id": "PENDING",
        "publisher": trust.PUBLISHER,
        "publication_timestamp": "2026-08-16T12:00:00Z",
        "sport": "Baseball",
        "league": "MLB",
        "parent_binding": {
            "parent_dataset_id": trust.EXPECTED_PARENT_DATASET_ID,
            "parent_dataset_version_id": trust.EXPECTED_PARENT_DATASET_VERSION_ID,
            "parent_dataset_manifest_id": trust.EXPECTED_PARENT_MANIFEST_ID,
            "parent_dataset_manifest_sha256": (
                trust.EXPECTED_PARENT_MANIFEST_SHA256
            ),
            "parent_partition_authority_sha256": (
                trust.EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256
            ),
            "parent_package_id": trust.EXPECTED_PARENT_PACKAGE_ID,
            "plate_appearance_class_order": list(trust.EXPECTED_PA_CLASS_ORDER),
        },
        "evidence_cutoff": trust.EVIDENCE_CUTOFF,
        "source_lineage": {
            "canonical_identity_mapping_count": 736_056,
            "evidence_artifacts": [
                {
                    "artifact_id": "active_identity",
                    "logical_uri": "source-evidence/active-identity.json",
                    "sha256": _sha("7"),
                    "byte_count": 1,
                },
                {
                    "artifact_id": "quarantine_impact",
                    "logical_uri": "source-evidence/quarantine-impact.json",
                    "sha256": _sha("8"),
                    "byte_count": 1,
                },
            ],
            "source_releases": [
                {
                    "source_key": "RETROSHEET",
                    "source_version_id": "00000000-0000-0000-0000-000000000001",
                    "identity_checksum": _sha("9"),
                }
            ],
            "extraction_revision": _sha("c"),
            "publishing_code_revision": _sha("d"),
            "database_transaction_mode": "READ_ONLY",
            "database_writes": 0,
            "provider_network_calls": 0,
            "external_network_calls": 0,
        },
        "identity_namespaces": [
            {
                "namespace": "RETROSHEET",
                "source_keys": ["RETROSHEET"],
                "source_version_ids": [
                    "00000000-0000-0000-0000-000000000001"
                ],
                "alias_rows": 2,
                "unique_source_ids": 2,
                "canonical_players": 2,
            }
        ],
        "bridge_files": bridge_files,
        "coverage_evidence": {
            "canonical_identity_mapping_count": 736_056,
            "active_unique_players_2021_2025": 3,
            "primary_unique_players_2023_2025": 2,
            "portable_parent_unique_players_2021_2025": 2,
            "portable_parent_unique_players_2023_2025": 1,
            "unrepresented_governed_players_2021_2025": 1,
            "unrepresented_governed_players_2023_2025": 1,
            "active_player_references_2021_2025": 20,
            "primary_player_references_2023_2025": 10,
            "portable_parent_player_references_2021_2025": 18,
            "portable_parent_player_references_2023_2025": 9,
            "exact_unique_player_rate_2021_2025": 1.0,
            "exact_reference_rate_2021_2025": 1.0,
            "exact_unique_player_rate_2023_2025": 1.0,
            "exact_reference_rate_2023_2025": 1.0,
            "unresolved_quarantine_rows": 1,
            "ambiguous_quarantine_rows": 1,
            "downstream_impacted_rows": 0,
        },
        "join_contracts": join_contracts,
        "join_validation": join_validation,
        "y_minus_1_prior_eligibility": eligibility,
        "source_rights": {
            "rights_control_ids": ["CHADWICK", "LAHMAN", "RETROSHEET"],
            "allowed_purposes": list(trust.PACKAGE_ALLOWED_PURPOSES),
            "prohibited_purposes": list(trust.PACKAGE_PROHIBITED_PURPOSES),
            "legal_conclusion": "NO_NEW_LEGAL_CONCLUSIONS",
            "public_raw_bridge_download_authorized": False,
            "public_raw_redistribution_authorized": False,
            "identity_data_resale_authorized": False,
            "unrestricted_commercial_redistribution_authorized": False,
        },
        "limitations": [],
        "totals": {
            "alias_rows": 2,
            "active_player_rows": 2,
            "namespace_count": 1,
            "join_contract_count": 1,
            "join_validation_count": 1,
            "y_minus_1_eligibility_records": 6,
            "semantic_file_count": 4,
            "semantic_file_bytes": sum(row["byte_count"] for row in bridge_files),
            "final_package_file_count": 7,
            "alias_authority_sha256": _sha("a"),
            "dimension_authority_sha256": _sha("b"),
        },
    }


def _validated(payload: dict[str, Any] | None = None) -> SportsIdentityBridgeManifestV1:
    source = payload or _manifest_payload()
    first = SportsIdentityBridgeManifestV1.model_validate(
        apply_bridge_manifest_identity(source)
    )
    return SportsIdentityBridgeManifestV1.model_validate(
        apply_bridge_manifest_identity(first.model_dump(mode="json"))
    )


def _validate_mutation(payload: dict[str, Any]) -> SportsIdentityBridgeManifestV1:
    return SportsIdentityBridgeManifestV1.model_validate(
        apply_bridge_manifest_identity(payload)
    )


def test_bridge_canonical_json_rejects_nan_duplicates_and_noncanonical_bytes(
    artifact_root: Path,
) -> None:
    value = {"z": [2, 1], "accent": "café", "a": {"valid": True}}
    encoded = canonical_json_bytes(value, trailing_newline=True)
    assert encoded == b'{"a":{"valid":true},"accent":"caf\xc3\xa9","z":[2,1]}\n'
    assert hashlib.sha256(encoded[:-1]).hexdigest() == hashlib.sha256(
        canonical_json_bytes(value)
    ).hexdigest()

    with pytest.raises(CanonicalJsonError, match="canonical JSON"):
        canonical_json_bytes({"invalid": math.nan})

    duplicate = artifact_root / "duplicate.json"
    duplicate.write_bytes(b'{"key":1,"key":2}\n')
    with pytest.raises(CanonicalJsonError, match="Duplicate JSON key"):
        load_canonical_json(duplicate)

    noncanonical = artifact_root / "noncanonical.json"
    noncanonical.write_bytes(b'{"z": 1, "a": 2}\n')
    with pytest.raises(CanonicalJsonError, match="canonical exchange encoding"):
        load_canonical_json(noncanonical)


def test_bridge_digest_excludes_timestamp_and_verifies_derived_ids() -> None:
    first = _validated()
    later_payload = _manifest_payload()
    later_payload["publication_timestamp"] = "2030-01-01T00:00:00Z"
    later = _validated(later_payload)

    assert later.bridge_manifest_sha256 == first.bridge_manifest_sha256
    assert later.bridge_manifest_id == (
        "sports-identity-bridge-manifest:sha256:"
        f"{first.bridge_manifest_sha256}"
    )
    assert later.bridge_package_id == f"sha256-{first.bridge_manifest_sha256}"
    assert verify_bridge_manifest_identity(later.model_dump(mode="json")) == (
        first.bridge_manifest_sha256
    )

    tampered = later.model_dump(mode="json")
    tampered["bridge_package_id"] = "sha256-" + _sha("f")
    with pytest.raises(CanonicalJsonError, match="bridge_package_id"):
        verify_bridge_manifest_identity(tampered)


def test_bridge_contract_accepts_exact_parent_binding() -> None:
    manifest = _validated()
    assert manifest.parent_binding.parent_dataset_manifest_sha256 == (
        trust.EXPECTED_PARENT_MANIFEST_SHA256
    )
    assert manifest.parent_binding.parent_partition_authority_sha256 == (
        trust.EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256
    )


@pytest.mark.parametrize(
    ("mismatched_anchor", "message"),
    (
        ("EXPECTED_BRIDGE_MANIFEST_SHA256", "release trust anchor"),
        ("EXPECTED_ALIAS_AUTHORITY_SHA256", "Alias authority"),
        ("EXPECTED_DIMENSION_AUTHORITY_SHA256", "Dimension authority"),
    ),
)
def test_frozen_release_anchors_accept_exact_and_reject_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    mismatched_anchor: str,
    message: str,
) -> None:
    manifest = _validated()
    exact = {
        "EXPECTED_BRIDGE_MANIFEST_SHA256": manifest.bridge_manifest_sha256,
        "EXPECTED_ALIAS_AUTHORITY_SHA256": (
            manifest.totals.alias_authority_sha256
        ),
        "EXPECTED_DIMENSION_AUTHORITY_SHA256": (
            manifest.totals.dimension_authority_sha256
        ),
    }
    for module in (release_trust, trust):
        for name, value in exact.items():
            monkeypatch.setattr(module, name, value)

    accepted = SportsIdentityBridgeManifestV1.model_validate(
        manifest.model_dump(mode="json")
    )
    assert accepted.bridge_manifest_sha256 == manifest.bridge_manifest_sha256

    for module in (release_trust, trust):
        monkeypatch.setattr(module, mismatched_anchor, _sha("f"))
    with pytest.raises(ValidationError, match=message):
        SportsIdentityBridgeManifestV1.model_validate(
            manifest.model_dump(mode="json")
        )


def test_governed_and_portable_parent_coverage_reconcile_with_documented_gap() -> None:
    manifest = _validated()
    coverage = manifest.coverage_evidence
    assert coverage.active_unique_players_2021_2025 == (
        coverage.portable_parent_unique_players_2021_2025
        + coverage.unrepresented_governed_players_2021_2025
    )
    assert coverage.primary_unique_players_2023_2025 == (
        coverage.portable_parent_unique_players_2023_2025
        + coverage.unrepresented_governed_players_2023_2025
    )
    assert manifest.totals.active_player_rows == (
        coverage.portable_parent_unique_players_2021_2025
    )

    wrong = _manifest_payload()
    wrong["coverage_evidence"]["unrepresented_governed_players_2021_2025"] = 0
    with pytest.raises(ValidationError, match="player counts do not reconcile"):
        _validate_mutation(wrong)


@pytest.mark.parametrize(
    "field",
    ["parent_dataset_manifest_sha256", "parent_partition_authority_sha256"],
)
def test_bridge_contract_rejects_wrong_parent_authority(field: str) -> None:
    payload = _manifest_payload()
    payload["parent_binding"][field] = _sha("f")
    with pytest.raises(ValidationError, match="frozen parent"):
        _validate_mutation(payload)


@pytest.mark.parametrize("mutation", ["version", "missing", "extra"])
def test_bridge_contract_rejects_version_missing_and_extra_fields(mutation: str) -> None:
    payload = _manifest_payload()
    if mutation == "version":
        payload["contract_version"] = "SPORTS_IDENTITY_BRIDGE_V2"
    elif mutation == "missing":
        payload.pop("coverage_evidence")
    else:
        payload["unexpected"] = True
    with pytest.raises(ValidationError):
        _validate_mutation(payload)


@pytest.mark.parametrize("mutation", ["namespace", "source_version_uuid"])
def test_bridge_contract_rejects_invalid_namespace_and_uuid(mutation: str) -> None:
    payload = _manifest_payload()
    if mutation == "namespace":
        payload["identity_namespaces"][0]["namespace"] = "PLAYER_NAME"
    else:
        payload["identity_namespaces"][0]["source_version_ids"] = ["not-a-uuid"]
    with pytest.raises(ValidationError):
        _validate_mutation(payload)


@pytest.mark.parametrize("mutation", ["missing", "extra", "unordered"])
def test_bridge_contract_rejects_wrong_semantic_file_inventory(mutation: str) -> None:
    payload = _manifest_payload()
    if mutation == "missing":
        payload["bridge_files"].pop()
    elif mutation == "extra":
        payload["bridge_files"].append(
            _semantic_file("unexpected.json", checksum=_sha("e"), byte_count=1)
        )
    else:
        payload["bridge_files"] = list(reversed(payload["bridge_files"]))
    with pytest.raises(ValidationError, match="semantic-file inventory"):
        _validate_mutation(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("alias_rows", 3),
        ("active_player_rows", 3),
        ("semantic_file_bytes", 999),
        ("join_contract_count", 2),
    ],
)
def test_bridge_contract_rejects_nonreconciling_totals(field: str, value: int) -> None:
    payload = _manifest_payload()
    payload["totals"][field] = value
    with pytest.raises(ValidationError):
        _validate_mutation(payload)
