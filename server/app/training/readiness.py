"""Provider-free Gate 3 dataset readiness checks that never fit or score a model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from math import ceil, isfinite
from typing import Any, Iterable, Mapping

from app.training.dataset import TrainingDatasetManifestData, TrainingRow


READY_STATUS = "READY_FOR_GATE_3B_MODEL_DEVELOPMENT"
NOT_READY_STATUS = "NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT"
A2_DATASET_POPULATION = "HISTORICAL_RECONSTRUCTION_A2"
A2_CUTOFF_POLICY_VERSION = "mlb_t_minus_6h_v1"
A2_LINEAGE_VERSION = "gate3a_a2_compact_lineage_v1"
_FORBIDDEN_FEATURE_TOKENS = ("market", "odds", "closing", "clv")
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


@dataclass(frozen=True)
class DatasetReadinessPolicy:
    version: str = "gate3a_a2_audit_only_readiness_v1"
    minimum_rows: int = 200
    minimum_holdout_rows: int = 50
    holdout_fraction: float = 0.20
    minimum_event_dates: int = 41
    minimum_development_dates: int = 40
    minimum_rolling_folds: int = 3
    initial_training_dates: int = 30
    validation_dates: int = 7
    fold_step_dates: int = 7
    minimum_feature_family_coverage: float = 0.70

    def __post_init__(self) -> None:
        if self.minimum_rows <= 0 or self.minimum_holdout_rows <= 0:
            raise ValueError("Readiness row thresholds must be positive")
        if not 0 < self.holdout_fraction < 0.5:
            raise ValueError("Readiness holdout_fraction must be between zero and 0.5")
        if not 0 <= self.minimum_feature_family_coverage <= 1:
            raise ValueError("minimum_feature_family_coverage must be between zero and one")
        if min(
            self.minimum_event_dates,
            self.minimum_development_dates,
            self.minimum_rolling_folds,
            self.initial_training_dates,
            self.validation_dates,
            self.fold_step_dates,
        ) <= 0:
            raise ValueError("Readiness temporal thresholds must be positive")


@dataclass(frozen=True)
class DatasetReadinessResult:
    status: str
    policy_version: str
    checks: dict[str, dict[str, Any]]
    row_count: int
    distinct_game_count: int
    distinct_event_dates: int
    development_date_groups: int
    holdout_date_groups: int
    holdout_rows: int
    rolling_origin_folds: int
    observed_outcome_classes: tuple[int, ...]
    feature_schema_version: str | None
    feature_version_id: str | None
    model_training_invoked: bool = False
    model_fitting_invoked: bool = False
    holdout_metrics_inspected: bool = False
    holdout_outcomes_inspected: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_historical_dataset_readiness(
    manifest: TrainingDatasetManifestData,
    rows: Iterable[TrainingRow],
    *,
    policy: DatasetReadinessPolicy = DatasetReadinessPolicy(),
) -> DatasetReadinessResult:
    """Check that a governed dataset can enter Gate 3B without using holdout labels.

    The audit deliberately does not import an estimator, construct a temporal
    holdout object, inspect holdout outcomes, or calculate any metric. It only
    verifies population, schema, leakage, date-group, row-count, and overall
    outcome-class prerequisites.
    """

    ordered = tuple(sorted(rows, key=_row_sort_key))
    dates = sorted({_event_at(row).date() for row in ordered})
    holdout_date_count = max(1, ceil(len(dates) * policy.holdout_fraction)) if dates else 0
    split_at = max(0, len(dates) - holdout_date_count)
    development_dates = dates[:split_at]
    holdout_dates = frozenset(dates[split_at:])
    holdout_rows = sum(_event_at(row).date() in holdout_dates for row in ordered)
    folds = _rolling_fold_count(
        len(development_dates),
        initial=policy.initial_training_dates,
        validation=policy.validation_dates,
        step=policy.fold_step_dates,
    )
    # Outcome-class sufficiency is inspected only in the development partition.
    # The held-out rows are counted by date/identity only and their labels are
    # deliberately never dereferenced by this audit.
    development_rows = tuple(
        row for row in ordered if _event_at(row).date() not in holdout_dates
    )
    classes = tuple(sorted({int(row.home_win) for row in development_rows}))
    game_count = len({row.game_id for row in ordered})
    training_cutoff = _parse_timestamp(manifest.training_cutoff, "manifest training_cutoff")
    manifest_feature_names = tuple(manifest.feature_names)
    manifest_feature_name_set = frozenset(manifest_feature_names)

    checks = {
        "manifest_row_count": _check(
            manifest.row_count == len(ordered),
            {"manifest": manifest.row_count, "loaded": len(ordered)},
            {"exact_match": True},
        ),
        "one_row_per_game": _check(
            game_count == len(ordered),
            {"rows": len(ordered), "distinct_games": game_count},
            {"exact_match": True},
        ),
        "minimum_rows": _check(
            len(ordered) >= policy.minimum_rows,
            len(ordered),
            {"minimum": policy.minimum_rows},
        ),
        "distinct_event_dates": _check(
            len(dates) >= policy.minimum_event_dates,
            len(dates),
            {"minimum": policy.minimum_event_dates},
        ),
        "development_date_groups": _check(
            len(development_dates) >= policy.minimum_development_dates,
            len(development_dates),
            {"minimum": policy.minimum_development_dates},
        ),
        "holdout_formable_without_label_access": _check(
            holdout_rows >= policy.minimum_holdout_rows and bool(holdout_dates),
            {"date_groups": len(holdout_dates), "rows": holdout_rows},
            {"minimum_rows": policy.minimum_holdout_rows},
        ),
        "rolling_origin_folds": _check(
            folds >= policy.minimum_rolling_folds,
            folds,
            {"minimum": policy.minimum_rolling_folds},
        ),
        "overall_outcome_classes": _check(
            classes == (0, 1),
            list(classes),
            {"exact": [0, 1]},
        ),
        "manifest_leakage_gates": _check(
            bool(manifest.leakage_checks)
            and all(value is True for value in manifest.leakage_checks.values()),
            dict(sorted(manifest.leakage_checks.items())),
            {"all": True},
        ),
        "row_point_in_time_contract": _check(
            all(
                _parse_timestamp(row.data_as_of, "row data_as_of")
                == _event_at(row) - timedelta(hours=6)
                and row.source_mode == "LIVE"
                and row.temporal_semantics == "RETROSPECTIVE_RECONSTRUCTION"
                and row.historical_quality_tier == "A"
                and row.cutoff_policy_version == A2_CUTOFF_POLICY_VERSION
                and _valid_source_evidence(row.source_evidence)
                and row.market_home_probability is None
                and row.market_timestamp is None
                and row.market_books == 0
                and row.closing_home_probability is None
                and row.closing_timestamp is None
                and not any(
                    token in str(name).lower()
                    for name in row.features
                    for token in _FORBIDDEN_FEATURE_TOKENS
                )
                and (
                    row.label_available_at is None
                    or _parse_timestamp(row.label_available_at, "row label_available_at")
                    <= training_cutoff
                )
                for row in ordered
            )
            and manifest.quality_tiers == ["A"]
            and manifest.cutoff_policy_version == A2_CUTOFF_POLICY_VERSION,
            {
                "rows_checked": len(ordered),
                "quality_tiers": manifest.quality_tiers,
                "cutoff_policy": manifest.cutoff_policy_version,
                "lineage_version": A2_LINEAGE_VERSION,
            },
            {
                "source_mode": "LIVE",
                "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                "quality_tiers": ["A"],
                "cutoff": "scheduled_first_pitch_minus_6h",
                "cutoff_policy": A2_CUTOFF_POLICY_VERSION,
                "lineage_version": A2_LINEAGE_VERSION,
                "market_fields": "absent",
            },
        ),
        "live_reconstructed_population": _check(
            manifest.source_modes == ["LIVE"]
            and manifest.temporal_semantics == "RETROSPECTIVE_RECONSTRUCTION"
            and manifest.population == A2_DATASET_POPULATION,
            {
                "source_modes": manifest.source_modes,
                "temporal_semantics": manifest.temporal_semantics,
                "population": manifest.population,
            },
            {
                "source_modes": ["LIVE"],
                "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                "population": A2_DATASET_POPULATION,
            },
        ),
        "schema_resolves": _check(
            bool(manifest.feature_schema_version)
            and bool(manifest.feature_version_id)
            and bool(manifest_feature_names)
            and len(manifest_feature_name_set) == len(manifest_feature_names)
            and all(
                row.feature_schema_version == manifest.feature_schema_version
                and row.feature_version_id == manifest.feature_version_id
                and len(row.features) == len(manifest_feature_names)
                and frozenset(row.features) == manifest_feature_name_set
                for row in ordered
            ),
            {
                "schema": manifest.feature_schema_version,
                "feature_version_id": manifest.feature_version_id,
                "feature_count": len(manifest_feature_names),
            },
            {"single_exact_contract": True},
        ),
        "required_feature_family_coverage": _check(
            all(
                _coverage_at_least(
                    manifest.feature_family_coverage.get(family),
                    policy.minimum_feature_family_coverage,
                )
                for family in REQUIRED_FEATURE_FAMILIES
            ),
            {
                family: manifest.feature_family_coverage.get(family)
                for family in REQUIRED_FEATURE_FAMILIES
            },
            {
                "families": list(REQUIRED_FEATURE_FAMILIES),
                "minimum_each": policy.minimum_feature_family_coverage,
            },
        ),
    }
    status = (
        READY_STATUS
        if checks and all(item["passed"] for item in checks.values())
        else NOT_READY_STATUS
    )
    return DatasetReadinessResult(
        status=status,
        policy_version=policy.version,
        checks=checks,
        row_count=len(ordered),
        distinct_game_count=game_count,
        distinct_event_dates=len(dates),
        development_date_groups=len(development_dates),
        holdout_date_groups=len(holdout_dates),
        holdout_rows=holdout_rows,
        rolling_origin_folds=folds,
        observed_outcome_classes=classes,
        feature_schema_version=manifest.feature_schema_version,
        feature_version_id=manifest.feature_version_id,
    )


def _event_at(row: TrainingRow) -> datetime:
    return _parse_timestamp(row.scheduled_first_pitch, "scheduled_first_pitch")


def _parse_timestamp(value: str, label: str) -> datetime:
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"Training row {label} must be timezone-aware")
    return value


def _row_sort_key(row: TrainingRow) -> tuple[datetime, str]:
    return _event_at(row), str(row.game_id)


def _rolling_fold_count(
    development_dates: int,
    *,
    initial: int,
    validation: int,
    step: int,
) -> int:
    count = 0
    start = initial
    while start + validation <= development_dates:
        count += 1
        start += step
    return count


def _check(passed: bool, observed: Any, required: Mapping[str, Any]) -> dict[str, Any]:
    return {"passed": bool(passed), "observed": observed, "required": dict(required)}


def _coverage_at_least(value: object, minimum: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isfinite(float(value)) and float(value) >= minimum


def _valid_source_evidence(value: object) -> bool:
    if not isinstance(value, list) or len(value) != 1:
        return False
    row = value[0]
    if not isinstance(row, dict):
        return False
    return (
        row.get("lineage_version") == A2_LINEAGE_VERSION
        and row.get("target_excluded") is True
        and row.get("temporal_valid") is True
        and all(
            _is_sha256(row.get(name))
            for name in (
                "attestation_identity_checksum",
                "canonical_set_checksum",
                "source_bundle_checksum",
            )
        )
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = [
    "DatasetReadinessPolicy",
    "DatasetReadinessResult",
    "A2_DATASET_POPULATION",
    "NOT_READY_STATUS",
    "REQUIRED_FEATURE_FAMILIES",
    "READY_STATUS",
    "audit_historical_dataset_readiness",
]
