from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.features.readiness import ReadinessStatus, calculate_readiness
from app.features.rolling import TimeAwareObservation, eligible_observations, rolling_mean
from app.forecasting.evaluation import brier_score, calibration_bins, chronological_split, ChronologicalRecord, log_loss
from app.forecasting.simulation import simulate_game


def test_simulation_is_reproducible_and_complete() -> None:
    first = simulate_game(4.8, 4.1, simulations=5000, seed=44)
    second = simulate_game(4.8, 4.1, simulations=5000, seed=44)
    assert first == second
    assert first.home_win_probability + first.away_win_probability == pytest.approx(1.0)
    assert first.mean_home_runs > first.mean_away_runs
    assert "8.5" in first.total_probabilities


def test_rolling_features_exclude_future_late_and_current_game() -> None:
    cutoff = datetime(2026, 8, 12, 22, tzinfo=UTC)
    rows = [
        TimeAwareObservation(cutoff - timedelta(days=2), cutoff - timedelta(days=1), 2, "old"),
        TimeAwareObservation(cutoff - timedelta(hours=3), cutoff - timedelta(hours=2), 4, "recent"),
        TimeAwareObservation(cutoff - timedelta(hours=1), cutoff + timedelta(minutes=1), 100, "published-late"),
        TimeAwareObservation(cutoff - timedelta(minutes=1), cutoff - timedelta(minutes=2), 100, "target"),
        TimeAwareObservation(cutoff + timedelta(hours=1), cutoff - timedelta(minutes=2), 100, "future"),
    ]
    eligible = eligible_observations(rows, prediction_time=cutoff, current_game_id="target", window=timedelta(days=7))
    assert [row.game_id for row in eligible] == ["old", "recent"]
    assert rolling_mean(rows, prediction_time=cutoff, current_game_id="target", days=7) == 3


def test_readiness_requires_critical_inputs() -> None:
    statuses = {key: ReadinessStatus.READY for key in [
        "schedule", "probable_pitchers", "pitcher_confirmation", "team_stats", "statcast",
        "odds", "sportsbook_count", "weather", "feature_completeness",
    ]}
    ready = calculate_readiness(statuses)
    assert ready.score == 100
    assert ready.ready_to_publish
    statuses["team_stats"] = ReadinessStatus.MISSING
    incomplete = calculate_readiness(statuses)
    assert not incomplete.ready_to_publish
    assert "team_stats" in incomplete.critical_missing


def test_probability_evaluation_and_chronological_split() -> None:
    probabilities, outcomes = [0.8, 0.3, 0.6, 0.2], [1, 0, 1, 0]
    assert brier_score(probabilities, outcomes) < 0.2
    assert log_loss(probabilities, outcomes) > 0
    assert sum(item.count for item in calibration_bins(probabilities, outcomes, 5)) == 4
    records = [ChronologicalRecord(datetime(2026, 1, day, tzinfo=UTC), {"day": day}) for day in range(1, 21)]
    train, validation, test = chronological_split(reversed(records))
    assert train[-1].event_at < validation[0].event_at < test[0].event_at
