"""Pure shadow-board pricing math.

No value is ever fabricated: every function below requires a real, complete,
two-sided quote to produce an edge, expected value, or recommendation. Without
a governed quote the caller assigns NO_BET and a zero virtual stake. Model
probabilities are read directly from the imported simulation distributions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.forecast_exchange.contracts import GameSplit, Uncertainty


class ShadowMarket(StrEnum):
    FULL_GAME_MONEYLINE = "FULL_GAME_MONEYLINE"
    FIRST_FIVE_MONEYLINE = "FIRST_FIVE_MONEYLINE"
    RUN_LINE_1_5 = "RUN_LINE_1_5"
    FULL_GAME_TOTAL = "FULL_GAME_TOTAL"
    FIRST_FIVE_TOTAL = "FIRST_FIVE_TOTAL"
    TEAM_TOTAL = "TEAM_TOTAL"


class Recommendation(StrEnum):
    NO_BET = "NO_BET"
    WATCH = "WATCH"
    QUALIFIED_BET = "QUALIFIED_BET"


# ----------------------------------------------------------------------------
# American odds primitives
# ----------------------------------------------------------------------------


def american_to_decimal(american: int) -> float:
    if american == 0:
        raise ValueError("American odds cannot be zero")
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def implied_probability(american: int) -> float:
    """Break-even (vigged) probability implied by one American price."""

    if american == 0:
        raise ValueError("American odds cannot be zero")
    if american > 0:
        return 100.0 / (american + 100.0)
    return abs(american) / (abs(american) + 100.0)


def no_vig_two_sided(implied_side: float, implied_opposite: float) -> float:
    """Proportional (Shin-free) no-vig probability for the priced side."""

    overround = implied_side + implied_opposite
    if overround <= 0:
        raise ValueError("Two-sided implied probabilities must be positive")
    return implied_side / overround


def expected_value(model_probability: float, decimal_odds: float) -> float:
    """Expected value per one unit staked at ``decimal_odds``."""

    if not 0.0 <= model_probability <= 1.0:
        raise ValueError("Model probability must be within [0, 1]")
    if decimal_odds <= 1.0:
        raise ValueError("Decimal odds must exceed 1")
    return model_probability * (decimal_odds - 1.0) - (1.0 - model_probability)


# ----------------------------------------------------------------------------
# Model probabilities from simulation distributions
# ----------------------------------------------------------------------------


def total_over_probability(split: GameSplit, line: float) -> float:
    return sum(cell.probability for cell in split.total_score_distribution if cell.total_score > line)


def total_under_probability(split: GameSplit, line: float) -> float:
    return sum(cell.probability for cell in split.total_score_distribution if cell.total_score < line)


def margin_at_least(split: GameSplit, threshold: int) -> float:
    """P(home_score - away_score >= threshold)."""

    return sum(cell.probability for cell in split.score_margin_distribution if cell.home_minus_away >= threshold)


def team_total_over(split: GameSplit, *, home: bool, line: float) -> float:
    total = 0.0
    for cell in split.joint_score_distribution:
        runs = cell.home_score if home else cell.away_score
        if runs > line:
            total += cell.probability
    return total


@dataclass(frozen=True)
class ModelProbability:
    probability: float
    lower: float | None
    upper: float | None


def model_probability(
    market: ShadowMarket,
    selection: str,
    *,
    line: float | None,
    full_game: GameSplit,
    first_five: GameSplit,
    uncertainty: Uncertainty,
) -> ModelProbability:
    """Return the model probability (and CI where the export provides one)."""

    if market == ShadowMarket.FULL_GAME_MONEYLINE:
        if selection == "HOME":
            return ModelProbability(full_game.home_win_probability, uncertainty.home_win_probability_lower, uncertainty.home_win_probability_upper)
        if selection == "AWAY":
            return ModelProbability(full_game.away_win_probability, uncertainty.away_win_probability_lower, uncertainty.away_win_probability_upper)
    elif market == ShadowMarket.FIRST_FIVE_MONEYLINE:
        if selection == "HOME":
            return ModelProbability(first_five.home_win_probability, uncertainty.first_five_home_win_probability_lower, uncertainty.first_five_home_win_probability_upper)
        if selection == "AWAY":
            return ModelProbability(first_five.away_win_probability, 1.0 - uncertainty.first_five_home_win_probability_upper, 1.0 - uncertainty.first_five_home_win_probability_lower)
    elif market == ShadowMarket.RUN_LINE_1_5:
        # ±1.5 has no push. Home -1.5 wins on a 2+ run home margin.
        if selection == "HOME_-1.5":
            return ModelProbability(margin_at_least(full_game, 2), None, None)
        if selection == "AWAY_+1.5":
            return ModelProbability(1.0 - margin_at_least(full_game, 2), None, None)
    elif market in (ShadowMarket.FULL_GAME_TOTAL, ShadowMarket.FIRST_FIVE_TOTAL):
        if line is None:
            raise ValueError("Total markets require a line")
        split = full_game if market == ShadowMarket.FULL_GAME_TOTAL else first_five
        if selection == "OVER":
            return ModelProbability(total_over_probability(split, line), None, None)
        if selection == "UNDER":
            return ModelProbability(total_under_probability(split, line), None, None)
    elif market == ShadowMarket.TEAM_TOTAL:
        if line is None:
            raise ValueError("Team total markets require a line")
        home = selection.startswith("HOME")
        over = selection.endswith("OVER")
        probability = team_total_over(full_game, home=home, line=line)
        return ModelProbability(probability if over else 1.0 - probability, None, None)
    raise ValueError(f"Unsupported market/selection: {market}/{selection}")


# ----------------------------------------------------------------------------
# Combined pricing of a single selection against a real two-sided quote
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ShadowPricingConfig:
    qualified_edge: float = 0.02
    watch_edge: float = 0.0


@dataclass(frozen=True)
class PricedSelection:
    model_probability: float
    break_even_probability: float
    no_vig_probability: float
    raw_edge: float
    no_vig_edge: float
    expected_value: float
    uncertainty_adjusted_edge: float
    best_decimal_odds: float
    best_american_odds: int
    recommendation: Recommendation


def price_selection(
    *,
    model: ModelProbability,
    side_american: int,
    opposite_american: int,
    best_american: int,
    is_ready: bool,
    config: ShadowPricingConfig | None = None,
) -> PricedSelection:
    """Price one selection given a complete two-sided quote. Never fabricates."""

    config = config or ShadowPricingConfig()
    break_even = implied_probability(side_american)
    no_vig = no_vig_two_sided(implied_probability(side_american), implied_probability(opposite_american))
    best_decimal = american_to_decimal(best_american)
    raw_edge = model.probability - break_even
    no_vig_edge = model.probability - no_vig
    ev = expected_value(model.probability, best_decimal)
    conservative = model.lower if model.lower is not None else model.probability
    uncertainty_adjusted_edge = conservative - no_vig

    if is_ready and ev > 0 and uncertainty_adjusted_edge >= config.qualified_edge:
        recommendation = Recommendation.QUALIFIED_BET
    elif no_vig_edge > config.watch_edge:
        recommendation = Recommendation.WATCH
    else:
        recommendation = Recommendation.NO_BET

    return PricedSelection(
        model_probability=model.probability,
        break_even_probability=break_even,
        no_vig_probability=no_vig,
        raw_edge=raw_edge,
        no_vig_edge=no_vig_edge,
        expected_value=ev,
        uncertainty_adjusted_edge=uncertainty_adjusted_edge,
        best_decimal_odds=best_decimal,
        best_american_odds=best_american,
        recommendation=recommendation,
    )


__all__ = [
    "ModelProbability",
    "PricedSelection",
    "Recommendation",
    "ShadowMarket",
    "ShadowPricingConfig",
    "american_to_decimal",
    "expected_value",
    "implied_probability",
    "margin_at_least",
    "model_probability",
    "no_vig_two_sided",
    "price_selection",
    "team_total_over",
    "total_over_probability",
    "total_under_probability",
]
