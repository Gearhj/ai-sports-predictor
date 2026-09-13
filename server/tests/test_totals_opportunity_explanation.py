"""What a totals opportunity explanation is allowed to say.

The numbers in this file are the live 2026-09-13 SD at SF Over 7.5: 49.42% at
+105 on BetMGM, against a V18 expected total of 8.03 runs. That example is the
awkward one on purpose. The model does not make Over the likelier side, the
expected total sits comfortably above the line, and the price still clears -- so
every wrong sentence this panel could produce is available to it.

Four of those wrong sentences are pinned below:

  * calling a research listing a wager, a decision or a stake;
  * offering "the expected total is 8.03, the line is 7.5" as a reason;
  * implying the model likes the side because it clears its price;
  * quietly borrowing the moneyline policy's penalties or thresholds, which are
    not established for totals and would present an unvalidated adjustment as a
    settled rule.
"""
from __future__ import annotations

import re

from app.explain.opportunity import SCHEMA_VERSION, explain_totals_opportunity

FORECAST = {
    "expected_total_runs": 8.0296,
    "simulation_count": 5000,
    "forecast_id": "mlb-v18-daily-2026-09-13-sdsf",
    "engine_id": "MLB_V18_WHOLE_GAME",
    "engine_version": "mlb-v18-whole-game-a6e7e99",
}

PRICE = {
    "american_odds": 105,
    "decimal_odds": 2.05,
    "sportsbook": "Betmgm",
    "observed_at": "2026-09-13T17:39:00Z",
    "model_implied_net_return_per_unit": 0.0131,
}


def build(**over):
    kwargs = dict(
        matchup="SD at SF",
        side="OVER",
        line=7.5,
        line_source="RECORDED_SPORTSBOOK_LINE",
        win_probability_pct=49.42,
        push_probability_pct=0.0,
        loss_probability_pct=50.58,
        price=PRICE,
        forecast=FORECAST,
        model_fair_american=102,
    )
    kwargs.update(over)
    return explain_totals_opportunity(**kwargs)


def text_of(explanation) -> str:
    joined = " ".join(bullet["text"] for bullet in explanation["bullets"])
    return f"{explanation['status_line']} {explanation['value_framing']['headline']} {joined}"


def test_nothing_in_it_claims_a_wager_a_decision_or_a_stake():
    explanation = build()
    assert explanation["recorded_wager"] is False
    assert explanation["schema_version"] == SCHEMA_VERSION
    assert "No virtual wager, decision or stake exists" in explanation["status_line"]
    assert explanation["details"]["decision_id"] is None
    assert explanation["details"]["position_id"] is None
    body = text_of(explanation).lower()
    for forbidden in ("we bet", "our position", "staked", "wagered", "recommended"):
        assert forbidden not in body


def test_a_clearing_underdog_is_value_at_the_price_not_a_prediction():
    explanation = build()
    framing = explanation["value_framing"]
    assert framing["kind"] == "LESS_LIKELY_BUT_PRICED_WELL"
    assert framing["model_makes_it_favoured"] is False
    assert "does NOT make this the more likely side" in framing["headline"]
    assert "value at the price, not a prediction" in framing["headline"]


def test_the_likelier_side_is_not_described_as_value_when_the_price_is_short():
    # Over 7.5 at a price that needs more than the model gives it.
    explanation = build(
        win_probability_pct=58.0,
        loss_probability_pct=42.0,
        price={**PRICE, "american_odds": -180, "decimal_odds": 1.5556,
               "model_implied_net_return_per_unit": -0.098},
    )
    framing = explanation["value_framing"]
    assert framing["kind"] == "LIKELIER_BUT_PRICE_SHORT"
    assert framing["model_makes_it_favoured"] is True
    assert "does not clear" in framing["headline"]


