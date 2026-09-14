"""The Eastern service day: the day AISP's budget and its slate both mean.

WHY THIS EXISTS
---------------
The provider's usage counter runs on the UTC calendar day. AISP's slates,
decision cutoffs and operational date all run on the Eastern calendar day.
Those two days are offset by four or five hours, and on 2026-09-14 that offset
cost a whole slate its prices: eighteen NFL objects spent between 22:00 and
23:41 Eastern on 09-13 landed inside the UTC day 09-14, exhausting an MLB
allowance before the 09-14 Eastern slate existed.

So the INTERNAL allocation day is Eastern midnight to Eastern midnight. A
request made at 20:30 Eastern belongs to that Eastern date's budget, not to the
next one.

WHAT THIS DELIBERATELY DOES NOT TOUCH
-------------------------------------
The provider's own monthly cap and reset. Those are the provider's billing
clock, reported by its account endpoint, and AISP reads them exactly as given.
Reinterpreting someone else's billing month is how a budget quietly diverges
from the bill it is supposed to track. Only the internal DAILY partition moves.

BOTH CLOCKS TRAVEL TOGETHER
---------------------------
Every operational receipt carries the Eastern service date AND the UTC
timestamp, because a reader reconciling AISP against the provider's dashboard
needs both and can derive neither from the other without knowing the rule.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

#: Versioned on purpose. A receipt written under the old rule and one written
#: under the new rule are not comparable, and a reader must be able to tell
#: which rule produced the number in front of them.
ALLOCATION_DAY_SEMANTICS = "AISP_ALLOCATION_DAY_V2_EASTERN"

#: What V1 meant, retained so an older receipt can still be read.
PRIOR_SEMANTICS = "AISP_ALLOCATION_DAY_V1_UTC"

SERVICE_TIMEZONE = "America/New_York"
ZONE = ZoneInfo(SERVICE_TIMEZONE)


def service_date(moment: datetime | None = None) -> date:
    """The Eastern calendar date a moment belongs to."""

    instant = moment or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(ZONE).date()


def service_day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC span of one Eastern service day: [start, end)."""

    start_local = datetime.combine(day, datetime.min.time(), tzinfo=ZONE)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def service_day_start(moment: datetime | None = None) -> datetime:
    """The UTC instant the current Eastern service day began."""

    return service_day_bounds(service_date(moment))[0]


def receipt_clocks(moment: datetime | None = None) -> dict[str, Any]:
    """Both clocks, for any operational receipt.

    A receipt that carries only one of these cannot be reconciled against the
    other system without the reader already knowing the conversion rule -- which
    is exactly the knowledge the last failure proved nobody reliably has.
    """

    instant = moment or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    local = instant.astimezone(ZONE)
    start, end = service_day_bounds(local.date())
    return {
        "allocation_day_semantics": ALLOCATION_DAY_SEMANTICS,
        "service_date": local.date().isoformat(),
        "service_timezone": SERVICE_TIMEZONE,
        "service_day_start_utc": start.isoformat(),
        "service_day_end_utc": end.isoformat(),
        "observed_at_utc": instant.isoformat(),
        "observed_at_local": local.isoformat(),
        # Stated rather than implied: the provider's month is its own clock.
        "provider_monthly_clock": "UTC calendar month, as reported by the provider",
    }


__all__ = [
    "ALLOCATION_DAY_SEMANTICS",
    "PRIOR_SEMANTICS",
    "SERVICE_TIMEZONE",
    "ZONE",
    "receipt_clocks",
    "service_date",
    "service_day_bounds",
    "service_day_start",
]
