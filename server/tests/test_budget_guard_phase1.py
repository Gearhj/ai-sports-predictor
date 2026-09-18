"""Phase 1 of the SportsGameOdds budget-guard repair.

The two guards asked the same question with different arithmetic. The local
object budget counted a rolling 30 days; the provider bills a UTC calendar
month and refills on the 1st. The consequence was dated and specific: with
September's allowance spent, the MLB and NFL pregame lanes would have stayed
dark from roughly October 1 to October 9 while the provider balance sat
refilled at 2,500 -- and the allocation plan would have zeroed October 1 as
well, because it took the newest usage reading without asking which month it
described.

Phase 1 aligns the window and refuses a prior-month reading. Phase 2
(2026-09-18, on the owner's instruction applying the September 300 recorded
2026-09-14) makes both guards read one instant-based schedule, ``reserve_at``:
300 through the September 2026 billing month, the October default of 400 from
2026-10-01T00:00Z -- including the four hours of the Sept 30 service day that
already belong to October's bill.

See docs/SGO_BUDGET_GUARD_AUDIT.md.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProviderRequestLedger
from app.services.provider_allocation import (
    reserve_at,
    DEFAULT_OBJECT_RESERVE,
    FALLBACK_MONTHLY_CAP,
    PROVIDER,
    USAGE_ENDPOINT_KEY,
    build_plan,
    request_objects,
    reserve_for,
)
from app.services.sgo_collection import (
    FREE_TIER_OBJECTS,
    MAX_EVENTS_PER_FETCH,
    OBJECT_RESERVE,
    OPENING_ADJUSTMENT_KEY,
    object_budget,
    phase1_reserve,
)
from app.services.service_day import service_date

SEPTEMBER_LATE = datetime(2026, 9, 30, 23, 50, tzinfo=UTC)
OCTOBER_FIRST = datetime(2026, 10, 1, 0, 0, 1, tzinfo=UTC)


def _usage(session: Session, *, used: int, at: datetime, cap: int = FALLBACK_MONTHLY_CAP) -> None:
    """A zero-object provider usage reading, exactly as the collector writes it."""

    tag = f"usage{used}{at.isoformat()}".ljust(64, "0")[:64]
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=USAGE_ENDPOINT_KEY, requested_at=at,
        status="RESERVED", request_fingerprint=tag, quota_cost=0)
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=USAGE_ENDPOINT_KEY, requested_at=at,
        status="SUCCEEDED", request_fingerprint=tag, completed_at=at,
        reservation_id=reservation.id, records_received=0, quota_cost=0,
        response_metadata={"usage": {"monthly_cap": cap, "used_this_month": used}}))
    session.commit()


def _spend(session: Session, *, objects: int | None, at: datetime,
           lane: str = "sgo_mlb_events", status: str = "SUCCEEDED") -> None:
    """A completed request pair that charged ``objects`` entities."""

    tag = f"{lane}{objects}{at.isoformat()}".ljust(64, "0")[:64]
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=lane, requested_at=at, status="RESERVED",
        request_fingerprint=tag, quota_cost=objects or 0)
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=lane, requested_at=at, status=status,
        request_fingerprint=tag, completed_at=at, reservation_id=reservation.id,
        records_received=objects))
    session.commit()


# -- 1. the September 30 to October 1 rollover -------------------------------

def test_the_monthly_reset_releases_the_window_at_midnight_utc(session) -> None:
    """The whole point of the repair, stated as one boundary.

    Under the rolling window these same rows still counted on October 1 and
    would have kept counting until October 9.
    """

    # September's ceiling is 2,500 - 300 = 2,200 since phase 2 (owner's schedule).
    _spend(session, objects=1300, at=datetime(2026, 9, 10, 12, tzinfo=UTC))
    _spend(session, objects=900, at=datetime(2026, 9, 25, 12, tzinfo=UTC))

    september = object_budget(session, now=SEPTEMBER_LATE)
    assert september.billing_month == "2026-09"
    assert september.objects_used == 2200
    assert september.spendable == 0, "September's allowance is spent"

    october = object_budget(session, now=OCTOBER_FIRST)
    assert october.billing_month == "2026-10"
    assert october.window_start == "2026-10-01T00:00:00+00:00"
    assert october.objects_used == 0
    assert october.spendable == FREE_TIER_OBJECTS - DEFAULT_OBJECT_RESERVE
    assert october.may_spend(1)[0] is True


def test_the_service_day_that_spans_the_reset_does_not_double_count(session) -> None:
    """September 30 Eastern runs to 04:00 UTC on October 1.

    Objects bought on the morning of October 1 UTC belong to October's bill,
    whatever service day AISP filed them under. Neither month may see both.
    """

    _spend(session, objects=40, at=datetime(2026, 9, 30, 22, tzinfo=UTC))
    _spend(session, objects=17, at=datetime(2026, 10, 1, 2, tzinfo=UTC))

    september = object_budget(session, now=datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    october = object_budget(session, now=datetime(2026, 10, 1, 3, tzinfo=UTC))
    assert september.objects_used == 40
    assert october.objects_used == 17


# -- 2. a stale September balance read on October 1 --------------------------

def test_a_september_reading_is_ignored_on_october_first(session) -> None:
    """The reading taken at 23:50 UTC on September 30 reports SEPTEMBER usage.

    Trusted as October's opening balance it says 1,926 of 2,500 are gone, the
    day's allowance computes to zero and every lane pauses for a full day on a
    freshly refilled account.
    """

    _usage(session, used=1926, at=SEPTEMBER_LATE)
    _spend(session, objects=30, at=datetime(2026, 10, 1, 1, tzinfo=UTC))

    plan = build_plan(session, now=datetime(2026, 10, 1, 12, tzinfo=UTC))
    assert plan.source == "LOCAL_LEDGER_COUNT_PRIOR_MONTH_READING_IGNORED"
    assert plan.used_this_month == 30, "only October's own spend counts"
    assert plan.daily_total > 0, "the account has refilled; the lanes must open"
    assert any("earlier billing month" in warning for warning in plan.warnings)
    assert any("costs" in warning and "no objects" in warning for warning in plan.warnings)


def test_a_current_month_reading_is_still_the_authority(session) -> None:
    """The month check rejects history, not the provider."""

    _usage(session, used=1926, at=datetime(2026, 9, 15, 6, tzinfo=UTC))
    _spend(session, objects=20, at=datetime(2026, 9, 15, 9, tzinfo=UTC))

    plan = build_plan(session, now=datetime(2026, 9, 15, 12, tzinfo=UTC))
    assert plan.source == "PROVIDER_ACCOUNT_USAGE"
    assert plan.used_this_month == 1946, "the reading plus AISP's spend since it"


# -- 3. correct current-month usage counting ---------------------------------

def test_only_the_current_months_rows_are_counted(session) -> None:
    _spend(session, objects=500, at=datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC))
    _spend(session, objects=11, at=datetime(2026, 9, 1, 0, 0, 1, tzinfo=UTC))
    _spend(session, objects=6, at=datetime(2026, 9, 14, 12, tzinfo=UTC))

    budget = object_budget(session, now=datetime(2026, 9, 20, 12, tzinfo=UTC))
    assert budget.objects_used == 17
    assert budget.requests == 2


def test_an_uncounted_request_is_still_charged_the_worst_case(session) -> None:
    """Under-counting an allowance is how an allowance is exceeded."""

    _spend(session, objects=None, at=datetime(2026, 9, 12, 12, tzinfo=UTC))
    _spend(session, objects=3, at=datetime(2026, 9, 13, 12, tzinfo=UTC))

    budget = object_budget(session, now=datetime(2026, 9, 20, 12, tzinfo=UTC))
    assert budget.objects_used == MAX_EVENTS_PER_FETCH + 3


def test_the_opening_adjustment_is_counted_in_its_month_but_is_not_a_request(session) -> None:
    """It carries objects nobody called for, so it adds to the spend and not the count."""

    _spend(session, objects=24, at=datetime(2026, 9, 9, 12, tzinfo=UTC),
           lane=OPENING_ADJUSTMENT_KEY)
    _spend(session, objects=5, at=datetime(2026, 9, 10, 12, tzinfo=UTC))

    budget = object_budget(session, now=datetime(2026, 9, 20, 12, tzinfo=UTC))
    assert budget.objects_used == 29
    assert budget.requests == 1
    assert object_budget(session, now=OCTOBER_FIRST).objects_used == 0


# -- 4. the 400-object reserve -----------------------------------------------

def test_phase_two_applies_the_owners_september_schedule_in_both_guards(session) -> None:
    """Phase 2 (2026-09-18, owner instruction): guard B takes the schedule exactly.

    The September 300 recorded 2026-09-14 (commit 21e1cab) used to reach only
    guard A; both guards now read ``reserve_at``.
    """

    moment = datetime(2026, 9, 20, 12, tzinfo=UTC)
    assert reserve_for(service_date(moment)) == reserve_at(moment) == 300
    reserve, basis = phase1_reserve(moment)
    assert reserve == 300 and "2026-09-14" in basis

    budget = object_budget(session, now=moment)
    assert budget.reserve == 300
    assert budget.spendable == FREE_TIER_OBJECTS - 300


def test_the_september_reserve_ends_at_the_providers_reset_not_eastern_midnight(session) -> None:
    """Sept 30 8 PM ET is 2026-10-01T00:00Z: October's bill, still the Sept 30 service day."""

    last_september = datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)     # 19:59:59 ET Sept 30
    first_october = datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)          # 20:00 ET Sept 30
    october_service_day = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)      # 00:30 ET Oct 1
    assert service_date(first_october).isoformat() == "2026-09-30"      # the trap
    assert (reserve_at(last_september), reserve_at(first_october), reserve_at(october_service_day)) == (300, 400, 400)
    assert phase1_reserve(last_september)[0] == 300 and phase1_reserve(first_october)[0] == 400
    assert object_budget(session, now=last_september).reserve == 300
    assert object_budget(session, now=first_october).reserve == 400
    # The schedule starts on the Eastern service day 2026-09-14, not before.
    assert reserve_at(datetime(2026, 9, 14, 3, 59, tzinfo=UTC)) == 400    # 23:59 ET Sept 13
    assert reserve_at(datetime(2026, 9, 14, 4, 0, tzinfo=UTC)) == 300     # 00:00 ET Sept 14


