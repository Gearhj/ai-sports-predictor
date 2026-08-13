"""Secondary fixed-stake threshold simulations for descriptive analysis only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class BettingThresholdResult:
    threshold: float
    bets: int
    stake_per_bet: float
    total_staked: float
    profit: float
    roi: float | None
    win_rate: float | None
    max_drawdown: float
    secondary_analysis: bool = True
    price_basis: str = "provided_decimal_price; descriptive, not an execution claim"


def simulate_betting_thresholds(
    probabilities: Sequence[float],
    decimal_odds: Sequence[float | None],
    outcomes: Sequence[int],
    *,
    thresholds: Sequence[float] = (0.0, 0.02, 0.04, 0.06),
    stake_per_bet: float = 1.0,
    price_basis: str = "provided_decimal_price; descriptive, not an execution claim",
) -> tuple[BettingThresholdResult, ...]:
    """Simulate home-side flat stakes; never use this as a primary model metric."""

    probability = np.asarray(probabilities, dtype=float)
    outcome = np.asarray(outcomes, dtype=int)
    if probability.ndim != 1 or len(probability) == 0 or outcome.shape != probability.shape:
        raise ValueError("Probabilities and outcomes must align")
    if len(decimal_odds) != len(probability) or not np.all(np.isin(outcome, [0, 1])):
        raise ValueError("Odds and binary outcomes must align")
    if not np.isfinite(probability).all() or np.any((probability < 0) | (probability > 1)) or stake_per_bet <= 0:
        raise ValueError("Bet simulation inputs are invalid")
    if any(threshold < 0 for threshold in thresholds):
        raise ValueError("Bet thresholds cannot be negative")
    results: list[BettingThresholdResult] = []
    for threshold in thresholds:
        returns: list[float] = []
        wins = 0
        for forecast, price, result in zip(probability, decimal_odds, outcome):
            if price is None:
                continue
            price_value = float(price)
            if not np.isfinite(price_value) or price_value <= 1.0:
                continue
            edge = float(forecast - 1.0 / price_value)
            if edge >= threshold:
                won = int(result) == 1
                wins += int(won)
                returns.append(stake_per_bet * (price_value - 1.0) if won else -stake_per_bet)
        total_staked = len(returns) * stake_per_bet
        profit = float(sum(returns))
        cumulative = np.concatenate([[0.0], np.cumsum(returns)])
        peaks = np.maximum.accumulate(cumulative)
        max_drawdown = float(np.max(peaks - cumulative))
        results.append(BettingThresholdResult(
            threshold=float(threshold),
            bets=len(returns),
            stake_per_bet=stake_per_bet,
            total_staked=total_staked,
            profit=profit,
            roi=profit / total_staked if total_staked else None,
            win_rate=wins / len(returns) if returns else None,
            max_drawdown=max_drawdown,
            price_basis=price_basis,
        ))
    return tuple(results)
