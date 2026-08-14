from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.historical.a2_schema import A2_SCHEMA
from app.modeling.gen1 import (
    A2RunStrengthClassifier,
    GEN1_ANALYSIS_FAMILIES,
    GEN1_ANALYSIS_FAMILY_SOURCES,
    GEN1_RESEARCH_EVALUATED_STATUS,
    GEN1_RESEARCH_FROZEN_STATUS,
    GEN1_UNIVERSE_OUTPUT_COUNT,
    GEN1_UNIVERSE_RAW_FEATURE_COUNT,
    GEN1_UNIVERSE_SCHEMA,
    GEN1_UNIVERSE_SCHEMA_HASH,
    Gen1ResearchPolicy,
    _apply_frozen_research_decision_rule,
    evaluate_gen1_holdout,
    gen1_leave_one_family_out,
    recent_variant_definitions,
    run_gen1_development,
    split_gen1_rows,
    validate_strict_a2_covariates,
)
from app.modeling.metrics import ClassificationMetrics, PairedBootstrapDelta
from app.training.dataset import TrainingRow
from app.training.gate3b_artifacts import Gen1ArtifactError, Gen1ResearchArtifactStore


SOURCE_FEATURE_VERSION_ID = UUID("7cdfa80f-725d-4061-8ca5-5967d9ea5701")
DERIVED_FEATURE_VERSION_ID = UUID("11111111-2222-4333-8444-555555555555")


