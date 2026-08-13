from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.ingestion import CanonicalNormalizer
from app.models import FeatureVersion, ModelEvaluation, ModelVersion, TrainingDatasetManifest
from app.training.dataset import (
    DatasetBuildResult,
    GovernedTrainingDatasetBuilder,
    TrainingRow,
)
from app.training.pipeline import Gate3TrainingPipeline, TrainingDataIntegrityError


START = datetime(2024, 4, 1, 19, tzinfo=UTC)


def _row(
    index: int,
    feature_version: FeatureVersion,
    *,
    day: int | None = None,
    home_win: int | None = None,
    feature_version_label: str | None = None,
    feature_version_id: str | None = None,
    schema_version: str = "mlb_point_in_time_v2",
) -> TrainingRow:
    event_day = index if day is None else day
    event_at = START + timedelta(days=event_day, minutes=index % 7)
    data_as_of = event_at - timedelta(hours=12)
    outcome = index % 2 if home_win is None else home_win
    home_runs, away_runs = ((6, 3) if outcome else (3, 6))
    return TrainingRow(
        game_id=f"fail-closed-{index}",
        scheduled_first_pitch=event_at.isoformat().replace("+00:00", "Z"),
        data_as_of=data_as_of.isoformat().replace("+00:00", "Z"),
        lifecycle="PREGAME" if index % 2 else "MORNING",
        feature_snapshot_id=f"fail-closed-snapshot-{index}",
        feature_snapshot_checksum=f"{index + 1:064x}",
        feature_version_id=feature_version_id or str(feature_version.id),
        feature_version=(
            feature_version_label
            or f"{feature_version.name}/{feature_version.version}"
        ),
        feature_schema_version=schema_version,
        source_mode="LIVE",
        source_evidence=[],
        home_team_id=f"home-{index % 8}",
        away_team_id=f"away-{index % 8}",
        features={
            "home_rating": 1510.0 if outcome else 1490.0,
            "away_rating": 1490.0 if outcome else 1510.0,
            "home_runs_per_game": 4.8 if outcome else 4.1,
            "away_runs_per_game": 4.1 if outcome else 4.8,
            "home_runs_allowed_per_game": 4.0 if outcome else 4.7,
            "away_runs_allowed_per_game": 4.7 if outcome else 4.0,
            "home_starter_era": 3.7 if outcome else 4.6,
            "away_starter_era": 4.6 if outcome else 3.7,
            "home_recent_runs": 4.9 if outcome else 4.0,
            "away_recent_runs": 4.0 if outcome else 4.9,
            "park_run_factor": 1.0,
        },
        missing_features=[],
        stale_features=[],
        provisional_features=[],
        feature_completeness=0.85,
        home_win=outcome,
        home_runs=home_runs,
        away_runs=away_runs,
        run_differential=home_runs - away_runs,
        total_runs=home_runs + away_runs,
        market_home_probability=0.52,
        market_timestamp=data_as_of.isoformat().replace("+00:00", "Z"),
        market_books=5,
        closing_home_probability=None,
        closing_timestamp=None,
    )


def _built(session, rows: list[TrainingRow]) -> DatasetBuildResult:
    builder = GovernedTrainingDatasetBuilder(session)
    manifest = builder._manifest(
        rows,
        [],
        Counter(),
        START + timedelta(days=200),
        rows[0].feature_schema_version if rows else None,
    )
    return DatasetBuildResult(manifest=manifest, rows=tuple(rows), exclusions=())


def _install_build(monkeypatch, built: DatasetBuildResult) -> None:
    monkeypatch.setattr(
        GovernedTrainingDatasetBuilder,
        "build",
        lambda self, *, training_cutoff=None, feature_schema_version=None: built,
    )


def _assert_no_governance_rows(session) -> None:
    assert session.scalar(select(func.count()).select_from(TrainingDatasetManifest)) == 0
    assert session.scalar(select(func.count()).select_from(ModelVersion)) == 0
    assert session.scalar(select(func.count()).select_from(ModelEvaluation)) == 0


