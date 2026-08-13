"""Nonnegative normalized stacking fitted only from temporal OOF predictions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class OOFPredictionRow:
    game_id: str
    event_at: datetime
    trained_through: datetime
    outcome: int
    predictions: Mapping[str, float]

    def __post_init__(self) -> None:
        if self.trained_through >= self.event_at:
            raise ValueError("OOF predictions must come from a strictly earlier training window")
        if self.outcome not in (0, 1) or not self.predictions:
            raise ValueError("OOF outcome and base predictions are required")
        if any(not 0 <= float(value) <= 1 for value in self.predictions.values()):
            raise ValueError("Base probabilities must be in [0, 1]")


@dataclass(frozen=True)
class StackingFit:
    model_names: tuple[str, ...]
    weights: tuple[float, ...]
    observations: int
    objective: float

    @property
    def weight_map(self) -> dict[str, float]:
        return dict(zip(self.model_names, self.weights))


class NonnegativeStacker:
    def __init__(self, *, l2_toward_uniform: float = 0.002) -> None:
        if l2_toward_uniform < 0:
            raise ValueError("Stack regularization cannot be negative")
        self.l2_toward_uniform = l2_toward_uniform
        self.fit_: StackingFit | None = None

    def fit(self, rows: Sequence[OOFPredictionRow]) -> NonnegativeStacker:
        if len(rows) < 4:
            raise ValueError("At least four OOF predictions are required")
        game_ids = [row.game_id for row in rows]
        if len(set(game_ids)) != len(game_ids):
            raise ValueError("OOF games must be unique")
        names = tuple(sorted(rows[0].predictions))
        if any(tuple(sorted(row.predictions)) != names for row in rows):
            raise ValueError("Every OOF row must contain the same base models")
        matrix = np.asarray([[row.predictions[name] for name in names] for row in rows], dtype=float)
        outcomes = np.asarray([row.outcome for row in rows], dtype=float)
        if len(np.unique(outcomes)) < 2:
            raise ValueError("Stacking requires both outcomes")
        initial = np.full(len(names), 1.0 / len(names))

        def objective(weights: np.ndarray) -> float:
            probability = np.clip(matrix @ weights, 1e-8, 1 - 1e-8)
            loss = -np.mean(outcomes * np.log(probability) + (1 - outcomes) * np.log(1 - probability))
            regularization = self.l2_toward_uniform * float(np.sum((weights - initial) ** 2))
            return float(loss + regularization)

        weights = initial.copy()
        current = objective(weights)
        for _ in range(5_000):
            probability = np.clip(matrix @ weights, 1e-8, 1 - 1e-8)
            gradient = matrix.T @ ((probability - outcomes) / (probability * (1 - probability))) / len(rows)
            gradient += 2 * self.l2_toward_uniform * (weights - initial)
            step = 1.0
            candidate = weights
            candidate_objective = current
            while step >= 1e-10:
                proposed = _project_simplex(weights - step * gradient)
                proposed_objective = objective(proposed)
                if proposed_objective <= current:
                    candidate = proposed
                    candidate_objective = proposed_objective
                    break
                step *= 0.5
            if float(np.max(np.abs(candidate - weights))) < 1e-10:
                weights = candidate
                current = candidate_objective
                break
            weights = candidate
            current = candidate_objective
        self.fit_ = StackingFit(names, tuple(float(value) for value in weights), len(rows), objective(weights))
        return self

    def predict(self, predictions: Mapping[str, Sequence[float]]) -> np.ndarray:
        if self.fit_ is None:
            raise RuntimeError("Stacker is not fitted")
        if set(predictions) != set(self.fit_.model_names):
            raise ValueError("Stack inference models do not match fitted base models")
        lengths = {len(predictions[name]) for name in self.fit_.model_names}
        if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
            raise ValueError("Base prediction arrays must share non-zero length")
        matrix = np.column_stack([predictions[name] for name in self.fit_.model_names]).astype(float)
        if not np.isfinite(matrix).all() or np.any((matrix < 0) | (matrix > 1)):
            raise ValueError("Base predictions must be finite probabilities")
        return np.clip(matrix @ np.asarray(self.fit_.weights), 1e-6, 1 - 1e-6)


def _project_simplex(values: np.ndarray) -> np.ndarray:
    """Euclidean projection onto nonnegative weights that sum to one."""

    ordered = np.sort(values)[::-1]
    cumulative = np.cumsum(ordered)
    eligible = np.flatnonzero(ordered - (cumulative - 1) / (np.arange(len(values)) + 1) > 0)
    if not len(eligible):
        return np.full(len(values), 1.0 / len(values))
    last = int(eligible[-1])
    threshold = (cumulative[last] - 1) / (last + 1)
    projected = np.maximum(values - threshold, 0.0)
    return projected / projected.sum()
