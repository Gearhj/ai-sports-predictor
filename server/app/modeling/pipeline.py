"""High-level, leakage-resistant temporal model development protocol."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from app.modeling.ablation import AblationSpec, apply_ablation
from app.modeling.betting import BettingThresholdResult, simulate_betting_thresholds
from app.modeling.calibration import CalibrationSelection, select_probability_calibrator
from app.modeling.diagnostics import OODDetector, OODReport
from app.modeling.estimators import (
    EmpiricalBayesTeamClassifier,
    HomePriorClassifier,
    LimitedXGBoostClassifier,
    NegativeBinomialRunRegressor,
    PoissonRunRegressor,
    RatingClassifier,
    RegularizedLogisticClassifier,
    RunStrengthClassifier,
)
from app.modeling.metrics import (
    ClassificationMetrics,
    PairedBootstrapDelta,
    classification_metrics,
    paired_block_bootstrap_delta,
    probability_log_loss,
)
from app.modeling.schema import FrozenFeatureSchema, GATE3_FEATURE_SCHEMA
from app.modeling.stacking import NonnegativeStacker, OOFPredictionRow
from app.modeling.temporal import (
    DevelopmentHoldoutSplit,
    FinalHoldoutReport,
    LabeledGame,
    RollingOriginFold,
    UnlabeledGame,
    date_group_chronological_holdout,
    rolling_origin_folds,
)


@dataclass(frozen=True)
class ModelingConfig:
    holdout_fraction: float = 0.20
    minimum_holdout_rows: int = 1
    min_development_dates: int = 40
    min_train_dates: int = 30
    validation_dates: int = 7
    step_dates: int = 7
    minimum_rolling_folds: int = 3
    logistic_c: float = 0.25
    poisson_alpha: float = 1.0
    home_prior_probability: float = 0.54
    home_prior_strength: float = 20.0
    team_prior_strength: float = 30.0
    include_xgboost: bool = True
    minimum_xgboost_rows: int = 500
    xgboost_estimators: int = 120
    xgboost_max_depth: int = 3
    xgboost_learning_rate: float = 0.05
    calibration_selection_fraction: float = 0.30
    minimum_isotonic_samples: int = 500
    stack_l2: float = 0.002
    bootstrap_resamples: int = 500
    random_seed: int = 20250601
    run_ablations: bool = True
    ablation_families: tuple[str, ...] = ("recent", "lifecycle")
    ablation_features: tuple[str, ...] = ("home_lineup_xwoba", "away_lineup_xwoba")
    betting_thresholds: tuple[float, ...] = (0.0, 0.02, 0.04, 0.06)


@dataclass(frozen=True)
class FoldFitAudit:
    fold_index: int
    model_name: str
    trained_through: datetime
    train_game_ids: tuple[str, ...]
    validation_game_ids: tuple[str, ...]
    fitted_row_ids: tuple[str, ...]


@dataclass(frozen=True)
class StackFoldAudit:
    """One strictly temporal meta-model fit and its untouched validation rows."""

    fold_index: int
    trained_through: datetime
    validation_started_at: datetime
    validation_ended_at: datetime
    train_game_ids: tuple[str, ...]
    validation_game_ids: tuple[str, ...]
    weights: Mapping[str, float]


@dataclass(frozen=True)
class StackOOFPredictionRow:
    """A stack prediction whose outcome was unavailable to its fitted weights."""

    game_id: str
    event_at: datetime
    trained_through: datetime
    outcome: int
    predictions: Mapping[str, float]
    raw_stacked_probability: float
    stack_fold_index: int

    def __post_init__(self) -> None:
        if self.trained_through >= self.event_at:
            raise ValueError("Stack OOF predictions must use strictly earlier meta-training rows")


@dataclass(frozen=True)
class CrossFittedStackResult:
    rows: tuple[StackOOFPredictionRow, ...]
    audits: tuple[StackFoldAudit, ...]


@dataclass(frozen=True)
class OOFGenerationResult:
    rows: tuple[OOFPredictionRow, ...]
    audits: tuple[FoldFitAudit, ...]


@dataclass(frozen=True)
class PredictionBundle:
    game_ids: tuple[str, ...]
    base_probabilities: Mapping[str, tuple[float, ...]]
    raw_stacked_probability: tuple[float, ...]
    calibrated_probability: tuple[float, ...]
    expected_home_runs: tuple[float, ...] | None
    expected_away_runs: tuple[float, ...] | None


class FittedModelBundle:
    """Final development-fit models; prediction accepts covariates, never labels."""

    def __init__(
        self,
        *,
        schema: FrozenFeatureSchema,
        classifiers: Mapping[str, object],
        stacker: NonnegativeStacker,
        calibration: CalibrationSelection,
        team_model: EmpiricalBayesTeamClassifier | None,
        poisson_model: PoissonRunRegressor | None,
        negative_binomial_model: NegativeBinomialRunRegressor | None,
    ) -> None:
        self.schema = schema
        self.classifiers = dict(classifiers)
        self.stacker = stacker
        self.calibration = calibration
        self.team_model = team_model
        self.poisson_model = poisson_model
        self.negative_binomial_model = negative_binomial_model

    def predict(self, observations: Sequence[UnlabeledGame]) -> PredictionBundle:
        if not observations:
            raise ValueError("At least one inference observation is required")
        matrix = self.schema.matrix([row.features for row in observations])
        names = self.schema.output_names
        base: dict[str, np.ndarray] = {
            name: np.asarray(model.predict_proba(matrix, names), dtype=float)  # type: ignore[attr-defined]
            for name, model in self.classifiers.items()
        }
        home_runs = away_runs = None
        if self.poisson_model is not None:
            home_runs, away_runs = self.poisson_model.predict_runs(matrix, names)
            base["poisson"] = self.poisson_model.predict_proba(matrix, names)
        if self.negative_binomial_model is not None:
            base["negative_binomial"] = self.negative_binomial_model.predict_proba(matrix, names)
        if self.team_model is not None:
            if any(row.home_team_id is None or row.away_team_id is None for row in observations):
                raise ValueError("Team IDs are required by the fitted empirical-Bayes model")
            base["team_eb"] = self.team_model.predict_proba(
                [str(row.home_team_id) for row in observations],
                [str(row.away_team_id) for row in observations],
            )
        raw = self.stacker.predict(base)
        calibrated = self.calibration.calibrator.transform(raw)  # type: ignore[attr-defined]
        return PredictionBundle(
            game_ids=tuple(row.game_id for row in observations),
            base_probabilities={name: tuple(float(value) for value in values) for name, values in sorted(base.items())},
            raw_stacked_probability=tuple(float(value) for value in raw),
            calibrated_probability=tuple(float(value) for value in calibrated),
            expected_home_runs=tuple(float(value) for value in home_runs) if home_runs is not None else None,
            expected_away_runs=tuple(float(value) for value in away_runs) if away_runs is not None else None,
        )


@dataclass(frozen=True)
class TemporalModelDevelopmentResult:
    fitted_bundle: FittedModelBundle
    calibration: CalibrationSelection
    feature_names: tuple[str, ...]
    feature_schema_hash: str
    hyperparameters: Mapping[str, Any]
    ensemble_weights: Mapping[str, float]
    temporal_windows: Mapping[str, Any]
    fold_audits: tuple[FoldFitAudit, ...]
    stack_fold_audits: tuple[StackFoldAudit, ...]
    oof_predictions: tuple[StackOOFPredictionRow, ...]
    oof_metrics: Mapping[str, ClassificationMetrics]
    paired_deltas_vs_stack: Mapping[str, PairedBootstrapDelta]
    holdout_predictions: PredictionBundle
    holdout_metrics: FinalHoldoutReport
    calibration_selection_scores: Mapping[str, float]
    ablation_metrics: Mapping[str, Mapping[str, float | int]]
    subgroup_metrics: Mapping[str, Mapping[str, float | int | None]]
    ood_diagnostic: OODReport
    betting_simulations: tuple[BettingThresholdResult, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serializable audit payload; fitted Python objects remain out-of-band."""

        return {
            "feature_names": list(self.feature_names),
            "feature_schema_hash": self.feature_schema_hash,
            "hyperparameters": dict(self.hyperparameters),
            "ensemble_weights": dict(self.ensemble_weights),
            "temporal_windows": dict(self.temporal_windows),
            "fold_audits": [_dataclass_with_datetimes(value) for value in self.fold_audits],
            "stack_fold_audits": [_dataclass_with_datetimes(value) for value in self.stack_fold_audits],
            "oof_predictions": [
                {
                    "game_id": row.game_id,
                    "event_at": row.event_at.isoformat(),
                    "trained_through": row.trained_through.isoformat(),
                    "outcome": row.outcome,
                    "predictions": dict(row.predictions),
                    "raw_stacked_probability": row.raw_stacked_probability,
                    "stack_fold_index": row.stack_fold_index,
                }
                for row in self.oof_predictions
            ],
            "oof_metrics": {name: asdict(value) for name, value in self.oof_metrics.items()},
            "paired_deltas_vs_stack": {name: asdict(value) for name, value in self.paired_deltas_vs_stack.items()},
            "holdout_predictions": _prediction_dict(self.holdout_predictions),
            "holdout_metrics": asdict(self.holdout_metrics),
            "calibration": {
                "method": self.calibration.method,
                "selection_scores": dict(self.calibration_selection_scores),
                "calibration_fit_count": self.calibration.calibration_fit_count,
                "selection_count": self.calibration.selection_count,
                "total_validation_count": self.calibration.total_validation_count,
                "selection_started_at": self.calibration.selection_started_at.isoformat(),
            },
            "ablation_metrics": {name: dict(value) for name, value in self.ablation_metrics.items()},
            "subgroup_metrics": {name: dict(value) for name, value in self.subgroup_metrics.items()},
            "ood_diagnostic": asdict(self.ood_diagnostic),
            "betting_simulations": [asdict(value) for value in self.betting_simulations],
        }


