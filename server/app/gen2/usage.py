"""Provider-free Gen-2 starter-duration and bullpen-usage primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
import json
from math import exp, isfinite
from typing import Iterable, Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression


STARTER_USAGE_VERSION = "mlb_gen2_starter_exit_hazard_v1"
BULLPEN_USAGE_VERSION = "mlb_gen2_bullpen_weighted_pool_v1"


class UsageError(ValueError):
    """Raised when usage evidence violates the governed contract."""


class PitchingRole(StrEnum):
    STARTER = "STARTER"
    RELIEVER = "RELIEVER"


class BullpenRole(StrEnum):
    CLOSER = "CLOSER"
    SETUP = "SETUP"
    MIDDLE = "MIDDLE"
    LONG = "LONG"
    UNKNOWN = "UNKNOWN"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class AvailabilityBasis(StrEnum):
    WORKLOAD_DERIVED_AVAILABILITY_PROXY = "WORKLOAD_DERIVED_AVAILABILITY_PROXY"
    OBSERVED_AVAILABLE = "OBSERVED_AVAILABLE"
    OBSERVED_UNAVAILABLE = "OBSERVED_UNAVAILABLE"


@dataclass(frozen=True)
class PitchingAppearance:
    appearance_id: str
    game_id: str
    player_id: str
    team_id: str
    role: PitchingRole
    official_date: date
    completed_at: datetime
    eligible_at: datetime
    pitches: int
    batters_faced: int
    outs_recorded: int

    def __post_init__(self) -> None:
        if not all((self.appearance_id, self.game_id, self.player_id, self.team_id)):
            raise UsageError("Pitching appearance identities are required")
        workloads = (self.pitches, self.batters_faced, self.outs_recorded)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in workloads):
            raise UsageError("Pitching workloads must be integer counts")
        if min(workloads) < 0:
            raise UsageError("Pitching workloads must be non-negative")
        if not isinstance(self.role, PitchingRole):
            raise UsageError("Pitching appearance role is invalid")
        if _utc(self.eligible_at) < _utc(self.completed_at):
            raise UsageError("Pitching appearance cannot be eligible before completion")


@dataclass(frozen=True)
class AvailabilityObservation:
    observation_id: str
    player_id: str
    team_id: str
    status: AvailabilityStatus
    observed_at: datetime
    available_as_of: datetime
    source: str
    applicable_game_id: str | None
    effective_through: datetime | None
    evidence_checksum: str

    def __post_init__(self) -> None:
        if not all((self.observation_id, self.player_id, self.team_id, self.source)):
            raise UsageError("Availability observation identities and source are required")
        if not isinstance(self.status, AvailabilityStatus):
            raise UsageError("Availability observation status is invalid")
        if _utc(self.available_as_of) < _utc(self.observed_at):
            raise UsageError("Availability cannot be available before observation")
        if self.applicable_game_id is not None and not self.applicable_game_id:
            raise UsageError("Availability applicable_game_id cannot be blank")
        if self.effective_through is not None and _utc(self.effective_through) <= _utc(
            self.available_as_of
        ):
            raise UsageError("Availability effective_through must follow availability")
        _validate_lineage(self.evidence_checksum, (self.observation_id,), "availability")


@dataclass(frozen=True)
class RelieverProfile:
    player_id: str
    team_id: str
    role: BullpenRole
    talent_score: float
    reliability: float
    evidence_cutoff: datetime
    evidence_checksum: str
    source_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.player_id or not self.team_id:
            raise UsageError("Reliever profile identities are required")
        if not isinstance(self.role, BullpenRole):
            raise UsageError("Reliever profile role is invalid")
        _unit(self.talent_score, "talent_score")
        _unit(self.reliability, "reliability")
        _utc(self.evidence_cutoff)
        _validate_lineage(
            self.evidence_checksum,
            self.source_evidence_ids,
            "reliever profile",
        )


@dataclass(frozen=True)
class RelieverAvailabilitySnapshot:
    player_id: str
    team_id: str
    cutoff: datetime
    target_official_date: date
    availability_probability: float
    basis: AvailabilityBasis
    pitches_1d: int
    pitches_2d: int
    pitches_3d: int
    appearances_3d: int
    consecutive_days: int
    contributing_appearance_ids: tuple[str, ...]
    observation_id: str | None
    evidence_checksum: str


@dataclass(frozen=True)
class BullpenPoolEntry:
    player_id: str
    appearance_probability: float
    expected_batters_faced: float
    availability: RelieverAvailabilitySnapshot


@dataclass(frozen=True)
class BullpenUsagePrediction:
    team_id: str
    inning: int
    score_differential: int
    entries: tuple[BullpenPoolEntry, ...]
    evidence_checksum: str
    model_version: str = BULLPEN_USAGE_VERSION


@dataclass(frozen=True)
class BullpenUsageConfig:
    pitch_penalty_1d: float = 0.012
    pitch_penalty_3d: float = 0.0025
    appearance_penalty_3d: float = 0.07
    consecutive_day_penalty: float = 0.11
    minimum_proxy_probability: float = 0.05
    maximum_proxy_probability: float = 0.98
    maximum_observation_age_hours: float = 24.0

    def __post_init__(self) -> None:
        values = (
            self.pitch_penalty_1d,
            self.pitch_penalty_3d,
            self.appearance_penalty_3d,
            self.consecutive_day_penalty,
            self.minimum_proxy_probability,
            self.maximum_proxy_probability,
            self.maximum_observation_age_hours,
        )
        if any(not isfinite(value) for value in values):
            raise UsageError("Bullpen availability configuration must be finite")
        if any(value < 0 for value in values[:4]):
            raise UsageError("Bullpen workload penalties must be non-negative")
        if not 0 <= self.minimum_proxy_probability <= self.maximum_proxy_probability <= 1:
            raise UsageError("Bullpen availability probability bounds are invalid")
        if self.maximum_observation_age_hours <= 0:
            raise UsageError("Bullpen observation age bound must be positive")


@dataclass(frozen=True)
class StarterHazardExample:
    example_id: str
    game_id: str
    starter_id: str
    evidence_end_at: datetime
    eligible_at: datetime
    inning: int
    batters_faced_so_far: int
    outs_recorded_so_far: int
    pitches_so_far: int
    days_rest: float
    prior_7d_pitches: float
    times_through_order: float
    runs_allowed: int
    exited_after_opportunity: bool
    source_evidence_ids: tuple[str, ...]
    evidence_checksum: str
    evidence_tier: str = "A"


@dataclass(frozen=True)
class StarterUsageContext:
    inning: int
    batters_faced_so_far: int
    outs_recorded_so_far: int
    pitches_so_far: int
    days_rest: float
    prior_7d_pitches: float
    times_through_order: float
    runs_allowed: int


@dataclass(frozen=True)
class StarterExitHazardModel:
    intercept: float
    coefficients: tuple[float, ...]
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]
    training_example_ids: tuple[str, ...]
    training_checksum: str
    fit_cutoff: datetime
    model_version: str = STARTER_USAGE_VERSION

    def exit_probability(self, context: StarterUsageContext) -> float:
        values = _starter_values(context)
        standardized = [
            (value - mean) / scale
            for value, mean, scale in zip(values, self.feature_means, self.feature_scales, strict=True)
        ]
        logit = self.intercept + sum(
            coefficient * value
            for coefficient, value in zip(self.coefficients, standardized, strict=True)
        )
        probability = _sigmoid(logit)
        return min(1.0 - 1e-9, max(1e-9, probability))


@dataclass(frozen=True)
class StarterDurationPrediction:
    expected_batters_faced: float
    expected_outs: float
    exit_after_batter_probabilities: tuple[tuple[int, float], ...]
    reach_inning_probabilities: tuple[tuple[int, float], ...]
    model_training_checksum: str
    input_checksum: str
    checksum: str
    model_version: str = STARTER_USAGE_VERSION


def derive_reliever_availability(
    appearances: Iterable[PitchingAppearance],
    observations: Iterable[AvailabilityObservation],
    *,
    player_id: str,
    team_id: str,
    current_game_id: str,
    cutoff: datetime,
    target_official_date: date,
    config: BullpenUsageConfig = BullpenUsageConfig(),
) -> RelieverAvailabilitySnapshot:
    """Derive point-in-time availability without claiming manager confirmation."""

    if not all((player_id, team_id, current_game_id)):
        raise UsageError("Availability identities are required")
    boundary = _utc(cutoff)
    rows = tuple(appearances)
    _unique((row.appearance_id for row in rows), "appearance")
    eligible = [
        row
        for row in rows
        if row.player_id == player_id
        and row.team_id == team_id
        and row.role is PitchingRole.RELIEVER
        and row.game_id != current_game_id
        and _utc(row.completed_at) < boundary
        and _utc(row.eligible_at) <= boundary
        and row.official_date < target_official_date
    ]
    eligible.sort(key=lambda row: (_utc(row.completed_at), row.appearance_id))

    observation_rows = tuple(observations)
    _unique((row.observation_id for row in observation_rows), "availability observation")
    observed = [
        row
        for row in observation_rows
        if row.player_id == player_id
        and row.team_id == team_id
        and _utc(row.observed_at) <= boundary
        and _utc(row.available_as_of) <= boundary
        and (
            row.applicable_game_id is None or row.applicable_game_id == current_game_id
        )
        and (
            row.effective_through is None or boundary < _utc(row.effective_through)
        )
        and boundary - _utc(row.available_as_of)
        <= timedelta(hours=config.maximum_observation_age_hours)
        and row.status is not AvailabilityStatus.UNKNOWN
    ]
    latest_observed = max(observed, key=lambda row: (_utc(row.available_as_of), row.observation_id), default=None)

    pitches_1d = _window_sum(eligible, target_official_date, 1, "pitches")
    pitches_2d = _window_sum(eligible, target_official_date, 2, "pitches")
    pitches_3d = _window_sum(eligible, target_official_date, 3, "pitches")
    appearances_3d = sum(
        1 for row in eligible if row.official_date >= target_official_date - timedelta(days=3)
    )
    consecutive = _consecutive_days({row.official_date for row in eligible}, target_official_date)

    if latest_observed is not None and latest_observed.status is AvailabilityStatus.AVAILABLE:
        probability = 1.0
        basis = AvailabilityBasis.OBSERVED_AVAILABLE
    elif latest_observed is not None and latest_observed.status is AvailabilityStatus.UNAVAILABLE:
        probability = 0.0
        basis = AvailabilityBasis.OBSERVED_UNAVAILABLE
    else:
        raw = (
            1.0
            - config.pitch_penalty_1d * pitches_1d
            - config.pitch_penalty_3d * pitches_3d
            - config.appearance_penalty_3d * appearances_3d
            - config.consecutive_day_penalty * consecutive
        )
        probability = min(config.maximum_proxy_probability, max(config.minimum_proxy_probability, raw))
        basis = AvailabilityBasis.WORKLOAD_DERIVED_AVAILABILITY_PROXY

    snapshot_payload = {
        "version": BULLPEN_USAGE_VERSION,
        "player_id": player_id,
        "team_id": team_id,
        "current_game_id": current_game_id,
        "cutoff": boundary.isoformat(),
        "target_official_date": target_official_date.isoformat(),
        "appearance_ids": [row.appearance_id for row in eligible],
        "appearance_rows": [
            {
                "appearance_id": row.appearance_id,
                "game_id": row.game_id,
                "official_date": row.official_date.isoformat(),
                "completed_at": _utc(row.completed_at).isoformat(),
                "eligible_at": _utc(row.eligible_at).isoformat(),
                "pitches": row.pitches,
                "batters_faced": row.batters_faced,
                "outs_recorded": row.outs_recorded,
            }
            for row in eligible
        ],
        "observation": (
            {
                "observation_id": latest_observed.observation_id,
                "status": latest_observed.status.value,
                "observed_at": _utc(latest_observed.observed_at).isoformat(),
                "available_as_of": _utc(latest_observed.available_as_of).isoformat(),
                "source": latest_observed.source,
                "applicable_game_id": latest_observed.applicable_game_id,
                "effective_through": (
                    _utc(latest_observed.effective_through).isoformat()
                    if latest_observed.effective_through
                    else None
                ),
                "evidence_checksum": latest_observed.evidence_checksum,
            }
            if latest_observed is not None
            else None
        ),
        "config": config.__dict__,
        "probability": float(probability),
        "basis": basis.value,
    }
    return RelieverAvailabilitySnapshot(
        player_id=player_id,
        team_id=team_id,
        cutoff=boundary,
        target_official_date=target_official_date,
        availability_probability=float(probability),
        basis=basis,
        pitches_1d=pitches_1d,
        pitches_2d=pitches_2d,
        pitches_3d=pitches_3d,
        appearances_3d=appearances_3d,
        consecutive_days=consecutive,
        contributing_appearance_ids=tuple(row.appearance_id for row in eligible),
        observation_id=latest_observed.observation_id if latest_observed else None,
        evidence_checksum=_checksum(snapshot_payload),
    )


def predict_bullpen_usage(
    profiles: Iterable[RelieverProfile],
    availability: Mapping[str, RelieverAvailabilitySnapshot],
    *,
    team_id: str,
    inning: int,
    score_differential: int,
    cutoff: datetime,
    target_official_date: date,
) -> BullpenUsagePrediction:
    """Return a bounded role-weighted available reliever pool."""

    if inning < 1:
        raise UsageError("Inning must be positive")
    boundary = _utc(cutoff)
    rows = tuple(profile for profile in profiles if profile.team_id == team_id)
    _unique((row.player_id for row in rows), "reliever profile")
    weighted: list[tuple[RelieverProfile, RelieverAvailabilitySnapshot, float]] = []
    for profile in rows:
        if _utc(profile.evidence_cutoff) > boundary:
            raise UsageError(f"Future reliever profile evidence for {profile.player_id}")
        snapshot = availability.get(profile.player_id)
        if snapshot is None or snapshot.team_id != team_id:
            raise UsageError(f"Missing same-team availability for reliever {profile.player_id}")
        if snapshot.cutoff != boundary or snapshot.target_official_date != target_official_date:
            raise UsageError(f"Mismatched availability cutoff for reliever {profile.player_id}")
        if snapshot.availability_probability <= 0:
            continue
        role_weight = _role_weight(profile.role, inning, score_differential)
        weight = (
            snapshot.availability_probability
            * role_weight
            * (0.5 + profile.talent_score)
            * (0.5 + 0.5 * profile.reliability)
        )
        if weight > 0:
            weighted.append((profile, snapshot, weight))
    total = sum(item[2] for item in weighted)
    if not weighted or not isfinite(total) or total <= 0:
        raise UsageError("No eligible reliever pool is available")

    entries = tuple(
        BullpenPoolEntry(
            player_id=profile.player_id,
            appearance_probability=weight / total,
            expected_batters_faced=_expected_batters(profile.role, inning),
            availability=snapshot,
        )
        for profile, snapshot, weight in sorted(weighted, key=lambda item: item[0].player_id)
    )
    if abs(sum(item.appearance_probability for item in entries) - 1.0) > 1e-12:
        raise UsageError("Bullpen usage probabilities do not sum to one")
    prediction_payload = {
        "version": BULLPEN_USAGE_VERSION,
        "team_id": team_id,
        "inning": inning,
        "score_differential": score_differential,
        "cutoff": boundary.isoformat(),
        "target_official_date": target_official_date.isoformat(),
        "profiles": [
            {
                "player_id": profile.player_id,
                "role": profile.role.value,
                "talent_score": profile.talent_score,
                "reliability": profile.reliability,
                "evidence_cutoff": _utc(profile.evidence_cutoff).isoformat(),
                "evidence_checksum": profile.evidence_checksum,
                "source_evidence_ids": sorted(profile.source_evidence_ids),
                "availability_checksum": snapshot.evidence_checksum,
                "weight": weight,
            }
            for profile, snapshot, weight in sorted(weighted, key=lambda item: item[0].player_id)
        ],
    }
    return BullpenUsagePrediction(
        team_id=team_id,
        inning=inning,
        score_differential=score_differential,
        entries=entries,
        evidence_checksum=_checksum(prediction_payload),
    )


def fit_starter_exit_hazard(
    examples: Iterable[StarterHazardExample],
    *,
    cutoff: datetime,
    current_game_id: str,
    minimum_examples: int = 20,
) -> StarterExitHazardModel:
    """Fit a chronological, fold-local discrete-time starter exit hazard."""

    boundary = _utc(cutoff)
    if not current_game_id:
        raise UsageError("current_game_id is required")
    rows = tuple(examples)
    _unique((row.example_id for row in rows), "starter hazard example")
    selected: list[StarterHazardExample] = []
    for row in rows:
        _validate_hazard_example(row)
        if (
            row.game_id != current_game_id
            and _utc(row.evidence_end_at) < boundary
            and _utc(row.eligible_at) <= boundary
            and row.evidence_tier == "A"
        ):
            selected.append(row)
    selected.sort(key=lambda row: (_utc(row.evidence_end_at), row.example_id))
    if len(selected) < minimum_examples:
        raise UsageError("Starter hazard training sample is below the declared minimum")
    labels = np.asarray([int(row.exited_after_opportunity) for row in selected], dtype=int)
    if len(set(labels.tolist())) != 2:
        raise UsageError("Starter hazard training requires exit and continue examples")
    matrix = np.asarray([_starter_values(row) for row in selected], dtype=float)
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales[scales == 0] = 1.0
    standardized = (matrix - means) / scales
    estimator = LogisticRegression(C=1.0, max_iter=2_000, random_state=0, solver="lbfgs")
    estimator.fit(standardized, labels)
    identifiers = tuple(row.example_id for row in selected)
    payload = {
        "version": STARTER_USAGE_VERSION,
        "fit_cutoff": boundary.isoformat(),
        "examples": [
            {
                "example_id": row.example_id,
                "game_id": row.game_id,
                "starter_id": row.starter_id,
                "evidence_end_at": _utc(row.evidence_end_at).isoformat(),
                "eligible_at": _utc(row.eligible_at).isoformat(),
                "features": list(_starter_values(row)),
                "exited_after_opportunity": row.exited_after_opportunity,
                "source_evidence_ids": row.source_evidence_ids,
                "evidence_checksum": row.evidence_checksum,
                "evidence_tier": row.evidence_tier,
            }
            for row in selected
        ],
    }
    return StarterExitHazardModel(
        intercept=float(estimator.intercept_[0]),
        coefficients=tuple(float(value) for value in estimator.coef_[0]),
        feature_means=tuple(float(value) for value in means),
        feature_scales=tuple(float(value) for value in scales),
        training_example_ids=identifiers,
        training_checksum=_checksum(payload),
        fit_cutoff=boundary,
    )


def predict_starter_duration(
    model: StarterExitHazardModel,
    future_opportunities: Sequence[StarterUsageContext],
) -> StarterDurationPrediction:
    """Convert conditional exit hazards into a normalized duration distribution."""

    if not future_opportunities:
        raise UsageError("At least one future starter opportunity is required")
    previous_batters = -1
    previous_inning = -1
    previous_outs = -1
    previous_pitches = -1
    survival = 1.0
    masses: list[tuple[int, float]] = []
    inning_reach: dict[int, float] = {}
    for index, context in enumerate(future_opportunities):
        _validate_context(context)
        if context.batters_faced_so_far <= previous_batters:
            raise UsageError("Starter opportunities must increase batters faced")
        if (
            context.inning < previous_inning
            or context.outs_recorded_so_far < previous_outs
            or context.pitches_so_far < previous_pitches
        ):
            raise UsageError("Starter opportunity state cannot regress")
        previous_batters = context.batters_faced_so_far
        previous_inning = context.inning
        previous_outs = context.outs_recorded_so_far
        previous_pitches = context.pitches_so_far
        inning_reach.setdefault(context.inning, survival)
        hazard = 1.0 if index == len(future_opportunities) - 1 else model.exit_probability(context)
        mass = survival * hazard
        masses.append((context.batters_faced_so_far, mass))
        survival *= 1.0 - hazard
    if abs(sum(value for _, value in masses) - 1.0) > 1e-10:
        raise UsageError("Starter duration distribution does not sum to one")
    context_by_batters = {row.batters_faced_so_far: row for row in future_opportunities}
    expected_batters = sum(batters * probability for batters, probability in masses)
    expected_outs = sum(
        context_by_batters[batters].outs_recorded_so_far * probability
        for batters, probability in masses
    )
    input_payload = [
        {
            "inning": row.inning,
            "batters_faced_so_far": row.batters_faced_so_far,
            "outs_recorded_so_far": row.outs_recorded_so_far,
            "pitches_so_far": row.pitches_so_far,
            "days_rest": row.days_rest,
            "prior_7d_pitches": row.prior_7d_pitches,
            "times_through_order": row.times_through_order,
            "runs_allowed": row.runs_allowed,
        }
        for row in future_opportunities
    ]
    input_checksum = _checksum(input_payload)
    output_payload = {
        "version": STARTER_USAGE_VERSION,
        "model_training_checksum": model.training_checksum,
        "input_checksum": input_checksum,
        "expected_batters_faced": expected_batters,
        "expected_outs": expected_outs,
        "exit_probabilities": masses,
        "reach_inning_probabilities": sorted(inning_reach.items()),
    }
    return StarterDurationPrediction(
        expected_batters_faced=expected_batters,
        expected_outs=expected_outs,
        exit_after_batter_probabilities=tuple(masses),
        reach_inning_probabilities=tuple(sorted(inning_reach.items())),
        model_training_checksum=model.training_checksum,
        input_checksum=input_checksum,
        checksum=_checksum(output_payload),
    )


def _starter_values(row: StarterHazardExample | StarterUsageContext) -> tuple[float, ...]:
    return (
        float(row.inning),
        float(row.batters_faced_so_far),
        float(row.outs_recorded_so_far),
        float(row.pitches_so_far),
        float(row.days_rest),
        float(row.prior_7d_pitches),
        float(row.times_through_order),
        float(row.runs_allowed),
    )


def _validate_hazard_example(row: StarterHazardExample) -> None:
    if not row.example_id or not row.game_id or not row.starter_id:
        raise UsageError("Starter hazard example identities are required")
    if _utc(row.eligible_at) < _utc(row.evidence_end_at):
        raise UsageError("Starter hazard example cannot be eligible before evidence ends")
    if not isinstance(row.exited_after_opportunity, bool):
        raise UsageError("Starter hazard label must be boolean")
    if row.evidence_tier != "A":
        raise UsageError("Starter hazard accepts Tier A evidence only")
    _validate_lineage(
        row.evidence_checksum,
        row.source_evidence_ids,
        "starter hazard",
    )
    _validate_context(
        StarterUsageContext(
            inning=row.inning,
            batters_faced_so_far=row.batters_faced_so_far,
            outs_recorded_so_far=row.outs_recorded_so_far,
            pitches_so_far=row.pitches_so_far,
            days_rest=row.days_rest,
            prior_7d_pitches=row.prior_7d_pitches,
            times_through_order=row.times_through_order,
            runs_allowed=row.runs_allowed,
        )
    )


def _validate_context(row: StarterUsageContext) -> None:
    values = _starter_values(row)
    if any(not isfinite(value) or value < 0 for value in values):
        raise UsageError("Starter usage features must be finite and non-negative")
    if row.inning < 1:
        raise UsageError("Starter usage inning must be positive")


def _window_sum(rows: Sequence[PitchingAppearance], target_date: date, days: int, field: str) -> int:
    lower = target_date - timedelta(days=days)
    return sum(int(getattr(row, field)) for row in rows if lower <= row.official_date < target_date)


def _consecutive_days(appearance_dates: set[date], target_date: date) -> int:
    count = 0
    cursor = target_date - timedelta(days=1)
    while cursor in appearance_dates:
        count += 1
        cursor -= timedelta(days=1)
    return count


def _role_weight(role: BullpenRole, inning: int, differential: int) -> float:
    close = abs(differential) <= 3
    if role is BullpenRole.CLOSER:
        return 3.0 if inning >= 9 and close else 0.35
    if role is BullpenRole.SETUP:
        return 2.2 if inning >= 7 and close else 0.75
    if role is BullpenRole.LONG:
        return 2.2 if inning <= 6 or differential <= -4 else 0.6
    if role is BullpenRole.MIDDLE:
        return 1.4 if 5 <= inning <= 8 else 0.9
    return 1.0


def _expected_batters(role: BullpenRole, inning: int) -> float:
    if role is BullpenRole.LONG and inning <= 6:
        return 7.0
    if role in {BullpenRole.CLOSER, BullpenRole.SETUP}:
        return 3.5
    return 4.5


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + exp(-value))
    exp_value = exp(value)
    return exp_value / (1.0 + exp_value)


def _unit(value: float, name: str) -> None:
    if not isfinite(value) or not 0 <= value <= 1:
        raise UsageError(f"{name} must be finite and within 0..1")


def _unique(values: Iterable[str], name: str) -> None:
    rows = tuple(values)
    if any(not value for value in rows) or len(rows) != len(set(rows)):
        raise UsageError(f"{name} identities must be nonempty and unique")


def _validate_lineage(checksum: str, source_ids: tuple[str, ...], name: str) -> None:
    if len(checksum) != 64 or any(
        character not in "0123456789abcdef" for character in checksum.lower()
    ):
        raise UsageError(f"{name} checksum must be SHA-256")
    _unique(source_ids, f"{name} source evidence")


def _checksum(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise UsageError("Usage timestamps must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "AvailabilityBasis",
    "AvailabilityObservation",
    "AvailabilityStatus",
    "BULLPEN_USAGE_VERSION",
    "BullpenPoolEntry",
    "BullpenRole",
    "BullpenUsageConfig",
    "BullpenUsagePrediction",
    "PitchingAppearance",
    "PitchingRole",
    "RelieverAvailabilitySnapshot",
    "RelieverProfile",
    "STARTER_USAGE_VERSION",
    "StarterDurationPrediction",
    "StarterExitHazardModel",
    "StarterHazardExample",
    "StarterUsageContext",
    "UsageError",
    "derive_reliever_availability",
    "fit_starter_exit_hazard",
    "predict_bullpen_usage",
    "predict_starter_duration",
]
