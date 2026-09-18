"""NFL pregame demand is the size of the slate, not a constant.

The failure this pins: `min(len(pregame_games), NFL_PREGAME_PER_GAME)` reserved
one object whether one game or fifteen were on the service day, so on the first
governed Week 2 Sunday fourteen of fifteen games were unfundable and the lane
reported ALLOCATION_LANE_DAILY_ALLOWANCE_SPENT for the rest of the day.

Demand must scale with the slate, stay zero when there is no NFL, and never
reserve for a game whose hold window has already closed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import Game, GameStatus, GameTeam, League, Season, Sport, Team
from app.services import nfl_paper_policy as policy
from app.services.allocation_demand import NFL_PREGAME_HOLD, measure

#: A Sunday inside the governed Week 2 slate, in the middle of the service day.
NOON_ET = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)


@pytest.fixture
def league(session: Session):
    sport = Sport(code="FOOTBALL", name="Football")
    session.add(sport)
    session.flush()
    entry = League(sport_id=sport.id, code="NFL", name="NFL")
    session.add(entry)
    session.flush()
    season = Season(league_id=entry.id, name="2026",
                    starts_at=NOON_ET - timedelta(days=30), ends_at=NOON_ET + timedelta(days=120))
    session.add(season)
    session.flush()
    return {"session": session, "league": entry, "season": season}


def add_game(ctx, *, kickoff: datetime, away: str, home: str) -> Game:
    session = ctx["session"]
    teams = []
    for abbreviation in (home, away):
        team = Team(league_id=ctx["league"].id, name=f"{abbreviation} team",
                    abbreviation=abbreviation)
        session.add(team)
        teams.append(team)
    session.flush()
    game = Game(league_id=ctx["league"].id, season_id=ctx["season"].id,
                starts_at=kickoff, status=GameStatus.SCHEDULED)
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=teams[0].id, is_home=True),
        GameTeam(game_id=game.id, team_id=teams[1].id, is_home=False),
    ])
    session.flush()
    return game


def reserved(session: Session, now: datetime) -> int:
    return measure(session, now=now)["reservations"]["sgo_nfl_events"]


def test_a_fifteen_game_sunday_reserves_fifteen_objects(league, monkeypatch):
    monkeypatch.setattr(policy, "ACTIVATED", True)
    kickoffs = ([datetime(2026, 9, 20, 17, 0, tzinfo=UTC)] * 8
                + [datetime(2026, 9, 20, 20, 5, tzinfo=UTC)] * 2
                + [datetime(2026, 9, 20, 20, 25, tzinfo=UTC)] * 3
                + [datetime(2026, 9, 21, 0, 20, tzinfo=UTC)])
    for index, kickoff in enumerate(kickoffs):
        add_game(league, kickoff=kickoff, away=f"A{index:02d}", home=f"H{index:02d}")

    # One CHECKPOINT object for each of the fourteen games on this service day.
    # None of them is advance-bought: their own checkpoints are hours away, and
    # buying twice for one game is what ADVANCE_MIN_LEAD exists to prevent.
    assert reserved(league["session"], NOON_ET) == 14, (
        "one object for each game on the service day, not one for the slate")


def test_a_single_game_day_still_reserves_exactly_one(league, monkeypatch):
    monkeypatch.setattr(policy, "ACTIVATED", True)
    add_game(league, kickoff=datetime(2026, 9, 20, 17, 0, tzinfo=UTC), away="AAA", home="BBB")
    assert reserved(league["session"], NOON_ET) == 1


def test_a_day_with_no_nfl_reserves_nothing(league, monkeypatch):
    monkeypatch.setattr(policy, "ACTIVATED", True)
    assert reserved(league["session"], NOON_ET) == 0


def test_a_game_past_its_hold_window_is_not_reserved(league, monkeypatch):
    """A price for a game that kicked off an hour ago buys nothing."""

    monkeypatch.setattr(policy, "ACTIVATED", True)
    add_game(league, kickoff=NOON_ET - NFL_PREGAME_HOLD - timedelta(minutes=30),
             away="OLD", home="GNE")
    add_game(league, kickoff=NOON_ET + timedelta(hours=2), away="NEW", home="UPC")
    assert reserved(league["session"], NOON_ET) == 1


def test_nothing_is_reserved_while_the_paper_policy_is_inactive(league, monkeypatch):
    monkeypatch.setattr(policy, "ACTIVATED", False)
    for index in range(4):
        add_game(league, kickoff=datetime(2026, 9, 20, 17, 0, tzinfo=UTC),
                 away=f"X{index}", home=f"Y{index}")
    assert reserved(league["session"], NOON_ET) == 0


def test_the_receipt_says_how_many_games_the_objects_are_for(league, monkeypatch):
    monkeypatch.setattr(policy, "ACTIVATED", True)
    for index in range(3):
        add_game(league, kickoff=datetime(2026, 9, 20, 17, 0, tzinfo=UTC),
                 away=f"P{index}", home=f"Q{index}")
    why = measure(league["session"], now=NOON_ET)["why"]["sgo_nfl_events"]
    assert "3 checkpoint object(s)" in why and "3 authorised NFL paper game(s)" in why


# -- the advance pass -------------------------------------------------------
def test_a_future_game_with_no_recent_price_is_due_in_advance(league, monkeypatch):
    """The state the Week 2 slate was actually in: one quote from 29 August."""

    from app.services.sgo_nfl_pregame import due_games

    monkeypatch.setattr(policy, "ACTIVATED", True)
    add_game(league, kickoff=NOON_ET + timedelta(hours=60), away="SUN", home="DAY")
    due = due_games(league["session"], now=NOON_ET)
    assert [entry["pass"] for entry in due] == ["ADVANCE"]


def test_a_game_beyond_the_advance_horizon_is_not_due(league, monkeypatch):
    from app.services.sgo_nfl_pregame import ADVANCE_HORIZON, due_games

    monkeypatch.setattr(policy, "ACTIVATED", True)
    add_game(league, kickoff=NOON_ET + ADVANCE_HORIZON + timedelta(hours=2),
             away="FAR", home="OFF")
    assert due_games(league["session"], now=NOON_ET) == []


def test_checkpoint_games_are_funded_before_advance_games(league, monkeypatch):
    """A partial grant must pay for the games that have a decision to make."""

    from app.services.sgo_nfl_pregame import due_games

    monkeypatch.setattr(policy, "ACTIVATED", True)
    add_game(league, kickoff=NOON_ET + timedelta(hours=60), away="ADV", home="ANC")
    add_game(league, kickoff=NOON_ET + timedelta(minutes=40), away="CHK", home="PNT")
    due = due_games(league["session"], now=NOON_ET)
    assert [entry["pass"] for entry in due] == ["CHECKPOINT", "ADVANCE"]


def test_a_recently_priced_game_is_not_bought_twice(league, monkeypatch):
    """Self-limiting: once the slate is covered the pass costs nothing."""

    from app.models import Market, MarketOutcome, MarketType, OddsSnapshot, Sportsbook
    from app.services.sgo_nfl_pregame import ADVANCE_REFRESH, due_games

    monkeypatch.setattr(policy, "ACTIVATED", True)
    session = league["session"]
    game = add_game(league, kickoff=NOON_ET + timedelta(hours=60), away="HAD", home="ONE")
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
    book = Sportsbook(key="sgo:draftkings", name="DraftKings")
    session.add_all([market, book])
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="a:home", label="ONE")
    session.add(outcome)
    session.flush()

    fresh = NOON_ET - ADVANCE_REFRESH + timedelta(hours=1)
    session.add(OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=fresh,
        available_as_of=fresh, american_odds=-120, decimal_odds=1.83,
        source="SPORTSGAMEODDS", source_mode="LIVE"))
    session.flush()
    assert due_games(session, now=NOON_ET) == [], "a recent price is not re-bought"

    # Once it ages past the refresh interval it becomes due again.
    stale = NOON_ET - ADVANCE_REFRESH - timedelta(hours=1)
    session.query(OddsSnapshot).update({"observed_at": stale, "available_as_of": stale})
    session.flush()
    assert [entry["pass"] for entry in due_games(session, now=NOON_ET)] == ["ADVANCE"]
