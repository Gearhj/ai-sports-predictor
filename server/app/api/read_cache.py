"""Stale-while-revalidate cache for expensive public read models.

WHY
---
Several first-viewport reads assemble their answer from hundreds of queries
(`/prediction-markets/board` 553, `/nfl/markets` 232, `/today` 181) or a slow
aggregate (`/experiment/comparison`, ~6 s of database time). They read persisted
state only, change on the collectors' multi-minute cadence, and do not vary by
caller. Recomputing them on every navigation made pages wait seconds.

WHAT IT DOES
------------
For an allowlisted GET path (keyed by path + its declared query parameters, see ``QUERY_PARAMS``):

* fresh (younger than ``FRESH_SECONDS``): served from memory;
* stale (up to ``MAX_STALE_SECONDS``): served from memory immediately, and one
  background recomputation starts (single flight per key);
* older, or absent: computed synchronously and stored; concurrent misses for one
  key share that one computation (single flight).

Every response carries its real generation time -- ``X-AISP-Generated-At`` and
``Age`` -- plus ``X-AISP-Cache`` (HIT, STALE or MISS) and an ``ETag``;
``If-None-Match`` returns 304. A cached body is never restamped as current.

WHAT IT NEVER DOES
------------------
Cache a non-GET, a non-200, anything outside the allowlist, or anything that
calls a provider: the allowlisted endpoints read stored AISP state only (proved
by ``scripts/profile_read_endpoints.py``: zero provider-ledger rows). The
declared TTL is the invalidation rule; nothing hides how old the data is.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

FRESH_SECONDS = 30.0
MAX_STALE_SECONDS = 15 * 60.0
#: Read models whose labels describe finite windows (decision windows, quote
#: freshness, leases) may not be served as stale for the full default window.
#: Past this age the request recomputes instead of serving the old body.
MAX_STALE_BY_PATH: dict[str, float] = {"/sports/readiness": 120.0,
                                       # Mission Control's chips name live clocks and countdowns.
                                       "/mission-control": 90.0,
                                       # Compete standings move when a wager is placed or settles.
                                       "/house-divided": 60.0}
MAX_ENTRIES = 256
MAX_BODY_BYTES = 4 * 1024 * 1024

#: Path prefixes after the API prefix. Each is a read model of persisted state
#: that does not depend on who is asking.
ALLOWLIST: tuple[str, ...] = (
    "/today", "/games/slate", "/nfl/markets/", "/nfl/weeks", "/experiment/comparison",
    "/prediction-markets/board", "/experiment/funnel", "/experiment/qualification-funnel",
    "/sports/readiness", "/sports/registry", "/markets/board", "/markets/rff", "/mlb/totals",
    "/bankroll", "/command-center/", "/slate-status", "/coverage/price-funnel",
    "/research/model-era", "/research/model-performance", "/research/wager-results", "/research/nfl-scorecard", "/show-board", "/cfb/research", "/price-coverage", "/public/config",
    "/mission-control", "/availability",
)
#: Paths cached only as an exact match (never their sub-paths). Owner 10-31 tester launch: Compete's public
#: standings (~2 s, the slowest home-page read) take no caller; its /account and /market-board children are
#: per-person or price-live and stay uncached.
EXACT: tuple[str, ...] = ("/house-divided",)

#: The query parameters that key each cached read (security review 11-01: a raw query-string key let junk
#: parameters force cold 6-12 s recomputes and evict warmed entries). Each list is exactly the query parameters the
#: endpoint DECLARES (FastAPI ignores every other one, so dropping it from the key can never change the answer);
#: ``test_security_20261101_platform`` fails if a cached route declares a parameter missing here. Keys are the path
#: after the API prefix; a trailing "/" entry covers that prefix's sub-paths (path parameters).
QUERY_PARAMS: dict[str, tuple[str, ...]] = {
    "/public/config": (), "/sports/registry": (), "/sports/readiness": (),
    "/today": ("date", "sport"), "/games/slate": ("date",), "/markets/board": ("date",),
    "/bankroll": ("experiment",),
    "/markets/rff": ("date", "league", "limit", "market"), "/markets/rff/": ("include_tape", "market"),
    "/coverage/price-funnel": ("event_date", "league"), "/research/model-era": ("event_date",),
    "/research/nfl-scorecard": (), "/research/model-performance": ("league", "test_data"),
    "/research/model-performance/overview": ("forecast_type", "lead", "model", "sport", "test_data"),
    "/research/wager-results": ("date_from", "date_to", "engine", "era", "sport"),
    "/show-board": ("date",), "/show-board/game": ("date", "game_key"), "/show-board/contract": (),
    "/experiment/comparison": (), "/experiment/funnel": ("days",),
    "/experiment/qualification-funnel": ("date", "policy"),
    "/nfl/markets/": (), "/nfl/weeks": (), "/nfl/weeks/": (),
    "/prediction-markets/board": ("date", "sport"), "/mlb/totals": ("date", "overs_only", "scenario_line"),
    "/slate-status": ("date", "timezone"),
    "/command-center/ledger": (), "/command-center/decisions": ("date",), "/command-center/markets": ("date",),
    "/command-center/replay": (), "/command-center/assessments": ("date",),
    "/availability": ("date",), "/mission-control": ("date",), "/cfb/research": ("date",),
    "/price-coverage": ("date",), "/house-divided": ("sport",),
}
#: At most this many cache computations (MISS, background refresh, warm) run at once. A MISS that finds every slot
#: busy is served the last copy it has (labelled STALE, with its real age) or 503 + Retry-After instead of queueing
#: another 6-12 s recompute on the database pool.
MAX_CONCURRENT_COMPUTES = 6
OVERLOAD_RETRY_AFTER_SECONDS = 5


@dataclass
class _Entry:
    body: bytes
    headers: list[tuple[bytes, bytes]]
    generated: float
    etag: str


@dataclass
class ReadCache:
    prefix: str
    entries: dict[str, _Entry] = field(default_factory=dict)
    refreshing: set[str] = field(default_factory=set)
    #: Synchronous (MISS) computations in flight, per key: concurrent misses share one.
    inflight: dict[str, "asyncio.Future[_Captured]"] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=lambda: {"HIT": 0, "STALE": 0, "MISS": 0, "BYPASS": 0,
                                                           "OVERLOAD": 0})
    #: Computations running now (bounded by ``MAX_CONCURRENT_COMPUTES``). This is the semaphore: it is taken
    #: synchronously before a computation is scheduled, so the bound can never overshoot.
    computing: int = 0
    #: Keys the warmers keep; evicted only after every other entry.
    warmed: set[str] = field(default_factory=set)

    def cacheable(self, scope: dict[str, Any]) -> bool:
        if scope.get("type") != "http" or scope.get("method") != "GET":
            return False
        path = scope.get("path", "")
        if not path.startswith(self.prefix):
            return False
        rest = path[len(self.prefix):]
        return rest in EXACT or any(rest == p.rstrip("/") or rest.startswith(p) for p in ALLOWLIST)


#: The running middleware, so the warmer can reach its cache.
ACTIVE: "ReadCacheMiddleware | None" = None
WARM_EVERY_SECONDS = 300.0


def first_viewport_paths(today: str) -> list[str]:
    """The reads the redesigned first viewports request for today's slate."""

    return [
        "/sports/readiness", "/sports/registry", "/public/config", "/today",
        "/command-center/ledger", f"/command-center/markets?date={today}",
        f"/command-center/decisions?date={today}", f"/games/slate?date={today}", "/games/slate",
        f"/markets/board?date={today}", "/nfl/weeks", "/bankroll?experiment=v2", "/experiment/funnel",
        "/experiment/comparison", f"/prediction-markets/board?date={today}&sport=MLB",
        f"/slate-status?date={today}", "/markets/rff?limit=1",
        "/research/model-performance/overview",
        "/research/model-performance?league=MLB", "/research/model-performance?league=NFL",
        "/research/nfl-scorecard",
    ]


