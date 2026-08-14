"""Provider-free MLB Gen-1 research modeling on governed A2 Tier-A rows.

This module is deliberately separate from the production/promotion Gate 3
pipeline.  ``MLB_GEN1_RESEARCH_POLICY_V1`` permits model research on the
supported strict A2 evidence families while preserving the stronger promotion
coverage gate.  Development is label-aware only for the chronological
development partition.  Final-holdout covariates and predictions are frozen
without dereferencing final-holdout labels; label evaluation is a separate
explicit operation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from hashlib import sha256
import json
from math import ceil, exp, isfinite, log
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from app.historical.a2_schema import A2_SCHEMA, A2_SCHEMA_HASH, A2_SCHEMA_VERSION
from app.modeling.ablation import AblationSpec
from app.modeling.calibration import (
    CalibrationSelection,
    IdentityCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    select_probability_calibrator,
)
from app.modeling.diagnostics import OODDetector, OODReport
from app.modeling.estimators import (
    EmpiricalBayesTeamClassifier,
    HomePriorClassifier,
    LimitedXGBoostClassifier,
    NegativeBinomialRunRegressor,
    PoissonRunRegressor,
    RegularizedLogisticClassifier,
)
from app.modeling.metrics import (
    ClassificationMetrics,
    PairedBootstrapDelta,
    brier_score,
    classification_metrics,
    expected_calibration_error,
    paired_block_bootstrap_delta,
    probability_log_loss,
)
from app.modeling.schema import FeatureSpec, FrozenFeatureSchema
from app.modeling.stacking import NonnegativeStacker, OOFPredictionRow
from app.modeling.temporal import LabeledGame, RollingOriginFold, UnlabeledGame, rolling_origin_folds


MLB_GEN1_RESEARCH_POLICY_VERSION = "MLB_GEN1_RESEARCH_POLICY_V1"
GEN1_UNIVERSE_SCHEMA_VERSION = "mlb_historical_pregame_gen1_tier_a_v1"
GEN1_UNIVERSE_SCHEMA_HASH = "586a39dad2b0d4cd8f386cf6162b16694108aab7afc37e72faf1642f33ebfc4a"
GEN1_UNIVERSE_RAW_FEATURE_COUNT = 230
GEN1_UNIVERSE_OUTPUT_COUNT = 460
GEN1_SUPPORTED_SOURCE_FAMILIES = frozenset(
    ("team", "talent", "rating", "recent", "hitting", "pitching", "bullpen", "schedule")
)
GEN1_ANALYSIS_FAMILY_SOURCES: Mapping[str, tuple[str, ...]] = {
    "rating": ("rating",),
    "team": ("team", "talent"),
    "recent_form": ("recent",),
    "hitting": ("hitting",),
    "pitching": ("pitching",),
    "bullpen": ("bullpen",),
    "rest_travel": ("schedule",),
}
GEN1_ANALYSIS_FAMILIES = tuple(GEN1_ANALYSIS_FAMILY_SOURCES)
GEN1_EXCLUDED_SOURCE_FAMILIES = frozenset(("lifecycle", "target_context"))
GEN1_RESEARCH_FROZEN_STATUS = "RESEARCH_CANDIDATE_FROZEN_HOLDOUT_LOCKED"
GEN1_RESEARCH_EVALUATED_STATUS = "RESEARCH_EVALUATED_NO_PROMOTION"
GEN1_FEATURE_CONTRACT_VERSION = "mlb_gen1_feature_contract_v1"
GEN1_RUN_STRENGTH_DRIVER_METHOD = "a2_run_strength_additive_log_odds_v1"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    ).encode("utf-8")


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _checksum(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _require_checksum(value: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{label} must be a timezone-aware timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


def _row_event_at(row: object) -> datetime:
    return _timestamp(getattr(row, "scheduled_first_pitch", None), "scheduled_first_pitch")


def _row_features(row: object) -> Mapping[str, object]:
    value = getattr(row, "features", None)
    if not isinstance(value, Mapping):
        raise ValueError("Gen-1 row features must be a mapping")
    return value


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not isfinite(converted):
        raise ValueError(f"{label} must be finite")
    return converted


def _probability_log_odds(value: object, label: str) -> float:
    probability = _finite_number(value, label)
    if not 0.0 < probability < 1.0:
        raise ValueError(f"{label} must be strictly between zero and one")
    return log(probability / (1.0 - probability))


def _probability_from_log_odds(value: float) -> float:
    if value >= 0:
        inverse = exp(-value)
        return 1.0 / (1.0 + inverse)
    exponential = exp(value)
    return exponential / (1.0 + exponential)


@dataclass(frozen=True, slots=True)
class Gen1XGBoostCandidate:
    name: str
    n_estimators: int
    max_depth: int
    learning_rate: float
    min_child_weight: float = 8.0
    reg_lambda: float = 5.0

    def __post_init__(self) -> None:
        if not self.name or not 1 <= self.n_estimators <= 400:
            raise ValueError("Invalid restrained XGBoost candidate")
        if not 1 <= self.max_depth <= 4 or not 0 < self.learning_rate <= 0.20:
            raise ValueError("XGBoost candidate exceeds the Gen-1 complexity cap")


DEFAULT_XGBOOST_CANDIDATES = (
    Gen1XGBoostCandidate("xgb_d2_n120_lr005", 120, 2, 0.05),
    Gen1XGBoostCandidate("xgb_d3_n120_lr005", 120, 3, 0.05),
    Gen1XGBoostCandidate("xgb_d3_n180_lr003", 180, 3, 0.03),
)


@dataclass(frozen=True, slots=True)
class Gen1ResearchPolicy:
    version: str = MLB_GEN1_RESEARCH_POLICY_VERSION
    holdout_fraction: float = 0.20
    minimum_rows: int = 2_001
    minimum_holdout_rows: int = 200
    minimum_event_dates: int = 151
    minimum_development_dates: int = 40
    minimum_train_dates: int = 30
    validation_dates: int = 7
    step_dates: int = 7
    minimum_rolling_folds: int = 5
    minimum_supported_family_coverage: float = 0.70
    home_prior_probability: float = 0.54
    home_prior_strength: float = 20.0
    team_prior_strength: float = 30.0
    logistic_c: float = 0.25
    poisson_alpha: float = 1.0
    xgboost_minimum_rows: int = 500
    xgboost_candidates: tuple[Gen1XGBoostCandidate, ...] = DEFAULT_XGBOOST_CANDIDATES
    calibration_selection_fraction: float = 0.30
    minimum_isotonic_samples: int = 500
    stack_l2: float = 0.002
    bootstrap_resamples: int = 1_000
    random_seed: int = 20260813
    minimum_subgroup_rows: int = 30
    ensemble_max_log_loss_degradation: float = 0.0
    ensemble_max_brier_degradation: float = 0.0
    ensemble_min_brier_improvement: float = 0.0005
    ensemble_max_ece_degradation: float = 0.0
    ensemble_min_stable_fold_fraction: float = 0.60
    nb_min_brier_improvement: float = 0.0001
    nb_min_log_loss_improvement: float = 0.0005
    research_max_ece: float = 0.05
    research_max_abs_calibration_intercept: float = 0.10
    research_min_calibration_slope: float = 0.80
    research_max_calibration_slope: float = 1.20
    research_reject_brier_degradation: float = 0.02
    research_reject_log_loss_degradation: float = 0.05
    research_reject_max_ece: float = 0.15
    research_reject_min_calibration_slope: float = 0.25
    research_reject_max_calibration_slope: float = 3.0

    def __post_init__(self) -> None:
        if self.version != MLB_GEN1_RESEARCH_POLICY_VERSION:
            raise ValueError("Unknown Gen-1 research policy version")
        if not 0 < self.holdout_fraction < 0.5:
            raise ValueError("Gen-1 holdout fraction must be between zero and one half")
        positive = (
            self.minimum_rows,
            self.minimum_holdout_rows,
            self.minimum_event_dates,
            self.minimum_development_dates,
            self.minimum_train_dates,
            self.validation_dates,
            self.step_dates,
            self.minimum_rolling_folds,
            self.bootstrap_resamples,
            self.minimum_subgroup_rows,
        )
        if min(positive) <= 0 or self.minimum_rolling_folds < 5:
            raise ValueError("Gen-1 temporal and sample thresholds are invalid")
        if self.bootstrap_resamples < 50:
            raise ValueError("Gen-1 bootstrap requires at least 50 resamples")
        if not 0 <= self.minimum_supported_family_coverage <= 1:
            raise ValueError("Gen-1 supported-family coverage must be in [0, 1]")
        if (
            not 0 <= self.research_max_ece <= 0.25
            or self.research_max_abs_calibration_intercept < 0
            or not 0 < self.research_min_calibration_slope
            <= self.research_max_calibration_slope
        ):
            raise ValueError("Gen-1 research classification thresholds are invalid")
        if (
            self.ensemble_min_brier_improvement < 0
            or self.ensemble_max_ece_degradation < 0
            or self.nb_min_brier_improvement < 0
            or self.nb_min_log_loss_improvement < 0
            or not 0 <= self.ensemble_min_stable_fold_fraction <= 1
            or self.research_reject_brier_degradation < 0
            or self.research_reject_log_loss_degradation < 0
            or self.research_reject_max_ece < self.research_max_ece
            or not 0 < self.research_reject_min_calibration_slope
            <= self.research_min_calibration_slope
            or self.research_reject_max_calibration_slope
            < self.research_max_calibration_slope
        ):
            raise ValueError("Gen-1 ensemble/rejection thresholds are invalid")
        if not self.xgboost_candidates or len({item.name for item in self.xgboost_candidates}) != len(
            self.xgboost_candidates
        ):
            raise ValueError("Gen-1 XGBoost candidate names must be unique")


_UNIVERSE_DEFINITION = FrozenFeatureSchema(
    GEN1_UNIVERSE_SCHEMA_VERSION,
    tuple(
        FeatureSpec(
            item.name,
            item.family,
            item.neutral_value,
            True,
            item.recent_window_days,
        )
        for item in A2_SCHEMA.features
        if not item.tier_b_only and item.family in GEN1_SUPPORTED_SOURCE_FAMILIES
    ),
)
if (
    len(_UNIVERSE_DEFINITION.features) != GEN1_UNIVERSE_RAW_FEATURE_COUNT
    or len(_UNIVERSE_DEFINITION.output_names) != GEN1_UNIVERSE_OUTPUT_COUNT
    or _UNIVERSE_DEFINITION.schema_hash != GEN1_UNIVERSE_SCHEMA_HASH
):
    raise RuntimeError("Frozen Gen-1 Tier-A universe drifted from its declared identity")


@dataclass(frozen=True, slots=True)
class Gen1FeatureSchema:
    """Paired-output schema that preserves A2's already-governed missing flags."""

    definition: FrozenFeatureSchema = _UNIVERSE_DEFINITION

    @property
    def version(self) -> str:
        return self.definition.version

    @property
    def features(self) -> tuple[FeatureSpec, ...]:
        return self.definition.features

    @property
    def output_names(self) -> tuple[str, ...]:
        return self.definition.output_names

    @property
    def raw_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.features)

    @property
    def schema_hash(self) -> str:
        return self.definition.schema_hash

    def matrix(self, rows: Sequence[Mapping[str, object]]) -> np.ndarray:
        if not rows:
            raise ValueError("Gen-1 feature matrix cannot be empty")
        result = np.empty((len(rows), len(self.output_names)), dtype=float)
        for row_index, row in enumerate(rows):
            if all(name in row for name in self.output_names):
                for column, name in enumerate(self.output_names):
                    value = _finite_number(row[name], name)
                    if name.endswith("__missing") and value not in (0.0, 1.0):
                        raise ValueError("A2 missing indicators must be exactly zero or one")
                    result[row_index, column] = value
            else:
                result[row_index] = self.definition.extract(row).as_array()
        return result

    def raw_matrix(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        missing_as_nan: bool,
    ) -> np.ndarray:
        if not rows:
            raise ValueError("Gen-1 raw matrix cannot be empty")
        result = np.empty((len(rows), len(self.features)), dtype=float)
        for row_index, row in enumerate(rows):
            for column, spec in enumerate(self.features):
                value = _finite_number(row.get(spec.name), spec.name)
                indicator_name = f"{spec.name}__missing"
                indicator = _finite_number(row.get(indicator_name), indicator_name)
                if indicator not in (0.0, 1.0):
                    raise ValueError("A2 missing indicators must be exactly zero or one")
                result[row_index, column] = np.nan if missing_as_nan and indicator == 1 else value
        return result

    def without(
        self,
        *,
        families: Sequence[str] = (),
        names: Sequence[str] = (),
        version_suffix: str,
    ) -> Gen1FeatureSchema:
        return Gen1FeatureSchema(
            self.definition.without(
                families=families,
                names=names,
                version_suffix=version_suffix,
            )
        )


GEN1_UNIVERSE_SCHEMA = Gen1FeatureSchema()


def analysis_family_for_source(source_family: str) -> str:
    for analysis, sources in GEN1_ANALYSIS_FAMILY_SOURCES.items():
        if source_family in sources:
            return analysis
    raise ValueError(f"Unsupported Gen-1 source family: {source_family}")


def gen1_analysis_mapping() -> dict[str, str]:
    return {item.name: analysis_family_for_source(item.family) for item in GEN1_UNIVERSE_SCHEMA.features}


def _expected_feature_range(name: str) -> dict[str, float | None]:
    """Conservative, non-clipping domain contract for one A2 raw value."""

    if "recent_delta" in name or "workload_trend" in name:
        return {"minimum": None, "maximum": None}
    if any(token in name for token in ("_reliability", "_rate", "_average", "_on_base")):
        return {"minimum": 0.0, "maximum": 1.0}
    if "_slugging" in name:
        return {"minimum": 0.0, "maximum": 4.0}
    if name.endswith("_elo_rating"):
        return {"minimum": 0.0, "maximum": 3_000.0}
    if "travel_miles" in name:
        return {"minimum": 0.0, "maximum": 10_000.0}
    if "hours_since" in name or "days_since" in name:
        return {"minimum": 0.0, "maximum": 1_000.0}
    if any(
        token in name
        for token in (
            "_sample_",
            "_pa_",
            "_bf_",
            "_pitches_",
            "_outs_",
            "_innings_",
            "_games",
            "_count",
            "_consecutive_",
            "doubleheader_game_number",
        )
    ):
        return {"minimum": 0.0, "maximum": 1_000_000.0}
    return {"minimum": 0.0, "maximum": None}


def gen1_feature_contract(schema: Gen1FeatureSchema) -> tuple[dict[str, object], ...]:
    """Freeze the source, temporal, range, and missingness semantics per feature."""

    return tuple(
        {
            "name": spec.name,
            "source_family": spec.family,
            "analysis_family": analysis_family_for_source(spec.family),
            "derivation": "direct_a2_reconstructed_value_without_post_cutoff_transform",
            "expected_range": {
                **_expected_feature_range(spec.name),
                "finite_when_observed": True,
                "enforcement": (
                    "diagnostic_expected_bounds_no_clipping; nonfinite values fail admission"
                ),
            },
            "missingness": {
                "nullable_source": True,
                "neutral_value": float(spec.neutral_value),
                "indicator_name": f"{spec.name}__missing",
                "indicator_semantics": "one_iff_a2_source_value_missing",
                "primary_path": "a2_neutral_value_plus_paired_indicator",
            },
            "source_schema_version": A2_SCHEMA_VERSION,
            "source_schema_hash": A2_SCHEMA_HASH,
            "temporal_rule": (
                "source evidence must satisfy strict A2 T-6h event/evidence/eligibility "
                "rules; no target-game or post-cutoff context"
            ),
            "recent_window_days": spec.recent_window_days,
            "tier": "A",
        }
        for spec in schema.features
    )


def gen1_feature_contract_hash(schema: Gen1FeatureSchema) -> str:
    return _checksum(
        {
            "version": GEN1_FEATURE_CONTRACT_VERSION,
            "features": gen1_feature_contract(schema),
        }
    )


def validate_strict_a2_covariates(row: object) -> None:
    """Revalidate A2 covariates without dereferencing any outcome label."""

    expected_values = {
        "feature_schema_version": A2_SCHEMA_VERSION,
        "source_mode": "LIVE",
        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
        "historical_quality_tier": "A",
        "cutoff_policy_version": "mlb_t_minus_6h_v1",
    }
    for attribute, expected in expected_values.items():
        value = getattr(row, attribute, None)
        if value != expected:
            raise ValueError(f"Gen-1 row {attribute} must equal {expected!r}")
    if any(
        getattr(row, attribute, None) is not None
        for attribute in (
            "market_home_probability",
            "market_timestamp",
            "closing_home_probability",
            "closing_timestamp",
        )
    ) or int(getattr(row, "market_books", 0)) != 0:
        raise ValueError("Gen-1 research rows cannot contain market or closing evidence")
    features = _row_features(row)
    expected_names = frozenset(A2_SCHEMA.output_names)
    actual_names = frozenset(features)
    if len(features) != len(A2_SCHEMA.output_names) or actual_names != expected_names:
        missing_names = sorted(expected_names - actual_names)
        unexpected_names = sorted(actual_names - expected_names)
        raise ValueError(
            "Gen-1 row does not match the exact A2 output-name contract "
            f"(missing={missing_names}, unexpected={unexpected_names})"
        )
    missing = set(str(value) for value in getattr(row, "missing_features", ()))
    for spec in A2_SCHEMA.features:
        raw = _finite_number(features[spec.name], spec.name)
        indicator = _finite_number(features[spec.missing_indicator_name], spec.missing_indicator_name)
        if indicator not in (0.0, 1.0):
            raise ValueError("A2 missing indicators must be exactly zero or one")
        if (spec.name in missing) != (indicator == 1.0):
            raise ValueError("A2 missing feature inventory disagrees with paired indicators")
        if spec.tier_b_only and indicator != 1.0:
            raise ValueError("Tier-B target-context evidence cannot enter Gen-1 research")
        if spec.tier_b_only and raw != float(spec.neutral_value):
            raise ValueError("Missing Tier-B target-context values must retain their frozen neutral")
    _row_event_at(row)


def supported_family_coverage(rows: Sequence[object]) -> dict[str, float]:
    if not rows:
        return {name: 0.0 for name in GEN1_ANALYSIS_FAMILIES}
    result: dict[str, float] = {}
    for analysis, sources in GEN1_ANALYSIS_FAMILY_SOURCES.items():
        specs = [item for item in GEN1_UNIVERSE_SCHEMA.features if item.family in sources]
        observed = sum(
            _finite_number(_row_features(row)[f"{spec.name}__missing"], spec.name) == 0.0
            for row in rows
            for spec in specs
        )
        result[analysis] = observed / (len(rows) * len(specs)) if specs else 0.0
    return result


