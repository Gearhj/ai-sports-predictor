"""The opportunity board surfaces live availability + immutable rescore evidence.

Seeds the existing demo slate, prices the board, then drives one manual
availability event through the real rescore path and asserts the board reflects
the persisted before/after evidence. No provider call, nothing fabricated.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.models import Game, Market, MarketType
from app.prospective.contracts import AvailabilityEventInput, AvailabilityEventType
from app.services.prospective_shadow_service import ProspectiveShadowService
from app.services.read_service import ReadService

SLATE = date(2026, 8, 12)


def _card(al_status, al_complete, hl_status, hl_complete, starters_confirmed):
    return SimpleNamespace(
        away_lineup=SimpleNamespace(status=al_status, complete=al_complete),
        home_lineup=SimpleNamespace(status=hl_status, complete=hl_complete),
        away_pitcher=SimpleNamespace(confirmed=starters_confirmed),
        home_pitcher=SimpleNamespace(confirmed=starters_confirmed),
    )


def test_lineup_confirmed_requires_complete_payload():
    full = ReadService._input_confirmation(_card("CONFIRMED", True, "CONFIRMED", True, True))
    assert full["lineup_confirmed"] is True and full["lineup_complete"] is True and full["starter_confirmed"] is True
    # A CONFIRMED status without the full persisted lineup payload is not confirmed.
    partial = ReadService._input_confirmation(_card("CONFIRMED", False, "CONFIRMED", True, True))
    assert partial["lineup_confirmed"] is False
    assert partial["lineup_complete"] is False
    assert partial["lineup_adequate"] is True  # still adequate for full-game markets
    # A projected lineup is adequate but not confirmed.
    projected = ReadService._input_confirmation(_card("PROJECTED", False, "PROJECTED", False, False))
    assert projected["lineup_confirmed"] is False and projected["lineup_adequate"] is True
    # A missing lineup is not adequate.
    missing = ReadService._input_confirmation(_card("MISSING", False, "PROJECTED", False, True))
    assert missing["lineup_adequate"] is False


async def _seed_game(session, settings):
    await seed_demo_data(session, settings, SLATE)
    market = session.scalar(select(Market).where(Market.market_type == MarketType.MONEYLINE))
    return session.get(Game, market.game_id)


@pytest.mark.asyncio
async def test_board_reports_injury_feed_not_configured(session, settings):
    game = await _seed_game(session, settings)
    read = ReadService(session, settings)
    card = read.game_card(game)
    forecast = read._latest_forecast(game.id)
    opportunities, summary = read._daily_opportunities([game], [card], [forecast])
    # No SportsDataIO credentials → the injury feed is honestly not configured.
    assert summary.injury_feed_status == "INJURY_FEED_NOT_CONFIGURED"
    assert opportunities[0].availability.injury_feed_status == "INJURY_FEED_NOT_CONFIGURED"
    # No availability event yet → nothing rescored, no fabricated before/after.
    assert opportunities[0].availability.rescored is False
    assert opportunities[0].availability.forecast_after is None
    assert opportunities[0].availability.last_availability_update is None


@pytest.mark.asyncio
async def test_unconfigured_feed_is_nonauthoritative_and_reasons_explain(session, settings):
    game = await _seed_game(session, settings)
    read = ReadService(session, settings)
    card = read.game_card(game)
    forecast = read._latest_forecast(game.id)
    opportunities, summary = read._daily_opportunities([game], [card], [forecast])
    opportunity = opportunities[0]
    availability = opportunity.availability
    # A non-authoritative feed must never claim "0 unavailable" (false-healthy).
    assert availability.injury_feed_authoritative is False
    assert availability.production_injury_coverage is False
    assert availability.unavailable_players is None
    assert summary.injury_feed_authoritative is False
    # Every non-qualified opportunity explains its blockers.
    assert opportunity.recommendation_state != "QUALIFIED_BET"
    assert "Production injury feed unavailable" in opportunity.qualification_reasons


@pytest.mark.asyncio
async def test_manual_event_rescore_surfaces_on_board(session, settings):
    game = await _seed_game(session, settings)
    read = ReadService(session, settings)

    # One clearly-labelled manual availability event → full rescore path.
    result = ProspectiveShadowService(session, settings).process_event(AvailabilityEventInput(
        event_type=AvailabilityEventType.LATE_SCRATCH,
        game_id=str(game.id),
        player_name="Test Player",
        description="Manual test scratch",
    ))
    assert result["games_rescored"] == 1

    # The board now surfaces the immutable rescore evidence from persisted state.
    card = read.game_card(game)
    forecast = read._latest_forecast(game.id)
    opportunities, _ = read._daily_opportunities([game], [card], [forecast])
    availability = next(o.availability for o in opportunities if o.game_id == str(game.id))
    assert availability.rescored is True
    assert availability.forecast_after is not None
    assert availability.last_availability_event == "LATE_SCRATCH"
    assert availability.last_availability_update is not None
    # A late scratch is reflected in the lineup-status display.
    assert availability.lineup_status == "CHANGED"
