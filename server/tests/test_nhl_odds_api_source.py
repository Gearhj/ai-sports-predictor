"""The Odds API as the NHL (icehockey_nhl) price source for AISP_NHL_PAPER_POLICY_V1. Built, OFF, UNFUNDED.

Offline only: every provider response here is a hand-built FIXTURE in the real
Odds API v4 /odds shape (the same shape ``TheOddsAPIAdapter._normalize_event``
parses for MLB, NFL and CFB), and every NHL forecast is a PREVIEW FIXTURE from
``tests.nhl_fixtures`` imported into a throwaway SQLite database. Team names are
real NHL names only because the binder matches on them. No request is ever sent.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import Settings
from app.data_sources.base import AdapterResponse, OddsEventDTO, SourceEnvelope
from app.data_sources.odds import TheOddsAPIAdapter
from app.ingestion.odds_league import canonical_league_for
from app.models import (
    ExternalIdMapping, Game, GameStatus, IngestionRun, Market, MarketOutcome, OddsSnapshot, OfficialAspPosition,
    ProviderRequestLedger,
)
from app.models.nhl_research import NhlResearchForecast
from app.nhl import identity, paper
from app.nhl import odds_api_price as nhl_odds
from app.services import odds_budget_lanes as lanes
from app.services.research_paper_policy import NHL
from tests import nhl_fixtures as fx
from tests.test_nhl_paper_lifecycle import START, _activate, _eligibility, _forecast, _settings

FETCHED = START - timedelta(minutes=50)            # inside the T-75..T-35 buy window
BOOK_CLOCK = START - timedelta(minutes=55)
DECIDE_AT = START - timedelta(minutes=20)          # inside the T-30..T-15 checkpoint
TOR_MTL = ("Toronto Maple Leafs", "Montreal Canadiens")
COL_DAL = ("Colorado Avalanche", "Dallas Stars")


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def raw_event(event_id: str, home: str, away: str, *, commence=START, home_ml=-150, away_ml=+135,
              clock=BOOK_CLOCK, books=("draftkings",), outcomes=None, market_key="h2h", market_clock=True) -> dict:
    """One Odds API v4 icehockey_nhl event (FIXTURE ids), every book stamped ``clock``."""

    def book(key):
        market = {"key": market_key, "outcomes": outcomes if outcomes is not None else [
            {"name": home, "price": home_ml}, {"name": away, "price": away_ml}]}
        if market_clock:
            market["last_update"] = _stamp(clock)
            return {"key": key, "title": key.title(), "last_update": _stamp(clock + timedelta(seconds=30)),
                    "markets": [market]}
        return {"key": key, "title": key.title(), "last_update": _stamp(clock), "markets": [market]}

    return {"id": event_id, "sport_key": "icehockey_nhl", "sport_title": "NHL",
            "commence_time": _stamp(commence), "home_team": home, "away_team": away,
            "bookmakers": [book(k) for k in books]}


def events(*raw: dict) -> list[OddsEventDTO]:
    """Through the SAME adapter normaliser the live path uses."""

    return [OddsEventDTO.model_validate(TheOddsAPIAdapter._normalize_event(r).model_dump(mode="json")) for r in raw]


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _measured_balance(session, remaining=500, used=0):
    """A provider reading inside this billing month (the provider's own counter)."""

    now = datetime.now(UTC)
    reserved = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key="quota_recovery_probe",
                                     requested_at=now - timedelta(minutes=1), status="RESERVED",
                                     request_fingerprint="b" * 64)
    session.add(reserved)
    session.flush()
    session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key="quota_recovery_probe",
                                      requested_at=reserved.requested_at, completed_at=now,
                                      status="SUCCEEDED", request_fingerprint="b" * 64, quota_cost=0,
                                      reservation_id=reserved.id, quota_remaining=remaining,
                                      response_metadata={"requests_used": used}))
    session.flush()


def _in_process(session, **kwargs) -> NhlResearchForecast:
    """A forecast carrying the AISP in-process provenance of the 2026-10-16 interface contract."""

    row, _game = _forecast(session, **kwargs)
    row.producer_evidence_status = nhl_odds.IN_PROCESS_EVIDENCE_STATUS
    row.evidence_mode = "AISP_IN_PROCESS_FROZEN_GEN1"
    row.producer_evidence_ref = {**(row.producer_evidence_ref or {}), "restComplete": True}
    session.flush()
    return row


def _bare_game(session, source_id="2026020250", home="BOS", away="NYR", start=START):
    """A SCHEDULED NHL game with no forecast at all (identity only)."""

    return identity.ensure_game(session, fx.game_block(source_id, home, away, start))


def _ledger(session, *, at, status="RESERVED", endpoint=nhl_odds.ENDPOINT_KEY):
    fingerprint = hashlib.sha256(f"{endpoint}|{status}|{at.isoformat()}".encode()).hexdigest()
    row = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=endpoint, requested_at=at, status=status,
                                request_fingerprint=fingerprint)
    session.add(row)
    session.flush()
    return row


# -- adapter, ingest map, settings: icehockey_nhl added, everything else unchanged --
def test_adapter_accepts_icehockey_nhl_and_still_refuses_other_keys():
    for key in ("baseball_mlb", "americanfootball_nfl", "americanfootball_ncaaf", "icehockey_nhl"):
        assert TheOddsAPIAdapter("k", sport_key=key).sport_key == key
    assert TheOddsAPIAdapter("k").sport_key == "baseball_mlb"                 # default unchanged
    for key in ("icehockey_ahl", "icehockey_nhl_championship_winner", "icehockey_sweden_hockey_league",
                "basketball_ncaab"):
        with pytest.raises(ValueError):
            TheOddsAPIAdapter("k", sport_key=key)


