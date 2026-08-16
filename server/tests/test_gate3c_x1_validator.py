from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

import app.gen2.exchange.contracts as contracts
import app.gen2.exchange.validator as exchange_validator
from app.gen2.exchange.canonical_json import (
    apply_manifest_identity,
    canonical_json_bytes,
    canonical_sha256,
)
from app.gen2.exchange.contracts import (
    CANONICAL_PA_CLASS_ORDER,
    Predicate,
    REQUIRED_LIMITATION_IDS,
    SportsDatasetManifestV1,
)
from app.gen2.exchange.package_metadata import normalized_markdown_bytes
from app.gen2.exchange.schema_registry import expected_field_contract
from app.gen2.exchange.source_rights import (
    build_attribution_markdown,
    build_source_rights,
)
from app.gen2.exchange.validator import (
    ExchangeValidationError,
    validate_sports_dataset_exchange,
)


FAMILIES = (
    "bullpen_usage",
    "pitch_sequence",
    "plate_appearance",
    "play_transition",
    "player_game_batting",
    "player_game_fielding",
    "player_game_pitching",
    "player_prior",
    "starter_usage",
    "weather",
)
SMALL_POPULATIONS = {
    "strict_deployable": 4,
    "enhanced_research": 3,
    "prior_only": 3,
}


def _artifact(logical_uri: str, payload: bytes) -> dict[str, object]:
    return {
        "logical_uri": logical_uri,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_count": len(payload),
        "semantic_digest_member": True,
    }


def _source_identity(artifact_id: str) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "logical_uri": f"source-evidence/{artifact_id}/evidence.json",
        "sha256": hashlib.sha256(artifact_id.encode("utf-8")).hexdigest(),
        "byte_count": 1,
    }


