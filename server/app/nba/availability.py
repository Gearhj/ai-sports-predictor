"""NBA star-out gate: AISP_OWNER_NBA_STAR_OUT_RECORD_ONLY_V1 (owner 2026-10-27, truth report NBA-D2).

WHAT IT DOES
------------
A qualifying NBA paper decision is recorded with no stake when a STAR of either team is listed OUT, DOUBTFUL or
QUESTIONABLE at the decision clock, or when AISP cannot prove the stars' availability (UNKNOWN fails closed). It never
changes a probability, a selection or a price; every NBA decision carries the availability receipt for research.

THE FROZEN STAR RULE (Claude, 2026-10-27; Codex FEATURE_REQUIREMENTS left it to PM/Claude to freeze)
--------------------------------------------------------------------------------------------------
* Role = average minutes per appearance over the team's CURRENT-SEASON regular-season games completed before the
  game's tip-off minus 90 minutes (the T-90 role freeze), from ESPN box scores keyed by ESPN athlete id (no names).
* Eligible = appeared (minutes > 0) in at least max(2, a quarter of the team's completed games) -- a quarter, not a
  half, so a star who has missed most games injured still counts -- AND either appeared in
  one of the team's last 5 completed games or is on this game's injury list (a traded-away player drops out).
* Stars = the top 2 eligible players by that average; ties broken by the ESPN athlete id, ascending.
* A team with fewer than 2 completed games, or any completed game whose box score AISP does not hold, is
  ROLE_UNKNOWN (no star list is claimed).
* Status = ESPN's per-game injury list in the latest snapshot AISP held at the decision clock, observed no more than
  45 minutes earlier. "Out" -> OUT, "Doubtful" -> DOUBTFUL, "Questionable" / "Day-To-Day" -> QUESTIONABLE,
  "Probable" -> PROBABLE. A star absent from the list is AVAILABLE only because the list is ESPN's full list for that
  team-game; a missing / stale snapshot is UNKNOWN, never healthy.

SOURCES AND CLOCKS
------------------
ESPN's free public game summary (injuries + header) and team schedule / box scores, collected by
``collect-nba-availability`` into immutable, content-addressed files under ``NBA_AVAILABILITY_ROOT``. The decision
reads only files AISP already holds (observed at or before the decision clock); it makes no request. The official
NBA injury report (PDF) is the PM's capture/parser; when its exact player crosswalk exists it joins here as a second
status source (worst status wins). $0; no key; no provider budget.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

RULE = "AISP_OWNER_NBA_STAR_OUT_RECORD_ONLY_V1"
REASON_OUT = "OWNER_NBA_STAR_OUT_RECORD_ONLY"
REASON_UNKNOWN = "NBA_STAR_AVAILABILITY_UNKNOWN_RECORD_ONLY"
SETTING = "nba_star_out_gate_effective_from"
SOURCE = "ESPN_PUBLIC_SUMMARY"
SCHEMA_SNAPSHOT = "AISP_NBA_AVAILABILITY_SNAPSHOT_V1"
SCHEMA_BOX = "AISP_NBA_BOX_MINUTES_V1"
BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ROLE_FREEZE = timedelta(minutes=90)
SNAPSHOT_MAX_AGE = timedelta(minutes=45)
COLLECT_AHEAD = timedelta(hours=8)
TRIGGER = frozenset({"OUT", "DOUBTFUL", "QUESTIONABLE"})
TIMEOUT = 15.0


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _when(text: Any) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat(str(text).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def root(settings: Any) -> Path:
    configured = getattr(settings, "nba_availability_root", None)
    return Path(configured) if configured else Path(__file__).resolve().parents[3] / ".runtime" / "nba-availability"


def normalize_status(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s == "out" or s.startswith("out"):
        return "OUT"
    if s.startswith("doubt"):
        return "DOUBTFUL"
    if s.startswith("question") or s.replace("_", "-") in ("day-to-day", "gtd") or s.startswith("day-to-day"):
        return "QUESTIONABLE"
    if s.startswith("prob"):
        return "PROBABLE"
    if s in ("active", "available"):
        return "AVAILABLE"
    return "UNKNOWN"


def _minutes(value: Any) -> float:
    text = str(value or "").strip()
    if not text or text in ("--", "-"):
        return 0.0
    if ":" in text:
        m, _, s = text.partition(":")
        try:
            return int(m) + int(s) / 60
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _write_once(path: Path, data: bytes) -> None:
    """Content-addressed, never overwritten: a second identical write is a no-op."""

    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _dump(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


# ----------------------------------------------------------------- parsing --
def parse_snapshot(raw: dict[str, Any], *, espn_game_id: str, observed_at: datetime, url: str) -> dict[str, Any]:
    """The injury lists and the two teams from one ESPN game summary."""

    comps = (((raw.get("header") or {}).get("competitions") or [{}])[0].get("competitors") or [])
    teams = {c.get("homeAway"): {"espn_team_id": str((c.get("team") or {}).get("id")),
                                 "abbreviation": (c.get("team") or {}).get("abbreviation")} for c in comps}
    injuries: dict[str, list[dict[str, Any]]] = {}
    for block in raw.get("injuries") or []:
        tid = str((block.get("team") or {}).get("id"))
        injuries[tid] = [{"espn_athlete_id": str((i.get("athlete") or {}).get("id")),
                          "name": (i.get("athlete") or {}).get("displayName"),
                          "raw_status": i.get("status"), "status": normalize_status(i.get("status")),
                          "listed_at": i.get("date")} for i in block.get("injuries") or []]
    return {"schema": SCHEMA_SNAPSHOT, "source": SOURCE, "source_url": url, "espn_game_id": espn_game_id,
            "observed_at_utc": _aware(observed_at).isoformat(), "teams": teams, "injuries": injuries,
            "injury_section_present": "injuries" in raw}


def parse_box(raw: dict[str, Any], *, espn_event_id: str) -> dict[str, Any]:
    comp = (((raw.get("header") or {}).get("competitions") or [{}])[0])
    completed = bool(((comp.get("status") or {}).get("type") or {}).get("completed"))
    season_type = ((raw.get("header") or {}).get("season") or {}).get("type")
    teams: dict[str, list[dict[str, Any]]] = {}
    for block in ((raw.get("boxscore") or {}).get("players") or []):
        tid = str((block.get("team") or {}).get("id"))
        stats = (block.get("statistics") or [{}])[0]
        labels = [str(x).upper() for x in (stats.get("labels") or stats.get("names") or [])]
        idx = labels.index("MIN") if "MIN" in labels else None
        rows = []
        for a in stats.get("athletes") or []:
            mins = 0.0 if a.get("didNotPlay") or idx is None else _minutes((a.get("stats") or [None] * (idx + 1))[idx] if len(a.get("stats") or []) > idx else None)
            rows.append({"espn_athlete_id": str((a.get("athlete") or {}).get("id")), "name": (a.get("athlete") or {}).get("displayName"), "minutes": round(mins, 2)})
        teams[tid] = rows
    return {"schema": SCHEMA_BOX, "espn_event_id": espn_event_id, "date": comp.get("date"), "completed": completed,
            "season_type": season_type, "teams": teams}


# ------------------------------------------------------------------ collect --
def _http_get(url: str) -> bytes:
    import httpx

    with httpx.Client(timeout=TIMEOUT, follow_redirects=False,
                      headers={"Accept": "application/json", "User-Agent": "AISP-nba-availability/1.0"}) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content


def due_games(session: Any, *, now: datetime) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from app.models import ExternalIdMapping, Game, GameStatus, League
    from app.nba import gen1_common as common

    rows = session.execute(
        select(Game.id, Game.starts_at, ExternalIdMapping.external_id)
        .join(League, League.id == Game.league_id)
        .join(ExternalIdMapping, (ExternalIdMapping.entity_id == Game.id)
              & (ExternalIdMapping.entity_type == "game") & (ExternalIdMapping.source == common.SOURCE_ESPN_GAME))
        .where(League.code == common.LEAGUE_CODE, Game.status == GameStatus.SCHEDULED,
               Game.starts_at > now, Game.starts_at <= now + COLLECT_AHEAD)
        .order_by(Game.starts_at)).all()
    return [{"game_id": str(g), "starts_at": _aware(s), "espn_game_id": str(e)} for g, s, e in rows]


def collect(session: Any, settings: Any, *, now: datetime | None = None,
            fetch: Callable[[str], bytes] | None = None) -> dict[str, Any]:
    """One cycle: a fresh injury snapshot for every NBA game in the next 8 hours, and any missing box score of the
    two teams' completed games. Writes files only; $0; never raises on a failed request."""

    base = {"source": SOURCE, "costUsd": 0, "rule": RULE}
    if not getattr(settings, "nba_availability_collector_enabled", False):
        return {**base, "status": "DISABLED", "requests": 0,
                "detail": "NBA_AVAILABILITY_COLLECTOR_ENABLED is off; no request is made."}
    moment = _aware(now or datetime.now(UTC))
    games = due_games(session, now=moment)
    if not games:
        return {**base, "status": "NO_NBA_GAME_IN_THE_NEXT_8_HOURS", "requests": 0}
    get = fetch or _http_get
    out = root(settings)
    requests = 0
    failures: list[str] = []
    snapshots = 0
    boxes = 0
    teams: set[str] = set()
    for g in games:
        url = f"{BASE}/summary?event={g['espn_game_id']}"
        try:
            raw = get(url); requests += 1
            observed = datetime.now(UTC) if now is None else moment
            snap = parse_snapshot(json.loads(raw), espn_game_id=g["espn_game_id"], observed_at=observed, url=url)
            data = _dump(snap)
            sha = hashlib.sha256(data).hexdigest()
            _write_once(out / "snapshots" / g["espn_game_id"] / f"{observed.strftime('%Y%m%dT%H%M%SZ')}_{sha[:16]}.json", data)
            snapshots += 1
            teams.update(t["espn_team_id"] for t in snap["teams"].values() if t.get("espn_team_id"))
        except Exception as error:  # noqa: BLE001 - reported, never raised
            failures.append(f"summary {g['espn_game_id']}: {type(error).__name__}")
    # The schedule is re-read every 4 hours, so last night's late finish is never missed for long.
    day = f"{moment.strftime('%Y%m%d')}-{moment.hour // 4}"
    for tid in sorted(teams):
        sched_path = out / "schedules" / tid / f"{day}.json"
        try:
            if sched_path.exists():
                sched = json.loads(sched_path.read_bytes())
            else:
                sched = json.loads(get(f"{BASE}/teams/{tid}/schedule?seasontype=2")); requests += 1
                compact = {"team": tid, "season": (sched.get("season") or {}).get("year"), "season_type": 2,
                           "held_at_utc": datetime.now(UTC).isoformat(),
                           "events": [{"id": str(e.get("id")), "date": e.get("date"),
                                       "completed": bool((((e.get("competitions") or [{}])[0].get("status") or {}).get("type") or {}).get("completed"))}
                                      for e in sched.get("events") or []]}
                _write_once(sched_path, _dump(compact))
                sched = compact
            for e in sched.get("events") or []:
                if not e.get("completed"):
                    continue
                box_path = out / "box" / f"{e['id']}.json"
                if box_path.exists():
                    continue
                box = parse_box(json.loads(get(f"{BASE}/summary?event={e['id']}")), espn_event_id=e["id"]); requests += 1
                if box["completed"]:
                    box["held_at_utc"] = datetime.now(UTC).isoformat()
                    _write_once(box_path, _dump(box)); boxes += 1
        except Exception as error:  # noqa: BLE001
            failures.append(f"team {tid}: {type(error).__name__}")
    return {**base, "status": "OK" if not failures else "PARTIAL", "games": len(games), "snapshots": snapshots,
            "box_scores_added": boxes, "requests": requests, "failures": failures[:20]}