@pytest.mark.asyncio
async def test_nhl_request_targets_the_nhl_path_with_h2h_only_and_never_the_network():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["params"] = request.url.path, dict(request.url.params)
        return httpx.Response(200, json=[raw_event("FIXTURENHL00001", *TOR_MTL)],
                              headers={"x-requests-remaining": "480", "x-requests-used": "20",
                                       "x-requests-last": "1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await TheOddsAPIAdapter("fixture-key", list(nhl_odds.MARKETS), "us", sport_key=nhl_odds.SPORT_KEY,
                                           client=client).get_market_snapshot()
    assert seen["path"] == "/v4/sports/icehockey_nhl/odds"
    assert seen["params"]["markets"] == "h2h" and seen["params"]["regions"] == "us"
    assert response.envelope.metadata["sport_key"] == "icehockey_nhl"
    assert response.envelope.metadata["requests_last"] == 1


def test_ingest_odds_still_refuses_icehockey_nhl_and_the_policy_source_is_unchanged():
    # The generic name-keyed binder stays closed to NHL; NHL binds only through its own exact path.
    assert canonical_league_for("THE_ODDS_API", "icehockey_nhl") is None
    assert NHL.permitted_quote_sources == ("THE_ODDS_API",)
    assert (nhl_odds.SPORT_KEY, nhl_odds.ENDPOINT_KEY, nhl_odds.MARKETS) == ("icehockey_nhl", "icehockey_nhl_odds",
                                                                             ("h2h",))
    assert (nhl_odds.BUY_OPENS, nhl_odds.BUY_CLOSES) == (timedelta(minutes=75), timedelta(minutes=35))
    from app.services.event_time_reconciliation import MATCH_WINDOW
    assert nhl_odds.START_TOLERANCE == MATCH_WINDOW and nhl_odds.AMBIGUITY_WINDOW == timedelta(hours=12)


def test_the_shipped_settings_are_off_and_unfunded(settings):
    assert settings.nhl_odds_api_enabled is False
    assert (settings.odds_nhl_lane_monthly_credits, settings.odds_nhl_lane_daily_credits) == (0, 0)
    for name in ("nhl_odds_api_enabled", "odds_nhl_lane_monthly_credits", "odds_nhl_lane_daily_credits"):
        assert Settings.model_fields[name].default in (False, 0)
    with pytest.raises(ValidationError):
        Settings(odds_nhl_lane_monthly_credits=501)
    with pytest.raises(ValidationError):
        Settings(odds_nhl_lane_daily_credits=49)
    with pytest.raises(ValidationError):
        Settings(odds_nhl_lane_daily_credits=-1)


# -- credit lanes: NHL has its own lane, unfunded unless configured ---------------
COMPILED = {  # every pre-existing lane's numbers and protection, exactly as before
    lanes.LANE_MLB_REQUIRED: (150, 8, True, 1), lanes.LANE_MLB_RECOVERY: (100, 6, True, 2),
    lanes.LANE_NFL_RESEARCH: (60, 6, False, 3), lanes.LANE_OPTIONAL_MARKETS: (90, 6, False, 4),
    lanes.LANE_OTHER_RESEARCH: (0, 0, False, 5), lanes.LANE_CFB_RESEARCH: (0, 0, False, 6),
    # MAIN's NBA Odds API lane (10-20): compiled unfunded, lowest precedence, yields like NHL.
    lanes.LANE_NBA_RESEARCH: (0, 0, False, 9),
}


def test_nhl_requests_are_attributed_to_their_own_lane_and_never_share_one():
    assert lanes.league_of_endpoint(nhl_odds.ENDPOINT_KEY) == "NHL"
    assert lanes.league_of_endpoint("americanfootball_ncaaf_odds") == "CFB"
    assert lanes.league_of_endpoint("americanfootball_nfl_odds") == "NFL"
    assert lanes.league_of_endpoint("baseball_mlb_odds") == "MLB"
    assert lanes.league_of_endpoint("icehockey_ahl_odds") == ""
    assert lanes.league_of_endpoint("quota_recovery_probe") == ""
    assert lanes.lane_for_request(league_code="NHL", markets=["h2h"]) == lanes.LANE_NHL_RESEARCH
    # Spreads and totals too: nothing NHL draws on the shared, funded optional lane.
    assert lanes.lane_for_request(league_code="NHL", markets=["spreads", "totals"]) == lanes.LANE_NHL_RESEARCH
    assert lanes.lane_for_request(league_code="CFB", markets=["h2h"]) == lanes.LANE_CFB_RESEARCH
    assert lanes.lane_for_request(league_code="NFL", markets=["h2h"]) == lanes.LANE_NFL_RESEARCH
    assert lanes.lane_for_request(league_code="MLB", markets=["h2h"]) == lanes.LANE_MLB_REQUIRED
    row = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=nhl_odds.ENDPOINT_KEY,
                                status="RESERVED", request_fingerprint="f" * 64,
                                response_metadata={"request_shape": {"markets": ["h2h"], "regions": "us"}})
    assert lanes.lane_of_ledger_row(row) == lanes.LANE_NHL_RESEARCH
    default_nhl = lanes.LANES[lanes.LANE_NHL_RESEARCH]
    assert (default_nhl.monthly_allocation, default_nhl.daily_cap, default_nhl.protected,
            default_nhl.precedence) == (0, 0, False, 8)
    # Every pre-existing lane is exactly as compiled before NHL existed.
    assert {l.key: (l.monthly_allocation, l.daily_cap, l.protected, l.precedence)
            for l in lanes.DEFAULT_LANES if l.key != lanes.LANE_NHL_RESEARCH} == COMPILED
    funded = {l.key: l for l in lanes.lanes_with_nhl(40, 8, lanes.lanes_with_cfb(30, 6))}
    assert (funded[lanes.LANE_NHL_RESEARCH].monthly_allocation, funded[lanes.LANE_NHL_RESEARCH].daily_cap) == (40, 8)
    assert (funded[lanes.LANE_CFB_RESEARCH].monthly_allocation, funded[lanes.LANE_CFB_RESEARCH].daily_cap) == (30, 6)
    assert all(funded[l.key] == l for l in lanes.DEFAULT_LANES
               if l.key not in (lanes.LANE_NHL_RESEARCH, lanes.LANE_CFB_RESEARCH))
    # Funding NHL leaves CFB (and everything else) exactly as compiled, and vice versa.
    assert {l.key: l for l in lanes.lanes_with_nhl(40, 8)}[lanes.LANE_CFB_RESEARCH] == lanes.LANES[
        lanes.LANE_CFB_RESEARCH]
    assert {l.key: l for l in lanes.lanes_with_cfb(40, 8)}[lanes.LANE_NHL_RESEARCH] == default_nhl


