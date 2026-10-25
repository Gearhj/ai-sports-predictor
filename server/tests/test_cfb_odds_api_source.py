"""The Odds API as a CFB (americanfootball_ncaaf) price source for AISP_CFB_PAPER_POLICY_V1.

Offline only: every provider response here is a hand-built FIXTURE in the real
Odds API v4 /odds shape (the same shape ``TheOddsAPIAdapter._normalize_event``
parses for MLB and NFL). No request is ever sent.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.cfb import odds_api_price as cfb_odds
from app.cfb import paper_decision, production_import
from app.data_sources.base import AdapterResponse, OddsEventDTO, SourceEnvelope
from app.data_sources.odds import TheOddsAPIAdapter
from app.ingestion.odds_league import canonical_league_for
from app.models import ExternalIdMapping, Game, Market, MarketOutcome, OddsSnapshot, ProviderRequestLedger, Sportsbook
from app.services import odds_budget_lanes as lanes
from app.services.research_paper_policy import (
    CFB, NBA, SOURCE_AMENDMENTS, effective_quote_sources, policy_config,
)
from tests.test_cfb_consumer_activation import _in_window, era, world  # noqa: F401 -- fixtures
from tests.test_cfb_production_discovery import KICK

FETCHED = KICK - timedelta(minutes=50)          # inside the T-75..T-35 buy window
BOOK_CLOCK = KICK - timedelta(minutes=55)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def raw_event(event_id: str, home: str, away: str, *, commence=KICK, home_ml=-150, away_ml=+130,
              clock=BOOK_CLOCK, books=("draftkings",), outcomes=None, market_clock=True) -> dict:
    """One Odds API v4 event (FIXTURE ids), h2h only, every book stamped ``clock``.

    ``market_clock=False`` is the DOCUMENTED /odds shape: the bookmaker carries
    ``last_update`` (stamped ``clock``) and the market carries none.
    """

    def book(key):
        market = {"key": "h2h", "outcomes": outcomes if outcomes is not None else [
            {"name": home, "price": home_ml}, {"name": away, "price": away_ml}]}
        if market_clock:
            market["last_update"] = _stamp(clock)
            return {"key": key, "title": key.title(), "last_update": _stamp(clock + timedelta(seconds=30)),
                    "markets": [market]}
        return {"key": key, "title": key.title(), "last_update": _stamp(clock), "markets": [market]}

    return {"id": event_id, "sport_key": "americanfootball_ncaaf", "sport_title": "NCAAF",
            "commence_time": _stamp(commence), "home_team": home, "away_team": away,
            "bookmakers": [book(k) for k in books]}


def events(*raw: dict) -> list[OddsEventDTO]:
    """Through the SAME adapter normaliser the live path uses."""

    return [OddsEventDTO.model_validate(TheOddsAPIAdapter._normalize_event(r).model_dump(mode="json")) for r in raw]


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


# -- adapter, league map and policy: NCAAF added, everything else unchanged ------
def test_adapter_accepts_ncaaf_and_keeps_mlb_nfl_and_refuses_others():
    # basketball_nba joined the served keys with MAIN's NBA Odds API lane (2026-10-20).
    for key in ("baseball_mlb", "americanfootball_nfl", "americanfootball_ncaaf", "icehockey_nhl", "basketball_nba"):
        assert TheOddsAPIAdapter("k", sport_key=key).sport_key == key
    assert TheOddsAPIAdapter("k").sport_key == "baseball_mlb"               # default unchanged
    for key in ("basketball_ncaab", "americanfootball_ncaaf_championship_winner"):
        with pytest.raises(ValueError):
            TheOddsAPIAdapter("k", sport_key=key)


@pytest.mark.asyncio
async def test_ncaaf_request_targets_the_ncaaf_path_with_h2h_only_and_never_the_network():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["params"] = request.url.path, dict(request.url.params)
        return httpx.Response(200, json=[raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls")],
                              headers={"x-requests-remaining": "480", "x-requests-used": "20",
                                       "x-requests-last": "1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await TheOddsAPIAdapter("fixture-key", ["h2h"], "us", sport_key="americanfootball_ncaaf",
                                           client=client).get_market_snapshot()
    assert seen["path"] == "/v4/sports/americanfootball_ncaaf/odds"
    assert seen["params"]["markets"] == "h2h" and seen["params"]["regions"] == "us"
    assert response.envelope.metadata["sport_key"] == "americanfootball_ncaaf"
    assert response.envelope.metadata["requests_last"] == 1


def test_ingest_odds_still_refuses_ncaaf_and_policy_change_is_cfb_only():
    # The generic name-keyed binder stays closed to NCAAF; CFB binds through its own exact path.
    assert canonical_league_for("THE_ODDS_API", "americanfootball_ncaaf") is None
    assert canonical_league_for("THE_ODDS_API", "baseball_mlb") == "MLB"
    assert canonical_league_for("THE_ODDS_API", "americanfootball_nfl") == "NFL"
    # The FROZEN CFB record stays SportsGameOdds-only; The Odds API arrives only
    # through the appended, prospective amendment, effective from its own clock.
    assert CFB.permitted_quote_sources == ("SPORTSGAMEODDS",)
    assert policy_config(CFB, code_commit="c", code_identity="i")["quotes"]["permitted_sources"] == [
        "SPORTSGAMEODDS"]
    assert NBA.permitted_quote_sources == ("SPORTSGAMEODDS",)
    # NBA gained its own appended amendment with MAIN's NBA Odds API lane; CFB's is byte-for-byte the same.
    assert [a.sport for a in SOURCE_AMENDMENTS] == ["CFB", "NBA"]
    (amendment,) = [a for a in SOURCE_AMENDMENTS if a.sport == "CFB"]
    assert (amendment.sport, amendment.policy_version, amendment.controlling_configuration,
            amendment.added_sources) == ("CFB", CFB.policy_version, CFB.controlling_configuration,
                                         ("THE_ODDS_API",))
    clock = datetime(2026, 9, 26, 16, 0, tzinfo=UTC)
    unset = SimpleNamespace(cfb_odds_api_source_effective_from=None)
    set_ = SimpleNamespace(cfb_odds_api_source_effective_from=clock)
    assert effective_quote_sources(CFB, unset, moment=clock + timedelta(days=1)) == (("SPORTSGAMEODDS",), [])
    assert effective_quote_sources(CFB, set_, moment=clock - timedelta(seconds=1))[0] == ("SPORTSGAMEODDS",)
    sources, applied = effective_quote_sources(CFB, set_, moment=clock)
    assert sources == ("SPORTSGAMEODDS", "THE_ODDS_API") and applied[0]["amendment_id"] == amendment.amendment_id
    assert effective_quote_sources(NBA, set_, moment=clock) == (("SPORTSGAMEODDS",), [])
    # Nothing else in the CFB policy moved.
    assert (CFB.max_quote_age_minutes, CFB.min_adjusted_edge, CFB.kelly_fraction, CFB.max_stake) == (240, 0.02, 0.25, 5.0)


# -- credit lanes: NCAAF is never NFL's lane, and is unfunded unless configured ---
def test_ncaaf_requests_are_attributed_to_their_own_lane_never_nfl():
    assert lanes.league_of_endpoint("americanfootball_ncaaf_odds") == "CFB"
    assert lanes.league_of_endpoint("americanfootball_nfl_odds") == "NFL"
    assert lanes.league_of_endpoint("baseball_mlb_odds") == "MLB"
    assert lanes.league_of_endpoint("quota_recovery_probe") == ""
    assert lanes.lane_for_request(league_code="CFB", markets=["h2h"]) == lanes.LANE_CFB_RESEARCH
    assert lanes.lane_for_request(league_code="NFL", markets=["h2h"]) == lanes.LANE_NFL_RESEARCH
    assert lanes.lane_for_request(league_code="MLB", markets=["h2h"]) == lanes.LANE_MLB_REQUIRED
    row = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key="americanfootball_ncaaf_odds",
                                status="RESERVED", request_fingerprint="f" * 64,
                                response_metadata={"request_shape": {"markets": ["h2h"], "regions": "us"}})
    assert lanes.lane_of_ledger_row(row) == lanes.LANE_CFB_RESEARCH
    row.endpoint_key = "americanfootball_nfl_odds"
    assert lanes.lane_of_ledger_row(row) == lanes.LANE_NFL_RESEARCH
    default_cfb = next(l for l in lanes.DEFAULT_LANES if l.key == lanes.LANE_CFB_RESEARCH)
    assert (default_cfb.monthly_allocation, default_cfb.daily_cap, default_cfb.protected) == (0, 0, False)
    funded = {l.key: l for l in lanes.lanes_with_cfb(40, 8)}
    assert (funded[lanes.LANE_CFB_RESEARCH].monthly_allocation, funded[lanes.LANE_CFB_RESEARCH].daily_cap) == (40, 8)
    # Every other lane keeps its compiled allocation.
    assert all(funded[l.key] == l for l in lanes.DEFAULT_LANES if l.key != lanes.LANE_CFB_RESEARCH)


def _measured_balance(session, remaining=500, used=0):
    """A provider reading inside this billing month (non-protected lanes stay locked without one)."""

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


def test_the_unfunded_cfb_lane_refuses_and_a_funded_one_leaves_protected_lanes_whole(session):
    # No provider reading this month and nothing reserved yet: a guarded request may take the
    # month's first reading (the 1 November rule, odds_budget_lanes FIRST_READING_ATTEMPTS) ...
    first = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=10,
                              lanes=lanes.lanes_with_cfb(40, 8))
    assert first.reconciliation["state"] == lanes.STATE_FIRST_READING
    assert first.may_spend(lanes.LANE_CFB_RESEARCH, 1) == (True, None)
    # ... but once that many chargeable requests came back without one, optional lanes lock, like NFL research.
    for minutes in range(lanes.FIRST_READING_ATTEMPTS):
        session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key="americanfootball_ncaaf_odds",
                                          requested_at=datetime.now(UTC) - timedelta(seconds=minutes + 1),
                                          status="RESERVED", request_fingerprint=f"{minutes:064d}",
                                          response_metadata={"request_shape": {"markets": ["h2h"]}}))
    session.flush()
    unknown = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=10,
                                lanes=lanes.lanes_with_cfb(40, 8))
    ok, why = unknown.may_spend(lanes.LANE_CFB_RESEARCH, 1)
    assert not ok and "optional lanes" in why.lower()        # no provider reading: locked, like NFL research
    _measured_balance(session)
    latched = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=500,
                                lanes=lanes.lanes_with_cfb(40, 8))
    assert latched.may_spend(lanes.LANE_CFB_RESEARCH, 1)[0] is False   # the provider floor still rules
    unfunded = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=10)
    ok, why = unfunded.may_spend(lanes.LANE_CFB_RESEARCH, 1)
    assert not ok and "0 of its 0" in why
    funded = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=10,
                               lanes=lanes.lanes_with_cfb(40, 8))
    assert funded.may_spend(lanes.LANE_CFB_RESEARCH, 1) == (True, None)
    assert funded.protected_unspent == unfunded.protected_unspent == 250


# -- team-name resolution ---------------------------------------------------------
SCHOOLS = {"texas": {"t-tx"}, "texas tech": {"t-tt"}, "georgia": {"t-ga"}, "hawaii": {"t-hi"},
           "miami": {"t-mia"}, "miami oh": {"t-moh"}, "dupe": {"t-d1", "t-d2"}}


@pytest.mark.parametrize("name, team, reason", [
    ("Texas Longhorns", "t-tx", cfb_odds.BOUND),
    ("Texas Tech Red Raiders", "t-tt", cfb_odds.BOUND),                 # longest school wins
    ("Miami Hurricanes", "t-mia", cfb_odds.BOUND),
    ("Miami (OH) RedHawks", "t-moh", cfb_odds.BOUND),
    ("Hawai'i Rainbow Warriors", "t-hi", cfb_odds.BOUND),               # apostrophe normalised
    ("Georgia State Panthers", None, cfb_odds.TEAM_NAME_PREFIX_COLLISION),   # a different school, not a mascot
    ("Texas A&M Aggies", None, cfb_odds.TEAM_NAME_PREFIX_COLLISION),
    ("Texas State Bobcats", None, cfb_odds.TEAM_NAME_PREFIX_COLLISION),
    ("Dupe Ducks", None, cfb_odds.TEAM_NAME_AMBIGUOUS),
    ("Oregon Ducks", None, cfb_odds.TEAM_NAME_UNMATCHED),
    ("Texasville Tigers", None, cfb_odds.TEAM_NAME_UNMATCHED),          # whole tokens only
])
def test_resolve_team(name, team, reason):
    got = cfb_odds.resolve_team(name, SCHOOLS)
    assert got.reason == reason
    assert (next(iter(got.team_ids)) if got.ok else None) == team


# AISP's own (CFBD) school names for the provider spellings refused in production through 2026-10-16.
ALIAS_SCHOOLS = {"app state": {"t-app"}, "massachusetts": {"t-mass"}, "southern miss": {"t-usm"},
                 "sam houston": {"t-shsu"}, "texas": {"t-tx"}, "houston": {"t-hou"}, "north carolina": {"t-unc"}}


@pytest.mark.parametrize("name, team, reason", [
    ("Appalachian State Mountaineers", "t-app", cfb_odds.BOUND),           # 23 refusals
    ("UMass Minutemen", "t-mass", cfb_odds.BOUND),                         # 38: Massachusetts@Buffalo Sat
    ("Southern Mississippi Golden Eagles", "t-usm", cfb_odds.BOUND),       # 38: Arkansas St@Southern Miss Sat
    ("Sam Houston State Bearkats", "t-shsu", cfb_odds.BOUND),              # was a prefix collision
    ("Sam Houston Bearkats", "t-shsu", cfb_odds.BOUND),                    # the direct spelling still binds
    ("Citadel Bulldogs", None, cfb_odds.TEAM_NAME_UNMATCHED),              # no AISP "The Citadel" team: refused
    # Different schools stay refused: an alias never turns a real collision into a bind.
    ("Texas Southern Tigers", None, cfb_odds.TEAM_NAME_PREFIX_COLLISION),
    ("Houston Baptist Huskies", None, cfb_odds.TEAM_NAME_PREFIX_COLLISION),
    ("North Carolina Central Eagles", None, cfb_odds.TEAM_NAME_PREFIX_COLLISION),
    ("McNeese State Cowboys", None, cfb_odds.TEAM_NAME_UNMATCHED),
    ("UMassachusetts Minutemen", None, cfb_odds.TEAM_NAME_UNMATCHED),      # whole tokens only
])
def test_resolve_team_through_the_alias_map(name, team, reason):
    got = cfb_odds.resolve_team(name, ALIAS_SCHOOLS)
    assert got.reason == reason
    assert (next(iter(got.team_ids)) if got.ok else None) == team


def test_an_alias_never_reroutes_a_name_that_binds_on_its_own():
    """If AISP itself held "Appalachian State", the provider name binds to it directly, not via the alias."""

    both = {"appalachian state": {"t-direct"}, "app state": {"t-alias"}, "citadel": {"t-cit"},
            "the citadel": {"t-the-cit"}}
    assert next(iter(cfb_odds.resolve_team("Appalachian State Mountaineers", both).team_ids)) == "t-direct"
    assert next(iter(cfb_odds.resolve_team("Citadel Bulldogs", both).team_ids)) == "t-cit"
    assert next(iter(cfb_odds.resolve_team("Citadel Bulldogs", {"the citadel": {"t-the-cit"}}).team_ids))         == "t-the-cit"


# -- binding against admitted CFB games ------------------------------------------
@pytest.fixture
def slate(world, era, monkeypatch):
    """Games 401 (Home 401 v Away 401) and 402 (Home 402 v Away 402), both at KICK, admitted and forecast."""

    moment, game = _in_window(world, era, monkeypatch)
    # The appended source amendment is in force for this slate (set at activation).
    world.settings.cfb_odds_api_source_effective_from = KICK - timedelta(days=1)
    other = world.session.get(Game, production_import.current_forecast_for_game(world.session, "402").canonical_game_id)
    return moment, game, other


def test_exact_binding_and_one_bad_event_fails_alone(world, slate):
    _moment, game, other = slate
    bound, receipts = cfb_odds.bind_events(world.session, events(
        raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls"),
        raw_event("FIXTUREEVT00002", "Away 402 Owls", "Home 402 Hawks"),        # reversed orientation
        raw_event("FIXTUREEVT00003", "Nowhere Nomads", "Away 401 Owls"),        # unmatched school
    ), received_at=FETCHED)
    assert [(e.source_event_id, g) for e, g in bound] == [("FIXTUREEVT00001", game.id)]
    by_event = {r["source_event_id"]: r["reason"] for r in receipts}
    assert by_event == {"FIXTUREEVT00001": cfb_odds.BOUND, "FIXTUREEVT00002": cfb_odds.ORIENTATION_REVERSED,
                        "FIXTUREEVT00003": cfb_odds.TEAM_NAME_UNMATCHED}
    assert other.id not in {g for _e, g in bound}                              # never substituted


# AISP_EVENT_CLOCK_WINDOW_V1 (hotfix 2026-10-24 verification): the provider kickoff binds when it is
# within MATCH_WINDOW (2 h) of the admitted kickoff on the same Eastern day; identity stays exact and
# the EARLIER of the two clocks still closes betting. Exact equality (the rule these cases used to
# pin) refused 43 CFB events, 34 of them for gaps of 1-10 minutes, and 8 NBA games for the provider's
# :10-past-the-hour listing.
@pytest.mark.parametrize("offset, reason", [
    (timedelta(0), cfb_odds.BOUND),
    (timedelta(minutes=1), cfb_odds.BOUND),                        # listing drift
    (timedelta(minutes=10), cfb_odds.BOUND),                       # the :10-past-the-hour listing
    (-timedelta(minutes=45), cfb_odds.BOUND),
    (timedelta(minutes=45), cfb_odds.BOUND),
    (timedelta(minutes=61), cfb_odds.BOUND),
    (timedelta(hours=2), cfb_odds.BOUND),                          # the window edge, inclusive
    (timedelta(hours=2, minutes=1), "START_OUTSIDE_MATCH_WINDOW"),  # same day, beyond the window
    (timedelta(days=7), "NO_GAME_FOR_EXACT_PARTICIPANTS"),         # a different Eastern day is a different slate
])
def test_kickoff_tolerance(world, slate, offset, reason):
    from app.services.event_time_reconciliation import MATCH_WINDOW

    assert cfb_odds.KICKOFF_TOLERANCE == MATCH_WINDOW
    bound, receipts = cfb_odds.bind_events(world.session, events(
        raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", commence=KICK + offset)),
        received_at=FETCHED - timedelta(hours=2))
    assert receipts[0]["reason"] == reason and (len(bound) == 1) == (reason == cfb_odds.BOUND)
    if reason == cfb_odds.BOUND:
        import ast
        clock = receipts[0]["clock_reconciliation"]
        clock = ast.literal_eval(clock) if isinstance(clock, str) else clock      # receipts stringify details
        assert clock["delta_seconds"] == offset.total_seconds()
        # Matching tolerance never extends betting time: the earlier clock governs.
        assert clock["earliest_start"] == min(KICK, KICK + offset).isoformat()


def test_two_events_claiming_one_game_are_both_refused_and_the_rest_still_binds(world, slate):
    _moment, game, other = slate
    bound, receipts = cfb_odds.bind_events(world.session, events(
        raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls"),
        raw_event("FIXTUREEVT00009", "Home 401 Hawks", "Away 401 Owls"),
        raw_event("FIXTUREEVT00002", "Home 402 Hawks", "Away 402 Owls"),
    ), received_at=FETCHED)
    assert [g for _e, g in bound] == [other.id]
    reasons = sorted(r["reason"] for r in receipts)
    assert reasons.count(cfb_odds.MORE_THAN_ONE_EVENT_BINDS_THIS_GAME) == 2


def test_an_ambiguous_game_fails_closed(world, slate):
    _moment, game, _other = slate
    # A second admitted game with the same teams inside the tolerance.
    from app.models import GameStatus, GameTeam

    twin = Game(season_id=game.season_id, league_id=game.league_id, starts_at=KICK + timedelta(minutes=30), status=GameStatus.SCHEDULED,
                source_mode="RESEARCH_SHADOW")
    world.session.add(twin)
    world.session.flush()
    for side in world.session.scalars(select(GameTeam).where(GameTeam.game_id == game.id)).all():
        world.session.add(GameTeam(game_id=twin.id, team_id=side.team_id, is_home=side.is_home))
    world.session.flush()
    bound, receipts = cfb_odds.bind_events(world.session, events(
        raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls")), received_at=FETCHED)
    assert bound == [] and receipts[0]["reason"] == cfb_odds.MORE_THAN_ONE_GAME_MATCHES


def test_started_events_and_conflicting_mappings_are_refused(world, slate):
    _moment, game, other = slate
    _b, receipts = cfb_odds.bind_events(world.session, events(
        raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls")), received_at=KICK + timedelta(minutes=1))
    assert receipts[0]["reason"] == cfb_odds.EVENT_ALREADY_STARTED        # in-play odds are never pregame
    world.session.add(ExternalIdMapping(entity_type="game", entity_id=other.id, source="THE_ODDS_API",
                                        external_id="FIXTUREEVT00001"))
    world.session.flush()
    _b, receipts = cfb_odds.bind_events(world.session, events(
        raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls"),
        raw_event("FIXTUREEVT00002", "Home 402 Hawks", "Away 402 Owls")), received_at=FETCHED)
    assert {r["source_event_id"]: r["reason"] for r in receipts} == {
        "FIXTUREEVT00001": cfb_odds.EVENT_MAPPED_TO_ANOTHER_GAME,
        "FIXTUREEVT00002": cfb_odds.GAME_MAPPED_TO_ANOTHER_EVENT}


# -- persistence: shape, clocks, validation, duplicates ---------------------------
def _persist(world, raw_events, *, received=FETCHED):
    bound, receipts = cfb_odds.bind_events(world.session, events(*raw_events), received_at=received)
    written = cfb_odds.persist(world.session, bound, requested_at=received - timedelta(seconds=2),
                               received_at=received, response={"archive_payload_sha256": "a" * 64})
    return bound, receipts, written


def test_a_pair_is_written_in_the_checkpoint_shape_with_separate_clocks(world, slate):
    _moment, game, _other = slate
    world.session.autoflush = False                     # production sessions do not autoflush
    _b, _r, written = _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls",
                                                 books=("draftkings", "fanduel"))])
    assert written["created"] == 4 and written["quote_receipts"] == []
    rows = world.session.scalars(select(OddsSnapshot)).all()
    assert {r.source for r in rows} == {"THE_ODDS_API"}
    assert {world.session.get(Sportsbook, r.sportsbook_id).key for r in rows} == {"draftkings", "fanduel"}
    for r in rows:
        assert r.observed_at.replace(tzinfo=UTC) == BOOK_CLOCK                   # book's market clock
        assert r.available_as_of.replace(tzinfo=UTC) == FETCHED                  # AISP's receipt clock
        assert r.raw_payload["book_last_update"] != r.raw_payload["market_last_update"]
        assert r.raw_payload["archive_payload_sha256"] == "a" * 64
    keys = {world.session.get(MarketOutcome, r.market_outcome_id).outcome_key for r in rows}
    assert keys == {"HOME:None", "AWAY:None"}
    from app.services.nfl_paper_decision import paired_quotes

    pairs = paired_quotes(world.session, game.id)
    assert {(p["source"], p["sportsbook"], p["home_american"], p["away_american"]) for p in pairs} == {
        ("THE_ODDS_API", "draftkings", -150, 130), ("THE_ODDS_API", "fanduel", -150, 130)}
    assert world.session.scalar(select(ExternalIdMapping.entity_id).where(
        ExternalIdMapping.source == "THE_ODDS_API", ExternalIdMapping.external_id == "FIXTUREEVT00001")) == game.id


def test_reingesting_the_same_response_creates_nothing_new(world, slate):
    world.session.autoflush = False
    raw = [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls")]
    first = _persist(world, raw)[2]
    counts = (_count(world.session, OddsSnapshot), _count(world.session, MarketOutcome),
              _count(world.session, Market), _count(world.session, ExternalIdMapping))
    second = _persist(world, raw, received=FETCHED + timedelta(minutes=10))[2]
    assert (first["created"], second["created"], second["existing"]) == (2, 0, 2)
    assert counts == (_count(world.session, OddsSnapshot), _count(world.session, MarketOutcome),
                      _count(world.session, Market), _count(world.session, ExternalIdMapping))


def test_an_in_batch_duplicate_is_written_once_without_autoflush(world, slate):
    world.session.autoflush = False
    bound, _r = cfb_odds.bind_events(world.session, events(
        raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls")), received_at=FETCHED)
    written = cfb_odds.persist(world.session, bound + bound, requested_at=FETCHED, received_at=FETCHED)
    assert (written["created"], written["existing"]) == (2, 2)
    assert _count(world.session, OddsSnapshot) == 2
    assert _count(world.session, ExternalIdMapping.__table__) >= 1


@pytest.mark.parametrize("outcomes, reason", [
    ([{"name": "Home 401 Hawks", "price": -150}], cfb_odds.MARKET_NOT_TWO_SIDED),              # one-sided
    ([{"name": "Home 401 Hawks", "price": -150}, {"name": "Away 401 Owls", "price": 130},
      {"name": "Draw", "price": 2000}], cfb_odds.MARKET_NOT_TWO_SIDED),
    ([{"name": "Home 401 Hawks", "price": -150}, {"name": "Away 402 Owls", "price": 130}],
     cfb_odds.OUTCOME_NAMES_DISAGREE_WITH_EVENT),                                             # mismatched
    ([{"name": "Home 401 Hawks", "price": -150}, {"name": "Away 401 Owls", "price": 50}],
     cfb_odds.INVALID_AMERICAN_PRICE),
])
def test_a_malformed_book_is_refused_alone(world, slate, outcomes, reason):
    raw = raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", outcomes=outcomes)
    good = raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", books=("fanduel",))
    raw["bookmakers"] += good["bookmakers"]
    _b, _r, written = _persist(world, [raw])
    assert [q["reason"] for q in written["quote_receipts"]] == [reason]
    assert written["created"] == 2                                  # the good book still persists
    assert {world.session.get(Sportsbook, r.sportsbook_id).key
            for r in world.session.scalars(select(OddsSnapshot))} == {"fanduel"}


def test_a_quote_clock_after_receipt_is_refused(world, slate):
    _b, _r, written = _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls",
                                                 clock=FETCHED + timedelta(minutes=6))])
    assert [q["reason"] for q in written["quote_receipts"]] == [cfb_odds.QUOTE_CLOCK_AFTER_RECEIPT]
    assert written["created"] == 0


# -- end to end: fixture response -> ingestion -> CFB checkpoint ------------------
def _prices_for(world):
    """Generous on the model's favourite, short on the other (qualifies under the unchanged policy)."""
    row = production_import.current_forecast_for_game(world.session, "401")
    return {"home_ml": 200, "away_ml": -260} if row.joint_home_win_probability >= 0.5 else {
        "home_ml": -260, "away_ml": 200}


