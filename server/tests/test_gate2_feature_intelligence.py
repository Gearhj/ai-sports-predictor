from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.features.bullpen import RelieverWorkload, aggregate_bullpen_availability, estimate_reliever_availability
from app.features.context import ParkFactorReference, select_park_factor
from app.features.lineup import LineupEntry, LineupSnapshot, aggregate_lineup, select_lineup_snapshot
from app.features.statcast import PlayerStatcastObservation, aggregate_statcast, normalize_percentage
from app.forecasting.ensemble import GameFeatures, MLBForecastEnsemble


def test_lineup_is_pa_and_order_weighted_and_exposes_missing_slots() -> None:
    result = aggregate_lineup(
        [
            LineupEntry("lead", 1, 0.380, 5),
            LineupEntry("ninth", 9, 0.280, 4),
            LineupEntry("uncertain", 2, 0.350, 5, participation_probability=0.5, replacement_value=None),
        ]
    )
    assert result.value is not None and 0.28 < result.value < 0.38
    assert result.weighted_coverage < 1
    assert result.slot_coverage == pytest.approx(3 / 9)
    assert result.missing_slots == (3, 4, 5, 6, 7, 8)
    assert result.uncertain_slots == (2,)


def test_lineup_uncertainty_uses_only_explicit_replacement_value() -> None:
    without = aggregate_lineup([LineupEntry("p1", 1, 0.400, 5, 0.5)])
    with_replacement = aggregate_lineup(
        [LineupEntry("p1", 1, 0.400, 5, 0.5, replacement_value=0.200)]
    )
    assert without.value == pytest.approx(0.400)
    assert without.weighted_coverage == pytest.approx(0.5)
    assert with_replacement.value == pytest.approx(0.300)
    assert with_replacement.replacement_slots == (1,)


def test_confirmed_lineup_supersedes_newer_projected_but_late_is_ineligible() -> None:
    cutoff = datetime(2026, 8, 12, 20, tzinfo=UTC)
    projected = LineupSnapshot((), cutoff - timedelta(minutes=10), cutoff - timedelta(minutes=10), "projection", False)
    confirmed = LineupSnapshot((), cutoff - timedelta(hours=1), cutoff - timedelta(hours=1), "club", True)
    late = LineupSnapshot((), cutoff - timedelta(minutes=2), cutoff + timedelta(minutes=1), "late-club", True)
    assert select_lineup_snapshot([projected, confirmed, late], prediction_time=cutoff) == confirmed


def test_bullpen_availability_is_monotonic_and_missing_remains_unknown() -> None:
    fresh = estimate_reliever_availability(RelieverWorkload("fresh", 0, 0, 0, 1, 1.0))
    tired = estimate_reliever_availability(RelieverWorkload("tired", 30, 50, 80, 1, 1.0))
    unknown = estimate_reliever_availability(RelieverWorkload("unknown", 0, 0, 0, 1, None))
    assert fresh.availability == 1
    assert tired.availability is not None and tired.availability < fresh.availability
    assert unknown.availability is None
    aggregate = aggregate_bullpen_availability([
        RelieverWorkload("fresh", 0, 0, 0, 3, 1.0),
        RelieverWorkload("unknown", 0, 0, 0, 1, None),
    ])
    assert aggregate.value == 1
    assert aggregate.coverage == pytest.approx(0.75)
    assert aggregate.missing_player_ids == ("unknown",)


def test_statcast_normalizes_percentages_and_aggregates_by_attempts() -> None:
    assert normalize_percentage(7.2) == pytest.approx(0.072)
    assert normalize_percentage(0.072) == pytest.approx(0.072)
    result = aggregate_statcast(
        [
            PlayerStatcastObservation("a", 100, xwoba=0.300, barrel_rate=10, hard_hit_rate=40, average_exit_velocity=90),
            PlayerStatcastObservation("b", 300, xwoba=0.340, barrel_rate=0.06, hard_hit_rate=None, average_exit_velocity=86),
        ],
        expected_attempts=500,
    )
    assert result.xwoba.value == pytest.approx(0.330)
    assert result.barrel_rate.value == pytest.approx(0.07)
    assert result.hard_hit_rate.value == pytest.approx(0.40)
    assert result.hard_hit_rate.coverage == pytest.approx(0.20)
    assert result.xwoba.coverage == pytest.approx(0.80)