def test_the_unfunded_nhl_lane_refuses_and_a_funded_one_leaves_protected_lanes_whole(session):
    _measured_balance(session)
    unfunded = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=10)
    ok, why = unfunded.may_spend(lanes.LANE_NHL_RESEARCH, 1)
    assert not ok and "0 of its 0" in why
    cfb_funded_only = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=10,
                                        lanes=lanes.lanes_with_cfb(40, 8))
    assert cfb_funded_only.may_spend(lanes.LANE_NHL_RESEARCH, 1)[0] is False   # CFB money is not NHL money
    funded = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=10,
                               lanes=lanes.lanes_with_nhl(40, 8))
    assert funded.may_spend(lanes.LANE_NHL_RESEARCH, 1) == (True, None)
    assert funded.protected_unspent == unfunded.protected_unspent == 250
    latched = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=500,
                                lanes=lanes.lanes_with_nhl(40, 8))
    assert latched.may_spend(lanes.LANE_NHL_RESEARCH, 1)[0] is False           # the provider floor still rules
    # The protected holdback still binds an NHL request: with 260 budgeted, 250 stay held for MLB.
    tight = lanes.lane_budget(session, monthly_cap=500, reserve=240, daily_cap=18, floor=10,
                              lanes=lanes.lanes_with_nhl(500, 48))
    ok, why = tight.may_spend(lanes.LANE_NHL_RESEARCH, 11)
    assert not ok and "reserved for protected lanes" in why
    # ...and NHL also yields to NFL: with 320 budgeted, 250 held for MLB and NFL's unspent 60, 10 are NHL's.
    room = lanes.lane_budget(session, monthly_cap=500, reserve=180, daily_cap=18, floor=10,
                             lanes=lanes.lanes_with_nhl(500, 48), in_play_today=frozenset())
    assert room.may_spend(lanes.LANE_NHL_RESEARCH, 10) == (True, None)
    ok, why = room.may_spend(lanes.LANE_NHL_RESEARCH, 11)
    assert not ok and "yields to NFL research moneylines" in why and "only 10 are free" in why


def test_the_lane_guard_never_lets_an_nhl_request_bypass_its_lane(session, settings):
    from app.data_sources import SourceError
    from app.jobs.orchestrator import JobOrchestrator

    _measured_balance(session)
    orchestrator = JobOrchestrator(session, settings)
    # Unfunded: refused with or without a declared market list.
    for shape in ({"markets": ["h2h"], "regions": "us"}, {"regions": "us"}, {}):
        with pytest.raises(SourceError, match="NHL_RESEARCH lane"):
            orchestrator._enforce_lane_budget(nhl_odds.ENDPOINT_KEY, shape)
    funded = JobOrchestrator(session, settings.model_copy(update={"odds_nhl_lane_monthly_credits": 30,
                                                                  "odds_nhl_lane_daily_credits": 6}))
    assert funded._enforce_lane_budget(nhl_odds.ENDPOINT_KEY, {"markets": ["h2h"], "regions": "us"}) is None
    # Unchanged for keys no lane knows: the monthly guard alone still applies to them.
    assert orchestrator._enforce_lane_budget("some_unknown_endpoint", {"markets": ["h2h"]}) is None
    assert orchestrator._enforce_lane_budget("americanfootball_nfl_odds", {}) is None


# -- identity: exact names, exact orientation, exact start ---------------------------
def test_exact_binding_by_both_teams_and_one_bad_event_fails_alone(session):
    tor, _ = _forecast(session)
    col, _ = _forecast(session, source_id="2026020241", home="COL", away="DAL")
    bound, receipts = nhl_odds.bind_events(session, events(
        raw_event("FIXTURENHL00001", "Toronto Maple Leafs", "Montréal Canadiens"),     # accent normalised
        raw_event("FIXTURENHL00002", *COL_DAL),
        raw_event("FIXTURENHL00003", "Utah Hockey Club", "Boston Bruins"),            # a name AISP does not hold
    ), received_at=FETCHED)
    assert [game_id for _e, game_id in bound] == [tor.game_id, col.game_id]
    assert {r["source_event_id"]: r["reason"] for r in receipts} == {
        "FIXTURENHL00001": nhl_odds.BOUND, "FIXTURENHL00002": nhl_odds.BOUND,
        "FIXTURENHL00003": "TEAM_NAME_UNMATCHED"}


# AISP_EVENT_CLOCK_WINDOW_V1 (hotfix 2026-10-24 verification): the provider start binds when it is
# within MATCH_WINDOW (2 h) of the admitted start on the same Eastern day, in either direction;
# identity stays exact and the EARLIER clock still closes betting. The 15-minute one-sided
# puck-drop grace these cases used to pin was superseded by MAIN's 10-20..10-23 releases.
@pytest.mark.parametrize("offset, reason", [
    (timedelta(0), nhl_odds.BOUND),
    (timedelta(minutes=1), nhl_odds.BOUND),                     # puck drop follows the scheduled start
    (timedelta(minutes=10), nhl_odds.BOUND),                    # the observed 2026-10-16 provider offset (:10 past)
    (timedelta(minutes=15), nhl_odds.BOUND),
    (timedelta(minutes=16), nhl_odds.BOUND),
    (-timedelta(minutes=1), nhl_odds.BOUND),
    (-timedelta(minutes=45), nhl_odds.BOUND),
    (timedelta(hours=2), nhl_odds.BOUND),                       # the window edge, inclusive
    (timedelta(hours=2, minutes=1), "START_OUTSIDE_MATCH_WINDOW"),
    (timedelta(hours=26), "NO_GAME_FOR_EXACT_PARTICIPANTS"),    # another Eastern day is another slate
    (timedelta(days=3), "NO_GAME_FOR_EXACT_PARTICIPANTS"),
])
def test_the_provider_start_is_the_admitted_start_up_to_the_puck_drop_grace(session, offset, reason):
    from app.services.event_time_reconciliation import MATCH_WINDOW

    assert nhl_odds.START_TOLERANCE == MATCH_WINDOW
    _forecast(session)
    bound, (receipt,) = nhl_odds.bind_events(session, events(raw_event("FIXTURENHL00001", *TOR_MTL,
                                                                       commence=START + offset)),
                                             received_at=FETCHED - timedelta(hours=2))
    assert receipt["reason"] == reason and (len(bound) == 1) == (reason == nhl_odds.BOUND)
    import ast
    clock = receipt["clock_reconciliation"]
    clock = ast.literal_eval(clock) if isinstance(clock, str) else clock     # NHL receipts stringify details
    if reason == nhl_odds.BOUND:
        assert clock["delta_seconds"] == offset.total_seconds()
        assert clock["earliest_start"] == min(START, START + offset).isoformat()
    elif reason == "START_OUTSIDE_MATCH_WINDOW":
        assert clock["canonical_start"] == START.isoformat()
        assert clock["provider_start"] == (START + offset).isoformat()


