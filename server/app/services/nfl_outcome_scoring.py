"""Pure scoring conventions for NFL outcome evaluation (no database, no provider).

The game-level three-way rules live in ``forecast_scoring`` and are reused, not
re-implemented:

* log loss      = -ln(max(p_actual, 1e-15)) over HOME / AWAY / TIE, using the
                  published probabilities (a tie is a class, never dropped);
* Brier score   = sum over the three classes of (p_class - 1[class = actual])^2,
                  so it ranges 0..2 (the "summed multiclass" convention);
* winner call   = the class with the largest published probability; a TIE
                  result is a miss for any HOME/AWAY call and is never excluded.

This module adds what the stored evaluation does not carry:

LINE EVALUATION (spread and total)
    For a recorded pre-kickoff line, the forecast's own paired score population
    gives exact WIN / LOSS / PUSH masses (home side for a spread, OVER for a
    total). The actual state is graded from the final score. The line is scored
    as a THREE-state forecast:

        line log loss = -ln(max(p_state, 1e-15))
        line Brier    = sum over WIN/LOSS/PUSH of (p_s - 1[s = actual])^2

    An integer line keeps its forecast push mass; a half-point line has none by
    construction. A push is never dropped and never counted as a loss.

MASS RECONCILIATION
    Published home + away + tie must equal one within a tolerance; the residual
    is reported, never silently absorbed.

CALIBRATION
    Game-level (probability, outcome) pairs are always retained. Binned
    calibration is published only at MIN_CALIBRATION_GAMES or more.

REVIEW PROGRESS
    Counts toward the registered review checkpoints (50, then 100 settled
    eligible games) within ONE frozen model cohort. No thresholds are chosen or
    applied here.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.probability.market_probabilities import (
    JointScorePopulation, _normalized_leg, _settle, evaluate_market,
)
from app.services.forecast_scoring import LOG_LOSS_EPSILON

LINE_STATES = ("WIN", "LOSS", "PUSH")
REVIEW_CHECKPOINTS = (50, 100)
MIN_CALIBRATION_GAMES = 100
CALIBRATION_BINS = 5
MASS_TOLERANCE = 1e-6

SMALL_SAMPLE_WARNING = (
    "Small sample. These are results for the games scored so far. A week of NFL games "
    "cannot show whether the model is skilful, well calibrated or profitable, and nothing "
    "here promotes a model, changes a threshold or grants wagering authority."
)


class LineScoringError(ValueError):
    pass


def line_kind(line: float) -> str:
    return "INTEGER" if float(line).is_integer() else "HALF_POINT" if (float(line) * 2).is_integer() else "FRACTIONAL"


def line_leg(market: str, line: float) -> dict[str, Any]:
    """The side scored for each market: the HOME handicap, or the OVER."""

    market = market.upper()
    if market == "SPREAD":
        return {"market": "SPREAD", "selection": "HOME", "line": float(line)}
    if market == "TOTAL":
        return {"market": "TOTAL", "selection": "OVER", "line": float(line)}
    raise LineScoringError(f"unsupported line market {market!r}")


def actual_line_state(market: str, line: float, home_score: int, away_score: int) -> str:
    leg = _normalized_leg(line_leg(market, line))
    return _settle(leg, int(away_score), int(home_score))


def three_state_scores(probabilities: Mapping[str, float], actual: str) -> dict[str, float]:
    """Log loss and summed Brier over WIN / LOSS / PUSH for one line."""

    actual = actual.upper()
    if actual not in LINE_STATES:
        raise LineScoringError(f"actual state {actual!r} is not WIN, LOSS or PUSH")
    masses = {state: float(probabilities.get(state.lower(), probabilities.get(state, 0.0)) or 0.0)
              for state in LINE_STATES}
    total = sum(masses.values())
    if not math.isclose(total, 1.0, abs_tol=MASS_TOLERANCE):
        raise LineScoringError(f"line masses sum to {total}, not 1 (void mass is not scoreable)")
    return {
        "log_loss": -math.log(max(masses[actual], LOG_LOSS_EPSILON)),
        "brier_score": sum((masses[state] - (1.0 if state == actual else 0.0)) ** 2 for state in LINE_STATES),
    }


def evaluate_line(population: JointScorePopulation, market: str, line: float,
                  home_score: int, away_score: int) -> dict[str, Any]:
    """The forecast's exact masses at a recorded line, graded and scored."""

    result = evaluate_market(population, line_leg(market, line))
    probabilities = result["probabilities"]
    if probabilities.get("void"):
        raise LineScoringError("a line with void mass cannot be scored as three states")
    actual = actual_line_state(market, line, home_score, away_score)
    scores = three_state_scores(probabilities, actual)
    return {
        "market": market.upper(),
        "scored_side": "HOME" if market.upper() == "SPREAD" else "OVER",
        "line": float(line),
        "line_kind": line_kind(line),
        "probabilities": {"win": probabilities["win"], "loss": probabilities["loss"],
                          "push": probabilities["push"]},
        "push_possible": line_kind(line) == "INTEGER",
        "actual_state": actual,
        "log_loss": scores["log_loss"],
        "brier_score": scores["brier_score"],
        "draws": result["effectiveDrawCount"],
    }