def test_failed_leakage_check_stops_before_artifact_manifest_or_fit(
    session,
    settings,
    artifact_root,
    monkeypatch,
) -> None:
    _, _, _, feature_version = CanonicalNormalizer(session).ensure_core(2025)
    session.commit()
    built = _built(session, [_row(0, feature_version)])
    built = replace(
        built,
        manifest=replace(
            built.manifest,
            leakage_checks={**built.manifest.leakage_checks, "pregame_cutoff": False},
        ),
    )
    _install_build(monkeypatch, built)
    root = artifact_root / "leakage-failure"
    controlled = settings.model_copy(update={"gate3_artifact_root": root})

    with pytest.raises(TrainingDataIntegrityError, match="pregame_cutoff"):
        Gate3TrainingPipeline(session, controlled).run(training_cutoff=START + timedelta(days=200))

    assert not root.exists()
    _assert_no_governance_rows(session)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("feature_version", "feature_version"),
        ("feature_schema", "feature_schema_version"),
        ("feature_version_id", "feature_version"),
    ],
)
def test_mixed_feature_identity_stops_before_artifact_or_manifest(
    mutation,
    message,
    session,
    settings,
    artifact_root,
    monkeypatch,
) -> None:
    _, _, _, feature_version = CanonicalNormalizer(session).ensure_core(2025)
    session.commit()
    first = _row(0, feature_version)
    second = _row(1, feature_version)
    if mutation == "feature_version":
        second = replace(second, feature_version="mlb_pregame/999.0.0")
    elif mutation == "feature_schema":
        second = replace(second, feature_schema_version="mlb_point_in_time_v999")
    else:
        second = replace(second, feature_version_id="00000000-0000-0000-0000-000000000999")
    built = _built(session, [first, second])
    _install_build(monkeypatch, built)
    root = artifact_root / f"mixed-{mutation}"
    controlled = settings.model_copy(update={"gate3_artifact_root": root})

    with pytest.raises(TrainingDataIntegrityError, match=message):
        Gate3TrainingPipeline(session, controlled).run(training_cutoff=START + timedelta(days=200))

    assert not root.exists()
    _assert_no_governance_rows(session)


def test_empty_diagnostic_manifest_binds_exact_contract_not_unrelated_latest(
    session,
    settings,
    artifact_root,
) -> None:
    _, _, _, governed = CanonicalNormalizer(session).ensure_core(2025)
    unrelated = FeatureVersion(
        name="mlb_pregame",
        version="999.0.0",
        schema_hash="9" * 64,
        definition={"purpose": "must never be substituted"},
    )
    session.add(unrelated)
    session.commit()
    controlled = settings.model_copy(update={"gate3_artifact_root": artifact_root / "exact-empty"})

    result = Gate3TrainingPipeline(session, controlled).run(training_cutoff=START)

    assert result.status == "INSUFFICIENT_DATA"
    persisted = session.get(TrainingDatasetManifest, UUID(result.manifest_id))
    assert persisted is not None
    assert persisted.feature_version_id == governed.id
    assert persisted.feature_version_id != unrelated.id


def _preflight_rows(feature_version: FeatureVersion, scenario: str) -> list[TrainingRow]:
    if scenario == "same_date":
        return [_row(index, feature_version, day=0) for index in range(140)]
    rows = [_row(index, feature_version) for index in range(70)]
    if scenario == "one_class":
        return [replace(row, home_win=1, home_runs=6, away_runs=3, run_differential=3, total_runs=9) for row in rows]
    if scenario == "fold_train_one_class":
        return [
            replace(row, home_win=0, home_runs=3, away_runs=6, run_differential=-3, total_runs=9)
            if index < 30
            else row
            for index, row in enumerate(rows)
        ]
    if scenario == "holdout_one_class":
        return [
            replace(row, home_win=1, home_runs=6, away_runs=3, run_differential=3, total_runs=9)
            if index >= 56
            else row
            for index, row in enumerate(rows)
        ]
    if scenario == "calibration_fit_one_class":
        return [
            replace(row, home_win=0, home_runs=3, away_runs=6, run_differential=-3, total_runs=9)
            if 37 <= index <= 45
            else row
            for index, row in enumerate(rows)
        ]
    return rows


