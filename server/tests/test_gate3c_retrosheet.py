from __future__ import annotations

import csv
from datetime import date
import io

import pytest

from app.gen2.event_taxonomy import PlateAppearanceOutcome
from app.gen2.retrosheet import (
    AUTOMATIC_RUNNER_AFTER_NP_BRIDGE,
    AUTOMATIC_RUNNER_DIRECT_FIRST_PLAY,
    BATTING_HEADER,
    HISTORICAL_ACTUAL_LINEUP_TIMING_RISK,
    HISTORICAL_ACTUAL_RELIEVER_TIMING_RISK,
    HISTORICAL_ACTUAL_STARTER_TIMING_RISK,
    RETROSHEET_CSV_HEADERS,
    RETROSPECTIVE_EVENT_TIMING_RISK,
    RetrosheetImportError,
    RetrosheetPbpQuality,
    RetrosheetPitcherRole,
    RetrosheetStatSemantics,
    classify_retrosheet_plate_appearance,
    parse_retrosheet_bundle,
    parse_retrosheet_csv,
)


VISITORS = tuple(f"vply{letter}{number:03d}" for number, letter in enumerate("abcdefghi", 1))
HOME = tuple(f"hply{letter}{number:03d}" for number, letter in enumerate("abcdefghi", 1))
VIS_PITCHERS = ("vpitj010", "vpitk011")
HOME_PITCHERS = ("hpitj010", "hpitk011")
VIS_BENCH = ("vbncl012", "vbncm013")


def _row(filename: str, **values: object) -> dict[str, str]:
    row = {column: "" for column in RETROSHEET_CSV_HEADERS[filename]}
    unknown = set(values) - set(row)
    assert not unknown
    row.update({key: str(value) for key, value in values.items()})
    return row


