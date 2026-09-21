from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
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
from app.services.league_scope import MLB

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
        for game in orchestrator._games_for_date(previous, MLB)
    )
    return (previous, today) if previous_incomplete else (today,)


#: Stamped by the supervisor at the launch that owns this process. A survivor
#: of an earlier supervisor keeps its own stale value, which is the whole point.
_OWNER_TOKEN = os.environ.get("ASP_SCHEDULER_INSTANCE", "").strip()
#: Latched once this process has been superseded, so it says so exactly once.
_STOOD_DOWN = False


def _owner_file() -> Path:
    return Path(__file__).resolve().parents[3] / ".runtime" / "scheduler-owner"


def is_current_scheduler_owner() -> bool:
    """True while this process is still the scheduler the supervisor launched.

    The host-wide owner token cannot answer this. Every backend here reads the
    same ``.env``, so a process that outlives its supervisor keeps presenting a
    perfectly valid one -- and a survivor that has lost its port is invisible to
    the supervisor's port check as well. The supervisor therefore stamps a token
    unique to each launch and writes it where the backend can re-read it.

    Fail-OPEN when the file is absent: that means the mechanism is not deployed
    on this host -- a developer running uvicorn by hand, a test -- and refusing
    to run every job would be a worse failure than the one being prevented.
    Fail-CLOSED the moment the file exists and disagrees, because at that point
    a newer supervisor has demonstrably claimed ownership.
    """

    try:
        claimed = _owner_file().read_text(encoding="ascii").strip()
    except OSError:
        return True
    if not claimed:
        return True
    return bool(_OWNER_TOKEN) and _OWNER_TOKEN == claimed


def _stand_down(job_name: str) -> None:
    """Retire this process's scheduler. Announced once, then silent."""

    global _STOOD_DOWN
    if not _STOOD_DOWN:
        _STOOD_DOWN = True
        logger.error(
            "scheduler_superseded",
            job=job_name,
            instance=_OWNER_TOKEN or "<unset>",
            detail=(
                "A newer supervisor owns the scheduler; this process is a "
                "survivor of an earlier launch and is retiring its own jobs."
            ),
        )
    scheduler = _SCHEDULER_INSTANCE
    mark_scheduler_stopped()
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:  # already down, or shutting down
            pass


async def execute_scheduled_job(job_name: str, settings: Settings) -> None:
    # Asked on every tick rather than once at startup: ownership is lost while
    # the process is running, not before it starts.
    if not is_current_scheduler_owner():
        _stand_down(job_name)
        return
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


async def execute_v18_market_pilot_seal(settings: Settings) -> None:
    """Research-export tick: run the sealer only when a packet or a first-seen
    game is due, so an idle window writes no job row. Reads only."""

    if not settings.v18_market_pilot_export_enabled:
        return
    from app.research_exports.v18_market_pilot_seal import JOB_NAME, work_due

    try:
        with SessionLocal() as session:
            due = work_due(session, settings)
    except Exception as exc:
        logger.error("v18_market_pilot_due_check_failed", error=str(exc))
        return
    if not due:
        return
    await execute_scheduled_job(JOB_NAME, settings)


async def execute_v18_market_pilot_outcomes(settings: Settings) -> None:
    """Research-export tick: export admitted outcomes for sealed pilot episodes
    only when a revision, a reason or a stop is due, so an unchanged slate
    writes no job row. Reads only (read-only transaction, rolled back)."""

    if not settings.v18_market_pilot_outcomes_export_enabled:
        return
    from app.research_exports.v18_market_pilot_outcomes import JOB_NAME, work_due

    try:
        with SessionLocal() as session:
            due = work_due(session, settings)
    except Exception as exc:
        logger.error("v18_market_pilot_outcomes_due_check_failed", error=str(exc))
        return
    if not due:
        return
    await execute_scheduled_job(JOB_NAME, settings)


