"""Live NBA and NHL feed for the test-mode Live View (ESPN public scoreboard + summary, cached briefly, read-only).

Returns the score bug, every play with its court/rink coordinate, ESPN's NBA win probability, the players on the ice
(NHL) and the box-score leaders. Never stored and never used for settlement.
"""
import json
import time
import urllib.request

BASE = "https://site.api.espn.com/apis/site/v2/sports"
PATH = {"nba": "basketball/nba", "nhl": "hockey/nhl"}
# AISP abbreviations -> ESPN's
ALIAS = {"nba": {"CHO": "CHA", "PHO": "PHX", "GSW": "GS", "NOP": "NO", "BRK": "BKN", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"},
         "nhl": {"TBL": "TB", "UTA": "UTAH", "LAK": "LA", "NJD": "NJ", "SJS": "SJ", "VEG": "VGK"}}
_cache: dict = {}


def _get(url: str, ttl: float):
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": "Python-urllib/3.12", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=12) as r:
        data = json.loads(r.read().decode("utf-8"))
    _cache[url] = (time.time(), data)
    return data


def _n(v) -> str:
    return "".join(ch for ch in str(v or "").upper() if ch.isalnum())


def _match(team: dict, abbr: str) -> bool:
    keys = {_n(team.get(k)) for k in ("abbreviation", "location", "displayName", "shortDisplayName", "name")}
    return _n(abbr) in keys


def _coord(p):
    c = p.get("coordinate") or {}
    x, y = c.get("x"), c.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or abs(x) > 1000 or abs(y) > 1000:
        return None
    return {"x": x, "y": y}


def court_live(league: str, date: str, home: str, away: str) -> dict:
    from datetime import datetime, timedelta
    lg = PATH[league]
    h, a = ALIAS[league].get(home.upper(), home), ALIAS[league].get(away.upper(), away)
    ev = comp = None
    base = datetime.strptime(date, "%Y%m%d")
    for off in (0, -1, 1):
        d = (base + timedelta(days=off)).strftime("%Y%m%d")
        sb = _get(f"{BASE}/{lg}/scoreboard?dates={d}&limit=100", 30)
        for e in sb.get("events", []) or []:
            c = (e.get("competitions") or [{}])[0]
            cs = {x.get("homeAway"): x for x in c.get("competitors", []) or []}
            if "home" in cs and "away" in cs and _match(cs["home"].get("team") or {}, h) and _match(cs["away"].get("team") or {}, a):
                ev, comp = e, c
                break
        if ev:
            break
    if ev is None:
        return {"source": "ESPN", "found": False, "reason": f"No {league.upper()} game {away} @ {home} on ESPN near {date}."}
    st = (comp.get("status") or {})
    stype = st.get("type") or {}
    state = stype.get("state")
    summ = _get(f"{BASE}/{lg}/summary?event={ev['id']}", 8 if state == "in" else 120)
    teams = {}
    for c in comp.get("competitors", []) or []:
        t = c.get("team") or {}
        teams[c.get("homeAway")] = {"id": t.get("id"), "abbr": t.get("abbreviation"), "name": t.get("displayName"), "short": t.get("shortDisplayName"),
                                    "color": t.get("color"), "alt": t.get("alternateColor"), "logo": t.get("logo"), "score": int(c.get("score") or 0),
                                    "record": ((c.get("records") or [{}])[0]).get("summary"),
                                    "linescores": [ls.get("value") for ls in c.get("linescores", []) or []]}
    athletes = {}
    for side in (summ.get("boxscore") or {}).get("players", []) or []:
        tid = (side.get("team") or {}).get("id")
        for grp in side.get("statistics", []) or []:
            keys = grp.get("keys") or grp.get("names") or []
            for row in grp.get("athletes", []) or []:
                at = row.get("athlete") or {}
                stats = dict(zip(keys, row.get("stats") or []))
                athletes[at.get("id")] = {"id": at.get("id"), "name": at.get("displayName"), "short": at.get("shortName"), "jersey": at.get("jersey"),
                                          "pos": (at.get("position") or {}).get("abbreviation"), "team": tid, "headshot": (at.get("headshot") or {}).get("href"),
                                          "starter": row.get("starter"), "stats": stats, "group": grp.get("name")}
    plays = []
    for p in summ.get("plays", []) or []:
        parts = []
        for x in p.get("participants", []) or []:
            at = x.get("athlete") or {}
            aid = at.get("id")
            info = athletes.get(aid, {})
            parts.append({"id": aid, "name": at.get("displayName") or info.get("name"), "short": at.get("shortName") or info.get("short"),
                          "headshot": (at.get("headshot") or {}).get("href") or info.get("headshot"), "role": x.get("type")})
        plays.append({"id": p.get("id"), "seq": p.get("sequenceNumber"), "type": (p.get("type") or {}).get("text"), "abbr": (p.get("type") or {}).get("abbreviation"),
                      "text": p.get("text"), "short": p.get("shortDescription"), "period": (p.get("period") or {}).get("number"),
                      "clock": (p.get("clock") or {}).get("displayValue"), "team": (p.get("team") or {}).get("id"),
                      "scoring": bool(p.get("scoringPlay")), "value": p.get("scoreValue"), "shooting": bool(p.get("shootingPlay")),
                      "attempted": p.get("pointsAttempted"), "coord": _coord(p), "away": p.get("awayScore"), "home": p.get("homeScore"),
                      "wallclock": p.get("wallclock"), "players": parts, "strength": (p.get("strength") or {}).get("text") if isinstance(p.get("strength"), dict) else None})
    wp = [{"play": w.get("playId"), "home": w.get("homeWinPercentage")} for w in summ.get("winprobability", []) or []]
    on_ice = []
    for side in summ.get("onIce", []) or []:
        tid = side.get("teamId") if isinstance(side, dict) else None
        for en in (side.get("entries") or []) if isinstance(side, dict) else []:
            info = athletes.get(str(en.get("athleteid")), {})
            on_ice.append({"team": tid, "id": en.get("athleteid"), "name": info.get("name"), "short": info.get("short"), "pos": info.get("pos"),
                           "jersey": info.get("jersey"), "headshot": info.get("headshot")})
    leaders = []
    for side in summ.get("leaders", []) or []:
        tid = (side.get("team") or {}).get("id")
        for cat in side.get("leaders", []) or []:
            top = (cat.get("leaders") or [{}])[0]
            at = top.get("athlete") or {}
            leaders.append({"team": tid, "category": cat.get("displayName") or cat.get("name"), "value": top.get("displayValue"),
                            "id": at.get("id"), "name": at.get("displayName"), "headshot": (at.get("headshot") or {}).get("href")})
    venue = ((summ.get("gameInfo") or {}).get("venue") or {})
    return {"source": "ESPN", "found": True, "league": league, "event_id": ev["id"],
            "status": {"state": state, "detail": stype.get("detail"), "short": stype.get("shortDetail"), "period": st.get("period"), "clock": st.get("displayClock")},
            "teams": teams, "plays": plays, "win_probability": wp, "on_ice": on_ice, "leaders": leaders,
            "athletes": list(athletes.values()), "venue": venue.get("fullName"), "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


if __name__ == "__main__":
    import sys
    out = court_live(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
    print(out.get("found"), out.get("status"), {k: (v["abbr"], v["score"]) for k, v in (out.get("teams") or {}).items()})
    print("plays", len(out.get("plays") or []), "coords", sum(1 for p in out.get("plays") or [] if p["coord"]), "wp", len(out.get("win_probability") or []), "onIce", len(out.get("on_ice") or []), "leaders", len(out.get("leaders") or []))
    print([p["type"] for p in (out.get("plays") or [])[-8:]])
    print((out.get("on_ice") or [])[:3])


SB_PATH = {"nfl": "football/nfl", "cfb": "football/college-football", "nba": "basketball/nba", "nhl": "hockey/nhl", "mlb": "baseball/mlb"}


def scores(league: str, date: str) -> dict:
    """Compact scoreboard for one league and day (ESPN, cached 25 s): state, period, clock and scores per game."""
    extra = "&groups=80&limit=400" if league == "cfb" else "&limit=100"
    sb = _get(f"{BASE}/{SB_PATH[league]}/scoreboard?dates={date}{extra}", 25)
    out = []
    for e in sb.get("events", []) or []:
        c = (e.get("competitions") or [{}])[0]
        st = (c.get("status") or {})
        teams = {}
        for x in c.get("competitors", []) or []:
            t = x.get("team") or {}
            teams[x.get("homeAway")] = {"abbr": t.get("abbreviation"), "name": t.get("displayName"), "short": t.get("shortDisplayName"), "location": t.get("location"), "score": int(x.get("score") or 0)}
        if "home" in teams and "away" in teams:
            out.append({"id": e.get("id"), "state": (st.get("type") or {}).get("state"), "detail": (st.get("type") or {}).get("shortDetail"), "period": st.get("period"),
                        "clock": st.get("displayClock"), "home": teams["home"], "away": teams["away"]})
    return {"league": league, "date": date, "games": out}
