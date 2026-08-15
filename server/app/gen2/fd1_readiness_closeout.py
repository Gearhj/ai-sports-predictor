"""Pure Gate 3C-FD1 closeout projection from verified local evidence.

The publication operator intentionally stops at durable warehouse evidence.  This
module joins its sanitized execute ledger to three caller-supplied, provider-free
audits: compact warehouse coverage, active-era identity coverage, and the read-only
2025 ASP/Retrosheet reconciliation.  It has no filesystem, database, transport, or
model capability.

The compact warehouse evidence is deliberately typed.  The local Parquet verifier
that produces it must count fully covered weather games (not merely weather rows),
complete two-sided lineups/starters, final scores, taxonomy classes, temporal folds,
and a *disjoint* row-level tier projection.  Missing evidence raises instead of being
turned into a favorable zero.
"""

from __future__ import annotations

from calendar import monthrange
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import date
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any

from app.gen2.fd1_identity import ResolutionStatus
from app.gen2.fd1_identity_coverage import (
    ACTIVE_SEASONS,
    ActiveIdentityCoverageReport,
    IdentityRelation,
)
from app.gen2.fd1_readiness import (
    ASP_2025_CANONICAL_REGULAR_SEASON_FINALS,
    PA_OUTCOME_NAMES,
    PRIMARY_SEASONS,
    TIER_PROJECTION_UNIT,
    Asp2025CanonicalSummary,
    ConservationConflict,
    DatasetPopulation,
    FD1ReadinessInput,
    FD1ReadinessResult,
    GameConflictKind,
    GameReconciliationConflict,
    IdentityConflict,
    OutcomeCount,
    PopulationSeparationSummary,
    SeasonImportSummary,
    SourceSummary,
    TemporalAuditSummary,
    TierLeakage,
    TrainingEligibilitySummary,
    audit_fd1_readiness,
)
from app.gen2.fd1_reconciliation import (
    FIELD_FAMILIES,
    ComparisonStatus,
    Fd1ReconciliationError,
    ReconciliationResult,
    normalize_asp_schedule_game_numbers,
)


FD1_READINESS_CLOSEOUT_SCHEMA_VERSION = "gate3c_fd1_readiness_closeout_v2"
FD1_WAREHOUSE_EVIDENCE_SCHEMA_VERSION = "gate3c_fd1_warehouse_evidence_v2"
OPERATOR_SCHEMA_VERSION = "gate3c_fd1_actual_publication_operator_v5"
FD1_POSTCOMMIT_SCHEMA_VERSION = "gate3c_fd1_postcommit_verification_v1"
FD1_POSTCOMMIT_STATUS = "COMMITTED_AND_POSTCOMMIT_VERIFIED"
FD1_ACTUAL_OPERATOR_LOGICAL_PATH = ".runtime/gate3c-fd1/publish_actual.py"
FD1_POSTCOMMIT_MODULE_LOGICAL_PATH = "server/app/gen2/fd1_postcommit.py"
FD1_POSTCOMMIT_SCRIPT_LOGICAL_PATH = ".runtime/gate3c-fd1/postcommit_verify.py"

MANAGER_USAGE_SCHEMA_VERSIONS = (
    "retrosheet_fd1_bullpen_usage_v6",
    "retrosheet_fd1_starter_usage_v6",
)
UMPIRE_WEATHER_SCHEMA_VERSION = "retrosheet_fd1_weather_v8"

EXPECTED_REGULAR_SEASON_GAMES: Mapping[int, int] = {
    2021: 2_429,
    2022: 2_430,
    2023: 2_430,
    2024: 2_429,
    2025: 2_430,
}
EXPECTED_COMPLETED_SUSPENDED_GAMES: Mapping[int, int] = {
    2021: 8,
    2022: 1,
    2023: 6,
    2024: 3,
    2025: 4,
}
EXPECTED_RELOCATED_SUSPENDED_GAMES: Mapping[int, int] = {
    2021: 1,
    2022: 0,
    2023: 0,
    2024: 0,
    2025: 0,
}
MATERIALIZED_FAMILIES = (
    "plate_appearance",
    "play_transition",
    "pitch_sequence",
    "player_game_batting",
    "player_game_pitching",
    "player_game_fielding",
    "starter_usage",
    "bullpen_usage",
    "weather",
)
RECONCILIATION_MATERIALIZED_FAMILIES = (
    "play_transition",
    "starter_usage",
    "player_game_batting",
    "player_game_pitching",
    "player_game_fielding",
)
REQUIRED_SOURCE_KEYS = (
    "CHADWICK_REGISTER_ARCHIVES",
    "RETROSHEET_AUXILIARY_DOWNLOADS",
    "RETROSHEET_CSV_DOWNLOADS",
    "SABR_LAHMAN_BOX_DOWNLOADS",
)
FD1_RUN_GOVERNANCE_SOURCE_KEYS = tuple(
    sorted((*REQUIRED_SOURCE_KEYS, "RETROSHEET_EVENT_ARCHIVES"))
)
_AUTHORITATIVE_RECONCILIATION_FAMILIES = (
    "game_identity",
    "team_date_number",
    "final_score",
)
_MATERIALIZATION_RECONCILIATION_CHECKS = (
    "actual_participant_timing_isolated",
    "automatic_runner_rule_placements",
    "base_out_conservation",
    "completed_suspension_semantics",
    "current_and_future_excluded",
    "exact_seven_file_schema",
    "identity_references",
    "plate_appearance_taxonomy",
    "runner_conservation",
    "score_conservation",
    "starter_sequence",
    "statcast_claims_absent",
    "zero_stat_lineup_position_reconciliation",
)
_CONFLICT_KIND_BY_FAMILY: Mapping[str, GameConflictKind] = {
    "team_date_number": GameConflictKind.TEAM_DATE_GAME_NUMBER,
    "final_score": GameConflictKind.FINAL_SCORE,
    "venue": GameConflictKind.VENUE,
    "starters": GameConflictKind.STARTER,
    "lineups": GameConflictKind.LINEUP,
    "batting_participation": GameConflictKind.PARTICIPATION,
    "pitching_participation": GameConflictKind.PARTICIPATION,
    "player_participation": GameConflictKind.PARTICIPATION,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RETROSHEET_PERSON_SOURCE_ID = re.compile(
    r"^retrosheet:player:[a-z-]{4}[a-z][0-9]{3}$"
)
_RETROSHEET_GAME_SOURCE_ID = re.compile(
    r"^retrosheet:game:[A-Z0-9]{3}[0-9]{9}$"
)


class FD1ReadinessCloseoutError(ValueError):
    """Closeout evidence cannot be projected without guessing."""


@dataclass(frozen=True, slots=True)
class FD1SeasonWarehouseEvidence:
    """Exact local-Parquet audit for one regular season.

    ``tier_candidate_rows`` is counted before classification at the declared
    row grain. ``tier_eligible_rows`` is the independently classified count and
    must match it. Every candidate is assigned exactly once, with prior-only
    population taking precedence, then enhanced-only evidence, then
    strict-deployable evidence. Overlap or unclassified rows belong in
    ``tier_leakage`` on the parent audit.
    """

    season: int
    imported_regular_season_games: int
    valid_final_games: int
    home_win_games: int
    away_win_games: int
    plate_appearance_games: int
    plate_appearances: int
    transition_games: int
    transitions: int
    complete_lineup_games: int
    lineup_timing_risk_games: int
    historical_actual_lineup_rows: int
    isolated_enhanced_lineup_rows: int
    starter_games: int
    starter_historical_reconstruction_games: int
    starter_duration_rows: int
    historical_actual_starter_rows: int
    isolated_enhanced_starter_rows: int
    bullpen_usage_games: int
    bullpen_usage_rows: int
    fielding_games: int
    fielding_rows: int
    fully_covered_weather_games: int
    pitch_sequence_games: int
    taxonomy_terminal_events: int
    taxonomy_mapped_terminal_events: int
    taxonomy_outcome_counts: tuple[OutcomeCount, ...]
    tier_candidate_rows: int
    tier_eligible_rows: int
    strict_deployable_rows: int
    enhanced_research_rows: int
    prior_only_rows: int
    unsafe_taxonomy_event_ids: tuple[str, ...] = ()
    blocked_partition_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.season not in EXPECTED_REGULAR_SEASON_GAMES:
            raise FD1ReadinessCloseoutError("warehouse season is outside FD1")
        for field in fields(self):
            if field.name in {
                "season",
                "taxonomy_outcome_counts",
                "unsafe_taxonomy_event_ids",
                "blocked_partition_ids",
            }:
                continue
            _nonnegative_int(getattr(self, field.name), field.name)
        if self.home_win_games + self.away_win_games != self.valid_final_games:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: final winner counts do not reconcile"
            )
        if self.tier_candidate_rows != self.tier_eligible_rows:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: tier candidates were not all classified"
            )
        if self.tier_eligible_rows != (
            self.strict_deployable_rows
            + self.enhanced_research_rows
            + self.prior_only_rows
        ):
            raise FD1ReadinessCloseoutError(
                f"{self.season}: tier rows are not a disjoint complete projection"
            )
        outcomes = {row.outcome for row in self.taxonomy_outcome_counts}
        if outcomes != set(PA_OUTCOME_NAMES):
            raise FD1ReadinessCloseoutError(
                f"{self.season}: all eight taxonomy outcomes must be explicit"
            )
        if sum(row.count for row in self.taxonomy_outcome_counts) != (
            self.taxonomy_mapped_terminal_events
        ):
            raise FD1ReadinessCloseoutError(
                f"{self.season}: taxonomy outcome counts do not reconcile"
            )
        _unique_nonblank(
            self.unsafe_taxonomy_event_ids,
            f"{self.season} unsafe taxonomy IDs",
        )
        _unique_nonblank(
            self.blocked_partition_ids,
            f"{self.season} blocked partition IDs",
        )


