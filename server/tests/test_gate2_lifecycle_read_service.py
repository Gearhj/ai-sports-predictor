from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.data_sources.base import ScheduleGameDTO, TeamDTO, VenueDTO
from app.ingestion import CanonicalNormalizer
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
from app.services.forecast_service import ForecastService
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


def test_game_card_exposes_failed_publication_snapshot_readiness_without_refresh_state(
    session,
    settings,
) -> None:
    first_pitch = datetime(2026, 8, 13, 23, 5, tzinfo=UTC)
    cutoff = first_pitch - timedelta(hours=6)
    game = CanonicalNormalizer(session).ingest_schedule(
        [
            ScheduleGameDTO(
                source_game_id="gate2-readiness-fallback-live",
                scheduled_at=first_pitch,
                event_date=first_pitch.date(),
                status="scheduled",
                away_team=TeamDTO(
                    source_id="gate2-readiness-away",
                    name="Readiness Away",
                    abbreviation="RDA",
                ),
                home_team=TeamDTO(
                    source_id="gate2-readiness-home",
                    name="Readiness Home",
                    abbreviation="RDH",
                ),
                venue=VenueDTO(
                    source_id="gate2-readiness-park",
                    name="Readiness Park",
                ),
                source_mode="LIVE",
            )
        ],
        "GATE2_READINESS_TEST",
        available_as_of=cutoff,
    )[0]
    session.commit()

    draft = ForecastService(session, settings).generate_for_game(
        game,
        data_as_of=cutoff,
        force_draft=True,
    )
    session.commit()

    assert draft.status == ForecastStatus.DRAFT
    assert session.scalar(
        select(GameRefreshState.id).where(GameRefreshState.game_id == game.id).limit(1)
    ) is None

    card = ReadService(session, settings).game_card(game)
    details = {component.key: component for component in card.readiness_detail}

    assert card.forecast_available is False
    assert card.data_as_of == cutoff
    assert card.data_readiness == draft.readiness_score
    assert details["probable_pitchers"].status == "missing"
    assert details["probable_pitchers"].critical is True
    assert details["probable_pitchers"].score == 0
    assert details["feature_completeness"].detail.startswith("0% measured coverage")
    assert card.last_refresh_at is None
