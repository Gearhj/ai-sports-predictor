from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.historical.materialization import (
    HistoricalMaterializationError,
    persist_historical_snapshot,
)
from app.historical.policy import (
    EvidenceClassification,
    FamilyEvidence,
    FamilyWeight,
    QualityPolicy,
)
from app.historical.snapshot import (
    HistoricalEvidenceReference,
    HistoricalFeature,
    build_historical_snapshot,
)
from app.ingestion import CanonicalNormalizer
from app.models import (
    FeatureVersion,
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


FIRST_PITCH = datetime(2025, 7, 2, 23, tzinfo=UTC)
CUTOFF = FIRST_PITCH - timedelta(hours=6)
COMPUTED_AT = datetime(2026, 8, 14, 12, tzinfo=UTC)


def _graph(session, *, source_mode: str = "LIVE", source_tier: str = "A"):
    _, league, season, feature_version = CanonicalNormalizer(session).ensure_core(2025)
    home = Team(league_id=league.id, name="Materialization Home", abbreviation="MTH")
    away = Team(league_id=league.id, name="Materialization Away", abbreviation="MTA")
    session.add_all([home, away])
    session.flush()
    game = Game(
        league_id=league.id,
        season_id=season.id,
        starts_at=FIRST_PITCH,
        status=GameStatus.FINAL,
        home_score=5,
        away_score=3,
        final_at=FIRST_PITCH + timedelta(hours=3),
        source_mode="LIVE",
    )
    run = IngestionRun(
        source="historical-fixture",
        job_name="materialization-test",
        status=RunStatus.SUCCEEDED,
        started_at=COMPUTED_AT - timedelta(minutes=2),
        completed_at=COMPUTED_AT - timedelta(minutes=1),
        records_processed=1,
    )
    session.add_all([game, run])
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    source = HistoricalSourceVersion(
        source_key="team-history",
        dataset_version="2025-v1",
        provider_name="fixture",
        source_mode=source_mode,
        source_classification=("OFFICIAL_PRIMARY" if source_mode == "LIVE" else "SYNTHETIC"),
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        quality_tier=source_tier,
        training_eligible=source_mode == "LIVE" and source_tier in {"A", "B"},
        evaluation_eligible=source_mode == "LIVE" and source_tier in {"A", "B"},
        availability_policy={"kind": "fixture"},
        policy_version="source-policy-v1",
        effective_from=datetime(2025, 1, 1, tzinfo=UTC),
        identity_checksum="1" * 64,
    )
    session.add(source)
    session.flush()
    checkpoint = HistoryBackfillCheckpoint(
        ingestion_run_id=run.id,
        source_version_id=source.id,
        stream_key="fixture:2025",
        sequence=0,
        season=2025,
        event_start_at=FIRST_PITCH,
        event_end_at=FIRST_PITCH,
        query_fingerprint="2" * 64,
        status="SUCCEEDED",
        requested_at=COMPUTED_AT - timedelta(minutes=2),
        completed_at=COMPUTED_AT - timedelta(minutes=1),
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
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="3" * 64,
    )
    session.add(checkpoint)
    session.flush()
    outcome = GameFinalOutcome(
        game_id=game.id,
        season_id=season.id,
        home_team_id=home.id,
        away_team_id=away.id,
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        scheduled_at=FIRST_PITCH,
        final_at=FIRST_PITCH + timedelta(hours=3),
        home_score=5,
        away_score=3,
        outcome_status="FINAL",
        revision=1,
        official=True,
        source_record_id="game-final",
        retrieved_at=COMPUTED_AT - timedelta(minutes=1),
        label_available_at=FIRST_PITCH + timedelta(hours=3, minutes=5),
        availability_basis="fixture",
        payload_checksum="4" * 64,
        source_mode=source_mode,
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="5" * 64,
    )
    prior_event_at = CUTOFF - timedelta(days=1)
    prior_game = Game(
        league_id=league.id,
        season_id=season.id,
        starts_at=prior_event_at - timedelta(hours=3),
        status=GameStatus.FINAL,
        home_score=4,
        away_score=2,
        final_at=prior_event_at,
        source_mode="LIVE",
    )
    session.add_all([outcome, prior_game])
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
        final_at=prior_event_at,
        home_score=4,
        away_score=2,
        outcome_status="FINAL",
        revision=1,
        official=True,
        source_record_id="prior-game-final",
        retrieved_at=COMPUTED_AT - timedelta(minutes=1),
        label_available_at=prior_event_at + timedelta(minutes=5),
        availability_basis="fixture",
        payload_checksum="6" * 64,
        source_mode=source_mode,
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="7" * 64,
    )
    session.add(prior_outcome)
    session.flush()
    team_fact = TeamGameHistoryObservation(
        game_id=prior_game.id,
        final_outcome_id=prior_outcome.id,
        season_id=season.id,
        team_id=home.id,
        opponent_team_id=away.id,
        is_home=True,
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        event_at=prior_event_at,
        evidence_end_at=prior_event_at,
        retrieved_at=COMPUTED_AT - timedelta(minutes=1),
        eligible_at=prior_event_at + timedelta(minutes=5),
        availability_basis="fixture",
        evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
        quality_tier="A",
        source_record_id="game-prior-team",
        stat_schema_version="fixture-v1",
        values={"strength": 0.55},
        payload_checksum="a" * 64,
        source_mode=source_mode,
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="8" * 64,
    )
    rating_fact = TeamGameHistoryObservation(
        game_id=prior_game.id,
        final_outcome_id=prior_outcome.id,
        season_id=season.id,
        team_id=away.id,
        opponent_team_id=home.id,
        is_home=False,
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        event_at=prior_event_at - timedelta(hours=1),
        evidence_end_at=prior_event_at - timedelta(hours=1),
        retrieved_at=COMPUTED_AT - timedelta(minutes=1),
        eligible_at=prior_event_at - timedelta(hours=1) + timedelta(minutes=5),
        availability_basis="fixture",
        evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
        quality_tier="A",
        source_record_id="rating-prior",
        stat_schema_version="fixture-v1",
        values={"rating": 1510.0},
        payload_checksum="b" * 64,
        source_mode=source_mode,
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="9" * 64,
    )
    session.add_all([team_fact, rating_fact])
    session.commit()
    return game, feature_version, source, checkpoint, outcome, team_fact, rating_fact


