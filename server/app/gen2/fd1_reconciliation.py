"""Read-only Gate 3C-FD1 reconciliation of 2025 ASP and Retrosheet truth.

The reconciler deliberately has no acquisition, provider, model, or persistence
capability.  Canonical ASP evidence can be loaded from an existing SQLAlchemy session;
Retrosheet evidence is supplied as immutable typed values (or adapted from already
materialized rows).  Neither source is ever mutated or promoted over the other.

Game matching is exact on official date and explicitly cross-walked home/away teams.
Same-day rematches require either exact game numbers or a unique chronological ordinal;
anything still ambiguous fails closed instead of guessing.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.gen2.fd1_identity import (
    PersonCrosswalkReport,
    ResolutionStatus,
    RetrosheetOrganizationCrosswalkReport,
)
from app.models import (
    ExternalIdMapping,
    Game,
    GameFinalOutcome,
    GameStateObservation,
    GameStatus,
    GameTeam,
    LineupEntry,
    LineupObservation,
    PlayerGameStat,
    StarterObservation,
    Team,
    Venue,
)


TARGET_SEASON = 2025
TARGET_GAME_COUNT = 2_430
TARGET_TEAM_COUNT = 30
LIVE_SOURCE_MODE = "LIVE"
FINAL_OUTCOME_STATUS = "FINAL"
MLB_STATS_API_SOURCE = "MLB_STATS_API"
LAHMAN_TEAM_SEASON_MAPPING_SOURCE = "LAHMAN_TEAM_SEASON"
LAHMAN_PARK_MAPPING_SOURCE = "LAHMAN_PARK"
RETROSHEET_SINGLE_GAME_SOURCE_NUMBER = 0
CANONICAL_SINGLE_GAME_NUMBER = 1
MAX_ASP_SAME_DAY_MATCHUP_GAMES = 2
ASP_GAME_NUMBER_SOURCE_EXPLICIT = "SOURCE_EXPLICIT_CANONICAL_ORDINAL"
ASP_GAME_NUMBER_DERIVED_SINGLETON = "DERIVED_CANONICAL_SINGLETON_ORDINAL"
ASP_GAME_NUMBER_DERIVED_START_TIME = "DERIVED_CANONICAL_START_TIME_ORDINAL"
ASP_GAME_NUMBER_NORMALIZATION_METHODS = frozenset(
    {
        ASP_GAME_NUMBER_SOURCE_EXPLICIT,
        ASP_GAME_NUMBER_DERIVED_SINGLETON,
        ASP_GAME_NUMBER_DERIVED_START_TIME,
    }
)


class Fd1ReconciliationError(ValueError):
    """Inputs cannot be reconciled without an unsupported inference."""


class ComparisonStatus(str, Enum):
    MATCH = "MATCH"
    CONFLICT = "CONFLICT"
    ASP_MISSING = "ASP_MISSING"
    RETROSHEET_MISSING = "RETROSHEET_MISSING"
    BOTH_MISSING = "BOTH_MISSING"
    NOT_COMPARABLE = "NOT_COMPARABLE"


FIELD_FAMILIES = (
    "game_identity",
    "team_date_number",
    "final_score",
    "venue",
    "starters",
    "lineups",
    "batting_participation",
    "pitching_participation",
    "player_participation",
)


AUTHORITY_POLICY: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        "game_identity": MappingProxyType(
            {
                "canonical_authority": "ASP_LIVE_CANONICAL_DATABASE",
                "retrosheet_role": "INDEPENDENT_EXACT_IDENTITY_RECONCILIATION",
                "resolution": "PRESERVE_BOTH_FAIL_ON_AMBIGUITY",
            }
        ),
        "team_date_number": MappingProxyType(
            {
                "canonical_authority": "ASP_LIVE_CANONICAL_SCHEDULE",
                "retrosheet_role": "INDEPENDENT_HISTORICAL_CROSS_CHECK",
                "resolution": "REPORT_CONFLICT_NEVER_OVERWRITE",
            }
        ),
        "final_score": MappingProxyType(
            {
                "canonical_authority": "ASP_GAME_FINAL_OUTCOME",
                "retrosheet_role": "INDEPENDENT_FINAL_SCORE_CROSS_CHECK",
                "resolution": "REPORT_CONFLICT_NEVER_OVERWRITE",
            }
        ),
        "venue": MappingProxyType(
            {
                "canonical_authority": "ASP_CANONICAL_VENUE",
                "retrosheet_role": "PARK_ID_CROSS_CHECK_OR_GAP_CANDIDATE",
                "resolution": "GAPS_REQUIRE_SEPARATE_GOVERNED_WRITE",
            }
        ),
        "starters": MappingProxyType(
            {
                "canonical_authority": "SOURCE_SCOPED",
                "retrosheet_role": "ACTUAL_RETROSPECTIVE_STARTER_TRUTH",
                "asp_role": "EXISTING_LIVE_CONFIRMATION_WHEN_PRESENT",
                "resolution": "PRESERVE_BOTH_WITH_TIMING_SEMANTICS",
            }
        ),
        "lineups": MappingProxyType(
            {
                "canonical_authority": "SOURCE_SCOPED",
                "retrosheet_role": "ACTUAL_RETROSPECTIVE_LINEUP_TRUTH",
                "asp_role": "EXISTING_LIVE_CONFIRMED_LINEUP_WHEN_PRESENT",
                "resolution": "PRESERVE_BOTH_WITH_TIMING_SEMANTICS",
            }
        ),
        "batting_participation": MappingProxyType(
            {
                "canonical_authority": "SOURCE_SCOPED",
                "retrosheet_role": "RETROSPECTIVE_BOX_PARTICIPATION",
                "resolution": "REPORT_SET_DIFFERENCES_NEVER_OVERWRITE",
            }
        ),
        "pitching_participation": MappingProxyType(
            {
                "canonical_authority": "SOURCE_SCOPED",
                "retrosheet_role": "RETROSPECTIVE_BOX_PARTICIPATION",
                "resolution": "REPORT_SET_DIFFERENCES_NEVER_OVERWRITE",
            }
        ),
        "player_participation": MappingProxyType(
            {
                "canonical_authority": "SOURCE_SCOPED",
                "retrosheet_role": "RETROSPECTIVE_BOX_PARTICIPATION",
                "resolution": "REPORT_SET_DIFFERENCES_NEVER_OVERWRITE",
            }
        ),
    }
)


def _nonblank(value: object, context: str) -> str:
    token = str(value).strip()
    if not token:
        raise Fd1ReconciliationError(f"{context} cannot be blank")
    return token


def _optional_token(value: object | None) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def _frozen_ids(values: Iterable[object] | None, context: str) -> frozenset[str] | None:
    if values is None:
        return None
    result = frozenset(_nonblank(value, context) for value in values)
    return result


@dataclass(frozen=True, slots=True)
class AspGameEvidence:
    """One immutable canonical ASP LIVE final, plus comparable local evidence."""

    game_id: str
    official_date: date
    scheduled_at: datetime
    home_team_id: str
    away_team_id: str
    home_score: int
    away_score: int
    source_record_id: str
    source_version_id: str
    game_number: int | None = None
    doubleheader_id: str | None = None
    venue_id: str | None = None
    home_starter_id: str | None = None
    away_starter_id: str | None = None
    home_lineup_ids: frozenset[str] | None = None
    away_lineup_ids: frozenset[str] | None = None
    batting_participant_ids: frozenset[str] | None = None
    pitching_participant_ids: frozenset[str] | None = None
    player_participant_ids: frozenset[str] | None = None
    team_external_ids: Mapping[str, tuple[str, ...]] | None = None
    venue_external_ids: tuple[str, ...] = ()
    source_mode: str = LIVE_SOURCE_MODE
    outcome_status: str = FINAL_OUTCOME_STATUS

    def __post_init__(self) -> None:
        for field_name in (
            "game_id",
            "home_team_id",
            "away_team_id",
            "source_record_id",
            "source_version_id",
        ):
            _nonblank(getattr(self, field_name), f"ASP {field_name}")
        if self.home_team_id == self.away_team_id:
            raise Fd1ReconciliationError("ASP home and away teams must differ")
        if self.official_date.year != TARGET_SEASON:
            raise Fd1ReconciliationError(
                "ASP evidence must be from the 2025 target season"
            )
        if self.scheduled_at.tzinfo is None:
            raise Fd1ReconciliationError("ASP scheduled_at must be timezone-aware")
        if self.home_score < 0 or self.away_score < 0:
            raise Fd1ReconciliationError("ASP final scores cannot be negative")
        if self.game_number is not None and self.game_number < 1:
            raise Fd1ReconciliationError(
                "ASP game_number must be positive when present"
            )
        if self.source_mode != LIVE_SOURCE_MODE:
            raise Fd1ReconciliationError(
                "ASP reconciliation accepts LIVE evidence only"
            )
        if self.outcome_status != FINAL_OUTCOME_STATUS:
            raise Fd1ReconciliationError(
                "ASP reconciliation accepts FINAL outcomes only"
            )
        for field_name in (
            "home_lineup_ids",
            "away_lineup_ids",
            "batting_participant_ids",
            "pitching_participant_ids",
            "player_participant_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _frozen_ids(getattr(self, field_name), f"ASP {field_name}"),
            )
        team_external = {
            _nonblank(key, "ASP team external-id key"): tuple(
                sorted(_nonblank(value, "ASP team external id") for value in values)
            )
            for key, values in (self.team_external_ids or {}).items()
        }
        object.__setattr__(
            self,
            "team_external_ids",
            MappingProxyType(dict(sorted(team_external.items()))),
        )
        object.__setattr__(
            self,
            "venue_external_ids",
            tuple(
                sorted(
                    _nonblank(value, "ASP venue external id")
                    for value in self.venue_external_ids
                )
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "official_date": self.official_date.isoformat(),
            "scheduled_at": self.scheduled_at.isoformat(),
            "home_team_id": self.home_team_id,
            "away_team_id": self.away_team_id,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "source_record_id": self.source_record_id,
            "source_version_id": self.source_version_id,
            "game_number": self.game_number,
            "doubleheader_id": self.doubleheader_id,
            "venue_id": self.venue_id,
            "home_starter_id": self.home_starter_id,
            "away_starter_id": self.away_starter_id,
            "home_lineup_ids": _sorted_or_none(self.home_lineup_ids),
            "away_lineup_ids": _sorted_or_none(self.away_lineup_ids),
            "batting_participant_ids": _sorted_or_none(self.batting_participant_ids),
            "pitching_participant_ids": _sorted_or_none(self.pitching_participant_ids),
            "player_participant_ids": _sorted_or_none(self.player_participant_ids),
            "team_external_ids": {
                key: list(value)
                for key, value in sorted((self.team_external_ids or {}).items())
            },
            "venue_external_ids": list(self.venue_external_ids),
            "source_mode": self.source_mode,
            "outcome_status": self.outcome_status,
        }


@dataclass(frozen=True, slots=True)
class AspScheduleGameNumberNormalization:
    """Derived canonical ordinals used only by the schedule-fingerprint bridge.

    The mappings never replace or mutate ``AspGameEvidence.game_number``.  They make
    the derivation method and its exact input checksum explicit when the canonical
    ASP warehouse retained a nullable provider field.
    """

    canonical_game_numbers: Mapping[str, int]
    methods_by_game_id: Mapping[str, str]
    method_counts: Mapping[str, int]
    evidence_sha256: str

    def __post_init__(self) -> None:
        numbers = {
            _nonblank(game_id, "ASP normalization game id"): number
            for game_id, number in self.canonical_game_numbers.items()
        }
        methods = {
            _nonblank(game_id, "ASP normalization method game id"): _nonblank(
                method, "ASP normalization method"
            )
            for game_id, method in self.methods_by_game_id.items()
        }
        if not numbers or set(numbers) != set(methods):
            raise Fd1ReconciliationError(
                "ASP game-number normalization coverage is incomplete"
            )
        if any(
            isinstance(number, bool) or not isinstance(number, int) or number < 1
            for number in numbers.values()
        ):
            raise Fd1ReconciliationError(
                "ASP normalized canonical game numbers must be positive integers"
            )
        unknown_methods = set(methods.values()) - ASP_GAME_NUMBER_NORMALIZATION_METHODS
        if unknown_methods:
            raise Fd1ReconciliationError(
                "ASP game-number normalization method is unsupported: "
                f"{sorted(unknown_methods)}"
            )
        actual_counts = Counter(methods.values())
        supplied_counts: dict[str, int] = {}
        for method, count in self.method_counts.items():
            token = _nonblank(method, "ASP normalization count method")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise Fd1ReconciliationError(
                    "ASP game-number normalization counts must be nonnegative integers"
                )
            supplied_counts[token] = count
        if supplied_counts != dict(actual_counts):
            raise Fd1ReconciliationError(
                "ASP game-number normalization method counts do not match its games"
            )
        checksum = _nonblank(
            self.evidence_sha256, "ASP game-number normalization evidence checksum"
        )
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise Fd1ReconciliationError(
                "ASP game-number normalization evidence checksum must be lowercase SHA-256"
            )
        object.__setattr__(
            self,
            "canonical_game_numbers",
            MappingProxyType(dict(sorted(numbers.items()))),
        )
        object.__setattr__(
            self,
            "methods_by_game_id",
            MappingProxyType(dict(sorted(methods.items()))),
        )
        object.__setattr__(
            self,
            "method_counts",
            MappingProxyType(dict(sorted(supplied_counts.items()))),
        )
        object.__setattr__(self, "evidence_sha256", checksum)

    @property
    def game_count(self) -> int:
        return len(self.canonical_game_numbers)

    @property
    def source_explicit_count(self) -> int:
        return self.method_counts.get(ASP_GAME_NUMBER_SOURCE_EXPLICIT, 0)

    @property
    def derived_count(self) -> int:
        return self.game_count - self.source_explicit_count

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": "GATE_3C_FD1_ASP_CANONICAL_GAME_NUMBER_NORMALIZATION_V1",
            "target_season": TARGET_SEASON,
            "game_count": self.game_count,
            "asp_source_rows_mutated": False,
            "source_explicit_count": self.source_explicit_count,
            "source_missing_derived_count": self.derived_count,
            "source_field_persistence": {
                "status": (
                    "AVAILABLE_FOR_ALL_GAMES"
                    if self.derived_count == 0
                    else (
                        "UNAVAILABLE_FOR_ALL_GAMES"
                        if self.source_explicit_count == 0
                        else "PARTIALLY_UNAVAILABLE"
                    )
                ),
                "persistence_gap": self.derived_count > 0,
                "source_evidence_preserved": True,
            },
            "method_counts": dict(self.method_counts),
            "evidence_sha256": self.evidence_sha256,
            "derivation_scope": "SCHEDULE_FINGERPRINT_AND_EXACT_GAME_PAIRING_ONLY",
            "archived_provider_cross_check": {
                "role": "INDEPENDENT_AUDIT_ONLY_NOT_RUNTIME_NORMALIZATION_INPUT",
                "selection_method": (
                    "EXACT_GAMEPK_OFFICIAL_DATE_MLB_SIDES_SCHEDULED_AT_GAME_TYPE_R"
                ),
                "duplicate_gamepk_rows_may_not_be_defaulted": True,
            },
            "basis_fields": [
                "asp_game_id",
                "asp_source_record_id",
                "official_date",
                "scheduled_at_utc",
                "home_team_id",
                "away_team_id",
                "source_game_number",
                "source_doubleheader_id",
                "same_day_matchup_size",
            ],
        }


@dataclass(frozen=True, slots=True)
class RetrosheetGameEvidence:
    """One immutable Retrosheet game/box record in source-native identities."""

    raw_game_id: str
    source_game_id: str
    game_date: date
    home_team_raw_id: str
    away_team_raw_id: str
    game_number: int
    home_score: int | None
    away_score: int | None
    source_game_number: int | None = None
    doubleheader_id: str | None = None
    park_raw_id: str | None = None
    home_starter_raw_id: str | None = None
    away_starter_raw_id: str | None = None
    home_lineup_raw_ids: frozenset[str] | None = None
    away_lineup_raw_ids: frozenset[str] | None = None
    batting_participant_raw_ids: frozenset[str] | None = None
    pitching_participant_raw_ids: frozenset[str] | None = None
    player_participant_raw_ids: frozenset[str] | None = None
    game_type: str = "regular"
    score_evidence: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "raw_game_id",
            "source_game_id",
            "home_team_raw_id",
            "away_team_raw_id",
        ):
            _nonblank(getattr(self, field_name), f"Retrosheet {field_name}")
        if self.home_team_raw_id == self.away_team_raw_id:
            raise Fd1ReconciliationError("Retrosheet home and away teams must differ")
        if self.game_date.year != TARGET_SEASON:
            raise Fd1ReconciliationError(
                "Retrosheet evidence must be from the 2025 target season"
            )
        if self.game_number < 1:
            raise Fd1ReconciliationError("Retrosheet game_number must be positive")
        if self.source_game_number is not None:
            if self.source_game_number < RETROSHEET_SINGLE_GAME_SOURCE_NUMBER:
                raise Fd1ReconciliationError(
                    "Retrosheet source_game_number cannot be negative"
                )
            expected_canonical = (
                CANONICAL_SINGLE_GAME_NUMBER
                if self.source_game_number == RETROSHEET_SINGLE_GAME_SOURCE_NUMBER
                else self.source_game_number
            )
            if self.game_number != expected_canonical:
                raise Fd1ReconciliationError(
                    "Retrosheet canonical game_number does not match source semantics"
                )
            if self.source_game_number == RETROSHEET_SINGLE_GAME_SOURCE_NUMBER:
                if self.doubleheader_id is not None:
                    raise Fd1ReconciliationError(
                        "Retrosheet source game number 0 cannot have a doubleheader id"
                    )
        if (self.home_score is None) != (self.away_score is None):
            raise Fd1ReconciliationError(
                "Retrosheet final scores must be both present or absent"
            )
        if self.home_score is not None and (self.home_score < 0 or self.away_score < 0):
            raise Fd1ReconciliationError("Retrosheet final scores cannot be negative")
        if self.game_type.strip().lower() not in {"regular", "r", "reg"}:
            raise Fd1ReconciliationError(
                "Retrosheet target evidence must be regular season"
            )
        for field_name in (
            "home_lineup_raw_ids",
            "away_lineup_raw_ids",
            "batting_participant_raw_ids",
            "pitching_participant_raw_ids",
            "player_participant_raw_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _frozen_ids(getattr(self, field_name), f"Retrosheet {field_name}"),
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_game_id": self.raw_game_id,
            "source_game_id": self.source_game_id,
            "game_date": self.game_date.isoformat(),
            "home_team_raw_id": self.home_team_raw_id,
            "away_team_raw_id": self.away_team_raw_id,
            "game_number": self.game_number,
            "source_game_number": self.source_game_number,
            "game_number_semantics": (
                "SOURCE_0_SINGLE_NORMALIZED_TO_CANONICAL_1"
                if self.source_game_number == RETROSHEET_SINGLE_GAME_SOURCE_NUMBER
                else (
                    "SOURCE_POSITIVE_NUMBER_PRESERVED"
                    if self.source_game_number is not None
                    else "CALLER_SUPPLIED_CANONICAL_NUMBER"
                )
            ),
            "doubleheader_id": self.doubleheader_id,
            "park_raw_id": self.park_raw_id,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "home_starter_raw_id": self.home_starter_raw_id,
            "away_starter_raw_id": self.away_starter_raw_id,
            "home_lineup_raw_ids": _sorted_or_none(self.home_lineup_raw_ids),
            "away_lineup_raw_ids": _sorted_or_none(self.away_lineup_raw_ids),
            "batting_participant_raw_ids": _sorted_or_none(
                self.batting_participant_raw_ids
            ),
            "pitching_participant_raw_ids": _sorted_or_none(
                self.pitching_participant_raw_ids
            ),
            "player_participant_raw_ids": _sorted_or_none(
                self.player_participant_raw_ids
            ),
            "game_type": self.game_type,
            "score_evidence": self.score_evidence,
        }


@dataclass(frozen=True, slots=True)
class ExactIdentityCrosswalk:
    """Explicit source-to-canonical identity links; no names or fuzzy matching."""

    team_ids: Mapping[str, str]
    player_ids: Mapping[str, str]
    venue_ids: Mapping[str, str]
    team_matching_method: str = "EXPLICIT_ID_ONLY_NO_NAME_MATCHING"

    def __post_init__(self) -> None:
        object.__setattr__(self, "team_ids", _freeze_crosswalk(self.team_ids, "team"))
        object.__setattr__(
            self, "player_ids", _freeze_crosswalk(self.player_ids, "player")
        )
        object.__setattr__(
            self, "venue_ids", _freeze_crosswalk(self.venue_ids, "venue")
        )
        object.__setattr__(
            self,
            "team_matching_method",
            _nonblank(self.team_matching_method, "team matching method"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "team_mapping_count": len(self.team_ids),
            "player_mapping_count": len(self.player_ids),
            "venue_mapping_count": len(self.venue_ids),
            "matching_method": self.team_matching_method,
            "team_matching_method": self.team_matching_method,
            "player_venue_matching_method": "EXPLICIT_ID_ONLY_NO_NAME_MATCHING",
        }


@dataclass(frozen=True, slots=True)
class TeamScheduleFingerprintMatch:
    """One exact, name-free team match and its source-preserving score evidence."""

    retrosheet_team_id: str
    asp_team_id: str
    schedule_game_count: int
    schedule_fingerprint_sha256: str
    asp_score_fingerprint_sha256: str
    retrosheet_score_fingerprint_sha256: str
    score_match_game_count: int
    score_conflict_game_count: int

    def __post_init__(self) -> None:
        _nonblank(self.retrosheet_team_id, "Retrosheet team id")
        _nonblank(self.asp_team_id, "ASP team id")
        if self.schedule_game_count < 1:
            raise Fd1ReconciliationError("team schedule fingerprint cannot be empty")
        for field_name in (
            "schedule_fingerprint_sha256",
            "asp_score_fingerprint_sha256",
            "retrosheet_score_fingerprint_sha256",
        ):
            value = _nonblank(getattr(self, field_name), field_name)
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise Fd1ReconciliationError(f"{field_name} must be lowercase SHA-256")
        if self.score_match_game_count < 0 or self.score_conflict_game_count < 0:
            raise Fd1ReconciliationError(
                "team score comparison counts cannot be negative"
            )
        if (
            self.score_match_game_count + self.score_conflict_game_count
            != self.schedule_game_count
        ):
            raise Fd1ReconciliationError(
                "team score comparison counts must cover the full schedule"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "retrosheet_team_id": self.retrosheet_team_id,
            "asp_team_id": self.asp_team_id,
            "schedule_game_count": self.schedule_game_count,
            "schedule_fingerprint_sha256": self.schedule_fingerprint_sha256,
            "asp_score_fingerprint_sha256": self.asp_score_fingerprint_sha256,
            "retrosheet_score_fingerprint_sha256": (
                self.retrosheet_score_fingerprint_sha256
            ),
            "score_match_game_count": self.score_match_game_count,
            "score_conflict_game_count": self.score_conflict_game_count,
        }


@dataclass(frozen=True, slots=True)
class ScheduleFingerprintCrosswalkBuild:
    """Auditable 2025 schedule bridge built without team names or provider IDs."""

    crosswalk: ExactIdentityCrosswalk
    game_ids: Mapping[str, str]
    team_matches: tuple[TeamScheduleFingerprintMatch, ...]
    expected_target_games: int
    expected_team_count: int
    score_match_game_count: int
    score_conflict_raw_game_ids: tuple[str, ...]
    asp_game_number_normalization: AspScheduleGameNumberNormalization

    def __post_init__(self) -> None:
        if self.expected_target_games < 1:
            raise Fd1ReconciliationError("expected_target_games must be positive")
        if self.expected_team_count < 2:
            raise Fd1ReconciliationError("expected_team_count must be at least two")
        if (
            self.asp_game_number_normalization.game_count
            != self.expected_target_games
        ):
            raise Fd1ReconciliationError(
                "schedule fingerprint ASP game-number normalization is incomplete"
            )
        if len(self.crosswalk.team_ids) != self.expected_team_count:
            raise Fd1ReconciliationError(
                "schedule fingerprint team mapping is incomplete"
            )
        if len(set(self.crosswalk.team_ids.values())) != self.expected_team_count:
            raise Fd1ReconciliationError(
                "schedule fingerprint team mapping is not one-to-one"
            )
        frozen_games = _freeze_crosswalk(self.game_ids, "game")
        if len(frozen_games) != self.expected_target_games:
            raise Fd1ReconciliationError(
                "schedule fingerprint game mapping is incomplete"
            )
        if len(set(frozen_games.values())) != self.expected_target_games:
            raise Fd1ReconciliationError(
                "schedule fingerprint game mapping is not one-to-one"
            )
        object.__setattr__(self, "game_ids", frozen_games)
        if len(self.team_matches) != self.expected_team_count:
            raise Fd1ReconciliationError(
                "schedule fingerprint team evidence is incomplete"
            )
        evidence_pairs = tuple(
            (row.retrosheet_team_id, row.asp_team_id) for row in self.team_matches
        )
        expected_pairs = tuple(sorted(self.crosswalk.team_ids.items()))
        if evidence_pairs != expected_pairs:
            raise Fd1ReconciliationError(
                "schedule fingerprint team evidence does not match the crosswalk"
            )
        conflicts = tuple(sorted(set(self.score_conflict_raw_game_ids)))
        if conflicts != self.score_conflict_raw_game_ids:
            raise Fd1ReconciliationError(
                "score conflict game identities must be unique and deterministic"
            )
        if self.score_match_game_count + len(conflicts) != self.expected_target_games:
            raise Fd1ReconciliationError(
                "score comparisons must cover every schedule-mapped game"
            )

    @property
    def score_conflict_game_count(self) -> int:
        return len(self.score_conflict_raw_game_ids)

    def as_dict(
        self,
        *,
        include_team_matches: bool = True,
        include_game_ids: bool = False,
    ) -> dict[str, Any]:
        document: dict[str, Any] = {
            "contract": "GATE_3C_FD1_2025_SCHEDULE_FINGERPRINT_CROSSWALK_V2",
            "read_only": True,
            "writes_performed": 0,
            "target_season": TARGET_SEASON,
            "expected_target_games": self.expected_target_games,
            "expected_team_count": self.expected_team_count,
            "team_mapping_count": len(self.crosswalk.team_ids),
            "game_mapping_count": len(self.game_ids),
            "score_match_game_count": self.score_match_game_count,
            "score_conflict_game_count": self.score_conflict_game_count,
            "score_conflict_raw_game_ids": list(self.score_conflict_raw_game_ids),
            "team_bridge": "EXACT_FULL_SEASON_SCHEDULE_FINGERPRINT_NO_NAMES",
            "team_identity_fields": [
                "official_date",
                "home_away_role",
                "canonical_game_ordinal",
            ],
            "score_evidence_fields": ["team_runs", "opponent_runs"],
            "score_evidence_role": (
                "SOURCE_PRESERVING_COMPARISON_NOT_TEAM_IDENTITY_KEY"
            ),
            "name_based_match_count": 0,
            "player_mapping_count": len(self.crosswalk.player_ids),
            "venue_mapping_count": len(self.crosswalk.venue_ids),
            "asp_game_number_normalization": (
                self.asp_game_number_normalization.as_dict()
            ),
        }
        if include_team_matches:
            document["team_matches"] = [row.as_dict() for row in self.team_matches]
        if include_game_ids:
            document["game_ids"] = dict(self.game_ids)
        return document


@dataclass(frozen=True, slots=True)
class ReconciliationCrosswalkBuild:
    """Auditable result of the exact, provider-free identity bridge."""

    crosswalk: ExactIdentityCrosswalk
    expected_target_games: int
    asp_game_count: int
    retrosheet_game_count: int
    referenced_team_count: int
    referenced_player_count: int
    referenced_venue_count: int
    not_comparable_venue_raw_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.expected_target_games < 1:
            raise Fd1ReconciliationError("expected_target_games must be positive")
        if self.asp_game_count != self.expected_target_games:
            raise Fd1ReconciliationError("crosswalk ASP population is incomplete")
        if self.retrosheet_game_count != self.expected_target_games:
            raise Fd1ReconciliationError(
                "crosswalk Retrosheet population is incomplete"
            )
        if len(self.crosswalk.team_ids) != self.referenced_team_count:
            raise Fd1ReconciliationError("crosswalk team coverage is incomplete")
        if len(self.crosswalk.player_ids) != self.referenced_player_count:
            raise Fd1ReconciliationError("crosswalk player coverage is incomplete")
        if (
            len(self.crosswalk.venue_ids) + len(self.not_comparable_venue_raw_ids)
            != self.referenced_venue_count
        ):
            raise Fd1ReconciliationError("crosswalk venue coverage does not reconcile")
        if tuple(sorted(set(self.not_comparable_venue_raw_ids))) != (
            self.not_comparable_venue_raw_ids
        ):
            raise Fd1ReconciliationError(
                "not-comparable venue identities must be unique and deterministic"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": "GATE_3C_FD1_2025_EXACT_IDENTITY_CROSSWALK_V1",
            "read_only": True,
            "writes_performed": 0,
            "target_season": TARGET_SEASON,
            "expected_target_games": self.expected_target_games,
            "asp_game_count": self.asp_game_count,
            "retrosheet_game_count": self.retrosheet_game_count,
            "referenced_team_count": self.referenced_team_count,
            "referenced_player_count": self.referenced_player_count,
            "referenced_venue_count": self.referenced_venue_count,
            "not_comparable_venue_count": len(self.not_comparable_venue_raw_ids),
            "not_comparable_venue_raw_ids": list(self.not_comparable_venue_raw_ids),
            "team_bridge": (
                "RETROSHEET_TEAM_2025_TO_LAHMAN_TEAM_SEASON_EXACT_TO_"
                "CANONICAL_EXTERNAL_MAPPING"
            ),
            "team_mapping_source": LAHMAN_TEAM_SEASON_MAPPING_SOURCE,
            "player_bridge": (
                "CHADWICK_RETROSHEET_TO_MLBAM_EXACT_TO_" "CANONICAL_EXTERNAL_MAPPING"
            ),
            "player_mapping_source": MLB_STATS_API_SOURCE,
            "venue_bridge": (
                "RETROSHEET_PARK_TO_LAHMAN_PARK_EXACT_TO_OPTIONAL_"
                "CANONICAL_EXTERNAL_MAPPING"
            ),
            "venue_mapping_source": LAHMAN_PARK_MAPPING_SOURCE,
            "name_based_match_count": 0,
            **self.crosswalk.as_dict(),
        }


def build_2025_exact_identity_crosswalk(
    asp_games: Sequence[AspGameEvidence],
    retrosheet_games: Sequence[RetrosheetGameEvidence],
    *,
    person_crosswalk: PersonCrosswalkReport,
    organization_crosswalk: RetrosheetOrganizationCrosswalkReport,
    canonical_external_mappings: Sequence[ExternalIdMapping],
    expected_target_games: int = TARGET_GAME_COUNT,
) -> ReconciliationCrosswalkBuild:
    """Build an exact 2025 crosswalk from governed evidence already in memory.

    Team mappings require a source-proven Retrosheet-to-Lahman team-season link
    and an existing canonical ``ExternalIdMapping`` in the
    ``LAHMAN_TEAM_SEASON`` namespace.  Player mappings require exact Chadwick
    Retrosheet and MLBAM assertions for the same UUID followed by the existing
    MLBAM canonical mapping.  A park without both exact bridge legs is retained
    as explicitly not comparable; no venue identity is guessed.
    """

    if expected_target_games < 1:
        raise Fd1ReconciliationError("expected_target_games must be positive")
    if len(asp_games) != expected_target_games:
        raise Fd1ReconciliationError(
            "crosswalk ASP target population is incomplete: "
            f"expected {expected_target_games}, received {len(asp_games)}"
        )
    if len(retrosheet_games) != expected_target_games:
        raise Fd1ReconciliationError(
            "crosswalk Retrosheet target population is incomplete: "
            f"expected {expected_target_games}, received {len(retrosheet_games)}"
        )
    if person_crosswalk.name_based_match_count != 0:
        raise Fd1ReconciliationError("person crosswalk used prohibited name matching")
    if organization_crosswalk.name_based_match_count != 0:
        raise Fd1ReconciliationError(
            "organization crosswalk used prohibited name matching"
        )
    _require_unique((row.game_id for row in asp_games), "ASP game_id")
    _require_unique(
        (row.raw_game_id for row in retrosheet_games), "Retrosheet raw_game_id"
    )

    external = _canonical_external_mapping_index(canonical_external_mappings)
    asp_team_ids = {
        team_id
        for game in asp_games
        for team_id in (game.home_team_id, game.away_team_id)
    }
    raw_team_ids = {
        team_id
        for game in retrosheet_games
        for team_id in (game.home_team_raw_id, game.away_team_raw_id)
    }
    team_claims: dict[tuple[int, str], list[Any]] = defaultdict(list)
    for claim in organization_crosswalk.team_seasons:
        team_claims[(claim.season, claim.retrosheet_team_id)].append(claim)

    team_ids: dict[str, str] = {}
    for raw_id in sorted(raw_team_ids):
        source_id = _retrosheet_source_value(raw_id, "team")
        claims = team_claims.get((TARGET_SEASON, source_id), [])
        if len(claims) != 1:
            qualifier = "missing" if not claims else "ambiguous"
            raise Fd1ReconciliationError(
                f"{qualifier} exact 2025 Retrosheet team assertion for {raw_id!r}"
            )
        claim = claims[0]
        if (
            claim.resolution_status is not ResolutionStatus.EXACT
            or claim.canonical_team_season_id is None
        ):
            raise Fd1ReconciliationError(
                f"non-exact 2025 Retrosheet team assertion for {raw_id!r}"
            )
        key = (
            "team",
            LAHMAN_TEAM_SEASON_MAPPING_SOURCE,
            claim.canonical_team_season_id,
        )
        canonical_id = external.get(key)
        if canonical_id is None:
            raise Fd1ReconciliationError(
                "missing canonical Lahman team-season mapping for "
                f"{claim.canonical_team_season_id!r}"
            )
        if canonical_id not in asp_team_ids:
            raise Fd1ReconciliationError(
                f"{raw_id!r}: canonical team mapping is outside the ASP population"
            )
        team_ids[raw_id] = canonical_id
    if len(set(team_ids.values())) != len(team_ids):
        raise Fd1ReconciliationError(
            "2025 Retrosheet team assertions collapse canonical team identities"
        )
    if set(team_ids.values()) != asp_team_ids:
        raise Fd1ReconciliationError(
            "ASP team population is not exactly covered by Retrosheet team assertions"
        )

    raw_player_ids = _referenced_retrosheet_player_ids(retrosheet_games)
    player_ids = _build_exact_player_ids(
        raw_player_ids,
        person_crosswalk=person_crosswalk,
        external=external,
    )

    raw_venue_ids = {
        game.park_raw_id for game in retrosheet_games if game.park_raw_id is not None
    }
    park_claims: dict[str, list[Any]] = defaultdict(list)
    for claim in organization_crosswalk.parks:
        park_claims[claim.retrosheet_park_id].append(claim)
    venue_ids: dict[str, str] = {}
    not_comparable: list[str] = []
    for raw_id in sorted(raw_venue_ids):
        source_id = _retrosheet_source_value(raw_id, "park")
        claims = park_claims.get(source_id, [])
        if len(claims) > 1:
            raise Fd1ReconciliationError(
                f"ambiguous Retrosheet park assertion for {raw_id!r}"
            )
        if not claims:
            not_comparable.append(raw_id)
            continue
        claim = claims[0]
        if (
            claim.resolution_status is not ResolutionStatus.EXACT
            or claim.canonical_park_id is None
        ):
            not_comparable.append(raw_id)
            continue
        canonical_id = external.get(
            ("venue", LAHMAN_PARK_MAPPING_SOURCE, claim.canonical_park_id)
        )
        if canonical_id is None:
            not_comparable.append(raw_id)
            continue
        venue_ids[raw_id] = canonical_id

    crosswalk = ExactIdentityCrosswalk(
        team_ids=team_ids,
        player_ids=player_ids,
        venue_ids=venue_ids,
    )
    return ReconciliationCrosswalkBuild(
        crosswalk=crosswalk,
        expected_target_games=expected_target_games,
        asp_game_count=len(asp_games),
        retrosheet_game_count=len(retrosheet_games),
        referenced_team_count=len(raw_team_ids),
        referenced_player_count=len(raw_player_ids),
        referenced_venue_count=len(raw_venue_ids),
        not_comparable_venue_raw_ids=tuple(not_comparable),
    )


def normalize_asp_schedule_game_numbers(
    games: Sequence[AspGameEvidence],
) -> AspScheduleGameNumberNormalization:
    """Return fail-closed canonical ordinals without changing ASP source evidence.

    A missing number is derivable only for an exact singleton matchup or for an
    all-missing two-game same-day matchup with distinct authoritative start times.
    Explicit and missing values may never be mixed within one matchup group.
    """

    if not games:
        raise Fd1ReconciliationError(
            "ASP game-number normalization requires at least one game"
        )
    _require_unique((row.game_id for row in games), "ASP normalization game_id")
    _require_unique(
        (row.source_record_id for row in games),
        "ASP normalization source_record_id",
    )
    groups: dict[tuple[date, str, str], list[AspGameEvidence]] = defaultdict(list)
    for game in games:
        groups[(game.official_date, game.home_team_id, game.away_team_id)].append(game)

    canonical_numbers: dict[str, int] = {}
    methods: dict[str, str] = {}
    group_sizes: dict[str, int] = {}
    for key in sorted(groups, key=_identity_key_sort):
        group = groups[key]
        if len(group) > MAX_ASP_SAME_DAY_MATCHUP_GAMES:
            raise Fd1ReconciliationError(
                f"ASP same-day matchup group {key} contains {len(group)} games; "
                f"maximum is {MAX_ASP_SAME_DAY_MATCHUP_GAMES}"
            )
        supplied = [game.game_number for game in group]
        present = [number for number in supplied if number is not None]
        if present and len(present) != len(group):
            raise Fd1ReconciliationError(
                f"mixed ASP game-number availability for {key}"
            )
        if len(present) == len(group):
            expected = list(range(1, len(group) + 1))
            if sorted(present) != expected:
                raise Fd1ReconciliationError(
                    f"ASP canonical game-number sequence for {key} is "
                    f"{sorted(present)}; expected {expected}"
                )
            for game in group:
                assert game.game_number is not None
                canonical_numbers[game.game_id] = game.game_number
                methods[game.game_id] = ASP_GAME_NUMBER_SOURCE_EXPLICIT
                group_sizes[game.game_id] = len(group)
            continue
        if len(group) == 1:
            game = group[0]
            canonical_numbers[game.game_id] = CANONICAL_SINGLE_GAME_NUMBER
            methods[game.game_id] = ASP_GAME_NUMBER_DERIVED_SINGLETON
            group_sizes[game.game_id] = 1
            continue

        ordered = sorted(group, key=lambda row: row.scheduled_at)
        if len({row.scheduled_at for row in ordered}) != len(ordered):
            raise Fd1ReconciliationError(
                f"ambiguous ASP same-day matchup {key}: start times are not unique"
            )
        for canonical_number, game in enumerate(ordered, start=1):
            canonical_numbers[game.game_id] = canonical_number
            methods[game.game_id] = ASP_GAME_NUMBER_DERIVED_START_TIME
            group_sizes[game.game_id] = len(group)

    evidence_rows = []
    for game in sorted(
        games,
        key=lambda row: (
            row.official_date,
            row.home_team_id,
            row.away_team_id,
            canonical_numbers[row.game_id],
            row.scheduled_at,
            row.game_id,
        ),
    ):
        evidence_rows.append(
            {
                "asp_game_id": game.game_id,
                "asp_source_record_id": game.source_record_id,
                "official_date": game.official_date.isoformat(),
                "scheduled_at_utc": game.scheduled_at.astimezone(UTC).isoformat(),
                "home_team_id": game.home_team_id,
                "away_team_id": game.away_team_id,
                "source_game_number": game.game_number,
                "source_doubleheader_id": game.doubleheader_id,
                "same_day_matchup_size": group_sizes[game.game_id],
                "canonical_game_number": canonical_numbers[game.game_id],
                "method": methods[game.game_id],
            }
        )
    payload = json.dumps(
        evidence_rows,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return AspScheduleGameNumberNormalization(
        canonical_game_numbers=canonical_numbers,
        methods_by_game_id=methods,
        method_counts=dict(Counter(methods.values())),
        evidence_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _validated_asp_game_number_normalization(
    games: Sequence[AspGameEvidence],
    supplied: AspScheduleGameNumberNormalization | None,
) -> AspScheduleGameNumberNormalization:
    expected = normalize_asp_schedule_game_numbers(games)
    if supplied is None:
        return expected
    if (
        dict(supplied.canonical_game_numbers)
        != dict(expected.canonical_game_numbers)
        or dict(supplied.methods_by_game_id) != dict(expected.methods_by_game_id)
        or dict(supplied.method_counts) != dict(expected.method_counts)
        or supplied.evidence_sha256 != expected.evidence_sha256
    ):
        raise Fd1ReconciliationError(
            "supplied ASP game-number normalization does not match the source cohort"
        )
    return supplied


def build_2025_schedule_fingerprint_crosswalk(
    asp_games: Sequence[AspGameEvidence],
    retrosheet_games: Sequence[RetrosheetGameEvidence],
    *,
    player_ids: Mapping[str, str] | None = None,
    venue_ids: Mapping[str, str] | None = None,
    asp_game_number_normalization: AspScheduleGameNumberNormalization | None = None,
    expected_target_games: int = TARGET_GAME_COUNT,
    expected_team_count: int = TARGET_TEAM_COUNT,
) -> ScheduleFingerprintCrosswalkBuild:
    """Build a name-free team and game bridge from two complete 2025 schedules.

    Team identity is established only from the exact, ordered multiset of
    ``(official_date, home/away role, canonical game ordinal)`` values.  When
    the ASP source number is unavailable, the separately checksummed
    normalization contract derives that ordinal without mutating source rows.
    All team fingerprints must be unique in each source, and the resulting
    mapping must make the complete opponent-aware game-key sets identical.

    Runs are retained in separate source-specific fingerprints and compared only
    *after* identity is proven.  They therefore corroborate the mapping without
    forcing equal scores or hiding a legitimate reconciliation conflict.
    ``player_ids`` and ``venue_ids`` may carry separately governed exact mappings;
    this builder never infers either family.
    """

    if expected_target_games < 1:
        raise Fd1ReconciliationError("expected_target_games must be positive")
    if expected_team_count < 2:
        raise Fd1ReconciliationError("expected_team_count must be at least two")
    if len(asp_games) != expected_target_games:
        raise Fd1ReconciliationError(
            "schedule fingerprint ASP population is incomplete: "
            f"expected {expected_target_games}, received {len(asp_games)}"
        )
    if len(retrosheet_games) != expected_target_games:
        raise Fd1ReconciliationError(
            "schedule fingerprint Retrosheet population is incomplete: "
            f"expected {expected_target_games}, received {len(retrosheet_games)}"
        )
    _require_unique((row.game_id for row in asp_games), "ASP game_id")
    _require_unique(
        (row.source_record_id for row in asp_games), "ASP source_record_id"
    )
    _require_unique(
        (row.raw_game_id for row in retrosheet_games), "Retrosheet raw_game_id"
    )
    normalization = _validated_asp_game_number_normalization(
        asp_games, asp_game_number_normalization
    )
    _validate_retrosheet_schedule_game_numbers(retrosheet_games)
    if any(game.home_score is None for game in retrosheet_games):
        raise Fd1ReconciliationError(
            "schedule fingerprint Retrosheet finals require complete final scores"
        )

    asp_identity_rows, asp_score_rows = _asp_team_fingerprint_rows(
        asp_games, normalization.canonical_game_numbers
    )
    retro_identity_rows, retro_score_rows = _retrosheet_team_fingerprint_rows(
        retrosheet_games
    )
    _validate_full_team_schedule_population(
        asp_identity_rows,
        source="ASP",
        expected_target_games=expected_target_games,
        expected_team_count=expected_team_count,
    )
    _validate_full_team_schedule_population(
        retro_identity_rows,
        source="Retrosheet",
        expected_target_games=expected_target_games,
        expected_team_count=expected_team_count,
    )

    asp_by_fingerprint = _unique_team_schedule_index(asp_identity_rows, source="ASP")
    retro_by_fingerprint = _unique_team_schedule_index(
        retro_identity_rows, source="Retrosheet"
    )
    if set(asp_by_fingerprint) != set(retro_by_fingerprint):
        missing_in_asp = len(set(retro_by_fingerprint) - set(asp_by_fingerprint))
        missing_in_retrosheet = len(set(asp_by_fingerprint) - set(retro_by_fingerprint))
        raise Fd1ReconciliationError(
            "full-season team schedule fingerprint sets do not match: "
            f"missing_in_asp={missing_in_asp}, "
            f"missing_in_retrosheet={missing_in_retrosheet}"
        )
    team_ids = {
        retro_by_fingerprint[fingerprint]: asp_team_id
        for fingerprint, asp_team_id in asp_by_fingerprint.items()
    }
    if len(team_ids) != expected_team_count:
        raise Fd1ReconciliationError(
            "full-season schedule fingerprints did not resolve every team"
        )
    if len(set(team_ids.values())) != expected_team_count:
        raise Fd1ReconciliationError(
            "full-season schedule fingerprints collapse canonical team identities"
        )

    asp_game_index = _asp_schedule_game_index(
        asp_games, normalization.canonical_game_numbers
    )
    retro_game_index = _retrosheet_schedule_game_index(retrosheet_games, team_ids)
    if set(asp_game_index) != set(retro_game_index):
        missing_in_asp = len(set(retro_game_index) - set(asp_game_index))
        missing_in_retrosheet = len(set(asp_game_index) - set(retro_game_index))
        raise Fd1ReconciliationError(
            "opponent-aware canonical schedule game keys do not match after team "
            f"resolution: missing_in_asp={missing_in_asp}, "
            f"missing_in_retrosheet={missing_in_retrosheet}"
        )

    game_ids: dict[str, str] = {}
    score_conflicts: list[str] = []
    paired_games: list[tuple[AspGameEvidence, RetrosheetGameEvidence]] = []
    for key in sorted(asp_game_index, key=_schedule_game_key_sort):
        asp = asp_game_index[key]
        retro = retro_game_index[key]
        game_ids[retro.raw_game_id] = asp.game_id
        paired_games.append((asp, retro))
        if (asp.home_score, asp.away_score) != (
            retro.home_score,
            retro.away_score,
        ):
            score_conflicts.append(retro.raw_game_id)

    per_team_score_counts: dict[str, Counter[str]] = {
        raw_team_id: Counter() for raw_team_id in team_ids
    }
    for asp, retro in paired_games:
        score_status = (
            "match"
            if (asp.home_score, asp.away_score) == (retro.home_score, retro.away_score)
            else "conflict"
        )
        per_team_score_counts[retro.home_team_raw_id][score_status] += 1
        per_team_score_counts[retro.away_team_raw_id][score_status] += 1

    team_matches = tuple(
        TeamScheduleFingerprintMatch(
            retrosheet_team_id=raw_team_id,
            asp_team_id=asp_team_id,
            schedule_game_count=len(retro_identity_rows[raw_team_id]),
            schedule_fingerprint_sha256=_fingerprint_sha256(
                retro_identity_rows[raw_team_id]
            ),
            asp_score_fingerprint_sha256=_fingerprint_sha256(
                asp_score_rows[asp_team_id]
            ),
            retrosheet_score_fingerprint_sha256=_fingerprint_sha256(
                retro_score_rows[raw_team_id]
            ),
            score_match_game_count=per_team_score_counts[raw_team_id]["match"],
            score_conflict_game_count=per_team_score_counts[raw_team_id]["conflict"],
        )
        for raw_team_id, asp_team_id in sorted(team_ids.items())
    )
    crosswalk = ExactIdentityCrosswalk(
        team_ids=team_ids,
        player_ids=player_ids or {},
        venue_ids=venue_ids or {},
        team_matching_method="EXACT_FULL_SEASON_SCHEDULE_FINGERPRINT_NO_NAMES",
    )
    return ScheduleFingerprintCrosswalkBuild(
        crosswalk=crosswalk,
        game_ids=game_ids,
        team_matches=team_matches,
        expected_target_games=expected_target_games,
        expected_team_count=expected_team_count,
        score_match_game_count=expected_target_games - len(score_conflicts),
        score_conflict_raw_game_ids=tuple(sorted(score_conflicts)),
        asp_game_number_normalization=normalization,
    )


TeamScheduleIdentity = tuple[date, str, int]
TeamScoreEvidence = tuple[date, str, int, int, int]
ScheduleGameKey = tuple[date, str, str, int]


def _asp_team_fingerprint_rows(
    games: Sequence[AspGameEvidence],
    canonical_game_numbers: Mapping[str, int],
) -> tuple[
    dict[str, tuple[TeamScheduleIdentity, ...]],
    dict[str, tuple[TeamScoreEvidence, ...]],
]:
    identities: dict[str, list[TeamScheduleIdentity]] = defaultdict(list)
    scores: dict[str, list[TeamScoreEvidence]] = defaultdict(list)
    for game in games:
        game_number = canonical_game_numbers[game.game_id]
        identities[game.home_team_id].append(
            (game.official_date, "HOME", game_number)
        )
        identities[game.away_team_id].append(
            (game.official_date, "AWAY", game_number)
        )
        scores[game.home_team_id].append(
            (
                game.official_date,
                "HOME",
                game_number,
                game.home_score,
                game.away_score,
            )
        )
        scores[game.away_team_id].append(
            (
                game.official_date,
                "AWAY",
                game_number,
                game.away_score,
                game.home_score,
            )
        )
    return (
        {team_id: tuple(sorted(rows)) for team_id, rows in identities.items()},
        {team_id: tuple(sorted(rows)) for team_id, rows in scores.items()},
    )


def _retrosheet_team_fingerprint_rows(
    games: Sequence[RetrosheetGameEvidence],
) -> tuple[
    dict[str, tuple[TeamScheduleIdentity, ...]],
    dict[str, tuple[TeamScoreEvidence, ...]],
]:
    identities: dict[str, list[TeamScheduleIdentity]] = defaultdict(list)
    scores: dict[str, list[TeamScoreEvidence]] = defaultdict(list)
    for game in games:
        if game.home_score is None or game.away_score is None:
            raise Fd1ReconciliationError(
                f"{game.raw_game_id}: Retrosheet final score is unavailable"
            )
        identities[game.home_team_raw_id].append(
            (game.game_date, "HOME", game.game_number)
        )
        identities[game.away_team_raw_id].append(
            (game.game_date, "AWAY", game.game_number)
        )
        scores[game.home_team_raw_id].append(
            (
                game.game_date,
                "HOME",
                game.game_number,
                game.home_score,
                game.away_score,
            )
        )
        scores[game.away_team_raw_id].append(
            (
                game.game_date,
                "AWAY",
                game.game_number,
                game.away_score,
                game.home_score,
            )
        )
    return (
        {team_id: tuple(sorted(rows)) for team_id, rows in identities.items()},
        {team_id: tuple(sorted(rows)) for team_id, rows in scores.items()},
    )


def _validate_full_team_schedule_population(
    rows: Mapping[str, tuple[TeamScheduleIdentity, ...]],
    *,
    source: str,
    expected_target_games: int,
    expected_team_count: int,
) -> None:
    if len(rows) != expected_team_count:
        raise Fd1ReconciliationError(
            f"{source} schedule references {len(rows)} teams; "
            f"expected exactly {expected_team_count}"
        )
    total_team_games = expected_target_games * 2
    if total_team_games % expected_team_count:
        raise Fd1ReconciliationError(
            "target game and team counts do not imply a complete equal schedule"
        )
    expected_team_games = total_team_games // expected_team_count
    incomplete = {
        team_id: len(team_rows)
        for team_id, team_rows in rows.items()
        if len(team_rows) != expected_team_games
    }
    if incomplete:
        raise Fd1ReconciliationError(
            f"{source} team schedules are not complete at {expected_team_games} games: "
            f"{dict(sorted(incomplete.items()))}"
        )


def _unique_team_schedule_index(
    rows: Mapping[str, tuple[TeamScheduleIdentity, ...]],
    *,
    source: str,
) -> dict[tuple[TeamScheduleIdentity, ...], str]:
    grouped: dict[tuple[TeamScheduleIdentity, ...], list[str]] = defaultdict(list)
    for team_id, schedule in rows.items():
        grouped[schedule].append(team_id)
    collisions = [
        tuple(sorted(team_ids)) for team_ids in grouped.values() if len(team_ids) != 1
    ]
    if collisions:
        raise Fd1ReconciliationError(
            f"{source} team schedule fingerprint collision: {sorted(collisions)}"
        )
    return {schedule: team_ids[0] for schedule, team_ids in grouped.items()}


def _validate_retrosheet_schedule_game_numbers(
    games: Sequence[RetrosheetGameEvidence],
) -> None:
    groups: dict[tuple[date, str, str], list[RetrosheetGameEvidence]] = defaultdict(
        list
    )
    for game in games:
        if game.source_game_number is None:
            raise Fd1ReconciliationError(
                f"{game.raw_game_id}: Retrosheet source game number is unavailable"
            )
        groups[(game.game_date, game.home_team_raw_id, game.away_team_raw_id)].append(
            game
        )
    for key, group in groups.items():
        canonical_numbers = sorted(game.game_number for game in group)
        expected_canonical = list(range(1, len(group) + 1))
        if canonical_numbers != expected_canonical:
            raise Fd1ReconciliationError(
                f"Retrosheet canonical game-number sequence for {key} is "
                f"{canonical_numbers}; expected {expected_canonical}"
            )
        source_numbers = sorted(
            game.source_game_number
            for game in group
            if game.source_game_number is not None
        )
        expected_source = (
            [RETROSHEET_SINGLE_GAME_SOURCE_NUMBER]
            if len(group) == 1
            else expected_canonical
        )
        if source_numbers != expected_source:
            raise Fd1ReconciliationError(
                f"Retrosheet source game-number sequence for {key} is "
                f"{source_numbers}; expected {expected_source}"
            )
        if len(group) > 1:
            doubleheader_ids = {game.doubleheader_id for game in group}
            if None in doubleheader_ids or len(doubleheader_ids) != 1:
                raise Fd1ReconciliationError(
                    f"Retrosheet doubleheader identity is incomplete or conflicting for {key}"
                )


def _asp_schedule_game_index(
    games: Sequence[AspGameEvidence],
    canonical_game_numbers: Mapping[str, int],
) -> dict[ScheduleGameKey, AspGameEvidence]:
    result: dict[ScheduleGameKey, AspGameEvidence] = {}
    for game in games:
        game_number = canonical_game_numbers[game.game_id]
        key = (
            game.official_date,
            game.home_team_id,
            game.away_team_id,
            game_number,
        )
        if key in result:
            raise Fd1ReconciliationError(f"duplicate ASP canonical schedule key {key}")
        result[key] = game
    return result


def _retrosheet_schedule_game_index(
    games: Sequence[RetrosheetGameEvidence],
    team_ids: Mapping[str, str],
) -> dict[ScheduleGameKey, RetrosheetGameEvidence]:
    result: dict[ScheduleGameKey, RetrosheetGameEvidence] = {}
    for game in games:
        key = (
            game.game_date,
            team_ids[game.home_team_raw_id],
            team_ids[game.away_team_raw_id],
            game.game_number,
        )
        if key in result:
            raise Fd1ReconciliationError(
                f"duplicate mapped Retrosheet canonical schedule key {key}"
            )
        result[key] = game
    return result


def _schedule_game_key_sort(key: ScheduleGameKey) -> tuple[str, str, str, int]:
    return (key[0].isoformat(), key[1], key[2], key[3])


def _fingerprint_sha256(
    rows: Sequence[TeamScheduleIdentity] | Sequence[TeamScoreEvidence],
) -> str:
    normalized = [
        [value.isoformat() if isinstance(value, date) else value for value in row]
        for row in rows
    ]
    payload = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_external_mapping_index(
    rows: Sequence[ExternalIdMapping],
) -> Mapping[tuple[str, str, str], str]:
    result: dict[tuple[str, str, str], str] = {}
    for row in rows:
        key = (
            _nonblank(row.entity_type, "canonical mapping entity_type"),
            _nonblank(row.source, "canonical mapping source"),
            _nonblank(row.external_id, "canonical mapping external_id"),
        )
        canonical_id = _nonblank(row.entity_id, "canonical mapping entity_id")
        if key in result:
            raise Fd1ReconciliationError(
                "ambiguous canonical external mapping for "
                f"{key[0]}:{key[1]}:{key[2]}"
            )
        result[key] = canonical_id
    return MappingProxyType(result)


def _retrosheet_source_value(raw_id: str, family: str) -> str:
    token = _nonblank(raw_id, f"Retrosheet {family} id")
    prefix = f"retrosheet:{family}:"
    if token.startswith(prefix):
        return _nonblank(token[len(prefix) :], f"Retrosheet {family} source value")
    if token.startswith("retrosheet:"):
        raise Fd1ReconciliationError(
            f"Retrosheet {family} id uses the wrong namespace: {token!r}"
        )
    return token


def _referenced_retrosheet_player_ids(
    games: Sequence[RetrosheetGameEvidence],
) -> set[str]:
    result: set[str] = set()
    for game in games:
        for player_id in (game.home_starter_raw_id, game.away_starter_raw_id):
            if player_id is not None:
                result.add(player_id)
        for values in (
            game.home_lineup_raw_ids,
            game.away_lineup_raw_ids,
            game.batting_participant_raw_ids,
            game.pitching_participant_raw_ids,
            game.player_participant_raw_ids,
        ):
            if values is not None:
                result.update(values)
    return result


def _source_row_keys(assertion: Any) -> set[tuple[str, int, str]]:
    return {
        (row.file_sha256, row.row_number, row.row_sha256)
        for row in assertion.source_rows
    }


def _build_exact_player_ids(
    raw_player_ids: set[str],
    *,
    person_crosswalk: PersonCrosswalkReport,
    external: Mapping[tuple[str, str, str], str],
) -> dict[str, str]:
    retro_claims: dict[str, list[Any]] = defaultdict(list)
    exact_mlbam_by_uuid: dict[str, list[Any]] = defaultdict(list)
    conflicting_mlbam = []
    for assertion in person_crosswalk.namespace_assertions:
        if assertion.namespace == "RETROSHEET":
            retro_claims[assertion.external_id].append(assertion)
        elif assertion.namespace == "MLBAM":
            if (
                assertion.resolution_status is ResolutionStatus.EXACT
                and assertion.canonical_chadwick_uuid is not None
            ):
                exact_mlbam_by_uuid[assertion.canonical_chadwick_uuid].append(assertion)
            elif assertion.resolution_status is ResolutionStatus.CONFLICT:
                conflicting_mlbam.append(assertion)

    result: dict[str, str] = {}
    for raw_id in sorted(raw_player_ids):
        source_id = _retrosheet_source_value(raw_id, "player")
        claims = retro_claims.get(source_id, [])
        if len(claims) != 1:
            qualifier = "missing" if not claims else "ambiguous"
            raise Fd1ReconciliationError(
                f"{qualifier} exact Chadwick Retrosheet assertion for {raw_id!r}"
            )
        claim = claims[0]
        if (
            claim.resolution_status is not ResolutionStatus.EXACT
            or claim.canonical_chadwick_uuid is None
        ):
            raise Fd1ReconciliationError(
                f"non-exact Chadwick Retrosheet assertion for {raw_id!r}"
            )
        source_rows = _source_row_keys(claim)
        if any(
            source_rows.intersection(_source_row_keys(row)) for row in conflicting_mlbam
        ):
            raise Fd1ReconciliationError(
                f"conflicting Chadwick MLBAM assertion for {raw_id!r}"
            )
        mlbam_claims = [
            row
            for row in exact_mlbam_by_uuid.get(claim.canonical_chadwick_uuid, [])
            if source_rows.intersection(_source_row_keys(row))
        ]
        if len(mlbam_claims) != 1:
            qualifier = "missing" if not mlbam_claims else "ambiguous"
            raise Fd1ReconciliationError(
                f"{qualifier} exact Chadwick MLBAM assertion for {raw_id!r}"
            )
        mlbam_id = mlbam_claims[0].external_id
        canonical_id = external.get(("player", MLB_STATS_API_SOURCE, mlbam_id))
        if canonical_id is None:
            raise Fd1ReconciliationError(
                f"missing canonical MLBAM player mapping for {raw_id!r}"
            )
        result[raw_id] = canonical_id
    if len(set(result.values())) != len(result):
        raise Fd1ReconciliationError(
            "Retrosheet player assertions collapse canonical player identities"
        )
    return result


def _freeze_crosswalk(values: Mapping[str, str], family: str) -> Mapping[str, str]:
    result: dict[str, str] = {}
    for raw, canonical in values.items():
        raw_id = _nonblank(raw, f"{family} source id")
        canonical_id = _nonblank(canonical, f"{family} canonical id")
        previous = result.setdefault(raw_id, canonical_id)
        if previous != canonical_id:
            raise Fd1ReconciliationError(f"ambiguous {family} crosswalk for {raw_id!r}")
    return MappingProxyType(dict(sorted(result.items())))


@dataclass(frozen=True, slots=True)
class FieldComparison:
    status: ComparisonStatus
    asp_value: Any
    retrosheet_value: Any
    details: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "asp": _json_value(self.asp_value),
            "retrosheet": _json_value(self.retrosheet_value),
            "details": _json_value(dict(self.details)),
        }


@dataclass(frozen=True, slots=True)
class GameReconciliation:
    asp: AspGameEvidence
    retrosheet: RetrosheetGameEvidence
    mapping_method: str
    mapped_retrosheet: Mapping[str, Any]
    comparisons: Mapping[str, FieldComparison]

    def as_dict(self) -> dict[str, Any]:
        return {
            "asp": self.asp.as_dict(),
            "retrosheet": self.retrosheet.as_dict(),
            "mapping_method": self.mapping_method,
            "mapped_retrosheet": _json_value(dict(self.mapped_retrosheet)),
            "comparisons": {
                family: self.comparisons[family].as_dict() for family in FIELD_FAMILIES
            },
        }


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    target_season: int
    expected_target_games: int
    asp_target_count: int
    retrosheet_target_count: int
    matched_game_count: int
    mapping_method_counts: Mapping[str, int]
    family_counts: Mapping[str, Mapping[str, int]]
    games: tuple[GameReconciliation, ...]
    unmatched_asp: tuple[AspGameEvidence, ...]
    unmatched_retrosheet: tuple[RetrosheetGameEvidence, ...]
    crosswalk_summary: Mapping[str, Any]

    @property
    def conflict_count(self) -> int:
        return sum(
            counts.get(ComparisonStatus.CONFLICT.value, 0)
            for counts in self.family_counts.values()
        )

    @property
    def complete_target_mapping(self) -> bool:
        return (
            self.asp_target_count == self.expected_target_games
            and self.retrosheet_target_count == self.expected_target_games
            and self.matched_game_count == self.expected_target_games
            and not self.unmatched_asp
            and not self.unmatched_retrosheet
        )

    def as_dict(self, *, include_games: bool = True) -> dict[str, Any]:
        document = {
            "contract": "GATE_3C_FD1_2025_RETROSHEET_ASP_RECONCILIATION_V1",
            "read_only": True,
            "writes_performed": 0,
            "target_season": self.target_season,
            "expected_target_games": self.expected_target_games,
            "asp_target_count": self.asp_target_count,
            "retrosheet_target_count": self.retrosheet_target_count,
            "matched_game_count": self.matched_game_count,
            "unmatched_asp_count": len(self.unmatched_asp),
            "unmatched_retrosheet_count": len(self.unmatched_retrosheet),
            "ambiguous_mapping_count": 0,
            "complete_target_mapping": self.complete_target_mapping,
            "conflict_count": self.conflict_count,
            "mapping_method_counts": dict(self.mapping_method_counts),
            "family_counts": {
                family: dict(self.family_counts[family]) for family in FIELD_FAMILIES
            },
            "authority_policy": {
                family: dict(AUTHORITY_POLICY[family]) for family in FIELD_FAMILIES
            },
            "crosswalk_summary": dict(self.crosswalk_summary),
            "unmatched_asp": [row.as_dict() for row in self.unmatched_asp],
            "unmatched_retrosheet": [
                row.as_dict() for row in self.unmatched_retrosheet
            ],
        }
        if include_games:
            document["games"] = [row.as_dict() for row in self.games]
        return document

    def to_json(self, *, include_games: bool = True, indent: int | None = 2) -> str:
        return json.dumps(
            self.as_dict(include_games=include_games),
            sort_keys=True,
            indent=indent,
        )


def reconcile_2025_retrosheet_to_asp(
    asp_games: Sequence[AspGameEvidence],
    retrosheet_games: Sequence[RetrosheetGameEvidence],
    crosswalk: ExactIdentityCrosswalk,
    *,
    expected_target_games: int = TARGET_GAME_COUNT,
    allow_exact_ordinal_doubleheader_mapping: bool = True,
) -> ReconciliationResult:
    """Return a source-preserving exact reconciliation; never write or overwrite.

    ``expected_target_games`` defaults to the 2,430-game 2025 regular-season target.
    Tests and bounded pilots may explicitly supply a smaller expected population.
    """

    if expected_target_games < 1:
        raise Fd1ReconciliationError("expected_target_games must be positive")
    if len(asp_games) != expected_target_games:
        raise Fd1ReconciliationError(
            "ASP target population is incomplete: "
            f"expected {expected_target_games}, received {len(asp_games)}"
        )
    _require_unique((row.game_id for row in asp_games), "ASP game_id")
    _require_unique(
        (row.raw_game_id for row in retrosheet_games), "Retrosheet raw_game_id"
    )

    asp_groups: dict[tuple[date, str, str], list[AspGameEvidence]] = defaultdict(list)
    for game in asp_games:
        asp_groups[(game.official_date, game.home_team_id, game.away_team_id)].append(
            game
        )

    retro_groups: dict[tuple[date, str, str], list[RetrosheetGameEvidence]] = (
        defaultdict(list)
    )
    for game in retrosheet_games:
        home_id = _required_mapping(
            crosswalk.team_ids, game.home_team_raw_id, "home team", game.raw_game_id
        )
        away_id = _required_mapping(
            crosswalk.team_ids, game.away_team_raw_id, "away team", game.raw_game_id
        )
        if home_id == away_id:
            raise Fd1ReconciliationError(
                f"{game.raw_game_id}: team crosswalk collapses home and away identity"
            )
        retro_groups[(game.game_date, home_id, away_id)].append(game)

    matched: list[tuple[AspGameEvidence, RetrosheetGameEvidence, str]] = []
    unmatched_asp: list[AspGameEvidence] = []
    unmatched_retro: list[RetrosheetGameEvidence] = []
    for key in sorted(set(asp_groups) | set(retro_groups), key=_identity_key_sort):
        asp_rows = asp_groups.get(key, [])
        retro_rows = retro_groups.get(key, [])
        if not asp_rows:
            unmatched_retro.extend(retro_rows)
            continue
        if not retro_rows:
            unmatched_asp.extend(asp_rows)
            continue
        if len(asp_rows) != len(retro_rows):
            if len(asp_rows) > 1 or len(retro_rows) > 1:
                raise Fd1ReconciliationError(
                    f"ambiguous game identity {key}: {len(asp_rows)} ASP versus "
                    f"{len(retro_rows)} Retrosheet candidates"
                )
            unmatched_asp.extend(asp_rows)
            unmatched_retro.extend(retro_rows)
            continue
        matched.extend(
            _pair_identity_group(
                key,
                asp_rows,
                retro_rows,
                allow_exact_ordinal_doubleheader_mapping=(
                    allow_exact_ordinal_doubleheader_mapping
                ),
            )
        )

    records = tuple(
        _reconcile_game(asp, retro, method, crosswalk)
        for asp, retro, method in sorted(
            matched,
            key=lambda item: (
                item[0].official_date,
                item[0].scheduled_at,
                item[0].game_id,
            ),
        )
    )
    method_counts = Counter(record.mapping_method for record in records)
    family_counts: dict[str, Mapping[str, int]] = {}
    for family in FIELD_FAMILIES:
        counts = Counter(record.comparisons[family].status.value for record in records)
        family_counts[family] = MappingProxyType(
            {status.value: counts.get(status.value, 0) for status in ComparisonStatus}
        )
    return ReconciliationResult(
        target_season=TARGET_SEASON,
        expected_target_games=expected_target_games,
        asp_target_count=len(asp_games),
        retrosheet_target_count=len(retrosheet_games),
        matched_game_count=len(records),
        mapping_method_counts=MappingProxyType(dict(sorted(method_counts.items()))),
        family_counts=MappingProxyType(family_counts),
        games=records,
        unmatched_asp=tuple(sorted(unmatched_asp, key=lambda row: row.game_id)),
        unmatched_retrosheet=tuple(
            sorted(unmatched_retro, key=lambda row: row.raw_game_id)
        ),
        crosswalk_summary=MappingProxyType(crosswalk.as_dict()),
    )


def _pair_identity_group(
    key: tuple[date, str, str],
    asp_rows: Sequence[AspGameEvidence],
    retro_rows: Sequence[RetrosheetGameEvidence],
    *,
    allow_exact_ordinal_doubleheader_mapping: bool,
) -> list[tuple[AspGameEvidence, RetrosheetGameEvidence, str]]:
    if len(asp_rows) == 1:
        return [(asp_rows[0], retro_rows[0], "EXACT_DATE_HOME_AWAY")]

    asp_numbers = [row.game_number for row in asp_rows]
    retro_numbers = [row.game_number for row in retro_rows]
    if all(number is not None for number in asp_numbers):
        if len(set(asp_numbers)) != len(asp_numbers):
            raise Fd1ReconciliationError(f"ambiguous ASP game numbers for {key}")
        if len(set(retro_numbers)) != len(retro_numbers):
            raise Fd1ReconciliationError(f"ambiguous Retrosheet game numbers for {key}")
        if set(asp_numbers) != set(retro_numbers):
            raise Fd1ReconciliationError(
                f"conflicting same-day game-number sets for {key}: "
                f"ASP={sorted(asp_numbers)} Retrosheet={sorted(retro_numbers)}"
            )
        by_number = {row.game_number: row for row in retro_rows}
        return [
            (row, by_number[row.game_number], "EXACT_DATE_HOME_AWAY_GAME_NUMBER")
            for row in sorted(asp_rows, key=lambda item: (item.game_number or 0))
        ]

    if any(number is not None for number in asp_numbers):
        raise Fd1ReconciliationError(f"mixed ASP game-number availability for {key}")
    if not allow_exact_ordinal_doubleheader_mapping:
        raise Fd1ReconciliationError(
            f"ambiguous same-day rematch {key}: ASP game numbers are unavailable"
        )
    ordered_asp = sorted(asp_rows, key=lambda row: (row.scheduled_at, row.game_id))
    if len({row.scheduled_at for row in ordered_asp}) != len(ordered_asp):
        raise Fd1ReconciliationError(
            f"ambiguous same-day rematch {key}: ASP start times are not unique"
        )
    ordered_retro = sorted(retro_rows, key=lambda row: row.game_number)
    if len({row.game_number for row in ordered_retro}) != len(ordered_retro):
        raise Fd1ReconciliationError(
            f"ambiguous same-day rematch {key}: Retrosheet game numbers are not unique"
        )
    expected_numbers = list(range(1, len(ordered_retro) + 1))
    if [row.game_number for row in ordered_retro] != expected_numbers:
        raise Fd1ReconciliationError(
            f"ambiguous same-day rematch {key}: Retrosheet ordinal sequence is not "
            f"{expected_numbers}"
        )
    return [
        (asp, retro, "EXACT_DATE_HOME_AWAY_ORDINAL_START_ORDER")
        for asp, retro in zip(ordered_asp, ordered_retro, strict=True)
    ]


def _reconcile_game(
    asp: AspGameEvidence,
    retro: RetrosheetGameEvidence,
    mapping_method: str,
    crosswalk: ExactIdentityCrosswalk,
) -> GameReconciliation:
    mapped_home = crosswalk.team_ids[retro.home_team_raw_id]
    mapped_away = crosswalk.team_ids[retro.away_team_raw_id]
    mapped_venue = (
        crosswalk.venue_ids.get(retro.park_raw_id)
        if retro.park_raw_id is not None
        else None
    )
    mapped_home_starter = _map_optional(retro.home_starter_raw_id, crosswalk.player_ids)
    mapped_away_starter = _map_optional(retro.away_starter_raw_id, crosswalk.player_ids)
    mapped_home_lineup, home_lineup_unmapped = _map_ids(
        retro.home_lineup_raw_ids, crosswalk.player_ids
    )
    mapped_away_lineup, away_lineup_unmapped = _map_ids(
        retro.away_lineup_raw_ids, crosswalk.player_ids
    )
    mapped_batting, batting_unmapped = _map_ids(
        retro.batting_participant_raw_ids, crosswalk.player_ids
    )
    mapped_pitching, pitching_unmapped = _map_ids(
        retro.pitching_participant_raw_ids, crosswalk.player_ids
    )
    mapped_players, player_unmapped = _map_ids(
        retro.player_participant_raw_ids, crosswalk.player_ids
    )

    identity = {
        "official_date": retro.game_date,
        "home_team_id": mapped_home,
        "away_team_id": mapped_away,
    }
    comparisons: dict[str, FieldComparison] = {
        "game_identity": FieldComparison(
            ComparisonStatus.MATCH,
            {
                "game_id": asp.game_id,
                "official_date": asp.official_date,
                "home_team_id": asp.home_team_id,
                "away_team_id": asp.away_team_id,
            },
            {"raw_game_id": retro.raw_game_id, **identity},
            MappingProxyType({"mapping_method": mapping_method}),
        ),
        "team_date_number": _team_date_number_comparison(
            asp, retro, mapped_home, mapped_away
        ),
        "final_score": _scalar_comparison(
            {"home": asp.home_score, "away": asp.away_score},
            (
                None
                if retro.home_score is None
                else {"home": retro.home_score, "away": retro.away_score}
            ),
        ),
        "venue": _identity_comparison(
            asp.venue_id,
            retro.park_raw_id,
            mapped_venue,
            missing_mapping=(retro.park_raw_id is not None and mapped_venue is None),
        ),
        "starters": _sided_identity_comparison(
            {"home": asp.home_starter_id, "away": asp.away_starter_id},
            {
                "home": retro.home_starter_raw_id,
                "away": retro.away_starter_raw_id,
            },
            {"home": mapped_home_starter, "away": mapped_away_starter},
        ),
        "lineups": _sided_set_comparison(
            {"home": asp.home_lineup_ids, "away": asp.away_lineup_ids},
            {
                "home": retro.home_lineup_raw_ids,
                "away": retro.away_lineup_raw_ids,
            },
            {"home": mapped_home_lineup, "away": mapped_away_lineup},
            {"home": home_lineup_unmapped, "away": away_lineup_unmapped},
        ),
        "batting_participation": _set_comparison(
            asp.batting_participant_ids,
            retro.batting_participant_raw_ids,
            mapped_batting,
            batting_unmapped,
        ),
        "pitching_participation": _set_comparison(
            asp.pitching_participant_ids,
            retro.pitching_participant_raw_ids,
            mapped_pitching,
            pitching_unmapped,
        ),
        "player_participation": _set_comparison(
            asp.player_participant_ids,
            retro.player_participant_raw_ids,
            mapped_players,
            player_unmapped,
        ),
    }
    mapped = MappingProxyType(
        {
            "home_team_id": mapped_home,
            "away_team_id": mapped_away,
            "venue_id": mapped_venue,
            "home_starter_id": mapped_home_starter,
            "away_starter_id": mapped_away_starter,
            "home_lineup_ids": _sorted_or_none(mapped_home_lineup),
            "away_lineup_ids": _sorted_or_none(mapped_away_lineup),
            "batting_participant_ids": _sorted_or_none(mapped_batting),
            "pitching_participant_ids": _sorted_or_none(mapped_pitching),
            "player_participant_ids": _sorted_or_none(mapped_players),
        }
    )
    return GameReconciliation(
        asp=asp,
        retrosheet=retro,
        mapping_method=mapping_method,
        mapped_retrosheet=mapped,
        comparisons=MappingProxyType(comparisons),
    )


def _team_date_number_comparison(
    asp: AspGameEvidence,
    retro: RetrosheetGameEvidence,
    mapped_home: str,
    mapped_away: str,
) -> FieldComparison:
    asp_value = {
        "date": asp.official_date,
        "home_team_id": asp.home_team_id,
        "away_team_id": asp.away_team_id,
        "game_number": asp.game_number,
        "doubleheader_id": asp.doubleheader_id,
    }
    retro_value = {
        "date": retro.game_date,
        "home_team_raw_id": retro.home_team_raw_id,
        "away_team_raw_id": retro.away_team_raw_id,
        "mapped_home_team_id": mapped_home,
        "mapped_away_team_id": mapped_away,
        "game_number": retro.game_number,
        "source_game_number": retro.source_game_number,
        "doubleheader_id": retro.doubleheader_id,
    }
    conflicts: dict[str, Any] = {}
    if asp.official_date != retro.game_date:
        conflicts["date"] = True
    if asp.home_team_id != mapped_home:
        conflicts["home_team"] = True
    if asp.away_team_id != mapped_away:
        conflicts["away_team"] = True
    compared = ["date", "home_team", "away_team"]
    unavailable: list[str] = []
    if asp.game_number is not None:
        compared.append("game_number")
        if asp.game_number != retro.game_number:
            conflicts["game_number"] = True
    else:
        unavailable.append("asp.game_number")
    if asp.doubleheader_id is not None and retro.doubleheader_id is not None:
        compared.append("doubleheader_id")
        if asp.doubleheader_id != retro.doubleheader_id:
            conflicts["doubleheader_id"] = True
    elif asp.doubleheader_id is None:
        unavailable.append("asp.doubleheader_id")
    else:
        unavailable.append("retrosheet.doubleheader_id")
    return FieldComparison(
        ComparisonStatus.CONFLICT if conflicts else ComparisonStatus.MATCH,
        asp_value,
        retro_value,
        MappingProxyType(
            {
                "compared_components": compared,
                "unavailable_components": unavailable,
                "conflicting_components": conflicts,
            }
        ),
    )


def _scalar_comparison(asp_value: Any, retro_value: Any) -> FieldComparison:
    if asp_value is None and retro_value is None:
        status = ComparisonStatus.BOTH_MISSING
    elif asp_value is None:
        status = ComparisonStatus.ASP_MISSING
    elif retro_value is None:
        status = ComparisonStatus.RETROSHEET_MISSING
    else:
        status = (
            ComparisonStatus.MATCH
            if asp_value == retro_value
            else ComparisonStatus.CONFLICT
        )
    return FieldComparison(status, asp_value, retro_value, MappingProxyType({}))


def _identity_comparison(
    asp_value: str | None,
    retro_raw: str | None,
    retro_mapped: str | None,
    *,
    missing_mapping: bool,
) -> FieldComparison:
    if missing_mapping:
        return FieldComparison(
            ComparisonStatus.NOT_COMPARABLE,
            asp_value,
            {"raw": retro_raw, "mapped": None},
            MappingProxyType({"unmapped_retrosheet_ids": [retro_raw]}),
        )
    comparison = _scalar_comparison(asp_value, retro_mapped)
    return FieldComparison(
        comparison.status,
        asp_value,
        {"raw": retro_raw, "mapped": retro_mapped},
        comparison.details,
    )


def _sided_identity_comparison(
    asp: Mapping[str, str | None],
    raw: Mapping[str, str | None],
    mapped: Mapping[str, str | None],
) -> FieldComparison:
    unmapped = [
        raw[side] for side in ("home", "away") if raw[side] and not mapped[side]
    ]
    if unmapped:
        status = ComparisonStatus.NOT_COMPARABLE
    else:
        status = _availability_and_equality(asp, mapped)
    return FieldComparison(
        status,
        dict(asp),
        {side: {"raw": raw[side], "mapped": mapped[side]} for side in ("home", "away")},
        MappingProxyType({"unmapped_retrosheet_ids": sorted(unmapped)}),
    )


def _sided_set_comparison(
    asp: Mapping[str, frozenset[str] | None],
    raw: Mapping[str, frozenset[str] | None],
    mapped: Mapping[str, frozenset[str] | None],
    unmapped: Mapping[str, tuple[str, ...]],
) -> FieldComparison:
    all_unmapped = sorted({value for values in unmapped.values() for value in values})
    if all_unmapped:
        status = ComparisonStatus.NOT_COMPARABLE
    else:
        normalized_asp = {side: asp[side] for side in ("home", "away")}
        normalized_retro = {side: mapped[side] for side in ("home", "away")}
        status = _availability_and_equality(normalized_asp, normalized_retro)
    return FieldComparison(
        status,
        {side: _sorted_or_none(asp[side]) for side in ("home", "away")},
        {
            side: {
                "raw": _sorted_or_none(raw[side]),
                "mapped": _sorted_or_none(mapped[side]),
            }
            for side in ("home", "away")
        },
        MappingProxyType(
            {
                "unmapped_retrosheet_ids": all_unmapped,
                "differences": _sided_differences(asp, mapped),
            }
        ),
    )


def _set_comparison(
    asp: frozenset[str] | None,
    raw: frozenset[str] | None,
    mapped: frozenset[str] | None,
    unmapped: tuple[str, ...],
) -> FieldComparison:
    if unmapped:
        status = ComparisonStatus.NOT_COMPARABLE
    else:
        status = _availability_and_equality(asp, mapped)
    details: dict[str, Any] = {"unmapped_retrosheet_ids": list(unmapped)}
    if asp is not None and mapped is not None:
        details["asp_only"] = sorted(asp - mapped)
        details["retrosheet_only"] = sorted(mapped - asp)
    return FieldComparison(
        status,
        _sorted_or_none(asp),
        {"raw": _sorted_or_none(raw), "mapped": _sorted_or_none(mapped)},
        MappingProxyType(details),
    )


def _availability_and_equality(asp: Any, retro: Any) -> ComparisonStatus:
    asp_missing = asp is None or (
        isinstance(asp, Mapping) and all(value is None for value in asp.values())
    )
    retro_missing = retro is None or (
        isinstance(retro, Mapping) and all(value is None for value in retro.values())
    )
    if asp_missing and retro_missing:
        return ComparisonStatus.BOTH_MISSING
    if asp_missing:
        return ComparisonStatus.ASP_MISSING
    if retro_missing:
        return ComparisonStatus.RETROSHEET_MISSING
    return ComparisonStatus.MATCH if asp == retro else ComparisonStatus.CONFLICT


def _sided_differences(
    asp: Mapping[str, frozenset[str] | None],
    retro: Mapping[str, frozenset[str] | None],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for side in ("home", "away"):
        if asp[side] is not None and retro[side] is not None:
            result[side] = {
                "asp_only": sorted(asp[side] - retro[side]),
                "retrosheet_only": sorted(retro[side] - asp[side]),
            }
    return result


def _map_optional(raw: str | None, crosswalk: Mapping[str, str]) -> str | None:
    return crosswalk.get(raw) if raw is not None else None


def _map_ids(
    raw_ids: frozenset[str] | None,
    crosswalk: Mapping[str, str],
) -> tuple[frozenset[str] | None, tuple[str, ...]]:
    if raw_ids is None:
        return None, ()
    unmapped = tuple(sorted(raw_id for raw_id in raw_ids if raw_id not in crosswalk))
    mapped = frozenset(crosswalk[raw_id] for raw_id in raw_ids if raw_id in crosswalk)
    return mapped, unmapped


def _required_mapping(
    crosswalk: Mapping[str, str], raw_id: str, family: str, game_id: str
) -> str:
    try:
        return crosswalk[raw_id]
    except KeyError as exc:
        raise Fd1ReconciliationError(
            f"{game_id}: no exact {family} crosswalk for {raw_id!r}"
        ) from exc


def _require_unique(values: Iterable[str], context: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise Fd1ReconciliationError(
            f"duplicate {context} values: {sorted(duplicates)[:10]}"
        )


def _identity_key_sort(value: tuple[date, str, str]) -> tuple[str, str, str]:
    return value[0].isoformat(), value[1], value[2]


def _sorted_or_none(values: Iterable[str] | None) -> list[str] | None:
    return None if values is None else sorted(values)


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset, tuple, list)):
        items = [_json_value(item) for item in value]
        return sorted(items) if isinstance(value, (set, frozenset)) else items
    return value


@dataclass(slots=True)
class _MaterializedGameAccumulator:
    identity: dict[str, Any]
    final_play_number: int = -1
    away_score: int | None = None
    home_score: int | None = None
    starters: dict[str, set[str]] | None = None
    lineups: dict[str, set[str]] | None = None
    batting: set[str] | None = None
    pitching: set[str] | None = None
    players: set[str] | None = None

    def __post_init__(self) -> None:
        self.starters = {"home": set(), "away": set()}
        self.lineups = {"home": set(), "away": set()}
        self.batting = set()
        self.pitching = set()
        self.players = set()


def retrosheet_evidence_from_materialized_rows(
    *,
    play_transition_rows: Iterable[Mapping[str, Any]],
    starter_usage_rows: Iterable[Mapping[str, Any]] = (),
    player_game_batting_rows: Iterable[Mapping[str, Any]] = (),
    player_game_pitching_rows: Iterable[Mapping[str, Any]] = (),
    player_game_fielding_rows: Iterable[Mapping[str, Any]] = (),
) -> tuple[RetrosheetGameEvidence, ...]:
    """Adapt already-materialized 2025 rows without reading raw files or persistence."""

    games: dict[str, _MaterializedGameAccumulator] = {}

    def accumulator(
        row: Mapping[str, Any], family: str
    ) -> _MaterializedGameAccumulator:
        raw_game_id = _nonblank(row.get("retrosheet_game_id"), f"{family}.game")
        game_date = _materialized_date(row.get("game_date"), family)
        if game_date.year != TARGET_SEASON:
            raise Fd1ReconciliationError(f"{family}: non-2025 row is outside target")
        identity = {
            "source_game_id": _nonblank(row.get("game_id"), f"{family}.game_id"),
            "game_date": game_date,
            "home_team_raw_id": _nonblank(
                row.get("home_team_id"), f"{family}.home_team_id"
            ),
            "away_team_raw_id": _nonblank(
                row.get("visiting_team_id"), f"{family}.visiting_team_id"
            ),
            "source_game_number": _materialized_source_game_number(
                row.get("game_number"), family
            ),
            "doubleheader_id": _optional_token(row.get("doubleheader_id")),
            "park_raw_id": _optional_token(row.get("park_id")),
            "game_type": _nonblank(row.get("game_type"), f"{family}.game_type"),
        }
        identity["game_number"] = _canonical_retrosheet_game_number(
            identity["source_game_number"]
        )
        if (
            identity["source_game_number"] == RETROSHEET_SINGLE_GAME_SOURCE_NUMBER
            and identity["doubleheader_id"] is not None
        ):
            raise Fd1ReconciliationError(
                f"{raw_game_id}: source game number 0 cannot have a doubleheader id"
            )
        current = games.setdefault(
            raw_game_id, _MaterializedGameAccumulator(identity=identity)
        )
        if current.identity != identity:
            raise Fd1ReconciliationError(
                f"{raw_game_id}: materialized identity conflicts across families"
            )
        return current

    for row in play_transition_rows:
        current = accumulator(row, "play_transition")
        play_number = int(row.get("play_number"))
        if play_number > current.final_play_number:
            current.final_play_number = play_number
            current.away_score = int(row.get("visiting_score_after"))
            current.home_score = int(row.get("home_score_after"))
        elif play_number == current.final_play_number:
            candidate = (
                int(row.get("visiting_score_after")),
                int(row.get("home_score_after")),
            )
            if candidate != (current.away_score, current.home_score):
                raise Fd1ReconciliationError(
                    f"{row.get('retrosheet_game_id')}: conflicting final play rows"
                )

    for row in starter_usage_rows:
        current = accumulator(row, "starter_usage")
        if row.get("actual_starter_target") is not True:
            raise Fd1ReconciliationError("starter_usage row is not an actual starter")
        side = _materialized_side(row, current.identity)
        current.starters[side].add(
            _nonblank(row.get("retrosheet_player_id"), "starter_usage.player")
        )

    for family, rows in (
        ("player_game_batting", player_game_batting_rows),
        ("player_game_pitching", player_game_pitching_rows),
        ("player_game_fielding", player_game_fielding_rows),
    ):
        for row in rows:
            current = accumulator(row, family)
            if row.get("is_point_estimate") is not True:
                continue
            player_id = _nonblank(row.get("retrosheet_player_id"), f"{family}.player")
            current.players.add(player_id)
            if family == "player_game_batting":
                current.batting.add(player_id)
                if row.get("actual_starting_lineup") is True:
                    current.lineups[_materialized_side(row, current.identity)].add(
                        player_id
                    )
            elif family == "player_game_pitching":
                current.pitching.add(player_id)

    result: list[RetrosheetGameEvidence] = []
    for raw_game_id, current in sorted(games.items()):
        if current.final_play_number < 0:
            raise Fd1ReconciliationError(
                f"{raw_game_id}: no play-transition final score evidence"
            )
        for side in ("home", "away"):
            if len(current.starters[side]) > 1:
                raise Fd1ReconciliationError(
                    f"{raw_game_id}: multiple actual {side} starters"
                )
        identity = current.identity
        result.append(
            RetrosheetGameEvidence(
                raw_game_id=raw_game_id,
                source_game_id=identity["source_game_id"],
                game_date=identity["game_date"],
                home_team_raw_id=identity["home_team_raw_id"],
                away_team_raw_id=identity["away_team_raw_id"],
                game_number=identity["game_number"],
                source_game_number=identity["source_game_number"],
                doubleheader_id=identity["doubleheader_id"],
                park_raw_id=identity["park_raw_id"],
                home_score=current.home_score,
                away_score=current.away_score,
                home_starter_raw_id=_only_or_none(current.starters["home"]),
                away_starter_raw_id=_only_or_none(current.starters["away"]),
                home_lineup_raw_ids=(
                    frozenset(current.lineups["home"]) if current.batting else None
                ),
                away_lineup_raw_ids=(
                    frozenset(current.lineups["away"]) if current.batting else None
                ),
                batting_participant_raw_ids=(
                    frozenset(current.batting) if current.batting else None
                ),
                pitching_participant_raw_ids=(
                    frozenset(current.pitching) if current.pitching else None
                ),
                player_participant_raw_ids=(
                    frozenset(current.players) if current.players else None
                ),
                game_type=identity["game_type"],
                score_evidence="FINAL_MATERIALIZED_PLAY_TRANSITION_STATE",
            )
        )
    return tuple(result)


def _materialized_date(value: object, family: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise Fd1ReconciliationError(f"{family}: invalid game_date {value!r}") from exc


def _materialized_source_game_number(value: object, family: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise Fd1ReconciliationError(
            f"{family}: invalid Retrosheet source game_number {value!r}"
        ) from exc
    if number < RETROSHEET_SINGLE_GAME_SOURCE_NUMBER:
        raise Fd1ReconciliationError(
            f"{family}: Retrosheet source game_number cannot be negative"
        )
    return number


def _canonical_retrosheet_game_number(source_game_number: int) -> int:
    """Normalize only Retrosheet's explicit zero-valued single-game convention."""

    return (
        CANONICAL_SINGLE_GAME_NUMBER
        if source_game_number == RETROSHEET_SINGLE_GAME_SOURCE_NUMBER
        else source_game_number
    )


