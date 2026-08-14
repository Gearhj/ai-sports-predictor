"""Pure, point-in-time player true-talent primitives for Generation 2.

The helpers in this module operate on bounded iterables and return immutable,
checksum-addressed values.  They do not read databases, artifacts, providers,
or labels from the game being forecast.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from math import isclose, isfinite
from typing import Iterable, Literal

from app.gen2.event_taxonomy import ordered_outcomes


PA_EVENT_CATEGORIES = tuple(outcome.value for outcome in ordered_outcomes())
PLAYER_IDENTITY_SCOPE = "player_id_across_teams"
TalentRole = Literal["hitter", "pitcher"]


@dataclass(frozen=True)
class PriorRateProfile:
    """A named population stratum used to construct a Dirichlet prior."""

    name: str
    probabilities: tuple[float, ...]
    effective_sample_size: float
    evidence_cutoff: datetime
    evidence_checksum: str
    source_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class DirichletPrior:
    categories: tuple[str, ...]
    alpha: tuple[float, ...]
    component_names: tuple[str, ...]
    checksum: str
    evidence_cutoff: datetime | None = None

    @property
    def effective_sample_size(self) -> float:
        return sum(self.alpha)

    @property
    def probabilities(self) -> dict[str, float]:
        total = self.effective_sample_size
        return dict(zip(self.categories, (value / total for value in self.alpha), strict=True))


@dataclass(frozen=True)
class SeasonEventSummary:
    """Completed prior-season PA counts; never a current/future-season row."""

    season: int
    counts: tuple[int, ...]
    evidence_cutoff: datetime
    evidence_id: str


@dataclass(frozen=True)
class PlateAppearanceEvent:
    event_id: str
    plate_appearance_id: str
    game_id: str
    player_id: str
    player_role: TalentRole
    team_id: str | None
    season: int
    event_at: datetime
    eligible_at: datetime
    outcome: str


@dataclass(frozen=True)
class DirichletPosterior:
    player_id: str
    player_role: TalentRole
    categories: tuple[str, ...]
    alpha: tuple[float, ...]
    observed_counts: tuple[int, ...]
    prior_effective_sample_size: float
    observed_sample_size: int
    effective_sample_size: float
    reliability: float
    variances: tuple[float, ...]
    evidence_cutoff: datetime
    latest_evidence_at: datetime | None
    evidence_checksum: str
    checksum: str
    team_ids: tuple[str, ...]
    identity_scope: str = PLAYER_IDENTITY_SCOPE
    method: str = "chronological_dirichlet_multinomial_pa_v1"

    @property
    def probabilities(self) -> dict[str, float]:
        return dict(
            zip(
                self.categories,
                (value / self.effective_sample_size for value in self.alpha),
                strict=True,
            )
        )

    @property
    def posterior_variances(self) -> dict[str, float]:
        return dict(zip(self.categories, self.variances, strict=True))


@dataclass(frozen=True)
class BinaryMetricObservation:
    observation_id: str
    player_id: str
    team_id: str | None
    game_id: str
    plate_appearance_id: str | None
    event_at: datetime
    eligible_at: datetime
    value: bool | None


@dataclass(frozen=True)
class BetaPrior:
    alpha: float
    beta: float
    evidence_cutoff: datetime
    evidence_checksum: str
    source_evidence_ids: tuple[str, ...]
    name: str = "population_prior"


@dataclass(frozen=True)
class BetaPosterior:
    player_id: str
    alpha: float
    beta: float
    mean: float
    variance: float
    prior_effective_sample_size: float
    observed_sample_size: int
    missing_observation_count: int
    effective_sample_size: float
    reliability: float
    evidence_cutoff: datetime
    evidence_checksum: str
    checksum: str
    team_ids: tuple[str, ...]
    identity_scope: str = PLAYER_IDENTITY_SCOPE
    method: str = "chronological_beta_binomial_metric_v1"


@dataclass(frozen=True)
class ContinuousMetricObservation:
    observation_id: str
    player_id: str
    team_id: str | None
    game_id: str
    plate_appearance_id: str | None
    event_at: datetime
    eligible_at: datetime
    value: float | None


@dataclass(frozen=True)
class NormalPrior:
    mean: float
    variance: float
    effective_sample_size: float
    evidence_cutoff: datetime
    evidence_checksum: str
    source_evidence_ids: tuple[str, ...]
    name: str = "population_prior"


@dataclass(frozen=True)
class NormalPosterior:
    player_id: str
    mean: float
    variance: float
    prior_effective_sample_size: float
    observed_sample_size: int
    missing_observation_count: int
    effective_sample_size: float
    reliability: float
    evidence_cutoff: datetime
    evidence_checksum: str
    checksum: str
    team_ids: tuple[str, ...]
    identity_scope: str = PLAYER_IDENTITY_SCOPE
    method: str = "chronological_normal_normal_metric_v1"


def build_hierarchical_pa_prior(
    *,
    categories: tuple[str, ...] = PA_EVENT_CATEGORIES,
    league: PriorRateProfile,
    role: PriorRateProfile | None = None,
    handedness: PriorRateProfile | None = None,
    rookie: PriorRateProfile | None = None,
    is_rookie: bool = False,
    evidence_cutoff: datetime | None = None,
) -> DirichletPrior:
    """Combine league, role, hand, and optional rookie population strata."""

    _validate_categories(categories)
    profiles = [league]
    if role is not None:
        profiles.append(role)
    if handedness is not None:
        profiles.append(handedness)
    if is_rookie and rookie is None:
        raise ValueError("a rookie population profile is required for a rookie prior")
    if is_rookie and rookie is not None:
        profiles.append(rookie)
    for profile in profiles:
        _validate_profile(profile, categories)
    if evidence_cutoff is None:
        raise ValueError("hierarchical prior requires a target evidence cutoff")
    boundary = _as_utc(evidence_cutoff)
    for profile in profiles:
        if _as_utc(profile.evidence_cutoff) >= boundary:
            raise ValueError("population-prior evidence must predate the target cutoff")
    alpha = tuple(
        sum(profile.probabilities[index] * profile.effective_sample_size for profile in profiles)
        for index in range(len(categories))
    )
    payload = {
        "categories": categories,
        "alpha": alpha,
        "components": tuple(
            (
                profile.name,
                _iso(profile.evidence_cutoff),
                profile.evidence_checksum,
                profile.source_evidence_ids,
            )
            for profile in profiles
        ),
        "target_cutoff": _iso(boundary),
        "is_rookie": is_rookie,
    }
    return DirichletPrior(
        categories=categories,
        alpha=alpha,
        component_names=tuple(profile.name for profile in profiles),
        checksum=deterministic_checksum(payload),
        evidence_cutoff=max(_as_utc(profile.evidence_cutoff) for profile in profiles),
    )


def build_weighted_multiseason_prior(
    *,
    base_prior: DirichletPrior,
    summaries: Iterable[SeasonEventSummary],
    target_season: int,
    seasons: int,
    decay: float = 0.65,
    evidence_cutoff: datetime | None = None,
) -> DirichletPrior:
    """Build a one-, two-, or three-season prior without backward leakage."""

    if seasons not in (1, 2, 3):
        raise ValueError("seasons must be one, two, or three")
    if target_season < 1876:
        raise ValueError("target_season is invalid")
    if not isfinite(decay) or not 0 < decay <= 1:
        raise ValueError("decay must be within (0, 1]")
    if evidence_cutoff is None:
        raise ValueError("multiseason prior requires a target evidence cutoff")
    boundary = _as_utc(evidence_cutoff)
    if base_prior.evidence_cutoff is None:
        raise ValueError("base prior is missing governed evidence cutoff")
    if _as_utc(base_prior.evidence_cutoff) >= boundary:
        raise ValueError("base prior evidence must predate the target cutoff")
    rows = tuple(summaries)
    if len({item.season for item in rows}) != len(rows):
        raise ValueError("season summaries must have unique seasons")
    for item in rows:
        _validate_season_summary(item, base_prior.categories)
        if item.season >= target_season:
            raise ValueError("current- or future-season evidence cannot enter a prior")
        if _as_utc(item.evidence_cutoff) >= boundary:
            raise ValueError("prior-season evidence must predate the target cutoff")
    selected = tuple(sorted(rows, key=lambda item: item.season, reverse=True)[:seasons])
    alpha = list(base_prior.alpha)
    components = list(base_prior.component_names)
    for item in selected:
        weight = decay ** (target_season - item.season - 1)
        alpha = [
            value + count * weight
            for value, count in zip(alpha, item.counts, strict=True)
        ]
        components.append(f"season_{item.season}@{weight:.12g}")
    actual_cutoff = max(
        (_as_utc(item.evidence_cutoff) for item in selected),
        default=_as_utc(base_prior.evidence_cutoff),
    )
    actual_cutoff = max(actual_cutoff, _as_utc(base_prior.evidence_cutoff))
    payload = {
        "base_prior": base_prior.checksum,
        "target_season": target_season,
        "seasons": seasons,
        "decay": decay,
        "selected": tuple(
            (item.season, item.counts, _iso(item.evidence_cutoff), item.evidence_id)
            for item in selected
        ),
        "alpha": tuple(alpha),
        "evidence_cutoff": _iso(actual_cutoff),
        "target_cutoff": _iso(boundary),
    }
    return DirichletPrior(
        categories=base_prior.categories,
        alpha=tuple(alpha),
        component_names=tuple(components),
        checksum=deterministic_checksum(payload),
        evidence_cutoff=actual_cutoff,
    )


def build_multiseason_prior_candidates(
    *,
    base_prior: DirichletPrior,
    summaries: Iterable[SeasonEventSummary],
    target_season: int,
    decay: float = 0.65,
    evidence_cutoff: datetime | None = None,
) -> dict[int, DirichletPrior]:
    """Return identical-input one/two/three-season candidates for validation."""

    rows = tuple(summaries)
    return {
        seasons: build_weighted_multiseason_prior(
            base_prior=base_prior,
            summaries=rows,
            target_season=target_season,
            seasons=seasons,
            decay=decay,
            evidence_cutoff=evidence_cutoff,
        )
        for seasons in (1, 2, 3)
    }


def estimate_pa_event_posterior(
    events: Iterable[PlateAppearanceEvent],
    *,
    player_id: str,
    player_role: TalentRole,
    cutoff: datetime,
    prior: DirichletPrior,
    current_game_id: str,
    current_plate_appearance_id: str | None = None,
    max_observations: int = 100_000,
) -> DirichletPosterior:
    """Update a PA event posterior using only strictly prior eligible events."""

    boundary = _as_utc(cutoff)
    _validate_query(player_id, current_game_id, max_observations)
    role = _validate_talent_role(player_role)
    if prior.evidence_cutoff is None or _as_utc(prior.evidence_cutoff) >= boundary:
        raise ValueError("player prior evidence must predate the posterior cutoff")
    selected = _select_pa_events(
        events,
        player_id=player_id,
        player_role=role,
        cutoff=boundary,
        current_game_id=current_game_id,
        current_plate_appearance_id=current_plate_appearance_id,
        categories=prior.categories,
        max_observations=max_observations,
    )
    category_index = {name: index for index, name in enumerate(prior.categories)}
    counts = [0] * len(prior.categories)
    for item in selected:
        counts[category_index[item.outcome]] += 1
    alpha = tuple(value + counts[index] for index, value in enumerate(prior.alpha))
    total = sum(alpha)
    variances = tuple(value * (total - value) / (total * total * (total + 1)) for value in alpha)
    evidence_payload = tuple(_pa_payload(item) for item in selected)
    evidence_checksum = deterministic_checksum(evidence_payload)
    payload = {
        "player_id": player_id,
        "player_role": role,
        "prior": prior.checksum,
        "counts": tuple(counts),
        "events": evidence_payload,
        "cutoff": _iso(boundary),
        "current_game_id": current_game_id,
        "current_plate_appearance_id": current_plate_appearance_id,
    }
    observed = len(selected)
    return DirichletPosterior(
        player_id=player_id,
        player_role=role,
        categories=prior.categories,
        alpha=alpha,
        observed_counts=tuple(counts),
        prior_effective_sample_size=prior.effective_sample_size,
        observed_sample_size=observed,
        effective_sample_size=total,
        reliability=observed / total,
        variances=variances,
        evidence_cutoff=boundary,
        latest_evidence_at=max(
            (_as_utc(item.event_at) for item in selected),
            default=None,
        ),
        evidence_checksum=evidence_checksum,
        checksum=deterministic_checksum(payload),
        team_ids=tuple(sorted({item.team_id for item in selected if item.team_id})),
    )


def update_beta_posterior(
    observations: Iterable[BinaryMetricObservation],
    *,
    player_id: str,
    cutoff: datetime,
    prior: BetaPrior,
    current_game_id: str,
    current_plate_appearance_id: str | None = None,
    max_observations: int = 100_000,
) -> BetaPosterior:
    """Conjugate update for binary underlying metrics such as swing/whiff."""

    boundary = _as_utc(cutoff)
    _validate_query(player_id, current_game_id, max_observations)
    _validate_beta_prior(prior)
    if _as_utc(prior.evidence_cutoff) >= boundary:
        raise ValueError("beta prior evidence must predate the posterior cutoff")
    selected, missing = _select_metric_observations(
        observations,
        player_id=player_id,
        cutoff=boundary,
        current_game_id=current_game_id,
        current_plate_appearance_id=current_plate_appearance_id,
        max_observations=max_observations,
    )
    successes = sum(bool(item.value) for item in selected)
    alpha = prior.alpha + successes
    beta = prior.beta + len(selected) - successes
    total = alpha + beta
    evidence_payload = tuple(_binary_payload(item) for item in selected)
    evidence_checksum = deterministic_checksum(
        {
            "prior_evidence_checksum": prior.evidence_checksum,
            "prior_source_evidence_ids": prior.source_evidence_ids,
            "events": evidence_payload,
        }
    )
    payload = {
        "player_id": player_id,
        "prior": (
            prior.name,
            prior.alpha,
            prior.beta,
            _iso(prior.evidence_cutoff),
            prior.evidence_checksum,
            prior.source_evidence_ids,
        ),
        "events": evidence_payload,
        "missing": missing,
        "cutoff": _iso(boundary),
        "current_game_id": current_game_id,
        "current_plate_appearance_id": current_plate_appearance_id,
    }
    return BetaPosterior(
        player_id=player_id,
        alpha=alpha,
        beta=beta,
        mean=alpha / total,
        variance=alpha * beta / (total * total * (total + 1)),
        prior_effective_sample_size=prior.alpha + prior.beta,
        observed_sample_size=len(selected),
        missing_observation_count=missing,
        effective_sample_size=total,
        reliability=len(selected) / total,
        evidence_cutoff=max(
            (_as_utc(item.eligible_at) for item in selected),
            default=_as_utc(prior.evidence_cutoff),
        ),
        evidence_checksum=evidence_checksum,
        checksum=deterministic_checksum(payload),
        team_ids=tuple(sorted({item.team_id for item in selected if item.team_id})),
    )


def update_normal_posterior(
    observations: Iterable[ContinuousMetricObservation],
    *,
    player_id: str,
    cutoff: datetime,
    prior: NormalPrior,
    observation_variance: float,
    current_game_id: str,
    current_plate_appearance_id: str | None = None,
    max_observations: int = 100_000,
) -> NormalPosterior:
    """Normal-normal update for continuous underlying metrics such as velocity."""

    boundary = _as_utc(cutoff)
    _validate_query(player_id, current_game_id, max_observations)
    _validate_normal_prior(prior, observation_variance)
    if _as_utc(prior.evidence_cutoff) >= boundary:
        raise ValueError("normal prior evidence must predate the posterior cutoff")
    selected, missing = _select_metric_observations(
        observations,
        player_id=player_id,
        cutoff=boundary,
        current_game_id=current_game_id,
        current_plate_appearance_id=current_plate_appearance_id,
        max_observations=max_observations,
    )
    values = tuple(float(item.value) for item in selected if item.value is not None)
    prior_precision = 1 / prior.variance
    observation_precision = 1 / observation_variance
    posterior_precision = prior_precision + len(values) * observation_precision
    variance = 1 / posterior_precision
    mean = variance * (
        prior.mean * prior_precision + sum(values) * observation_precision
    )
    total_effective = prior.effective_sample_size + len(values)
    evidence_payload = tuple(_continuous_payload(item) for item in selected)
    evidence_checksum = deterministic_checksum(
        {
            "prior_evidence_checksum": prior.evidence_checksum,
            "prior_source_evidence_ids": prior.source_evidence_ids,
            "events": evidence_payload,
        }
    )
    payload = {
        "player_id": player_id,
        "prior": (
            prior.name,
            prior.mean,
            prior.variance,
            prior.effective_sample_size,
            _iso(prior.evidence_cutoff),
            prior.evidence_checksum,
            prior.source_evidence_ids,
        ),
        "observation_variance": observation_variance,
        "events": evidence_payload,
        "missing": missing,
        "cutoff": _iso(boundary),
        "current_game_id": current_game_id,
        "current_plate_appearance_id": current_plate_appearance_id,
    }
    return NormalPosterior(
        player_id=player_id,
        mean=mean,
        variance=variance,
        prior_effective_sample_size=prior.effective_sample_size,
        observed_sample_size=len(values),
        missing_observation_count=missing,
        effective_sample_size=total_effective,
        reliability=len(values) / total_effective,
        evidence_cutoff=max(
            (_as_utc(item.eligible_at) for item in selected),
            default=_as_utc(prior.evidence_cutoff),
        ),
        evidence_checksum=evidence_checksum,
        checksum=deterministic_checksum(payload),
        team_ids=tuple(sorted({item.team_id for item in selected if item.team_id})),
    )


def deterministic_checksum(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _select_pa_events(
    events: Iterable[PlateAppearanceEvent],
    *,
    player_id: str,
    player_role: TalentRole,
    cutoff: datetime,
    current_game_id: str,
    current_plate_appearance_id: str | None,
    categories: tuple[str, ...],
    max_observations: int,
) -> tuple[PlateAppearanceEvent, ...]:
    selected: list[PlateAppearanceEvent] = []
    identifiers: set[str] = set()
    for item in events:
        _validate_pa_event(item)
        if item.player_id != player_id or item.player_role != player_role:
            continue
        event_at = _as_utc(item.event_at)
        eligible_at = _as_utc(item.eligible_at)
        if eligible_at < event_at:
            raise ValueError("PA evidence cannot be eligible before it occurs")
        if item.game_id == current_game_id or (
            current_plate_appearance_id is not None
            and item.plate_appearance_id == current_plate_appearance_id
        ):
            continue
        if event_at >= cutoff or eligible_at > cutoff:
            continue
        if item.outcome not in categories:
            raise ValueError(f"unsupported PA event outcome: {item.outcome}")
        if item.event_id in identifiers:
            raise ValueError("eligible PA event identifiers must be unique")
        identifiers.add(item.event_id)
        selected.append(item)
        if len(selected) > max_observations:
            raise ValueError("eligible observations exceed the bounded maximum")
    return tuple(sorted(selected, key=_observation_sort_key))


def _select_metric_observations(
    observations: Iterable[BinaryMetricObservation] | Iterable[ContinuousMetricObservation],
    *,
    player_id: str,
    cutoff: datetime,
    current_game_id: str,
    current_plate_appearance_id: str | None,
    max_observations: int,
) -> tuple[tuple[BinaryMetricObservation, ...] | tuple[ContinuousMetricObservation, ...], int]:
    selected: list[BinaryMetricObservation | ContinuousMetricObservation] = []
    identifiers: set[str] = set()
    missing = 0
    for item in observations:
        _validate_metric_observation(item)
        if item.player_id != player_id:
            continue
        event_at = _as_utc(item.event_at)
        eligible_at = _as_utc(item.eligible_at)
        if eligible_at < event_at:
            raise ValueError("metric evidence cannot be eligible before it occurs")
        if item.game_id == current_game_id or (
            current_plate_appearance_id is not None
            and item.plate_appearance_id == current_plate_appearance_id
        ):
            continue
        if event_at >= cutoff or eligible_at > cutoff:
            continue
        if item.observation_id in identifiers:
            raise ValueError("eligible metric observation identifiers must be unique")
        identifiers.add(item.observation_id)
        if len(identifiers) > max_observations:
            raise ValueError("eligible observations exceed the bounded maximum")
        if item.value is None:
            missing += 1
            continue
        if isinstance(item.value, float) and not isfinite(item.value):
            raise ValueError("metric values must be finite")
        selected.append(item)
    return tuple(sorted(selected, key=_observation_sort_key)), missing


def _validate_profile(profile: PriorRateProfile, categories: tuple[str, ...]) -> None:
    if not profile.name:
        raise ValueError("prior profile name is required")
    if len(profile.probabilities) != len(categories):
        raise ValueError("prior probability length must match categories")
    if any(not isfinite(value) or value <= 0 or value >= 1 for value in profile.probabilities):
        raise ValueError("prior category probabilities must be finite and within (0, 1)")
    if not isclose(sum(profile.probabilities), 1.0, abs_tol=1e-9):
        raise ValueError("prior category probabilities must sum to one")
    if not isfinite(profile.effective_sample_size) or profile.effective_sample_size <= 0:
        raise ValueError("prior effective sample size must be positive")
    _as_utc(profile.evidence_cutoff)
    if len(profile.evidence_checksum) != 64 or any(
        character not in "0123456789abcdef" for character in profile.evidence_checksum.lower()
    ):
        raise ValueError("prior evidence_checksum must be SHA-256")
    if not profile.source_evidence_ids or any(not item for item in profile.source_evidence_ids):
        raise ValueError("prior source evidence identities are required")
    if len(set(profile.source_evidence_ids)) != len(profile.source_evidence_ids):
        raise ValueError("prior source evidence identities must be unique")


def _validate_categories(categories: tuple[str, ...]) -> None:
    if len(categories) < 2 or any(not item for item in categories):
        raise ValueError("at least two named categories are required")
    if len(set(categories)) != len(categories):
        raise ValueError("categories must be unique")


def _validate_season_summary(item: SeasonEventSummary, categories: tuple[str, ...]) -> None:
    if len(item.counts) != len(categories):
        raise ValueError("season count length must match prior categories")
    if any(not isinstance(value, int) or value < 0 for value in item.counts):
        raise ValueError("season event counts must be non-negative integers")
    if not item.evidence_id:
        raise ValueError("season evidence_id is required")
    _as_utc(item.evidence_cutoff)


def _validate_beta_prior(prior: BetaPrior) -> None:
    if not prior.name or not isfinite(prior.alpha) or not isfinite(prior.beta):
        raise ValueError("beta prior must be named and finite")
    if prior.alpha <= 0 or prior.beta <= 0:
        raise ValueError("beta prior parameters must be positive")
    _validate_scalar_prior_lineage(
        prior.evidence_cutoff,
        prior.evidence_checksum,
        prior.source_evidence_ids,
        "beta prior",
    )


def _validate_normal_prior(prior: NormalPrior, observation_variance: float) -> None:
    values = (prior.mean, prior.variance, prior.effective_sample_size, observation_variance)
    if not prior.name or any(not isfinite(value) for value in values):
        raise ValueError("normal prior inputs must be named and finite")
    if prior.variance <= 0 or prior.effective_sample_size <= 0 or observation_variance <= 0:
        raise ValueError("normal and observation variances and prior sample must be positive")
    _validate_scalar_prior_lineage(
        prior.evidence_cutoff,
        prior.evidence_checksum,
        prior.source_evidence_ids,
        "normal prior",
    )


def _validate_scalar_prior_lineage(
    evidence_cutoff: datetime,
    evidence_checksum: str,
    source_evidence_ids: tuple[str, ...],
    label: str,
) -> None:
    _as_utc(evidence_cutoff)
    if len(evidence_checksum) != 64 or any(
        character not in "0123456789abcdef" for character in evidence_checksum.lower()
    ):
        raise ValueError(f"{label} evidence_checksum must be SHA-256")
    if (
        not source_evidence_ids
        or any(not item for item in source_evidence_ids)
        or len(set(source_evidence_ids)) != len(source_evidence_ids)
    ):
        raise ValueError(f"{label} source evidence identities must be nonempty and unique")


def _validate_pa_event(item: PlateAppearanceEvent) -> None:
    if not all((item.event_id, item.plate_appearance_id, item.game_id, item.player_id)):
        raise ValueError("PA event identity fields are required")
    if item.season < 1876:
        raise ValueError("PA event season is invalid")
    _validate_talent_role(item.player_role)
    event_at = _as_utc(item.event_at)
    if event_at.year != item.season:
        raise ValueError("PA event season must match its event timestamp")
    _as_utc(item.eligible_at)


def _validate_metric_observation(
    item: BinaryMetricObservation | ContinuousMetricObservation,
) -> None:
    if not all((item.observation_id, item.player_id, item.game_id)):
        raise ValueError("metric observation identity fields are required")
    event_at = _as_utc(item.event_at)
    eligible_at = _as_utc(item.eligible_at)
    if eligible_at < event_at:
        raise ValueError("metric evidence cannot be eligible before it occurs")
    if isinstance(item, BinaryMetricObservation):
        if item.value is not None and type(item.value) is not bool:
            raise ValueError("binary metric values must be bool or null")
    elif item.value is not None and (
        isinstance(item.value, bool)
        or not isinstance(item.value, (int, float))
        or not isfinite(float(item.value))
    ):
        raise ValueError("continuous metric values must be finite numeric or null")


def _validate_query(player_id: str, current_game_id: str, max_observations: int) -> None:
    if not player_id or not current_game_id:
        raise ValueError("player_id and current_game_id are required")
    if not isinstance(max_observations, int) or max_observations <= 0:
        raise ValueError("max_observations must be a positive integer")


def _validate_talent_role(value: str) -> TalentRole:
    if value not in ("hitter", "pitcher"):
        raise ValueError("player_role must be hitter or pitcher")
    return "hitter" if value == "hitter" else "pitcher"


def _observation_sort_key(item: object) -> tuple[datetime, datetime, str]:
    event_at = _as_utc(getattr(item, "event_at"))
    eligible_at = _as_utc(getattr(item, "eligible_at"))
    identifier = getattr(item, "event_id", getattr(item, "observation_id", ""))
    return event_at, eligible_at, identifier


def _pa_payload(item: PlateAppearanceEvent) -> tuple[object, ...]:
    return (
        item.event_id,
        item.plate_appearance_id,
        item.game_id,
        item.player_id,
        item.player_role,
        item.team_id,
        item.season,
        _iso(item.event_at),
        _iso(item.eligible_at),
        item.outcome,
    )


def _binary_payload(item: BinaryMetricObservation) -> tuple[object, ...]:
    return (
        item.observation_id,
        item.player_id,
        item.team_id,
        item.game_id,
        item.plate_appearance_id,
        _iso(item.event_at),
        _iso(item.eligible_at),
        item.value,
    )


def _continuous_payload(item: ContinuousMetricObservation) -> tuple[object, ...]:
    return (
        item.observation_id,
        item.player_id,
        item.team_id,
        item.game_id,
        item.plate_appearance_id,
        _iso(item.event_at),
        _iso(item.eligible_at),
        item.value,
    )


def _as_utc(value: datetime | None) -> datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None