def _csv(filename: str, rows: list[dict[str, str]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=RETROSHEET_CSV_HEADERS[filename],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _mutate(
    files: dict[str, str], filename: str, row_index: int, column: str, value: str
) -> dict[str, str]:
    rows = list(csv.DictReader(io.StringIO(files[filename])))
    rows[row_index][column] = value
    changed = dict(files)
    changed[filename] = _csv(filename, rows)
    return changed


def _roster_rows() -> list[dict[str, str]]:
    rows = []
    for team, players in (
        ("VIS", VISITORS + VIS_PITCHERS + VIS_BENCH),
        ("HOM", HOME + HOME_PITCHERS),
    ):
        for player in players:
            rows.append(
                _row(
                    "allplayers.csv",
                    id=player,
                    last="Fixture",
                    first=player,
                    bat="R",
                    throw="R",
                    team=team,
                    season=2025,
                )
            )
    return rows


def _game_row(number: int, *, pbp: str, vruns: int, hruns: int) -> dict[str, str]:
    gid = f"HOM20250401{number}"
    return _row(
        "gameinfo.csv",
        gid=gid,
        visteam="VIS",
        hometeam="HOM",
        site="TEST1",
        date="2025-04-01",
        number=number,
        innings=9,
        tiebreaker="",
        usedh="y",
        htbf="",
        suspend="",
        gametype="regular",
        vruns=vruns,
        hruns=hruns,
        wteam="VIS" if vruns > hruns else "HOM",
        lteam="HOM" if vruns > hruns else "VIS",
        line="y",
        batteries="both",
        lineups="y",
        box="y",
        pbp=pbp,
        season=2025,
    )


def _team_row(
    number: int,
    team: str,
    players: tuple[str, ...],
    pitchers: tuple[str, ...],
    *,
    runs: int,
    pbp: str,
    stattype: str = "value",
) -> dict[str, str]:
    opponent = "HOM" if team == "VIS" else "VIS"
    row = _row(
        "teamstats.csv",
        gid=f"HOM20250401{number}",
        team=team,
        inn1=runs,
        stattype=stattype,
        b_r=runs,
        date="2025-04-01",
        number=number,
        site="TEST1",
        vishome="v" if team == "VIS" else "h",
        opp=opponent,
        win=int((team == "VIS") == (runs > 0)),
        loss=int((team == "HOM") == (runs == 0)),
        tie=0,
        gametype="regular",
        box="y",
        pbp=pbp,
    )
    for position, player in enumerate(players, 1):
        row[f"start_l{position}"] = player
    row["start_f1"] = pitchers[0]
    for position, player in zip(range(2, 10), players[:8], strict=True):
        row[f"start_f{position}"] = player
    row["start_f10"] = players[8]
    return row


def _pitching_rows(
    number: int,
    team: str,
    pitchers: tuple[str, ...],
    *,
    pbp: str,
) -> list[dict[str, str]]:
    opponent = "HOM" if team == "VIS" else "VIS"
    result = []
    for sequence, player in enumerate(pitchers, 1):
        result.append(
            _row(
                "pitching.csv",
                gid=f"HOM20250401{number}",
                id=player,
                team=team,
                p_seq=sequence,
                stattype="value",
                p_gs=int(sequence == 1),
                date="2025-04-01",
                number=number,
                site="TEST1",
                vishome="v" if team == "VIS" else "h",
                opp=opponent,
                win=int(team == "VIS"),
                loss=int(team == "HOM"),
                tie=0,
                gametype="regular",
                box="y",
                pbp=pbp,
            )
        )
    return result


def _batting_row(
    player: str,
    *,
    lineup_position: int,
    sequence: int,
    pr: str = "",
) -> dict[str, str]:
    return _row(
        "batting.csv",
        gid="HOM202504011",
        id=player,
        team="VIS",
        b_lp=lineup_position,
        b_seq=sequence,
        stattype="value",
        pr=pr,
        date="2025-04-01",
        number=1,
        site="TEST1",
        vishome="v",
        opp="HOM",
        win=1,
        loss=0,
        tie=0,
        gametype="regular",
        box="y",
        pbp="y",
    )


def _play_row(
    play_number: int,
    *,
    batter: str,
    event: str,
    terminal: str,
    br1_pre: str = "",
    br1_post: str = "",
    br2_pre: str = "",
    br2_post: str = "",
    run_b: str = "",
    run1: str = "",
    run2: str = "",
    runs: int = 0,
) -> dict[str, str]:
    values: dict[str, object] = {
        "gid": "HOM202504011",
        "event": event,
        "inning": 1,
        "top_bot": 0,
        "vis_home": 0,
        "site": "TEST1",
        "batteam": "VIS",
        "pitteam": "HOM",
        "score_v": 0,
        "score_h": 0,
        "batter": batter,
        "pitcher": HOME_PITCHERS[0],
        "lp": play_number,
        "bat_f": 10,
        "bathand": "R",
        "pithand": "R",
        "balls": 0,
        "strikes": 0,
        "count": "00",
        "nump": 1,
        "pa": 1,
        "ab": 1,
        "outs_pre": 0,
        "outs_post": 0,
        "br1_pre": br1_pre,
        "br1_post": br1_post,
        "br2_pre": br2_pre,
        "br2_post": br2_post,
        "run_b": run_b,
        "run1": run1,
        "run2": run2,
        "runs": runs,
        "rbi": runs,
        "er": runs,
        "tur": 0,
        "pn": play_number,
        "date": "2025-04-01",
        "gametype": "regular",
        "pbp": "full",
    }
    values.update({column: int(column == terminal) for column in (
        "single", "double", "triple", "hr", "sh", "sf", "hbp", "walk", "k", "xi",
        "roe", "fc", "othout", "noout",
    )})
    return _row("plays.csv", **values)


@pytest.fixture
def synthetic_bundle_files() -> dict[str, str]:
    games = [
        _game_row(1, pbp="y", vruns=2, hruns=0),
        _game_row(2, pbp="", vruns=1, hruns=0),
    ]
    team_rows = [
        _team_row(1, "VIS", VISITORS, VIS_PITCHERS, runs=2, pbp="y"),
        _team_row(1, "HOM", HOME, HOME_PITCHERS, runs=0, pbp="y"),
        _team_row(2, "VIS", VISITORS, VIS_PITCHERS, runs=1, pbp=""),
        _team_row(2, "HOM", HOME, HOME_PITCHERS, runs=0, pbp=""),
    ]
    pitching = []
    for number, pbp in ((1, "y"), (2, "")):
        pitching.extend(_pitching_rows(number, "VIS", VIS_PITCHERS, pbp=pbp))
        pitching.extend(_pitching_rows(number, "HOM", HOME_PITCHERS, pbp=pbp))
    plays = [
        _play_row(
            1,
            batter=VISITORS[0],
            event="S7",
            terminal="single",
            br1_post=VISITORS[0],
        ),
        _play_row(
            2,
            batter=VISITORS[1],
            event="HR/F",
            terminal="hr",
            br1_pre=VISITORS[0],
            run_b=VISITORS[1],
            run1=VISITORS[0],
            runs=2,
        ),
    ]
    return {
        "allplayers.csv": _csv("allplayers.csv", _roster_rows()),
        "gameinfo.csv": _csv("gameinfo.csv", games),
        "teamstats.csv": _csv("teamstats.csv", team_rows),
        "batting.csv": _csv("batting.csv", []),
        "pitching.csv": _csv("pitching.csv", pitching),
        "fielding.csv": _csv("fielding.csv", []),
        "plays.csv": _csv("plays.csv", plays),
    }


def _with_pinch_runner_continuity(
    files: dict[str, str],
) -> dict[str, str]:
    changed = dict(files)
    changed["batting.csv"] = _csv(
        "batting.csv",
        [
            _batting_row(VISITORS[0], lineup_position=5, sequence=1),
            _batting_row(VIS_BENCH[0], lineup_position=5, sequence=2, pr="1"),
        ],
    )
    changed = _mutate(changed, "plays.csv", 1, "br1_pre", VIS_BENCH[0])
    return _mutate(changed, "plays.csv", 1, "run1", VIS_BENCH[0])


def _with_simultaneous_pinch_runner_continuity(
    files: dict[str, str],
) -> dict[str, str]:
    changed = dict(files)
    changed["batting.csv"] = _csv(
        "batting.csv",
        [
            _batting_row(VISITORS[0], lineup_position=5, sequence=1),
            _batting_row(VIS_BENCH[0], lineup_position=5, sequence=2, pr="1"),
            _batting_row(VISITORS[1], lineup_position=6, sequence=1),
            _batting_row(VIS_BENCH[1], lineup_position=6, sequence=2, pr="1"),
        ],
    )
    changed["plays.csv"] = _csv(
        "plays.csv",
        [
            _play_row(
                1,
                batter=VISITORS[0],
                event="S7",
                terminal="single",
                br1_post=VISITORS[0],
            ),
            _play_row(
                2,
                batter=VISITORS[1],
                event="S7.1-2",
                terminal="single",
                br1_pre=VISITORS[0],
                br1_post=VISITORS[1],
                br2_post=VISITORS[0],
            ),
            _play_row(
                3,
                batter=VISITORS[2],
                event="HR/F",
                terminal="hr",
                br1_pre=VIS_BENCH[1],
                br2_pre=VIS_BENCH[0],
                run_b=VISITORS[2],
                run1=VIS_BENCH[1],
                run2=VIS_BENCH[0],
                runs=3,
            ),
        ],
    )
    changed = _mutate(changed, "gameinfo.csv", 0, "vruns", "3")
    changed = _mutate(changed, "teamstats.csv", 0, "b_r", "3")
    return _mutate(changed, "teamstats.csv", 0, "inn1", "3")


def _with_completed_suspension(
    files: dict[str, str],
    *,
    completion_date: str = "2025-08-31",
    completion_site: str = "ALT01",
) -> dict[str, str]:
    changed = _mutate(files, "gameinfo.csv", 0, "suspend", completion_date)
    changed = _mutate(changed, "plays.csv", 1, "date", completion_date)
    return _mutate(changed, "plays.csv", 1, "site", completion_site)


def _extra_inning_play(
    play_number: int,
    *,
    inning: int,
    top_bottom: int,
    outs_post: int,
    event: str,
    pa: int,
    terminal: str | None = None,
    runner_base: int | None = None,
    second_runner_base: int | None = None,
) -> dict[str, str]:
    batting_team = "VIS" if top_bottom == 0 else "HOM"
    pitching_team = "HOM" if top_bottom == 0 else "VIS"
    batter = VISITORS[0] if top_bottom == 0 else HOME[0]
    pitcher = HOME_PITCHERS[0] if top_bottom == 0 else VIS_PITCHERS[0]
    values: dict[str, object] = {
        "gid": "HOM202504011",
        "event": event,
        "inning": inning,
        "top_bot": top_bottom,
        "vis_home": top_bottom,
        "site": "TEST1",
        "batteam": batting_team,
        "pitteam": pitching_team,
        "score_v": 0,
        "score_h": 0,
        "batter": batter,
        "pitcher": pitcher,
        "lp": play_number,
        "bat_f": 10,
        "bathand": "R",
        "pithand": "R",
        "balls": 0,
        "strikes": 0,
        "count": "00",
        "pitches": "" if pa == 0 else "S",
        "nump": pa,
        "pa": pa,
        "ab": pa,
        "outs_pre": 0,
        "outs_post": outs_post,
        "runs": 0,
        "rbi": 0,
        "er": 0,
        "tur": 0,
        "pn": play_number,
        "date": "2025-04-01",
        "gametype": "regular",
        "pbp": "full",
    }
    for column in (
        "single", "double", "triple", "hr", "sh", "sf", "hbp", "walk", "k",
        "xi", "roe", "fc", "othout", "noout",
    ):
        values[column] = int(column == terminal)
    if runner_base is not None:
        values[f"br{runner_base}_pre"] = VISITORS[8]
        values[f"br{runner_base}_post"] = VISITORS[8]
    if second_runner_base is not None:
        values[f"br{second_runner_base}_pre"] = VISITORS[7]
        values[f"br{second_runner_base}_post"] = VISITORS[7]
    return _row("plays.csv", **values)


def _with_automatic_runner(
    files: dict[str, str],
    *,
    np_bridge: bool,
) -> dict[str, str]:
    changed = _mutate(files, "gameinfo.csv", 0, "vruns", "0")
    changed = _mutate(changed, "gameinfo.csv", 0, "hruns", "0")
    changed = _mutate(changed, "gameinfo.csv", 0, "tiebreaker", "2")
    for row_index in (0, 1):
        changed = _mutate(changed, "teamstats.csv", row_index, "b_r", "0")
        changed = _mutate(changed, "teamstats.csv", row_index, "inn1", "0")
    plays = [
        _extra_inning_play(
            (inning - 1) * 2 + top_bottom + 1,
            inning=inning,
            top_bottom=top_bottom,
            outs_post=3,
            event="OUT",
            pa=1,
            terminal="othout",
        )
        for inning in range(1, 10)
        for top_bottom in (0, 1)
    ]
    next_play = 19
    if np_bridge:
        plays.append(
            _extra_inning_play(
                next_play,
                inning=10,
                top_bottom=0,
                outs_post=0,
                event="NP",
                pa=0,
            )
        )
        next_play += 1
    plays.append(
        _extra_inning_play(
            next_play,
            inning=10,
            top_bottom=0,
            outs_post=1,
            event="K",
            pa=1,
            terminal="k",
            runner_base=2,
        )
    )
    changed["plays.csv"] = _csv("plays.csv", plays)
    return changed


def _with_zero_stat_suspended_lineup_proof(
    files: dict[str, str],
) -> dict[str, str]:
    changed = _with_completed_suspension(files)
    changed = _mutate(changed, "teamstats.csv", 0, "start_f9", "")
    zero_stat = _batting_row(VISITORS[7], lineup_position=8, sequence=1)
    for column in BATTING_HEADER:
        if column.startswith("b_") and column not in {"b_lp", "b_seq"}:
            zero_stat[column] = "0"
    changed["batting.csv"] = _csv("batting.csv", [zero_stat])
    changed = _mutate(changed, "plays.csv", 0, "l8", VISITORS[7])
    changed = _mutate(changed, "plays.csv", 0, "lf8", "2")
    changed = _mutate(changed, "plays.csv", 1, "l8", VIS_BENCH[0])
    return _mutate(changed, "plays.csv", 1, "lf8", "11")


def _with_zero_stat_home_defensive_proof(
    files: dict[str, str],
) -> dict[str, str]:
    changed = _mutate(files, "teamstats.csv", 1, "start_f9", "")
    zero_stat = _row(
        "batting.csv",
        gid="HOM202504011",
        id=HOME[7],
        team="HOM",
        b_lp=8,
        b_seq=1,
        stattype="value",
        date="2025-04-01",
        number=1,
        site="TEST1",
        vishome="h",
        opp="VIS",
        win=0,
        loss=1,
        tie=0,
        gametype="regular",
        box="y",
        pbp="y",
    )
    for column in BATTING_HEADER:
        if column.startswith("b_") and column not in {"b_lp", "b_seq"}:
            zero_stat[column] = "0"
    changed["batting.csv"] = _csv("batting.csv", [zero_stat])
    return _mutate(changed, "plays.csv", 0, "f5", HOME[7])


def test_all_seven_official_header_contracts_are_exact_and_ordered() -> None:
    assert tuple(RETROSHEET_CSV_HEADERS) == (
        "allplayers.csv",
        "gameinfo.csv",
        "teamstats.csv",
        "batting.csv",
        "pitching.csv",
        "fielding.csv",
        "plays.csv",
    )
    assert {name: len(header) for name, header in RETROSHEET_CSV_HEADERS.items()} == {
        "allplayers.csv": 25,
        "gameinfo.csv": 43,
        "teamstats.csv": 111,
        "batting.csv": 39,
        "pitching.csv": 42,
        "fielding.csv": 28,
        "plays.csv": 177,
    }
    for filename, header in RETROSHEET_CSV_HEADERS.items():
        assert len(header) == len(set(header))
        parse_retrosheet_csv(filename, ",".join(header) + "\n")
        reordered = (header[1], header[0], *header[2:])
        with pytest.raises(RetrosheetImportError, match="header contract mismatch"):
            parse_retrosheet_csv(filename, ",".join(reordered) + "\n")


def test_bundle_derives_doubleheader_lineups_pitcher_roles_and_score_state(
    synthetic_bundle_files: dict[str, str],
) -> None:
    bundle = parse_retrosheet_bundle(
        synthetic_bundle_files,
        source_version_id="daily-logs-2025-fixture-v1",
    )

    assert [game.raw_game_id for game in bundle.games] == ["HOM202504011", "HOM202504012"]
    assert bundle.games[0].source_game_id != bundle.games[1].source_game_id
    assert bundle.games[0].doubleheader_id == bundle.games[1].doubleheader_id
    assert bundle.games[0].doubleheader_id == "retrosheet:doubleheader:HOM:20250401"
    assert len(bundle.lineups) == 36
    assert {entry.timing_risk_label for entry in bundle.lineups} == {
        HISTORICAL_ACTUAL_LINEUP_TIMING_RISK
    }

    first_game_pitchers = [p for p in bundle.pitchers if p.raw_game_id == "HOM202504011"]
    assert [p.role for p in first_game_pitchers if p.team_id == "VIS"] == [
        RetrosheetPitcherRole.STARTER,
        RetrosheetPitcherRole.RELIEVER,
    ]
    assert {p.timing_risk_label for p in first_game_pitchers} == {
        HISTORICAL_ACTUAL_STARTER_TIMING_RISK,
        HISTORICAL_ACTUAL_RELIEVER_TIMING_RISK,
    }

    first, second = bundle.plays
    assert first.pa_outcome is PlateAppearanceOutcome.SINGLE
    assert first.after.base_mask == 1
    assert second.before == first.after
    assert second.pa_outcome is PlateAppearanceOutcome.HOME_RUN
    assert (second.after.visiting_score, second.after.home_score) == (2, 0)
    assert second.source_play_id == "retrosheet:play:HOM202504011:2"
    assert second.batting_source_team_id == "retrosheet:team:VIS"
    assert second.batter_source_player_id == f"retrosheet:player:{VISITORS[1]}"
    assert second.timing_risk_label == RETROSPECTIVE_EVENT_TIMING_RISK
    assert bundle.pinch_runner_continuity_replacement_count == 0
    assert len(bundle.bundle_checksum) == 64
    same = parse_retrosheet_bundle(
        synthetic_bundle_files,
        source_version_id="daily-logs-2025-fixture-v1",
    )
    changed_version = parse_retrosheet_bundle(
        synthetic_bundle_files,
        source_version_id="daily-logs-2025-fixture-v2",
    )
    assert same.bundle_checksum == bundle.bundle_checksum
    assert changed_version.bundle_checksum != bundle.bundle_checksum


def test_stattype_semantics_are_preserved_but_only_value_derives_lineups(
    synthetic_bundle_files: dict[str, str],
) -> None:
    rows = list(csv.DictReader(io.StringIO(synthetic_bundle_files["teamstats.csv"])))
    for stattype in ("official", "lower", "upper"):
        variant = dict(rows[0])
        variant["stattype"] = stattype
        variant["start_l1"] = ""
        rows.append(variant)
    files = dict(synthetic_bundle_files)
    files["teamstats.csv"] = _csv("teamstats.csv", rows)

    bundle = parse_retrosheet_bundle(files, source_version_id="fixture-with-bounds")
    semantics = {
        record.semantics
        for record in bundle.stats
        if record.filename == "teamstats.csv" and record.raw_game_id == "HOM202504011"
    }
    assert semantics == {
        RetrosheetStatSemantics.BEST_ESTIMATE,
        RetrosheetStatSemantics.OFFICIAL_DISCREPANCY,
        RetrosheetStatSemantics.LOWER_BOUND,
        RetrosheetStatSemantics.UPPER_BOUND,
    }
    assert len(bundle.lineups) == 36

    bad = _mutate(synthetic_bundle_files, "teamstats.csv", 0, "stattype", "estimated")
    with pytest.raises(RetrosheetImportError, match="unmapped stattype"):
        parse_retrosheet_bundle(bad, source_version_id="bad-stattype")


def test_two_way_starter_can_hold_pitcher_and_dh_fielding_assignments(
    synthetic_bundle_files: dict[str, str],
) -> None:
    files = _mutate(
        synthetic_bundle_files,
        "teamstats.csv",
        0,
        "start_f1",
        VISITORS[8],
    )
    files = _mutate(files, "pitching.csv", 0, "id", VISITORS[8])

    bundle = parse_retrosheet_bundle(files, source_version_id="two-way-starter")
    two_way_lineup = next(
        entry
        for entry in bundle.lineups
        if entry.raw_game_id == "HOM202504011"
        and entry.team_id == "VIS"
        and entry.player_id == VISITORS[8]
    )
    starter = next(
        pitcher
        for pitcher in bundle.pitchers
        if pitcher.raw_game_id == "HOM202504011"
        and pitcher.team_id == "VIS"
        and pitcher.role is RetrosheetPitcherRole.STARTER
    )
    assert two_way_lineup.fielding_positions == (1, 10)
    assert starter.player_id == VISITORS[8]


def test_positionless_batting_starter_requires_play_participation_proof(
    synthetic_bundle_files: dict[str, str],
) -> None:
    proven = _mutate(
        synthetic_bundle_files,
        "teamstats.csv",
        0,
        "start_f2",
        "",
    )
    bundle = parse_retrosheet_bundle(proven, source_version_id="pre-fielding-replacement")
    entry = next(
        item
        for item in bundle.lineups
        if item.raw_game_id == "HOM202504011"
        and item.team_id == "VIS"
        and item.player_id == VISITORS[0]
    )
    assert entry.fielding_positions == ()

    unproven = _mutate(
        synthetic_bundle_files,
        "teamstats.csv",
        0,
        "start_f10",
        "",
    )
    with pytest.raises(RetrosheetImportError, match="lacks play-by-play participation proof"):
        parse_retrosheet_bundle(unproven, source_version_id="unproven-lineup")


def test_zero_stat_suspended_starter_position_is_proven_by_original_date_pbp_state(
    synthetic_bundle_files: dict[str, str],
) -> None:
    files = _with_zero_stat_suspended_lineup_proof(synthetic_bundle_files)

    bundle = parse_retrosheet_bundle(files, source_version_id="zero-stat-lineup-proof")

    entry = next(
        item
        for item in bundle.lineups
        if item.raw_game_id == "HOM202504011"
        and item.team_id == "VIS"
        and item.player_id == VISITORS[7]
    )
    assert entry.batting_order == 8
    assert entry.fielding_positions == (2,)
    assert (
        entry.fielding_positions_source
        == "PLAYS_INITIAL_ORIGINAL_DATE_LINEUP_STATE"
    )
    assert entry.position_reconciled_from_play_state is True
    assert entry.teamstats_position_discrepancy is True
    assert entry.position_proof_play_id == "retrosheet:play:HOM202504011:1"
    assert entry.position_proof_event_date == date(2025, 4, 1)
    assert bundle.lineup_position_reconciliation_count == 1
    assert bundle.plays[1].lineup_fielding_positions[7] == 11
    assert bundle.lineup_nonstarting_role_code_occurrence_count == 1


def test_zero_stat_home_starter_is_proven_by_first_defensive_pbp_state(
    synthetic_bundle_files: dict[str, str],
) -> None:
    files = _with_zero_stat_home_defensive_proof(synthetic_bundle_files)

    bundle = parse_retrosheet_bundle(files, source_version_id="zero-stat-defense-proof")

    entry = next(
        item
        for item in bundle.lineups
        if item.raw_game_id == "HOM202504011"
        and item.team_id == "HOM"
        and item.player_id == HOME[7]
    )
    assert entry.batting_order == 8
    assert entry.fielding_positions == (5,)
    assert (
        entry.fielding_positions_source
        == "PLAYS_INITIAL_ORIGINAL_DATE_DEFENSIVE_STATE"
    )
    assert entry.position_reconciled_from_play_state is True
    assert entry.teamstats_position_discrepancy is True
    assert entry.position_proof_play_id == "retrosheet:play:HOM202504011:1"
    assert entry.position_proof_event_date == date(2025, 4, 1)
    assert bundle.plays[0].defensive_fielders[4] == HOME[7]


@pytest.mark.parametrize("case", ("later_only", "duplicate_position"))
def test_zero_stat_home_defensive_proof_fails_closed(
    synthetic_bundle_files: dict[str, str],
    case: str,
) -> None:
    files = _with_zero_stat_home_defensive_proof(synthetic_bundle_files)
    if case == "later_only":
        files = _mutate(files, "plays.csv", 0, "f5", "")
        files = _mutate(files, "plays.csv", 1, "f5", HOME[7])
        message = "conflicting PBP defensive positions"
    else:
        files = _mutate(files, "plays.csv", 0, "f4", HOME[7])
        message = "conflicting PBP defensive positions"

    with pytest.raises(RetrosheetImportError, match=message):
        parse_retrosheet_bundle(files, source_version_id="bad-zero-stat-defense-proof")


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("no_proof", "lacks original-date PBP lineup proof"),
        ("conflicting_slot", "conflicting PBP lineup slots"),
        ("missing_position", "lacks a valid original-date PBP fielding position"),
        ("nonstarting_role", "lacks a valid original-date PBP fielding position"),
        ("completion_date_only", "conflicting PBP lineup slots"),
    ],
)
def test_zero_stat_lineup_position_proof_fails_closed(
    synthetic_bundle_files: dict[str, str],
    case: str,
    message: str,
) -> None:
    files = _with_zero_stat_suspended_lineup_proof(synthetic_bundle_files)
    if case == "no_proof":
        files = _mutate(files, "plays.csv", 0, "l8", "")
        files = _mutate(files, "plays.csv", 0, "lf8", "")
    elif case == "conflicting_slot":
        files = _mutate(files, "plays.csv", 0, "l7", VISITORS[7])
        files = _mutate(files, "plays.csv", 0, "lf7", "2")
    elif case == "missing_position":
        files = _mutate(files, "plays.csv", 0, "lf8", "")
    elif case == "nonstarting_role":
        files = _mutate(files, "plays.csv", 0, "lf8", "11")
    elif case == "completion_date_only":
        files = _mutate(files, "plays.csv", 0, "l8", VIS_BENCH[0])
        files = _mutate(files, "plays.csv", 1, "l8", VISITORS[7])
    else:  # pragma: no cover - parametrization is intentionally exhaustive
        raise AssertionError(case)

    with pytest.raises(RetrosheetImportError, match=message):
        parse_retrosheet_bundle(files, source_version_id="bad-zero-stat-lineup-proof")