@dataclass(frozen=True, slots=True)
class FD1ManagerCoverageEvidence:
    """Per-season manager proof derived from both v6 pitcher-usage families.

    A game is covered only when both team-manager assignments are present.
    ``manager_usage_rows`` is the full starter-plus-bullpen row denominator;
    every repeated manager assignment must retain exact teamstats lineage.
    Manager gaps are warehouse-integrity failures, not optional feature gaps.
    """

    season: int
    regular_season_games: int
    games_with_complete_manager_coverage: int
    manager_usage_rows: int
    manager_populated_usage_rows: int
    teamstats_lineage_verified_usage_rows: int
    unique_manager_ids: tuple[str, ...]
    missing_manager_game_ids: tuple[str, ...]
    usage_schema_versions: tuple[str, ...] = MANAGER_USAGE_SCHEMA_VERSIONS

    def __post_init__(self) -> None:
        if self.season not in EXPECTED_REGULAR_SEASON_GAMES:
            raise FD1ReadinessCloseoutError("manager coverage season is outside FD1")
        expected_games = EXPECTED_REGULAR_SEASON_GAMES[self.season]
        for field_name in (
            "regular_season_games",
            "games_with_complete_manager_coverage",
            "manager_usage_rows",
            "manager_populated_usage_rows",
            "teamstats_lineage_verified_usage_rows",
        ):
            _nonnegative_int(getattr(self, field_name), field_name)
        if self.regular_season_games != expected_games:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: manager regular-season denominator changed"
            )
        if self.usage_schema_versions != MANAGER_USAGE_SCHEMA_VERSIONS:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: manager evidence must come from both v6 usage families"
            )
        _sorted_unique_source_person_ids(
            self.unique_manager_ids,
            f"{self.season} unique manager IDs",
        )
        _sorted_unique_source_game_ids(
            self.missing_manager_game_ids,
            f"{self.season} missing manager game IDs",
        )
        if self.games_with_complete_manager_coverage + len(
            self.missing_manager_game_ids
        ) != self.regular_season_games:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: manager game coverage does not reconcile"
            )
        if self.manager_usage_rows < 2 * self.regular_season_games:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: manager usage denominator cannot cover both teams"
            )
        if self.manager_populated_usage_rows != self.manager_usage_rows:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: a v6 usage row is missing its manager assignment"
            )
        if (
            self.teamstats_lineage_verified_usage_rows
            != self.manager_populated_usage_rows
        ):
            raise FD1ReadinessCloseoutError(
                f"{self.season}: manager assignments lack exact teamstats lineage"
            )
        if (
            self.games_with_complete_manager_coverage != self.regular_season_games
            or self.missing_manager_game_ids
        ):
            raise FD1ReadinessCloseoutError(
                f"{self.season}: manager game coverage is incomplete"
            )
        if not self.unique_manager_ids:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: manager identity evidence is absent"
            )


@dataclass(frozen=True, slots=True)
class FD1UmpireCoverageEvidence:
    """Per-season umpire proof from one sentinel-safe v8 weather row per game.

    Plate-umpire games and total populated crew-role assignments are distinct:
    Retrosheet legitimately omits some non-plate crew positions.  Consistency,
    source presence, and exact gameinfo lineage are mandatory; partial umpire
    coverage remains report evidence and does not alter the FD1 modeling gate.
    """

    season: int
    regular_season_games: int
    weather_rows: int
    games_with_plate_umpire: int
    umpire_role_assignments: int
    gameinfo_lineage_verified_weather_rows: int
    unique_umpire_ids: tuple[str, ...]
    missing_plate_umpire_game_ids: tuple[str, ...]
    weather_schema_version: str = UMPIRE_WEATHER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.season not in EXPECTED_REGULAR_SEASON_GAMES:
            raise FD1ReadinessCloseoutError("umpire coverage season is outside FD1")
        expected_games = EXPECTED_REGULAR_SEASON_GAMES[self.season]
        for field_name in (
            "regular_season_games",
            "weather_rows",
            "games_with_plate_umpire",
            "umpire_role_assignments",
            "gameinfo_lineage_verified_weather_rows",
        ):
            _nonnegative_int(getattr(self, field_name), field_name)
        if self.regular_season_games != expected_games:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: umpire regular-season denominator changed"
            )
        if self.weather_schema_version != UMPIRE_WEATHER_SCHEMA_VERSION:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: umpire evidence must come from v8 weather rows"
            )
        if self.weather_rows != self.regular_season_games:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: weather rows do not reconcile to regular-season games"
            )
        if self.gameinfo_lineage_verified_weather_rows != self.weather_rows:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: umpire weather rows lack exact gameinfo lineage"
            )
        _sorted_unique_source_person_ids(
            self.unique_umpire_ids,
            f"{self.season} unique umpire IDs",
        )
        _sorted_unique_source_game_ids(
            self.missing_plate_umpire_game_ids,
            f"{self.season} missing plate-umpire game IDs",
        )
        if self.games_with_plate_umpire + len(
            self.missing_plate_umpire_game_ids
        ) != self.weather_rows:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: plate-umpire game coverage does not reconcile"
            )
        if not 0 < self.games_with_plate_umpire <= self.weather_rows:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: plate-umpire source presence is required"
            )
        if not (
            self.games_with_plate_umpire
            <= self.umpire_role_assignments
            <= 6 * self.weather_rows
        ):
            raise FD1ReadinessCloseoutError(
                f"{self.season}: umpire role assignments are inconsistent"
            )
        if not self.unique_umpire_ids:
            raise FD1ReadinessCloseoutError(
                f"{self.season}: umpire identity evidence is absent"
            )


@dataclass(frozen=True, slots=True)
class FD1WarehouseCloseoutEvidence:
    """Checksummed compact output of the local Parquet/readiness scan.

    Source-governance sets are explicit because the publication operator ledger
    proves byte provenance, not the separate authorization/license/attribution
    review.  An omitted source stays false in the terminal readiness input.  The
    three lineage checksums prevent a warehouse audit from being mixed with a
    different operator, active-identity report, or ASP reconciliation result.
    """

    seasons: tuple[FD1SeasonWarehouseEvidence, ...]
    manager_coverage: tuple[FD1ManagerCoverageEvidence, ...]
    umpire_coverage: tuple[FD1UmpireCoverageEvidence, ...]
    temporal: TemporalAuditSummary
    transition_state_outcome_cells: int
    operator_ledger_checksum: str
    identity_evidence_checksum: str
    reconciliation_result_checksum: str
    authorization_recorded_source_keys: tuple[str, ...]
    use_classification_recorded_source_keys: tuple[str, ...]
    license_recorded_source_keys: tuple[str, ...]
    attribution_required_source_keys: tuple[str, ...]
    attribution_recorded_source_keys: tuple[str, ...]
    conservation_conflicts: tuple[ConservationConflict, ...] = ()
    tier_leakage: tuple[TierLeakage, ...] = ()
    schema_version: str = FD1_WAREHOUSE_EVIDENCE_SCHEMA_VERSION
    tier_projection_unit: str = TIER_PROJECTION_UNIT
    derivation_checksum: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != FD1_WAREHOUSE_EVIDENCE_SCHEMA_VERSION:
            raise FD1ReadinessCloseoutError("warehouse evidence schema is invalid")
        if self.tier_projection_unit != TIER_PROJECTION_UNIT:
            raise FD1ReadinessCloseoutError("warehouse tier projection unit is invalid")
        if tuple(row.season for row in self.seasons) != ACTIVE_SEASONS:
            raise FD1ReadinessCloseoutError(
                "warehouse evidence must contain ordered seasons 2021 through 2025"
            )
        if tuple(row.season for row in self.manager_coverage) != ACTIVE_SEASONS:
            raise FD1ReadinessCloseoutError(
                "manager evidence must contain ordered seasons 2021 through 2025"
            )
        if tuple(row.season for row in self.umpire_coverage) != ACTIVE_SEASONS:
            raise FD1ReadinessCloseoutError(
                "umpire evidence must contain ordered seasons 2021 through 2025"
            )
        _nonnegative_int(
            self.transition_state_outcome_cells,
            "transition_state_outcome_cells",
        )
        _sha(self.operator_ledger_checksum, "warehouse operator ledger checksum")
        _sha(self.identity_evidence_checksum, "warehouse identity evidence checksum")
        _sha(
            self.reconciliation_result_checksum,
            "warehouse reconciliation result checksum",
        )
        if self.transition_state_outcome_cells > len(PA_OUTCOME_NAMES) * 24:
            raise FD1ReadinessCloseoutError(
                "transition state/outcome cells exceed the governed universe"
            )
        for field_name in (
            "authorization_recorded_source_keys",
            "use_classification_recorded_source_keys",
            "license_recorded_source_keys",
            "attribution_required_source_keys",
            "attribution_recorded_source_keys",
        ):
            values = getattr(self, field_name)
            if tuple(sorted(set(values))) != values:
                raise FD1ReadinessCloseoutError(
                    f"{field_name} must be sorted and unique"
                )
            if set(values) - set(REQUIRED_SOURCE_KEYS):
                raise FD1ReadinessCloseoutError(
                    f"{field_name} references a source outside the operator"
                )
        if self.attribution_required_source_keys != REQUIRED_SOURCE_KEYS:
            raise FD1ReadinessCloseoutError(
                "every required FD1 source must retain its attribution requirement"
            )
        if len({row.conflict_id for row in self.conservation_conflicts}) != len(
            self.conservation_conflicts
        ):
            raise FD1ReadinessCloseoutError("conservation conflict IDs are not unique")
        if len({row.conflict_id for row in self.tier_leakage}) != len(
            self.tier_leakage
        ):
            raise FD1ReadinessCloseoutError("tier leakage IDs are not unique")
        expected = _checksum(self._checksum_payload())
        if self.derivation_checksum:
            _sha(self.derivation_checksum, "warehouse derivation checksum")
            if self.derivation_checksum != expected:
                raise FD1ReadinessCloseoutError(
                    "warehouse evidence derivation checksum does not match"
                )
        else:
            object.__setattr__(self, "derivation_checksum", expected)

    def _checksum_payload(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tier_projection_unit": self.tier_projection_unit,
            "seasons": self.seasons,
            "manager_coverage": self.manager_coverage,
            "umpire_coverage": self.umpire_coverage,
            "temporal": self.temporal,
            "transition_state_outcome_cells": self.transition_state_outcome_cells,
            "operator_ledger_checksum": self.operator_ledger_checksum,
            "identity_evidence_checksum": self.identity_evidence_checksum,
            "reconciliation_result_checksum": self.reconciliation_result_checksum,
            "authorization_recorded_source_keys": (
                self.authorization_recorded_source_keys
            ),
            "use_classification_recorded_source_keys": (
                self.use_classification_recorded_source_keys
            ),
            "license_recorded_source_keys": self.license_recorded_source_keys,
            "attribution_required_source_keys": (
                self.attribution_required_source_keys
            ),
            "attribution_recorded_source_keys": self.attribution_recorded_source_keys,
            "conservation_conflicts": self.conservation_conflicts,
            "tier_leakage": self.tier_leakage,
        }

    def as_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True, slots=True)
