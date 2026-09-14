"""The proposed NFL paper policy: its tie arithmetic, and its refusal to act.

The numbers below are the real stored DEN at KC evidence, because the whole
point of the tie treatment is that it changes the answer on a real case rather
than in principle:

    NFL_M1_XGBOOST forecast 2d9d0197...  pAway 0.5753  pHome 0.3817  pTie 0.0430
    draftkings 2026-08-29T22:08:45-04:00  DEN +124  KC -148

Two separate things are checked. First, that a tie is a push and not a loss.
Second, that the no-vig comparison and the offered-price EV stay different
numbers -- conflating them is the specific error the policy exists to prevent.
"""
from __future__ import annotations

from app.services import nfl_paper_policy as policy
from app.services.nfl_paper_policy import (
    TiedMarket, american_to_decimal, kelly_fraction, model_conditional,
    moneyline_ev, no_vig_conditional, proposed_stake,
)

P_AWAY, P_HOME, P_TIE = 0.5753, 0.3817, 0.0430
DEN_AMERICAN, KC_AMERICAN = 124, -148


def den() -> TiedMarket:
    return TiedMarket(win_probability=P_AWAY, tie_probability=P_TIE,
                      decimal_odds=american_to_decimal(DEN_AMERICAN))


def test_the_policy_is_activated_for_virtual_research_only():
    assert policy.ACTIVATED is True
    assert policy.AUTHORITY["consumer_paper_authority"] == "GRANTED"
    # Consumer paper authority and producer authority are different things.
    # Granting the first must never appear to have granted the second.
    assert policy.AUTHORITY["producer_classification"] == "RESEARCH_SHADOW"
    assert policy.AUTHORITY["real_money"] == "NEVER"


def test_the_policy_name_is_not_an_mlb_policy_name():
    # A shared name is how an MLB evaluator would silently pick up NFL games.
    assert "NFL" in policy.POLICY_VERSION
    assert "PRACTICAL_PREGAME" not in policy.POLICY_VERSION


def test_exposure_ceilings_bind_above_the_per_position_cap():
    assert policy.POSITION_CAP_UNCALIBRATED < policy.MAX_SAME_GAME_PERCENTAGE
    assert policy.MAX_SAME_GAME_PERCENTAGE < policy.MAX_DAILY_PERCENTAGE


def test_a_tie_refunds_the_stake_rather_than_losing_it():
    market = den()
    assert market.loss_probability == P_HOME
    # EV = pWin x (d - 1) - pLoss. The tie contributes nothing at all.
    expected = P_AWAY * (american_to_decimal(DEN_AMERICAN) - 1.0) - P_HOME
    assert moneyline_ev(market) == expected


def test_treating_the_tie_as_a_loss_moves_ev_by_the_whole_tie_mass():
    """The error is larger than the policy's entire qualification threshold."""

    market = den()
    as_push = moneyline_ev(market)
    as_loss = P_AWAY * (market.decimal_odds - 1.0) - (P_HOME + P_TIE)
    assert round(as_push - as_loss, 6) == P_TIE
    assert P_TIE > policy.MIN_ADJUSTED_EDGE


def test_the_no_vig_edge_is_not_the_offered_price_edge():
    away_novig, home_novig = no_vig_conditional(
        american_to_decimal(DEN_AMERICAN), american_to_decimal(KC_AMERICAN))
    assert round(away_novig + home_novig, 9) == 1.0
    # The model on the market's own conditional basis -- NOT its raw pAway.
    conditional = model_conditional(P_AWAY, P_TIE)
    assert conditional > P_AWAY  # removing tie mass raises the conditional

    no_vig_edge = conditional - away_novig
    offered_ev = moneyline_ev(den())
    # Both are positive here, and they are emphatically not the same number.
    assert no_vig_edge > 0 and offered_ev > 0
    assert abs(offered_ev - no_vig_edge) > 0.10


def test_the_conditional_basis_is_what_makes_the_comparison_honest():
    # Comparing the raw unconditional pAway against a conditional no-vig price
    # understates the model by exactly the tie mass it forgot to remove.
    away_novig, _ = no_vig_conditional(
        american_to_decimal(DEN_AMERICAN), american_to_decimal(KC_AMERICAN))
    honest = model_conditional(P_AWAY, P_TIE) - away_novig
    naive = P_AWAY - away_novig
    assert honest > naive


def test_no_baseball_penalty_is_inherited():
    """The MLB starter and lineup calibrations must not appear under a new name."""

    gaps = {gap["gap"] for gap in policy.UNCALIBRATED_GAPS}
    assert "INACTIVES_AND_INJURY_STATUS" in gaps
    text = " ".join(str(gap) for gap in policy.UNCALIBRATED_GAPS).lower()
    # The absence is stated. An invented number would be worse than the gap.
    assert "not transferable" in text
    assert not hasattr(policy, "STARTER_PENALTY_FRACTION")
    assert not hasattr(policy, "LINEUP_PENALTY_FRACTION")


def test_a_stale_price_blocks_activation_and_a_missing_feed_does_not():
    blocking = {gap["gap"] for gap in policy.UNCALIBRATED_GAPS
                if gap["blocks_activation"]}
    assert blocking == {"PRICE_FRESHNESS"}


def test_quarter_kelly_conditions_on_the_game_being_decided():
    """The push term is the one that gets dropped, so it is pinned here."""

    market = den()
    profit = market.decimal_odds - 1.0
    decisive = market.win_probability + market.loss_probability
    assert decisive < 1.0  # because a tie is possible

    quarter = kelly_fraction(market) * policy.KELLY_FRACTION
    assert round(quarter, 4) == 0.0699

    # Omitting `decisive` -- the easy mistake -- understates the stake.
    naive = (moneyline_ev(market) / profit) * policy.KELLY_FRACTION
    assert naive < quarter
    assert round(naive, 4) == 0.0669


def test_the_uncalibrated_cap_is_the_one_that_applies():
    market = den()
    # Full Kelly on this evidence is large; the cap is what actually binds.
    assert kelly_fraction(market) > policy.POSITION_CAP_UNCALIBRATED
    staked = proposed_stake(market)
    assert staked == round(policy.POSITION_CAP_UNCALIBRATED * policy.STARTING_BANKROLL, 2)
    assert staked == 5.00
    # And the calibrated cap is not reachable while the gap is open.
    assert policy.POSITION_CAP_IF_CALIBRATED > policy.POSITION_CAP_UNCALIBRATED


def test_a_negative_edge_stakes_nothing():
    # A heavy favourite priced shorter than the model's own probability.
    market = TiedMarket(win_probability=0.40, tie_probability=0.03,
                        decimal_odds=american_to_decimal(-200))
    assert moneyline_ev(market) < 0
    assert kelly_fraction(market) == 0.0
    assert proposed_stake(market) == 0.0
