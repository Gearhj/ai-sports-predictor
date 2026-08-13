from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Mapping

from app.features.lifecycle import LifecycleStage


class DataSource(StrEnum):
    MLB_SCHEDULE = "mlb_schedule"
    MLB_TEAM_PLAYER = "mlb_team_player"
    OPEN_METEO = "open_meteo"
    THE_ODDS_API = "the_odds_api"
    STATCAST = "statcast"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    CRITICAL = "critical"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class FreshnessThreshold:
    fresh_for: timedelta
    stale_for: timedelta

    def __post_init__(self) -> None:
        if self.fresh_for < timedelta(0):
            raise ValueError("fresh_for cannot be negative")
        if self.stale_for < self.fresh_for:
            raise ValueError("stale_for must be greater than or equal to fresh_for")


@dataclass(frozen=True)
class FreshnessAssessment:
    source: DataSource
    status: FreshnessStatus
    available_as_of: datetime | None
    evaluated_at: datetime
    age: timedelta | None
    threshold: FreshnessThreshold
    enabled: bool


SOURCE_THRESHOLDS: Mapping[DataSource, FreshnessThreshold] = {
    DataSource.MLB_SCHEDULE: FreshnessThreshold(
        fresh_for=timedelta(hours=1), stale_for=timedelta(hours=6)
    ),
    DataSource.MLB_TEAM_PLAYER: FreshnessThreshold(
        fresh_for=timedelta(hours=24), stale_for=timedelta(hours=36)
    ),
    DataSource.OPEN_METEO: FreshnessThreshold(
        fresh_for=timedelta(hours=3), stale_for=timedelta(hours=6)
    ),
    DataSource.THE_ODDS_API: FreshnessThreshold(
        fresh_for=timedelta(minutes=15), stale_for=timedelta(minutes=30)
    ),
    DataSource.STATCAST: FreshnessThreshold(
        fresh_for=timedelta(hours=24), stale_for=timedelta(hours=72)
    ),
}


_CORE_REQUIRED = frozenset({DataSource.MLB_SCHEDULE, DataSource.MLB_TEAM_PLAYER})
_LATE_REQUIRED = frozenset({*_CORE_REQUIRED, DataSource.OPEN_METEO})

REQUIRED_SOURCES_BY_STAGE: Mapping[LifecycleStage, frozenset[DataSource]] = {
    LifecycleStage.MORNING: _CORE_REQUIRED,
    LifecycleStage.T_MINUS_6H: _CORE_REQUIRED,
    LifecycleStage.T_MINUS_3H: _LATE_REQUIRED,
    LifecycleStage.T_MINUS_60M: _LATE_REQUIRED,
    LifecycleStage.T_MINUS_30M: _LATE_REQUIRED,
    LifecycleStage.T_MINUS_10M: _LATE_REQUIRED,
    LifecycleStage.FINAL: _LATE_REQUIRED,
    LifecycleStage.POSTGAME: frozenset({DataSource.MLB_SCHEDULE}),
}


def assess_freshness(
    source: DataSource,
    available_as_of: datetime | None,
    as_of: datetime,
    *,
    enabled: bool = True,
    threshold: FreshnessThreshold | None = None,
) -> FreshnessAssessment:
    """Classify one source observation using availability, never event time."""

    instant = _aware(as_of)
    selected = threshold or SOURCE_THRESHOLDS[source]
    if not enabled:
        return FreshnessAssessment(
            source=source,
            status=FreshnessStatus.NOT_APPLICABLE,
            available_as_of=None,
            evaluated_at=instant,
            age=None,
            threshold=selected,
            enabled=False,
        )
    if available_as_of is None:
        return FreshnessAssessment(
            source=source,
            status=FreshnessStatus.MISSING,
            available_as_of=None,
            evaluated_at=instant,
            age=None,
            threshold=selected,
            enabled=True,
        )

    available = _aware(available_as_of)
    # A small clock skew must not create a negative age or imply extra freshness.
    age = max(instant - available, timedelta(0))
    if age <= selected.fresh_for:
        status = FreshnessStatus.FRESH
    elif age <= selected.stale_for:
        status = FreshnessStatus.STALE
    else:
        status = FreshnessStatus.CRITICAL
    return FreshnessAssessment(
        source=source,
        status=status,
        available_as_of=available,
        evaluated_at=instant,
        age=age,
        threshold=selected,
        enabled=True,
    )


def hard_blockers(
    stage: LifecycleStage,
    assessments: Mapping[DataSource, FreshnessAssessment | FreshnessStatus],
    *,
    require_market_comparison: bool = False,
) -> tuple[DataSource, ...]:
    """Return critical inputs that prohibit the claim requested at this stage.

    Odds are not required to publish a baseball forecast. They become a hard
    blocker only when the caller intends to publish a current market comparison.
    Statcast is optional in Gate 2 and is therefore never an implicit blocker.
    """

    required = set(REQUIRED_SOURCES_BY_STAGE[stage])
    if require_market_comparison and stage != LifecycleStage.POSTGAME:
        required.add(DataSource.THE_ODDS_API)
    blocked_statuses = {
        FreshnessStatus.CRITICAL,
        FreshnessStatus.MISSING,
        FreshnessStatus.NOT_APPLICABLE,
    }
    blockers = []
    for source in sorted(required, key=lambda item: item.value):
        value = assessments.get(source, FreshnessStatus.MISSING)
        status = value.status if isinstance(value, FreshnessAssessment) else value
        if status in blocked_statuses:
            blockers.append(source)
    return tuple(blockers)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Freshness timestamps must be timezone-aware")
    return value.astimezone(UTC)
