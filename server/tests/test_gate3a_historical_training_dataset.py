from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.ingestion import CanonicalNormalizer
from app.models import (
    Forecast,
    Game,
    GameFeatureSnapshot,
    GameFinalOutcome,
    GameStatus,
    GameTeam,
    HistoricalFeatureMaterialization,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    IngestionRun,
    RunStatus,
    Team,
    TeamGameHistoryObservation,
)
from app.training.dataset import GovernedTrainingDatasetBuilder


POLICY_VERSION = "mlb_t_minus_6h_v1"
TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"


def _checksum(value) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _historical_fixture(
    session,
    *,
    source_mode: str = "LIVE",
    eligibility_status: str = "ELIGIBLE",
    quality_tier: str = "A",
    late_label: bool = False,
    late_evidence: bool = False,
    tamper_evidence: bool = False,
    mixed_evidence: bool = False,
    fictitious_canonical_evidence: bool = False,
    market_feature: bool = False,
):
    _, league, season, feature_version = CanonicalNormalizer(session).ensure_core(2025)
    home = Team(league_id=league.id, name="Historical Home", abbreviation="HSH")
    away = Team(league_id=league.id, name="Historical Away", abbreviation="HSA")
    session.add_all([home, away])
    session.flush()

    scheduled_at = datetime(2025, 9, 1, 23, tzinfo=UTC)
    cutoff_at = scheduled_at - timedelta(hours=6)
    training_cutoff = scheduled_at + timedelta(hours=4)
    acquired_at = training_cutoff - timedelta(minutes=15)
    generated_at = training_cutoff + timedelta(days=1)
    game = Game(
        league_id=league.id,
        season_id=season.id,
        starts_at=scheduled_at,
        status=GameStatus.FINAL,
        source_mode="LIVE",
        home_score=5,
        away_score=3,
    )
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])

    source_training_eligible = source_mode == "LIVE"
    source = HistoricalSourceVersion(
        source_key="mlb-history-test",
        dataset_version="2025-v1",
        provider_name="fixture",
        source_mode=source_mode,
        source_classification="OFFICIAL_PRIMARY",
        temporal_semantics=TEMPORAL_SEMANTICS,
        quality_tier=quality_tier,
        training_eligible=source_training_eligible,
        evaluation_eligible=source_training_eligible,
        availability_policy={"basis": "fixture"},
        policy_version="source-policy-v1",
        effective_from=datetime(2025, 1, 1, tzinfo=UTC),
        identity_checksum="1" * 64,
    )
    run = IngestionRun(
        source="fixture",
        job_name="historical-fixture",
        status=RunStatus.SUCCEEDED,
        started_at=acquired_at - timedelta(minutes=1),
        completed_at=acquired_at,
        records_processed=1,
    )
    session.add_all([source, run])
    session.flush()
    checkpoint = HistoryBackfillCheckpoint(
        ingestion_run_id=run.id,
        source_version_id=source.id,
        stream_key="2025-fixture",
        sequence=0,
        season=2025,
        event_start_at=scheduled_at,
        event_end_at=scheduled_at,
        query_fingerprint="2" * 64,
        status="SUCCEEDED",
        requested_at=acquired_at - timedelta(minutes=1),
        completed_at=acquired_at,
        attempt_count=1,
        retry_count=0,
        records_received=1,
        records_accepted=1,
        records_rejected=0,
        duplicate_count=0,
        raw_references=[],
        raw_checksums=[],
        schema_version="fixture-v1",
        code_revision="test",
        source_mode=source_mode,
        temporal_semantics=TEMPORAL_SEMANTICS,
        identity_checksum="3" * 64,
    )
    session.add(checkpoint)
    session.flush()

    final_at = scheduled_at + timedelta(hours=3)
    label_available_at = (
        training_cutoff + timedelta(minutes=1)
        if late_label
        else final_at + timedelta(minutes=5)
    )
    outcome = GameFinalOutcome(
        game_id=game.id,
        season_id=season.id,
        home_team_id=home.id,
        away_team_id=away.id,
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        scheduled_at=scheduled_at,
        final_at=final_at,
        scheduled_innings=9,
        innings_played=9,
        home_score=5,
        away_score=3,
        outcome_status="FINAL",
        revision=1,
        official=True,
        source_record_id="final-game",
        retrieved_at=acquired_at,
        label_available_at=label_available_at,
        availability_basis="fixture",
        payload_checksum="4" * 64,
        source_mode=source_mode,
        temporal_semantics=TEMPORAL_SEMANTICS,
        identity_checksum="5" * 64,
    )
    session.add(outcome)
    session.flush()

    evidence_at = cutoff_at if late_evidence else cutoff_at - timedelta(days=1)
    prior_game = Game(
        league_id=league.id,
        season_id=season.id,
        starts_at=evidence_at - timedelta(hours=3),
        status=GameStatus.FINAL,
        source_mode=source_mode,
        home_score=4,
        away_score=2,
        final_at=evidence_at,
    )
    session.add(prior_game)
    session.flush()
    session.add_all([
        GameTeam(game_id=prior_game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=prior_game.id, team_id=away.id, is_home=False),
    ])
    prior_outcome = GameFinalOutcome(
        game_id=prior_game.id,
        season_id=season.id,
        home_team_id=home.id,
        away_team_id=away.id,
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        scheduled_at=prior_game.starts_at,
        final_at=evidence_at,
        scheduled_innings=9,
        innings_played=9,
        home_score=4,
        away_score=2,
        outcome_status="FINAL",
        revision=1,
        official=True,
        source_record_id="prior-final-game",
        retrieved_at=acquired_at,
        label_available_at=evidence_at,
        availability_basis="fixture",
        payload_checksum="6" * 64,
        source_mode=source_mode,
        temporal_semantics=TEMPORAL_SEMANTICS,
        identity_checksum="a" * 64,
    )
    session.add(prior_outcome)
    session.flush()
    canonical_fact = TeamGameHistoryObservation(
        game_id=prior_game.id,
        final_outcome_id=prior_outcome.id,
        season_id=season.id,
        team_id=home.id,
        opponent_team_id=away.id,
        is_home=True,
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        event_at=evidence_at,
        evidence_end_at=evidence_at,
        retrieved_at=acquired_at,
        eligible_at=evidence_at + (timedelta(0) if late_evidence else timedelta(minutes=5)),
        availability_basis="fixture",
        evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
        quality_tier="A",
        source_record_id="prior-team-game",
        stat_schema_version="fixture-v1",
        values={"runs": 4},
        payload_checksum="b" * 64,
        source_mode=source_mode,
        temporal_semantics=TEMPORAL_SEMANTICS,
        identity_checksum="c" * 64,
    )
    session.add(canonical_fact)
    session.flush()

    source_bundle = [{
        "source_version_id": str(source.id),
        "source_mode": source_mode,
        "temporal_semantics": TEMPORAL_SEMANTICS,
        "quality_tier": quality_tier,
    }]
    evidence_manifest = [{
        "evidence_id": str(canonical_fact.id),
        "canonical_table": "team_game_history_observations",
        "canonical_id": str(canonical_fact.id),
        "canonical_identity_checksum": canonical_fact.identity_checksum,
        "family": "team_history",
        "source": source.source_key,
        "source_record_id": canonical_fact.source_record_id,
        "source_version_id": str(source.id),
        "source_version_identity_checksum": source.identity_checksum,
        "checkpoint_id": str(checkpoint.id),
        "checkpoint_identity_checksum": checkpoint.identity_checksum,
        "source_mode": source_mode,
        "temporal_semantics": TEMPORAL_SEMANTICS,
        "evidence_classification": "RECONSTRUCTABLE_POINT_IN_TIME",
        "quality_tier": "A",
        "role": "FEATURE",
        "game_id": str(prior_game.id),
        "final_outcome_id": str(prior_outcome.id),
        "event_at": evidence_at.isoformat().replace("+00:00", "Z"),
        "evidence_end_at": evidence_at.isoformat().replace("+00:00", "Z"),
        "retrieved_at": acquired_at.isoformat().replace("+00:00", "Z"),
        "eligible_at": (
            evidence_at + (timedelta(0) if late_evidence else timedelta(minutes=5))
        ).isoformat().replace("+00:00", "Z"),
        "availability_basis": "fixture",
        "stat_schema_version": "fixture-v1",
        "raw_sha256": canonical_fact.payload_checksum,
        "payload_checksum": canonical_fact.payload_checksum,
    }]
    if mixed_evidence:
        demo_source = HistoricalSourceVersion(
            source_key="mixed-demo-source",
            dataset_version="2025-v1",
            provider_name="fixture",
            source_mode="DEMO",
            source_classification="SYNTHETIC",
            temporal_semantics=TEMPORAL_SEMANTICS,
            quality_tier="EXCLUDED",
            training_eligible=False,
            evaluation_eligible=False,
            availability_policy={"basis": "fixture"},
            policy_version="source-policy-v1",
            effective_from=datetime(2025, 1, 1, tzinfo=UTC),
            identity_checksum="7" * 64,
        )
        session.add(demo_source)
        session.flush()
        source_bundle.append({
            "source_version_id": str(demo_source.id),
            "source_mode": "DEMO",
            "temporal_semantics": TEMPORAL_SEMANTICS,
            "quality_tier": "EXCLUDED",
        })
        evidence_manifest.append({
            **evidence_manifest[0],
            "evidence_id": "mixed-demo",
            "source_version_id": str(demo_source.id),
            "source_mode": "DEMO",
            "payload_checksum": "7" * 64,
        })
    if fictitious_canonical_evidence:
        forged_id = str(uuid4())
        evidence_manifest[0]["evidence_id"] = forged_id
        evidence_manifest[0]["canonical_id"] = forged_id

    evidence_checksum = _checksum(evidence_manifest)
    if tamper_evidence:
        evidence_manifest[0]["event_at"] = (cutoff_at - timedelta(hours=1)).isoformat()
    feature_values = {
        "home_rating": 1520.0,
        "away_rating": 1480.0,
        "home_runs_per_game": 4.8,
        "away_runs_per_game": 4.2,
    }
    if market_feature:
        feature_values["market_home_probability"] = 0.55
    snapshot = GameFeatureSnapshot(
        game_id=game.id,
        generated_at=generated_at,
        data_as_of=cutoff_at,
        feature_schema_version="mlb_historical_v1",
        feature_version_id=feature_version.id,
        bullpen_observation_ids=[],
        roster_observation_ids=[],
        statcast_observation_ids=[],
        market_snapshot_ids=[],
        source_evidence=evidence_manifest,
        feature_values=feature_values,
        feature_completeness=0.8,
        missing_features=[],
        stale_features=[],
        provisional_features=[],
        checksum="8" * 64,
        source_mode="LIVE",
        frozen_at=generated_at,
    )
    session.add(snapshot)
    session.flush()

    materialization_training_eligible = (
        source_mode == "LIVE"
        and eligibility_status == "ELIGIBLE"
        and quality_tier in {"A", "B"}
    )
    materialization = HistoricalFeatureMaterialization(
        game_id=game.id,
        game_feature_snapshot_id=snapshot.id,
        feature_version_id=feature_version.id,
        game_final_outcome_id=outcome.id,
        checkpoint_id=checkpoint.id,
        scheduled_at=scheduled_at,
        cutoff_at=cutoff_at,
        generated_at=generated_at,
        cutoff_policy_version=POLICY_VERSION,
        cutoff_policy={"offset_hours": 6},
        source_bundle=source_bundle,
        source_bundle_checksum=_checksum(source_bundle),
        evidence_manifest=evidence_manifest,
        evidence_manifest_checksum=evidence_checksum,
        quality_tier=quality_tier,
        quality_score=80.0,
        quality_components={"score": 80.0, "tier": quality_tier},
        materializer_version="materializer-v1",
        code_revision="test",
        eligibility_status=eligibility_status,
        eligibility_reasons=[],
        training_eligible=materialization_training_eligible,
        source_mode=source_mode,
        temporal_semantics=TEMPORAL_SEMANTICS,
        identity_checksum="9" * 64,
    )
    session.add(materialization)
    session.commit()
    return game, snapshot, outcome, materialization, training_cutoff