async def execute_v18_market_pilot_outcomes_v2(settings: Settings) -> None:
    """Research-export tick for the V2 (capture-bound) outcome lane: runs the
    export only when a V2 revision, an unresolved reason or a stop is due.
    Reads only (read-only transaction, rolled back)."""

    if not settings.v18_market_pilot_outcomes_v2_enabled:
        return
    from app.research_exports.v18_market_pilot_outcomes_v2 import JOB_NAME, work_due

    try:
        with SessionLocal() as session:
            due = work_due(session, settings)
    except Exception as exc:
        logger.error("v18_market_pilot_outcomes_v2_due_check_failed", error=str(exc))
        return
    if not due:
        return
    await execute_scheduled_job(JOB_NAME, settings)


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


#: Scheduled coroutines run on single-worker LANES, in submission order per lane
#: (see app/jobs/lanes.py: prediction-market observation has its own lane so it
#: cannot hold up forecast imports and decision checkpoints, and the decision
#: checkpoints themselves -- `asp-dc-*` -- have a lane no other job shares, so a
#: long core job can never again spend a fifteen-minute window queued ahead of
#: them the way `generate-nfl-final-pregame-forecasts` did on 2026-09-20).
#:
#: WHY: job bodies are `async def` but do synchronous database, file-hashing and
#: CPU work. Awaited on the web server's event loop they froze HTTP for their
#: whole duration -- on 2026-09-15 a 392-second `finalize-games` run held the
#: NBA research page's HTML response for 116 seconds, and the five-minute
#: exchange consumer blocked every page for 40-60 seconds each cycle. Moving
#: the bodies to a single dedicated thread (with its own event loop) frees the
#: web loop while keeping the jobs as serial as they effectively were: one at
#: a time, first come first served, each still `max_instances=1`. Schedules,
#: ownership and job logic are unchanged.
def _job_executor(lane: str | None = None):
    from app.jobs import lanes

    return lanes.executor(lane or lanes.CORE_LANE)


