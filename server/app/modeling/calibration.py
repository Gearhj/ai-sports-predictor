"""Validation-only probability calibration and deterministic selector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import Sequence

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from app.modeling.metrics import probability_log_loss


class IdentityCalibrator:
    method = "identity"

    def fit(self, probabilities: Sequence[float], outcomes: Sequence[int]) -> IdentityCalibrator:
        _calibration_arrays(probabilities, outcomes, require_two_classes=False)
        return self

    def transform(self, probabilities: Sequence[float]) -> np.ndarray:
        return _probabilities(probabilities)


class PlattCalibrator:
    method = "platt"

    def __init__(self) -> None:
        self.estimator = LogisticRegression(C=1_000.0, solver="lbfgs", max_iter=1_000)
        self._fitted = False

    def fit(self, probabilities: Sequence[float], outcomes: Sequence[int]) -> PlattCalibrator:
        probability, outcome = _calibration_arrays(probabilities, outcomes)
        self.estimator.fit(_logits(probability).reshape(-1, 1), outcome)
        self._fitted = True
        return self

    def transform(self, probabilities: Sequence[float]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Calibrator is not fitted")
        probability = _probabilities(probabilities)
        return self.estimator.predict_proba(_logits(probability).reshape(-1, 1))[:, 1]


class IsotonicCalibrator:
    method = "isotonic"

    def __init__(self) -> None:
        self.estimator = IsotonicRegression(y_min=1e-6, y_max=1 - 1e-6, out_of_bounds="clip")
        self._fitted = False

    def fit(self, probabilities: Sequence[float], outcomes: Sequence[int]) -> IsotonicCalibrator:
        probability, outcome = _calibration_arrays(probabilities, outcomes)
        self.estimator.fit(probability, outcome)
        self._fitted = True
        return self

    def transform(self, probabilities: Sequence[float]) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("Calibrator is not fitted")
        return np.asarray(self.estimator.predict(_probabilities(probabilities)), dtype=float)


@dataclass(frozen=True)
class CalibrationSelection:
    method: str
    calibrator: object
    selection_scores: dict[str, float]
    calibration_fit_count: int
    selection_count: int
    total_validation_count: int
    selection_started_at: datetime


def select_probability_calibrator(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    event_at: Sequence[datetime],
    *,
    selection_fraction: float = 0.30,
    min_isotonic_samples: int = 200,
    allowed_methods: Sequence[str] = ("identity", "platt", "isotonic"),
) -> CalibrationSelection:
    """Select within temporal OOF validation data, then refit on all validation.

    Locked final-holdout data is intentionally absent from this API. Candidate
    calibrators fit on earlier validation dates and are scored on later
    validation dates. The selected method is finally refit on the complete OOF
    validation series before deployment to a still-locked holdout.
    """

    probability, outcome = _calibration_arrays(probabilities, outcomes)
    if len(event_at) != len(probability) or not 0 < selection_fraction < 1:
        raise ValueError("Calibration timestamps and selection fraction are invalid")
    if any(value.tzinfo is None or value.utcoffset() is None for value in event_at):
        raise ValueError("Calibration timestamps must be timezone-aware")
    methods = tuple(dict.fromkeys(allowed_methods))
    if not methods or any(method not in {"identity", "platt", "isotonic"} for method in methods):
        raise ValueError("Unsupported calibration method")
    order = np.asarray(sorted(range(len(event_at)), key=lambda index: (event_at[index], index)), dtype=int)
    ordered_dates = [event_at[index].date() for index in order]
    unique_dates = tuple(dict.fromkeys(ordered_dates))
    if len(unique_dates) < 3:
        raise ValueError("Calibration selection requires at least three date groups")
    selection_date_count = max(1, ceil(len(unique_dates) * selection_fraction))
    selection_start_date = unique_dates[-selection_date_count]
    fit_indices = np.asarray([index for index in order if event_at[index].date() < selection_start_date], dtype=int)
    selection_indices = np.asarray([index for index in order if event_at[index].date() >= selection_start_date], dtype=int)
    if len(fit_indices) < 4 or not len(selection_indices) or len(np.unique(outcome[fit_indices])) < 2:
        raise ValueError("Earlier validation data cannot fit a probability calibrator")

    scores: dict[str, float] = {}
    for method in methods:
        if method == "isotonic" and len(fit_indices) < min_isotonic_samples:
            continue
        candidate = _calibrator(method)
        candidate.fit(probability[fit_indices], outcome[fit_indices])
        selected_probability = candidate.transform(probability[selection_indices])
        scores[method] = probability_log_loss(selected_probability, outcome[selection_indices].astype(int))
    if not scores:
        raise ValueError("No calibration candidate was eligible")
    # Stable method order resolves exact ties in favor of the lower-variance
    # identity/Platt candidates rather than a flexible isotonic mapping.
    preference = {"identity": 0, "platt": 1, "isotonic": 2}
    selected_method = min(scores, key=lambda name: (scores[name], preference[name]))
    selected = _calibrator(selected_method)
    selected.fit(probability, outcome)
    selection_start = min(event_at[index] for index in selection_indices)
    return CalibrationSelection(
        method=selected_method,
        calibrator=selected,
        selection_scores=scores,
        calibration_fit_count=len(fit_indices),
        selection_count=len(selection_indices),
        total_validation_count=len(probability),
        selection_started_at=selection_start,
    )


def _calibrator(method: str) -> IdentityCalibrator | PlattCalibrator | IsotonicCalibrator:
    if method == "identity":
        return IdentityCalibrator()
    if method == "platt":
        return PlattCalibrator()
    if method == "isotonic":
        return IsotonicCalibrator()
    raise ValueError(f"Unknown calibration method: {method}")


def _calibration_arrays(
    probabilities: Sequence[float], outcomes: Sequence[int], *, require_two_classes: bool = True
) -> tuple[np.ndarray, np.ndarray]:
    probability = _probabilities(probabilities)
    outcome = np.asarray(outcomes, dtype=int)
    if outcome.shape != probability.shape or not np.all(np.isin(outcome, [0, 1])):
        raise ValueError("Calibration outcomes must be aligned and binary")
    if require_two_classes and len(np.unique(outcome)) < 2:
        raise ValueError("Calibration requires both outcomes")
    return probability, outcome


def _probabilities(probabilities: Sequence[float]) -> np.ndarray:
    values = np.asarray(probabilities, dtype=float)
    if values.ndim != 1 or len(values) == 0 or not np.isfinite(values).all() or np.any((values < 0) | (values > 1)):
        raise ValueError("Probabilities must be a finite non-empty vector in [0, 1]")
    return np.clip(values, 1e-6, 1 - 1e-6)


def _logits(probabilities: np.ndarray) -> np.ndarray:
    return np.log(probabilities / (1 - probabilities))
