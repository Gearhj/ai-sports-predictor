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
from collections import Counter
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

# ---------------------------------------------------------------------------
# Main full-game markets
#
# A SportsGameOdds oddID is `statID-statEntityID-periodID-betTypeID-sideID`, and
# every one of those five parts has to be checked before a row is treated as the
# combined game total. The provider returns roughly 1,300 odds per MLB event and
# only six of them are the three main full-game markets; the rest are team
# totals, player props, inning and half markets, even/odd and yes/no questions.
#
# The trap the brief names is real and is visible in the retained payload:
# `points-all-game-ou-over` is the combined game total, while
# `points-home-game-ou-over` is HOME TEAM total runs and
# `points-ALEC_BOHM_1_MLB-game-ou-over` is a player prop. All three are
# betTypeID "ou" with statID "points" or similar. Only statEntityID separates
# them, so statEntityID is checked explicitly rather than assumed.
# ---------------------------------------------------------------------------

#: The full game. Not "reg" (regulation only), "1ix5" (first five innings),
#: "1h", or any single-inning period -- those are different contracts with
#: different settlement and must never be mixed into a full-game market.
GAME_PERIOD = "game"
#: Runs. Distinguishes the run markets from hits, home runs, strikeouts, etc.
POINTS_STAT = "points"
#: The combined game, as opposed to one team ("home"/"away") or a player.
COMBINED_ENTITY = "all"

#: Provider bet type -> the AISP market key the normalizer already understands.
MAIN_MARKET_KEYS = {"ml": "h2h", "ou": "totals", "sp": "spreads"}

#: Reasons a main market was not retained. Each is a fact about the payload.
REJECT_NOT_MAIN_MARKET = "NOT_A_MAIN_FULL_GAME_MARKET"
REJECT_WRONG_ENTITY = "STAT_ENTITY_IS_NOT_THE_COMBINED_GAME"
REJECT_LINE_MISSING = "BOOKMAKER_LINE_MISSING"
REJECT_LINE_DISAGREES = "BOOKMAKER_SIDES_QUOTE_DIFFERENT_LINES"

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




# ---------------------------------------------------------------------------
# Main full-game market parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketSideQuote:
    """One bookmaker's price on one side of one market."""

    side_id: str
    odd_id: str
    #: The label the normalizer will store, e.g. "Over" or a team name.
    label: str
    american: int
    decimal: float
    #: The BOOKMAKER's own line, not the provider's consensus. Books disagree
    #: about the number as well as the price, and the retained payload has a
    #: book at 9.5 under an odd-level bookOverUnder of 8.5.
    point: float | None
    provider_updated_at: datetime


@dataclass(frozen=True)
class MainMarketQuote:
    """One bookmaker's two-sided quote on one full-game market."""

    event_id: str
    league_id: str
    commence_time: datetime
    home_team_id: str
    away_team_id: str
    home_team_name: str
    away_team_name: str
    bookmaker_key: str
    bookmaker_title: str
    #: h2h | totals | spreads
    market_key: str
    bet_type_id: str
    period_id: str
    stat_id: str
    stat_entity_id: str
    sides: tuple[MarketSideQuote, MarketSideQuote]
    received_at: datetime

    @property
    def freshness_at(self) -> datetime:
        """A pair is only as fresh as its stalest half."""
        return min(side.provider_updated_at for side in self.sides)


@dataclass
class MainMarketParseResult:
    events_seen: int = 0
    pregame_events: int = 0
    quotes: list["MainMarketQuote"] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    #: What was present in the payload but deliberately not ingested, so an
    #: unimplemented market is visible rather than silently dropped.
    observed_not_ingested: Counter = field(default_factory=Counter)

    def reject(self, event_id: str, book: str, market: str, reason: str) -> None:
        self.rejected.append({
            "event_id": event_id, "bookmaker": book, "market": market, "reason": reason,
        })

    def by_market(self) -> dict[str, int]:
        counts: Counter = Counter()
        for quote in self.quotes:
            counts[quote.market_key] += 1
        return dict(counts)


def classify_odd(entry: dict[str, Any]) -> str:
    """What kind of market one odds entry is. Used for the coverage matrix.

    Deliberately coarse and deliberately exhaustive: every returned odd lands in
    exactly one bucket, so nothing the provider sent can go unaccounted for.
    """

    bet_type = str(entry.get("betTypeID") or "")
    period = str(entry.get("periodID") or "")
    stat = str(entry.get("statID") or "")
    entity = str(entry.get("statEntityID") or "")

    if period != GAME_PERIOD:
        return "PARTIAL_GAME_PERIOD"
    if entity not in {COMBINED_ENTITY, "home", "away"}:
        return "PLAYER_PROP"
    if bet_type not in MAIN_MARKET_KEYS:
        return "OTHER_BET_TYPE"
    if stat != POINTS_STAT:
        return "NON_RUNS_STAT"
    if bet_type == "ou" and entity != COMBINED_ENTITY:
        return "TEAM_TOTAL"
    if bet_type in {"ml", "sp"} and entity == COMBINED_ENTITY:
        return "OTHER_BET_TYPE"
    return "MAIN_FULL_GAME"


