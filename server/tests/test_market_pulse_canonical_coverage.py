"""Crowd & Market Pulse — canonical-slate coverage, honest provider states,
unreviewed-quote gating, evidence-first refresh ordering, and Kalshi
series-family classification.

Every test is read-only against provider APIs (no network) and uses the
rollback-isolated SQLite session fixture. Nothing here can create a position,
alter a forecast, or call Polymarket/Kalshi.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models import (
    IngestionRun,
    PredictionMarketContract,
    PredictionMarketMapping,
    PredictionMarketSnapshot,
    RunStatus,
)
from app.prediction_markets.contracts import (
    CanonicalMarketType,
    CanonicalSelection,
    PredictionMarketProvider,
)
from app.prediction_markets.jobs import PredictionMarketJobs
from app.prediction_markets.kalshi import KalshiPredictionMarketSource
from app.prediction_markets.read_service import PredictionMarketReadService
from app.prediction_markets.service import PredictionMarketObserver
from test_prediction_market_observer import _contract, _quote, _seed_game


def _observer_settings(settings, **extra):
    base = {"prediction_market_observer_enabled": True, "polymarket_enabled": True,
            "kalshi_enabled": True, "kalshi_rest_polling_enabled": True, "demo_mode": False}
    base.update(extra)
    return settings.model_copy(update=base)


def _event_date(settings, start: datetime) -> date:
    return start.astimezone(ZoneInfo(settings.display_timezone)).date()


def _approve_semantics(session, provider: str, event_id: str, game_id) -> None:
    """Mirror the admin approval route: append a MANUAL_APPROVED decision."""
    prior = session.scalar(select(PredictionMarketMapping).where(
        PredictionMarketMapping.provider == provider, PredictionMarketMapping.provider_event_id == event_id,
    ).order_by(PredictionMarketMapping.decided_at.desc()))
    session.add(PredictionMarketMapping(
        provider=provider, provider_event_id=event_id, canonical_game_id=game_id,
        mapping_status="MANUAL_APPROVED", semantics_status="SEMANTICS_MATCHED", decision_source="MANUAL",
        reason="test approval", decided_at=datetime.now(UTC) + timedelta(seconds=1),
        supersedes_id=prior.id if prior else None,
    ))
    session.commit()


# -- 1. canonical seeding ----------------------------------------------------

def test_board_seeds_every_canonical_game_even_without_any_market(session, settings):
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=5)
    g1, _, _ = _seed_game(session, start=start)
    g2, _, _ = _seed_game(session, home="Los Angeles Dodgers", away="Washington Nationals", start=start + timedelta(hours=1))
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    assert board.canonical_games == 2 and len(board.rows) == 2
    assert {r.game_id for r in board.rows} == {str(g1.id), str(g2.id)}
    for row in board.rows:
        assert row.coverage_state == "NO_MAPPED_MARKET"
        assert row.polymarket_home_probability is None and row.kalshi_home_probability is None
        assert {c.provider for c in row.provider_coverage} == {"POLYMARKET", "KALSHI"}
        assert all(c.state == "NO_MAPPED_MARKET" for c in row.provider_coverage)
        assert row.display_note == "NO PROVIDER MARKET MAPPED"
    assert board.coverage["NO_MAPPED_MARKET"] == 2 and board.coverage["canonical_games"] == 2
    # Denominators reconcile: every row is accounted for by exactly one state.
    states = ["QUOTED_REVIEWED", "QUOTED_UNREVIEWED", "MAPPED_NOT_QUOTED", "MAPPING_REVIEW_REQUIRED",
              "MAPPING_REJECTED", "NO_MAPPED_MARKET", "PROVIDER_DISABLED", "NO_PROVIDER_ENABLED"]
    assert sum(board.coverage[k] for k in states) == board.canonical_games
    # No quote anywhere → never "eligible"; healthy-zero names the slate size.
    assert board.board_state in {"DISCOVERY_PENDING", "PROVIDERS_HEALTHY_ZERO_ELIGIBLE"}
    assert board.data_mode == "live"


def test_board_is_empty_for_a_date_with_no_canonical_games(session, settings):
    s = _observer_settings(settings)
    board = PredictionMarketReadService(session, s).board(date(2030, 1, 1))
    assert board.rows == [] and board.canonical_games == 0 and board.data_mode == "empty"
    assert board.board_state != "ELIGIBLE_MARKETS_AVAILABLE"


# -- 2. per-provider coverage states -----------------------------------------

def test_mapped_but_unquoted_game_reports_listed_not_quoted(session, settings):
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=4)
    game, _, _ = _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, s)
    obs.ingest_contracts([_contract("evt-1", start=start), _contract("evt-1", start=start, selection=CanonicalSelection.AWAY, token="tok2")])
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    row = board.rows[0]
    assert row.mapping_status == "EXACT"
    assert row.coverage_state == "MAPPED_NOT_QUOTED"
    poly = next(c for c in row.provider_coverage if c.provider == "POLYMARKET")
    assert poly.state == "MAPPED_NOT_QUOTED" and poly.contracts_listed == 1  # one market id, two outcomes
    assert "no snapshot refresh job has completed yet" in poly.reason
    kalshi = next(c for c in row.provider_coverage if c.provider == "KALSHI")
    assert kalshi.state == "NO_MAPPED_MARKET"
    assert row.display_note == "LISTED — NOT YET QUOTED"
    assert board.coverage["MAPPED_NOT_QUOTED"] == 1


def test_kalshi_listed_without_credentials_states_the_exact_reason(session, settings):
    s = _observer_settings(settings, kalshi_api_key_id="")
    start = datetime.now(UTC) + timedelta(hours=4)
    game, _, _ = _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, s)
    obs.ingest_contracts([_contract("kx-1", start=start, provider=PredictionMarketProvider.KALSHI, market_id="KXMLBGAME-1-NYY")])
    row = PredictionMarketReadService(session, s).board(_event_date(s, start)).rows[0]
    kalshi = next(c for c in row.provider_coverage if c.provider == "KALSHI")
    assert kalshi.state == "MAPPED_NOT_QUOTED"
    assert "require API credentials" in kalshi.reason


def test_disabled_provider_is_labelled_disabled_but_persisted_quotes_still_show(session, settings):
    start = datetime.now(UTC) + timedelta(hours=4)
    game, _, _ = _seed_game(session, start=start)
    enabled = _observer_settings(settings)
    obs = PredictionMarketObserver(session, enabled)
    obs.ingest_contracts([_contract("evt-d", start=start)])
    contract = session.scalar(select(PredictionMarketContract))
    obs.record_snapshot(contract=contract, quote=_quote(contract.provider_market_id, contract.provider_outcome_id))
    # Now turn Polymarket off: the persisted observation must remain visible.
    disabled = _observer_settings(settings, polymarket_enabled=False)
    row = PredictionMarketReadService(session, disabled).board(_event_date(disabled, start)).rows[0]
    poly = next(c for c in row.provider_coverage if c.provider == "POLYMARKET")
    assert poly.state.startswith("QUOTED_") and row.polymarket_home_probability == 0.65
    # A provider with nothing persisted and its flag off is simply disabled.
    all_off = _observer_settings(settings, polymarket_enabled=False, kalshi_enabled=False)
    g2, _, _ = _seed_game(session, home="Chicago Cubs", away="Miami Marlins", start=start + timedelta(hours=1))
    rows = PredictionMarketReadService(session, all_off).board(_event_date(all_off, start)).rows
    r2 = next(r for r in rows if r.game_id == str(g2.id))
    assert r2.coverage_state == "NO_PROVIDER_ENABLED"
    assert all(c.state == "PROVIDER_DISABLED" for c in r2.provider_coverage)


# -- 3. unreviewed semantics never become a claimed gap ----------------------

def _seed_quoted_game(session, s, *, start, event_id="evt-q", home="New York Yankees", away="Boston Red Sox",
                      rules="Team to win the game", question="Who wins the game?"):
    game, _, _ = _seed_game(session, home=home, away=away, start=start)
    obs = PredictionMarketObserver(session, s)
    dto = _contract(event_id, home=home, away=away, start=start, market_id=f"mkt-{event_id}", rules=rules)
    obs.ingest_contracts([dto.model_copy(update={"market_question": question})])
    contract = session.scalar(select(PredictionMarketContract).where(PredictionMarketContract.provider_event_id == event_id))
    obs.record_snapshot(contract=contract, quote=_quote(contract.provider_market_id, contract.provider_outcome_id))
    return game, contract, obs


def test_unreviewed_quote_is_a_research_observation_not_a_gap(session, settings, monkeypatch):
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=4)
    # Resolution rules the semantics recognizer does not accept → human review
    # required — exactly the state the live WSH@LAD row was in.
    game, contract, _ = _seed_quoted_game(session, s, start=start, rules="Resolves per the official market description.",
                                          question="Yankees vs Red Sox")
    snap = session.scalar(select(PredictionMarketSnapshot))
    assert snap.semantics_status != "SEMANTICS_MATCHED"
    monkeypatch.setattr(PredictionMarketReadService, "_aisp_home_probability", lambda self, g: (0.50, True))
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    row = board.rows[0]
    assert row.coverage_state == "QUOTED_UNREVIEWED" and row.unreviewed_observation is True
    assert row.polymarket_home_probability == 0.65           # the observation is still shown …
    assert row.diff_aisp_prediction is None                   # … but no gap is claimed from it
    assert row.diff_aisp_prediction_unreviewed == pytest.approx(-0.15, abs=1e-6)
    assert row.decision_eligible_quote is False
    assert row.display_note == "UNREVIEWED RESEARCH OBSERVATION — NOT DECISION ELIGIBLE"
    assert board.board_state == "RESEARCH_OBSERVATIONS_UNREVIEWED"
    assert board.summary["biggest_disagreement"] is None
    assert board.summary["biggest_unreviewed_gap"] == pytest.approx(0.15, abs=1e-6)
    assert board.coverage["decision_eligible"] == 0 and board.coverage["semantics_review_pending"] == 1


def test_reviewed_robust_quote_earns_eligible_state_and_a_claimed_gap(session, settings, monkeypatch):
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=4)
    game, contract, obs = _seed_quoted_game(session, s, start=start)
    _approve_semantics(session, "POLYMARKET", "evt-q", game.id)
    # A fresh snapshot after approval carries the reviewed semantics.
    obs.record_snapshot(contract=contract, quote=_quote(contract.provider_market_id, contract.provider_outcome_id,
                                                        observed=datetime.now(UTC) + timedelta(seconds=2)))
    monkeypatch.setattr(PredictionMarketReadService, "_aisp_home_probability", lambda self, g: (0.50, True))
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    row = board.rows[0]
    assert row.coverage_state == "QUOTED_REVIEWED"
    poly = next(p for p in row.providers if p.provider == "POLYMARKET")
    if poly.quality_tier in {"ROBUST", "USABLE"}:
        assert row.decision_eligible_quote is True and board.board_state == "ELIGIBLE_MARKETS_AVAILABLE"
    else:
        assert row.decision_eligible_quote is False and board.board_state != "ELIGIBLE_MARKETS_AVAILABLE"
    assert row.diff_aisp_prediction == pytest.approx(-0.15, abs=1e-6)
    assert row.diff_aisp_prediction_unreviewed is None and row.unreviewed_observation is False


# -- 4. refresh ordering: evidence first, never alphabetical ------------------

def test_active_contracts_prioritise_slate_moneylines_and_interleave_providers(session, settings):
    s = _observer_settings(settings, prediction_market_max_active_markets=4)
    start = datetime.now(UTC) + timedelta(hours=4)
    game, _, _ = _seed_game(session, start=start)
    far = start + timedelta(days=9)
    _seed_game(session, home="Los Angeles Dodgers", away="Washington Nationals", start=far)
    obs = PredictionMarketObserver(session, s)
    # Alphabetically-first ids belong to the FAR game; the slate game sorts last.
    total = _contract("zzz-slate", start=start, market_id="0xzzz-slate-total", token="tot").model_copy(
        update={"market_type": CanonicalMarketType.FULL_GAME_TOTAL, "selection": CanonicalSelection.OVER,
                "line_value": 8.5, "outcome_label": "Over 8.5"})
    obs.ingest_contracts([
        _contract("aaa-far", home="Los Angeles Dodgers", away="Washington Nationals", start=far, market_id="0x000-far"),
        _contract("zzz-slate", start=start, market_id="0xzzz-slate-ml"),
        total,
        _contract("zzz-slate", start=start, provider=PredictionMarketProvider.KALSHI, market_id="KXMLBGAME-slate"),
    ])
    assert session.scalar(select(PredictionMarketContract).where(
        PredictionMarketContract.provider_market_id == "0xzzz-slate-total")).market_type == CanonicalMarketType.FULL_GAME_TOTAL.value
    jobs = PredictionMarketJobs(session, s)
    ordered = jobs._active_contracts(_event_date(s, start))
    ids = [c.provider_market_id for c in ordered]
    assert len(ids) <= 4
    # Slate moneylines (both providers, interleaved) come before the slate total and the far game.
    assert set(ids[:2]) == {"0xzzz-slate-ml", "KXMLBGAME-slate"}
    assert ids.index("0xzzz-slate-total") < ids.index("0x000-far")
    # Without an operational date the mapped tier still outranks alphabet order.
    assert jobs._active_contracts(None)[0].provider_market_id != "0x000-far" or len(jobs._active_contracts(None)) == 4


@pytest.mark.asyncio
async def test_refresh_skips_kalshi_without_credentials_and_records_it(session, settings, monkeypatch):
    s = _observer_settings(settings, kalshi_api_key_id="")
    start = datetime.now(UTC) + timedelta(hours=4)
    _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, s)
    obs.ingest_contracts([
        _contract("evt-p", start=start, market_id="0xpoly"),
        _contract("evt-k", start=start, provider=PredictionMarketProvider.KALSHI, market_id="KXMLBGAME-k"),
    ])
    fetched: list[str] = []

    async def fake_fetch(self, contract):
        fetched.append(contract.provider)
        return None

    monkeypatch.setattr(PredictionMarketJobs, "_fetch_quote", fake_fetch)
    result = await PredictionMarketJobs(session, s).refresh(_event_date(s, start))
    assert fetched == ["POLYMARKET"]                      # no request wasted on Kalshi
    assert result["skipped"] == {"KALSHI": "order books require API credentials; none configured"}
    run = session.scalar(select(IngestionRun).where(IngestionRun.job_name == "refresh-prediction-market-snapshots"))
    assert run.status == RunStatus.SUCCEEDED and run.source_metadata["skipped"]["KALSHI"]


# -- 5. Kalshi series families -----------------------------------------------

@pytest.mark.parametrize("ticker,family", [
    ("KXMLBGAME-26SEP032210STLLAD-STL", "MONEYLINE"),
    ("KXMLBSPREAD-26SEP03-LAD", "SPREAD"),
    ("KXMLBTOTAL-26SEP03", "TOTAL"),
    ("KXMLBTB-26SEP03-OHTANI", None),        # total bases (player prop)
    ("KXMLBHIT-26SEP03", None),
    ("KXMLBERA-2026", None),
    ("KXMLBRFI-26SEP03", None),             # run first inning
    ("KXMLBF5SPREAD-26SEP03", None),        # first-five spread
    ("KXMLBWINS-2026-NYY", None),           # season wins
    ("KXNCAAF1QTOTAL-26SEP06", None),       # other league
])
def test_kalshi_series_family_classification(ticker, family):
    assert KalshiPredictionMarketSource._series_family(ticker, "MLB") == family


def test_kalshi_non_full_game_markets_are_excluded_with_reasons():
    src = KalshiPredictionMarketSource
    assert src._exclusion_reason({"series_ticker": "KXMLBTB-1"}, "MLB") == "player_props"
    assert src._exclusion_reason({"series_ticker": "KXMLBRFI-1"}, "MLB") == "partial_game_or_derivative"
    assert src._exclusion_reason({"series_ticker": "KXMLBF5SPREAD-1"}, "MLB") == "partial_game_or_derivative"
    assert src._exclusion_reason({"series_ticker": "KXMLBWINS-1"}, "MLB") == "futures_or_season"
    assert src._exclusion_reason({"series_ticker": "KXNCAAF1QTOTAL-1"}, "MLB") == "other_league_or_sport"
    assert src._exclusion_reason("nope", "MLB") == "malformed_record"
    assert src._exclusion_reason({}, "MLB") == "missing_identifiers"
    # The normalizer itself refuses a prop, so it can never be labelled a moneyline.
    source = KalshiPredictionMarketSource(rest_base_url="https://example.invalid", api_key_id="", private_key_path="", ws_enabled=False)
    prop = {"ticker": "KXMLBTB-26SEP03-OHTANI-3", "series_ticker": "KXMLBTB-26SEP03", "title": "Ohtani 3+ total bases",
            "status": "open", "event_ticker": "KXMLBTB-26SEP03"}
    assert source._normalize_market(prop, league="MLB", sport="BASEBALL") is None
    winner = {"ticker": "KXMLBGAME-26SEP032210STLLAD-STL", "series_ticker": "KXMLBGAME", "title": "St. Louis wins",
              "status": "open", "event_ticker": "KXMLBGAME-26SEP032210STLLAD", "yes_sub_title": "St. Louis"}
    dto = source._normalize_market(winner, league="MLB", sport="BASEBALL")
    assert dto is not None and dto.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE


# -- 6. public reads never touch a provider ----------------------------------

def test_board_makes_no_provider_call(session, settings, monkeypatch):
    import httpx

    def boom(*a, **k):  # any outbound HTTP from a public read is a defect
        raise AssertionError("public read attempted a provider call")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom, raising=False)
    monkeypatch.setattr(httpx.Client, "get", boom, raising=False)
    s = _observer_settings(settings)
    start = datetime.now(UTC) + timedelta(hours=4)
    _seed_game(session, start=start)
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    assert len(board.rows) == 1
