"""Reading SportsGameOdds' own payload shape, on its own terms.

WHY THIS IS NOT A FIELD-MAPPING MANIFEST
----------------------------------------
The generic fallback adapter maps payloads with dotted paths over nested lists,
reading identity out of leaf values. SportsGameOdds puts identity in dictionary
KEYS:

    event.odds["points-home-game-ml-home"].byBookmaker["draftkings"].odds
                ^ market, period and side          ^ bookmaker

A dotted path cannot name "each key of this dict, and the key is the identity",
so forcing this payload through that model would mean inventing paths that do
not describe it. This module reads the documented structure directly instead,
and every rule below is checked against the payload rather than assumed.

WHAT A PAIR HAS TO SURVIVE
--------------------------
A moneyline pair is admitted only when all of the following hold, and each
failure is recorded by name so a missing quote is never reported as a bare
"unavailable":

* both sides appear in the SAME response -- never assembled across two fetches;
* ``oddID`` and ``opposingOddID`` reconcile in both directions;
* event, bookmaker, market, period and league identity agree on both sides;
* the event is pregame by the provider's own status flags;
* both prices parse as valid American odds;
* both sides carry a provider update timestamp;
* both sides are marked available by the book.

TIME IS RECORDED, NOT ASSERTED
------------------------------
The two sides of a book's moneyline carry INDEPENDENT provider timestamps, and
they frequently disagree -- measured at up to 208 seconds apart. Appearing in
one response does not make two updates simultaneous, so this module never says
they were. It keeps both stamps, records the skew explicitly, and dates the pair
by the OLDER of the two: the age of a pair is the age of its stalest half.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.markets.odds import InvalidOddsError, american_to_decimal

#: Provider market identity for a full-game moneyline, both sides.
HOME_ODD_ID = "points-home-game-ml-home"
AWAY_ODD_ID = "points-away-game-ml-away"
MONEYLINE_BET_TYPE = "ml"
MONEYLINE_PERIOD = "game"
AISP_MARKET_KEY = "h2h"

_AMERICAN = re.compile(r"^[+-]?\d+$")

# Why a candidate pair was refused. Each is a fact about the payload.
REJECT_NO_HOME_SIDE = "NO_HOME_SIDE_IN_RESPONSE"
REJECT_NO_AWAY_SIDE = "NO_AWAY_SIDE_IN_RESPONSE"
REJECT_PAIR_MISMATCH = "ODD_ID_PAIRING_DOES_NOT_RECONCILE"
REJECT_MARKET_IDENTITY = "MARKET_IDENTITY_MISMATCH"
REJECT_NOT_PREGAME = "EVENT_IS_NOT_PREGAME"
REJECT_BOOK_ONE_SIDED = "BOOKMAKER_PRICED_ONLY_ONE_SIDE"
REJECT_PRICE_INVALID = "PRICE_IS_NOT_VALID_AMERICAN_ODDS"
REJECT_TIMESTAMP_MISSING = "PROVIDER_TIMESTAMP_MISSING"
REJECT_UNAVAILABLE = "BOOKMAKER_MARKED_PRICE_UNAVAILABLE"


class SgoPayloadError(ValueError):
    """The payload is not the documented SportsGameOdds events shape."""


@dataclass(frozen=True)
class SidePrice:
    side: str                    # "home" | "away"
    odd_id: str
    american: int
    decimal: float
    provider_updated_at: datetime
    available: bool


@dataclass(frozen=True)
class MoneylinePair:
    """One bookmaker's complete two-sided moneyline on one pregame event."""

    event_id: str
    league_id: str
    commence_time: datetime
    home_team_id: str
    away_team_id: str
    home_team_name: str
    away_team_name: str
    bookmaker_key: str           # provider's own key, un-namespaced
    bookmaker_title: str
    home: SidePrice
    away: SidePrice
    received_at: datetime

    @property
    def pair_freshness_at(self) -> datetime:
        """The pair is only as fresh as its stalest half."""
        return min(self.home.provider_updated_at, self.away.provider_updated_at)

    @property
    def timestamp_skew_seconds(self) -> float:
        delta = self.home.provider_updated_at - self.away.provider_updated_at
        return abs(delta.total_seconds())

    def provenance(self) -> dict[str, Any]:
        """Everything a later reader needs to audit this price without the payload."""
        return {
            "provider": "SPORTSGAMEODDS",
            "provider_event_id": self.event_id,
            "provider_bookmaker_key": self.bookmaker_key,
            "home_odd_id": self.home.odd_id,
            "away_odd_id": self.away.odd_id,
            "home_provider_updated_at": self.home.provider_updated_at.isoformat(),
            "away_provider_updated_at": self.away.provider_updated_at.isoformat(),
            "pair_freshness_at": self.pair_freshness_at.isoformat(),
            "timestamp_skew_seconds": self.timestamp_skew_seconds,
            "received_at": self.received_at.isoformat(),
            "simultaneity_claimed": False,
            "note": ("The two sides carry independent provider update times. They are "
                     "not asserted to be simultaneous; the pair is dated by the older."),
        }


