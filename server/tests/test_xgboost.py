from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from app.forecasting.xgboost_model import XGBoostTrainingRow, train_chronological_xgboost


def test_concrete_xgboost_training_uses_chronological_windows() -> None:
    rng = np.random.default_rng(17)
    start = datetime(2025, 4, 1, tzinfo=UTC)
    rows = []
    for index in range(80):
        strength = float(rng.normal())
        rows.append(XGBoostTrainingRow(
            event_at=start + timedelta(days=index),
            features=[strength, float(rng.normal()), index / 80],
            home_win=int(strength + float(rng.normal(scale=.4)) > 0),
        ))
    result = train_chronological_xgboost(rows, n_estimators=12, max_depth=2)
    assert result.feature_count == 3
    assert (result.train_count, result.validation_count, result.test_count) == (56, 12, 12)
    assert result.training_window["split_policy"] == "chronological_70_15_15"
    assert 0 <= result.validation_metrics["brier_score"] <= 1
    assert 0 <= result.test_metrics["brier_score"] <= 1
