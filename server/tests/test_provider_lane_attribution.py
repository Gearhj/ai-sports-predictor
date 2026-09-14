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


def test_every_lane_declares_the_league_it_may_buy():
    # If a lane is ever added without a league, the correction silently stops
    # protecting it, so the mapping is asserted to cover all of them.
    from app.services.provider_allocation import LANES
    assert {lane.key for lane in LANES} == set(LANE_LEAGUE)


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
