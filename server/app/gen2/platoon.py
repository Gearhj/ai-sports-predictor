"""Partially pooled batter and pitcher handedness estimates for Gen 2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import exp, isfinite, log
from typing import Iterable, Literal

from app.gen2.talent import PLAYER_IDENTITY_SCOPE, deterministic_checksum


PlayerRole = Literal["batter", "pitcher"]


@dataclass(frozen=True)
class PlatoonObservation:
    observation_id: str
    plate_appearance_id: str
    game_id: str
    event_at: datetime
    eligible_at: datetime
    batter_id: str
    pitcher_id: str
    batter_team_id: str | None
    pitcher_team_id: str | None
    batter_hand: str
    pitcher_hand: str
    metric_name: str
    success: bool | None


@dataclass(frozen=True)
class PlatoonRatePrior:
    probability: float
    evidence_cutoff: datetime
    evidence_checksum: str
    source_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class PlatoonEstimate:
    player_id: str
    player_role: PlayerRole
    opponent_hand: str
    metric_name: str
    overall_probability: float
    league_split_probability: float
    prior_probability: float
    probability: float
    variance: float
    prior_effective_sample_size: float
    observed_sample_size: int
    missing_observation_count: int
    effective_sample_size: float
    reliability: float
    evidence_cutoff: datetime
    forecast_cutoff: datetime
    latest_evidence_at: datetime | None
    evidence_checksum: str
    checksum: str
    team_ids: tuple[str, ...]
    source_evidence_ids: tuple[str, ...]
    identity_scope: str = PLAYER_IDENTITY_SCOPE
    method: str = "player_platoon_partial_pooling_v1"


@dataclass(frozen=True)
class MatchupPlatoonProbability:
    probability: float
    variance: float
    checksum: str
    method: str = "additive_log_odds_batter_pitcher_platoon_v2"


def estimate_player_platoon(
    observations: Iterable[PlatoonObservation],
    *,
    player_id: str,
    player_role: PlayerRole,
    opponent_hand: str,
    metric_name: str,
    cutoff: datetime,
    current_game_id: str,
    overall_prior: PlatoonRatePrior,
    league_split_prior: PlatoonRatePrior,
    overall_regression_sample_size: float = 80.0,
    league_split_effective_sample_size: float = 80.0,
    current_plate_appearance_id: str | None = None,
    max_observations: int = 100_000,
) -> PlatoonEstimate:
    """Shrink a player split toward both overall talent and the league split.

    ``success`` is deliberately metric-neutral: callers may estimate K, BB/HBP,
    HR, hit, contact, or another declared binary event.  Team IDs are retained
    as provenance but never define the player aggregation, so trades do not
    split or merge identities.
    """

    boundary = _as_utc(cutoff)
    _validate_prior(overall_prior, boundary, "overall")
    _validate_prior(league_split_prior, boundary, "league split")
    overall_probability = overall_prior.probability
    league_split_probability = league_split_prior.probability
    role = _validate_query(
        player_id=player_id,
        player_role=player_role,
        opponent_hand=opponent_hand,
        metric_name=metric_name,
        current_game_id=current_game_id,
        overall_probability=overall_probability,
        league_split_probability=league_split_probability,
        overall_regression_sample_size=overall_regression_sample_size,
        league_split_effective_sample_size=league_split_effective_sample_size,
        max_observations=max_observations,
    )
    hand = opponent_hand.upper()
    selected: list[PlatoonObservation] = []
    identifiers: set[str] = set()
    missing = 0
    for item in observations:
        _validate_observation(item)
        item_player_id = item.batter_id if role == "batter" else item.pitcher_id
        item_opponent_hand = item.pitcher_hand if role == "batter" else item.batter_hand
        if (
            item_player_id != player_id
            or item_opponent_hand.upper() != hand
            or item.metric_name != metric_name
        ):
            continue
        event_at = _as_utc(item.event_at)
        eligible_at = _as_utc(item.eligible_at)
        if eligible_at < event_at:
            raise ValueError("platoon evidence cannot be eligible before it occurs")
        if item.game_id == current_game_id or (
            current_plate_appearance_id is not None
            and item.plate_appearance_id == current_plate_appearance_id
        ):
            continue
        if event_at >= boundary or eligible_at > boundary:
            continue
        if item.observation_id in identifiers:
            raise ValueError("eligible platoon observation identifiers must be unique")
        identifiers.add(item.observation_id)
        if len(identifiers) > max_observations:
            raise ValueError("eligible observations exceed the bounded maximum")
        if item.success is None:
            missing += 1
            continue
        selected.append(item)
    selected.sort(
        key=lambda item: (
            _as_utc(item.event_at),
            _as_utc(item.eligible_at),
            item.observation_id,
        )
    )
    prior_effective = overall_regression_sample_size + league_split_effective_sample_size
    prior_alpha = (
        overall_probability * overall_regression_sample_size
        + league_split_probability * league_split_effective_sample_size
    )
    prior_beta = prior_effective - prior_alpha
    successes = sum(bool(item.success) for item in selected)
    alpha = prior_alpha + successes
    beta = prior_beta + len(selected) - successes
    total = alpha + beta
    evidence_payload = tuple(_observation_payload(item) for item in selected)
    prior_payload = {
        "overall": _prior_payload(overall_prior),
        "league_split": _prior_payload(league_split_prior),
    }
    evidence_checksum = deterministic_checksum(
        {"priors": prior_payload, "observations": evidence_payload}
    )
    payload = {
        "player_id": player_id,
        "player_role": role,
        "opponent_hand": hand,
        "metric_name": metric_name,
        "overall_probability": overall_probability,
        "league_split_probability": league_split_probability,
        "prior_lineage": prior_payload,
        "overall_regression_sample_size": overall_regression_sample_size,
        "league_split_effective_sample_size": league_split_effective_sample_size,
        "events": evidence_payload,
        "missing": missing,
        "cutoff": boundary.isoformat(),
        "current_game_id": current_game_id,
        "current_plate_appearance_id": current_plate_appearance_id,
    }
    team_ids = {
        item.batter_team_id if role == "batter" else item.pitcher_team_id for item in selected
    }
    source_evidence_ids = tuple(
        sorted(
            set(overall_prior.source_evidence_ids)
            | set(league_split_prior.source_evidence_ids)
            | {item.observation_id for item in selected}
        )
    )
    actual_evidence_cutoff = max(
        (
            _as_utc(overall_prior.evidence_cutoff),
            _as_utc(league_split_prior.evidence_cutoff),
            *(_as_utc(item.eligible_at) for item in selected),
        )
    )
    return PlatoonEstimate(
        player_id=player_id,
        player_role=role,
        opponent_hand=hand,
        metric_name=metric_name,
        overall_probability=overall_probability,
        league_split_probability=league_split_probability,
        prior_probability=prior_alpha / prior_effective,
        probability=alpha / total,
        variance=alpha * beta / (total * total * (total + 1)),
        prior_effective_sample_size=prior_effective,
        observed_sample_size=len(selected),
        missing_observation_count=missing,
        effective_sample_size=total,
        reliability=len(selected) / total,
        evidence_cutoff=actual_evidence_cutoff,
        forecast_cutoff=boundary,
        latest_evidence_at=max(
            (_as_utc(item.event_at) for item in selected),
            default=None,
        ),
        evidence_checksum=evidence_checksum,
        checksum=deterministic_checksum(payload),
        team_ids=tuple(sorted(item for item in team_ids if item)),
        source_evidence_ids=source_evidence_ids,
    )


def combine_batter_pitcher_platoon(
    *,
    batter_split: PlatoonEstimate,
    pitcher_split: PlatoonEstimate,
    batter_hand: str,
    pitcher_hand: str,
) -> MatchupPlatoonProbability:
    """Combine partially pooled player split effects on an additive-logit scale."""

    league_probability = batter_split.league_split_probability
    batter_overall_probability = batter_split.overall_probability
    pitcher_overall_probability = pitcher_split.overall_probability
    values = (
        league_probability,
        batter_overall_probability,
        batter_split.probability,
        pitcher_overall_probability,
        pitcher_split.probability,
    )
    if any(not isfinite(value) or not 0 < value < 1 for value in values):
        raise ValueError("matchup probabilities must be finite and within (0, 1)")
    if batter_split.player_role != "batter" or pitcher_split.player_role != "pitcher":
        raise ValueError("matchup requires one batter split and one pitcher split")
    if batter_split.metric_name != pitcher_split.metric_name:
        raise ValueError("batter and pitcher splits must estimate the same metric")
    if batter_split.league_split_probability != pitcher_split.league_split_probability:
        raise ValueError("batter and pitcher estimates must share one governed league prior")
    normalized_batter_hand = batter_hand.upper()
    normalized_pitcher_hand = pitcher_hand.upper()
    if normalized_batter_hand not in ("L", "R", "S") or normalized_pitcher_hand not in (
        "L",
        "R",
        "S",
    ):
        raise ValueError("matchup hands must be L, R, or S")
    if batter_split.opponent_hand != normalized_pitcher_hand:
        raise ValueError("batter split is not compatible with the matchup pitcher hand")
    if pitcher_split.opponent_hand != normalized_batter_hand:
        raise ValueError("pitcher split is not compatible with the matchup batter hand")
    if batter_split.forecast_cutoff != pitcher_split.forecast_cutoff:
        raise ValueError("batter and pitcher split estimates must share one forecast cutoff")
    log_odds = (
        _logit(league_probability)
        + (_logit(batter_overall_probability) - _logit(league_probability))
        + (_logit(pitcher_overall_probability) - _logit(league_probability))
        + (_logit(batter_split.probability) - _logit(batter_overall_probability))
        + (_logit(pitcher_split.probability) - _logit(pitcher_overall_probability))
    )
    probability = 1 / (1 + exp(-log_odds))
    batter_logit_variance = batter_split.variance / (
        batter_split.probability**2 * (1 - batter_split.probability) ** 2
    )
    pitcher_logit_variance = pitcher_split.variance / (
        pitcher_split.probability**2 * (1 - pitcher_split.probability) ** 2
    )
    variance = (probability * (1 - probability)) ** 2 * (
        batter_logit_variance + pitcher_logit_variance
    )
    payload = {
        "league_probability": league_probability,
        "batter_overall_probability": batter_overall_probability,
        "batter_split_checksum": batter_split.checksum,
        "pitcher_overall_probability": pitcher_overall_probability,
        "pitcher_split_checksum": pitcher_split.checksum,
        "batter_hand": normalized_batter_hand,
        "pitcher_hand": normalized_pitcher_hand,
        "forecast_cutoff": batter_split.forecast_cutoff.isoformat(),
        "probability": probability,
        "variance": variance,
    }
    return MatchupPlatoonProbability(
        probability=probability,
        variance=variance,
        checksum=deterministic_checksum(payload),
    )


def _validate_query(
    *,
    player_id: str,
    player_role: str,
    opponent_hand: str,
    metric_name: str,
    current_game_id: str,
    overall_probability: float,
    league_split_probability: float,
    overall_regression_sample_size: float,
    league_split_effective_sample_size: float,
    max_observations: int,
) -> PlayerRole:
    if not player_id or not metric_name or not current_game_id:
        raise ValueError("player_id, metric_name, and current_game_id are required")
    if player_role not in ("batter", "pitcher"):
        raise ValueError("player_role must be batter or pitcher")
    if opponent_hand.upper() not in ("L", "R", "S"):
        raise ValueError("opponent_hand must be L, R, or S")
    probabilities = (overall_probability, league_split_probability)
    if any(not isfinite(value) or not 0 < value < 1 for value in probabilities):
        raise ValueError("prior probabilities must be finite and within (0, 1)")
    samples = (overall_regression_sample_size, league_split_effective_sample_size)
    if any(not isfinite(value) or value <= 0 for value in samples):
        raise ValueError("partial-pooling sample sizes must be finite and positive")
    if not isinstance(max_observations, int) or max_observations <= 0:
        raise ValueError("max_observations must be a positive integer")
    return "batter" if player_role == "batter" else "pitcher"


def _validate_observation(item: PlatoonObservation) -> None:
    identities = (
        item.observation_id,
        item.plate_appearance_id,
        item.game_id,
        item.batter_id,
        item.pitcher_id,
    )
    if not all(identities):
        raise ValueError("platoon observation identity fields are required")
    if not item.metric_name:
        raise ValueError("platoon metric_name is required")
    if item.batter_hand.upper() not in ("L", "R", "S"):
        raise ValueError("batter_hand must be L, R, or S")
    if item.pitcher_hand.upper() not in ("L", "R", "S"):
        raise ValueError("pitcher_hand must be L, R, or S")
    event_at = _as_utc(item.event_at)
    eligible_at = _as_utc(item.eligible_at)
    if eligible_at < event_at:
        raise ValueError("platoon evidence cannot be eligible before it occurs")
    if item.success is not None and type(item.success) is not bool:
        raise ValueError("platoon success must be bool or null")


def _validate_prior(
    prior: PlatoonRatePrior,
    boundary: datetime,
    label: str,
) -> None:
    if not isfinite(prior.probability) or not 0 < prior.probability < 1:
        raise ValueError(f"{label} prior probability must be finite and within (0, 1)")
    if _as_utc(prior.evidence_cutoff) >= boundary:
        raise ValueError(f"{label} prior evidence must predate the forecast cutoff")
    if len(prior.evidence_checksum) != 64 or any(
        character not in "0123456789abcdef"
        for character in prior.evidence_checksum.lower()
    ):
        raise ValueError(f"{label} prior evidence checksum must be SHA-256")
    if (
        not prior.source_evidence_ids
        or any(not identity for identity in prior.source_evidence_ids)
        or len(set(prior.source_evidence_ids)) != len(prior.source_evidence_ids)
    ):
        raise ValueError(f"{label} prior source evidence identities must be nonempty and unique")


def _prior_payload(prior: PlatoonRatePrior) -> dict[str, object]:
    return {
        "probability": prior.probability,
        "evidence_cutoff": _as_utc(prior.evidence_cutoff).isoformat(),
        "evidence_checksum": prior.evidence_checksum,
        "source_evidence_ids": tuple(sorted(prior.source_evidence_ids)),
    }


def _observation_payload(item: PlatoonObservation) -> tuple[object, ...]:
    return (
        item.observation_id,
        item.plate_appearance_id,
        item.game_id,
        _as_utc(item.event_at).isoformat(),
        _as_utc(item.eligible_at).isoformat(),
        item.batter_id,
        item.pitcher_id,
        item.batter_team_id,
        item.pitcher_team_id,
        item.batter_hand.upper(),
        item.pitcher_hand.upper(),
        item.metric_name,
        item.success,
    )


def _logit(value: float) -> float:
    return log(value / (1 - value))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)
