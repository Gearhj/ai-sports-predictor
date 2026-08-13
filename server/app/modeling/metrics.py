"""Deterministic probability, run, calibration, and paired-delta metrics."""

from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from typing import Callable, Hashable, Sequence

import numpy as np


@dataclass(frozen=True)
class ClassificationMetrics:
    observations: int
    brier_score: float
    log_loss: float
    probability_rmse: float
    expected_calibration_error: float
    calibration_intercept: float
    calibration_slope: float


@dataclass(frozen=True)
class PairedBootstrapDelta:
    estimate: float
    lower: float
    upper: float
    confidence_level: float
    resamples: int
    groups: int


def brier_score(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    probability, outcome = _probability_arrays(probabilities, outcomes)
    return float(np.mean((probability - outcome) ** 2))


def probability_log_loss(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    probability, outcome = _probability_arrays(probabilities, outcomes)
    clipped = np.clip(probability, 1e-15, 1 - 1e-15)
    return float(-np.mean(outcome * np.log(clipped) + (1 - outcome) * np.log(1 - clipped)))


def rmse(actual: Sequence[float], predicted: Sequence[float]) -> float:
    left = np.asarray(actual, dtype=float)
    right = np.asarray(predicted, dtype=float)
    if left.ndim != 1 or right.ndim != 1 or len(left) == 0 or len(left) != len(right):
        raise ValueError("Actual and predicted values must have equal non-zero length")
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError("RMSE inputs must be finite")
    return float(sqrt(np.mean((left - right) ** 2)))


def expected_calibration_error(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    bins: int = 10,
    strategy: str = "quantile",
) -> float:
    probability, outcome = _probability_arrays(probabilities, outcomes)
    if bins < 2:
        raise ValueError("At least two calibration bins are required")
    bin_count = min(bins, len(probability))
    if strategy == "quantile":
        groups = np.array_split(np.argsort(probability, kind="stable"), bin_count)
    elif strategy == "uniform":
        edges = np.linspace(0.0, 1.0, bin_count + 1)
        assignments = np.minimum(np.digitize(probability, edges[1:-1]), bin_count - 1)
        groups = [np.flatnonzero(assignments == index) for index in range(bin_count)]
    else:
        raise ValueError("strategy must be 'quantile' or 'uniform'")
    error = 0.0
    for members in groups:
        if len(members):
            error += len(members) / len(probability) * abs(
                float(np.mean(probability[members]) - np.mean(outcome[members]))
            )
    return float(error)


def calibration_intercept_slope(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    max_iterations: int = 100,
) -> tuple[float, float]:
    """Fit outcome ~ intercept + slope * logit(probability) by Newton steps."""

    probability, outcome = _probability_arrays(probabilities, outcomes)
    if len(np.unique(outcome)) < 2:
        raise ValueError("Calibration slope requires both outcomes")
    clipped = np.clip(probability, 1e-6, 1 - 1e-6)
    logit = np.log(clipped / (1 - clipped))
    design = np.column_stack([np.ones(len(logit)), logit])
    coefficients = np.asarray([log(float(outcome.mean()) / (1 - float(outcome.mean()))), 1.0])
    penalty = np.diag([0.0, 1e-8])
    for _ in range(max_iterations):
        linear = np.clip(design @ coefficients, -30, 30)
        fitted = 1.0 / (1.0 + np.exp(-linear))
        weights = np.maximum(fitted * (1 - fitted), 1e-8)
        information = design.T @ (weights[:, None] * design) + penalty
        gradient = design.T @ (outcome - fitted) - penalty @ coefficients
        try:
            step = np.linalg.solve(information, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(information) @ gradient
        coefficients += np.clip(step, -5.0, 5.0)
        if float(np.max(np.abs(step))) < 1e-9:
            break
    return float(coefficients[0]), float(coefficients[1])


def classification_metrics(
    probabilities: Sequence[float], outcomes: Sequence[int], *, bins: int = 10
) -> ClassificationMetrics:
    intercept, slope = calibration_intercept_slope(probabilities, outcomes)
    return ClassificationMetrics(
        observations=len(probabilities),
        brier_score=brier_score(probabilities, outcomes),
        log_loss=probability_log_loss(probabilities, outcomes),
        probability_rmse=rmse(outcomes, probabilities),
        expected_calibration_error=expected_calibration_error(probabilities, outcomes, bins=bins),
        calibration_intercept=intercept,
        calibration_slope=slope,
    )


def paired_block_bootstrap_delta(
    predictions_a: Sequence[float],
    predictions_b: Sequence[float],
    outcomes: Sequence[int],
    groups: Sequence[Hashable],
    *,
    metric: Callable[[Sequence[float], Sequence[int]], float] = probability_log_loss,
    resamples: int = 1_000,
    confidence_level: float = 0.95,
    random_seed: int = 20250601,
) -> PairedBootstrapDelta:
    first, outcome = _probability_arrays(predictions_a, outcomes)
    second, second_outcome = _probability_arrays(predictions_b, outcomes)
    if not np.array_equal(outcome, second_outcome) or len(groups) != len(outcome):
        raise ValueError("Paired predictions, outcomes, and groups must align")
    if resamples < 50 or not 0 < confidence_level < 1:
        raise ValueError("Bootstrap configuration is invalid")
    unique_groups = tuple(dict.fromkeys(groups))
    if len(unique_groups) < 2:
        raise ValueError("At least two independent groups are required")
    indices = {
        group: np.asarray([index for index, value in enumerate(groups) if value == group], dtype=int)
        for group in unique_groups
    }
    rng = np.random.default_rng(random_seed)
    deltas = np.empty(resamples, dtype=float)
    for sample_index in range(resamples):
        selected = rng.choice(len(unique_groups), size=len(unique_groups), replace=True)
        sample = np.concatenate([indices[unique_groups[index]] for index in selected])
        deltas[sample_index] = metric(first[sample].tolist(), outcome[sample].astype(int).tolist()) - metric(
            second[sample].tolist(), outcome[sample].astype(int).tolist()
        )
    alpha = (1 - confidence_level) / 2
    estimate = metric(first.tolist(), outcome.astype(int).tolist()) - metric(
        second.tolist(), outcome.astype(int).tolist()
    )
    return PairedBootstrapDelta(
        estimate=float(estimate),
        lower=float(np.quantile(deltas, alpha)),
        upper=float(np.quantile(deltas, 1 - alpha)),
        confidence_level=confidence_level,
        resamples=resamples,
        groups=len(unique_groups),
    )


def _probability_arrays(
    probabilities: Sequence[float], outcomes: Sequence[int]
) -> tuple[np.ndarray, np.ndarray]:
    probability = np.asarray(probabilities, dtype=float)
    outcome = np.asarray(outcomes, dtype=float)
    if probability.ndim != 1 or outcome.ndim != 1 or len(probability) == 0 or len(probability) != len(outcome):
        raise ValueError("Probabilities and outcomes must have equal non-zero length")
    if not np.isfinite(probability).all() or np.any((probability < 0) | (probability > 1)):
        raise ValueError("Probabilities must be finite and between zero and one")
    if not np.all(np.isin(outcome, [0, 1])):
        raise ValueError("Outcomes must be binary")
    return probability, outcome