def _main_market_pairs(
    odds: dict[str, Any],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """The (market_key, side_a, side_b) triples this consumer retains.

    Each is located by its exact oddID, not by scanning for anything that looks
    like a total. Scanning is how a team total or a first-five line ends up
    priced as the full game.
    """

    wanted = [
        ("h2h", HOME_ODD_ID, AWAY_ODD_ID),
        ("totals", "points-all-game-ou-over", "points-all-game-ou-under"),
        ("spreads", "points-home-game-sp-home", "points-away-game-sp-away"),
    ]
    found = []
    for market_key, first_id, second_id in wanted:
        first, second = odds.get(first_id), odds.get(second_id)
        if isinstance(first, dict) and isinstance(second, dict):
            found.append((market_key, first, second))
    return found


def _verify_identity(entry: dict[str, Any], odd_id: str, opposing_id: str) -> str | None:
    """Every part of the oddID, checked. Returns a rejection reason or None."""

    if entry.get("oddID") != odd_id or entry.get("opposingOddID") != opposing_id:
        return REJECT_PAIR_MISMATCH
    if str(entry.get("periodID") or "") != GAME_PERIOD:
        return REJECT_NOT_MAIN_MARKET
    if str(entry.get("statID") or "") != POINTS_STAT:
        return REJECT_NOT_MAIN_MARKET
    bet_type = str(entry.get("betTypeID") or "")
    if bet_type not in MAIN_MARKET_KEYS:
        return REJECT_NOT_MAIN_MARKET
    entity = str(entry.get("statEntityID") or "")
    if bet_type == "ou" and entity != COMBINED_ENTITY:
        # A team total wearing the same betTypeID. This is the check that stops
        # "home team over 4.5" being priced against the game's total-runs PMF.
        return REJECT_WRONG_ENTITY
    if bet_type in {"ml", "sp"} and entity not in {"home", "away"}:
        return REJECT_WRONG_ENTITY
    if entry.get("cancelled") is True:
        return REJECT_UNAVAILABLE
    return None


def _book_line(raw: dict[str, Any], market_key: str) -> float | None:
    """The bookmaker's own line for this side, or None when it did not post one.

    Never the odd-level ``bookOverUnder``/``fairOverUnder``: those are the
    provider's consensus, and the retained payload contains a book quoting 9.5
    under an odd-level 8.5. Pricing a book's odds against another number's line
    would be a quote that exists nowhere.
    """

    if market_key == "h2h":
        return None
    field_name = "overUnder" if market_key == "totals" else "spread"
    value = raw.get(field_name)
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def parse_main_markets(
    payload: Any, *, received_at: datetime, now: datetime | None = None,
    league_id: str = "MLB",
) -> MainMarketParseResult:
    """Every complete, pregame, two-sided MAIN full-game market in one response.

    Main means: full game, runs, and one of moneyline / combined total / run
    line. Everything else the provider returned is counted in
    ``observed_not_ingested`` so the coverage matrix can show what exists
    without this consumer pretending to support it.
    """

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SgoPayloadError("payload is not a SportsGameOdds events response")
    now = now or received_at
    result = MainMarketParseResult()

    for event in payload["data"]:
        if not isinstance(event, dict):
            continue
        result.events_seen += 1
        event_id = str(event.get("eventID") or "")
        if not event_id:
            continue
        if str(event.get("leagueID") or "") != league_id:
            result.reject(event_id, "-", "-", REJECT_MARKET_IDENTITY)
            continue

        odds = event.get("odds")
        if isinstance(odds, dict):
            for entry in odds.values():
                if isinstance(entry, dict):
                    result.observed_not_ingested[classify_odd(entry)] += 1

        if not is_pregame(event, now=now):
            result.reject(event_id, "-", "-", REJECT_NOT_PREGAME)
            continue
        result.pregame_events += 1
        if not isinstance(odds, dict):
            result.reject(event_id, "-", "-", REJECT_NO_HOME_SIDE)
            continue

        teams = event.get("teams") or {}
        home_team, away_team = teams.get("home") or {}, teams.get("away") or {}
        commence = _parse_time((event.get("status") or {}).get("startsAt"))
        home_id = str(home_team.get("teamID") or "")
        away_id = str(away_team.get("teamID") or "")
        if not (commence and home_id and away_id):
            result.reject(event_id, "-", "-", REJECT_MARKET_IDENTITY)
            continue
        home_name = str((home_team.get("names") or {}).get("long") or home_id)
        away_name = str((away_team.get("names") or {}).get("long") or away_id)

        for market_key, first, second in _main_market_pairs(odds):
            first_id, second_id = str(first.get("oddID")), str(second.get("oddID"))
            failure = (
                _verify_identity(first, first_id, second_id)
                or _verify_identity(second, second_id, first_id)
            )
            if failure is not None:
                result.reject(event_id, "-", market_key, failure)
                continue
            _collect_main_books(
                result, event=event, event_id=event_id, market_key=market_key,
                first=first, second=second, commence=commence,
                home_id=home_id, away_id=away_id,
                home_name=home_name, away_name=away_name, received_at=received_at,
            )
    return result


def _collect_main_books(
    result: MainMarketParseResult, *, event: dict[str, Any], event_id: str,
    market_key: str, first: dict[str, Any], second: dict[str, Any],
    commence: datetime, home_id: str, away_id: str,
    home_name: str, away_name: str, received_at: datetime,
) -> None:
    """One two-sided quote per bookmaker that priced BOTH sides of this market."""

    first_books = first.get("byBookmaker") or {}
    second_books = second.get("byBookmaker") or {}
    if not isinstance(first_books, dict) or not isinstance(second_books, dict):
        result.reject(event_id, "-", market_key, REJECT_BOOK_ONE_SIDED)
        return

    def label_for(entry: dict[str, Any]) -> str:
        side = str(entry.get("sideID") or "")
        if market_key == "totals":
            return "Over" if side == "over" else "Under"
        return home_name if side == "home" else away_name

    for book in sorted(set(first_books) | set(second_books)):
        raw_first, raw_second = first_books.get(book), second_books.get(book)
        if not isinstance(raw_first, dict) or not isinstance(raw_second, dict):
            result.reject(event_id, book, market_key, REJECT_BOOK_ONE_SIDED)
            continue

        sides: list[MarketSideQuote] = []
        failure: str | None = None
        for entry, raw in ((first, raw_first), (second, raw_second)):
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
            point = _book_line(raw, market_key)
            if market_key != "h2h" and point is None:
                failure = REJECT_LINE_MISSING
                break
            sides.append(MarketSideQuote(
                side_id=str(entry.get("sideID") or ""),
                odd_id=str(entry.get("oddID") or ""),
                label=label_for(entry),
                american=american,
                decimal=american_to_decimal(american),
                point=point,
                provider_updated_at=updated,
            ))
        if failure is not None:
            result.reject(event_id, book, market_key, failure)
            continue

        if market_key == "totals" and sides[0].point != sides[1].point:
            # Over 8.5 against Under 9.0 is not one market. A book whose sides
            # disagree has not offered a two-sided total, and pairing them would
            # invent a quote with a push window nobody posted.
            result.reject(event_id, book, market_key, REJECT_LINE_DISAGREES)
            continue
        if market_key == "spreads":
            combined = (sides[0].point or 0.0) + (sides[1].point or 0.0)
            if abs(combined) > 1e-9:
                # A run line's two sides must be exact opposites (-1.5 / +1.5).
                result.reject(event_id, book, market_key, REJECT_LINE_DISAGREES)
                continue

        result.quotes.append(MainMarketQuote(
            event_id=event_id,
            league_id=str(event.get("leagueID") or ""),
            commence_time=commence,
            home_team_id=home_id, away_team_id=away_id,
            home_team_name=home_name, away_team_name=away_name,
            bookmaker_key=book,
            bookmaker_title=str(raw_first.get("bookmakerName") or book)
            .replace("_", " ").title(),
            market_key=market_key,
            bet_type_id=str(first.get("betTypeID") or ""),
            period_id=str(first.get("periodID") or ""),
            stat_id=str(first.get("statID") or ""),
            stat_entity_id=str(first.get("statEntityID") or ""),
            sides=(sides[0], sides[1]),
            received_at=received_at,
        ))


__all__ = [
    "AISP_MARKET_KEY",
    "AWAY_ODD_ID",
    "COMBINED_ENTITY",
    "GAME_PERIOD",
    "MAIN_MARKET_KEYS",
    "MainMarketParseResult",
    "MainMarketQuote",
    "MarketSideQuote",
    "POINTS_STAT",
    "REJECT_LINE_DISAGREES",
    "REJECT_LINE_MISSING",
    "REJECT_NOT_MAIN_MARKET",
    "REJECT_WRONG_ENTITY",
    "classify_odd",
    "parse_main_markets",
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
