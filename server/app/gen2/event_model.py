"""Provider-free, point-in-time Gen-2 plate-appearance event models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from math import isfinite, log
from typing import Iterable, Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression

from app.gen2.event_taxonomy import PlateAppearanceOutcome, ordered_outcomes


HIERARCHICAL_EVENT_MODEL_VERSION = "mlb_gen2_pa_hierarchical_multinomial_v1"
XGBOOST_EVENT_MODEL_VERSION = "mlb_gen2_pa_xgboost_multiclass_v1"
EVENT_FEATURE_SCHEMA_VERSION = "mlb_gen2_pa_event_features_v1"
TEMPERATURE_CALIBRATION_VERSION = "mlb_gen2_multiclass_temperature_v1"


class EventModelError(ValueError):
    """Raised when event-model evidence or predictions violate the contract."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _checksum(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EventModelError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


_REFERENCE_OUTCOME = PlateAppearanceOutcome.BIP_OUT
_LOGIT_OUTCOMES = tuple(outcome for outcome in ordered_outcomes() if outcome != _REFERENCE_OUTCOME)
EVENT_FEATURE_NAMES = tuple(
    [f"batter_posterior_logit_{outcome.value.lower()}" for outcome in _LOGIT_OUTCOMES]
    + [f"pitcher_allowed_posterior_logit_{outcome.value.lower()}" for outcome in _LOGIT_OUTCOMES]
    + [f"league_environment_logit_{outcome.value.lower()}" for outcome in _LOGIT_OUTCOMES]
    + [
        "batter_effective_sample",
        "pitcher_effective_sample",
        "batter_posterior_variance",
        "pitcher_posterior_variance",
        "same_hand_indicator",
        "batter_left_indicator",
        "pitcher_left_indicator",
        "prior_period_park_run_factor",
    ]
)
EVENT_FEATURE_SCHEMA_HASH = _checksum(
    {
        "version": EVENT_FEATURE_SCHEMA_VERSION,
        "ordered_features": EVENT_FEATURE_NAMES,
        "outcomes": [outcome.value for outcome in ordered_outcomes()],
        "reference_outcome": _REFERENCE_OUTCOME.value,
        "missingness": "TRAIN_FOLD_MEDIAN_PLUS_EXPLICIT_RELIABILITY",
        "temporal_rule": "MAX_EVIDENCE_END_LT_PA_AND_ELIGIBLE_LE_PA",
    }
)


@dataclass(frozen=True)
class PlateAppearanceTrainingRow:
    row_id: str
    game_id: str
    plate_appearance_id: str
    event_date: date
    event_at: datetime
    evidence_end_at: datetime
    eligible_at: datetime
    outcome: PlateAppearanceOutcome
    features: Mapping[str, float | int | None]
    source_game_ids: tuple[str, ...]
    evidence_checksum: str
    evidence_tier: str = "A"

    def __post_init__(self) -> None:
        if not self.row_id or not self.game_id or not self.plate_appearance_id:
            raise EventModelError("row/game/plate-appearance identities are required")
        event_at = _utc(self.event_at, "event_at")
        evidence_end = _utc(self.evidence_end_at, "evidence_end_at")
        eligible = _utc(self.eligible_at, "eligible_at")
        if evidence_end >= event_at:
            raise EventModelError("feature evidence must end before the current PA")
        if eligible < evidence_end:
            raise EventModelError("feature evidence cannot be eligible before it ends")
        if eligible > event_at:
            raise EventModelError("feature evidence is not eligible at the current PA")
        if self.game_id in self.source_game_ids:
            raise EventModelError("current-game evidence is forbidden in event predictors")
        if (
            not self.source_game_ids
            or any(not identity for identity in self.source_game_ids)
            or len(set(self.source_game_ids)) != len(self.source_game_ids)
        ):
            raise EventModelError("event source game identities must be nonempty and unique")
        if len(self.evidence_checksum) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.evidence_checksum.lower()
        ):
            raise EventModelError("evidence_checksum must be SHA-256")
        if self.evidence_tier != "A":
            raise EventModelError("strict event training accepts Tier A evidence only")
        if set(self.features) != set(EVENT_FEATURE_NAMES) or len(self.features) != len(EVENT_FEATURE_NAMES):
            raise EventModelError("event feature mapping does not match the frozen schema")
        for name, value in self.features.items():
            if value is not None and (not isinstance(value, (int, float)) or not isfinite(float(value))):
                raise EventModelError(f"feature {name} must be finite numeric or null")


