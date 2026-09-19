"""The edge figures a decision RECORDED, each named for what it is.

WHY
---
CHC @ CIN on 2026-09-18 showed "+0.1 pts" in its reason and "edge -0.1 pts" one
line below. Both were right; they were different quantities under one word.
Traced from the code that wrote them:

* ``evidence.adjusted_edge`` -- practical_policy.py: the ADJUSTED model
  probability (after the decision's recorded uncertainty and evidence
  penalties) minus the same-book no-vig probability: (0.5718 - 0.02) - 0.5507
  = +0.11 pts. The MLB policy's first test (>= 2.0 pts). Shown as
  "Recorded policy-adjusted edge".
* ``evidence.adjusted_expected_value`` -- practical_policy.py: the adjusted
  probability's expected return at the offered decimal odds, p_adj*d - 1
  (-3.64%). The MLB policy's second test (> 0).
* ``shadow_experiment_decisions.edge`` -- forecast_exchange/pricing.py:286 via
  the opportunity candidate's ``raw_edge``: the UNADJUSTED model probability
  minus the offered price's BREAK-EVEN probability 1/d, vig included:
  0.5718 - 0.57265 = -0.085 pts. Not a policy test.
* ``shadow_experiment_decisions.expected_value`` -- pricing.py
  ``expected_value(model.raw_win(), decimal)``: the UNADJUSTED model-implied
  expected return at the offered odds, p_raw*d - 1 (-0.15%). Not a policy test.

RULES
-----
Every value here is read from what the decision stored. Nothing is recomputed
from current quotes, current forecasts, or the sampling-error mapping corrected
on 2026-09-18 (e428b99): a decision made before that keeps the edge it was made
on. A value the decision did not record is None and is shown as unavailable.
The only arithmetic is 1/d of the recorded price, stated as the break-even.

POLICY TESTS (unchanged; read from the recorded decision)
--------------------------------------------------------
* MLB ``AISP_PRACTICAL_PREGAME_POLICY_V2_1``: recorded policy-adjusted edge
  >= 2.0 pts AND recorded policy-adjusted expected return > 0.
* CFB ``AISP_CFB_PAPER_POLICY_V1``: recorded edge vs no-vig >= 2.0 pts AND
  recorded expected return > 0 (this policy applies no adjustments).
* NFL ``AISP_NFL_PAPER_POLICY_V1``: recorded tie-aware expected return per $1
  >= 0.02; it records no no-vig edge.
"""

from __future__ import annotations

from typing import Any

MLB_POLICY = "AISP_PRACTICAL_PREGAME_POLICY_V2_1"
NFL_POLICY = "AISP_NFL_PAPER_POLICY_V1"
CFB_POLICY = "AISP_CFB_PAPER_POLICY_V1"

POINTS = "probability_points"
RETURN = "return_per_dollar"


def _decimal(american: Any) -> float | None:
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _field(key: str, label: str, value: Any, unit: str, source: str, *, policy_test: bool = False,
           note: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "value": _num(value), "unit": unit, "source": source,
            "policy_test": policy_test, "available": value is not None, "note": note}


def _test(field: dict[str, Any], threshold: float, operator: str) -> dict[str, Any]:
    value = field["value"]
    passed = None if value is None else (value >= threshold if operator == ">=" else value > threshold)
    return {"metric": field["key"], "label": field["label"], "value": value, "unit": field["unit"],
            "threshold": threshold, "operator": operator, "passed": passed}


