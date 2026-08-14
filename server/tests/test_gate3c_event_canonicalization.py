from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from itertools import chain

import pytest

from app.gen2.event_taxonomy import (
    PA_OUTCOME_TAXONOMY_IDENTITY,
    PA_OUTCOME_TAXONOMY_VERSION,
    RETAINED_2025_06_17_CATEGORY_COUNTS,
    SOURCE_TOKEN_TO_OUTCOME,
    PlateAppearanceOutcome,
    TaxonomyObservation,
    TaxonomyValidationError,
    TruncatedPlateAppearanceEvidence,
    ordered_outcomes,
    validate_retained_2025_06_17_counts,
)
from app.gen2.mlb_pbp import (
    HALF_INNING_RUNNER_PLACEMENT_VERSION,
    PITCH_EVENT_SCHEMA_VERSION,
    PLATE_APPEARANCE_SCHEMA_VERSION,
    CanonicalEventError,
    CountEventKind,
    canonicalize_mlb_play_by_play,
    validate_pregame_feature_fields,
)
from app.gen2.statcast_alignment import (
    STATCAST_ALIGNMENT_SCHEMA_VERSION,
    StatcastAlignmentError,
    align_statcast_to_mlb,
)


PLAYERS = {
    101: "player-batter-101",
    102: "player-batter-102",
    103: "player-batter-103",
    104: "player-batter-104",
    105: "player-runner-105",
    201: "player-pitcher-201",
}
TEAMS = {10: "team-home", 20: "team-away"}


def _runner(
    player_id: int,
    *,
    start: str | None,
    end: str | None,
    is_out: bool = False,
    out_number: int | None = None,
    scoring: bool = False,
    play_index: int = 0,
) -> dict[str, object]:
    return {
        "details": {
            "runner": {"id": player_id},
            "isScoringEvent": scoring,
            "playIndex": play_index,
        },
        "movement": {
            "originBase": start,
            "start": start,
            "end": end,
            "isOut": is_out,
            "outNumber": out_number,
        },
    }


def _pitch(
    index: int,
    *,
    description: str,
    balls: int,
    strikes: int,
    outs: int,
    pitch_number: int,
) -> dict[str, object]:
    return {
        "index": index,
        "isPitch": True,
        "type": "pitch",
        "playId": f"00000000-0000-0000-0000-{index + 1:012d}",
        "pitchNumber": pitch_number,
        "startTime": f"2025-06-18T01:{index:02d}:00Z",
        "endTime": f"2025-06-18T01:{index:02d}:05Z",
        "count": {"balls": balls, "strikes": strikes, "outs": outs},
        "details": {"description": description},
    }


def _automatic(
    index: int,
    *,
    description: str,
    balls: int,
    strikes: int,
    outs: int,
) -> dict[str, object]:
    return {
        "index": index,
        "isPitch": False,
        "type": "no_pitch",
        "startTime": f"2025-06-18T01:{index:02d}:00Z",
        "endTime": f"2025-06-18T01:{index:02d}:05Z",
        "count": {"balls": balls, "strikes": strikes, "outs": outs},
        "details": {"description": description},
    }


def _play(
    index: int,
    *,
    batter: int,
    pitcher: int = 201,
    events: list[dict[str, object]],
    terminal: str,
    outs_after: int,
    away_score: int,
    home_score: int = 0,
    runners: list[dict[str, object]],
    rbi: int = 0,
    inning: int = 1,
    is_top: bool = True,
    post_runners: dict[str, int] | None = None,
) -> dict[str, object]:
    final_count = events[-1]["count"]
    matchup = {
        "batter": {"id": batter},
        "pitcher": {"id": pitcher},
        "batSide": {"code": "R"},
        "pitchHand": {"code": "L"},
    }
    for base, player_id in (post_runners or {}).items():
        matchup[{"1B": "postOnFirst", "2B": "postOnSecond", "3B": "postOnThird"}[base]] = {
            "id": player_id
        }
    return {
        "atBatIndex": index,
        "about": {
            "atBatIndex": index,
            "inning": inning,
            "isTopInning": is_top,
            "halfInning": "top" if is_top else "bottom",
            "isComplete": True,
            "endTime": f"2025-06-18T02:{index:02d}:00Z",
        },
        "matchup": matchup,
        "playEvents": events,
        "runners": runners,
        "count": {
            "balls": final_count["balls"],
            "strikes": final_count["strikes"],
            "outs": outs_after,
        },
        "result": {
            "eventType": terminal,
            "awayScore": away_score,
            "homeScore": home_score,
            "rbi": rbi,
        },
    }


def _feed(
    plays: list[dict[str, object]],
    *,
    away_players: tuple[int, ...] = (101, 102, 103, 104),
    home_players: tuple[int, ...] = (101, 102, 103, 105),
) -> dict[str, object]:
    final_result = plays[-1]["result"]
    return {
        "gamePk": 777001,
        "gameData": {
            "status": {"abstractGameState": "Final", "codedGameState": "F"},
            "teams": {"home": {"id": 10}, "away": {"id": 20}},
        },
        "liveData": {
            "plays": {"allPlays": plays},
            "linescore": {
                "teams": {
                    "away": {"runs": final_result["awayScore"]},
                    "home": {"runs": final_result["homeScore"]},
                }
            },
            "boxscore": {
                "teams": {
                    "away": {
                        "players": {
                            f"ID{player_id}": {"person": {"id": player_id}}
                            for player_id in away_players
                        }
                    },
                    "home": {
                        "players": {
                            f"ID{player_id}": {"person": {"id": player_id}}
                            for player_id in home_players
                        }
                    },
                }
            },
        },
    }


def _canonicalize(plays: list[dict[str, object]]):
    return _canonicalize_feed(_feed(plays))


