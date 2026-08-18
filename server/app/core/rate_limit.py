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

import threading
from collections import deque
from collections.abc import Callable
from time import monotonic

from fastapi import HTTPException, Request, status

from app.api.dependencies import AppSettings


class InMemoryRateLimiter:
    """Thread-safe sliding-window counter keyed by an opaque string."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def check(self, key: str, *, limit: int, window_seconds: float) -> float | None:
        """Register one hit. Return ``None`` if allowed, else retry-after seconds."""

        if limit <= 0 or window_seconds <= 0:
            return None
        now = self._clock()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._hits.get(key)
            if bucket is None:
                bucket = deque()
                self._hits[key] = bucket
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return max(window_seconds - (now - bucket[0]), 0.0)
            bucket.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_limiter = InMemoryRateLimiter()


def get_rate_limiter() -> InMemoryRateLimiter:
    return _limiter


def _client_key(request: Request, settings) -> str:
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


__all__ = ["InMemoryRateLimiter", "get_rate_limiter", "rate_limit"]