def run_temporal_model_development(
    rows: Sequence[object],
    config: ModelingConfig | None = None,
    *,
    schema: FrozenFeatureSchema = GATE3_FEATURE_SCHEMA,
) -> TemporalModelDevelopmentResult:
    """Develop, calibrate, stack, and finally evaluate one temporal model bundle.

    Input may be ``app.training.dataset.TrainingRow`` or ``LabeledGame``. For a
    TrainingRow, ``scheduled_first_pitch`` and ``features`` are consumed
    directly. All preprocessing, candidate models, calibration, stack weights,
    ablations, subgroup analysis, and OOD references are fitted from development
    rows/temporal OOF predictions. Locked holdout outcomes are accessed only by
    the final single-use metric call near the end of this function.
    """

    settings = config or ModelingConfig()
    labeled = tuple(_adapt_training_row(row) for row in rows)
    _validate_training_row_versions(rows)
    split = date_group_chronological_holdout(
        labeled,
        holdout_fraction=settings.holdout_fraction,
        min_development_dates=settings.min_development_dates,
    )
    if len(split.holdout.observations) < settings.minimum_holdout_rows:
        raise ValueError(
            f"Locked holdout contains {len(split.holdout.observations)} games; "
            f"at least {settings.minimum_holdout_rows} are required"
        )
    folds = rolling_origin_folds(
        split.development,
        min_train_dates=settings.min_train_dates,
        validation_dates=settings.validation_dates,
        step_dates=settings.step_dates,
    )
    if len(folds) < settings.minimum_rolling_folds:
        raise ValueError(
            f"Only {len(folds)} rolling-origin folds are available; "
            f"at least {settings.minimum_rolling_folds} are required"
        )
    _validate_fold_classes(folds)
    include_xgboost = settings.include_xgboost and len(split.development) >= settings.minimum_xgboost_rows
    include_poisson = all(row.home_runs is not None and row.away_runs is not None for row in split.development)
    include_team = all(row.home_team_id and row.away_team_id for row in split.development)
    factories = _classifier_factories(settings, include_xgboost=include_xgboost)
    oof = _generate_oof(
        split.development,
        folds,
        schema,
        factories,
        settings,
        include_poisson=include_poisson,
        include_team=include_team,
    )
    # Base estimators are already out-of-fold, but fitting stack weights on all
    # of their OOF outcomes and scoring those same rows would still be
    # resubstitution at the meta-model layer.  Build a second expanding-origin
    # series so every reported stack prediction is made by weights fitted only
    # on earlier base-OOF rows.
    cross_fitted_stack = _cross_fit_stacker(oof.rows, l2_toward_uniform=settings.stack_l2)
    validation_rows = cross_fitted_stack.rows
    base_oof = _base_prediction_columns(validation_rows)
    raw_stack = np.asarray([row.raw_stacked_probability for row in validation_rows], dtype=float)
    oof_outcomes = np.asarray([row.outcome for row in validation_rows], dtype=int)
    oof_event_at = [row.event_at for row in validation_rows]
    calibration = select_probability_calibrator(
        raw_stack,
        oof_outcomes,
        oof_event_at,
        selection_fraction=settings.calibration_selection_fraction,
        min_isotonic_samples=settings.minimum_isotonic_samples,
    )
    oof_metrics = {
        **{
            name: classification_metrics(values, oof_outcomes)
            for name, values in sorted(base_oof.items())
        },
        "stack_raw": classification_metrics(raw_stack, oof_outcomes),
    }
    date_groups = [row.event_at.date() for row in validation_rows]
    paired = {
        name: paired_block_bootstrap_delta(
            values,
            raw_stack,
            oof_outcomes,
            date_groups,
            resamples=settings.bootstrap_resamples,
            random_seed=settings.random_seed,
        )
        for name, values in sorted(base_oof.items())
    }
    ablations = _run_ablations(split.development, folds, schema, settings) if settings.run_ablations else {}
    subgroups = _subgroup_metrics(split.development, validation_rows, raw_stack)
    betting = _development_betting(split.development, validation_rows, raw_stack, settings.betting_thresholds)

    # Only after all validation evidence and method selection are fixed do we
    # fit the deployable stacker on the complete base-OOF series.  Holdout
    # labels remain inaccessible throughout both meta-model fitting stages.
    stacker = NonnegativeStacker(l2_toward_uniform=settings.stack_l2).fit(oof.rows)

    # Fit all production-candidate objects only on development rows.
    fitted = _fit_final_bundle(
        split.development,
        schema,
        factories,
        stacker,
        calibration,
        settings,
        include_poisson=include_poisson,
        include_team=include_team,
    )
    development_matrix = schema.matrix([row.features for row in split.development])
    holdout_matrix = schema.matrix([row.features for row in split.holdout.observations])
    ood = OODDetector().fit(development_matrix, schema.output_names).diagnose(holdout_matrix, schema.output_names)
    holdout_predictions = fitted.predict(split.holdout.observations)
    run_comparison_predictions = None
    if holdout_predictions.expected_home_runs is not None:
        run_strength = fitted.classifiers.get("run_strength")
        if not isinstance(run_strength, RunStrengthClassifier):
            raise AssertionError("The fitted bundle is missing its simple run-strength baseline")
        simple_home_runs, simple_away_runs = run_strength.expected_runs(
            holdout_matrix,
            schema.output_names,
        )
        run_comparison_predictions = {
            "simple_run_strength": (simple_home_runs, simple_away_runs),
        }

    # This is deliberately the first and only access to final holdout labels.
    holdout_metrics = split.holdout.final_evaluate(
        holdout_predictions.calibrated_probability,
        predicted_home_runs=holdout_predictions.expected_home_runs,
        predicted_away_runs=holdout_predictions.expected_away_runs,
        comparison_probabilities={
            **holdout_predictions.base_probabilities,
            "stack_raw": holdout_predictions.raw_stacked_probability,
        },
        comparison_run_predictions=run_comparison_predictions,
    )
    windows = _temporal_windows(
        split,
        folds,
        validation_rows,
        base_oof_games=len(oof.rows),
    )
    return TemporalModelDevelopmentResult(
        fitted_bundle=fitted,
        calibration=calibration,
        feature_names=schema.output_names,
        feature_schema_hash=schema.schema_hash,
        hyperparameters={
            **asdict(settings),
            "xgboost_eligible": include_xgboost,
            "poisson_eligible": include_poisson,
            "negative_binomial_eligible": include_poisson,
            "team_eb_eligible": include_team,
        },
        ensemble_weights=stacker.fit_.weight_map if stacker.fit_ else {},
        temporal_windows=windows,
        fold_audits=oof.audits,
        stack_fold_audits=cross_fitted_stack.audits,
        oof_predictions=validation_rows,
        oof_metrics=oof_metrics,
        paired_deltas_vs_stack=paired,
        holdout_predictions=holdout_predictions,
        holdout_metrics=holdout_metrics,
        calibration_selection_scores=calibration.selection_scores,
        ablation_metrics=ablations,
        subgroup_metrics=subgroups,
        ood_diagnostic=ood,
        betting_simulations=betting,
    )


