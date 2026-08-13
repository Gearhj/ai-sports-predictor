"""Provider-free Gate 3 dataset build, model development, and evidence persistence."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version as dependency_version
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import FeatureVersion, ModelEvaluation, ModelStatus, ModelVersion, TrainingDatasetManifest
from app.training.artifacts import Gate3ModelArtifactStore
from app.training.dataset import GovernedTrainingDatasetBuilder, TrainingDatasetArtifactStore


class InsufficientTrainingDataError(RuntimeError):
    pass


class TrainingDataIntegrityError(RuntimeError):
    """Raised before persistence when governed training evidence is inconsistent."""


@dataclass(frozen=True)
class Gate3TrainingRunResult:
    status: str
    promotion_decision: str
    promotion_reason: str
    dataset_id: str
    manifest_checksum: str
    manifest_id: str
    row_count: int
    excluded_row_count: int
    exclusion_reasons: dict[str, int]
    model_version: str | None
    evaluation_id: str | None
    artifact_uri: str | None
    runtime_seconds: float
    provider_calls: int = 0


class Gate3TrainingPipeline:
    """Run local model development without importing or invoking any adapter."""

    POLICY_VERSION = "gate3-promotion-v1"
    FEATURE_VERSION_NAME = "mlb_pregame"
    FEATURE_VERSION = "2.0.0"
    MIN_DEVELOPMENT_DATE_GROUPS = 40
    MIN_TRAIN_DATE_GROUPS = 30
    VALIDATION_DATE_GROUPS = 7
    FOLD_STEP_DATE_GROUPS = 7
    CALIBRATION_SELECTION_FRACTION = 0.30
    MIN_SUBGROUP_ROWS = 30
    MAX_SUBGROUP_BRIER_DEGRADATION = 0.03
    MAX_SUBGROUP_LOG_LOSS_DEGRADATION = 0.10
    MAX_SUBGROUP_ECE = 0.10
    MAX_ABS_CALIBRATION_INTERCEPT = 0.10
    MIN_CALIBRATION_SLOPE = 0.80
    MAX_CALIBRATION_SLOPE = 1.20
    REQUIRED_FEATURE_FAMILIES = (
        "team_strength",
        "starting_pitcher",
        "lineup",
        "bullpen",
        "statcast",
        "recent_form",
        "park_weather",
        "rest_travel",
        "defense_baserunning",
        "opponent_adjustment",
    )

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def run(self, *, training_cutoff: datetime | None = None) -> Gate3TrainingRunResult:
        started = time.perf_counter()
        cutoff = training_cutoff or datetime.now(UTC)
        built = GovernedTrainingDatasetBuilder(self.session).build(training_cutoff=cutoff)
        self._validate_dataset_integrity(built)
        sufficiency = self._training_sufficiency(built.rows)
        dataset_store = TrainingDatasetArtifactStore(self.settings.gate3_artifact_root / "datasets")
        artifact_manifest = dataset_store.save(built)
        persisted = self._persist_manifest(artifact_manifest, built.exclusions, sufficiency)
        if sufficiency["status"] == "INSUFFICIENT_DATA":
            reason = str(sufficiency["reason"])
            self.session.commit()
            return Gate3TrainingRunResult(
                status="INSUFFICIENT_DATA",
                promotion_decision="NO_PROMOTION",
                promotion_reason=reason,
                dataset_id=artifact_manifest.dataset_id,
                manifest_checksum=artifact_manifest.checksum,
                manifest_id=str(persisted.id),
                row_count=artifact_manifest.row_count,
                excluded_row_count=artifact_manifest.excluded_row_count,
                exclusion_reasons=artifact_manifest.exclusion_reasons,
                model_version=None,
                evaluation_id=None,
                artifact_uri=artifact_manifest.artifact_uri,
                runtime_seconds=round(time.perf_counter() - started, 6),
            )

        from app.modeling.pipeline import ModelingConfig, run_temporal_model_development

        config = ModelingConfig(
            holdout_fraction=self.settings.gate3_holdout_fraction,
            minimum_holdout_rows=self.settings.gate3_min_holdout_rows,
            min_development_dates=self.MIN_DEVELOPMENT_DATE_GROUPS,
            min_train_dates=self.MIN_TRAIN_DATE_GROUPS,
            validation_dates=self.VALIDATION_DATE_GROUPS,
            step_dates=self.FOLD_STEP_DATE_GROUPS,
            minimum_rolling_folds=self.settings.gate3_rolling_folds,
            calibration_selection_fraction=self.CALIBRATION_SELECTION_FRACTION,
            random_seed=self.settings.gate3_random_seed,
            bootstrap_resamples=self.settings.gate3_bootstrap_iterations,
        )
        development = run_temporal_model_development(built.rows, config)
        evidence = _development_evidence(development)
        code_revision = _code_revision()
        dependencies = _dependencies()
        model_store = Gate3ModelArtifactStore(self.settings.gate3_artifact_root / "models")
        bundle = model_store.save(
            development.fitted_bundle,
            name="asp_mlb_gate3",
            algorithm="gate3_temporal_oof_calibrated_nonnegative_stack",
            target="home_win",
            dataset_id=artifact_manifest.dataset_id,
            dataset_checksum=artifact_manifest.checksum,
            feature_version=artifact_manifest.feature_version or "unknown",
            feature_schema_version=artifact_manifest.feature_schema_version or "unknown",
            feature_names=list(development.feature_names),
            training_window=evidence["training_window"],
            validation_window=evidence["validation_window"],
            holdout_window=evidence["holdout_window"],
            hyperparameters=dict(development.hyperparameters),
            calibrator=development.calibration.calibrator,
            calibration=evidence["calibration"],
            ensemble_weights=dict(development.ensemble_weights),
            simulation_configuration={
                "draws": self.settings.simulation_draws,
                "seed": self.settings.simulation_seed,
            },
            seed_policy={
                "training": self.settings.gate3_random_seed,
                "bootstrap": self.settings.gate3_random_seed + 1,
            },
            target_definition=artifact_manifest.target_definitions,
            code_revision=code_revision,
            dependency_versions=dependencies,
            metrics=evidence["metrics"],
            predictions=evidence["predictions"],
        )
        decision, reason = self._promotion_decision(development, built.manifest)
        model = self._persist_model(bundle, persisted, evidence, code_revision, decision, reason)
        evaluation = self._persist_evaluation(
            model,
            persisted,
            bundle,
            evidence,
            code_revision,
            decision,
            reason,
        )
        self.session.commit()
        return Gate3TrainingRunResult(
            status="SUCCEEDED",
            promotion_decision=decision,
            promotion_reason=reason,
            dataset_id=artifact_manifest.dataset_id,
            manifest_checksum=artifact_manifest.checksum,
            manifest_id=str(persisted.id),
            row_count=artifact_manifest.row_count,
            excluded_row_count=artifact_manifest.excluded_row_count,
            exclusion_reasons=artifact_manifest.exclusion_reasons,
            model_version=bundle.version,
            evaluation_id=str(evaluation.id),
            artifact_uri=model.artifact_uri,
            runtime_seconds=round(time.perf_counter() - started, 6),
        )

    def _persist_manifest(
        self,
        manifest: Any,
        exclusions: Any,
        training_sufficiency: dict[str, Any],
    ) -> TrainingDatasetManifest:
        existing = self.session.scalar(select(TrainingDatasetManifest).where(
            TrainingDatasetManifest.identity_checksum == manifest.checksum,
        ))
        if existing:
            return existing
        feature_version_id = self._resolve_feature_version(manifest).id
        event_start_at = _parse_optional(manifest.date_start) or _parse_optional(manifest.training_cutoff)
        event_end_at = _parse_optional(manifest.date_end, end=True) or event_start_at
        record = TrainingDatasetManifest(
            name="mlb_pregame_training",
            version=manifest.dataset_id,
            feature_version_id=feature_version_id,
            source_mode="LIVE",
            schema_version=manifest.feature_schema_version or "unknown",
            target="multi_target_mlb_game",
            target_definition=manifest.target_definitions,
            cutoff_policy={"training_cutoff": manifest.training_cutoff, "snapshot": "latest published pregame"},
            split_policy={"policy": "date_grouped_rolling_origin_with_untouched_holdout"},
            feature_names=manifest.feature_names,
            seasons=manifest.seasons,
            event_start_at=event_start_at,
            event_end_at=event_end_at,
            row_count=manifest.row_count,
            game_count=manifest.row_count,
            excluded_row_count=manifest.excluded_row_count,
            artifact_uri=manifest.artifact_uri,
            artifact_checksum=manifest.rows_checksum,
            source_evidence=[{"rows_checksum": manifest.rows_checksum}],
            quality_summary={
                "exclusion_reasons": manifest.exclusion_reasons,
                "leakage_checks": manifest.leakage_checks,
                "feature_family_coverage": manifest.feature_family_coverage,
                "training_sufficiency": training_sufficiency,
                "exclusions_checksum": hashlib.sha256(json.dumps(list(exclusions), sort_keys=True).encode()).hexdigest(),
            },
            code_revision=_code_revision(),
            identity_checksum=manifest.checksum,
            built_at=datetime.fromisoformat(manifest.created_at.replace("Z", "+00:00")),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def _validate_dataset_integrity(self, built: Any) -> None:
        rows = tuple(built.rows)
        manifest = built.manifest
        schemas = {str(row.feature_schema_version) for row in rows}
        versions = {str(row.feature_version) for row in rows}
        version_ids = {str(row.feature_version_id) for row in rows if row.feature_version_id}
        schema_mismatches = [
            item for item in built.exclusions
            if item.get("reason") == "feature_schema_mismatch"
        ]
        if schema_mismatches or len(schemas) > 1:
            raise TrainingDataIntegrityError(
                "Governed training rows contain mixed feature_schema_version evidence"
            )
        if rows and (len(versions) != 1 or "unknown" in versions or len(version_ids) != 1):
            raise TrainingDataIntegrityError(
                "Governed training rows must bind exactly one known feature_version and feature_version_id"
            )
        if rows and (
            manifest.feature_schema_version not in schemas
            or manifest.feature_version not in versions
            or str(manifest.feature_version_id) not in version_ids
        ):
            raise TrainingDataIntegrityError(
                "Governed training manifest feature identity does not match its rows"
            )
        failed_leakage_checks = sorted(
            name for name, passed in dict(manifest.leakage_checks).items() if passed is not True
        )
        if failed_leakage_checks:
            raise TrainingDataIntegrityError(
                "Governed training leakage checks failed: " + ", ".join(failed_leakage_checks)
            )
        # Resolve the exact registry row before writing even the immutable
        # dataset artifact. This prevents an unknown or conflicting feature ID
        # from leaving a seemingly governed manifest on disk.
        self._resolve_feature_version(manifest)

    def _resolve_feature_version(self, manifest: Any) -> FeatureVersion:
        feature_version: FeatureVersion | None = None
        if manifest.feature_version_id:
            try:
                feature_version_id = UUID(str(manifest.feature_version_id))
            except (TypeError, ValueError) as exc:
                raise TrainingDataIntegrityError(
                    "Governed training manifest has an invalid feature_version_id"
                ) from exc
            feature_version = self.session.get(FeatureVersion, feature_version_id)
            if feature_version is None:
                raise TrainingDataIntegrityError(
                    "Governed training manifest references an unknown feature_version_id"
                )
        elif manifest.feature_version:
            try:
                name, version = str(manifest.feature_version).rsplit("/", 1)
            except ValueError as exc:
                raise TrainingDataIntegrityError(
                    "Governed training manifest has an invalid feature_version label"
                ) from exc
            feature_version = self.session.scalar(select(FeatureVersion).where(
                FeatureVersion.name == name,
                FeatureVersion.version == version,
            ))
        elif manifest.row_count == 0:
            # Empty diagnostic builds contain no row from which to derive an ID.
            # Bind only the exact feature contract implemented by this pipeline;
            # never substitute the newest or otherwise unrelated registry row.
            feature_version = self.session.scalar(select(FeatureVersion).where(
                FeatureVersion.name == self.FEATURE_VERSION_NAME,
                FeatureVersion.version == self.FEATURE_VERSION,
            ))
        if feature_version is None:
            raise TrainingDataIntegrityError(
                "The governed training manifest does not resolve to one exact MLB feature version"
            )
        expected = f"{feature_version.name}/{feature_version.version}"
        if manifest.feature_version and str(manifest.feature_version) != expected:
            raise TrainingDataIntegrityError(
                "Governed training manifest feature_version label and ID disagree"
            )
        return feature_version

    def _training_sufficiency(self, rows: Any) -> dict[str, Any]:
        ordered = sorted(
            rows,
            key=lambda row: (_training_event_at(row), str(row.game_id)),
        )
        event_dates = sorted({_training_event_at(row).date() for row in ordered})
        holdout_date_count = max(1, math.ceil(len(event_dates) * self.settings.gate3_holdout_fraction)) if event_dates else 0
        split_index = len(event_dates) - holdout_date_count
        development_dates = event_dates[:max(0, split_index)]
        holdout_dates = set(event_dates[max(0, split_index):])
        development_rows = [row for row in ordered if _training_event_at(row).date() in set(development_dates)]
        holdout_rows = [row for row in ordered if _training_event_at(row).date() in holdout_dates]

        folds: list[dict[str, Any]] = []
        validation_start = self.MIN_TRAIN_DATE_GROUPS
        while validation_start + self.VALIDATION_DATE_GROUPS <= len(development_dates):
            train_dates = set(development_dates[:validation_start])
            validation_dates = set(
                development_dates[validation_start:validation_start + self.VALIDATION_DATE_GROUPS]
            )
            train_rows = [row for row in development_rows if _training_event_at(row).date() in train_dates]
            validation_rows = [row for row in development_rows if _training_event_at(row).date() in validation_dates]
            folds.append({
                "index": len(folds),
                "train_rows": train_rows,
                "validation_rows": validation_rows,
            })
            validation_start += self.FOLD_STEP_DATE_GROUPS

        base_oof_rows = [row for fold in folds for row in fold["validation_rows"]]
        prior_base_oof_rows: list[Any] = []
        stack_validation_folds: list[dict[str, Any]] = []
        for fold in folds:
            if (
                len(prior_base_oof_rows) >= 4
                and {int(row.home_win) for row in prior_base_oof_rows} == {0, 1}
            ):
                stack_validation_folds.append(fold)
            prior_base_oof_rows.extend(fold["validation_rows"])
        oof_rows = [row for fold in stack_validation_folds for row in fold["validation_rows"]]
        oof_dates = sorted({_training_event_at(row).date() for row in oof_rows})
        selection_date_count = max(
            1,
            math.ceil(len(oof_dates) * self.CALIBRATION_SELECTION_FRACTION),
        ) if oof_dates else 0
        calibration_fit_dates = set(oof_dates[:-selection_date_count]) if selection_date_count else set()
        calibration_fit_rows = [
            row for row in oof_rows
            if _training_event_at(row).date() in calibration_fit_dates
        ]

        fold_class_observations = [
            {
                "fold": fold["index"],
                "classes": sorted({int(row.home_win) for row in fold["train_rows"]}),
                "rows": len(fold["train_rows"]),
            }
            for fold in folds
        ]
        checks: dict[str, dict[str, Any]] = {
            "minimum_rows": _check(
                len(ordered) >= self.settings.gate3_min_training_rows,
                len(ordered),
                {"minimum": self.settings.gate3_min_training_rows},
            ),
            "distinct_event_dates": _check(
                len(event_dates) >= self.MIN_DEVELOPMENT_DATE_GROUPS + 1,
                len(event_dates),
                {"minimum": self.MIN_DEVELOPMENT_DATE_GROUPS + 1},
            ),
            "development_date_groups": _check(
                len(development_dates) >= self.MIN_DEVELOPMENT_DATE_GROUPS,
                len(development_dates),
                {"minimum": self.MIN_DEVELOPMENT_DATE_GROUPS},
            ),
            "locked_holdout_rows": _check(
                len(holdout_rows) >= self.settings.gate3_min_holdout_rows,
                len(holdout_rows),
                {"minimum": self.settings.gate3_min_holdout_rows},
            ),
            "holdout_outcome_classes": _check(
                {int(row.home_win) for row in holdout_rows} == {0, 1},
                sorted({int(row.home_win) for row in holdout_rows}),
                {"exact": [0, 1]},
            ),
            "rolling_origin_folds": _check(
                len(folds) >= self.settings.gate3_rolling_folds,
                len(folds),
                {"minimum": self.settings.gate3_rolling_folds},
            ),
            "stack_rolling_origin_folds": _check(
                bool(stack_validation_folds),
                len(stack_validation_folds),
                {"minimum": 1, "policy": "strict earlier-fold meta-model fit"},
            ),
            "overall_outcome_classes": _check(
                {int(row.home_win) for row in ordered} == {0, 1},
                sorted({int(row.home_win) for row in ordered}),
                {"exact": [0, 1]},
            ),
            "fold_outcome_classes": _check(
                bool(folds) and all(item["classes"] == [0, 1] for item in fold_class_observations),
                fold_class_observations,
                {"each_training_fold": [0, 1]},
            ),
            "oof_outcome_classes": _check(
                len(oof_rows) >= 4 and {int(row.home_win) for row in oof_rows} == {0, 1},
                {
                    "base_rows": len(base_oof_rows),
                    "cross_fitted_stack_rows": len(oof_rows),
                    "classes": sorted({int(row.home_win) for row in oof_rows}),
                },
                {"minimum_rows": 4, "exact_classes": [0, 1]},
            ),
            "calibration_fit_outcome_classes": _check(
                len(oof_dates) >= 3
                and len(calibration_fit_rows) >= 4
                and {int(row.home_win) for row in calibration_fit_rows} == {0, 1},
                {
                    "oof_date_groups": len(oof_dates),
                    "fit_rows": len(calibration_fit_rows),
                    "classes": sorted({int(row.home_win) for row in calibration_fit_rows}),
                },
                {"minimum_oof_date_groups": 3, "minimum_fit_rows": 4, "exact_classes": [0, 1]},
            ),
        }
        failed = [name for name, check in checks.items() if not check["passed"]]
        if failed:
            reason = "Training preflight is insufficient; failed checks: " + ", ".join(failed) + "."
            status = "INSUFFICIENT_DATA"
        else:
            reason = "All provider-free Gate 3 temporal, holdout, fold, and class preflight checks passed."
            status = "SUFFICIENT"
        return {"status": status, "reason": reason, "checks": checks}

    def _persist_model(
        self,
        bundle: Any,
        manifest: TrainingDatasetManifest,
        evidence: dict[str, Any],
        revision: str,
        decision: str,
        reason: str,
    ) -> ModelVersion:
        existing = self.session.scalar(select(ModelVersion).where(ModelVersion.identity_checksum == bundle.identity_checksum))
        if existing:
            return existing
        feature_version = self.session.get(FeatureVersion, manifest.feature_version_id) if manifest.feature_version_id else None
        model_root = self.settings.gate3_artifact_root.resolve()
        artifact_uri = (model_root / "models" / bundle.name / bundle.version).relative_to(model_root.parent).as_posix()
        record = ModelVersion(
            name=bundle.name,
            version=bundle.version,
            feature_version_id=feature_version.id if feature_version else None,
            algorithm=bundle.algorithm,
            target="home_win",
            training_manifest_id=manifest.id,
            status=ModelStatus.CANDIDATE if decision == "CANDIDATE_FOR_CHAMPION" else ModelStatus.CHALLENGER,
            training_window=bundle.training_window,
            validation_window=bundle.validation_window,
            holdout_window=bundle.holdout_window,
            hyperparameters=bundle.hyperparameters,
            artifact_uri=artifact_uri,
            artifact_checksum=bundle.model_sha256,
            calibrator_uri=(
                f"{artifact_uri}/{bundle.calibrator_filename}" if bundle.calibrator_filename else None
            ),
            calibrator_checksum=bundle.calibrator_sha256,
            parameters={"calibration": bundle.calibration},
            metrics=evidence["metrics"],
            ensemble_weights=bundle.ensemble_weights,
            code_revision=revision,
            identity_checksum=bundle.identity_checksum,
            feature_names=bundle.feature_names,
            seed_policy=bundle.seed_policy,
            target_definition=bundle.target_definition,
            promotion_decision=decision,
            promotion_reason=reason,
            trained_at=datetime.now(UTC),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def _persist_evaluation(
        self,
        model: ModelVersion,
        manifest: TrainingDatasetManifest,
        bundle: Any,
        evidence: dict[str, Any],
        revision: str,
        decision: str,
        reason: str,
    ) -> ModelEvaluation:
        identity_payload = {
            "model_identity": bundle.identity_checksum,
            "manifest_identity": manifest.identity_checksum,
            "metrics": evidence["metrics"],
            "baseline_metrics": evidence["baseline_metrics"],
            "slices": evidence["slices"],
            "calibration": evidence["calibration"],
            "prediction_artifact_checksum": bundle.predictions_sha256,
            "validation_window": evidence["validation_window"],
            "holdout_window": evidence["holdout_window"],
            "code_revision": revision,
        }
        identity = hashlib.sha256(
            json.dumps(
                identity_payload,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        existing = self.session.scalar(select(ModelEvaluation).where(ModelEvaluation.identity_checksum == identity))
        if existing:
            return existing
        record = ModelEvaluation(
            model_version_id=model.id,
            training_manifest_id=manifest.id,
            evaluation_type="ROLLING_ORIGIN_FINAL_HOLDOUT",
            status="COMPLETED",
            validation_window=evidence["validation_window"],
            holdout_window=evidence["holdout_window"],
            metrics=evidence["metrics"],
            baseline_metrics=evidence["baseline_metrics"],
            slice_metrics=evidence["slices"],
            calibration=evidence["calibration"],
            prediction_artifact_uri=(
                f"{model.artifact_uri}/{bundle.predictions_filename}" if bundle.predictions_filename else None
            ),
            prediction_artifact_checksum=bundle.predictions_sha256,
            evaluated_at=datetime.now(UTC),
            source_mode="LIVE",
            code_revision=revision,
            identity_checksum=identity,
            superiority_claim=False,
            promotion_recommendation=decision == "CANDIDATE_FOR_CHAMPION",
            notes=reason,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def _promotion_decision(self, development: Any, manifest: Any) -> tuple[str, str]:
        holdout = _as_mapping(development.holdout_metrics.classification)
        comparators = development.holdout_metrics.comparison_classification or {}
        logistic = _as_mapping(comparators["logistic"]) if "logistic" in comparators else {}
        home_prior = _as_mapping(comparators["home_prior"]) if "home_prior" in comparators else {}
        paired = dict(development.paired_deltas_vs_stack or {})
        intercept = holdout.get("calibration_intercept")
        slope = holdout.get("calibration_slope")
        coverage = dict(getattr(manifest, "feature_family_coverage", {}) or {})
        coverage_passed = all(
            family in coverage
            and _finite_number(coverage[family])
            and float(coverage[family]) >= self.settings.gate3_feature_min_coverage
            for family in self.REQUIRED_FEATURE_FAMILIES
        )
        criteria = {
            "leakage_and_reproducibility": bool(manifest.leakage_checks)
            and all(value is True for value in manifest.leakage_checks.values()),
            "promotion_holdout_rows": (
                development.temporal_windows.get("holdout_games", 0)
                >= self.settings.gate3_promotion_min_holdout_rows
            ),
            "logistic_brier": _metric_better(holdout, logistic, "brier_score"),
            "logistic_log_loss": _metric_better(holdout, logistic, "log_loss"),
            "home_prior_brier": _metric_better(holdout, home_prior, "brier_score"),
            "home_prior_log_loss": _metric_better(holdout, home_prior, "log_loss"),
            "feature_family_coverage": coverage_passed,
            "subgroup_adequacy_and_stability": self._subgroup_policy_passes(development),
            "calibration_ece": (
                _finite_number(holdout.get("expected_calibration_error"))
                and float(holdout["expected_calibration_error"])
                <= self.settings.gate3_calibration_ece_threshold
            ),
            "calibration_intercept": (
                _finite_number(intercept)
                and abs(float(intercept)) <= self.MAX_ABS_CALIBRATION_INTERCEPT
            ),
            "calibration_slope": (
                _finite_number(slope)
                and self.MIN_CALIBRATION_SLOPE <= float(slope) <= self.MAX_CALIBRATION_SLOPE
            ),
            "run_model_vs_simple_baseline": self._run_model_policy_passes(development),
            "ood_stability": not bool(getattr(development.ood_diagnostic, "flagged", True)),
            "paired_bootstrap_non_degradation": bool(paired) and all(
                _finite_number(getattr(delta, "lower", None)) and float(delta.lower) >= 0
                for delta in paired.values()
            ),
        }
        failed = [name for name, passed in criteria.items() if not passed]
        if not failed:
            return "CANDIDATE_FOR_CHAMPION", (
                f"All {self.POLICY_VERSION} machine-checkable criteria passed; explicit human review is still required."
            )
        return "CHALLENGER_READY", (
            f"A reproducible artifact exists, but {self.POLICY_VERSION} criteria failed or were unavailable: "
            + ", ".join(failed)
            + "."
        )

    def _subgroup_policy_passes(self, development: Any) -> bool:
        subgroups = dict(getattr(development, "subgroup_metrics", {}) or {})
        oof_metrics = dict(getattr(development, "oof_metrics", {}) or {})
        overall = _as_mapping(oof_metrics.get("stack_raw"))
        if len(subgroups) < 2 or not overall:
            return False
        required_overall = ("brier_score", "log_loss")
        if not all(_finite_number(overall.get(name)) for name in required_overall):
            return False
        for raw_metrics in subgroups.values():
            metrics = _as_mapping(raw_metrics)
            observations = metrics.get("observations")
            brier = metrics.get("brier_score")
            log_loss = metrics.get("log_loss")
            ece = metrics.get("ece", metrics.get("expected_calibration_error"))
            if not (
                isinstance(observations, int)
                and observations >= self.MIN_SUBGROUP_ROWS
                and _finite_number(brier)
                and _finite_number(log_loss)
                and _finite_number(ece)
                and float(brier) <= float(overall["brier_score"]) + self.MAX_SUBGROUP_BRIER_DEGRADATION
                and float(log_loss) <= float(overall["log_loss"]) + self.MAX_SUBGROUP_LOG_LOSS_DEGRADATION
                and float(ece) <= self.MAX_SUBGROUP_ECE
            ):
                return False
        return True

    @staticmethod
    def _run_model_policy_passes(development: Any) -> bool:
        hyperparameters = dict(getattr(development, "hyperparameters", {}) or {})
        run_model_included = any(
            bool(hyperparameters.get(name))
            for name in ("poisson_eligible", "negative_binomial_eligible")
        )
        if not run_model_included:
            return True
        holdout_report = development.holdout_metrics
        run_metrics = dict(getattr(holdout_report, "comparison_run_metrics", None) or {})
        candidate = _as_mapping(holdout_report)
        baseline = _as_mapping(run_metrics.get("simple_run_strength"))
        metric_names = ("home_runs_rmse", "away_runs_rmse", "total_runs_rmse")
        return bool(candidate) and bool(baseline) and all(
            _finite_number(candidate.get(name))
            and _finite_number(baseline.get(name))
            and float(candidate[name]) < float(baseline[name])
            for name in metric_names
        )


def _parse_optional(value: str | None, *, end: bool = False) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if end and len(value) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed


def _training_event_at(row: Any) -> datetime:
    raw = getattr(row, "scheduled_first_pitch", None)
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TrainingDataIntegrityError(
                "Governed training row has an invalid scheduled_first_pitch"
            ) from exc
    else:
        raise TrainingDataIntegrityError(
            "Governed training row is missing scheduled_first_pitch"
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TrainingDataIntegrityError(
            "Governed training row scheduled_first_pitch must be timezone-aware"
        )
    return parsed


def _check(passed: bool, observed: Any, required: Any) -> dict[str, Any]:
    return {"passed": bool(passed), "observed": observed, "required": required}


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        return asdict(value)
    except (TypeError, ValueError):
        return {}


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _metric_better(candidate: dict[str, Any], baseline: dict[str, Any], name: str) -> bool:
    return (
        _finite_number(candidate.get(name))
        and _finite_number(baseline.get(name))
        and float(candidate[name]) < float(baseline[name])
    )


def _development_evidence(development: Any) -> dict[str, Any]:
    """Translate the pure modeling result into JSON-safe registry evidence."""

    serialized = development.to_dict()
    windows = dict(development.temporal_windows)
    validation_metrics = asdict(development.oof_metrics["stack_raw"])
    holdout_report = asdict(development.holdout_metrics)
    holdout_metrics = {
        **dict(holdout_report["classification"]),
        "home_runs_rmse": holdout_report["home_runs_rmse"],
        "away_runs_rmse": holdout_report["away_runs_rmse"],
        "total_runs_rmse": holdout_report["total_runs_rmse"],
        "home_runs_mae": holdout_report["home_runs_mae"],
        "away_runs_mae": holdout_report["away_runs_mae"],
        "total_runs_mae": holdout_report["total_runs_mae"],
        "home_runs_poisson_deviance": holdout_report["home_runs_poisson_deviance"],
        "away_runs_poisson_deviance": holdout_report["away_runs_poisson_deviance"],
        "market_observations": holdout_report["market_observations"],
        "comparison_run_metrics": holdout_report.get("comparison_run_metrics"),
    }
    run_maes = [
        value
        for value in (holdout_report["home_runs_mae"], holdout_report["away_runs_mae"])
        if value is not None
    ]
    holdout_metrics["run_mae"] = sum(run_maes) / len(run_maes) if run_maes else None
    comparator_metrics = {
        name: asdict(metrics)
        for name, metrics in sorted((development.holdout_metrics.comparison_classification or {}).items())
    }
    if development.holdout_metrics.market_classification is not None:
        comparator_metrics["closing_market_matched"] = asdict(
            development.holdout_metrics.market_classification
        )
    validation_window = {
        "start_date": windows["oof_validation_start"],
        "end_date": windows["oof_validation_end"],
        "games": windows["oof_games"],
        "rolling_origin_folds": windows["stack_rolling_folds"],
        "base_oof_games": windows["base_oof_games"],
        "base_rolling_origin_folds": windows["rolling_folds"],
    }
    holdout_window = {
        "start_date": windows["first_holdout_date"],
        "end_date": windows["holdout_end"],
        "games": windows["holdout_games"],
        "locked": True,
    }
    training_window = {
        "start_date": windows["development_start"],
        "end_date": windows["development_end"],
        "games": windows["development_games"],
    }
    calibration = {
        "method": development.calibration.method,
        "selection_scores": dict(development.calibration_selection_scores),
        "calibration_fit_count": development.calibration.calibration_fit_count,
        "selection_count": development.calibration.selection_count,
        "total_validation_count": development.calibration.total_validation_count,
        "selection_started_at": development.calibration.selection_started_at.isoformat(),
        "fit_policy": "temporal out-of-fold validation only; locked holdout excluded",
    }
    predictions = [
        {"split": "ROLLING_ORIGIN_VALIDATION", **row}
        for row in serialized["oof_predictions"]
    ]
    holdout = serialized["holdout_predictions"]
    for index, game_id in enumerate(holdout["game_ids"]):
        predictions.append({
            "split": "LOCKED_HOLDOUT",
            "game_id": game_id,
            "base_probabilities": {
                name: values[index] for name, values in holdout["base_probabilities"].items()
            },
            "raw_stacked_probability": holdout["raw_stacked_probability"][index],
            "calibrated_probability": holdout["calibrated_probability"][index],
            "expected_home_runs": (
                holdout["expected_home_runs"][index]
                if holdout["expected_home_runs"] is not None else None
            ),
            "expected_away_runs": (
                holdout["expected_away_runs"][index]
                if holdout["expected_away_runs"] is not None else None
            ),
        })
    return {
        "training_window": training_window,
        "validation_window": validation_window,
        "holdout_window": holdout_window,
        "metrics": {
            "validation": validation_metrics,
            "holdout": holdout_metrics,
            "rolling_origin_folds": windows["stack_rolling_folds"],
            "base_rolling_origin_folds": windows["rolling_folds"],
            "base_oof_games": windows["base_oof_games"],
            "feature_schema_hash": development.feature_schema_hash,
            "run_distribution_diagnostic": (
                development.fitted_bundle.negative_binomial_model.dispersion_diagnostic
                if development.fitted_bundle.negative_binomial_model is not None
                else None
            ),
        },
        "baseline_metrics": comparator_metrics,
        "calibration": calibration,
        "slices": {
            "subgroups": serialized["subgroup_metrics"],
            "ablations": serialized["ablation_metrics"],
            "paired_deltas_vs_stack": serialized["paired_deltas_vs_stack"],
            "ood": serialized["ood_diagnostic"],
            "betting": serialized["betting_simulations"],
            "fold_audits": serialized["fold_audits"],
            "stack_fold_audits": serialized["stack_fold_audits"],
        },
        "predictions": predictions,
    }


def _code_revision() -> str:
    """Bind artifacts to both Git lineage and the exact local server sources.

    ``git rev-parse HEAD`` alone is ambiguous while a governed run is executed
    from a dirty worktree.  The content digest keeps that run reproducible and
    changes for tracked or untracked Python sources before they are committed.
    """

    repository_root = Path(__file__).resolve().parents[3]
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = "unavailable"

    digest = hashlib.sha256()
    source_files = sorted((repository_root / "server" / "app").rglob("*.py"))
    source_files.extend(sorted((repository_root / "server" / "alembic").rglob("*.py")))
    requirements = repository_root / "server" / "requirements.txt"
    if requirements.is_file():
        source_files.append(requirements)
    for path in sorted(set(source_files)):
        relative = path.relative_to(repository_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return f"{head}:{digest.hexdigest()}"


def _dependencies() -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("numpy", "scikit-learn", "xgboost", "joblib"):
        try:
            result[name] = dependency_version(name)
        except Exception:
            result[name] = "unavailable"
    return result
