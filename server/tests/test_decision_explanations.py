"""Explanations that stay inside what was actually saved.

The cases here are the ones where a plausible sentence is easiest to reach for
and hardest to justify: an underdog taken on price, a selection whose stored
probability was cut by a penalty, a game with no recorded attribution, a totals
push, and a settled wager whose rationale must not move afterwards.

The underdog numbers are the real recorded Kansas City position: +195, raw
38.46%, adjusted 36.46%, break-even 33.8983%.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.explain.outcome import explain_outcome
from app.explain.pricing import break_even_probability, compare, decimal_from_american
from app.explain.wager import explain_wager

SLATE = date(2026, 9, 12)


def decision(**over):
    evidence = {
        "policy_version": "AISP_PRACTICAL_PREGAME_POLICY_V2_1",
        "tier": "TIER_B_PROBABLE_STABLE",
        "gates_passed": [
            "valid_current_forecast", "fresh_quote",
            "adjusted_edge_minimum", "adjusted_ev_positive",
        ],
        "raw_model_probability": 0.38460000000000005,
        "adjusted_probability": 0.3646,
        "market_probability": 0.32310934207445563,
        "starter_penalty": 0.02,
        "lineup_penalty": 0.0,
        "uncertainty_adjustment": 0.0,
        "feature_decomposition": {
            "components": [{"component": "sides", "status": "unknown"}],
            "model_required_missing": [],
        },
        "model_lineage": {
            "engine_id": "MLB_V18_WHOLE_GAME",
            "forecast_id": "mlb-v18-daily-910080c6",
            "evidence_cutoff_utc": "2026-09-12T17:18:15Z",
        },
    }
    evidence.update(over.pop("evidence", {}))
    values = {
        "id": "decision-1",
        "market": "MONEYLINE",
        "selection": "KC ML",
        "line": None,
        "american_odds": 195,
        "decimal_odds": 2.95,
        "model_probability": 0.3846,
        "no_vig_probability": 0.32310934207445563,
        "recommendation": "QUALIFIED_BET",
        "recommendation_reason": "V2 TIER_B_PROBABLE_STABLE: qualifies.",
        "qualification_reasons": [],
        "phase": SimpleNamespace(value="FINAL_PREGAME"),
        "recommended_at": datetime(2026, 9, 12, 19, 30, tzinfo=UTC),
        "quote_observed_at": datetime(2026, 9, 12, 19, 28, tzinfo=UTC),
        "sportsbook": "Bovada",
        "slate_date": SLATE,
        "evidence": evidence,
    }
    values.update(over)
    return SimpleNamespace(**values)


def position(stake=2.49):
    return SimpleNamespace(id="position-1", stake=stake,
                           result=SimpleNamespace(value="loss"))


MATCHUP = {"home": "BOS", "away": "KC", "label": "KC at BOS"}

#: A small mass function whose mean and tails can be checked by hand.
PMF = {str(k): v for k, v in {
    4: 0.05, 6: 0.20, 8: 0.30, 10: 0.25, 12: 0.15, 16: 0.05}.items()}


# -- the price arithmetic ----------------------------------------------------

class TestBreakEven:
    def test_plus_195_needs_33_90_percent(self):
        """The number the brief names, at full precision."""

        decimal = decimal_from_american(195)
        assert decimal == pytest.approx(2.95)
        assert break_even_probability(decimal) == pytest.approx(0.338983, abs=1e-6)

    def test_a_favourite_price_needs_more_than_half(self):
        assert break_even_probability(decimal_from_american(-160)) == \
            pytest.approx(0.615385, abs=1e-6)

    def test_american_odds_inside_the_impossible_band_are_refused(self):
        assert decimal_from_american(50) is None
        assert decimal_from_american(0) is None

    def test_rounding_happens_only_at_the_display_boundary(self):
        """Comparing rounded values would move the margin by enough to matter."""

        priced = compare(american_odds=195, decimal_odds=2.95,
                         model_probability=0.3646, raw_model_probability=0.3846)
        # Full precision internally...
        assert priced.margin_over_break_even == pytest.approx(0.025617, abs=1e-6)
        # ...rounded once on the way out.
        assert priced.as_dict()["margin_over_break_even_pts"] == pytest.approx(2.56)


# -- likely to win vs worth the price ---------------------------------------

class TestValueFramingSeparatesTheTwoQuestions:
    def test_an_underdog_is_not_called_the_likely_winner(self):
        payload = explain_wager(decision=decision(), position=position(),
                                forecast=None, matchup=MATCHUP)
        framing = payload["value_framing"]
        assert framing["kind"] == "UNDERDOG_TAKEN_ON_PRICE"
        assert framing["model_makes_it_favoured"] is False
        assert framing["clears_break_even"] is True
        assert "did NOT make this the more likely winner" in framing["headline"]
        # And nowhere does the panel claim it was.
        joined = " ".join(bullet["text"] for bullet in payload["bullets"]).lower()
        assert "most likely" not in joined
        assert "likely winner" not in joined

    def test_a_favourite_is_framed_differently(self):
        payload = explain_wager(
            decision=decision(american_odds=-160, decimal_odds=1.625,
                              evidence={"adjusted_probability": 0.6186,
                                        "raw_model_probability": 0.6386}),
            position=position(), forecast=None, matchup=MATCHUP)
        assert payload["value_framing"]["kind"] == "FAVOURED_AND_PRICED_WELL"
        assert payload["value_framing"]["model_makes_it_favoured"] is True

    def test_the_two_market_comparisons_stay_separate(self):
        """Beating the price and disagreeing with the market are different."""

        price = explain_wager(decision=decision(), position=position(),
                              forecast=None, matchup=MATCHUP)["price"]
        assert price["break_even_probability_pct"] == pytest.approx(33.90)
        assert price["no_vig_probability_pct"] == pytest.approx(32.31)
        assert price["margin_over_break_even_pts"] == pytest.approx(2.56)
        assert price["disagreement_with_market_pts"] == pytest.approx(4.15)
        assert price["margin_over_break_even_pts"] != \
            price["disagreement_with_market_pts"]


# -- what was actually saved -------------------------------------------------

class TestOnlySavedEvidence:
    def test_the_recorded_adjustment_is_named_with_its_reason(self):
        payload = explain_wager(decision=decision(), position=position(),
                                forecast=None, matchup=MATCHUP)
        text = next(b["text"] for b in payload["bullets"] if b["key"] == "probability")
        assert "38.46%" in text and "36.46%" in text
        assert "2.00 pts for starting pitcher not yet confirmed" in text

    def test_zero_penalties_are_not_listed_as_adjustments(self):
        payload = explain_wager(
            decision=decision(evidence={
                "raw_model_probability": 0.40, "adjusted_probability": 0.40,
                "starter_penalty": 0.0, "lineup_penalty": 0.0,
                "uncertainty_adjustment": 0.0,
            }),
            position=position(), forecast=None, matchup=MATCHUP)
        text = next(b["text"] for b in payload["bullets"] if b["key"] == "probability")
        assert "before adjustments" not in text

    def test_the_policy_bullet_uses_the_stored_gates(self):
        payload = explain_wager(decision=decision(), position=position(),
                                forecast=None, matchup=MATCHUP)
        text = next(b["text"] for b in payload["bullets"] if b["key"] == "policy")
        assert "AISP_PRACTICAL_PREGAME_POLICY_V2_1" in text
        assert "4 policy checks passed" in text

    def test_missing_attribution_says_so_and_claims_nothing(self):
        """The sentence the brief asks for, and none of the ones it forbids."""

        payload = explain_wager(decision=decision(), position=position(),
                                forecast=None, matchup=MATCHUP)
        support = next(b for b in payload["bullets"] if b["key"] == "support")
        assert support["attribution_available"] is False
        assert "not recorded for this forecast" in support["text"]
        joined = " ".join(b["text"] for b in payload["bullets"]).lower()
        for invented in ("wind", "bullpen advantage", "is hot", "park favours"):
            assert invented not in joined

    def test_recorded_contributions_are_used_when_they_exist(self):
        payload = explain_wager(
            decision=decision(evidence={"feature_decomposition": {
                "contributions": [
                    {"label": "starting pitching", "direction": "KC"},
                ]}}),
            position=position(), forecast=None, matchup=MATCHUP)
        support = next(b for b in payload["bullets"] if b["key"] == "support")
        assert support["attribution_available"] is True
        assert "starting pitching favoured KC" in support["text"]

    def test_a_value_without_a_direction_is_not_attribution(self):
        """Presence is not evidence that an input drove the estimate."""

        payload = explain_wager(
            decision=decision(evidence={"feature_decomposition": {
                "contributions": [{"label": "wind_speed_mph", "value": 14}]}}),
            position=position(), forecast=None, matchup=MATCHUP)
        support = next(b for b in payload["bullets"] if b["key"] == "support")
        assert support["attribution_available"] is False


# -- recorded wager vs qualifying opportunity --------------------------------

class TestRecordedVersusQualifying:
    def test_a_recorded_wager_says_so_and_names_the_stake(self):
        payload = explain_wager(decision=decision(), position=position(2.49),
                                forecast=None, matchup=MATCHUP)
        assert payload["recorded_wager"] is True
        assert "A virtual wager was recorded" in payload["status_line"]
        assert "$2.49" in payload["bullets"][0]["text"]

    def test_a_qualifying_opportunity_never_implies_a_position(self):
        payload = explain_wager(decision=decision(), position=None,
                                forecast=None, matchup=MATCHUP)
        assert payload["recorded_wager"] is False
        assert "No virtual wager was recorded" in payload["status_line"]
        assert "staked" not in payload["bullets"][0]["text"]


# -- totals ------------------------------------------------------------------

class TestTotals:
    def total_decision(self, line, side="OVER"):
        return decision(market="TOTAL", selection=f"{side} {line:g}", line=line,
                        american_odds=-110, decimal_odds=1.909090909)

    def test_an_integer_line_explains_the_push(self):
        payload = explain_outcome(
            decision=self.total_decision(8.0), position=position(),
            settlement=None, final={"home_score": 4, "away_score": 4},
            forecast_distributions={"totalRunDistribution": PMF}, matchup=MATCHUP)
        needed = next(b for b in payload["bullets"] if b["key"] == "needed")
        assert "above 8" in needed["text"]
        assert "would have pushed and returned the stake" in needed["text"]

    def test_a_half_line_mentions_no_push(self):
        payload = explain_outcome(
            decision=self.total_decision(8.5), position=position(),
            settlement=None, final={"home_score": 4, "away_score": 5},
            forecast_distributions={"totalRunDistribution": PMF}, matchup=MATCHUP)
        needed = next(b for b in payload["bullets"] if b["key"] == "needed")
        assert "pushed" not in needed["text"]

    def test_an_expected_total_above_the_line_is_not_offered_as_a_reason(self):
        """The trap the brief names.

        The forecast's mean is 9.0 and the line is 8.0, but nothing in the
        explanation may present that as evidence the Over is likely or valuable.
        """

        payload = explain_wager(decision=self.total_decision(8.0), position=position(),
                                forecast=None, matchup=MATCHUP)
        joined = " ".join(b["text"] for b in payload["bullets"]).lower()
        assert "expected total" not in joined
        assert "above the line" not in joined


# -- what happened -----------------------------------------------------------

class TestOutcomeRefusesBothLabels:
    def test_a_loss_is_not_labelled_a_model_failure(self):
        payload = explain_outcome(
            decision=decision(), position=position(), settlement=None,
            final={"home_score": 5, "away_score": 1},
            forecast_distributions={"totalRunDistribution": PMF}, matchup=MATCHUP)
        joined = (" ".join(b["text"] for b in payload["bullets"])
                  + payload["uncertainty"]["text"]).lower()
        assert "model failure" not in joined
        assert "the model was wrong" not in joined
        assert payload["result"] == "loss"

    def test_a_result_inside_the_range_is_not_labelled_bad_luck(self):
        payload = explain_outcome(
            decision=decision(), position=position(), settlement=None,
            final={"home_score": 5, "away_score": 3},
            forecast_distributions={"totalRunDistribution": PMF}, matchup=MATCHUP)
        assert payload["uncertainty"]["inside_central_range"] is True
        assert "luck" not in payload["uncertainty"]["text"].lower()
        assert "does not by itself" in payload["uncertainty"]["text"]

    def test_it_reports_the_tail_the_result_actually_fell_in(self):
        payload = explain_outcome(
            decision=decision(), position=position(), settlement=None,
            final={"home_score": 10, "away_score": 6},  # 16 total
            forecast_distributions={"totalRunDistribution": PMF}, matchup=MATCHUP)
        bullet = next(b for b in payload["bullets"] if b["key"] == "forecast_vs_result")
        assert bullet["actual_total_runs"] == 16
        # P(total >= 16) = 0.05
        assert bullet["probability_at_or_beyond_pct"] == pytest.approx(5.0)

    def test_wins_are_explained_too(self):
        payload = explain_outcome(
            decision=decision(), position=SimpleNamespace(
                id="p", stake=2.49, result=SimpleNamespace(value="win")),
            settlement=None, final={"home_score": 2, "away_score": 6},
            forecast_distributions={"totalRunDistribution": PMF}, matchup=MATCHUP)
        assert payload["result"] == "win"
        assert "won" in payload["bullets"][1]["text"]

    def test_a_push_returns_the_stake_and_says_so(self):
        payload = explain_outcome(
            decision=decision(market="TOTAL", selection="OVER 8", line=8.0),
            position=SimpleNamespace(id="p", stake=2.0,
                                     result=SimpleNamespace(value="push")),
            settlement=None, final={"home_score": 4, "away_score": 4},
            forecast_distributions={"totalRunDistribution": PMF}, matchup=MATCHUP)
        assert payload["result"] == "push"
        assert "stake was returned" in payload["bullets"][1]["text"]

    def test_no_event_evidence_says_so_rather_than_narrating_the_score(self):
        payload = explain_outcome(
            decision=decision(), position=position(), settlement=None,
            final={"home_score": 12, "away_score": 2},
            forecast_distributions={"totalRunDistribution": PMF}, matchup=MATCHUP)
        events = next(b for b in payload["bullets"] if b["key"] == "events")
        assert events["evidence_available"] is False
        assert "Deeper event analysis is unavailable" in events["text"]
        # The unavailability sentence legitimately NAMES home runs as a thing
        # that cannot be reported, so a bare substring check is the wrong test.
        # What must be absent is any CLAIM: a count, or a causal verb.
        import re
        joined = " ".join(b["text"] for b in payload["bullets"])
        assert re.search(r"\d+\s+home runs", joined) is None
        for invented in ("collapsed", "blew", "bullpen advantage", "was hot"):
            assert invented not in joined.lower()

    def test_recorded_event_evidence_is_used_when_present(self):
        payload = explain_outcome(
            decision=decision(), position=position(), settlement=None,
            final={"home_score": 12, "away_score": 2},
            forecast_distributions={"totalRunDistribution": PMF}, matchup=MATCHUP,
            event_evidence={"home_runs": 6, "starter_batters_faced": 9,
                            "relief_batters_faced": 34, "source": "TEST"})
        events = next(b for b in payload["bullets"] if b["key"] == "events")
        assert events["evidence_available"] is True
        assert "6 home runs" in events["text"]
        assert "the starter faced 9 batters" in events["text"]

    def test_nothing_is_returned_while_the_wager_is_open(self):
        payload = explain_outcome(
            decision=decision(),
            position=SimpleNamespace(id="p", stake=1.0,
                                     result=SimpleNamespace(value="pending")),
            settlement=None, final=None, forecast_distributions=None, matchup=MATCHUP)
        assert payload is None


# -- history does not move ---------------------------------------------------

class TestRationaleIsFrozen:
    def test_settlement_does_not_change_the_why_text(self):
        """The same decision explained open and settled must read identically."""

        before = explain_wager(decision=decision(), position=None,
                               forecast=None, matchup=MATCHUP)
        after = explain_wager(decision=decision(), position=position(),
                              forecast=None, matchup=MATCHUP)
        # Only the recorded-wager line differs; the reasoning is the same.
        assert [b["text"] for b in before["bullets"][1:]] == \
            [b["text"] for b in after["bullets"][1:]]
        assert before["value_framing"] == after["value_framing"]
        assert before["price"] == after["price"]

    def test_a_later_price_cannot_enter_the_explanation(self):
        """The decision's own stored price is used, never a current one."""

        payload = explain_wager(decision=decision(), position=position(),
                                forecast=None, matchup=MATCHUP)
        assert payload["price"]["american_odds"] == 195
        assert payload["details"]["quote_observed_at_utc"].startswith("2026-09-12T19:28")

    def test_details_name_the_bound_forecast_not_a_later_one(self):
        details = explain_wager(decision=decision(), position=position(),
                                forecast=None, matchup=MATCHUP)["details"]
        assert details["forecast_identity"] == "mlb-v18-daily-910080c6"
        assert "Later forecasts" in details["bound_at_decision_time"]