class FD1ReadinessCloseout:
    schema_version: str
    operator_ledger_checksum: str
    warehouse_evidence_checksum: str
    identity_evidence_checksum: str
    reconciliation_evidence_checksum: str
    code_revision: str
    manager_coverage: tuple[FD1ManagerCoverageEvidence, ...]
    umpire_coverage: tuple[FD1UmpireCoverageEvidence, ...]
    evidence: FD1ReadinessInput
    result: FD1ReadinessResult
    derivation_checksum: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operator_ledger_checksum": self.operator_ledger_checksum,
            "warehouse_evidence_checksum": self.warehouse_evidence_checksum,
            "identity_evidence_checksum": self.identity_evidence_checksum,
            "reconciliation_evidence_checksum": self.reconciliation_evidence_checksum,
            "code_revision": self.code_revision,
            "manager_coverage": _plain(self.manager_coverage),
            "umpire_coverage": _plain(self.umpire_coverage),
            "readiness_input": _plain(self.evidence),
            "readiness_result": self.result.as_dict(),
            "derivation_checksum": self.derivation_checksum,
        }


def build_fd1_readiness_closeout(
    operator_closeout: Mapping[str, Any],
    warehouse: FD1WarehouseCloseoutEvidence,
    identity: ActiveIdentityCoverageReport,
    reconciliation: ReconciliationResult,
    *,
    identity_conflicts: Sequence[IdentityConflict] = (),
) -> FD1ReadinessCloseout:
    """Project verified local evidence and run the terminal readiness audit."""

    operator = _validated_operator(operator_closeout)
    operator_checksum = _checksum(operator_closeout)
    reconciliation_checksum = _checksum(
        reconciliation.as_dict(include_games=True)
    )
    if warehouse.operator_ledger_checksum != operator_checksum:
        raise FD1ReadinessCloseoutError(
            "warehouse evidence is bound to a different operator ledger"
        )
    if warehouse.identity_evidence_checksum != identity.derivation_checksum:
        raise FD1ReadinessCloseoutError(
            "warehouse evidence is bound to a different identity audit"
        )
    if warehouse.reconciliation_result_checksum != reconciliation_checksum:
        raise FD1ReadinessCloseoutError(
            "warehouse evidence is bound to a different reconciliation result"
        )
    _validate_reconciliation_operator_binding(operator, reconciliation)
    _validate_active_identity_summary(identity, operator)
    identity_conflict_rows = tuple(identity_conflicts)
    _validate_identity_conflict_projection(identity, identity_conflict_rows)
    sources = _source_summaries(operator, warehouse)
    priors_by_season = _prior_rows_by_season(operator)
    parks_by_season = _park_rows_by_target_season(operator)
    seasons = tuple(
        _season_summary(
            row,
            identity,
            player_prior_rows=priors_by_season[row.season],
            park_factor_rows=parks_by_season.get(row.season, 0),
        )
        for row in warehouse.seasons
    )
    population = PopulationSeparationSummary(
        tier_candidate_row_count=sum(row.tier_candidate_rows for row in seasons),
        tier_eligible_row_count=sum(row.tier_eligible_rows for row in seasons),
        strict_row_count=sum(row.strict_deployable_rows for row in seasons),
        enhanced_row_count=sum(row.enhanced_research_rows for row in seasons),
        prior_only_row_count=sum(row.prior_only_rows for row in seasons),
        historical_actual_lineup_rows=sum(
            row.historical_actual_lineup_rows for row in warehouse.seasons
        ),
        isolated_enhanced_lineup_rows=sum(
            row.isolated_enhanced_lineup_rows for row in warehouse.seasons
        ),
        historical_actual_starter_rows=sum(
            row.historical_actual_starter_rows for row in warehouse.seasons
        ),
        isolated_enhanced_starter_rows=sum(
            row.isolated_enhanced_starter_rows for row in warehouse.seasons
        ),
        leakage=warehouse.tier_leakage,
    )
    primary = tuple(row for row in seasons if row.season in PRIMARY_SEASONS)
    training_outcomes = Counter[str]()
    for row in primary:
        training_outcomes.update(
            {outcome.outcome: outcome.count for outcome in row.taxonomy_outcome_counts}
        )
    training = TrainingEligibilitySummary(
        event_training_rows=sum(row.taxonomy_mapped_terminal_events for row in primary),
        event_outcome_counts=tuple(
            OutcomeCount(name, training_outcomes[name]) for name in PA_OUTCOME_NAMES
        ),
        transition_training_rows=sum(row.transitions for row in primary),
        transition_state_outcome_cells=warehouse.transition_state_outcome_cells,
        simulator_validation_games=sum(row.valid_final_games for row in primary),
        starter_duration_rows=sum(row.starter_duration_rows for row in primary),
        bullpen_usage_rows=sum(row.bullpen_usage_rows for row in primary),
        # A source-season prior is eligible for primary season Y only when it is
        # exactly Y-1.  2021 and 2025 annual rows are retained but are not counted
        # as primary event-training predictors in this FD1 closeout.
        chronological_player_prior_rows=sum(
            priors_by_season[season] for season in (2022, 2023, 2024)
        ),
        prior_period_park_factor_rows=sum(parks_by_season.values()),
    )
    evidence = FD1ReadinessInput(
        sources=sources,
        seasons=seasons,
        asp_2025=_asp_summary(reconciliation),
        population_separation=population,
        temporal=warehouse.temporal,
        training=training,
        identity_conflicts=identity_conflict_rows,
        conservation_conflicts=warehouse.conservation_conflicts,
    )
    result = audit_fd1_readiness(evidence)
    payload = {
        "schema_version": FD1_READINESS_CLOSEOUT_SCHEMA_VERSION,
        "operator_ledger_checksum": operator_checksum,
        "warehouse_evidence_checksum": warehouse.derivation_checksum,
        "identity_evidence_checksum": identity.derivation_checksum,
        "reconciliation_evidence_checksum": reconciliation_checksum,
        "code_revision": operator["publication"]["code_revision"],
        "manager_coverage": warehouse.manager_coverage,
        "umpire_coverage": warehouse.umpire_coverage,
        "readiness_input": evidence,
        "readiness_result": result.as_dict(),
    }
    return FD1ReadinessCloseout(
        schema_version=FD1_READINESS_CLOSEOUT_SCHEMA_VERSION,
        operator_ledger_checksum=operator_checksum,
        warehouse_evidence_checksum=warehouse.derivation_checksum,
        identity_evidence_checksum=identity.derivation_checksum,
        reconciliation_evidence_checksum=reconciliation_checksum,
        code_revision=operator["publication"]["code_revision"],
        manager_coverage=warehouse.manager_coverage,
        umpire_coverage=warehouse.umpire_coverage,
        evidence=evidence,
        result=result,
        derivation_checksum=_checksum(payload),
    )