def test_zero_stat_dh_position_requires_explicit_gameinfo_usedh(
    synthetic_bundle_files: dict[str, str],
) -> None:
    allowed = _with_zero_stat_suspended_lineup_proof(synthetic_bundle_files)
    allowed = _mutate(allowed, "plays.csv", 0, "lf8", "10")
    bundle = parse_retrosheet_bundle(allowed, source_version_id="zero-stat-dh-proof")
    entry = next(item for item in bundle.lineups if item.player_id == VISITORS[7])
    assert entry.fielding_positions == (10,)

    disallowed = _mutate(allowed, "gameinfo.csv", 0, "usedh", "")
    with pytest.raises(RetrosheetImportError, match="valid original-date PBP fielding"):
        parse_retrosheet_bundle(disallowed, source_version_id="unjustified-zero-stat-dh")


def test_between_play_pinch_runner_is_reconciled_from_exact_batting_value_rows(
    synthetic_bundle_files: dict[str, str],
) -> None:
    files = _with_pinch_runner_continuity(synthetic_bundle_files)

    bundle = parse_retrosheet_bundle(files, source_version_id="proven-pinch-runner")

    assert bundle.plays[0].after.runner_on_first == VISITORS[0]
    assert bundle.plays[1].before.runner_on_first == VIS_BENCH[0]
    assert bundle.plays[1].pinch_runner_replacement_before is True
    assert bundle.pinch_runner_continuity_replacement_count == 1


