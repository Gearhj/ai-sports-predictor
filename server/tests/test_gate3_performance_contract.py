from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models import (
    FeatureVersion,
    ModelEvaluation,
    ModelStatus,
    ModelVersion,
    TrainingDatasetManifest,
)


def _manifest(feature_version_id, *, version: str, rows: int) -> TrainingDatasetManifest:
    return TrainingDatasetManifest(
        name="mlb_pregame_training",
        version=version,
        feature_version_id=feature_version_id,
        source_mode="LIVE",
        schema_version="mlb_point_in_time_v2",
        target="home_win",
        target_definition={"home_win": "final home score > away score"},
        cutoff_policy={"snapshot": "latest published pregame"},
        split_policy={
            "policy": "date_grouped_rolling_origin_with_untouched_holdout",
            "policy_version": "gate3-public-eval-v2",
            "rolling_origin_folds": 3,
        },
        feature_names=["home_rating", "away_rating"],
        seasons=[2023, 2024, 2025],
        event_start_at=datetime(2023, 3, 30, tzinfo=UTC),
        event_end_at=datetime(2025, 9, 28, tzinfo=UTC),
        row_count=rows,
        game_count=rows,
        excluded_row_count=9,
        artifact_uri=f".runtime/gate3/datasets/{version}",
        artifact_checksum="a" * 64,
        source_evidence=[{"source_mode": "LIVE"}],
        quality_summary={"leakage_checks": {"pregame_cutoff": True}},
        code_revision="test-revision",
        identity_checksum=(version.encode().hex() + "0" * 64)[:64],
        built_at=datetime(2026, 8, 13, 1, tzinfo=UTC),
    )


def _model(manifest: TrainingDatasetManifest, feature: FeatureVersion) -> ModelVersion:
    return ModelVersion(
        name="asp_mlb_gate3",
        version="3.0.0-test",
        feature_version_id=feature.id,
        algorithm="calibrated_stacked_ensemble",
        status=ModelStatus.CANDIDATE,
        target="home_win",
        training_manifest_id=manifest.id,
        training_window={"start": "2023-03-30", "end": "2023-09-30", "games": 2400},
        validation_window={"start": "2024-03-28", "end": "2024-09-29", "games": 2400},
        holdout_window={"start": "2025-03-27", "end": "2025-09-28", "games": 2400},
        artifact_uri=".runtime/gate3/models/3.0.0-test/model.joblib",
        artifact_checksum="7" * 64,
        calibrator_uri=".runtime/gate3/models/3.0.0-test/calibrator.joblib",
        calibrator_checksum="8" * 64,
        code_revision="test-revision",
        identity_checksum="b" * 64,
        feature_names=["home_rating", "away_rating"],
        promotion_decision="CHALLENGER_READY",
        promotion_reason="The locked holdout did not satisfy every promotion criterion.",
    )


def test_performance_contract_reports_not_run_without_governed_evidence(client) -> None:
    response = client.get("/api/v1/performance")
    assert response.status_code == 200
    body = response.json()
    assert body["model_status"] == "DEVELOPMENT"
    assert body["evaluated_games"] == 0
    assert body["offline_evaluation"] == {
        "status": "NOT_RUN",
        "status_reason": "No governed offline evaluation or training-dataset manifest has been published.",
        "model_version": None,
            "manifest_version": None,
            "feature_version": None,
            "dataset_checksum": None,
            "feature_set_checksum": None,
            "research_decision": None,
            "evaluation_policy_version": "gate3-promotion-v1",
        "rolling_origin_folds": 0,
        "train_window": None,
        "validation_window": None,
        "holdout_window": None,
        "validation_metrics": None,
        "holdout_metrics": None,
        "baselines": [],
        "promotion": {
            "decision": "PENDING",
            "reason": "No promotion decision exists because offline evaluation has not run.",
            "criteria_version": "gate3-promotion-v1",
        },
    }