def _validated_operator(value: Mapping[str, Any]) -> Mapping[str, Any]:
    operator = _mapping(value, "operator closeout")
    if operator.get("schema_version") != OPERATOR_SCHEMA_VERSION:
        raise FD1ReadinessCloseoutError("operator closeout schema is invalid")
    if operator.get("success") is not True or operator.get("mode") != "execute":
        raise FD1ReadinessCloseoutError("readiness requires a successful execute ledger")
    if operator.get("sanitized") is not True:
        raise FD1ReadinessCloseoutError("operator closeout is not sanitized")
    transaction = _mapping(operator.get("transaction"), "operator transaction")
    if (
        transaction.get("committed") is not True
        or transaction.get("rolled_back") is not False
        or transaction.get("single_publish_call") is not True
        or transaction.get("committed_by_publication_function") is not False
        or transaction.get("status_at_closeout") != FD1_POSTCOMMIT_STATUS
        or transaction.get("commit_observed_by_postcommit_verifier") is not True
    ):
        raise FD1ReadinessCloseoutError("operator transaction proof is incomplete")
    _validated_postcommit_proof(operator)
    prohibited = _mapping(operator.get("prohibited_activity"), "prohibited activity")
    expected_false = (
        "provider_transport_constructed",
        "mlb_or_savant_automation",
        "predictive_model_fit_or_promotion",
        "odds_acquisition",
        "position_population",
    )
    if _nonnegative_int(prohibited.get("network_requests"), "network_requests") != 0:
        raise FD1ReadinessCloseoutError("operator performed a network request")
    if any(prohibited.get(name) is not False for name in expected_false):
        raise FD1ReadinessCloseoutError("operator reports prohibited activity")
    store = _mapping(operator.get("store"), "governed store summary")
    if store.get("default_governed_limits") is not True:
        raise FD1ReadinessCloseoutError("publication did not use governed store limits")
    publication = _mapping(operator.get("publication"), "publication summary")
    _sha(str(publication.get("code_revision", "")), "publication code revision")
    _positive_int(publication.get("batches_seen"), "publication batches_seen")
    _positive_int(publication.get("parquet_rows"), "publication parquet_rows")
    _positive_int(
        publication.get("identity_mappings_seen"),
        "publication identity_mappings_seen",
    )
    if (
        publication.get("mode") != "execute"
        or publication.get("provider_transport_constructed") is not False
        or publication.get("network_requests") != 0
        or publication.get("transaction_committed_by_function") is not False
    ):
        raise FD1ReadinessCloseoutError("publication boundary proof failed")
    readiness = _mapping(operator.get("readiness_evidence"), "readiness evidence")
    preflight = _mapping(
        readiness.get("raw_source_population_preflight"), "population preflight"
    )
    if preflight.get("passed") is not True or preflight.get("commit_permitted") is not True:
        raise FD1ReadinessCloseoutError("raw source population preflight did not pass")
    if preflight.get("basis") != "RAW_RETROSHEET_REGULAR_SEASON_AND_COMPLETION_EVIDENCE":
        raise FD1ReadinessCloseoutError("raw source population basis changed")
    expected_preflight = _mapping(preflight.get("expected"), "preflight expected")
    actual = _mapping(preflight.get("actual"), "preflight actual")
    preflight_populations = {
        "regular_season_games": EXPECTED_REGULAR_SEASON_GAMES,
        "completed_suspended_games": EXPECTED_COMPLETED_SUSPENDED_GAMES,
        "relocated_suspended_games": EXPECTED_RELOCATED_SUSPENDED_GAMES,
    }
    for name, governed in preflight_populations.items():
        expected_counts = _int_key_mapping(
            _mapping(expected_preflight.get(name), f"expected {name}")
        )
        actual_counts = _int_key_mapping(
            _mapping(actual.get(name), f"actual {name}")
        )
        if expected_counts != dict(governed) or actual_counts != dict(governed):
            raise FD1ReadinessCloseoutError(f"operator {name} changed")
    preflight_checks = _mapping(preflight.get("checks"), "preflight checks")
    required_preflight_checks = {
        "exact_season_set",
        "regular_season_game_counts",
        "completed_suspended_game_counts",
        "exactly_one_2021_relocation",
    }
    if set(preflight_checks) != required_preflight_checks or any(
        preflight_checks[name] is not True for name in required_preflight_checks
    ):
        raise FD1ReadinessCloseoutError("raw source population checks did not pass")
    materialization = _mapping(
        operator.get("retrosheet_materialization"), "materialization"
    )
    if (
        materialization.get("start_date") != "2021-01-01"
        or materialization.get("end_date") != "2025-12-31"
        or materialization.get("knowledge_cutoff_exclusive") != "2026-01-01"
        or materialization.get("single_pass_source_scan") is not True
    ):
        raise FD1ReadinessCloseoutError(
            "operator materialization window or scan semantics changed"
        )
    monthly = _sequence(materialization.get("monthly"), "materialization months")
    if (
        _nonnegative_int(
            materialization.get("monthly_window_count"), "monthly_window_count"
        )
        != 60
        or len(monthly) != 60
    ):
        raise FD1ReadinessCloseoutError(
            "materialization must contain all 60 monthly windows"
        )
    expected_months = {
        (season, month)
        for season in EXPECTED_REGULAR_SEASON_GAMES
        for month in range(1, 13)
    }
    observed_months: set[tuple[int, int]] = set()
    monthly_games: Counter[int] = Counter()
    monthly_suspended: Counter[int] = Counter()
    monthly_relocated: Counter[int] = Counter()
    for raw in monthly:
        row = _mapping(raw, "materialization month")
        season = _nonnegative_int(row.get("season"), "materialization month season")
        month = _nonnegative_int(row.get("month"), "materialization month number")
        key = (season, month)
        if key not in expected_months or key in observed_months:
            raise FD1ReadinessCloseoutError(
                "materialization monthly windows are missing, duplicated, or out of scope"
            )
        observed_months.add(key)
        expected_start = date(season, month, 1)
        expected_end = date(season, month, monthrange(season, month)[1])
        if (
            row.get("window_start") != expected_start.isoformat()
            or row.get("window_end") != expected_end.isoformat()
        ):
            raise FD1ReadinessCloseoutError(
                "a materialization monthly window has incorrect calendar boundaries"
            )
        monthly_games[season] += _nonnegative_int(
            row.get("selected_games"), "monthly selected_games"
        )
        monthly_suspended[season] += _nonnegative_int(
            row.get("completed_suspended_games"),
            "monthly completed_suspended_games",
        )
        monthly_relocated[season] += _nonnegative_int(
            row.get("relocated_suspended_games"),
            "monthly relocated_suspended_games",
        )
        checks = _mapping(row.get("reconciliation_checks"), "monthly checks")
        if set(checks) != set(_MATERIALIZATION_RECONCILIATION_CHECKS) or any(
            checks[name] is not True for name in _MATERIALIZATION_RECONCILIATION_CHECKS
        ):
            raise FD1ReadinessCloseoutError(
                "a monthly materialization reconciliation check did not pass"
            )
    if observed_months != expected_months or dict(monthly_games) != dict(
        EXPECTED_REGULAR_SEASON_GAMES
    ):
        raise FD1ReadinessCloseoutError(
            "monthly materialization game counts do not reconcile"
        )
    if dict(monthly_suspended) != dict(EXPECTED_COMPLETED_SUSPENDED_GAMES):
        raise FD1ReadinessCloseoutError(
            "monthly suspended-game counts do not reconcile"
        )
    if dict(monthly_relocated) != dict(EXPECTED_RELOCATED_SUSPENDED_GAMES):
        raise FD1ReadinessCloseoutError(
            "monthly relocated-game counts do not reconcile"
        )
    materialized_families = tuple(
        _sequence(materialization.get("families"), "materialized families")
    )
    if materialized_families != MATERIALIZED_FAMILIES:
        raise FD1ReadinessCloseoutError("materialized family set or order changed")
    seasons = _mapping(materialization.get("seasons"), "materialization seasons")
    selected = {
        int(key): _nonnegative_int(
            _mapping(summary, f"season {key}").get("selected_games"),
            f"season {key} selected_games",
        )
        for key, summary in seasons.items()
    }
    if selected != dict(EXPECTED_REGULAR_SEASON_GAMES):
        raise FD1ReadinessCloseoutError("materialized game counts do not reconcile")
    for field_name, expected_counts in (
        ("completed_suspended_games", EXPECTED_COMPLETED_SUSPENDED_GAMES),
        ("relocated_suspended_games", EXPECTED_RELOCATED_SUSPENDED_GAMES),
    ):
        observed = {
            int(key): _nonnegative_int(
                _mapping(summary, f"season {key}").get(field_name),
                f"season {key} {field_name}",
            )
            for key, summary in seasons.items()
        }
        if observed != dict(expected_counts):
            raise FD1ReadinessCloseoutError(
                f"materialized {field_name} do not reconcile"
            )
    identity_summary = _mapping(operator.get("identity"), "operator identity summary")
    identity_mappings = _positive_int(
        identity_summary.get("mappings"), "identity mappings"
    )
    if publication.get("identity_mappings_seen") != identity_mappings:
        raise FD1ReadinessCloseoutError(
            "published identity mappings do not match the operator ledger"
        )
    if (
        identity_summary.get("name_based_person_matches") != 0
        or identity_summary.get("name_based_organization_matches") != 0
    ):
        raise FD1ReadinessCloseoutError("operator identity boundary proof failed")
    source_versions = _mapping(operator.get("source_versions"), "source versions")
    if set(source_versions) != set(REQUIRED_SOURCE_KEYS):
        raise FD1ReadinessCloseoutError("operator source-version set is incomplete")
    for key, raw in source_versions.items():
        source = _mapping(raw, f"source version {key}")
        _nonblank(source.get("dataset_version"), f"{key} dataset_version")
        _sha(str(source.get("identity_checksum", "")), f"{key} identity checksum")
        if source.get("source_mode") != "LIVE":
            raise FD1ReadinessCloseoutError(f"{key} source mode is not LIVE")
    extractions = _sequence(operator.get("extractions_used"), "extractions")
    if not extractions:
        raise FD1ReadinessCloseoutError("operator extraction evidence is empty")
    for raw in extractions:
        extraction = _mapping(raw, "extraction")
        if extraction.get("source_key") not in REQUIRED_SOURCE_KEYS:
            raise FD1ReadinessCloseoutError("an extraction source is outside FD1")
        _positive_int(extraction.get("member_count"), "extraction member_count")
        _positive_int(extraction.get("expanded_bytes"), "extraction expanded_bytes")
        _nonblank(
            extraction.get("manifest_logical_uri"), "extraction manifest logical URI"
        )
        if extraction.get("member_bytes_verified") is not True:
            raise FD1ReadinessCloseoutError("an extraction was not byte-verified")
        _sha(str(extraction.get("archive_sha256", "")), "extraction archive checksum")
        _sha(str(extraction.get("identity_checksum", "")), "extraction identity")
    return operator


