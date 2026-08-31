from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.core.time import local_day_bounds, local_today
from app.jobs import scheduler as scheduler_module
from app.jobs.scheduler import _scheduled_event_dates, build_scheduler
from app.models.entities import GameStatus


def test_eastern_today_does_not_flip_at_midnight_utc() -> None:
    instant = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)
    assert local_today("America/New_York", instant) == date(2026, 8, 12)
    start, end = local_day_bounds(date(2026, 8, 12), "America/New_York")
    assert start == datetime(2026, 8, 12, 4, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 13, 4, 0, tzinfo=UTC)


def test_scheduler_registration_is_quota_safe(settings) -> None:
    # Pin every optional feature off so a developer's local .env (NFL shadow,
    # observer, live shadow) cannot leak extra registrations into the baseline.
    baseline = settings.model_copy(update={
        "live_shadow_enabled": False,
        "nfl_shadow_enabled": False,
        "prediction_market_observer_enabled": False,
        "event_relative_scheduler_enabled": False,
        "odds_polling_enabled": False,
    })
    disabled_odds = build_scheduler(baseline)
    assert {job.id for job in disabled_odds.get_jobs()} == {"asp-daily-pipeline", "asp-finalize-games"}
    enabled = baseline.model_copy(update={"odds_polling_enabled": True})
    with_odds = build_scheduler(enabled)
    assert {job.id for job in with_odds.get_jobs()} == {"asp-daily-pipeline", "asp-finalize-games", "asp-odds-poll"}

    live_shadow = baseline.model_copy(update={"live_shadow_enabled": True})
    with_shadow = {job.id for job in build_scheduler(live_shadow).get_jobs()}
    assert with_shadow == {
        "asp-daily-pipeline",
        "asp-finalize-games",
        "asp-shadow-experiment-freeze",
        "asp-availability-poll",
    }


def test_scheduler_registers_all_observer_jobs_when_armed(settings) -> None:
    observer = settings.model_copy(update={
        "live_shadow_enabled": False,
        "nfl_shadow_enabled": False,
        "prediction_market_observer_enabled": True,
        "event_relative_scheduler_enabled": False,
        "odds_polling_enabled": False,
    })
    job_ids = {job.id for job in build_scheduler(observer).get_jobs()}
    assert {
        "asp-pm-discover",
        "asp-pm-refresh",
        "asp-pm-consensus",
        "asp-pm-alerts",
        "asp-pm-finalize-pregame",
    } <= job_ids


def test_finalize_carries_an_incomplete_prior_local_slate(settings, monkeypatch) -> None:
    today = date(2026, 8, 29)
    previous = date(2026, 8, 28)
    monkeypatch.setattr(scheduler_module, "local_today", lambda _timezone: today)

    orchestrator = SimpleNamespace(
        _games_for_date=lambda event_date: (
            [SimpleNamespace(status=GameStatus.LIVE)] if event_date == previous else []
        )
    )

    assert _scheduled_event_dates("finalize-games", orchestrator, settings) == (
        previous,
        today,
    )


def test_finalize_does_not_reopen_a_terminal_prior_slate(settings, monkeypatch) -> None:
    today = date(2026, 8, 29)
    monkeypatch.setattr(scheduler_module, "local_today", lambda _timezone: today)
    orchestrator = SimpleNamespace(
        _games_for_date=lambda _event_date: [
            SimpleNamespace(status=GameStatus.FINAL),
            SimpleNamespace(status=GameStatus.CANCELED),
        ]
    )

    assert _scheduled_event_dates("finalize-games", orchestrator, settings) == (today,)


def test_nonfinalization_jobs_remain_on_current_local_date(settings, monkeypatch) -> None:
    today = date(2026, 8, 29)
    monkeypatch.setattr(scheduler_module, "local_today", lambda _timezone: today)
    orchestrator = SimpleNamespace(
        _games_for_date=lambda _event_date: (_ for _ in ()).throw(
            AssertionError("non-finalization jobs must not inspect another slate")
        )
    )

    assert _scheduled_event_dates("freeze-shadow-experiment", orchestrator, settings) == (
        today,
    )
