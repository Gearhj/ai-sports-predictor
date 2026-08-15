from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.audit.immutability import _APPEND_ONLY_TYPES
from app.gen2.parquet_store import (
    EVENT_FAMILIES,
    HistoricalEventParquetStore,
    HistoricalParquetIntegrityError,
)
from app.gen2.source_authorization import (
    ExistingEvidenceDerivedUseDecision,
    OwnerApproval,
    require_local_processing,
)
from app.models import OpenDataDownloadManifest, OpenDataIdentityMapping


NOW = datetime(2026, 8, 14, tzinfo=UTC)
RAW_MANIFEST = (
    {
        "logical_uri": "raw/open/retrosheet/release-2025/gameinfo.csv",
        "file_sha256": "a" * 64,
    },
)


def _grant():
    checksum = hashlib.sha256(
        json.dumps(RAW_MANIFEST, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    owner = OwnerApproval(
        source_key="retrosheet-open-data",
        approved=True,
        decided_at=NOW - timedelta(days=1),
    )
    decision = ExistingEvidenceDerivedUseDecision(
        source_key="retrosheet-open-data",
        evidence_manifest_checksum=checksum,
        allowed=True,
        decided_at=NOW - timedelta(hours=1),
        owner_approval_identity_checksum=owner.identity_checksum,
    )
    return require_local_processing(
        decision,
        owner_approval=owner,
        as_of=NOW,
        expected_source_key="retrosheet-open-data",
        expected_manifest_checksum=checksum,
    )


def _write(store: HistoricalEventParquetStore, family: str, rows):
    return store.write_partition(
        rows,
        source_key="retrosheet-open-data",
        source_version_id=uuid4(),
        checkpoint_id=uuid4(),
        event_family=family,
        season=2024,
        month=7,
        schema_version=f"mlb-gen2-{family}-v1",
        raw_contributor_manifest=RAW_MANIFEST,
        code_revision="fd1-test",
        evidence_tier="A",
        timing_classification="RETROSPECTIVE_RECONSTRUCTION",
        authorization_grant=_grant(),
    )


def test_fd1_family_registry_is_frozen_and_complete() -> None:
    assert {
        "plate_appearance",
        "play_transition",
        "pitch_sequence",
        "player_game_batting",
        "player_game_pitching",
        "player_game_fielding",
        "starter_usage",
        "bullpen_usage",
        "weather",
        "player_prior",
    } <= EVENT_FAMILIES


def test_weather_and_player_prior_allow_only_semantically_valid_zero_counts(
    artifact_root,
) -> None:
    store = HistoricalEventParquetStore(
        artifact_root / "fd1",
        minimum_free_bytes_after_write=0,
        free_space=lambda _: 10**9,
    )
    weather = _write(
        store,
        "weather",
        [
            {
                "canonical_game_id": "game-1",
                "official_date": "2024-07-03",
                "temperature_f": 81.0,
                "source_station_id": "RETROSHEET_GAMEINFO",
            }
        ],
    )
    assert (weather.game_count, weather.plate_appearance_count, weather.player_count) == (
        1,
        0,
        0,
    )
    assert store.validate(weather).is_file()

    prior = _write(
        store,
        "player_prior",
        [
            {
                "canonical_player_id": "player-1",
                "official_date": "2024-07-01",
                "prior_seasons": 3,
            }
        ],
    )
    assert (prior.game_count, prior.plate_appearance_count, prior.player_count) == (
        0,
        0,
        1,
    )
    assert store.validate(prior).is_file()


def test_family_grain_counts_are_rederived_and_fail_closed(artifact_root) -> None:
    store = HistoricalEventParquetStore(
        artifact_root / "fd1-bad",
        minimum_free_bytes_after_write=0,
        free_space=lambda _: 10**9,
    )
    with pytest.raises(HistoricalParquetIntegrityError, match="player_count cannot be derived"):
        _write(
            store,
            "starter_usage",
            [
                {
                    "canonical_game_id": "game-1",
                    "official_date": "2024-07-03",
                    "batters_faced": 24,
                }
            ],
        )


def test_fd1_download_and_identity_records_are_append_only_and_migrated(
    artifact_root,
    monkeypatch,
) -> None:
    assert OpenDataDownloadManifest in _APPEND_ONLY_TYPES
    assert OpenDataIdentityMapping in _APPEND_ONLY_TYPES
    database = (artifact_root / "fd1-schema.db").resolve()
    database_url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert {
        "open_data_download_manifests",
        "open_data_identity_mappings",
    } <= tables
    identity_uniques = {
        row["name"]: tuple(row["column_names"])
        for row in inspect(engine).get_unique_constraints(
            "open_data_identity_mappings"
        )
    }
    assert identity_uniques["uq_open_data_identity_mapping_natural"] == (
        "source_version_id",
        "entity_type",
        "source_namespace",
        "external_id",
        "source_row_number",
    )
    engine.dispose()
    command.downgrade(config, "20260814_0013")
    downgraded = create_engine(database_url)
    assert "uq_open_data_identity_mapping_natural" not in {
        row["name"]
        for row in inspect(downgraded).get_unique_constraints(
            "open_data_identity_mappings"
        )
    }
    downgraded.dispose()
    command.upgrade(config, "head")
    command.check(config)


def test_identity_natural_key_migration_fails_closed_on_0013_duplicates(
    artifact_root,
    monkeypatch,
) -> None:
    database = (artifact_root / "fd1-duplicate-natural-key.db").resolve()
    database_url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260814_0013")
    engine = create_engine(database_url)
    statement = text(
        "INSERT INTO open_data_identity_mappings ("
        "source_version_id, entity_type, source_namespace, external_id, "
        "canonical_namespace, canonical_id, source_row_number, "
        "source_row_checksum, confidence, valid_from, valid_through, "
        "evidence_logical_uri, identity_checksum, id"
        ") VALUES ("
        ":source_version_id, 'person', 'RETROSHEET', 'duplicate-player', "
        "'chadwick_person', :canonical_id, 2, :row_checksum, 'EXACT', "
        "NULL, NULL, 'raw/people-a.csv', :identity_checksum, :id"
        ")"
    )
    shared = {
        "source_version_id": str(uuid4()),
        "canonical_id": str(uuid4()),
        "row_checksum": "a" * 64,
    }
    with engine.begin() as connection:
        connection.execute(
            statement,
            [
                {
                    **shared,
                    "identity_checksum": "b" * 64,
                    "id": str(uuid4()),
                },
                {
                    **shared,
                    "identity_checksum": "c" * 64,
                    "id": str(uuid4()),
                },
            ],
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="duplicate open_data_identity_mappings"):
        command.upgrade(config, "20260814_0014")
