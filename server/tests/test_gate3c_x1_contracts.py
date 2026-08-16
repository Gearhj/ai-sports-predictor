from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.gen2.exchange.canonical_json import (
    CanonicalJsonError,
    apply_manifest_identity,
    canonical_json_bytes,
    canonical_sha256,
    load_canonical_json,
    verify_manifest_identity,
)
from app.gen2.exchange.contracts import (
    CANONICAL_PA_CLASS_ORDER,
    SportsDatasetManifestV1,
    validate_portable_uri,
)
from app.gen2.exchange.evidence import load_fd1_evidence
from app.gen2.exchange.manifest_builder import build_manifest
from app.gen2.exchange.schema_registry import (
    EXPECTED_LOGICAL_SCHEMAS,
    SchemaRegistryError,
    build_schema_registry,
)
from app.gen2.exchange.source_rights import (
    PACKAGE_PROHIBITED_PURPOSES,
    READ_ONLY_REGISTRATION,
    RETROSHEET_ATTRIBUTION,
    SOURCE_RIGHTS_RECORD_ORDER,
    build_attribution_markdown,
    build_source_rights,
)


def _package_artifact(logical_uri: str, payload: bytes) -> dict[str, object]:
    return {
        "logical_uri": logical_uri,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_count": len(payload),
        "semantic_digest_member": True,
    }


