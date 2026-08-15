"""Provider-free validation and parsing for Retrosheet Daily Logs CSV exports.

The module deliberately accepts text supplied by an authorized acquisition layer; it does
not download data or write to persistence.  Parsing is fail-closed because the Retrosheet
tables are historical truth and can otherwise leak post-game participant information into
pregame features.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import csv
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from hashlib import sha256
import io
import json
import re
from types import MappingProxyType

from app.gen2.event_taxonomy import PlateAppearanceOutcome


RETROSHEET_SOURCE_ID = "retrosheet"
RETROSHEET_DATASET_ID = "retrosheet_daily_logs_csv"
RETROSHEET_SCHEMA_VERSION = "retrosheet_daily_logs_csv_v8"

AUTOMATIC_RUNNER_DIRECT_FIRST_PLAY = "DIRECT_FIRST_PLAY_OF_EXTRA_HALF"
AUTOMATIC_RUNNER_AFTER_NP_BRIDGE = "AFTER_INITIAL_NP_BRIDGE"

HISTORICAL_ACTUAL_LINEUP_TIMING_RISK = "HISTORICAL_ACTUAL_LINEUP_TIMING_RISK"
HISTORICAL_ACTUAL_STARTER_TIMING_RISK = "HISTORICAL_ACTUAL_STARTER_TIMING_RISK"
HISTORICAL_ACTUAL_RELIEVER_TIMING_RISK = "HISTORICAL_ACTUAL_RELIEVER_USAGE_TIMING_RISK"
RETROSPECTIVE_EVENT_TIMING_RISK = "RETROSPECTIVE_EVENT_OUTCOME_TIMING_RISK"
RETROSPECTIVE_ACTUAL_TIMING_SEMANTICS = "RETROSPECTIVE_ACTUAL_TIMING_RISK"


ALLPLAYERS_HEADER = (
    "id", "last", "first", "bat", "throw", "team", "g", "g_p", "g_sp", "g_rp",
    "g_c", "g_1b", "g_2b", "g_3b", "g_ss", "g_lf", "g_cf", "g_rf", "g_of",
    "g_dh", "g_ph", "g_pr", "first_g", "last_g", "season",
)

GAMEINFO_HEADER = (
    "gid", "visteam", "hometeam", "site", "date", "number", "starttime",
    "daynight", "innings", "tiebreaker", "usedh", "htbf", "timeofgame",
    "attendance", "fieldcond", "precip", "sky", "temp", "winddir", "windspeed",
    "oscorer", "forfeit", "suspend", "umphome", "ump1b", "ump2b", "ump3b",
    "umplf", "umprf", "wp", "lp", "save", "gametype", "vruns", "hruns",
    "wteam", "lteam", "line", "batteries", "lineups", "box", "pbp", "season",
)

TEAMSTATS_HEADER = (
    ("gid", "team")
    + tuple(f"inn{inning}" for inning in range(1, 29))
    + ("lob", "mgr", "stattype")
    + tuple(
        f"b_{field}"
        for field in (
            "pa", "ab", "r", "h", "d", "t", "hr", "rbi", "sh", "sf", "hbp",
            "w", "iw", "k", "sb", "cs", "gdp", "xi", "roe",
        )
    )
    + tuple(
        f"p_{field}"
        for field in (
            "ipouts", "noout", "bfp", "h", "d", "t", "hr", "r", "er", "w",
            "iw", "k", "hbp", "wp", "bk", "sh", "sf", "sb", "cs", "pb",
        )
    )
    + tuple(f"d_{field}" for field in ("po", "a", "e", "dp", "tp", "pb", "wp", "sb", "cs"))
    + tuple(f"start_l{position}" for position in range(1, 10))
    + tuple(f"start_f{position}" for position in range(1, 11))
    + (
        "date", "number", "site", "vishome", "opp", "win", "loss", "tie",
        "gametype", "box", "pbp",
    )
)

BATTING_HEADER = (
    "gid", "id", "team", "b_lp", "b_seq", "stattype", "b_pa", "b_ab", "b_r",
    "b_h", "b_d", "b_t", "b_hr", "b_rbi", "b_sh", "b_sf", "b_hbp", "b_w",
    "b_iw", "b_k", "b_sb", "b_cs", "b_gdp", "b_xi", "b_roe", "dh", "ph",
    "pr", "date", "number", "site", "vishome", "opp", "win", "loss", "tie",
    "gametype", "box", "pbp",
)

PITCHING_HEADER = (
    "gid", "id", "team", "p_seq", "stattype", "p_ipouts", "p_noout", "p_bfp",
    "p_h", "p_d", "p_t", "p_hr", "p_r", "p_er", "p_w", "p_iw", "p_k",
    "p_hbp", "p_wp", "p_bk", "p_sh", "p_sf", "p_sb", "p_cs", "p_pb", "wp",
    "lp", "save", "p_gs", "p_gf", "p_cg", "date", "number", "site", "vishome", "opp",
    "win", "loss", "tie", "gametype", "box", "pbp",
)

FIELDING_HEADER = (
    "gid", "id", "team", "d_seq", "d_pos", "stattype", "d_ifouts", "d_po", "d_a",
    "d_e", "d_dp", "d_tp", "d_pb", "d_wp", "d_sb", "d_cs", "d_gs", "date",
    "number", "site", "vishome", "opp", "win", "loss", "tie", "gametype", "box",
    "pbp",
)

_PA_TERMINAL_COLUMNS = (
    "single", "double", "triple", "hr", "sh", "sf", "hbp", "walk", "k", "xi",
    "roe", "fc", "othout", "noout",
)

PLAYS_HEADER = (
    (
        "gid", "event", "inning", "top_bot", "vis_home", "site", "batteam",
        "pitteam", "score_v", "score_h", "batter", "pitcher", "lp", "bat_f",
        "bathand", "pithand", "balls", "strikes", "count", "pitches", "nump", "pa",
        "ab",
    )
    + _PA_TERMINAL_COLUMNS
    + (
        "oth", "bip", "bunt", "ground", "fly", "line", "iw", "gdp", "othdp", "tp",
        "fle", "wp", "pb", "bk", "oa", "di", "sb2", "sb3", "sbh", "cs2", "cs3",
        "csh", "pko1", "pko2", "pko3", "k_safe",
    )
    + tuple(f"e{position}" for position in range(1, 10))
    + ("outs_pre", "outs_post")
    + tuple(f"br{base}_{when}" for when in ("pre", "post") for base in range(1, 4))
    + tuple(f"lob_id{base}" for base in range(1, 4))
    + tuple(f"pr{base}_{when}" for when in ("pre", "post") for base in range(1, 4))
    + ("run_b", "run1", "run2", "run3")
    + ("prun_b", "prun1", "prun2", "prun3")
    + ("ur_b", "ur1", "ur2", "ur3")
    + ("rbi_b", "rbi1", "rbi2", "rbi3")
    + ("runs", "rbi", "er", "tur")
    + tuple(f"l{position}" for position in range(1, 10))
    + tuple(f"lf{position}" for position in range(1, 10))
    + tuple(f"f{position}" for position in range(2, 10))
    + tuple(f"po{position}" for position in range(0, 10))
    + tuple(f"a{position}" for position in range(1, 10))
    + (
        "fseq", "batout1", "batout2", "batout3", "brout_b", "brout1", "brout2",
        "brout3", "firstf", "loc", "hittype", "dpopp", "pivot", "pn", "umphome",
        "ump1b", "ump2b", "ump3b", "umplf", "umprf", "date", "gametype", "pbp",
    )
)

RETROSHEET_CSV_HEADERS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "allplayers.csv": ALLPLAYERS_HEADER,
    "gameinfo.csv": GAMEINFO_HEADER,
    "teamstats.csv": TEAMSTATS_HEADER,
    "batting.csv": BATTING_HEADER,
    "pitching.csv": PITCHING_HEADER,
    "fielding.csv": FIELDING_HEADER,
    "plays.csv": PLAYS_HEADER,
})
RETROSHEET_REQUIRED_FILENAMES = frozenset(RETROSHEET_CSV_HEADERS)


class RetrosheetImportError(ValueError):
    """A Retrosheet payload failed a closed validation boundary."""


class RetrosheetStatType(str, Enum):
    VALUE = "value"
    OFFICIAL = "official"
    LOWER = "lower"
    UPPER = "upper"


class RetrosheetStatSemantics(str, Enum):
    BEST_ESTIMATE = "BEST_ESTIMATE"
    OFFICIAL_DISCREPANCY = "OFFICIAL_DISCREPANCY"
    LOWER_BOUND = "LOWER_BOUND"
    UPPER_BOUND = "UPPER_BOUND"


_STAT_SEMANTICS = MappingProxyType({
    RetrosheetStatType.VALUE: RetrosheetStatSemantics.BEST_ESTIMATE,
    RetrosheetStatType.OFFICIAL: RetrosheetStatSemantics.OFFICIAL_DISCREPANCY,
    RetrosheetStatType.LOWER: RetrosheetStatSemantics.LOWER_BOUND,
    RetrosheetStatType.UPPER: RetrosheetStatSemantics.UPPER_BOUND,
})


class RetrosheetPbpQuality(str, Enum):
    FULL = "FULL"
    DEDUCED = "DEDUCED"
    NONE = "NONE"


class RetrosheetPitcherRole(str, Enum):
    STARTER = "STARTER"
    RELIEVER = "RELIEVER"


@dataclass(frozen=True, slots=True)
class RetrosheetCsvTable:
    filename: str
    header: tuple[str, ...]
    rows: tuple[Mapping[str, str], ...]
    content_checksum: str


@dataclass(frozen=True, slots=True)
class RetrosheetGame:
    raw_game_id: str
    source_game_id: str
    visiting_team: str
    home_team: str
    game_date: date
    season: int
    game_number: int
    doubleheader_id: str | None
    site: str
    gametype: str
    regulation_innings: int | None
    tiebreaker_base: int | None
    designated_hitter_used: bool
    home_team_batted_first: bool
    pbp_quality: RetrosheetPbpQuality
    visiting_runs: int | None
    home_runs: int | None
    box_known: bool
    lineups_known: bool
    batteries_known: bool
    suspended: bool
    completion_date: date | None

    @property
    def final_available_date(self) -> date:
        """Return the first date on which the completed final can be evidence."""

        return self.completion_date or self.game_date

    @property
    def visiting_source_team_id(self) -> str:
        return source_team_id(self.visiting_team)

    @property
    def home_source_team_id(self) -> str:
        return source_team_id(self.home_team)


@dataclass(frozen=True, slots=True)
class RetrosheetStatRecord:
    filename: str
    row_number: int
    raw_game_id: str
    team_id: str
    player_id: str | None
    stattype: RetrosheetStatType
    semantics: RetrosheetStatSemantics
    values: Mapping[str, str]

    @property
    def is_point_estimate(self) -> bool:
        return self.stattype is RetrosheetStatType.VALUE


@dataclass(frozen=True, slots=True)
class RetrosheetLineupEntry:
    raw_game_id: str
    source_game_id: str
    team_id: str
    player_id: str
    source_player_id: str
    batting_order: int
    fielding_positions: tuple[int, ...]
    fielding_positions_source: str = "TEAMSTATS_START_F"
    position_reconciled_from_play_state: bool = False
    teamstats_position_discrepancy: bool = False
    position_proof_play_id: str | None = None
    position_proof_event_date: date | None = None
    evidence_kind: str = "HISTORICAL_ACTUAL"
    temporal_semantics: str = RETROSPECTIVE_ACTUAL_TIMING_SEMANTICS
    timing_risk_label: str = HISTORICAL_ACTUAL_LINEUP_TIMING_RISK

    @property
    def source_team_id(self) -> str:
        return source_team_id(self.team_id)


@dataclass(frozen=True, slots=True)
class RetrosheetPitcherAppearance:
    raw_game_id: str
    source_game_id: str
    team_id: str
    player_id: str
    source_player_id: str
    appearance_sequence: int
    role: RetrosheetPitcherRole
    evidence_kind: str = "HISTORICAL_ACTUAL"
    temporal_semantics: str = RETROSPECTIVE_ACTUAL_TIMING_SEMANTICS
    timing_risk_label: str = ""

    @property
    def source_team_id(self) -> str:
        return source_team_id(self.team_id)


@dataclass(frozen=True, slots=True)
class RetrosheetBaseOutState:
    outs: int
    runner_on_first: str | None
    runner_on_second: str | None
    runner_on_third: str | None
    visiting_score: int
    home_score: int

    @property
    def base_mask(self) -> int:
        return (
            int(self.runner_on_first is not None)
            | (int(self.runner_on_second is not None) << 1)
            | (int(self.runner_on_third is not None) << 2)
        )


@dataclass(frozen=True, slots=True)
class RetrosheetPlayTransition:
    raw_game_id: str
    source_game_id: str
    source_play_id: str
    play_number: int
    half_play_number: int
    event_date: date
    site: str
    event_text: str
    inning: int
    top_bottom: int
    batting_team: str
    pitching_team: str
    batter_id: str | None
    pitcher_id: str | None
    before: RetrosheetBaseOutState
    after: RetrosheetBaseOutState
    runs_scored: int
    pa_terminal_token: str | None
    pa_outcome: PlateAppearanceOutcome | None
    pbp_quality: RetrosheetPbpQuality
    lineup_slots: tuple[str | None, ...]
    lineup_fielding_positions: tuple[int | None, ...]
    defensive_fielders: tuple[str | None, ...]
    pinch_runner_replacement_before: bool = False
    automatic_runner_placement_before: bool = False
    automatic_runner_placement_mode: str | None = None
    automatic_runner_id: str | None = None
    timing_risk_label: str = RETROSPECTIVE_EVENT_TIMING_RISK

    @property
    def batting_source_team_id(self) -> str:
        return source_team_id(self.batting_team)

    @property
    def pitching_source_team_id(self) -> str:
        return source_team_id(self.pitching_team)

    @property
    def batter_source_player_id(self) -> str | None:
        return source_player_id(self.batter_id) if self.batter_id else None

    @property
    def pitcher_source_player_id(self) -> str | None:
        return source_player_id(self.pitcher_id) if self.pitcher_id else None


@dataclass(frozen=True, slots=True)
class RetrosheetImportBundle:
    source_id: str
    source_version_id: str
    schema_version: str
    tables: Mapping[str, RetrosheetCsvTable]
    games: tuple[RetrosheetGame, ...]
    stats: tuple[RetrosheetStatRecord, ...]
    lineups: tuple[RetrosheetLineupEntry, ...]
    pitchers: tuple[RetrosheetPitcherAppearance, ...]
    plays: tuple[RetrosheetPlayTransition, ...]
    pinch_runner_continuity_replacement_count: int
    automatic_runner_placement_count: int
    lineup_position_reconciliation_count: int
    lineup_nonstarting_role_code_occurrence_count: int
    bundle_checksum: str


@dataclass(frozen=True, slots=True)
class _ContinuityResolution:
    pinch_runner_replacement: bool = False
    automatic_runner_mode: str | None = None
    automatic_runner_id: str | None = None


_GAME_ID_PATTERN = re.compile(r"^[A-Z0-9]{3}[0-9]{9}$")
_TEAM_ID_PATTERN = re.compile(r"^[A-Z0-9]{3}$")
_PLAYER_ID_PATTERN = re.compile(r"^[a-z-]{4}[a-z][0-9]{3}$")
_SOURCE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,199}$")


def source_game_id(raw_game_id: str) -> str:
    _validate_game_id(raw_game_id)
    return f"{RETROSHEET_SOURCE_ID}:game:{raw_game_id}"


def source_player_id(raw_player_id: str) -> str:
    _validate_player_id(raw_player_id, "player id")
    return f"{RETROSHEET_SOURCE_ID}:player:{raw_player_id}"


def source_team_id(raw_team_id: str) -> str:
    _validate_team_id(raw_team_id, "team id")
    return f"{RETROSHEET_SOURCE_ID}:team:{raw_team_id}"


def source_play_id(raw_game_id: str, play_number: int) -> str:
    _validate_game_id(raw_game_id)
    if play_number <= 0:
        raise RetrosheetImportError("play number must be positive")
    return f"{RETROSHEET_SOURCE_ID}:play:{raw_game_id}:{play_number}"


def parse_retrosheet_csv(filename: str, text: str) -> RetrosheetCsvTable:
    """Parse one exact Daily Logs contract without interpreting provider provenance."""

    expected = RETROSHEET_CSV_HEADERS.get(filename)
    if expected is None:
        raise RetrosheetImportError(f"unsupported Retrosheet CSV filename: {filename!r}")
    if not isinstance(text, str):
        raise RetrosheetImportError(f"{filename}: CSV payload must be text")

    try:
        parsed = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (csv.Error, UnicodeError) as exc:
        raise RetrosheetImportError(f"{filename}: malformed CSV: {exc}") from exc
    if not parsed:
        raise RetrosheetImportError(f"{filename}: missing header")

    header = list(parsed[0])
    if header:
        header[0] = header[0].removeprefix("\ufeff")
    actual = tuple(header)
    if actual != expected:
        raise RetrosheetImportError(
            f"{filename}: header contract mismatch; expected {expected!r}, received {actual!r}"
        )

    rows: list[Mapping[str, str]] = []
    for row_number, fields in enumerate(parsed[1:], start=2):
        if not fields or (len(fields) == 1 and not fields[0]):
            raise RetrosheetImportError(f"{filename}:{row_number}: blank row is not allowed")
        if len(fields) != len(expected):
            raise RetrosheetImportError(
                f"{filename}:{row_number}: expected {len(expected)} fields, got {len(fields)}"
            )
        rows.append(MappingProxyType(dict(zip(expected, fields, strict=True))))

    return RetrosheetCsvTable(
        filename=filename,
        header=expected,
        rows=tuple(rows),
        content_checksum=sha256(text.encode("utf-8")).hexdigest(),
    )


def parse_stattype(value: object, *, context: str = "stattype") -> RetrosheetStatType:
    token = str(value).strip().lower()
    try:
        return RetrosheetStatType(token)
    except ValueError as exc:
        raise RetrosheetImportError(f"{context}: unmapped stattype {value!r}") from exc


def classify_retrosheet_plate_appearance(
    row: Mapping[str, str],
) -> tuple[str | None, PlateAppearanceOutcome | None]:
    """Map the one-hot Retrosheet PA columns into the frozen Gen2 taxonomy."""

    pa = _binary(row.get("pa"), "plays.pa")
    selected = [
        column
        for column in _PA_TERMINAL_COLUMNS
        if _binary(row.get(column), f"plays.{column}") == 1
    ]
    if pa == 0:
        if selected:
            raise RetrosheetImportError("non-PA play contains a terminal PA token")
        return None, None
    if len(selected) != 1:
        raise RetrosheetImportError(
            f"PA must select exactly one Retrosheet terminal column; selected={selected!r}"
        )

    token = selected[0]
    outcome = _RETROSHEET_PA_CROSSWALK.get(token)
    if outcome is None:
        raise RetrosheetImportError(f"unmapped Retrosheet PA terminal token: {token!r}")
    return token, outcome


_RETROSHEET_PA_CROSSWALK: Mapping[str, PlateAppearanceOutcome] = MappingProxyType({
    "single": PlateAppearanceOutcome.SINGLE,
    "double": PlateAppearanceOutcome.DOUBLE,
    "triple": PlateAppearanceOutcome.TRIPLE,
    "hr": PlateAppearanceOutcome.HOME_RUN,
    "sh": PlateAppearanceOutcome.BIP_OUT,
    "sf": PlateAppearanceOutcome.BIP_OUT,
    "hbp": PlateAppearanceOutcome.WALK_HBP_CI,
    "walk": PlateAppearanceOutcome.WALK_HBP_CI,
    "k": PlateAppearanceOutcome.STRIKEOUT,
    "xi": PlateAppearanceOutcome.WALK_HBP_CI,
    "roe": PlateAppearanceOutcome.OTHER_REACH,
    "fc": PlateAppearanceOutcome.OTHER_REACH,
    "othout": PlateAppearanceOutcome.BIP_OUT,
    # noout is intentionally absent.  It cannot be mapped without inventing a target.
})
RETROSHEET_PA_CROSSWALK = _RETROSHEET_PA_CROSSWALK


def parse_retrosheet_bundle(
    files: Mapping[str, str], *, source_version_id: str
) -> RetrosheetImportBundle:
    """Parse and reconcile all seven Retrosheet Daily Logs tables atomically."""

    _validate_source_version(source_version_id)
    names = frozenset(files)
    if names != RETROSHEET_REQUIRED_FILENAMES:
        missing = sorted(RETROSHEET_REQUIRED_FILENAMES - names)
        extra = sorted(names - RETROSHEET_REQUIRED_FILENAMES)
        raise RetrosheetImportError(
            "Retrosheet bundle must contain exactly seven contracts; "
            f"missing={missing}, extra={extra}"
        )

    tables = {name: parse_retrosheet_csv(name, files[name]) for name in sorted(files)}
    games = _parse_games(tables["gameinfo.csv"])
    games_by_id = {game.raw_game_id: game for game in games}
    roster = _parse_roster(tables["allplayers.csv"])
    stat_records = _parse_stat_records(tables, games_by_id, roster)
    team_value_rows = _team_value_rows(stat_records, games_by_id)
    lineups = _derive_lineups(team_value_rows, games_by_id, roster)
    pitchers = _derive_pitchers(stat_records, team_value_rows, games_by_id, roster)
    pinch_runner_replacements = _derive_pinch_runner_replacements(stat_records)
    plays = _parse_plays(
        tables["plays.csv"],
        games_by_id,
        roster,
        pinch_runner_replacements,
    )
    lineups = _reconcile_positionless_lineup_participation(
        lineups,
        plays,
        games_by_id,
        stat_records,
    )
    _validate_score_totals(games_by_id, team_value_rows, plays)

    table_view = MappingProxyType(dict(sorted(tables.items())))
    checksum_payload = {
        "schema_version": RETROSHEET_SCHEMA_VERSION,
        "source_id": RETROSHEET_SOURCE_ID,
        "source_version_id": source_version_id,
        "tables": {name: table.content_checksum for name, table in table_view.items()},
    }
    checksum = sha256(_canonical_json(checksum_payload).encode("utf-8")).hexdigest()
    return RetrosheetImportBundle(
        source_id=RETROSHEET_SOURCE_ID,
        source_version_id=source_version_id,
        schema_version=RETROSHEET_SCHEMA_VERSION,
        tables=table_view,
        games=games,
        stats=stat_records,
        lineups=lineups,
        pitchers=pitchers,
        plays=plays,
        pinch_runner_continuity_replacement_count=sum(
            play.pinch_runner_replacement_before for play in plays
        ),
        automatic_runner_placement_count=sum(
            play.automatic_runner_placement_before for play in plays
        ),
        lineup_position_reconciliation_count=sum(
            entry.position_reconciled_from_play_state for entry in lineups
        ),
        lineup_nonstarting_role_code_occurrence_count=sum(
            position in {11, 12}
            for play in plays
            for position in play.lineup_fielding_positions
        ),
        bundle_checksum=checksum,
    )


def _parse_games(table: RetrosheetCsvTable) -> tuple[RetrosheetGame, ...]:
    games: list[RetrosheetGame] = []
    seen: set[str] = set()
    groups: dict[tuple[str, date], list[RetrosheetGame]] = defaultdict(list)
    for row_number, row in enumerate(table.rows, start=2):
        context = f"gameinfo.csv:{row_number}"
        gid = row["gid"].strip()
        _validate_game_id(gid, context)
        if gid in seen:
            raise RetrosheetImportError(f"{context}: duplicate gid {gid!r}")
        seen.add(gid)

        visiting = row["visteam"].strip()
        home = row["hometeam"].strip()
        _validate_team_id(visiting, f"{context}.visteam")
        _validate_team_id(home, f"{context}.hometeam")
        if visiting == home:
            raise RetrosheetImportError(f"{context}: visiting and home teams must differ")
        game_date = _date(row["date"], f"{context}.date")
        game_number = _integer(row["number"], f"{context}.number", minimum=0, maximum=9)
        season = _integer(row["season"], f"{context}.season", minimum=1800, maximum=2200)
        expected_gid = f"{home}{game_date:%Y%m%d}{game_number}"
        if gid != expected_gid:
            raise RetrosheetImportError(
                f"{context}: gid {gid!r} does not match home/date/number {expected_gid!r}"
            )
        if season != game_date.year:
            raise RetrosheetImportError(f"{context}: season does not match game date")

        pbp_quality = _game_pbp_quality(row["pbp"], f"{context}.pbp")
        visiting_runs = _optional_integer(row["vruns"], f"{context}.vruns", minimum=0)
        home_runs = _optional_integer(row["hruns"], f"{context}.hruns", minimum=0)
        regulation_innings = _optional_integer(
            row["innings"], f"{context}.innings", minimum=1, maximum=99
        )
        tiebreaker_base = _optional_integer(
            row["tiebreaker"], f"{context}.tiebreaker", minimum=1, maximum=3
        )
        box_known = _availability(row["box"], f"{context}.box")
        lineups_known = _availability(row["lineups"], f"{context}.lineups")
        batteries_known = _batteries_known(row["batteries"], f"{context}.batteries")
        completion_date = _suspension_completion_date(
            row["suspend"], game_date, f"{context}.suspend"
        )
        suspended = completion_date is not None
        if suspended and (
            visiting_runs is None
            or home_runs is None
            or pbp_quality is RetrosheetPbpQuality.NONE
            or not box_known
            or not lineups_known
            or not batteries_known
            or row["batteries"].strip().lower() != "both"
        ):
            raise RetrosheetImportError(
                f"{context}: completed suspended game requires final scores, FULL/DEDUCED "
                "PBP, box, lineups, and both batteries"
            )
        game = RetrosheetGame(
            raw_game_id=gid,
            source_game_id=source_game_id(gid),
            visiting_team=visiting,
            home_team=home,
            game_date=game_date,
            season=season,
            game_number=game_number,
            doubleheader_id=None,
            site=_required(row["site"], f"{context}.site"),
            gametype=_required(row["gametype"], f"{context}.gametype"),
            regulation_innings=regulation_innings,
            tiebreaker_base=tiebreaker_base,
            designated_hitter_used=_yes_no(row["usedh"], f"{context}.usedh"),
            home_team_batted_first=_yes_no(row["htbf"], f"{context}.htbf"),
            pbp_quality=pbp_quality,
            visiting_runs=visiting_runs,
            home_runs=home_runs,
            box_known=box_known,
            lineups_known=lineups_known,
            batteries_known=batteries_known,
            suspended=suspended,
            completion_date=completion_date,
        )
        games.append(game)
        groups[(home, game_date)].append(game)

    replacements: dict[str, RetrosheetGame] = {}
    for (home, game_date), members in groups.items():
        numbers = [game.game_number for game in members]
        if len(numbers) != len(set(numbers)):
            raise RetrosheetImportError(f"duplicate game number for {home} on {game_date}")
        if 0 in numbers and len(numbers) > 1:
            raise RetrosheetImportError(
                f"single-game number 0 cannot share {home} {game_date} with another game"
            )
        if any(number > 0 for number in numbers):
            set_id = f"{RETROSHEET_SOURCE_ID}:doubleheader:{home}:{game_date:%Y%m%d}"
            for game in members:
                replacements[game.raw_game_id] = replace(game, doubleheader_id=set_id)

    return tuple(replacements.get(game.raw_game_id, game) for game in games)


def _parse_roster(table: RetrosheetCsvTable) -> frozenset[tuple[str, str, int]]:
    roster: set[tuple[str, str, int]] = set()
    for row_number, row in enumerate(table.rows, start=2):
        context = f"allplayers.csv:{row_number}"
        player = row["id"].strip()
        team = row["team"].strip()
        season = _integer(row["season"], f"{context}.season", minimum=1800, maximum=2200)
        _validate_player_id(player, f"{context}.id")
        _validate_team_id(team, f"{context}.team")
        key = (player, team, season)
        if key in roster:
            raise RetrosheetImportError(f"{context}: duplicate player/team/season roster row")
        roster.add(key)
    return frozenset(roster)


def _parse_stat_records(
    tables: Mapping[str, RetrosheetCsvTable],
    games: Mapping[str, RetrosheetGame],
    roster: frozenset[tuple[str, str, int]],
) -> tuple[RetrosheetStatRecord, ...]:
    result: list[RetrosheetStatRecord] = []
    for filename in ("teamstats.csv", "batting.csv", "pitching.csv", "fielding.csv"):
        table = tables[filename]
        for row_number, row in enumerate(table.rows, start=2):
            context = f"{filename}:{row_number}"
            game = _known_game(row["gid"], games, context)
            team = row["team"].strip()
            _validate_team_for_game(team, game, context)
            _validate_stat_context(row, game, team, context)
            stattype = parse_stattype(row["stattype"], context=f"{context}.stattype")
            player: str | None = None
            if filename != "teamstats.csv":
                player = row["id"].strip()
                _validate_rostered_player(player, team, game.season, roster, f"{context}.id")
            result.append(
                RetrosheetStatRecord(
                    filename=filename,
                    row_number=row_number,
                    raw_game_id=game.raw_game_id,
                    team_id=team,
                    player_id=player,
                    stattype=stattype,
                    semantics=_STAT_SEMANTICS[stattype],
                    values=row,
                )
            )
    return tuple(result)


def _derive_pinch_runner_replacements(
    stats: tuple[RetrosheetStatRecord, ...],
) -> frozenset[tuple[str, str, str, str]]:
    """Return only substitutions proved by adjacent batting value rows.

    Daily Logs do not emit a separate play row for every between-play pinch
    runner.  A replacement is therefore eligible for continuity reconciliation
    only when the batting value table contains one unambiguous outgoing and
    incoming player in the same game, team, and lineup slot, the incoming
    sequence immediately follows the outgoing sequence, and the incoming row
    carries the explicit ``pr=1`` flag.
    """

    by_slot: dict[
        tuple[str, str, int],
        dict[int, list[RetrosheetStatRecord]],
    ] = defaultdict(lambda: defaultdict(list))
    for record in stats:
        if record.filename != "batting.csv" or not record.is_point_estimate:
            continue
        context = f"batting.csv:{record.row_number}"
        lineup_position = _optional_integer(
            record.values["b_lp"],
            f"{context}.b_lp",
            minimum=1,
            maximum=9,
        )
        sequence = _optional_integer(
            record.values["b_seq"],
            f"{context}.b_seq",
            minimum=1,
        )
        if lineup_position is None or sequence is None:
            continue
        by_slot[(record.raw_game_id, record.team_id, lineup_position)][sequence].append(
            record
        )

    replacements: set[tuple[str, str, str, str]] = set()
    for (gid, team, _lineup_position), by_sequence in by_slot.items():
        for sequence, incoming_rows in by_sequence.items():
            if len(incoming_rows) != 1 or sequence <= 1:
                continue
            incoming = incoming_rows[0]
            incoming_pr = _optional_binary(
                incoming.values["pr"],
                f"batting.csv:{incoming.row_number}.pr",
            )
            outgoing_rows = by_sequence.get(sequence - 1, ())
            if incoming_pr != 1 or len(outgoing_rows) != 1:
                continue
            outgoing = outgoing_rows[0]
            assert outgoing.player_id is not None
            assert incoming.player_id is not None
            if outgoing.player_id == incoming.player_id:
                continue
            replacements.add((gid, team, outgoing.player_id, incoming.player_id))
    return frozenset(replacements)


def _team_value_rows(
    stats: tuple[RetrosheetStatRecord, ...], games: Mapping[str, RetrosheetGame]
) -> Mapping[tuple[str, str], RetrosheetStatRecord]:
    rows: dict[tuple[str, str], RetrosheetStatRecord] = {}
    for record in stats:
        if record.filename != "teamstats.csv" or not record.is_point_estimate:
            continue
        key = (record.raw_game_id, record.team_id)
        if key in rows:
            raise RetrosheetImportError(f"duplicate value teamstats row for {key!r}")
        rows[key] = record
    for game in games.values():
        for team in (game.visiting_team, game.home_team):
            if (game.raw_game_id, team) not in rows:
                raise RetrosheetImportError(
                    f"missing value teamstats row for {game.raw_game_id}/{team}"
                )
    return MappingProxyType(rows)


def _derive_lineups(
    team_rows: Mapping[tuple[str, str], RetrosheetStatRecord],
    games: Mapping[str, RetrosheetGame],
    roster: frozenset[tuple[str, str, int]],
) -> tuple[RetrosheetLineupEntry, ...]:
    lineups: list[RetrosheetLineupEntry] = []
    for (gid, team), record in sorted(team_rows.items()):
        game = games[gid]
        row = record.values
        players = [row[f"start_l{position}"].strip() for position in range(1, 10)]
        present = [player for player in players if player]
        if game.lineups_known and len(present) != 9:
            raise RetrosheetImportError(f"{gid}/{team}: known lineup is not complete")
        if not game.lineups_known and present:
            raise RetrosheetImportError(
                f"{gid}/{team}: lineup values contradict unknown lineup flag"
            )
        if len(present) != len(set(present)):
            raise RetrosheetImportError(f"{gid}/{team}: duplicate player in starting batting order")

        positions_by_player: dict[str, list[int]] = defaultdict(list)
        for position in range(1, 11):
            player = row[f"start_f{position}"].strip()
            if not player:
                continue
            _validate_rostered_player(player, team, game.season, roster, f"{gid}.start_f{position}")
            positions_by_player[player].append(position)

        for batting_order, player in enumerate(players, start=1):
            if not player:
                continue
            _validate_rostered_player(
                player, team, game.season, roster, f"{gid}.start_l{batting_order}"
            )
            positions = tuple(positions_by_player.get(player, ()))
            lineups.append(
                RetrosheetLineupEntry(
                    raw_game_id=gid,
                    source_game_id=game.source_game_id,
                    team_id=team,
                    player_id=player,
                    source_player_id=source_player_id(player),
                    batting_order=batting_order,
                    fielding_positions=positions,
                    fielding_positions_source=(
                        "TEAMSTATS_START_F" if positions else "UNRESOLVED"
                    ),
                )
            )
    return tuple(lineups)


def _reconcile_positionless_lineup_participation(
    lineups: tuple[RetrosheetLineupEntry, ...],
    plays: tuple[RetrosheetPlayTransition, ...],
    games: Mapping[str, RetrosheetGame],
    stats: tuple[RetrosheetStatRecord, ...],
) -> tuple[RetrosheetLineupEntry, ...]:
    """Resolve only source-proven positionless announced starters.

    Current Daily Logs can list the announced batting-order starter while
    ``start_f*`` identifies the eventual replacement who appears in the final
    box.  A real batting appearance remains sufficient participation proof but
    does not manufacture a defensive position.  A zero-stat starter is accepted
    only when its exact ``b_lp``/``b_seq`` value row is zero and the first
    original-date PBP state proves either the player's batting slot/``lf*`` role
    or one exact defensive ``f2``-``f9`` position.  Considering both sides of
    that first state is necessary for a home starter replaced between an initial
    no-play defensive record and the home team's first plate appearance.
    """

    batting_appearances = {
        (play.raw_game_id, play.batting_team, play.batter_id)
        for play in plays
        if play.batter_id is not None
    }
    batting_value_rows: dict[tuple[str, str, str], list[RetrosheetStatRecord]] = (
        defaultdict(list)
    )
    for record in stats:
        if (
            record.filename == "batting.csv"
            and record.is_point_estimate
            and record.player_id is not None
        ):
            batting_value_rows[
                (record.raw_game_id, record.team_id, record.player_id)
            ].append(record)

    reconciled: list[RetrosheetLineupEntry] = []
    for entry in lineups:
        if entry.fielding_positions:
            reconciled.append(entry)
            continue
        key = (entry.raw_game_id, entry.team_id, entry.player_id)
        if key in batting_appearances:
            reconciled.append(
                replace(
                    entry,
                    fielding_positions_source="PLAY_BATTER_PARTICIPATION_NO_POSITION",
                )
            )
            continue

        zero_stat_rows = [
            record
            for record in batting_value_rows.get(key, ())
            if _is_exact_zero_stat_starting_batting_row(record, entry)
        ]
        if len(zero_stat_rows) != 1:
            raise RetrosheetImportError(
                f"{entry.raw_game_id}/{entry.team_id}: positionless lineup player "
                f"{entry.player_id!r} lacks play-by-play participation proof or one "
                "exact zero-stat starting batting row"
            )
        game = games[entry.raw_game_id]
        original_states = sorted(
            (
                play
                for play in plays
                if play.raw_game_id == entry.raw_game_id
                and play.event_date == game.game_date
            ),
            key=lambda play: play.play_number,
        )
        if not original_states:
            raise RetrosheetImportError(
                f"{entry.raw_game_id}/{entry.team_id}: zero-stat lineup player "
                f"{entry.player_id!r} lacks original-date PBP state proof"
            )
        proof = original_states[0]
        proof_source: str
        if proof.batting_team == entry.team_id:
            if not any(player is not None for player in proof.lineup_slots):
                raise RetrosheetImportError(
                    f"{entry.raw_game_id}/{entry.team_id}: zero-stat lineup player "
                    f"{entry.player_id!r} lacks original-date PBP lineup proof"
                )
            observed_slots = tuple(
                slot
                for slot, player in enumerate(proof.lineup_slots, start=1)
                if player == entry.player_id
            )
            if observed_slots != (entry.batting_order,):
                raise RetrosheetImportError(
                    f"{entry.raw_game_id}/{entry.team_id}: zero-stat lineup player "
                    f"{entry.player_id!r} has conflicting PBP lineup slots "
                    f"{observed_slots!r}; expected {(entry.batting_order,)!r}"
                )
            position = proof.lineup_fielding_positions[entry.batting_order - 1]
            proof_source = "PLAYS_INITIAL_ORIGINAL_DATE_LINEUP_STATE"
        else:
            assert proof.pitching_team == entry.team_id
            if not any(player is not None for player in proof.defensive_fielders):
                raise RetrosheetImportError(
                    f"{entry.raw_game_id}/{entry.team_id}: zero-stat lineup player "
                    f"{entry.player_id!r} lacks original-date PBP defensive proof"
                )
            observed_positions = tuple(
                position
                for position, player in enumerate(proof.defensive_fielders, start=1)
                if player == entry.player_id
            )
            if len(observed_positions) != 1:
                raise RetrosheetImportError(
                    f"{entry.raw_game_id}/{entry.team_id}: zero-stat lineup player "
                    f"{entry.player_id!r} has conflicting PBP defensive positions "
                    f"{observed_positions!r}; expected one exact position"
                )
            position = observed_positions[0]
            proof_source = "PLAYS_INITIAL_ORIGINAL_DATE_DEFENSIVE_STATE"
        valid_starting_position = bool(
            position is not None
            and (
                1 <= position <= 9
                or (position == 10 and game.designated_hitter_used)
            )
        )
        if not valid_starting_position:
            raise RetrosheetImportError(
                f"{entry.raw_game_id}/{entry.team_id}: zero-stat lineup player "
                f"{entry.player_id!r} lacks a valid original-date PBP fielding position"
            )
        assert position is not None
        reconciled.append(
            replace(
                entry,
                fielding_positions=(position,),
                fielding_positions_source=proof_source,
                position_reconciled_from_play_state=True,
                teamstats_position_discrepancy=True,
                position_proof_play_id=proof.source_play_id,
                position_proof_event_date=proof.event_date,
            )
        )
    return tuple(reconciled)


def _is_exact_zero_stat_starting_batting_row(
    record: RetrosheetStatRecord,
    entry: RetrosheetLineupEntry,
) -> bool:
    row = record.values
    try:
        if _integer(row["b_lp"], "batting.b_lp", minimum=1, maximum=9) != entry.batting_order:
            return False
        if _integer(row["b_seq"], "batting.b_seq", minimum=1) != 1:
            return False
        for column in BATTING_HEADER:
            if not column.startswith("b_") or column in {"b_lp", "b_seq"}:
                continue
            value = _optional_integer(row[column], f"batting.{column}", minimum=0)
            if value not in {None, 0}:
                return False
    except RetrosheetImportError:
        return False
    return True


def _derive_pitchers(
    stats: tuple[RetrosheetStatRecord, ...],
    team_rows: Mapping[tuple[str, str], RetrosheetStatRecord],
    games: Mapping[str, RetrosheetGame],
    roster: frozenset[tuple[str, str, int]],
) -> tuple[RetrosheetPitcherAppearance, ...]:
    grouped: dict[tuple[str, str], list[RetrosheetStatRecord]] = defaultdict(list)
    for record in stats:
        if record.filename == "pitching.csv" and record.is_point_estimate:
            grouped[(record.raw_game_id, record.team_id)].append(record)

    appearances: list[RetrosheetPitcherAppearance] = []
    for game in games.values():
        for team in (game.visiting_team, game.home_team):
            key = (game.raw_game_id, team)
            rows = grouped.get(key, [])
            if game.batteries_known and not rows:
                raise RetrosheetImportError(
                    f"{game.raw_game_id}/{team}: known battery lacks pitching rows"
                )
            if not rows:
                continue
            sequenced: list[tuple[int, RetrosheetStatRecord]] = []
            for record in rows:
                sequence = _integer(
                    record.values["p_seq"],
                    f"pitching.csv:{record.row_number}.p_seq",
                    minimum=1,
                )
                sequenced.append((sequence, record))
            sequenced.sort(key=lambda item: item[0])
            observed = [sequence for sequence, _ in sequenced]
            if observed != list(range(1, len(observed) + 1)):
                raise RetrosheetImportError(
                    f"{game.raw_game_id}/{team}: pitching sequence must be unique and contiguous"
                )

            expected_starter = team_rows[key].values["start_f1"].strip()
            if game.batteries_known and not expected_starter:
                raise RetrosheetImportError(
                    f"{game.raw_game_id}/{team}: known battery lacks teamstats starting pitcher"
                )
            actual_starter = sequenced[0][1].player_id
            if expected_starter and expected_starter != actual_starter:
                raise RetrosheetImportError(
                    f"{game.raw_game_id}/{team}: teamstats and pitching starters disagree"
                )
            for sequence, record in sequenced:
                assert record.player_id is not None
                _validate_rostered_player(
                    record.player_id,
                    team,
                    game.season,
                    roster,
                    f"pitching.csv:{record.row_number}.id",
                )
                gs = _optional_binary(
                    record.values["p_gs"],
                    f"pitching.csv:{record.row_number}.p_gs",
                )
                is_starter = sequence == 1
                if gs is not None and bool(gs) != is_starter:
                    raise RetrosheetImportError(
                        f"pitching.csv:{record.row_number}: p_gs conflicts with p_seq"
                    )
                role = (
                    RetrosheetPitcherRole.STARTER
                    if is_starter
                    else RetrosheetPitcherRole.RELIEVER
                )
                risk = (
                    HISTORICAL_ACTUAL_STARTER_TIMING_RISK
                    if is_starter
                    else HISTORICAL_ACTUAL_RELIEVER_TIMING_RISK
                )
                appearances.append(
                    RetrosheetPitcherAppearance(
                        raw_game_id=game.raw_game_id,
                        source_game_id=game.source_game_id,
                        team_id=team,
                        player_id=record.player_id,
                        source_player_id=source_player_id(record.player_id),
                        appearance_sequence=sequence,
                        role=role,
                        timing_risk_label=risk,
                    )
                )
    return tuple(appearances)


def _parse_plays(
    table: RetrosheetCsvTable,
    games: Mapping[str, RetrosheetGame],
    roster: frozenset[tuple[str, str, int]],
    pinch_runner_replacements: frozenset[tuple[str, str, str, str]],
) -> tuple[RetrosheetPlayTransition, ...]:
    grouped: dict[str, list[tuple[int, Mapping[str, str]]]] = defaultdict(list)
    for row_number, row in enumerate(table.rows, start=2):
        game = _known_game(row["gid"], games, f"plays.csv:{row_number}")
        grouped[game.raw_game_id].append((row_number, row))

    transitions: list[RetrosheetPlayTransition] = []
    for gid, indexed_rows in grouped.items():
        game = games[gid]
        if game.pbp_quality is RetrosheetPbpQuality.NONE:
            raise RetrosheetImportError(f"{gid}: plays exist but gameinfo says PBP is unavailable")
        original_segment_plays = 0
        completion_segment_plays = 0
        completion_sites: set[str] = set()
        previous: RetrosheetPlayTransition | None = None
        for ordinal, (row_number, row) in enumerate(indexed_rows, start=1):
            context = f"plays.csv:{row_number}"
            play_number = _integer(row["pn"], f"{context}.pn", minimum=1)
            if play_number != ordinal:
                raise RetrosheetImportError(
                    f"{gid}: play numbers must start at 1 and be contiguous"
                )
            inning = _integer(row["inning"], f"{context}.inning", minimum=1)
            top_bottom = _binary(row["top_bot"], f"{context}.top_bot")
            vis_home = _binary(row["vis_home"], f"{context}.vis_home")
            expected_vis_home = top_bottom ^ int(game.home_team_batted_first)
            if vis_home != expected_vis_home:
                raise RetrosheetImportError(f"{context}: top_bot/vis_home conflicts with htbf")
            batting_team = row["batteam"].strip()
            pitching_team = row["pitteam"].strip()
            expected_batting = game.home_team if vis_home else game.visiting_team
            expected_pitching = game.visiting_team if vis_home else game.home_team
            if (batting_team, pitching_team) != (expected_batting, expected_pitching):
                raise RetrosheetImportError(f"{context}: batting/pitching teams conflict with game")

            play_quality = _play_pbp_quality(row["pbp"], f"{context}.pbp")
            if play_quality is not game.pbp_quality:
                raise RetrosheetImportError(f"{context}: PBP quality conflicts with gameinfo")
            event_date = _date(row["date"], f"{context}.date")
            event_site = _required(row["site"], f"{context}.site")
            if not game.suspended:
                if event_date != game.game_date or event_site != game.site:
                    raise RetrosheetImportError(
                        f"{context}: event date/site differs from a non-suspended game"
                    )
            else:
                assert game.completion_date is not None
                if event_date == game.game_date:
                    original_segment_plays += 1
                    if event_site != game.site:
                        raise RetrosheetImportError(
                            f"{context}: original suspended-game segment site conflicts "
                            "with gameinfo"
                        )
                elif event_date == game.completion_date:
                    completion_segment_plays += 1
                    completion_sites.add(event_site)
                else:
                    raise RetrosheetImportError(
                        f"{context}: suspended-game event date must be the original or "
                        "exact completion date"
                    )
            if row["gametype"].strip() != game.gametype:
                raise RetrosheetImportError(f"{context}: gametype conflicts with gameinfo")

            batter = _optional_rostered_player(
                row["batter"], batting_team, game.season, roster, f"{context}.batter"
            )
            pitcher = _optional_rostered_player(
                row["pitcher"], pitching_team, game.season, roster, f"{context}.pitcher"
            )
            terminal, outcome = classify_retrosheet_plate_appearance(row)
            if terminal is not None and (batter is None or pitcher is None):
                raise RetrosheetImportError(f"{context}: terminal PA requires batter and pitcher")
            lineup_slots = tuple(
                row[f"l{slot}"].strip() or None for slot in range(1, 10)
            )
            lineup_fielding_positions = tuple(
                _optional_integer(
                    row[f"lf{slot}"],
                    f"{context}.lf{slot}",
                    minimum=1,
                    # Retrosheet lineup-state role codes include PH (11) and PR
                    # (12); they are preserved here but cannot prove a starting
                    # defensive position below.
                    maximum=12,
                )
                for slot in range(1, 10)
            )
            defensive_fielders = (
                pitcher,
                *(row[f"f{position}"].strip() or None for position in range(2, 10)),
            )

            before = _base_out_state(row, "pre", context)
            _validate_state_runners(before, batting_team, game.season, roster, context)
            run_ids = tuple(row[column].strip() for column in ("run_b", "run1", "run2", "run3"))
            for column, runner in zip(("run_b", "run1", "run2", "run3"), run_ids, strict=True):
                if runner:
                    _validate_rostered_player(
                        runner,
                        batting_team,
                        game.season,
                        roster,
                        f"{context}.{column}",
                    )
            scoring_runners = [runner for runner in run_ids if runner]
            if len(scoring_runners) != len(set(scoring_runners)):
                raise RetrosheetImportError(f"{context}: same runner scored more than once")
            expected_scorers = (
                batter,
                before.runner_on_first,
                before.runner_on_second,
                before.runner_on_third,
            )
            for column, runner, expected_runner in zip(
                ("run_b", "run1", "run2", "run3"),
                run_ids,
                expected_scorers,
                strict=True,
            ):
                if runner and runner != expected_runner:
                    raise RetrosheetImportError(
                        f"{context}.{column}: scoring identity conflicts with pre-play state"
                    )
            runs = _integer(row["runs"], f"{context}.runs", minimum=0, maximum=4)
            if runs != sum(bool(runner) for runner in run_ids):
                raise RetrosheetImportError(
                    f"{context}: runs does not equal identified scoring runners"
                )
            after = _base_out_state(
                row,
                "post",
                context,
                visiting_score=before.visiting_score + (runs if vis_home == 0 else 0),
                home_score=before.home_score + (runs if vis_home == 1 else 0),
            )
            _validate_state_runners(after, batting_team, game.season, roster, context)
            after_runners = {
                runner
                for runner in (
                    after.runner_on_first,
                    after.runner_on_second,
                    after.runner_on_third,
                )
                if runner
            }
            if after_runners.intersection(scoring_runners):
                raise RetrosheetImportError(
                    f"{context}: a scoring runner cannot remain on base after the play"
                )
            _validate_play_reference_ids(
                row,
                batting_team=batting_team,
                pitching_team=pitching_team,
                season=game.season,
                roster=roster,
                context=context,
            )
            if after.outs < before.outs:
                raise RetrosheetImportError(f"{context}: outs cannot decrease within a play")

            half_play_number = (
                previous.half_play_number + 1
                if previous is not None
                and (previous.inning, previous.top_bottom) == (inning, top_bottom)
                else 1
            )
            transition = RetrosheetPlayTransition(
                raw_game_id=gid,
                source_game_id=game.source_game_id,
                source_play_id=source_play_id(gid, play_number),
                play_number=play_number,
                half_play_number=half_play_number,
                event_date=event_date,
                site=event_site,
                event_text=_required(row["event"], f"{context}.event"),
                inning=inning,
                top_bottom=top_bottom,
                batting_team=batting_team,
                pitching_team=pitching_team,
                batter_id=batter,
                pitcher_id=pitcher,
                before=before,
                after=after,
                runs_scored=runs,
                pa_terminal_token=terminal,
                pa_outcome=outcome,
                pbp_quality=play_quality,
                lineup_slots=lineup_slots,
                lineup_fielding_positions=lineup_fielding_positions,
                defensive_fielders=defensive_fielders,
            )
            continuity = _validate_play_continuity(
                previous,
                transition,
                game,
                context,
                pinch_runner_replacements,
            )
            if continuity.pinch_runner_replacement or continuity.automatic_runner_mode:
                transition = replace(
                    transition,
                    pinch_runner_replacement_before=continuity.pinch_runner_replacement,
                    automatic_runner_placement_before=(
                        continuity.automatic_runner_mode is not None
                    ),
                    automatic_runner_placement_mode=continuity.automatic_runner_mode,
                    automatic_runner_id=continuity.automatic_runner_id,
                )
            transitions.append(transition)
            previous = transition
        if game.suspended:
            if original_segment_plays == 0 or completion_segment_plays == 0:
                raise RetrosheetImportError(
                    f"{gid}: completed suspended game requires PBP on both the original "
                    "and exact completion dates"
                )
            if len(completion_sites) != 1:
                raise RetrosheetImportError(
                    f"{gid}: completion-date plays must use one consistent site"
                )
    for game in games.values():
        if game.pbp_quality is not RetrosheetPbpQuality.NONE and game.raw_game_id not in grouped:
            raise RetrosheetImportError(
                f"{game.raw_game_id}: gameinfo declares PBP but plays.csv has no rows"
            )
    return tuple(transitions)


def _base_out_state(
    row: Mapping[str, str],
    when: str,
    context: str,
    *,
    visiting_score: int | None = None,
    home_score: int | None = None,
) -> RetrosheetBaseOutState:
    outs = _integer(row[f"outs_{when}"], f"{context}.outs_{when}", minimum=0, maximum=3)
    runners: list[str | None] = []
    for base in range(1, 4):
        value = row[f"br{base}_{when}"].strip()
        if value:
            _validate_player_id(value, f"{context}.br{base}_{when}")
            runners.append(value)
        else:
            runners.append(None)
    occupied = [runner for runner in runners if runner is not None]
    if len(occupied) != len(set(occupied)):
        raise RetrosheetImportError(f"{context}: same runner occupies multiple bases {when}")
    if when == "post" and outs == 3 and occupied:
        raise RetrosheetImportError(f"{context}: bases must be empty after the third out")
    if visiting_score is None:
        visiting_score = _integer(row["score_v"], f"{context}.score_v", minimum=0)
    if home_score is None:
        home_score = _integer(row["score_h"], f"{context}.score_h", minimum=0)
    return RetrosheetBaseOutState(
        outs=outs,
        runner_on_first=runners[0],
        runner_on_second=runners[1],
        runner_on_third=runners[2],
        visiting_score=visiting_score,
        home_score=home_score,
    )


def _validate_state_runners(
    state: RetrosheetBaseOutState,
    batting_team: str,
    season: int,
    roster: frozenset[tuple[str, str, int]],
    context: str,
) -> None:
    for base, runner in enumerate(
        (state.runner_on_first, state.runner_on_second, state.runner_on_third),
        start=1,
    ):
        if runner:
            _validate_rostered_player(
                runner,
                batting_team,
                season,
                roster,
                f"{context}.runner_on_base_{base}",
            )


def _validate_play_reference_ids(
    row: Mapping[str, str],
    *,
    batting_team: str,
    pitching_team: str,
    season: int,
    roster: frozenset[tuple[str, str, int]],
    context: str,
) -> None:
    batting_columns = (
        *(f"lob_id{base}" for base in range(1, 4)),
        *(f"l{position}" for position in range(1, 10)),
    )
    pitching_columns = (
        *(f"pr{base}_{when}" for when in ("pre", "post") for base in range(1, 4)),
        "prun_b",
        "prun1",
        "prun2",
        "prun3",
        *(f"f{position}" for position in range(2, 10)),
    )
    for team, columns in (
        (batting_team, batting_columns),
        (pitching_team, pitching_columns),
    ):
        for column in columns:
            player = row[column].strip()
            if player:
                _validate_rostered_player(
                    player,
                    team,
                    season,
                    roster,
                    f"{context}.{column}",
                )


def _validate_play_continuity(
    previous: RetrosheetPlayTransition | None,
    current: RetrosheetPlayTransition,
    game: RetrosheetGame,
    context: str,
    pinch_runner_replacements: frozenset[tuple[str, str, str, str]],
) -> _ContinuityResolution:
    if previous is None:
        if (current.inning, current.top_bottom) != (1, 0):
            raise RetrosheetImportError(f"{context}: first play must begin in the top of the first")
        if (current.before.visiting_score, current.before.home_score) != (0, 0):
            raise RetrosheetImportError(f"{context}: first play must begin scoreless")
        if current.before.base_mask:
            raise RetrosheetImportError(f"{context}: first play must begin with empty bases")
        return _ContinuityResolution()
    if current.event_date < previous.event_date:
        raise RetrosheetImportError(f"{context}: play event date moved backwards")
    if (current.inning, current.top_bottom) < (previous.inning, previous.top_bottom):
        raise RetrosheetImportError(f"{context}: inning/half order moved backwards")
    if (
        current.before.visiting_score,
        current.before.home_score,
    ) != (previous.after.visiting_score, previous.after.home_score):
        raise RetrosheetImportError(f"{context}: score is not conserved between plays")
    same_half = (current.inning, current.top_bottom) == (previous.inning, previous.top_bottom)
    if same_half:
        before_bases = (
            current.before.runner_on_first,
            current.before.runner_on_second,
            current.before.runner_on_third,
        )
        after_bases = (
            previous.after.runner_on_first,
            previous.after.runner_on_second,
            previous.after.runner_on_third,
        )
        outs_match = current.before.outs == previous.after.outs
        if not outs_match or before_bases != after_bases:
            if outs_match and _is_proven_pinch_runner_replacement(
                previous,
                current,
                after_bases,
                before_bases,
                pinch_runner_replacements,
            ):
                return _ContinuityResolution(pinch_runner_replacement=True)
            automatic_runner_id = _proven_automatic_runner_after_np(
                previous, current, game
            )
            if automatic_runner_id is not None:
                return _ContinuityResolution(
                    automatic_runner_mode=AUTOMATIC_RUNNER_AFTER_NP_BRIDGE,
                    automatic_runner_id=automatic_runner_id,
                )
            raise RetrosheetImportError(f"{context}: base/out state is not conserved between plays")
    else:
        expected_half = (
            (previous.inning, 1)
            if previous.top_bottom == 0
            else (previous.inning + 1, 0)
        )
        if (current.inning, current.top_bottom) != expected_half:
            raise RetrosheetImportError(f"{context}: half-inning sequence is not contiguous")
        if previous.after.outs != 3:
            raise RetrosheetImportError(f"{context}: prior half-inning did not record three outs")
        if current.before.outs != 0:
            raise RetrosheetImportError(f"{context}: a new half-inning must reset outs to zero")
        if current.before.base_mask:
            automatic_runner_id = _configured_automatic_runner_id(game, current)
            if automatic_runner_id is None or current.half_play_number != 1:
                raise RetrosheetImportError(
                    f"{context}: unproven automatic-runner placement at start of half"
                )
            return _ContinuityResolution(
                automatic_runner_mode=AUTOMATIC_RUNNER_DIRECT_FIRST_PLAY,
                automatic_runner_id=automatic_runner_id,
            )
    return _ContinuityResolution()


def _configured_automatic_runner_id(
    game: RetrosheetGame,
    current: RetrosheetPlayTransition,
) -> str | None:
    """Return the exact source runner only under the declared extra-inning rule."""

    if (
        game.tiebreaker_base is None
        or game.regulation_innings is None
        or current.inning <= game.regulation_innings
        or current.before.outs != 0
    ):
        return None
    runners = (
        current.before.runner_on_first,
        current.before.runner_on_second,
        current.before.runner_on_third,
    )
    configured = runners[game.tiebreaker_base - 1]
    if configured is None or sum(runner is not None for runner in runners) != 1:
        return None
    return configured


def _proven_automatic_runner_after_np(
    previous: RetrosheetPlayTransition,
    current: RetrosheetPlayTransition,
    game: RetrosheetGame,
) -> str | None:
    """Recognize the current Daily Logs NP bridge before runner placement.

    MIL202104010 has an empty, no-op ``NP`` as the first top-10 record and places
    the declared second-base runner in the following strikeout's pre-state.  This
    contract permits only that first-play bridge shape; it does not infer runners
    after arbitrary no-play records later in a half.
    """

    automatic_runner_id = _configured_automatic_runner_id(game, current)
    if automatic_runner_id is None:
        return None
    if previous.half_play_number != 1 or current.half_play_number != 2:
        return None
    if previous.event_text.strip().upper() != "NP":
        return None
    if previous.pa_terminal_token is not None or previous.runs_scored != 0:
        return None
    if previous.before.outs != 0 or previous.after.outs != 0:
        return None
    if previous.before.base_mask != 0 or previous.after.base_mask != 0:
        return None
    if (
        previous.before.visiting_score,
        previous.before.home_score,
    ) != (
        previous.after.visiting_score,
        previous.after.home_score,
    ):
        return None
    return automatic_runner_id


def _is_proven_pinch_runner_replacement(
    previous: RetrosheetPlayTransition,
    current: RetrosheetPlayTransition,
    after_bases: tuple[str | None, str | None, str | None],
    before_bases: tuple[str | None, str | None, str | None],
    pinch_runner_replacements: frozenset[tuple[str, str, str, str]],
) -> bool:
    """Recognize source-proven identity-only replacements on occupied bases.

    Multiple pinch runners can enter during the same between-play substitution
    interval.  Every changed base must preserve occupancy and have its own exact
    adjacent batting-value ``pr=1`` proof; one proven change never authorizes an
    unrelated runner-identity change on another base.
    """

    changed = [
        index
        for index, (outgoing, incoming) in enumerate(
            zip(after_bases, before_bases, strict=True)
        )
        if outgoing != incoming
    ]
    if not changed:
        return False
    if previous.raw_game_id != current.raw_game_id:
        return False
    if previous.batting_team != current.batting_team:
        return False
    for base in changed:
        outgoing = after_bases[base]
        incoming = before_bases[base]
        if outgoing is None or incoming is None:
            return False
        if (
            current.raw_game_id,
            current.batting_team,
            outgoing,
            incoming,
        ) not in pinch_runner_replacements:
            return False
    return True


def _validate_score_totals(
    games: Mapping[str, RetrosheetGame],
    team_rows: Mapping[tuple[str, str], RetrosheetStatRecord],
    plays: tuple[RetrosheetPlayTransition, ...],
) -> None:
    last_play: dict[str, RetrosheetPlayTransition] = {}
    run_sums: dict[tuple[str, str], int] = defaultdict(int)
    for play in plays:
        last_play[play.raw_game_id] = play
        run_sums[(play.raw_game_id, play.batting_team)] += play.runs_scored

    for gid, game in games.items():
        expected = {game.visiting_team: game.visiting_runs, game.home_team: game.home_runs}
        for team, final_runs in expected.items():
            value_row = team_rows[(gid, team)].values
            b_r = _optional_integer(value_row["b_r"], f"{gid}/{team}.b_r", minimum=0)
            innings = [
                _optional_integer(value_row[f"inn{number}"], f"{gid}/{team}.inn{number}", minimum=0)
                for number in range(1, 29)
            ]
            known_innings = [runs for runs in innings if runs is not None]
            if final_runs is not None and b_r is not None and b_r != final_runs:
                raise RetrosheetImportError(f"{gid}/{team}: batting runs conflict with game final")
            if final_runs is not None and known_innings and sum(known_innings) != final_runs:
                raise RetrosheetImportError(f"{gid}/{team}: line score conflicts with game final")
            if gid in last_play and final_runs is not None and run_sums[(gid, team)] != final_runs:
                raise RetrosheetImportError(f"{gid}/{team}: play runs conflict with game final")
        if gid in last_play:
            if game.visiting_runs is None or game.home_runs is None:
                raise RetrosheetImportError(f"{gid}: PBP requires known final scores")
            final = last_play[gid].after
            if (final.visiting_score, final.home_score) != (game.visiting_runs, game.home_runs):
                raise RetrosheetImportError(f"{gid}: terminal play score conflicts with game final")


def _validate_stat_context(
    row: Mapping[str, str], game: RetrosheetGame, team: str, context: str
) -> None:
    row_date = _date(row["date"], f"{context}.date")
    row_number = _integer(row["number"], f"{context}.number", minimum=0, maximum=9)
    expected_side = "v" if team == game.visiting_team else "h"
    expected_opponent = game.home_team if expected_side == "v" else game.visiting_team
    if row_date != game.game_date or row_number != game.game_number:
        raise RetrosheetImportError(f"{context}: date/number conflicts with gameinfo")
    if row["site"].strip() != game.site:
        raise RetrosheetImportError(f"{context}: site conflicts with gameinfo")
    if row["vishome"].strip().lower() != expected_side:
        raise RetrosheetImportError(f"{context}: vishome conflicts with team identity")
    if row["opp"].strip() != expected_opponent:
        raise RetrosheetImportError(f"{context}: opponent conflicts with game identity")
    if row["gametype"].strip() != game.gametype:
        raise RetrosheetImportError(f"{context}: gametype conflicts with gameinfo")
    if _availability(row["box"], f"{context}.box") is not game.box_known:
        raise RetrosheetImportError(f"{context}: box availability conflicts with gameinfo")
    if _game_pbp_quality(row["pbp"], f"{context}.pbp") is not game.pbp_quality:
        raise RetrosheetImportError(f"{context}: PBP quality conflicts with gameinfo")


def _known_game(
    raw_game_id: object, games: Mapping[str, RetrosheetGame], context: str
) -> RetrosheetGame:
    gid = str(raw_game_id).strip()
    _validate_game_id(gid, f"{context}.gid")
    game = games.get(gid)
    if game is None:
        raise RetrosheetImportError(f"{context}: unknown game id {gid!r}")
    return game


def _validate_team_for_game(team: str, game: RetrosheetGame, context: str) -> None:
    _validate_team_id(team, f"{context}.team")
    if team not in (game.visiting_team, game.home_team):
        raise RetrosheetImportError(f"{context}: team is not a participant in {game.raw_game_id}")


def _validate_rostered_player(
    player: str,
    team: str,
    season: int,
    roster: frozenset[tuple[str, str, int]],
    context: str,
) -> None:
    _validate_player_id(player, context)
    if (player, team, season) not in roster:
        raise RetrosheetImportError(
            f"{context}: player {player!r} is not rostered for {team}/{season}"
        )


def _optional_rostered_player(
    value: object,
    team: str,
    season: int,
    roster: frozenset[tuple[str, str, int]],
    context: str,
) -> str | None:
    player = str(value).strip()
    if not player:
        return None
    _validate_rostered_player(player, team, season, roster, context)
    return player


def _validate_game_id(value: str, context: str = "game id") -> None:
    if not _GAME_ID_PATTERN.fullmatch(value):
        raise RetrosheetImportError(f"{context}: invalid 12-character Retrosheet game id {value!r}")


def _validate_team_id(value: str, context: str) -> None:
    if not _TEAM_ID_PATTERN.fullmatch(value):
        raise RetrosheetImportError(f"{context}: invalid Retrosheet team id {value!r}")


def _validate_player_id(value: str, context: str) -> None:
    if not _PLAYER_ID_PATTERN.fullmatch(value):
        raise RetrosheetImportError(
            f"{context}: invalid 8-character Retrosheet player id {value!r}"
        )


def _validate_source_version(value: object) -> None:
    token = str(value).strip()
    if not _SOURCE_VERSION_PATTERN.fullmatch(token):
        raise RetrosheetImportError("source_version_id must be a stable, nonblank identifier")


def _date(value: object, context: str) -> date:
    token = str(value).strip()
    if re.fullmatch(r"[0-9]{8}", token):
        try:
            return date(int(token[:4]), int(token[4:6]), int(token[6:8]))
        except ValueError as exc:
            raise RetrosheetImportError(
                f"{context}: invalid date {value!r}"
            ) from exc
    for separator in ("-", "/"):
        try:
            return date.fromisoformat(token.replace(separator, "-"))
        except ValueError:
            pass
    raise RetrosheetImportError(f"{context}: invalid date {value!r}")


def _suspension_completion_date(
    value: object, game_date: date, context: str
) -> date | None:
    token = str(value).strip()
    if not token:
        return None
    completion_date = _date(token, context)
    if completion_date <= game_date:
        raise RetrosheetImportError(
            f"{context}: suspension completion date must follow the original game date"
        )
    return completion_date


def _integer(
    value: object,
    context: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    token = str(value).strip()
    if not re.fullmatch(r"-?[0-9]+", token):
        raise RetrosheetImportError(f"{context}: expected integer, got {value!r}")
    parsed = int(token)
    if minimum is not None and parsed < minimum:
        raise RetrosheetImportError(f"{context}: value {parsed} is below {minimum}")
    if maximum is not None and parsed > maximum:
        raise RetrosheetImportError(f"{context}: value {parsed} exceeds {maximum}")
    return parsed


def _optional_integer(
    value: object,
    context: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    if not str(value).strip():
        return None
    return _integer(value, context, minimum=minimum, maximum=maximum)


def _binary(value: object, context: str) -> int:
    token = str(value).strip()
    if token not in {"0", "1"}:
        raise RetrosheetImportError(f"{context}: expected binary 0/1, got {value!r}")
    return int(token)


def _optional_binary(value: object, context: str) -> int | None:
    if not str(value).strip():
        return None
    return _binary(value, context)


def _yes_no(value: object, context: str) -> bool:
    token = str(value).strip().lower()
    if token in {"", "n", "no", "false", "0"}:
        return False
    if token in {"y", "yes", "true", "1"}:
        return True
    raise RetrosheetImportError(f"{context}: unmapped yes/no token {value!r}")


def _availability(value: object, context: str) -> bool:
    token = str(value).strip().lower()
    if token == "":
        return False
    if token in {"y", "yes", "true", "1"}:
        return True
    raise RetrosheetImportError(f"{context}: unmapped availability token {value!r}")


def _batteries_known(value: object, context: str) -> bool:
    token = str(value).strip().lower()
    if token == "":
        return False
    if token in {"p", "both"}:
        return True
    raise RetrosheetImportError(f"{context}: unmapped batteries token {value!r}")


def _game_pbp_quality(value: object, context: str) -> RetrosheetPbpQuality:
    token = str(value).strip().lower()
    mapping = {
        "": RetrosheetPbpQuality.NONE,
        "y": RetrosheetPbpQuality.FULL,
        "d": RetrosheetPbpQuality.DEDUCED,
    }
    try:
        return mapping[token]
    except KeyError as exc:
        raise RetrosheetImportError(f"{context}: unmapped game PBP token {value!r}") from exc


def _play_pbp_quality(value: object, context: str) -> RetrosheetPbpQuality:
    token = str(value).strip().lower()
    mapping = {"full": RetrosheetPbpQuality.FULL, "deduced": RetrosheetPbpQuality.DEDUCED}
    try:
        return mapping[token]
    except KeyError as exc:
        raise RetrosheetImportError(f"{context}: unmapped play PBP token {value!r}") from exc


def _required(value: object, context: str) -> str:
    token = str(value).strip()
    if not token:
        raise RetrosheetImportError(f"{context}: value is required")
    return token


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
