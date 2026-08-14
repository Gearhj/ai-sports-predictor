from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest

from app.gen2.parquet_store import (
    DEFAULT_MAXIMUM_PARTITION_BYTES,
    DEFAULT_MAXIMUM_STORE_BYTES,
    DEFAULT_MINIMUM_FREE_BYTES_AFTER_WRITE,
    HistoricalEventParquetStore,
    HistoricalParquetCollisionError,
    HistoricalParquetIntegrityError,
    HistoricalParquetStorageLimitError,
)
from app.gen2.source_authorization import (
    ExistingEvidenceDerivedUseDecision,
    OwnerApproval,
    require_local_processing,
)


NOW = datetime(2026, 8, 14, tzinfo=UTC)
RAW_MANIFEST = (
    {
        "logical_uri": "raw/historical/statcast/2024/04/pilot.json.gz",
        "file_sha256": "3" * 64,
    },
)


def _manifest_checksum(manifest) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _grant(source_key: str, manifest):
    owner = OwnerApproval(
        source_key=source_key,
        approved=True,
        decided_at=NOW - timedelta(days=1),
    )
    manifest_checksum = _manifest_checksum(tuple(manifest))
    decision = ExistingEvidenceDerivedUseDecision(
        source_key=source_key,
        evidence_manifest_checksum=manifest_checksum,
        allowed=True,
        decided_at=NOW - timedelta(hours=1),
        owner_approval_identity_checksum=owner.identity_checksum,
    )
    return require_local_processing(
        decision,
        owner_approval=owner,
        as_of=NOW,
        expected_source_key=source_key,
        expected_manifest_checksum=manifest_checksum,
    )


def _rows() -> list[dict[str, object]]:
    return [
        {
            "game_id": "game-1",
            "plate_appearance_id": "game-1-pa-1",
            "pitch_sequence": 1,
            "event_timestamp": datetime(2024, 4, 8, 17, 1, tzinfo=UTC),
            "batter_id": "batter-1",
            "pitcher_id": "pitcher-1",
            "pitch_type": "FF",
            "release_speed": 95.2,
        },
        {
            "game_id": "game-1",
            "plate_appearance_id": "game-1-pa-1",
            "pitch_sequence": 2,
            "event_timestamp": datetime(2024, 4, 8, 17, 2, tzinfo=UTC),
            "batter_id": "batter-1",
            "pitcher_id": "pitcher-1",
            "pitch_type": "SL",
            "release_speed": 87.1,
        },
        {
            "game_id": "game-2",
            "plate_appearance_id": "game-2-pa-1",
            "pitch_sequence": 1,
            "event_timestamp": datetime(2024, 4, 9, 18, 3, tzinfo=UTC),
            "batter_id": "batter-2",
            "pitcher_id": "pitcher-2",
            "pitch_type": "CH",
            "release_speed": 89.4,
        },
    ]


def _write(store: HistoricalEventParquetStore):
    return store.write_partition(
        _rows(),
        source_key="baseball-savant-statcast",
        source_version_id="1" * 32,
        checkpoint_id="2" * 32,
        event_family="pitch",
        season=2024,
        month=4,
        schema_version="gate3c-pitch-v1",
        raw_contributor_manifest=RAW_MANIFEST,
        code_revision="gate3c-storage-test",
        evidence_tier="A",
        timing_classification="RETROSPECTIVE_EVENT_TRUTH",
        authorization_grant=_grant("baseball-savant-statcast", RAW_MANIFEST),
    )


def test_store_defaults_preserve_100_gib_reserve_and_caps() -> None:
    assert DEFAULT_MINIMUM_FREE_BYTES_AFTER_WRITE == 100 * 1024**3
    assert DEFAULT_MAXIMUM_PARTITION_BYTES == 100 * 1024**3
    assert DEFAULT_MAXIMUM_STORE_BYTES == 100 * 1024**3


