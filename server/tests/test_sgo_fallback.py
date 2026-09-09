"""The rules that make a fallback quote safe to price a governed decision.

Each test pins a rule that, if it broke, would let a wrong or unearned price
reach a decision: a pair assembled from mismatched sides, a live game's price
treated as pregame, a fallback outbidding the provider it exists to stand in
for, or a stale price looking fresh because we happened to fetch it just now.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.data_sources import provider_registry as registry
from app.data_sources.sgo_parser import (
    AWAY_ODD_ID,
    HOME_ODD_ID,
    REJECT_BOOK_ONE_SIDED,
    REJECT_MARKET_IDENTITY,
    REJECT_NOT_PREGAME,
    REJECT_PAIR_MISMATCH,
    REJECT_PRICE_INVALID,
    REJECT_TIMESTAMP_MISSING,
    REJECT_UNAVAILABLE,
    parse_american,
    parse_events,
)

NOW = datetime(2026, 9, 9, 22, 0, tzinfo=UTC)
KICKOFF = (NOW + timedelta(hours=2)).isoformat().replace("+00:00", "Z")


def _side(odd_id, opposing, price, updated="2026-09-09T21:55:00.000Z",
          available=True, book="draftkings"):
    return {
        "oddID": odd_id, "opposingOddID": opposing, "betTypeID": "ml", "periodID": "game",
        "marketName": "Moneyline",
        "byBookmaker": {book: {"odds": price, "lastUpdatedAt": updated, "available": available}},
    }


def _payload():
    event = {
        "eventID": "evt-1", "leagueID": "MLB", "sportID": "BASEBALL", "type": "match",
        "status": {"started": False, "live": False, "completed": False, "ended": False,
                   "cancelled": False, "startsAt": KICKOFF},
        "teams": {
            "home": {"teamID": "BALTIMORE_ORIOLES_MLB",
                     "names": {"long": "Baltimore Orioles", "short": "BAL"}},
            "away": {"teamID": "CLEVELAND_GUARDIANS_MLB",
                     "names": {"long": "Cleveland Guardians", "short": "CLE"}},
        },
        "odds": {HOME_ODD_ID: _side(HOME_ODD_ID, AWAY_ODD_ID, "-118"),
                 AWAY_ODD_ID: _side(AWAY_ODD_ID, HOME_ODD_ID, "+102")},
    }
    return {"success": True, "data": [event]}


def _parse(payload):
    return parse_events(payload, received_at=NOW, now=NOW, league_id="MLB")


# -- the happy path ----------------------------------------------------------

def test_a_clean_pregame_pair_is_admitted() -> None:
    result = _parse(_payload())
    assert len(result.pairs) == 1 and not result.rejected
    pair = result.pairs[0]
    assert pair.home.american == -118 and pair.away.american == 102
    assert pair.bookmaker_key == "draftkings"
    assert pair.home_team_name == "Baltimore Orioles"


# -- American conversion -----------------------------------------------------

@pytest.mark.parametrize("american,decimal", [
    (100, 2.0), (145, 2.45), (2500, 26.0), (9000, 91.0),
    (-110, 1.9090909090909092), (-150, 1.6666666666666667),
    (-10000, 1.01), (-100000, 1.001),
])
def test_american_converts_deterministically(american, decimal) -> None:
    from app.markets.odds import american_to_decimal

    assert american_to_decimal(american) == pytest.approx(decimal, rel=1e-12)


@pytest.mark.parametrize("value", ["", "abc", "+", None, True, "0", "50", "-99", 99, 3.5])
def test_a_price_that_is_not_american_odds_is_refused_not_coerced(value) -> None:
    """American odds do not exist between -100 and +100. A number in that range
    is a different quantity wearing the same clothes."""
    assert parse_american(value) is None


def test_the_original_american_price_survives_alongside_the_decimal() -> None:
    pair = _parse(_payload()).pairs[0]
    assert pair.home.american == -118
    assert pair.home.decimal == pytest.approx(1 + 100 / 118)


# -- paired-side integrity ---------------------------------------------------

def test_a_pairing_that_does_not_reconcile_both_ways_is_refused() -> None:
    payload = _payload()
    payload["data"][0]["odds"][HOME_ODD_ID]["opposingOddID"] = "points-away-game-ml-other"
    result = _parse(payload)
    assert not result.pairs
    assert result.rejected["evt-1"]["-"] == REJECT_PAIR_MISMATCH


def test_a_market_that_is_not_a_full_game_moneyline_is_refused() -> None:
    payload = _payload()
    payload["data"][0]["odds"][HOME_ODD_ID]["periodID"] = "1i"
    assert _parse(payload).rejected["evt-1"]["-"] == REJECT_MARKET_IDENTITY


def test_a_book_that_priced_one_side_is_never_completed_from_another_book() -> None:
    payload = _payload()
    payload["data"][0]["odds"][AWAY_ODD_ID]["byBookmaker"] = {
        "fanduel": {"odds": "+102", "lastUpdatedAt": "2026-09-09T21:55:00.000Z",
                    "available": True}}
    result = _parse(payload)
    assert not result.pairs
    assert result.rejected["evt-1"] == {"draftkings": REJECT_BOOK_ONE_SIDED,
                                        "fanduel": REJECT_BOOK_ONE_SIDED}


def test_an_unavailable_side_is_not_a_quote() -> None:
    payload = _payload()
    payload["data"][0]["odds"][AWAY_ODD_ID]["byBookmaker"]["draftkings"]["available"] = False
    assert _parse(payload).rejected["evt-1"]["draftkings"] == REJECT_UNAVAILABLE


def test_a_side_without_a_provider_timestamp_is_refused() -> None:
    payload = _payload()
    del payload["data"][0]["odds"][HOME_ODD_ID]["byBookmaker"]["draftkings"]["lastUpdatedAt"]
    assert _parse(payload).rejected["evt-1"]["draftkings"] == REJECT_TIMESTAMP_MISSING


def test_an_unparseable_price_is_refused() -> None:
    payload = _payload()
    payload["data"][0]["odds"][HOME_ODD_ID]["byBookmaker"]["draftkings"]["odds"] = "even"
    assert _parse(payload).rejected["evt-1"]["draftkings"] == REJECT_PRICE_INVALID


# -- pregame -----------------------------------------------------------------

@pytest.mark.parametrize("flag", ["started", "live", "completed", "ended", "cancelled"])
def test_the_provider_saying_the_game_began_is_enough_to_refuse(flag) -> None:
    payload = _payload()
    payload["data"][0]["status"][flag] = True
    assert _parse(payload).rejected["evt-1"]["-"] == REJECT_NOT_PREGAME


def test_a_past_start_time_is_refused_even_if_every_flag_says_otherwise() -> None:
    """A status flag can lag. A quote is only usable before the game exists."""
    payload = _payload()
    payload["data"][0]["status"]["startsAt"] = (
        NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    assert _parse(payload).rejected["evt-1"]["-"] == REJECT_NOT_PREGAME


# -- timestamps --------------------------------------------------------------

def test_a_pair_is_only_as_fresh_as_its_stalest_half() -> None:
    payload = _payload()
    payload["data"][0]["odds"][AWAY_ODD_ID]["byBookmaker"]["draftkings"]["lastUpdatedAt"] = (
        "2026-09-09T21:51:00.000Z")
    pair = _parse(payload).pairs[0]
    assert pair.pair_freshness_at == datetime(2026, 9, 9, 21, 51, tzinfo=UTC)
    assert pair.timestamp_skew_seconds == 240.0
    assert pair.home.provider_updated_at != pair.away.provider_updated_at


def test_simultaneity_is_never_claimed_and_both_stamps_are_kept() -> None:
    payload = _payload()
    payload["data"][0]["odds"][AWAY_ODD_ID]["byBookmaker"]["draftkings"]["lastUpdatedAt"] = (
        "2026-09-09T21:51:00.000Z")
    provenance = _parse(payload).pairs[0].provenance()
    assert provenance["simultaneity_claimed"] is False
    assert provenance["timestamp_skew_seconds"] == 240.0
    assert provenance["home_provider_updated_at"] != provenance["away_provider_updated_at"]
    assert provenance["received_at"] == NOW.isoformat()
    # Dated by the older stamp, never by when we happened to fetch it.
    assert provenance["pair_freshness_at"] < provenance["received_at"]


# -- source governance -------------------------------------------------------

def test_the_fallback_is_governed_only_while_switched_on_and_in_window() -> None:
    september = datetime(2026, 9, 15, tzinfo=UTC)
    assert registry.governed_sources(
        now=september, fallback_enabled=False) == registry.GOVERNED_SOURCES
    armed = registry.governed_sources(now=september, fallback_enabled=True)
    assert armed == registry.GOVERNED_SOURCES | {registry.SPORTSGAMEODDS}


def test_the_fallback_expires_by_date_with_nobody_remembering() -> None:
    last_day = datetime(2026, 9, 30, 23, 59, tzinfo=UTC)
    october = datetime(2026, 10, 1, 0, 1, tzinfo=UTC)
    assert registry.SPORTSGAMEODDS in registry.governed_sources(
        now=last_day, fallback_enabled=True)
    assert registry.governed_sources(
        now=october, fallback_enabled=True) == registry.GOVERNED_SOURCES


def test_the_permanent_governed_set_never_gains_the_fallback() -> None:
    assert registry.GOVERNED_SOURCES == frozenset({registry.THE_ODDS_API})


def test_the_fallback_is_never_relabelled_as_the_primary() -> None:
    identity = registry.identity(registry.SPORTSGAMEODDS)
    assert identity.key == "SPORTSGAMEODDS" and identity.display_name == "SportsGameOdds"
    assert identity.bookmaker_key("draftkings") == "sgo:draftkings"
    assert registry.identity(registry.THE_ODDS_API).bookmaker_key("draftkings") == "draftkings"


def test_a_fallback_price_never_outbids_the_provider_it_stands_in_for() -> None:
    """Otherwise it is a ninth bookmaker, not a fallback."""
    rows = [("THE_ODDS_API", 1.90), ("SPORTSGAMEODDS", 2.60)]
    assert registry.prefer_primary(rows, lambda row: row[0]) == [("THE_ODDS_API", 1.90)]
    only = [("SPORTSGAMEODDS", 2.60)]
    assert registry.prefer_primary(only, lambda row: row[0]) == only


# -- routing -----------------------------------------------------------------

def test_every_routing_precondition_names_its_own_refusal(settings) -> None:
    from app.services.sgo_collection import eligible

    september = datetime(2026, 9, 15, tzinfo=UTC)
    armed = settings.model_copy(update={
        "sportsgameodds_enabled": True, "sportsgameodds_api_key": "x" * 32,
        "sportsgameodds_base_url": "https://api.sportsgameodds.com/v2"})
    assert eligible(armed, now=september) == (True, "FALLBACK_ELIGIBLE")
    assert eligible(armed.model_copy(update={"sportsgameodds_enabled": False}),
                    now=september)[1] == "FALLBACK_DISABLED"
    assert eligible(armed.model_copy(update={"sportsgameodds_api_key": ""}),
                    now=september)[1] == "FALLBACK_NO_CREDENTIAL"
    assert eligible(armed.model_copy(update={"sportsgameodds_base_url": ""}),
                    now=september)[1] == "FALLBACK_NO_BASE_URL"
    assert eligible(armed, now=datetime(2026, 10, 1, tzinfo=UTC))[1].startswith(
        "FALLBACK_WINDOW_CLOSED")


def test_the_dto_conversion_keeps_both_sides_of_one_book_together() -> None:
    from app.services.sgo_collection import to_event_dtos

    pairs = _parse(_payload()).pairs
    events = to_event_dtos(pairs)
    assert len(events) == 1
    book = events[0].bookmakers[0]
    # Namespacing happens at the write boundary, not here.
    assert book.key == "draftkings"
    assert len(book.markets) == 1 and book.markets[0].key == "h2h"
    assert {outcome.price for outcome in book.markets[0].outcomes} == {-118, 102}
    # The market carries the pair's freshness, which becomes observed_at.
    assert book.markets[0].last_update == pairs[0].pair_freshness_at
