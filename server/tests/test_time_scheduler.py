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


#: The only registration that can spend a provider credit. Quota safety is a
#: statement about this job, not about how many jobs exist.
PROVIDER_SPENDING_JOBS = {"asp-odds-poll"}

#: Jobs that appear only when their feature flag is on.
LIVE_SHADOW_JOBS = {"asp-dc-shadow-experiment-freeze", "asp-availability-poll"}


def test_scheduler_registration_is_quota_safe(settings) -> None:
    """Nothing that can spend a provider credit is registered while polling is off.

    This used to compare the whole registration against a frozen list, which
    made it fail whenever any unrelated no-cost job was added — it had already
    drifted by four jobs before the season-account opener existed. Asserting
    the flag-gated deltas says what the test is actually for, and does not
    silently bless a job nobody reviewed.
    """

    # Pin every optional feature off so a developer's local .env (NFL shadow,
    # observer, live shadow) cannot leak extra registrations into the baseline.
    baseline = settings.model_copy(update={
        "live_shadow_enabled": False,
        "nfl_shadow_enabled": False,
        "prediction_market_observer_enabled": False,
        "event_relative_scheduler_enabled": False,
        "odds_polling_enabled": False,
    })
    baseline_ids = {job.id for job in build_scheduler(baseline).get_jobs()}
    assert baseline_ids & PROVIDER_SPENDING_JOBS == set()
    assert baseline_ids & LIVE_SHADOW_JOBS == set()
    assert "asp-daily-pipeline" in baseline_ids
    assert "asp-finalize-games" in baseline_ids

    enabled = baseline.model_copy(update={"odds_polling_enabled": True})
    with_odds = {job.id for job in build_scheduler(enabled).get_jobs()}
    # Turning polling on adds exactly the polling job and nothing else.
    assert with_odds - baseline_ids == {"asp-odds-poll"}

    live_shadow = baseline.model_copy(update={"live_shadow_enabled": True})
    with_shadow = {job.id for job in build_scheduler(live_shadow).get_jobs()}
    assert with_shadow - baseline_ids == LIVE_SHADOW_JOBS
    assert with_shadow & PROVIDER_SPENDING_JOBS == set()


def test_season_account_opening_is_registered_and_spends_nothing(settings) -> None:
    """The season opener runs on the existing scheduler, with no second scheduler.

    It is unconditional on purpose: opening a season account is accounting, not
    an optional feature, and it makes no provider request.
    """

    baseline = settings.model_copy(update={
        "live_shadow_enabled": False,
        "nfl_shadow_enabled": False,
        "prediction_market_observer_enabled": False,
        "event_relative_scheduler_enabled": False,
        "odds_polling_enabled": False,
    })
    ids = {job.id for job in build_scheduler(baseline).get_jobs()}
    assert "asp-season-account-opening" in ids
    assert ids & PROVIDER_SPENDING_JOBS == set()


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
        _games_for_date=lambda event_date, _league: (
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
        _games_for_date=lambda _event_date, _league: [
            SimpleNamespace(status=GameStatus.FINAL),
            SimpleNamespace(status=GameStatus.CANCELED),
        ]
    )

    assert _scheduled_event_dates("finalize-games", orchestrator, settings) == (today,)


def test_nonfinalization_jobs_remain_on_current_local_date(settings, monkeypatch) -> None:
    today = date(2026, 8, 29)
    monkeypatch.setattr(scheduler_module, "local_today", lambda _timezone: today)
    orchestrator = SimpleNamespace(
        _games_for_date=lambda _event_date, _league: (_ for _ in ()).throw(
            AssertionError("non-finalization jobs must not inspect another slate")
        )
    )

    assert _scheduled_event_dates("freeze-shadow-experiment", orchestrator, settings) == (
        today,
    )


# -- an unplayed "if necessary" game MLB dropped from its schedule ------------
#
# efc2eb2f (ALCS game, Fri 2026-10-16 21:00 ET) was last listed 2026-10-15
# 21:25 ET and then dropped by MLB. It stayed SCHEDULED, so finalize-games kept
# re-fetching 2026-10-16 all of 2026-10-17 -- the fetches that kept writing the
# postponed Friday copy of the rained-out game every 30 minutes.