def test_simultaneous_pinch_runners_require_exact_proof_for_every_changed_base(
    synthetic_bundle_files: dict[str, str],
) -> None:
    files = _with_simultaneous_pinch_runner_continuity(synthetic_bundle_files)

    bundle = parse_retrosheet_bundle(files, source_version_id="proven-double-pinch-runner")

    transition = bundle.plays[2]
    assert transition.before.runner_on_first == VIS_BENCH[1]
    assert transition.before.runner_on_second == VIS_BENCH[0]
    assert transition.pinch_runner_replacement_before is True
    assert bundle.pinch_runner_continuity_replacement_count == 1

    one_unproven = _mutate(files, "batting.csv", 3, "pr", "")
    with pytest.raises(RetrosheetImportError, match="base/out state is not conserved"):
        parse_retrosheet_bundle(one_unproven, source_version_id="partly-proven-double-pr")


@pytest.mark.parametrize(
    ("np_bridge", "mode", "half_play_number"),
    [
        (False, AUTOMATIC_RUNNER_DIRECT_FIRST_PLAY, 1),
        (True, AUTOMATIC_RUNNER_AFTER_NP_BRIDGE, 2),
    ],
)
def test_declared_extra_inning_automatic_runner_is_source_proven(
    synthetic_bundle_files: dict[str, str],
    np_bridge: bool,
    mode: str,
    half_play_number: int,
) -> None:
    files = _with_automatic_runner(synthetic_bundle_files, np_bridge=np_bridge)

    bundle = parse_retrosheet_bundle(files, source_version_id="automatic-runner")

    game = bundle.games[0]
    placement = next(
        play for play in bundle.plays if play.automatic_runner_placement_before
    )
    assert game.regulation_innings == 9
    assert game.tiebreaker_base == 2
    assert placement.inning == 10
    assert placement.half_play_number == half_play_number
    assert placement.automatic_runner_placement_mode == mode
    assert placement.automatic_runner_id == VISITORS[8]
    assert placement.before.runner_on_second == VISITORS[8]
    assert bundle.automatic_runner_placement_count == 1


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_base", "unproven automatic-runner"),
        ("not_extra", "unproven automatic-runner"),
        ("missing_rule", "unproven automatic-runner"),
        ("multiple_runners", "unproven automatic-runner"),
        ("outs", "new half-inning must reset outs"),
        ("score", "score is not conserved"),
        ("invalid_np_bridge", "base/out state is not conserved"),
    ],
)
def test_unproven_automatic_runner_shapes_fail_closed(
    synthetic_bundle_files: dict[str, str],
    case: str,
    message: str,
) -> None:
    np_bridge = case == "invalid_np_bridge"
    files = _with_automatic_runner(synthetic_bundle_files, np_bridge=np_bridge)
    placement_index = -1
    if case == "wrong_base":
        for column in ("br2_pre", "br2_post"):
            files = _mutate(files, "plays.csv", placement_index, column, "")
        for column in ("br1_pre", "br1_post"):
            files = _mutate(files, "plays.csv", placement_index, column, VISITORS[8])
    elif case == "not_extra":
        files = _mutate(files, "gameinfo.csv", 0, "innings", "10")
    elif case == "missing_rule":
        files = _mutate(files, "gameinfo.csv", 0, "tiebreaker", "")
    elif case == "multiple_runners":
        for column in ("br1_pre", "br1_post"):
            files = _mutate(files, "plays.csv", placement_index, column, VISITORS[7])
    elif case == "outs":
        files = _mutate(files, "plays.csv", placement_index, "outs_pre", "1")
    elif case == "score":
        files = _mutate(files, "plays.csv", placement_index, "score_v", "1")
    elif case == "invalid_np_bridge":
        files = _mutate(files, "plays.csv", -2, "event", "BK")
    else:  # pragma: no cover - parametrization is intentionally exhaustive
        raise AssertionError(case)

    with pytest.raises(RetrosheetImportError, match=message):
        parse_retrosheet_bundle(files, source_version_id="unproven-automatic-runner")


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("pr", "0"),
        ("b_lp", "6"),
        ("b_seq", "3"),
        ("stattype", "official"),
    ],
)
def test_between_play_runner_identity_change_without_exact_pr_evidence_fails_closed(
    synthetic_bundle_files: dict[str, str],
    column: str,
    value: str,
) -> None:
    files = _with_pinch_runner_continuity(synthetic_bundle_files)
    files = _mutate(files, "batting.csv", 1, column, value)

    with pytest.raises(RetrosheetImportError, match="base/out state is not conserved"):
        parse_retrosheet_bundle(files, source_version_id="unproven-pinch-runner")


