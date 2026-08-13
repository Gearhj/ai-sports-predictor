from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import log
from statistics import fmean
from typing import Iterable, Sequence


def brier_score(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    _validate(probabilities, outcomes)
    return fmean((probability - outcome) ** 2 for probability, outcome in zip(probabilities, outcomes))


def log_loss(probabilities: Sequence[float], outcomes: Sequence[int], epsilon: float = 1e-15) -> float:
    _validate(probabilities, outcomes)
    return -fmean(
        outcome * log(min(max(probability, epsilon), 1 - epsilon))
        + (1 - outcome) * log(min(max(1 - probability, epsilon), 1 - epsilon))
        for probability, outcome in zip(probabilities, outcomes)
    )


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_probability: float | None
    observed_rate: float | None


def calibration_bins(
    probabilities: Sequence[float], outcomes: Sequence[int], bin_count: int = 10
) -> list[CalibrationBin]:
    _validate(probabilities, outcomes)
    if bin_count < 2:
        raise ValueError("At least two calibration bins are required")
    result: list[CalibrationBin] = []
    for index in range(bin_count):
        lower, upper = index / bin_count, (index + 1) / bin_count
        in_bin = (
            (lambda probability: lower <= probability <= upper)
            if index == bin_count - 1
            else (lambda probability: lower <= probability < upper)
        )
        members = [
            (probability, outcome)
            for probability, outcome in zip(probabilities, outcomes)
            if in_bin(probability)
        ]
        result.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                count=len(members),
                mean_probability=fmean(value[0] for value in members) if members else None,
                observed_rate=fmean(value[1] for value in members) if members else None,
            )
        )
    return result


@dataclass(frozen=True)
class ChronologicalRecord:
    event_at: datetime
    values: dict


def chronological_split(
    records: Iterable[ChronologicalRecord], train_fraction: float = 0.70, validation_fraction: float = 0.15
) -> tuple[list[ChronologicalRecord], list[ChronologicalRecord], list[ChronologicalRecord]]:
    if not 0 < train_fraction < 1 or not 0 <= validation_fraction < 1:
        raise ValueError("Invalid chronological split fractions")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("A non-empty test fraction is required")
    ordered = sorted(records, key=lambda record: record.event_at)
    train_end = int(len(ordered) * train_fraction)
    validation_end = train_end + int(len(ordered) * validation_fraction)
    return ordered[:train_end], ordered[train_end:validation_end], ordered[validation_end:]


def mean_absolute_error(actual: Sequence[float], predicted: Sequence[float]) -> float:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("Actual and predicted values must have equal non-zero lengths")
    return fmean(abs(left - right) for left, right in zip(actual, predicted))


def _validate(probabilities: Sequence[float], outcomes: Sequence[int]) -> None:
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("Probabilities and outcomes must have equal non-zero lengths")
    if any(not 0 <= probability <= 1 for probability in probabilities):
        raise ValueError("Probabilities must be between 0 and 1")
    if any(outcome not in (0, 1) for outcome in outcomes):
        raise ValueError("Outcomes must be binary")