def test_historical_row_builds_without_forecast_and_live_path_stays_separate(session) -> None:
    game, snapshot, outcome, materialization, training_cutoff = _historical_fixture(session)
    forecast_count = session.scalar(select(func.count()).select_from(Forecast))

    result = GovernedTrainingDatasetBuilder(session).build_historical(
        cutoff_policy_version=POLICY_VERSION,
        training_cutoff=training_cutoff,
    )

    assert result.trainable
    assert result.manifest.row_count == 1
    assert result.manifest.population == "HISTORICAL_RECONSTRUCTION"
    assert result.manifest.temporal_semantics == TEMPORAL_SEMANTICS
    assert result.manifest.cutoff_policy_version == POLICY_VERSION
    assert result.manifest.quality_tiers == ["A"]
    assert result.manifest.quality_score_summary == {
        "minimum": 80.0,
        "maximum": 80.0,
        "mean": 80.0,
    }
    assert all(result.manifest.leakage_checks.values())
    row = result.rows[0]
    assert row.game_id == str(game.id)
    assert row.feature_snapshot_id == str(snapshot.id)
    assert row.historical_materialization_id == str(materialization.id)
    assert row.historical_outcome_id == str(outcome.id)
    assert row.historical_quality_tier == "A"
    assert row.market_home_probability is None
    assert row.closing_home_probability is None
    assert row.market_books == 0
    assert row.home_runs == 5 and row.away_runs == 3 and row.home_win == 1
    assert session.scalar(select(func.count()).select_from(Forecast)) == forecast_count == 0

    prospective = GovernedTrainingDatasetBuilder(session).build(training_cutoff=training_cutoff)
    assert prospective.manifest.population == "PROSPECTIVE_LIVE"
    assert prospective.manifest.row_count == 0
    assert session.scalar(select(func.count()).select_from(Forecast)) == 0


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"source_mode": "DEMO"}, "historical_non_live_source_mode"),
        (
            {"eligibility_status": "INELIGIBLE"},
            "historical_materialization_not_training_eligible",
        ),
        ({"quality_tier": "B"}, "historical_quality_tier_not_allowed"),
        ({"late_label": True}, "historical_label_unavailable_at_training_cutoff"),
        ({"market_feature": True}, "historical_market_or_clv_feature_forbidden"),
    ],
)
def test_historical_path_excludes_noneligible_populations(session, options, reason: str) -> None:
    *_, training_cutoff = _historical_fixture(session, **options)

    result = GovernedTrainingDatasetBuilder(session).build_historical(
        cutoff_policy_version=POLICY_VERSION,
        training_cutoff=training_cutoff,
    )

    assert result.manifest.row_count == 0
    assert result.manifest.exclusion_reasons == {reason: 1}


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"tamper_evidence": True}, "historical_evidence_manifest_checksum_mismatch"),
        ({"mixed_evidence": True}, "historical_source_bundle_mixed_mode"),
        ({"late_evidence": True}, "historical_evidence_after_cutoff_or_mixed"),
        (
            {"fictitious_canonical_evidence": True},
            "historical_canonical_evidence_invalid",
        ),
    ],
)
def test_historical_path_fails_closed_on_tampered_or_mixed_evidence(
    session,
    options,
    reason: str,
) -> None:
    *_, training_cutoff = _historical_fixture(session, **options)

    result = GovernedTrainingDatasetBuilder(session).build_historical(
        cutoff_policy_version=POLICY_VERSION,
        training_cutoff=training_cutoff,
    )

    assert result.manifest.row_count == 0
    assert result.manifest.exclusion_reasons == {reason: 1}


