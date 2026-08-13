"""Historical posterior orchestration around the Gate 2 Bayes helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from app.features.talent import TalentEstimate, estimate_platoon_talent, estimate_true_talent
from app.historical.policy import EvidenceClassification, EvidenceTier, tier_for_classification
from app.historical.temporal import as_utc


@dataclass(frozen=True)
class HistoricalTalentEvidence:
    value: float
    sample_size: float
    event_end_at: datetime
    eligible_at: datetime
    evidence_ids: tuple[str, ...]
    game_ids: tuple[str, ...] = ()
    classification: EvidenceClassification = (
        EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME
    )


@dataclass(frozen=True)
class HistoricalTalentPosterior:
    target_game_id: str
    cutoff: datetime
    estimate: TalentEstimate
    evidence_ids: tuple[str, ...]
    tier: EvidenceTier
    component_names: tuple[str, ...]
    method: str = "historical_empirical_bayes_orchestration_v1"


def build_historical_talent_posterior(
    *,
    target_game_id: str,
    cutoff: datetime,
    prior: HistoricalTalentEvidence,
    season: HistoricalTalentEvidence | None = None,
    recent: HistoricalTalentEvidence | None = None,
    season_evidence_multiplier: float = 1.0,
    recent_evidence_multiplier: float = 0.25,
    lower_bound: float = 0.0,
    upper_bound: float = 1.0,
) -> HistoricalTalentPosterior:
    """Validate point-in-time components, then delegate shrinkage to Gate 2."""

    if not target_game_id:
        raise ValueError("target_game_id is required")
    boundary = as_utc(cutoff)
    components = (("prior", prior), ("season", season), ("recent", recent))
    used: list[tuple[str, HistoricalTalentEvidence]] = []
    for name, component in components:
        if component is None:
            continue
        _validate_component(
            name,
            component,
            boundary=boundary,
            target_game_id=target_game_id,
            positive_sample=name == "prior",
        )
        if component.sample_size > 0:
            used.append((name, component))

    estimate = estimate_true_talent(
        prior=prior.value,
        prior_sample_size=prior.sample_size,
        season_value=season.value if season is not None else None,
        season_sample_size=season.sample_size if season is not None else 0.0,
        recent_value=recent.value if recent is not None else None,
        recent_sample_size=recent.sample_size if recent is not None else 0.0,
        season_evidence_multiplier=season_evidence_multiplier,
        recent_evidence_multiplier=recent_evidence_multiplier,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )
    evidence_ids = tuple(
        sorted({identifier for _, component in used for identifier in component.evidence_ids})
    )
    risk = {
        EvidenceTier.C: 0,
        EvidenceTier.A: 1,
        EvidenceTier.B: 2,
        EvidenceTier.EXCLUDED: 3,
    }
    tier = max(
        (tier_for_classification(component.classification) for _, component in used),
        key=risk.__getitem__,
    )
    return HistoricalTalentPosterior(
        target_game_id=target_game_id,
        cutoff=boundary,
        estimate=estimate,
        evidence_ids=evidence_ids,
        tier=tier,
        component_names=tuple(name for name, _ in used),
    )


def build_historical_platoon_posterior(
    *,
    overall: HistoricalTalentPosterior,
    split: HistoricalTalentEvidence | None,
    regression_sample_size: float = 80.0,
    split_evidence_multiplier: float = 1.0,
) -> HistoricalTalentPosterior:
    """Shrink an eligible historical handedness split toward overall talent."""

    if split is not None:
        _validate_component(
            "split",
            split,
            boundary=overall.cutoff,
            target_game_id=overall.target_game_id,
            positive_sample=False,
        )
    estimate = estimate_platoon_talent(
        overall_talent=overall.estimate.value,
        split_value=split.value if split is not None else None,
        split_sample_size=split.sample_size if split is not None else 0.0,
        regression_sample_size=regression_sample_size,
        split_evidence_multiplier=split_evidence_multiplier,
        lower_bound=overall.estimate.lower_bound,
        upper_bound=overall.estimate.upper_bound,
    )
    split_ids = split.evidence_ids if split is not None and split.sample_size > 0 else ()
    split_tier = (
        tier_for_classification(split.classification)
        if split is not None and split.sample_size > 0
        else EvidenceTier.A
    )
    risk = {
        EvidenceTier.C: 0,
        EvidenceTier.A: 1,
        EvidenceTier.B: 2,
        EvidenceTier.EXCLUDED: 3,
    }
    tier = max(
        (overall.tier, split_tier),
        key=risk.__getitem__,
    )
    return HistoricalTalentPosterior(
        target_game_id=overall.target_game_id,
        cutoff=overall.cutoff,
        estimate=estimate,
        evidence_ids=tuple(sorted(set(overall.evidence_ids) | set(split_ids))),
        tier=tier,
        component_names=overall.component_names + (("platoon_split",) if split_ids else ()),
        method="historical_platoon_empirical_bayes_orchestration_v1",
    )


def _validate_component(
    name: str,
    component: HistoricalTalentEvidence,
    *,
    boundary: datetime,
    target_game_id: str,
    positive_sample: bool,
) -> None:
    if not isfinite(component.value):
        raise ValueError(f"{name} value must be finite")
    if not isfinite(component.sample_size) or component.sample_size < 0:
        raise ValueError(f"{name} sample_size must be finite and non-negative")
    if positive_sample and component.sample_size <= 0:
        raise ValueError("Historical talent prior requires a positive sample size")
    if as_utc(component.event_end_at) >= boundary:
        raise ValueError(f"{name} evidence must end strictly before cutoff")
    if as_utc(component.eligible_at) > boundary:
        raise ValueError(f"{name} evidence was not eligible by cutoff")
    if as_utc(component.eligible_at) < as_utc(component.event_end_at):
        raise ValueError(f"{name} evidence cannot be eligible before its event end")
    if target_game_id in component.game_ids:
        raise ValueError(f"{name} evidence cannot reference the target game")
    if component.classification is EvidenceClassification.FORWARD_ONLY:
        raise ValueError(f"{name} forward-only evidence cannot enter a historical posterior")
    if not component.evidence_ids or any(not item for item in component.evidence_ids):
        raise ValueError(f"{name} evidence identifiers are required")