def edge_metrics(sport: str, american: Any, model_probability: Any, no_vig: Any,
                 stored_edge: Any, stored_expected_value: Any, evidence: Any) -> dict[str, Any] | None:
    """Every edge figure one priced decision recorded, labelled, with its controlling tests."""

    d = _decimal(american)
    if d is None:
        return None
    ev = evidence if isinstance(evidence, dict) else {}
    offered = {"american": american, "decimal": round(d, 6), "break_even_probability": round(1.0 / d, 6)}
    sport = str(sport or "").upper()

    if sport in ("MLB", "BASEBALL_MLB"):
        from app.services.practical_policy import MIN_ADJUSTED_EDGE

        policy = ev.get("policy_version") or (MLB_POLICY if "adjusted_edge" in ev else None)
        edge = _field("recorded_policy_adjusted_edge", "Recorded policy-adjusted edge", ev.get("adjusted_edge"),
                      POINTS, "shadow_experiment_decisions.evidence.adjusted_edge", policy_test=True,
                      note="adjusted model probability minus the same-book no-vig probability, as recorded")
        ret = _field("recorded_policy_adjusted_return",
                     "Recorded policy-adjusted expected return at the offered odds",
                     ev.get("adjusted_expected_value"), RETURN,
                     "shadow_experiment_decisions.evidence.adjusted_expected_value", policy_test=True)
        fields = [
            edge, ret,
            _field("unadjusted_edge_vs_break_even",
                   "Unadjusted model probability minus the offered price's break-even probability",
                   stored_edge, POINTS, "shadow_experiment_decisions.edge (forecast_exchange.pricing raw_edge)"),
            _field("unadjusted_model_return", "Unadjusted model-implied expected return at the offered odds",
                   stored_expected_value, RETURN, "shadow_experiment_decisions.expected_value"),
            _field("recorded_unadjusted_edge_vs_no_vig",
                   "Recorded unadjusted model probability minus the no-vig probability",
                   ev.get("raw_edge"), POINTS, "shadow_experiment_decisions.evidence.raw_edge"),
        ]
        return {
            "policy": policy, "offered": offered,
            "inputs": {
                "model_probability": _num(ev.get("raw_model_probability", model_probability)),
                "adjusted_probability": _num(ev.get("adjusted_probability")),
                "no_vig_probability": _num(ev.get("market_probability", no_vig)),
                "penalties": ({"uncertainty": _num(ev.get("uncertainty_adjustment")),
                               "starter": _num(ev.get("starter_penalty")),
                               "lineup": _num(ev.get("lineup_penalty"))}
                              if "adjusted_probability" in ev else None),
            },
            "fields": fields,
            "tests": [_test(edge, MIN_ADJUSTED_EDGE, ">="), _test(ret, 0.0, ">")] if policy == MLB_POLICY else [],
        }

    if sport == "CFB":
        from app.services.research_paper_policy import CFB

        chosen = ev.get("selection") or ev.get("best") or {}
        terms = chosen.get("terms") or {}
        edge = _field("recorded_policy_edge", "Recorded policy edge (this policy applies no adjustments)",
                      terms.get("edge"), POINTS, "evidence.selection.terms.edge", policy_test=True,
                      note="model probability minus the same-book no-vig probability, as recorded")
        ret = _field("recorded_policy_return", "Recorded policy expected return at the offered odds",
                     terms.get("expected_return_per_dollar"), RETURN,
                     "evidence.selection.terms.expected_return_per_dollar", policy_test=True)
        return {
            "policy": CFB_POLICY, "offered": offered,
            "inputs": {"model_probability": _num(chosen.get("win_probability", model_probability)),
                       "adjusted_probability": None,
                       "no_vig_probability": _num(terms.get("no_vig_probability")), "penalties": None},
            "fields": [edge, ret],
            "tests": [_test(edge, CFB.min_adjusted_edge, ">="), _test(ret, 0.0, ">")] if terms else [],
        }

    if sport == "NFL":
        from app.services.nfl_paper_policy import MIN_ADJUSTED_EDGE as NFL_MIN

        chosen = ev.get("selection") or ev.get("best") or {}
        ret = _field("recorded_policy_return", "Recorded policy expected return at the offered odds (tie-aware)",
                     chosen.get("expected_value", stored_expected_value) if chosen else stored_expected_value,
                     RETURN, "evidence.selection.expected_value", policy_test=True)
        edge = _field("recorded_policy_adjusted_edge", "Recorded policy-adjusted edge", None, POINTS,
                      "not recorded", note="the NFL paper policy records no no-vig edge")
        return {
            "policy": NFL_POLICY, "offered": {**offered, "break_even_probability": None},
            "inputs": {"model_probability": _num(chosen.get("win_probability", model_probability)) if chosen
                       else _num(model_probability),
                       "adjusted_probability": None, "no_vig_probability": None, "penalties": None,
                       "tie_probability": _num(chosen.get("tie_probability")) if chosen else None},
            "fields": [ret, edge],
            "tests": [_test(ret, NFL_MIN, ">=")] if ret["value"] is not None else [],
        }
    return None


def controlling_value(metrics: dict[str, Any] | None) -> float | None:
    """The policy's first recorded test value, for a 'closest to qualifying' ranking."""

    if not metrics or not metrics.get("tests"):
        return None
    return metrics["tests"][0]["value"]


__all__ = ["edge_metrics", "controlling_value"]
