"""/preview-api/* on the production backend (2026-10-25 site release).

The redesigned site reads its live feeds from these paths; in test mode the preview server on 5199 answered them.
Same paths and the same JSON, so the client is unchanged. Every handler is a read: public ESPN / MLB Stats API
feeds with short caches, AISP read-only queries (SET TRANSACTION READ ONLY), local UFC archive files, and the Booth
(one explain-only language-model call, rate limited). Blocking work runs off the event loop.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.dependencies import AppSettings, CurrentUser
from app.core.rate_limit import rate_limit

router = APIRouter(prefix="/preview-api", include_in_schema=False)

# Security review 10-27: values that reach provider URLs are validated (a date, a team code or name), and the
# research window is clamped, so a query string cannot reshape an outbound request or force a huge scan.
# Security review 11-01: fullmatch (a "$" anchor let a trailing newline through), and a date must be a real calendar
# day between 2015-01-01 and 30 days from now. The window is wide on purpose: Live View and Game Center open old,
# finished games; it only stops a caller from walking the provider's calendar without end.
_DATE = re.compile(r"(\d{8}|\d{4}-\d{2}-\d{2})", re.ASCII)
_TEAM = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .&'()-]{0,39}")
EARLIEST_DATE = date_cls(2015, 1, 1)
FUTURE_DAYS = 30


def _date_ok(value: str) -> bool:
    if not _DATE.fullmatch(value):
        return False
    try:
        day = datetime.strptime(value.replace("-", ""), "%Y%m%d").date()
    except ValueError:  # 20261340, 2026-02-30
        return False
    today = datetime.now(ZoneInfo("America/New_York")).date()
    return EARLIEST_DATE <= day <= today + timedelta(days=FUTURE_DAYS)


def _bad(**values: str) -> str | None:
    for key, value in values.items():
        if not value:
            continue
        if key == "date" and not _date_ok(value):
            return f"date must be a real day (YYYYMMDD or YYYY-MM-DD) from {EARLIEST_DATE:%Y-%m-%d} to {FUTURE_DAYS} days ahead"
        if key != "date" and not _TEAM.fullmatch(value):
            return f"{key} must be a team code or name"
    return None


def _feed_limit(name: str, per_minute: int):
    """Per-client sliding-window budget for one unauthenticated feed (security review 11-01).

    The shared "compute" category (30/min) is too tight for the site's own polling: Game Night reads /scores every
    20 s per league and Alerts every 45 s per league, Live Views poll every 10 s, the Slate polls every live football
    game every 30 s, and a tester may keep several tabs open. Each feed therefore gets its own budget, sized at
    several times one heavy user's polling, on the same limiter, client key and kill switch as rate_limit()."""
    from fastapi import HTTPException, status

    from app.core.rate_limit import _client_key, get_rate_limiter

    def dependency(request: Request, settings: AppSettings) -> None:
        if not settings.rate_limit_enabled:
            return
        try:
            retry = get_rate_limiter().check(f"feeds:{name}:{_client_key(request, settings)}", limit=per_minute,
                                             window_seconds=60.0)
        except Exception:  # noqa: BLE001 - an availability control fails open, like rate_limit()
            return
        if retry is not None:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                detail="Too many requests. Please slow down and retry shortly.",
                                headers={"Retry-After": str(int(retry) + 1)})

    return Depends(dependency)


# Requests per minute per client. One heavy user (several tabs) stays well under each.
FEED_LIMITS = {"mlb": 120, "football": 180, "scores": 240, "arena_live": 120, "ufc_card": 30, "ufc_bout": 90,
               "logos": 30, "arena_league": 90}


