from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event, func, select, update

from app.data_sources.base import (
    HistoricalBoxscoreDTO,
    HistoricalFinalDTO,
    HistoricalPlayerGameEvidenceDTO,
    HistoricalScheduleGameDTO,
    HistoricalTeamGameEvidenceDTO,
    ScheduleGameDTO,
    TeamDTO,
    VenueDTO,
)
from app.historical.persistence import HistoricalCanonicalPersistenceService
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
from app.historical.reconstruction import (
    HistoricalReconstructionService,
    ReconstructionPopulation,
    build_and_persist_historical_dataset,
    run_historical_reconstruction_dataset,
)
from app.historical.snapshot import (
    HISTORICAL_SNAPSHOT_TYPE,
    EvidenceRole,
    HistoricalEvidenceReference,
    HistoricalFeature,
    build_historical_snapshot,
)
from app.historical.temporal import CUTOFF_POLICY_VERSION
from app.modeling.schema import GATE3_FEATURE_SCHEMA
from app.models import (
    FeatureVersion,
    Forecast,
    Game,
    GameFeatureSnapshot,
    HistoricalFeatureMaterialization,
    GameFinalOutcome,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    ProviderRequestLedger,
    TeamGameHistoryObservation,
    TrainingDatasetManifest,
)
from app.training.dataset import (
    DatasetArtifactStorageError,
    GovernedTrainingDatasetBuilder,
    HISTORICAL_ROW_LINEAGE_VERSION,
)


ACQUIRED = datetime(2026, 8, 14, 12, tzinfo=UTC)


def _schedule(game_id: str, event_date: date, home_score: int, away_score: int):
    first_pitch = datetime.combine(event_date, datetime.min.time(), tzinfo=UTC) + timedelta(hours=23)
    final_at = first_pitch + timedelta(hours=3)
    return HistoricalScheduleGameDTO(
        game=ScheduleGameDTO(
            source_game_id=game_id,
            scheduled_at=first_pitch,
            event_date=event_date,
            status="Final",
            away_team=TeamDTO(source_id="10", name="Away 10", abbreviation="A10"),
            home_team=TeamDTO(source_id="20", name="Home 20", abbreviation="H20"),
            venue=VenueDTO(
                source_id="1",
                name="A1 Park",
                latitude=40.0,
                longitude=-74.0,
            ),
            source_mode="LIVE",
            away_score=away_score,
            home_score=home_score,
            scheduled_innings=9,
        ),
        season=2025,
        official_date=event_date,
        game_type="R",
        abstract_state="Final",
        coded_state="F",
        acquired_at=ACQUIRED,
        final=HistoricalFinalDTO(
            source_game_id=game_id,
            official_date=event_date,
            status="Final",
            abstract_state="Final",
            home_score=home_score,
            away_score=away_score,
            innings_played=9,
            event_completed_at=final_at,
            completion_time_basis="AUTHORITATIVE_FINAL_AT",
            completion_time_source_field="gameData.status.finalAt",
            completion_time_method="fixture authoritative completion",
            completion_time_limitation="provider-defined final timestamp",
            acquired_at=ACQUIRED,
        ),
    )


def _player(
    game_id: str,
    team_id: str,
    player_id: str,
    *,
    starter: bool = False,
    batting_order: int | None = None,
    earned_runs: int = 0,
    target_multiplier: int = 1,
):
    is_home = team_id == "20"
    pitching = None
    if starter or batting_order is None:
        pitching = {
            "earnedRuns": earned_runs * target_multiplier,
            "inningsPitched": "6.0" if starter else "1.0",
            "numberOfPitches": (85 if starter else 18) * target_multiplier,
        }
    batting = (
        {"hits": target_multiplier, "xwoba": 0.31 + target_multiplier * 0.01}
        if batting_order is not None
        else None
    )
    return HistoricalPlayerGameEvidenceDTO(
        source_game_id=game_id,
        source_player_id=player_id,
        source_team_id=team_id,
        full_name=f"Player {player_id}",
        is_home=is_home,
        batting_order=batting_order,
        appeared_as_pitcher=pitching is not None,
        actual_starting_pitcher=starter,
        starter_provenance=(
            "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION" if starter else None
        ),
        lineup_provenance="HISTORICAL_ACTUAL_LINEUP" if batting_order else None,
        position="P" if pitching is not None else "CF",
        batting=batting,
        pitching=pitching,
        acquired_at=ACQUIRED,
    )


