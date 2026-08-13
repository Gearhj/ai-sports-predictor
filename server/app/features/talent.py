"""Transparent empirical-Bayes helpers for point-in-time MLB talent estimates.

These functions deliberately distinguish relatively stable season skill evidence
from noisier recent outcomes.  They do not contain streak, momentum, morale, or
other narrative bonuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class TalentEvidence:
    """One observable term in a true-talent posterior."""

    name: str
    value: float
    raw_sample_size: float
    evidence_multiplier: float
    effective_sample_size: float
    normalized_weight: float


@dataclass(frozen=True)
class TalentEstimate:
    """Estimate plus enough metadata to reproduce its shrinkage."""

    value: float
    prior: float
    lower_bound: float
    upper_bound: float
    total_effective_sample_size: float
    evidence: tuple[TalentEvidence, ...]
    recent_evidence_used: bool
    method: str = "empirical_bayes_weighted_evidence_v1"

    @property
    def weights(self) -> dict[str, float]:
        return {item.name: item.normalized_weight for item in self.evidence}


def estimate_true_talent(
    *,
    prior: float,
    prior_sample_size: float,
    season_value: float | None,
    season_sample_size: float = 0.0,
    recent_value: float | None = None,
    recent_sample_size: float = 0.0,
    season_evidence_multiplier: float = 1.0,
    recent_evidence_multiplier: float = 0.25,
    lower_bound: float = 0.0,
    upper_bound: float = 1.0,
) -> TalentEstimate:
    """Estimate underlying skill from a prior, season evidence, and recent form.

    Sample sizes are converted to effective samples by explicit multipliers.
    The default recent multiplier is intentionally smaller because a short run of
    outcomes is noisier than the underlying season skill estimate.  A missing or
    zero-sample observation contributes no weight; it is never replaced with an
    invented value.
    """

    _validate_bounds(lower_bound, upper_bound)
    _validate_value("prior", prior, lower_bound, upper_bound)
    _validate_sample("prior_sample_size", prior_sample_size, positive=True)
    _validate_multiplier("season_evidence_multiplier", season_evidence_multiplier)
    _validate_multiplier("recent_evidence_multiplier", recent_evidence_multiplier)

    terms: list[tuple[str, float, float, float]] = [
        ("prior", prior, prior_sample_size, 1.0),
    ]
    _append_optional_term(
        terms,
        name="season",
        value=season_value,
        sample_size=season_sample_size,
        multiplier=season_evidence_multiplier,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )
    _append_optional_term(
        terms,
        name="recent",
        value=recent_value,
        sample_size=recent_sample_size,
        multiplier=recent_evidence_multiplier,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )

    total_effective = sum(sample * multiplier for _, _, sample, multiplier in terms)
    posterior = sum(value * sample * multiplier for _, value, sample, multiplier in terms) / total_effective
    bounded = min(max(posterior, lower_bound), upper_bound)
    evidence = tuple(
        TalentEvidence(
            name=name,
            value=value,
            raw_sample_size=sample,
            evidence_multiplier=multiplier,
            effective_sample_size=sample * multiplier,
            normalized_weight=(sample * multiplier) / total_effective,
        )
        for name, value, sample, multiplier in terms
    )
    return TalentEstimate(
        value=bounded,
        prior=prior,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        total_effective_sample_size=total_effective,
        evidence=evidence,
        recent_evidence_used=any(item.name == "recent" for item in evidence),
    )


def estimate_platoon_talent(
    *,
    overall_talent: float,
    split_value: float | None,
    split_sample_size: float = 0.0,
    regression_sample_size: float = 80.0,
    split_evidence_multiplier: float = 1.0,
    lower_bound: float = 0.0,
    upper_bound: float = 1.0,
) -> TalentEstimate:
    """Shrink a handedness/platoon split toward established overall talent."""

    return estimate_true_talent(
        prior=overall_talent,
        prior_sample_size=regression_sample_size,
        season_value=split_value,
        season_sample_size=split_sample_size,
        season_evidence_multiplier=split_evidence_multiplier,
        recent_value=None,
        recent_sample_size=0.0,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )


def _append_optional_term(
    terms: list[tuple[str, float, float, float]],
    *,
    name: str,
    value: float | None,
    sample_size: float,
    multiplier: float,
    lower_bound: float,
    upper_bound: float,
) -> None:
    _validate_sample(f"{name}_sample_size", sample_size)
    if value is None or sample_size == 0 or multiplier == 0:
        return
    _validate_value(f"{name}_value", value, lower_bound, upper_bound)
    terms.append((name, value, sample_size, multiplier))


def _validate_bounds(lower_bound: float, upper_bound: float) -> None:
    if not isfinite(lower_bound) or not isfinite(upper_bound) or lower_bound >= upper_bound:
        raise ValueError("Bounds must be finite and lower_bound must be less than upper_bound")


def _validate_value(name: str, value: float, lower_bound: float, upper_bound: float) -> None:
    if not isfinite(value) or not lower_bound <= value <= upper_bound:
        raise ValueError(f"{name} must be finite and within [{lower_bound}, {upper_bound}]")


def _validate_sample(name: str, value: float, *, positive: bool = False) -> None:
    if not isfinite(value) or value < 0 or (positive and value == 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a finite {qualifier} value")


def _validate_multiplier(name: str, value: float) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
