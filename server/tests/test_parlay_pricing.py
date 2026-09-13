"""Combining legs: pushes preserved, one book, distinct games, independence declared.

The interesting cases here are the ones where a plausible shortcut gives a
different answer than the correct arithmetic:

*   "Every leg wins" is NOT "the ticket pays". They differ by exactly the
    push mass, and the difference flatters the ticket if the two are conflated.
*   A pushed leg is removed, not lost. A ticket with one push and one win pays.
*   Expected return has to be taken at the price actually being offered, which
    is not the product of the legs when a real combined quote exists.
"""
from __future__ import annotations

import pytest

from app.parlay.pricing import (
    PRICE_ESTIMATED_FROM_LEGS,
    PRICE_RECORDED_COMBINED,
    Leg,
    ParlayPricingError,
    price_ticket,
)


def leg(
    game="g1", market="MONEYLINE", selection="AAA ML",
    p_win=0.6, p_push=0.0, book="Bovada", odds=2.0, line=None,
) -> Leg:
    return Leg(
        game_id=game, market=market, selection=selection,
        p_win=p_win, p_push=p_push, p_loss=1.0 - p_win - p_push,
        decimal_odds=odds, sportsbook=book,
        forecast_id="mlb-v18-daily-test", engine_id="MLB_V18_WHOLE_GAME",
        starts_at="2026-09-14T23:10:00+00:00", line=line,
    )


class TestALegIsWellFormed:
    def test_probabilities_must_sum_to_one(self):
        with pytest.raises(ParlayPricingError, match="sum to"):
            Leg(game_id="g", market="MONEYLINE", selection="x",
                p_win=0.6, p_push=0.0, p_loss=0.3, decimal_odds=2.0,
                sportsbook="b", forecast_id="f", engine_id="e")

    def test_a_leg_must_name_the_forecast_behind_its_probability(self):
        """An unattributed probability is not evidence.

        Without this the ticket could carry a number nobody can trace to a
        forecast, which is exactly what makes a recorded ticket unauditable
        later.
        """

        with pytest.raises(ParlayPricingError, match="names no forecast"):
            Leg(game_id="g", market="MONEYLINE", selection="x",
                p_win=0.6, p_push=0.0, p_loss=0.4, decimal_odds=2.0,
                sportsbook="b", forecast_id="", engine_id="e")

    def test_a_price_that_cannot_pay_is_refused(self):
        with pytest.raises(ParlayPricingError, match="must exceed 1"):
            leg(odds=1.0)


class TestTicketShapeRules:
    def test_two_selections_from_one_game_are_refused(self):
        """Correlated by construction; the independent product would be wrong.

        An Over and that game's favourite are the same event seen twice. This
        release excludes same-game tickets outright rather than approximating
        them.
        """

        with pytest.raises(ParlayPricingError, match="same game"):
            price_ticket([
                leg(game="g1", selection="AAA ML"),
                leg(game="g1", market="TOTAL", selection="OVER 8.5", line=8.5),
            ])

    def test_mixing_sportsbooks_is_refused(self):
        with pytest.raises(ParlayPricingError, match="one sportsbook"):
            price_ticket([leg(game="g1", book="Bovada"),
                          leg(game="g2", book="Fanduel")])

    def test_a_single_leg_is_not_a_ticket(self):
        with pytest.raises(ParlayPricingError, match="at least two legs"):
            price_ticket([leg()])


class TestNoPushes:
    def test_all_win_and_pays_agree_when_no_leg_can_push(self):
        """With no push mass the two probabilities are the same number.

        That they are equal here is what makes them differing elsewhere
        meaningful rather than an artefact.
        """

        price = price_ticket([leg(game="g1", p_win=0.6), leg(game="g2", p_win=0.5)])
        assert price.all_legs_win_pct == pytest.approx(30.0)
        assert price.ticket_pays_pct == pytest.approx(30.0)
        assert price.stake_returned_pct == 0.0
        assert price.ticket_loses_pct == pytest.approx(70.0)
        assert price.push_possible is False

    def test_combined_odds_are_the_product_and_labelled_as_an_estimate(self):
        price = price_ticket([leg(game="g1", odds=2.0), leg(game="g2", odds=2.5)])
        assert price.combined_decimal_odds == pytest.approx(5.0)
        assert price.price_provenance == PRICE_ESTIMATED_FROM_LEGS

    def test_expected_return_matches_the_closed_form(self):
        # E[multiple] = (0.6*2.0) * (0.5*2.5) = 1.2 * 1.25 = 1.5 -> +0.5
        price = price_ticket([
            leg(game="g1", p_win=0.6, odds=2.0),
            leg(game="g2", p_win=0.5, odds=2.5),
        ])
        assert price.expected_net_return_per_unit == pytest.approx(0.5)

    def test_a_fairly_priced_ticket_has_zero_expected_return(self):
        price = price_ticket([
            leg(game="g1", p_win=0.5, odds=2.0),
            leg(game="g2", p_win=0.25, odds=4.0),
        ])
        assert price.expected_net_return_per_unit == pytest.approx(0.0, abs=1e-9)


