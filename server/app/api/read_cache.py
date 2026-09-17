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
For an allowlisted GET path (keyed by path + query):

* fresh (younger than ``FRESH_SECONDS``): served from memory;
* stale (up to ``MAX_STALE_SECONDS``): served from memory immediately, and one
  background recomputation starts (single flight per key);
* older, or absent: computed synchronously and stored.

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
MAX_ENTRIES = 256
MAX_BODY_BYTES = 4 * 1024 * 1024

#: Path prefixes after the API prefix. Each is a read model of persisted state
#: that does not depend on who is asking.
ALLOWLIST: tuple[str, ...] = (
    "/today", "/games/slate", "/nfl/markets/", "/nfl/weeks", "/experiment/comparison",
    "/prediction-markets/board", "/experiment/funnel", "/experiment/qualification-funnel",
    "/sports/readiness", "/sports/registry", "/markets/board", "/markets/rff", "/mlb/totals",
    "/bankroll", "/command-center/", "/slate-status", "/coverage/price-funnel",
    "/research/model-era", "/research/model-performance", "/research/nfl-scorecard", "/show-board", "/cfb/research", "/price-coverage", "/public/config",
)


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
    stats: dict[str, int] = field(default_factory=lambda: {"HIT": 0, "STALE": 0, "MISS": 0, "BYPASS": 0})

    def cacheable(self, scope: dict[str, Any]) -> bool:
        if scope.get("type") != "http" or scope.get("method") != "GET":
            return False
        path = scope.get("path", "")
        if not path.startswith(self.prefix):
            return False
        rest = path[len(self.prefix):]
        return any(rest == p.rstrip("/") or rest.startswith(p) for p in ALLOWLIST)


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


def _key(scope: dict[str, Any]) -> str:
    return f"{scope.get('path')}?{scope.get('query_string', b'').decode('latin-1')}"


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
        key = _key(scope)
        entry = self.cache.entries.get(key)
        if entry is not None and time.time() - entry.generated < FRESH_SECONDS or key in self.cache.refreshing:
            return
        self.cache.refreshing.add(key)
        try:
            computed = await self._compute(scope)
            if computed is not None:
                self._store(key, computed)
        finally:
            self.cache.refreshing.discard(key)

    async def fetch_json(self, url: str) -> Any:
        import json

        await self.warm(url)
        entry = self.cache.entries.get(_key(self._scope_for(url)))
        return json.loads(entry.body) if entry else None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if not self.cache.cacheable(scope):
            await self.app(scope, receive, send)
            return
        key = _key(scope)
        entry = self.cache.entries.get(key)
        now = time.time()
        if entry is not None and now - entry.generated <= MAX_STALE_SECONDS:
            fresh = now - entry.generated <= FRESH_SECONDS
            state = "HIT" if fresh else "STALE"
            if not fresh and key not in self.cache.refreshing:
                self.cache.refreshing.add(key)
                asyncio.create_task(self._refresh(key, scope))
            self.cache.stats[state] += 1
            await self._send(entry, state, scope, send)
            return
        computed = await self._compute(scope)
        if computed is None:  # not cacheable after all (error, too large, streamed)
            self.cache.stats["BYPASS"] += 1
            await self.app(scope, receive, send)
            return
        self._store(key, computed)
        self.cache.stats["MISS"] += 1
        await self._send(computed, "MISS", scope, send)

    async def _refresh(self, key: str, scope: dict[str, Any]) -> None:
        try:
            computed = await self._compute(scope)
            if computed is not None:
                self._store(key, computed)
        finally:
            self.cache.refreshing.discard(key)

    def _store(self, key: str, entry: _Entry) -> None:
        if len(self.cache.entries) >= MAX_ENTRIES and key not in self.cache.entries:
            oldest = min(self.cache.entries, key=lambda k: self.cache.entries[k].generated)
            self.cache.entries.pop(oldest, None)
        self.cache.entries[key] = entry

    async def _compute(self, scope: dict[str, Any]) -> _Entry | None:
        """Run the real endpoint with a private receive/send pair and capture it."""

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
            return None
        kept = [(k, v) for k, v in headers if k.lower() in (b"content-type",)]
        return _Entry(body=body, headers=kept, generated=started,
                      etag='"' + hashlib.sha1(body).hexdigest() + '"')

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