def _boxscore(schedule: HistoricalScheduleGameDTO, *, target_multiplier: int):
    game_id = schedule.game.source_game_id
    assert schedule.final is not None
    return HistoricalBoxscoreDTO(
        source_game_id=game_id,
        acquired_at=ACQUIRED,
        teams=[
            HistoricalTeamGameEvidenceDTO(
                source_game_id=game_id,
                source_team_id="20",
                is_home=True,
                team_stats={
                    "batting": {"runs": schedule.final.home_score, "ops": 0.730},
                },
                acquired_at=ACQUIRED,
            ),
            HistoricalTeamGameEvidenceDTO(
                source_game_id=game_id,
                source_team_id="10",
                is_home=False,
                team_stats={
                    "batting": {"runs": schedule.final.away_score, "ops": 0.710},
                },
                acquired_at=ACQUIRED,
            ),
        ],
        players=[
            _player(game_id, "20", "h-starter", starter=True, earned_runs=2, target_multiplier=target_multiplier),
            _player(game_id, "10", "a-starter", starter=True, earned_runs=3, target_multiplier=target_multiplier),
            _player(game_id, "20", "h-reliever", earned_runs=1, target_multiplier=target_multiplier),
            _player(game_id, "10", "a-reliever", earned_runs=0, target_multiplier=target_multiplier),
            _player(game_id, "20", "h-batter", batting_order=1, target_multiplier=target_multiplier),
            _player(game_id, "10", "a-batter", batting_order=1, target_multiplier=target_multiplier),
        ],
    )


def _canonical_history(session):
    prior = _schedule("prior-game", date(2025, 4, 1), 5, 2)
    target = _schedule("target-game", date(2025, 4, 10), 4, 3)
    service = HistoricalCanonicalPersistenceService(session)
    source = service.ensure_a1_source_version(effective_from=ACQUIRED)
    service.persist_chunk(
        schedule_rows=[prior, target],
        boxscores=[
            _boxscore(prior, target_multiplier=1),
            _boxscore(target, target_multiplier=9),
        ],
        source_version=source,
        stream_key="a1-reconstruction-fixture",
        sequence=0,
        query_fingerprint="f" * 64,
        requested_at=ACQUIRED - timedelta(minutes=1),
        completed_at=ACQUIRED,
        code_revision="test",
        attempt_count=1,
        retry_count=0,
        request_accounting={
            "attempts": 1,
            "retries": 0,
            "successes": 1,
            "failed_attempts": 0,
            "operations": {
                "fixture_local": {
                    "attempts": 1,
                    "successes": 1,
                    "failed_attempts": 0,
                },
            },
        },
    )
    return session.scalar(select(Game).where(Game.starts_at == target.game.scheduled_at))


def test_post_cutoff_target_context_is_explicit_tier_b_and_performance_is_forbidden() -> None:
    first_pitch = datetime(2025, 4, 10, 23, tzinfo=UTC)
    context = {
        "context_type": "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION",
        "player_id": "starter-1",
        "performance_values_used": False,
        "publication_time_established": False,
    }
    evidence = HistoricalEvidenceReference(
        evidence_id="context-1",
        family="target_context",
        source="fixture",
        source_record_id="target:starter",
        raw_sha256="a" * 64,
        game_id="target",
        event_at=first_pitch + timedelta(hours=3),
        eligible_at=first_pitch + timedelta(hours=3),
        classification=EvidenceClassification.PARTIALLY_RECONSTRUCTABLE,
        role=EvidenceRole.TARGET_CONTEXT,
        context=context,
    )
    snapshot = build_historical_snapshot(
        game_id="target",
        scheduled_first_pitch=first_pitch,
        builder_version="test",
        feature_schema_version="test",
        feature_version_id="test",
        features=(),
        evidence=(evidence,),
        quality_policy=QualityPolicy((FamilyWeight("target_context", 1, True),)),
        family_evidence=(FamilyEvidence(
            "target_context",
            EvidenceClassification.PARTIALLY_RECONSTRUCTABLE,
            provisional=True,
        ),),
    )
    assert snapshot.tier.value == "B"
    assert snapshot.snapshot_type == HISTORICAL_SNAPSHOT_TYPE

    with pytest.raises(ValueError, match="performance"):
        build_historical_snapshot(
            game_id="target",
            scheduled_first_pitch=first_pitch,
            builder_version="test",
            feature_schema_version="test",
            feature_version_id="test",
            features=(),
            evidence=(HistoricalEvidenceReference(
                **{
                    **evidence.__dict__,
                    "context": {**context, "performance_values_used": True},
                }
            ),),
            quality_policy=QualityPolicy((FamilyWeight("target_context", 1, True),)),
            family_evidence=(FamilyEvidence(
                "target_context",
                EvidenceClassification.PARTIALLY_RECONSTRUCTABLE,
                provisional=True,
            ),),
        )
    with pytest.raises(ValueError, match="Target outcomes"):
        build_historical_snapshot(
            game_id="target",
            scheduled_first_pitch=first_pitch,
            builder_version="test",
            feature_schema_version="test",
            feature_version_id="test",
            features=(),
            evidence=(replace(evidence, role=EvidenceRole.TARGET_OUTCOME),),
            quality_policy=QualityPolicy((FamilyWeight("target_context", 1, True),)),
            family_evidence=(FamilyEvidence(
                "target_context",
                EvidenceClassification.PARTIALLY_RECONSTRUCTABLE,
                provisional=True,
            ),),
        )


