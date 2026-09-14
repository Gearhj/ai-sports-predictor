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
    affordability,
    collect_nfl_live,
    in_watch_window,
    objects_spent_today,
    tracked_game_ids,
)


@pytest.fixture
def live_demand(monkeypatch):
    """Give the in-game lane a reason to exist.

    Capacity follows demand now: the live lane reserves nothing unless a game
    is actually being FOLLOWED -- a recorded position or a parlay leg. That is
    the whole point of the change, so a test about this lane's share has to
    supply the demand rather than assume a standing percentage of the day.
    """

    from app.services import allocation_demand

    original = allocation_demand.measure

    def measured(session, *, now=None):
        result = original(session, now=now)
        result["nfl_games_followed"] = 1
        result["reservations"]["sgo_nfl_live"] = allocation_demand.NFL_LIVE_PER_GAME
        return result

    monkeypatch.setattr(allocation_demand, "measure", measured)
    return measured


def seed_balance(session: Session, *, remaining: int, now: datetime | None = None):
    """Record a provider usage reading, so the real plan has a real balance.

    Seeding the reading rather than stubbing the planner means these tests
    exercise the allocation AISP actually runs, including its apportionment and
    its in-flight accounting.
    """

    from app.services.provider_allocation import (
        FALLBACK_MONTHLY_CAP, PROVIDER, USAGE_ENDPOINT_KEY)
    from app.models import ProviderRequestLedger

    moment = now or datetime.now(UTC)
    used = max(0, FALLBACK_MONTHLY_CAP - remaining)
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=USAGE_ENDPOINT_KEY, requested_at=moment,
        status="RESERVED", request_fingerprint="u" * 64, quota_cost=0,
    )
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=USAGE_ENDPOINT_KEY, requested_at=moment,
        status="SUCCEEDED", request_fingerprint="u" * 64, completed_at=moment,
        reservation_id=reservation.id, records_received=0, quota_cost=0,
        response_metadata={"usage": {
            "monthly_cap": FALLBACK_MONTHLY_CAP, "used_this_month": used}},
    ))
    session.commit()


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


def test_a_balance_below_the_reserve_funds_no_lane_and_says_when_it_will(session):
    from app.services.provider_allocation import reserve_for
    from app.services.service_day import service_date

    # Under the reserve IN FORCE TODAY. The reserve is a schedule now, so
    # seeding against the old 400 constant would leave the balance comfortably
    # above the real reserve and fund every lane -- the test would pass or fail
    # on the calendar rather than on the behaviour.
    reserve = reserve_for(service_date(datetime.now(UTC)))
    seed_balance(session, remaining=reserve - 10)
    money = affordability(session, wanted=2)
    assert money["allowed"] == 0
    assert money["reason"] == "BUDGET_PAUSED_UNTIL_REPLENISHMENT"
    assert "replenishes on" in money["detail"]


def test_the_lane_gets_its_own_share_rather_than_defending_a_floor(session, live_demand):
    from app.services.provider_allocation import OBJECT_RESERVE, build_plan

    seed_balance(session, remaining=OBJECT_RESERVE + 600)
    plan = build_plan(session)
    # Every funded lane has a share, and the shares add up to the daily total.
    assert sum(a.daily_allowance for a in plan.allocations.values()) == plan.daily_total
    money = affordability(session, wanted=1)
    assert money["allowed"] == 1
    assert money["daily_cap"] == plan.allocations["sgo_nfl_live"].daily_allowance