def _generate_oof(
    development: Sequence[LabeledGame],
    folds: Sequence[RollingOriginFold],
    schema: FrozenFeatureSchema,
    factories: Mapping[str, Callable[[], object]],
    config: ModelingConfig,
    *,
    include_poisson: bool,
    include_team: bool,
) -> OOFGenerationResult:
    del development  # Fold membership is the only permitted source below.
    predictions: list[OOFPredictionRow] = []
    audits: list[FoldFitAudit] = []
    for fold in folds:
        train_matrix = schema.matrix([row.features for row in fold.train])
        validation_matrix = schema.matrix([row.features for row in fold.validation])
        train_outcomes = [row.home_win for row in fold.train]
        train_ids = tuple(row.game_id for row in fold.train)
        validation_ids = tuple(row.game_id for row in fold.validation)
        fold_predictions: dict[str, np.ndarray] = {}
        for name, factory in factories.items():
            model = factory()
            model.fit(train_matrix, train_outcomes, schema.output_names, row_ids=train_ids)  # type: ignore[attr-defined]
            fold_predictions[name] = np.asarray(model.predict_proba(validation_matrix, schema.output_names), dtype=float)  # type: ignore[attr-defined]
            audits.append(FoldFitAudit(
                fold.index,
                name,
                fold.trained_through,
                train_ids,
                validation_ids,
                tuple(model.fit_row_ids),  # type: ignore[attr-defined]
            ))
        if include_poisson:
            poisson = PoissonRunRegressor(alpha=config.poisson_alpha).fit(
                train_matrix,
                [float(row.home_runs) for row in fold.train if row.home_runs is not None],
                [float(row.away_runs) for row in fold.train if row.away_runs is not None],
                schema.output_names,
                row_ids=train_ids,
            )
            fold_predictions["poisson"] = poisson.predict_proba(validation_matrix, schema.output_names)
            audits.append(FoldFitAudit(fold.index, "poisson", fold.trained_through, train_ids, validation_ids, poisson.fit_row_ids))
            negative_binomial = NegativeBinomialRunRegressor(mean_alpha=config.poisson_alpha).fit(
                train_matrix,
                [float(row.home_runs) for row in fold.train if row.home_runs is not None],
                [float(row.away_runs) for row in fold.train if row.away_runs is not None],
                schema.output_names,
                row_ids=train_ids,
            )
            fold_predictions["negative_binomial"] = negative_binomial.predict_proba(
                validation_matrix,
                schema.output_names,
            )
            audits.append(FoldFitAudit(
                fold.index,
                "negative_binomial",
                fold.trained_through,
                train_ids,
                validation_ids,
                negative_binomial.fit_row_ids,
            ))
        if include_team:
            team = EmpiricalBayesTeamClassifier(prior_strength=config.team_prior_strength).fit(
                [str(row.home_team_id) for row in fold.train],
                [str(row.away_team_id) for row in fold.train],
                train_outcomes,
            )
            fold_predictions["team_eb"] = team.predict_proba(
                [str(row.home_team_id) for row in fold.validation],
                [str(row.away_team_id) for row in fold.validation],
            )
            audits.append(FoldFitAudit(fold.index, "team_eb", fold.trained_through, train_ids, validation_ids, train_ids))
        for row_index, row in enumerate(fold.validation):
            predictions.append(OOFPredictionRow(
                game_id=row.game_id,
                event_at=row.event_at,
                trained_through=fold.trained_through,
                outcome=row.home_win,
                predictions={name: float(values[row_index]) for name, values in sorted(fold_predictions.items())},
            ))
    return OOFGenerationResult(tuple(predictions), tuple(audits))


