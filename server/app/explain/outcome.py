"""What happened, said only as far as the retained evidence supports.

TWO LABELS THIS REFUSES TO APPLY
--------------------------------
A loss is not a model failure and a result inside the interval is not bad luck.
Both are stories, and both are reached for constantly because they end the
conversation. A 40% selection losing is the single most ordinary thing that can
happen to a 40% selection; it is evidence about the model only in aggregate, and
one game is not aggregate. Equally, a result sitting inside the forecast's
central range does not mean nothing went wrong -- a model can be wrong in a way
that still produces an unremarkable score.

So this module describes. It reports what the selection needed, what happened,
and how unusual the result was under the forecast's OWN distribution, and then
it stops. Where the mechanism is unresolved it says so in a sentence, which is
more useful than either label.

WHERE EVENT EVIDENCE COMES FROM
-------------------------------
``pitch_events`` and ``player_game_stats`` are still empty, so nothing is derived
from AISP's own play store. What the September 12 games now have is the published
explanation catalogue: observed event counts, starter and relief exposure and
inning scoring, each bound to an exact forecast identity. Games outside that
catalogue keep the honest unavailable sentence rather than borrowing another
game's shape.

Three kinds of statement stay apart, and the payload labels each one:

*   OBSERVED_CONTRIBUTOR -- a fact about the game. "New York hit six home runs."
*   POSSIBLE_EXPLANATION -- that fact located in the simulated population. Six
    against a simulated average of 1.18, reached in 0.24% of 5,000 draws.
*   ESTABLISHED_MODEL_DEFECT -- never claimed. Nothing in this catalogue shows
    that the duration model, the plate-appearance probabilities or the
    transitions were defective, and a tail location is not a p-value.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "AISP_OUTCOME_EXPLANATION_V1"

#: Result codes, said in words rather than left as enum values.
RESULT_LANGUAGE = {
    "win": "won", "loss": "lost", "push": "pushed",
    "void": "was voided", "pending": "has not settled yet",
}


def explain_outcome(
    *,
    decision: Any,
    position: Any | None,
    settlement: Any | None,
    final: dict[str, Any] | None,
    forecast_distributions: dict[str, Any] | None,
    matchup: dict[str, Any] | None = None,
    event_evidence: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The "What happened?" payload, or None while nothing has settled."""

    result = _result_code(position, settlement)
    if result in (None, "pending") and final is None:
        return None

    market = str(getattr(decision, "market", "") or "").upper()
    selection = str(getattr(decision, "selection", "") or "")
    bullets: list[dict[str, Any]] = []

    # Order matters here. What was selected and what happened come first,
    # then the observations that speak to THAT result. The total-runs interval
    # is moved last and marked secondary: for a moneyline wager it is context
    # about scoring, and it neither validates nor explains the home-win
    # probability the decision actually rested on. Leading with it made a
    # 2-12 loss look like it was being answered with a remark about run totals.
    needed = _needed_bullet(decision, market, selection)
    if needed is not None:
        bullets.append(needed)

    outcome = _outcome_bullet(result, final, matchup, position, settlement)
    if outcome is not None:
        bullets.append(outcome)

    events = _event_bullet(event_evidence)
    bullets.append(events)
    population = _population_bullet(event_evidence)
    if population is not None:
        bullets.append(population)

    comparison = _forecast_vs_result(
        final, forecast_distributions, market, decision)
    if comparison is not None:
        bullets.append(comparison)

    return {
        "schema_version": SCHEMA_VERSION,
        "result": result,
        "result_language": RESULT_LANGUAGE.get(result or "", result),
        "bullets": bullets,
        "uncertainty": _uncertainty(final, forecast_distributions, result),
        "interpretation_guard": (
            "This describes the result. A single game does not establish that "
            "the model was wrong, and a result inside the forecast's range does "
            "not establish that it was merely unlucky."
        ),
        "event_evidence_available": bool(events.get("evidence_available")),
        "population_comparison_available": population is not None,
        "statement_kinds": {
            "OBSERVED_CONTRIBUTOR": "A fact about what happened in the game.",
            "POSSIBLE_EXPLANATION": (
                "An observed fact located against the simulated population. "
                "Informative, not causal."
            ),
            "SECONDARY_CONTEXT": (
                "Related background that does not speak to the selection itself."
            ),
            "UNRESOLVED_HYPOTHESIS": (
                "A question the evidence raises and does not answer."
            ),
            "ESTABLISHED_MODEL_DEFECT": (
                "Not claimed anywhere here. No evidence in this catalogue "
                "establishes that a model component was defective."
            ),
        },
    }


# -- bullets -----------------------------------------------------------------


