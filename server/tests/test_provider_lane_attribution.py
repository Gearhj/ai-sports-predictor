"""A lane must not be charged for a request it never made.

WHAT HAPPENED
-------------
Before the NFL collectors were taught to name their own lane, they called
`record_request` without an `endpoint_key` and fell through to its default --
the MLB lane. On the evening of 2026-09-13, eighteen NFL objects were booked as
MLB pregame spend. The budget day is the UTC day, which begins at 20:00 ET, so
those four requests landed in 09-14's budget and exhausted the MLB allowance
before the 09-14 slate could be priced at all. Ten MLB games showed no price,
and the ledger appeared to say MLB had spent eighteen objects on them.

WHY THE LEDGER IS NOT REWRITTEN
-------------------------------
The rows record what was actually requested, including the filters that prove
the mislabel. Rewriting them would destroy the evidence the correction depends
on and would be editing accounting history to make a number look right. The
attribution is corrected instead, from each row's own retained filters.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import ProviderRequestLedger
from app.services.provider_allocation import (
    LANE_LEAGUE, PROVIDER, build_plan, misattributed_rows,
)

#: Noon UTC is 08:00 EDT -- comfortably inside the 09-14 Eastern service day,
#: which runs 04:00Z to 04:00Z. Offsets below stay inside it on purpose: a row
#: ten hours earlier would be 22:00 ET on the 13th and belongs to THAT day,
#: which is the whole point of the Eastern boundary.
NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)
SAME_DAY = datetime(2026, 9, 14, 6, tzinfo=UTC)   # 02:00 EDT on the 14th


def _row(session, *, lane, league, objects, at, fingerprint, metadata=...):
    """A reservation and its settlement, as the real lifecycle writes them.

    A SUCCEEDED row without a reservation violates the ledger's own lifecycle
    constraint, so the pair is created rather than a bare settlement -- which
    also means the test exercises the shape the accounting actually reads.
    """

    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=lane, requested_at=at,
        status="RESERVED", request_fingerprint=fingerprint, quota_cost=objects)
    session.add(reservation)
    session.flush()
    if metadata is ...:
        metadata = {"request_shape": {"filters": {"leagueID": league}}}
    session.add(ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=lane, requested_at=at,
        status="SUCCEEDED", completed_at=at, records_received=objects,
        request_fingerprint=fingerprint, reservation_id=reservation.id,
        response_metadata=metadata))
    session.flush()


def test_every_lane_declares_the_leagues_it_may_buy():
    # If a lane is ever added without a league, the correction silently stops
    # protecting it, so the mapping is asserted to cover all of them.
    from app.services.provider_allocation import LANE_LEAGUES, LANES
    assert {lane.key for lane in LANES} == set(LANE_LEAGUES)
    assert all(leagues for leagues in LANE_LEAGUES.values()), "an open set protects nothing"
    # One day-before show run covers whatever show sports are on the slate, so
    # the preview lane is the one lane with more than one permitted league --
    # and it is still closed. NBA and College Football are absent because
    # SportsGameOdds' identifiers for them are in no retained evidence.
    assert LANE_LEAGUES["sgo_show_preview_events"] == frozenset({"MLB", "NFL"})
    assert LANE_LEAGUE["sgo_show_preview_events"] is None, "a multi-league lane names no single league"
    assert LANE_LEAGUE["sgo_mlb_events"] == "MLB"


def test_a_preview_run_may_buy_either_show_sport_but_not_a_third(session):
    """A closed set, not an exemption."""

    from app.services.provider_allocation import LANE_LEAGUES

    _row(session, lane="sgo_show_preview_events", league="MLB", objects=2,
         at=SAME_DAY, fingerprint="p" * 64)
    _row(session, lane="sgo_show_preview_events", league="NFL", objects=1,
         at=SAME_DAY, fingerprint="q" * 64)
    assert misattributed_rows(session, now=NOW) == []

    _row(session, lane="sgo_show_preview_events", league="NBA", objects=1,
         at=SAME_DAY, fingerprint="r" * 64)
    bad = misattributed_rows(session, now=NOW)
    assert len(bad) == 1
    assert bad[0].response_metadata["request_shape"]["filters"]["leagueID"] == "NBA"
    assert "NBA" not in LANE_LEAGUES["sgo_show_preview_events"]


def test_an_nfl_request_booked_to_the_mlb_lane_is_detected(session):
    _row(session, lane="sgo_mlb_events", league="NFL", objects=14,
         at=SAME_DAY, fingerprint="a" * 64)
    bad = misattributed_rows(session, now=NOW)
    assert len(bad) == 1
    assert bad[0].records_received == 14


def test_a_correctly_booked_request_is_not_flagged(session):
    _row(session, lane="sgo_mlb_events", league="MLB", objects=5,
         at=NOW - timedelta(hours=2), fingerprint="b" * 64)
    _row(session, lane="sgo_nfl_live", league="NFL", objects=2,
         at=NOW - timedelta(hours=2), fingerprint="c" * 64)
    assert misattributed_rows(session, now=NOW) == []


def test_the_mlb_lane_is_not_starved_by_another_leagues_spend(session):
    """The exact 2026-09-14 failure, reproduced and then corrected."""

    for i, objects in enumerate((1, 14, 2, 1)):
        _row(session, lane="sgo_mlb_events", league="NFL", objects=objects,
             at=SAME_DAY, fingerprint=str(i) * 64)

    plan = build_plan(session, now=NOW)
    mlb = plan.allocations["sgo_mlb_events"]
    # Eighteen NFL objects must not appear as MLB spend.
    assert mlb.spent_today == 0
    assert mlb.room == mlb.daily_allowance

    # But they are still real spend against the month: the objects were bought.
    assert plan.used_this_month >= 18


def test_a_row_without_retained_filters_is_left_alone(session):
    """Absence of evidence is not evidence of mislabelling."""

    _row(session, lane="sgo_mlb_events", league=None, objects=3, at=NOW,
         fingerprint="d" * 64, metadata={})
    assert misattributed_rows(session, now=NOW) == []
    assert build_plan(session, now=NOW).allocations["sgo_mlb_events"].spent_today == 3


# -- the lane a request settles under ----------------------------------------
#
# The correction above is ONE-SIDED on purpose: it takes objects off the lane
# that was wrongly charged, and it cannot put them on the lane that really
# spent them, because a corrected attribution is a guess about where they
# belong. So while terminal rows were written under a hard-coded MLB key, the
# NFL lane read as unspent after spending and its daily allowance was available
# to draw a second time. These pin the source fix: the terminal row settles
# under the lane its reservation named.

def _round_trip(session, *, lane, params, objects, now, succeeded=True):
    """A real reservation and settlement, through the production writers."""

    from app.services.sgo_collection import complete_request, object_budget, record_request

    budget = object_budget(session, now=now)
    reservation = record_request(session, requested_at=now, params=params, budget=budget,
                                 objects_requested=objects, endpoint_key=lane)
    session.flush()
    complete_request(session, reservation, objects=objects, succeeded=succeeded, budget=budget)
    session.flush()
    return reservation


def test_an_nfl_reservation_settles_on_the_nfl_lane(session):
    from app.models import ProviderRequestLedger

    _round_trip(session, lane="sgo_nfl_events", params={"leagueID": "NFL", "limit": "1"},
                objects=1, now=SAME_DAY)

    rows = session.query(ProviderRequestLedger).filter(
        ProviderRequestLedger.endpoint_key == "sgo_nfl_events").all()
    assert {row.status for row in rows} == {"RESERVED", "SUCCEEDED"}
    terminal = next(row for row in rows if row.status == "SUCCEEDED")
    assert terminal.records_received == 1
    assert terminal.response_metadata["lane"] == "sgo_nfl_events"
    # And nothing landed on a lane that did not ask for it.
    assert session.query(ProviderRequestLedger).filter(
        ProviderRequestLedger.endpoint_key == "sgo_mlb_events").count() == 0


def test_lane_usage_attributes_that_object_to_nfl(session):
    from app.services.provider_allocation import _lane_usage

    _round_trip(session, lane="sgo_nfl_events", params={"leagueID": "NFL", "limit": "1"},
                objects=1, now=SAME_DAY)

    usage = _lane_usage(session, now=NOW)
    assert usage["sgo_nfl_events"] == (1, 0), "the lane that spent it must see it"
    assert usage["sgo_mlb_events"] == (0, 0), "and no other lane may be charged"
    # Correctly filed, so there is nothing for the correction to undo.
    assert misattributed_rows(session, now=NOW) == []


def test_a_lane_that_has_spent_can_no_longer_draw_the_same_allowance(session):
    """The consequence the fix exists for: a cap the lane can actually see.

    An NFL object reserved and settled under the MLB key left NFL's own lane
    reading zero, so NFL could be granted its daily allowance again after
    already having spent it.
    """

    from app.services.provider_allocation import request_objects

    _row(session, lane="sgo_nfl_live", league="NFL", objects=2,
         at=SAME_DAY, fingerprint="e" * 64)
    before = build_plan(session, now=NOW).allocations["sgo_nfl_live"]
    assert before.spent_today == 2
    assert before.room == max(0, before.daily_allowance - 2)

    granted = request_objects(session, lane="sgo_nfl_live", wanted=before.daily_allowance, now=NOW)
    assert granted.granted <= before.room


def test_the_mlb_lane_reads_the_same_before_and_after_the_fix(session):
    """The fix tightens; it does not release a single MLB object.

    MLB was never over-charged for another lane's objects -- the one-sided
    correction already removed them. So filing them correctly changes MLB's
    number by zero, which is what makes this safe to deploy inside a month
    whose limits the owner set deliberately.
    """

    # What the bug produced: an NFL request settled under the MLB key.
    _row(session, lane="sgo_mlb_events", league="NFL", objects=3,
         at=SAME_DAY, fingerprint="f" * 64)
    _row(session, lane="sgo_mlb_events", league="MLB", objects=7,
         at=SAME_DAY, fingerprint="g" * 64)
    buggy = build_plan(session, now=NOW).allocations["sgo_mlb_events"].spent_today

    # What the fix produces: the same two requests, each on its own lane.
    other = _fresh_session_like(session)
    _row(other, lane="sgo_nfl_events", league="NFL", objects=3,
         at=SAME_DAY, fingerprint="h" * 64)
    _row(other, lane="sgo_mlb_events", league="MLB", objects=7,
         at=SAME_DAY, fingerprint="i" * 64)
    fixed = build_plan(other, now=NOW).allocations["sgo_mlb_events"].spent_today

    assert buggy == fixed == 7
    # And only the fixed ledger lets the NFL lane see its own spend.
    assert build_plan(other, now=NOW).allocations["sgo_nfl_events"].spent_today == 3


def _fresh_session_like(_session):
    """A second isolated session, so two ledgers can be compared side by side."""

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_an_explicit_lane_overrides_the_reservation(session):
    """The Show Preview collector names its lane rather than inferring it."""

    from app.models import ProviderRequestLedger
    from app.services.sgo_collection import complete_request, object_budget, record_request

    budget = object_budget(session, now=SAME_DAY)
    reservation = record_request(session, requested_at=SAME_DAY, params={"leagueID": "NFL"},
                                 budget=budget, objects_requested=1,
                                 endpoint_key="sgo_show_preview_events")
    session.flush()
    complete_request(session, reservation, objects=1, succeeded=True, budget=budget,
                     endpoint_key="sgo_show_preview_events",
                     extra_metadata={"horizon": "SHOW_PREVIEW"})
    session.flush()

    terminal = session.query(ProviderRequestLedger).filter(
        ProviderRequestLedger.status == "SUCCEEDED").one()
    assert terminal.endpoint_key == "sgo_show_preview_events"
    assert terminal.response_metadata["horizon"] == "SHOW_PREVIEW"
