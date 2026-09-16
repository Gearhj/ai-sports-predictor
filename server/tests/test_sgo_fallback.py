"""The rules that make a fallback quote safe to price a governed decision.

Each test pins a rule that, if it broke, would let a wrong or unearned price
reach a decision: a pair assembled from mismatched sides, a live game's price
treated as pregame, a fallback outbidding the provider it exists to stand in
for, or a stale price looking fresh because we happened to fetch it just now.
"""
from __future__ import annotations

import json
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


# -- object budget, cooldown and ledger accounting ---------------------------


def _ledger_row(session, *, requested_at, objects, status="SUCCEEDED"):
    """A completed request pair, exactly as the collector writes it."""
    from app.data_sources.provider_registry import SPORTSGAMEODDS
    from app.models import ProviderRequestLedger

    reserved = ProviderRequestLedger(
        provider=SPORTSGAMEODDS, endpoint_key="sgo_mlb_events", status="RESERVED",
        requested_at=requested_at, request_fingerprint=f"fp-{requested_at.isoformat()}-{objects}",
        controlled_live_request=True, source_mode="LIVE", response_metadata={})
    session.add(reserved)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=SPORTSGAMEODDS, endpoint_key="sgo_mlb_events", status=status,
        requested_at=requested_at, completed_at=requested_at,
        reservation_id=reserved.id, request_fingerprint=reserved.request_fingerprint,
        records_received=objects, controlled_live_request=True, source_mode="LIVE",
        response_metadata={}))
    session.flush()
    return reserved


def test_requests_and_billable_objects_are_counted_separately(session) -> None:
    """One ledger row is one request. The sum of records_received is the object
    spend. Conflating them is how a 2,500-object allowance gets misread as
    2,500 requests."""
    from app.services.sgo_collection import object_budget

    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    _ledger_row(session, requested_at=now - timedelta(hours=2), objects=10)
    _ledger_row(session, requested_at=now - timedelta(hours=1), objects=7)
    budget = object_budget(session, now=now)
    assert budget.requests == 2
    assert budget.objects_used == 17


def test_the_reserve_is_never_spendable(session) -> None:
    from app.services.sgo_collection import (
        FREE_TIER_OBJECTS, OBJECT_RESERVE, object_budget)

    budget = object_budget(session, now=datetime(2026, 9, 20, 12, 0, tzinfo=UTC))
    assert budget.spendable == FREE_TIER_OBJECTS - OBJECT_RESERVE
    assert budget.may_spend(budget.spendable)[0] is True
    allowed, refusal = budget.may_spend(budget.spendable + 1)
    assert allowed is False and "reserve" in refusal


def test_the_guard_fails_closed_before_a_request_that_would_overrun(session) -> None:
    from app.services.sgo_collection import (
        FREE_TIER_OBJECTS, OBJECT_RESERVE, object_budget)

    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    # Spend everything except five objects of headroom.
    _ledger_row(session, requested_at=now - timedelta(days=1),
                objects=FREE_TIER_OBJECTS - OBJECT_RESERVE - 5)
    budget = object_budget(session, now=now)
    assert budget.spendable == 5
    assert budget.may_spend(5)[0] is True
    assert budget.may_spend(6)[0] is False


def test_a_request_the_provider_never_counted_is_charged_the_worst_case(session) -> None:
    """Under-counting an allowance is how an allowance gets exceeded."""
    from app.services.sgo_collection import MAX_EVENTS_PER_FETCH, object_budget

    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    _ledger_row(session, requested_at=now - timedelta(hours=1), objects=None)
    assert object_budget(session, now=now).objects_used == MAX_EVENTS_PER_FETCH


def test_objects_from_a_previous_billing_month_are_not_counted(session) -> None:
    """The provider bills a UTC calendar month and refills on the 1st."""
    from app.services.sgo_collection import object_budget

    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    _ledger_row(session, requested_at=datetime(2026, 8, 31, 23, 59, tzinfo=UTC), objects=500)
    _ledger_row(session, requested_at=now - timedelta(days=1), objects=9)
    budget = object_budget(session, now=now)
    assert budget.objects_used == 9
    assert budget.billing_month == "2026-09"