def _decision_401(result):
    return next(d for d in result["decisions"] if d["source_game_id"] == "401")


def test_end_to_end_an_odds_api_pair_reaches_a_qualified_decision(world, slate):
    moment, _game, _other = slate
    world.session.autoflush = False
    _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", **_prices_for(world))])
    result = paper_decision.evaluate(world.session, world.settings, now=moment)
    decision = _decision_401(result)
    assert decision["outcome"] == "QUALIFIED" and decision["reason"] == "ALL_GATES_PASSED"
    assert decision["selection"]["source"] == "THE_ODDS_API"
    assert decision["selection"]["sportsbook"] == "draftkings"
    assert decision["position_id"]
    # Game 402 had no bound pair: its own honest reason, nothing substituted.
    assert next(d for d in result["decisions"] if d["source_game_id"] == "402")["reason"] == "NO_TWO_SIDED_QUOTE"


def test_end_to_end_a_stale_odds_api_pair_declines_quote_too_stale(world, slate):
    moment, _game, _other = slate
    old = moment - timedelta(minutes=241)
    _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", clock=old,
                               **_prices_for(world))], received=old + timedelta(minutes=1))
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert (decision["outcome"], decision["reason"]) == ("DECLINED", "QUOTE_TOO_STALE")


def test_end_to_end_a_mismatched_response_declines_no_two_sided_quote(world, slate):
    moment, _game, _other = slate
    _b, _r, written = _persist(world, [raw_event(
        "FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls",
        outcomes=[{"name": "Home 401 Hawks", "price": 200}, {"name": "Away 402 Owls", "price": -260}])])
    assert written["created"] == 0
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert (decision["outcome"], decision["reason"]) == ("DECLINED", "NO_TWO_SIDED_QUOTE")


