from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

import numpy as np
from xgboost import XGBClassifier

from app.forecasting.evaluation import brier_score, chronological_split, ChronologicalRecord, log_loss


@dataclass(frozen=True)
class XGBoostTrainingRow:
    event_at: datetime
    features: Sequence[float]
    home_win: int


@dataclass(frozen=True)
class XGBoostTrainingResult:
    model: XGBClassifier
    feature_count: int
    train_count: int
    validation_count: int
    test_count: int
    training_window: dict[str, str]
    validation_metrics: dict[str, float]
    test_metrics: dict[str, float]


def train_chronological_xgboost(
    rows: Sequence[XGBoostTrainingRow],
    *,
    random_seed: int = 20250601,
    n_estimators: int = 100,
    max_depth: int = 3,
) -> XGBoostTrainingResult:
    """Train the concrete Phase 1 boosting component with time-ordered splits."""
    if len(rows) < 20:
        raise ValueError("At least 20 chronological observations are required")
    feature_count = len(rows[0].features)
    if feature_count == 0 or any(len(row.features) != feature_count for row in rows):
        raise ValueError("All rows must share a non-empty feature schema")
    if any(row.home_win not in (0, 1) for row in rows):
        raise ValueError("home_win outcomes must be binary")
    records = [ChronologicalRecord(row.event_at, {"row": row}) for row in rows]
    train_records, validation_records, test_records = chronological_split(records, 0.70, 0.15)
    if not validation_records or not test_records:
        raise ValueError("Chronological validation and test windows must be non-empty")

    def arrays(items: Sequence[ChronologicalRecord]) -> tuple[np.ndarray, np.ndarray]:
        selected = [item.values["row"] for item in items]
        return np.asarray([row.features for row in selected], dtype=float), np.asarray([row.home_win for row in selected], dtype=int)

    x_train, y_train = arrays(train_records)
    x_validation, y_validation = arrays(validation_records)
    x_test, y_test = arrays(test_records)
    if len(set(y_train.tolist())) < 2:
        raise ValueError("Training window must contain both outcomes")
    model = XGBClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.9,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=random_seed,
        n_jobs=1,
        tree_method="hist",
    )
    model.fit(x_train, y_train, eval_set=[(x_validation, y_validation)], verbose=False)
    validation_probability = model.predict_proba(x_validation)[:, 1].tolist()
    test_probability = model.predict_proba(x_test)[:, 1].tolist()
    ordered = sorted(rows, key=lambda row: row.event_at)
    return XGBoostTrainingResult(
        model=model,
        feature_count=feature_count,
        train_count=len(train_records),
        validation_count=len(validation_records),
        test_count=len(test_records),
        training_window={
            "train_start": ordered[0].event_at.isoformat(),
            "train_end": train_records[-1].event_at.isoformat(),
            "validation_end": validation_records[-1].event_at.isoformat(),
            "test_end": ordered[-1].event_at.isoformat(),
            "split_policy": "chronological_70_15_15",
        },
        validation_metrics={"brier_score": brier_score(validation_probability, y_validation.tolist()), "log_loss": log_loss(validation_probability, y_validation.tolist())},
        test_metrics={"brier_score": brier_score(test_probability, y_test.tolist()), "log_loss": log_loss(test_probability, y_test.tolist())},
    )
