from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Iterable


class StakingStrategy(StrEnum):
    FLAT = "flat"
    FIXED_PERCENTAGE = "fixed_percentage"
    FRACTIONAL_KELLY = "fractional_kelly"


class SettlementResult(StrEnum):
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
    VOID = "void"


@dataclass(frozen=True)
class StakePolicy:
    strategy: StakingStrategy = StakingStrategy.FRACTIONAL_KELLY
    flat_stake: float = 10.0
    fixed_percentage: float = 0.01
    kelly_fraction: float = 0.25
    maximum_stake_percentage: float = 0.025
    label: str = "PROVISIONAL PHASE 1 BANKROLL POLICY"


def kelly_fraction(model_probability: float, decimal_odds: float) -> float:
    if not 0 <= model_probability <= 1:
        raise ValueError("Model probability must be between 0 and 1")
    if not isfinite(decimal_odds) or decimal_odds <= 1:
        raise ValueError("Decimal odds must be greater than 1")
    net_odds = decimal_odds - 1.0
    loss_probability = 1.0 - model_probability
    return max(0.0, (net_odds * model_probability - loss_probability) / net_odds)


def calculate_stake(
    bankroll: float,
    policy: StakePolicy,
    *,
    model_probability: float | None = None,
    decimal_odds: float | None = None,
) -> float:
    if bankroll < 0:
        raise ValueError("Bankroll cannot be negative")
    cap = bankroll * policy.maximum_stake_percentage
    if policy.strategy == StakingStrategy.FLAT:
        raw = policy.flat_stake
    elif policy.strategy == StakingStrategy.FIXED_PERCENTAGE:
        raw = bankroll * policy.fixed_percentage
    else:
        if model_probability is None or decimal_odds is None:
            raise ValueError("Fractional Kelly requires model_probability and decimal_odds")
        raw = bankroll * kelly_fraction(model_probability, decimal_odds) * policy.kelly_fraction
    return round(min(raw, cap, bankroll), 2)


def settle_profit(stake: float, decimal_odds: float, result: SettlementResult | str) -> float:
    if stake < 0:
        raise ValueError("Stake cannot be negative")
    if decimal_odds <= 1:
        raise ValueError("Decimal odds must be greater than 1")
    normalized = SettlementResult(result)
    if normalized == SettlementResult.WIN:
        return round(stake * (decimal_odds - 1.0), 2)
    if normalized == SettlementResult.LOSS:
        return round(-stake, 2)
    return 0.0


@dataclass(frozen=True)
class SettledPosition:
    stake: float
    decimal_odds: float
    result: SettlementResult


@dataclass(frozen=True)
class BankrollMetrics:
    starting_bankroll: float
    current_bankroll: float
    peak_bankroll: float
    maximum_drawdown: float
    profit_loss: float
    roi: float
    positions: int
    wins: int
    losses: int
    pushes: int


def bankroll_metrics(
    positions: Iterable[SettledPosition], starting_bankroll: float = 1000.0
) -> BankrollMetrics:
    if starting_bankroll <= 0:
        raise ValueError("Starting bankroll must be positive")
    balance = starting_bankroll
    peak = starting_bankroll
    maximum_drawdown = 0.0
    wins = losses = pushes = count = 0
    total_staked = 0.0
    for position in positions:
        count += 1
        total_staked += position.stake
        balance += settle_profit(position.stake, position.decimal_odds, position.result)
        peak = max(peak, balance)
        maximum_drawdown = max(maximum_drawdown, (peak - balance) / peak if peak else 0.0)
        if position.result == SettlementResult.WIN:
            wins += 1
        elif position.result == SettlementResult.LOSS:
            losses += 1
        elif position.result == SettlementResult.PUSH:
            pushes += 1
    profit_loss = balance - starting_bankroll
    return BankrollMetrics(
        starting_bankroll=starting_bankroll,
        current_bankroll=round(balance, 2),
        peak_bankroll=round(peak, 2),
        maximum_drawdown=maximum_drawdown,
        profit_loss=round(profit_loss, 2),
        roi=profit_loss / total_staked if total_staked else 0.0,
        positions=count,
        wins=wins,
        losses=losses,
        pushes=pushes,
    )
