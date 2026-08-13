from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo


def local_today(timezone_name: str, now: datetime | None = None) -> date:
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(ZoneInfo(timezone_name)).date()


def local_day_bounds(event_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    start_local = datetime.combine(event_date, time.min, tzinfo=zone)
    end_local = datetime.combine(event_date.fromordinal(event_date.toordinal() + 1), time.min, tzinfo=zone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)
