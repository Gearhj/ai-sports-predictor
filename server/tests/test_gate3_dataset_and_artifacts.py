from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.ingestion import CanonicalNormalizer
from app.models import (
    Forecast,
    ForecastLifecycle,
    ForecastStatus,
    Game,
    GameFeatureSnapshot,
    GameStateObservation,
    GameStatus,
    GameTeam,
    ModelEvaluation,
    ModelVersion,
    Team,
    TrainingDatasetManifest,
)
from app.training.artifacts import Gate3ModelArtifactStore
from app.training.dataset import (
    DatasetBuildResult,
    GovernedTrainingDatasetBuilder,
    TrainingDatasetArtifactStore,
    TrainingRow,
)
from app.training.pipeline import Gate3TrainingPipeline
from app.training.replay import Gate3BacktestReplay


def _eligible_game(session, *, evidence_delay_minutes: int = 0):
    _, league, season, feature_version = CanonicalNormalizer(session).ensure_core(2025)
    home = Team(league_id=league.id, name="Gate 3 Home", abbreviation="G3H")
    away = Team(league_id=league.id, name="Gate 3 Away", abbreviation="G3A")
    session.add_all([home, away])
    session.flush()
    starts_at = datetime(2025, 9, 1, 23, tzinfo=UTC)
    game = Game(
        league_id=league.id,
        season_id=season.id,
        starts_at=starts_at,
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
    pregame = GameStateObservation(
        game_id=game.id,
        home_team_id=home.id,
        away_team_id=away.id,
        scheduled_at=starts_at,
        status="SCHEDULED",
        is_postponed=False,
        home_starter_confirmation="PROBABLE",
        away_starter_confirmation="PROBABLE",
        observed_at=starts_at - timedelta(hours=2),
        available_as_of=starts_at - timedelta(hours=2),
        source="GATE3_TEST",
        source_mode="LIVE",
        payload_checksum="1" * 64,
    )
    final = GameStateObservation(
        game_id=game.id,
        home_team_id=home.id,
        away_team_id=away.id,
        scheduled_at=starts_at,
        status="FINAL",
        is_postponed=False,
        home_starter_confirmation="CONFIRMED",
        away_starter_confirmation="CONFIRMED",
        home_score=5,
        away_score=3,
        final_at=starts_at + timedelta(hours=3),
        observed_at=starts_at + timedelta(hours=3),
        available_as_of=starts_at + timedelta(hours=3, minutes=1),
        source="GATE3_TEST",
        source_mode="LIVE",
        payload_checksum="2" * 64,
    )
    session.add_all([pregame, final])
    session.flush()
    snapshot = GameFeatureSnapshot(
        game_id=game.id,
        generated_at=starts_at - timedelta(minutes=45),
        data_as_of=starts_at - timedelta(minutes=45),
        feature_schema_version="mlb_point_in_time_v2",
        feature_version_id=feature_version.id,
        game_state_observation_id=pregame.id,
        bullpen_observation_ids=[],
        roster_observation_ids=[],
        statcast_observation_ids=[],
        market_snapshot_ids=[],
        source_evidence=[{
            "family": "game_state",
            "id": str(pregame.id),
            "source": "GATE3_TEST",
            "source_mode": "LIVE",
            "observed_at": (pregame.observed_at + timedelta(minutes=evidence_delay_minutes)).isoformat(),
            "available_as_of": (pregame.available_as_of + timedelta(minutes=evidence_delay_minutes)).isoformat(),
        }],
        feature_values={
            "home_rating": 1520.0,
            "away_rating": 1480.0,
            "home_runs_per_game": 4.8,
            "away_runs_per_game": 4.2,
            "home_runs_allowed_per_game": 4.0,
            "away_runs_allowed_per_game": 4.6,
            "temperature_f": 75.0,
        },
        feature_completeness=0.55,
        missing_features=["home_lineup_xwoba", "away_lineup_xwoba"],
        stale_features=[],
        provisional_features=[],
        checksum="3" * 64,
        source_mode="LIVE",
        frozen_at=starts_at - timedelta(minutes=44),
    )
    session.add(snapshot)
    session.flush()
    forecast = Forecast(
        game_id=game.id,
        model_version_id=CanonicalNormalizer(session).ensure_core(2025)[0].id,  # replaced below
        feature_version_id=feature_version.id,
        feature_snapshot_id=snapshot.id,
        version=1,
        generated_at=starts_at - timedelta(minutes=44),
        available_as_of=starts_at - timedelta(minutes=44),
        status=ForecastStatus.PUBLISHED,
        lifecycle=ForecastLifecycle.PREGAME,
        home_win_probability=0.58,
        away_win_probability=0.42,
        source_mode="LIVE",
        published_at=starts_at - timedelta(minutes=44),
    )
    # The model foreign key needs a real registry row; reuse the normalizer's
    # deterministic Gate 2 registration path through ForecastService versions.
    from app.services.forecast_service import ForecastService

    model_version, _ = ForecastService(session, type("S", (), {
        "ensemble_baseline_weight": 0.3,
        "ensemble_run_weight": 0.45,
        "ensemble_boosting_weight": 0.25,
        "simulation_draws": 500,
        "simulation_seed": 7,
    })())._versions()
    forecast.model_version_id = model_version.id
    session.add(forecast)
    session.commit()
    return game, starts_at


def test_governed_dataset_uses_only_pregame_snapshot_and_append_only_final(session) -> None:
    game, starts_at = _eligible_game(session)

    result = GovernedTrainingDatasetBuilder(session).build(
        training_cutoff=starts_at + timedelta(hours=4),
    )

    assert result.trainable
    assert result.manifest.row_count == 1
    assert result.manifest.leakage_checks == {
        "one_row_per_game": True,
        "pregame_cutoff": True,
        "live_source_only": True,
        "no_target_named_features": True,
        "final_scores_targets_only": True,
        "feature_schema_consistent": True,
        "snapshot_checksum_present": True,
        "source_evidence_cutoff": True,
        "market_cutoff": True,
    }
    row = result.rows[0]
    assert row.game_id == str(game.id)
    assert row.home_win == 1
    assert (row.home_runs, row.away_runs, row.run_differential, row.total_runs) == (5, 3, 2, 8)
    assert row.data_as_of < row.scheduled_first_pitch
    assert "home_score" not in row.features
    assert row.features["feature_completeness"] == pytest.approx(0.55)
    assert row.features["lineups_confirmed"] == 0.0
    assert row.features["starters_confirmed"] == 0.0


def test_dataset_before_final_availability_is_empty_and_immutable_round_trip(session, artifact_root) -> None:
    _, starts_at = _eligible_game(session)
    result = GovernedTrainingDatasetBuilder(session).build(
        training_cutoff=starts_at + timedelta(hours=2),
    )
    assert not result.trainable
    assert result.manifest.row_count == 0
    assert result.manifest.exclusion_reasons == {"final_outcome_unavailable": 1}

    store = TrainingDatasetArtifactStore(artifact_root / "gate3" / "datasets")
    saved = store.save(result)
    loaded_manifest, loaded_rows = store.load(saved.dataset_id)
    assert loaded_manifest.checksum == result.manifest.checksum
    assert loaded_rows == ()
    with pytest.raises(ValueError, match="Invalid dataset identifier"):
        store.load("../../escape")


def test_dataset_rejects_snapshot_evidence_that_arrived_after_its_cutoff(session) -> None:
    _, starts_at = _eligible_game(session, evidence_delay_minutes=120)

    result = GovernedTrainingDatasetBuilder(session).build(
        training_cutoff=starts_at + timedelta(hours=4),
    )

    assert result.manifest.row_count == 0
    assert result.manifest.exclusion_reasons == {"source_evidence_after_snapshot_cutoff": 1}


def test_gate3_model_bundle_is_content_identified_and_tamper_evident(artifact_root) -> None:
    store = Gate3ModelArtifactStore(artifact_root / "gate3" / "models")
    manifest = store.save(
        {"coefficient": 0.25},
        name="asp_mlb_logistic",
        algorithm="regularized_logistic",
        target="home_win",
        dataset_id="mlb-gate3-test",
        dataset_checksum="a" * 64,
        feature_version="mlb_pregame/2.0.0",
        feature_schema_version="mlb_point_in_time_v2",
        feature_names=["rating_difference", "rating_difference_missing"],
        training_window={"start": "2023-01-01", "end": "2024-06-30", "games": 1000},
        validation_window={"start": "2024-07-01", "end": "2024-09-30", "games": 200},
        holdout_window={"start": "2025-01-01", "end": "2025-09-30", "games": 300},
        hyperparameters={"C": 1.0},
        calibrator={"kind": "platt"},
        calibration={"kind": "platt", "fit_split": "validation_oof"},
        ensemble_weights={"logistic": 1.0},
        simulation_configuration={"draws": 20000},
        seed_policy={"training": 17, "bootstrap": 19},
        target_definition={"home_win": "final home score > away score"},
        code_revision="test-revision",
        dependency_versions={"scikit-learn": "test"},
        metrics={"holdout": {"brier_score": 0.24}},
        predictions=[{"game_id": "one", "split": "HOLDOUT", "probability": 0.55}],
    )
    model, calibrator, loaded = store.load(manifest.name, manifest.version)
    assert model == {"coefficient": 0.25}
    assert calibrator == {"kind": "platt"}
    assert loaded.identity_checksum == manifest.identity_checksum

    model_path = artifact_root / "gate3" / "models" / manifest.name / manifest.version / "model.joblib"
    model_path.write_bytes(model_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        store.load(manifest.name, manifest.version)
    with pytest.raises(ValueError, match="safe path segment"):
        store.save(
            object(), name="../escape", algorithm="x", target="home_win",
            dataset_id="x", dataset_checksum="b" * 64, feature_version="x",
            feature_schema_version="x", feature_names=["x"], training_window={},
            validation_window={}, holdout_window={}, hyperparameters={}, calibrator=None,
            calibration={}, ensemble_weights={}, simulation_configuration={}, seed_policy={},
            target_definition={}, code_revision="x", dependency_versions={}, metrics={},
        )


def test_controlled_training_run_records_zero_sample_without_model_or_evaluation(
    session,
    settings,
    artifact_root,
) -> None:
    CanonicalNormalizer(session).ensure_core(2026)
    session.commit()
    controlled_settings = settings.model_copy(
        update={"gate3_artifact_root": artifact_root / "gate3"},
    )
    cutoff = datetime(2026, 8, 13, 12, tzinfo=UTC)

    first = Gate3TrainingPipeline(session, controlled_settings).run(training_cutoff=cutoff)
    second = Gate3TrainingPipeline(session, controlled_settings).run(training_cutoff=cutoff)

    assert first.status == second.status == "INSUFFICIENT_DATA"
    assert first.promotion_decision == second.promotion_decision == "NO_PROMOTION"
    assert first.row_count == second.row_count == 0
    assert first.evaluation_id is second.evaluation_id is None
    assert first.model_version is second.model_version is None
    assert first.provider_calls == second.provider_calls == 0
    assert session.scalar(select(func.count()).select_from(TrainingDatasetManifest)) == 1
    assert session.scalar(select(func.count()).select_from(ModelEvaluation)) == 0
    persisted = session.scalar(select(TrainingDatasetManifest))
    assert persisted is not None
    assert persisted.feature_version_id is not None
    assert persisted.source_evidence == [{"rows_checksum": persisted.artifact_checksum}]


def test_historical_replay_uses_point_in_time_state_and_stays_draft(session, settings) -> None:
    game, starts_at = _eligible_game(session)
    cutoff = starts_at - timedelta(minutes=45)

    result = Gate3BacktestReplay(session, settings).run_game(game.id, data_as_of=cutoff)
    replay = session.get(Forecast, UUID(result.forecast_id))

    assert result.research_mode == "BACKTEST"
    assert not result.evaluation_eligible
    assert result.provider_calls == 0
    assert replay is not None
    assert replay.status == ForecastStatus.DRAFT
    assert replay.published_at is None
    assert replay.generated_at.replace(tzinfo=UTC) > starts_at
    assert replay.readiness_components["_gate3"] == {
        "research_mode": "BACKTEST",
        "evaluation_eligible": False,
        "requested_data_as_of": cutoff.isoformat(),
        "actual_generated_at": result.generated_at,
        "point_in_time_service": "GameStateService.get_game_state",
        "provider_calls": 0,
    }


def test_sufficient_synthetic_dataset_runs_through_governed_pipeline(
    session,
    settings,
    artifact_root,
    monkeypatch,
) -> None:
    _, _, _, feature_version = CanonicalNormalizer(session).ensure_core(2025)
    session.commit()
    start = datetime(2024, 4, 1, 19, tzinfo=UTC)
    rows: list[TrainingRow] = []
    for day in range(70):
        for game_number in range(2):
            event_at = start + timedelta(days=day, minutes=20 * game_number)
            cutoff = event_at - timedelta(hours=12)
            home_win = game_number
            strength = 1.0 if home_win else -1.0
            home_runs, away_runs = ((6, 3) if home_win else (3, 6))
            index = day * 2 + game_number
            rows.append(TrainingRow(
                game_id=f"synthetic-{index}",
                scheduled_first_pitch=event_at.isoformat().replace("+00:00", "Z"),
                data_as_of=cutoff.isoformat().replace("+00:00", "Z"),
                lifecycle="PREGAME" if day % 2 else "MORNING",
                feature_snapshot_id=f"synthetic-snapshot-{index}",
                feature_snapshot_checksum=f"{index + 1:064x}",
                feature_version_id=str(feature_version.id),
                feature_version=f"{feature_version.name}/{feature_version.version}",
                feature_schema_version="mlb_point_in_time_v2",
                source_mode="LIVE",
                source_evidence=[],
                home_team_id=f"home-{game_number}",
                away_team_id=f"away-{game_number}",
                features={
                    "home_rating": 1500 + strength * 90,
                    "away_rating": 1500.0,
                    "home_runs_per_game": 4.5 + strength * 0.6,
                    "away_runs_per_game": 4.5 - strength * 0.4,
                    "home_runs_allowed_per_game": 4.4 - strength * 0.4,
                    "away_runs_allowed_per_game": 4.4 + strength * 0.5,
                    "home_ops": 0.720 + strength * 0.025,
                    "away_ops": 0.720 - strength * 0.020,
                    "home_recent_runs": 4.5 + strength * 0.7,
                    "away_recent_runs": 4.5 - strength * 0.5,
                    "home_starter_era": 4.2 - strength * 0.4,
                    "away_starter_era": 4.2 + strength * 0.4,
                    "park_run_factor": 1.0,
                },
                missing_features=[],
                stale_features=[],
                provisional_features=[],
                feature_completeness=0.85,
                home_win=home_win,
                home_runs=home_runs,
                away_runs=away_runs,
                run_differential=home_runs - away_runs,
                total_runs=home_runs + away_runs,
                market_home_probability=0.52,
                market_timestamp=cutoff.isoformat().replace("+00:00", "Z"),
                market_books=5,
                closing_home_probability=None,
                closing_timestamp=None,
            ))
    builder = GovernedTrainingDatasetBuilder(session)
    manifest = builder._manifest(
        rows,
        [],
        Counter(),
        start + timedelta(days=80),
        "mlb_point_in_time_v2",
    )
    built = DatasetBuildResult(manifest=manifest, rows=tuple(rows), exclusions=())
    monkeypatch.setattr(
        GovernedTrainingDatasetBuilder,
        "build",
        lambda self, *, training_cutoff=None, feature_schema_version=None: built,
    )
    controlled_settings = settings.model_copy(update={
        "gate3_artifact_root": artifact_root / "gate3-sufficient",
        "gate3_min_training_rows": 20,
        "gate3_min_holdout_rows": 10,
        "gate3_promotion_min_holdout_rows": 50,
        "gate3_rolling_folds": 3,
        "gate3_bootstrap_iterations": 100,
    })

    result = Gate3TrainingPipeline(session, controlled_settings).run(
        training_cutoff=start + timedelta(days=80),
    )

    assert result.status == "SUCCEEDED"
    assert result.row_count == 140
    assert result.provider_calls == 0
    assert result.model_version is not None
    assert result.evaluation_id is not None
    evaluation = session.get(ModelEvaluation, UUID(result.evaluation_id))
    assert evaluation is not None
    assert evaluation.status == "COMPLETED"
    assert evaluation.metrics["rolling_origin_folds"] >= 1
    assert evaluation.metrics["base_rolling_origin_folds"] >= 3
    assert evaluation.validation_window["start_date"] == evaluation.slice_metrics["stack_fold_audits"][0]["validation_started_at"]
    assert evaluation.validation_window["end_date"] == evaluation.slice_metrics["stack_fold_audits"][-1]["validation_ended_at"]
    assert evaluation.validation_window["games"] == evaluation.metrics["validation"]["observations"]
    assert evaluation.metrics["holdout"]["observations"] >= 10
    assert {"home_prior", "logistic"}.issubset(evaluation.baseline_metrics)
    model = session.get(ModelVersion, evaluation.model_version_id)
    assert model is not None
    assert model.status.name in {"CHALLENGER", "CANDIDATE"}
    assert model.artifact_checksum and model.identity_checksum