async def warm_forever(prefix: str = "/api/v1") -> None:
    """Keep today's first-viewport read models computed. Reads only; never a provider."""

    from app.core.time import local_today

    await asyncio.sleep(20)
    while True:
        cache = ACTIVE
        if cache is not None:
            today = local_today("America/New_York").isoformat()
            paths = first_viewport_paths(today)
            try:  # the current NFL week, whose board is the slowest read
                weeks = await cache.fetch_json(f"{prefix}/nfl/weeks")
                current = (weeks or {}).get("current") or {}
                if current.get("season") and current.get("week"):
                    paths.append(f"/nfl/markets/{current['season']}/{current['week']}")
            except Exception:  # noqa: BLE001 - warming is best effort
                pass
            for path in paths:
                try:
                    await cache.warm(prefix + path)
                except Exception:  # noqa: BLE001
                    continue
        await asyncio.sleep(WARM_EVERY_SECONDS)


#: Mission Control recomputes in 7-12 s cold and may be served stale for only 90 s, so the 5-minute warmer above
#: left most page loads waiting on a cold recompute (owner 10-26: "the page loading time is still very long").
#: Its own loop keeps yesterday, today and tomorrow (the days it serves) younger than its stale window.
MISSION_WARM_EVERY_SECONDS = 60.0


