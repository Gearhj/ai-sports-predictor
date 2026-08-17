from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from app.gen2.exchange.identity_bridge.rows import (
    IdentityBridgeRowError,
    build_alias_parquet,
    build_dimension_parquet,
    validate_alias_parquet,
    validate_dimension_parquet,
)


def _sha(character: str) -> str:
    return character * 64


def _uuid(number: int) -> str:
    return f"00000000-0000-0000-0000-{number:012d}"


def _alias_row(index: int = 1, **updates: Any) -> dict[str, Any]:
    source_player_id = f"player{index}"
    namespace = str(updates.get("namespace", "RETROSHEET"))
    if "parent_join_key" in updates:
        parent_join_key = updates["parent_join_key"]
    elif namespace == "RETROSHEET":
        parent_join_key = f"retrosheet:player:{source_player_id}"
    elif namespace == "LAHMAN":
        parent_join_key = source_player_id
    else:
        parent_join_key = None
    row = {
        "canonical_player_uuid": _uuid(index),
        "namespace": namespace,
        "source_player_id": source_player_id,
        "parent_join_key": parent_join_key,
        "source_key": namespace,
        "source_version_id": _uuid(100 + index),
        "source_version_identity_sha256": _sha("a"),
        "mapping_confidence": "EXACT",
        "mapping_status": "RESOLVED",
        "mapping_basis": "GOVERNED_SOURCE_IDENTIFIER",
        "first_represented_season": 2021,
        "last_represented_season": 2025,
        "primary_2023_2025": True,
        "source_row_number": index,
        "source_row_sha256": _sha(str(index % 10)),
        "source_row_identity": f"sha256:{_sha(str(index % 10))}",
        "evidence_logical_uri": f"source-evidence/alias-{index}.json",
        "mapping_identity_sha256": _sha(chr(ord("b") + index - 1)),
    }
    row.update(updates)
    return row


def _dimension_row(index: int = 1, *, has_prior: bool = True, **updates: Any) -> dict[str, Any]:
    row = {
        "canonical_player_uuid": _uuid(index),
        "first_represented_season": 2021,
        "last_represented_season": 2025,
        "prior_history_2021_2022": has_prior,
        "primary_active_2023_2025": True,
        "has_retrosheet_alias": True,
        "has_lahman_alias": False,
        "has_chadwick_alias": False,
        "has_mlbam_alias": False,
        "has_baseball_reference_alias": False,
        "has_player_prior": has_prior,
        "minimum_player_prior_target_season": 2022 if has_prior else None,
        "maximum_player_prior_target_season": 2025 if has_prior else None,
        "batter_reference_count": 5,
        "pitcher_reference_count": 3,
        "other_player_reference_count": 1,
        "player_prior_row_count": 4 if has_prior else 0,
    }
    row.update(updates)
    return row


def test_alias_parquet_is_deterministic_and_validates_exact_digest(
    artifact_root: Path,
) -> None:
    rows = [_alias_row(2), _alias_row(1)]
    first = build_alias_parquet(rows)
    second = build_alias_parquet(list(reversed(rows)))

    assert first.payload == second.payload
    assert first.file_sha256 == hashlib.sha256(first.payload).hexdigest()
    assert first.file_sha256 == second.file_sha256
    assert first.schema_fingerprint == second.schema_fingerprint
    assert first.row_authority_sha256 == second.row_authority_sha256
    assert first.row_count == 2

    path = artifact_root / first.logical_uri
    path.write_bytes(first.payload)
    loaded, validated = validate_alias_parquet(path)
    assert [row["source_player_id"] for row in loaded] == ["player1", "player2"]
    assert validated == first


def test_optional_parent_join_key_is_preserved_for_non_parent_namespace(
    artifact_root: Path,
) -> None:
    artifact = build_alias_parquet(
        [_alias_row(1, namespace="CHADWICK", source_key="CHADWICK")]
    )
    path = artifact_root / artifact.logical_uri
    path.write_bytes(artifact.payload)

    rows, _validated = validate_alias_parquet(path)
    assert rows[0]["parent_join_key"] is None


def test_duplicate_semantic_alias_is_rejected() -> None:
    first = _alias_row(1)
    duplicate = _alias_row(1, mapping_identity_sha256=_sha("c"))
    with pytest.raises(IdentityBridgeRowError, match="Duplicate semantic alias"):
        build_alias_parquet([first, duplicate])


