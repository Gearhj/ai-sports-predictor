"""NFL model-versus-market arithmetic on a joint final-score distribution.

Pure functions; no database, no provider. Every probability here is read off
the SAME persisted joint distribution, so a moneyline, a spread and a total
query never disagree with each other or with the forecast's own outcome
probabilities.

Three ideas that must stay distinct:

* THREE-WAY forecast: home / away / tie are separate model outcomes. The tie
  is the model's own final-score tie; the engine does not simulate overtime,
  so it is never read as an overtime probability.
* TWO-WAY moneyline: most US books VOID a pushed two-way NFL moneyline when
  the game ends tied. The comparable model probability is therefore the
  CONDITIONAL non-tie probability p / (1 - p_tie), stated as such.
* SPREAD / TOTAL: a whole-number line can push; win/loss/push are reported
  separately and the push mass is never folded into either side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

MONEYLINE_TIE_RULE_VOID = "TWO_WAY_TIE_VOIDS"
MONEYLINE_TIE_RULE_THREE_WAY = "THREE_WAY"


@dataclass(frozen=True)
class Cell:
    home: int
    away: int
    probability: float


def cells_from_distribution(rows: Iterable[dict[str, Any]]) -> list[Cell]:
    """Accept either the producer's snake_case or the supplement's camelCase cells."""

    out: list[Cell] = []
    for row in rows:
        home = row.get("home_score", row.get("homeScore"))
        away = row.get("away_score", row.get("awayScore"))
        out.append(Cell(int(home), int(away), float(row["probability"])))
    return out


def outcome_probabilities(cells: list[Cell]) -> dict[str, float]:
    home = away = tie = 0.0
    for cell in cells:
        if cell.home > cell.away:
            home += cell.probability
        elif cell.away > cell.home:
            away += cell.probability
        else:
            tie += cell.probability
    return {"home": home, "away": away, "tie": tie}


def conditional_non_tie(probability: float, tie_probability: float) -> float | None:
    """The two-way comparable probability when a tie voids the wager."""

    denominator = 1.0 - tie_probability
    if denominator <= 0:
        return None
    return probability / denominator


def spread_query(cells: list[Cell], *, home_line: float) -> dict[str, float]:
    """Home covers when (home - away) + home_line > 0. Push when it equals 0."""

    home_cover = away_cover = push = 0.0
    for cell in cells:
        margin = cell.home - cell.away + home_line
        if margin > 0:
            home_cover += cell.probability
        elif margin < 0:
            away_cover += cell.probability
        else:
            push += cell.probability
    return {"home_cover": home_cover, "away_cover": away_cover, "push": push}


def total_query(cells: list[Cell], *, line: float) -> dict[str, float]:
    over = under = push = 0.0
    for cell in cells:
        total = cell.home + cell.away
        if total > line:
            over += cell.probability
        elif total < line:
            under += cell.probability
        else:
            push += cell.probability
    return {"over": over, "under": under, "push": push}


def american_to_decimal(american: int | float) -> float:
    american = float(american)
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def implied_probability(american: int | float) -> float:
    return 1.0 / american_to_decimal(american)


def no_vig_pair(implied_a: float, implied_b: float) -> tuple[float, float]:
    total = implied_a + implied_b
    if total <= 0:
        return (0.0, 0.0)
    return (implied_a / total, implied_b / total)


def edge(model_probability: float | None, market_probability: float | None) -> float | None:
    if model_probability is None or market_probability is None:
        return None
    return model_probability - market_probability


def expected_value(model_probability: float, decimal_odds: float, *, push: float = 0.0) -> float:
    """Unit-stake EV with a push refunding the stake (contributes zero)."""

    lose = max(0.0, 1.0 - model_probability - push)
    return model_probability * (decimal_odds - 1.0) - lose


__all__ = [
    "Cell", "MONEYLINE_TIE_RULE_THREE_WAY", "MONEYLINE_TIE_RULE_VOID", "american_to_decimal",
    "cells_from_distribution", "conditional_non_tie", "edge", "expected_value",
    "implied_probability", "no_vig_pair", "outcome_probabilities", "spread_query", "total_query",
]
