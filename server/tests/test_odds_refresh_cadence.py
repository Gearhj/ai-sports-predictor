"""Odds refresh cadence: the exactly-once reservation deduplicates within a
refresh window while the configurable daily budget caps the number of windows.
This keeps the default (budget 1) at one call per day while allowing a deployer
to run a pregame cadence — without weakening the quota protections."""

from __future__ import annotations

import pytest

from app.data_sources import SourceError
from app.jobs.orchestrator import JobOrchestrator


def _reserve(orch: JobOrchestrator, window: int):
    return orch._reserve_provider_request(
        "THE_ODDS_API", "baseball_mlb_odds",
        controlled_live_request=False,
        request_shape={"regions": "us", "markets": ["h2h"], "request_window": window},
    )


def test_reservation_dedups_within_window_but_allows_next_window(session, settings):
    orch = JobOrchestrator(session, settings)
    first = _reserve(orch, 1)
    session.commit()
    # Same window → duplicate reservation is rejected (idempotency guard).
    with pytest.raises(SourceError):
        _reserve(orch, 1)
    session.rollback()
    # The next refresh window is allowed — the cadence is not blocked all day.
    second = _reserve(orch, 2)
    session.commit()
    assert first.id != second.id


def test_daily_budget_still_caps_calls(session, settings):
    capped = settings.model_copy(update={"odds_daily_request_budget": 1})
    orch = JobOrchestrator(session, capped)
    _reserve(orch, 1)
    session.commit()
    # One RESERVED row today already meets the default budget of 1 → the next
    # refresh is refused by the quota policy, not by the per-window fingerprint.
    with pytest.raises(SourceError):
        orch._enforce_odds_quota_policy()
