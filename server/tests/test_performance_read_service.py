from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.models import Forecast, Game, GameStatus
from app.services.read_service import ReadService


@pytest.mark.asyncio
async def test_settled_performance_populates_auditable_metrics(session, settings) -> None:
    await seed_demo_data(session, settings, date(2026, 8, 12))
    games = list(session.scalars(select(Game).order_by(Game.starts_at)))
    assert games
    for index, game in enumerate(games):
        forecast = session.scalar(
            select(Forecast)
            .where(Forecast.game_id == game.id)
            .order_by(Forecast.version.desc())
            .limit(1)
        )
        assert forecast is not None
        game.starts_at = max(game.starts_at, forecast.generated_at + timedelta(minutes=1))
        game.status = GameStatus.FINAL
        game.home_score = 5 + index % 3
        game.away_score = 3 + index % 2
    session.commit()

    performance = ReadService(session, settings).performance()

    assert performance.evaluation_available is True
    assert performance.evaluated_games == len(games)
    assert performance.brier_score is not None
    assert performance.log_loss is not None
    assert performance.run_mae is not None
    assert performance.total_mae is not None
    assert performance.market_benchmark_available is True
    assert performance.versus_market_brier is not None
    assert performance.rolling
    assert performance.by_confidence
    assert len(performance.model_components) > 1
