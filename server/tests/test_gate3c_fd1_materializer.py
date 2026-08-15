from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import polars as pl
import pytest

import app.gen2.fd1_materializer as materializer_module
from app.gen2.fd1_materializer import (
    GEN2_ACTUAL_LINEUP_UPPER_BOUND,
    GEN2_PRIMARY_2023_2025,
    GEN2_PRIOR_ONLY_2021_2022,
    MATERIALIZED_FAMILIES,
    Fd1MaterializationError,
    GameOrder,
    is_strictly_prior_game,
    iter_retrosheet_monthly_materializations,
    iter_strict_prior_rows,
    materialize_retrosheet_window,
    verify_payload_checksum,
)
from app.gen2.retrosheet import (
    AUTOMATIC_RUNNER_AFTER_NP_BRIDGE,
    HISTORICAL_ACTUAL_LINEUP_TIMING_RISK,
    HISTORICAL_ACTUAL_STARTER_TIMING_RISK,
    RETROSHEET_CSV_HEADERS,
    RetrosheetImportError,
)


VISITORS = tuple(f"vply{letter}{number:03d}" for number, letter in enumerate("abcdefghi", 1))
HOME = tuple(f"hply{letter}{number:03d}" for number, letter in enumerate("abcdefghi", 1))
VIS_PITCHERS = ("vpitj010", "vpitk011")
HOME_PITCHERS = ("hpitj010", "hpitk011")
VIS_MANAGER = "mgrva001"
HOME_MANAGER = "mgrha002"


def _row(filename: str, **values: object) -> dict[str, str]:
    result = {column: "" for column in RETROSHEET_CSV_HEADERS[filename]}
    assert not set(values) - set(result)
    result.update({key: str(value) for key, value in values.items()})
    return result


def _game_id(game_date: date, number: int) -> str:
    return f"HOM{game_date:%Y%m%d}{number}"


def _roster_rows(seasons: set[int]) -> list[dict[str, str]]:
    return [
        _row(
            "allplayers.csv",
            id=player,
            last="Fixture",
            first=player,
            bat="R",
            throw="R",
            team=team,
            season=season,
        )
        for season in sorted(seasons)
        for team, players in (
            ("VIS", VISITORS + VIS_PITCHERS),
            ("HOM", HOME + HOME_PITCHERS),
        )
        for player in players
    ]


def _game_row(
    game_date: date,
    number: int,
    *,
    completion_date: date | None = None,
) -> dict[str, str]:
    return _row(
        "gameinfo.csv",
        gid=_game_id(game_date, number),
        visteam="VIS",
        hometeam="HOM",
        site="TEST1",
        date=game_date.strftime("%Y%m%d"),
        number=number,
        starttime="1305",
        daynight="day",
        innings=9,
        tiebreaker="",
        usedh="y",
        htbf="",
        fieldcond="dry",
        precip="none",
        sky="clear",
        temp=72,
        winddir="out to center",
        windspeed=8,
        oscorer="scora001",
        umphome="umpaa001",
        ump1b="umpbb002",
        ump2b="umpcc003",
        ump3b="umpdd004",
        suspend=(completion_date.strftime("%Y%m%d") if completion_date else ""),
        gametype="regular",
        vruns=1,
        hruns=0,
        wteam="VIS",
        lteam="HOM",
        line="y",
        batteries="both",
        lineups="y",
        box="y",
        pbp="y",
        season=game_date.year,
    )


def _team_row(
    game_date: date,
    number: int,
    team: str,
    players: tuple[str, ...],
    pitchers: tuple[str, ...],
) -> dict[str, str]:
    opponent = "HOM" if team == "VIS" else "VIS"
    runs = int(team == "VIS")
    row = _row(
        "teamstats.csv",
        gid=_game_id(game_date, number),
        team=team,
        inn1=runs,
        stattype="value",
        b_r=runs,
        date=game_date.strftime("%Y%m%d"),
        number=number,
        site="TEST1",
        vishome="v" if team == "VIS" else "h",
        opp=opponent,
        win=int(team == "VIS"),
        loss=int(team == "HOM"),
        tie=0,
        gametype="regular",
        box="y",
        pbp="y",
        mgr=VIS_MANAGER if team == "VIS" else HOME_MANAGER,
    )
    for position, player in enumerate(players, 1):
        row[f"start_l{position}"] = player
    row["start_f1"] = pitchers[0]
    for position, player in zip(range(2, 10), players[:8], strict=True):
        row[f"start_f{position}"] = player
    row["start_f10"] = players[8]
    return row