def _validated_postcommit_proof(operator: Mapping[str, Any]) -> Mapping[str, Any]:
    proof = _mapping(
        operator.get("postcommit_verification"), "postcommit verification"
    )
    if (
        proof.get("schema_version") != FD1_POSTCOMMIT_SCHEMA_VERSION
        or proof.get("all_checks_passed") is not True
    ):
        raise FD1ReadinessCloseoutError("postcommit verification did not pass")
    declared = _sha(
        str(proof.get("derivation_checksum", "")),
        "postcommit derivation checksum",
    )
    checksum_payload = dict(proof)
    checksum_payload.pop("derivation_checksum", None)
    if _checksum(checksum_payload) != declared:
        raise FD1ReadinessCloseoutError("postcommit derivation checksum changed")

    required_mappings = (
        "pretransaction_ledger",
        "execute_result",
        "operator_code",
        "verifier_code",
        "source_registry",
        "sql",
        "parquet",
        "prohibited_activity",
    )
    sections = {
        name: _mapping(proof.get(name), f"postcommit {name}")
        for name in required_mappings
    }
    referenced = _sequence(
        proof.get("referenced_artifacts"), "postcommit referenced artifacts"
    )
    if len(referenced) != 3:
        raise FD1ReadinessCloseoutError(
            "postcommit artifact verification set is incomplete"
        )
    artifacts_by_name: dict[str, Mapping[str, Any]] = {}
    for raw in referenced:
        artifact = _mapping(raw, "postcommit referenced artifact")
        name = _nonblank(artifact.get("name"), "postcommit artifact name")
        if name in artifacts_by_name:
            raise FD1ReadinessCloseoutError(
                "postcommit artifact verification set is duplicated"
            )
        artifacts_by_name[name] = artifact
        _sha(str(artifact.get("sha256", "")), "postcommit artifact checksum")
        _sha(
            str(artifact.get("canonical_sha256", "")),
            "postcommit artifact canonical checksum",
        )
    artifact_fields = {
        "retrosheet_source_evidence": ("logical_path", "sha256", "bytes"),
        "schedule_fingerprint_bridge": ("logical_path", "sha256", "bytes"),
        "asp_retrosheet_full_reconciliation": (
            "logical_path",
            "sha256",
            "bytes",
        ),
    }
    if set(artifacts_by_name) != set(artifact_fields):
        raise FD1ReadinessCloseoutError(
            "postcommit artifact verification set is incomplete"
        )
    for name, fields_to_bind in artifact_fields.items():
        declared_artifact = _mapping(operator.get(name), f"operator {name}")
        verified_artifact = artifacts_by_name[name]
        if any(
            verified_artifact.get(field_name) != declared_artifact.get(field_name)
            for field_name in fields_to_bind
        ):
            raise FD1ReadinessCloseoutError(
                f"postcommit artifact proof differs from {name}"
            )

    for section_name in ("pretransaction_ledger", "execute_result"):
        section = sections[section_name]
        _sha(str(section.get("sha256", "")), f"postcommit {section_name} checksum")
        _sha(
            str(section.get("canonical_sha256", "")),
            f"postcommit {section_name} canonical checksum",
        )
        _positive_int(section.get("bytes"), f"postcommit {section_name} bytes")
    pretransaction = dict(operator)
    pretransaction["success_scope"] = (
        "PRETRANSACTION_VALIDATION_AND_ARTIFACT_FINALIZATION"
    )
    pretransaction_transaction = dict(
        _mapping(pretransaction.get("transaction"), "operator transaction")
    )
    pretransaction_transaction.update(
        {
            "status_at_closeout": "PENDING_CALLER_COMMIT",
            "committed": False,
            "rolled_back": False,
            "committed_by_publication_function": False,
        }
    )
    pretransaction_transaction.pop("commit_observed_by_postcommit_verifier", None)
    pretransaction["transaction"] = pretransaction_transaction
    pretransaction.pop("postcommit_verification", None)
    if _checksum(pretransaction) != sections["pretransaction_ledger"].get(
        "canonical_sha256"
    ):
        raise FD1ReadinessCloseoutError(
            "postcommit proof is bound to a different pretransaction ledger"
        )
    for section_name, pattern in (
        (
            "pretransaction_ledger",
            r"(?:[A-Za-z0-9._-]+/)*publish-actual-execute-"
            r"pretransaction-closeout-sha256-([0-9a-f]{64})\.json",
        ),
        (
            "execute_result",
            r"(?:[A-Za-z0-9._-]+/)*publish-actual-execute-result-"
            r"sha256-([0-9a-f]{64})\.json",
        ),
    ):
        section = sections[section_name]
        logical_path = _nonblank(
            section.get("logical_path"), f"postcommit {section_name} logical path"
        )
        match = re.fullmatch(pattern, logical_path)
        if match is None or match.group(1) != section.get("sha256"):
            raise FD1ReadinessCloseoutError(
                f"postcommit {section_name} is not content-addressed"
            )
    operator_code = sections["operator_code"]
    publication = _mapping(operator.get("publication"), "publication")
    if (
        operator_code.get("logical_path") != FD1_ACTUAL_OPERATOR_LOGICAL_PATH
        or operator_code.get("combined_code_revision")
        != publication.get("code_revision")
    ):
        raise FD1ReadinessCloseoutError(
            "postcommit proof is bound to a different publication revision"
        )
    for name in ("sha256", "stable_code_revision", "combined_code_revision"):
        _sha(str(operator_code.get(name, "")), f"postcommit operator {name}")
    _positive_int(operator_code.get("bytes"), "postcommit operator bytes")
    verifier_code = sections["verifier_code"]
    if (
        verifier_code.get("module_logical_path")
        != FD1_POSTCOMMIT_MODULE_LOGICAL_PATH
        or verifier_code.get("script_logical_path")
        != FD1_POSTCOMMIT_SCRIPT_LOGICAL_PATH
    ):
        raise FD1ReadinessCloseoutError("postcommit verifier-code path changed")
    for name in ("module_sha256", "script_sha256"):
        _sha(str(verifier_code.get(name, "")), f"postcommit verifier {name}")
    for name in ("module_bytes", "script_bytes"):
        _positive_int(
            verifier_code.get(name), f"postcommit verifier {name}"
        )
    for section_name in ("source_registry", "sql", "parquet"):
        _sha(
            str(sections[section_name].get("population_checksum", "")),
            f"postcommit {section_name} population checksum",
        )
    source_registry = sections["source_registry"]
    registered_downloads = _sequence(
        operator.get("registered_downloads_used"), "registered downloads"
    )
    if (
        _nonnegative_int(
            source_registry.get("source_version_count"),
            "postcommit source version count",
        )
        != len(FD1_RUN_GOVERNANCE_SOURCE_KEYS)
        or _nonnegative_int(
            source_registry.get("registered_download_count"),
            "postcommit registered download count",
        )
        != len(registered_downloads)
        or tuple(
            _sequence(
                source_registry.get("operator_contributor_source_keys"),
                "postcommit contributor source keys",
            )
        )
        != REQUIRED_SOURCE_KEYS
        or tuple(
            _sequence(
                source_registry.get("run_governance_source_keys"),
                "postcommit governance source keys",
            )
        )
        != FD1_RUN_GOVERNANCE_SOURCE_KEYS
    ):
        raise FD1ReadinessCloseoutError(
            "postcommit source-registry proof differs from the operator ledger"
        )

    identity = _mapping(operator.get("identity"), "operator identity")
    expected_batches = _positive_int(publication.get("batches_seen"), "batches_seen")
    expected_rows = _positive_int(publication.get("parquet_rows"), "parquet_rows")
    expected_mappings = _positive_int(
        publication.get("identity_mappings_seen"), "identity_mappings_seen"
    )
    identity_batches = _positive_int(identity.get("batches"), "identity batches")
    expected_runs = expected_batches + identity_batches
    sql = sections["sql"]
    expected_sql_counts = {
        "ingestion_run_count": expected_runs,
        "partition_run_count": expected_batches,
        "identity_run_count": identity_batches,
        "checkpoint_count": expected_batches,
        "partition_count": expected_batches,
        "partition_row_count": expected_rows,
        "identity_mapping_count": expected_mappings,
    }
    if any(
        _nonnegative_int(sql.get(name), f"postcommit SQL {name}") != value
        for name, value in expected_sql_counts.items()
    ):
        raise FD1ReadinessCloseoutError(
            "postcommit SQL counts differ from the publication ledger"
        )
    derived_use_decisions = _positive_int(
        sql.get("derived_use_decision_count"),
        "postcommit SQL derived_use_decision_count",
    )
    derived_use_created = _nonnegative_int(
        publication.get("derived_use_rows_created"),
        "derived_use_rows_created",
    )
    derived_use_observations = derived_use_created + _nonnegative_int(
        publication.get("derived_use_rows_reused"),
        "derived_use_rows_reused",
    )
    if (
        derived_use_decisions > derived_use_observations
        or derived_use_created > derived_use_decisions
    ):
        raise FD1ReadinessCloseoutError(
            "postcommit derived-use counts differ from the publication ledger"
        )
    parquet = sections["parquet"]
    if (
        _nonnegative_int(
            parquet.get("partition_count"), "postcommit Parquet partition_count"
        )
        != expected_batches
        or _nonnegative_int(parquet.get("row_count"), "postcommit Parquet row_count")
        != expected_rows
        or parquet.get("all_sql_rows_validated") is not True
        or parquet.get("all_store_files_accounted_for") is not True
    ):
        raise FD1ReadinessCloseoutError(
            "postcommit Parquet counts differ from the publication ledger"
        )

    publication_count_pairs = {
        "partition_rows": expected_batches,
        "checkpoint_rows": expected_batches,
        "ingestion_run_rows": expected_runs,
        "identity_mapping_rows": expected_mappings,
    }
    for prefix, expected in publication_count_pairs.items():
        observed = _nonnegative_int(
            publication.get(f"{prefix}_created"), f"{prefix}_created"
        ) + _nonnegative_int(
            publication.get(f"{prefix}_reused"), f"{prefix}_reused"
        )
        if observed != expected:
            raise FD1ReadinessCloseoutError(
                f"publication {prefix} counters do not reconcile"
            )

    prohibited = sections["prohibited_activity"]
    zero_fields = (
        "network_requests",
        "database_writes",
        "governed_parquet_writes",
        "raw_source_scans",
        "subprocesses",
    )
    if any(
        _nonnegative_int(prohibited.get(name), f"postcommit {name}") != 0
        for name in zero_fields
    ):
        raise FD1ReadinessCloseoutError("postcommit verifier reports prohibited writes")
    for name in (
        "provider_transport_constructed",
        "mlb_or_savant_automation",
        "predictive_model_fit_or_promotion",
        "odds_acquisition",
        "position_population",
    ):
        if prohibited.get(name) is not False:
            raise FD1ReadinessCloseoutError(
                "postcommit verifier reports prohibited activity"
            )
    return proof