def _canonicalize_feed(
    feed: dict[str, object],
    *,
    players: dict[int, str] = PLAYERS,
    teams: dict[int, str] = TEAMS,
    canonical_game_id: str = "canonical-game-777001",
):
    return canonicalize_mlb_play_by_play(
        feed,
        source_version_id="mlb-feed-v1",
        canonical_game_id=canonical_game_id,
        canonical_player_ids=players,
        canonical_team_ids=teams,
    )


def _single_double_out_plays() -> list[dict[str, object]]:
    return [
        _play(
            0,
            batter=101,
            events=[_pitch(0, description="In play, no out", balls=0, strikes=0, outs=0,
                           pitch_number=1)],
            terminal="single",
            outs_after=0,
            away_score=0,
            runners=[_runner(101, start=None, end="1B")],
        ),
        _play(
            1,
            batter=102,
            events=[_pitch(0, description="In play, run(s)", balls=0, strikes=0, outs=0,
                           pitch_number=1)],
            terminal="double",
            outs_after=0,
            away_score=1,
            rbi=1,
            # MLB commonly lists the batter before the forced/advancing runner.
            runners=[
                _runner(102, start=None, end="2B"),
                _runner(101, start="1B", end="score", scoring=True),
            ],
        ),
        _play(
            2,
            batter=103,
            events=[_pitch(0, description="In play, out(s)", balls=0, strikes=0, outs=0,
                           pitch_number=1)],
            terminal="field_out",
            outs_after=1,
            away_score=1,
            runners=[_runner(103, start=None, end=None, is_out=True, out_number=1)],
        ),
    ]


def _strikeout_with_automatic_event():
    play = _play(
        0,
        batter=101,
        events=[
            _automatic(
                0,
                description="Automatic Strike - Batter Pitch Timer Violation",
                balls=0,
                strikes=1,
                outs=0,
            ),
            _pitch(1, description="Ball", balls=1, strikes=1, outs=0, pitch_number=1),
            _pitch(
                2,
                description="Swinging Strike",
                balls=1,
                strikes=2,
                outs=0,
                pitch_number=2,
            ),
            _pitch(
                3,
                description="Swinging Strike",
                balls=1,
                strikes=3,
                outs=0,
                pitch_number=3,
            ),
        ],
        terminal="strikeout",
        outs_after=1,
        away_score=0,
        runners=[
            _runner(
                101,
                start=None,
                end=None,
                is_out=True,
                out_number=1,
                play_index=3,
            )
        ],
    )
    game = _canonicalize([play])
    rows = [
        {
            "game_pk": "777001",
            "at_bat_number": "1",
            "pitch_number": "1",
            "batter": "101",
            "pitcher": "201",
            "inning": "1",
            "inning_topbot": "Top",
            "outs_when_up": "0",
            "balls": "0",
            "strikes": "0",
            "stand": "R",
            "p_throws": "L",
            "description": "automatic_strike",
            "type": "S",
            "events": "",
            "home_score": "999",
            "post_home_score": "999",
        },
        {
            "game_pk": "777001",
            "at_bat_number": "1",
            "pitch_number": "2",
            "batter": "101",
            "pitcher": "201",
            "inning": "1",
            "inning_topbot": "Top",
            "outs_when_up": "0",
            "balls": "0",
            "strikes": "1",
            "stand": "R",
            "p_throws": "L",
            "description": "ball",
            "type": "B",
            "events": "",
            "pitch_type": "FF",
            "release_speed": "96.2",
            "pfx_x": "-0.35",
            "pfx_z": "1.22",
            "plate_x": "0.1",
            "plate_z": "2.8",
        },
        {
            "game_pk": "777001",
            "at_bat_number": "1",
            "pitch_number": "3",
            "batter": "101",
            "pitcher": "201",
            "inning": "1",
            "inning_topbot": "Top",
            "outs_when_up": "0",
            "balls": "1",
            "strikes": "1",
            "stand": "R",
            "p_throws": "L",
            "description": "swinging_strike",
            "type": "S",
            "events": "",
        },
        {
            "game_pk": "777001",
            "at_bat_number": "1",
            "pitch_number": "4",
            "batter": "101",
            "pitcher": "201",
            "inning": "1",
            "inning_topbot": "Top",
            "outs_when_up": "0",
            "balls": "1",
            "strikes": "2",
            "stand": "R",
            "p_throws": "L",
            "description": "swinging_strike",
            "type": "S",
            "events": "strikeout",
        },
    ]
    for row in rows:
        row.update({"on_1b": "", "on_2b": "", "on_3b": ""})
    return game, rows


def _placement_action(
    player_id: int,
    *,
    away_score: int,
    home_score: int,
    index: int = 0,
    base: int = 2,
    outs: int = 0,
) -> dict[str, object]:
    return {
        "base": base,
        "count": {"balls": 0, "strikes": 0, "outs": outs},
        "details": {
            "awayScore": away_score,
            "description": f"Player {player_id} starts inning at 2nd base.",
            "event": "Runner Placed On Base",
            "eventType": "runner_placed",
            "hasReview": False,
            "homeScore": home_score,
            "isOut": False,
            "isScoringPlay": False,
        },
        "endTime": "2025-06-18T01:00:05Z",
        "index": index,
        "isPitch": False,
        "player": {"id": player_id},
        "startTime": "2025-06-18T01:00:00Z",
        "type": "action",
    }


