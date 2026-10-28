"""THE CROWD (owner 10-27: "a small box ... what the vibe is in the public ... hot off the presses ... how the crowd
reacts to big plays or controversial calls").

Free, public, keyless sources only ($0):
  * Bluesky public search  api.bsky.app/xrpc/app.bsky.feed.searchPosts  (latest posts mentioning the teams)
  * Mastodon tag timelines mastodon.social/api/v1/timelines/tag/<tag>   (#NBA, #NHL, #NFL, #CFB, #MLB, team tags)
X/Twitter's API is paid and scraping it is against its terms; Reddit refuses unauthenticated requests. So: these two.

Sentiment is a small sports lexicon scored locally (no paid model): each post gets a score in [-1, 1]; the crowd's
vibe is the average of the last 6 hours with a label (HYPED / ANGRY / NERVOUS / SALTY / QUIET / MIXED), a volume
(posts per hour), a split between the two teams, the words trending in the posts, and a few short quotes with the
author's handle and age (profanity masked, links removed, at most 140 characters). Cached 120 s per query.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "AISP-crowd/0.1 (personal research)", "Accept": "application/json"}
BSKY = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
MASTO = "https://mastodon.social/api/v1/timelines/tag/"
WINDOW_H = 6
_CACHE: dict[str, tuple[float, dict]] = {}

POS = {"win": 1, "won": 1, "wins": 1, "clutch": 2, "goat": 2, "fire": 1, "lfg": 2, "huge": 1, "great": 1, "amazing": 2, "love": 1, "beautiful": 1,
       "dominant": 2, "dominating": 2, "electric": 2, "insane": 1, "unreal": 1, "lets go": 2, "let's go": 2, "hyped": 2, "best": 1, "elite": 2, "comeback": 2,
       "money": 1, "cooking": 1, "beast": 1, "statement": 1, "excited": 1, "nice": 1, "good": 1, "happy": 1, "proud": 1, "mvp": 1, "buzzer": 1}
NEG = {"lose": -1, "lost": -1, "losing": -1, "choke": -2, "choked": -2, "terrible": -2, "awful": -2, "brutal": -2, "trash": -2, "garbage": -2, "embarrassing": -2,
       "pathetic": -2, "worst": -2, "cooked": -1, "washed": -2, "rigged": -2, "robbed": -2, "refs": -1, "ref": -1, "officiating": -1, "blown": -1, "bad": -1,
       "injury": -1, "injured": -1, "hurt": -1, "out": 0, "ugh": -1, "frustrating": -2, "disaster": -2, "sad": -1, "angry": -2, "furious": -2, "hate": -2,
       "nervous": -1, "scared": -1, "sloppy": -1, "boring": -1, "fire the": -2}
# A post counts only if it names a team in full, both nicknames, or one nickname with the league / sport / city
# ("Knights" alone pulled Final Fantasy, "Senators" alone pulled politics).
SPORT_WORDS = {
    "NBA": ("nba", "basketball", "hoops", "rebounds", "assists", "tipoff", "tip-off", "dunk", "three-pointer", "triple-double", "points"),
    "NHL": ("nhl", "hockey", "goal", "goalie", "puck", "powerplay", "power play", "shutout", "saves", "hat trick", "period"),
    "NFL": ("nfl", "football", "touchdown", "qb", "quarterback", "field goal", "interception", "sack", "yards", "kickoff", "snf", "mnf", "tnf"),
    "CFB": ("cfb", "college football", "ncaa", "touchdown", "qb", "quarterback", "yards", "bowl"),
    "MLB": ("mlb", "baseball", "inning", "homer", "home run", "pitcher", "strikeout", "bullpen", "world series", "postseason"),
}
CONTROVERSY = ("refs", "ref ", "rigged", "robbed", "officiating", "missed call", "bad call", "review", "challenge", "var ", "replay")
NERVES = ("nervous", "scared", "worried", "anxious", "sweating", "can't watch", "cant watch")
PROFANE = re.compile(r"\b(f+u+c+k\w*|s+h+i+t\w*|b+i+t+c+h\w*|a+s+s+h+o+l+e\w*|d+a+m+n\w*|c+u+n+t\w*|d+i+c+k\w*)\b", re.I)
STOP = set("the a an and or but to of in on at for with is are was were be been this that it its they them their we our you your i me my he she his her "
           "game games team teams tonight night season vs at just get got so not no yes all one two three what when who how will would can could do did "
           "about from up down out more most very really like have has had there here then than also now new over after before last first https http social www com".split())


def _get(url: str, timeout: float = 12.0):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read())


def _clean(text: str) -> str:
    # Mastodon splits links and hashtags into spans: drop tags without spaces (rejoins them), then drop links.
    text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"</p>\s*<p>|<br\s*/?>", " ", text or "")))
    text = re.sub(r"^RE:\s*\S+", "", text.strip())
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return PROFANE.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text)


def score(text: str) -> float:
    t = f" {text.lower()} "
    s = 0
    for w, v in {**POS, **NEG}.items():
        if v and f" {w} " in t or (v and " " in w and w in t):
            s += v
    s += 0.5 * t.count("!") if s > 0 else 0
    return max(-1.0, min(1.0, s / 4))


def _bsky(q: str) -> list[dict]:
    try:
        d = _get(f"{BSKY}?{urllib.parse.urlencode({'q': q, 'limit': 50, 'sort': 'latest'})}")
    except Exception:
        return []
    out = []
    for p in d.get("posts") or []:
        rec = p.get("record") or {}
        txt = rec.get("text") or ""
        handle = (p.get("author") or {}).get("handle") or "bsky"
        uri = p.get("uri") or ""
        rkey = uri.rsplit("/", 1)[-1]
        out.append({"source": "Bluesky", "text": txt, "at": rec.get("createdAt") or p.get("indexedAt"), "author": "@" + handle,
                    "url": f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else None, "likes": p.get("likeCount") or 0})
    return out


def _masto(tag: str) -> list[dict]:
    try:
        d = _get(MASTO + urllib.parse.quote(tag) + "?limit=40")
    except Exception:
        return []
    return [{"source": "Mastodon", "text": s.get("content") or "", "at": s.get("created_at"), "author": "@" + ((s.get("account") or {}).get("acct") or "mastodon"),
             "url": s.get("url") if str(s.get("url") or "").startswith("https://") else None, "likes": (s.get("favourites_count") or 0) + (s.get("reblogs_count") or 0)} for s in d if isinstance(s, dict)]


def _when(v) -> float | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def vibe(league: str, home: str = "", away: str = "", home_name: str = "", away_name: str = "") -> dict:
    """The crowd around one game (both teams named) or a whole league (no teams)."""
    key = "|".join([league, home, away, home_name, away_name]).lower()
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    lg = league.upper()
    nick = lambda n: (n or "").split(" ")[-1] if n else ""
    teams = [t for t in (home_name or home, away_name or away) if t]
    ctx = SPORT_WORDS.get(lg, ())
    # Full names first (quoted), then the two nicknames together.
    # College names often arrive without a mascot ("Delaware"), so CFB searches carry the word football.
    tail = " football" if lg == "CFB" else ""
    queries = ([f'"{t}"{tail}' for t in teams] + [f"{nick(home_name) or home} {nick(away_name) or away}"]) if teams else [lg]
    nicks = [nick(t).lower() for t in teams if nick(t)]
    cities = [" ".join(t.split(" ")[:-1]).lower() for t in teams if " " in t]

    def relevant(text: str) -> bool:
        low = f" {text.lower()} "
        sporty = any(w in low for w in ctx) or f"#{lg.lower()}" in low
        if not teams:
            return sporty
        named = [t for t in teams if t.lower() in low]
        # A bare school or one-word name ("Delaware") is a state, a company, a tax shelter: it needs the sport too.
        if len(named) == 2 or (named and (sporty or (lg != "CFB" and all(len(t.split()) >= 2 for t in named)))):
            return True
        hits = [n for n in nicks if re.search(rf"\b{re.escape(n)}\b", low)]
        return len(hits) >= 2 or (bool(hits) and (sporty or any(c and c in low for c in cities)))
    posts: list[dict] = []
    for q in queries[:3]:
        posts += _bsky(q)
    for tag in ([lg] + [re.sub(r"\W", "", nick(t) or t) for t in teams])[:3]:
        posts += _masto(tag)
    now = time.time()
    seen, rows = set(), []
    for p in posts:
        t = _clean(p["text"])
        at = _when(p["at"])
        if len(t) < 12 or at is None or now - at > WINDOW_H * 3600 or t.lower() in seen:
            continue
        if not relevant(t):
            continue
        seen.add(t.lower())
        rows.append({**p, "text": t, "ts": at, "score": score(t)})
    rows.sort(key=lambda r: r["ts"], reverse=True)
    n = len(rows)
    avg = sum(r["score"] for r in rows) / n if n else 0.0
    low = " ".join(r["text"].lower() for r in rows)
    contro = sum(low.count(w) for w in CONTROVERSY)
    nerves = sum(low.count(w) for w in NERVES)
    span_h = max(0.25, (now - min((r["ts"] for r in rows), default=now)) / 3600) if rows else WINDOW_H
    per_hour = round(n / span_h, 1) if n else 0
    if n < 3:
        label = "QUIET"
    elif contro >= max(3, n * 0.15):
        label = "SALTY"
    elif avg >= 0.18:
        label = "HYPED"
    elif avg <= -0.18:
        label = "ANGRY"
    elif nerves >= max(2, n * 0.1):
        label = "NERVOUS"
    else:
        label = "MIXED"
    split = {}
    for side, t in (("home", home_name or home), ("away", away_name or away)):
        if not t:
            continue
        mine = [r for r in rows if (nick(t) or t).lower() in r["text"].lower()]
        split[side] = {"team": t, "mentions": len(mine), "vibe": round(sum(r["score"] for r in mine) / len(mine), 2) if mine else None}
    words: dict[str, int] = {}
    for r in rows:
        for w in re.findall(r"[a-z][a-z']{3,}", r["text"].lower()):
            if w not in STOP and not any(w in (x or "").lower().split() for x in teams):
                words[w] = words.get(w, 0) + 1
    trending = [w for w, c in sorted(words.items(), key=lambda kv: -kv[1]) if c >= 2][:6]
    quotes = sorted(rows, key=lambda r: (abs(r["score"]) + min(r["likes"], 50) / 50, r["ts"]), reverse=True)[:5]
    out = {
        "schema": "AISP_CROWD_VIBE_V1", "league": lg, "teams": teams, "label": label, "vibe": round(avg, 2), "posts": n, "per_hour": per_hour,
        "controversy": contro, "split": split, "trending": trending, "window_hours": WINDOW_H, "sources": ["Bluesky", "Mastodon"],
        "quotes": [{"text": (q["text"][:137] + "…") if len(q["text"]) > 140 else q["text"], "author": q["author"], "source": q["source"], "url": q["url"],
                    "age_min": int((now - q["ts"]) / 60), "tone": "up" if q["score"] > 0.1 else "down" if q["score"] < -0.1 else "flat"} for q in quotes],
        "generated_at": datetime.now(timezone.utc).isoformat(), "cost_usd": 0,
    }
    if len(_CACHE) > 400:  # production: distinct queries cannot grow the cache without bound
        _CACHE.clear()
    _CACHE[key] = (time.time(), out)
    return out
