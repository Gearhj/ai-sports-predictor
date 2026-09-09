"""Monthly and daily credit budgeting for The Odds API, from persisted evidence.

TWO INDEPENDENT GUARDS, BOTH FAIL-CLOSED
----------------------------------------
1. **The provider floor** (existing, unchanged): the newest successful reading's
   ``requests_remaining`` must stay above ``ODDS_QUOTA_FLOOR``. That guard lives
   in ``app.jobs.odds_quota_recovery`` and this module never relaxes it.
2. **The AISP budget** (new): our own ledger accounting of what we have spent
   this billing month, against a configured cap and an untouchable reserve.

The second guard exists because the first can only speak after a request has
already been made. A reading is a rear-view mirror; a budget is a speed limit.

BILLING MONTH
-------------
Measured, not assumed: at 2026-08-31 20:02 EDT (2026-09-01 00:02 UTC) the
provider's ``x-requests-used`` header fell from 477 to 3 and
``x-requests-remaining`` rose from 23 to 497. The billing month is therefore the
**UTC calendar month**, and the window starts at 00:00 UTC on the 1st.

WHAT COUNTS AS SPENT
--------------------
Every RESERVED row in the current window counts at its policy cost, whether or
not a response came back. A reservation that never completed may still have
reached the provider, so counting it as free would be optimistic in exactly the
situation where optimism is most expensive. Where a row completed and carries a
real ``quota_cost`` header, that measured value replaces the estimate.

WHAT A STALE READING PROVES
---------------------------
Nothing. If the newest reading predates the current window's start, it describes
a balance that has since been reset and cannot show credits are available. The
state is reported as ``PROVIDER_BALANCE_UNKNOWN`` and the budget falls back to
AISP's own ledger arithmetic, which is a *lower bound* on spend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ProviderRequestLedger

PROVIDER = "THE_ODDS_API"

#: Endpoints that are documented by the provider as costing no usage credit and
#: are recorded with quota_cost 0 in our own ledger. They are excluded from
#: spend so a balance check can never eat the budget it is protecting.
ZERO_COST_ENDPOINTS = frozenset({"quota_recovery_probe"})

BALANCE_MEASURED = "PROVIDER_BALANCE_MEASURED"
BALANCE_UNKNOWN = "PROVIDER_BALANCE_UNKNOWN"

MODE_DISABLED = "DISABLED"
MODE_CHECKPOINT = "EVENT_CHECKPOINT"
MODE_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
MODE_FLOOR_LATCHED = "PROVIDER_FLOOR_LATCHED"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def billing_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The current UTC calendar month as [start, next_start)."""

    now = _aware(now) or datetime.now(UTC)
    now = now.astimezone(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    following = (start + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, following


@dataclass(frozen=True)
class BudgetState:
    """Everything the operator and the gate need, from persisted rows only."""

    window_start: datetime
    window_end: datetime
    monthly_cap: int
    reserve: int
    #: Credits AISP can prove it has spent in this window.
    ledger_spent: int
    #: The provider's own used/remaining, only when the reading is inside this window.
    provider_used: int | None
    provider_remaining: int | None
    provider_reading_at: datetime | None
    balance_state: str
    daily_cap: int
    daily_spent: int
    requests_this_window: int
    floor: int
    #: True when the newest in-window reading sits at or below the floor.
    floor_latched: bool

    @property
    def spent(self) -> int:
        """The most pessimistic defensible spend figure.

        The provider's own counter is authoritative when it is in-window; our
        ledger is used when it is higher (a request we reserved that the
        provider has not yet counted) or when there is no in-window reading.
        """

        if self.provider_used is None:
            return self.ledger_spent
        return max(self.provider_used, self.ledger_spent)

    @property
    def budgeted_remaining(self) -> int:
        """Credits AISP will allow itself to spend before the reserve."""

        return max(0, self.monthly_cap - self.reserve - self.spent)

    @property
    def daily_remaining(self) -> int:
        return max(0, self.daily_cap - self.daily_spent)

    @property
    def reserve_intact(self) -> bool:
        return self.spent <= self.monthly_cap - self.reserve

    def may_spend(self, credits: int) -> tuple[bool, str | None]:
        """Fail-closed budget check. The provider floor is enforced elsewhere."""

        if credits <= 0:
            return False, "A request must declare a positive credit cost"
        if self.floor_latched:
            return False, (
                f"Provider balance {self.provider_remaining} is at or below the "
                f"floor of {self.floor}"
            )
        if credits > self.daily_remaining:
            return False, (
                f"Daily budget exhausted: {self.daily_spent}/{self.daily_cap} credits used "
                f"today, {credits} required"
            )
        if credits > self.budgeted_remaining:
            return False, (
                f"Monthly budget exhausted: {self.spent}/{self.monthly_cap} credits used this "
                f"billing month, reserve of {self.reserve} is not spendable, {credits} required"
            )
        return True, None

    def projected_exhaustion(self, *, planned_daily_credits: float | None = None) -> dict[str, Any]:
        """When the budget runs out at the observed or planned burn rate."""

        elapsed = max(
            1.0,
            (min(datetime.now(UTC), self.window_end) - self.window_start).total_seconds() / 86400.0,
        )
        observed_rate = self.spent / elapsed
        rate = planned_daily_credits if planned_daily_credits is not None else observed_rate
        days_in_window = (self.window_end - self.window_start).days
        if rate <= 0:
            return {
                "rate_credits_per_day": round(rate, 2),
                "observed_rate_credits_per_day": round(observed_rate, 2),
                "exhaustion_date": None,
                "survives_the_month": True,
                "note": "No spend recorded at this rate; the budget is not being consumed.",
            }
        days_left = self.budgeted_remaining / rate
        exhaustion = datetime.now(UTC) + timedelta(days=days_left)
        survives = exhaustion >= self.window_end
        return {
            "rate_credits_per_day": round(rate, 2),
            "observed_rate_credits_per_day": round(observed_rate, 2),
            "days_remaining": round(days_left, 1),
            "exhaustion_date": exhaustion.date().isoformat(),
            "window_ends": self.window_end.date().isoformat(),
            "survives_the_month": survives,
            "note": (
                f"At {rate:.1f} credits/day the budgeted {self.budgeted_remaining} remaining "
                f"credits last {days_left:.1f} days; the billing month has "
                f"{days_in_window} days."
            ),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": PROVIDER,
            "billing_window_start_utc": self.window_start.isoformat(),
            "billing_window_end_utc": self.window_end.isoformat(),
            "billing_window_basis": "UTC calendar month (measured from the 2026-09-01T00:00Z counter reset)",
            "monthly_cap": self.monthly_cap,
            "reserve": self.reserve,
            "reserve_intact": self.reserve_intact,
            "spent": self.spent,
            "ledger_spent": self.ledger_spent,
            "budgeted_remaining": self.budgeted_remaining,
            "provider_used": self.provider_used,
            "provider_remaining": self.provider_remaining,
            "provider_reading_at_utc": (
                self.provider_reading_at.isoformat() if self.provider_reading_at else None
            ),
            "balance_state": self.balance_state,
            "balance_note": (
                "The provider's own counter, read inside this billing window."
                if self.balance_state == BALANCE_MEASURED
                else "No provider reading inside this billing window. A reading from a "
                     "previous window describes a balance that has since reset and cannot "
                     "show credits are available; AISP's own ledger is used instead and is "
                     "only a lower bound on spend."
            ),
            "daily_cap": self.daily_cap,
            "daily_spent": self.daily_spent,
            "daily_remaining": self.daily_remaining,
            "requests_this_window": self.requests_this_window,
            "floor": self.floor,
            "floor_latched": self.floor_latched,
        }


def _policy_cost(row: ProviderRequestLedger) -> int:
    """What a ledger row cost, measured where possible and estimated otherwise."""

    if row.endpoint_key in ZERO_COST_ENDPOINTS:
        return 0
    if row.quota_cost is not None:
        return int(row.quota_cost)
    shape = (row.response_metadata or {}).get("request_shape") or {}
    markets = shape.get("markets") or []
    regions = shape.get("regions")
    region_count = len([r for r in str(regions).split(",") if r.strip()]) if regions else 1
    if markets:
        return max(1, len(markets) * max(1, region_count))
    # Unknown shape: assume the historical worst case rather than free.
    return 3


def budget_state(
    session: Session,
    *,
    monthly_cap: int,
    reserve: int,
    daily_cap: int,
    floor: int,
    now: datetime | None = None,
    display_timezone: str = "America/New_York",
) -> BudgetState:
    """Compute the current budget position from the provider request ledger."""

    now = _aware(now) or datetime.now(UTC)
    window_start, window_end = billing_window(now)

    rows = list(session.scalars(
        select(ProviderRequestLedger).where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.requested_at >= window_start,
            ProviderRequestLedger.requested_at < window_end,
        ).order_by(ProviderRequestLedger.requested_at)
    ))

    # One logical request appears as a RESERVED row and, on completion, a second
    # row carrying the outcome. Group by reservation so a completed request is
    # counted once, at its measured cost.
    by_reservation: dict[Any, list[ProviderRequestLedger]] = {}
    for row in rows:
        key = row.reservation_id or row.id
        by_reservation.setdefault(key, []).append(row)

    ledger_spent = 0
    requests = 0
    for group in by_reservation.values():
        completed = [r for r in group if r.status in ("SUCCEEDED", "FAILED")]
        chosen = completed[-1] if completed else group[-1]
        if chosen.endpoint_key in ZERO_COST_ENDPOINTS:
            continue
        requests += 1
        ledger_spent += _policy_cost(chosen)

    reading = next(
        (
            r for r in reversed(rows)
            if r.status == "SUCCEEDED" and r.quota_remaining is not None
        ),
        None,
    )
    provider_used = None
    provider_remaining = None
    reading_at = None
    balance_state = BALANCE_UNKNOWN
    if reading is not None:
        provider_remaining = int(reading.quota_remaining)
        used = (reading.response_metadata or {}).get("requests_used")
        provider_used = int(used) if used is not None else monthly_cap - provider_remaining
        reading_at = _aware(reading.completed_at or reading.requested_at)
        balance_state = BALANCE_MEASURED

    from app.core.time import local_day_bounds, local_today

    day = local_today(display_timezone, now)
    day_start, day_end = local_day_bounds(day, display_timezone)
    daily_spent = 0
    for group in by_reservation.values():
        anchor = _aware(group[0].requested_at)
        if anchor is None or not (day_start <= anchor < day_end):
            continue
        completed = [r for r in group if r.status in ("SUCCEEDED", "FAILED")]
        chosen = completed[-1] if completed else group[-1]
        daily_spent += _policy_cost(chosen)

    return BudgetState(
        window_start=window_start,
        window_end=window_end,
        monthly_cap=monthly_cap,
        reserve=reserve,
        ledger_spent=ledger_spent,
        provider_used=provider_used,
        provider_remaining=provider_remaining,
        provider_reading_at=reading_at,
        balance_state=balance_state,
        daily_cap=daily_cap,
        daily_spent=daily_spent,
        requests_this_window=requests,
        floor=floor,
        floor_latched=provider_remaining is not None and provider_remaining <= floor,
    )


def collection_mode(*, polling_enabled: bool, state: BudgetState) -> str:
    if not polling_enabled:
        return MODE_DISABLED
    if state.floor_latched:
        return MODE_FLOOR_LATCHED
    if state.budgeted_remaining <= 0 or state.daily_remaining <= 0:
        return MODE_BUDGET_EXHAUSTED
    return MODE_CHECKPOINT


def spend_by_day(session: Session, *, since: date) -> list[dict[str, Any]]:
    """Per-day credit spend, for the operator's burn chart and the audit."""

    rows = session.execute(
        select(
            func.date_trunc("day", ProviderRequestLedger.requested_at).label("day"),
            ProviderRequestLedger.endpoint_key,
            ProviderRequestLedger.status,
            func.count().label("requests"),
            func.coalesce(func.sum(ProviderRequestLedger.quota_cost), 0).label("credits"),
        )
        .where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.requested_at >= datetime(since.year, since.month, since.day, tzinfo=UTC),
        )
        .group_by("day", ProviderRequestLedger.endpoint_key, ProviderRequestLedger.status)
        .order_by("day")
    ).all()
    return [
        {
            "day": row.day.date().isoformat(),
            "endpoint_key": row.endpoint_key,
            "status": row.status,
            "requests": int(row.requests),
            "credits": int(row.credits or 0),
        }
        for row in rows
    ]


__all__ = [
    "BALANCE_MEASURED",
    "BALANCE_UNKNOWN",
    "MODE_BUDGET_EXHAUSTED",
    "MODE_CHECKPOINT",
    "MODE_DISABLED",
    "MODE_FLOOR_LATCHED",
    "PROVIDER",
    "ZERO_COST_ENDPOINTS",
    "BudgetState",
    "billing_window",
    "budget_state",
    "collection_mode",
    "spend_by_day",
]
