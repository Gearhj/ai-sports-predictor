"""Research-only point-in-time replay that cannot publish forecasts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Forecast, ForecastLifecycle, ForecastStatus, Game
from app.services.forecast_service import ForecastService
from app.services.game_state_service import GameStateService


@dataclass(frozen=True)
class BacktestReplayResult:
    game_id: str
    forecast_id: str
    feature_snapshot_id: str
    requested_data_as_of: str
    generated_at: str
    status: str
    research_mode: str = "BACKTEST"
    evaluation_eligible: bool = False
    provider_calls: int = 0


class Gate3BacktestReplay:
    """Reconstruct a historical cutoff through the operational state service.

    Replays intentionally use the same point-in-time state/snapshot path as an
    operational forecast, but are always forced to DRAFT and explicitly marked
    BACKTEST. They therefore cannot enter public forward-performance queries.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def run_game(
        self,
        game_id: Any,
        *,
        data_as_of: datetime,
        lifecycle: ForecastLifecycle = ForecastLifecycle.PREGAME,
    ) -> BacktestReplayResult:
        cutoff = _aware(data_as_of)
        game = self.session.get(Game, game_id)
        if game is None:
            raise LookupError(f"Unknown game {game_id}")
        state = GameStateService(self.session).get_game_state(game.id, cutoff)
        if state.source_mode != "LIVE":
            raise ValueError("Gate 3 backtests accept LIVE evidence only")
        if cutoff >= state.scheduled_at:
            raise ValueError("Backtest feature cutoff must be before observed first pitch")

        forecast = ForecastService(self.session, self.settings).generate_for_game(
            game,
            lifecycle=lifecycle,
            data_as_of=cutoff,
            force_draft=True,
        )
        if forecast.status != ForecastStatus.DRAFT or forecast.published_at is not None:
            raise RuntimeError("Backtest replay invariant violated: research forecast became public")
        readiness = dict(forecast.readiness_components or {})
        readiness["_gate3"] = {
            "research_mode": "BACKTEST",
            "evaluation_eligible": False,
            "requested_data_as_of": cutoff.isoformat(),
            "actual_generated_at": _aware(forecast.generated_at).isoformat(),
            "point_in_time_service": "GameStateService.get_game_state",
            "provider_calls": 0,
        }
        forecast.readiness_components = readiness
        self.session.flush()
        if forecast.feature_snapshot_id is None:
            raise RuntimeError("Backtest replay did not freeze a feature snapshot")
        return BacktestReplayResult(
            game_id=str(game.id),
            forecast_id=str(forecast.id),
            feature_snapshot_id=str(forecast.feature_snapshot_id),
            requested_data_as_of=cutoff.isoformat(),
            generated_at=_aware(forecast.generated_at).isoformat(),
            status=forecast.status.value,
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