def test_reversed_orientation_is_named_and_never_flipped(session):
    _forecast(session)
    for event_id, commence in (("FIXTURENHL00001", START), ("FIXTURENHL00002", START + timedelta(hours=1))):
        bound, (receipt,) = nhl_odds.bind_events(session, events(raw_event(event_id, *TOR_MTL[::-1],
                                                                           commence=commence)), received_at=FETCHED)
        assert bound == [] and receipt["reason"] == nhl_odds.ORIENTATION_REVERSED


def test_two_admitted_games_near_one_start_are_ambiguous_even_with_an_exact_match(session):
    _forecast(session)
    _bare_game(session, source_id="2026020299", home="TOR", away="MTL", start=START + timedelta(hours=3))
    bound, (receipt,) = nhl_odds.bind_events(session, events(raw_event("FIXTURENHL00001", *TOR_MTL)),
                                             received_at=FETCHED)
    assert bound == [] and receipt["reason"] == "MORE_THAN_ONE_GAME_MATCHES" and len(receipt["candidates"]) == 2


def test_two_events_claiming_one_game_are_both_refused_and_the_rest_still_binds(session):
    _forecast(session)
    col, _ = _forecast(session, source_id="2026020241", home="COL", away="DAL")
    bound, receipts = nhl_odds.bind_events(session, events(
        raw_event("FIXTURENHL00001", *TOR_MTL), raw_event("FIXTURENHL00009", *TOR_MTL),
        raw_event("FIXTURENHL00002", *COL_DAL)), received_at=FETCHED)
    assert [g for _e, g in bound] == [col.game_id]
    reasons = {r["source_event_id"]: r["reason"] for r in receipts}
    assert reasons["FIXTURENHL00001"] == reasons["FIXTURENHL00009"] == "MORE_THAN_ONE_EVENT_BINDS_THIS_GAME"


def test_started_events_finished_games_and_conflicting_mappings_are_refused(session):
    tor, _ = _forecast(session)
    col, _ = _forecast(session, source_id="2026020241", home="COL", away="DAL")
    (_b, (started,)) = nhl_odds.bind_events(session, events(raw_event("FIXTURENHL00001", *TOR_MTL)),
                                            received_at=START)
    assert started["reason"] == "EVENT_ALREADY_STARTED"
    session.get(Game, col.game_id).status = GameStatus.POSTPONED
    session.add(ExternalIdMapping(entity_type="game", entity_id=tor.game_id, source="THE_ODDS_API",
                                  external_id="FIXTURENHL00077"))
    session.flush()
    bound, receipts = nhl_odds.bind_events(session, events(raw_event("FIXTURENHL00001", *TOR_MTL),
                                                           raw_event("FIXTURENHL00002", *COL_DAL)),
                                           received_at=FETCHED)
    assert bound == []
    assert {r["source_event_id"]: r["reason"] for r in receipts} == {
        "FIXTURENHL00001": "GAME_MAPPED_TO_ANOTHER_EVENT", "FIXTURENHL00002": "GAME_NOT_SCHEDULED"}


# -- persistence: the checkpoint's shape, separate clocks, retained start ----------------
def test_a_pair_is_written_in_the_checkpoint_shape_with_separate_clocks_and_replays_idempotently(session):
    session.autoflush = False
    tor, _ = _forecast(session)
    bound, _r = nhl_odds.bind_events(session, events(raw_event("FIXTURENHL00001", *TOR_MTL)), received_at=FETCHED)
    requested = FETCHED - timedelta(seconds=3)
    written = nhl_odds.persist(session, bound, requested_at=requested, received_at=FETCHED,
                               response={"ledger_reservation_id": "fixture-ledger"})
    assert (written["created"], written["existing"], written["quote_receipts"]) == (2, 0, [])
    (market,) = session.scalars(select(Market).where(Market.game_id == tor.game_id)).all()
    assert market.period == paper.MARKET_PERIOD
    keys = set(session.scalars(select(MarketOutcome.outcome_key).where(MarketOutcome.market_id == market.id)))
    assert keys == {"HOME:None", "AWAY:None"}
    for row in session.scalars(select(OddsSnapshot)):
        raw = row.raw_payload
        assert row.observed_at.replace(tzinfo=UTC) == BOOK_CLOCK                     # quote clock
        assert row.available_as_of.replace(tzinfo=UTC) == FETCHED                    # receipt clock
        assert raw["requested_at"] == requested.isoformat()                          # fetch clock
        assert (raw["sport_key"], raw["market"], raw["quote_clock_source"]) == ("icehockey_nhl", "h2h",
                                                                                "MARKET_LAST_UPDATE")
        assert raw["event_commence_time"] == START.isoformat()
        assert raw["ledger_reservation_id"] == "fixture-ledger" and row.source_event_id == "FIXTURENHL00001"
    assert session.scalar(select(ExternalIdMapping.entity_id).where(
        ExternalIdMapping.source == "THE_ODDS_API", ExternalIdMapping.external_id == "FIXTURENHL00001")) == tor.game_id
    again = nhl_odds.persist(session, bound, requested_at=requested, received_at=FETCHED)
    assert (again["created"], again["existing"]) == (0, 2) and _count(session, OddsSnapshot) == 2


def test_a_three_way_market_is_never_stored(session):
    _forecast(session)
    bound, _r = nhl_odds.bind_events(session, events(raw_event(
        "FIXTURENHL00001", *TOR_MTL, market_key="h2h_3_way",
        outcomes=[{"name": TOR_MTL[0], "price": 150}, {"name": "Draw", "price": 330},
                  {"name": TOR_MTL[1], "price": 180}])), received_at=FETCHED)
    written = nhl_odds.persist(session, bound, requested_at=FETCHED, received_at=FETCHED)
    assert written["created"] == 0 and written["quote_receipts"][0]["reason"] == nhl_odds.NOT_THE_TWO_WAY_MARKET
    assert _count(session, OddsSnapshot) == 0