def _needed_bullet(decision: Any, market: str, selection: str) -> dict[str, Any] | None:
    if market == "TOTAL":
        line = getattr(decision, "line", None)
        side = (selection.split()[0].upper() if selection else "")
        if line is None:
            return None
        if side == "OVER":
            text = (
                f"The selection needed the combined total to finish above "
                f"{line:g}. Exactly {line:g} would have pushed and returned the stake."
                if float(line).is_integer() else
                f"The selection needed the combined total to finish above {line:g}."
            )
        else:
            text = (
                f"The selection needed the combined total to finish below "
                f"{line:g}. Exactly {line:g} would have pushed and returned the stake."
                if float(line).is_integer() else
                f"The selection needed the combined total to finish below {line:g}."
            )
        return {"key": "needed", "text": text}
    return {
        "key": "needed",
        "text": f"The selection needed {selection.replace(' ML', '')} to win the game.",
    }


def _outcome_bullet(
    result: str | None, final: dict[str, Any] | None,
    matchup: dict[str, Any] | None, position: Any, settlement: Any,
) -> dict[str, Any] | None:
    if final is None:
        return {
            "key": "outcome",
            "text": "No governed final outcome is recorded for this game yet.",
        }
    away, home = final.get("away_score"), final.get("home_score")
    away_abbr = (matchup or {}).get("away", "Away")
    home_abbr = (matchup or {}).get("home", "Home")
    text = f"Final: {away_abbr} {away}, {home_abbr} {home}"
    if result and result in RESULT_LANGUAGE:
        text += f" — the wager {RESULT_LANGUAGE[result]}"
    profit = getattr(settlement, "profit_loss", None) if settlement is not None else None
    if profit is not None:
        text += f" ({float(profit):+.2f})"
    if result == "push":
        text += ". The stake was returned"
    elif result == "void":
        text += ". The leg was voided and the stake returned"
    return {"key": "outcome", "text": text + "."}


def _forecast_vs_result(
    final: dict[str, Any] | None, distributions: dict[str, Any] | None,
    market: str, decision: Any,
) -> dict[str, Any] | None:
    """How the actual result sat under the forecast's own distribution.

    This is the one genuinely supported "difference between forecast and
    result" AISP holds for every V18 game: the total-runs mass function is
    stored, so the actual total's position within it can be stated exactly
    rather than characterised.
    """

    if final is None or not distributions:
        return None
    away, home = final.get("away_score"), final.get("home_score")
    if away is None or home is None:
        return None
    total = int(away) + int(home)

    mass = _mass(distributions.get("totalRunDistribution"))
    if not mass:
        return None
    expected = sum(runs * p for runs, p in mass.items())
    at_or_above = sum(p for runs, p in mass.items() if runs >= total)
    at_or_below = sum(p for runs, p in mass.items() if runs <= total)

    text = (
        f"Secondary context: the forecast expected about {expected:.2f} total "
        f"runs; the game produced {total}."
    )
    # Whichever tail the result fell in is the informative one.
    if total >= expected:
        text += (
            f" Under that forecast, {at_or_above * 100:.1f}% of simulated games "
            f"reached {total} runs or more."
        )
    else:
        text += (
            f" Under that forecast, {at_or_below * 100:.1f}% of simulated games "
            f"finished at {total} runs or fewer."
        )
    if market != "TOTAL":
        text += (
            " This is about scoring, not about the side that was backed — it "
            "neither validates nor explains the win probability."
        )
    return {
        "key": "forecast_vs_result",
        "statement_kind": "SECONDARY_CONTEXT",
        "text": text,
        "expected_total_runs": round(expected, 4),
        "actual_total_runs": total,
        "probability_at_or_beyond_pct": round(
            (at_or_above if total >= expected else at_or_below) * 100.0, 4),
    }


