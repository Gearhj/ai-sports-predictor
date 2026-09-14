"""Why each game has no price, from stored evidence alone.

WHY A REASON IS NOT OPTIONAL
----------------------------
Ten games with no price rendered identically, and identical blanks teach a
reader something false: that the market is missing. On 2026-09-14 the real
cause was that eighteen NFL objects had been booked to the MLB lane the
previous evening, exhausting an allowance MLB never spent -- so the collector
was refused all day and never asked a sportsbook anything. "No price" and "we
never looked" are different facts and a reader can act on only one of them.

WHAT THIS WILL NOT SAY
----------------------
That a sportsbook has not posted a market. AISP can only observe what it
requested and received; it cannot observe a market it never asked for. That
claim is made only where a retained response actually shows an event returned
without a full-game market, and never inferred from an absence.

Reads persisted rows only. No provider request, at any time, for any reason.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, OddsSnapshot,
    ProviderRequestLedger, Sportsbook, Team,
)
from app.services.quote_coverage import MAX_QUOTE_STALENESS_MINUTES

SCHEMA_VERSION = "AISP_PRICE_COVERAGE_V1"

#: How far ahead of first pitch the pregame collector may spend on a game.
COLLECTION_WINDOW = timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES)

#: One reason per game, each a different thing to do about it.
PRICED = "PRICED"
WINDOW_NOT_OPEN = "COLLECTION_WINDOW_NOT_OPEN"
ALLOCATION_EXHAUSTED = "DAILY_PROVIDER_ALLOCATION_EXHAUSTED"
NOT_SELECTED = "EVENT_NOT_SELECTED_UNDER_TODAYS_ALLOCATION"
NO_FULL_GAME_MARKET = "PROVIDER_RETURNED_NO_FULL_GAME_MARKET"
INCOMPLETE_PAIR = "INCOMPLETE_TWO_SIDED_QUOTE"
IDENTITY_FAILED = "IDENTITY_MAPPING_FAILED"
EXPIRED = "QUOTE_EXPIRED"
INTAKE_FAILURE = "INTAKE_FAILURE"

EXPLANATION: dict[str, str] = {
    PRICED: "A complete two-sided quote is stored and still fresh.",
    WINDOW_NOT_OPEN: (
        "This game's collection window has not opened yet. Nothing has been "
        "requested for it, so no sportsbook has been asked."
    ),
    ALLOCATION_EXHAUSTED: (
        "The provider lane's daily allowance is spent, so the collector was "
        "refused before it could request this game. This says nothing about "
        "whether a market exists."
    ),
    NOT_SELECTED: (
        "The window is open and the lane has room, but this game was not among "
        "those the allocation covered."
    ),
    NO_FULL_GAME_MARKET: (
        "The provider returned this event without a full-game market. This is "
        "the only reason here that reflects what a sportsbook actually offered."
    ),
    INCOMPLETE_PAIR: (
        "Only one side is stored. A single side is not a price: a market needs "
        "both sides from the same provider, book and market."
    ),
    IDENTITY_FAILED: (
        "A provider event was returned but could not be bound to this game, so "
        "its prices were not stored against it."
    ),
    EXPIRED: (
        f"Every stored pair is older than the {MAX_QUOTE_STALENESS_MINUTES}-"
        "minute freshness limit, measured on the staler side."
    ),
    INTAKE_FAILURE: (
        "A request for this lane failed today. The failure is recorded; the "
        "prices were never received."
    ),
}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _pairs(session: Session, game_id: Any) -> list[dict[str, Any]]:
    """Complete same-provider, same-book, same-market two-sided quotes."""

    rows = session.execute(
        select(Market.id, Sportsbook.key, OddsSnapshot.source,
               MarketOutcome.outcome_key, OddsSnapshot.observed_at,
               OddsSnapshot.american_odds)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id == game_id, Market.market_type == "MONEYLINE")
        .order_by(OddsSnapshot.observed_at.desc())
    ).all()

    latest: dict[tuple, dict[str, Any]] = {}
    for market_id, book, source, outcome_key, observed_at, american in rows:
        side = str(outcome_key).split(":")[0].upper()
        if side not in ("HOME", "AWAY") or american is None:
            continue
        latest.setdefault((market_id, book, source, side), {
            "observed_at": _aware(observed_at), "american": int(american)})

    out: list[dict[str, Any]] = []
    for (market_id, book, source, _side) in set(latest):
        away = latest.get((market_id, book, source, "AWAY"))
        home = latest.get((market_id, book, source, "HOME"))
        if away is None or home is None:
            continue
        older = min(away["observed_at"], home["observed_at"])
        out.append({"sportsbook": book, "source": source,
                    "away_american": away["american"],
                    "home_american": home["american"],
                    # The staler side governs; the fresher one never rescues it.
                    "pair_observed_at": older.isoformat()})
    return out


def _lane_state(session: Session, now: datetime) -> dict[str, Any]:
    from app.services.provider_allocation import build_plan, misattributed_rows

    plan = build_plan(session, now=now)
    lane = plan.allocations["sgo_mlb_events"]
    misbooked = misattributed_rows(session, now=now)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    failures = session.scalar(
        select(func.count(ProviderRequestLedger.id)).where(
            ProviderRequestLedger.endpoint_key == "sgo_mlb_events",
            ProviderRequestLedger.status == "FAILED",
            ProviderRequestLedger.requested_at >= midnight))
    return {
        "daily_allowance": lane.daily_allowance,
        "spent_today": lane.spent_today,
        "room": lane.room,
        "failures_today": int(failures or 0),
        "objects_misattributed_today": sum(
            int(row.records_received or 0) for row in misbooked),
        "misattributed_requests_today": len(misbooked),
    }


def price_coverage(
    session: Session, *, day: str | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    """Every MLB game on one slate, and why it does or does not have a price."""

    from zoneinfo import ZoneInfo

    from app.services.slate_status import slate_window

    moment = now or datetime.now(UTC)
    # The slate is a LOCAL calendar day. The provider budget is a UTC day, and
    # the four-hour offset between them is exactly what let the previous
    # evening's spend exhaust this slate's allowance before it existed.
    zone = ZoneInfo("America/New_York")
    target = (datetime.fromisoformat(day).date() if day
              else moment.astimezone(zone).date())
    window = slate_window(target)
    lane = _lane_state(session, moment)

    rows = session.execute(
        select(Game.id, Game.starts_at, Game.status)
        .join(League, League.id == Game.league_id)
        .where(League.code == "MLB",
               Game.starts_at >= window.start_utc, Game.starts_at < window.end_utc)
        .order_by(Game.starts_at)
    ).all()

    games: list[dict[str, Any]] = []
    for game_id, starts_at, status in rows:
        starts = _aware(starts_at)
        sides = dict(session.execute(
            select(GameTeam.is_home, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == game_id)).all())
        pairs = _pairs(session, game_id)
        any_snapshot = session.scalar(
            select(func.count(OddsSnapshot.id))
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .where(Market.game_id == game_id))

        fresh = [p for p in pairs
                 if (moment - datetime.fromisoformat(p["pair_observed_at"])
                     ).total_seconds() / 60 <= MAX_QUOTE_STALENESS_MINUTES]

        if fresh:
            reason = PRICED
        elif pairs:
            reason = EXPIRED
        elif any_snapshot:
            reason = INCOMPLETE_PAIR
        elif lane["failures_today"]:
            reason = INTAKE_FAILURE
        elif starts - moment > COLLECTION_WINDOW:
            reason = WINDOW_NOT_OPEN
        elif lane["room"] <= 0:
            reason = ALLOCATION_EXHAUSTED
        else:
            reason = NOT_SELECTED

        games.append({
            "game_id": str(game_id),
            "teams": {"away": sides.get(False), "home": sides.get(True)},
            "starts_at": starts.isoformat(),
            "game_status": str(status),
            "pairs": pairs,
            "fresh_pairs": len(fresh),
            "stored_snapshots": int(any_snapshot or 0),
            "reason": reason,
            "explanation": EXPLANATION[reason],
            "window_opens_at": (starts - COLLECTION_WINDOW).isoformat(),
        })

    priced = sum(1 for g in games if g["reason"] == PRICED)
    return {
        "schema_version": SCHEMA_VERSION,
        "window": {
            "date": window.day.isoformat(),
            "timezone": window.timezone,
            "description": (
                f"Games starting on {window.day.isoformat()} local time in "
                f"{window.timezone}. The provider budget day is the UTC day, "
                "which is not the same span."),
        },
        "generated_at": moment.isoformat(),
        "lane": lane,
        "summary": {
            "games": len(games),
            "with_stored_prices": priced,
            "without_prices": len(games) - priced,
            "headline": _headline(len(games), priced, lane),
        },
        "games": games,
    }


def _headline(total: int, priced: int, lane: dict[str, Any]) -> str:
    """One line a reader can act on, naming the lane's actual state."""

    parts = [f"{total} MLB games", f"{priced} with stored prices"]
    if lane["objects_misattributed_today"]:
        parts.append(
            f"provider lane was charged {lane['objects_misattributed_today']} "
            "objects belonging to another league")
    elif lane["room"] <= 0 and lane["daily_allowance"]:
        parts.append(
            f"provider lane stopped after {lane['spent_today']} objects")
    elif lane["failures_today"]:
        parts.append(f"{lane['failures_today']} intake failure(s) today")
    else:
        parts.append(f"{lane['room']} of {lane['daily_allowance']} lane objects left")
    if priced < total:
        parts.append("investigation required" if lane["objects_misattributed_today"]
                     else "see each row for its reason")
    return " · ".join(parts)


__all__ = ["EXPLANATION", "SCHEMA_VERSION", "price_coverage"]