@dataclass
class ParseResult:
    pairs: list[MoneylinePair] = field(default_factory=list)
    #: Why each candidate was refused: {event_id: {bookmaker or "-": reason}}
    rejected: dict[str, dict[str, str]] = field(default_factory=dict)
    events_seen: int = 0
    pregame_events: int = 0

    def reject(self, event_id: str, who: str, reason: str) -> None:
        self.rejected.setdefault(event_id, {})[who] = reason


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_american(value: Any) -> int | None:
    """A price is a valid American price or it is nothing.

    The provider sends strings ("+3300", "-125"). Anything that is not an
    integer string, or that falls inside (-100, +100) where American odds do not
    exist, is refused rather than coerced.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str) and _AMERICAN.match(value.strip()):
        candidate = int(value.strip())
    else:
        return None
    try:
        american_to_decimal(candidate)
    except (InvalidOddsError, ValueError):
        return None
    return candidate


def is_pregame(event: dict[str, Any], *, now: datetime) -> bool:
    """Pregame by the provider's own flags AND by the clock.

    Both, deliberately. A flag can lag, and a start time can be revised; a quote
    is only usable before the game exists to be watched.
    """
    status = event.get("status") or {}
    if not isinstance(status, dict):
        return False
    if any(bool(status.get(flag)) for flag in ("started", "live", "completed", "ended", "cancelled")):
        return False
    starts = _parse_time(status.get("startsAt"))
    return starts is not None and starts > now


def parse_events(
    payload: Any, *, received_at: datetime, now: datetime | None = None,
    league_id: str = "MLB",
) -> ParseResult:
    """Every complete, pregame, two-sided moneyline in one response."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SgoPayloadError("payload is not a SportsGameOdds events response")
    now = now or received_at
    result = ParseResult()

    for event in payload["data"]:
        if not isinstance(event, dict):
            continue
        result.events_seen += 1
        event_id = str(event.get("eventID") or "")
        if not event_id:
            continue
        if str(event.get("leagueID") or "") != league_id:
            result.reject(event_id, "-", REJECT_MARKET_IDENTITY)
            continue
        if not is_pregame(event, now=now):
            result.reject(event_id, "-", REJECT_NOT_PREGAME)
            continue
        result.pregame_events += 1

        odds = event.get("odds")
        if not isinstance(odds, dict):
            result.reject(event_id, "-", REJECT_NO_HOME_SIDE)
            continue
        home_market, away_market = odds.get(HOME_ODD_ID), odds.get(AWAY_ODD_ID)
        if not isinstance(home_market, dict):
            result.reject(event_id, "-", REJECT_NO_HOME_SIDE)
            continue
        if not isinstance(away_market, dict):
            result.reject(event_id, "-", REJECT_NO_AWAY_SIDE)
            continue

        # The pairing must reconcile in BOTH directions. One-way agreement would
        # admit a side pointing at a market that does not point back.
        if (home_market.get("oddID") != HOME_ODD_ID
                or home_market.get("opposingOddID") != AWAY_ODD_ID
                or away_market.get("oddID") != AWAY_ODD_ID
                or away_market.get("opposingOddID") != HOME_ODD_ID):
            result.reject(event_id, "-", REJECT_PAIR_MISMATCH)
            continue
        for market in (home_market, away_market):
            if (market.get("betTypeID") != MONEYLINE_BET_TYPE
                    or market.get("periodID") != MONEYLINE_PERIOD):
                result.reject(event_id, "-", REJECT_MARKET_IDENTITY)
                break
        else:
            _collect_books(event, event_id, home_market, away_market, received_at, result)
    return result