async def warm_mission_control_forever(prefix: str = "/api/v1") -> None:
    """Keep Mission Control's three served days computed. Reads stored state only; never a provider."""

    from datetime import timedelta

    from app.core.time import local_today

    await asyncio.sleep(30)
    while True:
        cache = ACTIVE
        if cache is not None:
            today = local_today("America/New_York")
            for day in (today, today - timedelta(days=1), today + timedelta(days=1)):
                try:
                    await cache.warm(f"{prefix}/mission-control?date={day.isoformat()}")
                except Exception:  # noqa: BLE001 - warming is best effort
                    continue
        await asyncio.sleep(MISSION_WARM_EVERY_SECONDS)


def _allowed_params(rest: str) -> tuple[str, ...] | None:
    """The declared query parameters for a path after the API prefix; None = unknown path (keep every one)."""

    rest = rest.rstrip("/") or "/"
    if rest in QUERY_PARAMS:
        return QUERY_PARAMS[rest]
    best = None
    for prefix in QUERY_PARAMS:
        if prefix.endswith("/") and rest.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return QUERY_PARAMS[best] if best is not None else None


def normalized_query(scope: dict[str, Any], prefix: str = "/api/v1") -> str:
    """The query string reduced to the endpoint's declared parameters, ordered by name (each name's values keep
    their order, so "last value wins" is unchanged)."""

    from urllib.parse import parse_qsl, urlencode

    raw = scope.get("query_string", b"") or b""
    if isinstance(raw, bytes):
        raw = raw.decode("latin-1")
    path = scope.get("path", "") or ""
    rest = path[len(prefix):] if path.startswith(prefix) else path
    allowed = _allowed_params(rest)
    pairs = parse_qsl(raw, keep_blank_values=True)
    if allowed is not None:
        pairs = [(k, v) for k, v in pairs if k in allowed]
    pairs.sort(key=lambda kv: kv[0])
    return urlencode(pairs)


def _key(scope: dict[str, Any], prefix: str | None = None) -> str:
    if prefix is None:
        prefix = ACTIVE.cache.prefix if ACTIVE is not None else "/api/v1"
    return f"{scope.get('path')}?{normalized_query(scope, prefix)}"


@dataclass
class _Captured:
    """One endpoint run, kept whole so a response that cannot be cached is still answered without running twice."""

    status: int
    headers: list[tuple[bytes, bytes]]
    body: bytes
    entry: "_Entry | None"


