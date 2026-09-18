"""The edge figures behind one recorded decision, each named for what it is.

WHY
---
CHC @ CIN on 2026-09-18 showed "+0.1 pts" in its reason and "edge -0.1 pts" one
line below. Both were right; they were different quantities under one word:

* the policy's ADJUSTED ADVANTAGE OVER THE NO-VIG MARKET,
  ``evidence.adjusted_edge`` = (0.5718 - 0.02 starter) - 0.5507 = +0.11 pts;
* the stored ``shadow_experiment_decisions.edge``, which is
  ``forecast_exchange.pricing``'s raw model probability minus the offered
  price's BREAK-EVEN probability (1/decimal, vig included) =
  0.5718 - 0.57265 = -0.085 pts.

And the price the stake is paid at is a third quantity: the EXPECTED RETURN AT
THE OFFERED PRICE, p x decimal - 1 (raw -0.15%, adjusted -3.64%).

Exact identity, with q the same-book no-vig probability and d the decimal price:
``p*d - 1 = (p - q)*d + (q*d - 1)`` -- the advantage over the no-vig market,
scaled by the price, plus the market's own return at that price (the vig cost).

WHAT EACH POLICY CONTROLS ON (unchanged; read from the recorded decision)
-----------------------------------------------------------------------
* MLB ``AISP_PRACTICAL_PREGAME_POLICY_V2_1``: adjusted advantage over no-vig
  >= 2.0 pts AND adjusted expected return at the offered price > 0.
* CFB ``AISP_CFB_PAPER_POLICY_V1`` (``research_paper_policy.moneyline_terms``):
  advantage over no-vig >= 2.0 pts AND expected return > 0 (no penalties).
* NFL ``AISP_NFL_PAPER_POLICY_V1``: tie-aware expected return per $1 at the
  offered price >= 0.02; it has no no-vig advantage test.

This module only reads what each decision recorded. It changes no decision,
threshold or stored value.
"""

from __future__ import annotations

from typing import Any

MLB_POLICY = "AISP_PRACTICAL_PREGAME_POLICY_V2_1"
NFL_POLICY = "AISP_NFL_PAPER_POLICY_V1"
CFB_POLICY = "AISP_CFB_PAPER_POLICY_V1"


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


def _test(metric: str, label: str, value: float | None, threshold: float, operator: str) -> dict[str, Any]:
    passed = None if value is None else (value >= threshold if operator == ">=" else value > threshold)
    return {"metric": metric, "label": label, "value": value, "threshold": threshold,
            "operator": operator, "passed": passed}


