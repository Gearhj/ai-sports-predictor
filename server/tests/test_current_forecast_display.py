"""Intraday current-forecast activation.

Today/Game Analysis must display the newest VALID point-in-time version — a
governed intraday rescore supersedes the morning forecast for display — while
every earlier version stays immutable in history at its own cutoff. The
scheduled odds cadence only spends quota inside the pregame window.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.jobs.orchestrator import JobOrchestrator
from app.models import Forecast, ForecastStatus, Game, GameTeam, Market, MarketType
from app.prospective.contracts import AvailabilityEventInput, AvailabilityEventType
from app.services.prospective_shadow_service import ProspectiveShadowService
from app.services.read_service import ReadService

SLATE = date(2026, 8, 12)


async def _seed_game(session, settings):
    await seed_demo_data(session, settings, SLATE)
    market = session.scalar(select(Market).where(Market.market_type == MarketType.MONEYLINE))
    return session.get(Game, market.game_id)


@pytest.mark.asyncio
async def test_display_uses_latest_rescore_and_history_keeps_morning(session, settings):
    game = await _seed_game(session, settings)
    read = ReadService(session, settings)

    morning = read._latest_forecast(game.id)
    assert morning is not None and morning.status == ForecastStatus.PUBLISHED
    morning_version, morning_prob = morning.version, morning.home_win_probability

    # A governed intraday rescore (confirmed lineup) persists a new version.
    ProspectiveShadowService(session, settings).process_event(AvailabilityEventInput(
        event_type=AvailabilityEventType.LINEUP_CONFIRMED,
        game_id=str(game.id),
        published_at=datetime.now(UTC) - timedelta(seconds=30),
        description="Lineup status None -> CONFIRMED",
    ))

    # The display now selects the newer version; the morning row is untouched.
    current = read._latest_forecast(game.id)
    assert current is not None
    assert current.version == morning_version + 1
    assert current.id != morning.id
    session.refresh(morning)
    assert morning.home_win_probability == morning_prob  # immutable, not mutated

    analysis = read.game_analysis(game.id)
    versions = [item["version"] for item in analysis.forecast_history]
    assert len(analysis.forecast_history) >= 2  # morning + rescore both present
    assert f"v{morning_version}" in " ".join(versions)
    assert f"v{morning_version + 1}" in analysis.forecast_version  # current is the rescore
    assert analysis.forecast_history[-1]["rescore_reason"]  # reason recorded on the newest


@pytest.mark.asyncio
async def test_history_versions_are_point_in_time(session, settings):
    game = await _seed_game(session, settings)
    read = ReadService(session, settings)
    morning = read._latest_forecast(game.id)
    morning_entry_prob = round(morning.home_win_probability * 100, 6)

    ProspectiveShadowService(session, settings).process_event(AvailabilityEventInput(
        event_type=AvailabilityEventType.LINEUP_CONFIRMED, game_id=str(game.id),
        published_at=datetime.now(UTC) - timedelta(seconds=30),
    ))

    history = read.game_analysis(game.id).forecast_history
    morning_row = history[0]
    # The historical row still reports the morning forecast's own probability,
    # never the newest version's — no grafting of later inputs onto an old row.
    assert round(morning_row["home_probability"], 6) == morning_entry_prob
    assert morning_row["rescore_reason"] is None  # the baseline was not a rescore


@pytest.mark.asyncio
async def test_scheduled_odds_refresh_is_gated_to_pregame_window(session, settings):
    await seed_demo_data(session, settings, SLATE)
    market = session.scalar(select(Market).where(Market.market_type == MarketType.MONEYLINE))
    game = session.get(Game, market.game_id)
    game.source_mode = "LIVE"
    orch = JobOrchestrator(session, settings)
    now = datetime.now(UTC)

    game.starts_at = now + timedelta(hours=settings.odds_pregame_window_hours + 3)
    session.add(game); session.commit()
    assert orch.odds_pregame_window_open(now) is False  # far out -> no quota spent

    game.starts_at = now + timedelta(hours=1)  # inside the window
    session.add(game); session.commit()
    assert orch.odds_pregame_window_open(now) is True

    game.starts_at = now - timedelta(minutes=10)  # already started
    session.add(game); session.commit()
    assert orch.odds_pregame_window_open(now) is False
