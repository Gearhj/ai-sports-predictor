"""What-If Scenario Lab: honesty, immutability and determinism.

These run against an in-memory session built from the shared test fixtures and
never touch the live PostgreSQL database or any provider.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.forecasting.ensemble import GameFeatures, MLBForecastEnsemble
from app.services.scenario_service import (
    BASELINE_TOLERANCE,
    CONTROL_BOUNDS,
    _deltas,
    _identity_hash,
    _interpretation,
    _metrics,
    _sensitivity,
)


class _Settings:
    """Minimal stand-in carrying only what the scenario path reads."""

    ensemble_baseline_weight = 0.30
    ensemble_run_weight = 0.45
    ensemble_boosting_weight = 0.25
    simulation_draws = 4000
    simulation_seed = 20250601


SETTINGS = _Settings()


def features(**overrides) -> GameFeatures:
    base = GameFeatures(
        home_rating=1520.0, away_rating=1495.0,
        home_runs_per_game=4.6, away_runs_per_game=4.4,
        home_runs_allowed_per_game=4.3, away_runs_allowed_per_game=4.5,
        temperature_f=78.0, park_run_factor=1.0,
        feature_completeness=0.8, sample_reliability=0.7,
    )
    return replace(base, **overrides) if overrides else base


def run(feature_set: GameFeatures):
    return MLBForecastEnsemble(
        baseline_weight=SETTINGS.ensemble_baseline_weight,
        run_weight=SETTINGS.ensemble_run_weight,
        boosting_weight=SETTINGS.ensemble_boosting_weight,
        simulation_draws=SETTINGS.simulation_draws,
        simulation_seed=SETTINGS.simulation_seed,
    ).predict(feature_set, data_readiness=62.5)


# -- determinism and identity ---------------------------------------------
def test_identical_inputs_produce_identical_results():
    a = _metrics(run(features(temperature_f=55.0)))
    b = _metrics(run(features(temperature_f=55.0)))
    assert a == b


def test_scenario_hash_is_stable_and_order_independent():
    one = {"forecast": "f1", "overrides": {"temperature_f": 55.0, "park_run_factor": 1.1}}
    two = {"overrides": {"park_run_factor": 1.1, "temperature_f": 55.0}, "forecast": "f1"}
    assert _identity_hash(one) == _identity_hash(two)


def test_different_assumptions_produce_a_different_hash():
    assert _identity_hash({"overrides": {"temperature_f": 55.0}}) \
        != _identity_hash({"overrides": {"temperature_f": 56.0}})


# -- the scenario flows through the real model -----------------------------
def test_temperature_moves_expected_runs_through_the_run_component():
    cold = _metrics(run(features(temperature_f=45.0)))
    hot = _metrics(run(features(temperature_f=100.0)))
    assert hot["expected_total_runs"] > cold["expected_total_runs"]


def test_park_factor_scales_expected_runs():
    neutral = _metrics(run(features(park_run_factor=1.0)))
    hitter = _metrics(run(features(park_run_factor=1.12)))
    assert hitter["expected_total_runs"] > neutral["expected_total_runs"]


def test_an_unchanged_scenario_equals_the_baseline_exactly():
    baseline = _metrics(run(features()))
    unchanged = _metrics(run(features()))
    deltas = _deltas(baseline, unchanged)
    assert deltas["home_win_probability_points"] == 0.0
    assert deltas["expected_total_runs"] == 0.0


def test_a_scenario_produces_a_full_distribution_not_just_a_number():
    scenario = _metrics(run(features(park_run_factor=1.15)))
    for key in (
        "total_runs_distribution", "home_runs_distribution", "away_runs_distribution",
        "home_cover_probabilities", "total_probabilities",
    ):
        assert scenario[key], key
    assert sum(scenario["total_runs_distribution"].values()) == pytest.approx(1.0, abs=1e-9)


# -- delta arithmetic ------------------------------------------------------
def test_probability_deltas_are_percentage_points():
    official = _metrics(run(features()))
    scenario = _metrics(run(features(park_run_factor=1.15)))
    deltas = _deltas(official, scenario)
    expected = (scenario["home_win_probability"] - official["home_win_probability"]) * 100
    assert deltas["home_win_probability_points"] == pytest.approx(expected, abs=1e-12)


def test_home_and_away_probability_deltas_are_equal_and_opposite():
    deltas = _deltas(_metrics(run(features())), _metrics(run(features(temperature_f=50.0))))
    assert deltas["home_win_probability_points"] == pytest.approx(
        -deltas["away_win_probability_points"], abs=1e-9)


def test_run_deltas_stay_in_runs_not_percentages():
    official = _metrics(run(features()))
    scenario = _metrics(run(features(park_run_factor=1.12)))
    deltas = _deltas(official, scenario)
    assert deltas["expected_total_runs"] == pytest.approx(
        scenario["expected_total_runs"] - official["expected_total_runs"], abs=1e-12)
    assert abs(deltas["expected_total_runs"]) < 5


# -- interpretation is arithmetic, not narrative ---------------------------
def _capabilities():
    return {"controls": [
        {"key": "temperature_f", "label": "Temperature", "status": "SUPPORTED"},
        {"key": "park_run_factor", "label": "Park run factor", "status": "SUPPORTED"},
    ]}


def test_interpretation_reports_no_change_when_nothing_is_applied():
    text = _interpretation(_metrics(run(features())), _metrics(run(features())), {}, _capabilities())
    assert "No supported assumption is active" in text


def test_interpretation_quotes_the_computed_deltas():
    official = _metrics(run(features()))
    scenario = _metrics(run(features(park_run_factor=1.15)))
    text = _interpretation(official, scenario, {"park_run_factor": 1.15}, _capabilities())
    points = abs(scenario["home_win_probability"] - official["home_win_probability"]) * 100
    total = scenario["expected_total_runs"] - official["expected_total_runs"]
    assert f"{points:.1f} percentage points" in text
    assert f"{total:+.2f}" in text
    assert "Park run factor" in text


def test_interpretation_says_so_when_the_change_is_negligible():
    official = _metrics(run(features()))
    scenario = _metrics(run(features(temperature_f=78.01)))
    text = _interpretation(official, scenario, {"temperature_f": 78.01}, _capabilities())
    assert "essentially unchanged" in text


# -- sensitivity -----------------------------------------------------------
def test_sensitivity_is_empty_below_two_assumptions():
    assert _sensitivity(SETTINGS, features(), 62.5, {"temperature_f": 55.0}) == []


def test_sensitivity_computes_each_effect_in_isolation():
    rows = _sensitivity(SETTINGS, features(), 62.5,
                        {"temperature_f": 50.0, "park_run_factor": 1.12})
    assert {row["key"] for row in rows} == {"temperature_f", "park_run_factor"}
    # Ordered by magnitude of the win-probability effect.
    assert abs(rows[0]["home_win_probability_points"]) >= abs(rows[1]["home_win_probability_points"])
    for row in rows:
        assert isinstance(row["expected_total_runs"], float)


def test_isolated_effects_need_not_sum_to_the_combined_effect():
    combined = _deltas(
        _metrics(run(features())),
        _metrics(run(features(temperature_f=50.0, park_run_factor=1.12))),
    )["expected_total_runs"]
    rows = _sensitivity(SETTINGS, features(), 62.5,
                        {"temperature_f": 50.0, "park_run_factor": 1.12})
    isolated = sum(row["expected_total_runs"] for row in rows)
    # They are close but not identical, which is exactly why the UI says so.
    assert combined == pytest.approx(isolated, abs=0.35)


# -- bounds ----------------------------------------------------------------
def test_control_bounds_are_baseball_realistic():
    temp = CONTROL_BOUNDS["temperature_f"]
    assert temp["min"] >= 0 and temp["max"] <= 130
    assert temp["domain_min"] > temp["min"] and temp["domain_max"] < temp["max"]
    park = CONTROL_BOUNDS["park_run_factor"]
    assert 0.5 < park["min"] < 1.0 < park["max"] < 2.0


def test_baseline_tolerance_is_a_regression_guard_not_a_fudge():
    assert BASELINE_TOLERANCE <= 1e-9
