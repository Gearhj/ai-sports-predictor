from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, create_engine, inspect

from app.audit import ImmutableRecordError
from app.models import BullpenAvailabilityObservation, GameFeatureSnapshot, GameStateObservation


GATE2_TABLES = {
    "game_state_observations",
    "lineup_observations",
    "lineup_entries",
    "starter_observations",
    "roster_availability_observations",
    "bullpen_availability_observations",
    "statcast_player_observations",
    "player_talent_estimates",
    "park_factor_versions",
    "team_rating_observations",
    "source_freshness_policies",
    "game_feature_snapshots",
    "forecast_movement_evidence",
    "provider_request_ledger",
    "game_refresh_states",
}


def _config(database_url: str) -> Config:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_gate2_migration_upgrades_prior_head_and_round_trips(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{(artifact_root / 'gate2.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("THE_ODDS_API_KEY", "")
    monkeypatch.setenv("ODDS_POLLING_ENABLED", "false")
    monkeypatch.setenv("OPEN_METEO_ENABLED", "false")
    monkeypatch.setenv("STATCAST_ENABLED", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    config = _config(database_url)

    command.upgrade(config, "20260812_0002")
    engine = create_engine(database_url)
    assert GATE2_TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert GATE2_TABLES <= set(schema.get_table_names())
    assert "feature_snapshot_id" in {column["name"] for column in schema.get_columns("forecasts")}
    assert any(
        foreign_key["referred_table"] == "game_feature_snapshots"
        and foreign_key["constrained_columns"] == ["feature_snapshot_id"]
        for foreign_key in schema.get_foreign_keys("forecasts")
    )
    game_state_columns = {
        column["name"]: column for column in schema.get_columns("game_state_observations")
    }
    assert game_state_columns["home_team_id"]["nullable"] is True
    assert game_state_columns["away_team_id"]["nullable"] is True
    game_state_foreign_keys = schema.get_foreign_keys("game_state_observations")
    assert any(
        foreign_key["referred_table"] == "teams"
        and foreign_key["constrained_columns"] == ["home_team_id"]
        for foreign_key in game_state_foreign_keys
    )
    assert any(
        foreign_key["referred_table"] == "teams"
        and foreign_key["constrained_columns"] == ["away_team_id"]
        for foreign_key in game_state_foreign_keys
    )
    game_state_indexes = {
        index["name"]: index["column_names"]
        for index in schema.get_indexes("game_state_observations")
    }
    assert game_state_indexes["ix_game_state_obs_home_team"] == ["home_team_id"]
    assert game_state_indexes["ix_game_state_obs_away_team"] == ["away_team_id"]
    bullpen_columns = {
        column["name"]: column
        for column in schema.get_columns("bullpen_availability_observations")
    }
    assert bullpen_columns["workload"]["nullable"] is True
    assert isinstance(bullpen_columns["workload"]["type"], JSON)
    expected_mode_isolated_uniques = {
        "game_state_observations": {
            "uq_game_state_obs_evidence": [
                "game_id", "source", "source_mode", "available_as_of", "payload_checksum",
            ],
        },
        "lineup_observations": {
            "uq_lineup_obs_evidence": [
                "game_id", "team_id", "source", "source_mode", "available_as_of", "payload_checksum",
            ],
        },
        "starter_observations": {
            "uq_starter_obs_evidence": [
                "game_id", "team_id", "source", "source_mode", "available_as_of", "payload_checksum",
            ],
        },
        "roster_availability_observations": {
            "uq_roster_avail_obs_evidence": [
                "player_id", "team_id", "source", "source_mode", "available_as_of", "payload_checksum",
            ],
        },
        "player_talent_estimates": {
            "uq_player_talent_estimate": [
                "player_id", "role", "metric_name", "version", "source", "source_mode", "available_as_of",
            ],
        },
        "bullpen_availability_observations": {
            "uq_bullpen_avail_obs": [
                "game_id", "team_id", "player_id", "source", "source_mode", "available_as_of",
            ],
        },
        "statcast_player_observations": {
            "uq_statcast_player_obs": [
                "player_id", "role", "season", "source", "source_mode", "available_as_of", "payload_checksum",
            ],
        },
        "park_factor_versions": {
            "uq_park_factor_version": [
                "venue_id", "season", "version", "source", "source_mode",
            ],
        },
        "team_rating_observations": {
            "uq_team_rating_observation": [
                "team_id", "method", "version", "source", "source_mode", "available_as_of",
            ],
        },
        "game_refresh_states": {
            "uq_game_refresh_state": [
                "game_id", "evaluated_at", "lifecycle_stage", "source_mode",
            ],
        },
    }
    for table_name, expected_constraints in expected_mode_isolated_uniques.items():
        actual_constraints = {
            constraint["name"]: constraint["column_names"]
            for constraint in schema.get_unique_constraints(table_name)
        }
        for constraint_name, expected_columns in expected_constraints.items():
            assert actual_constraints[constraint_name] == expected_columns

    provider_columns = {
        column["name"]: column for column in schema.get_columns("provider_request_ledger")
    }
    assert provider_columns["reservation_id"]["nullable"] is True
    assert any(
        foreign_key["referred_table"] == "provider_request_ledger"
        and foreign_key["constrained_columns"] == ["reservation_id"]
        for foreign_key in schema.get_foreign_keys("provider_request_ledger")
    )
    provider_uniques = {
        constraint["name"]: constraint["column_names"]
        for constraint in schema.get_unique_constraints("provider_request_ledger")
    }
    assert provider_uniques["uq_provider_ledger_terminal_reservation"] == ["reservation_id"]
    assert {constraint["name"] for constraint in schema.get_check_constraints("provider_request_ledger")} >= {
        "ck_provider_ledger_status",
        "ck_provider_ledger_lifecycle",
    }
    assert {
        index["name"]: index["column_names"]
        for index in schema.get_indexes("provider_request_ledger")
    }["ix_provider_ledger_reservation"] == ["reservation_id"]
    engine.dispose()

    command.downgrade(config, "20260812_0002")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert GATE2_TABLES.isdisjoint(schema.get_table_names())
    assert "feature_snapshot_id" not in {column["name"] for column in schema.get_columns("forecasts")}
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert GATE2_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_gate2_observations_and_feature_snapshots_are_append_only(session) -> None:
    now = datetime(2026, 8, 12, 18, tzinfo=UTC)
    observation = GameStateObservation(
        game_id=uuid4(),
        scheduled_at=datetime(2026, 8, 12, 23, tzinfo=UTC),
        status="SCHEDULED",
        is_postponed=False,
        home_starter_confirmation="UNKNOWN",
        away_starter_confirmation="UNKNOWN",
        observed_at=now,
        available_as_of=now,
        source="OFFLINE_TEST",
        source_mode="LIVE",
    )
    session.add(observation)
    session.commit()

    observation.status = "FINAL"
    with pytest.raises(ImmutableRecordError, match="append-only"):
        session.commit()
    session.rollback()

    snapshot = GameFeatureSnapshot(
        game_id=uuid4(),
        generated_at=now,
        data_as_of=now,
        feature_schema_version="gate2-test",
        bullpen_observation_ids=[],
        roster_observation_ids=[],
        statcast_observation_ids=[],
        market_snapshot_ids=[],
        source_evidence=[],
        feature_values={},
        feature_completeness=0,
        missing_features=["lineup"],
        stale_features=[],
        provisional_features=[],
        checksum="0" * 64,
        source_mode="LIVE",
    )
    session.add(snapshot)
    session.commit()

    snapshot.feature_completeness = 0.5
    with pytest.raises(ImmutableRecordError, match="append-only"):
        session.commit()
    session.rollback()

    session.delete(snapshot)
    with pytest.raises(ImmutableRecordError, match="append-only"):
        session.commit()
    session.rollback()

    bullpen = BullpenAvailabilityObservation(
        game_id=uuid4(),
        team_id=uuid4(),
        player_id=uuid4(),
        workload={
            "pitches_last_2d": 28,
            "pitches_last_3d": 44,
            "expected_usage_weight": 0.65,
        },
        observed_at=now,
        available_as_of=now,
        source="OFFLINE_TEST",
        source_mode="LIVE",
    )
    session.add(bullpen)
    session.commit()
    assert bullpen.workload == {
        "pitches_last_2d": 28,
        "pitches_last_3d": 44,
        "expected_usage_weight": 0.65,
    }

    bullpen.workload = {"pitches_last_2d": 0}
    with pytest.raises(ImmutableRecordError, match="append-only"):
        session.commit()
    session.rollback()