def _patch_small_contract(
    monkeypatch: pytest.MonkeyPatch, *, total_bytes: int
) -> None:
    for module in (contracts, exchange_validator):
        monkeypatch.setattr(module, "EXPECTED_FILE_COUNT", len(FAMILIES))
        monkeypatch.setattr(module, "EXPECTED_ROW_COUNT", len(FAMILIES))
        monkeypatch.setattr(module, "EXPECTED_PARQUET_BYTES", total_bytes)
        monkeypatch.setattr(module, "EXPECTED_POPULATIONS", SMALL_POPULATIONS)
    monkeypatch.setattr(contracts, "EXPECTED_FINALIZED_RUN_ID", "small-fixture")
    monkeypatch.setattr(contracts, "EXPECTED_SOURCE_SEMANTIC_CHECKSUM", "a" * 64)
    monkeypatch.setattr(contracts, "EXPECTED_DATABASE_MIGRATION", "fixture-migration")
    monkeypatch.setattr(contracts, "EXPECTED_GENERATION_REVISION", "b" * 64)
    monkeypatch.setattr(contracts, "EXPECTED_FINALIZER_REVISION", "c" * 64)
    monkeypatch.setattr(
        contracts, "EXPECTED_PUBLISHER", "AI Sports Predictor test fixture"
    )
    monkeypatch.setattr(
        contracts, "EXPECTED_DEVELOPMENT_PURPOSE", "Package-only validator testing."
    )
    monkeypatch.setattr(
        contracts,
        "EXPECTED_READINESS_INVENTORY_BINDING_NOTE",
        "Fixed fixture evidence only.",
    )
    source_artifacts = []
    for field_name, artifact_id in (
        ("finalized_evidence", "finalized_evidence"),
        ("readiness_evidence", "readiness_evidence"),
        ("active_identity_evidence", "active_identity_evidence"),
        ("quarantine_impact_evidence", "quarantine_impact_evidence"),
    ):
        artifact = _source_identity(artifact_id)
        source_artifacts.append(
            (
                field_name,
                artifact["artifact_id"],
                artifact["logical_uri"],
                artifact["sha256"],
                artifact["byte_count"],
            )
        )
    monkeypatch.setattr(
        contracts, "EXPECTED_SOURCE_ARTIFACTS", tuple(source_artifacts)
    )
    monkeypatch.setattr(contracts, "EXPECTED_READINESS_OPERATOR_REVISION", "f" * 64)
    monkeypatch.setattr(
        contracts,
        "EXPECTED_SOURCE_RELEASES",
        (
            (
                "RETROSHEET_CSV_DOWNLOADS",
                "fixture-source-v1",
                "fixture",
                "e" * 64,
            ),
        ),
    )
    monkeypatch.setattr(
        contracts,
        "EXPECTED_FAMILY_AUTHORITIES",
        tuple(
            (
                family,
                f"small_{family}_v1",
                "RETROSHEET_CSV_DOWNLOADS",
                "fixture-source-v1",
            )
            for family in FAMILIES
        ),
    )
    monkeypatch.setattr(
        contracts,
        "EXPECTED_FAMILY_SEMANTICS",
        tuple(
            (
                family,
                "Small package-only validator fixture.",
                ("CONTEXT",),
                ("FIXTURE_ONLY",),
                tuple(SMALL_POPULATIONS),
                (),
            )
            for family in FAMILIES
        ),
    )
    monkeypatch.setattr(
        contracts,
        "EXPECTED_RIGHTS_BY_PARTITION_SOURCE",
        (("RETROSHEET_CSV_DOWNLOADS", ("RETROSHEET",)),),
    )
    monkeypatch.setattr(
        contracts,
        "EXPECTED_TARGET_DEFINITIONS",
        (("plate_appearance_outcome", ("plate_appearance",), "TARGET"),),
    )
    monkeypatch.setattr(
        contracts,
        "EXPECTED_TARGET_NARRATIVES",
        (
            (
                "plate_appearance_outcome",
                "Canonical eight-class fixture target.",
                (),
            ),
        ),
    )
    monkeypatch.setattr(contracts, "EXPECTED_PA_TAXONOMY_VERSION", "fixture-v1")
    monkeypatch.setattr(
        contracts, "EXPECTED_PA_TAXONOMY_IDENTITY", "fixture-taxonomy"
    )
    monkeypatch.setattr(
        contracts,
        "EXPECTED_FOLDS",
        (
            (
                "small-fold",
                (2023,),
                2024,
                "2023-12-31",
                "2024-01-01",
                "2024-12-31",
                1,
                1,
            ),
        ),
    )
    monkeypatch.setattr(
        contracts,
        "EXPECTED_TEMPORAL_SEASON_ROLES",
        (("2025", "PRIMARY_GEN2_MODELING"),),
    )
    temporal = _temporal_contract()
    monkeypatch.setattr(
        contracts,
        "EXPECTED_TEMPORAL_NARRATIVES",
        tuple(
            (name, temporal[name])
            for name in (
                "player_prior_rule",
                "strict_timing_risk_separation",
                "same_date_game_ordering",
                "suspended_game_handling",
                "doubleheader_handling",
                "fold_compatibility",
            )
        ),
    )
    season_rows = [_season(season) for season in range(2021, 2026)]
    monkeypatch.setattr(
        contracts,
        "EXPECTED_SEASON_COVERAGE",
        tuple(
            (
                row["season"],
                row["role"],
                row["games"],
                row["plate_appearances"],
                row["transitions"],
                row["starter_targets"],
                row["bullpen_appearances"],
                row["weather_coverage"]["covered"],
                row["weather_coverage"]["eligible"],
                row["complete_lineup_coverage"]["covered"],
                row["complete_lineup_coverage"]["eligible"],
                row["date_range"]["minimum"],
                row["date_range"]["maximum"],
            )
            for row in season_rows
        ),
    )
    identity = _identity_coverage()
    monkeypatch.setattr(
        contracts,
        "EXPECTED_IDENTITY_COVERAGE",
        tuple(
            (
                field_name,
                identity[field_name]["label"],
                tuple(identity[field_name]["seasons"]),
                identity[field_name]["unique_players"],
                identity[field_name]["reference_count"],
                identity[field_name]["exact_reference_rate"],
                identity[field_name]["exact_unique_rate"],
                identity[field_name]["unresolved_references"],
                identity[field_name]["ambiguous_references"],
            )
            for field_name in ("all_2021_2025", "primary_2023_2025")
        ),
    )
    monkeypatch.setattr(
        contracts,
        "EXPECTED_IDENTITY_TOTALS",
        (
            ("quarantine_rows", 0),
            ("unresolved_quarantine_rows", 0),
            ("ambiguous_quarantine_rows", 0),
            ("downstream_impacted_rows", 0),
            ("canonical_identity_mapping_count", 1),
            ("identity_source_versions", ("fixture-v1",)),
            ("derivation_checksum", "d" * 64),
        ),
    )
    expected_populations = (
        _population("prior_only", 3, FAMILIES[7:], 1),
        _population("enhanced_research", 3, FAMILIES[4:7], 2),
        _population("strict_deployable", 4, FAMILIES[:4], 3),
    )
    population_keys = (
        "population_id",
        "expected_row_count",
        "allowed_uses",
        "prohibited_uses",
        "season_scope",
        "predicate_contract_version",
        "readiness_operator_revision",
        "evaluation_precedence",
        "row_selection",
    )
    monkeypatch.setattr(
        contracts,
        "expected_population_contracts",
        lambda: tuple(
            {key: row[key] for key in population_keys}
            for row in expected_populations
        ),
    )
    monkeypatch.setattr(
        contracts,
        "EXPECTED_POPULATION_NARRATIVES",
        tuple(
            (
                row["population_id"],
                row["description"],
                row["temporal_meaning"],
            )
            for row in expected_populations
        ),
    )
    monkeypatch.setattr(
        contracts,
        "EXPECTED_LIMITATIONS",
        tuple(
            (
                limitation_id,
                f"Fixture preserves {limitation_id}.",
                "FIXTURE",
            )
            for limitation_id in REQUIRED_LIMITATION_IDS
        ),
    )
    monkeypatch.setattr(
        exchange_validator,
        "EXPECTED_LOGICAL_SCHEMAS",
        {family: f"small_{family}_v1" for family in FAMILIES},
    )
    monkeypatch.setattr(
        exchange_validator, "EXPECTED_PHYSICAL_VARIANT_COUNT", len(FAMILIES)
    )


