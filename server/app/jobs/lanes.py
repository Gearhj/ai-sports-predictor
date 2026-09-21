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

AND AGAIN ON 2026-09-20, THE OTHER WAY ROUND
--------------------------------------------
At 12:30:13 ET ``generate-nfl-final-pregame-forecasts`` started and ran 799
seconds against its usual ~480. It holds the ``core`` thread, so
``evaluate-nfl-paper-checkpoint`` -- due at 12:29:52 -- could not run before its
window closed at 12:45. Eight games came within minutes of having no decision at
all; they were recovered only because an operator triggered the checkpoint by
hand through the admin path at 12:41:32, which runs outside that thread.

The lesson is that "decision checkpoints share a lane with the long jobs that
feed them" is the hazard, not any particular long job. A checkpoint's window is
fifteen minutes wide and it cannot be moved; a forecast generation pass has no
deadline at all. They must not queue against each other.

WHAT
----
Five single-worker lanes:

* ``core`` -- every job that imports forecasts, collects decision inputs,
  settles or scores. Unchanged: one at a time.
* ``decision-checkpoints`` -- the jobs that MUST run inside a T-30..T-15
  decision window (``asp-dc-*``). Nothing else. They read frozen forecasts and
  stored quotes, call no provider, and are the only jobs here whose lateness
  costs a decision outright.
* ``prediction-markets`` -- the read-only Prediction Market Observer jobs
  (``asp-pm-*``). They touch only their own tables and their own providers,
  open their own session per run, and never gate an AISP decision.

* ``producer-import`` -- the producer-evidence import (``asp-im-*``). A FINAL
  forecast is only usable once IMPORTED, so an import queued behind the very
  job that produces the forecast is the one delay that silently costs a
  decision. On 2026-09-20 that wait was a measured 703.56 seconds.

* ``research-export`` -- read-only private research exports (``asp-rx-*``),
  such as the V18 market-pilot packet sealer. They read retained rows, write
  only their own files outside the database, never call a provider and never
  create a decision or position, so they must not wait behind -- or hold up --
  an import or a decision checkpoint.

Each lane is still serial, each job is still ``max_instances=1``, and each run
still asks ``is_current_scheduler_owner()`` and opens a job-local session in
``execute_scheduled_job``. Nothing moves between processes.

A lane changes WHERE a job runs, never WHEN it is eligible. Every checkpoint
owner still reads the clock itself at the moment its body executes and selects
games by ``starts_at`` against that moment, so a job that reaches its lane after
the cutoff finds no game and refuses. ``fired_at`` is recorded as evidence and
is never used as the evaluation moment -- a late run must not be backdated to
the time it was due.

The scheduler also records, per run, when APScheduler fired the job and how
long it waited for its lane (``JobRun.parameters.scheduling``), so queue delay
is measured rather than inferred.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

CORE_LANE = "core"
PREDICTION_MARKET_LANE = "prediction-markets"
PREDICTION_MARKET_JOB_PREFIX = "asp-pm-"
RESEARCH_EXPORT_LANE = "research-export"
RESEARCH_EXPORT_JOB_PREFIX = "asp-rx-"
DECISION_CHECKPOINT_LANE = "decision-checkpoints"
DECISION_CHECKPOINT_JOB_PREFIX = "asp-dc-"

#: The producer-evidence import (``asp-im-*``).
#:
#: It used to share ``core`` with model execution, and on 2026-09-20 that cost a
#: measured 703.56 seconds of queue wait: the import due at about 12:31:52 sat
#: behind `generate-nfl-final-pregame-forecasts`, which held ``core`` for 799.3
#: seconds, and did not start until 12:43:35. The job ledger recorded the wait
#: itself -- `parameters.scheduling.queue_wait_seconds = 703.56`.
#:
#: That matters beyond one slow evening. A FINAL forecast is only usable if it
#: is IMPORTED before the last checkpoint tick inside the decision window, so an
#: import queued behind the very job that produces the forecast is the one delay
#: that can silently cost a decision. The producer contract
#: (`nfl-final-delivery-operational-v2`) requires a measured shared-queue bound
#: of at most 600 seconds and independent per-game import; 703.56 exceeds it.
#:
#: Same remedy as the decision checkpoints: its own single worker, so a long
#: model run cannot hold it. Nothing about what the import DOES changes.
IMPORT_LANE = "producer-import"
IMPORT_JOB_PREFIX = "asp-im-"

#: Scheduler-id prefix -> lane. Anything unlisted stays on the core lane.
_LANE_PREFIXES: tuple[tuple[str, str], ...] = (
    (PREDICTION_MARKET_JOB_PREFIX, PREDICTION_MARKET_LANE),
    (RESEARCH_EXPORT_JOB_PREFIX, RESEARCH_EXPORT_LANE),
    (DECISION_CHECKPOINT_JOB_PREFIX, DECISION_CHECKPOINT_LANE),
    (IMPORT_JOB_PREFIX, IMPORT_LANE),
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


# -- one checkpoint execution at a time ------------------------------------
_CHECKPOINT_LOCKS: dict[str, threading.Lock] = {}
_CHECKPOINT_LOCKS_GUARD = threading.Lock()


@contextmanager
def decision_checkpoint_guard(key: str) -> Iterator[bool]:
    """Hold ``key``'s checkpoint slot for this execution, or yield ``False``.

    WHY THIS EXISTS
    ---------------
    Every checkpoint owner decides whether to write by READING first -- "is
    there already a FINAL_PREGAME decision for this experiment and game?" -- and
    then inserting when there is not (``nfl_paper_decision.record_outcome``,
    ``cfb.paper_decision.record``, ``shadow_experiment_service._append_decision``).
    Read-then-write is idempotent only while the executions are serialised.
    Until now they always were: every one of these jobs sat on the single
    ``core`` thread, and APScheduler's ``max_instances=1`` stopped a job
    overlapping itself. That is what makes a five-minute job firing three times
    inside a fifteen-minute window record one decision, not three.

    The three checkpoints still share ONE single-worker lane, so they remain
    serialised against each other exactly as before. What was never covered --
    before this change and after it -- is the admin path: ``POST
    /admin/jobs/{job_name}`` awaits the orchestrator directly on the web event
    loop, outside every lane. That is precisely the path used to rescue the
    2026-09-20 incident at 12:41:32, so it is a path that gets used when a
    window is already in trouble, which is the worst moment to discover that two
    executions can interleave between the read and the write.

    WHY IT REFUSES INSTEAD OF WAITING
    ---------------------------------
    A blocking lock acquired from the web event loop would freeze HTTP for the
    length of the checkpoint -- reintroducing, on the request thread, the exact
    pathology the lanes were built to remove. So a second concurrent execution
    does not queue: it declines and says so. Nothing is lost by declining,
    because the execution already holding the slot is doing that same work
    against the same window, and the caller gets a receipt naming the reason
    rather than a silent no-op.
    """

    with _CHECKPOINT_LOCKS_GUARD:
        lock = _CHECKPOINT_LOCKS.setdefault(key, threading.Lock())
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


__all__ = [
    "CORE_LANE", "DECISION_CHECKPOINT_JOB_PREFIX", "DECISION_CHECKPOINT_LANE",
    "PREDICTION_MARKET_LANE", "RESEARCH_EXPORT_LANE", "current_meta",
    "IMPORT_LANE", "IMPORT_JOB_PREFIX",
    "decision_checkpoint_guard", "executor", "lane_for", "run_with_meta",
]