@dataclass(frozen=True)
class EventModelEvidence:
    model_version: str
    feature_schema_version: str
    feature_schema_hash: str
    ordered_outcomes: tuple[str, ...]
    ordered_features: tuple[str, ...]
    training_rows: int
    training_dates: int
    fit_cutoff: datetime
    fold_identity_checksum: str
    training_population_checksum: str
    training_evidence_checksum: str
    preprocessing_checksum: str
    parameter_checksum: str
    coefficient_rows: tuple[tuple[str, tuple[float, ...], float], ...]


@dataclass(frozen=True)
class EventProbabilityVector:
    probabilities: Mapping[PlateAppearanceOutcome, float]
    model_version: str
    feature_schema_hash: str

    def __post_init__(self) -> None:
        if set(self.probabilities) != set(ordered_outcomes()):
            raise EventModelError("prediction does not contain the frozen outcome universe")
        values = tuple(float(self.probabilities[outcome]) for outcome in ordered_outcomes())
        if any(not isfinite(value) or value < 0 for value in values):
            raise EventModelError("event prediction is not finite and nonnegative")
        if abs(sum(values) - 1.0) > 1e-12:
            raise EventModelError("event probabilities must sum to one within 1e-12")


def oof_model_training_checksum(
    *,
    fold_identity_checksum: str,
    training_cutoff: datetime,
    training_sample_ids: Sequence[str],
) -> str:
    _require_sha256(fold_identity_checksum, "fold_identity_checksum")
    boundary = _utc(training_cutoff, "training_cutoff")
    identifiers = tuple(training_sample_ids)
    if (
        not identifiers
        or any(not identity for identity in identifiers)
        or len(set(identifiers)) != len(identifiers)
    ):
        raise EventModelError("OOF training sample identities must be nonempty and unique")
    return _checksum(
        {
            "fold_identity_checksum": fold_identity_checksum,
            "training_cutoff": boundary.isoformat(),
            "training_sample_ids": identifiers,
        }
    )


@dataclass(frozen=True)
class OOFMulticlassPrediction:
    sample_id: str
    target: PlateAppearanceOutcome
    probabilities: Mapping[PlateAppearanceOutcome, float]
    event_at: datetime
    fold_identity_checksum: str
    training_cutoff: datetime
    training_sample_ids: tuple[str, ...]
    model_training_checksum: str

    def __post_init__(self) -> None:
        if not self.sample_id:
            raise EventModelError("OOF sample identity is required")
        event_at = _utc(self.event_at, "OOF event_at")
        training_cutoff = _utc(self.training_cutoff, "OOF training_cutoff")
        if training_cutoff >= event_at:
            raise EventModelError("OOF fold training cutoff must predate the sample")
        if self.sample_id in self.training_sample_ids:
            raise EventModelError("OOF sample appears in its model training population")
        EventProbabilityVector(
            self.probabilities,
            "oof",
            EVENT_FEATURE_SCHEMA_HASH,
        )
        expected = oof_model_training_checksum(
            fold_identity_checksum=self.fold_identity_checksum,
            training_cutoff=training_cutoff,
            training_sample_ids=self.training_sample_ids,
        )
        if self.model_training_checksum != expected:
            raise EventModelError("OOF model-training checksum does not match fold evidence")


