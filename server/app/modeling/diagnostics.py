"""Train-reference out-of-distribution diagnostics without outcome labels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class OODReport:
    observations: int
    mean_robust_z: float
    max_robust_z: float
    outside_training_range_fraction: float
    missingness_shift: float
    flagged: bool
    reasons: tuple[str, ...]


class OODDetector:
    def __init__(
        self,
        *,
        robust_z_threshold: float = 6.0,
        outside_range_threshold: float = 0.20,
        missingness_shift_threshold: float = 0.20,
    ) -> None:
        self.robust_z_threshold = robust_z_threshold
        self.outside_range_threshold = outside_range_threshold
        self.missingness_shift_threshold = missingness_shift_threshold
        self._names: tuple[str, ...] | None = None
        self._median: np.ndarray | None = None
        self._scale: np.ndarray | None = None
        self._minimum: np.ndarray | None = None
        self._maximum: np.ndarray | None = None
        self._missing_rate: np.ndarray | None = None

    def fit(self, matrix: np.ndarray, feature_names: Sequence[str]) -> OODDetector:
        if self._names is not None:
            raise RuntimeError("OODDetector instances cannot be refitted")
        values, names = _matrix_and_names(matrix, feature_names)
        self._names = names
        self._median = np.median(values, axis=0)
        quartile_25, quartile_75 = np.quantile(values, [0.25, 0.75], axis=0)
        robust_scale = (quartile_75 - quartile_25) / 1.349
        standard = values.std(axis=0)
        self._scale = np.where(robust_scale > 1e-9, robust_scale, np.where(standard > 1e-9, standard, 1.0))
        self._minimum = values.min(axis=0)
        self._maximum = values.max(axis=0)
        missing_indices = [index for index, name in enumerate(names) if name.endswith("__missing")]
        self._missing_rate = values[:, missing_indices].mean(axis=0) if missing_indices else np.asarray([], dtype=float)
        return self

    def diagnose(self, matrix: np.ndarray, feature_names: Sequence[str]) -> OODReport:
        if any(value is None for value in (self._names, self._median, self._scale, self._minimum, self._maximum, self._missing_rate)):
            raise RuntimeError("OODDetector is not fitted")
        values, names = _matrix_and_names(matrix, feature_names)
        if names != self._names:
            raise ValueError("OOD feature order does not match the training reference")
        assert self._median is not None and self._scale is not None
        assert self._minimum is not None and self._maximum is not None and self._missing_rate is not None
        raw_indices = [index for index, name in enumerate(names) if not name.endswith("__missing")]
        robust_z = np.abs((values[:, raw_indices] - self._median[raw_indices]) / self._scale[raw_indices])
        outside = (values[:, raw_indices] < self._minimum[raw_indices]) | (values[:, raw_indices] > self._maximum[raw_indices])
        missing_indices = [index for index, name in enumerate(names) if name.endswith("__missing")]
        missing_shift = (
            float(np.max(np.abs(values[:, missing_indices].mean(axis=0) - self._missing_rate)))
            if missing_indices else 0.0
        )
        max_z = float(np.max(robust_z))
        outside_fraction = float(np.mean(outside))
        reasons: list[str] = []
        if max_z > self.robust_z_threshold:
            reasons.append("robust_z")
        if outside_fraction > self.outside_range_threshold:
            reasons.append("outside_training_range")
        if missing_shift > self.missingness_shift_threshold:
            reasons.append("missingness_shift")
        return OODReport(
            observations=len(values),
            mean_robust_z=float(np.mean(robust_z)),
            max_robust_z=max_z,
            outside_training_range_fraction=outside_fraction,
            missingness_shift=missing_shift,
            flagged=bool(reasons),
            reasons=tuple(reasons),
        )


def _matrix_and_names(matrix: np.ndarray, feature_names: Sequence[str]) -> tuple[np.ndarray, tuple[str, ...]]:
    values = np.asarray(matrix, dtype=float)
    names = tuple(feature_names)
    if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] != len(names):
        raise ValueError("Feature matrix and names must align")
    if not np.isfinite(values).all() or len(set(names)) != len(names):
        raise ValueError("OOD features must be finite and uniquely named")
    return values, names
