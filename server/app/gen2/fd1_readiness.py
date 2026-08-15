"""Provider-free Gate 3C-FD1 reconciliation and data-readiness audit.

The audit is intentionally a pure function over compact, caller-supplied
summaries.  It does not know how to connect to a provider, a database, or the
filesystem.  That boundary lets the acquisition/import pipeline persist its
own evidence and then pass an immutable projection here for a fail-closed
decision.

The three terminal decisions are deliberately narrow:

* ``READY_FOR_GEN2_FREE_DATA_MODELING`` means every hard gate and configured
  sufficiency threshold passed with complete required-family coverage.
* ``READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS`` means training can proceed,
  but at least one non-critical free-data family is incomplete.
* ``NOT_READY_DATA_INTEGRITY_FAILURE`` means an integrity gate failed or a
  required training population cannot yet be formed.

No decision in this module fits, promotes, or deploys a model.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from enum import Enum
from math import isfinite
import re
from typing import Any, Iterable

from app.gen2.event_taxonomy import PlateAppearanceOutcome, ordered_outcomes


ASP_2025_CANONICAL_REGULAR_SEASON_FINALS = 2_430
REQUIRED_SEASONS = (2021, 2022, 2023, 2024, 2025)
PRIMARY_SEASONS = (2023, 2024, 2025)
PRIOR_ONLY_SEASONS = (2021, 2022)
PA_OUTCOME_NAMES = tuple(outcome.value for outcome in ordered_outcomes())
TIER_PROJECTION_UNIT = "DISJOINT_TRAINING_ELIGIBILITY_ROWS"

# These are measurements that Retrosheet/Lahman/Chadwick/NOAA do not create.
# Keeping the list frozen prevents a basic event field from being relabeled as
# Statcast merely to make a coverage report look better.
STATCAST_TRACKING_GAPS = (
    "pitch_velocity",
    "pitch_spin",
    "pitch_movement",
    "pitch_location",
    "launch_speed",
    "launch_angle",
    "barrel",
    "hard_hit",
    "xwoba",
    "detailed_chase_contact",
    "tracking_based_defense",
    "tracking_based_runner_speed",
)

_SHA256 = re.compile(r"[0-9a-f]{64}")


class ReadinessDecision(str, Enum):
    READY_FOR_GEN2_FREE_DATA_MODELING = "READY_FOR_GEN2_FREE_DATA_MODELING"
    READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS = (
        "READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS"
    )
    NOT_READY_DATA_INTEGRITY_FAILURE = "NOT_READY_DATA_INTEGRITY_FAILURE"


class DatasetPopulation(str, Enum):
    PRIMARY = "GEN2_PRIMARY_2023_2025"
    PRIOR_ONLY = "GEN2_PRIOR_ONLY_2021_2022"


class GameConflictKind(str, Enum):
    FINAL_SCORE = "FINAL_SCORE"
    TEAM_DATE_GAME_NUMBER = "TEAM_DATE_GAME_NUMBER"
    GAME_TYPE = "GAME_TYPE"
    DUPLICATE_MAPPING = "DUPLICATE_MAPPING"
    VENUE = "VENUE"
    STARTER = "STARTER"
    LINEUP = "LINEUP"
    PARTICIPATION = "PARTICIPATION"

    @property
    def blocks_integrity(self) -> bool:
        return self in {
            self.FINAL_SCORE,
            self.TEAM_DATE_GAME_NUMBER,
            self.GAME_TYPE,
            self.DUPLICATE_MAPPING,
        }


class TemporalLeakageKind(str, Enum):
    CURRENT_GAME = "CURRENT_GAME"
    FUTURE_EVIDENCE = "FUTURE_EVIDENCE"
    SAME_DAY_DOUBLEHEADER_ORDER = "SAME_DAY_DOUBLEHEADER_ORDER"
    POSTGAME_IN_PREGAME = "POSTGAME_IN_PREGAME"
    PARK_FACTOR_FUTURE_SEASON = "PARK_FACTOR_FUTURE_SEASON"
    WEATHER_AFTER_CUTOFF = "WEATHER_AFTER_CUTOFF"


@dataclass(frozen=True, slots=True)
class SourceFileChecksumIssue:
    logical_path: str
    expected_sha256: str | None
    actual_sha256: str | None
    detail: str

    def __post_init__(self) -> None:
        _nonblank(self.logical_path, "checksum logical_path")
        _optional_sha256(self.expected_sha256, "expected_sha256")
        _optional_sha256(self.actual_sha256, "actual_sha256")
        _nonblank(self.detail, "checksum detail")


@dataclass(frozen=True, slots=True)
class SourceSummary:
    source_key: str
    source_version: str
    artifact_count: int
    checksum_verified_artifact_count: int
    checksum_issues: tuple[SourceFileChecksumIssue, ...] = ()
    required_for_readiness: bool = True
    authorization_recorded: bool = True
    use_classification_recorded: bool = True
    license_recorded: bool = True
    attribution_required: bool = False
    attribution_recorded: bool = True

    def __post_init__(self) -> None:
        _nonblank(self.source_key, "source_key")
        _nonblank(self.source_version, "source_version")
        _counts(
            self,
            "artifact_count",
            "checksum_verified_artifact_count",
        )
        if self.checksum_verified_artifact_count > self.artifact_count:
            raise ValueError("verified source artifacts cannot exceed downloaded artifacts")
        _unique(
            (issue.logical_path for issue in self.checksum_issues),
            f"{self.source_key} checksum logical paths",
        )

    @property
    def checksums_complete(self) -> bool:
        return (
            self.artifact_count > 0
            and self.checksum_verified_artifact_count == self.artifact_count
            and not self.checksum_issues
        )


@dataclass(frozen=True, slots=True)
class OutcomeCount:
    outcome: str | PlateAppearanceOutcome
    count: int

    def __post_init__(self) -> None:
        name = (
            self.outcome.value
            if isinstance(self.outcome, PlateAppearanceOutcome)
            else self.outcome
        )
        if name not in PA_OUTCOME_NAMES:
            raise ValueError(f"unknown plate-appearance outcome: {name!r}")
        _nonnegative(self.count, f"{name} count")
        object.__setattr__(self, "outcome", name)


@dataclass(frozen=True, slots=True)
class IdentityConflict:
    conflict_id: str
    season: int
    source_namespace: str
    external_id: str
    candidate_canonical_ids: tuple[str, ...]
    affected_game_ids: tuple[str, ...] = ()
    detail: str = "ambiguous or conflicting person identity"

    def __post_init__(self) -> None:
        _nonblank(self.conflict_id, "identity conflict_id")
        _season(self.season)
        _nonblank(self.source_namespace, "identity source_namespace")
        _nonblank(self.external_id, "identity external_id")
        if not self.candidate_canonical_ids:
            raise ValueError("identity conflict requires a canonical candidate")
        _unique_nonblank(self.candidate_canonical_ids, "candidate canonical identities")
        _unique_nonblank(self.affected_game_ids, "affected game identities")
        _nonblank(self.detail, "identity conflict detail")


@dataclass(frozen=True, slots=True)
class ConservationConflict:
    conflict_id: str
    season: int
    partition_id: str
    game_id: str
    event_id: str
    invariant: str
    detail: str

    def __post_init__(self) -> None:
        _nonblank(self.conflict_id, "conservation conflict_id")
        _season(self.season)
        for name in ("partition_id", "game_id", "event_id", "invariant", "detail"):
            _nonblank(getattr(self, name), f"conservation {name}")


@dataclass(frozen=True, slots=True)
class TierLeakage:
    conflict_id: str
    row_id: str
    source_population: str
    destination_population: str
    reason: str

    def __post_init__(self) -> None:
        for name in (
            "conflict_id",
            "row_id",
            "source_population",
            "destination_population",
            "reason",
        ):
            _nonblank(getattr(self, name), f"tier leakage {name}")


@dataclass(frozen=True, slots=True)
class TemporalLeakage:
    conflict_id: str
    row_id: str
    kind: TemporalLeakageKind
    evidence_date: date
    target_date: date
    detail: str

    def __post_init__(self) -> None:
        _nonblank(self.conflict_id, "temporal conflict_id")
        _nonblank(self.row_id, "temporal row_id")
        _nonblank(self.detail, "temporal leakage detail")


@dataclass(frozen=True, slots=True)
class GameReconciliationConflict:
    conflict_id: str
    retrosheet_game_id: str
    asp_game_id: str
    kind: GameConflictKind
    retrosheet_value: str
    asp_value: str
    detail: str

    def __post_init__(self) -> None:
        for name in (
            "conflict_id",
            "retrosheet_game_id",
            "asp_game_id",
            "retrosheet_value",
            "asp_value",
            "detail",
        ):
            _nonblank(getattr(self, name), f"game conflict {name}")


@dataclass(frozen=True, slots=True)
class Asp2025CanonicalSummary:
    canonical_regular_season_finals: int
    retrosheet_regular_season_finals: int
    mapped_games: int
    exact_match_games: int
    missing_in_retrosheet: tuple[str, ...] = ()
    missing_in_asp: tuple[str, ...] = ()
    conflicts: tuple[GameReconciliationConflict, ...] = ()

    def __post_init__(self) -> None:
        _counts(
            self,
            "canonical_regular_season_finals",
            "retrosheet_regular_season_finals",
            "mapped_games",
            "exact_match_games",
        )
        if self.mapped_games > min(
            self.canonical_regular_season_finals,
            self.retrosheet_regular_season_finals,
        ):
            raise ValueError("mapped 2025 games exceed a source population")
        if self.exact_match_games > self.mapped_games:
            raise ValueError("exact 2025 matches exceed mapped games")
        _unique_nonblank(self.missing_in_retrosheet, "2025 games missing in Retrosheet")
        _unique_nonblank(self.missing_in_asp, "2025 games missing in ASP")
        _unique((row.conflict_id for row in self.conflicts), "2025 game conflict identities")


@dataclass(frozen=True, slots=True)
class SeasonImportSummary:
    """Compact source/import projection for one regular season.

    Every game-family numerator uses ``valid_final_games`` as its denominator.
    Player identity and taxonomy keep their own natural denominators.
    """

    season: int
    population: DatasetPopulation
    expected_regular_season_games: int = 0
    imported_regular_season_games: int = 0
    valid_final_games: int = 0
    home_win_games: int = 0
    away_win_games: int = 0
    plate_appearance_games: int = 0
    plate_appearances: int = 0
    transition_games: int = 0
    transitions: int = 0
    identity_unique_player_count: int = 0
    resolved_identity_unique_player_count: int = 0
    identity_reference_count: int = 0
    resolved_identity_reference_count: int = 0
    unresolved_identity_ids: tuple[str, ...] = ()
    complete_lineup_games: int = 0
    lineup_timing_risk_games: int = 0
    starter_games: int = 0
    starter_historical_reconstruction_games: int = 0
    starter_duration_rows: int = 0
    bullpen_usage_games: int = 0
    bullpen_usage_rows: int = 0
    fielding_games: int = 0
    fielding_rows: int = 0
    weather_games: int = 0
    pitch_sequence_games: int = 0
    taxonomy_terminal_events: int = 0
    taxonomy_mapped_terminal_events: int = 0
    taxonomy_outcome_counts: tuple[OutcomeCount, ...] = ()
    unsafe_taxonomy_event_ids: tuple[str, ...] = ()
    blocked_partition_ids: tuple[str, ...] = ()
    player_prior_rows: int = 0
    park_factor_rows: int = 0
    tier_candidate_rows: int = 0
    tier_eligible_rows: int = 0
    strict_deployable_rows: int = 0
    enhanced_research_rows: int = 0
    prior_only_rows: int = 0

    def __post_init__(self) -> None:
        _season(self.season)
        count_names = tuple(
            field.name
            for field in fields(self)
            if field.name
            not in {
                "season",
                "population",
                "unresolved_identity_ids",
                "taxonomy_outcome_counts",
                "unsafe_taxonomy_event_ids",
                "blocked_partition_ids",
            }
        )
        _counts(self, *count_names)
        for name in (
            "imported_regular_season_games",
            "valid_final_games",
            "home_win_games",
            "away_win_games",
            "plate_appearance_games",
            "transition_games",
            "complete_lineup_games",
            "lineup_timing_risk_games",
            "starter_games",
            "starter_historical_reconstruction_games",
            "bullpen_usage_games",
            "fielding_games",
            "weather_games",
            "pitch_sequence_games",
        ):
            if getattr(self, name) > self.expected_regular_season_games:
                raise ValueError(f"{self.season} {name} exceeds expected games")
        if self.valid_final_games > self.imported_regular_season_games:
            raise ValueError("valid finals cannot exceed imported games")
        if self.resolved_identity_reference_count > self.identity_reference_count:
            raise ValueError("resolved identity references exceed all references")
        if self.resolved_identity_unique_player_count > self.identity_unique_player_count:
            raise ValueError("resolved unique player identities exceed all unique players")
        if self.taxonomy_mapped_terminal_events > self.taxonomy_terminal_events:
            raise ValueError("mapped taxonomy events exceed all terminal events")
        if self.tier_candidate_rows != self.tier_eligible_rows:
            raise ValueError(
                "tier candidate rows must equal rows classified exactly once"
            )
        if self.tier_eligible_rows != (
            self.strict_deployable_rows
            + self.enhanced_research_rows
            + self.prior_only_rows
        ):
            raise ValueError(
                "tier-eligible rows must be classified exactly once as "
                "strict, enhanced, or prior-only"
            )
        _unique_nonblank(self.unresolved_identity_ids, f"{self.season} unresolved identities")
        _unique_nonblank(
            self.unsafe_taxonomy_event_ids,
            f"{self.season} unsafe taxonomy events",
        )
        _unique_nonblank(self.blocked_partition_ids, f"{self.season} blocked partitions")
        _unique(
            (row.outcome for row in self.taxonomy_outcome_counts),
            f"{self.season} taxonomy outcomes",
        )

    @property
    def outcome_counts(self) -> dict[str, int]:
        present = {row.outcome: row.count for row in self.taxonomy_outcome_counts}
        return {name: present.get(name, 0) for name in PA_OUTCOME_NAMES}


@dataclass(frozen=True, slots=True)
class PopulationSeparationSummary:
    tier_candidate_row_count: int
    tier_eligible_row_count: int
    strict_row_count: int
    enhanced_row_count: int
    prior_only_row_count: int
    historical_actual_lineup_rows: int
    isolated_enhanced_lineup_rows: int
    historical_actual_starter_rows: int
    isolated_enhanced_starter_rows: int
    leakage: tuple[TierLeakage, ...] = ()
    projection_unit: str = TIER_PROJECTION_UNIT

    def __post_init__(self) -> None:
        _counts(
            self,
            "tier_candidate_row_count",
            "tier_eligible_row_count",
            "strict_row_count",
            "enhanced_row_count",
            "prior_only_row_count",
            "historical_actual_lineup_rows",
            "isolated_enhanced_lineup_rows",
            "historical_actual_starter_rows",
            "isolated_enhanced_starter_rows",
        )
        if self.projection_unit != TIER_PROJECTION_UNIT:
            raise ValueError(
                f"population projection_unit must be {TIER_PROJECTION_UNIT}"
            )
        if self.tier_candidate_row_count != self.tier_eligible_row_count:
            raise ValueError(
                "population tier candidates must equal rows classified exactly once"
            )
        if self.tier_eligible_row_count != (
            self.strict_row_count + self.enhanced_row_count + self.prior_only_row_count
        ):
            raise ValueError(
                "population tier rows must be a complete disjoint classification"
            )
        if self.isolated_enhanced_lineup_rows > self.historical_actual_lineup_rows:
            raise ValueError("isolated lineup rows exceed all historical actual lineup rows")
        if self.isolated_enhanced_starter_rows > self.historical_actual_starter_rows:
            raise ValueError("isolated starter rows exceed all historical actual starter rows")
        _unique((row.conflict_id for row in self.leakage), "tier leakage identities")

    @property
    def isolated(self) -> bool:
        return (
            not self.leakage
            and self.historical_actual_lineup_rows == self.isolated_enhanced_lineup_rows
            and self.historical_actual_starter_rows == self.isolated_enhanced_starter_rows
        )


@dataclass(frozen=True, slots=True)
class TemporalFoldEvidence:
    fold_id: str
    training_end: date
    validation_start: date
    validation_end: date
    training_rows: int
    validation_rows: int

    def __post_init__(self) -> None:
        _nonblank(self.fold_id, "fold_id")
        _counts(self, "training_rows", "validation_rows")

    @property
    def formable(self) -> bool:
        return (
            self.training_end < self.validation_start <= self.validation_end
            and self.training_rows > 0
            and self.validation_rows > 0
        )


@dataclass(frozen=True, slots=True)
class TemporalAuditSummary:
    chronology_checked_rows: int
    folds: tuple[TemporalFoldEvidence, ...]
    leakage: tuple[TemporalLeakage, ...] = ()

    def __post_init__(self) -> None:
        _nonnegative(self.chronology_checked_rows, "chronology_checked_rows")
        _unique((row.fold_id for row in self.folds), "temporal fold identities")
        _unique((row.conflict_id for row in self.leakage), "temporal leakage identities")

    @property
    def formable_fold_count(self) -> int:
        return sum(row.formable for row in self.folds)


@dataclass(frozen=True, slots=True)
class TrainingEligibilitySummary:
    event_training_rows: int
    event_outcome_counts: tuple[OutcomeCount, ...]
    transition_training_rows: int
    transition_state_outcome_cells: int
    simulator_validation_games: int
    starter_duration_rows: int
    bullpen_usage_rows: int
    chronological_player_prior_rows: int
    prior_period_park_factor_rows: int

    def __post_init__(self) -> None:
        _counts(
            self,
            "event_training_rows",
            "transition_training_rows",
            "transition_state_outcome_cells",
            "simulator_validation_games",
            "starter_duration_rows",
            "bullpen_usage_rows",
            "chronological_player_prior_rows",
            "prior_period_park_factor_rows",
        )
        if self.transition_state_outcome_cells > len(PA_OUTCOME_NAMES) * 24:
            raise ValueError("transition state/outcome cells exceed the governed universe")
        _unique(
            (row.outcome for row in self.event_outcome_counts),
            "training outcome classes",
        )

    @property
    def outcome_counts(self) -> dict[str, int]:
        present = {row.outcome: row.count for row in self.event_outcome_counts}
        return {name: present.get(name, 0) for name in PA_OUTCOME_NAMES}

    @property
    def every_event_outcome_present(self) -> bool:
        return all(self.outcome_counts[name] > 0 for name in PA_OUTCOME_NAMES)


@dataclass(frozen=True, slots=True)
class ReadinessPolicy:
    minimum_primary_identity_coverage: float = 0.995
    minimum_primary_lineup_coverage: float = 0.95
    minimum_primary_starter_coverage: float = 0.99
    minimum_primary_bullpen_coverage: float = 0.90
    minimum_primary_fielding_coverage: float = 0.95
    minimum_primary_weather_coverage: float = 0.90
    minimum_primary_plate_appearance_coverage: float = 0.99
    minimum_primary_transition_coverage: float = 0.99
    minimum_temporal_folds: int = 2

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "minimum_temporal_folds":
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError("minimum_temporal_folds must be a positive integer")
            elif (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(float(value))
                or not 0 <= value <= 1
            ):
                raise ValueError(f"{field.name} must be a finite ratio in 0..1")


@dataclass(frozen=True, slots=True)
class FD1ReadinessInput:
    sources: tuple[SourceSummary, ...]
    seasons: tuple[SeasonImportSummary, ...]
    asp_2025: Asp2025CanonicalSummary
    population_separation: PopulationSeparationSummary
    temporal: TemporalAuditSummary
    training: TrainingEligibilitySummary
    identity_conflicts: tuple[IdentityConflict, ...] = ()
    conservation_conflicts: tuple[ConservationConflict, ...] = ()

    def __post_init__(self) -> None:
        _unique((row.source_key for row in self.sources), "source keys")
        _unique((row.conflict_id for row in self.identity_conflicts), "identity conflicts")
        _unique(
            (row.conflict_id for row in self.conservation_conflicts),
            "conservation conflicts",
        )


@dataclass(frozen=True, slots=True)
class CoverageMetric:
    covered: int
    eligible: int

    def __post_init__(self) -> None:
        _nonnegative(self.covered, "coverage numerator")
        _nonnegative(self.eligible, "coverage denominator")
        if self.covered > self.eligible:
            raise ValueError("coverage numerator exceeds denominator")

    @property
    def ratio(self) -> float | None:
        return self.covered / self.eligible if self.eligible else None

    @property
    def complete(self) -> bool:
        return self.eligible > 0 and self.covered == self.eligible

    def meets(self, threshold: float) -> bool:
        ratio = self.ratio
        return ratio is not None and ratio >= threshold


@dataclass(frozen=True, slots=True)
class SeasonReconciliation:
    season: int
    population: DatasetPopulation
    expected_games: int
    imported_games: int
    valid_finals: int
    home_win_games: int
    away_win_games: int
    games_complete: bool
    plate_appearances: CoverageMetric
    base_out_transitions: CoverageMetric
    player_identity_unique: CoverageMetric
    player_identity: CoverageMetric
    lineups: CoverageMetric
    starters: CoverageMetric
    bullpen: CoverageMetric
    fielding: CoverageMetric
    weather: CoverageMetric
    pitch_sequences: CoverageMetric
    taxonomy: CoverageMetric
    taxonomy_outcome_counts: tuple[OutcomeCount, ...]
    plate_appearance_rows: int
    transition_rows: int
    starter_duration_rows: int
    bullpen_usage_rows: int
    fielding_rows: int
    player_prior_rows: int
    park_factor_rows: int
    unresolved_identity_count: int
    identity_conflict_count: int
    unsafe_taxonomy_event_count: int
    blocked_partition_count: int
    strict_rows: int
    enhanced_rows: int
    prior_only_rows: int


@dataclass(frozen=True, slots=True)
class AuditAnswer:
    key: str
    passed: bool
    evidence: str


@dataclass(frozen=True, slots=True)
class FD1ReadinessResult:
    decision: ReadinessDecision
    source_summaries: tuple[SourceSummary, ...]
    season_reconciliation: tuple[SeasonReconciliation, ...]
    asp_2025_reconciliation: Asp2025CanonicalSummary
    population_separation: PopulationSeparationSummary
    temporal_folds: tuple[TemporalFoldEvidence, ...]
    training_eligibility: TrainingEligibilitySummary
    answers: tuple[AuditAnswer, ...]
    integrity_failures: tuple[str, ...]
    readiness_blockers: tuple[str, ...]
    documented_limitations: tuple[str, ...]
    checksum_issues: tuple[SourceFileChecksumIssue, ...]
    identity_conflicts: tuple[IdentityConflict, ...]
    game_conflicts: tuple[GameReconciliationConflict, ...]
    conservation_conflicts: tuple[ConservationConflict, ...]
    tier_leakage: tuple[TierLeakage, ...]
    temporal_leakage: tuple[TemporalLeakage, ...]
    statcast_gaps: tuple[str, ...] = STATCAST_TRACKING_GAPS

    @property
    def modeling_can_proceed(self) -> bool:
        return self.decision is not ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE

    def answer(self, key: str) -> AuditAnswer:
        for answer in self.answers:
            if answer.key == key:
                return answer
        raise KeyError(key)

    def season(self, season: int) -> SeasonReconciliation:
        for row in self.season_reconciliation:
            if row.season == season:
                return row
        raise KeyError(season)

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON-ready report payload."""

        payload = {
            field.name: _jsonable(getattr(self, field.name)) for field in fields(self)
        }
        payload["modeling_can_proceed"] = self.modeling_can_proceed
        return payload