def test_performance_contract_blocks_insufficient_latest_live_manifest(
    client,
    session,
    settings,
) -> None:
    feature = FeatureVersion(
        name="mlb_pregame",
        version="2.0.0",
        schema_hash="f" * 64,
    )
    session.add(feature)
    session.flush()
    manifest = _manifest(feature.id, version="controlled-run-2026-08-13", rows=9)
    session.add(manifest)
    session.commit()

    body = client.get("/api/v1/performance").json()
    evaluation = body["offline_evaluation"]
    assert body["model_status"] == "DEVELOPMENT"
    assert evaluation["status"] == "INSUFFICIENT_DATA"
    assert evaluation["status_reason"] == (
        f"Only 9 eligible LIVE games were available; "
        f"the governed policy requires at least {settings.gate3_min_training_rows}."
    )
    assert evaluation["manifest_version"] == "controlled-run-2026-08-13"
    assert evaluation["feature_version"] == "mlb_pregame/2.0.0"
    assert evaluation["evaluation_policy_version"] == "gate3-public-eval-v2"
    assert evaluation["rolling_origin_folds"] == 0
    assert evaluation["validation_metrics"] is None
    assert evaluation["holdout_metrics"] is None
    assert evaluation["baselines"] == []
    assert evaluation["promotion"]["decision"] == "BLOCKED"


def test_performance_contract_maps_latest_completed_immutable_evaluation(
    client,
    session,
) -> None:
    feature = FeatureVersion(
        name="mlb_pregame",
        version="3.0.0",
        schema_hash="f" * 64,
    )
    session.add(feature)
    session.flush()
    manifest = _manifest(feature.id, version="mlb-gate3-governed-v1", rows=7200)
    session.add(manifest)
    session.flush()
    model = _model(manifest, feature)
    session.add(model)
    session.flush()
    evaluation = ModelEvaluation(
        model_version_id=model.id,
        training_manifest_id=manifest.id,
        evaluation_type="ROLLING_ORIGIN_FINAL_HOLDOUT",
        status="COMPLETED",
        validation_window=model.validation_window,
        holdout_window=model.holdout_window,
        metrics={
            "rolling_origin_folds": 3,
            "validation": {
                "brier_score": 0.220,
                "log_loss": 0.632,
                "expected_calibration_error": 0.031,
                "run_mae": 1.22,
            },
            "holdout": {
                "brier_score": 0.224,
                "log_loss": 0.641,
                "expected_calibration_error": 0.035,
                "run_mae": 1.26,
            },
        },
        baseline_metrics={
            "market_consensus": {
                "name": "Market consensus",
                "sample_size": 2100,
                "brier_score": 0.226,
                "log_loss": 0.646,
                "expected_calibration_error": 0.039,
                "run_mae": None,
            },
            "team_strength": {
                "name": "Team-strength baseline",
                "observations": 2400,
                "brier_score": 0.233,
                "log_loss": 0.658,
                "ece": 0.047,
                "run_mae": 1.38,
            },
        },
        slice_metrics={"evaluation_policy_version": "gate3-public-eval-v3"},
        calibration={"kind": "platt"},
        prediction_artifact_uri=".runtime/gate3/models/3.0.0-test/predictions.jsonl",
        prediction_artifact_checksum="9" * 64,
        evaluated_at=datetime(2026, 8, 13, 3, tzinfo=UTC),
        source_mode="LIVE",
        code_revision="test-revision",
        identity_checksum="c" * 64,
        superiority_claim=False,
        promotion_recommendation=False,
        notes="Immutable governed evaluation completed.",
    )
    session.add(evaluation)
    session.commit()

    body = client.get("/api/v1/performance").json()
    offline = body["offline_evaluation"]
    assert body["model_status"] == "DEVELOPMENT"
    assert body["evaluation_available"] is False  # forward metrics remain separate
    assert offline["status"] == "AVAILABLE"
    assert offline["model_version"] == "asp_mlb_gate3 v3.0.0-test"
    assert offline["manifest_version"] == "mlb-gate3-governed-v1"
    assert offline["feature_version"] == "mlb_pregame/3.0.0"
    assert offline["evaluation_policy_version"] == "gate3-public-eval-v3"
    assert offline["rolling_origin_folds"] == 3
    assert offline["train_window"] == {
        "start_date": "2023-03-30",
        "end_date": "2023-09-30",
        "games": 2400,
    }
    assert offline["validation_window"]["games"] == 2400
    assert offline["holdout_window"]["games"] == 2400
    assert offline["validation_metrics"]["brier_score"] == 0.220
    assert offline["holdout_metrics"]["expected_calibration_error"] == 0.035
    assert [baseline["name"] for baseline in offline["baselines"]] == [
        "Market consensus",
        "Team-strength baseline",
    ]
    assert offline["baselines"][1]["expected_calibration_error"] == 0.047
    assert offline["promotion"] == {
        "decision": "NOT_PROMOTED",
        "reason": "The locked holdout did not satisfy every promotion criterion.",
        "criteria_version": "gate3-public-eval-v3",
    }