def test_guard_a_enforces_the_instant_reserve_even_on_the_sept_30_snapshot(session) -> None:
    """The Sept 30 snapshot froze 300 at Eastern midnight; after the reset guard A holds 400."""

    september_evening = datetime(2026, 9, 30, 12, tzinfo=UTC)
    snapshot_plan = build_plan(session, now=september_evening)
    assert snapshot_plan.reserve == 300
    assert build_plan(session, now=datetime(2026, 10, 1, 0, 30, tzinfo=UTC)).reserve == 400


def test_the_reserve_is_four_hundred_once_the_schedule_lapses(session) -> None:
    october = datetime(2026, 10, 5, 12, tzinfo=UTC)
    assert reserve_for(service_date(october)) == DEFAULT_OBJECT_RESERVE
    assert phase1_reserve(october)[0] == 400
    assert object_budget(session, now=october).reserve == 400


def test_september_spendable_moves_by_exactly_the_approved_hundred(session) -> None:
    """Phase 1 left September's number unchanged (verified 2026-09-15). Phase 2
    releases exactly the 100 objects the owner approved: the window still covers
    the same rows, and only the reserve moves from 400 to 300.
    """

    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    for day, objects in ((9, 1245), (10, 330), (11, 25), (14, 333)):
        _spend(session, objects=objects, at=datetime(2026, 9, day, 12, tzinfo=UTC))

    budget = object_budget(session, now=now)
    rolling_since = now - timedelta(days=30)
    rolling = session.scalar(
        select(ProviderRequestLedger.records_received)
        .where(ProviderRequestLedger.requested_at < rolling_since))
    assert rolling is None, "nothing in the ledger predates the old window"
    assert budget.objects_used == 1933
    assert budget.spendable == FREE_TIER_OBJECTS - 300 - 1933 == 267


