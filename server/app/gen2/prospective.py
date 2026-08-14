"""Immutable, provider-free governance for prospective Generation 2 evidence.

This module deliberately contains no database, provider, scheduler, or outcome
adapter.  It defines the semantic identities that a persistence layer must
append, and an in-memory ledger used to prove the two important boundaries:

* a frozen plan becomes invalid when code, schema, or model components drift;
* target values may only be loaded after a plan/checkpoint claim is consumed.

Target revision references expose opaque checksums and availability metadata,
not score or winner values.  That lets a claim bind the exact eligible
population and latest available official revisions before the label loader is
invoked.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
import json
import re


PROSPECTIVE_POLICY_VERSION = "mlb_gen2_prospective_policy_v1"
PROSPECTIVE_PLAN_SCHEMA_VERSION = "mlb_gen2_prospective_evaluation_plan_v1"
PROSPECTIVE_CLAIM_SCHEMA_VERSION = "mlb_gen2_prospective_evaluation_claim_v1"
PROSPECTIVE_CLAIM_BINDING_SCHEMA_VERSION = (
    "mlb_gen2_prospective_evaluation_claim_binding_v1"
)
PROSPECTIVE_GAME_ASSIGNMENT_SCHEMA_VERSION = (
    "mlb_gen2_prospective_game_assignment_v1"
)
PROSPECTIVE_LABEL_ACCESS_SCHEMA_VERSION = "mlb_gen2_prospective_label_access_v1"
PROSPECTIVE_DECISION_SCHEMA_VERSION = "mlb_gen2_prospective_decision_v1"
TARGET_REVISION_COMMITMENT_SCHEMA_VERSION = (
    "mlb_gen2_prospective_target_revision_commitment_v1"
)
ELIGIBILITY_ASSESSMENT_SCHEMA_VERSION = "mlb_gen2_prospective_eligibility_assessment_v1"
GEN2_RESEARCH_POLICY_VERSION = "MLB_GEN2_RESEARCH_POLICY_V1"

PRIMARY_PROSPECTIVE_LIFECYCLE = "FINAL_T_MINUS_10"
GEN2_PROSPECTIVE_REGISTRATION_STATUS = "GEN2_PROSPECTIVE_RESEARCH_CHALLENGER"
CURRENT_DEVELOPMENT_FORECAST_TRACK = "CURRENT_DEVELOPMENT"
GEN2_RESEARCH_FORECAST_TRACK = "GEN2_RESEARCH_CHALLENGER"
ASP_INDEPENDENT_FORECAST_FAMILY = "ASP_INDEPENDENT"
ASP_MARKET_AUGMENTED_FORECAST_FAMILY = "ASP_MARKET_AUGMENTED_DEFERRED"

INTERIM_COMPLETED_GAME_THRESHOLD = 300
FINAL_COMPLETED_GAME_THRESHOLD = 500

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEX_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
class ProspectiveGovernanceError(RuntimeError):
    """Base error for a fail-closed prospective governance operation."""


class ProspectivePlanDriftError(ProspectiveGovernanceError):
    """Raised when runtime identity no longer matches the frozen plan."""


class ProspectiveClaimError(ProspectiveGovernanceError):
    """Raised when a checkpoint cannot atomically consume a claim."""


class ProspectiveLabelAccessError(ProspectiveGovernanceError):
    """Raised before any target loader is invoked without a valid claim."""


class ForecastLifecycle(str, Enum):
    """Declared forecast lifecycle vocabulary; values are display-stable."""

    OVERNIGHT = "Overnight"
    MORNING = "Morning"
    T_MINUS_6 = "T-6"
    T_MINUS_3 = "T-3"
    T_MINUS_60 = "T-60"
    T_MINUS_30 = "T-30"
    T_MINUS_10 = "T-10"
    POSTGAME = "Postgame"


DECLARED_FORECAST_LIFECYCLES = tuple(item.value for item in ForecastLifecycle)


class ProspectiveCheckpoint(str, Enum):
    INTERIM_300 = "INTERIM_300_COMPLETED_ELIGIBLE_GAMES"
    FINAL_500 = "FINAL_500_COMPLETED_ELIGIBLE_GAMES"

    @property
    def minimum_completed_games(self) -> int:
        if self is ProspectiveCheckpoint.INTERIM_300:
            return INTERIM_COMPLETED_GAME_THRESHOLD
        return FINAL_COMPLETED_GAME_THRESHOLD


class Gen2ResearchDecision(str, Enum):
    GEN2_RESEARCH_CANDIDATE = "GEN2_RESEARCH_CANDIDATE"
    GEN2_RESEARCH_CHALLENGER = "GEN2_RESEARCH_CHALLENGER"
    GEN2_REJECTED = "GEN2_REJECTED"


ALLOWED_GEN2_TERMINAL_DECISIONS = tuple(item.value for item in Gen2ResearchDecision)


class ProspectiveExclusionReason(str, Enum):
    NO_GEN2_SCORE = "NO_GEN2_SCORE"
    MULTIPLE_PRIMARY_FORECASTS = "MULTIPLE_PRIMARY_FORECASTS"
    WRONG_LIFECYCLE = "WRONG_LIFECYCLE"
    WRONG_FORECAST_TRACK = "WRONG_FORECAST_TRACK"
    WRONG_FORECAST_FAMILY = "WRONG_FORECAST_FAMILY"
    WRONG_SEASON = "WRONG_SEASON"
    NON_REGULAR_SEASON = "NON_REGULAR_SEASON"
    NON_LIVE_EVIDENCE = "NON_LIVE_EVIDENCE"
    AMBIGUOUS_GAME_IDENTITY = "AMBIGUOUS_GAME_IDENTITY"
    MUTABLE_FORECAST = "MUTABLE_FORECAST"
    INCOMPLETE_LINEAGE = "INCOMPLETE_LINEAGE"
    BEFORE_PLAN_WINDOW = "BEFORE_PLAN_WINDOW"
    LATE_OR_POST_START_SCORE = "LATE_OR_POST_START_SCORE"
    PLAN_OR_MODEL_MISMATCH = "PLAN_OR_MODEL_MISMATCH"
    PRESTART_POSTPONED_OLD_OCCURRENCE = "PRESTART_POSTPONED_OLD_OCCURRENCE"
    POST_SUSPENSION_REPLACEMENT = "POST_SUSPENSION_REPLACEMENT"


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
        return _utc(value, "timestamp").isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def canonical_checksum(value: object) -> str:
    """Return the lowercase SHA-256 of canonical JSON semantics."""

    return sha256(_canonical(value)).hexdigest()


def target_revision_commitment_checksum(
    *,
    game_id: str,
    occurrence_id: str,
    revision_id: str,
    revision_sequence: int,
    payload: object,
) -> str:
    """Commit one official revision identity to its canonical label payload."""

    _require_text(game_id, "game id")
    _require_text(occurrence_id, "occurrence id")
    _require_text(revision_id, "revision id")
    if isinstance(revision_sequence, bool) or not isinstance(revision_sequence, int):
        raise TypeError("Target revision sequence must be an integer")
    if revision_sequence < 0:
        raise ValueError("Target revision sequence cannot be negative")
    return canonical_checksum(
        {
            "schema_version": TARGET_REVISION_COMMITMENT_SCHEMA_VERSION,
            "game_id": game_id,
            "occurrence_id": occurrence_id,
            "revision_id": revision_id,
            "revision_sequence": revision_sequence,
            "payload": payload,
        }
    )


def prospective_game_assignment_checksum(
    *,
    plan_identity_checksum: str,
    game_id: str,
    occurrence_id: str,
) -> str:
    """Identity for one globally single-plan prospective game occurrence."""

    _require_sha256(plan_identity_checksum, "plan identity checksum")
    _require_text(game_id, "game id")
    _require_text(occurrence_id, "occurrence id")
    return canonical_checksum(
        {
            "schema_version": PROSPECTIVE_GAME_ASSIGNMENT_SCHEMA_VERSION,
            "plan_identity_checksum": plan_identity_checksum,
            "game_id": game_id,
            "occurrence_id": occurrence_id,
        }
    )


def _require_sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value


def _utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class FrozenRuleSet:
    name: str
    version: str
    rules: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.name, "rule-set name")
        _require_text(self.version, "rule-set version")
        if not self.rules or any(not item.strip() for item in self.rules):
            raise ValueError("A prospective rule set must contain non-empty rules")
        if len(set(self.rules)) != len(self.rules):
            raise ValueError("Prospective rule-set entries must be unique")

    @property
    def identity_checksum(self) -> str:
        return canonical_checksum(asdict(self))


ELIGIBILITY_POLICY = FrozenRuleSet(
    name="prospective_eligibility",
    version="mlb_gen2_prospective_eligibility_v1",
    rules=(
        "CANONICAL_2026_REGULAR_SEASON_LIVE_OCCURRENCE",
        "EXACTLY_ONE_IMMUTABLE_GEN2_FINAL_T_MINUS_10_FORECAST",
        "FORECAST_CREATED_AFTER_PLAN_START_AND_STRICTLY_BEFORE_FIRST_PITCH",
        "COMPLETE_INPUT_MODEL_AND_PLAN_LINEAGE",
        "GOVERNED_OFFICIAL_FINAL_REQUIRED_AT_CLAIM",
    ),
)
EXCLUSION_POLICY = FrozenRuleSet(
    name="prospective_exclusions",
    version="mlb_gen2_prospective_exclusions_v1",
    rules=tuple(item.value for item in ProspectiveExclusionReason),
)
CORRECTION_POLICY = FrozenRuleSet(
    name="prospective_corrections",
    version="mlb_gen2_prospective_corrections_v1",
    rules=(
        "OFFICIAL_TARGET_CORRECTIONS_ARE_APPEND_ONLY",
        "CLAIM_BINDS_LATEST_ELIGIBLE_REVISION_AVAILABLE_AT_CLAIM_TIME",
        "FROZEN_FORECAST_NEVER_CHANGES_AFTER_A_TARGET_CORRECTION",
        "POST_CLAIM_TARGET_REVISIONS_DO_NOT_REWRITE_CONSUMED_EVIDENCE",
    ),
)
POSTPONEMENT_POLICY = FrozenRuleSet(
    name="prospective_postponements",
    version="mlb_gen2_prospective_postponements_v1",
    rules=(
        "PRESTART_POSTPONEMENT_EXCLUDES_THE_OLD_OCCURRENCE",
        "CANONICAL_RESCHEDULED_OCCURRENCE_MAY_EARN_NEW_ELIGIBILITY",
        "SUSPENDED_GAME_REMAINS_BOUND_TO_THE_ORIGINAL_OCCURRENCE",
        "POST_SUSPENSION_REPLACEMENT_FORECAST_IS_FORBIDDEN",
    ),
)

PROSPECTIVE_METRICS = (
    "brier_score",
    "log_loss",
    "calibration_slope",
    "calibration_intercept",
    "expected_calibration_error",
    "roc_auc",
    "accuracy",
    "home_runs_mae",
    "away_runs_mae",
    "total_runs_mae",
    "run_differential_mae",
    "home_runs_rmse",
    "away_runs_rmse",
    "total_runs_rmse",
    "run_differential_rmse",
    "predictive_interval_coverage",
    "ranked_probability_score_or_crps",
)
PROSPECTIVE_BASELINES = (
    "HOME_FIELD_PRIOR",
    "FROZEN_GEN1",
    "CHRONOLOGICAL_ELO",
    "INDEPENDENT_POISSON",
    "NEGATIVE_BINOMIAL",
    "SHARED_ENVIRONMENT_COUNT_MODEL",
)

REQUIRED_FROZEN_COMPONENTS = (
    "historical_model",
    "feature_schema",
    "player_state",
    "event_model",
    "starter_duration",
    "bullpen_usage",
    "base_out_transition",
    "simulator",
    "residual",
    "stack",
    "probability_calibration",
    "event_calibration",
    "ood",
    "missingness",
)


@dataclass(frozen=True, slots=True)
class FrozenComponentIdentity:
    name: str
    version: str
    checksum: str

    def __post_init__(self) -> None:
        _require_text(self.name, "component name")
        _require_text(self.version, f"{self.name} version")
        _require_sha256(self.checksum, f"{self.name} checksum")


@dataclass(frozen=True, slots=True)
class RuntimeIdentitySnapshot:
    code_revision: str
    code_checksum: str
    schema_bundle_version: str
    schema_bundle_checksum: str
    components: tuple[FrozenComponentIdentity, ...]

    def __post_init__(self) -> None:
        _require_text(self.code_revision, "code revision")
        _require_sha256(self.code_checksum, "code checksum")
        _require_text(self.schema_bundle_version, "schema bundle version")
        _require_sha256(self.schema_bundle_checksum, "schema bundle checksum")
        _validate_components(self.components)


def _validate_components(components: tuple[FrozenComponentIdentity, ...]) -> None:
    names = tuple(item.name for item in components)
    if names != REQUIRED_FROZEN_COMPONENTS:
        raise ValueError(
            "Frozen components must match the exact declared component order: "
            + ", ".join(REQUIRED_FROZEN_COMPONENTS)
        )


def _policy_payload() -> dict[str, object]:
    return {
        "policy_version": PROSPECTIVE_POLICY_VERSION,
        "eligibility": asdict(ELIGIBILITY_POLICY) | {
            "identity_checksum": ELIGIBILITY_POLICY.identity_checksum
        },
        "exclusions": asdict(EXCLUSION_POLICY) | {
            "identity_checksum": EXCLUSION_POLICY.identity_checksum
        },
        "corrections": asdict(CORRECTION_POLICY) | {
            "identity_checksum": CORRECTION_POLICY.identity_checksum
        },
        "postponements": asdict(POSTPONEMENT_POLICY) | {
            "identity_checksum": POSTPONEMENT_POLICY.identity_checksum
        },
        "interim_completed_games": INTERIM_COMPLETED_GAME_THRESHOLD,
        "final_completed_games": FINAL_COMPLETED_GAME_THRESHOLD,
        "metrics": PROSPECTIVE_METRICS,
        "baselines": PROSPECTIVE_BASELINES,
    }


PROSPECTIVE_POLICY_IDENTITY_CHECKSUM = canonical_checksum(_policy_payload())


@dataclass(frozen=True, slots=True)
class ProspectiveEvaluationPlan:
    """An immutable prospective window, frozen before its first forecast."""

    plan_id: str
    created_at: datetime
    evaluation_starts_at: datetime
    runtime_identity: RuntimeIdentitySnapshot
    schema_version: str = PROSPECTIVE_PLAN_SCHEMA_VERSION
    policy_version: str = PROSPECTIVE_POLICY_VERSION
    policy_identity_checksum: str = PROSPECTIVE_POLICY_IDENTITY_CHECKSUM
    research_policy_version: str = GEN2_RESEARCH_POLICY_VERSION
    target_season: int = 2026
    lifecycle: str = PRIMARY_PROSPECTIVE_LIFECYCLE
    forecast_lifecycle: ForecastLifecycle = ForecastLifecycle.T_MINUS_10
    forecast_track: str = GEN2_RESEARCH_FORECAST_TRACK
    forecast_family: str = ASP_INDEPENDENT_FORECAST_FAMILY
    registration_status: str = GEN2_PROSPECTIVE_REGISTRATION_STATUS
    interim_completed_games: int = INTERIM_COMPLETED_GAME_THRESHOLD
    final_completed_games: int = FINAL_COMPLETED_GAME_THRESHOLD
    metrics: tuple[str, ...] = PROSPECTIVE_METRICS
    baselines: tuple[str, ...] = PROSPECTIVE_BASELINES

    def __post_init__(self) -> None:
        _require_text(self.plan_id, "plan id")
        created_at = _utc(self.created_at, "created_at")
        starts_at = _utc(self.evaluation_starts_at, "evaluation_starts_at")
        if created_at > starts_at:
            raise ValueError("A prospective plan must be frozen before its evaluation starts")
        if (
            self.schema_version != PROSPECTIVE_PLAN_SCHEMA_VERSION
            or self.policy_version != PROSPECTIVE_POLICY_VERSION
            or self.policy_identity_checksum != PROSPECTIVE_POLICY_IDENTITY_CHECKSUM
            or self.research_policy_version != GEN2_RESEARCH_POLICY_VERSION
        ):
            raise ValueError("Prospective plan schema or policy identity drifted")
        _require_sha256(self.policy_identity_checksum, "prospective policy checksum")
        if self.target_season != 2026:
            raise ValueError("This prospective policy is frozen to the 2026 regular season")
        if (
            self.lifecycle != PRIMARY_PROSPECTIVE_LIFECYCLE
            or self.forecast_lifecycle is not ForecastLifecycle.T_MINUS_10
            or self.forecast_track != GEN2_RESEARCH_FORECAST_TRACK
            or self.forecast_family != ASP_INDEPENDENT_FORECAST_FAMILY
            or self.registration_status != GEN2_PROSPECTIVE_REGISTRATION_STATUS
        ):
            raise ValueError("Prospective plan lifecycle or research track is not authorized")
        if (
            self.interim_completed_games != INTERIM_COMPLETED_GAME_THRESHOLD
            or self.final_completed_games != FINAL_COMPLETED_GAME_THRESHOLD
        ):
            raise ValueError("Prospective thresholds are frozen at 300 interim and 500 final")
        if self.metrics != PROSPECTIVE_METRICS or self.baselines != PROSPECTIVE_BASELINES:
            raise ValueError("Prospective metrics and baselines are frozen")

    @property
    def plan_identity_checksum(self) -> str:
        return canonical_checksum(self.semantic_payload())

    @property
    def model_identity(self) -> FrozenComponentIdentity:
        return self.runtime_identity.components[0]

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "policy_identity_checksum": self.policy_identity_checksum,
            "research_policy_version": self.research_policy_version,
            "plan_id": self.plan_id,
            "created_at": _utc(self.created_at, "created_at"),
            "evaluation_starts_at": _utc(
                self.evaluation_starts_at, "evaluation_starts_at"
            ),
            "target_season": self.target_season,
            "lifecycle": self.lifecycle,
            "forecast_lifecycle": self.forecast_lifecycle,
            "forecast_track": self.forecast_track,
            "forecast_family": self.forecast_family,
            "registration_status": self.registration_status,
            "interim_completed_games": self.interim_completed_games,
            "final_completed_games": self.final_completed_games,
            "metrics": self.metrics,
            "baselines": self.baselines,
            "runtime_identity": asdict(self.runtime_identity),
            "rules": _policy_payload(),
        }

    def require_current_runtime(self, runtime: RuntimeIdentitySnapshot) -> None:
        if runtime != self.runtime_identity:
            raise ProspectivePlanDriftError(
                "Code, schema, or frozen component drift invalidated the prospective plan"
            )


@dataclass(frozen=True, slots=True)
class ProspectiveForecastSnapshot:
    """Label-free metadata for one immutable pregame forecast."""

    game_id: str
    occurrence_id: str
    forecast_id: str
    generated_at: datetime
    scheduled_first_pitch: datetime
    season: int
    game_type: str
    evidence_mode: str
    lifecycle: ForecastLifecycle
    forecast_track: str
    forecast_family: str
    plan_identity_checksum: str
    model_identity_checksum: str
    lineage_checksum: str
    immutable: bool = True
    lineage_complete: bool = True
    canonical_identity_unambiguous: bool = True
    postponed_before_start: bool = False
    rescheduled_from_occurrence_id: str | None = None
    suspended_original_occurrence: bool = False
    replacement_after_suspension: bool = False

    def __post_init__(self) -> None:
        for label, value in (
            ("game id", self.game_id),
            ("occurrence id", self.occurrence_id),
            ("forecast id", self.forecast_id),
            ("game type", self.game_type),
            ("evidence mode", self.evidence_mode),
            ("forecast track", self.forecast_track),
            ("forecast family", self.forecast_family),
        ):
            _require_text(value, label)
        _utc(self.generated_at, "forecast generated_at")
        _utc(self.scheduled_first_pitch, "scheduled_first_pitch")
        for label, value in (
            ("plan identity checksum", self.plan_identity_checksum),
            ("model identity checksum", self.model_identity_checksum),
            ("lineage checksum", self.lineage_checksum),
        ):
            _require_sha256(value, label)
        if self.rescheduled_from_occurrence_id == self.occurrence_id:
            raise ValueError("A rescheduled occurrence must have a new canonical occurrence id")
        if self.replacement_after_suspension and not self.suspended_original_occurrence:
            raise ValueError("A post-suspension replacement must identify a suspended game")

    @property
    def forecast_identity_checksum(self) -> str:
        return canonical_checksum(asdict(self))

    def to_record(self) -> dict[str, object]:
        """Return a JSON-safe record that preserves the exact assessed evidence."""

        return {
            "game_id": self.game_id,
            "occurrence_id": self.occurrence_id,
            "forecast_id": self.forecast_id,
            "generated_at": _utc(self.generated_at, "generated_at").isoformat(),
            "scheduled_first_pitch": _utc(
                self.scheduled_first_pitch, "scheduled_first_pitch"
            ).isoformat(),
            "season": self.season,
            "game_type": self.game_type,
            "evidence_mode": self.evidence_mode,
            "lifecycle": self.lifecycle.value,
            "forecast_track": self.forecast_track,
            "forecast_family": self.forecast_family,
            "plan_identity_checksum": self.plan_identity_checksum,
            "model_identity_checksum": self.model_identity_checksum,
            "lineage_checksum": self.lineage_checksum,
            "immutable": self.immutable,
            "lineage_complete": self.lineage_complete,
            "canonical_identity_unambiguous": self.canonical_identity_unambiguous,
            "postponed_before_start": self.postponed_before_start,
            "rescheduled_from_occurrence_id": self.rescheduled_from_occurrence_id,
            "suspended_original_occurrence": self.suspended_original_occurrence,
            "replacement_after_suspension": self.replacement_after_suspension,
        }

    @classmethod
    def from_record(cls, value: Mapping[str, object]) -> ProspectiveForecastSnapshot:
        """Restore and validate a JSON-safe assessed forecast record."""

        try:
            return cls(
                game_id=str(value["game_id"]),
                occurrence_id=str(value["occurrence_id"]),
                forecast_id=str(value["forecast_id"]),
                generated_at=datetime.fromisoformat(str(value["generated_at"])),
                scheduled_first_pitch=datetime.fromisoformat(
                    str(value["scheduled_first_pitch"])
                ),
                season=int(value["season"]),
                game_type=str(value["game_type"]),
                evidence_mode=str(value["evidence_mode"]),
                lifecycle=ForecastLifecycle(str(value["lifecycle"])),
                forecast_track=str(value["forecast_track"]),
                forecast_family=str(value["forecast_family"]),
                plan_identity_checksum=str(value["plan_identity_checksum"]),
                model_identity_checksum=str(value["model_identity_checksum"]),
                lineage_checksum=str(value["lineage_checksum"]),
                immutable=_strict_record_bool(value["immutable"], "immutable"),
                lineage_complete=_strict_record_bool(
                    value["lineage_complete"], "lineage_complete"
                ),
                canonical_identity_unambiguous=_strict_record_bool(
                    value["canonical_identity_unambiguous"],
                    "canonical_identity_unambiguous",
                ),
                postponed_before_start=_strict_record_bool(
                    value["postponed_before_start"], "postponed_before_start"
                ),
                rescheduled_from_occurrence_id=(
                    None
                    if value.get("rescheduled_from_occurrence_id") is None
                    else str(value["rescheduled_from_occurrence_id"])
                ),
                suspended_original_occurrence=_strict_record_bool(
                    value["suspended_original_occurrence"],
                    "suspended_original_occurrence",
                ),
                replacement_after_suspension=_strict_record_bool(
                    value["replacement_after_suspension"],
                    "replacement_after_suspension",
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Prospective forecast evidence record is invalid") from exc


def _strict_record_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


@dataclass(frozen=True, slots=True)
class EligibleProspectiveGame:
    game_id: str
    occurrence_id: str
    forecast_id: str
    forecast_identity_checksum: str
    forecast_generated_at: datetime
    scheduled_first_pitch: datetime
    plan_identity_checksum: str
    assessed_forecast: ProspectiveForecastSnapshot
    eligibility_policy_checksum: str
    eligibility_proof_checksum: str

    def __post_init__(self) -> None:
        for label, value in (
            ("game id", self.game_id),
            ("occurrence id", self.occurrence_id),
            ("forecast id", self.forecast_id),
        ):
            _require_text(value, label)
        _require_sha256(self.forecast_identity_checksum, "forecast identity checksum")
        _require_sha256(self.plan_identity_checksum, "plan identity checksum")
        _require_sha256(self.eligibility_policy_checksum, "eligibility policy checksum")
        _require_sha256(self.eligibility_proof_checksum, "eligibility proof checksum")
        if _utc(self.forecast_generated_at, "forecast_generated_at") >= _utc(
            self.scheduled_first_pitch, "scheduled_first_pitch"
        ):
            raise ValueError("An eligible prospective forecast must precede first pitch")
        forecast = self.assessed_forecast
        if (
            forecast.game_id != self.game_id
            or forecast.occurrence_id != self.occurrence_id
            or forecast.forecast_id != self.forecast_id
            or forecast.forecast_identity_checksum != self.forecast_identity_checksum
            or forecast.generated_at != self.forecast_generated_at
            or forecast.scheduled_first_pitch != self.scheduled_first_pitch
            or forecast.plan_identity_checksum != self.plan_identity_checksum
        ):
            raise ValueError("Eligibility proof does not match its assessed forecast")
        if self.eligibility_policy_checksum != ELIGIBILITY_POLICY.identity_checksum:
            raise ValueError("Eligibility proof uses an unknown policy identity")
        expected = _eligibility_proof_checksum(
            plan_identity_checksum=self.plan_identity_checksum,
            forecast=forecast,
        )
        if self.eligibility_proof_checksum != expected:
            raise ValueError("Eligibility proof checksum does not match its semantics")


def _eligibility_proof_checksum(
    *,
    plan_identity_checksum: str,
    forecast: ProspectiveForecastSnapshot,
) -> str:
    return canonical_checksum(
        {
            "schema_version": ELIGIBILITY_ASSESSMENT_SCHEMA_VERSION,
            "eligible": True,
            "plan_identity_checksum": plan_identity_checksum,
            "forecast_identity_checksum": forecast.forecast_identity_checksum,
            "eligibility_policy_checksum": ELIGIBILITY_POLICY.identity_checksum,
            "prospective_policy_checksum": PROSPECTIVE_POLICY_IDENTITY_CHECKSUM,
            "required_lifecycle": PRIMARY_PROSPECTIVE_LIFECYCLE,
            "forecast_lifecycle": ForecastLifecycle.T_MINUS_10,
            "forecast_track": GEN2_RESEARCH_FORECAST_TRACK,
            "forecast_family": ASP_INDEPENDENT_FORECAST_FAMILY,
        }
    )


@dataclass(frozen=True, slots=True)
class ForecastEligibilityAssessment:
    game_id: str
    occurrence_id: str | None
    binding: EligibleProspectiveGame | None
    exclusion_reasons: tuple[ProspectiveExclusionReason, ...]

    @property
    def eligible(self) -> bool:
        return self.binding is not None and not self.exclusion_reasons


def assess_forecast_eligibility(
    plan: ProspectiveEvaluationPlan,
    forecasts: Sequence[ProspectiveForecastSnapshot],
) -> ForecastEligibilityAssessment:
    """Apply the frozen forecast-only eligibility rules without reading labels."""

    if not forecasts:
        return ForecastEligibilityAssessment(
            game_id="UNKNOWN",
            occurrence_id=None,
            binding=None,
            exclusion_reasons=(ProspectiveExclusionReason.NO_GEN2_SCORE,),
        )
    game_ids = {item.game_id for item in forecasts}
    if len(game_ids) != 1:
        raise ValueError("One eligibility assessment cannot mix canonical game ids")
    game_id = forecasts[0].game_id
    primary = [
        item
        for item in forecasts
        if item.forecast_track == plan.forecast_track
        and item.lifecycle is plan.forecast_lifecycle
    ]
    if not primary:
        reasons: list[ProspectiveExclusionReason] = []
        if not any(item.forecast_track == plan.forecast_track for item in forecasts):
            reasons.append(ProspectiveExclusionReason.WRONG_FORECAST_TRACK)
        if not any(item.lifecycle is plan.forecast_lifecycle for item in forecasts):
            reasons.append(ProspectiveExclusionReason.WRONG_LIFECYCLE)
        if not reasons:
            reasons.append(ProspectiveExclusionReason.NO_GEN2_SCORE)
        return ForecastEligibilityAssessment(
            game_id=game_id,
            occurrence_id=forecasts[0].occurrence_id,
            binding=None,
            exclusion_reasons=tuple(reasons),
        )
    if len(primary) != 1:
        return ForecastEligibilityAssessment(
            game_id=game_id,
            occurrence_id=primary[0].occurrence_id,
            binding=None,
            exclusion_reasons=(ProspectiveExclusionReason.MULTIPLE_PRIMARY_FORECASTS,),
        )
    forecast = primary[0]
    reasons = []
    if forecast.forecast_family != plan.forecast_family:
        reasons.append(ProspectiveExclusionReason.WRONG_FORECAST_FAMILY)
    if forecast.season != plan.target_season:
        reasons.append(ProspectiveExclusionReason.WRONG_SEASON)
    if forecast.game_type != "R":
        reasons.append(ProspectiveExclusionReason.NON_REGULAR_SEASON)
    if forecast.evidence_mode != "LIVE":
        reasons.append(ProspectiveExclusionReason.NON_LIVE_EVIDENCE)
    if not forecast.canonical_identity_unambiguous:
        reasons.append(ProspectiveExclusionReason.AMBIGUOUS_GAME_IDENTITY)
    if not forecast.immutable:
        reasons.append(ProspectiveExclusionReason.MUTABLE_FORECAST)
    if not forecast.lineage_complete:
        reasons.append(ProspectiveExclusionReason.INCOMPLETE_LINEAGE)
    if _utc(forecast.generated_at, "generated_at") < _utc(
        plan.evaluation_starts_at, "evaluation_starts_at"
    ):
        reasons.append(ProspectiveExclusionReason.BEFORE_PLAN_WINDOW)
    if _utc(forecast.generated_at, "generated_at") >= _utc(
        forecast.scheduled_first_pitch, "scheduled_first_pitch"
    ):
        reasons.append(ProspectiveExclusionReason.LATE_OR_POST_START_SCORE)
    if (
        forecast.plan_identity_checksum != plan.plan_identity_checksum
        or forecast.model_identity_checksum != plan.model_identity.checksum
    ):
        reasons.append(ProspectiveExclusionReason.PLAN_OR_MODEL_MISMATCH)
    if forecast.postponed_before_start:
        reasons.append(ProspectiveExclusionReason.PRESTART_POSTPONED_OLD_OCCURRENCE)
    if forecast.replacement_after_suspension:
        reasons.append(ProspectiveExclusionReason.POST_SUSPENSION_REPLACEMENT)
    if reasons:
        return ForecastEligibilityAssessment(
            game_id=game_id,
            occurrence_id=forecast.occurrence_id,
            binding=None,
            exclusion_reasons=tuple(dict.fromkeys(reasons)),
        )
    return ForecastEligibilityAssessment(
        game_id=game_id,
        occurrence_id=forecast.occurrence_id,
        binding=EligibleProspectiveGame(
            game_id=forecast.game_id,
            occurrence_id=forecast.occurrence_id,
            forecast_id=forecast.forecast_id,
            forecast_identity_checksum=forecast.forecast_identity_checksum,
            forecast_generated_at=forecast.generated_at,
            scheduled_first_pitch=forecast.scheduled_first_pitch,
            plan_identity_checksum=forecast.plan_identity_checksum,
            assessed_forecast=forecast,
            eligibility_policy_checksum=ELIGIBILITY_POLICY.identity_checksum,
            eligibility_proof_checksum=_eligibility_proof_checksum(
                plan_identity_checksum=plan.plan_identity_checksum,
                forecast=forecast,
            ),
        ),
        exclusion_reasons=(),
    )


@dataclass(frozen=True, slots=True)
class TargetRevisionReference:
    """Opaque official-final revision metadata; this object has no labels."""

    game_id: str
    occurrence_id: str
    revision_id: str
    revision_checksum: str
    revision_sequence: int
    available_at: datetime
    official_final: bool = True

    def __post_init__(self) -> None:
        for label, value in (
            ("game id", self.game_id),
            ("occurrence id", self.occurrence_id),
            ("revision id", self.revision_id),
        ):
            _require_text(value, label)
        _require_sha256(self.revision_checksum, "target revision checksum")
        _utc(self.available_at, "target revision available_at")
        if self.revision_sequence < 0:
            raise ValueError("Target revision sequence cannot be negative")


@dataclass(frozen=True, slots=True)
class ClaimedTargetLabel:
    """Typed label envelope bound to one opaque target-revision reference."""

    game_id: str
    occurrence_id: str
    revision_id: str
    revision_checksum: str
    revision_sequence: int
    payload: object

    def __post_init__(self) -> None:
        for label, value in (
            ("game id", self.game_id),
            ("occurrence id", self.occurrence_id),
            ("revision id", self.revision_id),
        ):
            _require_text(value, label)
        _require_sha256(self.revision_checksum, "label revision checksum")
        if isinstance(self.revision_sequence, bool) or not isinstance(
            self.revision_sequence, int
        ):
            raise TypeError("Label revision sequence must be an integer")
        if self.revision_sequence < 0:
            raise ValueError("Label revision sequence cannot be negative")

    @property
    def payload_commitment_checksum(self) -> str:
        return target_revision_commitment_checksum(
            game_id=self.game_id,
            occurrence_id=self.occurrence_id,
            revision_id=self.revision_id,
            revision_sequence=self.revision_sequence,
            payload=self.payload,
        )

    @property
    def source_identity_checksum(self) -> str:
        return canonical_checksum(
            {
                "game_id": self.game_id,
                "occurrence_id": self.occurrence_id,
                "revision_id": self.revision_id,
                "revision_checksum": self.revision_checksum,
                "revision_sequence": self.revision_sequence,
            }
        )


def validate_claimed_target_labels(
    references: tuple[TargetRevisionReference, ...],
    labels: Sequence[ClaimedTargetLabel],
) -> tuple[ClaimedTargetLabel, ...]:
    """Require one exact, ordered typed label for every claimed target revision."""

    resolved = tuple(labels)
    if len(resolved) != len(references):
        raise ProspectiveLabelAccessError(
            "Target loader did not return exactly one label per claimed revision"
        )
    for index, (reference, label) in enumerate(zip(references, resolved, strict=True)):
        if not isinstance(label, ClaimedTargetLabel):
            raise ProspectiveLabelAccessError(
                f"Target loader result {index} is not a ClaimedTargetLabel"
            )
        expected = (
            reference.game_id,
            reference.occurrence_id,
            reference.revision_id,
            reference.revision_checksum,
            reference.revision_sequence,
        )
        actual = (
            label.game_id,
            label.occurrence_id,
            label.revision_id,
            label.revision_checksum,
            label.revision_sequence,
        )
        if actual != expected:
            raise ProspectiveLabelAccessError(
                f"Target loader result {index} does not match its claimed "
                "game/occurrence/revision/checksum"
            )
        if label.payload_commitment_checksum != reference.revision_checksum:
            raise ProspectiveLabelAccessError(
                f"Target loader result {index} payload does not match the claimed "
                "revision commitment"
            )
    return resolved


@dataclass(frozen=True, slots=True)
class ClaimedGameBinding:
    game: EligibleProspectiveGame
    target_revision: TargetRevisionReference
    schema_version: str = PROSPECTIVE_CLAIM_BINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            self.game.game_id != self.target_revision.game_id
            or self.game.occurrence_id != self.target_revision.occurrence_id
        ):
            raise ValueError("Forecast and target revision identities do not match")
        if not self.target_revision.official_final:
            raise ValueError("A prospective claim requires a governed official final")
        if self.schema_version != PROSPECTIVE_CLAIM_BINDING_SCHEMA_VERSION:
            raise ValueError("Prospective claim binding schema drifted")

    @property
    def binding_identity_checksum(self) -> str:
        return canonical_checksum(
            {
                "schema_version": self.schema_version,
                "plan_identity_checksum": self.game.plan_identity_checksum,
                "game_id": self.game.game_id,
                "occurrence_id": self.game.occurrence_id,
                "forecast_id": self.game.forecast_id,
                "forecast_identity_checksum": self.game.forecast_identity_checksum,
                "eligibility_policy_checksum": self.game.eligibility_policy_checksum,
                "eligibility_proof_checksum": self.game.eligibility_proof_checksum,
                "target_revision_id": self.target_revision.revision_id,
                "target_revision_checksum": self.target_revision.revision_checksum,
                "target_revision_sequence": self.target_revision.revision_sequence,
            }
        )


@dataclass(frozen=True, slots=True)
class ProspectiveEvaluationClaim:
    """A consumed plan/checkpoint key that authorizes one label read."""

    plan_identity_checksum: str
    checkpoint: ProspectiveCheckpoint
    claimed_at: datetime
    bindings: tuple[ClaimedGameBinding, ...]
    code_revision: str
    code_checksum: str
    schema_bundle_version: str
    schema_bundle_checksum: str
    schema_version: str = PROSPECTIVE_CLAIM_SCHEMA_VERSION
    status: str = "PROSPECTIVE_LABEL_ACCESS_AUTHORIZED"

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint, ProspectiveCheckpoint):
            raise ValueError("Prospective claim checkpoint must use the frozen vocabulary")
        _require_sha256(self.plan_identity_checksum, "plan identity checksum")
        _utc(self.claimed_at, "claimed_at")
        _require_text(self.code_revision, "code revision")
        _require_sha256(self.code_checksum, "code checksum")
        _require_text(self.schema_bundle_version, "schema bundle version")
        _require_sha256(self.schema_bundle_checksum, "schema bundle checksum")
        if self.schema_version != PROSPECTIVE_CLAIM_SCHEMA_VERSION:
            raise ValueError("Prospective claim schema drifted")
        if self.status != "PROSPECTIVE_LABEL_ACCESS_AUTHORIZED":
            raise ValueError("Unknown prospective claim status")
        if len(self.bindings) < self.checkpoint.minimum_completed_games:
            raise ValueError("Prospective claim does not meet its frozen checkpoint threshold")
        keys = tuple((item.game.game_id, item.game.occurrence_id) for item in self.bindings)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("Prospective claim bindings must be unique and canonically sorted")
        if any(item.game.plan_identity_checksum != self.plan_identity_checksum for item in self.bindings):
            raise ValueError("Prospective claim contains a forecast from another plan")
        if any(
            _utc(item.target_revision.available_at, "target available_at")
            > _utc(self.claimed_at, "claimed_at")
            for item in self.bindings
        ):
            raise ValueError("Prospective claim cannot bind a future target revision")

    @property
    def global_claim_identity_checksum(self) -> str:
        return canonical_checksum(
            {
                "schema_version": self.schema_version,
                "plan_identity_checksum": self.plan_identity_checksum,
                "checkpoint": self.checkpoint,
            }
        )

    @property
    def eligible_set_checksum(self) -> str:
        return canonical_checksum(
            [
                {
                    "game_id": item.game.game_id,
                    "occurrence_id": item.game.occurrence_id,
                    "forecast_identity_checksum": item.game.forecast_identity_checksum,
                    "eligibility_proof_checksum": item.game.eligibility_proof_checksum,
                    "target_revision_id": item.target_revision.revision_id,
                    "target_revision_checksum": item.target_revision.revision_checksum,
                    "binding_identity_checksum": item.binding_identity_checksum,
                }
                for item in self.bindings
            ]
        )

    @property
    def claim_identity_checksum(self) -> str:
        return canonical_checksum(
            {
                "schema_version": self.schema_version,
                "status": self.status,
                "global_claim_identity_checksum": self.global_claim_identity_checksum,
                "eligible_set_checksum": self.eligible_set_checksum,
                "claimed_at": _utc(self.claimed_at, "claimed_at"),
                "code_revision": self.code_revision,
                "code_checksum": self.code_checksum,
                "schema_bundle_version": self.schema_bundle_version,
                "schema_bundle_checksum": self.schema_bundle_checksum,
            }
        )


def _latest_revisions_at(
    *,
    games: Sequence[EligibleProspectiveGame],
    revisions: Iterable[TargetRevisionReference],
    claimed_at: datetime,
) -> tuple[ClaimedGameBinding, ...]:
    by_key: dict[tuple[str, str], list[TargetRevisionReference]] = {}
    claim_time = _utc(claimed_at, "claimed_at")
    for revision in revisions:
        if revision.official_final and _utc(revision.available_at, "available_at") <= claim_time:
            by_key.setdefault((revision.game_id, revision.occurrence_id), []).append(revision)
    bindings: list[ClaimedGameBinding] = []
    for game in sorted(games, key=lambda item: (item.game_id, item.occurrence_id)):
        candidates = by_key.get((game.game_id, game.occurrence_id), [])
        if not candidates:
            raise ProspectiveClaimError(
                f"Eligible game {game.game_id} has no governed official final at claim time"
            )
        highest_sequence = max(item.revision_sequence for item in candidates)
        latest = [item for item in candidates if item.revision_sequence == highest_sequence]
        identities = {(item.revision_id, item.revision_checksum) for item in latest}
        if len(identities) != 1:
            raise ProspectiveClaimError(
                f"Eligible game {game.game_id} has ambiguous latest target revisions"
            )
        selected = max(latest, key=lambda item: _utc(item.available_at, "available_at"))
        bindings.append(ClaimedGameBinding(game=game, target_revision=selected))
    return tuple(bindings)


@dataclass(frozen=True, slots=True)
class ProspectiveLabelAccessConsumption:
    """Append-only reservation written before one claim's labels are loaded."""

    plan_identity_checksum: str
    global_claim_identity_checksum: str
    claim_identity_checksum: str
    consumed_at: datetime
    code_revision: str
    code_checksum: str
    schema_bundle_version: str
    schema_bundle_checksum: str
    schema_version: str = PROSPECTIVE_LABEL_ACCESS_SCHEMA_VERSION
    status: str = "PROSPECTIVE_LABEL_ACCESS_CONSUMED"

    def __post_init__(self) -> None:
        for label, value in (
            ("plan identity checksum", self.plan_identity_checksum),
            ("global claim identity checksum", self.global_claim_identity_checksum),
            ("claim identity checksum", self.claim_identity_checksum),
            ("code checksum", self.code_checksum),
            ("schema bundle checksum", self.schema_bundle_checksum),
        ):
            _require_sha256(value, label)
        _utc(self.consumed_at, "consumed_at")
        _require_text(self.code_revision, "code revision")
        _require_text(self.schema_bundle_version, "schema bundle version")
        if (
            self.schema_version != PROSPECTIVE_LABEL_ACCESS_SCHEMA_VERSION
            or self.status != "PROSPECTIVE_LABEL_ACCESS_CONSUMED"
        ):
            raise ValueError("Prospective label-access schema or status drifted")

    @property
    def access_identity_checksum(self) -> str:
        return canonical_checksum(asdict(self))


