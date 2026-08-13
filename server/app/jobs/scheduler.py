from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import Settings
from app.core.logging import logger
from app.core.time import local_today
from app.db.session import SessionLocal
from app.jobs.orchestrator import JobOrchestrator


async def execute_scheduled_job(job_name: str, settings: Settings) -> None:
    with SessionLocal() as session:
        try:
            await JobOrchestrator(session, settings).run(
                job_name,
                event_date=local_today(settings.display_timezone),
            )
        except Exception as exc:
            logger.error("scheduled_job_failed", job=job_name, error=str(exc))


def build_scheduler(settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.display_timezone)
    common: dict[str, Any] = {
        "max_instances": 1,
        "coalesce": True,
        "misfire_grace_time": 900,
        "replace_existing": True,
    }
    scheduler.add_job(
        execute_scheduled_job,
        "cron",
        hour=6,
        minute=0,
        id="asp-daily-pipeline",
        kwargs={"job_name": "daily-pipeline", "settings": settings},
        **common,
    )
    scheduler.add_job(
        execute_scheduled_job,
        "interval",
        minutes=30,
        id="asp-finalize-games",
        kwargs={"job_name": "finalize-games", "settings": settings},
        **common,
    )
    if settings.odds_polling_enabled:
        scheduler.add_job(
            execute_scheduled_job,
            "interval",
            minutes=settings.odds_poll_interval_minutes,
            id="asp-odds-poll",
            kwargs={"job_name": "refresh-odds", "settings": settings},
            next_run_time=datetime.now(UTC),
            **common,
        )
    return scheduler