def test_the_reserve_is_never_reachable(session) -> None:
    now = datetime(2026, 9, 20, 12, tzinfo=UTC)
    _spend(session, objects=FREE_TIER_OBJECTS - reserve_at(now) - 5, at=now - timedelta(days=1))

    budget = object_budget(session, now=now)
    assert budget.spendable == 5
    assert budget.may_spend(5)[0] is True
    allowed, refusal = budget.may_spend(6)
    assert allowed is False
    assert "reserve" in refusal and "2026-09" in refusal


# -- 5. fail-closed behaviour ------------------------------------------------

def test_a_moment_without_an_offset_authorises_nothing(session) -> None:
    """A month boundary cannot be placed without an offset.

    At 23:40 on the last day of a month the guess decides which month's bill a
    request lands on, so the guard refuses to guess.
    """

    budget = object_budget(session, now=datetime(2026, 9, 30, 23, 40))
    assert budget.blocked_reason is not None
    assert "billing month" in budget.blocked_reason
    assert budget.spendable == 0
    allowed, refusal = budget.may_spend(1)
    assert allowed is False and "unavailable" in refusal


def test_a_blocked_budget_reports_itself_as_blocked(session) -> None:
    body = object_budget(session, now=datetime(2026, 9, 30, 23, 40)).as_dict()
    assert body["blocked_reason"]
    assert body["objects_spendable"] == 0