def test_after_a_start_change_the_same_price_and_clock_is_a_new_row_at_the_new_start(session):
    session.autoflush = False
    tor, _ = _forecast(session)
    bound, _r = nhl_odds.bind_events(session, events(raw_event("FIXTURENHL00001", *TOR_MTL)), received_at=FETCHED)
    assert nhl_odds.persist(session, bound, requested_at=FETCHED, received_at=FETCHED)["created"] == 2
    moved = START + timedelta(minutes=30)
    session.get(Game, tor.game_id).starts_at = moved
    session.flush()
    bound, (receipt,) = nhl_odds.bind_events(session, events(raw_event("FIXTURENHL00001", *TOR_MTL, commence=moved)),
                                             received_at=FETCHED)
    assert receipt["reason"] == nhl_odds.BOUND
    written = nhl_odds.persist(session, bound, requested_at=FETCHED, received_at=FETCHED)
    assert (written["created"], written["existing"]) == (2, 0)
    starts = sorted(r.raw_payload["event_commence_time"] for r in session.scalars(select(OddsSnapshot)))
    assert starts == sorted([START.isoformat()] * 2 + [moved.isoformat()] * 2)


# -- the plan: one request per start-time cluster, decision-capable forecasts only --------
def test_plan_is_due_once_per_start_cluster(session):
    _in_process(session)
    _in_process(session, source_id="2026020241", home="COL", away="DAL")
    assert nhl_odds.plan(session, now=START - timedelta(minutes=90))["due"] is False        # too early
    planned = nhl_odds.plan(session, now=FETCHED)
    assert planned["due"] and sorted(g["source_game_id"] for g in planned["due_games"]) == ["2026020240",
                                                                                           "2026020241"]
    assert {g["route"] for g in planned["games"]} == {"AISP_IN_PROCESS"}
    _ledger(session, at=START - timedelta(minutes=80))                                       # before the window
    _ledger(session, at=START - timedelta(minutes=70), endpoint="americanfootball_ncaaf_odds")  # another sport
    assert nhl_odds.plan(session, now=FETCHED)["due"] is True
    _ledger(session, at=START - timedelta(minutes=70))
    # The window's request recorded no outcome (interrupted) and priced neither game: one retry is
    # due on a later tick ...
    retry = nhl_odds.plan(session, now=FETCHED)
    assert retry["due"] is True
    assert nhl_odds.plan(session, now=START - timedelta(minutes=65))["due"] is False         # still in flight
    _ledger(session, at=START - timedelta(minutes=55))
    # ... and never a third request in the same window.
    bought = nhl_odds.plan(session, now=FETCHED)
    assert bought["due"] is False and {g["state"] for g in bought["games"]} == {nhl_odds.ALREADY_REQUESTED_IN_WINDOW}
    assert nhl_odds.plan(session, now=START - timedelta(minutes=30))["due"] is False         # too late


def _terminal(session, reserved, status):
    session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=reserved.endpoint_key,
                                      requested_at=reserved.requested_at,
                                      completed_at=reserved.requested_at + timedelta(seconds=2),
                                      status=status, request_fingerprint=reserved.request_fingerprint,
                                      reservation_id=reserved.id))
    session.flush()


def test_a_failed_request_is_retried_once_inside_the_window_and_then_never_again(session):
    """2026-10-17: a FAILED request used to cost the cluster its price outright."""
    _in_process(session)
    first = _ledger(session, at=START - timedelta(minutes=72))
    in_flight = nhl_odds.plan(session, now=START - timedelta(minutes=70))                   # 2 min old, no outcome
    assert in_flight["due"] is False
    assert in_flight["games"][0]["window_requests"]["blocking"] == "IN_FLIGHT"
    _terminal(session, first, "FAILED")
    retry = nhl_odds.plan(session, now=START - timedelta(minutes=62))
    assert retry["due"] is True and retry["games"][0]["state"] == nhl_odds.DUE
    assert retry["games"][0]["window_requests"]["failed"] == 1
    second = _ledger(session, at=START - timedelta(minutes=62))
    _terminal(session, second, "FAILED")
    spent = nhl_odds.plan(session, now=START - timedelta(minutes=52))
    assert spent["due"] is False
    assert spent["games"][0]["window_requests"]["blocking"] == "RETRY_LIMIT_REACHED"


def test_a_retry_that_succeeds_closes_the_window_and_an_interrupted_request_is_retried(session):
    _in_process(session)
    _terminal(session, _ledger(session, at=START - timedelta(minutes=74)), "FAILED")
    _terminal(session, _ledger(session, at=START - timedelta(minutes=64)), "SUCCEEDED")
    assert nhl_odds.plan(session, now=START - timedelta(minutes=54))["games"][0]["window_requests"]["blocking"]         == "SUCCEEDED"
    # A reservation left with no outcome for longer than the in-flight grace (process died
    # mid-request) is retried, once, for a later cluster's window too.
    _in_process(session, source_id="2026020241", home="COL", away="DAL", start=START + timedelta(hours=2))
    later_open = START + timedelta(hours=2) - nhl_odds.BUY_OPENS
    _ledger(session, at=later_open + timedelta(minutes=1))
    assert nhl_odds.plan(session, now=later_open + timedelta(minutes=10))["due"] is False      # still in flight
    late = nhl_odds.plan(session, now=later_open + timedelta(minutes=20))
    assert late["due"] is True and late["games"][-1]["window_requests"]["interrupted"] == 1


def test_a_succeeded_request_that_priced_nothing_for_a_game_is_retried_once_for_that_game(session):
    """fcc5e46 under the 2026-10-17 window rule: a SUCCEEDED request counts as bought, except for a
    due game it bound no quote for -- that game is owed one retry, 9+ min later, and never a third."""
    _in_process(session)                                                         # TOR-MTL 2026020240
    _in_process(session, source_id="2026020241", home="COL", away="DAL")
    first_at = START - timedelta(minutes=73)
    _terminal(session, _ledger(session, at=first_at), "SUCCEEDED")
    bound, _r = nhl_odds.bind_events(session, events(raw_event("FIXTURENHL00001", *TOR_MTL,
                                                               clock=START - timedelta(minutes=74))),
                                     received_at=first_at + timedelta(seconds=3))
    nhl_odds.persist(session, bound, requested_at=first_at, received_at=first_at + timedelta(seconds=3))
    assert nhl_odds.plan(session, now=first_at + timedelta(minutes=5))["due"] is False      # < 9 min
    retry = nhl_odds.plan(session, now=first_at + timedelta(minutes=10))
    assert [g["source_game_id"] for g in retry["due_games"]] == ["2026020241"]              # only the unpriced game
    states = {g["source_game_id"]: g["state"] for g in retry["games"]}
    assert states == {"2026020240": nhl_odds.ALREADY_REQUESTED_IN_WINDOW, "2026020241": nhl_odds.DUE}
    assert retry["games"][1]["window_requests"]["blocking"] == "SUCCEEDED"
    # The retry succeeds but still prices nothing for COL-DAL: the window is closed for good.
    _terminal(session, _ledger(session, at=first_at + timedelta(minutes=10)), "SUCCEEDED")
    closed = nhl_odds.plan(session, now=START - timedelta(minutes=40))
    assert closed["due"] is False
    assert {g["state"] for g in closed["games"]} == {nhl_odds.ALREADY_REQUESTED_IN_WINDOW}
    assert {g["window_requests"]["attempts"] for g in closed["games"]} == {2}