def test_other_sources_are_still_refused_by_the_checkpoint(world, slate, monkeypatch):
    moment, game, _other = slate
    _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", **_prices_for(world))])
    world.session.execute(OddsSnapshot.__table__.update().values(source="SOME_RESEARCH_FEED"))
    world.session.flush()
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert (decision["outcome"], decision["reason"]) == ("DECLINED", "NO_PERMITTED_PROVIDER")


# -- the collector job: plan, guards, ledger, lane --------------------------------
def test_plan_is_due_once_per_kickoff_cluster(world, slate):
    assert cfb_odds.plan(world.session, now=KICK - timedelta(minutes=90))["due"] is False    # too early
    planned = cfb_odds.plan(world.session, now=FETCHED)
    assert planned["due"] and sorted(g["source_game_id"] for g in planned["due_games"]) == ["401", "402"]
    reserved = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=cfb_odds.ENDPOINT_KEY,
                                     requested_at=KICK - timedelta(minutes=70), status="RESERVED",
                                     request_fingerprint="0" * 64)
    world.session.add(reserved)
    world.session.flush()
    _cfb_terminal(world.session, reserved, "SUCCEEDED")
    assert cfb_odds.plan(world.session, now=FETCHED)["due"] is False                        # already bought
    assert cfb_odds.plan(world.session, now=KICK - timedelta(minutes=30))["due"] is False   # too late