def test_performance_contract_fails_closed_for_failed_evaluation(client, session) -> None:
    feature = FeatureVersion(name="mlb_pregame", version="3.0.0", schema_hash="f" * 64)
    session.add(feature)
    session.flush()
    manifest = _manifest(feature.id, version="failed-run", rows=1000)
    session.add(manifest)
    session.flush()
    model = _model(manifest, feature)
    session.add(model)
    session.flush()
    session.add(ModelEvaluation(
        model_version_id=model.id,
        training_manifest_id=manifest.id,
        evaluation_type="ROLLING_ORIGIN_FINAL_HOLDOUT",
        status="FAILED",
        validation_window=model.validation_window,
        holdout_window=model.holdout_window,
        metrics={"holdout": {"brier_score": 0.0, "log_loss": 0.0}},
        baseline_metrics={"baseline": {"sample_size": 1000, "brier_score": 0.25}},
        evaluated_at=datetime(2026, 8, 13, 4, tzinfo=UTC),
        source_mode="LIVE",
        code_revision="test-revision",
        identity_checksum="d" * 64,
        superiority_claim=False,
        notes="Calibration fit failed before the locked holdout completed.",
    ))
    session.commit()

    offline = client.get("/api/v1/performance").json()["offline_evaluation"]
    assert offline["status"] == "FAILED"
    assert offline["status_reason"] == "Calibration fit failed before the locked holdout completed."
    assert offline["validation_metrics"] is None
    assert offline["holdout_metrics"] is None
    assert offline["baselines"] == []
    assert offline["promotion"]["decision"] == "BLOCKED"


def test_performance_contract_respects_structured_temporal_insufficiency(
    client,
    session,
) -> None:
    feature = FeatureVersion(name="mlb_pregame", version="3.0.0", schema_hash="f" * 64)
    session.add(feature)
    session.flush()
    manifest = _manifest(feature.id, version="temporally-ineligible", rows=7200)
    reason = "The sample has fewer distinct event dates than the chronological policy requires."
    manifest.quality_summary = {
        "leakage_checks": {"pregame_cutoff": True},
        "training_sufficiency": {
            "status": "INSUFFICIENT_DATA",
            "reason": reason,
            "checks": {
                "distinct_event_dates": {
                    "passed": False,
                    "observed": 2,
                    "required": 5,
                },
            },
        },
    }
    session.add(manifest)
    session.commit()

    offline = client.get("/api/v1/performance").json()["offline_evaluation"]
    assert offline["status"] == "INSUFFICIENT_DATA"
    assert offline["status_reason"] == reason
    assert offline["promotion"] == {
        "decision": "BLOCKED",
        "reason": reason,
        "criteria_version": "gate3-public-eval-v2",
    }