# ------------------------------------------------------------------- assess --
def _latest_snapshot(out: Path, espn_game_id: str, moment: datetime) -> dict[str, Any] | None:
    folder = out / "snapshots" / espn_game_id
    if not folder.exists():
        return None
    best = None
    for p in folder.glob("*.json"):
        try:
            doc = json.loads(p.read_bytes())
        except (OSError, ValueError):
            continue
        observed = _when(doc.get("observed_at_utc"))
        if observed is None or observed > moment:
            continue
        if best is None or observed > best[0]:
            best = (observed, doc, hashlib.sha256(p.read_bytes()).hexdigest())
    if best is None:
        return None
    return {**best[1], "_observed": best[0], "_sha256": best[2]}


def _team_roles(out: Path, team_id: str, freeze: datetime, listed: set[str]) -> dict[str, Any]:
    sched_dir = out / "schedules" / team_id
    files = sorted(sched_dir.glob("*.json")) if sched_dir.exists() else []
    if not files:
        return {"state": "ROLE_UNKNOWN", "reason": "NO_TEAM_SCHEDULE_HELD"}
    sched = json.loads(files[-1].read_bytes())
    done = sorted([e for e in sched.get("events") or [] if e.get("completed") and (_when(e.get("date")) or freeze) < freeze],
                  key=lambda e: e.get("date") or "")
    if len(done) < 2:
        return {"state": "ROLE_UNKNOWN", "reason": "FEWER_THAN_2_COMPLETED_GAMES", "games": len(done)}
    played: dict[str, list[float]] = {}
    names: dict[str, str] = {}
    recent: set[str] = set()
    for k, e in enumerate(done):
        p = out / "box" / f"{e['id']}.json"
        if not p.exists():
            return {"state": "ROLE_UNKNOWN", "reason": "BOX_SCORE_NOT_HELD", "event": e["id"], "games": len(done)}
        box = json.loads(p.read_bytes())
        for row in (box.get("teams") or {}).get(team_id, []):
            if row.get("minutes", 0) > 0:
                played.setdefault(row["espn_athlete_id"], []).append(float(row["minutes"]))
                names[row["espn_athlete_id"]] = row.get("name") or row["espn_athlete_id"]
                if k >= len(done) - 5:
                    recent.add(row["espn_athlete_id"])
    need = max(2, math.ceil(len(done) / 4))
    ranked = sorted(((sum(m) / len(m), aid) for aid, m in played.items()
                     if len(m) >= need and (aid in recent or aid in listed)), key=lambda t: (-t[0], t[1]))
    if len(ranked) < 2:
        return {"state": "ROLE_UNKNOWN", "reason": "FEWER_THAN_2_ELIGIBLE_PLAYERS", "games": len(done)}
    return {"state": "KNOWN", "games": len(done), "min_appearances": need,
            "stars": [{"espn_athlete_id": aid, "name": names[aid], "mpg": round(mpg, 1), "appearances": len(played[aid])} for mpg, aid in ranked[:2]]}


