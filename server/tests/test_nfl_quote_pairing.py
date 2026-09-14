"""A pair is the unit, and its older leg decides whether it is fresh.

TWO CORRECTIONS THIS PINS
-------------------------
A single side is not a price. Qualification compares a model probability
against a market, and a market has two sides -- from the SAME provider, book
and market row, so the terms match by construction rather than by assumption.
Pairing across books or providers assembles a market nobody was offering.

And an API fetch is not a fresh quote. Each leg carries the BOOK's own
`observed_at`; the two legs can be far apart, so a pair is exactly as fresh as
its staler half. Ageing by the newer leg would let a stale market pass because
one side happened to tick recently, and ageing by AISP's retrieval time would
mean nothing about the market at all.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models import (Market, MarketOutcome, MarketType, OddsSnapshot,
                        Sportsbook)
from app.services.nfl_paper_decision import MAX_QUOTE_AGE_MINUTES, paired_quotes

NOW = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
GAME = uuid.uuid4()


def _book(session, key):
    book = Sportsbook(key=key, name=key, region="us", active=True)
    session.add(book)
    session.flush()
    return book


def _market(session, game_id):
    market = Market(game_id=game_id, market_type=MarketType.MONEYLINE,
                    period="full_game")
    session.add(market)
    session.flush()
    return market


def _side(session, market, book, side, american, observed_at, source):
    outcome = session.query(MarketOutcome).filter_by(
        market_id=market.id, outcome_key=f"{side}:None").one_or_none()
    if outcome is None:
        outcome = MarketOutcome(market_id=market.id, outcome_key=f"{side}:None",
                                label=side)
        session.add(outcome)
        session.flush()
    session.add(OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=book.id,
        observed_at=observed_at, available_as_of=NOW,
        american_odds=american, decimal_odds=2.0, implied_probability=0.5,
        source=source, source_event_id="e1", source_mode="LIVE",
        raw_payload={}, payload_checksum="c" * 64))
    session.flush()


def test_one_side_alone_is_not_a_pair(session):
    market = _market(session, GAME)
    book = _book(session, "sgo:draftkings")
    _side(session, market, book, "AWAY", 124, NOW, "SPORTSGAMEODDS")
    assert paired_quotes(session, GAME) == []


def test_a_pair_is_aged_by_its_older_leg(session):
    market = _market(session, GAME)
    book = _book(session, "sgo:draftkings")
    # One leg ticked a minute ago; the other is four hours stale.
    _side(session, market, book, "AWAY", 124,
          NOW - timedelta(minutes=1), "SPORTSGAMEODDS")
    _side(session, market, book, "HOME", -148,
          NOW - timedelta(minutes=300), "SPORTSGAMEODDS")

    pair = paired_quotes(session, GAME)[0]
    assert pair["older_leg"] == "HOME"
    assert pair["legs_apart_minutes"] == 299.0
    age = (NOW - datetime.fromisoformat(pair["pair_observed_at"])).total_seconds() / 60
    # The pair is 300 minutes old, not 1. The newer leg does not rescue it.
    assert age == 300.0
    assert age > MAX_QUOTE_AGE_MINUTES


def test_a_pair_within_the_limit_on_both_legs_is_fresh(session):
    market = _market(session, GAME)
    book = _book(session, "sgo:draftkings")
    _side(session, market, book, "AWAY", 124,
          NOW - timedelta(minutes=20), "SPORTSGAMEODDS")
    _side(session, market, book, "HOME", -148,
          NOW - timedelta(minutes=45), "SPORTSGAMEODDS")

    pair = paired_quotes(session, GAME)[0]
    age = (NOW - datetime.fromisoformat(pair["pair_observed_at"])).total_seconds() / 60
    assert age == 45.0
    assert age <= MAX_QUOTE_AGE_MINUTES


def test_sides_from_two_different_books_do_not_form_a_pair(session):
    market = _market(session, GAME)
    dk = _book(session, "sgo:draftkings")
    fd = _book(session, "sgo:fanduel")
    _side(session, market, dk, "AWAY", 124, NOW, "SPORTSGAMEODDS")
    _side(session, market, fd, "HOME", -148, NOW, "SPORTSGAMEODDS")
    # Each book has one side. Neither is a market.
    assert paired_quotes(session, GAME) == []


def test_sides_from_two_providers_do_not_form_a_pair(session):
    market = _market(session, GAME)
    book = _book(session, "draftkings")
    _side(session, market, book, "AWAY", 124, NOW, "SPORTSGAMEODDS")
    _side(session, market, book, "HOME", -148, NOW, "THE_ODDS_API")
    # Same book key, different providers: not one offered market.
    assert paired_quotes(session, GAME) == []


def test_the_newest_snapshot_per_side_is_the_one_that_counts(session):
    market = _market(session, GAME)
    book = _book(session, "sgo:draftkings")
    _side(session, market, book, "AWAY", 110,
          NOW - timedelta(minutes=90), "SPORTSGAMEODDS")
    _side(session, market, book, "AWAY", 124,
          NOW - timedelta(minutes=10), "SPORTSGAMEODDS")
    _side(session, market, book, "HOME", -148,
          NOW - timedelta(minutes=12), "SPORTSGAMEODDS")

    pair = paired_quotes(session, GAME)[0]
    assert pair["away_american"] == 124      # the newer away price
    assert pair["older_leg"] == "HOME"       # 12 minutes beats 10
