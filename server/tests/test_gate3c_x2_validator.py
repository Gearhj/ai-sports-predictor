from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import pytest

from app.gen2.exchange.canonical_json import canonical_json_bytes
from app.gen2.exchange.identity_bridge import (
    release_trust,
    rights,
    security,
    trust,
    validator,
)
from app.gen2.exchange.identity_bridge.canonical import (
    CANONICALIZATION_VERSION,
    apply_bridge_manifest_identity,
)
from app.gen2.exchange.identity_bridge.contracts import (
    SportsIdentityBridgeManifestV1,
)
from app.gen2.exchange.identity_bridge.package_metadata import readme_bytes
from app.gen2.exchange.identity_bridge.parent import (
    FamilyFieldSeasonEvidence,
    ParentIdentityEvidence,
    PlateAppearanceRoleEvidence,
    PlayerFamilyCoverage,
    PlayerPriorEvidence,
    ReferenceCoverageEvidence,
    RetrosheetPlayerEvidence,
)
from app.gen2.exchange.identity_bridge.revision import (
    extraction_code_revision,
    publishing_code_revision,
)
from app.gen2.exchange.identity_bridge.rows import (
    apply_row_hash,
    build_alias_parquet,
    build_dimension_parquet,
)


_RAW_ID = "aardd001"
_CANONICAL_ID = "00000000-0000-4000-8000-000000000001"
_LAHMAN_ID = "aardsda01"
_PROJECT_ROOT = Path(validator.__file__).resolve().parents[5]
_RELEASE_ANCHORS = (
    "EXPECTED_BRIDGE_MANIFEST_SHA256",
    "EXPECTED_ALIAS_AUTHORITY_SHA256",
    "EXPECTED_DIMENSION_AUTHORITY_SHA256",
)


