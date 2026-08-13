from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib


@dataclass(frozen=True)
class ModelArtifactManifest:
    name: str
    version: str
    algorithm: str
    created_at: str
    feature_names: list[str]
    training_window: dict[str, str]
    hyperparameters: dict[str, Any]
    evaluation_metrics: dict[str, float]
    artifact_sha256: str
    artifact_filename: str


class ModelArtifactStore:
    """Versioned, checksum-verified model serialization; never overwrites artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(
        self,
        model: object,
        *,
        name: str,
        version: str,
        algorithm: str,
        feature_names: list[str],
        training_window: dict[str, str],
        hyperparameters: dict[str, Any],
        evaluation_metrics: dict[str, float],
    ) -> ModelArtifactManifest:
        directory = self.root / name / version
        directory.mkdir(parents=True, exist_ok=True)
        artifact_path = directory / "model.joblib"
        manifest_path = directory / "manifest.json"
        if artifact_path.exists() or manifest_path.exists():
            raise FileExistsError(f"Model artifact {name}/{version} already exists")
        joblib.dump(model, artifact_path)
        checksum = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        manifest = ModelArtifactManifest(
            name=name,
            version=version,
            algorithm=algorithm,
            created_at=datetime.now(UTC).isoformat(),
            feature_names=feature_names,
            training_window=training_window,
            hyperparameters=hyperparameters,
            evaluation_metrics=evaluation_metrics,
            artifact_sha256=checksum,
            artifact_filename=artifact_path.name,
        )
        manifest_path.write_text(json.dumps(asdict(manifest), indent=2, sort_keys=True), encoding="utf-8")
        return manifest

    def load(self, name: str, version: str) -> tuple[object, ModelArtifactManifest]:
        directory = self.root / name / version
        manifest = ModelArtifactManifest(**json.loads((directory / "manifest.json").read_text(encoding="utf-8")))
        artifact_path = directory / manifest.artifact_filename
        actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if actual != manifest.artifact_sha256:
            raise ValueError("Model artifact checksum mismatch")
        return joblib.load(artifact_path), manifest