def _cfb_reserve(session, at, fingerprint):
    row = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=cfb_odds.ENDPOINT_KEY, requested_at=at,
                                status="RESERVED", request_fingerprint=fingerprint)
    session.add(row)
    session.flush()
    return row


def _cfb_terminal(session, reserved, status):
    session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=reserved.endpoint_key,
                                      requested_at=reserved.requested_at,
                                      completed_at=reserved.requested_at + timedelta(seconds=2), status=status,
                                      request_fingerprint=reserved.request_fingerprint, reservation_id=reserved.id))
    session.flush()


def test_a_failed_cfb_request_is_retried_once_inside_the_window(world, slate):
    """2026-10-17: a FAILED request used to count as the cluster's one purchase, so its games
    could never be priced. Now: in flight blocks, FAILED is retried once, then never again."""
    first = _cfb_reserve(world.session, KICK - timedelta(minutes=74), "1" * 64)
    assert cfb_odds.plan(world.session, now=KICK - timedelta(minutes=70))["due"] is False    # in flight
    _cfb_terminal(world.session, first, "FAILED")
    retry = cfb_odds.plan(world.session, now=KICK - timedelta(minutes=64))
    assert retry["due"] is True and {g["state"] for g in retry["games"]} == {"DUE"}
    assert {g["window_requests"]["failed"] for g in retry["games"]} == {1}
    _cfb_terminal(world.session, _cfb_reserve(world.session, KICK - timedelta(minutes=64), "2" * 64), "FAILED")
    spent = cfb_odds.plan(world.session, now=KICK - timedelta(minutes=54))
    assert spent["due"] is False
    assert {g["window_requests"]["blocking"] for g in spent["games"]} == {"RETRY_LIMIT_REACHED"}


