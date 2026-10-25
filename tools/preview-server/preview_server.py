"""AISP TEST-MODE PREVIEW on 127.0.0.1:5199.

Serves the client build named in current_dist.txt (next to this file) with an SPA
fallback to index.html, forwards GET /api/* to the production backend on 127.0.0.1:8010,
and answers every other method with 405 so the preview can never write anything
(the one POST, /preview-api/booth, asks a language model a question and writes nothing).
Repoint by editing current_dist.txt; it is re-read on every request. Requests are
appended to preview_requests.log.
"""
import mimetypes
import os
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
POINTER = HERE / "current_dist.txt"
DEFAULT_DIST = r"C:\Users\james\ai-sports-predictor\client\dist"
UPSTREAM = "http://127.0.0.1:8010"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5199
LOG = HERE / "preview_requests.log"
HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding",
       "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"}
FORWARD_REQ = {"accept", "if-none-match", "if-modified-since", "x-aisp-client"}
_lock = threading.Lock()

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")


def dist_dir() -> str:
    try:
        first = POINTER.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if first and os.path.isdir(first):
            return first
    except Exception:
        pass
    return DEFAULT_DIST


def log(line: str) -> None:
    with _lock:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + line + "\n")


# --- preview-only feeds (TEST MODE) --------------------------------------------
# /preview-api/mlb/live   GET  trimmed MLB Stats API live feed for the diamond (free, public, cached)
# /preview-api/booth      POST "Ask the Booth": one question -> Claude Haiku, grounded on the context
#                              the page sends. Explains; never predicts or recommends a bet.
# Neither touches AISP's database or any paid provider. The Anthropic key is James's
# user-level ANTHROPIC_API_KEY (process env, else HKCU\Environment).
import json
import re
import winreg
from collections import deque

STATS = "https://statsapi.mlb.com/api"
HEADSHOT = "https://img.mlbstatic.com/mlb-photos/image/upload/w_213,ar_1:1,c_fill,g_face,q_auto:best/v1/people/{pid}/headshot/67/current"
_cache: dict = {}
BOOTH_MODEL = "claude-haiku-5-5"
BOOTH_MAX_TOKENS = 200
BOOTH_RATE = deque(maxlen=30)  # timestamps of the last 30 questions (max 30 / 5 min)
BOOTH_LOG = HERE / "booth_log.jsonl"


def _anthropic_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, "ANTHROPIC_API_KEY")[0] or None
    except OSError:
        return None


