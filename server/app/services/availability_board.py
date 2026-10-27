"""Injury and key-player availability for EVERY sport (owner 2026-10-27: "we really need injury data for every sport
... do whatever it takes except spend a dime").

Free, keyless ESPN public feeds only ($0):
  * per-game summary   -> injury lists (NBA, NFL, NHL, MLB; ESPN publishes none for college football), MLB probables
  * team schedule + box scores -> NBA minutes / NHL time on ice and goalie starts
  * team depth charts  -> NFL and CFB starting quarterback

KEY PLAYERS (frozen 2026-10-27; reported on every game, never a probability change)
  NBA  the top-2 minutes players per team (the same rule as the NBA star-out gate, app/nba/availability.py)
  NHL  the top-2 skaters by points per appearance (time on ice breaks ties), plus the goalie with the most starts in
       the last 5 games (ranking by time on ice alone picked defencemen over McDavid / Bedard)
  NFL  QB1 on ESPN's depth chart          CFB  QB1 on ESPN's depth chart (no injury list exists; status UNKNOWN)
  MLB  the probable starting pitchers

A key player listed OUT / DOUBTFUL / QUESTIONABLE makes the game KEY_OUT; missing / stale data is UNKNOWN, never
healthy. Only the NBA gate acts on funding (owner-approved); for every other sport this is information and a
decision-time receipt source. Files are immutable and content-addressed under <NBA_AVAILABILITY_ROOT>/leagues/<lg>/;
the day index is the only rewritten file.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.nba.availability import (
    ROLE_FREEZE, TRIGGER, _aware, _dump, _http_get, _minutes, _team_roles, _when, _write_once, normalize_status, root,
)

ET = ZoneInfo("America/New_York")
BASE = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUES: dict[str, dict[str, Any]] = {
    "NBA": {"path": "basketball/nba", "role": "minutes", "board": ""},
    "NHL": {"path": "hockey/nhl", "role": "nhl", "board": ""},
    "NFL": {"path": "football/nfl", "role": "qb", "board": ""},
    "CFB": {"path": "football/college-football", "role": "qb", "board": "&groups=80"},
    "MLB": {"path": "baseball/mlb", "role": "probables", "board": ""},
}
AHEAD = timedelta(hours=36)
NEAR = timedelta(hours=8)
FRESH_NEAR = timedelta(minutes=10)
FRESH_FAR = timedelta(minutes=60)
STALE_AFTER = timedelta(minutes=45)
LONG_TERM = ("reserve", "-il", " il", "il-", "60-day", "10-day", "15-day", "season")


def _lg_root(settings: Any, lg: str) -> Path:
    return root(settings) / "leagues" / lg.lower()


def _et_day(moment: datetime) -> str:
    return moment.astimezone(ET).strftime("%Y-%m-%d")


def _long_term(raw: str | None) -> bool:
    text = f" {str(raw or '').lower()} "
    return any(k in text for k in LONG_TERM)


# ---------------------------------------------------------------- parsing --
def parse_summary(raw: dict[str, Any], *, league: str, event_id: str, observed_at: datetime, url: str) -> dict[str, Any]:
    comp = (((raw.get("header") or {}).get("competitions") or [{}])[0])
    teams, probables = {}, {}
    for c in comp.get("competitors") or []:
        t = c.get("team") or {}
        teams[c.get("homeAway")] = {"espn_team_id": str(t.get("id")), "abbreviation": t.get("abbreviation"), "name": t.get("displayName")}
        probables[str(t.get("id"))] = [{"espn_athlete_id": str((p.get("athlete") or {}).get("id")), "name": (p.get("athlete") or {}).get("displayName")}
                                       for p in c.get("probables") or [] if (p.get("athlete") or {}).get("id")]
    injuries = {}
    for block in raw.get("injuries") or []:
        tid = str((block.get("team") or {}).get("id"))
        injuries[tid] = [{"espn_athlete_id": str((i.get("athlete") or {}).get("id")), "name": (i.get("athlete") or {}).get("displayName"),
                          "position": ((i.get("athlete") or {}).get("position") or {}).get("abbreviation"),
                          "raw_status": i.get("status"), "status": normalize_status(i.get("status")) if not _long_term(i.get("status")) else "OUT",
                          "long_term": _long_term(i.get("status")), "listed_at": i.get("date"),
                          "detail": ((i.get("details") or {}).get("type") if isinstance(i.get("details"), dict) else None)}
                         for i in block.get("injuries") or []]
    return {"schema": "AISP_AVAILABILITY_SNAPSHOT_V1", "league": league, "espn_event_id": event_id, "source_url": url,
            "observed_at_utc": _aware(observed_at).isoformat(), "starts_at": comp.get("date"), "teams": teams,
            "injuries": injuries, "probables": probables, "injury_section_present": "injuries" in raw}


def parse_box(raw: dict[str, Any], *, event_id: str) -> dict[str, Any]:
    """Minutes (NBA MIN, NHL skater TOI) per player and NHL goalie appearances, per team."""

    comp = (((raw.get("header") or {}).get("competitions") or [{}])[0])
    teams: dict[str, list[dict[str, Any]]] = {}
    for block in ((raw.get("boxscore") or {}).get("players") or []):
        tid = str((block.get("team") or {}).get("id"))
        rows: list[dict[str, Any]] = []
        for group in block.get("statistics") or []:
            labels = [str(x).upper() for x in (group.get("labels") or group.get("names") or [])]
            goalie = str(group.get("name") or "").lower() == "goalies"
            idx = labels.index("TOI") if "TOI" in labels else labels.index("MIN") if "MIN" in labels else None
            gi, ai = (labels.index("G") if "G" in labels else None), (labels.index("A") if "A" in labels else None)
            for a in group.get("athletes") or []:
                stats = a.get("stats") or []
                mins = 0.0 if a.get("didNotPlay") or idx is None or idx >= len(stats) else _minutes(stats[idx])
                pts = sum(int(_minutes(stats[i])) for i in (gi, ai) if i is not None and i < len(stats))
                rows.append({"espn_athlete_id": str((a.get("athlete") or {}).get("id")), "name": (a.get("athlete") or {}).get("displayName"),
                             "minutes": 0.0 if goalie else round(mins, 2), "points": 0 if goalie else pts, "goalie": goalie, "appeared": goalie or mins > 0})
        teams[tid] = rows
    return {"schema": "AISP_BOX_MINUTES_V2", "espn_event_id": event_id, "date": comp.get("date"),
            "completed": bool(((comp.get("status") or {}).get("type") or {}).get("completed")), "teams": teams}


def _box_dir(lg: str) -> str:
    return "box2" if lg == "NHL" else "box"


def _nhl_skaters(out: Path, team_id: str, freeze: datetime, listed: set[str]) -> dict[str, Any]:
    files = sorted((out / "schedules" / team_id).glob("*.json")) if (out / "schedules" / team_id).exists() else []
    if not files:
        return {"state": "ROLE_UNKNOWN", "reason": "NO_TEAM_SCHEDULE_HELD"}
    done = sorted([e for e in json.loads(files[-1].read_bytes()).get("events") or [] if e.get("completed") and (_when(e.get("date")) or freeze) < freeze],
                  key=lambda e: e.get("date") or "")
    if len(done) < 2:
        return {"state": "ROLE_UNKNOWN", "reason": "FEWER_THAN_2_COMPLETED_GAMES"}
    acc: dict[str, list[float]] = {}
    names: dict[str, str] = {}
    recent: set[str] = set()
    for k, e in enumerate(done):
        p = out / "box2" / f"{e['id']}.json"
        if not p.exists():
            return {"state": "ROLE_UNKNOWN", "reason": "BOX_SCORE_NOT_HELD"}
        for r in (json.loads(p.read_bytes()).get("teams") or {}).get(team_id, []):
            if r.get("goalie") or r.get("minutes", 0) <= 0:
                continue
            a = acc.setdefault(r["espn_athlete_id"], [0, 0.0, 0.0]); a[0] += 1; a[1] += r.get("points", 0); a[2] += r["minutes"]
            names[r["espn_athlete_id"]] = r.get("name") or r["espn_athlete_id"]
            if k >= len(done) - 5:
                recent.add(r["espn_athlete_id"])
    need = max(2, -(-len(done) // 4))
    ranked = sorted(((v[1] / v[0], v[2] / v[0], aid, v[0]) for aid, v in acc.items() if v[0] >= need and (aid in recent or aid in listed)),
                    key=lambda t: (-t[0], -t[1], t[2]))
    if len(ranked) < 2:
        return {"state": "ROLE_UNKNOWN", "reason": "FEWER_THAN_2_ELIGIBLE_PLAYERS"}
    return {"state": "KNOWN", "stars": [{"espn_athlete_id": aid, "name": names[aid], "ppg": round(ppg, 2), "mpg": round(toi, 1), "appearances": n}
                                        for ppg, toi, aid, n in ranked[:2]]}


def qb1(depth: dict[str, Any]) -> dict[str, Any] | None:
    for chart in depth.get("depthchart") or []:
        qb = ((chart.get("positions") or {}).get("qb") or {}).get("athletes") or []
        if qb:
            return {"espn_athlete_id": str(qb[0].get("id")), "name": qb[0].get("displayName")}
    return None


# ---------------------------------------------------------------- collect --
def collect(session: Any, settings: Any, *, now: datetime | None = None,
            fetch: Callable[[str], bytes] | None = None, leagues: tuple[str, ...] = tuple(LEAGUES)) -> dict[str, Any]:
    """One cycle for every league: snapshot every game starting within 36 h (every 10 min inside 8 h, hourly
    beyond), plus the role inputs each league's key-player rule needs. $0; never raises on a failed request."""

    if not getattr(settings, "nba_availability_collector_enabled", False):
        return {"status": "DISABLED", "requests": 0, "costUsd": 0}
    moment = _aware(now or datetime.now(UTC))
    get = fetch or _http_get
    stats: dict[str, Any] = {"requests": 0, "snapshots": 0, "boxes": 0, "depth": 0, "failures": []}

    def fetch_json(url: str) -> dict[str, Any]:
        stats["requests"] += 1
        return json.loads(get(url))

    for lg in leagues:
        cfg = LEAGUES[lg]
        out = _lg_root(settings, lg)
        days = sorted({moment.astimezone(ET).date(), (moment + timedelta(hours=24)).astimezone(ET).date(), (moment + AHEAD).astimezone(ET).date()})
        teams: set[str] = set()
        for d in days:
            try:
                board = fetch_json(f"{BASE}/{cfg['path']}/scoreboard?dates={d.strftime('%Y%m%d')}{cfg['board']}&limit=300")
            except Exception as error:  # noqa: BLE001
                stats["failures"].append(f"{lg} scoreboard {d}: {type(error).__name__}"); continue
            index_path = out / "index" / f"{d.isoformat()}.json"
            index = json.loads(index_path.read_bytes()) if index_path.exists() else {}
            for ev in board.get("events") or []:
                start = _when(ev.get("date"))
                comp = (ev.get("competitions") or [{}])[0]
                state = ((comp.get("status") or {}).get("type") or {}).get("state")
                eid = str(ev.get("id"))
                index[eid] = {"name": ev.get("name"), "starts_at": ev.get("date"), "state": state,
                              "teams": {c.get("homeAway"): {"espn_team_id": str((c.get("team") or {}).get("id")), "abbreviation": (c.get("team") or {}).get("abbreviation")} for c in comp.get("competitors") or []}}
                if start is None or state != "pre" or not (moment < start <= moment + AHEAD):
                    continue
                folder = out / "snapshots" / eid
                latest = max((p.stat().st_mtime for p in folder.glob("*.json")), default=None) if folder.exists() else None
                need = FRESH_NEAR if start - moment <= NEAR else FRESH_FAR
                for side in index[eid]["teams"].values():
                    teams.add(side["espn_team_id"])
                if latest is not None and moment.timestamp() - latest < need.total_seconds() - 30:
                    continue
                url = f"{BASE}/{cfg['path']}/summary?event={eid}"
                try:
                    observed = datetime.now(UTC) if now is None else moment
                    data = _dump(parse_summary(fetch_json(url), league=lg, event_id=eid, observed_at=observed, url=url))
                    _write_once(folder / f"{observed.strftime('%Y%m%dT%H%M%SZ')}_{hashlib.sha256(data).hexdigest()[:16]}.json", data)
                    stats["snapshots"] += 1
                except Exception as error:  # noqa: BLE001
                    stats["failures"].append(f"{lg} summary {eid}: {type(error).__name__}")
            index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = index_path.with_suffix(".tmp"); tmp.write_bytes(_dump(index)); tmp.replace(index_path)
        # role inputs
        stamp = f"{moment.strftime('%Y%m%d')}-{moment.hour // 4}"
        for tid in sorted(teams):
            try:
                if cfg["role"] in ("minutes", "nhl"):
                    sched_path = out / "schedules" / tid / f"{stamp}.json"
                    if not sched_path.exists():
                        sched = fetch_json(f"{BASE}/{cfg['path']}/teams/{tid}/schedule?seasontype=2")
                        _write_once(sched_path, _dump({"team": tid, "events": [{"id": str(e.get("id")), "date": e.get("date"),
                                    "completed": bool((((e.get("competitions") or [{}])[0].get("status") or {}).get("type") or {}).get("completed"))} for e in sched.get("events") or []]}))
                    for e in json.loads(sched_path.read_bytes()).get("events") or []:
                        box_path = out / _box_dir(lg) / f"{e['id']}.json"
                        if e.get("completed") and not box_path.exists():
                            box = parse_box(fetch_json(f"{BASE}/{cfg['path']}/summary?event={e['id']}"), event_id=e["id"])
                            if box["completed"]:
                                _write_once(box_path, _dump(box)); stats["boxes"] += 1
                elif cfg["role"] == "qb":
                    depth_path = out / "depth" / tid / f"{moment.strftime('%Y%m%d')}.json"
                    if not depth_path.exists():
                        _write_once(depth_path, _dump({"team": tid, "qb1": qb1(fetch_json(f"{BASE}/{cfg['path']}/teams/{tid}/depthcharts")),
                                                       "observed_at_utc": moment.isoformat()}))
                        stats["depth"] += 1
            except Exception as error:  # noqa: BLE001
                stats["failures"].append(f"{lg} team {tid}: {type(error).__name__}")
    stats["failures"] = stats["failures"][:30]
    return {"status": "OK" if not stats["failures"] else "PARTIAL", "costUsd": 0, "source": "ESPN_PUBLIC", **stats}