def test_deduced_pbp_quality_is_preserved_across_game_stats_and_plays(
    synthetic_bundle_files: dict[str, str],
) -> None:
    files = _mutate(synthetic_bundle_files, "gameinfo.csv", 0, "pbp", "d")
    for row_index in (0, 1):
        files = _mutate(files, "teamstats.csv", row_index, "pbp", "d")
    for row_index in range(4):
        files = _mutate(files, "pitching.csv", row_index, "pbp", "d")
    for row_index in range(2):
        files = _mutate(files, "plays.csv", row_index, "pbp", "deduced")

    bundle = parse_retrosheet_bundle(files, source_version_id="deduced-pbp")
    assert bundle.games[0].pbp_quality is RetrosheetPbpQuality.DEDUCED
    assert bundle.games[1].pbp_quality is RetrosheetPbpQuality.NONE
    assert {play.pbp_quality for play in bundle.plays} == {RetrosheetPbpQuality.DEDUCED}


def test_completed_suspension_preserves_exact_dates_and_relocated_completion_site(
    synthetic_bundle_files: dict[str, str],
) -> None:
    files = _with_completed_suspension(synthetic_bundle_files)

    bundle = parse_retrosheet_bundle(files, source_version_id="completed-suspension")

    game = bundle.games[0]
    assert game.suspended is True
    assert game.completion_date == date(2025, 8, 31)
    assert game.final_available_date == date(2025, 8, 31)
    assert game.box_known is True
    assert [(play.event_date, play.site) for play in bundle.plays] == [
        (date(2025, 4, 1), "TEST1"),
        (date(2025, 8, 31), "ALT01"),
    ]


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("suspend", "unknown", "invalid date"),
        ("suspend", "2025-04-01", "must follow the original game date"),
        ("vruns", "", "completed suspended game requires"),
        ("pbp", "", "completed suspended game requires"),
        ("box", "", "completed suspended game requires"),
        ("lineups", "", "completed suspended game requires"),
        ("batteries", "p", "completed suspended game requires"),
    ],
)
def test_incomplete_or_ambiguous_suspension_fails_closed(
    synthetic_bundle_files: dict[str, str],
    column: str,
    value: str,
    message: str,
) -> None:
    files = _with_completed_suspension(synthetic_bundle_files)
    files = _mutate(files, "gameinfo.csv", 0, column, value)

    with pytest.raises(RetrosheetImportError, match=message):
        parse_retrosheet_bundle(files, source_version_id="invalid-suspension")


