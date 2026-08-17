"""Strict semantic contract for the MLB FD1 player identity companion package."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.gen2.exchange.contracts import validate_portable_uri
from app.gen2.exchange.identity_bridge import trust as bridge_trust
from app.gen2.exchange.identity_bridge.canonical import (
    CANONICALIZATION_VERSION,
    canonical_json_bytes,
    verify_bridge_manifest_identity,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")

IdentityNamespaceName = Literal[
    "RETROSHEET",
    "LAHMAN",
    "CHADWICK",
    "MLBAM",
    "BASEBALL_REFERENCE",
]


class BridgeContractError(ValueError):
    """The identity bridge is structurally or semantically inconsistent."""


def _require_sha256(value: str, label: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise BridgeContractError(f"{label} must be a lowercase SHA-256")


def _require_text(value: str, label: str) -> None:
    if not value or value.strip() != value:
        raise BridgeContractError(f"{label} must be nonempty trimmed text")


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise BridgeContractError(f"{label} is not a portable identifier")


def _require_canonical_uuid(value: str, label: str) -> None:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise BridgeContractError(f"{label} is not a UUID") from exc
    if str(parsed) != value:
        raise BridgeContractError(f"{label} is not a canonical lowercase UUID")


def _require_sorted_unique(values: Sequence[str], label: str) -> None:
    if tuple(values) != tuple(sorted(values)) or len(values) != len(set(values)):
        raise BridgeContractError(f"{label} must be sorted and unique")


class StrictModel(BaseModel):
    """Frozen strict JSON model used by every V1 bridge contract object."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
    ) -> Self:
        """Accept mappings through strict JSON mode without scalar coercion."""

        del strict
        if isinstance(obj, Mapping):
            return cls.model_validate_json(
                canonical_json_bytes(obj),
                strict=True,
                context=context,
            )
        return super().model_validate(
            obj,
            strict=True,
            from_attributes=from_attributes,
            context=context,
        )


class ParentDatasetBinding(StrictModel):
    parent_dataset_id: Literal["asp.mlb.open-data.fd1"]
    parent_dataset_version_id: str
    parent_dataset_manifest_id: str
    parent_dataset_manifest_sha256: str
    parent_partition_authority_sha256: str
    parent_package_id: str
    plate_appearance_class_order: tuple[str, ...]

    @model_validator(mode="after")
    def _frozen_parent(self) -> Self:
        expected = {
            "parent_dataset_id": bridge_trust.EXPECTED_PARENT_DATASET_ID,
            "parent_dataset_version_id": (
                bridge_trust.EXPECTED_PARENT_DATASET_VERSION_ID
            ),
            "parent_dataset_manifest_id": bridge_trust.EXPECTED_PARENT_MANIFEST_ID,
            "parent_dataset_manifest_sha256": (
                bridge_trust.EXPECTED_PARENT_MANIFEST_SHA256
            ),
            "parent_partition_authority_sha256": (
                bridge_trust.EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256
            ),
            "parent_package_id": bridge_trust.EXPECTED_PARENT_PACKAGE_ID,
        }
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise BridgeContractError(f"{field} differs from the frozen parent")
        if self.plate_appearance_class_order != bridge_trust.EXPECTED_PA_CLASS_ORDER:
            raise BridgeContractError(
                "Plate-appearance class order differs from the frozen parent"
            )
        return self


class ArtifactIdentity(StrictModel):
    artifact_id: str
    logical_uri: str
    sha256: str
    byte_count: int = Field(gt=0)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        _require_identifier(self.artifact_id, "artifact_id")
        validate_portable_uri(self.logical_uri)
        _require_sha256(self.sha256, "artifact sha256")
        return self


class SourceRelease(StrictModel):
    source_key: str
    source_version_id: str
    identity_checksum: str

    @model_validator(mode="after")
    def _valid(self) -> Self:
        _require_identifier(self.source_key, "source_key")
        _require_canonical_uuid(self.source_version_id, "source_version_id")
        _require_sha256(self.identity_checksum, "source release identity_checksum")
        return self