# ------------------------------------------------------------------ board --
def _latest(folder: Path, moment: datetime) -> dict[str, Any] | None:
    best = None
    for p in folder.glob("*.json") if folder.exists() else []:
        try:
            raw = p.read_bytes(); doc = json.loads(raw)
        except (OSError, ValueError):
            continue
        t = _when(doc.get("observed_at_utc"))
        if t is not None and t <= moment and (best is None or t > best[0]):
            best = (t, doc, hashlib.sha256(raw).hexdigest())
    return None if best is None else {**best[1], "_observed": best[0], "_sha256": best[2]}


def _nhl_goalie(out: Path, team_id: str, freeze: datetime) -> dict[str, Any] | None:
    files = sorted((out / "schedules" / team_id).glob("*.json")) if (out / "schedules" / team_id).exists() else []
    if not files:
        return None
    done = sorted([e for e in json.loads(files[-1].read_bytes()).get("events") or [] if e.get("completed") and (_when(e.get("date")) or freeze) < freeze],
                  key=lambda e: e.get("date") or "")[-5:]
    starts: dict[str, list[Any]] = {}
    for e in done:
        p = out / "box2" / f"{e['id']}.json"
        if not p.exists():
            return None
        goalies = [r for r in (json.loads(p.read_bytes()).get("teams") or {}).get(team_id, []) if r.get("goalie")]
        if goalies:  # the first goalie listed is the one who started
            g = goalies[0]; starts.setdefault(g["espn_athlete_id"], [g["name"], 0])[1] += 1
    if not starts:
        return None
    aid, (name, n) = sorted(starts.items(), key=lambda kv: (-kv[1][1], kv[0]))[0]
    return {"espn_athlete_id": aid, "name": name, "role": f"G1 · {n} of last {len(done)} starts"}


