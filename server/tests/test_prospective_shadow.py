"""Prospective live shadow pilot: availability-event-triggered rescoring.

Uses the existing AISP forecast pipeline on a seeded demo slate. All odds are
real persisted observations (seeded here, never fabricated by product code).
Every decision is virtual and non-executable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.models import (
    AvailabilityEvent,
    Forecast,
    Game,
    GameTeam,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    ShadowRescore,
)
from app.prospective.contracts import AvailabilityEventInput, AvailabilityEventType
from app.prospective.event_sources import MLBDerivedAvailabilityEventSource
from app.services.prospective_shadow_service import ProspectiveShadowService, RescoreError

SLATE = date(2026, 8, 12)


async def _seed_slate(session, settings):
    await seed_demo_data(session, settings, SLATE)
    # Pick a game that has a two-sided moneyline market.
    market = session.scalar(select(Market).where(Market.market_type == MarketType.MONEYLINE))
    assert market is not None
    return session.get(Game, market.game_id)


def _add_better_home_price(session, game, *, american=+250):
    """Seed one real (labelled synthetic-test) improved home moneyline price."""
    home_team = session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
    market = session.scalar(select(Market).where(Market.game_id == game.id, Market.market_type == MarketType.MONEYLINE))
    outcome = session.scalar(select(MarketOutcome).where(
        MarketOutcome.market_id == market.id, MarketOutcome.participant_team_id == home_team.team_id))
    now = datetime.now(UTC)
    from app.markets.odds import american_to_decimal
    session.add(OddsSnapshot(
        market_outcome_id=outcome.id,
        sportsbook_id=session.scalar(select(OddsSnapshot.sportsbook_id).where(OddsSnapshot.market_outcome_id == outcome.id)),
        observed_at=now, available_as_of=now,
        american_odds=american, decimal_odds=american_to_decimal(american),
        source="SYNTHETIC_TEST_BOOK", source_mode="DEMO",
    ))
    session.commit()


@pytest.mark.asyncio
async def test_availability_event_flows_through_full_rescore_path(session, settings):
    game = await _seed_slate(session, settings)
    svc = ProspectiveShadowService(session, settings)

    result = svc.process_event(AvailabilityEventInput(
        event_type=AvailabilityEventType.LATE_SCRATCH,
        game_id=str(game.id),
        player_name="Test Player",
        published_at=datetime.now(UTC) - timedelta(seconds=30),
        description="Star player scratched from the lineup",
    ))

    assert result["games_rescored"] == 1
    event = session.query(AvailabilityEvent).one()
    assert event.source == "MANUAL_ADMIN"
    assert event.event_type == "LATE_SCRATCH"
    rescore = session.query(ShadowRescore).one()
    # Complete lineage: event -> before/after forecast -> quotes -> decision.
    assert rescore.availability_event_id == event.id
    assert rescore.after_forecast_id is not None
    assert rescore.before_forecast_id is not None
    assert rescore.after_forecast_id != rescore.before_forecast_id  # a new immutable version
    assert rescore.after_home_win_probability is not None
    # Timing recorded.
    assert rescore.aisp_received_at is not None
    assert rescore.rescore_started_at <= rescore.rescore_completed_at
    assert rescore.source_published_at is not None
    # Decisions are present and virtual.
    assert set(rescore.decisions["after"].keys()) == {"HOME", "AWAY"}


@pytest.mark.asyncio
async def test_real_quotes_snapshotted_and_book_move_detected(session, settings):
    game = await _seed_slate(session, settings)
    svc = ProspectiveShadowService(session, settings)

    def refresher(g):
        _add_better_home_price(session, g, american=+400)

    result = svc.process_event(
        AvailabilityEventInput(event_type=AvailabilityEventType.PROBABLE_PITCHER_CHANGED, game_id=str(game.id)),
        odds_refresher=refresher,
    )
    rescore = session.query(ShadowRescore).one()
    assert rescore.pre_quote is not None  # real persisted odds captured
    assert rescore.post_quote is not None
    assert rescore.book_moved is True  # the refresh moved the home price
    assert rescore.pre_quote["home_american"] != rescore.post_quote["home_american"]
    assert result["rescores"][0]["book_moved"] is True


@pytest.mark.asyncio
async def test_unresolved_event_fails_closed(session, settings):
    import uuid
    await seed_demo_data(session, settings, SLATE)
    svc = ProspectiveShadowService(session, settings)
    with pytest.raises(RescoreError):
        svc.process_event(AvailabilityEventInput(
            event_type=AvailabilityEventType.CONFIRMED_OUT, game_id=str(uuid.uuid4())))
    session.rollback()
    assert session.query(ShadowRescore).count() == 0


@pytest.mark.asyncio
async def test_no_quote_yields_no_fabricated_price(session, settings):
    game = await _seed_slate(session, settings)
    svc = ProspectiveShadowService(session, settings)
    forecast = svc._latest_forecast(game)
    # With no governed quote, decisions are NO_BET and carry no invented price.
    decisions = svc._moneyline_decisions(forecast, None)
    for side in ("HOME", "AWAY"):
        assert decisions[side]["has_quote"] is False
        assert decisions[side]["recommendation"] == "NO_BET"
        assert decisions[side]["no_vig_probability"] is None
        assert decisions[side]["virtual_stake"] == 0.0


@pytest.mark.asyncio
async def test_idempotent_event_processing(session, settings):
    game = await _seed_slate(session, settings)
    svc = ProspectiveShadowService(session, settings)
    published = datetime.now(UTC)
    payload = AvailabilityEventInput(
        event_type=AvailabilityEventType.LINEUP_CONFIRMED, game_id=str(game.id), published_at=published)
    svc.process_event(payload)
    second = svc.process_event(payload)
    assert second["games_rescored"] == 0  # same event key -> no new rescore
    assert session.query(AvailabilityEvent).count() == 1
    assert session.query(ShadowRescore).count() == 1


@pytest.mark.asyncio
async def test_event_and_rescore_are_immutable(session, settings):
    from app.audit.immutability import ImmutableRecordError
    game = await _seed_slate(session, settings)
    svc = ProspectiveShadowService(session, settings)
    svc.process_event(AvailabilityEventInput(event_type=AvailabilityEventType.LATE_SCRATCH, game_id=str(game.id)))
    rescore = session.query(ShadowRescore).one()
    with pytest.raises(ImmutableRecordError):
        session.delete(rescore)
        session.flush()
    session.rollback()
    event = session.query(AvailabilityEvent).one()
    with pytest.raises(ImmutableRecordError):
        event.event_type = "ACTIVATED"
        session.flush()
    session.rollback()


def test_mlb_derived_probable_pitcher_diff():
    # Pure diff over the configured MLB Stats API probable map; no fabrication.
    events = MLBDerivedAvailabilityEventSource.diff_probable_pitchers(
        fresh_probables={"g1": "pitcher-new", "g2": "pitcher-same", "g3": "pitcher-first"},
        persisted_probables={"g1": "pitcher-old", "g2": "pitcher-same", "g3": None},
    )
    assert len(events) == 1
    assert events[0].event_type == AvailabilityEventType.PROBABLE_PITCHER_CHANGED
    assert events[0].game_id == "g1"


@pytest.mark.asyncio
async def test_rescore_board_api_and_admin_submission(client, session, settings, admin_headers):
    game = await _seed_slate(session, settings)

    # Admin submits a manual availability event (manual adapter).
    response = client.post(
        "/api/v1/admin/availability-events",
        headers=admin_headers,
        json={"event_type": "LATE_SCRATCH", "game_id": str(game.id), "player_name": "Scratched"},
    )
    assert response.status_code == 200
    assert response.json()["games_rescored"] == 1

    # Non-admin cannot submit.
    assert client.post("/api/v1/admin/availability-events",
                       json={"event_type": "LATE_SCRATCH", "game_id": str(game.id)}).status_code == 401

    # Public read board (no provider call).
    board = client.get(f"/api/v1/shadow-board/rescores?date={SLATE.isoformat()}")
    assert board.status_code == 200
    body = board.json()
    assert body["status"] == "RESEARCH_SHADOW"
    assert body["executable"] is False
    assert len(body["rescores"]) == 1
    row = body["rescores"][0]
    assert row["availability_change"]["event_type"] == "LATE_SCRATCH"
    assert "forecast_before" in row and "forecast_after" in row
    assert "timing" in row and row["timing"]["rescore_completed_at"] is not None