def test_atomic_content_addressed_zstd_partition_is_reused_idempotently(
    artifact_root: Path,
) -> None:
    root = artifact_root / "gen2-events"
    store = HistoricalEventParquetStore(
        root,
        free_space=lambda _: 500 * 1024**3,
    )

    first = _write(store)
    path = store.validate(first)
    assert first.reused is False
    assert first.storage_format == "PARQUET"
    assert first.compression == "ZSTD"
    assert first.row_count == 3
    assert first.game_count == 2
    assert first.plate_appearance_count == 2
    assert first.player_count == 4
    assert first.min_event_date == "2024-04-08"
    assert first.max_event_date == "2024-04-09"
    assert len(first.schema_hash) == 64
    assert len(first.raw_contributor_checksum) == 64
    assert len(first.identity_checksum) == 64
    assert path.is_file()
    assert path.read_bytes()[:4] == b"PAR1"
    assert path.read_bytes()[-4:] == b"PAR1"
    assert re.fullmatch(r"part-[0-9a-f]{64}\.parquet", path.name)
    assert not list(root.rglob("*.tmp"))
    assert pl.read_parquet(path).height == 3

    second = _write(store)
    assert second.reused is True
    assert second.logical_uri == first.logical_uri
    assert second.file_sha256 == first.file_sha256
    assert second.identity_checksum == first.identity_checksum
    assert [item.resolve() for item in root.rglob("*.parquet")] == [path]

    tight_reuse = HistoricalEventParquetStore(
        root,
        minimum_free_bytes_after_write=0,
        maximum_store_bytes=first.file_size_bytes,
        free_space=lambda _: 500 * 1024**3,
    )
    assert _write(tight_reuse).reused is True


def test_existing_content_address_collision_fails_closed(artifact_root: Path) -> None:
    store = HistoricalEventParquetStore(
        artifact_root / "gen2-events",
        free_space=lambda _: 500 * 1024**3,
    )
    artifact = _write(store)
    path = store.resolve(artifact.logical_uri)
    path.write_bytes(b"corrupt-existing-evidence")

    with pytest.raises(HistoricalParquetCollisionError, match="conflicting evidence"):
        _write(store)


def test_store_rejects_escape_links_bad_manifest_and_storage_limits(
    artifact_root: Path,
) -> None:
    store = HistoricalEventParquetStore(
        artifact_root / "gen2-events",
        minimum_free_bytes_after_write=0,
        maximum_partition_bytes=1,
        maximum_store_bytes=100,
        free_space=lambda _: 1_000_000,
    )
    with pytest.raises(HistoricalParquetStorageLimitError, match="partition exceeds"):
        _write(store)
    with pytest.raises(HistoricalParquetIntegrityError, match="root-confined"):
        store.resolve("../outside.parquet")

    normal = HistoricalEventParquetStore(
        artifact_root / "normal-events",
        minimum_free_bytes_after_write=0,
        free_space=lambda _: 1_000_000_000,
    )
    with pytest.raises(HistoricalParquetIntegrityError, match="manifest checksum"):
        malformed_manifest = ({"file_sha256": "4" * 64},)
        normal.write_partition(
            _rows(),
            source_key="statcast",
            source_version_id=uuid4(),
            checkpoint_id=uuid4(),
            event_family="pitch",
            season=2024,
            month=4,
            schema_version="v1",
            raw_contributor_manifest=malformed_manifest,
            raw_contributor_checksum="5" * 64,
            code_revision="test",
            evidence_tier="A",
            timing_classification="RETROSPECTIVE_EVENT_TRUTH",
            authorization_grant=_grant("statcast", malformed_manifest),
        )

    reserve = HistoricalEventParquetStore(
        artifact_root / "reserve-events",
        minimum_free_bytes_after_write=100 * 1024**3,
        free_space=lambda _: 10,
    )
    with pytest.raises(HistoricalParquetStorageLimitError, match="free-space reserve"):
        _write(reserve)


