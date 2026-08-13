from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import fmean


@dataclass(frozen=True)
class TimeAwareObservation:
    event_at: datetime
    available_as_of: datetime
    value: float
    game_id: str | None = None


def eligible_observations(
    observations: Iterable[TimeAwareObservation],
    *,
    prediction_time: datetime,
    current_game_id: str | None = None,
    window: timedelta | None = None,
) -> list[TimeAwareObservation]:
    """Return observations genuinely knowable at prediction time.

    Both event time and publication time must precede the prediction boundary;
    the game being predicted is always excluded by identity.
    """
    lower = prediction_time - window if window else None
    return sorted(
        [
            observation
            for observation in observations
            if observation.event_at < prediction_time
            and observation.available_as_of <= prediction_time
            and (current_game_id is None or observation.game_id != current_game_id)
            and (lower is None or observation.event_at >= lower)
        ],
        key=lambda observation: observation.event_at,
    )


def rolling_mean(
    observations: Iterable[TimeAwareObservation],
    *,
    prediction_time: datetime,
    days: int,
    current_game_id: str | None = None,
) -> float | None:
    eligible = eligible_observations(
        observations,
        prediction_time=prediction_time,
        current_game_id=current_game_id,
        window=timedelta(days=days),
    )
    return fmean(item.value for item in eligible) if eligible else None
