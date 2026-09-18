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
    # none has its preview slot on the Sunday service day.
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


# -- the Show Prep preview pass --------------------------------------------
#: A Sunday 1:00 PM ET kickoff. Its slate is Sunday, so Show Prep freezes on
#: Saturday at 16:30 ET and the preview slot is Saturday 15:00-16:30 ET.
SUNDAY_1PM = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
SAT_FREEZE = datetime(2026, 9, 19, 20, 30, tzinfo=UTC)   # 16:30 ET
SAT_SLOT_OPENS = datetime(2026, 9, 19, 19, 0, tzinfo=UTC)  # 15:00 ET


def _priced(league, game, at: datetime) -> None:
    from app.models import Market, MarketOutcome, MarketType, OddsSnapshot, Sportsbook

    session = league["session"]
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
    book = Sportsbook(key=f"sgo:book-{game.id.hex[:6]}", name="Book")
    session.add_all([market, book])
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="a:home", label="HOME")
    session.add(outcome)
    session.flush()
    session.add(OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=at,
        available_as_of=at, american_odds=-120, decimal_odds=1.83,
        source="SPORTSGAMEODDS", source_mode="LIVE"))
    session.flush()


def test_the_preview_slot_ends_at_the_show_prep_freeze(league):
    from app.core.config import get_settings
    from app.services.sgo_nfl_pregame import preview_slot

    opens, closes = preview_slot(get_settings(), SUNDAY_1PM)
    assert closes == SAT_FREEZE, "the slot ends exactly when Show Prep freezes"
    assert opens == SAT_SLOT_OPENS


def test_a_preview_bought_in_its_slot_is_usable_at_the_freeze():
    """The whole point: a price Show Prep can actually use when it freezes."""

    from app.core.config import get_settings
    from app.services.sgo_nfl_pregame import PREVIEW_LEAD

    max_age = timedelta(hours=get_settings().show_preview_quote_max_age_hours)
    # The oldest possible preview price is one bought the moment the slot opens.
    assert PREVIEW_LEAD <= max_age
    # And it also satisfies the far stricter decision rule, which is not relaxed.
    assert PREVIEW_LEAD <= timedelta(minutes=240)


def test_a_game_is_due_only_inside_its_preview_slot(league, monkeypatch):
    from app.services.sgo_nfl_pregame import due_games

    monkeypatch.setattr(policy, "ACTIVATED", True)
    add_game(league, kickoff=SUNDAY_1PM, away="CAR", home="ATL")
    session = league["session"]
    before = SAT_SLOT_OPENS - timedelta(minutes=5)
    inside = SAT_SLOT_OPENS + timedelta(minutes=30)
    after = SAT_FREEZE + timedelta(minutes=5)
    assert due_games(session, now=before) == []
    assert [e["pass"] for e in due_games(session, now=inside)] == ["PREVIEW"]
    assert due_games(session, now=after) == []


def test_the_friday_midnight_buy_does_not_satisfy_show_prep(league, monkeypatch):
    """The 2026-09-18 00:09 purchase was 40 hours old at the freeze. It must not
    suppress the preview buy that Show Prep actually needs."""

    from app.services.sgo_nfl_pregame import due_games

    monkeypatch.setattr(policy, "ACTIVATED", True)
    game = add_game(league, kickoff=SUNDAY_1PM, away="CAR", home="ATL")
    _priced(league, game, datetime(2026, 9, 18, 4, 9, tzinfo=UTC))
    inside = SAT_SLOT_OPENS + timedelta(minutes=30)
    assert [e["pass"] for e in due_games(league["session"], now=inside)] == ["PREVIEW"]


def test_a_game_is_preview_bought_at_most_once(league, monkeypatch):
    """Once is structural: priced since the slot opened means not due again."""

    from app.services.sgo_nfl_pregame import due_games

    monkeypatch.setattr(policy, "ACTIVATED", True)
    game = add_game(league, kickoff=SUNDAY_1PM, away="CAR", home="ATL")
    _priced(league, game, SAT_SLOT_OPENS + timedelta(minutes=10))
    for minutes in (20, 45, 85):
        assert due_games(league["session"], now=SAT_SLOT_OPENS + timedelta(minutes=minutes)) == []


def test_the_midnight_snapshot_counts_the_days_preview_slots(league, monkeypatch):
    """The NYG @ LAR failure: demand must count a slot that opens LATER today,
    because the snapshot is issued at midnight and does not grow mid-day."""

    monkeypatch.setattr(policy, "ACTIVATED", True)
    for index in range(3):
        add_game(league, kickoff=SUNDAY_1PM, away=f"A{index}", home=f"H{index}")
    saturday_midnight = datetime(2026, 9, 19, 4, 5, tzinfo=UTC)  # 00:05 ET
    measured = measure(league["session"], now=saturday_midnight)
    assert measured["nfl_advance_games_needing_price"] == 3
    assert measured["reservations"]["sgo_nfl_events"] == 3


def test_a_preview_slot_on_another_day_is_not_counted_today(league, monkeypatch):
    monkeypatch.setattr(policy, "ACTIVATED", True)
    add_game(league, kickoff=SUNDAY_1PM, away="CAR", home="ATL")
    friday_midnight = datetime(2026, 9, 18, 4, 5, tzinfo=UTC)
    assert measure(league["session"], now=friday_midnight)["nfl_advance_games_needing_price"] == 0


def test_checkpoint_games_are_funded_before_preview_games(league, monkeypatch):
    from app.services.sgo_nfl_pregame import due_games

    monkeypatch.setattr(policy, "ACTIVATED", True)
    moment = SAT_SLOT_OPENS + timedelta(minutes=30)
    add_game(league, kickoff=SUNDAY_1PM, away="PRV", home="IEW")
    add_game(league, kickoff=moment + timedelta(minutes=40), away="CHK", home="PNT")
    assert [e["pass"] for e in due_games(league["session"], now=moment)] == ["CHECKPOINT", "PREVIEW"]


def test_the_checkpoint_buy_still_happens_after_a_preview_buy(league, monkeypatch):
    """Show Prep and the decision window are different purchases."""

    from app.services.sgo_nfl_pregame import due_games

    monkeypatch.setattr(policy, "ACTIVATED", True)
    game = add_game(league, kickoff=SUNDAY_1PM, away="CAR", home="ATL")
    _priced(league, game, SAT_SLOT_OPENS + timedelta(minutes=10))
    at_checkpoint = SUNDAY_1PM - timedelta(minutes=40)
    assert [e["pass"] for e in due_games(league["session"], now=at_checkpoint)] == ["CHECKPOINT"]
