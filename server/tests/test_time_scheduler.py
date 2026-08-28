from __future__ import annotations

from datetime import UTC, date, datetime

from app.core.time import local_day_bounds, local_today
from app.jobs.scheduler import build_scheduler


def test_eastern_today_does_not_flip_at_midnight_utc() -> None:
    instant = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)
    assert local_today("America/New_York", instant) == date(2026, 8, 12)
    start, end = local_day_bounds(date(2026, 8, 12), "America/New_York")
    assert start == datetime(2026, 8, 12, 4, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 13, 4, 0, tzinfo=UTC)


def test_scheduler_registration_is_quota_safe(settings) -> None:
    baseline = settings.model_copy(update={"live_shadow_enabled": False})
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