def test_the_cooldown_counts_from_the_last_attempt_not_the_last_success(session) -> None:
    """A failing provider must not be hammered by a five-minute scheduler tick."""
    from app.services.sgo_collection import (
        MIN_SECONDS_BETWEEN_FETCHES, seconds_since_last_fetch)

    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    assert seconds_since_last_fetch(session, now=now) is None
    _ledger_row(session, requested_at=now - timedelta(seconds=120), objects=0, status="FAILED")
    since = seconds_since_last_fetch(session, now=now)
    assert since == pytest.approx(120, abs=1)
    assert since < MIN_SECONDS_BETWEEN_FETCHES


def test_a_ledger_row_carries_the_filters_and_never_the_credential(session) -> None:
    from app.services.sgo_collection import object_budget, record_request

    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    params = {"leagueID": "MLB", "limit": "6", "startsAfter": "2026-09-20T12:00:00Z",
              "startsBefore": "2026-09-20T20:00:00Z", "finalized": "false",
              "oddsPresent": "true"}
    row = record_request(session, requested_at=now, params=params,
                         budget=object_budget(session, now=now))
    shape = row.response_metadata["request_shape"]
    assert shape["filters"] == params
    assert shape["billable_unit"] == "event object"
    blob = json.dumps(row.response_metadata)
    for forbidden in ("apikey", "api_key", "x-api-key", "authorization"):
        assert forbidden not in blob.lower()


def test_the_terminal_row_records_objects_and_remaining_allowance(session) -> None:
    from app.data_sources.provider_registry import SPORTSGAMEODDS
    from app.models import ProviderRequestLedger
    from app.services.sgo_collection import (
        FREE_TIER_OBJECTS, OBJECT_RESERVE, complete_request, object_budget, record_request)
    from sqlalchemy import select

    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    budget = object_budget(session, now=now)
    row = record_request(session, requested_at=now, params={"leagueID": "MLB"}, budget=budget)
    complete_request(session, row, objects=8, succeeded=True, budget=budget)
    terminal = session.scalars(
        select(ProviderRequestLedger).where(
            ProviderRequestLedger.provider == SPORTSGAMEODDS,
            ProviderRequestLedger.status == "SUCCEEDED")).all()
    assert len(terminal) == 1
    assert terminal[0].records_received == 8
    # Objects, not credits: this provider has no credit concept.
    assert terminal[0].quota_cost is None
    assert terminal[0].quota_remaining == FREE_TIER_OBJECTS - OBJECT_RESERVE - 8


def test_a_failed_request_spends_no_objects_but_still_starts_the_cooldown(session) -> None:
    from app.services.sgo_collection import (
        complete_request, object_budget, record_request, seconds_since_last_fetch)

    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    budget = object_budget(session, now=now)
    row = record_request(session, requested_at=now, params={"leagueID": "MLB"}, budget=budget)
    complete_request(session, row, objects=0, succeeded=False, budget=budget,
                     error="ReadTimeout")
    assert object_budget(session, now=now).objects_used == 0
    assert seconds_since_last_fetch(session, now=now) == pytest.approx(0, abs=1)


# -- cutoff coverage: presence is not usability ------------------------------


class _Snap:
    def __init__(self, source, observed_at):
        self.source = source
        self.observed_at = observed_at


class _Ident:
    def __init__(self, value):
        self.id = value


def _row(source, minutes_old, outcome, book):
    """A (market, outcome, snapshot, sportsbook) row as the read path shapes it."""
    return (_Ident("m1"), _Ident(outcome),
            _Snap(source, datetime.now(UTC) - timedelta(minutes=minutes_old)),
            _Ident(book))