def _cross_fit_stacker(
    rows: Sequence[OOFPredictionRow],
    *,
    l2_toward_uniform: float,
) -> CrossFittedStackResult:
    """Create expanding-origin predictions for the meta-model itself.

    Rows from the first base-OOF fold seed the meta-training history.  Each
    later base-OOF fold is predicted from a stacker fitted only on complete,
    chronologically earlier base-OOF folds.  Seed folds that do not yet contain
    both outcomes remain training evidence but are never scored in-sample.
    """

    if not rows:
        raise ValueError("Stack cross-fitting requires base OOF predictions")
    grouped: dict[datetime, list[OOFPredictionRow]] = {}
    for row in sorted(rows, key=lambda value: (value.trained_through, value.event_at, value.game_id)):
        grouped.setdefault(row.trained_through, []).append(row)

    prior: list[OOFPredictionRow] = []
    predictions: list[StackOOFPredictionRow] = []
    audits: list[StackFoldAudit] = []
    for group in grouped.values():
        ordered_group = sorted(group, key=lambda value: (value.event_at, value.game_id))
        validation_start = ordered_group[0].event_at
        if prior and max(row.event_at for row in prior) >= validation_start:
            raise ValueError("Stack cross-fitting requires non-overlapping chronological base OOF folds")
        if len(prior) >= 4 and len({row.outcome for row in prior}) == 2:
            stacker = NonnegativeStacker(l2_toward_uniform=l2_toward_uniform).fit(prior)
            probability = stacker.predict(_base_prediction_columns(ordered_group))
            trained_through = max(row.event_at for row in prior)
            fold_index = len(audits)
            train_game_ids = tuple(row.game_id for row in prior)
            validation_game_ids = tuple(row.game_id for row in ordered_group)
            if set(train_game_ids) & set(validation_game_ids):
                raise AssertionError("Stack meta-training and validation games overlap")
            audits.append(StackFoldAudit(
                fold_index=fold_index,
                trained_through=trained_through,
                validation_started_at=validation_start,
                validation_ended_at=ordered_group[-1].event_at,
                train_game_ids=train_game_ids,
                validation_game_ids=validation_game_ids,
                weights=dict(stacker.fit_.weight_map) if stacker.fit_ else {},
            ))
            predictions.extend(
                StackOOFPredictionRow(
                    game_id=row.game_id,
                    event_at=row.event_at,
                    trained_through=trained_through,
                    outcome=row.outcome,
                    predictions=dict(row.predictions),
                    raw_stacked_probability=float(probability[index]),
                    stack_fold_index=fold_index,
                )
                for index, row in enumerate(ordered_group)
            )
        prior.extend(ordered_group)

    if not predictions:
        raise ValueError("No temporal stack validation fold can be formed from base OOF predictions")
    return CrossFittedStackResult(tuple(predictions), tuple(audits))