def _is_recent_window(spec: FeatureSpec) -> bool:
    return spec.recent_window_days in (14, 30)


def _is_bayesian_recent(spec: FeatureSpec) -> bool:
    return _is_recent_window(spec) and any(
        token in spec.name for token in ("_posterior_", "_reliability_", "_recent_delta_")
    )


def _is_recent_sample_count(spec: FeatureSpec) -> bool:
    return _is_recent_window(spec) and any(
        token in spec.name
        for token in ("_sample_", "_batting_pa_", "_pitching_bf_")
    )


@dataclass(frozen=True, slots=True)
class RecentVariantDefinition:
    name: str
    schema: Gen1FeatureSchema
    included_recent_names: tuple[str, ...]
    excluded_recent_names: tuple[str, ...]
    unmatched_bayesian_delta_names: tuple[str, ...]


def recent_variant_definitions(
    universe: Gen1FeatureSchema = GEN1_UNIVERSE_SCHEMA,
) -> tuple[RecentVariantDefinition, ...]:
    recent_specs = tuple(item for item in universe.features if _is_recent_window(item))
    definitions: list[RecentVariantDefinition] = []
    for name in ("none", "raw", "bayesian"):
        excluded: set[str] = set()
        for spec in recent_specs:
            if name == "none":
                excluded.add(spec.name)
            elif name == "raw" and _is_bayesian_recent(spec):
                excluded.add(spec.name)
            elif name == "bayesian" and not (
                _is_bayesian_recent(spec) or _is_recent_sample_count(spec)
            ):
                excluded.add(spec.name)
        schema = universe.without(
            names=tuple(sorted(excluded)),
            version_suffix=f"recent_{name}",
        )
        included = tuple(sorted(spec.name for spec in recent_specs if spec.name not in excluded))
        unmatched = tuple(
            sorted(
                spec.name
                for spec in recent_specs
                if name == "bayesian" and "_recent_delta_" in spec.name
            )
        )
        definitions.append(
            RecentVariantDefinition(
                name=name,
                schema=schema,
                included_recent_names=included,
                excluded_recent_names=tuple(sorted(excluded)),
                unmatched_bayesian_delta_names=unmatched,
            )
        )
    return tuple(definitions)


def gen1_leave_one_family_out(
    schema: Gen1FeatureSchema,
) -> tuple[AblationSpec, ...]:
    return tuple(
        AblationSpec(
            name=f"without_{analysis}",
            excluded_families=sources,
        )
        for analysis, sources in GEN1_ANALYSIS_FAMILY_SOURCES.items()
    )


