from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class LifecycleStage(StrEnum):
    """Auditable, per-game refresh and publication lifecycle."""

    MORNING = "MORNING"
    T_MINUS_6H = "T_MINUS_6H"
    T_MINUS_3H = "T_MINUS_3H"
    T_MINUS_60M = "T_MINUS_60M"
    T_MINUS_30M = "T_MINUS_30M"
    T_MINUS_10M = "T_MINUS_10M"
    FINAL = "FINAL"
    POSTGAME = "POSTGAME"


MORNING_LEAD = timedelta(hours=12)

_TIMED_TARGETS: tuple[tuple[LifecycleStage, timedelta], ...] = (
    (LifecycleStage.MORNING, MORNING_LEAD),
    (LifecycleStage.T_MINUS_6H, timedelta(hours=6)),
    (LifecycleStage.T_MINUS_3H, timedelta(hours=3)),
    (LifecycleStage.T_MINUS_60M, timedelta(minutes=60)),
    (LifecycleStage.T_MINUS_30M, timedelta(minutes=30)),
    (LifecycleStage.T_MINUS_10M, timedelta(minutes=10)),
)


@dataclass(frozen=True)
class LifecycleTarget:
    stage: LifecycleStage
    target_at: datetime | None


@dataclass(frozen=True)
class LifecycleState:
    stage: LifecycleStage
    game_starts_at: datetime
    evaluated_at: datetime
    next_target: LifecycleTarget | None
    pregame_publication_allowed: bool
    final_forecast_frozen: bool


def lifecycle_targets(game_starts_at: datetime) -> tuple[LifecycleTarget, ...]:
    """Return the timed targets derived solely from one game's current start.

    FINAL is triggered by the successful final-forecast freeze, and POSTGAME is
    triggered by a recorded game completion, so neither receives an invented
    wall-clock target here.
    """

    starts_at = _aware(game_starts_at)
    return tuple(
        LifecycleTarget(stage=stage, target_at=starts_at - lead)
        for stage, lead in _TIMED_TARGETS
    )


def classify_lifecycle(
    game_starts_at: datetime,
    as_of: datetime,
    *,
    final_forecast_frozen_at: datetime | None = None,
    game_final_at: datetime | None = None,
) -> LifecycleStage:
    """Classify a game without global state or browser-local time.

    A frozen final forecast remains FINAL through the live game. POSTGAME is
    entered only from an observed final-game timestamp. If first pitch passes
    before the freeze is recorded, the state still closes to FINAL so no new
    pregame publication can occur.
    """

    starts_at = _aware(game_starts_at)
    instant = _aware(as_of)
    frozen_at = _optional_aware(final_forecast_frozen_at)
    completed_at = _optional_aware(game_final_at)

    if completed_at is not None and instant >= completed_at:
        return LifecycleStage.POSTGAME
    if frozen_at is not None and instant >= frozen_at:
        return LifecycleStage.FINAL
    if instant >= starts_at:
        return LifecycleStage.FINAL

    stage = LifecycleStage.MORNING
    for target in lifecycle_targets(starts_at):
        if target.target_at is not None and instant >= target.target_at:
            stage = target.stage
    return stage


def next_lifecycle_target(
    game_starts_at: datetime,
    as_of: datetime,
    *,
    final_forecast_frozen_at: datetime | None = None,
    game_final_at: datetime | None = None,
) -> LifecycleTarget | None:
    """Return the next timed or evidence-triggered transition for one game."""

    starts_at = _aware(game_starts_at)
    instant = _aware(as_of)
    frozen_at = _optional_aware(final_forecast_frozen_at)
    completed_at = _optional_aware(game_final_at)

    if completed_at is not None and instant >= completed_at:
        return None
    if frozen_at is not None and instant >= frozen_at:
        if completed_at is not None and completed_at > instant:
            return LifecycleTarget(LifecycleStage.POSTGAME, completed_at)
        return LifecycleTarget(LifecycleStage.POSTGAME, None)
    if instant >= starts_at:
        if completed_at is not None and completed_at > instant:
            return LifecycleTarget(LifecycleStage.POSTGAME, completed_at)
        return LifecycleTarget(LifecycleStage.POSTGAME, None)

    for target in lifecycle_targets(starts_at):
        if target.target_at is not None and target.target_at > instant:
            return target

    # T_MINUS_10M refresh is followed by an evidence-triggered freeze. It does
    # not get a fabricated timestamp distinct from the successful publication.
    return LifecycleTarget(LifecycleStage.FINAL, None)


def pregame_publication_allowed(
    game_starts_at: datetime,
    as_of: datetime,
    *,
    final_forecast_frozen_at: datetime | None = None,
    game_final_at: datetime | None = None,
) -> bool:
    """Close publication at the first final freeze and always at first pitch."""

    starts_at = _aware(game_starts_at)
    instant = _aware(as_of)
    frozen_at = _optional_aware(final_forecast_frozen_at)
    completed_at = _optional_aware(game_final_at)
    return (
        instant < starts_at
        and (frozen_at is None or instant < frozen_at)
        and (completed_at is None or instant < completed_at)
    )


def evaluate_lifecycle(
    game_starts_at: datetime,
    as_of: datetime,
    *,
    final_forecast_frozen_at: datetime | None = None,
    game_final_at: datetime | None = None,
) -> LifecycleState:
    starts_at = _aware(game_starts_at)
    instant = _aware(as_of)
    frozen_at = _optional_aware(final_forecast_frozen_at)
    return LifecycleState(
        stage=classify_lifecycle(
            starts_at,
            instant,
            final_forecast_frozen_at=frozen_at,
            game_final_at=game_final_at,
        ),
        game_starts_at=starts_at,
        evaluated_at=instant,
        next_target=next_lifecycle_target(
            starts_at,
            instant,
            final_forecast_frozen_at=frozen_at,
            game_final_at=game_final_at,
        ),
        pregame_publication_allowed=pregame_publication_allowed(
            starts_at,
            instant,
            final_forecast_frozen_at=frozen_at,
            game_final_at=game_final_at,
        ),
        final_forecast_frozen=frozen_at is not None and instant >= frozen_at,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Lifecycle timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _optional_aware(value: datetime | None) -> datetime | None:
    return _aware(value) if value is not None else None