def _fit_final_bundle(
    development: Sequence[LabeledGame],
    schema: FrozenFeatureSchema,
    factories: Mapping[str, Callable[[], object]],
    stacker: NonnegativeStacker,
    calibration: CalibrationSelection,
    config: ModelingConfig,
    *,
    include_poisson: bool,
    include_team: bool,
) -> FittedModelBundle:
    matrix = schema.matrix([row.features for row in development])
    outcomes = [row.home_win for row in development]
    row_ids = [row.game_id for row in development]
    classifiers: dict[str, object] = {}
    for name, factory in factories.items():
        classifiers[name] = factory().fit(matrix, outcomes, schema.output_names, row_ids=row_ids)  # type: ignore[attr-defined]
    poisson = None
    negative_binomial = None
    if include_poisson:
        poisson = PoissonRunRegressor(alpha=config.poisson_alpha).fit(
            matrix,
            [float(row.home_runs) for row in development if row.home_runs is not None],
            [float(row.away_runs) for row in development if row.away_runs is not None],
            schema.output_names,
            row_ids=row_ids,
        )
        negative_binomial = NegativeBinomialRunRegressor(mean_alpha=config.poisson_alpha).fit(
            matrix,
            [float(row.home_runs) for row in development if row.home_runs is not None],
            [float(row.away_runs) for row in development if row.away_runs is not None],
            schema.output_names,
            row_ids=row_ids,
        )
    team = None
    if include_team:
        team = EmpiricalBayesTeamClassifier(prior_strength=config.team_prior_strength).fit(
            [str(row.home_team_id) for row in development],
            [str(row.away_team_id) for row in development],
            outcomes,
        )
    return FittedModelBundle(
        schema=schema,
        classifiers=classifiers,
        stacker=stacker,
        calibration=calibration,
        team_model=team,
        poisson_model=poisson,
        negative_binomial_model=negative_binomial,
    )


