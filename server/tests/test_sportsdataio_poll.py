"""SportsDataIO injuries poll wired into the existing availability job.

Verifies: trial data records Data Health but creates no events; a provider
failure fabricates nothing; identity resolves to a live game by team (and never
to a blank/trial team). The provider fetch is mocked — no network, no key.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.jobs.orchestrator import JobOrchestrator
from app.models import AvailabilityEvent, Game, GameTeam, IngestionRun, Market, MarketType, RunStatus, Team
from app.prospective.contracts import AvailabilityEventInput, AvailabilityEventType
from app.prospective.sportsdataio import STATUS_ERROR, STATUS_TRIAL, SportsDataIOAvailabilityAdapter

SLATE = date(2026, 8, 12)


def _configured(settings):
    return settings.model_copy(update={"sportsdataio_api_key": "k" * 24, "sportsdataio_enabled": True})


@pytest.mark.asyncio
async def test_poll_trial_data_records_health_but_no_events(session, settings, monkeypatch):
    configured = _configured(settings)
    trial_rows = [{"PlayerID": str(i), "InjuryStatus": "Scrambled", "InjuryBodyPart": "Scrambled",
                   "Status": "Inactive", "Team": None} for i in range(6)]

    async def fake_fetch(self, *, client=None):
        return (200, trial_rows)

    monkeypatch.setattr(SportsDataIOAvailabilityAdapter, "fetch_injuries", fake_fetch)
    result = await JobOrchestrator(session, configured)._poll_sportsdataio_injuries(SLATE, datetime.now(UTC))

    assert result["configured"] is True
    assert result["trial"] is True
    assert result["records"] == 6
    assert result["events_created"] == 0            # scrambled data never fabricates events
    run = session.scalar(select(IngestionRun).where(IngestionRun.source == "SPORTSDATAIO"))
    assert run.status == RunStatus.SUCCEEDED
    assert (run.source_metadata or {}).get("trial") is True
    assert SportsDataIOAvailabilityAdapter(configured).status(session) == STATUS_TRIAL


@pytest.mark.asyncio
async def test_poll_provider_failure_fabricates_nothing(session, settings, monkeypatch):
    configured = _configured(settings)

    async def failing_fetch(self, *, client=None):
        return (503, [])

    monkeypatch.setattr(SportsDataIOAvailabilityAdapter, "fetch_injuries", failing_fetch)
    result = await JobOrchestrator(session, configured)._poll_sportsdataio_injuries(SLATE, datetime.now(UTC))

    assert result.get("error") is True
    assert result["events_created"] == 0
    assert session.query(AvailabilityEvent).count() == 0
    run = session.scalar(select(IngestionRun).where(IngestionRun.source == "SPORTSDATAIO"))
    assert run.status == RunStatus.FAILED
    assert SportsDataIOAvailabilityAdapter(configured).status(session) == STATUS_ERROR


@pytest.mark.asyncio
async def test_disabled_feed_skips_poll(session, settings, monkeypatch):
    def _boom(self, *, client=None):  # must never be called when unconfigured
        raise AssertionError("fetch must not run without credentials")

    monkeypatch.setattr(SportsDataIOAvailabilityAdapter, "fetch_injuries", _boom)
    result = await JobOrchestrator(session, settings)._poll_sportsdataio_injuries(SLATE, datetime.now(UTC))
    assert result == {"configured": False, "records": 0, "events_created": 0}


@pytest.mark.asyncio
async def test_resolver_matches_live_game_by_team_and_never_forces(session, settings):
    await seed_demo_data(session, settings, SLATE)
    market = session.scalar(select(Market).where(Market.market_type == MarketType.MONEYLINE))
    game = session.get(Game, market.game_id)
    game.source_mode = "LIVE"  # resolver targets the live slate
    home = session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
    team = session.get(Team, home.team_id)
    session.commit()

    orch = JobOrchestrator(session, settings)
    matched = orch._resolve_injury_game(
        AvailabilityEventInput(event_type=AvailabilityEventType.CONFIRMED_OUT,
                               team_source_id=team.abbreviation, player_source_id="1"),
        SLATE,
    )
    assert matched == game.id
    # A blank team (trial-scrambled) never resolves to a game — no forced mapping.
    assert orch._resolve_injury_game(
        AvailabilityEventInput(event_type=AvailabilityEventType.CONFIRMED_OUT), SLATE) is None