def audit_fd1_readiness(
    evidence: FD1ReadinessInput,
    *,
    policy: ReadinessPolicy | None = None,
) -> FD1ReadinessResult:
    """Reconcile FD1 import evidence and return one terminal decision."""

    rules = policy or ReadinessPolicy()
    integrity: list[str] = []
    blockers: list[str] = []
    limitations: list[str] = []

    checksum_issues = tuple(
        issue for source in evidence.sources for issue in source.checksum_issues
    )
    required_sources = tuple(
        source for source in evidence.sources if source.required_for_readiness
    )
    if not required_sources:
        integrity.append("no required source summary was supplied")
    for source in required_sources:
        if not source.checksums_complete:
            integrity.append(
                f"{source.source_key}: checksums verified "
                f"{source.checksum_verified_artifact_count}/{source.artifact_count}; "
                f"issues={len(source.checksum_issues)}"
            )
        missing_governance = []
        if not source.authorization_recorded:
            missing_governance.append("authorization")
        if not source.use_classification_recorded:
            missing_governance.append("use classification")
        if not source.license_recorded:
            missing_governance.append("license")
        if source.attribution_required and not source.attribution_recorded:
            missing_governance.append("attribution")
        if missing_governance:
            integrity.append(
                f"{source.source_key}: missing source governance: "
                + ", ".join(missing_governance)
            )

    season_groups: dict[int, list[SeasonImportSummary]] = {}
    for season in evidence.seasons:
        season_groups.setdefault(season.season, []).append(season)
    for season, rows in sorted(season_groups.items()):
        if len(rows) != 1:
            integrity.append(f"{season}: duplicate season summaries ({len(rows)})")
    by_season = {season: rows[0] for season, rows in season_groups.items()}
    for season in REQUIRED_SEASONS:
        if season not in by_season:
            integrity.append(f"{season}: required season summary is missing")

    reconciliations = tuple(
        _reconcile_season(row, evidence.identity_conflicts)
        for row in sorted(evidence.seasons, key=lambda value: value.season)
    )
    reconciliation_by_season = {row.season: row for row in reconciliations}

    for summary in evidence.seasons:
        if summary.complete_lineup_games != summary.lineup_timing_risk_games:
            integrity.append(
                f"{summary.season}: historical actual lineups are not fully labeled "
                "HISTORICAL_ACTUAL_LINEUP_TIMING_RISK "
                f"({summary.lineup_timing_risk_games}/{summary.complete_lineup_games})"
            )
        if summary.starter_games != summary.starter_historical_reconstruction_games:
            integrity.append(
                f"{summary.season}: historical actual starters are not fully labeled "
                "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION "
                f"({summary.starter_historical_reconstruction_games}/"
                f"{summary.starter_games})"
            )

    for season in PRIMARY_SEASONS:
        summary = by_season.get(season)
        if summary is None:
            continue
        if summary.population is not DatasetPopulation.PRIMARY:
            integrity.append(f"{season}: primary season is assigned to {summary.population.value}")
        if summary.expected_regular_season_games <= 0:
            integrity.append(f"{season}: expected regular-season population is empty")
        if (
            summary.imported_regular_season_games != summary.expected_regular_season_games
            or summary.valid_final_games != summary.expected_regular_season_games
        ):
            integrity.append(
                f"{season}: incomplete modern game population "
                f"expected={summary.expected_regular_season_games}, "
                f"imported={summary.imported_regular_season_games}, "
                f"valid_finals={summary.valid_final_games}"
            )
        if summary.home_win_games + summary.away_win_games != summary.valid_final_games:
            integrity.append(
                f"{season}: home/away outcomes do not reconcile to valid finals "
                f"({summary.home_win_games}+{summary.away_win_games}!="
                f"{summary.valid_final_games})"
            )
        _audit_taxonomy_reconciliation(summary, integrity)

    for season in PRIOR_ONLY_SEASONS:
        summary = by_season.get(season)
        if summary is None:
            continue
        if summary.population is not DatasetPopulation.PRIOR_ONLY:
            integrity.append(
                f"{season}: prior-only season is assigned to {summary.population.value}"
            )
        if summary.imported_regular_season_games <= 0 or summary.player_prior_rows <= 0:
            blockers.append(f"{season}: prior-only history/player priors are not populated")
        elif (
            summary.expected_regular_season_games > 0
            and summary.valid_final_games < summary.expected_regular_season_games
        ):
            limitations.append(
                f"{season}: prior-only finals cover {summary.valid_final_games}/"
                f"{summary.expected_regular_season_games} games"
            )
        _audit_taxonomy_reconciliation(summary, integrity)

    if evidence.identity_conflicts:
        integrity.append(
            "identity conflicts remain: "
            + ",".join(sorted(row.conflict_id for row in evidence.identity_conflicts))
        )
    if evidence.conservation_conflicts:
        integrity.append(
            "base/out, runner, run, or score conservation conflicts remain: "
            + ",".join(sorted(row.conflict_id for row in evidence.conservation_conflicts))
        )
    if evidence.population_separation.leakage:
        integrity.append(
            "strict/enhanced/prior-only tier leakage remains: "
            + ",".join(
                sorted(row.conflict_id for row in evidence.population_separation.leakage)
            )
        )
    if evidence.temporal.leakage:
        integrity.append(
            "temporal leakage remains: "
            + ",".join(sorted(row.conflict_id for row in evidence.temporal.leakage))
        )

    population_totals_reconcile = _audit_population_totals(evidence, integrity)
    training_totals_reconcile = _audit_training_totals(evidence, integrity)
    _audit_asp_2025(evidence.asp_2025, integrity, limitations)

    primary_rows = tuple(
        reconciliation_by_season[season]
        for season in PRIMARY_SEASONS
        if season in reconciliation_by_season
    )
    primary_complete = (
        len(primary_rows) == len(PRIMARY_SEASONS)
        and all(row.games_complete for row in primary_rows)
        and _asp_2025_complete(evidence.asp_2025)
    )
    both_game_outcomes = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.home_win_games > 0 and row.away_win_games > 0 for row in primary_rows
    )
    pa_complete = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.plate_appearances.complete for row in primary_rows
    )
    transitions_complete = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.base_out_transitions.complete for row in primary_rows
    )
    identity_sufficient = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.player_identity.meets(rules.minimum_primary_identity_coverage)
        and row.player_identity_unique.meets(rules.minimum_primary_identity_coverage)
        for row in primary_rows
    ) and not evidence.identity_conflicts
    lineups_sufficient = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.lineups.meets(rules.minimum_primary_lineup_coverage) for row in primary_rows
    ) and evidence.population_separation.isolated
    starters_sufficient = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.starters.meets(rules.minimum_primary_starter_coverage) for row in primary_rows
    ) and evidence.training.starter_duration_rows > 0
    bullpen_sufficient = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.bullpen.meets(rules.minimum_primary_bullpen_coverage) for row in primary_rows
    ) and evidence.training.bullpen_usage_rows > 0
    fielding_sufficient = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.fielding.meets(rules.minimum_primary_fielding_coverage) for row in primary_rows
    )
    weather_sufficient = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.weather.meets(rules.minimum_primary_weather_coverage) for row in primary_rows
    )
    pa_sufficient = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.plate_appearances.meets(rules.minimum_primary_plate_appearance_coverage)
        for row in primary_rows
    )
    transition_sufficient = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.base_out_transitions.meets(rules.minimum_primary_transition_coverage)
        for row in primary_rows
    )
    taxonomy_complete = len(primary_rows) == len(PRIMARY_SEASONS) and all(
        row.taxonomy.complete and row.unsafe_taxonomy_event_count == 0
        for row in primary_rows
    )
    priors_available = (
        evidence.training.chronological_player_prior_rows > 0
        and all(
            by_season[season].player_prior_rows > 0
            for season in REQUIRED_SEASONS
            if season in by_season
        )
        and len(by_season) >= len(REQUIRED_SEASONS)
    )
    parks_formable = (
        evidence.training.prior_period_park_factor_rows > 0
        and all(
            by_season[season].park_factor_rows > 0
            for season in PRIMARY_SEASONS
            if season in by_season
        )
        and all(season in by_season for season in PRIMARY_SEASONS)
    )
    population_assignments_valid = all(
        by_season.get(season) is not None
        and by_season[season].population
        is (
            DatasetPopulation.PRIMARY
            if season in PRIMARY_SEASONS
            else DatasetPopulation.PRIOR_ONLY
        )
        for season in REQUIRED_SEASONS
    )
    tiers_separated = (
        evidence.population_separation.isolated
        and population_totals_reconcile
        and population_assignments_valid
    )
    temporal_folds_formable = (
        not evidence.temporal.leakage
        and evidence.temporal.chronology_checked_rows > 0
        and evidence.temporal.formable_fold_count >= rules.minimum_temporal_folds
        and evidence.temporal.formable_fold_count == len(evidence.temporal.folds)
    )

    outcome_counts = evidence.training.outcome_counts
    training_count_reconciles = (
        sum(outcome_counts.values()) == evidence.training.event_training_rows
    )
    if not training_count_reconciles:
        integrity.append(
            "event training outcome counts do not reconcile: "
            f"classes={sum(outcome_counts.values())}, "
            f"rows={evidence.training.event_training_rows}"
        )
    event_trainable = (
        evidence.training.event_training_rows > 0
        and evidence.training.every_event_outcome_present
        and training_count_reconciles
        and pa_sufficient
        and taxonomy_complete
        and identity_sufficient
        and temporal_folds_formable
        and tiers_separated
        and training_totals_reconcile
    )
    simulator_trainable = (
        evidence.training.transition_training_rows > 0
        and evidence.training.transition_state_outcome_cells > 0
        and evidence.training.simulator_validation_games > 0
        and transition_sufficient
        and taxonomy_complete
        and not evidence.conservation_conflicts
        and training_totals_reconcile
    )

    required_checks = (
        (both_game_outcomes, "primary game outcome classes are not both present"),
        (pa_sufficient, "primary plate-appearance coverage is below the readiness threshold"),
        (
            transition_sufficient,
            "primary base/out transition coverage is below the readiness threshold",
        ),
        (taxonomy_complete, "primary terminal-event taxonomy is incomplete or unsafe"),
        (identity_sufficient, "primary player identity coverage is insufficient"),
        (lineups_sufficient, "actual lineup coverage/isolation is insufficient"),
        (starters_sufficient, "starter-duration targets are not trainable"),
        (bullpen_sufficient, "bullpen-usage targets are not trainable"),
        (fielding_sufficient, "primary fielding coverage is insufficient"),
        (weather_sufficient, "primary weather coverage is insufficient"),
        (priors_available, "chronological player priors are unavailable"),
        (parks_formable, "prior-period park factors cannot be formed"),
        (tiers_separated, "strict/enhanced/prior-only populations are not separated"),
        (temporal_folds_formable, "rolling temporal folds cannot be formed"),
        (event_trainable, "the existing eight-class event model cannot be fitted"),
        (simulator_trainable, "the existing simulator cannot be fitted and validated"),
    )
    blockers.extend(message for passed, message in required_checks if not passed)

    for row in primary_rows:
        _coverage_limitation(row.season, "plate appearances", row.plate_appearances, limitations)
        _coverage_limitation(
            row.season,
            "base/out transitions",
            row.base_out_transitions,
            limitations,
        )
        _coverage_limitation(row.season, "player identity", row.player_identity, limitations)
        _coverage_limitation(
            row.season,
            "unique player identity",
            row.player_identity_unique,
            limitations,
        )
        _coverage_limitation(row.season, "actual lineups", row.lineups, limitations)
        _coverage_limitation(row.season, "starters", row.starters, limitations)
        _coverage_limitation(row.season, "bullpen", row.bullpen, limitations)
        _coverage_limitation(row.season, "fielding", row.fielding, limitations)
        _coverage_limitation(row.season, "weather", row.weather, limitations)
        _coverage_limitation(row.season, "taxonomy", row.taxonomy, limitations)
        # Pitch strings are a useful but optional Retrosheet family.  Their
        # incompleteness is always documented and never presented as Statcast.
        _coverage_limitation(
            row.season,
            "Retrosheet pitch sequences",
            row.pitch_sequences,
            limitations,
        )
        if row.blocked_partition_count:
            limitations.append(
                f"{row.season}: {row.blocked_partition_count} unsafe/incomplete "
                "partitions are quarantined"
            )

    answers = (
        AuditAnswer(
            "primary_games_complete",
            primary_complete,
            _season_boolean_evidence(primary_rows, lambda row: row.games_complete),
        ),
        AuditAnswer(
            "both_game_outcome_classes_present",
            both_game_outcomes,
            "; ".join(
                f"{row.season}=home:{row.home_win_games},away:{row.away_win_games}"
                for row in primary_rows
            ) or "no primary seasons",
        ),
        AuditAnswer(
            "plate_appearances_complete",
            pa_complete,
            _season_coverage_evidence(primary_rows, "plate_appearances"),
        ),
        AuditAnswer(
            "base_out_transitions_complete",
            transitions_complete,
            _season_coverage_evidence(primary_rows, "base_out_transitions"),
        ),
        AuditAnswer(
            "player_ids_resolved",
            identity_sufficient,
            "references="
            + _season_coverage_evidence(primary_rows, "player_identity")
            + "; unique_players="
            + _season_coverage_evidence(primary_rows, "player_identity_unique"),
        ),
        AuditAnswer(
            "actual_lineups_available_and_timing_risk_isolated",
            lineups_sufficient,
            _season_coverage_evidence(primary_rows, "lineups")
            + f"; tier_isolated={evidence.population_separation.isolated}",
        ),
        AuditAnswer(
            "starter_duration_trainable",
            starters_sufficient,
            f"eligible_rows={evidence.training.starter_duration_rows}; "
            + _season_coverage_evidence(primary_rows, "starters"),
        ),
        AuditAnswer(
            "bullpen_usage_trainable",
            bullpen_sufficient,
            f"eligible_rows={evidence.training.bullpen_usage_rows}; "
            + _season_coverage_evidence(primary_rows, "bullpen"),
        ),
        AuditAnswer(
            "player_priors_available",
            priors_available,
            f"chronological_rows={evidence.training.chronological_player_prior_rows}",
        ),
        AuditAnswer(
            "prior_only_park_factors_formable",
            parks_formable,
            f"prior_period_rows={evidence.training.prior_period_park_factor_rows}",
        ),
        AuditAnswer(
            "weather_coverage_sufficient",
            weather_sufficient,
            _season_coverage_evidence(primary_rows, "weather"),
        ),
        AuditAnswer(
            "fielding_coverage_sufficient",
            fielding_sufficient,
            _season_coverage_evidence(primary_rows, "fielding"),
        ),
        AuditAnswer(
            "terminal_event_taxonomy_complete",
            taxonomy_complete,
            _season_coverage_evidence(primary_rows, "taxonomy"),
        ),
        AuditAnswer(
            "strict_enhanced_prior_only_separated",
            tiers_separated,
            f"unit={evidence.population_separation.projection_unit}, "
            f"candidates={evidence.population_separation.tier_candidate_row_count}, "
            f"classified={evidence.population_separation.tier_eligible_row_count}, "
            f"strict={evidence.population_separation.strict_row_count}, "
            f"enhanced={evidence.population_separation.enhanced_row_count}, "
            f"prior_only={evidence.population_separation.prior_only_row_count}, "
            f"leaks={len(evidence.population_separation.leakage)}",
        ),
        AuditAnswer(
            "rolling_temporal_folds_formable",
            temporal_folds_formable,
            f"formable={evidence.temporal.formable_fold_count}/"
            f"{len(evidence.temporal.folds)}, leakage={len(evidence.temporal.leakage)}",
        ),
        AuditAnswer(
            "event_model_trainable",
            event_trainable,
            f"rows={evidence.training.event_training_rows}, "
            f"outcomes_present={sum(value > 0 for value in outcome_counts.values())}/8",
        ),
        AuditAnswer(
            "simulator_trainable_and_validatable",
            simulator_trainable,
            f"transitions={evidence.training.transition_training_rows}, "
            f"state_outcome_cells={evidence.training.transition_state_outcome_cells}/192, "
            f"validation_games={evidence.training.simulator_validation_games}",
        ),
        AuditAnswer(
            "statcast_tracking_gaps_explicit",
            True,
            ",".join(STATCAST_TRACKING_GAPS),
        ),
    )

    integrity = _dedupe(integrity)
    blockers = _dedupe(blockers)
    limitations = _dedupe(limitations)
    if integrity or blockers:
        decision = ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE
    elif limitations:
        decision = ReadinessDecision.READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS
    else:
        decision = ReadinessDecision.READY_FOR_GEN2_FREE_DATA_MODELING

    return FD1ReadinessResult(
        decision=decision,
        source_summaries=evidence.sources,
        season_reconciliation=reconciliations,
        asp_2025_reconciliation=evidence.asp_2025,
        population_separation=evidence.population_separation,
        temporal_folds=evidence.temporal.folds,
        training_eligibility=evidence.training,
        answers=answers,
        integrity_failures=integrity,
        readiness_blockers=blockers,
        documented_limitations=limitations,
        checksum_issues=checksum_issues,
        identity_conflicts=evidence.identity_conflicts,
        game_conflicts=evidence.asp_2025.conflicts,
        conservation_conflicts=evidence.conservation_conflicts,
        tier_leakage=evidence.population_separation.leakage,
        temporal_leakage=evidence.temporal.leakage,
    )