def _classifier_factories(config: ModelingConfig, *, include_xgboost: bool) -> dict[str, Callable[[], object]]:
    factories: dict[str, Callable[[], object]] = {
        "home_prior": lambda: HomePriorClassifier(
            prior_probability=config.home_prior_probability,
            prior_strength=config.home_prior_strength,
        ),
        "rating": RatingClassifier,
        "run_strength": RunStrengthClassifier,
        "logistic": lambda: RegularizedLogisticClassifier(
            regularization_c=config.logistic_c,
            random_seed=config.random_seed,
        ),
    }
    if include_xgboost:
        factories["xgboost"] = lambda: LimitedXGBoostClassifier(
            n_estimators=config.xgboost_estimators,
            max_depth=config.xgboost_max_depth,
            learning_rate=config.xgboost_learning_rate,
            random_seed=config.random_seed,
        )
    return factories


def _run_ablations(
    development: Sequence[LabeledGame],
    folds: Sequence[RollingOriginFold],
    schema: FrozenFeatureSchema,
    config: ModelingConfig,
) -> dict[str, dict[str, float | int]]:
    specs = [AblationSpec("full")]
    specs.extend(AblationSpec(f"without_{family}", excluded_families=(family,)) for family in config.ablation_families)
    specs.extend(AblationSpec(f"without_{name}", excluded_features=(name,)) for name in config.ablation_features)
    results: dict[str, dict[str, float | int]] = {}
    for spec in specs:
        ablated_schema = apply_ablation(schema, spec)
        oof = _generate_oof(
            development,
            folds,
            ablated_schema,
            {"logistic": lambda: RegularizedLogisticClassifier(regularization_c=config.logistic_c, random_seed=config.random_seed)},
            config,
            include_poisson=False,
            include_team=False,
        )
        values = [row.predictions["logistic"] for row in oof.rows]
        outcomes = [row.outcome for row in oof.rows]
        results[spec.name] = {
            "observations": len(values),
            "log_loss": probability_log_loss(values, outcomes),
            "feature_count": len(ablated_schema.output_names),
        }
    return results


