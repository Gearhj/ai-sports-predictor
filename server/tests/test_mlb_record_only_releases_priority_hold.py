"""An MLB game the owner has made record-only holds no shared-allowance room (hotfix 2026-10-24, MEDIUM-3).

WHAT HAPPENED
-------------
Owner Oct19: the rest of MLB 2026 is recorded, never funded
(``owner_funding_restrictions.mlb_position_restriction`` ->
``OWNER_MLB_2026_POSTSEASON_RECORD_ONLY``). But ``shared_priority_reservation._mlb_pending``
still treated such a game as "waiting for its decision" and reserved the era's
maximum ($9.94 of the shared $100, $10 in this fixture) away from CFB/NHL until
first pitch -- room nothing could ever use.

THE RULE
--------
A game for which the owner's MLB record-only scope is in force at the decision
moment is not waiting for funding and holds nothing. The ONE predicate
(``mlb_record_only_active``) drives both the hold and the funding refusal, so
they cannot disagree. With the owner clock unset, or for a game outside the
scope (2027), the hold is exactly what it was. SQLite fixtures only.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import owner_funding_restrictions as ofr
from app.services import shared_daily_allowance as sda
from tests.test_shared_priority_reservation import SAT, _settings, saturday  # noqa: F401 -- fixture

START = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)              # the fixture's 8 PM ET Saturday MLB game
OWNER_CLOCK = "2026-10-01T00:00:00+00:00"       # before the fixture Saturday (the live clock is 10-19)


def _mlb_holds(session, settings, moment):
    return [r for r in sda.room(session, settings, day=SAT, moment=moment, sport="CFB")["reservations"]
            if r["sport"] == "MLB"]


def test_the_predicate_is_the_owner_clock_and_the_2026_scope():
    on = SimpleNamespace(owner_large_gap_record_only_effective_from=OWNER_CLOCK)
    off = SimpleNamespace(owner_large_gap_record_only_effective_from=None)
    at = START - timedelta(hours=2)
    assert ofr.mlb_record_only_active(START, moment=at, settings=on) is True
    assert ofr.mlb_record_only_active(START.replace(tzinfo=None), moment=at, settings=on) is True     # naive = UTC
    assert ofr.mlb_record_only_active(START, moment=at, settings=off) is False
    assert ofr.mlb_record_only_active(START, moment=datetime(2026, 9, 30, 23, 59, tzinfo=UTC), settings=on) is False
    assert ofr.mlb_record_only_active(START.replace(year=2027), moment=at, settings=on) is False


def test_with_the_owner_clock_set_a_2026_mlb_game_holds_no_room(session, saturday, settings):
    w = saturday
    moment = START - timedelta(hours=2)                        # awaiting its V18 publication, pre-cutoff
    held = _settings(settings)                                 # owner clock unset: the hold is as before
    assert [r["reserve_usd"] for r in _mlb_holds(session, held, moment)] == [10.0]
    record_only = _settings(settings, owner_large_gap_record_only_effective_from=OWNER_CLOCK)
    assert _mlb_holds(session, record_only, moment) == []
    # The room CFB sees grows by exactly that reservation; the $100 total is untouched.
    before = sda.room(session, held, day=SAT, moment=moment, sport="CFB")
    after = sda.room(session, record_only, day=SAT, moment=moment, sport="CFB")
    assert round(after["room_usd"] - before["room_usd"], 2) == 10.0
    assert after["reservations"] and all(r["sport"] == "NHL" for r in after["reservations"])


def test_a_qualified_bet_awaiting_its_position_holds_nothing_either_when_record_only(session, saturday, settings):
    from tests.test_shared_priority_reservation import _decide

    w = saturday
    _decide(session, w.mlb_era, w.mlb, sport="BASEBALL_MLB", at=START - timedelta(minutes=20),
            recommendation="QUALIFIED_BET")
    after_cutoff = START - timedelta(minutes=10)
    assert [r["state"] for r in _mlb_holds(session, _settings(settings), after_cutoff)] == ["QUALIFIED_AWAITING_POSITION"]
    record_only = _settings(settings, owner_large_gap_record_only_effective_from=OWNER_CLOCK)
    assert _mlb_holds(session, record_only, after_cutoff) == []


def test_a_game_outside_the_owner_scope_still_holds(session, saturday, settings):
    from tests.test_shared_priority_reservation import _mlb_world

    next_year = datetime(2027, 10, 16, 23, 0, tzinfo=UTC)
    _era, game = _mlb_world(session, settings, next_year)
    session.commit()
    record_only = _settings(settings, owner_large_gap_record_only_effective_from=OWNER_CLOCK)
    day = next_year.date()
    holds = [r for r in sda.room(session, record_only, day=day, moment=next_year - timedelta(hours=2), sport="CFB")
             ["reservations"] if r["sport"] == "MLB"]
    assert [r["game_id"] for r in holds] == [str(game.id)]


def test_the_funding_refusal_and_the_hold_release_agree(session, saturday, settings):
    """``mlb_position_restriction`` refuses exactly when the hold is released."""

    w = saturday
    decision = SimpleNamespace(id="d", recommended_at=START - timedelta(minutes=20), model_probability=0.55,
                               sportsbook="book", odds_snapshot_id=None, no_vig_probability=0.5)
    on = _settings(settings, owner_large_gap_record_only_effective_from=OWNER_CLOCK)
    moment = START - timedelta(minutes=10)
    receipt = ofr.mlb_position_restriction(starts_at=START, decision=decision, moment=moment, settings=on)
    assert receipt and receipt["reason"] == "OWNER_MLB_2026_POSTSEASON_RECORD_ONLY" and receipt["record_only"] is True
    assert _mlb_holds(session, on, moment) == []
    off = _settings(settings)
    assert ofr.mlb_position_restriction(starts_at=START, decision=decision, moment=moment, settings=off) is None
    assert w.mlb is not None