def _reconcile_season(
    summary: SeasonImportSummary,
    identity_conflicts: tuple[IdentityConflict, ...],
) -> SeasonReconciliation:
    finals = summary.valid_final_games
    target_games = summary.expected_regular_season_games
    return SeasonReconciliation(
        season=summary.season,
        population=summary.population,
        expected_games=summary.expected_regular_season_games,
        imported_games=summary.imported_regular_season_games,
        valid_finals=finals,
        home_win_games=summary.home_win_games,
        away_win_games=summary.away_win_games,
        games_complete=(
            summary.expected_regular_season_games > 0
            and summary.imported_regular_season_games == summary.expected_regular_season_games
            and finals == summary.expected_regular_season_games
        ),
        plate_appearances=CoverageMetric(summary.plate_appearance_games, target_games),
        base_out_transitions=CoverageMetric(summary.transition_games, target_games),
        player_identity_unique=CoverageMetric(
            summary.resolved_identity_unique_player_count,
            summary.identity_unique_player_count,
        ),
        player_identity=CoverageMetric(
            summary.resolved_identity_reference_count,
            summary.identity_reference_count,
        ),
        lineups=CoverageMetric(summary.complete_lineup_games, target_games),
        starters=CoverageMetric(summary.starter_games, target_games),
        bullpen=CoverageMetric(summary.bullpen_usage_games, target_games),
        fielding=CoverageMetric(summary.fielding_games, target_games),
        weather=CoverageMetric(summary.weather_games, target_games),
        pitch_sequences=CoverageMetric(summary.pitch_sequence_games, target_games),
        taxonomy=CoverageMetric(
            summary.taxonomy_mapped_terminal_events,
            summary.taxonomy_terminal_events,
        ),
        taxonomy_outcome_counts=tuple(
            OutcomeCount(name, summary.outcome_counts[name]) for name in PA_OUTCOME_NAMES
        ),
        plate_appearance_rows=summary.plate_appearances,
        transition_rows=summary.transitions,
        starter_duration_rows=summary.starter_duration_rows,
        bullpen_usage_rows=summary.bullpen_usage_rows,
        fielding_rows=summary.fielding_rows,
        player_prior_rows=summary.player_prior_rows,
        park_factor_rows=summary.park_factor_rows,
        unresolved_identity_count=len(summary.unresolved_identity_ids),
        identity_conflict_count=sum(
            conflict.season == summary.season for conflict in identity_conflicts
        ),
        unsafe_taxonomy_event_count=len(summary.unsafe_taxonomy_event_ids),
        blocked_partition_count=len(summary.blocked_partition_ids),
        strict_rows=summary.strict_deployable_rows,
        enhanced_rows=summary.enhanced_research_rows,
        prior_only_rows=summary.prior_only_rows,
    )


