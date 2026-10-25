"""Historical market replay: stored sportsbook prices exactly as they stood at a past instant.

The replay exists so My Card can be demonstrated when nothing is current. It is
built from immutable stored observations and is honest about its time:

* ``as_of`` is the observation time of a real stored LIVE quote from a permitted
  provider, at least one freshness window in the past, so nothing in a replay
  can be mistaken for a current price.
* Only observations made at or before ``as_of`` are read; each quote keeps its
  own ``observed_at``; freshness is measured at ``as_of``, never at now.
* Only games that had not started at ``as_of`` appear, and nothing learned
  afterwards is returned -- no status, score, final, model, edge or decision.

Nothing here writes, and no provider is called.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

REPLAY_SCHEMA = "AISP_HISTORICAL_MARKET_REPLAY_V1"
REPLAY_BANNER = "Historical market replay — prices shown as they appeared at {time}."
REPLAY_PICK_LABEL = "Historical virtual pick — not an AISP decision."
REQUIRED_MARKETS = ("MONEYLINE", "SPREAD", "TOTAL")
LOOKBACK = timedelta(days=21)
#: The only keys a replay quote carries; everything else in coverage is dropped.
QUOTE_KEYS = ("market", "line", "sportsbook", "sportsbook_key", "provider", "classification",
              "observed_at", "age_minutes", "fresh", "sides")
GAME_KEYS = ("game_id", "league", "matchup", "home", "away", "starts_at")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def choose_as_of(session: Session, now: datetime | None = None) -> dict[str, Any] | None:
    """The newest stored pregame moment with moneyline, spread and total coverage.

    Returns the anchoring game and the observation instant, or None when no such
    moment exists in the lookback. The instant is at least one freshness window
    old, so a replay never overlaps the live freshness window.
    """

    from app.models import Game, Market, MarketOutcome, OddsSnapshot
    from app.services.command_center import PERMITTED_SOURCES, PRICED_LEAGUES, QUOTE_FRESHNESS_MINUTES
    from app.services.league_scope import in_league

    now = _aware(now) or datetime.now(UTC)
    cutoff = now - timedelta(minutes=QUOTE_FRESHNESS_MINUTES)
    league_filter = in_league(PRICED_LEAGUES[0])
    for code in PRICED_LEAGUES[1:]:
        league_filter = league_filter | in_league(code)
    kinds = func.count(func.distinct(Market.market_type))
    row = session.execute(
        select(Game.id, func.max(OddsSnapshot.observed_at).label("seen"), kinds)
        .select_from(OddsSnapshot)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Game, Game.id == Market.game_id)
        .where(OddsSnapshot.source_mode == "LIVE", OddsSnapshot.source.in_(PERMITTED_SOURCES),
               OddsSnapshot.observed_at <= cutoff, OddsSnapshot.observed_at >= cutoff - LOOKBACK,
               OddsSnapshot.observed_at < Game.starts_at, league_filter,
               Market.market_type.in_([_market_member(name) for name in REQUIRED_MARKETS]))
        .group_by(Game.id)
        .having(kinds >= len(REQUIRED_MARKETS))
        .order_by(func.max(OddsSnapshot.observed_at).desc(), Game.id)
        .limit(1)
    ).first()
    if row is None:
        return None
    return {"anchor_game_id": str(row[0]), "as_of": _aware(row[1])}


def _market_member(name: str):
    from app.models import MarketType

    return MarketType[name]


def historical_replay(session: Session, *, now: datetime | None = None,
                      as_of: datetime | None = None) -> dict[str, Any]:
    """The replay payload. ``as_of`` may pin a moment; it must be old enough."""

    from app.services.command_center import ET, QUOTE_FRESHNESS_MINUTES, market_coverage

    now = _aware(now) or datetime.now(UTC)
    anchor = None
    if as_of is None:
        chosen = choose_as_of(session, now)
        if chosen is None:
            return _empty(now, "No stored pregame moment with moneyline, spread and total prices "
                               "exists in the last 21 days.")
        as_of, anchor = chosen["as_of"], chosen["anchor_game_id"]
    as_of = _aware(as_of)
    if as_of > now - timedelta(minutes=QUOTE_FRESHNESS_MINUTES):
        return _empty(now, "A replay moment must be older than the live freshness window.")

    coverage = market_coverage(session, as_of.astimezone(ET).date(), as_of=as_of)
    games = []
    for game in coverage["games"]:
        starts = game.get("starts_at")
        if not starts or datetime.fromisoformat(starts) <= as_of:
            continue  # started (or no start) at the replay moment: its later story is not shown
        quotes = [{key: quote.get(key) for key in QUOTE_KEYS} for quote in game["quotes"]
                  if datetime.fromisoformat(quote["observed_at"]) <= as_of]
        if not any(q["fresh"] for q in quotes):
            continue
        games.append({**{key: game.get(key) for key in GAME_KEYS}, "quotes": quotes})
    shown = as_of.astimezone(ET)
    time_text = f"{shown:%b} {shown.day}, {shown:%Y} {shown:%I:%M %p} ET".replace(" 0", " ")
    return {
        "schema_version": REPLAY_SCHEMA,
        "mode": "HISTORICAL_REPLAY",
        "available": bool(games),
        "as_of": as_of.isoformat(),
        "as_of_label": time_text,
        "anchor_game_id": anchor,
        "banner": REPLAY_BANNER.format(time=time_text),
        "pick_label": REPLAY_PICK_LABEL,
        "freshness_window_minutes": QUOTE_FRESHNESS_MINUTES,
        "freshness_measured_at": as_of.isoformat(),
        "generated_at": now.isoformat(),
        "games": games,
        "excluded": ["game status", "scores and finals", "model probabilities", "edges",
                     "expected value", "decisions", "positions", "later prices"],
        "creates_decisions": False,
        "creates_positions": False,
        "provider_calls_made": 0,
    }


def _empty(now: datetime, message: str) -> dict[str, Any]:
    return {"schema_version": REPLAY_SCHEMA, "mode": "HISTORICAL_REPLAY", "available": False,
            "as_of": None, "as_of_label": None, "anchor_game_id": None, "banner": None,
            "pick_label": REPLAY_PICK_LABEL, "generated_at": now.isoformat(), "games": [],
            "message": message, "creates_decisions": False, "creates_positions": False,
            "provider_calls_made": 0}
