"""Pure normalization and coverage-aware Statcast aggregation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable


@dataclass(frozen=True)
class PlayerStatcastObservation:
    player_id: str
    attempts: float
    xwoba: float | None = None
    barrel_rate: float | None = None
    hard_hit_rate: float | None = None
    average_exit_velocity: float | None = None


@dataclass(frozen=True)
class WeightedMetric:
    value: float | None
    observed_attempts: float
    denominator_attempts: float
    coverage: float


@dataclass(frozen=True)
class StatcastAggregate:
    xwoba: WeightedMetric
    barrel_rate: WeightedMetric
    hard_hit_rate: WeightedMetric
    average_exit_velocity: WeightedMetric
    player_count: int
    expected_attempts: float
    method: str = "attempt_weighted_statcast_v1"


def normalize_percentage(value: float | int | str | None) -> float | None:
    """Normalize either fraction-form or percentage-form input to ``[0, 1]``."""

    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Percentage value must be numeric") from exc
    if not isfinite(numeric) or numeric < 0:
        raise ValueError("Percentage value must be finite and non-negative")
    if numeric <= 1:
        return numeric
    if numeric <= 100:
        return numeric / 100.0
    raise ValueError("Percentage value cannot exceed 100")


def normalize_statcast_observation(observation: PlayerStatcastObservation) -> PlayerStatcastObservation:
    """Return an observation with consistent rate units and validated measures."""

    if not observation.player_id:
        raise ValueError("player_id is required")
    if not isfinite(observation.attempts) or observation.attempts < 0:
        raise ValueError("attempts must be finite and non-negative")
    xwoba = _bounded_optional("xwoba", observation.xwoba, 0.0, 1.0)
    exit_velocity = _bounded_optional(
        "average_exit_velocity",
        observation.average_exit_velocity,
        0.0,
        130.0,
    )
    return PlayerStatcastObservation(
        player_id=observation.player_id,
        attempts=observation.attempts,
        xwoba=xwoba,
        barrel_rate=normalize_percentage(observation.barrel_rate),
        hard_hit_rate=normalize_percentage(observation.hard_hit_rate),
        average_exit_velocity=exit_velocity,
    )


def aggregate_statcast(
    observations: Iterable[PlayerStatcastObservation],
    *,
    expected_attempts: float | None = None,
) -> StatcastAggregate:
    """Aggregate each metric independently so missing fields remain visible."""

    rows = tuple(normalize_statcast_observation(item) for item in observations)
    observed_total = sum(item.attempts for item in rows)
    if expected_attempts is None:
        denominator = observed_total
    else:
        if not isfinite(expected_attempts) or expected_attempts < 0:
            raise ValueError("expected_attempts must be finite and non-negative")
        if expected_attempts < observed_total:
            raise ValueError("expected_attempts cannot be smaller than observed attempts")
        denominator = expected_attempts
    return StatcastAggregate(
        xwoba=_weighted(rows, "xwoba", denominator),
        barrel_rate=_weighted(rows, "barrel_rate", denominator),
        hard_hit_rate=_weighted(rows, "hard_hit_rate", denominator),
        average_exit_velocity=_weighted(rows, "average_exit_velocity", denominator),
        player_count=len(rows),
        expected_attempts=denominator,
    )


def _weighted(
    rows: tuple[PlayerStatcastObservation, ...],
    name: str,
    denominator: float,
) -> WeightedMetric:
    available = [(float(getattr(row, name)), row.attempts) for row in rows if getattr(row, name) is not None]
    observed = sum(attempts for _, attempts in available)
    value = sum(metric * attempts for metric, attempts in available) / observed if observed else None
    return WeightedMetric(
        value=value,
        observed_attempts=observed,
        denominator_attempts=denominator,
        coverage=observed / denominator if denominator else 0.0,
    )


def _bounded_optional(name: str, value: float | None, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    if not isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be finite and within [{minimum}, {maximum}]")
    return value