@pytest.mark.parametrize(
    ("scenario", "settings_update", "failed_checks"),
    [
        ("same_date", {}, {"distinct_event_dates", "development_date_groups", "rolling_origin_folds"}),
        ("ordinary", {"gate3_min_holdout_rows": 20}, {"locked_holdout_rows"}),
        ("ordinary", {"gate3_rolling_folds": 4}, {"rolling_origin_folds"}),
        ("one_class", {}, {"overall_outcome_classes", "fold_outcome_classes"}),
        ("fold_train_one_class", {}, {"fold_outcome_classes"}),
        ("holdout_one_class", {}, {"holdout_outcome_classes"}),
        ("calibration_fit_one_class", {}, {"calibration_fit_outcome_classes"}),
    ],
)
def test_structured_preflight_returns_insufficient_without_model_fit(
    scenario,
    settings_update,
    failed_checks,
    session,
    settings,
    artifact_root,
    monkeypatch,
) -> None:
    _, _, _, feature_version = CanonicalNormalizer(session).ensure_core(2025)
    session.commit()
    built = _built(session, _preflight_rows(feature_version, scenario))
    _install_build(monkeypatch, built)

    def forbidden_fit(*args, **kwargs):
        raise AssertionError("Model fitting must not run after an insufficient preflight")

    monkeypatch.setattr("app.modeling.pipeline.run_temporal_model_development", forbidden_fit)
    controlled = settings.model_copy(update={
        "gate3_artifact_root": artifact_root / f"preflight-{scenario}",
        "gate3_min_training_rows": 20,
        "gate3_min_holdout_rows": 10,
        "gate3_rolling_folds": 3,
        **settings_update,
    })

    result = Gate3TrainingPipeline(session, controlled).run(
        training_cutoff=START + timedelta(days=200),
    )

    assert result.status == "INSUFFICIENT_DATA"
    assert result.promotion_decision == "NO_PROMOTION"
    assert result.model_version is None
    assert result.evaluation_id is None
    persisted = session.get(TrainingDatasetManifest, UUID(result.manifest_id))
    assert persisted is not None
    sufficiency = persisted.quality_summary["training_sufficiency"]
    assert sufficiency["status"] == "INSUFFICIENT_DATA"
    assert sufficiency["reason"] == result.promotion_reason
    assert failed_checks <= {
        name for name, check in sufficiency["checks"].items() if check["passed"] is False
    }
    assert session.scalar(select(func.count()).select_from(ModelVersion)) == 0
    assert session.scalar(select(func.count()).select_from(ModelEvaluation)) == 0


@dataclass
class _HoldoutPolicyFixture:
    classification: dict[str, float | int | None]
    home_runs_rmse: float | None
    away_runs_rmse: float | None
    total_runs_rmse: float | None
    comparison_classification: dict[str, dict[str, float | int | None]]
    comparison_run_metrics: dict[str, dict[str, float | int | None]]


def _promotion_fixtures(pipeline: Gate3TrainingPipeline):
    classification = {
        "observations": 300,
        "brier_score": 0.18,
        "log_loss": 0.55,
        "probability_rmse": 0.42,
        "expected_calibration_error": 0.03,
        "calibration_intercept": 0.02,
        "calibration_slope": 1.0,
    }
    baseline = {
        **classification,
        "brier_score": 0.21,
        "log_loss": 0.62,
    }
    run_baseline = {
        "observations": 300,
        "home_runs_rmse": 1.20,
        "away_runs_rmse": 1.20,
        "total_runs_rmse": 1.70,
    }
    development = SimpleNamespace(
        temporal_windows={"holdout_games": 300},
        holdout_metrics=_HoldoutPolicyFixture(
            classification=classification,
            home_runs_rmse=1.00,
            away_runs_rmse=1.00,
            total_runs_rmse=1.40,
            comparison_classification={"logistic": baseline, "home_prior": baseline},
            comparison_run_metrics={"simple_run_strength": run_baseline},
        ),
        paired_deltas_vs_stack={
            "logistic": SimpleNamespace(lower=0.001),
            "home_prior": SimpleNamespace(lower=0.001),
        },
        subgroup_metrics={
            "MORNING": {"observations": 120, "brier_score": 0.19, "log_loss": 0.59, "ece": 0.04},
            "PREGAME": {"observations": 120, "brier_score": 0.19, "log_loss": 0.59, "ece": 0.04},
        },
        oof_metrics={"stack_raw": {"brier_score": 0.18, "log_loss": 0.55}},
        hyperparameters={"poisson_eligible": True, "negative_binomial_eligible": True},
        ood_diagnostic=SimpleNamespace(flagged=False),
    )
    manifest = SimpleNamespace(
        leakage_checks={"all_governed_checks": True},
        feature_family_coverage={family: 0.90 for family in pipeline.REQUIRED_FEATURE_FAMILIES},
    )
    return manifest, development