@pytest.fixture(scope="module")
def authoritative_bundle() -> dict[str, object]:
    evidence = load_fd1_evidence()
    registry = build_schema_registry(evidence.recovery_manifest)
    rights = build_source_rights()
    registry_bytes = canonical_json_bytes(registry, trailing_newline=True)
    rights_bytes = canonical_json_bytes(rights, trailing_newline=True)
    attribution_bytes = (
        build_attribution_markdown().replace("\r\n", "\n").rstrip("\n") + "\n"
    ).encode("utf-8")
    source_artifact_ids: dict[str, dict[str, object]] = {}
    for public_id, local_id in {
        "finalized_evidence": "final_result",
        "readiness_evidence": "readiness",
        "active_identity_evidence": "active_identity",
        "quarantine_impact_evidence": "quarantine_impact",
    }.items():
        artifact = evidence.artifact(local_id)
        source_artifact_ids[public_id] = {
            "artifact_id": public_id,
            "logical_uri": f"source-evidence/{public_id}/sha256-{artifact.sha256}.json",
            "sha256": artifact.sha256,
            "byte_count": artifact.byte_count,
        }
    manifest = build_manifest(
        recovery_manifest=evidence.recovery_manifest,
        readiness=evidence.readiness,
        finalized_result=evidence.final_result,
        active_identity=evidence.active_identity,
        quarantine_impact=evidence.quarantine_impact,
        source_artifact_identities=source_artifact_ids,
        schema_registry_artifact=_package_artifact(
            "schema-registry.json", registry_bytes
        ),
        source_rights_artifact=_package_artifact("source-rights.json", rights_bytes),
        attribution_artifact=_package_artifact("attribution.md", attribution_bytes),
        publishing_code_revision="f" * 64,
        source_rights=rights,
        published_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    return {
        "evidence": evidence,
        "registry": registry,
        "rights": rights,
        "manifest": manifest,
    }


def test_canonical_json_digest_and_semantic_manifest_identity(
    artifact_root: Path,
) -> None:
    value = {"z": [3, 2, 1], "accent": "café", "a": {"truth": True}}
    encoded = canonical_json_bytes(value, trailing_newline=True)
    assert encoded == (
        b'{"a":{"truth":true},"accent":"caf\xc3\xa9","z":[3,2,1]}\n'
    )
    assert canonical_sha256(value) == hashlib.sha256(encoded[:-1]).hexdigest()

    path = artifact_root / "canonical.json"
    path.write_bytes(encoded)
    assert load_canonical_json(path) == value

    base = {
        "dataset_id": "asp.mlb.open-data.fd1",
        "published_at": "2026-08-15T00:00:00Z",
        "semantic_value": {"rows": 3_738_096},
    }
    first = apply_manifest_identity(base)
    later = apply_manifest_identity(
        {**base, "published_at": "2030-01-01T00:00:00Z"}
    )
    assert first["manifest_sha256"] == later["manifest_sha256"]
    assert verify_manifest_identity(first) == first["manifest_sha256"]
    assert first["package_id"] == f"sha256-{first['manifest_sha256']}"

    tampered = copy.deepcopy(first)
    tampered["semantic_value"]["rows"] += 1
    with pytest.raises(CanonicalJsonError, match="semantic manifest digest"):
        verify_manifest_identity(tampered)


@pytest.mark.parametrize(
    "logical_uri",
    [
        "/absolute/path.parquet",
        "C:/absolute/path.parquet",
        "//server/share/path.parquet",
        r"\\server\share\path.parquet",
        "file:///tmp/path.parquet",
        "data/../escape.parquet",
        "./data/path.parquet",
        "data//path.parquet",
        r"data\path.parquet",
    ],
)
def test_portable_uri_rejects_absolute_traversal_unc_and_backslashes(
    logical_uri: str,
) -> None:
    with pytest.raises(ValueError):
        validate_portable_uri(logical_uri)


def test_canonical_loader_rejects_duplicate_keys_and_noncanonical_bytes(
    artifact_root: Path,
) -> None:
    duplicate = artifact_root / "duplicate.json"
    duplicate.write_bytes(b'{"key":1,"key":2}\n')
    with pytest.raises(CanonicalJsonError, match="Duplicate JSON key"):
        load_canonical_json(duplicate)

    noncanonical = artifact_root / "noncanonical.json"
    noncanonical.write_bytes(b'{"z": 1, "a": 2}\n')
    with pytest.raises(CanonicalJsonError, match="canonical exchange encoding"):
        load_canonical_json(noncanonical)


@pytest.mark.parametrize("mutation", ["missing_version", "unknown_version"])
def test_manifest_contract_rejects_missing_and_unknown_versions(
    authoritative_bundle: dict[str, object], mutation: str
) -> None:
    manifest = authoritative_bundle["manifest"]
    assert isinstance(manifest, SportsDatasetManifestV1)
    payload = manifest.model_dump(mode="json")
    if mutation == "missing_version":
        payload.pop("contract_version")
    else:
        payload["contract_version"] = "SPORTS_DATASET_MANIFEST_V2"
    with pytest.raises(ValidationError):
        SportsDatasetManifestV1.model_validate(payload)


def test_manifest_contract_rejects_population_count_and_class_order_changes(
    authoritative_bundle: dict[str, object],
) -> None:
    manifest = authoritative_bundle["manifest"]
    assert isinstance(manifest, SportsDatasetManifestV1)
    assert verify_manifest_identity(manifest.model_dump(mode="json")) == (
        manifest.manifest_sha256
    )

    wrong_population = manifest.model_dump(mode="json")
    wrong_population["populations"][0]["expected_row_count"] += 1
    with pytest.raises(ValidationError, match="Population policy/predicate"):
        SportsDatasetManifestV1.model_validate(wrong_population)

    wrong_order = manifest.model_dump(mode="json")
    pa = next(
        row
        for row in wrong_order["target_definitions"]
        if row["target_id"] == "plate_appearance_outcome"
    )
    pa["ordered_classes"] = list(reversed(CANONICAL_PA_CLASS_ORDER))
    with pytest.raises(ValidationError, match="Plate-appearance taxonomy"):
        SportsDatasetManifestV1.model_validate(wrong_order)


def test_schema_registry_has_ten_families_thirty_three_versions_and_full_counts(
    authoritative_bundle: dict[str, object],
) -> None:
    registry = authoritative_bundle["registry"]
    assert isinstance(registry, dict)
    assert registry["schema_name"] == "SPORTS_DATASET_SCHEMA_REGISTRY_V1"
    assert registry["family_count"] == 10
    assert registry["physical_variant_count"] == 33
    assert registry["partition_count"] == 329

    families = registry["families"]
    assert [row["family"] for row in families] == sorted(EXPECTED_LOGICAL_SCHEMAS)
    assert sum(row["physical_variant_count"] for row in families) == 33
    assert sum(row["partition_count"] for row in families) == 329
    seen_versions: set[str] = set()
    seen_family_fingerprints: set[tuple[str, str]] = set()
    provenance_fields: set[str] = set()
    for family in families:
        assert sum(
            variant["compatible_partition_count"]
            for variant in family["physical_variants"]
        ) == family["partition_count"]
        for variant in family["physical_variants"]:
            version = variant["physical_schema_version"]
            family_fingerprint = (
                family["family"],
                variant["physical_schema_fingerprint"],
            )
            assert version not in seen_versions
            assert family_fingerprint not in seen_family_fingerprints
            seen_versions.add(version)
            seen_family_fingerprints.add(family_fingerprint)
            physical_fields = [
                {"name": field["name"], "dtype": field["dtype"]}
                for field in variant["fields"]
            ]
            assert canonical_sha256(physical_fields) == (
                variant["physical_schema_fingerprint"]
            )
            for field in variant["fields"]:
                assert field["nullable"] is True
                assert field["semantic_description"]
                assert field["target_feature_identity_classification"]
                assert field["temporal_risk_classification"]
                assert field["source_field_lineage"]
                if field["name"] in {"source_key", "source_version_id"}:
                    assert (
                        field["target_feature_identity_classification"]
                        == "PROVENANCE"
                    )
                    assert field["temporal_risk_classification"] == "PROVENANCE_ONLY"
                    provenance_fields.add(field["name"])
    assert len(seen_versions) == len(seen_family_fingerprints) == 33
    assert provenance_fields == {"source_key", "source_version_id"}
    assert not {
        "logical_uri",
        "path",
        "published_at",
        "timestamp",
    }.intersection(str(key) for key in registry)


def test_schema_registry_rejects_recovery_manifest_tampering(
    authoritative_bundle: dict[str, object],
) -> None:
    evidence = authoritative_bundle["evidence"]
    payload = evidence.recovery_manifest.to_dict()
    fingerprint = payload["files"][0]["physical_schema_fingerprint"]
    payload["files"][0]["physical_schema_fingerprint"] = (
        ("0" if fingerprint[0] != "0" else "1") + fingerprint[1:]
    )
    with pytest.raises(SchemaRegistryError, match="fingerprint mismatch"):
        build_schema_registry(payload)


def test_source_rights_freezes_notice_six_records_zero_use_and_prohibitions(
    authoritative_bundle: dict[str, object],
) -> None:
    rights = authoritative_bundle["rights"]
    assert isinstance(rights, dict)
    assert rights["record_count"] == 6
    assert tuple(row["rights_control_id"] for row in rights["records"]) == (
        SOURCE_RIGHTS_RECORD_ORDER
    )
    assert tuple(rights["package_prohibited_purposes"]) == (
        PACKAGE_PROHIBITED_PURPOSES
    )
    assert rights["package_controls"] == {
        "legal_conclusion": "NO_NEW_LEGAL_CONCLUSIONS",
        "public_redistribution_authorized": False,
        "raw_redistribution_authorized": False,
        "unreviewed_commercial_redistribution_authorized": False,
    }

    expected_notice = (
        "The information used here was obtained free of\n"
        "charge from and is copyrighted by Retrosheet.  Interested\n"
        'parties may contact Retrosheet at "www.retrosheet.org".'
    )
    assert RETROSHEET_ATTRIBUTION == expected_notice
    by_id = {row["rights_control_id"]: row for row in rights["records"]}
    assert by_id["RETROSHEET"]["required_attribution_text"] == expected_notice

    for record_id in (
        "LAHMAN_SEAMHEADS_NEGRO_LEAGUES_EXCLUDED",
        "NOAA_NCEI_LCDV2_ZERO_USE",
        "OPEN_METEO_FREE_HOSTED_ZERO_USE",
    ):
        record = by_id[record_id]
        assert record["fd1_use"]["manifest_artifact_count"] == 0
        assert record["allowed_purposes"] == [READ_ONLY_REGISTRATION]
    assert by_id["NOAA_NCEI_LCDV2_ZERO_USE"]["source_releases"] == []
    assert by_id["OPEN_METEO_FREE_HOSTED_ZERO_USE"]["source_releases"] == []

    markdown = build_attribution_markdown()
    assert "> The information used here was obtained free of" in markdown
    assert "> parties may contact Retrosheet at \"www.retrosheet.org\"." in markdown
    assert build_source_rights() == rights
