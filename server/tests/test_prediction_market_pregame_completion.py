"""Kalshi/Polymarket pregame completion contracts (fail-before / pass-after).

1  Kalshi public REST works with no API key (no Authorization header).
2  Kalshi pagination finds slate markets beyond the first page.
3  KXMLBGAME is classified as the full-game winner series.
4  Props, totals-of-props, spreads-of-partials, inning markets, futures, combos are excluded.
5  Home/away outcome orientation from the event ticker.
6  Doubleheader games do not collide.
7  Polymarket event/token orientation.
8  A valid pregame snapshot remains selectable after the market goes in-play.
9  In-play/final observations never replace the final pregame snapshot.
10 Delayed persistence cannot create false pregame evidence.
11 Disjoint game-level states sum to the canonical slate.
12 Consensus requires at least two reviewed providers.
14 No provider observation changes forecasts or experiment records.
15 No credentials are required for the baseline provider path.
16 No trading endpoint can be called from this feature.
+  Versioned template authority: held closed until a human decision; inherited only when every condition holds.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select

from app.models import (
    Forecast, Game, GameStatus, GameTeam, League, OfficialAspPosition, PredictionMarketSnapshot, ShadowExperimentDecision, Sport, Team,
)
from app.prediction_markets.contracts import (
    CanonicalMarketType, CanonicalSelection, PredictionMarketProvider, ProviderQuoteDTO, normalize_kalshi_book,
)
from app.prediction_markets.kalshi import KalshiPredictionMarketSource, classify_series, parse_event_ticker, parse_rules_start
from app.prediction_markets.polymarket import PolymarketPredictionMarketSource
from app.prediction_markets.read_service import PredictionMarketReadService
from app.prediction_markets.semantics import (
    KALSHI_MLB_WINNER, POLYMARKET_MLB_WINNER, STATUS_APPROVED, STATUS_HELD, SemanticTemplateAuthority,
)
from app.prediction_markets.service import PredictionMarketObserver
from test_market_pulse_canonical_coverage import _event_date, _observer_settings

ET = ZoneInfo("America/New_York")


# -- fixtures -------------------------------------------------------------------

def _league(session):
    sport = session.scalar(select(Sport).where(Sport.code == "BASEBALL"))
    if sport is None:
        sport = Sport(code="BASEBALL", name="Baseball", active=True)
        session.add(sport); session.flush()
    lg = session.scalar(select(League).where(League.code == "MLB"))
    if lg is None:
        lg = League(sport_id=sport.id, code="MLB", name="MLB", active=True)
        session.add(lg); session.flush()
    return lg


def _team(session, lg, name: str, abbr: str) -> Team:
    t = session.scalar(select(Team).where(Team.league_id == lg.id, Team.abbreviation == abbr))
    if t is None:
        t = Team(league_id=lg.id, name=name, abbreviation=abbr, city=name.split()[0], active=True)
        session.add(t); session.flush()
    return t


def _game(session, *, home: tuple[str, str], away: tuple[str, str], start: datetime, status=GameStatus.SCHEDULED) -> Game:
    lg = _league(session)
    h, a = _team(session, lg, *home), _team(session, lg, *away)
    g = Game(league_id=lg.id, starts_at=start, status=status, source_mode="LIVE")
    session.add(g); session.flush()
    session.add_all([GameTeam(game_id=g.id, team_id=h.id, is_home=True), GameTeam(game_id=g.id, team_id=a.id, is_home=False)])
    session.commit()
    return g


def _ticker_for(start: datetime, away: str, home: str, game: int | None = None) -> str:
    local = start.astimezone(ET)
    return f"KXMLBGAME-{local.strftime('%y%b%d%H%M').upper()}{away}{home}" + (f"G{game}" if game else "")


def _kalshi_market(event_ticker: str, yes: str, yes_title: str, *, bid="0.5500", ask="0.5800", last="0.5600", status="active"):
    return {
        "ticker": f"{event_ticker}-{yes}", "event_ticker": event_ticker, "title": f"{yes_title} wins", "yes_sub_title": yes_title,
        "no_sub_title": yes_title, "status": status, "open_time": "2026-09-01T22:25:00Z", "close_time": "2026-09-11T01:10:00Z",
        "expected_expiration_time": "2026-09-05T01:10:00Z", "yes_bid_dollars": bid, "yes_ask_dollars": ask,
        "last_price_dollars": last, "yes_bid_size_fp": "120.00", "yes_ask_size_fp": "90.00", "volume_fp": "1500.00",
        "open_interest_fp": "800.00", "liquidity_dollars": "5000.00",
        "rules_primary": f"If {yes_title} wins the {_matchup_text(event_ticker)} professional baseball game originally scheduled for "
                         f"{_when_text(event_ticker)}, then the market resolves to Yes.",
    }


_NAMES = {"DET": "Detroit", "CLE": "Cleveland", "AZ": "Arizona", "HOU": "Houston", "ATH": "A's", "SEA": "Seattle", "NYY": "New York Y",
          "SD": "San Diego", "LAD": "Los Angeles D", "WSH": "Washington", "MIL": "Milwaukee", "CIN": "Cincinnati"}


def _matchup_text(event_ticker: str) -> str:
    p = parse_event_ticker(event_ticker)
    return f"{_NAMES[p['away_ticker']]} vs {_NAMES[p['home_ticker']]}"


def _when_text(event_ticker: str) -> str:
    p = parse_event_ticker(event_ticker)
    return p["starts_at"].strftime("%b %-d, %Y at %-I:%M %p EDT") if hasattr(p["starts_at"], "strftime") and False else \
        p["starts_at"].strftime("%b %d, %Y at %I:%M %p EDT").replace(" 0", " ").replace("at 0", "at ")


def _kalshi_event(event_ticker: str, *, markets=None, title=None):
    p = parse_event_ticker(event_ticker)
    away, home = p["away_ticker"], p["home_ticker"]
    return {
        "event_ticker": event_ticker, "series_ticker": "KXMLBGAME", "title": title or f"{_NAMES[away]} vs {_NAMES[home]}",
        "sub_title": f"{away} vs {home}", "settlement_sources": [{"name": "ESPN"}, {"name": "the Governing League"}],
        "markets": markets or [_kalshi_market(event_ticker, away, _NAMES[away]), _kalshi_market(event_ticker, home, _NAMES[home])],
    }


class _KalshiFake:
    """MockTransport handler: paginated events, series metadata, order books.
    Records every request so a test can prove no credential was attached."""

    def __init__(self, events: list[dict], page_size: int = 2):
        self.events = events
        self.page_size = page_size
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/series/KXMLBGAME"):
            return httpx.Response(200, json={"series": {"ticker": "KXMLBGAME", "title": "Professional Baseball Game", "category": "Sports",
                                                          "tags": ["Baseball"], "contract_url": "https://assets.kalshi.com/regulatory/product-certifications/MLBGAME.pdf"}})
        if path.endswith("/events"):
            if request.url.params.get("series_ticker") != "KXMLBGAME" or request.url.params.get("status") != "open":
                return httpx.Response(200, json={"events": [], "cursor": None})
            cursor = int(request.url.params.get("cursor") or 0)
            page = self.events[cursor:cursor + self.page_size]
            nxt = cursor + self.page_size
            return httpx.Response(200, json={"events": page, "cursor": str(nxt) if nxt < len(self.events) else None})
        if "/orderbook" in path:
            return httpx.Response(200, json={"orderbook_fp": {"yes_dollars": [["0.5500", "120.00"], ["0.5400", "50.00"]],
                                                              "no_dollars": [["0.4200", "90.00"]]}})
        return httpx.Response(404, json={"error": "unexpected"})


def _kalshi_source(fake: _KalshiFake) -> KalshiPredictionMarketSource:
    return KalshiPredictionMarketSource(rest_base_url="https://public.kalshi.test/trade-api/v2",
                                        client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
                                        api_key_id="", private_key_path="", ws_enabled=False)


# -- 1 / 15 / 16: public REST, no credentials, no trading --------------------------

@pytest.mark.asyncio
async def test_kalshi_public_rest_works_with_no_api_key_and_sends_no_credentials():
    fake = _KalshiFake([_kalshi_event("KXMLBGAME-26SEP042010AZHOU")])
    source = _kalshi_source(fake)
    assert source.requires_credentials is False and source.connection_mode == "REST_ONLY"
    resp = await source.discover_game_events(series_ticker="KXMLBGAME", league="MLB", sport="BASEBALL")
    assert resp.envelope.metadata["credentials_used"] is False
    assert len(resp.envelope.data) == 2
    assert all("authorization" not in {k.lower() for k in r.headers} for r in fake.requests)
    assert all("kalshi-access-key" not in {k.lower() for k in r.headers} for r in fake.requests)
    book = await source.get_order_book("KXMLBGAME-26SEP042010AZHOU-HOU")
    quote = ProviderQuoteDTO.model_validate(book.envelope.data[0])
    assert quote.book.best_bid() == 0.55 and quote.book.best_ask() == pytest.approx(0.58)


def test_no_trading_capability_in_the_kalshi_or_polymarket_adapters():
    import inspect

    for cls in (KalshiPredictionMarketSource, PolymarketPredictionMarketSource):
        src = inspect.getsource(cls).lower()
        assert "/portfolio" not in src and "/orders" not in src and "client.post(" not in src and "place_order" not in src
        assert not [m for m in dir(cls) if "order" in m.lower() and "book" not in m.lower()]


# -- 2: pagination ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kalshi_pagination_reaches_slate_markets_beyond_the_first_page():
    tickers = ["KXMLBGAME-26SEP041810MILCIN", "KXMLBGAME-26SEP042010AZHOU", "KXMLBGAME-26SEP042010TORKC",
               "KXMLBGAME-26SEP042140NYYSD", "KXMLBGAME-26SEP042210ATHSEA"]
    _NAMES.update({"TOR": "Toronto", "KC": "Kansas City"})
    fake = _KalshiFake([_kalshi_event(t) for t in tickers], page_size=2)
    resp = await _kalshi_source(fake).discover_game_events(series_ticker="KXMLBGAME", league="MLB", sport="BASEBALL")
    assert resp.envelope.metadata["pages"] == 3 and resp.envelope.metadata["events_seen"] == 5
    found = {c["provider_event_id"] for c in resp.envelope.data}
    assert found == set(tickers)  # the last page's ATH@SEA is found, not just the first N
    assert len(resp.envelope.metadata["quotes"]) == 10


# -- 3 / 4: classification and exclusions ---------------------------------------------

@pytest.mark.parametrize("series,family", [
    ("KXMLBGAME", "MONEYLINE"), ("KXMLBSPREAD", "SPREAD"), ("KXMLBTOTAL", "TOTAL"),
    ("KXMLBHIT", "PLAYER_PROP"), ("KXMLBTB", "PLAYER_PROP"), ("KXMLBERA", "PLAYER_PROP"), ("KXMLBSB", "PLAYER_PROP"),
    ("KXMLBF5SPREAD", "PARTIAL_GAME"), ("KXMLBINNINGWIN", "PARTIAL_GAME"), ("KXMLBRFI", "PARTIAL_GAME"), ("KXMLBTEAMTOTAL", "PARTIAL_GAME"),
    ("KXMLBWINS", "FUTURES"), ("KXMLBCHAMP", "FUTURES"), ("KXMLBCOMBO", "COMBO"), ("KXNCAAF1QTOTAL", "OTHER_LEAGUE"),
])
def test_series_classification(series, family):
    assert classify_series(series, "MLB") == family


@pytest.mark.asyncio
async def test_non_full_game_series_yield_no_contracts_and_honest_exclusions():
    prop_event = {"event_ticker": "KXMLBHIT-26SEP042210WSHLAD", "series_ticker": "KXMLBHIT", "title": "Hits",
                  "markets": [{"ticker": "KXMLBHIT-26SEP042210WSHLAD-LADSOHTANI17-3", "event_ticker": "KXMLBHIT-26SEP042210WSHLAD",
                               "title": "Shohei Ohtani: 3+ hits?", "status": "active"}]}
    source = _kalshi_source(_KalshiFake([]))
    excluded: dict[str, int] = {}
    assert source._normalize_event(prop_event, league="MLB", sport="BASEBALL", excluded=excluded) == []
    assert excluded == {"player_props": 1}
    for fam, bucket in (("KXMLBF5SPREAD", "partial_game_or_derivative"), ("KXMLBWINS", "futures_or_season"), ("KXMLBCOMBO", "combo_or_multivariate")):
        ex: dict[str, int] = {}
        ev = {"event_ticker": f"{fam}-26SEP04X", "series_ticker": fam, "markets": [{"ticker": f"{fam}-26SEP04X-A", "event_ticker": f"{fam}-26SEP04X"}]}
        assert source._normalize_event(ev, league="MLB", sport="BASEBALL", excluded=ex) == [] and ex == {bucket: 1}


# -- 5: orientation --------------------------------------------------------------------

def test_event_ticker_gives_time_teams_orientation_and_rules_cross_check():
    p = parse_event_ticker("KXMLBGAME-26SEP041915DETCLEG2")
    assert p["starts_at"] == datetime(2026, 9, 4, 19, 15, tzinfo=ET) and (p["away_ticker"], p["home_ticker"], p["game_number"]) == ("DET", "CLE", 2)
    assert parse_event_ticker("KXMLBGAME-26SEP042010AZHOU")["away_ticker"] == "AZ"
    assert parse_rules_start("If Cincinnati wins the Cincinnati vs Los Angeles D professional baseball game originally scheduled for Sep 7, 2026 at 9:10 PM EDT, then the market resolves to Yes.") == datetime(2026, 9, 7, 21, 10, tzinfo=ET)
    assert parse_event_ticker("KXMLBGAME-26SEP04XXXXXX") is None  # unregistered pair fails closed
    source = _kalshi_source(_KalshiFake([]))
    ex: dict[str, int] = {}
    out = source._normalize_event(_kalshi_event("KXMLBGAME-26SEP042010AZHOU"), league="MLB", sport="BASEBALL", excluded=ex)
    by_yes = {dto.raw["yes_team_abbr"]: dto for dto, _ in out}
    assert by_yes["HOU"].selection == CanonicalSelection.HOME and by_yes["AZ"].selection == CanonicalSelection.AWAY
    assert by_yes["HOU"].provider_home_team == "HOU" and by_yes["HOU"].provider_away_team == "AZ"
    assert by_yes["HOU"].scheduled_start_at == datetime(2026, 9, 4, 20, 10, tzinfo=ET)
    assert by_yes["HOU"].raw["start_time_conflict"] is False
    assert by_yes["HOU"].market_type == CanonicalMarketType.FULL_GAME_MONEYLINE


# -- 6: doubleheader -------------------------------------------------------------------

def test_doubleheader_events_map_to_distinct_games(session, settings):
    s = _observer_settings(settings)
    g1 = _game(session, home=("Cleveland Guardians", "CLE"), away=("Detroit Tigers", "DET"), start=datetime(2026, 9, 4, 14, 10, tzinfo=ET))
    g2 = _game(session, home=("Cleveland Guardians", "CLE"), away=("Detroit Tigers", "DET"), start=datetime(2026, 9, 4, 19, 15, tzinfo=ET))
    source = _kalshi_source(_KalshiFake([]))
    ex: dict[str, int] = {}
    dtos = [dto for dto, _ in source._normalize_event(_kalshi_event("KXMLBGAME-26SEP041410DETCLEG1"), league="MLB", sport="BASEBALL", excluded=ex)]
    dtos += [dto for dto, _ in source._normalize_event(_kalshi_event("KXMLBGAME-26SEP041915DETCLEG2"), league="MLB", sport="BASEBALL", excluded=ex)]
    PredictionMarketObserver(session, s).ingest_contracts(dtos)
    from app.models import PredictionMarketMapping

    latest = {}
    for m in session.scalars(select(PredictionMarketMapping).order_by(PredictionMarketMapping.decided_at.desc())):
        latest.setdefault(m.provider_event_id, m)
    assert latest["KXMLBGAME-26SEP041410DETCLEG1"].mapping_status == "EXACT" and latest["KXMLBGAME-26SEP041410DETCLEG1"].canonical_game_id == g1.id
    assert latest["KXMLBGAME-26SEP041915DETCLEG2"].mapping_status == "EXACT" and latest["KXMLBGAME-26SEP041915DETCLEG2"].canonical_game_id == g2.id


# -- 7: Polymarket orientation ---------------------------------------------------------

def test_polymarket_event_and_token_orientation():
    market = {"id": "3973269", "question": "New York Yankees vs. San Diego Padres", "conditionId": "0xfa96",
              "clobTokenIds": json.dumps(["5336", "9206"]), "outcomes": json.dumps(["New York Yankees", "San Diego Padres"]),
              "sportsMarketType": "moneyline", "gameStartTime": "2026-09-05 01:40:00+00", "acceptingOrders": True, "closed": False, "active": True,
              "description": "In the upcoming MLB game between the New York Yankees and San Diego Padres, scheduled for September 4 at 9:40PM ET:\n\n"
                             "This market will resolve to \"New York Yankees\" if the New York Yankees win the game.\n\n"
                             "This market will resolve to \"San Diego Padres\" if the San Diego Padres win the game.\n\n"
                             "If the game is postponed, this market will remain open until the game has been completed. If the game is canceled entirely, "
                             "with no make-up game, or ends in a tie, this market will resolve 50-50."}
    event = {"id": "930715", "slug": "mlb-nyy-sd-2026-09-04", "title": "New York Yankees vs. San Diego Padres"}
    source = PolymarketPredictionMarketSource(gamma_base_url="https://gamma.test", clob_base_url="https://clob.test")
    contracts, reason = source._normalize_market(market, league_hint="MLB", event=event)
    assert reason is None and {c.provider_outcome_id: c.selection for c in contracts} == {"5336": CanonicalSelection.AWAY, "9206": CanonicalSelection.HOME}
    assert contracts[0].provider_home_team == "San Diego Padres" and contracts[0].provider_away_team == "New York Yankees"
    assert contracts[0].scheduled_start_at == datetime(2026, 9, 5, 1, 40, tzinfo=UTC)


# -- 8 / 9 / 10: pregame authority ------------------------------------------------------

def _record(obs: PredictionMarketObserver, contract, *, observed_at: datetime, now: datetime, bid=0.55, ask=0.58):
    quote = ProviderQuoteDTO(provider=PredictionMarketProvider(contract.provider), provider_market_id=contract.provider_market_id,
                             provider_outcome_id=contract.provider_outcome_id,
                             book=normalize_kalshi_book([(bid, 100.0)], [(round(1 - ask, 6), 80.0)]), provider_observed_at=observed_at,
                             raw={"t": observed_at.isoformat(), "bid": bid, "ask": ask})
    return obs.record_snapshot(contract=contract, quote=quote, now=now)


def _ingest_kalshi_game(session, s, *, start: datetime, event_ticker: str | None = None):
    game = _game(session, home=("Houston Astros", "HOU"), away=("Arizona Diamondbacks", "AZ"), start=start)
    event_ticker = event_ticker or _ticker_for(start, "AZ", "HOU")
    source = _kalshi_source(_KalshiFake([]))
    ex: dict[str, int] = {}
    dtos = [dto for dto, _ in source._normalize_event(_kalshi_event(event_ticker), league="MLB", sport="BASEBALL", excluded=ex)]
    obs = PredictionMarketObserver(session, s)
    obs.ingest_contracts(dtos)
    from app.models import PredictionMarketContract

    home = session.scalar(select(PredictionMarketContract).where(PredictionMarketContract.provider == "KALSHI",
                                                                  PredictionMarketContract.selection == "HOME"))
    return game, obs, home


def test_final_pregame_snapshot_survives_in_play_and_final_observations(session, settings, monkeypatch):
    s = _observer_settings(settings)
    now = datetime.now(UTC)
    start = now + timedelta(minutes=20)
    game, obs, contract = _ingest_kalshi_game(session, s, start=start)
    a = _record(obs, contract, observed_at=now - timedelta(minutes=5), now=now - timedelta(minutes=5), bid=0.55, ask=0.58)  # T-25m
    b = _record(obs, contract, observed_at=now, now=now, bid=0.57, ask=0.60)                                              # T-20m (final pregame)
    assert a.pregame_eligible and b.pregame_eligible and b.timeline_cut == "T_MINUS_30_MINUTES"
    # Market goes in-play, then the game is FINAL: later observations are recorded honestly but never replace the pregame price.
    c = _record(obs, contract, observed_at=start + timedelta(minutes=30), now=start + timedelta(minutes=30), bid=0.90, ask=0.93)
    assert c.in_play and not c.pregame_eligible and c.timeline_cut == "IN_PLAY_FIRST_OBSERVED"
    game.status = GameStatus.FINAL
    session.commit()
    monkeypatch.setattr(PredictionMarketReadService, "_aisp_home_probability", lambda self, g: (0.50, True))
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    row = board.rows[0]
    kalshi = next(c for c in row.provider_coverage if c.provider == "KALSHI")
    assert kalshi.state == "QUOTED_UNREVIEWED" and kalshi.pregame_observed_at == b.provider_observed_at and kalshi.latest_in_play is True
    assert row.kalshi_home_probability == pytest.approx(0.585)  # the T-20m midpoint, not 0.915
    prob = next(p for p in row.providers if p.provider == "KALSHI")
    assert prob.observation_kind == "FINAL_PREGAME" and prob.latest_in_play is True and prob.in_play is False
    assert row.evidence_state == "PREGAME_UNREVIEWED"


def test_delayed_persistence_cannot_create_false_pregame_evidence(session, settings):
    s = _observer_settings(settings)
    now = datetime.now(UTC)
    start = now - timedelta(minutes=10)  # game already started
    game, obs, contract = _ingest_kalshi_game(session, s, start=start)
    # Source time claims pregame, but the observation only arrives 10 minutes after first pitch.
    snap = _record(obs, contract, observed_at=start - timedelta(minutes=3), now=now)
    assert snap.pregame_eligible is False and "LATE_INGESTION" in (snap.quality_flags or []) and snap.timeline_cut is None
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    kalshi = next(c for c in board.rows[0].provider_coverage if c.provider == "KALSHI")
    assert kalshi.state == "QUOTED_STRUCTURALLY_EXCLUDED" and kalshi.pregame_observed_at is None


# -- 11: disjoint states sum to the slate ------------------------------------------------

def test_disjoint_game_states_sum_to_the_canonical_slate(session, settings, monkeypatch):
    s = _observer_settings(settings)
    now = datetime.now(UTC)
    start = now + timedelta(minutes=45)
    game, obs, contract = _ingest_kalshi_game(session, s, start=start)
    _record(obs, contract, observed_at=now, now=now)
    _game(session, home=("Seattle Mariners", "SEA"), away=("Athletics", "ATH"), start=start)  # no mapped market
    monkeypatch.setattr(PredictionMarketReadService, "_aisp_home_probability", lambda self, g: (0.50, True))
    board = PredictionMarketReadService(session, s).board(_event_date(s, start))
    cov = board.coverage
    assert board.canonical_games == 2
    assert cov["evidence_state_sum"] == 2 == cov["PREGAME_UNREVIEWED"] + cov["NO_MAPPED_MARKET_GAME"]
    assert cov["exclusive_sum"] == 2
    assert board.coverage_by_provider["KALSHI"]["QUOTED_UNREVIEWED"] == 1 and board.coverage_by_provider["POLYMARKET"]["NO_MAPPED_MARKET"] == 2


# -- 12: consensus gating -----------------------------------------------------------------

def test_consensus_requires_two_reviewed_pregame_providers(session, settings):
    s = _observer_settings(settings)
    now = datetime.now(UTC)
    start = now + timedelta(minutes=45)
    game, obs, contract = _ingest_kalshi_game(session, s, start=start)
    _record(obs, contract, observed_at=now, now=now)
    assert obs.compute_consensus(game.id, now=now) >= 0
    from app.models import PredictionMarketConsensusSnapshot

    cons = session.scalar(select(PredictionMarketConsensusSnapshot).where(PredictionMarketConsensusSnapshot.canonical_game_id == game.id)
                          .order_by(PredictionMarketConsensusSnapshot.computed_at.desc()))
    assert cons is None or cons.eligible is False  # one unreviewed provider can never form a consensus


# -- 14: protected records untouched ------------------------------------------------------

def test_provider_observations_never_touch_forecasts_or_experiment_records(session, settings, monkeypatch):
    s = _observer_settings(settings)
    before = tuple(session.scalar(select(func.count()).select_from(t)) for t in (Forecast, ShadowExperimentDecision, OfficialAspPosition))
    now = datetime.now(UTC)
    game, obs, contract = _ingest_kalshi_game(session, s, start=now + timedelta(minutes=45))
    _record(obs, contract, observed_at=now, now=now)
    monkeypatch.setattr(PredictionMarketReadService, "_aisp_home_probability", lambda self, g: (0.50, True))
    PredictionMarketReadService(session, s).board(_event_date(s, now + timedelta(minutes=45)))
    after = tuple(session.scalar(select(func.count()).select_from(t)) for t in (Forecast, ShadowExperimentDecision, OfficialAspPosition))
    assert before == after
    assert session.scalar(select(func.count()).select_from(PredictionMarketSnapshot)) == 1


# -- template authority --------------------------------------------------------------------

def test_template_authority_holds_closed_until_a_human_decision_then_inherits(session, settings):
    s = _observer_settings(settings)
    now = datetime.now(UTC)
    game, obs, contract = _ingest_kalshi_game(session, s, start=now + timedelta(hours=3))
    from app.models import PredictionMarketMapping

    def latest_mapping():
        return session.scalar(select(PredictionMarketMapping).where(PredictionMarketMapping.provider == "KALSHI")
                              .order_by(PredictionMarketMapping.decided_at.desc()))

    m = latest_mapping()
    assert m.mapping_status == "EXACT" and m.semantics_status == "SEMANTICS_REVIEW_REQUIRED" and "HELD_CLOSED" in (m.reason or "")
    authority = SemanticTemplateAuthority(session)
    packet = authority.review_packet()
    assert {t["template_key"] for t in packet} == {KALSHI_MLB_WINNER, POLYMARKET_MLB_WINNER}
    assert all(t["status"] == STATUS_HELD and t["decision_required"] for t in packet)
    # One human decision per provider template — never per game.
    authority.decide(provider="KALSHI", template_key=KALSHI_MLB_WINNER, status=STATUS_APPROVED, decided_by_user_id=None, notes="approved in test")
    source = _kalshi_source(_KalshiFake([]))
    ex: dict[str, int] = {}
    dtos = [dto for dto, _ in source._normalize_event(_kalshi_event(_ticker_for(game.starts_at, "AZ", "HOU")), league="MLB", sport="BASEBALL", excluded=ex)]
    obs.ingest_contracts(dtos, now=now + timedelta(minutes=1))
    m2 = latest_mapping()
    assert m2.semantics_status == "SEMANTICS_MATCHED" and m2.decision_source == "TEMPLATE" and "@v2:APPROVED" in (m2.reason or "")
    # An ambiguous market (rules deviate from the template) stays held even after approval.
    odd = _kalshi_event("KXMLBGAME-26SEP042140NYYSD")
    for mk in odd["markets"]:
        mk["rules_primary"] = "If the game is decided after nine innings only, the market resolves to Yes."
    _game(session, home=("San Diego Padres", "SD"), away=("New York Yankees", "NYY"), start=datetime(2026, 9, 4, 21, 40, tzinfo=ET))
    dtos = [dto for dto, _ in source._normalize_event(odd, league="MLB", sport="BASEBALL", excluded=ex)]
    obs.ingest_contracts(dtos, now=now + timedelta(minutes=2))
    held = session.scalar(select(PredictionMarketMapping).where(PredictionMarketMapping.provider_event_id == "KXMLBGAME-26SEP042140NYYSD")
                          .order_by(PredictionMarketMapping.decided_at.desc()))
    assert held.semantics_status != "SEMANTICS_MATCHED"
