"""Availability-aware bullpen workload features without inferred injuries."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable


@dataclass(frozen=True)
class RelieverWorkload:
    player_id: str
    pitches_last_1d: float
    pitches_last_2d: float
    pitches_last_3d: float
    expected_usage_weight: float
    expected_availability: float | None


@dataclass(frozen=True)
class RelieverAvailability:
    player_id: str
    availability: float | None
    workload_penalty: float
    expected_usage_weight: float
    expected_availability: float | None
    method: str = "observed_pitch_workload_v1"


@dataclass(frozen=True)
class BullpenAvailability:
    value: float | None
    coverage: float
    known_role_weight: float
    total_role_weight: float
    relievers: tuple[RelieverAvailability, ...]
    missing_player_ids: tuple[str, ...]
    method: str = "role_weighted_reliever_availability_v1"


def estimate_reliever_availability(workload: RelieverWorkload) -> RelieverAvailability:
    """Return a bounded availability score monotonic in observed workload.

    The cumulative-window penalty is intentionally transparent.  Expected
    availability must come from explicit pregame evidence; ``None`` remains
    unknown instead of being treated as healthy or available.
    """

    _validate_workload(workload)
    penalty = min(
        1.0,
        0.55 * workload.pitches_last_1d / 35.0
        + 0.30 * workload.pitches_last_2d / 60.0
        + 0.15 * workload.pitches_last_3d / 90.0,
    )
    availability = (
        workload.expected_availability * (1.0 - penalty)
        if workload.expected_availability is not None
        else None
    )
    return RelieverAvailability(
        player_id=workload.player_id,
        availability=availability,
        workload_penalty=penalty,
        expected_usage_weight=workload.expected_usage_weight,
        expected_availability=workload.expected_availability,
    )


def aggregate_bullpen_availability(workloads: Iterable[RelieverWorkload]) -> BullpenAvailability:
    """Combine relievers by expected role share and expose missing coverage."""

    estimates = tuple(estimate_reliever_availability(item) for item in workloads)
    total_weight = sum(item.expected_usage_weight for item in estimates)
    known = tuple(item for item in estimates if item.availability is not None)
    known_weight = sum(item.expected_usage_weight for item in known)
    value = (
        sum(item.availability * item.expected_usage_weight for item in known) / known_weight
        if known_weight
        else None
    )
    return BullpenAvailability(
        value=value,
        coverage=known_weight / total_weight if total_weight else 0.0,
        known_role_weight=known_weight,
        total_role_weight=total_weight,
        relievers=estimates,
        missing_player_ids=tuple(item.player_id for item in estimates if item.availability is None),
    )


def _validate_workload(workload: RelieverWorkload) -> None:
    if not workload.player_id:
        raise ValueError("player_id is required")
    values = (
        workload.pitches_last_1d,
        workload.pitches_last_2d,
        workload.pitches_last_3d,
    )
    if any(not isfinite(value) or value < 0 for value in values):
        raise ValueError("Pitch workloads must be finite and non-negative")
    if not values[0] <= values[1] <= values[2]:
        raise ValueError("Cumulative 1d/2d/3d pitch workloads must be non-decreasing")
    if not isfinite(workload.expected_usage_weight) or workload.expected_usage_weight < 0:
        raise ValueError("expected_usage_weight must be finite and non-negative")
    if workload.expected_availability is not None and (
        not isfinite(workload.expected_availability)
        or not 0 <= workload.expected_availability <= 1
    ):
        raise ValueError("expected_availability must be within [0, 1] when supplied")