class A2EloClassifier:
    """Direct replay-Elo baseline; no outcome-based coefficient fitting."""

    def __init__(self) -> None:
        self._index: int | None = None
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(
        self,
        matrix: np.ndarray,
        outcomes: Sequence[int],
        feature_names: Sequence[str],
        *,
        row_ids: Sequence[str] = (),
    ) -> A2EloClassifier:
        values = np.asarray(matrix, dtype=float)
        if values.ndim != 2 or values.shape[0] != len(outcomes):
            raise ValueError("Elo training evidence must align")
        self._index = tuple(feature_names).index("elo_expected_home_probability")
        self.fit_row_ids = tuple(str(value) for value in row_ids)
        return self

    def predict_proba(self, matrix: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
        if self._index is None:
            raise RuntimeError("Elo classifier is not fitted")
        if tuple(feature_names).index("elo_expected_home_probability") != self._index:
            raise ValueError("Elo feature order drifted")
        values = np.asarray(matrix, dtype=float)
        return np.clip(values[:, self._index], 1e-6, 1 - 1e-6)


class A2RunStrengthClassifier:
    """Fixed transparent expected-run baseline using pregame talent estimates."""

    _REQUIRED = (
        "home_offense_true_talent",
        "away_offense_true_talent",
        "home_run_prevention_true_talent",
        "away_run_prevention_true_talent",
    )
    _MIN_EXPECTED_RUNS = 0.05
    _MAX_EXPECTED_RUNS = 20.0
    _MIN_PROBABILITY = 1e-6
    _MAX_PROBABILITY = 1.0 - _MIN_PROBABILITY

    def __init__(self, *, home_multiplier: float = 1.025, run_scale: float = 1.55) -> None:
        if (
            not isfinite(float(home_multiplier))
            or not isfinite(float(run_scale))
            or home_multiplier <= 0.0
            or run_scale <= 0.0
        ):
            raise ValueError("Run-strength multiplier and scale must be finite and positive")
        self.home_multiplier = home_multiplier
        self.run_scale = run_scale
        self._indices: tuple[int, ...] | None = None
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(
        self,
        matrix: np.ndarray,
        outcomes: Sequence[int],
        feature_names: Sequence[str],
        *,
        row_ids: Sequence[str] = (),
    ) -> A2RunStrengthClassifier:
        values = np.asarray(matrix, dtype=float)
        if values.ndim != 2 or values.shape[0] != len(outcomes):
            raise ValueError("Run-strength training evidence must align")
        names = tuple(feature_names)
        self._indices = tuple(names.index(name) for name in self._REQUIRED)
        self.fit_row_ids = tuple(str(value) for value in row_ids)
        return self

    def expected_runs(
        self,
        matrix: np.ndarray,
        feature_names: Sequence[str],
    ) -> tuple[np.ndarray, np.ndarray]:
        if self._indices is None:
            raise RuntimeError("Run-strength classifier is not fitted")
        names = tuple(feature_names)
        if tuple(names.index(name) for name in self._REQUIRED) != self._indices:
            raise ValueError("Run-strength feature order drifted")
        values = np.asarray(matrix, dtype=float)
        home_offense, away_offense, home_prevention, away_prevention = self._indices
        home = np.clip(
            (values[:, home_offense] + values[:, away_prevention])
            * 0.5
            * self.home_multiplier,
            self._MIN_EXPECTED_RUNS,
            self._MAX_EXPECTED_RUNS,
        )
        away = np.clip(
            (values[:, away_offense] + values[:, home_prevention]) * 0.5,
            self._MIN_EXPECTED_RUNS,
            self._MAX_EXPECTED_RUNS,
        )
        return home, away

    def predict_proba(self, matrix: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
        home, away = self.expected_runs(matrix, feature_names)
        log_odds = (home - away) / self.run_scale
        probability = np.empty_like(log_odds, dtype=float)
        nonnegative = log_odds >= 0.0
        inverse = np.exp(-log_odds[nonnegative])
        probability[nonnegative] = 1.0 / (1.0 + inverse)
        exponential = np.exp(log_odds[~nonnegative])
        probability[~nonnegative] = exponential / (1.0 + exponential)
        return np.clip(
            probability,
            self._MIN_PROBABILITY,
            self._MAX_PROBABILITY,
        )


class FoldTrainingMedianImputer:
    """Fold-local median imputation with no indicator columns."""

    def __init__(self, schema: Gen1FeatureSchema) -> None:
        self.schema = schema
        self.medians_: np.ndarray | None = None
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        row_ids: Sequence[str],
    ) -> FoldTrainingMedianImputer:
        if self.medians_ is not None:
            raise RuntimeError("FoldTrainingMedianImputer cannot be refitted")
        matrix = self.schema.raw_matrix(rows, missing_as_nan=True)
        medians = np.nanmedian(matrix, axis=0)
        fallback = np.asarray([item.neutral_value for item in self.schema.features], dtype=float)
        self.medians_ = np.where(np.isnan(medians), fallback, medians)
        self.fit_row_ids = tuple(str(value) for value in row_ids)
        return self

    def transform(self, rows: Sequence[Mapping[str, object]]) -> np.ndarray:
        if self.medians_ is None:
            raise RuntimeError("FoldTrainingMedianImputer is not fitted")
        matrix = self.schema.raw_matrix(rows, missing_as_nan=True)
        missing = np.isnan(matrix)
        return np.where(missing, self.medians_[None, :], matrix)

    @property
    def parameters(self) -> dict[str, float]:
        if self.medians_ is None:
            raise RuntimeError("FoldTrainingMedianImputer is not fitted")
        return {
            name: float(value)
            for name, value in zip(self.schema.raw_names, self.medians_)
        }


class NativeMissingXGBoostClassifier:
    """Restrained XGBoost path that consumes raw NaNs without imputation flags."""

    def __init__(self, candidate: Gen1XGBoostCandidate, *, random_seed: int) -> None:
        self.candidate = candidate
        self.random_seed = random_seed
        self.estimator: object | None = None
        self.fit_row_ids: tuple[str, ...] = ()

    def fit(
        self,
        matrix: np.ndarray,
        outcomes: Sequence[int],
        *,
        row_ids: Sequence[str],
    ) -> NativeMissingXGBoostClassifier:
        from xgboost import XGBClassifier

        values = np.asarray(matrix, dtype=float)
        labels = np.asarray(outcomes, dtype=int)
        if values.ndim != 2 or values.shape[0] != len(labels) or len(np.unique(labels)) < 2:
            raise ValueError("Native-missing XGBoost evidence must align and contain both classes")
        self.estimator = XGBClassifier(
            n_estimators=self.candidate.n_estimators,
            max_depth=self.candidate.max_depth,
            learning_rate=self.candidate.learning_rate,
            min_child_weight=self.candidate.min_child_weight,
            reg_lambda=self.candidate.reg_lambda,
            subsample=0.85,
            colsample_bytree=0.85,
            objective="binary:logistic",
            eval_metric="logloss",
            random_state=self.random_seed,
            n_jobs=1,
            tree_method="hist",
            verbosity=0,
            missing=np.nan,
        )
        self.estimator.fit(values, labels, verbose=False)
        self.fit_row_ids = tuple(str(value) for value in row_ids)
        return self

    def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
        if self.estimator is None:
            raise RuntimeError("Native-missing XGBoost is not fitted")
        return self.estimator.predict_proba(np.asarray(matrix, dtype=float))[:, 1]  # type: ignore[attr-defined]


@dataclass(frozen=True, slots=True)
class BlindHoldoutPlan:
    observations: tuple[UnlabeledGame, ...]
    first_holdout_date: date
    holdout_identity_checksum: str
    development_game_ids: tuple[str, ...]
    holdout_game_ids: tuple[str, ...]
    development_dates: tuple[date, ...]
    holdout_dates: tuple[date, ...]


@dataclass(frozen=True, slots=True)
class Gen1Split:
    development: tuple[LabeledGame, ...]
    holdout: BlindHoldoutPlan


def _blind_observation(row: object) -> UnlabeledGame:
    return UnlabeledGame(
        game_id=str(getattr(row, "game_id")),
        event_at=_row_event_at(row),
        features=_row_features(row),
        lifecycle=str(getattr(row, "lifecycle", "HISTORICAL_A2_T_MINUS_6H")),
        source_mode=str(getattr(row, "source_mode", "LIVE")),
        home_team_id=str(getattr(row, "home_team_id")),
        away_team_id=str(getattr(row, "away_team_id")),
        market_home_probability=None,
        closing_home_probability=None,
    )


def _blind_identity(observations: Sequence[UnlabeledGame]) -> str:
    return _checksum(
        {
            "observations": [
                {
                    "event_at": row.event_at,
                    "feature_checksum": _checksum(
                        {name: row.features[name] for name in A2_SCHEMA.output_names}
                    ),
                    "game_id": row.game_id,
                    "home_team_id": row.home_team_id,
                    "away_team_id": row.away_team_id,
                }
                for row in observations
            ]
        }
    )


def split_gen1_rows(
    rows: Sequence[object],
    policy: Gen1ResearchPolicy,
) -> Gen1Split:
    """Date-group split that never reads final-holdout target attributes."""

    if len(rows) < policy.minimum_rows:
        raise ValueError("Gen-1 research dataset is below its minimum row count")
    ordered = tuple(sorted(rows, key=lambda row: (_row_event_at(row), str(getattr(row, "game_id")))))
    for row in ordered:
        validate_strict_a2_covariates(row)
    game_ids = tuple(str(getattr(row, "game_id")) for row in ordered)
    if len(set(game_ids)) != len(game_ids):
        raise ValueError("Gen-1 research requires one row per game")
    dates = sorted({_row_event_at(row).date() for row in ordered})
    if len(dates) < policy.minimum_event_dates:
        raise ValueError("Gen-1 research dataset lacks required event-date groups")
    holdout_date_count = max(1, ceil(len(dates) * policy.holdout_fraction))
    split_index = len(dates) - holdout_date_count
    if split_index < policy.minimum_development_dates:
        raise ValueError("Gen-1 development window is too short")
    first_holdout_date = dates[split_index]
    development_rows = tuple(
        row for row in ordered if _row_event_at(row).date() < first_holdout_date
    )
    holdout_rows = tuple(
        row for row in ordered if _row_event_at(row).date() >= first_holdout_date
    )
    if len(holdout_rows) < policy.minimum_holdout_rows:
        raise ValueError("Gen-1 blind holdout is below its minimum row count")
    development: list[LabeledGame] = []
    for row in development_rows:
        # Target attributes are dereferenced only on the development side.
        development.append(
            LabeledGame(
                game_id=str(getattr(row, "game_id")),
                event_at=_row_event_at(row),
                features=_row_features(row),
                home_win=int(getattr(row, "home_win")),
                lifecycle=str(getattr(row, "lifecycle", "HISTORICAL_A2_T_MINUS_6H")),
                source_mode=str(getattr(row, "source_mode", "LIVE")),
                home_runs=float(getattr(row, "home_runs")),
                away_runs=float(getattr(row, "away_runs")),
                home_team_id=str(getattr(row, "home_team_id")),
                away_team_id=str(getattr(row, "away_team_id")),
                market_home_probability=None,
                closing_home_probability=None,
            )
        )
    observations = tuple(_blind_observation(row) for row in holdout_rows)
    return Gen1Split(
        development=tuple(development),
        holdout=BlindHoldoutPlan(
            observations=observations,
            first_holdout_date=first_holdout_date,
            holdout_identity_checksum=_blind_identity(observations),
            development_game_ids=tuple(row.game_id for row in development),
            holdout_game_ids=tuple(row.game_id for row in observations),
            development_dates=tuple(dates[:split_index]),
            holdout_dates=tuple(dates[split_index:]),
        ),
    )


@dataclass(frozen=True, slots=True)
class Gen1FoldAudit:
    fold_index: int
    model_name: str
    train_game_ids: tuple[str, ...]
    validation_game_ids: tuple[str, ...]
    trained_through: datetime
    train_started_at: datetime
    validation_started_at: datetime
    validation_ended_at: datetime
    train_rows: int
    validation_rows: int
    train_home_wins: int
    validation_home_wins: int


@dataclass(frozen=True, slots=True)
class Gen1RunOOFRow:
    game_id: str
    event_at: datetime
    fold_index: int
    actual_home_runs: float
    actual_away_runs: float
    predictions: Mapping[str, tuple[float, float]]


@dataclass(frozen=True, slots=True)
class Gen1StackOOFRow:
    game_id: str
    event_at: datetime
    outcome: int
    predictions: Mapping[str, float]
    raw_ensemble_probability: float
    trained_through: datetime
    stack_fold_index: int


@dataclass(frozen=True, slots=True)
class Gen1PredictionBundle:
    game_ids: tuple[str, ...]
    base_probabilities: Mapping[str, tuple[float, ...]]
    raw_selected_probability: tuple[float, ...]
    calibrated_probability: tuple[float, ...]
    run_predictions: Mapping[str, tuple[tuple[float, ...], tuple[float, ...]]]


@dataclass(frozen=True, slots=True)
class EnsembleDecision:
    action: str
    selected_name: str
    best_single_name: str
    raw_ensemble_log_loss: float
    raw_ensemble_brier: float
    best_single_log_loss: float
    best_single_brier: float
    brier_improvement: float
    log_loss_degradation: float
    ece_degradation: float
    stable_fold_fraction: float
    required_brier_improvement: float
    maximum_log_loss_degradation: float
    maximum_ece_degradation: float
    required_stable_fold_fraction: float
    reason: str


@dataclass(frozen=True, slots=True)
class Gen1ResearchDecisionRule:
    version: str
    candidate_label: str
    challenger_label: str
    rejected_label: str
    matched_probability_comparators: tuple[str, ...]
    require_lower_bound_nonnegative_for: tuple[str, ...]
    require_point_improvement_metrics: tuple[str, ...]
    maximum_ece: float
    maximum_abs_calibration_intercept: float
    minimum_calibration_slope: float
    maximum_calibration_slope: float
    require_ood_clear: bool
    require_run_model_non_degradation: bool
    reject_brier_degradation_vs_home_prior: float
    reject_log_loss_degradation_vs_home_prior: float
    reject_maximum_ece: float
    reject_minimum_calibration_slope: float
    reject_maximum_calibration_slope: float
    orm_status_mapping: Mapping[str, str]
    promotion_decision: str = "RESEARCH_ONLY_NO_PROMOTION"


def frozen_research_decision_rule(
    policy: Gen1ResearchPolicy,
) -> Gen1ResearchDecisionRule:
    return Gen1ResearchDecisionRule(
        version="MLB_GEN1_RESEARCH_CLASSIFICATION_RULE_V1",
        candidate_label="RESEARCH_CANDIDATE",
        challenger_label="RESEARCH_CHALLENGER",
        rejected_label="REJECTED_GEN1",
        matched_probability_comparators=("home_prior", "elo", "logistic"),
        require_lower_bound_nonnegative_for=("log_loss", "brier"),
        require_point_improvement_metrics=("log_loss", "brier_score"),
        maximum_ece=policy.research_max_ece,
        maximum_abs_calibration_intercept=policy.research_max_abs_calibration_intercept,
        minimum_calibration_slope=policy.research_min_calibration_slope,
        maximum_calibration_slope=policy.research_max_calibration_slope,
        require_ood_clear=True,
        require_run_model_non_degradation=True,
        reject_brier_degradation_vs_home_prior=policy.research_reject_brier_degradation,
        reject_log_loss_degradation_vs_home_prior=(
            policy.research_reject_log_loss_degradation
        ),
        reject_maximum_ece=policy.research_reject_max_ece,
        reject_minimum_calibration_slope=policy.research_reject_min_calibration_slope,
        reject_maximum_calibration_slope=policy.research_reject_max_calibration_slope,
        orm_status_mapping={
            "RESEARCH_CANDIDATE": "CANDIDATE",
            "RESEARCH_CHALLENGER": "CHALLENGER",
            "REJECTED_GEN1": "DEVELOPMENT",
        },
    )


@dataclass(frozen=True, slots=True)
class Gen1FittedBundle:
    schema: Gen1FeatureSchema
    classifiers: Mapping[str, object]
    poisson_model: PoissonRunRegressor
    negative_binomial_model: NegativeBinomialRunRegressor | None
    team_model: EmpiricalBayesTeamClassifier
    stacker: NonnegativeStacker
    ensemble_decision: EnsembleDecision
    calibration: CalibrationSelection
    ood_detector: OODDetector
    selected_xgboost_candidate: Gen1XGBoostCandidate | None
    training_only_imputation_parameters: Mapping[str, float]
    team_fit_sample_counts: Mapping[str, int]

    def predict(self, observations: Sequence[UnlabeledGame]) -> Gen1PredictionBundle:
        if not observations:
            raise ValueError("Gen-1 prediction requires observations")
        matrix = self.schema.matrix([row.features for row in observations])
        names = self.schema.output_names
        probabilities = {
            name: np.asarray(model.predict_proba(matrix, names), dtype=float)  # type: ignore[attr-defined]
            for name, model in self.classifiers.items()
        }
        poisson_home, poisson_away = self.poisson_model.predict_runs(matrix, names)
        probabilities["poisson"] = self.poisson_model.predict_proba(matrix, names)
        nb_home: np.ndarray | None = None
        nb_away: np.ndarray | None = None
        if self.negative_binomial_model is not None:
            nb_home, nb_away = self.negative_binomial_model.predict_runs(matrix, names)
            probabilities["negative_binomial"] = (
                self.negative_binomial_model.predict_proba(matrix, names)
            )
        probabilities["team_eb"] = self.team_model.predict_proba(
            [str(row.home_team_id) for row in observations],
            [str(row.away_team_id) for row in observations],
        )
        run_strength = self.classifiers.get("run_strength")
        if not isinstance(run_strength, A2RunStrengthClassifier):
            raise RuntimeError("Gen-1 fitted bundle lost its run-strength baseline")
        simple_home, simple_away = run_strength.expected_runs(matrix, names)
        if self.ensemble_decision.action == "KEEP_ENSEMBLE":
            raw = self.stacker.predict(probabilities)
        else:
            raw = probabilities[self.ensemble_decision.best_single_name]
        calibrated = self.calibration.calibrator.transform(raw)  # type: ignore[attr-defined]
        return Gen1PredictionBundle(
            game_ids=tuple(row.game_id for row in observations),
            base_probabilities={
                name: tuple(float(value) for value in values)
                for name, values in sorted(probabilities.items())
            },
            raw_selected_probability=tuple(float(value) for value in raw),
            calibrated_probability=tuple(float(value) for value in calibrated),
            run_predictions={
                "run_strength": (
                    tuple(float(value) for value in simple_home),
                    tuple(float(value) for value in simple_away),
                ),
                "poisson": (
                    tuple(float(value) for value in poisson_home),
                    tuple(float(value) for value in poisson_away),
                ),
                **(
                    {
                        "negative_binomial": (
                            tuple(float(value) for value in nb_home),
                            tuple(float(value) for value in nb_away),
                        )
                    }
                    if nb_home is not None and nb_away is not None
                    else {}
                ),
            },
        )

    def explainability_summary(self, *, maximum_features: int = 30) -> dict[str, object]:
        result: dict[str, object] = {
            "basis": "development_fit_only",
            "ensemble": asdict(self.ensemble_decision),
            "ensemble_weights": self.stacker.fit_.weight_map if self.stacker.fit_ else {},
            "unsupported": [],
        }
        logistic = self.classifiers.get("logistic")
        if isinstance(logistic, RegularizedLogisticClassifier):
            coefficients = np.asarray(logistic.estimator.coef_[0], dtype=float)
            means = np.asarray(logistic.preprocessor._means, dtype=float)
            scales = np.asarray(logistic.preprocessor._scales, dtype=float)
            order = np.argsort(np.abs(coefficients))[::-1][:maximum_features]
            result["logistic_contract"] = {
                "penalty": logistic.estimator.penalty,
                "solver": logistic.estimator.solver,
                "regularization_c": float(logistic.estimator.C),
                "intercept": float(logistic.estimator.intercept_[0]),
                "coefficient_count": len(coefficients),
                "coefficient_checksum": _checksum(coefficients.tolist()),
                "all_coefficients": {
                    name: float(coefficients[index])
                    for index, name in enumerate(self.schema.output_names)
                },
                "preprocessing_basis": "development_fit_mean_scale_no_holdout",
                "preprocessing_fit_rows": logistic.preprocessor.audit.fit_row_count,
                "preprocessing_means_checksum": _checksum(means.tolist()),
                "preprocessing_scales_checksum": _checksum(scales.tolist()),
                "preprocessing_means": {
                    name: float(means[index])
                    for index, name in enumerate(self.schema.output_names)
                },
                "preprocessing_scales": {
                    name: float(scales[index])
                    for index, name in enumerate(self.schema.output_names)
                },
                "training_only_median_comparator_parameters": dict(
                    self.training_only_imputation_parameters
                ),
            }
            result["logistic_standardized_coefficients"] = [
                {
                    "feature": self.schema.output_names[index],
                    "coefficient": float(coefficients[index]),
                }
                for index in order
            ]
        xgboost = self.classifiers.get("xgboost")
        if isinstance(xgboost, LimitedXGBoostClassifier) and xgboost.estimator is not None:
            importance = np.asarray(xgboost.estimator.feature_importances_, dtype=float)  # type: ignore[attr-defined]
            order = np.argsort(importance)[::-1][:maximum_features]
            result["xgboost_gain_proxy"] = [
                {"feature": self.schema.output_names[index], "importance": float(importance[index])}
                for index in order
                if importance[index] > 0
            ]
        else:
            result["unsupported"] = [
                "xgboost feature importance unavailable because XGBoost was not eligible"
            ]
        result["empirical_bayes_team_probabilities"] = dict(
            sorted(self.team_model.team_probabilities_.items())
        )
        result["empirical_bayes_contract"] = {
            "prior_strength": self.team_model.prior_strength,
            "home_prior": self.team_model.home_prior,
            "fit_team_count": len(self.team_model.team_probabilities_),
            "fit_team_game_counts": dict(sorted(self.team_fit_sample_counts.items())),
            "posterior_probability_checksum": _checksum(
                dict(sorted(self.team_model.team_probabilities_.items()))
            ),
            "posterior_probabilities": dict(
                sorted(self.team_model.team_probabilities_.items())
            ),
        }
        result["run_distribution_contract"] = {
            "poisson": "separate_regularized_log_mean_models",
            "negative_binomial": (
                "shared_poisson_conditional_means_plus_training_residual_nb2_dispersion"
            ),
            "negative_binomial_is_independent_mean_glm": False,
            "negative_binomial_retained": self.negative_binomial_model is not None,
            "negative_binomial_dispersion": (
                self.negative_binomial_model.dispersion_diagnostic
                if self.negative_binomial_model is not None
                else None
            ),
        }
        return result

    def prediction_driver_contract(self) -> dict[str, object]:
        """Describe only the local-driver capability of the frozen selection."""

        selected_model = self.ensemble_decision.selected_name
        common: dict[str, object] = {
            "selected_model": selected_model,
            "holdout_covariates_only": True,
            "holdout_labels_used": False,
            "baseline_probability_available": True,
            "final_probability_available": True,
        }
        if selected_model == "run_strength":
            return {
                **common,
                "selected_model_driver_status": "SUPPORTED_SELECTED_MODEL",
                "method": GEN1_RUN_STRENGTH_DRIVER_METHOD,
                "baseline_final_top_positive_top_negative": True,
                "exact_selected_raw_attribution": True,
                "exact_final_reconciliation_with_explicit_calibration_adjustment": True,
                "supplementary_component_method": None,
                "unavailable_reason": None,
            }
        if selected_model == "logistic":
            return {
                **common,
                "selected_model_driver_status": "SUPPORTED_SELECTED_MODEL",
                "method": (
                    "regularized_logistic_component_standardized_linear_contribution"
                ),
                "baseline_final_top_positive_top_negative": True,
                "exact_selected_raw_attribution": True,
                "exact_final_reconciliation_with_explicit_calibration_adjustment": True,
                "supplementary_component_method": None,
                "unavailable_reason": None,
            }
        if selected_model == "nonnegative_stack":
            return {
                **common,
                "selected_model_driver_status": "UNAVAILABLE",
                "method": None,
                "baseline_final_top_positive_top_negative": False,
                "exact_selected_raw_attribution": False,
                "exact_final_reconciliation_with_explicit_calibration_adjustment": False,
                "supplementary_component_method": (
                    "regularized_logistic_component_standardized_linear_contribution"
                ),
                "unavailable_reason": (
                    "nonnegative_stack_has_no_governed_exact_local_driver_method"
                ),
            }
        return {
            **common,
            "selected_model_driver_status": "UNAVAILABLE",
            "method": None,
            "baseline_final_top_positive_top_negative": False,
            "exact_selected_raw_attribution": False,
            "exact_final_reconciliation_with_explicit_calibration_adjustment": False,
            "supplementary_component_method": None,
            "unavailable_reason": "selected_model_has_no_governed_local_driver_method",
        }

    def summarize_prediction_drivers(
        self,
        drivers: Mapping[str, Mapping[str, object]],
    ) -> dict[str, object]:
        status_counts: dict[str, int] = defaultdict(int)
        method_counts: dict[str, int] = defaultdict(int)
        reason_counts: dict[str, int] = defaultdict(int)
        exact_reconciliation_count = 0
        for driver in drivers.values():
            status_counts[str(driver.get("status", "MISSING_STATUS"))] += 1
            if driver.get("method") is not None:
                method_counts[str(driver["method"])] += 1
            if driver.get("reason") is not None:
                reason_counts[str(driver["reason"])] += 1
            reconciliation = driver.get("reconciliation")
            if (
                isinstance(reconciliation, Mapping)
                and reconciliation.get("passed") is True
            ):
                exact_reconciliation_count += 1
        return {
            **self.prediction_driver_contract(),
            "prediction_count": len(drivers),
            "record_status_counts": dict(sorted(status_counts.items())),
            "record_method_counts": dict(sorted(method_counts.items())),
            "record_reason_counts": dict(sorted(reason_counts.items())),
            "records_with_nonempty_top_positive": sum(
                bool(driver.get("top_positive")) for driver in drivers.values()
            ),
            "records_with_nonempty_top_negative": sum(
                bool(driver.get("top_negative")) for driver in drivers.values()
            ),
            "records_with_exact_reconciliation": exact_reconciliation_count,
        }

    def require_prediction_driver_freeze_ready(
        self,
        drivers: Mapping[str, Mapping[str, object]],
    ) -> dict[str, object]:
        contract = self.summarize_prediction_drivers(drivers)
        driver_count = len(drivers)
        selected_driver_supported = (
            contract["selected_model_driver_status"]
            == "SUPPORTED_SELECTED_MODEL"
        )
        all_records_supported = contract["record_status_counts"] == {
            "SUPPORTED_SELECTED_MODEL": driver_count
        }
        selected_method = contract.get("method")
        all_methods_match = isinstance(selected_method, str) and contract[
            "record_method_counts"
        ] == {selected_method: driver_count}
        all_positive_present = (
            contract["records_with_nonempty_top_positive"] == driver_count
        )
        all_negative_present = (
            contract["records_with_nonempty_top_negative"] == driver_count
        )
        exact_reconciliation_required = bool(
            contract.get("exact_selected_raw_attribution")
            or contract.get(
                "exact_final_reconciliation_with_explicit_calibration_adjustment"
            )
        )
        exact_reconciliation_satisfied = (
            not exact_reconciliation_required
            or contract["records_with_exact_reconciliation"] == driver_count
        )
        gate = {
            "passed": (
                driver_count > 0
                and selected_driver_supported
                and all_records_supported
                and all_methods_match
                and all_positive_present
                and all_negative_present
                and exact_reconciliation_satisfied
            ),
            "selected_driver_supported": selected_driver_supported,
            "all_records_supported": all_records_supported,
            "all_methods_match": all_methods_match,
            "all_records_have_top_positive": all_positive_present,
            "all_records_have_top_negative": all_negative_present,
            "exact_reconciliation_required": exact_reconciliation_required,
            "exact_reconciliation_satisfied": exact_reconciliation_satisfied,
            "records": driver_count,
        }
        contract["freeze_gate"] = gate
        if gate["passed"] is not True:
            raise RuntimeError(
                "Gen-1 selected-model local-driver evidence failed the pre-artifact "
                f"freeze gate: {gate}"
            )
        return contract

    def _run_strength_prediction_drivers(
        self,
        observation: UnlabeledGame,
        prediction: Gen1PredictionBundle,
        prediction_index: int,
        *,
        maximum_each_direction: int,
    ) -> dict[str, object]:
        run_strength = self.classifiers.get("run_strength")
        if not isinstance(run_strength, A2RunStrengthClassifier):
            return {
                "status": "UNAVAILABLE",
                "selected_model": "run_strength",
                "reason": "run_strength_component_missing",
                "baseline_probability": 0.5,
                "final_probability": prediction.calibrated_probability[prediction_index],
                "top_positive": [],
                "top_negative": [],
            }
        try:
            expected_home = float(
                prediction.run_predictions["run_strength"][0][prediction_index]
            )
            expected_away = float(
                prediction.run_predictions["run_strength"][1][prediction_index]
            )
            raw_probability = float(
                prediction.base_probabilities["run_strength"][prediction_index]
            )
        except (KeyError, IndexError) as exc:
            raise ValueError("Frozen run-strength prediction evidence is incomplete") from exc
        home_prior_comparator_probability = float(
            prediction.base_probabilities["home_prior"][prediction_index]
        )
        baseline_probability = 0.5
        frozen_raw_selected_probability = float(
            prediction.raw_selected_probability[prediction_index]
        )
        final_probability = float(prediction.calibrated_probability[prediction_index])
        tolerance = 1e-12
        feature_values = {
            name: _finite_number(observation.features.get(name), name)
            for name in A2RunStrengthClassifier._REQUIRED
        }
        feature_missing = {
            name: _finite_number(
                observation.features.get(f"{name}__missing"),
                f"{name}__missing",
            )
            for name in A2RunStrengthClassifier._REQUIRED
        }
        if any(value not in (0.0, 1.0) for value in feature_missing.values()):
            raise ValueError("Run-strength driver missing indicators must be binary")
        unclipped_home = (
            feature_values["home_offense_true_talent"]
            + feature_values["away_run_prevention_true_talent"]
        ) * 0.5 * run_strength.home_multiplier
        unclipped_away = (
            feature_values["away_offense_true_talent"]
            + feature_values["home_run_prevention_true_talent"]
        ) * 0.5
        recomputed_home = min(
            max(unclipped_home, run_strength._MIN_EXPECTED_RUNS),
            run_strength._MAX_EXPECTED_RUNS,
        )
        recomputed_away = min(
            max(unclipped_away, run_strength._MIN_EXPECTED_RUNS),
            run_strength._MAX_EXPECTED_RUNS,
        )
        recomputed_model_log_odds = (
            recomputed_home - recomputed_away
        ) / run_strength.run_scale
        recomputed_raw_probability = min(
            max(
                _probability_from_log_odds(recomputed_model_log_odds),
                run_strength._MIN_PROBABILITY,
            ),
            run_strength._MAX_PROBABILITY,
        )
        matrix = self.schema.matrix((observation.features,))
        api_home, api_away = run_strength.expected_runs(
            matrix, self.schema.output_names
        )
        api_raw_probability = float(
            run_strength.predict_proba(matrix, self.schema.output_names)[0]
        )
        recalibrated_probability = float(
            self.calibration.calibrator.transform((raw_probability,))[0]  # type: ignore[attr-defined]
        )
        if (
            abs(expected_home - recomputed_home) > tolerance
            or abs(expected_away - recomputed_away) > tolerance
            or abs(float(api_home[0]) - recomputed_home) > tolerance
            or abs(float(api_away[0]) - recomputed_away) > tolerance
            or abs(api_raw_probability - recomputed_raw_probability) > tolerance
            or abs(raw_probability - recomputed_raw_probability) > tolerance
            or abs(raw_probability - frozen_raw_selected_probability) > tolerance
            or abs(final_probability - recalibrated_probability) > tolerance
        ):
            raise ValueError(
                "Frozen selected run-strength prediction does not reconcile with its "
                "governed model and calibrator"
            )
        coefficients = {
            "home_offense_true_talent": (
                0.5 * run_strength.home_multiplier / run_strength.run_scale
            ),
            "away_run_prevention_true_talent": (
                0.5 * run_strength.home_multiplier / run_strength.run_scale
            ),
            "away_offense_true_talent": -0.5 / run_strength.run_scale,
            "home_run_prevention_true_talent": -0.5 / run_strength.run_scale,
        }
        feature_terms = [
            {
                "name": name,
                "term_type": "GOVERNED_FEATURE",
                "feature": name,
                "feature_value": feature_values[name],
                "source_missing": feature_missing[name] == 1.0,
                "value_semantics": (
                    "FROZEN_NEUTRAL_FOR_MISSING_SOURCE"
                    if feature_missing[name] == 1.0
                    else "OBSERVED_RECONSTRUCTED_VALUE"
                ),
                "log_odds_coefficient": coefficients[name],
                "log_odds_contribution": feature_values[name] * coefficients[name],
            }
            for name in A2RunStrengthClassifier._REQUIRED
        ]
        model_log_odds = recomputed_model_log_odds
        raw_log_odds = _probability_log_odds(
            raw_probability, "run_strength raw probability"
        )
        raw_adjustment_terms = [
            {
                "name": "home_expected_runs_clip_adjustment",
                "term_type": "GOVERNED_RUN_CLIP_ADJUSTMENT",
                "log_odds_contribution": (
                    (recomputed_home - unclipped_home) / run_strength.run_scale
                ),
            },
            {
                "name": "away_expected_runs_clip_adjustment",
                "term_type": "GOVERNED_RUN_CLIP_ADJUSTMENT",
                "log_odds_contribution": -(
                    (recomputed_away - unclipped_away) / run_strength.run_scale
                ),
            },
            {
                "name": "probability_clip_adjustment",
                "term_type": "GOVERNED_PROBABILITY_CLIP_ADJUSTMENT",
                "log_odds_contribution": raw_log_odds - model_log_odds,
            },
        ]
        raw_log_odds_terms = feature_terms + raw_adjustment_terms
        raw_terms_sum = float(
            sum(
                float(item["log_odds_contribution"])
                for item in raw_log_odds_terms
            )
        )
        reconstructed_raw_probability = _probability_from_log_odds(raw_terms_sum)
        raw_error = abs(raw_terms_sum - raw_log_odds)
        raw_probability_error = abs(reconstructed_raw_probability - raw_probability)
        probability_bridge_terms = [
            {
                "name": "selected_run_strength_delta_from_zero_component_origin",
                "term_type": "SELECTED_MODEL_PROBABILITY_DELTA",
                "probability_contribution": raw_probability - baseline_probability,
            },
            {
                "name": "frozen_calibration_delta",
                "term_type": "FROZEN_CALIBRATION_PROBABILITY_DELTA",
                "calibration_method": self.calibration.method,
                "probability_contribution": final_probability - raw_probability,
            },
        ]
        reconstructed_final_probability = baseline_probability + sum(
            float(item["probability_contribution"])
            for item in probability_bridge_terms
        )
        final_probability_error = abs(
            reconstructed_final_probability - final_probability
        )
        positive = sorted(
            (
                item
                for item in feature_terms
                if float(item["log_odds_contribution"]) > 0.0
            ),
            key=lambda item: (
                -float(item["log_odds_contribution"]),
                str(item["name"]),
            ),
        )[:maximum_each_direction]
        negative = sorted(
            (
                item
                for item in feature_terms
                if float(item["log_odds_contribution"]) < 0.0
            ),
            key=lambda item: (
                float(item["log_odds_contribution"]),
                str(item["name"]),
            ),
        )[:maximum_each_direction]
        return {
            "status": "SUPPORTED_SELECTED_MODEL",
            "selected_model": "run_strength",
            "method": GEN1_RUN_STRENGTH_DRIVER_METHOD,
            "scope": (
                "exact_selected_run_strength_raw_log_odds_with_explicit_frozen_"
                "calibration_reconciliation"
            ),
            "baseline_probability": baseline_probability,
            "baseline_semantics": "zero_component_algebraic_origin_not_evidence",
            "home_prior_comparator_probability": home_prior_comparator_probability,
            "raw_selected_probability": raw_probability,
            "raw_selected_log_odds": raw_log_odds,
            "final_probability": final_probability,
            "expected_home_runs": expected_home,
            "expected_away_runs": expected_away,
            "unclipped_home_runs": unclipped_home,
            "unclipped_away_runs": unclipped_away,
            "home_multiplier": run_strength.home_multiplier,
            "run_scale": run_strength.run_scale,
            "expected_run_bounds": {
                "minimum": run_strength._MIN_EXPECTED_RUNS,
                "maximum": run_strength._MAX_EXPECTED_RUNS,
            },
            "probability_bounds": {
                "minimum": run_strength._MIN_PROBABILITY,
                "maximum": run_strength._MAX_PROBABILITY,
            },
            "raw_log_odds_terms": raw_log_odds_terms,
            "raw_log_odds_terms_checksum": _checksum(raw_log_odds_terms),
            "probability_bridge_terms": probability_bridge_terms,
            "probability_bridge_terms_checksum": _checksum(
                probability_bridge_terms
            ),
            "top_positive": positive,
            "top_negative": negative,
            "reconciliation": {
                "raw_terms_log_odds_sum": raw_terms_sum,
                "raw_log_odds_absolute_error": raw_error,
                "reconstructed_raw_probability": reconstructed_raw_probability,
                "raw_probability_absolute_error": raw_probability_error,
                "recomputed_expected_home_runs": recomputed_home,
                "recomputed_expected_away_runs": recomputed_away,
                "recomputed_model_raw_probability": recomputed_raw_probability,
                "model_api_raw_probability": api_raw_probability,
                "frozen_raw_selected_probability": frozen_raw_selected_probability,
                "recalibrated_probability": recalibrated_probability,
                "probability_bridge_sum": reconstructed_final_probability,
                "reconstructed_final_probability": reconstructed_final_probability,
                "final_probability_absolute_error": final_probability_error,
                "absolute_tolerance": tolerance,
                "passed": (
                    raw_error <= tolerance
                    and raw_probability_error <= tolerance
                    and final_probability_error <= tolerance
                ),
            },
        }

    def prediction_drivers(
        self,
        observation: UnlabeledGame,
        *,
        maximum_each_direction: int = 5,
        frozen_predictions: Gen1PredictionBundle | None = None,
        frozen_prediction_index: int = 0,
    ) -> dict[str, object]:
        """Serializable truthful local drivers for the frozen selected model."""

        prediction = frozen_predictions or self.predict((observation,))
        prediction_index = frozen_prediction_index if frozen_predictions is not None else 0
        if prediction.game_ids[prediction_index] != observation.game_id:
            raise ValueError("Frozen prediction and driver observation identities differ")
        selected_model = self.ensemble_decision.selected_name
        if selected_model == "run_strength":
            return self._run_strength_prediction_drivers(
                observation,
                prediction,
                prediction_index,
                maximum_each_direction=maximum_each_direction,
            )
        if selected_model not in {"nonnegative_stack", "logistic"}:
            return {
                "status": "UNAVAILABLE",
                "selected_model": selected_model,
                "reason": "selected_model_has_no_governed_local_driver_method",
                "baseline_probability": prediction.base_probabilities.get(
                    "home_prior", (None,)
                )[prediction_index],
                "final_probability": prediction.calibrated_probability[prediction_index],
                "top_positive": [],
                "top_negative": [],
            }
        logistic = self.classifiers.get("logistic")
        if not isinstance(logistic, RegularizedLogisticClassifier):
            return {
                "status": "UNAVAILABLE",
                "selected_model": selected_model,
                "reason": "regularized_logistic_component_missing",
                "baseline_probability": prediction.base_probabilities.get(
                    "home_prior", (None,)
                )[prediction_index],
                "final_probability": prediction.calibrated_probability[prediction_index],
                "top_positive": [],
                "top_negative": [],
            }
        matrix = self.schema.matrix((observation.features,))
        transformed = logistic.preprocessor.transform(matrix, self.schema.output_names)[0]
        coefficients = np.asarray(logistic.estimator.coef_[0], dtype=float)
        contributions = transformed * coefficients
        ordered_positive = np.argsort(contributions)[::-1]
        ordered_negative = np.argsort(contributions)
        positive = [
            {
                "feature": self.schema.output_names[index],
                "standardized_linear_contribution": float(contributions[index]),
            }
            for index in ordered_positive
            if contributions[index] > 0
        ][:maximum_each_direction]
        negative = [
            {
                "feature": self.schema.output_names[index],
                "standardized_linear_contribution": float(contributions[index]),
            }
            for index in ordered_negative
            if contributions[index] < 0
        ][:maximum_each_direction]
        if selected_model == "logistic":
            tolerance = 1e-12
            baseline_probability = float(
                prediction.base_probabilities["home_prior"][prediction_index]
            )
            logistic_probability = float(
                prediction.base_probabilities["logistic"][prediction_index]
            )
            frozen_raw_selected_probability = float(
                prediction.raw_selected_probability[prediction_index]
            )
            final_probability = float(
                prediction.calibrated_probability[prediction_index]
            )
            recomputed_logistic_probability = float(
                logistic.predict_proba(matrix, self.schema.output_names)[0]
            )
            recalibrated_probability = float(
                self.calibration.calibrator.transform((logistic_probability,))[0]  # type: ignore[attr-defined]
            )
            intercept = float(logistic.estimator.intercept_[0])
            linear_log_odds_sum = intercept + float(contributions.sum())
            logistic_log_odds = _probability_log_odds(
                logistic_probability, "logistic raw probability"
            )
            reconstructed_logistic_probability = _probability_from_log_odds(
                linear_log_odds_sum
            )
            probability_bridge_terms = [
                {
                    "name": "selected_logistic_delta_from_home_prior",
                    "term_type": "SELECTED_MODEL_PROBABILITY_DELTA",
                    "probability_contribution": (
                        logistic_probability - baseline_probability
                    ),
                },
                {
                    "name": "frozen_calibration_delta",
                    "term_type": "FROZEN_CALIBRATION_PROBABILITY_DELTA",
                    "calibration_method": self.calibration.method,
                    "probability_contribution": (
                        final_probability - logistic_probability
                    ),
                },
            ]
            reconstructed_final_probability = baseline_probability + sum(
                float(item["probability_contribution"])
                for item in probability_bridge_terms
            )
            reconciliation = {
                "linear_log_odds_sum": linear_log_odds_sum,
                "logistic_log_odds_absolute_error": abs(
                    linear_log_odds_sum - logistic_log_odds
                ),
                "reconstructed_raw_probability": (
                    reconstructed_logistic_probability
                ),
                "raw_probability_absolute_error": abs(
                    reconstructed_logistic_probability - logistic_probability
                ),
                "recomputed_model_raw_probability": (
                    recomputed_logistic_probability
                ),
                "frozen_raw_selected_probability": (
                    frozen_raw_selected_probability
                ),
                "recalibrated_probability": recalibrated_probability,
                "probability_bridge_sum": reconstructed_final_probability,
                "reconstructed_final_probability": (
                    reconstructed_final_probability
                ),
                "final_probability_absolute_error": abs(
                    reconstructed_final_probability - final_probability
                ),
                "absolute_tolerance": tolerance,
            }
            reconciliation["passed"] = (
                reconciliation["logistic_log_odds_absolute_error"] <= tolerance
                and reconciliation["raw_probability_absolute_error"] <= tolerance
                and abs(logistic_probability - recomputed_logistic_probability)
                <= tolerance
                and abs(
                    logistic_probability - frozen_raw_selected_probability
                )
                <= tolerance
                and abs(final_probability - recalibrated_probability) <= tolerance
                and reconciliation["final_probability_absolute_error"] <= tolerance
            )
            if reconciliation["passed"] is not True:
                raise ValueError(
                    "Frozen selected logistic prediction does not reconcile with its "
                    "governed model and calibrator"
                )
            all_contributions = [float(value) for value in contributions]
            return {
                "status": "SUPPORTED_SELECTED_MODEL",
                "selected_model": selected_model,
                "method": (
                    "regularized_logistic_component_standardized_linear_contribution"
                ),
                "scope": "exact_selected_raw_logistic_with_frozen_calibration_bridge",
                "baseline_probability": baseline_probability,
                "baseline_semantics": "frozen_home_prior_comparator",
                "raw_selected_probability": logistic_probability,
                "final_probability": final_probability,
                "intercept": intercept,
                "full_linear_contribution_count": len(all_contributions),
                "full_linear_contribution_checksum": _checksum(
                    all_contributions
                ),
                "probability_bridge_terms": probability_bridge_terms,
                "probability_bridge_terms_checksum": _checksum(
                    probability_bridge_terms
                ),
                "top_positive": positive,
                "top_negative": negative,
                "reconciliation": reconciliation,
            }
        return {
            "status": "SUPPLEMENTARY_ENSEMBLE_COMPONENT",
            "selected_model": selected_model,
            "method": "regularized_logistic_component_standardized_linear_contribution",
            "scope": (
                "supplementary_component_not_selected_ensemble_attribution"
            ),
            "baseline_probability": prediction.base_probabilities["home_prior"][
                prediction_index
            ],
            "logistic_probability": prediction.base_probabilities["logistic"][
                prediction_index
            ],
            "final_probability": prediction.calibrated_probability[prediction_index],
            "intercept": float(logistic.estimator.intercept_[0]),
            "top_positive": positive,
            "top_negative": negative,
        }


@dataclass(frozen=True, slots=True)
class Gen1DevelopmentResult:
    status: str
    policy: Gen1ResearchPolicy
    source_dataset_rows_checksum: str
    selected_schema: Gen1FeatureSchema
    recent_variant: RecentVariantDefinition
    split: BlindHoldoutPlan
    folds: tuple[Gen1FoldAudit, ...]
    base_oof_metrics: Mapping[str, ClassificationMetrics]
    base_oof_secondary_metrics: Mapping[str, Mapping[str, object]]
    ensemble_oof_metrics: ClassificationMetrics
    ensemble_oof_secondary_metrics: Mapping[str, object]
    selected_oof_metrics: ClassificationMetrics
    selected_oof_secondary_metrics: Mapping[str, object]
    development_run_metrics: Mapping[str, Mapping[str, object]]
    negative_binomial_retention: Mapping[str, object]
    calibration: Mapping[str, object]
    ensemble_decision: EnsembleDecision
    research_decision_rule: Gen1ResearchDecisionRule
    paired_bootstrap: Mapping[str, Mapping[str, PairedBootstrapDelta]]
    recent_variant_evidence: Mapping[str, Mapping[str, object]]
    xgboost_candidate_evidence: Mapping[str, Mapping[str, object]]
    missingness_comparison: Mapping[str, Mapping[str, object]]
    ablation_metrics: Mapping[str, Mapping[str, object]]
    subgroup_metrics: Mapping[str, Mapping[str, object]]
    probability_bands: tuple[Mapping[str, object], ...]
    ood_report: OODReport
    covariate_ood: Mapping[str, object]
    explainability: Mapping[str, object]
    holdout_driver_summaries: Mapping[str, Mapping[str, object]]
    holdout_predictions: Gen1PredictionBundle
    fitted_bundle: Gen1FittedBundle
    candidate_identity_checksum: str
    provider_calls: int = 0
    holdout_labels_accessed: bool = False

    def metadata(self) -> dict[str, object]:
        return {
            "status": self.status,
            "policy": asdict(self.policy),
            "source_dataset_rows_checksum": self.source_dataset_rows_checksum,
            "source_a2_schema": A2_SCHEMA_VERSION,
            "source_a2_schema_hash": A2_SCHEMA_HASH,
            "universe_schema": GEN1_UNIVERSE_SCHEMA_VERSION,
            "universe_schema_hash": GEN1_UNIVERSE_SCHEMA_HASH,
            "universe_raw_features": GEN1_UNIVERSE_RAW_FEATURE_COUNT,
            "universe_outputs": GEN1_UNIVERSE_OUTPUT_COUNT,
            "selected_schema": self.selected_schema.version,
            "selected_schema_hash": self.selected_schema.schema_hash,
            "feature_contract_version": GEN1_FEATURE_CONTRACT_VERSION,
            "feature_contract_hash": gen1_feature_contract_hash(self.selected_schema),
            "feature_contract": list(gen1_feature_contract(self.selected_schema)),
            "selected_output_names": list(self.selected_schema.output_names),
            "analysis_family_mapping": dict(GEN1_ANALYSIS_FAMILY_SOURCES),
            "recent_variant": {
                "name": self.recent_variant.name,
                "included": list(self.recent_variant.included_recent_names),
                "excluded": list(self.recent_variant.excluded_recent_names),
                "unmatched_bayesian_delta_terms": list(
                    self.recent_variant.unmatched_bayesian_delta_names
                ),
            },
            "split": {
                "first_holdout_date": self.split.first_holdout_date,
                "development_game_count": len(self.split.development_game_ids),
                "holdout_game_count": len(self.split.holdout_game_ids),
                "development_dates": [value.isoformat() for value in self.split.development_dates],
                "holdout_dates": [value.isoformat() for value in self.split.holdout_dates],
                "holdout_identity_checksum": self.split.holdout_identity_checksum,
                "holdout_labels_accessed": False,
            },
            "folds": [asdict(value) for value in self.folds],
            "base_oof_metrics": {
                name: asdict(value) for name, value in self.base_oof_metrics.items()
            },
            "base_oof_secondary_metrics": {
                name: dict(value)
                for name, value in self.base_oof_secondary_metrics.items()
            },
            "ensemble_oof_metrics": asdict(self.ensemble_oof_metrics),
            "ensemble_oof_secondary_metrics": dict(
                self.ensemble_oof_secondary_metrics
            ),
            "selected_oof_metrics": asdict(self.selected_oof_metrics),
            "selected_oof_secondary_metrics": dict(
                self.selected_oof_secondary_metrics
            ),
            "development_run_metrics": {
                name: dict(value) for name, value in self.development_run_metrics.items()
            },
            "negative_binomial_retention": dict(
                self.negative_binomial_retention
            ),
            "calibration": dict(self.calibration),
            "ensemble_decision": asdict(self.ensemble_decision),
            "research_decision_rule": asdict(self.research_decision_rule),
            "paired_bootstrap": {
                name: {metric: asdict(value) for metric, value in metrics.items()}
                for name, metrics in self.paired_bootstrap.items()
            },
            "recent_variant_evidence": {
                name: dict(value) for name, value in self.recent_variant_evidence.items()
            },
            "xgboost_candidate_evidence": {
                name: dict(value) for name, value in self.xgboost_candidate_evidence.items()
            },
            "missingness_comparison": {
                name: dict(value) for name, value in self.missingness_comparison.items()
            },
            "ablations": {name: dict(value) for name, value in self.ablation_metrics.items()},
            "subgroups": {name: dict(value) for name, value in self.subgroup_metrics.items()},
            "probability_bands": list(self.probability_bands),
            "ood": asdict(self.ood_report),
            "covariate_ood": dict(self.covariate_ood),
            "explainability": dict(self.explainability),
            "holdout_driver_summary_checksum": _checksum(self.holdout_driver_summaries),
            "holdout_driver_count": len(self.holdout_driver_summaries),
            "holdout_prediction_checksum": _checksum(asdict(self.holdout_predictions)),
            "candidate_identity_checksum": self.candidate_identity_checksum,
            "provider_calls": 0,
            "promotion": "NOT_EVALUATED_RESEARCH_ONLY",
        }


@dataclass(frozen=True, slots=True)
class Gen1HoldoutEvaluation:
    status: str
    candidate_identity_checksum: str
    holdout_identity_checksum: str
    observations: int
    candidate_metrics: ClassificationMetrics
    candidate_secondary_metrics: Mapping[str, object]
    comparison_metrics: Mapping[str, ClassificationMetrics]
    comparison_secondary_metrics: Mapping[str, Mapping[str, object]]
    run_metrics: Mapping[str, Mapping[str, float | int]]
    paired_bootstrap: Mapping[str, Mapping[str, PairedBootstrapDelta]]
    candidate_metric_intervals: Mapping[str, Mapping[str, float | int]]
    subgroup_metrics: Mapping[str, Mapping[str, object]]
    probability_bands: tuple[Mapping[str, object], ...]
    prediction_rows: tuple[Mapping[str, object], ...]
    evaluation_identity_checksum: str
    research_decision: str
    research_decision_checks: Mapping[str, bool]
    provider_calls: int = 0
    promotion_decision: str = "RESEARCH_ONLY_NO_PROMOTION"

    def metadata(self) -> dict[str, object]:
        return {
            "status": self.status,
            "candidate_identity_checksum": self.candidate_identity_checksum,
            "holdout_identity_checksum": self.holdout_identity_checksum,
            "observations": self.observations,
            "candidate_metrics": asdict(self.candidate_metrics),
            "candidate_secondary_metrics": dict(self.candidate_secondary_metrics),
            "comparison_metrics": {
                name: asdict(value) for name, value in self.comparison_metrics.items()
            },
            "comparison_secondary_metrics": {
                name: dict(value)
                for name, value in self.comparison_secondary_metrics.items()
            },
            "run_metrics": {name: dict(value) for name, value in self.run_metrics.items()},
            "paired_bootstrap": {
                name: {metric: asdict(value) for metric, value in metrics.items()}
                for name, metrics in self.paired_bootstrap.items()
            },
            "candidate_metric_intervals": {
                name: dict(value)
                for name, value in self.candidate_metric_intervals.items()
            },
            "subgroups": {name: dict(value) for name, value in self.subgroup_metrics.items()},
            "probability_bands": list(self.probability_bands),
            "prediction_row_count": len(self.prediction_rows),
            "prediction_rows_checksum": _checksum(self.prediction_rows),
            "evaluation_identity_checksum": self.evaluation_identity_checksum,
            "research_decision": self.research_decision,
            "research_decision_checks": dict(self.research_decision_checks),
            "provider_calls": 0,
            "promotion_decision": self.promotion_decision,
        }


@dataclass(frozen=True, slots=True)
class _BaseOOFResult:
    rows: tuple[OOFPredictionRow, ...]
    run_rows: tuple[Gen1RunOOFRow, ...]
    negative_binomial_diagnostics: tuple[Mapping[str, object], ...]
    audits: tuple[Gen1FoldAudit, ...]


def _classifier_factories(
    policy: Gen1ResearchPolicy,
    selected_xgboost: Gen1XGBoostCandidate | None,
) -> dict[str, Callable[[], object]]:
    factories: dict[str, Callable[[], object]] = {
        "home_prior": lambda: HomePriorClassifier(
            prior_probability=policy.home_prior_probability,
            prior_strength=policy.home_prior_strength,
        ),
        "elo": A2EloClassifier,
        "run_strength": A2RunStrengthClassifier,
        "logistic": lambda: RegularizedLogisticClassifier(
            regularization_c=policy.logistic_c,
            random_seed=policy.random_seed,
        ),
    }
    if selected_xgboost is not None:
        factories["xgboost"] = lambda: LimitedXGBoostClassifier(
            n_estimators=selected_xgboost.n_estimators,
            max_depth=selected_xgboost.max_depth,
            learning_rate=selected_xgboost.learning_rate,
            min_child_weight=selected_xgboost.min_child_weight,
            reg_lambda=selected_xgboost.reg_lambda,
            random_seed=policy.random_seed,
        )
    return factories


def _generate_base_oof(
    folds: Sequence[RollingOriginFold],
    schema: Gen1FeatureSchema,
    policy: Gen1ResearchPolicy,
    *,
    selected_xgboost: Gen1XGBoostCandidate | None,
) -> _BaseOOFResult:
    factories = _classifier_factories(policy, selected_xgboost)
    rows: list[OOFPredictionRow] = []
    run_rows: list[Gen1RunOOFRow] = []
    negative_binomial_diagnostics: list[Mapping[str, object]] = []
    audits: list[Gen1FoldAudit] = []
    for fold in folds:
        train_features = [row.features for row in fold.train]
        validation_features = [row.features for row in fold.validation]
        train_matrix = schema.matrix(train_features)
        validation_matrix = schema.matrix(validation_features)
        outcomes = [row.home_win for row in fold.train]
        train_ids = tuple(row.game_id for row in fold.train)
        validation_ids = tuple(row.game_id for row in fold.validation)
        predictions: dict[str, np.ndarray] = {}
        run_predictions: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for name, factory in factories.items():
            model = factory()
            model.fit(
                train_matrix,
                outcomes,
                schema.output_names,
                row_ids=train_ids,
            )
            predictions[name] = np.asarray(
                model.predict_proba(validation_matrix, schema.output_names),
                dtype=float,
            )
            if name == "run_strength":
                if not isinstance(model, A2RunStrengthClassifier):
                    raise RuntimeError("Gen-1 run-strength factory drifted")
                run_predictions[name] = model.expected_runs(
                    validation_matrix,
                    schema.output_names,
                )
            audits.append(
                Gen1FoldAudit(
                    fold_index=fold.index,
                    model_name=name,
                    train_game_ids=train_ids,
                    validation_game_ids=validation_ids,
                    trained_through=fold.trained_through,
                    train_started_at=min(row.event_at for row in fold.train),
                    validation_started_at=min(row.event_at for row in fold.validation),
                    validation_ended_at=max(row.event_at for row in fold.validation),
                    train_rows=len(fold.train),
                    validation_rows=len(fold.validation),
                    train_home_wins=sum(row.home_win for row in fold.train),
                    validation_home_wins=sum(row.home_win for row in fold.validation),
                )
            )
        poisson = PoissonRunRegressor(alpha=policy.poisson_alpha).fit(
            train_matrix,
            [float(row.home_runs) for row in fold.train],
            [float(row.away_runs) for row in fold.train],
            schema.output_names,
            row_ids=train_ids,
        )
        predictions["poisson"] = poisson.predict_proba(validation_matrix, schema.output_names)
        run_predictions["poisson"] = poisson.predict_runs(
            validation_matrix,
            schema.output_names,
        )
        negative_binomial = NegativeBinomialRunRegressor(
            mean_alpha=policy.poisson_alpha
        ).fit(
            train_matrix,
            [float(row.home_runs) for row in fold.train],
            [float(row.away_runs) for row in fold.train],
            schema.output_names,
            row_ids=train_ids,
        )
        negative_binomial_diagnostics.append(
            {
                "fold_index": fold.index,
                **negative_binomial.dispersion_diagnostic,
            }
        )
        predictions["negative_binomial"] = negative_binomial.predict_proba(
            validation_matrix,
            schema.output_names,
        )
        run_predictions["negative_binomial"] = negative_binomial.predict_runs(
            validation_matrix,
            schema.output_names,
        )
        team = EmpiricalBayesTeamClassifier(
            prior_strength=policy.team_prior_strength,
            home_prior=policy.home_prior_probability,
        ).fit(
            [str(row.home_team_id) for row in fold.train],
            [str(row.away_team_id) for row in fold.train],
            outcomes,
        )
        predictions["team_eb"] = team.predict_proba(
            [str(row.home_team_id) for row in fold.validation],
            [str(row.away_team_id) for row in fold.validation],
        )
        for name in ("poisson", "negative_binomial", "team_eb"):
            audits.append(
                Gen1FoldAudit(
                    fold_index=fold.index,
                    model_name=name,
                    train_game_ids=train_ids,
                    validation_game_ids=validation_ids,
                    trained_through=fold.trained_through,
                    train_started_at=min(row.event_at for row in fold.train),
                    validation_started_at=min(row.event_at for row in fold.validation),
                    validation_ended_at=max(row.event_at for row in fold.validation),
                    train_rows=len(fold.train),
                    validation_rows=len(fold.validation),
                    train_home_wins=sum(row.home_win for row in fold.train),
                    validation_home_wins=sum(row.home_win for row in fold.validation),
                )
            )
        for index, row in enumerate(fold.validation):
            rows.append(
                OOFPredictionRow(
                    game_id=row.game_id,
                    event_at=row.event_at,
                    trained_through=fold.trained_through,
                    outcome=row.home_win,
                    predictions={
                        name: float(values[index]) for name, values in sorted(predictions.items())
                    },
                )
            )
            run_rows.append(
                Gen1RunOOFRow(
                    game_id=row.game_id,
                    event_at=row.event_at,
                    fold_index=fold.index,
                    actual_home_runs=float(row.home_runs),
                    actual_away_runs=float(row.away_runs),
                    predictions={
                        name: (float(values[0][index]), float(values[1][index]))
                        for name, values in sorted(run_predictions.items())
                    },
                )
            )
    return _BaseOOFResult(
        tuple(rows),
        tuple(run_rows),
        tuple(negative_binomial_diagnostics),
        tuple(audits),
    )


def _logistic_oof(
    folds: Sequence[RollingOriginFold],
    schema: Gen1FeatureSchema,
    policy: Gen1ResearchPolicy,
) -> tuple[tuple[str, datetime, int, float], ...]:
    result: list[tuple[str, datetime, int, float]] = []
    for fold in folds:
        train = schema.matrix([row.features for row in fold.train])
        validation = schema.matrix([row.features for row in fold.validation])
        model = RegularizedLogisticClassifier(
            regularization_c=policy.logistic_c,
            random_seed=policy.random_seed,
        ).fit(
            train,
            [row.home_win for row in fold.train],
            schema.output_names,
            row_ids=[row.game_id for row in fold.train],
        )
        predicted = model.predict_proba(validation, schema.output_names)
        result.extend(
            (row.game_id, row.event_at, row.home_win, float(predicted[index]))
            for index, row in enumerate(fold.validation)
        )
    return tuple(result)


def _secondary_classification_metrics(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
) -> dict[str, object]:
    values = np.asarray(probabilities, dtype=float)
    labels = np.asarray(outcomes, dtype=int)
    if len(values) != len(labels) or not len(values):
        raise ValueError("Secondary metrics require aligned observations")
    accuracy = float(np.mean((values >= 0.5).astype(int) == labels))
    if len(np.unique(labels)) < 2:
        return {
            "accuracy": accuracy,
            "roc_auc": None,
            "roc_auc_status": "UNAVAILABLE_ONE_CLASS",
            "classification_threshold": 0.5,
        }
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), dtype=float)
    cursor = 0
    while cursor < len(values):
        end = cursor + 1
        while end < len(values) and values[order[end]] == values[order[cursor]]:
            end += 1
        ranks[order[cursor:end]] = (cursor + 1 + end) / 2.0
        cursor = end
    positives = labels == 1
    positive_count = int(positives.sum())
    negative_count = len(labels) - positive_count
    auc = (
        float(ranks[positives].sum())
        - positive_count * (positive_count + 1) / 2.0
    ) / (positive_count * negative_count)
    return {
        "accuracy": accuracy,
        "roc_auc": float(auc),
        "roc_auc_status": "SUPPORTED",
        "classification_threshold": 0.5,
    }


