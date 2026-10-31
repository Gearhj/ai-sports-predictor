"""Day-before opening prices (owner 2026-10-30: "collect a day-before opening price for every game ... and show it
next to the current price. Don't change when AISP bets.").

Sportsbooks post moneylines the day before (NFL a week out). AISP buys its betting price about 75 minutes before
each start; this lane asks The Odds API's US region once per sport per Eastern day, from ``OPENS_AFTER_ET_HOUR``,
for every game that sport has in the next ``WINDOW`` -- one request returns the whole slate, 1 credit (h2h only),
at most one per sport a day, and never while the shared daily budget is within ``HEADROOM`` of its limit.

What it stores is deliberately apart from ``odds_snapshots``: append-only JSONL under
``<runtime>/opening-lines/<league>/<ET date>.jsonl``. An opening price therefore never enters a consensus, a no-vig
probability, a freshness count or an execution price; it is display only (``openers``: the earliest line received
for each game before its start).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

SPORT_KEYS = {"NHL": "icehockey_nhl", "NBA": "basketball_nba", "NFL": "americanfootball_nfl",
              "CFB": "americanfootball_ncaaf", "MLB": "baseball_mlb"}
WINDOW = timedelta(hours=48)
OPENS_AFTER_ET_HOUR = 10
HEADROOM = 30
PROVIDER = "THE_ODDS_API"
REGION = "us"
MARKETS = ["h2h"]
ET = ZoneInfo("America/New_York")


def endpoint_key(league: str) -> str:
    # Deliberately free of provider sport keys ("icehockey_nhl", "ncaaf"...): this lane is not any sport's betting
    # lane and must never be charged to one (odds_budget_lanes.league_of_endpoint).
    return f"opening_line_{league.lower()}"


def root(settings: Any) -> Path:
    from app.nba.availability import root as availability_root

    return availability_root(settings).parent / "opening-lines"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _asked_today(session: Any, league: str, moment: datetime) -> bool:
    from app.models import ProviderRequestLedger

    day = moment.astimezone(ET).date()
    start = datetime(day.year, day.month, day.day, tzinfo=ET)
    return bool(session.scalar(select(func.count(ProviderRequestLedger.id)).where(
        ProviderRequestLedger.provider == PROVIDER, ProviderRequestLedger.endpoint_key == endpoint_key(league),
        ProviderRequestLedger.status == "RESERVED", ProviderRequestLedger.requested_at >= start,
        ProviderRequestLedger.requested_at < start + timedelta(days=1))))


def plan(session: Any, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Which sports are due their one opening-price request today. Reads the database only."""

    from app.models import Game, GameStatus, League

    moment = _aware(now or datetime.now(UTC))
    if moment.astimezone(ET).hour < OPENS_AFTER_ET_HOUR:
        return {"due": [], "reason": f"opening prices are asked from {OPENS_AFTER_ET_HOUR}:00 ET"}
    due: list[dict[str, Any]] = []
    for league in SPORT_KEYS:
        games = session.scalar(select(func.count(Game.id)).join(League, League.id == Game.league_id).where(
            League.code == league, Game.status == GameStatus.SCHEDULED,
            Game.starts_at > moment, Game.starts_at <= moment + WINDOW)) or 0
        if games and not _asked_today(session, league, moment):
            due.append({"league": league, "sport_key": SPORT_KEYS[league], "games_in_window": games})
    return {"due": due}


def _decimal(american: float) -> float:
    return 1 + (american / 100 if american > 0 else 100 / -american)


def _american(decimal: float) -> int:
    return round((decimal - 1) * 100) if decimal >= 2 else round(-100 / (decimal - 1))


def extract(raw: list[dict[str, Any]], *, league: str, received_at: datetime) -> list[dict[str, Any]]:
    """Each event's US-book moneylines: the best price per side, the median price and the median no-vig home
    probability. Events without a clean two-way line at any book are skipped."""

    rows = []
    received = _aware(received_at)
    for event in raw or []:
        home, away = event.get("home_team"), event.get("away_team")
        try:
            if datetime.fromisoformat(str(event.get("commence_time")).replace("Z", "+00:00")) <= received:
                continue  # already started: not an opening price
        except ValueError:
            continue
        books: dict[str, list[float]] = {}
        for book in event.get("bookmakers") or []:
            market = next((m for m in book.get("markets") or [] if m.get("key") == "h2h"), None)
            prices = {o.get("name"): o.get("price") for o in (market or {}).get("outcomes") or []}
            if len(prices) == 2 and isinstance(prices.get(home), (int, float)) and isinstance(prices.get(away), (int, float)) \
                    and abs(prices[home]) >= 100 and abs(prices[away]) >= 100:
                books[book.get("key") or "?"] = [prices[home], prices[away]]
        if not books:
            continue
        dh = [_decimal(h) for h, _ in books.values()]
        da = [_decimal(a) for _, a in books.values()]
        no_vig = [(1 / h) / (1 / h + 1 / a) for h, a in zip(dh, da)]
        best_home = max(books.items(), key=lambda kv: _decimal(kv[1][0]))
        best_away = max(books.items(), key=lambda kv: _decimal(kv[1][1]))
        rows.append({"schema": "AISP_OPENING_LINE_V1", "league": league, "provider_event_id": event.get("id"),
                     "commence_time": event.get("commence_time"), "home_team": home, "away_team": away,
                     "best_home": {"american": best_home[1][0], "book": best_home[0]},
                     "best_away": {"american": best_away[1][1], "book": best_away[0]},
                     "median_home_american": _american(median(dh)), "median_away_american": _american(median(da)),
                     "no_vig_home": round(median(no_vig), 4), "books": len(books),
                     "received_at_utc": received.isoformat()})
    return rows


