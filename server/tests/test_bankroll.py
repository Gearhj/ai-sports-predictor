from __future__ import annotations

import pytest

from app.bankroll.service import (
    SettlementResult,
    SettledPosition,
    StakePolicy,
    StakingStrategy,
    bankroll_metrics,
    calculate_stake,
    kelly_fraction,
    settle_profit,
)


def test_kelly_and_maximum_cap() -> None:
    assert kelly_fraction(0.60, 2.0) == pytest.approx(0.20)
    policy = StakePolicy(
        strategy=StakingStrategy.FRACTIONAL_KELLY,
        kelly_fraction=0.25,
        maximum_stake_percentage=0.02,
    )
    assert calculate_stake(1000, policy, model_probability=0.75, decimal_odds=2.0) == 20
    assert calculate_stake(1000, policy, model_probability=0.40, decimal_odds=2.0) == 0


def test_win_loss_push_settlement() -> None:
    assert settle_profit(20, 2.5, SettlementResult.WIN) == 30
    assert settle_profit(20, 2.5, SettlementResult.LOSS) == -20
    assert settle_profit(20, 2.5, SettlementResult.PUSH) == 0


def test_bankroll_metrics_keep_losses_and_drawdown() -> None:
    metrics = bankroll_metrics([
        SettledPosition(100, 2.0, SettlementResult.WIN),
        SettledPosition(150, 2.0, SettlementResult.LOSS),
        SettledPosition(30, 2.0, SettlementResult.PUSH),
    ])
    assert metrics.positions == 3
    assert (metrics.wins, metrics.losses, metrics.pushes) == (1, 1, 1)
    assert metrics.current_bankroll == 950
    assert metrics.maximum_drawdown > 0