@pytest.mark.parametrize(
    ("row_index", "column", "value", "message"),
    [
        (1, "date", "2025-08-30", "original or exact completion date"),
        (0, "site", "ALT01", "original suspended-game segment site"),
    ],
)
def test_suspension_play_date_and_site_contract_fails_closed(
    synthetic_bundle_files: dict[str, str],
    row_index: int,
    column: str,
    value: str,
    message: str,
) -> None:
    files = _with_completed_suspension(synthetic_bundle_files)
    files = _mutate(files, "plays.csv", row_index, column, value)

    with pytest.raises(RetrosheetImportError, match=message):
        parse_retrosheet_bundle(files, source_version_id="invalid-suspension-segment")


def test_suspension_requires_source_plays_on_exact_completion_date(
    synthetic_bundle_files: dict[str, str],
) -> None:
    files = _with_completed_suspension(synthetic_bundle_files)
    files = _mutate(files, "plays.csv", 1, "date", "2025-04-01")
    files = _mutate(files, "plays.csv", 1, "site", "TEST1")

    with pytest.raises(RetrosheetImportError, match="requires PBP on both"):
        parse_retrosheet_bundle(files, source_version_id="missing-completion-segment")


def test_home_team_batting_first_drives_team_and_score_transition(
    synthetic_bundle_files: dict[str, str],
) -> None:
    files = _mutate(synthetic_bundle_files, "gameinfo.csv", 0, "htbf", "y")
    files = _mutate(files, "gameinfo.csv", 0, "vruns", "0")
    files = _mutate(files, "gameinfo.csv", 0, "hruns", "2")
    files = _mutate(files, "teamstats.csv", 0, "inn1", "0")
    files = _mutate(files, "teamstats.csv", 0, "b_r", "0")
    files = _mutate(files, "teamstats.csv", 1, "inn1", "2")
    files = _mutate(files, "teamstats.csv", 1, "b_r", "2")
    for row_index, batter in enumerate(HOME[:2]):
        files = _mutate(files, "plays.csv", row_index, "vis_home", "1")
        files = _mutate(files, "plays.csv", row_index, "batteam", "HOM")
        files = _mutate(files, "plays.csv", row_index, "pitteam", "VIS")
        files = _mutate(files, "plays.csv", row_index, "batter", batter)
        files = _mutate(files, "plays.csv", row_index, "pitcher", VIS_PITCHERS[0])
    files = _mutate(files, "plays.csv", 0, "br1_post", HOME[0])
    files = _mutate(files, "plays.csv", 1, "br1_pre", HOME[0])
    files = _mutate(files, "plays.csv", 1, "run_b", HOME[1])
    files = _mutate(files, "plays.csv", 1, "run1", HOME[0])

    bundle = parse_retrosheet_bundle(files, source_version_id="home-bats-first")
    assert {play.batting_team for play in bundle.plays} == {"HOM"}
    assert (bundle.plays[-1].after.visiting_score, bundle.plays[-1].after.home_score) == (
        0,
        2,
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("single", PlateAppearanceOutcome.SINGLE),
        ("double", PlateAppearanceOutcome.DOUBLE),
        ("triple", PlateAppearanceOutcome.TRIPLE),
        ("hr", PlateAppearanceOutcome.HOME_RUN),
        ("sh", PlateAppearanceOutcome.BIP_OUT),
        ("sf", PlateAppearanceOutcome.BIP_OUT),
        ("hbp", PlateAppearanceOutcome.WALK_HBP_CI),
        ("walk", PlateAppearanceOutcome.WALK_HBP_CI),
        ("k", PlateAppearanceOutcome.STRIKEOUT),
        ("xi", PlateAppearanceOutcome.WALK_HBP_CI),
        ("roe", PlateAppearanceOutcome.OTHER_REACH),
        ("fc", PlateAppearanceOutcome.OTHER_REACH),
        ("othout", PlateAppearanceOutcome.BIP_OUT),
    ],
)
def test_pa_taxonomy_crosswalk(token: str, expected: PlateAppearanceOutcome) -> None:
    row = {column: "0" for column in (
        "single", "double", "triple", "hr", "sh", "sf", "hbp", "walk", "k", "xi",
        "roe", "fc", "othout", "noout",
    )}
    row.update(pa="1")
    row[token] = "1"
    assert classify_retrosheet_plate_appearance(row) == (token, expected)


