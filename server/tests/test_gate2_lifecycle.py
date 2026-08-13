from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.features.lifecycle import (
    LifecycleStage,
    classify_lifecycle,
    evaluate_lifecycle,
    lifecycle_targets,
    next_lifecycle_target,
    pregame_publication_allowed,
)


START = datetime(2026, 8, 12, 23, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(hours=24), LifecycleStage.MORNING),
        (timedelta(hours=12), LifecycleStage.MORNING),
        (timedelta(hours=6), LifecycleStage.T_MINUS_6H),
        (timedelta(hours=3), LifecycleStage.T_MINUS_3H),
        (timedelta(minutes=60), LifecycleStage.T_MINUS_60M),
        (timedelta(minutes=30), LifecycleStage.T_MINUS_30M),
        (timedelta(minutes=10), LifecycleStage.T_MINUS_10M),
    ],
)
def test_timed_stage_classification_is_inclusive_at_each_boundary(
    offset: timedelta, expected: LifecycleStage
) -> None:
    assert classify_lifecycle(START, START - offset) == expected


def test_next_target_is_strictly_after_the_evaluation_instant() -> None:
    before_morning = START - timedelta(hours=18)
    assert next_lifecycle_target(START, before_morning).stage == LifecycleStage.MORNING
    assert (
        next_lifecycle_target(START, START - timedelta(hours=12)).stage
        == LifecycleStage.T_MINUS_6H
    )
    assert next_lifecycle_target(START, START - timedelta(minutes=9)).stage == LifecycleStage.FINAL
    assert next_lifecycle_target(START, START - timedelta(minutes=9)).target_at is None


def test_final_freeze_closes_publication_and_persists_through_live_game() -> None:
    frozen_at = START - timedelta(minutes=8)
    state = evaluate_lifecycle(
        START,
        START - timedelta(minutes=5),
        final_forecast_frozen_at=frozen_at,
    )
    assert state.stage == LifecycleStage.FINAL
    assert state.final_forecast_frozen is True
    assert state.pregame_publication_allowed is False

    live_state = evaluate_lifecycle(
        START,
        START + timedelta(hours=1),
        final_forecast_frozen_at=frozen_at,
    )
    assert live_state.stage == LifecycleStage.FINAL
    assert live_state.next_target.stage == LifecycleStage.POSTGAME


def test_first_pitch_closes_pregame_publication_even_without_freeze_record() -> None:
    assert pregame_publication_allowed(START, START - timedelta(microseconds=1)) is True
    assert pregame_publication_allowed(START, START) is False
    assert classify_lifecycle(START, START) == LifecycleStage.FINAL


def test_observed_final_game_timestamp_enters_postgame() -> None:
    final_at = START + timedelta(hours=3)
    assert classify_lifecycle(START, final_at, game_final_at=final_at) == LifecycleStage.POSTGAME
    assert next_lifecycle_target(START, final_at, game_final_at=final_at) is None


def test_reschedules_recompute_every_target_from_that_games_start() -> None:
    original = lifecycle_targets(START)
    delayed_start = START + timedelta(hours=2, minutes=15)
    delayed = lifecycle_targets(delayed_start)
    assert [target.stage for target in original] == [target.stage for target in delayed]
    assert all(
        delayed_target.target_at - original_target.target_at == timedelta(hours=2, minutes=15)
        for original_target, delayed_target in zip(original, delayed)
    )


def test_doubleheader_games_are_classified_independently() -> None:
    first_start = START
    second_start = START + timedelta(hours=4)
    instant = START - timedelta(minutes=45)
    assert classify_lifecycle(first_start, instant) == LifecycleStage.T_MINUS_60M
    assert classify_lifecycle(second_start, instant) == LifecycleStage.T_MINUS_6H


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        classify_lifecycle(START.replace(tzinfo=None), START)
