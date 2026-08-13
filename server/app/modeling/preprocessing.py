"""Fold-local numeric preprocessing with an auditable fit boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class PreprocessingAudit:
    feature_names: tuple[str, ...]
    fit_row_ids: tuple[str, ...]
    fit_row_count: int


class FoldPreprocessor:
    """Standardize continuous columns while leaving missing flags as 0/1.

    One instance can be fitted exactly once.  Model factories must create a new
    instance per temporal fold, preventing validation or holdout values from
    influencing centering and scale.
    """

    def __init__(self) -> None:
        self._feature_names: tuple[str, ...] | None = None
        self._means: np.ndarray | None = None
        self._scales: np.ndarray | None = None
        self._fit_row_ids: tuple[str, ...] = ()

    @property
    def audit(self) -> PreprocessingAudit:
        if self._feature_names is None:
            raise RuntimeError("Preprocessor is not fitted")
        return PreprocessingAudit(
            feature_names=self._feature_names,
            fit_row_ids=self._fit_row_ids,
            fit_row_count=len(self._fit_row_ids) if self._fit_row_ids else int(self._fit_count),
        )

    def fit(
        self,
        matrix: np.ndarray,
        feature_names: Sequence[str],
        *,
        row_ids: Sequence[str] = (),
    ) -> FoldPreprocessor:
        if self._feature_names is not None:
            raise RuntimeError("FoldPreprocessor instances cannot be refitted")
        values = _matrix(matrix)
        names = tuple(feature_names)
        if values.shape[1] != len(names) or len(set(names)) != len(names):
            raise ValueError("Feature names must uniquely match matrix columns")
        if row_ids and len(row_ids) != values.shape[0]:
            raise ValueError("row_ids must match matrix rows")
        means = values.mean(axis=0)
        scales = values.std(axis=0)
        for index, name in enumerate(names):
            if name.endswith("__missing"):
                means[index] = 0.0
                scales[index] = 1.0
            elif scales[index] < 1e-12:
                scales[index] = 1.0
        self._feature_names = names
        self._means = means
        self._scales = scales
        self._fit_row_ids = tuple(str(item) for item in row_ids)
        self._fit_count = values.shape[0]
        return self

    def transform(self, matrix: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
        if self._feature_names is None or self._means is None or self._scales is None:
            raise RuntimeError("Preprocessor is not fitted")
        values = _matrix(matrix)
        if tuple(feature_names) != self._feature_names:
            raise ValueError("Inference feature order does not match fitted schema")
        if values.shape[1] != len(self._feature_names):
            raise ValueError("Inference feature count does not match fitted schema")
        return (values - self._means) / self._scales

    def fit_transform(
        self,
        matrix: np.ndarray,
        feature_names: Sequence[str],
        *,
        row_ids: Sequence[str] = (),
    ) -> np.ndarray:
        self.fit(matrix, feature_names, row_ids=row_ids)
        return self.transform(matrix, feature_names)


def _matrix(matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] == 0:
        raise ValueError("A non-empty two-dimensional matrix is required")
    if not np.isfinite(values).all():
        raise ValueError("Preprocessed feature matrices must be finite")
    return values