def _materialized_side(row: Mapping[str, Any], identity: Mapping[str, Any]) -> str:
    team_id = _nonblank(row.get("team_id"), "materialized team_id")
    if team_id == identity["home_team_raw_id"]:
        return "home"
    if team_id == identity["away_team_raw_id"]:
        return "away"
    raise Fd1ReconciliationError(
        f"{row.get('retrosheet_game_id')}: participant team {team_id!r} is not a game side"
    )


def _only_or_none(values: set[str]) -> str | None:
    return next(iter(values)) if values else None


def load_asp_2025_live_evidence(session: Session) -> tuple[AspGameEvidence, ...]:
    """Read canonical 2025 LIVE finals and comparable evidence; perform no writes."""

    outcomes = list(
        session.scalars(
            select(GameFinalOutcome)
            .where(
                GameFinalOutcome.source_mode == LIVE_SOURCE_MODE,
                GameFinalOutcome.official_date >= date(TARGET_SEASON, 1, 1),
                GameFinalOutcome.official_date <= date(TARGET_SEASON, 12, 31),
                GameFinalOutcome.outcome_status == FINAL_OUTCOME_STATUS,
                GameFinalOutcome.official.is_(True),
            )
            .order_by(
                GameFinalOutcome.game_id,
                GameFinalOutcome.revision.desc(),
                GameFinalOutcome.retrieved_at.desc(),
            )
        )
    )
    latest = _latest_outcomes(outcomes)
    game_ids = [outcome.game_id for outcome in latest]
    if not game_ids:
        return ()

    games = {
        row.id: row
        for row in session.scalars(select(Game).where(Game.id.in_(game_ids)))
    }
    side_rows: dict[Any, list[tuple[GameTeam, Team]]] = defaultdict(list)
    for side, team in session.execute(
        select(GameTeam, Team)
        .join(Team, Team.id == GameTeam.team_id)
        .where(GameTeam.game_id.in_(game_ids))
    ):
        side_rows[side.game_id].append((side, team))
    external = _external_id_index(session, game_ids, side_rows, latest)
    game_numbers = _game_state_index(session, game_ids)
    lineups = _lineup_index(session, game_ids)
    starters = _starter_index(session, game_ids)
    participation = _participation_index(session, game_ids)

    result: list[AspGameEvidence] = []
    for outcome in latest:
        game = games.get(outcome.game_id)
        if game is None:
            raise Fd1ReconciliationError(
                f"{outcome.game_id}: canonical Game is missing"
            )
        status = (
            game.status.value if isinstance(game.status, Enum) else str(game.status)
        )
        if (
            game.source_mode != LIVE_SOURCE_MODE
            or status.lower() != GameStatus.FINAL.value
        ):
            raise Fd1ReconciliationError(
                f"{outcome.game_id}: canonical Game is not a LIVE final"
            )
        sides = side_rows.get(outcome.game_id, [])
        home = [row for row in sides if row[0].is_home]
        away = [row for row in sides if not row[0].is_home]
        if len(home) != 1 or len(away) != 1 or len(sides) != 2:
            raise Fd1ReconciliationError(
                f"{outcome.game_id}: expected exactly one canonical home and away GameTeam"
            )
        home_side, home_team = home[0]
        away_side, away_team = away[0]
        _validate_canonical_outcome(game, outcome, home_side, away_side)
        if home_team.id != outcome.home_team_id or away_team.id != outcome.away_team_id:
            raise Fd1ReconciliationError(
                f"{outcome.game_id}: GameTeam identities conflict with final outcome"
            )
        state = game_numbers.get(outcome.game_id)
        home_starter = starters.get((outcome.game_id, home_team.id))
        away_starter = starters.get((outcome.game_id, away_team.id))
        if home_starter is None and home_side.pitcher_confirmed:
            home_starter = home_side.probable_pitcher_id
        if away_starter is None and away_side.pitcher_confirmed:
            away_starter = away_side.probable_pitcher_id
        participant = participation.get(outcome.game_id, {})
        venue_id = outcome.venue_id or game.venue_id
        result.append(
            AspGameEvidence(
                game_id=str(game.id),
                official_date=outcome.official_date,
                scheduled_at=_aware(outcome.scheduled_at),
                home_team_id=str(home_team.id),
                away_team_id=str(away_team.id),
                home_score=outcome.home_score,
                away_score=outcome.away_score,
                source_record_id=outcome.source_record_id,
                source_version_id=str(outcome.source_version_id),
                game_number=state.game_number if state is not None else None,
                doubleheader_id=(
                    _optional_token(state.doubleheader_status)
                    if state is not None
                    else None
                ),
                venue_id=str(venue_id) if venue_id is not None else None,
                home_starter_id=(
                    str(home_starter) if home_starter is not None else None
                ),
                away_starter_id=(
                    str(away_starter) if away_starter is not None else None
                ),
                home_lineup_ids=lineups.get((outcome.game_id, home_team.id)),
                away_lineup_ids=lineups.get((outcome.game_id, away_team.id)),
                batting_participant_ids=participant.get("batting"),
                pitching_participant_ids=participant.get("pitching"),
                player_participant_ids=participant.get("all"),
                team_external_ids=MappingProxyType(
                    {
                        str(home_team.id): external.get(("team", home_team.id), ()),
                        str(away_team.id): external.get(("team", away_team.id), ()),
                    }
                ),
                venue_external_ids=(
                    external.get(("venue", venue_id), ())
                    if venue_id is not None
                    else ()
                ),
            )
        )
    return tuple(
        sorted(
            result, key=lambda row: (row.official_date, row.scheduled_at, row.game_id)
        )
    )


