"""Governed A2 admission and two-phase orchestration for MLB Gen-1 research.

This entrypoint never calls a provider and never weakens the existing Gate 3
promotion readiness policy.  It applies the separately authorized
``MLB_GEN1_RESEARCH_POLICY_V1`` to the supported strict Tier-A evidence
families, freezes a development-only candidate, and exposes final-holdout
evaluation only through an explicit one-use artifact claim.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from importlib.metadata import version as dependency_version
import json
from math import ceil
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.historical.a2_schema import A2_SCHEMA, A2_SCHEMA_HASH, A2_SCHEMA_VERSION
from app.modeling.gen1 import (
    GEN1_ANALYSIS_FAMILIES,
    GEN1_UNIVERSE_SCHEMA_HASH,
    Gen1DevelopmentResult,
    Gen1HoldoutEvaluation,
    Gen1ResearchPolicy,
    MLB_GEN1_RESEARCH_POLICY_VERSION,
    evaluate_gen1_holdout,
    run_gen1_development,
    split_gen1_rows,
    supported_family_coverage,
    validate_strict_a2_covariates,
)
from app.models import (
    FeatureVersion,
    ModelEvaluation,
    ModelStatus,
    ModelVersion,
    TrainingDatasetManifest,
)
from app.training.dataset import (
    DatasetBuildResult,
    TrainingDatasetArtifactStore,
    TrainingDatasetManifestData,
    TrainingRow,
)
from app.training.gate3b_artifacts import (
    Gen1EvaluationArtifactManifest,
    Gen1FrozenArtifactManifest,
    Gen1ResearchArtifactStore,
)


GEN1_FEATURE_VERSION_NAME = "mlb_gen1_research"
GEN1_A2_POPULATION = "HISTORICAL_RECONSTRUCTION_A2"
GEN1_A2_TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"
GEN1_A2_CUTOFF_POLICY = "mlb_t_minus_6h_v1"
GEN1_A2_STRICT_MANIFEST_NAME = "mlb_historical_a2_tier_a_strict"
GEN1_RESEARCH_ADMISSION_STATUS = "ADMITTED_FOR_MLB_GEN1_RESEARCH_ONLY"
GEN1_RESEARCH_REJECTED_STATUS = "REJECTED_FOR_MLB_GEN1_RESEARCH"
GEN1_APPROVED_A2_DATASET_ID = "mlb-gate3-a2-3059f81db1d0e278"
GEN1_APPROVED_A2_MANIFEST_ID = "d4087299-8a04-4176-bad2-a9ec19128b62"
GEN1_APPROVED_A2_IDENTITY_CHECKSUM = (
    "3059f81db1d0e2783d0357cf765fbc027958a1d0786c8da83611bae82dca1748"
)
GEN1_APPROVED_A2_ROWS_CHECKSUM = (
    "bc627c16f85ac3372cb2cb3fb1c9dab6767291704dac790fb5ffe07b8af406bb"
)
GEN1_APPROVED_A2_FEATURE_VERSION_ID = "7cdfa80f-725d-4061-8ca5-5967d9ea5701"
GEN1_APPROVED_A2_ROW_COUNT = 2_428
GEN1_APPROVED_A2_EVENT_DATE_COUNT = 184


class Gen1AdmissionError(RuntimeError):
    """Raised before model imports/fits when strict A2 admission fails."""


@dataclass(frozen=True, slots=True)
class Gen1BlindedHoldoutRow:
    """Phase-1 row view that physically cannot expose final-holdout targets."""

    game_id: str
    scheduled_first_pitch: str
    lifecycle: str
    feature_version_id: str
    feature_version: str
    feature_schema_version: str
    source_mode: str
    home_team_id: str
    away_team_id: str
    features: Mapping[str, object]
    missing_features: Sequence[str]
    market_home_probability: None
    market_timestamp: None
    market_books: int
    closing_home_probability: None
    closing_timestamp: None
    temporal_semantics: str
    historical_quality_tier: str
    cutoff_policy_version: str


_PHASE1_TARGET_VALUE = re.compile(
    rb'("(?:away_runs|home_runs|home_win|run_differential|total_runs)"\s*:\s*)'
    rb'-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
)


def _blinded_holdout_row(value: Mapping[str, object]) -> Gen1BlindedHoldoutRow:
    return Gen1BlindedHoldoutRow(
        game_id=str(value["game_id"]),
        scheduled_first_pitch=str(value["scheduled_first_pitch"]),
        lifecycle=str(value.get("lifecycle", "HISTORICAL_A2_T_MINUS_6H")),
        feature_version_id=str(value["feature_version_id"]),
        feature_version=str(value["feature_version"]),
        feature_schema_version=str(value["feature_schema_version"]),
        source_mode=str(value["source_mode"]),
        home_team_id=str(value["home_team_id"]),
        away_team_id=str(value["away_team_id"]),
        features=dict(value["features"]),  # type: ignore[arg-type]
        missing_features=tuple(str(item) for item in value.get("missing_features", ())),
        market_home_probability=None,
        market_timestamp=None,
        market_books=int(value.get("market_books", 0)),
        closing_home_probability=None,
        closing_timestamp=None,
        temporal_semantics=str(value["temporal_semantics"]),
        historical_quality_tier=str(value["historical_quality_tier"]),
        cutoff_policy_version=str(value["cutoff_policy_version"]),
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _json_native(value: object) -> object:
    """Return a canonical value safe for every supported SQL JSON backend."""

    return json.loads(_canonical(value))


def _checksum(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _phase1_first_holdout_date(
    rows: Sequence[object],
    policy: Gen1ResearchPolicy,
) -> date:
    dates = sorted(
        {
            datetime.fromisoformat(
                str(getattr(row, "scheduled_first_pitch")).replace("Z", "+00:00")
            ).date()
            for row in rows
        }
    )
    if len(dates) < policy.minimum_event_dates:
        raise Gen1AdmissionError("Gen-1 dataset lacks required event-date groups")
    holdout_date_count = max(1, ceil(len(dates) * policy.holdout_fraction))
    split_index = len(dates) - holdout_date_count
    if split_index < policy.minimum_development_dates:
        raise Gen1AdmissionError("Gen-1 development window is too short")
    return dates[split_index]


def _blind_phase1_rows(
    rows: Sequence[object],
    policy: Gen1ResearchPolicy,
) -> tuple[object, ...]:
    first_holdout_date = _phase1_first_holdout_date(rows, policy)
    result: list[object] = []
    for row in rows:
        event_date = datetime.fromisoformat(
            str(getattr(row, "scheduled_first_pitch")).replace("Z", "+00:00")
        ).date()
        if event_date < first_holdout_date:
            result.append(row)
            continue
        result.append(
            _blinded_holdout_row(
                {
                    "game_id": getattr(row, "game_id"),
                    "scheduled_first_pitch": getattr(row, "scheduled_first_pitch"),
                    "lifecycle": getattr(row, "lifecycle"),
                    "feature_version_id": getattr(row, "feature_version_id"),
                    "feature_version": getattr(row, "feature_version"),
                    "feature_schema_version": getattr(row, "feature_schema_version"),
                    "source_mode": getattr(row, "source_mode"),
                    "home_team_id": getattr(row, "home_team_id"),
                    "away_team_id": getattr(row, "away_team_id"),
                    "features": getattr(row, "features"),
                    "missing_features": getattr(row, "missing_features"),
                    "market_books": getattr(row, "market_books"),
                    "temporal_semantics": getattr(row, "temporal_semantics"),
                    "historical_quality_tier": getattr(
                        row, "historical_quality_tier"
                    ),
                    "cutoff_policy_version": getattr(row, "cutoff_policy_version"),
                }
            )
        )
    return tuple(result)


def _load_phase1_blinded_artifact(
    artifact_root: Path,
    dataset_id: str,
    policy: Gen1ResearchPolicy,
) -> tuple[TrainingDatasetManifestData, tuple[object, ...], str]:
    """Verify labeled bytes, but never decode holdout target values in Phase 1."""

    if not dataset_id.startswith("mlb-gate3-") or any(
        value in dataset_id for value in ("/", "\\", "..")
    ):
        raise Gen1AdmissionError("Invalid exact A2 dataset identifier")
    root = artifact_root.resolve()
    directory = (root / dataset_id).resolve()
    if root not in directory.parents:
        raise Gen1AdmissionError("A2 artifact path escapes its governed root")
    manifest_path = directory / "manifest.json"
    rows_path = directory / "rows.jsonl"
    manifest = TrainingDatasetManifestData(
        **json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    rows_bytes = rows_path.read_bytes()
    verified_rows_checksum = sha256(rows_bytes.rstrip(b"\n")).hexdigest()
    if verified_rows_checksum != manifest.rows_checksum:
        raise Gen1AdmissionError("A2 training rows byte checksum mismatch")
    raw_lines = tuple(line for line in rows_bytes.splitlines() if line.strip())
    redacted_rows: list[Mapping[str, object]] = []
    for line in raw_lines:
        sanitized, target_count = _PHASE1_TARGET_VALUE.subn(rb"\1null", line)
        if target_count != 5:
            raise Gen1AdmissionError(
                "A2 row target fields could not be exactly redacted before Phase 1"
            )
        redacted_rows.append(json.loads(sanitized))
    redacted = tuple(redacted_rows)
    if len(redacted) != manifest.row_count:
        raise Gen1AdmissionError("A2 training row count does not match its manifest")
    provisional = tuple(_blinded_holdout_row(value) for value in redacted)
    first_holdout_date = _phase1_first_holdout_date(provisional, policy)
    rows: list[object] = []
    for raw_line, value in zip(raw_lines, redacted):
        event_date = datetime.fromisoformat(
            str(value["scheduled_first_pitch"]).replace("Z", "+00:00")
        ).date()
        if event_date >= first_holdout_date:
            rows.append(_blinded_holdout_row(value))
        else:
            rows.append(TrainingRow(**json.loads(raw_line)))
    return manifest, tuple(rows), verified_rows_checksum


def feature_version_checksum(feature_version: FeatureVersion) -> str:
    return _checksum(
        {
            "id": str(feature_version.id),
            "name": feature_version.name,
            "version": feature_version.version,
            "schema_hash": feature_version.schema_hash,
            "definition": feature_version.definition,
        }
    )


@dataclass(frozen=True, slots=True)
class Gen1AdmissionResult:
    status: str
    policy: Gen1ResearchPolicy
    build: DatasetBuildResult
    dataset_id: str
    dataset_identity_checksum: str
    dataset_rows_checksum: str
    source_feature_version_id: str
    source_feature_version_checksum: str
    source_feature_schema_hash: str
    row_count: int
    event_date_count: int
    development_date_count: int
    holdout_date_count: int
    holdout_row_count: int
    rolling_fold_count: int
    supported_family_coverage: Mapping[str, float]
    checks: Mapping[str, Mapping[str, object]]
    provider_calls: int = 0
    promotion_gate_modified: bool = False

    def metadata(self) -> dict[str, object]:
        return {
            "status": self.status,
            "policy": asdict(self.policy),
            "research_policy_version": MLB_GEN1_RESEARCH_POLICY_VERSION,
            "dataset_id": self.dataset_id,
            "dataset_identity_checksum": self.dataset_identity_checksum,
            "dataset_rows_checksum": self.dataset_rows_checksum,
            "source_feature_version_id": self.source_feature_version_id,
            "source_feature_version_checksum": self.source_feature_version_checksum,
            "source_feature_schema_hash": self.source_feature_schema_hash,
            "row_count": self.row_count,
            "event_date_count": self.event_date_count,
            "development_date_count": self.development_date_count,
            "holdout_date_count": self.holdout_date_count,
            "holdout_row_count": self.holdout_row_count,
            "rolling_fold_count": self.rolling_fold_count,
            "supported_family_coverage": dict(self.supported_family_coverage),
            "checks": {name: dict(value) for name, value in self.checks.items()},
            "provider_calls": 0,
            "promotion_gate_modified": False,
        }


@dataclass(frozen=True, slots=True)
class Gen1FrozenRunResult:
    admission: Gen1AdmissionResult
    development: Gen1DevelopmentResult
    feature_version_id: str
    feature_version_checksum: str
    artifact: Gen1FrozenArtifactManifest
    provider_calls: int = 0


@dataclass(frozen=True, slots=True)
class Gen1EvaluationRunResult:
    evaluation: Gen1HoldoutEvaluation
    artifact: Gen1EvaluationArtifactManifest
    model_version_id: str | None = None
    model_evaluation_id: str | None = None
    provider_calls: int = 0


def _check(passed: bool, observed: object, required: object) -> dict[str, object]:
    return {"passed": bool(passed), "observed": observed, "required": required}


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_manifest_checks(
    manifest: TrainingDatasetManifestData,
    rows: Sequence[object],
    *,
    verified_artifact_rows_checksum: str,
) -> dict[str, dict[str, object]]:
    return {
        "strict_a2_contract": _check(
            manifest.feature_schema_version == A2_SCHEMA_VERSION
            and manifest.feature_names == list(A2_SCHEMA.output_names)
            and manifest.frozen_output_names == list(A2_SCHEMA.output_names)
            and manifest.population == GEN1_A2_POPULATION
            and manifest.temporal_semantics == GEN1_A2_TEMPORAL_SEMANTICS
            and manifest.cutoff_policy_version == GEN1_A2_CUTOFF_POLICY
            and manifest.quality_tiers == ["A"]
            and manifest.source_modes == ["LIVE"],
            {
                "schema": manifest.feature_schema_version,
                "feature_count": len(manifest.feature_names),
                "population": manifest.population,
                "temporal_semantics": manifest.temporal_semantics,
                "cutoff_policy": manifest.cutoff_policy_version,
                "quality_tiers": manifest.quality_tiers,
                "source_modes": manifest.source_modes,
            },
            {
                "schema": A2_SCHEMA_VERSION,
                "feature_count": len(A2_SCHEMA.output_names),
                "population": GEN1_A2_POPULATION,
                "temporal_semantics": GEN1_A2_TEMPORAL_SEMANTICS,
                "cutoff_policy": GEN1_A2_CUTOFF_POLICY,
                "quality_tiers": ["A"],
                "source_modes": ["LIVE"],
            },
        ),
        "artifact_identity": _check(
            _sha256(manifest.checksum)
            and _sha256(manifest.rows_checksum)
            and manifest.row_count == len(rows)
            and verified_artifact_rows_checksum == manifest.rows_checksum,
            {
                "manifest_checksum": manifest.checksum,
                "rows_checksum": manifest.rows_checksum,
                "manifest_rows": manifest.row_count,
                "loaded_rows": len(rows),
                "verified_artifact_rows_checksum": verified_artifact_rows_checksum,
            },
            {"checksums": "exact_sha256", "row_count": "exact"},
        ),
        "leakage_contract": _check(
            bool(manifest.leakage_checks)
            and all(value is True for value in manifest.leakage_checks.values()),
            dict(sorted(manifest.leakage_checks.items())),
            {"all": True},
        ),
        "unique_games": _check(
            len({row.game_id for row in rows}) == len(rows),
            {"rows": len(rows), "games": len({row.game_id for row in rows})},
            {"exact_match": True},
        ),
        "feature_version_identity": _check(
            bool(manifest.feature_version_id)
            and all(str(row.feature_version_id) == str(manifest.feature_version_id) for row in rows)
            and all(row.feature_version == manifest.feature_version for row in rows),
            {
                "feature_version_id": manifest.feature_version_id,
                "feature_version": manifest.feature_version,
            },
            {"single_exact_contract": True},
        ),
        "zero_market_provider_scope": _check(
            all(
                row.market_home_probability is None
                and row.market_timestamp is None
                and row.market_books == 0
                and row.closing_home_probability is None
                and row.closing_timestamp is None
                for row in rows
            ),
            {"rows_checked": len(rows), "provider_calls": 0},
            {"market_fields": "absent", "provider_calls": 0},
        ),
    }


def admit_gen1_build(
    build: DatasetBuildResult,
    *,
    source_feature_version_id: str,
    source_feature_version_checksum: str,
    verified_artifact_rows_checksum: str,
    policy: Gen1ResearchPolicy = Gen1ResearchPolicy(),
    persisted_readiness: Mapping[str, object] | None = None,
) -> Gen1AdmissionResult:
    """Admit only exact strict A2 evidence under the separate research policy."""

    try:
        UUID(str(source_feature_version_id))
    except (TypeError, ValueError) as exc:
        raise Gen1AdmissionError("Gen-1 source feature-version ID is invalid") from exc
    if not _sha256(source_feature_version_checksum):
        raise Gen1AdmissionError("Gen-1 source feature-version checksum is invalid")
    if not _sha256(verified_artifact_rows_checksum):
        raise Gen1AdmissionError("Verified A2 artifact rows checksum is invalid")
    rows = tuple(build.rows)
    checks = _strict_manifest_checks(
        build.manifest,
        rows,
        verified_artifact_rows_checksum=verified_artifact_rows_checksum,
    )
    covariate_errors: list[str] = []
    for row in rows:
        try:
            validate_strict_a2_covariates(row)
        except ValueError as exc:
            covariate_errors.append(f"{row.game_id}:{exc}")
            if len(covariate_errors) >= 5:
                break
    checks["row_covariate_contract"] = _check(
        not covariate_errors,
        {"rows_checked": len(rows), "sample_errors": covariate_errors},
        {"errors": 0},
    )
    coverage = supported_family_coverage(rows)
    checks["supported_family_coverage"] = _check(
        set(coverage) == set(GEN1_ANALYSIS_FAMILIES)
        and all(
            value >= policy.minimum_supported_family_coverage
            for value in coverage.values()
        ),
        dict(coverage),
        {
            "families": list(GEN1_ANALYSIS_FAMILIES),
            "minimum_each": policy.minimum_supported_family_coverage,
            "scope": "MLB_GEN1_RESEARCH_POLICY_V1_ONLY",
        },
    )
    if persisted_readiness is not None:
        raw_checks = persisted_readiness.get("checks")
        readiness_checks = raw_checks if isinstance(raw_checks, Mapping) else {}
        unexpected_failures = sorted(
            name
            for name, value in readiness_checks.items()
            if name != "required_feature_family_coverage"
            and isinstance(value, Mapping)
            and value.get("passed") is not True
        )
        checks["promotion_readiness_failures_are_only_unsupported_families"] = _check(
            not unexpected_failures,
            {
                "persisted_status": persisted_readiness.get("status"),
                "unexpected_failures": unexpected_failures,
                "allowed_research_exception": "required_feature_family_coverage",
            },
            {"unexpected_failures": []},
        )
    failures = [name for name, value in checks.items() if value["passed"] is not True]
    if failures:
        raise Gen1AdmissionError(
            "Gen-1 A2 admission failed before model development: " + ", ".join(failures)
        )
    phase1_rows = _blind_phase1_rows(rows, policy)
    split = split_gen1_rows(phase1_rows, policy)
    # Count folds structurally using date groups only. Model development repeats
    # and class-validates the exact fold construction.
    development_dates = len(split.holdout.development_dates)
    fold_count = 0
    start = policy.minimum_train_dates
    while start + policy.validation_dates <= development_dates:
        fold_count += 1
        start += policy.step_dates
    if fold_count < policy.minimum_rolling_folds:
        raise Gen1AdmissionError("Gen-1 admission cannot form at least five rolling folds")
    return Gen1AdmissionResult(
        status=GEN1_RESEARCH_ADMISSION_STATUS,
        policy=policy,
        build=DatasetBuildResult(
            manifest=build.manifest,
            rows=phase1_rows,  # type: ignore[arg-type]
            exclusions=build.exclusions,
        ),
        dataset_id=build.manifest.dataset_id,
        dataset_identity_checksum=build.manifest.checksum,
        dataset_rows_checksum=build.manifest.rows_checksum,
        source_feature_version_id=str(source_feature_version_id),
        source_feature_version_checksum=source_feature_version_checksum,
        source_feature_schema_hash=A2_SCHEMA_HASH,
        row_count=len(rows),
        event_date_count=len(split.holdout.development_dates) + len(split.holdout.holdout_dates),
        development_date_count=len(split.holdout.development_dates),
        holdout_date_count=len(split.holdout.holdout_dates),
        holdout_row_count=len(split.holdout.holdout_game_ids),
        rolling_fold_count=fold_count,
        supported_family_coverage=coverage,
        checks=checks,
    )


def load_gen1_a2_admission(
    session: Session,
    *,
    dataset_artifact_root: Path,
    manifest_identity_checksum: str,
    policy: Gen1ResearchPolicy = Gen1ResearchPolicy(),
) -> Gen1AdmissionResult:
    """Load one exact ORM/on-disk A2 strict identity; no fuzzy newest selection."""

    if not _sha256(manifest_identity_checksum):
        raise Gen1AdmissionError("Exact A2 manifest identity must be a SHA-256")
    if manifest_identity_checksum != GEN1_APPROVED_A2_IDENTITY_CHECKSUM:
        raise Gen1AdmissionError(
            "Gate 3B is authorized only for the explicitly approved strict A2 identity"
        )
    record = session.scalar(
        select(TrainingDatasetManifest).where(
            TrainingDatasetManifest.identity_checksum == manifest_identity_checksum
        )
    )
    if record is None:
        raise Gen1AdmissionError("Exact A2 TrainingDatasetManifest was not found")
    if (
        str(record.id) != GEN1_APPROVED_A2_MANIFEST_ID
        or record.version != GEN1_APPROVED_A2_DATASET_ID
        or record.artifact_checksum != GEN1_APPROVED_A2_ROWS_CHECKSUM
        or str(record.feature_version_id) != GEN1_APPROVED_A2_FEATURE_VERSION_ID
        or record.row_count != GEN1_APPROVED_A2_ROW_COUNT
        or record.game_count != GEN1_APPROVED_A2_ROW_COUNT
        or record.seasons != [2025]
        or
        record.name != GEN1_A2_STRICT_MANIFEST_NAME
        or record.schema_version != A2_SCHEMA_VERSION
        or record.source_mode != "LIVE"
        or record.identity_checksum != manifest_identity_checksum
    ):
        raise Gen1AdmissionError("ORM manifest is not the exact strict A2 research source")
    quality = record.quality_summary if isinstance(record.quality_summary, Mapping) else {}
    if (
        quality.get("stage") != "GATE_3A_A2"
        or quality.get("population") != "TIER_A_STRICT"
        or quality.get("schema_hash") != A2_SCHEMA_HASH
        or quality.get("quality_tiers") != ["A"]
    ):
        raise Gen1AdmissionError("ORM strict A2 quality summary failed revalidation")
    source_feature = session.get(FeatureVersion, record.feature_version_id)
    if source_feature is None or source_feature.schema_hash != A2_SCHEMA_HASH:
        raise Gen1AdmissionError("A2 source FeatureVersion is missing or drifted")
    artifact_manifest, rows, verified_rows_checksum = _load_phase1_blinded_artifact(
        dataset_artifact_root,
        record.version,
        policy,
    )
    if (
        artifact_manifest.checksum != record.identity_checksum
        or artifact_manifest.rows_checksum != record.artifact_checksum
        or artifact_manifest.feature_version_id != str(source_feature.id)
        or artifact_manifest.feature_schema_version != record.schema_version
        or artifact_manifest.row_count != record.row_count
    ):
        raise Gen1AdmissionError("A2 ORM and artifact identities do not reconcile")
    build = DatasetBuildResult(
        manifest=artifact_manifest,
        rows=rows,
        exclusions=(),
    )
    readiness = quality.get("readiness")
    admission = admit_gen1_build(
        build,
        source_feature_version_id=str(source_feature.id),
        source_feature_version_checksum=feature_version_checksum(source_feature),
        verified_artifact_rows_checksum=verified_rows_checksum,
        policy=policy,
        persisted_readiness=readiness if isinstance(readiness, Mapping) else None,
    )
    if (
        admission.dataset_id != GEN1_APPROVED_A2_DATASET_ID
        or admission.dataset_rows_checksum != GEN1_APPROVED_A2_ROWS_CHECKSUM
        or admission.row_count != GEN1_APPROVED_A2_ROW_COUNT
        or admission.event_date_count != GEN1_APPROVED_A2_EVENT_DATE_COUNT
    ):
        raise Gen1AdmissionError("Approved strict A2 inventory failed exact reconciliation")
    return admission


def _gen1_feature_definition_from_source(
    development: Gen1DevelopmentResult,
    *,
    source_feature_version_id: str,
    source_feature_version_checksum: str,
) -> dict[str, object]:
    definition = _json_native({
        "stage": "GATE_3B_GEN1_RESEARCH",
        "research_policy_version": MLB_GEN1_RESEARCH_POLICY_VERSION,
        "research_only": True,
        "promotion_eligible": False,
        "source_feature_version_id": source_feature_version_id,
        "source_feature_version_checksum": source_feature_version_checksum,
        "source_feature_schema_version": A2_SCHEMA_VERSION,
        "source_feature_schema_hash": A2_SCHEMA_HASH,
        "universe_feature_schema_hash": GEN1_UNIVERSE_SCHEMA_HASH,
        "derived_feature_schema_version": (
            f"gen1-v1-{development.selected_schema.schema_hash[:16]}"
        ),
        "derived_feature_schema_hash": development.selected_schema.schema_hash,
        "model_input_schema_version": development.selected_schema.version,
        "model_input_schema_hash": development.selected_schema.schema_hash,
        "feature_contract_version": development.metadata()[
            "feature_contract_version"
        ],
        "feature_contract_hash": development.metadata()["feature_contract_hash"],
        "feature_contract": development.metadata()["feature_contract"],
        "recent_variant": development.recent_variant.name,
        "raw_feature_names": list(development.selected_schema.raw_names),
        "ordered_output_names": list(development.selected_schema.output_names),
        "missing_value_policy": (
            "PRIMARY_A2_NEUTRAL_PLUS_PAIRED_INDICATOR; "
            "DEV_ONLY_TRAINING_MEDIAN_AND_XGB_NATIVE_MISSING_COMPARATORS"
        ),
        "lifecycle_inputs_excluded": True,
        "tier_b_target_context_excluded": True,
    })
    if not isinstance(definition, dict):  # pragma: no cover - structural guard
        raise Gen1AdmissionError("Gen-1 feature definition is not a JSON object")
    return definition


def _gen1_feature_definition(
    development: Gen1DevelopmentResult,
    admission: Gen1AdmissionResult,
) -> dict[str, object]:
    return _gen1_feature_definition_from_source(
        development,
        source_feature_version_id=admission.source_feature_version_id,
        source_feature_version_checksum=admission.source_feature_version_checksum,
    )


def prospective_gen1_feature_version(
    *,
    development: Gen1DevelopmentResult,
    admission: Gen1AdmissionResult,
) -> FeatureVersion:
    """Build the deterministic Phase-1 identity without inserting ORM state."""

    definition = _gen1_feature_definition(development, admission)
    version = f"gen1-v1-{development.selected_schema.schema_hash[:16]}"
    identity = uuid5(
        NAMESPACE_URL,
        "ai-sports-predictor/mlb-gen1-feature/" + _checksum(
            {
                "name": GEN1_FEATURE_VERSION_NAME,
                "version": version,
                "schema_hash": development.selected_schema.schema_hash,
                "definition": definition,
            }
        ),
    )
    return FeatureVersion(
        id=identity,
        name=GEN1_FEATURE_VERSION_NAME,
        version=version,
        schema_hash=development.selected_schema.schema_hash,
        definition=definition,
    )


def ensure_gen1_feature_version(
    session: Session,
    *,
    development: Gen1DevelopmentResult,
    admission: Gen1AdmissionResult,
) -> FeatureVersion:
    """Register a distinct derived feature identity; never reuse the A2 ID."""

    expected = prospective_gen1_feature_version(
        development=development,
        admission=admission,
    )
    existing = session.scalar(
        select(FeatureVersion).where(
            FeatureVersion.name == GEN1_FEATURE_VERSION_NAME,
            FeatureVersion.version == expected.version,
        )
    )
    if existing is not None:
        if (
            existing.id != expected.id
            or existing.schema_hash != development.selected_schema.schema_hash
            or existing.definition != expected.definition
            or str(existing.id) == admission.source_feature_version_id
        ):
            raise Gen1AdmissionError("Existing Gen-1 FeatureVersion drifted or aliases A2")
        return existing
    row = expected
    session.add(row)
    session.flush()
    if str(row.id) == admission.source_feature_version_id:
        raise Gen1AdmissionError("Derived Gen-1 FeatureVersion cannot reuse the A2 identity")
    return row


def model_version_lineage_parameters(
    *,
    admission: Gen1AdmissionResult,
    development: Gen1DevelopmentResult,
    derived_feature_version: FeatureVersion,
) -> dict[str, object]:
    """Exact immutable field names consumed by public integrity mapping."""

    if (
        derived_feature_version.schema_hash != development.selected_schema.schema_hash
        or str(derived_feature_version.id) == admission.source_feature_version_id
    ):
        raise Gen1AdmissionError("Derived Gen-1 FeatureVersion identity is inconsistent")
    return {
        "source_feature_version_id": admission.source_feature_version_id,
        "source_feature_version_checksum": admission.source_feature_version_checksum,
        "source_feature_schema_version": A2_SCHEMA_VERSION,
        "source_feature_schema_hash": A2_SCHEMA_HASH,
        "derived_feature_version_id": str(derived_feature_version.id),
        "derived_feature_version_checksum": feature_version_checksum(
            derived_feature_version
        ),
        "derived_feature_schema_version": derived_feature_version.version,
        "derived_feature_schema_hash": derived_feature_version.schema_hash,
        "model_input_schema_version": development.selected_schema.version,
        "model_input_schema_hash": development.selected_schema.schema_hash,
        "feature_contract_hash": development.metadata()["feature_contract_hash"],
        "research_policy_version": MLB_GEN1_RESEARCH_POLICY_VERSION,
        "research_only": True,
        "promotion_eligible": False,
    }


def dependency_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("numpy", "scikit-learn", "xgboost", "joblib"):
        try:
            result[name] = dependency_version(name)
        except Exception:
            result[name] = "unavailable"
    return result


def _require_approved_candidate_source(
    candidate: Gen1FrozenArtifactManifest,
) -> None:
    if (
        candidate.dataset_id != GEN1_APPROVED_A2_DATASET_ID
        or candidate.dataset_identity_checksum
        != GEN1_APPROVED_A2_IDENTITY_CHECKSUM
        or candidate.dataset_rows_checksum != GEN1_APPROVED_A2_ROWS_CHECKSUM
        or candidate.source_feature_version_id
        != GEN1_APPROVED_A2_FEATURE_VERSION_ID
    ):
        raise Gen1AdmissionError(
            "Gate 3B operator candidate is not bound to the approved strict A2 source"
        )


def _require_approved_admission_source(admission: Gen1AdmissionResult) -> None:
    if (
        admission.dataset_id != GEN1_APPROVED_A2_DATASET_ID
        or admission.dataset_identity_checksum
        != GEN1_APPROVED_A2_IDENTITY_CHECKSUM
        or admission.dataset_rows_checksum != GEN1_APPROVED_A2_ROWS_CHECKSUM
        or admission.source_feature_version_id
        != GEN1_APPROVED_A2_FEATURE_VERSION_ID
        or admission.row_count != GEN1_APPROVED_A2_ROW_COUNT
        or admission.event_date_count != GEN1_APPROVED_A2_EVENT_DATE_COUNT
    ):
        raise Gen1AdmissionError(
            "Gate 3B operator admission is not bound to the approved strict A2 source"
        )


def freeze_gen1_research_candidate(
    session: Session,
    *,
    admission: Gen1AdmissionResult,
    artifact_store: Gen1ResearchArtifactStore,
    code_revision: str,
    baseline_code_revision: str,
    enable_xgboost: bool = True,
    run_ablations: bool = True,
    require_approved_source: bool = False,
) -> Gen1FrozenRunResult:
    """Development-only fit/freeze. Final-holdout labels remain untouched."""

    if admission.status != GEN1_RESEARCH_ADMISSION_STATUS:
        raise Gen1AdmissionError("Only admitted strict A2 evidence may enter Gen-1")
    if require_approved_source:
        _require_approved_admission_source(admission)
    development = run_gen1_development(
        admission.build.rows,
        policy=admission.policy,
        source_dataset_rows_checksum=admission.dataset_rows_checksum,
        enable_xgboost=enable_xgboost,
        run_ablations=run_ablations,
    )
    feature_version = prospective_gen1_feature_version(
        development=development,
        admission=admission,
    )
    derived_checksum = feature_version_checksum(feature_version)
    artifact = artifact_store.freeze_candidate(
        development,
        dataset_id=admission.dataset_id,
        dataset_identity_checksum=admission.dataset_identity_checksum,
        dataset_rows_checksum=admission.dataset_rows_checksum,
        source_feature_version_id=admission.source_feature_version_id,
        source_feature_version_checksum=admission.source_feature_version_checksum,
        derived_feature_version_id=str(feature_version.id),
        derived_feature_version=feature_version.version,
        derived_feature_version_checksum=derived_checksum,
        code_revision=code_revision,
        baseline_code_revision=baseline_code_revision,
        dependency_versions=dependency_versions(),
    )
    return Gen1FrozenRunResult(
        admission=admission,
        development=development,
        feature_version_id=str(feature_version.id),
        feature_version_checksum=derived_checksum,
        artifact=artifact,
    )


def freeze_gen1_research_candidate_with_policy(
    session: Session,
    *,
    admission: Gen1AdmissionResult,
    artifact_store: Gen1ResearchArtifactStore,
    policy: Gen1ResearchPolicy,
    code_revision: str,
    baseline_code_revision: str,
    enable_xgboost: bool = True,
    run_ablations: bool = True,
) -> Gen1FrozenRunResult:
    if admission.status != GEN1_RESEARCH_ADMISSION_STATUS:
        raise Gen1AdmissionError("Only admitted strict A2 evidence may enter Gen-1")
    if policy != admission.policy:
        raise Gen1AdmissionError("Freeze policy must match the immutable admission policy")
    development = run_gen1_development(
        admission.build.rows,
        policy=policy,
        source_dataset_rows_checksum=admission.dataset_rows_checksum,
        enable_xgboost=enable_xgboost,
        run_ablations=run_ablations,
    )
    feature_version = prospective_gen1_feature_version(
        development=development,
        admission=admission,
    )
    derived_checksum = feature_version_checksum(feature_version)
    artifact = artifact_store.freeze_candidate(
        development,
        dataset_id=admission.dataset_id,
        dataset_identity_checksum=admission.dataset_identity_checksum,
        dataset_rows_checksum=admission.dataset_rows_checksum,
        source_feature_version_id=admission.source_feature_version_id,
        source_feature_version_checksum=admission.source_feature_version_checksum,
        derived_feature_version_id=str(feature_version.id),
        derived_feature_version=feature_version.version,
        derived_feature_version_checksum=derived_checksum,
        code_revision=code_revision,
        baseline_code_revision=baseline_code_revision,
        dependency_versions=dependency_versions(),
    )
    return Gen1FrozenRunResult(
        admission=admission,
        development=development,
        feature_version_id=str(feature_version.id),
        feature_version_checksum=derived_checksum,
        artifact=artifact,
    )


def register_terminal_gen1_research_evaluation(
    session: Session,
    *,
    artifact_store: Gen1ResearchArtifactStore,
    candidate: Gen1FrozenArtifactManifest,
    development: Gen1DevelopmentResult,
    evaluation_artifact: Gen1EvaluationArtifactManifest,
    evaluation: Gen1HoldoutEvaluation,
    code_revision: str,
) -> tuple[ModelVersion, ModelEvaluation]:
    """Append immutable ORM research evidence only after terminal Phase 2."""

    stored_development, stored_candidate = artifact_store.load_candidate(
        candidate.artifact_identity_checksum
    )
    stored_evaluation, stored_evaluation_artifact = artifact_store.load_evaluation(
        candidate.artifact_identity_checksum
    )
    if (
        stored_candidate != candidate
        or stored_evaluation_artifact != evaluation_artifact
        or stored_development.candidate_identity_checksum
        != development.candidate_identity_checksum
        or stored_evaluation.evaluation_identity_checksum
        != evaluation.evaluation_identity_checksum
    ):
        raise Gen1AdmissionError("Terminal Gen-1 stored artifacts drifted from registration input")
    development = stored_development
    candidate = stored_candidate
    evaluation = stored_evaluation
    evaluation_artifact = stored_evaluation_artifact
    if (
        evaluation_artifact.status != "RESEARCH_EVALUATED_NO_PROMOTION"
        or evaluation_artifact.promotion_decision != "RESEARCH_ONLY_NO_PROMOTION"
        or evaluation_artifact.evaluation_identity_checksum
        != evaluation.evaluation_identity_checksum
        or candidate.development_identity_checksum
        != development.candidate_identity_checksum
        or candidate.artifact_identity_checksum
        != evaluation_artifact.candidate_artifact_identity_checksum
        or candidate.code_revision != code_revision
    ):
        raise Gen1AdmissionError("Terminal Gen-1 registration identities do not reconcile")
    manifest = session.scalar(
        select(TrainingDatasetManifest).where(
            TrainingDatasetManifest.identity_checksum
            == candidate.dataset_identity_checksum
        )
    )
    if (
        manifest is None
        or str(manifest.feature_version_id) != candidate.source_feature_version_id
        or manifest.artifact_checksum != candidate.dataset_rows_checksum
    ):
        raise Gen1AdmissionError("Terminal Gen-1 source manifest lineage is invalid")
    derived_definition = _gen1_feature_definition_from_source(
        development,
        source_feature_version_id=candidate.source_feature_version_id,
        source_feature_version_checksum=candidate.source_feature_version_checksum,
    )
    expected_derived = FeatureVersion(
        id=UUID(candidate.derived_feature_version_id),
        name=GEN1_FEATURE_VERSION_NAME,
        version=candidate.derived_feature_version,
        schema_hash=candidate.derived_feature_schema_hash,
        definition=derived_definition,
    )
    if feature_version_checksum(expected_derived) != candidate.derived_feature_version_checksum:
        raise Gen1AdmissionError("Terminal Gen-1 prospective feature identity drifted")
    derived_feature = session.get(FeatureVersion, expected_derived.id)
    colliding_feature = session.scalar(
        select(FeatureVersion).where(
            FeatureVersion.name == GEN1_FEATURE_VERSION_NAME,
            FeatureVersion.version == candidate.derived_feature_version,
        )
    )
    if derived_feature is None:
        if colliding_feature is not None:
            raise Gen1AdmissionError("Terminal Gen-1 feature version collision")
        derived_feature = expected_derived
        session.add(derived_feature)
        session.flush()
    if (
        derived_feature.version != candidate.derived_feature_version
        or derived_feature.schema_hash != candidate.derived_feature_schema_hash
        or derived_feature.definition != derived_definition
        or feature_version_checksum(derived_feature)
        != candidate.derived_feature_version_checksum
    ):
        raise Gen1AdmissionError("Terminal Gen-1 derived feature lineage is invalid")
    lineage = {
        "source_feature_version_id": candidate.source_feature_version_id,
        "source_feature_version_checksum": candidate.source_feature_version_checksum,
        "source_feature_schema_version": candidate.source_feature_schema_version,
        "source_feature_schema_hash": candidate.source_feature_schema_hash,
        "derived_feature_version_id": candidate.derived_feature_version_id,
        "derived_feature_version_checksum": candidate.derived_feature_version_checksum,
        "derived_feature_schema_version": derived_feature.version,
        "derived_feature_schema_hash": derived_feature.schema_hash,
        "model_input_schema_version": candidate.model_input_schema_version,
        "model_input_schema_hash": candidate.model_input_schema_hash,
        "feature_contract_version": candidate.feature_contract_version,
        "feature_contract_hash": candidate.feature_contract_hash,
        "research_policy_version": MLB_GEN1_RESEARCH_POLICY_VERSION,
        "baseline_code_revision": candidate.baseline_code_revision,
        "research_only": True,
        "promotion_eligible": False,
        "research_decision": evaluation.research_decision,
        "research_decision_rule": asdict(development.research_decision_rule),
    }
    json_lineage = _json_native(lineage)
    orm_status_name = development.research_decision_rule.orm_status_mapping.get(
        evaluation.research_decision
    )
    if orm_status_name not in {"CANDIDATE", "CHALLENGER", "DEVELOPMENT"}:
        raise Gen1AdmissionError("Frozen research decision has no safe ORM status")
    orm_status = {
        "CANDIDATE": ModelStatus.CANDIDATE,
        "CHALLENGER": ModelStatus.CHALLENGER,
        "DEVELOPMENT": ModelStatus.DEVELOPMENT,
    }[orm_status_name]
    validation_window = {
        "start": min(item.validation_started_at for item in development.folds).isoformat(),
        "end": max(item.validation_ended_at for item in development.folds).isoformat(),
        "folds": len({item.fold_index for item in development.folds}),
        "games": len(
            {
                game_id
                for item in development.folds
                for game_id in item.validation_game_ids
            }
        ),
    }
    holdout_window = {
        "start": development.split.holdout_dates[0].isoformat(),
        "end": development.split.holdout_dates[-1].isoformat(),
        "games": evaluation.observations,
        "identity_checksum": development.split.holdout_identity_checksum,
    }
    candidate_model_path = (
        artifact_store.root
        / "candidates"
        / candidate.artifact_identity_checksum
        / candidate.model_filename
    ).resolve()
    evaluation_predictions_path = (
        artifact_store.root
        / "evaluations"
        / candidate.artifact_identity_checksum
        / evaluation_artifact.predictions_filename
    ).resolve()
    calibration_candidates = development.calibration.get("candidate_evidence")
    selected_method = development.calibration.get("method")
    selected_calibration_evidence = (
        calibration_candidates.get(selected_method)
        if isinstance(calibration_candidates, Mapping)
        and isinstance(selected_method, str)
        else None
    )
    if not isinstance(selected_calibration_evidence, Mapping):
        raise Gen1AdmissionError("Selected development calibration evidence is missing")
    validation_metrics = {
        key: selected_calibration_evidence.get(key)
        for key in (
            "observations",
            "brier_score",
            "log_loss",
            "expected_calibration_error",
            "calibration_intercept",
            "calibration_slope",
            "accuracy",
            "roc_auc",
        )
    }
    model_identity = _checksum(
        {
            "candidate_artifact": candidate.artifact_identity_checksum,
            "terminal_evaluation": evaluation_artifact.artifact_identity_checksum,
            "derived_feature_version": str(derived_feature.id),
            "training_manifest": str(manifest.id),
            "research_decision": evaluation.research_decision,
            "code_revision": code_revision,
        }
    )
    model = session.scalar(
        select(ModelVersion).where(ModelVersion.identity_checksum == model_identity)
    )
    if model is None:
        model = ModelVersion(
            name="mlb_gen1_research",
            version=f"gen1-evaluated-{evaluation.evaluation_identity_checksum[:16]}",
            feature_version_id=derived_feature.id,
            algorithm=(
                "chronological_gen1_baselines_logistic_poisson_nb_eb_xgb_stack_calibrated"
            ),
            status=orm_status,
            training_window=_json_native({
                "start": development.split.development_dates[0].isoformat(),
                "end": development.split.development_dates[-1].isoformat(),
                "games": len(development.split.development_game_ids),
            }),
            validation_window=_json_native(validation_window),
            holdout_window=_json_native(holdout_window),
            hyperparameters=_json_native(asdict(development.policy)),
            artifact_uri=candidate_model_path.as_posix(),
            artifact_checksum=candidate.model_sha256,
            calibrator_uri=candidate_model_path.as_posix(),
            calibrator_checksum=candidate.model_sha256,
            parameters=json_lineage,
            metrics=_json_native({
                "validation": validation_metrics,
                "holdout": asdict(evaluation.candidate_metrics),
                "selected_raw_development_oof": asdict(
                    development.selected_oof_metrics
                ),
                "final_holdout_secondary": dict(
                    evaluation.candidate_secondary_metrics
                ),
                "research_decision": evaluation.research_decision,
                "research_decision_checks": dict(
                    evaluation.research_decision_checks
                ),
            }),
            target="home_win",
            training_manifest_id=manifest.id,
            ensemble_weights=_json_native(
                development.fitted_bundle.stacker.fit_.weight_map
                if development.fitted_bundle.stacker.fit_ is not None
                else {}
            ),
            code_revision=code_revision,
            identity_checksum=model_identity,
            feature_names=_json_native(list(development.selected_schema.output_names)),
            seed_policy=_json_native({
                "random_seed": development.policy.random_seed,
                "fold_local_only": True,
                "holdout_selection_use": False,
            }),
            target_definition=_json_native({
                **lineage,
                "target": "official_home_win",
                "label_source": "exact_frozen_strict_a2_artifact_after_claim",
            }),
            promotion_decision="NOT_PROMOTED",
            promotion_reason=(
                f"{evaluation.research_decision}; frozen Gen-1 research policy forbids live promotion"
            ),
            trained_at=datetime.fromisoformat(candidate.created_at),
        )
        session.add(model)
        session.flush()
    elif (
        model.feature_version_id != derived_feature.id
        or model.training_manifest_id != manifest.id
        or model.status != orm_status
        or model.parameters != json_lineage
    ):
        raise Gen1AdmissionError("Existing terminal Gen-1 ModelVersion drifted")
    evaluation_identity = _checksum(
        {
            "model_identity": model.identity_checksum,
            "artifact_identity": evaluation_artifact.artifact_identity_checksum,
            "evaluation_identity": evaluation.evaluation_identity_checksum,
            "research_decision": evaluation.research_decision,
        }
    )
    record = session.scalar(
        select(ModelEvaluation).where(
            ModelEvaluation.identity_checksum == evaluation_identity
        )
    )
    if record is None:
        record = ModelEvaluation(
            model_version_id=model.id,
            training_manifest_id=manifest.id,
            evaluation_type="GEN1_RESEARCH_FINAL_HOLDOUT",
            status="COMPLETED",
            validation_window=_json_native(validation_window),
            holdout_window=_json_native(holdout_window),
            metrics=_json_native({
                "validation": validation_metrics,
                "holdout": asdict(evaluation.candidate_metrics),
                "selected_raw_development_oof": asdict(
                    development.selected_oof_metrics
                ),
                "secondary": dict(evaluation.candidate_secondary_metrics),
                "run_metrics": {
                    name: dict(value) for name, value in evaluation.run_metrics.items()
                },
                "candidate_metric_intervals": {
                    name: dict(value)
                    for name, value in evaluation.candidate_metric_intervals.items()
                },
                "probability_bands": list(evaluation.probability_bands),
                "research_decision": evaluation.research_decision,
                "research_decision_checks": dict(
                    evaluation.research_decision_checks
                ),
                "promotion_decision": "RESEARCH_ONLY_NO_PROMOTION",
                "rolling_origin_folds": len(
                    {item.fold_index for item in development.folds}
                ),
            }),
            baseline_metrics=_json_native({
                name: {
                    **asdict(value),
                    "secondary": dict(
                        evaluation.comparison_secondary_metrics[name]
                    ),
                }
                for name, value in evaluation.comparison_metrics.items()
            }),
            slice_metrics=_json_native({
                "evaluation_policy_version": MLB_GEN1_RESEARCH_POLICY_VERSION,
                "research_decision": evaluation.research_decision,
                "subgroups": {
                    name: dict(value)
                    for name, value in evaluation.subgroup_metrics.items()
                },
                "probability_bands": list(evaluation.probability_bands),
            }),
            calibration=_json_native(dict(development.calibration)),
            prediction_artifact_uri=evaluation_predictions_path.as_posix(),
            prediction_artifact_checksum=evaluation_artifact.predictions_sha256,
            evaluated_at=datetime.fromisoformat(evaluation_artifact.evaluated_at),
            source_mode="LIVE",
            code_revision=code_revision,
            identity_checksum=evaluation_identity,
            superiority_claim=False,
            promotion_recommendation=False,
            notes=(
                f"research_decision={evaluation.research_decision}; "
                "promotion=RESEARCH_ONLY_NO_PROMOTION"
            ),
        )
        session.add(record)
        session.flush()
    elif (
        record.model_version_id != model.id
        or record.training_manifest_id != manifest.id
        or record.metrics is None
        or record.metrics.get("research_decision") != evaluation.research_decision
    ):
        raise Gen1AdmissionError("Existing terminal Gen-1 ModelEvaluation drifted")
    return model, record


def resume_terminal_gen1_registration(
    session: Session,
    *,
    artifact_store: Gen1ResearchArtifactStore,
    candidate_artifact_identity_checksum: str,
    code_revision: str,
    baseline_code_revision: str,
    require_approved_source: bool = False,
) -> Gen1EvaluationRunResult:
    """Idempotently finish ORM registration from revalidated terminal artifacts."""

    development, candidate = artifact_store.load_candidate(
        candidate_artifact_identity_checksum
    )
    if (
        candidate.code_revision != code_revision
        or candidate.baseline_code_revision != baseline_code_revision
    ):
        raise Gen1AdmissionError(
            "Current Gate 3B code identity does not match the frozen candidate"
        )
    if require_approved_source:
        _require_approved_candidate_source(candidate)
    evaluation, evaluation_artifact = artifact_store.load_evaluation(
        candidate_artifact_identity_checksum
    )
    model, record = register_terminal_gen1_research_evaluation(
        session,
        artifact_store=artifact_store,
        candidate=candidate,
        development=development,
        evaluation_artifact=evaluation_artifact,
        evaluation=evaluation,
        code_revision=code_revision,
    )
    session.commit()
    return Gen1EvaluationRunResult(
        evaluation=evaluation,
        artifact=evaluation_artifact,
        model_version_id=str(model.id),
        model_evaluation_id=str(record.id),
    )


def evaluate_frozen_gen1_candidate_once(
    *,
    session: Session,
    artifact_store: Gen1ResearchArtifactStore,
    candidate_artifact_identity_checksum: str,
    dataset_artifact_root: Path,
    code_revision: str,
    baseline_code_revision: str,
    require_approved_source: bool = False,
) -> Gen1EvaluationRunResult:
    """Claim first, then load labels only from the exact frozen A2 artifact."""

    _, preclaim_candidate = artifact_store.load_candidate(
        candidate_artifact_identity_checksum
    )
    if (
        preclaim_candidate.code_revision != code_revision
        or preclaim_candidate.baseline_code_revision != baseline_code_revision
    ):
        raise Gen1AdmissionError(
            "Current Gate 3B code identity does not match the frozen candidate"
        )
    if require_approved_source:
        _require_approved_candidate_source(preclaim_candidate)
    claim = artifact_store.claim_holdout_evaluation(
        candidate_artifact_identity_checksum
    )
    try:
        development, candidate = artifact_store.load_candidate(
            candidate_artifact_identity_checksum
        )
        source_manifest, source_rows = TrainingDatasetArtifactStore(
            dataset_artifact_root
        ).load(candidate.dataset_id)
        if (
            source_manifest.dataset_id != candidate.dataset_id
            or source_manifest.checksum != candidate.dataset_identity_checksum
            or source_manifest.rows_checksum != candidate.dataset_rows_checksum
            or source_manifest.rows_checksum
            != development.source_dataset_rows_checksum
            or source_manifest.feature_version_id
            != candidate.source_feature_version_id
        ):
            raise Gen1AdmissionError(
                "Claimed Phase-2 source artifact does not match the frozen A2 identity"
            )
        source_ids = [row.game_id for row in source_rows]
        if len(set(source_ids)) != len(source_ids):
            raise Gen1AdmissionError(
                "Claimed Phase-2 source artifact contains duplicate game identities"
            )
        by_id = {row.game_id: row for row in source_rows}
        if any(game_id not in by_id for game_id in development.split.holdout_game_ids):
            raise Gen1AdmissionError(
                "Claimed Phase-2 source artifact is missing frozen holdout games"
            )
        labeled_holdout_rows = tuple(
            by_id[game_id] for game_id in development.split.holdout_game_ids
        )
        evaluation = evaluate_gen1_holdout(
            development,
            labeled_holdout_rows,
            verified_source_dataset_rows_checksum=source_manifest.rows_checksum,
        )
        artifact = artifact_store.complete_holdout_evaluation(claim, evaluation)
        model, record = register_terminal_gen1_research_evaluation(
            session,
            artifact_store=artifact_store,
            candidate=candidate,
            development=development,
            evaluation_artifact=artifact,
            evaluation=evaluation,
            code_revision=code_revision,
        )
        session.commit()
    except Exception as exc:
        artifact_store.record_failed_evaluation(claim, exc)
        session.rollback()
        raise
    return Gen1EvaluationRunResult(
        evaluation=evaluation,
        artifact=artifact,
        model_version_id=str(model.id),
        model_evaluation_id=str(record.id),
    )


def _operator_paths() -> tuple[Path, Path]:
    from app.core.config import get_settings

    settings = get_settings()
    return (
        (settings.gate3_artifact_root / "datasets").resolve(),
        (settings.gate3_artifact_root / "gate3b").resolve(),
    )


def _operator_code_identity() -> tuple[str, str]:
    import subprocess

    repository_root = Path(__file__).resolve().parents[3]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    relative_files = (
        "server/app/historical/a2_schema.py",
        "server/app/modeling/ablation.py",
        "server/app/modeling/calibration.py",
        "server/app/modeling/diagnostics.py",
        "server/app/modeling/estimators.py",
        "server/app/modeling/gen1.py",
        "server/app/modeling/metrics.py",
        "server/app/modeling/preprocessing.py",
        "server/app/modeling/schema.py",
        "server/app/modeling/stacking.py",
        "server/app/modeling/temporal.py",
        "server/app/models/entities.py",
        "server/app/training/dataset.py",
        "server/app/training/gate3b.py",
        "server/app/training/gate3b_artifacts.py",
        "server/pyproject.toml",
    )
    files = tuple(repository_root / value for value in relative_files)
    if any(not path.is_file() for path in files):
        raise Gen1AdmissionError("Gate 3B code-identity source inventory is incomplete")
    digest = sha256()
    digest.update(f"baseline_head={head}\n".encode("utf-8"))
    for path in sorted(files):
        relative = path.relative_to(repository_root).as_posix()
        payload = path.read_bytes()
        digest.update(f"{relative}\0{len(payload)}\0".encode("utf-8"))
        digest.update(payload)
    return f"gate3b-content-{digest.hexdigest()}", head


def _operator_main() -> None:
    """Provider-free, explicit two-phase Gate 3B operator entrypoint."""

    import argparse

    from app.audit import install_immutability_guards
    from app.db.session import SessionLocal

    parser = argparse.ArgumentParser(
        description="Provider-free governed MLB Gen-1 research workflow"
    )
    parser.add_argument(
        "command",
        choices=(
            "inspect-source",
            "phase1-freeze",
            "inspect-candidate",
            "phase2-evaluate",
            "resume-registration",
        ),
    )
    parser.add_argument("--manifest-identity")
    parser.add_argument("--candidate-identity")
    args = parser.parse_args()
    dataset_root, artifact_root = _operator_paths()
    store = Gen1ResearchArtifactStore(artifact_root)
    if args.command in {"inspect-source", "phase1-freeze"}:
        if not args.manifest_identity:
            parser.error(f"{args.command} requires --manifest-identity")
        if args.manifest_identity != GEN1_APPROVED_A2_IDENTITY_CHECKSUM:
            parser.error("manifest identity is not the approved Gate 3B A2 source")
    if args.command in {
        "inspect-candidate",
        "phase2-evaluate",
        "resume-registration",
    } and not args.candidate_identity:
        parser.error(f"{args.command} requires --candidate-identity")
    code_revision, baseline_code_revision = _operator_code_identity()
    install_immutability_guards()
    with SessionLocal() as session:
        if args.command == "inspect-source":
            admission = load_gen1_a2_admission(
                session,
                dataset_artifact_root=dataset_root,
                manifest_identity_checksum=args.manifest_identity,
            )
            result: object = {
                "mode": "READ_ONLY_SOURCE_INSPECTION",
                **admission.metadata(),
                "phase1_holdout_target_values_materialized": False,
                "provider_calls": 0,
                "model_fit_invoked": False,
            }
        elif args.command == "phase1-freeze":
            admission = load_gen1_a2_admission(
                session,
                dataset_artifact_root=dataset_root,
                manifest_identity_checksum=args.manifest_identity,
            )
            frozen = freeze_gen1_research_candidate(
                session,
                admission=admission,
                artifact_store=store,
                code_revision=code_revision,
                baseline_code_revision=baseline_code_revision,
                require_approved_source=True,
            )
            result = {
                "status": frozen.development.status,
                "candidate_artifact_identity_checksum": (
                    frozen.artifact.artifact_identity_checksum
                ),
                "holdout_identity_checksum": (
                    frozen.development.split.holdout_identity_checksum
                ),
                "source_dataset_rows_checksum": admission.dataset_rows_checksum,
                "prospective_derived_feature_version_id": frozen.feature_version_id,
                "orm_rows_inserted": 0,
                "holdout_labels_accessed": False,
                "provider_calls": 0,
            }
        elif args.command == "inspect-candidate":
            development, candidate = store.load_candidate(args.candidate_identity)
            try:
                _, terminal = store.load_evaluation(args.candidate_identity)
            except FileNotFoundError:
                terminal = None
            result = {
                "mode": "READ_ONLY_CANDIDATE_INSPECTION",
                "candidate": asdict(candidate),
                "development_status": development.status,
                "terminal_evaluation": asdict(terminal) if terminal else None,
                "provider_calls": 0,
            }
        elif args.command == "phase2-evaluate":
            evaluated = evaluate_frozen_gen1_candidate_once(
                session=session,
                artifact_store=store,
                candidate_artifact_identity_checksum=args.candidate_identity,
                dataset_artifact_root=dataset_root,
                code_revision=code_revision,
                baseline_code_revision=baseline_code_revision,
                require_approved_source=True,
            )
            result = {
                "status": evaluated.evaluation.status,
                "research_decision": evaluated.evaluation.research_decision,
                "promotion_decision": evaluated.evaluation.promotion_decision,
                "model_version_id": evaluated.model_version_id,
                "model_evaluation_id": evaluated.model_evaluation_id,
                "evaluation_artifact_identity_checksum": (
                    evaluated.artifact.artifact_identity_checksum
                ),
                "provider_calls": 0,
            }
        else:
            evaluated = resume_terminal_gen1_registration(
                session,
                artifact_store=store,
                candidate_artifact_identity_checksum=args.candidate_identity,
                code_revision=code_revision,
                baseline_code_revision=baseline_code_revision,
            )
            result = {
                "status": "TERMINAL_REGISTRATION_RECONCILED",
                "research_decision": evaluated.evaluation.research_decision,
                "model_version_id": evaluated.model_version_id,
                "model_evaluation_id": evaluated.model_evaluation_id,
                "provider_calls": 0,
            }
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


__all__ = [
    "GEN1_APPROVED_A2_DATASET_ID",
    "GEN1_APPROVED_A2_IDENTITY_CHECKSUM",
    "GEN1_APPROVED_A2_MANIFEST_ID",
    "GEN1_APPROVED_A2_ROWS_CHECKSUM",
    "GEN1_A2_CUTOFF_POLICY",
    "GEN1_A2_POPULATION",
    "GEN1_A2_STRICT_MANIFEST_NAME",
    "GEN1_A2_TEMPORAL_SEMANTICS",
    "GEN1_FEATURE_VERSION_NAME",
    "GEN1_RESEARCH_ADMISSION_STATUS",
    "GEN1_RESEARCH_REJECTED_STATUS",
    "Gen1AdmissionError",
    "Gen1AdmissionResult",
    "Gen1EvaluationRunResult",
    "Gen1FrozenRunResult",
    "admit_gen1_build",
    "dependency_versions",
    "ensure_gen1_feature_version",
    "evaluate_frozen_gen1_candidate_once",
    "feature_version_checksum",
    "freeze_gen1_research_candidate",
    "freeze_gen1_research_candidate_with_policy",
    "load_gen1_a2_admission",
    "model_version_lineage_parameters",
    "prospective_gen1_feature_version",
    "register_terminal_gen1_research_evaluation",
    "resume_terminal_gen1_registration",
]


if __name__ == "__main__":
    _operator_main()