def game_view(settings: Any, lg: str, eid: str, meta: dict[str, Any], moment: datetime) -> dict[str, Any]:
    out = _lg_root(settings, lg)
    snap = _latest(out / "snapshots" / eid, moment)
    view: dict[str, Any] = {"league": lg, "espn_event_id": eid, "name": meta.get("name"), "starts_at": meta.get("starts_at"),
                            "teams": {}, "state": "UNKNOWN", "reason": "NO_SNAPSHOT_HELD"}
    if snap is None:
        view["teams"] = {s: {"abbreviation": t.get("abbreviation"), "key_players": [], "notable": []} for s, t in (meta.get("teams") or {}).items()}
        return view
    age = (moment - snap["_observed"]).total_seconds() / 60
    view.update({"snapshot_observed_at_utc": snap["_observed"].isoformat(), "snapshot_age_minutes": round(age, 1),
                 "snapshot_sha256": snap["_sha256"], "injury_feed": snap.get("injury_section_present")})
    starts = _when(snap.get("starts_at") or meta.get("starts_at")) or moment
    freeze = starts - ROLE_FREEZE
    state, reason = "CLEAR", "NO_KEY_PLAYER_LISTED"
    role = LEAGUES[lg]["role"]
    for side, team in (snap.get("teams") or {}).items():
        tid = team.get("espn_team_id")
        listed = {i["espn_athlete_id"]: i for i in (snap.get("injuries") or {}).get(str(tid), [])}
        keys: list[dict[str, Any]] = []
        known = True
        if role == "minutes" or role == "nhl":
            roles = _team_roles(out, str(tid), freeze, set(listed)) if role == "minutes" else _nhl_skaters(out, str(tid), freeze, set(listed))
            if roles["state"] == "KNOWN":
                keys = [{**s, "role": f"{s['mpg']} min/game" if role == "minutes" else f"{s['ppg']} pts/game"} for s in roles["stars"]]
            else:
                known = False
            if role == "nhl":
                g = _nhl_goalie(out, str(tid), freeze)
                if g: keys.append(g)
        elif role == "qb":
            files = sorted((out / "depth" / str(tid)).glob("*.json")) if (out / "depth" / str(tid)).exists() else []
            q = json.loads(files[-1].read_bytes()).get("qb1") if files else None
            if q: keys = [{**q, "role": "QB1"}]
            else: known = False
        elif role == "probables":
            keys = [{**p, "role": "Probable SP"} for p in (snap.get("probables") or {}).get(str(tid), [])]
            known = bool(keys)
        for k in keys:
            row = listed.get(k["espn_athlete_id"])
            k["status"] = row["status"] if row else ("AVAILABLE" if snap.get("injury_section_present") else "UNKNOWN")
            k["raw_status"] = row["raw_status"] if row else None
            if k["status"] in TRIGGER:
                state, reason = "KEY_OUT", f"{team.get('abbreviation')} {k['name']} {k['status']}"
        if (not known or not snap.get("injury_section_present")) and state == "CLEAR":
            state, reason = "UNKNOWN", ("NO_PUBLIC_INJURY_LIST" if not snap.get("injury_section_present") else f"{team.get('abbreviation')} KEY_PLAYERS_UNKNOWN")
        notable = sorted([i for i in listed.values() if i["status"] in TRIGGER],
                         key=lambda i: (i["long_term"], {"OUT": 0, "DOUBTFUL": 1, "QUESTIONABLE": 2}.get(i["status"], 3), i["name"] or ""))
        view["teams"][side] = {"abbreviation": team.get("abbreviation"), "name": team.get("name"), "key_players": keys,
                               "notable": notable[:8], "listed": len(listed)}
    if age > STALE_AFTER.total_seconds() / 60 and state == "CLEAR":
        state, reason = "UNKNOWN", "SNAPSHOT_STALE"
    view.update({"state": state, "reason": reason})
    return view


def board(settings: Any, day: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Every game AISP holds availability for on an ET date, all leagues."""

    moment = _aware(now or datetime.now(UTC))
    games = []
    for lg in LEAGUES:
        p = _lg_root(settings, lg) / "index" / f"{day}.json"
        if not p.exists():
            continue
        for eid, meta in json.loads(p.read_bytes()).items():
            try:
                games.append(game_view(settings, lg, eid, meta, moment))
            except Exception as error:  # noqa: BLE001 - one bad file never hides the board
                games.append({"league": lg, "espn_event_id": eid, "name": meta.get("name"), "state": "UNKNOWN", "reason": f"READ_FAILED:{type(error).__name__}", "teams": {}})
    games.sort(key=lambda g: g.get("starts_at") or "")
    return {"schema": "AISP_AVAILABILITY_BOARD_V1", "date": day, "generated_at_utc": moment.isoformat(), "source": "ESPN public feeds ($0)",
            "rules": {lg: cfg["role"] for lg, cfg in LEAGUES.items()}, "games": games}
