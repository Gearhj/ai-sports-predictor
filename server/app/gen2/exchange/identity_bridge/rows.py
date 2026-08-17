"""Deterministic Parquet row contracts for the MLB FD1 identity bridge."""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import polars as pl

from app.gen2.exchange.canonical_json import canonical_sha256
from app.gen2.exchange.contracts import validate_portable_uri


SUPPORTED_NAMESPACES = (
    "BASEBALL_REFERENCE",
    "CHADWICK",
    "LAHMAN",
    "MLBAM",
    "RETROSHEET",
)
ALIAS_SORT_FIELDS = (
    "namespace",
    "source_player_id",
    "canonical_player_uuid",
    "source_version_id",
    "source_row_number",
)
DIMENSION_SORT_FIELDS = ("canonical_player_uuid",)

ALIAS_SCHEMA: Mapping[str, pl.DataType] = {
    "canonical_player_uuid": pl.String,
    "namespace": pl.String,
    "source_player_id": pl.String,
    "parent_join_key": pl.String,
    "source_key": pl.String,
    "source_version_id": pl.String,
    "source_version_identity_sha256": pl.String,
    "mapping_confidence": pl.String,
    "mapping_status": pl.String,
    "mapping_basis": pl.String,
    "first_represented_season": pl.Int64,
    "last_represented_season": pl.Int64,
    "primary_2023_2025": pl.Boolean,
    "source_row_number": pl.Int64,
    "source_row_sha256": pl.String,
    "source_row_identity": pl.String,
    "evidence_logical_uri": pl.String,
    "mapping_identity_sha256": pl.String,
    "row_sha256": pl.String,
}