def test_canonical_history_materializes_separate_a_b_and_persists_dataset_rows(
    session,
    artifact_root,
) -> None:
    target = _canonical_history(session)
    assert target is not None
    service = HistoricalReconstructionService(session)

    strict = service.materialize_game(
        target,
        population=ReconstructionPopulation.TIER_A_STRICT,
        computed_at=ACQUIRED,
        code_revision="test-a1",
    )
    enhanced = service.materialize_game(
        target,
        population=ReconstructionPopulation.TIER_B_ENHANCED,
        computed_at=ACQUIRED,
        code_revision="test-a1",
    )
    session.commit()

    assert strict.persistence is not None
    assert enhanced.persistence is not None
    assert strict.reconstruction.snapshot is not None
    assert enhanced.reconstruction.snapshot is not None
    assert strict.reconstruction.snapshot.tier.value == "A"
    assert enhanced.reconstruction.snapshot.tier.value == "B"
    assert strict.reconstruction.target_context_rows == 0
    assert enhanced.reconstruction.target_context_rows == 4
    assert strict.persistence.feature_snapshot.feature_version_id != enhanced.persistence.feature_snapshot.feature_version_id
    assert strict.persistence.feature_snapshot.bayesian_metadata["historical_reconstruction"]["snapshot_type"] == HISTORICAL_SNAPSHOT_TYPE
    assert enhanced.persistence.feature_snapshot.provisional_features
    assert "home_score" not in enhanced.persistence.feature_snapshot.feature_values
    assert "away_score" not in enhanced.persistence.feature_snapshot.feature_values
    assert enhanced.persistence.feature_snapshot.feature_values["home_starter_era"] == pytest.approx(3.0)
    assert enhanced.persistence.feature_snapshot.feature_values["home_lineup_talent_coverage"] == 1.0
    assert enhanced.persistence.feature_snapshot.feature_values["home_lineup_xwoba"] == pytest.approx(0.32)
    enhanced_meta = enhanced.reconstruction.snapshot.reconstruction_metadata["feature_derivation"]
    assert enhanced_meta["home_starter_bullpen_lineup"]["starter_workload"]["method"] == "prior_eligible_appearance_workload_v1"
    assert enhanced_meta["home_starter_bullpen_lineup"]["bullpen_workload"]["availability_feature"] is None

    dataset_root = artifact_root / "gate3" / "datasets"
    strict_dataset = build_and_persist_historical_dataset(
        session,
        population=ReconstructionPopulation.TIER_A_STRICT,
        artifact_root=dataset_root,
        training_cutoff=ACQUIRED + timedelta(days=1),
        code_revision="test-a1",
    )
    enhanced_dataset = build_and_persist_historical_dataset(
        session,
        population=ReconstructionPopulation.TIER_B_ENHANCED,
        artifact_root=dataset_root,
        training_cutoff=ACQUIRED + timedelta(days=1),
        code_revision="test-a1",
    )
    session.commit()

    assert strict_dataset.status == enhanced_dataset.status == "SUCCEEDED"
    assert strict_dataset.build.manifest.row_count == 1
    assert enhanced_dataset.build.manifest.row_count == 1
    assert strict_dataset.build.rows[0].historical_quality_tier == "A"
    assert enhanced_dataset.build.rows[0].historical_quality_tier == "B"
    assert strict_dataset.artifact is not None and strict_dataset.artifact.artifact_uri
    assert enhanced_dataset.artifact is not None and enhanced_dataset.artifact.artifact_uri
    assert strict_dataset.artifact.artifact_storage_policy == {
        "maximum_artifact_bytes": 128 * 1024 * 1024,
        "minimum_free_bytes_after_write": 100 * 1024**3,
        "projected_artifact_bytes": strict_dataset.artifact.artifact_storage_policy[
            "projected_artifact_bytes"
        ],
    }
    assert strict_dataset.artifact.artifact_storage_policy[
        "projected_artifact_bytes"
    ] < 128 * 1024 * 1024
    assert session.scalar(select(func.count()).select_from(TrainingDatasetManifest)) == 2
    assert session.scalar(select(func.count()).select_from(HistoricalFeatureMaterialization)) == 2
    assert session.scalar(select(func.count()).select_from(Forecast)) == 0
    assert session.scalar(select(func.count()).select_from(ProviderRequestLedger)) == 0
    assert all(strict_dataset.build.manifest.leakage_checks.values())
    assert all(enhanced_dataset.build.manifest.leakage_checks.values())
    assert strict_dataset.build.rows[0].cutoff_policy_version == CUTOFF_POLICY_VERSION
    rerun = run_historical_reconstruction_dataset(
        session,
        population=ReconstructionPopulation.TIER_A_STRICT,
        artifact_root=dataset_root,
        computed_at=ACQUIRED,
        training_cutoff=ACQUIRED + timedelta(days=1),
        code_revision="test-a1",
    )
    assert rerun.status == "SUCCEEDED"
    assert rerun.dataset.manifest is not None
    assert rerun.dataset.manifest.id == strict_dataset.manifest.id