def _source_summaries(
    operator: Mapping[str, Any], warehouse: FD1WarehouseCloseoutEvidence
) -> tuple[SourceSummary, ...]:
    versions = _mapping(operator["source_versions"], "source versions")
    downloads = _sequence(operator.get("registered_downloads_used"), "downloads")
    counts: Counter[str] = Counter()
    download_archives: dict[tuple[str, str], str | None] = {}
    for raw in downloads:
        row = _mapping(raw, "registered download")
        key = _nonblank(row.get("source_key"), "download source_key")
        if key not in versions:
            raise FD1ReadinessCloseoutError("download source is outside source versions")
        object_key = _nonblank(row.get("object_key"), "download object_key")
        _nonblank(row.get("source_release"), "download source_release")
        _positive_int(row.get("upstream_revision"), "download upstream_revision")
        _positive_int(row.get("content_length"), "download content_length")
        _sha(str(row.get("payload_sha256", "")), "download payload checksum")
        raw_archive_sha = row.get("archive_sha256")
        archive_sha = (
            None
            if raw_archive_sha is None
            else _sha(str(raw_archive_sha), "download archive checksum")
        )
        _nonblank(row.get("raw_logical_uri"), "download raw logical URI")
        _sha(
            str(row.get("download_identity_checksum", "")),
            "download identity checksum",
        )
        download_key = (key, object_key)
        if download_key in download_archives:
            raise FD1ReadinessCloseoutError("used downloads repeat a source/object key")
        download_archives[download_key] = archive_sha
        counts[key] += 1
    if set(counts) != set(REQUIRED_SOURCE_KEYS):
        raise FD1ReadinessCloseoutError("used downloads do not cover every required source")
    for raw in _sequence(operator.get("extractions_used"), "extractions"):
        row = _mapping(raw, "extraction")
        key = (
            _nonblank(row.get("source_key"), "extraction source_key"),
            _nonblank(row.get("object_key"), "extraction object_key"),
        )
        if download_archives.get(key) != _sha(
            str(row.get("archive_sha256", "")), "extraction archive checksum"
        ):
            raise FD1ReadinessCloseoutError(
                "an extraction does not match its registered download"
            )
    authorized = set(warehouse.authorization_recorded_source_keys)
    classified = set(warehouse.use_classification_recorded_source_keys)
    licensed = set(warehouse.license_recorded_source_keys)
    attribution_required = set(warehouse.attribution_required_source_keys)
    attribution_recorded = set(warehouse.attribution_recorded_source_keys)
    return tuple(
        SourceSummary(
            source_key=key,
            source_version=_nonblank(
                _mapping(versions[key], f"source {key}").get("dataset_version"),
                f"{key} dataset_version",
            ),
            artifact_count=counts[key],
            checksum_verified_artifact_count=counts[key],
            required_for_readiness=True,
            authorization_recorded=key in authorized,
            use_classification_recorded=key in classified,
            license_recorded=key in licensed,
            attribution_required=key in attribution_required,
            attribution_recorded=key in attribution_recorded,
        )
        for key in REQUIRED_SOURCE_KEYS
    )


def _prior_rows_by_season(operator: Mapping[str, Any]) -> dict[int, int]:
    priors = _mapping(operator.get("player_priors"), "player priors")
    rows = _int_key_mapping(
        _mapping(priors.get("rows_by_season"), "player-prior season counts")
    )
    if set(rows) != set(EXPECTED_REGULAR_SEASON_GAMES):
        raise FD1ReadinessCloseoutError("player-prior seasons are incomplete")
    if sum(rows.values()) != _nonnegative_int(
        priors.get("published_rows"), "published player-prior rows"
    ):
        raise FD1ReadinessCloseoutError("player-prior row counts do not reconcile")
    if priors.get("rows") != priors.get("published_rows"):
        raise FD1ReadinessCloseoutError("selected and published player priors differ")
    return rows


def _park_rows_by_target_season(operator: Mapping[str, Any]) -> dict[int, int]:
    readiness = _mapping(operator.get("readiness_evidence"), "readiness evidence")
    park = _mapping(
        readiness.get("prior_period_park_factor_formability"), "park-factor build"
    )
    if park.get("formable") is not True:
        raise FD1ReadinessCloseoutError("prior-period park factors are not formable")
    seasons = _sequence(park.get("seasons"), "park-factor seasons")
    result: dict[int, int] = {}
    for raw in seasons:
        row = _mapping(raw, "park-factor season")
        target = _positive_int(row.get("target_season"), "park target season")
        if target in result:
            raise FD1ReadinessCloseoutError("duplicate park target season")
        if row.get("formable") is not True:
            raise FD1ReadinessCloseoutError(f"park factors are not formable for {target}")
        prior_seasons = tuple(
            _positive_int(value, "park prior season")
            for value in _sequence(row.get("prior_seasons"), "park prior seasons")
        )
        if not prior_seasons or any(season >= target for season in prior_seasons):
            raise FD1ReadinessCloseoutError("park factors contain target/future evidence")
        result[target] = _positive_int(row.get("factor_count"), "park factor_count")
    if set(result) != set(PRIMARY_SEASONS):
        raise FD1ReadinessCloseoutError("park factors do not cover all primary seasons")
    if sum(result.values()) != _positive_int(park.get("factor_count"), "park factor total"):
        raise FD1ReadinessCloseoutError("park factor counts do not reconcile")
    return result


def _season_summary(
    row: FD1SeasonWarehouseEvidence,
    identity: ActiveIdentityCoverageReport,
    *,
    player_prior_rows: int,
    park_factor_rows: int,
) -> SeasonImportSummary:
    coverage = next(
        (item for item in identity.season_coverage if item.seasons == (row.season,)),
        None,
    )
    if coverage is None:
        raise FD1ReadinessCloseoutError(
            f"active identity coverage is missing season {row.season}"
        )
    link = coverage.relation(IdentityRelation.RETROSHEET_TO_CHADWICK)
    unresolved = tuple(
        item.retrosheet_id
        for item in identity.affected_players
        if row.season in item.seasons
        and item.retrosheet_to_chadwick_status is not ResolutionStatus.EXACT
    )
    return SeasonImportSummary(
        season=row.season,
        population=(
            DatasetPopulation.PRIMARY
            if row.season in PRIMARY_SEASONS
            else DatasetPopulation.PRIOR_ONLY
        ),
        expected_regular_season_games=EXPECTED_REGULAR_SEASON_GAMES[row.season],
        imported_regular_season_games=row.imported_regular_season_games,
        valid_final_games=row.valid_final_games,
        home_win_games=row.home_win_games,
        away_win_games=row.away_win_games,
        plate_appearance_games=row.plate_appearance_games,
        plate_appearances=row.plate_appearances,
        transition_games=row.transition_games,
        transitions=row.transitions,
        identity_unique_player_count=link.source_unique_id_count,
        resolved_identity_unique_player_count=link.exact_unique_link_count,
        identity_reference_count=link.source_reference_count,
        resolved_identity_reference_count=link.exact_reference_count,
        unresolved_identity_ids=unresolved,
        complete_lineup_games=row.complete_lineup_games,
        lineup_timing_risk_games=row.lineup_timing_risk_games,
        starter_games=row.starter_games,
        starter_historical_reconstruction_games=(
            row.starter_historical_reconstruction_games
        ),
        starter_duration_rows=row.starter_duration_rows,
        bullpen_usage_games=row.bullpen_usage_games,
        bullpen_usage_rows=row.bullpen_usage_rows,
        fielding_games=row.fielding_games,
        fielding_rows=row.fielding_rows,
        weather_games=row.fully_covered_weather_games,
        pitch_sequence_games=row.pitch_sequence_games,
        taxonomy_terminal_events=row.taxonomy_terminal_events,
        taxonomy_mapped_terminal_events=row.taxonomy_mapped_terminal_events,
        taxonomy_outcome_counts=row.taxonomy_outcome_counts,
        unsafe_taxonomy_event_ids=row.unsafe_taxonomy_event_ids,
        blocked_partition_ids=row.blocked_partition_ids,
        player_prior_rows=player_prior_rows,
        park_factor_rows=park_factor_rows,
        tier_candidate_rows=row.tier_candidate_rows,
        tier_eligible_rows=row.tier_eligible_rows,
        strict_deployable_rows=row.strict_deployable_rows,
        enhanced_research_rows=row.enhanced_research_rows,
        prior_only_rows=row.prior_only_rows,
    )


