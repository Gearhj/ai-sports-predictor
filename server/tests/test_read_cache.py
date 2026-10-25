"""The read-model cache serves stale-while-revalidate honestly."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import read_cache


def make_app():
    calls = {"today": 0, "admin": 0}
    inner = FastAPI()

    @inner.get("/api/v1/today")
    def today():
        calls["today"] += 1
        return {"n": calls["today"]}

    @inner.get("/api/v1/admin/jobs")
    def admin():
        calls["admin"] += 1
        return {"n": calls["admin"]}

    inner.add_middleware(read_cache.ReadCacheMiddleware, prefix="/api/v1")
    return inner, calls


def test_fresh_hits_are_served_from_memory_with_their_real_generation_time():
    app, calls = make_app()
    client = TestClient(app)
    first = client.get("/api/v1/today")
    second = client.get("/api/v1/today")
    assert first.headers["x-aisp-cache"] == "MISS" and second.headers["x-aisp-cache"] == "HIT"
    assert first.json() == second.json() == {"n": 1}
    assert calls["today"] == 1
    # Never restamped: the cached copy reports when it was generated.
    assert second.headers["x-aisp-generated-at"] == first.headers["x-aisp-generated-at"]


def test_a_matching_etag_returns_not_modified():
    app, _ = make_app()
    client = TestClient(app)
    etag = client.get("/api/v1/today").headers["etag"]
    assert client.get("/api/v1/today", headers={"If-None-Match": etag}).status_code == 304


def test_stale_entries_are_served_then_refreshed_once(monkeypatch):
    app, calls = make_app()
    client = TestClient(app)
    client.get("/api/v1/today")
    monkeypatch.setattr(read_cache, "FRESH_SECONDS", -1.0)
    stale = client.get("/api/v1/today")
    assert stale.headers["x-aisp-cache"] == "STALE"
    assert stale.json() == {"n": 1}  # the old answer, immediately


def test_paths_outside_the_allowlist_are_never_cached():
    app, calls = make_app()
    client = TestClient(app)
    client.get("/api/v1/admin/jobs")
    response = client.get("/api/v1/admin/jobs")
    assert "x-aisp-cache" not in response.headers
    assert calls["admin"] == 2


async def test_concurrent_misses_for_one_key_share_one_computation():
    """A hover prefetch and the page load (or two tabs) on an uncached key build it ONCE."""

    import time as _time

    import httpx

    calls = {"n": 0}
    inner = FastAPI()

    @inner.get("/api/v1/mission-control")
    def mission():
        calls["n"] += 1
        _time.sleep(0.3)                                  # long enough for the second request to arrive mid-build
        return {"n": calls["n"]}

    inner.add_middleware(read_cache.ReadCacheMiddleware, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=inner), base_url="http://testserver") as client:
        first, second = await asyncio.gather(client.get("/api/v1/mission-control?date=2026-10-17"),
                                             client.get("/api/v1/mission-control?date=2026-10-17"))
        other = await client.get("/api/v1/mission-control?date=2026-10-18")
    assert first.json() == second.json() == {"n": 1}
    assert first.headers["x-aisp-cache"] == second.headers["x-aisp-cache"] == "MISS"
    assert other.json() == {"n": 2} and calls["n"] == 2            # a different key is its own computation
    assert read_cache.ACTIVE.cache.inflight == {}


def test_mission_control_is_cached_but_never_served_older_than_ninety_seconds():
    calls = {"n": 0}
    inner = FastAPI()

    @inner.get("/api/v1/mission-control")
    def mission():
        calls["n"] += 1
        return {"n": calls["n"]}

    inner.add_middleware(read_cache.ReadCacheMiddleware, prefix="/api/v1")
    client = TestClient(inner)
    assert read_cache.MAX_STALE_BY_PATH["/mission-control"] == 90.0
    first = client.get("/api/v1/mission-control?date=2026-10-17")
    assert first.headers["x-aisp-cache"] == "MISS"
    assert client.get("/api/v1/mission-control?date=2026-10-17").headers["x-aisp-cache"] == "HIT"
    # Each date is its own key.
    assert client.get("/api/v1/mission-control?date=2026-10-18").headers["x-aisp-cache"] == "MISS"
    cache = read_cache.ACTIVE.cache
    key = "/api/v1/mission-control?date=2026-10-17"
    cache.entries[key].generated -= 60                       # inside 90 s: served stale, refreshed behind
    assert client.get("/api/v1/mission-control?date=2026-10-17").headers["x-aisp-cache"] == "STALE"
    cache.entries[key].generated -= 120                      # past 90 s: recomputed before answering
    late = client.get("/api/v1/mission-control?date=2026-10-17")
    assert late.headers["x-aisp-cache"] == "MISS" and late.json()["n"] > first.json()["n"]
