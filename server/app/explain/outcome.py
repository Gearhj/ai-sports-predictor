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

WHAT COUNTS AS EVENT EVIDENCE
-----------------------------
Home runs, walks, starter batters faced, relief exposure and big innings are
real explanations when they are recorded. AISP currently stores none of them:
``pitch_events`` and ``player_game_stats`` are empty, and the retained
availability audit shows the Statcast and bullpen tables at zero rows. So the
event section reports what is genuinely held -- the governed final, and the
forecast's own distribution over that result -- and states plainly that deeper
event analysis is unavailable rather than assembling a plausible narrative from
the score alone.

The distinction the September 12 diagnostics drew is preserved here too:
observations about a result describe the result. "Six home runs against 1.175
expected" is a fact about the game; it does not establish that the duration
model, the plate-appearance probabilities or the transitions were defective.
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

    needed = _needed_bullet(decision, market, selection)
    if needed is not None:
        bullets.append(needed)

    outcome = _outcome_bullet(result, final, matchup, position, settlement)
    if outcome is not None:
        bullets.append(outcome)

    comparison = _forecast_vs_result(final, forecast_distributions, market, decision)
    if comparison is not None:
        bullets.append(comparison)

    events = _event_bullet(event_evidence)
    bullets.append(events)

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
        f"The forecast expected about {expected:.2f} total runs; the game "
        f"produced {total}."
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
    return {
        "key": "forecast_vs_result",
        "text": text,
        "expected_total_runs": round(expected, 4),
        "actual_total_runs": total,
        "probability_at_or_beyond_pct": round(
            (at_or_above if total >= expected else at_or_below) * 100.0, 4),
    }


def _event_bullet(event_evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Home runs, walks, starter duration, relief exposure — only if stored."""

    if not event_evidence:
        return {
            "key": "events",
            "evidence_available": False,
            "text": (
                "Deeper event analysis is unavailable: AISP holds no play-level "
                "or player-level record for this game, so home runs, walks, "
                "starter duration, relief exposure and inning-by-inning scoring "
                "cannot be reported."
            ),
        }
    parts: list[str] = []
    for key, template in (
        ("home_runs", "{value} home runs"),
        ("walks", "{value} walks"),
        ("starter_batters_faced", "the starter faced {value} batters"),
        ("relief_batters_faced", "relief faced {value}"),
        ("largest_inning_runs", "the biggest inning produced {value} runs"),
    ):
        value = event_evidence.get(key)
        if value is not None:
            parts.append(template.format(value=value))
    if not parts:
        return {
            "key": "events",
            "evidence_available": False,
            "text": "Deeper event analysis is unavailable for this game.",
        }
    return {
        "key": "events",
        "evidence_available": True,
        "text": "Recorded events: " + "; ".join(parts) + ".",
        "source": event_evidence.get("source"),
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
        "inside_central_range": inside,
        "central_range": [low, high],
        "text": text,
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