def test_one_source_alias_cannot_map_to_multiple_canonical_uuids() -> None:
    first = _alias_row(1)
    conflicting = _alias_row(
        1,
        canonical_player_uuid=_uuid(2),
        mapping_identity_sha256=_sha("c"),
    )
    with pytest.raises(IdentityBridgeRowError, match="multiple UUIDs"):
        build_alias_parquet([first, conflicting])


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"namespace": "PLAYER_NAME"}, "namespace"),
        ({"canonical_player_uuid": "not-a-uuid"}, "UUID"),
        ({"source_version_id": "not-a-uuid"}, "UUID"),
    ],
)
def test_alias_rows_reject_invalid_namespace_and_uuid(
    updates: dict[str, Any], message: str
) -> None:
    with pytest.raises(IdentityBridgeRowError, match=message):
        build_alias_parquet([_alias_row(1, **updates)])


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"mapping_status": "UNRESOLVED"}, "not resolved"),
        ({"mapping_status": "AMBIGUOUS"}, "not resolved"),
        ({"mapping_basis": "NAME_ONLY"}, "forbidden"),
        ({"mapping_basis": "FUZZY_MATCH"}, "forbidden"),
        ({"mapping_basis": "BIRTH_DATE_PLUS_NAME"}, "forbidden"),
    ],
)
def test_alias_rows_reject_unresolved_ambiguous_name_and_fuzzy_mapping(
    updates: dict[str, Any], message: str
) -> None:
    with pytest.raises(IdentityBridgeRowError, match=message):
        build_alias_parquet([_alias_row(1, **updates)])


def test_alias_parquet_rejects_row_hash_tampering(artifact_root: Path) -> None:
    artifact = build_alias_parquet([_alias_row(1)])
    path = artifact_root / artifact.logical_uri
    path.write_bytes(artifact.payload)
    frame = pl.read_parquet(path).with_columns(
        pl.lit("RETROSHEET_TAMPERED").alias("source_key")
    )
    frame.write_parquet(path)

    with pytest.raises(IdentityBridgeRowError, match="row SHA-256 differs"):
        validate_alias_parquet(path)


def test_dimension_parquet_supports_valid_prior_and_legitimate_missing_prior(
    artifact_root: Path,
) -> None:
    rows = [_dimension_row(2, has_prior=False), _dimension_row(1, has_prior=True)]
    first = build_dimension_parquet(rows)
    second = build_dimension_parquet(list(reversed(rows)))
    assert first.payload == second.payload
    assert first.file_sha256 == hashlib.sha256(first.payload).hexdigest()
    assert first.row_authority_sha256 == second.row_authority_sha256

    path = artifact_root / first.logical_uri
    path.write_bytes(first.payload)
    loaded, validated = validate_dimension_parquet(path)
    assert validated == first
    missing = next(row for row in loaded if not row["has_player_prior"])
    assert missing["minimum_player_prior_target_season"] is None
    assert missing["maximum_player_prior_target_season"] is None
    assert missing["player_prior_row_count"] == 0


def test_dimension_rows_require_unique_canonical_players() -> None:
    with pytest.raises(IdentityBridgeRowError, match="Duplicate active-player UUID"):
        build_dimension_parquet([_dimension_row(1), _dimension_row(1)])


@pytest.mark.parametrize(
    "updates",
    [
        {"minimum_player_prior_target_season": 2022},
        {"maximum_player_prior_target_season": 2025},
        {"player_prior_row_count": 1},
    ],
)
def test_dimension_missing_prior_requires_null_window_and_zero_rows(
    updates: dict[str, Any],
) -> None:
    with pytest.raises(IdentityBridgeRowError, match="Missing-prior fields"):
        build_dimension_parquet([_dimension_row(1, has_prior=False, **updates)])


@pytest.mark.parametrize("tamper", ["truncated", "schema"])
def test_dimension_parquet_rejects_tampered_bytes_and_schema(
    artifact_root: Path, tamper: str
) -> None:
    artifact = build_dimension_parquet([_dimension_row(1)])
    path = artifact_root / artifact.logical_uri
    if tamper == "truncated":
        path.write_bytes(artifact.payload[:32])
        message = "unreadable"
    else:
        path.write_bytes(artifact.payload)
        frame = pl.read_parquet(path).with_columns(
            pl.col("batter_reference_count").cast(pl.String)
        )
        frame.write_parquet(path)
        message = "schema differs"

    with pytest.raises(IdentityBridgeRowError, match=message):
        validate_dimension_parquet(path)
