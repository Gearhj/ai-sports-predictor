from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.ingestion.normalizer import CanonicalNormalizer
from app.jobs.scheduler import build_scheduler
from app.models import Game, GameRefreshState, GameStatus
from app.services.game_refresh_state_service import GameRefreshStateService


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _game(session, starts_at: datetime) -> Game:
    _, league, season, _ = CanonicalNormalizer(session).ensure_core(starts_at.year)
    game = Game(
        league_id=league.id,
        season_id=season.id,
        starts_at=starts_at,
        status=GameStatus.SCHEDULED,
        scheduled_innings=9,
        source_mode="LIVE",
    )
    session.add(game)
    session.commit()
    return game


def test_selector_persists_only_material_append_only_transitions(session) -> None:
    game = _game(session, NOW + timedelta(hours=10))
    service = GameRefreshStateService(session)

    morning = service.record_due_states(as_of=NOW)
    session.commit()
    assert len(morning) == 1
    assert morning[0].game_id == game.id
    assert morning[0].lifecycle_stage == "MORNING"
    assert morning[0].next_scheduled_refresh_at.replace(tzinfo=UTC) == (
        game.starts_at.replace(tzinfo=UTC) - timedelta(hours=6)
    )

    assert service.record_due_states(as_of=NOW + timedelta(hours=1)) == []
    session.commit()
    assert session.scalar(select(func.count()).select_from(GameRefreshState)) == 1

    at_six_hours = game.starts_at.replace(tzinfo=UTC) - timedelta(hours=6)
    transition = service.record_due_states(as_of=at_six_hours)
    session.commit()
    assert [state.lifecycle_stage for state in transition] == ["T_MINUS_6H"]
    assert session.scalar(select(func.count()).select_from(GameRefreshState)) == 2
    assert morning[0].lifecycle_stage == "MORNING"


def test_reschedule_in_same_stage_appends_new_first_pitch_evidence(session) -> None:
    game = _game(session, NOW + timedelta(hours=5))
    service = GameRefreshStateService(session)
    first = service.record_due_states(as_of=NOW)
    session.commit()
    assert first[0].lifecycle_stage == "T_MINUS_6H"

    game.starts_at = game.starts_at.replace(tzinfo=UTC) + timedelta(minutes=30)
    session.commit()
    rescheduled = service.record_due_states(as_of=NOW + timedelta(minutes=1))
    session.commit()
    assert len(rescheduled) == 1
    assert rescheduled[0].lifecycle_stage == "T_MINUS_6H"
    assert rescheduled[0].first_pitch_at.replace(tzinfo=UTC) == game.starts_at.replace(tzinfo=UTC)


def test_doubleheader_games_receive_independent_states(session) -> None:
    first = _game(session, NOW + timedelta(minutes=45))
    second = _game(session, NOW + timedelta(hours=4))
    states = GameRefreshStateService(session).record_due_states(as_of=NOW)
    session.commit()
    by_game = {state.game_id: state.lifecycle_stage for state in states}
    assert by_game[first.id] == "T_MINUS_60M"
    assert by_game[second.id] == "T_MINUS_6H"


def test_first_pitch_records_final_missed_without_dispatching_work(session) -> None:
    game = _game(session, NOW + timedelta(minutes=5))
    service = GameRefreshStateService(session)
    before = service.record_due_states(as_of=NOW)
    session.commit()
    assert before[0].final_forecast_status == "PENDING_FREEZE"

    at_first_pitch = service.record_due_states(as_of=game.starts_at.replace(tzinfo=UTC))
    session.commit()
    assert at_first_pitch[0].lifecycle_stage == "FINAL"
    assert at_first_pitch[0].final_forecast_status == "MISSED"
    assert at_first_pitch[0].final_snapshot_frozen is False
    assert at_first_pitch[0].time_to_first_pitch_seconds == 0


def test_event_relative_scheduler_requires_both_flags(settings) -> None:
    job_id = "asp-event-relative-selector"
    combinations = (
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, True),
    )
    for scheduler_enabled, event_enabled, expected in combinations:
        configured = settings.model_copy(
            update={
                "scheduler_enabled": scheduler_enabled,
                "event_relative_scheduler_enabled": event_enabled,
            }
        )
        scheduler = build_scheduler(configured)
        assert (job_id in {job.id for job in scheduler.get_jobs()}) is expected


def test_demo_and_far_future_games_are_not_selected(session) -> None:
    demo = _game(session, NOW + timedelta(hours=2))
    demo.source_mode = "DEMO"
    far_future = _game(session, NOW + timedelta(hours=13))
    session.commit()

    states = GameRefreshStateService(session).record_due_states(as_of=NOW)
    session.commit()
    assert states == []
    assert session.scalar(select(func.count()).select_from(GameRefreshState)) == 0
