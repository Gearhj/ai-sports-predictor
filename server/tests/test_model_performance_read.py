"""Metric definitions behind the consolidated Model Performance read model."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import log
from types import SimpleNamespace

import pytest

from app.services.forecast_evaluation import CLOCK_UNRESOLVED, CLOCK_VERIFIED
from app.services.model_performance_read import ModelPerformanceReadService, _auc


def _row(home: float, outcome: str, *, day: int = 0, tie: float = 0.0, market: float | None = None,
         clock: str = CLOCK_VERIFIED):
    away = 1.0 - home - tie
    probs = {"home": home, "away": away, "tie": tie}
    actual = {"HOME": 1.0, "AWAY": 0.0, "TIE": 0.0}
    brier = sum((probs[k] - (1.0 if k.upper() == outcome else 0.0)) ** 2 for k in probs)
    p_actual = probs[outcome.lower()]
    return SimpleNamespace(
        detail={"published_probabilities": probs, "publication_clock": {"classification": clock}},
        actual_outcome=outcome, winner_correct=(home >= away) == (outcome == "HOME") and outcome != "TIE",
        log_loss=-log(p_actual), brier_score=brier, margin_error=1.0 - actual[outcome], total_error=-2.0,
        kickoff_utc=datetime(2026, 9, 10, 23, tzinfo=UTC) + timedelta(days=day),
        market_available=market is not None,
        market_evidence={"markets": {"MONEYLINE": {"available": True, "no_vig_home_probability": market}}}
        if market is not None else None,
    )


def test_two_way_brier_is_half_the_stored_three_class_brier_without_a_tie() -> None:
    rows = [_row(0.6, "HOME"), _row(0.7, "AWAY"), _row(0.45, "HOME")]
    metrics = ModelPerformanceReadService(None)._metrics(rows)  # type: ignore[arg-type]
    assert metrics["brier_two_way"] == pytest.approx(metrics["brier_three_class"] / 2)
    assert metrics["n"] == 3 and metrics["two_way_n"] == 3


def test_market_delta_uses_only_games_with_a_market_and_the_same_two_way_scoring() -> None:
    rows = [_row(0.6, "HOME", market=0.5), _row(0.7, "AWAY")]
    market = ModelPerformanceReadService(None)._metrics(rows)["market"]  # type: ignore[arg-type]
    assert market["n"] == 1
    assert market["model_brier_two_way"] == pytest.approx(0.16)
    assert market["market_brier_two_way"] == pytest.approx(0.25)
    assert market["delta_brier"] == pytest.approx(-0.09)


def test_ties_are_excluded_from_two_way_metrics_but_counted_in_n() -> None:
    rows = [_row(0.5, "HOME", tie=0.04), _row(0.4, "TIE", tie=0.04)]
    metrics = ModelPerformanceReadService(None)._metrics(rows)  # type: ignore[arg-type]
    assert metrics["n"] == 2 and metrics["two_way_n"] == 1


def test_auc_is_undefined_until_both_classes_occur() -> None:
    assert _auc([(0.6, True), (0.7, True)]) is None
    assert _auc([(0.6, True), (0.4, False)]) == 1.0
    assert _auc([(0.5, True), (0.5, False)]) == 0.5


def test_calibration_and_series_carry_their_own_counts() -> None:
    rows = [_row(0.62, "HOME", day=0), _row(0.66, "AWAY", day=0), _row(0.31, "AWAY", day=1)]
    metrics = ModelPerformanceReadService(None)._metrics(rows)  # type: ignore[arg-type]
    assert sum(b["n"] for b in metrics["calibration"]) == 3
    six = next(b for b in metrics["calibration"] if b["lower"] == pytest.approx(0.6))
    assert six["n"] == 2 and six["observed"] == 0.5
    assert [p["cumulative_n"] for p in metrics["series"]] == [2, 3]


def test_cohorts_separate_unresolved_publication_clocks() -> None:
    rows = [_row(0.6, "HOME"), _row(0.6, "HOME", clock=CLOCK_UNRESOLVED)]
    rows[0].model_label = rows[1].model_label = "M"
    for row in rows:
        row.scoring_version, row.snapshot_type, row.canonical_game_id = "S", "PROSPECTIVE", None
        row.id, row.home_score, row.away_score, row.predicted_outcome = "x", 3, 2, "HOME"
        row.predicted_home_score = row.predicted_away_score = None
        row.published_before_kickoff_seconds = 3600
    model = ModelPerformanceReadService(None)._model("E", "v1", rows, {})  # type: ignore[arg-type]
    assert model["cohorts"]["ALL"]["n"] == 2
    assert model["cohorts"]["VERIFIED_PUBLICATION"]["n"] == 1
