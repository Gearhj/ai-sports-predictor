"""Package-only validator for an immutable sports dataset exchange.

This module intentionally has no import path to ASP settings, databases,
providers, raw data, the FD1 source store, or the publisher implementation.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import polars as pl

from app.gen2.exchange.canonical_json import (
    CanonicalJsonError,
    canonical_json_bytes,
    canonical_sha256,
    load_canonical_json,
    verify_manifest_identity,
)
from app.gen2.exchange.contracts import (
    EXPECTED_FILE_COUNT,
    EXPECTED_PARQUET_BYTES,
    EXPECTED_POPULATIONS,
    EXPECTED_ROW_COUNT,
    Predicate,
    SportsDatasetManifestV1,
)
from app.gen2.exchange.filesystem import (
    ExchangePathError,
    assert_regular_independent_file,
    governed_root,
    iter_relative_files,
    resolve_under,
    sha256_file,
)
from app.gen2.exchange.package_metadata import normalized_markdown_bytes, readme_bytes
from app.gen2.exchange.schema_registry import (
    EXPECTED_LOGICAL_SCHEMAS,
    EXPECTED_PHYSICAL_VARIANT_COUNT,
    SCHEMA_REGISTRY_NAME,
    SCHEMA_REGISTRY_VERSION,
    expected_field_contract,
)
from app.gen2.exchange.source_rights import (
    build_attribution_markdown,
    build_source_rights,
)


VALIDATION_REPORT_VERSION = "SPORTS_DATASET_VALIDATION_REPORT_V1"
REQUIRED_METADATA_FILES = frozenset(
    {
        "README.md",
        "attribution.md",
        "schema-registry.json",
        "source-rights.json",
        "sports-dataset-manifest.json",
        "validation-report.json",
    }
)


class ExchangeValidationError(RuntimeError):
    """The package is incomplete, corrupted, ambiguous, or non-portable."""


class ExchangeValidationDeadline(ExchangeValidationError):
    def __init__(self, progress: Mapping[str, Any]):
        super().__init__("Exchange validation reached its safe runtime bound")
        self.progress = dict(progress)


@dataclass(frozen=True, slots=True)
class ValidationProgress:
    stage: str
    elapsed_seconds: float
    files_checked: int
    files_total: int
    bytes_checked: int
    bytes_total: int
    current_family: str | None
    hash_mismatches: int
    schema_mismatches: int


@dataclass(frozen=True, slots=True)
class ValidationResult:
    package_root: Path
    manifest_id: str
    manifest_sha256: str
    report: dict[str, Any]
    elapsed_seconds: float


def validate_sports_dataset_exchange(
    package_root: Path,
    *,
    heartbeat_seconds: int = 30,
    max_runtime_minutes: float = 90,
    require_auxiliary_files: bool = True,
    progress_callback: Callable[[ValidationProgress], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ValidationResult:
    """Validate a package without consulting any ASP source or operational state."""

    if isinstance(heartbeat_seconds, bool) or not 1 <= heartbeat_seconds <= 30:
        raise ValueError("heartbeat_seconds must be between 1 and 30")
    if isinstance(max_runtime_minutes, bool) or max_runtime_minutes <= 0:
        raise ValueError("max_runtime_minutes must be positive")
    root = governed_root(package_root)
    started = clock()
    deadline = started + max_runtime_minutes * 60
    last_emit = started - heartbeat_seconds
    files_checked = 0
    bytes_checked = 0
    current_family: str | None = None
    hash_mismatches = 0
    schema_mismatches = 0

    def snapshot(stage: str) -> ValidationProgress:
        return ValidationProgress(
            stage=stage,
            elapsed_seconds=max(clock() - started, 0.0),
            files_checked=files_checked,
            files_total=EXPECTED_FILE_COUNT,
            bytes_checked=bytes_checked,
            bytes_total=EXPECTED_PARQUET_BYTES,
            current_family=current_family,
            hash_mismatches=hash_mismatches,
            schema_mismatches=schema_mismatches,
        )

    def emit(stage: str, *, force: bool = False) -> None:
        nonlocal last_emit
        now = clock()
        if now >= deadline:
            raise ExchangeValidationDeadline(asdict(snapshot(stage)))
        if progress_callback is not None and (force or now - last_emit >= heartbeat_seconds):
            progress_callback(snapshot(stage))
            last_emit = now

    emit("LOADING_CONTRACT", force=True)
    manifest_path = resolve_under(
        root, "sports-dataset-manifest.json", require_exists=True, require_file=True
    )
    assert_regular_independent_file(manifest_path)
    try:
        raw_manifest = load_canonical_json(manifest_path)
        if not isinstance(raw_manifest, dict):
            raise ExchangeValidationError("Manifest must be a JSON object")
        manifest = SportsDatasetManifestV1.model_validate(raw_manifest)
        digest = verify_manifest_identity(raw_manifest)
    except (CanonicalJsonError, ValueError) as exc:
        raise ExchangeValidationError("Sports dataset manifest is invalid") from exc

    schema_registry = _load_object(root, "schema-registry.json")
    source_rights = _load_object(root, "source-rights.json")
    _validate_semantic_artifacts(root, manifest)
    _validate_schema_registry_shape(schema_registry, manifest)
    _validate_source_rights(source_rights, manifest)
    _validate_exact_attribution(root)

    declared_files = {
        "attribution.md",
        "schema-registry.json",
        "source-rights.json",
        "sports-dataset-manifest.json",
        *(row.package_relative_uri for row in manifest.partitions),
    }
    if require_auxiliary_files:
        declared_files.update({"README.md", "validation-report.json"})
    actual_files: set[str] = set()
    for relative, path in iter_relative_files(root):
        # URI confinement rejects links; link-count validation additionally
        # prevents every package file (including metadata) from aliasing mutable
        # bytes outside the immutable exchange.
        assert_regular_independent_file(path)
        actual_files.add(relative)
    if actual_files != declared_files:
        missing = sorted(declared_files - actual_files)
        extra = sorted(actual_files - declared_files)
        raise ExchangeValidationError(
            f"Package file inventory differs: missing={missing}, extra={extra}"
        )

    variants, expected_variant_counts = _schema_variants(schema_registry)
    observed_variant_counts: Counter[tuple[str, str]] = Counter()
    population_counts: Counter[str] = Counter()
    family_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"files": 0, "rows": 0, "parquet_bytes": 0}
    )
    season_totals: dict[int, dict[str, int]] = defaultdict(
        lambda: {"files": 0, "rows": 0, "parquet_bytes": 0}
    )
    predicates = {row.population_id: row.row_selection for row in manifest.populations}
    emit("VALIDATING_PARTITIONS", force=True)
    for partition in manifest.partitions:
        current_family = partition.family
        path = resolve_under(
            root,
            partition.package_relative_uri,
            require_exists=True,
            require_file=True,
        )
        info = assert_regular_independent_file(path)
        if info.st_size != partition.byte_count:
            raise ExchangeValidationError(
                f"Parquet byte count changed: {partition.package_relative_uri}"
            )
        actual_hash = sha256_file(path, on_chunk=lambda count: _noop(count))
        bytes_checked += info.st_size
        if actual_hash != partition.file_sha256:
            hash_mismatches += 1
            raise ExchangeValidationError(
                f"Parquet checksum changed: {partition.package_relative_uri}"
            )
        try:
            schema = pl.read_parquet_schema(path)
        except Exception as exc:
            raise ExchangeValidationError(
                f"Parquet footer is unreadable: {partition.package_relative_uri}"
            ) from exc
        fields = tuple({"name": name, "dtype": str(dtype)} for name, dtype in schema.items())
        fingerprint = canonical_sha256(list(fields))
        declared_fields = tuple(row.model_dump(mode="json") for row in partition.physical_schema)
        if fingerprint != partition.physical_schema_fingerprint or fields != declared_fields:
            schema_mismatches += 1
            raise ExchangeValidationError(
                f"Parquet schema changed: {partition.package_relative_uri}"
            )
        registry_fields = variants.get((partition.family, fingerprint))
        if registry_fields != fields:
            schema_mismatches += 1
            raise ExchangeValidationError(
                f"Parquet schema is not declared by the registry: {partition.package_relative_uri}"
            )
        observed_variant_counts[(partition.family, fingerprint)] += 1
        row_count, minimum, maximum, counts, membership_errors = _partition_row_evidence(
            path,
            schema,
            partition.family,
            partition.season,
            predicates,
        )
        if (
            row_count != partition.row_count
            or minimum != partition.minimum_date
            or maximum != partition.maximum_date
        ):
            raise ExchangeValidationError(
                f"Parquet row/date metadata changed: {partition.package_relative_uri}"
            )
        if membership_errors or sum(counts.values()) != row_count:
            raise ExchangeValidationError(
                f"Population predicates are not exhaustive/disjoint: {partition.package_relative_uri}"
            )
        population_counts.update(counts)
        family = family_totals[partition.family]
        family["files"] += 1
        family["rows"] += row_count
        family["parquet_bytes"] += info.st_size
        season = season_totals[partition.season]
        season["files"] += 1
        season["rows"] += row_count
        season["parquet_bytes"] += info.st_size
        files_checked += 1
        emit("VALIDATING_PARTITIONS")

    if dict(population_counts) != EXPECTED_POPULATIONS:
        raise ExchangeValidationError(
            f"Population totals differ: {dict(population_counts)}"
        )
    if dict(observed_variant_counts) != expected_variant_counts:
        raise ExchangeValidationError(
            "Observed physical-schema frequencies differ from the registry"
        )
    expected_families = {
        row.family_id: {
            "files": row.file_count,
            "rows": row.row_count,
            "parquet_bytes": row.byte_count,
        }
        for row in manifest.data_families
    }
    if dict(family_totals) != expected_families:
        raise ExchangeValidationError("Family aggregates do not reconcile")
    totals = {
        "parquet_files": files_checked,
        "rows": sum(row["rows"] for row in family_totals.values()),
        "parquet_bytes": sum(row["parquet_bytes"] for row in family_totals.values()),
    }
    if totals != manifest.totals:
        raise ExchangeValidationError("Package totals do not reconcile")

    report = {
        "schema_version": VALIDATION_REPORT_VERSION,
        "status": "VALID",
        "contract_version": manifest.contract_version,
        "manifest_id": manifest.manifest_id,
        "manifest_sha256": digest,
        "package_id": manifest.package_id,
        "package_total_files": EXPECTED_FILE_COUNT + len(REQUIRED_METADATA_FILES),
        "parquet_files_checked": files_checked,
        "totals": totals,
        "population_counts": dict(sorted(population_counts.items())),
        "family_totals": dict(sorted(family_totals.items())),
        "season_totals": {
            str(season): values for season, values in sorted(season_totals.items())
        },
        "checks": {
            "attribution_present": True,
            "canonical_manifest_digest": True,
            "file_inventory_exact": True,
            "partition_hashes": True,
            "partition_rows": True,
            "partition_schemas": True,
            "population_predicates": True,
            "portable_relative_uris": True,
            "source_rights_references": True,
            "target_definitions": True,
            "temporal_contract": True,
            "limitation_inventory": True,
        },
        "database_accessed": False,
        "source_runtime_accessed": False,
        "provider_network_calls": 0,
    }
    if require_auxiliary_files:
        _validate_auxiliary_files(root, report, manifest)
    emit("PACKAGE_VALIDATED", force=True)
    return ValidationResult(
        package_root=root,
        manifest_id=manifest.manifest_id,
        manifest_sha256=digest,
        report=report,
        elapsed_seconds=max(clock() - started, 0.0),
    )


def _load_object(root: Path, uri: str) -> dict[str, Any]:
    try:
        path = resolve_under(root, uri, require_exists=True, require_file=True)
        assert_regular_independent_file(path)
        value = load_canonical_json(path)
    except CanonicalJsonError as exc:
        raise ExchangeValidationError(f"{uri} is not canonical JSON") from exc
    if not isinstance(value, dict):
        raise ExchangeValidationError(f"{uri} must contain a JSON object")
    return value


def _validate_semantic_artifacts(
    root: Path, manifest: SportsDatasetManifestV1
) -> None:
    expected = {
        row.logical_uri: (row.sha256, row.byte_count)
        for row in manifest.package_artifacts
    }
    if set(expected) != {"attribution.md", "schema-registry.json", "source-rights.json"}:
        raise ExchangeValidationError("Semantic package artifact inventory is invalid")
    for uri, (checksum, byte_count) in expected.items():
        path = resolve_under(root, uri, require_exists=True, require_file=True)
        info = assert_regular_independent_file(path)
        if info.st_size != byte_count or sha256_file(path) != checksum:
            raise ExchangeValidationError(f"Semantic package artifact changed: {uri}")


def _validate_schema_registry_shape(
    registry: Mapping[str, Any], manifest: SportsDatasetManifestV1
) -> None:
    if set(registry) != {
        "schema_name",
        "schema_version",
        "nullability_policy",
        "family_count",
        "physical_variant_count",
        "partition_count",
        "families",
    }:
        raise ExchangeValidationError("Schema registry root fields are invalid")
    if (
        registry.get("schema_name") != SCHEMA_REGISTRY_NAME
        or registry.get("schema_version") != SCHEMA_REGISTRY_VERSION
        or registry.get("nullability_policy")
        != "CONSERVATIVE_ALL_PARQUET_FIELDS_NULLABLE"
    ):
        raise ExchangeValidationError("Schema registry version is invalid")
    families = registry.get("families")
    if not isinstance(families, list) or len(families) != 10:
        raise ExchangeValidationError("Schema registry must contain ten families")
    if (
        registry.get("family_count") != len(EXPECTED_LOGICAL_SCHEMAS)
        or registry.get("partition_count") != EXPECTED_FILE_COUNT
        or registry.get("physical_variant_count") != EXPECTED_PHYSICAL_VARIANT_COUNT
    ):
        raise ExchangeValidationError("Schema registry counts are invalid")
    names = [row.get("family") for row in families if isinstance(row, dict)]
    if names != [row.family_id for row in manifest.data_families]:
        raise ExchangeValidationError("Schema registry families differ from the manifest")
    for row, family_manifest in zip(families, manifest.data_families, strict=True):
        if set(row) != {
            "family",
            "logical_schema_version",
            "partition_count",
            "physical_variant_count",
            "physical_variants",
        }:
            raise ExchangeValidationError("Schema-family metadata fields are invalid")
        variants = row.get("physical_variants")
        if (
            row.get("logical_schema_version")
            != EXPECTED_LOGICAL_SCHEMAS[row["family"]]
            or row.get("logical_schema_version") != family_manifest.schema_id
            or type(row.get("partition_count")) is not int
            or type(row.get("physical_variant_count")) is not int
            or row.get("partition_count") != family_manifest.file_count
            or not isinstance(variants, list)
            or row.get("physical_variant_count") != len(variants)
        ):
            raise ExchangeValidationError("Schema-family authority/counts are invalid")


def _schema_variants(
    registry: Mapping[str, Any],
) -> tuple[
    dict[tuple[str, str], tuple[dict[str, str], ...]],
    dict[tuple[str, str], int],
]:
    result: dict[tuple[str, str], tuple[dict[str, str], ...]] = {}
    counts: dict[tuple[str, str], int] = {}
    total = 0
    for family in registry["families"]:
        name = family["family"]
        counted = 0
        variants = family.get("physical_variants")
        if not isinstance(variants, list) or not variants:
            raise ExchangeValidationError("Schema family has no physical versions")
        for variant in variants:
            if not isinstance(variant, dict) or set(variant) != {
                "physical_schema_version",
                "physical_schema_fingerprint",
                "compatible_partition_count",
                "fields",
            }:
                raise ExchangeValidationError("Schema variant metadata fields are invalid")
            fingerprint = variant["physical_schema_fingerprint"]
            raw_fields = variant.get("fields")
            if not isinstance(raw_fields, list) or not raw_fields:
                raise ExchangeValidationError("Schema variant fields are invalid")
            fields_list: list[dict[str, str]] = []
            names: set[str] = set()
            for row in raw_fields:
                if not isinstance(row, dict):
                    raise ExchangeValidationError("Schema field metadata is invalid")
                name_value = row.get("name")
                dtype_value = row.get("dtype")
                if not isinstance(name_value, str) or not isinstance(dtype_value, str):
                    raise ExchangeValidationError("Schema field name/dtype is invalid")
                try:
                    expected = expected_field_contract(
                        family=name, name=name_value, dtype=dtype_value
                    )
                except ValueError as exc:
                    raise ExchangeValidationError(
                        "Schema field metadata cannot be derived"
                    ) from exc
                if row != expected or name_value in names:
                    raise ExchangeValidationError("Schema field metadata differs from V1")
                names.add(name_value)
                fields_list.append({"name": name_value, "dtype": dtype_value})
            fields = tuple(fields_list)
            if canonical_sha256(list(fields)) != fingerprint:
                raise ExchangeValidationError("Schema registry fingerprint is invalid")
            key = (name, fingerprint)
            if key in result:
                raise ExchangeValidationError("Schema registry variant is duplicated")
            result[key] = fields
            compatible_value = variant["compatible_partition_count"]
            if type(compatible_value) is not int or compatible_value <= 0:
                raise ExchangeValidationError("Schema variant partition count is invalid")
            compatible = compatible_value
            if variant.get("physical_schema_version") != (
                f"{family['logical_schema_version']}+physical-sha256-{fingerprint}"
            ):
                raise ExchangeValidationError("Physical schema version identity is invalid")
            counts[key] = compatible
            counted += compatible
        if counted != int(family["partition_count"]):
            raise ExchangeValidationError("Schema variant counts do not reconcile")
        total += counted
    if total != EXPECTED_FILE_COUNT or len(result) != EXPECTED_PHYSICAL_VARIANT_COUNT:
        raise ExchangeValidationError("Schema registry partition totals do not reconcile")
    return result, counts


def _validate_source_rights(
    rights: Mapping[str, Any], manifest: SportsDatasetManifestV1
) -> None:
    if dict(rights) != build_source_rights():
        raise ExchangeValidationError(
            "Source-rights content/identity differs from the exact V1 policy"
        )
    if rights.get("schema_version") != "SPORTS_DATASET_SOURCE_RIGHTS_V1":
        raise ExchangeValidationError("Source-rights contract version is invalid")
    records = rights.get("records")
    if not isinstance(records, list) or len(records) != 6:
        raise ExchangeValidationError("Source-rights contract must contain six records")
    ids = [row.get("rights_control_id") for row in records if isinstance(row, dict)]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ExchangeValidationError("Source-right records are not sorted and unique")
    if tuple(ids) != manifest.source_rights_ids:
        raise ExchangeValidationError("Manifest source-right references differ")
    purposes = rights.get("package_allowed_purposes")
    if purposes != [
        "INTERNAL_DERIVED_ANALYTICS",
        "LOCAL_PRIVATE_MODEL_DEVELOPMENT",
        "READ_ONLY_REGISTRATION",
    ]:
        raise ExchangeValidationError("Package purpose boundary is invalid")
    forbidden = set(rights.get("package_prohibited_purposes", []))
    if not {
        "PUBLIC_BULK_DOWNLOAD",
        "PUBLIC_RAW_REDISTRIBUTION",
        "RAW_DATA_RESALE",
        "UNREVIEWED_COMMERCIAL_REDISTRIBUTION",
    }.issubset(forbidden):
        raise ExchangeValidationError("Package redistribution limitations are incomplete")
    retrosheet = [row for row in records if row.get("source_name") == "Retrosheet"]
    if len(retrosheet) != 1 or "www.retrosheet.org" not in str(
        retrosheet[0].get("required_attribution_text", "")
    ):
        raise ExchangeValidationError("Canonical Retrosheet attribution is absent")


def _validate_exact_attribution(root: Path) -> None:
    path = resolve_under(root, "attribution.md", require_exists=True, require_file=True)
    assert_regular_independent_file(path)
    expected = normalized_markdown_bytes(build_attribution_markdown())
    try:
        actual = path.read_bytes()
    except OSError as exc:
        raise ExchangeValidationError("Package attribution is unreadable") from exc
    if actual != expected:
        raise ExchangeValidationError("Package attribution differs from exact V1 text")


def _partition_row_evidence(
    path: Path,
    schema: Mapping[str, pl.DataType],
    family: str,
    season: int,
    predicates: Mapping[str, Predicate],
) -> tuple[int, str, str, dict[str, int], int]:
    date_field = "official_date" if family == "player_prior" else "game_date"
    if date_field not in schema:
        raise ExchangeValidationError("Parquet date field is missing")
    first_field = next(iter(schema), None)
    if first_field is None:
        raise ExchangeValidationError("Parquet schema is empty")
    row_guard = pl.col(first_field).is_null() | pl.col(first_field).is_not_null()
    expressions: list[pl.Expr] = [
        pl.len().alias("__rows"),
        pl.col(date_field).min().alias("__minimum"),
        pl.col(date_field).max().alias("__maximum"),
    ]
    membership: pl.Expr | None = None
    for name, predicate in predicates.items():
        expression = _compile_predicate(predicate, schema, family, season).fill_null(
            False
        )
        expressions.append(
            (row_guard & expression)
            .cast(pl.Int64)
            .sum()
            .alias(f"__population_{name}")
        )
        indicator = expression.cast(pl.Int16)
        membership = indicator if membership is None else membership + indicator
    if membership is None:
        raise ExchangeValidationError("Population predicate inventory is empty")
    expressions.append(
        (membership != 1).cast(pl.Int64).sum().alias("__population_membership_errors")
    )
    try:
        row = (
            pl.scan_parquet(path, hive_partitioning=False)
            .select(expressions)
            .collect()
            .row(0, named=True)
        )
    except Exception as exc:
        raise ExchangeValidationError("Parquet row metadata is unreadable") from exc
    counts = {
        name: int(row[f"__population_{name}"] or 0) for name in predicates
    }
    return (
        int(row["__rows"]),
        str(row["__minimum"]),
        str(row["__maximum"]),
        counts,
        int(row["__population_membership_errors"] or 0),
    )


def _compile_predicate(
    predicate: Predicate,
    schema: Mapping[str, pl.DataType],
    family: str,
    season: int,
) -> pl.Expr:
    if predicate.op == "and":
        assert predicate.args is not None
        result = _compile_predicate(predicate.args[0], schema, family, season)
        for item in predicate.args[1:]:
            result &= _compile_predicate(item, schema, family, season)
        return result
    if predicate.op == "or":
        assert predicate.args is not None
        result = _compile_predicate(predicate.args[0], schema, family, season)
        for item in predicate.args[1:]:
            result |= _compile_predicate(item, schema, family, season)
        return result
    if predicate.op == "not":
        assert predicate.arg is not None
        return ~_compile_predicate(predicate.arg, schema, family, season)
    assert predicate.field is not None
    field = _field_expression(predicate.field, schema, family, season)
    if predicate.op == "eq":
        return (field == pl.lit(predicate.value)).fill_null(False)
    assert predicate.values is not None
    return field.is_in(list(predicate.values)).fill_null(False)


def _field_expression(
    field: str,
    schema: Mapping[str, pl.DataType],
    family: str,
    season: int,
) -> pl.Expr:
    if field == "partition.family":
        return pl.lit(family)
    if field == "partition.season":
        return pl.lit(season)
    name = field.removeprefix("row.")
    if name in schema:
        return pl.col(name)
    return pl.lit(None)


def _validate_auxiliary_files(
    root: Path,
    expected_report: Mapping[str, Any],
    manifest: SportsDatasetManifestV1,
) -> None:
    report_path = resolve_under(
        root, "validation-report.json", require_exists=True, require_file=True
    )
    assert_regular_independent_file(report_path)
    actual = _load_object(root, "validation-report.json")
    if actual != expected_report:
        raise ExchangeValidationError("Persisted validation report differs from validation")
    expected_report_bytes = validation_report_bytes(expected_report)
    if report_path.read_bytes() != expected_report_bytes:
        raise ExchangeValidationError("Persisted validation report bytes differ")
    readme_path = resolve_under(root, "README.md", require_exists=True, require_file=True)
    assert_regular_independent_file(readme_path)
    try:
        actual_readme = readme_path.read_bytes()
    except OSError as exc:
        raise ExchangeValidationError("Package README is unreadable") from exc
    if actual_readme != readme_bytes(manifest, expected_report_bytes):
        raise ExchangeValidationError("README bytes differ from deterministic V1 content")


def validation_report_bytes(report: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(dict(report), trailing_newline=True)


def _noop(_: int) -> None:
    return None


def _progress_to_text(progress: ValidationProgress) -> str:
    elapsed = int(progress.elapsed_seconds)
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    return "\n".join(
        (
            "SPORTS DATASET EXCHANGE",
            f"Stage: {progress.stage}",
            f"Elapsed: {hours:02d}:{minutes:02d}:{seconds:02d}",
            f"Files checked: {progress.files_checked} / {progress.files_total}",
            f"Bytes checked: {progress.bytes_checked} / {progress.bytes_total}",
            f"Current family: {progress.current_family or '-'}",
            f"Hash mismatches: {progress.hash_mismatches}",
            f"Schema mismatches: {progress.schema_mismatches}",
        )
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="validate-sports-dataset-exchange")
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--heartbeat-seconds", type=int, default=30)
    parser.add_argument("--max-runtime-minutes", type=float, default=90)
    args = parser.parse_args(argv)
    try:
        result = validate_sports_dataset_exchange(
            args.package_root,
            heartbeat_seconds=args.heartbeat_seconds,
            max_runtime_minutes=args.max_runtime_minutes,
            progress_callback=lambda progress: print(
                _progress_to_text(progress), file=sys.stderr, flush=True
            ),
        )
    except ExchangeValidationDeadline as exc:
        print(
            json.dumps(
                {
                    "status": "STOPPED",
                    "reason": "MAX_RUNTIME_REACHED",
                    "resume_point": exc.progress,
                    "provider_network_calls": 0,
                    "database_accessed": False,
                },
                indent=2,
            )
        )
        raise SystemExit(75) from exc
    except (ExchangePathError, ExchangeValidationError, ValueError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, indent=2))
        raise SystemExit(2) from exc
    print(
        json.dumps(
            {
                "status": "VALID",
                "package_root": str(result.package_root),
                "manifest_id": result.manifest_id,
                "manifest_sha256": result.manifest_sha256,
                "elapsed_seconds": result.elapsed_seconds,
                "provider_network_calls": 0,
                "database_accessed": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()


__all__ = [
    "ExchangeValidationDeadline",
    "ExchangeValidationError",
    "ValidationProgress",
    "ValidationResult",
    "validate_sports_dataset_exchange",
    "validation_report_bytes",
]