@pytest.mark.parametrize(
    "broken_evidence",
    [
        "leakage_check",
        "model_artifact",
        "calibrator_artifact",
        "prediction_artifact",
        "feature_lineage",
        "manifest_lineage",
        "code_revision",
        "non_completed_status",
    ],
)
def test_completed_evaluation_fails_closed_without_complete_consistent_evidence(
    client,
    session,
    broken_evidence: str,
) -> None:
    feature = FeatureVersion(name="mlb_pregame", version="3.0.0", schema_hash="f" * 64)
    session.add(feature)
    session.flush()
    manifest = _manifest(feature.id, version="integrity-run", rows=7200)
    if broken_evidence == "leakage_check":
        manifest.quality_summary = {"leakage_checks": {"pregame_cutoff": False}}
    session.add(manifest)
    session.flush()
    model = _model(manifest, feature)

    if broken_evidence == "model_artifact":
        model.artifact_checksum = None
    elif broken_evidence == "calibrator_artifact":
        model.calibrator_checksum = None
    elif broken_evidence == "feature_lineage":
        other_feature = FeatureVersion(name="mlb_pregame", version="3.0.1", schema_hash="e" * 64)
        session.add(other_feature)
        session.flush()
        model.feature_version_id = other_feature.id
    elif broken_evidence == "manifest_lineage":
        other_manifest = _manifest(feature.id, version="other-manifest", rows=7200)
        other_manifest.identity_checksum = "e" * 64
        session.add(other_manifest)
        session.flush()
        model.training_manifest_id = other_manifest.id
    elif broken_evidence == "code_revision":
        model.code_revision = "different-revision"

    session.add(model)
    session.flush()
    evaluation = ModelEvaluation(
        model_version_id=model.id,
        training_manifest_id=manifest.id,
        evaluation_type="ROLLING_ORIGIN_FINAL_HOLDOUT",
        status="COMPLETED",
        validation_window=model.validation_window,
        holdout_window=model.holdout_window,
        metrics={
            "validation": {"brier_score": 0.22, "log_loss": 0.63},
            "holdout": {"brier_score": 0.23, "log_loss": 0.65},
        },
        baseline_metrics={"home_prior": {"sample_size": 7200, "brier_score": 0.25}},
        prediction_artifact_uri=".runtime/gate3/models/integrity-run/predictions.jsonl",
        prediction_artifact_checksum="9" * 64,
        evaluated_at=datetime(2026, 8, 13, 5, tzinfo=UTC),
        source_mode="LIVE",
        code_revision="test-revision",
        identity_checksum="d" * 64,
        superiority_claim=False,
        promotion_recommendation=False,
    )
    if broken_evidence == "prediction_artifact":
        evaluation.prediction_artifact_checksum = None
    elif broken_evidence == "non_completed_status":
        evaluation.status = "AVAILABLE"
    session.add(evaluation)
    session.commit()

    offline = client.get("/api/v1/performance").json()["offline_evaluation"]
    expected_status = "NOT_RUN" if broken_evidence == "non_completed_status" else "FAILED"
    assert offline["status"] == expected_status
    if expected_status == "FAILED":
        assert "failed evidence-integrity checks" in offline["status_reason"]
    else:
        assert "not COMPLETED" in offline["status_reason"]
    assert offline["validation_metrics"] is None
    assert offline["holdout_metrics"] is None
    assert offline["baselines"] == []
    assert offline["promotion"]["decision"] == (
        "PENDING" if expected_status == "NOT_RUN" else "BLOCKED"
    )


