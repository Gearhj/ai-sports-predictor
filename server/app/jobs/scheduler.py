from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import Settings
from app.core.logging import logger
from app.core.time import local_today
from app.db.session import SessionLocal
from app.jobs.orchestrator import JobOrchestrator
from app.services.game_refresh_state_service import GameRefreshStateService


async def execute_scheduled_job(job_name: str, settings: Settings) -> None:
    with SessionLocal() as session:
        try:
            await JobOrchestrator(session, settings).run(
                job_name,
                event_date=local_today(settings.display_timezone),
            )
        except Exception as exc:
            logger.error("scheduled_job_failed", job=job_name, error=str(exc))


def select_event_relative_refresh_states(settings: Settings) -> None:
    """Persist due lifecycle selections without calling a provider or job."""

    if not (settings.scheduler_enabled and settings.event_relative_scheduler_enabled):
        return
    with SessionLocal() as session:
        try:
            states = GameRefreshStateService(session).record_due_states()
            session.commit()
            logger.info("event_relative_states_selected", records=len(states))
        except Exception as exc:
            session.rollback()
            logger.error("event_relative_state_selection_failed", error=str(exc))


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
    if settings.scheduler_enabled and settings.event_relative_scheduler_enabled:
        scheduler.add_job(
            select_event_relative_refresh_states,
            "interval",
            minutes=1,
            id="asp-event-relative-selector",
            kwargs={"settings": settings},
            **common,
        )
    if settings.live_shadow_enabled:
        # Armed live shadow operation. The poll job runs at the fast base cadence
        # and internally enforces the per-game fast/slow window, so it never polls
        # a started game and never exceeds the intended cadence.
        scheduler.add_job(
            execute_scheduled_job,
            "interval",
            minutes=settings.availability_poll_fast_minutes,
            id="asp-availability-poll",
            kwargs={"job_name": "poll-availability-events", "settings": settings},
            **common,
        )
        if settings.the_odds_api_key:
            scheduler.add_job(
                execute_scheduled_job,
                "interval",
                minutes=settings.odds_pregame_refresh_minutes,
                id="asp-live-odds-refresh",
                kwargs={"job_name": "refresh-odds", "settings": settings},
                **common,
            )
    return scheduler