def _latest_outcomes(outcomes: Sequence[GameFinalOutcome]) -> list[GameFinalOutcome]:
    grouped: dict[Any, list[GameFinalOutcome]] = defaultdict(list)
    for outcome in outcomes:
        grouped[outcome.game_id].append(outcome)
    result: list[GameFinalOutcome] = []
    for game_id, rows in grouped.items():
        highest = max(row.revision for row in rows)
        candidates = [row for row in rows if row.revision == highest]
        if len(candidates) != 1:
            raise Fd1ReconciliationError(
                f"{game_id}: ambiguous latest official final revision {highest}"
            )
        result.append(candidates[0])
    return result


def _validate_canonical_outcome(
    game: Game,
    outcome: GameFinalOutcome,
    home: GameTeam,
    away: GameTeam,
) -> None:
    checks = {
        "Game.home_score": game.home_score,
        "Game.away_score": game.away_score,
        "GameTeam.home.score": home.score,
        "GameTeam.away.score": away.score,
    }
    expected = {
        "Game.home_score": outcome.home_score,
        "Game.away_score": outcome.away_score,
        "GameTeam.home.score": outcome.home_score,
        "GameTeam.away.score": outcome.away_score,
    }
    conflicts = {
        field: {"canonical": value, "outcome": expected[field]}
        for field, value in checks.items()
        if value is not None and value != expected[field]
    }
    if conflicts:
        raise Fd1ReconciliationError(
            f"{outcome.game_id}: internal ASP final-score conflict {conflicts}"
        )
    if outcome.venue_id is not None and game.venue_id is not None:
        if outcome.venue_id != game.venue_id:
            raise Fd1ReconciliationError(
                f"{outcome.game_id}: Game venue conflicts with final outcome venue"
            )


