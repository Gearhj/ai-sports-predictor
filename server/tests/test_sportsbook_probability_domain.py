"""Missing sportsbook evidence can never become 0%, an edge, or a consensus.

The defect these guard: ``_market_context`` returned ``0.0`` for "no two-sided
quote" beside a separate ``has_moneyline`` flag, and the prediction-markets
read service read the number without the flag -- rendering a confident
"Sportsbook 0.0%" for a game with zero reporting books.

The rule under test: a sportsbook probability exists only when a complete
two-sided observation exists AND the normalized value lies strictly inside
(0, 1). It is deliberately NOT a global "zero means missing" rule.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.markets.availability import (
    average_probability,
    is_valid_probability,
    model_edge,
    observed_probability,
)
from app.models import Forecast, Game, GameStatus, GameTeam, Team
from app.prediction_markets.read_service import PredictionMarketReadService
from app.services.read_service import ReadService

SLATE = date(2026, 8, 12)


# -- the rule itself ------------------------------------------------------


@pytest.mark.parametrize("value", [0.0, 1.0, -0.2, 1.4, None, "0.5", True, False, float("nan")])
def test_values_outside_the_open_unit_interval_are_not_probabilities(value):
    assert is_valid_probability(value) is False


@pytest.mark.parametrize("value", [0.0001, 0.5, 0.9999])
def test_values_inside_the_open_unit_interval_are_probabilities(value):
    assert is_valid_probability(value) is True


def test_a_probability_without_a_two_sided_quote_is_absent():
    # Even a perfectly plausible number is not an observation without evidence.
    assert observed_probability(0.55, has_two_sided_quote=False) is None
    assert observed_probability(0.55, has_two_sided_quote=True) == 0.55
    # And a "0.0" with the flag set is still outside the valid domain.
    assert observed_probability(0.0, has_two_sided_quote=True) is None


def test_an_average_excludes_invalid_members_rather_than_zeroing_them():
    assert average_probability([0.4, 0.6]) == pytest.approx(0.5)
    # A missing book is dropped, not counted as zero (which would give 0.3333).
    assert average_probability([0.4, 0.6, None]) == pytest.approx(0.5)
    assert average_probability([0.4, 0.6, 0.0]) == pytest.approx(0.5)
    assert average_probability([None, 0.0, 1.0]) is None
    assert average_probability([]) is None


def test_an_edge_requires_both_sides_to_exist():
    assert model_edge(0.56, 0.53) == pytest.approx(0.03)
    assert model_edge(0.56, None) is None
    # The exact failure mode: a fabricated zero market becomes a +56 pt "edge".
    assert model_edge(0.56, 0.0) is None
    assert model_edge(None, 0.53) is None


# -- the read paths -------------------------------------------------------


async def _quoteless_game(session, settings) -> Game:
    """A game that never received a sportsbook quote.

    Built rather than deleted into existence: odds snapshots are append-only.
    """

    await seed_demo_data(session, settings, SLATE)
    template = session.scalar(select(Forecast))
    seeded = session.get(Game, template.game_id)
    teams = session.scalars(select(Team).limit(2)).all()

    game = Game(
        league_id=seeded.league_id, season_id=seeded.season_id,
        venue_id=seeded.venue_id, starts_at=seeded.starts_at,
        status=GameStatus.SCHEDULED, source_mode=seeded.source_mode,
    )
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=teams[0].id, is_home=False),
        GameTeam(game_id=game.id, team_id=teams[1].id, is_home=True),
    ])
    session.add(Forecast(
        game_id=game.id, model_version_id=template.model_version_id,
        feature_version_id=template.feature_version_id, version=1,
        generated_at=template.generated_at, available_as_of=template.available_as_of,
        status=template.status, home_win_probability=0.611,
        away_win_probability=0.389, source_mode=seeded.source_mode,
    ))
    session.flush()
    return game


@pytest.mark.asyncio
async def test_market_context_reports_absence_as_none_not_zero(session, settings):
    game = await _quoteless_game(session, settings)
    home = session.scalar(select(GameTeam).where(
        GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
    away = session.scalar(select(GameTeam).where(
        GameTeam.game_id == game.id, GameTeam.is_home.is_(False)))

    context = ReadService(session, settings)._market_context(
        game.id, home.team_id, away.team_id)
    assert context["home_probability"] is None
    assert context["has_moneyline"] is False
    assert context["total"] is None
    # Coverage is a real measurement and stays a genuine zero.
    assert context["book_count"] == 0


@pytest.mark.asyncio
async def test_a_quoteless_game_card_shows_no_market_and_no_edge(session, settings):
    game = await _quoteless_game(session, settings)
    card = ReadService(session, settings).game_card(game)

    assert card.market_available is False
    assert card.market_home_probability is None
    assert card.market_total is None
    assert card.best_home_moneyline is None
    # No edge is computed against a market that does not exist.
    assert card.model_edge is None
    assert card.edge_side == "none"
    # The model forecast itself is unaffected, and zero books is a real count.
    assert card.home_win_probability == pytest.approx(61.1, abs=1e-6)
    assert card.books_reporting == 0


@pytest.mark.asyncio
async def test_market_pulse_excludes_an_unobserved_sportsbook(session, settings):
    game = await _quoteless_game(session, settings)
    detail = PredictionMarketReadService(session, settings).game_detail(game.id)
    row = detail.row

    assert row.sportsbook_home_probability is None
    # Excluded from every derived comparison rather than compared against 0.
    assert row.diff_aisp_sportsbook is None
    assert row.aisp_home_probability is not None


@pytest.mark.asyncio
async def test_a_quoted_game_still_reports_its_market(session, settings):
    """The rule must not suppress a genuine observation."""

    await seed_demo_data(session, settings, SLATE)
    template = session.scalar(select(Forecast))
    game = session.get(Game, template.game_id)
    card = ReadService(session, settings).game_card(game)

    assert card.market_available is True
    assert card.market_home_probability is not None
    assert 0.0 < card.market_home_probability < 100.0
    assert card.books_reporting > 0


@pytest.mark.asyncio
async def test_a_quoteless_game_produces_no_market_board_row(session, settings):
    game = await _quoteless_game(session, settings)
    board = ReadService(session, settings).market_board(SLATE)
    assert all(row.game_id != str(game.id) for row in board.rows)
    # Every row that does exist carries a usable consensus, never a zero one.
    for row in board.rows:
        assert row.consensus != 0
        if row.edge is not None:
            assert row.asp_fair is not None
