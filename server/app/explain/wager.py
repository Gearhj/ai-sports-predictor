"""Why a wager was recorded, in plain language, from what was actually saved.

WHAT THIS MAY AND MAY NOT SAY
-----------------------------
Every bullet is derived from the decision row and the forecast it names. Nothing
is inferred from what a feature *is*: a decision that stored a wind reading does
not thereby support "the wind favoured the Over", because the presence of an
input is not evidence that the input drove the estimate. Attribution requires
recorded attribution, and when there is none the panel says so in one sentence
rather than filling the space with something plausible.

The failure this guards against is specific and seductive. AISP has starter
states, lineup states and readiness components on nearly every decision. It
would be easy, and completely unfounded, to turn "starters confirmed" into "the
pitching matchup drove the pick". The starter state is a GATE -- it says the
inputs were trustworthy enough to act on -- not a driver.

HISTORY DOES NOT MOVE
---------------------
An explanation is built from the decision's own frozen evidence and the forecast
it bound. A later forecast, a corrected lineup, today's price or the result
itself never enter. That is what keeps a settled wager's rationale the same
sentence after settlement that it was before, which is the only way the record
is worth anything.
"""

from __future__ import annotations

from typing import Any

from app.explain.pricing import PriceComparison, compare

SCHEMA_VERSION = "AISP_WAGER_EXPLANATION_V1"

#: Stored gate names rendered as something a reader can act on. A gate absent
#: from this map is shown as its own recorded name rather than dropped: an
#: unexplained gate is still evidence, and hiding it would understate the
#: policy.
GATE_LANGUAGE: dict[str, str] = {
    "valid_current_forecast": "a current forecast existed for this game",
    "model_required_inputs_valid_or_governed_fallback":
        "the inputs the model requires were present or covered by a governed fallback",
    "complete_two_sided_moneyline_quote":
        "one sportsbook priced both sides, so the market was complete",
    "fresh_quote": "the price was recent enough to act on",
    "market_probabilities_available": "the market's own implied probabilities were available",
    "not_extreme_disagreement":
        "the disagreement with the market was large enough to matter but not so "
        "large that it suggested an input error",
    "no_unresolved_scratch": "no unresolved scratch was outstanding",
    "starters_confirmed_or_probable_stable":
        "both starting pitchers were confirmed, or had been stable long enough to rely on",
    "lineup_confirmed_adequate_or_pending_allowed":
        "the lineups were confirmed, or still pending in a way the policy allows",
    "adjusted_edge_minimum": "the edge cleared the policy's minimum after adjustments",
    "adjusted_ev_positive": "the expected value stayed positive after adjustments",
    "two_sided_total_quote": "one sportsbook priced both Over and Under",
}

#: Recorded adjustments, and what each one is FOR. Named so a reader can see
#: that a deduction is a deliberate haircut for weaker evidence rather than a
#: change to the model's opinion.
PENALTY_LANGUAGE: dict[str, str] = {
    "starter_penalty": "starting pitcher not yet confirmed",
    "lineup_penalty": "lineup not yet confirmed",
    "uncertainty_adjustment": "forecast uncertainty",
}


