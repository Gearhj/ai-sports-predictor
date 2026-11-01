"""Security/availability review 11-01: resilient accept loop, body cap, read-cache key, rate-limit keys."""
from __future__ import annotations

import asyncio
import socket
import sys

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api import read_cache
from app.core import rate_limit as rl
from app.core.body_limit import BodyLimitMiddleware

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Proactor loop is Windows-only")


def _winerror(code: int) -> OSError:
    return OSError(None, f"winerror {code}", None, code)


# --------------------------------------------------------------------------- 1. resilient accept loop


@windows_only
def test_the_override_is_active_only_because_the_installed_stdlib_matches_the_copy():
    from app import winloop

    assert winloop.stock_matches() and winloop.RESILIENT_ACTIVE
    assert winloop.ResilientProactorEventLoop._start_serving is winloop._resilient_start_serving
    assert issubclass(winloop.ResilientProactorEventLoop, asyncio.ProactorEventLoop)


@windows_only
def test_a_different_stdlib_falls_back_to_stock(monkeypatch):
    from app import winloop

    monkeypatch.setattr(winloop, "_stock_source_sha256", lambda: "0" * 64)
    assert winloop.stock_matches() is False


class _FakeSock:
    def __init__(self):
        self.closed = False

    def fileno(self):
        return -1 if self.closed else 7

    def close(self):
        self.closed = True


class _FailedFuture:
    def __init__(self, exc):
        self.exc = exc
        self.callbacks = []

    def result(self):
        raise self.exc

    def add_done_callback(self, cb):
        self.callbacks.append(cb)


class _FakeLoop:
    _debug = False

    def __init__(self, accept_results):
        self.accept_results = list(accept_results)
        self.accept_calls = 0
        self.soon, self.later, self.handled = [], [], []
        self._accept_futures = {}
        self.closed = False

        loop = self

        class _Proactor:
            def accept(self, sock):
                loop.accept_calls += 1
                return loop.accept_results.pop(0)

        self._proactor = _Proactor()

    def is_closed(self):
        return self.closed

    def call_soon(self, cb):
        self.soon.append(cb)

    def call_later(self, delay, cb):
        self.later.append((delay, cb))

    def call_exception_handler(self, ctx):
        self.handled.append(ctx)


@windows_only
def test_a_netname_deleted_accept_rearms_with_backoff_instead_of_closing_the_listener():
    from app.winloop import BACKOFF_INITIAL, BACKOFF_MAX, _resilient_start_serving

    failing = [_FailedFuture(_winerror(64)) for _ in range(8)]
    fake, sock = _FakeLoop(failing + [_FailedFuture(_winerror(64))]), _FakeSock()
    _resilient_start_serving(fake, lambda: None, sock)
    fake.soon.pop()()                                    # first arm
    assert fake.accept_calls == 1 and fake._accept_futures[7] is failing[0]
    delays = []
    for fut in failing:
        fut.callbacks[0](fut)                            # the accept completes with WinError 64
        assert not sock.closed and fake.handled == []
        delay, rearm = fake.later.pop()
        delays.append(delay)
        rearm()                                          # re-armed: accept is called again
    assert fake.accept_calls == 9
    assert delays[0] == BACKOFF_INITIAL and max(delays) == BACKOFF_MAX and delays == sorted(delays)


@windows_only
def test_rearm_does_nothing_once_the_server_closed_its_socket():
    from app.winloop import _resilient_start_serving

    fut = _FailedFuture(_winerror(10054))
    fake, sock = _FakeLoop([fut]), _FakeSock()
    _resilient_start_serving(fake, lambda: None, sock)
    fake.soon.pop()()
    fut.callbacks[0](fut)
    _, rearm = fake.later.pop()
    sock.close()
    rearm()
    assert fake.accept_calls == 1


@windows_only
def test_other_accept_errors_keep_the_stock_close_behaviour():
    from app.winloop import _resilient_start_serving

    fut = _FailedFuture(_winerror(87))
    fake, sock = _FakeLoop([fut]), _FakeSock()
    _resilient_start_serving(fake, lambda: None, sock)
    fake.soon.pop()()
    fut.callbacks[0](fut)
    assert sock.closed and fake.later == []
    assert fake.handled and fake.handled[0]["message"] == "Accept failed on a socket"


