"""The show-production clock and the Show Board's target slate date.

Every step is a local wall-clock time on the day BEFORE the slate, in the
configured timezone, so DST changes move the UTC instants and never the show.
These are the owner's intended times; while the Show Preview switches are off
nothing is registered from them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings

#: Leagues the Show Board covers. College Football has no registered schedule
#: source in AISP; it appears only through a preview forecast that names a game.
SHOW_SPORTS = ("MLB", "NFL", "NBA", "CFB")


@dataclass(frozen=True)
class ClockStep:
    key: str
    label: str
    local_time: str
    provider_calls: bool
    implemented: bool
    note: str


def steps(settings: Settings) -> list[ClockStep]:
    return [
        ClockStep("SCHEDULE_SYNC", "Synchronize tomorrow's schedule", settings.show_preview_schedule_sync_time,
                  False, False, "Uses the existing schedule sources; no preview-specific job exists in V1."),
        ClockStep("FORECAST_IMPORT", "Import or reuse tomorrow's preview forecasts", settings.show_preview_forecast_import_time,
                  False, True, "Imports only AISP_SHOW_PREVIEW_FORECAST_V1 packages. A final forecast is never relabelled."),
        ClockStep("MARKET_COLLECTION", "Collect available next-day sportsbook markets", settings.show_preview_market_collection_time,
                  True, False, "Not implemented in V1 and refused by name. Requires the owner's provider plan and approval."),
        ClockStep("BUILD", "Build preliminary comparisons and rankings", settings.show_preview_build_time,
                  False, True, "Read-only over persisted forecasts and quotes."),
        ClockStep("FREEZE", "Freeze the immutable Show Board", settings.show_preview_freeze_time,
                  False, True, "Content-addressed snapshot; only when auto-freeze is enabled or an administrator freezes."),
        ClockStep("RECORDING", "James and Scott record", settings.show_preview_recording_time,
                  False, False, "Human step."),
    ]


def _at(slate_date: date, hhmm: str, tz: ZoneInfo) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime.combine(slate_date - timedelta(days=1), time(hour, minute), tz).astimezone(UTC)


def planned_freeze_utc(slate_date: date, settings: Settings) -> datetime:
    return _at(slate_date, settings.show_preview_freeze_time, ZoneInfo(settings.show_preview_timezone))


def clock_for(slate_date: date, settings: Settings) -> list[dict[str, Any]]:
    tz = ZoneInfo(settings.show_preview_timezone)
    out = [{
        "key": step.key, "label": step.label, "local_time": step.local_time, "timezone": settings.show_preview_timezone,
        "at_utc": _at(slate_date, step.local_time, tz).isoformat(), "provider_calls": step.provider_calls,
        "implemented": step.implemented, "note": step.note,
    } for step in steps(settings)]
    out.append({"key": "GAME_DAY", "label": "Existing final forecast, market and decision workflow", "local_time": None,
                "timezone": settings.show_preview_timezone, "at_utc": None, "provider_calls": True, "implemented": True,
                "note": "Unchanged final-pregame process (T-30 to T-15 decision window)."})
    out.append({"key": "AFTER_GAME", "label": "Existing settlement and preview-versus-final comparison", "local_time": None,
                "timezone": settings.show_preview_timezone, "at_utc": None, "provider_calls": False, "implemented": True,
                "note": "Settlement unchanged; the comparison is read-only."})
    return out


def local_today(now: datetime, settings: Settings) -> date:
    return now.astimezone(ZoneInfo(settings.show_preview_timezone)).date()


def next_relevant_slate_date(session: Session, now: datetime, settings: Settings, horizon_days: int = 14) -> date:
    """The next local date after today that has a scheduled game in a Show sport.

    The show is recorded the day before, so today's slate is never the target.
    When nothing is registered in the horizon, tomorrow is returned and the
    board says so rather than pretending a slate exists.
    """

    tz = ZoneInfo(settings.show_preview_timezone)
    tomorrow = local_today(now, settings) + timedelta(days=1)
    lo = datetime.combine(tomorrow, time.min, tz)
    hi = lo + timedelta(days=horizon_days)
    first = session.execute(text("""
        select min(g.starts_at) from games g join leagues l on l.id = g.league_id
        where g.starts_at >= :lo and g.starts_at < :hi and l.code in ('MLB', 'NFL', 'NBA', 'CFB')"""),
        {"lo": lo.astimezone(UTC), "hi": hi.astimezone(UTC)}).scalar()
    if first is None:
        return tomorrow
    if isinstance(first, str):  # SQLite returns aggregates of datetimes as text
        first = datetime.fromisoformat(first)
    moment = first if first.tzinfo else first.replace(tzinfo=UTC)
    return moment.astimezone(tz).date()