def test_pa_taxonomy_rejects_unmapped_ambiguous_and_malformed_tokens() -> None:
    row = {column: "0" for column in (
        "single", "double", "triple", "hr", "sh", "sf", "hbp", "walk", "k", "xi",
        "roe", "fc", "othout", "noout",
    )}
    row.update(pa="1", noout="1")
    with pytest.raises(RetrosheetImportError, match="unmapped"):
        classify_retrosheet_plate_appearance(row)
    row.update(noout="0", single="1", double="1")
    with pytest.raises(RetrosheetImportError, match="exactly one"):
        classify_retrosheet_plate_appearance(row)
    row.update(single="maybe", double="0")
    with pytest.raises(RetrosheetImportError, match="expected binary"):
        classify_retrosheet_plate_appearance(row)


@pytest.mark.parametrize(
    ("filename", "row_index", "column", "value", "message"),
    [
        ("plays.csv", 1, "score_v", "1", "score is not conserved"),
        ("plays.csv", 1, "br1_pre", "", "scoring identity conflicts"),
        ("plays.csv", 1, "runs", "1", "identified scoring runners"),
        ("gameinfo.csv", 0, "vruns", "3", "batting runs conflict"),
    ],
)
def test_score_and_base_conservation_fail_closed(
    synthetic_bundle_files: dict[str, str],
    filename: str,
    row_index: int,
    column: str,
    value: str,
    message: str,
) -> None:
    files = _mutate(synthetic_bundle_files, filename, row_index, column, value)
    with pytest.raises(RetrosheetImportError, match=message):
        parse_retrosheet_bundle(files, source_version_id="broken-conservation")