@dataclass(frozen=True)
class FittedHierarchicalEventModel:
    estimator: LogisticRegression
    medians: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    evidence: EventModelEvidence

    def predict(self, features: Mapping[str, float | int | None]) -> EventProbabilityVector:
        matrix = _matrix((features,), self.medians)
        transformed = (matrix - np.asarray(self.means)) / np.asarray(self.scales)
        raw = self.estimator.predict_proba(transformed)[0]
        values = np.zeros(len(ordered_outcomes()), dtype=float)
        for class_id, probability in zip(self.estimator.classes_, raw, strict=True):
            values[int(class_id)] = float(probability)
        probabilities = {
            outcome: float(values[index])
            for index, outcome in enumerate(ordered_outcomes())
        }
        return EventProbabilityVector(
            probabilities=probabilities,
            model_version=self.evidence.model_version,
            feature_schema_hash=self.evidence.feature_schema_hash,
        )


@dataclass(frozen=True)
class FittedXGBoostEventModel:
    estimator: object
    evidence: EventModelEvidence

    def predict(self, features: Mapping[str, float | int | None]) -> EventProbabilityVector:
        matrix = _native_missing_matrix((features,))
        raw = np.asarray(self.estimator.predict_proba(matrix), dtype=float)[0]
        if raw.shape != (len(ordered_outcomes()),):
            raise EventModelError("XGBoost returned an unexpected class shape")
        raw /= raw.sum()
        return EventProbabilityVector(
            probabilities={
                outcome: float(raw[index]) for index, outcome in enumerate(ordered_outcomes())
            },
            model_version=self.evidence.model_version,
            feature_schema_hash=self.evidence.feature_schema_hash,
        )


def _validate_feature_mapping(features: Mapping[str, float | int | None]) -> None:
    if set(features) != set(EVENT_FEATURE_NAMES) or len(features) != len(EVENT_FEATURE_NAMES):
        raise EventModelError("event feature mapping does not match the frozen schema")


def _native_missing_matrix(
    rows: Sequence[Mapping[str, float | int | None]],
) -> np.ndarray:
    for row in rows:
        _validate_feature_mapping(row)
    return np.asarray(
        [
            [np.nan if row[name] is None else float(row[name]) for name in EVENT_FEATURE_NAMES]
            for row in rows
        ],
        dtype=float,
    )


def _matrix(
    rows: Sequence[Mapping[str, float | int | None]],
    medians: Sequence[float] | None = None,
) -> np.ndarray:
    matrix = _native_missing_matrix(rows)
    if medians is None:
        learned = np.nanmedian(matrix, axis=0)
        learned = np.where(np.isnan(learned), 0.0, learned)
    else:
        learned = np.asarray(medians, dtype=float)
        if learned.shape != (len(EVENT_FEATURE_NAMES),):
            raise EventModelError("imputation vector does not match the frozen schema")
    locations = np.where(np.isnan(matrix))
    matrix[locations] = learned[locations[1]]
    return matrix


def _validate_training_rows(
    rows: Iterable[PlateAppearanceTrainingRow],
    *,
    fit_cutoff: datetime,
    excluded_game_ids: tuple[str, ...],
    fold_identity_checksum: str,
) -> tuple[PlateAppearanceTrainingRow, ...]:
    boundary = _utc(fit_cutoff, "fit_cutoff")
    _require_sha256(fold_identity_checksum, "fold_identity_checksum")
    if any(not identity for identity in excluded_game_ids) or len(
        set(excluded_game_ids)
    ) != len(excluded_game_ids):
        raise EventModelError("excluded game identities must be unique")
    excluded = set(excluded_game_ids)
    materialized = tuple(
        row
        for row in rows
        if row.game_id not in excluded and _utc(row.event_at, "event_at") < boundary
    )
    if not materialized:
        raise EventModelError("event training requires rows")
    identities: dict[tuple[str, str], PlateAppearanceTrainingRow] = {}
    for row in materialized:
        key = (row.game_id, row.plate_appearance_id)
        prior = identities.get(key)
        if prior is not None and prior != row:
            raise EventModelError("conflicting duplicate plate-appearance training row")
        identities[key] = row
    unique = tuple(sorted(identities.values(), key=lambda row: (row.event_at, row.row_id)))
    present = {row.outcome for row in unique}
    missing = set(ordered_outcomes()) - present
    if missing:
        raise EventModelError(
            "every frozen PA outcome must appear in training; missing "
            + ",".join(sorted(outcome.value for outcome in missing))
        )
    return unique


