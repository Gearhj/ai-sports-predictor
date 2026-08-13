from __future__ import annotations

import pytest

from app.markets.odds import (
    InvalidOddsError,
    american_implied_probability,
    american_to_decimal,
    decimal_implied_probability,
    decimal_to_american,
    detect_arbitrage,
    game_match,
    no_vig_probabilities,
    probability_to_american,
)


@pytest.mark.parametrize(("american", "decimal"), [(100, 2.0), (150, 2.5), (-110, 1.9090909), (-200, 1.5)])
def test_american_decimal_round_trip(american: int, decimal: float) -> None:
    assert american_to_decimal(american) == pytest.approx(decimal)
    assert decimal_to_american(decimal) == american


def test_implied_probability_and_no_vig() -> None:
    assert american_implied_probability(-110) == pytest.approx(0.5238095)
    assert decimal_implied_probability(2.5) == pytest.approx(0.4)
    market = no_vig_probabilities({"home": -120, "away": 110})
    assert sum(market.probabilities.values()) == pytest.approx(1.0)
    assert market.overround > 0
    assert market.raw_probabilities["home"] > market.probabilities["home"]
    assert probability_to_american(market.probabilities["home"]) < 0


def test_simple_two_way_arbitrage() -> None:
    arbitrage = detect_arbitrage({"home": 2.10, "away": 2.10}, bankroll=1000)
    assert arbitrage.exists
    assert sum(arbitrage.stakes.values()) == pytest.approx(1000)
    assert arbitrage.guaranteed_profit == pytest.approx(50, rel=0.01)
    no_arbitrage = detect_arbitrage({"home": 1.90, "away": 1.90})
    assert not no_arbitrage.exists
    assert no_arbitrage.stakes == {}


def test_market_and_team_matching() -> None:
    assert game_match("New York Yankees", "Boston Red Sox", "NY Yankees", "Boston Red Sox")
    assert not game_match("New York Yankees", "Boston Red Sox", "New York Mets", "Boston Red Sox")


@pytest.mark.parametrize("value", [0, 50, -50])
def test_invalid_american_odds(value: int) -> None:
    with pytest.raises(InvalidOddsError):
        american_to_decimal(value)
