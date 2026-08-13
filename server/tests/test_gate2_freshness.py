from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.features.freshness import (
    DataSource,
    FreshnessStatus,
    SOURCE_THRESHOLDS,
    assess_freshness,
    hard_blockers,
)
from app.features.lifecycle import LifecycleStage


NOW = datetime(2026, 8, 12, 18, 0, tzinfo=UTC)


@pytest.mark.parametrize("source", list(DataSource))
def test_source_threshold_boundaries_are_deterministic(source: DataSource) -> None:
    threshold = SOURCE_THRESHOLDS[source]
    assert assess_freshness(source, NOW - threshold.fresh_for, NOW).status == FreshnessStatus.FRESH
    assert (
        assess_freshness(source, NOW - threshold.fresh_for - timedelta(microseconds=1), NOW).status
        == FreshnessStatus.STALE
    )
    assert assess_freshness(source, NOW - threshold.stale_for, NOW).status == FreshnessStatus.STALE
    assert (
        assess_freshness(source, NOW - threshold.stale_for - timedelta(microseconds=1), NOW).status
        == FreshnessStatus.CRITICAL
    )


def test_missing_and_disabled_are_distinct() -> None:
    assert assess_freshness(DataSource.MLB_SCHEDULE, None, NOW).status == FreshnessStatus.MISSING
    assert (
        assess_freshness(DataSource.STATCAST, None, NOW, enabled=False).status
        == FreshnessStatus.NOT_APPLICABLE
    )


def test_disabled_statcast_is_not_a_hard_blocker() -> None:
    assessments = {
        DataSource.MLB_SCHEDULE: FreshnessStatus.FRESH,
        DataSource.MLB_TEAM_PLAYER: FreshnessStatus.FRESH,
        DataSource.OPEN_METEO: FreshnessStatus.FRESH,
        DataSource.STATCAST: FreshnessStatus.NOT_APPLICABLE,
    }
    assert hard_blockers(LifecycleStage.T_MINUS_10M, assessments) == ()


def test_weather_becomes_stage_specific_late_blocker() -> None:
    assessments = {
        DataSource.MLB_SCHEDULE: FreshnessStatus.FRESH,
        DataSource.MLB_TEAM_PLAYER: FreshnessStatus.FRESH,
        DataSource.OPEN_METEO: FreshnessStatus.CRITICAL,
    }
    assert hard_blockers(LifecycleStage.T_MINUS_6H, assessments) == ()
    assert hard_blockers(LifecycleStage.T_MINUS_3H, assessments) == (DataSource.OPEN_METEO,)


def test_market_claim_requires_noncritical_odds_but_forecast_does_not() -> None:
    assessments = {
        DataSource.MLB_SCHEDULE: FreshnessStatus.FRESH,
        DataSource.MLB_TEAM_PLAYER: FreshnessStatus.FRESH,
        DataSource.OPEN_METEO: FreshnessStatus.FRESH,
        DataSource.THE_ODDS_API: FreshnessStatus.CRITICAL,
    }
    assert hard_blockers(LifecycleStage.T_MINUS_30M, assessments) == ()
    assert hard_blockers(
        LifecycleStage.T_MINUS_30M,
        assessments,
        require_market_comparison=True,
    ) == (DataSource.THE_ODDS_API,)


def test_stale_is_visible_but_not_a_hard_blocker() -> None:
    assessments = {
        DataSource.MLB_SCHEDULE: FreshnessStatus.STALE,
        DataSource.MLB_TEAM_PLAYER: FreshnessStatus.STALE,
    }
    assert hard_blockers(LifecycleStage.MORNING, assessments) == ()


def test_missing_required_assessment_fails_closed() -> None:
    assert hard_blockers(LifecycleStage.MORNING, {}) == (
        DataSource.MLB_SCHEDULE,
        DataSource.MLB_TEAM_PLAYER,
    )


def test_naive_freshness_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        assess_freshness(DataSource.MLB_SCHEDULE, NOW, NOW.replace(tzinfo=None))
