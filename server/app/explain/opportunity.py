"""Why a totals opportunity looks interesting — and why that is not a wager.

AN OPPORTUNITY IS NOT A WAGER
-----------------------------
Totals are research under the current funded policy, which is moneyline-only.
Nothing here creates a decision, a position or a stake, and the wording never
implies one: the panel is "Why this opportunity?", the status line says no
virtual wager exists, and no stake appears anywhere.

That distinction is the whole reason this is a separate builder rather than a
flag on the wager explanation. A wager explanation is reconstructed from a
frozen decision row — a thing that happened. This is computed from a price and a
distribution that are both still moving, and calling the two by the same name
would let a research listing read as a record of action.

WHAT IT MAY NOT SAY
-------------------
The moneyline policy's probability penalties, thresholds and staking rules are
NOT applied. There is no established totals policy, and borrowing the moneyline
one would present an unvalidated adjustment as a settled rule. So the model
probability shown is V18's own, unadjusted, and no qualification claim is made.

And an expected total above the line is never offered as a reason. A forecast
mean of 8.03 against a line of 7.5 is not evidence that Over is likely or
valuable; only the probability at that exact line is, and only against the price
actually offered.
"""

from __future__ import annotations

from typing import Any

from app.explain.pricing import compare

SCHEMA_VERSION = "AISP_TOTALS_OPPORTUNITY_EXPLANATION_V1"


def explain_totals_opportunity(
    *,
    matchup: str,
    side: str,
    line: float,
    line_source: str,
    win_probability_pct: float,
    push_probability_pct: float,
    loss_probability_pct: float,
    price: dict[str, Any] | None,
    forecast: dict[str, Any],
    model_fair_american: int | None,
) -> dict[str, Any]:
    """Build the opportunity panel from the exact record being displayed.

    Every number comes from the same row the reader is looking at, bound to that
    line and that forecast. Nothing is recomputed from a different line: a
    probability for Over 8.0 must never be shown beside a price for Over 8.5,
    and the only way to guarantee that is to build both from one record.
    """

    comparison = compare(
        american_odds=(price or {}).get("american_odds"),
        decimal_odds=(price or {}).get("decimal_odds"),
        model_probability=win_probability_pct / 100.0,
        raw_model_probability=None,
        no_vig_probability=None,
    )

    bullets: list[dict[str, Any]] = [{
        "key": "selection",
        "text": (
            f"{side.title()} {line:g} in {matchup} is a research opportunity, "
            "not a recorded wager."
        ),
    }, {
        "key": "probability",
        "text": (
            f"The model gives {side.title()} {line:g} a "
            f"{win_probability_pct:.2f}% chance"
            + (f", with a {push_probability_pct:.2f}% chance the total lands "
               f"exactly on {line:g} and the stake is returned"
               if push_probability_pct > 0 else
               f". A half-run line cannot land on {line:g}, so there is no push")
            + "."
        ),
    }]

    if price is not None and comparison.break_even is not None:
        sign = "+" if price["american_odds"] > 0 else ""
        bullets.append({
            "key": "price",
            "text": (
                f"The {sign}{price['american_odds']} price requires about "
                f"{comparison.break_even * 100:.2f}% to break even."
            ),
        })
        cents = price["model_implied_net_return_per_unit"] * 100.0
        bullets.append({
            "key": "return",
            "text": (
                f"That produces a model-implied expected return of about "
                f"{cents:+.1f} cents per $1 at {price['sportsbook']}."
            ),
        })
    else:
        bullets.append({
            "key": "price",
            "text": (
                "No sportsbook price is recorded for this side, so no return "
                "can be estimated."
            ),
        })

    # Deliberately last, and deliberately not a reason. The expected total is
    # context about the game, not an argument for either side.
    expected = forecast.get("expected_total_runs")
    if expected is not None:
        bullets.append({
            "key": "context",
            "text": (
                f"For context, the forecast's expected total is {expected:.2f} "
                f"runs over {forecast.get('simulation_count', 0):,} simulated "
                "games. On its own that is not a reason to take either side — "
                "only the probability at this exact line, against this price, is."
            ),
            "statement_kind": "SECONDARY_CONTEXT",
        })

    bullets.append({
        "key": "support",
        "text": (
            "Detailed matchup contributions were not recorded for this forecast, "
            "so no claim is made about team strength, pitching, weather or park."
        ),
        "attribution_available": False,
    })

    return {
        "schema_version": SCHEMA_VERSION,
        "recorded_wager": False,
        "status_line": (
            "Research opportunity. No virtual wager, decision or stake exists "
            "for this selection."
        ),
        "market": "TOTAL",
        "selection": f"{side.title()} {line:g}",
        "bullets": bullets,
        "price": comparison.as_dict(),
        "value_framing": _framing(
            win_probability_pct, push_probability_pct, comparison),
        "details": {
            "decision_id": None,
            "position_id": None,
            "phase": None,
            "recommended_at_utc": None,
            "quote_observed_at_utc": (price or {}).get("observed_at"),
            "sportsbook": (price or {}).get("sportsbook"),
            "policy_version": None,
            "staking_version": None,
            "tier": None,
            "gates_passed": [],
            "gates_passed_count": 0,
            "forecast_identity": forecast.get("forecast_id"),
            "engine_id": forecast.get("engine_id"),
            "engine_version": forecast.get("engine_version"),
            "forecast_evidence_cutoff_utc": None,
            "forecast_published_utc": None,
            "line": line,
            "line_source": line_source,
            "model_fair_american_odds": model_fair_american,
            "bound_at_decision_time": (
                "Every figure here is bound to this exact line and this exact "
                "forecast. Totals are research under the current funded policy, "
                "which is moneyline-only: no totals threshold, probability "
                "penalty or staking rule is applied, because none is established."
            ),
        },
    }


def _framing(
    win_pct: float, push_pct: float, comparison: Any,
) -> dict[str, Any]:
    """Most likely and best value are different questions, and may differ."""

    likely = win_pct > 50.0
    clears = comparison.clears_break_even
    if clears is None:
        headline = "No offered price is recorded, so there is no value to assess."
        kind = "UNKNOWN"
    elif likely and clears:
        headline = (
            "The model makes this the more likely side, and the price still "
            "clears what it requires."
        )
        kind = "LIKELIER_AND_PRICED_WELL"
    elif not likely and clears:
        headline = (
            "The model does NOT make this the more likely side. It clears its "
            "price because the offered number requires less than the model's "
            "estimate — value at the price, not a prediction."
        )
        kind = "LESS_LIKELY_BUT_PRICED_WELL"
    elif likely:
        headline = (
            "The model makes this the more likely side, but the price does not "
            "clear what it requires."
        )
        kind = "LIKELIER_BUT_PRICE_SHORT"
    else:
        headline = "Neither the model's estimate nor the price supports this side."
        kind = "NEITHER"

    return {
        "kind": kind,
        "headline": headline,
        "model_makes_it_favoured": likely,
        "priced_as_underdog": comparison.priced_as_underdog,
        "clears_break_even": clears,
        "note": (
            "The most likely outcome and the best estimated value can be "
            "different selections. Sorting by one does not rank the other."
        ),
    }


__all__ = ["SCHEMA_VERSION", "explain_totals_opportunity"]
