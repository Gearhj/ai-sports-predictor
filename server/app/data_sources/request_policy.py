from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import TypeVar

import httpx

from .base import SourceError


T = TypeVar("T")


class RequestCapExceeded(SourceError):
    """Raised before transport when the physical-attempt budget is exhausted."""

    def __init__(self, source: str, maximum: int) -> None:
        super().__init__(
            source,
            f"Request cap reached for {source} ({maximum}/{maximum})",
            retryable=False,
        )
        self.maximum = maximum


class CircuitOpenError(SourceError):
    def __init__(self, source: str, threshold: int) -> None:
        super().__init__(
            source,
            f"Provider circuit is open after {threshold} consecutive rejections",
            retryable=False,
        )
        self.threshold = threshold


@dataclass(frozen=True)
class ConservativeRequestPolicy:
    max_attempts: int = 3
    minimum_interval_seconds: float = 1.0
    base_backoff_seconds: float = 1.0
    maximum_backoff_seconds: float = 8.0
    circuit_rejection_threshold: int = 3

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if self.minimum_interval_seconds < 0:
            raise ValueError("minimum_interval_seconds must be non-negative")
        if self.base_backoff_seconds < 0 or self.maximum_backoff_seconds < 0:
            raise ValueError("backoff values must be non-negative")
        if self.maximum_backoff_seconds < self.base_backoff_seconds:
            raise ValueError("maximum_backoff_seconds must be at least base_backoff_seconds")
        if self.circuit_rejection_threshold < 1:
            raise ValueError("circuit_rejection_threshold must be positive")


@dataclass
class RequestAccounting:
    maximum_requests: int
    attempts: int = 0
    successes: int = 0
    failed_attempts: int = 0
    retries: int = 0
    rate_limits: int = 0
    provider_rejections: int = 0
    circuit_trips: int = 0
    backoff_seconds: float = 0.0

    @property
    def remaining(self) -> int:
        return max(0, self.maximum_requests - self.attempts)

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


class ProviderCircuitBreaker:
    """Small in-memory breaker for one bounded, single-source backfill run."""

    def __init__(self, source: str, threshold: int) -> None:
        self.source = source
        self.threshold = threshold
        self.consecutive_rejections = 0
        self.is_open = False

    def before_request(self) -> None:
        if self.is_open:
            raise CircuitOpenError(self.source, self.threshold)

    def record_success(self) -> None:
        self.consecutive_rejections = 0

    def record_failure(self, *, rejection: bool) -> bool:
        if not rejection:
            self.consecutive_rejections = 0
            return False
        self.consecutive_rejections += 1
        if self.consecutive_rejections >= self.threshold:
            self.is_open = True
        return self.is_open


class ConservativeRequestExecutor:
    """Bounded retry, pacing, accounting, and rejection circuit breaker.

    Every physical attempt consumes budget, including retries. Authentication,
    schema, identity, and other non-retryable failures are returned immediately.
    The executor never retries more than three times and never logs request URLs.
    """

    def __init__(
        self,
        source: str,
        *,
        maximum_requests: int,
        policy: ConservativeRequestPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if maximum_requests < 0:
            raise ValueError("maximum_requests must be non-negative")
        self.source = source
        self.policy = policy or ConservativeRequestPolicy()
        self.accounting = RequestAccounting(maximum_requests=maximum_requests)
        self.breaker = ProviderCircuitBreaker(source, self.policy.circuit_rejection_threshold)
        self._sleep = sleep
        self._clock = clock
        self._last_attempt_at: float | None = None

    async def execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        last_error: BaseException | None = None
        for attempt_index in range(self.policy.max_attempts):
            self.breaker.before_request()
            self._reserve_attempt()
            await self._pace()
            self._last_attempt_at = self._clock()
            try:
                value = await operation()
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                last_error = exc
                self.accounting.failed_attempts += 1
                retryable, rejection, retry_after, status_code = _failure_policy(exc)
                if status_code == 429:
                    self.accounting.rate_limits += 1
                if rejection:
                    self.accounting.provider_rejections += 1
                if self.breaker.record_failure(rejection=rejection):
                    self.accounting.circuit_trips += 1
                    raise CircuitOpenError(self.source, self.breaker.threshold) from exc
                if not retryable or attempt_index + 1 >= self.policy.max_attempts:
                    raise _source_error(self.source, exc, retryable=retryable) from exc
                self.accounting.retries += 1
                delay = retry_after
                if delay is None:
                    delay = min(
                        self.policy.maximum_backoff_seconds,
                        self.policy.base_backoff_seconds * (2**attempt_index),
                    )
                delay = max(0.0, min(delay, self.policy.maximum_backoff_seconds))
                self.accounting.backoff_seconds += delay
                if delay:
                    await self._sleep(delay)
                continue
            self.accounting.successes += 1
            self.breaker.record_success()
            return value
        assert last_error is not None
        raise _source_error(self.source, last_error, retryable=False)

    def _reserve_attempt(self) -> None:
        if self.accounting.attempts >= self.accounting.maximum_requests:
            raise RequestCapExceeded(self.source, self.accounting.maximum_requests)
        self.accounting.attempts += 1

    async def _pace(self) -> None:
        if self._last_attempt_at is None or self.policy.minimum_interval_seconds == 0:
            return
        elapsed = self._clock() - self._last_attempt_at
        remaining = self.policy.minimum_interval_seconds - elapsed
        if remaining > 0:
            await self._sleep(remaining)


def _failure_policy(
    exc: BaseException,
) -> tuple[bool, bool, float | None, int | None]:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        retryable = status in {408, 425, 429} or 500 <= status <= 599
        rejection = status in {403, 429}
        return retryable, rejection, _retry_after_seconds(exc.response), status
    if isinstance(exc, httpx.TransportError):
        return True, False, None, None
    if isinstance(exc, SourceError):
        return exc.retryable, False, None, None
    return False, False, None, None


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


def _source_error(source: str, exc: BaseException, *, retryable: bool) -> SourceError:
    if isinstance(exc, SourceError):
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        return SourceError(source, f"{source} request returned HTTP {exc.response.status_code}", retryable=retryable)
    return SourceError(source, f"{source} request failed: {type(exc).__name__}", retryable=retryable)