def _season(season: int) -> dict[str, object]:
    return {
        "season": season,
        "role": "PRIOR_ONLY_HISTORY" if season < 2023 else "PRIMARY_GEN2_MODELING",
        "games": 1,
        "plate_appearances": 1,
        "transitions": 1,
        "starter_targets": 1,
        "bullpen_appearances": 1,
        "weather_coverage": {"covered": 1, "eligible": 1, "ratio": 1.0},
        "complete_lineup_coverage": {
            "covered": 1,
            "eligible": 1,
            "ratio": 1.0,
        },
        "date_range": {
            "minimum": f"{season}-01-01",
            "maximum": f"{season}-12-31",
        },
    }


def _population(
    population_id: str, count: int, families: tuple[str, ...], precedence: int
) -> dict[str, object]:
    return {
        "population_id": population_id,
        "description": f"Small validator fixture population: {population_id}",
        "expected_row_count": count,
        "allowed_uses": ["TEST_VALIDATION"],
        "prohibited_uses": ["PRODUCTION"],
        "season_scope": [2025],
        "temporal_meaning": "Fixture-only partition-family split.",
        "predicate_contract_version": "small-package-v1",
        "readiness_operator_revision": "f" * 64,
        "evaluation_precedence": precedence,
        "row_selection": {
            "op": "in",
            "field": "partition.family",
            "values": list(families),
        },
    }


def _identity_coverage() -> dict[str, object]:
    identity_slice = {
        "label": "fixture",
        "seasons": [2025],
        "unique_players": 1,
        "reference_count": 1,
        "exact_reference_rate": 1.0,
        "exact_unique_rate": 1.0,
        "unresolved_references": 0,
        "ambiguous_references": 0,
    }
    return {
        "all_2021_2025": identity_slice,
        "primary_2023_2025": identity_slice,
        "quarantine_rows": 0,
        "unresolved_quarantine_rows": 0,
        "ambiguous_quarantine_rows": 0,
        "downstream_impacted_rows": 0,
        "canonical_identity_mapping_count": 1,
        "identity_source_versions": ["fixture-v1"],
        "derivation_checksum": "d" * 64,
    }


def _temporal_contract() -> dict[str, object]:
    return {
        "season_roles": {"2025": "PRIMARY_GEN2_MODELING"},
        "player_prior_rule": "Only prior-season evidence is eligible.",
        "no_current_game_evidence": True,
        "no_current_season_future_evidence": True,
        "strict_timing_risk_separation": "Fixture preserves strict separation.",
        "same_date_game_ordering": "Doubleheaders use source order.",
        "suspended_game_handling": "Official completion date is used.",
        "doubleheader_handling": "Games remain independently identified.",
        "fold_compatibility": "Chronological only.",
        "folds": [
            {
                "fold_id": "small-fold",
                "training_seasons": [2023],
                "validation_season": 2024,
                "training_end": "2023-12-31",
                "validation_start": "2024-01-01",
                "validation_end": "2024-12-31",
                "training_rows": 1,
                "validation_rows": 1,
            }
        ],
        "untouched_final_holdout_claimed": False,
    }