class ProspectiveGovernanceLedger:
    """Append-only semantic ledger for plans and globally single-use claims.

    Durable database persistence may mirror this contract, but it must retain
    these identities and uniqueness semantics.  The ledger has intentionally no
    update/delete operation.
    """

    def __init__(self) -> None:
        self._plans: dict[str, ProspectiveEvaluationPlan] = {}
        self._claims: dict[str, ProspectiveEvaluationClaim] = {}
        self._claimed_game_plan: dict[tuple[str, str], str] = {}
        self._label_accesses: dict[str, ProspectiveLabelAccessConsumption] = {}

    @property
    def plans(self) -> tuple[ProspectiveEvaluationPlan, ...]:
        return tuple(self._plans.values())

    @property
    def claims(self) -> tuple[ProspectiveEvaluationClaim, ...]:
        return tuple(self._claims.values())

    @property
    def label_accesses(self) -> tuple[ProspectiveLabelAccessConsumption, ...]:
        return tuple(self._label_accesses.values())

    def register_plan(
        self,
        plan: ProspectiveEvaluationPlan,
        *,
        runtime: RuntimeIdentitySnapshot,
    ) -> ProspectiveEvaluationPlan:
        plan.require_current_runtime(runtime)
        identity = plan.plan_identity_checksum
        existing = self._plans.get(identity)
        if existing is not None:
            if existing != plan:
                raise ProspectiveGovernanceError("Prospective plan identity collision")
            return existing
        if any(item.plan_id == plan.plan_id for item in self._plans.values()):
            raise ProspectiveGovernanceError(
                "A prospective plan id is append-only and cannot be replaced"
            )
        self._plans[identity] = plan
        return plan

    def claim_checkpoint(
        self,
        *,
        plan_identity_checksum: str,
        checkpoint: ProspectiveCheckpoint,
        eligible_games: Sequence[EligibleProspectiveGame],
        target_revisions: Iterable[TargetRevisionReference],
        runtime: RuntimeIdentitySnapshot,
        claimed_at: datetime,
    ) -> ProspectiveEvaluationClaim:
        """Consume one plan/checkpoint key without dereferencing target values."""

        plan = self._plans.get(plan_identity_checksum)
        if plan is None:
            raise ProspectiveClaimError("Prospective plan is not registered")
        plan.require_current_runtime(runtime)
        claim_time = _utc(claimed_at, "claimed_at")
        global_identity = canonical_checksum(
            {
                "schema_version": PROSPECTIVE_CLAIM_SCHEMA_VERSION,
                "plan_identity_checksum": plan_identity_checksum,
                "checkpoint": checkpoint,
            }
        )
        if global_identity in self._claims:
            raise ProspectiveClaimError(
                "The exact prospective plan/checkpoint already has a consumed global claim"
            )
        final_key = canonical_checksum(
            {
                "schema_version": PROSPECTIVE_CLAIM_SCHEMA_VERSION,
                "plan_identity_checksum": plan_identity_checksum,
                "checkpoint": ProspectiveCheckpoint.FINAL_500,
            }
        )
        if checkpoint is ProspectiveCheckpoint.INTERIM_300 and final_key in self._claims:
            raise ProspectiveClaimError("The final claim already consumed this plan window")
        if len(eligible_games) < checkpoint.minimum_completed_games:
            raise ProspectiveClaimError(
                f"{checkpoint.value} requires at least {checkpoint.minimum_completed_games} "
                "completed eligible games"
            )
        game_keys = tuple((item.game_id, item.occurrence_id) for item in eligible_games)
        if len(set(game_keys)) != len(game_keys):
            raise ProspectiveClaimError("Prospective eligible games must be unique")
        if any(item.plan_identity_checksum != plan_identity_checksum for item in eligible_games):
            raise ProspectiveClaimError("Prospective eligible set contains another plan")
        for game in eligible_games:
            reassessed = assess_forecast_eligibility(plan, (game.assessed_forecast,))
            if not reassessed.eligible or reassessed.binding != game:
                raise ProspectiveClaimError(
                    f"Eligible game {game.game_id} lacks an exact verified eligibility proof"
                )
        reused = [
            key
            for key in game_keys
            if key in self._claimed_game_plan
            and self._claimed_game_plan[key] != plan_identity_checksum
        ]
        if reused:
            raise ProspectiveClaimError(
                "Prospective games already claimed by another model/window cannot be reused"
            )
        bindings = _latest_revisions_at(
            games=eligible_games,
            revisions=target_revisions,
            claimed_at=claim_time,
        )
        claim = ProspectiveEvaluationClaim(
            plan_identity_checksum=plan_identity_checksum,
            checkpoint=checkpoint,
            claimed_at=claim_time,
            bindings=bindings,
            code_revision=runtime.code_revision,
            code_checksum=runtime.code_checksum,
            schema_bundle_version=runtime.schema_bundle_version,
            schema_bundle_checksum=runtime.schema_bundle_checksum,
        )
        if claim.global_claim_identity_checksum != global_identity:
            raise ProspectiveClaimError("Prospective global claim identity mismatch")
        # All validation precedes these append-only writes.
        self._claims[global_identity] = claim
        for key in game_keys:
            self._claimed_game_plan.setdefault(key, plan_identity_checksum)
        return claim

    def read_claimed_labels(
        self,
        claim: ProspectiveEvaluationClaim,
        *,
        runtime: RuntimeIdentitySnapshot,
        loader: Callable[
            [tuple[TargetRevisionReference, ...]], Sequence[ClaimedTargetLabel]
        ],
        consumed_at: datetime | None = None,
    ) -> tuple[ClaimedTargetLabel, ...]:
        """Invoke ``loader`` only after claim, integrity, and drift validation."""

        stored = self._claims.get(claim.global_claim_identity_checksum)
        if stored is None or stored != claim:
            raise ProspectiveLabelAccessError(
                "Prospective labels require the exact stored single-use claim"
            )
        if stored.claim_identity_checksum != claim.claim_identity_checksum:
            raise ProspectiveLabelAccessError("Prospective claim integrity check failed")
        plan = self._plans.get(claim.plan_identity_checksum)
        if plan is None:
            raise ProspectiveLabelAccessError("Prospective claim has no registered plan")
        try:
            plan.require_current_runtime(runtime)
        except ProspectivePlanDriftError as exc:
            raise ProspectiveLabelAccessError(
                "Prospective plan drift blocks label access"
            ) from exc
        if claim.global_claim_identity_checksum in self._label_accesses:
            raise ProspectiveLabelAccessError(
                "Prospective label access is globally single-use and already consumed"
            )
        consumption = ProspectiveLabelAccessConsumption(
            plan_identity_checksum=claim.plan_identity_checksum,
            global_claim_identity_checksum=claim.global_claim_identity_checksum,
            claim_identity_checksum=claim.claim_identity_checksum,
            consumed_at=consumed_at or datetime.now(UTC),
            code_revision=runtime.code_revision,
            code_checksum=runtime.code_checksum,
            schema_bundle_version=runtime.schema_bundle_version,
            schema_bundle_checksum=runtime.schema_bundle_checksum,
        )
        # Consume before invoking the loader. A loader failure cannot be retried
        # after labels may have been observed.
        self._label_accesses[claim.global_claim_identity_checksum] = consumption
        references = tuple(item.target_revision for item in claim.bindings)
        return validate_claimed_target_labels(references, loader(references))