def _json(obj: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(obj, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/mlb/live", dependencies=[_feed_limit("mlb", FEED_LIMITS["mlb"])])
async def mlb_live(date: str = "", home: str = "", away: str = "") -> JSONResponse:
    from app.preview_feeds import feeds

    if not (date and home and away):
        return _json({"error": "date, home and away are required"}, 400)
    if (why := _bad(date=date, home=home, away=away)):
        return _json({"error": why}, 400)
    try:
        return _json(await run_in_threadpool(feeds.mlb_live, date, home, away))
    except Exception as exc:  # the provider being down is a 502, never a crash
        feeds.log(f"MLB_LIVE_ERROR {exc}")
        return _json({"source": "MLB_STATS_API", "found": False, "reason": f"MLB Stats API unavailable: {type(exc).__name__}"}, 502)


@router.get("/football/live", dependencies=[_feed_limit("football", FEED_LIMITS["football"])])
async def football_live(date: str = "", league: str = "nfl", home: str = "", home_name: str = "", away: str = "",
                        away_name: str = "", lite: str = "") -> JSONResponse:
    from app.preview_feeds import feeds

    if league not in ("nfl", "college-football") or not date:
        return _json({"error": "league (nfl|college-football) and date (YYYYMMDD) are required"}, 400)
    if (why := _bad(date=date, home=home, home_name=home_name, away=away, away_name=away_name)):
        return _json({"error": why}, 400)
    try:
        data = await run_in_threadpool(feeds.football_live, league, date, home, home_name, away, away_name)
    except Exception as exc:
        feeds.log(f"FOOTBALL_LIVE_ERROR {type(exc).__name__} {exc}")
        return _json({"source": "ESPN", "found": False, "reason": f"ESPN unavailable: {type(exc).__name__}"}, 502)
    if lite:
        wp = data.get("win_probability") or []
        step = max(1, len(wp) // 80)
        thin = wp[::step]
        if wp and thin[-1] is not wp[-1]:
            thin.append(wp[-1])
        data = {**{k: data.get(k) for k in ("source", "found", "reason", "league", "event_id", "status", "teams", "situation", "retrieved_at")},
                "win_probability": thin}
    return _json(data)


@router.get("/logos/cfb", dependencies=[_feed_limit("logos", FEED_LIMITS["logos"])])
async def cfb_logos() -> JSONResponse:
    from app.preview_feeds import feeds

    def build() -> list[dict[str, Any]]:
        out = []
        for grp in ("80", "81"):
            data = feeds._fetch_json(f"{feeds.ESPN}/college-football/teams?limit=1000&groups={grp}", 86400)
            for t in (((data.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []):
                tm = t.get("team") or {}
                logo = next((l.get("href") for l in tm.get("logos") or [] if "dark" not in (l.get("rel") or [])), None) \
                    or next((l.get("href") for l in tm.get("logos") or []), None)
                out.append({"id": tm.get("id"), "abbr": tm.get("abbreviation"), "location": tm.get("location"),
                            "short": tm.get("shortDisplayName"), "display": tm.get("displayName"),
                            "nickname": tm.get("nickname") or tm.get("name"), "color": tm.get("color"),
                            "alt": tm.get("alternateColor"), "logo": logo, "fbs": grp == "80"})
        return out
    try:
        return _json({"source": "ESPN", "teams": await run_in_threadpool(build)})
    except Exception as exc:
        feeds.log(f"CFB_LOGOS_ERROR {type(exc).__name__} {exc}")
        return _json({"source": "ESPN", "teams": [], "reason": type(exc).__name__}, 502)


@router.get("/scores", dependencies=[_feed_limit("scores", FEED_LIMITS["scores"])])
async def scores(league: str = "", date: str = "") -> JSONResponse:
    from app.preview_feeds import court_live

    if league not in ("nfl", "cfb", "nba", "nhl", "mlb") or not date:
        return _json({"error": "league (nfl|cfb|nba|nhl|mlb) and date (YYYYMMDD) are required"}, 400)
    if (why := _bad(date=date)):
        return _json({"error": why}, 400)
    try:
        return _json(await run_in_threadpool(court_live.scores, league, date))
    except Exception as exc:
        return _json({"league": league, "games": [], "reason": type(exc).__name__}, 502)


@router.get("/insights/{kind}")
async def insights(kind: str, days: int = 0) -> JSONResponse:
    from app.preview_feeds import insights_db

    days = max(1, min(120, days)) if days else 0
    calls = {"closing": lambda: insights_db.beat_the_close(days or 75), "movers": insights_db.movers,
             "lab": lambda: insights_db.lab(days or 75), "closes": lambda: insights_db.closes(days or 21),
             "underdogs": lambda: insights_db.underdogs(days or 120)}
    if kind not in calls:
        return _json({"error": "unknown insights endpoint"}, 404)
    try:
        return _json(await run_in_threadpool(calls[kind]))
    except Exception as exc:
        return _json({"error": f"insights unavailable: {type(exc).__name__}"}, 502)


@router.get("/vibe", dependencies=[Depends(rate_limit("feeds:vibe", category="compute"))])
async def vibe(league: str = "", home: str = "", away: str = "", home_name: str = "", away_name: str = "") -> JSONResponse:
    """THE CROWD box (owner 10-27): public Bluesky + Mastodon posts about a game or league, scored locally, $0."""
    if league not in ("nfl", "cfb", "nba", "nhl", "mlb"):
        return _json({"error": "league (nfl|cfb|nba|nhl|mlb) is required"}, 400)
    home_name, away_name = home_name[:40], away_name[:40]
    if bad := _bad(home=home, away=away, home_name=home_name, away_name=away_name):
        return _json({"error": bad}, 400)
    from app.preview_feeds import crowd_vibe

    try:
        return _json(await run_in_threadpool(crowd_vibe.vibe, league, home, away, home_name, away_name))
    except Exception as exc:
        return _json({"league": league.upper(), "label": "QUIET", "posts": 0, "quotes": [], "reason": type(exc).__name__}, 502)


# The gate alarm's self-heal log (Mission Control's Repair Drone). Only display fields leave the machine: no run ids,
# provider payloads, paths or exception text.
HEAL_LOG = Path(r"C:\Users\james\aisp-analysis\gate-alarm\heal.jsonl")
_HEAL_FIELDS = ("at", "heal", "job", "why", "status", "terminated")


def _heals() -> list[dict[str, Any]]:
    out = []
    for line in HEAL_LOG.read_text(encoding="utf-8").splitlines()[-60:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        item = {k: row[k] for k in _HEAL_FIELDS if isinstance(row.get(k), (str, bool))}
        if row.get("error"):
            item["error"] = "error"
        if row.get("issued"):
            item["issued"] = "week grant"
        out.append(item)
    return out


@router.get("/heals")
async def heals() -> JSONResponse:
    try:
        return _json({"heals": await run_in_threadpool(_heals)})
    except OSError:
        return _json({"heals": []})


@router.get("/opening-lines")
async def opening_lines(settings: AppSettings) -> JSONResponse:
    """Day-before opening moneylines by game id (app/services/opening_lines.py): files only, display only."""

    from app.services.opening_lines import openers

    try:
        return _json({"openers": await run_in_threadpool(openers, settings)})
    except OSError:
        return _json({"openers": {}})


@router.post("/arena/sync", dependencies=[Depends(rate_limit("feeds:arena", category="compute"))])
async def arena_sync(request: Request, user: CurrentUser, settings: AppSettings) -> JSONResponse:
    """The signed-in player's LIVE Arena calls (a game; never AISP's bankroll)."""
    from app.preview_feeds import arena_league

    body = await request.body()
    if len(body) > 400_000:
        return _json({"error": "too large"}, 413)
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except ValueError:
        return _json({"error": "bad JSON"}, 400)
    out, status = await run_in_threadpool(arena_league.sync, arena_league.store_path(settings), str(user.id),
                                          user.display_name, payload if isinstance(payload, dict) else {})
    return _json(out, status)


@router.get("/arena/league", dependencies=[_feed_limit("arena_league", FEED_LIMITS["arena_league"])])
async def arena_league_board(user: CurrentUser, settings: AppSettings) -> JSONResponse:
    """Every player's calls; a rival's side stays hidden until you lock the same game or it starts."""
    from app.preview_feeds import arena_league

    return _json(await run_in_threadpool(arena_league.league, arena_league.store_path(settings), str(user.id)))


@router.get("/arena/live", dependencies=[_feed_limit("arena_live", FEED_LIMITS["arena_live"])])
async def arena_live(league: str = "", date: str = "", home: str = "", away: str = "") -> JSONResponse:
    from app.preview_feeds import court_live

    if league not in ("nba", "nhl") or not date or not home or not away:
        return _json({"error": "league (nba|nhl), date (YYYYMMDD), home and away are required"}, 400)
    if (why := _bad(date=date, home=home, away=away)):
        return _json({"error": why}, 400)
    try:
        return _json(await run_in_threadpool(court_live.court_live, league, date, home, away))
    except Exception as exc:
        return _json({"source": "ESPN", "found": False, "reason": f"ESPN unavailable: {type(exc).__name__}"}, 502)


@router.get("/ufc/archive")
async def ufc_archive() -> JSONResponse:
    from app.preview_feeds import ufc_archive as archive

    try:
        return _json(await run_in_threadpool(archive.build))
    except Exception as exc:
        return _json({"error": f"UFC archive unavailable: {type(exc).__name__}"}, 502)


@router.get("/ufc/card", dependencies=[_feed_limit("ufc_card", FEED_LIMITS["ufc_card"])])
async def ufc_card(date: str = "", card: str = "") -> JSONResponse:
    """This weekend's UFC card (or the last finished one): ESPN public feeds + UFC.com rankings, read-only."""
    from app.preview_feeds import ufc_card as feed

    if (why := _bad(date=date)):
        return _json({"error": why}, 400)
    try:
        return _json(await run_in_threadpool(feed.card, "last" if card == "last" else date.replace("-", "")))
    except Exception as exc:  # the provider being down is a 502, never a crash
        return _json({"found": False, "reason": f"ESPN unavailable: {type(exc).__name__}"}, 502)


@router.get("/ufc/bout", dependencies=[_feed_limit("ufc_bout", FEED_LIMITS["ufc_bout"])])
async def ufc_bout(event: str = "", comp: str = "", a: str = "", b: str = "") -> JSONResponse:
    """One fight: official fight stats (live while it is on), ESPN's timeline and the judges' scores."""
    from app.preview_feeds import ufc_card as feed

    try:
        return _json(await run_in_threadpool(feed.bout, event, comp, a, b))
    except Exception as exc:
        return _json({"found": False, "reason": f"ESPN unavailable: {type(exc).__name__}"}, 502)


@router.get("/health")
async def health() -> JSONResponse:
    from app.preview_feeds import feeds

    return _json({"ok": True})


_BOOTH_LOCK = threading.Lock()
_BOOTH_DAY: dict[str, Any] = {"day": None, "site": 0, "users": {}}


def _booth_allow(user_id: str, per_user: int, per_site: int) -> str | None:
    """Count one question against today's caps (ET day); the reason it is refused, else None."""
    day = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    with _BOOTH_LOCK:
        if _BOOTH_DAY["day"] != day:
            _BOOTH_DAY.update(day=day, site=0, users={})
        used = _BOOTH_DAY["users"].get(user_id, 0)
        if used >= per_user:
            return f"You have used today's {per_user} Booth questions. They reset at midnight ET."
        if _BOOTH_DAY["site"] >= per_site:
            return "The Booth has answered its daily limit for the whole site. It resets at midnight ET."
        _BOOTH_DAY["users"][user_id] = used + 1
        _BOOTH_DAY["site"] += 1
    return None


@router.post("/booth")
async def booth(request: Request, user: CurrentUser, settings: AppSettings) -> JSONResponse:
    """Paid language-model answers (security review 10-27): signed-in users only, a per-user and a whole-site
    daily cap, and a bounded request."""
    from app.preview_feeds import feeds

    body = await request.body()
    if len(body) > 65_536:
        return _json({"error": "request too large"}, 413)
    try:
        import json as _jsonlib

        payload = _jsonlib.loads(body or b"{}")
    except Exception:
        return _json({"error": "bad JSON"}, 400)
    refused = _booth_allow(str(user.id), settings.booth_per_user_per_day, settings.booth_site_per_day)
    if refused:
        return _json({"error": refused}, 429)
    out = await run_in_threadpool(feeds.booth, payload if isinstance(payload, dict) else {})
    status = 200 if "answer" in out else (429 if "breath" in str(out.get("error")) else 502)
    return _json(out, status)