def _audit_taxonomy_reconciliation(
    summary: SeasonImportSummary,
    failures: list[str],
) -> None:
    category_sum = sum(summary.outcome_counts.values())
    if category_sum != summary.taxonomy_mapped_terminal_events:
        failures.append(
            f"{summary.season}: taxonomy class counts do not reconcile "
            f"({category_sum}!={summary.taxonomy_mapped_terminal_events})"
        )
    if summary.unsafe_taxonomy_event_ids:
        failures.append(
            f"{summary.season}: unsafe taxonomy events remain: "
            + ",".join(sorted(summary.unsafe_taxonomy_event_ids))
        )


def _audit_population_totals(
    evidence: FD1ReadinessInput,
    failures: list[str],
) -> bool:
    valid = True
    expected = (
        sum(row.tier_candidate_rows for row in evidence.seasons),
        sum(row.tier_eligible_rows for row in evidence.seasons),
        sum(row.strict_deployable_rows for row in evidence.seasons),
        sum(row.enhanced_research_rows for row in evidence.seasons),
        sum(row.prior_only_rows for row in evidence.seasons),
    )
    reported = (
        evidence.population_separation.tier_candidate_row_count,
        evidence.population_separation.tier_eligible_row_count,
        evidence.population_separation.strict_row_count,
        evidence.population_separation.enhanced_row_count,
        evidence.population_separation.prior_only_row_count,
    )
    if expected != reported:
        valid = False
        failures.append(
            "population tier totals do not reconcile: "
            f"season_summaries={expected}, separation_summary={reported}"
        )
    separation = evidence.population_separation
    if separation.historical_actual_lineup_rows != separation.isolated_enhanced_lineup_rows:
        valid = False
        failures.append(
            "historical actual lineup rows are not fully isolated in enhanced evidence "
            f"({separation.isolated_enhanced_lineup_rows}/"
            f"{separation.historical_actual_lineup_rows})"
        )
    if separation.historical_actual_starter_rows != separation.isolated_enhanced_starter_rows:
        valid = False
        failures.append(
            "historical actual starter rows are not fully isolated in enhanced evidence "
            f"({separation.isolated_enhanced_starter_rows}/"
            f"{separation.historical_actual_starter_rows})"
        )
    for summary in evidence.seasons:
        if summary.population is DatasetPopulation.PRIMARY and summary.prior_only_rows:
            valid = False
            failures.append(
                f"{summary.season}: {summary.prior_only_rows} prior-only rows entered "
                "the primary population"
            )
        if summary.population is DatasetPopulation.PRIOR_ONLY and (
            summary.strict_deployable_rows or summary.enhanced_research_rows
        ):
            valid = False
            failures.append(
                f"{summary.season}: prior-only season contains strict/enhanced rows "
                f"({summary.strict_deployable_rows}/{summary.enhanced_research_rows})"
            )
        if summary.population is DatasetPopulation.PRIMARY and (
            summary.strict_deployable_rows == 0
            or summary.enhanced_research_rows == 0
        ):
            valid = False
            failures.append(
                f"{summary.season}: primary tier projection must contain both "
                "strict and enhanced rows"
            )
        if (
            summary.population is DatasetPopulation.PRIOR_ONLY
            and summary.prior_only_rows == 0
        ):
            valid = False
            failures.append(f"{summary.season}: prior-only tier projection is empty")
    return valid


