"""Player-prop quotes: parsing, fail-closed identity, quote-only reads and the
inactive, budget-bound single-event collector. No network is ever reached."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.data_sources.base import SourceError
from app.data_sources.odds import TheOddsAPIAdapter
from app.models import (
    ExternalIdMapping, Game, GameStatus, GameTeam, League, LineupEntry, LineupObservation, Player,
    PlayerPropQuote, ProviderRequestLedger, Roster, Season, Sport, Team,
)
from app.player_props import collector
from app.player_props import contract as c
from app.player_props.identity import normalize_person, resolve_game, resolve_player
from app.player_props.ingest import ingest_event_odds, parse_event_odds
from app.player_props.read import QUOTE_FIELDS, player_props_for_games
from app.probability.assessments import player_props
from app.services.odds_budget_lanes import LANE_MLB_REQUIRED, LANE_OTHER_RESEARCH, lane_for_request

FIXTURE = Path(__file__).parent / "fixtures" / "the_odds_api" / "event_odds_player_props.json"
EVENT_ID = "e912304de2b2ce35b473ce2ecd3d1502"
START = datetime(2026, 9, 20, 23, 5, tzinfo=UTC)
OBSERVED = datetime(2026, 9, 20, 18, 0, tzinfo=UTC)
INGESTED = datetime(2026, 9, 20, 18, 2, tzinfo=UTC)
SENTINEL = "test-sentinel-player-prop-key-never-leaks"
FORBIDDEN = ("prob", "fair", "edge", "ev", "expected", "decision", "confidence", "recommend", "position",
             "kelly", "stake", "model")


def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _league(session, code: str) -> League:
    league = session.scalar(select(League).where(League.code == code))
    if league is None:
        sport = Sport(code=f"S_{code}", name=code)
        session.add(sport)
        session.flush()
        league = League(sport_id=sport.id, code=code, name=code)
        session.add(league)
        session.flush()
    return league


def _team(session, league, name, abbr):
    team = Team(league_id=league.id, name=name, abbreviation=abbr)
    session.add(team)
    session.flush()
    return team


def _player(session, name, team=None):
    player = Player(full_name=name, team_id=team.id if team else None)
    session.add(player)
    session.flush()
    return player


@pytest.fixture
def world(session):
    """NYY (home) v BOS at START, with governed evidence for the cast below."""

    mlb = _league(session, "MLB")
    season = Season(league_id=mlb.id, name="2026", starts_at=START - timedelta(days=200),
                    ends_at=START + timedelta(days=60))
    session.add(season)
    session.flush()
    nyy = _team(session, mlb, "New York Yankees", "NYY")
    bos = _team(session, mlb, "Boston Red Sox", "BOS")
    lad = _team(session, mlb, "Los Angeles Dodgers", "LAD")
    game = Game(league_id=mlb.id, season_id=season.id, starts_at=START, status=GameStatus.SCHEDULED)
    session.add(game)
    session.flush()
    cole = _player(session, "Gerrit Cole")
    session.add_all([GameTeam(game_id=game.id, team_id=nyy.id, is_home=True, probable_pitcher_id=cole.id),
                     GameTeam(game_id=game.id, team_id=bos.id, is_home=False)])
    as_of = START - timedelta(days=30)

    def roster(player, team):
        session.add(Roster(season_id=season.id, team_id=team.id, player_id=player.id, available_as_of=as_of))

    cast = {"cole": cole}
    cast["judge"] = _player(session, "Aaron Judge")
    roster(cast["judge"], nyy)
    cast["devers"] = _player(session, "Rafael Devers")
    lineup = LineupObservation(game_id=game.id, team_id=bos.id, lineup_status="CONFIRMED", is_complete=True,
                               observed_at=START - timedelta(hours=6), available_as_of=START - timedelta(hours=6),
                               source="MLB_STATS_API")
    session.add(lineup)
    session.flush()
    session.add(LineupEntry(lineup_observation_id=lineup.id, player_id=cast["devers"].id, batting_order=3))
    cast["ramirez"] = _player(session, "José Ramírez")
    roster(cast["ramirez"], bos)
    # Two "Will Smith"s, one on each side of this game: ambiguous.
    cast["smith_nyy"] = _player(session, "Will Smith")
    cast["smith_bos"] = _player(session, "Will Smith")
    roster(cast["smith_nyy"], nyy)
    roster(cast["smith_bos"], bos)
    # Two "Max Muncy"s: one plays for LAD, the other has no governed evidence.
    cast["muncy_lad"] = _player(session, "Max Muncy")
    cast["muncy_other"] = _player(session, "Max Muncy", team=nyy)  # Player.team_id is not evidence
    roster(cast["muncy_lad"], lad)
    session.flush()
    return {"game": game, "nyy": nyy, "bos": bos, "lad": lad, "season": season, **cast}


def _map_event(session, game, event_id=EVENT_ID):
    session.add(ExternalIdMapping(entity_type="game", entity_id=game.id, source="THE_ODDS_API", external_id=event_id))
    session.flush()


def _table_counts(session, *, skip=("player_prop_quotes", "provider_request_ledger")):
    from app.db.base import Base

    return {name: session.scalar(select(func.count()).select_from(table))
            for name, table in Base.metadata.tables.items() if name not in skip}


# ---------------------------------------------------------------- parsing & wording

def test_fixture_parses_every_outcome_exactly():
    header, outcomes = parse_event_odds(payload())
    assert header["event_id"] == EVENT_ID and header["sport_key"] == "baseball_mlb"
    assert header["home_team"] == "New York Yankees" and header["commence_time"] == START
    assert len(outcomes) == 15
    judge_over = outcomes[0]
    assert (judge_over.sportsbook_key, judge_over.market_key, judge_over.outcome_name, judge_over.player_name,
            judge_over.point, judge_over.american) == ("draftkings", "batter_hits", "Over", "Aaron Judge", 0.5, -185)
    assert judge_over.observed_at == OBSERVED  # the market's own update time, not the book's
    hr_yes = next(o for o in outcomes if o.market_key == "batter_home_runs" and o.outcome_name == "Yes")
    assert hr_yes.observed_at == datetime(2026, 9, 20, 18, 1, tzinfo=UTC)  # falls back to the book time
    assert len({o.payload_sha256 for o in outcomes}) == 15


@pytest.mark.parametrize(("market", "outcome", "point", "text"), [
    ("batter_hits", "Over", 0.5, "1+ hit"),
    ("batter_hits", "Over", 1.5, "2+ hits"),
    ("batter_hits", "Under", 0.5, "No hit"),
    ("batter_hits", "Under", 1.5, "Under 1.5 hits"),
    ("batter_total_bases", "Over", 1.5, "2+ total bases"),
    ("batter_home_runs", "Yes", None, "1+ home run"),
    ("batter_home_runs", "No", None, "No home run"),
    ("pitcher_strikeouts", "Over", 6.5, "7+ strikeouts"),
    ("pitcher_strikeouts", "Over", 6.0, "Over 6 strikeouts"),
])
def test_wording(market, outcome, point, text):
    assert c.describe(market, outcome, point) == text


def test_prices_and_names():
    assert c.decimal_from_american(-185) == pytest.approx(1.540541, abs=1e-6)
    assert c.decimal_from_american(240) == 3.4
    assert c.decimal_from_american(50) is None and c.decimal_from_american(0) is None
    assert normalize_person("José Ramírez") == normalize_person("Jose Ramirez Jr.") == "jose ramirez"
    assert normalize_person("J.D. Martinez") == "jd martinez"


# ---------------------------------------------------------------- identity

def test_game_binds_by_mapping_then_by_matcher(session, world):
    game = world["game"]
    unmapped = resolve_game(session, event_id=EVENT_ID, home_team="New York Yankees",
                            away_team="Boston Red Sox", commence_time=START)
    assert unmapped.game_id == game.id and unmapped.reason is None
    _map_event(session, game)
    mapped = resolve_game(session, event_id=EVENT_ID, home_team="New York Yankees",
                          away_team="Boston Red Sox", commence_time=START)
    assert mapped.game_id == game.id and set(mapped.team_ids) == {world["nyy"].id, world["bos"].id}


def test_game_refusals(session, world):
    assert resolve_game(session, event_id="nomatch00000", home_team="Chicago Cubs",
                        away_team="St. Louis Cardinals", commence_time=START).reason == c.GAME_UNRESOLVED
    # A mapped event whose participants are reversed names a different fixture.
    _map_event(session, world["game"])
    assert resolve_game(session, event_id=EVENT_ID, home_team="Boston Red Sox",
                        away_team="New York Yankees", commence_time=START).reason == c.PARTICIPANT_CONFLICT
    # An event mapped to a non-MLB game is never bound.
    nfl = _league(session, "NFL")
    other = Game(league_id=nfl.id, starts_at=START, status=GameStatus.SCHEDULED)
    session.add(other)
    session.flush()
    _map_event(session, other, "nflevent0000001")
    assert resolve_game(session, event_id="nflevent0000001", home_team="New York Yankees",
                        away_team="Boston Red Sox", commence_time=START).reason == c.GAME_NOT_MLB


def test_player_resolution_fails_closed(session, world):
    game = resolve_game(session, event_id=EVENT_ID, home_team="New York Yankees",
                        away_team="Boston Red Sox", commence_time=START)
    judge = resolve_player(session, game, "Aaron Judge", reference=INGESTED)
    assert (judge.player_id, judge.team_id, judge.evidence) == (world["judge"].id, world["nyy"].id, "ROSTER")
    devers = resolve_player(session, game, "Rafael Devers", reference=INGESTED)
    assert (devers.team_id, devers.evidence) == (world["bos"].id, "LINEUP")
    cole = resolve_player(session, game, "Gerrit Cole", reference=INGESTED)
    assert (cole.team_id, cole.evidence) == (world["nyy"].id, "PROBABLE_PITCHER")
    assert resolve_player(session, game, "Jose Ramirez Jr.", reference=INGESTED).player_id == world["ramirez"].id
    assert resolve_player(session, game, "Will Smith", reference=INGESTED).reason == c.PLAYER_AMBIGUOUS
    assert resolve_player(session, game, "Max Muncy", reference=INGESTED).reason == c.PLAYER_WRONG_TEAM
    assert resolve_player(session, game, "Nobody Invented", reference=INGESTED).reason == c.PLAYER_UNKNOWN
    # Evidence available only after the reference time does not count.
    early = START - timedelta(days=40)
    assert resolve_player(session, game, "Aaron Judge", reference=early).reason == c.PLAYER_NO_EVIDENCE
    # A lineup posted after first pitch never binds a pregame quote.
    assert resolve_player(session, game, "Rafael Devers",
                          reference=START - timedelta(hours=7)).reason == c.PLAYER_NO_EVIDENCE


def test_ingest_stores_resolved_and_quarantined_and_never_creates_players(session, world):
    players_before = session.scalar(select(func.count(Player.id)))
    result = ingest_event_odds(session, payload(), ingested_at=INGESTED)
    assert (result.resolved, result.quarantined, result.duplicates) == (7, 8, 0)
    assert result.reasons == {c.PLAYER_AMBIGUOUS: 1, c.PLAYER_WRONG_TEAM: 1, c.PLAYER_UNKNOWN: 1,
                              c.ORIENTATION_CONFLICT: 2, c.MARKET_UNSUPPORTED: 1, c.PRICE_MISSING: 1,
                              c.LINE_MISSING: 1}
    assert session.scalar(select(func.count(Player.id))) == players_before
    rows = session.scalars(select(PlayerPropQuote)).all()
    for row in rows:
        assert row.game_id == world["game"].id and row.provider_event_id == EVENT_ID
        if row.identity_status == "RESOLVED":
            assert row.selectable and row.player_id and row.team_id and row.outcome_side
            assert row.quarantine_reason is None
        else:
            assert not row.selectable and row.player_id is None and row.quarantine_reason
    judge = next(r for r in rows if r.provider_player_name == "Aaron Judge" and r.provider_outcome_name == "Over"
                 and r.provider_market_key == "batter_hits")
    assert (judge.line, judge.american_price, judge.sportsbook_title, judge.outcome_side) == (0.5, -185, "DraftKings", "OVER")
    assert judge.provider_observed_at.replace(tzinfo=UTC) == OBSERVED
    assert judge.ingested_at.replace(tzinfo=UTC) == INGESTED
    # Idempotent: the same observation is never stored twice.
    again = ingest_event_odds(session, payload(), ingested_at=INGESTED + timedelta(minutes=5))
    assert (again.resolved, again.quarantined, again.duplicates) == (0, 0, 15)


def test_unbound_event_is_quarantined_whole(session, world):
    body = payload()
    body["home_team"], body["away_team"] = "Chicago Cubs", "St. Louis Cardinals"
    result = ingest_event_odds(session, body, ingested_at=INGESTED)
    assert result.resolved == 0 and result.reasons == {c.GAME_UNRESOLVED: 15}
    assert session.scalar(select(func.count(PlayerPropQuote.id)).where(PlayerPropQuote.selectable)) == 0
    body["sport_key"], body["id"] = "americanfootball_nfl", "nflevent0000001"
    assert ingest_event_odds(session, body, ingested_at=INGESTED).reasons == {c.GAME_NOT_MLB: 15}


# ---------------------------------------------------------------- read model

def test_read_model_is_quotes_only_with_freshness(session, world):
    ingest_event_odds(session, payload(), ingested_at=INGESTED)
    fresh = player_props_for_games(session, [str(world["game"].id)], now=OBSERVED + timedelta(minutes=30))
    assert fresh["status"] == "AVAILABLE" and len(fresh["quotes"]) == 7
    assert fresh["assessment"] == "Sportsbook market — no AISP assessment"
    assert fresh["producer_disposition"]["player_prop_probabilities"] == "INSUFFICIENT_EVIDENCE"
    assert fresh["quarantined"][c.PLAYER_AMBIGUOUS] == 1
    for quote in fresh["quotes"]:
        assert set(quote) == set(QUOTE_FIELDS)
        for key in quote:
            assert not any(key == f or key.startswith(f + "_") or key.endswith("_" + f) for f in FORBIDDEN), key
        assert quote["selectable"] and quote["state"] == "CURRENT"
    hit = next(q for q in fresh["quotes"] if q["player_name"] == "Aaron Judge" and q["market_key"] == "batter_hits"
               and q["outcome"] == "OVER")
    assert hit["description"] == "1+ hit"
    assert (hit["provider_outcome"], hit["line"], hit["american"], hit["sportsbook"]) == ("Over", 0.5, -185, "DraftKings")
    assert hit["observed_at"] == OBSERVED.isoformat()
    stale = player_props_for_games(session, [str(world["game"].id)], now=OBSERVED + timedelta(minutes=245))
    assert all(not q["selectable"] and q["state"] == "STORED_REFERENCE_ONLY" for q in stale["quotes"])
    assert player_props(session, [str(world["game"].id)], OBSERVED)["quotes"]
    empty = player_props()
    assert empty["status"] == "NOT_STORED" and empty["quotes"] == []


def test_quotes_never_touch_official_state(session, world):
    before = _table_counts(session)
    ingest_event_odds(session, payload(), ingested_at=INGESTED)
    player_props_for_games(session, [str(world["game"].id)], now=OBSERVED)
    assert _table_counts(session) == before


def test_model_has_no_assessment_columns():
    columns = set(PlayerPropQuote.__table__.columns.keys())
    for column in columns:
        assert not set(column.split("_")) & {"probability", "fair", "edge", "ev", "expected", "decision",
                                             "confidence", "kelly", "stake", "model"}, column


# ---------------------------------------------------------------- lanes & collector

def test_player_props_never_use_protected_lanes():
    assert lane_for_request(league_code="MLB", markets=["batter_hits"]) == LANE_OTHER_RESEARCH
    assert lane_for_request(league_code="MLB", markets=["pitcher_strikeouts"]) == LANE_OTHER_RESEARCH
    assert lane_for_request(league_code="MLB", markets=["h2h"]) == LANE_MLB_REQUIRED


def _settings(settings, **overrides):
    # Budget settings are stated, not inherited from the deployed .env.
    base = {"odds_daily_request_budget": 1, "odds_monthly_credit_cap": 500, "odds_monthly_credit_reserve": 150,
            "odds_daily_credit_budget": 18, "odds_quota_floor": 10, "odds_manual_cooldown_minutes": 5}
    return settings.model_copy(update={**base, **overrides})


def _reading(session, *, used, remaining, at):
    fingerprint = f"{used:064d}"
    """One completed moneyline request carrying an in-window provider balance."""

    reserved = ProviderRequestLedger(
        provider="THE_ODDS_API", endpoint_key="baseball_mlb_odds", requested_at=at, status="RESERVED",
        request_fingerprint=fingerprint, source_mode="LIVE",
        response_metadata={"request_shape": {"markets": ["h2h"], "regions": ["us"]}})
    session.add(reserved)
    session.flush()
    session.add(ProviderRequestLedger(
        provider="THE_ODDS_API", endpoint_key="baseball_mlb_odds", requested_at=at, completed_at=at,
        status="SUCCEEDED", request_fingerprint=fingerprint, source_mode="LIVE", reservation_id=reserved.id,
        quota_cost=1, quota_remaining=remaining, response_metadata={"requests_used": used}))
    session.flush()


NOW = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)


def test_default_plan_is_a_blocked_dry_run_that_writes_nothing(session, settings, world):
    _map_event(session, world["game"])
    before = session.scalar(select(func.count(ProviderRequestLedger.id)))
    plan = collector.plan(session, _settings(settings), world["game"].id, now=NOW)
    assert plan["mode"] == "DRY_RUN" and plan["provider_calls_made"] == 0 and plan["credits_spent"] == 0
    assert plan["predicted_credits"] == 1 and plan["lane"] == LANE_OTHER_RESEARCH
    assert plan["provider_event_id"] == EVENT_ID and plan["provider_event_source"] == "EXTERNAL_ID_MAPPING"
    assert plan["request"]["path"] == f"/v4/sports/baseball_mlb/events/{EVENT_ID}/odds"
    assert plan["request"]["params"] == {"regions": "us", "markets": "batter_hits",
                                         "oddsFormat": "american", "dateFormat": "iso"}
    assert not plan["may_execute"]
    assert {"live_execution_enabled", "research_lane_allocation"} <= set(plan["blocked_by"])
    assert "apiKey" not in json.dumps(plan)
    assert session.scalar(select(func.count(ProviderRequestLedger.id))) == before


def test_plan_passes_only_when_enabled_funded_and_inside_the_reserve(session, settings, world):
    _map_event(session, world["game"])
    _reading(session, used=1, remaining=499, at=NOW - timedelta(days=1))
    on = _settings(settings, player_prop_live_enabled=True, player_prop_research_monthly_credits=5,
                   player_prop_research_daily_credits=1)
    plan = collector.plan(session, on, world["game"].id, now=NOW)
    assert plan["may_execute"], plan["blocked_by"]


def test_reserve_rejection(session, settings, world):
    _map_event(session, world["game"])
    on = _settings(settings, player_prop_live_enabled=True, player_prop_research_monthly_credits=5,
                   player_prop_research_daily_credits=1)
    # Lanes attribute 1 credit; the provider counts 300. Either way the reserve binds.
    # 300 used: 50 spendable above the 150 reserve, all owed to protected MLB lanes.
    _reading(session, used=300, remaining=200, at=NOW - timedelta(days=1))
    plan = collector.plan(session, on, world["game"].id, now=NOW)
    lane = next(ch for ch in plan["checks"] if ch["check"] == "research_lane_allocation")
    assert not lane["ok"] and "reserved for protected lanes" in lane["detail"]
    # 350 used: nothing is spendable above the reserve at all.
    _reading(session, used=350, remaining=150, at=NOW - timedelta(hours=20))
    plan = collector.plan(session, on, world["game"].id, now=NOW)
    assert "monthly_and_daily_budget" in plan["blocked_by"]


def test_daily_slot_started_game_and_shape_refusals(session, settings, world):
    _map_event(session, world["game"])
    on = _settings(settings, player_prop_live_enabled=True, player_prop_research_monthly_credits=5,
                   player_prop_research_daily_credits=1)
    _reading(session, used=1, remaining=499, at=NOW - timedelta(hours=1))  # today's only request slot
    assert "daily_request_slot" in collector.plan(session, on, world["game"].id, now=NOW)["blocked_by"]
    assert "game_not_started" in collector.plan(session, on, world["game"].id,
                                                now=START + timedelta(seconds=1))["blocked_by"]
    for kwargs in ({"markets": ["batter_hits", "batter_total_bases"]}, {"markets": ["h2h"]},
                   {"markets": []}, {"regions": "us,uk"}, {"regions": "uk"}):
        with pytest.raises(collector.PlanRefused):
            collector.plan(session, on, world["game"].id, now=NOW, **kwargs)
    unmapped = Game(league_id=world["game"].league_id, starts_at=START, status=GameStatus.SCHEDULED)
    session.add(unmapped)
    session.flush()
    plan = collector.plan(session, on, unmapped.id, now=NOW)
    assert plan["provider_event_id"] is None and "provider_event_bound" in plan["blocked_by"]


class _Recorder:
    def __init__(self, status=200, body=None):
        self.calls: list[httpx.Request] = []
        self.status, self.body = status, body if body is not None else payload()

    def client(self):
        def handler(request):
            self.calls.append(request)
            return httpx.Response(self.status, json=self.body, headers={
                "x-requests-remaining": "489", "x-requests-used": "11", "x-requests-last": "1"})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_execute_refuses_without_confirm_or_when_blocked(session, settings, world):
    _map_event(session, world["game"])
    recorder = _Recorder()
    keyed = _settings(settings, the_odds_api_key=SENTINEL)
    dry = await collector.execute(session, keyed, world["game"].id, client=recorder.client(), now=NOW)
    assert dry["mode"] == "DRY_RUN"
    refused = await collector.execute(session, keyed, world["game"].id, confirm=True,
                                      client=recorder.client(), now=NOW)
    assert refused["mode"] == "REFUSED" and refused["provider_calls_made"] == 0
    assert recorder.calls == []
    assert session.scalar(select(func.count(ProviderRequestLedger.id))) == 0


@pytest.mark.asyncio
async def test_execute_makes_one_bounded_call_and_never_leaks_the_key(session, settings, world, caplog):
    _map_event(session, world["game"])
    _reading(session, used=1, remaining=499, at=datetime.now(UTC) - timedelta(days=1))
    world["game"].starts_at = datetime.now(UTC) + timedelta(hours=6)
    session.flush()
    on = _settings(settings, the_odds_api_key=SENTINEL, player_prop_live_enabled=True,
                   player_prop_research_monthly_credits=5, player_prop_research_daily_credits=1)
    recorder = _Recorder()
    result = await collector.execute(session, on, world["game"].id, confirm=True, client=recorder.client())
    assert result["mode"] == "EXECUTED", result.get("blocked_by")
    assert len(recorder.calls) == 1
    request = recorder.calls[0]
    assert request.url.path == f"/v4/sports/baseball_mlb/events/{EVENT_ID}/odds"
    assert request.url.params["markets"] == "batter_hits" and request.url.params["regions"] == "us"
    assert (result["credits_spent"], result["remaining_reported"]) == (1, 489)
    rows = session.scalars(select(ProviderRequestLedger).where(
        ProviderRequestLedger.endpoint_key == collector.ENDPOINT_KEY)).all()
    assert sorted(r.status for r in rows) == ["RESERVED", "SUCCEEDED"]
    done = next(r for r in rows if r.status == "SUCCEEDED")
    assert (done.quota_cost, done.quota_remaining) == (1, 489)
    stored = session.scalars(select(PlayerPropQuote)).all()
    assert stored and all(q.request_ledger_id == done.reservation_id for q in stored)
    # The key appears nowhere we keep or return.
    blob = json.dumps(result, default=str) + json.dumps([r.response_metadata for r in rows], default=str)
    blob += json.dumps([q.detail for q in stored], default=str) + caplog.text
    assert SENTINEL not in blob


@pytest.mark.asyncio
async def test_adapter_errors_never_carry_the_url_or_key():
    def handler(request):
        raise httpx.ConnectError(f"boom {request.url}")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError) as caught:
            await TheOddsAPIAdapter(api_key=SENTINEL, client=client).get_event_odds(EVENT_ID, ["batter_hits"])
    assert SENTINEL not in str(caught.value) and "apiKey" not in str(caught.value)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as client:
        with pytest.raises(SourceError) as caught:
            await TheOddsAPIAdapter(api_key=SENTINEL, client=client).get_event_odds(EVENT_ID, ["batter_hits"])
    assert caught.value.status_code == 401 and SENTINEL not in str(caught.value)
    for bad_event, markets in (("../sports", ["batter_hits"]), (EVENT_ID, []), (EVENT_ID, ["h2h,spreads"])):
        with pytest.raises(SourceError):
            await TheOddsAPIAdapter(api_key=SENTINEL).get_event_odds(bad_event, markets)
    with pytest.raises(SourceError):
        await TheOddsAPIAdapter(api_key="").get_event_odds(EVENT_ID, ["batter_hits"])