@dataclass(frozen=True, slots=True)
class ProspectiveDecisionRecord:
    plan_identity_checksum: str
    claim_identity_checksum: str
    checkpoint: ProspectiveCheckpoint
    decision: Gen2ResearchDecision
    completed_eligible_games: int
    decided_at: datetime
    schema_version: str = PROSPECTIVE_DECISION_SCHEMA_VERSION
    official_positions_enabled: bool = False
    production_promotion_authorized: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint, ProspectiveCheckpoint):
            raise ValueError("Prospective decision checkpoint must use the frozen vocabulary")
        if not isinstance(self.decision, Gen2ResearchDecision):
            raise ValueError("Prospective decision must be a Gate 3C research decision")
        _require_sha256(self.plan_identity_checksum, "plan identity checksum")
        _require_sha256(self.claim_identity_checksum, "claim identity checksum")
        _utc(self.decided_at, "decided_at")
        if self.schema_version != PROSPECTIVE_DECISION_SCHEMA_VERSION:
            raise ValueError("Prospective decision schema drifted")
        if self.completed_eligible_games < self.checkpoint.minimum_completed_games:
            raise ValueError("Prospective decision is below its declared checkpoint threshold")
        if self.official_positions_enabled or self.production_promotion_authorized:
            raise ValueError(
                "Gate 3C prospective decisions cannot enable positions or production promotion"
            )

    @property
    def decision_identity_checksum(self) -> str:
        return canonical_checksum(asdict(self))


def require_gen2_research_decision(value: str | Gen2ResearchDecision) -> Gen2ResearchDecision:
    """Accept only the three Gate 3C research decisions; never a production status."""

    try:
        return Gen2ResearchDecision(value)
    except ValueError as exc:
        raise ValueError(
            "Gate 3C decisions are limited to GEN2_RESEARCH_CANDIDATE, "
            "GEN2_RESEARCH_CHALLENGER, or GEN2_REJECTED"
        ) from exc


def validate_code_revision(value: str) -> str:
    """Optional strict helper for callers that use a Git hexadecimal revision."""

    if not isinstance(value, str) or not _HEX_REVISION.fullmatch(value):
        raise ValueError("code revision must be 7-64 lowercase hexadecimal characters")
    return value