def _subgroup_metrics(
    development: Sequence[LabeledGame],
    rows: Sequence[OOFPredictionRow | StackOOFPredictionRow],
    stacked: Sequence[float],
) -> dict[str, dict[str, float | int | None]]:
    lifecycle_by_game = {row.game_id: row.lifecycle for row in development}
    grouped: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        grouped.setdefault(lifecycle_by_game[row.game_id], []).append(index)
    result: dict[str, dict[str, float | int | None]] = {}
    for lifecycle, indices in sorted(grouped.items()):
        probabilities = [float(stacked[index]) for index in indices]
        outcomes = [rows[index].outcome for index in indices]
        metrics = None
        if len(indices) >= 4 and len(set(outcomes)) == 2:
            metrics = classification_metrics(probabilities, outcomes, bins=min(10, len(indices)))
        result[lifecycle] = {
            "observations": len(indices),
            "log_loss": metrics.log_loss if metrics else None,
            "brier_score": metrics.brier_score if metrics else None,
            "ece": metrics.expected_calibration_error if metrics else None,
        }
    return result


def _development_betting(
    development: Sequence[LabeledGame],
    rows: Sequence[OOFPredictionRow | StackOOFPredictionRow],
    stacked: Sequence[float],
    thresholds: Sequence[float],
) -> tuple[BettingThresholdResult, ...]:
    by_game = {row.game_id: row for row in development}
    selected = [
        (index, by_game[row.game_id].market_home_probability)
        for index, row in enumerate(rows)
        if by_game[row.game_id].market_home_probability is not None
    ]
    if not selected:
        return ()
    # A no-vig market probability is converted to an implied decimal price only
    # for a secondary sensitivity simulation; it is not represented as an
    # executable historical sportsbook price.
    return simulate_betting_thresholds(
        [float(stacked[index]) for index, _ in selected],
        [1.0 / float(market) for _, market in selected if market is not None],
        [rows[index].outcome for index, _ in selected],
        thresholds=thresholds,
        price_basis="reciprocal of no-vig market probability; not an executable sportsbook price",
    )


def _base_prediction_columns(
    rows: Sequence[OOFPredictionRow | StackOOFPredictionRow],
) -> dict[str, np.ndarray]:
    names = tuple(sorted(rows[0].predictions))
    return {
        name: np.asarray([row.predictions[name] for row in rows], dtype=float)
        for name in names
    }


def _validate_fold_classes(folds: Sequence[RollingOriginFold]) -> None:
    for fold in folds:
        if len({row.home_win for row in fold.train}) < 2:
            raise ValueError(f"Rolling fold {fold.index} training window contains one outcome class")


