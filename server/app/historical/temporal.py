"""Deterministic historical cutoffs and strict point-in-time windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import fmean
from typing import Generic, Iterable, TypeVar

from app.historical.policy import EvidenceClassification


T = TypeVar("T")
DEFAULT_CUTOFF_OFFSET = timedelta(hours=6)
CUTOFF_POLICY_VERSION = "mlb_t_minus_6h_v1"


@dataclass(frozen=True)
class HistoricalEvent(Generic[T]):
    event_id: str
    game_id: str | None
    event_at: datetime
    eligible_at: datetime
    value: T
    classification: EvidenceClassification = (
        EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME
    )


def deterministic_cutoff(
    scheduled_first_pitch: datetime,
    *,
    offset: timedelta = DEFAULT_CUTOFF_OFFSET,
) -> datetime:
    """Return the version-one historical boundary: first pitch minus six hours."""

    first_pitch = as_utc(scheduled_first_pitch)
    if offset <= timedelta(0):
        raise ValueError("Historical cutoff offset must be positive")
    return first_pitch - offset


def strictly_eligible_events(
    events: Iterable[HistoricalEvent[T]],
    *,
    cutoff: datetime,
    current_game_id: str,
    window: timedelta | None = None,
    allowed_classifications: tuple[EvidenceClassification, ...] = (
        EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME,
        EvidenceClassification.PARTIALLY_RECONSTRUCTABLE,
    ),
) -> tuple[HistoricalEvent[T], ...]:
    """Select events strictly before a cutoff and never from the target game.

    Event occurrence is strict (``event_at < cutoff``); conservative knowledge
    eligibility is inclusive (``eligible_at <= cutoff``).  A target-game row is
    excluded by identity even if its timestamps are malformed to look old.
    """

    boundary = as_utc(cutoff)
    if not current_game_id:
        raise ValueError("current_game_id is required")
    if window is not None and window <= timedelta(0):
        raise ValueError("Rolling window must be positive")
    allowed = set(allowed_classifications)
    if not allowed or EvidenceClassification.FORWARD_ONLY in allowed:
        raise ValueError("Rolling windows cannot admit forward-only evidence")

    rows = tuple(events)
    _validate_unique_event_ids(rows)
    lower = boundary - window if window is not None else None
    eligible = [
        item
        for item in rows
        if item.event_id
        and item.game_id != current_game_id
        and item.classification in allowed
        and as_utc(item.event_at) < boundary
        and as_utc(item.eligible_at) <= boundary
        and (lower is None or as_utc(item.event_at) >= lower)
    ]
    return tuple(
        sorted(
            eligible,
            key=lambda item: (
                as_utc(item.event_at),
                as_utc(item.eligible_at),
                item.event_id,
            ),
        )
    )


def strict_rolling_mean(
    events: Iterable[HistoricalEvent[float]],
    *,
    cutoff: datetime,
    current_game_id: str,
    window: timedelta,
) -> float | None:
    selected = strictly_eligible_events(
        events,
        cutoff=cutoff,
        current_game_id=current_game_id,
        window=window,
    )
    return fmean(float(item.value) for item in selected) if selected else None


def strict_rolling_sum(
    events: Iterable[HistoricalEvent[float]],
    *,
    cutoff: datetime,
    current_game_id: str,
    window: timedelta,
) -> float:
    return sum(
        float(item.value)
        for item in strictly_eligible_events(
            events,
            cutoff=cutoff,
            current_game_id=current_game_id,
            window=window,
        )
    )


def as_utc(value: datetime) -> datetime:
    """Normalize an aware timestamp to UTC and reject ambiguous naive input."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Historical timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _validate_unique_event_ids(events: tuple[HistoricalEvent[object], ...]) -> None:
    identifiers = [item.event_id for item in events]
    if any(not identifier for identifier in identifiers):
        raise ValueError("Historical event_id is required")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Historical event_id values must be unique")
