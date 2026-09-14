"""In-game NFL tracking: what it costs, what it refuses, and what it records.

The expensive failure here is not a wrong score. It is a collector that quietly
outbids something else for a scarce allowance, so most of these tests are about
money: that a tracked game outside its window costs nothing, that a finished
game is never paid for twice, that the daily cap and the MLB pregame floor both
bite, and that every refusal reports the arithmetic instead of going quiet.

The in-progress fixture is the real event shape -- verified against a live
response -- with the parts that only a game in play has: `live` true, `ended`
false, a `currentPeriodID`, and a `results` map carrying only the quarters that
have actually been played.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scoreboard import GameScoreboardObservation
from app.scoreboard.nfl import classify, periods_from, scoreboard_from_event
from app.scoreboard.nfl_live import (
    MAX_OBJECTS_PER_DAY,
    MLB_PREGAME_FLOOR,
    affordability,
    collect_nfl_live,
    in_watch_window,
    objects_spent_today,
    tracked_game_ids,
)


def budget_with(spendable: int):
    """A real ObjectBudget with the spendable figure the test needs."""

    from app.services.sgo_collection import FREE_TIER_OBJECTS, OBJECT_RESERVE, ObjectBudget

    return ObjectBudget(
        window_days=30, requests=0,
        objects_used=max(0, FREE_TIER_OBJECTS - OBJECT_RESERVE - spendable),
        allowance=FREE_TIER_OBJECTS, reserve=OBJECT_RESERVE,
    )


def in_progress_event(**over):
    """A real SGO NFL event, mid-third-quarter. No clock field: there is none."""

    event = {
        "eventID": "live-1",
        "status": {
            "started": True, "live": True, "ended": False, "completed": False,
            "cancelled": False, "delayed": False, "inBreak": False,
            "finalized": False,
            "currentPeriodID": "3q", "previousPeriodID": "2q",
            "displayShort": "Q3", "displayLong": "3rd Quarter",
            "startsAt": "2026-09-15T00:15:00.000Z",
            # Only the quarters actually reached.
            "periods": {"started": ["1q", "2q", "3q", "1h"],
                        "ended": ["1q", "2q", "1h"]},
        },
        "teams": {
            "home": {"names": {"short": "DEN"}, "score": 13},
            "away": {"names": {"short": "KC"}, "score": 17},
        },
        "results": {
            "1q": {"home": {"points": 3}, "away": {"points": 7}},
            "2q": {"home": {"points": 10}, "away": {"points": 3}},
            "3q": {"home": {"points": 0}, "away": {"points": 7}},
            "1h": {"home": {"points": 13}, "away": {"points": 10}},
        },
    }
    event.update(over)
    return event


@pytest.fixture
def nfl_game(session: Session):
    """One NFL game, kicking off now, with both clubs attached."""

    from app.models import Game, GameTeam, League, Season, Sport, Team, Venue

    sport = Sport(name="Football", code="FOOTBALL")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, name="NFL", code="NFL")
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id, name="2026",
        starts_at=datetime(2026, 9, 1, tzinfo=UTC),
        ends_at=datetime(2027, 2, 1, tzinfo=UTC),
    )
    venue = Venue(name="Test Field")
    away = Team(league_id=league.id, name="Kansas City Chiefs", abbreviation="KC")
    home = Team(league_id=league.id, name="Denver Broncos", abbreviation="DEN")
    session.add_all([season, venue, away, home])
    session.flush()
    game = Game(
        league_id=league.id, season_id=season.id, venue_id=venue.id,
        starts_at=datetime(2026, 9, 15, 0, 15, tzinfo=UTC), status="LIVE",
    )
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    session.commit()
    return game


# -- what an in-progress event actually says ------------------------------

def test_a_game_in_play_is_in_progress_not_final():
    status, detail = classify(in_progress_event()["status"])
    assert status == "IN_PROGRESS"
    assert detail == "3rd Quarter"


def test_only_the_quarters_played_are_reported_and_the_set_is_complete():
    periods, complete = periods_from(in_progress_event())
    assert [p["label"] for p in periods] == ["1Q", "2Q", "3Q"]
    # Every period the source says started is present, so nothing is missing --
    # the fourth quarter has not happened, which is not the same as unreported.
    assert complete is True


def test_a_partial_game_is_not_reconciled_against_a_final_that_does_not_exist():
    state = scoreboard_from_event(in_progress_event())
    assert state["status"] == "IN_PROGRESS"
    assert state["periods_reconcile"] is None
    assert (state["away_score"], state["home_score"]) == (17, 13)
    assert state["period_label"] == "3Q"


def test_no_clock_is_reported_for_a_game_in_play():
    state = scoreboard_from_event(in_progress_event())
    assert state["clock_seconds_remaining"] is None
    # And no score timestamp, so freshness can only be the age of the check.
    assert state["source_observed_at"] is None


def test_a_quarter_the_source_has_not_reported_yet_makes_the_set_incomplete():
    event = in_progress_event()
    del event["results"]["3q"]          # started, but no points published
    periods, complete = periods_from(event)
    assert [p["label"] for p in periods] == ["1Q", "2Q"]
    assert complete is False


# -- the watch window -----------------------------------------------------

def test_a_game_outside_its_window_is_not_tracked(session, nfl_game):
    long_after = nfl_game.starts_at + timedelta(hours=9)
    assert tracked_game_ids(session, now=long_after) == []


def test_a_game_inside_its_window_is_tracked_and_live(session, nfl_game):
    during = nfl_game.starts_at + timedelta(hours=1)
    tracked = tracked_game_ids(session, now=during)
    assert tracked == [nfl_game.id]
    assert in_watch_window(session, tracked, now=during) == [nfl_game.id]


def test_a_game_already_recorded_final_is_never_paid_for_again(session, nfl_game):
    during = nfl_game.starts_at + timedelta(hours=1)
    session.add(GameScoreboardObservation(
        game_id=nfl_game.id, league_code="NFL", source="SPORTSGAMEODDS",
        status="FINAL", away_score=24, home_score=20,
        periods_complete=True, retrieved_at=during, checked_at=during,
        payload_checksum="f" * 64,
    ))
    session.commit()
    tracked = tracked_game_ids(session, now=during)
    assert tracked == [nfl_game.id]
    # Tracked, but not worth an object: the final cannot change.
    assert in_watch_window(session, tracked, now=during) == []


# -- money ----------------------------------------------------------------

def test_nothing_is_spent_when_no_tracked_game_is_in_its_window(session, nfl_game):
    result = collect_nfl_live(
        session, object(), now=nfl_game.starts_at + timedelta(hours=9),
        fetch=lambda *_: pytest.fail("must not call the provider"))
    assert result["objects"] == 0
    assert result["requests"] == 0
    assert result["reason"] == "NO_TRACKED_NFL_GAME_IS_IN_ITS_WATCH_WINDOW"


def test_the_mlb_pregame_floor_refuses_and_shows_its_arithmetic(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.sgo_collection.object_budget",
        lambda *a, **k: budget_with(MLB_PREGAME_FLOOR - 40))
    money = affordability(session, wanted=2)
    assert money["allowed"] == 0
    assert money["floor_room"] == 0
    assert str(MLB_PREGAME_FLOOR) in money["detail"]
    assert "held for MLB pregame odds" in money["detail"]


def test_the_daily_cap_limits_the_spend_even_when_the_budget_is_healthy(
        session, monkeypatch):
    monkeypatch.setattr(
        "app.services.sgo_collection.object_budget",
        lambda *a, **k: budget_with(MLB_PREGAME_FLOOR + 1000))
    monkeypatch.setattr(
        "app.scoreboard.nfl_live.objects_spent_today",
        lambda *a, **k: MAX_OBJECTS_PER_DAY - 1)
    money = affordability(session, wanted=4)
    assert money["allowed"] == 1
    assert "of its" in money["detail"]


def test_nfl_live_spend_is_counted_separately_from_mlb_pregame(session):
    from app.models import ProviderRequestLedger

    now = datetime.now(UTC)
    # The ledger's lifecycle constraint requires a terminal row to point at its
    # own reservation, so each spend is written the way the collector writes it.
    for key, fingerprint, objects in (
            ("sgo_mlb_events", "a" * 64, 120), ("sgo_nfl_live", "b" * 64, 3)):
        reservation = ProviderRequestLedger(
            provider="SPORTSGAMEODDS", endpoint_key=key, requested_at=now,
            status="RESERVED", request_fingerprint=fingerprint,
        )
        session.add(reservation)
        session.flush()
        session.add(ProviderRequestLedger(
            provider="SPORTSGAMEODDS", endpoint_key=key, requested_at=now,
            status="SUCCEEDED", request_fingerprint=fingerprint,
            records_received=objects, completed_at=now,
            reservation_id=reservation.id,
        ))
    session.commit()
    # The MLB spend must not count against the NFL daily cap, or the cap would
    # be unenforceable and the attribution wrong.
    assert objects_spent_today(session, now=now) == 3


# -- end to end, without spending anything --------------------------------

def test_a_tracked_live_game_is_recorded_from_the_provider_payload(
        session, nfl_game, monkeypatch):
    during = nfl_game.starts_at + timedelta(hours=1)

    monkeypatch.setattr(
        "app.services.sgo_collection.object_budget",
        lambda *a, **k: budget_with(MLB_PREGAME_FLOOR + 1000))
    monkeypatch.setattr(
        "app.services.sgo_collection.eligible", lambda *a, **k: (True, "OK"))

    result = collect_nfl_live(
        session, object(), now=during,
        fetch=lambda *_: {"data": [in_progress_event()]})

    assert result["requests"] == 1
    assert result["objects"] == 1
    assert result["games_matched"] == 1
    assert result["in_progress"] == 1
    assert result["unmatched_events"] == []

    row = session.scalars(
        select(GameScoreboardObservation).where(
            GameScoreboardObservation.game_id == nfl_game.id)).one()
    assert row.status == "IN_PROGRESS"
    assert (row.away_score, row.home_score) == (17, 13)
    assert row.period_label == "3Q"
    assert row.clock_seconds_remaining is None
    assert row.source_observed_at is None
    assert [p["label"] for p in row.periods] == ["1Q", "2Q", "3Q"]


def test_an_event_for_a_different_game_is_skipped_not_attached(
        session, nfl_game, monkeypatch):
    during = nfl_game.starts_at + timedelta(hours=1)

    monkeypatch.setattr(
        "app.services.sgo_collection.object_budget",
        lambda *a, **k: budget_with(MLB_PREGAME_FLOOR + 1000))
    monkeypatch.setattr(
        "app.services.sgo_collection.eligible", lambda *a, **k: (True, "OK"))

    wrong = in_progress_event()
    wrong["teams"]["away"]["names"]["short"] = "BUF"

    result = collect_nfl_live(
        session, object(), now=during, fetch=lambda *_: {"data": [wrong]})
    assert result["games_matched"] == 0
    assert result["unmatched_events"] == ["BUF@DEN 2026-09-15"]
    assert session.scalars(select(GameScoreboardObservation)).all() == []