def test_candidate_policy_passes_only_when_all_declared_evidence_is_available(
    session,
    settings,
) -> None:
    pipeline = Gate3TrainingPipeline(session, settings)
    manifest, development = _promotion_fixtures(pipeline)

    decision, reason = pipeline._promotion_decision(development, manifest)

    assert decision == "CANDIDATE_FOR_CHAMPION"
    assert "explicit human review" in reason


@pytest.mark.parametrize(
    ("case", "failed_criterion"),
    [
        ("coverage_unavailable", "feature_family_coverage"),
        ("coverage_below_threshold", "feature_family_coverage"),
        ("subgroups_unavailable", "subgroup_adequacy_and_stability"),
        ("subgroup_sparse", "subgroup_adequacy_and_stability"),
        ("subgroup_unstable", "subgroup_adequacy_and_stability"),
        ("intercept_unavailable", "calibration_intercept"),
        ("intercept_unstable", "calibration_intercept"),
        ("slope_unavailable", "calibration_slope"),
        ("slope_unstable", "calibration_slope"),
        ("run_baseline_unavailable", "run_model_vs_simple_baseline"),
        ("run_model_underperforms", "run_model_vs_simple_baseline"),
    ],
)
def test_candidate_policy_remains_challenger_for_failed_or_unavailable_evidence(
    case,
    failed_criterion,
    session,
    settings,
) -> None:
    pipeline = Gate3TrainingPipeline(session, settings)
    manifest, development = _promotion_fixtures(pipeline)
    manifest = deepcopy(manifest)
    development = deepcopy(development)
    if case == "coverage_unavailable":
        manifest.feature_family_coverage = {}
    elif case == "coverage_below_threshold":
        manifest.feature_family_coverage["lineup"] = settings.gate3_feature_min_coverage - 0.01
    elif case == "subgroups_unavailable":
        development.subgroup_metrics = {}
    elif case == "subgroup_sparse":
        development.subgroup_metrics["MORNING"]["observations"] = pipeline.MIN_SUBGROUP_ROWS - 1
    elif case == "subgroup_unstable":
        development.subgroup_metrics["MORNING"]["brier_score"] = 0.60
    elif case == "intercept_unavailable":
        development.holdout_metrics.classification["calibration_intercept"] = None
    elif case == "intercept_unstable":
        development.holdout_metrics.classification["calibration_intercept"] = 0.50
    elif case == "slope_unavailable":
        development.holdout_metrics.classification["calibration_slope"] = None
    elif case == "slope_unstable":
        development.holdout_metrics.classification["calibration_slope"] = 0.50
    elif case == "run_baseline_unavailable":
        development.holdout_metrics.comparison_run_metrics = {}
    else:
        development.holdout_metrics.comparison_run_metrics["simple_run_strength"]["total_runs_rmse"] = 1.20

    decision, reason = pipeline._promotion_decision(development, manifest)

    assert decision == "CHALLENGER_READY"
    assert failed_criterion in reason


def test_candidate_policy_allows_run_models_to_be_explicitly_excluded(
    session,
    settings,
) -> None:
    pipeline = Gate3TrainingPipeline(session, settings)
    manifest, development = _promotion_fixtures(pipeline)
    development.hyperparameters = {
        "poisson_eligible": False,
        "negative_binomial_eligible": False,
    }
    development.holdout_metrics.comparison_run_metrics = {}
    development.holdout_metrics.home_runs_rmse = None
    development.holdout_metrics.away_runs_rmse = None
    development.holdout_metrics.total_runs_rmse = None

    decision, _ = pipeline._promotion_decision(development, manifest)

    assert decision == "CANDIDATE_FOR_CHAMPION"
