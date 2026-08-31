from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import Settings
from app.core.logging import logger
from app.core.time import local_today
from app.db.session import SessionLocal
from app.jobs.orchestrator import JobOrchestrator
from app.models.entities import (
    Game, GameStatus, ShadowBoardDecision, ShadowDecisionSettlement,
    ShadowForecast, ShadowForecastBundle,
)
from sqlalchemy import select
from app.services.game_refresh_state_service import GameRefreshStateService


def _scheduled_event_dates(
    job_name: str,
    orchestrator: JobOrchestrator,
    settings: Settings,
) -> tuple[date, ...]:
    """Carry an unfinished prior local slate through the midnight boundary.

    Only the quota-free finalization pipeline receives the carryover date. Other
    scheduled jobs remain bound to the current local date, and a prior slate
    containing only terminal games does not trigger another provider refresh.
    """

    today = local_today(settings.display_timezone)
    if job_name == "finalize-nfl-shadow-games":
        now = datetime.now(UTC)
        candidate_dates = set(orchestrator.session.scalars(
            select(ShadowForecast.event_date)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .join(Game, Game.id == ShadowForecast.canonical_game_id)
            .where(
                ShadowForecastBundle.sport == "NFL",
                Game.status != GameStatus.FINAL,
                Game.starts_at <= now,
                ShadowForecast.event_date >= today - timedelta(days=3),
                ShadowForecast.event_date <= today,
            )
            .distinct()
        ))
        return tuple(sorted(candidate_dates))
    if job_name == "settle-nfl-shadow-decisions":
        settled_ids = select(ShadowDecisionSettlement.decision_id)
        candidate_dates = set(orchestrator.session.scalars(
            select(ShadowBoardDecision.slate_date)
            .join(ShadowForecast, ShadowForecast.id == ShadowBoardDecision.shadow_forecast_id)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .join(Game, Game.id == ShadowForecast.canonical_game_id)
            .where(
                ShadowForecastBundle.sport == "NFL",
                Game.status == GameStatus.FINAL,
                ShadowBoardDecision.slate_date >= today - timedelta(days=3),
                ShadowBoardDecision.slate_date <= today,
                ShadowBoardDecision.id.not_in(settled_ids),
            )
            .distinct()
        ))
        return tuple(sorted(candidate_dates))
    if job_name != "finalize-games":
        return (today,)
    previous = today - timedelta(days=1)
    previous_incomplete = any(
        game.status in (GameStatus.SCHEDULED, GameStatus.LIVE)
        for game in orchestrator._games_for_date(previous)
    )
    return (previous, today) if previous_incomplete else (today,)


async def execute_scheduled_job(job_name: str, settings: Settings) -> None:
    with SessionLocal() as session:
        orchestrator = JobOrchestrator(session, settings)
        try:
            event_dates = _scheduled_event_dates(job_name, orchestrator, settings)
        except Exception as exc:
            logger.error("scheduled_job_failed", job=job_name, error=str(exc))
            return
        for event_date in event_dates:
            try:
                await orchestrator.run(job_name, event_date=event_date)
            except Exception as exc:
                logger.error(
                    "scheduled_job_failed",
                    job=job_name,
                    event_date=event_date.isoformat(),
                    error=str(exc),
                )


async def execute_pregame_odds_refresh(settings: Settings) -> None:
    """Scheduled odds cadence gate: refresh only when a monitored game is near
    first pitch, so the daily quota is spent in the pregame window rather than
    overnight. Event-triggered and pipeline refreshes bypass this gate."""

    with SessionLocal() as session:
        if not JobOrchestrator(session, settings).odds_pregame_window_open(datetime.now(UTC)):
            logger.info("odds_refresh_skipped", reason="outside_pregame_window")
            return
    await execute_scheduled_job("refresh-odds", settings)


async def execute_nfl_shadow_odds_refresh(settings: Settings) -> None:
    """Spend NFL quota only when a bounded event-relative checkpoint is due."""

    now = datetime.now(UTC)
    with SessionLocal() as session:
        from app.services.nfl_shadow_odds_service import NFLShadowOddsService

        if not NFLShadowOddsService(session).checkpoint_due(now):
            logger.info("nfl_odds_refresh_skipped", reason="no_missing_checkpoint")
            return
    await execute_scheduled_job("refresh-nfl-shadow-odds", settings)


