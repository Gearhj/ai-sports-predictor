from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import Settings
from app.core.logging import logger
from app.core.time import local_day_bounds, local_today
from app.db.session import SessionLocal
from app.jobs.orchestrator import JobOrchestrator
from app.models.entities import (
    Game, GameStatus, JobRun, RunStatus, ShadowBoardDecision, ShadowDecisionSettlement,
    ShadowForecast, ShadowForecastBundle,
)
from sqlalchemy import select
from app.services.game_refresh_state_service import GameRefreshStateService

# Local (display-timezone) hour at which the daily pipeline is scheduled.
DAILY_PIPELINE_HOUR = 6
DAILY_PIPELINE_JOB = "daily-pipeline"


def daily_pipeline_catch_up_time(
    settings: Settings,
    *,
    now: datetime | None = None,
    session_factory: Any | None = None,
) -> datetime | None:
    """Return an immediate first-run time for the daily pipeline when today's
    scheduled run was missed, else ``None`` (normal cron cadence).

    Observed failure this repairs: the backend was down across 06:00 local, the
    cron fire fell outside APScheduler's 15-minute misfire grace, and the day
    then had NO schedule, NO forecasts and an empty Today surface — a
    dead-scheduler zero-slate day that is indistinguishable from an honest
    no-bet day. Catch-up runs the very same idempotent job the cron would have
    run, exactly once, only when (a) the scheduler is enabled, (b) local time
    is already past the scheduled hour, and (c) no SUCCEEDED ``daily-pipeline``
    run exists for the current local date. Any bookkeeping failure fails safe
    to the normal cron (never a duplicate).
    """

    if not settings.scheduler_enabled:
        return None
    now = now or datetime.now(UTC)
    zone = ZoneInfo(settings.display_timezone)
    local_now = now.astimezone(zone)
    if local_now.hour < DAILY_PIPELINE_HOUR:
        return None
    today = local_now.date()
    factory = session_factory or SessionLocal
    try:
        with factory() as session:
            start_utc, end_utc = local_day_bounds(today, settings.display_timezone)
            succeeded = session.scalar(
                select(JobRun.id).where(
                    JobRun.job_name == DAILY_PIPELINE_JOB,
                    JobRun.status == RunStatus.SUCCEEDED,
                    JobRun.started_at >= start_utc,
                    JobRun.started_at < end_utc,
                ).limit(1)
            )
    except Exception as exc:  # fail safe: fall back to the normal cron
        logger.warning("daily_pipeline_catch_up_check_failed", error=str(exc))
        return None
    if succeeded is not None:
        return None
    logger.warning("daily_pipeline_catch_up_scheduled", event_date=today.isoformat())
    return now


def reconcile_abandoned_job_runs(
    *,
    now: datetime | None = None,
    session_factory: Any | None = None,
) -> int:
    """Mark ``JobRun`` rows left RUNNING by a previous process as FAILED.

    A job row only reaches a terminal status through the orchestrator's own
    try/except; a process killed mid-job leaves it RUNNING forever (four such
    rows were observed in the live table). In the single-process topology this
    runs in, any RUNNING row present at scheduler start is provably orphaned.
    NOTE: a multi-replica deployment must NOT call this unconditionally — it
    needs a per-process/lease identity on the row first.
    """

    now = now or datetime.now(UTC)
    factory = session_factory or SessionLocal
    try:
        with factory() as session:
            rows = list(session.scalars(select(JobRun).where(JobRun.status == RunStatus.RUNNING)))
            for run in rows:
                run.status = RunStatus.FAILED
                run.completed_at = now
                run.error_message = "ABANDONED: process restarted before the job completed"
            session.commit()
    except Exception as exc:
        logger.error("job_run_reconciliation_failed", error=str(exc))
        return 0
    if rows:
        logger.warning("job_runs_reconciled", abandoned=len(rows))
    return len(rows)


# -- runtime state ---------------------------------------------------------
# Process-local truth for "is the authoritative scheduler actually running?".
# Set by the application lifespan; read by the public status surfaces so the UI
# only claims SCHEDULER RUNNING when this process really started it. The live
# scheduler instance is kept so status surfaces report the REAL next-run time
# of each registered job — never a stale last-run-plus-cadence projection that
# lands in the past after a process restart.
_SCHEDULER_RUNNING = False
_SCHEDULER_INSTANCE: AsyncIOScheduler | None = None
_SCHEDULER_STARTED_AT: datetime | None = None


def mark_scheduler_started(scheduler: AsyncIOScheduler | None = None) -> None:
    global _SCHEDULER_RUNNING, _SCHEDULER_INSTANCE, _SCHEDULER_STARTED_AT
    _SCHEDULER_RUNNING = True
    _SCHEDULER_INSTANCE = scheduler
    _SCHEDULER_STARTED_AT = datetime.now(UTC)


def mark_scheduler_stopped() -> None:
    global _SCHEDULER_RUNNING, _SCHEDULER_INSTANCE, _SCHEDULER_STARTED_AT
    _SCHEDULER_RUNNING = False
    _SCHEDULER_INSTANCE = None
    _SCHEDULER_STARTED_AT = None


def scheduler_is_running() -> bool:
    return _SCHEDULER_RUNNING


def scheduler_started_at() -> datetime | None:
    return _SCHEDULER_STARTED_AT


def scheduler_next_run_time(job_id: str) -> datetime | None:
    """The registered job's actual next fire time, or None when unknowable."""
    if not _SCHEDULER_RUNNING or _SCHEDULER_INSTANCE is None:
        return None
    try:
        job = _SCHEDULER_INSTANCE.get_job(job_id)
    except Exception:
        return None
    return getattr(job, "next_run_time", None) if job is not None else None


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