def _audit_training_totals(
    evidence: FD1ReadinessInput,
    failures: list[str],
) -> bool:
    primary = tuple(row for row in evidence.seasons if row.season in PRIMARY_SEASONS)
    all_seasons = evidence.seasons
    available = {
        "event_training_rows": sum(row.taxonomy_mapped_terminal_events for row in primary),
        "transition_training_rows": sum(row.transitions for row in primary),
        "simulator_validation_games": sum(row.valid_final_games for row in primary),
        "starter_duration_rows": sum(row.starter_duration_rows for row in primary),
        "bullpen_usage_rows": sum(row.bullpen_usage_rows for row in primary),
        "chronological_player_prior_rows": sum(row.player_prior_rows for row in all_seasons),
        "prior_period_park_factor_rows": sum(row.park_factor_rows for row in all_seasons),
    }
    valid = True
    for name, imported_count in available.items():
        eligible_count = getattr(evidence.training, name)
        if eligible_count > imported_count:
            valid = False
            failures.append(
                f"training eligibility exceeds imported {name}: "
                f"eligible={eligible_count}, imported={imported_count}"
            )
    return valid


def _asp_2025_complete(summary: Asp2025CanonicalSummary) -> bool:
    return (
        summary.canonical_regular_season_finals
        == ASP_2025_CANONICAL_REGULAR_SEASON_FINALS
        and summary.retrosheet_regular_season_finals
        == ASP_2025_CANONICAL_REGULAR_SEASON_FINALS
        and summary.mapped_games == ASP_2025_CANONICAL_REGULAR_SEASON_FINALS
        and not summary.missing_in_retrosheet
        and not summary.missing_in_asp
        and not any(row.kind.blocks_integrity for row in summary.conflicts)
    )


