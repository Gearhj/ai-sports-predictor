"""Prediction-market observation runs on its own lane (2026-09-18, CHC @ CIN).

Four prediction-market jobs held the single job thread from 22:11:01 to
22:13:34 UTC and the V18 import due at 22:12:47 started at 22:13:57, inside the
game's decision window. Each lane stays serial; the lanes no longer wait on
each other.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.jobs import lanes
from app.jobs.orchestrator import JobOrchestrator
from app.jobs.scheduler import build_scheduler, off_event_loop


@pytest.mark.asyncio
async def test_a_busy_prediction_market_lane_does_not_delay_a_core_job():
    events: list[tuple[str, float]] = []
    origin = time.perf_counter()

    async def slow_prediction_market_job():
        events.append(("pm:start", time.perf_counter() - origin))
        time.sleep(0.6)
        events.append(("pm:end", time.perf_counter() - origin))

    async def forecast_import():
        events.append(("core:start", time.perf_counter() - origin))

    pm = asyncio.ensure_future(off_event_loop(slow_prediction_market_job, lanes.PREDICTION_MARKET_LANE)())
    await asyncio.sleep(0.05)
    await off_event_loop(forecast_import, lanes.CORE_LANE)()
    core_started = dict(events)["core:start"]
    assert "pm:end" not in dict(events), "the core job had to wait for the prediction-market job"
    assert core_started < 0.4
    await pm


@pytest.mark.asyncio
async def test_each_lane_is_still_serial():
    order: list[str] = []

    def make(name):
        async def job():
            order.append(f"{name}:start")
            time.sleep(0.1)
            order.append(f"{name}:end")
        return job

    lane = lanes.PREDICTION_MARKET_LANE
    await asyncio.gather(off_event_loop(make("a"), lane)(), off_event_loop(make("b"), lane)())
    assert order == ["a:start", "a:end", "b:start", "b:end"]


def test_only_prediction_market_jobs_leave_the_core_lane(settings):
    scheduler = build_scheduler(settings.model_copy(update={
        "scheduler_core_jobs_enabled": True, "prediction_market_observer_enabled": True,
        "v18_market_pilot_export_enabled": False}))
    seen = {lanes.CORE_LANE: set(), lanes.PREDICTION_MARKET_LANE: set(), lanes.RESEARCH_EXPORT_LANE: set()}
    for job in scheduler.get_jobs():
        if inspect.iscoroutinefunction(job.func):
            seen[getattr(job.func, "__aisp_job_lane__")].add(job.id)
    assert seen[lanes.PREDICTION_MARKET_LANE] == {
        "asp-pm-discover", "asp-pm-refresh", "asp-pm-consensus", "asp-pm-alerts", "asp-pm-finalize-pregame"}
    assert seen[lanes.RESEARCH_EXPORT_LANE] == set()
    for critical in ("asp-model-exchange-consumer", "asp-shadow-experiment-freeze", "asp-odds-checkpoint"):
        if any(job.id == critical for job in scheduler.get_jobs()):
            assert critical in seen[lanes.CORE_LANE]


def test_a_run_records_when_it_fired_and_how_long_it_waited(session, settings, monkeypatch):
    async def instant(self, name, event_date):
        return {"ok": True}

    monkeypatch.setattr(JobOrchestrator, "_execute", instant)
    fired = datetime.now(UTC) - timedelta(seconds=70)
    run = lanes.run_with_meta(lanes.CORE_LANE, fired,
                              lambda: asyncio.run(JobOrchestrator(session, settings).run("refresh-scoreboard")))
    scheduling = run.parameters["scheduling"]
    assert scheduling["lane"] == "core"
    assert scheduling["fired_at"] == fired.isoformat()
    assert scheduling["queue_wait_seconds"] >= 70
    assert lanes.current_meta() is None


@pytest.mark.asyncio
async def test_a_manual_run_carries_no_scheduling_record(session, settings, monkeypatch):
    async def instant(self, name, event_date):
        return {"ok": True}

    monkeypatch.setattr(JobOrchestrator, "_execute", instant)
    run = await JobOrchestrator(session, settings).run("refresh-scoreboard")
    assert "scheduling" not in run.parameters