def test_game_identity_starter_and_quality_conflicts_fail_closed(
    synthetic_bundle_files: dict[str, str],
) -> None:
    bad_gid = _mutate(synthetic_bundle_files, "gameinfo.csv", 0, "number", "0")
    with pytest.raises(RetrosheetImportError, match="does not match home/date/number"):
        parse_retrosheet_bundle(bad_gid, source_version_id="bad-game-id")

    bad_starter = _mutate(
        synthetic_bundle_files,
        "pitching.csv",
        0,
        "id",
        VIS_PITCHERS[1],
    )
    with pytest.raises(RetrosheetImportError, match="starters disagree"):
        parse_retrosheet_bundle(bad_starter, source_version_id="bad-starter")

    bad_quality = _mutate(synthetic_bundle_files, "gameinfo.csv", 0, "pbp", "estimated")
    with pytest.raises(RetrosheetImportError, match="unmapped game PBP token"):
        parse_retrosheet_bundle(bad_quality, source_version_id="bad-quality")


def test_bundle_requires_exactly_seven_files(
    synthetic_bundle_files: dict[str, str],
) -> None:
    incomplete = dict(synthetic_bundle_files)
    incomplete.pop("fielding.csv")
    with pytest.raises(RetrosheetImportError, match="exactly seven"):
        parse_retrosheet_bundle(incomplete, source_version_id="missing-table")
