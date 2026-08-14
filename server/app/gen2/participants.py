"""Point-in-time roster, lineup, and starter contracts for Gen-2 research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Iterable


PARTICIPATION_CONTRACT_VERSION = "mlb_gen2_participation_snapshot_v2"


class ParticipationError(ValueError):
    """Raised when participant evidence is ambiguous or temporally invalid."""


class ParticipationPopulation(StrEnum):
    STRICT_PREGAME = "STRICT_PREGAME"
    ACTUAL_LINEUP_TIMING_RISK = "ACTUAL_LINEUP_TIMING_RISK"


class RosterStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INJURED = "INJURED"
    SUSPENDED = "SUSPENDED"
    OPTIONED = "OPTIONED"
    DESIGNATED = "DESIGNATED"
    UNKNOWN = "UNKNOWN"


class LineupEvidenceKind(StrEnum):
    PROJECTED = "PROJECTED"
    CONFIRMED = "CONFIRMED"
    HISTORICAL_ACTUAL = "HISTORICAL_ACTUAL"


class StarterEvidenceKind(StrEnum):
    PROBABLE = "PROBABLE"
    CONFIRMED = "CONFIRMED"
    HISTORICAL_ACTUAL = "HISTORICAL_ACTUAL"


class ParticipantTemporalSemantics(StrEnum):
    POINT_IN_TIME_LIVE = "POINT_IN_TIME_LIVE"
    RETROSPECTIVE_ACTUAL_TIMING_RISK = "RETROSPECTIVE_ACTUAL_TIMING_RISK"


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ParticipationError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _identity(value: str, field: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ParticipationError(f"{field} is required")
    return stripped


def _sha(value: str, field: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise ParticipationError(f"{field} must be SHA-256")
    return value.lower()


def _checksum(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return sha256(payload).hexdigest()


@dataclass(frozen=True)
class ParticipationTarget:
    game_id: str
    team_id: str
    official_date: date
    first_pitch_at: datetime
    cutoff_at: datetime

    def __post_init__(self) -> None:
        _identity(self.game_id, "game_id")
        _identity(self.team_id, "team_id")
        first_pitch = _utc(self.first_pitch_at, "first_pitch_at")
        cutoff = _utc(self.cutoff_at, "cutoff_at")
        if cutoff >= first_pitch:
            raise ParticipationError("participation cutoff must precede first pitch")


@dataclass(frozen=True)
class RosterObservation:
    observation_id: str
    player_id: str
    team_id: str
    status: RosterStatus
    defensive_positions: tuple[str, ...]
    observed_at: datetime
    eligible_at: datetime
    source_version_id: str
    source_record_id: str
    evidence_checksum: str
    game_id: str | None = None
    official_date: date | None = None
    temporal_semantics: ParticipantTemporalSemantics = (
        ParticipantTemporalSemantics.POINT_IN_TIME_LIVE
    )

    def __post_init__(self) -> None:
        for field, value in (
            ("observation_id", self.observation_id),
            ("player_id", self.player_id),
            ("team_id", self.team_id),
            ("source_version_id", self.source_version_id),
            ("source_record_id", self.source_record_id),
        ):
            _identity(value, field)
        observed = _utc(self.observed_at, "observed_at")
        eligible = _utc(self.eligible_at, "eligible_at")
        if eligible < observed:
            raise ParticipationError("roster eligible_at cannot precede observed_at")
        if not isinstance(self.status, RosterStatus):
            raise ParticipationError("roster status is invalid")
        if not isinstance(self.temporal_semantics, ParticipantTemporalSemantics):
            raise ParticipationError("roster temporal semantics are invalid")
        historical = (
            self.temporal_semantics
            is ParticipantTemporalSemantics.RETROSPECTIVE_ACTUAL_TIMING_RISK
        )
        if historical and (not self.game_id or self.official_date is None):
            raise ParticipationError(
                "retrospective roster evidence requires exact game and official date"
            )
        if not historical and (self.game_id is not None or self.official_date is not None):
            raise ParticipationError("live roster evidence cannot claim retrospective game scope")
        positions = tuple(position.strip().upper() for position in self.defensive_positions)
        if any(not position for position in positions) or len(set(positions)) != len(positions):
            raise ParticipationError("defensive positions must be unique nonempty codes")
        object.__setattr__(self, "defensive_positions", positions)
        _sha(self.evidence_checksum, "evidence_checksum")


@dataclass(frozen=True)
class LineupObservation:
    observation_id: str
    game_id: str
    team_id: str
    player_id: str
    batting_order: int
    defensive_position: str
    kind: LineupEvidenceKind
    observed_at: datetime
    eligible_at: datetime
    source_version_id: str
    source_record_id: str
    evidence_checksum: str
    replaces_player_id: str | None = None
    scratched_player_id: str | None = None
    temporal_semantics: ParticipantTemporalSemantics = (
        ParticipantTemporalSemantics.POINT_IN_TIME_LIVE
    )

    def __post_init__(self) -> None:
        for field, value in (
            ("observation_id", self.observation_id),
            ("game_id", self.game_id),
            ("team_id", self.team_id),
            ("player_id", self.player_id),
            ("source_version_id", self.source_version_id),
            ("source_record_id", self.source_record_id),
        ):
            _identity(value, field)
        if self.batting_order not in range(1, 10):
            raise ParticipationError("batting_order must be in 1..9")
        position = _identity(self.defensive_position, "defensive_position").upper()
        object.__setattr__(self, "defensive_position", position)
        observed = _utc(self.observed_at, "observed_at")
        eligible = _utc(self.eligible_at, "eligible_at")
        historical = self.kind is LineupEvidenceKind.HISTORICAL_ACTUAL
        if not isinstance(self.kind, LineupEvidenceKind) or not isinstance(
            self.temporal_semantics, ParticipantTemporalSemantics
        ):
            raise ParticipationError("lineup kind or temporal semantics is invalid")
        if historical != (
            self.temporal_semantics
            is ParticipantTemporalSemantics.RETROSPECTIVE_ACTUAL_TIMING_RISK
        ):
            raise ParticipationError("lineup kind and temporal semantics disagree")
        if eligible < observed:
            raise ParticipationError("lineup eligible_at cannot precede observed_at")
        if self.replaces_player_id is not None:
            _identity(self.replaces_player_id, "replaces_player_id")
            if self.replaces_player_id == self.player_id:
                raise ParticipationError("a replacement cannot replace itself")
        if self.scratched_player_id is not None:
            _identity(self.scratched_player_id, "scratched_player_id")
            if self.scratched_player_id == self.player_id:
                raise ParticipationError("a lineup player cannot scratch itself")
        _sha(self.evidence_checksum, "evidence_checksum")


@dataclass(frozen=True)
class StarterObservation:
    observation_id: str
    game_id: str
    team_id: str
    player_id: str
    kind: StarterEvidenceKind
    observed_at: datetime
    eligible_at: datetime
    source_version_id: str
    source_record_id: str
    evidence_checksum: str
    temporal_semantics: ParticipantTemporalSemantics = (
        ParticipantTemporalSemantics.POINT_IN_TIME_LIVE
    )

    def __post_init__(self) -> None:
        for field, value in (
            ("observation_id", self.observation_id),
            ("game_id", self.game_id),
            ("team_id", self.team_id),
            ("player_id", self.player_id),
            ("source_version_id", self.source_version_id),
            ("source_record_id", self.source_record_id),
        ):
            _identity(value, field)
        observed = _utc(self.observed_at, "observed_at")
        eligible = _utc(self.eligible_at, "eligible_at")
        historical = self.kind is StarterEvidenceKind.HISTORICAL_ACTUAL
        if not isinstance(self.kind, StarterEvidenceKind) or not isinstance(
            self.temporal_semantics, ParticipantTemporalSemantics
        ):
            raise ParticipationError("starter kind or temporal semantics is invalid")
        if historical != (
            self.temporal_semantics
            is ParticipantTemporalSemantics.RETROSPECTIVE_ACTUAL_TIMING_RISK
        ):
            raise ParticipationError("starter kind and temporal semantics disagree")
        if eligible < observed:
            raise ParticipationError("starter eligible_at cannot precede observed_at")
        _sha(self.evidence_checksum, "evidence_checksum")


@dataclass(frozen=True)
class LineupEntry:
    player_id: str
    batting_order: int
    defensive_position: str
    roster_status: RosterStatus
    evidence_kind: LineupEvidenceKind
    observation_id: str


@dataclass(frozen=True)
class ParticipationSnapshot:
    version: str
    game_id: str
    team_id: str
    population: ParticipationPopulation
    cutoff_at: datetime
    lineup: tuple[LineupEntry, ...]
    starter_id: str | None
    starter_kind: StarterEvidenceKind | None
    active_roster_player_ids: tuple[str, ...]
    scratch_player_ids: tuple[str, ...]
    replacement_player_ids: tuple[str, ...]
    evidence_observation_ids: tuple[str, ...]
    evidence_checksums: tuple[str, ...]
    complete_lineup: bool
    starter_available: bool
    training_eligible: bool
    research_comparison_eligible: bool
    evidence_tier: str
    temporal_risk: str | None
    checksum: str


def _eligible_for_population(
    *,
    observed_at: datetime,
    eligible_at: datetime,
    historical_actual: bool,
    target: ParticipationTarget,
    population: ParticipationPopulation,
) -> bool:
    observed = _utc(observed_at, "observed_at")
    eligible = _utc(eligible_at, "eligible_at")
    if population is ParticipationPopulation.STRICT_PREGAME:
        return observed <= target.cutoff_at and eligible <= target.cutoff_at and not historical_actual
    if historical_actual:
        return True
    return observed <= target.cutoff_at and eligible <= target.cutoff_at


def _latest_unique(items, semantic_key):
    grouped = {}
    for item in items:
        key = semantic_key(item)
        order = (_utc(item.observed_at, "observed_at"), _utc(item.eligible_at, "eligible_at"))
        prior = grouped.get(key)
        if prior is None or order > prior[0]:
            grouped[key] = (order, item)
        elif order == prior[0] and item != prior[1]:
            raise ParticipationError(f"ambiguous simultaneous participant evidence for {key}")
    return tuple(value[1] for value in grouped.values())


def build_participation_snapshot(
    target: ParticipationTarget,
    *,
    roster_observations: Iterable[RosterObservation],
    lineup_observations: Iterable[LineupObservation],
    starter_observations: Iterable[StarterObservation],
    population: ParticipationPopulation,
) -> ParticipationSnapshot:
    """Build one team/game snapshot without crossing the declared population boundary."""

    roster_candidates = tuple(
        row
        for row in roster_observations
        if row.team_id == target.team_id
        and (
            row.temporal_semantics
            is not ParticipantTemporalSemantics.RETROSPECTIVE_ACTUAL_TIMING_RISK
            or (row.game_id == target.game_id and row.official_date == target.official_date)
        )
        and _eligible_for_population(
            observed_at=row.observed_at,
            eligible_at=row.eligible_at,
            historical_actual=(
                row.temporal_semantics
                is ParticipantTemporalSemantics.RETROSPECTIVE_ACTUAL_TIMING_RISK
            ),
            target=target,
            population=population,
        )
    )
    roster_latest = _latest_unique(roster_candidates, lambda row: row.player_id)
    roster_by_player = {row.player_id: row for row in roster_latest}
    active = tuple(
        sorted(player_id for player_id, row in roster_by_player.items() if row.status is RosterStatus.ACTIVE)
    )

    lineup_candidates = tuple(
        row
        for row in lineup_observations
        if row.game_id == target.game_id
        and row.team_id == target.team_id
        and _eligible_for_population(
            observed_at=row.observed_at,
            eligible_at=row.eligible_at,
            historical_actual=row.kind is LineupEvidenceKind.HISTORICAL_ACTUAL,
            target=target,
            population=population,
        )
    )
    lineup_latest = sorted(
        _latest_unique(lineup_candidates, lambda row: row.batting_order),
        key=lambda row: row.batting_order,
    )
    player_ids = [row.player_id for row in lineup_latest]
    if len(player_ids) != len(set(player_ids)):
        raise ParticipationError("one player occupies multiple batting-order slots")
    for row in lineup_latest:
        roster = roster_by_player.get(row.player_id)
        if roster is None:
            raise ParticipationError(f"lineup player {row.player_id} lacks governed team roster evidence")
        if roster.status is not RosterStatus.ACTIVE:
            raise ParticipationError(f"lineup player {row.player_id} is not active")
        if roster.defensive_positions and row.defensive_position.upper() not in roster.defensive_positions:
            raise ParticipationError(
                f"lineup position {row.defensive_position} conflicts with roster evidence for {row.player_id}"
            )
    for row in lineup_latest:
        if row.replaces_player_id is not None:
            prior = [
                candidate
                for candidate in lineup_candidates
                if candidate.batting_order == row.batting_order
                and candidate.player_id == row.replaces_player_id
                and candidate.observed_at < row.observed_at
            ]
            if not prior:
                raise ParticipationError("replacement does not resolve an earlier lineup player")

    starter_candidates = tuple(
        row
        for row in starter_observations
        if row.game_id == target.game_id
        and row.team_id == target.team_id
        and _eligible_for_population(
            observed_at=row.observed_at,
            eligible_at=row.eligible_at,
            historical_actual=row.kind is StarterEvidenceKind.HISTORICAL_ACTUAL,
            target=target,
            population=population,
        )
    )
    latest_starters = _latest_unique(starter_candidates, lambda row: "starter")
    starter = latest_starters[0] if latest_starters else None
    if starter is not None:
        roster = roster_by_player.get(starter.player_id)
        if roster is None or roster.status is not RosterStatus.ACTIVE:
            raise ParticipationError("starter lacks active governed team roster evidence")

    historical_actual_used = any(
        row.kind is LineupEvidenceKind.HISTORICAL_ACTUAL for row in lineup_latest
    ) or (starter is not None and starter.kind is StarterEvidenceKind.HISTORICAL_ACTUAL)
    if population is ParticipationPopulation.STRICT_PREGAME and historical_actual_used:
        raise ParticipationError("historical actual participation entered strict pregame state")
    evidence_rows = tuple(roster_latest) + tuple(lineup_latest) + ((starter,) if starter else ())
    paired_evidence = tuple(
        sorted(
            ((_participant_row_payload(row)) for row in evidence_rows),
            key=lambda value: (str(value["row_type"]), str(value["observation_id"])),
        )
    )
    observation_ids = tuple(str(row["observation_id"]) for row in paired_evidence)
    evidence_checksums = tuple(str(row["evidence_checksum"]) for row in paired_evidence)
    complete = len(lineup_latest) == 9 and [row.batting_order for row in lineup_latest] == list(range(1, 10))
    training_eligible = (
        population is ParticipationPopulation.STRICT_PREGAME
        and complete
        and starter is not None
        and not historical_actual_used
    )
    research_comparison_eligible = complete and starter is not None
    lineup_entries = tuple(
        LineupEntry(
            player_id=row.player_id,
            batting_order=row.batting_order,
            defensive_position=row.defensive_position.upper(),
            roster_status=roster_by_player[row.player_id].status,
            evidence_kind=row.kind,
            observation_id=row.observation_id,
        )
        for row in lineup_latest
    )
    scratch_player_ids = tuple(
        sorted(
            {
                player
                for row in lineup_latest
                for player in (row.scratched_player_id,)
                if player is not None
            }
        )
    )
    replacement_player_ids = tuple(
        sorted(row.player_id for row in lineup_latest if row.replaces_player_id is not None)
    )
    evidence_tier = "A" if population is ParticipationPopulation.STRICT_PREGAME else "B"
    temporal_risk = (
        None
        if population is ParticipationPopulation.STRICT_PREGAME
        else "HISTORICAL_ACTUAL_LINEUP_TIMING_RISK"
    )
    payload = {
        "version": PARTICIPATION_CONTRACT_VERSION,
        "game_id": target.game_id,
        "team_id": target.team_id,
        "official_date": target.official_date.isoformat(),
        "first_pitch_at": _utc(target.first_pitch_at, "first_pitch_at").isoformat(),
        "population": population.value,
        "cutoff_at": _utc(target.cutoff_at, "cutoff_at").isoformat(),
        "lineup": [
            {
                "player_id": row.player_id,
                "batting_order": row.batting_order,
                "defensive_position": row.defensive_position,
                "roster_status": row.roster_status.value,
                "evidence_kind": row.evidence_kind.value,
                "observation_id": row.observation_id,
            }
            for row in lineup_entries
        ],
        "starter": [starter.player_id, starter.kind.value] if starter else None,
        "active_roster_player_ids": active,
        "scratch_player_ids": scratch_player_ids,
        "replacement_player_ids": replacement_player_ids,
        "evidence_rows": paired_evidence,
        "complete_lineup": complete,
        "starter_available": starter is not None,
        "training_eligible": training_eligible,
        "research_comparison_eligible": research_comparison_eligible,
        "evidence_tier": evidence_tier,
        "temporal_risk": temporal_risk,
    }
    return ParticipationSnapshot(
        version=PARTICIPATION_CONTRACT_VERSION,
        game_id=target.game_id,
        team_id=target.team_id,
        population=population,
        cutoff_at=target.cutoff_at,
        lineup=lineup_entries,
        starter_id=starter.player_id if starter else None,
        starter_kind=starter.kind if starter else None,
        active_roster_player_ids=active,
        scratch_player_ids=scratch_player_ids,
        replacement_player_ids=replacement_player_ids,
        evidence_observation_ids=observation_ids,
        evidence_checksums=evidence_checksums,
        complete_lineup=complete,
        starter_available=starter is not None,
        training_eligible=training_eligible,
        research_comparison_eligible=research_comparison_eligible,
        evidence_tier=evidence_tier,
        temporal_risk=temporal_risk,
        checksum=_checksum(payload),
    )


def _participant_row_payload(
    row: RosterObservation | LineupObservation | StarterObservation,
) -> dict[str, object]:
    base: dict[str, object] = {
        "row_type": type(row).__name__,
        "observation_id": row.observation_id,
        "player_id": row.player_id,
        "team_id": row.team_id,
        "observed_at": _utc(row.observed_at, "observed_at").isoformat(),
        "eligible_at": _utc(row.eligible_at, "eligible_at").isoformat(),
        "source_version_id": row.source_version_id,
        "source_record_id": row.source_record_id,
        "evidence_checksum": row.evidence_checksum,
        "temporal_semantics": row.temporal_semantics.value,
    }
    if isinstance(row, RosterObservation):
        base.update(
            {
                "game_id": row.game_id,
                "official_date": row.official_date.isoformat() if row.official_date else None,
                "status": row.status.value,
                "defensive_positions": row.defensive_positions,
            }
        )
    elif isinstance(row, LineupObservation):
        base.update(
            {
                "game_id": row.game_id,
                "batting_order": row.batting_order,
                "defensive_position": row.defensive_position,
                "kind": row.kind.value,
                "replaces_player_id": row.replaces_player_id,
                "scratched_player_id": row.scratched_player_id,
            }
        )
    else:
        base.update({"game_id": row.game_id, "kind": row.kind.value})
    return base
