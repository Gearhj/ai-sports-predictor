"""Correct a settled position when its game's final changes -- beside, never instead of.

``position_results`` is one immutable row per position: what AISP settled, when,
against which score. If a later, better or corrected final gives a different
result, that row -- and the settled position itself, which the audit layer
refuses to modify -- stays exactly as it was. An adjustment row records the
prior and corrected result, the profit difference and the observation that
caused it; the bankroll counts the original profit plus every adjustment delta,
and a reader asks ``current_result`` for the corrected outcome.

Idempotent: one adjustment per (position, causing observation). A game whose
settled scores still match its projection produces nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Game, Market, MarketOutcome, OfficialAspPosition, PositionSettlement,
    PositionSettlementAdjustment,
)


def correct_settled_positions(session: Session, game: Game, *, observation_id: Any,
                              now: datetime | None = None) -> list[dict[str, Any]]:
    from app.bankroll.service import SettlementResult, settle_profit
    from app.bankroll.settlement import determine_result

    moment = now or datetime.now(UTC)
    if game.home_score is None or game.away_score is None:
        return []
    rows = session.execute(
        select(OfficialAspPosition, PositionSettlement)
        .join(PositionSettlement, PositionSettlement.position_id == OfficialAspPosition.id)
        .join(MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .where(Market.game_id == game.id)
    ).all()
    out: list[dict[str, Any]] = []
    for position, settlement in rows:
        latest = session.scalar(select(PositionSettlementAdjustment).where(
            PositionSettlementAdjustment.position_id == position.id)
            .order_by(PositionSettlementAdjustment.adjusted_at.desc()).limit(1))
        basis_home = latest.corrected_home_score if latest else settlement.game_home_score
        basis_away = latest.corrected_away_score if latest else settlement.game_away_score
        if (basis_home, basis_away) == (game.home_score, game.away_score):
            continue
        if observation_id is not None and session.scalar(select(PositionSettlementAdjustment.id).where(
                PositionSettlementAdjustment.position_id == position.id,
                PositionSettlementAdjustment.game_state_observation_id == observation_id)):
            continue
        outcome = session.get(MarketOutcome, position.market_outcome_id)
        market = session.get(Market, outcome.market_id)
        corrected = determine_result(session, game, market, outcome)
        corrected_profit = round(settle_profit(float(position.stake), decimal_odds=float(position.decimal_odds),
                                               result=SettlementResult(corrected.value)), 2)
        previous_result = latest.corrected_result if latest else settlement.result.name
        previous_profit = float(latest.corrected_profit_loss if latest else settlement.profit_loss)
        adjustment = PositionSettlementAdjustment(
            position_id=position.id, original_settlement_id=settlement.id,
            game_state_observation_id=observation_id,
            previous_result=str(previous_result), corrected_result=corrected.name,
            previous_profit_loss=round(previous_profit, 2), corrected_profit_loss=corrected_profit,
            profit_loss_delta=round(corrected_profit - previous_profit, 2),
            previous_home_score=basis_home, previous_away_score=basis_away,
            corrected_home_score=game.home_score, corrected_away_score=game.away_score,
            reason=(f"Final corrected from {basis_away}-{basis_home} to {game.away_score}-{game.home_score} "
                    "(away-home) by a later retained observation; the original settlement is retained."),
            adjusted_at=moment)
        # The settled position and its settlement are immutable (the audit
        # layer refuses any update); the correction lives only here, and the
        # bankroll readers add its delta.
        session.add(adjustment)
        session.flush()
        out.append({"position_id": str(position.id), "previous": str(previous_result),
                    "corrected": corrected.name, "delta": float(adjustment.profit_loss_delta)})
    return out


def current_result(session: Session, position: OfficialAspPosition) -> str:
    """The position's result after any recorded correction."""

    latest = session.scalar(select(PositionSettlementAdjustment).where(
        PositionSettlementAdjustment.position_id == position.id)
        .order_by(PositionSettlementAdjustment.adjusted_at.desc()).limit(1))
    return latest.corrected_result if latest else str(getattr(position.result, "name", position.result))


def adjustment_total(session: Session, position_ids: list[Any]) -> float:
    """The sum of every adjustment delta for these positions (0.0 when none)."""

    from sqlalchemy import func

    if not position_ids:
        return 0.0
    total = session.scalar(select(func.coalesce(func.sum(PositionSettlementAdjustment.profit_loss_delta), 0))
                           .where(PositionSettlementAdjustment.position_id.in_(position_ids)))
    return float(total or 0.0)


__all__ = ["adjustment_total", "correct_settled_positions", "current_result"]
