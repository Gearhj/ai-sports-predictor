from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.modeling import (
    EmpiricalBayesTeamClassifier,
    HomePriorClassifier,
    LimitedXGBoostClassifier,
    NegativeBinomialRunRegressor,
    NonnegativeStacker,
    OOFPredictionRow,
    OODDetector,
    PlattCalibrator,
    PoissonRunRegressor,
    RatingClassifier,
    RegularizedLogisticClassifier,
    RunStrengthClassifier,
    brier_score,
    calibration_intercept_slope,
    classification_metrics,
    expected_calibration_error,
    paired_block_bootstrap_delta,
    probability_log_loss,
    rmse,
    select_probability_calibrator,
)


START = datetime(2025, 4, 1, tzinfo=UTC)


def test_probability_metrics_and_date_block_bootstrap_are_deterministic() -> None:
    outcomes = [0, 1] * 10
    good = [0.20, 0.80] * 10
    bad = [0.70, 0.30] * 10
    groups = [START.date() + timedelta(days=index // 2) for index in range(20)]

    report = classification_metrics(good, outcomes, bins=5)
    intercept, slope = calibration_intercept_slope(good, outcomes)
    delta = paired_block_bootstrap_delta(good, bad, outcomes, groups, resamples=100, random_seed=9)
    repeated = paired_block_bootstrap_delta(good, bad, outcomes, groups, resamples=100, random_seed=9)

    assert report.brier_score == pytest.approx(brier_score(good, outcomes))
    assert report.probability_rmse == pytest.approx(rmse(outcomes, good))
    assert report.expected_calibration_error == pytest.approx(expected_calibration_error(good, outcomes, bins=5))
    assert np.isfinite([intercept, slope]).all()
    assert delta.estimate < 0
    assert delta == repeated


def test_calibrator_selection_uses_earlier_and_later_validation_dates_only() -> None:
    outcomes = np.asarray([index % 2 for index in range(80)], dtype=int)
    # Deliberately underconfident base probabilities give Platt a useful option.
    probabilities = np.where(outcomes == 1, 0.58, 0.42)
    event_at = [START + timedelta(days=index) for index in range(80)]
    selection = select_probability_calibrator(
        probabilities,
        outcomes,
        event_at,
        min_isotonic_samples=20,
    )
    calibrated = selection.calibrator.transform([0.42, 0.58])

    assert selection.method in {"identity", "platt", "isotonic"}
    assert selection.calibration_fit_count + selection.selection_count == 80
    assert selection.total_validation_count == 80
    assert set(selection.selection_scores) == {"identity", "platt", "isotonic"}
    assert np.all((calibrated > 0) & (calibrated < 1))
    with pytest.raises(ValueError, match="date groups"):
        select_probability_calibrator([0.4, 0.6], [0, 1], [START, START + timedelta(days=1)])


def test_nonnegative_stacker_learns_from_strict_oof_rows() -> None:
    rows = []
    for index in range(30):
        outcome = index % 2
        rows.append(OOFPredictionRow(
            game_id=f"g-{index}",
            event_at=START + timedelta(days=index + 10),
            trained_through=START + timedelta(days=index),
            outcome=outcome,
            predictions={
                "good": 0.85 if outcome else 0.15,
                "bad": 0.15 if outcome else 0.85,
                "neutral": 0.50,
            },
        ))
    stacker = NonnegativeStacker().fit(rows)

    assert stacker.fit_ is not None
    assert sum(stacker.fit_.weights) == pytest.approx(1.0)
    assert all(weight >= 0 for weight in stacker.fit_.weights)
    assert stacker.fit_.weight_map["good"] > stacker.fit_.weight_map["bad"]
    prediction = stacker.predict({"good": [0.8], "bad": [0.2], "neutral": [0.5]})
    assert prediction[0] > 0.5
    with pytest.raises(ValueError, match="strictly earlier"):
        OOFPredictionRow("bad-oof", START, START, 1, {"x": 0.5})


def test_interpretable_classifiers_and_empirical_bayes_shrinkage() -> None:
    names = (
        "home_rating", "away_rating", "home_runs_per_game", "away_runs_per_game",
        "home_runs_allowed_per_game", "away_runs_allowed_per_game",
    )
    matrix = np.asarray([
        [1600, 1450, 5.2, 3.8, 3.7, 5.1],
        [1450, 1600, 3.8, 5.2, 5.1, 3.7],
        [1550, 1500, 4.8, 4.2, 4.0, 4.7],
        [1500, 1550, 4.2, 4.8, 4.7, 4.0],
    ], dtype=float)
    outcomes = [1, 0, 1, 0]

    prior = HomePriorClassifier().fit(matrix, outcomes, names)
    rating = RatingClassifier().fit(matrix, outcomes, names)
    runs = RunStrengthClassifier().fit(matrix, outcomes, names)
    logistic = RegularizedLogisticClassifier().fit(matrix, outcomes, names, row_ids=("a", "b", "c", "d"))

    assert 0 < prior.predict_proba(matrix, names)[0] < 1
    assert rating.predict_proba(matrix, names)[0] > rating.predict_proba(matrix, names)[1]
    assert runs.predict_proba(matrix, names)[0] > runs.predict_proba(matrix, names)[1]
    assert logistic.predict_proba(matrix, names)[0] > logistic.predict_proba(matrix, names)[1]
    assert logistic.preprocessor.audit.fit_row_ids == ("a", "b", "c", "d")

    team = EmpiricalBayesTeamClassifier(prior_strength=20).fit(
        ["A", "A", "C", "D"], ["B", "C", "D", "B"], [1, 1, 0, 0]
    )
    probability = team.predict_proba(["A", "unknown"], ["B", "other"])
    assert 0.5 < probability[0] < 0.80  # Evidence is useful but visibly shrunk.
    assert probability[1] == pytest.approx(0.54)


def test_poisson_run_model_predicts_positive_runs_and_win_probability() -> None:
    rng = np.random.default_rng(3)
    strength = np.linspace(-1.5, 1.5, 50)
    matrix = np.column_stack([strength, rng.normal(size=50)])
    home_runs = np.clip(4.5 + strength + rng.normal(scale=0.2, size=50), 0, None)
    away_runs = np.clip(4.5 - strength + rng.normal(scale=0.2, size=50), 0, None)
    model = PoissonRunRegressor(alpha=0.2).fit(matrix, home_runs, away_runs, ("strength", "noise"))
    predicted_home, predicted_away = model.predict_runs(matrix, ("strength", "noise"))
    probability = model.predict_proba(matrix, ("strength", "noise"))

    assert np.all(predicted_home > 0) and np.all(predicted_away > 0)
    assert probability[-1] > probability[0]
    assert np.all((probability > 0) & (probability < 1))


def test_negative_binomial_run_model_records_training_only_dispersion() -> None:
    rng = np.random.default_rng(13)
    strength = np.linspace(-1.5, 1.5, 80)
    matrix = np.column_stack([strength, rng.normal(size=80)])
    home_mean = np.clip(4.5 + strength, 0.2, None)
    away_mean = np.clip(4.5 - strength, 0.2, None)
    # Gamma-Poisson draws provide deliberate NB2-style overdispersion.
    home_rate = rng.gamma(shape=2.0, scale=home_mean / 2.0)
    away_rate = rng.gamma(shape=2.0, scale=away_mean / 2.0)
    home_runs = rng.poisson(home_rate)
    away_runs = rng.poisson(away_rate)
    model = NegativeBinomialRunRegressor(mean_alpha=0.2).fit(
        matrix,
        home_runs,
        away_runs,
        ("strength", "noise"),
        row_ids=tuple(f"g-{index}" for index in range(80)),
    )

    probability = model.predict_proba(matrix, ("strength", "noise"))

    assert np.all((probability > 0) & (probability < 1))
    assert probability[-1] > probability[0]
    assert model.dispersion_diagnostic["material_overdispersion"] is True
    assert model.fit_row_ids == tuple(f"g-{index}" for index in range(80))


def test_limited_xgboost_enforces_complexity_and_trains_deterministically() -> None:
    with pytest.raises(ValueError, match="complexity"):
        LimitedXGBoostClassifier(n_estimators=401)
    rng = np.random.default_rng(11)
    matrix = rng.normal(size=(60, 3))
    outcomes = (matrix[:, 0] + matrix[:, 1] * 0.2 > 0).astype(int)
    first = LimitedXGBoostClassifier(n_estimators=10, max_depth=2, random_seed=17).fit(
        matrix, outcomes, ("a", "b", "c")
    )
    second = LimitedXGBoostClassifier(n_estimators=10, max_depth=2, random_seed=17).fit(
        matrix, outcomes, ("a", "b", "c")
    )
    assert np.allclose(first.predict_proba(matrix, ("a", "b", "c")), second.predict_proba(matrix, ("a", "b", "c")))


def test_ood_diagnostic_uses_only_training_feature_reference() -> None:
    names = ("x", "x__missing", "y", "y__missing")
    train = np.asarray([[0.0, 0, 1.0, 0], [0.1, 0, 1.1, 0], [-0.1, 0, 0.9, 0], [0.05, 0, 1.05, 0]])
    detector = OODDetector().fit(train, names)
    ordinary = detector.diagnose(np.asarray([[0.02, 0, 1.02, 0]]), names)
    shifted = detector.diagnose(np.asarray([[50.0, 1, -40.0, 1]]), names)

    assert ordinary.flagged is False
    assert shifted.flagged is True
    assert {"robust_z", "outside_training_range", "missingness_shift"}.issubset(shifted.reasons)


def test_platt_calibrator_rejects_single_class_and_log_loss_orders_models() -> None:
    with pytest.raises(ValueError, match="both outcomes"):
        PlattCalibrator().fit([0.2, 0.3], [0, 0])
    assert probability_log_loss([0.2, 0.8], [0, 1]) < probability_log_loss([0.8, 0.2], [0, 1])