async def _serve_once_after_a_failed_accept(loop: asyncio.AbstractEventLoop) -> bytes | None:
    async def echo(reader, writer):
        writer.write(await reader.readexactly(4))
        await writer.drain()
        writer.close()

    original = loop._proactor.accept
    calls = {"n": 0}

    def flaky_accept(sock):
        calls["n"] += 1
        if calls["n"] == 1:
            fut = loop.create_future()
            fut.set_exception(_winerror(64))
            return fut
        return original(sock)

    loop._proactor.accept = flaky_accept
    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    await asyncio.sleep(0.3)                     # the failed accept and its backoff happen first
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 3)
        writer.write(b"ping")
        await writer.drain()
        data = await asyncio.wait_for(reader.readexactly(4), 3)
        writer.close()
        return data
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        return None
    finally:
        server.close()


@windows_only
def test_a_real_resilient_loop_still_accepts_after_an_accept_fails():
    from app.winloop import ResilientProactorEventLoop

    loop = ResilientProactorEventLoop()
    try:
        assert loop.run_until_complete(_serve_once_after_a_failed_accept(loop)) == b"ping"
    finally:
        loop.close()


@windows_only
def test_the_stock_loop_loses_its_listener_on_the_same_failure():
    """The outage itself: stock CPython closes the listening socket after one WinError 64."""

    loop = asyncio.ProactorEventLoop()
    loop.set_exception_handler(lambda _loop, _ctx: None)
    try:
        assert loop.run_until_complete(_serve_once_after_a_failed_accept(loop)) is None
    finally:
        loop.close()


@windows_only
def test_uvicorn_resolves_the_supervisor_loop_string_to_the_resilient_loop():
    from uvicorn.config import Config

    from app.winloop import ResilientProactorEventLoop

    config = Config("app.main:app", loop="app.winloop:ResilientProactorEventLoop")
    assert config.get_loop_factory() is ResilientProactorEventLoop


def test_the_supervisor_runs_uvicorn_on_the_resilient_loop():
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "asp-backend-supervisor.ps1"
    line = next(l for l in script.read_text(encoding="utf-8").splitlines() if "-m uvicorn app.main:app" in l)
    assert "--loop app.winloop:ResilientProactorEventLoop" in line


# --------------------------------------------------------------------------- 2. body limit


def _body_app():
    seen = {"n": 0}
    inner = FastAPI()

    @inner.post("/preview-api/arena/sync")
    @inner.post("/preview-api/booth")
    @inner.post("/api/v1/probability/evaluate")
    @inner.post("/api/v1/auth/login")
    async def echo(request: Request):
        seen["n"] += 1
        return {"bytes": len(await request.body())}

    @inner.get("/api/v1/today")
    def today():
        return {"ok": True}

    inner.add_middleware(BodyLimitMiddleware, api_prefix="/api/v1")
    return inner, seen


@pytest.mark.parametrize("path,cap", [("/preview-api/arena/sync", 512 * 1024), ("/preview-api/booth", 64 * 1024),
                                      ("/api/v1/probability/evaluate", 128 * 1024), ("/api/v1/auth/login", 64 * 1024)])
def test_a_declared_length_over_the_path_cap_is_refused_before_the_route_runs(path, cap):
    app, seen = _body_app()
    client = TestClient(app)
    ok = client.post(path, content=b"x" * cap, headers={"content-type": "application/json"})
    assert ok.status_code == 200 and ok.json() == {"bytes": cap}
    big = client.post(path, content=b"x" * (cap + 1), headers={"content-type": "application/json"})
    assert big.status_code == 413 and seen["n"] == 1


def test_the_arena_route_s_own_400000_byte_limit_still_fits_under_the_cap():
    app, _ = _body_app()
    assert TestClient(app).post("/preview-api/arena/sync", content=b"x" * 400_000).status_code == 200


def test_a_bad_content_length_is_refused():
    async def run():
        app, seen = _body_app()
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(m):
            sent.append(m)

        await app({"type": "http", "method": "POST", "path": "/api/v1/auth/login", "headers": [
            (b"content-length", b"abc")], "query_string": b""}, receive, send)
        return sent, seen

    sent, seen = asyncio.run(run())
    assert sent[0]["status"] == 400 and seen["n"] == 0