def edge_metrics(sport: str, american: Any, model_probability: Any, no_vig: Any,
                 stored_edge: Any, stored_expected_value: Any, evidence: Any) -> dict[str, Any] | None:
    """Every edge figure one priced decision recorded, labelled, with its controlling tests."""

    d = _decimal(american)
    if d is None:
        return None
    ev = evidence if isinstance(evidence, dict) else {}
    break_even = 1.0 / d
    sport = str(sport or "").upper()

    if sport in ("MLB", "BASEBALL_MLB"):
        from app.services.practical_policy import MIN_ADJUSTED_EDGE

        p = _num(ev.get("raw_model_probability")) if ev.get("raw_model_probability") is not None else _num(model_probability)
        q = _num(ev.get("market_probability")) if ev.get("market_probability") is not None else _num(no_vig)
        p_adj = _num(ev.get("adjusted_probability"))
        adv_adj = _num(ev.get("adjusted_edge"))
        ret_adj = _num(ev.get("adjusted_expected_value"))
        policy = ev.get("policy_version") or (MLB_POLICY if adv_adj is not None else None)
        tests = ([
            _test("advantage_vs_no_vig.adjusted", "Adjusted advantage over the no-vig market",
                  adv_adj, MIN_ADJUSTED_EDGE, ">="),
            _test("return_at_offered_price.adjusted", "Adjusted expected return at the offered price",
                  ret_adj, 0.0, ">"),
        ] if policy == MLB_POLICY else [])
        penalties = ({"uncertainty": _num(ev.get("uncertainty_adjustment")), "starter": _num(ev.get("starter_penalty")),
                      "lineup": _num(ev.get("lineup_penalty"))} if p_adj is not None else None)
        return {
            "policy": policy,
            "offered": {"american": american, "decimal": round(d, 6), "break_even_probability": round(break_even, 6)},
            "no_vig_probability": q,
            "model_probability": {"raw": p, "adjusted": p_adj, "penalties": penalties},
            "advantage_vs_no_vig": {"raw": (p - q) if p is not None and q is not None else None, "adjusted": adv_adj},
            "advantage_vs_break_even": {"raw": _num(stored_edge) if stored_edge is not None
                                        else ((p - break_even) if p is not None else None),
                                        "adjusted": (p_adj - break_even) if p_adj is not None else None},
            "return_at_offered_price": {"raw": _num(stored_expected_value) if stored_expected_value is not None
                                        else ((p * d - 1) if p is not None else None),
                                        "adjusted": ret_adj},
            "vig_cost_at_offered_price": (q * d - 1) if q is not None else None,
            "tests": tests,
        }

    if sport == "CFB":
        from app.services.research_paper_policy import CFB

        chosen = ev.get("selection") or ev.get("best") or {}
        terms = chosen.get("terms") or {}
        p = _num(chosen.get("win_probability")) if chosen else _num(model_probability)
        q = _num(terms.get("no_vig_probability"))
        adv = _num(terms.get("edge"))
        ret = _num(terms.get("expected_return_per_dollar")) if terms else _num(stored_expected_value)
        return {
            "policy": CFB_POLICY,
            "offered": {"american": american, "decimal": round(d, 6), "break_even_probability": round(break_even, 6)},
            "no_vig_probability": q,
            "model_probability": {"raw": p, "adjusted": None, "penalties": None},
            "advantage_vs_no_vig": {"raw": adv, "adjusted": None},
            "advantage_vs_break_even": {"raw": (p - break_even) if p is not None else None, "adjusted": None},
            "return_at_offered_price": {"raw": ret, "adjusted": None},
            "vig_cost_at_offered_price": (q * d - 1) if q is not None else None,
            "tests": [
                _test("advantage_vs_no_vig.raw", "Advantage over the no-vig market", adv, CFB.min_adjusted_edge, ">="),
                _test("return_at_offered_price.raw", "Expected return at the offered price", ret, 0.0, ">"),
            ] if terms else [],
        }

    if sport == "NFL":
        from app.services.nfl_paper_policy import MIN_ADJUSTED_EDGE as NFL_MIN

        chosen = ev.get("selection") or ev.get("best") or {}
        p = _num(chosen.get("win_probability")) if chosen else _num(model_probability)
        ret = _num(chosen.get("expected_value")) if chosen else _num(stored_expected_value)
        return {
            "policy": NFL_POLICY,
            "offered": {"american": american, "decimal": round(d, 6), "break_even_probability": None},
            "no_vig_probability": None,
            "model_probability": {"raw": p, "adjusted": None, "penalties": None,
                                  "tie": _num(chosen.get("tie_probability")) if chosen else None},
            "advantage_vs_no_vig": {"raw": None, "adjusted": None},
            "advantage_vs_break_even": {"raw": None, "adjusted": None},
            "return_at_offered_price": {"raw": ret, "adjusted": None},
            "vig_cost_at_offered_price": None,
            "tests": [_test("return_at_offered_price.raw", "Tie-aware expected return at the offered price",
                            ret, NFL_MIN, ">=")] if ret is not None else [],
        }
    return None


def controlling_advantage(metrics: dict[str, Any] | None) -> float | None:
    """The figure a 'closest to qualifying' ranking should use: the policy's first test."""

    if not metrics or not metrics.get("tests"):
        return None
    return metrics["tests"][0]["value"]


__all__ = ["edge_metrics", "controlling_advantage"]
