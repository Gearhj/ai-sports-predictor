"""Qualification funnel + policy report + Market Pulse health-state tests.

Read-only: the funnel/policy never create a position, never mutate a forecast,
and never loosen a threshold.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import Forecast, OfficialAspPosition
from app.prediction_markets.read_service import PredictionMarketReadService
from app.services.qualification_service import FUNNEL_GATES, QualificationService


# -- exact policy report -----------------------------------------------------

def test_policy_report_matches_code_and_config(session, settings):
    report = QualificationService(session, settings).policy_report()
    assert report["experiment_supported_market_types"] == ["MONEYLINE"]
    assert "SPREAD" in report["board_supported_market_types"] and "TOTAL" in report["board_supported_market_types"]
    assert report["readiness_threshold"] == settings.opportunity_qualified_readiness == 70.0
    assert report["qualified_edge_uncertainty_adjusted"] == settings.shadow_qualified_edge == 0.02
    assert report["extreme_disagreement_threshold"] == settings.opportunity_extreme_disagreement_threshold == 0.15
    assert report["final_pregame_cutoff_minutes"] == 15
    assert report["quote_freshness_minutes_board"] == 240
    st = report["staking"]
    assert st["kelly_fraction"] == 0.25 and st["position_cap_percentage"] == 0.01
    assert st["same_game_cap_percentage"] == 0.02 and st["daily_cap_percentage"] == 0.05
    assert st["zero_kelly_behavior"].startswith("non-positive edge")


# -- funnel reconciliation (empty slate) -------------------------------------

def test_funnel_reconciles_and_creates_nothing(session, settings):
    before_positions = session.scalar(select(func.count()).select_from(OfficialAspPosition))
    before_forecasts = session.scalar(select(func.count()).select_from(Forecast))
    funnel = QualificationService(session, settings).funnel(date(2026, 8, 30))
    # Funnel counts are monotonic non-increasing down the gate chain.
    counts = [s["count"] for s in funnel["steps"] if s["key"] not in {"positions_created"}]
    assert counts == sorted(counts, reverse=True)
    # Qualified never exceeds any survived gate; positions never exceed qualified.
    assert funnel["qualified_selections"] <= funnel["steps"][0]["count"]
    assert funnel["positions_created"] <= funnel["qualified_selections"]
    assert funnel["experiment_market_types"] == ["MONEYLINE"]
    # Read-only: nothing created or mutated.
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == before_positions
    assert session.scalar(select(func.count()).select_from(Forecast)) == before_forecasts


def test_funnel_gate_order_maps_to_rejection_reasons():
    # Every gate with a reason uses one of the authoritative ordered reasons.
    reasons = {r for _, _, r in FUNNEL_GATES if r}
    assert "Latest quote is stale" in reasons
    assert "Starting pitcher probable" in reasons
    assert "Readiness below 70" in reasons


# -- Market Pulse health-state distinction (exact observer state machine) ----

def _observer_settings(settings, **extra):
    base = {
        "prediction_market_observer_enabled": True,
        "polymarket_enabled": True,
        "kalshi_enabled": True,
        "kalshi_rest_polling_enabled": True,
    }
    base.update(extra)
    return settings.model_copy(update=base)


def _record_discovery(session, provider: str, *, status, markets_seen=None, excluded=None, error=None, completed_at=None):
    from datetime import UTC, datetime

    from app.models import IngestionRun

    now = completed_at or datetime.now(UTC)
    meta = {}
    if markets_seen is not None:
        meta["markets_seen"] = markets_seen
    if excluded is not None:
        meta["excluded"] = excluded
    run = IngestionRun(
        source=provider, job_name="discover-prediction-markets", status=status,
        started_at=now, completed_at=now, records_processed=0,
        error_message=error, source_metadata=meta,
    )
    session.add(run)
    session.commit()
    return run


def test_board_state_observer_disabled(session, settings):
    s = settings.model_copy(update={"prediction_market_observer_enabled": False})
    board = PredictionMarketReadService(session, s).board(date(2026, 8, 30))
    assert board.board_state == "OBSERVER_DISABLED"
    assert board.observer_enabled is False
    assert len(board.providers) == 2  # Polymarket + Kalshi health always reported


def test_board_state_providers_disabled(session, settings):
    s = settings.model_copy(update={
        "prediction_market_observer_enabled": True,
        "polymarket_enabled": False,
        "kalshi_enabled": False,
    })
    board = PredictionMarketReadService(session, s).board(date(2026, 8, 30))
    assert board.board_state == "PROVIDERS_DISABLED"
    assert "disabled" in board.state_reason.lower()


def test_board_state_kalshi_without_rest_polling_counts_as_disabled(session, settings):
    s = settings.model_copy(update={
        "prediction_market_observer_enabled": True,
        "polymarket_enabled": False,
        "kalshi_enabled": True,
        "kalshi_rest_polling_enabled": False,
    })
    board = PredictionMarketReadService(session, s).board(date(2026, 8, 30))
    assert board.board_state == "PROVIDERS_DISABLED"


def test_board_state_discovery_pending_before_first_run(session, settings):
    board = PredictionMarketReadService(session, _observer_settings(settings)).board(date(2026, 8, 30))
    assert board.board_state == "DISCOVERY_PENDING"
    assert "no discovery job has completed" in board.state_reason


def test_board_state_zero_raw_records_never_claims_healthy_zero(session, settings):
    from app.models import RunStatus

    _record_discovery(session, "POLYMARKET", status=RunStatus.SUCCEEDED, markets_seen=0, excluded={})
    _record_discovery(session, "KALSHI", status=RunStatus.SUCCEEDED, markets_seen=0, excluded={})
    board = PredictionMarketReadService(session, _observer_settings(settings)).board(date(2026, 8, 30))
    assert board.board_state == "DISCOVERY_PENDING"
    assert "zero provider records" in board.state_reason


def test_board_state_healthy_zero_eligible_requires_inspected_records(session, settings):
    from app.models import RunStatus

    _record_discovery(session, "POLYMARKET", status=RunStatus.SUCCEEDED,
                      markets_seen=180, excluded={"player_props": 120, "futures_or_season": 40})
    _record_discovery(session, "KALSHI", status=RunStatus.SUCCEEDED,
                      markets_seen=40, excluded={"missing_identifiers": 2})
    board = PredictionMarketReadService(session, _observer_settings(settings)).board(date(2026, 8, 30))
    assert board.board_state == "PROVIDERS_HEALTHY_ZERO_ELIGIBLE"
    assert board.rows == []
    assert "inspected 220 raw provider market(s)" in board.state_reason
    poly = next(p for p in board.providers if p.provider == "POLYMARKET")
    assert poly.raw_markets_inspected == 180
    assert poly.excluded["player_props"] == 120
    # Excluded Market Activity aggregates provider discovery exclusions.
    assert board.excluded_summary["player_props"] == 120
    assert board.excluded_summary["futures_or_season"] == 40
    assert board.excluded_summary["missing_identifiers"] == 2


def test_board_state_provider_degraded_on_failed_discovery(session, settings):
    from app.models import RunStatus

    _record_discovery(session, "POLYMARKET", status=RunStatus.FAILED, error="Polymarket request failed: HTTP 500")
    board = PredictionMarketReadService(session, _observer_settings(settings)).board(date(2026, 8, 30))
    assert board.board_state == "PROVIDER_DEGRADED"
    assert "POLYMARKET" in board.state_reason


def test_board_reports_registered_observer_jobs(session, settings):
    board = PredictionMarketReadService(session, _observer_settings(settings)).board(date(2026, 8, 30))
    names = {j.name for j in board.jobs}
    assert names == {
        "discover-prediction-markets",
        "refresh-prediction-market-snapshots",
        "compute-prediction-market-consensus",
        "evaluate-prediction-market-alerts",
        "finalize-prediction-market-pregame",
    }
    for job in board.jobs:
        assert job.cadence_minutes > 0
        # No scheduler in tests: the board must not fabricate a next run.
        assert job.scheduler_running is False
        assert job.next_run_at is None


def test_stale_discovery_is_not_degraded_right_after_scheduler_restart(session, settings, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.jobs import scheduler as sched
    from app.models import RunStatus

    # A successful discovery from 10 hours ago (well past 3x cadence).
    _record_discovery(session, "POLYMARKET", status=RunStatus.SUCCEEDED, markets_seen=100, excluded={},
                      completed_at=datetime.now(UTC) - timedelta(hours=10))
    monkeypatch.setattr(sched, "_SCHEDULER_RUNNING", True)
    monkeypatch.setattr(sched, "_SCHEDULER_INSTANCE", None)

    # Scheduler just restarted after downtime: catching up, not degraded.
    monkeypatch.setattr(sched, "_SCHEDULER_STARTED_AT", datetime.now(UTC) - timedelta(minutes=5))
    board = PredictionMarketReadService(session, _observer_settings(settings)).board(date(2026, 8, 30))
    assert board.board_state == "PROVIDERS_HEALTHY_ZERO_ELIGIBLE"

    # Scheduler has been up for hours without a discovery success: degraded.
    monkeypatch.setattr(sched, "_SCHEDULER_STARTED_AT", datetime.now(UTC) - timedelta(hours=5))
    board = PredictionMarketReadService(session, _observer_settings(settings)).board(date(2026, 8, 30))
    assert board.board_state == "PROVIDER_DEGRADED"
    assert "stale" in board.state_reason


def test_next_run_times_come_from_the_live_scheduler_never_a_projection(session, settings, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from app.jobs import scheduler as sched
    from app.models import RunStatus

    # An old last run that a last-run-plus-cadence projection would turn into
    # a next-run timestamp in the past.
    from app.models import JobRun

    session.add(JobRun(job_name="discover-prediction-markets", status=RunStatus.SUCCEEDED,
                       started_at=datetime.now(UTC) - timedelta(hours=9),
                       completed_at=datetime.now(UTC) - timedelta(hours=9), result={"records": 1}))
    session.commit()

    future = datetime.now(UTC) + timedelta(minutes=7)
    fake = SimpleNamespace(get_job=lambda job_id: SimpleNamespace(next_run_time=future) if job_id == "asp-pm-discover" else None)
    monkeypatch.setattr(sched, "_SCHEDULER_RUNNING", True)
    monkeypatch.setattr(sched, "_SCHEDULER_INSTANCE", fake)
    monkeypatch.setattr(sched, "_SCHEDULER_STARTED_AT", datetime.now(UTC))

    board = PredictionMarketReadService(session, _observer_settings(settings)).board(date(2026, 8, 30))
    discover = next(j for j in board.jobs if j.name == "discover-prediction-markets")
    assert discover.next_run_at == future  # the scheduler's real fire time
    assert board.next_discovery_at == future
    for job in board.jobs:
        # Never a fabricated timestamp: real value or honest None.
        assert job.next_run_at in (future, None)
        if job.next_run_at is not None:
            assert job.next_run_at > datetime.now(UTC)


def test_public_board_read_makes_zero_provider_calls(session, settings, monkeypatch):
    import httpx

    def _no_network(*args, **kwargs):
        raise AssertionError("public read attempted a provider network call")

    monkeypatch.setattr(httpx.AsyncClient, "get", _no_network)
    monkeypatch.setattr(httpx.Client, "get", _no_network, raising=False)
    board = PredictionMarketReadService(session, _observer_settings(settings)).board(date(2026, 8, 30))
    assert board.board_state in {"DISCOVERY_PENDING", "PROVIDERS_HEALTHY_ZERO_ELIGIBLE"}


def test_starter_gate_analysis_is_readonly(session, settings):
    before = session.scalar(select(func.count()).select_from(OfficialAspPosition))
    result = QualificationService(session, settings).starter_gate_analysis(date(2026, 8, 30))
    assert result["policy_requires_confirmed"] is True
    assert set(result["starter_states"]) == {"MISSING", "PROBABLE", "CONFIRMED"}
    assert result["probable_stable_challenger"]["additional_qualified_if_adopted"] >= 0
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == before
