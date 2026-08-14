"""Immutable two-phase artifacts for MLB Gen-1 research development.

Candidate freeze and final-holdout evaluation are intentionally separate.
The evaluation directory is created exclusively before labels are resolved, so
an exact frozen candidate/holdout identity can be evaluated at most once by
this governed store.  A failed claim remains consumed and requires a new
candidate identity rather than silently retrying after label access.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping
from uuid import UUID, uuid4

import joblib

from app.historical.a2_schema import A2_SCHEMA_HASH, A2_SCHEMA_VERSION
from app.modeling.gen1 import (
    GEN1_RESEARCH_EVALUATED_STATUS,
    GEN1_RESEARCH_FROZEN_STATUS,
    GEN1_UNIVERSE_SCHEMA_HASH,
    GEN1_FEATURE_CONTRACT_VERSION,
    Gen1DevelopmentResult,
    Gen1HoldoutEvaluation,
    MLB_GEN1_RESEARCH_POLICY_VERSION,
)


DEFAULT_GEN1_MAXIMUM_ARTIFACT_BYTES = 128 * 1024 * 1024
DEFAULT_GEN1_MINIMUM_FREE_BYTES_AFTER_WRITE = 100 * 1024 * 1024 * 1024
GEN1_EVALUATION_CLAIMED_STATUS = "RESEARCH_HOLDOUT_EVALUATION_CLAIMED"
GEN1_EVALUATION_FAILED_STATUS = "RESEARCH_HOLDOUT_EVALUATION_FAILED_CLAIM_CONSUMED"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Gen1ArtifactError(RuntimeError):
    """Raised when immutable Gen-1 artifacts fail closed."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    ).encode("utf-8")


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _checksum_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _checksum(value: object) -> str:
    return _checksum_bytes(_canonical(value))


def _file_checksum(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise Gen1ArtifactError(f"{label} must be a lowercase SHA-256")
    return value


def _sync(path: Path) -> None:
    with path.open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())


@dataclass(frozen=True, slots=True)
class Gen1FrozenArtifactManifest:
    status: str
    artifact_identity_checksum: str
    development_identity_checksum: str
    created_at: str
    research_policy_version: str
    dataset_id: str
    dataset_identity_checksum: str
    dataset_rows_checksum: str
    source_feature_version_id: str
    source_feature_version_checksum: str
    source_feature_schema_version: str
    source_feature_schema_hash: str
    feature_contract_version: str
    feature_contract_hash: str
    derived_feature_version_id: str
    derived_feature_version: str
    derived_feature_version_checksum: str
    derived_feature_schema_version: str
    derived_feature_schema_hash: str
    model_input_schema_version: str
    model_input_schema_hash: str
    universe_feature_schema_hash: str
    holdout_identity_checksum: str
    holdout_labels_accessed: bool
    provider_calls: int
    code_revision: str
    baseline_code_revision: str
    dependency_versions: dict[str, str]
    model_filename: str
    model_sha256: str
    development_filename: str
    development_sha256: str
    predictions_filename: str
    predictions_sha256: str
    total_artifact_bytes: int


@dataclass(frozen=True, slots=True)
class Gen1EvaluationClaim:
    status: str
    claim_identity_checksum: str
    candidate_artifact_identity_checksum: str
    development_identity_checksum: str
    source_dataset_rows_checksum: str
    holdout_identity_checksum: str
    global_holdout_claim_identity_checksum: str
    claimed_at: str
    claim_uri: str
    global_claim_uri: str


@dataclass(frozen=True, slots=True)
class Gen1EvaluationArtifactManifest:
    status: str
    artifact_identity_checksum: str
    evaluation_identity_checksum: str
    candidate_artifact_identity_checksum: str
    development_identity_checksum: str
    holdout_identity_checksum: str
    claim_identity_checksum: str
    evaluated_at: str
    promotion_decision: str
    provider_calls: int
    evaluation_filename: str
    evaluation_sha256: str
    metrics_filename: str
    metrics_sha256: str
    predictions_filename: str
    predictions_sha256: str
    total_artifact_bytes: int


