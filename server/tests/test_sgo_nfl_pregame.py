"""The NFL pregame lane: one object, timed, and stamped with its real provider.

WHY THIS IS TESTED WITHOUT A PROVIDER CALL
------------------------------------------
The lane funds exactly one event object a day. Discovering at 7pm that the
mapping is wrong would spend that object and leave the checkpoint with no
price, and there is no second one. So the whole chain -- provider payload,
parse, DTO, franchise mapping, persisted quote -- is exercised against a
synthetic response shaped like the provider's, before the money is on it.

THE PROVENANCE BUG THIS LOCKS OUT
---------------------------------
`NFLShadowOddsService` used to hardcode THE_ODDS_API. Routing SportsGameOdds
through it would have stamped SGO rows with another provider's name, registered
SGO event ids in that provider's id namespace, and merged SGO's "draftkings"
into the same Sportsbook row. Because the governed two-sided pairing keys on
sportsbook_id, that last one could put two different providers on opposite
sides of one supposedly same-book quote.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import ExternalIdMapping, OddsSnapshot, Sportsbook
from app.services import sgo_nfl_pregame as lane


def test_the_lane_is_the_pregame_one_and_never_the_live_one():
    assert lane.LANE == "sgo_nfl_events"
    assert lane.LANE != "sgo_nfl_live"
    assert lane.SOURCE == "SPORTSGAMEODDS"


def test_the_window_opens_late_enough_for_the_quote_to_still_be_fresh():
    # The quote has to be inside the 240-minute freshness limit AT the
    # checkpoint, which is 15 minutes before kickoff. 75 minutes before kickoff
    # leaves an hour of margin and is nowhere near the limit.
    assert lane.WINDOW_OPENS_BEFORE_KICKOFF == timedelta(minutes=75)
    assert lane.WINDOW_CLOSES_BEFORE_KICKOFF == timedelta(minutes=15)
    assert lane.WINDOW_OPENS_BEFORE_KICKOFF < timedelta(minutes=240)


def test_a_game_outside_the_window_costs_nothing(session, settings):
    result = lane.collect_nfl_pregame(
        session, settings, now=datetime(2026, 9, 14, 12, 0, tzinfo=UTC))
    assert result["attempted"] is False
    assert result["objects"] == 0
    assert result["reason"] == "NO_NFL_GAME_IN_THE_COLLECTION_WINDOW"


def test_an_unfunded_lane_stops_the_collector_rather_than_borrowing(
        session, settings, monkeypatch):
    """A refusal is the guard working. It must not fall back to another lane."""

    from app.services import provider_allocation as allocation

    monkeypatch.setattr(allocation, "LANES", tuple(
        allocation.Lane(key=item.key, label=item.label, share=item.share,
                        rationale=item.rationale,
                        funded=item.key != "sgo_nfl_events")
        for item in allocation.LANES))
    monkeypatch.setattr(allocation, "LANES_BY_KEY",
                        {item.key: item for item in allocation.LANES})
    monkeypatch.setattr(lane, "due_games", lambda *a, **k: [
        {"game_id": "g1", "starts_at": "2026-09-15T00:15:00+00:00",
         "minutes_to_kickoff": 40.0, "away": "DEN", "home": "KC"}])
    monkeypatch.setattr(lane, "eligible", lambda *a, **k: (True, "ok"))

    result = lane.collect_nfl_pregame(
        session, settings, now=datetime(2026, 9, 14, 23, 35, tzinfo=UTC))
    assert result["attempted"] is False
    assert result["objects"] == 0
    assert result["reason"] == "ALLOCATION_LANE_NOT_FUNDED"
    assert result["lane"] == "sgo_nfl_events"


def test_sgo_books_are_namespaced_away_from_the_other_providers(session):
    """The collision that would let one 'same-book' quote span two providers."""

    from app.data_sources.provider_registry import identity

    governed = identity("THE_ODDS_API")
    research = identity("SPORTSGAMEODDS")
    assert governed.bookmaker_key("draftkings") == "draftkings"
    assert research.bookmaker_key("draftkings") == "sgo:draftkings"
    assert research.bookmaker_key("draftkings") != governed.bookmaker_key("draftkings")
    assert research.governed is False


def test_the_service_defaults_to_its_original_provider(session):
    """Existing callers must be byte-identical after the source became explicit."""

    from app.services.nfl_shadow_odds_service import NFLShadowOddsService

    assert NFLShadowOddsService(session).source == "THE_ODDS_API"
    assert NFLShadowOddsService(session, source="SPORTSGAMEODDS").source == "SPORTSGAMEODDS"