def _training_checksum(rows: Sequence[PlateAppearanceTrainingRow]) -> str:
    return _checksum(
        [
            {
                "row_id": row.row_id,
                "game_id": row.game_id,
                "plate_appearance_id": row.plate_appearance_id,
                "event_date": row.event_date.isoformat(),
                "event_at": row.event_at.isoformat(),
                "evidence_end_at": row.evidence_end_at.isoformat(),
                "eligible_at": row.eligible_at.isoformat(),
                "outcome": row.outcome.value,
                "features": {name: row.features[name] for name in EVENT_FEATURE_NAMES},
                "source_game_ids": row.source_game_ids,
                "evidence_checksum": row.evidence_checksum,
                "evidence_tier": row.evidence_tier,
            }
            for row in rows
        ]
    )


def fit_hierarchical_event_model(
    rows: Iterable[PlateAppearanceTrainingRow],
    *,
    fit_cutoff: datetime,
    excluded_game_ids: tuple[str, ...],
    fold_identity_checksum: str,
    regularization_c: float = 0.25,
) -> FittedHierarchicalEventModel:
    """Fit the permanent regularized multinomial baseline on training data only."""

    if not isfinite(regularization_c) or regularization_c <= 0:
        raise EventModelError("regularization_c must be finite and positive")
    boundary = _utc(fit_cutoff, "fit_cutoff")
    training = _validate_training_rows(
        rows,
        fit_cutoff=boundary,
        excluded_game_ids=excluded_game_ids,
        fold_identity_checksum=fold_identity_checksum,
    )
    native = _native_missing_matrix(tuple(row.features for row in training))
    medians_array = np.nanmedian(native, axis=0)
    medians_array = np.where(np.isnan(medians_array), 0.0, medians_array)
    matrix = _matrix(tuple(row.features for row in training), medians_array)
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales = np.where(scales <= 1e-12, 1.0, scales)
    transformed = (matrix - means) / scales
    targets = np.asarray([ordered_outcomes().index(row.outcome) for row in training])
    estimator = LogisticRegression(
        C=regularization_c,
        solver="lbfgs",
        max_iter=4_000,
        random_state=0,
    )
    estimator.fit(transformed, targets)
    coefficient_rows = tuple(
        (
            ordered_outcomes()[int(class_id)].value,
            tuple(float(value) for value in estimator.coef_[index]),
            float(estimator.intercept_[index]),
        )
        for index, class_id in enumerate(estimator.classes_)
    )
    preprocessing = {
        "medians": medians_array.tolist(),
        "means": means.tolist(),
        "scales": scales.tolist(),
        "fit_scope": "TRAINING_ROWS_ONLY",
    }
    evidence = EventModelEvidence(
        model_version=HIERARCHICAL_EVENT_MODEL_VERSION,
        feature_schema_version=EVENT_FEATURE_SCHEMA_VERSION,
        feature_schema_hash=EVENT_FEATURE_SCHEMA_HASH,
        ordered_outcomes=tuple(outcome.value for outcome in ordered_outcomes()),
        ordered_features=EVENT_FEATURE_NAMES,
        training_rows=len(training),
        training_dates=len({row.event_date for row in training}),
        fit_cutoff=boundary,
        fold_identity_checksum=fold_identity_checksum,
        training_population_checksum=_checksum(
            {
                "fit_cutoff": boundary.isoformat(),
                "fold_identity_checksum": fold_identity_checksum,
                "excluded_game_ids": excluded_game_ids,
                "row_ids": [row.row_id for row in training],
            }
        ),
        training_evidence_checksum=_training_checksum(training),
        preprocessing_checksum=_checksum(preprocessing),
        parameter_checksum=_checksum(coefficient_rows),
        coefficient_rows=coefficient_rows,
    )
    return FittedHierarchicalEventModel(
        estimator=estimator,
        medians=tuple(float(value) for value in medians_array),
        means=tuple(float(value) for value in means),
        scales=tuple(float(value) for value in scales),
        evidence=evidence,
    )