def _pinch_runner_action(
    player_id: int,
    replaced_player_id: int,
    *,
    index: int = 0,
    base: int = 1,
    outs: int = 0,
) -> dict[str, object]:
    return {
        "base": base,
        "count": {"balls": 0, "strikes": 0, "outs": outs},
        "details": {
            "awayScore": 0,
            "description": "Offensive Substitution: pinch-runner.",
            "event": "Offensive Substitution",
            "eventType": "offensive_substitution",
            "hasReview": False,
            "homeScore": 0,
            "isOut": False,
            "isScoringPlay": False,
        },
        "endTime": "2025-06-18T01:00:05Z",
        "index": index,
        "isPitch": False,
        "isSubstitution": True,
        "player": {"id": player_id},
        "position": {"code": "12", "name": "Pinch Runner"},
        "replacedPlayer": {"id": replaced_player_id},
        "startTime": "2025-06-18T01:00:00Z",
        "type": "action",
    }


def _extra_inning_feed() -> dict[str, object]:
    plays: list[dict[str, object]] = []
    at_bat_index = 0
    for inning in range(1, 10):
        for is_top in (True, False):
            for outs_before, batter in enumerate((101, 102, 103)):
                play = _play(
                    at_bat_index,
                    batter=batter,
                    events=[
                        _pitch(
                            0,
                            description="In play, out(s)",
                            balls=0,
                            strikes=0,
                            outs=outs_before,
                            pitch_number=1,
                        )
                    ],
                    terminal="field_out",
                    outs_after=outs_before + 1,
                    away_score=0,
                    home_score=0,
                    runners=[
                        _runner(
                            batter,
                            start=None,
                            end=None,
                            is_out=True,
                            out_number=outs_before + 1,
                        )
                    ],
                    inning=inning,
                    is_top=is_top,
                )
                play["result"]["isOut"] = True
                plays.append(play)
                at_bat_index += 1

    top_placement = _placement_action(104, away_score=0, home_score=0)
    top_first = _play(
        at_bat_index,
        batter=101,
        events=[
            top_placement,
            _pitch(
                1,
                description="In play, out(s)",
                balls=0,
                strikes=0,
                outs=0,
                pitch_number=1,
            ),
        ],
        terminal="field_out",
        outs_after=1,
        away_score=0,
        runners=[
            _runner(
                101,
                start=None,
                end=None,
                is_out=True,
                out_number=1,
                play_index=1,
            )
        ],
        inning=10,
        is_top=True,
        post_runners={"2B": 104},
    )
    top_first["result"]["isOut"] = True
    plays.append(top_first)
    at_bat_index += 1
    for outs_before, batter in ((1, 102), (2, 103)):
        play = _play(
            at_bat_index,
            batter=batter,
            events=[
                _pitch(
                    0,
                    description="In play, out(s)",
                    balls=0,
                    strikes=0,
                    outs=outs_before,
                    pitch_number=1,
                )
            ],
            terminal="field_out",
            outs_after=outs_before + 1,
            away_score=0,
            runners=[
                _runner(
                    batter,
                    start=None,
                    end=None,
                    is_out=True,
                    out_number=outs_before + 1,
                )
            ],
            inning=10,
            is_top=True,
            post_runners={"2B": 104} if outs_before == 1 else None,
        )
        play["result"]["isOut"] = True
        plays.append(play)
        at_bat_index += 1

    bottom_placement = _placement_action(105, away_score=0, home_score=0)
    walkoff = _play(
        at_bat_index,
        batter=101,
        events=[
            bottom_placement,
            _pitch(
                1,
                description="In play, no out",
                balls=0,
                strikes=0,
                outs=0,
                pitch_number=1,
            ),
        ],
        terminal="single",
        outs_after=0,
        away_score=0,
        home_score=1,
        runners=[
            _runner(101, start=None, end="1B", play_index=1),
            _runner(
                105,
                start="2B",
                end="score",
                scoring=True,
                play_index=1,
            ),
        ],
        rbi=1,
        inning=10,
        is_top=False,
        post_runners={"1B": 101},
    )
    walkoff["result"]["isOut"] = False
    plays.append(walkoff)
    return _feed(plays)


def _runner_edge_feed() -> dict[str, object]:
    double = _play(
        0,
        batter=101,
        events=[
            _pitch(
                0,
                description="In play, no out",
                balls=0,
                strikes=0,
                outs=0,
                pitch_number=1,
            )
        ],
        terminal="double",
        outs_after=0,
        away_score=0,
        runners=[_runner(101, start=None, end="2B")],
        post_runners={"2B": 101},
    )
    double["result"]["isOut"] = False

    dropped_events = [
        _pitch(
            0,
            description="Called Strike",
            balls=0,
            strikes=1,
            outs=0,
            pitch_number=1,
        ),
        _pitch(
            1,
            description="Foul",
            balls=0,
            strikes=2,
            outs=0,
            pitch_number=2,
        ),
        _pitch(
            2,
            description="Swinging Strike",
            balls=0,
            strikes=3,
            outs=0,
            pitch_number=3,
        ),
    ]
    dropped_events[-1]["details"].update({"isStrike": True, "isOut": False})
    placeholder = {
        "details": {
            "event": "Strikeout",
            "eventType": "strikeout",
            "isScoringEvent": False,
            "playIndex": 2,
            "runner": {"id": 102},
        },
        "movement": {
            "end": None,
            "isOut": None,
            "originBase": None,
            "outBase": None,
            "outNumber": None,
            "start": None,
        },
    }
    batter_safe = _runner(102, start=None, end="1B", play_index=2)
    batter_safe["details"].update(
        {"event": "Passed Ball", "eventType": "passed_ball"}
    )
    dropped = _play(
        1,
        batter=102,
        events=dropped_events,
        terminal="strikeout",
        outs_after=0,
        away_score=0,
        runners=[
            _runner(101, start="2B", end="3B", play_index=2),
            placeholder,
            batter_safe,
        ],
        post_runners={"1B": 102, "3B": 101},
    )
    dropped["result"]["isOut"] = False

    pinch_action = _pinch_runner_action(104, 102)
    pinch_play = _play(
        2,
        batter=103,
        events=[
            pinch_action,
            _pitch(
                1,
                description="In play, out(s)",
                balls=0,
                strikes=0,
                outs=0,
                pitch_number=1,
            ),
        ],
        terminal="field_out",
        outs_after=1,
        away_score=0,
        runners=[
            _runner(
                103,
                start=None,
                end=None,
                is_out=True,
                out_number=1,
                play_index=1,
            )
        ],
        post_runners={"1B": 104, "3B": 101},
    )
    pinch_play["result"]["isOut"] = True
    return _feed([double, dropped, pinch_play])


