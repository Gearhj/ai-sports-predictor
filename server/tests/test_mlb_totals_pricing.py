"""Pricing an Over/Under off V18's retained total-runs distribution.

The fixture is the real shape: integer total runs, mass summing to one, support
starting at 1 because a 0-0 MLB final does not exist.

What these pin down is the handful of places a totals page goes quietly wrong:
a push folded into a loss on an integer line, a half-run line invented a push it
cannot have, percent and fraction crossing the same boundary, and a truncated
distribution renormalised into a confident-looking number.
"""
from __future__ import annotations

import math

import pytest

from app.totals.pricing import (
    TotalsDistribution,
    TotalsPricingError,
    american_from_decimal,
    expected_net_return,
    fair_decimal_odds,
    price_line,
)

#: Deliberately small and exactly summing to 1, so every expected value below
#: can be checked by hand rather than by re-running the implementation.
MASS = {1: 0.05, 2: 0.10, 3: 0.20, 4: 0.30, 5: 0.20, 6: 0.10, 7: 0.05}


def build(mass=None) -> TotalsDistribution:
    return TotalsDistribution.from_stored(
        {str(k): v for k, v in (mass or MASS).items()},
        forecast_id="mlb-v18-daily-test", engine_id="MLB_V18_WHOLE_GAME",
        engine_version="v18", simulation_count=5000,
    )


class TestTheDistributionItself:
    def test_reads_a_stored_mass_function_keyed_by_string(self):
        distribution = build()
        assert distribution.support == (1, 7)
        assert distribution.expected_total_runs == pytest.approx(4.0)

    def test_refuses_a_distribution_that_does_not_sum_to_one(self):
        """Truncated evidence is refused, not rescaled.

        Renormalising would turn a distribution missing its tail into a
        perfectly plausible Over probability, with nothing on screen to say the
        tail was gone.
        """

        with pytest.raises(TotalsPricingError, match="truncated or double-counted"):
            build({1: 0.2, 2: 0.3})

    def test_refuses_an_absent_distribution(self):
        with pytest.raises(TotalsPricingError, match="no total-runs distribution"):
            TotalsDistribution.from_stored(
                None, forecast_id="f", engine_id="MLB_V18_WHOLE_GAME")

    def test_refuses_a_non_numeric_entry(self):
        with pytest.raises(TotalsPricingError, match="non-numeric"):
            TotalsDistribution.from_stored(
                {"four": 1.0}, forecast_id="f", engine_id="MLB_V18_WHOLE_GAME")


class TestHalfRunLines:
    """No push is reachable, so the two sides must exhaust the probability."""

    def test_prices_a_half_line_with_no_push(self):
        priced = price_line(build(), 4.5)
        # P(total > 4.5) = P(5) + P(6) + P(7) = 0.35
        assert priced.over_pct == pytest.approx(35.0)
        assert priced.under_pct == pytest.approx(65.0)
        assert priced.push_pct == 0.0
        assert priced.push_possible is False

    def test_over_and_under_sum_to_one_hundred(self):
        priced = price_line(build(), 3.5)
        assert priced.over_pct + priced.under_pct == pytest.approx(100.0)

    def test_a_line_below_the_support_is_a_certain_over(self):
        priced = price_line(build(), 0.5)
        assert priced.over_pct == pytest.approx(100.0)
        assert priced.under_pct == pytest.approx(0.0)


class TestIntegerLinesPreservePushes:
    def test_the_push_is_the_mass_exactly_on_the_line(self):
        priced = price_line(build(), 4.0)
        assert priced.push_pct == pytest.approx(30.0)
        assert priced.push_possible is True

    def test_a_push_is_not_folded_into_either_side(self):
        """The defect this exists to catch.

        Counting the push as a loss would make Under 4 look like 65%, and the
        two sides would sum to 100 while both were wrong.
        """

        priced = price_line(build(), 4.0)
        assert priced.over_pct == pytest.approx(35.0)   # 5, 6, 7
        assert priced.under_pct == pytest.approx(35.0)  # 1, 2, 3
        assert priced.over_pct + priced.under_pct + priced.push_pct == pytest.approx(100.0)

    def test_the_sides_mirror_each_other_around_the_push(self):
        priced = price_line(build(), 3.0)
        over_win, over_push, over_loss = priced.fraction("OVER")
        under_win, under_push, under_loss = priced.fraction("UNDER")
        assert over_win == pytest.approx(under_loss)
        assert over_loss == pytest.approx(under_win)
        assert over_push == under_push


class TestUnitsAreExplicit:
    def test_probabilities_are_percent_and_say_so(self):
        priced = price_line(build(), 4.5)
        assert priced.probability_units == "PERCENT"
        assert priced.as_dict()["probability_units"] == "PERCENT"
        assert 0.0 <= priced.over_pct <= 100.0

    def test_fraction_returns_fractions_for_money_arithmetic(self):
        priced = price_line(build(), 4.5)
        win, push, loss = priced.fraction("OVER")
        assert win == pytest.approx(0.35)
        assert win + push + loss == pytest.approx(1.0)

    def test_an_unknown_side_is_refused_rather_than_defaulted(self):
        with pytest.raises(TotalsPricingError, match="OVER or UNDER"):
            price_line(build(), 4.5).fraction("YES")


class TestExpectedReturn:
    def test_uses_the_stated_formula(self):
        # 0.35 * (3.0 - 1) - 0.65 = 0.70 - 0.65 = 0.05
        assert expected_net_return(0.35, 0.65, 3.0) == pytest.approx(0.05)

    def test_a_push_contributes_nothing(self):
        """Stake back is neither a win nor a loss.

        Same side, same price; the only difference is that 30% of the mass moved
        from the loss column to a push. The return must improve by exactly that
        0.30, and not by more.
        """

        without_push = expected_net_return(0.35, 0.65, 2.0)
        with_push = expected_net_return(0.35, 0.35, 2.0)
        assert with_push - without_push == pytest.approx(0.30)

    def test_a_fair_price_returns_zero(self):
        fair = fair_decimal_odds(0.35, 0.0)
        assert expected_net_return(0.35, 0.65, fair) == pytest.approx(0.0, abs=1e-12)

    def test_fair_odds_take_the_push_out_of_the_pool(self):
        """A push is not a stake at risk, so it does not dilute the fair price."""

        # 35% win, 30% push: the decided pool is 70%, so the conditional win is 0.5.
        assert fair_decimal_odds(0.35, 0.30) == pytest.approx(2.0)

    def test_refuses_a_price_that_cannot_pay(self):
        with pytest.raises(TotalsPricingError, match="greater than 1"):
            expected_net_return(0.5, 0.5, 1.0)

    def test_refuses_a_probability_that_is_not_a_fraction(self):
        """Catches a percent arriving where a fraction belongs."""

        with pytest.raises(TotalsPricingError, match="fraction"):
            expected_net_return(35.0, 65.0, 2.0)


class TestAmericanConversion:
    def test_round_trips_both_signs(self):
        assert american_from_decimal(2.5) == 150
        assert american_from_decimal(1.5) == -200
        assert american_from_decimal(2.0) == 100

    def test_an_unpriceable_input_is_none_not_zero(self):
        assert american_from_decimal(None) is None
        assert american_from_decimal(1.0) is None
        assert american_from_decimal(math.inf) is None
