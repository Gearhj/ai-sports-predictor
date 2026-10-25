"""Central operational-date authority tests.

The deterministic anchor: 2026-08-30 22:45 America/New_York == 2026-08-31 02:45
UTC. Every daily AISP surface must resolve that instant to 2026-08-30.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.core.time import local_date_of, local_day_bounds, local_today

ANCHOR_UTC = datetime(2026, 8, 31, 2, 45, tzinfo=UTC)  # 2026-08-30 22:45 ET (EDT)
EASTERN = "America/New_York"


# -- core authority ----------------------------------------------------------

def test_local_today_holds_eastern_date_during_utc_next_day_hours():
    assert local_today(EASTERN, ANCHOR_UTC) == date(2026, 8, 30)


def test_local_today_at_2359_eastern():
    # 23:59 EDT on Aug 30 == 03:59 UTC Aug 31.
    assert local_today(EASTERN, datetime(2026, 8, 31, 3, 59, tzinfo=UTC)) == date(2026, 8, 30)


def test_local_today_rolls_at_eastern_midnight_not_utc_midnight():
    assert local_today(EASTERN, datetime(2026, 8, 31, 3, 59, 59, tzinfo=UTC)) == date(2026, 8, 30)
    assert local_today(EASTERN, datetime(2026, 8, 31, 4, 0, tzinfo=UTC)) == date(2026, 8, 31)


def test_local_today_dst_spring_forward():
    # 2026-03-08: EST->EDT. 01:59 EST == 06:59 UTC; 11:30 PM EDT == 03:30 UTC Mar 9.
    assert local_today(EASTERN, datetime(2026, 3, 8, 6, 59, tzinfo=UTC)) == date(2026, 3, 8)
    assert local_today(EASTERN, datetime(2026, 3, 9, 3, 30, tzinfo=UTC)) == date(2026, 3, 8)


def test_local_today_dst_fall_back():
    # 2026-11-01: EDT->EST. 11:30 PM EST == 04:30 UTC Nov 2.
    assert local_today(EASTERN, datetime(2026, 11, 2, 4, 30, tzinfo=UTC)) == date(2026, 11, 1)


def test_configured_timezone_default_is_eastern(settings):
    # The configured fallback display timezone is America/New_York.
    assert settings.display_timezone == EASTERN
    assert local_today(settings.display_timezone, ANCHOR_UTC) == date(2026, 8, 30)


def test_local_date_of_matches_local_today():
    assert local_date_of(ANCHOR_UTC, EASTERN) == date(2026, 8, 30)


def test_local_day_bounds_cover_the_full_eastern_day():
    start, end = local_day_bounds(date(2026, 8, 30), EASTERN)
    assert start == datetime(2026, 8, 30, 4, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 31, 4, 0, tzinfo=UTC)
    assert start <= ANCHOR_UTC < end


# -- surface reconciliation: every default date is the same authority --------

def test_route_default_dates_reconcile(session, settings):
    from app.api.routes import _current_public_date, _today

    expected = local_today(settings.display_timezone)
    assert _today(None, settings) == expected
    assert _current_public_date(None, settings) == expected


def test_public_surfaces_agree_on_default_operating_date(client, settings):
    expected = local_today(settings.display_timezone).isoformat()
    pulse = client.get("/api/v1/prediction-markets/board").json()
    funnel = client.get("/api/v1/experiment/qualification-funnel").json()
    market = client.get("/api/v1/markets/board").json()
    config = client.get("/api/v1/public/config").json()
    mission = client.get("/api/v1/mission-control").json()
    assert mission["date"] == expected and mission["is_today"] is True
    assert pulse["date"] == expected
    assert funnel["date"] == expected
    assert market["date"] == expected
    assert config["operational_date"] == expected
    assert config["display_timezone"] == settings.display_timezone


def test_explicit_date_parameter_is_respected(client):
    funnel = client.get("/api/v1/experiment/qualification-funnel?date=2026-08-30").json()
    assert funnel["date"] == "2026-08-30"
    pulse = client.get("/api/v1/prediction-markets/board?date=2026-08-30").json()
    assert pulse["date"] == "2026-08-30"


def test_scheduler_daily_slate_uses_display_timezone(settings, monkeypatch):
    from app.jobs import scheduler as scheduler_module

    seen: dict[str, str] = {}

    def fake_local_today(tz_name: str, now=None):
        seen["tz"] = tz_name
        return date(2026, 8, 30)

    monkeypatch.setattr(scheduler_module, "local_today", fake_local_today)
    dates = scheduler_module._scheduled_event_dates("refresh-odds", object(), settings)
    assert dates == (date(2026, 8, 30),)
    assert seen["tz"] == settings.display_timezone


# -- odds daily budget window is the operational (Eastern) day ---------------

def _reserve(session, requested_at: datetime) -> None:
    from hashlib import sha256

    from app.models import ProviderRequestLedger

    fingerprint = sha256(requested_at.isoformat().encode()).hexdigest()
    session.add(ProviderRequestLedger(
        provider="THE_ODDS_API", endpoint_key="baseball_mlb_odds",
        requested_at=requested_at, status="RESERVED",
        request_fingerprint=fingerprint, controlled_live_request=False, source_mode="LIVE",
    ))
    session.commit()


def test_odds_budget_window_is_keyed_to_the_eastern_day(session, settings, monkeypatch):
    from app.data_sources import SourceError
    from app.jobs import orchestrator as orchestrator_module
    from app.jobs.orchestrator import JobOrchestrator

    monkeypatch.setattr(orchestrator_module, "local_today", lambda tz, now=None: date(2026, 8, 30))
    s = settings.model_copy(update={"the_odds_api_key": "test-key", "odds_daily_request_budget": 2})
    orchestrator = JobOrchestrator(session, s)

    # 00:30 UTC on Aug 30 is 20:30 ET Aug 29 — the PREVIOUS operational day.
    # Under the old UTC window this reservation stole budget from Aug 30.
    _reserve(session, datetime(2026, 8, 30, 0, 30, tzinfo=UTC))
    _reserve(session, datetime(2026, 8, 30, 15, 0, tzinfo=UTC))  # 11:00 ET Aug 30
    orchestrator._enforce_odds_quota_policy()  # 1/2 used on the ET day — allowed

    _reserve(session, datetime(2026, 8, 30, 16, 0, tzinfo=UTC))  # 12:00 ET Aug 30
    with pytest.raises(SourceError) as excinfo:
        orchestrator._enforce_odds_quota_policy()
    assert "Daily request budget reached (2/2)" in str(excinfo.value)
    assert "2026-08-30" in str(excinfo.value)


def test_odds_budget_counts_late_eastern_evening_against_the_same_slate(session, settings, monkeypatch):
    from app.data_sources import SourceError
    from app.jobs import orchestrator as orchestrator_module
    from app.jobs.orchestrator import JobOrchestrator

    monkeypatch.setattr(orchestrator_module, "local_today", lambda tz, now=None: date(2026, 8, 30))
    s = settings.model_copy(update={"the_odds_api_key": "test-key", "odds_daily_request_budget": 2})
    orchestrator = JobOrchestrator(session, s)

    # 02:30 UTC Aug 31 is 22:30 ET Aug 30 — still the Aug 30 operational day.
    _reserve(session, datetime(2026, 8, 31, 2, 30, tzinfo=UTC))
    _reserve(session, datetime(2026, 8, 30, 20, 0, tzinfo=UTC))
    with pytest.raises(SourceError):
        orchestrator._enforce_odds_quota_policy()


# -- honest scheduler-running signal -----------------------------------------

def test_scheduler_running_flag_defaults_false_and_toggles():
    from app.jobs.scheduler import (
        mark_scheduler_started,
        mark_scheduler_stopped,
        scheduler_is_running,
    )

    assert scheduler_is_running() is False
    try:
        mark_scheduler_started()
        assert scheduler_is_running() is True
    finally:
        mark_scheduler_stopped()
    assert scheduler_is_running() is False


def test_dashboard_never_reports_running_scheduler_without_runtime_start(session, settings):
    from datetime import datetime as dt

    from app.services.read_service import ReadService

    armed = settings.model_copy(update={
        "scheduler_enabled": True, "live_shadow_enabled": True, "the_odds_api_key": "test-key",
    })
    service = ReadService(session, armed)
    _, _, _, running = service._odds_freshness({}, dt.now(UTC))
    assert running is False  # configuration alone must never claim RUNNING

    from app.jobs.scheduler import mark_scheduler_started, mark_scheduler_stopped

    try:
        mark_scheduler_started()
        _, _, _, running = service._odds_freshness({}, dt.now(UTC))
        assert running is True
    finally:
        mark_scheduler_stopped()