def _batting_rows(game_date: date, number: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for team, player, runs in (("VIS", VISITORS[0], 1), ("HOM", HOME[0], 0)):
        opponent = "HOM" if team == "VIS" else "VIS"
        values = dict(
            gid=_game_id(game_date, number),
            id=player,
            team=team,
            b_lp=1,
            b_seq=1,
            stattype="value",
            b_pa=1,
            b_ab=1,
            b_r=runs,
            b_h=runs,
            b_hr=runs,
            date=game_date.strftime("%Y%m%d"),
            number=number,
            site="TEST1",
            vishome="v" if team == "VIS" else "h",
            opp=opponent,
            win=int(team == "VIS"),
            loss=int(team == "HOM"),
            tie=0,
            gametype="regular",
            box="y",
            pbp="y",
        )
        rows.append(_row("batting.csv", **values))
        if team == "VIS":
            values.update(stattype="lower")
            rows.append(_row("batting.csv", **values))
    return rows


def _pitching_rows(
    game_date: date,
    number: int,
    team: str,
    pitchers: tuple[str, ...],
) -> list[dict[str, str]]:
    opponent = "HOM" if team == "VIS" else "VIS"
    return [
        _row(
            "pitching.csv",
            gid=_game_id(game_date, number),
            id=player,
            team=team,
            p_seq=sequence,
            stattype="value",
            p_ipouts=3 if sequence == 1 else 0,
            p_bfp=1 if sequence == 1 else 0,
            p_h=int(team == "HOM" and sequence == 1),
            p_hr=int(team == "HOM" and sequence == 1),
            p_r=int(team == "HOM" and sequence == 1),
            p_er=int(team == "HOM" and sequence == 1),
            p_gs=int(sequence == 1),
            p_gf=int(sequence == 2),
            date=game_date.strftime("%Y%m%d"),
            number=number,
            site="TEST1",
            vishome="v" if team == "VIS" else "h",
            opp=opponent,
            win=int(team == "VIS" and sequence == 1),
            loss=int(team == "HOM" and sequence == 1),
            tie=0,
            gametype="regular",
            box="y",
            pbp="y",
        )
        for sequence, player in enumerate(pitchers, 1)
    ]


def _fielding_rows(game_date: date, number: int) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for team, player in (("VIS", VISITORS[0]), ("HOM", HOME[0])):
        opponent = "HOM" if team == "VIS" else "VIS"
        result.append(
            _row(
                "fielding.csv",
                gid=_game_id(game_date, number),
                id=player,
                team=team,
                d_seq=1,
                d_pos=2,
                stattype="value",
                d_ifouts=3,
                d_po=1,
                d_gs=1,
                date=game_date.strftime("%Y%m%d"),
                number=number,
                site="TEST1",
                vishome="v" if team == "VIS" else "h",
                opp=opponent,
                win=int(team == "VIS"),
                loss=int(team == "HOM"),
                tie=0,
                gametype="regular",
                box="y",
                pbp="y",
            )
        )
    return result


def _play_row(game_date: date, number: int) -> dict[str, str]:
    values: dict[str, object] = {
        "gid": _game_id(game_date, number),
        "event": "HR/F",
        "inning": 1,
        "top_bot": 0,
        "vis_home": 0,
        "site": "TEST1",
        "batteam": "VIS",
        "pitteam": "HOM",
        "score_v": 0,
        "score_h": 0,
        "batter": VISITORS[0],
        "pitcher": HOME_PITCHERS[0],
        "lp": 1,
        "bat_f": 10,
        "bathand": "R",
        "pithand": "R",
        "balls": 1,
        "strikes": 1,
        "count": "11",
        "pitches": "BCX",
        "nump": 3,
        "pa": 1,
        "ab": 1,
        "hr": 1,
        "outs_pre": 0,
        "outs_post": 0,
        "run_b": VISITORS[0],
        "runs": 1,
        "rbi": 1,
        "er": 1,
        "tur": 0,
        "pn": 1,
        "date": game_date.strftime("%Y%m%d"),
        "gametype": "regular",
        "pbp": "full",
    }
    for column in (
        "single",
        "double",
        "triple",
        "sh",
        "sf",
        "hbp",
        "walk",
        "k",
        "xi",
        "roe",
        "fc",
        "othout",
        "noout",
    ):
        values[column] = 0
    return _row("plays.csv", **values)


def _resumption_play_row(
    game_date: date,
    number: int,
    *,
    completion_date: date,
    completion_site: str,
) -> dict[str, str]:
    row = _play_row(game_date, number)
    row.update(
        {
            "event": "NP",
            "site": completion_site,
            "score_v": "1",
            "pitches": "",
            "nump": "0",
            "pa": "0",
            "ab": "0",
            "hr": "0",
            "run_b": "",
            "runs": "0",
            "rbi": "0",
            "er": "0",
            "pn": "2",
            "lp": "2",
            "date": completion_date.strftime("%Y%m%d"),
        }
    )
    return row


def _write_bundle(
    directory: Path,
    games: list[tuple[date, int]],
    *,
    suspensions: dict[tuple[date, int], tuple[date, str]] | None = None,
) -> Path:
    directory.mkdir()
    suspended = suspensions or {}
    seasons = {game_date.year for game_date, _ in games}
    tables: dict[str, list[dict[str, str]]] = {
        "allplayers.csv": _roster_rows(seasons),
        "gameinfo.csv": [],
        "teamstats.csv": [],
        "batting.csv": [],
        "pitching.csv": [],
        "fielding.csv": [],
        "plays.csv": [],
    }
    for game_date, number in games:
        suspension = suspended.get((game_date, number))
        tables["gameinfo.csv"].append(
            _game_row(
                game_date,
                number,
                completion_date=suspension[0] if suspension else None,
            )
        )
        tables["teamstats.csv"].extend(
            (
                _team_row(game_date, number, "VIS", VISITORS, VIS_PITCHERS),
                _team_row(game_date, number, "HOM", HOME, HOME_PITCHERS),
            )
        )
        tables["batting.csv"].extend(_batting_rows(game_date, number))
        tables["pitching.csv"].extend(
            _pitching_rows(game_date, number, "VIS", VIS_PITCHERS)
        )
        tables["pitching.csv"].extend(
            _pitching_rows(game_date, number, "HOM", HOME_PITCHERS)
        )
        tables["fielding.csv"].extend(_fielding_rows(game_date, number))
        tables["plays.csv"].append(_play_row(game_date, number))
        if suspension is not None:
            tables["plays.csv"].append(
                _resumption_play_row(
                    game_date,
                    number,
                    completion_date=suspension[0],
                    completion_site=suspension[1],
                )
            )
    for filename, rows in tables.items():
        with (directory / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=RETROSHEET_CSV_HEADERS[filename],
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
    return directory


def _mutate_bundle_csv(
    directory: Path,
    filename: str,
    row_index: int,
    column: str,
    value: str,
) -> None:
    path = directory / filename
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[row_index][column] = value
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=RETROSHEET_CSV_HEADERS[filename],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _automatic_runner_play_row(
    game_date: date,
    number: int,
    *,
    play_number: int,
    inning: int,
    top_bottom: int,
    outs_post: int,
    event: str,
    pa: int,
    terminal: str | None = None,
    automatic_runner: bool = False,
) -> dict[str, str]:
    batting_team = "VIS" if top_bottom == 0 else "HOM"
    pitching_team = "HOM" if top_bottom == 0 else "VIS"
    batter = VISITORS[0] if top_bottom == 0 else HOME[0]
    pitcher = HOME_PITCHERS[0] if top_bottom == 0 else VIS_PITCHERS[0]
    values: dict[str, object] = {
        "gid": _game_id(game_date, number),
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
        "date": game_date.strftime("%Y%m%d"),
        "gametype": "regular",
        "pbp": "full",
    }
    for column in (
        "single", "double", "triple", "hr", "sh", "sf", "hbp", "walk", "k",
        "xi", "roe", "fc", "othout", "noout",
    ):
        values[column] = int(column == terminal)
    if automatic_runner:
        values["br2_pre"] = VISITORS[8]
        values["br2_post"] = VISITORS[8]
    return _row("plays.csv", **values)


def _write_automatic_runner_bundle(directory: Path, game_date: date) -> Path:
    bundle = _write_bundle(directory, [(game_date, 0)])
    for column, value in (
        ("vruns", "0"),
        ("hruns", "0"),
        ("tiebreaker", "2"),
    ):
        _mutate_bundle_csv(bundle, "gameinfo.csv", 0, column, value)
    for row_index in (0, 1):
        _mutate_bundle_csv(bundle, "teamstats.csv", row_index, "b_r", "0")
        _mutate_bundle_csv(bundle, "teamstats.csv", row_index, "inn1", "0")
    plays = [
        _automatic_runner_play_row(
            game_date,
            0,
            play_number=(inning - 1) * 2 + top_bottom + 1,
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
    plays.extend(
        (
            _automatic_runner_play_row(
                game_date,
                0,
                play_number=19,
                inning=10,
                top_bottom=0,
                outs_post=0,
                event="NP",
                pa=0,
            ),
            _automatic_runner_play_row(
                game_date,
                0,
                play_number=20,
                inning=10,
                top_bottom=0,
                outs_post=1,
                event="K",
                pa=1,
                terminal="k",
                automatic_runner=True,
            ),
        )
    )
    with (bundle / "plays.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=RETROSHEET_CSV_HEADERS["plays.csv"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(plays)
    return bundle


def _write_zero_stat_suspended_lineup_bundle(
    directory: Path,
    game_date: date,
    completion_date: date,
) -> Path:
    bundle = _write_bundle(
        directory,
        [(game_date, 0)],
        suspensions={(game_date, 0): (completion_date, "TEST1")},
    )
    _mutate_bundle_csv(bundle, "teamstats.csv", 0, "start_f9", "")
    batting_path = bundle / "batting.csv"
    with batting_path.open("r", encoding="utf-8", newline="") as stream:
        batting_rows = list(csv.DictReader(stream))
    zero_stat = _row(
        "batting.csv",
        gid=_game_id(game_date, 0),
        id=VISITORS[7],
        team="VIS",
        b_lp=8,
        b_seq=1,
        stattype="value",
        date=game_date.strftime("%Y%m%d"),
        number=0,
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
    for column in RETROSHEET_CSV_HEADERS["batting.csv"]:
        if column.startswith("b_") and column not in {"b_lp", "b_seq"}:
            zero_stat[column] = "0"
    batting_rows.append(zero_stat)
    with batting_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=RETROSHEET_CSV_HEADERS["batting.csv"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(batting_rows)
    _mutate_bundle_csv(bundle, "plays.csv", 0, "l8", VISITORS[7])
    _mutate_bundle_csv(bundle, "plays.csv", 0, "lf8", "2")
    _mutate_bundle_csv(bundle, "plays.csv", 1, "l8", VISITORS[6])
    _mutate_bundle_csv(bundle, "plays.csv", 1, "lf8", "11")
    return bundle


@pytest.fixture
def five_game_bundle(tmp_path: Path) -> Path:
    return _write_bundle(
        tmp_path / "daily-logs",
        [
            (date(2024, 3, 31), 0),
            (date(2024, 4, 1), 0),
            (date(2024, 4, 4), 1),
            (date(2024, 4, 4), 2),
            (date(2024, 4, 8), 0),
        ],
    )


def test_one_week_filter_coalesces_family_month_payloads(
    five_game_bundle: Path,
) -> None:
    result = materialize_retrosheet_window(
        five_game_bundle,
        source_version_id="fixture-through-2025",
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 7),
        knowledge_cutoff=date(2025, 1, 1),
    )

    assert result.counters.selected_games == 3
    assert result.counters.primary_games == 3
    assert result.counters.prior_only_games == 0
    assert result.counters.family_rows == {
        "plate_appearance": 3,
        "play_transition": 3,
        "pitch_sequence": 3,
        "player_game_batting": 9,
        "player_game_pitching": 12,
        "player_game_fielding": 6,
        "starter_usage": 6,
        "bullpen_usage": 6,
        "weather": 3,
    }
    assert {batch.family for batch in result.batches} == set(MATERIALIZED_FAMILIES)
    assert len({batch.partition_key for batch in result.batches}) == len(result.batches)
    assert all(
        batch.publication_scope == "PILOT_OR_PARTIAL_MONTH_STAGING"
        for batch in result.batches
    )
    assert all(batch.min_event_date >= date(2024, 4, 1) for batch in result.batches)
    assert all(batch.max_event_date <= date(2024, 4, 7) for batch in result.batches)
    assert all(
        row["population_category"] == GEN2_PRIMARY_2023_2025
        for batch in result.batches
        for row in batch.rows
    )
    for batch in result.batches:
        assert pl.DataFrame(list(batch.iter_rows())).height == batch.row_count
        verify_payload_checksum(batch)


def test_doubleheader_order_is_explicit_and_only_earlier_game_is_prior(
    five_game_bundle: Path,
) -> None:
    result = materialize_retrosheet_window(
        five_game_bundle,
        source_version_id="fixture-through-2025",
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 7),
        knowledge_cutoff=date(2025, 1, 1),
    )
    first_id = "retrosheet:game:HOM202404041"
    second_id = "retrosheet:game:HOM202404042"
    first = result.game_orders[first_id]
    second = result.game_orders[second_id]

    assert first.doubleheader_id == second.doubleheader_id
    assert is_strictly_prior_game(first, second)
    assert not is_strictly_prior_game(second, first)
    assert not is_strictly_prior_game(second, second)

    batting_rows = result.family("player_game_batting")[0].rows
    prior = list(
        iter_strict_prior_rows(
            batting_rows,
            target_game_id=second_id,
            game_orders=result.game_orders,
        )
    )
    assert prior
    assert first_id in {row["game_id"] for row in prior}
    assert second_id not in {row["game_id"] for row in prior}


def test_current_and_future_games_and_unsupported_seasons_are_excluded(
    five_game_bundle: Path,
    tmp_path: Path,
) -> None:
    result = materialize_retrosheet_window(
        five_game_bundle,
        source_version_id="fixture-through-2025",
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 8),
        knowledge_cutoff=date(2024, 4, 4),
    )
    assert result.counters.selected_games == 1
    assert result.counters.exclusion_counts["current_or_future"] == 3
    assert set(result.game_orders) == {"retrosheet:game:HOM202404010"}

    future_bundle = _write_bundle(
        tmp_path / "future-daily-logs", [(date(2026, 4, 1), 0)]
    )
    unsupported = materialize_retrosheet_window(
        future_bundle,
        source_version_id="future-fixture",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 1),
        knowledge_cutoff=date(2027, 1, 1),
    )
    assert unsupported.counters.selected_games == 0
    assert unsupported.counters.exclusion_counts["unsupported_season"] == 1
    assert unsupported.batches == ()


def test_actual_participants_stay_out_of_strict_same_game_population(
    five_game_bundle: Path,
) -> None:
    result = materialize_retrosheet_window(
        five_game_bundle,
        source_version_id="fixture-through-2025",
        start_date=date(2024, 4, 1),
        end_date=date(2024, 4, 1),
        knowledge_cutoff=date(2025, 1, 1),
    )
    batting = result.family("player_game_batting")[0].rows
    actual_lineups = [row for row in batting if row["actual_starting_lineup"]]
    assert actual_lineups
    assert {
        row["actual_lineup_timing_risk"] for row in actual_lineups
    } == {HISTORICAL_ACTUAL_LINEUP_TIMING_RISK}
    assert {row["enhanced_population"] for row in actual_lineups} == {
        GEN2_ACTUAL_LINEUP_UPPER_BOUND
    }
    assert all(not row["strict_same_game_pregame_eligible"] for row in actual_lineups)
    assert (
        result.family("player_game_batting")[0]
        .quality_report["positionless_actual_lineup_rows"]
        == 0
    )

    starters = result.family("starter_usage")[0].rows
    assert {row["timing_risk_label"] for row in starters} == {
        HISTORICAL_ACTUAL_STARTER_TIMING_RISK
    }
    assert all(not row["strict_same_game_pregame_eligible"] for row in starters)
    assert all(not row["announcement_time_known"] for row in starters)
    assert {row["retrosheet_manager_id"] for row in starters} == {
        VIS_MANAGER,
        HOME_MANAGER,
    }
    assert all(row["manager_id"].startswith("retrosheet:player:") for row in starters)
    assert all(
        row["manager_evidence_source_filename"] == "teamstats.csv"
        for row in starters
    )
    assert (
        result.family("starter_usage")[0].quality_report["manager_assignment_rows"]
        == len(starters)
    )

    bullpen = result.family("bullpen_usage")[0]
    assert bullpen.schema_version == "retrosheet_fd1_bullpen_usage_v6"
    assert bullpen.quality_report["manager_assignment_rows"] == bullpen.row_count

    weather = result.family("weather")[0]
    assert weather.schema_version == "retrosheet_fd1_weather_v8"
    assert weather.quality_report["umpire_game_rows"] == weather.row_count
    assert weather.quality_report["umpire_role_assignments"] == weather.row_count * 4
    assert {
        row["retrosheet_umpire_ids"]["home"] for row in weather.rows
    } == {"umpaa001"}

    uncertainty = {row["stattype"]: row["stat_semantics"] for row in batting}
    assert uncertainty["value"] == "BEST_ESTIMATE"
    assert uncertainty["lower"] == "LOWER_BOUND"


def test_weather_preserves_missing_person_sentinel_and_unresolved_source_id(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "official-sentinel-daily-logs",
        [(date(2025, 5, 16), 0)],
    )
    for column, value in (
        ("oscorer", "(none)"),
        ("umphome", "jascs901"),
        ("ump2b", "(none)"),
        ("umplf", "(none)"),
        ("umprf", "(none)"),
    ):
        _mutate_bundle_csv(bundle, "gameinfo.csv", 0, column, value)

    result = materialize_retrosheet_window(
        bundle,
        source_version_id="fixture-through-2025",
        start_date=date(2025, 5, 16),
        end_date=date(2025, 5, 16),
        knowledge_cutoff=date(2026, 1, 1),
    )

    weather = result.family("weather")[0]
    assert weather.schema_version == "retrosheet_fd1_weather_v8"
    assert weather.row_count == 1
    row = weather.rows[0]
    assert row["official_scorer_id"] is None
    assert row["retrosheet_official_scorer_id"] == "(none)"
    assert row["official_scorer_ids"] == []
    assert row["retrosheet_official_scorer_ids"] == []
    assert row["umpire_home_id"] == "retrosheet:player:jascs901"
    assert row["umpire_second_base_id"] is None
    assert row["umpire_left_field_id"] is None
    assert row["umpire_right_field_id"] is None
    assert row["retrosheet_umpire_ids"] == {
        "home": "jascs901",
        "first_base": "umpbb002",
        "second_base": "(none)",
        "third_base": "umpdd004",
        "left_field": "(none)",
        "right_field": "(none)",
    }
    assert weather.quality_report["umpire_game_rows"] == 1
    assert weather.quality_report["umpire_role_assignments"] == 3


def test_suspended_game_preserves_ordered_multi_official_scorers_and_lineage(
    tmp_path: Path,
) -> None:
    game_date = date(2025, 7, 1)
    completion_date = date(2025, 7, 2)
    bundle = _write_bundle(
        tmp_path / "bos-20250701-shaped-multi-scorer",
        [(game_date, 0)],
        suspensions={(game_date, 0): (completion_date, "TEST1")},
    )
    _mutate_bundle_csv(
        bundle,
        "gameinfo.csv",
        0,
        "oscorer",
        "foxxl701;medes701",
    )

    result = materialize_retrosheet_window(
        bundle,
        source_version_id="fixture-through-2025",
        start_date=game_date,
        end_date=completion_date,
        knowledge_cutoff=date(2026, 1, 1),
    )

    weather = result.family("weather")[0]
    assert weather.schema_version == "retrosheet_fd1_weather_v8"
    row = weather.rows[0]
    assert row["retrosheet_official_scorer_id"] == "foxxl701;medes701"
    assert row["official_scorer_id"] is None
    assert row["retrosheet_official_scorer_ids"] == ["foxxl701", "medes701"]
    assert row["official_scorer_ids"] == [
        "retrosheet:player:foxxl701",
        "retrosheet:player:medes701",
    ]
    assert row["official_scorer_collection_game_date"] == "2025-07-01"
    assert row["official_scorer_collection_completion_date"] == "2025-07-02"
    assert row["official_scorer_component_date_assignment"] == "UNAVAILABLE"
    assert row["official_scorer_collection_contract"] == (
        "FROZEN_RETROSHEET_THROUGH_2025_TARGET_SEASONS_2021_2025"
    )
    assert row["official_scorer_source_positions"] == [1, 2]
    assert row["official_scorer_source_filenames"] == ["gameinfo.csv", "gameinfo.csv"]
    assert row["official_scorer_source_row_numbers"] == [
        row["source_row_number"],
        row["source_row_number"],
    ]
    assert row["official_scorer_source_row_checksums"] == [
        row["source_row_checksum"],
        row["source_row_checksum"],
    ]
    assert row["official_scorer_collection_semantics"] == (
        "ORDERED_SOURCE_COMPONENTS_GAME_LEVEL_DATES_ONLY_"
        "NO_COMPONENT_DATE_ASSIGNMENT"
    )
    assert weather.quality_report["official_scorer_game_rows"] == 1
    assert weather.quality_report["official_scorer_assignments"] == 2
    assert weather.quality_report["multi_official_scorer_games"] == 1


@pytest.mark.parametrize(
    ("token", "message"),
    (
        ("foxxl701;", "blank or padded component"),
        ("foxxl701;foxxl701", "must be distinct"),
        ("foxxl701;medes701;ellib701", "exactly two IDs"),
        ("foxxl701;INVALID1", "invalid 8-character Retrosheet player id"),
    ),
)
def test_suspended_game_rejects_malformed_multi_official_scorers(
    tmp_path: Path,
    token: str,
    message: str,
) -> None:
    game_date = date(2025, 7, 1)
    completion_date = date(2025, 7, 2)
    bundle = _write_bundle(
        tmp_path / f"invalid-multi-scorer-{len(token)}-{sum(map(ord, token))}",
        [(game_date, 0)],
        suspensions={(game_date, 0): (completion_date, "TEST1")},
    )
    _mutate_bundle_csv(bundle, "gameinfo.csv", 0, "oscorer", token)

    with pytest.raises(RetrosheetImportError, match=message):
        materialize_retrosheet_window(
            bundle,
            source_version_id="fixture-through-2025",
            start_date=game_date,
            end_date=completion_date,
            knowledge_cutoff=date(2026, 1, 1),
        )


def test_non_suspended_game_rejects_compound_official_scorer(tmp_path: Path) -> None:
    game_date = date(2025, 7, 1)
    bundle = _write_bundle(
        tmp_path / "invalid-nonsuspended-multi-scorer",
        [(game_date, 0)],
    )
    _mutate_bundle_csv(
        bundle,
        "gameinfo.csv",
        0,
        "oscorer",
        "foxxl701;medes701",
    )

    with pytest.raises(RetrosheetImportError, match="requires a completed suspended game"):
        materialize_retrosheet_window(
            bundle,
            source_version_id="fixture-through-2025",
            start_date=game_date,
            end_date=game_date,
            knowledge_cutoff=date(2026, 1, 1),
        )


def test_weather_does_not_alias_near_match_missing_person_tokens(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "invalid-official-sentinel-daily-logs",
        [(date(2025, 5, 16), 0)],
    )
    _mutate_bundle_csv(bundle, "gameinfo.csv", 0, "umplf", "(NONE)")

    with pytest.raises(RetrosheetImportError, match="invalid 8-character Retrosheet player id"):
        materialize_retrosheet_window(
            bundle,
            source_version_id="fixture-through-2025",
            start_date=date(2025, 5, 16),
            end_date=date(2025, 5, 16),
            knowledge_cutoff=date(2026, 1, 1),
        )


def test_prior_only_population_pitch_semantics_and_checksums_are_idempotent(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(tmp_path / "prior-daily-logs", [(date(2022, 5, 2), 0)])
    kwargs = dict(
        source_version_id="fixture-prior-2022",
        start_date=date(2022, 5, 2),
        end_date=date(2022, 5, 2),
        knowledge_cutoff=date(2023, 1, 1),
    )
    first = materialize_retrosheet_window(bundle, **kwargs)
    second = materialize_retrosheet_window(bundle, **kwargs)

    assert first.build_checksum == second.build_checksum
    assert [batch.partition_checksum for batch in first.batches] == [
        batch.partition_checksum for batch in second.batches
    ]
    assert all(
        row["population_category"] == GEN2_PRIOR_ONLY_2021_2022
        for batch in first.batches
        for row in batch.rows
    )
    pitch = first.family("pitch_sequence")[0].rows[0]
    assert pitch["source_pitch_sequence"] == "BCX"
    assert pitch["statcast_pitch_event"] is False
    assert pitch["velocity_available"] is False
    assert "velocity" not in pitch
    assert len(pitch["source_row_checksum"]) == 64
    assert len(pitch["source_file_sha256"]) == 64
    assert all(len(row["row_checksum"]) == 64 for batch in first.batches for row in batch.rows)


def test_window_and_memory_bounds_fail_before_unbounded_materialization(
    five_game_bundle: Path,
) -> None:
    with pytest.raises(Fd1MaterializationError, match="cannot exceed 31 days"):
        materialize_retrosheet_window(
            five_game_bundle,
            source_version_id="fixture-through-2025",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 1),
            knowledge_cutoff=date(2025, 1, 1),
        )
    with pytest.raises(Fd1MaterializationError, match="selected window exceeded 1 games"):
        materialize_retrosheet_window(
            five_game_bundle,
            source_version_id="fixture-through-2025",
            start_date=date(2024, 4, 1),
            end_date=date(2024, 4, 7),
            knowledge_cutoff=date(2025, 1, 1),
            maximum_games=1,
        )


def test_single_pass_dispatch_is_byte_identical_to_individual_windows(
    five_game_bundle: Path,
    tmp_path: Path,
) -> None:
    staging = tmp_path / "dispatch-staging"
    staging.mkdir()
    dispatched = list(
        iter_retrosheet_monthly_materializations(
            five_game_bundle,
            source_version_id="fixture-through-2025",
            start_date=date(2024, 3, 31),
            end_date=date(2024, 4, 8),
            knowledge_cutoff=date(2025, 1, 1),
            temporary_directory=staging,
        )
    )
    individual = [
        materialize_retrosheet_window(
            five_game_bundle,
            source_version_id="fixture-through-2025",
            start_date=month_start,
            end_date=month_end,
            knowledge_cutoff=date(2025, 1, 1),
        )
        for month_start, month_end in (
            (date(2024, 3, 31), date(2024, 3, 31)),
            (date(2024, 4, 1), date(2024, 4, 8)),
        )
    ]

    assert [result.build_checksum for result in dispatched] == [
        result.build_checksum for result in individual
    ]
    assert [result.selected_bundle_checksum for result in dispatched] == [
        result.selected_bundle_checksum for result in individual
    ]
    assert [
        [batch.partition_checksum for batch in result.batches]
        for result in dispatched
    ] == [
        [batch.partition_checksum for batch in result.batches]
        for result in individual
    ]
    assert [
        [[dict(row) for row in batch.rows] for batch in result.batches]
        for result in dispatched
    ] == [
        [[dict(row) for row in batch.rows] for batch in result.batches]
        for result in individual
    ]
    assert list(staging.iterdir()) == []


def test_completed_suspension_materializes_with_governed_segments_and_dispatch_equivalence(
    tmp_path: Path,
) -> None:
    game_date = date(2024, 4, 1)
    completion_date = date(2024, 8, 26)
    bundle = _write_bundle(
        tmp_path / "suspended-daily-logs",
        [(game_date, 0)],
        suspensions={(game_date, 0): (completion_date, "ALT01")},
    )
    kwargs = dict(
        source_version_id="fixture-completed-suspension",
        start_date=game_date,
        end_date=game_date,
        knowledge_cutoff=date(2025, 1, 1),
    )

    window = materialize_retrosheet_window(bundle, **kwargs)
    dispatched = list(
        iter_retrosheet_monthly_materializations(
            bundle,
            **kwargs,
            temporary_directory=tmp_path,
        )
    )

    assert len(dispatched) == 1
    assert dispatched[0].build_checksum == window.build_checksum
    assert dispatched[0].selected_bundle_checksum == window.selected_bundle_checksum
    assert window.counters.completed_suspended_games == 1
    assert window.counters.relocated_suspended_games == 1
    assert window.counters.suspended_original_segment_plays == 1
    assert window.counters.suspended_completion_segment_plays == 1
    assert all(
        row["game_suspended"] is True
        and row["suspension_completion_date"] == "2024-08-26"
        and row["final_available_date"] == "2024-08-26"
        for batch in window.batches
        for row in batch.rows
    )
    play_rows = window.family("play_transition")[0].rows
    assert [row["retrosheet_event_date"] for row in play_rows] == [
        "2024-04-01",
        "2024-08-26",
    ]
    assert [row["retrosheet_event_site"] for row in play_rows] == ["TEST1", "ALT01"]
    assert [row["resumed_segment"] for row in play_rows] == [False, True]
    assert [row["event_site_changed"] for row in play_rows] == [False, True]
    assert window.family("play_transition")[0].quality_report[
        "relocated_suspended_event_rows"
    ] == 1


def test_suspended_final_cutoff_and_incomplete_evidence_are_excluded(
    tmp_path: Path,
) -> None:
    game_date = date(2024, 4, 1)
    completion_date = date(2024, 8, 26)
    cutoff_bundle = _write_bundle(
        tmp_path / "suspended-after-cutoff",
        [(game_date, 0)],
        suspensions={(game_date, 0): (completion_date, "TEST1")},
    )
    cutoff = materialize_retrosheet_window(
        cutoff_bundle,
        source_version_id="fixture-suspension-cutoff",
        start_date=game_date,
        end_date=game_date,
        knowledge_cutoff=completion_date,
    )
    assert cutoff.counters.selected_games == 0
    assert cutoff.counters.exclusion_counts["suspended_not_final_by_cutoff"] == 1

    incomplete_bundle = _write_bundle(
        tmp_path / "suspended-incomplete",
        [(game_date, 0)],
        suspensions={(game_date, 0): (completion_date, "TEST1")},
    )
    _mutate_bundle_csv(incomplete_bundle, "gameinfo.csv", 0, "box", "")
    incomplete = materialize_retrosheet_window(
        incomplete_bundle,
        source_version_id="fixture-suspension-incomplete",
        start_date=game_date,
        end_date=game_date,
        knowledge_cutoff=date(2025, 1, 1),
    )
    assert incomplete.counters.selected_games == 0
    assert incomplete.counters.exclusion_counts["suspended_incomplete_evidence"] == 1


def test_suspended_final_is_not_a_prior_before_its_completion_date() -> None:
    observation = GameOrder(
        game_id="retrosheet:game:OBS202404010",
        game_date=date(2024, 4, 1),
        completion_date=date(2024, 8, 26),
        game_number=0,
        doubleheader_id=None,
    )
    before_completion = GameOrder(
        game_id="retrosheet:game:TGT202406010",
        game_date=date(2024, 6, 1),
        completion_date=None,
        game_number=0,
        doubleheader_id=None,
    )
    after_completion = GameOrder(
        game_id="retrosheet:game:TGT202409010",
        game_date=date(2024, 9, 1),
        completion_date=None,
        game_number=0,
        doubleheader_id=None,
    )

    assert not is_strictly_prior_game(observation, before_completion)
    assert is_strictly_prior_game(observation, after_completion)


def test_automatic_runner_placement_is_persisted_in_materialized_payload(
    tmp_path: Path,
) -> None:
    game_date = date(2024, 4, 1)
    bundle = _write_automatic_runner_bundle(
        tmp_path / "automatic-runner-daily-logs", game_date
    )

    result = materialize_retrosheet_window(
        bundle,
        source_version_id="fixture-automatic-runner",
        start_date=game_date,
        end_date=game_date,
        knowledge_cutoff=date(2025, 1, 1),
    )

    assert result.counters.automatic_runner_placements == 1
    assert result.counters.automatic_runner_direct_placements == 0
    assert result.counters.automatic_runner_np_bridge_placements == 1
    placement = next(
        row
        for row in result.family("play_transition")[0].rows
        if row["automatic_runner_placement_before"]
    )
    assert placement["regulation_innings"] == 9
    assert placement["tiebreaker_base"] == 2
    assert placement["half_play_number"] == 2
    assert (
        placement["automatic_runner_placement_mode"]
        == AUTOMATIC_RUNNER_AFTER_NP_BRIDGE
    )
    assert placement["automatic_runner_base"] == 2
    assert placement["retrosheet_automatic_runner_id"] == VISITORS[8]
    assert placement["automatic_runner_player_id"] == (
        f"retrosheet:player:{VISITORS[8]}"
    )
    assert result.family("play_transition")[0].quality_report[
        "automatic_runner_np_bridge_rows"
    ] == 1


def test_zero_stat_suspended_lineup_position_is_materialized_as_audited_pbp_evidence(
    tmp_path: Path,
) -> None:
    game_date = date(2024, 4, 1)
    completion_date = date(2024, 8, 26)
    bundle = _write_zero_stat_suspended_lineup_bundle(
        tmp_path / "zero-stat-suspended-lineup",
        game_date,
        completion_date,
    )

    result = materialize_retrosheet_window(
        bundle,
        source_version_id="fixture-zero-stat-suspended-lineup",
        start_date=game_date,
        end_date=game_date,
        knowledge_cutoff=date(2025, 1, 1),
    )

    target = next(
        row
        for row in result.family("player_game_batting")[0].rows
        if row["retrosheet_player_id"] == VISITORS[7]
        and row["stattype"] == "value"
    )
    assert target["actual_starting_lineup"] is True
    assert target["actual_batting_order"] == 8
    assert target["actual_fielding_positions"] == [2]
    assert (
        target["actual_fielding_positions_source"]
        == "PLAYS_INITIAL_ORIGINAL_DATE_LINEUP_STATE"
    )
    assert target["position_reconciled_from_play_state"] is True
    assert target["teamstats_position_discrepancy"] is True
    assert target["position_proof_play_id"].endswith(":1")
    assert target["position_proof_event_date"] == "2024-04-01"
    assert result.counters.lineup_position_reconciliations == 1
    assert result.counters.lineup_nonstarting_role_code_occurrences == 1
    quality = result.family("player_game_batting")[0].quality_report
    assert quality["lineup_position_reconciliation_rows"] == 1
    assert quality["teamstats_position_discrepancy_rows"] == 1
    assert quality["positionless_actual_lineup_rows"] == 0
    play_quality = result.family("play_transition")[0].quality_report
    assert play_quality["lineup_nonstarting_role_code_occurrences"] == 1
    completion_play = result.family("play_transition")[0].rows[1]
    assert completion_play["retrosheet_lineup_role_codes"][7] == 11
    assert completion_play["retrosheet_lineup_role_code_domain"].startswith("1_12")
    fielding_players = {
        row["retrosheet_player_id"]
        for row in result.family("player_game_fielding")[0].rows
    }
    assert VISITORS[7] not in fielding_players


def test_single_pass_dispatch_reads_each_source_once_and_enforces_month_caps(
    five_game_bundle: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original = materializer_module._scan_source_file_once

    def counted_scan(*args: object, **kwargs: object) -> str:
        calls.append(str(args[1]))
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(materializer_module, "_scan_source_file_once", counted_scan)
    results = list(
        iter_retrosheet_monthly_materializations(
            five_game_bundle,
            source_version_id="fixture-through-2025",
            start_date=date(2024, 3, 31),
            end_date=date(2024, 4, 8),
            knowledge_cutoff=date(2025, 1, 1),
            temporary_directory=tmp_path,
        )
    )
    assert len(results) == 2
    assert sorted(calls) == sorted(RETROSHEET_CSV_HEADERS)
    assert len(calls) == len(set(calls)) == 7

    with pytest.raises(Fd1MaterializationError, match="exceeded 1 games"):
        list(
            iter_retrosheet_monthly_materializations(
                five_game_bundle,
                source_version_id="fixture-through-2025",
                start_date=date(2024, 4, 1),
                end_date=date(2024, 4, 8),
                knowledge_cutoff=date(2025, 1, 1),
                maximum_games_per_window=1,
                temporary_directory=tmp_path,
            )
        )
