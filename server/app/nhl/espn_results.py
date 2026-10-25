"""NHL finals from ESPN's free public scoreboard: the results source for NHL paper settlement. OFF.

WHY THIS EXISTS
---------------
``app.nhl.paper.settle`` settles an NHL position only from a source-backed ``nhl_game_results``
row. Until now the only writer of that table was the producer's result manifest
(``results_consumer``), and the producer has never published one, so a played NHL game could
never settle. Under the coordinator's NHL simplification
(CLAUDE_COORDINATOR_NHL_SIMPLIFICATION_2026-10-16) AISP reads NHL finals itself from ESPN's
public scoreboard: one keyless GET per US-Eastern date, the same public endpoint family AISP
already reads for NFL in-game scores (``app.scoreboard.nfl_espn``). No key, no account, no
charge. ``nhl_espn_results_enabled`` defaults to False: until it is set, the scheduler registers
no job and a manual run refuses before any request.

WHEN IT ASKS (``due_games``)
---------------------------
Only for the dates of real AISP NHL games -- bound to their NHL game id, both teams bound to
their NHL team ids, never a fixture or synthetic game -- that started more than 2h30m ago and are
either still waiting for a result (scheduled/live, within ``nhl_espn_results_lookback_days``) or
already final FROM THIS SOURCE and still inside ``nhl_espn_results_correction_hours`` (so a later
score change is seen). No such game, no request. A failed request is reported, never raised, and
the next cycle simply asks again.

HOW AN EVENT IS MATCHED -- AND WHEN IT IS REFUSED
------------------------------------------------
By BOTH clubs in orientation (ESPN's home is AISP's home) plus a start within twelve hours. ESPN
spells five clubs its own way (LA, NJ, SJ, TB, UTAH); ``ESPN_TO_NHL`` translates them to the NHL
tri-codes AISP's NHL teams carry, through an explicit table. The NHL tri-code itself is accepted
too, because no spelling of one club is another club's code. A spelling outside the table is
reported and never coerced into the nearest club. (The five short spellings, and ESPN's NHL
status/linescore conventions below, are modelled on ESPN's other leagues: no retained ESPN NHL
response was available to read them from. Anything that does not fit fails closed and is
reported -- the game simply stays unsettled.) Two ESPN events fitting one AISP game, or two
AISP games fitting one event, are refused: an unsettled position costs nothing, a result on the
wrong game costs the truth. The match is then re-proved through the NHL identity bindings
(``gen1_import.bind_game``) before anything is written.

WHAT A FINAL BECOMES
--------------------
One ``NHL_GEN1_RESULT_V1`` document -- the producer's own result contract, proved by
``contract.validate_result`` before anything is written -- stored in ``nhl_game_results``:

* regulation goals = ESPN's per-period linescores for periods 1-3;
* a regulation tie went to extra time. Outside the playoffs period 4 is the (sudden-death)
  overtime; a 0-0 overtime with ESPN reporting SO / period 5 is a shootout, whose tally is
  period 5's linescore (absent that, the winner's credited deciding goal, and the stored
  derivation says so). In the playoffs every period after the third is overtime;
* the final is ESPN's score, which must agree with that arithmetic, with each competitor's
  ``winner`` flag and with ESPN's season type, or nothing is written (REFUSED, reported, and
  retried next cycle);
* ``final.official`` is true only for a completed event (``STATUS_FINAL*``, state post,
  completed). It means "the published final of a finished game" from a public scoreboard that is
  named on every row as ``ESPN_SCOREBOARD_PUBLIC`` -- not a league-certified boxscore;
* clocks are honest. ESPN publishes no time at which a score became true, so observed and
  retrieved are both the moment AISP received the response; completion is null, never invented;
  ``imported_at`` / ``available_as_of`` are the database write clock (never before retrieval);
* ``result_sha256`` is the contract's content identity of that document, so the stored result
  re-proves itself and a later revert to an earlier score is still a NEW identity.
  ``object_sha256`` is the SHA-256 of the canonical (SPORTS_CANONICAL_JSON_V1) raw ESPN event,
  which is kept verbatim in the stored document beside the contract result, the request (URL,
  response SHA-256 and size, retrieval clock) and the match.

The game row is then marked FINAL by ``gen1_import.apply_official_final``, the same function the
producer's result import uses. NOTHING HERE SETTLES: ``app.nhl.paper.settle`` stays the one NHL
settlement owner (it runs in the NHL paper checkpoint), and the generic experiment settler still
hands NHL to it.

CORRECTIONS AND OTHER SOURCES
-----------------------------
A result is never edited. A later ESPN final whose substance differs (regulation goals, how it
was decided, OT/SO, the final) is a NEW row naming ``supersedesResultSha256`` = the current one;
``paper.correct`` turns a changed winner into an adjustment beside the original settlement. A
re-read with the same substance writes nothing. A game whose current result came from another
source (the producer's manifest) is left alone and reported as ``HELD_FROM_OTHER_SOURCE``.

POSTPONED / CANCELLED
---------------------
A matched event that ESPN reports postponed or cancelled, for a game AISP still holds as
scheduled 2h30m after its start, sets the game's status to POSTPONED / CANCELED -- the change
``app.nhl.schedule`` makes for a producer schedule entry -- with a SCHEDULE receipt, and
``paper.settle`` then voids any position on it. Never applied to a game that holds a result.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ExternalIdMapping, Game, GameStatus, GameTeam, League, Team
from app.models.nhl_research import NHL_GAME_SOURCE, NHL_LEAGUE_CODE, NHL_TEAM_SOURCE, NhlGameResult
from app.nhl import contract as c

SOURCE = "ESPN_SCOREBOARD_PUBLIC"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
DOCUMENT_SCHEMA = "AISP_NHL_ESPN_RESULT_V1"
DEFAULT_INTERVAL_MINUTES = 30
#: A regulation NHL game takes about two and a half hours; before that nothing is asked.
SETTLE_AFTER_START = timedelta(hours=2, minutes=30)
#: Both clubs plus a start within this distance. The same orientation never meets twice inside
#: twelve hours, so a moved start still matches and a rematch never collides.
START_TOLERANCE = timedelta(hours=12)
REQUEST_TIMEOUT_SECONDS = 15.0
#: Games that are never real: no result is ever written for them.
FIXTURE_SOURCE_MODES = frozenset({"SYNTHETIC", "FIXTURE", "PREVIEW_FIXTURE"})
ET = ZoneInfo("America/New_York")

#: ESPN club spelling -> NHL tri-code (what AISP's NHL teams carry, bound by NHL team id). ESPN
#: shortens five clubs; the other twenty-seven use the NHL tri-code. The NHL spelling of the five
#: is accepted too: no spelling of one club is another club's code, so nothing can be confused.
_ESPN_SHORT = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA"}
NHL_CODES = frozenset({
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET", "EDM", "FLA", "LAK", "MIN",
    "MTL", "NJD", "NSH", "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA",
    "VAN", "VGK", "WPG", "WSH",
})
ESPN_TO_NHL: dict[str, str] = {**{code: code for code in NHL_CODES}, **_ESPN_SHORT}
#: ESPN's season type -> the NHL game type the canonical game id carries.
SEASON_TYPES = {1: "PRESEASON", 2: "REGULAR", 3: "PLAYOFF"}
VOID_EVENT_STATUSES = {"STATUS_POSTPONED": GameStatus.POSTPONED, "STATUS_CANCELED": GameStatus.CANCELED,
                       "STATUS_CANCELLED": GameStatus.CANCELED}
_GAME_REF = re.compile(r"^nhl:game:(\d{10})$")


class Refused(ValueError):
    """An ESPN event that cannot become a result. The reason is reported; nothing is written."""


# ------------------------------------------------------------------ parsing --
def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _z(moment: datetime) -> str:
    return _aware(moment).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return int(number) if number.is_integer() else None
    return None


def _when(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _aware(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def nhl_code(abbreviation: Any) -> str | None:
    """The NHL tri-code for an ESPN club spelling, or None when the spelling is not in the table."""

    return ESPN_TO_NHL.get(str(abbreviation or "").strip().upper())


@dataclass
class Event:
    """One ESPN scoreboard event as received, with the request it arrived in."""

    raw: dict[str, Any]
    request: dict[str, Any]
    retrieved_at: datetime
    event_id: str | None
    start: datetime | None
    home_abbreviation: str | None
    away_abbreviation: str | None
    home_code: str | None
    away_code: str | None
    status_name: str
    status_state: str
    completed: bool
    detail: str
    period: int | None
    matches: list[uuid.UUID] = field(default_factory=list)

    @property
    def final(self) -> bool:
        return self.status_name.startswith("STATUS_FINAL") and self.completed and self.status_state == "post"

    def label(self) -> dict[str, Any]:
        return {"espn_event_id": self.event_id, "espn_away": self.away_abbreviation,
                "espn_home": self.home_abbreviation, "espn_start_utc": self.start.isoformat() if self.start else None,
                "espn_status": self.status_name, "espn_detail": self.detail or None}


def _competition(event: dict[str, Any]) -> dict[str, Any]:
    competitions = event.get("competitions")
    return competitions[0] if isinstance(competitions, list) and competitions and isinstance(
        competitions[0], dict) else {}


def _sides(event: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sides: dict[str, dict[str, Any]] = {}
    for competitor in _competition(event).get("competitors") or []:
        if isinstance(competitor, dict) and competitor.get("homeAway") in ("home", "away"):
            if competitor["homeAway"] in sides:
                return {}                                  # two homes (or aways): not one game
            sides[competitor["homeAway"]] = competitor
    return sides


def parse_event(event: dict[str, Any], *, request: dict[str, Any], retrieved_at: datetime) -> Event:
    sides = _sides(event)
    abbreviation = {k: (v.get("team") or {}).get("abbreviation") for k, v in sides.items()}
    status = _competition(event).get("status") or event.get("status") or {}
    kind = status.get("type") or {}
    return Event(
        raw=event, request=request, retrieved_at=retrieved_at,
        event_id=str(event.get("id")) if event.get("id") is not None else None,
        start=_when(event.get("date") or _competition(event).get("date")),
        home_abbreviation=abbreviation.get("home"), away_abbreviation=abbreviation.get("away"),
        home_code=nhl_code(abbreviation.get("home")), away_code=nhl_code(abbreviation.get("away")),
        status_name=str(kind.get("name") or ""), status_state=str(kind.get("state") or ""),
        completed=kind.get("completed") is True,
        detail=str(kind.get("detail") or kind.get("shortDetail") or ""), period=_int(status.get("period")))


# ---------------------------------------------------------------- targets --
@dataclass
class Target:
    """An AISP NHL game with its exact NHL identity, as AISP holds it now."""

    game_id: uuid.UUID
    starts_at: datetime
    status: GameStatus
    source_mode: str
    canonical_game_ref: str | None = None
    home_code: str | None = None
    away_code: str | None = None
    home_ref: str | None = None
    away_ref: str | None = None
    current: NhlGameResult | None = None
    watch: str = "AWAITING_RESULT"

    @property
    def game_type(self) -> str | None:
        found = _GAME_REF.match(self.canonical_game_ref or "")
        return c.GAME_TYPES.get(found.group(1)[4:6]) if found else None

    @property
    def complete(self) -> bool:
        return None not in (self.canonical_game_ref, self.home_code, self.away_code, self.home_ref,
                            self.away_ref, self.game_type)

    @property
    def date(self) -> str:
        return self.starts_at.astimezone(ET).strftime("%Y%m%d")

    def label(self) -> dict[str, Any]:
        return {"game_id": str(self.game_id), "canonical_game_ref": self.canonical_game_ref,
                "matchup": f"{self.away_code}@{self.home_code}", "starts_at": self.starts_at.isoformat(),
                "watch": self.watch}


def _single(pairs: list[tuple[Any, Any]]) -> dict[Any, Any]:
    """entity -> external id, keeping only entities bound exactly once (two bindings = no identity)."""

    seen: dict[Any, list[Any]] = {}
    for entity, external in pairs:
        seen.setdefault(entity, []).append(external)
    return {entity: values[0] for entity, values in seen.items() if len(set(values)) == 1}


def _targets(session: Session, rows: list[Any]) -> list[Target]:
    ids = [row.id for row in rows]
    if not ids:
        return []
    game_refs = _single(session.execute(select(ExternalIdMapping.entity_id, ExternalIdMapping.external_id).where(
        ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == NHL_GAME_SOURCE,
        ExternalIdMapping.entity_id.in_(ids))).all())
    sides = session.execute(select(GameTeam.game_id, GameTeam.is_home, Team.id, Team.abbreviation)
                            .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(ids))).all()
    team_refs = _single(session.execute(select(ExternalIdMapping.entity_id, ExternalIdMapping.external_id).where(
        ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == NHL_TEAM_SOURCE,
        ExternalIdMapping.entity_id.in_(sorted({side.id for side in sides}, key=str)))).all())
    targets = {row.id: Target(game_id=row.id, starts_at=_aware(row.starts_at), status=row.status,
                              source_mode=row.source_mode, canonical_game_ref=game_refs.get(row.id))
               for row in rows}
    for game_id, is_home, team_id, abbreviation in sides:
        target = targets[game_id]
        code = str(abbreviation or "").upper() if str(abbreviation or "").upper() in NHL_CODES else None
        if is_home:
            target.home_code, target.home_ref = code, team_refs.get(team_id)
        else:
            target.away_code, target.away_ref = code, team_refs.get(team_id)
    return list(targets.values())


def _nhl_games(session: Session, *, earliest: datetime, latest: datetime,
               statuses: tuple[GameStatus, ...] | None = None) -> list[Any]:
    query = (select(Game.id, Game.starts_at, Game.status, Game.source_mode)
             .join(League, League.id == Game.league_id)
             .where(League.code == NHL_LEAGUE_CODE, Game.starts_at >= earliest, Game.starts_at <= latest))
    if statuses:
        query = query.where(Game.status.in_(statuses))
    return [row for row in session.execute(query).all() if row.source_mode not in FIXTURE_SOURCE_MODES]


def due_games(session: Session, settings: Any, *, now: datetime) -> list[Target]:
    """Real NHL games started 2h30m+ ago that await a result, or hold one from this source to re-check."""

    from app.nhl.read_service import current_result

    lookback = timedelta(days=int(getattr(settings, "nhl_espn_results_lookback_days", 7)))
    correction = timedelta(hours=int(getattr(settings, "nhl_espn_results_correction_hours", 48)))
    rows = _nhl_games(session, earliest=now - max(lookback, correction), latest=now - SETTLE_AFTER_START,
                      statuses=(GameStatus.SCHEDULED, GameStatus.LIVE, GameStatus.FINAL))
    targets = _targets(session, rows)
    results: dict[uuid.UUID, list[NhlGameResult]] = {}
    if targets:
        for row in session.scalars(select(NhlGameResult).where(
                NhlGameResult.game_id.in_([t.game_id for t in targets]))):
            results.setdefault(row.game_id, []).append(row)
    due = []
    for target in targets:
        target.current = current_result(results.get(target.game_id, []))
        if target.status in (GameStatus.SCHEDULED, GameStatus.LIVE) and target.starts_at >= now - lookback:
            due.append(target)
        elif (target.status == GameStatus.FINAL and target.current is not None
              and target.current.final_source == SOURCE and target.starts_at >= now - correction):
            target.watch = "CORRECTION_WATCH"
            due.append(target)
    return sorted(due, key=lambda t: (t.starts_at, str(t.game_id)))


# ----------------------------------------------------------------- result --
def _lines(competitor: dict[str, Any]) -> dict[int, int]:
    out: dict[int, int] = {}
    for index, entry in enumerate(competitor.get("linescores") or []):
        if not isinstance(entry, dict):
            raise Refused("LINESCORE_MALFORMED")
        number = _int(entry.get("period")) or index + 1
        value = _int(entry.get("value"))
        if value is None or value < 0 or number in out:
            raise Refused("LINESCORE_MALFORMED")
        out[number] = value
    return out


def derive_result(event: Event, target: Target, *, supersedes: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The contract result for a FINAL event (and how it was derived), or ``Refused``."""

    if not event.final:
        raise Refused(f"NOT_FINAL:{event.status_name or 'UNKNOWN'}")
    sides = _sides(event.raw)
    if set(sides) != {"home", "away"}:
        raise Refused("COMPETITORS_NOT_ONE_HOME_ONE_AWAY")
    home, away = sides["home"], sides["away"]
    final_home, final_away = _int(home.get("score")), _int(away.get("score"))
    if final_home is None or final_away is None or final_home < 0 or final_away < 0:
        raise Refused("FINAL_SCORE_MISSING")
    hl, al = _lines(home), _lines(away)
    if any(p not in hl or p not in al for p in (1, 2, 3)):
        raise Refused("REGULATION_LINESCORE_INCOMPLETE")
    if set(hl) != set(al):
        raise Refused("LINESCORE_PERIODS_DIFFER_BETWEEN_TEAMS")
    extra = sorted(p for p in hl if p > 3)
    game_type = target.game_type
    if min(hl) != 1 or (extra != list(range(4, 4 + len(extra))) if game_type == "PLAYOFF"
                        else not set(extra) <= {4, 5}):
        # Playoff overtimes are consecutive periods; otherwise only OT (4) and the shootout (5)
        # exist, and a scoreless overtime column may be left out before a shootout.
        raise Refused("LINESCORE_PERIODS_UNRECOGNISED")
    season_type = SEASON_TYPES.get(_int((event.raw.get("season") or {}).get("type")) or 0)
    if season_type is not None and season_type != game_type:
        raise Refused(f"GAME_TYPE_DIFFERS:espn={season_type}:aisp={game_type}")
    rh, ra = hl[1] + hl[2] + hl[3], al[1] + al[2] + al[3]
    words = set(re.split(r"[^A-Z0-9]+", event.detail.upper()))
    tally = None
    overtime: dict[str, Any] | None = None
    shootout: dict[str, Any] | None = None
    if rh != ra:
        decided = "REGULATION"
        if any(hl[p] or al[p] for p in extra):
            raise Refused("EXTRA_TIME_GOALS_AFTER_A_REGULATION_DECISION")
        if (final_home, final_away) != (rh, ra):
            raise Refused("FINAL_DIFFERS_FROM_REGULATION_LINESCORE")
    elif game_type == "PLAYOFF":
        oh, oa = sum(hl[p] for p in extra), sum(al[p] for p in extra)
        if oh + oa != 1:
            raise Refused("PLAYOFF_OVERTIME_SHAPE")
        decided = "OVERTIME"
        overtime = {"played": True, "homeGoals": oh, "awayGoals": oa, "periods": len(extra)}
        if (final_home, final_away) != (rh + oh, ra + oa):
            raise Refused("FINAL_DIFFERS_FROM_REGULATION_PLUS_OVERTIME")
    else:
        oh, oa = hl.get(4, 0), al.get(4, 0)
        if oh + oa == 1:
            decided = "OVERTIME"
            if hl.get(5) or al.get(5):
                raise Refused("SHOOTOUT_AFTER_A_SCORING_OVERTIME")
            overtime = {"played": True, "homeGoals": oh, "awayGoals": oa, "periods": 1}
            if (final_home, final_away) != (rh + oh, ra + oa):
                raise Refused("FINAL_DIFFERS_FROM_REGULATION_PLUS_OVERTIME")
        elif oh + oa == 0:
            decided = "SHOOTOUT"
            if not ({"SO", "SHOOTOUT"} & words or event.period == 5 or 5 in hl):
                raise Refused("TIED_AFTER_OVERTIME_BUT_NO_SHOOTOUT_REPORTED")
            home_won = final_home > final_away
            if (final_home, final_away) != ((rh + 1, ra) if home_won else (rh, ra + 1)):
                raise Refused("FINAL_DIFFERS_FROM_REGULATION_PLUS_SHOOTOUT_GOAL")
            if 5 in hl:
                sh, sa = hl[5], al[5]
                if sh == sa or (sh > sa) != home_won:
                    raise Refused("SHOOTOUT_TALLY_DISAGREES_WITH_THE_FINAL")
                tally = "ESPN_LINESCORE_PERIOD_5"
            else:
                sh, sa = (1, 0) if home_won else (0, 1)
                tally = "DECISIVE_GOAL_CREDIT_ONLY"
            overtime = {"played": True, "homeGoals": 0, "awayGoals": 0, "periods": 1}
            shootout = {"played": True, "homeScored": sh, "awayScored": sa}
        else:
            raise Refused("OVERTIME_SHAPE")
    if final_home == final_away:
        raise Refused("FINAL_TIED")
    winner = "HOME" if final_home > final_away else "AWAY"
    for side, competitor in (("HOME", home), ("AWAY", away)):
        flag = competitor.get("winner")
        if isinstance(flag, bool) and flag != (winner == side):
            raise Refused("WINNER_FLAG_DISAGREES_WITH_THE_SCORE")
    stamp = _z(event.retrieved_at)
    document = c.with_result_identity({
        "schema": c.RESULT_SCHEMA, "canonicalGameId": target.canonical_game_ref, "gameType": game_type,
        "homeCanonicalTeamId": target.home_ref, "awayCanonicalTeamId": target.away_ref,
        "regulation": {"homeGoals": rh, "awayGoals": ra, "result": "HOME" if rh > ra else "AWAY" if ra > rh else "TIE",
                       "source": SOURCE, "observedAtUtc": stamp, "retrievedAtUtc": stamp},
        "decidedIn": decided, "overtime": overtime, "shootout": shootout,
        "final": {"homeScore": final_home, "awayScore": final_away, "winner": winner, "official": True,
                  "source": SOURCE, "observedAtUtc": stamp, "retrievedAtUtc": stamp, "completedAtUtc": None},
        "supersedesResultSha256": supersedes, "resultSha256": None,
    })
    problems = c.validate_result(document)
    if problems:
        raise Refused("CONTRACT_INVALID:" + ",".join(problems))
    derivation = {"statusName": event.status_name, "statusDetail": event.detail or None,
                  "statusPeriod": event.period, "homeLinescores": [hl[p] for p in sorted(hl)],
                  "awayLinescores": [al[p] for p in sorted(al)], "shootoutTally": tally,
                  "official": "ESPN reports the event completed (STATUS_FINAL, state post); public scoreboard, "
                              "not a league-certified boxscore",
                  "clocks": "observed = retrieved = response receipt; ESPN publishes no completion clock"}
    return document, derivation


