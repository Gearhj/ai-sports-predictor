from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from app.features.readiness import ReadinessStatus, calculate_readiness
from app.features.rolling import TimeAwareObservation, eligible_observations, rolling_mean
from app.forecasting.evaluation import brier_score, calibration_bins, chronological_split, ChronologicalRecord, log_loss
from app.forecasting.simulation import simulate_game
from app.services.read_service import _simulation_over_probability, _snapshot_evidence_timestamp


def test_simulation_is_reproducible_and_complete() -> None:
    first = simulate_game(4.8, 4.1, simulations=5000, seed=44)
    second = simulate_game(4.8, 4.1, simulations=5000, seed=44)
    assert first == second
    assert first.home_win_probability + first.away_win_probability == pytest.approx(1.0)
    assert first.mean_home_runs > first.mean_away_runs
    assert "8.5" in first.total_probabilities


def test_simulation_over_probability_supports_consensus_total_lines() -> None:
    simulation = simulate_game(4.8, 4.1, simulations=5000, seed=44)
    summary = asdict(simulation)

    expected = sum(
        share
        for runs, share in summary["total_runs_distribution"].items()
        if runs == ">20" or (runs not in {"<0", ">20"} and float(runs) > 8.3)
    )

    assert _simulation_over_probability(summary, 8.3) == pytest.approx(expected)
    assert _simulation_over_probability(summary, 8.3) > 0
    assert _simulation_over_probability(summary, 8.5) == pytest.approx(
        summary["total_probabilities"]["8.5"]["over"]
    )


def test_snapshot_evidence_timestamp_uses_only_requested_families() -> None:
    snapshot = type("Snapshot", (), {
        "source_evidence": [
            {"family": "roster", "available_as_of": "2026-08-12T18:00:00Z"},
            {"family": "roster", "available_as_of": "2026-08-12T18:05:00+00:00"},
            {"family": "statcast", "available_as_of": "2026-08-12T19:00:00+00:00"},
        ]
    })()

    assert _snapshot_evidence_timestamp(snapshot, "roster") == datetime(2026, 8, 12, 18, 5, tzinfo=UTC)
    assert _snapshot_evidence_timestamp(snapshot, "bullpen") is None


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