def _teams(session: Any, league: str) -> Any:
    """``name -> team id or None`` through the same closed name maps the betting lanes bind with."""

    from app.cfb.odds_api_price import normalise

    if league == "NBA":
        from app.nba.odds_api_price import nba_franchises, resolve_team as nba_resolve

        franchises = nba_franchises(session)
        return lambda name: (lambda r: next(iter(r.team_ids)) if r.ok else None)(nba_resolve(name, franchises))
    if league == "CFB":
        from app.cfb.odds_api_price import cfb_schools, resolve_team as cfb_resolve

        schools = cfb_schools(session)
        return lambda name: (lambda r: next(iter(r.team_ids)) if r.ok else None)(cfb_resolve(name, schools))
    from app.models import League, Team

    by_name: dict[str, set[Any]] = {}
    by_abbr: dict[str, set[Any]] = {}
    for team_id, name, abbr in session.execute(select(Team.id, Team.name, Team.abbreviation)
                                               .join(League, League.id == Team.league_id)
                                               .where(League.code == league)).all():
        by_name.setdefault(normalise(name), set()).add(team_id)
        by_abbr.setdefault(str(abbr or "").upper(), set()).add(team_id)
    one = lambda ids: next(iter(ids)) if len(ids) == 1 else None  # noqa: E731
    if league == "NFL":
        # The NFL lane binds by abbreviation (AISP still names LV "Oakland Raiders"; the feed says "Las Vegas").
        from app.services.nfl_shadow_odds_service import NFL_ODDS_TEAM_IDENTITIES

        return lambda name: one(by_abbr.get(NFL_ODDS_TEAM_IDENTITIES.get(str(name or "").strip(), ""), set())) \
            or one(by_name.get(normalise(name), set()))
    return lambda name: one(by_name.get(normalise(name), set()))


def bind(session: Any, league: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach ``game_id``: exactly one AISP game with the same home and away teams on the same Eastern date.
    Read-only; a row that does not bind keeps ``game_id`` None (stored, never shown)."""

    from app.models import Game, GameTeam, League

    if not rows:
        return rows
    team_of = _teams(session, league)
    kicks = [datetime.fromisoformat(r["commence_time"].replace("Z", "+00:00")) for r in rows]
    games: dict[tuple[Any, Any, Any], list[Any]] = {}
    for game_id, starts_at in session.execute(select(Game.id, Game.starts_at).join(League, League.id == Game.league_id)
                                              .where(League.code == league, Game.starts_at >= min(kicks) - timedelta(days=1),
                                                     Game.starts_at <= max(kicks) + timedelta(days=1))).all():
        sides = {s.is_home: s.team_id for s in session.scalars(select(GameTeam).where(GameTeam.game_id == game_id))}
        if True in sides and False in sides:
            games.setdefault((sides[True], sides[False], _aware(starts_at).astimezone(ET).date()), []).append(game_id)
    for row, kick in zip(rows, kicks):
        home, away = team_of(row["home_team"]), team_of(row["away_team"])
        found = games.get((home, away, kick.astimezone(ET).date()), []) if home and away else []
        row["game_id"] = str(found[0]) if len(found) == 1 else None
    return rows


def store(settings: Any, league: str, rows: list[dict[str, Any]], received_at: datetime) -> Path:
    day = _aware(received_at).astimezone(ET).date().isoformat()
    path = root(settings) / league.lower() / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return path


#: Owner 10-31 tester launch: every home-page view read the opener files again. They change once a day per sport,
#: so a minute-old answer is the same answer.
_MEMO: dict[tuple[str, str, int], tuple[float, dict[str, dict[str, Any]]]] = {}
MEMO_SECONDS = 60.0


def openers(settings: Any, *, now: datetime | None = None, days_back: int = 3) -> dict[str, dict[str, Any]]:
    """The earliest bound line received before each game's start, from the last ``days_back`` Eastern days."""

    if now is not None:
        return _openers(settings, now=now, days_back=days_back)
    import time

    key = (str(root(settings)), _aware(datetime.now(UTC)).astimezone(ET).date().isoformat(), days_back)
    hit = _MEMO.get(key)
    if hit and time.monotonic() - hit[0] < MEMO_SECONDS:
        return hit[1]
    out = _openers(settings, now=None, days_back=days_back)
    _MEMO.clear()
    _MEMO[key] = (time.monotonic(), out)
    return out


def _openers(settings: Any, *, now: datetime | None, days_back: int) -> dict[str, dict[str, Any]]:
    today = _aware(now or datetime.now(UTC)).astimezone(ET).date()
    days = [(today - timedelta(days=n)).isoformat() for n in range(days_back + 1)]
    first: dict[str, dict[str, Any]] = {}
    base = root(settings)
    for league in SPORT_KEYS:
        for day in days:
            path = base / league.lower() / f"{day}.jsonl"
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                game_id = row.get("game_id")
                if not game_id or row.get("received_at_utc", "") >= str(row.get("commence_time", "")).replace("Z", "+00:00"):
                    continue
                if game_id not in first or row["received_at_utc"] < first[game_id]["received_at_utc"]:
                    first[game_id] = row
    return first