def test_a_one_sided_primary_row_does_not_suppress_a_fallback_pair() -> None:
    """One side prices nothing, so it must not cost a game its coverage."""
    from app.services.read_service import _prefer_primary_per_market

    rows = [
        _row("THE_ODDS_API", 5, "home", "dk"),           # one side only
        _row("SPORTSGAMEODDS", 3, "home", "sgo:dk"),
        _row("SPORTSGAMEODDS", 3, "away", "sgo:dk"),
    ]
    kept = _prefer_primary_per_market(rows)
    assert {row[2].source for row in kept} == {"THE_ODDS_API", "SPORTSGAMEODDS"}


def test_a_stale_two_sided_primary_does_not_suppress_a_fresh_fallback_pair() -> None:
    from app.services.quote_coverage import MAX_QUOTE_STALENESS_MINUTES
    from app.services.read_service import _prefer_primary_per_market

    stale = MAX_QUOTE_STALENESS_MINUTES + 30
    rows = [
        _row("THE_ODDS_API", stale, "home", "dk"),
        _row("THE_ODDS_API", stale, "away", "dk"),
        _row("SPORTSGAMEODDS", 3, "home", "sgo:dk"),
        _row("SPORTSGAMEODDS", 3, "away", "sgo:dk"),
    ]
    kept = _prefer_primary_per_market(rows)
    assert "SPORTSGAMEODDS" in {row[2].source for row in kept}


def test_a_usable_primary_pair_still_suppresses_the_fallback() -> None:
    """The fallback must not become a ninth bookmaker when the primary works."""
    from app.services.read_service import _prefer_primary_per_market

    rows = [
        _row("THE_ODDS_API", 10, "home", "dk"),
        _row("THE_ODDS_API", 10, "away", "dk"),
        _row("SPORTSGAMEODDS", 3, "home", "sgo:dk"),
        _row("SPORTSGAMEODDS", 3, "away", "sgo:dk"),
    ]
    kept = _prefer_primary_per_market(rows)
    assert {row[2].source for row in kept} == {"THE_ODDS_API"}


def test_two_primary_books_with_one_side_each_are_not_a_usable_pair() -> None:
    """A pair is one book's two sides. Two halves from two books is neither."""
    from app.services.read_service import _prefer_primary_per_market

    rows = [
        _row("THE_ODDS_API", 5, "home", "dk"),
        _row("THE_ODDS_API", 5, "away", "fd"),
        _row("SPORTSGAMEODDS", 3, "home", "sgo:dk"),
        _row("SPORTSGAMEODDS", 3, "away", "sgo:dk"),
    ]
    kept = _prefer_primary_per_market(rows)
    assert "SPORTSGAMEODDS" in {row[2].source for row in kept}


def test_prefer_primary_without_a_usability_test_keeps_its_old_behaviour() -> None:
    from app.data_sources import provider_registry as reg

    rows = [("THE_ODDS_API", 1), ("SPORTSGAMEODDS", 2)]
    assert reg.prefer_primary(rows, lambda r: r[0]) == [("THE_ODDS_API", 1)]
    assert reg.prefer_primary(
        rows, lambda r: r[0], primary_usable=lambda _: False) == rows


def _coverage(age_at_cutoff_minutes):
    """An EventCoverage whose best quote is this old AT the cutoff."""
    from app.services.quote_coverage import (
        STATE_COMPLETE, EventCoverage, QuoteObservation)

    cutoff = datetime(2026, 9, 9, 23, 0, tzinfo=UTC)
    quote = QuoteObservation(
        observed_at=cutoff - timedelta(minutes=age_at_cutoff_minutes),
        sportsbook_id="sb-1", book="dk", home_american=-110, away_american=-110)
    return EventCoverage(
        game_id="g", matchup="AAA @ BBB", starts_at=cutoff + timedelta(minutes=15),
        cutoff=cutoff, state=STATE_COMPLETE, cause=None, best_quote=quote,
        qualifying=[quote], out_of_window=[], partial_observations=0,
        next_action_at=None, next_action=None)


