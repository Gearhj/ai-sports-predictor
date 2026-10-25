"""Each recorded edge figure is named for what it is (CHC @ CIN, 2026-09-18)."""

from __future__ import annotations

import pytest

from app.services.edge_metrics import controlling_value, edge_metrics

CHC_EVIDENCE = {
    "policy_version": "AISP_PRACTICAL_PREGAME_POLICY_V2_1", "raw_model_probability": 0.5718,
    "market_probability": 0.5506567324679315, "raw_edge": 0.021143, "uncertainty_adjustment": 0.0,
    "starter_penalty": 0.02, "lineup_penalty": 0.0, "adjusted_probability": 0.5518,
    "adjusted_edge": 0.001143, "adjusted_expected_value": -0.036392,
}


def _fields(m):
    return {f["key"]: f for f in m["fields"]}


def test_mlb_shows_the_recorded_policy_adjusted_edge_and_labels_the_stored_columns():
    m = edge_metrics("MLB", -134, 0.5718, 0.5506567324679315, -0.0008495726495726386,
                     -0.0014835820895522933, CHC_EVIDENCE)
    f = _fields(m)
    assert f["recorded_policy_adjusted_edge"]["label"] == "Recorded policy-adjusted edge"
    assert f["recorded_policy_adjusted_edge"]["value"] == pytest.approx(0.001143)          # +0.11 pts
    # The stored columns keep their own meanings and are never called the policy edge.
    assert f["unadjusted_edge_vs_break_even"]["value"] == pytest.approx(-0.00084957, abs=1e-8)  # -0.085 pts
    assert f["unadjusted_edge_vs_break_even"]["label"].startswith("Unadjusted model probability minus")
    assert f["unadjusted_model_return"]["value"] == pytest.approx(-0.00148358, abs=1e-8)
    assert "policy-adjusted" not in f["unadjusted_edge_vs_break_even"]["label"]
    assert "policy-adjusted" not in f["unadjusted_model_return"]["label"]
    assert m["offered"]["break_even_probability"] == pytest.approx(0.57265, abs=1e-5)
    assert [t["metric"] for t in m["tests"]] == ["recorded_policy_adjusted_edge", "recorded_policy_adjusted_return"]
    assert [t["passed"] for t in m["tests"]] == [False, False]
    assert controlling_value(m) == pytest.approx(0.001143)


def test_the_recorded_value_is_shown_as_recorded_never_recomputed():
    # A decision made before the 2026-09-18 sampling-error correction recorded a
    # zero uncertainty deduction; its edge stays exactly what it recorded.
    m = edge_metrics("MLB", -134, 0.5718, 0.55066, None, None, CHC_EVIDENCE)
    assert m["inputs"]["penalties"]["uncertainty"] == 0.0
    assert _fields(m)["recorded_policy_adjusted_edge"]["value"] == pytest.approx(0.001143)


def test_a_missing_recorded_policy_edge_is_unavailable_not_substituted():
    evidence = {k: v for k, v in CHC_EVIDENCE.items() if k != "adjusted_edge"}
    m = edge_metrics("MLB", -134, 0.5718, 0.55066, -0.00085, -0.0015, evidence)
    edge = _fields(m)["recorded_policy_adjusted_edge"]
    assert edge["value"] is None and edge["available"] is False
    # The break-even column is still present, under its own name only.
    assert _fields(m)["unadjusted_edge_vs_break_even"]["value"] == pytest.approx(-0.00085)


def test_nfl_controls_on_recorded_tie_aware_return_and_records_no_edge():
    evidence = {"best": {"win_probability": 0.7053, "tie_probability": 0.0297, "expected_value": 0.035128}}
    m = edge_metrics("NFL", -235, 0.7053, None, None, 0.035128, evidence)
    assert [t["metric"] for t in m["tests"]] == ["recorded_policy_return"]
    assert m["tests"][0]["passed"] is True and m["tests"][0]["threshold"] == 0.02
    assert _fields(m)["recorded_policy_adjusted_edge"]["available"] is False


def test_cfb_uses_the_recorded_terms_and_says_it_is_unadjusted():
    evidence = {"selection": {"win_probability": 0.6, "terms": {"no_vig_probability": 0.55, "edge": 0.05,
                                                                "expected_return_per_dollar": 0.04}}}
    m = edge_metrics("CFB", -120, 0.6, None, None, 0.04, evidence)
    assert [t["passed"] for t in m["tests"]] == [True, True]
    assert "no adjustments" in _fields(m)["recorded_policy_edge"]["label"]


def test_an_unpriced_decision_has_no_edge_figures():
    assert edge_metrics("MLB", None, None, None, None, None, {}) is None