def test_the_lane_is_capped_by_its_own_daily_share_not_by_the_whole_balance(session, live_demand):
    from app.models import ProviderRequestLedger
    from app.services.provider_allocation import (
        OBJECT_RESERVE, PROVIDER, build_plan)

    seed_balance(session, remaining=OBJECT_RESERVE + 600)
    allowance = build_plan(session).allocations["sgo_nfl_live"].daily_allowance
    assert allowance >= 2

    # Spend ONE object, leaving the balance healthy and the lane far from empty.
    #
    # Deliberately one, not `allowance - 1`. The daily allowance is derived from
    # the REMAINING balance divided by the days left, so a large spend inside a
    # day lowers that same day's allowance underneath itself -- spending seven
    # of eight leaves zero, not one, because the allowance has meanwhile fallen
    # to seven. That is the documented behaviour of a budget with a deadline,
    # not a defect, and it is pinned on its own below. What THIS test is about
    # is the lane's share being the binding constraint rather than the balance.
    now = datetime.now(UTC)
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key="sgo_nfl_live", requested_at=now,
        status="RESERVED", request_fingerprint="s" * 64, quota_cost=1)
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=PROVIDER, endpoint_key="sgo_nfl_live", requested_at=now,
        status="SUCCEEDED", request_fingerprint="s" * 64, completed_at=now,
        reservation_id=reservation.id, records_received=1))
    session.commit()

    after = build_plan(session).allocations["sgo_nfl_live"]
    money = affordability(session, wanted=1000)
    # Capped by the lane's own day, not by the 600 spendable objects sitting in
    # the balance -- which is the whole point of having lanes at all.
    assert money["allowed"] == after.room
    assert money["allowed"] < 600
    assert "objects a day" in money["detail"]


def test_todays_allowance_falls_as_today_is_spent(session, live_demand):
    """A budget with a deadline recomputes from what is left, including today.

    This is a real and slightly surprising consequence: a lane can never spend
    its stated allowance down to exactly zero remaining, because each object it
    spends also lowers the allowance it is measured against. Pinned here so the
    next reader meets it as a documented property rather than as an off-by-one.
    """

    from app.models import ProviderRequestLedger
    from app.services.provider_allocation import OBJECT_RESERVE, PROVIDER, build_plan

    seed_balance(session, remaining=OBJECT_RESERVE + 600)
    before = build_plan(session).allocations["sgo_nfl_live"].daily_allowance

    now = datetime.now(UTC)
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key="sgo_nfl_live", requested_at=now,
        status="RESERVED", request_fingerprint="t" * 64, quota_cost=before - 1)
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=PROVIDER, endpoint_key="sgo_nfl_live", requested_at=now,
        status="SUCCEEDED", request_fingerprint="t" * 64, completed_at=now,
        reservation_id=reservation.id, records_received=before - 1))
    session.commit()

    after = build_plan(session).allocations["sgo_nfl_live"]
    # The allowance moved because the balance did. It never moves UP on a spend.
    assert after.daily_allowance <= before
    assert after.spent_today == before - 1
    # And the lane is never pushed negative, whatever the arithmetic does.
    assert after.room >= 0


def test_a_reservation_in_flight_is_visible_to_a_concurrent_job(session):
    from app.models import ProviderRequestLedger
    from app.services.provider_allocation import (
        OBJECT_RESERVE, PROVIDER, build_plan)

    seed_balance(session, remaining=OBJECT_RESERVE + 600)
    allowance = build_plan(session).allocations["sgo_nfl_live"].daily_allowance

    # A reservation with no terminal row yet: another job is mid-request.
    session.add(ProviderRequestLedger(
        provider=PROVIDER, endpoint_key="sgo_nfl_live",
        requested_at=datetime.now(UTC), status="RESERVED",
        request_fingerprint="f" * 64, quota_cost=allowance))
    session.commit()

    # The second job must see the whole lane as committed, not free.
    money = affordability(session, wanted=allowance)
    assert money["allowed"] == 0


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
        session, nfl_game, monkeypatch, live_demand):
    during = nfl_game.starts_at + timedelta(hours=1)

    from app.services.provider_allocation import OBJECT_RESERVE

    seed_balance(session, remaining=OBJECT_RESERVE + 900, now=during)
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
        session, nfl_game, monkeypatch, live_demand):
    during = nfl_game.starts_at + timedelta(hours=1)

    from app.services.provider_allocation import OBJECT_RESERVE

    seed_balance(session, remaining=OBJECT_RESERVE + 900, now=during)
    monkeypatch.setattr(
        "app.services.sgo_collection.eligible", lambda *a, **k: (True, "OK"))

    wrong = in_progress_event()
    wrong["teams"]["away"]["names"]["short"] = "BUF"

    result = collect_nfl_live(
        session, object(), now=during, fetch=lambda *_: {"data": [wrong]})
    assert result["games_matched"] == 0
    assert result["unmatched_events"] == ["BUF@DEN 2026-09-15"]
    assert session.scalars(select(GameScoreboardObservation)).all() == []