PM_FINALIZE_WINDOW_MINUTES = 20


def pm_pregame_capture_action(session, now: datetime) -> str | None:
    """Which pregame job the 5-minute tick should run, from persisted state only.

    * A mapped game starts within PM_FINALIZE_WINDOW_MINUTES -> the full
      finalize job (final pregame snapshot + refresh + consensus).
    * Otherwise a mapped game starts within the widest scheduled cut offset
      (T-6h) -> the bounded, idempotent cut capture only, so the T-6h / T-2h /
      T-30m / T-5m milestones can actually be captured by the scheduler.
    * Otherwise None: no provider request is spent.
    """
    from app.models import PredictionMarketMapping
    from app.prediction_markets.contracts import TIMELINE_CUT_OFFSETS_MINUTES
    from app.prediction_markets.jobs import PREGAME_CAPTURE_CUTS

    widest = max(TIMELINE_CUT_OFFSETS_MINUTES[c] for c in PREGAME_CAPTURE_CUTS)

    def mapped_game_within(minutes: int):
        return session.scalar(
            select(PredictionMarketMapping.id)
            .join(Game, Game.id == PredictionMarketMapping.canonical_game_id)
            .where(
                PredictionMarketMapping.mapping_status.in_(("EXACT", "MANUAL_APPROVED")),
                Game.starts_at > now,
                Game.starts_at <= now + timedelta(minutes=minutes),
            )
            .limit(1)
        )

    if mapped_game_within(PM_FINALIZE_WINDOW_MINUTES) is not None:
        return "finalize-prediction-market-pregame"
    if mapped_game_within(widest) is not None:
        return "capture-prediction-market-pregame-cuts"
    return None


async def execute_pm_finalize_pregame(settings: Settings) -> None:
    """Five-minute pregame tick: finalize when a mapped game is about to start,
    capture due scheduled cuts when one is within the widest cut window,
    otherwise skip without spending provider requests."""

    now = datetime.now(UTC)
    with SessionLocal() as session:
        action = pm_pregame_capture_action(session, now)
    if action is None:
        logger.info("pm_finalize_pregame_skipped", reason="no_mapped_game_in_window")
        return
    await execute_scheduled_job(action, settings)


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
    # A missed 06:00 run (process down across the cron fire) is caught up once,
    # immediately, on scheduler start; the cron cadence resumes afterwards.
    daily_kwargs: dict[str, Any] = dict(common)
    catch_up_at = daily_pipeline_catch_up_time(settings)
    if catch_up_at is not None:
        daily_kwargs["next_run_time"] = catch_up_at
    if settings.model_exchange_auto_import_enabled:
        # Recurring consumption of COMPLETED local research artifacts (verified
        # forecast bundles, research supplements, benchmark studies). Never a
        # provider request and never a model execution.
        scheduler.add_job(
            execute_scheduled_job, "interval",
            minutes=settings.model_exchange_import_interval_minutes,
            id="asp-model-exchange-consumer",
            kwargs={"job_name": "consume-model-exchange-research-artifacts", "settings": settings},
            next_run_time=datetime.now(UTC),
            **common,
        )
    if not settings.scheduler_core_jobs_enabled:
        return scheduler
    scheduler.add_job(
        execute_scheduled_job,
        "cron",
        hour=DAILY_PIPELINE_HOUR,
        minute=0,
        id="asp-daily-pipeline",
        kwargs={"job_name": DAILY_PIPELINE_JOB, "settings": settings},
        **daily_kwargs,
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
    if settings.odds_checkpoint_collection_enabled and settings.owns_odds_collection:
        # Event-aware collection. The tick is cheap and frequent; the SPEND is
        # rare, because the job itself decides from persisted rows whether a
        # checkpoint is due. A 5-minute tick bounds how late a checkpoint can be
        # served without ever making a request that is not due.
        scheduler.add_job(
            execute_scheduled_job,
            "interval",
            minutes=5,
            id="asp-odds-checkpoint",
            kwargs={"job_name": "collect-odds-checkpoint", "settings": settings},
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
        # SUPERSEDED. This is the ten-minute three-market poller that consumed
        # 495 of September's 500 credits in four days while consulting no
        # budget at all. It is now off unless explicitly re-enabled, and even
        # then only in the process that owns collection -- so it can never
        # again be armed as a side effect of live shadow operation.
        if (settings.odds_legacy_pregame_refresh_enabled
                and settings.owns_odds_collection
                and settings.the_odds_api_key):
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
        # Research scoring runs on the same hour as finalization but offset, so
        # it reads the finals that pass already persisted rather than racing it.
        # It spends no quota and creates no decision, so it is not bound to the
        # odds-collection owner -- only to this single scheduler process.
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=60, jitter=None,
            next_run_time=datetime.now(UTC) + timedelta(minutes=6),
            id="asp-nfl-research-evaluate",
            kwargs={"job_name": "evaluate-nfl-research-forecasts", "settings": settings},
            **common,
        )
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=60,
            id="asp-nfl-shadow-performance",
            kwargs={"job_name": "recalculate-nfl-shadow-performance", "settings": settings},
            **common,
        )
        # NFL odds are optional research and must never outrank required MLB
        # moneylines, so this legacy poller is bound to the same single owner.
        # The event-aware collector serves NFL at priority 2 from the
        # NFL_RESEARCH lane instead.
        if (settings.nfl_shadow_odds_enabled
                and settings.owns_odds_collection
                and settings.the_odds_api_key):
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
        scheduler.add_job(
            execute_pm_finalize_pregame, "interval", minutes=5,
            id="asp-pm-finalize-pregame", kwargs={"settings": settings}, **common,
        )
    return scheduler