def test_symlink_root_is_rejected_when_platform_allows_symlink(
    artifact_root: Path,
) -> None:
    target = artifact_root / "target"
    target.mkdir()
    link = artifact_root / "linked-root"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows environment does not grant symlink creation")
    with pytest.raises(HistoricalParquetIntegrityError, match="links or reparse"):
        HistoricalEventParquetStore(link)


def test_canonical_identity_fields_drive_counts_and_invalid_time_fails_closed(
    artifact_root: Path,
) -> None:
    store = HistoricalEventParquetStore(
        artifact_root / "canonical-events",
        minimum_free_bytes_after_write=0,
        free_space=lambda _: 1_000_000_000,
    )
    canonical_rows = [
        {
            "canonical_game_id": "game-1",
            "canonical_plate_appearance_id": "pa-1",
            "canonical_batter_id": "batter-1",
            "canonical_pitcher_id": "pitcher-1",
            "official_date": "2024-04-30",
            "event_at": "2024-05-01T00:30:00+00:00",
        }
    ]
    artifact = store.write_partition(
        canonical_rows,
        source_key="mlb-stats-api",
        source_version_id=uuid4(),
        checkpoint_id=uuid4(),
        event_family="plate_appearance",
        season=2024,
        month=4,
        schema_version="canonical-pa-v1",
        raw_contributor_manifest=RAW_MANIFEST,
        code_revision="test",
        evidence_tier="A",
        timing_classification="RETROSPECTIVE_EVENT_TRUTH",
        authorization_grant=_grant("mlb-stats-api", RAW_MANIFEST),
    )
    assert artifact.game_count == 1
    assert artifact.plate_appearance_count == 1
    assert artifact.player_count == 2
    assert artifact.min_event_date == "2024-04-30"
    assert artifact.min_event_at == "2024-05-01T00:30:00+00:00"
    assert store.validate(artifact).is_file()

    invalid_rows = [
        {
            **canonical_rows[0],
            "event_at": "not-a-timestamp",
        }
    ]
    with pytest.raises(HistoricalParquetIntegrityError, match="invalid temporal"):
        store.write_partition(
            invalid_rows,
            source_key="mlb-stats-api",
            source_version_id=uuid4(),
            checkpoint_id=uuid4(),
            event_family="plate_appearance",
            season=2024,
            month=4,
            schema_version="canonical-pa-v1",
            raw_contributor_manifest=RAW_MANIFEST,
            code_revision="test",
            evidence_tier="A",
            timing_classification="RETROSPECTIVE_EVENT_TRUTH",
            authorization_grant=_grant("mlb-stats-api", RAW_MANIFEST),
        )


def test_raw_contributor_metadata_rejects_secrets_and_absolute_paths(
    artifact_root: Path,
) -> None:
    store = HistoricalEventParquetStore(
        artifact_root / "safe-metadata",
        minimum_free_bytes_after_write=0,
        free_space=lambda _: 1_000_000_000,
    )

    def attempt(manifest):
        return store.write_partition(
            _rows(),
            source_key="statcast",
            source_version_id=uuid4(),
            checkpoint_id=uuid4(),
            event_family="pitch",
            season=2024,
            month=4,
            schema_version="v1",
            raw_contributor_manifest=manifest,
            code_revision="test",
            evidence_tier="A",
            timing_classification="RETROSPECTIVE_EVENT_TRUTH",
            authorization_grant=_grant("statcast", manifest),
        )

    secret_manifest = (
        {
            "logical_uri": "raw/statcast/day.csv.gz",
            "file_sha256": "a" * 64,
            "api_key": "must-not-persist",
        },
    )
    with pytest.raises(HistoricalParquetIntegrityError, match="secret-bearing"):
        attempt(secret_manifest)

    absolute_manifest = (
        {
            "logical_uri": "C:/Users/example/raw.csv.gz",
            "file_sha256": "b" * 64,
        },
    )
    with pytest.raises(HistoricalParquetIntegrityError, match="root-relative"):
        attempt(absolute_manifest)
