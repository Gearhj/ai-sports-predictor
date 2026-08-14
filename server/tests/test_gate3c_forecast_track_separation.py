from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.audit import ImmutableRecordError
from app.bankroll.positions import publish_official_positions
from app.core.time import local_day_bounds, local_today
from app.jobs import JobOrchestrator, seed_demo_data
from app.models import (
    Forecast,
    ForecastLifecycle,
    ForecastStatus,
    ForecastTrack,
    Game,
    GameFeatureSnapshot,
    GameStatus,
    ModelVersion,
)
from app.services.read_service import ReadService


def _config(database_url: str) -> Config:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_forecast_track_migration_backfills_and_round_trips(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{(artifact_root / 'forecast-tracks.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("THE_ODDS_API_KEY", "")
    monkeypatch.setenv("ODDS_POLLING_ENABLED", "false")
    monkeypatch.setenv("OPEN_METEO_ENABLED", "false")
    monkeypatch.setenv("STATCAST_ENABLED", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    config = _config(database_url)

    command.upgrade(config, "20260814_0011")
    engine = create_engine(database_url)
    legacy_id = uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO forecasts ("
            "game_id, model_version_id, feature_version_id, version, generated_at, "
            "available_as_of, status, lifecycle, home_win_probability, "
            "away_win_probability, source_mode, id"
            ") VALUES ("
            ":game_id, :model_id, :feature_id, 1, :instant, :instant, "
            "'DRAFT', 'EARLY', 0.5, 0.5, 'LIVE', :id"
            ")"
        ), {
            "game_id": uuid4().hex,
            "model_id": uuid4().hex,
            "feature_id": uuid4().hex,
            "instant": "2026-08-14 12:00:00",
            "id": legacy_id,
        })
    assert "forecast_track" not in {
        column["name"] for column in inspect(engine).get_columns("forecasts")
    }
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    columns = {column["name"]: column for column in schema.get_columns("forecasts")}
    assert columns["forecast_track"]["nullable"] is False
    assert {
        item["name"]: item["column_names"]
        for item in schema.get_unique_constraints("forecasts")
    }["uq_forecast_game_track_version"] == ["game_id", "forecast_track", "version"]
    assert {
        item["name"]: item["column_names"]
        for item in schema.get_indexes("forecasts")
    }["ix_forecast_game_track_status_version"] == [
        "game_id", "forecast_track", "status", "version",
    ]
    assert "forecasttrack" in {
        item["name"] for item in schema.get_check_constraints("forecasts")
    }
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT forecast_track FROM forecasts WHERE id = :id"
        ), {"id": legacy_id}) == ForecastTrack.CURRENT_DEVELOPMENT.value
    engine.dispose()
    command.check(config)

    command.downgrade(config, "20260814_0011")
    engine = create_engine(database_url)
    assert "forecast_track" not in {
        column["name"] for column in inspect(engine).get_columns("forecasts")
    }
    engine.dispose()
    command.upgrade(config, "head")

    # A unique challenger version still cannot be represented safely by the
    # old schema: recency alone could make it operational after track removal.
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO forecasts ("
            "game_id, model_version_id, feature_version_id, forecast_track, version, "
            "generated_at, available_as_of, status, lifecycle, home_win_probability, "
            "away_win_probability, source_mode, id"
            ") VALUES ("
            ":game_id, :model_id, :feature_id, :forecast_track, 999, :instant, "
            ":instant, 'DRAFT', 'EARLY', 0.5, 0.5, 'LIVE', :id"
            ")"
        ), {
            "game_id": uuid4().hex,
            "model_id": uuid4().hex,
            "feature_id": uuid4().hex,
            "forecast_track": ForecastTrack.GEN2_RESEARCH_CHALLENGER.value,
            "instant": "2026-08-14 13:00:00",
            "id": uuid4().hex,
        })
    engine.dispose()
    with pytest.raises(RuntimeError, match="any non-current forecast exists"):
        command.downgrade(config, "20260814_0011")
    engine = create_engine(database_url)
    assert "forecast_track" in {
        column["name"] for column in inspect(engine).get_columns("forecasts")
    }
    engine.dispose()


def _forecast(
    *,
    game: Game,
    model: ModelVersion,
    snapshot: GameFeatureSnapshot,
    version: int,
    track: ForecastTrack,
    generated_at: datetime,
    home_probability: float,
    status: ForecastStatus = ForecastStatus.PUBLISHED,
) -> Forecast:
    return Forecast(
        game_id=game.id,
        model_version_id=model.id,
        feature_version_id=snapshot.feature_version_id,
        feature_snapshot_id=snapshot.id,
        forecast_track=track,
        version=version,
        generated_at=generated_at,
        available_as_of=generated_at,
        status=status,
        lifecycle=ForecastLifecycle.PREGAME,
        home_win_probability=home_probability,
        away_win_probability=1 - home_probability,
        expected_home_runs=4.8,
        expected_away_runs=4.1,
        expected_total_runs=8.9,
        expected_run_difference=0.7,
        confidence=70,
        readiness_score=90,
        source_mode="LIVE",
        published_at=generated_at if status == ForecastStatus.PUBLISHED else None,
    )