def _validate_identity_conflict_projection(
    identity: ActiveIdentityCoverageReport,
    supplied: tuple[IdentityConflict, ...],
) -> None:
    active_pairs = {
        (issue.retrosheet_id, season)
        for issue in identity.affected_players
        if issue.retrosheet_to_chadwick_status is ResolutionStatus.CONFLICT
        for season in issue.seasons
    }
    supplied_pairs = {(row.external_id, row.season) for row in supplied}
    if active_pairs != supplied_pairs:
        raise FD1ReadinessCloseoutError(
            "exact candidate-bearing identity conflicts do not match the active audit"
        )
    if any(row.source_namespace != "RETROSHEET" for row in supplied):
        raise FD1ReadinessCloseoutError(
            "active identity conflicts must use the RETROSHEET namespace"
        )


def _validate_active_identity_summary(
    identity: ActiveIdentityCoverageReport,
    operator: Mapping[str, Any],
) -> None:
    if identity.derivation_checksum != _identity_derivation_checksum(identity):
        raise FD1ReadinessCloseoutError(
            "active identity derivation checksum does not match its contents"
        )
    versions = _mapping(operator.get("source_versions"), "source versions")
    retrosheet_version = _nonblank(
        _mapping(
            versions["RETROSHEET_CSV_DOWNLOADS"],
            "Retrosheet identity source version",
        ).get("dataset_version"),
        "Retrosheet identity dataset version",
    )
    if identity.source_release != retrosheet_version or any(
        row.source_release != retrosheet_version for row in identity.source_files
    ):
        raise FD1ReadinessCloseoutError(
            "active identity evidence is from a different Retrosheet release"
        )
    for row in identity.crosswalk_source_files:
        if row.source_key not in versions:
            raise FD1ReadinessCloseoutError(
                "identity crosswalk source is outside the operator ledger"
            )
        expected_release = _nonblank(
            _mapping(versions[row.source_key], "identity crosswalk source").get(
                "dataset_version"
            ),
            "identity crosswalk source release",
        )
        if row.source_release != expected_release:
            raise FD1ReadinessCloseoutError(
                "identity crosswalk evidence is from a different source release"
            )
    slices = (
        *identity.season_coverage,
        *identity.population_coverage,
        identity.overall_coverage,
    )
    for coverage in slices:
        if any(
            row.source_unique_id_count != coverage.unique_retrosheet_players
            or row.source_reference_count != coverage.reference_count
            for row in coverage.exact_link_rates
        ):
            raise FD1ReadinessCloseoutError(
                f"identity link denominators cross coverage slice {coverage.label}"
            )
    seasons = {row.seasons[0]: row for row in identity.season_coverage}
    population_seasons = {
        identity.population_coverage[0].population: (2021, 2022),
        identity.population_coverage[1].population: (2023, 2024, 2025),
    }
    for population in identity.population_coverage:
        expected_seasons = population_seasons[population.population]
        if population.seasons != expected_seasons:
            raise FD1ReadinessCloseoutError(
                "identity population seasons do not match the FD1 split"
            )
        for relation in IdentityRelation:
            combined = population.relation(relation)
            rows = tuple(seasons[season].relation(relation) for season in expected_seasons)
            for field_name in (
                "source_reference_count",
                "exact_reference_count",
                "conflict_reference_count",
                "unresolved_reference_count",
            ):
                if getattr(combined, field_name) != sum(
                    getattr(row, field_name) for row in rows
                ):
                    raise FD1ReadinessCloseoutError(
                        "identity population reference counts do not reconcile"
                    )
    for relation in IdentityRelation:
        overall = identity.overall_coverage.relation(relation)
        populations = tuple(
            row.relation(relation) for row in identity.population_coverage
        )
        for field_name in (
            "source_reference_count",
            "exact_reference_count",
            "conflict_reference_count",
            "unresolved_reference_count",
        ):
            if getattr(overall, field_name) != sum(
                getattr(row, field_name) for row in populations
            ):
                raise FD1ReadinessCloseoutError(
                    "overall identity reference counts do not reconcile"
                )
    primary = identity.primary.relation(IdentityRelation.RETROSHEET_TO_CHADWICK)
    unique_pass = primary.exact_unique_rate >= identity.primary_threshold
    reference_pass = primary.exact_reference_rate >= identity.primary_threshold
    if (
        identity.primary_unique_meets_threshold != unique_pass
        or identity.primary_reference_meets_threshold != reference_pass
        or identity.primary_meets_threshold != (unique_pass and reference_pass)
    ):
        raise FD1ReadinessCloseoutError(
            "active identity threshold flags do not match their exact denominators"
        )
    if tuple(row.seasons[0] for row in identity.season_coverage) != ACTIVE_SEASONS:
        raise FD1ReadinessCloseoutError("active identity season coverage is incomplete")


