"""Provider-free Gen-2 game count comparators and dependence diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from math import exp, isfinite, lgamma, log
from typing import Iterable, Mapping, Sequence

import numpy as np
from sklearn.linear_model import PoissonRegressor


COUNT_BASELINES_VERSION = "mlb_gen2_count_baselines_v1"
SHARED_ENVIRONMENT_VERSION = "mlb_gen2_shared_environment_count_v1"
COUNT_FEATURE_SCHEMA_VERSION = "mlb_gen2_count_features_v1"
COUNT_FEATURE_NAMES = (
    "simulator_mean_home_runs",
    "simulator_mean_away_runs",
    "simulator_variance_home_runs",
    "simulator_variance_away_runs",
    "event_strength_home",
    "event_strength_away",
    "starter_duration_home",
    "starter_duration_away",
    "bullpen_strength_home",
    "bullpen_strength_away",
    "prior_period_park_run_factor",
    "weather_run_environment",
)


class CountModelError(ValueError):
    """Raised when governed count-model evidence is invalid."""


def _checksum(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


COUNT_FEATURE_SCHEMA_HASH = _checksum(
    {
        "version": COUNT_FEATURE_SCHEMA_VERSION,
        "ordered_features": COUNT_FEATURE_NAMES,
        "missingness": "TRAIN_FOLD_MEDIAN",
        "temporal_rule": "MAX_EVIDENCE_END_LT_FIRST_PITCH_AND_ELIGIBLE_LE_CUTOFF",
    }
)


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CountModelError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class CountTrainingRow:
    row_id: str
    game_id: str
    official_date: date
    first_pitch_at: datetime
    computed_at: datetime
    evidence_end_at: datetime
    eligible_at: datetime
    label_available_at: datetime
    home_runs: int
    away_runs: int
    features: Mapping[str, float | int | None]
    source_game_ids: tuple[str, ...]
    evidence_checksum: str
    evidence_tier: str = "A"

    def __post_init__(self) -> None:
        if not self.row_id or not self.game_id:
            raise CountModelError("count row and game identities are required")
        first_pitch = _utc(self.first_pitch_at, "first_pitch_at")
        computed = _utc(self.computed_at, "computed_at")
        evidence_end = _utc(self.evidence_end_at, "evidence_end_at")
        eligible = _utc(self.eligible_at, "eligible_at")
        label_available = _utc(self.label_available_at, "label_available_at")
        if computed > first_pitch:
            raise CountModelError("count features were computed after first pitch")
        if evidence_end >= first_pitch:
            raise CountModelError("count evidence must end before first pitch")
        if evidence_end > computed:
            raise CountModelError("count evidence cannot end after features were computed")
        if eligible < evidence_end:
            raise CountModelError("count evidence cannot be eligible before it ends")
        if eligible > computed:
            raise CountModelError("count evidence is not eligible at computed_at")
        if label_available < first_pitch:
            raise CountModelError("count label cannot be available before first pitch")
        if self.game_id in self.source_game_ids:
            raise CountModelError("target-game evidence is forbidden")
        if (
            not self.source_game_ids
            or any(not identity for identity in self.source_game_ids)
            or len(set(self.source_game_ids)) != len(self.source_game_ids)
        ):
            raise CountModelError("count source game identities must be nonempty and unique")
        if self.home_runs < 0 or self.away_runs < 0:
            raise CountModelError("run labels must be nonnegative")
        if self.evidence_tier != "A":
            raise CountModelError("strict count models accept Tier A evidence only")
        if set(self.features) != set(COUNT_FEATURE_NAMES):
            raise CountModelError("count feature mapping does not match frozen schema")
        for name, value in self.features.items():
            if value is not None and (not isinstance(value, (int, float)) or not isfinite(float(value))):
                raise CountModelError(f"feature {name} must be finite numeric or null")
        if len(self.evidence_checksum) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.evidence_checksum.lower()
        ):
            raise CountModelError("evidence_checksum must be SHA-256")


@dataclass(frozen=True)
class CountModelEvidence:
    version: str
    feature_schema_hash: str
    rows: int
    dates: int
    fit_cutoff: datetime
    training_population_checksum: str
    evidence_checksum: str
    preprocessing_checksum: str
    parameter_checksum: str
    home_overdispersion_alpha: float
    away_overdispersion_alpha: float
    residual_home_away_covariance: float
    shared_environment_fraction: float


@dataclass(frozen=True)
class CountPrediction:
    mean_home_runs: float
    mean_away_runs: float
    home_nb2_alpha: float
    away_nb2_alpha: float
    shared_environment_lambda: float
    model_version: str

    def __post_init__(self) -> None:
        values = (
            self.mean_home_runs,
            self.mean_away_runs,
            self.home_nb2_alpha,
            self.away_nb2_alpha,
            self.shared_environment_lambda,
        )
        if any(not isfinite(value) or value < 0 for value in values):
            raise CountModelError("count prediction parameters must be finite/nonnegative")
        if self.shared_environment_lambda > min(self.mean_home_runs, self.mean_away_runs):
            raise CountModelError("shared environment exceeds a marginal mean")


@dataclass(frozen=True)
class FittedCountModels:
    home_poisson: PoissonRegressor
    away_poisson: PoissonRegressor
    medians: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    evidence: CountModelEvidence

    def predict(self, features: Mapping[str, float | int | None]) -> CountPrediction:
        matrix = _matrix((features,), self.medians)
        transformed = (matrix - np.asarray(self.means)) / np.asarray(self.scales)
        home = float(self.home_poisson.predict(transformed)[0])
        away = float(self.away_poisson.predict(transformed)[0])
        shared = min(home, away) * self.evidence.shared_environment_fraction
        return CountPrediction(
            mean_home_runs=home,
            mean_away_runs=away,
            home_nb2_alpha=self.evidence.home_overdispersion_alpha,
            away_nb2_alpha=self.evidence.away_overdispersion_alpha,
            shared_environment_lambda=shared,
            model_version=SHARED_ENVIRONMENT_VERSION,
        )


def _native_matrix(rows: Sequence[Mapping[str, float | int | None]]) -> np.ndarray:
    for row in rows:
        if set(row) != set(COUNT_FEATURE_NAMES):
            raise CountModelError("count feature mapping does not match frozen schema")
    return np.asarray(
        [
            [np.nan if row[name] is None else float(row[name]) for name in COUNT_FEATURE_NAMES]
            for row in rows
        ],
        dtype=float,
    )


def _matrix(
    rows: Sequence[Mapping[str, float | int | None]],
    medians: Sequence[float] | None = None,
) -> np.ndarray:
    matrix = _native_matrix(rows)
    learned = np.nanmedian(matrix, axis=0) if medians is None else np.asarray(medians)
    learned = np.where(np.isnan(learned), 0.0, learned)
    if learned.shape != (len(COUNT_FEATURE_NAMES),):
        raise CountModelError("count imputation vector has the wrong shape")
    missing = np.where(np.isnan(matrix))
    matrix[missing] = learned[missing[1]]
    return matrix


def _nb2_alpha(target: np.ndarray, mean: np.ndarray) -> float:
    numerator = float(np.sum((target - mean) ** 2 - mean))
    denominator = float(np.sum(mean**2))
    return max(0.0, numerator / denominator) if denominator > 0 else 0.0


def fit_count_models(
    rows: Iterable[CountTrainingRow],
    *,
    fit_cutoff: datetime,
    current_game_id: str,
    regularization: float = 1.0,
) -> FittedCountModels:
    """Fit independent Poisson means, NB2 diagnostics, and shared dependence."""

    if not isfinite(regularization) or regularization < 0:
        raise CountModelError("regularization must be finite and nonnegative")
    boundary = _utc(fit_cutoff, "fit_cutoff")
    if not current_game_id:
        raise CountModelError("current_game_id is required")
    materialized = tuple(
        row
        for row in rows
        if row.game_id != current_game_id
        and _utc(row.label_available_at, "label_available_at") <= boundary
    )
    if len(materialized) < 20:
        raise CountModelError("count-model fitting requires at least 20 games")
    semantic: dict[str, CountTrainingRow] = {}
    for row in materialized:
        prior = semantic.get(row.game_id)
        if prior is not None and prior != row:
            raise CountModelError("conflicting duplicate count row")
        semantic[row.game_id] = row
    training = tuple(sorted(semantic.values(), key=lambda row: (row.official_date, row.game_id)))
    if len(training) < 20:
        raise CountModelError("count-model fitting requires 20 unique games")
    native = _native_matrix(tuple(row.features for row in training))
    medians = np.nanmedian(native, axis=0)
    medians = np.where(np.isnan(medians), 0.0, medians)
    matrix = _matrix(tuple(row.features for row in training), medians)
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales = np.where(scales <= 1e-12, 1.0, scales)
    transformed = (matrix - means) / scales
    home_target = np.asarray([row.home_runs for row in training], dtype=float)
    away_target = np.asarray([row.away_runs for row in training], dtype=float)
    home_model = PoissonRegressor(alpha=regularization, max_iter=2_000)
    away_model = PoissonRegressor(alpha=regularization, max_iter=2_000)
    home_model.fit(transformed, home_target)
    away_model.fit(transformed, away_target)
    home_mean = np.asarray(home_model.predict(transformed), dtype=float)
    away_mean = np.asarray(away_model.predict(transformed), dtype=float)
    home_residual = home_target - home_mean
    away_residual = away_target - away_mean
    covariance = float(np.mean(home_residual * away_residual))
    marginal_floor = min(float(home_mean.mean()), float(away_mean.mean()))
    shared_fraction = min(0.95, max(0.0, covariance / marginal_floor)) if marginal_floor > 0 else 0.0
    preprocessing = {
        "medians": medians.tolist(),
        "means": means.tolist(),
        "scales": scales.tolist(),
        "fit_scope": "TRAINING_ROWS_ONLY",
    }
    parameters = {
        "home_coef": home_model.coef_.tolist(),
        "home_intercept": float(home_model.intercept_),
        "away_coef": away_model.coef_.tolist(),
        "away_intercept": float(away_model.intercept_),
        "regularization": regularization,
        "shared_fraction": shared_fraction,
    }
    evidence = CountModelEvidence(
        version=SHARED_ENVIRONMENT_VERSION,
        feature_schema_hash=COUNT_FEATURE_SCHEMA_HASH,
        rows=len(training),
        dates=len({row.official_date for row in training}),
        fit_cutoff=boundary,
        training_population_checksum=_checksum(
            {
                "fit_cutoff": boundary.isoformat(),
                "current_game_id": current_game_id,
                "row_ids": [row.row_id for row in training],
                "game_ids": [row.game_id for row in training],
            }
        ),
        evidence_checksum=_checksum(
            [
                {
                    "row_id": row.row_id,
                    "game_id": row.game_id,
                    "official_date": row.official_date.isoformat(),
                    "first_pitch_at": row.first_pitch_at.isoformat(),
                    "computed_at": row.computed_at.isoformat(),
                    "evidence_end_at": row.evidence_end_at.isoformat(),
                    "eligible_at": row.eligible_at.isoformat(),
                    "label_available_at": row.label_available_at.isoformat(),
                    "home_runs": row.home_runs,
                    "away_runs": row.away_runs,
                    "features": {name: row.features[name] for name in COUNT_FEATURE_NAMES},
                    "evidence_checksum": row.evidence_checksum,
                    "source_game_ids": row.source_game_ids,
                    "evidence_tier": row.evidence_tier,
                }
                for row in training
            ]
        ),
        preprocessing_checksum=_checksum(preprocessing),
        parameter_checksum=_checksum(parameters),
        home_overdispersion_alpha=_nb2_alpha(home_target, home_mean),
        away_overdispersion_alpha=_nb2_alpha(away_target, away_mean),
        residual_home_away_covariance=covariance,
        shared_environment_fraction=shared_fraction,
    )
    return FittedCountModels(
        home_poisson=home_model,
        away_poisson=away_model,
        medians=tuple(float(value) for value in medians),
        means=tuple(float(value) for value in means),
        scales=tuple(float(value) for value in scales),
        evidence=evidence,
    )


def poisson_pmf(value: int, mean: float) -> float:
    if value < 0 or not isfinite(mean) or mean < 0:
        raise CountModelError("invalid Poisson parameters")
    if mean == 0:
        return 1.0 if value == 0 else 0.0
    return exp(-mean + value * log(mean) - lgamma(value + 1))


def nb2_pmf(value: int, mean: float, alpha: float) -> float:
    if value < 0 or mean < 0 or alpha < 0 or not all(isfinite(v) for v in (mean, alpha)):
        raise CountModelError("invalid NB2 parameters")
    if alpha <= 1e-12:
        return poisson_pmf(value, mean)
    if mean == 0:
        return 1.0 if value == 0 else 0.0
    size = 1.0 / alpha
    probability = size / (size + mean)
    log_probability = (
        lgamma(value + size)
        - lgamma(size)
        - lgamma(value + 1)
        + size * log(probability)
        + value * log(1.0 - probability)
    )
    return exp(log_probability)


def shared_environment_joint_distribution(
    prediction: CountPrediction,
    *,
    max_runs: int = 25,
    maximum_omitted_mass: float = 1e-6,
) -> np.ndarray:
    """Return a shared-Poisson plus idiosyncratic-NB2 joint distribution.

    The bounded matrix is returned only when its omitted tail mass is below the
    declared tolerance; otherwise callers must expand ``max_runs`` rather than
    silently treating a materially truncated conditional distribution as the
    full joint score law.
    """

    if not 5 <= max_runs <= 50:
        raise CountModelError("max_runs must be in 5..50")
    if not isfinite(maximum_omitted_mass) or not 0 <= maximum_omitted_mass < 1:
        raise CountModelError("maximum_omitted_mass must be finite and in [0,1)")
    shared = prediction.shared_environment_lambda
    home_only = prediction.mean_home_runs - shared
    away_only = prediction.mean_away_runs - shared
    joint = np.zeros((max_runs + 1, max_runs + 1), dtype=float)
    for home in range(max_runs + 1):
        for away in range(max_runs + 1):
            probability = 0.0
            for common in range(min(home, away) + 1):
                probability += (
                    poisson_pmf(common, shared)
                    * nb2_pmf(home - common, home_only, prediction.home_nb2_alpha)
                    * nb2_pmf(away - common, away_only, prediction.away_nb2_alpha)
                )
            joint[home, away] = probability
    mass = float(joint.sum())
    if not 0 < mass <= 1.0 + 1e-12:
        raise CountModelError("joint score distribution has invalid mass")
    omitted_mass = max(0.0, 1.0 - mass)
    if omitted_mass > maximum_omitted_mass:
        raise CountModelError(
            "joint score grid omits probability mass above the governed tolerance"
        )
    joint /= mass
    return joint
