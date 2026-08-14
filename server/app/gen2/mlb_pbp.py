"""Provider-free canonical MLB play-by-play extraction for Gate 3C.

Base occupancy, outs, and score transitions are reconstructed exclusively from
MLB ``liveData.plays.allPlays`` runner movements and then reconciled to MLB's
official play totals.  Statcast is never consulted by this module.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from typing import Any

from app.gen2.event_taxonomy import (
    MLB_OFFICIAL_EARLY_PA_TERMINATION_TOKENS,
    PA_OUTCOME_TAXONOMY_IDENTITY,
    PA_OUTCOME_TAXONOMY_VERSION,
    TRUNCATED_PA_EXCLUSION_REASON,
    PlateAppearanceOutcome,
    TaxonomyValidationError,
    TruncatedPlateAppearanceEvidence,
    classify_terminal_result,
    normalize_source_token,
    validate_official_early_termination,
)


PITCH_EVENT_SCHEMA_VERSION = "mlb_gen2_pitch_event_v2"
PLATE_APPEARANCE_SCHEMA_VERSION = "mlb_gen2_plate_appearance_v2"
MLB_PBP_GAME_SCHEMA_VERSION = "mlb_gen2_mlb_pbp_game_v2"
HALF_INNING_RUNNER_PLACEMENT_VERSION = (
    "mlb_gen2_half_inning_runner_placement_v1"
)
PINCH_RUNNER_SUBSTITUTION_VERSION = "mlb_gen2_pinch_runner_substitution_v1"
NULL_RUNNER_PLACEHOLDER_VERSION = "mlb_gen2_null_runner_placeholder_v1"
DROPPED_THIRD_STRIKE_VERSION = "mlb_gen2_dropped_third_strike_v1"

_BASE_ORDER = ("1B", "2B", "3B")
_BASE_BITS = {"1B": 1, "2B": 2, "3B": 4}

FORBIDDEN_PREGAME_FEATURE_FIELDS = frozenset({
    "away_score",
    "away_score_after",
    "away_score_before",
    "bat_score",
    "bat_score_diff",
    "delta_home_win_exp",
    "delta_pitcher_run_exp",
    "delta_run_exp",
    "fld_score",
    "home_score",
    "home_score_after",
    "home_score_before",
    "home_score_diff",
    "home_win_exp",
    "post_away_score",
    "post_bat_score",
    "post_fld_score",
    "post_home_score",
    "score_after",
    "score_before",
})
_FORBIDDEN_PREGAME_FIELD_TOKENS = (
    "score",
    "win_exp",
    "win_expectancy",
    "run_exp",
    "run_expectancy",
)


class CanonicalEventError(ValueError):
    """MLB evidence is incomplete, ambiguous, or internally conflicting."""


class CountEventKind(str, Enum):
    PITCH = "PITCH"
    AUTOMATIC_BALL = "AUTOMATIC_BALL"
    AUTOMATIC_STRIKE = "AUTOMATIC_STRIKE"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _checksum(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BaseRunnerIdentity:
    base: str
    source_id: str
    canonical_id: str

    def __post_init__(self) -> None:
        if self.base not in {"1B", "2B", "3B"}:
            raise CanonicalEventError(f"Unsupported occupied base: {self.base}")
        _positive_id(self.source_id, "base-runner source ID")
        _required_text(self.canonical_id, "base-runner canonical ID")

    def as_dict(self) -> dict[str, str]:
        return {
            "base": self.base,
            "source_id": self.source_id,
            "canonical_id": self.canonical_id,
        }


@dataclass(frozen=True, slots=True)
class StructuralRunnerEvidence:
    evidence_type: str
    contract_version: str
    source_event_index: int
    source_position: int
    base: str | None
    player_source_id: str
    player_id: str
    replaced_player_source_id: str | None
    replaced_player_id: str | None
    event_timestamp: str | None
    source_checksum: str
    content_checksum: str

    def as_dict(self) -> dict[str, object]:
        return {
            "evidence_type": self.evidence_type,
            "contract_version": self.contract_version,
            "source_event_index": self.source_event_index,
            "source_position": self.source_position,
            "base": self.base,
            "player_source_id": self.player_source_id,
            "player_id": self.player_id,
            "replaced_player_source_id": self.replaced_player_source_id,
            "replaced_player_id": self.replaced_player_id,
            "event_timestamp": self.event_timestamp,
            "source_checksum": self.source_checksum,
            "content_checksum": self.content_checksum,
        }


@dataclass(frozen=True, slots=True)
class BaseOutScoreState:
    outs: int
    base_mask: int
    away_score: int
    home_score: int
    runners: tuple[BaseRunnerIdentity, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.outs <= 3:
            raise CanonicalEventError("Out state must be in 0..3")
        if not 0 <= self.base_mask <= 7:
            raise CanonicalEventError("Base mask must be in 0..7")
        if self.away_score < 0 or self.home_score < 0:
            raise CanonicalEventError("Scores cannot be negative")
        ordered = tuple(sorted(self.runners, key=lambda row: _base_sort_key(row.base)))
        if ordered != self.runners:
            raise CanonicalEventError("Base-runner identities must be ordered by base")
        bases = [row.base for row in self.runners]
        if len(set(bases)) != len(bases):
            raise CanonicalEventError("A base cannot contain multiple runner identities")
        if len({row.source_id for row in self.runners}) != len(self.runners):
            raise CanonicalEventError("A source runner cannot occupy multiple bases")
        if len({row.canonical_id for row in self.runners}) != len(self.runners):
            raise CanonicalEventError("A canonical runner cannot occupy multiple bases")
        expected_mask = sum(_BASE_BITS[row.base] for row in self.runners)
        if self.base_mask != expected_mask:
            raise CanonicalEventError(
                "Base mask does not match exact base-runner identities"
            )
        if self.outs == 3 and self.runners:
            raise CanonicalEventError("An ended half-inning cannot retain base runners")

    def as_dict(self) -> dict[str, object]:
        return {
            "outs": self.outs,
            "base_mask": self.base_mask,
            "away_score": self.away_score,
            "home_score": self.home_score,
            "runners": [row.as_dict() for row in self.runners],
        }


@dataclass(frozen=True, slots=True)
class MlbCountEvent:
    """One ordered MLB physical pitch or automatic count-changing event."""

    source_version_id: str
    source_game_id: str
    at_bat_index: int
    at_bat_number: int
    source_event_index: int
    source_play_id: str | None
    source_pitch_number: int | None
    canonical_game_id: str
    canonical_plate_appearance_id: str
    canonical_sequence: int
    event_kind: CountEventKind
    is_pitch: bool
    source_description: str
    batter_source_id: str
    pitcher_source_id: str
    batter_id: str
    pitcher_id: str
    inning: int
    inning_half: str
    balls_before: int
    strikes_before: int
    balls_after: int
    strikes_after: int
    outs: int
    runner_state_before: tuple[BaseRunnerIdentity, ...]
    structural_evidence_checksums: tuple[str, ...]
    event_timestamp: str
    native_identity_checksum: str
    canonical_identity_checksum: str
    content_checksum: str

    @property
    def schema_version(self) -> str:
        return PITCH_EVENT_SCHEMA_VERSION

    @property
    def canonical_event_id(self) -> str:
        return self.canonical_identity_checksum

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_version_id": self.source_version_id,
            "source_game_id": self.source_game_id,
            "at_bat_index": self.at_bat_index,
            "at_bat_number": self.at_bat_number,
            "source_event_index": self.source_event_index,
            "source_play_id": self.source_play_id,
            "source_pitch_number": self.source_pitch_number,
            "canonical_game_id": self.canonical_game_id,
            "canonical_plate_appearance_id": self.canonical_plate_appearance_id,
            "canonical_sequence": self.canonical_sequence,
            "event_kind": self.event_kind.value,
            "is_pitch": self.is_pitch,
            "source_description": self.source_description,
            "batter_source_id": self.batter_source_id,
            "pitcher_source_id": self.pitcher_source_id,
            "batter_id": self.batter_id,
            "pitcher_id": self.pitcher_id,
            "inning": self.inning,
            "inning_half": self.inning_half,
            "balls_before": self.balls_before,
            "strikes_before": self.strikes_before,
            "balls_after": self.balls_after,
            "strikes_after": self.strikes_after,
            "outs": self.outs,
            "runner_state_before": [
                row.as_dict() for row in self.runner_state_before
            ],
            "structural_evidence_checksums": list(
                self.structural_evidence_checksums
            ),
            "event_timestamp": self.event_timestamp,
            "native_identity_checksum": self.native_identity_checksum,
            "canonical_identity_checksum": self.canonical_identity_checksum,
            "canonical_event_id": self.canonical_event_id,
            "content_checksum": self.content_checksum,
        }


@dataclass(frozen=True, slots=True)
class CanonicalPlateAppearance:
    source_version_id: str
    source_game_id: str
    at_bat_index: int
    at_bat_number: int
    canonical_game_id: str
    canonical_plate_appearance_id: str
    inning: int
    inning_half: str
    batting_team_source_id: str
    pitching_team_source_id: str
    batting_team_id: str
    pitching_team_id: str
    batter_source_id: str
    pitcher_source_id: str
    batter_id: str
    pitcher_id: str
    batter_hand: str
    pitcher_hand: str
    state_before: BaseOutScoreState
    state_after: BaseOutScoreState
    terminal_source_token: str
    outcome: PlateAppearanceOutcome | None
    rbi: int
    runs_scored: int
    batter_reached: bool
    batter_out: bool
    source_event_timestamp: str
    official_early_termination: bool
    exclusion_reason: str | None
    runner_movement_checksum: str
    structural_runner_evidence: tuple[StructuralRunnerEvidence, ...]
    count_event_checksums: tuple[str, ...]
    native_identity_checksum: str
    canonical_identity_checksum: str
    content_checksum: str

    @property
    def schema_version(self) -> str:
        return PLATE_APPEARANCE_SCHEMA_VERSION

    @property
    def canonical_pa_id(self) -> str:
        return self.canonical_plate_appearance_id

    @property
    def truncated_evidence(self) -> TruncatedPlateAppearanceEvidence | None:
        if not self.official_early_termination:
            return None
        return TruncatedPlateAppearanceEvidence(
            mlb_result_token=self.terminal_source_token,
            official_game_final=True,
            play_complete=True,
            outs_before=self.state_before.outs,
            outs_after=self.state_after.outs,
            runner_out_recorded=True,
            batter_has_terminal_result=False,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "taxonomy_version": PA_OUTCOME_TAXONOMY_VERSION,
            "taxonomy_identity": PA_OUTCOME_TAXONOMY_IDENTITY,
            "source_version_id": self.source_version_id,
            "source_game_id": self.source_game_id,
            "at_bat_index": self.at_bat_index,
            "at_bat_number": self.at_bat_number,
            "canonical_game_id": self.canonical_game_id,
            "canonical_plate_appearance_id": self.canonical_plate_appearance_id,
            "inning": self.inning,
            "inning_half": self.inning_half,
            "batting_team_source_id": self.batting_team_source_id,
            "pitching_team_source_id": self.pitching_team_source_id,
            "batting_team_id": self.batting_team_id,
            "pitching_team_id": self.pitching_team_id,
            "batter_source_id": self.batter_source_id,
            "pitcher_source_id": self.pitcher_source_id,
            "batter_id": self.batter_id,
            "pitcher_id": self.pitcher_id,
            "batter_hand": self.batter_hand,
            "pitcher_hand": self.pitcher_hand,
            "state_before": self.state_before.as_dict(),
            "state_after": self.state_after.as_dict(),
            "terminal_source_token": self.terminal_source_token,
            "outcome": self.outcome.value if self.outcome is not None else None,
            "rbi": self.rbi,
            "runs_scored": self.runs_scored,
            "batter_reached": self.batter_reached,
            "batter_out": self.batter_out,
            "source_event_timestamp": self.source_event_timestamp,
            "official_early_termination": self.official_early_termination,
            "exclusion_reason": self.exclusion_reason,
            "runner_movement_checksum": self.runner_movement_checksum,
            "structural_runner_evidence": [
                row.as_dict() for row in self.structural_runner_evidence
            ],
            "count_event_checksums": list(self.count_event_checksums),
            "native_identity_checksum": self.native_identity_checksum,
            "canonical_identity_checksum": self.canonical_identity_checksum,
            "content_checksum": self.content_checksum,
        }


@dataclass(frozen=True, slots=True)
class CanonicalMlbGame:
    source_version_id: str
    source_game_id: str
    canonical_game_id: str
    official_final: bool
    plate_appearances: tuple[CanonicalPlateAppearance, ...]
    count_events: tuple[MlbCountEvent, ...]
    native_identity_checksum: str
    canonical_identity_checksum: str
    content_checksum: str

    @property
    def schema_version(self) -> str:
        return MLB_PBP_GAME_SCHEMA_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_version_id": self.source_version_id,
            "source_game_id": self.source_game_id,
            "canonical_game_id": self.canonical_game_id,
            "official_final": self.official_final,
            "plate_appearances": [row.as_dict() for row in self.plate_appearances],
            "count_events": [row.as_dict() for row in self.count_events],
            "native_identity_checksum": self.native_identity_checksum,
            "canonical_identity_checksum": self.canonical_identity_checksum,
            "content_checksum": self.content_checksum,
        }


@dataclass(frozen=True, slots=True)
class _RunnerIdentity:
    source_id: str
    canonical_id: str


@dataclass(frozen=True, slots=True)
class _MovementResult:
    runners: Mapping[str, _RunnerIdentity]
    outs_after: int
    away_score_after: int
    home_score_after: int
    runs_scored: int
    batter_reached: bool
    batter_out: bool
    runner_out_recorded: bool
    normalized_movements: tuple[Mapping[str, object], ...]
    suppressed_placeholder_evidence: tuple[StructuralRunnerEvidence, ...]


def canonicalize_mlb_play_by_play(
    feed: Mapping[str, Any],
    *,
    source_version_id: str,
    canonical_game_id: str,
    canonical_player_ids: Mapping[str | int, str],
    canonical_team_ids: Mapping[str | int, str],
) -> CanonicalMlbGame:
    """Canonicalize one final MLB game feed without I/O or provider construction."""

    source_version = _required_text(source_version_id, "source_version_id")
    game_id = _required_text(canonical_game_id, "canonical_game_id")
    players = _exact_mapping(canonical_player_ids, "player")
    teams = _exact_mapping(canonical_team_ids, "team")
    if game_id in set(players.values()).union(teams.values()):
        raise CanonicalEventError(
            "Canonical game, player, and team identity targets must be disjoint"
        )
    overlap = sorted(set(players.values()).intersection(teams.values()))
    if overlap:
        raise CanonicalEventError(
            "Canonical player and team identity targets must be disjoint: "
            f"{overlap}"
        )
    payload = _unwrap_feed(feed)
    source_game_id = _positive_id(payload.get("gamePk"), "gamePk")
    game_data = _mapping(payload.get("gameData"), "gameData")
    status = _mapping(game_data.get("status"), "gameData.status")
    official_final = (
        str(status.get("abstractGameState") or "").strip().lower() == "final"
        and str(status.get("codedGameState") or "").strip().upper() in {"F", "O"}
    )
    if not official_final:
        raise CanonicalEventError("Canonical MLB play-by-play requires an official final")

    game_teams = _mapping(game_data.get("teams"), "gameData.teams")
    home_source_team_id = _positive_id(
        _mapping(game_teams.get("home"), "gameData.teams.home").get("id"),
        "home team ID",
    )
    away_source_team_id = _positive_id(
        _mapping(game_teams.get("away"), "gameData.teams.away").get("id"),
        "away team ID",
    )
    home_team_id = _resolve(teams, home_source_team_id, "home team")
    away_team_id = _resolve(teams, away_source_team_id, "away team")
    if home_team_id == away_team_id:
        raise CanonicalEventError("Home and away teams resolve to one canonical identity")

    live_data = _mapping(payload.get("liveData"), "liveData")
    plays_container = _mapping(live_data.get("plays"), "liveData.plays")
    raw_plays = _sequence(plays_container.get("allPlays"), "liveData.plays.allPlays")
    if not raw_plays:
        raise CanonicalEventError("Final MLB game contains no play-by-play")

    plate_appearances: list[CanonicalPlateAppearance] = []
    count_events: list[MlbCountEvent] = []
    current_half: tuple[int, str] | None = None
    runners: dict[str, _RunnerIdentity] = {}
    outs = 0
    away_score = 0
    home_score = 0
    observed_event_native_ids: set[str] = set()

    for expected_index, raw_play_value in enumerate(raw_plays):
        play = _mapping(raw_play_value, f"allPlays[{expected_index}]")
        at_bat_index = _integer(play.get("atBatIndex"), "atBatIndex", minimum=0)
        about = _mapping(play.get("about"), f"allPlays[{expected_index}].about")
        about_index = _integer(about.get("atBatIndex"), "about.atBatIndex", minimum=0)
        if at_bat_index != expected_index or about_index != at_bat_index:
            raise CanonicalEventError(
                "MLB at-bat indexes must be complete, unique, and contiguous from zero"
            )
        if about.get("isComplete") is not True:
            raise CanonicalEventError(f"MLB play {at_bat_index} is not complete")
        inning = _integer(about.get("inning"), "inning", minimum=1)
        is_top = about.get("isTopInning")
        if not isinstance(is_top, bool):
            raise CanonicalEventError("MLB inning side must be an explicit boolean")
        inning_half = "TOP" if is_top else "BOTTOM"
        source_half = str(about.get("halfInning") or "").strip().upper()
        if source_half and source_half != inning_half:
            raise CanonicalEventError("MLB inning side fields conflict")
        half = (inning, inning_half)
        new_half = half != current_half
        if new_half:
            _validate_half_inning_transition(
                previous=current_half,
                current=half,
                previous_outs=outs,
                at_bat_index=at_bat_index,
            )
            runners = {}
            outs = 0
            current_half = half

        matchup = _mapping(play.get("matchup"), f"play {at_bat_index} matchup")
        batter_source_id = _positive_id(
            _mapping(matchup.get("batter"), "matchup.batter").get("id"),
            "batter ID",
        )
        pitcher_source_id = _positive_id(
            _mapping(matchup.get("pitcher"), "matchup.pitcher").get("id"),
            "pitcher ID",
        )
        batter_id = _resolve(players, batter_source_id, "batter")
        pitcher_id = _resolve(players, pitcher_source_id, "pitcher")
        batter_hand = _hand(matchup.get("batSide"), "batter", allow_switch=True)
        pitcher_hand = _hand(matchup.get("pitchHand"), "pitcher", allow_switch=False)
        if inning_half == "TOP":
            batting_source_team_id, batting_team_id = away_source_team_id, away_team_id
            pitching_source_team_id, pitching_team_id = home_source_team_id, home_team_id
        else:
            batting_source_team_id, batting_team_id = home_source_team_id, home_team_id
            pitching_source_team_id, pitching_team_id = away_source_team_id, away_team_id

        indexed_events = _indexed_play_events(play, at_bat_index=at_bat_index)
        _validate_substitution_timing_and_matchup(
            indexed_events,
            batter_source_id=batter_source_id,
            pitcher_source_id=pitcher_source_id,
        )
        structural_evidence: list[StructuralRunnerEvidence] = []
        if new_half:
            placement = _initial_runner_placement(
                indexed_events,
                inning=inning,
                inning_half=inning_half,
                at_bat_index=at_bat_index,
                batter_source_id=batter_source_id,
                batting_side="away" if inning_half == "TOP" else "home",
                live_data=live_data,
                players=players,
                away_score=away_score,
                home_score=home_score,
            )
            if placement is not None:
                runners[placement.base or ""] = _RunnerIdentity(
                    source_id=placement.player_source_id,
                    canonical_id=placement.player_id,
                )
                structural_evidence.append(placement)
        elif any(
            str(
                _mapping(event.get("details"), "play event details").get("eventType")
                or ""
            ).strip().lower()
            == "runner_placed"
            for _index, event in indexed_events
        ):
            raise CanonicalEventError(
                "Runner placement is allowed only on the first PA of a half-inning"
            )
        runners, substitutions = _apply_pinch_runner_substitutions(
            indexed_events,
            current_runners=runners,
            players=players,
            batting_side="away" if inning_half == "TOP" else "home",
            live_data=live_data,
            outs=outs,
        )
        structural_evidence.extend(substitutions)
        state_before = _state(
            outs=outs,
            runners=runners,
            away_score=away_score,
            home_score=home_score,
        )

        at_bat_number = at_bat_index + 1
        canonical_pa_identity = {
            "schema_version": PLATE_APPEARANCE_SCHEMA_VERSION,
            "canonical_game_id": game_id,
            "at_bat_number": at_bat_number,
        }
        canonical_pa_id = _checksum(canonical_pa_identity)
        raw_runners = _sequence(play.get("runners"), f"play {at_bat_index} runners")
        movement = _apply_runner_movements(
            raw_runners,
            current_runners=runners,
            players=players,
            batter_source_id=batter_source_id,
            outs_before=outs,
            away_score_before=away_score,
            home_score_before=home_score,
            inning_half=inning_half,
        )
        structural_evidence.extend(movement.suppressed_placeholder_evidence)
        result = _mapping(play.get("result"), f"play {at_bat_index} result")
        terminal_token = normalize_source_token(result.get("eventType"))
        play_complete = about.get("isComplete") is True
        early_evidence: TruncatedPlateAppearanceEvidence | None = None
        try:
            outcome = classify_terminal_result(terminal_token)
        except TaxonomyValidationError as exc:
            if terminal_token not in MLB_OFFICIAL_EARLY_PA_TERMINATION_TOKENS:
                raise CanonicalEventError(str(exc)) from exc
            early_evidence = TruncatedPlateAppearanceEvidence(
                mlb_result_token=terminal_token,
                official_game_final=official_final,
                play_complete=play_complete,
                outs_before=outs,
                outs_after=movement.outs_after,
                runner_out_recorded=movement.runner_out_recorded,
                batter_has_terminal_result=(movement.batter_reached or movement.batter_out),
            )
            try:
                validate_official_early_termination(early_evidence)
            except TaxonomyValidationError as evidence_error:
                raise CanonicalEventError(str(evidence_error)) from evidence_error
            outcome = None

        expected_outs = _integer(
            _mapping(play.get("count"), "play.count").get("outs"),
            "play outs",
            minimum=0,
            maximum=3,
        )
        if movement.outs_after != expected_outs:
            raise CanonicalEventError(
                f"MLB play {at_bat_index} runner outs do not match official play count"
            )
        expected_away_score = _integer(
            result.get("awayScore"), "result.awayScore", minimum=0
        )
        expected_home_score = _integer(
            result.get("homeScore"), "result.homeScore", minimum=0
        )
        if (
            movement.away_score_after != expected_away_score
            or movement.home_score_after != expected_home_score
        ):
            raise CanonicalEventError(
                f"MLB play {at_bat_index} runner scoring does not match official score"
            )
        rbi = _integer(result.get("rbi", 0), "result.rbi", minimum=0)
        if rbi > movement.runs_scored:
            raise CanonicalEventError("RBI cannot exceed runs scored by official movements")
        dropped_third_strike = _proven_dropped_third_strike(
            play,
            indexed_events=indexed_events,
            movement=movement,
            result=result,
            terminal_token=terminal_token,
            batter_source_id=batter_source_id,
            batter_id=batter_id,
            outs_before=outs,
        )
        if dropped_third_strike is not None:
            structural_evidence.append(dropped_third_strike)
        if outcome is not None:
            _validate_outcome_transition(
                outcome,
                terminal_token=terminal_token,
                outs_before=outs,
                outs_after=movement.outs_after,
                runs_scored=movement.runs_scored,
                batter_reached=movement.batter_reached,
                batter_out=movement.batter_out,
                dropped_third_strike=dropped_third_strike is not None,
            )

        after_runners = dict(movement.runners)
        if movement.outs_after == 3:
            after_runners.clear()
        _validate_matchup_post_runners(matchup, after_runners=after_runners)
        state_after = _state(
            outs=movement.outs_after,
            runners=after_runners,
            away_score=movement.away_score_after,
            home_score=movement.home_score_after,
        )
        runner_states = _runner_states_before_count_events(
            indexed_events,
            current_runners=runners,
            normalized_movements=movement.normalized_movements,
            final_runners=movement.runners,
            batter_source_id=batter_source_id,
            players=players,
        )
        evidence_checksums = tuple(row.content_checksum for row in structural_evidence)
        pa_count_events = _canonical_count_events(
            play,
            indexed_events=indexed_events,
            runner_states=runner_states,
            structural_evidence_checksums=evidence_checksums,
            source_version_id=source_version,
            source_game_id=source_game_id,
            canonical_game_id=game_id,
            canonical_plate_appearance_id=canonical_pa_id,
            at_bat_index=at_bat_index,
            at_bat_number=at_bat_number,
            batter_source_id=batter_source_id,
            pitcher_source_id=pitcher_source_id,
            batter_id=batter_id,
            pitcher_id=pitcher_id,
            inning=inning,
            inning_half=inning_half,
            outs_before=outs,
        )
        for event in pa_count_events:
            if event.native_identity_checksum in observed_event_native_ids:
                raise CanonicalEventError("Duplicate MLB count-event native identity")
            observed_event_native_ids.add(event.native_identity_checksum)
        source_event_timestamp = _timestamp(
            about.get("endTime") or play.get("playEndTime"),
            f"play {at_bat_index} end timestamp",
        )
        native_identity = {
            "source_version_id": source_version,
            "source_game_id": source_game_id,
            "at_bat_index": at_bat_index,
        }
        normalized_movements = list(movement.normalized_movements)
        runner_movement_checksum = _checksum(normalized_movements)
        content = {
            "schema_version": PLATE_APPEARANCE_SCHEMA_VERSION,
            "taxonomy_identity": PA_OUTCOME_TAXONOMY_IDENTITY,
            "native_identity": native_identity,
            "canonical_identity": canonical_pa_identity,
            "inning": inning,
            "inning_half": inning_half,
            "batting_team_source_id": batting_source_team_id,
            "pitching_team_source_id": pitching_source_team_id,
            "batting_team_id": batting_team_id,
            "pitching_team_id": pitching_team_id,
            "batter_source_id": batter_source_id,
            "pitcher_source_id": pitcher_source_id,
            "batter_id": batter_id,
            "pitcher_id": pitcher_id,
            "batter_hand": batter_hand,
            "pitcher_hand": pitcher_hand,
            "state_before": state_before.as_dict(),
            "state_after": state_after.as_dict(),
            "terminal_source_token": terminal_token,
            "outcome": outcome.value if outcome is not None else None,
            "rbi": rbi,
            "runs_scored": movement.runs_scored,
            "batter_reached": movement.batter_reached,
            "batter_out": movement.batter_out,
            "source_event_timestamp": source_event_timestamp,
            "official_early_termination": early_evidence is not None,
            "exclusion_reason": (
                TRUNCATED_PA_EXCLUSION_REASON
                if early_evidence is not None
                else None
            ),
            "runner_movement_checksum": runner_movement_checksum,
            "structural_runner_evidence": [
                row.as_dict() for row in structural_evidence
            ],
            "count_event_checksums": [event.content_checksum for event in pa_count_events],
        }
        plate_appearance = CanonicalPlateAppearance(
            source_version_id=source_version,
            source_game_id=source_game_id,
            at_bat_index=at_bat_index,
            at_bat_number=at_bat_number,
            canonical_game_id=game_id,
            canonical_plate_appearance_id=canonical_pa_id,
            inning=inning,
            inning_half=inning_half,
            batting_team_source_id=batting_source_team_id,
            pitching_team_source_id=pitching_source_team_id,
            batting_team_id=batting_team_id,
            pitching_team_id=pitching_team_id,
            batter_source_id=batter_source_id,
            pitcher_source_id=pitcher_source_id,
            batter_id=batter_id,
            pitcher_id=pitcher_id,
            batter_hand=batter_hand,
            pitcher_hand=pitcher_hand,
            state_before=state_before,
            state_after=state_after,
            terminal_source_token=terminal_token,
            outcome=outcome,
            rbi=rbi,
            runs_scored=movement.runs_scored,
            batter_reached=movement.batter_reached,
            batter_out=movement.batter_out,
            source_event_timestamp=source_event_timestamp,
            official_early_termination=early_evidence is not None,
            exclusion_reason=(
                TRUNCATED_PA_EXCLUSION_REASON if early_evidence is not None else None
            ),
            runner_movement_checksum=runner_movement_checksum,
            structural_runner_evidence=tuple(structural_evidence),
            count_event_checksums=tuple(event.content_checksum for event in pa_count_events),
            native_identity_checksum=_checksum(native_identity),
            canonical_identity_checksum=canonical_pa_id,
            content_checksum=_checksum(content),
        )
        plate_appearances.append(plate_appearance)
        count_events.extend(pa_count_events)
        runners = after_runners
        outs = movement.outs_after
        away_score = movement.away_score_after
        home_score = movement.home_score_after

    _validate_final_game_state(
        live_data,
        final_half=current_half,
        away_score=away_score,
        home_score=home_score,
    )

    native_game_identity = {
        "source_version_id": source_version,
        "source_game_id": source_game_id,
    }
    canonical_game_identity = {
        "schema_version": MLB_PBP_GAME_SCHEMA_VERSION,
        "canonical_game_id": game_id,
    }
    game_content = {
        "schema_version": MLB_PBP_GAME_SCHEMA_VERSION,
        "native_identity": native_game_identity,
        "canonical_identity": canonical_game_identity,
        "official_final": official_final,
        "plate_appearance_checksums": [row.content_checksum for row in plate_appearances],
        "count_event_checksums": [row.content_checksum for row in count_events],
    }
    return CanonicalMlbGame(
        source_version_id=source_version,
        source_game_id=source_game_id,
        canonical_game_id=game_id,
        official_final=official_final,
        plate_appearances=tuple(plate_appearances),
        count_events=tuple(count_events),
        native_identity_checksum=_checksum(native_game_identity),
        canonical_identity_checksum=_checksum(canonical_game_identity),
        content_checksum=_checksum(game_content),
    )


def validate_pregame_feature_fields(fields: Iterable[object]) -> None:
    """Reject target score and win/run-expectancy fields from pregame matrices."""

    forbidden: list[str] = []
    for raw in fields:
        name = str(raw or "").strip().lower().replace("-", "_")
        if not name:
            raise CanonicalEventError("Pregame feature names cannot be empty")
        if name in FORBIDDEN_PREGAME_FEATURE_FIELDS or any(
            token in name for token in _FORBIDDEN_PREGAME_FIELD_TOKENS
        ):
            forbidden.append(name)
    if forbidden:
        raise CanonicalEventError(
            f"Target score/win-expectancy fields are forbidden from pregame features: "
            f"{sorted(set(forbidden))}"
        )


def validate_pregame_feature_payload(payload: Mapping[str, object]) -> None:
    validate_pregame_feature_fields(payload.keys())
    for name, value in payload.items():
        if isinstance(value, float) and not isfinite(value):
            raise CanonicalEventError(f"Pregame feature {name} is non-finite")


def _indexed_play_events(
    play: Mapping[str, Any], *, at_bat_index: int
) -> tuple[tuple[int, Mapping[str, Any]], ...]:
    raw_events = _sequence(play.get("playEvents"), f"play {at_bat_index} events")
    indexed: list[tuple[int, Mapping[str, Any]]] = []
    seen_indexes: set[int] = set()
    for position, raw_event in enumerate(raw_events):
        event = _mapping(raw_event, f"play {at_bat_index} event {position}")
        index = _integer(event.get("index"), "play event index", minimum=0)
        if index != position:
            raise CanonicalEventError(
                "MLB play-event indexes must be complete, ordered, and contiguous"
            )
        if index in seen_indexes:
            raise CanonicalEventError("Duplicate MLB play-event index")
        seen_indexes.add(index)
        indexed.append((index, event))
    indexed.sort(key=lambda item: item[0])
    return tuple(indexed)


def _is_count_event(event: Mapping[str, Any]) -> bool:
    if event.get("isPitch") is True:
        return True
    if str(event.get("type") or "").strip().lower() != "no_pitch":
        return False
    details = _mapping(event.get("details"), "play event details")
    description = str(details.get("description") or "").strip().lower()
    return description.startswith("automatic ball") or description.startswith(
        "automatic strike"
    )


def _first_count_event(
    indexed_events: Sequence[tuple[int, Mapping[str, Any]]],
) -> tuple[int, Mapping[str, Any]]:
    for index, event in indexed_events:
        if _is_count_event(event):
            return index, event
    raise CanonicalEventError("A complete MLB plate appearance has no count event")


def _validate_half_inning_transition(
    *,
    previous: tuple[int, str] | None,
    current: tuple[int, str],
    previous_outs: int,
    at_bat_index: int,
) -> None:
    if previous is None:
        if current != (1, "TOP") or at_bat_index != 0:
            raise CanonicalEventError("MLB play-by-play must begin in the top of the first")
        return
    if previous_outs != 3:
        raise CanonicalEventError(
            "A new half-inning cannot begin before the prior half records three outs"
        )
    inning, side = previous
    expected = (inning, "BOTTOM") if side == "TOP" else (inning + 1, "TOP")
    if current != expected:
        raise CanonicalEventError(
            f"Illegal MLB half-inning progression: previous={previous}, current={current}"
        )


def _team_player_ids(live_data: Mapping[str, Any], side: str) -> set[str]:
    boxscore = _mapping(live_data.get("boxscore"), "liveData.boxscore")
    teams = _mapping(boxscore.get("teams"), "liveData.boxscore.teams")
    team = _mapping(teams.get(side), f"liveData.boxscore.teams.{side}")
    raw_players = _mapping(team.get("players"), f"boxscore {side} players")
    result: set[str] = set()
    for value in raw_players.values():
        row = _mapping(value, f"boxscore {side} player")
        person = _mapping(row.get("person"), "boxscore player.person")
        result.add(_positive_id(person.get("id"), "boxscore player ID"))
    return result


def _evidence(
    *,
    evidence_type: str,
    contract_version: str,
    source_event_index: int,
    source_position: int,
    base: str | None,
    player_source_id: str,
    player_id: str,
    replaced_player_source_id: str | None = None,
    replaced_player_id: str | None = None,
    event_timestamp: str | None = None,
    raw_source: object,
) -> StructuralRunnerEvidence:
    source_checksum = _checksum(raw_source)
    normalized = {
        "evidence_type": evidence_type,
        "contract_version": contract_version,
        "source_event_index": source_event_index,
        "source_position": source_position,
        "base": base,
        "player_source_id": player_source_id,
        "player_id": player_id,
        "replaced_player_source_id": replaced_player_source_id,
        "replaced_player_id": replaced_player_id,
        "event_timestamp": event_timestamp,
        "source_checksum": source_checksum,
    }
    return StructuralRunnerEvidence(
        **normalized,
        content_checksum=_checksum(normalized),
    )


def _validate_substitution_timing_and_matchup(
    indexed_events: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    batter_source_id: str,
    pitcher_source_id: str,
) -> None:
    first_index, _first = _first_count_event(indexed_events)
    for index, event in indexed_events:
        details = _mapping(event.get("details"), "play event details")
        event_type = str(details.get("eventType") or "").strip().lower()
        position = event.get("position")
        position_code = (
            str(_mapping(position, "substitution position").get("code") or "").strip()
            if position is not None
            else ""
        )
        is_substitution = event.get("isSubstitution") is True
        is_pitcher_change = event_type == "pitching_substitution" or (
            is_substitution and position_code == "1"
        )
        has_base = event.get("base") is not None
        is_batter_change = (
            is_substitution
            and not has_base
            and (
                position_code == "11"
                or event_type == "offensive_substitution"
            )
        )
        if not is_pitcher_change and not is_batter_change:
            continue
        if index >= first_index:
            role = "pitcher" if is_pitcher_change else "batter"
            raise CanonicalEventError(
                f"Unsupported mid-PA {role} substitution at event {index}"
            )
        player_source_id = _positive_id(
            _mapping(event.get("player"), "substitution player").get("id"),
            "substitution player ID",
        )
        expected = pitcher_source_id if is_pitcher_change else batter_source_id
        if player_source_id != expected:
            role = "pitcher" if is_pitcher_change else "batter"
            raise CanonicalEventError(
                f"Pre-count {role} substitution conflicts with matchup identity"
            )


def _initial_runner_placement(
    indexed_events: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    inning: int,
    inning_half: str,
    at_bat_index: int,
    batter_source_id: str,
    batting_side: str,
    live_data: Mapping[str, Any],
    players: Mapping[str, str],
    away_score: int,
    home_score: int,
) -> StructuralRunnerEvidence | None:
    candidates = [
        (index, event)
        for index, event in indexed_events
        if str(
            _mapping(event.get("details"), "play event details").get("eventType")
            or ""
        ).strip().lower()
        == "runner_placed"
    ]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise CanonicalEventError("A half-inning has ambiguous runner-placement actions")
    if inning < 10:
        raise CanonicalEventError("Automatic runner placement before extra innings is unsupported")
    index, event = candidates[0]
    first_index, first_event = _first_count_event(indexed_events)
    if index >= first_index:
        raise CanonicalEventError("Runner placement must precede every count event")
    if event.get("type") != "action" or event.get("isPitch") is not False:
        raise CanonicalEventError("Runner placement must be an explicit non-pitch action")
    if _integer(event.get("base"), "runner-placement base", minimum=1, maximum=3) != 2:
        raise CanonicalEventError("Automatic runner placement must target second base")
    count = _mapping(event.get("count"), "runner-placement count")
    if {
        "balls": _integer(count.get("balls"), "placement balls", minimum=0),
        "strikes": _integer(count.get("strikes"), "placement strikes", minimum=0),
        "outs": _integer(count.get("outs"), "placement outs", minimum=0),
    } != {"balls": 0, "strikes": 0, "outs": 0}:
        raise CanonicalEventError("Runner placement requires a zero count and zero outs")
    details = _mapping(event.get("details"), "runner-placement details")
    if str(details.get("event") or "").strip() != "Runner Placed On Base":
        raise CanonicalEventError("Runner placement event label is unsupported")
    if details.get("isOut") is not False or details.get("isScoringPlay") is not False:
        raise CanonicalEventError("Runner placement cannot be an out or scoring action")
    if _integer(details.get("awayScore"), "placement away score", minimum=0) != away_score:
        raise CanonicalEventError("Runner placement conflicts with the away-score ledger")
    if _integer(details.get("homeScore"), "placement home score", minimum=0) != home_score:
        raise CanonicalEventError("Runner placement conflicts with the home-score ledger")
    player_source_id = _positive_id(
        _mapping(event.get("player"), "runner-placement player").get("id"),
        "runner-placement player ID",
    )
    if player_source_id == batter_source_id:
        raise CanonicalEventError("Placed runner cannot also be the current batter")
    if player_source_id not in _team_player_ids(live_data, batting_side):
        raise CanonicalEventError("Placed runner is not on the batting-side boxscore")
    player_id = _resolve(players, player_source_id, "placed runner")
    event_timestamp = _timestamp(
        event.get("endTime"), "runner-placement end timestamp"
    )
    first_start = _timestamp(
        first_event.get("startTime"), "first count-event start timestamp"
    )
    if _parse_timestamp(event_timestamp) > _parse_timestamp(first_start):
        raise CanonicalEventError("Runner placement ends after the first count event starts")
    return _evidence(
        evidence_type="HALF_INNING_RUNNER_PLACEMENT",
        contract_version=HALF_INNING_RUNNER_PLACEMENT_VERSION,
        source_event_index=index,
        source_position=index,
        base="2B",
        player_source_id=player_source_id,
        player_id=player_id,
        event_timestamp=event_timestamp,
        raw_source={
            "at_bat_index": at_bat_index,
            "inning": inning,
            "inning_half": inning_half,
            "event": event,
        },
    )


def _apply_pinch_runner_substitutions(
    indexed_events: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    current_runners: Mapping[str, _RunnerIdentity],
    players: Mapping[str, str],
    batting_side: str,
    live_data: Mapping[str, Any],
    outs: int,
) -> tuple[dict[str, _RunnerIdentity], tuple[StructuralRunnerEvidence, ...]]:
    first_index, first_event = _first_count_event(indexed_events)
    updated = dict(current_runners)
    evidence: list[StructuralRunnerEvidence] = []
    team_players: set[str] | None = None
    for index, event in indexed_events:
        details = _mapping(event.get("details"), "play event details")
        event_type = str(details.get("eventType") or "").strip().lower()
        position_value = event.get("position")
        position_code = (
            str(_mapping(position_value, "substitution position").get("code") or "").strip()
            if position_value is not None
            else ""
        )
        has_pinch_signature = (
            event_type == "offensive_substitution"
            and (event.get("base") is not None or position_code == "12")
        ) or position_code == "12"
        if not has_pinch_signature:
            continue
        if (
            event.get("type") != "action"
            or event.get("isPitch") is not False
            or event.get("isSubstitution") is not True
            or event_type != "offensive_substitution"
            or position_code != "12"
        ):
            raise CanonicalEventError("Pinch-runner substitution shape is incomplete")
        if index >= first_index:
            raise CanonicalEventError("Mid-PA pinch-runner substitution is unsupported")
        base_number = _integer(
            event.get("base"), "pinch-runner base", minimum=1, maximum=3
        )
        base = f"{base_number}B"
        count = _mapping(event.get("count"), "pinch-runner count")
        if (
            _integer(count.get("balls"), "pinch-runner balls", minimum=0) != 0
            or _integer(count.get("strikes"), "pinch-runner strikes", minimum=0) != 0
            or _integer(count.get("outs"), "pinch-runner outs", minimum=0) != outs
        ):
            raise CanonicalEventError("Pinch-runner substitution count conflicts with PA start")
        player_source_id = _positive_id(
            _mapping(event.get("player"), "pinch-runner player").get("id"),
            "pinch-runner player ID",
        )
        replaced_source_id = _positive_id(
            _mapping(event.get("replacedPlayer"), "replaced runner").get("id"),
            "replaced runner ID",
        )
        if player_source_id == replaced_source_id:
            raise CanonicalEventError("Pinch runner cannot replace the same source identity")
        observed = updated.get(base)
        if observed is None or observed.source_id != replaced_source_id:
            raise CanonicalEventError(
                "Pinch-runner replacement does not match the occupied base identity"
            )
        if any(row.source_id == player_source_id for row in updated.values()):
            raise CanonicalEventError("Pinch runner already occupies another base")
        if team_players is None:
            team_players = _team_player_ids(live_data, batting_side)
        if {player_source_id, replaced_source_id}.difference(team_players):
            raise CanonicalEventError("Pinch-runner identities are not on the batting team")
        player_id = _resolve(players, player_source_id, "pinch runner")
        replaced_id = _resolve(players, replaced_source_id, "replaced runner")
        event_timestamp = _timestamp(
            event.get("endTime"), "pinch-runner substitution timestamp"
        )
        first_start = _timestamp(
            first_event.get("startTime"), "first count-event start timestamp"
        )
        if _parse_timestamp(event_timestamp) > _parse_timestamp(first_start):
            raise CanonicalEventError("Pinch-runner substitution occurs after count play begins")
        updated[base] = _RunnerIdentity(
            source_id=player_source_id,
            canonical_id=player_id,
        )
        evidence.append(_evidence(
            evidence_type="PINCH_RUNNER_SUBSTITUTION",
            contract_version=PINCH_RUNNER_SUBSTITUTION_VERSION,
            source_event_index=index,
            source_position=index,
            base=base,
            player_source_id=player_source_id,
            player_id=player_id,
            replaced_player_source_id=replaced_source_id,
            replaced_player_id=replaced_id,
            event_timestamp=event_timestamp,
            raw_source=event,
        ))
    return updated, tuple(evidence)


def _canonical_count_events(
    play: Mapping[str, Any],
    *,
    indexed_events: Sequence[tuple[int, Mapping[str, Any]]],
    runner_states: Mapping[int, tuple[BaseRunnerIdentity, ...]],
    structural_evidence_checksums: tuple[str, ...],
    source_version_id: str,
    source_game_id: str,
    canonical_game_id: str,
    canonical_plate_appearance_id: str,
    at_bat_index: int,
    at_bat_number: int,
    batter_source_id: str,
    pitcher_source_id: str,
    batter_id: str,
    pitcher_id: str,
    inning: int,
    inning_half: str,
    outs_before: int,
) -> tuple[MlbCountEvent, ...]:
    balls = 0
    strikes = 0
    event_outs_state = outs_before
    sequence = 0
    output: list[MlbCountEvent] = []
    seen_play_ids: set[str] = set()
    for event_index, event in indexed_events:
        count = _mapping(event.get("count"), "play event count")
        post_balls = _integer(count.get("balls"), "event balls", minimum=0, maximum=4)
        post_strikes = _integer(
            count.get("strikes"), "event strikes", minimum=0, maximum=3
        )
        event_outs = _integer(count.get("outs"), "event outs", minimum=0, maximum=3)
        if event_outs < event_outs_state:
            raise CanonicalEventError("MLB play-event outs cannot decrease within a PA")
        is_pitch = event.get("isPitch") is True
        event_type = str(event.get("type") or "").strip().lower()
        details = _mapping(event.get("details"), "play event details")
        description = str(details.get("description") or "").strip()
        event_kind: CountEventKind | None = None
        if is_pitch:
            event_kind = CountEventKind.PITCH
        elif event_type == "no_pitch" and description.lower().startswith("automatic ball"):
            event_kind = CountEventKind.AUTOMATIC_BALL
        elif event_type == "no_pitch" and description.lower().startswith("automatic strike"):
            event_kind = CountEventKind.AUTOMATIC_STRIKE

        if event_kind is None:
            if (post_balls, post_strikes) != (balls, strikes):
                raise CanonicalEventError(
                    "A non-pitch MLB event changed the count without an explicit "
                    "automatic-ball/strike description"
                )
            event_outs_state = event_outs
            continue
        _validate_count_transition(
            event_kind,
            balls_before=balls,
            strikes_before=strikes,
            balls_after=post_balls,
            strikes_after=post_strikes,
        )
        sequence += 1
        source_play_id: str | None = None
        source_pitch_number: int | None = None
        if is_pitch:
            source_play_id = _required_text(event.get("playId"), "pitch playId")
            if source_play_id in seen_play_ids:
                raise CanonicalEventError("Duplicate MLB physical pitch playId")
            seen_play_ids.add(source_play_id)
            source_pitch_number = _integer(
                event.get("pitchNumber"), "pitchNumber", minimum=1
            )
        event_timestamp = _timestamp(
            event.get("endTime") or event.get("startTime"),
            "count-event timestamp",
        )
        native_identity = {
            "source_version_id": source_version_id,
            "source_game_id": source_game_id,
            "at_bat_index": at_bat_index,
            "source_event_index": event_index,
            "source_play_id": source_play_id,
        }
        canonical_identity = {
            "schema_version": PITCH_EVENT_SCHEMA_VERSION,
            "canonical_game_id": canonical_game_id,
            "at_bat_number": at_bat_number,
            "canonical_sequence": sequence,
        }
        canonical_identity_checksum = _checksum(canonical_identity)
        runner_state_before = runner_states.get(event_index)
        if runner_state_before is None:
            raise CanonicalEventError(
                f"Missing runner-state snapshot for count event {event_index}"
            )
        content = {
            "native_identity": native_identity,
            "canonical_identity": canonical_identity,
            "event_kind": event_kind.value,
            "is_pitch": is_pitch,
            "source_pitch_number": source_pitch_number,
            "description": description,
            "batter_source_id": batter_source_id,
            "pitcher_source_id": pitcher_source_id,
            "batter_id": batter_id,
            "pitcher_id": pitcher_id,
            "canonical_plate_appearance_id": canonical_plate_appearance_id,
            "inning": inning,
            "inning_half": inning_half,
            "balls_before": balls,
            "strikes_before": strikes,
            "balls_after": post_balls,
            "strikes_after": post_strikes,
            "outs": event_outs,
            "runner_state_before": [
                row.as_dict() for row in runner_state_before
            ],
            "structural_evidence_checksums": list(
                structural_evidence_checksums
            ),
            "event_timestamp": event_timestamp,
        }
        output.append(MlbCountEvent(
            source_version_id=source_version_id,
            source_game_id=source_game_id,
            at_bat_index=at_bat_index,
            at_bat_number=at_bat_number,
            source_event_index=event_index,
            source_play_id=source_play_id,
            source_pitch_number=source_pitch_number,
            canonical_game_id=canonical_game_id,
            canonical_plate_appearance_id=canonical_plate_appearance_id,
            canonical_sequence=sequence,
            event_kind=event_kind,
            is_pitch=is_pitch,
            source_description=description,
            batter_source_id=batter_source_id,
            pitcher_source_id=pitcher_source_id,
            batter_id=batter_id,
            pitcher_id=pitcher_id,
            inning=inning,
            inning_half=inning_half,
            balls_before=balls,
            strikes_before=strikes,
            balls_after=post_balls,
            strikes_after=post_strikes,
            outs=event_outs,
            runner_state_before=runner_state_before,
            structural_evidence_checksums=structural_evidence_checksums,
            event_timestamp=event_timestamp,
            native_identity_checksum=_checksum(native_identity),
            canonical_identity_checksum=canonical_identity_checksum,
            content_checksum=_checksum(content),
        ))
        balls, strikes = post_balls, post_strikes
        event_outs_state = event_outs

    play_count = _mapping(play.get("count"), "play.count")
    expected_balls = _integer(play_count.get("balls"), "play balls", minimum=0, maximum=4)
    expected_strikes = _integer(
        play_count.get("strikes"), "play strikes", minimum=0, maximum=3
    )
    expected_outs = _integer(play_count.get("outs"), "play outs", minimum=0, maximum=3)
    if output and (balls, strikes) != (expected_balls, expected_strikes):
        raise CanonicalEventError("Ordered MLB count events do not reconcile to PA count")
    if event_outs_state > expected_outs:
        raise CanonicalEventError("MLB play-event outs exceed the official PA outs-after")
    return tuple(output)


def _validate_count_transition(
    event_kind: CountEventKind,
    *,
    balls_before: int,
    strikes_before: int,
    balls_after: int,
    strikes_after: int,
) -> None:
    ball_delta = balls_after - balls_before
    strike_delta = strikes_after - strikes_before
    if event_kind is CountEventKind.AUTOMATIC_BALL:
        valid = ball_delta == 1 and strike_delta == 0
    elif event_kind is CountEventKind.AUTOMATIC_STRIKE:
        valid = ball_delta == 0 and strike_delta == 1
    else:
        valid = (
            ball_delta in {0, 1}
            and strike_delta in {0, 1}
            and not (ball_delta == 1 and strike_delta == 1)
        )
    if not valid:
        raise CanonicalEventError(
            "MLB count transition is inconsistent with physical/automatic event kind"
        )


def _apply_runner_movements(
    raw_runners: Sequence[object],
    *,
    current_runners: Mapping[str, _RunnerIdentity],
    players: Mapping[str, str],
    batter_source_id: str,
    outs_before: int,
    away_score_before: int,
    home_score_before: int,
    inning_half: str,
) -> _MovementResult:
    normalized: list[dict[str, object]] = []
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    parsed_records: list[
        tuple[int, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], str]
    ] = []
    for position, raw_record in enumerate(raw_runners):
        record = _mapping(raw_record, f"runner movement {position}")
        details = _mapping(record.get("details"), "runner movement details")
        movement = _mapping(record.get("movement"), "runner movement")
        runner_source_id = _positive_id(
            _mapping(details.get("runner"), "runner identity").get("id"),
            "runner ID",
        )
        parsed_records.append((position, record, details, movement, runner_source_id))

    suppressed: list[StructuralRunnerEvidence] = []
    retained_records: list[
        tuple[int, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], str]
    ] = []
    for position, record, details, movement, runner_source_id in parsed_records:
        is_out = movement.get("isOut")
        all_null_placeholder = is_out is None and all(
            movement.get(name) is None
            for name in ("start", "originBase", "end", "outBase", "outNumber")
        )
        if not all_null_placeholder:
            retained_records.append(
                (position, record, details, movement, runner_source_id)
            )
            continue
        play_index = _integer(
            details.get("playIndex"), "placeholder runner playIndex", minimum=0
        )
        matches = [
            candidate
            for candidate in parsed_records
            if candidate[0] != position
            and candidate[4] == runner_source_id
            and isinstance(candidate[3].get("isOut"), bool)
            and _integer(
                candidate[2].get("playIndex"),
                "matching runner playIndex",
                minimum=0,
            )
            == play_index
        ]
        if len(matches) != 1:
            raise CanonicalEventError(
                "All-null runner placeholder lacks one exact same-runner movement"
            )
        if details.get("isScoringEvent") is not False:
            raise CanonicalEventError(
                "All-null runner placeholder cannot be a scoring record"
            )
        runner_id = _resolve(players, runner_source_id, "placeholder runner")
        suppressed.append(_evidence(
            evidence_type="NULL_RUNNER_PLACEHOLDER_SUPPRESSED",
            contract_version=NULL_RUNNER_PLACEHOLDER_VERSION,
            source_event_index=play_index,
            source_position=position,
            base=None,
            player_source_id=runner_source_id,
            player_id=runner_id,
            raw_source={
                "placeholder": record,
                "matched_source_position": matches[0][0],
                "matched_source_checksum": _checksum(matches[0][1]),
            },
        ))

    for position, record, details, movement, runner_source_id in retained_records:
        runner_id = _resolve(players, runner_source_id, "runner")
        play_index = _integer(
            details.get("playIndex"), "runner movement playIndex", minimum=0
        )
        start = _base(movement.get("start"))
        origin = _base(movement.get("originBase"))
        if start is None:
            start = origin
        end = _base(movement.get("end"), allow_score=True)
        is_out = movement.get("isOut")
        if not isinstance(is_out, bool):
            raise CanonicalEventError("Runner movement isOut must be boolean")
        out_number_raw = movement.get("outNumber")
        out_number = (
            None
            if out_number_raw is None
            else _integer(out_number_raw, "runner outNumber", minimum=1, maximum=3)
        )
        is_scoring_event = details.get("isScoringEvent") is True
        item = {
            "source_runner_id": runner_source_id,
            "runner_id": runner_id,
            "start": start,
            "origin": origin,
            "end": end,
            "is_out": is_out,
            "out_number": out_number,
            "is_scoring_event": is_scoring_event,
            "play_index": play_index,
            "detail_event_type": str(details.get("eventType") or "").strip().lower(),
            "source_position": position,
            "source_checksum": _checksum(record),
        }
        normalized.append(item)
        grouped[runner_source_id].append(item)

    updated = dict(current_runners)
    initial_bases: dict[str, str] = {}
    final_destinations: dict[str, tuple[str, _RunnerIdentity]] = {}
    out_numbers: list[int] = []
    runs_scored = 0
    batter_reached = False
    batter_out = False
    runner_out_recorded = False

    for runner_source_id, path in grouped.items():
        runner = _RunnerIdentity(
            source_id=runner_source_id,
            canonical_id=str(path[0]["runner_id"]),
        )
        first_start = path[0]["start"]
        if first_start is None:
            if runner_source_id != batter_source_id:
                raise CanonicalEventError(
                    "Only the batter may begin a runner movement without a base"
                )
        else:
            existing = current_runners.get(str(first_start))
            if existing != runner:
                raise CanonicalEventError(
                    "Runner movement start does not match prior MLB base occupancy: "
                    f"source_runner={runner_source_id}, start={first_start}, "
                    f"observed={existing.source_id if existing is not None else None}"
                )
            if str(first_start) in initial_bases:
                raise CanonicalEventError("Two runner paths begin on the same base")
            initial_bases[str(first_start)] = runner_source_id

        current_position = first_start
        terminal_out = False
        terminal_score = False
        reached_safely = False
        for step_index, step in enumerate(path):
            declared_start = step["start"]
            if step_index > 0 and declared_start != current_position:
                raise CanonicalEventError("Runner movement path is discontinuous")
            is_out = bool(step["is_out"])
            end = step["end"]
            if is_out:
                if end is not None:
                    raise CanonicalEventError("Out runner movement cannot retain an end base")
                out_number = step["out_number"]
                if out_number is None:
                    raise CanonicalEventError("Out runner movement lacks outNumber")
                out_numbers.append(int(out_number))
                runner_out_recorded = True
                terminal_out = True
                current_position = None
            else:
                if step["out_number"] is not None:
                    raise CanonicalEventError("Safe runner movement cannot have outNumber")
                if end == "SCORE":
                    if step["is_scoring_event"] is not True:
                        raise CanonicalEventError(
                            "Scoring movement lacks official isScoringEvent evidence"
                        )
                    runs_scored += 1
                    terminal_score = True
                    reached_safely = True
                    current_position = None
                elif end is None:
                    current_position = declared_start
                else:
                    current_position = end
                    reached_safely = True
            if (terminal_out or terminal_score) and step_index != len(path) - 1:
                raise CanonicalEventError("Runner movement continues after terminal state")

        if runner_source_id == batter_source_id:
            batter_out = terminal_out
            batter_reached = reached_safely
        if current_position in {"1B", "2B", "3B"}:
            destination = str(current_position)
            if destination in final_destinations:
                raise CanonicalEventError("Two runner paths end on the same base")
            final_destinations[destination] = (runner_source_id, runner)

    for base in initial_bases:
        updated.pop(base, None)
    for base, (_source_id, runner) in final_destinations.items():
        if base in updated:
            raise CanonicalEventError("Runner destination conflicts with stationary runner")
        updated[base] = runner

    outs_after = outs_before + len(out_numbers)
    if outs_after > 3:
        raise CanonicalEventError("Runner movements produce more than three outs")
    if sorted(out_numbers) != list(range(outs_before + 1, outs_after + 1)):
        raise CanonicalEventError("Runner outNumber sequence conflicts with outs state")
    away_score_after = away_score_before + (runs_scored if inning_half == "TOP" else 0)
    home_score_after = home_score_before + (runs_scored if inning_half == "BOTTOM" else 0)
    return _MovementResult(
        runners=updated,
        outs_after=outs_after,
        away_score_after=away_score_after,
        home_score_after=home_score_after,
        runs_scored=runs_scored,
        batter_reached=batter_reached,
        batter_out=batter_out,
        runner_out_recorded=runner_out_recorded,
        normalized_movements=tuple(normalized),
        suppressed_placeholder_evidence=tuple(suppressed),
    )


def _base_sort_key(base: str) -> int:
    try:
        return _BASE_ORDER.index(base)
    except ValueError as exc:
        raise CanonicalEventError(f"Unsupported occupied base: {base}") from exc


def _runner_identities(
    runners: Mapping[str, _RunnerIdentity],
) -> tuple[BaseRunnerIdentity, ...]:
    return tuple(
        BaseRunnerIdentity(
            base=base,
            source_id=runner.source_id,
            canonical_id=runner.canonical_id,
        )
        for base, runner in sorted(
            runners.items(), key=lambda item: _base_sort_key(item[0])
        )
    )


def _state(
    *,
    outs: int,
    runners: Mapping[str, _RunnerIdentity],
    away_score: int,
    home_score: int,
) -> BaseOutScoreState:
    identities = _runner_identities(runners)
    return BaseOutScoreState(
        outs=outs,
        base_mask=_base_mask(runners),
        away_score=away_score,
        home_score=home_score,
        runners=identities,
    )


def _apply_timed_movement_batch(
    current: Mapping[str, _RunnerIdentity],
    steps: Sequence[Mapping[str, object]],
    *,
    batter_source_id: str,
    players: Mapping[str, str],
) -> dict[str, _RunnerIdentity]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for step in sorted(steps, key=lambda row: int(row["source_position"])):
        grouped[str(step["source_runner_id"])].append(step)
    updated = dict(current)
    starts: dict[str, str] = {}
    destinations: dict[str, _RunnerIdentity] = {}
    for source_id, path in grouped.items():
        identity = _RunnerIdentity(
            source_id=source_id,
            canonical_id=_resolve(players, source_id, "timed runner"),
        )
        start = path[0]["start"]
        if start is None:
            if source_id != batter_source_id:
                raise CanonicalEventError(
                    "Only the batter may start a timed runner path without a base"
                )
            if any(row.source_id == source_id for row in updated.values()):
                raise CanonicalEventError("Timed batter path duplicates an occupied runner")
        else:
            base = str(start)
            observed = updated.get(base)
            if observed != identity:
                raise CanonicalEventError(
                    "Timed runner movement conflicts with base identity"
                )
            if base in starts:
                raise CanonicalEventError("Timed runner paths duplicate a starting base")
            starts[base] = source_id
        position = start
        for step_index, step in enumerate(path):
            if step_index and step["start"] != position:
                raise CanonicalEventError("Timed runner path is discontinuous")
            if bool(step["is_out"]) or step["end"] == "SCORE":
                position = None
            elif step["end"] is None:
                position = step["start"]
            else:
                position = step["end"]
        if position in _BASE_ORDER:
            base = str(position)
            if base in destinations:
                raise CanonicalEventError("Timed runners share a destination base")
            destinations[base] = identity
    for base in starts:
        updated.pop(base, None)
    for base, identity in destinations.items():
        if base in updated:
            raise CanonicalEventError(
                "Timed runner destination conflicts with a stationary runner"
            )
        updated[base] = identity
    return updated


def _runner_states_before_count_events(
    indexed_events: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    current_runners: Mapping[str, _RunnerIdentity],
    normalized_movements: Sequence[Mapping[str, object]],
    final_runners: Mapping[str, _RunnerIdentity],
    batter_source_id: str,
    players: Mapping[str, str],
) -> dict[int, tuple[BaseRunnerIdentity, ...]]:
    event_indexes = {index for index, _event in indexed_events}
    steps_by_index: dict[int, list[Mapping[str, object]]] = defaultdict(list)
    for step in normalized_movements:
        play_index = int(step["play_index"])
        if play_index not in event_indexes:
            raise CanonicalEventError(
                f"Runner movement refers to absent play event {play_index}"
            )
        steps_by_index[play_index].append(step)
    ledger = dict(current_runners)
    snapshots: dict[int, tuple[BaseRunnerIdentity, ...]] = {}
    for event_index, event in indexed_events:
        if _is_count_event(event):
            snapshots[event_index] = _runner_identities(ledger)
        if steps_by_index.get(event_index):
            ledger = _apply_timed_movement_batch(
                ledger,
                steps_by_index[event_index],
                batter_source_id=batter_source_id,
                players=players,
            )
    if ledger != dict(final_runners):
        raise CanonicalEventError(
            "Timed runner identities do not reconcile to the official PA movement ledger"
        )
    return snapshots


def _proven_dropped_third_strike(
    play: Mapping[str, Any],
    *,
    indexed_events: Sequence[tuple[int, Mapping[str, Any]]],
    movement: _MovementResult,
    result: Mapping[str, Any],
    terminal_token: str,
    batter_source_id: str,
    batter_id: str,
    outs_before: int,
) -> StructuralRunnerEvidence | None:
    candidate_shape = (
        terminal_token == "strikeout"
        and result.get("isOut") is False
        and movement.batter_reached
        and not movement.batter_out
        and movement.outs_after == outs_before
    )
    if not candidate_shape:
        return None
    count_events = [
        (index, event)
        for index, event in indexed_events
        if _is_count_event(event)
    ]
    if not count_events:
        return None
    event_index, final_event = count_events[-1]
    details = _mapping(final_event.get("details"), "dropped-third-strike pitch details")
    count = _mapping(final_event.get("count"), "dropped-third-strike count")
    batter_steps = [
        step
        for step in movement.normalized_movements
        if step["source_runner_id"] == batter_source_id
    ]
    proven = (
        final_event.get("isPitch") is True
        and str(details.get("description") or "").strip().lower()
        in {"swinging strike", "swinging strike (blocked)"}
        and details.get("isStrike") is True
        and details.get("isOut") is False
        and _integer(count.get("strikes"), "dropped-third strikes", minimum=0) == 3
        and len(batter_steps) == 1
        and batter_steps[0]["play_index"] == event_index
        and batter_steps[0]["start"] is None
        and batter_steps[0]["end"] == "1B"
        and batter_steps[0]["is_out"] is False
        and batter_steps[0]["detail_event_type"] in {"passed_ball", "wild_pitch"}
    )
    if not proven:
        return None
    matchup = _mapping(play.get("matchup"), "dropped-third-strike matchup")
    post_first = _mapping(
        matchup.get("postOnFirst"), "dropped-third-strike postOnFirst"
    )
    if _positive_id(post_first.get("id"), "postOnFirst runner ID") != batter_source_id:
        return None
    timestamp = _timestamp(
        final_event.get("endTime") or final_event.get("startTime"),
        "dropped-third-strike timestamp",
    )
    return _evidence(
        evidence_type="DROPPED_THIRD_STRIKE_SAFE_REACH",
        contract_version=DROPPED_THIRD_STRIKE_VERSION,
        source_event_index=event_index,
        source_position=int(batter_steps[0]["source_position"]),
        base="1B",
        player_source_id=batter_source_id,
        player_id=batter_id,
        event_timestamp=timestamp,
        raw_source={
            "result": result,
            "terminal_event": final_event,
            "batter_movement": batter_steps[0],
            "placeholder_evidence_checksums": [
                row.content_checksum
                for row in movement.suppressed_placeholder_evidence
            ],
        },
    )


def _validate_matchup_post_runners(
    matchup: Mapping[str, Any],
    *,
    after_runners: Mapping[str, _RunnerIdentity],
) -> None:
    post_fields = ("postOnFirst", "postOnSecond", "postOnThird")
    if not any(field in matchup for field in post_fields):
        return
    for base, field in (
        ("1B", "postOnFirst"),
        ("2B", "postOnSecond"),
        ("3B", "postOnThird"),
    ):
        raw = matchup.get(field)
        observed = after_runners.get(base)
        if raw is None:
            if observed is not None:
                raise CanonicalEventError(
                    f"matchup.{field} omits an occupied canonical base"
                )
            continue
        source_id = _positive_id(
            _mapping(raw, f"matchup.{field}").get("id"),
            f"matchup.{field} runner ID",
        )
        if observed is None or observed.source_id != source_id:
            raise CanonicalEventError(
                f"matchup.{field} conflicts with canonical runner identity"
            )


def _validate_final_game_state(
    live_data: Mapping[str, Any],
    *,
    final_half: tuple[int, str] | None,
    away_score: int,
    home_score: int,
) -> None:
    if final_half is None:
        raise CanonicalEventError("Final game has no completed half-inning evidence")
    linescore = _mapping(live_data.get("linescore"), "liveData.linescore")
    teams = _mapping(linescore.get("teams"), "liveData.linescore.teams")
    expected_away = _integer(
        _mapping(teams.get("away"), "linescore away").get("runs"),
        "final away runs",
        minimum=0,
    )
    expected_home = _integer(
        _mapping(teams.get("home"), "linescore home").get("runs"),
        "final home runs",
        minimum=0,
    )
    if (away_score, home_score) != (expected_away, expected_home):
        raise CanonicalEventError("Canonical scores do not match the final linescore")


def _validate_outcome_transition(
    outcome: PlateAppearanceOutcome,
    *,
    terminal_token: str,
    outs_before: int,
    outs_after: int,
    runs_scored: int,
    batter_reached: bool,
    batter_out: bool,
    dropped_third_strike: bool,
) -> None:
    if outcome is PlateAppearanceOutcome.STRIKEOUT:
        ordinary_strikeout = batter_out and outs_after > outs_before
        safe_reach = (
            dropped_third_strike
            and batter_reached
            and not batter_out
            and outs_after == outs_before
        )
        if not ordinary_strikeout and not safe_reach:
            raise CanonicalEventError("Strikeout taxonomy conflicts with batter/outs movement")
    elif outcome is PlateAppearanceOutcome.WALK_HBP_CI:
        if not batter_reached:
            raise CanonicalEventError("Walk/HBP/CI taxonomy conflicts with batter movement")
    elif outcome is PlateAppearanceOutcome.HOME_RUN:
        if not batter_reached or runs_scored < 1:
            raise CanonicalEventError("Home-run taxonomy conflicts with scoring movement")
    elif outcome in {
        PlateAppearanceOutcome.SINGLE,
        PlateAppearanceOutcome.DOUBLE,
        PlateAppearanceOutcome.TRIPLE,
        PlateAppearanceOutcome.OTHER_REACH,
    }:
        if not batter_reached:
            raise CanonicalEventError(
                f"{terminal_token} taxonomy conflicts with batter movement"
            )
    elif outcome is PlateAppearanceOutcome.BIP_OUT and outs_after <= outs_before:
        # MLB retains a sacrifice event token when a fielding error lets the
        # batter and all runners reach safely.  The frozen taxonomy still maps
        # that exact source token to BIP_OUT, while the canonical transition
        # preserves the official zero-out/error movement rather than inventing
        # an out.
        if terminal_token not in {"sac_bunt", "sac_fly"} or not batter_reached:
            raise CanonicalEventError("BIP-out taxonomy does not record an out transition")


def _unwrap_feed(feed: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(feed, Mapping):
        raise CanonicalEventError("MLB feed must be a mapping")
    payload = feed.get("payload")
    if payload is None:
        return feed
    return _mapping(payload, "feed payload")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CanonicalEventError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CanonicalEventError(f"{label} must be an array")
    return value


def _required_text(value: object, label: str) -> str:
    parsed = str(value or "").strip()
    if not parsed:
        raise CanonicalEventError(f"{label} is required")
    return parsed


def _integer(
    value: object,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool):
        raise CanonicalEventError(f"{label} must be an integer")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise CanonicalEventError(f"{label} must be an integer") from exc
    if str(value).strip() not in {str(parsed), f"+{parsed}"}:
        raise CanonicalEventError(f"{label} must be an exact integer")
    if minimum is not None and parsed < minimum:
        raise CanonicalEventError(f"{label} is below {minimum}")
    if maximum is not None and parsed > maximum:
        raise CanonicalEventError(f"{label} exceeds {maximum}")
    return parsed


def _positive_id(value: object, label: str) -> str:
    return str(_integer(value, label, minimum=1))


def _exact_mapping(values: Mapping[str | int, str], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    reverse: dict[str, str] = {}
    for raw_source, raw_canonical in values.items():
        source = _positive_id(raw_source, f"{label} source ID")
        canonical = _required_text(raw_canonical, f"canonical {label} ID")
        if source in result:
            raise CanonicalEventError(f"Duplicate source {label} mapping: {source}")
        if canonical in reverse:
            raise CanonicalEventError(
                f"Ambiguous canonical {label} mapping: {canonical}"
            )
        result[source] = canonical
        reverse[canonical] = source
    return result


def _resolve(values: Mapping[str, str], source_id: str, label: str) -> str:
    resolved = values.get(source_id)
    if resolved is None:
        raise CanonicalEventError(f"Missing exact canonical mapping for {label} {source_id}")
    return resolved


def _hand(value: object, label: str, *, allow_switch: bool) -> str:
    code = str(_mapping(value, f"{label} hand").get("code") or "").strip().upper()
    allowed = {"L", "R", "S"} if allow_switch else {"L", "R"}
    if code not in allowed:
        raise CanonicalEventError(f"Unsupported {label} hand: {code or '<empty>'}")
    return code


def _base(value: object, *, allow_score: bool = False) -> str | None:
    if value is None:
        return None
    parsed = str(value).strip().upper()
    if not parsed:
        return None
    allowed = {"1B", "2B", "3B"}
    if allow_score:
        allowed.add("SCORE")
    if parsed not in allowed:
        raise CanonicalEventError(f"Unsupported MLB runner base: {parsed}")
    return parsed


def _base_mask(runners: Mapping[str, _RunnerIdentity]) -> int:
    unexpected = set(runners).difference(_BASE_ORDER)
    if unexpected:
        raise CanonicalEventError(f"Unexpected runner bases: {sorted(unexpected)}")
    return sum(_BASE_BITS[base] for base in _BASE_ORDER if base in runners)


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CanonicalEventError("Timestamp is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CanonicalEventError("Timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _timestamp(value: object, label: str) -> str:
    text = _required_text(value, label)
    try:
        parsed = _parse_timestamp(text)
    except CanonicalEventError as exc:
        raise CanonicalEventError(f"{label}: {exc}") from exc
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "BaseRunnerIdentity",
    "BaseOutScoreState",
    "CanonicalEventError",
    "CanonicalMlbGame",
    "CanonicalPlateAppearance",
    "CountEventKind",
    "FORBIDDEN_PREGAME_FEATURE_FIELDS",
    "HALF_INNING_RUNNER_PLACEMENT_VERSION",
    "MLB_PBP_GAME_SCHEMA_VERSION",
    "MlbCountEvent",
    "PITCH_EVENT_SCHEMA_VERSION",
    "PLATE_APPEARANCE_SCHEMA_VERSION",
    "StructuralRunnerEvidence",
    "canonicalize_mlb_play_by_play",
    "validate_pregame_feature_fields",
    "validate_pregame_feature_payload",
]