def test_statcast_all_missing_metric_stays_null() -> None:
    result = aggregate_statcast([PlayerStatcastObservation("a", 50)], expected_attempts=100)
    assert result.xwoba.value is None
    assert result.xwoba.coverage == 0


def test_park_factor_requires_exact_venue_season_or_explicit_neutral_fallback() -> None:
    references = [ParkFactorReference("fenway", 2026, 1.07, "versioned-reference")]
    exact = select_park_factor(references, venue_id="fenway", season=2026)
    missing = select_park_factor(references, venue_id="fenway", season=2025)
    assert exact.run_factor == pytest.approx(1.07)
    assert not exact.provisional and not exact.neutral_fallback
    assert missing.run_factor == pytest.approx(1.0)
    assert missing.provisional and missing.neutral_fallback
    assert "neutral provisional" in missing.reason


def test_player_first_lineup_talent_changes_operational_run_expectation() -> None:
    ensemble = MLBForecastEnsemble(simulation_draws=500, simulation_seed=17)
    baseline = ensemble.predict(GameFeatures(), data_readiness=80)
    stronger_home_lineup = ensemble.predict(
        GameFeatures(home_lineup_xwoba=0.370, away_lineup_xwoba=0.320),
        data_readiness=80,
    )
    assert stronger_home_lineup.expected_home_runs > baseline.expected_home_runs
    assert stronger_home_lineup.home_win_probability > baseline.home_win_probability


def test_statcast_contact_changes_operational_run_expectation() -> None:
    ensemble = MLBForecastEnsemble(simulation_draws=500, simulation_seed=19)
    baseline = ensemble.predict(GameFeatures(), data_readiness=80)
    stronger_home_contact = ensemble.predict(
        GameFeatures(
            home_statcast_xwoba=0.380,
            home_barrel_rate=0.140,
            home_hard_hit_rate=0.480,
            home_average_exit_velocity=93.0,
        ),
        data_readiness=80,
    )
    assert stronger_home_contact.expected_home_runs > baseline.expected_home_runs
    assert stronger_home_contact.home_win_probability > baseline.home_win_probability


def test_bullpen_availability_changes_opponent_run_expectation() -> None:
    ensemble = MLBForecastEnsemble(simulation_draws=500, simulation_seed=23)
    fully_available = ensemble.predict(
        GameFeatures(away_bullpen_availability=1.0),
        data_readiness=80,
    )
    depleted = ensemble.predict(
        GameFeatures(away_bullpen_availability=0.0),
        data_readiness=80,
    )
    assert depleted.expected_home_runs > fully_available.expected_home_runs
    assert depleted.home_win_probability > fully_available.home_win_probability


def test_observed_rest_and_travel_change_operational_run_expectation() -> None:
    ensemble = MLBForecastEnsemble(simulation_draws=500, simulation_seed=29)
    rested = ensemble.predict(
        GameFeatures(home_days_since_previous_game=2.0, home_travel_distance_miles=0.0),
        data_readiness=80,
    )
    short_rest_travel = ensemble.predict(
        GameFeatures(home_days_since_previous_game=0.25, home_travel_distance_miles=1800.0),
        data_readiness=80,
    )
    assert short_rest_travel.expected_home_runs < rested.expected_home_runs
    assert short_rest_travel.home_win_probability < rested.home_win_probability


def test_confidence_is_capped_by_measured_coverage_and_readiness() -> None:
    ensemble = MLBForecastEnsemble(simulation_draws=500, simulation_seed=31)
    sparse = ensemble.predict(
        GameFeatures(feature_completeness=0.20, sample_reliability=0.0),
        data_readiness=80,
    )
    complete = ensemble.predict(
        GameFeatures(feature_completeness=1.0, sample_reliability=1.0),
        data_readiness=80,
    )
    assert sparse.confidence <= 62.0
    assert complete.confidence > sparse.confidence
