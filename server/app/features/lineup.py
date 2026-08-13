"""Deterministic, provenance-friendly MLB lineup aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Iterable, Literal


# Opportunity multipliers approximate the declining plate-appearance share by
# batting order.  They affect aggregation weight, never a batter's talent value.
BATTING_ORDER_WEIGHTS: dict[int, float] = {
    1: 1.08,
    2: 1.06,
    3: 1.04,
    4: 1.02,
    5: 1.00,
    6: 0.98,
    7: 0.96,
    8: 0.94,
    9: 0.92,
}


@dataclass(frozen=True)
class LineupEntry:
    player_id: str
    batting_order: int
    talent_value: float | None
    projected_plate_appearances: float
    participation_probability: float = 1.0
    replacement_value: float | None = None
    role: Literal["starter", "replacement", "unknown"] = "starter"


@dataclass(frozen=True)
class LineupSnapshot:
    entries: tuple[LineupEntry, ...]
    observed_at: datetime
    available_as_of: datetime
    source: str
    confirmed: bool


@dataclass(frozen=True)
class LineupAggregate:
    value: float | None
    known_weight: float
    potential_weight: float
    weighted_coverage: float
    slot_coverage: float
    known_slots: int
    missing_slots: tuple[int, ...]
    uncertain_slots: tuple[int, ...]
    replacement_slots: tuple[int, ...]
    confirmed: bool
    source: str | None
    method: str = "pa_and_batting_order_weighted_v1"


def select_lineup_snapshot(
    snapshots: Iterable[LineupSnapshot],
    *,
    prediction_time: datetime,
) -> LineupSnapshot | None:
    """Select eligible lineup evidence, preferring confirmed over projected.

    A confirmed snapshot supersedes every eligible projected snapshot.  Within
    the same confirmation state, the latest availability time wins.
    """

    cutoff = _aware(prediction_time)
    eligible = [
        snapshot
        for snapshot in snapshots
        if _aware(snapshot.observed_at) <= cutoff and _aware(snapshot.available_as_of) <= cutoff
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda item: (
            int(item.confirmed),
            _aware(item.available_as_of),
            _aware(item.observed_at),
            item.source,
        ),
    )


def aggregate_lineup(
    entries: Iterable[LineupEntry],
    *,
    expected_slots: int = 9,
    confirmed: bool = False,
    source: str | None = None,
) -> LineupAggregate:
    """Aggregate only explicitly supplied batter and replacement values.

    Participation uncertainty reduces evidence coverage.  The complement is
    included only when an explicit replacement value is supplied; no neutral or
    league-average batter is fabricated for a missing player or slot.
    """

    if expected_slots <= 0 or expected_slots > len(BATTING_ORDER_WEIGHTS):
        raise ValueError("expected_slots must be between 1 and 9")
    rows = tuple(entries)
    orders = [row.batting_order for row in rows]
    if len(orders) != len(set(orders)):
        raise ValueError("A lineup cannot contain duplicate batting-order slots")

    contribution = 0.0
    known_weight = 0.0
    potential_weight = 0.0
    known_slots: set[int] = set()
    uncertain_slots: set[int] = set()
    replacement_slots: set[int] = set()

    for row in rows:
        _validate_entry(row, expected_slots)
        opportunity = row.projected_plate_appearances * BATTING_ORDER_WEIGHTS[row.batting_order]
        potential_weight += opportunity
        probability = row.participation_probability
        slot_has_evidence = False

        if row.talent_value is not None and probability > 0:
            weight = opportunity * probability
            contribution += row.talent_value * weight
            known_weight += weight
            slot_has_evidence = True
        if row.replacement_value is not None and probability < 1:
            replacement_weight = opportunity * (1 - probability)
            contribution += row.replacement_value * replacement_weight
            known_weight += replacement_weight
            replacement_slots.add(row.batting_order)
            slot_has_evidence = True
        if row.role == "replacement" and row.talent_value is not None:
            replacement_slots.add(row.batting_order)
        if probability < 1:
            uncertain_slots.add(row.batting_order)
        if slot_has_evidence:
            known_slots.add(row.batting_order)

    expected = set(range(1, expected_slots + 1))
    missing = expected - known_slots
    value = contribution / known_weight if known_weight else None
    weighted_coverage = known_weight / potential_weight if potential_weight else 0.0
    return LineupAggregate(
        value=value,
        known_weight=known_weight,
        potential_weight=potential_weight,
        weighted_coverage=min(max(weighted_coverage, 0.0), 1.0),
        slot_coverage=len(known_slots & expected) / expected_slots,
        known_slots=len(known_slots & expected),
        missing_slots=tuple(sorted(missing)),
        uncertain_slots=tuple(sorted(uncertain_slots)),
        replacement_slots=tuple(sorted(replacement_slots)),
        confirmed=confirmed,
        source=source,
    )


def aggregate_lineup_snapshot(snapshot: LineupSnapshot, *, expected_slots: int = 9) -> LineupAggregate:
    return aggregate_lineup(
        snapshot.entries,
        expected_slots=expected_slots,
        confirmed=snapshot.confirmed,
        source=snapshot.source,
    )


def _validate_entry(row: LineupEntry, expected_slots: int) -> None:
    if not row.player_id:
        raise ValueError("player_id is required")
    if row.batting_order not in range(1, expected_slots + 1):
        raise ValueError(f"batting_order must be between 1 and {expected_slots}")
    if not isfinite(row.projected_plate_appearances) or row.projected_plate_appearances < 0:
        raise ValueError("projected_plate_appearances must be finite and non-negative")
    if not isfinite(row.participation_probability) or not 0 <= row.participation_probability <= 1:
        raise ValueError("participation_probability must be within [0, 1]")
    for name, value in (("talent_value", row.talent_value), ("replacement_value", row.replacement_value)):
        if value is not None and not isfinite(value):
            raise ValueError(f"{name} must be finite when supplied")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
