"""Prediction Market Observer — focused tests.

Covers Kalshi order-book normalization, price definitions, fail-closed game
mapping, semantics suppression, append-only idempotent persistence, consensus
eligibility, the public read board, the public-read/provider boundary, and the
regression guarantees that nothing here can become odds, a stake, a forecast, or
an order.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    Game,
    GameStatus,
    GameTeam,
    League,
    OddsSnapshot,
    PredictionMarketContract,
    PredictionMarketSnapshot,
    Sport,
    Team,
)
from app.prediction_markets.contracts import (
    CanonicalMarketType,
    CanonicalSelection,
    NormalizedOrderBook,
    OrderBookLevel,
    PredictionMarketProvider,
    ProviderContractDTO,
    ProviderQuoteDTO,
    normalize_kalshi_book,
    price_view,
)
from app.prediction_markets.mapping import map_event_to_game
from app.prediction_markets.read_service import PredictionMarketReadService
from app.prediction_markets.service import PredictionMarketObserver
from app.prediction_markets.contracts import MappingStatus


def _seed_game(session, *, home="New York Yankees", away="Boston Red Sox", start=None, league="MLB"):
    sport = session.scalar(select(Sport).where(Sport.code == "BASEBALL"))
    if sport is None:
        sport = Sport(code="BASEBALL", name="Baseball", active=True)
        session.add(sport)
        session.flush()
    lg = session.scalar(select(League).where(League.code == league))
    if lg is None:
        lg = League(sport_id=sport.id, code=league, name=league, active=True)
        session.add(lg)
        session.flush()
    def _team(name: str, abbr: str) -> Team:
        existing = session.scalar(select(Team).where(Team.league_id == lg.id, Team.name == name))
        if existing is not None:
            return existing
        t = Team(league_id=lg.id, name=name, abbreviation=abbr, city=name.split()[0], active=True)
        session.add(t)
        session.flush()
        return t

    home_team = _team(home, home.split()[-1][:3].upper())
    away_team = _team(away, away.split()[-1][:3].upper())
    game = Game(league_id=lg.id, starts_at=start or (datetime.now(UTC) + timedelta(hours=3)),
                status=GameStatus.SCHEDULED, source_mode="LIVE")
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home_team.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away_team.id, is_home=False),
    ])
    session.commit()
    return game, home_team, away_team


def _contract(event_id, *, home="New York Yankees", away="Boston Red Sox", start, selection=CanonicalSelection.HOME,
              provider=PredictionMarketProvider.POLYMARKET, market_id=None, token="tok", rules="Team to win the game"):
    return ProviderContractDTO(
        provider=provider, provider_event_id=event_id, provider_market_id=market_id or f"mkt-{event_id}",
        provider_outcome_id=token, sport="BASEBALL", league="MLB", provider_home_team=home, provider_away_team=away,
        scheduled_start_at=start, market_type=CanonicalMarketType.FULL_GAME_MONEYLINE, selection=selection,
        outcome_label=home if selection == CanonicalSelection.HOME else away, market_question="Who wins the game?",
        resolution_rules=rules, market_status="active",
    )


def _quote(market_id, token, *, yes=0.60, no=0.30, provider=PredictionMarketProvider.POLYMARKET, observed=None):
    book = normalize_kalshi_book([(yes, 100.0)], [(no, 80.0)])
    return ProviderQuoteDTO(
        provider=provider, provider_market_id=market_id, provider_outcome_id=token, book=book,
        last_trade=0.61, volume=1000.0, open_interest=500.0, provider_observed_at=observed or datetime.now(UTC),
        raw={"m": market_id, "t": token, "yes": yes, "no": no},
    )


# -- pricing + normalization -------------------------------------------------

def test_kalshi_book_normalization_and_prices():
    book = normalize_kalshi_book([(0.60, 100.0)], [(0.30, 80.0)])
    pv = price_view(book, last_trade=0.62)
    assert pv.best_bid == 0.60 and pv.best_ask == 0.70  # 1 - 0.30
    assert pv.midpoint == 0.65 and pv.book_complete
    assert pv.last_trade == 0.62 and pv.midpoint != pv.last_trade  # distinct definitions


def test_missing_side_yields_no_fabricated_ask():
    pv = price_view(normalize_kalshi_book([(0.55, 50.0)], []))
    assert pv.best_ask is None and pv.midpoint is None and pv.microprice is None


def test_crossed_and_out_of_range_levels_dropped():
    book = normalize_kalshi_book([(0.60, 10.0), (1.5, 5.0)], [(0.30, 10.0)])
    assert all(0.0 <= l.price <= 1.0 for l in book.bids)


# -- mapping fail-closed -----------------------------------------------------

@pytest.mark.asyncio
async def test_exact_mapping(session, settings):
    game, _, _ = _seed_game(session, start=datetime(2026, 8, 30, 23, 5, tzinfo=UTC))
    d = map_event_to_game(session, provider="POLYMARKET", provider_event_id="e1", league_code="MLB",
                          provider_home_team="New York Yankees", provider_away_team="Boston Red Sox",
                          scheduled_start_at=datetime(2026, 8, 30, 23, 5, tzinfo=UTC))
    assert d.status == MappingStatus.EXACT and str(d.canonical_game_id) == str(game.id)


@pytest.mark.asyncio
async def test_reversed_teams_fail_closed(session, settings):
    _seed_game(session, start=datetime(2026, 8, 30, 23, 5, tzinfo=UTC))
    d = map_event_to_game(session, provider="POLYMARKET", provider_event_id="e2", league_code="MLB",
                          provider_home_team="Boston Red Sox", provider_away_team="New York Yankees",
                          scheduled_start_at=datetime(2026, 8, 30, 23, 5, tzinfo=UTC))
    assert d.status == MappingStatus.REVIEW_REQUIRED


@pytest.mark.asyncio
async def test_doubleheader_is_ambiguous(session, settings):
    start = datetime(2026, 8, 30, 23, 5, tzinfo=UTC)
    _seed_game(session, start=start)
    _seed_game(session, start=start)  # same teams + time => doubleheader/concurrent
    d = map_event_to_game(session, provider="POLYMARKET", provider_event_id="e3", league_code="MLB",
                          provider_home_team="New York Yankees", provider_away_team="Boston Red Sox",
                          scheduled_start_at=start)
    assert d.status == MappingStatus.AMBIGUOUS


@pytest.mark.asyncio
async def test_kickoff_mismatch_needs_review(session, settings):
    _seed_game(session, start=datetime(2026, 8, 30, 20, 0, tzinfo=UTC))
    d = map_event_to_game(session, provider="POLYMARKET", provider_event_id="e4", league_code="MLB",
                          provider_home_team="New York Yankees", provider_away_team="Boston Red Sox",
                          scheduled_start_at=datetime(2026, 8, 30, 23, 5, tzinfo=UTC))
    assert d.status == MappingStatus.REVIEW_REQUIRED and d.start_time_delta_seconds and d.start_time_delta_seconds > 5400


@pytest.mark.asyncio
async def test_no_candidate_is_unresolved(session, settings):
    _seed_game(session, home="Chicago Cubs", away="St. Louis Cardinals", start=datetime(2026, 8, 30, 23, 5, tzinfo=UTC))
    d = map_event_to_game(session, provider="POLYMARKET", provider_event_id="e5", league_code="MLB",
                          provider_home_team="New York Yankees", provider_away_team="Boston Red Sox",
                          scheduled_start_at=datetime(2026, 8, 30, 23, 5, tzinfo=UTC))
    assert d.status == MappingStatus.UNRESOLVED


# -- ingestion + snapshot persistence ----------------------------------------

@pytest.mark.asyncio
async def test_ingestion_idempotent_and_maps(session, settings):
    start = datetime(2026, 8, 30, 23, 5, tzinfo=UTC)
    game, _, _ = _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, settings)
    contract = _contract("e1", start=start)
    r1 = obs.ingest_contracts([contract])
    r2 = obs.ingest_contracts([contract])  # identical rerun
    assert r1["contracts"] == 1 and r2["contracts"] == 0  # idempotent
    row = session.scalar(select(PredictionMarketContract))
    assert row is not None
    from app.models import PredictionMarketMapping
    mapping = session.scalar(select(PredictionMarketMapping))
    assert mapping.mapping_status == "EXACT" and str(mapping.canonical_game_id) == str(game.id)


@pytest.mark.asyncio
async def test_snapshot_append_only_and_duplicate_noop(session, settings):
    start = datetime.now(UTC) + timedelta(hours=3)
    _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, settings)
    obs.ingest_contracts([_contract("e1", start=start)])
    contract = session.scalar(select(PredictionMarketContract))
    observed = datetime.now(UTC)
    q = _quote(contract.provider_market_id, contract.provider_outcome_id, observed=observed)
    first = obs.record_snapshot(contract=contract, quote=q)
    dup = obs.record_snapshot(contract=contract, quote=q)  # same payload + time
    assert first is not None and dup is None
    assert session.scalar(select(func.count()).select_from(PredictionMarketSnapshot)) == 1
    assert first.midpoint == 0.65 and first.pregame_eligible and not first.in_play


@pytest.mark.asyncio
async def test_post_start_snapshot_excluded_from_pregame(session, settings):
    start = datetime.now(UTC) - timedelta(minutes=30)  # already started
    _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, settings)
    obs.ingest_contracts([_contract("e1", start=start)])
    contract = session.scalar(select(PredictionMarketContract))
    snap = obs.record_snapshot(contract=contract, quote=_quote(contract.provider_market_id, contract.provider_outcome_id))
    assert snap.in_play and not snap.pregame_eligible


# -- consensus eligibility ---------------------------------------------------

@pytest.mark.asyncio
async def test_consensus_needs_two_qualified_providers(session, settings):
    start = datetime(2026, 8, 30, 23, 5, tzinfo=UTC)
    game, _, _ = _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, settings)
    # Only one provider, and semantics not matched by default -> no consensus
    obs.ingest_contracts([_contract("e1", start=start, rules="Who wins?")])
    contract = session.scalar(select(PredictionMarketContract))
    obs.record_snapshot(contract=contract, quote=_quote(contract.provider_market_id, contract.provider_outcome_id))
    obs.compute_consensus(game.id)
    from app.models import PredictionMarketConsensusSnapshot
    cons = session.scalar(select(PredictionMarketConsensusSnapshot))
    assert cons is not None and cons.eligible is False


# -- read board --------------------------------------------------------------

@pytest.mark.asyncio
async def test_board_renders_mapped_game(session, settings):
    start = datetime.now(UTC) + timedelta(hours=3)
    game, _, _ = _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, settings)
    obs.ingest_contracts([_contract("e1", start=start)])
    contract = session.scalar(select(PredictionMarketContract))
    obs.record_snapshot(contract=contract, quote=_quote(contract.provider_market_id, contract.provider_outcome_id))
    from zoneinfo import ZoneInfo
    event_date = start.astimezone(ZoneInfo(settings.display_timezone)).date()
    board = PredictionMarketReadService(session, settings).board(event_date)
    assert board.data_mode == "live" and len(board.rows) == 1
    row = board.rows[0]
    assert row.mapping_status == "EXACT" and row.polymarket_home_probability == 0.65


# -- regression: no crossover into odds / stakes / execution -----------------

@pytest.mark.asyncio
async def test_no_prediction_snapshot_becomes_odds(session, settings):
    start = datetime(2026, 8, 30, 23, 5, tzinfo=UTC)
    _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, settings)
    obs.ingest_contracts([_contract("e1", start=start)])
    contract = session.scalar(select(PredictionMarketContract))
    before_odds = session.scalar(select(func.count()).select_from(OddsSnapshot))
    obs.record_snapshot(contract=contract, quote=_quote(contract.provider_market_id, contract.provider_outcome_id))
    after_odds = session.scalar(select(func.count()).select_from(OddsSnapshot))
    assert before_odds == after_odds  # observer never writes an OddsSnapshot


def test_no_trading_symbols_in_prediction_market_package():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "prediction_markets"
    banned = ("place_order", "submit_order", "sign_order", "connect_wallet", "buy(", "sell(", "withdraw", "deposit")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in banned:
            assert token.lower() not in text, f"{token} found in {path.name}"


# -- public-read / provider boundary + credential secrecy --------------------

@pytest.mark.asyncio
async def test_public_reads_make_zero_provider_calls(session, settings, monkeypatch):
    start = datetime.now(UTC) + timedelta(hours=3)
    game, _, _ = _seed_game(session, start=start)
    obs = PredictionMarketObserver(session, settings)
    obs.ingest_contracts([_contract("e1", start=start)])
    contract = session.scalar(select(PredictionMarketContract))
    obs.record_snapshot(contract=contract, quote=_quote(contract.provider_market_id, contract.provider_outcome_id))

    # Any attempt to open an HTTP client during a public read must blow up.
    import httpx

    def _boom(*a, **k):
        raise AssertionError("public read attempted a network/provider call")

    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    monkeypatch.setattr(httpx, "Client", _boom)
    read = PredictionMarketReadService(session, settings)
    from zoneinfo import ZoneInfo
    event_date = start.astimezone(ZoneInfo(settings.display_timezone)).date()
    assert read.board(event_date).rows  # DB only
    assert read.game_detail(game.id) is not None
    assert read.alerts(event_date) is not None
    assert read.health_summary() is not None


def test_kalshi_rest_only_without_credentials():
    from app.prediction_markets.kalshi import KalshiPredictionMarketSource

    assert KalshiPredictionMarketSource().connection_mode == "REST_ONLY"
    assert KalshiPredictionMarketSource(api_key_id="x", private_key_path="/p", ws_enabled=True).connection_mode == "STREAMING"


def test_health_summary_never_exposes_credentials(session, settings):
    s = settings.model_copy(update={"kalshi_api_key_id": "SECRET_ID", "kalshi_private_key_path": "/secret/key.pem"})
    summary = PredictionMarketReadService(session, s).health_summary()
    blob = summary.model_dump_json()
    assert "SECRET_ID" not in blob and "/secret/key.pem" not in blob


# -- Polymarket Gamma /events discovery parsing ------------------------------

@pytest.mark.asyncio
async def test_polymarket_discovery_parses_gamma_events_and_counts_exclusions():
    from app.prediction_markets.polymarket import PolymarketPredictionMarketSource

    game_event = {
        "id": "981", "slug": "mlb-bos-nyy-2026-08-30", "gameId": "10078058",
        "title": "Boston Red Sox vs. New York Yankees",
        "startTime": "2026-08-31T00:05:00Z", "seriesSlug": "mlb",
        "markets": [
            {
                "id": "1", "conditionId": "0xml", "sportsMarketType": "moneyline",
                "question": "Boston Red Sox vs. New York Yankees",
                "gameStartTime": "2026-08-31 00:05:00+00",
                "outcomes": "[\"Boston Red Sox\", \"New York Yankees\"]",
                "clobTokenIds": "[\"tok-away\", \"tok-home\"]",
                "active": True, "closed": False,
            },
            {
                "id": "2", "conditionId": "0xsp", "sportsMarketType": "spreads",
                "question": "Spread: New York Yankees (-1.5)", "line": -1.5,
                "gameStartTime": "2026-08-31 00:05:00+00",
                "outcomes": "[\"New York Yankees\", \"Boston Red Sox\"]",
                "clobTokenIds": "[\"tok-s1\", \"tok-s2\"]",
                "active": True, "closed": False,
            },
            {
                "id": "3", "conditionId": "0xnr", "sportsMarketType": "nrfi",
                "question": "Will there be a run scored in the first inning?",
                "gameStartTime": "2026-08-31 00:05:00+00",
                "outcomes": "[\"Yes\", \"No\"]", "clobTokenIds": "[\"t1\", \"t2\"]",
                "active": True, "closed": False,
            },
        ],
    }
    futures_event = {
        "id": "982", "slug": "mlb-world-series-champion-2026",
        "title": "MLB World Series Champion 2026",
        "markets": [
            {"id": "9", "conditionId": "0xws", "sportsMarketType": None,
             "question": "Will the New York Yankees win the 2026 World Series?",
             "outcomes": "[\"Yes\", \"No\"]", "clobTokenIds": "[\"w1\", \"w2\"]"},
        ],
    }

    source = PolymarketPredictionMarketSource()

    async def fake_get(base, path, params=None):
        assert path == "/events"
        assert params["tag_slug"] == "mlb"
        return [game_event, futures_event]

    source._get = fake_get  # type: ignore[method-assign]
    resp = await source.discover_contracts(league="MLB", limit=100)
    meta = resp.envelope.metadata
    assert meta["events_seen"] == 2
    assert meta["markets_seen"] == 4
    assert meta["query"]["path"] == "/events"
    # NRFI is a period market and the World Series market is a future — both
    # inspected, both excluded, with honest reason buckets.
    assert meta["excluded"]["non_governed_market_type"] == 1
    assert meta["excluded"]["futures_or_season"] == 1
    dtos = [ProviderContractDTO.model_validate(item) for item in resp.envelope.data]
    ml = [d for d in dtos if d.market_type == CanonicalMarketType.FULL_GAME_MONEYLINE]
    assert {d.selection for d in ml} == {CanonicalSelection.HOME, CanonicalSelection.AWAY}
    # "Away vs. Home" title order: the Yankees are the home side.
    assert all(d.provider_home_team == "New York Yankees" for d in dtos)
    assert all(d.provider_away_team == "Boston Red Sox" for d in dtos)
    assert all(d.provider_event_id == "10078058" for d in dtos)
    spread = [d for d in dtos if d.market_type == CanonicalMarketType.FULL_GAME_SPREAD]
    assert spread and all(d.line_value == -1.5 for d in spread)
