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

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bankroll.occurrence import RULE_ID as POSTPONEMENT_RULE_ID
from app.models import (
    Game, Market, MarketOutcome, OfficialAspPosition, PositionResult, PositionSettlement,
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
        # A postponement VOID (AISP_MLB_POSTPONED_NOT_PLAYED_ON_ORIGINAL_ET_DATE_V1,
        # as a settlement or as a governed correction) is not a score-based
        # result: the occurrence was never played, and the makeup's score must
        # never turn it back into a win or a loss. Scoped to that rule on
        # purpose: every other VOID (a CFB/NBA cancellation that later gets a
        # final) is corrected exactly as before.
        if (settlement.result == PositionResult.VOID
                and str(settlement.settlement_note or "").startswith(POSTPONEMENT_RULE_ID)) or (
                latest is not None and str(latest.corrected_result).upper() == "VOID"
                and str(latest.reason or "").startswith(POSTPONEMENT_RULE_ID)):
            continue
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


@dataclass(frozen=True)
class EffectiveSettlement:
    """A settled position's result and P&L after every recorded correction.

    ``result`` and ``original_result`` are upper-case result NAMES (the form
    both the settlement enum column and the adjustment rows store).
    ``events`` are the corrections in the order they were recorded:
    ``(adjusted_at, profit_loss_delta, corrected_result)``.
    """

    position_id: Any
    result: str
    profit_loss: float
    delta: float
    adjusted_at: datetime
    original_result: str
    original_profit_loss: float
    closing_line_value: float | None
    events: tuple[tuple[datetime, float, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "adjusted": True,
            "original_result": self.original_result,
            "original_profit_loss": round(self.original_profit_loss, 2),
            "adjustment_delta": round(self.delta, 2),
            "adjusted_at": self.adjusted_at.isoformat() if self.adjusted_at else None,
        }


def effective_settlements(session: Session, position_ids: list[Any] | None = None, *,
                          experiment_ids: list[Any] | None = None) -> dict[Any, EffectiveSettlement]:
    """Corrected result and P&L for every ADJUSTED position among these (one read).

    Keyed by position id; a position with no adjustment is absent, so a reader
    keeps its original settlement for it -- with zero adjustment rows every
    reader's output is exactly what it was. Select positions by id, by
    experiment, or both.
    """

    ids = [value for value in (position_ids or []) if value is not None]
    experiments = [value for value in (experiment_ids or []) if value is not None]
    if not ids and not experiments:
        return {}
    statement = (
        select(PositionSettlementAdjustment, PositionSettlement)
        .join(PositionSettlement, PositionSettlement.id == PositionSettlementAdjustment.original_settlement_id)
    )
    if ids:
        statement = statement.where(PositionSettlementAdjustment.position_id.in_(ids))
    if experiments:
        statement = statement.join(
            OfficialAspPosition, OfficialAspPosition.id == PositionSettlementAdjustment.position_id,
        ).where(OfficialAspPosition.shadow_experiment_id.in_(experiments))
    grouped: dict[Any, list[tuple[Any, Any]]] = {}
    for adjustment, settlement in session.execute(statement.order_by(
            PositionSettlementAdjustment.adjusted_at, PositionSettlementAdjustment.created_at,
            PositionSettlementAdjustment.id)).all():
        grouped.setdefault(adjustment.position_id, []).append((adjustment, settlement))
    out: dict[Any, EffectiveSettlement] = {}
    for position_id, items in grouped.items():
        settlement = items[0][1]
        latest = items[-1][0]
        original_result = str(getattr(settlement.result, "name", settlement.result)).upper()
        original = float(settlement.profit_loss)
        delta = round(sum(float(adjustment.profit_loss_delta) for adjustment, _ in items), 2)
        result = str(latest.corrected_result).upper()
        out[position_id] = EffectiveSettlement(
            position_id=position_id, result=result, profit_loss=round(original + delta, 2), delta=delta,
            adjusted_at=latest.adjusted_at, original_result=original_result, original_profit_loss=original,
            # No close exists for an occurrence that was never played.
            closing_line_value=(None if result == "VOID" else (
                latest.closing_line_value if latest.closing_line_value is not None
                else settlement.closing_line_value)),
            events=tuple((adjustment.adjusted_at, float(adjustment.profit_loss_delta),
                          str(adjustment.corrected_result).upper()) for adjustment, _ in items),
        )
    return out


__all__ = ["EffectiveSettlement", "adjustment_total", "correct_settled_positions", "current_result",
           "effective_settlements"]