def assess(game: dict[str, Any], moment: datetime, settings: Any) -> dict[str, Any]:
    """The availability receipt for one game at the decision clock, from files AISP already holds."""

    moment = _aware(moment)
    out = root(settings)
    receipt: dict[str, Any] = {"rule": RULE, "source": SOURCE, "evaluated_at_utc": moment.isoformat(),
                               "espn_game_id": str(game.get("espn_game_id")), "triggers": sorted(TRIGGER)}
    snap = _latest_snapshot(out, str(game.get("espn_game_id")), moment)
    if snap is None:
        return {**receipt, "state": "UNKNOWN", "reason": "NO_AVAILABILITY_SNAPSHOT_HELD"}
    age = moment - snap["_observed"]
    receipt.update({"snapshot_observed_at_utc": snap["_observed"].isoformat(), "snapshot_sha256": snap["_sha256"],
                    "snapshot_age_minutes": round(age.total_seconds() / 60, 1)})
    if age > SNAPSHOT_MAX_AGE:
        return {**receipt, "state": "UNKNOWN", "reason": "AVAILABILITY_SNAPSHOT_STALE"}
    if not snap.get("injury_section_present"):
        return {**receipt, "state": "UNKNOWN", "reason": "NO_INJURY_SECTION_IN_SOURCE"}
    starts = _aware(game["starts_at"]) if isinstance(game.get("starts_at"), datetime) else _when(game.get("starts_at"))
    freeze = (starts or moment) - ROLE_FREEZE
    # Role eligibility is frozen using only injury identities held by T-90.
    role_snap = _latest_snapshot(out, str(game.get("espn_game_id")), min(moment, freeze))
    if role_snap is None:
        # No injury list held by T-90 (collector down earlier): roles cannot be frozen with what was known, fail closed.
        return {**receipt, "state": "UNKNOWN", "reason": "NO_INJURY_LIST_HELD_BY_T90"}
    teams_out: dict[str, Any] = {}
    state = "CLEAR"
    reason = "NO_STAR_LISTED"
    for side in ("away", "home"):
        team = (snap.get("teams") or {}).get(side) or {}
        tid = team.get("espn_team_id")
        team_list_present = str(tid) in (snap.get("injuries") or {})
        listed = {i["espn_athlete_id"]: i for i in (snap.get("injuries") or {}).get(str(tid), [])}
        role_listed = {i["espn_athlete_id"] for i in ((role_snap or {}).get("injuries") or {}).get(str(tid), [])}
        roles = _team_roles(out, str(tid), min(moment, freeze), role_listed) if tid else {"state": "ROLE_UNKNOWN", "reason": "NO_TEAM_IN_SNAPSHOT"}
        entry = {"abbreviation": team.get("abbreviation"), "espn_team_id": tid, "role_state": roles["state"],
                 "role_detail": {k: v for k, v in roles.items() if k not in ("stars", "state")},
                 "team_injury_list_present": team_list_present, "stars": []}
        if not team_list_present and state != "STAR_OUT":
            state, reason = "UNKNOWN", f"{side.upper()}_TEAM_INJURY_LIST_NOT_PRESENT"
        if roles["state"] != "KNOWN":
            if state != "STAR_OUT":
                state, reason = "UNKNOWN", f"{side.upper()}_{roles.get('reason', 'ROLE_UNKNOWN')}"
        else:
            for star in roles["stars"]:
                row = listed.get(star["espn_athlete_id"])
                status = row["status"] if row else ("AVAILABLE" if team_list_present else "UNKNOWN")
                entry["stars"].append({**star, "status": status, "raw_status": row["raw_status"] if row else None,
                                       "listed_at": row["listed_at"] if row else None})
                if status in TRIGGER:
                    state, reason = "STAR_OUT", f"{team.get('abbreviation')} {star['name']} {status}"
                elif status == "UNKNOWN" and state == "CLEAR":
                    state, reason = "UNKNOWN", f"{team.get('abbreviation')} {star['name']} status unrecognised"
        teams_out[side] = entry
    return {**receipt, "state": state, "reason": reason, "role_freeze_utc": freeze.isoformat(), "teams": teams_out}


