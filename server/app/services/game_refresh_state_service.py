from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.lifecycle import (
    LifecycleStage,
    evaluate_lifecycle,
    lifecycle_targets,
)
from app.models import (
    Forecast,
    ForecastLifecycle,
    ForecastStatus,
    ForecastTrack,
    Game,
    GameFeatureSnapshot,
    GameRefreshState,
    GameStateObservation,
    GameStatus,
)


class GameRefreshStateService:
    """Select and persist event-relative state without executing refresh work.

    This service is deliberately limited to evidence selection and append-only
    lifecycle state. It never imports an adapter or invokes the job orchestrator.
    A future dispatcher may consume these rows after separate provider, quota,
    freshness, and publication authorization checks.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def record_due_states(self, *, as_of: datetime | None = None) -> list[GameRefreshState]:
        instant = _aware(as_of or datetime.now(UTC))
        games = list(
            self.session.scalars(
                select(Game)
                .where(
                    Game.source_mode == "LIVE",
                    Game.status.in_((GameStatus.SCHEDULED, GameStatus.LIVE, GameStatus.FINAL)),
                )
                .order_by(Game.starts_at, Game.id)
            )
        )
        recorded: list[GameRefreshState] = []
        for game in games:
            state = self._state_if_due(game, instant)
            if state is not None:
                self.session.add(state)
                self.session.flush()
                recorded.append(state)
        return recorded

    def _state_if_due(self, game: Game, instant: datetime) -> GameRefreshState | None:
        current_game_evidence = self.session.scalar(
            select(GameStateObservation)
            .where(
                GameStateObservation.game_id == game.id,
                GameStateObservation.available_as_of <= instant,
                GameStateObservation.source_mode == game.source_mode,
            )
            .order_by(
                GameStateObservation.available_as_of.desc(),
                GameStateObservation.observed_at.desc(),
                GameStateObservation.id.desc(),
            )
            .limit(1)
        )
        starts_at = _aware(
            current_game_evidence.scheduled_at if current_game_evidence else game.starts_at
        )
        first_target = lifecycle_targets(starts_at)[0].target_at
        if first_target is not None and instant < first_target:
            return None

        latest = self.session.scalar(
            select(GameRefreshState)
            .where(GameRefreshState.game_id == game.id)
            .order_by(GameRefreshState.available_as_of.desc(), GameRefreshState.created_at.desc())
            .limit(1)
        )
        final_forecast = self._final_forecast(game.id, starts_at, instant)
        frozen_at = None
        if final_forecast is not None:
            frozen_at = _aware(
                final_forecast.published_at
                or final_forecast.available_as_of
                or final_forecast.generated_at
            )
        elif latest is not None and latest.final_snapshot_frozen:
            frozen_at = _aware(latest.available_as_of)

        evidence_is_final = bool(
            current_game_evidence
            and str(current_game_evidence.status).lower() == GameStatus.FINAL.value
        )
        completed_at = None
        if evidence_is_final:
            completed_at = _aware(
                current_game_evidence.final_at or current_game_evidence.available_as_of
            )
        elif game.status == GameStatus.FINAL:
            completed_at = _optional_aware(game.final_at)
        lifecycle = evaluate_lifecycle(
            starts_at,
            instant,
            final_forecast_frozen_at=frozen_at,
            game_final_at=completed_at,
        )
        latest_feature = self.session.scalar(
            select(GameFeatureSnapshot)
            .where(
                GameFeatureSnapshot.game_id == game.id,
                GameFeatureSnapshot.data_as_of <= instant,
                GameFeatureSnapshot.source_mode == game.source_mode,
            )
            .order_by(GameFeatureSnapshot.data_as_of.desc())
            .limit(1)
        )
        latest_forecast = self.session.scalar(
            select(Forecast)
            .where(
                Forecast.game_id == game.id,
                Forecast.available_as_of <= instant,
                Forecast.source_mode == game.source_mode,
                Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
            )
            .order_by(Forecast.version.desc())
            .limit(1)
        )
        last_source_refresh_at = self.session.scalar(
            select(GameStateObservation.available_as_of)
            .where(
                GameStateObservation.game_id == game.id,
                GameStateObservation.available_as_of <= instant,
                GameStateObservation.source_mode == game.source_mode,
            )
            .order_by(GameStateObservation.available_as_of.desc())
            .limit(1)
        )
        frozen = frozen_at is not None and frozen_at <= instant
        final_status, freeze_reason = _final_status(
            lifecycle.stage,
            frozen=frozen,
            starts_at=starts_at,
            instant=instant,
        )
        next_refresh_at = lifecycle.next_target.target_at if lifecycle.next_target else None

        candidate = GameRefreshState(
            game_id=game.id,
            lifecycle_stage=lifecycle.stage.value,
            evaluated_at=instant,
            available_as_of=instant,
            first_pitch_at=starts_at,
            time_to_first_pitch_seconds=max(0, int((starts_at - instant).total_seconds())),
            last_source_refresh_at=_optional_aware(last_source_refresh_at),
            last_feature_snapshot_id=latest_feature.id if latest_feature else None,
            last_forecast_id=latest_forecast.id if latest_forecast else None,
            next_scheduled_refresh_at=next_refresh_at,
            final_forecast_status=final_status,
            final_snapshot_frozen=frozen,
            freeze_reason=freeze_reason,
            source_mode=game.source_mode,
        )
        if latest is not None and not _materially_changed(latest, candidate):
            return None
        return candidate

    def _final_forecast(
        self,
        game_id: Any,
        starts_at: datetime,
        instant: datetime,
    ) -> Forecast | None:
        return self.session.scalar(
            select(Forecast)
            .where(
                Forecast.game_id == game_id,
                Forecast.lifecycle == ForecastLifecycle.FINAL,
                Forecast.status == ForecastStatus.PUBLISHED,
                Forecast.available_as_of < starts_at,
                Forecast.available_as_of <= instant,
                Forecast.source_mode == "LIVE",
                Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
            )
            .order_by(Forecast.version.desc())
            .limit(1)
        )


def _materially_changed(previous: GameRefreshState, candidate: GameRefreshState) -> bool:
    return any(
        (
            previous.lifecycle_stage != candidate.lifecycle_stage,
            _optional_aware(previous.first_pitch_at) != candidate.first_pitch_at,
            _optional_aware(previous.last_source_refresh_at) != candidate.last_source_refresh_at,
            previous.last_feature_snapshot_id != candidate.last_feature_snapshot_id,
            previous.last_forecast_id != candidate.last_forecast_id,
            _optional_aware(previous.next_scheduled_refresh_at)
            != candidate.next_scheduled_refresh_at,
            previous.final_forecast_status != candidate.final_forecast_status,
            previous.final_snapshot_frozen != candidate.final_snapshot_frozen,
            previous.freeze_reason != candidate.freeze_reason,
            previous.source_mode != candidate.source_mode,
        )
    )


def _final_status(
    stage: LifecycleStage,
    *,
    frozen: bool,
    starts_at: datetime,
    instant: datetime,
) -> tuple[str, str | None]:
    if frozen:
        return "FROZEN", "Published final pregame forecast is frozen"
    if stage == LifecycleStage.T_MINUS_10M:
        return "PENDING_FREEZE", None
    if stage == LifecycleStage.POSTGAME:
        return "NOT_FROZEN", "Final game state was observed without a final forecast freeze"
    if stage == LifecycleStage.FINAL and instant >= starts_at:
        return "MISSED", "First pitch closed pregame publication before a final freeze"
    return "NOT_STARTED", None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite drops timezone information even for timezone-aware columns.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_aware(value: datetime | None) -> datetime | None:
    return _aware(value) if value is not None else None
