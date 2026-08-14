from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.data_sources import MLBStatsAPIAdapter, SourceError
from app.jobs import JobOrchestrator, seed_demo_data
from app.models import Forecast, Game, GameFeatureSnapshot, IngestionRun, JobRun, ModelStatus, ModelVersion, RunStatus


EVENT_DATE = date(2026, 8, 12)


def test_empty_database_public_read_contract(client) -> None:
    dashboard = client.get("/api/v1/dashboard/today").json()
    assert dashboard["data_mode"] == "empty"
    assert dashboard["games"] == []
    assert dashboard["summary"] == {
        "games": 0,
        "complete_forecasts": 0,
        "qualifying_edges": 0,
        "last_refresh": None,
        "overall_data_health": 0.0,
        "official_bankroll": 1000.0,
        "season_return": 0.0,
    }

    markets = client.get(f"/api/v1/markets/board?date={EVENT_DATE}").json()
    assert markets["data_mode"] == "empty"
    assert markets["books"] == []
    assert markets["rows"] == []
    assert markets["quota_remaining"] is None

    performance = client.get("/api/v1/performance").json()
    assert performance["data_mode"] == "empty"
    assert performance["model_status"] == "DEVELOPMENT"
    assert performance["evaluation_available"] is False
    assert performance["evaluated_games"] == 0
    for metric in ("brier_score", "log_loss", "accuracy", "run_mae", "total_mae", "versus_market_brier"):
        assert performance[metric] is None
    assert performance["model_components"] == []

    bankroll = client.get("/api/v1/bankroll").json()
    assert bankroll["data_mode"] == "empty"
    assert bankroll["experiment_status"] == "NOT_STARTED"
    assert bankroll["starting_bankroll"] == 1000.0
    assert bankroll["current_bankroll"] == 1000.0
    assert bankroll["peak_bankroll"] == 1000.0
    assert bankroll["total_return"] == 0.0
    assert bankroll["roi"] == 0.0
    assert bankroll["max_drawdown"] == 0.0
    assert bankroll["positions_count"] == 0
    assert bankroll["record"] == "0–0–0"
    assert bankroll["positions"] == []

    health = client.get(f"/api/v1/health/sources?date={EVENT_DATE}").json()
    assert health["data_mode"] == "empty"
    assert health["database_status"] == "ready"
    assert health["sports_data_status"] == "empty"
    assert health["provider_evidence_status"] == "none"
    assert health["overall_score"] == 0.0
    assert health["games_discovered"] == 0
    assert health["missing_games"] is None
    database = next(source for source in health["sources"] if source["id"] == "database")
    assert database["status"] == "ready"
    assert "no sports records" in database["detail"].lower()


def test_public_model_and_bankroll_policy_contract(client, session) -> None:
    # Internal enum state is not governed promotion evidence and must not leak
    # into the public contract.
    session.add(ModelVersion(
        name="ungoverned-test-model",
        version="0.0.1",
        algorithm="test-only",
        status=ModelStatus.CHAMPION,
    ))
    session.commit()

    payload = client.get("/api/v1/public/config").json()
    assert payload["data_mode"] == "empty"
    assert payload["model_status"] == "DEVELOPMENT"
    assert payload["component_weights"] == {"baseline": 0.30, "run": 0.45, "boosting": 0.25}
    assert payload["bankroll_policy"] == {
        "kelly_fraction": 0.25,
        "maximum_stake_percentage": 0.025,
        "standard_edge_threshold": 0.04,
        "label": "PROVISIONAL PHASE 1 BANKROLL POLICY",
    }

    performance = client.get("/api/v1/performance").json()
    dashboard = client.get("/api/v1/dashboard/today").json()
    health = client.get(f"/api/v1/health/sources?date={EVENT_DATE}").json()
    assert performance["model_status"] == "DEVELOPMENT"
    assert dashboard["model_status"] == "DEVELOPMENT"
    assert health["model_status"] == "DEVELOPMENT"
    assert performance["component_weights"] == {"baseline": 30.0, "run": 45.0, "boosting": 25.0}
    bankroll = client.get("/api/v1/bankroll").json()
    assert bankroll["policy_kelly_fraction"] == 0.25
    assert bankroll["policy_max_stake_percentage"] == 2.5
    assert bankroll["policy_edge_threshold_percentage"] == 4.0


