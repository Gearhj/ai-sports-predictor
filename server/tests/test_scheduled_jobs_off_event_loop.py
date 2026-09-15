"""A scheduled job's synchronous work must not freeze the web server's event loop.

On 2026-09-15 job bodies ran on the HTTP loop: a 392-second `finalize-games`
run held a page's HTML response for 116 seconds. Scheduled coroutines now run
on one dedicated worker thread, still one at a time.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.jobs.scheduler import build_scheduler, off_event_loop


@pytest.mark.asyncio
async def test_a_blocking_job_leaves_the_event_loop_responsive():
    async def blocking_job():
        time.sleep(0.6)  # synchronous work, as the real job bodies do
        return "done"

    job = asyncio.ensure_future(off_event_loop(blocking_job)())
    started = time.perf_counter()
    await asyncio.sleep(0.05)  # the loop answers while the job is still running
    assert time.perf_counter() - started < 0.3
    assert not job.done()
    assert await job == "done"


@pytest.mark.asyncio
async def test_jobs_still_run_one_at_a_time_in_order():
    order: list[str] = []

    def make(name):
        async def job():
            order.append(f"{name}:start")
            time.sleep(0.1)
            order.append(f"{name}:end")
        return job

    await asyncio.gather(off_event_loop(make("a"))(), off_event_loop(make("b"))())
    assert order == ["a:start", "a:end", "b:start", "b:end"]


def test_every_registered_coroutine_job_is_moved_off_the_loop(settings):
    scheduler = build_scheduler(settings.model_copy(update={"scheduler_core_jobs_enabled": True}))
    import inspect

    for job in scheduler.get_jobs():
        if inspect.iscoroutinefunction(job.func):
            assert getattr(job.func, "__aisp_off_event_loop__", False), job.id