def _statcast_rows_for_game(game) -> list[dict[str, object]]:
    description_map = {
        "Called Strike": ("called_strike", "S"),
        "Foul": ("foul", "S"),
        "In play, no out": ("hit_into_play", "X"),
        "In play, out(s)": ("hit_into_play", "X"),
        "Swinging Strike": ("swinging_strike", "S"),
    }
    pa_by_number = {pa.at_bat_number: pa for pa in game.plate_appearances}
    events_by_pa: dict[int, list] = {}
    for event in game.count_events:
        events_by_pa.setdefault(event.at_bat_number, []).append(event)
    rows: list[dict[str, object]] = []
    for at_bat_number, events in sorted(events_by_pa.items()):
        pa = pa_by_number[at_bat_number]
        ordered = sorted(events, key=lambda event: event.canonical_sequence)
        for position, event in enumerate(ordered):
            description, source_type = description_map[event.source_description]
            runners = {runner.base: runner.source_id for runner in event.runner_state_before}
            rows.append({
                "game_pk": game.source_game_id,
                "at_bat_number": str(at_bat_number),
                "pitch_number": str(event.canonical_sequence),
                "batter": pa.batter_source_id,
                "pitcher": pa.pitcher_source_id,
                "inning": str(pa.inning),
                "inning_topbot": "Top" if pa.inning_half == "TOP" else "Bot",
                "outs_when_up": str(event.outs),
                "balls": str(event.balls_before),
                "strikes": str(event.strikes_before),
                "stand": pa.batter_hand,
                "p_throws": pa.pitcher_hand,
                "description": description,
                "type": source_type,
                "events": (
                    pa.terminal_source_token if position == len(ordered) - 1 else ""
                ),
                "on_1b": runners.get("1B", ""),
                "on_2b": runners.get("2B", ""),
                "on_3b": runners.get("3B", ""),
            })
    return rows


def test_taxonomy_contract_has_exact_eight_classes_and_stable_source_mapping() -> None:
    assert PA_OUTCOME_TAXONOMY_VERSION == "mlb_gen2_pa_outcome_taxonomy_v1"
    assert len(PA_OUTCOME_TAXONOMY_IDENTITY) == 64
    assert ordered_outcomes() == tuple(PlateAppearanceOutcome)
    assert tuple(outcome.value for outcome in ordered_outcomes()) == (
        "STRIKEOUT",
        "WALK_HBP_CI",
        "HOME_RUN",
        "SINGLE",
        "DOUBLE",
        "TRIPLE",
        "BIP_OUT",
        "OTHER_REACH",
    )
    assert SOURCE_TOKEN_TO_OUTCOME["catcher_interf"] is PlateAppearanceOutcome.WALK_HBP_CI
    assert SOURCE_TOKEN_TO_OUTCOME["grounded_into_double_play"] is (
        PlateAppearanceOutcome.BIP_OUT
    )
    assert set(RETAINED_2025_06_17_CATEGORY_COUNTS) == set(ordered_outcomes())


def test_retained_taxonomy_counts_require_exact_population_and_proven_exclusion() -> None:
    representative_token = {
        outcome: next(token for token, mapped in SOURCE_TOKEN_TO_OUTCOME.items()
                      if mapped is outcome)
        for outcome in ordered_outcomes()
    }
    evidence = TruncatedPlateAppearanceEvidence(
        mlb_result_token="caught_stealing_2b",
        official_game_final=True,
        play_complete=True,
        outs_before=2,
        outs_after=3,
        runner_out_recorded=True,
        batter_has_terminal_result=False,
    )
    observations = list(chain.from_iterable(
        [representative_token[outcome]] * count
        for outcome, count in RETAINED_2025_06_17_CATEGORY_COUNTS.items()
    ))
    observations.append(TaxonomyObservation("truncated_pa", evidence))
    report = validate_retained_2025_06_17_counts(observations)
    assert report.modeled_count == 1_139
    assert report.excluded_truncated_pa_count == 1
    assert report.total_observation_count == 1_140

    with pytest.raises(TaxonomyValidationError, match="requires exact official MLB"):
        validate_retained_2025_06_17_counts([*observations[:-1], "truncated_pa"])
    with pytest.raises(TaxonomyValidationError, match="counts differ"):
        validate_retained_2025_06_17_counts(observations[:-2] + observations[-1:])
    with pytest.raises(TaxonomyValidationError, match="Unmapped"):
        validate_retained_2025_06_17_counts([*observations[:-1], "mystery_result"])


