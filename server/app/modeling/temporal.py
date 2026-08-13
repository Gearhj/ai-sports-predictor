"""Date-group temporal splits and a single-use locked final holdout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import ceil, log
from typing import Mapping, Sequence


@dataclass(frozen=True)
class UnlabeledGame:
    game_id: str
    event_at: datetime
    features: Mapping[str, object]
    lifecycle: str
    source_mode: str
    home_team_id: str | None = None
    away_team_id: str | None = None
    market_home_probability: float | None = None
    closing_home_probability: float | None = None


@dataclass(frozen=True)
class LabeledGame:
    game_id: str
    event_at: datetime
    features: Mapping[str, object]
    home_win: int
    lifecycle: str = "FINAL"
    source_mode: str = "LIVE"
    home_runs: float | None = None
    away_runs: float | None = None
    home_team_id: str | None = None
    away_team_id: str | None = None
    market_home_probability: float | None = None
    closing_home_probability: float | None = None

    def __post_init__(self) -> None:
        if not self.game_id:
            raise ValueError("game_id is required")
        if self.event_at.tzinfo is None or self.event_at.utcoffset() is None:
            raise ValueError("event_at must be timezone-aware")
        if self.home_win not in (0, 1):
            raise ValueError("home_win must be binary")
        if (self.home_runs is None) != (self.away_runs is None):
            raise ValueError("Home and away run labels must be supplied together")
        if self.home_runs is not None and self.away_runs is not None:
            if self.home_runs == self.away_runs:
                raise ValueError("Completed baseball games cannot have tied final scores")
            if int(self.home_runs > self.away_runs) != self.home_win:
                raise ValueError("home_win must agree with the final run labels")
        if self.market_home_probability is not None and not 0 < self.market_home_probability < 1:
            raise ValueError("Market probability must be strictly between zero and one")
        if self.closing_home_probability is not None and not 0 < self.closing_home_probability < 1:
            raise ValueError("Closing probability must be strictly between zero and one")

    def unlabeled(self) -> UnlabeledGame:
        return UnlabeledGame(
            game_id=self.game_id,
            event_at=self.event_at,
            features=self.features,
            lifecycle=self.lifecycle,
            source_mode=self.source_mode,
            home_team_id=self.home_team_id,
            away_team_id=self.away_team_id,
            market_home_probability=self.market_home_probability,
            closing_home_probability=self.closing_home_probability,
        )


@dataclass(frozen=True)
class RunHoldoutMetrics:
    """Run-prediction metrics calculated on one identical locked sample."""

    observations: int
    home_runs_rmse: float
    away_runs_rmse: float
    total_runs_rmse: float
    home_runs_mae: float
    away_runs_mae: float
    total_runs_mae: float
    home_runs_poisson_deviance: float
    away_runs_poisson_deviance: float
    total_runs_poisson_deviance: float


@dataclass(frozen=True)
class FinalHoldoutReport:
    classification: object
    home_runs_rmse: float | None
    away_runs_rmse: float | None
    total_runs_rmse: float | None
    home_runs_mae: float | None = None
    away_runs_mae: float | None = None
    total_runs_mae: float | None = None
    home_runs_poisson_deviance: float | None = None
    away_runs_poisson_deviance: float | None = None
    total_runs_poisson_deviance: float | None = None
    comparison_run_metrics: Mapping[str, RunHoldoutMetrics] | None = None
    comparison_classification: Mapping[str, object] | None = None
    market_classification: object | None = None
    market_observations: int = 0


class LockedHoldout:
    """Expose holdout covariates while keeping labels behind one final evaluation."""

    def __init__(self, rows: Sequence[LabeledGame]) -> None:
        if not rows:
            raise ValueError("Locked holdout cannot be empty")
        self._observations = tuple(row.unlabeled() for row in rows)
        self._outcomes = tuple(row.home_win for row in rows)
        self._home_runs = tuple(row.home_runs for row in rows)
        self._away_runs = tuple(row.away_runs for row in rows)
        self._evaluated = False

    @property
    def observations(self) -> tuple[UnlabeledGame, ...]:
        return self._observations

    @property
    def game_ids(self) -> tuple[str, ...]:
        return tuple(row.game_id for row in self._observations)

    @property
    def event_dates(self) -> tuple[date, ...]:
        return tuple(row.event_at.date() for row in self._observations)

    @property
    def evaluated(self) -> bool:
        return self._evaluated

    def final_evaluate(
        self,
        probabilities: Sequence[float],
        *,
        predicted_home_runs: Sequence[float] | None = None,
        predicted_away_runs: Sequence[float] | None = None,
        comparison_probabilities: Mapping[str, Sequence[float]] | None = None,
        comparison_run_predictions: Mapping[
            str,
            tuple[Sequence[float], Sequence[float]],
        ] | None = None,
    ) -> FinalHoldoutReport:
        """Reveal labels internally exactly once, only to calculate final metrics."""

        if self._evaluated:
            raise RuntimeError("The locked holdout has already been evaluated")
        if len(probabilities) != len(self._observations):
            raise ValueError("Holdout predictions must match holdout observations")
        if (predicted_home_runs is None) != (predicted_away_runs is None):
            raise ValueError("Both home and away run predictions are required")
        if comparison_run_predictions and predicted_home_runs is None:
            raise ValueError("Candidate run predictions are required with run comparators")
        from app.modeling.metrics import classification_metrics

        classification = classification_metrics(probabilities, self._outcomes)
        comparison: dict[str, object] = {}
        for name, values in sorted((comparison_probabilities or {}).items()):
            if len(values) != len(self._observations):
                raise ValueError(f"Holdout comparator {name} does not align with the locked holdout")
            comparison[name] = classification_metrics(values, self._outcomes)
        market_indices = [
            index
            for index, row in enumerate(self._observations)
            if row.closing_home_probability is not None
        ]
        market_classification = None
        if market_indices:
            market_outcomes = [self._outcomes[index] for index in market_indices]
            if len(set(market_outcomes)) >= 2:
                market_classification = classification_metrics(
                    [float(self._observations[index].closing_home_probability) for index in market_indices],
                    market_outcomes,
                )
        home_rmse = away_rmse = total_rmse = None
        home_mae = away_mae = total_mae = None
        home_deviance = away_deviance = total_deviance = None
        run_metrics: dict[str, RunHoldoutMetrics] = {}
        if predicted_home_runs is not None and predicted_away_runs is not None:
            if any(value is None for value in (*self._home_runs, *self._away_runs)):
                raise ValueError("Run labels are not available for every holdout game")
            actual_home = [float(value) for value in self._home_runs if value is not None]
            actual_away = [float(value) for value in self._away_runs if value is not None]
            named_predictions = {
                "candidate": (predicted_home_runs, predicted_away_runs),
                **dict(comparison_run_predictions or {}),
            }
            if "candidate" in (comparison_run_predictions or {}):
                raise ValueError("Run comparator name 'candidate' is reserved")
            for name, (home_prediction, away_prediction) in sorted(named_predictions.items()):
                run_metrics[name] = _run_holdout_metrics(
                    actual_home,
                    actual_away,
                    home_prediction,
                    away_prediction,
                    expected_observations=len(self._observations),
                    label=name,
                )
            candidate = run_metrics["candidate"]
            home_rmse = candidate.home_runs_rmse
            away_rmse = candidate.away_runs_rmse
            total_rmse = candidate.total_runs_rmse
            home_mae = candidate.home_runs_mae
            away_mae = candidate.away_runs_mae
            total_mae = candidate.total_runs_mae
            home_deviance = candidate.home_runs_poisson_deviance
            away_deviance = candidate.away_runs_poisson_deviance
            total_deviance = candidate.total_runs_poisson_deviance
        self._evaluated = True
        return FinalHoldoutReport(
            classification=classification,
            home_runs_rmse=home_rmse,
            away_runs_rmse=away_rmse,
            total_runs_rmse=total_rmse,
            home_runs_mae=home_mae,
            away_runs_mae=away_mae,
            total_runs_mae=total_mae,
            home_runs_poisson_deviance=home_deviance,
            away_runs_poisson_deviance=away_deviance,
            total_runs_poisson_deviance=total_deviance,
            comparison_run_metrics={
                name: metrics
                for name, metrics in run_metrics.items()
                if name != "candidate"
            },
            comparison_classification=comparison,
            market_classification=market_classification,
            market_observations=len(market_indices),
        )


@dataclass(frozen=True)
class DevelopmentHoldoutSplit:
    development: tuple[LabeledGame, ...]
    holdout: LockedHoldout
    first_holdout_date: date


@dataclass(frozen=True)
class RollingOriginFold:
    index: int
    train: tuple[LabeledGame, ...]
    validation: tuple[LabeledGame, ...]

    @property
    def trained_through(self) -> datetime:
        return max(row.event_at for row in self.train)


def date_group_chronological_holdout(
    rows: Sequence[LabeledGame],
    *,
    holdout_fraction: float = 0.20,
    min_development_dates: int = 3,
    require_live: bool = True,
) -> DevelopmentHoldoutSplit:
    if not 0 < holdout_fraction < 1:
        raise ValueError("holdout_fraction must be between zero and one")
    ordered = _validated_rows(rows, require_live=require_live)
    dates = sorted({row.event_at.date() for row in ordered})
    holdout_dates = max(1, ceil(len(dates) * holdout_fraction))
    split_index = len(dates) - holdout_dates
    if split_index < min_development_dates:
        raise ValueError("Not enough date groups before the requested holdout")
    first_holdout = dates[split_index]
    development = tuple(row for row in ordered if row.event_at.date() < first_holdout)
    held_out = tuple(row for row in ordered if row.event_at.date() >= first_holdout)
    if not development or not held_out:
        raise ValueError("Development and holdout groups must both be non-empty")
    return DevelopmentHoldoutSplit(development, LockedHoldout(held_out), first_holdout)


def rolling_origin_folds(
    rows: Sequence[LabeledGame],
    *,
    min_train_dates: int,
    validation_dates: int = 1,
    step_dates: int | None = None,
    require_live: bool = True,
) -> tuple[RollingOriginFold, ...]:
    if min_train_dates < 1 or validation_dates < 1:
        raise ValueError("Temporal windows must contain at least one date")
    step = validation_dates if step_dates is None else step_dates
    if step < validation_dates:
        raise ValueError("step_dates cannot create overlapping validation windows")
    ordered = _validated_rows(rows, require_live=require_live)
    dates = sorted({row.event_at.date() for row in ordered})
    folds: list[RollingOriginFold] = []
    validation_start = min_train_dates
    while validation_start + validation_dates <= len(dates):
        train_dates = set(dates[:validation_start])
        validation_group = set(dates[validation_start:validation_start + validation_dates])
        train = tuple(row for row in ordered if row.event_at.date() in train_dates)
        validation = tuple(row for row in ordered if row.event_at.date() in validation_group)
        if max(row.event_at.date() for row in train) >= min(row.event_at.date() for row in validation):
            raise AssertionError("Temporal fold boundary is not strictly chronological")
        folds.append(RollingOriginFold(len(folds), train, validation))
        validation_start += step
    if not folds:
        raise ValueError("No rolling-origin fold can be formed")
    return tuple(folds)


def _validated_rows(rows: Sequence[LabeledGame], *, require_live: bool) -> tuple[LabeledGame, ...]:
    if not rows:
        raise ValueError("At least one labeled game is required")
    game_ids = [row.game_id for row in rows]
    if len(set(game_ids)) != len(game_ids):
        raise ValueError("Each game may appear only once in a modeling dataset")
    if require_live and any(row.source_mode != "LIVE" for row in rows):
        raise ValueError("Model development data must be LIVE-only")
    return tuple(sorted(rows, key=lambda row: (row.event_at, row.game_id)))


def _run_holdout_metrics(
    actual_home: Sequence[float],
    actual_away: Sequence[float],
    predicted_home: Sequence[float],
    predicted_away: Sequence[float],
    *,
    expected_observations: int,
    label: str,
) -> RunHoldoutMetrics:
    if not label:
        raise ValueError("Run comparator names cannot be empty")
    if len(predicted_home) != expected_observations or len(predicted_away) != expected_observations:
        raise ValueError(f"Run predictions for {label} must match holdout observations")
    from app.modeling.metrics import rmse

    actual_total = [home + away for home, away in zip(actual_home, actual_away)]
    predicted_total = [
        float(home) + float(away)
        for home, away in zip(predicted_home, predicted_away)
    ]
    return RunHoldoutMetrics(
        observations=expected_observations,
        home_runs_rmse=rmse(actual_home, predicted_home),
        away_runs_rmse=rmse(actual_away, predicted_away),
        total_runs_rmse=rmse(actual_total, predicted_total),
        home_runs_mae=_mae(actual_home, predicted_home),
        away_runs_mae=_mae(actual_away, predicted_away),
        total_runs_mae=_mae(actual_total, predicted_total),
        home_runs_poisson_deviance=_poisson_deviance(actual_home, predicted_home),
        away_runs_poisson_deviance=_poisson_deviance(actual_away, predicted_away),
        total_runs_poisson_deviance=_poisson_deviance(actual_total, predicted_total),
    )


def _mae(actual: Sequence[float], predicted: Sequence[float]) -> float:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("MAE inputs must have equal non-zero length")
    return sum(abs(float(left) - float(right)) for left, right in zip(actual, predicted)) / len(actual)


def _poisson_deviance(actual: Sequence[float], predicted: Sequence[float]) -> float:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("Poisson deviance inputs must have equal non-zero length")
    total = 0.0
    for observed, estimate in zip(actual, predicted):
        value = float(observed)
        mean = max(float(estimate), 1e-12)
        if value < 0:
            raise ValueError("Poisson outcomes cannot be negative")
        term = mean if value == 0 else value * log(value / mean) - (value - mean)
        total += 2.0 * term
    return total / len(actual)
