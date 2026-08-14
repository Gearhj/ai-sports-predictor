"""Chronological player-state variants for controlled Generation 2 research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Iterable

from app.gen2.talent import PLAYER_IDENTITY_SCOPE, deterministic_checksum


HALF_LIFE_CANDIDATES_DAYS = (14, 30, 60)


class DynamicStateVariant(StrEnum):
    STATIC_TRUE_TALENT = "static_true_talent"
    OUTCOME_EWMA = "outcome_ewma"
    UNDERLYING_SKILL_EWMA = "underlying_skill_ewma"


class EvidenceKind(StrEnum):
    OUTCOME = "outcome"
    UNDERLYING_SKILL = "underlying_skill"


@dataclass(frozen=True)
class DynamicMetricObservation:
    observation_id: str
    player_id: str
    team_id: str | None
    game_id: str
    plate_appearance_id: str | None
    pitch_id: str | None
    event_at: datetime
    eligible_at: datetime
    metric_name: str
    evidence_kind: EvidenceKind
    value: float | None


@dataclass(frozen=True)
class DynamicStateEstimate:
    player_id: str
    metric_name: str
    variant: DynamicStateVariant
    half_life_days: int | None
    value: float
    posterior_variance: float
    static_value: float
    static_variance: float
    prior_effective_sample_size: float
    observed_sample_size: int
    missing_observation_count: int
    observed_effective_sample_size: float
    weighted_observation_sample_size: float
    effective_sample_size: float
    reliability: float
    evidence_cutoff: datetime
    latest_evidence_at: datetime | None
    evidence_checksum: str
    static_evidence_checksum: str
    static_source_evidence_ids: tuple[str, ...]
    checksum: str
    team_ids: tuple[str, ...]
    identity_scope: str = PLAYER_IDENTITY_SCOPE
    method: str = "point_in_time_dynamic_player_state_v1"


def estimate_dynamic_state(
    observations: Iterable[DynamicMetricObservation],
    *,
    player_id: str,
    metric_name: str,
    cutoff: datetime,
    current_game_id: str,
    static_value: float,
    static_variance: float,
    static_evidence_cutoff: datetime,
    static_evidence_checksum: str,
    static_source_evidence_ids: tuple[str, ...],
    variant: DynamicStateVariant,
    half_life_days: int | None = None,
    current_plate_appearance_id: str | None = None,
    prior_effective_sample_size: float = 20.0,
    max_observations: int = 100_000,
) -> DynamicStateEstimate:
    """Estimate one declared state variant with strict event-time eligibility.

    Outcome evidence is deliberately segregated from underlying-skill evidence.
    Missing observations contribute neither a zero nor an invented population
    value.  Current game and current plate appearance IDs are always excluded,
    even when their timestamps are malformed to look historical.
    """

    boundary = _as_utc(cutoff)
    static_cutoff = _as_utc(static_evidence_cutoff)
    _validate_static_lineage(
        static_cutoff,
        static_evidence_checksum,
        static_source_evidence_ids,
        boundary,
    )
    selected_variant = DynamicStateVariant(variant)
    _validate_query(
        player_id=player_id,
        metric_name=metric_name,
        current_game_id=current_game_id,
        static_value=static_value,
        static_variance=static_variance,
        prior_effective_sample_size=prior_effective_sample_size,
        max_observations=max_observations,
    )
    if selected_variant is DynamicStateVariant.STATIC_TRUE_TALENT:
        if half_life_days is not None:
            raise ValueError("the static variant does not use a half-life")
        payload = {
            "player_id": player_id,
            "metric_name": metric_name,
            "variant": selected_variant.value,
            "static_value": static_value,
            "static_variance": static_variance,
            "prior_effective_sample_size": prior_effective_sample_size,
            "static_evidence_cutoff": static_cutoff.isoformat(),
            "static_evidence_checksum": static_evidence_checksum,
            "static_source_evidence_ids": static_source_evidence_ids,
            "cutoff": boundary.isoformat(),
            "current_game_id": current_game_id,
            "current_plate_appearance_id": current_plate_appearance_id,
        }
        empty_checksum = deterministic_checksum(
            {
                "static_evidence_checksum": static_evidence_checksum,
                "static_source_evidence_ids": static_source_evidence_ids,
            }
        )
        return DynamicStateEstimate(
            player_id=player_id,
            metric_name=metric_name,
            variant=selected_variant,
            half_life_days=None,
            value=static_value,
            posterior_variance=static_variance,
            static_value=static_value,
            static_variance=static_variance,
            prior_effective_sample_size=prior_effective_sample_size,
            observed_sample_size=0,
            missing_observation_count=0,
            observed_effective_sample_size=0.0,
            weighted_observation_sample_size=0.0,
            effective_sample_size=prior_effective_sample_size,
            reliability=0.0,
            evidence_cutoff=static_cutoff,
            latest_evidence_at=None,
            evidence_checksum=empty_checksum,
            static_evidence_checksum=static_evidence_checksum,
            static_source_evidence_ids=static_source_evidence_ids,
            checksum=deterministic_checksum(payload),
            team_ids=(),
        )
    if half_life_days not in HALF_LIFE_CANDIDATES_DAYS:
        raise ValueError("half_life_days must be one of the predeclared 14/30/60 candidates")
    evidence_kind = (
        EvidenceKind.OUTCOME
        if selected_variant is DynamicStateVariant.OUTCOME_EWMA
        else EvidenceKind.UNDERLYING_SKILL
    )
    selected, missing = _select_observations(
        observations,
        player_id=player_id,
        metric_name=metric_name,
        evidence_kind=evidence_kind,
        cutoff=boundary,
        current_game_id=current_game_id,
        current_plate_appearance_id=current_plate_appearance_id,
        max_observations=max_observations,
    )
    weighted = tuple(
        (
            item,
            0.5
            ** (
                (boundary - _as_utc(item.event_at)).total_seconds()
                / (float(half_life_days) * 86_400.0)
            ),
        )
        for item in selected
    )
    observed_weight = sum(weight for _, weight in weighted)
    total_weight = prior_effective_sample_size + observed_weight
    weighted_sum = sum(float(item.value) * weight for item, weight in weighted)
    value = (static_value * prior_effective_sample_size + weighted_sum) / total_weight
    weight_square_sum = sum(weight * weight for _, weight in weighted)
    kish_sample = (
        observed_weight * observed_weight / weight_square_sum if weight_square_sum else 0.0
    )
    observation_mean = weighted_sum / observed_weight if observed_weight else static_value
    observation_variance = (
        sum(weight * (float(item.value) - observation_mean) ** 2 for item, weight in weighted)
        / observed_weight
        if observed_weight
        else 0.0
    )
    prior_fraction = prior_effective_sample_size / total_weight
    observation_fraction = observed_weight / total_weight
    posterior_variance = prior_fraction * prior_fraction * static_variance
    if observed_weight:
        evidence_variance = max(observation_variance, static_variance)
        posterior_variance += (
            observation_fraction * observation_fraction * evidence_variance / max(kish_sample, 1.0)
        )
    evidence_payload = tuple(_observation_payload(item) for item in selected)
    evidence_checksum = deterministic_checksum(
        {
            "static_evidence_checksum": static_evidence_checksum,
            "static_source_evidence_ids": static_source_evidence_ids,
            "events": evidence_payload,
        }
    )
    payload = {
        "player_id": player_id,
        "metric_name": metric_name,
        "variant": selected_variant.value,
        "half_life_days": half_life_days,
        "static_value": static_value,
        "static_variance": static_variance,
        "prior_effective_sample_size": prior_effective_sample_size,
        "static_evidence_cutoff": static_cutoff.isoformat(),
        "static_evidence_checksum": static_evidence_checksum,
        "static_source_evidence_ids": static_source_evidence_ids,
        "events": evidence_payload,
        "missing": missing,
        "cutoff": boundary.isoformat(),
        "current_game_id": current_game_id,
        "current_plate_appearance_id": current_plate_appearance_id,
    }
    return DynamicStateEstimate(
        player_id=player_id,
        metric_name=metric_name,
        variant=selected_variant,
        half_life_days=half_life_days,
        value=value,
        posterior_variance=posterior_variance,
        static_value=static_value,
        static_variance=static_variance,
        prior_effective_sample_size=prior_effective_sample_size,
        observed_sample_size=len(selected),
        missing_observation_count=missing,
        observed_effective_sample_size=observed_weight,
        weighted_observation_sample_size=kish_sample,
        effective_sample_size=total_weight,
        reliability=observed_weight / total_weight,
        evidence_cutoff=max(
            (_as_utc(item.eligible_at) for item in selected),
            default=static_cutoff,
        ),
        latest_evidence_at=max(
            (_as_utc(item.event_at) for item in selected),
            default=None,
        ),
        evidence_checksum=evidence_checksum,
        static_evidence_checksum=static_evidence_checksum,
        static_source_evidence_ids=static_source_evidence_ids,
        checksum=deterministic_checksum(payload),
        team_ids=tuple(sorted({item.team_id for item in selected if item.team_id})),
    )


def build_dynamic_state_candidates(
    observations: Iterable[DynamicMetricObservation],
    *,
    player_id: str,
    metric_name: str,
    cutoff: datetime,
    current_game_id: str,
    static_value: float,
    static_variance: float,
    static_evidence_cutoff: datetime,
    static_evidence_checksum: str,
    static_source_evidence_ids: tuple[str, ...],
    current_plate_appearance_id: str | None = None,
    prior_effective_sample_size: float = 20.0,
    half_life_candidates: tuple[int, ...] = HALF_LIFE_CANDIDATES_DAYS,
    max_observations: int = 100_000,
) -> tuple[DynamicStateEstimate, ...]:
    """Create the static and six declared EWMA research candidates."""

    if half_life_candidates != HALF_LIFE_CANDIDATES_DAYS:
        raise ValueError("candidate half-lives are predeclared as exactly 14, 30, and 60 days")
    buffered: list[DynamicMetricObservation] = []
    for item in observations:
        buffered.append(item)
        if len(buffered) > max_observations:
            raise ValueError("input observations exceed the bounded maximum")
    rows = tuple(buffered)
    common = {
        "player_id": player_id,
        "metric_name": metric_name,
        "cutoff": cutoff,
        "current_game_id": current_game_id,
        "current_plate_appearance_id": current_plate_appearance_id,
        "static_value": static_value,
        "static_variance": static_variance,
        "static_evidence_cutoff": static_evidence_cutoff,
        "static_evidence_checksum": static_evidence_checksum,
        "static_source_evidence_ids": static_source_evidence_ids,
        "prior_effective_sample_size": prior_effective_sample_size,
        "max_observations": max_observations,
    }
    candidates = [
        estimate_dynamic_state(
            (),
            variant=DynamicStateVariant.STATIC_TRUE_TALENT,
            half_life_days=None,
            **common,
        )
    ]
    for variant in (
        DynamicStateVariant.OUTCOME_EWMA,
        DynamicStateVariant.UNDERLYING_SKILL_EWMA,
    ):
        candidates.extend(
            estimate_dynamic_state(
                rows,
                variant=variant,
                half_life_days=half_life,
                **common,
            )
            for half_life in half_life_candidates
        )
    return tuple(candidates)


def _select_observations(
    observations: Iterable[DynamicMetricObservation],
    *,
    player_id: str,
    metric_name: str,
    evidence_kind: EvidenceKind,
    cutoff: datetime,
    current_game_id: str,
    current_plate_appearance_id: str | None,
    max_observations: int,
) -> tuple[tuple[DynamicMetricObservation, ...], int]:
    selected: list[DynamicMetricObservation] = []
    identifiers: set[str] = set()
    missing = 0
    for item in observations:
        _validate_observation(item)
        if (
            item.player_id != player_id
            or item.metric_name != metric_name
            or item.evidence_kind is not evidence_kind
        ):
            continue
        event_at = _as_utc(item.event_at)
        eligible_at = _as_utc(item.eligible_at)
        if eligible_at < event_at:
            raise ValueError("dynamic evidence cannot be eligible before it occurs")
        if item.game_id == current_game_id or (
            current_plate_appearance_id is not None
            and item.plate_appearance_id == current_plate_appearance_id
        ):
            continue
        if event_at >= cutoff or eligible_at > cutoff:
            continue
        if item.observation_id in identifiers:
            raise ValueError("eligible dynamic observation identifiers must be unique")
        identifiers.add(item.observation_id)
        if len(identifiers) > max_observations:
            raise ValueError("eligible observations exceed the bounded maximum")
        if item.value is None:
            missing += 1
            continue
        if not isfinite(item.value):
            raise ValueError("dynamic metric values must be finite")
        selected.append(item)
    return (
        tuple(
            sorted(
                selected,
                key=lambda item: (
                    _as_utc(item.event_at),
                    _as_utc(item.eligible_at),
                    item.observation_id,
                ),
            )
        ),
        missing,
    )


def _validate_query(
    *,
    player_id: str,
    metric_name: str,
    current_game_id: str,
    static_value: float,
    static_variance: float,
    prior_effective_sample_size: float,
    max_observations: int,
) -> None:
    if not player_id or not metric_name or not current_game_id:
        raise ValueError("player_id, metric_name, and current_game_id are required")
    values = (static_value, static_variance, prior_effective_sample_size)
    if any(not isfinite(value) for value in values):
        raise ValueError("state inputs must be finite")
    if static_variance <= 0 or prior_effective_sample_size <= 0:
        raise ValueError("variance and prior sample must be positive")
    if not isinstance(max_observations, int) or max_observations <= 0:
        raise ValueError("max_observations must be a positive integer")


def _validate_static_lineage(
    static_evidence_cutoff: datetime,
    static_evidence_checksum: str,
    static_source_evidence_ids: tuple[str, ...],
    target_cutoff: datetime,
) -> None:
    if static_evidence_cutoff >= target_cutoff:
        raise ValueError("static-state evidence must predate the target cutoff")
    if len(static_evidence_checksum) != 64 or any(
        character not in "0123456789abcdef" for character in static_evidence_checksum.lower()
    ):
        raise ValueError("static-state evidence checksum must be SHA-256")
    if (
        not static_source_evidence_ids
        or any(not identity for identity in static_source_evidence_ids)
        or len(set(static_source_evidence_ids)) != len(static_source_evidence_ids)
    ):
        raise ValueError("static-state source evidence identities must be nonempty and unique")


def _validate_observation(item: DynamicMetricObservation) -> None:
    if not all((item.observation_id, item.player_id, item.game_id, item.metric_name)):
        raise ValueError("dynamic observation identity fields are required")
    if not isinstance(item.evidence_kind, EvidenceKind):
        raise ValueError("dynamic observation evidence_kind is invalid")
    event_at = _as_utc(item.event_at)
    eligible_at = _as_utc(item.eligible_at)
    if eligible_at < event_at:
        raise ValueError("dynamic evidence cannot be eligible before it occurs")
    if item.value is not None and (
        isinstance(item.value, bool)
        or not isinstance(item.value, (int, float))
        or not isfinite(float(item.value))
    ):
        raise ValueError("dynamic metric values must be finite numeric or null")


def _observation_payload(item: DynamicMetricObservation) -> tuple[object, ...]:
    return (
        item.observation_id,
        item.player_id,
        item.team_id,
        item.game_id,
        item.plate_appearance_id,
        item.pitch_id,
        _as_utc(item.event_at).isoformat(),
        _as_utc(item.eligible_at).isoformat(),
        item.metric_name,
        item.evidence_kind.value,
        item.value,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)
