"""Scheduler reliability repairs: missed daily-pipeline catch-up, abandoned
job-run reconciliation, and the unauthenticated readiness probe.

Live evidence that motivated these:
* ``job_runs`` had NO ``daily-pipeline`` row for 2026-09-03 because the process
  was dead across 06:00 local; the day had zero games and zero forecasts.
* Four ``job_runs`` rows sat in RUNNING for days after their process was
  killed mid-job — an impossible status the product surfaced as "running".
* The only unauthenticated probe (``/healthz``) never touched the database or
  the scheduler, so a supervisor could not tell a wedged process from a
  healthy one.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.jobs import scheduler as scheduler_module
from app.jobs.scheduler import (
    DAILY_PIPELINE_HOUR,
    build_scheduler,
    daily_pipeline_catch_up_time,
    reconcile_abandoned_job_runs,
)
from app.models.entities import JobRun, RunStatus

ET = ZoneInfo("America/New_York")


def _factory(session):
    @contextmanager
    def factory():
        yield session

    return factory


def _run(session, job_name, status, started_local: datetime) -> JobRun:
    run = JobRun(job_name=job_name, status=status, started_at=started_local.astimezone(UTC),
                 parameters={"event_date": started_local.date().isoformat()})
    session.add(run)
    session.commit()
    return run


def _armed(settings):
    return settings.model_copy(update={
        "scheduler_enabled": True, "live_shadow_enabled": False, "nfl_shadow_enabled": False,
        "prediction_market_observer_enabled": False, "event_relative_scheduler_enabled": False,
        "odds_polling_enabled": False,
    })


# -- catch-up ----------------------------------------------------------------

def test_missed_daily_pipeline_is_caught_up_immediately(session, settings):
    now = datetime(2026, 9, 3, 11, 46, tzinfo=ET)  # the real restart time; 06:00 was missed
    assert daily_pipeline_catch_up_time(_armed(settings), now=now, session_factory=_factory(session)) == now


def test_failed_run_today_still_triggers_catch_up(session, settings):
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ET)
    _run(session, "daily-pipeline", RunStatus.FAILED, datetime(2026, 9, 3, 6, 0, tzinfo=ET))
    assert daily_pipeline_catch_up_time(_armed(settings), now=now, session_factory=_factory(session)) == now


def test_successful_run_today_suppresses_catch_up(session, settings):
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ET)
    _run(session, "daily-pipeline", RunStatus.SUCCEEDED, datetime(2026, 9, 3, 6, 1, tzinfo=ET))
    assert daily_pipeline_catch_up_time(_armed(settings), now=now, session_factory=_factory(session)) is None


def test_yesterdays_success_does_not_count_for_today(session, settings):
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ET)
    _run(session, "daily-pipeline", RunStatus.SUCCEEDED, datetime(2026, 9, 2, 6, 1, tzinfo=ET))
    assert daily_pipeline_catch_up_time(_armed(settings), now=now, session_factory=_factory(session)) == now


def test_before_scheduled_hour_defers_to_cron(session, settings):
    now = datetime(2026, 9, 3, DAILY_PIPELINE_HOUR - 1, 30, tzinfo=ET)
    assert daily_pipeline_catch_up_time(_armed(settings), now=now, session_factory=_factory(session)) is None


def test_eastern_day_boundary_not_utc(session, settings):
    # 23:30 ET on 9/2 is 03:30 UTC on 9/3: a 9/2 success must still count.
    now = datetime(2026, 9, 2, 23, 30, tzinfo=ET)
    _run(session, "daily-pipeline", RunStatus.SUCCEEDED, datetime(2026, 9, 2, 6, 1, tzinfo=ET))
    assert daily_pipeline_catch_up_time(_armed(settings), now=now, session_factory=_factory(session)) is None


def test_disabled_scheduler_never_catches_up(session, settings):
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ET)
    disabled = _armed(settings).model_copy(update={"scheduler_enabled": False})
    assert daily_pipeline_catch_up_time(disabled, now=now, session_factory=_factory(session)) is None


def test_bookkeeping_failure_fails_safe_to_cron(settings):
    @contextmanager
    def broken():
        raise RuntimeError("database unavailable")
        yield  # pragma: no cover

    now = datetime(2026, 9, 3, 12, 0, tzinfo=ET)
    assert daily_pipeline_catch_up_time(_armed(settings), now=now, session_factory=broken) is None


def test_build_scheduler_applies_catch_up_as_first_fire_only(settings, monkeypatch):
    armed = _armed(settings)
    fire_at = datetime(2026, 9, 3, 15, 46, tzinfo=UTC)
    monkeypatch.setattr(scheduler_module, "daily_pipeline_catch_up_time", lambda s: fire_at)
    sched = build_scheduler(armed)
    job = sched.get_job("asp-daily-pipeline")
    assert job is not None and job.next_run_time == fire_at
    # The cron trigger is untouched: the fire after the catch-up is the next 06:00 local.
    following = job.trigger.get_next_fire_time(fire_at, fire_at + timedelta(seconds=1))
    assert following.astimezone(ET).hour == DAILY_PIPELINE_HOUR and following > fire_at

    monkeypatch.setattr(scheduler_module, "daily_pipeline_catch_up_time", lambda s: None)
    normal = build_scheduler(armed).get_job("asp-daily-pipeline")
    # No explicit first fire: APScheduler computes it from the cron at start.
    assert getattr(normal, "next_run_time", None) is None


def test_scheduler_registration_unchanged_when_no_catch_up(settings, monkeypatch):
    monkeypatch.setattr(scheduler_module, "daily_pipeline_catch_up_time", lambda s: None)
    ids = {job.id for job in build_scheduler(_armed(settings)).get_jobs()}
    assert ids == {"asp-daily-pipeline", "asp-finalize-games"}


# -- abandoned run reconciliation --------------------------------------------

def test_running_rows_are_marked_failed_and_visible(session):
    stale = _run(session, "refresh-prediction-market-snapshots", RunStatus.RUNNING, datetime(2026, 9, 1, 22, 3, tzinfo=ET))
    done = _run(session, "finalize-games", RunStatus.SUCCEEDED, datetime(2026, 9, 3, 11, 0, tzinfo=ET))
    now = datetime(2026, 9, 3, 11, 46, tzinfo=UTC)
    assert reconcile_abandoned_job_runs(now=now, session_factory=_factory(session)) == 1
    session.refresh(stale)
    session.refresh(done)
    completed = stale.completed_at if stale.completed_at.tzinfo else stale.completed_at.replace(tzinfo=UTC)
    assert stale.status == RunStatus.FAILED and completed == now  # SQLite stores naive UTC
    assert "ABANDONED" in (stale.error_message or "")
    assert done.status == RunStatus.SUCCEEDED and done.error_message is None
    # Idempotent: a second pass finds nothing.
    assert reconcile_abandoned_job_runs(now=now, session_factory=_factory(session)) == 0
    assert session.scalar(select(JobRun).where(JobRun.status == RunStatus.RUNNING)) is None


def test_reconciliation_failure_is_contained():
    @contextmanager
    def broken():
        raise RuntimeError("database unavailable")
        yield  # pragma: no cover

    assert reconcile_abandoned_job_runs(session_factory=broken) == 0


# -- readiness probe ---------------------------------------------------------

def test_readyz_reports_database_and_scheduler_without_secrets(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ready", "database": "reachable", "scheduler": "disabled"}


def test_readyz_is_503_when_database_is_unreachable(client, monkeypatch):
    from app.db import session as session_module

    @contextmanager
    def broken():
        raise RuntimeError("connection refused")
        yield  # pragma: no cover

    monkeypatch.setattr(session_module, "SessionLocal", broken)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"
    assert "connection" not in response.text.lower() or "refused" not in response.text.lower()


def test_healthz_still_unauthenticated_liveness(client):
    assert client.get("/healthz").json() == {"status": "ok"}