def _probability_evidence(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
) -> dict[str, object]:
    result: dict[str, object] = {
        "observations": len(probabilities),
        "brier_score": brier_score(probabilities, outcomes),
        "log_loss": probability_log_loss(probabilities, outcomes),
        "expected_calibration_error": expected_calibration_error(
            probabilities,
            outcomes,
            bins=min(10, len(probabilities)),
        ),
        **_secondary_classification_metrics(probabilities, outcomes),
    }
    if len(set(int(value) for value in outcomes)) < 2:
        result.update(
            {
                "calibration_intercept": None,
                "calibration_slope": None,
                "calibration_status": "UNAVAILABLE_ONE_CLASS",
            }
        )
    else:
        primary = classification_metrics(probabilities, outcomes)
        result.update(
            {
                "probability_rmse": primary.probability_rmse,
                "calibration_intercept": primary.calibration_intercept,
                "calibration_slope": primary.calibration_slope,
                "calibration_status": "SUPPORTED",
            }
        )
    return result


def _select_recent_variant(
    folds: Sequence[RollingOriginFold],
    policy: Gen1ResearchPolicy,
) -> tuple[RecentVariantDefinition, dict[str, dict[str, object]]]:
    evidence: dict[str, dict[str, object]] = {}
    by_name = {item.name: item for item in recent_variant_definitions()}
    fold_by_game = {
        row.game_id: fold.index
        for fold in folds
        for row in fold.validation
    }
    fold_metrics_by_variant: dict[str, dict[int, dict[str, object]]] = {}
    for name, definition in by_name.items():
        rows = _logistic_oof(folds, definition.schema, policy)
        probabilities = [row[3] for row in rows]
        outcomes = [row[2] for row in rows]
        metrics = _probability_evidence(probabilities, outcomes)
        by_fold: dict[int, dict[str, object]] = {}
        for fold_index in sorted({fold_by_game[row[0]] for row in rows}):
            selected = [row for row in rows if fold_by_game[row[0]] == fold_index]
            by_fold[fold_index] = _probability_evidence(
                [row[3] for row in selected],
                [row[2] for row in selected],
            )
        fold_metrics_by_variant[name] = by_fold
        evidence[name] = {
            **metrics,
            "schema_version": definition.schema.version,
            "schema_hash": definition.schema.schema_hash,
            "raw_feature_count": len(definition.schema.features),
            "output_count": len(definition.schema.output_names),
            "included_recent_names": list(definition.included_recent_names),
            "excluded_recent_names": list(definition.excluded_recent_names),
            "unmatched_bayesian_delta_names": list(
                definition.unmatched_bayesian_delta_names
            ),
            "selection_basis": "identical_chronological_development_oof_log_loss_then_brier",
            "fold_metrics": {str(index): value for index, value in by_fold.items()},
        }
    selected_name = min(
        evidence,
        key=lambda name: (
            float(evidence[name]["log_loss"]),
            float(evidence[name]["brier_score"]),
            ("none", "raw", "bayesian").index(name),
        ),
    )
    for name in evidence:
        evidence[name]["selected"] = name == selected_name
        baseline = evidence["none"]
        selected = evidence[selected_name]
        evidence[name]["deltas_vs_none"] = {
            "log_loss": float(evidence[name]["log_loss"]) - float(baseline["log_loss"]),
            "brier_score": (
                float(evidence[name]["brier_score"]) - float(baseline["brier_score"])
            ),
            "expected_calibration_error": (
                float(evidence[name]["expected_calibration_error"])
                - float(baseline["expected_calibration_error"])
            ),
        }
        evidence[name]["deltas_vs_selected"] = {
            "log_loss": float(evidence[name]["log_loss"]) - float(selected["log_loss"]),
            "brier_score": (
                float(evidence[name]["brier_score"]) - float(selected["brier_score"])
            ),
            "expected_calibration_error": (
                float(evidence[name]["expected_calibration_error"])
                - float(selected["expected_calibration_error"])
            ),
        }
        comparable_folds = sorted(fold_metrics_by_variant[name])
        no_worse = sum(
            float(fold_metrics_by_variant[name][index]["log_loss"])
            <= float(fold_metrics_by_variant["none"][index]["log_loss"])
            and float(fold_metrics_by_variant[name][index]["brier_score"])
            <= float(fold_metrics_by_variant["none"][index]["brier_score"])
            for index in comparable_folds
        )
        evidence[name]["fold_consistency_vs_none"] = {
            "no_worse_on_brier_and_log_loss_folds": no_worse,
            "folds": len(comparable_folds),
            "fraction": no_worse / len(comparable_folds),
        }
    return by_name[selected_name], evidence


