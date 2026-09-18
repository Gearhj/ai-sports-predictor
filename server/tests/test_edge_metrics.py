"""Each recorded edge figure is named for what it is (CHC @ CIN, 2026-09-18)."""

from __future__ import annotations

import pytest

from app.services.edge_metrics import controlling_advantage, edge_metrics

CHC_EVIDENCE = {
    "policy_version": "AISP_PRACTICAL_PREGAME_POLICY_V2_1", "raw_model_probability": 0.5718,
    "market_probability": 0.5506567324679315, "raw_edge": 0.021143, "uncertainty_adjustment": 0.0,
    "starter_penalty": 0.02, "lineup_penalty": 0.0, "adjusted_probability": 0.5518,
    "adjusted_edge": 0.001143, "adjusted_expected_value": -0.036392,
}


def test_mlb_separates_the_policy_advantage_from_the_break_even_figure():
    m = edge_metrics("MLB", -134, 0.5718, 0.5506567324679315, -0.0008495726495726386,
                     -0.0014835820895522933, CHC_EVIDENCE)
    assert m["advantage_vs_no_vig"]["adjusted"] == pytest.approx(0.001143)        # +0.11 pts: the policy's test
    assert m["advantage_vs_break_even"]["raw"] == pytest.approx(0.5718 - 134 / 234)  # -0.085 pts: the stored column
    assert m["offered"]["break_even_probability"] == pytest.approx(0.57265, abs=1e-5)
    assert m["return_at_offered_price"]["adjusted"] == pytest.approx(-0.036392)
    assert [t["metric"] for t in m["tests"]] == ["advantage_vs_no_vig.adjusted", "return_at_offered_price.adjusted"]
    assert [t["passed"] for t in m["tests"]] == [False, False]
    assert controlling_advantage(m) == pytest.approx(0.001143)


def test_the_identity_holds_return_equals_scaled_advantage_plus_vig_cost():
    m = edge_metrics("MLB", -134, 0.5718, 0.5506567324679315, None, None, CHC_EVIDENCE)
    d = m["offered"]["decimal"]
    scaled = m["advantage_vs_no_vig"]["raw"] * d
    assert m["return_at_offered_price"]["raw"] == pytest.approx(scaled + m["vig_cost_at_offered_price"], abs=1e-6)


def test_nfl_controls_on_tie_aware_return_and_has_no_no_vig_test():
    evidence = {"best": {"win_probability": 0.7053, "tie_probability": 0.0297, "expected_value": 0.035128}}
    m = edge_metrics("NFL", -235, 0.7053, None, None, 0.035128, evidence)
    assert [t["metric"] for t in m["tests"]] == ["return_at_offered_price.raw"]
    assert m["tests"][0]["passed"] is True and m["tests"][0]["threshold"] == 0.02
    assert m["advantage_vs_no_vig"]["raw"] is None


def test_cfb_uses_the_recorded_terms():
    evidence = {"selection": {"win_probability": 0.6, "terms": {"no_vig_probability": 0.55, "edge": 0.05,
                                                                "expected_return_per_dollar": 0.04}}}
    m = edge_metrics("CFB", -120, 0.6, None, None, 0.04, evidence)
    assert [t["passed"] for t in m["tests"]] == [True, True]


def test_an_unpriced_decision_has_no_edge_figures():
    assert edge_metrics("MLB", None, None, None, None, None, {}) is None
