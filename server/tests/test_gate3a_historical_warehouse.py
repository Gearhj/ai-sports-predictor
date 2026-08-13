from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.audit import ImmutableRecordError
from app.models import (
    FeatureVersion,
    Game,
    GameFinalOutcome,
    GameFeatureSnapshot,
    GameStatus,
    HistoricalFeatureMaterialization,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    IngestionRun,
    League,
    Player,
    PlayerGameHistoryObservation,
    RunStatus,
    Season,
    Sport,
    Team,
    TeamGameHistoryObservation,
    Venue,
)
from app.audit.immutability import _APPEND_ONLY_TYPES


GATE3A_TABLES = {
    "historical_source_versions",
    "history_backfill_checkpoints",
    "game_final_outcomes",
    "team_game_history_observations",
    "player_game_history_observations",
    "historical_feature_materializations",
}

EXPECTED_INDEXES = {
    "historical_source_versions": {
        "ix_hist_source_lookup",
        "ix_hist_source_eligibility",
    },
    "history_backfill_checkpoints": {
        "ix_hist_checkpoint_source_stream",
        "ix_hist_checkpoint_event_window",
        "ix_hist_checkpoint_status_completed",
    },
    "game_final_outcomes": {
        "ix_game_final_game_available",
        "ix_game_final_season_scheduled",
        "ix_game_final_checkpoint",
    },
    "team_game_history_observations": {
        "ix_team_game_history_game",
        "ix_team_game_history_team_eligible",
        "ix_team_game_history_checkpoint",
    },
    "player_game_history_observations": {
        "ix_player_game_history_game",
        "ix_player_game_history_player_eligible",
        "ix_player_game_history_team_eligible",
        "ix_player_game_history_checkpoint",
    },
    "historical_feature_materializations": {
        "ix_hist_materialization_game_cutoff",
        "ix_hist_materialization_checkpoint",
        "ix_hist_materialization_eligibility",
    },
}


