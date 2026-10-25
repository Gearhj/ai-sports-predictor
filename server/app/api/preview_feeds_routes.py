"""/preview-api/* on the production backend (2026-10-25 site release).

The redesigned site reads its live feeds from these paths; in test mode the preview server on 5199 answered them.
Same paths and the same JSON, so the client is unchanged. Every handler is a read: public ESPN / MLB Stats API
feeds with short caches, AISP read-only queries (SET TRANSACTION READ ONLY), local UFC archive files, and the Booth
(one explain-only language-model call, rate limited). Blocking work runs off the event loop.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/preview-api", include_in_schema=False)


def _json(obj: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(obj, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/mlb/live")
async def mlb_live(date: str = "", home: str = "", away: str = "") -> JSONResponse:
    from app.preview_feeds import feeds

    if not (date and home and away):
        return _json({"error": "date, home and away are required"}, 400)
    try:
        return _json(await run_in_threadpool(feeds.mlb_live, date, home, away))
    except Exception as exc:  # the provider being down is a 502, never a crash
        feeds.log(f"MLB_LIVE_ERROR {exc}")
        return _json({"source": "MLB_STATS_API", "found": False, "reason": f"MLB Stats API unavailable: {type(exc).__name__}"}, 502)


@router.get("/football/live")
async def football_live(date: str = "", league: str = "nfl", home: str = "", home_name: str = "", away: str = "",
                        away_name: str = "", lite: str = "") -> JSONResponse:
    from app.preview_feeds import feeds

    if league not in ("nfl", "college-football") or not date:
        return _json({"error": "league (nfl|college-football) and date (YYYYMMDD) are required"}, 400)
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


@router.get("/logos/cfb")
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


@router.get("/scores")
async def scores(league: str = "", date: str = "") -> JSONResponse:
    from app.preview_feeds import court_live

    if league not in ("nfl", "cfb", "nba", "nhl", "mlb") or not date:
        return _json({"error": "league (nfl|cfb|nba|nhl|mlb) and date (YYYYMMDD) are required"}, 400)
    try:
        return _json(await run_in_threadpool(court_live.scores, league, date))
    except Exception as exc:
        return _json({"league": league, "games": [], "reason": type(exc).__name__}, 502)


@router.get("/insights/{kind}")
async def insights(kind: str, days: int = 0) -> JSONResponse:
    from app.preview_feeds import insights_db

    calls = {"closing": lambda: insights_db.beat_the_close(days or 75), "movers": insights_db.movers,
             "lab": lambda: insights_db.lab(days or 75), "closes": lambda: insights_db.closes(days or 21)}
    if kind not in calls:
        return _json({"error": "unknown insights endpoint"}, 404)
    try:
        return _json(await run_in_threadpool(calls[kind]))
    except Exception as exc:
        return _json({"error": f"insights unavailable: {type(exc).__name__}"}, 502)


@router.get("/arena/live")
async def arena_live(league: str = "", date: str = "", home: str = "", away: str = "") -> JSONResponse:
    from app.preview_feeds import court_live

    if league not in ("nba", "nhl") or not date or not home or not away:
        return _json({"error": "league (nba|nhl), date (YYYYMMDD), home and away are required"}, 400)
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


@router.get("/health")
async def health() -> JSONResponse:
    from app.preview_feeds import feeds

    return _json({"ok": True, "booth_key": bool(feeds._anthropic_key()), "booth_model": feeds.BOOTH_MODEL})


@router.post("/booth")
async def booth(request: Request) -> JSONResponse:
    from app.preview_feeds import feeds

    try:
        payload = await request.json()
    except Exception:
        return _json({"error": "bad JSON"}, 400)
    out = await run_in_threadpool(feeds.booth, payload if isinstance(payload, dict) else {})
    status = 200 if "answer" in out else (429 if "breath" in str(out.get("error")) else 502)
    return _json(out, status)