def test_v4_quality_is_honest_and_lineage_is_compact(session) -> None:
    target = _canonical_history(session)
    assert target is not None
    population = ReconstructionPopulation.TIER_A_STRICT
    service = HistoricalReconstructionService(session)
    result = service.materialize_game(
        target,
        population=population,
        computed_at=ACQUIRED,
        code_revision="test-a1-v4-quality",
    )
    assert result.persistence is not None
    persisted = result.persistence
    components = persisted.materialization.quality_components

    assert population.feature_version.endswith("-v4")
    assert population.materializer_version.endswith("_v4")
    assert components["temporal_integrity"]["score"] == 100.0
    assert components["feature_coverage"]["observed_feature_count"] == 16
    assert components["feature_coverage"]["expected_feature_count"] == 43
    assert components["feature_coverage"]["fraction"] == pytest.approx(16 / 43)
    assert persisted.materialization.quality_score == pytest.approx((16 / 43) * 100)
    assert persisted.materialization.quality_score < components["temporal_integrity"]["score"]

    built = GovernedTrainingDatasetBuilder(session).build_historical(
        cutoff_policy_version=CUTOFF_POLICY_VERSION,
        training_cutoff=ACQUIRED + timedelta(days=1),
        feature_schema_version=GATE3_FEATURE_SCHEMA.version,
        quality_tiers=("A",),
        historical_feature_version_id=persisted.feature_snapshot.feature_version_id,
        historical_materializer_version=population.materializer_version,
    )
    assert built.manifest.row_count == 1
    row = built.rows[0]
    assert row.features["feature_completeness"] == pytest.approx(16 / 43)
    assert row.features["sample_reliability"] == pytest.approx(16 / 43)
    assert row.missing_features and len(row.missing_features) == 27
    assert built.manifest.feature_names == [
        item.name for item in GATE3_FEATURE_SCHEMA.features
    ]
    assert built.manifest.frozen_output_names == list(GATE3_FEATURE_SCHEMA.output_names)
    assert built.manifest.observed_feature_names is not None
    assert len(built.manifest.observed_feature_names) == 20

    assert len(row.source_evidence) == 1
    lineage = row.source_evidence[0]
    assert lineage["lineage_version"] == HISTORICAL_ROW_LINEAGE_VERSION
    assert lineage["canonical_reference_count"] == len(
        persisted.materialization.evidence_manifest
    )
    assert lineage["evidence_manifest_checksum"] == (
        persisted.materialization.evidence_manifest_checksum
    )
    assert lineage["source_bundle_checksum"] == (
        persisted.materialization.source_bundle_checksum
    )
    assert "event_at" not in lineage
    assert "raw_sha256" not in lineage
    assert all(built.manifest.leakage_checks.values())