def _external_id_index(
    session: Session,
    game_ids: Sequence[Any],
    sides: Mapping[Any, Sequence[tuple[GameTeam, Team]]],
    outcomes: Sequence[GameFinalOutcome],
) -> Mapping[tuple[str, Any], tuple[str, ...]]:
    entity_ids = set(game_ids)
    entity_ids.update(side.team_id for rows in sides.values() for side, _ in rows)
    entity_ids.update(
        outcome.venue_id for outcome in outcomes if outcome.venue_id is not None
    )
    rows = session.scalars(
        select(ExternalIdMapping).where(
            ExternalIdMapping.entity_id.in_(entity_ids),
            ExternalIdMapping.source == MLB_STATS_API_SOURCE,
        )
    )
    index: dict[tuple[str, Any], list[str]] = defaultdict(list)
    for row in rows:
        index[(row.entity_type, row.entity_id)].append(row.external_id)
    return MappingProxyType(
        {key: tuple(sorted(values)) for key, values in index.items()}
    )


def _game_state_index(
    session: Session, game_ids: Sequence[Any]
) -> Mapping[Any, GameStateObservation]:
    rows = list(
        session.scalars(
            select(GameStateObservation).where(
                GameStateObservation.game_id.in_(game_ids),
                GameStateObservation.source_mode == LIVE_SOURCE_MODE,
            )
        )
    )
    grouped: dict[Any, list[GameStateObservation]] = defaultdict(list)
    for row in rows:
        grouped[row.game_id].append(row)
    result: dict[Any, GameStateObservation] = {}
    for game_id, candidates in grouped.items():
        latest_at = max(_aware(row.available_as_of) for row in candidates)
        latest = [row for row in candidates if _aware(row.available_as_of) == latest_at]
        signatures = {(row.game_number, row.doubleheader_status) for row in latest}
        if len(signatures) != 1:
            raise Fd1ReconciliationError(
                f"{game_id}: ambiguous latest GameState game-number evidence"
            )
        result[game_id] = sorted(latest, key=lambda row: str(row.id))[0]
    return MappingProxyType(result)