def test_a_cfb_reservation_without_an_outcome_is_in_flight_then_interrupted(world, slate):
    _cfb_reserve(world.session, KICK - timedelta(minutes=74), "3" * 64)
    assert cfb_odds.plan(world.session, now=KICK - timedelta(minutes=60))["due"] is False    # 14 min: in flight
    late = cfb_odds.plan(world.session, now=KICK - timedelta(minutes=55))                    # 19 min: interrupted
    assert late["due"] is True
    assert {g["window_requests"]["interrupted"] for g in late["games"]} == {1}


@pytest.mark.asyncio
async def test_collector_job_is_off_by_default_and_on_spends_one_ledgered_cfb_credit(world, slate, settings,
                                                                                     monkeypatch):
    from app.jobs.orchestrator import JobOrchestrator

    world.session.commit()                  # the refused attempt below rolls back to here
    off = await JobOrchestrator(world.session, settings).collect_cfb_odds_api(KICK.date())
    assert off == {"records": 0, "status": "DISABLED", "provider_called": False}

    calls = []

    async def fake_snapshot(self):
        calls.append((self.sport_key, tuple(self.markets), self.regions))
        raw = [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", **_prices_for(world))]
        data = [TheOddsAPIAdapter._normalize_event(r).model_dump(mode="json") for r in raw]
        return AdapterResponse(envelope=SourceEnvelope(
            source="THE_ODDS_API", fetched_at=FETCHED, data=data,
            metadata={"event_count": 1, "sport_key": self.sport_key, "requests_remaining": 479,
                      "requests_used": 21, "requests_last": 1}), raw_payload=raw)

    real_plan = cfb_odds.plan
    monkeypatch.setattr(cfb_odds, "plan", lambda session, now=None: real_plan(session, now=FETCHED))
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", fake_snapshot)
    on = settings.model_copy(update={"cfb_odds_api_enabled": True, "the_odds_api_key": "fixture-not-a-key",  # secret-guard: test-fixture
                                     "odds_daily_request_budget": 2,
                                     "odds_cfb_lane_monthly_credits": 30, "odds_cfb_lane_daily_credits": 6})
    orchestrator = JobOrchestrator(world.session, on)

    unfunded = settings.model_copy(update={"cfb_odds_api_enabled": True, "the_odds_api_key": "fixture-not-a-key",  # secret-guard: test-fixture
                                           "odds_daily_request_budget": 2})
    from app.data_sources import SourceError

    with pytest.raises(SourceError, match="CFB paper moneylines"):
        await JobOrchestrator(world.session, unfunded).collect_cfb_odds_api(KICK.date())
    world.session.rollback()
    assert calls == []                                                   # refused before any request

    _measured_balance(world.session)
    result = await orchestrator.collect_cfb_odds_api(KICK.date())
    assert calls == [("americanfootball_ncaaf", ("h2h",), "us")]
    assert result["provider_called"] and result["bound"] == 1 and result["created"] == 2
    ledger = world.session.scalars(select(ProviderRequestLedger).where(
        ProviderRequestLedger.endpoint_key == cfb_odds.ENDPOINT_KEY)).all()
    assert sorted(r.status for r in ledger) == ["RESERVED", "SUCCEEDED"]
    terminal = next(r for r in ledger if r.status == "SUCCEEDED")
    assert terminal.quota_cost == 1 and terminal.quota_remaining == 479
    assert {r["reason"] for r in terminal.response_metadata["event_receipts"]} == {cfb_odds.BOUND}
    assert lanes.lane_of_ledger_row(terminal, group=ledger) == lanes.LANE_CFB_RESEARCH
    state = lanes.lane_budget(world.session, monthly_cap=500, reserve=150, daily_cap=18, floor=10)
    assert state.lanes[lanes.LANE_CFB_RESEARCH].spent == 1 and state.lanes[lanes.LANE_NFL_RESEARCH].spent == 0
    # The plan now sees this kickoff cluster as bought: the next tick spends nothing.
    # (The reservation carries the real wall clock; align it with the fixture clock.)
    world.session.execute(ProviderRequestLedger.__table__.update().where(
        ProviderRequestLedger.endpoint_key == cfb_odds.ENDPOINT_KEY).values(requested_at=FETCHED))
    again = await orchestrator.collect_cfb_odds_api(KICK.date())
    assert again["status"] == "NOT_DUE" and len(calls) == 1


# -- Codex 2026-09-26 corrections ---------------------------------------------------
def test_codex_clock_fixture_provider_kickoff_before_admitted_is_refused_and_never_funded(world, slate):
    """Admitted 19:30Z, provider 18:45Z, receipt 18:40Z, decision 19:10Z (Codex's exact fixture shape)."""

    moment, game, _other = slate
    provider_kickoff = KICK - timedelta(minutes=45)
    assert provider_kickoff < moment < KICK
    bound, receipts, written = _persist(world, [raw_event(
        "CLOCKPROOF", "Home 401 Hawks", "Away 401 Owls", commence=provider_kickoff, **_prices_for(world))])
    # AISP_EVENT_CLOCK_WINDOW_V1 (MAIN 10-20..23, verified by the 2026-10-24 hotfix): a provider kickoff
    # 45 min EARLIER than the admitted one is the same game, so it binds -- and the EARLIER clock
    # governs: the pair's earliest start is 18:45Z, before the 19:10Z decision, so the pair is dead
    # at the decision and nothing can ever be funded from it. (Exact equality used to refuse it as
    # KICKOFF_DISAGREEMENT; "never funded" is what the fixture proves either way.)
    import ast
    (receipt,) = receipts
    assert receipt["reason"] == cfb_odds.BOUND and [g for _e, g in bound] == [game.id]
    clock = receipt["clock_reconciliation"]
    clock = ast.literal_eval(clock) if isinstance(clock, str) else clock
    assert clock["earliest_start"] == provider_kickoff.isoformat() and clock["delta_seconds"] == -2700.0
    assert written["created"] == 2 and _count(world.session, OddsSnapshot) == 2
    assert paper_decision.kickoff_bound_pairs(world.session, game.id, KICK, moment=moment) == []
    # The earlier clock also moved the game's operational start to 18:45Z, so at 19:10Z the game is
    # past its checkpoint and the natural evaluation records nothing for it (review MEDIUM-1 notes
    # this "earlier clock opens earlier" hazard; it is unchanged by the hotfix). Never funded.
    result = paper_decision.evaluate(world.session, world.settings, now=moment)
    assert [d for d in result["decisions"] if d.get("source_game_id") == "401"] == []
    assert _positions(world) == 0


def test_the_frozen_record_alone_refuses_odds_api_pairs_before_the_amendment_clock(world, slate):
    moment, _game, _other = slate
    _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", **_prices_for(world))])
    world.settings.cfb_odds_api_source_effective_from = None
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert (decision["outcome"], decision["reason"]) == ("DECLINED", "NO_PERMITTED_PROVIDER")
    assert decision["permitted_quote_sources"] == ["SPORTSGAMEODDS"]
    world.settings.cfb_odds_api_source_effective_from = moment + timedelta(minutes=1)     # not yet effective
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert decision["reason"] == "NO_PERMITTED_PROVIDER"


def test_book_clock_only_shape_parses_with_book_provenance_and_never_a_market_clock():
    raw = raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", market_clock=False)
    assert "last_update" not in raw["bookmakers"][0]["markets"][0]
    event = TheOddsAPIAdapter._normalize_event(raw)
    market = event.bookmakers[0].markets[0]
    assert market.clock_source == "BOOK" and market.last_update == event.bookmakers[0].last_update
    dumped = event.model_dump(mode="json")
    assert dumped["bookmakers"][0]["markets"][0]["clock_source"] == "BOOK"
    assert OddsEventDTO.model_validate(dumped).bookmakers[0].markets[0].clock_source == "BOOK"


def test_market_clock_shape_is_byte_identical_to_before():
    """When the market clock is present the DTO dump carries no new key (MLB/NFL unchanged)."""

    raw = raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls")
    event = TheOddsAPIAdapter._normalize_event(raw)
    dumped = event.model_dump(mode="json")
    assert set(dumped["bookmakers"][0]["markets"][0]) == {"key", "last_update", "outcomes"}
    assert event.bookmakers[0].markets[0].clock_source is None
    assert event.bookmakers[0].markets[0].last_update == BOOK_CLOCK


def test_a_clockless_book_is_skipped_alone_not_the_whole_response():
    raw = raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", market_clock=False,
                    books=("draftkings", "fanduel"))
    del raw["bookmakers"][1]["last_update"]                       # neither clock for fanduel
    refusals: list = []
    event = TheOddsAPIAdapter._normalize_event(raw, refusals)
    assert [b.key for b in event.bookmakers] == ["draftkings"]
    assert refusals == [{"reason": "BOOK_CLOCK_MISSING", "event_id": "FIXTUREEVT00001", "book": "fanduel"}]