def test_v4_selection_ignores_coexisting_v1_without_duplicate_exclusion(session) -> None:
    target = _canonical_history(session)
    assert target is not None
    population = ReconstructionPopulation.TIER_A_STRICT
    service = HistoricalReconstructionService(session)
    current = service.materialize_game(
        target,
        population=population,
        computed_at=ACQUIRED,
        code_revision="test-a1-v4-selection",
    )
    assert current.persistence is not None
    session.flush()

    v4_snapshot = current.persistence.feature_snapshot
    v4_materialization = current.persistence.materialization
    legacy_version = FeatureVersion(
        name=population.feature_version_name,
        version="a1-tier-a-strict-v1",
        schema_hash=GATE3_FEATURE_SCHEMA.schema_hash,
        definition={"legacy_fixture": True},
    )
    session.add(legacy_version)
    session.flush()
    snapshot_values = {
        column.name: getattr(v4_snapshot, column.name)
        for column in GameFeatureSnapshot.__table__.columns
        if column.name not in {"id", "created_at"}
    }
    snapshot_values.update(
        feature_version_id=legacy_version.id,
        checksum="1" * 64,
    )
    legacy_snapshot = GameFeatureSnapshot(**snapshot_values)
    session.add(legacy_snapshot)
    session.flush()
    materialization_values = {
        column.name: getattr(v4_materialization, column.name)
        for column in HistoricalFeatureMaterialization.__table__.columns
        if column.name not in {"id", "created_at"}
    }
    materialization_values.update(
        game_feature_snapshot_id=legacy_snapshot.id,
        feature_version_id=legacy_version.id,
        materializer_version="gate3a_a1_tier_a_strict_v1",
        identity_checksum="2" * 64,
    )
    session.add(HistoricalFeatureMaterialization(**materialization_values))
    session.flush()

    built = GovernedTrainingDatasetBuilder(session).build_historical(
        cutoff_policy_version=CUTOFF_POLICY_VERSION,
        training_cutoff=ACQUIRED + timedelta(days=1),
        feature_schema_version=GATE3_FEATURE_SCHEMA.version,
        quality_tiers=("A",),
        historical_feature_version_id=v4_snapshot.feature_version_id,
        historical_materializer_version=population.materializer_version,
    )
    assert built.manifest.row_count == 1
    assert built.rows[0].materializer_version == population.materializer_version
    assert "duplicate_historical_materialization" not in built.manifest.exclusion_reasons


def test_compact_lineage_still_fails_on_canonical_db_tamper(session) -> None:
    target = _canonical_history(session)
    assert target is not None
    population = ReconstructionPopulation.TIER_A_STRICT
    service = HistoricalReconstructionService(session)
    result = service.materialize_game(
        target,
        population=population,
        computed_at=ACQUIRED,
        code_revision="test-a1-v4-tamper",
    )
    assert result.persistence is not None
    materialization_id = result.persistence.materialization.id
    session.commit()
    session.execute(
        update(HistoricalFeatureMaterialization)
        .where(HistoricalFeatureMaterialization.id == materialization_id)
        .values(evidence_manifest_checksum="0" * 64)
    )
    session.commit()

    built = GovernedTrainingDatasetBuilder(session).build_historical(
        cutoff_policy_version=CUTOFF_POLICY_VERSION,
        training_cutoff=ACQUIRED + timedelta(days=1),
        feature_schema_version=GATE3_FEATURE_SCHEMA.version,
        quality_tiers=("A",),
        historical_feature_version_id=result.persistence.feature_snapshot.feature_version_id,
        historical_materializer_version=population.materializer_version,
    )
    assert built.manifest.row_count == 0
    assert built.manifest.exclusion_reasons == {
        "historical_evidence_manifest_checksum_mismatch": 1
    }


