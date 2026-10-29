"""Pinnacle benchmark lane (owner 2026-10-28: "collect both and implement any changes that could possibly provide an
edge").

Pinnacle's closing moneyline is the market's sharpest estimate and the standard yardstick for whether a price was
good. AISP's US-region feed never carried it. This lane asks The Odds API's EU region (where Pinnacle is listed;
"from public website which may incur a delay") for one sport's moneylines when one of that sport's games starts
within ``LEAD`` and the sport was not asked in the last ``SPACING`` -- 1 credit per request, at most
``PINNACLE_BENCHMARK_DAILY_REQUEST_CAP`` a day, and never while the shared daily budget is within ``HEADROOM`` of its
limit, so it can never take a request the betting lanes need.

What it stores is deliberately apart from ``odds_snapshots``: append-only JSONL under
``<runtime>/sharp-benchmark/<league>/<ET date>.jsonl`` (plus the raw archive). Pinnacle therefore never enters a
consensus, a no-vig probability, a freshness count or an execution price; it is evidence for measuring AISP's
model and prices against the sharpest close (``closing_lines``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

SPORT_KEYS = {"NHL": "icehockey_nhl", "NBA": "basketball_nba", "NFL": "americanfootball_nfl",
              "CFB": "americanfootball_ncaaf", "MLB": "baseball_mlb"}
LEAD = timedelta(minutes=50)
SPACING = timedelta(minutes=25)
HEADROOM = 30
PROVIDER = "THE_ODDS_API"
REGION = "eu"
BOOK = "pinnacle"
MARKETS = ["h2h"]


def endpoint_key(league: str) -> str:
    # Deliberately free of provider sport keys ("icehockey_nhl", "americanfootball"...): this lane is not any sport's
    # betting lane and must never be charged to one (odds_budget_lanes.league_of_endpoint).
    return f"pinnacle_benchmark_{league.lower()}"


def root(settings: Any) -> Path:
    from app.nba.availability import root as availability_root

    return availability_root(settings).parent / "sharp-benchmark"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def requests_today(session: Any, settings: Any) -> int:
    from app.core.time import local_day_bounds, local_today
    from app.models import ProviderRequestLedger

    start, end = local_day_bounds(local_today(settings.display_timezone), settings.display_timezone)
    return session.scalar(select(func.count(ProviderRequestLedger.id)).where(
        ProviderRequestLedger.provider == PROVIDER, ProviderRequestLedger.endpoint_key.like("pinnacle_benchmark_%"),
        ProviderRequestLedger.status == "RESERVED", ProviderRequestLedger.requested_at >= start,
        ProviderRequestLedger.requested_at < end)) or 0


def plan(session: Any, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Which sports are due one Pinnacle request now. Reads the database only."""

    from app.models import Game, GameStatus, League, ProviderRequestLedger

    moment = _aware(now or datetime.now(UTC))
    used = requests_today(session, settings)
    cap = int(getattr(settings, "pinnacle_benchmark_daily_request_cap", 0))
    due: list[dict[str, Any]] = []
    for league in SPORT_KEYS:
        starts = session.scalars(select(Game.starts_at).join(League, League.id == Game.league_id).where(
            League.code == league, Game.status == GameStatus.SCHEDULED,
            Game.starts_at > moment, Game.starts_at <= moment + LEAD)).all()
        if not starts:
            continue
        last = session.scalar(select(func.max(ProviderRequestLedger.requested_at)).where(
            ProviderRequestLedger.provider == PROVIDER, ProviderRequestLedger.endpoint_key == endpoint_key(league),
            ProviderRequestLedger.status == "RESERVED"))
        if last is not None and moment - _aware(last) < SPACING:
            continue
        due.append({"league": league, "sport_key": SPORT_KEYS[league], "games_starting": len(starts),
                    "first_start": min(_aware(s) for s in starts).isoformat()})
    room = max(0, cap - used)
    return {"due": due[:room], "requests_today": used, "daily_cap": cap, "skipped_for_cap": due[room:]}


def extract(raw: list[dict[str, Any]], *, league: str, received_at: datetime) -> list[dict[str, Any]]:
    """Pinnacle's two-way moneyline per event, with Pinnacle's own clock; events without one are skipped."""

    rows = []
    for event in raw or []:
        book = next((b for b in event.get("bookmakers") or [] if b.get("key") == BOOK), None)
        market = next((m for m in (book or {}).get("markets") or [] if m.get("key") == "h2h"), None)
        if not market:
            continue
        prices = {o.get("name"): o.get("price") for o in market.get("outcomes") or []}
        home, away = event.get("home_team"), event.get("away_team")
        if home not in prices or away not in prices or len(prices) != 2:
            continue  # a draw outcome or a name mismatch is not the two-way line
        rows.append({"schema": "AISP_PINNACLE_BENCHMARK_V1", "league": league, "provider_event_id": event.get("id"),
                     "commence_time": event.get("commence_time"), "home_team": home, "away_team": away,
                     "home_american": prices[home], "away_american": prices[away],
                     "pinnacle_last_update": market.get("last_update") or book.get("last_update"),
                     "received_at_utc": _aware(received_at).isoformat(),
                     "eu_books_in_response": len(event.get("bookmakers") or [])})
    return rows


def store(settings: Any, league: str, rows: list[dict[str, Any]], received_at: datetime) -> Path:
    from zoneinfo import ZoneInfo

    day = _aware(received_at).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    path = root(settings) / league.lower() / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return path


def _no_vig_home(home_american: float, away_american: float) -> float:
    dec = lambda a: 1 + (a / 100 if a > 0 else 100 / -a)  # noqa: E731
    ih, ia = 1 / dec(home_american), 1 / dec(away_american)
    return ih / (ih + ia)


def closing_lines(settings: Any, league: str, days: list[str]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """The last Pinnacle line received before each event's start: key (home, away, commence), with no-vig home."""

    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for day in days:
        p = root(settings) / league.lower() / f"{day}.jsonl"
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (row.get("received_at_utc") or "") >= (row.get("commence_time") or "").replace("Z", "+00:00"):
                continue
            key = (row["home_team"], row["away_team"], row["commence_time"])
            if key not in best or row["received_at_utc"] > best[key]["received_at_utc"]:
                best[key] = {**row, "no_vig_home": round(_no_vig_home(row["home_american"], row["away_american"]), 4)}
    return best