def _schedule_world(session, *, sibling_last_seen: datetime, own_last_seen: datetime):
    from uuid import uuid4

    from app.models import Game, GameStateObservation

    def game(starts, status=GameStatus.SCHEDULED):
        row = Game(league_id=uuid4(), starts_at=starts, status=status, source_mode="LIVE")
        session.add(row)
        session.flush()
        return row

    def seen(row, available, scheduled):
        session.add(GameStateObservation(
            game_id=row.id, scheduled_at=scheduled, status="scheduled", is_postponed=False,
            observed_at=available, available_as_of=available, source="MLB_STATS_API", source_mode="LIVE",
            payload_checksum=uuid4().hex))

    if_necessary = game(datetime(2026, 10, 17, 1, 0, tzinfo=UTC))        # 21:00 ET Fri 10-16
    sibling = game(datetime(2026, 10, 17, 0, 0, tzinfo=UTC), GameStatus.FINAL)   # 20:00 ET Fri 10-16
    seen(if_necessary, own_last_seen, if_necessary.starts_at)
    seen(sibling, datetime(2026, 10, 15, 1, 0, tzinfo=UTC), sibling.starts_at)
    seen(sibling, sibling_last_seen, sibling.starts_at)
    session.commit()
    return if_necessary, sibling


def _finalize_dates(session, settings, monkeypatch, games, *, with_session=True):
    today, previous = date(2026, 10, 17), date(2026, 10, 16)
    monkeypatch.setattr(scheduler_module, "local_today", lambda _timezone: today)
    orchestrator = SimpleNamespace(
        _games_for_date=lambda event_date, _league: games if event_date == previous else [],
        **({"session": session} if with_session else {}),
    )
    return _scheduled_event_dates("finalize-games", orchestrator, settings)


def test_finalize_does_not_carry_a_game_the_source_stopped_listing(session, settings, monkeypatch) -> None:
    dropped, sibling = _schedule_world(session, own_last_seen=datetime(2026, 10, 16, 1, 25, tzinfo=UTC),
                                       sibling_last_seen=datetime(2026, 10, 17, 9, 30, tzinfo=UTC))
    assert scheduler_module._dropped_from_source(session, dropped, datetime(2026, 10, 17, 12, tzinfo=UTC),
                                                 siblings=[sibling.id])
    assert _finalize_dates(session, settings, monkeypatch, [dropped, sibling]) == (date(2026, 10, 17),)


def test_a_game_alone_on_its_date_is_never_treated_as_dropped(session, settings, monkeypatch) -> None:
    """Siblings are the other games of the date (the indexed lookup); none means no evidence of a drop."""

    dropped, _sibling = _schedule_world(session, own_last_seen=datetime(2026, 10, 16, 1, 25, tzinfo=UTC),
                                        sibling_last_seen=datetime(2026, 10, 17, 9, 30, tzinfo=UTC))
    assert not scheduler_module._dropped_from_source(session, dropped, datetime(2026, 10, 17, 12, tzinfo=UTC),
                                                     siblings=[])
    assert _finalize_dates(session, settings, monkeypatch, [dropped]) == (date(2026, 10, 16), date(2026, 10, 17))


def test_finalize_still_carries_a_game_the_source_still_lists(session, settings, monkeypatch) -> None:
    listed, sibling = _schedule_world(session, own_last_seen=datetime(2026, 10, 17, 9, 0, tzinfo=UTC),
                                      sibling_last_seen=datetime(2026, 10, 17, 9, 30, tzinfo=UTC))
    assert _finalize_dates(session, settings, monkeypatch, [listed, sibling]) == (
        date(2026, 10, 16), date(2026, 10, 17))


def test_finalize_still_carries_through_a_provider_outage(session, settings, monkeypatch) -> None:
    """Nothing was observed for anyone since: an outage never looks like a drop."""

    starved, sibling = _schedule_world(session, own_last_seen=datetime(2026, 10, 16, 1, 25, tzinfo=UTC),
                                       sibling_last_seen=datetime(2026, 10, 16, 1, 30, tzinfo=UTC))
    assert _finalize_dates(session, settings, monkeypatch, [starved, sibling]) == (
        date(2026, 10, 16), date(2026, 10, 17))


def test_finalize_without_a_session_keeps_the_previous_rule(session, settings, monkeypatch) -> None:
    dropped, sibling = _schedule_world(session, own_last_seen=datetime(2026, 10, 16, 1, 25, tzinfo=UTC),
                                       sibling_last_seen=datetime(2026, 10, 17, 9, 30, tzinfo=UTC))
    assert _finalize_dates(session, settings, monkeypatch, [dropped, sibling], with_session=False) == (
        date(2026, 10, 16), date(2026, 10, 17))