async def execute_nfl_final_pregame_generation(settings: Settings) -> None:
    """Invoke the producer only for an unfrozen game in its 15–30m window."""

    now = datetime.now(UTC)
    with SessionLocal() as session:
        due_events = set(session.scalars(
            select(ShadowForecast.event_id)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(
                ShadowForecastBundle.sport == "NFL",
                ShadowForecast.scheduled_start_utc >= now + timedelta(minutes=15),
                ShadowForecast.scheduled_start_utc <= now + timedelta(minutes=30),
            )
            .distinct()
        ))
        frozen_events = set(session.scalars(
            select(ShadowForecast.event_id)
            .join(ShadowForecastBundle, ShadowForecastBundle.id == ShadowForecast.bundle_id)
            .where(
                ShadowForecastBundle.sport == "NFL",
                ShadowForecast.snapshot_type == "FINAL_PREGAME",
                ShadowForecast.event_id.in_(due_events) if due_events else False,
            )
            .distinct()
        ))
    if not due_events - frozen_events:
        logger.info("nfl_final_pregame_generation_skipped", reason="no_unfrozen_game_in_window")
        return
    await execute_scheduled_job("generate-nfl-final-pregame-forecasts", settings)
    await execute_scheduled_job("freeze-nfl-final-pregame", settings)


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
        # Provider-free, idempotent evidence capture and cutoff finalization.
        # Five-minute cadence ensures the 15-minute decision cutoff is not
        # missed by the coarser 30-minute finalization interval.
        scheduler.add_job(
            execute_scheduled_job,
            "interval",
            minutes=5,
            id="asp-shadow-experiment-freeze",
            kwargs={"job_name": "freeze-shadow-experiment", "settings": settings},
            **common,
        )
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
                execute_pregame_odds_refresh,
                "interval",
                minutes=settings.odds_pregame_refresh_minutes,
                id="asp-live-odds-refresh",
                kwargs={"settings": settings},
                **common,
            )
    if settings.nfl_shadow_enabled:
        # Provider-free import/evaluation/finalization jobs reuse the one app
        # scheduler and remain independently idempotent.
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=30,
            id="asp-nfl-shadow-import",
            kwargs={"job_name": "import-nfl-shadow-forecast-bundle", "settings": settings},
            **common,
        )
        if settings.nfl_shadow_producer_enabled:
            # One daily current-evidence refresh plus an event-gated FINAL pass.
            # Both use the same existing scheduler and hardened exchange.
            scheduler.add_job(
                execute_scheduled_job, "cron", hour=5, minute=30,
                id="asp-nfl-shadow-producer-current",
                kwargs={"job_name": "generate-nfl-shadow-forecasts", "settings": settings},
                **common,
            )
            scheduler.add_job(
                execute_nfl_final_pregame_generation, "interval", minutes=5,
                id="asp-nfl-shadow-producer-final",
                kwargs={"settings": settings},
                **common,
            )
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=30,
            id="asp-nfl-shadow-evaluate",
            kwargs={"job_name": "evaluate-nfl-shadow-board", "settings": settings},
            **common,
        )
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=5,
            id="asp-nfl-shadow-final-pregame",
            kwargs={"job_name": "freeze-nfl-final-pregame", "settings": settings},
            **common,
        )
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=60,
            id="asp-nfl-shadow-finalize",
            kwargs={"job_name": "finalize-nfl-shadow-games", "settings": settings},
            **common,
        )
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=60,
            id="asp-nfl-shadow-settle",
            kwargs={"job_name": "settle-nfl-shadow-decisions", "settings": settings},
            **common,
        )
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=60,
            id="asp-nfl-shadow-performance",
            kwargs={"job_name": "recalculate-nfl-shadow-performance", "settings": settings},
            **common,
        )
        if settings.nfl_shadow_odds_enabled and settings.the_odds_api_key:
            scheduler.add_job(
                execute_nfl_shadow_odds_refresh, "interval",
                minutes=settings.odds_pregame_refresh_minutes,
                id="asp-nfl-shadow-odds",
                kwargs={"settings": settings},
                **common,
            )
    if settings.prediction_market_observer_enabled:
        # Read-only market observation. Discovery + snapshot + consensus + alerts
        # cadences reuse this scheduler; provider access is off unless the
        # provider flags are also armed.
        scheduler.add_job(
            execute_scheduled_job, "interval",
            minutes=settings.prediction_market_discovery_minutes,
            id="asp-pm-discover", kwargs={"job_name": "discover-prediction-markets", "settings": settings}, **common,
        )
        scheduler.add_job(
            execute_scheduled_job, "interval",
            minutes=settings.prediction_market_snapshot_minutes,
            id="asp-pm-refresh", kwargs={"job_name": "refresh-prediction-market-snapshots", "settings": settings}, **common,
        )
        scheduler.add_job(
            execute_scheduled_job, "interval",
            minutes=max(settings.prediction_market_snapshot_minutes, 5),
            id="asp-pm-consensus", kwargs={"job_name": "compute-prediction-market-consensus", "settings": settings}, **common,
        )
        scheduler.add_job(
            execute_scheduled_job, "interval",
            minutes=max(settings.prediction_market_snapshot_minutes, 5),
            id="asp-pm-alerts", kwargs={"job_name": "evaluate-prediction-market-alerts", "settings": settings}, **common,
        )
    return scheduler