def _audit_asp_2025(
    summary: Asp2025CanonicalSummary,
    failures: list[str],
    limitations: list[str],
) -> None:
    if summary.canonical_regular_season_finals != ASP_2025_CANONICAL_REGULAR_SEASON_FINALS:
        failures.append(
            "existing ASP 2025 canonical finals changed: "
            f"expected={ASP_2025_CANONICAL_REGULAR_SEASON_FINALS}, "
            f"observed={summary.canonical_regular_season_finals}"
        )
    if summary.retrosheet_regular_season_finals != ASP_2025_CANONICAL_REGULAR_SEASON_FINALS:
        failures.append(
            "Retrosheet 2025 finals are incomplete: "
            f"expected={ASP_2025_CANONICAL_REGULAR_SEASON_FINALS}, "
            f"observed={summary.retrosheet_regular_season_finals}"
        )
    if (
        summary.mapped_games + len(summary.missing_in_retrosheet)
        != summary.canonical_regular_season_finals
    ):
        failures.append(
            "ASP-to-Retrosheet 2025 mapping count does not reconcile: "
            f"mapped={summary.mapped_games}, missing={len(summary.missing_in_retrosheet)}, "
            f"canonical={summary.canonical_regular_season_finals}"
        )
    if (
        summary.mapped_games + len(summary.missing_in_asp)
        != summary.retrosheet_regular_season_finals
    ):
        failures.append(
            "Retrosheet-to-ASP 2025 mapping count does not reconcile: "
            f"mapped={summary.mapped_games}, missing={len(summary.missing_in_asp)}, "
            f"retrosheet={summary.retrosheet_regular_season_finals}"
        )
    if summary.missing_in_retrosheet:
        failures.append(
            "2025 games missing in Retrosheet: " + ",".join(summary.missing_in_retrosheet)
        )
    if summary.missing_in_asp:
        failures.append("2025 games missing in ASP: " + ",".join(summary.missing_in_asp))
    blocking = tuple(row for row in summary.conflicts if row.kind.blocks_integrity)
    if blocking:
        failures.append(
            "blocking 2025 game conflicts: "
            + ",".join(sorted(row.conflict_id for row in blocking))
        )
    documented = tuple(row for row in summary.conflicts if not row.kind.blocks_integrity)
    if documented:
        limitations.append(
            "documented non-authoritative 2025 source differences: "
            + ",".join(sorted(row.conflict_id for row in documented))
        )


