from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.cli import _select_historical_reconstruction_targets
from app.models import (
    Game,
    GameFinalOutcome,
    GameStatus,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    IngestionRun,
    League,
    RunStatus,
    Season,
    Sport,
    Team,
)


def _config(database_url: str) -> Config:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _provider_disabled_environment(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("THE_ODDS_API_KEY", "")
    monkeypatch.setenv("ODDS_POLLING_ENABLED", "false")
    monkeypatch.setenv("OPEN_METEO_ENABLED", "false")
    monkeypatch.setenv("STATCAST_ENABLED", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")


@pytest.mark.parametrize(
    ("revision_range", "database_url", "expected_sql"),
    [
        (
            "20260814_0005:20260814_0006",
            "postgresql+psycopg://asp:asp@localhost/asp",
            "LAG(id) OVER",
        ),
        (
            "20260814_0006:20260814_0007",
            "sqlite://",
            "date(checkpoints.event_start_at)",
        ),
        (
            "20260814_0006:20260814_0007",
            "postgresql+psycopg://asp:asp@localhost/asp",
            "AT TIME ZONE 'UTC'",
        ),
    ],
)
def test_a1_offline_sql_is_supported_and_utc_explicit(
    monkeypatch: pytest.MonkeyPatch,
    revision_range: str,
    database_url: str,
    expected_sql: str,
) -> None:
    output = StringIO()
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(
        config,
        revision_range,
        sql=True,
    )

    generated = output.getvalue()
    assert expected_sql in generated
    assert "UPDATE" in generated


def _insert_legacy_observation(
    connection,
    *,
    table_name: str,
    observation_id: str,
    payload_checksum: str,
    retrieved_at: str,
    created_at: str,
) -> None:
    shared = {
        "game_id": "1" * 32,
        "final_outcome_id": "2" * 32,
        "season_id": "3" * 32,
        "team_id": "4" * 32,
        "opponent_team_id": "5" * 32,
        "source_version_id": "6" * 32,
        "checkpoint_id": "7" * 32,
        "event_at": "2025-06-18 05:00:00+00:00",
        "evidence_end_at": "2025-06-18 05:00:00+00:00",
        "retrieved_at": retrieved_at,
        "eligible_at": "2025-06-18 05:00:00+00:00",
        "availability_basis": "OFFICIAL_COMPLETED_GAME",
        "evidence_classification": "RECONSTRUCTABLE_POINT_IN_TIME",
        "quality_tier": "A",
        "source_record_id": "game:777:record",
        "stat_schema_version": "gate3a-a0",
        "values": "{}",
        "payload_checksum": payload_checksum,
        "source_mode": "LIVE",
        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
        "identity_checksum": observation_id[0] * 64,
        "id": observation_id,
        "created_at": created_at,
    }
    if table_name == "team_game_history_observations":
        connection.execute(
            text(
                "INSERT INTO team_game_history_observations ("
                "game_id, final_outcome_id, season_id, team_id, opponent_team_id, "
                "is_home, source_version_id, checkpoint_id, event_at, evidence_end_at, "
                "retrieved_at, eligible_at, availability_basis, evidence_classification, "
                "quality_tier, source_record_id, stat_schema_version, \"values\", "
                "payload_checksum, source_mode, temporal_semantics, identity_checksum, "
                "id, created_at) VALUES ("
                ":game_id, :final_outcome_id, :season_id, :team_id, :opponent_team_id, "
                "1, :source_version_id, :checkpoint_id, :event_at, :evidence_end_at, "
                ":retrieved_at, :eligible_at, :availability_basis, "
                ":evidence_classification, :quality_tier, :source_record_id, "
                ":stat_schema_version, :values, :payload_checksum, :source_mode, "
                ":temporal_semantics, :identity_checksum, :id, :created_at)"
            ),
            shared,
        )
        return

    connection.execute(
        text(
            "INSERT INTO player_game_history_observations ("
            "game_id, final_outcome_id, season_id, player_id, team_id, "
            "opponent_team_id, role, started, source_version_id, checkpoint_id, "
            "event_at, evidence_end_at, retrieved_at, eligible_at, availability_basis, "
            "evidence_classification, quality_tier, source_record_id, stat_schema_version, "
            "\"values\", payload_checksum, source_mode, temporal_semantics, "
            "identity_checksum, id, created_at) VALUES ("
            ":game_id, :final_outcome_id, :season_id, :player_id, :team_id, "
            ":opponent_team_id, 'BATTER', 1, :source_version_id, :checkpoint_id, "
            ":event_at, :evidence_end_at, :retrieved_at, :eligible_at, "
            ":availability_basis, :evidence_classification, :quality_tier, "
            ":source_record_id, :stat_schema_version, :values, :payload_checksum, "
            ":source_mode, :temporal_semantics, :identity_checksum, :id, :created_at)"
        ),
        {**shared, "player_id": "8" * 32},
    )


def _assert_revision_chain(engine, table_name: str, expected_ids: list[str]) -> None:
    with engine.connect() as connection:
        rows = connection.execute(text(
            f"SELECT id, revision, supersedes_observation_id FROM {table_name} "
            "ORDER BY revision"
        )).mappings().all()
    assert [row["id"] for row in rows] == expected_ids
    assert [row["revision"] for row in rows] == list(range(1, len(rows) + 1))
    assert [row["supersedes_observation_id"] for row in rows] == [
        None,
        *expected_ids[:-1],
    ]


def _insert_0006_checkpoint(
    connection,
    *,
    checkpoint_id: str,
    event_start_at: str,
    event_end_at: str,
    checksum_character: str,
) -> None:
    connection.execute(text(
        "INSERT INTO history_backfill_checkpoints ("
        "ingestion_run_id, source_version_id, stream_key, sequence, season, "
        "event_start_at, event_end_at, query_fingerprint, status, requested_at, "
        "completed_at, attempt_count, retry_count, records_received, "
        "records_accepted, records_rejected, duplicate_count, raw_references, "
        "raw_checksums, schema_version, code_revision, source_mode, "
        "temporal_semantics, identity_checksum, id, created_at) VALUES ("
        ":ingestion_run_id, :source_version_id, :stream_key, :sequence, 2025, "
        ":event_start_at, :event_end_at, :query_fingerprint, 'SUCCEEDED', "
        ":requested_at, :completed_at, 1, 0, 1, 1, 0, 0, '[]', '[]', "
        "'gate3a-a1', 'test', 'LIVE', 'RETROSPECTIVE_RECONSTRUCTION', "
        ":identity_checksum, :id, :created_at)"
    ), {
        "ingestion_run_id": checksum_character * 32,
        "source_version_id": "d" * 32,
        "stream_key": f"fixture:{checksum_character}",
        "sequence": int(checksum_character, 16),
        "event_start_at": event_start_at,
        "event_end_at": event_end_at,
        "query_fingerprint": checksum_character * 64,
        "requested_at": "2026-08-13 19:00:00+00:00",
        "completed_at": "2026-08-13 20:00:00+00:00",
        "identity_checksum": checksum_character * 64,
        "id": checkpoint_id,
        "created_at": "2026-08-13 20:00:00+00:00",
    })


def _insert_0006_outcome(
    connection,
    *,
    outcome_id: str,
    game_id: str,
    checkpoint_id: str,
    checksum_character: str,
) -> None:
    connection.execute(text(
        "INSERT INTO game_final_outcomes ("
        "game_id, season_id, home_team_id, away_team_id, source_version_id, "
        "checkpoint_id, scheduled_at, final_at, completion_time_basis, "
        "completion_time_source_field, completion_time_method, "
        "completion_time_limitation, evidence_classification, quality_tier, "
        "reconstructed_at, home_score, away_score, home_win, run_differential, "
        "total_runs, outcome_status, revision, official, source_record_id, "
        "retrieved_at, label_available_at, availability_basis, payload_checksum, "
        "source_mode, temporal_semantics, identity_checksum, id, created_at) VALUES ("
        ":game_id, :season_id, :home_team_id, :away_team_id, :source_version_id, "
        ":checkpoint_id, :scheduled_at, :final_at, 'AUTHORITATIVE_FINAL_AT', "
        "'legacy:event_completed_at', 'legacy A0 explicit event_completed_at', "
        "'legacy row predates A1 completion-evidence metadata', "
        "'RECONSTRUCTABLE_POINT_IN_TIME', 'A', :retrieved_at, 5, 3, 1, 2, 8, "
        "'FINAL', 1, 1, 'game:existing-0006', :retrieved_at, :retrieved_at, "
        "'ASP_ACQUISITION_TIME', :payload_checksum, 'LIVE', "
        "'RETROSPECTIVE_RECONSTRUCTION', :identity_checksum, :id, :created_at)"
    ), {
        "game_id": game_id,
        "season_id": "a" * 32,
        "home_team_id": "b" * 32,
        "away_team_id": "c" * 32,
        "source_version_id": "d" * 32,
        "checkpoint_id": checkpoint_id,
        "scheduled_at": "2025-06-18 02:10:00+00:00",
        "final_at": "2025-06-18 05:00:00+00:00",
        "retrieved_at": "2026-08-13 20:00:00+00:00",
        "payload_checksum": checksum_character * 64,
        "identity_checksum": checksum_character * 64,
        "id": outcome_id,
        "created_at": "2026-08-13 20:00:00+00:00",
    })


def test_a1_migrations_revision_legacy_checksum_variants_and_add_official_date(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{(artifact_root / 'gate3a-a1.db').as_posix()}"
    _provider_disabled_environment(monkeypatch, database_url)
    config = _config(database_url)
    command.upgrade(config, "20260814_0005")

    team_ids = ["f" * 32, "1" * 31 + "0"]
    player_ids = ["e" * 32, "2" * 31 + "0"]
    engine = create_engine(database_url)
    with engine.begin() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 0
        for table_name, ids in (
            ("team_game_history_observations", team_ids),
            ("player_game_history_observations", player_ids),
        ):
            _insert_legacy_observation(
                connection,
                table_name=table_name,
                observation_id=ids[0],
                payload_checksum="1" * 64,
                retrieved_at="2026-08-13 20:00:00+00:00",
                created_at="2026-08-13 20:01:00+00:00",
            )
            _insert_legacy_observation(
                connection,
                table_name=table_name,
                observation_id=ids[1],
                payload_checksum="2" * 64,
                retrieved_at="2026-08-13 21:00:00+00:00",
                created_at="2026-08-13 19:00:00+00:00",
            )
    engine.dispose()

    command.upgrade(config, "20260814_0006")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert "official_date" not in {
        column["name"] for column in schema.get_columns("game_final_outcomes")
    }
    _assert_revision_chain(engine, "team_game_history_observations", team_ids)
    _assert_revision_chain(engine, "player_game_history_observations", player_ids)
    one_day_checkpoint_id = "e" * 32
    multi_day_checkpoint_id = "9" * 32
    outcome_id = "3" * 32
    ambiguous_outcome_id = "4" * 32
    with engine.begin() as connection:
        _insert_0006_checkpoint(
            connection,
            checkpoint_id=one_day_checkpoint_id,
            event_start_at="2025-06-17 00:00:00+00:00",
            event_end_at="2025-06-17 23:59:59+00:00",
            checksum_character="a",
        )
        _insert_0006_checkpoint(
            connection,
            checkpoint_id=multi_day_checkpoint_id,
            event_start_at="2025-06-16 00:00:00+00:00",
            event_end_at="2025-06-17 23:59:59+00:00",
            checksum_character="b",
        )
        _insert_0006_outcome(
            connection,
            outcome_id=outcome_id,
            game_id="9" * 32,
            checkpoint_id=one_day_checkpoint_id,
            checksum_character="a",
        )
        _insert_0006_outcome(
            connection,
            outcome_id=ambiguous_outcome_id,
            game_id="8" * 32,
            checkpoint_id=multi_day_checkpoint_id,
            checksum_character="c",
        )
    engine.dispose()

    # This is also the safe path for a populated warehouse already stamped
    # 0006: 0007 adds an honest nullable field without rewriting A1 evidence.
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    official_column = next(
        column
        for column in schema.get_columns("game_final_outcomes")
        if column["name"] == "official_date"
    )
    assert official_column["nullable"] is True
    assert "ix_game_final_official_date" in {
        row["name"] for row in schema.get_indexes("game_final_outcomes")
    }
    with engine.connect() as connection:
        migrated_dates = dict(connection.execute(text(
            "SELECT id, official_date FROM game_final_outcomes "
            "WHERE id IN (:exact_id, :ambiguous_id)"
        ), {
            "exact_id": outcome_id,
            "ambiguous_id": ambiguous_outcome_id,
        }).all())
    # This game's first pitch is 2025-06-18 UTC, but its checkpoint proves the
    # MLB official schedule date was 2025-06-17.
    assert migrated_dates[outcome_id] == "2025-06-17"
    assert migrated_dates[ambiguous_outcome_id] is None
    engine.dispose()
    command.check(config)

    command.downgrade(config, "20260814_0005")
    engine = create_engine(database_url)
    schema = inspect(engine)
    for table_name in (
        "team_game_history_observations",
        "player_game_history_observations",
    ):
        assert {"revision", "supersedes_observation_id"}.isdisjoint(
            column["name"] for column in schema.get_columns(table_name)
        )
        with engine.connect() as connection:
            assert connection.execute(
                text(f"SELECT count(*) FROM {table_name}")
            ).scalar_one() == 2
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    _assert_revision_chain(engine, "team_game_history_observations", team_ids)
    _assert_revision_chain(engine, "player_game_history_observations", player_ids)
    engine.dispose()
    command.check(config)


def _historical_outcome(
    *,
    game: Game,
    season: Season,
    home: Team,
    away: Team,
    source: HistoricalSourceVersion,
    checkpoint: HistoryBackfillCheckpoint,
    official_date: date,
    revision: int,
    checksum_character: str,
    supersedes_outcome_id=None,
) -> GameFinalOutcome:
    final_at = game.starts_at + timedelta(hours=3)
    retrieved_at = datetime(2026, 8, 13, 20, revision, tzinfo=UTC)
    return GameFinalOutcome(
        game_id=game.id,
        season_id=season.id,
        home_team_id=home.id,
        away_team_id=away.id,
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        official_date=official_date,
        scheduled_at=game.starts_at,
        final_at=final_at,
        home_score=5,
        away_score=3,
        revision=revision,
        supersedes_outcome_id=supersedes_outcome_id,
        source_record_id=f"game:{game.id}:revision:{revision}",
        retrieved_at=retrieved_at,
        label_available_at=retrieved_at,
        availability_basis="ASP_ACQUISITION_TIME",
        payload_checksum=checksum_character * 64,
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum=checksum_character * 64,
    )


def test_cli_target_selection_uses_latest_mlb_official_date_not_chunk_or_utc_date(
    session: Session,
) -> None:
    sport = Sport(code="MLB-A1-CLI", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(
        sport_id=sport.id,
        code="MLB-A1-CLI",
        name="Major League Baseball",
    )
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id,
        name="2025-cli",
        starts_at=datetime(2025, 3, 1, tzinfo=UTC),
        ends_at=datetime(2025, 11, 1, tzinfo=UTC),
    )
    home = Team(league_id=league.id, name="Home", abbreviation="HCL")
    away = Team(league_id=league.id, name="Away", abbreviation="ACL")
    run = IngestionRun(
        source="MLB_STATS_API",
        job_name="historical:multi-day",
        status=RunStatus.SUCCEEDED,
        records_processed=4,
    )
    session.add_all([season, home, away, run])
    session.flush()
    source = HistoricalSourceVersion(
        source_key="MLB_STATS_API",
        dataset_version="cli-official-date",
        provider_name="MLB Stats API",
        source_mode="LIVE",
        source_classification="OFFICIAL_PRIMARY",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        quality_tier="A",
        training_eligible=True,
        evaluation_eligible=True,
        availability_policy={"kind": "completed_event"},
        policy_version="gate3a-a1",
        effective_from=datetime(2025, 1, 1, tzinfo=UTC),
        identity_checksum="a" * 64,
    )
    session.add(source)
    session.flush()
    checkpoint = HistoryBackfillCheckpoint(
        ingestion_run_id=run.id,
        source_version_id=source.id,
        stream_key="mlb:2025-06-16:2025-06-18",
        sequence=0,
        season=2025,
        event_start_at=datetime(2025, 6, 16, tzinfo=UTC),
        event_end_at=datetime(2025, 6, 18, 23, 59, tzinfo=UTC),
        query_fingerprint="b" * 64,
        status="SUCCEEDED",
        requested_at=datetime(2026, 8, 13, 19, tzinfo=UTC),
        completed_at=datetime(2026, 8, 13, 20, tzinfo=UTC),
        attempt_count=1,
        retry_count=0,
        records_received=4,
        records_accepted=4,
        records_rejected=0,
        duplicate_count=0,
        raw_references=[],
        raw_checksums=[],
        schema_version="fixture-v1",
        code_revision="test",
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="c" * 64,
    )
    one_day_checkpoint = HistoryBackfillCheckpoint(
        ingestion_run_id=run.id,
        source_version_id=source.id,
        stream_key="mlb:2025-06-17",
        sequence=1,
        season=2025,
        event_start_at=datetime(2025, 6, 17, tzinfo=UTC),
        event_end_at=datetime(2025, 6, 17, 23, 59, tzinfo=UTC),
        query_fingerprint="d" * 64,
        status="SUCCEEDED",
        requested_at=datetime(2026, 8, 13, 19, tzinfo=UTC),
        completed_at=datetime(2026, 8, 13, 20, tzinfo=UTC),
        attempt_count=1,
        retry_count=0,
        records_received=1,
        records_accepted=1,
        records_rejected=0,
        duplicate_count=0,
        raw_references=[],
        raw_checksums=[],
        schema_version="fixture-v1",
        code_revision="test",
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="e" * 64,
    )
    session.add_all([checkpoint, one_day_checkpoint])
    session.flush()

    late_us_game = Game(
        league_id=league.id,
        season_id=season.id,
        starts_at=datetime(2025, 6, 18, 2, 10, tzinfo=UTC),
        status=GameStatus.FINAL,
        home_score=5,
        away_score=3,
        final_at=datetime(2025, 6, 18, 5, 10, tzinfo=UTC),
        source_mode="LIVE",
    )
    daytime_game = Game(
        league_id=league.id,
        season_id=season.id,
        starts_at=datetime(2025, 6, 17, 17, 10, tzinfo=UTC),
        status=GameStatus.FINAL,
        home_score=5,
        away_score=3,
        final_at=datetime(2025, 6, 17, 20, 10, tzinfo=UTC),
        source_mode="LIVE",
    )
    corrected_game = Game(
        league_id=league.id,
        season_id=season.id,
        starts_at=datetime(2025, 6, 18, 1, 10, tzinfo=UTC),
        status=GameStatus.FINAL,
        home_score=5,
        away_score=3,
        final_at=datetime(2025, 6, 18, 4, 10, tzinfo=UTC),
        source_mode="LIVE",
    )
    session.add_all([late_us_game, daytime_game, corrected_game])
    session.flush()

    late_outcome = _historical_outcome(
        game=late_us_game,
        season=season,
        home=home,
        away=away,
        source=source,
        checkpoint=checkpoint,
        official_date=date(2025, 6, 17),
        revision=1,
        checksum_character="d",
    )
    daytime_outcome = _historical_outcome(
        game=daytime_game,
        season=season,
        home=home,
        away=away,
        source=source,
        checkpoint=one_day_checkpoint,
        official_date=date(2025, 6, 17),
        revision=1,
        checksum_character="e",
    )
    old_correction = _historical_outcome(
        game=corrected_game,
        season=season,
        home=home,
        away=away,
        source=source,
        checkpoint=checkpoint,
        official_date=date(2025, 6, 17),
        revision=1,
        checksum_character="f",
    )
    session.add_all([late_outcome, daytime_outcome, old_correction])
    session.flush()
    latest_correction = _historical_outcome(
        game=corrected_game,
        season=season,
        home=home,
        away=away,
        source=source,
        checkpoint=checkpoint,
        official_date=date(2025, 6, 18),
        revision=2,
        checksum_character="1",
        supersedes_outcome_id=old_correction.id,
    )
    session.add(latest_correction)
    session.commit()

    selected = _select_historical_reconstruction_targets(
        session,
        start_date=date(2025, 6, 17),
        end_date=date(2025, 6, 17),
    )

    assert {row.id for row in selected} == {late_outcome.id, daytime_outcome.id}
    # Selection follows outcome official_date across both a one-day checkpoint
    # and a multi-day checkpoint; checkpoint chunk membership is irrelevant.
    assert {row.checkpoint_id for row in selected} == {
        checkpoint.id,
        one_day_checkpoint.id,
    }
    assert late_outcome.scheduled_at.date() == date(2025, 6, 18)
    assert latest_correction.id not in {row.id for row in selected}
    assert old_correction.id not in {row.id for row in selected}