def _config(database_url: str) -> Config:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _provider_disabled_environment(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("THE_ODDS_API_KEY", "")
    monkeypatch.setenv("ODDS_POLLING_ENABLED", "false")
    monkeypatch.setenv("OPEN_METEO_ENABLED", "false")
    monkeypatch.setenv("STATCAST_ENABLED", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")


def _constraint_sql(schema, table: str, name: str) -> str:
    return next(
        row["sqltext"]
        for row in schema.get_check_constraints(table)
        if row["name"] == name
    )


def test_gate3a_migration_upgrades_0004_and_round_trips(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{(artifact_root / 'gate3a.db').as_posix()}"
    _provider_disabled_environment(monkeypatch, database_url)
    config = _config(database_url)

    command.upgrade(config, "20260813_0004")
    engine = create_engine(database_url)
    assert GATE3A_TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert GATE3A_TABLES <= set(schema.get_table_names())
    for table, expected in EXPECTED_INDEXES.items():
        assert expected <= {row["name"] for row in schema.get_indexes(table)}

    source_columns = {
        column["name"]: column
        for column in schema.get_columns("historical_source_versions")
    }
    assert source_columns["source_mode"]["nullable"] is False
    assert source_columns["availability_policy"]["nullable"] is False
    assert source_columns["identity_checksum"]["nullable"] is False
    assert {
        row["name"]
        for row in schema.get_unique_constraints("historical_source_versions")
    } >= {"uq_hist_source_key_version_mode", "uq_hist_source_identity"}

    checkpoint_foreign_keys = schema.get_foreign_keys("history_backfill_checkpoints")
    assert {row["referred_table"] for row in checkpoint_foreign_keys} >= {
        "ingestion_runs",
        "historical_source_versions",
        "history_backfill_checkpoints",
    }
    assert "RETROSPECTIVE_RECONSTRUCTION" in _constraint_sql(
        schema,
        "history_backfill_checkpoints",
        "ck_hist_checkpoint_temporal_semantics",
    )
    assert {
        row["name"]
        for row in schema.get_unique_constraints("history_backfill_checkpoints")
    } >= {
        "uq_hist_checkpoint_run_stream_sequence",
        "uq_hist_checkpoint_identity",
    }

    final_foreign_keys = schema.get_foreign_keys("game_final_outcomes")
    assert {row["referred_table"] for row in final_foreign_keys} >= {
        "games",
        "seasons",
        "teams",
        "venues",
        "game_state_observations",
        "historical_source_versions",
        "history_backfill_checkpoints",
        "game_final_outcomes",
    }
    assert "home_score >= 0" in _constraint_sql(
        schema, "game_final_outcomes", "ck_game_final_scores"
    )

    for table in (
        "team_game_history_observations",
        "player_game_history_observations",
    ):
        columns = {column["name"]: column for column in schema.get_columns(table)}
        assert columns["event_at"]["nullable"] is False
        assert columns["eligible_at"]["nullable"] is False
        assert columns["values"]["nullable"] is False
        assert columns["source_mode"]["nullable"] is False

    materialization_uniques = {
        row["name"]
        for row in schema.get_unique_constraints("historical_feature_materializations")
    }
    assert materialization_uniques >= {
        "uq_hist_materialization_identity",
        "uq_hist_materialization_semantic",
    }
    assert "quality_score <= 100" in _constraint_sql(
        schema,
        "historical_feature_materializations",
        "ck_hist_materialization_quality",
    )
    engine.dispose()

    command.check(config)
    command.downgrade(config, "20260813_0004")
    engine = create_engine(database_url)
    assert GATE3A_TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, "head")
    command.check(config)


def _source(*, mode: str, version: str, checksum: str) -> HistoricalSourceVersion:
    return HistoricalSourceVersion(
        source_key="MLB_STATS_API",
        dataset_version=version,
        provider_name="MLB Stats API",
        source_mode=mode,
        source_classification="OFFICIAL_PRIMARY",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        quality_tier="A" if mode == "LIVE" else "EXCLUDED",
        training_eligible=mode == "LIVE",
        evaluation_eligible=mode == "LIVE",
        availability_policy={"kind": "completed_event", "lag_minutes": 0},
        policy_version="gate3a-cutoff-v1",
        effective_from=datetime(2025, 1, 1, tzinfo=UTC),
        identity_checksum=checksum,
    )


def _checkpoint(run_id, source_id, *, checksum: str) -> HistoryBackfillCheckpoint:
    requested = datetime(2026, 8, 14, 12, tzinfo=UTC)
    return HistoryBackfillCheckpoint(
        ingestion_run_id=run_id,
        source_version_id=source_id,
        stream_key="schedule:2025-04-01",
        sequence=0,
        season=2025,
        event_start_at=datetime(2025, 4, 1, tzinfo=UTC),
        event_end_at=datetime(2025, 4, 1, 23, 59, tzinfo=UTC),
        cursor_before={"date": "2025-04-01"},
        cursor_after={"date": "2025-04-02"},
        query_fingerprint="f" * 64,
        status="SUCCEEDED",
        requested_at=requested,
        completed_at=requested + timedelta(seconds=1),
        attempt_count=1,
        retry_count=0,
        records_received=15,
        records_accepted=15,
        records_rejected=0,
        duplicate_count=0,
        raw_references=["data/raw/historical/mlb/2025/04/01/chunk.json"],
        raw_checksums=["a" * 64],
        schema_version="mlb-history-v1",
        code_revision="4ca843d",
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum=checksum,
    )


def test_gate3a_source_modes_coexist_and_governance_rows_are_append_only(session) -> None:
    live = _source(mode="LIVE", version="2025-v1", checksum="a" * 64)
    demo = _source(mode="DEMO", version="2025-v1", checksum="b" * 64)
    session.add_all([live, demo])
    session.commit()
    assert {live.source_mode, demo.source_mode} == {"LIVE", "DEMO"}
    assert demo.training_eligible is False

    live.quality_tier = "B"
    with pytest.raises(ImmutableRecordError, match="HistoricalSourceVersion records are append-only"):
        session.commit()
    session.rollback()

    run = IngestionRun(
        source="MLB_STATS_API",
        job_name="backfill-mlb-history",
        status=RunStatus.SUCCEEDED,
        records_processed=15,
    )
    session.add(run)
    session.flush()
    checkpoint = _checkpoint(run.id, live.id, checksum="c" * 64)
    session.add(checkpoint)
    session.commit()

    checkpoint.cursor_after = {"date": "2025-04-03"}
    with pytest.raises(ImmutableRecordError, match="HistoryBackfillCheckpoint records are append-only"):
        session.commit()
    session.rollback()


def test_gate3a_database_constraints_fail_closed(session) -> None:
    excluded = _source(mode="DEMO", version="bad-demo", checksum="d" * 64)
    excluded.training_eligible = True
    excluded.evaluation_eligible = True
    session.add(excluded)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    invalid_mode = _source(mode="HISTORICAL_RECONSTRUCTION", version="bad-mode", checksum="e" * 64)
    session.add(invalid_mode)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    assert any(
        "quality_score <= 100" in str(constraint.sqltext)
        for constraint in HistoricalFeatureMaterialization.__table__.constraints
        if hasattr(constraint, "sqltext")
    )


def test_gate3a_checkpoint_identity_is_idempotent_and_source_mode_isolated(session) -> None:
    live = _source(mode="LIVE", version="checkpoint-live", checksum="3" * 64)
    demo = _source(mode="DEMO", version="checkpoint-demo", checksum="4" * 64)
    run = IngestionRun(
        source="MLB_STATS_API",
        job_name="backfill-mlb-history",
        status=RunStatus.SUCCEEDED,
        records_processed=15,
    )
    session.add_all([live, demo, run])
    session.flush()
    first = _checkpoint(run.id, live.id, checksum="5" * 64)
    session.add(first)
    session.commit()

    duplicate_identity = _checkpoint(run.id, demo.id, checksum="5" * 64)
    duplicate_identity.stream_key = "demo:schedule:2025-04-01"
    duplicate_identity.sequence = 1
    duplicate_identity.source_mode = "DEMO"
    session.add(duplicate_identity)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    duplicate_sequence = _checkpoint(run.id, demo.id, checksum="6" * 64)
    duplicate_sequence.source_mode = "DEMO"
    session.add(duplicate_sequence)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_game_final_outcome_has_target_only_integrity_and_is_append_only(session) -> None:
    outcome = GameFinalOutcome(
        game_id=uuid4(),
        season_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        source_version_id=uuid4(),
        checkpoint_id=uuid4(),
        scheduled_at=datetime(2025, 4, 1, 19, tzinfo=UTC),
        final_at=datetime(2025, 4, 1, 22, tzinfo=UTC),
        home_score=5,
        away_score=3,
        revision=1,
        source_record_id="game:777",
        retrieved_at=datetime(2026, 8, 14, tzinfo=UTC),
        label_available_at=datetime(2025, 4, 1, 22, tzinfo=UTC),
        availability_basis="OFFICIAL_FINAL_EVENT",
        payload_checksum="1" * 64,
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="2" * 64,
    )
    # ORM metadata must encode every target-only invariant even before parent
    # fixtures are persisted in an integration backfill test.
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in outcome.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    assert "home_score >= 0" in checks["ck_game_final_scores"]
    assert "home_team_id <> away_team_id" in checks["ck_game_final_distinct_sides"]
    assert "label_available_at >= final_at" in checks["ck_game_final_availability"]
    assert GameFinalOutcome in _APPEND_ONLY_TYPES


def test_all_gate3a_facts_persist_with_lineage_and_reject_mutation(session) -> None:
    sport = Sport(code="MLB-G3A", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB-G3A", name="Major League Baseball")
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id,
        name="2025",
        starts_at=datetime(2025, 3, 1, tzinfo=UTC),
        ends_at=datetime(2025, 11, 1, tzinfo=UTC),
    )
    home = Team(league_id=league.id, name="Home", abbreviation="H3A")
    away = Team(league_id=league.id, name="Away", abbreviation="A3A")
    venue = Venue(name="Historical Park", timezone="America/New_York")
    session.add_all([season, home, away, venue])
    session.flush()
    player = Player(team_id=home.id, full_name="Historical Player", position="P")
    first_pitch = datetime(2025, 4, 1, 19, tzinfo=UTC)
    final_at = first_pitch + timedelta(hours=3)
    game = Game(
        league_id=league.id,
        season_id=season.id,
        venue_id=venue.id,
        starts_at=first_pitch,
        status=GameStatus.FINAL,
        home_score=5,
        away_score=3,
        final_at=final_at,
        source_mode="LIVE",
    )
    feature_version = FeatureVersion(
        name="mlb_gate3a",
        version="1",
        schema_hash="7" * 64,
        definition={"ordered_features": ["home_rating"]},
    )
    run = IngestionRun(
        source="MLB_STATS_API",
        job_name="backfill-mlb-history",
        status=RunStatus.SUCCEEDED,
        records_processed=3,
    )
    session.add_all([player, game, feature_version, run])
    session.flush()
    source = _source(mode="LIVE", version="warehouse-graph", checksum="8" * 64)
    session.add(source)
    session.flush()
    checkpoint = _checkpoint(run.id, source.id, checksum="9" * 64)
    session.add(checkpoint)
    session.flush()
    outcome = GameFinalOutcome(
        game_id=game.id,
        season_id=season.id,
        home_team_id=home.id,
        away_team_id=away.id,
        venue_id=venue.id,
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        scheduled_at=first_pitch,
        final_at=final_at,
        scheduled_innings=9,
        innings_played=9,
        home_score=5,
        away_score=3,
        outcome_status="FINAL",
        revision=1,
        official=True,
        source_record_id="game:777",
        source_published_at=final_at,
        retrieved_at=datetime(2026, 8, 14, tzinfo=UTC),
        label_available_at=final_at,
        availability_basis="OFFICIAL_FINAL_EVENT",
        payload_checksum="a" * 64,
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="b" * 64,
    )
    session.add(outcome)
    session.flush()
    team_fact = TeamGameHistoryObservation(
        game_id=game.id,
        final_outcome_id=outcome.id,
        season_id=season.id,
        team_id=home.id,
        opponent_team_id=away.id,
        is_home=True,
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        event_at=final_at,
        evidence_end_at=final_at,
        retrieved_at=datetime(2026, 8, 14, tzinfo=UTC),
        eligible_at=final_at,
        availability_basis="OFFICIAL_COMPLETED_GAME",
        evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
        quality_tier="A",
        source_record_id="game:777:team:H3A",
        stat_schema_version="team-game-v1",
        values={"runs": 5, "hits": 8},
        payload_checksum="c" * 64,
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="d" * 64,
    )
    player_fact = PlayerGameHistoryObservation(
        game_id=game.id,
        final_outcome_id=outcome.id,
        season_id=season.id,
        player_id=player.id,
        team_id=home.id,
        opponent_team_id=away.id,
        role="PITCHER",
        started=True,
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        event_at=final_at,
        evidence_end_at=final_at,
        retrieved_at=datetime(2026, 8, 14, tzinfo=UTC),
        eligible_at=final_at,
        availability_basis="OFFICIAL_COMPLETED_GAME",
        evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
        quality_tier="A",
        source_record_id="game:777:player:42",
        stat_schema_version="player-game-v1",
        values={"innings_pitched": 6.0},
        payload_checksum="e" * 64,
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="f" * 64,
    )
    session.add_all([team_fact, player_fact])
    cutoff = first_pitch - timedelta(hours=6)
    snapshot = GameFeatureSnapshot(
        game_id=game.id,
        generated_at=datetime(2026, 8, 14, tzinfo=UTC),
        data_as_of=cutoff,
        feature_schema_version="mlb-gate3a-v1",
        feature_version_id=feature_version.id,
        bullpen_observation_ids=[],
        roster_observation_ids=[],
        statcast_observation_ids=[],
        market_snapshot_ids=[],
        source_evidence=[{"identity_checksum": team_fact.identity_checksum}],
        feature_values={"home_rating": 1510.0},
        feature_completeness=1.0,
        missing_features=[],
        stale_features=[],
        provisional_features=[],
        checksum="0" * 64,
        source_mode="LIVE",
        frozen_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
    session.add(snapshot)
    session.flush()
    materialization = HistoricalFeatureMaterialization(
        game_id=game.id,
        game_feature_snapshot_id=snapshot.id,
        feature_version_id=feature_version.id,
        game_final_outcome_id=outcome.id,
        checkpoint_id=checkpoint.id,
        scheduled_at=first_pitch,
        cutoff_at=cutoff,
        generated_at=datetime(2026, 8, 14, tzinfo=UTC),
        cutoff_policy_version="gate3a-six-hour-v1",
        cutoff_policy={"hours_before_first_pitch": 6},
        source_bundle=[str(source.id)],
        source_bundle_checksum="1" * 64,
        evidence_manifest=[str(team_fact.id), str(player_fact.id)],
        evidence_manifest_checksum="2" * 64,
        quality_tier="A",
        quality_score=92.5,
        quality_components={"team_history": 100, "player_history": 85},
        materializer_version="gate3a-v1",
        code_revision="4ca843d",
        eligibility_status="ELIGIBLE",
        eligibility_reasons=[],
        training_eligible=True,
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="3" * 64,
    )
    session.add(materialization)
    session.commit()

    records_and_mutations = (
        (HistoricalSourceVersion, source.id, "provider_name", "rewritten"),
        (HistoryBackfillCheckpoint, checkpoint.id, "status", "FAILED"),
        (GameFinalOutcome, outcome.id, "home_score", 99),
        (TeamGameHistoryObservation, team_fact.id, "values", {"runs": 99}),
        (PlayerGameHistoryObservation, player_fact.id, "values", {"innings_pitched": 99}),
        (
            HistoricalFeatureMaterialization,
            materialization.id,
            "quality_score",
            1.0,
        ),
    )
    for model, identifier, field, value in records_and_mutations:
        record = session.get(model, identifier)
        assert record is not None
        setattr(record, field, value)
        with pytest.raises(ImmutableRecordError, match=f"{model.__name__} records are append-only"):
            session.commit()
        session.rollback()

    persisted = session.get(HistoricalFeatureMaterialization, materialization.id)
    assert persisted is not None
    assert persisted.quality_score == 92.5
    assert persisted.game_feature_snapshot_id == snapshot.id
    assert persisted.game_final_outcome_id == outcome.id