def _temporal_windows(
    split: DevelopmentHoldoutSplit,
    folds: Sequence[RollingOriginFold],
    oof_rows: Sequence[StackOOFPredictionRow],
    *,
    base_oof_games: int,
) -> dict[str, Any]:
    if not oof_rows:
        raise ValueError("Temporal windows require cross-fitted stack validation rows")
    return {
        "development_start": min(row.event_at for row in split.development).isoformat(),
        "development_end": max(row.event_at for row in split.development).isoformat(),
        "oof_validation_start": min(row.event_at for row in oof_rows).isoformat(),
        "oof_validation_end": max(row.event_at for row in oof_rows).isoformat(),
        "first_holdout_date": split.first_holdout_date.isoformat(),
        "holdout_end": max(row.event_at for row in split.holdout.observations).isoformat(),
        "development_games": len(split.development),
        "holdout_games": len(split.holdout.observations),
        "rolling_folds": len(folds),
        "oof_games": len(oof_rows),
        "base_oof_games": base_oof_games,
        "stack_rolling_folds": len({row.stack_fold_index for row in oof_rows}),
        "split_policy": "date_grouped_expanding_origin_then_locked_latest_holdout",
        "stack_validation_policy": "expanding_origin_meta_fit_on_strictly_earlier_base_oof_folds",
    }


def _adapt_training_row(row: object) -> LabeledGame:
    if isinstance(row, LabeledGame):
        return row
    game_id = str(_required(row, "game_id"))
    event_at = _datetime(_required(row, "scheduled_first_pitch"), "scheduled_first_pitch")
    data_as_of_value = getattr(row, "data_as_of", None)
    data_as_of = _datetime(data_as_of_value, "data_as_of") if data_as_of_value is not None else None
    if data_as_of is not None and data_as_of >= event_at:
        raise ValueError(f"Training row {game_id} is not strictly pregame")
    feature_values = dict(_required(row, "features"))
    completeness = getattr(row, "feature_completeness", None)
    if feature_values.get("feature_completeness") is None and completeness is not None:
        feature_values["feature_completeness"] = completeness
    if feature_values.get("minutes_to_first_pitch") is None and data_as_of is not None:
        feature_values["minutes_to_first_pitch"] = (event_at - data_as_of).total_seconds() / 60.0
    lifecycle = _enum_value(getattr(row, "lifecycle", "FINAL"))
    source_mode = _enum_value(getattr(row, "source_mode", "LIVE")).upper()
    market = getattr(row, "market_home_probability", None)
    closing_market = getattr(row, "closing_home_probability", None)
    return LabeledGame(
        game_id=game_id,
        event_at=event_at,
        features=feature_values,
        home_win=int(_required(row, "home_win")),
        lifecycle=lifecycle,
        source_mode=source_mode,
        home_runs=_optional_float(getattr(row, "home_runs", None)),
        away_runs=_optional_float(getattr(row, "away_runs", None)),
        home_team_id=_optional_string(getattr(row, "home_team_id", None)),
        away_team_id=_optional_string(getattr(row, "away_team_id", None)),
        market_home_probability=_optional_float(market),
        closing_home_probability=_optional_float(closing_market),
    )


def _validate_training_row_versions(rows: Sequence[object]) -> None:
    for attribute in ("feature_version", "feature_schema_version"):
        values = {
            str(getattr(row, attribute))
            for row in rows
            if not isinstance(row, LabeledGame) and getattr(row, attribute, None) is not None
        }
        if len(values) > 1:
            raise ValueError(f"Training rows mix {attribute} values: {sorted(values)}")


def _required(row: object, attribute: str) -> Any:
    value = getattr(row, attribute, None)
    if value is None:
        raise ValueError(f"Training row is missing {attribute}")
    return value


def _datetime(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{label} must be an ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _prediction_dict(value: PredictionBundle) -> dict[str, Any]:
    return {
        "game_ids": list(value.game_ids),
        "base_probabilities": {name: list(values) for name, values in value.base_probabilities.items()},
        "raw_stacked_probability": list(value.raw_stacked_probability),
        "calibrated_probability": list(value.calibrated_probability),
        "expected_home_runs": list(value.expected_home_runs) if value.expected_home_runs is not None else None,
        "expected_away_runs": list(value.expected_away_runs) if value.expected_away_runs is not None else None,
    }


def _dataclass_with_datetimes(value: object) -> dict[str, Any]:
    result = asdict(value)
    for key, item in tuple(result.items()):
        if isinstance(item, datetime):
            result[key] = item.isoformat()
    return result