def mass_reconciliation(home: float | None, away: float | None, tie: float | None) -> dict[str, Any]:
    values = [home, away, tie]
    if any(value is None for value in values):
        return {"reconciled": False, "sum": None, "residual": None,
                "reason": "One or more published outcome probabilities is missing."}
    total = float(home) + float(away) + float(tie)
    return {"reconciled": abs(total - 1.0) <= MASS_TOLERANCE, "sum": total,
            "residual": total - 1.0, "tolerance": MASS_TOLERANCE}


def calibration(pairs: Sequence[tuple[float, int]], *, minimum: int = MIN_CALIBRATION_GAMES,
                bins: int = CALIBRATION_BINS) -> dict[str, Any]:
    """Binned calibration of the HOME-win probability, only at a sufficient sample.

    ``pairs`` are (home win probability, 1 if the home side won else 0); ties
    are passed as 0 because a tie is not a home win.
    """

    n = len(pairs)
    if n < minimum:
        return {"available": False, "pairs": n, "minimum": minimum,
                "reason": f"Calibration is shown from {minimum} settled games; {n} so far."}
    table = []
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [(p, y) for p, y in pairs if (low <= p < high) or (index == bins - 1 and p == 1.0)]
        if not members:
            table.append({"bin": [low, high], "games": 0, "mean_forecast": None, "observed_rate": None})
            continue
        table.append({
            "bin": [low, high], "games": len(members),
            "mean_forecast": sum(p for p, _ in members) / len(members),
            "observed_rate": sum(y for _, y in members) / len(members),
        })
    return {"available": True, "pairs": n, "minimum": minimum, "bins": table}


def review_progress(settled_games: int, *, checkpoints: Iterable[int] = REVIEW_CHECKPOINTS) -> dict[str, Any]:
    marks = sorted(set(int(c) for c in checkpoints))
    reached = [c for c in marks if settled_games >= c]
    upcoming = next((c for c in marks if settled_games < c), None)
    return {
        "settled_eligible_games": settled_games,
        "checkpoints": [{"games": c, "reached": settled_games >= c,
                         "remaining": max(0, c - settled_games)} for c in marks],
        "last_reached": reached[-1] if reached else None,
        "next_checkpoint": upcoming,
        "note": "Review checkpoints count settled eligible games within one frozen model "
                "cohort. Reaching one opens a review; it does not pass or fail anything.",
    }


def mean(values: Iterable[float | None]) -> float | None:
    present = [float(v) for v in values if v is not None]
    return sum(present) / len(present) if present else None


__all__ = [
    "CALIBRATION_BINS", "LINE_STATES", "LineScoringError", "MIN_CALIBRATION_GAMES",
    "REVIEW_CHECKPOINTS", "SMALL_SAMPLE_WARNING", "actual_line_state", "calibration",
    "evaluate_line", "line_kind", "line_leg", "mass_reconciliation", "mean",
    "review_progress", "three_state_scores",
]