def _event_bullet(event_evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Observed events and exposure, when the catalogue supports them.

    Everything here is an OBSERVED CONTRIBUTOR: a fact about what happened. It
    is deliberately worded as description. "New York hit six home runs" is a
    fact; "the home runs caused the miss" is a claim this evidence does not
    support, and the sentence never drifts from the first into the second.
    """

    if not event_evidence:
        return {
            "key": "events",
            "evidence_available": False,
            "statement_kind": "UNAVAILABLE",
            "text": (
                "Deeper event analysis is unavailable: AISP holds no play-level "
                "or player-level record for this game, so home runs, walks, "
                "starter duration, relief exposure and inning-by-inning scoring "
                "cannot be reported."
            ),
        }

    parts: list[str] = []
    for side in ("away", "home"):
        block = event_evidence.get(f"{side}_events") or {}
        team = block.get("team") or side.upper()
        counts = block.get("counts") or {}
        completed = block.get("completed_pa")
        fragments = []
        for key, label in (("homeRuns", "home run"), ("baseOnBalls", "walk"),
                           ("strikeOuts", "strikeout")):
            value = counts.get(key)
            if value:
                fragments.append(f"{value} {label}{'s' if value != 1 else ''}")
        if fragments:
            suffix = f" in {completed} completed plate appearances" if completed else ""
            parts.append(f"{team} recorded {', '.join(fragments)}{suffix}")

    exposure = event_evidence.get("exposure") or {}
    for side in ("away", "home"):
        block = exposure.get(side) or {}
        starter = block.get("starter_bf")
        relief = block.get("relief_bf")
        if starter is not None:
            team = block.get("team") or side.upper()
            piece = f"{team}'s starter faced {starter} batters"
            if relief is not None:
                piece += f" and its relief faced {relief}"
            parts.append(piece)

    largest = event_evidence.get("largest_inning")
    if largest and largest.get("runs"):
        parts.append(
            f"the largest inning contained {largest['runs']} runs"
            + (f" ({largest['team']})" if largest.get("team") else "")
        )

    if not parts:
        return {
            "key": "events",
            "evidence_available": False,
            "statement_kind": "UNAVAILABLE",
            "text": "Deeper event analysis is unavailable for this game.",
        }
    return {
        "key": "events",
        "evidence_available": True,
        "statement_kind": "OBSERVED_CONTRIBUTOR",
        "text": "What the game produced: " + "; ".join(parts) + ".",
        "source": event_evidence.get("source"),
    }


#: Said the same way wherever a population comparison appears.
_POPULATION_LIMITS = (
    "A retrospective reproduction, captured after the result was known and "
    "selected for explanation. Probability sums condition on simulated exposures "
    "rather than the plate appearances that actually occurred, so this locates "
    "the result — it does not establish calibration, causation, or a model defect."
)


def _population_bullet(event_evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """Where an observed count sits in the retained simulated population.

    This is a POSSIBLE EXPLANATION and is labelled as one. Locating six home
    runs in the top 0.24% of 5,000 draws is informative and is not a cause, not
    a p-value, and not evidence of recurring miscalibration. The comparison is
    also retrospective -- reproduced after the result was known and selected
    because it was interesting -- so it cannot serve as independent validation.
    """

    population = (event_evidence or {}).get("population")
    if not population:
        return None
    highlights = population.get("highlights") or []
    if not highlights:
        # The comparison exists and nothing stood out. Saying so is informative;
        # dropping the bullet would report the comparison as unavailable, which
        # is a different and untrue statement.
        return {
            "key": "population",
            "statement_kind": "POSSIBLE_EXPLANATION",
            "text": (
                "Against the retained simulation, no observed event class fell "
                "in an unusual tail of the "
                f"{population.get('simulation_count', 0):,}-draw population."
            ),
            "classification": population.get("classification"),
            "limits": _POPULATION_LIMITS,
        }
    parts = [
        f"{row['label']} {row['actual']} against a simulated average of "
        f"{row['mean']:.2f} ({row['tail_pct']:.2f}% of {row['draws']:,} draws "
        f"reached it)"
        for row in highlights
    ]
    return {
        "key": "population",
        "statement_kind": "POSSIBLE_EXPLANATION",
        "text": "Against the retained simulation: " + "; ".join(parts) + ".",
        "classification": population.get("classification"),
        "limits": _POPULATION_LIMITS,
    }


def _uncertainty(
    final: dict[str, Any] | None, distributions: dict[str, Any] | None,
    result: str | None,
) -> dict[str, Any]:
    """One short statement, and deliberately not a verdict."""

    mass = _mass((distributions or {}).get("totalRunDistribution"))
    if final is None or not mass:
        return {
            "resolved": False,
            "text": (
                "There is not enough retained evidence to say why this result "
                "differed from the forecast."
            ),
        }
    away, home = final.get("away_score"), final.get("home_score")
    if away is None or home is None:
        return {"resolved": False, "text": "No governed final is recorded."}
    total = int(away) + int(home)
    ordered = sorted(mass.items())
    cumulative = 0.0
    low = high = None
    for runs, probability in ordered:
        if low is None and cumulative + probability >= 0.10:
            low = runs
        cumulative += probability
        if high is None and cumulative >= 0.90:
            high = runs
    inside = low is not None and high is not None and low <= total <= high

    if inside:
        text = (
            f"The final total sat inside the forecast's central range "
            f"({low}–{high} runs). That is consistent with the forecast; it does "
            "not by itself show the forecast was right or wrong about this game."
        )
    else:
        text = (
            f"The final total fell outside the forecast's central range "
            f"({low}–{high} runs). Without play-level evidence the mechanism is "
            "unresolved, so no cause is assigned here."
        )
    return {
        "resolved": False,
        "statement_kind": "UNRESOLVED_HYPOTHESIS",
        "inside_central_range": inside,
        "central_range": [low, high],
        "text": text,
        "note": (
            "Whether any unusual result here reflects recurring plate-appearance, "
            "duration or transition error is unresolved. One game cannot settle it."
        ),
    }


# -- helpers -----------------------------------------------------------------


def _result_code(position: Any, settlement: Any) -> str | None:
    for source in (settlement, position):
        value = getattr(getattr(source, "result", None), "value", None)
        if value:
            return str(value)
    return None


def _mass(raw: Any) -> dict[int, float]:
    if not isinstance(raw, dict) or not raw:
        return {}
    out: dict[int, float] = {}
    for key, value in raw.items():
        try:
            out[int(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


__all__ = ["RESULT_LANGUAGE", "SCHEMA_VERSION", "explain_outcome"]
