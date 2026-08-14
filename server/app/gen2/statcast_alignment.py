"""Count-aware Statcast-to-MLB play-by-play reconciliation.

Statcast numbers physical pitches and automatic balls/strikes in one ordered
sequence.  MLB marks the latter as ``no_pitch``.  Alignment therefore operates
on MLB count events, not merely MLB physical pitches, and rejects every missing,
extra, ambiguous, or conflicting row.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Any

from app.gen2.event_taxonomy import (
    PA_OUTCOME_TAXONOMY_IDENTITY,
    PA_OUTCOME_TAXONOMY_VERSION,
    TRUNCATED_PA_SOURCE_TOKEN,
    PlateAppearanceOutcome,
    TaxonomyObservation,
    TaxonomyValidationError,
    classify_terminal_result,
    ordered_outcomes,
    validate_taxonomy_counts,
)
from app.gen2.mlb_pbp import (
    PITCH_EVENT_SCHEMA_VERSION,
    BaseOutScoreState,
    BaseRunnerIdentity,
    CanonicalMlbGame,
    CanonicalPlateAppearance,
    CountEventKind,
    MlbCountEvent,
)


STATCAST_ALIGNMENT_SCHEMA_VERSION = "mlb_gen2_statcast_mlb_alignment_v2"


class StatcastAlignmentError(ValueError):
    """Cross-source pitch/PA alignment is not exact and one-to-one."""


_STATCAST_DESCRIPTION_KIND = {
    "automatic_ball": "AUTOMATIC_BALL",
    "automatic_strike": "AUTOMATIC_STRIKE",
    "ball": "BALL",
    "blocked_ball": "BLOCKED_BALL",
    "called_strike": "CALLED_STRIKE",
    "foul": "FOUL",
    "foul_bunt": "FOUL_BUNT",
    "foul_tip": "FOUL_TIP",
    "hit_by_pitch": "HIT_BY_PITCH",
    "hit_into_play": "IN_PLAY",
    "hit_into_play_no_out": "IN_PLAY",
    "hit_into_play_score": "IN_PLAY",
    "missed_bunt": "MISSED_BUNT",
    "pitchout": "PITCHOUT",
    "swinging_strike": "SWINGING_STRIKE",
    "swinging_strike_blocked": "SWINGING_STRIKE_BLOCKED",
}
_MLB_DESCRIPTION_KIND = {
    "ball": "BALL",
    "ball in dirt": "BLOCKED_BALL",
    "called strike": "CALLED_STRIKE",
    "foul": "FOUL",
    "foul bunt": "FOUL_BUNT",
    "foul tip": "FOUL_TIP",
    "hit by pitch": "HIT_BY_PITCH",
    "in play, no out": "IN_PLAY",
    "in play, out(s)": "IN_PLAY",
    "in play, run(s)": "IN_PLAY",
    "missed bunt": "MISSED_BUNT",
    "pitchout": "PITCHOUT",
    "swinging strike": "SWINGING_STRIKE",
    "swinging strike (blocked)": "SWINGING_STRIKE_BLOCKED",
}
_BALL_KINDS = frozenset({
    "AUTOMATIC_BALL",
    "BALL",
    "BLOCKED_BALL",
    "HIT_BY_PITCH",
    "PITCHOUT",
})
_STRIKE_KINDS = frozenset({
    "AUTOMATIC_STRIKE",
    "CALLED_STRIKE",
    "FOUL",
    "FOUL_BUNT",
    "FOUL_TIP",
    "MISSED_BUNT",
    "SWINGING_STRIKE",
    "SWINGING_STRIKE_BLOCKED",
})
_SWING_KINDS = frozenset({
    "FOUL",
    "FOUL_BUNT",
    "FOUL_TIP",
    "IN_PLAY",
    "MISSED_BUNT",
    "SWINGING_STRIKE",
    "SWINGING_STRIKE_BLOCKED",
})
_CONTACT_KINDS = frozenset({"FOUL", "FOUL_BUNT", "FOUL_TIP", "IN_PLAY"})
_WHIFF_KINDS = frozenset({
    "MISSED_BUNT",
    "SWINGING_STRIKE",
    "SWINGING_STRIKE_BLOCKED",
})
_FOUL_KINDS = frozenset({"FOUL", "FOUL_BUNT", "FOUL_TIP"})


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _checksum(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AlignedPitchEvent:
    """One canonical pitch/count event retaining both provider-native identities."""

    statcast_source_version_id: str
    mlb_source_version_id: str
    source_game_id: str
    canonical_game_id: str
    at_bat_number: int
    pitch_sequence: int
    canonical_plate_appearance_id: str
    canonical_event_id: str
    statcast_pitch_number: int
    mlb_at_bat_index: int
    mlb_event_index: int
    mlb_play_id: str | None
    mlb_pitch_number: int | None
    is_physical_pitch: bool
    count_event_kind: str
    event_timestamp: str
    batter_source_id: str
    pitcher_source_id: str
    batter_id: str
    pitcher_id: str
    batter_hand: str
    pitcher_hand: str
    inning: int
    inning_half: str
    balls_before: int
    strikes_before: int
    balls_after: int
    strikes_after: int
    outs_before: int
    runner_state_before: tuple[BaseRunnerIdentity, ...]
    structural_evidence_checksums: tuple[str, ...]
    pa_state_before: BaseOutScoreState
    pa_state_after: BaseOutScoreState
    pitch_type: str | None
    release_velocity: float | None
    movement_x: float | None
    movement_z: float | None
    plate_x: float | None
    plate_z: float | None
    swing: bool
    contact: bool
    whiff: bool
    called_strike: bool
    ball: bool
    foul: bool
    batted_ball_event: str | None
    launch_speed: float | None
    launch_angle: float | None
    estimated_woba_value: float | None
    barrel: bool | None
    hard_hit: bool | None
    source_description: str
    terminal_source_token: str | None
    terminal_outcome: PlateAppearanceOutcome | None
    statcast_native_identity_checksum: str
    mlb_native_identity_checksum: str
    canonical_identity_checksum: str
    statcast_source_row_checksum: str
    mlb_event_checksum: str
    mlb_pa_checksum: str
    content_checksum: str

    @property
    def schema_version(self) -> str:
        return PITCH_EVENT_SCHEMA_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "taxonomy_version": PA_OUTCOME_TAXONOMY_VERSION,
            "taxonomy_identity": PA_OUTCOME_TAXONOMY_IDENTITY,
            "statcast_source_version_id": self.statcast_source_version_id,
            "mlb_source_version_id": self.mlb_source_version_id,
            "source_game_id": self.source_game_id,
            "canonical_game_id": self.canonical_game_id,
            "at_bat_number": self.at_bat_number,
            "pitch_sequence": self.pitch_sequence,
            "canonical_plate_appearance_id": self.canonical_plate_appearance_id,
            "canonical_event_id": self.canonical_event_id,
            "statcast_pitch_number": self.statcast_pitch_number,
            "mlb_at_bat_index": self.mlb_at_bat_index,
            "mlb_event_index": self.mlb_event_index,
            "mlb_play_id": self.mlb_play_id,
            "mlb_pitch_number": self.mlb_pitch_number,
            "is_physical_pitch": self.is_physical_pitch,
            "count_event_kind": self.count_event_kind,
            "event_timestamp": self.event_timestamp,
            "batter_source_id": self.batter_source_id,
            "pitcher_source_id": self.pitcher_source_id,
            "batter_id": self.batter_id,
            "pitcher_id": self.pitcher_id,
            "batter_hand": self.batter_hand,
            "pitcher_hand": self.pitcher_hand,
            "inning": self.inning,
            "inning_half": self.inning_half,
            "balls_before": self.balls_before,
            "strikes_before": self.strikes_before,
            "balls_after": self.balls_after,
            "strikes_after": self.strikes_after,
            "outs_before": self.outs_before,
            "runner_state_before": [
                row.as_dict() for row in self.runner_state_before
            ],
            "structural_evidence_checksums": list(
                self.structural_evidence_checksums
            ),
            "pa_state_before": self.pa_state_before.as_dict(),
            "pa_state_after": self.pa_state_after.as_dict(),
            "pitch_type": self.pitch_type,
            "release_velocity": self.release_velocity,
            "movement_x": self.movement_x,
            "movement_z": self.movement_z,
            "plate_x": self.plate_x,
            "plate_z": self.plate_z,
            "swing": self.swing,
            "contact": self.contact,
            "whiff": self.whiff,
            "called_strike": self.called_strike,
            "ball": self.ball,
            "foul": self.foul,
            "batted_ball_event": self.batted_ball_event,
            "launch_speed": self.launch_speed,
            "launch_angle": self.launch_angle,
            "estimated_woba_value": self.estimated_woba_value,
            "barrel": self.barrel,
            "hard_hit": self.hard_hit,
            "source_description": self.source_description,
            "terminal_source_token": self.terminal_source_token,
            "terminal_outcome": (
                self.terminal_outcome.value if self.terminal_outcome is not None else None
            ),
            "native_identities": {
                "statcast": {
                    "source_version_id": self.statcast_source_version_id,
                    "game_pk": self.source_game_id,
                    "at_bat_number": self.at_bat_number,
                    "pitch_number": self.statcast_pitch_number,
                    "checksum": self.statcast_native_identity_checksum,
                },
                "mlb": {
                    "source_version_id": self.mlb_source_version_id,
                    "game_pk": self.source_game_id,
                    "at_bat_index": self.mlb_at_bat_index,
                    "event_index": self.mlb_event_index,
                    "play_id": self.mlb_play_id,
                    "checksum": self.mlb_native_identity_checksum,
                },
            },
            "canonical_identity_checksum": self.canonical_identity_checksum,
            "statcast_source_row_checksum": self.statcast_source_row_checksum,
            "mlb_event_checksum": self.mlb_event_checksum,
            "mlb_pa_checksum": self.mlb_pa_checksum,
            "content_checksum": self.content_checksum,
        }


@dataclass(frozen=True, slots=True)
class StatcastAlignmentResult:
    statcast_source_version_id: str
    mlb_source_version_id: str
    source_game_id: str
    canonical_game_id: str
    aligned_events: tuple[AlignedPitchEvent, ...]
    plate_appearance_count: int
    physical_pitch_count: int
    automatic_count_event_count: int
    taxonomy_category_counts: Mapping[str, int]
    truncated_pa_exclusion_count: int
    mlb_game_content_checksum: str
    content_checksum: str

    @property
    def schema_version(self) -> str:
        return STATCAST_ALIGNMENT_SCHEMA_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "statcast_source_version_id": self.statcast_source_version_id,
            "mlb_source_version_id": self.mlb_source_version_id,
            "source_game_id": self.source_game_id,
            "canonical_game_id": self.canonical_game_id,
            "aligned_event_count": len(self.aligned_events),
            "plate_appearance_count": self.plate_appearance_count,
            "physical_pitch_count": self.physical_pitch_count,
            "automatic_count_event_count": self.automatic_count_event_count,
            "taxonomy_category_counts": dict(self.taxonomy_category_counts),
            "truncated_pa_exclusion_count": self.truncated_pa_exclusion_count,
            "mlb_game_content_checksum": self.mlb_game_content_checksum,
            "aligned_event_checksums": [row.content_checksum for row in self.aligned_events],
            "content_checksum": self.content_checksum,
        }


def align_statcast_to_mlb(
    statcast_rows: Iterable[Mapping[str, Any]],
    mlb_game: CanonicalMlbGame,
    *,
    statcast_source_version_id: str,
) -> StatcastAlignmentResult:
    """Align every Statcast row to exactly one ordered MLB count event."""

    statcast_version = _required_text(
        statcast_source_version_id, "statcast_source_version_id"
    )
    if not mlb_game.official_final:
        raise StatcastAlignmentError("Statcast alignment requires an official final MLB game")
    source_game_id = _positive_id(mlb_game.source_game_id, "MLB source game ID")
    rows_by_pa: dict[int, list[tuple[int, Mapping[str, Any], str]]] = defaultdict(list)
    observed_statcast_keys: set[tuple[str, int, int]] = set()
    for position, raw_row in enumerate(statcast_rows):
        if not isinstance(raw_row, Mapping):
            raise StatcastAlignmentError(f"Statcast row {position} must be an object")
        row = dict(raw_row)
        game_pk = _positive_id(row.get("game_pk"), "Statcast game_pk")
        if game_pk != source_game_id:
            raise StatcastAlignmentError(
                f"Statcast row belongs to game {game_pk}, expected {source_game_id}"
            )
        at_bat_number = _integer(
            row.get("at_bat_number"), "Statcast at_bat_number", minimum=1
        )
        pitch_number = _integer(
            row.get("pitch_number"), "Statcast pitch_number", minimum=1
        )
        key = (game_pk, at_bat_number, pitch_number)
        if key in observed_statcast_keys:
            raise StatcastAlignmentError(f"Duplicate/ambiguous Statcast pitch key: {key}")
        observed_statcast_keys.add(key)
        rows_by_pa[at_bat_number].append((pitch_number, row, _checksum(row)))

    pa_by_number: dict[int, CanonicalPlateAppearance] = {}
    for pa in mlb_game.plate_appearances:
        if pa.at_bat_number in pa_by_number:
            raise StatcastAlignmentError("Duplicate canonical MLB plate-appearance number")
        pa_by_number[pa.at_bat_number] = pa
    events_by_pa: dict[int, list[MlbCountEvent]] = defaultdict(list)
    observed_mlb_events: set[str] = set()
    for event in mlb_game.count_events:
        if event.canonical_identity_checksum in observed_mlb_events:
            raise StatcastAlignmentError("Duplicate canonical MLB count-event identity")
        observed_mlb_events.add(event.canonical_identity_checksum)
        events_by_pa[event.at_bat_number].append(event)

    if set(rows_by_pa) != set(pa_by_number):
        missing = sorted(set(pa_by_number).difference(rows_by_pa))
        extra = sorted(set(rows_by_pa).difference(pa_by_number))
        raise StatcastAlignmentError(
            f"Statcast/MLB PA populations differ: missing={missing}, extra={extra}"
        )

    aligned: list[AlignedPitchEvent] = []
    taxonomy_observations: list[TaxonomyObservation] = []
    for at_bat_number in sorted(pa_by_number):
        pa = pa_by_number[at_bat_number]
        source_rows = sorted(rows_by_pa[at_bat_number], key=lambda item: item[0])
        mlb_events = sorted(
            events_by_pa.get(at_bat_number, []), key=lambda item: item.canonical_sequence
        )
        expected_sequence = list(range(1, len(source_rows) + 1))
        if [row[0] for row in source_rows] != expected_sequence:
            raise StatcastAlignmentError(
                f"Statcast pitch numbers are not contiguous for PA {at_bat_number}"
            )
        if len(source_rows) != len(mlb_events) or not source_rows:
            raise StatcastAlignmentError(
                f"Statcast/MLB count-event cardinality differs for PA {at_bat_number}"
            )
        if [event.canonical_sequence for event in mlb_events] != expected_sequence:
            raise StatcastAlignmentError(
                f"MLB count-event sequence is incomplete for PA {at_bat_number}"
            )

        terminal_token = _terminal_token(source_rows, pa)
        taxonomy_observations.append(TaxonomyObservation(
            source_token=terminal_token,
            truncated_evidence=(
                pa.truncated_evidence
                if terminal_token == TRUNCATED_PA_SOURCE_TOKEN
                else None
            ),
        ))

        for index, ((pitch_number, row, source_row_checksum), mlb_event) in enumerate(
            zip(source_rows, mlb_events, strict=True)
        ):
            is_terminal = index == len(source_rows) - 1
            aligned.append(_align_one(
                row,
                source_row_checksum=source_row_checksum,
                statcast_source_version_id=statcast_version,
                pitch_number=pitch_number,
                pa=pa,
                mlb_event=mlb_event,
                terminal_token=terminal_token if is_terminal else None,
                terminal_outcome=pa.outcome if is_terminal else None,
            ))

    report = validate_taxonomy_counts(taxonomy_observations)
    category_counts = MappingProxyType({
        outcome.value: int(report.category_counts.get(outcome, 0))
        for outcome in ordered_outcomes()
    })
    physical_pitch_count = sum(row.is_physical_pitch for row in aligned)
    automatic_count = len(aligned) - physical_pitch_count
    content = {
        "schema_version": STATCAST_ALIGNMENT_SCHEMA_VERSION,
        "statcast_source_version_id": statcast_version,
        "mlb_source_version_id": mlb_game.source_version_id,
        "source_game_id": source_game_id,
        "canonical_game_id": mlb_game.canonical_game_id,
        "aligned_event_checksums": [row.content_checksum for row in aligned],
        "plate_appearance_count": len(pa_by_number),
        "physical_pitch_count": physical_pitch_count,
        "automatic_count_event_count": automatic_count,
        "taxonomy": report.as_dict(),
        "mlb_game_content_checksum": mlb_game.content_checksum,
    }
    return StatcastAlignmentResult(
        statcast_source_version_id=statcast_version,
        mlb_source_version_id=mlb_game.source_version_id,
        source_game_id=source_game_id,
        canonical_game_id=mlb_game.canonical_game_id,
        aligned_events=tuple(aligned),
        plate_appearance_count=len(pa_by_number),
        physical_pitch_count=physical_pitch_count,
        automatic_count_event_count=automatic_count,
        taxonomy_category_counts=category_counts,
        truncated_pa_exclusion_count=report.excluded_truncated_pa_count,
        mlb_game_content_checksum=mlb_game.content_checksum,
        content_checksum=_checksum(content),
    )


def _terminal_token(
    source_rows: list[tuple[int, Mapping[str, Any], str]],
    pa: CanonicalPlateAppearance,
) -> str:
    terminals: list[tuple[int, str]] = []
    for pitch_number, row, _checksum_value in source_rows:
        token = str(row.get("events") or "").strip().lower()
        if token:
            terminals.append((pitch_number, token))
    if len(terminals) != 1 or terminals[0][0] != source_rows[-1][0]:
        raise StatcastAlignmentError(
            f"PA {pa.at_bat_number} must have exactly one terminal result on its last row"
        )
    token = terminals[0][1]
    try:
        outcome = classify_terminal_result(
            token,
            truncated_evidence=(
                pa.truncated_evidence if token == TRUNCATED_PA_SOURCE_TOKEN else None
            ),
        )
    except TaxonomyValidationError as exc:
        raise StatcastAlignmentError(str(exc)) from exc
    if pa.outcome is None:
        if token != TRUNCATED_PA_SOURCE_TOKEN or outcome is not None:
            raise StatcastAlignmentError(
                "MLB early-terminated PA does not align to exact truncated_pa token"
            )
    elif token != pa.terminal_source_token or outcome is not pa.outcome:
        raise StatcastAlignmentError(
            f"Statcast/MLB terminal result conflicts for PA {pa.at_bat_number}"
        )
    return token


def _align_one(
    row: Mapping[str, Any],
    *,
    source_row_checksum: str,
    statcast_source_version_id: str,
    pitch_number: int,
    pa: CanonicalPlateAppearance,
    mlb_event: MlbCountEvent,
    terminal_token: str | None,
    terminal_outcome: PlateAppearanceOutcome | None,
) -> AlignedPitchEvent:
    batter_source_id = _positive_id(row.get("batter"), "Statcast batter")
    pitcher_source_id = _positive_id(row.get("pitcher"), "Statcast pitcher")
    inning = _integer(row.get("inning"), "Statcast inning", minimum=1)
    inning_half = _inning_half(row.get("inning_topbot"))
    balls = _integer(row.get("balls"), "Statcast balls", minimum=0, maximum=3)
    strikes = _integer(row.get("strikes"), "Statcast strikes", minimum=0, maximum=2)
    outs_when_up = _integer(
        row.get("outs_when_up"), "Statcast outs_when_up", minimum=0, maximum=2
    )
    batter_hand = _enum(row.get("stand"), {"L", "R", "S"}, "Statcast stand")
    pitcher_hand = _enum(row.get("p_throws"), {"L", "R"}, "Statcast p_throws")
    statcast_runners = _statcast_runner_source_ids(row)
    mlb_runners = {
        runner.base: runner.source_id for runner in mlb_event.runner_state_before
    }
    expected = {
        "batter": (batter_source_id, pa.batter_source_id),
        "pitcher": (pitcher_source_id, pa.pitcher_source_id),
        "inning": (inning, pa.inning),
        "inning_half": (inning_half, pa.inning_half),
        "balls_before": (balls, mlb_event.balls_before),
        "strikes_before": (strikes, mlb_event.strikes_before),
        "outs_before": (outs_when_up, mlb_event.outs),
        "batter_hand": (batter_hand, pa.batter_hand),
        "pitcher_hand": (pitcher_hand, pa.pitcher_hand),
        "canonical_sequence": (pitch_number, mlb_event.canonical_sequence),
        "base_runner_identities": (statcast_runners, mlb_runners),
    }
    conflicts = sorted(name for name, values in expected.items() if values[0] != values[1])
    if conflicts:
        raise StatcastAlignmentError(
            f"Statcast/MLB alignment fields conflict for PA {pa.at_bat_number}, "
            f"pitch {pitch_number}: {conflicts}"
        )

    statcast_description = _required_text(row.get("description"), "Statcast description")
    statcast_kind = _STATCAST_DESCRIPTION_KIND.get(statcast_description.lower())
    if statcast_kind is None:
        raise StatcastAlignmentError(
            f"Unsupported Statcast pitch description: {statcast_description}"
        )
    mlb_kind = _mlb_description_kind(mlb_event)
    if statcast_kind != mlb_kind:
        raise StatcastAlignmentError(
            f"Statcast/MLB pitch descriptions conflict for PA {pa.at_bat_number}, "
            f"pitch {pitch_number}: {statcast_kind} != {mlb_kind}"
        )
    expected_type = "X" if statcast_kind == "IN_PLAY" else (
        "B" if statcast_kind in _BALL_KINDS else "S"
    )
    source_type = _enum(row.get("type"), {"B", "S", "X"}, "Statcast type")
    if source_type != expected_type:
        raise StatcastAlignmentError("Statcast pitch type conflicts with description")

    statcast_native_identity = {
        "source_version_id": statcast_source_version_id,
        "game_pk": pa.source_game_id,
        "at_bat_number": pa.at_bat_number,
        "pitch_number": pitch_number,
    }
    pitch_type = _optional_text(row.get("pitch_type"))
    release_velocity = _optional_float(row.get("release_speed"), "release_speed")
    movement_x = _optional_float(row.get("pfx_x"), "pfx_x")
    movement_z = _optional_float(row.get("pfx_z"), "pfx_z")
    plate_x = _optional_float(row.get("plate_x"), "plate_x")
    plate_z = _optional_float(row.get("plate_z"), "plate_z")
    launch_speed = _optional_float(row.get("launch_speed"), "launch_speed")
    launch_angle = _optional_float(row.get("launch_angle"), "launch_angle")
    estimated_woba = _optional_float(
        row.get("estimated_woba_using_speedangle"),
        "estimated_woba_using_speedangle",
    )
    barrel = _barrel(row)
    hard_hit = _hard_hit(row, launch_speed)
    batted_ball_event = _optional_text(row.get("bb_type"))
    canonical_identity_checksum = mlb_event.canonical_identity_checksum
    content = {
        "schema_version": PITCH_EVENT_SCHEMA_VERSION,
        "statcast_native_identity": statcast_native_identity,
        "statcast_source_row_checksum": source_row_checksum,
        "mlb_native_identity_checksum": mlb_event.native_identity_checksum,
        "mlb_event_checksum": mlb_event.content_checksum,
        "canonical_identity_checksum": canonical_identity_checksum,
        "canonical_plate_appearance_id": pa.canonical_plate_appearance_id,
        "event_timestamp": mlb_event.event_timestamp,
        "batter_id": pa.batter_id,
        "pitcher_id": pa.pitcher_id,
        "count": {
            "balls_before": balls,
            "strikes_before": strikes,
            "balls_after": mlb_event.balls_after,
            "strikes_after": mlb_event.strikes_after,
            "outs_before": mlb_event.outs,
        },
        "runner_state_before": [
            runner.as_dict() for runner in mlb_event.runner_state_before
        ],
        "structural_evidence_checksums": list(
            mlb_event.structural_evidence_checksums
        ),
        "pa_state_before": pa.state_before.as_dict(),
        "pa_state_after": pa.state_after.as_dict(),
        "mlb_pa_checksum": pa.content_checksum,
        "description_kind": statcast_kind,
        "terminal_source_token": terminal_token,
        "terminal_outcome": (
            terminal_outcome.value if terminal_outcome is not None else None
        ),
        "measurements": {
            "pitch_type": pitch_type,
            "release_velocity": release_velocity,
            "movement_x": movement_x,
            "movement_z": movement_z,
            "plate_x": plate_x,
            "plate_z": plate_z,
            "launch_speed": launch_speed,
            "launch_angle": launch_angle,
            "estimated_woba_value": estimated_woba,
            "barrel": barrel,
            "hard_hit": hard_hit,
        },
    }
    return AlignedPitchEvent(
        statcast_source_version_id=statcast_source_version_id,
        mlb_source_version_id=pa.source_version_id,
        source_game_id=pa.source_game_id,
        canonical_game_id=pa.canonical_game_id,
        at_bat_number=pa.at_bat_number,
        pitch_sequence=pitch_number,
        canonical_plate_appearance_id=pa.canonical_plate_appearance_id,
        canonical_event_id=canonical_identity_checksum,
        statcast_pitch_number=pitch_number,
        mlb_at_bat_index=mlb_event.at_bat_index,
        mlb_event_index=mlb_event.source_event_index,
        mlb_play_id=mlb_event.source_play_id,
        mlb_pitch_number=mlb_event.source_pitch_number,
        is_physical_pitch=mlb_event.is_pitch,
        count_event_kind=mlb_event.event_kind.value,
        event_timestamp=mlb_event.event_timestamp,
        batter_source_id=batter_source_id,
        pitcher_source_id=pitcher_source_id,
        batter_id=pa.batter_id,
        pitcher_id=pa.pitcher_id,
        batter_hand=batter_hand,
        pitcher_hand=pitcher_hand,
        inning=inning,
        inning_half=inning_half,
        balls_before=balls,
        strikes_before=strikes,
        balls_after=mlb_event.balls_after,
        strikes_after=mlb_event.strikes_after,
        outs_before=mlb_event.outs,
        runner_state_before=mlb_event.runner_state_before,
        structural_evidence_checksums=mlb_event.structural_evidence_checksums,
        pa_state_before=pa.state_before,
        pa_state_after=pa.state_after,
        pitch_type=pitch_type,
        release_velocity=release_velocity,
        movement_x=movement_x,
        movement_z=movement_z,
        plate_x=plate_x,
        plate_z=plate_z,
        swing=statcast_kind in _SWING_KINDS,
        contact=statcast_kind in _CONTACT_KINDS,
        whiff=statcast_kind in _WHIFF_KINDS,
        called_strike=statcast_kind == "CALLED_STRIKE",
        ball=statcast_kind in _BALL_KINDS,
        foul=statcast_kind in _FOUL_KINDS,
        batted_ball_event=batted_ball_event,
        launch_speed=launch_speed,
        launch_angle=launch_angle,
        estimated_woba_value=estimated_woba,
        barrel=barrel,
        hard_hit=hard_hit,
        source_description=statcast_description,
        terminal_source_token=terminal_token,
        terminal_outcome=terminal_outcome,
        statcast_native_identity_checksum=_checksum(statcast_native_identity),
        mlb_native_identity_checksum=mlb_event.native_identity_checksum,
        canonical_identity_checksum=canonical_identity_checksum,
        statcast_source_row_checksum=source_row_checksum,
        mlb_event_checksum=mlb_event.content_checksum,
        mlb_pa_checksum=pa.content_checksum,
        content_checksum=_checksum(content),
    )


def _mlb_description_kind(event: MlbCountEvent) -> str:
    if event.event_kind is CountEventKind.AUTOMATIC_BALL:
        return "AUTOMATIC_BALL"
    if event.event_kind is CountEventKind.AUTOMATIC_STRIKE:
        return "AUTOMATIC_STRIKE"
    kind = _MLB_DESCRIPTION_KIND.get(event.source_description.strip().lower())
    if kind is None:
        raise StatcastAlignmentError(
            f"Unsupported MLB pitch description: {event.source_description}"
        )
    return kind


def _statcast_runner_source_ids(row: Mapping[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for base, field in (("1B", "on_1b"), ("2B", "on_2b"), ("3B", "on_3b")):
        if field not in row:
            raise StatcastAlignmentError(
                f"Statcast base-runner field is required: {field}"
            )
        value = row.get(field)
        if value is None or str(value).strip() == "":
            continue
        output[base] = _positive_id(value, f"Statcast {field}")
    if len(set(output.values())) != len(output):
        raise StatcastAlignmentError(
            "One Statcast runner identity cannot occupy multiple bases"
        )
    return output


def _barrel(row: Mapping[str, Any]) -> bool | None:
    explicit = _optional_boolean(row.get("barrel"), "barrel")
    if explicit is not None:
        return explicit
    launch_speed_angle = row.get("launch_speed_angle")
    if launch_speed_angle is None or str(launch_speed_angle).strip() == "":
        return None
    category = _integer(
        launch_speed_angle, "launch_speed_angle", minimum=1, maximum=6
    )
    return category == 6


def _hard_hit(row: Mapping[str, Any], launch_speed: float | None) -> bool | None:
    explicit = _optional_boolean(row.get("hard_hit"), "hard_hit")
    if explicit is not None:
        return explicit
    return None if launch_speed is None else launch_speed >= 95.0


def _optional_boolean(value: object, label: str) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        return value
    parsed = str(value).strip().lower()
    if parsed in {"1", "true"}:
        return True
    if parsed in {"0", "false"}:
        return False
    raise StatcastAlignmentError(f"{label} must be boolean/0/1 when present")


def _optional_float(value: object, label: str) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise StatcastAlignmentError(f"{label} must be numeric when present") from exc
    if not isfinite(parsed):
        raise StatcastAlignmentError(f"{label} must be finite when present")
    return parsed


def _optional_text(value: object) -> str | None:
    parsed = str(value or "").strip()
    return parsed or None


def _required_text(value: object, label: str) -> str:
    parsed = str(value or "").strip()
    if not parsed:
        raise StatcastAlignmentError(f"{label} is required")
    return parsed


def _integer(
    value: object,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool):
        raise StatcastAlignmentError(f"{label} must be an integer")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise StatcastAlignmentError(f"{label} must be an integer") from exc
    if str(value).strip() not in {str(parsed), f"+{parsed}"}:
        raise StatcastAlignmentError(f"{label} must be an exact integer")
    if minimum is not None and parsed < minimum:
        raise StatcastAlignmentError(f"{label} is below {minimum}")
    if maximum is not None and parsed > maximum:
        raise StatcastAlignmentError(f"{label} exceeds {maximum}")
    return parsed


def _positive_id(value: object, label: str) -> str:
    return str(_integer(value, label, minimum=1))


def _enum(value: object, allowed: set[str], label: str) -> str:
    parsed = _required_text(value, label).upper()
    if parsed not in allowed:
        raise StatcastAlignmentError(f"{label} has unsupported value: {parsed}")
    return parsed


def _inning_half(value: object) -> str:
    parsed = _required_text(value, "Statcast inning_topbot").strip().upper()
    if parsed == "TOP":
        return "TOP"
    if parsed in {"BOT", "BOTTOM"}:
        return "BOTTOM"
    raise StatcastAlignmentError(f"Unsupported Statcast inning side: {parsed}")


__all__ = [
    "AlignedPitchEvent",
    "STATCAST_ALIGNMENT_SCHEMA_VERSION",
    "StatcastAlignmentError",
    "StatcastAlignmentResult",
    "align_statcast_to_mlb",
]
