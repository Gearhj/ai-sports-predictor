"""Spread and total must be kept from the event object that was already paid for.

THE DEFECT
----------
`collect_nfl_pregame` parsed main markets and then ingested only the moneyline
pairs, so the 2026-09-17 receipt read `main_markets: {"h2h": 9, "totals": 7,
"spreads": 8}` while the database held nine books of moneyline and nothing else.
Seven books of totals and eight of spreads were bought and discarded.

The repair adds no request and no object: the same response goes through the
same franchise-map binding a second time, for the other two markets.
"""
from __future__ import annotations

import inspect
from datetime import UTC, datetime

from app.services import sgo_nfl_pregame as lane
from app.services.sgo_collection import parse_main_markets, to_market_event_dtos

OBSERVED = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
KICKOFF = "2026-09-20T17:00:00Z"


def _book(odds: str, *, line: str | None = None, field: str = "overUnder") -> dict:
    entry = {"odds": odds, "lastUpdatedAt": "2026-09-20T15:50:00.000Z", "available": True}
    if line is not None:
        entry[field] = line
    return entry


def _odd(odd_id: str, opposing: str, *, entity: str, bet_type: str, side: str,
         books: dict) -> dict:
    return {
        "oddID": odd_id, "opposingOddID": opposing, "statID": "points",
        "statEntityID": entity, "periodID": "game", "betTypeID": bet_type,
        "sideID": side, "cancelled": False, "byBookmaker": books,
    }


def _payload() -> dict:
    """One NFL event, shaped like the retained SportsGameOdds response.

    The two books disagree on the line -- 2.5 against an integer 3.0, and 44.5
    against an integer 44.0 -- which is the case that matters: an integer line
    can push and a half-point line cannot, so the two must never be merged.
    """

    odds = {
        "points-home-game-sp-home": _odd(
            "points-home-game-sp-home", "points-away-game-sp-away",
            entity="home", bet_type="sp", side="home",
            books={"draftkings": _book("-110", line="-2.5", field="spread"),
                   "fanduel": _book("-110", line="-3.0", field="spread")}),
        "points-away-game-sp-away": _odd(
            "points-away-game-sp-away", "points-home-game-sp-home",
            entity="away", bet_type="sp", side="away",
            books={"draftkings": _book("-110", line="+2.5", field="spread"),
                   "fanduel": _book("-110", line="+3.0", field="spread")}),
        "points-all-game-ou-over": _odd(
            "points-all-game-ou-over", "points-all-game-ou-under",
            entity="all", bet_type="ou", side="over",
            books={"draftkings": _book("-108", line="44.5"),
                   "fanduel": _book("-108", line="44.0")}),
        "points-all-game-ou-under": _odd(
            "points-all-game-ou-under", "points-all-game-ou-over",
            entity="all", bet_type="ou", side="under",
            books={"draftkings": _book("-112", line="44.5"),
                   "fanduel": _book("-112", line="44.0")}),
    }
    return {"data": [{
        "eventID": "sgo-nfl-week2-1", "leagueID": "NFL",
        "status": {"startsAt": KICKOFF, "started": False, "live": False,
                   "completed": False, "ended": False, "cancelled": False},
        "teams": {"home": {"teamID": "ATL_NFL", "names": {"long": "Atlanta Falcons"}},
                  "away": {"teamID": "CAR_NFL", "names": {"long": "Carolina Panthers"}}},
        "odds": odds,
    }]}


def test_the_collector_ingests_main_markets_from_the_same_response():
    """The wiring itself: both ingests happen, and only one fetch is made."""

    source = inspect.getsource(lane.collect_nfl_pregame)
    assert "to_market_event_dtos(fetch.main.quotes)" in source
    assert source.count("fetch_nfl_pregame(") == 1, "no second provider call"
    # Both ingests go through the one service, so the franchise-map binding and
    # duplicate suppression are identical for all three markets.
    assert source.count("service.ingest(") == 2


def test_spread_and_total_quotes_survive_the_conversion_to_dtos():
    parsed = parse_main_markets(_payload(), received_at=OBSERVED, now=OBSERVED, league_id="NFL")
    assert parsed is not None
    events = to_market_event_dtos(parsed.quotes)
    assert events, "the parsed spreads and totals must reach the ingest path"

    markets: dict[str, list] = {}
    for event in events:
        for book in event.bookmakers:
            for market in book.markets:
                markets.setdefault(market.key, []).extend(
                    (book.key, outcome) for outcome in market.outcomes)
    assert set(markets) == {"spreads", "totals"}, (
        "h2h is excluded here because the moneyline parse already wrote it")


def test_every_book_keeps_its_own_line_so_an_integer_still_pushes():
    parsed = parse_main_markets(_payload(), received_at=OBSERVED, now=OBSERVED, league_id="NFL")
    events = to_market_event_dtos(parsed.quotes)

    points: dict[tuple[str, str], set] = {}
    for event in events:
        for book in event.bookmakers:
            for market in book.markets:
                for outcome in market.outcomes:
                    points.setdefault((book.key, market.key), set()).add(outcome.point)

    spreads = {book: values for (book, key), values in points.items() if key == "spreads"}
    totals = {book: values for (book, key), values in points.items() if key == "totals"}
    # The integer book keeps its integer: storing a consensus against a book
    # would record a quote that book never made, and would turn a push into a
    # loss or a win.
    assert any(3.0 in values or -3.0 in values for values in spreads.values())
    assert any(2.5 in values or -2.5 in values for values in spreads.values())
    assert any(44.0 in values for values in totals.values())
    assert any(44.5 in values for values in totals.values())


def test_prices_and_provider_timestamps_are_preserved():
    parsed = parse_main_markets(_payload(), received_at=OBSERVED, now=OBSERVED, league_id="NFL")
    events = to_market_event_dtos(parsed.quotes)
    prices, stamps = set(), set()
    for event in events:
        for book in event.bookmakers:
            for market in book.markets:
                stamps.add(market.last_update)
                prices.update(outcome.price for outcome in market.outcomes)
    assert {-110, -108, -112} <= prices, "the book's own American price is kept"
    assert stamps, "each market carries the provider's own observation time"


def test_the_receipt_reports_the_extra_coverage_separately():
    source = inspect.getsource(lane.collect_nfl_pregame)
    for field in ("market_quotes", "market_duplicates", "market_events_rejected"):
        assert f'"{field}"' in source
