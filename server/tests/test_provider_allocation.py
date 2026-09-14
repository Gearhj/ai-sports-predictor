"""One budget, shared: the arithmetic, the boundary, and the concurrency.

The failure this replaces was not a bug in any one consumer. Each defended its
own share with its own floor, every floor was individually defensible, and
together they funded a daily spend the month could not pay for. So the tests
here are about the properties a single allocation has to have and overlapping
floors cannot: the parts sum to the whole, a lane cannot spend another's share,
an in-flight reservation is visible before it settles, and the reserve is never
reachable however the shares are drawn.

The balance itself comes from the provider's own usage reading, so these also
pin the parsing of that reading and what happens when it is missing.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import ProviderRequestLedger
from app.services.provider_allocation import (
    FALLBACK_MONTHLY_CAP,
    LANES,
    OBJECT_RESERVE,
    PROVIDER,
    USAGE_ENDPOINT_KEY,
    apportion,
    build_plan,
    replenishment_date,
    request_objects,
)
from app.services.sgo_usage import UsageUnavailableError, parse_usage, refresh_usage

USAGE_PAYLOAD = {
    "success": True,
    "data": {
        "tier": "amateur",
        "isActive": True,
        "rateLimits": {
            "per-minute": {"max-requests": 10, "current-requests": 0},
            "per-day": {"max-requests": 500000, "current-requests": 4,
                        "max-entities": 3000000, "current-entities": 18},
            "per-month": {"max-requests": "unlimited",
                          "max-entities": 2500, "current-entities": 1897},
        },
    },
}


def record_usage(session: Session, *, used: int, cap: int = FALLBACK_MONTHLY_CAP,
                 at: datetime | None = None):
    moment = at or datetime.now(UTC)
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=USAGE_ENDPOINT_KEY, requested_at=moment,
        status="RESERVED", request_fingerprint=f"u{used}".ljust(64, "0"),
        quota_cost=0)
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=USAGE_ENDPOINT_KEY, requested_at=moment,
        status="SUCCEEDED", request_fingerprint=f"u{used}".ljust(64, "0"),
        completed_at=moment, reservation_id=reservation.id,
        records_received=0, quota_cost=0,
        response_metadata={"usage": {"monthly_cap": cap, "used_this_month": used}}))
    session.commit()


def spend(session: Session, *, lane: str, objects: int, at: datetime | None = None,
          settled: bool = True):
    moment = at or datetime.now(UTC)
    tag = f"{lane}{objects}{settled}".ljust(64, "0")[:64]
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=lane, requested_at=moment,
        status="RESERVED", request_fingerprint=tag, quota_cost=objects)
    session.add(reservation)
    session.flush()
    if settled:
        session.add(ProviderRequestLedger(
            provider=PROVIDER, endpoint_key=lane, requested_at=moment,
            status="SUCCEEDED", request_fingerprint=tag, completed_at=moment,
            reservation_id=reservation.id, records_received=objects))
    session.commit()


# -- the provider's own numbers -------------------------------------------

def test_the_monthly_entity_cap_is_what_is_read_not_the_request_cap():
    usage = parse_usage(USAGE_PAYLOAD)
    assert usage["monthly_cap"] == 2500
    assert usage["used_this_month"] == 1897
    assert usage["remaining"] == 603
    # Requests are unlimited on this tier; planning against them would be
    # planning against the wrong resource entirely.
    assert usage["requests_per_minute_cap"] == 10


def test_a_response_without_a_numeric_cap_is_refused_not_guessed():
    payload = {"data": {"rateLimits": {"per-month": {"max-entities": "unlimited"}}}}
    with pytest.raises(UsageUnavailableError):
        parse_usage(payload)


def test_the_usage_read_is_recorded_as_a_zero_object_ledger_row(session):
    result = refresh_usage(session, object(), fetch=lambda *_: USAGE_PAYLOAD)
    assert result["read"] is True
    row = session.query(ProviderRequestLedger).filter_by(
        endpoint_key=USAGE_ENDPOINT_KEY, status="SUCCEEDED").one()
    # It returns account data, not event objects, so it must cost nothing.
    assert row.records_received == 0
    assert row.quota_remaining == 603


def test_an_unreachable_usage_endpoint_reports_why_and_writes_nothing(session):
    def boom(_settings):
        raise RuntimeError("connection reset")

    result = refresh_usage(session, object(), fetch=boom)
    assert result["read"] is False
    assert "connection reset" in result["reason"]
    assert session.query(ProviderRequestLedger).count() == 0


# -- replenishment --------------------------------------------------------

def test_the_balance_replenishes_at_the_start_of_the_next_utc_month():
    assert replenishment_date(datetime(2026, 9, 14, tzinfo=UTC)).isoformat() == "2026-10-01"
    assert replenishment_date(datetime(2026, 12, 31, tzinfo=UTC)).isoformat() == "2027-01-01"
    assert replenishment_date(datetime(2026, 2, 3, tzinfo=UTC)).isoformat() == "2026-03-01"


def test_the_daily_rate_uses_what_is_left_and_the_days_left(session):
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    record_usage(session, used=1897, at=now)
    plan = build_plan(session, now=now)
    assert plan.remaining == 603
    assert plan.spendable == 203          # the 400 reserve is never in play
    assert plan.days_remaining == 17      # through 2026-10-01
    assert plan.daily_total == 11         # 203 // 17


# -- the parts sum to the whole -------------------------------------------

@pytest.mark.parametrize("total", [0, 1, 2, 3, 7, 11, 40, 70, 203])
def test_every_apportionment_sums_to_its_total(total):
    allocated = apportion(total)
    assert sum(allocated.values()) == total
    # And nothing is ever negative, at any total.
    assert all(value >= 0 for value in allocated.values())


def test_an_unfunded_lane_gets_nothing_and_its_share_goes_to_the_others():
    allocated = apportion(11)
    unfunded = [lane.key for lane in LANES if not lane.funded]
    assert unfunded, "the test needs at least one unfunded lane to be meaningful"
    for key in unfunded:
        assert allocated[key] == 0
    assert sum(allocated.values()) == 11


def test_a_small_share_is_not_truncated_out_of_existence():
    # Truncation would quietly defund the smallest lane for a whole month.
    allocated = apportion(11)
    assert allocated["sgo_nfl_periods"] >= 1


def test_the_apportionment_is_deterministic():
    assert apportion(11) == apportion(11)


# -- the boundary ---------------------------------------------------------

def test_a_lane_that_has_spent_its_day_is_refused_by_name(session):
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    record_usage(session, used=1897, at=now)
    allowance = build_plan(session, now=now).allocations["sgo_nfl_live"].daily_allowance
    spend(session, lane="sgo_nfl_live", objects=allowance, at=now)

    grant = request_objects(session, lane="sgo_nfl_live", wanted=2, now=now)
    assert grant.granted == 0
    assert grant.reason == "LANE_DAILY_ALLOWANCE_SPENT"


def test_one_lane_spending_its_day_does_not_starve_another(session):
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    record_usage(session, used=1897, at=now)
    plan = build_plan(session, now=now)
    spend(session, lane="sgo_mlb_events",
          objects=plan.allocations["sgo_mlb_events"].daily_allowance, at=now)

    # MLB is done for the day; NFL in-game still has its own share.
    assert request_objects(session, lane="sgo_mlb_events", wanted=1, now=now).granted == 0
    assert request_objects(session, lane="sgo_nfl_live", wanted=1, now=now).granted == 1


def test_a_partial_grant_says_so_rather_than_refusing_outright(session):
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    record_usage(session, used=1897, at=now)
    allowance = build_plan(session, now=now).allocations["sgo_mlb_events"].daily_allowance
    grant = request_objects(session, lane="sgo_mlb_events",
                            wanted=allowance + 50, now=now)
    assert grant.granted == allowance
    assert grant.reason == "GRANTED_PARTIAL"


def test_an_in_flight_reservation_blocks_a_concurrent_job(session):
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    record_usage(session, used=1897, at=now)
    allowance = build_plan(session, now=now).allocations["sgo_mlb_events"].daily_allowance
    # Reserved, no terminal row: another job is mid-request right now.
    spend(session, lane="sgo_mlb_events", objects=allowance, at=now, settled=False)

    grant = request_objects(session, lane="sgo_mlb_events", wanted=1, now=now)
    assert grant.granted == 0
    assert grant.plan.allocations["sgo_mlb_events"].in_flight == allowance


def test_actual_returned_objects_reconcile_against_what_was_reserved(session):
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    record_usage(session, used=1897, at=now)
    allowance = build_plan(session, now=now).allocations["sgo_mlb_events"].daily_allowance

    # Reserved for the whole allowance, but the provider returned one object.
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key="sgo_mlb_events", requested_at=now,
        status="RESERVED", request_fingerprint="r".ljust(64, "0"),
        quota_cost=allowance)
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=PROVIDER, endpoint_key="sgo_mlb_events", requested_at=now,
        status="SUCCEEDED", request_fingerprint="r".ljust(64, "0"),
        completed_at=now, reservation_id=reservation.id, records_received=1))
    session.commit()

    allocation = build_plan(session, now=now).allocations["sgo_mlb_events"]
    # The settled count is what is charged; the reservation stops counting once
    # it has a terminal row, so an over-reservation is given back.
    assert allocation.spent_today == 1
    assert allocation.in_flight == 0
    assert allocation.room == allowance - 1


def test_the_reserve_is_never_reachable_however_the_shares_are_drawn(session):
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    # Exactly at the reserve: nothing above it is spendable.
    record_usage(session, used=FALLBACK_MONTHLY_CAP - OBJECT_RESERVE, at=now)
    plan = build_plan(session, now=now)
    assert plan.spendable == 0
    assert plan.daily_total == 0
    for lane in LANES:
        assert request_objects(session, lane=lane.key, wanted=1, now=now).granted == 0


def test_a_missing_usage_reading_falls_back_and_says_it_is_blind(session):
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    spend(session, lane="sgo_mlb_events", objects=50, at=now)
    plan = build_plan(session, now=now)
    assert plan.source == "LOCAL_LEDGER_COUNT"
    assert plan.used_this_month == 50
    assert any("cannot see a spend made" in warning for warning in plan.warnings)


def test_spend_since_the_last_reading_is_added_to_the_providers_number(session):
    read_at = datetime(2026, 9, 14, 6, tzinfo=UTC)
    now = read_at + timedelta(hours=3)
    record_usage(session, used=1897, at=read_at)
    spend(session, lane="sgo_mlb_events", objects=20, at=now)

    plan = build_plan(session, now=now)
    # Otherwise a heavy hour would be invisible until the next read.
    assert plan.used_this_month == 1917
    assert plan.remaining == 583


def test_an_unknown_lane_is_refused_rather_than_given_a_default(session):
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    record_usage(session, used=1897, at=now)
    grant = request_objects(session, lane="sgo_something_new", wanted=5, now=now)
    assert grant.granted == 0
    assert grant.reason == "UNKNOWN_LANE:sgo_something_new"