def off_event_loop(func, lane: str | None = None):
    """Run a scheduled coroutine function on its lane's thread, not the web loop."""

    import asyncio
    import functools
    import inspect

    from app.jobs import lanes

    if not inspect.iscoroutinefunction(func):
        return func
    lane = lane or lanes.CORE_LANE

    @functools.wraps(func)
    async def runner(*args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        fired_at = datetime.now(UTC)
        return await loop.run_in_executor(
            _job_executor(lane),
            lambda: lanes.run_with_meta(lane, fired_at, lambda: asyncio.run(func(*args, **kwargs))),
        )

    runner.__aisp_off_event_loop__ = True  # type: ignore[attr-defined]
    runner.__aisp_job_lane__ = lane  # type: ignore[attr-defined]
    return runner


def build_scheduler(settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.display_timezone)
    _add_job = scheduler.add_job

    def add_job(func, *args: Any, **kwargs: Any):
        from app.jobs.lanes import lane_for

        return _add_job(off_event_loop(func, lane_for(kwargs.get("id"))), *args, **kwargs)

    scheduler.add_job = add_job  # type: ignore[method-assign]
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
    if (settings.model_exchange_auto_import_enabled
            or settings.mlb_v18_exchange_auto_import_enabled):
        # Recurring consumption of COMPLETED local research artifacts (verified
        # forecast bundles, research supplements, benchmark studies). Never a
        # provider request and never a model execution.
        scheduler.add_job(
            execute_scheduled_job, "interval",
            minutes=settings.model_exchange_import_interval_minutes,
            # The `asp-im-` prefix puts this on the `producer-import` lane. It
            # used to share `core` with model execution, and on 2026-09-20 the
            # import due at about 12:31:52 waited a measured 703.56 seconds
            # behind `generate-nfl-final-pregame-forecasts` (799.3 s on core).
            # A FINAL forecast is only usable once IMPORTED, so an import queued
            # behind the job that produces the forecast is the one delay that
            # silently costs a decision. Cadence and behaviour are unchanged --
            # only the thread it runs on.
            id="asp-im-model-exchange-consumer",
            kwargs={"job_name": "consume-model-exchange-research-artifacts", "settings": settings},
            next_run_time=datetime.now(UTC),
            **common,
        )
        # THE NFL FINAL PAIR, ON ITS OWN OWNER AND ITS OWN LANE.
        #
        # The broad pass above commits every sport in one transaction, so an
        # NFL forecast verified in its first stage is invisible to a checkpoint
        # until CFB, NBA, the labs family, the lab study and MLB V18 have all
        # finished. And `max_instances=1` means a long broad pass SKIPS the next
        # fire rather than delaying it -- a 353-second run on 2026-09-21 skipped
        # the following one outright.
        #
        # Against a T-15 edge either is fatal to the last usable import, so this
        # runs every minute on `nfl-final-import`, commits on its own, and
        # imports nothing but the NFL forecast/supplement pair. Idempotent by
        # content address, so it cannot duplicate what the broad pass imports.
        scheduler.add_job(
            execute_scheduled_job, "interval",
            minutes=settings.nfl_final_import_interval_minutes,
            id="asp-nf-nfl-final-pair",
            kwargs={"job_name": "import-nfl-final-pair", "settings": settings},
            next_run_time=datetime.now(UTC) + timedelta(seconds=90),
            **common,
        )
    # AISP DAY-BEFORE SHOW PREVIEW V1: registers the 4:30 PM ET freeze only when
    # both Show Preview switches are on. Never a provider request, and no
    # preview collection job exists to register.
    from app.show_preview.jobs import register_show_preview_jobs
    register_show_preview_jobs(scheduler, settings, common)
    if not settings.scheduler_core_jobs_enabled:
        return scheduler
    # Canonical events for the NEXT slate, registered before anything needs to
    # map to one. First among the core jobs deliberately: V18 publication, odds
    # collection and the coverage funnel can only address a game AISP has
    # registered, and until this existed AISP registered only today's.
    scheduler.add_job(
        execute_scheduled_job, "interval", minutes=180, jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(minutes=2),
        id="asp-mlb-event-bridge",
        kwargs={"job_name": "bridge-mlb-canonical-events", "settings": settings},
        **common,
    )
    # V18 research scoring. Reads the finals the live MLB state observations
    # already persisted and writes research evaluations only -- no provider
    # request, no model execution, no decision, position, settlement or bankroll
    # change, and no model activation. It is therefore not bound to the odds
    # ownership flag, only to this single scheduler process. Thirty minutes
    # matches the settlement cadence a slate finishes on, and the job is a
    # legitimate no-op once every final in range is already scored.
    scheduler.add_job(
        execute_scheduled_job, "interval", minutes=30, jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(minutes=3),
        id="asp-mlb-v18-research-evaluate",
        kwargs={"job_name": "evaluate-mlb-v18-research-forecasts", "settings": settings},
        **common,
    )
    # Season accounts open themselves at their configured boundary. Daily is
    # the right cadence for a once-a-season event: it is frequent enough that a
    # season opens on the day it should, and the job is a no-op on every run
    # before the boundary and on every run after the contribution exists.
    scheduler.add_job(
        execute_scheduled_job, "interval", hours=6, jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(minutes=4),
        id="asp-season-account-opening",
        kwargs={"job_name": "open-season-experiment-accounts", "settings": settings},
        **common,
    )
    # An armed model era opens itself. Five minutes is short enough that the
    # first slate after the boundary is governed by the new engine, and the job
    # is a no-op on every run before the boundary and after the snapshot.
    scheduler.add_job(
        execute_scheduled_job, "interval", minutes=5, jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(minutes=1),
        id="asp-model-era-boundary",
        kwargs={"job_name": "open-model-era-at-boundary", "settings": settings},
        **common,
    )
    scheduler.add_job(
        execute_scheduled_job,
        "cron",
        hour=DAILY_PIPELINE_HOUR,
        minute=0,
        id="asp-daily-pipeline",
        kwargs={"job_name": DAILY_PIPELINE_JOB, "settings": settings},
        **daily_kwargs,
    )
    # Inert threshold research. Reads decisions that already exist and records
    # what other minimums would have selected; creates nothing and changes no
    # threshold, so it is bound only to the single scheduler process.
    scheduler.add_job(
        execute_scheduled_job, "interval", minutes=240, jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(minutes=8),
        id="asp-shadow-threshold-ladder",
        kwargs={"job_name": "record-shadow-threshold-ladder", "settings": settings},
        **common,
    )
    # Settlement's owner. The recurring cadence stays thirty minutes, but
    # without an explicit next_run_time an interval job first fires a full
    # interval after process start, so every restart deferred settlement by up
    # to half an hour -- a game that went final during a deploy sat unsettled
    # and read as an open position. Ninety seconds lets the process finish
    # starting and then settles whatever became final while it was down.
    scheduler.add_job(
        execute_scheduled_job,
        "interval",
        minutes=30,
        next_run_time=datetime.now(UTC) + timedelta(seconds=90),
        id="asp-finalize-games",
        kwargs={"job_name": "finalize-games", "settings": settings},
        **common,
    )
    # Near-live scoreboard. Two minutes is the fastest cadence the free MLB
    # Stats API sustains comfortably and it costs no metered allowance: one
    # request covers the whole slate. The job asks a local question first and
    # returns immediately when nothing is in its watch window, so an empty
    # overnight slate is not seven hundred pointless requests.
    #
    # Display-only. It writes to game_scoreboard_observations and nowhere else,
    # so it cannot touch a final, a settlement, a stake or a bankroll.
    scheduler.add_job(
        execute_scheduled_job,
        "interval",
        minutes=2,
        jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(seconds=30),
        id="asp-scoreboard-refresh",
        kwargs={"job_name": "refresh-scoreboard", "settings": settings},
        **common,
    )
    # The provider's own balance, read hourly. Free -- the endpoint returns
    # account data, not event objects -- and it is what the whole allocation
    # plans against, so a stale reading is the one thing that would make the
    # budget wrong in the expensive direction.
    scheduler.add_job(
        execute_scheduled_job,
        "interval",
        minutes=60,
        jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(seconds=45),
        id="asp-provider-usage",
        kwargs={"job_name": "refresh-provider-usage", "settings": settings},
        **common,
    )
    # Governed finals for completed games that are missing one. This is what
    # the settlement chain was waiting on: it ran only when a person typed a
    # command, so a finished game could sit ungoverned indefinitely and every
    # ticket on it stayed open. Twenty minutes is frequent enough that a game
    # settles within the hour it ends, and the job is a no-op -- no request at
    # all -- on every run where nothing is missing.
    scheduler.add_job(
        execute_scheduled_job,
        "interval",
        minutes=20,
        jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(minutes=3),
        id="asp-governed-final-recovery",
        kwargs={"job_name": "recover-governed-finals", "settings": settings},
        **common,
    )
    # Recorded parlay tickets settle themselves once every leg's game has a
    # governed final. The job existed and was never scheduled, which is why the
    # first recorded ticket sat open with both its games finished: nothing was
    # ever going to call it. It touches no provider and is a no-op when no
    # ticket is open, so thirty minutes costs nothing and matches the cadence
    # the straight-bet ledger settles on.
    scheduler.add_job(
        execute_scheduled_job,
        "interval",
        minutes=30,
        jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(minutes=2),
        id="asp-settle-parlay-tickets",
        kwargs={"job_name": "settle-parlay-tickets", "settings": settings},
        **common,
    )
    # In-game NFL score for tracked games. Fifteen minutes is the fastest
    # cadence the remaining object allowance can sustain for a bounded set of
    # games; the collector itself refuses when a tracked game is outside its
    # watch window, when the daily NFL cap is spent, or when spending would eat
    # the floor held for MLB pregame odds. Most cycles cost nothing at all.
    scheduler.add_job(
        execute_scheduled_job,
        "interval",
        minutes=15,
        jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(minutes=4),
        id="asp-nfl-live-scores",
        kwargs={"job_name": "collect-nfl-live-scores", "settings": settings},
        **common,
    )
    # NFL pregame odds: one object, spent close to a game's decision
    # checkpoint so the quote is still inside the 240-minute freshness window
    # when the policy evaluates it. Every cycle outside a game's 75-minute
    # collection window costs nothing and makes no request at all.
    scheduler.add_job(
        execute_scheduled_job,
        "interval",
        minutes=15,
        jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(minutes=2),
        id="asp-nfl-pregame-odds",
        kwargs={"job_name": "collect-nfl-pregame-odds", "settings": settings},
        **common,
    )
    # The NFL paper policy's checkpoint. Five minutes matches the existing
    # final-pregame cadence, and the 30-15 minute window means a game is
    # evaluated two or three times rather than once -- which is what makes a
    # single slow tick harmless. Every run outside a window is a no-op.
    #
    # The `asp-dc-` prefix puts it on the `decision-checkpoints` lane. On
    # 2026-09-20 this job was due at 12:29:52 and could not run before its 12:45
    # window closed, because `generate-nfl-final-pregame-forecasts` started at
    # 12:30:13 and held the shared `core` thread for 799 seconds. A slow tick is
    # harmless; a slow NEIGHBOUR was not. The cadence, the window and every gate
    # below are unchanged -- only the thread it runs on.
    scheduler.add_job(
        execute_scheduled_job,
        "interval",
        minutes=5,
        jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(minutes=3),
        id="asp-dc-nfl-paper-checkpoint",
        kwargs={"job_name": "evaluate-nfl-paper-checkpoint", "settings": settings},
        **common,
    )
    # The CFB paper checkpoint (AISP_CFB_PAPER_POLICY_V1), same cadence and
    # window as NFL, and on the same `decision-checkpoints` lane. Registered
    # only when the CFB consumer is on; positions additionally wait on
    # CFB_PAPER_DECISIONS_ENABLED inside the owner.
    if settings.cfb_production_consumer_enabled:
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=5, jitter=None,
            next_run_time=datetime.now(UTC) + timedelta(minutes=2),
            id="asp-dc-cfb-paper-checkpoint",
            kwargs={"job_name": "evaluate-cfb-paper-checkpoint", "settings": settings},
            **common,
        )
        # CFB finals from retained producer evidence, then scoring. Reads local
        # files only; settlement stays with finalize-games.
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=30, jitter=None,
            next_run_time=datetime.now(UTC) + timedelta(minutes=5),
            id="asp-cfb-final-ingest",
            kwargs={"job_name": "ingest-cfb-finals", "settings": settings},
            **common,
        )
        # CFB SportsGameOdds prices: registered only when the owner turns the
        # lane on, and even then it spends only genuinely uncommitted capacity.
        if settings.cfb_sgo_lane_enabled:
            scheduler.add_job(
                execute_scheduled_job, "interval", minutes=10, jitter=None,
                next_run_time=datetime.now(UTC) + timedelta(minutes=7),
                id="asp-cfb-pregame-odds",
                kwargs={"job_name": "collect-cfb-pregame-odds", "settings": settings},
                **common,
            )
    # NBA Gen1: nothing is registered unless the owner turns the NBA consumer on.
    # Then the paper checkpoint records a reason per game (positions additionally
    # wait on a pinned owner grant and NBA_PAPER_DECISIONS_ENABLED), and finals
    # are read from pinned retained objects only. The historical import is
    # manual (import-nba-historical-evidence) and never scheduled.
    if settings.nba_production_consumer_enabled:
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=5, jitter=None,
            next_run_time=datetime.now(UTC) + timedelta(minutes=4),
            id="asp-nba-paper-checkpoint",
            kwargs={"job_name": "evaluate-nba-paper-checkpoint", "settings": settings},
            **common,
        )
        scheduler.add_job(
            execute_scheduled_job, "interval", minutes=30, jitter=None,
            next_run_time=datetime.now(UTC) + timedelta(minutes=8),
            id="asp-nba-final-ingest",
            kwargs={"job_name": "ingest-nba-finals", "settings": settings},
            **common,
        )
        # NBA SportsGameOdds prices: registered only when the owner turns the lane
        # on; it still spends nothing without an authority window and an object cap.
        if settings.nba_sgo_lane_enabled:
            scheduler.add_job(
                execute_scheduled_job, "interval", minutes=10, jitter=None,
                next_run_time=datetime.now(UTC) + timedelta(minutes=9),
                id="asp-nba-pregame-odds",
                kwargs={"job_name": "collect-nba-pregame-odds", "settings": settings},
                **common,
            )
    # NFL quarter breakdowns, one SportsGameOdds object per completed game.
    # Hourly is far more often than it needs to be and costs nothing on the runs
    # where every recent game already has its breakdown -- which is most of
    # them. The budget guard, not this interval, is what bounds the spend.
    scheduler.add_job(
        execute_scheduled_job,
        "interval",
        minutes=60,
        jitter=None,
        next_run_time=datetime.now(UTC) + timedelta(minutes=6),
        id="asp-nfl-period-breakdowns",
        kwargs={"job_name": "collect-nfl-period-breakdowns", "settings": settings},
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
        #
        # This is MLB's FINAL_PREGAME checkpoint -- the phase that may create a
        # virtual position -- so it belongs on the `decision-checkpoints` lane
        # with NFL's and CFB's, not behind whatever long import happens to hold
        # `core` when its cutoff arrives.
        scheduler.add_job(
            execute_scheduled_job,
            "interval",
            minutes=5,
            id="asp-dc-shadow-experiment-freeze",
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
            # Poll the existing event gate every minute.  The Model Studio
            # producer can take roughly eight minutes and the eligibility
            # consumer polls every five; a five-minute producer cadence could
            # therefore finish too late for the fixed T-15 decision cutoff.
            # This remains the sole owner, and the event/frozen checks keep
            # repeated ticks provider-free and idempotent.
            scheduler.add_job(
                execute_nfl_final_pregame_generation, "interval", minutes=1,
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
        # Free automatic finals, ordered BEFORE the evaluator so a game that
        # ended in the last hour is detected and scored on the same pass. It
        # spends no sportsbook credit -- a public CC-BY-4.0 GitHub asset, probed
        # through a 43-byte timestamp file -- but it is still bound to the one
        # supervised process, because two backends racing on the same
        # append-only result rows would be pointless duplication.
        if settings.owns_nfl_final_ingest:
            scheduler.add_job(
                execute_scheduled_job, "interval",
                minutes=settings.nfl_final_ingest_interval_minutes, jitter=None,
                next_run_time=datetime.now(UTC) + timedelta(minutes=4),
                id="asp-nfl-final-ingest",
                kwargs={"job_name": "ingest-nfl-finals", "settings": settings},
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
    if settings.v18_market_pilot_export_enabled:
        # Private V18 vs fresh-market pilot export (protocol f75aeccd...). Its
        # own lane (asp-rx-*): it only reads retained rows and writes its own
        # write-once packet files, so it neither waits behind nor delays an
        # import or a decision checkpoint. Registered only with the core jobs,
        # in the one process that owns them, so there is a single writer.
        scheduler.add_job(
            execute_v18_market_pilot_seal, "interval",
            minutes=settings.v18_market_pilot_interval_minutes, jitter=None,
            id="asp-rx-v18-market-pilot-seal", kwargs={"settings": settings}, **common,
        )
    if settings.v18_market_pilot_outcomes_export_enabled:
        # The same pilot's admitted-outcome export: a separate switch, the same
        # research-export lane and the same single-writer owner. Every 10
        # minutes; it reads retained rows only and writes its own files.
        scheduler.add_job(
            execute_v18_market_pilot_outcomes, "interval", minutes=10, jitter=None,
            id="asp-rx-v18-market-pilot-outcomes", kwargs={"settings": settings}, **common,
        )
    if settings.v18_market_pilot_outcomes_v2_enabled:
        # V2 outcome lane for the accepted amendment 7508ae72...: its own switch,
        # the same research-export lane and single-writer owner, every 10 minutes.
        scheduler.add_job(
            execute_v18_market_pilot_outcomes_v2, "interval", minutes=10, jitter=None,
            id="asp-rx-v18-market-pilot-outcomes-v2", kwargs={"settings": settings}, **common,
        )
    return scheduler
