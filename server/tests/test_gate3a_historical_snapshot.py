from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.historical.policy import (
    EvidenceClassification,
    EvidenceTier,
    FamilyEvidence,
    FamilyWeight,
    QualityPolicy,
    ResearchProvenance,
)
from app.historical.snapshot import (
    EvidenceRole,
    HistoricalEvidenceReference,
    HistoricalFeature,
    build_historical_snapshot,
)


FIRST_PITCH = datetime(2025, 7, 2, 18, tzinfo=UTC)
CUTOFF = FIRST_PITCH - timedelta(hours=6)


def _reference(
    evidence_id: str,
    family: str,
    *,
    game_id: str = "prior",
    event_at: datetime = CUTOFF - timedelta(days=1),
    eligible_at: datetime | None = None,
    classification: EvidenceClassification = EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME,
    role: EvidenceRole = EvidenceRole.FEATURE,
) -> HistoricalEvidenceReference:
    return HistoricalEvidenceReference(
        evidence_id=evidence_id,
        family=family,
        source="fixture",
        source_record_id=f"record-{evidence_id}",
        raw_sha256=("a" if evidence_id == "team" else "b") * 64,
        game_id=game_id,
        event_at=event_at,
        eligible_at=eligible_at or event_at + timedelta(minutes=5),
        classification=classification,
        role=role,
    )


