"""Deterministic, target-safe historical feature snapshot materialization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Iterable, Mapping

from app.historical.policy import (
    EvidenceClassification,
    EvidenceTier,
    FamilyEvidence,
    QualityPolicy,
    QualityReport,
    ResearchProvenance,
    score_reconstruction_quality,
    tier_for_classification,
)
from app.historical.temporal import CUTOFF_POLICY_VERSION, as_utc, deterministic_cutoff


class EvidenceRole(StrEnum):
    STRUCTURAL = "STRUCTURAL"
    FEATURE = "FEATURE"
    TARGET_OUTCOME = "TARGET_OUTCOME"


@dataclass(frozen=True)
class HistoricalFeature:
    name: str
    family: str
    value: object


@dataclass(frozen=True)
class HistoricalEvidenceReference:
    evidence_id: str
    family: str
    source: str
    source_record_id: str
    raw_sha256: str
    game_id: str | None
    event_at: datetime
    eligible_at: datetime
    classification: EvidenceClassification
    role: EvidenceRole = EvidenceRole.FEATURE


@dataclass(frozen=True)
class HistoricalSnapshot:
    game_id: str
    scheduled_first_pitch: datetime
    cutoff: datetime
    cutoff_policy_version: str
    builder_version: str
    feature_schema_version: str
    feature_version_id: str
    provenance: ResearchProvenance
    tier: EvidenceTier
    quality: QualityReport
    features: tuple[HistoricalFeature, ...]
    evidence: tuple[HistoricalEvidenceReference, ...]
    missing_fields: tuple[str, ...]
    provisional_fields: tuple[str, ...]
    unavailable_families: tuple[str, ...]
    timing_risk_families: tuple[str, ...]
    canonical_payload_json: str
    checksum: str
    method: str = "historical_snapshot_canonical_sha256_v1"

    def to_dict(self) -> dict[str, object]:
        payload = json.loads(self.canonical_payload_json)
        payload["checksum"] = self.checksum
        payload["method"] = self.method
        return payload


def build_historical_snapshot(
    *,
    game_id: str,
    scheduled_first_pitch: datetime,
    builder_version: str,
    feature_schema_version: str,
    feature_version_id: str,
    features: Iterable[HistoricalFeature],
    evidence: Iterable[HistoricalEvidenceReference],
    quality_policy: QualityPolicy,
    family_evidence: Iterable[FamilyEvidence],
    missing_fields: Iterable[str] = (),
    provisional_fields: Iterable[str] = (),
    unavailable_families: Iterable[str] = (),
    timing_risk_families: Iterable[str] = (),
    provenance: ResearchProvenance = ResearchProvenance.HISTORICAL_RECONSTRUCTION,
    cutoff_policy_version: str = CUTOFF_POLICY_VERSION,
) -> HistoricalSnapshot:
    """Build an order-independent snapshot whose checksum binds its policy.

    Target outcomes and target-game feature events are rejected.  A target-game
    schedule or venue observation is allowed only as ``STRUCTURAL`` evidence
    when it was itself temporally eligible before the deterministic cutoff.
    """

    _require_text("game_id", game_id)
    _require_text("builder_version", builder_version)
    _require_text("feature_schema_version", feature_schema_version)
    _require_text("feature_version_id", feature_version_id)
    if provenance is not ResearchProvenance.HISTORICAL_RECONSTRUCTION:
        raise ValueError("Historical snapshots require HISTORICAL_RECONSTRUCTION provenance")
    if cutoff_policy_version != CUTOFF_POLICY_VERSION:
        raise ValueError(f"Unsupported historical cutoff policy: {cutoff_policy_version}")

    first_pitch = as_utc(scheduled_first_pitch)
    cutoff = deterministic_cutoff(first_pitch)
    feature_rows = tuple(features)
    evidence_rows = tuple(evidence)
    family_rows = tuple(family_evidence)
    _validate_features(feature_rows)
    _validate_evidence(
        evidence_rows,
        target_game_id=game_id,
        cutoff=cutoff,
    )
    quality = score_reconstruction_quality(quality_policy, family_rows)
    _validate_family_claims(feature_rows, evidence_rows, family_rows)

    ordered_features = tuple(sorted(feature_rows, key=lambda item: (item.name, item.family)))
    ordered_evidence = tuple(
        sorted(
            evidence_rows,
            key=lambda item: (
                item.family,
                item.source,
                item.source_record_id,
                item.evidence_id,
            ),
        )
    )
    explicit_missing = _validated_names("missing_fields", missing_fields)
    explicit_provisional = _validated_names("provisional_fields", provisional_fields)
    explicit_unavailable = _validated_names("unavailable_families", unavailable_families)
    explicit_timing = _validated_names("timing_risk_families", timing_risk_families)
    missing = tuple(
        sorted(
            set(explicit_missing)
            | {item.name for item in ordered_features if item.value is None}
        )
    )
    provisional_families = {item.family for item in family_rows if item.provisional}
    provisional = tuple(
        sorted(
            set(explicit_provisional)
            | {item.name for item in ordered_features if item.family in provisional_families}
        )
    )
    excluded_families = {
        component.family
        for component in quality.components
        if component.effective_tier is EvidenceTier.EXCLUDED
    }
    unavailable = tuple(sorted(set(explicit_unavailable) | excluded_families))
    timing_risk = tuple(
        sorted(set(explicit_timing) | set(quality.timing_risk_families))
    )

    payload = {
        "game_id": game_id,
        "scheduled_first_pitch": _timestamp(first_pitch),
        "cutoff": _timestamp(cutoff),
        "cutoff_policy_version": cutoff_policy_version,
        "builder_version": builder_version,
        "feature_schema_version": feature_schema_version,
        "feature_version_id": feature_version_id,
        "provenance": provenance.value,
        "tier": quality.tier.value,
        "quality_policy": {
            "version": quality_policy.version,
            "rounding_digits": quality_policy.rounding_digits,
            "tier_credits": {
                "A": quality_policy.tier_a_credit,
                "B": quality_policy.tier_b_credit,
                "C": quality_policy.tier_c_credit,
                "EXCLUDED": quality_policy.excluded_credit,
            },
            "family_weights": [
                {
                    "family": item.family,
                    "weight": item.weight,
                    "critical": item.critical,
                }
                for item in sorted(quality_policy.family_weights, key=lambda value: value.family)
            ],
        },
        "quality": quality.to_dict(),
        "features": [
            {
                "name": item.name,
                "family": item.family,
                "value": _canonical_value(item.value),
            }
            for item in ordered_features
        ],
        "evidence": [_evidence_dict(item) for item in ordered_evidence],
        "missing_fields": list(missing),
        "provisional_fields": list(provisional),
        "unavailable_families": list(unavailable),
        "timing_risk_families": list(timing_risk),
    }
    canonical = canonical_json(payload)
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return HistoricalSnapshot(
        game_id=game_id,
        scheduled_first_pitch=first_pitch,
        cutoff=cutoff,
        cutoff_policy_version=cutoff_policy_version,
        builder_version=builder_version,
        feature_schema_version=feature_schema_version,
        feature_version_id=feature_version_id,
        provenance=provenance,
        tier=quality.tier,
        quality=quality,
        features=ordered_features,
        evidence=ordered_evidence,
        missing_fields=missing,
        provisional_fields=provisional,
        unavailable_families=unavailable,
        timing_risk_families=timing_risk,
        canonical_payload_json=canonical,
        checksum=checksum,
    )


def canonical_json(value: object) -> str:
    """Return strict canonical JSON suitable for a semantic SHA-256 checksum."""

    canonical = _canonical_value(value)
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _validate_features(rows: tuple[HistoricalFeature, ...]) -> None:
    names = [item.name for item in rows]
    if len(names) != len(set(names)):
        raise ValueError("Historical feature names must be unique")
    for item in rows:
        _require_text("feature name", item.name)
        _require_text("feature family", item.family)
        _canonical_value(item.value)


def _validate_evidence(
    rows: tuple[HistoricalEvidenceReference, ...],
    *,
    target_game_id: str,
    cutoff: datetime,
) -> None:
    identifiers = [item.evidence_id for item in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Historical evidence identifiers must be unique")
    for item in rows:
        for name, value in (
            ("evidence_id", item.evidence_id),
            ("family", item.family),
            ("source", item.source),
            ("source_record_id", item.source_record_id),
        ):
            _require_text(name, value)
        if not _is_sha256(item.raw_sha256):
            raise ValueError("raw_sha256 must be a 64-character hexadecimal SHA-256")
        event_at = as_utc(item.event_at)
        eligible_at = as_utc(item.eligible_at)
        if event_at >= cutoff:
            raise ValueError("Historical evidence must occur strictly before cutoff")
        if eligible_at > cutoff:
            raise ValueError("Historical evidence was not eligible by cutoff")
        if eligible_at < event_at:
            raise ValueError("Historical evidence cannot be eligible before it occurred")
        if item.role is EvidenceRole.TARGET_OUTCOME:
            raise ValueError("Target outcomes cannot enter a feature snapshot")
        if item.role is EvidenceRole.FEATURE and item.game_id == target_game_id:
            raise ValueError("Target-game events cannot enter feature evidence")
        if (
            item.role is EvidenceRole.FEATURE
            and item.classification is EvidenceClassification.FORWARD_ONLY
        ):
            raise ValueError("Forward-only evidence cannot populate a historical feature")


def _validate_family_claims(
    features: tuple[HistoricalFeature, ...],
    evidence: tuple[HistoricalEvidenceReference, ...],
    family_rows: tuple[FamilyEvidence, ...],
) -> None:
    claims = {item.family: item for item in family_rows}
    if len(claims) != len(family_rows):
        raise ValueError("Family evidence claims must be unique")
    rank = {
        EvidenceTier.C: 0,
        EvidenceTier.A: 1,
        EvidenceTier.B: 2,
        EvidenceTier.EXCLUDED: 3,
    }
    for feature in features:
        claim = claims.get(feature.family)
        if claim is None:
            raise ValueError(f"Feature family lacks a quality claim: {feature.family}")
        if feature.value is not None and (
            not claim.present
            or not claim.timing_valid
            or claim.neutral_fallback
            or claim.classification is EvidenceClassification.FORWARD_ONLY
        ):
            raise ValueError(f"Unavailable family cannot expose a value: {feature.family}")
    for family, claim in claims.items():
        references = [item for item in evidence if item.family == family]
        if not references or not claim.present:
            continue
        worst_reference = max(
            (tier_for_classification(item.classification) for item in references),
            key=rank.__getitem__,
        )
        if rank[tier_for_classification(claim.classification)] < rank[worst_reference]:
            raise ValueError(f"Family claim is stronger than its evidence: {family}")


def _evidence_dict(item: HistoricalEvidenceReference) -> dict[str, object]:
    return {
        "evidence_id": item.evidence_id,
        "family": item.family,
        "source": item.source,
        "source_record_id": item.source_record_id,
        "raw_sha256": item.raw_sha256.lower(),
        "game_id": item.game_id,
        "event_at": _timestamp(as_utc(item.event_at)),
        "eligible_at": _timestamp(as_utc(item.eligible_at)),
        "classification": item.classification.value,
        "tier": tier_for_classification(item.classification).value,
        "role": item.role.value,
    }


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Canonical feature values must be finite")
        return 0.0 if value == 0 else value
    if isinstance(value, datetime):
        return _timestamp(as_utc(value))
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Canonical mapping keys must be strings")
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise ValueError(f"Unsupported canonical value type: {type(value).__name__}")


def _validated_names(name: str, values: Iterable[str]) -> tuple[str, ...]:
    result = tuple(values)
    if any(not item for item in result):
        raise ValueError(f"{name} cannot contain blank values")
    return tuple(sorted(set(result)))


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)


def _require_text(name: str, value: str) -> None:
    if not value:
        raise ValueError(f"{name} is required")
