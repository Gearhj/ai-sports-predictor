from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class ReadinessStatus(StrEnum):
    READY = "ready"
    STALE = "stale"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


DEFAULT_WEIGHTS = {
    "schedule": 10.0,
    "probable_pitchers": 15.0,
    "pitcher_confirmation": 10.0,
    "team_stats": 15.0,
    "statcast": 10.0,
    "odds": 15.0,
    "sportsbook_count": 5.0,
    "weather": 10.0,
    "feature_completeness": 10.0,
}

# Gate 2 keeps market and Statcast evidence visible without making either a
# prerequisite for a baseball forecast. Dynamic critical fields are supplied
# by the forecast lifecycle at evaluation time.
GATE2_WEIGHTS = {
    "schedule": 10.0,
    "probable_pitchers": 10.0,
    "pitcher_confirmation": 5.0,
    "lineups": 10.0,
    "roster": 5.0,
    "team_stats": 15.0,
    "statcast": 5.0,
    "bullpen": 5.0,
    "odds": 10.0,
    "sportsbook_count": 5.0,
    "weather": 10.0,
    "feature_completeness": 10.0,
}

STATUS_FACTOR = {
    ReadinessStatus.READY: 1.0,
    ReadinessStatus.STALE: 0.5,
    ReadinessStatus.MISSING: 0.0,
    ReadinessStatus.NOT_APPLICABLE: 1.0,
}


@dataclass(frozen=True)
class ReadinessComponent:
    key: str
    label: str
    status: ReadinessStatus
    weight: float
    score: float
    detail: str
    critical: bool = False
    #: Why this component is not READY, as a stable machine-readable code.
    #: None when the component is ready. It never softens the status: a
    #: component with no registered source stays MISSING and scores zero, but
    #: it now says WHY instead of being indistinguishable from evidence that
    #: merely arrived late.
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class ReadinessResult:
    score: float
    components: tuple[ReadinessComponent, ...]
    critical_missing: tuple[str, ...]
    ready_to_publish: bool


def calculate_readiness(
    statuses: Mapping[str, ReadinessStatus | str],
    details: Mapping[str, str] | None = None,
    *,
    reasons: Mapping[str, str] | None = None,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
    critical: frozenset[str] = frozenset({"schedule", "probable_pitchers", "team_stats", "feature_completeness"}),
    minimum_publish_score: float = 55.0,
    block_stale_critical: bool = False,
) -> ReadinessResult:
    details = details or {}
    reasons = reasons or {}
    components: list[ReadinessComponent] = []
    numerator = denominator = 0.0
    missing: list[str] = []
    for key, weight in weights.items():
        status = ReadinessStatus(statuses.get(key, ReadinessStatus.MISSING))
        component_score = weight * STATUS_FACTOR[status]
        components.append(
            ReadinessComponent(
                key=key,
                label=key.replace("_", " ").title(),
                status=status,
                weight=weight,
                score=component_score,
                detail=details.get(key, _default_detail(key, status)),
                critical=key in critical,
                unavailable_reason=(
                    None if status is ReadinessStatus.READY
                    else reasons.get(key, _default_reason(status))
                ),
            )
        )
        numerator += component_score
        denominator += weight
        if key in critical and (
            status == ReadinessStatus.MISSING
            or (block_stale_critical and status == ReadinessStatus.STALE)
        ):
            missing.append(key)
    score = round(100.0 * numerator / denominator, 1) if denominator else 0.0
    return ReadinessResult(
        score=score,
        components=tuple(components),
        critical_missing=tuple(missing),
        ready_to_publish=score >= minimum_publish_score and not missing,
    )


def _default_detail(key: str, status: ReadinessStatus) -> str:
    return f"{key.replace('_', ' ').title()}: {status.value.replace('_', ' ')}"


#: Stable reason codes. A caller that knows exactly why a component is
#: unavailable supplies its own; these are the honest fallbacks.
REASON_NO_SOURCE_EVIDENCE = "NO_SOURCE_EVIDENCE_REGISTERED"
REASON_EVIDENCE_STALE = "EVIDENCE_STALE"
REASON_EVIDENCE_MISSING = "EVIDENCE_MISSING"
REASON_COVERAGE_BELOW_THRESHOLD = "MEASURED_COVERAGE_BELOW_THRESHOLD"


def _default_reason(status: ReadinessStatus) -> str | None:
    if status is ReadinessStatus.STALE:
        return REASON_EVIDENCE_STALE
    if status is ReadinessStatus.MISSING:
        return REASON_EVIDENCE_MISSING
    return None