class TestPushesAreKept:
    def test_the_ticket_can_pay_without_every_leg_winning(self):
        """One push and one win pays. The pushed leg is removed, not lost."""

        price = price_ticket([
            leg(game="g1", market="TOTAL", selection="OVER 8", line=8.0,
                p_win=0.45, p_push=0.10, odds=2.0),
            leg(game="g2", p_win=0.50, odds=2.0),
        ])
        # every leg wins: 0.45 * 0.50 = 22.5%
        assert price.all_legs_win_pct == pytest.approx(22.5)
        # no loss: (0.45+0.10) * 0.50 = 27.5%; all push: 0.10 * 0 = 0
        assert price.ticket_pays_pct == pytest.approx(27.5)
        assert price.push_possible is True

    def test_pays_strictly_exceeds_all_win_whenever_a_push_is_possible(self):
        price = price_ticket([
            leg(game="g1", p_win=0.45, p_push=0.10, odds=2.0),
            leg(game="g2", p_win=0.50, p_push=0.08, odds=2.0),
        ])
        assert price.ticket_pays_pct > price.all_legs_win_pct

    def test_every_leg_pushing_returns_the_stake_and_is_not_a_win(self):
        price = price_ticket([
            leg(game="g1", p_win=0.45, p_push=0.10, odds=2.0),
            leg(game="g2", p_win=0.50, p_push=0.20, odds=2.0),
        ])
        assert price.stake_returned_pct == pytest.approx(2.0)  # 0.10 * 0.20
        # and it is excluded from "pays", which means "returns MORE than stake"
        assert price.ticket_pays_pct == pytest.approx(
            (0.55 * 0.70 - 0.10 * 0.20) * 100.0)

    def test_the_four_outcomes_partition_the_probability(self):
        price = price_ticket([
            leg(game="g1", p_win=0.45, p_push=0.10, odds=2.0),
            leg(game="g2", p_win=0.50, p_push=0.08, odds=2.0),
        ])
        total = (price.ticket_pays_pct + price.stake_returned_pct
                 + price.ticket_loses_pct)
        assert total == pytest.approx(100.0)

    def test_a_push_improves_expected_return_over_the_same_loss_mass(self):
        """Moving mass from loss to push must help, and by a bounded amount."""

        without = price_ticket([
            leg(game="g1", p_win=0.45, p_push=0.0, odds=2.0),
            leg(game="g2", p_win=0.50, odds=2.0),
        ])
        with_push = price_ticket([
            leg(game="g1", p_win=0.45, p_push=0.10, odds=2.0),
            leg(game="g2", p_win=0.50, odds=2.0),
        ])
        assert with_push.expected_net_return_per_unit > \
            without.expected_net_return_per_unit


class TestRecordedCombinedQuote:
    def test_a_recorded_quote_is_labelled_differently_from_an_estimate(self):
        price = price_ticket(
            [leg(game="g1", odds=2.0), leg(game="g2", odds=2.0)],
            recorded_combined_decimal_odds=3.6,
        )
        assert price.price_provenance == PRICE_RECORDED_COMBINED
        assert price.combined_decimal_odds == pytest.approx(3.6)

    def test_expected_return_is_taken_at_the_offered_price(self):
        """A book that pays 3.6 where the legs multiply to 4.0 pays less.

        Reporting the estimate's expected return against a recorded quote would
        describe a ticket nobody is offering.
        """

        estimated = price_ticket([leg(game="g1", odds=2.0), leg(game="g2", odds=2.0)])
        recorded = price_ticket(
            [leg(game="g1", odds=2.0), leg(game="g2", odds=2.0)],
            recorded_combined_decimal_odds=3.6,
        )
        assert recorded.expected_net_return_per_unit < \
            estimated.expected_net_return_per_unit
        # 0.6*0.6 = 0.36 of the time it pays 3.6 -> 1.296 -> -0.704
        assert recorded.expected_net_return_per_unit == pytest.approx(0.296, abs=1e-6)


class TestWhatTheTicketSaysAboutItself:
    def test_independence_is_declared_on_every_ticket(self):
        payload = price_ticket([leg(game="g1"), leg(game="g2")]).as_dict()
        assert "independent" in payload["independence_note"]
        assert "correlation" in payload["independence_note"]

    def test_each_probability_carries_its_own_definition(self):
        payload = price_ticket([leg(game="g1"), leg(game="g2")]).as_dict()
        definitions = payload["probability_definitions"]
        assert "pushes" not in definitions["all_legs_win_probability_pct"].lower() \
            or "No leg pushes" in definitions["all_legs_win_probability_pct"]
        assert "more than the stake" in definitions["ticket_pays_probability_pct"]
        assert "returned" in definitions["stake_returned_probability_pct"]

    def test_units_are_stated(self):
        payload = price_ticket([leg(game="g1"), leg(game="g2")]).as_dict()
        assert payload["probability_units"] == "PERCENT"


class TestThreeLegs:
    def test_three_distinct_games_price_as_the_product(self):
        price = price_ticket([
            leg(game="g1", p_win=0.5, odds=2.0),
            leg(game="g2", p_win=0.5, odds=2.0),
            leg(game="g3", p_win=0.5, odds=2.0),
        ])
        assert price.leg_count == 3
        assert price.all_legs_win_pct == pytest.approx(12.5)
        assert price.combined_decimal_odds == pytest.approx(8.0)
        assert price.expected_net_return_per_unit == pytest.approx(0.0, abs=1e-9)

    def test_a_repeated_game_among_three_is_still_refused(self):
        with pytest.raises(ParlayPricingError, match="same game"):
            price_ticket([leg(game="g1"), leg(game="g2"), leg(game="g1",
                                                              selection="OVER 8.5")])
