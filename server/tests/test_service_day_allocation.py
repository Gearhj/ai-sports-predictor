"""The Eastern service day, and an allowance that does not move once issued.

TWO FAILURES THIS PINS
----------------------
A UTC budget day against Eastern slates. Eighteen NFL objects spent at 22:00
Eastern on 09-13 landed in the UTC day 09-14 and exhausted an MLB allowance
before the 09-14 Eastern slate existed. The internal day is now Eastern, so an
evening request belongs to that evening's budget.

And an allowance recomputed from the falling balance, which shrank as it was
spent: a lane told it had eight objects could draw only seven. The day's
capacity is now fixed at its first calculation and reconciled against, never
reduced.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.services.provider_allocation import (
    DEFAULT_OBJECT_RESERVE, reserve_for, reserve_note,
)
from app.services.service_day import (
    ALLOCATION_DAY_SEMANTICS, receipt_clocks, service_date, service_day_bounds,
)


def test_an_evening_eastern_request_belongs_to_that_eastern_day():
    """22:00 ET on the 13th is the 13th, not the 14th."""

    # 2026-09-14T02:00Z is 2026-09-13 22:00 EDT -- the exact instant of the
    # first of the four mislabelled requests.
    assert service_date(datetime(2026, 9, 14, 2, 0, tzinfo=UTC)) == date(2026, 9, 13)
    # And 04:00Z is Eastern midnight, the first moment of the 14th.
    assert service_date(datetime(2026, 9, 14, 4, 0, tzinfo=UTC)) == date(2026, 9, 14)


def test_an_nfl_request_after_8pm_does_not_touch_the_next_slates_allocation():
    """The specific check the assignment names."""

    after_eight = datetime(2026, 9, 15, 0, 30, tzinfo=UTC)   # 20:30 EDT on 09-14
    assert service_date(after_eight) == date(2026, 9, 14)
    start, end = service_day_bounds(date(2026, 9, 15))
    assert after_eight < start, "20:30 ET must fall BEFORE the next service day"


def test_the_service_day_is_a_full_local_day():
    start, end = service_day_bounds(date(2026, 9, 14))
    assert (end - start).total_seconds() == 24 * 3600
    assert start == datetime(2026, 9, 14, 4, tzinfo=UTC)     # EDT is UTC-4


def test_receipts_carry_both_clocks_and_the_semantics_version():
    clocks = receipt_clocks(datetime(2026, 9, 14, 23, 50, tzinfo=UTC))
    assert clocks["service_date"] == "2026-09-14"
    assert clocks["observed_at_utc"].startswith("2026-09-14T23:50")
    assert clocks["allocation_day_semantics"] == ALLOCATION_DAY_SEMANTICS
    # The provider's month is its own clock and is labelled as such.
    assert "provider" in clocks["provider_monthly_clock"].lower()


def test_the_reserve_reduction_expires_by_itself():
    assert reserve_for(date(2026, 9, 13)) == DEFAULT_OBJECT_RESERVE
    assert reserve_for(date(2026, 9, 14)) == 300
    assert reserve_for(date(2026, 9, 30)) == 300
    # Restored at the October reset with nobody having to remember.
    assert reserve_for(date(2026, 10, 1)) == DEFAULT_OBJECT_RESERVE
    note = reserve_note(date(2026, 9, 14))
    assert note["scheduled"] is True
    assert note["restores_to"] == DEFAULT_OBJECT_RESERVE


def test_the_allowance_does_not_shrink_as_the_day_is_spent(session):
    """The snapshot is issued once and reconciled against, never reduced."""

    from app.models import ProviderAllocationSnapshot
    from app.services.provider_allocation import daily_snapshot

    moment = datetime(2026, 9, 14, 18, tzinfo=UTC)
    first = daily_snapshot(session, now=moment)
    issued = dict(first.lanes)
    total = first.daily_total

    # A later calculation on the same service day returns the SAME row.
    later = daily_snapshot(session, now=datetime(2026, 9, 14, 23, tzinfo=UTC))
    assert later.id == first.id
    assert dict(later.lanes) == issued
    assert later.daily_total == total

    assert session.query(ProviderAllocationSnapshot).count() == 1


def test_a_new_service_day_gets_its_own_allocation(session):
    from app.services.provider_allocation import daily_snapshot

    today = daily_snapshot(session, now=datetime(2026, 9, 14, 18, tzinfo=UTC))
    tomorrow = daily_snapshot(session, now=datetime(2026, 9, 15, 18, tzinfo=UTC))
    assert tomorrow.id != today.id
    assert tomorrow.service_date != today.service_date
