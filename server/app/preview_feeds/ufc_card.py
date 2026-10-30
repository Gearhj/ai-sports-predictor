"""This weekend's UFC card for the MMA page's Fight Night view (owner 10-30: "the tale of the tape, the octagon, the
fighter's stats, info on fighting style, info on odds for each fighter, a golden card if this is a title fight").

Read-only, public ESPN feeds only, stdlib only (the same file is served by the preview server on 5199 and by the
backend's /preview-api, like court_live.py):
  * site.api.espn.com .../mma/ufc/scoreboard          the card, weight classes, rounds, live round/clock, winners
  * sports.core.api.espn.com .../athletes/{id}         height, reach, weight, age, stance, styles, gym, flag, headshot
  * sports.core.api.espn.com .../athletes/{id}/statistics   career striking and grappling rates
  * sports.core.api.espn.com .../competitions/{id}/odds     DraftKings moneyline, opening and current
  * www.ufc.com/rankings (official, HTML)              champion and rank badges -> title fight
Odds are shown for information only: AISP has no MMA model or wager.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
CORE = "https://sports.core.api.espn.com/v2/sports/mma"
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _get(url: str, ttl: float):
    now = time.time()
    with _lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < ttl:
            return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    with _lock:
        _cache[url] = (now, data)
    return data


def _safe(url: str, ttl: float):
    try:
        return _get(url, ttl)
    except Exception:  # one missing profile or price never sinks the card
        return None


def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _american(v):
    if v in (None, "", "EVEN", "even"):
        return 100 if v in ("EVEN", "even") else None
    try:
        return int(str(v).replace("+", ""))
    except ValueError:
        return None


def _ref_id(ref: str | None) -> str | None:
    m = re.search(r"/athletes/(\d+)", ref or "")
    return m.group(1) if m else None


def _fighter(aid: str) -> dict:
    p = _safe(f"{CORE}/athletes/{aid}?lang=en&region=us", 6 * 3600) or {}
    s = _safe(f"{CORE}/athletes/{aid}/statistics?lang=en&region=us", 6 * 3600) or {}
    stats = {}
    for cat in ((s.get("splits") or {}).get("categories") or []):
        for x in cat.get("stats") or []:
            stats[x.get("name")] = _num(x.get("value"))
    styles = [x.get("text") for x in p.get("styles") or [] if x.get("text")]
    return {
        "id": aid, "name": p.get("displayName"), "nickname": p.get("nickname"),
        "headshot": (p.get("headshot") or {}).get("href"), "flag": (p.get("flag") or {}).get("href"),
        "country": p.get("citizenship"), "age": p.get("age"), "height": p.get("displayHeight"), "height_in": p.get("height"),
        "reach": p.get("displayReach"), "reach_in": p.get("reach"), "weight": p.get("displayWeight"),
        "stance": (p.get("stance") or {}).get("text"), "styles": styles,
        "gym": (p.get("association") or {}).get("name"),
        "stats": {"slpm": stats.get("strikeLPM"), "str_acc": stats.get("strikeAccuracy"), "td_avg": stats.get("takedownAvg"),
                  "td_acc": stats.get("takedownAccuracy"), "sub_avg": stats.get("submissionAvg")},
    }


def archetype(f: dict) -> str | None:
    """A plain-language style read from the career rates (ESPN's own style tag is shown beside it)."""
    st = f.get("stats") or {}
    slpm, td, sub = st.get("slpm"), st.get("td_avg"), st.get("sub_avg")
    if slpm is None and td is None:
        return None
    if (td or 0) >= 2.5 and (sub or 0) >= 1.0:
        return "Wrestle-grappler: takes it down and hunts the finish"
    if (td or 0) >= 2.5:
        return "Wrestler: wants it on the mat"
    if (sub or 0) >= 1.2:
        return "Submission threat: dangerous off the back and in scrambles"
    if (slpm or 0) >= 5.0:
        return "Volume striker: high pace on the feet"
    if (slpm or 0) >= 3.5 and (td or 0) < 1.0:
        return "Striker: keeps it standing"
    return "All-rounder: mixes striking and grappling"


def _odds(event_id: str, comp_id: str, live: bool) -> dict:
    o = _safe(f"{CORE}/leagues/ufc/events/{event_id}/competitions/{comp_id}/odds?lang=en&region=us", 120 if live else 600) or {}
    item = (o.get("items") or [None])[0] or {}
    out = {"provider": (item.get("provider") or {}).get("name")}
    for key in ("homeAthleteOdds", "awayAthleteOdds"):
        side = item.get(key) or {}
        aid = _ref_id((side.get("athlete") or {}).get("$ref"))
        if not aid:
            continue
        cur = _american(((side.get("current") or {}).get("moneyLine") or {}).get("american")) or _american(side.get("moneyLine"))
        opn = _american(((side.get("open") or {}).get("moneyLine") or {}).get("american"))
        out[aid] = {"american": cur, "open": opn}
    return out


def _no_vig(a, b):
    if a is None or b is None:
        return None
    imp = lambda x: 100 / (x + 100) if x > 0 else -x / (-x + 100)  # noqa: E731
    pa, pb = imp(a), imp(b)
    return round(pa / (pa + pb), 4)


RANKINGS = "https://www.ufc.com/rankings"


def _norm(name: str | None) -> str:
    text = unicodedata.normalize("NFKD", html.unescape(str(name or "")))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.replace("'", "")).split())


