"""The single retry opportunity, and everything that must not happen around it.

Model Studio rejected the first retime patch for one defect: the retry predicate
was `T-21 < to_kickoff <= T-23`, which on the new one-minute tick made BOTH T-23
and T-22 retry-eligible, and the primary-only ledger query could not see the
T-23 attempt, so the retry never closed. A focused reproduction returned a T-22
CHECKPOINT row.

These pin the corrected invariant: one primary in [T-27, T-25], at most one
retry at T-23, consumed by the attempt rather than by its outcome, and nothing
at or after T-21.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
import app.models  # noqa: F401
from app.models import (
    Game, GameStatus, GameTeam, League, ProviderRequestLedger, Season, Sport, Team,
)
from app.services import sgo_nfl_pregame as pregame

KICK = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)
TIED = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)      # a second game on the same kickoff


def settings(retime: bool = True):
    return SimpleNamespace(
        nfl_late_checkpoint_retime_enabled=retime,
        show_preview_freeze_time="16:30",
        show_preview_timezone="America/New_York",
    )


def _add_game(db, league, season, kickoff, home, away):
    game = Game(league_id=league.id, season_id=season.id, starts_at=kickoff,
                status=GameStatus.SCHEDULED)
    db.add(game); db.flush()
    for is_home, abbr in ((True, home), (False, away)):
        team = Team(league_id=league.id, name=abbr, abbreviation=abbr)
        db.add(team); db.flush()
        db.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
    db.flush()
    return game


@pytest.fixture()
def world():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        sport = Sport(code="FOOTBALL", name="NFL"); db.add(sport); db.flush()
        league = League(sport_id=sport.id, code="NFL", name="NFL"); db.add(league); db.flush()
        season = Season(league_id=league.id, name="2026",
                        starts_at=KICK - timedelta(days=30), ends_at=KICK + timedelta(days=90))
        db.add(season); db.flush()
        db.league, db.season = league, season
        db.game = _add_game(db, league, season, KICK, "HOM", "AWY")
        yield db


def due(db, *, at: datetime, retime: bool = True):
    return [r for r in pregame.due_games(db, now=at, settings=settings(retime))
            if r["pass"] == "CHECKPOINT"]


def attempt(db, *, minutes_before: float, status: str, charged=None, reservation_id=None,
            endpoint="sgo_nfl_events", kickoff: datetime = KICK):
    """One ledger row obeying ck_provider_ledger_lifecycle."""

    requested = kickoff - timedelta(minutes=minutes_before)
    row = ProviderRequestLedger(
        provider="SPORTSGAMEODDS", endpoint_key=endpoint, requested_at=requested,
        status=status, quota_cost=1, records_received=charged,
        reservation_id=reservation_id, request_fingerprint=uuid4().hex,
        completed_at=None if status == "RESERVED" else requested + timedelta(seconds=1))
    db.add(row); db.flush()
    return row


def failed_primary(db, *, kickoff: datetime = KICK):
    """A primary that failed terminally, charged nothing: the one case owed a retry."""

    reservation = attempt(db, minutes_before=26, status="RESERVED", kickoff=kickoff)
    attempt(db, minutes_before=26, status="FAILED", charged=0,
            reservation_id=reservation.id, kickoff=kickoff)


def objects_charged(db) -> int:
    rows = db.execute(select(ProviderRequestLedger)).scalars().all()
    return sum(r.records_received or 0 for r in rows if str(r.status) != "RESERVED")


# 1. The rejected defect, reproduced and now closed.
def test_the_rejected_t22_second_retry_is_gone(world):
    failed_primary(world)
    assert due(world, at=KICK - timedelta(minutes=23))     # the one retry is owed
    assert not due(world, at=KICK - timedelta(minutes=22))  # the defect: never again


# 2. Exactly one retry after a terminal zero-charge primary failure.
def test_exactly_one_retry_follows_a_terminal_zero_charge_primary(world):
    failed_primary(world)
    # The retry window is (T-22, T-23]: it opens at T-23 and is strictly closed
    # before T-22, so every instant in it is earlier than the rejected T-22.
    offered = [m for m in (24, 23.5, 23, 22.5, 22, 21.5)
               if due(world, at=KICK - timedelta(minutes=m))]
    assert offered == [23, 22.5]
    assert 22 not in offered and 21.5 not in offered

    # ONE ATTEMPT is what bounds it, not one tick: the first reservation spends
    # the opportunity, and the window is shut for the rest of its span.
    attempt(world, minutes_before=23, status="RESERVED")
    assert not [m for m in (23, 22.75, 22.5, 22) if due(world, at=KICK - timedelta(minutes=m))]


# 3. No T-22 request after ANY T-23 retry disposition.
@pytest.mark.parametrize("disposition", ["RESERVED_ONLY", "FAILED_ZERO", "FAILED_CHARGED",
                                         "SUCCEEDED"])
def test_no_t22_request_after_any_t23_retry_disposition(world, disposition):
    failed_primary(world)
    reservation = attempt(world, minutes_before=23, status="RESERVED")
    if disposition == "FAILED_ZERO":
        attempt(world, minutes_before=23, status="FAILED", charged=0,
                reservation_id=reservation.id)
    elif disposition == "FAILED_CHARGED":
        attempt(world, minutes_before=23, status="FAILED", charged=1,
                reservation_id=reservation.id)
    elif disposition == "SUCCEEDED":
        attempt(world, minutes_before=23, status="SUCCEEDED", charged=1,
                reservation_id=reservation.id)
    for minutes in (22.5, 22, 21.5):
        assert not due(world, at=KICK - timedelta(minutes=minutes)), minutes


# 4. An unknown primary charge prevents the retry.
def test_an_unknown_primary_charge_prevents_the_retry(world):
    attempt(world, minutes_before=26, status="RESERVED")   # no terminal row: unknown
    assert not due(world, at=KICK - timedelta(minutes=23))


# 5. A successful primary prevents the retry.
def test_a_successful_primary_prevents_the_retry(world):
    reservation = attempt(world, minutes_before=26, status="RESERVED")
    attempt(world, minutes_before=26, status="SUCCEEDED", charged=1,
            reservation_id=reservation.id)
    assert not due(world, at=KICK - timedelta(minutes=23))


def test_a_charged_primary_failure_prevents_the_retry(world):
    reservation = attempt(world, minutes_before=26, status="RESERVED")
    attempt(world, minutes_before=26, status="FAILED", charged=1,
            reservation_id=reservation.id)
    assert not due(world, at=KICK - timedelta(minutes=23))


# 6. Repeated one-minute ticks create no additional requests.
def test_repeated_one_minute_ticks_are_inert_once_resolved(world):
    """Every minute from T-40 to T-1, with the ledger updated as the lane would.

    The lane is offered the primary once and the retry once. Every other tick,
    including all the new one-minute ticks the retime introduces, is inert.
    """

    offered: list[float] = []
    for whole in range(40, 0, -1):
        for minutes in (whole, whole - 0.5):
            if minutes <= 0:
                continue
            if due(world, at=KICK - timedelta(minutes=minutes)):
                offered.append(minutes)
                # the lane reserves, then the request terminates as a zero-charge
                # failure -- the only path that keeps a retry owed
                reservation = attempt(world, minutes_before=minutes, status="RESERVED")
                attempt(world, minutes_before=minutes, status="FAILED", charged=0,
                        reservation_id=reservation.id)
    assert offered == [27, 23]          # one primary, one retry, nothing else
    assert objects_charged(world) == 0   # both failures were charged nothing


# 7. Nothing at or after T-21.
@pytest.mark.parametrize("minutes", [21, 20.5, 20, 16, 15, 10, 1])
def test_no_request_at_or_after_the_t21_hard_stop(world, minutes):
    failed_primary(world)
    assert not pregame.due_games(world, now=KICK - timedelta(minutes=minutes),
                                 settings=settings())


# 8. Charged objects reconcile exactly.
def test_charged_objects_reconcile_exactly(world):
    """A successful primary charges exactly one object, and nothing follows it."""

    assert due(world, at=KICK - timedelta(minutes=26))
    reservation = attempt(world, minutes_before=26, status="RESERVED")
    attempt(world, minutes_before=26, status="SUCCEEDED", charged=1,
            reservation_id=reservation.id)
    assert objects_charged(world) == 1
    for minutes in (25, 23, 22):
        assert not due(world, at=KICK - timedelta(minutes=minutes)), minutes
    assert objects_charged(world) == 1


def test_a_failed_primary_and_its_retry_charge_exactly_one(world):
    failed_primary(world)
    assert objects_charged(world) == 0
    assert due(world, at=KICK - timedelta(minutes=23))
    reservation = attempt(world, minutes_before=23, status="RESERVED")
    attempt(world, minutes_before=23, status="SUCCEEDED", charged=1,
            reservation_id=reservation.id)
    assert objects_charged(world) == 1
    assert not due(world, at=KICK - timedelta(minutes=22))


# 9. Week 3 allocation: tied kickoffs share no extra retry cycle.
def test_tied_kickoff_games_get_one_primary_and_one_retry_each(world):
    """Week 3 puts thirteen games on three kickoffs. Each is accounted separately.

    The danger with a tied kickoff is a per-kickoff rather than per-game rule:
    one game's failed primary must not open a retry for its neighbours, and one
    game's retry must not consume theirs.
    """

    second = _add_game(world, world.league, world.season, TIED, "HM2", "AW2")

    # ONE request serves the whole cluster -- the provider bills the event
    # object, not the call -- so both tied games are due in the SAME primary.
    primary = due(world, at=KICK - timedelta(minutes=26))
    assert {r["game_id"] for r in primary} == {str(world.game.id), str(second.id)}

    # That one request failing with zero charge owes the cluster ONE retry
    # cycle, not one per game.
    failed_primary(world)
    retry = due(world, at=KICK - timedelta(minutes=23))
    assert {r["game_id"] for r in retry} == {str(world.game.id), str(second.id)}

    # And attempting it consumes the cycle for the cluster, not per game.
    attempt(world, minutes_before=23, status="RESERVED")
    assert not due(world, at=KICK - timedelta(minutes=22.5))
    assert not due(world, at=KICK - timedelta(minutes=22))


def test_the_retime_adds_no_object_per_game(world):
    """One buy per game per regime: the retime moves the object, never adds one."""

    retimed = [m for m in range(90, 0, -1) if due(world, at=KICK - timedelta(minutes=m))]
    legacy = [m for m in range(90, 0, -1)
              if due(world, at=KICK - timedelta(minutes=m), retime=False)]
    assert retimed == [27, 26, 25]          # one window
    assert legacy[0] == 75 and legacy[-1] == 15
    # Neither regime offers a second window once a price lands inside the first.
    assert pregame.retimed_checkpoint_window(KICK) == (
        KICK - timedelta(minutes=27), KICK - timedelta(minutes=25))
    assert pregame.retimed_retry_window(KICK) == (
        KICK - timedelta(minutes=23), KICK - timedelta(minutes=22))


# 10. Nothing else moves.
def test_other_leagues_decisions_and_the_exporter_are_untouched(world):
    """NFL-only, sgo_nfl_events-only, and no decision or staking constant moves."""

    # The decision cutoff and the unretimed window constants are unchanged.
    assert pregame.WINDOW_CLOSES_BEFORE_KICKOFF == timedelta(minutes=15)
    assert pregame.WINDOW_OPENS_BEFORE_KICKOFF == timedelta(minutes=75)
    assert pregame.SOURCE == "SPORTSGAMEODDS"

    # An MLB terminal row does not settle an NFL reservation, so it cannot
    # manufacture a retry on the NFL lane.
    reservation = attempt(world, minutes_before=26, status="RESERVED")
    attempt(world, minutes_before=26, status="FAILED", charged=0,
            reservation_id=reservation.id, endpoint="sgo_mlb_events")
    assert not due(world, at=KICK - timedelta(minutes=23))

    # The early-line exporter's own geometry is a separate frozen contract and
    # is not read or written here.
    from app.research_exports import nfl_early_line as el
    assert (el.LATE_PRIMARY_EARLIEST, el.LATE_PRIMARY_LATEST) == (1620, 1500)
    assert el.LATE_HARD_STOP == 1260