def _collect_books(
    event: dict[str, Any], event_id: str,
    home_market: dict[str, Any], away_market: dict[str, Any],
    received_at: datetime, result: ParseResult,
) -> None:
    teams = event.get("teams") or {}
    home_team, away_team = teams.get("home") or {}, teams.get("away") or {}
    commence = _parse_time((event.get("status") or {}).get("startsAt"))
    home_id, away_id = str(home_team.get("teamID") or ""), str(away_team.get("teamID") or "")
    if not (commence and home_id and away_id):
        result.reject(event_id, "-", REJECT_MARKET_IDENTITY)
        return

    home_books = home_market.get("byBookmaker") or {}
    away_books = away_market.get("byBookmaker") or {}
    if not isinstance(home_books, dict) or not isinstance(away_books, dict):
        result.reject(event_id, "-", REJECT_BOOK_ONE_SIDED)
        return

    for book in sorted(set(home_books) | set(away_books)):
        raw_home, raw_away = home_books.get(book), away_books.get(book)
        # A pair is one bookmaker's two sides. A book that priced only one side
        # has not offered a market, and its half is never combined with another
        # book's half or another provider's.
        if not isinstance(raw_home, dict) or not isinstance(raw_away, dict):
            result.reject(event_id, book, REJECT_BOOK_ONE_SIDED)
            continue
        sides = {}
        failure = None
        for side, raw, odd_id in (("home", raw_home, HOME_ODD_ID), ("away", raw_away, AWAY_ODD_ID)):
            american = parse_american(raw.get("odds"))
            if american is None:
                failure = REJECT_PRICE_INVALID
                break
            updated = _parse_time(raw.get("lastUpdatedAt"))
            if updated is None:
                failure = REJECT_TIMESTAMP_MISSING
                break
            if raw.get("available") is not True:
                failure = REJECT_UNAVAILABLE
                break
            sides[side] = SidePrice(
                side=side, odd_id=odd_id, american=american,
                decimal=american_to_decimal(american),
                provider_updated_at=updated, available=True,
            )
        if failure is not None:
            result.reject(event_id, book, failure)
            continue
        result.pairs.append(MoneylinePair(
            event_id=event_id,
            league_id=str(event.get("leagueID") or ""),
            commence_time=commence,
            home_team_id=home_id, away_team_id=away_id,
            home_team_name=str((home_team.get("names") or {}).get("long") or home_id),
            away_team_name=str((away_team.get("names") or {}).get("long") or away_id),
            bookmaker_key=book,
            bookmaker_title=str((raw_home.get("bookmakerName") or book)).replace("_", " ").title(),
            home=sides["home"], away=sides["away"], received_at=received_at,
        ))


__all__ = [
    "AISP_MARKET_KEY",
    "AWAY_ODD_ID",
    "HOME_ODD_ID",
    "MoneylinePair",
    "ParseResult",
    "REJECT_BOOK_ONE_SIDED",
    "REJECT_MARKET_IDENTITY",
    "REJECT_NOT_PREGAME",
    "REJECT_NO_AWAY_SIDE",
    "REJECT_NO_HOME_SIDE",
    "REJECT_PAIR_MISMATCH",
    "REJECT_PRICE_INVALID",
    "REJECT_TIMESTAMP_MISSING",
    "REJECT_UNAVAILABLE",
    "SgoPayloadError",
    "SidePrice",
    "is_pregame",
    "parse_american",
    "parse_events",
]
