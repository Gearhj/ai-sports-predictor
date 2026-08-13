from __future__ import annotations

from datetime import UTC, date, timedelta

import pytest
from sqlalchemy import select

from app.jobs.orchestrator import JobOrchestrator
from app.models import (
    FeatureVersion,
    Forecast,
    ForecastLifecycle,
    ForecastStatus,
    Game,
    GameRefreshState,
    GameTeam,
    LineupObservation,
    ModelVersion,
)
from app.services.read_service import ReadService


@pytest.mark.asyncio
async def test_game_card_exposes_persisted_lifecycle_lineups_and_readiness(session, settings) -> None:
    event_date = date(2026, 8, 12)
    await JobOrchestrator(session, settings).refresh_schedule(event_date, demo_only=True)
    game = session.scalar(select(Game).order_by(Game.starts_at))
    assert game is not None
    sides = list(session.scalars(select(GameTeam).where(GameTeam.game_id == game.id)))
    home = next(side for side in sides if side.is_home)
    away = next(side for side in sides if not side.is_home)
    starts_at = game.starts_at if game.starts_at.tzinfo else game.starts_at.replace(tzinfo=UTC)
    data_as_of = starts_at - timedelta(minutes=40)
    source_refresh = data_as_of - timedelta(minutes=2)
    next_refresh = data_as_of + timedelta(minutes=10)

    feature_version = session.scalar(select(FeatureVersion).order_by(FeatureVersion.created_at))
    assert feature_version is not None
    model = ModelVersion(name="gate2-read-model", version="test", algorithm="test")
    session.add(model)
    session.flush()
    forecast = Forecast(
        game_id=game.id,
        model_version_id=model.id,
        feature_version_id=feature_version.id,
        version=1,
        generated_at=data_as_of,
        available_as_of=data_as_of,
        status=ForecastStatus.PUBLISHED,
        lifecycle=ForecastLifecycle.PREGAME,
        home_win_probability=0.55,
        away_win_probability=0.45,
        readiness_score=82,
        readiness_components={
            "lineups": {
                "status": "stale",
                "score": 7.5,
                "weight": 10,
                "detail": "Home confirmed; away projected",
                "critical": True,
            }
        },
        source_mode="DEMO",
        published_at=data_as_of,
    )
    session.add(forecast)
    session.add_all([
        LineupObservation(
            game_id=game.id,
            team_id=away.team_id,
            lineup_status="PROJECTED",
            is_complete=False,
            observed_at=data_as_of - timedelta(minutes=5),
            available_as_of=data_as_of - timedelta(minutes=4),
            source="TEST_LINEUP",
            source_mode="DEMO",
        ),
        LineupObservation(
            game_id=game.id,
            team_id=home.team_id,
            lineup_status="CONFIRMED",
            is_complete=True,
            observed_at=data_as_of - timedelta(minutes=5),
            available_as_of=data_as_of - timedelta(minutes=4),
            source="TEST_LINEUP",
            source_mode="DEMO",
        ),
        GameRefreshState(
            game_id=game.id,
            lifecycle_stage="T_MINUS_60M",
            evaluated_at=data_as_of,
            available_as_of=data_as_of,
            first_pitch_at=starts_at,
            time_to_first_pitch_seconds=2400,
            last_source_refresh_at=source_refresh,
            last_forecast_id=forecast.id,
            next_scheduled_refresh_at=next_refresh,
            final_forecast_status="NOT_STARTED",
            final_snapshot_frozen=False,
            source_mode="DEMO",
        ),
    ])
    session.commit()

    card = ReadService(session, settings).game_card(game)
    assert card.data_as_of == data_as_of
    assert card.lifecycle_stage == "T_MINUS_60M"
    assert card.time_to_first_pitch_seconds == 2400
    assert card.last_refresh_at == source_refresh
    assert card.next_scheduled_refresh_at == next_refresh
    assert card.final_forecast_status == "NOT_STARTED"
    assert card.final_snapshot_frozen is False
    assert card.away_lineup.status == "PROJECTED"
    assert card.away_lineup.complete is False
    assert card.home_lineup.status == "CONFIRMED"
    assert card.home_lineup.complete is True
    assert card.readiness_detail[0].key == "lineups"
    assert card.readiness_detail[0].critical is True
    assert card.readiness_detail[0].detail == "Home confirmed; away projected"