DIMENSION_SCHEMA: Mapping[str, pl.DataType] = {
    "canonical_player_uuid": pl.String,
    "first_represented_season": pl.Int64,
    "last_represented_season": pl.Int64,
    "prior_history_2021_2022": pl.Boolean,
    "primary_active_2023_2025": pl.Boolean,
    "has_retrosheet_alias": pl.Boolean,
    "has_lahman_alias": pl.Boolean,
    "has_chadwick_alias": pl.Boolean,
    "has_mlbam_alias": pl.Boolean,
    "has_baseball_reference_alias": pl.Boolean,
    "has_player_prior": pl.Boolean,
    "minimum_player_prior_target_season": pl.Int64,
    "maximum_player_prior_target_season": pl.Int64,
    "batter_reference_count": pl.Int64,
    "pitcher_reference_count": pl.Int64,
    "other_player_reference_count": pl.Int64,
    "player_prior_row_count": pl.Int64,
    "row_sha256": pl.String,
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ROW_IDENTITY = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN_MAPPING_BASIS = re.compile(
    r"(?:NAME|FUZZY|LEVENSHTEIN|BIRTH[_ -]?DATE|PROBABILISTIC)", re.IGNORECASE
)


class IdentityBridgeRowError(ValueError):
    """A bridge row, schema, hash, or uniqueness invariant is invalid."""


@dataclass(frozen=True, slots=True)
class ParquetArtifact:
    logical_uri: str
    payload: bytes
    row_count: int
    byte_count: int
    file_sha256: str
    schema_fingerprint: str
    row_authority_sha256: str


def row_sha256(row: Mapping[str, Any]) -> str:
    payload = dict(row)
    payload.pop("row_sha256", None)
    return canonical_sha256(payload)


def apply_row_hash(row: Mapping[str, Any]) -> dict[str, Any]:
    resolved = dict(row)
    resolved["row_sha256"] = row_sha256(resolved)
    return resolved


def build_alias_parquet(rows: Iterable[Mapping[str, Any]]) -> ParquetArtifact:
    normalized = [apply_row_hash(row) for row in rows]
    _validate_alias_rows(normalized, require_sorted=False)
    normalized.sort(key=lambda row: tuple(row[field] for field in ALIAS_SORT_FIELDS))
    _validate_alias_rows(normalized, require_sorted=True)
    return _serialize(
        "player-identity-aliases.parquet",
        normalized,
        schema=ALIAS_SCHEMA,
    )


def build_dimension_parquet(rows: Iterable[Mapping[str, Any]]) -> ParquetArtifact:
    normalized = [apply_row_hash(row) for row in rows]
    _validate_dimension_rows(normalized, require_sorted=False)
    normalized.sort(key=lambda row: row["canonical_player_uuid"])
    _validate_dimension_rows(normalized, require_sorted=True)
    return _serialize(
        "active-player-dimension.parquet",
        normalized,
        schema=DIMENSION_SCHEMA,
    )


def validate_alias_parquet(path: Path) -> tuple[list[dict[str, Any]], ParquetArtifact]:
    rows, payload = _read_parquet(path, ALIAS_SCHEMA)
    _validate_alias_rows(rows, require_sorted=True)
    return rows, _artifact_from_payload(
        "player-identity-aliases.parquet", payload, rows, ALIAS_SCHEMA
    )


def validate_dimension_parquet(
    path: Path,
) -> tuple[list[dict[str, Any]], ParquetArtifact]:
    rows, payload = _read_parquet(path, DIMENSION_SCHEMA)
    _validate_dimension_rows(rows, require_sorted=True)
    return rows, _artifact_from_payload(
        "active-player-dimension.parquet", payload, rows, DIMENSION_SCHEMA
    )


def schema_fingerprint(schema: Mapping[str, pl.DataType]) -> str:
    return canonical_sha256(
        [{"name": name, "dtype": str(dtype)} for name, dtype in schema.items()]
    )


def rows_authority_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    return canonical_sha256([dict(row) for row in rows])


def _serialize(
    logical_uri: str,
    rows: list[dict[str, Any]],
    *,
    schema: Mapping[str, pl.DataType],
) -> ParquetArtifact:
    frame = pl.DataFrame(rows, schema=schema, strict=True)
    stream = io.BytesIO()
    frame.write_parquet(
        stream,
        compression="zstd",
        compression_level=9,
        statistics="full",
        row_group_size=8_192,
        data_page_size=1_048_576,
    )
    payload = stream.getvalue()
    return _artifact_from_payload(logical_uri, payload, rows, schema)


def _artifact_from_payload(
    logical_uri: str,
    payload: bytes,
    rows: Sequence[Mapping[str, Any]],
    schema: Mapping[str, pl.DataType],
) -> ParquetArtifact:
    import hashlib

    return ParquetArtifact(
        logical_uri=logical_uri,
        payload=payload,
        row_count=len(rows),
        byte_count=len(payload),
        file_sha256=hashlib.sha256(payload).hexdigest(),
        schema_fingerprint=schema_fingerprint(schema),
        row_authority_sha256=rows_authority_sha256(rows),
    )


def _read_parquet(
    path: Path, expected_schema: Mapping[str, pl.DataType]
) -> tuple[list[dict[str, Any]], bytes]:
    try:
        payload = Path(path).read_bytes()
        frame = pl.read_parquet(io.BytesIO(payload))
    except Exception as exc:
        raise IdentityBridgeRowError("Bridge Parquet is unreadable") from exc
    actual_schema = dict(frame.schema)
    if tuple(actual_schema) != tuple(expected_schema) or any(
        actual_schema[name] != dtype for name, dtype in expected_schema.items()
    ):
        raise IdentityBridgeRowError("Bridge Parquet schema differs")
    return frame.to_dicts(), payload


def _validate_alias_rows(
    rows: Sequence[Mapping[str, Any]], *, require_sorted: bool
) -> None:
    expected_fields = set(ALIAS_SCHEMA)
    semantic_rows: set[tuple[str, str]] = set()
    alias_targets: dict[tuple[str, str], str] = {}
    mapping_identities: set[str] = set()
    sort_keys: list[tuple[Any, ...]] = []
    for row in rows:
        if set(row) != expected_fields:
            raise IdentityBridgeRowError("Alias row fields differ from V1")
        canonical = _uuid(row["canonical_player_uuid"], "canonical_player_uuid")
        namespace = row["namespace"]
        if namespace not in SUPPORTED_NAMESPACES:
            raise IdentityBridgeRowError("Alias namespace is invalid")
        source_id = _identifier(row["source_player_id"], "source_player_id")
        source_key = _identifier(row["source_key"], "source_key")
        _uuid(row["source_version_id"], "source_version_id")
        _digest(
            row["source_version_identity_sha256"],
            "source_version_identity_sha256",
        )
        if row["mapping_status"] != "RESOLVED":
            raise IdentityBridgeRowError("Alias mapping is not resolved")
        if row["mapping_confidence"] not in {"EXACT", "HIGH"}:
            raise IdentityBridgeRowError("Alias confidence is not governed")
        basis = _identifier(row["mapping_basis"], "mapping_basis")
        if _FORBIDDEN_MAPPING_BASIS.search(basis):
            raise IdentityBridgeRowError("Name/fuzzy identity basis is forbidden")
        first = _represented_season(row["first_represented_season"])
        last = _represented_season(row["last_represented_season"])
        if first > last:
            raise IdentityBridgeRowError("Alias represented season window is invalid")
        if not isinstance(row["primary_2023_2025"], bool):
            raise IdentityBridgeRowError("Alias primary-season indicator is invalid")
        if not isinstance(row["source_row_number"], int) or isinstance(
            row["source_row_number"], bool
        ) or row["source_row_number"] < 1:
            raise IdentityBridgeRowError("Alias source row number is invalid")
        source_row_sha = _digest(row["source_row_sha256"], "source_row_sha256")
        if row["source_row_identity"] != f"sha256:{source_row_sha}":
            raise IdentityBridgeRowError("Alias source-row identity differs")
        if not _SOURCE_ROW_IDENTITY.fullmatch(str(row["source_row_identity"])):
            raise IdentityBridgeRowError("Alias source-row identity is invalid")
        validate_portable_uri(_identifier(row["evidence_logical_uri"], "evidence_logical_uri"))
        mapping_identity = _digest(
            row["mapping_identity_sha256"], "mapping_identity_sha256"
        )
        _digest(row["row_sha256"], "row_sha256")
        if row_sha256(row) != row["row_sha256"]:
            raise IdentityBridgeRowError("Alias row SHA-256 differs")

        join_key = row["parent_join_key"]
        if namespace == "RETROSHEET":
            if join_key != f"retrosheet:player:{source_id}":
                raise IdentityBridgeRowError("Retrosheet parent join key differs")
        elif namespace == "LAHMAN":
            if join_key != source_id:
                raise IdentityBridgeRowError("Lahman parent join key differs")
        elif join_key is not None:
            raise IdentityBridgeRowError("Non-parent alias has a parent join key")

        natural = (namespace, source_id)
        previous = alias_targets.setdefault(natural, canonical)
        if previous != canonical:
            raise IdentityBridgeRowError("One source alias maps to multiple UUIDs")
        if natural in semantic_rows:
            raise IdentityBridgeRowError("Duplicate semantic alias row")
        semantic_rows.add(natural)
        if mapping_identity in mapping_identities:
            raise IdentityBridgeRowError("Duplicate canonical mapping identity")
        mapping_identities.add(mapping_identity)
        sort_keys.append(tuple(row[field] for field in ALIAS_SORT_FIELDS))
        if not source_key or not canonical:
            raise IdentityBridgeRowError("Alias authority is incomplete")
    if require_sorted and sort_keys != sorted(sort_keys):
        raise IdentityBridgeRowError("Alias rows are not canonically sorted")


def _validate_dimension_rows(
    rows: Sequence[Mapping[str, Any]], *, require_sorted: bool
) -> None:
    expected_fields = set(DIMENSION_SCHEMA)
    seen: set[str] = set()
    order: list[str] = []
    for row in rows:
        if set(row) != expected_fields:
            raise IdentityBridgeRowError("Dimension row fields differ from V1")
        canonical = _uuid(row["canonical_player_uuid"], "canonical_player_uuid")
        if canonical in seen:
            raise IdentityBridgeRowError("Duplicate active-player UUID")
        seen.add(canonical)
        order.append(canonical)
        first = _represented_season(row["first_represented_season"])
        last = _represented_season(row["last_represented_season"])
        if first > last:
            raise IdentityBridgeRowError("Dimension represented season window is invalid")
        if not isinstance(row["prior_history_2021_2022"], bool):
            raise IdentityBridgeRowError("Prior-history indicator is invalid")
        if not isinstance(row["primary_active_2023_2025"], bool):
            raise IdentityBridgeRowError("Primary-active indicator is invalid")
        for field in (
            "has_retrosheet_alias",
            "has_lahman_alias",
            "has_chadwick_alias",
            "has_mlbam_alias",
            "has_baseball_reference_alias",
            "has_player_prior",
        ):
            if not isinstance(row[field], bool):
                raise IdentityBridgeRowError(f"{field} must be Boolean")
        minimum = row["minimum_player_prior_target_season"]
        maximum = row["maximum_player_prior_target_season"]
        if row["has_player_prior"]:
            if minimum is None or maximum is None or _season(minimum) > _season(maximum):
                raise IdentityBridgeRowError("Player-prior target window is invalid")
            if row["player_prior_row_count"] <= 0:
                raise IdentityBridgeRowError("Player-prior row count is inconsistent")
            expected_prior_history = minimum <= 2023
            if row["prior_history_2021_2022"] is not expected_prior_history:
                raise IdentityBridgeRowError("Prior-history indicator is inconsistent")
        elif minimum is not None or maximum is not None or row["player_prior_row_count"] != 0:
            raise IdentityBridgeRowError("Missing-prior fields are inconsistent")
        elif row["prior_history_2021_2022"]:
            raise IdentityBridgeRowError("Missing-prior history indicator is inconsistent")
        for field in (
            "batter_reference_count",
            "pitcher_reference_count",
            "other_player_reference_count",
            "player_prior_row_count",
        ):
            value = row[field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise IdentityBridgeRowError(f"{field} is invalid")
        _digest(row["row_sha256"], "row_sha256")
        if row_sha256(row) != row["row_sha256"]:
            raise IdentityBridgeRowError("Dimension row SHA-256 differs")
    if require_sorted and order != sorted(order):
        raise IdentityBridgeRowError("Dimension rows are not canonically sorted")


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise IdentityBridgeRowError(f"{field} is invalid")
    if any(ord(character) < 32 for character in value):
        raise IdentityBridgeRowError(f"{field} contains a control character")
    return value


def _uuid(value: Any, field: str) -> str:
    text = _identifier(value, field)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise IdentityBridgeRowError(f"{field} is not a UUID") from exc
    if str(parsed) != text:
        raise IdentityBridgeRowError(f"{field} is not a canonical lowercase UUID")
    return text


def _digest(value: Any, field: str) -> str:
    text = str(value)
    if not _SHA256.fullmatch(text):
        raise IdentityBridgeRowError(f"{field} is not a lowercase SHA-256")
    return text


def _season(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 2021 <= value <= 2026:
        raise IdentityBridgeRowError("Represented season is outside the bridge range")
    return value


def _represented_season(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 2021 <= value <= 2025:
        raise IdentityBridgeRowError("Represented season is outside 2021-2025")
    return value


__all__ = [
    "ALIAS_SCHEMA",
    "DIMENSION_SCHEMA",
    "IdentityBridgeRowError",
    "ParquetArtifact",
    "SUPPORTED_NAMESPACES",
    "apply_row_hash",
    "build_alias_parquet",
    "build_dimension_parquet",
    "row_sha256",
    "rows_authority_sha256",
    "schema_fingerprint",
    "validate_alias_parquet",
    "validate_dimension_parquet",
]