def test_a_later_cluster_is_due_again_after_the_earlier_one_was_bought(session):
    _in_process(session)
    _in_process(session, source_id="2026020241", home="COL", away="DAL", start=START + timedelta(hours=1))
    _ledger(session, at=FETCHED)                                                             # bought the 23:00 cluster
    later = nhl_odds.plan(session, now=FETCHED + timedelta(hours=1))
    assert [g["source_game_id"] for g in later["due_games"]] == ["2026020241"]
    reserved = _ledger(session, at=FETCHED + timedelta(minutes=55))                          # the 00:00 cluster bought
    session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=nhl_odds.ENDPOINT_KEY,
                                      requested_at=reserved.requested_at, completed_at=reserved.requested_at,
                                      status="SUCCEEDED", request_fingerprint=reserved.request_fingerprint,
                                      reservation_id=reserved.id))
    session.flush()
    assert nhl_odds.plan(session, now=FETCHED + timedelta(hours=1))["due"] is False


def test_plan_never_buys_for_a_game_whose_forecast_cannot_reach_a_decision(session):
    _bare_game(session)                                                                       # no forecast
    producer, _ = _forecast(session)                                                         # producer route, no eligibility
    wrong = _in_process(session, source_id="2026020241", home="COL", away="DAL")
    wrong.configuration_sha256 = "0" * 64
    late = _in_process(session, source_id="2026020242", home="EDM", away="CGY",
                       published=FETCHED + timedelta(minutes=1), imported=FETCHED + timedelta(minutes=2))
    fixture = _in_process(session, source_id="2026020243", home="VGK", away="MIN")
    fixture.record_class = "SYNTHETIC_FIXTURE"
    session.flush()
    planned = nhl_odds.plan(session, now=FETCHED)
    assert planned["due"] is False
    states = {g["source_game_id"]: g["state"] for g in planned["games"]}
    assert states == {"2026020250": nhl_odds.NO_FORECAST, "2026020240": nhl_odds.NOT_DECISION_ELIGIBLE,
                      "2026020241": nhl_odds.NOT_DECISION_ELIGIBLE, "2026020242": nhl_odds.NO_FORECAST,
                      "2026020243": nhl_odds.NO_FORECAST}
    assert late.forecast_id                                                                   # held only later
    # The producer route opens once its eligibility record is held.
    _eligibility(session, producer, published=FETCHED - timedelta(minutes=30))
    reopened = nhl_odds.plan(session, now=FETCHED)
    assert [g["source_game_id"] for g in reopened["due_games"]] == ["2026020240"]
    assert next(g for g in reopened["games"] if g["source_game_id"] == "2026020240")["route"] == "PRODUCER_ELIGIBILITY"


def test_plan_never_buys_for_an_in_process_forecast_with_incomplete_rest(session):
    """A day-ahead row the generator could not give complete rest (or a row that never says) is refused by
    the paper lifecycle's in-process eligibility, so no credit is spent on its game (review MAJOR 2026-10-16)."""

    stale = _in_process(session)
    stale.producer_evidence_ref = {**stale.producer_evidence_ref, "restComplete": False}
    silent = _in_process(session, source_id="2026020241", home="COL", away="DAL")
    silent.producer_evidence_ref = {k: v for k, v in silent.producer_evidence_ref.items() if k != "restComplete"}
    session.flush()
    planned = nhl_odds.plan(session, now=FETCHED)
    assert planned["due"] is False
    assert {g["source_game_id"]: g["state"] for g in planned["games"]} == {
        "2026020240": nhl_odds.NOT_DECISION_ELIGIBLE, "2026020241": nhl_odds.NOT_DECISION_ELIGIBLE}
    capability = nhl_odds.decision_capability(session, stale, now=FETCHED, starts_at=START)
    assert capability["capable"] is False and "REST_FEATURES_NOT_COMPLETE_AT_GENERATION" in capability["detail"]


def test_plan_skips_a_moved_game_and_games_that_are_not_scheduled(session):
    moved = _in_process(session)
    done = _in_process(session, source_id="2026020241", home="COL", away="DAL")
    session.get(Game, moved.game_id).starts_at = START + timedelta(minutes=5)                # forecast is for the old start
    session.get(Game, done.game_id).status = GameStatus.POSTPONED
    session.flush()
    planned = nhl_odds.plan(session, now=FETCHED)
    assert planned["due"] is False
    assert [(g["source_game_id"], g["state"]) for g in planned["games"]] == [("2026020240", nhl_odds.NO_FORECAST)]


# -- the collector job: switch, guards, ledger, lane ------------------------------------
def _fake_snapshot(calls, raw, *, fail=False):
    async def fake(self):
        calls.append((self.sport_key, tuple(self.markets), self.regions))
        if fail:
            raise httpx.ConnectError("fixture: no network in tests")
        data = [TheOddsAPIAdapter._normalize_event(r).model_dump(mode="json") for r in raw]
        return AdapterResponse(envelope=SourceEnvelope(
            source="THE_ODDS_API", fetched_at=FETCHED, data=data,
            metadata={"event_count": len(raw), "sport_key": self.sport_key, "requests_remaining": 479,
                      "requests_used": 21, "requests_last": 1}), raw_payload=raw)
    return fake


ON = {"nhl_odds_api_enabled": True, "the_odds_api_key": "fixture-not-a-key", "odds_daily_request_budget": 3,  # secret-guard: test-fixture
      "odds_nhl_lane_monthly_credits": 30, "odds_nhl_lane_daily_credits": 6}


def _align_ledger(session):
    """Reservations carry the real wall clock; align them with the fixture clock."""

    session.execute(ProviderRequestLedger.__table__.update().where(
        ProviderRequestLedger.endpoint_key == nhl_odds.ENDPOINT_KEY).values(requested_at=FETCHED))
    session.flush()