def _write_small_package(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, list[Path]]:
    package = root / "small-package"
    package.mkdir()
    partitions: list[dict[str, object]] = []
    family_contracts: list[dict[str, object]] = []
    registry_families: list[dict[str, object]] = []
    parquet_paths: list[Path] = []

    for index, family in enumerate(FAMILIES):
        build_path = package / f".build-{index:02d}.parquet"
        date_field = "official_date" if family == "player_prior" else "game_date"
        pl.DataFrame(
            {date_field: [f"2025-01-{index + 1:02d}"], "fixture_value": [index]}
        ).write_parquet(build_path, compression="zstd", statistics=True)
        schema = pl.read_parquet_schema(build_path)
        fields = [{"name": name, "dtype": str(dtype)} for name, dtype in schema.items()]
        registry_fields = [
            expected_field_contract(
                family=family, name=field["name"], dtype=field["dtype"]
            )
            for field in fields
        ]
        fingerprint = canonical_sha256(fields)
        checksum = hashlib.sha256(build_path.read_bytes()).hexdigest()
        size = build_path.stat().st_size
        date = f"2025-01-{index + 1:02d}"
        logical_uri = (
            f"parquet/{family}/season=2025/month=01/part-{checksum}.parquet"
        )
        path = package.joinpath(*logical_uri.split("/"))
        path.parent.mkdir(parents=True)
        build_path.replace(path)
        parquet_paths.append(path)
        checkpoint = f"fixture-checkpoint-{index:02d}"
        governance = hashlib.sha256(family.encode("utf-8")).hexdigest()
        schema_id = f"small_{family}_v1"
        source_identity = (
            f"historical-events/source=retrosheet_csv_downloads/family={family}/"
            f"season=2025/month=01/schema={schema_id}/"
            f"source-version=fixture-source-v1/checkpoint={checkpoint}/"
            f"governance={governance}/part-{checksum}.parquet"
        )
        partition_id = canonical_sha256(
            {
                "checkpoint_id": checkpoint,
                "declared_schema": schema_id,
                "family": family,
                "governance_checksum": governance,
                "month": 1,
                "season": 2025,
                "source_key": "retrosheet_csv_downloads",
                "source_version_id": "fixture-source-v1",
            }
        )
        partitions.append(
            {
                "partition_id": partition_id,
                "family": family,
                "season": 2025,
                "month": 1,
                "package_relative_uri": logical_uri,
                "original_source_logical_identity": source_identity,
                "row_count": 1,
                "byte_count": size,
                "minimum_date": date,
                "maximum_date": date,
                "file_sha256": checksum,
                "schema_id": schema_id,
                "physical_schema_fingerprint": fingerprint,
                "physical_schema": fields,
                "source_key": "RETROSHEET_CSV_DOWNLOADS",
                "source_version_id": "fixture-source-v1",
                "checkpoint_lineage_id": checkpoint,
                "governance_checksum": governance,
                "temporal_classification": ["FIXTURE_ONLY"],
                "target_eligibility": (
                    ["plate_appearance_outcome"]
                    if family == "plate_appearance"
                    else []
                ),
                "source_rights_ids": ["RETROSHEET"],
            }
        )
        family_contracts.append(
            {
                "family_id": family,
                "schema_id": f"small_{family}_v1",
                "purpose": "Small package-only validator fixture.",
                "target_feature_role": ["CONTEXT"],
                "temporal_classification": ["FIXTURE_ONLY"],
                "population_eligibility": list(SMALL_POPULATIONS),
                "file_count": 1,
                "row_count": 1,
                "byte_count": size,
                "date_range": {"minimum": date, "maximum": date},
                "source_key": "RETROSHEET_CSV_DOWNLOADS",
                "source_version_id": "fixture-source-v1",
                "rights_control_ids": ["RETROSHEET"],
                "known_limitations": [],
            }
        )
        registry_families.append(
            {
                "family": family,
                "logical_schema_version": f"small_{family}_v1",
                "partition_count": 1,
                "physical_variant_count": 1,
                "physical_variants": [
                    {
                        "physical_schema_version": (
                            f"small_{family}_v1+physical-sha256-{fingerprint}"
                        ),
                        "physical_schema_fingerprint": fingerprint,
                        "compatible_partition_count": 1,
                        "fields": registry_fields,
                    }
                ],
            }
        )

    total_bytes = sum(path.stat().st_size for path in parquet_paths)
    _patch_small_contract(monkeypatch, total_bytes=total_bytes)
    authority_projection = [
        {
            field: partition[field]
            for field in contracts.PARTITION_AUTHORITY_FIELDS
        }
        for partition in sorted(
            partitions, key=lambda row: str(row["package_relative_uri"])
        )
    ]
    monkeypatch.setattr(
        contracts,
        "EXPECTED_PARTITION_AUTHORITY_SHA256",
        canonical_sha256(authority_projection),
    )
    schema_registry = {
        "schema_name": "SPORTS_DATASET_SCHEMA_REGISTRY_V1",
        "schema_version": "sports_dataset_schema_registry_v1",
        "nullability_policy": "CONSERVATIVE_ALL_PARQUET_FIELDS_NULLABLE",
        "family_count": 10,
        "physical_variant_count": 10,
        "partition_count": len(FAMILIES),
        "families": registry_families,
    }
    rights = build_source_rights()
    schema_bytes = canonical_json_bytes(schema_registry, trailing_newline=True)
    rights_bytes = canonical_json_bytes(rights, trailing_newline=True)
    attribution_bytes = normalized_markdown_bytes(build_attribution_markdown())
    (package / "schema-registry.json").write_bytes(schema_bytes)
    (package / "source-rights.json").write_bytes(rights_bytes)
    (package / "attribution.md").write_bytes(attribution_bytes)

    rights_ids = [row["rights_control_id"] for row in rights["records"]]
    manifest_payload: dict[str, object] = {
        "contract_name": "SPORTS_DATASET_MANIFEST",
        "contract_version": "SPORTS_DATASET_MANIFEST_V1",
        "canonicalization_version": "SPORTS_CANONICAL_JSON_V1",
        "supported_consumer_range": ">=1.0.0,<2.0.0",
        "manifest_id": "pending",
        "manifest_sha256": "0" * 64,
        "dataset_id": "asp.mlb.open-data.fd1",
        "dataset_version_id": "pending",
        "package_id": "pending",
        "publisher": "AI Sports Predictor test fixture",
        "published_at": "2026-08-15T00:00:00Z",
        "sport": "Baseball",
        "league": "MLB",
        "development_purpose": "Package-only validator testing.",
        "evidence_cutoff": "2026-08-15",
        "source_lineage": {
            "finalized_fd1_recovery_run_id": "small-fixture",
            "source_semantic_manifest_checksum": "a" * 64,
            "finalized_evidence": _source_identity("finalized_evidence"),
            "readiness_evidence": _source_identity("readiness_evidence"),
            "active_identity_evidence": _source_identity("active_identity_evidence"),
            "quarantine_impact_evidence": _source_identity(
                "quarantine_impact_evidence"
            ),
            "generation_revision": "b" * 64,
            "finalizer_revision": "c" * 64,
            "publishing_code_revision": "d" * 64,
            "database_migration": "fixture-migration",
            "source_releases": [
                {
                    "source_key": "RETROSHEET_CSV_DOWNLOADS",
                    "source_version_id": "fixture-source-v1",
                    "dataset_version": "fixture",
                    "identity_checksum": "e" * 64,
                }
            ],
            "readiness_inventory_binding_note": "Fixed fixture evidence only.",
        },
        "seasons": [_season(season) for season in range(2021, 2026)],
        "populations": [
            _population("prior_only", 3, FAMILIES[7:], 1),
            _population("enhanced_research", 3, FAMILIES[4:7], 2),
            _population("strict_deployable", 4, FAMILIES[:4], 3),
        ],
        "data_families": family_contracts,
        "partitions": partitions,
        "identity_coverage": _identity_coverage(),
        "target_definitions": [
            {
                "target_id": "plate_appearance_outcome",
                "family_ids": ["plate_appearance"],
                "role": "TARGET",
                "description": "Canonical eight-class fixture target.",
                "ordered_classes": list(CANONICAL_PA_CLASS_ORDER),
                "taxonomy_version": "fixture-v1",
                "taxonomy_identity": "fixture-taxonomy",
                "limitations": [],
            }
        ],
        "temporal_contract": _temporal_contract(),
        "data_quality_evidence": {
            "eight_class_taxonomy_complete": True,
            "unsafe_terminal_events": 0,
            "transition_conservation_passed": True,
            "score_run_runner_conservation_passed": True,
            "identity_coverage_passed": True,
            "tier_leaks": 0,
            "temporal_leaks": 0,
            "unique_partitions": True,
            "schema_validation_passed": True,
            "quarantine_downstream_impact": 0,
        },
        "known_limitations": [
            {
                "limitation_id": limitation_id,
                "description": f"Fixture preserves {limitation_id}.",
                "scope": "FIXTURE",
            }
            for limitation_id in REQUIRED_LIMITATION_IDS
        ],
        "source_rights_ids": rights_ids,
        "package_artifacts": [
            _artifact("attribution.md", attribution_bytes),
            _artifact("schema-registry.json", schema_bytes),
            _artifact("source-rights.json", rights_bytes),
        ],
        "totals": {
            "parquet_bytes": total_bytes,
            "parquet_files": len(FAMILIES),
            "rows": len(FAMILIES),
        },
    }
    # Derive identity from the contract-normalized representation. Pydantic
    # converts predicate lists to tuples internally, so normalizing once before
    # the semantic digest mirrors the bytes a consumer will load.
    normalized = SportsDatasetManifestV1.model_validate(
        apply_manifest_identity(manifest_payload)
    ).model_dump(mode="json")
    manifest = SportsDatasetManifestV1.model_validate(
        apply_manifest_identity(normalized)
    )
    (package / "sports-dataset-manifest.json").write_bytes(
        canonical_json_bytes(manifest.model_dump(mode="json"), trailing_newline=True)
    )
    return package, parquet_paths


