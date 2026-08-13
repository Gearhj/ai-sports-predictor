from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bankroll.service import SettlementResult, SettledPosition, StakePolicy, StakingStrategy, bankroll_metrics, calculate_stake
from app.core.config import Settings
from app.models import (
    Forecast,
    ForecastStatus,
    Game,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    OfficialAspPosition,
    PositionResult,
    Sportsbook,
)


def publish_official_positions(session: Session, settings: Settings, games: list[Game]) -> list[OfficialAspPosition]:
    """Publish conservative virtual-only positions from fully paired moneylines.

    This is intentionally an explicit, feature-flagged admin operation. It never
    places a wager and never runs merely because an API credential exists.
    """
    if not settings.official_positions_enabled:
        return []
    bankroll = _current_bankroll(session)
    policy = StakePolicy(
        strategy=StakingStrategy.FRACTIONAL_KELLY,
        kelly_fraction=0.25,
        maximum_stake_percentage=settings.official_max_stake_percentage,
    )
    created: list[OfficialAspPosition] = []
    for game in games:
        forecast = session.scalar(select(Forecast).where(
            Forecast.game_id == game.id,
            Forecast.status == ForecastStatus.PUBLISHED,
        ).order_by(Forecast.version.desc()).limit(1))
        if not forecast:
            continue
        candidates = _moneyline_candidates(session, game.id)
        for candidate in candidates:
            probability = forecast.home_win_probability if candidate["is_home"] else forecast.away_win_probability
            edge = probability - candidate["no_vig_probability"]
            if edge < settings.opportunity_standard_threshold:
                continue
            duplicate = session.scalar(select(OfficialAspPosition.id).where(
                OfficialAspPosition.forecast_id == forecast.id,
                OfficialAspPosition.market_outcome_id == candidate["outcome"].id,
            ).limit(1))
            if duplicate:
                continue
            stake = calculate_stake(
                bankroll,
                policy,
                model_probability=probability,
                decimal_odds=candidate["snapshot"].decimal_odds,
            )
            if stake <= 0:
                continue
            position = OfficialAspPosition(
                forecast_id=forecast.id,
                market_outcome_id=candidate["outcome"].id,
                odds_snapshot_id=candidate["snapshot"].id,
                placed_at=datetime.now(UTC),
                stake=stake,
                decimal_odds=candidate["snapshot"].decimal_odds,
                result=PositionResult.PENDING,
                strategy=policy.label,
            )
            session.add(position)
            created.append(position)
            # Kelly positions are capped, but reserve stakes sequentially so a
            # slate cannot size every position against the same full balance.
            bankroll = max(0.0, bankroll - stake)
    session.flush()
    return created


def _moneyline_candidates(session: Session, game_id: Any) -> list[dict[str, Any]]:
    rows = list(session.execute(
        select(MarketOutcome, OddsSnapshot, Sportsbook)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id == game_id, Market.market_type == MarketType.MONEYLINE)
        .order_by(OddsSnapshot.observed_at.desc())
    ))
    latest: dict[tuple[Any, Any], tuple[MarketOutcome, OddsSnapshot, Sportsbook]] = {}
    for outcome, snapshot, book in rows:
        latest.setdefault((outcome.id, book.id), (outcome, snapshot, book))
    by_book: dict[Any, list[tuple[MarketOutcome, OddsSnapshot, Sportsbook]]] = {}
    for row in latest.values():
        by_book.setdefault(row[2].id, []).append(row)
    from app.markets.odds import no_vig_probabilities
    no_vig: dict[Any, list[float]] = {}
    for book_rows in by_book.values():
        if len(book_rows) != 2:
            continue
        result = no_vig_probabilities({str(row[0].id): row[1].decimal_odds for row in book_rows}, american=False)
        for outcome_id, probability in result.probabilities.items():
            no_vig.setdefault(outcome_id, []).append(probability)
    grouped: dict[Any, list[tuple[MarketOutcome, OddsSnapshot, Sportsbook]]] = {}
    for row in latest.values():
        grouped.setdefault(row[0].id, []).append(row)
    candidates: list[dict[str, Any]] = []
    for outcome_id, outcome_rows in grouped.items():
        probabilities = no_vig.get(str(outcome_id))
        if not probabilities:
            continue
        best = max(outcome_rows, key=lambda row: row[1].decimal_odds)
        from app.models import GameTeam
        side = session.scalar(select(GameTeam).where(GameTeam.game_id == game_id, GameTeam.team_id == best[0].participant_team_id))
        if not side:
            continue
        candidates.append({
            "outcome": best[0], "snapshot": best[1], "book": best[2],
            "is_home": side.is_home,
            "no_vig_probability": sum(probabilities) / len(probabilities),
        })
    return candidates


def _current_bankroll(session: Session) -> float:
    positions = list(session.scalars(select(OfficialAspPosition).order_by(OfficialAspPosition.placed_at)))
    settled = [
        SettledPosition(float(position.stake), position.decimal_odds, SettlementResult(position.result.value))
        for position in positions
        if position.result.value in {"win", "loss", "push", "void"}
    ]
    available = bankroll_metrics(settled).current_bankroll
    reserved_open_stakes = sum(
        float(position.stake)
        for position in positions
        if position.result == PositionResult.PENDING
    )
    return max(0.0, available - reserved_open_stakes)
