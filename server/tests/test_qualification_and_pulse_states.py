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


# -- Market Pulse health-state distinction (section 3) -----------------------

def test_board_state_observer_disabled(session, settings):
    s = settings.model_copy(update={"prediction_market_observer_enabled": False})
    board = PredictionMarketReadService(session, s).board(date(2026, 8, 30))
    assert board.board_state == "OBSERVER_DISABLED"
    assert board.observer_enabled is False
    assert len(board.providers) == 2  # Polymarket + Kalshi health always reported


def test_board_state_healthy_zero_markets_not_unavailable(session, settings):
    s = settings.model_copy(update={"prediction_market_observer_enabled": True})
    board = PredictionMarketReadService(session, s).board(date(2026, 8, 30))
    # Backend healthy + observer armed + no markets => healthy zero, NOT unavailable.
    assert board.board_state == "PROVIDERS_HEALTHY_ZERO_MARKETS"
    assert board.rows == []
    assert "no semantically comparable" in board.state_reason or "no discovery" in board.state_reason
    assert {p.provider for p in board.providers} == {"POLYMARKET", "KALSHI"}


def test_starter_gate_analysis_is_readonly(session, settings):
    before = session.scalar(select(func.count()).select_from(OfficialAspPosition))
    result = QualificationService(session, settings).starter_gate_analysis(date(2026, 8, 30))
    assert result["policy_requires_confirmed"] is True
    assert set(result["starter_states"]) == {"MISSING", "PROBABLE", "CONFIRMED"}
    assert result["probable_stable_challenger"]["additional_qualified_if_adopted"] >= 0
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == before
