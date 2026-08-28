from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bankroll.service import SettlementResult, settle_profit
from app.models import (
    Forecast,
    ForecastTrack,
    Game,
    GameStatus,
    GameTeam,
    Market,
    MarketOutcome,
    MarketType,
    OfficialAspPosition,
    PositionResult,
    PositionSettlement,
    PositionStatus,
)


def settle_completed_positions(session: Session) -> list[PositionSettlement]:
    rows = list(session.execute(
        select(OfficialAspPosition, MarketOutcome, Market, Game)
        .join(Forecast, Forecast.id == OfficialAspPosition.forecast_id)
        .join(MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Game, Game.id == Market.game_id)
        .where(
            OfficialAspPosition.status == PositionStatus.OPEN,
            # Prospective experiment positions use this same canonical ledger,
            # but their extended bankroll/CLV evidence is finalized by the
            # experiment service below the same audited finalize job.
            OfficialAspPosition.shadow_experiment_id.is_(None),
            Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
            Forecast.source_mode == Game.source_mode,
            Game.status == GameStatus.FINAL,
            Game.home_score.is_not(None),
            Game.away_score.is_not(None),
        )
    ))
    settled: list[PositionSettlement] = []
    for position, outcome, market, game in rows:
        existing = session.scalar(select(PositionSettlement).where(PositionSettlement.position_id == position.id))
        if existing:
            continue
        result = determine_result(session, game, market, outcome)
        profit_loss = settle_profit(float(position.stake), position.decimal_odds, SettlementResult(result.value))
        now = datetime.now(UTC)
        position.result = result
        position.status = PositionStatus.SETTLED
        position.settled_at = now
        position.payout = round(float(position.stake) + profit_loss, 2)
        settlement = PositionSettlement(
            position_id=position.id,
            result=result,
            settled_at=now,
            profit_loss=profit_loss,
            game_home_score=game.home_score,
            game_away_score=game.away_score,
            settlement_note="Settled deterministically from canonical final score",
        )
        session.add(settlement)
        settled.append(settlement)
    session.flush()
    return settled


def determine_result(session: Session, game: Game, market: Market, outcome: MarketOutcome) -> PositionResult:
    home_score, away_score = int(game.home_score), int(game.away_score)
    if market.market_type == MarketType.MONEYLINE:
        if outcome.participant_team_id is None:
            return PositionResult.VOID
        selected_home = _is_home_selection(session, game.id, outcome.participant_team_id)
        selected_score, opponent_score = (home_score, away_score) if selected_home else (away_score, home_score)
        return PositionResult.WIN if selected_score > opponent_score else PositionResult.LOSS
    line = _outcome_line(outcome)
    if market.market_type == MarketType.SPREAD:
        if outcome.participant_team_id is None or line is None:
            return PositionResult.VOID
        selected_home = _is_home_selection(session, game.id, outcome.participant_team_id)
        selected_score, opponent_score = (home_score, away_score) if selected_home else (away_score, home_score)
        adjusted = selected_score + line - opponent_score
    elif market.market_type == MarketType.TOTAL:
        if line is None:
            return PositionResult.VOID
        total = home_score + away_score
        adjusted = (total - line) if outcome.label.lower() == "over" else (line - total)
    else:
        return PositionResult.VOID
    if adjusted > 0:
        return PositionResult.WIN
    if adjusted < 0:
        return PositionResult.LOSS
    return PositionResult.PUSH


def _is_home_selection(session: Session, game_id: object, team_id: object) -> bool:
    side = session.scalar(select(GameTeam).where(GameTeam.game_id == game_id, GameTeam.team_id == team_id))
    return bool(side and side.is_home)


def _outcome_line(outcome: MarketOutcome) -> float | None:
    try:
        suffix = outcome.outcome_key.rsplit(":", 1)[1]
        return None if suffix == "None" else float(suffix)
    except (IndexError, ValueError):
        return None
