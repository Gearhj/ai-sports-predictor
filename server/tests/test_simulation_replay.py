"""Statistical and correctness invariants for the Monte Carlo replay.

These run entirely against the pure simulator and in-memory objects. They never
touch the live PostgreSQL database and never make a provider call.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.forecasting.simulation import simulate_game
from app.services.simulation_replay_service import (
    REPRESENTATIVE_KEYS,
    SCORE_CAP,
    _joint_matrix,
    _representative,
    _sampling_uncertainty,
    _validate,
)

LAM_HOME = 4.2619
LAM_AWAY = 4.5634
SIMS = 20_000
SEED = 20250601


@pytest.fixture(scope="module")
def run():
    return simulate_game(LAM_HOME, LAM_AWAY, simulations=SIMS, seed=SEED, return_draws=True)


# -- determinism ----------------------------------------------------------
def test_same_seed_reproduces_identical_draws(run):
    again = simulate_game(LAM_HOME, LAM_AWAY, simulations=SIMS, seed=SEED, return_draws=True)
    assert np.array_equal(run.draws.home, again.draws.home)
    assert np.array_equal(run.draws.away, again.draws.away)
    assert np.array_equal(run.draws.regulation_tie, again.draws.regulation_tie)
    assert run.home_win_probability == again.home_win_probability


def test_different_seed_changes_the_run(run):
    other = simulate_game(LAM_HOME, LAM_AWAY, simulations=SIMS, seed=SEED + 1, return_draws=True)
    assert not np.array_equal(run.draws.home, other.draws.home)


def test_requesting_draws_does_not_change_any_aggregate():
    without = simulate_game(LAM_HOME, LAM_AWAY, simulations=SIMS, seed=SEED)
    with_draws = simulate_game(LAM_HOME, LAM_AWAY, simulations=SIMS, seed=SEED, return_draws=True)
    assert without.draws is None
    for field in (
        "home_win_probability", "away_win_probability", "mean_home_runs", "mean_away_runs",
        "mean_total_runs", "mean_run_difference", "home_runs_distribution",
        "away_runs_distribution", "total_runs_distribution", "run_difference_distribution",
        "home_cover_probabilities", "away_cover_probabilities", "total_probabilities",
    ):
        assert getattr(without, field) == getattr(with_draws, field), field


# -- probability and distribution normalization ---------------------------
def test_win_probabilities_sum_to_one(run):
    assert run.home_win_probability + run.away_win_probability == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("field", [
    "home_runs_distribution", "away_runs_distribution",
    "total_runs_distribution", "run_difference_distribution",
])
def test_score_frequency_distributions_normalize(run, field):
    values = getattr(run, field).values()
    assert all(v >= 0 for v in values)
    assert sum(values) == pytest.approx(1.0, abs=1e-9)


def test_total_line_probabilities_normalize(run):
    for line, probs in run.total_probabilities.items():
        assert probs["over"] + probs["under"] + probs["push"] == pytest.approx(1.0, abs=1e-9), line
        assert all(0.0 <= p <= 1.0 for p in probs.values())


def test_push_only_occurs_on_whole_number_totals(run):
    for line, probs in run.total_probabilities.items():
        if float(line) != int(float(line)):
            assert probs["push"] == 0.0, f"half-run line {line} cannot push"


def test_run_line_cover_probabilities_are_valid(run):
    for probs in (run.home_cover_probabilities, run.away_cover_probabilities):
        assert all(0.0 <= p <= 1.0 for p in probs.values())
    # A team covering -1.5 must be no more likely than winning outright.
    assert run.home_cover_probabilities["-1.5"] <= run.home_win_probability + 1e-12


# -- baseball domain invariants -------------------------------------------
def test_no_negative_runs(run):
    assert int(run.draws.home.min()) >= 0
    assert int(run.draws.away.min()) >= 0


def test_no_simulated_game_ends_in_a_tie(run):
    assert int(np.sum(run.draws.home == run.draws.away)) == 0


def test_regulation_ties_are_resolved_by_exactly_one_run(run):
    tie = run.draws.regulation_tie
    assert tie.any(), "fixture should produce some extra-inning games"
    margins = np.abs(run.draws.home[tie] - run.draws.away[tie])
    assert set(np.unique(margins).tolist()) == {1}


def test_non_tie_draws_keep_their_regulation_margin(run):
    regulation = ~run.draws.regulation_tie
    assert int(np.sum(run.draws.home[regulation] == run.draws.away[regulation])) == 0


def test_winner_matches_the_score(run):
    home_wins = run.draws.home > run.draws.away
    assert float(np.mean(home_wins)) == pytest.approx(run.home_win_probability, abs=1e-12)


def test_simulation_count_identity(run):
    assert run.simulations == SIMS == len(run.draws.home) == len(run.draws.away)


def test_no_nan_or_infinite_output(run):
    scalars = [
        run.home_win_probability, run.away_win_probability, run.mean_home_runs,
        run.mean_away_runs, run.mean_total_runs, run.mean_run_difference,
    ]
    for value in scalars:
        assert math.isfinite(value)
    for field in ("home_runs_distribution", "total_runs_distribution"):
        assert all(math.isfinite(v) for v in getattr(run, field).values())


def test_simulator_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        simulate_game(0.0, 4.0)
    with pytest.raises(ValueError):
        simulate_game(4.0, float("nan"))
    with pytest.raises(ValueError):
        simulate_game(4.0, 4.0, simulations=10)


# -- joint distribution ----------------------------------------------------
def test_joint_matrix_normalizes_and_keeps_overflow(run):
    joint = _joint_matrix(run.draws.home, run.draws.away)
    flat = [p for row in joint["probabilities"] for p in row]
    assert sum(flat) == pytest.approx(1.0, abs=1e-6)
    assert all(p >= 0 for p in flat)
    assert joint["home_labels"][-1] == f">{SCORE_CAP}"
    assert sum(sum(row) for row in joint["counts"]) == SIMS


def test_joint_matrix_marginals_match_the_official_marginals(run):
    joint = _joint_matrix(run.draws.home, run.draws.away)
    counts = np.array(joint["counts"])
    home_marginal = counts.sum(axis=1) / SIMS
    for score in range(SCORE_CAP + 1):
        assert home_marginal[score] == pytest.approx(
            run.home_runs_distribution[str(score)], abs=1e-9
        ), score


def test_joint_diagonal_is_empty_because_games_cannot_tie(run):
    counts = np.array(_joint_matrix(run.draws.home, run.draws.away)["counts"])
    assert int(np.trace(counts)) == 0


# -- representative outcomes ----------------------------------------------
def test_representative_outcomes_are_real_draws(run):
    outcomes = _representative(run.draws.home, run.draws.away, run.draws.regulation_tie)
    assert [o["key"] for o in outcomes] == list(REPRESENTATIVE_KEYS)
    for outcome in outcomes:
        if not outcome["available"]:
            continue
        index = outcome["draw_index"]
        assert int(run.draws.home[index]) == outcome["home_runs"]
        assert int(run.draws.away[index]) == outcome["away_runs"]
        assert outcome["total_runs"] == outcome["home_runs"] + outcome["away_runs"]
        assert 0 < outcome["share_of_simulations"] <= 1


def test_representative_outcomes_respect_their_own_criteria(run):
    by_key = {o["key"]: o for o in _representative(
        run.draws.home, run.draws.away, run.draws.regulation_tie)}
    assert by_key["home_win"]["home_runs"] > by_key["home_win"]["away_runs"]
    assert by_key["away_win"]["away_runs"] > by_key["away_win"]["home_runs"]
    assert by_key["high_scoring"]["total_runs"] > by_key["low_scoring"]["total_runs"]
    assert by_key["extra_innings"]["went_extra_innings"] is True
    assert abs(by_key["extra_innings"]["home_runs"] - by_key["extra_innings"]["away_runs"]) == 1


def test_representative_selection_is_deterministic(run):
    first = _representative(run.draws.home, run.draws.away, run.draws.regulation_tie)
    second = _representative(run.draws.home, run.draws.away, run.draws.regulation_tie)
    assert first == second


def test_unavailable_criterion_reports_itself_rather_than_substituting():
    home = np.array([5, 4, 6])
    away = np.array([2, 1, 3])
    no_ties = np.array([False, False, False])
    by_key = {o["key"]: o for o in _representative(home, away, no_ties)}
    assert by_key["extra_innings"]["available"] is False
    assert by_key["extra_innings"]["home_runs"] is None
    assert by_key["away_win"]["available"] is False


# -- replay validation -----------------------------------------------------
def test_replay_validation_detects_an_exact_match(run):
    persisted = {
        "home_win_probability": run.home_win_probability,
        "mean_total_runs": run.mean_total_runs,
        "total_runs_distribution": run.total_runs_distribution,
    }
    validation = _validate(persisted, run)
    assert validation.exact is True
    assert validation.max_absolute_delta == 0.0
    assert validation.compared_fields > 0


def test_replay_validation_detects_divergence(run):
    persisted = {"home_win_probability": run.home_win_probability + 0.01}
    validation = _validate(persisted, run)
    assert validation.exact is False
    assert validation.max_absolute_delta == pytest.approx(0.01, abs=1e-9)


# -- sampling uncertainty --------------------------------------------------
def test_sampling_uncertainty_is_bounded_and_finite():
    u = _sampling_uncertainty(0.5, SIMS)
    assert math.isfinite(u["standard_error"])
    assert 0.0 <= u["confidence_95_low"] < u["confidence_95_high"] <= 1.0
    assert u["standard_error"] == pytest.approx(math.sqrt(0.25 / SIMS), rel=1e-9)


def test_sampling_uncertainty_handles_degenerate_probability():
    u = _sampling_uncertainty(1.0, SIMS)
    assert u["standard_error"] == 0.0
    assert u["confidence_95_high"] <= 1.0