def _ranks() -> dict:
    """UFC.com's official rankings: normalised division -> {"champion": name, "ranked": {name: rank}}.
    (ESPN's ranking feed is years stale, e.g. it still lists Sterling as bantamweight champion, so it is not used.)"""
    now = time.time()
    with _lock:
        hit = _cache.get(RANKINGS)
        if hit and now - hit[0] < 6 * 3600:
            return hit[1]
    try:
        req = urllib.request.Request(RANKINGS, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            page = resp.read().decode("utf-8", "replace")
    except Exception:
        return {}
    out = {}
    for group in re.findall(r'<div class="view-grouping">(.*?)</table>', page, re.S):
        div = re.search(r'view-grouping-header">\s*(.*?)\s*<', group, re.S)
        if not div or "pound" in div.group(1).lower():
            continue
        champ = re.search(r"<h5>\s*<a[^>]*>(.*?)</a>", group, re.S)
        names = re.findall(r"views-field-title[^>]*>\s*<a[^>]*>(.*?)</a>", group, re.S)
        out[_norm(div.group(1))] = {"champion": _norm(champ.group(1)) if champ else None,
                                    "ranked": {_norm(n): i + 1 for i, n in enumerate(names)}}
    with _lock:
        _cache[RANKINGS] = (now, out)
    return out


def _division(weight_class: str) -> str:
    w = weight_class or ""
    return _norm(("Women's " + w[2:]) if w.startswith("W ") else w)


def _method(comp: dict) -> str | None:
    for d in comp.get("details") or []:
        text = ((d.get("type") or {}).get("text") or "")
        if text.lower().startswith("unofficial winner"):
            m = text[len("Unofficial Winner"):].strip()
            return {"Kotko": "KO/TKO", "Tko": "KO/TKO", "Ko": "KO/TKO"}.get(m.title(), m) or None
    return None


def card(date: str = "") -> dict:
    """The current (or next) UFC card, every bout with both fighters' tape, career stats, style read and odds."""
    if date == "last":
        # The most recent finished card in the last three weeks (ESPN accepts a date range).
        today = time.gmtime()
        start = time.strftime("%Y%m%d", time.gmtime(time.time() - 21 * 86400))
        sb = _get(SCOREBOARD + f"?dates={start}-{time.strftime('%Y%m%d', today)}", 600)
        done = [e for e in sb.get("events") or [] if all(((c.get("status") or {}).get("type") or {}).get("completed") for c in e.get("competitions") or [])]
        sb = {"events": sorted(done, key=lambda e: e.get("date") or "")[-1:]}
    else:
        sb = _get(SCOREBOARD + (f"?dates={re.sub(r'[^0-9]', '', date)}" if date else ""), 60)
    events = sb.get("events") or []
    if not events:
        return {"found": False, "reason": "ESPN lists no UFC card right now."}
    ev = events[0]
    comps = ev.get("competitions") or []
    live_any = any(((c.get("status") or {}).get("type") or {}).get("state") == "in" for c in comps)
    ids = sorted({str(x.get("id")) for c in comps for x in c.get("competitors") or [] if x.get("id")})
    with ThreadPoolExecutor(max_workers=12) as pool:
        fighters = dict(zip(ids, pool.map(_fighter, ids)))
        odds = list(pool.map(lambda c: _odds(str(ev["id"]), str(c["id"]), live_any), comps))
    table = _ranks()
    for f in fighters.values():
        f["style_read"] = archetype(f)
        f["champion"], f["rank"] = False, None
    bouts = []
    # ESPN lists the card from the first prelim to the main event; the page shows the main event first.
    for idx, (c, o) in enumerate(reversed(list(zip(comps, odds)))):
        st = c.get("status") or {}
        stype = st.get("type") or {}
        corner = sorted(c.get("competitors") or [], key=lambda x: x.get("order") or 9)
        sides = []
        for x in corner:
            aid = str(x.get("id"))
            rec = ((x.get("records") or [{}])[0]).get("summary")
            sides.append({"id": aid, "record": rec, "winner": bool(x.get("winner")), "odds": o.get(aid) or {}})
        if len(sides) != 2:
            continue
        a_ml, b_ml = sides[0]["odds"].get("american"), sides[1]["odds"].get("american")
        p_a = _no_vig(a_ml, b_ml)
        rounds = ((c.get("format") or {}).get("regulation") or {}).get("periods") or 3
        wc = (c.get("type") or {}).get("abbreviation") or ""
        div = table.get(_division(wc)) or {}
        for s in sides:
            f = fighters.get(s["id"]) or {}
            n = _norm(f.get("name"))
            f["champion"] = f.get("champion") or (bool(n) and n == div.get("champion"))
            f["rank"] = f.get("rank") or (div.get("ranked") or {}).get(n)
        champ_in = any((fighters.get(s["id"]) or {}).get("champion") for s in sides)
        # A champion in a five-round bout is a title fight; so is any five-rounder on a numbered card (vacant and
        # interim belts have no champion to find).
        title = rounds == 5 and (champ_in or bool(re.match(r"^UFC \d+", ev.get("name") or "")))
        bouts.append({
            "id": str(c.get("id")), "slot": "MAIN EVENT" if idx == 0 else "CO-MAIN" if idx == 1 else None,
            "weight_class": wc, "rounds": rounds, "title_fight": title, "starts_at": c.get("date"),
            "state": stype.get("state"), "status": stype.get("description") or stype.get("detail"),
            "period": st.get("period"), "clock": st.get("displayClock"),
            "method": _method(c) if stype.get("state") == "post" else None,
            "card_segment": None, "a": sides[0], "b": sides[1],
            "market_p_a": p_a, "odds_provider": o.get("provider"),
        })
    # Main card = the bouts sharing the main event's start time (ESPN times prelims earlier).
    main_start = bouts[0]["starts_at"] if bouts else None
    for b in bouts:
        b["card_segment"] = "Main card" if b["starts_at"] == main_start else "Prelims"
    venue = ((comps[0].get("venue") if comps else None) or {}) if comps else {}
    return {
        "found": True, "source": "ESPN (public)", "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": {"id": str(ev.get("id")), "name": ev.get("name"), "date": ev.get("date"),
                  "venue": venue.get("fullName"), "city": ((venue.get("address") or {}).get("city")),
                  "live": live_any, "complete": all(((c.get("status") or {}).get("type") or {}).get("completed") for c in comps)},
        "bouts": bouts, "fighters": fighters,
        "notes": ["Card, results, fighter profiles, career stats and rankings: ESPN's public feeds.",
                  "Odds: the sportsbook ESPN lists (usually DraftKings), opening and current moneyline. Information only: AISP has no UFC model or wager.",
                  "Style read is AISP's plain-language summary of each fighter's career striking, takedown and submission rates."],
    }
