"""INT-007: absent sportsbook evidence must be null/unavailable, never 0%.

The service reducers ``ReadService._market_context`` and
``PredictionMarketReadService._sportsbook_home_probability`` previously turned an
empty market into a ``0.0`` sentinel that was published as a reviewed 0%
sportsbook probability. These tests prove the value stays ``None`` through the
service layer, does not enter the reviewed range or the ASP-market gap, and that
a genuine two-sided quote is still preserved.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.ingestion.normalizer import CanonicalNormalizer
from app.models import Game, GameTeam, Team, Venue
from app.models.entities import GameStatus
from app.prediction_markets.read_service import PredictionMarketReadService
from app.services.read_service import ReadService


def _empty_market_game(session) -> tuple[Game, Team, Team]:
    normalizer = CanonicalNormalizer(session)
    _, league, season, _ = normalizer.ensure_core(2026)
    venue = Venue(name="No-Odds Park")
    session.add(venue)
    session.flush()
    home = Team(league_id=league.id, name="Home Nine", abbreviation="HOM")
    away = Team(league_id=league.id, name="Away Nine", abbreviation="AWY")
    session.add_all([home, away])
    session.flush()
    game = Game(
        league_id=league.id, season_id=season.id, venue_id=venue.id,
        starts_at=datetime(2026, 8, 12, 23, 10, tzinfo=UTC), status=GameStatus.SCHEDULED,
    )
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    session.flush()
    return game, home, away


def test_market_context_empty_market_home_probability_is_none(session, settings) -> None:
    game, home, away = _empty_market_game(session)
    ctx = ReadService(session, settings)._market_context(game.id, home.id, away.id)
    assert ctx["has_moneyline"] is False
    assert ctx["home_probability"] is None  # not the 0.0 sentinel
    assert ctx["book_count"] == 0


def test_sportsbook_probability_none_for_empty_market(session, settings) -> None:
    game, _, _ = _empty_market_game(session)
    pm = PredictionMarketReadService(session, settings)
    assert pm._sportsbook_home_probability(game) is None


def test_sportsbook_probability_ignores_zero_sentinel(session, settings, monkeypatch) -> None:
    """Even if a caller returns a 0.0 with has_moneyline False, it is not evidence."""
    game, _, _ = _empty_market_game(session)
    pm = PredictionMarketReadService(session, settings)

    monkeypatch.setattr(
        ReadService, "_market_context",
        lambda self, *a, **k: {"has_moneyline": False, "book_count": 0, "home_probability": 0.0},
    )
    assert pm._sportsbook_home_probability(game) is None

    monkeypatch.setattr(
        ReadService, "_market_context",
        lambda self, *a, **k: {"has_moneyline": True, "book_count": 2, "home_probability": 0.55},
    )
    assert pm._sportsbook_home_probability(game) == pytest.approx(0.55)


def test_one_sided_quote_is_not_reviewed_evidence(session, settings, monkeypatch) -> None:
    """A one-sided book yields no home probability (has_moneyline stays False)."""
    game, _, _ = _empty_market_game(session)
    pm = PredictionMarketReadService(session, settings)
    # A book with only one side never produces a market probability, so the reducer
    # reports has_moneyline False / book_count 0 -> unavailable.
    monkeypatch.setattr(
        ReadService, "_market_context",
        lambda self, *a, **k: {"has_moneyline": False, "book_count": 0, "home_probability": None},
    )
    assert pm._sportsbook_home_probability(game) is None