def test_a_quote_that_expires_before_the_decision_is_not_coverage() -> None:
    """The gate reads the clock when the freeze job runs, not at the cutoff.

    236.8 minutes old at a cutoff is inside the 240-minute window and outside it
    four minutes later -- which is how a game reads as covered right up until it
    silently is not.
    """
    from app.services.quote_coverage import (
        DECISION_LATENCY_MINUTES, MAX_QUOTE_STALENESS_MINUTES)

    edge = MAX_QUOTE_STALENESS_MINUTES - DECISION_LATENCY_MINUTES
    safe = _coverage(edge)
    assert safe.covered and safe.usable_through_cutoff and not safe.needs_refresh

    expiring = _coverage(edge + 1)
    assert expiring.covered, "still inside the window at the cutoff"
    assert not expiring.usable_through_cutoff
    assert expiring.needs_refresh, "and therefore still worth refreshing"


def test_the_staleness_threshold_itself_is_untouched() -> None:
    from app.services.quote_coverage import (
        FINAL_PREGAME_CUTOFF_MINUTES, MAX_QUOTE_STALENESS_MINUTES)

    assert MAX_QUOTE_STALENESS_MINUTES == 240
    assert FINAL_PREGAME_CUTOFF_MINUTES == 15


# -- opening adjustment ------------------------------------------------------


def _apply_opening(session, objects=24, day=None):
    from app.services.sgo_collection import apply_opening_adjustment

    return apply_opening_adjustment(
        session,
        objects=objects,
        occurred_at=day or datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
        evidence={"total_objects": objects, "fetches": [{"objects": objects}]},
    )


def test_the_opening_adjustment_is_counted_as_objects(session) -> None:
    from app.services.sgo_collection import object_budget

    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    assert object_budget(session, now=now).objects_used == 0
    _apply_opening(session)
    budget = object_budget(session, now=now)
    assert budget.objects_used == 24
    assert budget.spendable == budget.allowance - budget.reserve - 24


def test_the_opening_adjustment_is_not_counted_as_a_request(session) -> None:
    """It records objects spent, not a call anyone made."""
    from app.services.sgo_collection import object_budget

    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    _apply_opening(session)
    assert object_budget(session, now=now).requests == 0


def test_the_opening_adjustment_does_not_hold_off_the_next_fetch(session) -> None:
    """A cooldown exists to space out real requests. An adjustment is not one."""
    from app.services.sgo_collection import seconds_since_last_fetch

    _apply_opening(session)
    assert seconds_since_last_fetch(
        session, now=datetime(2026, 9, 9, 12, 30, tzinfo=UTC)) is None


def test_applying_the_opening_adjustment_twice_changes_nothing(session) -> None:
    from app.services.sgo_collection import object_budget

    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    first = _apply_opening(session)
    assert first["applied"] is True
    second = _apply_opening(session)
    assert second["applied"] is False and second["reason"] == "ALREADY_RECORDED"
    assert second["ledger_id"] == first["ledger_id"]
    assert object_budget(session, now=now).objects_used == 24


def test_an_adjustment_never_subtracts_from_the_objects_used(session) -> None:
    """The regression this pins actually happened.

    Excluding adjustments from the request count while still counting them in
    the with-counts total made the unknown-object term negative, and a negative
    unknown SUBTRACTS: 24 recorded objects were reported as 4, and the guard
    would have permitted twenty objects that were already spent.
    """
    from app.services.sgo_collection import object_budget

    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    _apply_opening(session)
    budget = object_budget(session, now=now)
    assert budget.objects_used == 24, "an adjustment must add, never subtract"
    assert budget.objects_used >= 0 and budget.spendable <= budget.allowance


def test_the_adjustment_is_counted_in_its_own_billing_month_only(session) -> None:
    """Dated to the day the objects were spent, so it clears at that month's reset."""
    from app.services.sgo_collection import object_budget

    day = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    _apply_opening(session, day=day)
    same_month = datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)
    next_month = datetime(2026, 10, 1, 0, 0, 1, tzinfo=UTC)
    assert object_budget(session, now=same_month).objects_used == 24
    assert object_budget(session, now=next_month).objects_used == 0


