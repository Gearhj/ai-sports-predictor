"""Process-local sliding-window rate limiting for abuse-sensitive routes.

This is a *non-distributed* control: the counters live in a single process.
It is a bounded, dependency-free abuse brake suitable for local and private
beta deployments (brute-force, credential-stuffing, and denial-of-wallet on
the admin job trigger). A multi-instance public deployment MUST replace the
in-memory store with a shared backend (e.g. Redis) and terminate requests
behind a trusted proxy that sets a reliable client address. See the deep
security audit, section 11.

Design choices that matter for security:

* The client key defaults to the socket peer (``request.client.host``) and
  never trusts ``X-Forwarded-For`` unless ``rate_limit_trust_forwarded_for``
  is explicitly enabled for a known, trusted-proxy deployment. Spoofable
  headers must not silently widen an attacker's budget.
* The limiter fails *open* on internal error: an availability control must
  not become a self-inflicted denial of service if bookkeeping breaks.
"""

from __future__ import annotations

import ipaddress
import threading
from collections import deque
from collections.abc import Callable
from time import monotonic

from fastapi import HTTPException, Request, status

from app.api.dependencies import AppSettings


#: Bounded key growth (security review 11-01). Each bucket remembers the longest window it was checked or counted
#: with (a bucket only ever ``record``-ed keeps ``RECORD_RETENTION_SECONDS``, the longest window config allows, so a
#: login lockout is never forgotten early). Every ``PRUNE_EVERY_SECONDS`` buckets that are empty or whose newest hit
#: is past that retention are dropped; past ``MAX_KEYS`` live buckets the ones that would expire soonest go first,
#: so rotating client addresses cannot grow the dict without bound.
PRUNE_EVERY_SECONDS = 60.0
MAX_KEYS = 50_000
RECORD_RETENTION_SECONDS = 86_400.0


class InMemoryRateLimiter:
    """Thread-safe sliding-window counter keyed by an opaque string."""

    def __init__(self, *, clock: Callable[[], float] = monotonic, max_keys: int = MAX_KEYS,
                 prune_every_seconds: float = PRUNE_EVERY_SECONDS) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._retention: dict[str, float] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._max_keys = max_keys
        self._prune_every = prune_every_seconds
        self._last_prune = clock()

    def _keep_for(self, key: str, window_seconds: float) -> None:
        if window_seconds > self._retention.get(key, 0.0):
            self._retention[key] = window_seconds

    def _prune_locked(self, now: float) -> None:
        if now - self._last_prune < self._prune_every and len(self._hits) <= self._max_keys:
            return
        self._last_prune = now

        def expires(key: str) -> float:
            bucket = self._hits[key]
            return bucket[-1] + self._retention.get(key, RECORD_RETENTION_SECONDS) if bucket else float("-inf")

        for key in [k for k in self._hits if expires(k) <= now]:
            self._drop_locked(key)
        overflow = len(self._hits) - self._max_keys
        if overflow > 0:
            for key in sorted(self._hits, key=expires)[:overflow]:
                self._drop_locked(key)

    def _drop_locked(self, key: str) -> None:
        self._hits.pop(key, None)
        self._retention.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._hits)

    def check(self, key: str, *, limit: int, window_seconds: float) -> float | None:
        """Register one hit. Return ``None`` if allowed, else retry-after seconds."""

        if limit <= 0 or window_seconds <= 0:
            return None
        now = self._clock()
        cutoff = now - window_seconds
        with self._lock:
            self._prune_locked(now)
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = deque()
                self._hits[key] = bucket
            self._keep_for(key, window_seconds)
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return max(window_seconds - (now - bucket[0]), 0.0)
            bucket.append(now)
            return None

    def count(self, key: str, window_seconds: float) -> int:
        """How many hits ``key`` has inside the window (does not register one)."""
        cutoff = self._clock() - window_seconds
        with self._lock:
            bucket = self._hits.get(key)
            if not bucket:
                return 0
            self._keep_for(key, window_seconds)
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            return len(bucket)

    def record(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            self._hits.setdefault(key, deque()).append(now)
            self._retention.setdefault(key, RECORD_RETENTION_SECONDS)

    def clear(self, key: str) -> None:
        with self._lock:
            self._drop_locked(key)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._retention.clear()


_limiter = InMemoryRateLimiter()


def get_rate_limiter() -> InMemoryRateLimiter:
    return _limiter


def normalize_client_address(value: str) -> str:
    """One budget per client network, never per rotated address (security review 11-01).

    IPv6 clients usually control a whole /64, so each address in it would otherwise get its own budget: an IPv6
    address is keyed by its /64 network, an IPv4-mapped IPv6 address by its IPv4 address, an IPv4 address as is.
    Anything that does not parse is returned trimmed (bounded to 64 characters). Never raises.
    """

    try:
        text = (value or "").strip()
        if text.startswith("[") and "]" in text:            # "[2001:db8::1]:443"
            text = text[1:text.index("]")]
        if "%" in text:                                      # zone id
            text = text.split("%", 1)[0]
        address = ipaddress.ip_address(text)
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped is not None:
                return str(address.ipv4_mapped)
            return str(ipaddress.IPv6Network((address, 64), strict=False))
        return str(address)
    except Exception:  # noqa: BLE001 - a bad value is keyed as given, never routed to the fail-open path
        try:
            return str(value).strip()[:64] or "unknown"
        except Exception:  # noqa: BLE001
            return "unknown"


def _client_key(request: Request, settings) -> str:
    return normalize_client_address(_raw_client_address(request, settings))


def _raw_client_address(request: Request, settings) -> str:
    client = request.client
    local = client is not None and client.host in ("127.0.0.1", "::1")
    if getattr(settings, "rate_limit_trust_cf_connecting_ip", False) and local:
        cf = (request.headers.get("cf-connecting-ip") or "").strip()
        if cf:
            return cf
    if settings.rate_limit_trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
    client = request.client
    return client.host if client is not None else "unknown"


def _limit_for(settings, category: str) -> int:
    if category == "auth":
        return settings.auth_rate_limit_per_minute
    if category == "admin":
        return settings.admin_rate_limit_per_minute
    if category == "compute":
        return getattr(settings, "compute_rate_limit_per_minute", 30)
    return 0


def rate_limit(scope: str, *, category: str) -> Callable[..., None]:
    """Build a FastAPI dependency enforcing the ``category`` policy for ``scope``."""

    def dependency(request: Request, settings: AppSettings) -> None:
        if not settings.rate_limit_enabled:
            return
        try:
            limit = _limit_for(settings, category)
            key = f"{scope}:{_client_key(request, settings)}"
            retry_after = get_rate_limiter().check(
                key, limit=limit, window_seconds=60.0
            )
        except Exception:  # noqa: BLE001 - availability control must fail open
            return
        if retry_after is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down and retry shortly.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    return dependency


__all__ = ["InMemoryRateLimiter", "get_rate_limiter", "normalize_client_address", "rate_limit"]