class ReadCacheMiddleware:
    def __init__(self, app: Any, prefix: str = "/api/v1") -> None:
        global ACTIVE
        self.app = app
        self.cache = ReadCache(prefix=prefix)
        ACTIVE = self

    @staticmethod
    def _scope_for(url: str) -> dict[str, Any]:
        path, _, query = url.partition("?")
        return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
                "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
                "query_string": query.encode(), "headers": [(b"host", b"127.0.0.1")],
                "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 8010)}

    async def warm(self, url: str) -> None:
        scope = self._scope_for(url)
        if not self.cache.cacheable(scope):
            return
        key = self._cache_key(scope)
        self.cache.warmed.add(key)
        entry = self.cache.entries.get(key)
        if entry is not None and time.time() - entry.generated < FRESH_SECONDS or key in self.cache.refreshing:
            return
        # The warmer waits (up to a minute) for a free slot rather than skipping, and takes only one.
        for _ in range(120):
            if self.cache.computing < MAX_CONCURRENT_COMPUTES:
                break
            await asyncio.sleep(0.5)
        else:
            return
        if key in self.cache.refreshing:
            return
        self.cache.refreshing.add(key)
        self.cache.computing += 1
        try:
            computed = await self._compute(self._normalized_scope(scope))
            if computed.entry is not None:
                self._store(key, computed.entry)
        finally:
            self.cache.computing -= 1
            self.cache.refreshing.discard(key)

    async def fetch_json(self, url: str) -> Any:
        import json

        await self.warm(url)
        entry = self.cache.entries.get(self._cache_key(self._scope_for(url)))
        return json.loads(entry.body) if entry else None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if not self.cache.cacheable(scope):
            await self.app(scope, receive, send)
            return
        key = self._cache_key(scope)
        entry = self.cache.entries.get(key)
        now = time.time()
        rest = scope.get("path", "")[len(self.cache.prefix):]
        max_stale = MAX_STALE_BY_PATH.get(rest.rstrip("/"), MAX_STALE_SECONDS)
        if entry is not None and now - entry.generated <= max_stale:
            fresh = now - entry.generated <= FRESH_SECONDS
            state = "HIT" if fresh else "STALE"
            if (not fresh and key not in self.cache.refreshing
                    and self.cache.computing < MAX_CONCURRENT_COMPUTES):
                self.cache.refreshing.add(key)
                self.cache.computing += 1
                asyncio.create_task(self._refresh(key, self._normalized_scope(scope)))
            self.cache.stats[state] += 1
            await self._send(entry, state, scope, send)
            return
        if key not in self.cache.inflight and self.cache.computing >= MAX_CONCURRENT_COMPUTES:
            # Every compute slot is busy: answer with the last copy (honestly aged) or ask the client to retry.
            self.cache.stats["OVERLOAD"] += 1
            if entry is not None:
                await self._send(entry, "STALE", scope, send)
            else:
                await self._send_overloaded(send)
            return
        computed = await self._compute_once(key, scope)
        if computed.entry is None:  # not cacheable (error, too large): answer with the run already made
            self.cache.stats["BYPASS"] += 1
            await self._replay(computed, send)
            return
        self._store(key, computed.entry)
        self.cache.stats["MISS"] += 1
        await self._send(computed.entry, "MISS", scope, send)

    def _cache_key(self, scope: dict[str, Any]) -> str:
        return _key(scope, self.cache.prefix)

    def _normalized_scope(self, scope: dict[str, Any]) -> dict[str, Any]:
        """The scope the endpoint runs with: exactly the query the cache key names."""

        inner = dict(scope)
        inner["query_string"] = normalized_query(scope, self.cache.prefix).encode("latin-1")
        return inner

    @staticmethod
    async def _send_overloaded(send: Any) -> None:
        body = b'{"detail":"This page is busy. Please retry in a few seconds."}'
        await send({"type": "http.response.start", "status": 503, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"retry-after", str(OVERLOAD_RETRY_AFTER_SECONDS).encode()), (b"cache-control", b"no-store"),
            (b"x-aisp-cache", b"OVERLOAD")]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _replay(captured: "_Captured", send: Any) -> None:
        headers = [(k, v) for k, v in captured.headers if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(captured.body)).encode()))
        headers.append((b"x-aisp-cache", b"BYPASS"))
        await send({"type": "http.response.start", "status": captured.status or 500, "headers": headers})
        await send({"type": "http.response.body", "body": captured.body})

    async def _compute_once(self, key: str, scope: dict[str, Any]) -> "_Captured":
        """Single flight for a MISS: a hover prefetch and the page load (or two tabs) asking for the same
        uncached key share ONE computation instead of each building it. The body does not vary by caller
        (the cache keys by path + query only), so every waiter can be answered with it. A waiter that
        disconnects does not cancel the shared computation (``shield``)."""

        running = self.cache.inflight.get(key)
        if running is None:
            self.cache.computing += 1           # the slot is taken before scheduling: the bound never overshoots
            running = asyncio.ensure_future(self._compute(self._normalized_scope(scope)))
            self.cache.inflight[key] = running

            def _done(task: "asyncio.Future[_Captured]", k: str = key) -> None:
                self.cache.computing -= 1
                if self.cache.inflight.get(k) is task:
                    self.cache.inflight.pop(k, None)
                if not task.cancelled():
                    task.exception()        # retrieved here, so a failure nobody awaited is never logged as lost
            running.add_done_callback(_done)
        return await asyncio.shield(running)

    async def _refresh(self, key: str, scope: dict[str, Any]) -> None:
        """Background refresh of a stale entry; the caller already took its compute slot."""

        try:
            computed = await self._compute(scope)
            if computed.entry is not None:
                self._store(key, computed.entry)
        finally:
            self.cache.computing -= 1
            self.cache.refreshing.discard(key)

    def _store(self, key: str, entry: _Entry) -> None:
        if len(self.cache.entries) >= MAX_ENTRIES and key not in self.cache.entries:
            # Entries the warmers keep go last, so a flood of new keys cannot push out the first viewports.
            pool = [k for k in self.cache.entries if k not in self.cache.warmed] or list(self.cache.entries)
            oldest = min(pool, key=lambda k: self.cache.entries[k].generated)
            self.cache.entries.pop(oldest, None)
        self.cache.entries[key] = entry

    async def _compute(self, scope: dict[str, Any]) -> "_Captured":
        """Run the real endpoint with a private receive/send pair and capture it (``entry`` None = not cacheable)."""

        started = time.time()
        status = {"code": 0}
        headers: list[tuple[bytes, bytes]] = []
        chunks: list[bytes] = []
        inner_scope = dict(scope)
        # The cache owns validation; the endpoint always renders a full body.
        inner_scope["headers"] = [(k, v) for k, v in scope.get("headers", []) if k.lower() != b"if-none-match"]

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def capture(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers.extend(message.get("headers", []))
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(inner_scope, receive, capture)
        body = b"".join(chunks)
        if status["code"] != 200 or len(body) > MAX_BODY_BYTES:
            return _Captured(status=status["code"], headers=headers, body=body, entry=None)
        kept = [(k, v) for k, v in headers if k.lower() in (b"content-type",)]
        entry = _Entry(body=body, headers=kept, generated=started, etag='"' + hashlib.sha1(body).hexdigest() + '"')
        return _Captured(status=200, headers=headers, body=body, entry=entry)

    async def _send(self, entry: _Entry, state: str, scope: dict[str, Any], send: Any) -> None:
        age = max(0, int(time.time() - entry.generated))
        generated = datetime.fromtimestamp(entry.generated, UTC).isoformat()
        common = [
            (b"etag", entry.etag.encode()),
            (b"age", str(age).encode()),
            (b"x-aisp-generated-at", generated.encode()),
            (b"x-aisp-cache", state.encode()),
            (b"cache-control", b"no-cache"),
            (b"access-control-expose-headers", b"X-AISP-Generated-At, X-AISP-Cache, Age, ETag"),
        ]
        match = dict(scope.get("headers", [])).get(b"if-none-match")
        if match is not None and match.decode("latin-1") == entry.etag:
            await send({"type": "http.response.start", "status": 304, "headers": common})
            await send({"type": "http.response.body", "body": b""})
            return
        await send({"type": "http.response.start", "status": 200,
                    "headers": entry.headers + common + [(b"content-length", str(len(entry.body)).encode())]})
        await send({"type": "http.response.body", "body": entry.body})