def _xgboost_candidate_oof(
    folds: Sequence[RollingOriginFold],
    schema: Gen1FeatureSchema,
    policy: Gen1ResearchPolicy,
    candidate: Gen1XGBoostCandidate,
) -> tuple[tuple[str, datetime, int, float], ...]:
    result: list[tuple[str, datetime, int, float]] = []
    for fold in folds:
        train = schema.matrix([row.features for row in fold.train])
        validation = schema.matrix([row.features for row in fold.validation])
        model = LimitedXGBoostClassifier(
            n_estimators=candidate.n_estimators,
            max_depth=candidate.max_depth,
            learning_rate=candidate.learning_rate,
            min_child_weight=candidate.min_child_weight,
            reg_lambda=candidate.reg_lambda,
            random_seed=policy.random_seed,
        ).fit(
            train,
            [row.home_win for row in fold.train],
            schema.output_names,
            row_ids=[row.game_id for row in fold.train],
        )
        predicted = model.predict_proba(validation, schema.output_names)
        result.extend(
            (row.game_id, row.event_at, row.home_win, float(predicted[index]))
            for index, row in enumerate(fold.validation)
        )
    return tuple(result)


def _select_xgboost_candidate(
    development: Sequence[LabeledGame],
    folds: Sequence[RollingOriginFold],
    schema: Gen1FeatureSchema,
    policy: Gen1ResearchPolicy,
    *,
    enabled: bool,
) -> tuple[Gen1XGBoostCandidate | None, dict[str, dict[str, object]]]:
    if not enabled or len(development) < policy.xgboost_minimum_rows:
        return None, {
            "status": {
                "eligible": False,
                "reason": "development_rows_below_xgboost_minimum_or_explicitly_disabled",
                "observed_rows": len(development),
                "minimum_rows": policy.xgboost_minimum_rows,
            }
        }
    evidence: dict[str, dict[str, object]] = {}
    for candidate in policy.xgboost_candidates:
        rows = _xgboost_candidate_oof(folds, schema, policy, candidate)
        metrics = classification_metrics([row[3] for row in rows], [row[2] for row in rows])
        evidence[candidate.name] = {
            **asdict(metrics),
            "parameters": asdict(candidate),
            "selection_basis": "identical_chronological_development_oof_log_loss_then_brier",
        }
    selected_name = min(
        evidence,
        key=lambda name: (
            float(evidence[name]["log_loss"]),
            float(evidence[name]["brier_score"]),
            name,
        ),
    )
    for name in evidence:
        evidence[name]["selected"] = name == selected_name
    return next(item for item in policy.xgboost_candidates if item.name == selected_name), evidence


def _negative_binomial_retention_decision(
    base: _BaseOOFResult,
    policy: Gen1ResearchPolicy,
) -> tuple[bool, dict[str, object]]:
    outcomes = [row.outcome for row in base.rows]
    poisson = classification_metrics(
        [row.predictions["poisson"] for row in base.rows],
        outcomes,
    )
    negative_binomial = classification_metrics(
        [row.predictions["negative_binomial"] for row in base.rows],
        outcomes,
    )
    brier_improvement = poisson.brier_score - negative_binomial.brier_score
    log_loss_improvement = poisson.log_loss - negative_binomial.log_loss
    material_overdispersion_folds = sum(
        bool(value["material_overdispersion"])
        for value in base.negative_binomial_diagnostics
    )
    retain = material_overdispersion_folds > 0 and (
        (
            brier_improvement >= policy.nb_min_brier_improvement
            and log_loss_improvement >= 0.0
        )
        or (
            log_loss_improvement >= policy.nb_min_log_loss_improvement
            and brier_improvement >= 0.0
        )
    )
    return retain, {
        "action": "RETAIN" if retain else "DROP",
        "selection_use": "development_oof_only",
        "poisson_probability_metrics": asdict(poisson),
        "negative_binomial_probability_metrics": asdict(negative_binomial),
        "brier_improvement": brier_improvement,
        "log_loss_improvement": log_loss_improvement,
        "required_brier_improvement": policy.nb_min_brier_improvement,
        "required_log_loss_improvement": policy.nb_min_log_loss_improvement,
        "material_overdispersion_folds": material_overdispersion_folds,
        "folds": len(base.negative_binomial_diagnostics),
        "fold_dispersion_diagnostics": list(base.negative_binomial_diagnostics),
        "distribution_contract": (
            "shared_poisson_means_plus_training_residual_nb2_dispersion"
        ),
        "independent_negative_binomial_mean_fit": False,
        "holdout_labels_used": False,
    }


