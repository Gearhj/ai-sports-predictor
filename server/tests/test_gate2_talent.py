from __future__ import annotations

import pytest

from app.features.talent import estimate_platoon_talent, estimate_true_talent


def test_small_sample_shrinks_more_than_large_sample() -> None:
    small = estimate_true_talent(
        prior=0.320, prior_sample_size=200, season_value=0.400, season_sample_size=20
    )
    large = estimate_true_talent(
        prior=0.320, prior_sample_size=200, season_value=0.400, season_sample_size=600
    )
    assert small.value == pytest.approx((0.320 * 200 + 0.400 * 20) / 220)
    assert abs(small.value - 0.320) < abs(large.value - 0.320)
    assert large.value < 0.400


def test_extreme_observation_is_bounded_and_strongly_shrunk() -> None:
    estimate = estimate_true_talent(
        prior=0.320,
        prior_sample_size=300,
        season_value=1.0,
        season_sample_size=1,
        lower_bound=0.0,
        upper_bound=1.0,
    )
    assert 0.320 < estimate.value < 0.325
    assert 0 <= estimate.value <= 1
    assert sum(estimate.weights.values()) == pytest.approx(1.0)


def test_missing_recent_falls_back_to_prior_and_season_without_invention() -> None:
    result = estimate_true_talent(
        prior=0.310,
        prior_sample_size=150,
        season_value=0.340,
        season_sample_size=300,
        recent_value=None,
        recent_sample_size=50,
    )
    assert result.value == pytest.approx((0.310 * 150 + 0.340 * 300) / 450)
    assert not result.recent_evidence_used
    assert set(result.weights) == {"prior", "season"}


def test_underlying_season_skill_dominates_noisy_recent_outcome() -> None:
    stable = estimate_true_talent(
        prior=0.320,
        prior_sample_size=200,
        season_value=0.360,
        season_sample_size=500,
        recent_value=0.100,
        recent_sample_size=25,
        recent_evidence_multiplier=0.20,
    )
    no_recent = estimate_true_talent(
        prior=0.320,
        prior_sample_size=200,
        season_value=0.360,
        season_sample_size=500,
        recent_value=None,
    )
    assert stable.weights["recent"] < 0.01
    assert abs(stable.value - no_recent.value) < 0.003
    assert stable.value > 0.345


def test_platoon_split_regresses_to_overall_talent() -> None:
    tiny = estimate_platoon_talent(
        overall_talent=0.330, split_value=0.500, split_sample_size=5, regression_sample_size=80
    )
    established = estimate_platoon_talent(
        overall_talent=0.330, split_value=0.500, split_sample_size=400, regression_sample_size=80
    )
    missing = estimate_platoon_talent(
        overall_talent=0.330, split_value=None, split_sample_size=0, regression_sample_size=80
    )
    assert abs(tiny.value - 0.330) < abs(established.value - 0.330)
    assert missing.value == pytest.approx(0.330)


def test_no_streak_or_morale_adjustment_is_present() -> None:
    result = estimate_true_talent(
        prior=0.320, prior_sample_size=100, season_value=0.340, season_sample_size=100
    )
    assert set(result.weights) == {"prior", "season"}
    assert "streak" not in result.method
    assert "morale" not in result.method