def fit_xgboost_event_model(
    rows: Iterable[PlateAppearanceTrainingRow],
    *,
    fit_cutoff: datetime,
    excluded_game_ids: tuple[str, ...],
    fold_identity_checksum: str,
    estimators: int = 100,
    max_depth: int = 3,
) -> FittedXGBoostEventModel:
    """Fit the sole restrained boosted challenger with native missing values."""

    if not 1 <= estimators <= 400 or not 1 <= max_depth <= 4:
        raise EventModelError("restrained XGBoost bounds were violated")
    from xgboost import XGBClassifier

    boundary = _utc(fit_cutoff, "fit_cutoff")
    training = _validate_training_rows(
        rows,
        fit_cutoff=boundary,
        excluded_game_ids=excluded_game_ids,
        fold_identity_checksum=fold_identity_checksum,
    )
    matrix = _native_missing_matrix(tuple(row.features for row in training))
    targets = np.asarray([ordered_outcomes().index(row.outcome) for row in training])
    estimator = XGBClassifier(
        objective="multi:softprob",
        num_class=len(ordered_outcomes()),
        n_estimators=estimators,
        max_depth=max_depth,
        learning_rate=0.04,
        min_child_weight=8.0,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=4.0,
        reg_alpha=0.1,
        random_state=0,
        n_jobs=1,
        tree_method="hist",
        eval_metric="mlogloss",
    )
    estimator.fit(matrix, targets)
    booster = estimator.get_booster()
    raw_model = bytes(booster.save_raw(raw_format="json"))
    parameters = {
        "objective": "multi:softprob",
        "num_class": len(ordered_outcomes()),
        "n_estimators": estimators,
        "max_depth": max_depth,
        "learning_rate": 0.04,
        "min_child_weight": 8.0,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_lambda": 4.0,
        "reg_alpha": 0.1,
        "random_state": 0,
    }
    evidence = EventModelEvidence(
        model_version=XGBOOST_EVENT_MODEL_VERSION,
        feature_schema_version=EVENT_FEATURE_SCHEMA_VERSION,
        feature_schema_hash=EVENT_FEATURE_SCHEMA_HASH,
        ordered_outcomes=tuple(outcome.value for outcome in ordered_outcomes()),
        ordered_features=EVENT_FEATURE_NAMES,
        training_rows=len(training),
        training_dates=len({row.event_date for row in training}),
        fit_cutoff=boundary,
        fold_identity_checksum=fold_identity_checksum,
        training_population_checksum=_checksum(
            {
                "fit_cutoff": boundary.isoformat(),
                "fold_identity_checksum": fold_identity_checksum,
                "excluded_game_ids": excluded_game_ids,
                "row_ids": [row.row_id for row in training],
            }
        ),
        training_evidence_checksum=_training_checksum(training),
        preprocessing_checksum=_checksum({"native_missing": True, "fit_scope": "TRAINING_ROWS_ONLY"}),
        parameter_checksum=_checksum(
            {"parameters": parameters, "booster_sha256": sha256(raw_model).hexdigest()}
        ),
        coefficient_rows=(),
    )
    return FittedXGBoostEventModel(estimator=estimator, evidence=evidence)


@dataclass(frozen=True)
class MulticlassMetrics:
    rows: int
    log_loss: float
    multiclass_brier: float
    classwise_brier: Mapping[str, float]
    top_label_ece: float