def explain_wager(
    *,
    decision: Any,
    position: Any | None,
    forecast: Any | None,
    matchup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the "Why this wager?" payload for one decision.

    ``position`` is None for a qualifying opportunity that was never recorded as
    a wager. That difference is carried explicitly, because a panel that reads
    the same either way would imply money was committed when it was not.
    """

    evidence = dict(getattr(decision, "evidence", None) or {})
    market = str(getattr(decision, "market", "") or "").upper()
    selection = str(getattr(decision, "selection", "") or "")

    adjusted = evidence.get("adjusted_probability")
    raw = evidence.get("raw_model_probability")
    model_probability = (
        adjusted if adjusted is not None
        else getattr(decision, "model_probability", None)
    )
    comparison = compare(
        american_odds=getattr(decision, "american_odds", None),
        decimal_odds=getattr(decision, "decimal_odds", None),
        model_probability=model_probability,
        raw_model_probability=raw,
        no_vig_probability=(
            evidence.get("market_probability")
            if evidence.get("market_probability") is not None
            else getattr(decision, "no_vig_probability", None)
        ),
    )

    recorded = position is not None
    bullets: list[dict[str, Any]] = [
        _selection_bullet(decision, market, selection, matchup, recorded, position),
        _probability_bullet(comparison, evidence),
        _price_bullet(comparison),
        _policy_bullet(decision, evidence),
    ]
    support = _support_bullet(evidence, forecast)
    if support is not None:
        bullets.append(support)

    return {
        "schema_version": SCHEMA_VERSION,
        "recorded_wager": recorded,
        "status_line": (
            "A virtual wager was recorded for this selection."
            if recorded else
            "This selection qualified under the policy. No virtual wager was recorded."
        ),
        "market": market,
        "selection": selection,
        "bullets": bullets,
        "price": comparison.as_dict(),
        "value_framing": _value_framing(comparison),
        "details": _details(decision, position, evidence, forecast),
    }


# -- individual bullets ------------------------------------------------------


def _selection_bullet(
    decision: Any, market: str, selection: str, matchup: dict[str, Any] | None,
    recorded: bool, position: Any,
) -> dict[str, Any]:
    where = f" in {matchup['label']}" if matchup and matchup.get("label") else ""
    if market == "TOTAL":
        line = getattr(decision, "line", None)
        side = selection.split()[0].title() if selection else "The total"
        text = (
            f"{side} {line:g} runs{where} was the selection"
            if line is not None else f"{selection}{where} was the selection"
        )
    else:
        text = f"{selection}{where} was the selection"
    if recorded and position is not None:
        text += f", staked at ${float(position.stake):.2f}"
    return {"key": "selection", "text": text + "."}


def _probability_bullet(
    comparison: PriceComparison, evidence: dict[str, Any],
) -> dict[str, Any]:
    """The model's estimate, and any recorded haircut applied to it."""

    model = comparison.model_probability
    raw = comparison.raw_model_probability
    if model is None:
        return {
            "key": "probability",
            "text": "No model probability was recorded with this decision.",
        }

    text = f"The model gave it {model * 100:.2f}%"
    applied = [
        (name, float(evidence[name]))
        for name in PENALTY_LANGUAGE
        if evidence.get(name) not in (None, 0, 0.0)
    ]
    if raw is not None and applied and abs(raw - model) > 1e-12:
        parts = ", ".join(
            f"{value * 100:.2f} pts for {PENALTY_LANGUAGE[name]}"
            for name, value in applied
        )
        text = (
            f"The model gave it {raw * 100:.2f}% before adjustments and "
            f"{model * 100:.2f}% after: {parts}"
        )
    elif raw is not None and abs(raw - model) > 1e-12:
        text = (
            f"The model gave it {raw * 100:.2f}% before adjustments and "
            f"{model * 100:.2f}% after"
        )
    return {"key": "probability", "text": text + "."}


def _price_bullet(comparison: PriceComparison) -> dict[str, Any]:
    """The offered price and what that price alone requires."""

    if comparison.american_odds is None or comparison.break_even is None:
        return {
            "key": "price",
            "text": "No usable offered price was recorded with this decision.",
        }
    sign = "+" if comparison.american_odds > 0 else ""
    text = (
        f"The offered price was {sign}{comparison.american_odds}, which needs "
        f"{comparison.break_even * 100:.2f}% to break even"
    )
    margin = comparison.margin_over_break_even
    if margin is not None:
        text += (
            f" — the estimate is {abs(margin) * 100:.2f} points "
            f"{'above' if margin > 0 else 'below'} that"
        )
    return {"key": "price", "text": text + "."}


def _policy_bullet(decision: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    """Only the reasons the stored policy actually recorded."""

    gates = [str(gate) for gate in (evidence.get("gates_passed") or [])]
    if gates:
        # Ordinary language only. The policy version string and the count of
        # checks are identifiers, not reasons -- a reader asking "why this
        # wager" is not helped by "AISP_PRACTICAL_PREGAME_POLICY_V2_1" or by
        # "11 checks passed", and both now live in the details disclosure.
        decisive = [g for g in gates if g in ("adjusted_edge_minimum", "adjusted_ev_positive")]
        readable = [GATE_LANGUAGE.get(g, g.replace("_", " ")) for g in (decisive or gates[:2])]
        return {
            "key": "policy",
            "text": f"It qualified because {', and '.join(readable)}.",
        }

    reasons = [str(r) for r in (getattr(decision, "qualification_reasons", None) or [])]
    if reasons:
        return {
            "key": "policy",
            "text": "The recorded policy notes were: " + "; ".join(reasons) + ".",
        }
    recorded_reason = str(getattr(decision, "recommendation_reason", "") or "").strip()
    if recorded_reason:
        return {"key": "policy", "text": recorded_reason}
    return {
        "key": "policy",
        "text": "No policy reasons were recorded with this decision.",
    }


def _support_bullet(
    evidence: dict[str, Any], forecast: Any | None,
) -> dict[str, Any] | None:
    """A matchup or input contribution, but only where one was recorded.

    This is where an explanation is most tempted to overreach, so the bar is
    deliberately high: a component must be recorded with a direction or a
    contribution, not merely be present with a value. AISP's stored
    ``feature_decomposition`` today carries completeness classes, not
    contributions -- it can say an input was missing, never that an input drove
    the estimate.
    """

    decomposition = dict(evidence.get("feature_decomposition") or {})
    contributions = decomposition.get("contributions") or decomposition.get("attributions")
    if isinstance(contributions, list) and contributions:
        described = []
        for entry in contributions[:3]:
            if not isinstance(entry, dict):
                continue
            name = entry.get("label") or entry.get("component") or entry.get("feature")
            direction = entry.get("direction") or entry.get("favours")
            if name and direction:
                described.append(f"{name} favoured {direction}")
        if described:
            return {
                "key": "support",
                "text": "Recorded contributions: " + "; ".join(described) + ".",
                "attribution_available": True,
            }

    missing = [str(m) for m in (decomposition.get("model_required_missing") or [])]
    if missing:
        return {
            "key": "support",
            "text": (
                "Detailed matchup contributions were not recorded for this "
                f"forecast. Inputs recorded as missing: {', '.join(missing[:4])}."
            ),
            "attribution_available": False,
        }
    return {
        "key": "support",
        "text": (
            "Detailed matchup contributions were not recorded for this forecast, "
            "so no claim is made about team strength, pitching, weather or park."
        ),
        "attribution_available": False,
    }


# -- framing -----------------------------------------------------------------


def _value_framing(comparison: PriceComparison) -> dict[str, Any]:
    """Separate "likely to win" from "worth taking at this price"."""

    favoured = comparison.model_makes_it_favoured
    clears = comparison.clears_break_even
    underdog = comparison.priced_as_underdog

    if favoured is None or clears is None:
        headline = "There is not enough recorded price or probability evidence to frame this."
        kind = "UNKNOWN"
    elif favoured and clears:
        headline = (
            "The model made this the more likely winner, and the price still "
            "cleared what it requires."
        )
        kind = "FAVOURED_AND_PRICED_WELL"
    elif not favoured and clears:
        headline = (
            "The model did NOT make this the more likely winner. It was taken "
            "because the offered price required a lower chance than the model's "
            "estimate — value at the price, not a prediction that it would win."
        )
        kind = "UNDERDOG_TAKEN_ON_PRICE"
    elif favoured and not clears:
        headline = (
            "The model made this the more likely winner, but the price did not "
            "clear what it requires."
        )
        kind = "FAVOURED_BUT_PRICE_SHORT"
    else:
        headline = "Neither the model's estimate nor the price supported this selection."
        kind = "NEITHER"

    return {
        "kind": kind,
        "headline": headline,
        "model_makes_it_favoured": favoured,
        "priced_as_underdog": underdog,
        "clears_break_even": clears,
        "note": (
            "Being worth backing at a price and being likely to win are "
            "different things. An underdog qualifies when its estimated chance "
            "exceeds what the price requires."
        ),
    }


def _details(
    decision: Any, position: Any, evidence: dict[str, Any], forecast: Any | None,
) -> dict[str, Any]:
    """Identities and timestamps, for the secondary disclosure."""

    lineage = dict(evidence.get("model_lineage") or {})
    return {
        "decision_id": str(getattr(decision, "id", "")) or None,
        "position_id": str(position.id) if position is not None else None,
        "phase": getattr(getattr(decision, "phase", None), "value", None),
        "recommended_at_utc": _iso(getattr(decision, "recommended_at", None)),
        "quote_observed_at_utc": _iso(getattr(decision, "quote_observed_at", None)),
        "sportsbook": getattr(decision, "sportsbook", None),
        "policy_version": evidence.get("policy_version"),
        "staking_version": evidence.get("staking_version"),
        "tier": evidence.get("tier"),
        "gates_passed": list(evidence.get("gates_passed") or []),
        "gates_passed_count": len(evidence.get("gates_passed") or []),
        "forecast_identity": (
            lineage.get("forecast_id")
            or (getattr(forecast, "forecast_id", None) if forecast is not None else None)
        ),
        "engine_id": lineage.get("engine_id"),
        "engine_version": lineage.get("engine_version"),
        "forecast_evidence_cutoff_utc": lineage.get("evidence_cutoff_utc"),
        "forecast_published_utc": lineage.get("published_utc"),
        "bound_at_decision_time": (
            "This explanation uses only the forecast and price this decision "
            "bound when it was made. Later forecasts, corrected lineups, current "
            "prices and the result do not enter it."
        ),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


__all__ = ["GATE_LANGUAGE", "PENALTY_LANGUAGE", "SCHEMA_VERSION", "explain_wager"]