def _base_rows_for_retention(
    rows: Sequence[OOFPredictionRow],
    *,
    retain_negative_binomial: bool,
) -> tuple[OOFPredictionRow, ...]:
    if retain_negative_binomial:
        return tuple(rows)
    return tuple(
        OOFPredictionRow(
            game_id=row.game_id,
            event_at=row.event_at,
            outcome=row.outcome,
            predictions={
                name: value
                for name, value in row.predictions.items()
                if name != "negative_binomial"
            },
            trained_through=row.trained_through,
        )
        for row in rows
    )


def _cross_fit_ensemble(
    rows: Sequence[OOFPredictionRow],
    policy: Gen1ResearchPolicy,
) -> tuple[tuple[Gen1StackOOFRow, ...], NonnegativeStacker]:
    grouped: dict[datetime, list[OOFPredictionRow]] = defaultdict(list)
    for row in sorted(rows, key=lambda item: (item.trained_through, item.event_at, item.game_id)):
        grouped[row.trained_through].append(row)
    prior: list[OOFPredictionRow] = []
    output: list[Gen1StackOOFRow] = []
    stack_fold_index = 0
    for group in grouped.values():
        current = sorted(group, key=lambda item: (item.event_at, item.game_id))
        if len(prior) >= 4 and len({item.outcome for item in prior}) == 2:
            stacker = NonnegativeStacker(l2_toward_uniform=policy.stack_l2).fit(prior)
            columns = {
                name: [item.predictions[name] for item in current]
                for name in sorted(current[0].predictions)
            }
            predicted = stacker.predict(columns)
            trained_through = max(item.event_at for item in prior)
            output.extend(
                Gen1StackOOFRow(
                    game_id=row.game_id,
                    event_at=row.event_at,
                    outcome=row.outcome,
                    predictions=dict(row.predictions),
                    raw_ensemble_probability=float(predicted[index]),
                    trained_through=trained_through,
                    stack_fold_index=stack_fold_index,
                )
                for index, row in enumerate(current)
            )
            stack_fold_index += 1
        prior.extend(current)
    if not output:
        raise ValueError("Gen-1 cannot form a cross-fitted ensemble validation series")
    final = NonnegativeStacker(l2_toward_uniform=policy.stack_l2).fit(rows)
    return tuple(output), final


def _ensemble_decision(
    rows: Sequence[Gen1StackOOFRow],
    policy: Gen1ResearchPolicy,
) -> tuple[EnsembleDecision, dict[str, ClassificationMetrics], ClassificationMetrics]:
    outcomes = [row.outcome for row in rows]
    model_names = tuple(sorted(rows[0].predictions))
    base = {
        name: classification_metrics([row.predictions[name] for row in rows], outcomes)
        for name in model_names
    }
    ensemble = classification_metrics(
        [row.raw_ensemble_probability for row in rows],
        outcomes,
    )
    best_name = min(
        base,
        key=lambda name: (base[name].log_loss, base[name].brier_score, name),
    )
    best = base[best_name]
    brier_improvement = best.brier_score - ensemble.brier_score
    log_loss_degradation = ensemble.log_loss - best.log_loss
    ece_degradation = (
        ensemble.expected_calibration_error - best.expected_calibration_error
    )
    fold_groups: dict[int, list[Gen1StackOOFRow]] = defaultdict(list)
    for row in rows:
        fold_groups[row.stack_fold_index].append(row)
    stable_folds = 0
    for group in fold_groups.values():
        group_outcomes = [row.outcome for row in group]
        ensemble_brier = brier_score(
            [row.raw_ensemble_probability for row in group],
            group_outcomes,
        )
        single_brier = brier_score(
            [row.predictions[best_name] for row in group],
            group_outcomes,
        )
        ensemble_log_loss = probability_log_loss(
            [row.raw_ensemble_probability for row in group],
            group_outcomes,
        )
        single_log_loss = probability_log_loss(
            [row.predictions[best_name] for row in group],
            group_outcomes,
        )
        if (
            ensemble_brier
            <= single_brier + policy.ensemble_max_brier_degradation
            and ensemble_log_loss
            <= single_log_loss + policy.ensemble_max_log_loss_degradation
        ):
            stable_folds += 1
    stable_fold_fraction = stable_folds / len(fold_groups)
    checks = {
        "material_brier_improvement": (
            brier_improvement >= policy.ensemble_min_brier_improvement
        ),
        "no_material_log_loss_regression": (
            log_loss_degradation <= policy.ensemble_max_log_loss_degradation
        ),
        "calibration_not_degraded": (
            ece_degradation <= policy.ensemble_max_ece_degradation
        ),
        "reasonable_fold_stability": (
            stable_fold_fraction >= policy.ensemble_min_stable_fold_fraction
        ),
    }
    keep = all(checks.values())
    failed = [name for name, passed in checks.items() if not passed]
    return (
        EnsembleDecision(
            action="KEEP_ENSEMBLE" if keep else "DROP_TO_BEST_SINGLE",
            selected_name="nonnegative_stack" if keep else best_name,
            best_single_name=best_name,
            raw_ensemble_log_loss=ensemble.log_loss,
            raw_ensemble_brier=ensemble.brier_score,
            best_single_log_loss=best.log_loss,
            best_single_brier=best.brier_score,
            brier_improvement=brier_improvement,
            log_loss_degradation=log_loss_degradation,
            ece_degradation=ece_degradation,
            stable_fold_fraction=stable_fold_fraction,
            required_brier_improvement=policy.ensemble_min_brier_improvement,
            maximum_log_loss_degradation=policy.ensemble_max_log_loss_degradation,
            maximum_ece_degradation=policy.ensemble_max_ece_degradation,
            required_stable_fold_fraction=policy.ensemble_min_stable_fold_fraction,
            reason=(
                "ensemble_met_predeclared_material_quality_calibration_stability_rule"
                if keep
                else "ensemble_failed:" + ",".join(failed)
            ),
        ),
        base,
        ensemble,
    )


def _selected_oof_probabilities(
    rows: Sequence[Gen1StackOOFRow],
    decision: EnsembleDecision,
) -> np.ndarray:
    if decision.action == "KEEP_ENSEMBLE":
        return np.asarray([row.raw_ensemble_probability for row in rows], dtype=float)
    return np.asarray(
        [row.predictions[decision.best_single_name] for row in rows],
        dtype=float,
    )


def _paired_bootstrap(
    rows: Sequence[Gen1StackOOFRow],
    selected: Sequence[float],
    policy: Gen1ResearchPolicy,
) -> dict[str, dict[str, PairedBootstrapDelta]]:
    outcomes = [row.outcome for row in rows]
    groups = [row.event_at.date() for row in rows]
    result: dict[str, dict[str, PairedBootstrapDelta]] = {}
    for name in sorted(rows[0].predictions):
        comparator = [row.predictions[name] for row in rows]
        result[name] = {
            "log_loss": paired_block_bootstrap_delta(
                comparator,
                selected,
                outcomes,
                groups,
                metric=probability_log_loss,
                resamples=policy.bootstrap_resamples,
                random_seed=policy.random_seed,
            ),
            "brier": paired_block_bootstrap_delta(
                comparator,
                selected,
                outcomes,
                groups,
                metric=brier_score,
                resamples=policy.bootstrap_resamples,
                random_seed=policy.random_seed + 1,
            ),
        }
    return result


def _calibration_candidate_evidence(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    event_at: Sequence[datetime],
    policy: Gen1ResearchPolicy,
    selection: CalibrationSelection,
) -> dict[str, dict[str, object]]:
    values = np.asarray(probabilities, dtype=float)
    labels = np.asarray(outcomes, dtype=int)
    order = np.asarray(
        sorted(range(len(event_at)), key=lambda index: (event_at[index], index)),
        dtype=int,
    )
    ordered_dates = [event_at[index].date() for index in order]
    unique_dates = tuple(dict.fromkeys(ordered_dates))
    selection_date_count = max(
        1,
        ceil(len(unique_dates) * policy.calibration_selection_fraction),
    )
    selection_start = unique_dates[-selection_date_count]
    fit_indices = np.asarray(
        [index for index in order if event_at[index].date() < selection_start],
        dtype=int,
    )
    score_indices = np.asarray(
        [index for index in order if event_at[index].date() >= selection_start],
        dtype=int,
    )
    factories: Mapping[str, Callable[[], object]] = {
        "identity": IdentityCalibrator,
        "platt": PlattCalibrator,
        "isotonic": IsotonicCalibrator,
    }
    result: dict[str, dict[str, object]] = {}
    for method, factory in factories.items():
        if method == "isotonic" and len(fit_indices) < policy.minimum_isotonic_samples:
            result[method] = {
                "status": "INELIGIBLE",
                "reason": "calibration_fit_rows_below_minimum_isotonic_samples",
                "fit_rows": len(fit_indices),
                "minimum_rows": policy.minimum_isotonic_samples,
                "selected": False,
            }
            continue
        calibrator = factory()
        calibrator.fit(values[fit_indices], labels[fit_indices])  # type: ignore[attr-defined]
        predicted = calibrator.transform(values[score_indices])  # type: ignore[attr-defined]
        result[method] = {
            "status": "EVALUATED_DEVELOPMENT_OOF_TEMPORAL_SELECTION",
            **_probability_evidence(
                predicted.tolist(),
                labels[score_indices].astype(int).tolist(),
            ),
            "fit_rows": len(fit_indices),
            "selection_rows": len(score_indices),
            "selection_started_at": min(event_at[index] for index in score_indices),
            "selected": method == selection.method,
            "holdout_labels_used": False,
        }
    return result


def _missingness_comparison(
    folds: Sequence[RollingOriginFold],
    schema: Gen1FeatureSchema,
    policy: Gen1ResearchPolicy,
    selected_xgboost: Gen1XGBoostCandidate | None,
) -> dict[str, dict[str, object]]:
    paired_rows = _logistic_oof(folds, schema, policy)
    result: dict[str, dict[str, object]] = {
        "paired_a2_neutral_plus_indicator": {
            **asdict(
                classification_metrics(
                    [row[3] for row in paired_rows],
                    [row[2] for row in paired_rows],
                )
            ),
            "basis": "frozen_a2_neutral_constants_with_paired_missing_indicators",
            "output_count": len(schema.output_names),
        }
    }
    median_predictions: list[float] = []
    median_outcomes: list[int] = []
    fold_parameter_checksums: list[str] = []
    for fold in folds:
        imputer = FoldTrainingMedianImputer(schema).fit(
            [row.features for row in fold.train],
            row_ids=[row.game_id for row in fold.train],
        )
        train = imputer.transform([row.features for row in fold.train])
        validation = imputer.transform([row.features for row in fold.validation])
        model = RegularizedLogisticClassifier(
            regularization_c=policy.logistic_c,
            random_seed=policy.random_seed,
        ).fit(
            train,
            [row.home_win for row in fold.train],
            schema.raw_names,
            row_ids=[row.game_id for row in fold.train],
        )
        median_predictions.extend(model.predict_proba(validation, schema.raw_names).tolist())
        median_outcomes.extend(row.home_win for row in fold.validation)
        fold_parameter_checksums.append(_checksum(imputer.parameters))
    result["training_only_neutral"] = {
        **asdict(classification_metrics(median_predictions, median_outcomes)),
        "basis": "fold_training_median_imputation_indicators_dropped",
        "raw_feature_count": len(schema.raw_names),
        "fold_parameter_checksums": fold_parameter_checksums,
        "validation_or_holdout_used_for_imputation": False,
    }
    if selected_xgboost is None:
        result["xgboost_native_missing"] = {
            "status": "UNSUPPORTED",
            "reason": "xgboost_not_eligible_or_explicitly_disabled",
        }
    else:
        native_predictions: list[float] = []
        native_outcomes: list[int] = []
        for fold in folds:
            train = schema.raw_matrix([row.features for row in fold.train], missing_as_nan=True)
            validation = schema.raw_matrix(
                [row.features for row in fold.validation],
                missing_as_nan=True,
            )
            model = NativeMissingXGBoostClassifier(
                selected_xgboost,
                random_seed=policy.random_seed,
            ).fit(
                train,
                [row.home_win for row in fold.train],
                row_ids=[row.game_id for row in fold.train],
            )
            native_predictions.extend(model.predict_proba(validation).tolist())
            native_outcomes.extend(row.home_win for row in fold.validation)
        result["xgboost_native_missing"] = {
            **asdict(classification_metrics(native_predictions, native_outcomes)),
            "basis": "raw_nan_xgboost_native_missing_no_indicators",
            "raw_feature_count": len(schema.raw_names),
            "candidate": selected_xgboost.name,
        }
    return result


def _oof_group_permutation_importance(
    folds: Sequence[RollingOriginFold],
    schema: Gen1FeatureSchema,
    policy: Gen1ResearchPolicy,
    selected_xgboost: Gen1XGBoostCandidate | None,
) -> dict[str, object]:
    """Bounded seven-group permutation importance on untouched fold validation."""

    names = schema.output_names
    name_index = {name: index for index, name in enumerate(names)}
    columns = {
        analysis: tuple(
            column
            for spec in schema.features
            if spec.family in sources
            for column in (
                name_index[spec.name],
                name_index[f"{spec.name}__missing"],
            )
        )
        for analysis, sources in GEN1_ANALYSIS_FAMILY_SOURCES.items()
    }
    baseline_probabilities: list[float] = []
    permuted_probabilities: dict[str, list[float]] = {
        name: [] for name in GEN1_ANALYSIS_FAMILIES
    }
    outcomes: list[int] = []
    for fold in folds:
        train = schema.matrix([row.features for row in fold.train])
        validation = schema.matrix([row.features for row in fold.validation])
        if selected_xgboost is not None:
            model = LimitedXGBoostClassifier(
                n_estimators=selected_xgboost.n_estimators,
                max_depth=selected_xgboost.max_depth,
                learning_rate=selected_xgboost.learning_rate,
                min_child_weight=selected_xgboost.min_child_weight,
                reg_lambda=selected_xgboost.reg_lambda,
                random_seed=policy.random_seed,
            ).fit(
                train,
                [row.home_win for row in fold.train],
                names,
                row_ids=[row.game_id for row in fold.train],
            )
        else:
            model = RegularizedLogisticClassifier(
                regularization_c=policy.logistic_c,
                random_seed=policy.random_seed,
            ).fit(
                train,
                [row.home_win for row in fold.train],
                names,
                row_ids=[row.game_id for row in fold.train],
            )
        baseline_probabilities.extend(model.predict_proba(validation, names).tolist())
        outcomes.extend(row.home_win for row in fold.validation)
        for group_index, analysis in enumerate(GEN1_ANALYSIS_FAMILIES):
            candidate = validation.copy()
            rng = np.random.default_rng(
                policy.random_seed + 10_000 * (fold.index + 1) + group_index
            )
            order = rng.permutation(len(candidate))
            group_columns = columns[analysis]
            candidate[:, group_columns] = candidate[order][:, group_columns]
            permuted_probabilities[analysis].extend(
                model.predict_proba(candidate, names).tolist()
            )
    baseline = classification_metrics(baseline_probabilities, outcomes)
    return {
        "status": "SUPPORTED",
        "method": "chronological_oof_within_validation_group_permutation",
        "model": (
            "selected_restrained_xgboost"
            if selected_xgboost is not None
            else "regularized_logistic_fallback_xgboost_not_eligible"
        ),
        "selected_xgboost_candidate": (
            asdict(selected_xgboost) if selected_xgboost is not None else None
        ),
        "model_selection_use": False,
        "baseline": asdict(baseline),
        "groups": {
            analysis: {
                **asdict(
                    classification_metrics(permuted_probabilities[analysis], outcomes)
                ),
                "delta_log_loss": probability_log_loss(
                    permuted_probabilities[analysis], outcomes
                )
                - baseline.log_loss,
                "delta_brier": brier_score(permuted_probabilities[analysis], outcomes)
                - baseline.brier_score,
                "source_families": list(GEN1_ANALYSIS_FAMILY_SOURCES[analysis]),
            }
            for analysis in GEN1_ANALYSIS_FAMILIES
        },
    }


def _ablation_metrics(
    folds: Sequence[RollingOriginFold],
    schema: Gen1FeatureSchema,
    policy: Gen1ResearchPolicy,
    *,
    enabled: bool,
) -> dict[str, dict[str, object]]:
    if not enabled:
        return {
            spec.name: {
                "status": "NOT_RUN_IN_BOUNDED_TEST_MODE",
                "excluded_source_families": list(spec.excluded_families),
            }
            for spec in gen1_leave_one_family_out(schema)
        }
    result: dict[str, dict[str, object]] = {}
    full_rows = _logistic_oof(folds, schema, policy)
    full_metrics = classification_metrics(
        [row[3] for row in full_rows],
        [row[2] for row in full_rows],
    )
    result["full"] = {**asdict(full_metrics), "output_count": len(schema.output_names)}
    for spec in gen1_leave_one_family_out(schema):
        ablated = schema.without(
            families=spec.excluded_families,
            version_suffix=spec.name,
        )
        rows = _logistic_oof(folds, ablated, policy)
        metrics = classification_metrics([row[3] for row in rows], [row[2] for row in rows])
        result[spec.name] = {
            **asdict(metrics),
            "excluded_source_families": list(spec.excluded_families),
            "analysis_family": spec.name.removeprefix("without_"),
            "output_count": len(ablated.output_names),
            "delta_log_loss_vs_full": metrics.log_loss - full_metrics.log_loss,
            "delta_brier_vs_full": metrics.brier_score - full_metrics.brier_score,
        }
    return result


_FAVORITE_CONFIDENCE_BANDS = (
    ("50_55", 0.50, 0.55),
    ("55_60", 0.55, 0.60),
    ("60_65", 0.60, 0.65),
    ("65_70", 0.65, 0.70),
    ("70_plus", 0.70, 1.0000001),
)


def _favorite_band_name(probability: float) -> str:
    confidence = max(float(probability), 1.0 - float(probability))
    for name, lower, upper in _FAVORITE_CONFIDENCE_BANDS:
        if lower <= confidence < upper:
            return name
    raise ValueError("Favorite confidence must be in [0.5, 1]")