def _identity_derivation_checksum(identity: ActiveIdentityCoverageReport) -> str:
    payload = {
        "schema_version": identity.schema_version,
        "source_release": identity.source_release,
        "source_files": [asdict(row) for row in identity.source_files],
        "crosswalk_source_files": [
            asdict(row) for row in identity.crosswalk_source_files
        ],
        "season_coverage": [asdict(row) for row in identity.season_coverage],
        "population_coverage": [asdict(row) for row in identity.population_coverage],
        "overall_coverage": asdict(identity.overall_coverage),
        "affected_players": [asdict(row) for row in identity.affected_players],
        "primary_threshold": identity.primary_threshold,
        "primary_unique_meets_threshold": identity.primary_unique_meets_threshold,
        "primary_reference_meets_threshold": (
            identity.primary_reference_meets_threshold
        ),
        "name_based_match_count": identity.name_based_match_count,
    }
    return sha256(
        json.dumps(
            _plain(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _asp_summary(result: ReconciliationResult) -> Asp2025CanonicalSummary:
    if result.target_season != 2025:
        raise FD1ReadinessCloseoutError("reconciliation target season is not 2025")
    if result.expected_target_games != ASP_2025_CANONICAL_REGULAR_SEASON_FINALS:
        raise FD1ReadinessCloseoutError("reconciliation target count changed")
    if len(result.games) != result.matched_game_count:
        raise FD1ReadinessCloseoutError("reconciliation game detail count changed")
    if result.matched_game_count + len(result.unmatched_asp) != result.asp_target_count:
        raise FD1ReadinessCloseoutError("ASP reconciliation counts do not reconcile")
    if (
        result.matched_game_count + len(result.unmatched_retrosheet)
        != result.retrosheet_target_count
    ):
        raise FD1ReadinessCloseoutError(
            "Retrosheet reconciliation counts do not reconcile"
        )
    if len({row.asp.game_id for row in result.games}) != len(result.games):
        raise FD1ReadinessCloseoutError("reconciliation repeats an ASP game")
    if len({row.retrosheet.raw_game_id for row in result.games}) != len(result.games):
        raise FD1ReadinessCloseoutError("reconciliation repeats a Retrosheet game")
    observed_methods = Counter(game.mapping_method for game in result.games)
    if dict(result.mapping_method_counts) != dict(observed_methods):
        raise FD1ReadinessCloseoutError(
            "reconciliation mapping-method counts changed"
        )
    if set(result.family_counts) != set(FIELD_FAMILIES):
        raise FD1ReadinessCloseoutError("reconciliation family set is incomplete")
    for family in FIELD_FAMILIES:
        observed = Counter(
            game.comparisons[family].status.value for game in result.games
        )
        declared = dict(result.family_counts[family])
        if set(declared) != {status.value for status in ComparisonStatus}:
            raise FD1ReadinessCloseoutError(
                f"reconciliation status set changed for {family}"
            )
        if any(
            declared.get(status.value, 0) != observed[status.value]
            for status in ComparisonStatus
        ):
            raise FD1ReadinessCloseoutError(
                f"reconciliation family counts changed for {family}"
            )
    conflicts: list[GameReconciliationConflict] = []
    exact = 0
    for game in result.games:
        authoritative_exact = True
        for family in _AUTHORITATIVE_RECONCILIATION_FAMILIES:
            comparison = game.comparisons[family]
            authoritative_exact &= comparison.status is ComparisonStatus.MATCH
            if family == "game_identity" and comparison.status is not ComparisonStatus.MATCH:
                raise FD1ReadinessCloseoutError(
                    "a mapped reconciliation game has conflicting game identity"
                )
        exact += authoritative_exact
        for family, kind in _CONFLICT_KIND_BY_FAMILY.items():
            comparison = game.comparisons[family]
            # Missing authoritative score/team evidence is blocking.  Optional
            # families become discrepancy objects only for an actual disagreement;
            # their one-sided missingness remains in the full reconciliation report.
            include = comparison.status is ComparisonStatus.CONFLICT or (
                family in {"team_date_number", "final_score"}
                and comparison.status is not ComparisonStatus.MATCH
            )
            if not include:
                continue
            conflicts.append(
                GameReconciliationConflict(
                    conflict_id=(
                        f"2025:{game.retrosheet.raw_game_id}:{family}"
                    ),
                    retrosheet_game_id=game.retrosheet.raw_game_id,
                    asp_game_id=game.asp.game_id,
                    kind=kind,
                    retrosheet_value=_compact_json(comparison.retrosheet_value),
                    asp_value=_compact_json(comparison.asp_value),
                    detail=f"{family} comparison status={comparison.status.value}",
                )
            )
    return Asp2025CanonicalSummary(
        canonical_regular_season_finals=result.asp_target_count,
        retrosheet_regular_season_finals=result.retrosheet_target_count,
        mapped_games=result.matched_game_count,
        exact_match_games=exact,
        missing_in_retrosheet=tuple(row.game_id for row in result.unmatched_asp),
        missing_in_asp=tuple(row.raw_game_id for row in result.unmatched_retrosheet),
        conflicts=tuple(conflicts),
    )


def _validate_reconciliation_operator_binding(
    operator: Mapping[str, Any],
    result: ReconciliationResult,
) -> None:
    asp_rows = [game.asp for game in result.games]
    asp_rows.extend(result.unmatched_asp)
    try:
        normalization = normalize_asp_schedule_game_numbers(asp_rows)
    except Fd1ReconciliationError as exc:
        raise FD1ReadinessCloseoutError(
            f"ASP game-number normalization is not readiness-safe: {exc}"
        ) from exc
    expected_normalization = normalization.as_dict()
    declared_preflight = _mapping(
        operator.get("asp_game_number_preflight"),
        "operator ASP game-number preflight",
    )
    bridge = _mapping(
        operator.get("schedule_fingerprint_bridge"),
        "operator schedule fingerprint bridge",
    )
    bridge_summary = _mapping(
        bridge.get("summary"), "operator schedule fingerprint bridge summary"
    )
    declared_bridge_normalization = _mapping(
        bridge_summary.get("asp_game_number_normalization"),
        "operator schedule bridge ASP game-number normalization",
    )
    if (
        dict(declared_preflight) != expected_normalization
        or dict(declared_bridge_normalization) != expected_normalization
    ):
        raise FD1ReadinessCloseoutError(
            "operator ASP game-number normalization differs from reconciliation"
        )

    evidence = _mapping(
        operator.get("retrosheet_source_evidence"),
        "operator Retrosheet source evidence",
    )
    _nonblank(evidence.get("logical_path"), "reconciliation logical path")
    record_count = _nonnegative_int(
        evidence.get("record_count"), "reconciliation record_count"
    )
    expected_count = _nonnegative_int(
        evidence.get("expected_record_count"), "reconciliation expected_record_count"
    )
    if record_count != ASP_2025_CANONICAL_REGULAR_SEASON_FINALS or (
        expected_count != ASP_2025_CANONICAL_REGULAR_SEASON_FINALS
    ):
        raise FD1ReadinessCloseoutError(
            "operator reconciliation record count changed"
        )
    retrosheet_rows = [game.retrosheet for game in result.games]
    retrosheet_rows.extend(result.unmatched_retrosheet)
    if len({row.raw_game_id for row in retrosheet_rows}) != len(retrosheet_rows):
        raise FD1ReadinessCloseoutError(
            "reconciliation evidence repeats a Retrosheet game"
        )
    serialized = b"".join(
        json.dumps(
            row.as_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
        for row in sorted(retrosheet_rows, key=lambda item: item.raw_game_id)
    )
    if len(retrosheet_rows) != record_count:
        raise FD1ReadinessCloseoutError(
            "reconciliation result is not bound to all operator Retrosheet rows"
        )
    if _sha(str(evidence.get("sha256", "")), "reconciliation evidence checksum") != (
        sha256(serialized).hexdigest()
    ):
        raise FD1ReadinessCloseoutError(
            "reconciliation evidence checksum does not match the operator"
        )
    if _positive_int(evidence.get("bytes"), "reconciliation evidence bytes") != len(
        serialized
    ):
        raise FD1ReadinessCloseoutError(
            "reconciliation evidence byte count does not match the operator"
        )
    derived = _mapping(
        evidence.get("derived_from_materialized_2025_rows"),
        "materialized reconciliation family counts",
    )
    if set(derived) != set(RECONCILIATION_MATERIALIZED_FAMILIES):
        raise FD1ReadinessCloseoutError(
            "operator reconciliation family set changed"
        )
    materialized_2025 = _mapping(
        _mapping(
            _mapping(
                operator.get("retrosheet_materialization"), "materialization"
            ).get("seasons"),
            "materialization seasons",
        ).get("2025"),
        "2025 materialization",
    )
    family_rows = _mapping(
        materialized_2025.get("family_rows"), "2025 materialized family rows"
    )
    for family in RECONCILIATION_MATERIALIZED_FAMILIES:
        count = _positive_int(derived.get(family), f"{family} reconciliation rows")
        if count != _positive_int(
            family_rows.get(family), f"{family} materialized rows"
        ):
            raise FD1ReadinessCloseoutError(
                f"{family} reconciliation rows do not match materialization"
            )
    expected_source_numbers = dict(
        Counter(row.source_game_number for row in retrosheet_rows)
    )
    if None in expected_source_numbers:
        raise FD1ReadinessCloseoutError(
            "operator reconciliation omitted source game-number semantics"
        )
    source_numbers = _int_key_mapping(
        _mapping(
            evidence.get("source_game_number_counts"),
            "source game-number counts",
        )
    )
    canonical_numbers = _int_key_mapping(
        _mapping(
            evidence.get("canonical_game_number_counts"),
            "canonical game-number counts",
        )
    )
    if source_numbers != expected_source_numbers or canonical_numbers != dict(
        Counter(row.game_number for row in retrosheet_rows)
    ):
        raise FD1ReadinessCloseoutError(
            "operator reconciliation game-number counts changed"
        )
    if (
        evidence.get("source_zero_normalization")
        != "RETROSHEET_SOURCE_0_SINGLE_TO_CANONICAL_1_IN_RECONCILER"
        or evidence.get("operator_game_number_workaround_applied") is not False
    ):
        raise FD1ReadinessCloseoutError(
            "operator reconciliation game-number semantics changed"
        )


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FD1ReadinessCloseoutError(f"{context} must be an object")
    return value


def _sequence(value: object, context: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise FD1ReadinessCloseoutError(f"{context} must be an array")
    return value


def _int_key_mapping(value: Mapping[str, Any]) -> dict[int, int]:
    result: dict[int, int] = {}
    for raw_key, raw_value in value.items():
        try:
            key = int(raw_key)
        except (TypeError, ValueError) as exc:
            raise FD1ReadinessCloseoutError("season key must be an integer") from exc
        if key in result:
            raise FD1ReadinessCloseoutError("duplicate normalized season key")
        result[key] = _nonnegative_int(raw_value, f"season {key} count")
    return result


def _nonnegative_int(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FD1ReadinessCloseoutError(f"{context} must be a nonnegative integer")
    return value


def _positive_int(value: object, context: str) -> int:
    result = _nonnegative_int(value, context)
    if result == 0:
        raise FD1ReadinessCloseoutError(f"{context} must be positive")
    return result


def _nonblank(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FD1ReadinessCloseoutError(f"{context} must be nonblank")
    return value.strip()


def _sha(value: str, context: str) -> str:
    token = value.lower()
    if not _SHA256.fullmatch(token):
        raise FD1ReadinessCloseoutError(f"{context} must be SHA-256")
    return token


def _unique_nonblank(values: Sequence[str], context: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise FD1ReadinessCloseoutError(f"{context} must be nonblank")
    if len(values) != len(set(values)):
        raise FD1ReadinessCloseoutError(f"{context} must be unique")


def _sorted_unique_nonblank(values: Sequence[str], context: str) -> None:
    _unique_nonblank(values, context)
    if tuple(sorted(values)) != tuple(values):
        raise FD1ReadinessCloseoutError(f"{context} must be sorted")


def _sorted_unique_source_person_ids(values: Sequence[str], context: str) -> None:
    _sorted_unique_nonblank(values, context)
    if any(not _RETROSHEET_PERSON_SOURCE_ID.fullmatch(value) for value in values):
        raise FD1ReadinessCloseoutError(
            f"{context} must use canonical Retrosheet person IDs"
        )


def _sorted_unique_source_game_ids(values: Sequence[str], context: str) -> None:
    _sorted_unique_nonblank(values, context)
    if any(not _RETROSHEET_GAME_SOURCE_ID.fullmatch(value) for value in values):
        raise FD1ReadinessCloseoutError(
            f"{context} must use canonical Retrosheet game IDs"
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(item) for item in value]
        return sorted(items, key=lambda item: _compact_json(item)) if isinstance(
            value, (set, frozenset)
        ) else items
    return value


def _compact_json(value: Any) -> str:
    return json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def _checksum(value: Any) -> str:
    return sha256(_compact_json(value).encode("utf-8")).hexdigest()


def canonical_fd1_closeout_checksum(value: Any) -> str:
    """Hash an operator/result payload using the closeout's canonical JSON form."""

    return _checksum(value)


__all__ = [
    "EXPECTED_COMPLETED_SUSPENDED_GAMES",
    "EXPECTED_RELOCATED_SUSPENDED_GAMES",
    "EXPECTED_REGULAR_SEASON_GAMES",
    "FD1_ACTUAL_OPERATOR_LOGICAL_PATH",
    "FD1_POSTCOMMIT_MODULE_LOGICAL_PATH",
    "FD1_READINESS_CLOSEOUT_SCHEMA_VERSION",
    "FD1_POSTCOMMIT_SCHEMA_VERSION",
    "FD1_POSTCOMMIT_SCRIPT_LOGICAL_PATH",
    "FD1_POSTCOMMIT_STATUS",
    "FD1_WAREHOUSE_EVIDENCE_SCHEMA_VERSION",
    "FD1ReadinessCloseout",
    "FD1ReadinessCloseoutError",
    "FD1ManagerCoverageEvidence",
    "FD1SeasonWarehouseEvidence",
    "FD1UmpireCoverageEvidence",
    "FD1WarehouseCloseoutEvidence",
    "MANAGER_USAGE_SCHEMA_VERSIONS",
    "MATERIALIZED_FAMILIES",
    "REQUIRED_SOURCE_KEYS",
    "UMPIRE_WEATHER_SCHEMA_VERSION",
    "build_fd1_readiness_closeout",
    "canonical_fd1_closeout_checksum",
]