class Gen1ResearchArtifactStore:
    """Root-confined atomic store for frozen candidates and one holdout result."""

    def __init__(
        self,
        root: Path,
        *,
        maximum_artifact_bytes: int = DEFAULT_GEN1_MAXIMUM_ARTIFACT_BYTES,
        minimum_free_bytes_after_write: int = DEFAULT_GEN1_MINIMUM_FREE_BYTES_AFTER_WRITE,
    ) -> None:
        if maximum_artifact_bytes <= 0 or minimum_free_bytes_after_write < 0:
            raise ValueError("Gen-1 artifact storage limits are invalid")
        self.root = root.resolve()
        self.maximum_artifact_bytes = int(maximum_artifact_bytes)
        self.minimum_free_bytes_after_write = int(minimum_free_bytes_after_write)

    def freeze_candidate(
        self,
        development: Gen1DevelopmentResult,
        *,
        dataset_id: str,
        dataset_identity_checksum: str,
        dataset_rows_checksum: str,
        source_feature_version_id: str,
        source_feature_version_checksum: str,
        derived_feature_version_id: str,
        derived_feature_version: str,
        derived_feature_version_checksum: str,
        code_revision: str,
        baseline_code_revision: str,
        dependency_versions: Mapping[str, str],
    ) -> Gen1FrozenArtifactManifest:
        if development.status != GEN1_RESEARCH_FROZEN_STATUS:
            raise Gen1ArtifactError("Only a frozen Gen-1 development result may be stored")
        if development.holdout_labels_accessed or development.provider_calls != 0:
            raise Gen1ArtifactError("Candidate freeze detected holdout/provider scope leakage")
        if dataset_rows_checksum != development.source_dataset_rows_checksum:
            raise Gen1ArtifactError(
                "Candidate source rows checksum does not match the blinded development lock"
            )
        for label, value in (
            ("dataset_identity_checksum", dataset_identity_checksum),
            ("dataset_rows_checksum", dataset_rows_checksum),
            ("source_feature_version_checksum", source_feature_version_checksum),
            ("derived_feature_version_checksum", derived_feature_version_checksum),
            ("development_identity_checksum", development.candidate_identity_checksum),
            ("holdout_identity_checksum", development.split.holdout_identity_checksum),
        ):
            _require_sha256(value, label)
        try:
            UUID(str(source_feature_version_id))
            UUID(str(derived_feature_version_id))
        except (TypeError, ValueError) as exc:
            raise Gen1ArtifactError("Source and derived feature-version IDs must be UUIDs") from exc
        if (
            not dataset_id
            or not code_revision
            or not baseline_code_revision
            or not derived_feature_version
        ):
            raise Gen1ArtifactError("Dataset and code identities are required")

        self.root.mkdir(parents=True, exist_ok=True)
        staging = self._contained(self.root / f".candidate-{uuid4().hex}")
        staging.mkdir(mode=0o777, exist_ok=False)
        try:
            model_path = staging / "development.joblib"
            metadata_path = staging / "development.json"
            predictions_path = staging / "holdout_predictions.jsonl"
            joblib.dump(development, model_path)
            _sync(model_path)
            metadata_bytes = _canonical(development.metadata())
            metadata_path.write_bytes(metadata_bytes)
            _sync(metadata_path)
            with predictions_path.open("xb") as handle:
                for index, game_id in enumerate(development.holdout_predictions.game_ids):
                    row = {
                        "game_id": game_id,
                        "base_probabilities": {
                            name: values[index]
                            for name, values in development.holdout_predictions.base_probabilities.items()
                        },
                        "raw_selected_probability": (
                            development.holdout_predictions.raw_selected_probability[index]
                        ),
                        "calibrated_probability": (
                            development.holdout_predictions.calibrated_probability[index]
                        ),
                        "run_predictions": {
                            name: {
                                "home": values[0][index],
                                "away": values[1][index],
                            }
                            for name, values in development.holdout_predictions.run_predictions.items()
                        },
                        "drivers": development.holdout_driver_summaries[game_id],
                        "outcome": None,
                        "label_accessed": False,
                    }
                    handle.write(_canonical(row) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            model_checksum = _file_checksum(model_path)
            metadata_checksum = _file_checksum(metadata_path)
            predictions_checksum = _file_checksum(predictions_path)
            created_at = datetime.now(UTC).isoformat()
            semantic = {
                "status": GEN1_RESEARCH_FROZEN_STATUS,
                "development_identity_checksum": development.candidate_identity_checksum,
                "research_policy_version": MLB_GEN1_RESEARCH_POLICY_VERSION,
                "dataset_id": dataset_id,
                "dataset_identity_checksum": dataset_identity_checksum,
                "dataset_rows_checksum": dataset_rows_checksum,
                "source_feature_version_id": str(source_feature_version_id),
                "source_feature_version_checksum": source_feature_version_checksum,
                "source_feature_schema_version": A2_SCHEMA_VERSION,
                "source_feature_schema_hash": A2_SCHEMA_HASH,
                "feature_contract_version": GEN1_FEATURE_CONTRACT_VERSION,
                "feature_contract_hash": development.metadata()[
                    "feature_contract_hash"
                ],
                "derived_feature_version_id": str(derived_feature_version_id),
                "derived_feature_version": derived_feature_version,
                "derived_feature_version_checksum": derived_feature_version_checksum,
                "derived_feature_schema_version": derived_feature_version,
                "derived_feature_schema_hash": development.selected_schema.schema_hash,
                "model_input_schema_version": development.selected_schema.version,
                "model_input_schema_hash": development.selected_schema.schema_hash,
                "universe_feature_schema_hash": GEN1_UNIVERSE_SCHEMA_HASH,
                "holdout_identity_checksum": development.split.holdout_identity_checksum,
                "holdout_labels_accessed": False,
                "provider_calls": 0,
                "code_revision": code_revision,
                "baseline_code_revision": baseline_code_revision,
                "dependency_versions": dict(sorted(dependency_versions.items())),
                "model_filename": model_path.name,
                "model_sha256": model_checksum,
                "development_filename": metadata_path.name,
                "development_sha256": metadata_checksum,
                "predictions_filename": predictions_path.name,
                "predictions_sha256": predictions_checksum,
            }
            artifact_identity = _checksum(semantic)
            final = self._candidate_directory(artifact_identity)
            manifest = Gen1FrozenArtifactManifest(
                artifact_identity_checksum=artifact_identity,
                created_at=created_at,
                total_artifact_bytes=0,
                **semantic,
            )
            manifest = self._manifest_with_measured_size(manifest, staging)
            self._guard_storage(manifest.total_artifact_bytes)
            manifest_path = staging / "manifest.json"
            manifest_path.write_bytes(
                json.dumps(asdict(manifest), indent=2, sort_keys=True).encode("utf-8")
            )
            _sync(manifest_path)
            if final.exists():
                existing = self._load_manifest(final)
                self._validate_frozen_directory(final, existing)
                if existing.artifact_identity_checksum != artifact_identity:
                    raise Gen1ArtifactError("Gen-1 candidate artifact identity collision")
                shutil.rmtree(staging, ignore_errors=True)
                return existing
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final)
            return manifest
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def load_candidate(
        self,
        artifact_identity_checksum: str,
    ) -> tuple[Gen1DevelopmentResult, Gen1FrozenArtifactManifest]:
        directory = self._candidate_directory(artifact_identity_checksum)
        manifest = self._load_manifest(directory)
        self._validate_frozen_directory(directory, manifest)
        model_path = self._contained(directory / manifest.model_filename)
        development = joblib.load(model_path)
        if not isinstance(development, Gen1DevelopmentResult):
            raise Gen1ArtifactError("Frozen candidate contains an unexpected object type")
        if (
            development.candidate_identity_checksum
            != manifest.development_identity_checksum
            or development.split.holdout_identity_checksum
            != manifest.holdout_identity_checksum
            or development.status != GEN1_RESEARCH_FROZEN_STATUS
            or development.holdout_labels_accessed
            or development.provider_calls != 0
            or development.selected_schema.version
            != manifest.model_input_schema_version
            or development.selected_schema.schema_hash
            != manifest.model_input_schema_hash
            or manifest.derived_feature_schema_version
            != manifest.derived_feature_version
        ):
            raise Gen1ArtifactError("Frozen candidate object failed semantic revalidation")
        return development, manifest

    def claim_holdout_evaluation(
        self,
        candidate_artifact_identity_checksum: str,
    ) -> Gen1EvaluationClaim:
        development, candidate = self.load_candidate(candidate_artifact_identity_checksum)
        global_identity = _checksum(
            {
                "source_dataset_rows_checksum": candidate.dataset_rows_checksum,
                "holdout_identity_checksum": development.split.holdout_identity_checksum,
            }
        )
        global_directory = self._holdout_claim_directory(global_identity)
        global_directory.parent.mkdir(parents=True, exist_ok=True)
        try:
            global_directory.mkdir(mode=0o777, exist_ok=False)
        except FileExistsError as exc:
            raise Gen1ArtifactError(
                "The exact source-dataset holdout has an already consumed global claim"
            ) from exc
        directory = self._evaluation_directory(candidate.artifact_identity_checksum)
        directory.parent.mkdir(parents=True, exist_ok=True)
        try:
            directory.mkdir(mode=0o777, exist_ok=False)
        except FileExistsError as exc:
            raise Gen1ArtifactError(
                "The exact frozen candidate/holdout identity already has a consumed evaluation claim"
            ) from exc
        claimed_at = datetime.now(UTC).isoformat()
        semantic = {
            "status": GEN1_EVALUATION_CLAIMED_STATUS,
            "candidate_artifact_identity_checksum": candidate.artifact_identity_checksum,
            "development_identity_checksum": development.candidate_identity_checksum,
            "source_dataset_rows_checksum": candidate.dataset_rows_checksum,
            "holdout_identity_checksum": development.split.holdout_identity_checksum,
            "global_holdout_claim_identity_checksum": global_identity,
            "claimed_at": claimed_at,
        }
        identity = _checksum(semantic)
        claim_path = directory / "claim.json"
        relative = directory.relative_to(self.root).as_posix()
        global_relative = global_directory.relative_to(self.root).as_posix()
        claim = Gen1EvaluationClaim(
            claim_identity_checksum=identity,
            claim_uri=relative,
            global_claim_uri=global_relative,
            **semantic,
        )
        try:
            payload = json.dumps(asdict(claim), indent=2, sort_keys=True).encode("utf-8")
            for path in (global_directory / "claim.json", claim_path):
                with path.open("xb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
        except Exception:
            # Preserve a consumed directory even when claim serialization fails.
            raise
        return claim

    def complete_holdout_evaluation(
        self,
        claim: Gen1EvaluationClaim,
        evaluation: Gen1HoldoutEvaluation,
    ) -> Gen1EvaluationArtifactManifest:
        directory = self._evaluation_directory(claim.candidate_artifact_identity_checksum)
        self._validate_claim(directory, claim)
        if (directory / "manifest.json").exists() or (directory / "failed.json").exists():
            raise Gen1ArtifactError("The holdout evaluation claim is already terminal")
        if (
            evaluation.status != GEN1_RESEARCH_EVALUATED_STATUS
            or evaluation.candidate_identity_checksum != claim.development_identity_checksum
            or evaluation.holdout_identity_checksum != claim.holdout_identity_checksum
            or evaluation.provider_calls != 0
            or evaluation.promotion_decision != "RESEARCH_ONLY_NO_PROMOTION"
        ):
            raise Gen1ArtifactError("Holdout evaluation does not match its consumed claim")
        evaluation_path = directory / "evaluation.joblib"
        metrics_path = directory / "metrics.json"
        predictions_path = directory / "predictions.jsonl"
        joblib.dump(evaluation, evaluation_path)
        _sync(evaluation_path)
        metrics_bytes = json.dumps(
            evaluation.metadata(), indent=2, sort_keys=True
        ).encode("utf-8")
        with metrics_path.open("xb") as handle:
            handle.write(metrics_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        with predictions_path.open("xb") as handle:
            for row in evaluation.prediction_rows:
                handle.write(_canonical(row) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        metrics_checksum = _file_checksum(metrics_path)
        predictions_checksum = _file_checksum(predictions_path)
        evaluation_checksum = _file_checksum(evaluation_path)
        evaluated_at = datetime.now(UTC).isoformat()
        semantic = {
            "status": GEN1_RESEARCH_EVALUATED_STATUS,
            "evaluation_identity_checksum": evaluation.evaluation_identity_checksum,
            "candidate_artifact_identity_checksum": claim.candidate_artifact_identity_checksum,
            "development_identity_checksum": claim.development_identity_checksum,
            "holdout_identity_checksum": claim.holdout_identity_checksum,
            "claim_identity_checksum": claim.claim_identity_checksum,
            "promotion_decision": "RESEARCH_ONLY_NO_PROMOTION",
            "provider_calls": 0,
            "evaluation_filename": evaluation_path.name,
            "evaluation_sha256": evaluation_checksum,
            "metrics_filename": metrics_path.name,
            "metrics_sha256": metrics_checksum,
            "predictions_filename": predictions_path.name,
            "predictions_sha256": predictions_checksum,
        }
        identity = _checksum(semantic)
        projected_size = (
            (directory / "claim.json").stat().st_size
            + evaluation_path.stat().st_size
            + metrics_path.stat().st_size
            + predictions_path.stat().st_size
            + 2048
        )
        self._guard_storage(projected_size)
        manifest = Gen1EvaluationArtifactManifest(
            artifact_identity_checksum=identity,
            evaluated_at=evaluated_at,
            total_artifact_bytes=projected_size,
            **semantic,
        )
        manifest_path = directory / "manifest.json"
        with manifest_path.open("xb") as handle:
            handle.write(json.dumps(asdict(manifest), indent=2, sort_keys=True).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        return manifest

    def record_failed_evaluation(
        self,
        claim: Gen1EvaluationClaim,
        error: BaseException,
    ) -> None:
        directory = self._evaluation_directory(claim.candidate_artifact_identity_checksum)
        self._validate_claim(directory, claim)
        if (directory / "manifest.json").exists() or (directory / "failed.json").exists():
            return
        payload = {
            "status": GEN1_EVALUATION_FAILED_STATUS,
            "claim_identity_checksum": claim.claim_identity_checksum,
            "candidate_artifact_identity_checksum": claim.candidate_artifact_identity_checksum,
            "failure_type": type(error).__name__,
            "labels_may_have_been_accessed": True,
            "claim_consumed": True,
            "retry_permitted": False,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        with (directory / "failed.json").open("xb") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

    def load_evaluation(
        self,
        candidate_artifact_identity_checksum: str,
    ) -> tuple[Gen1HoldoutEvaluation, Gen1EvaluationArtifactManifest]:
        directory = self._evaluation_directory(candidate_artifact_identity_checksum)
        path = self._contained(directory / "manifest.json")
        manifest = Gen1EvaluationArtifactManifest(
            **json.loads(path.read_text(encoding="utf-8"))
        )
        claim = Gen1EvaluationClaim(
            **json.loads((directory / "claim.json").read_text(encoding="utf-8"))
        )
        self._validate_claim(directory, claim)
        if claim.claim_identity_checksum != manifest.claim_identity_checksum:
            raise Gen1ArtifactError("Gen-1 evaluation claim and manifest diverged")
        metrics_path = self._contained(directory / manifest.metrics_filename)
        if _file_checksum(metrics_path) != manifest.metrics_sha256:
            raise Gen1ArtifactError("Gen-1 holdout metrics checksum mismatch")
        predictions_path = self._contained(
            directory / manifest.predictions_filename
        )
        if _file_checksum(predictions_path) != manifest.predictions_sha256:
            raise Gen1ArtifactError("Gen-1 holdout predictions checksum mismatch")
        evaluation_path = self._contained(directory / manifest.evaluation_filename)
        if _file_checksum(evaluation_path) != manifest.evaluation_sha256:
            raise Gen1ArtifactError("Gen-1 holdout evaluation object checksum mismatch")
        semantic = {
            key: value
            for key, value in asdict(manifest).items()
            if key not in ("artifact_identity_checksum", "evaluated_at", "total_artifact_bytes")
        }
        if _checksum(semantic) != manifest.artifact_identity_checksum:
            raise Gen1ArtifactError("Gen-1 evaluation manifest identity mismatch")
        evaluation = joblib.load(evaluation_path)
        if (
            not isinstance(evaluation, Gen1HoldoutEvaluation)
            or evaluation.evaluation_identity_checksum
            != manifest.evaluation_identity_checksum
            or evaluation.candidate_identity_checksum
            != manifest.development_identity_checksum
            or evaluation.holdout_identity_checksum
            != manifest.holdout_identity_checksum
            or evaluation.status != GEN1_RESEARCH_EVALUATED_STATUS
            or evaluation.provider_calls != 0
        ):
            raise Gen1ArtifactError("Gen-1 evaluation object failed semantic revalidation")
        return evaluation, manifest

    def _manifest_with_measured_size(
        self,
        manifest: Gen1FrozenArtifactManifest,
        directory: Path,
    ) -> Gen1FrozenArtifactManifest:
        measured = sum(path.stat().st_size for path in directory.iterdir() if path.is_file())
        projected = measured
        while True:
            candidate = Gen1FrozenArtifactManifest(
                **{**asdict(manifest), "total_artifact_bytes": projected}
            )
            manifest_bytes = len(json.dumps(asdict(candidate), indent=2, sort_keys=True).encode("utf-8"))
            updated = measured + manifest_bytes
            if updated == projected:
                return candidate
            projected = updated

    def _load_manifest(self, directory: Path) -> Gen1FrozenArtifactManifest:
        path = self._contained(directory / "manifest.json")
        return Gen1FrozenArtifactManifest(
            **json.loads(path.read_text(encoding="utf-8"))
        )

    def _validate_frozen_directory(
        self,
        directory: Path,
        manifest: Gen1FrozenArtifactManifest,
    ) -> None:
        if (
            manifest.status != GEN1_RESEARCH_FROZEN_STATUS
            or manifest.research_policy_version != MLB_GEN1_RESEARCH_POLICY_VERSION
            or manifest.source_feature_schema_version != A2_SCHEMA_VERSION
            or manifest.source_feature_schema_hash != A2_SCHEMA_HASH
            or manifest.feature_contract_version != GEN1_FEATURE_CONTRACT_VERSION
            or not _SHA256.fullmatch(manifest.feature_contract_hash)
            or manifest.universe_feature_schema_hash != GEN1_UNIVERSE_SCHEMA_HASH
            or manifest.holdout_labels_accessed
            or manifest.provider_calls != 0
        ):
            raise Gen1ArtifactError("Gen-1 frozen manifest governance fields are invalid")
        semantic = {
            key: value
            for key, value in asdict(manifest).items()
            if key not in ("artifact_identity_checksum", "created_at", "total_artifact_bytes")
        }
        if _checksum(semantic) != manifest.artifact_identity_checksum:
            raise Gen1ArtifactError("Gen-1 frozen manifest identity mismatch")
        for filename, expected, label in (
            (manifest.model_filename, manifest.model_sha256, "model"),
            (manifest.development_filename, manifest.development_sha256, "development"),
            (manifest.predictions_filename, manifest.predictions_sha256, "predictions"),
        ):
            path = self._contained(directory / filename)
            if _file_checksum(path) != expected:
                raise Gen1ArtifactError(f"Gen-1 {label} artifact checksum mismatch")

    def _validate_claim(self, directory: Path, expected: Gen1EvaluationClaim) -> None:
        path = self._contained(directory / "claim.json")
        actual = Gen1EvaluationClaim(**json.loads(path.read_text(encoding="utf-8")))
        semantic = {
            key: value
            for key, value in asdict(actual).items()
            if key
            not in ("claim_identity_checksum", "claim_uri", "global_claim_uri")
        }
        global_path = self._contained(
            self.root / actual.global_claim_uri / "claim.json"
        )
        global_actual = Gen1EvaluationClaim(
            **json.loads(global_path.read_text(encoding="utf-8"))
        )
        if (
            actual != expected
            or global_actual != expected
            or _checksum(semantic) != actual.claim_identity_checksum
            or actual.status != GEN1_EVALUATION_CLAIMED_STATUS
        ):
            raise Gen1ArtifactError("Gen-1 holdout evaluation claim failed revalidation")

    def _candidate_directory(self, identity: str) -> Path:
        safe = _require_sha256(identity, "candidate artifact identity")
        return self._contained(self.root / "candidates" / safe)

    def _evaluation_directory(self, identity: str) -> Path:
        safe = _require_sha256(identity, "candidate artifact identity")
        return self._contained(self.root / "evaluations" / safe)

    def _holdout_claim_directory(self, identity: str) -> Path:
        safe = _require_sha256(identity, "global holdout claim identity")
        return self._contained(self.root / "holdout_claims" / safe)

    def _contained(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise Gen1ArtifactError("Gen-1 artifact path escapes its governed root")
        return resolved

    def _guard_storage(self, projected_bytes: int) -> None:
        if projected_bytes > self.maximum_artifact_bytes:
            raise Gen1ArtifactError("Gen-1 artifact exceeds its configured byte cap")
        probe = self.root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            free = shutil.disk_usage(probe).free
        except OSError as exc:
            raise Gen1ArtifactError("Unable to verify Gen-1 artifact free space") from exc
        if free < projected_bytes + self.minimum_free_bytes_after_write:
            raise Gen1ArtifactError("Gen-1 artifact free-space reserve would be violated")


__all__ = [
    "DEFAULT_GEN1_MAXIMUM_ARTIFACT_BYTES",
    "DEFAULT_GEN1_MINIMUM_FREE_BYTES_AFTER_WRITE",
    "GEN1_EVALUATION_CLAIMED_STATUS",
    "GEN1_EVALUATION_FAILED_STATUS",
    "Gen1ArtifactError",
    "Gen1EvaluationArtifactManifest",
    "Gen1EvaluationClaim",
    "Gen1FrozenArtifactManifest",
    "Gen1ResearchArtifactStore",
]