def test_artifact_storage_guard_fails_before_any_write(session, artifact_root) -> None:
    target = _canonical_history(session)
    assert target is not None
    population = ReconstructionPopulation.TIER_A_STRICT
    result = HistoricalReconstructionService(session).materialize_game(
        target,
        population=population,
        computed_at=ACQUIRED,
        code_revision="test-a1-v4-storage",
    )
    assert result.persistence is not None
    blocked_root = artifact_root / "blocked-before-write"
    assert not blocked_root.exists()

    with pytest.raises(DatasetArtifactStorageError, match="requires .* bytes"):
        build_and_persist_historical_dataset(
            session,
            population=population,
            artifact_root=blocked_root,
            training_cutoff=ACQUIRED + timedelta(days=1),
            code_revision="test-a1-v4-storage",
            maximum_artifact_bytes=1,
            minimum_free_bytes_after_write=0,
        )
    assert not blocked_root.exists()
    assert session.scalar(select(func.count()).select_from(TrainingDatasetManifest)) == 0


def test_v4_materialization_rejects_a_caller_reduced_feature_denominator(session) -> None:
    target = _canonical_history(session)
    assert target is not None
    population = ReconstructionPopulation.TIER_A_STRICT
    service = HistoricalReconstructionService(session)
    feature_version = service.ensure_feature_version(population)
    built = service.build_snapshot(
        target,
        population=population,
        feature_version=feature_version,
        computed_at=ACQUIRED,
    )
    assert built.snapshot is not None
    complete = built.snapshot
    assert any(item.name == "home_rating" for item in complete.features)
    families = sorted({item.family for item in complete.evidence})
    reduced = build_historical_snapshot(
        game_id=complete.game_id,
        scheduled_first_pitch=complete.scheduled_first_pitch,
        builder_version=complete.builder_version,
        feature_schema_version=complete.feature_schema_version,
        feature_version_id=complete.feature_version_id,
        features=tuple(
            item for item in complete.features if item.name != "home_rating"
        ),
        evidence=complete.evidence,
        quality_policy=QualityPolicy(tuple(
            FamilyWeight(family, 1, True) for family in families
        )),
        family_evidence=tuple(
            FamilyEvidence(
                family,
                EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME,
            )
            for family in families
        ),
        missing_fields=complete.missing_fields,
        provisional_fields=complete.provisional_fields,
        unavailable_families=complete.unavailable_families,
        timing_risk_families=complete.timing_risk_families,
        reconstruction_metadata=complete.reconstruction_metadata,
    )
    prior_row = session.scalar(select(TeamGameHistoryObservation).where(
        TeamGameHistoryObservation.game_id != target.id,
    ))
    target_outcome = session.scalar(select(GameFinalOutcome).where(
        GameFinalOutcome.game_id == target.id,
    ).order_by(GameFinalOutcome.revision.desc()))
    assert prior_row is not None and target_outcome is not None
    source = session.get(HistoricalSourceVersion, prior_row.source_version_id)
    checkpoint = session.get(HistoryBackfillCheckpoint, prior_row.checkpoint_id)
    assert source is not None and checkpoint is not None

    with pytest.raises(HistoricalMaterializationError, match="frozen non-derived schema"):
        persist_historical_snapshot(
            session,
            game=target,
            feature_version=feature_version,
            checkpoint=checkpoint,
            historical_snapshot=reduced,
            source_versions=(source,),
            code_revision="test-a1-v4-frozen-denominator",
            final_outcome=target_outcome,
            computed_at=ACQUIRED,
            materializer_version=population.materializer_version,
        )