def _probability_bands(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    minimum_rows: int,
) -> tuple[dict[str, object], ...]:
    values = np.asarray(probabilities, dtype=float)
    labels = np.asarray(outcomes, dtype=int)
    if len(values) != len(labels) or not len(values):
        raise ValueError("Probability bands require aligned observations")
    result: list[dict[str, object]] = []
    confidence = np.maximum(values, 1.0 - values)
    favorite_home = values >= 0.5
    favorite_won = np.where(favorite_home, labels == 1, labels == 0)
    for name, lower, upper in _FAVORITE_CONFIDENCE_BANDS:
        members = np.flatnonzero((confidence >= lower) & (confidence < upper))
        mean_confidence = (
            float(confidence[members].mean()) if len(members) else None
        )
        realized_frequency = (
            float(favorite_won[members].mean()) if len(members) else None
        )
        calibration_error = (
            realized_frequency - mean_confidence
            if realized_frequency is not None and mean_confidence is not None
            else None
        )
        result.append(
            {
                "band": name,
                "lower": lower,
                "upper": 1.0 if upper > 1 else upper,
                "observations": int(len(members)),
                "mean_favorite_confidence": mean_confidence,
                "favorite_accuracy": realized_frequency,
                "realized_favorite_win_frequency": realized_frequency,
                "calibration_error": calibration_error,
                "absolute_calibration_error": (
                    abs(calibration_error) if calibration_error is not None else None
                ),
                "home_favorite_fraction": (
                    float(favorite_home[members].mean()) if len(members) else None
                ),
                "status": (
                    "SUPPORTED"
                    if len(members) >= minimum_rows
                    else "INSUFFICIENT_SAMPLE"
                ),
                "minimum_rows": minimum_rows,
            }
        )
    return tuple(result)


def _subgroup_members(row: UnlabeledGame, probability: float) -> tuple[str, ...]:
    features = row.features
    groups = [
        f"month_{row.event_at.month:02d}",
        "early_season" if row.event_at.month <= 6 else "later_season",
        "home_favorite" if probability >= 0.5 else "away_favorite",
        f"favorite_band_{_favorite_band_name(probability)}",
    ]
    elo_difference = float(features["home_elo_rating"]) - float(features["away_elo_rating"])
    if elo_difference >= 50:
        groups.append("team_strength_home_clear")
    elif elo_difference <= -50:
        groups.append("team_strength_away_clear")
    else:
        groups.append("team_strength_close")
    doubleheader = float(features["doubleheader_game_number"])
    groups.append("doubleheader" if doubleheader > 1 else "singleheader")
    home_rest = float(features["home_days_since_previous_game"])
    away_rest = float(features["away_days_since_previous_game"])
    groups.append("short_rest" if min(home_rest, away_rest) <= 1 else "normal_rest")
    travel = max(float(features["home_travel_miles"]), float(features["away_travel_miles"]))
    groups.append("travel_500_plus" if travel >= 500 else "travel_under_500")
    rest_travel_missing = any(
        float(features[name]) == 1.0
        for name in (
            "home_days_since_previous_game__missing",
            "away_days_since_previous_game__missing",
            "home_travel_miles__missing",
            "away_travel_miles__missing",
        )
    )
    groups.append(
        "rest_travel_unavailable" if rest_travel_missing else "rest_travel_available"
    )
    bullpen_proxy = max(
        float(features["home_bullpen_fatigue"]),
        float(features["away_bullpen_fatigue"]),
    )
    groups.append("bullpen_proxy_high" if bullpen_proxy >= 0.50 else "bullpen_proxy_low")
    recent_missing = any(
        float(features[name]) == 1.0
        for name in (
            "home_runs_per_game_14d__missing",
            "away_runs_per_game_14d__missing",
        )
    )
    if recent_missing:
        groups.append("recent_form_unavailable")
    else:
        recent_magnitude = abs(
            float(features["home_runs_per_game_14d"])
            - float(features["away_runs_per_game_14d"])
        )
        groups.append(
            "recent_form_large_difference"
            if recent_magnitude >= 0.75
            else "recent_form_small_difference"
        )
    indicators = [
        float(features[name])
        for name in GEN1_UNIVERSE_SCHEMA.output_names
        if name.endswith("__missing")
    ]
    groups.append("high_missingness" if float(np.mean(indicators)) >= 0.20 else "low_missingness")
    return tuple(groups)


def _subgroup_metrics(
    observations: Sequence[UnlabeledGame],
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    minimum_rows: int,
) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(observations):
        for name in _subgroup_members(row, float(probabilities[index])):
            grouped[name].append(index)
    result: dict[str, dict[str, object]] = {}
    for name, indices in sorted(grouped.items()):
        selected_outcomes = [int(outcomes[index]) for index in indices]
        if len(indices) < minimum_rows:
            result[name] = {
                "status": "INSUFFICIENT_ROWS",
                "observations": len(indices),
                "minimum_rows": minimum_rows,
            }
        elif len(set(selected_outcomes)) < 2:
            result[name] = {
                "status": "INSUFFICIENT_CLASSES",
                "observations": len(indices),
                "classes": sorted(set(selected_outcomes)),
            }
        else:
            metrics = classification_metrics(
                [float(probabilities[index]) for index in indices],
                selected_outcomes,
                bins=min(10, len(indices)),
            )
            result[name] = {"status": "SUPPORTED", **asdict(metrics)}
    return result


def _fit_final_bundle(
    development: Sequence[LabeledGame],
    schema: Gen1FeatureSchema,
    policy: Gen1ResearchPolicy,
    selected_xgboost: Gen1XGBoostCandidate | None,
    stacker: NonnegativeStacker,
    decision: EnsembleDecision,
    calibration: CalibrationSelection,
    *,
    retain_negative_binomial: bool,
) -> Gen1FittedBundle:
    matrix = schema.matrix([row.features for row in development])
    names = schema.output_names
    outcomes = [row.home_win for row in development]
    row_ids = [row.game_id for row in development]
    classifiers = {
        name: factory().fit(matrix, outcomes, names, row_ids=row_ids)  # type: ignore[attr-defined]
        for name, factory in _classifier_factories(policy, selected_xgboost).items()
    }
    poisson = PoissonRunRegressor(alpha=policy.poisson_alpha).fit(
        matrix,
        [float(row.home_runs) for row in development],
        [float(row.away_runs) for row in development],
        names,
        row_ids=row_ids,
    )
    negative_binomial = (
        NegativeBinomialRunRegressor(mean_alpha=policy.poisson_alpha).fit(
            matrix,
            [float(row.home_runs) for row in development],
            [float(row.away_runs) for row in development],
            names,
            row_ids=row_ids,
        )
        if retain_negative_binomial
        else None
    )
    team = EmpiricalBayesTeamClassifier(
        prior_strength=policy.team_prior_strength,
        home_prior=policy.home_prior_probability,
    ).fit(
        [str(row.home_team_id) for row in development],
        [str(row.away_team_id) for row in development],
        outcomes,
    )
    team_counts: dict[str, int] = defaultdict(int)
    for row in development:
        team_counts[str(row.home_team_id)] += 1
        team_counts[str(row.away_team_id)] += 1
    ood = OODDetector().fit(matrix, names)
    final_imputer = FoldTrainingMedianImputer(schema).fit(
        [row.features for row in development],
        row_ids=row_ids,
    )
    return Gen1FittedBundle(
        schema=schema,
        classifiers=classifiers,
        poisson_model=poisson,
        negative_binomial_model=negative_binomial,
        team_model=team,
        stacker=stacker,
        ensemble_decision=decision,
        calibration=calibration,
        ood_detector=ood,
        selected_xgboost_candidate=selected_xgboost,
        training_only_imputation_parameters=final_imputer.parameters,
        team_fit_sample_counts=dict(sorted(team_counts.items())),
    )


def _development_run_evidence(
    rows: Sequence[Gen1RunOOFRow],
) -> dict[str, dict[str, object]]:
    if not rows:
        raise ValueError("Gen-1 run evidence requires OOF validation rows")
    contracts = {
        "run_strength": "fixed_transparent_a2_talent_average",
        "poisson": "separate_regularized_poisson_log_means",
        "negative_binomial": (
            "shared_poisson_means_plus_training_residual_nb2_dispersion"
        ),
    }
    result: dict[str, dict[str, object]] = {}
    for name, contract in contracts.items():
        predicted_home = [row.predictions[name][0] for row in rows]
        predicted_away = [row.predictions[name][1] for row in rows]
        aggregate = _run_metrics(
            [row.actual_home_runs for row in rows],
            [row.actual_away_runs for row in rows],
            predicted_home,
            predicted_away,
        )
        fold_metrics = []
        for fold_index in sorted({row.fold_index for row in rows}):
            fold = [row for row in rows if row.fold_index == fold_index]
            fold_metrics.append(
                {
                    "fold_index": fold_index,
                    **_run_metrics(
                        [row.actual_home_runs for row in fold],
                        [row.actual_away_runs for row in fold],
                        [row.predictions[name][0] for row in fold],
                        [row.predictions[name][1] for row in fold],
                    ),
                }
            )
        result[name] = {
            "status": "SUPPORTED_DEVELOPMENT_OOF",
            "distribution_contract": contract,
            "independent_negative_binomial_mean_fit": (
                False if name == "negative_binomial" else None
            ),
            "aggregate": aggregate,
            "folds": fold_metrics,
        }
    return result


def _covariate_ood_evidence(
    fitted: Gen1FittedBundle,
    observations: Sequence[UnlabeledGame],
) -> dict[str, object]:
    """Label-free per-game reliability, schedule, and distribution status."""

    matrix = fitted.schema.matrix([row.features for row in observations])
    detector = fitted.ood_detector
    if any(
        value is None
        for value in (
            detector._median,
            detector._scale,
            detector._minimum,
            detector._maximum,
        )
    ):
        raise RuntimeError("Gen-1 OOD detector is not fitted")
    median = np.asarray(detector._median, dtype=float)
    scale = np.asarray(detector._scale, dtype=float)
    minimum = np.asarray(detector._minimum, dtype=float)
    maximum = np.asarray(detector._maximum, dtype=float)
    names = fitted.schema.output_names
    raw_indices = [index for index, name in enumerate(names) if not name.endswith("__missing")]
    reliability_names = tuple(
        spec.name for spec in fitted.schema.features if "reliability" in spec.name
    )
    by_game: dict[str, dict[str, object]] = {}
    counts = {"NORMAL": 0, "CAUTION": 0, "LOW_RELIABILITY": 0}
    for row_index, observation in enumerate(observations):
        values = matrix[row_index]
        robust_z = np.abs((values[raw_indices] - median[raw_indices]) / scale[raw_indices])
        outside = (values[raw_indices] < minimum[raw_indices]) | (
            values[raw_indices] > maximum[raw_indices]
        )
        reasons: list[str] = []
        if float(np.max(robust_z)) > detector.robust_z_threshold:
            reasons.append("robust_z")
        if float(np.mean(outside)) > detector.outside_range_threshold:
            reasons.append("outside_training_range")
        observed_reliability = [
            float(observation.features[name])
            for name in reliability_names
            if float(observation.features[f"{name}__missing"]) == 0.0
        ]
        reliability_mean = (
            float(np.mean(observed_reliability)) if observed_reliability else None
        )
        reliability_coverage = len(observed_reliability) / len(reliability_names)
        low_reliability = (
            reliability_mean is None
            or reliability_mean < 0.20
            or reliability_coverage < 0.50
        )
        schedule_reasons = []
        if float(observation.features["doubleheader_game_number"]) > 1.0:
            schedule_reasons.append("doubleheader")
        if min(
            float(observation.features["home_days_since_previous_game"]),
            float(observation.features["away_days_since_previous_game"]),
        ) < 1.0:
            schedule_reasons.append("sub_one_day_rest")
        if max(
            float(observation.features["home_travel_miles"]),
            float(observation.features["away_travel_miles"]),
        ) > 2_500.0:
            schedule_reasons.append("travel_over_2500_miles")
        schedule_missing = float(
            np.mean(
                [
                    float(observation.features[f"{name}__missing"])
                    for name in (
                        "home_days_since_previous_game",
                        "away_days_since_previous_game",
                        "home_travel_miles",
                        "away_travel_miles",
                    )
                ]
            )
        )
        if schedule_missing >= 0.50:
            schedule_reasons.append("schedule_state_incomplete")
        reasons.extend(schedule_reasons)
        status = (
            "LOW_RELIABILITY"
            if low_reliability
            else "CAUTION"
            if reasons
            else "NORMAL"
        )
        counts[status] += 1
        by_game[observation.game_id] = {
            "status": status,
            "reasons": sorted(set(reasons)),
            "bayesian_reliability_mean": reliability_mean,
            "bayesian_reliability_coverage": reliability_coverage,
            "maximum_robust_z": float(np.max(robust_z)),
            "outside_training_range_fraction": float(np.mean(outside)),
            "schedule_state_reasons": schedule_reasons,
            "outcome_labels_used": False,
        }
    aggregate_status = (
        "LOW_RELIABILITY"
        if counts["LOW_RELIABILITY"]
        else "CAUTION"
        if counts["CAUTION"]
        else "NORMAL"
    )
    return {
        "status": aggregate_status,
        "counts": counts,
        "observations": len(observations),
        "per_prediction": by_game,
        "policy": {
            "low_reliability_mean_below": 0.20,
            "low_reliability_coverage_below": 0.50,
            "unusual_schedule_states": [
                "doubleheader",
                "sub_one_day_rest",
                "travel_over_2500_miles",
                "schedule_state_incomplete",
            ],
        },
        "outcome_labels_used": False,
    }


def run_gen1_development(
    rows: Sequence[object],
    policy: Gen1ResearchPolicy = Gen1ResearchPolicy(),
    *,
    source_dataset_rows_checksum: str,
    enable_xgboost: bool = True,
    run_ablations: bool = True,
) -> Gen1DevelopmentResult:
    """Fit and freeze development-only evidence; never read holdout labels."""

    _require_checksum(source_dataset_rows_checksum, "source_dataset_rows_checksum")
    split = split_gen1_rows(rows, policy)
    folds = rolling_origin_folds(
        split.development,
        min_train_dates=policy.minimum_train_dates,
        validation_dates=policy.validation_dates,
        step_dates=policy.step_dates,
    )
    if len(folds) < policy.minimum_rolling_folds:
        raise ValueError("Gen-1 requires at least five chronological rolling folds")
    if any(len({row.home_win for row in fold.train}) < 2 for fold in folds):
        raise ValueError("Every Gen-1 training fold must contain both outcome classes")
    recent_variant, recent_evidence = _select_recent_variant(folds, policy)
    schema = recent_variant.schema
    selected_xgboost, xgboost_evidence = _select_xgboost_candidate(
        split.development,
        folds,
        schema,
        policy,
        enabled=enable_xgboost,
    )
    base_oof = _generate_base_oof(
        folds,
        schema,
        policy,
        selected_xgboost=selected_xgboost,
    )
    retain_negative_binomial, nb_retention = (
        _negative_binomial_retention_decision(base_oof, policy)
    )
    retained_base_rows = _base_rows_for_retention(
        base_oof.rows,
        retain_negative_binomial=retain_negative_binomial,
    )
    stack_rows, final_stacker = _cross_fit_ensemble(retained_base_rows, policy)
    decision, base_metrics, ensemble_metrics = _ensemble_decision(stack_rows, policy)
    selected_oof = _selected_oof_probabilities(stack_rows, decision)
    oof_outcomes = np.asarray([row.outcome for row in stack_rows], dtype=int)
    selected_oof_metrics = classification_metrics(selected_oof, oof_outcomes)
    base_secondary = {
        name: _secondary_classification_metrics(
            [row.predictions[name] for row in stack_rows],
            oof_outcomes,
        )
        for name in sorted(stack_rows[0].predictions)
    }
    selected_secondary = _secondary_classification_metrics(
        selected_oof,
        oof_outcomes,
    )
    raw_ensemble_secondary = _secondary_classification_metrics(
        [row.raw_ensemble_probability for row in stack_rows],
        oof_outcomes,
    )
    calibration = select_probability_calibrator(
        selected_oof,
        oof_outcomes,
        [row.event_at for row in stack_rows],
        selection_fraction=policy.calibration_selection_fraction,
        min_isotonic_samples=policy.minimum_isotonic_samples,
        allowed_methods=("identity", "platt", "isotonic"),
    )
    fitted = _fit_final_bundle(
        split.development,
        schema,
        policy,
        selected_xgboost,
        final_stacker,
        decision,
        calibration,
        retain_negative_binomial=retain_negative_binomial,
    )
    holdout_predictions = fitted.predict(split.holdout.observations)
    development_matrix = schema.matrix([row.features for row in split.development])
    holdout_matrix = schema.matrix([row.features for row in split.holdout.observations])
    ood = fitted.ood_detector.diagnose(holdout_matrix, schema.output_names)
    covariate_ood = _covariate_ood_evidence(
        fitted,
        split.holdout.observations,
    )
    paired = _paired_bootstrap(stack_rows, selected_oof, policy)
    development_run_metrics = _development_run_evidence(base_oof.run_rows)
    development_run_metrics["negative_binomial"]["retained_for_final_candidate"] = (
        retain_negative_binomial
    )
    missingness = _missingness_comparison(
        folds,
        schema,
        policy,
        selected_xgboost,
    )
    ablations = _ablation_metrics(
        folds,
        schema,
        policy,
        enabled=run_ablations,
    )
    development_by_game = {row.game_id: row for row in split.development}
    stack_observations = tuple(development_by_game[row.game_id].unlabeled() for row in stack_rows)
    subgroups = _subgroup_metrics(
        stack_observations,
        selected_oof,
        oof_outcomes,
        minimum_rows=policy.minimum_subgroup_rows,
    )
    bands = _probability_bands(
        selected_oof,
        oof_outcomes,
        minimum_rows=policy.minimum_subgroup_rows,
    )
    permutation = _oof_group_permutation_importance(
        folds,
        schema,
        policy,
        selected_xgboost,
    )
    calibrator_evidence = _calibration_candidate_evidence(
        selected_oof,
        oof_outcomes,
        [row.event_at for row in stack_rows],
        policy,
        calibration,
    )
    calibration_metadata = {
        "method": calibration.method,
        "selection_scores": dict(calibration.selection_scores),
        "candidate_evidence": calibrator_evidence,
        "calibration_fit_count": calibration.calibration_fit_count,
        "selection_count": calibration.selection_count,
        "total_validation_count": calibration.total_validation_count,
        "selection_started_at": calibration.selection_started_at.isoformat(),
        "allowed_methods": ["identity", "platt", "isotonic"],
        "fit_basis": "development_cross_fitted_oof_only",
        "holdout_labels_used": False,
    }
    research_rule = frozen_research_decision_rule(policy)
    identity_payload = {
        "policy": asdict(policy),
        "source_dataset_rows_checksum": source_dataset_rows_checksum,
        "source_schema_hash": A2_SCHEMA_HASH,
        "universe_schema_hash": GEN1_UNIVERSE_SCHEMA_HASH,
        "selected_schema_hash": schema.schema_hash,
        "feature_contract_hash": gen1_feature_contract_hash(schema),
        "recent_variant": recent_variant.name,
        "split_identity": split.holdout.holdout_identity_checksum,
        "development_ids": list(split.holdout.development_game_ids),
        "fold_count": len(folds),
        "ensemble_decision": asdict(decision),
        "negative_binomial_retention": nb_retention,
        "ensemble_weights": final_stacker.fit_.weight_map if final_stacker.fit_ else {},
        "calibration": calibration_metadata,
        "research_decision_rule": asdict(research_rule),
        "holdout_predictions_checksum": _checksum(asdict(holdout_predictions)),
        "covariate_ood_checksum": _checksum(covariate_ood),
    }
    identity = _checksum(identity_payload)
    per_game_ood = covariate_ood["per_prediction"]
    if not isinstance(per_game_ood, Mapping):
        raise RuntimeError("Gen-1 per-prediction OOD evidence drifted")
    holdout_drivers = {
        observation.game_id: {
            **fitted.prediction_drivers(
                observation,
                frozen_predictions=holdout_predictions,
                frozen_prediction_index=index,
            ),
            "ood": per_game_ood[observation.game_id],
        }
        for index, observation in enumerate(split.holdout.observations)
    }
    driver_contract = fitted.require_prediction_driver_freeze_ready(holdout_drivers)
    explainability = {
        **fitted.explainability_summary(),
        "oof_group_permutation_importance": permutation,
        "per_prediction_driver_contract": driver_contract,
    }
    del development_matrix  # Explicitly avoid retaining the full matrix in the result.
    return Gen1DevelopmentResult(
        status=GEN1_RESEARCH_FROZEN_STATUS,
        policy=policy,
        source_dataset_rows_checksum=source_dataset_rows_checksum,
        selected_schema=schema,
        recent_variant=recent_variant,
        split=split.holdout,
        folds=base_oof.audits,
        base_oof_metrics=base_metrics,
        base_oof_secondary_metrics=base_secondary,
        ensemble_oof_metrics=ensemble_metrics,
        ensemble_oof_secondary_metrics=raw_ensemble_secondary,
        selected_oof_metrics=selected_oof_metrics,
        selected_oof_secondary_metrics=selected_secondary,
        development_run_metrics=development_run_metrics,
        negative_binomial_retention=nb_retention,
        calibration=calibration_metadata,
        ensemble_decision=decision,
        research_decision_rule=research_rule,
        paired_bootstrap=paired,
        recent_variant_evidence=recent_evidence,
        xgboost_candidate_evidence=xgboost_evidence,
        missingness_comparison=missingness,
        ablation_metrics=ablations,
        subgroup_metrics=subgroups,
        probability_bands=bands,
        ood_report=ood,
        covariate_ood=covariate_ood,
        explainability=explainability,
        holdout_driver_summaries=holdout_drivers,
        holdout_predictions=holdout_predictions,
        fitted_bundle=fitted,
        candidate_identity_checksum=identity,
    )


