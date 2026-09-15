"""Show Preview scheduling: registered only when the owner switches it on.

``register_show_preview_jobs`` adds exactly one job -- the 4:30 PM ET freeze --
and only when BOTH ``show_preview_enabled`` and
``show_preview_auto_freeze_enabled`` are true. No collection job is ever
registered in V1. The freeze reads persisted evidence and writes a snapshot; it
calls no provider and generates no forecast.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from app.core.config import Settings

logger = structlog.get_logger(__name__)
AUTO_FREEZE_JOB_ID = "asp-show-preview-auto-freeze"


def auto_freeze_registration_allowed(settings: Settings) -> bool:
    return bool(settings.show_preview_enabled) and bool(settings.show_preview_auto_freeze_enabled)


def register_show_preview_jobs(scheduler: Any, settings: Settings, common: dict[str, Any]) -> list[str]:
    if not auto_freeze_registration_allowed(settings):
        return []
    hour, minute = (int(part) for part in settings.show_preview_freeze_time.split(":"))
    scheduler.add_job(
        execute_show_preview_auto_freeze, "cron", hour=hour, minute=minute,
        timezone=ZoneInfo(settings.show_preview_timezone), id=AUTO_FREEZE_JOB_ID,
        kwargs={"settings": settings}, **common,
    )
    return [AUTO_FREEZE_JOB_ID]


def run_show_preview_auto_freeze(session: Any, settings: Settings, *, now: datetime | None = None) -> dict[str, Any]:
    from app.show_preview.clock import next_relevant_slate_date
    from app.show_preview.freeze import SnapshotAlreadyFrozen, freeze_show_board

    if not auto_freeze_registration_allowed(settings):
        return {"frozen": False, "reason": "SHOW_PREVIEW_AUTO_FREEZE_DISABLED"}
    moment = now or datetime.now(UTC)
    slate = next_relevant_slate_date(session, moment, settings)
    try:
        result = freeze_show_board(session, slate, settings, now=moment, trigger="AUTO_FREEZE")
    except SnapshotAlreadyFrozen as error:
        return {"frozen": False, "reason": "ALREADY_FROZEN", "detail": str(error), "slate_date": slate.isoformat()}
    session.commit()
    return {"frozen": result.created, "slate_date": slate.isoformat(), "snapshot_sha256": result.snapshot.snapshot_sha256}


async def execute_show_preview_auto_freeze(settings: Settings) -> None:
    from app.db.session import SessionLocal
    from app.jobs.scheduler import is_current_scheduler_owner

    if not is_current_scheduler_owner() or not auto_freeze_registration_allowed(settings):
        return
    with SessionLocal() as session:
        try:
            logger.info("show_preview_auto_freeze", **run_show_preview_auto_freeze(session, settings))
        except Exception as error:  # one attempt per day; logged, never retried in a loop
            session.rollback()
            logger.error("show_preview_auto_freeze_failed", error=str(error))
