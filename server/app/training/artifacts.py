"""Immutable, checksum-bound Gate 3 model bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import joblib


_SAFE_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class Gate3ArtifactManifest:
    name: str
    version: str
    identity_checksum: str
    created_at: str
    algorithm: str
    target: str
    dataset_id: str
    dataset_checksum: str
    feature_version: str
    feature_schema_version: str
    feature_names: list[str]
    training_window: dict[str, Any]
    validation_window: dict[str, Any]
    holdout_window: dict[str, Any]
    hyperparameters: dict[str, Any]
    calibration: dict[str, Any]
    ensemble_weights: dict[str, float]
    simulation_configuration: dict[str, Any]
    seed_policy: dict[str, Any]
    target_definition: dict[str, Any]
    code_revision: str
    dependency_versions: dict[str, str]
    model_filename: str
    model_sha256: str
    calibrator_filename: str | None
    calibrator_sha256: str | None
    metrics_filename: str
    metrics_sha256: str
    predictions_filename: str | None
    predictions_sha256: str | None


class Gate3ModelArtifactStore:
    """Store and load only internally generated, root-confined bundles."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def save(
        self,
        model: object,
        *,
        name: str,
        algorithm: str,
        target: str,
        dataset_id: str,
        dataset_checksum: str,
        feature_version: str,
        feature_schema_version: str,
        feature_names: list[str],
        training_window: dict[str, Any],
        validation_window: dict[str, Any],
        holdout_window: dict[str, Any],
        hyperparameters: dict[str, Any],
        calibrator: object | None,
        calibration: dict[str, Any],
        ensemble_weights: dict[str, float],
        simulation_configuration: dict[str, Any],
        seed_policy: dict[str, Any],
        target_definition: dict[str, Any],
        code_revision: str,
        dependency_versions: dict[str, str],
        metrics: dict[str, Any],
        predictions: list[dict[str, Any]] | None = None,
    ) -> Gate3ArtifactManifest:
        safe_name = self._segment(name)
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.root / f".model-{uuid4().hex}"
        staging.mkdir(mode=0o777, exist_ok=False)
        try:
            model_path = staging / "model.joblib"
            calibrator_path = staging / "calibrator.joblib"
            metrics_path = staging / "metrics.json"
            predictions_path = staging / "predictions.jsonl"
            joblib.dump(model, model_path)
            self._sync(model_path)
            model_checksum = _sha256(model_path)
            calibrator_checksum: str | None = None
            calibrator_filename: str | None = None
            if calibrator is not None:
                joblib.dump(calibrator, calibrator_path)
                self._sync(calibrator_path)
                calibrator_checksum = _sha256(calibrator_path)
                calibrator_filename = calibrator_path.name
            metrics_path.write_bytes(_canonical(metrics))
            self._sync(metrics_path)
            predictions_checksum: str | None = None
            predictions_filename: str | None = None
            if predictions is not None:
                with predictions_path.open("xb") as handle:
                    for row in predictions:
                        handle.write(_canonical(row) + b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                predictions_checksum = _sha256(predictions_path)
                predictions_filename = predictions_path.name

            identity_input = {
                "algorithm": algorithm,
                "target": target,
                "dataset_id": dataset_id,
                "dataset_checksum": dataset_checksum,
                "feature_version": feature_version,
                "feature_schema_version": feature_schema_version,
                "feature_names": feature_names,
                "training_window": training_window,
                "validation_window": validation_window,
                "holdout_window": holdout_window,
                "hyperparameters": hyperparameters,
                "model_sha256": model_checksum,
                "calibrator_sha256": calibrator_checksum,
                "calibration": calibration,
                "ensemble_weights": ensemble_weights,
                "simulation_configuration": simulation_configuration,
                "seed_policy": seed_policy,
                "target_definition": target_definition,
                "code_revision": code_revision,
                "dependency_versions": dependency_versions,
            }
            identity = hashlib.sha256(_canonical(identity_input)).hexdigest()
            version = f"3.0.0-{identity[:12]}"
            final = self._contained(self.root / safe_name / version)
            manifest = Gate3ArtifactManifest(
                name=safe_name,
                version=version,
                identity_checksum=identity,
                created_at=datetime.now(UTC).isoformat(),
                algorithm=algorithm,
                target=target,
                dataset_id=dataset_id,
                dataset_checksum=dataset_checksum,
                feature_version=feature_version,
                feature_schema_version=feature_schema_version,
                feature_names=list(feature_names),
                training_window=training_window,
                validation_window=validation_window,
                holdout_window=holdout_window,
                hyperparameters=hyperparameters,
                calibration=calibration,
                ensemble_weights=ensemble_weights,
                simulation_configuration=simulation_configuration,
                seed_policy=seed_policy,
                target_definition=target_definition,
                code_revision=code_revision,
                dependency_versions=dependency_versions,
                model_filename=model_path.name,
                model_sha256=model_checksum,
                calibrator_filename=calibrator_filename,
                calibrator_sha256=calibrator_checksum,
                metrics_filename=metrics_path.name,
                metrics_sha256=_sha256(metrics_path),
                predictions_filename=predictions_filename,
                predictions_sha256=predictions_checksum,
            )
            (staging / "manifest.json").write_text(
                json.dumps(asdict(manifest), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            self._sync(staging / "manifest.json")
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                existing = self._read_manifest(final)
                if existing.identity_checksum != identity:
                    raise FileExistsError(f"Model version collision at {final}")
                shutil.rmtree(staging, ignore_errors=True)
                return existing
            os.replace(staging, final)
            return manifest
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def load(self, name: str, version: str) -> tuple[object, object | None, Gate3ArtifactManifest]:
        directory = self._contained(self.root / self._segment(name) / self._version(version))
        manifest = self._read_manifest(directory)
        model_path = self._contained(directory / manifest.model_filename)
        if _sha256(model_path) != manifest.model_sha256:
            raise ValueError("Model artifact checksum mismatch")
        calibrator = None
        if manifest.calibrator_filename:
            calibrator_path = self._contained(directory / manifest.calibrator_filename)
            if _sha256(calibrator_path) != manifest.calibrator_sha256:
                raise ValueError("Calibrator artifact checksum mismatch")
            calibrator = joblib.load(calibrator_path)
        metrics_path = self._contained(directory / manifest.metrics_filename)
        if _sha256(metrics_path) != manifest.metrics_sha256:
            raise ValueError("Evaluation artifact checksum mismatch")
        if manifest.predictions_filename:
            predictions_path = self._contained(directory / manifest.predictions_filename)
            if _sha256(predictions_path) != manifest.predictions_sha256:
                raise ValueError("Prediction artifact checksum mismatch")
        return joblib.load(model_path), calibrator, manifest

    def _read_manifest(self, directory: Path) -> Gate3ArtifactManifest:
        path = self._contained(directory / "manifest.json")
        return Gate3ArtifactManifest(**json.loads(path.read_text(encoding="utf-8")))

    def _contained(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("Artifact path escapes the governed root")
        return resolved

    @staticmethod
    def _segment(value: str) -> str:
        if not _SAFE_SEGMENT.fullmatch(value):
            raise ValueError("Model name must be a lowercase safe path segment")
        return value

    @staticmethod
    def _version(value: str) -> str:
        if not re.fullmatch(r"3\.0\.0-[0-9a-f]{12}", value):
            raise ValueError("Invalid Gate 3 model version")
        return value

    @staticmethod
    def _sync(path: Path) -> None:
        with path.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