def gate_active(settings: Any, moment: datetime) -> bool:
    clock = _when(getattr(settings, SETTING, None)) if getattr(settings, SETTING, None) else None
    return clock is not None and _aware(moment) >= clock


def apply(decision: dict[str, Any], game: dict[str, Any], moment: datetime, settings: Any) -> dict[str, Any]:
    """Attach the availability receipt to every NBA decision; when the gate is in force a QUALIFIED decision whose
    stars are not CLEAR becomes RECORD_ONLY (prices and value kept, no position)."""

    try:
        receipt = assess(game, moment, settings)
    except Exception as error:  # noqa: BLE001 - an unreadable source is UNKNOWN, never healthy
        receipt = {"rule": RULE, "state": "UNKNOWN", "reason": f"ASSESSMENT_FAILED:{type(error).__name__}"}
    result = {**decision, "availability": receipt}
    try:
        from app.nba.injury_research import features
        result["injury_features"] = features(game, _aware(moment), root(settings))  # nonfunding research receipt
    except Exception as error:  # noqa: BLE001
        result["injury_features"] = {"state": "UNKNOWN", "reason": f"FEATURES_FAILED:{type(error).__name__}", "funding_authority": False}
    if decision.get("outcome") != "QUALIFIED" or not gate_active(settings, moment) or receipt.get("state") == "CLEAR":
        return result
    reason = REASON_OUT if receipt.get("state") == "STAR_OUT" else REASON_UNKNOWN
    selection = decision.get("selection") or decision.get("best") or {}
    restriction = {"rule": RULE, "record_only": True, "reason": reason, "sport": "NBA",
                   "effective_from_utc": _when(getattr(settings, SETTING)).isoformat(),
                   "availability_state": receipt.get("state"), "availability_reason": receipt.get("reason"),
                   "original_qualification": {"outcome": decision.get("outcome"), "reason": decision.get("reason")},
                   "sportsbook": selection.get("sportsbook"), "snapshot_id": str(selection.get("odds_snapshot_id") or ""),
                   "funding_checked_at_utc": _aware(moment).isoformat()}
    return {**result, "funding_restriction": restriction, "outcome": "RECORD_ONLY", "reason": reason,
            "detail": f"Owner star-out rule: {receipt.get('reason')}; retained decision and prices; no position. {reason}"}