@pytest.fixture(autouse=True)
def _isolate_synthetic_release_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthetic packages must remain valid after the real release is frozen."""

    for module in (release_trust, trust):
        for name in _RELEASE_ANCHORS:
            monkeypatch.setattr(module, name, None)


def _sha(character: str) -> str:
    return character * 64


def _write_parent_rights(root: Path) -> None:
    root.mkdir()
    notices = {
        "CHADWICK_PERSONS_REGISTER": rights._CHADWICK_NOTICE,
        "RETROSHEET": rights._RETROSHEET_NOTICE,
        "SABR_LAHMAN_MODERN_MLB": rights._LAHMAN_NOTICE,
    }
    parent_rights = {
        "schema_version": rights.PARENT_SOURCE_RIGHTS_SCHEMA_VERSION,
        "package_allowed_purposes": list(rights.ALLOWED_PURPOSES),
        "package_controls": {
            "legal_conclusion": "NO_NEW_LEGAL_CONCLUSIONS",
            "public_redistribution_authorized": False,
            "raw_redistribution_authorized": False,
            "unreviewed_commercial_redistribution_authorized": False,
        },
        "records": [
            {
                "rights_control_id": rights_id,
                "required_attribution_text": notices[rights_id],
                "allowed_purposes": list(rights.ALLOWED_PURPOSES),
            }
            for rights_id in rights.REQUIRED_RIGHTS_IDS
        ],
    }
    (root / "source-rights.json").write_bytes(
        canonical_json_bytes(parent_rights, trailing_newline=True)
    )
    (root / "attribution.md").write_text(
        "\n\n".join(notices.values()) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _prior(domain: str, source_row: int) -> PlayerPriorEvidence:
    return PlayerPriorEvidence(
        event_family="player_prior",
        canonical_game_id=None,
        canonical_player_id=_CANONICAL_ID,
        player_id=None,
        lahman_player_id=_LAHMAN_ID,
        identity_resolution_status="EXACT",
        season=2022,
        official_date="2022-12-31",
        official_date_semantics="SEASON_END",
        population="MODERN_MLB",
        coverage_role="PLAYER_PRIOR",
        domain=domain,
        position=None,
        eligible_from_season=2023,
        same_season_predictor_eligible=False,
        team_ids=("BOS",),
        league_ids=("AL",),
        metrics_json="{}",
        source_key="SABR_LAHMAN_BOX_DOWNLOADS",
        source_releases=("lahman_1871-2025_csv",),
        source_logical_tables=(domain.title(),),
        source_logical_uris=(f"open-data/lahman/{domain.lower()}.csv",),
        source_file_sha256=_sha("1"),
        source_file_sha256s=(_sha("1"),),
        source_row_numbers=(source_row,),
        source_row_sha256s=(_sha("2"),),
        source_derivation_checksum=_sha("3"),
        timing_classification="RETROSPECTIVE_RECONSTRUCTION",
        source_version_id=trust.EXPECTED_SOURCE_RELEASES[-1][1],
        source_partition_uri="historical-events/player-prior.parquet",
        source_partition_sha256=_sha("4"),
    )


def _parent_evidence(parent_root: Path) -> ParentIdentityEvidence:
    field_rows: list[FamilyFieldSeasonEvidence] = []
    for family, field in validator._CONSUMER_JOIN_FIELDS:
        for season in (2021, 2022, 2023, 2024, 2025):
            active = season >= 2023
            field_rows.append(
                FamilyFieldSeasonEvidence(
                    family=family,
                    field=field,
                    raw_mirror_field=f"raw_{field}",
                    season=season,
                    reference_count=1 if active else 0,
                    unique_player_count=1 if active else 0,
                )
            )

    other = (
        PlayerFamilyCoverage(_RAW_ID, "bullpen_usage", 2023, 2025, 3, ("player_id",)),
        PlayerFamilyCoverage(
            _RAW_ID,
            "pitch_sequence",
            2023,
            2025,
            6,
            ("batter_id", "pitcher_id"),
        ),
        PlayerFamilyCoverage(
            _RAW_ID, "player_game_batting", 2023, 2025, 3, ("player_id",)
        ),
        PlayerFamilyCoverage(
            _RAW_ID, "player_game_fielding", 2023, 2025, 3, ("player_id",)
        ),
        PlayerFamilyCoverage(
            _RAW_ID, "player_game_pitching", 2023, 2025, 3, ("player_id",)
        ),
        PlayerFamilyCoverage(_RAW_ID, "starter_usage", 2023, 2025, 3, ("player_id",)),
    )
    governed = ReferenceCoverageEvidence(
        reference_count=27,
        unique_player_count=1,
        primary_reference_count=27,
        primary_unique_player_count=1,
        expected_reference_count=21_356_559,
        expected_unique_player_count=2_693,
        expected_primary_reference_count=12_838_083,
        expected_primary_unique_player_count=2_071,
        counts_reconcile=False,
    )
    return ParentIdentityEvidence(
        parent_root=parent_root,
        parent_manifest_id=trust.EXPECTED_PARENT_MANIFEST_ID,
        parent_manifest_sha256=trust.EXPECTED_PARENT_MANIFEST_SHA256,
        parent_dataset_version_id=trust.EXPECTED_PARENT_DATASET_VERSION_ID,
        parent_package_id=trust.EXPECTED_PARENT_PACKAGE_ID,
        parent_partition_authority_sha256=(
            trust.EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256
        ),
        pa_class_order=trust.EXPECTED_PA_CLASS_ORDER,
        minimum_join_field_specs=(),
        governed_reference_field_specs=(),
        field_season_evidence=tuple(field_rows),
        players=(
            RetrosheetPlayerEvidence(
                retrosheet_player_id=_RAW_ID,
                first_season=2023,
                last_season=2025,
                total_join_references=27,
                plate_appearance_batter_references=3,
                plate_appearance_pitcher_references=3,
                seasons=(2023, 2024, 2025),
                families=tuple(
                    sorted({family for family, _field in validator._CONSUMER_JOIN_FIELDS})
                ),
            ),
        ),
        plate_appearance_role_evidence=tuple(
            PlateAppearanceRoleEvidence(_RAW_ID, season, 1, 1)
            for season in (2023, 2024, 2025)
        ),
        other_family_coverage=other,
        player_priors=(_prior("BATTING", 1), _prior("PITCHING", 2)),
        all_retrosheet_player_ids=(_RAW_ID,),
        primary_retrosheet_player_ids=(_RAW_ID,),
        minimum_join_coverage=governed,
        governed_reference_coverage=governed,
        governed_counts_reconcile=False,
    )


def _alias_rows() -> list[dict[str, Any]]:
    chadwick = trust.EXPECTED_SOURCE_RELEASES[0]
    lahman = trust.EXPECTED_SOURCE_RELEASES[-1]
    definitions = (
        ("RETROSHEET", _RAW_ID, chadwick, f"retrosheet:player:{_RAW_ID}"),
        ("LAHMAN", _LAHMAN_ID, lahman, _LAHMAN_ID),
        ("CHADWICK", _CANONICAL_ID, chadwick, None),
        ("MLBAM", "12345", chadwick, None),
        ("BASEBALL_REFERENCE", "aardsda01", chadwick, None),
    )
    return [
        {
            "canonical_player_uuid": _CANONICAL_ID,
            "namespace": namespace,
            "source_player_id": source_id,
            "parent_join_key": join_key,
            "source_key": release[0],
            "source_version_id": release[1],
            "source_version_identity_sha256": release[2],
            "mapping_confidence": "EXACT",
            "mapping_status": "RESOLVED",
            "mapping_basis": "EXPLICIT_IDENTIFIER_EXACT",
            "first_represented_season": 2022,
            "last_represented_season": 2025,
            "primary_2023_2025": True,
            "source_row_number": index,
            "source_row_sha256": hashlib.sha256(
                f"source-row-{index}".encode("utf-8")
            ).hexdigest(),
            "source_row_identity": "PENDING",
            "evidence_logical_uri": f"open-data/identity/row-{index}.csv",
            "mapping_identity_sha256": hashlib.sha256(
                f"mapping-{index}".encode("utf-8")
            ).hexdigest(),
        }
        for index, (namespace, source_id, release, join_key) in enumerate(
            definitions, start=1
        )
    ]


def _normalized_alias_rows() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in _alias_rows():
        row = dict(raw)
        row["source_row_identity"] = f"sha256:{row['source_row_sha256']}"
        result.append(apply_row_hash(row))
    return result


def _dimension_rows() -> list[dict[str, Any]]:
    return [
        apply_row_hash(
            {
                "canonical_player_uuid": _CANONICAL_ID,
                "first_represented_season": 2022,
                "last_represented_season": 2025,
                "prior_history_2021_2022": True,
                "primary_active_2023_2025": True,
                "has_retrosheet_alias": True,
                "has_lahman_alias": True,
                "has_chadwick_alias": True,
                "has_mlbam_alias": True,
                "has_baseball_reference_alias": True,
                "has_player_prior": True,
                "minimum_player_prior_target_season": 2023,
                "maximum_player_prior_target_season": 2023,
                "batter_reference_count": 3,
                "pitcher_reference_count": 3,
                "other_player_reference_count": 21,
                "player_prior_row_count": 2,
            }
        )
    ]


def _bridge_file(
    logical_uri: str,
    payload: bytes,
    *,
    alias_artifact: Any,
    dimension_artifact: Any,
) -> dict[str, Any]:
    artifact = {
        alias_artifact.logical_uri: alias_artifact,
        dimension_artifact.logical_uri: dimension_artifact,
    }.get(logical_uri)
    if artifact is not None:
        return {
            "logical_uri": logical_uri,
            "media_type": "application/vnd.apache.parquet",
            "sha256": artifact.file_sha256,
            "byte_count": artifact.byte_count,
            "row_count": artifact.row_count,
            "physical_schema_fingerprint": artifact.schema_fingerprint,
            "row_authority_sha256": artifact.row_authority_sha256,
            "semantic_digest_member": True,
        }
    return {
        "logical_uri": logical_uri,
        "media_type": (
            "application/json" if logical_uri.endswith(".json") else "text/markdown"
        ),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_count": len(payload),
        "row_count": None,
        "physical_schema_fingerprint": None,
        "row_authority_sha256": None,
        "semantic_digest_member": True,
    }


def _build_package(
    package_root: Path,
    parent: ParentIdentityEvidence,
    *,
    semantic_overrides: dict[str, bytes] | None = None,
    manifest_mutator: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[SportsIdentityBridgeManifestV1, list[dict[str, Any]], list[dict[str, Any]]]:
    package_root.mkdir()
    alias_rows = _normalized_alias_rows()
    dimension_rows = _dimension_rows()
    alias_artifact = build_alias_parquet(alias_rows)
    dimension_artifact = build_dimension_parquet(dimension_rows)
    semantic: dict[str, bytes] = {
        "active-player-dimension.parquet": dimension_artifact.payload,
        "attribution.md": rights.attribution_bytes(parent.parent_root),
        "player-identity-aliases.parquet": alias_artifact.payload,
        "source-rights.json": rights.source_rights_bytes(parent.parent_root),
    }
    semantic.update(semantic_overrides or {})
    resolution = {_RAW_ID: _CANONICAL_ID}
    contracts, join_validation = validator._recompute_join_evidence(parent, resolution)
    y_minus_1 = validator._recompute_y_minus_1(parent, resolution)
    bridge_files = [
        _bridge_file(
            logical_uri,
            semantic[logical_uri],
            alias_artifact=alias_artifact,
            dimension_artifact=dimension_artifact,
        )
        for logical_uri in trust.REQUIRED_SEMANTIC_BRIDGE_FILES
    ]
    payload: dict[str, Any] = {
        "contract_name": trust.CONTRACT_NAME,
        "contract_version": trust.CONTRACT_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "bridge_manifest_id": "PENDING",
        "bridge_manifest_sha256": "0" * 64,
        "bridge_package_id": "PENDING",
        "publisher": trust.PUBLISHER,
        "publication_timestamp": "2026-08-16T12:00:00Z",
        "sport": "Baseball",
        "league": "MLB",
        "parent_binding": {
            "parent_dataset_id": trust.EXPECTED_PARENT_DATASET_ID,
            "parent_dataset_version_id": parent.parent_dataset_version_id,
            "parent_dataset_manifest_id": parent.parent_manifest_id,
            "parent_dataset_manifest_sha256": parent.parent_manifest_sha256,
            "parent_partition_authority_sha256": (
                parent.parent_partition_authority_sha256
            ),
            "parent_package_id": parent.parent_package_id,
            "plate_appearance_class_order": list(parent.pa_class_order),
        },
        "evidence_cutoff": trust.EVIDENCE_CUTOFF,
        "source_lineage": {
            "canonical_identity_mapping_count": 736_056,
            "evidence_artifacts": [
                {
                    "artifact_id": artifact_id,
                    "logical_uri": logical_uri,
                    "sha256": sha256,
                    "byte_count": byte_count,
                }
                for artifact_id, logical_uri, sha256, byte_count
                in trust.EXPECTED_EVIDENCE_ARTIFACTS
            ],
            "source_releases": [
                {
                    "source_key": row[0],
                    "source_version_id": row[1],
                    "identity_checksum": row[2],
                }
                for row in trust.EXPECTED_SOURCE_RELEASES
            ],
            "extraction_revision": extraction_code_revision(_PROJECT_ROOT),
            "publishing_code_revision": publishing_code_revision(_PROJECT_ROOT),
            "database_transaction_mode": "READ_ONLY",
            "database_writes": 0,
            "provider_network_calls": 0,
            "external_network_calls": 0,
        },
        "identity_namespaces": validator._namespace_inventory(alias_rows),
        "bridge_files": bridge_files,
        "coverage_evidence": {
            "canonical_identity_mapping_count": 736_056,
            "active_unique_players_2021_2025": 2_693,
            "primary_unique_players_2023_2025": 2_071,
            "portable_parent_unique_players_2021_2025": 1,
            "portable_parent_unique_players_2023_2025": 1,
            "unrepresented_governed_players_2021_2025": 2_692,
            "unrepresented_governed_players_2023_2025": 2_070,
            "active_player_references_2021_2025": 21_356_559,
            "primary_player_references_2023_2025": 12_838_083,
            "portable_parent_player_references_2021_2025": 27,
            "portable_parent_player_references_2023_2025": 27,
            "exact_unique_player_rate_2021_2025": 1.0,
            "exact_reference_rate_2021_2025": 1.0,
            "exact_unique_player_rate_2023_2025": 1.0,
            "exact_reference_rate_2023_2025": 1.0,
            "unresolved_quarantine_rows": 1_187,
            "ambiguous_quarantine_rows": 16,
            "downstream_impacted_rows": 0,
        },
        "join_contracts": contracts,
        "join_validation": join_validation,
        "y_minus_1_prior_eligibility": y_minus_1,
        "source_rights": {
            "rights_control_ids": list(rights.REQUIRED_RIGHTS_IDS),
            "allowed_purposes": list(rights.ALLOWED_PURPOSES),
            "prohibited_purposes": list(rights.PROHIBITED_PURPOSES),
            "legal_conclusion": "NO_NEW_LEGAL_CONCLUSIONS",
            "public_raw_bridge_download_authorized": False,
            "public_raw_redistribution_authorized": False,
            "identity_data_resale_authorized": False,
            "unrestricted_commercial_redistribution_authorized": False,
        },
        "limitations": [
            {
                "limitation_id": limitation_id,
                "description": description,
                "scope": scope,
            }
            for limitation_id, description, scope in trust.EXPECTED_LIMITATIONS
        ],
        "totals": {
            "alias_rows": alias_artifact.row_count,
            "active_player_rows": dimension_artifact.row_count,
            "namespace_count": 5,
            "join_contract_count": len(contracts),
            "join_validation_count": len(join_validation),
            "y_minus_1_eligibility_records": 6,
            "semantic_file_count": 4,
            "semantic_file_bytes": sum(row["byte_count"] for row in bridge_files),
            "final_package_file_count": 7,
            "alias_authority_sha256": alias_artifact.row_authority_sha256,
            "dimension_authority_sha256": dimension_artifact.row_authority_sha256,
        },
    }
    if manifest_mutator is not None:
        manifest_mutator(payload)
    resolved = apply_bridge_manifest_identity(payload)
    manifest = SportsIdentityBridgeManifestV1.model_validate(resolved)
    manifest = SportsIdentityBridgeManifestV1.model_validate(
        apply_bridge_manifest_identity(manifest.model_dump(mode="json"))
    )
    for logical_uri, data in semantic.items():
        (package_root / logical_uri).write_bytes(data)
    (package_root / validator.MANIFEST_LOGICAL_URI).write_bytes(
        canonical_json_bytes(manifest.model_dump(mode="json"), trailing_newline=True)
    )
    return manifest, alias_rows, dimension_rows


@pytest.fixture
def valid_staging_package(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, ParentIdentityEvidence]:
    parent_root = artifact_root / "parent"
    _write_parent_rights(parent_root)
    parent = _parent_evidence(parent_root)
    bridge_root = artifact_root / "bridge"
    _build_package(bridge_root, parent)
    monkeypatch.setattr(
        validator,
        "scan_parent_identity",
        lambda _root, **_kwargs: parent,
    )
    return bridge_root, parent_root, parent


def _add_auxiliary(
    bridge_root: Path,
    staging_result: validator.IdentityBridgeValidationResult,
) -> None:
    (bridge_root / "validation-report.json").write_bytes(staging_result.report_bytes)
    (bridge_root / "README.md").write_bytes(
        readme_bytes(staging_result.manifest, staging_result.report_bytes)
    )


def test_staging_and_full_validation_are_deterministic(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    bridge_root, parent_root, _parent = valid_staging_package
    first = validator.validate_identity_bridge(
        bridge_root, parent_root, require_auxiliary=False
    )
    second = validator.validate_identity_bridge(
        bridge_root, parent_root, require_auxiliary=False
    )

    assert first.status == "PASS"
    assert first.file_count == 5
    assert first.report == second.report
    assert first.report_bytes == second.report_bytes
    assert first.report_bytes == validator.validation_report_bytes(first.report)

    _add_auxiliary(bridge_root, first)
    final = validator.validate_identity_bridge(
        bridge_root, parent_root, require_auxiliary=True
    )
    assert final.status == "PASS"
    assert final.file_count == 7
    assert final.report_bytes == first.report_bytes


@pytest.mark.parametrize("mismatched_anchor", _RELEASE_ANCHORS)
def test_validator_remains_bound_to_frozen_release_anchors(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
    monkeypatch: pytest.MonkeyPatch,
    mismatched_anchor: str,
) -> None:
    bridge_root, parent_root, _parent = valid_staging_package
    baseline = validator.validate_identity_bridge(
        bridge_root, parent_root, require_auxiliary=False
    )
    exact = {
        "EXPECTED_BRIDGE_MANIFEST_SHA256": (
            baseline.manifest.bridge_manifest_sha256
        ),
        "EXPECTED_ALIAS_AUTHORITY_SHA256": (
            baseline.manifest.totals.alias_authority_sha256
        ),
        "EXPECTED_DIMENSION_AUTHORITY_SHA256": (
            baseline.manifest.totals.dimension_authority_sha256
        ),
    }
    for module in (release_trust, trust):
        for name, value in exact.items():
            monkeypatch.setattr(module, name, value)
    accepted = validator.validate_identity_bridge(
        bridge_root, parent_root, require_auxiliary=False
    )
    assert accepted.manifest.bridge_manifest_sha256 == (
        baseline.manifest.bridge_manifest_sha256
    )

    wrong = _sha("f") if exact[mismatched_anchor] != _sha("f") else _sha("e")
    for module in (release_trust, trust):
        monkeypatch.setattr(module, mismatched_anchor, wrong)
    with pytest.raises(validator.IdentityBridgeValidationError, match="manifest is invalid"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=False
        )


def test_auxiliary_modes_accept_crash_safe_five_six_or_seven_file_staging(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    bridge_root, parent_root, _parent = valid_staging_package
    with pytest.raises(validator.IdentityBridgeValidationError, match="inventory"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=True
        )

    staging = validator.validate_identity_bridge(
        bridge_root, parent_root, require_auxiliary=False
    )
    assert staging.file_count == 5

    report_path = bridge_root / "validation-report.json"
    readme_path = bridge_root / "README.md"
    report_path.write_bytes(staging.report_bytes)
    report_only = validator.validate_identity_bridge(
        bridge_root, parent_root, require_auxiliary=False
    )
    assert report_only.file_count == 6

    report_path.unlink()
    readme_path.write_bytes(readme_bytes(staging.manifest, staging.report_bytes))
    readme_only = validator.validate_identity_bridge(
        bridge_root, parent_root, require_auxiliary=False
    )
    assert readme_only.file_count == 6

    report_path.write_bytes(staging.report_bytes)
    resumed = validator.validate_identity_bridge(
        bridge_root, parent_root, require_auxiliary=False
    )
    assert resumed.file_count == 7
    final = validator.validate_identity_bridge(
        bridge_root, parent_root, require_auxiliary=True
    )
    assert final.file_count == 7


def test_extra_file_and_empty_directory_are_rejected(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    bridge_root, parent_root, _parent = valid_staging_package
    (bridge_root / "extra.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(validator.IdentityBridgeValidationError, match="extra"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=False
        )
    (bridge_root / "extra.txt").unlink()
    (bridge_root / "empty").mkdir()
    with pytest.raises(validator.IdentityBridgeValidationError, match="directory"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=False
        )


def test_hardlinked_package_file_is_rejected(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
    artifact_root: Path,
) -> None:
    bridge_root, parent_root, _parent = valid_staging_package
    attribution = bridge_root / "attribution.md"
    external = artifact_root / "external-attribution.md"
    external.write_bytes(attribution.read_bytes())
    attribution.unlink()
    try:
        os.link(external, attribution)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")
    with pytest.raises(validator.IdentityBridgeValidationError, match="unsafe"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=False
        )


def test_symlinked_package_file_is_rejected(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
    artifact_root: Path,
) -> None:
    bridge_root, parent_root, _parent = valid_staging_package
    attribution = bridge_root / "attribution.md"
    external = artifact_root / "symlink-target.md"
    external.write_bytes(attribution.read_bytes())
    attribution.unlink()
    try:
        attribution.symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(validator.IdentityBridgeValidationError, match="unsafe"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=False
        )


def test_duplicate_key_and_noncanonical_manifest_bytes_fail_closed(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    bridge_root, parent_root, _parent = valid_staging_package
    manifest_path = bridge_root / validator.MANIFEST_LOGICAL_URI
    manifest_path.write_bytes(b'{"contract_name":"a","contract_name":"b"}\n')
    with pytest.raises(validator.IdentityBridgeValidationError, match="manifest"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=False
        )

    raw = {"contract_name": "a"}
    manifest_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(validator.IdentityBridgeValidationError, match="manifest"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=False
        )


def test_fresh_parent_rescan_must_match_manifest_binding(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root, parent_root, parent = valid_staging_package
    monkeypatch.setattr(
        validator,
        "scan_parent_identity",
        lambda _root, **_kwargs: replace(parent, parent_package_id="sha256-changed"),
    )
    with pytest.raises(validator.IdentityBridgeValidationError, match="parent binding"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=False
        )


def test_parquet_row_tampering_is_rejected(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    import polars as pl

    bridge_root, parent_root, _parent = valid_staging_package
    path = bridge_root / "player-identity-aliases.parquet"
    frame = pl.read_parquet(path).with_columns(
        pl.lit("UNRESOLVED").alias("mapping_status")
    )
    frame.write_parquet(path)
    with pytest.raises(validator.IdentityBridgeValidationError, match="Parquet"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=False
        )


def test_all_five_namespaces_and_parent_join_coverage_are_required(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    _bridge_root, _parent_root, parent = valid_staging_package
    aliases = _normalized_alias_rows()
    dimensions = _dimension_rows()
    manifest, _rows, _dimensions = _build_package(
        parent.parent_root.parent / "secondary-bridge", parent
    )

    with pytest.raises(validator.IdentityBridgeValidationError, match="all five"):
        validator._validate_namespace_inventory(
            manifest, [row for row in aliases if row["namespace"] != "MLBAM"]
        )
    with pytest.raises(validator.IdentityBridgeValidationError, match="Retrosheet"):
        validator._validate_parent_population(
            parent,
            [row for row in aliases if row["namespace"] != "RETROSHEET"],
            dimensions,
        )


def test_every_player_prior_lahman_alias_must_target_the_same_uuid(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    _bridge_root, _parent_root, parent = valid_staging_package
    aliases = copy.deepcopy(_normalized_alias_rows())
    lahman = next(row for row in aliases if row["namespace"] == "LAHMAN")
    lahman["source_player_id"] = "wrong0001"
    lahman["parent_join_key"] = "wrong0001"
    lahman["row_sha256"] = "0" * 64
    lahman = apply_row_hash(lahman)
    aliases = [
        lahman if row["namespace"] == "LAHMAN" else row for row in aliases
    ]
    with pytest.raises(validator.IdentityBridgeValidationError, match="Lahman"):
        validator._validate_parent_population(parent, aliases, _dimension_rows())


def test_governed_evidence_artifacts_are_exact_and_ordered(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    _bridge_root, parent_root, parent = valid_staging_package
    changed = parent.parent_root.parent / "changed-evidence-artifacts"

    def mutate(payload: dict[str, Any]) -> None:
        payload["source_lineage"]["evidence_artifacts"][1]["byte_count"] += 1

    _build_package(changed, parent, manifest_mutator=mutate)
    with pytest.raises(
        validator.IdentityBridgeValidationError, match="governed-evidence artifacts"
    ):
        validator.validate_identity_bridge(
            changed, parent_root, require_auxiliary=False
        )


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("extraction_revision", "extraction revision"),
        ("publishing_code_revision", "publishing revision"),
    ),
)
def test_implementation_revisions_are_recomputed_from_project_code(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
    field: str,
    message: str,
) -> None:
    _bridge_root, parent_root, parent = valid_staging_package
    changed = parent.parent_root.parent / f"changed-{field}"

    def mutate(payload: dict[str, Any]) -> None:
        payload["source_lineage"][field] = _sha("a")

    _build_package(changed, parent, manifest_mutator=mutate)
    with pytest.raises(validator.IdentityBridgeValidationError, match=message):
        validator.validate_identity_bridge(
            changed, parent_root, require_auxiliary=False
        )


def test_manifest_rights_control_ids_are_exact_parent_projection(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    _bridge_root, parent_root, parent = valid_staging_package
    changed = parent.parent_root.parent / "changed-rights-controls"

    def mutate(payload: dict[str, Any]) -> None:
        payload["source_rights"]["rights_control_ids"][-1] = "ZZZ_TEST_CONTROL"

    _build_package(changed, parent, manifest_mutator=mutate)
    with pytest.raises(validator.IdentityBridgeValidationError, match="rights-control"):
        validator.validate_identity_bridge(
            changed, parent_root, require_auxiliary=False
        )


def test_manifest_limitations_are_exact_v1_policy(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    _bridge_root, parent_root, parent = valid_staging_package
    changed = parent.parent_root.parent / "changed-limitations"

    def mutate(payload: dict[str, Any]) -> None:
        payload["limitations"][3]["description"] += " Altered."

    _build_package(changed, parent, manifest_mutator=mutate)
    with pytest.raises(validator.IdentityBridgeValidationError, match="limitations"):
        validator.validate_identity_bridge(
            changed, parent_root, require_auxiliary=False
        )


def test_alias_source_releases_are_independently_frozen(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    bridge_root, parent_root, parent = valid_staging_package
    changed = parent.parent_root.parent / "changed-release"

    def mutate(payload: dict[str, Any]) -> None:
        payload["source_lineage"]["source_releases"][0]["identity_checksum"] = _sha(
            "9"
        )

    _build_package(changed, parent, manifest_mutator=mutate)
    with pytest.raises(validator.IdentityBridgeValidationError, match="source-release"):
        validator.validate_identity_bridge(
            changed, parent_root, require_auxiliary=False
        )
    assert bridge_root.exists()


def test_y_minus_1_is_recomputed_not_trusted_from_manifest(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    _bridge_root, parent_root, parent = valid_staging_package
    changed = parent.parent_root.parent / "changed-y-minus-1"

    def mutate(payload: dict[str, Any]) -> None:
        row = payload["y_minus_1_prior_eligibility"][0]
        row["rows_with_eligible_prior"] = 0
        row["legitimate_missing_prior_rows"] = row["reference_rows"]
        row["eligible_prior_rate"] = 0.0

    _build_package(changed, parent, manifest_mutator=mutate)
    with pytest.raises(validator.IdentityBridgeValidationError, match="Y-minus-1"):
        validator.validate_identity_bridge(
            changed, parent_root, require_auxiliary=False
        )


def test_rights_bytes_are_exact_even_when_manifest_declares_tamper(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    _bridge_root, parent_root, parent = valid_staging_package
    changed = parent.parent_root.parent / "changed-rights"
    tampered = rights.attribution_bytes(parent_root) + b"Additional grant.\n"
    _build_package(
        changed,
        parent,
        semantic_overrides={"attribution.md": tampered},
    )
    with pytest.raises(validator.IdentityBridgeValidationError, match="rights/attribution"):
        validator.validate_identity_bridge(
            changed, parent_root, require_auxiliary=False
        )


def test_portable_security_scan_rejects_secret_marker_in_canonical_manifest(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    _bridge_root, parent_root, parent = valid_staging_package
    changed = parent.parent_root.parent / "changed-security"

    def mutate(payload: dict[str, Any]) -> None:
        payload["limitations"][0]["description"] = (
            "DATABASE_URL=postgresql://user:password@localhost/database"
        )

    _build_package(changed, parent, manifest_mutator=mutate)
    with pytest.raises(validator.IdentityBridgeValidationError, match="security scan"):
        validator.validate_identity_bridge(
            changed, parent_root, require_auxiliary=False
        )


def test_portable_security_scan_rejects_physical_asp_runtime_path(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    _bridge_root, parent_root, parent = valid_staging_package
    changed = parent.parent_root.parent / "changed-runtime-path"

    def mutate(payload: dict[str, Any]) -> None:
        payload["limitations"][0]["description"] = (
            r"C:\Users\james\ai-sports-predictor\.runtime\security.db"
        )

    _build_package(changed, parent, manifest_mutator=mutate)
    with pytest.raises(validator.IdentityBridgeValidationError, match="security scan"):
        validator.validate_identity_bridge(
            changed, parent_root, require_auxiliary=False
        )


def test_security_scan_uses_decoded_rows_for_binary_parquet(
    artifact_root: Path,
) -> None:
    parquet = artifact_root / "rows.parquet"
    parquet.write_bytes(b"PAR1" + b"\\\\opaque\\binary" + b"PAR1")
    inventory = (("rows.parquet", parquet),)

    security.validate_package_security(
        artifact_root,
        inventory,
        row_payloads=({"source_player_id": "aardd001"},),
    )
    with pytest.raises(security.IdentityBridgeSecurityError):
        security.validate_package_security(
            artifact_root,
            inventory,
            row_payloads=(
                {
                    "source_player_id": (
                        r"C:\Users\james\ai-sports-predictor\.runtime\security.db"
                    )
                },
            ),
        )


def test_validation_report_and_readme_bytes_must_be_exact(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    bridge_root, parent_root, _parent = valid_staging_package
    staging = validator.validate_identity_bridge(
        bridge_root, parent_root, require_auxiliary=False
    )
    _add_auxiliary(bridge_root, staging)
    (bridge_root / "validation-report.json").write_bytes(
        staging.report_bytes + b" "
    )
    with pytest.raises(validator.IdentityBridgeValidationError, match="report bytes"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=True
        )

    (bridge_root / "validation-report.json").write_bytes(staging.report_bytes)
    (bridge_root / "README.md").write_bytes(b"not deterministic\n")
    with pytest.raises(validator.IdentityBridgeValidationError, match="README"):
        validator.validate_identity_bridge(
            bridge_root, parent_root, require_auxiliary=True
        )


def test_deadline_is_fail_closed_before_parent_or_package_reads(
    valid_staging_package: tuple[Path, Path, ParentIdentityEvidence],
) -> None:
    bridge_root, parent_root, _parent = valid_staging_package

    class Clock:
        def __init__(self) -> None:
            self.value = -61.0

        def __call__(self) -> float:
            self.value += 61.0
            return self.value

    with pytest.raises(validator.IdentityBridgeValidationDeadline) as captured:
        validator.validate_identity_bridge(
            bridge_root,
            parent_root,
            require_auxiliary=False,
            max_runtime_minutes=1,
            clock=Clock(),
        )
    assert captured.value.progress["stage"] == "INVENTORY"


@pytest.mark.parametrize("interval_name", ["max_runtime_minutes", "heartbeat_seconds"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_validation_intervals_are_rejected_before_package_reads(
    monkeypatch: pytest.MonkeyPatch,
    interval_name: str,
    value: float,
) -> None:
    def forbid_package_read(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("package root must not be inspected")

    monkeypatch.setattr(validator, "governed_root", forbid_package_read)
    arguments: dict[str, Any] = {
        "heartbeat_seconds": 1,
        "max_runtime_minutes": 1,
    }
    arguments[interval_name] = value
    with pytest.raises(ValueError):
        validator.validate_identity_bridge(
            Path("fixture-bridge"),
            Path("fixture-parent"),
            require_auxiliary=False,
            **arguments,
        )


def test_publishing_revision_attests_semantic_trust_but_not_release_self_anchors(
    artifact_root: Path,
) -> None:
    synthetic_root = artifact_root / "revision-project"
    bridge_code = (
        synthetic_root / "server" / "app" / "gen2" / "exchange" / "identity_bridge"
    )
    bridge_code.mkdir(parents=True)
    (bridge_code / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    semantic_trust = bridge_code / "trust.py"
    release_trust = bridge_code / "release_trust.py"
    semantic_trust.write_text("SEMANTIC = 1\n", encoding="utf-8")
    release_trust.write_text("RELEASE = None\n", encoding="utf-8")
    publisher_code = bridge_code.parent / "identity_bridge_publisher"
    publisher_code.mkdir()
    same_named_non_anchor = publisher_code / "release_trust.py"
    same_named_non_anchor.write_text("ATTESTED = 1\n", encoding="utf-8")

    initial = publishing_code_revision(synthetic_root)
    release_trust.write_text("RELEASE = 'frozen'\n", encoding="utf-8")
    assert publishing_code_revision(synthetic_root) == initial

    same_named_non_anchor.write_text("ATTESTED = 2\n", encoding="utf-8")
    assert publishing_code_revision(synthetic_root) != initial
    same_named_non_anchor.write_text("ATTESTED = 1\n", encoding="utf-8")

    semantic_trust.write_text("SEMANTIC = 2\n", encoding="utf-8")
    assert publishing_code_revision(synthetic_root) != initial


def test_validator_import_graph_has_no_database_builder_or_runtime_authority() -> None:
    source_text = Path(validator.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden = {
        "app.db",
        "app.models",
        "app.gen2.exchange.evidence",
        "app.gen2.exchange.identity_bridge.builder",
        "app.gen2.exchange.identity_bridge.source",
    }
    assert not any(
        candidate == blocked or candidate.startswith(f"{blocked}.")
        for candidate in imports
        for blocked in forbidden
    )
    assert ".runtime" not in source_text


def test_cli_returns_deadline_and_invalid_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def deadline(*_args: Any, **_kwargs: Any) -> Any:
        raise validator.IdentityBridgeValidationDeadline({"stage": "TEST"})

    monkeypatch.setattr(validator, "validate_identity_bridge", deadline)
    assert validator.main(
        [
            "--bridge-package-root",
            "bridge",
            "--parent-package-root",
            "parent",
        ]
    ) == 75
    assert '"status":"DEADLINE"' in capsys.readouterr().err

    def invalid(*_args: Any, **_kwargs: Any) -> Any:
        raise validator.IdentityBridgeValidationError("invalid package")

    monkeypatch.setattr(validator, "validate_identity_bridge", invalid)
    assert validator.main(
        [
            "--bridge-package-root",
            "bridge",
            "--parent-package-root",
            "parent",
        ]
    ) == 2
    assert '"status":"INVALID"' in capsys.readouterr().err