def _lineup_index(
    session: Session, game_ids: Sequence[Any]
) -> Mapping[tuple[Any, Any], frozenset[str]]:
    observations = list(
        session.scalars(
            select(LineupObservation).where(
                LineupObservation.game_id.in_(game_ids),
                LineupObservation.source_mode == LIVE_SOURCE_MODE,
                LineupObservation.lineup_status == "CONFIRMED",
                LineupObservation.is_complete.is_(True),
            )
        )
    )
    latest = _latest_observation_by_game_team(observations, "lineup")
    if not latest:
        return MappingProxyType({})
    entries: dict[Any, set[str]] = defaultdict(set)
    for row in session.scalars(
        select(LineupEntry).where(
            LineupEntry.lineup_observation_id.in_([item.id for item in latest.values()])
        )
    ):
        if row.is_starter:
            entries[row.lineup_observation_id].add(str(row.player_id))
    result: dict[tuple[Any, Any], frozenset[str]] = {}
    for key, observation in latest.items():
        player_ids = frozenset(entries.get(observation.id, set()))
        if not player_ids:
            raise Fd1ReconciliationError(
                f"{observation.game_id}: confirmed complete lineup has no starters"
            )
        result[key] = player_ids
    return MappingProxyType(result)


def _starter_index(
    session: Session, game_ids: Sequence[Any]
) -> Mapping[tuple[Any, Any], Any]:
    observations = list(
        session.scalars(
            select(StarterObservation).where(
                StarterObservation.game_id.in_(game_ids),
                StarterObservation.source_mode == LIVE_SOURCE_MODE,
                StarterObservation.confirmation_state == "CONFIRMED",
                StarterObservation.confirmed_pitcher_id.is_not(None),
            )
        )
    )
    latest = _latest_observation_by_game_team(observations, "starter")
    return MappingProxyType(
        {key: observation.confirmed_pitcher_id for key, observation in latest.items()}
    )