def _coverage_limitation(
    season: int,
    family: str,
    metric: CoverageMetric,
    limitations: list[str],
) -> None:
    if metric.eligible > 0 and not metric.complete:
        limitations.append(
            f"{season}: {family} coverage is {metric.covered}/{metric.eligible} "
            f"({metric.ratio:.4%})"
        )


def _season_boolean_evidence(
    rows: tuple[SeasonReconciliation, ...],
    predicate: Any,
) -> str:
    return "; ".join(
        f"{row.season}={'yes' if predicate(row) else 'no'}" for row in rows
    ) or "no primary seasons"


def _season_coverage_evidence(
    rows: tuple[SeasonReconciliation, ...],
    attribute: str,
) -> str:
    values = []
    for row in rows:
        metric = getattr(row, attribute)
        ratio = "N/A" if metric.ratio is None else f"{metric.ratio:.4%}"
        values.append(f"{row.season}={metric.covered}/{metric.eligible} ({ratio})")
    return "; ".join(values) or "no primary seasons"


def _counts(value: object, *names: str) -> None:
    for name in names:
        _nonnegative(getattr(value, name), name)


def _nonnegative(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _nonblank(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")


def _season(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1876 <= value <= 9999:
        raise ValueError("season must be a valid MLB year")


def _optional_sha256(value: str | None, name: str) -> None:
    if value is not None and not _SHA256.fullmatch(value.lower()):
        raise ValueError(f"{name} must be SHA-256 when present")


def _unique(values: Iterable[object], name: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValueError(f"{name} must be unique")


def _unique_nonblank(values: Iterable[str], name: str) -> None:
    materialized = tuple(values)
    for value in materialized:
        _nonblank(value, name)
    _unique(materialized, name)


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, CoverageMetric):
        return {
            "covered": value.covered,
            "eligible": value.eligible,
            "ratio": value.ratio,
            "complete": value.complete,
        }
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


__all__ = [
    "ASP_2025_CANONICAL_REGULAR_SEASON_FINALS",
    "AuditAnswer",
    "Asp2025CanonicalSummary",
    "ConservationConflict",
    "CoverageMetric",
    "DatasetPopulation",
    "FD1ReadinessInput",
    "FD1ReadinessResult",
    "GameConflictKind",
    "GameReconciliationConflict",
    "IdentityConflict",
    "OutcomeCount",
    "PA_OUTCOME_NAMES",
    "PRIMARY_SEASONS",
    "PRIOR_ONLY_SEASONS",
    "PopulationSeparationSummary",
    "REQUIRED_SEASONS",
    "ReadinessDecision",
    "ReadinessPolicy",
    "STATCAST_TRACKING_GAPS",
    "TIER_PROJECTION_UNIT",
    "SeasonImportSummary",
    "SeasonReconciliation",
    "SourceFileChecksumIssue",
    "SourceSummary",
    "TemporalAuditSummary",
    "TemporalFoldEvidence",
    "TemporalLeakage",
    "TemporalLeakageKind",
    "TierLeakage",
    "TrainingEligibilitySummary",
    "audit_fd1_readiness",
]