def _run_metrics(
    actual_home: Sequence[float],
    actual_away: Sequence[float],
    predicted_home: Sequence[float],
    predicted_away: Sequence[float],
) -> dict[str, float | int]:
    if not (
        len(actual_home)
        == len(actual_away)
        == len(predicted_home)
        == len(predicted_away)
        and len(actual_home)
    ):
        raise ValueError("Run metrics require aligned non-empty values")
    actual_home_array = np.asarray(actual_home, dtype=float)
    actual_away_array = np.asarray(actual_away, dtype=float)
    predicted_home_array = np.asarray(predicted_home, dtype=float)
    predicted_away_array = np.asarray(predicted_away, dtype=float)
    actual_total = actual_home_array + actual_away_array
    predicted_total = predicted_home_array + predicted_away_array
    actual_differential = actual_home_array - actual_away_array
    predicted_differential = predicted_home_array - predicted_away_array
    return {
        "observations": len(actual_home),
        "home_rmse": float(np.sqrt(np.mean((actual_home_array - predicted_home_array) ** 2))),
        "away_rmse": float(np.sqrt(np.mean((actual_away_array - predicted_away_array) ** 2))),
        "total_rmse": float(np.sqrt(np.mean((actual_total - predicted_total) ** 2))),
        "home_mae": float(np.mean(np.abs(actual_home_array - predicted_home_array))),
        "away_mae": float(np.mean(np.abs(actual_away_array - predicted_away_array))),
        "total_mae": float(np.mean(np.abs(actual_total - predicted_total))),
        "differential_rmse": float(
            np.sqrt(np.mean((actual_differential - predicted_differential) ** 2))
        ),
        "differential_mae": float(
            np.mean(np.abs(actual_differential - predicted_differential))
        ),
    }


def _block_bootstrap_metric_interval(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    groups: Sequence[object],
    *,
    metric: Callable[[Sequence[float], Sequence[int]], float],
    resamples: int,
    random_seed: int,
) -> dict[str, float | int]:
    values = np.asarray(probabilities, dtype=float)
    labels = np.asarray(outcomes, dtype=int)
    if len(values) != len(labels) or len(groups) != len(labels):
        raise ValueError("Metric bootstrap inputs must align")
    unique_groups = tuple(dict.fromkeys(groups))
    if len(unique_groups) < 2 or resamples < 50:
        raise ValueError("Metric bootstrap requires two groups and fifty resamples")
    indices = {
        group: np.asarray(
            [index for index, value in enumerate(groups) if value == group],
            dtype=int,
        )
        for group in unique_groups
    }
    rng = np.random.default_rng(random_seed)
    estimates = np.empty(resamples, dtype=float)
    for sample_index in range(resamples):
        selected = rng.choice(
            len(unique_groups),
            size=len(unique_groups),
            replace=True,
        )
        sample = np.concatenate(
            [indices[unique_groups[index]] for index in selected]
        )
        estimates[sample_index] = metric(
            values[sample].tolist(),
            labels[sample].astype(int).tolist(),
        )
    return {
        "estimate": metric(values.tolist(), labels.astype(int).tolist()),
        "lower": float(np.quantile(estimates, 0.025)),
        "upper": float(np.quantile(estimates, 0.975)),
        "confidence_level": 0.95,
        "resamples": resamples,
        "date_groups": len(unique_groups),
    }


def _apply_frozen_research_decision_rule(
    development: Gen1DevelopmentResult,
    candidate: ClassificationMetrics,
    comparisons: Mapping[str, ClassificationMetrics],
    paired: Mapping[str, Mapping[str, PairedBootstrapDelta]],
    run_metrics: Mapping[str, Mapping[str, float | int | str | None]],
) -> tuple[str, dict[str, bool]]:
    rule = development.research_decision_rule
    evidence_complete = all(
        name in comparisons
        and name in paired
        and all(metric in paired[name] for metric in rule.require_lower_bound_nonnegative_for)
        for name in rule.matched_probability_comparators
    )
    point_improvement = evidence_complete and all(
        candidate.log_loss < comparisons[name].log_loss
        and candidate.brier_score < comparisons[name].brier_score
        for name in rule.matched_probability_comparators
    )
    uncertainty = evidence_complete and all(
        paired[name][metric].lower >= 0.0
        for name in rule.matched_probability_comparators
        for metric in rule.require_lower_bound_nonnegative_for
    )
    calibration = (
        candidate.expected_calibration_error <= rule.maximum_ece
        and abs(candidate.calibration_intercept)
        <= rule.maximum_abs_calibration_intercept
        and rule.minimum_calibration_slope
        <= candidate.calibration_slope
        <= rule.maximum_calibration_slope
    )
    home_prior = comparisons.get("home_prior")
    gross_degradation = home_prior is None or (
        candidate.brier_score
        > home_prior.brier_score + rule.reject_brier_degradation_vs_home_prior
        or candidate.log_loss
        > home_prior.log_loss + rule.reject_log_loss_degradation_vs_home_prior
        or candidate.expected_calibration_error > rule.reject_maximum_ece
        or candidate.calibration_slope < rule.reject_minimum_calibration_slope
        or candidate.calibration_slope > rule.reject_maximum_calibration_slope
    )
    clear_uncertainty_degradation = bool(
        evidence_complete
        and any(
            paired[name][metric].upper < 0.0
            for name in rule.matched_probability_comparators
            for metric in rule.require_lower_bound_nonnegative_for
        )
    )
    baseline_run = run_metrics.get("run_strength")
    learned_runs = [run_metrics.get("poisson"), run_metrics.get("negative_binomial")]
    run_names = ("total_rmse", "total_mae", "differential_rmse", "differential_mae")
    run_non_degradation = bool(baseline_run) and any(
        learned is not None
        and all(float(learned[name]) <= float(baseline_run[name]) for name in run_names)
        for learned in learned_runs
    )
    checks = {
        "complete_matched_evidence": evidence_complete,
        "point_improvement_vs_home_prior_elo_and_logistic": point_improvement,
        "time_block_bootstrap_lower_bounds_nonnegative": uncertainty,
        "calibration_thresholds": calibration,
        "ood_clear_before_label_access": (
            not development.ood_report.flagged
            and development.covariate_ood.get("status") == "NORMAL"
        ),
        "run_model_non_degradation": run_non_degradation,
        "research_only_no_promotion": rule.promotion_decision
        == "RESEARCH_ONLY_NO_PROMOTION",
        "not_grossly_degraded_vs_home_prior": not gross_degradation,
        "no_statistically_clear_degradation": not clear_uncertainty_degradation,
    }
    if not evidence_complete:
        return rule.rejected_label, checks
    if gross_degradation or clear_uncertainty_degradation:
        return rule.rejected_label, checks
    if all(checks.values()):
        return rule.candidate_label, checks
    return rule.challenger_label, checks


def evaluate_gen1_holdout(
    development: Gen1DevelopmentResult,
    labeled_holdout_rows: Sequence[object],
    *,
    verified_source_dataset_rows_checksum: str,
) -> Gen1HoldoutEvaluation:
    """Explicit single-use evaluation primitive; storage enforces the one-use claim."""

    _require_checksum(
        verified_source_dataset_rows_checksum,
        "verified_source_dataset_rows_checksum",
    )
    if verified_source_dataset_rows_checksum != development.source_dataset_rows_checksum:
        raise ValueError("Final-holdout labels are not bound to the frozen A2 rows checksum")
    row_ids = [str(getattr(row, "game_id")) for row in labeled_holdout_rows]
    if len(set(row_ids)) != len(row_ids):
        raise ValueError("Final-holdout source contains duplicate game identities")
    by_id = {str(getattr(row, "game_id")): row for row in labeled_holdout_rows}
    if set(by_id) != set(development.split.holdout_game_ids):
        raise ValueError("Final-holdout game identities do not match the frozen plan")
    ordered_rows = tuple(by_id[game_id] for game_id in development.split.holdout_game_ids)
    observations = tuple(_blind_observation(row) for row in ordered_rows)
    if _blind_identity(observations) != development.split.holdout_identity_checksum:
        raise ValueError("Final-holdout covariates changed after candidate freeze")
    if tuple(development.holdout_predictions.game_ids) != development.split.holdout_game_ids:
        raise ValueError("Frozen final-holdout predictions do not match the holdout plan")

    # Labels are dereferenced only after exact frozen identity revalidation.
    outcomes = tuple(int(getattr(row, "home_win")) for row in ordered_rows)
    home_runs = tuple(float(getattr(row, "home_runs")) for row in ordered_rows)
    away_runs = tuple(float(getattr(row, "away_runs")) for row in ordered_rows)
    probabilities = development.holdout_predictions.calibrated_probability
    candidate_metrics = classification_metrics(probabilities, outcomes)
    candidate_secondary = _secondary_classification_metrics(probabilities, outcomes)
    comparisons = {
        name: classification_metrics(values, outcomes)
        for name, values in development.holdout_predictions.base_probabilities.items()
    }
    comparisons["selected_raw"] = classification_metrics(
        development.holdout_predictions.raw_selected_probability,
        outcomes,
    )
    comparison_secondary = {
        name: _secondary_classification_metrics(values, outcomes)
        for name, values in development.holdout_predictions.base_probabilities.items()
    }
    comparison_secondary["selected_raw"] = _secondary_classification_metrics(
        development.holdout_predictions.raw_selected_probability,
        outcomes,
    )
    dates = [row.event_at.date() for row in observations]
    paired: dict[str, dict[str, PairedBootstrapDelta]] = {}
    for name, values in development.holdout_predictions.base_probabilities.items():
        paired[name] = {
            "log_loss": paired_block_bootstrap_delta(
                values,
                probabilities,
                outcomes,
                dates,
                metric=probability_log_loss,
                resamples=development.policy.bootstrap_resamples,
                random_seed=development.policy.random_seed,
            ),
            "brier": paired_block_bootstrap_delta(
                values,
                probabilities,
                outcomes,
                dates,
                metric=brier_score,
                resamples=development.policy.bootstrap_resamples,
                random_seed=development.policy.random_seed + 1,
            ),
        }
    candidate_intervals = {
        "log_loss": _block_bootstrap_metric_interval(
            probabilities,
            outcomes,
            dates,
            metric=probability_log_loss,
            resamples=development.policy.bootstrap_resamples,
            random_seed=development.policy.random_seed + 2,
        ),
        "brier": _block_bootstrap_metric_interval(
            probabilities,
            outcomes,
            dates,
            metric=brier_score,
            resamples=development.policy.bootstrap_resamples,
            random_seed=development.policy.random_seed + 3,
        ),
    }
    run_metrics = {}
    for name, values in development.holdout_predictions.run_predictions.items():
        metrics = _run_metrics(home_runs, away_runs, values[0], values[1])
        metrics["distribution_contract"] = {
            "run_strength": "fixed_transparent_talent_average",
            "poisson": "separate_regularized_poisson_log_means",
            "negative_binomial": (
                "shared_poisson_means_plus_training_residual_nb2_dispersion"
            ),
        }[name]
        metrics["independent_negative_binomial_mean_fit"] = (
            False if name == "negative_binomial" else None
        )
        run_metrics[name] = metrics
    research_decision, research_checks = _apply_frozen_research_decision_rule(
        development,
        candidate_metrics,
        comparisons,
        paired,
        run_metrics,
    )
    subgroups = _subgroup_metrics(
        observations,
        probabilities,
        outcomes,
        minimum_rows=development.policy.minimum_subgroup_rows,
    )
    prediction_rows = tuple(
        {
            "game_id": observation.game_id,
            "event_at": observation.event_at,
            "candidate_probability": probabilities[index],
            "selected_raw_probability": (
                development.holdout_predictions.raw_selected_probability[index]
            ),
            "base_probabilities": {
                name: values[index]
                for name, values in development.holdout_predictions.base_probabilities.items()
            },
            "actual_home_win": outcomes[index],
            "actual_home_runs": home_runs[index],
            "actual_away_runs": away_runs[index],
            "run_predictions": {
                name: {"home": values[0][index], "away": values[1][index]}
                for name, values in development.holdout_predictions.run_predictions.items()
            },
            "favorite_confidence_band": _favorite_band_name(probabilities[index]),
            "ood": development.holdout_driver_summaries[observation.game_id]["ood"],
        }
        for index, observation in enumerate(observations)
    )
    bands = _probability_bands(
        probabilities,
        outcomes,
        minimum_rows=development.policy.minimum_subgroup_rows,
    )
    identity_payload = {
        "candidate": development.candidate_identity_checksum,
        "holdout": development.split.holdout_identity_checksum,
        "candidate_metrics": asdict(candidate_metrics),
        "candidate_secondary_metrics": candidate_secondary,
        "comparison_metrics": {
            name: asdict(value) for name, value in comparisons.items()
        },
        "comparison_secondary_metrics": comparison_secondary,
        "run_metrics": run_metrics,
        "paired_bootstrap": {
            name: {metric: asdict(value) for metric, value in metrics.items()}
            for name, metrics in paired.items()
        },
        "candidate_metric_intervals": candidate_intervals,
        "subgroups": subgroups,
        "probability_bands": bands,
        "prediction_rows_checksum": _checksum(prediction_rows),
        "research_decision_rule": asdict(development.research_decision_rule),
        "research_decision": research_decision,
        "research_decision_checks": research_checks,
        "status": GEN1_RESEARCH_EVALUATED_STATUS,
        "promotion": "RESEARCH_ONLY_NO_PROMOTION",
    }
    identity = _checksum(identity_payload)
    return Gen1HoldoutEvaluation(
        status=GEN1_RESEARCH_EVALUATED_STATUS,
        candidate_identity_checksum=development.candidate_identity_checksum,
        holdout_identity_checksum=development.split.holdout_identity_checksum,
        observations=len(outcomes),
        candidate_metrics=candidate_metrics,
        candidate_secondary_metrics=candidate_secondary,
        comparison_metrics=comparisons,
        comparison_secondary_metrics=comparison_secondary,
        run_metrics=run_metrics,
        paired_bootstrap=paired,
        candidate_metric_intervals=candidate_intervals,
        subgroup_metrics=subgroups,
        probability_bands=bands,
        prediction_rows=prediction_rows,
        evaluation_identity_checksum=identity,
        research_decision=research_decision,
        research_decision_checks=research_checks,
    )


__all__ = [
    "A2EloClassifier",
    "A2RunStrengthClassifier",
    "BlindHoldoutPlan",
    "DEFAULT_XGBOOST_CANDIDATES",
    "EnsembleDecision",
    "FoldTrainingMedianImputer",
    "GEN1_ANALYSIS_FAMILIES",
    "GEN1_ANALYSIS_FAMILY_SOURCES",
    "GEN1_EXCLUDED_SOURCE_FAMILIES",
    "GEN1_RESEARCH_EVALUATED_STATUS",
    "GEN1_RESEARCH_FROZEN_STATUS",
    "GEN1_SUPPORTED_SOURCE_FAMILIES",
    "GEN1_UNIVERSE_OUTPUT_COUNT",
    "GEN1_UNIVERSE_RAW_FEATURE_COUNT",
    "GEN1_UNIVERSE_SCHEMA",
    "GEN1_UNIVERSE_SCHEMA_HASH",
    "GEN1_UNIVERSE_SCHEMA_VERSION",
    "Gen1DevelopmentResult",
    "Gen1FeatureSchema",
    "Gen1FittedBundle",
    "Gen1HoldoutEvaluation",
    "Gen1PredictionBundle",
    "Gen1ResearchPolicy",
    "Gen1Split",
    "Gen1XGBoostCandidate",
    "MLB_GEN1_RESEARCH_POLICY_VERSION",
    "NativeMissingXGBoostClassifier",
    "RecentVariantDefinition",
    "analysis_family_for_source",
    "evaluate_gen1_holdout",
    "gen1_analysis_mapping",
    "gen1_leave_one_family_out",
    "recent_variant_definitions",
    "run_gen1_development",
    "split_gen1_rows",
    "supported_family_coverage",
    "validate_strict_a2_covariates",
]