def test_book_clock_pairs_persist_with_book_clock_provenance(world, slate):
    world.session.autoflush = False
    _b, _r, written = _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls",
                                                 market_clock=False)])
    assert written["created"] == 2 and written["quote_receipts"] == []
    for r in world.session.scalars(select(OddsSnapshot)).all():
        assert r.observed_at.replace(tzinfo=UTC) == BOOK_CLOCK
        assert r.raw_payload["quote_clock_source"] == cfb_odds.BOOK_CLOCK
        assert r.raw_payload["market_last_update"] is None                   # never relabelled
        assert r.raw_payload["book_last_update"] == BOOK_CLOCK.isoformat()


@pytest.mark.asyncio
async def test_book_clock_only_response_archives_and_binds_through_the_real_adapter(world, slate, settings,
                                                                                    monkeypatch):
    """The documented /odds shape end to end: real get_market_snapshot (mock transport), archive, bind, store."""

    from app.jobs.orchestrator import JobOrchestrator

    world.session.commit()
    raw = [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", market_clock=False,
                     **_prices_for(world))]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/americanfootball_ncaaf/odds")
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

    real_plan = cfb_odds.plan
    monkeypatch.setattr(cfb_odds, "plan", lambda session, now=None: real_plan(session, now=FETCHED))
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", mocked_snapshot)
    on = settings.model_copy(update={"cfb_odds_api_enabled": True, "the_odds_api_key": "fixture-not-a-key",  # secret-guard: test-fixture
                                     "odds_daily_request_budget": 2,
                                     "odds_cfb_lane_monthly_credits": 30, "odds_cfb_lane_daily_credits": 6})
    _measured_balance(world.session)
    result = await JobOrchestrator(world.session, on).collect_cfb_odds_api(KICK.date())
    assert result["provider_called"] and result["bound"] == 1 and result["created"] == 2
    assert result["archive_payload_sha256"] and result["clock_refusals"] == []
    rows = world.session.scalars(select(OddsSnapshot)).all()
    assert {r.raw_payload["quote_clock_source"] for r in rows} == {cfb_odds.BOOK_CLOCK}
    assert {r.raw_payload["archive_payload_sha256"] for r in rows} == {result["archive_payload_sha256"]}


