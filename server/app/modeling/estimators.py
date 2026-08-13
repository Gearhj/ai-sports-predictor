"""Restrained deterministic and trainable Gate 3 model families."""

from __future__ import annotations

from collections import defaultdict
from math import ceil, exp, log, sqrt
from typing import Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression, PoissonRegressor

from app.modeling.preprocessing import FoldPreprocessor


class HomePriorClassifier:
    def __init__(self, *, prior_probability: float = 0.54, prior_strength: float = 20.0) -> None:
        if not 0 < prior_probability < 1 or prior_strength < 0:
            raise ValueError("Home-prior parameters are invalid")
        self.prior_probability = prior_probability
        self.prior_strength = prior_strength
        self.probability_: float | None = None
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(self, matrix: np.ndarray, outcomes: Sequence[int], feature_names: Sequence[str], *, row_ids: Sequence[str] = ()) -> HomePriorClassifier:
        values, labels = _classification_data(matrix, outcomes, feature_names, require_two_classes=False)
        self.probability_ = float((labels.sum() + self.prior_strength * self.prior_probability) / (len(labels) + self.prior_strength))
        self.fit_row_ids = _row_ids(row_ids, len(values))
        return self

    def predict_proba(self, matrix: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
        if self.probability_ is None:
            raise RuntimeError("Classifier is not fitted")
        values = _inference_matrix(matrix, feature_names)
        return np.full(len(values), self.probability_, dtype=float)


class RatingClassifier:
    def __init__(self, *, home_advantage: float = 24.0, rating_scale: float = 400.0) -> None:
        self.home_advantage = home_advantage
        self.rating_scale = rating_scale
        self._indices: tuple[int, int] | None = None
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(self, matrix: np.ndarray, outcomes: Sequence[int], feature_names: Sequence[str], *, row_ids: Sequence[str] = ()) -> RatingClassifier:
        values, _ = _classification_data(matrix, outcomes, feature_names, require_two_classes=False)
        self._indices = (_index(feature_names, "home_rating"), _index(feature_names, "away_rating"))
        self.fit_row_ids = _row_ids(row_ids, len(values))
        return self

    def predict_proba(self, matrix: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
        if self._indices is None:
            raise RuntimeError("Classifier is not fitted")
        values = _inference_matrix(matrix, feature_names)
        home, away = self._indices
        exponent = -(values[:, home] + self.home_advantage - values[:, away]) / self.rating_scale
        return np.clip(1.0 / (1.0 + np.power(10.0, exponent)), 1e-6, 1 - 1e-6)


class RunStrengthClassifier:
    def __init__(self, *, run_scale: float = 1.55, home_run_multiplier: float = 1.025) -> None:
        self.run_scale = run_scale
        self.home_run_multiplier = home_run_multiplier
        self._indices: tuple[int, int, int, int] | None = None
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(self, matrix: np.ndarray, outcomes: Sequence[int], feature_names: Sequence[str], *, row_ids: Sequence[str] = ()) -> RunStrengthClassifier:
        values, _ = _classification_data(matrix, outcomes, feature_names, require_two_classes=False)
        self._indices = tuple(_index(feature_names, name) for name in (
            "home_runs_per_game", "away_runs_per_game",
            "home_runs_allowed_per_game", "away_runs_allowed_per_game",
        ))
        self.fit_row_ids = _row_ids(row_ids, len(values))
        return self

    def expected_runs(self, matrix: np.ndarray, feature_names: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
        if self._indices is None:
            raise RuntimeError("Classifier is not fitted")
        values = _inference_matrix(matrix, feature_names)
        home_rpg, away_rpg, home_allowed, away_allowed = self._indices
        home = np.clip((values[:, home_rpg] + values[:, away_allowed]) / 2 * self.home_run_multiplier, 1.0, 10.0)
        away = np.clip((values[:, away_rpg] + values[:, home_allowed]) / 2, 1.0, 10.0)
        return home, away

    def predict_proba(self, matrix: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
        home, away = self.expected_runs(matrix, feature_names)
        return np.clip(1.0 / (1.0 + np.exp(-(home - away) / self.run_scale)), 1e-6, 1 - 1e-6)


class RegularizedLogisticClassifier:
    def __init__(self, *, regularization_c: float = 0.25, max_iterations: int = 1_000, random_seed: int = 20250601) -> None:
        if regularization_c <= 0:
            raise ValueError("regularization_c must be positive")
        self.preprocessor = FoldPreprocessor()
        self.estimator = LogisticRegression(
            C=regularization_c,
            solver="lbfgs",
            max_iter=max_iterations,
            random_state=random_seed,
        )
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(self, matrix: np.ndarray, outcomes: Sequence[int], feature_names: Sequence[str], *, row_ids: Sequence[str] = ()) -> RegularizedLogisticClassifier:
        values, labels = _classification_data(matrix, outcomes, feature_names)
        identifiers = _row_ids(row_ids, len(values))
        transformed = self.preprocessor.fit_transform(values, feature_names, row_ids=identifiers)
        self.estimator.fit(transformed, labels)
        self.fit_row_ids = identifiers
        return self

    def predict_proba(self, matrix: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
        values = _inference_matrix(matrix, feature_names)
        transformed = self.preprocessor.transform(values, feature_names)
        return self.estimator.predict_proba(transformed)[:, 1]


class PoissonRunRegressor:
    def __init__(self, *, alpha: float = 1.0, max_iterations: int = 500) -> None:
        if alpha < 0:
            raise ValueError("Poisson regularization cannot be negative")
        self.preprocessor = FoldPreprocessor()
        self.home_model = PoissonRegressor(alpha=alpha, max_iter=max_iterations)
        self.away_model = PoissonRegressor(alpha=alpha, max_iter=max_iterations)
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(
        self,
        matrix: np.ndarray,
        home_runs: Sequence[float],
        away_runs: Sequence[float],
        feature_names: Sequence[str],
        *,
        row_ids: Sequence[str] = (),
    ) -> PoissonRunRegressor:
        values = _inference_matrix(matrix, feature_names)
        home = np.asarray(home_runs, dtype=float)
        away = np.asarray(away_runs, dtype=float)
        if home.shape != (len(values),) or away.shape != (len(values),) or np.any(home < 0) or np.any(away < 0):
            raise ValueError("Run targets must be aligned and nonnegative")
        identifiers = _row_ids(row_ids, len(values))
        transformed = self.preprocessor.fit_transform(values, feature_names, row_ids=identifiers)
        self.home_model.fit(transformed, home)
        self.away_model.fit(transformed, away)
        self.fit_row_ids = identifiers
        return self

    def predict_runs(self, matrix: np.ndarray, feature_names: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
        values = _inference_matrix(matrix, feature_names)
        transformed = self.preprocessor.transform(values, feature_names)
        return np.clip(self.home_model.predict(transformed), 0.05, 20.0), np.clip(self.away_model.predict(transformed), 0.05, 20.0)

    def predict_proba(self, matrix: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
        home, away = self.predict_runs(matrix, feature_names)
        # A regulation tie is split evenly as a restrained approximation to
        # extra innings; this is deterministic and stated rather than simulated.
        return np.asarray([
            _poisson_home_probability(float(home_mean), float(away_mean))
            for home_mean, away_mean in zip(home, away)
        ], dtype=float)


class NegativeBinomialRunRegressor:
    """Regularized log-mean model with a fitted NB2 predictive dispersion.

    The conditional means use the same fold-local, L2-regularized log-link fit
    as the Poisson model. Dispersion is then estimated only from that training
    fold's residuals. This keeps the comparison restrained while testing
    whether variance materially exceeds the Poisson mean assumption.
    """

    def __init__(self, *, mean_alpha: float = 1.0, minimum_dispersion: float = 1e-6) -> None:
        if minimum_dispersion <= 0:
            raise ValueError("Negative-binomial dispersion floor must be positive")
        self.mean_model = PoissonRunRegressor(alpha=mean_alpha)
        self.minimum_dispersion = minimum_dispersion
        self.home_dispersion_: float | None = None
        self.away_dispersion_: float | None = None
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(
        self,
        matrix: np.ndarray,
        home_runs: Sequence[float],
        away_runs: Sequence[float],
        feature_names: Sequence[str],
        *,
        row_ids: Sequence[str] = (),
    ) -> NegativeBinomialRunRegressor:
        self.mean_model.fit(
            matrix,
            home_runs,
            away_runs,
            feature_names,
            row_ids=row_ids,
        )
        home_mean, away_mean = self.mean_model.predict_runs(matrix, feature_names)
        self.home_dispersion_ = _nb2_dispersion(home_runs, home_mean, self.minimum_dispersion)
        self.away_dispersion_ = _nb2_dispersion(away_runs, away_mean, self.minimum_dispersion)
        self.fit_row_ids = self.mean_model.fit_row_ids
        return self

    @property
    def dispersion_diagnostic(self) -> dict[str, float | bool]:
        if self.home_dispersion_ is None or self.away_dispersion_ is None:
            raise RuntimeError("Negative-binomial model is not fitted")
        return {
            "home_alpha": self.home_dispersion_,
            "away_alpha": self.away_dispersion_,
            "material_overdispersion": max(self.home_dispersion_, self.away_dispersion_) >= 0.05,
        }

    def predict_runs(self, matrix: np.ndarray, feature_names: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
        return self.mean_model.predict_runs(matrix, feature_names)

    def predict_proba(self, matrix: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
        if self.home_dispersion_ is None or self.away_dispersion_ is None:
            raise RuntimeError("Negative-binomial model is not fitted")
        home, away = self.predict_runs(matrix, feature_names)
        return np.asarray([
            _negative_binomial_home_probability(
                float(home_mean),
                float(away_mean),
                self.home_dispersion_,
                self.away_dispersion_,
            )
            for home_mean, away_mean in zip(home, away)
        ], dtype=float)


class EmpiricalBayesTeamClassifier:
    """Shrink team win evidence toward .500 before comparing a matchup."""

    def __init__(self, *, prior_strength: float = 30.0, home_prior: float = 0.54) -> None:
        if prior_strength <= 0 or not 0 < home_prior < 1:
            raise ValueError("Empirical-Bayes prior parameters are invalid")
        self.prior_strength = prior_strength
        self.home_prior = home_prior
        self.team_probabilities_: dict[str, float] = {}

    def fit(
        self,
        home_team_ids: Sequence[str],
        away_team_ids: Sequence[str],
        outcomes: Sequence[int],
    ) -> EmpiricalBayesTeamClassifier:
        if not home_team_ids or len(home_team_ids) != len(away_team_ids) or len(home_team_ids) != len(outcomes):
            raise ValueError("Team matchups and outcomes must align")
        wins: dict[str, float] = defaultdict(float)
        games: dict[str, int] = defaultdict(int)
        for home, away, outcome in zip(home_team_ids, away_team_ids, outcomes):
            if not home or not away or home == away or outcome not in (0, 1):
                raise ValueError("Invalid team matchup evidence")
            wins[home] += outcome
            wins[away] += 1 - outcome
            games[home] += 1
            games[away] += 1
        self.team_probabilities_ = {
            team: (wins[team] + self.prior_strength * 0.5) / (games[team] + self.prior_strength)
            for team in games
        }
        return self

    def predict_proba(self, home_team_ids: Sequence[str], away_team_ids: Sequence[str]) -> np.ndarray:
        if not self.team_probabilities_:
            raise RuntimeError("Classifier is not fitted")
        if len(home_team_ids) != len(away_team_ids):
            raise ValueError("Team matchup arrays must align")
        home_advantage = _logit(self.home_prior)
        result = []
        for home, away in zip(home_team_ids, away_team_ids):
            home_strength = self.team_probabilities_.get(home, 0.5)
            away_strength = self.team_probabilities_.get(away, 0.5)
            result.append(_logistic(home_advantage + _logit(home_strength) - _logit(away_strength)))
        return np.asarray(result, dtype=float)


class LimitedXGBoostClassifier:
    """A deliberately small, deterministic XGBoost challenger.

    It accepts no evaluation set, so a caller cannot accidentally use locked
    holdout labels for early stopping. Hyperparameters must be selected from
    rolling development folds before this fixed-size estimator is fitted.
    """

    def __init__(
        self,
        *,
        n_estimators: int = 100,
        max_depth: int = 3,
        learning_rate: float = 0.05,
        min_child_weight: float = 8.0,
        reg_lambda: float = 5.0,
        random_seed: int = 20250601,
    ) -> None:
        if not 1 <= n_estimators <= 400 or not 1 <= max_depth <= 4 or not 0 < learning_rate <= 0.20:
            raise ValueError("XGBoost complexity exceeds the Gate 3 limits")
        self.preprocessor = FoldPreprocessor()
        self.parameters = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "learning_rate": learning_rate,
            "min_child_weight": min_child_weight,
            "reg_lambda": reg_lambda,
            "subsample": 0.85,
            "colsample_bytree": 0.85,
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "random_state": random_seed,
            "n_jobs": 1,
            "tree_method": "hist",
            "verbosity": 0,
        }
        self.estimator: object | None = None
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(self, matrix: np.ndarray, outcomes: Sequence[int], feature_names: Sequence[str], *, row_ids: Sequence[str] = ()) -> LimitedXGBoostClassifier:
        from xgboost import XGBClassifier

        values, labels = _classification_data(matrix, outcomes, feature_names)
        identifiers = _row_ids(row_ids, len(values))
        transformed = self.preprocessor.fit_transform(values, feature_names, row_ids=identifiers)
        self.estimator = XGBClassifier(**self.parameters)
        self.estimator.fit(transformed, labels, verbose=False)
        self.fit_row_ids = identifiers
        return self

    def predict_proba(self, matrix: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
        if self.estimator is None:
            raise RuntimeError("Classifier is not fitted")
        values = _inference_matrix(matrix, feature_names)
        transformed = self.preprocessor.transform(values, feature_names)
        return self.estimator.predict_proba(transformed)[:, 1]  # type: ignore[attr-defined]


def _classification_data(
    matrix: np.ndarray,
    outcomes: Sequence[int],
    feature_names: Sequence[str],
    *,
    require_two_classes: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    values = _inference_matrix(matrix, feature_names)
    labels = np.asarray(outcomes, dtype=int)
    if labels.shape != (len(values),) or not np.all(np.isin(labels, [0, 1])):
        raise ValueError("Binary outcomes must align with feature rows")
    if require_two_classes and len(np.unique(labels)) < 2:
        raise ValueError("Training data must contain both outcomes")
    return values, labels


def _inference_matrix(matrix: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] != len(tuple(feature_names)):
        raise ValueError("Feature matrix and names must align")
    if not np.isfinite(values).all():
        raise ValueError("Feature matrix must be finite")
    return values


def _index(feature_names: Sequence[str], name: str) -> int:
    try:
        return tuple(feature_names).index(name)
    except ValueError as exc:
        raise ValueError(f"Required feature is absent: {name}") from exc


def _row_ids(row_ids: Sequence[str], count: int) -> tuple[str, ...]:
    if row_ids and len(row_ids) != count:
        raise ValueError("row_ids must align with feature rows")
    return tuple(str(value) for value in row_ids) if row_ids else tuple(f"row-{index}" for index in range(count))


def _logit(probability: float) -> float:
    clipped = min(max(probability, 1e-6), 1 - 1e-6)
    return log(clipped / (1 - clipped))


def _logistic(value: float) -> float:
    return 1.0 / (1.0 + exp(-value))


def _poisson_home_probability(home_mean: float, away_mean: float) -> float:
    """Exact finite Poisson sum without adding a direct SciPy dependency."""

    maximum = int(max(60, ceil(max(home_mean, away_mean) + 12 * sqrt(max(home_mean, away_mean)))))
    home_mass = exp(-home_mean)
    away_mass = exp(-away_mean)
    away_below = 0.0
    win = tie = 0.0
    for runs in range(maximum + 1):
        win += home_mass * away_below
        tie += home_mass * away_mass
        away_below += away_mass
        home_mass *= home_mean / (runs + 1)
        away_mass *= away_mean / (runs + 1)
    return min(max(win + 0.5 * tie, 1e-6), 1 - 1e-6)


def _nb2_dispersion(
    outcomes: Sequence[float],
    means: Sequence[float],
    floor: float,
) -> float:
    observed = np.asarray(outcomes, dtype=float)
    expected = np.asarray(means, dtype=float)
    if observed.shape != expected.shape or len(observed) == 0:
        raise ValueError("Dispersion outcomes and fitted means must align")
    denominator = float(np.sum(expected ** 2))
    estimate = float(np.sum((observed - expected) ** 2 - expected) / denominator) if denominator > 0 else floor
    return min(max(estimate, floor), 5.0)


def _negative_binomial_home_probability(
    home_mean: float,
    away_mean: float,
    home_alpha: float,
    away_alpha: float,
) -> float:
    maximum = int(max(
        80,
        ceil(home_mean + 14 * sqrt(home_mean + home_alpha * home_mean ** 2)),
        ceil(away_mean + 14 * sqrt(away_mean + away_alpha * away_mean ** 2)),
    ))
    home_mass = _negative_binomial_masses(home_mean, home_alpha, maximum)
    away_mass = _negative_binomial_masses(away_mean, away_alpha, maximum)
    away_below = 0.0
    win = tie = 0.0
    for home_probability, away_probability in zip(home_mass, away_mass):
        win += home_probability * away_below
        tie += home_probability * away_probability
        away_below += away_probability
    return min(max(win + 0.5 * tie, 1e-6), 1 - 1e-6)


def _negative_binomial_masses(mean: float, alpha: float, maximum: int) -> list[float]:
    size = 1.0 / max(alpha, 1e-9)
    success = size / (size + mean)
    current = success ** size
    result = [current]
    for count in range(maximum):
        current *= (count + size) / (count + 1) * (1.0 - success)
        result.append(current)
    return result