def test_package_only_validator_accepts_a_patched_small_exchange(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, _paths = _write_small_package(artifact_root, monkeypatch)

    result = validate_sports_dataset_exchange(
        package,
        heartbeat_seconds=1,
        max_runtime_minutes=1,
        require_auxiliary_files=False,
    )

    assert result.report["status"] == "VALID"
    assert result.report["parquet_files_checked"] == 10
    assert result.report["population_counts"] == SMALL_POPULATIONS
    assert result.report["database_accessed"] is False
    assert result.report["source_runtime_accessed"] is False
    assert result.report["provider_network_calls"] == 0


def test_validator_cli_reports_deadline_as_resumable_stop(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    progress = {"stage": "VALIDATING_PARTITIONS", "files_checked": 7}

    def stopped(*_args: object, **_kwargs: object) -> object:
        raise exchange_validator.ExchangeValidationDeadline(progress)

    monkeypatch.setattr(
        exchange_validator, "validate_sports_dataset_exchange", stopped
    )
    with pytest.raises(SystemExit) as exit_info:
        exchange_validator.main(
            ["--package-root", str(artifact_root), "--max-runtime-minutes", "0.5"]
        )

    assert exit_info.value.code == 75
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "STOPPED",
        "reason": "MAX_RUNTIME_REACHED",
        "resume_point": progress,
        "provider_network_calls": 0,
        "database_accessed": False,
    }


def test_predicate_null_leaf_is_false_before_logical_not() -> None:
    predicate = Predicate.model_validate_json(
        '{"op":"not","arg":{"op":"eq","field":"row.flag","value":1}}'
    )
    expression = exchange_validator._compile_predicate(
        predicate,
        {"flag": pl.Int64},
        "player_game_pitching",
        2023,
    )
    values = (
        pl.DataFrame({"flag": [None, 1, 0]}, schema={"flag": pl.Int64})
        .select(expression.alias("selected"))
        .get_column("selected")
        .to_list()
    )
    assert values == [True, False, True]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "file inventory differs"),
        ("extra", "file inventory differs"),
        ("tampered", "Parquet checksum changed"),
    ],
)
def test_package_only_validator_rejects_missing_extra_and_tampered_files(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    package, paths = _write_small_package(artifact_root, monkeypatch)
    if mutation == "missing":
        paths[0].unlink()
    elif mutation == "extra":
        (package / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    else:
        payload = bytearray(paths[0].read_bytes())
        payload[len(payload) // 2] ^= 0xFF
        paths[0].write_bytes(payload)

    with pytest.raises(ExchangeValidationError, match=message):
        validate_sports_dataset_exchange(
            package,
            heartbeat_seconds=1,
            max_runtime_minutes=1,
            require_auxiliary_files=False,
        )
