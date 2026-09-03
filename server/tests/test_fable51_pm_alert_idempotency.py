"""Regression: prediction-market alert evaluation must not self-destruct on an
in-batch duplicate idempotency key.

Root cause (observed in the live ``job_runs`` table: 286 of 332
``evaluate-prediction-market-alerts`` runs FAILED in three days with
``UniqueViolation … uq_pm_alert_idempotency``): ``_latest_snapshots_by_provider``
returns one snapshot per (provider, market_type, selection, line_value), but the
alert idempotency key omits ``line_value``.  Two TOTAL lines for the same
selection therefore map onto one key inside a single evaluation.  Because the
session is ``autoflush=False``, ``_exists`` cannot see the first pending row and
the second ``session.add`` violates the unique constraint on flush — failing
the whole job and discarding every alert it had produced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.prediction_market import PredictionMarketAlert, PredictionMarketContract, PredictionMarketSnapshot
from app.prediction_markets.service import PredictionMarketObserver
from tests.test_prediction_market_observer import _contract, _seed_game


def _thin_snapshot(session, contract, game, *, line_value: float, observed: datetime) -> PredictionMarketSnapshot:
    snap = PredictionMarketSnapshot(
        provider=contract.provider,
        contract_id=contract.id,
        provider_market_id=contract.provider_market_id,
        provider_outcome_id=contract.provider_outcome_id,
        canonical_game_id=game.id,
        market_type="FULL_GAME_TOTAL",
        selection="UNDER",
        line_value=line_value,
        best_bid=0.40,
        best_ask=0.45,
        midpoint=0.425,
        top_bid_size=5.0,
        top_ask_size=5.0,
        provider_observed_at=observed,
        aisp_received_at=observed,
        game_starts_at=game.starts_at,
        pregame_eligible=True,
        in_play=False,
        quality_tier="C",
        quality_components={"quote_age": 1},
        quality_flags=["THIN_DEPTH"],
        mapping_status="EXACT",
        semantics_status="SEMANTICS_MATCHED",
        book_complete=True,
        quote_fresh=True,
        market_liquid=False,
        raw_payload_sha256="0" * 64,
        idempotency_key=f"test:{contract.provider}:{line_value}:{observed.timestamp()}",
    )
    session.add(snap)
    session.flush()
    return snap


@pytest.mark.asyncio
async def test_two_lines_same_selection_produce_one_alert_and_no_unique_violation(session, settings):
    start = datetime.now(UTC) + timedelta(hours=3)
    game, _, _ = _seed_game(session, start=start)
    # The operational ``SessionLocal`` is autoflush=False; the test fixture's
    # default autoflush=True would let ``_exists`` see the pending row and mask
    # the live defect. Pin the operational setting so the test reproduces it.
    session.autoflush = False
    observer = PredictionMarketObserver(session, settings)
    observer.ingest_contracts([_contract("e-total", start=start)])
    contract = session.scalar(select(PredictionMarketContract))
    now = datetime.now(UTC)
    # Same provider / market_type / selection, two distinct lines -> two
    # "latest" snapshots -> one alert idempotency key.
    _thin_snapshot(session, contract, game, line_value=8.5, observed=now - timedelta(seconds=30))
    _thin_snapshot(session, contract, game, line_value=9.0, observed=now - timedelta(seconds=20))
    session.commit()

    # Before the repair this raised IntegrityError (UniqueViolation) on commit.
    written = observer.evaluate_alerts(game.id, now=now)

    assert written == 1
    assert session.scalar(select(func.count()).select_from(PredictionMarketAlert)) == 1
    alert = session.scalar(select(PredictionMarketAlert))
    assert alert.alert_type == "THIN_MARKET_WARNING"
    assert alert.market_type == "FULL_GAME_TOTAL" and alert.selection == "UNDER"


@pytest.mark.asyncio
async def test_re_evaluation_in_same_window_is_idempotent(session, settings):
    start = datetime.now(UTC) + timedelta(hours=3)
    game, _, _ = _seed_game(session, start=start)
    # The operational ``SessionLocal`` is autoflush=False; the test fixture's
    # default autoflush=True would let ``_exists`` see the pending row and mask
    # the live defect. Pin the operational setting so the test reproduces it.
    session.autoflush = False
    observer = PredictionMarketObserver(session, settings)
    observer.ingest_contracts([_contract("e-total-2", start=start)])
    contract = session.scalar(select(PredictionMarketContract))
    now = datetime.now(UTC)
    _thin_snapshot(session, contract, game, line_value=8.5, observed=now - timedelta(seconds=30))
    session.commit()

    first = observer.evaluate_alerts(game.id, now=now)
    # A fresh observer (new job run) inside the same 5-minute bucket must see the
    # persisted key and write nothing new.
    second = PredictionMarketObserver(session, settings).evaluate_alerts(game.id, now=now + timedelta(seconds=10))
    assert (first, second) == (1, 0)
    assert session.scalar(select(func.count()).select_from(PredictionMarketAlert)) == 1