def test_a_chunked_body_is_counted_and_refused_past_the_cap():
    async def run(chunks):
        app, seen = _body_app()
        pending = list(chunks)
        sent = []

        async def receive():
            if pending:
                body = pending.pop(0)
                return {"type": "http.request", "body": body, "more_body": bool(pending)}
            await asyncio.sleep(3600)

        async def send(m):
            sent.append(m)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": "/preview-api/booth", "raw_path": b"/preview-api/booth",
                 "root_path": "", "query_string": b"", "server": ("testserver", 80), "client": ("1.2.3.4", 1),
                 "headers": [(b"host", b"testserver"), (b"transfer-encoding", b"chunked")]}
        await asyncio.wait_for(app(scope, receive, send), 5)
        return sent

    small = asyncio.run(run([b"x" * 1024] * 4))
    assert small[0]["status"] == 200
    big = asyncio.run(run([b"x" * 16 * 1024] * 5))           # 80 KiB > 64 KiB
    starts = [m for m in big if m["type"] == "http.response.start"]
    assert len(starts) == 1 and starts[0]["status"] == 413


def test_get_requests_are_untouched():
    app, _ = _body_app()
    assert TestClient(app).get("/api/v1/today").status_code == 200


def test_the_real_app_installs_the_body_cap_outermost():
    from app.main import app

    assert app.user_middleware[0].cls is BodyLimitMiddleware
    client = TestClient(app)
    assert client.post("/api/v1/auth/login", content=b"x" * (64 * 1024 + 1),
                       headers={"content-type": "application/json"}).status_code == 413


# --------------------------------------------------------------------------- 3. read cache key + overload


def _cache_app(status_code: int = 200):
    calls = {"n": 0}
    inner = FastAPI()

    @inner.get("/api/v1/mission-control")
    def mission(date: str | None = None):
        from fastapi.responses import JSONResponse

        calls["n"] += 1
        return JSONResponse({"n": calls["n"], "date": date}, status_code=status_code)

    inner.add_middleware(read_cache.ReadCacheMiddleware, prefix="/api/v1")
    return inner, calls


def test_undeclared_query_params_cannot_bust_the_cache():
    app, calls = _cache_app()
    client = TestClient(app)
    first = client.get("/api/v1/mission-control?date=2026-11-01")
    junk = client.get("/api/v1/mission-control?zz=1&date=2026-11-01&cb=987654")
    assert first.headers["x-aisp-cache"] == "MISS" and junk.headers["x-aisp-cache"] == "HIT"
    assert calls["n"] == 1 and junk.json() == first.json()
    assert list(read_cache.ACTIVE.cache.entries) == ["/api/v1/mission-control?date=2026-11-01"]


def test_the_warmer_and_requests_share_one_key():
    scope = read_cache.ReadCacheMiddleware._scope_for("/api/v1/prediction-markets/board?sport=MLB&date=2026-11-01")
    request = dict(scope, query_string=b"date=2026-11-01&x=1&sport=MLB")
    assert read_cache._key(scope, "/api/v1") == read_cache._key(request, "/api/v1") \
        == "/api/v1/prediction-markets/board?date=2026-11-01&sport=MLB"
    # Path-parameter routes use their prefix entry; repeated values keep their order (last value wins).
    rff = {"path": "/api/v1/markets/rff/abc", "query_string": b"market=A&junk=1&market=B"}
    assert read_cache._key(rff, "/api/v1") == "/api/v1/markets/rff/abc?market=A&market=B"


def test_every_declared_query_param_of_every_cached_route_is_in_the_key():
    from fastapi.routing import APIRoute

    from app.api.house_divided_routes import router as house_divided_router
    from app.api.routes import router

    def declared(dependant):
        names = {p.alias or p.name for p in dependant.query_params}
        for sub in dependant.dependencies:
            names |= declared(sub)
        return names

    cache = read_cache.ReadCache(prefix="/api/v1")
    checked = 0
    for r in [*router.routes, *house_divided_router.routes]:
        if not isinstance(r, APIRoute) or "GET" not in r.methods:
            continue
        path = "/api/v1" + r.path
        if not cache.cacheable({"type": "http", "method": "GET", "path": path}):
            continue
        rest = r.path.split("{", 1)[0] if "{" in r.path else r.path
        allowed = read_cache._allowed_params(rest + ("x" if rest.endswith("/") else ""))
        assert allowed is not None, f"{r.path} has no QUERY_PARAMS entry"
        assert declared(r.dependant) <= set(allowed), f"{r.path}: {declared(r.dependant) - set(allowed)}"
        checked += 1
    assert checked >= 30


def test_an_uncacheable_answer_is_not_computed_twice():
    app, calls = _cache_app(status_code=404)
    response = TestClient(app).get("/api/v1/mission-control?date=2026-11-01")
    assert response.status_code == 404 and response.headers["x-aisp-cache"] == "BYPASS"
    assert calls["n"] == 1 and response.json()["n"] == 1


