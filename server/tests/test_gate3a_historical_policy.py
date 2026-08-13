from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.historical.policy import (
    EvidenceClassification,
    EvidenceTier,
    FamilyEvidence,
    FamilyWeight,
    QualityPolicy,
    ResearchProvenance,
    score_reconstruction_quality,
    tier_for_evidence,
)
from app.historical.temporal import (
    HistoricalEvent,
    deterministic_cutoff,
    strict_rolling_mean,
    strictly_eligible_events,
)


def _quality_policy() -> QualityPolicy:
    return QualityPolicy(
        family_weights=(
            FamilyWeight("team_history", 2.0, critical=True),
            FamilyWeight("lineup", 1.0, critical=True),
            FamilyWeight("weather", 1.0),
        )
    )


def test_t_minus_six_hour_cutoff_is_exact_and_timezone_normalized() -> None:
    first_pitch = datetime(2025, 7, 2, 19, tzinfo=timezone(timedelta(hours=-4)))
    cutoff = deterministic_cutoff(first_pitch)

    assert cutoff == datetime(2025, 7, 2, 17, tzinfo=UTC)
    assert cutoff == deterministic_cutoff(first_pitch)
    with pytest.raises(ValueError, match="timezone-aware"):
        deterministic_cutoff(datetime(2025, 7, 2, 19))


def test_strict_window_excludes_target_boundary_late_future_old_and_forward_only() -> None:
    cutoff = datetime(2025, 7, 2, 12, tzinfo=UTC)
    events = (
        HistoricalEvent("valid", "prior", cutoff - timedelta(hours=2), cutoff, 2.0),
        HistoricalEvent("target", "target", cutoff - timedelta(hours=3), cutoff, 50.0),
        HistoricalEvent("boundary", "prior-2", cutoff, cutoff, 50.0),
        HistoricalEvent("late", "prior-3", cutoff - timedelta(hours=1), cutoff + timedelta(seconds=1), 50.0),
        HistoricalEvent("future", "future", cutoff + timedelta(hours=1), cutoff + timedelta(hours=1), 50.0),
        HistoricalEvent("old", "old", cutoff - timedelta(days=8), cutoff - timedelta(days=8), 50.0),
        HistoricalEvent(
            "forward-only",
            "prior-4",
            cutoff - timedelta(hours=1),
            cutoff,
            50.0,
            EvidenceClassification.FORWARD_ONLY,
        ),
    )

    selected = strictly_eligible_events(
        reversed(events),
        cutoff=cutoff,
        current_game_id="target",
        window=timedelta(days=7),
    )

    assert tuple(item.event_id for item in selected) == ("valid",)
    assert strict_rolling_mean(
        events,
        cutoff=cutoff,
        current_game_id="target",
        window=timedelta(days=7),
    ) == pytest.approx(2.0)


def test_quality_is_zero_to_one_hundred_and_tier_c_is_forward_gold_standard() -> None:
    policy = QualityPolicy(family_weights=(FamilyWeight("history", 1.0, critical=True),))
    historical = score_reconstruction_quality(
        policy,
        [FamilyEvidence("history", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME)],
    )
    prospective = score_reconstruction_quality(
        policy,
        [
            FamilyEvidence(
                "history",
                EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME,
                provenance=ResearchProvenance.LIVE,
            )
        ],
    )

    assert historical.score == 100.0
    assert historical.score_fraction == 1.0
    assert historical.tier is EvidenceTier.A
    assert prospective.score == 100.0
    assert prospective.tier is EvidenceTier.C
    assert tier_for_evidence(
        EvidenceClassification.FORWARD_ONLY,
        ResearchProvenance.HISTORICAL_RECONSTRUCTION,
    ) is EvidenceTier.EXCLUDED
    assert tier_for_evidence(
        EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME,
        ResearchProvenance.DEMO,
    ) is EvidenceTier.EXCLUDED


def test_quality_cannot_increase_after_downgrade_removal_or_less_coverage() -> None:
    policy = _quality_policy()
    complete = (
        FamilyEvidence("team_history", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
        FamilyEvidence("lineup", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
        FamilyEvidence("weather", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
    )
    baseline = score_reconstruction_quality(policy, complete)
    partial = score_reconstruction_quality(
        policy,
        (
            complete[0],
            FamilyEvidence("lineup", EvidenceClassification.PARTIALLY_RECONSTRUCTABLE),
            complete[2],
        ),
    )
    provisional = score_reconstruction_quality(
        policy,
        (complete[0], FamilyEvidence("lineup", complete[1].classification, provisional=True), complete[2]),
    )
    less_coverage = score_reconstruction_quality(
        policy,
        (complete[0], FamilyEvidence("lineup", complete[1].classification, coverage=0.4), complete[2]),
    )
    removed = score_reconstruction_quality(policy, complete[:2])
    invalid = score_reconstruction_quality(
        policy,
        (complete[0], FamilyEvidence("lineup", complete[1].classification, timing_valid=False), complete[2]),
    )

    assert baseline.score == 100.0
    assert all(
        result.score <= baseline.score
        for result in (partial, provisional, less_coverage, removed, invalid)
    )
    assert partial.score == provisional.score
    assert partial.tier is provisional.tier is EvidenceTier.B
    assert removed.score == 75.0
    assert invalid.tier is EvidenceTier.EXCLUDED


def test_neutral_or_forward_only_evidence_never_counts_as_observed_quality() -> None:
    policy = QualityPolicy(family_weights=(FamilyWeight("weather", 1.0, critical=True),))
    neutral = score_reconstruction_quality(
        policy,
        [
            FamilyEvidence(
                "weather",
                EvidenceClassification.PARTIALLY_RECONSTRUCTABLE,
                neutral_fallback=True,
            )
        ],
    )
    forward_only = score_reconstruction_quality(
        policy,
        [FamilyEvidence("weather", EvidenceClassification.FORWARD_ONLY)],
    )

    assert neutral.score == forward_only.score == 0.0
    assert neutral.tier is forward_only.tier is EvidenceTier.EXCLUDED
    assert neutral.timing_risk_families == ("weather",)