def _snapshot(*, reverse: bool = False, policy_version: str = "quality-v1"):
    features = [
        HistoricalFeature("home_team_strength", "team_history", 0.55),
        HistoricalFeature("park_run_factor", "park", 1.02),
    ]
    evidence = [_reference("team", "team_history"), _reference("park", "park", game_id="park-v1")]
    family = [
        FamilyEvidence("team_history", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
        FamilyEvidence("park", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
    ]
    weights = [FamilyWeight("team_history", 2.0, True), FamilyWeight("park", 1.0, True)]
    if reverse:
        features.reverse()
        evidence.reverse()
        family.reverse()
        weights.reverse()
    return build_historical_snapshot(
        game_id="target",
        scheduled_first_pitch=FIRST_PITCH,
        builder_version="builder-v1",
        feature_schema_version="schema-v1",
        feature_version_id="feature-v1",
        features=features,
        evidence=evidence,
        quality_policy=QualityPolicy(tuple(weights), version=policy_version),
        family_evidence=family,
    )


def test_snapshot_checksum_is_order_independent_and_json_serializable() -> None:
    first = _snapshot()
    shuffled = _snapshot(reverse=True)

    assert shuffled.checksum == first.checksum
    assert shuffled.canonical_payload_json == first.canonical_payload_json
    assert first.cutoff == CUTOFF
    assert first.quality.score == 100.0
    assert first.quality.score_fraction == 1.0
    assert first.tier is EvidenceTier.A
    assert first.to_dict()["checksum"] == first.checksum
    assert first.to_dict()["quality"]["score"] == 100.0


def test_snapshot_checksum_binds_quality_policy_and_feature_values() -> None:
    baseline = _snapshot()
    changed_policy = _snapshot(policy_version="quality-v2")
    changed_feature = build_historical_snapshot(
        game_id="target",
        scheduled_first_pitch=FIRST_PITCH,
        builder_version="builder-v1",
        feature_schema_version="schema-v1",
        feature_version_id="feature-v1",
        features=(HistoricalFeature("home_team_strength", "team_history", 0.56),),
        evidence=(_reference("team", "team_history"),),
        quality_policy=QualityPolicy((FamilyWeight("team_history", 1.0, True),), version="quality-v1"),
        family_evidence=(
            FamilyEvidence("team_history", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
        ),
    )

    assert changed_policy.checksum != baseline.checksum
    assert changed_feature.checksum != baseline.checksum


@pytest.mark.parametrize(
    "reference,match",
    [
        (_reference("target", "team_history", game_id="target"), "Target-game events"),
        (
            _reference("outcome", "team_history", game_id="target", role=EvidenceRole.TARGET_OUTCOME),
            "Target outcomes",
        ),
        (
            _reference("boundary", "team_history", event_at=CUTOFF, eligible_at=CUTOFF),
            "strictly before cutoff",
        ),
        (
            _reference(
                "late",
                "team_history",
                event_at=CUTOFF - timedelta(hours=1),
                eligible_at=CUTOFF + timedelta(seconds=1),
            ),
            "not eligible by cutoff",
        ),
    ],
)
def test_snapshot_rejects_target_postcutoff_and_late_evidence(reference, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        build_historical_snapshot(
            game_id="target",
            scheduled_first_pitch=FIRST_PITCH,
            builder_version="builder-v1",
            feature_schema_version="schema-v1",
            feature_version_id="feature-v1",
            features=(HistoricalFeature("strength", "team_history", 0.5),),
            evidence=(reference,),
            quality_policy=QualityPolicy((FamilyWeight("team_history", 1.0, True),)),
            family_evidence=(
                FamilyEvidence("team_history", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
            ),
        )


def test_target_game_structural_schedule_evidence_is_allowed_but_not_outcome() -> None:
    structural = _reference(
        "schedule",
        "game_identity",
        game_id="target",
        role=EvidenceRole.STRUCTURAL,
    )
    snapshot = build_historical_snapshot(
        game_id="target",
        scheduled_first_pitch=FIRST_PITCH,
        builder_version="builder-v1",
        feature_schema_version="schema-v1",
        feature_version_id="feature-v1",
        features=(HistoricalFeature("is_home", "game_identity", True),),
        evidence=(structural,),
        quality_policy=QualityPolicy((FamilyWeight("game_identity", 1.0, True),)),
        family_evidence=(
            FamilyEvidence("game_identity", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
        ),
    )

    assert snapshot.evidence[0].role is EvidenceRole.STRUCTURAL
    assert snapshot.tier is EvidenceTier.A


def test_forward_only_missing_family_is_explicitly_excluded_without_fabricated_value() -> None:
    snapshot = build_historical_snapshot(
        game_id="target",
        scheduled_first_pitch=FIRST_PITCH,
        builder_version="builder-v1",
        feature_schema_version="schema-v1",
        feature_version_id="feature-v1",
        features=(HistoricalFeature("market_probability", "market", None),),
        evidence=(),
        quality_policy=QualityPolicy((FamilyWeight("market", 1.0, True),)),
        family_evidence=(
            FamilyEvidence(
                "market",
                EvidenceClassification.FORWARD_ONLY,
                present=False,
            ),
        ),
    )

    assert snapshot.tier is EvidenceTier.EXCLUDED
    assert snapshot.quality.score == 0.0
    assert snapshot.missing_fields == ("market_probability",)
    assert snapshot.unavailable_families == ("market",)


def test_snapshot_cannot_mix_demo_live_or_backtest_provenance() -> None:
    for provenance in (ResearchProvenance.LIVE, ResearchProvenance.DEMO, ResearchProvenance.BACKTEST):
        with pytest.raises(ValueError, match="HISTORICAL_RECONSTRUCTION"):
            build_historical_snapshot(
                game_id="target",
                scheduled_first_pitch=FIRST_PITCH,
                builder_version="builder-v1",
                feature_schema_version="schema-v1",
                feature_version_id="feature-v1",
                features=(HistoricalFeature("strength", "team_history", 0.5),),
                evidence=(_reference("team", "team_history"),),
                quality_policy=QualityPolicy((FamilyWeight("team_history", 1.0, True),)),
                family_evidence=(
                    FamilyEvidence("team_history", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
                ),
                provenance=provenance,
            )


def test_family_cannot_claim_stronger_tier_than_partial_evidence() -> None:
    partial = _reference(
        "team",
        "lineup",
        classification=EvidenceClassification.PARTIALLY_RECONSTRUCTABLE,
    )
    with pytest.raises(ValueError, match="stronger than its evidence"):
        build_historical_snapshot(
            game_id="target",
            scheduled_first_pitch=FIRST_PITCH,
            builder_version="builder-v1",
            feature_schema_version="schema-v1",
            feature_version_id="feature-v1",
            features=(HistoricalFeature("lineup_value", "lineup", 0.5),),
            evidence=(partial,),
            quality_policy=QualityPolicy((FamilyWeight("lineup", 1.0, True),)),
            family_evidence=(
                FamilyEvidence("lineup", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
            ),
        )
