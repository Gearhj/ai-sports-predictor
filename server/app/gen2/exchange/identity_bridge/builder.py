"""Build deterministic semantic artifacts for ``SPORTS_IDENTITY_BRIDGE_V1``."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.gen2.exchange.evidence import load_fd1_evidence
from app.gen2.exchange.identity_bridge.canonical import (
    CANONICALIZATION_VERSION,
    apply_bridge_manifest_identity,
    canonical_json_bytes,
)
from app.gen2.exchange.identity_bridge.contracts import (
    SportsIdentityBridgeManifestV1,
)
from app.gen2.exchange.identity_bridge.parent import ParentIdentityEvidence
from app.gen2.exchange.identity_bridge.revision import (
    extraction_code_revision,
    publishing_code_revision,
)
from app.gen2.exchange.identity_bridge.rights import (
    ALLOWED_PURPOSES,
    PROHIBITED_PURPOSES,
    REQUIRED_RIGHTS_IDS,
    attribution_bytes,
    source_rights_bytes,
)
from app.gen2.exchange.identity_bridge.rows import (
    ParquetArtifact,
    build_alias_parquet,
    build_dimension_parquet,
)
from app.gen2.exchange.identity_bridge.source import IdentityBridgeSourceResult
from app.gen2.exchange.identity_bridge import trust as bridge_trust


_NAMESPACE_MAP = {
    "retrosheet": "RETROSHEET",
    "lahman": "LAHMAN",
    "chadwick_person": "CHADWICK",
    "mlbam": "MLBAM",
    "baseball_reference": "BASEBALL_REFERENCE",
}
_CONSUMER_JOIN_FIELDS = (
    ("bullpen_usage", "player_id"),
    ("pitch_sequence", "batter_id"),
    ("pitch_sequence", "pitcher_id"),
    ("plate_appearance", "batter_id"),
    ("plate_appearance", "pitcher_id"),
    ("player_game_batting", "player_id"),
    ("player_game_fielding", "player_id"),
    ("player_game_pitching", "player_id"),
    ("starter_usage", "player_id"),
)


class IdentityBridgeBuildError(RuntimeError):
    """Authoritative sources cannot form one exact portable bridge package."""


@dataclass(frozen=True, slots=True)
class BridgeBuildResult:
    manifest: SportsIdentityBridgeManifestV1
    manifest_bytes: bytes
    alias_artifact: ParquetArtifact
    dimension_artifact: ParquetArtifact
    alias_rows: tuple[dict[str, Any], ...]
    dimension_rows: tuple[dict[str, Any], ...]
    semantic_files: Mapping[str, bytes]


def build_identity_bridge(
    *,
    project_root: Path,
    parent: ParentIdentityEvidence,
    source: IdentityBridgeSourceResult,
    publication_timestamp: datetime | None = None,
) -> BridgeBuildResult:
    """Build all semantic bytes in memory without writing package or database files."""

    timestamp = publication_timestamp or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise IdentityBridgeBuildError("publication_timestamp must be timezone-aware")
    _validate_source_population(parent, source)

    resolution_by_raw = {
        row.raw_retrosheet_player_id: str(row.canonical_player_id)
        for row in source.retrosheet_resolutions
    }
    canonical_context = _canonical_context(parent, source, resolution_by_raw)
    alias_rows = _build_alias_rows(source, canonical_context)
    dimension_rows = _build_dimension_rows(
        parent, source, canonical_context, resolution_by_raw
    )
    alias_artifact = build_alias_parquet(alias_rows)
    dimension_artifact = build_dimension_parquet(dimension_rows)

    rights_payload = source_rights_bytes(parent.parent_root)
    attribution_payload = attribution_bytes(parent.parent_root)
    semantic_files = {
        "active-player-dimension.parquet": dimension_artifact.payload,
        "attribution.md": attribution_payload,
        "player-identity-aliases.parquet": alias_artifact.payload,
        "source-rights.json": rights_payload,
    }
    join_contracts, join_validation = _build_join_evidence(
        parent, resolution_by_raw
    )
    eligibility = _build_y_minus_1(parent, resolution_by_raw)
    evidence_artifacts, governed = _governed_evidence(Path(project_root))
    _cross_check_governed_evidence(parent, source, governed)

    bridge_files = tuple(
        _bridge_file(
            logical_uri,
            payload,
            alias_artifact=alias_artifact,
            dimension_artifact=dimension_artifact,
        )
        for logical_uri, payload in semantic_files.items()
    )
    namespaces = _namespace_inventory(alias_rows)
    primary_portable_players = sum(
        bool(row["primary_active_2023_2025"]) for row in dimension_rows
    )
    coverage = {
        "canonical_identity_mapping_count": (
            source.governed_evidence.canonical_mapping_rows
        ),
        "active_unique_players_2021_2025": 2_693,
        "primary_unique_players_2023_2025": 2_071,
        "active_player_references_2021_2025": 21_356_559,
        "primary_player_references_2023_2025": 12_838_083,
        "portable_parent_unique_players_2021_2025": len(dimension_rows),
        "portable_parent_unique_players_2023_2025": primary_portable_players,
        "portable_parent_player_references_2021_2025": (
            parent.governed_reference_coverage.reference_count
        ),
        "portable_parent_player_references_2023_2025": (
            parent.governed_reference_coverage.primary_reference_count
        ),
        "unrepresented_governed_players_2021_2025": 2_693 - len(dimension_rows),
        "unrepresented_governed_players_2023_2025": 2_071 - primary_portable_players,
        "exact_unique_player_rate_2021_2025": 1.0,
        "exact_reference_rate_2021_2025": 1.0,
        "exact_unique_player_rate_2023_2025": 1.0,
        "exact_reference_rate_2023_2025": 1.0,
        "unresolved_quarantine_rows": source.governed_evidence.unresolved_rows,
        "ambiguous_quarantine_rows": (
            source.governed_evidence.source_declared_conflict_rows
        ),
        "downstream_impacted_rows": 0,
    }

    payload: dict[str, Any] = {
        "contract_name": bridge_trust.CONTRACT_NAME,
        "contract_version": bridge_trust.CONTRACT_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "bridge_manifest_id": "PENDING",
        "bridge_manifest_sha256": "0" * 64,
        "bridge_package_id": "PENDING",
        "publisher": bridge_trust.PUBLISHER,
        "publication_timestamp": timestamp.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "sport": "Baseball",
        "league": "MLB",
        "parent_binding": {
            "parent_dataset_id": bridge_trust.EXPECTED_PARENT_DATASET_ID,
            "parent_dataset_version_id": parent.parent_dataset_version_id,
            "parent_dataset_manifest_id": parent.parent_manifest_id,
            "parent_dataset_manifest_sha256": parent.parent_manifest_sha256,
            "parent_partition_authority_sha256": (
                parent.parent_partition_authority_sha256
            ),
            "parent_package_id": parent.parent_package_id,
            "plate_appearance_class_order": list(parent.pa_class_order),
        },
        "evidence_cutoff": bridge_trust.EVIDENCE_CUTOFF,
        "source_lineage": {
            "canonical_identity_mapping_count": (
                source.governed_evidence.canonical_mapping_rows
            ),
            "evidence_artifacts": evidence_artifacts,
            "source_releases": [
                {
                    "source_key": row.source_key,
                    "source_version_id": str(row.source_version_id),
                    "identity_checksum": row.release_identity_checksum,
                }
                for row in source.governed_evidence.source_versions
            ],
            "extraction_revision": extraction_code_revision(Path(project_root)),
            "publishing_code_revision": publishing_code_revision(Path(project_root)),
            "database_transaction_mode": "READ_ONLY",
            "database_writes": 0,
            "provider_network_calls": 0,
            "external_network_calls": 0,
        },
        "identity_namespaces": namespaces,
        "bridge_files": bridge_files,
        "coverage_evidence": coverage,
        "join_contracts": join_contracts,
        "join_validation": join_validation,
        "y_minus_1_prior_eligibility": eligibility,
        "source_rights": {
            "rights_control_ids": list(REQUIRED_RIGHTS_IDS),
            "allowed_purposes": list(ALLOWED_PURPOSES),
            "prohibited_purposes": list(PROHIBITED_PURPOSES),
            "legal_conclusion": "NO_NEW_LEGAL_CONCLUSIONS",
            "public_raw_bridge_download_authorized": False,
            "public_raw_redistribution_authorized": False,
            "identity_data_resale_authorized": False,
            "unrestricted_commercial_redistribution_authorized": False,
        },
        "limitations": _limitations(),
        "totals": {
            "alias_rows": alias_artifact.row_count,
            "active_player_rows": dimension_artifact.row_count,
            "namespace_count": len(namespaces),
            "join_contract_count": len(join_contracts),
            "join_validation_count": len(join_validation),
            "y_minus_1_eligibility_records": len(eligibility),
            "semantic_file_count": len(bridge_files),
            "semantic_file_bytes": sum(row["byte_count"] for row in bridge_files),
            "final_package_file_count": bridge_trust.FINAL_PACKAGE_FILE_COUNT,
            "alias_authority_sha256": alias_artifact.row_authority_sha256,
            "dimension_authority_sha256": dimension_artifact.row_authority_sha256,
        },
    }
    resolved = apply_bridge_manifest_identity(payload)
    manifest = SportsIdentityBridgeManifestV1.model_validate(resolved)
    normalized = manifest.model_dump(mode="json")
    manifest = SportsIdentityBridgeManifestV1.model_validate(
        apply_bridge_manifest_identity(normalized)
    )
    manifest_bytes = canonical_json_bytes(
        manifest.model_dump(mode="json"), trailing_newline=True
    )
    return BridgeBuildResult(
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        alias_artifact=alias_artifact,
        dimension_artifact=dimension_artifact,
        alias_rows=tuple(alias_rows),
        dimension_rows=tuple(dimension_rows),
        semantic_files=semantic_files,
    )


def _validate_source_population(
    parent: ParentIdentityEvidence, source: IdentityBridgeSourceResult
) -> None:
    if tuple(source.raw_retrosheet_player_ids) != tuple(parent.all_retrosheet_player_ids):
        raise IdentityBridgeBuildError("Source extraction Retrosheet scope differs")
    prior_ids = tuple(sorted({row.canonical_player_id for row in parent.player_priors}))
    if tuple(str(value) for value in source.prior_canonical_player_ids) != prior_ids:
        raise IdentityBridgeBuildError("Source extraction player_prior scope differs")
    if len(source.retrosheet_resolutions) != len(parent.all_retrosheet_player_ids):
        raise IdentityBridgeBuildError("Retrosheet resolution count differs")
    if len(source.canonical_player_ids) != len(parent.all_retrosheet_player_ids):
        raise IdentityBridgeBuildError("Portable canonical-player scope differs")
    if source.database_boundary.transaction_read_only is not True:
        raise IdentityBridgeBuildError("Identity extraction was not read-only")
    if source.database_boundary.transaction_rolled_back is not True:
        raise IdentityBridgeBuildError("Identity extraction did not roll back")
    if source.database_boundary.rejected_statement_count != 0:
        raise IdentityBridgeBuildError("Identity extraction rejected SQL")


def _canonical_context(
    parent: ParentIdentityEvidence,
    source: IdentityBridgeSourceResult,
    resolution_by_raw: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    context: dict[str, dict[str, Any]] = {
        str(value): {"seasons": set(), "active_seasons": set(), "prior_rows": []}
        for value in source.canonical_player_ids
    }
    for row in parent.players:
        canonical = resolution_by_raw[row.retrosheet_player_id]
        context[canonical]["seasons"].update(row.seasons)
        context[canonical]["active_seasons"].update(row.seasons)
    for prior in parent.player_priors:
        context[prior.canonical_player_id]["seasons"].add(prior.season)
        context[prior.canonical_player_id]["prior_rows"].append(prior)
    for canonical, row in context.items():
        if not row["seasons"]:
            raise IdentityBridgeBuildError(
                f"Canonical player has no represented season: {canonical}"
            )
    return context


def _build_alias_rows(
    source: IdentityBridgeSourceResult,
    context: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for alias in source.aliases:
        namespace = _NAMESPACE_MAP.get(alias.source_namespace)
        if namespace is None:
            raise IdentityBridgeBuildError("Unsupported person namespace entered aliases")
        canonical = str(alias.canonical_player_id)
        seasons = set(context[canonical]["seasons"])
        active_seasons = set(context[canonical]["active_seasons"])
        parent_join_key: str | None
        if namespace == "RETROSHEET":
            parent_join_key = f"retrosheet:player:{alias.external_id}"
        elif namespace == "LAHMAN":
            parent_join_key = alias.external_id
        else:
            parent_join_key = None
        rows.append(
            {
                "canonical_player_uuid": canonical,
                "namespace": namespace,
                "source_player_id": alias.external_id,
                "parent_join_key": parent_join_key,
                "source_key": alias.source_key,
                "source_version_id": str(alias.source_version_id),
                "source_version_identity_sha256": (
                    alias.source_release_identity_checksum
                ),
                "mapping_confidence": alias.confidence,
                "mapping_status": "RESOLVED",
                "mapping_basis": alias.mapping_basis,
                "first_represented_season": min(seasons),
                "last_represented_season": max(seasons),
                "primary_2023_2025": bool(
                    active_seasons & {2023, 2024, 2025}
                ),
                "source_row_number": alias.source_row_number,
                "source_row_sha256": alias.source_row_checksum,
                "source_row_identity": f"sha256:{alias.source_row_checksum}",
                "evidence_logical_uri": alias.evidence_logical_uri,
                "mapping_identity_sha256": alias.identity_checksum,
            }
        )
    return rows


def _build_dimension_rows(
    parent: ParentIdentityEvidence,
    source: IdentityBridgeSourceResult,
    context: Mapping[str, Mapping[str, Any]],
    resolution_by_raw: Mapping[str, str],
) -> list[dict[str, Any]]:
    player_by_canonical = {
        resolution_by_raw[row.retrosheet_player_id]: row for row in parent.players
    }
    namespace_by_canonical: dict[str, set[str]] = defaultdict(set)
    for alias in source.aliases:
        namespace_by_canonical[str(alias.canonical_player_id)].add(
            _NAMESPACE_MAP[alias.source_namespace]
        )
    rows: list[dict[str, Any]] = []
    for canonical in sorted(context):
        player = player_by_canonical.get(canonical)
        if player is None:
            raise IdentityBridgeBuildError("Portable player lacks Retrosheet evidence")
        seasons = set(context[canonical]["seasons"])
        active_seasons = set(context[canonical]["active_seasons"])
        priors = list(context[canonical]["prior_rows"])
        namespaces = namespace_by_canonical[canonical]
        other = (
            player.total_join_references
            - player.plate_appearance_batter_references
            - player.plate_appearance_pitcher_references
        )
        if other < 0:
            raise IdentityBridgeBuildError("Player role references do not reconcile")
        eligible_seasons = [row.eligible_from_season for row in priors]
        rows.append(
            {
                "canonical_player_uuid": canonical,
                "first_represented_season": min(seasons),
                "last_represented_season": max(seasons),
                "prior_history_2021_2022": any(
                    row.season in {2021, 2022} for row in priors
                ),
                "primary_active_2023_2025": bool(
                    active_seasons & {2023, 2024, 2025}
                ),
                "has_retrosheet_alias": "RETROSHEET" in namespaces,
                "has_lahman_alias": "LAHMAN" in namespaces,
                "has_chadwick_alias": "CHADWICK" in namespaces,
                "has_mlbam_alias": "MLBAM" in namespaces,
                "has_baseball_reference_alias": "BASEBALL_REFERENCE" in namespaces,
                "has_player_prior": bool(priors),
                "minimum_player_prior_target_season": (
                    min(eligible_seasons) if eligible_seasons else None
                ),
                "maximum_player_prior_target_season": (
                    max(eligible_seasons) if eligible_seasons else None
                ),
                "batter_reference_count": player.plate_appearance_batter_references,
                "pitcher_reference_count": player.plate_appearance_pitcher_references,
                "other_player_reference_count": other,
                "player_prior_row_count": len(priors),
            }
        )
    return rows


def _bridge_file(
    logical_uri: str,
    payload: bytes,
    *,
    alias_artifact: ParquetArtifact,
    dimension_artifact: ParquetArtifact,
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


def _namespace_inventory(alias_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for namespace in bridge_trust.ALLOWED_IDENTITY_NAMESPACES:
        rows = [row for row in alias_rows if row["namespace"] == namespace]
        if not rows:
            continue
        output.append(
            {
                "namespace": namespace,
                "source_keys": sorted({str(row["source_key"]) for row in rows}),
                "source_version_ids": sorted(
                    {str(row["source_version_id"]) for row in rows}
                ),
                "alias_rows": len(rows),
                "unique_source_ids": len(
                    {str(row["source_player_id"]) for row in rows}
                ),
                "canonical_players": len(
                    {str(row["canonical_player_uuid"]) for row in rows}
                ),
            }
        )
    return output


def _build_join_evidence(
    parent: ParentIdentityEvidence, resolution_by_raw: Mapping[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    field_evidence = {
        (row.family, row.field, row.season): row
        for row in parent.field_season_evidence
    }
    coverage = {(row.retrosheet_player_id, row.family): row for row in parent.other_family_coverage}
    for family, field in _CONSUMER_JOIN_FIELDS:
        contract_id = f"{family}.{field}.retrosheet-to-canonical"
        seasons = tuple(
            season
            for season in (2021, 2022, 2023, 2024, 2025)
            if field_evidence[(family, field, season)].reference_count > 0
        )
        references = sum(
            field_evidence[(family, field, season)].reference_count for season in seasons
        )
        if family == "plate_appearance":
            raw_ids = {
                row.retrosheet_player_id
                for row in parent.plate_appearance_role_evidence
                if (field == "batter_id" and row.batter_references > 0)
                or (field == "pitcher_id" and row.pitcher_references > 0)
            }
        else:
            raw_ids = {
                raw_id
                for (raw_id, candidate_family), row in coverage.items()
                if candidate_family == family and field in row.fields
            }
        if not raw_ids or not raw_ids.issubset(resolution_by_raw):
            raise IdentityBridgeBuildError(f"Join scope differs for {family}.{field}")
        contracts.append(
            {
                "join_contract_id": contract_id,
                "source_family": family,
                "physical_field_name": field,
                "namespace": "RETROSHEET",
                "target_canonical_field": (
                    "player-identity-aliases.canonical_player_uuid"
                ),
                "join_type": "EXACT_PARENT_JOIN_KEY_TO_ALIAS",
                "null_policy": "REJECT_NULL_OR_BLANK",
                "duplicate_policy": "REQUIRE_ONE_TARGET_PER_NAMESPACE_SOURCE_ID",
                "unresolved_policy": "FAIL_CLOSED",
            }
        )
        validations.append(
            {
                "join_contract_id": contract_id,
                "seasons": list(seasons),
                "reference_count": references,
                "resolved_reference_count": references,
                "unresolved_reference_count": 0,
                "ambiguous_reference_count": 0,
                "multiply_mapped_reference_count": 0,
                "unique_source_ids": len(raw_ids),
                "unique_canonical_players": len(
                    {resolution_by_raw[value] for value in raw_ids}
                ),
                "mapping_rate": 1.0,
            }
        )

    prior_contract_id = "player_prior.canonical_player_id.canonical-to-dimension"
    prior_ids = {row.canonical_player_id for row in parent.player_priors}
    contracts.append(
        {
            "join_contract_id": prior_contract_id,
            "source_family": "player_prior",
            "physical_field_name": "canonical_player_id",
            "namespace": "CHADWICK",
            "target_canonical_field": (
                "active-player-dimension.canonical_player_uuid"
            ),
            "join_type": "EXACT_CANONICAL_UUID",
            "null_policy": "REJECT_NULL_OR_BLANK",
            "duplicate_policy": "ALLOW_MULTIPLE_PRIOR_ROWS_PER_CANONICAL_UUID",
            "unresolved_policy": "FAIL_CLOSED",
        }
    )
    validations.append(
        {
            "join_contract_id": prior_contract_id,
            "seasons": sorted({row.season for row in parent.player_priors}),
            "reference_count": len(parent.player_priors),
            "resolved_reference_count": len(parent.player_priors),
            "unresolved_reference_count": 0,
            "ambiguous_reference_count": 0,
            "multiply_mapped_reference_count": 0,
            "unique_source_ids": len(prior_ids),
            "unique_canonical_players": len(prior_ids),
            "mapping_rate": 1.0,
        }
    )
    contracts.sort(key=lambda row: row["join_contract_id"])
    validations.sort(key=lambda row: row["join_contract_id"])
    return contracts, validations


def _build_y_minus_1(
    parent: ParentIdentityEvidence, resolution_by_raw: Mapping[str, str]
) -> list[dict[str, Any]]:
    prior_by_domain: dict[str, dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in parent.player_priors:
        prior_by_domain[row.domain][row.canonical_player_id].add(row.season)
    pa = {(row.retrosheet_player_id, row.season): row for row in parent.plate_appearance_role_evidence}
    result: list[dict[str, Any]] = []
    for target_season in (2023, 2024, 2025):
        for role, field, domain in (
            ("BATTER", "batter_references", "BATTING"),
            ("PITCHER", "pitcher_references", "PITCHING"),
        ):
            total = 0
            eligible = 0
            for (raw_id, season), evidence in pa.items():
                if season != target_season:
                    continue
                count = int(getattr(evidence, field))
                total += count
                canonical = resolution_by_raw[raw_id]
                if any(
                    prior_season <= target_season - 1
                    for prior_season in prior_by_domain[domain].get(canonical, set())
                ):
                    eligible += count
            if total <= 0:
                raise IdentityBridgeBuildError("Plate-appearance role has no rows")
            missing = total - eligible
            result.append(
                {
                    "target_season": target_season,
                    "role": role,
                    "reference_rows": total,
                    "rows_with_eligible_prior": eligible,
                    "legitimate_missing_prior_rows": missing,
                    "maximum_eligible_prior_season": target_season - 1,
                    "eligible_prior_rate": eligible / total,
                    "unresolved_identity_rows": 0,
                    "ambiguous_identity_rows": 0,
                    "multiply_mapped_identity_rows": 0,
                }
            )
    return result


def _governed_evidence(project_root: Path) -> tuple[list[dict[str, Any]], Any]:
    evidence = load_fd1_evidence(project_root)
    by_id = {row.artifact_id: row for row in evidence.artifacts}
    artifacts: list[dict[str, Any]] = []
    for (
        artifact_id,
        logical_uri,
        expected_sha256,
        expected_byte_count,
    ) in bridge_trust.EXPECTED_EVIDENCE_ARTIFACTS:
        row = by_id.get(artifact_id)
        if row is None:
            raise IdentityBridgeBuildError("Finalized identity evidence is incomplete")
        if (row.sha256, row.byte_count) != (
            expected_sha256,
            expected_byte_count,
        ):
            raise IdentityBridgeBuildError(
                "Finalized identity evidence authority differs"
            )
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "logical_uri": logical_uri,
                "sha256": expected_sha256,
                "byte_count": expected_byte_count,
            }
        )
    return artifacts, evidence


def _cross_check_governed_evidence(
    parent: ParentIdentityEvidence,
    source: IdentityBridgeSourceResult,
    evidence: Any,
) -> None:
    report = evidence.active_identity["active_identity_coverage_report"]
    overall = report["overall_coverage"]
    primary = next(
        row
        for row in report["population_coverage"]
        if row["population"] == "GEN2_PRIMARY_2023_2025"
    )
    actual = (
        overall["unique_retrosheet_players"],
        overall["reference_count"],
        primary["unique_retrosheet_players"],
        primary["reference_count"],
        report["name_based_match_count"],
    )
    if actual != (2_693, 21_356_559, 2_071, 12_838_083, 0):
        raise IdentityBridgeBuildError("Finalized active identity evidence differs")
    quarantine = evidence.quarantine_impact["summary"]
    if (
        quarantine["canonical_mapping_rows_inserted"],
        quarantine["quarantine_rows"],
        quarantine["source_declared_unresolved_rows"],
        quarantine["source_declared_ambiguity_rows"],
        quarantine["blocking_conflicts"],
    ) != (736_056, 1_203, 1_187, 16, 0):
        raise IdentityBridgeBuildError("Finalized quarantine evidence differs")
    if (
        source.governed_evidence.canonical_mapping_rows != 736_056
        or parent.governed_reference_coverage.unique_player_count != 2_692
        or parent.governed_reference_coverage.primary_unique_player_count != 2_070
        or parent.governed_counts_reconcile
    ):
        raise IdentityBridgeBuildError("Portable/governed identity scope differs")


def _limitations() -> list[dict[str, str]]:
    return [
        {"limitation_id": key, "description": description, "scope": scope}
        for key, description, scope in bridge_trust.EXPECTED_LIMITATIONS
    ]


__all__ = [
    "BridgeBuildResult",
    "IdentityBridgeBuildError",
    "build_identity_bridge",
]
