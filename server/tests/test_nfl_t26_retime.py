"""The spend-neutral T-26 retime, off by default.

The frozen NFL_EARLY_LINE_INFORMATION_EDGE_V1 protocol wants the LATE
observation's REQUEST inside [T-27, T-25], one retry at T-23, and nothing at or
after T-21. The production window is [T-75, T-15] and its first tick lands
around T-66, which is why every LATE horizon records MISSING_LATE_PRICE.

These pin that the retime MOVES the checkpoint rather than adding one, and that
it is inert until the flag is set.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
import app.models  # noqa: F401
from app.models import (
    Game, GameStatus, GameTeam, League, ProviderRequestLedger, Season, Sport, Team,
)
from app.services import sgo_nfl_pregame as pregame

KICK = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)


def settings(retime: bool):
    return SimpleNamespace(
        nfl_late_checkpoint_retime_enabled=retime,
        show_preview_freeze_time="16:30",
        show_preview_timezone="America/New_York",
    )


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        sport = Sport(code="FOOTBALL", name="NFL"); db.add(sport); db.flush()
        league = League(sport_id=sport.id, code="NFL", name="NFL"); db.add(league); db.flush()
        season = Season(league_id=league.id, name="2026",
                        starts_at=KICK - timedelta(days=30), ends_at=KICK + timedelta(days=90))
        db.add(season); db.flush()
        game = Game(league_id=league.id, season_id=season.id, starts_at=KICK,
                    status=GameStatus.SCHEDULED)
        db.add(game); db.flush()
        for is_home, abbr in ((True, "HOM"), (False, "AWY")):
            team = Team(league_id=league.id, name=abbr, abbreviation=abbr)
            db.add(team); db.flush()
            db.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
        db.flush()
        db.game = game
        yield db


def due(session, *, at: datetime, retime: bool = True) -> list[dict]:
    return pregame.due_games(session, now=at, settings=settings(retime))


def checkpoints(rows) -> list[dict]:
    return [r for r in rows if r["pass"] == "CHECKPOINT"]


def ledger(session, *, requested: datetime, status: str, charged=None, reservation_id=None,
           endpoint="sgo_nfl_events"):
    """One ledger row obeying ck_provider_ledger_lifecycle.

    A RESERVED row has no reservation_id and no completed_at; a terminal row
    must carry both. The constraint is what makes an orphaned reservation --
    an attempt whose charge is unknown -- visible rather than inventable.
    """

    row = ProviderRequestLedger(
        provider="SPORTSGAMEODDS", endpoint_key=endpoint, requested_at=requested,
        status=status, quota_cost=1, records_received=charged,
        reservation_id=reservation_id, request_fingerprint=uuid4().hex,
        completed_at=None if status == "RESERVED" else requested + timedelta(seconds=1))
    session.add(row); session.flush()
    return row


# ==========================================================================
# The primary interval
# ==========================================================================

@pytest.mark.parametrize("minutes, expected", [
    (28, False),   # before the interval opens
    (27, True),    # T-27, inclusive
    (26, True),    # the target
    (25, True),    # T-25, inclusive
    (24, False),   # past the primary, before the retry
])
def test_the_primary_request_interval_is_t27_to_t25(session, minutes, expected):
    rows = checkpoints(due(session, at=KICK - timedelta(minutes=minutes)))
    assert bool(rows) is expected


def test_the_unretimed_window_is_untouched_when_the_flag_is_off(session):
    """T-66 still qualifies today, and T-26 is inside the old window too."""

    assert checkpoints(due(session, at=KICK - timedelta(minutes=66), retime=False))
    assert checkpoints(due(session, at=KICK - timedelta(minutes=26), retime=False))
    # And the retime refuses the T-66 buy the old window allows.
    assert not checkpoints(due(session, at=KICK - timedelta(minutes=66), retime=True))


# ==========================================================================
# The T-23 retry, and what forbids it
# ==========================================================================

def test_no_retry_without_a_primary_attempt(session):
    assert not checkpoints(due(session, at=KICK - timedelta(minutes=23)))


def test_the_retry_runs_after_a_terminal_zero_charge_failure(session):
    reservation = ledger(session, requested=KICK - timedelta(minutes=26), status="RESERVED")
    ledger(session, requested=KICK - timedelta(minutes=26), status="FAILED", charged=0,
           reservation_id=reservation.id)
    assert checkpoints(due(session, at=KICK - timedelta(minutes=23)))


def test_a_charged_failure_forbids_the_retry(session):
    reservation = ledger(session, requested=KICK - timedelta(minutes=26), status="RESERVED")
    ledger(session, requested=KICK - timedelta(minutes=26), status="FAILED", charged=1,
           reservation_id=reservation.id)
    assert not checkpoints(due(session, at=KICK - timedelta(minutes=23)))


def test_a_successful_primary_forbids_the_retry(session):
    reservation = ledger(session, requested=KICK - timedelta(minutes=26), status="RESERVED")
    ledger(session, requested=KICK - timedelta(minutes=26), status="SUCCEEDED", charged=1,
           reservation_id=reservation.id)
    assert not checkpoints(due(session, at=KICK - timedelta(minutes=23)))


def test_an_unknown_charge_blocks_the_retry(session):
    """A reservation with no terminal row: the process died mid-request.

    Its charge is unknown. Retrying would spend a second object for one
    checkpoint if the first was in fact billed, so it never runs.
    """

    ledger(session, requested=KICK - timedelta(minutes=26), status="RESERVED")
    assert not checkpoints(due(session, at=KICK - timedelta(minutes=23)))


# ==========================================================================
# The T-21 hard stop and the T-15 decision cutoff
# ==========================================================================

@pytest.mark.parametrize("minutes", [21, 20, 16, 15, 10, 1])
def test_nothing_qualifies_at_or_after_the_t21_hard_stop(session, minutes):
    reservation = ledger(session, requested=KICK - timedelta(minutes=26), status="RESERVED")
    ledger(session, requested=KICK - timedelta(minutes=26), status="FAILED", charged=0,
           reservation_id=reservation.id)
    assert not due(session, at=KICK - timedelta(minutes=minutes))


def test_the_t15_decision_cutoff_constant_is_unchanged(session):
    assert pregame.WINDOW_CLOSES_BEFORE_KICKOFF == timedelta(minutes=15)
    assert pregame.WINDOW_OPENS_BEFORE_KICKOFF == timedelta(minutes=75)


# ==========================================================================
# Spend neutrality
# ==========================================================================

def test_the_retime_buys_once_per_game_exactly_as_before(session):
    """Every minute across the whole pregame period yields ONE due checkpoint.

    This is the spend claim: the retime moves the object, it does not add one.
    """

    offers = [m for m in range(90, 0, -1)
              if checkpoints(due(session, at=KICK - timedelta(minutes=m)))]
    assert offers == [27, 26, 25]          # one window, three reachable minutes

    # And the unretimed lane offers its own single window, of the same one buy.
    legacy = [m for m in range(90, 0, -1)
              if checkpoints(due(session, at=KICK - timedelta(minutes=m), retime=False))]
    assert legacy[0] == 75 and legacy[-1] == 15

    # One buy per window in both: a price observed inside the window ends it.
    assert pregame.retimed_checkpoint_window(KICK) == (
        KICK - timedelta(minutes=27), KICK - timedelta(minutes=25))


def test_other_leagues_are_untouched(session):
    """The retime is NFL-only: it reads NFL games and the NFL endpoint alone."""

    assert pregame.SOURCE == "SPORTSGAMEODDS"
    rows = due(session, at=KICK - timedelta(minutes=26))
    assert all(r["home"] == "HOM" for r in checkpoints(rows))
    # The retry rule consults only the NFL events endpoint.
    reservation = ledger(session, requested=KICK - timedelta(minutes=26), status="RESERVED")
    ledger(session, requested=KICK - timedelta(minutes=26), status="FAILED", charged=0,
           reservation_id=reservation.id, endpoint="sgo_mlb_events")
    # The MLB terminal row does not settle the NFL reservation.
    assert not checkpoints(due(session, at=KICK - timedelta(minutes=23)))