def _gen1_evidence_graph(
    session,
    *,
    population: str = "TIER_A_STRICT",
    broken_lineage: str | None = None,
) -> tuple[ModelVersion, ModelEvaluation]:
    source_feature = FeatureVersion(
        name="mlb_historical_pregame_a2",
        version="a2-tier-a-strict-v6",
        schema_hash="1" * 64,
    )
    session.add(source_feature)
    session.flush()
    manifest = _manifest(
        source_feature.id,
        version=f"gen1-{population.lower()}",
        rows=2428,
    )
    manifest.name = (
        "mlb_historical_a2_tier_a_strict"
        if population == "TIER_A_STRICT"
        else "mlb_historical_a2_tier_b_enhanced"
    )
    manifest.schema_version = "mlb_historical_pregame_a2_v2"
    manifest.seasons = [2025]
    manifest.cutoff_policy = {
        "policy_version": "mlb_t_minus_6h_v1",
        "hours_before_first_pitch": 6,
        "population": population,
    }
    manifest.quality_summary = {
        "stage": "GATE_3A_A2",
        "population": population,
        "quality_tiers": ["A" if population == "TIER_A_STRICT" else "B"],
        "schema_hash": source_feature.schema_hash,
        "leakage_checks": {
            "pregame_cutoff": True,
            "no_market_or_clv": True,
            "historical_tier_consistent": True,
        },
    }
    session.add(manifest)
    session.flush()

    source_lineage = {
        "research_policy_version": "MLB_GEN1_RESEARCH_POLICY_V1",
        "source_feature_version_id": str(source_feature.id),
        "source_feature_schema_version": manifest.schema_version,
        "source_feature_schema_hash": source_feature.schema_hash,
    }
    derived_schema_version = "mlb_historical_pregame_gen1_selected_v1"
    definition = {
        **source_lineage,
        "derived_feature_schema_version": derived_schema_version,
        "derived_feature_schema_hash": "2" * 64,
    }
    if broken_lineage == "definition":
        definition.pop("source_feature_version_id")
    derived_feature = FeatureVersion(
        name="mlb_historical_pregame_gen1_tier_a",
        # Registry labels and schema versions are deliberately distinct. Public
        # integrity checks must bind the model to the definition's schema
        # identity, not reinterpret this registry label as the schema version.
        version="gen1-v1-registry-label",
        schema_hash="2" * 64,
        definition=definition,
    )
    session.add(derived_feature)
    session.flush()

    model = _model(manifest, derived_feature)
    model.name = "mlb_gen1_research"
    model.version = f"gen1-{population.lower()}"
    model.algorithm = "gen1_research_only"
    model.parameters = {
        **source_lineage,
        "derived_feature_schema_version": derived_schema_version,
        "derived_feature_schema_hash": derived_feature.schema_hash,
    }
    if broken_lineage == "parameters":
        model.parameters["source_feature_schema_hash"] = "3" * 64
    session.add(model)
    session.flush()

    evaluation = ModelEvaluation(
        model_version_id=model.id,
        training_manifest_id=manifest.id,
        evaluation_type="GEN1_RESEARCH_FINAL_HOLDOUT",
        status="COMPLETED",
        validation_window=model.validation_window,
        holdout_window=model.holdout_window,
        metrics={
            "rolling_origin_folds": 5,
            "research_decision": "RESEARCH_CHALLENGER",
            "validation": {"brier_score": 0.23, "log_loss": 0.66},
            "holdout": {"brier_score": 0.24, "log_loss": 0.68},
        },
        baseline_metrics={
            "home_prior": {"sample_size": 505, "brier_score": 0.25},
        },
        slice_metrics={
            "evaluation_policy_version": "MLB_GEN1_RESEARCH_POLICY_V1",
        },
        calibration={"kind": "platt"},
        prediction_artifact_uri=".runtime/gate3/gen1/evaluation/predictions.jsonl",
        prediction_artifact_checksum="4" * 64,
        evaluated_at=datetime(2026, 8, 14, 18, tzinfo=UTC),
        source_mode="LIVE",
        code_revision="test-revision",
        identity_checksum="5" * 64,
        superiority_claim=False,
        promotion_recommendation=False,
        notes="Gen-1 research evaluation completed without production promotion.",
    )
    session.add(evaluation)
    session.commit()
    return model, evaluation


def test_gen1_evaluation_accepts_strict_a2_with_derived_feature_lineage(
    client,
    session,
) -> None:
    operational = ModelVersion(
        name="asp_mlb_ensemble",
        version="2.0.0-operational",
        algorithm="operational_untrained_ensemble",
        status=ModelStatus.DEVELOPMENT,
    )
    session.add(operational)
    session.flush()
    model, _ = _gen1_evidence_graph(session)

    body = client.get("/api/v1/performance").json()
    offline = body["offline_evaluation"]
    assert offline["status"] == "AVAILABLE"
    assert offline["model_version"] == f"{model.name} v{model.version}"
    assert offline["evaluation_policy_version"] == "MLB_GEN1_RESEARCH_POLICY_V1"
    assert offline["dataset_checksum"] == session.get(
        TrainingDatasetManifest,
        model.training_manifest_id,
    ).identity_checksum
    assert offline["feature_set_checksum"] == "2" * 64
    assert offline["research_decision"] == "RESEARCH_CHALLENGER"
    assert "without production promotion" in offline["status_reason"]
    assert offline["holdout_metrics"]["brier_score"] == 0.24
    assert body["model_version"] == (
        f"{operational.name} v{operational.version} · awaiting settled sample"
    )


@pytest.mark.parametrize(
    ("population", "broken_lineage"),
    [
        ("TIER_B_ENHANCED", None),
        ("TIER_A_STRICT", "definition"),
        ("TIER_A_STRICT", "parameters"),
    ],
)
def test_gen1_evaluation_fails_closed_for_non_strict_or_broken_lineage(
    client,
    session,
    population: str,
    broken_lineage: str | None,
) -> None:
    _gen1_evidence_graph(
        session,
        population=population,
        broken_lineage=broken_lineage,
    )

    offline = client.get("/api/v1/performance").json()["offline_evaluation"]
    assert offline["status"] == "FAILED"
    assert "failed evidence-integrity checks" in offline["status_reason"]
    assert offline["validation_metrics"] is None
    assert offline["holdout_metrics"] is None
    assert offline["promotion"]["decision"] == "BLOCKED"
