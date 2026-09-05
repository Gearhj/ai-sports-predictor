"""Pregame snapshot coverage (capture certification) contracts.

1  Every (game, provider, milestone) cell is exactly one state and the
   per-provider counts reconcile to the canonical slate.
2  A milestone whose window has not opened is NOT_DUE — never missed.
3  A captured cut is recognised by its persisted source-time label, and the
   FINAL milestone is CAPTURED only once first pitch has passed.
4  A window that passed without any capture run is MISSED_SCHEDULER_RUN; one
   whose runs recorded provider errors is FAILED_PROVIDER_CALL.
5  Games without an accepted mapping are MAPPING_UNAVAILABLE; postponed games
   STRUCTURALLY_INELIGIBLE; disabled providers NOT_APPLICABLE.
6  The read makes zero provider calls and a bounded number of SQL statements.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from app.models import GameStatus, IngestionRun, RunStatus
from app.prediction_markets.capture_coverage import MILESTONES, PROVIDERS, STATES, PregameCaptureCoverageService
from app.prediction_markets.contracts import PredictionMarketProvider
from app.prediction_markets.service import PredictionMarketObserver
from test_market_pulse_canonical_coverage import _event_date, _observer_settings
from test_prediction_market_observer import _contract, _quote, _seed_game


def _states(cov, game_id, provider) -> dict[str, str]:
    row = next(r for r in cov.rows if r.game_id == str(game_id))
    return {c.milestone: c.state for c in row.providers[provider].cells}


def _run(session, job: str, started: datetime, *, errors: int = 0) -> None:
    session.add(IngestionRun(source="PREDICTION_MARKET", job_name=job, status=RunStatus.SUCCEEDED, started_at=started,
                             completed_at=started + timedelta(seconds=5), records_processed=0,
                             source_metadata={"requests": 1, "errors": errors, "per_provider": {}}))
    session.commit()


def test_future_milestones_are_not_due_and_counts_reconcile(session, settings):
    s = _observer_settings(settings)
    now = datetime.now(UTC)
    start = now + timedelta(hours=8)
    game, _, _ = _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, s)
    obs.ingest_contracts([_contract("evt-a", start=start, market_id="0xa", token="tok-a")])
    from app.models import PredictionMarketContract
    from sqlalchemy import select

    contract = session.scalar(select(PredictionMarketContract).where(PredictionMarketContract.provider_market_id == "0xa"))
    obs.record_snapshot(contract=contract, quote=_quote("0xa", "tok-a", observed=now - timedelta(minutes=1)), now=now)

    cov = PregameCaptureCoverageService(session, s).coverage(_event_date(s, start), now=now)
    assert cov.canonical_games == 1 and cov.milestones == list(MILESTONES) and cov.providers == list(PROVIDERS)
    poly = _states(cov, game.id, "POLYMARKET")
    assert poly["FIRST_DISCOVERED"] == "CAPTURED"
    assert {poly[m] for m in ("T_MINUS_6_HOURS", "T_MINUS_2_HOURS", "T_MINUS_30_MINUTES", "T_MINUS_5_MINUTES", "FINAL_VALID_PREGAME")} == {"NOT_DUE"}
    assert all(v in STATES for v in poly.values())
    # Kalshi has no mapping for this game.
    assert set(_states(cov, game.id, "KALSHI").values()) == {"MAPPING_UNAVAILABLE"}
    for p in PROVIDERS:
        for m in MILESTONES:
            assert sum(cov.counts[p][m].values()) == cov.totals[p][m] == cov.canonical_games


def test_cut_capture_and_final_after_first_pitch(session, settings):
    s = _observer_settings(settings)
    now = datetime.now(UTC)
    start = now - timedelta(minutes=20)                       # first pitch 20 minutes ago
    game, _, _ = _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, s)
    obs.ingest_contracts([_contract("evt-b", start=start, market_id="0xb", token="tok-b")])
    from app.models import PredictionMarketContract
    from sqlalchemy import select

    contract = session.scalar(select(PredictionMarketContract).where(PredictionMarketContract.provider_market_id == "0xb"))
    # Observations at T-5h50 (labelled T-6h), T-1h50 (T-2h), T-25m (T-30m) and T-4m (T-5m); ingested promptly.
    for minutes in (350, 110, 25, 4):
        at = start - timedelta(minutes=minutes)
        obs.record_snapshot(contract=contract, quote=_quote("0xb", "tok-b", observed=at), now=at + timedelta(seconds=20))
    # An in-play observation after first pitch must not become the FINAL evidence.
    obs.record_snapshot(contract=contract, quote=_quote("0xb", "tok-b", yes=0.9, no=0.05, observed=start + timedelta(minutes=5)),
                        now=start + timedelta(minutes=5, seconds=10))
    for minutes in (355, 115, 28, 4):
        _run(session, "capture-pregame-cuts", start - timedelta(minutes=minutes))

    cov = PregameCaptureCoverageService(session, s).coverage(_event_date(s, start), now=now)
    poly = _states(cov, game.id, "POLYMARKET")
    assert poly == {"FIRST_DISCOVERED": "CAPTURED", "T_MINUS_6_HOURS": "CAPTURED", "T_MINUS_2_HOURS": "CAPTURED",
                    "T_MINUS_30_MINUTES": "CAPTURED", "T_MINUS_5_MINUTES": "CAPTURED", "FINAL_VALID_PREGAME": "CAPTURED"}
    row = next(r for r in cov.rows if r.game_id == str(game.id))
    final = next(c for c in row.providers["POLYMARKET"].cells if c.milestone == "FINAL_VALID_PREGAME")
    assert final.observed_before_start is True and final.seconds_before_start == 240 and final.midpoint != 0.925
    assert all(c.observed_before_start for c in row.providers["POLYMARKET"].cells if c.state == "CAPTURED")


def test_missed_run_failed_call_ineligible_and_disabled(session, settings):
    s = _observer_settings(settings, kalshi_enabled=False)
    now = datetime.now(UTC)
    start = now + timedelta(minutes=10)                       # first pitch in 10 minutes
    game, _, _ = _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, s)
    obs.ingest_contracts([_contract("evt-c", start=start, market_id="0xc", token="tok-c")])
    # One capture run at T-118m recorded provider errors: it sat inside the open
    # T-6h and T-2h windows (capture is idempotent, any later pregame run could
    # have captured them), so both are FAILED_PROVIDER_CALL. No run ever sat in
    # the T-30m window, which opened 20 minutes ago, so that one is
    # MISSED_SCHEDULER_RUN. The T-5m window opens in 5 minutes, the mapping was
    # decided just now, and first pitch is ahead: those are NOT_DUE, never missed.
    _run(session, "capture-pregame-cuts", start - timedelta(minutes=118), errors=3)

    cov = PregameCaptureCoverageService(session, s).coverage(_event_date(s, start), now=now)
    poly = _states(cov, game.id, "POLYMARKET")
    assert poly["T_MINUS_6_HOURS"] == "FAILED_PROVIDER_CALL"
    assert poly["T_MINUS_2_HOURS"] == "FAILED_PROVIDER_CALL"
    assert poly["T_MINUS_30_MINUTES"] == "MISSED_SCHEDULER_RUN"
    assert poly["T_MINUS_5_MINUTES"] == "NOT_DUE" and poly["FIRST_DISCOVERED"] == "NOT_DUE" and poly["FINAL_VALID_PREGAME"] == "NOT_DUE"
    assert set(_states(cov, game.id, "KALSHI").values()) == {"NOT_APPLICABLE"}   # provider disabled

    game.status = GameStatus.POSTPONED
    session.commit()
    cov = PregameCaptureCoverageService(session, s).coverage(_event_date(s, start), now=now)
    assert set(_states(cov, game.id, "POLYMARKET").values()) == {"STRUCTURALLY_INELIGIBLE"}


def test_coverage_read_is_bounded_and_makes_no_provider_call(session, settings, monkeypatch):
    s = _observer_settings(settings)
    now = datetime.now(UTC)
    start = now + timedelta(hours=5)
    for i in range(6):
        _seed_game(session, home=f"Home Club H{i}Z", away=f"Away Nine A{i}Z", start=start + timedelta(minutes=i))
    import httpx

    def forbidden(*a, **k):
        raise AssertionError("provider call attempted during a read")

    monkeypatch.setattr(httpx.AsyncClient, "get", forbidden)
    monkeypatch.setattr(httpx.Client, "get", forbidden)
    statements: list[str] = []
    engine = session.get_bind()

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        cov = PregameCaptureCoverageService(session, s).coverage(_event_date(s, start), now=now)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert cov.canonical_games == 6
    assert len(statements) <= 12, len(statements)                              # bulk prefetch, not per-cell queries
    assert all(sum(cov.counts[p][m].values()) == 6 for p in PROVIDERS for m in MILESTONES)