@pytest.mark.asyncio
async def test_collector_is_off_by_default_and_on_spends_one_ledgered_nhl_credit(session, settings, monkeypatch):
    from app.data_sources import SourceError
    from app.jobs.orchestrator import JobOrchestrator

    _in_process(session)
    _in_process(session, source_id="2026020241", home="COL", away="DAL")
    session.commit()                       # the refused attempts below roll back to here
    calls: list = []
    raw = [raw_event("FIXTURENHL00001", *TOR_MTL), raw_event("FIXTURENHL00002", *COL_DAL),
           raw_event("FIXTURENHL00003", "Utah Hockey Club", "Boston Bruins"),
           raw_event("FIXTURENHL00004", *TOR_MTL, commence=START + timedelta(hours=3))]   # beyond the 2 h window
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", _fake_snapshot(calls, raw))
    real_plan = nhl_odds.plan
    monkeypatch.setattr(nhl_odds, "plan", lambda s, now=None: real_plan(s, now=FETCHED))

    # Shipped settings: nothing is planned, nothing is requested.
    off = await JobOrchestrator(session, settings).collect_nhl_odds_api(START.date())
    assert off == {"records": 0, "status": "DISABLED", "provider_called": False}

    # Switched on but the NHL lane unfunded: refused by the lane guard before any request.
    _measured_balance(session)
    session.commit()
    unfunded = settings.model_copy(update={**ON, "odds_nhl_lane_monthly_credits": 0, "odds_nhl_lane_daily_credits": 0})
    with pytest.raises(SourceError, match="NHL paper moneylines"):
        await JobOrchestrator(session, unfunded).collect_nhl_odds_api(START.date())
    session.rollback()
    # Funding CFB does not fund NHL.
    cfb_funded = unfunded.model_copy(update={"odds_cfb_lane_monthly_credits": 30, "odds_cfb_lane_daily_credits": 6})
    with pytest.raises(SourceError, match="NHL paper moneylines"):
        await JobOrchestrator(session, cfb_funded).collect_nhl_odds_api(START.date())
    session.rollback()
    assert calls == [] and _count(session, ProviderRequestLedger) == 2      # only the balance reading

    # On, funded, due games: exactly one ledgered request, charged to NHL_RESEARCH.
    orchestrator = JobOrchestrator(session, settings.model_copy(update=ON))
    result = await orchestrator.collect_nhl_odds_api(START.date())
    assert calls == [("icehockey_nhl", ("h2h",), "us")]
    assert result["provider_called"] and result["events"] == 4 and result["bound"] == 2 and result["created"] == 4
    ledger = session.scalars(select(ProviderRequestLedger).where(
        ProviderRequestLedger.endpoint_key == nhl_odds.ENDPOINT_KEY)).all()
    assert sorted(r.status for r in ledger) == ["RESERVED", "SUCCEEDED"]
    reserved = next(r for r in ledger if r.status == "RESERVED")
    shape = reserved.response_metadata["request_shape"]
    assert (shape["sport_key"], shape["markets"], sorted(shape["due_games"])) == (
        "icehockey_nhl", ["h2h"], ["2026020240", "2026020241"])
    terminal = next(r for r in ledger if r.status == "SUCCEEDED")
    assert terminal.reservation_id == reserved.id and terminal.quota_cost == 1 and terminal.quota_remaining == 479
    assert {r["source_event_id"]: r["reason"] for r in terminal.response_metadata["event_receipts"]} == {
        "FIXTURENHL00001": nhl_odds.BOUND, "FIXTURENHL00002": nhl_odds.BOUND,
        "FIXTURENHL00003": "TEAM_NAME_UNMATCHED", "FIXTURENHL00004": "START_OUTSIDE_MATCH_WINDOW"}
    assert lanes.lane_of_ledger_row(terminal, group=ledger) == lanes.LANE_NHL_RESEARCH
    state = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=10)
    assert state.lanes[lanes.LANE_NHL_RESEARCH].spent == 1
    assert state.lanes[lanes.LANE_CFB_RESEARCH].spent == state.lanes[lanes.LANE_NFL_RESEARCH].spent == 0
    assert state.lanes[lanes.LANE_OTHER_RESEARCH].spent == 0
    assert {r.raw_payload["ledger_reservation_id"] for r in session.scalars(select(OddsSnapshot))} == {str(reserved.id)}
    run = session.scalars(select(IngestionRun).where(IngestionRun.source == "THE_ODDS_API")).one()
    assert run.source_metadata["requests_remaining"] == 479               # the provider floor's next reading
    # The plan now sees this start cluster as bought: the next tick spends nothing.
    _align_ledger(session)
    again = await orchestrator.collect_nhl_odds_api(START.date())
    assert again["status"] == "NOT_DUE" and len(calls) == 1


@pytest.mark.asyncio
async def test_a_failed_request_is_ledgered_failed_and_retried_once_on_a_later_tick(session, settings, monkeypatch):
    """Since 2026-10-17 a FAILED request no longer costs its cluster the price: the next tick (a new
    10-minute refresh window) retries it once; a second failure closes the window. A retry inside the
    SAME refresh window is still refused by the reservation itself, before any provider call."""
    from app.data_sources import SourceError
    from app.jobs.orchestrator import JobOrchestrator

    _in_process(session)
    _measured_balance(session)
    session.commit()
    calls: list = []
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", _fake_snapshot(calls, [], fail=True))
    real_plan = nhl_odds.plan
    monkeypatch.setattr(nhl_odds, "plan", lambda s, now=None: real_plan(s, now=FETCHED))
    orchestrator = JobOrchestrator(session, settings.model_copy(update=ON))
    with pytest.raises(httpx.ConnectError):
        await orchestrator.collect_nhl_odds_api(START.date())
    ledger = session.scalars(select(ProviderRequestLedger).where(
        ProviderRequestLedger.endpoint_key == nhl_odds.ENDPOINT_KEY)).all()
    assert sorted(r.status for r in ledger) == ["FAILED", "RESERVED"]
    failed = next(r for r in ledger if r.status == "FAILED")
    assert failed.error_type == "ConnectError" and failed.response_metadata["response_received"] is False
    assert _count(session, OddsSnapshot) == 0
    _align_ledger(session)
    assert nhl_odds.plan(session, now=FETCHED)["due"] is True                         # a retry is owed
    with pytest.raises(SourceError, match="already reserved for this refresh window"):
        await orchestrator.collect_nhl_odds_api(START.date())                         # same refresh window
    assert len(calls) == 1
    # The next tick falls in a new refresh window: the earlier rows' shape no longer collides.
    session.execute(ProviderRequestLedger.__table__.update().where(
        ProviderRequestLedger.endpoint_key == nhl_odds.ENDPOINT_KEY).values(request_fingerprint="e" * 64))
    session.commit()
    with pytest.raises(httpx.ConnectError):
        await orchestrator.collect_nhl_odds_api(START.date())                         # the one retry
    assert len(calls) == 2
    _align_ledger(session)
    again = await orchestrator.collect_nhl_odds_api(START.date())
    assert again["status"] == "NOT_DUE" and len(calls) == 2                           # two attempts: closed
    window = next(g for g in again["plan"]["games"] if g.get("window_requests"))["window_requests"]
    assert (window["blocking"], window["failed"]) == ("RETRY_LIMIT_REACHED", 2)