def _latest_observation_by_game_team(
    observations: Sequence[Any], family: str
) -> Mapping[tuple[Any, Any], Any]:
    grouped: dict[tuple[Any, Any], list[Any]] = defaultdict(list)
    for row in observations:
        grouped[(row.game_id, row.team_id)].append(row)
    result: dict[tuple[Any, Any], Any] = {}
    for key, rows in grouped.items():
        latest_at = max(_aware(row.available_as_of) for row in rows)
        latest = [row for row in rows if _aware(row.available_as_of) == latest_at]
        if len(latest) != 1:
            raise Fd1ReconciliationError(
                f"{key[0]}: ambiguous latest {family} evidence for team {key[1]}"
            )
        result[key] = latest[0]
    return MappingProxyType(result)


def _participation_index(
    session: Session, game_ids: Sequence[Any]
) -> Mapping[Any, Mapping[str, frozenset[str]]]:
    rows = list(
        session.scalars(
            select(PlayerGameStat).where(PlayerGameStat.game_id.in_(game_ids))
        )
    )
    grouped: dict[Any, dict[str, set[str]]] = defaultdict(
        lambda: {"batting": set(), "pitching": set(), "all": set()}
    )
    for row in rows:
        family = _declared_player_stat_family(row.values)
        player_id = str(row.player_id)
        grouped[row.game_id]["all"].add(player_id)
        if family in {"batting", "pitching"}:
            grouped[row.game_id][family].add(player_id)
    result: dict[Any, Mapping[str, frozenset[str]]] = {}
    for game_id, families in grouped.items():
        result[game_id] = MappingProxyType(
            {family: frozenset(values) for family, values in families.items() if values}
        )
    return MappingProxyType(result)


def _declared_player_stat_family(values: Mapping[str, Any]) -> str | None:
    """Recognize only explicit family metadata; never infer role from metric names."""

    token = str(values.get("stat_family", "")).strip().lower()
    return token if token in {"batting", "pitching", "fielding"} else None


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