@pytest.mark.asyncio
async def test_research_challenger_cannot_win_operational_reads_by_recency(
    session,
    settings,
    client,
) -> None:
    await seed_demo_data(session, settings, date(2026, 8, 12))
    event_date = local_today(settings.display_timezone)
    game = session.scalar(select(Game).order_by(Game.starts_at))
    assert game is not None
    demo_forecast = session.scalar(
        select(Forecast)
        .where(Forecast.game_id == game.id)
        .order_by(Forecast.version.desc())
        .limit(1)
    )
    assert demo_forecast is not None
    demo_snapshot = session.get(GameFeatureSnapshot, demo_forecast.feature_snapshot_id)
    assert demo_snapshot is not None
    public_day_start, _ = local_day_bounds(event_date, settings.display_timezone)
    game.starts_at = public_day_start + timedelta(hours=18)
    generated_at = game.starts_at - timedelta(minutes=20)
    game.source_mode = "LIVE"
    live_snapshot = GameFeatureSnapshot(
        game_id=game.id,
        generated_at=generated_at,
        data_as_of=generated_at - timedelta(seconds=1),
        feature_schema_version="gate3c-track-test-v1",
        feature_version_id=demo_forecast.feature_version_id,
        bullpen_observation_ids=[],
        roster_observation_ids=[],
        statcast_observation_ids=[],
        market_snapshot_ids=[],
        source_evidence=[],
        feature_values={},
        feature_completeness=0,
        missing_features=[],
        stale_features=[],
        provisional_features=[],
        checksum="a" * 64,
        source_mode="LIVE",
        frozen_at=generated_at,
    )
    research_model = ModelVersion(
        name="mlb_gen2_research_challenger",
        version="2.0.0-test",
        algorithm="provider-free-test-only",
    )
    session.add_all([live_snapshot, research_model])
    session.flush()
    current = _forecast(
        game=game,
        model=session.get(ModelVersion, demo_forecast.model_version_id),
        snapshot=live_snapshot,
        version=2,
        track=ForecastTrack.CURRENT_DEVELOPMENT,
        generated_at=generated_at,
        home_probability=0.60,
    )
    challenger = _forecast(
        game=game,
        model=research_model,
        snapshot=live_snapshot,
        version=999,
        track=ForecastTrack.GEN2_RESEARCH_CHALLENGER,
        generated_at=generated_at + timedelta(seconds=30),
        home_probability=0.01,
    )
    # Version identity is scoped by track: the same game/version may coexist.
    challenger_same_version = _forecast(
        game=game,
        model=research_model,
        snapshot=live_snapshot,
        version=2,
        track=ForecastTrack.GEN2_RESEARCH_CHALLENGER,
        generated_at=generated_at + timedelta(seconds=20),
        home_probability=0.02,
        status=ForecastStatus.DRAFT,
    )
    session.add_all([current, challenger, challenger_same_version])
    session.commit()

    reader = ReadService(session, settings)
    assert reader._latest_forecast(game.id).id == current.id
    card = reader.game_card(game)
    assert card.home_win_probability == pytest.approx(60.0)
    analysis = reader.game_analysis(game.id)
    assert analysis is not None
    assert [row["home_probability"] for row in analysis.forecast_history] == [60.0]

    response = client.get(f"/api/v1/games/{game.id}")
    assert response.status_code == 200
    assert response.json()["game"]["home_win_probability"] == pytest.approx(60.0)
    dashboard = client.get(f"/api/v1/dashboard/today?date={event_date.isoformat()}")
    assert dashboard.status_code == 200
    assert len(dashboard.json()["games"]) == 1
    assert dashboard.json()["games"][0]["home_win_probability"] == pytest.approx(60.0)

    enabled = settings.model_copy(update={
        "official_positions_enabled": True,
        "opportunity_standard_threshold": 0.0,
    })
    positions = publish_official_positions(session, enabled, [game])
    session.commit()
    assert positions
    assert {position.forecast_id for position in positions} == {current.id}
    assert ReadService(session, enabled).bankroll().positions_count == len(positions)

    game.status = GameStatus.FINAL
    game.home_score = 5
    game.away_score = 3
    session.commit()
    performance = reader.performance()
    assert performance.evaluated_games == 1
    assert performance.brier_score == pytest.approx(0.16)
    assert performance.model_version.startswith(
        session.get(ModelVersion, current.model_version_id).name
    )
    health = reader.health(event_date)
    assert health.model_version.startswith(
        session.get(ModelVersion, current.model_version_id).name
    )
    assert health.latest_forecast == current.generated_at.isoformat()
    recalculated = await JobOrchestrator(session, settings).recalculate_performance(
        event_date
    )
    assert recalculated["evaluated_games"] == 1
    assert session.get(ModelVersion, current.model_version_id).metrics[
        "evaluated_games"
    ] == 1
    assert session.get(ModelVersion, research_model.id).metrics is None

    challenger_same_version.forecast_track = ForecastTrack.CURRENT_DEVELOPMENT
    with pytest.raises(ImmutableRecordError, match="track is immutable"):
        session.commit()
    session.rollback()

    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE forecasts SET forecast_track = 'INVALID' WHERE id = :id"),
            {"id": challenger.id.hex},
        )
        session.commit()
    session.rollback()

    duplicate = _forecast(
        game=game,
        model=research_model,
        snapshot=live_snapshot,
        version=999,
        track=ForecastTrack.GEN2_RESEARCH_CHALLENGER,
        generated_at=generated_at + timedelta(seconds=40),
        home_probability=0.03,
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
