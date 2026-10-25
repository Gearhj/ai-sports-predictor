from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.bankroll.positions import publish_official_positions
from app.bankroll.settlement import determine_result, settle_completed_positions
from app.jobs import seed_demo_data
from app.models import (
    Game,
    GameStatus,
    Market,
    MarketOutcome,
    MarketType,
    OfficialAspPosition,
    PositionResult,
    PositionStatus,
)
from app.services.read_service import ReadService


@pytest.mark.asyncio
async def test_explicit_position_publication_is_flagged_and_idempotent(session, settings) -> None:
    await seed_demo_data(session, settings, date(2026, 8, 12))
    games = list(session.scalars(select(Game)))
    assert publish_official_positions(session, settings, games) == []
    enabled = settings.model_copy(update={
        "official_positions_enabled": True,
        "opportunity_standard_threshold": 0.0,
    })
    first = publish_official_positions(session, enabled, games)
    session.commit()
    assert first
    assert all(position.strategy == "PROVISIONAL PHASE 1 BANKROLL POLICY" for position in first)
    assert all(position.odds_snapshot_id is not None for position in first)
    assert publish_official_positions(session, enabled, games) == []


@pytest.mark.asyncio
async def test_moneyline_positions_settle_from_canonical_final_score(session, settings) -> None:
    await seed_demo_data(session, settings, date(2026, 8, 12))
    games = list(session.scalars(select(Game)))
    enabled = settings.model_copy(update={"official_positions_enabled": True, "opportunity_standard_threshold": 0.0})
    positions = publish_official_positions(session, enabled, games)
    assert positions
    for game in games:
        game.status = GameStatus.FINAL
        game.home_score = 6
        game.away_score = 3
    session.commit()
    settlements = settle_completed_positions(session)
    session.commit()
    assert len(settlements) == len(positions)
    assert all(position.status == PositionStatus.SETTLED for position in positions)
    assert all(position.result in {PositionResult.WIN, PositionResult.LOSS} for position in positions)


@pytest.mark.asyncio
async def test_total_push_and_unmatched_moneyline_void(session, settings) -> None:
    await seed_demo_data(session, settings, date(2026, 8, 12))
    game = session.scalar(select(Game).order_by(Game.starts_at))
    game.status = GameStatus.FINAL
    game.home_score = 5
    game.away_score = 3
    total_market = session.scalar(select(Market).where(Market.game_id == game.id, Market.market_type == MarketType.TOTAL))
    over_eight = session.scalar(select(MarketOutcome).where(
        MarketOutcome.market_id == total_market.id,
        MarketOutcome.label == "Over",
        MarketOutcome.outcome_key.like("%:8.0"),
    ))
    if over_eight is None:
        over_eight = MarketOutcome(market_id=total_market.id, outcome_key="over:8.0", label="Over")
        session.add(over_eight)
        session.flush()
    assert determine_result(session, game, total_market, over_eight) == PositionResult.PUSH
    moneyline = session.scalar(select(Market).where(Market.game_id == game.id, Market.market_type == MarketType.MONEYLINE))
    unmatched = MarketOutcome(market_id=moneyline.id, outcome_key="unknown:None", label="Unknown", participant_team_id=None)
    session.add(unmatched)
    session.flush()
    assert determine_result(session, game, moneyline, unmatched) == PositionResult.VOID


@pytest.mark.asyncio
async def test_a_moneyline_tie_is_a_push_for_both_sides(session, settings) -> None:
    await seed_demo_data(session, settings, date(2026, 8, 12))
    game = session.scalar(select(Game).order_by(Game.starts_at))
    game.status = GameStatus.FINAL
    game.home_score = 20
    game.away_score = 20
    moneyline = session.scalar(select(Market).where(Market.game_id == game.id, Market.market_type == MarketType.MONEYLINE))
    sides = [o for o in session.scalars(select(MarketOutcome).where(MarketOutcome.market_id == moneyline.id))
             if o.participant_team_id is not None]
    assert len(sides) == 2
    assert {determine_result(session, game, moneyline, side) for side in sides} == {PositionResult.PUSH}


@pytest.mark.asyncio
async def test_bankroll_api_schema_accepts_void_position(session, settings) -> None:
    await seed_demo_data(session, settings, date(2026, 8, 12))
    games = list(session.scalars(select(Game)))
    enabled = settings.model_copy(update={"official_positions_enabled": True, "opportunity_standard_threshold": 0.0})
    positions = publish_official_positions(session, enabled, games)
    assert positions
    positions[0].result = PositionResult.VOID
    positions[0].status = PositionStatus.SETTLED
    session.commit()

    bankroll = ReadService(session, settings).bankroll()

    assert any(position.result == "VOID" for position in bankroll.positions)
