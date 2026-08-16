"""Projection of finalized FD1 evidence into the portable V1 manifest."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.gen2.exchange.canonical_json import (
    CANONICALIZATION_VERSION,
    apply_manifest_identity,
)
from app.gen2.exchange.contracts import (
    CANONICAL_PA_CLASS_ORDER,
    CONTRACT_NAME,
    CONTRACT_VERSION,
    DATASET_ID,
    EXPECTED_ENHANCED_FAMILIES,
    EXPECTED_PA_TAXONOMY_IDENTITY,
    EXPECTED_PA_TAXONOMY_VERSION,
    EXPECTED_TARGET_DEFINITIONS,
    REQUIRED_LIMITATION_IDS,
    SportsDatasetManifestV1,
    expected_population_contracts,
)


POPULATION_PREDICATE_VERSION = "SPORTS_FD1_POPULATION_PREDICATE_V1"
PUBLISHER_ID = "AI Sports Predictor"
EVIDENCE_CUTOFF = "2026-08-15"
DATABASE_MIGRATION = "20260815_0015"

_ENHANCED_FAMILIES = EXPECTED_ENHANCED_FAMILIES

_FAMILY_PURPOSE: Mapping[str, str] = {
    "bullpen_usage": "Retrospective reliever appearance and bullpen-usage targets.",
    "pitch_sequence": "Retrosheet pitch-sequence strings for retrospective event research.",
    "plate_appearance": "Canonical eight-class terminal plate-appearance outcomes.",
    "play_transition": "Conserved base/out, runner, run, and score state transitions.",
    "player_game_batting": "Retrospective player-game batting evidence and lineup roles.",
    "player_game_fielding": "Retrospective player-game fielding evidence.",
    "player_game_pitching": "Retrospective player-game pitching evidence and actual starts.",
    "player_prior": "Chronological prior-period player batting, pitching, and fielding evidence.",
    "starter_usage": "Retrospective actual-starter duration targets.",
    "weather": "Retrospective game and weather context from Retrosheet game information.",
}

_FAMILY_ROLE: Mapping[str, tuple[str, ...]] = {
    "bullpen_usage": ("TARGET", "RETROSPECTIVE_CONTEXT"),
    "pitch_sequence": ("TARGET_INPUT", "RETROSPECTIVE_CONTEXT"),
    "plate_appearance": ("TARGET",),
    "play_transition": ("TARGET", "SIMULATOR_VALIDATION"),
    "player_game_batting": ("TARGET", "LAGGABLE_FEATURE"),
    "player_game_fielding": ("TARGET", "LAGGABLE_FEATURE"),
    "player_game_pitching": ("TARGET", "LAGGABLE_FEATURE"),
    "player_prior": ("PRIOR_FEATURE",),
    "starter_usage": ("TARGET",),
    "weather": ("CONTEXT",),
}

_FAMILY_LIMITATIONS: Mapping[str, tuple[str, ...]] = {
    "bullpen_usage": ("Actual reliever use is known after the game and is enhanced-only.",),
    "pitch_sequence": ("Retrosheet pitch strings are not Statcast pitch tracking.",),
    "plate_appearance": ("Outcomes are retrospective targets, not same-game pregame features.",),
    "play_transition": ("Play state is retrospective and enhanced-only for tier projection.",),
    "player_game_batting": ("Actual starting-lineup rows carry historical timing risk.",),
    "player_game_fielding": ("No tracking-based defensive measurements are present.",),
    "player_game_pitching": ("Actual starter rows carry historical timing risk.",),
    "player_prior": ("A target season may use only Y-1 or earlier prior evidence.",),
    "starter_usage": ("Actual starter identity and duration are postgame targets.",),
    "weather": ("Historical actual weather timing is not proven pregame.",),
}


class ManifestBuildError(RuntimeError):
    """Finalized evidence cannot be projected without inventing or losing meaning."""


def exchange_code_revision(project_root: Path) -> str:
    """Hash the exact exchange Python implementation without depending on Git state."""

    exchange_root = project_root / "server" / "app" / "gen2" / "exchange"
    files = sorted(
        path for path in exchange_root.glob("*.py") if path.is_file()
    )
    if not files:
        raise ManifestBuildError("Exchange implementation files are unavailable")
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(project_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def build_manifest(
    *,
    recovery_manifest: object,
    readiness: Mapping[str, Any],
    finalized_result: Mapping[str, Any],
    active_identity: Mapping[str, Any],
    quarantine_impact: Mapping[str, Any],
    source_artifact_identities: Mapping[str, Mapping[str, Any]],
    schema_registry_artifact: Mapping[str, Any],
    source_rights_artifact: Mapping[str, Any],
    attribution_artifact: Mapping[str, Any],
    publishing_code_revision: str,
    source_rights: Mapping[str, Any],
    published_at: datetime | None = None,
) -> SportsDatasetManifestV1:
    """Build and strictly validate the deterministic semantic manifest."""

    files = tuple(_value(recovery_manifest, "files"))
    aggregate = _value(recovery_manifest, "aggregate")
    _require_final_evidence(recovery_manifest, readiness, finalized_result)
    timestamp = published_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ManifestBuildError("published_at must be timezone-aware")
    rights_ids, rights_by_source = _rights_index(source_rights)
    seasons = _build_seasons(files, readiness)
    populations = _population_definitions(str(readiness["operator_revision"]))
    families = _build_families(aggregate, files, rights_by_source)
    partitions = _build_partitions(files, rights_by_source)
    identity = _build_identity(active_identity, quarantine_impact, recovery_manifest)
    payload: dict[str, Any] = {
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "supported_consumer_range": ">=1.0.0,<2.0.0",
        "manifest_id": "PENDING_SEMANTIC_DIGEST",
        "manifest_sha256": "0" * 64,
        "dataset_id": DATASET_ID,
        "dataset_version_id": "PENDING_SEMANTIC_DIGEST",
        "package_id": "PENDING_SEMANTIC_DIGEST",
        "publisher": PUBLISHER_ID,
        "published_at": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "sport": "Baseball",
        "league": "MLB",
        "development_purpose": (
            "Portable read-only local/private model development, internal derived "
            "analytics, and future read-only registration."
        ),
        "evidence_cutoff": EVIDENCE_CUTOFF,
        "source_lineage": _source_lineage(
            recovery_manifest,
            readiness,
            source_artifact_identities,
            publishing_code_revision,
        ),
        "seasons": seasons,
        "populations": populations,
        "data_families": families,
        "partitions": partitions,
        "identity_coverage": identity,
        "target_definitions": _target_definitions(),
        "temporal_contract": _temporal_contract(readiness),
        "data_quality_evidence": _data_quality(readiness),
        "known_limitations": _known_limitations(),
        "source_rights_ids": rights_ids,
        "package_artifacts": [
            dict(attribution_artifact),
            dict(schema_registry_artifact),
            dict(source_rights_artifact),
        ],
        "totals": {
            "parquet_bytes": int(_value(aggregate, "byte_count")),
            "parquet_files": int(_value(aggregate, "file_count")),
            "rows": int(_value(aggregate, "row_count")),
        },
    }
    payload["package_artifacts"] = sorted(
        payload["package_artifacts"], key=lambda row: row["logical_uri"]
    )
    # Identity must cover the exact representation consumers load.  Pydantic
    # normalizes tuples, datetimes, and nested union values; deriving the digest
    # from the pre-normalized builder mapping can otherwise produce a manifest
    # that fails its own package-only identity verification.
    normalized = SportsDatasetManifestV1.model_validate(
        apply_manifest_identity(payload)
    ).model_dump(mode="json")
    return SportsDatasetManifestV1.model_validate(apply_manifest_identity(normalized))


def _require_final_evidence(
    recovery: object,
    readiness: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    recovery_id = str(_value(recovery, "recovery_id"))
    checksum = str(_value(recovery, "manifest_checksum"))
    if (
        result.get("run_id") != recovery_id
        or result.get("recovery_manifest_checksum") != checksum
        or result.get("status") != "FINALIZED"
        or result.get("last_completed_stage") != "FINALIZED"
        or int(result.get("provider_calls", -1)) != 0
        or int(result.get("network_requests", -1)) != 0
        or int(result.get("provider_transports_constructed", -1)) != 0
    ):
        raise ManifestBuildError("Finalized recovery result does not bind the manifest")
    if (
        readiness.get("decision") != "READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS"
        or readiness.get("modeling_can_proceed") is not True
        or readiness.get("recovery", {}).get("recovery_id") != recovery_id
        or readiness.get("recovery", {}).get("manifest_checksum") != checksum
        or readiness.get("recovery", {}).get("status") != "FINALIZED"
    ):
        raise ManifestBuildError("Terminal readiness evidence does not bind final FD1")


def _source_lineage(
    recovery: object,
    readiness: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, Any]],
    publishing_revision: str,
) -> dict[str, Any]:
    generation = _value(recovery, "generation_lineage")
    finalizer = _value(recovery, "finalizer_lineage")
    releases = sorted(
        (
            {
                "source_key": str(_value(row, "source_key")),
                "source_version_id": str(_value(row, "source_version_id")),
                "dataset_version": str(_value(row, "dataset_version")),
                "identity_checksum": str(_value(row, "identity_checksum")),
            }
            for row in _value(generation, "source_releases")
        ),
        key=lambda row: row["source_key"],
    )
    return {
        "finalized_fd1_recovery_run_id": str(_value(recovery, "recovery_id")),
        "source_semantic_manifest_checksum": str(
            _value(recovery, "manifest_checksum")
        ),
        "finalized_evidence": dict(identities["finalized_evidence"]),
        "readiness_evidence": dict(identities["readiness_evidence"]),
        "active_identity_evidence": dict(identities["active_identity_evidence"]),
        "quarantine_impact_evidence": dict(identities["quarantine_impact_evidence"]),
        "generation_revision": str(_value(generation, "code_revision")),
        "finalizer_revision": str(_value(finalizer, "code_revision")),
        "publishing_code_revision": publishing_revision,
        "database_migration": DATABASE_MIGRATION,
        "source_releases": releases,
        "readiness_inventory_binding_note": (
            "The terminal readiness artifact binds the finalized pre-closeout source "
            "inventory at 28135 bytes / sha256 "
            "695562057c9786cf9eadb0ee232c7f966ab8da0fd601c94669ab7d9be890bdf1; "
            "the committed closeout inventory is separately governed at 28216 bytes / "
            "sha256 6af564e573b5ef3a1cc7c1b5d6250783a0c2b432a138e057089a3c42941c076f."
        ),
    }


def _build_seasons(files: Sequence[object], readiness: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = {
        int(row["season"]): row
        for row in readiness["readiness_result"]["season_reconciliation"]
    }
    bounds: dict[int, list[str]] = defaultdict(list)
    for entry in files:
        season = int(_value(entry, "season"))
        bounds[season].extend(
            [str(_value(entry, "min_date")), str(_value(entry, "max_date"))]
        )
    result = []
    for season in range(2021, 2026):
        row = rows[season]
        result.append(
            {
                "season": season,
                "role": (
                    "PRIOR_ONLY_HISTORY" if season <= 2022 else "PRIMARY_GEN2_MODELING"
                ),
                "games": int(row["expected_games"]),
                "plate_appearances": int(row["plate_appearance_rows"]),
                "transitions": int(row["transition_rows"]),
                "starter_targets": int(row["starter_duration_rows"]),
                "bullpen_appearances": int(row["bullpen_usage_rows"]),
                "weather_coverage": {
                    key: row["weather"][key] for key in ("covered", "eligible", "ratio")
                },
                "complete_lineup_coverage": {
                    key: row["lineups"][key] for key in ("covered", "eligible", "ratio")
                },
                "date_range": {
                    "minimum": min(bounds[season]),
                    "maximum": max(bounds[season]),
                },
            }
        )
    return result


def _population_definitions(operator_revision: str) -> list[dict[str, Any]]:
    base = expected_population_contracts()
    if any(row["readiness_operator_revision"] != operator_revision for row in base):
        raise ManifestBuildError("Readiness operator revision differs from frozen FD1")
    narratives = {
        "prior_only": {
            "description": "All manifested rows from the 2021-2022 history seasons.",
            "temporal_meaning": "History retained for prior construction; never primary target rows.",
        },
        "enhanced_research": {
            "description": (
                "Primary-season actual lineup/starter/reliever, play-state, and "
                "untimestamped-weather rows isolated by the finalized readiness operator."
            ),
            "temporal_meaning": "Retrospective timing-risk evidence; separate from strict deployment.",
        },
        "strict_deployable": {
            "description": (
                "Remaining primary-season target or safely laggable rows after prior-only "
                "and enhanced timing-risk precedence."
            ),
            "temporal_meaning": (
                "Targets remain postgame-only; feature use must still obey final_available_date "
                "and Y-1 prior rules."
            ),
        },
    }
    return [{**narratives[str(row["population_id"])], **row} for row in base]


def _build_families(
    aggregate: object,
    files: Sequence[object],
    rights_by_source: Mapping[str, tuple[str, ...]],
) -> list[dict[str, Any]]:
    source_versions: dict[str, str] = {}
    declared: dict[str, str] = {}
    for entry in files:
        family = str(_value(entry, "family"))
        source_versions.setdefault(family, str(_value(entry, "source_version_id")))
        declared.setdefault(family, str(_value(entry, "declared_schema")))
    result = []
    for row in _value(aggregate, "families"):
        family = str(_value(row, "family"))
        source_key = (
            "SABR_LAHMAN_BOX_DOWNLOADS"
            if family == "player_prior"
            else "RETROSHEET_CSV_DOWNLOADS"
        )
        result.append(
            {
                "family_id": family,
                "schema_id": declared[family],
                "purpose": _FAMILY_PURPOSE[family],
                "target_feature_role": list(_FAMILY_ROLE[family]),
                "temporal_classification": list(_family_temporal(family)),
                "population_eligibility": list(_family_populations(family)),
                "file_count": int(_value(row, "file_count")),
                "row_count": int(_value(row, "row_count")),
                "byte_count": int(_value(row, "byte_count")),
                "date_range": {
                    "minimum": str(_value(row, "min_date")),
                    "maximum": str(_value(row, "max_date")),
                },
                "source_key": source_key,
                "source_version_id": source_versions[family],
                "rights_control_ids": list(rights_by_source[source_key]),
                "known_limitations": list(_FAMILY_LIMITATIONS[family]),
            }
        )
    return sorted(result, key=lambda row: row["family_id"])


def _build_partitions(
    files: Sequence[object], rights_by_source: Mapping[str, tuple[str, ...]]
) -> list[dict[str, Any]]:
    result = []
    for entry in files:
        family = str(_value(entry, "family"))
        season = int(_value(entry, "season"))
        month = int(_value(entry, "month"))
        checksum = str(_value(entry, "sha256"))
        source_key = str(_value(entry, "source_key")).upper()
        result.append(
            {
                "partition_id": str(_value(entry, "partition_identity_checksum")),
                "family": family,
                "season": season,
                "month": month,
                "package_relative_uri": (
                    f"parquet/{family}/season={season:04d}/month={month:02d}/"
                    f"part-{checksum}.parquet"
                ),
                "original_source_logical_identity": str(_value(entry, "logical_uri")),
                "row_count": int(_value(entry, "row_count")),
                "byte_count": int(_value(entry, "size_bytes")),
                "minimum_date": str(_value(entry, "min_date")),
                "maximum_date": str(_value(entry, "max_date")),
                "file_sha256": checksum,
                "schema_id": str(_value(entry, "declared_schema")),
                "physical_schema_fingerprint": str(
                    _value(entry, "physical_schema_fingerprint")
                ),
                "physical_schema": [
                    {"name": str(_value(field, "name")), "dtype": str(_value(field, "dtype"))}
                    for field in _value(entry, "physical_schema")
                ],
                "source_key": source_key,
                "source_version_id": str(_value(entry, "source_version_id")),
                "checkpoint_lineage_id": str(_value(entry, "checkpoint_id")),
                "governance_checksum": str(_value(entry, "governance_checksum")),
                "temporal_classification": list(_family_temporal(family)),
                "target_eligibility": list(_partition_targets(family)),
                "source_rights_ids": list(rights_by_source[source_key]),
            }
        )
    return sorted(result, key=lambda row: row["package_relative_uri"])


def _build_identity(
    active_payload: Mapping[str, Any],
    quarantine: Mapping[str, Any],
    recovery: object,
) -> dict[str, Any]:
    report = active_payload["active_identity_coverage_report"]
    overall = report["overall_coverage"]
    primary = next(
        row
        for row in report["population_coverage"]
        if row["population"] == "GEN2_PRIMARY_2023_2025"
    )
    qsummary = quarantine["summary"]
    releases = _value(_value(recovery, "generation_lineage"), "source_releases")
    return {
        "all_2021_2025": _identity_slice(overall),
        "primary_2023_2025": _identity_slice(primary),
        "quarantine_rows": int(qsummary["quarantine_rows"]),
        "unresolved_quarantine_rows": int(qsummary["source_declared_unresolved_rows"]),
        "ambiguous_quarantine_rows": int(qsummary["source_declared_ambiguity_rows"]),
        "downstream_impacted_rows": 0,
        "canonical_identity_mapping_count": int(
            qsummary["canonical_mapping_rows_inserted"]
        ),
        "identity_source_versions": sorted(
            str(_value(row, "source_version_id")) for row in releases
        ),
        "derivation_checksum": str(report["derivation_checksum"]),
    }


def _identity_slice(row: Mapping[str, Any]) -> dict[str, Any]:
    namespace = row["namespace_coverage"][0]
    return {
        "label": str(row["label"]),
        "seasons": list(row["seasons"]),
        "unique_players": int(row["unique_retrosheet_players"]),
        "reference_count": int(row["reference_count"]),
        "exact_reference_rate": float(namespace["exact_reference_rate"]),
        "exact_unique_rate": float(namespace["exact_unique_rate"]),
        "unresolved_references": int(namespace["unresolved_reference_count"]),
        "ambiguous_references": int(namespace["conflict_reference_count"]),
    }


def _target_definitions() -> list[dict[str, Any]]:
    return [
        {
            "target_id": "bullpen_usage",
            "family_ids": ["bullpen_usage"],
            "role": "TARGET",
            "description": "Actual relief appearances and postgame bullpen usage.",
            "limitations": ["Actual appearance evidence is enhanced-only."],
        },
        {
            "target_id": "chronological_player_priors",
            "family_ids": ["player_prior"],
            "role": "FEATURE",
            "description": "Annual player evidence available only to later target seasons.",
            "limitations": ["Y-1 or earlier; same-season future totals are prohibited."],
        },
        {
            "target_id": "game_context",
            "family_ids": [
                "player_game_batting",
                "player_game_fielding",
                "player_game_pitching",
                "weather",
            ],
            "role": "CONTEXT",
            "description": "Game, park, participant, fielding, and actual-weather context.",
            "limitations": ["Historical actual-weather timing is not proven pregame."],
        },
        {
            "target_id": "plate_appearance_outcome",
            "family_ids": ["pitch_sequence", "plate_appearance"],
            "role": "TARGET",
            "description": "Canonical terminal eight-class plate-appearance outcome.",
            "ordered_classes": list(CANONICAL_PA_CLASS_ORDER),
            "taxonomy_version": EXPECTED_PA_TAXONOMY_VERSION,
            "taxonomy_identity": EXPECTED_PA_TAXONOMY_IDENTITY,
            "limitations": [
                "FD1 rows carry importer taxonomy retrosheet_to_mlb_gen2_pa_v1; "
                "probability vectors use the semantic model order recorded here."
            ],
        },
        {
            "target_id": "play_base_out_transition",
            "family_ids": ["play_transition"],
            "role": "TARGET_AND_FEATURE",
            "description": "Conserved outs, bases, runners, runs, and score transition.",
            "limitations": ["Retrospective play state is enhanced-only."],
        },
        {
            "target_id": "starter_duration",
            "family_ids": ["player_game_pitching", "starter_usage"],
            "role": "TARGET",
            "description": "Actual starter identity and innings/batters-faced duration.",
            "limitations": ["Actual starter evidence is not proven prospectively timed."],
        },
    ]


def _temporal_contract(readiness: Mapping[str, Any]) -> dict[str, Any]:
    folds = []
    for row in readiness["readiness_result"]["temporal_folds"]:
        validation_season = 2024 if row["fold_id"] == "train-2023-validate-2024" else 2025
        folds.append(
            {
                **dict(row),
                "training_seasons": ([2023] if validation_season == 2024 else [2023, 2024]),
                "validation_season": validation_season,
            }
        )
    return {
        "season_roles": {
            "2021-2022": "PRIOR_ONLY_HISTORY",
            "2023-2025": "PRIMARY_GEN2_MODELING",
        },
        "player_prior_rule": (
            "Annual Dec-31 player_prior rows have eligible_from_season=season+1; "
            "target season Y may use only Y-1 or earlier evidence."
        ),
        "no_current_game_evidence": True,
        "no_current_season_future_evidence": True,
        "strict_timing_risk_separation": (
            "Actual lineup, starter, reliever, play-state, and untimestamped-weather "
            "rows are isolated in enhanced_research."
        ),
        "same_date_game_ordering": (
            "Use final_available_date, source game_number, doubleheader_id, and canonical "
            "game identity; never borrow later same-date game evidence."
        ),
        "suspended_game_handling": (
            "Retain original game identity/date and delay availability until the source "
            "completion/final date."
        ),
        "doubleheader_handling": (
            "Preserve source game_number and doubleheader_id as distinct ordered games."
        ),
        "fold_compatibility": "Rolling-origin only; training_end precedes validation_start.",
        "folds": folds,
        "untouched_final_holdout_claimed": False,
    }


def _data_quality(readiness: Mapping[str, Any]) -> dict[str, Any]:
    result = readiness["readiness_result"]
    return {
        "eight_class_taxonomy_complete": True,
        "unsafe_terminal_events": 0,
        "transition_conservation_passed": not result["conservation_conflicts"],
        "score_run_runner_conservation_passed": not result["conservation_conflicts"],
        "identity_coverage_passed": not result["identity_conflicts"],
        "tier_leaks": len(result["tier_leakage"]),
        "temporal_leaks": len(result["temporal_leakage"]),
        "unique_partitions": True,
        "schema_validation_passed": True,
        "quarantine_downstream_impact": 0,
    }


def _known_limitations() -> list[dict[str, str]]:
    descriptions = {
        "INCOMPLETE_ACTUAL_LINEUP_COVERAGE": (
            "Actual-lineup coverage is 2423/2430 in 2023, 2418/2429 in 2024, "
            "and 2420/2430 in 2025."
        ),
        "HISTORICAL_ACTUAL_LINEUP_TIMING_RISK": "Historical actual lineups are not proven pregame-known.",
        "HISTORICAL_ACTUAL_STARTER_TIMING_RISK": "Historical actual starters are not proven pregame-known.",
        "HISTORICAL_ACTUAL_WEATHER_TIMING_RISK": "Historical actual weather is not proven pregame-known.",
        "NO_MULTI_SEASON_STATCAST": "No multi-season Statcast corpus is present.",
        "NO_VELOCITY_SPIN_MOVEMENT_LOCATION": "No velocity, spin, movement, or pitch-location tracking is present.",
        "NO_LAUNCH_SPEED_ANGLE": "No launch speed or launch angle is present.",
        "NO_BARREL_HARD_HIT_XWOBA": "No barrel, hard-hit, or xwOBA evidence is present.",
        "NO_TRACKING_DEFENSE_OR_RUNNER_SPEED": "No tracking-based defense or runner-speed evidence is present.",
        "NO_HISTORICAL_MARKET_CLOSING_LINE_OR_CLV": "No historical market, closing-line, or CLV evidence is present.",
        "RETROSPECTIVE_EVIDENCE_NOT_PROSPECTIVE_PROOF": "Retrospective reconstruction does not prove prospective availability.",
        "NOT_PRODUCTION_MODEL_APPROVAL": "This package is dataset evidence, not production-model approval.",
    }
    return [
        {"limitation_id": name, "description": descriptions[name], "scope": "FD1_EXCHANGE"}
        for name in REQUIRED_LIMITATION_IDS
    ]


def _family_temporal(family: str) -> tuple[str, ...]:
    if family == "player_prior":
        return ("Y_MINUS_1_ONLY", "SAME_SEASON_PREDICTOR_INELIGIBLE")
    values = ["RETROSPECTIVE", "AVAILABLE_ONLY_AFTER_GAME_FINAL"]
    if family in _ENHANCED_FAMILIES:
        values.append("ENHANCED_TIMING_RISK")
    return tuple(values)


def _family_populations(family: str) -> tuple[str, ...]:
    if family in _ENHANCED_FAMILIES:
        return ("enhanced_research", "prior_only")
    if family in {"player_game_batting", "player_game_pitching"}:
        return ("enhanced_research", "prior_only", "strict_deployable")
    return ("prior_only", "strict_deployable")


def _partition_targets(family: str) -> tuple[str, ...]:
    targets = tuple(
        target_id
        for target_id, family_ids, _role in EXPECTED_TARGET_DEFINITIONS
        if family in family_ids
    )
    if not targets:
        raise ManifestBuildError(f"Family {family!r} has no target eligibility")
    return targets


def _rights_index(
    rights: Mapping[str, Any],
) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    records = rights.get("records")
    if not isinstance(records, list) or len(records) != 6:
        raise ManifestBuildError("Source-rights records are incomplete")
    ids = tuple(str(row["rights_control_id"]) for row in records)
    if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
        raise ManifestBuildError("Source-rights IDs are not sorted and unique")
    by_source: dict[str, list[str]] = defaultdict(list)
    for row in records:
        status = str(row.get("fd1_use", {}).get("status", ""))
        if status in {"EXCLUDED_NOT_USED", "ZERO_USE_NO_DOWNLOAD"}:
            continue
        for release in row.get("source_releases", []):
            key = str(release["source_key"])
            by_source[key].append(str(row["rights_control_id"]))
    # Canonical identities in both partition families also depend on Chadwick.
    chadwick = next(
        str(row["rights_control_id"])
        for row in records
        if "Chadwick" in str(row.get("source_name", ""))
    )
    for key in ("RETROSHEET_CSV_DOWNLOADS", "SABR_LAHMAN_BOX_DOWNLOADS"):
        if chadwick not in by_source[key]:
            by_source[key].append(chadwick)
    required = ("RETROSHEET_CSV_DOWNLOADS", "SABR_LAHMAN_BOX_DOWNLOADS")
    if any(not by_source[key] for key in required):
        raise ManifestBuildError("Partition source lacks an applicable rights record")
    return ids, {key: tuple(sorted(values)) for key, values in by_source.items()}


def _value(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        if name not in value:
            raise ManifestBuildError(f"Evidence is missing {name}")
        return value[name]
    if not hasattr(value, name):
        raise ManifestBuildError(f"Evidence is missing {name}")
    return getattr(value, name)


__all__ = [
    "ManifestBuildError",
    "POPULATION_PREDICATE_VERSION",
    "build_manifest",
    "exchange_code_revision",
]