# -- V3: the retained kickoff is re-checked at decision and at position creation ---
def _reschedule(world, game, kickoff, monkeypatch):
    """Move the canonical game; keep discovery from re-importing the old kickoff, keep the lease current."""

    world.settings.cfb_production_consumer_enabled = False
    lease = production_import.present_eligibility
    monkeypatch.setattr(production_import, "present_eligibility",
                        lambda session, forecast, now: {**lease(session, forecast, now=KICK - timedelta(minutes=20)),
                                                        "eligible": True})
    game.starts_at = kickoff
    world.session.flush()


def _positions(world):
    from app.models import OfficialAspPosition

    return _count(world.session, OfficialAspPosition)


def test_v3_a_game_moved_later_invalidates_the_old_quote_until_one_at_the_new_kickoff_arrives(
        world, slate, monkeypatch):
    _moment, game, _other = slate
    world.session.autoflush = False
    _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", **_prices_for(world))])
    new_kick = KICK + timedelta(minutes=30)                                    # 19:30Z -> 20:00Z
    _reschedule(world, game, new_kick, monkeypatch)
    at = new_kick - timedelta(minutes=20)
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=at))
    assert (decision["outcome"], decision["reason"]) == ("DECLINED", paper_decision.QUOTE_KICKOFF_MISMATCH)
    assert not decision.get("position_id") and _positions(world) == 0
    (refusal,) = decision["kickoff_refusals"]
    assert refusal["current_kickoff"] == new_kick.isoformat()
    assert refusal["retained_kickoffs"] == [KICK.isoformat()]
    # A fresh quote retained at the NEW kickoff: same book, same price, same book clock -- a new row.
    _b, receipts, written = _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls",
                                                       commence=new_kick, **_prices_for(world))])
    assert receipts[0]["reason"] == cfb_odds.BOUND and written["created"] == 2
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=at + timedelta(minutes=1)))
    assert (decision["outcome"], decision["reason"]) == ("QUALIFIED", "ALL_GATES_PASSED")
    assert decision["selection"]["retained_kickoff"] == new_kick.isoformat()
    assert decision["position_id"] and _positions(world) == 1


