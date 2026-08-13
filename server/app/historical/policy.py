"""Pure Gate 3A provenance, evidence-tier, and quality policy contracts.

Operational provenance is deliberately separate from the temporal quality of
an individual feature family.  A historical record must never be relabelled as
prospectively captured ``LIVE`` evidence merely because it is reconstructable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Iterable


class ResearchProvenance(StrEnum):
    """How a record entered ASP, not how temporally reliable it is."""

    LIVE = "LIVE"
    DEMO = "DEMO"
    HISTORICAL_RECONSTRUCTION = "HISTORICAL_RECONSTRUCTION"
    BACKTEST = "BACKTEST"


class EvidenceClassification(StrEnum):
    """Temporal meaning of one source-derived feature family."""

    RECONSTRUCTABLE_POINT_IN_TIME = "RECONSTRUCTABLE_POINT_IN_TIME"
    PARTIALLY_RECONSTRUCTABLE = "PARTIALLY_RECONSTRUCTABLE"
    FORWARD_ONLY = "FORWARD_ONLY"


class EvidenceTier(StrEnum):
    """Research population, with exclusion kept distinct from Tier C.

    Tier C is the forward, prospectively captured gold standard.  It is not a
    label for unavailable retrospective evidence.
    """

    A = "A"
    B = "B"
    C = "C"
    EXCLUDED = "EXCLUDED"


_TIER_RISK = {
    EvidenceTier.C: 0,
    EvidenceTier.A: 1,
    EvidenceTier.B: 2,
    EvidenceTier.EXCLUDED: 3,
}


def tier_for_classification(classification: EvidenceClassification) -> EvidenceTier:
    """Map retrospective evidence to Tier A/B or the excluded state."""

    return {
        EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME: EvidenceTier.A,
        EvidenceClassification.PARTIALLY_RECONSTRUCTABLE: EvidenceTier.B,
        EvidenceClassification.FORWARD_ONLY: EvidenceTier.EXCLUDED,
    }[classification]


def tier_for_evidence(
    classification: EvidenceClassification,
    provenance: ResearchProvenance,
) -> EvidenceTier:
    """Resolve tier from independent temporal and acquisition dimensions."""

    if provenance in {ResearchProvenance.DEMO, ResearchProvenance.BACKTEST}:
        return EvidenceTier.EXCLUDED
    if classification is EvidenceClassification.FORWARD_ONLY:
        return EvidenceTier.EXCLUDED
    if provenance is ResearchProvenance.LIVE:
        return (
            EvidenceTier.C
            if classification is EvidenceClassification.RECONSTRUCTABLE_POINT_IN_TIME
            else EvidenceTier.B
        )
    return tier_for_classification(classification)


@dataclass(frozen=True)
class FamilyWeight:
    family: str
    weight: float
    critical: bool = False


@dataclass(frozen=True)
class FamilyEvidence:
    """Evidence supplied for one expected feature family.

    ``coverage`` and ``freshness`` are explicit bounded multipliers.  Missing,
    timing-invalid, forward-only, and neutral fallback evidence receives zero
    quality credit.  Provisional evidence can only downgrade Tier A to Tier B.
    """

    family: str
    classification: EvidenceClassification
    provenance: ResearchProvenance = ResearchProvenance.HISTORICAL_RECONSTRUCTION
    coverage: float = 1.0
    freshness: float = 1.0
    present: bool = True
    provisional: bool = False
    neutral_fallback: bool = False
    timing_valid: bool = True


@dataclass(frozen=True)
class QualityPolicy:
    family_weights: tuple[FamilyWeight, ...]
    tier_a_credit: float = 1.0
    tier_b_credit: float = 0.5
    tier_c_credit: float = 1.0
    excluded_credit: float = 0.0
    version: str = "gate3a_quality_v1"
    rounding_digits: int = 12


@dataclass(frozen=True)
class QualityComponent:
    family: str
    weight: float
    classification: EvidenceClassification | None
    provenance: ResearchProvenance | None
    effective_tier: EvidenceTier
    coverage: float
    freshness: float
    credit: float
    weighted_credit: float
    present: bool
    provisional: bool
    neutral_fallback: bool
    timing_valid: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "weight": self.weight,
            "classification": self.classification.value if self.classification else None,
            "provenance": self.provenance.value if self.provenance else None,
            "effective_tier": self.effective_tier.value,
            "coverage": self.coverage,
            "freshness": self.freshness,
            "credit": self.credit,
            "weighted_credit": self.weighted_credit,
            "present": self.present,
            "provisional": self.provisional,
            "neutral_fallback": self.neutral_fallback,
            "timing_valid": self.timing_valid,
        }


@dataclass(frozen=True)
class QualityReport:
    score: float
    score_fraction: float
    tier: EvidenceTier
    components: tuple[QualityComponent, ...]
    missing_families: tuple[str, ...]
    timing_risk_families: tuple[str, ...]
    policy_version: str
    method: str = "fixed_weight_monotonic_evidence_quality_v1"

    def to_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "score_fraction": self.score_fraction,
            "tier": self.tier.value,
            "components": [component.to_dict() for component in self.components],
            "missing_families": list(self.missing_families),
            "timing_risk_families": list(self.timing_risk_families),
            "policy_version": self.policy_version,
            "method": self.method,
        }


def score_reconstruction_quality(
    policy: QualityPolicy,
    evidence: Iterable[FamilyEvidence],
) -> QualityReport:
    """Score expected families with monotonic downgrade/removal behavior.

    All expected families remain in the denominator.  Consequently removing a
    family, reducing coverage/freshness, making evidence provisional, or
    downgrading its classification cannot increase the result.
    """

    weights = _validated_weights(policy)
    supplied = tuple(evidence)
    by_family: dict[str, FamilyEvidence] = {}
    for item in supplied:
        _validate_family_evidence(item)
        if item.family in by_family:
            raise ValueError(f"Duplicate family evidence: {item.family}")
        if item.family not in weights:
            raise ValueError(f"Evidence family is not defined by policy: {item.family}")
        by_family[item.family] = item

    tier_credits = _validated_credits(policy)
    components: list[QualityComponent] = []
    for family in sorted(weights):
        definition = weights[family]
        item = by_family.get(family)
        if item is None:
            components.append(
                QualityComponent(
                    family=family,
                    weight=definition.weight,
                    classification=None,
                    provenance=None,
                    effective_tier=EvidenceTier.EXCLUDED,
                    coverage=0.0,
                    freshness=0.0,
                    credit=0.0,
                    weighted_credit=0.0,
                    present=False,
                    provisional=False,
                    neutral_fallback=False,
                    timing_valid=False,
                )
            )
            continue

        tier = _effective_tier(item)
        eligible = item.present and item.timing_valid and not item.neutral_fallback
        coverage = item.coverage if eligible else 0.0
        freshness = item.freshness if eligible else 0.0
        credit = tier_credits[tier] * coverage * freshness
        components.append(
            QualityComponent(
                family=family,
                weight=definition.weight,
                classification=item.classification,
                provenance=item.provenance,
                effective_tier=tier,
                coverage=coverage,
                freshness=freshness,
                credit=credit,
                weighted_credit=definition.weight * credit,
                present=item.present,
                provisional=item.provisional,
                neutral_fallback=item.neutral_fallback,
                timing_valid=item.timing_valid,
            )
        )

    denominator = sum(component.weight for component in components)
    score_fraction = round(
        sum(component.weighted_credit for component in components) / denominator,
        policy.rounding_digits,
    )
    score = round(score_fraction * 100.0, policy.rounding_digits)
    critical = {
        family
        for family, definition in weights.items()
        if definition.critical
    } or set(weights)
    report_tier = _aggregate_tier(
        component.effective_tier for component in components if component.family in critical
    )
    missing = tuple(sorted(component.family for component in components if not component.present))
    timing_risk = tuple(
        sorted(
            component.family
            for component in components
            if not component.timing_valid
            or component.neutral_fallback
            or component.provisional
            or component.effective_tier not in {EvidenceTier.A, EvidenceTier.C}
        )
    )
    return QualityReport(
        score=score,
        score_fraction=score_fraction,
        tier=report_tier,
        components=tuple(components),
        missing_families=missing,
        timing_risk_families=timing_risk,
        policy_version=policy.version,
    )


def _effective_tier(item: FamilyEvidence) -> EvidenceTier:
    if not item.present or not item.timing_valid or item.neutral_fallback:
        return EvidenceTier.EXCLUDED
    tier = tier_for_evidence(item.classification, item.provenance)
    if item.provisional and tier in {EvidenceTier.A, EvidenceTier.C}:
        return EvidenceTier.B
    return tier


def _aggregate_tier(tiers: Iterable[EvidenceTier]) -> EvidenceTier:
    rows = tuple(tiers)
    if not rows:
        return EvidenceTier.EXCLUDED
    if EvidenceTier.EXCLUDED in rows:
        return EvidenceTier.EXCLUDED
    if EvidenceTier.B in rows:
        return EvidenceTier.B
    if all(item is EvidenceTier.C for item in rows):
        return EvidenceTier.C
    return EvidenceTier.A


def _validated_weights(policy: QualityPolicy) -> dict[str, FamilyWeight]:
    if not policy.version:
        raise ValueError("Quality policy version is required")
    if policy.rounding_digits < 0 or policy.rounding_digits > 15:
        raise ValueError("rounding_digits must be between 0 and 15")
    result: dict[str, FamilyWeight] = {}
    for item in policy.family_weights:
        if not item.family:
            raise ValueError("Quality family name is required")
        if item.family in result:
            raise ValueError(f"Duplicate family weight: {item.family}")
        if not isfinite(item.weight) or item.weight <= 0:
            raise ValueError("Family weights must be finite and positive")
        result[item.family] = item
    if not result:
        raise ValueError("Quality policy must define at least one family")
    return result


def _validated_credits(policy: QualityPolicy) -> dict[EvidenceTier, float]:
    credits = {
        EvidenceTier.A: policy.tier_a_credit,
        EvidenceTier.B: policy.tier_b_credit,
        EvidenceTier.C: policy.tier_c_credit,
        EvidenceTier.EXCLUDED: policy.excluded_credit,
    }
    if any(not isfinite(value) or value < 0 or value > 1 for value in credits.values()):
        raise ValueError("Tier credits must be finite and within [0, 1]")
    if not (
        credits[EvidenceTier.C]
        >= credits[EvidenceTier.A]
        >= credits[EvidenceTier.B]
        >= credits[EvidenceTier.EXCLUDED]
    ):
        raise ValueError("Tier credits must be monotonic C >= A >= B >= EXCLUDED")
    if credits[EvidenceTier.EXCLUDED] != 0:
        raise ValueError("Excluded evidence must receive zero quality credit")
    return credits


def _validate_family_evidence(item: FamilyEvidence) -> None:
    if not item.family:
        raise ValueError("Evidence family name is required")
    for name, value in (("coverage", item.coverage), ("freshness", item.freshness)):
        if not isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be finite and within [0, 1]")
