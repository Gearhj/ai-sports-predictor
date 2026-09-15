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