def test_v3_b_a_game_moved_earlier_past_the_cutoff_creates_no_position(world, slate, monkeypatch):
    moment, game, _other = slate                                               # decision clock 19:10Z
    world.session.autoflush = False
    _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", **_prices_for(world))])
    era = paper_decision.experiment(world.session)
    (due,) = [g for g in paper_decision.games_at_checkpoint(world.session, now=moment) if g["game_id"] == game.id]
    decided = paper_decision.decide(world.session, world.settings, era, due, moment)
    assert decided["outcome"] == "QUALIFIED"
    # Rescheduled to 19:20Z between the checkpoint and position creation: cutoff 19:05Z < 19:10Z.
    _reschedule(world, game, KICK - timedelta(minutes=10), monkeypatch)
    recorded = paper_decision.record(world.session, era, due, decided, moment)
    assert (recorded["outcome"], recorded["reason"]) == ("DECLINED", paper_decision.PAST_DECISION_CUTOFF)
    assert recorded["position_guard"]["cutoff"] == (KICK - timedelta(minutes=25)).isoformat()
    assert recorded["position_id"] is None and _positions(world) == 0
    # And a checkpoint run after the move no longer even considers the game.
    result = paper_decision.evaluate(world.session, world.settings, now=moment)
    assert all(d["source_game_id"] != "401" for d in result["decisions"])
    assert _positions(world) == 0


def test_v3_b_a_quote_bound_at_the_old_kickoff_is_refused_at_position_creation(world, slate, monkeypatch):
    """Same kickoff race, but the move keeps the clock inside the cutoff: the retained kickoff decides."""

    moment, game, _other = slate
    world.session.autoflush = False
    _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", **_prices_for(world))])
    era = paper_decision.experiment(world.session)
    (due,) = [g for g in paper_decision.games_at_checkpoint(world.session, now=moment) if g["game_id"] == game.id]
    decided = paper_decision.decide(world.session, world.settings, era, due, moment)
    _reschedule(world, game, KICK + timedelta(minutes=5), monkeypatch)
    recorded = paper_decision.record(world.session, era, due, decided, moment)
    assert (recorded["outcome"], recorded["reason"]) == ("DECLINED", paper_decision.QUOTE_KICKOFF_MISMATCH)
    assert recorded["position_id"] is None and _positions(world) == 0


def test_v3_c_normalized_timestamps_compare_as_instants(world, slate):
    from zoneinfo import ZoneInfo

    eastern = datetime(2026, 9, 26, 15, 30, tzinfo=ZoneInfo("America/New_York"))
    same = {paper_decision.utc_instant(v) for v in (
        eastern, "2026-09-26T19:30:00Z", "2026-09-26T19:30:00+00:00", "2026-09-26T15:30:00-04:00",
        datetime(2026, 9, 26, 19, 30), datetime(2026, 9, 26, 19, 30, tzinfo=UTC))}
    assert same == {datetime(2026, 9, 26, 19, 30, tzinfo=UTC)}
    assert paper_decision.utc_instant("not a time") is None and paper_decision.utc_instant(None) is None
    # A provider kickoff written with an ET offset binds and matches the UTC game kickoff, and an
    # ET-aware game kickoff (what the production session hands back) matches the stored UTC one.
    moment, game, _other = slate
    raw = raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", **_prices_for(world))
    raw["commence_time"] = KICK.astimezone(ZoneInfo("America/New_York")).isoformat()     # ...T15:30:00-04:00
    _b, receipts, written = _persist(world, [raw])
    assert receipts[0]["reason"] == cfb_odds.BOUND and written["created"] == 2
    kickoff_et = paper_decision.utc_instant(KICK.astimezone(ZoneInfo("America/New_York")))
    assert len(paper_decision.kickoff_bound_pairs(world.session, game.id, kickoff_et, moment=moment)) == 1
    # A BOUND pair pins the canonical start it was bound against: a kickoff change refuses it
    # (BINDING_IDENTITY_OR_SCHEDULE_CHANGED), exactly as the old exact rule did for a different reason.
    assert paper_decision.kickoff_bound_pairs(world.session, game.id, KICK + timedelta(seconds=1), moment=moment) == []
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert decision["outcome"] == "QUALIFIED" and decision["position_id"]


def test_v3_d_book_clock_quote_keeps_book_provenance_and_is_still_aged(world, slate):
    moment, _game, _other = slate
    world.session.autoflush = False
    _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", market_clock=False,
                               **_prices_for(world))])
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert decision["outcome"] == "QUALIFIED"
    assert decision["selection"]["quote_clock_source"] == cfb_odds.BOOK_CLOCK
    assert decision["selection"]["observed_at"] == BOOK_CLOCK.isoformat()


def test_v3_d_a_stale_book_clock_quote_declines_quote_too_stale(world, slate):
    moment, _game, _other = slate
    _persist(world, [raw_event("FIXTUREEVT00001", "Home 401 Hawks", "Away 401 Owls", market_clock=False,
                               clock=moment - timedelta(minutes=241), **_prices_for(world))],
             received=moment - timedelta(minutes=200))
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert (decision["outcome"], decision["reason"]) == ("DECLINED", "QUOTE_TOO_STALE")
    (pair,) = decision["pairs_considered"]
    assert pair["home_quote_clock_source"] == pair["away_quote_clock_source"] == cfb_odds.BOOK_CLOCK
