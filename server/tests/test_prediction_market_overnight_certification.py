"""Overnight capture certification contracts (fail-before / pass-after).

Reproduced defects on the live 09-04 slate:
* Closed Polymarket books (HTTP 404) were re-requested every refresh cycle
  (81 errors per 10-minute run): a retry storm on markets that can never
  yield another pregame observation.
* Contracts of canonical games already FINAL were still in the active set.
* Run records had no response-status distribution, so the storm was invisible.
* 33,390 Kalshi listing quotes persisted before the persister was restricted
  to accepted mappings were counted as current "incomplete order book" evidence.

Contracts:
1  A 404 on a market book terminalizes the contract by appending a new
   contract version (append-only, prior version and every observation kept)
   and the next cycle makes no request for it.
2  Contracts whose canonical game is FINAL are never requested.
3  Every attempted request lands in exactly one status bucket; a 5xx is
   retryable and does not terminalize.
4  Legacy pre-authority Kalshi observations are classified explicitly,
   preserved, and excluded from current book-quality evidence.
5  Terminalization is idempotent and never touches evidence.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.models import GameStatus, IngestionRun, PredictionMarketContract, PredictionMarketSnapshot
from app.prediction_markets.contracts import PredictionMarketProvider
from app.prediction_markets.jobs import PredictionMarketJobs
from app.prediction_markets.polymarket import PolymarketPredictionMarketSource
from app.prediction_markets.read_service import LEGACY_LISTING_AUTHORITY_CUTOFF, PredictionMarketReadService
from app.prediction_markets.service import PredictionMarketObserver
from test_market_pulse_canonical_coverage import _event_date, _observer_settings
from test_prediction_market_observer import _contract, _quote, _seed_game


def _book_transport(statuses: dict[str, int]):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("token_id", "")
        calls.append(token)
        status = statuses.get(token, 200)
        if status != 200:
            return httpx.Response(status, json={"error": "not found" if status == 404 else "upstream"})
        return httpx.Response(200, json={"market": "0xmkt", "asset_id": token, "timestamp": str(int(datetime.now(UTC).timestamp() * 1000)),
                                         "bids": [{"price": "0.55", "size": "100"}], "asks": [{"price": "0.58", "size": "80"}],
                                         "hash": "h", "tick_size": "0.01"})

    return handler, calls


def _jobs_with_books(session, s, monkeypatch, statuses: dict[str, int]):
    handler, calls = _book_transport(statuses)

    def source(self):
        return PolymarketPredictionMarketSource(gamma_base_url="https://gamma.test", clob_base_url="https://clob.test",
                                                client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(PredictionMarketJobs, "_polymarket_source", source)
    return PredictionMarketJobs(session, s), calls


def _versions(session, market_id: str) -> list[PredictionMarketContract]:
    return list(session.scalars(select(PredictionMarketContract).where(PredictionMarketContract.provider_market_id == market_id)
                                .order_by(PredictionMarketContract.version)))


@pytest.mark.asyncio
async def test_closed_polymarket_book_404_is_terminalized_and_not_retried(session, settings, monkeypatch):
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=3)
    _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, s)
    obs.ingest_contracts([_contract("evt-closed", start=start, market_id="0xclosed", token="tok-closed")])
    # A valid pregame observation exists before the market disappears.
    obs.record_snapshot(contract=_versions(session, "0xclosed")[0], quote=_quote("0xclosed", "tok-closed"))
    before_snaps = session.scalar(select(func.count()).select_from(PredictionMarketSnapshot))

    jobs, calls = _jobs_with_books(session, s, monkeypatch, {"tok-closed": 404})
    first = await jobs.refresh(_event_date(s, start))
    assert calls == ["tok-closed"]
    assert first["status_distribution"]["404"] == 1 and first["status_distribution"]["2xx"] == 0
    assert first["errors"] == 1 and first["requests"] == 1 and first["terminalized"] == 1
    versions = _versions(session, "0xclosed")
    assert [v.market_status for v in versions] == ["active", "closed"]        # appended, never edited
    assert versions[1].version == versions[0].version + 1 and versions[1].provider_outcome_id == "tok-closed"
    assert session.scalar(select(func.count()).select_from(PredictionMarketSnapshot)) == before_snaps  # evidence untouched

    second = await jobs.refresh(_event_date(s, start))
    assert calls == ["tok-closed"]                                              # no retry storm: zero new requests
    assert second["requests"] == 0 and second["status_distribution"]["404"] == 0 and second["terminalized"] == 0

    run = session.scalars(select(IngestionRun).where(IngestionRun.job_name == "refresh-prediction-market-snapshots")
                          .order_by(IngestionRun.started_at)).first()
    assert run.source_metadata["status_distribution"]["404"] == 1
    assert run.source_metadata["terminalized_count"] == 1 and run.source_metadata["terminalized"] == ["0xclosed"]

    # The final valid pregame observation is still the coverage authority.
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    poly = next(c for c in board.rows[0].provider_coverage if c.provider == "POLYMARKET")
    assert poly.pregame_observed_at is not None and poly.state == "QUOTED_UNREVIEWED" and poly.latest_in_play is False


@pytest.mark.asyncio
async def test_contracts_of_final_games_are_never_requested(session, settings, monkeypatch):
    s = _observer_settings(settings)
    start = datetime.now(UTC) - timedelta(hours=4)
    game, _, _ = _seed_game(session, start=start)
    game.status = GameStatus.FINAL
    session.commit()
    obs = PredictionMarketObserver(session, s)
    obs.ingest_contracts([_contract("evt-final", start=start, market_id="0xfinal", token="tok-final")])

    jobs, calls = _jobs_with_books(session, s, monkeypatch, {})
    result = await jobs.refresh(_event_date(s, start))
    assert calls == []                                                          # no provider request for a FINAL game
    assert result["requests"] == 0 and result["skipped_final_games"] == 1 and result["terminalized"] == 1
    assert [v.market_status for v in _versions(session, "0xfinal")] == ["active", "closed"]
    again = await jobs.refresh(_event_date(s, start))
    assert again["skipped_final_games"] == 0 and again["terminalized"] == 0    # terminal contracts leave the active set


@pytest.mark.asyncio
async def test_status_distribution_accounts_every_request_and_5xx_stays_retryable(session, settings, monkeypatch):
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=3)
    _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, s)
    obs.ingest_contracts([_contract("evt-ok", start=start, market_id="0xok", token="tok-ok"),
                          _contract("evt-5xx", start=start, market_id="0x5xx", token="tok-5xx")])
    jobs, calls = _jobs_with_books(session, s, monkeypatch, {"tok-5xx": 503})
    result = await jobs.refresh(_event_date(s, start))
    dist = result["status_distribution"]
    assert sorted(calls) == ["tok-5xx", "tok-ok"]
    assert dist["2xx"] == 1 and dist["5xx"] == 1 and sum(dist.values()) == result["requests"] == 2
    assert result["records"] == 1 and result["errors"] == 1 and result["terminalized"] == 0
    assert [v.market_status for v in _versions(session, "0x5xx")] == ["active"]  # retryable: stays active
    second = await jobs.refresh(_event_date(s, start))
    assert len(calls) == 4 and second["status_distribution"]["5xx"] == 1        # retried next cycle, once


def test_terminalization_is_idempotent_and_append_only(session, settings):
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=3)
    _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, s)
    obs.ingest_contracts([_contract("evt-t", start=start, market_id="0xt", token="tok-t")])
    active = _versions(session, "0xt")[0]
    first = obs.mark_contract_terminal(active, reason="provider returned HTTP 404 for the market book")
    assert first is not None and first.market_status == "closed" and first.version == active.version + 1
    assert obs.mark_contract_terminal(active, reason="provider returned HTTP 404 for the market book") is None
    versions = _versions(session, "0xt")
    assert len(versions) == 2 and versions[0].market_status == "active"       # prior version never rewritten
    assert versions[1].content_checksum != versions[0].content_checksum
    assert PredictionMarketJobs(session, s)._active_contracts(_event_date(s, start)) == []


def test_legacy_pre_authority_kalshi_observations_are_classified_and_excluded(session, settings):
    """Kalshi listing quotes persisted for unmapped markets before the persister
    was restricted to accepted mappings are LEGACY_PRE_AUTHORITY_OBSERVATION:
    preserved, reported under their own key, never counted as current
    incomplete-book evidence. A current unmapped book-less row still counts."""
    from app.prediction_markets.contracts import ProviderQuoteDTO, normalize_kalshi_book

    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=3)
    obs = PredictionMarketObserver(session, s)

    def bookless(market_id: str) -> ProviderQuoteDTO:
        return ProviderQuoteDTO(provider=PredictionMarketProvider.KALSHI, provider_market_id=market_id,
                                provider_outcome_id=f"{market_id}:YES", book=normalize_kalshi_book([(0.5, 100.0)], []),
                                last_trade=0.5, provider_observed_at=datetime.now(UTC), raw={"source": "events.nested_markets"})

    # No canonical game exists for either contract: both stay unmapped.
    obs.ingest_contracts([
        _contract("evt-legacy", start=start, provider=PredictionMarketProvider.KALSHI, market_id="KXMLBGAME-legacy",
                  token="KXMLBGAME-legacy:YES", home="Nowhere Nine", away="Elsewhere Eight"),
        _contract("evt-current", start=start, provider=PredictionMarketProvider.KALSHI, market_id="KXMLBGAME-current",
                  token="KXMLBGAME-current:YES", home="Nowhere Nine", away="Elsewhere Eight"),
    ])
    legacy_contract = _versions(session, "KXMLBGAME-legacy")[0]
    current_contract = _versions(session, "KXMLBGAME-current")[0]
    legacy_now = LEGACY_LISTING_AUTHORITY_CUTOFF - timedelta(minutes=5)
    legacy_quote = bookless("KXMLBGAME-legacy").model_copy(update={"provider_observed_at": legacy_now - timedelta(seconds=30)})
    legacy = obs.record_snapshot(contract=legacy_contract, quote=legacy_quote, now=legacy_now)
    current = obs.record_snapshot(contract=current_contract, quote=bookless("KXMLBGAME-current"))
    assert legacy is not None and legacy.canonical_game_id is None and legacy.book_complete is False
    assert current is not None and current.canonical_game_id is None and current.book_complete is False

    summary = PredictionMarketReadService(session, s)._excluded_summary([])
    assert summary.get("legacy_pre_authority_observations") == 1                 # classified, not hidden
    assert summary.get("incomplete_order_books") == 1                            # only the current row counts
    assert session.scalar(select(func.count()).select_from(PredictionMarketSnapshot)) == 2   # preserved, not cleaned