def test_when_every_compute_slot_is_busy_a_miss_gets_503_or_the_last_copy():
    app, calls = _cache_app()
    client = TestClient(app)
    client.get("/api/v1/mission-control?date=2026-11-01")
    cache = read_cache.ACTIVE.cache
    cache.entries["/api/v1/mission-control?date=2026-11-01"].generated -= 3600     # far past its stale window
    cache.computing = read_cache.MAX_CONCURRENT_COMPUTES
    try:
        old = client.get("/api/v1/mission-control?date=2026-11-01")
        assert old.status_code == 200 and old.headers["x-aisp-cache"] == "STALE" and int(old.headers["age"]) >= 3600
        cold = client.get("/api/v1/mission-control?date=2026-11-02")
        assert cold.status_code == 503 and cold.headers["retry-after"] == "5"
        assert calls["n"] == 1
    finally:
        cache.computing = 0
    assert client.get("/api/v1/mission-control?date=2026-11-02").headers["x-aisp-cache"] == "MISS"
    assert cache.computing == 0


def test_warmed_entries_are_evicted_last(monkeypatch):
    monkeypatch.setattr(read_cache, "MAX_ENTRIES", 3)
    app, _ = _cache_app()
    client = TestClient(app)
    client.get("/api/v1/mission-control?date=2026-01-01")
    cache = read_cache.ACTIVE.cache                    # the middleware is built on the first request
    cache.warmed.add("/api/v1/mission-control?date=2026-01-01")
    for day in range(2, 9):
        client.get(f"/api/v1/mission-control?date=2026-01-0{day}")
    assert "/api/v1/mission-control?date=2026-01-01" in cache.entries and len(cache.entries) == 3


# --------------------------------------------------------------------------- 4. rate-limit keys


@pytest.mark.parametrize("raw,key", [
    ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
    ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
    ("[2001:db8:1:2::9]", "2001:db8:1:2::/64"),
    ("fe80::1%eth0", "fe80::/64"),
    ("::ffff:203.0.113.7", "203.0.113.7"),
    (" 203.0.113.7 ", "203.0.113.7"),
    ("not-an-ip", "not-an-ip"),
    ("x" * 500, "x" * 64),
    ("", "unknown"),
])
def test_client_addresses_are_keyed_by_network(raw, key):
    assert rl.normalize_client_address(raw) == key


def test_rotating_ipv6_addresses_share_one_budget():
    class _Req:
        def __init__(self, host):
            self.client = type("C", (), {"host": host})()
            self.headers = {}

    settings = type("S", (), {"rate_limit_trust_forwarded_for": False, "rate_limit_trust_cf_connecting_ip": False})()
    keys = {rl._client_key(_Req(f"2001:db8:aa:bb::{i:x}"), settings) for i in range(1, 200)}
    assert keys == {"2001:db8:aa:bb::/64"}


def test_cf_connecting_ip_is_normalised_too():
    class _Req:
        client = type("C", (), {"host": "127.0.0.1"})()
        headers = {"cf-connecting-ip": "2001:db8:5:6::77"}

    settings = type("S", (), {"rate_limit_trust_forwarded_for": False, "rate_limit_trust_cf_connecting_ip": True})()
    assert rl._client_key(_Req(), settings) == "2001:db8:5:6::/64"


def test_expired_buckets_are_pruned():
    now = {"t": 1000.0}
    limiter = rl.InMemoryRateLimiter(clock=lambda: now["t"])
    for i in range(500):
        limiter.check(f"k{i}", limit=5, window_seconds=60.0)
    assert len(limiter) == 500
    now["t"] += 120
    limiter.check("fresh", limit=5, window_seconds=60.0)
    assert len(limiter) == 1


def test_the_key_count_is_capped_soonest_expiring_first():
    now = {"t": 0.0}
    limiter = rl.InMemoryRateLimiter(clock=lambda: now["t"], max_keys=10)
    limiter.record("login-fail:owner")                       # a long-lived lockout bucket
    for i in range(100):
        now["t"] += 0.01
        limiter.check(f"rot{i}", limit=5, window_seconds=60.0)
    assert len(limiter) <= 11
    assert limiter.count("login-fail:owner", 900) == 1


def test_a_recorded_lockout_outlives_the_minute_buckets():
    now = {"t": 0.0}
    limiter = rl.InMemoryRateLimiter(clock=lambda: now["t"])
    limiter.record("login-fail:a@b:1.2.3.4")
    now["t"] += 600
    limiter.check("other", limit=5, window_seconds=60.0)      # triggers a prune
    assert limiter.count("login-fail:a@b:1.2.3.4", 900) == 1