@pytest.mark.asyncio
async def test_deliberately_seeded_demo_stays_off_the_public_today_surface(client, session, settings) -> None:
    started_at = datetime.now(UTC)
    result = await seed_demo_data(session, settings, EVENT_DATE)
    completed_at = datetime.now(UTC)

    forecast_rows = list(session.execute(select(Forecast, Game).join(Game, Game.id == Forecast.game_id)))
    assert result["steps"]["forecasts"]["records"] == len(forecast_rows) == 3
    first_pitch = min(game.starts_at for _, game in forecast_rows)
    assert all(
        started_at <= forecast.generated_at.replace(tzinfo=UTC) <= completed_at
        for forecast, _ in forecast_rows
    )
    assert all(forecast.source_mode == "DEMO" for forecast, _ in forecast_rows)
    snapshots = [
        session.get(GameFeatureSnapshot, forecast.feature_snapshot_id)
        for forecast, _ in forecast_rows
    ]
    assert all(snapshot is not None for snapshot in snapshots)
    assert all(
        snapshot.data_as_of.replace(tzinfo=UTC)
        == first_pitch.replace(tzinfo=UTC) - timedelta(minutes=90)
        for snapshot in snapshots if snapshot is not None
    )
    assert all(snapshot.source_mode == "DEMO" for snapshot in snapshots if snapshot is not None)

    dashboard = client.get("/api/v1/dashboard/today").json()
    markets = client.get(f"/api/v1/markets/board?date={EVENT_DATE}").json()
    health = client.get(f"/api/v1/health/sources?date={EVENT_DATE}").json()
    config = client.get("/api/v1/public/config").json()

    assert dashboard["data_mode"] == "empty"
    assert dashboard["games"] == []
    assert markets["data_mode"] == "demo"
    assert markets["rows"]
    assert health["data_mode"] == "demo"
    assert health["sports_data_status"] == "available"
    assert health["provider_evidence_status"] == "demo"
    assert config["data_mode"] == "demo"

    # No positions have been published merely because demo data exists.
    bankroll = client.get("/api/v1/bankroll").json()
    assert bankroll["data_mode"] == "empty"
    assert bankroll["experiment_status"] == "NOT_STARTED"
    assert bankroll["current_bankroll"] == 1000.0


@pytest.mark.asyncio
async def test_schedule_provider_failure_never_substitutes_demo(monkeypatch, session, settings) -> None:
    async def fail_schedule(adapter: MLBStatsAPIAdapter, event_date: date):
        raise SourceError("MLB_STATS_API", "provider unavailable")

    monkeypatch.setattr(MLBStatsAPIAdapter, "get_schedule", fail_schedule)

    with pytest.raises(SourceError, match="provider unavailable"):
        await JobOrchestrator(session, settings).run("refresh-schedule", event_date=EVENT_DATE)

    assert session.scalar(select(Game.id).limit(1)) is None
    run = session.scalar(select(JobRun).order_by(JobRun.created_at.desc()).limit(1))
    assert run is not None
    assert run.status == RunStatus.FAILED
    assert run.error_message == "provider unavailable"


@pytest.mark.asyncio
async def test_missing_odds_key_never_substitutes_demo(session, settings) -> None:
    settings_without_odds_key = settings.model_copy(update={"the_odds_api_key": ""})
    with pytest.raises(SourceError, match="THE_ODDS_API_KEY is not configured"):
        await JobOrchestrator(session, settings_without_odds_key).run("refresh-odds", event_date=EVENT_DATE)

    assert session.scalar(select(IngestionRun.id).where(IngestionRun.source == "ASP_DEMO_FIXTURE")) is None
    run = session.scalar(select(JobRun).order_by(JobRun.created_at.desc()).limit(1))
    assert run is not None
    assert run.status == RunStatus.FAILED
    assert run.error_message == "THE_ODDS_API_KEY is not configured"
