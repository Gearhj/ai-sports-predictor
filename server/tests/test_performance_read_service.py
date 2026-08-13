from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.models import (
    Forecast,
    ForecastComponent,
    Game,
    GameFeatureSnapshot,
    GameStatus,
    MarketComparison,
)
from app.services.read_service import ReadService


@pytest.mark.asyncio
async def test_settled_performance_populates_auditable_metrics(session, settings) -> None:
    """Exercise the production metric path with explicitly synthetic LIVE rows.

    Demo fixtures themselves are intentionally excluded from public model
    performance, so this test promotes only its local test scaffolding and
    supplies a distinct LIVE feature snapshot for each forecast.
    """
    await seed_demo_data(session, settings, date(2026, 8, 12))
    games = list(session.scalars(select(Game).order_by(Game.starts_at)))
    assert games
    for index, game in enumerate(games):
        demo_forecast = session.scalar(
            select(Forecast)
            .where(Forecast.game_id == game.id)
            .order_by(Forecast.version.desc())
            .limit(1)
        )
        assert demo_forecast is not None
        game.starts_at = max(game.starts_at, demo_forecast.generated_at + timedelta(minutes=1))
        live_snapshot = GameFeatureSnapshot(
            game_id=game.id,
            generated_at=demo_forecast.generated_at,
            data_as_of=demo_forecast.generated_at - timedelta(minutes=1),
            feature_schema_version="performance-test-live-v1",
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
            checksum=f"{index + 1:064x}",
            source_mode="LIVE",
            frozen_at=demo_forecast.generated_at,
        )
        session.add(live_snapshot)
        session.flush()
        game.source_mode = "LIVE"
        forecast = Forecast(
            game_id=game.id,
            model_version_id=demo_forecast.model_version_id,
            feature_version_id=demo_forecast.feature_version_id,
            feature_snapshot_id=live_snapshot.id,
            version=demo_forecast.version + 1,
            generated_at=demo_forecast.generated_at,
            available_as_of=demo_forecast.generated_at,
            status=demo_forecast.status,
            lifecycle=demo_forecast.lifecycle,
            home_win_probability=demo_forecast.home_win_probability,
            away_win_probability=demo_forecast.away_win_probability,
            expected_home_runs=demo_forecast.expected_home_runs,
            expected_away_runs=demo_forecast.expected_away_runs,
            confidence=demo_forecast.confidence,
            readiness_score=demo_forecast.readiness_score,
            fair_home_moneyline=demo_forecast.fair_home_moneyline,
            fair_away_moneyline=demo_forecast.fair_away_moneyline,
            expected_total_runs=demo_forecast.expected_total_runs,
            expected_run_difference=demo_forecast.expected_run_difference,
            uncertainty=demo_forecast.uncertainty,
            readiness_components=demo_forecast.readiness_components,
            model_drivers=demo_forecast.model_drivers,
            feature_snapshot_reference=f"sha256:{live_snapshot.checksum}",
            source_mode="LIVE",
            simulation_summary=demo_forecast.simulation_summary,
            published_at=demo_forecast.generated_at,
        )
        session.add(forecast)
        session.flush()
        for component in session.scalars(select(ForecastComponent).where(
            ForecastComponent.forecast_id == demo_forecast.id
        )):
            session.add(ForecastComponent(
                forecast_id=forecast.id,
                component_name=component.component_name,
                weight=component.weight,
                output=component.output,
            ))
        for comparison in session.scalars(select(MarketComparison).where(
            MarketComparison.forecast_id == demo_forecast.id
        )):
            session.add(MarketComparison(
                forecast_id=forecast.id,
                market_outcome_id=comparison.market_outcome_id,
                observed_at=comparison.observed_at,
                asp_probability=comparison.asp_probability,
                market_no_vig_probability=comparison.market_no_vig_probability,
                edge=comparison.edge,
                best_decimal_odds=comparison.best_decimal_odds,
                consensus_decimal_odds=comparison.consensus_decimal_odds,
            ))
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


@pytest.mark.asyncio
async def test_demo_settlements_are_not_public_model_performance(session, settings) -> None:
    await seed_demo_data(session, settings, date(2026, 8, 12))
    for game in session.scalars(select(Game)):
        game.status = GameStatus.FINAL
        game.home_score = 5
        game.away_score = 3
    session.commit()

    performance = ReadService(session, settings).performance()
    assert performance.evaluation_available is False
    assert performance.evaluated_games == 0