def test_later_outcome_correction_invalidates_old_history_and_dataset_lineage(session) -> None:
    target = _canonical_history(session)
    assert target is not None
    population = ReconstructionPopulation.TIER_A_STRICT
    service = HistoricalReconstructionService(session)
    materialized = service.materialize_game(
        target,
        population=population,
        computed_at=ACQUIRED,
        code_revision="test-a1-v4-correction-staleness",
    )
    assert materialized.persistence is not None
    prior_outcome = session.scalar(select(GameFinalOutcome).where(
        GameFinalOutcome.game_id != target.id,
    ).order_by(GameFinalOutcome.revision.desc()))
    assert prior_outcome is not None
    correction_time = ACQUIRED + timedelta(hours=12)
    correction_values = {
        column.name: getattr(prior_outcome, column.name)
        for column in GameFinalOutcome.__table__.columns
        if column.name not in {"id", "created_at"}
    }
    correction_values.update(
        home_score=6,
        away_score=2,
        home_win=True,
        run_differential=4,
        total_runs=8,
        revision=prior_outcome.revision + 1,
        supersedes_outcome_id=prior_outcome.id,
        reconstructed_at=correction_time,
        retrieved_at=correction_time,
        label_available_at=correction_time,
        payload_checksum="9" * 64,
        identity_checksum="8" * 64,
    )
    session.add(GameFinalOutcome(**correction_values))
    session.flush()

    before_correction = GovernedTrainingDatasetBuilder(session).build_historical(
        cutoff_policy_version=CUTOFF_POLICY_VERSION,
        training_cutoff=ACQUIRED + timedelta(hours=1),
        feature_schema_version=GATE3_FEATURE_SCHEMA.version,
        quality_tiers=("A",),
        historical_feature_version_id=(
            materialized.persistence.feature_snapshot.feature_version_id
        ),
        historical_materializer_version=population.materializer_version,
    )
    assert before_correction.manifest.row_count == 1

    rebuilt = service.build_snapshot(
        target,
        population=population,
        computed_at=ACQUIRED + timedelta(days=1),
    )
    assert rebuilt.snapshot is None
    assert rebuilt.reason == "PRIOR_TEAM_HISTORY_REQUIRED_FOR_BOTH_SIDES"

    after_correction = GovernedTrainingDatasetBuilder(session).build_historical(
        cutoff_policy_version=CUTOFF_POLICY_VERSION,
        training_cutoff=ACQUIRED + timedelta(days=1),
        feature_schema_version=GATE3_FEATURE_SCHEMA.version,
        quality_tiers=("A",),
        historical_feature_version_id=(
            materialized.persistence.feature_snapshot.feature_version_id
        ),
        historical_materializer_version=population.materializer_version,
    )
    assert after_correction.manifest.row_count == 0
    assert after_correction.manifest.exclusion_reasons == {
        "historical_canonical_evidence_invalid": 1
    }


def test_latest_semantic_revision_is_selected_and_stale_reference_fails(session) -> None:
    target = _canonical_history(session)
    assert target is not None
    old = session.scalar(select(TeamGameHistoryObservation).where(
        TeamGameHistoryObservation.game_id != target.id,
    ).order_by(TeamGameHistoryObservation.id))
    assert old is not None
    correction = TeamGameHistoryObservation(
        game_id=old.game_id,
        final_outcome_id=old.final_outcome_id,
        season_id=old.season_id,
        team_id=old.team_id,
        opponent_team_id=old.opponent_team_id,
        is_home=old.is_home,
        source_version_id=old.source_version_id,
        checkpoint_id=old.checkpoint_id,
        event_at=old.event_at,
        evidence_end_at=old.evidence_end_at,
        retrieved_at=old.retrieved_at,
        eligible_at=old.eligible_at,
        availability_basis=old.availability_basis,
        evidence_classification=old.evidence_classification,
        quality_tier=old.quality_tier,
        source_record_id=old.source_record_id,
        revision=2,
        supersedes_observation_id=old.id,
        stat_schema_version=old.stat_schema_version,
        values={"batting": {"runs": 6, "ops": 0.750}},
        payload_checksum="d" * 64,
        source_mode=old.source_mode,
        temporal_semantics=old.temporal_semantics,
        identity_checksum="e" * 64,
    )
    session.add(correction)
    session.flush()

    service = HistoricalReconstructionService(session)
    feature_version = service.ensure_feature_version(
        ReconstructionPopulation.TIER_A_STRICT
    )
    rebuilt = service.build_snapshot(
        target,
        population=ReconstructionPopulation.TIER_A_STRICT,
        feature_version=feature_version,
        computed_at=ACQUIRED,
    )
    assert rebuilt.snapshot is not None
    evidence_ids = {row.evidence_id for row in rebuilt.snapshot.evidence}
    assert str(correction.id) in evidence_ids
    assert str(old.id) not in evidence_ids

    source = session.get(HistoricalSourceVersion, old.source_version_id)
    checkpoint = session.get(HistoryBackfillCheckpoint, old.checkpoint_id)
    outcome = session.scalar(select(GameFinalOutcome).where(
        GameFinalOutcome.game_id == target.id,
    ).order_by(GameFinalOutcome.revision.desc()))
    assert source is not None and checkpoint is not None and outcome is not None
    event_at = old.event_at.replace(tzinfo=UTC) if old.event_at.tzinfo is None else old.event_at
    eligible_at = old.eligible_at.replace(tzinfo=UTC) if old.eligible_at.tzinfo is None else old.eligible_at
    starts_at = target.starts_at.replace(tzinfo=UTC) if target.starts_at.tzinfo is None else target.starts_at
    stale = build_historical_snapshot(
        game_id=str(target.id),
        scheduled_first_pitch=starts_at,
        builder_version="stale-revision-test",
        feature_schema_version="mlb_pregame_gate3_v1",
        feature_version_id=str(feature_version.id),
        features=(HistoricalFeature("home_rating", "historical_team", 1500.0),),
        evidence=(HistoricalEvidenceReference(
            evidence_id=str(old.id),
            family="historical_team",
            source=source.source_key,
            source_record_id=old.source_record_id,
            raw_sha256=old.payload_checksum,
            game_id=str(old.game_id),
            event_at=event_at,
            eligible_at=eligible_at,
            classification=EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME,
        ),),
        quality_policy=QualityPolicy((FamilyWeight("historical_team", 1, True),)),
        family_evidence=(FamilyEvidence(
            "historical_team",
            EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME,
        ),),
    )
    with pytest.raises(HistoricalMaterializationError, match="latest eligible"):
        persist_historical_snapshot(
            session,
            game=target,
            feature_version=feature_version,
            checkpoint=checkpoint,
            historical_snapshot=stale,
            source_versions=(source,),
            code_revision="test-a1",
            final_outcome=outcome,
            computed_at=ACQUIRED,
            materializer_version="stale-revision-regression-v1",
        )