def test_the_expected_total_is_context_and_says_it_is_not_a_reason():
    explanation = build()
    context = next(b for b in explanation["bullets"] if b["key"] == "context")
    assert context["statement_kind"] == "SECONDARY_CONTEXT"
    assert "not a reason to take either side" in context["text"]
    # And it is last among the substantive claims, not the argument's opening.
    keys = [bullet["key"] for bullet in explanation["bullets"]]
    assert keys.index("context") > keys.index("price")

    # The sentence the panel must never produce: mean above line, therefore Over.
    body = text_of(explanation)
    assert not re.search(
        r"(above|higher than|exceeds)[^.]{0,40}line[^.]{0,40}"
        r"(so|therefore|which means|favou?rs)",
        body, re.IGNORECASE,
    )


def test_a_half_run_line_states_that_no_push_is_possible():
    explanation = build()
    probability = next(b for b in explanation["bullets"] if b["key"] == "probability")
    assert "no push" in probability["text"]
    assert "49.42%" in probability["text"]


def test_an_integer_line_explains_the_push_instead_of_hiding_it():
    explanation = build(
        line=8.0, win_probability_pct=44.1, push_probability_pct=11.3,
        loss_probability_pct=44.6,
    )
    probability = next(b for b in explanation["bullets"] if b["key"] == "probability")
    assert "11.30% chance the total lands exactly on 8" in probability["text"]
    assert "stake is returned" in probability["text"]


def test_break_even_and_expected_return_come_from_the_offered_price():
    explanation = build()
    price_bullet = next(b for b in explanation["bullets"] if b["key"] == "price")
    # +105 needs 1/2.05 = 48.78%.
    assert "48.78%" in price_bullet["text"]
    assert "+105" in price_bullet["text"]
    returns = next(b for b in explanation["bullets"] if b["key"] == "return")
    assert "+1.3 cents per $1" in returns["text"]
    assert "Betmgm" in returns["text"]


def test_no_price_means_no_value_claim_at_all():
    explanation = build(price=None)
    assert explanation["value_framing"]["kind"] == "UNKNOWN"
    assert explanation["value_framing"]["clears_break_even"] is None
    price_bullet = next(b for b in explanation["bullets"] if b["key"] == "price")
    assert "no return can be estimated" in price_bullet["text"].lower()
    assert not any(bullet["key"] == "return" for bullet in explanation["bullets"])


def test_no_moneyline_policy_rule_is_borrowed():
    explanation = build()
    details = explanation["details"]
    assert details["policy_version"] is None
    assert details["staking_version"] is None
    assert details["tier"] is None
    assert details["gates_passed"] == []
    assert details["gates_passed_count"] == 0
    note = details["bound_at_decision_time"]
    assert "no totals threshold, probability penalty or staking rule is applied" in note
    # The model probability shown is V18's own, unadjusted.
    assert explanation["price"]["model_probability_pct"] == 49.42
    assert explanation["price"]["raw_model_probability_pct"] is None


def test_the_panel_is_bound_to_the_line_and_forecast_it_was_built_from():
    explanation = build()
    details = explanation["details"]
    assert details["line"] == 7.5
    assert details["line_source"] == "RECORDED_SPORTSBOOK_LINE"
    assert details["forecast_identity"] == FORECAST["forecast_id"]
    assert details["engine_id"] == "MLB_V18_WHOLE_GAME"
    assert details["quote_observed_at_utc"] == PRICE["observed_at"]
    assert details["sportsbook"] == "Betmgm"
    assert explanation["selection"] == "Over 7.5"


def test_no_causal_story_is_invented_about_the_game():
    explanation = build()
    support = next(b for b in explanation["bullets"] if b["key"] == "support")
    assert support["attribution_available"] is False
    body = text_of(explanation)
    # The specific inventions the brief named, in their causal form.
    assert not re.search(
        r"(wind|bullpen|park|rotation|weather|offen[cs]e)[^.]{0,40}"
        r"(favou?rs|drove|caused|because|explains)",
        body, re.IGNORECASE,
    )
    assert not re.search(r"\b(hot|cold|due|streaking)\b", body, re.IGNORECASE)