def test_historical_tier_mixing_requires_explicit_authorization(session) -> None:
    *_, training_cutoff = _historical_fixture(session)
    builder = GovernedTrainingDatasetBuilder(session)

    with pytest.raises(ValueError, match="allow_mixed_tiers"):
        builder.build_historical(
            cutoff_policy_version=POLICY_VERSION,
            training_cutoff=training_cutoff,
            quality_tiers=("A", "B"),
        )

    result = builder.build_historical(
        cutoff_policy_version=POLICY_VERSION,
        training_cutoff=training_cutoff,
        quality_tiers=("A", "B"),
        allow_mixed_tiers=True,
    )
    assert result.manifest.row_count == 1
    assert result.manifest.leakage_checks["historical_tier_consistent"]


def test_historical_policy_version_is_required_and_exact(session) -> None:
    *_, training_cutoff = _historical_fixture(session)
    builder = GovernedTrainingDatasetBuilder(session)

    with pytest.raises(ValueError, match="cutoff_policy_version"):
        builder.build_historical(cutoff_policy_version="", training_cutoff=training_cutoff)
    mismatch = builder.build_historical(
        cutoff_policy_version="different-policy",
        training_cutoff=training_cutoff,
    )
    assert mismatch.manifest.row_count == 0
    assert mismatch.manifest.exclusion_reasons == {"historical_cutoff_policy_mismatch": 1}