def test_materialization_validation_queries_are_bounded_by_batches_not_evidence_rows(
    session,
) -> None:
    prior = [
        _schedule(f"prior-game-{day}", date(2025, 4, day), 5, 2)
        for day in range(1, 13)
    ]
    target_schedule = _schedule("target-game", date(2025, 4, 20), 4, 3)
    persistence = HistoricalCanonicalPersistenceService(session)
    source = persistence.ensure_a1_source_version(effective_from=ACQUIRED)
    persistence.persist_chunk(
        schedule_rows=[*prior, target_schedule],
        boxscores=[
            *(
                _boxscore(schedule, target_multiplier=index)
                for index, schedule in enumerate(prior, start=1)
            ),
            _boxscore(target_schedule, target_multiplier=20),
        ],
        source_version=source,
        stream_key="a1-performance-fixture",
        sequence=0,
        query_fingerprint="a" * 64,
        requested_at=ACQUIRED - timedelta(minutes=1),
        completed_at=ACQUIRED,
        code_revision="test",
        attempt_count=1,
        retry_count=0,
        request_accounting={
            "attempts": 1,
            "retries": 0,
            "successes": 1,
            "failed_attempts": 0,
            "operations": {
                "fixture_local": {
                    "attempts": 1,
                    "successes": 1,
                    "failed_attempts": 0,
                },
            },
        },
    )
    target = session.scalar(select(Game).where(
        Game.starts_at == target_schedule.game.scheduled_at,
    ))
    assert target is not None
    reconstruction = HistoricalReconstructionService(session)
    feature_version = reconstruction.ensure_feature_version(
        ReconstructionPopulation.TIER_A_STRICT
    )
    built = reconstruction.build_snapshot(
        target,
        population=ReconstructionPopulation.TIER_A_STRICT,
        feature_version=feature_version,
        computed_at=ACQUIRED,
    )
    assert built.snapshot is not None
    assert len(built.snapshot.evidence) >= 60
    checkpoint = session.scalar(select(HistoryBackfillCheckpoint).where(
        HistoryBackfillCheckpoint.stream_key == "a1-performance-fixture",
    ))
    outcome = session.scalar(select(GameFinalOutcome).where(
        GameFinalOutcome.game_id == target.id,
    ))
    assert checkpoint is not None and outcome is not None

    selects: list[str] = []

    def record_select(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record_select)
    try:
        persist_historical_snapshot(
            session,
            game=target,
            feature_version=feature_version,
            checkpoint=checkpoint,
            historical_snapshot=built.snapshot,
            source_versions=(source,),
            code_revision="test-a1-v4-batched-validation",
            final_outcome=outcome,
            computed_at=ACQUIRED,
            materializer_version="batched-validation-regression-v1",
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_select)

    # This stays roughly constant as evidence grows; the pre-batching path
    # issued at least two revision SELECTs per reference (144+ here).
    assert len(selects) <= 30, f"unexpected SELECT amplification: {len(selects)}"
