"""Frozen Gate 3C plate-appearance outcome taxonomy.

This module is deliberately provider-free.  It defines the modeled target and
the only exceptional terminal value that may be excluded.  A ``truncated_pa``
row is not a catch-all for bad source data: it is admissible only when official
MLB play-by-play proves that a runner made the third out before the batter
completed a plate appearance.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from types import MappingProxyType


PA_OUTCOME_TAXONOMY_VERSION = "mlb_gen2_pa_outcome_taxonomy_v1"


class TaxonomyValidationError(ValueError):
    """A source terminal value cannot enter the frozen Gen-2 taxonomy."""


class PlateAppearanceOutcome(str, Enum):
    """The eight ordered outcome classes frozen for the initial Gen-2 model."""

    STRIKEOUT = "STRIKEOUT"
    WALK_HBP_CI = "WALK_HBP_CI"
    HOME_RUN = "HOME_RUN"
    SINGLE = "SINGLE"
    DOUBLE = "DOUBLE"
    TRIPLE = "TRIPLE"
    BIP_OUT = "BIP_OUT"
    OTHER_REACH = "OTHER_REACH"


_ORDERED_OUTCOMES = tuple(PlateAppearanceOutcome)


def ordered_outcomes() -> tuple[PlateAppearanceOutcome, ...]:
    """Return the immutable class order used by matrices and probability vectors."""

    return _ORDERED_OUTCOMES


SOURCE_TOKEN_TO_OUTCOME: Mapping[str, PlateAppearanceOutcome] = MappingProxyType({
    "strikeout": PlateAppearanceOutcome.STRIKEOUT,
    "strikeout_double_play": PlateAppearanceOutcome.STRIKEOUT,
    "walk": PlateAppearanceOutcome.WALK_HBP_CI,
    "intent_walk": PlateAppearanceOutcome.WALK_HBP_CI,
    "hit_by_pitch": PlateAppearanceOutcome.WALK_HBP_CI,
    "catcher_interf": PlateAppearanceOutcome.WALK_HBP_CI,
    "home_run": PlateAppearanceOutcome.HOME_RUN,
    "single": PlateAppearanceOutcome.SINGLE,
    "double": PlateAppearanceOutcome.DOUBLE,
    "triple": PlateAppearanceOutcome.TRIPLE,
    "field_out": PlateAppearanceOutcome.BIP_OUT,
    "force_out": PlateAppearanceOutcome.BIP_OUT,
    "grounded_into_double_play": PlateAppearanceOutcome.BIP_OUT,
    "double_play": PlateAppearanceOutcome.BIP_OUT,
    "sac_fly": PlateAppearanceOutcome.BIP_OUT,
    "sac_bunt": PlateAppearanceOutcome.BIP_OUT,
    "field_error": PlateAppearanceOutcome.OTHER_REACH,
    "fielders_choice": PlateAppearanceOutcome.OTHER_REACH,
})

# These are official runner events that can end an inning while the batter's
# count is still live.  They are not modeled batter outcomes.
MLB_OFFICIAL_EARLY_PA_TERMINATION_TOKENS = frozenset({
    "caught_stealing_2b",
    "caught_stealing_3b",
    "caught_stealing_home",
    "pickoff_1b",
    "pickoff_2b",
    "pickoff_3b",
    "pickoff_caught_stealing_2b",
    "pickoff_caught_stealing_3b",
    "pickoff_caught_stealing_home",
})

TRUNCATED_PA_SOURCE_TOKEN = "truncated_pa"
TRUNCATED_PA_EXCLUSION_REASON = "OFFICIAL_RUNNER_OUT_ENDED_INNING_BEFORE_PA_TERMINAL"

# Exact category counts from the retained 2025-06-17 archive.  They are a
# reconciliation fixture, not a multi-season prevalence estimate.
RETAINED_2025_06_17_CATEGORY_COUNTS: Mapping[PlateAppearanceOutcome, int] = (
    MappingProxyType({
        PlateAppearanceOutcome.STRIKEOUT: 237,
        PlateAppearanceOutcome.WALK_HBP_CI: 122,
        PlateAppearanceOutcome.HOME_RUN: 43,
        PlateAppearanceOutcome.SINGLE: 151,
        PlateAppearanceOutcome.DOUBLE: 51,
        PlateAppearanceOutcome.TRIPLE: 3,
        PlateAppearanceOutcome.BIP_OUT: 524,
        PlateAppearanceOutcome.OTHER_REACH: 8,
    })
)
RETAINED_2025_06_17_TRUNCATED_PA_COUNT = 1


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def taxonomy_definition() -> dict[str, object]:
    """Return the complete semantic definition used for content identity."""

    return {
        "ordered_outcomes": [outcome.value for outcome in ordered_outcomes()],
        "source_mapping": {
            token: outcome.value for token, outcome in sorted(SOURCE_TOKEN_TO_OUTCOME.items())
        },
        "truncated_pa": {
            "modeled": False,
            "source_token": TRUNCATED_PA_SOURCE_TOKEN,
            "required_official_mlb_events": sorted(
                MLB_OFFICIAL_EARLY_PA_TERMINATION_TOKENS
            ),
            "rule": TRUNCATED_PA_EXCLUSION_REASON,
        },
        "version": PA_OUTCOME_TAXONOMY_VERSION,
    }


PA_OUTCOME_TAXONOMY_IDENTITY = sha256(
    _canonical_json(taxonomy_definition()).encode("utf-8")
).hexdigest()


@dataclass(frozen=True, slots=True)
class TruncatedPlateAppearanceEvidence:
    """Exact MLB evidence required to exclude one non-terminal batter sequence."""

    mlb_result_token: str
    official_game_final: bool
    play_complete: bool
    outs_before: int
    outs_after: int
    runner_out_recorded: bool
    batter_has_terminal_result: bool

    def __post_init__(self) -> None:
        token = normalize_source_token(self.mlb_result_token)
        object.__setattr__(self, "mlb_result_token", token)
        if not 0 <= self.outs_before <= 2:
            raise TaxonomyValidationError("Truncated PA outs_before must be in 0..2")
        if not 0 <= self.outs_after <= 3:
            raise TaxonomyValidationError("Truncated PA outs_after must be in 0..3")

    @property
    def proves_official_early_termination(self) -> bool:
        return (
            self.mlb_result_token in MLB_OFFICIAL_EARLY_PA_TERMINATION_TOKENS
            and self.official_game_final
            and self.play_complete
            and self.runner_out_recorded
            and not self.batter_has_terminal_result
            and self.outs_after == 3
            and self.outs_after > self.outs_before
        )

    def validate(self) -> None:
        if not self.proves_official_early_termination:
            raise TaxonomyValidationError(
                "truncated_pa lacks proven official runner-out early termination"
            )


@dataclass(frozen=True, slots=True)
class TaxonomyObservation:
    """One provider terminal token and any evidence needed for exclusion."""

    source_token: str
    truncated_evidence: TruncatedPlateAppearanceEvidence | None = None


@dataclass(frozen=True, slots=True)
class TaxonomyCountReport:
    taxonomy_version: str
    taxonomy_identity: str
    source_counts: Mapping[str, int]
    category_counts: Mapping[PlateAppearanceOutcome, int]
    modeled_count: int
    excluded_truncated_pa_count: int
    total_observation_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "taxonomy_version": self.taxonomy_version,
            "taxonomy_identity": self.taxonomy_identity,
            "source_counts": dict(sorted(self.source_counts.items())),
            "category_counts": {
                outcome.value: int(self.category_counts.get(outcome, 0))
                for outcome in ordered_outcomes()
            },
            "modeled_count": self.modeled_count,
            "excluded_truncated_pa_count": self.excluded_truncated_pa_count,
            "total_observation_count": self.total_observation_count,
        }


def normalize_source_token(value: object) -> str:
    token = str(value or "").strip().lower()
    if not token:
        raise TaxonomyValidationError("Plate-appearance terminal source token is required")
    return token


def classify_terminal_result(
    source_token: object,
    *,
    truncated_evidence: TruncatedPlateAppearanceEvidence | None = None,
) -> PlateAppearanceOutcome | None:
    """Map one exact provider terminal token or prove its sole allowed exclusion."""

    token = normalize_source_token(source_token)
    outcome = SOURCE_TOKEN_TO_OUTCOME.get(token)
    if outcome is not None:
        if truncated_evidence is not None:
            raise TaxonomyValidationError(
                "Truncated-PA evidence cannot accompany a modeled terminal result"
            )
        return outcome
    if token == TRUNCATED_PA_SOURCE_TOKEN:
        if truncated_evidence is None:
            raise TaxonomyValidationError(
                "truncated_pa requires exact official MLB early-termination evidence"
            )
        truncated_evidence.validate()
        return None
    raise TaxonomyValidationError(f"Unmapped plate-appearance terminal token: {token}")


def validate_official_early_termination(
    evidence: TruncatedPlateAppearanceEvidence,
) -> None:
    """Validate an MLB non-batter terminal before Statcast may call it truncated."""

    evidence.validate()


def validate_taxonomy_counts(
    observations: Iterable[str | TaxonomyObservation],
    *,
    expected_category_counts: Mapping[PlateAppearanceOutcome | str, int] | None = None,
    expected_truncated_pa_count: int | None = None,
) -> TaxonomyCountReport:
    """Classify a partition and optionally enforce exact per-category counts.

    Unknown tokens, unproven ``truncated_pa`` rows, missing expected classes, and
    surplus rows all fail closed.  Passing no expected counts performs strict
    mapping validation without asserting a particular population size.
    """

    source_counts: Counter[str] = Counter()
    category_counts: Counter[PlateAppearanceOutcome] = Counter()
    truncated_count = 0
    total = 0
    for raw in observations:
        observation = raw if isinstance(raw, TaxonomyObservation) else TaxonomyObservation(raw)
        token = normalize_source_token(observation.source_token)
        outcome = classify_terminal_result(
            token,
            truncated_evidence=observation.truncated_evidence,
        )
        source_counts[token] += 1
        total += 1
        if outcome is None:
            truncated_count += 1
        else:
            category_counts[outcome] += 1

    if expected_category_counts is not None:
        normalized_expected: dict[PlateAppearanceOutcome, int] = {}
        for raw_outcome, raw_count in expected_category_counts.items():
            try:
                outcome = (
                    raw_outcome
                    if isinstance(raw_outcome, PlateAppearanceOutcome)
                    else PlateAppearanceOutcome(str(raw_outcome))
                )
            except ValueError as exc:
                raise TaxonomyValidationError(
                    f"Unknown expected taxonomy class: {raw_outcome}"
                ) from exc
            count = int(raw_count)
            if count < 0:
                raise TaxonomyValidationError("Expected taxonomy counts cannot be negative")
            if outcome in normalized_expected:
                raise TaxonomyValidationError(f"Duplicate expected taxonomy class: {outcome}")
            normalized_expected[outcome] = count
        if set(normalized_expected) != set(ordered_outcomes()):
            missing = sorted(
                outcome.value
                for outcome in set(ordered_outcomes()).difference(normalized_expected)
            )
            raise TaxonomyValidationError(
                f"Expected taxonomy counts must specify all eight classes; missing={missing}"
            )
        actual = {outcome: category_counts[outcome] for outcome in ordered_outcomes()}
        if actual != normalized_expected:
            differences = {
                outcome.value: {
                    "actual": actual[outcome],
                    "expected": normalized_expected[outcome],
                }
                for outcome in ordered_outcomes()
                if actual[outcome] != normalized_expected[outcome]
            }
            raise TaxonomyValidationError(f"Taxonomy category counts differ: {differences}")

    if expected_truncated_pa_count is not None:
        expected = int(expected_truncated_pa_count)
        if expected < 0:
            raise TaxonomyValidationError("Expected truncated_pa count cannot be negative")
        if truncated_count != expected:
            raise TaxonomyValidationError(
                "Taxonomy truncated_pa count differs: "
                f"actual={truncated_count}, expected={expected}"
            )

    return TaxonomyCountReport(
        taxonomy_version=PA_OUTCOME_TAXONOMY_VERSION,
        taxonomy_identity=PA_OUTCOME_TAXONOMY_IDENTITY,
        source_counts=MappingProxyType(dict(sorted(source_counts.items()))),
        category_counts=MappingProxyType({
            outcome: category_counts[outcome] for outcome in ordered_outcomes()
        }),
        modeled_count=sum(category_counts.values()),
        excluded_truncated_pa_count=truncated_count,
        total_observation_count=total,
    )


def validate_retained_2025_06_17_counts(
    observations: Iterable[str | TaxonomyObservation],
) -> TaxonomyCountReport:
    """Apply the exact retained one-day reconciliation fixture."""

    return validate_taxonomy_counts(
        observations,
        expected_category_counts=RETAINED_2025_06_17_CATEGORY_COUNTS,
        expected_truncated_pa_count=RETAINED_2025_06_17_TRUNCATED_PA_COUNT,
    )


__all__ = [
    "MLB_OFFICIAL_EARLY_PA_TERMINATION_TOKENS",
    "PA_OUTCOME_TAXONOMY_IDENTITY",
    "PA_OUTCOME_TAXONOMY_VERSION",
    "PlateAppearanceOutcome",
    "RETAINED_2025_06_17_CATEGORY_COUNTS",
    "RETAINED_2025_06_17_TRUNCATED_PA_COUNT",
    "SOURCE_TOKEN_TO_OUTCOME",
    "TRUNCATED_PA_EXCLUSION_REASON",
    "TRUNCATED_PA_SOURCE_TOKEN",
    "TaxonomyCountReport",
    "TaxonomyObservation",
    "TaxonomyValidationError",
    "TruncatedPlateAppearanceEvidence",
    "classify_terminal_result",
    "normalize_source_token",
    "ordered_outcomes",
    "taxonomy_definition",
    "validate_official_early_termination",
    "validate_retained_2025_06_17_counts",
    "validate_taxonomy_counts",
]