# --- what a bounded allowance may be spent on -------------------------------

def test_a_game_whose_freshness_window_has_not_opened_is_not_requested(monkeypatch):
    """A quote fetched eight hours early cannot still be fresh at the cutoff.

    Before this guard the fallback asked for every uncovered game on the board,
    including tomorrow's. Those games stay permanently "needs_refresh" -- the
    quote ages out of its own window long before anyone decides on it -- so the
    next tick asks again, and the next. A bounded allowance drains on games it
    cannot help while the games it could help go unfunded.
    """

    from datetime import UTC, datetime, timedelta

    from app.services import sgo_collection
    from app.services.quote_coverage import MAX_QUOTE_STALENESS_MINUTES

    now = datetime(2026, 9, 11, 18, 0, tzinfo=UTC)
    inside = now + timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES - 30)
    outside = now + timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES + 90)
    events = [
        {"cutoff_utc": inside.isoformat(), "covered": False, "needs_refresh": True},
        {"cutoff_utc": outside.isoformat(), "covered": False, "needs_refresh": True},
        {"cutoff_utc": outside.isoformat(), "covered": False, "needs_refresh": True},
    ]

    class _Coverage:
        def __init__(self, *args, **kwargs) -> None: ...
        def daily_report(self, **kwargs):
            return {"events": events}

    captured: dict[str, object] = {}

    def _record(session, *, requested_at, params, budget, job_run_id=None):
        captured["params"] = params
        raise RuntimeError("stop before the request")

    monkeypatch.setattr("app.services.quote_coverage.QuoteCoverageService", _Coverage)
    monkeypatch.setattr(sgo_collection, "record_request", _record)
    monkeypatch.setattr(sgo_collection, "eligible", lambda settings, now=None: (True, "OK"))
    monkeypatch.setattr(sgo_collection, "seconds_since_last_fetch", lambda session, now=None: None)

    class _Budget:
        def may_spend(self, objects):
            captured["wanted"] = objects
            return True, None

        def as_dict(self):
            return {}

    monkeypatch.setattr(sgo_collection, "object_budget", lambda session, now=None: _Budget())

    with pytest.raises(RuntimeError):
        sgo_collection.collect_for_open_cutoffs(object(), object(), now=now)

    # Exactly the one game inside its window -- not the two that cannot be helped.
    assert captured["wanted"] == 1


def test_when_nothing_is_inside_its_window_the_tick_says_so_and_spends_nothing(monkeypatch):
    """A quiet tick must be legible: "too early" is not "nothing to do"."""

    from datetime import UTC, datetime, timedelta

    from app.services import sgo_collection
    from app.services.quote_coverage import MAX_QUOTE_STALENESS_MINUTES

    now = datetime(2026, 9, 11, 18, 0, tzinfo=UTC)
    far = now + timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES + 120)

    class _Coverage:
        def __init__(self, *args, **kwargs) -> None: ...
        def daily_report(self, **kwargs):
            return {"events": [
                {"cutoff_utc": far.isoformat(), "covered": False, "needs_refresh": True},
            ]}

    monkeypatch.setattr("app.services.quote_coverage.QuoteCoverageService", _Coverage)
    monkeypatch.setattr(sgo_collection, "eligible", lambda settings, now=None: (True, "OK"))
    monkeypatch.setattr(sgo_collection, "seconds_since_last_fetch", lambda session, now=None: None)

    result = sgo_collection.collect_for_open_cutoffs(object(), object(), now=now)
    assert result["attempted"] is False
    assert result["objects"] == 0
    assert result["reason"] == "NO_GAME_INSIDE_ITS_FRESHNESS_WINDOW_NEEDS_A_QUOTE"
    assert result["uncovered_but_window_not_open"] == 1