def test_a_zero_or_negative_request_is_still_refused(session) -> None:
    budget = object_budget(session, now=datetime(2026, 9, 20, 12, tzinfo=UTC))
    assert budget.may_spend(0)[0] is False
    assert budget.may_spend(-5)[0] is False


# -- 6. no regression in the MLB or NFL pregame lanes ------------------------

def test_the_pregame_lanes_are_not_dark_in_october(session) -> None:
    """The consequence the repair exists to prevent, tested where it bit.

    September's allowance is spent and the newest reading is September's. Both
    guards must nevertheless open on October 2: the plan because it ignores a
    prior-month reading, the local budget because September's rows are on
    September's bill.
    """

    _usage(session, used=1926, at=SEPTEMBER_LATE)  # the stale reading
    _spend(session, objects=2100, at=datetime(2026, 9, 25, 12, tzinfo=UTC))
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)

    budget = object_budget(session, now=now)
    assert budget.objects_used == 0 and budget.spendable > 0

    plan = build_plan(session, now=now)
    assert plan.daily_total > 0

    grant = request_objects(session, lane="sgo_mlb_events", wanted=1, now=now)
    assert grant.granted == 1, f"MLB pregame was refused: {grant.reason}"
    # NFL pregame draws its share from reserved demand, which this fixture has
    # none of. The refusal must therefore be about the lane's own allowance --
    # never about the month or the reserve.
    nfl = request_objects(session, lane="sgo_nfl_events", wanted=1, now=now)
    assert nfl.reason in (None, "LANE_DAILY_ALLOWANCE_SPENT")


def test_a_pregame_lane_is_refused_by_name_when_the_local_guard_is_spent(session) -> None:
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    _usage(session, used=0, at=datetime(2026, 10, 1, 6, tzinfo=UTC))
    _spend(session, objects=FREE_TIER_OBJECTS - OBJECT_RESERVE,
           at=datetime(2026, 10, 1, 12, tzinfo=UTC))

    budget = object_budget(session, now=now)
    assert budget.spendable == 0
    allowed, refusal = budget.may_spend(1)
    assert allowed is False and "object budget would be exceeded" in refusal


def test_the_live_lanes_are_still_governed_by_the_plan_alone(session) -> None:
    """A regression pin, not a behaviour change.

    In-game collection answers to the allocation plan; the local object budget
    is recorded alongside it for accounting. Phase 1 must not quietly promote
    the local guard into those lanes.
    """

    import inspect

    from app.scoreboard import collect as periods
    from app.scoreboard import nfl_live

    for module in (periods, nfl_live):
        source = inspect.getsource(module)
        assert "object_budget(" in source, "the budget is still recorded"
        assert "budget.may_spend(" not in source, (
            f"{module.__name__} must not enforce the local object guard")


# -- 7. no effect on persisted request-ledger rows ---------------------------

def test_the_repair_reads_the_ledger_and_never_writes_it(session) -> None:
    """Both guards are recomputed from the ledger on every read.

    That is what makes the rollback in the audit a plain revert: there is no
    persisted state under either guard to unwind.
    """

    at = datetime(2026, 9, 12, 12, tzinfo=UTC)
    _spend(session, objects=42, at=at)
    before = [
        (row.id, row.status, row.records_received, row.requested_at, row.quota_cost)
        for row in session.scalars(select(ProviderRequestLedger).order_by(ProviderRequestLedger.id))
    ]

    object_budget(session, now=datetime(2026, 9, 20, 12, tzinfo=UTC))
    object_budget(session, now=OCTOBER_FIRST)
    object_budget(session, now=datetime(2026, 9, 30, 23, 40))
    session.flush()

    after = [
        (row.id, row.status, row.records_received, row.requested_at, row.quota_cost)
        for row in session.scalars(select(ProviderRequestLedger).order_by(ProviderRequestLedger.id))
    ]
    assert after == before
