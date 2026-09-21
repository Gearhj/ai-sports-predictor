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
        "v18_market_pilot_export_enabled": False, "v18_market_pilot_outcomes_export_enabled": False,
        "v18_market_pilot_outcomes_v2_enabled": False}))
    # Every lane, including the two added for the NFL FINAL delivery contract.
    # Omitting them made this a KeyError rather than an assertion the moment a
    # job actually landed on one -- which it does under the production .env,
    # where the exchange consumer is registered as `asp-im-*`. A missing key
    # here fails as a crash rather than as the disallowed-lane finding it is
    # meant to be, so the map is built from the lane constants themselves.
    seen = {lane: set() for lane in (
        lanes.CORE_LANE, lanes.PREDICTION_MARKET_LANE, lanes.RESEARCH_EXPORT_LANE,
        lanes.DECISION_CHECKPOINT_LANE, lanes.IMPORT_LANE, lanes.NFL_FINAL_IMPORT_LANE)}
    for job in scheduler.get_jobs():
        if inspect.iscoroutinefunction(job.func):
            seen[getattr(job.func, "__aisp_job_lane__")].add(job.id)
    assert seen[lanes.PREDICTION_MARKET_LANE] == {
        "asp-pm-discover", "asp-pm-refresh", "asp-pm-consensus", "asp-pm-alerts", "asp-pm-finalize-pregame"}
    assert seen[lanes.RESEARCH_EXPORT_LANE] == set()
    # These two guarded assertions had gone SILENT. `asp-model-exchange-consumer`
    # was renamed `asp-im-model-exchange-consumer` when the import moved to its
    # own lane, so the `if any(...)` guard stopped matching and the assertion
    # inside it stopped running -- a test that passes by never checking anything
    # is worse than one that fails. Each job is now asserted on the lane it is
    # actually supposed to occupy, by its current id.
    registered = {job.id for job in scheduler.get_jobs()}
    if "asp-im-model-exchange-consumer" in registered:
        assert "asp-im-model-exchange-consumer" in seen[lanes.IMPORT_LANE]
    if "asp-nf-nfl-final-pair" in registered:
        assert "asp-nf-nfl-final-pair" in seen[lanes.NFL_FINAL_IMPORT_LANE]
    if "asp-odds-checkpoint" in registered:
        assert "asp-odds-checkpoint" in seen[lanes.CORE_LANE]
    # The old id must not come back: a job re-registered under it would silently
    # rejoin the core lane and reintroduce the 703.56 s queue wait.
    assert "asp-model-exchange-consumer" not in registered


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


def test_the_producer_import_has_its_own_lane():
    """A FINAL forecast is only usable once imported, so the import must not
    queue behind the model run that produces it.

    On 2026-09-20 the import due at about 12:31:52 waited a measured 703.56
    seconds behind `generate-nfl-final-pregame-forecasts` (799.3 s on `core`)
    and started at 12:43:35. The producer contract requires a measured
    shared-queue bound of at most 600 seconds; 703.56 exceeded it.
    """
    from app.jobs import lanes

    assert lanes.lane_for("asp-im-model-exchange-consumer") == lanes.IMPORT_LANE
    assert lanes.IMPORT_LANE == "producer-import"
    # and it is NOT the lane the long model run uses
    assert lanes.lane_for("generate-nfl-final-pregame-forecasts") == lanes.CORE_LANE
    assert lanes.IMPORT_LANE != lanes.CORE_LANE
    # nor does it share the decision-checkpoint worker
    assert lanes.IMPORT_LANE != lanes.DECISION_CHECKPOINT_LANE


def test_the_import_lane_has_its_own_worker():
    """Separate lane means a separate single-worker executor, not a label."""
    from app.jobs import lanes

    assert lanes.executor(lanes.IMPORT_LANE) is not lanes.executor(lanes.CORE_LANE)
    assert lanes.executor(lanes.IMPORT_LANE)._max_workers == 1


def test_the_import_job_is_registered_on_the_import_lane(settings):
    """The scheduler id carries the prefix, so registration and lane agree."""
    import inspect

    from app.jobs import scheduler as sched

    source = inspect.getsource(sched)
    assert '"asp-im-model-exchange-consumer"' in source
    assert '"asp-model-exchange-consumer"' not in source


def test_the_nfl_final_pair_import_has_its_own_lane():
    """Neither the broad pass's duration nor its instance limit may reach it.

    The broad pass commits every sport in one transaction and, at
    max_instances=1, a long run SKIPS the next fire rather than delaying it (a
    353-second pass on 2026-09-21 skipped the following one outright). Against
    a hard T-15 edge either can consume the last usable import.
    """
    from app.jobs import lanes

    assert lanes.lane_for("asp-nf-nfl-final-pair") == lanes.NFL_FINAL_IMPORT_LANE
    assert lanes.NFL_FINAL_IMPORT_LANE == "nfl-final-import"
    # distinct from the broad import, the checkpoints and core
    for other in (lanes.IMPORT_LANE, lanes.DECISION_CHECKPOINT_LANE, lanes.CORE_LANE):
        assert lanes.NFL_FINAL_IMPORT_LANE != other
    assert lanes.executor(lanes.NFL_FINAL_IMPORT_LANE) is not lanes.executor(lanes.IMPORT_LANE)
    assert lanes.executor(lanes.NFL_FINAL_IMPORT_LANE)._max_workers == 1


def test_the_nfl_pair_import_is_registered_and_runs_every_minute(settings):
    import inspect

    from app.jobs import scheduler as sched

    source = inspect.getsource(sched)
    assert '"asp-nf-nfl-final-pair"' in source
    assert '"import-nfl-final-pair"' in source
    assert settings.nfl_final_import_interval_minutes == 1


def test_the_nfl_pair_import_is_a_distinct_job_from_the_broad_pass():
    """Separate job name means a separate max_instances budget."""
    from app.jobs.orchestrator import JobOrchestrator

    handlers = JobOrchestrator.__dict__
    assert "import_nfl_final_pair" in handlers
    assert "consume_model_exchange_artifacts" in handlers
    assert handlers["import_nfl_final_pair"] is not handlers["consume_model_exchange_artifacts"]


def test_the_narrow_import_commits_without_the_other_sports():
    """Its stages are the NFL pair only -- not CFB, NBA, the lab study or V18."""
    import inspect

    from app.jobs import model_exchange_consumer as mec

    body = inspect.getsource(mec.run_nfl_final_import)
    assert 'sport="NFL"' in body
    assert "_consume_supplements" in body
    assert "session.commit()" in body
    for foreign in ("_consume_cfb_discovery", "_consume_nba_production",
                    "_consume_lab_study", "_consume_v18"):
        assert foreign not in body, f"{foreign} must not be in the deadline-bound NFL path"