def test_mlb_pbp_builds_score_base_out_transitions_only_from_runner_movements() -> None:
    game = _canonicalize(_single_double_out_plays())
    assert [row.outcome for row in game.plate_appearances] == [
        PlateAppearanceOutcome.SINGLE,
        PlateAppearanceOutcome.DOUBLE,
        PlateAppearanceOutcome.BIP_OUT,
    ]
    first, second, third = game.plate_appearances
    assert first.schema_version == PLATE_APPEARANCE_SCHEMA_VERSION
    assert first.state_before.base_mask == 0
    assert first.state_after.base_mask == 1
    assert second.state_before.base_mask == 1
    assert second.state_before.away_score == 0
    assert second.state_after.base_mask == 2
    assert second.state_after.away_score == 1
    assert second.runs_scored == 1
    assert third.state_before.base_mask == 2
    assert third.state_after.base_mask == 2
    assert third.state_after.outs == 1
    assert all(len(row.native_identity_checksum) == 64 for row in game.plate_appearances)
    assert all(len(row.canonical_identity_checksum) == 64 for row in game.plate_appearances)
    assert all(len(row.content_checksum) == 64 for row in game.plate_appearances)

    reordered = deepcopy(_feed(_single_double_out_plays()))
    reordered = {key: reordered[key] for key in reversed(tuple(reordered))}
    repeated = canonicalize_mlb_play_by_play(
        reordered,
        source_version_id="mlb-feed-v1",
        canonical_game_id="canonical-game-777001",
        canonical_player_ids=PLAYERS,
        canonical_team_ids=TEAMS,
    )
    assert repeated.content_checksum == game.content_checksum


def test_pbp_fails_closed_on_score_conflict_unmapped_terminal_and_leakage_fields() -> None:
    conflict = _single_double_out_plays()
    conflict[1]["result"]["awayScore"] = 9
    with pytest.raises(CanonicalEventError, match="runner scoring"):
        _canonicalize(conflict)

    unmapped = _single_double_out_plays()
    unmapped[0]["result"]["eventType"] = "unsupported_reach"
    with pytest.raises(CanonicalEventError, match="Unmapped"):
        _canonicalize(unmapped)

    validate_pregame_feature_fields(["batter_true_talent", "pitcher_whiff_state"])
    with pytest.raises(CanonicalEventError, match="forbidden"):
        validate_pregame_feature_fields(["batter_true_talent", "post_home_score"])
    with pytest.raises(CanonicalEventError, match="forbidden"):
        validate_pregame_feature_fields(["home_win_expectancy"])


def test_count_aware_alignment_includes_automatic_no_pitch_and_retains_both_identities() -> None:
    game, rows = _strikeout_with_automatic_event()
    assert [event.event_kind for event in game.count_events] == [
        CountEventKind.AUTOMATIC_STRIKE,
        CountEventKind.PITCH,
        CountEventKind.PITCH,
        CountEventKind.PITCH,
    ]
    result = align_statcast_to_mlb(
        rows,
        game,
        statcast_source_version_id="savant-detail-v1",
    )
    assert result.plate_appearance_count == 1
    assert result.physical_pitch_count == 3
    assert result.automatic_count_event_count == 1
    assert len(result.aligned_events) == 4
    automatic, ball, swing, terminal = result.aligned_events
    assert automatic.schema_version == PITCH_EVENT_SCHEMA_VERSION
    assert automatic.is_physical_pitch is False
    assert automatic.strikes_before == 0 and automatic.strikes_after == 1
    assert ball.release_velocity == 96.2
    assert ball.ball is True and ball.swing is False
    assert swing.swing is True and swing.whiff is True
    assert terminal.terminal_outcome is PlateAppearanceOutcome.STRIKEOUT
    assert terminal.terminal_source_token == "strikeout"
    assert all(len(row.statcast_native_identity_checksum) == 64 for row in result.aligned_events)
    assert all(len(row.mlb_native_identity_checksum) == 64 for row in result.aligned_events)
    assert all(
        row.canonical_identity_checksum == game.count_events[index].canonical_identity_checksum
        for index, row in enumerate(result.aligned_events)
    )
    # Deliberately conflicting Statcast score columns never populate canonical state.
    assert automatic.pa_state_before.home_score == 0
    assert automatic.pa_state_after.home_score == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "cardinality"),
        ("duplicate", "Duplicate/ambiguous"),
        ("pitcher", "fields conflict"),
        ("count", "fields conflict"),
        ("terminal", "terminal result conflicts"),
        ("description", "descriptions conflict"),
    ],
)
def test_statcast_alignment_fails_closed_on_missing_ambiguous_or_conflicting_rows(
    mutation: str,
    message: str,
) -> None:
    game, rows = _strikeout_with_automatic_event()
    changed = deepcopy(rows)
    if mutation == "missing":
        changed.pop(1)
        for number, row in enumerate(changed, start=1):
            row["pitch_number"] = str(number)
    elif mutation == "duplicate":
        changed.append(deepcopy(changed[-1]))
    elif mutation == "pitcher":
        changed[1]["pitcher"] = "999"
    elif mutation == "count":
        changed[1]["strikes"] = "0"
    elif mutation == "terminal":
        changed[-1]["events"] = "walk"
    elif mutation == "description":
        changed[1]["description"] = "called_strike"
        changed[1]["type"] = "S"
    with pytest.raises(StatcastAlignmentError, match=message):
        align_statcast_to_mlb(
            changed,
            game,
            statcast_source_version_id="savant-detail-v1",
        )


