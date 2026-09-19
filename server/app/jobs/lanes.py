"""Scheduled-job execution lanes: observation and research exports off the core thread.

WHY
---
Every scheduled coroutine used to run on ONE worker thread, first come first
served. On 2026-09-18 four prediction-market jobs (consensus, discovery,
snapshots, alerts) held that thread from 22:11:01 to 22:13:34 UTC. The V18
exchange import due at 22:12:47 started at 22:13:57, so a forecast published at
22:08:35 reached AISP at 22:13:57, inside CHC @ CIN's 22:10-22:25 decision
window. APScheduler also SKIPS a fire whose previous instance is still waiting
for the thread ("maximum number of running instances reached"), so a busy
thread drops checkpoints outright, not just delays them.

WHAT
----
Three single-worker lanes:

* ``core`` -- every job that imports forecasts, collects decision inputs,
  evaluates checkpoints, settles or scores. Unchanged: one at a time.
* ``prediction-markets`` -- the read-only Prediction Market Observer jobs
  (``asp-pm-*``). They touch only their own tables and their own providers,
  open their own session per run, and never gate an AISP decision.

* ``research-export`` -- read-only private research exports (``asp-rx-*``),
  such as the V18 market-pilot packet sealer. They read retained rows, write
  only their own files outside the database, never call a provider and never
  create a decision or position, so they must not wait behind -- or hold up --
  an import or a decision checkpoint.

Each lane is still serial, each job is still ``max_instances=1``, and each run
still asks ``is_current_scheduler_owner()`` and opens a job-local session in
``execute_scheduled_job``. Nothing moves between processes.

The scheduler also records, per run, when APScheduler fired the job and how
long it waited for its lane (``JobRun.parameters.scheduling``), so queue delay
is measured rather than inferred.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

CORE_LANE = "core"
PREDICTION_MARKET_LANE = "prediction-markets"
PREDICTION_MARKET_JOB_PREFIX = "asp-pm-"
RESEARCH_EXPORT_LANE = "research-export"
RESEARCH_EXPORT_JOB_PREFIX = "asp-rx-"

#: Scheduler-id prefix -> lane. Anything unlisted stays on the core lane.
_LANE_PREFIXES: tuple[tuple[str, str], ...] = (
    (PREDICTION_MARKET_JOB_PREFIX, PREDICTION_MARKET_LANE),
    (RESEARCH_EXPORT_JOB_PREFIX, RESEARCH_EXPORT_LANE),
)

_EXECUTORS: dict[str, ThreadPoolExecutor] = {}
_EXECUTORS_LOCK = threading.Lock()
_meta = threading.local()


def lane_for(job_id: str | None) -> str:
    """The lane a registered job runs on, from its scheduler id."""

    value = str(job_id or "")
    for prefix, lane in _LANE_PREFIXES:
        if value.startswith(prefix):
            return lane
    return CORE_LANE


def executor(lane: str = CORE_LANE) -> ThreadPoolExecutor:
    """The lane's single worker thread (created on first use)."""

    with _EXECUTORS_LOCK:
        pool = _EXECUTORS.get(lane)
        if pool is None:
            pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"aisp-job-{lane}")
            _EXECUTORS[lane] = pool
        return pool


def run_with_meta(lane: str, fired_at: datetime, work: Any) -> Any:
    """Run ``work()`` on the current lane thread with this run's scheduling facts visible."""

    started = datetime.now(UTC)
    _meta.value = {
        "lane": lane,
        "fired_at": fired_at.isoformat(),
        "lane_started_at": started.isoformat(),
        "queue_wait_seconds": round((started - fired_at).total_seconds(), 3),
    }
    try:
        return work()
    finally:
        _meta.value = None


def current_meta() -> dict[str, Any] | None:
    """Scheduling facts of the run executing on this thread, or None (manual runs)."""

    value = getattr(_meta, "value", None)
    return dict(value) if value else None


__all__ = [
    "CORE_LANE", "PREDICTION_MARKET_LANE", "RESEARCH_EXPORT_LANE", "current_meta", "executor",
    "lane_for", "run_with_meta",
]