def row_fields(document: dict[str, Any]) -> dict[str, Any]:
    """The ``nhl_game_results`` columns a contract result fills (as ``import_result_bytes`` fills them)."""

    reg, final = document["regulation"], document["final"]
    ot, so = document.get("overtime") or {}, document.get("shootout") or {}
    return {
        "game_type": document["gameType"], "regulation_home_goals": reg["homeGoals"],
        "regulation_away_goals": reg["awayGoals"], "regulation_result": reg["result"],
        "decided_in": document["decidedIn"],
        "overtime_home_goals": ot.get("homeGoals") if ot.get("played") else None,
        "overtime_away_goals": ot.get("awayGoals") if ot.get("played") else None,
        "shootout_home_scored": so.get("homeScored"), "shootout_away_scored": so.get("awayScored"),
        "final_home_score": final["homeScore"], "final_away_score": final["awayScore"],
        "final_winner": final["winner"], "final_official": final["official"],
    }


def _same_substance(row: NhlGameResult, document: dict[str, Any]) -> bool:
    return all(getattr(row, name) == value for name, value in row_fields(document).items())


# ----------------------------------------------------------------- fetch --
def fetch_scoreboard(day: str) -> bytes:
    """One public GET for one US-Eastern date. No key, no retry; returns the response bytes."""

    import httpx

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False,
                      headers={"Accept": "application/json", "User-Agent": "AISP-nhl-results/1.0"}) as client:
        response = client.get(SCOREBOARD_URL, params={"dates": day})
    if response.status_code != 200:
        raise RuntimeError(f"ESPN scoreboard returned HTTP {response.status_code}")
    return response.content