@pytest.mark.asyncio
async def test_book_clock_only_response_archives_and_binds_through_the_real_adapter(session, settings, monkeypatch):
    """The documented /odds shape end to end: real get_market_snapshot (mock transport), archive, bind, store."""

    from app.jobs.orchestrator import JobOrchestrator

    _in_process(session)
    session.commit()
    raw = [raw_event("FIXTURENHL00001", *TOR_MTL, market_clock=False)]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/icehockey_nhl/odds")
        assert request.url.params["markets"] == "h2h"
        return httpx.Response(200, json=raw, headers={"x-requests-remaining": "479", "x-requests-used": "21",
                                                      "x-requests-last": "1"})

    real_snapshot = TheOddsAPIAdapter.get_market_snapshot

    async def mocked_snapshot(self):
        self._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            response = await real_snapshot(self)
        finally:
            await self._client.aclose()
        return AdapterResponse(envelope=response.envelope.model_copy(update={"fetched_at": FETCHED}),
                               raw_payload=response.raw_payload)

    real_plan = nhl_odds.plan
    monkeypatch.setattr(nhl_odds, "plan", lambda s, now=None: real_plan(s, now=FETCHED))
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", mocked_snapshot)
    _measured_balance(session)
    result = await JobOrchestrator(session, settings.model_copy(update=ON)).collect_nhl_odds_api(START.date())
    assert result["provider_called"] and result["bound"] == 1 and result["created"] == 2
    assert result["archive_payload_sha256"] and result["clock_refusals"] == []
    rows = session.scalars(select(OddsSnapshot)).all()
    assert {r.raw_payload["quote_clock_source"] for r in rows} == {"BOOK_LAST_UPDATE"}
    assert {r.raw_payload["market_last_update"] for r in rows} == {None}                 # never relabelled
    assert {r.raw_payload["archive_payload_sha256"] for r in rows} == {result["archive_payload_sha256"]}


def test_the_scheduler_registers_the_collector_only_when_switched_on(settings, monkeypatch):
    from app.jobs import lanes as job_lanes
    from app.jobs import scheduler as scheduler_module
    from app.jobs.orchestrator import AVAILABLE_JOBS

    monkeypatch.setattr(scheduler_module, "daily_pipeline_catch_up_time", lambda s: None)
    armed = settings.model_copy(update={
        "scheduler_enabled": True, "live_shadow_enabled": False, "nfl_shadow_enabled": False,
        "prediction_market_observer_enabled": False, "event_relative_scheduler_enabled": False,
        "odds_polling_enabled": False})

    def jobs(**update):
        return {job.id: job for job in scheduler_module.build_scheduler(armed.model_copy(update=update)).get_jobs()}

    assert "asp-nhl-odds-api" not in jobs()
    assert "asp-nhl-odds-api" not in jobs(nhl_paper_checkpoint_enabled=True, odds_nhl_lane_monthly_credits=30,
                                          odds_nhl_lane_daily_credits=6)
    job = jobs(nhl_odds_api_enabled=True)["asp-nhl-odds-api"]
    assert job.kwargs["job_name"] == "collect-nhl-odds-api"
    assert job.trigger.interval == timedelta(minutes=10)
    assert getattr(job.func, "__aisp_job_lane__") == job_lanes.CORE_LANE
    assert "collect-nhl-odds-api" in AVAILABLE_JOBS
    # Core-jobs-off processes never collect.
    assert "asp-nhl-odds-api" not in jobs(nhl_odds_api_enabled=True, scheduler_core_jobs_enabled=False)


# -- end to end: a collected pair reaches the NHL checkpoint and funds a position ----------
@pytest.mark.asyncio
async def test_a_collected_pair_reaches_a_qualified_nhl_position(session, settings, monkeypatch):
    from app.jobs.orchestrator import JobOrchestrator

    s = _settings(settings)
    forecast, _game = _forecast(session)
    _eligibility(session, forecast)                                 # the producer route (unchanged)
    _activate(session, s)
    _measured_balance(session)
    session.commit()
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot",
                        _fake_snapshot([], [raw_event("FIXTURENHL00001", *TOR_MTL)]))
    real_plan = nhl_odds.plan
    monkeypatch.setattr(nhl_odds, "plan", lambda s_, now=None: real_plan(s_, now=FETCHED))
    collected = await JobOrchestrator(session, s.model_copy(update=ON)).collect_nhl_odds_api(START.date())
    assert collected["created"] == 2
    decision = paper.evaluate(session, s, now=DECIDE_AT, clock=lambda: DECIDE_AT)["decisions"][0]
    assert decision["outcome"] == "QUALIFIED", decision.get("detail")
    assert decision["selection"]["provider_event_id"] == "FIXTURENHL00001"
    assert datetime.fromisoformat(decision["selection"]["event_commence_time"]) == START
    assert decision["position_id"] and _count(session, OfficialAspPosition) == 1


def test_paper_leg_accepts_the_puck_drop_offset_within_the_match_window_and_nothing_else():
    from app.nhl import paper
    from app.services.event_time_reconciliation import MATCH_WINDOW
    assert paper.PROVIDER_START_GRACE == nhl_odds.START_TOLERANCE == MATCH_WINDOW
    for offset, ok in ((timedelta(0), True), (timedelta(minutes=10), True), (timedelta(minutes=15), True),
                       (timedelta(minutes=16), True), (-timedelta(minutes=1), True), (timedelta(hours=2), True),
                       (timedelta(hours=2, minutes=1), False), (-timedelta(hours=2, minutes=1), False)):
        assert paper.provider_start_matches(START + offset, START) is ok
    assert paper.provider_start_matches(None, START) is False and paper.provider_start_matches(START, None) is False