def test_truncated_pa_aligns_only_to_proven_runner_out_ending_the_inning() -> None:
    plays = [
        _play(
            0,
            batter=101,
            events=[_pitch(0, description="In play, out(s)", balls=0, strikes=0, outs=0,
                           pitch_number=1)],
            terminal="field_out",
            outs_after=1,
            away_score=0,
            runners=[_runner(101, start=None, end=None, is_out=True, out_number=1)],
        ),
        _play(
            1,
            batter=102,
            events=[_pitch(0, description="In play, out(s)", balls=0, strikes=0, outs=1,
                           pitch_number=1)],
            terminal="field_out",
            outs_after=2,
            away_score=0,
            runners=[_runner(102, start=None, end=None, is_out=True, out_number=2)],
        ),
        _play(
            2,
            batter=103,
            events=[_pitch(0, description="In play, no out", balls=0, strikes=0, outs=2,
                           pitch_number=1)],
            terminal="single",
            outs_after=2,
            away_score=0,
            runners=[_runner(103, start=None, end="1B")],
        ),
        _play(
            3,
            batter=104,
            events=[_pitch(0, description="Ball", balls=1, strikes=0, outs=2,
                           pitch_number=1)],
            terminal="caught_stealing_2b",
            outs_after=3,
            away_score=0,
            runners=[_runner(103, start="1B", end=None, is_out=True, out_number=3)],
        ),
    ]
    game = _canonicalize(plays)
    truncated = game.plate_appearances[-1]
    assert truncated.outcome is None
    assert truncated.official_early_termination is True
    assert truncated.exclusion_reason == (
        "OFFICIAL_RUNNER_OUT_ENDED_INNING_BEFORE_PA_TERMINAL"
    )
    assert truncated.state_before.base_mask == 1
    assert truncated.state_after.base_mask == 0
    assert truncated.state_after.outs == 3

    statcast_descriptions = ["hit_into_play", "hit_into_play", "hit_into_play", "ball"]
    statcast_events = ["field_out", "field_out", "single", "truncated_pa"]
    rows = []
    for pa, description, terminal_event in zip(
        game.plate_appearances,
        statcast_descriptions,
        statcast_events,
        strict=True,
    ):
        rows.append({
            "game_pk": game.source_game_id,
            "at_bat_number": str(pa.at_bat_number),
            "pitch_number": "1",
            "batter": pa.batter_source_id,
            "pitcher": pa.pitcher_source_id,
            "inning": str(pa.inning),
            "inning_topbot": "Top",
            "outs_when_up": str(pa.state_before.outs),
            "balls": "0",
            "strikes": "0",
            "stand": pa.batter_hand,
            "p_throws": pa.pitcher_hand,
            "description": description,
            "type": "X" if description == "hit_into_play" else "B",
            "events": terminal_event,
            "on_1b": next(
                (runner.source_id for runner in pa.state_before.runners
                 if runner.base == "1B"),
                "",
            ),
            "on_2b": next(
                (runner.source_id for runner in pa.state_before.runners
                 if runner.base == "2B"),
                "",
            ),
            "on_3b": next(
                (runner.source_id for runner in pa.state_before.runners
                 if runner.base == "3B"),
                "",
            ),
        })
    result = align_statcast_to_mlb(
        rows,
        game,
        statcast_source_version_id="savant-detail-v1",
    )
    assert result.truncated_pa_exclusion_count == 1
    assert result.aligned_events[-1].terminal_source_token == "truncated_pa"
    assert result.aligned_events[-1].terminal_outcome is None

    unproven = deepcopy(plays)
    unproven[-1]["count"]["outs"] = 2
    unproven[-1]["runners"][0]["movement"]["outNumber"] = 2
    with pytest.raises(CanonicalEventError):
        _canonicalize(unproven)


def test_extra_inning_runner_placement_is_structural_and_statcast_auditable() -> None:
    feed = _extra_inning_feed()
    game = _canonicalize_feed(feed)
    placements = [
        (pa, evidence)
        for pa in game.plate_appearances
        for evidence in pa.structural_runner_evidence
        if evidence.evidence_type == "HALF_INNING_RUNNER_PLACEMENT"
    ]
    assert len(game.plate_appearances) == 58
    assert len(game.count_events) == 58
    assert len(placements) == 2
    assert {
        (pa.inning_half, evidence.base, evidence.player_source_id)
        for pa, evidence in placements
    } == {("TOP", "2B", "104"), ("BOTTOM", "2B", "105")}
    assert all(
        evidence.contract_version == HALF_INNING_RUNNER_PLACEMENT_VERSION
        for _pa, evidence in placements
    )
    assert all(len(evidence.source_checksum) == 64 for _pa, evidence in placements)
    assert all(len(evidence.content_checksum) == 64 for _pa, evidence in placements)

    top_tenth = next(
        pa
        for pa in game.plate_appearances
        if pa.inning == 10 and pa.inning_half == "TOP"
    )
    assert [runner.as_dict() for runner in top_tenth.state_before.runners] == [{
        "base": "2B",
        "source_id": "104",
        "canonical_id": PLAYERS[104],
    }]
    assert top_tenth.state_before.base_mask == 2

    rows = _statcast_rows_for_game(game)
    result = align_statcast_to_mlb(
        rows,
        game,
        statcast_source_version_id="retained-statcast-fixture-v1",
    )
    assert result.schema_version == STATCAST_ALIGNMENT_SCHEMA_VERSION
    assert result.mlb_game_content_checksum == game.content_checksum
    aligned_top_tenth = next(
        event
        for event in result.aligned_events
        if event.at_bat_number == top_tenth.at_bat_number
    )
    assert aligned_top_tenth.runner_state_before == top_tenth.state_before.runners
    assert aligned_top_tenth.structural_evidence_checksums == tuple(
        evidence.content_checksum for evidence in top_tenth.structural_runner_evidence
    )
    assert aligned_top_tenth.mlb_pa_checksum == top_tenth.content_checksum

    wrong_runner = deepcopy(rows)
    source_row = next(
        row
        for row in wrong_runner
        if int(row["at_bat_number"]) == top_tenth.at_bat_number
    )
    source_row["on_2b"] = "105"
    with pytest.raises(StatcastAlignmentError, match="base_runner_identities"):
        align_statcast_to_mlb(
            wrong_runner,
            game,
            statcast_source_version_id="retained-statcast-fixture-v1",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_base",
        "late_action",
        "score_conflict",
        "batter_identity",
        "wrong_team",
        "duplicate_action",
        "prior_half_incomplete",
        "illegal_progression",
        "final_score_conflict",
    ],
)
def test_extra_inning_runner_placement_contract_fails_closed_nearby(
    mutation: str,
) -> None:
    feed = _extra_inning_feed()
    plays = feed["liveData"]["plays"]["allPlays"]
    top_tenth = next(
        play
        for play in plays
        if play["about"]["inning"] == 10 and play["about"]["isTopInning"] is True
    )
    placement = next(
        event
        for event in top_tenth["playEvents"]
        if event["details"].get("eventType") == "runner_placed"
    )
    if mutation == "wrong_base":
        placement["base"] = 1
    elif mutation == "late_action":
        placement["index"] = 2
    elif mutation == "score_conflict":
        placement["details"]["awayScore"] = 1
    elif mutation == "batter_identity":
        placement["player"]["id"] = top_tenth["matchup"]["batter"]["id"]
    elif mutation == "wrong_team":
        placement["player"]["id"] = 105
    elif mutation == "duplicate_action":
        duplicate = deepcopy(placement)
        duplicate["index"] = 2
        top_tenth["playEvents"].append(duplicate)
    elif mutation == "prior_half_incomplete":
        prior = next(
            play
            for play in reversed(plays)
            if play["about"]["inning"] == 9
            and play["about"]["isTopInning"] is False
        )
        batter = prior["matchup"]["batter"]["id"]
        prior["runners"] = [_runner(batter, start=None, end="1B")]
        prior["count"]["outs"] = 2
        prior["result"].update({"eventType": "single", "isOut": False})
        prior["matchup"]["postOnFirst"] = {"id": batter}
    elif mutation == "illegal_progression":
        top_tenth["about"]["inning"] = 11
    elif mutation == "final_score_conflict":
        feed["liveData"]["linescore"]["teams"]["home"]["runs"] = 9
    with pytest.raises(CanonicalEventError):
        _canonicalize_feed(feed)


