"""Deterministic logical and physical schema registry for the FD1 exchange.

The recovery manifest is the authoritative inventory because it binds every
Parquet partition to an ordered physical schema and its fingerprint.  This
module deliberately projects no logical paths, filesystem timestamps, or
operator timestamps into the exchange registry.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any


SCHEMA_REGISTRY_NAME = "SPORTS_DATASET_SCHEMA_REGISTRY_V1"
SCHEMA_REGISTRY_VERSION = "sports_dataset_schema_registry_v1"
EXPECTED_PARTITION_COUNT = 329
EXPECTED_PHYSICAL_VARIANT_COUNT = 33
EXPECTED_LOGICAL_SCHEMAS: Mapping[str, str] = MappingProxyType({
    "bullpen_usage": "retrosheet_fd1_bullpen_usage_v6",
    "pitch_sequence": "retrosheet_fd1_pitch_sequence_v5",
    "plate_appearance": "retrosheet_fd1_plate_appearance_v5",
    "play_transition": "retrosheet_fd1_play_transition_v5",
    "player_game_batting": "retrosheet_fd1_player_game_batting_v5",
    "player_game_fielding": "retrosheet_fd1_player_game_fielding_v5",
    "player_game_pitching": "retrosheet_fd1_player_game_pitching_v5",
    "player_prior": "gate3c_fd1_identity_prior_v1",
    "starter_usage": "retrosheet_fd1_starter_usage_v6",
    "weather": "retrosheet_fd1_weather_v8",
})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PhysicalFields = tuple[tuple[str, str], ...]
_GOVERNANCE_MARKERS = (
    "available",
    "available_only",
    "eligible",
    "evidence_mode",
    "future_evidence",
    "historical_reconstruction",
    "population",
    "source_mode",
    "strict_",
    "target_only_not_pregame_feature",
    "timestamp_known",
    "semantics",
    "temporal_semantics",
    "time_known",
    "timing_",
    "uncertainty",
)
_PROVENANCE_MARKERS = (
    "checksum",
    "filename",
    "logical_table",
    "logical_uri",
    "row_number",
    "sha256",
    "source_release",
)
_PROVENANCE_FIELD_NAMES = frozenset({"source_key", "source_version_id"})
_TARGET_NAMES = frozenset(
    {
        "actual_appearance_target",
        "actual_batting_order",
        "actual_fielding_positions",
        "actual_start_flag",
        "actual_starter_target",
        "home_score_after",
        "outcome",
        "outs_after",
        "pa_outcome",
        "runs_scored",
        "source_terminal_token",
        "terminal_play_id",
        "terminal_play_number",
        "visiting_score_after",
    }
)
_DESCRIPTION_OVERRIDES: Mapping[str, str] = {
    "event_date": "Governed event date assigned to the record.",
    "game_date": "Official date of the represented MLB game.",
    "official_date": "Chronological as-of date for the prior-period record.",
    "season": "MLB season assigned by the governed materialization.",
    "month": "Calendar month used by the governed partition.",
    "game_id": "Canonical ASP identifier for the represented MLB game.",
    "player_id": "Canonical ASP identifier for the represented player.",
    "batter_id": "Canonical ASP identifier for the batter.",
    "pitcher_id": "Canonical ASP identifier for the pitcher.",
    "plate_appearance_id": "Canonical identifier for the plate appearance.",
    "play_id": "Canonical identifier for the source play transition.",
    "row_checksum": "Checksum binding the complete canonical row payload.",
    "source_row_checksum": "Checksum binding the contributing source row.",
    "source_file_sha256": "SHA-256 digest of the contributing source file.",
    "source_key": "Governed source-registry key for the record.",
    "source_version_id": "Immutable source-release identifier for the record.",
    "identity_namespace": "Namespace used to interpret canonical identifiers.",
    "timing_risk_label": "Explicit temporal-risk label assigned by governance.",
    "target_only_not_pregame_feature": (
        "Guard marking retrospective outcome data as target-only evidence."
    ),
}


class SchemaRegistryError(ValueError):
    """The recovery manifest cannot yield the frozen exchange schema registry."""


def build_schema_registry(recovery_manifest: object) -> dict[str, Any]:
    """Build and validate the deterministic ten-family FD1 schema registry.

    ``recovery_manifest`` may be the typed ``RecoveryManifest`` or its decoded
    canonical-JSON mapping.  Every variant is addressed by the SHA-256 of its
    ordered ``[{"name": ..., "dtype": ...}]`` physical field document.
    """

    files = _sequence(_value(recovery_manifest, "files"), "recovery manifest files")
    aggregate = _value(recovery_manifest, "aggregate")
    aggregate_file_count = _positive_int(
        _value(aggregate, "file_count"), "recovery aggregate file_count"
    )
    if len(files) != EXPECTED_PARTITION_COUNT or aggregate_file_count != len(files):
        raise SchemaRegistryError(
            f"Schema registry requires exactly {EXPECTED_PARTITION_COUNT} partitions"
        )

    variants: dict[str, dict[str, tuple[_PhysicalFields, int]]] = {}
    declared_by_family: dict[str, set[str]] = {}
    global_fields_by_fingerprint: dict[str, tuple[tuple[str, str], ...]] = {}
    family_partition_counts: Counter[str] = Counter()

    for entry in files:
        family = _text(_value(entry, "family"), "partition family")
        if family not in EXPECTED_LOGICAL_SCHEMAS:
            raise SchemaRegistryError(f"Unknown FD1 schema family: {family!r}")
        declared_schema = _text(
            _value(entry, "declared_schema"), "declared schema version"
        )
        fingerprint = _text(
            _value(entry, "physical_schema_fingerprint"),
            "physical schema fingerprint",
        )
        if _SHA256.fullmatch(fingerprint) is None:
            raise SchemaRegistryError("Physical schema fingerprint is not SHA-256")

        fields = _physical_fields(_value(entry, "physical_schema"))
        calculated = _canonical_sha256(
            [{"name": name, "dtype": dtype} for name, dtype in fields]
        )
        if fingerprint != calculated:
            raise SchemaRegistryError(
                f"Physical schema fingerprint mismatch for family {family!r}"
            )
        prior_global = global_fields_by_fingerprint.setdefault(fingerprint, fields)
        if prior_global != fields:
            raise SchemaRegistryError(
                "One physical fingerprint is associated with inconsistent field lists"
            )

        family_variants = variants.setdefault(family, {})
        prior = family_variants.get(fingerprint)
        if prior is None:
            family_variants[fingerprint] = (fields, 1)
        else:
            prior_fields, compatible_count = prior
            if prior_fields != fields:
                raise SchemaRegistryError(
                    "One family variant has inconsistent ordered physical fields"
                )
            family_variants[fingerprint] = (prior_fields, compatible_count + 1)
        declared_by_family.setdefault(family, set()).add(declared_schema)
        family_partition_counts[family] += 1

    if set(variants) != set(EXPECTED_LOGICAL_SCHEMAS):
        raise SchemaRegistryError("Schema registry does not contain exactly ten FD1 families")
    variant_count = sum(len(items) for items in variants.values())
    if variant_count != EXPECTED_PHYSICAL_VARIANT_COUNT:
        raise SchemaRegistryError(
            "Schema registry physical variant count differs from the frozen FD1 corpus"
        )
    if sum(family_partition_counts.values()) != EXPECTED_PARTITION_COUNT:
        raise SchemaRegistryError("Schema registry partition counts do not reconcile")
    _validate_aggregate_family_counts(aggregate, family_partition_counts)

    schemas: list[dict[str, Any]] = []
    for family in sorted(EXPECTED_LOGICAL_SCHEMAS):
        expected_declared = EXPECTED_LOGICAL_SCHEMAS[family]
        if declared_by_family.get(family) != {expected_declared}:
            raise SchemaRegistryError(
                f"Family {family!r} does not use its frozen logical schema version"
            )
        physical_variants = []
        for fingerprint, (fields, compatible_count) in sorted(variants[family].items()):
            physical_variants.append(
                {
                    "physical_schema_version": (
                        f"{expected_declared}+physical-sha256-{fingerprint}"
                    ),
                    "physical_schema_fingerprint": fingerprint,
                    "compatible_partition_count": compatible_count,
                    "fields": [
                        expected_field_contract(family=family, name=name, dtype=dtype)
                        for name, dtype in fields
                    ],
                }
            )
        schemas.append(
            {
                "family": family,
                "logical_schema_version": expected_declared,
                "partition_count": family_partition_counts[family],
                "physical_variant_count": len(physical_variants),
                "physical_variants": physical_variants,
            }
        )

    return {
        "schema_name": SCHEMA_REGISTRY_NAME,
        "schema_version": SCHEMA_REGISTRY_VERSION,
        "nullability_policy": "CONSERVATIVE_ALL_PARQUET_FIELDS_NULLABLE",
        "family_count": len(schemas),
        "physical_variant_count": variant_count,
        "partition_count": sum(row["partition_count"] for row in schemas),
        "families": schemas,
    }


def _physical_fields(value: object) -> tuple[tuple[str, str], ...]:
    rows = _sequence(value, "physical schema")
    if not rows:
        raise SchemaRegistryError("A physical schema cannot be empty")
    fields = tuple(
        (
            _text(_value(row, "name"), "physical field name"),
            _text(_value(row, "dtype"), "physical field dtype"),
        )
        for row in rows
    )
    names = [name for name, _dtype in fields]
    if len(names) != len(set(names)):
        raise SchemaRegistryError("A physical schema repeats a field name")
    return fields


def _field_contract(*, family: str, name: str, dtype: str) -> dict[str, Any]:
    classification = _field_classification(name)
    return {
        "name": name,
        "dtype": dtype,
        # Polars' recovery schema does not retain Parquet required/optional
        # repetition levels.  Treating every field as nullable is the only
        # fail-safe consumer contract; it never over-promises non-null values.
        "nullable": True,
        "semantic_description": _semantic_description(family, name, dtype),
        "target_feature_identity_classification": classification,
        "temporal_risk_classification": _temporal_risk(family, name, classification),
        "source_field_lineage": list(_source_lineage(family, name)),
    }


def expected_field_contract(*, family: str, name: str, dtype: str) -> dict[str, Any]:
    """Return the exact V1 metadata contract for one physical field."""

    if family not in EXPECTED_LOGICAL_SCHEMAS:
        raise SchemaRegistryError(f"Unknown FD1 schema family: {family!r}")
    if not isinstance(name, str) or not name or not isinstance(dtype, str) or not dtype:
        raise SchemaRegistryError("Physical field name/dtype must be non-empty text")
    return _field_contract(family=family, name=name, dtype=dtype)


def _field_classification(name: str) -> str:
    if any(marker in name for marker in _GOVERNANCE_MARKERS):
        return "GOVERNANCE"
    if name in _PROVENANCE_FIELD_NAMES or any(
        marker in name for marker in _PROVENANCE_MARKERS
    ):
        return "PROVENANCE"
    if (
        name in _TARGET_NAMES
        or name.startswith(("actual_", "stat_"))
        or name.endswith("_target")
    ):
        return "TARGET"
    if (
        name.endswith("_id")
        or name.endswith("_ids")
        or name.startswith("canonical_")
        or "identity_" in name
    ):
        return "IDENTITY"
    return "FEATURE"


def _temporal_risk(family: str, name: str, classification: str) -> str:
    if classification == "TARGET":
        return "POSTGAME_TARGET_ONLY"
    if classification == "GOVERNANCE":
        return "EXPLICIT_TIMING_OR_ELIGIBILITY_GUARD"
    if classification == "PROVENANCE":
        return "PROVENANCE_ONLY"
    if classification == "IDENTITY":
        return "IDENTITY_METADATA"
    if family == "player_prior":
        return "PRIOR_PERIOD_FEATURE"
    if name in {"season", "month", "event_date", "game_date", "official_date"}:
        return "EVENT_TIME_CONTEXT"
    return "RETROSPECTIVE_ACTUAL_NOT_PREGAME_SAFE"


def _semantic_description(family: str, name: str, dtype: str) -> str:
    if name in _DESCRIPTION_OVERRIDES:
        return _DESCRIPTION_OVERRIDES[name]
    label = name.replace("_", " ")
    if name.startswith("stat_"):
        return f"Source-reported retrospective statistic: {label}."
    if name.endswith("_checksum") or name.endswith("_sha256"):
        return f"Integrity digest binding {label}."
    if name.endswith("_id"):
        return f"Governed identifier for {label.removesuffix(' id')}."
    if name.endswith("_ids"):
        return f"Governed identifier collection for {label.removesuffix(' ids')}."
    if dtype == "Boolean":
        return f"Boolean indicator for {label}."
    if dtype.startswith("List("):
        return f"Ordered value collection for {label}."
    return f"Governed {family} value representing {label}."


def _source_lineage(family: str, name: str) -> tuple[str, ...]:
    if family == "player_prior":
        if name.startswith("canonical_") or "identity_" in name:
            return ("SABR_LAHMAN_BOX_DOWNLOADS", "ASP_CANONICALIZATION")
        return ("SABR_LAHMAN_BOX_DOWNLOADS", "ASP_PRIOR_DERIVATION")
    if name.startswith("retrosheet_"):
        return ("RETROSHEET_CSV_DOWNLOADS",)
    if name.startswith("manager_evidence_"):
        return ("RETROSHEET_AUXILIARY_DOWNLOADS",)
    if name.startswith("stat_"):
        return ("RETROSHEET_CSV_DOWNLOADS",)
    if name.startswith("source_"):
        return ("RETROSHEET_CSV_DOWNLOADS", "GOVERNED_SOURCE_MANIFEST")
    if name.startswith("canonical_") or "identity_" in name:
        return (
            "CHADWICK_REGISTER_ARCHIVES",
            "RETROSHEET_CSV_DOWNLOADS",
            "ASP_CANONICALIZATION",
        )
    if name.startswith("noaa_"):
        return ("DERIVED_GAP_FILL_POLICY",)
    return ("RETROSHEET_CSV_DOWNLOADS", "ASP_FD1_DERIVATION")


def _validate_aggregate_family_counts(
    aggregate: object, expected: Mapping[str, int]
) -> None:
    family_rows = _sequence(_value(aggregate, "families"), "aggregate families")
    actual: dict[str, int] = {}
    for row in family_rows:
        family = _text(_value(row, "family"), "aggregate family")
        if family in actual:
            raise SchemaRegistryError("Recovery aggregate repeats a family")
        actual[family] = _positive_int(
            _value(row, "file_count"), "aggregate family file_count"
        )
    if actual != dict(expected):
        raise SchemaRegistryError("Recovery aggregate family counts do not reconcile")


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _value(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        if name not in value:
            raise SchemaRegistryError(f"Missing schema-registry input field: {name}")
        return value[name]
    try:
        return getattr(value, name)
    except AttributeError as exc:
        raise SchemaRegistryError(
            f"Missing schema-registry input attribute: {name}"
        ) from exc


def _sequence(value: object, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(
        value, Sequence
    ):
        raise SchemaRegistryError(f"{label} must be an ordered sequence")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SchemaRegistryError(f"{label} must be nonblank canonical text")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SchemaRegistryError(f"{label} must be a positive integer")
    return value


__all__ = [
    "EXPECTED_LOGICAL_SCHEMAS",
    "EXPECTED_PARTITION_COUNT",
    "EXPECTED_PHYSICAL_VARIANT_COUNT",
    "SCHEMA_REGISTRY_NAME",
    "SCHEMA_REGISTRY_VERSION",
    "SchemaRegistryError",
    "build_schema_registry",
    "expected_field_contract",
]