def multiclass_metrics(
    targets: Sequence[PlateAppearanceOutcome],
    predictions: Sequence[Mapping[PlateAppearanceOutcome, float]],
    *,
    bins: int = 10,
) -> MulticlassMetrics:
    if len(targets) != len(predictions) or not targets:
        raise EventModelError("targets and predictions must be nonempty and aligned")
    if bins <= 0:
        raise EventModelError("bins must be positive")
    matrix = np.asarray(
        [
            [EventProbabilityVector(row, "metrics", EVENT_FEATURE_SCHEMA_HASH).probabilities[o] for o in ordered_outcomes()]
            for row in predictions
        ],
        dtype=float,
    )
    labels = np.asarray([ordered_outcomes().index(target) for target in targets], dtype=int)
    truth = np.eye(len(ordered_outcomes()))[labels]
    selected = np.clip(matrix[np.arange(len(labels)), labels], 1e-15, 1.0)
    classwise = np.mean((matrix - truth) ** 2, axis=0)
    confidence = matrix.max(axis=1)
    correct = (matrix.argmax(axis=1) == labels).astype(float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for index in range(bins):
        if index == bins - 1:
            mask = (confidence >= edges[index]) & (confidence <= edges[index + 1])
        else:
            mask = (confidence >= edges[index]) & (confidence < edges[index + 1])
        if np.any(mask):
            ece += float(mask.mean()) * abs(float(confidence[mask].mean() - correct[mask].mean()))
    return MulticlassMetrics(
        rows=len(labels),
        log_loss=float(-np.mean(np.log(selected))),
        multiclass_brier=float(np.mean(np.sum((matrix - truth) ** 2, axis=1))),
        classwise_brier={
            outcome.value: float(classwise[index])
            for index, outcome in enumerate(ordered_outcomes())
        },
        top_label_ece=ece,
    )


@dataclass(frozen=True)
class TemperatureCalibrator:
    version: str
    temperature: float
    fit_rows: int
    selection_rows: int
    fit_checksum: str
    selection_checksum: str
    parameter_checksum: str
    uncalibrated_selection_metrics: MulticlassMetrics
    calibrated_selection_metrics: MulticlassMetrics

    def calibrate(
        self, probabilities: Mapping[PlateAppearanceOutcome, float]
    ) -> EventProbabilityVector:
        vector = np.asarray(
            [EventProbabilityVector(probabilities, "input", EVENT_FEATURE_SCHEMA_HASH).probabilities[o] for o in ordered_outcomes()],
            dtype=float,
        )
        logits = np.log(np.clip(vector, 1e-15, 1.0)) / self.temperature
        logits -= logits.max()
        calibrated = np.exp(logits)
        calibrated /= calibrated.sum()
        return EventProbabilityVector(
            probabilities={o: float(calibrated[i]) for i, o in enumerate(ordered_outcomes())},
            model_version=self.version,
            feature_schema_hash=EVENT_FEATURE_SCHEMA_HASH,
        )


def fit_temperature_calibrator(
    fit_records: Sequence[OOFMulticlassPrediction],
    *,
    selection_records: Sequence[OOFMulticlassPrediction],
) -> TemperatureCalibrator:
    """Fit temperature on one OOF slice and audit it on a disjoint selection slice."""

    fit_rows = tuple(fit_records)
    selection_rows = tuple(selection_records)
    if not fit_rows or not selection_rows:
        raise EventModelError("OOF fit and selection records must be nonempty")
    fit_sample_ids = tuple(row.sample_id for row in fit_rows)
    selection_sample_ids = tuple(row.sample_id for row in selection_rows)
    if len(set(fit_sample_ids)) != len(fit_sample_ids):
        raise EventModelError("OOF fit sample identities must be unique and aligned")
    if len(set(selection_sample_ids)) != len(selection_sample_ids):
        raise EventModelError("OOF selection sample identities must be unique and aligned")
    if set(fit_sample_ids).intersection(selection_sample_ids):
        raise EventModelError("calibration fit and selection populations must be disjoint")
    fit_targets = tuple(row.target for row in fit_rows)
    fit_predictions = tuple(row.probabilities for row in fit_rows)
    selection_targets = tuple(row.target for row in selection_rows)
    selection_predictions = tuple(row.probabilities for row in selection_rows)
    candidates = np.linspace(0.5, 2.0, 151)
    best_temperature = 1.0
    best_loss = float("inf")
    for candidate in candidates:
        losses: list[float] = []
        for target, row in zip(fit_targets, fit_predictions, strict=True):
            vector = np.asarray(
                [EventProbabilityVector(row, "oof", EVENT_FEATURE_SCHEMA_HASH).probabilities[o] for o in ordered_outcomes()],
                dtype=float,
            )
            logits = np.log(np.clip(vector, 1e-15, 1.0)) / float(candidate)
            logits -= logits.max()
            transformed = np.exp(logits)
            transformed /= transformed.sum()
            losses.append(-log(float(np.clip(transformed[ordered_outcomes().index(target)], 1e-15, 1.0))))
        mean_loss = float(np.mean(losses))
        if mean_loss < best_loss - 1e-15:
            best_loss = mean_loss
            best_temperature = float(candidate)
    fit_payload = {
        "records": [_oof_payload(row) for row in fit_rows],
        "targets": [target.value for target in fit_targets],
        "predictions": [
            [float(row[outcome]) for outcome in ordered_outcomes()] for row in fit_predictions
        ],
        "selection_grid": [0.5, 2.0, 0.01],
    }
    calibrated_selection: list[Mapping[PlateAppearanceOutcome, float]] = []
    for row in selection_predictions:
        vector = np.asarray(
            [EventProbabilityVector(row, "selection", EVENT_FEATURE_SCHEMA_HASH).probabilities[o] for o in ordered_outcomes()],
            dtype=float,
        )
        logits = np.log(np.clip(vector, 1e-15, 1.0)) / best_temperature
        logits -= logits.max()
        transformed = np.exp(logits)
        transformed /= transformed.sum()
        calibrated_selection.append(
            {outcome: float(transformed[index]) for index, outcome in enumerate(ordered_outcomes())}
        )
    selection_payload = {
        "records": [_oof_payload(row) for row in selection_rows],
        "targets": [target.value for target in selection_targets],
        "predictions": [
            [float(row[outcome]) for outcome in ordered_outcomes()]
            for row in selection_predictions
        ],
    }
    uncalibrated_metrics = multiclass_metrics(selection_targets, selection_predictions)
    calibrated_metrics = multiclass_metrics(selection_targets, calibrated_selection)
    return TemperatureCalibrator(
        version=TEMPERATURE_CALIBRATION_VERSION,
        temperature=best_temperature,
        fit_rows=len(fit_targets),
        selection_rows=len(selection_targets),
        fit_checksum=_checksum(fit_payload),
        selection_checksum=_checksum(selection_payload),
        parameter_checksum=_checksum(
            {
                "version": TEMPERATURE_CALIBRATION_VERSION,
                "temperature": best_temperature,
                "feature_schema_hash": EVENT_FEATURE_SCHEMA_HASH,
                "fit_checksum": _checksum(fit_payload),
                "selection_checksum": _checksum(selection_payload),
            }
        ),
        uncalibrated_selection_metrics=uncalibrated_metrics,
        calibrated_selection_metrics=calibrated_metrics,
    )


def _oof_payload(row: OOFMulticlassPrediction) -> dict[str, object]:
    return {
        "sample_id": row.sample_id,
        "target": row.target.value,
        "event_at": _utc(row.event_at, "OOF event_at").isoformat(),
        "fold_identity_checksum": row.fold_identity_checksum,
        "training_cutoff": _utc(row.training_cutoff, "OOF training_cutoff").isoformat(),
        "training_sample_ids": row.training_sample_ids,
        "model_training_checksum": row.model_training_checksum,
        "probabilities": [
            float(row.probabilities[outcome]) for outcome in ordered_outcomes()
        ],
    }


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value.lower()
    ):
        raise EventModelError(f"{field} must be SHA-256")