def _events(raw: bytes, *, day: str, retrieved_at: datetime) -> list[Event]:
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("events", []), list):
        raise ValueError("RESPONSE_IS_NOT_A_SCOREBOARD")
    leagues = payload.get("leagues")
    if isinstance(leagues, list) and leagues and isinstance(leagues[0], dict) \
            and leagues[0].get("abbreviation") not in (None, "NHL"):
        raise ValueError(f"RESPONSE_IS_NOT_NHL:{leagues[0].get('abbreviation')}")
    request = {"url": f"{SCOREBOARD_URL}?dates={day}", "date": day, "retrievedAtUtc": _z(retrieved_at),
               "responseSha256": c.sha256_hex(raw), "responseBytes": len(raw), "costUsd": 0}
    return [parse_event(e, request=request, retrieved_at=retrieved_at)
            for e in payload.get("events") or [] if isinstance(e, dict)]


# ------------------------------------------------------------------ cycle --
def ingest(session: Session, settings: Any, *, now: datetime | None = None,
           fetch: Callable[[str], bytes] | None = None,
           clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """One cycle: ask ESPN for the dates of due NHL games and write what is final. Commits its work."""

    from app.nhl.gen1_import import ImportSummary, _receipt, apply_official_final, bind_game

    clock = clock or (lambda: datetime.now(UTC))
    base: dict[str, Any] = {"source": SOURCE, "costUsd": 0, "research_only": True,
                            "settlement_owner": "app.nhl.paper.settle"}
    if not getattr(settings, "nhl_espn_results_enabled", False):
        return {**base, "records": 0, "requests": 0, "status": "DISABLED",
                "detail": "NHL_ESPN_RESULTS_ENABLED is off; no request is made and nothing is written."}
    moment = _aware(now) if now else _aware(clock())
    due = due_games(session, settings, now=moment)
    if not due:
        return {**base, "records": 0, "requests": 0, "status": "NO_NHL_GAME_AWAITING_A_RESULT"}

    dates = sorted({t.date for t in due})
    events: list[Event] = []
    failures: dict[str, str] = {}
    for day in dates:
        try:
            raw = (fetch or fetch_scoreboard)(day)
            events.extend(_events(raw, day=day, retrieved_at=_aware(clock())))
        except Exception as error:  # noqa: BLE001 - a failed request is reported, never raised
            failures[day] = f"{type(error).__name__}:{str(error)[:120]}"

    # Every real NHL game near these events is a candidate, due or not, so a duplicate is SEEN.
    pool = _targets(session, _nhl_games(session, earliest=min(t.starts_at for t in due) - timedelta(days=2),
                                        latest=max(t.starts_at for t in due) + timedelta(days=2)))
    unrecognised = []
    for event in events:
        if event.home_code is None or event.away_code is None or event.start is None:
            unrecognised.append(event.label())
            continue
        event.matches = [t.game_id for t in pool
                         if (t.home_code, t.away_code) == (event.home_code, event.away_code)
                         and abs(t.starts_at - event.start) <= START_TOLERANCE]

    summary = ImportSummary(run_id=f"nhl-espn-{uuid.uuid4().hex[:12]}")
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in (
        "imported", "corrections", "statuses", "unchanged", "held_from_other_source", "not_final", "refused",
        "unmatched", "request_failed")}

    def receipt(kind: str, disposition: str, target: Target, event: Event | None, problems: list[str],
                identity: str | None = None, object_sha: str | None = None) -> None:
        _receipt(session, summary, kind=kind, disposition=disposition, identity=identity, object_sha256=object_sha,
                 manifest_sha256=None, game_ref=target.canonical_game_ref, bound=disposition in ("IMPORTED", "UPDATED"),
                 problems=problems, source_label=(f"{SOURCE} {event.request['url']} event={event.event_id}"
                                                  if event else SOURCE), now=_aware(clock()))

    def refuse(target: Target, event: Event | None, reason: str, *, record: bool = True) -> None:
        out["refused"].append({**target.label(), **(event.label() if event else {}), "reason": reason})
        if record:
            receipt("RESULT", "REJECTED", target, event, [reason])

    try:
        for target in due:
            if target.date in failures:
                out["request_failed"].append({**target.label(), "error": failures[target.date]})
                continue
            if not target.complete:
                refuse(target, None, "NHL_IDENTITY_INCOMPLETE")
                continue
            fitting = [e for e in events if target.game_id in e.matches]
            if not fitting:
                flipped = [e.event_id for e in events if e.start is not None
                           and (e.home_code, e.away_code) == (target.away_code, target.home_code)
                           and abs(target.starts_at - e.start) <= START_TOLERANCE]
                if flipped:
                    refuse(target, None, f"ORIENTATION_DIFFERS_FROM_ESPN:{flipped}")
                else:
                    out["unmatched"].append(target.label())
                continue
            if len(fitting) > 1:
                refuse(target, None, f"AMBIGUOUS_ESPN_EVENTS:{sorted(str(e.event_id) for e in fitting)}")
                continue
            event = fitting[0]
            if len(event.matches) > 1:
                refuse(target, event, f"AMBIGUOUS_AISP_GAMES:{sorted(str(g) for g in event.matches)}")
                continue
            current = target.current
            if event.status_name in VOID_EVENT_STATUSES:
                status = VOID_EVENT_STATUSES[event.status_name]
                game = session.get(Game, target.game_id)
                if current is not None or game is None or game.status not in (GameStatus.SCHEDULED, GameStatus.LIVE):
                    refuse(target, event, "VOID_STATUS_IGNORED_GAME_HOLDS_A_RESULT_OR_IS_FINAL", record=False)
                    continue
                notes = [f"STATUS:{game.status.value}->{status.value}", f"SOURCE:{SOURCE}",
                         f"ESPN_EVENT:{event.event_id}"]
                game.status = status
                session.flush()
                receipt("SCHEDULE", "UPDATED", target, event, notes)
                out["statuses"].append({**target.label(), **event.label(), "status": status.value})
                continue
            if not event.final:
                out["not_final"].append({**target.label(), **event.label()})
                continue
            if current is not None and current.final_source != SOURCE:
                out["held_from_other_source"].append({**target.label(), **event.label(),
                                                      "held_result_sha256": current.result_sha256,
                                                      "held_source": current.final_source})
                continue
            try:
                document, derivation = derive_result(
                    event, target, supersedes=current.result_sha256 if current is not None else None)
            except Refused as why:
                refuse(target, event, str(why))
                continue
            if current is not None and _same_substance(current, document):
                out["unchanged"].append({**target.label(), "result_sha256": current.result_sha256})
                continue
            bound, orientation = bind_game(session, target.canonical_game_ref, target.home_ref, target.away_ref)
            if bound != target.game_id or orientation:
                refuse(target, event, f"NHL_BINDING_DISAGREES:{orientation or bound}")
                continue
            identity = document["resultSha256"]
            raw_sha = c.sha256_hex(c.canonical_bytes(event.raw, trailing_newline=False))
            if session.scalar(select(NhlGameResult.id).where(NhlGameResult.result_sha256 == identity)):
                receipt("RESULT", "DUPLICATE", target, event, [], identity=identity, object_sha=raw_sha)
                continue
            written = _aware(clock())
            if written < event.retrieved_at:
                refuse(target, event, "CLOCK_ORDER:write_before_retrieval")
                continue
            final = document["final"]
            reg = document["regulation"]
            session.add(NhlGameResult(
                result_sha256=identity, object_sha256=raw_sha, supersedes_result_sha256=document["supersedesResultSha256"],
                canonical_game_ref=target.canonical_game_ref, game_id=target.game_id,
                regulation_source=reg["source"], regulation_observed_utc=c.parse_utc(reg["observedAtUtc"]),
                regulation_retrieved_utc=c.parse_utc(reg["retrievedAtUtc"]), final_source=final["source"],
                final_observed_utc=c.parse_utc(final["observedAtUtc"]),
                final_retrieved_utc=c.parse_utc(final["retrievedAtUtc"]), final_completed_utc=None,
                document={"schema": DOCUMENT_SCHEMA, "result": document,
                          "source": {"source": SOURCE, **event.request, "espnEventId": event.event_id,
                                     "rawEventSha256": raw_sha, "canonicalization": c.CANONICALIZATION},
                          "match": {"aispGameId": str(target.game_id), "canonicalGameId": target.canonical_game_ref,
                                    "espnHome": event.home_abbreviation, "espnAway": event.away_abbreviation,
                                    "nhlHome": target.home_code, "nhlAway": target.away_code,
                                    "aispStartUtc": _z(target.starts_at), "espnStartUtc": _z(event.start),
                                    "startDifferenceSeconds": int((event.start - target.starts_at).total_seconds())},
                          "derivation": derivation, "rawEvent": event.raw},
                available_as_of=written, imported_at=written, **row_fields(document)))
            apply_official_final(session, target.game_id, final)
            session.flush()
            notes = [f"SUPERSEDES:{current.result_sha256}"] if current is not None else []
            receipt("RESULT", "IMPORTED", target, event, notes, identity=identity, object_sha=raw_sha)
            entry = {**target.label(), **event.label(), "result_sha256": identity, "decided_in": document["decidedIn"],
                     "final": f"{final['awayScore']}-{final['homeScore']} (away-home)", "winner": final["winner"]}
            out["corrections" if current is not None else "imported"].append(
                {**entry, "supersedes_result_sha256": current.result_sha256} if current is not None else entry)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {**base, "records": len(out["imported"]) + len(out["corrections"]),
            "status": "REQUEST_FAILED" if len(failures) == len(dates) else "CHECKED",
            "requests": len(dates), "dates": dates, "events": len(events), "games_due": len(due),
            "unrecognised_events": unrecognised, "request_failures": failures, "unchanged": len(out["unchanged"]),
            **{k: v for k, v in out.items() if k != "unchanged"}}


__all__ = ["DOCUMENT_SCHEMA", "ESPN_TO_NHL", "NHL_CODES", "SCOREBOARD_URL", "SOURCE", "Refused", "derive_result",
           "due_games", "fetch_scoreboard", "ingest", "nhl_code", "parse_event", "row_fields"]