def _pure_snapshot(
    game: Game,
    feature_version: FeatureVersion,
    canonical_evidence,
    *,
    evidence_value: float = 0.55,
    classification: EvidenceClassification = EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME,
    event_at: datetime = CUTOFF - timedelta(days=1),
    reverse: bool = False,
):
    team_fact, rating_fact = canonical_evidence
    evidence = [
        HistoricalEvidenceReference(
            evidence_id=str(team_fact.id),
            family="team_history",
            source="team-history",
            source_record_id=team_fact.source_record_id,
            raw_sha256=team_fact.payload_checksum,
            game_id=str(team_fact.game_id),
            event_at=event_at,
            eligible_at=event_at + timedelta(minutes=5),
            classification=classification,
        ),
        HistoricalEvidenceReference(
            evidence_id=str(rating_fact.id),
            family="rating",
            source="team-history",
            source_record_id=rating_fact.source_record_id,
            raw_sha256=rating_fact.payload_checksum,
            game_id=str(rating_fact.game_id),
            event_at=event_at - timedelta(hours=1),
            eligible_at=event_at - timedelta(hours=1) + timedelta(minutes=5),
            classification=EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME,
        ),
    ]
    features = [
        HistoricalFeature("home_team_strength", "team_history", evidence_value),
        HistoricalFeature("home_rating", "rating", 1510.0),
    ]
    family = [
        FamilyEvidence("team_history", classification),
        FamilyEvidence("rating", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
    ]
    weights = [FamilyWeight("team_history", 1.0, True), FamilyWeight("rating", 1.0, True)]
    if reverse:
        evidence.reverse()
        features.reverse()
        family.reverse()
        weights.reverse()
    return build_historical_snapshot(
        game_id=str(game.id),
        scheduled_first_pitch=FIRST_PITCH,
        builder_version="builder-v1",
        feature_schema_version="mlb-historical-v1",
        feature_version_id=str(feature_version.id),
        features=features,
        evidence=evidence,
        quality_policy=QualityPolicy(tuple(weights), version="quality-v1"),
        family_evidence=family,
    )


def _persist(session, graph, snapshot, **overrides):
    game, feature_version, source, checkpoint, outcome = graph[:5]
    arguments = {
        "game": game,
        "feature_version": feature_version,
        "checkpoint": checkpoint,
        "historical_snapshot": snapshot,
        "source_versions": (source,),
        "final_outcome": outcome,
        "computed_at": COMPUTED_AT,
        "code_revision": "test-revision",
    }
    arguments.update(overrides)
    return persist_historical_snapshot(session, **arguments)


def test_persistence_is_idempotent_and_does_not_create_forecast_or_operational_refs(session) -> None:
    graph = _graph(session)
    snapshot = _pure_snapshot(graph[0], graph[1], graph[5:])

    first = _persist(session, graph, snapshot)
    session.commit()
    second = _persist(session, graph, snapshot, computed_at=COMPUTED_AT + timedelta(hours=1))

    assert second.reused_feature_snapshot and second.reused_materialization
    assert second.feature_snapshot.id == first.feature_snapshot.id
    assert second.materialization.id == first.materialization.id
    assert session.scalar(select(func.count()).select_from(GameFeatureSnapshot)) == 1
    assert session.scalar(select(func.count()).select_from(HistoricalFeatureMaterialization)) == 1
    assert session.scalar(select(func.count()).select_from(Forecast)) == 0
    persisted = second.feature_snapshot
    assert persisted.data_as_of.replace(tzinfo=UTC) == CUTOFF
    assert persisted.generated_at.replace(tzinfo=UTC) == COMPUTED_AT
    assert persisted.frozen_at.replace(tzinfo=UTC) == COMPUTED_AT
    assert persisted.game_state_observation_id is None
    assert persisted.market_snapshot_ids == []
    assert second.materialization.quality_score == 100.0
    assert second.materialization.source_mode == "LIVE"
    assert second.materialization.temporal_semantics == "RETROSPECTIVE_RECONSTRUCTION"


def test_evidence_reordering_reuses_semantic_snapshot(session) -> None:
    graph = _graph(session)
    first_snapshot = _pure_snapshot(graph[0], graph[1], graph[5:])
    reordered = _pure_snapshot(graph[0], graph[1], graph[5:], reverse=True)
    assert reordered.checksum == first_snapshot.checksum

    first = _persist(session, graph, first_snapshot)
    session.commit()
    second = _persist(session, graph, reordered)

    assert second.materialization.id == first.materialization.id
    assert second.feature_snapshot.id == first.feature_snapshot.id


def test_target_outcome_perturbation_cannot_change_feature_snapshot_semantics(session) -> None:
    graph = _graph(session)
    snapshot = _pure_snapshot(graph[0], graph[1], graph[5:])
    result = _persist(session, graph, snapshot)
    session.commit()
    original_checksum = result.feature_snapshot.checksum
    original_values = dict(result.feature_snapshot.feature_values)

    perturbed_target = {"home_score": 99, "away_score": 0}
    rebuilt = _pure_snapshot(graph[0], graph[1], graph[5:])
    assert perturbed_target != {
        "home_score": graph[4].home_score,
        "away_score": graph[4].away_score,
    }
    assert rebuilt.checksum == snapshot.checksum
    assert result.feature_snapshot.bayesian_metadata["historical_reconstruction"][
        "pure_snapshot_checksum"
    ] == snapshot.checksum
    assert result.feature_snapshot.checksum == original_checksum
    assert result.feature_snapshot.feature_values == original_values
    assert "home_score" not in result.feature_snapshot.feature_values


def test_downgraded_or_removed_evidence_cannot_improve_and_collides_fail_closed(session) -> None:
    graph = _graph(session)
    baseline = _pure_snapshot(graph[0], graph[1], graph[5:])
    downgraded = _pure_snapshot(
        graph[0],
        graph[1],
        graph[5:],
        classification=EvidenceClassification.PARTIALLY_RECONSTRUCTABLE,
    )
    assert downgraded.quality.score <= baseline.quality.score
    assert downgraded.tier.value == "B"
    removed = build_historical_snapshot(
        game_id=str(graph[0].id),
        scheduled_first_pitch=FIRST_PITCH,
        builder_version="builder-v1",
        feature_schema_version="mlb-historical-v1",
        feature_version_id=str(graph[1].id),
        features=(HistoricalFeature("home_rating", "rating", 1510.0),),
        evidence=baseline.evidence[1:],
        quality_policy=QualityPolicy(
            (
                FamilyWeight("team_history", 1.0, True),
                FamilyWeight("rating", 1.0, True),
            ),
            version="quality-v1",
        ),
        family_evidence=(
            FamilyEvidence("rating", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
        ),
    )
    assert removed.quality.score <= baseline.quality.score
    assert removed.tier.value == "EXCLUDED"
    _persist(session, graph, baseline)
    session.commit()

    with pytest.raises(HistoricalMaterializationError, match="classification mismatch"):
        _persist(session, graph, downgraded)
    with pytest.raises(HistoricalMaterializationError, match="EXCLUDED"):
        _persist(session, graph, removed)


def test_future_evidence_is_rejected_before_persistence(session) -> None:
    graph = _graph(session)
    with pytest.raises(ValueError, match="strictly before cutoff"):
        _pure_snapshot(graph[0], graph[1], graph[5:], event_at=CUTOFF)
    assert session.scalar(select(func.count()).select_from(GameFeatureSnapshot)) == 0


def test_nonexistent_canonical_evidence_uuid_is_rejected(session) -> None:
    graph = _graph(session)
    baseline = _pure_snapshot(graph[0], graph[1], graph[5:])
    fictitious = replace(baseline.evidence[0], evidence_id=str(uuid4()))
    snapshot = build_historical_snapshot(
        game_id=str(graph[0].id),
        scheduled_first_pitch=FIRST_PITCH,
        builder_version="builder-v1",
        feature_schema_version="mlb-historical-v1",
        feature_version_id=str(graph[1].id),
        features=baseline.features,
        evidence=(fictitious, baseline.evidence[1]),
        quality_policy=QualityPolicy(
            (FamilyWeight("team_history", 1.0, True), FamilyWeight("rating", 1.0, True)),
            version="quality-v1",
        ),
        family_evidence=(
            FamilyEvidence("team_history", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
            FamilyEvidence("rating", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
        ),
    )

    with pytest.raises(HistoricalMaterializationError, match="nonexistent canonical history row"):
        _persist(session, graph, snapshot)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("source_record_id", "forged-record", "source record mismatch"),
        ("raw_sha256", "f" * 64, "payload checksum mismatch"),
    ],
)
def test_canonical_evidence_metadata_mismatch_is_rejected(session, field, value, match) -> None:
    graph = _graph(session)
    baseline = _pure_snapshot(graph[0], graph[1], graph[5:])
    forged = replace(baseline.evidence[0], **{field: value})
    snapshot = build_historical_snapshot(
        game_id=str(graph[0].id),
        scheduled_first_pitch=FIRST_PITCH,
        builder_version="builder-v1",
        feature_schema_version="mlb-historical-v1",
        feature_version_id=str(graph[1].id),
        features=baseline.features,
        evidence=(forged, baseline.evidence[1]),
        quality_policy=QualityPolicy(
            (FamilyWeight("team_history", 1.0, True), FamilyWeight("rating", 1.0, True)),
            version="quality-v1",
        ),
        family_evidence=(
            FamilyEvidence("team_history", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
            FamilyEvidence("rating", EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME),
        ),
    )

    with pytest.raises(HistoricalMaterializationError, match=match):
        _persist(session, graph, snapshot)


@pytest.mark.parametrize(
    "source_mode,source_tier,match",
    [
        ("DEMO", "EXCLUDED", "non-LIVE"),
        ("LIVE", "C", "Tier C/EXCLUDED"),
        ("LIVE", "EXCLUDED", "Tier C/EXCLUDED"),
    ],
)
def test_source_isolation_and_ineligible_tiers_fail_closed(
    session,
    source_mode: str,
    source_tier: str,
    match: str,
) -> None:
    graph = _graph(session, source_mode=source_mode, source_tier=source_tier)
    snapshot = _pure_snapshot(graph[0], graph[1], graph[5:])

    with pytest.raises(HistoricalMaterializationError, match=match):
        _persist(session, graph, snapshot)


def test_checksum_tampering_and_non_t_minus_six_fail_closed(session) -> None:
    graph = _graph(session)
    snapshot = _pure_snapshot(graph[0], graph[1], graph[5:])
    tampered = replace(snapshot, checksum="f" * 64)
    bad_cutoff = replace(snapshot, cutoff=CUTOFF + timedelta(hours=1))

    with pytest.raises(HistoricalMaterializationError, match="checksum"):
        _persist(session, graph, tampered)
    with pytest.raises(HistoricalMaterializationError, match="T-6h"):
        _persist(session, graph, bad_cutoff)


def test_outcome_correction_never_rebinds_or_duplicates_target_independent_snapshot(session) -> None:
    graph = _graph(session)
    snapshot = _pure_snapshot(graph[0], graph[1], graph[5:])
    first = _persist(session, graph, snapshot)
    session.commit()
    old_outcome = graph[4]
    corrected = GameFinalOutcome(
        game_id=old_outcome.game_id,
        season_id=old_outcome.season_id,
        home_team_id=old_outcome.home_team_id,
        away_team_id=old_outcome.away_team_id,
        source_version_id=old_outcome.source_version_id,
        checkpoint_id=old_outcome.checkpoint_id,
        scheduled_at=old_outcome.scheduled_at,
        final_at=old_outcome.final_at,
        home_score=4,
        away_score=3,
        outcome_status="FINAL",
        revision=2,
        supersedes_outcome_id=old_outcome.id,
        official=True,
        source_record_id="game-final-correction",
        retrieved_at=COMPUTED_AT,
        label_available_at=old_outcome.label_available_at,
        availability_basis="official-correction",
        payload_checksum="c" * 64,
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="d" * 64,
    )
    session.add(corrected)
    session.commit()

    with pytest.raises(HistoricalMaterializationError, match="latest governed revision"):
        _persist(session, graph, snapshot)
    with pytest.raises(HistoricalMaterializationError, match="one-to-one"):
        _persist(
            session,
            graph,
            snapshot,
            final_outcome=corrected,
            computed_at=COMPUTED_AT + timedelta(minutes=1),
        )

    assert session.scalar(select(func.count()).select_from(GameFeatureSnapshot)) == 1
    assert session.scalar(select(func.count()).select_from(HistoricalFeatureMaterialization)) == 1
    persisted = session.get(HistoricalFeatureMaterialization, first.materialization.id)
    assert persisted is not None
    assert persisted.game_final_outcome_id == old_outcome.id
    assert persisted.game_feature_snapshot_id == first.feature_snapshot.id