def _hex(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _policy() -> Gen1ResearchPolicy:
    return Gen1ResearchPolicy(
        minimum_rows=40,
        minimum_holdout_rows=8,
        minimum_event_dates=20,
        minimum_development_dates=15,
        minimum_train_dates=3,
        validation_dates=2,
        step_dates=2,
        minimum_rolling_folds=5,
        xgboost_minimum_rows=10_000,
        minimum_isotonic_samples=10_000,
        bootstrap_resamples=50,
        minimum_subgroup_rows=4,
    )


def _feature_values(day: int, game_number: int) -> tuple[dict[str, float], list[str]]:
    home_win = game_number == 1
    strength = 1.0 if home_win else -1.0
    overrides = {
        "home_elo_rating": 1500.0 + 55.0 * strength + day * 0.2,
        "away_elo_rating": 1500.0 - 35.0 * strength,
        "elo_expected_home_probability": 0.64 if home_win else 0.42,
        "home_offense_true_talent": 4.8 + 0.6 * strength,
        "away_offense_true_talent": 4.6 - 0.5 * strength,
        "home_run_prevention_true_talent": 4.3 - 0.4 * strength,
        "away_run_prevention_true_talent": 4.4 + 0.4 * strength,
        "home_runs_per_game_season": 4.7 + 0.5 * strength,
        "away_runs_per_game_season": 4.5 - 0.4 * strength,
        "home_runs_allowed_per_game_season": 4.4 - 0.3 * strength,
        "away_runs_allowed_per_game_season": 4.4 + 0.3 * strength,
        "home_runs_per_game_14d": 4.8 + 0.7 * strength,
        "away_runs_per_game_14d": 4.4 - 0.5 * strength,
        "home_runs_per_game_30d": 4.7 + 0.5 * strength,
        "away_runs_per_game_30d": 4.5 - 0.4 * strength,
        "home_bullpen_fatigue": 0.65 if day % 3 == 0 else 0.25,
        "away_bullpen_fatigue": 0.60 if day % 4 == 0 else 0.20,
        "home_days_since_previous_game": 1.0 if day % 2 else 2.0,
        "away_days_since_previous_game": 1.0 if day % 3 else 2.0,
        "home_travel_miles": 650.0 if day % 5 == 0 else 80.0,
        "away_travel_miles": 550.0 if day % 6 == 0 else 60.0,
        "doubleheader_game_number": 2.0 if day % 9 == 0 and game_number else 1.0,
        "minutes_to_first_pitch": 360.0,
        "cutoff_offset_hours": 6.0,
        "starters_confirmed": 0.0,
        "lineups_confirmed": 0.0,
    }
    result: dict[str, float] = {}
    missing: list[str] = []
    for spec in A2_SCHEMA.features:
        is_missing = spec.tier_b_only or (
            spec.recent_window_days in (14, 30) and day == 0
        )
        value = float(spec.neutral_value if is_missing else overrides.get(spec.name, spec.neutral_value))
        result[spec.name] = value
        result[spec.missing_indicator_name] = 1.0 if is_missing else 0.0
        if is_missing:
            missing.append(spec.name)
    assert tuple(result) == A2_SCHEMA.output_names
    return result, sorted(missing)


def synthetic_rows(days: int = 20) -> tuple[TrainingRow, ...]:
    start = datetime(2025, 4, 1, 17, tzinfo=UTC)
    rows: list[TrainingRow] = []
    for day in range(days):
        for game_number in range(2):
            game_id = f"gen1-{day:03d}-{game_number}"
            event_at = start + timedelta(days=day, hours=3 * game_number)
            features, missing = _feature_values(day, game_number)
            home_win = game_number
            home_runs, away_runs = ((6, 3) if home_win else (3, 5))
            rows.append(
                TrainingRow(
                    game_id=game_id,
                    scheduled_first_pitch=event_at.isoformat().replace("+00:00", "Z"),
                    data_as_of=(event_at - timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
                    lifecycle="HISTORICAL_A2_T_MINUS_6H",
                    feature_snapshot_id=f"snapshot-{game_id}",
                    feature_snapshot_checksum=_hex(f"snapshot:{game_id}"),
                    feature_version_id=str(SOURCE_FEATURE_VERSION_ID),
                    feature_version="mlb_historical_pregame_a2/a2-tier-a-strict-v6",
                    feature_schema_version="mlb_historical_pregame_a2_v2",
                    source_mode="LIVE",
                    source_evidence=[
                        {
                            "lineage_version": "gate3a_a2_compact_lineage_v1",
                            "attestation_identity_checksum": _hex(f"attestation:{game_id}"),
                            "canonical_set_checksum": _hex(f"canonical:{game_id}"),
                            "source_bundle_checksum": _hex(f"source:{game_id}"),
                            "target_excluded": True,
                            "temporal_valid": True,
                        }
                    ],
                    home_team_id=f"team-{(day + game_number) % 8}",
                    away_team_id=f"team-{(day + game_number + 3) % 8}",
                    features=features,
                    missing_features=missing,
                    stale_features=[],
                    provisional_features=[],
                    feature_completeness=1.0 - len(missing) / len(A2_SCHEMA.features),
                    home_win=home_win,
                    home_runs=home_runs,
                    away_runs=away_runs,
                    run_differential=home_runs - away_runs,
                    total_runs=home_runs + away_runs,
                    market_home_probability=None,
                    market_timestamp=None,
                    market_books=0,
                    closing_home_probability=None,
                    closing_timestamp=None,
                    temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                    historical_materialization_id=f"materialization-{game_id}",
                    historical_materialization_identity_checksum=_hex(f"materialization:{game_id}"),
                    historical_outcome_id=f"outcome-{game_id}",
                    historical_outcome_identity_checksum=_hex(f"outcome:{game_id}"),
                    historical_quality_tier="A",
                    historical_quality_score=0.95,
                    cutoff_policy_version="mlb_t_minus_6h_v1",
                    materializer_version="gate3a_a2_chronological_v6",
                    label_available_at="2026-08-14T00:00:00Z",
                )
            )
    return tuple(rows)


class _LabelGuard:
    def __init__(self, row: TrainingRow, *, raise_on_access: bool) -> None:
        self._row = row
        self._raise = raise_on_access

    def __getattr__(self, name: str):
        if self._raise and name in {"home_win", "home_runs", "away_runs"}:
            raise AssertionError(f"blind holdout label accessed: {name}")
        return getattr(self._row, name)


class _LabelCounter:
    def __init__(self, row: TrainingRow) -> None:
        self._row = row
        self.accesses = {"home_win": 0, "home_runs": 0, "away_runs": 0}

    def __getattr__(self, name: str):
        if name in self.accesses:
            self.accesses[name] += 1
        return getattr(self._row, name)


@pytest.fixture(scope="module")
def gen1_case():
    rows = synthetic_rows()
    result = run_gen1_development(
        rows,
        _policy(),
        source_dataset_rows_checksum="b" * 64,
        enable_xgboost=False,
        run_ablations=False,
    )
    return SimpleNamespace(rows=rows, result=result)


def test_gen1_frozen_universe_and_analysis_contract() -> None:
    assert GEN1_UNIVERSE_SCHEMA_HASH == "586a39dad2b0d4cd8f386cf6162b16694108aab7afc37e72faf1642f33ebfc4a"
    assert len(GEN1_UNIVERSE_SCHEMA.features) == GEN1_UNIVERSE_RAW_FEATURE_COUNT == 230
    assert len(GEN1_UNIVERSE_SCHEMA.output_names) == GEN1_UNIVERSE_OUTPUT_COUNT == 460
    assert set(item.family for item in GEN1_UNIVERSE_SCHEMA.features) == {
        "team", "talent", "rating", "recent", "hitting", "pitching", "bullpen", "schedule"
    }
    assert not any(item.family in {"lifecycle", "target_context"} for item in GEN1_UNIVERSE_SCHEMA.features)
    assert GEN1_ANALYSIS_FAMILY_SOURCES["team"] == ("team", "talent")
    assert tuple(item.name for item in gen1_leave_one_family_out(GEN1_UNIVERSE_SCHEMA)) == tuple(
        f"without_{name}" for name in GEN1_ANALYSIS_FAMILIES
    )
    variants = {item.name: item for item in recent_variant_definitions()}
    assert set(variants) == {"none", "raw", "bayesian"}
    assert not variants["none"].included_recent_names
    assert variants["raw"].included_recent_names
    assert variants["bayesian"].unmatched_bayesian_delta_names


def test_a2_covariate_contract_accepts_canonical_json_key_order() -> None:
    row = synthetic_rows(days=1)[0]
    alphabetic_features = dict(sorted(row.features.items()))
    assert len(alphabetic_features) == len(A2_SCHEMA.output_names) == 544
    assert tuple(alphabetic_features) != A2_SCHEMA.output_names

    canonical_json_shape = replace(row, features=alphabetic_features)
    validate_strict_a2_covariates(canonical_json_shape)
    assert GEN1_UNIVERSE_SCHEMA.matrix([canonical_json_shape.features]).tolist() == (
        GEN1_UNIVERSE_SCHEMA.matrix([row.features]).tolist()
    )

    missing_one = dict(alphabetic_features)
    missing_one.pop(A2_SCHEMA.output_names[0])
    with pytest.raises(ValueError, match="exact A2 output-name contract"):
        validate_strict_a2_covariates(replace(row, features=missing_one))


def test_split_never_dereferences_blind_holdout_labels() -> None:
    rows = synthetic_rows()
    guarded = tuple(
        _LabelGuard(row, raise_on_access=index >= len(rows) - 8)
        for index, row in enumerate(rows)
    )
    split = split_gen1_rows(guarded, _policy())
    assert len(split.holdout.holdout_game_ids) == 8
    assert len(split.development) == 32


def test_development_freezes_full_mandatory_evidence_without_holdout_labels(gen1_case) -> None:
    result = gen1_case.result
    assert result.status == GEN1_RESEARCH_FROZEN_STATUS
    assert result.holdout_labels_accessed is False
    assert result.provider_calls == 0
    assert len({audit.fold_index for audit in result.folds}) >= 5
    assert all(
        audit.train_started_at <= audit.trained_through < audit.validation_started_at
        <= audit.validation_ended_at
        and audit.train_rows == len(audit.train_game_ids)
        and audit.validation_rows == len(audit.validation_game_ids)
        for audit in result.folds
    )
    assert result.recent_variant.name in {"none", "raw", "bayesian"}
    assert sum(bool(value["selected"]) for value in result.recent_variant_evidence.values()) == 1
    assert all(
        "deltas_vs_none" in value and "fold_consistency_vs_none" in value
        for value in result.recent_variant_evidence.values()
    )
    assert result.xgboost_candidate_evidence["status"]["eligible"] is False
    assert set(result.calibration["candidate_evidence"]) == {"identity", "platt", "isotonic"}
    assert result.calibration["candidate_evidence"]["isotonic"]["status"] == "INELIGIBLE"
    assert {
        "brier_score", "log_loss", "expected_calibration_error",
        "calibration_intercept", "calibration_slope",
    }.issubset(result.calibration["candidate_evidence"]["identity"])
    assert result.missingness_comparison["training_only_neutral"]["validation_or_holdout_used_for_imputation"] is False
    assert result.missingness_comparison["xgboost_native_missing"]["status"] == "UNSUPPORTED"
    assert set(result.ablation_metrics) == {
        "without_rating", "without_team", "without_recent_form", "without_hitting",
        "without_pitching", "without_bullpen", "without_rest_travel",
    }
    permutation = result.explainability["oof_group_permutation_importance"]
    assert permutation["status"] == "SUPPORTED"
    assert set(permutation["groups"]) == set(GEN1_ANALYSIS_FAMILIES)
    assert result.explainability["run_distribution_contract"]["negative_binomial_is_independent_mean_glm"] is False
    assert result.negative_binomial_retention["action"] in {"RETAIN", "DROP"}
    assert set(result.development_run_metrics) == {
        "run_strength", "poisson", "negative_binomial"
    }
    assert result.explainability["logistic_contract"]["coefficient_count"] == len(
        result.selected_schema.output_names
    )
    assert result.metadata()["feature_contract_hash"]
    assert len(result.metadata()["feature_contract"]) == len(result.selected_schema.features)
    assert result.research_decision_rule.orm_status_mapping == {
        "RESEARCH_CANDIDATE": "CANDIDATE",
        "RESEARCH_CHALLENGER": "CHALLENGER",
        "REJECTED_GEN1": "DEVELOPMENT",
    }
    assert all(
        item["band"] in {"50_55", "55_60", "60_65", "65_70", "70_plus"}
        and "calibration_error" in item
        and item["status"] in {"SUPPORTED", "INSUFFICIENT_SAMPLE"}
        for item in result.probability_bands
    )
    first_game = result.split.holdout_game_ids[0]
    drivers = result.holdout_driver_summaries[first_game]
    assert drivers["status"] in {
        "SUPPORTED_SELECTED_MODEL", "SUPPLEMENTARY_ENSEMBLE_COMPONENT", "UNAVAILABLE"
    }
    assert "selected_model" in drivers
    assert drivers["ood"]["status"] in {"NORMAL", "CAUTION", "LOW_RELIABILITY"}
    assert {"baseline_probability", "final_probability", "top_positive", "top_negative"}.issubset(drivers)
    driver_contract = result.explainability["per_prediction_driver_contract"]
    assert driver_contract["selected_model"] == result.ensemble_decision.selected_name
    assert driver_contract["selected_model_driver_status"] == (
        "SUPPORTED_SELECTED_MODEL"
    )
    assert driver_contract["freeze_gate"]["passed"] is True
    assert driver_contract["record_status_counts"] == {
        "SUPPORTED_SELECTED_MODEL": len(result.split.holdout_game_ids)
    }
    assert driver_contract["records_with_nonempty_top_positive"] == len(
        result.split.holdout_game_ids
    )
    assert driver_contract["records_with_nonempty_top_negative"] == len(
        result.split.holdout_game_ids
    )


def test_selected_model_driver_branches_and_run_strength_reconciliation(
    gen1_case,
) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        A2RunStrengthClassifier(run_scale=0.0)
    with pytest.raises(ValueError, match="finite and positive"):
        A2RunStrengthClassifier(home_multiplier=float("nan"))
    result = gen1_case.result
    observation = result.split.observations[0]

    def selected_bundle(name: str):
        decision = replace(
            result.fitted_bundle.ensemble_decision,
            action=(
                "KEEP_ENSEMBLE"
                if name == "nonnegative_stack"
                else "DROP_TO_BEST_SINGLE"
            ),
            selected_name=name,
            best_single_name=(
                result.fitted_bundle.ensemble_decision.best_single_name
                if name == "nonnegative_stack"
                else name
            ),
        )
        return replace(result.fitted_bundle, ensemble_decision=decision)

    run_bundle = selected_bundle("run_strength")
    run_prediction = run_bundle.predict((observation,))
    run_driver = run_bundle.prediction_drivers(
        observation,
        frozen_predictions=run_prediction,
        frozen_prediction_index=0,
    )
    assert run_driver == run_bundle.prediction_drivers(
        observation,
        frozen_predictions=run_prediction,
        frozen_prediction_index=0,
    )
    assert run_driver["status"] == "SUPPORTED_SELECTED_MODEL"
    assert run_driver["method"] == "a2_run_strength_additive_log_odds_v1"
    assert run_driver["baseline_probability"] == 0.5
    assert run_driver["baseline_semantics"] == (
        "zero_component_algebraic_origin_not_evidence"
    )
    assert run_driver["top_positive"]
    assert run_driver["top_negative"]
    assert run_driver["reconciliation"]["passed"] is True
    assert run_driver["reconciliation"]["reconstructed_final_probability"] == (
        pytest.approx(run_driver["final_probability"], abs=1e-12)
    )
    assert run_driver["reconciliation"]["raw_terms_log_odds_sum"] == pytest.approx(
        run_driver["raw_selected_log_odds"], abs=1e-12
    )
    feature_terms = [
        item
        for item in run_driver["raw_log_odds_terms"]
        if item["term_type"] == "GOVERNED_FEATURE"
    ]
    assert [item["feature"] for item in feature_terms] == [
        "home_offense_true_talent",
        "away_offense_true_talent",
        "home_run_prevention_true_talent",
        "away_run_prevention_true_talent",
    ]
    assert all(
        item["source_missing"] in {True, False}
        and item["value_semantics"]
        in {"OBSERVED_RECONSTRUCTED_VALUE", "FROZEN_NEUTRAL_FOR_MISSING_SOURCE"}
        for item in feature_terms
    )
    json.dumps(run_driver, allow_nan=False, sort_keys=True)
    run_summary = run_bundle.require_prediction_driver_freeze_ready(
        {observation.game_id: run_driver}
    )
    assert run_summary["freeze_gate"]["passed"] is True
    assert run_summary["records_with_exact_reconciliation"] == 1

    tampered_runs = {
        **run_prediction.run_predictions,
        "run_strength": (
            (run_prediction.run_predictions["run_strength"][0][0] + 0.01,),
            run_prediction.run_predictions["run_strength"][1],
        ),
    }
    tampered_base = {
        **run_prediction.base_probabilities,
        "run_strength": (
            run_prediction.base_probabilities["run_strength"][0] + 0.01,
        ),
    }
    tampered_predictions = (
        replace(run_prediction, run_predictions=tampered_runs),
        replace(run_prediction, base_probabilities=tampered_base),
        replace(
            run_prediction,
            raw_selected_probability=(
                run_prediction.raw_selected_probability[0] + 0.01,
            ),
        ),
        replace(
            run_prediction,
            calibrated_probability=(
                run_prediction.calibrated_probability[0] + 0.01,
            ),
        ),
    )
    for tampered in tampered_predictions:
        with pytest.raises(ValueError, match="does not reconcile"):
            run_bundle.prediction_drivers(
                observation,
                frozen_predictions=tampered,
                frozen_prediction_index=0,
            )

    logistic_bundle = selected_bundle("logistic")
    logistic_prediction = logistic_bundle.predict((observation,))
    logistic_driver = logistic_bundle.prediction_drivers(
        observation,
        frozen_predictions=logistic_prediction,
        frozen_prediction_index=0,
    )
    assert logistic_driver["status"] == "SUPPORTED_SELECTED_MODEL"
    assert logistic_bundle.prediction_driver_contract()[
        "selected_model_driver_status"
    ] == "SUPPORTED_SELECTED_MODEL"
    assert logistic_bundle.prediction_driver_contract()[
        "exact_final_reconciliation_with_explicit_calibration_adjustment"
    ] is True
    assert logistic_driver["reconciliation"]["passed"] is True
    assert logistic_bundle.require_prediction_driver_freeze_ready(
        {observation.game_id: logistic_driver}
    )["freeze_gate"]["passed"] is True

    stack_bundle = selected_bundle("nonnegative_stack")
    stack_prediction = stack_bundle.predict((observation,))
    stack_driver = stack_bundle.prediction_drivers(
        observation,
        frozen_predictions=stack_prediction,
        frozen_prediction_index=0,
    )
    assert stack_driver["status"] == "SUPPLEMENTARY_ENSEMBLE_COMPONENT"
    stack_contract = stack_bundle.prediction_driver_contract()
    assert stack_contract["selected_model_driver_status"] == "UNAVAILABLE"
    assert stack_contract["baseline_final_top_positive_top_negative"] is False
    with pytest.raises(RuntimeError, match="pre-artifact freeze gate"):
        stack_bundle.require_prediction_driver_freeze_ready(
            {observation.game_id: stack_driver}
        )

    for selected_name in (
        "home_prior",
        "elo",
        "poisson",
        "negative_binomial",
        "team_eb",
        "xgboost",
    ):
        unavailable_bundle = selected_bundle(selected_name)
        unavailable = unavailable_bundle.prediction_drivers(
            observation,
            frozen_predictions=run_prediction,
            frozen_prediction_index=0,
        )
        assert unavailable == {
            "status": "UNAVAILABLE",
            "selected_model": selected_name,
            "reason": "selected_model_has_no_governed_local_driver_method",
            "baseline_probability": run_prediction.base_probabilities["home_prior"][0],
            "final_probability": run_prediction.calibrated_probability[0],
            "top_positive": [],
            "top_negative": [],
        }
        contract = unavailable_bundle.prediction_driver_contract()
        assert contract["selected_model_driver_status"] == "UNAVAILABLE"
        assert contract["method"] is None
        assert contract["baseline_final_top_positive_top_negative"] is False
        with pytest.raises(RuntimeError, match="pre-artifact freeze gate"):
            unavailable_bundle.require_prediction_driver_freeze_ready(
                {observation.game_id: unavailable}
            )


def test_explicit_holdout_evaluation_reads_each_target_once_and_applies_frozen_rule(gen1_case) -> None:
    result = gen1_case.result
    holdout_ids = set(result.split.holdout_game_ids)
    counted = [_LabelCounter(row) for row in gen1_case.rows if row.game_id in holdout_ids]
    evaluation = evaluate_gen1_holdout(
        result,
        counted,
        verified_source_dataset_rows_checksum="b" * 64,
    )
    assert evaluation.status == GEN1_RESEARCH_EVALUATED_STATUS
    assert evaluation.research_decision in {
        "RESEARCH_CANDIDATE", "RESEARCH_CHALLENGER", "REJECTED_GEN1"
    }
    assert evaluation.promotion_decision == "RESEARCH_ONLY_NO_PROMOTION"
    assert {"run_strength", "poisson"}.issubset(evaluation.run_metrics)
    if "negative_binomial" in evaluation.run_metrics:
        assert evaluation.run_metrics["negative_binomial"]["independent_negative_binomial_mean_fit"] is False
    assert "differential_rmse" in evaluation.run_metrics["poisson"]
    assert set(evaluation.candidate_metric_intervals) == {"log_loss", "brier"}
    assert {"accuracy", "roc_auc"}.issubset(evaluation.candidate_secondary_metrics)
    assert "home_favorite" in evaluation.subgroup_metrics or "away_favorite" in evaluation.subgroup_metrics
    assert all(counter.accesses == {"home_win": 1, "home_runs": 1, "away_runs": 1} for counter in counted)


def test_frozen_research_rule_rejects_clear_material_degradation(gen1_case) -> None:
    candidate = ClassificationMetrics(20, 0.40, 1.20, 0.63, 0.25, 0.30, 0.10)
    comparator = ClassificationMetrics(20, 0.24, 0.68, 0.49, 0.04, 0.01, 1.0)
    delta = PairedBootstrapDelta(-0.10, -0.20, -0.02, 0.95, 50, 4)
    decision, checks = _apply_frozen_research_decision_rule(
        gen1_case.result,
        candidate,
        {name: comparator for name in ("home_prior", "elo", "logistic")},
        {
            name: {"log_loss": delta, "brier": delta}
            for name in ("home_prior", "elo", "logistic")
        },
        {
            "run_strength": {
                "total_rmse": 1.0, "total_mae": 1.0,
                "differential_rmse": 1.0, "differential_mae": 1.0,
            },
            "poisson": {
                "total_rmse": 2.0, "total_mae": 2.0,
                "differential_rmse": 2.0, "differential_mae": 2.0,
            },
        },
    )
    assert decision == "REJECTED_GEN1"
    assert checks["not_grossly_degraded_vs_home_prior"] is False


def _freeze(
    store: Gen1ResearchArtifactStore,
    result,
    *,
    code_revision: str = "synthetic-test-revision",
) -> object:
    return store.freeze_candidate(
        result,
        dataset_id="mlb-gate3-a2-synthetic",
        dataset_identity_checksum="a" * 64,
        dataset_rows_checksum="b" * 64,
        source_feature_version_id=str(SOURCE_FEATURE_VERSION_ID),
        source_feature_version_checksum="c" * 64,
        derived_feature_version_id=str(DERIVED_FEATURE_VERSION_ID),
        derived_feature_version="gen1-v1-synthetic",
        derived_feature_version_checksum="d" * 64,
        code_revision=code_revision,
        baseline_code_revision="synthetic-baseline-head",
        dependency_versions={"numpy": "test", "scikit-learn": "test", "xgboost": "test", "joblib": "test"},
    )


def test_two_phase_artifact_is_tamper_evident_and_holdout_claim_is_single_use(
    gen1_case, tmp_path: Path
) -> None:
    store = Gen1ResearchArtifactStore(
        tmp_path / "gate3b",
        minimum_free_bytes_after_write=0,
    )
    frozen = _freeze(store, gen1_case.result)
    second_candidate = _freeze(
        store,
        gen1_case.result,
        code_revision="synthetic-test-revision-2",
    )
    assert second_candidate.artifact_identity_checksum != frozen.artifact_identity_checksum
    loaded, loaded_manifest = store.load_candidate(frozen.artifact_identity_checksum)
    assert loaded.candidate_identity_checksum == gen1_case.result.candidate_identity_checksum
    assert loaded_manifest.source_feature_version_id == str(SOURCE_FEATURE_VERSION_ID)
    assert loaded_manifest.derived_feature_version_id == str(DERIVED_FEATURE_VERSION_ID)
    holdout_ids = set(loaded.split.holdout_game_ids)
    holdout = [row for row in gen1_case.rows if row.game_id in holdout_ids]
    claim = store.claim_holdout_evaluation(frozen.artifact_identity_checksum)
    evaluation = evaluate_gen1_holdout(
        loaded,
        holdout,
        verified_source_dataset_rows_checksum="b" * 64,
    )
    evaluated = store.complete_holdout_evaluation(claim, evaluation)
    assert evaluated.status == GEN1_RESEARCH_EVALUATED_STATUS
    assert evaluation.promotion_decision == "RESEARCH_ONLY_NO_PROMOTION"
    with pytest.raises(Gen1ArtifactError, match="consumed global claim"):
        store.claim_holdout_evaluation(second_candidate.artifact_identity_checksum)

    evaluation_predictions = (
        tmp_path
        / "gate3b"
        / "evaluations"
        / frozen.artifact_identity_checksum
        / evaluated.predictions_filename
    )
    original_evaluation_predictions = evaluation_predictions.read_bytes()
    evaluation_predictions.write_bytes(original_evaluation_predictions + b"tampered")
    with pytest.raises(Gen1ArtifactError, match="predictions checksum mismatch"):
        store.load_evaluation(frozen.artifact_identity_checksum)
    evaluation_predictions.write_bytes(original_evaluation_predictions)
    evaluation_object = (
        tmp_path
        / "gate3b"
        / "evaluations"
        / frozen.artifact_identity_checksum
        / evaluated.evaluation_filename
    )
    evaluation_object.write_bytes(evaluation_object.read_bytes() + b"tampered")
    with pytest.raises(Gen1ArtifactError, match="evaluation object checksum mismatch"):
        store.load_evaluation(frozen.artifact_identity_checksum)

    model_path = (
        tmp_path / "gate3b" / "candidates" / frozen.artifact_identity_checksum / "development.joblib"
    )
    model_path.write_bytes(model_path.read_bytes() + b"tampered")
    with pytest.raises(Gen1ArtifactError, match="model artifact checksum mismatch"):
        store.load_candidate(frozen.artifact_identity_checksum)
