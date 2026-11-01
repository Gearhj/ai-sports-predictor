"""One bounded, thread-safe TTL + LRU cache for the preview feeds' provider reads (security review 11-01).

The feeds used to keep plain module-level dicts keyed by provider URL that were never evicted, so every new date,
event id or athlete id asked for by an unauthenticated caller grew process memory for good. This cache holds at most
``max_entries`` items and at most ``max_weight`` units of weight (the feeds weigh an item by the byte length of the
provider response, since one MLB live feed is megabytes): expired items are dropped on insert, then the least
recently used ones.

TTL semantics are unchanged from the old dicts: the caller passes its freshness window on every lookup (the same URL
may be read with a short window while a game is live and a long one after it ends), and the item's insert time is
compared against it. The TTL given at insert only decides when an item is old enough to be evicted early.

``fetch_limit`` also caps how many outbound provider requests run at once across all feeds, so a burst of cache
misses queues instead of fanning out.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from contextlib import contextmanager
from typing import Any

_MISSING = object()


class BoundedTTLCache:
    def __init__(self, max_entries: int = 512, *, max_weight: int = 64 * 1024 * 1024,
                 clock: Callable[[], float] = time.time) -> None:
        if max_entries < 1 or max_weight < 1:
            raise ValueError("max_entries and max_weight must be >= 1")
        self.max_entries = max_entries
        self.max_weight = max_weight
        self._clock = clock
        # key -> (stored_at, ttl, weight, value)
        self._items: OrderedDict[Hashable, tuple[float, float, int, Any]] = OrderedDict()
        self._weight = 0
        self._lock = threading.Lock()

    def now(self) -> float:
        return self._clock()

    @property
    def weight(self) -> int:
        with self._lock:
            return self._weight

    def get(self, key: Hashable, ttl: float, default: Any = None) -> Any:
        """The value stored for ``key`` if it is younger than ``ttl`` seconds, else ``default``."""
        now = self._clock()
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return default
            stored_at, _, _, value = hit
            if now - stored_at >= ttl:
                return default
            self._items.move_to_end(key)
            return value

    def set(self, key: Hashable, value: Any, ttl: float, *, weight: int = 1, stored_at: float | None = None) -> None:
        """Store ``value``. An item heavier than the whole budget is not kept at all."""
        now = self._clock()
        weight = max(1, int(weight))
        with self._lock:
            self._drop(key)
            if weight > self.max_weight:
                return
            self._items[key] = (now if stored_at is None else stored_at, float(ttl), weight, value)
            self._weight += weight
            if len(self._items) > self.max_entries or self._weight > self.max_weight:
                for k in [k for k, (at, life, _, _) in self._items.items() if now - at >= life and k != key]:
                    self._drop(k)
            while len(self._items) > self.max_entries or self._weight > self.max_weight:
                oldest = next(iter(self._items))
                self._drop(oldest)

    def _drop(self, key: Hashable) -> None:
        old = self._items.pop(key, None)
        if old is not None:
            self._weight -= old[2]

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __contains__(self, key: Hashable) -> bool:
        with self._lock:
            return key in self._items

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._weight = 0


# The one cache every preview feed (feeds.py, court_live.py, ufc_card.py) reads through. 512 items and 48 MB of raw
# provider JSON is far above a busy night's working set (15 MLB live feeds of 1-3 MB each, a UFC card's ~60 fighter
# reads, a few scoreboards) and a hard ceiling on what anonymous callers can make the process hold.
FEED_CACHE = BoundedTTLCache(512, max_weight=48 * 1024 * 1024)

MAX_CONCURRENT_FETCHES = 8
_fetch_slots = threading.BoundedSemaphore(MAX_CONCURRENT_FETCHES)


@contextmanager
def fetch_limit(timeout: float = 30.0):
    """Hold one of the shared outbound-request slots; raise TimeoutError if none frees up in ``timeout`` seconds."""
    if not _fetch_slots.acquire(timeout=timeout):
        raise TimeoutError("too many provider requests in flight")
    try:
        yield
    finally:
        _fetch_slots.release()


def cached_fetch(cache: BoundedTTLCache, key: Hashable, ttl: float, fetch: Callable[[], tuple[Any, int]]) -> Any:
    """``cache[key]`` while fresh, else ``fetch()`` under the shared outbound limit. ``fetch`` returns
    ``(value, weight)``; the value is stored with ``ttl``, timed from when the fetch started."""
    hit = cache.get(key, ttl, _MISSING)
    if hit is not _MISSING:
        return hit
    started = cache.now()
    with fetch_limit():
        data, weight = fetch()
    cache.set(key, data, ttl, weight=weight, stored_at=started)
    return data