def _fetch_json(url: str, ttl: float):
    now = time.time()
    hit = _cache.get(url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": "Python-urllib/3.12"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    _cache[url] = (now, data)
    return data


def _person(node):
    if not node or not isinstance(node, dict) or node.get("id") is None:
        return None
    return {"id": node["id"], "name": node.get("fullName"), "headshot": HEADSHOT.format(pid=node["id"])}


def _player_box(box_teams: dict, pid):
    if pid is None:
        return None
    key = f"ID{pid}"
    for side in ("away", "home"):
        p = (box_teams.get(side, {}).get("players") or {}).get(key)
        if p:
            return p
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _season(pid, group: str, season: str = "2026") -> dict | None:
    """Regular-season line for one player (cached 10 min). The boxscore's seasonStats are postseason-only in October."""
    if pid is None:
        return None
    try:
        data = _fetch_json(f"{STATS}/v1/people/{pid}/stats?stats=season&group={group}&season={season}&gameType=R", 600)
    except Exception:
        return None
    for st in data.get("stats", []) or []:
        for sp in st.get("splits", []) or []:
            if group == "fielding" and (sp.get("position") or {}).get("abbreviation") != "C":
                continue
            return sp.get("stat") or None
    return None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _regular_batting(pid):
    st = _season(pid, "hitting")
    if not st:
        return None
    return {"pa": _int(st.get("plateAppearances")), "k": _int(st.get("strikeOuts")), "bb": _int(st.get("baseOnBalls")), "hbp": _int(st.get("hitByPitch")),
            "h": _int(st.get("hits")), "hr": _int(st.get("homeRuns")), "doubles": _int(st.get("doubles")), "triples": _int(st.get("triples")),
            "gidp": _int(st.get("groundIntoDoublePlay")), "sb": _int(st.get("stolenBases")), "cs": _int(st.get("caughtStealing")),
            "avg": st.get("avg"), "obp": st.get("obp"), "slg": st.get("slg"), "ops": st.get("ops"), "go_ao": st.get("groundOutsToAirouts"),
            "games": _int(st.get("gamesPlayed"))}


def _regular_pitching(pid):
    st = _season(pid, "pitching")
    if not st:
        return None
    return {"bf": _int(st.get("battersFaced")), "k": _int(st.get("strikeOuts")), "bb": _int(st.get("baseOnBalls")), "hbp": _int(st.get("hitBatsmen")),
            "h": _int(st.get("hits")), "hr": _int(st.get("homeRuns")), "go_ao": st.get("groundOutsToAirouts"), "sb": _int(st.get("stolenBases")),
            "cs": _int(st.get("caughtStealing")), "era": st.get("era"), "whip": st.get("whip"), "k9": st.get("strikeoutsPer9Inn"), "bb9": st.get("walksPer9Inn"),
            "strike_pct": st.get("strikePercentage"), "ip": st.get("inningsPitched"), "gidp": _int(st.get("groundIntoDoublePlay"))}


def _regular_catching(pid):
    st = _season(pid, "fielding")
    if not st:
        return None
    return {"cs": _int(st.get("caughtStealing")), "sb_allowed": _int(st.get("stolenBases")), "cs_pct": st.get("caughtStealingPercentage"),
            "passed_ball": _int(st.get("passedBall")), "pickoffs": _int(st.get("pickoffs"))}


def _zones(matchup: dict):
    out = []
    try:
        for st in (matchup.get("batterHotColdZoneStats") or {}).get("stats", []) or []:
            for sp in st.get("splits", []) or []:
                if (sp.get("stat") or {}).get("name") != "onBasePlusSlugging":
                    continue
                for z in sp["stat"].get("zones", []) or []:
                    if z.get("value") not in (None, "-", ""):
                        out.append({"zone": z.get("zone"), "value": z.get("value"), "temp": z.get("temp")})
    except Exception:
        return []
    return out


def _pitch(e: dict):
    pd = e.get("pitchData") or {}
    co = pd.get("coordinates") or {}
    d = e.get("details") or {}
    return {"n": e.get("pitchNumber"), "pX": co.get("pX"), "pZ": co.get("pZ"), "szTop": pd.get("strikeZoneTop"), "szBot": pd.get("strikeZoneBottom"),
            "speed": pd.get("startSpeed"), "type": (d.get("type") or {}).get("description"), "call": (d.get("call") or {}).get("description"),
            "isStrike": d.get("isStrike"), "isBall": d.get("isBall"), "inPlay": d.get("isInPlay"), "zone": pd.get("zone"),
            "balls": (e.get("count") or {}).get("balls"), "strikes": (e.get("count") or {}).get("strikes"),
            "spin": (pd.get("breaks") or {}).get("spinRate")}


def _at_bat(play: dict):
    if not play:
        return None
    a, r = play.get("about") or {}, play.get("result") or {}
    pitches = [_pitch(e) for e in play.get("playEvents", []) or [] if e.get("isPitch")]
    batter = ((play.get("matchup") or {}).get("batter") or {})
    return {"atBatIndex": play.get("atBatIndex"), "inning": a.get("inning"), "half": a.get("halfInning"), "complete": a.get("isComplete"),
            "batter": batter.get("fullName"), "bat_side": ((play.get("matchup") or {}).get("batSide") or {}).get("code"),
            "event": r.get("event"), "description": r.get("description"), "is_out": r.get("isOut"), "pitches": pitches}


def _hit(play: dict):
    """The batted-ball record of one play, or None."""
    if not play:
        return None
    ev = None
    for e in play.get("playEvents", []) or []:
        if e.get("hitData") and (e.get("details") or {}).get("isInPlay"):  # fouls carry hitData too; only balls in play count
            ev = e
    if not ev:
        return None
    h, r, a = ev["hitData"], play.get("result") or {}, play.get("about") or {}
    if not r.get("event") or not a.get("isComplete"):
        return None
    co = h.get("coordinates") or {}
    fielder = None
    for rn in play.get("runners", []) or []:
        for c in rn.get("credits", []) or []:
            if c.get("credit") in ("f_putout", "f_fielded_ball", "f_assist"):
                fielder = fielder or (c.get("position") or {}).get("abbreviation")
    return {"key": f"{play.get('atBatIndex')}-{a.get('endTime') or ''}", "atBatIndex": play.get("atBatIndex"), "inning": a.get("inning"), "half": a.get("halfInning"),
            "coordX": co.get("coordX"), "coordY": co.get("coordY"), "speed": h.get("launchSpeed"), "angle": h.get("launchAngle"),
            "distance": h.get("totalDistance"), "trajectory": h.get("trajectory"), "hardness": h.get("hardness"), "location": h.get("location"),
            "event": r.get("event"), "description": r.get("description"), "is_out": bool(r.get("isOut")), "fielder": fielder,
            "batter": ((play.get("matchup") or {}).get("batter") or {}).get("fullName")}


def mlb_live(date: str, home: str, away: str) -> dict:
    sched = _fetch_json(f"{STATS}/v1/schedule?sportId=1&date={date}&hydrate=probablePitcher,team", 600)
    game = None
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            ha = g["teams"]["home"]["team"].get("abbreviation", "")
            aa = g["teams"]["away"]["team"].get("abbreviation", "")
            if ha.upper() == home.upper() and aa.upper() == away.upper():
                game = g
    if game is None:
        return {"source": "MLB_STATS_API", "found": False, "reason": f"No MLB game {away} @ {home} on {date} in the MLB schedule."}
    pk = game["gamePk"]
    feed = _fetch_json(f"{STATS}/v1.1/game/{pk}/feed/live", 8)
    try:
        wp_raw = _fetch_json(f"{STATS}/v1/game/{pk}/winProbability", 20)
    except Exception:
        wp_raw = []
    gd, ld = feed.get("gameData", {}), feed.get("liveData", {})
    ls = ld.get("linescore", {}) or {}
    plays = ld.get("plays", {}) or {}
    box = (ld.get("boxscore", {}) or {}).get("teams", {}) or {}
    status = gd.get("status", {}) or {}
    offense, defense = ls.get("offense", {}) or {}, ls.get("defense", {}) or {}
    cur = plays.get("currentPlay", {}) or {}
    matchup = cur.get("matchup", {}) or {}
    batter = _person(offense.get("batter")) or _person(matchup.get("batter"))
    pitcher = _person(defense.get("pitcher")) or _person(matchup.get("pitcher"))
    bbox, pbox = _player_box(box, batter and batter["id"]), _player_box(box, pitcher and pitcher["id"])

    def batting(p):
        if not p:
            return None
        s, g = (p.get("seasonStats") or {}).get("batting") or {}, (p.get("stats") or {}).get("batting") or {}
        return {"season": {k: s.get(k) for k in ("avg", "obp", "slg", "ops", "homeRuns", "rbi", "hits", "atBats", "strikeOuts", "baseOnBalls", "stolenBases")},
                "game": {k: g.get(k) for k in ("summary", "hits", "atBats", "runs", "rbi", "homeRuns", "baseOnBalls", "strikeOuts")},
                "position": (p.get("position") or {}).get("abbreviation"), "jersey": p.get("jerseyNumber")}

    def pitching(p):
        if not p:
            return None
        s, g = (p.get("seasonStats") or {}).get("pitching") or {}, (p.get("stats") or {}).get("pitching") or {}
        return {"season": {k: s.get(k) for k in ("era", "whip", "inningsPitched", "strikeOuts", "baseOnBalls", "wins", "losses", "saves", "homeRuns")},
                "game": {k: g.get(k) for k in ("summary", "inningsPitched", "hits", "runs", "earnedRuns", "baseOnBalls", "strikeOuts", "pitchesThrown", "numberOfPitches", "strikes", "homeRuns")},
                "jersey": p.get("jerseyNumber")}

    all_plays = plays.get("allPlays", []) or []
    recent = []
    for p in all_plays[-12:]:
        r, a = p.get("result", {}) or {}, p.get("about", {}) or {}
        if not r.get("description"):
            continue
        recent.append({"atBatIndex": p.get("atBatIndex"), "inning": a.get("inning"), "half": a.get("halfInning"),
                       "event": r.get("event"), "description": r.get("description"), "rbi": r.get("rbi"),
                       "awayScore": r.get("awayScore"), "homeScore": r.get("homeScore"), "isScoringPlay": a.get("isScoringPlay"),
                       "endTime": a.get("endTime")})
    wp = []
    for w in wp_raw or []:
        a = w.get("about", {}) or {}
        hp = _num(w.get("homeTeamWinProbability"))
        if hp is None:
            continue
        res = w.get("result") or {}
        wp.append({"atBatIndex": w.get("atBatIndex"), "inning": a.get("inning"), "half": a.get("halfInning"), "home": round(hp / 100, 4),
                   "event": res.get("event"), "awayScore": res.get("awayScore"), "homeScore": res.get("homeScore")})
    teams = {}
    for side in ("away", "home"):
        t = (gd.get("teams", {}) or {}).get(side, {}) or {}
        pp = (game["teams"][side].get("probablePitcher") or {})
        teams[side] = {"id": t.get("id"), "abbr": t.get("abbreviation"), "name": t.get("name"), "record": (t.get("record") or {}).get("leagueRecord"),
                       "probable": _person(pp) if pp else None}
    innings = [{"num": i.get("num"), "away": (i.get("away") or {}).get("runs"), "home": (i.get("home") or {}).get("runs")} for i in ls.get("innings", []) or []]
    runners = {base: _person(offense.get(base)) for base in ("first", "second", "third")}

    def defender(k):
        return _person(defense.get(k))

    venue = gd.get("venue", {}) or {}
    weather = gd.get("weather", {}) or {}
    lt = ls.get("teams", {}) or {}
    return {
        "source": "MLB_STATS_API", "found": True, "game_pk": pk, "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "series": game.get("seriesDescription"), "series_game": game.get("seriesGameNumber"), "venue": venue.get("name"),
        "weather": {"condition": weather.get("condition"), "temp": weather.get("temp"), "wind": weather.get("wind")},
        "status": {"abstract": status.get("abstractGameState"), "detailed": status.get("detailedState"), "inning": ls.get("currentInning"),
                   "inning_ordinal": ls.get("currentInningOrdinal"), "inning_state": ls.get("inningState"), "is_top": ls.get("isTopInning"),
                   "scheduled_innings": ls.get("scheduledInnings")},
        "score": {"away": (lt.get("away") or {}).get("runs"), "home": (lt.get("home") or {}).get("runs"),
                  "away_hits": (lt.get("away") or {}).get("hits"), "home_hits": (lt.get("home") or {}).get("hits"),
                  "away_errors": (lt.get("away") or {}).get("errors"), "home_errors": (lt.get("home") or {}).get("errors")},
        "count": {"balls": ls.get("balls"), "strikes": ls.get("strikes"), "outs": ls.get("outs")},
        "runners": runners, "teams": teams, "innings": innings,
        "offense": {"batter": batter, "on_deck": _person(offense.get("onDeck")), "in_hole": _person(offense.get("inHole")),
                    "batting_order": offense.get("battingOrder"), "team_id": (offense.get("team") or {}).get("id"),
                    "bat_side": (matchup.get("batSide") or {}).get("code")},
        "defense": {"pitcher": pitcher, "pitch_hand": (matchup.get("pitchHand") or {}).get("code"), "team_id": (defense.get("team") or {}).get("id"),
                    "catcher": defender("catcher"), "first": defender("first"), "second": defender("second"), "third": defender("third"),
                    "shortstop": defender("shortstop"), "left": defender("left"), "center": defender("center"), "right": defender("right")},
        "batter_stats": batting(bbox), "pitcher_stats": pitching(pbox),
        "regular": {
            "season": "2026 regular season (MLB Stats API)",
            "batter": _regular_batting(batter and batter["id"]),
            "pitcher": _regular_pitching(pitcher and pitcher["id"]),
            "runners": {base: _regular_batting(r["id"]) if r else None for base, r in runners.items()},
            "catcher": _regular_catching((defense.get("catcher") or {}).get("id")),
            "on_deck": _regular_batting((offense.get("onDeck") or {}).get("id")),
        },
        "zones": _zones(matchup),
        "at_bat": _at_bat(next((pl for pl in reversed(all_plays) if any(e.get("isPitch") for e in pl.get("playEvents", []) or [])), None)),
        "last_hit": next((h for h in (_hit(pl) for pl in reversed(all_plays[-6:])) if h), None),
        "recent_plays": recent, "win_probability": wp,
    }


ESPN = "https://site.api.espn.com/apis/site/v2/sports/football"


def _norm(v) -> str:
    return "".join(ch for ch in str(v or "").lower() if ch.isalnum())


def _team_matches(team: dict, abbr: str, name: str) -> bool:
    keys = {_norm(team.get(k)) for k in ("abbreviation", "location", "displayName", "shortDisplayName", "name", "nickname")}
    keys.discard("")
    for want in (_norm(abbr), _norm(name)):
        if not want:
            continue
        if want in keys:
            return True
        if len(want) > 4 and any(want in k or k in want for k in keys if len(k) > 4):
            return True
    return False


def _find_event(league: str, date: str, home: tuple, away: tuple):
    from datetime import datetime, timedelta
    base = datetime.strptime(date, "%Y%m%d")
    for off in (0, -1, 1):
        d = (base + timedelta(days=off)).strftime("%Y%m%d")
        extra = "&groups=80&limit=400" if league == "college-football" else "&limit=100"
        sb = _fetch_json(f"{ESPN}/{league}/scoreboard?dates={d}{extra}", 30)
        for ev in sb.get("events", []) or []:
            comp = (ev.get("competitions") or [{}])[0]
            cs = {c.get("homeAway"): c for c in comp.get("competitors", []) or []}
            if "home" in cs and "away" in cs and _team_matches(cs["home"].get("team") or {}, *home) and _team_matches(cs["away"].get("team") or {}, *away):
                return ev, comp
    return None, None


def _play(pl: dict) -> dict:
    st, en = pl.get("start") or {}, pl.get("end") or {}
    pick = lambda d: {"down": d.get("down"), "distance": d.get("distance"), "yardLine": d.get("yardLine"), "yardsToEndzone": d.get("yardsToEndzone"),
                      "team": (d.get("team") or {}).get("id"), "text": d.get("shortDownDistanceText") or d.get("downDistanceText"), "possessionText": d.get("possessionText")}
    return {"id": pl.get("id"), "seq": pl.get("sequenceNumber"), "type": (pl.get("type") or {}).get("text"), "text": pl.get("text"),
            "period": (pl.get("period") or {}).get("number"), "clock": (pl.get("clock") or {}).get("displayValue"),
            "start": pick(st), "end": pick(en), "yards": pl.get("statYardage"), "yac": pl.get("yardsAfterCatch"),
            "scoring": bool(pl.get("scoringPlay")), "turnover": bool(pl.get("isTurnover")), "penalty": bool(pl.get("isPenalty")),
            "awayScore": pl.get("awayScore"), "homeScore": pl.get("homeScore"), "wallclock": pl.get("wallclock"),
            "offense": next((tp.get("id") for tp in pl.get("teamParticipants", []) or [] if tp.get("type") == "offense"), None)}


def _depth(league: str, team_id: str):
    """Depth chart starters (NFL only) and the roster of one team, cached 30 minutes. ESPN public API."""
    out = {"off": {}, "def": {}, "offFormation": None, "defFormation": None}
    if league == "nfl":
        try:
            dc = _fetch_json(f"{ESPN}/nfl/teams/{team_id}/depthcharts", 1800).get("depthchart", []) or []
        except Exception:
            dc = []
        for ch in dc:
            pos = ch.get("positions") or {}
            tgt = "off" if "qb" in pos else "def" if ("lde" in pos or "mlb" in pos) else None
            if not tgt or out[tgt]:
                continue
            out[tgt + "Formation"] = ch.get("name")
            for k, v in pos.items():
                aths = (v or {}).get("athletes") or []
                if aths:
                    a = aths[0]
                    out[tgt][k] = {"id": a.get("id"), "name": a.get("displayName"), "jersey": a.get("jersey")}
    roster = []
    try:
        r = _fetch_json(f"{ESPN}/{league}/teams/{team_id}/roster", 1800)
        for grp in r.get("athletes", []) or []:
            for a in grp.get("items", []) or [grp] if isinstance(grp, dict) and "items" in grp else []:
                roster.append({"id": a.get("id"), "first": a.get("firstName") or "", "last": a.get("lastName") or "", "team": team_id, "jersey": a.get("jersey"),
                               "pos": (a.get("position") or {}).get("abbreviation")})
    except Exception:
        pass
    return out, roster


def football_live(league: str, date: str, home_abbr: str, home_name: str, away_abbr: str, away_name: str) -> dict:
    ev, comp = _find_event(league, date, (home_abbr, home_name), (away_abbr, away_name))
    if ev is None:
        return {"source": "ESPN", "found": False, "reason": f"No {league} game {away_abbr or away_name} @ {home_abbr or home_name} on ESPN's scoreboard near {date}."}
    eid = ev["id"]
    state = ((ev.get("status") or {}).get("type") or {}).get("state")
    summ = _fetch_json(f"{ESPN}/{league}/summary?event={eid}", 6 if state == "in" else 60)
    teams = {}
    for c in comp.get("competitors", []) or []:
        t = c.get("team") or {}
        teams[c.get("homeAway")] = {"id": t.get("id"), "abbr": t.get("abbreviation"), "name": t.get("displayName"), "short": t.get("shortDisplayName"),
                                    "color": t.get("color"), "alt": t.get("alternateColor"), "logo": t.get("logo"), "score": _int(c.get("score")),
                                    "record": next((r.get("summary") for r in c.get("records", []) or [] if r.get("type") == "total"), None)}
    stt = ev.get("status") or {}
    sit = comp.get("situation") or {}
    plays = []
    drives = summ.get("drives") or {}
    # ESPN lists the drive in progress under both "previous" and "current": keep one copy of each drive and play
    drive_list = []
    seen_dr = {}
    for dr in (drives.get("previous") or []) + ([drives["current"]] if drives.get("current") else []):
        key = dr.get("id") or id(dr)
        if key in seen_dr:
            drive_list[seen_dr[key]] = dr
        else:
            seen_dr[key] = len(drive_list)
            drive_list.append(dr)
    seen_pl = {}
    for dr in drive_list:
        for pl in dr.get("plays", []) or []:
            p = _play(pl)
            if p.get("id") in seen_pl:
                plays[seen_pl[p["id"]]] = p
            else:
                seen_pl[p.get("id")] = len(plays)
                plays.append(p)
    plays = plays[-400:]
    wp = [{"playId": w.get("playId"), "home": w.get("homeWinPercentage")} for w in summ.get("winprobability", []) or [] if w.get("homeWinPercentage") is not None]
    stats = {}
    for t in (summ.get("boxscore") or {}).get("teams", []) or []:
        tid = (t.get("team") or {}).get("id")
        stats[tid] = {x.get("name"): x.get("displayValue") for x in t.get("statistics", []) or []}
    leaders = []
    for tl in summ.get("leaders", []) or []:
        tid = (tl.get("team") or {}).get("id")
        for cat in tl.get("leaders", []) or []:
            for ld in (cat.get("leaders") or [])[:1]:
                a = ld.get("athlete") or {}
                leaders.append({"team": tid, "cat": cat.get("name"), "label": cat.get("displayName"), "name": a.get("displayName"), "id": a.get("id"), "line": ld.get("displayValue"),
                                "pos": (a.get("position") or {}).get("abbreviation"), "jersey": a.get("jersey")})
    players = {}
    for tp in (summ.get("boxscore") or {}).get("players", []) or []:
        tid = (tp.get("team") or {}).get("id")
        for cat in tp.get("statistics", []) or []:
            for rank, at in enumerate(cat.get("athletes", []) or []):
                a = at.get("athlete") or {}
                if a.get("id"):
                    if a["id"] not in players:
                        full = a.get("displayName") or ""
                        parts = full.split()
                        players[a["id"]] = {"id": a["id"], "team": tid, "first": parts[0] if parts else "", "last": " ".join(parts[1:]) if len(parts) > 1 else full, "jersey": a.get("jersey"), "ranks": {}}
                    players[a["id"]].setdefault("ranks", {}).setdefault(cat.get("name") or "", rank)
                    labels = cat.get("labels") or []
                    vals = at.get("stats") or []
                    if labels and len(labels) == len(vals):
                        players[a["id"]].setdefault("stats", {})[cat.get("name") or ""] = dict(zip(labels, vals))
    slug = "college-football" if league == "college-football" else "nfl"
    depth = {}
    for side in ("home", "away"):
        tid = (teams.get(side) or {}).get("id")
        if tid:
            d, rost = _depth(league, tid)
            depth[tid] = d
            for r in rost:
                if not r["id"]:
                    continue
                if r["id"] not in players:
                    players[r["id"]] = r
                else:
                    players[r["id"]]["pos"] = r.get("pos")
    for pl in players.values():
        pl["headshot"] = f"https://a.espncdn.com/i/headshots/{slug}/players/full/{pl['id']}.png"
    return {"source": "ESPN", "found": True, "league": league, "event_id": eid, "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": {"state": state, "detail": (stt.get("type") or {}).get("detail"), "short": (stt.get("type") or {}).get("shortDetail"), "period": stt.get("period"),
                       "clock": stt.get("displayClock")},
            "venue": ((comp.get("venue") or {}).get("fullName")), "teams": teams,
            "situation": {"down": sit.get("down"), "distance": sit.get("distance"), "yardLine": sit.get("yardLine"), "possession": sit.get("possession"),
                          "isRedZone": sit.get("isRedZone"), "text": sit.get("shortDownDistanceText") or sit.get("downDistanceText"),
                          "possessionText": sit.get("possessionText"), "homeTimeouts": sit.get("homeTimeouts"), "awayTimeouts": sit.get("awayTimeouts"),
                          "lastPlay": (sit.get("lastPlay") or {}).get("text")} if sit else None,
            "plays": plays, "win_probability": wp, "stats": stats, "leaders": leaders, "players": list(players.values()), "depth": depth,
            "date": next((c.get("date") for c in ((summ.get("header") or {}).get("competitions") or []) if c.get("date")), None),
            "week": (summ.get("header") or {}).get("week"), "season": ((summ.get("header") or {}).get("season") or {}).get("year"),
            "broadcast": next((((b.get("media") or {}).get("shortName")) for b in comp.get("broadcasts", []) or [] if (b.get("media") or {}).get("shortName")), None)
                         or next((n for b in comp.get("broadcasts", []) or [] for n in (b.get("names") or [])), None),
            "venue_city": ", ".join(x for x in (((summ.get("gameInfo") or {}).get("venue") or {}).get("address") or {}).values() if isinstance(x, str)) or None,
            "weather": {"text": (ev.get("weather") or {}).get("displayValue"), "temp": (ev.get("weather") or {}).get("temperature")} if ev.get("weather") else None,
            "attendance": (summ.get("gameInfo") or {}).get("attendance"),
            "drives": [{"id": d.get("id"), "team": (d.get("team") or {}).get("abbreviation"), "team_id": (d.get("team") or {}).get("id"), "desc": d.get("description"),
                        "start": (d.get("start") or {}).get("text"), "start_yl": (d.get("start") or {}).get("yardLine"), "end_yl": (d.get("end") or {}).get("yardLine"),
                        "period": ((d.get("start") or {}).get("period") or {}).get("number"), "clock": ((d.get("start") or {}).get("clock") or {}).get("displayValue"),
                        "yards": d.get("yards"), "plays": d.get("offensivePlays"), "result": d.get("displayResult") or d.get("result"), "score": bool(d.get("isScore")),
                        "elapsed": (d.get("timeElapsed") or {}).get("displayValue"),
                        "play_ids": [pl.get("id") for pl in d.get("plays", []) or []]}
                       for d in drive_list][-40:],
            "scoring": [{"id": sp.get("id"), "team": (sp.get("team") or {}).get("abbreviation"), "team_id": (sp.get("team") or {}).get("id"), "type": (sp.get("type") or {}).get("abbreviation"),
                         "text": sp.get("text"), "period": (sp.get("period") or {}).get("number"), "clock": (sp.get("clock") or {}).get("displayValue"),
                         "away": sp.get("awayScore"), "home": sp.get("homeScore")} for sp in summ.get("scoringPlays", []) or []]}


BOOTH_SYSTEM = """You are "The Booth": the friendly, sharp co-host sitting next to the fan during the game on AISP Live View.
Voice: TWO short sentences, 35 words at most, answer first. Talk like a fan at a sports bar: plain, direct, conversational, a little personality. Explain any football or baseball term in the same breath if a casual fan might not know it.
Examples of the voice:
Q: Why did the win probability just move? A: Houston grabbed 19 yards on third down, so they keep the ball and creep toward field-goal range. That one snap swung their chances by about 16 points.
Q: What am I looking at in this formation? A: Stroud is in the shotgun, standing a few yards behind the center with receivers spread wide. It's a quick-throw setup.
Rules:
- The page may be replaying an earlier play. Talk about the moment on screen as if you are watching it live. NEVER say the game is over, final or already decided unless the fan asks about the final result.
- Never say "the context", "the feed", "the data", "provided", "it appears" or "I can't see". Never ask a question back. If a number is missing, say "we don't have that yet" and give your best plain-English read.
- No lists, no markdown, no emojis.
- Only say what a team "usually" does if football_feed.how_this_team_plays_this_game or next_play_model_top_calls says so; otherwise give the general football read and say it is general.
- Never predict who wins, pick a side or tell anyone to bet. If asked, say the models and the market make that call and say in plain words what the numbers on screen show.
- Never invent numbers. Scores, odds, probabilities and stats come only from the information you were given. General knowledge of the sport is welcome.
This is research and paper only."""


def booth(payload: dict) -> dict:
    question = str(payload.get("question") or "").strip()[:600]
    context = payload.get("context")
    if not question:
        return {"error": "question required"}
    now = time.time()
    while BOOTH_RATE and now - BOOTH_RATE[0] > 300:
        BOOTH_RATE.popleft()
    if len(BOOTH_RATE) >= 30:
        return {"error": "The booth is catching its breath (30 questions per 5 minutes in test mode). Try again shortly."}
    key = _anthropic_key()
    if not key:
        return {"error": "No ANTHROPIC_API_KEY is available to the preview server."}
    ctx_text = json.dumps(context, ensure_ascii=False)[:14000] if context is not None else "(no context sent)"
    user_text = "CONTEXT (JSON, as of the page's last refresh):" + chr(10) + ctx_text + chr(10) + chr(10) + "QUESTION: " + question
    def _call(body: dict):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json", "User-Agent": "aisp-preview/1"},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))

    base = {"model": BOOTH_MODEL, "max_tokens": BOOTH_MAX_TOKENS, "system": BOOTH_SYSTEM,
            "messages": [{"role": "user", "content": user_text}]}
    t0 = time.time()
    try:
        try:
            # The booth answers fast and short: no extended thinking (it would eat the token budget).
            data = _call({**base, "thinking": {"type": "disabled"}})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 400 and "thinking" in detail:
                data = _call({**base, "max_tokens": BOOTH_MAX_TOKENS + 400})
            else:
                log(f"BOOTH_HTTP_{e.code} {detail}")
                return {"error": f"The booth could not reach the model (HTTP {e.code})."}
    except urllib.error.HTTPError as e:
        log(f"BOOTH_HTTP_{e.code} " + e.read().decode("utf-8", "replace")[:300])
        return {"error": f"The booth could not reach the model (HTTP {e.code})."}
    except Exception as e:  # network
        return {"error": f"The booth could not reach the model: {type(e).__name__}."}
    BOOTH_RATE.append(now)
    usage = data.get("usage") or {}
    answer = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    parts = re.split(r"(?<=[.!?])\s+", answer.strip())
    answer = " ".join(parts[:2]).replace(chr(10), " ").strip()  # the Booth speaks in two sentences, always
    out = {"answer": answer, "model": data.get("model") or BOOTH_MODEL, "latency_ms": int((time.time() - t0) * 1000),
           "usage": {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")},
           "disclaimer": "Explains only. Never a prediction or a bet recommendation. Research and paper only."}
    try:
        with open(BOOTH_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "q": question, "in": usage.get("input_tokens"), "out": usage.get("output_tokens"), "ms": out["latency_ms"]}) + chr(10))
    except OSError:
        pass
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "AISPPreview/1"

    def log_message(self, fmt, *args):  # quiet console; file log instead
        pass

    def _refuse(self, code=405, msg="preview is read-only"):
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _preview_get(self, path: str, query: str):
        from urllib.parse import parse_qs
        q = {k: v[0] for k, v in parse_qs(query).items()}
        if path == "/preview-api/mlb/live":
            date, home, away = q.get("date", ""), q.get("home", ""), q.get("away", "")
            if not (date and home and away):
                return self._json({"error": "date, home and away are required"}, 400)
            try:
                data = mlb_live(date, home, away)
            except Exception as e:
                log(f"MLB_LIVE_ERROR {e}")
                return self._json({"source": "MLB_STATS_API", "found": False, "reason": f"MLB Stats API unavailable: {type(e).__name__}"}, 502)
            log(f"GET {self.path} -> 200 (preview feed)")
            return self._json(data)
        if path == "/preview-api/football/live":
            league = q.get("league", "nfl")
            if league not in ("nfl", "college-football") or not q.get("date"):
                return self._json({"error": "league (nfl|college-football) and date (YYYYMMDD) are required"}, 400)
            try:
                data = football_live(league, q["date"], q.get("home", ""), q.get("home_name", ""), q.get("away", ""), q.get("away_name", ""))
                if q.get("lite"):
                    # the home page's live rows only need the score bug and a thinned win-probability line
                    wp = data.get("win_probability") or []
                    step = max(1, len(wp) // 80)
                    thin = wp[::step]
                    if wp and thin[-1] is not wp[-1]:
                        thin.append(wp[-1])
                    data = {**{k: data.get(k) for k in ("source", "found", "reason", "league", "event_id", "status", "teams", "situation", "retrieved_at")}, "win_probability": thin}
            except Exception as e:
                log(f"FOOTBALL_LIVE_ERROR {type(e).__name__} {e}")
                return self._json({"source": "ESPN", "found": False, "reason": f"ESPN unavailable: {type(e).__name__}"}, 502)
            log(f"GET {self.path[:140]} -> 200 (preview feed)")
            return self._json(data)
        if path == "/preview-api/logos/cfb":
            # College logos + colours from ESPN's public team list (FBS and FCS), cached a day.
            try:
                out = []
                for grp in ("80", "81"):
                    data = _fetch_json(f"{ESPN}/college-football/teams?limit=1000&groups={grp}", 86400)
                    for t in (((data.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []):
                        tm = t.get("team") or {}
                        logo = next((l.get("href") for l in tm.get("logos") or [] if "dark" not in (l.get("rel") or [])), None) or next((l.get("href") for l in tm.get("logos") or []), None)
                        out.append({"id": tm.get("id"), "abbr": tm.get("abbreviation"), "location": tm.get("location"), "short": tm.get("shortDisplayName"), "display": tm.get("displayName"), "nickname": tm.get("nickname") or tm.get("name"), "color": tm.get("color"), "alt": tm.get("alternateColor"), "logo": logo, "fbs": grp == "80"})
                return self._json({"source": "ESPN", "teams": out})
            except Exception as e:
                log(f"CFB_LOGOS_ERROR {type(e).__name__} {e}")
                return self._json({"source": "ESPN", "teams": [], "reason": type(e).__name__}, 502)
        if path == "/preview-api/scores":
            league = q.get("league", "")
            if league not in ("nfl", "cfb", "nba", "nhl", "mlb") or not q.get("date"):
                return self._json({"error": "league (nfl|cfb|nba|nhl|mlb) and date (YYYYMMDD) are required"}, 400)
            try:
                if str(HERE) not in sys.path:
                    sys.path.insert(0, str(HERE))
                import court_live
                return self._json(court_live.scores(league, q["date"]))
            except Exception as e:
                log(f"SCORES_ERROR {type(e).__name__} {e}")
                return self._json({"league": league, "games": [], "reason": type(e).__name__}, 502)
        if path.startswith("/preview-api/insights/"):
            # Beat the Close, Market Movers, Model Lab, closing lines: read-only queries on AISP's own tables.
            try:
                if str(HERE) not in sys.path:
                    sys.path.insert(0, str(HERE))
                import insights_db
                kind = path.rsplit("/", 1)[-1]
                days = int(q.get("days") or 0) or None
                if kind == "closing":
                    return self._json(insights_db.beat_the_close(days or 75))
                if kind == "movers":
                    return self._json(insights_db.movers())
                if kind == "lab":
                    return self._json(insights_db.lab(days or 75))
                if kind == "closes":
                    return self._json(insights_db.closes(days or 21))
                return self._json({"error": "unknown insights endpoint"}, 404)
            except Exception as e:
                log(f"INSIGHTS_ERROR {type(e).__name__} {e}")
                return self._json({"error": f"insights unavailable: {type(e).__name__}"}, 502)
        if path == "/preview-api/arena/live":
            # NBA / NHL live play-by-play with coordinates (ESPN public API, cached briefly).
            league = q.get("league", "")
            if league not in ("nba", "nhl") or not q.get("date") or not q.get("home") or not q.get("away"):
                return self._json({"error": "league (nba|nhl), date (YYYYMMDD), home and away are required"}, 400)
            try:
                if str(HERE) not in sys.path:
                    sys.path.insert(0, str(HERE))
                import court_live
                data = court_live.court_live(league, q["date"], q["home"], q["away"])
            except Exception as e:
                log(f"ARENA_LIVE_ERROR {type(e).__name__} {e}")
                return self._json({"source": "ESPN", "found": False, "reason": f"ESPN unavailable: {type(e).__name__}"}, 502)
            log(f"GET {self.path[:140]} -> 200 (preview feed)")
            return self._json(data)
        if path == "/preview-api/ufc/archive":
            # Historical UFC archive (Codex handoff + overlay + raw source, orientation-corrected). Local files only.
            try:
                if str(HERE) not in sys.path:
                    sys.path.insert(0, str(HERE))
                import ufc_archive
                return self._json(ufc_archive.build())
            except Exception as e:
                log(f"UFC_ARCHIVE_ERROR {type(e).__name__} {e}")
                return self._json({"error": f"UFC archive unavailable: {type(e).__name__}"}, 502)
        if path == "/preview-api/health":
            return self._json({"ok": True, "dist": dist_dir(), "booth_key": bool(_anthropic_key()), "booth_model": BOOTH_MODEL})
        return self._json({"error": "unknown preview endpoint"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/preview-api/booth":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except Exception:
                return self._json({"error": "bad JSON"}, 400)
            out = booth(payload if isinstance(payload, dict) else {})
            log(f"POST /preview-api/booth -> {'ok' if 'answer' in out else out.get('error')}")
            status = 200 if "answer" in out else (429 if "breath" in str(out.get("error")) else 502)
            return self._json(out, status)
        log(f"REFUSED POST {self.path}"); self._refuse()
    def do_PUT(self): log(f"REFUSED PUT {self.path}"); self._refuse()
    def do_PATCH(self): log(f"REFUSED PATCH {self.path}"); self._refuse()
    def do_DELETE(self): log(f"REFUSED DELETE {self.path}"); self._refuse()

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path.startswith("/api/"):
            return self._proxy()
        if path.startswith("/preview-api/"):
            return self._preview_get(path, query)
        root = dist_dir()
        rel = path.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(root, rel))
        if not full.startswith(os.path.normpath(root)) or not os.path.isfile(full):
            full = os.path.join(root, "index.html")
        try:
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            return self._refuse(404, "preview build not found: " + root)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype if not ctype.startswith("text/") else ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if full.endswith("index.html") else "public, max-age=3600")
        self.send_header("X-AISP-Preview-Dist", root)
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self):
        url = UPSTREAM + self.path
        headers = {k: v for k, v in self.headers.items() if k.lower() in FORWARD_REQ}
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
                status = resp.status
                rh = resp.headers
        except urllib.error.HTTPError as e:
            body = e.read(); status = e.code; rh = e.headers
        except Exception as e:  # upstream down
            log(f"UPSTREAM_ERROR GET {self.path} {e}")
            return self._refuse(502, "backend unreachable")
        log(f"GET {self.path} -> {status}")
        self.send_response(status)
        for k, v in rh.items():
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    log(f"START port={PORT} dist={dist_dir()}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