def test_retained_runner_edge_shapes_are_explicit_and_identity_preserving() -> None:
    game = _canonicalize_feed(_runner_edge_feed())
    dropped = game.plate_appearances[1]
    pinch = game.plate_appearances[2]
    assert [
        evidence.evidence_type for evidence in dropped.structural_runner_evidence
    ] == [
        "NULL_RUNNER_PLACEHOLDER_SUPPRESSED",
        "DROPPED_THIRD_STRIKE_SAFE_REACH",
    ]
    assert [
        evidence.evidence_type for evidence in pinch.structural_runner_evidence
    ] == ["PINCH_RUNNER_SUBSTITUTION"]
    assert [runner.as_dict() for runner in dropped.state_after.runners] == [
        {"base": "1B", "source_id": "102", "canonical_id": PLAYERS[102]},
        {"base": "3B", "source_id": "101", "canonical_id": PLAYERS[101]},
    ]
    assert [runner.as_dict() for runner in pinch.state_before.runners] == [
        {"base": "1B", "source_id": "104", "canonical_id": PLAYERS[104]},
        {"base": "3B", "source_id": "101", "canonical_id": PLAYERS[101]},
    ]

    rows = _statcast_rows_for_game(game)
    result = align_statcast_to_mlb(
        rows,
        game,
        statcast_source_version_id="retained-edge-fixture-v1",
    )
    pinch_pitch = next(
        event for event in result.aligned_events if event.at_bat_number == 3
    )
    assert pinch_pitch.runner_state_before == pinch.state_before.runners
    assert pinch_pitch.structural_evidence_checksums == tuple(
        evidence.content_checksum for evidence in pinch.structural_runner_evidence
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "placeholder_without_mate",
        "placeholder_with_two_mates",
        "placeholder_scoring",
        "dropped_pitch_is_out",
        "dropped_without_safe_reach_reason",
        "pinch_replaced_identity",
        "pinch_after_pitch",
        "pinch_position",
    ],
)
def test_retained_runner_edge_shapes_fail_closed_outside_exact_evidence(
    mutation: str,
) -> None:
    feed = _runner_edge_feed()
    dropped = feed["liveData"]["plays"]["allPlays"][1]
    pinch = feed["liveData"]["plays"]["allPlays"][2]
    if mutation == "placeholder_without_mate":
        dropped["runners"].pop(2)
    elif mutation == "placeholder_with_two_mates":
        dropped["runners"].append(deepcopy(dropped["runners"][2]))
    elif mutation == "placeholder_scoring":
        dropped["runners"][1]["details"]["isScoringEvent"] = True
    elif mutation == "dropped_pitch_is_out":
        dropped["playEvents"][-1]["details"]["isOut"] = True
    elif mutation == "dropped_without_safe_reach_reason":
        dropped["runners"][2]["details"]["eventType"] = "stolen_base"
    elif mutation == "pinch_replaced_identity":
        pinch["playEvents"][0]["replacedPlayer"]["id"] = 101
    elif mutation == "pinch_after_pitch":
        pinch["playEvents"][0]["index"] = 2
    elif mutation == "pinch_position":
        pinch["playEvents"][0]["position"]["code"] = "11"
    with pytest.raises(CanonicalEventError):
        _canonicalize_feed(feed)


@pytest.mark.parametrize(
    ("role", "event_type", "position_code"),
    [
        ("batter", "offensive_substitution", "11"),
        ("pitcher", "pitching_substitution", "1"),
    ],
)
def test_mid_pa_batter_and_pitcher_substitutions_are_unsupported(
    role: str,
    event_type: str,
    position_code: str,
) -> None:
    plays = _single_double_out_plays()
    player_id = 101 if role == "batter" else 201
    plays[0]["playEvents"].append({
        "count": {"balls": 0, "strikes": 0, "outs": 0},
        "details": {"eventType": event_type},
        "index": 1,
        "isPitch": False,
        "isSubstitution": True,
        "player": {"id": player_id},
        "position": {"code": position_code},
        "type": "action",
    })
    with pytest.raises(CanonicalEventError, match=f"mid-PA {role} substitution"):
        _canonicalize(plays)