class SourceLineage(StrictModel):
    canonical_identity_mapping_count: int = Field(gt=0)
    evidence_artifacts: tuple[ArtifactIdentity, ...] = Field(min_length=2)
    source_releases: tuple[SourceRelease, ...] = Field(min_length=1)
    extraction_revision: str
    publishing_code_revision: str
    database_transaction_mode: Literal["READ_ONLY"]
    database_writes: Literal[0]
    provider_network_calls: Literal[0]
    external_network_calls: Literal[0]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        _require_sha256(self.extraction_revision, "extraction_revision")
        _require_sha256(self.publishing_code_revision, "publishing_code_revision")
        artifact_ids = [row.artifact_id for row in self.evidence_artifacts]
        artifact_uris = [row.logical_uri for row in self.evidence_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise BridgeContractError("Evidence artifact IDs must be unique")
        if len(artifact_uris) != len(set(uri.casefold() for uri in artifact_uris)):
            raise BridgeContractError("Evidence artifact URIs must be case-unique")
        release_ids = [
            (row.source_key, row.source_version_id) for row in self.source_releases
        ]
        if len(release_ids) != len(set(release_ids)):
            raise BridgeContractError("Source releases must be unique")
        if release_ids != sorted(release_ids):
            raise BridgeContractError("Source releases must use canonical order")
        return self


class IdentityNamespace(StrictModel):
    namespace: IdentityNamespaceName
    source_keys: tuple[str, ...] = Field(min_length=1)
    source_version_ids: tuple[str, ...] = Field(min_length=1)
    alias_rows: int = Field(gt=0)
    unique_source_ids: int = Field(gt=0)
    canonical_players: int = Field(gt=0)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        for value in self.source_keys:
            _require_identifier(value, "identity namespace source_key")
        for value in self.source_version_ids:
            _require_canonical_uuid(value, "identity namespace source_version_id")
        _require_sorted_unique(self.source_keys, "identity namespace source_keys")
        _require_sorted_unique(
            self.source_version_ids, "identity namespace source_version_ids"
        )
        if self.alias_rows != self.unique_source_ids:
            raise BridgeContractError(
                "One alias row is required for every unique namespace/source ID"
            )
        if self.canonical_players > self.alias_rows:
            raise BridgeContractError("Namespace canonical-player count exceeds aliases")
        return self


class BridgeFile(StrictModel):
    logical_uri: str
    media_type: Literal[
        "application/json",
        "text/markdown",
        "application/vnd.apache.parquet",
    ]
    sha256: str
    byte_count: int = Field(gt=0)
    row_count: int | None = Field(default=None, gt=0)
    physical_schema_fingerprint: str | None = None
    row_authority_sha256: str | None = None
    semantic_digest_member: Literal[True]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        validate_portable_uri(self.logical_uri)
        _require_sha256(self.sha256, "bridge file sha256")
        parquet = self.logical_uri.endswith(".parquet")
        if parquet:
            if self.media_type != "application/vnd.apache.parquet":
                raise BridgeContractError("Parquet bridge file has the wrong media type")
            if self.row_count is None:
                raise BridgeContractError("Parquet bridge file requires a row count")
            if self.physical_schema_fingerprint is None:
                raise BridgeContractError(
                    "Parquet bridge file requires a physical-schema fingerprint"
                )
            if self.row_authority_sha256 is None:
                raise BridgeContractError(
                    "Parquet bridge file requires a row-authority checksum"
                )
            _require_sha256(
                self.physical_schema_fingerprint,
                "physical_schema_fingerprint",
            )
            _require_sha256(self.row_authority_sha256, "row_authority_sha256")
        elif any(
            value is not None
            for value in (
                self.row_count,
                self.physical_schema_fingerprint,
                self.row_authority_sha256,
            )
        ):
            raise BridgeContractError(
                "Non-Parquet semantic files cannot declare row/schema authority"
            )
        elif self.logical_uri.endswith(".json") and self.media_type != "application/json":
            raise BridgeContractError("JSON bridge file has the wrong media type")
        elif self.logical_uri.endswith(".md") and self.media_type != "text/markdown":
            raise BridgeContractError("Markdown bridge file has the wrong media type")
        return self


class CoverageEvidence(StrictModel):
    canonical_identity_mapping_count: int = Field(gt=0)
    active_unique_players_2021_2025: int = Field(gt=0)
    primary_unique_players_2023_2025: int = Field(gt=0)
    portable_parent_unique_players_2021_2025: int = Field(gt=0)
    portable_parent_unique_players_2023_2025: int = Field(gt=0)
    unrepresented_governed_players_2021_2025: int = Field(ge=0)
    unrepresented_governed_players_2023_2025: int = Field(ge=0)
    active_player_references_2021_2025: int = Field(gt=0)
    primary_player_references_2023_2025: int = Field(gt=0)
    portable_parent_player_references_2021_2025: int = Field(gt=0)
    portable_parent_player_references_2023_2025: int = Field(gt=0)
    exact_unique_player_rate_2021_2025: float = Field(ge=0.0, le=1.0)
    exact_reference_rate_2021_2025: float = Field(ge=0.0, le=1.0)
    exact_unique_player_rate_2023_2025: float = Field(ge=0.0, le=1.0)
    exact_reference_rate_2023_2025: float = Field(ge=0.0, le=1.0)
    unresolved_quarantine_rows: int = Field(ge=0)
    ambiguous_quarantine_rows: int = Field(ge=0)
    downstream_impacted_rows: Literal[0]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.primary_unique_players_2023_2025 > self.active_unique_players_2021_2025:
            raise BridgeContractError("Primary unique players exceed all active players")
        if self.active_unique_players_2021_2025 != (
            self.portable_parent_unique_players_2021_2025
            + self.unrepresented_governed_players_2021_2025
        ):
            raise BridgeContractError(
                "Governed and portable 2021-2025 player counts do not reconcile"
            )
        if self.primary_unique_players_2023_2025 != (
            self.portable_parent_unique_players_2023_2025
            + self.unrepresented_governed_players_2023_2025
        ):
            raise BridgeContractError(
                "Governed and portable 2023-2025 player counts do not reconcile"
            )
        if (
            self.portable_parent_unique_players_2023_2025
            > self.portable_parent_unique_players_2021_2025
        ):
            raise BridgeContractError(
                "Portable primary players exceed all portable parent players"
            )
        if (
            self.primary_player_references_2023_2025
            > self.active_player_references_2021_2025
        ):
            raise BridgeContractError("Primary references exceed all active references")
        if (
            self.portable_parent_player_references_2021_2025
            > self.active_player_references_2021_2025
            or self.portable_parent_player_references_2023_2025
            > self.primary_player_references_2023_2025
        ):
            raise BridgeContractError(
                "Portable parent references exceed governed reference evidence"
            )
        rates = (
            self.exact_unique_player_rate_2021_2025,
            self.exact_reference_rate_2021_2025,
            self.exact_unique_player_rate_2023_2025,
            self.exact_reference_rate_2023_2025,
        )
        if any(not math.isclose(value, 1.0, abs_tol=1e-12) for value in rates):
            raise BridgeContractError("Active identity coverage must remain exactly 100%")
        return self


class JoinContract(StrictModel):
    join_contract_id: str
    source_family: str
    physical_field_name: str
    namespace: IdentityNamespaceName
    target_canonical_field: str
    join_type: str
    null_policy: str
    duplicate_policy: str
    unresolved_policy: str

    @model_validator(mode="after")
    def _valid(self) -> Self:
        _require_identifier(self.join_contract_id, "join_contract_id")
        for field in (
            "source_family",
            "physical_field_name",
            "target_canonical_field",
            "join_type",
            "null_policy",
            "duplicate_policy",
            "unresolved_policy",
        ):
            _require_text(str(getattr(self, field)), field)
        return self


class JoinValidation(StrictModel):
    join_contract_id: str
    seasons: tuple[int, ...] = Field(min_length=1)
    reference_count: int = Field(gt=0)
    resolved_reference_count: int = Field(ge=0)
    unresolved_reference_count: Literal[0]
    ambiguous_reference_count: Literal[0]
    multiply_mapped_reference_count: Literal[0]
    unique_source_ids: int = Field(gt=0)
    unique_canonical_players: int = Field(gt=0)
    mapping_rate: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        _require_identifier(self.join_contract_id, "join validation contract ID")
        if tuple(sorted(set(self.seasons))) != self.seasons:
            raise BridgeContractError("Join-validation seasons must be sorted and unique")
        if any(season < 2021 or season > 2025 for season in self.seasons):
            raise BridgeContractError("Join-validation season is outside 2021-2025")
        if self.resolved_reference_count != self.reference_count:
            raise BridgeContractError("Join-validation reference totals do not reconcile")
        if self.unique_canonical_players > self.unique_source_ids:
            raise BridgeContractError(
                "Join validation maps more canonical players than source IDs"
            )
        expected_rate = self.resolved_reference_count / self.reference_count
        if not math.isclose(self.mapping_rate, expected_rate, abs_tol=1e-12):
            raise BridgeContractError("Join-validation mapping rate does not reconcile")
        if not math.isclose(self.mapping_rate, 1.0, abs_tol=1e-12):
            raise BridgeContractError("Every active identity join must be exact")
        return self


class YMinus1PriorEligibility(StrictModel):
    target_season: Literal[2023, 2024, 2025]
    role: Literal["BATTER", "PITCHER"]
    reference_rows: int = Field(gt=0)
    rows_with_eligible_prior: int = Field(ge=0)
    legitimate_missing_prior_rows: int = Field(ge=0)
    maximum_eligible_prior_season: int
    eligible_prior_rate: float = Field(ge=0.0, le=1.0)
    unresolved_identity_rows: Literal[0]
    ambiguous_identity_rows: Literal[0]
    multiply_mapped_identity_rows: Literal[0]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if (
            self.rows_with_eligible_prior + self.legitimate_missing_prior_rows
            != self.reference_rows
        ):
            raise BridgeContractError("Y-minus-1 prior totals do not reconcile")
        if self.maximum_eligible_prior_season != self.target_season - 1:
            raise BridgeContractError("Y-minus-1 prior boundary is not chronological")
        expected_rate = self.rows_with_eligible_prior / self.reference_rows
        if not math.isclose(self.eligible_prior_rate, expected_rate, abs_tol=1e-12):
            raise BridgeContractError("Y-minus-1 eligibility rate does not reconcile")
        return self


class SourceRights(StrictModel):
    rights_control_ids: tuple[str, ...] = Field(min_length=1)
    allowed_purposes: tuple[str, ...]
    prohibited_purposes: tuple[str, ...]
    legal_conclusion: Literal["NO_NEW_LEGAL_CONCLUSIONS"]
    public_raw_bridge_download_authorized: Literal[False]
    public_raw_redistribution_authorized: Literal[False]
    identity_data_resale_authorized: Literal[False]
    unrestricted_commercial_redistribution_authorized: Literal[False]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        _require_sorted_unique(self.rights_control_ids, "rights_control_ids")
        if self.allowed_purposes != bridge_trust.PACKAGE_ALLOWED_PURPOSES:
            raise BridgeContractError("Bridge allowed purposes differ from V1")
        if self.prohibited_purposes != bridge_trust.PACKAGE_PROHIBITED_PURPOSES:
            raise BridgeContractError("Bridge prohibited purposes differ from V1")
        return self


class Limitation(StrictModel):
    limitation_id: str
    description: str
    scope: Literal["IDENTITY_BRIDGE"]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        _require_identifier(self.limitation_id, "limitation_id")
        _require_text(self.description, "limitation description")
        return self


class BridgeTotals(StrictModel):
    alias_rows: int = Field(gt=0)
    active_player_rows: int = Field(gt=0)
    namespace_count: int = Field(gt=0)
    join_contract_count: int = Field(gt=0)
    join_validation_count: int = Field(gt=0)
    y_minus_1_eligibility_records: Literal[6]
    semantic_file_count: Literal[4]
    semantic_file_bytes: int = Field(gt=0)
    final_package_file_count: Literal[7]
    alias_authority_sha256: str
    dimension_authority_sha256: str

    @model_validator(mode="after")
    def _valid(self) -> Self:
        _require_sha256(self.alias_authority_sha256, "alias_authority_sha256")
        _require_sha256(
            self.dimension_authority_sha256, "dimension_authority_sha256"
        )
        return self


class SportsIdentityBridgeManifestV1(StrictModel):
    contract_name: Literal["SPORTS_IDENTITY_BRIDGE"]
    contract_version: Literal["SPORTS_IDENTITY_BRIDGE_V1"]
    canonicalization_version: Literal["SPORTS_CANONICAL_JSON_V1"]
    bridge_manifest_id: str
    bridge_manifest_sha256: str
    bridge_package_id: str
    publisher: Literal["AI Sports Predictor"]
    publication_timestamp: datetime
    sport: Literal["Baseball"]
    league: Literal["MLB"]
    parent_binding: ParentDatasetBinding
    evidence_cutoff: str
    source_lineage: SourceLineage
    identity_namespaces: tuple[IdentityNamespace, ...] = Field(min_length=1)
    bridge_files: tuple[BridgeFile, ...]
    coverage_evidence: CoverageEvidence
    join_contracts: tuple[JoinContract, ...] = Field(min_length=1)
    join_validation: tuple[JoinValidation, ...] = Field(min_length=1)
    y_minus_1_prior_eligibility: tuple[YMinus1PriorEligibility, ...]
    source_rights: SourceRights
    limitations: tuple[Limitation, ...]
    totals: BridgeTotals

    @model_validator(mode="after")
    def _semantic_contract(self) -> Self:
        if (
            self.publication_timestamp.tzinfo is None
            or self.publication_timestamp.utcoffset() is None
        ):
            raise BridgeContractError("publication_timestamp must be timezone-aware")
        try:
            date.fromisoformat(self.evidence_cutoff)
        except ValueError as exc:
            raise BridgeContractError("evidence_cutoff is not an ISO calendar date") from exc
        if self.evidence_cutoff != bridge_trust.EVIDENCE_CUTOFF:
            raise BridgeContractError("evidence_cutoff differs from frozen source evidence")

        digest = verify_bridge_manifest_identity(self.model_dump(mode="json"))
        if (
            bridge_trust.EXPECTED_BRIDGE_MANIFEST_SHA256 is not None
            and digest != bridge_trust.EXPECTED_BRIDGE_MANIFEST_SHA256
        ):
            raise BridgeContractError("Bridge manifest differs from its release trust anchor")

        namespaces = [row.namespace for row in self.identity_namespaces]
        if len(namespaces) != len(set(namespaces)):
            raise BridgeContractError("Identity namespaces must be unique")
        expected_order = tuple(
            namespace
            for namespace in bridge_trust.ALLOWED_IDENTITY_NAMESPACES
            if namespace in namespaces
        )
        if tuple(namespaces) != expected_order:
            raise BridgeContractError("Identity namespaces are not in canonical order")
        if "RETROSHEET" not in namespaces:
            raise BridgeContractError("The required RETROSHEET namespace is absent")

        file_uris = tuple(row.logical_uri for row in self.bridge_files)
        if file_uris != bridge_trust.REQUIRED_SEMANTIC_BRIDGE_FILES:
            raise BridgeContractError(
                "Bridge semantic-file inventory is incomplete or unordered"
            )
        if len({uri.casefold() for uri in file_uris}) != len(file_uris):
            raise BridgeContractError("Bridge semantic-file URIs case-collide")
        files = {row.logical_uri: row for row in self.bridge_files}
        aliases = files["player-identity-aliases.parquet"]
        dimension = files["active-player-dimension.parquet"]
        if aliases.row_count != self.totals.alias_rows:
            raise BridgeContractError("Alias Parquet rows differ from bridge totals")
        if dimension.row_count != self.totals.active_player_rows:
            raise BridgeContractError("Dimension Parquet rows differ from bridge totals")
        if aliases.row_authority_sha256 != self.totals.alias_authority_sha256:
            raise BridgeContractError("Alias authority checksums do not reconcile")
        if dimension.row_authority_sha256 != self.totals.dimension_authority_sha256:
            raise BridgeContractError("Dimension authority checksums do not reconcile")
        if (
            bridge_trust.EXPECTED_ALIAS_AUTHORITY_SHA256 is not None
            and self.totals.alias_authority_sha256
            != bridge_trust.EXPECTED_ALIAS_AUTHORITY_SHA256
        ):
            raise BridgeContractError("Alias authority differs from its frozen anchor")
        if (
            bridge_trust.EXPECTED_DIMENSION_AUTHORITY_SHA256 is not None
            and self.totals.dimension_authority_sha256
            != bridge_trust.EXPECTED_DIMENSION_AUTHORITY_SHA256
        ):
            raise BridgeContractError("Dimension authority differs from its frozen anchor")

        if sum(row.alias_rows for row in self.identity_namespaces) != self.totals.alias_rows:
            raise BridgeContractError("Namespace alias counts do not reconcile")
        if self.totals.active_player_rows != (
            self.coverage_evidence.portable_parent_unique_players_2021_2025
        ):
            raise BridgeContractError(
                "Active-player dimension and portable parent coverage differ"
            )
        if self.source_lineage.canonical_identity_mapping_count != (
            self.coverage_evidence.canonical_identity_mapping_count
        ):
            raise BridgeContractError("Canonical mapping evidence does not reconcile")

        contract_ids = [row.join_contract_id for row in self.join_contracts]
        if len(contract_ids) != len(set(contract_ids)):
            raise BridgeContractError("Join-contract IDs must be unique")
        if contract_ids != sorted(contract_ids):
            raise BridgeContractError("Join contracts must use canonical ID order")
        validation_keys = [
            (row.join_contract_id, row.seasons) for row in self.join_validation
        ]
        if len(validation_keys) != len(set(validation_keys)):
            raise BridgeContractError("Join-validation records must be unique")
        validated_ids = {row.join_contract_id for row in self.join_validation}
        if validated_ids != set(contract_ids):
            raise BridgeContractError(
                "Every and only declared join contract must have validation"
            )

        eligibility_keys = [
            (row.target_season, row.role)
            for row in self.y_minus_1_prior_eligibility
        ]
        expected_eligibility = [
            (season, role)
            for season in (2023, 2024, 2025)
            for role in ("BATTER", "PITCHER")
        ]
        if eligibility_keys != expected_eligibility:
            raise BridgeContractError(
                "Y-minus-1 eligibility must contain the canonical six records"
            )

        limitation_ids = [row.limitation_id for row in self.limitations]
        if len(limitation_ids) != len(set(limitation_ids)):
            raise BridgeContractError("Limitation IDs must be unique")
        if limitation_ids != sorted(limitation_ids):
            raise BridgeContractError("Limitations must use canonical ID order")

        expected_totals = {
            "namespace_count": len(self.identity_namespaces),
            "join_contract_count": len(self.join_contracts),
            "join_validation_count": len(self.join_validation),
            "semantic_file_count": len(self.bridge_files),
            "semantic_file_bytes": sum(row.byte_count for row in self.bridge_files),
            "final_package_file_count": bridge_trust.FINAL_PACKAGE_FILE_COUNT,
            "y_minus_1_eligibility_records": len(
                self.y_minus_1_prior_eligibility
            ),
        }
        for field, expected in expected_totals.items():
            if getattr(self.totals, field) != expected:
                raise BridgeContractError(f"Bridge total {field} does not reconcile")
        return self


def manifest_json_schema() -> dict[str, Any]:
    """Return the Draft 2020-12 structural companion for the V1 contract."""

    schema = SportsIdentityBridgeManifestV1.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "https://ai-sports-predictor.local/contracts/"
        "SPORTS_IDENTITY_BRIDGE_V1.schema.json"
    )
    schema["title"] = bridge_trust.CONTRACT_VERSION
    return schema


__all__ = [
    "ArtifactIdentity",
    "BridgeContractError",
    "BridgeFile",
    "BridgeTotals",
    "CoverageEvidence",
    "IdentityNamespace",
    "IdentityNamespaceName",
    "JoinContract",
    "JoinValidation",
    "Limitation",
    "ParentDatasetBinding",
    "SourceLineage",
    "SourceRelease",
    "SourceRights",
    "SportsIdentityBridgeManifestV1",
    "StrictModel",
    "YMinus1PriorEligibility",
    "manifest_json_schema",
]