def test_external_to_canonical_targets_are_globally_injective() -> None:
    feed = _feed(_single_double_out_plays())
    duplicate_players = dict(PLAYERS)
    duplicate_players[102] = duplicate_players[101]
    with pytest.raises(CanonicalEventError, match="Ambiguous canonical player"):
        _canonicalize_feed(feed, players=duplicate_players)

    overlapping_teams = dict(TEAMS)
    overlapping_teams[10] = PLAYERS[101]
    with pytest.raises(CanonicalEventError, match="must be disjoint"):
        _canonicalize_feed(feed, teams=overlapping_teams)

    with pytest.raises(CanonicalEventError, match="must be disjoint"):
        _canonicalize_feed(feed, canonical_game_id=PLAYERS[101])

    source_play = _play(
        0,
        batter=101,
        events=[
            _pitch(
                0,
                description="In play, out(s)",
                balls=0,
                strikes=0,
                outs=0,
                pitch_number=1,
            )
        ],
        terminal="field_out",
        outs_after=1,
        away_score=0,
        runners=[
            _runner(101, start=None, end=None, is_out=True, out_number=1)
        ],
    )
    source_game = _canonicalize_feed(
        _feed([source_play]),
        players={101: "same-batter", 201: "same-pitcher"},
    )
    changed_source_play = deepcopy(source_play)
    changed_source_play["matchup"]["batter"]["id"] = 102
    changed_source_play["runners"][0]["details"]["runner"]["id"] = 102
    changed_source_game = _canonicalize_feed(
        _feed([changed_source_play]),
        players={102: "same-batter", 201: "same-pitcher"},
    )
    assert (
        changed_source_game.count_events[0].content_checksum
        != source_game.count_events[0].content_checksum
    )

    changed_team_feed = _feed([deepcopy(source_play)])
    changed_team_feed["gameData"]["teams"] = {
        "home": {"id": 30},
        "away": {"id": 40},
    }
    changed_team_game = _canonicalize_feed(
        changed_team_feed,
        players={101: "same-batter", 201: "same-pitcher"},
        teams={30: "team-home", 40: "team-away"},
    )
    assert (
        changed_team_game.plate_appearances[0].content_checksum
        != source_game.plate_appearances[0].content_checksum
    )


@pytest.mark.parametrize("mutation", ["missing_field", "wrong_base", "duplicate_runner"])
def test_statcast_base_runner_identities_fail_closed(mutation: str) -> None:
    game = _canonicalize_feed(_runner_edge_feed())
    rows = _statcast_rows_for_game(game)
    target = next(
        row
        for row in rows
        if row["at_bat_number"] == "2" and row["pitch_number"] == "1"
    )
    if mutation == "missing_field":
        target.pop("on_2b")
    elif mutation == "wrong_base":
        target["on_2b"] = "104"
    elif mutation == "duplicate_runner":
        target["on_1b"] = target["on_2b"]
    with pytest.raises(StatcastAlignmentError):
        align_statcast_to_mlb(
            rows,
            game,
            statcast_source_version_id="retained-edge-fixture-v1",
        )


def test_alignment_checksums_bind_pa_states_runner_identities_and_evidence() -> None:
    game = _canonicalize_feed(_extra_inning_feed())
    rows = _statcast_rows_for_game(game)
    baseline = align_statcast_to_mlb(
        rows,
        game,
        statcast_source_version_id="retained-statcast-fixture-v1",
    )
    target_pa = next(
        pa
        for pa in game.plate_appearances
        if pa.inning == 10 and pa.inning_half == "TOP"
    )
    target_event = next(
        event
        for event in game.count_events
        if event.at_bat_number == target_pa.at_bat_number
    )
    tampered_runner = replace(
        target_pa.state_before.runners[0], canonical_id="tampered-runner-id"
    )
    tampered_before = replace(target_pa.state_before, runners=(tampered_runner,))
    tampered_after = replace(target_pa.state_after, runners=(tampered_runner,))
    tampered_pa = replace(
        target_pa,
        state_before=tampered_before,
        state_after=tampered_after,
    )
    tampered_event = replace(
        target_event,
        runner_state_before=(tampered_runner,),
    )
    tampered_game = replace(
        game,
        plate_appearances=tuple(
            tampered_pa if pa is target_pa else pa for pa in game.plate_appearances
        ),
        count_events=tuple(
            tampered_event if event is target_event else event
            for event in game.count_events
        ),
    )
    state_changed = align_statcast_to_mlb(
        rows,
        tampered_game,
        statcast_source_version_id="retained-statcast-fixture-v1",
    )
    baseline_event = next(
        event
        for event in baseline.aligned_events
        if event.at_bat_number == target_pa.at_bat_number
    )
    changed_event = next(
        event
        for event in state_changed.aligned_events
        if event.at_bat_number == target_pa.at_bat_number
    )
    assert changed_event.content_checksum != baseline_event.content_checksum
    assert state_changed.content_checksum != baseline.content_checksum

    evidence_event = replace(
        target_event,
        structural_evidence_checksums=("f" * 64,),
    )
    evidence_game = replace(
        game,
        count_events=tuple(
            evidence_event if event is target_event else event
            for event in game.count_events
        ),
    )
    evidence_changed = align_statcast_to_mlb(
        rows,
        evidence_game,
        statcast_source_version_id="retained-statcast-fixture-v1",
    )
    evidence_aligned = next(
        event
        for event in evidence_changed.aligned_events
        if event.at_bat_number == target_pa.at_bat_number
    )
    assert evidence_aligned.content_checksum != baseline_event.content_checksum
    assert evidence_changed.content_checksum != baseline.content_checksum
