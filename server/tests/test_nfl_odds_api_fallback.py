"""The NFL price fallback: SportsGameOdds refused -> ONE The Odds API request.

Every test here is offline: `TheOddsAPIAdapter.get_market_snapshot` is replaced
by a fake that records its call, and the SGO collector is replaced by a stub
that returns its refusal (or its success) receipt. Nothing reaches a network.

WHAT IS PINNED
--------------
* SGO refused -> exactly one Odds API request (americanfootball_nfl, h2h, us),
  ledgered RESERVED then SUCCEEDED, quotes bound to the due cluster's games by
  exact identity, nothing written for any other game -- and the NFL paper
  decision then reads that pair as permitted and fresh;
* SGO collected, deferred or had nothing due -> no Odds API call;
* flag off -> no call and the SGO receipt is returned unchanged;
* daily cap reached, quota policy refused -> no request;
* a failed zero-charge request -> ledger FAILED, and ONE owed retry on a later
  tick before the T-21 hard stop; a charged failure owes none;
* the same cluster is never requested twice;
* (hotfix 2026-10-24) the primary is eligible on EVERY tick from T-30 through
  T-21, so a tick refused before any claim (quota/cooldown) is followed by a
  successful attempt on the next tick instead of closing the window.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.data_sources import SourceError
from app.data_sources.base import AdapterResponse, SourceEnvelope
from app.data_sources.odds import TheOddsAPIAdapter
from app.jobs.orchestrator import JobOrchestrator
from app.models import (
    ExternalIdMapping, Game, GameStatus, GameTeam, League, NflAcquisitionPhaseClaim,
    OddsSnapshot, ProviderRequestLedger, Season, ShadowForecast, ShadowForecastBundle,
    Sport, Team,
)
from app.services import nfl_odds_api_fallback as fallback
from app.services import nfl_paper_decision as decision
from app.services import nfl_paper_policy as policy
from app.services import odds_budget_lanes as lanes
from app.services import sgo_nfl_pregame

KICK = datetime(2026, 10, 11, 17, 0, tzinfo=UTC)          # a 1:00 p.m. ET Sunday cluster
LATE = datetime(2026, 10, 11, 20, 25, tzinfo=UTC)         # a later cluster, out of scope
T26 = KICK - timedelta(minutes=26)
T23 = KICK - timedelta(minutes=23)
BOOK_CLOCK = KICK - timedelta(minutes=31)                 # the books' own last_update
OWNER = "fallback-test-owner"

GAMES = (
    # (home id, away id, home name, away name, kickoff)
    ("LV", "KC", "Las Vegas Raiders", "Kansas City Chiefs", KICK),
    ("BAL", "TEN", "Baltimore Ravens", "Tennessee Titans", KICK),
    ("SEA", "LAC", "Seattle Seahawks", "Los Angeles Chargers", LATE),
)

SGO_PAUSED = {"attempted": False, "reason": "ALLOCATION_BUDGET_PAUSED_UNTIL_REPLENISHMENT",
              "objects": 0, "lane": "sgo_nfl_events"}


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class World:
    def __init__(self, session):
        self.session = session
        sport = Sport(code="FOOTBALL", name="NFL"); session.add(sport); session.flush()
        league = League(sport_id=sport.id, code="NFL", name="NFL"); session.add(league); session.flush()
        season = Season(league_id=league.id, name="2026", starts_at=KICK - timedelta(days=40),
                        ends_at=KICK + timedelta(days=120))
        session.add(season); session.flush()
        bundle = ShadowForecastBundle(
            bundle_sha256=uuid.uuid4().hex + uuid.uuid4().hex, bundle_id="b-fallback",
            logical_uri="asp-model-exchange://fixture", source_fixture_id="f",
            slate_date=KICK.date(), league="NFL", sport="NFL", status="IMPORTED",
            research_shadow_only=True, generated_at=KICK - timedelta(minutes=40),
            forecast_count=len(GAMES), quote_count=0, candidate_count=0,
            imported_at=KICK - timedelta(minutes=34), contract_version="v1", evidence={})
        session.add(bundle); session.flush()
        self.games: dict[str, Game] = {}
        self.names: dict[str, tuple[str, str]] = {}
        for home_id, away_id, home_name, away_name, kickoff in GAMES:
            game = Game(league_id=league.id, season_id=season.id, starts_at=kickoff,
                        status=GameStatus.SCHEDULED)
            session.add(game); session.flush()
            for is_home, abbr, name in ((True, home_id, home_name), (False, away_id, away_name)):
                team = Team(league_id=league.id, name=name, abbreviation=abbr)
                session.add(team); session.flush()
                session.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
            available = kickoff - timedelta(minutes=35)
            session.add(ShadowForecast(
                bundle_id=bundle.id, forecast_id=f"fid-{home_id}-{uuid.uuid4().hex}",
                engine_id="NFL_M1_XGBOOST", engine_version="NFL_M1_2026_RESEARCH_SHADOW_V1",
                model_artifact_sha256=uuid.uuid4().hex, simulator_id="s", simulator_version="1",
                simulator_seed=1, event_id=f"2026_06_{away_id}_{home_id}",
                source_event_id=f"nflverse:2026_06_{away_id}_{home_id}", event_date=kickoff.date(),
                season=2026, home_team_id=home_id, away_team_id=away_id, is_ready=True,
                readiness_score=99.0, readiness_threshold=50.0, status="READY",
                full_home_win_probability=0.60, full_away_win_probability=0.38,
                full_expected_home_score=24.0, full_expected_away_score=20.0, distributions={},
                uncertainty={}, readiness_detail={}, forecast_schema_version="1",
                snapshot_type="FINAL_PREGAME", snapshot_version=1, identity_evidence={},
                lineage_detail={}, canonical_game_id=game.id, available_as_of_utc=available,
                scheduled_start_utc=kickoff,
                forecast_cutoff_utc=available - timedelta(minutes=5), created_at=available))
            self.games[home_id] = game
            self.names[home_id] = (home_name, away_name)
        session.commit()

    def gid(self, home_id: str) -> str:
        return str(self.games[home_id].id)

    def raw(self, home_id: str, *, home_ml=+120, away_ml=-140) -> dict:
        home, away = self.names[home_id]
        kickoff = self.games[home_id].starts_at
        market = {"key": "h2h", "last_update": _stamp(BOOK_CLOCK), "outcomes": [
            {"name": home, "price": home_ml}, {"name": away, "price": away_ml}]}
        return {"id": f"oddsapi-{home_id}", "sport_key": "americanfootball_nfl",
                "commence_time": _stamp(kickoff.replace(tzinfo=UTC) if kickoff.tzinfo is None
                                        else kickoff),
                "home_team": home, "away_team": away,
                "bookmakers": [{"key": "draftkings", "title": "DraftKings",
                                "last_update": _stamp(BOOK_CLOCK), "markets": [market]}]}


@pytest.fixture
def world(session):
    return World(session)


def _measured_balance(session, remaining=19967):
    """An Odds API reading inside this billing month (non-protected lanes need one)."""

    now = datetime.now(UTC)
    reserved = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key="quota_recovery_probe",
                                     requested_at=now - timedelta(minutes=1), status="RESERVED",
                                     request_fingerprint="c" * 64)
    session.add(reserved)
    session.flush()
    session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key="quota_recovery_probe",
                                      requested_at=reserved.requested_at, completed_at=now,
                                      status="SUCCEEDED", request_fingerprint="c" * 64, quota_cost=0,
                                      reservation_id=reserved.id, quota_remaining=remaining,
                                      response_metadata={"requests_used": 0}))
    session.commit()


@pytest.fixture
def on(settings, monkeypatch):
    monkeypatch.setenv("ASP_ODDS_COLLECTION_OWNER", OWNER)
    return settings.model_copy(update={
        "nfl_odds_api_fallback_enabled": True, "the_odds_api_key": "fixture-not-a-key",  # secret-guard: test-fixture
        "odds_collection_owner": OWNER, "odds_daily_request_budget": 20,
        "odds_monthly_credit_cap": 20000, "odds_daily_credit_budget": 18,
        "nfl_shadow_odds_enabled": False,
    })


@pytest.fixture
def provider(world, monkeypatch):
    """A fake The Odds API: records each call and answers with every NFL event."""

    calls: list[tuple] = []
    state = {"fail": None}

    async def fake_snapshot(self):
        calls.append((self.sport_key, tuple(self.markets), self.regions))
        if state["fail"] is not None:
            raise state["fail"]
        raw = [world.raw("LV"), world.raw("BAL"), world.raw("SEA")]
        data = [TheOddsAPIAdapter._normalize_event(r).model_dump(mode="json") for r in raw]
        return AdapterResponse(envelope=SourceEnvelope(
            source="THE_ODDS_API", fetched_at=T26, data=data,
            metadata={"event_count": len(raw), "sport_key": self.sport_key,
                      "requests_remaining": 19966, "requests_used": 34, "requests_last": 1}),
            raw_payload=raw)

    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", fake_snapshot)
    return SimpleNamespace(calls=calls, state=state)


def _sgo(monkeypatch, receipt):
    seen = []

    def stub(*args, **kwargs):
        seen.append(kwargs)
        return dict(receipt)

    monkeypatch.setattr(sgo_nfl_pregame, "collect_nfl_pregame", stub)
    return seen


async def _tick(session, settings, moment):
    """The real job entry point, with the fallback's clock pinned to ``moment``."""

    orchestrator = JobOrchestrator(session, settings)
    real = orchestrator._nfl_odds_api_fallback

    async def pinned(**kwargs):
        return await real(now=moment, **kwargs)

    orchestrator._nfl_odds_api_fallback = pinned
    return await orchestrator.collect_nfl_pregame_odds(moment.date())


def _ledger(session):
    return session.scalars(select(ProviderRequestLedger).where(
        ProviderRequestLedger.endpoint_key == fallback.ENDPOINT_KEY)).all()


# -- 1. SGO refused -> one request -> bound -> the decision reads it -------------------
@pytest.mark.asyncio
async def test_sgo_refused_makes_one_request_binds_the_cluster_and_the_decision_reads_it(
        world, on, provider, monkeypatch):
    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)

    result = await _tick(world.session, on, T26)

    assert result["reason"] == SGO_PAUSED["reason"]               # the SGO receipt is kept
    receipt = result["odds_api_fallback"]
    assert receipt["reason"] == "COLLECTED" and receipt["attempted"] is True, receipt.get("detail")
    assert provider.calls == [("americanfootball_nfl", ("h2h",), "us")]
    # Exactly the due cluster's two games bound; the 20:25 game was answered and NOT written.
    assert receipt["bound_game_ids"] == sorted([world.gid("LV"), world.gid("BAL")])
    assert receipt["out_of_scope_events"] == 1
    assert receipt["quotes"] == 4                                   # two games x two sides
    sources = set(world.session.scalars(select(OddsSnapshot.source)))
    assert sources == {"THE_ODDS_API"}
    assert world.session.scalar(select(ExternalIdMapping.id).where(
        ExternalIdMapping.external_id == "oddsapi-SEA")) is None

    # Ledgered: RESERVED then SUCCEEDED, charged to the NFL_RESEARCH credit lane.
    ledger = _ledger(world.session)
    assert sorted(r.status for r in ledger) == ["RESERVED", "SUCCEEDED"]
    terminal = next(r for r in ledger if r.status == "SUCCEEDED")
    assert terminal.quota_cost == 1 and terminal.quota_remaining == 19966
    assert lanes.lane_of_ledger_row(terminal, group=ledger) == lanes.LANE_NFL_RESEARCH
    shape = terminal.response_metadata["request_shape"]
    assert shape["markets"] == ["h2h"] and shape["regions"] == "us"
    assert shape["clusters"] == [{"kickoff_cluster_utc": KICK.isoformat(), "phase": "PRIMARY"}]

    # THE DECISION READS IT: a same-book, two-sided THE_ODDS_API pair, permitted,
    # and fresh at the latest checkpoint instant (T-15) on the book's own clock.
    pairs = decision.paired_quotes(world.session, world.games["LV"].id)
    assert len(pairs) == 1 and pairs[0]["source"] == "THE_ODDS_API"
    assert pairs[0]["sportsbook"] == "draftkings"
    assert policy.PERMITTED_QUOTE_PROVIDERS["THE_ODDS_API"]["permitted"] is True

    monkeypatch.setattr(decision, "_check_readiness", lambda *a, **k: SimpleNamespace(
        verified=True, reason=None, sub_reasons=(), detail=None, as_record=lambda: {"verified": True}))
    monkeypatch.setattr(decision.eligibility, "check",
                        lambda *a, **k: {"eligible": True, "supplement_id": "fixture-supplement"})
    monkeypatch.setattr(decision.eligibility, "successor_gap", lambda *a, binding, **k: binding)
    era = SimpleNamespace(id=uuid.uuid4(), starting_bankroll=1000)
    game = world.games["LV"]
    outcome = decision._decide(
        world.session, era,
        {"game_id": game.id, "away": "KC", "home": "LV", "starts_at": KICK.isoformat()},
        KICK - timedelta(minutes=15), on)
    assert outcome["outcome"] == "QUALIFIED", outcome
    assert outcome["selection"]["source"] == "THE_ODDS_API"
    assert outcome["selection"]["pair_age_minutes"] == 16.0          # T-31 book clock at T-15
    assert outcome["selection"]["pair_age_minutes"] <= decision.MAX_QUOTE_AGE_MINUTES


# -- 2. SGO succeeded (or anything but a refusal) -> no Odds API call ----------------
@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [
    {"attempted": True, "reason": "COLLECTED", "objects": 2},
    {"attempted": True, "reason": "REQUEST_FAILED", "objects": 0},
    {"attempted": False, "reason": "NO_NFL_GAME_IN_THE_COLLECTION_WINDOW", "objects": 0},
    {"attempted": False, "reason": "DEFERRED_AMBIGUOUS_SELECTION", "objects": 0},
    {"attempted": False, "reason": "PHASE_ALREADY_CLAIMED", "objects": 0},
])
async def test_no_fallback_unless_sgo_refused(world, on, provider, monkeypatch, receipt):
    _measured_balance(world.session)
    _sgo(monkeypatch, receipt)
    result = await _tick(world.session, on, T26)
    assert result == receipt
    assert provider.calls == [] and _ledger(world.session) == []


@pytest.mark.parametrize("reason", [
    "LANE_HALTED", "PROVIDER_NOT_ELIGIBLE", "NFL_LANE_RENEWAL_CAP_REACHED", "OBJECT_RESERVE_GUARD",
    "ALLOCATION_BUDGET_PAUSED_UNTIL_REPLENISHMENT", "ALLOCATION_LANE_DAILY_ALLOWANCE_SPENT",
    "ALLOCATION_LANE_NOT_FUNDED", "ALLOCATION_OWNER_EXCEPTION_REQUEST_LIMIT_REACHED",
])
def test_every_budget_or_eligibility_refusal_triggers(reason):
    assert fallback.sgo_refused({"attempted": False, "reason": reason}) is True


# -- 3. flag off -> no fallback, receipt unchanged -----------------------------------
@pytest.mark.asyncio
async def test_flag_off_makes_no_request_and_leaves_the_sgo_receipt_alone(
        world, on, provider, monkeypatch):
    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)
    off = on.model_copy(update={"nfl_odds_api_fallback_enabled": False})
    result = await _tick(world.session, off, T26)
    assert result == SGO_PAUSED and "odds_api_fallback" not in result
    assert provider.calls == [] and _ledger(world.session) == []


def test_the_flag_is_on_by_default_and_independent_of_the_legacy_poller(settings):
    assert settings.nfl_odds_api_fallback_enabled is True
    assert settings.nfl_odds_api_fallback_daily_request_cap == 20
    armed = settings.model_copy(update={"nfl_odds_api_fallback_enabled": True})
    assert armed.nfl_shadow_odds_enabled is False                    # never turned on by this flag


def test_the_legacy_interval_poller_stays_unscheduled(on):
    from app.jobs.scheduler import build_scheduler

    scheduler = build_scheduler(on.model_copy(update={"nfl_shadow_enabled": True}))
    assert scheduler.get_job("asp-nfl-shadow-odds") is None


# -- 4. daily cap reached -> no request ----------------------------------------------
@pytest.mark.asyncio
async def test_the_daily_cap_stops_the_request(world, on, provider, monkeypatch):
    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)
    for n in range(2):
        world.session.add(ProviderRequestLedger(
            provider="THE_ODDS_API", endpoint_key=fallback.ENDPOINT_KEY,
            requested_at=T26 - timedelta(hours=3, minutes=n), status="RESERVED",
            request_fingerprint=f"{n}".ljust(64, "d")))
    world.session.commit()
    capped = on.model_copy(update={"nfl_odds_api_fallback_daily_request_cap": 2})
    result = await _tick(world.session, capped, T26)
    receipt = result["odds_api_fallback"]
    assert receipt["reason"] == "DAILY_REQUEST_CAP_REACHED"
    assert receipt["requests_today"] == 2 and receipt["daily_request_cap"] == 2
    assert provider.calls == []
    assert world.session.scalar(select(func.count(NflAcquisitionPhaseClaim.id))) == 0


@pytest.mark.asyncio
async def test_the_existing_odds_quota_policy_is_respected(world, on, provider, monkeypatch):
    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)

    def refuse(self, credit_cost=1, **kwargs):              # the fallback passes its cooldown scope
        raise SourceError("THE_ODDS_API", "Credit budget exhausted", retryable=False)

    monkeypatch.setattr(JobOrchestrator, "_enforce_odds_quota_policy", refuse)
    result = await _tick(world.session, on, T26)
    assert result["odds_api_fallback"]["reason"] == "ODDS_QUOTA_POLICY_REFUSED"
    assert provider.calls == [] and _ledger(world.session) == []


# -- 5. a failed request -> FAILED, with the existing owed-retry semantics ------------
@pytest.mark.asyncio
async def test_a_zero_charge_failure_is_ledgered_failed_and_owes_one_retry_at_t23(
        world, on, provider, monkeypatch):
    import httpx

    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)
    # Proven zero charge: the connection was never established, so no request was sent.
    try:
        raise httpx.ConnectError("connection refused")
    except httpx.ConnectError as cause:
        provider.state["fail"] = SourceError("THE_ODDS_API", "Odds request failed: ConnectError")
        provider.state["fail"].__cause__ = cause

    first = await _tick(world.session, on, T26)
    assert first["odds_api_fallback"]["reason"] == "REQUEST_FAILED"
    assert first["odds_api_fallback"]["response_received"] is False
    assert first["odds_api_fallback"]["zero_charge_proven"] is True
    ledger = _ledger(world.session)
    assert sorted(r.status for r in ledger) == ["FAILED", "RESERVED"]
    failed = next(r for r in ledger if r.status == "FAILED")
    assert failed.error_type == "SourceError" and failed.response_metadata["response_received"] is False
    assert failed.response_metadata["zero_charge_proven"] is True

    # The claim is spent by trying, so no second primary; the single owed retry is due on the very
    # next tick (hotfix 2026-10-24: the window is T-30..T-21 on every tick, not two fixed instants).
    assert fallback.retry_is_owed(world.session, kickoff=KICK) is True
    assert [e["phase"] for e in fallback.due_games(world.session, now=T26 + timedelta(minutes=1))] == ["RETRY", "RETRY"]
    provider.state["fail"] = None
    retry = await _tick(world.session, on, T26 + timedelta(minutes=1))
    assert retry["odds_api_fallback"]["reason"] == "COLLECTED"
    assert len(provider.calls) == 2
    phases = sorted(c["phase"] for r in _ledger(world.session) if r.status == "RESERVED"
                    for c in r.response_metadata["request_shape"]["clusters"])
    assert phases == ["PRIMARY", "RETRY"]
    # ...and never a third, on any later tick of the window.
    assert fallback.retry_is_owed(world.session, kickoff=KICK) is False
    for later in (T23, T23 + timedelta(seconds=30), KICK - timedelta(minutes=22)):
        assert (await _tick(world.session, on, later))["odds_api_fallback"]["reason"] \
            == "NO_GAME_IN_THE_CHECKPOINT_PHASE"
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_a_failure_after_a_response_owes_no_retry(world, on, provider, monkeypatch):
    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)

    def broken_ingest(self, *a, **k):
        raise RuntimeError("ingest broke after the provider answered")

    from app.services.nfl_shadow_odds_service import NFLShadowOddsService

    monkeypatch.setattr(NFLShadowOddsService, "ingest", broken_ingest)
    result = await _tick(world.session, on, T26)
    assert result["odds_api_fallback"]["reason"] == "REQUEST_FAILED"
    assert result["odds_api_fallback"]["response_received"] is True
    assert fallback.retry_is_owed(world.session, kickoff=KICK) is False
    retry = await _tick(world.session, on, T23)
    assert retry["odds_api_fallback"]["reason"] == "NO_GAME_IN_THE_CHECKPOINT_PHASE"
    assert len(provider.calls) == 1


# -- 6. no duplicate request in the same cluster -------------------------------------
@pytest.mark.asyncio
async def test_one_request_per_cluster_across_every_primary_tick(world, on, provider, monkeypatch):
    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)
    for offset in range(30, 21, -1):                               # every 1-minute tick of the T-30..T-21 window
        await _tick(world.session, on, KICK - timedelta(minutes=offset))
    assert len(provider.calls) == 1
    assert sorted(r.status for r in _ledger(world.session)) == ["RESERVED", "SUCCEEDED"]
    claims = world.session.scalars(select(NflAcquisitionPhaseClaim)).all()
    assert [(c.lane, c.phase, c.provider) for c in claims] == [
        (fallback.CLAIM_LANE, "PRIMARY", "THE_ODDS_API")]
    # A successful primary owes no retry.
    assert (await _tick(world.session, on, T23))["odds_api_fallback"]["reason"] \
        == "NO_GAME_IN_THE_CHECKPOINT_PHASE"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_a_cluster_another_run_holds_is_inert(world, on, provider, monkeypatch):
    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)
    # Another contender's claim lands between this run's due check and its claim.
    real_due = fallback.due_games

    def due_then_lose(session, *, now):
        due = real_due(session, now=now)
        sgo_nfl_pregame.claim_phase(
            session, kickoff_cluster=KICK, phase="PRIMARY", due_game_ids=["x"],
            invocation_id="other", invocation_path="SCHEDULED", now=now,
            lane=fallback.CLAIM_LANE, provider=fallback.PROVIDER)
        return due

    monkeypatch.setattr(fallback, "due_games", due_then_lose)
    result = await _tick(world.session, on, T26)
    assert result["odds_api_fallback"]["reason"] == "PHASE_ALREADY_CLAIMED"
    assert provider.calls == [] and _ledger(world.session) == []


@pytest.mark.asyncio
async def test_outside_the_window_nothing_is_requested(world, on, provider, monkeypatch):
    """(T-21, T-30]: T-30 is the first eligible tick, T-21 is the hard stop (exclusive)."""
    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)
    for moment in (KICK - timedelta(minutes=60), KICK - timedelta(minutes=30, seconds=1),
                   KICK - timedelta(minutes=21), KICK - timedelta(minutes=20)):
        result = await _tick(world.session, on, moment)
        assert result["odds_api_fallback"]["reason"] == "NO_GAME_IN_THE_CHECKPOINT_PHASE"
    assert provider.calls == []
    assert fallback.due_games(world.session, now=KICK - timedelta(minutes=30))
    assert not fallback.due_games(world.session, now=KICK - timedelta(minutes=21))


# -- (hotfix 2026-10-24) a refused tick no longer closes the window ------------------
@pytest.mark.asyncio
async def test_a_refusal_at_t27_is_followed_by_a_successful_attempt_at_t26(world, on, provider, monkeypatch):
    """TNF 10-22: both [T-27, T-25] ticks were refused by a cooldown another lane had set; a refusal
    happens before any claim or reservation, so the window simply closed. Now the next tick buys."""
    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)
    real = JobOrchestrator._enforce_odds_quota_policy
    refusals = {"left": 1}

    def refuse_once(self, credit_cost=1, **kwargs):
        if refusals["left"]:
            refusals["left"] -= 1
            raise SourceError("THE_ODDS_API", "Odds refresh cooldown active for 88 more seconds", retryable=False)
        return real(self, credit_cost, **kwargs)

    monkeypatch.setattr(JobOrchestrator, "_enforce_odds_quota_policy", refuse_once)
    refused = await _tick(world.session, on, KICK - timedelta(minutes=27))
    assert refused["odds_api_fallback"]["reason"] == "ODDS_QUOTA_POLICY_REFUSED"
    assert provider.calls == [] and _ledger(world.session) == []
    assert world.session.scalars(select(NflAcquisitionPhaseClaim)).all() == []         # nothing claimed, nothing spent
    # The next tick: still due, one request, bound.
    bought = await _tick(world.session, on, T26)
    assert bought["odds_api_fallback"]["reason"] == "COLLECTED"
    assert bought["odds_api_fallback"]["bound_game_ids"] == sorted([world.gid("LV"), world.gid("BAL")])
    assert len(provider.calls) == 1
    # And T-25, T-24 ... T-22 buy nothing more: the cluster is claimed and captured.
    for offset in (25, 24, 23, 22):
        assert (await _tick(world.session, on, KICK - timedelta(minutes=offset)))["odds_api_fallback"]["reason"] \
            == "NO_GAME_IN_THE_CHECKPOINT_PHASE"
    assert len(provider.calls) == 1
    assert sorted(r.status for r in _ledger(world.session)) == ["RESERVED", "SUCCEEDED"]


@pytest.mark.asyncio
async def test_a_non_owner_process_never_spends(world, on, provider, monkeypatch):
    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)
    monkeypatch.setenv("ASP_ODDS_COLLECTION_OWNER", "someone-else")
    result = await _tick(world.session, on, T26)
    assert result["odds_api_fallback"]["reason"] == "NOT_THE_ODDS_COLLECTION_OWNER"
    assert provider.calls == []


def test_scoped_ingest_keeps_the_unscoped_receipt_shape(world):
    """Existing callers (no ``game_ids``) see exactly the old keys."""

    from app.services.nfl_shadow_odds_service import NFLShadowOddsService

    events = [TheOddsAPIAdapter._normalize_event(world.raw("LV"))]
    result = NFLShadowOddsService(world.session).ingest(events, available_as_of=T26)
    assert set(result) == {"events", "mapped_events", "quotes", "duplicates", "rejected_events", "event_receipts"}
    assert result["mapped_events"] == 1 and result["quotes"] == 2


# -- owner-approved NFL lane daily cap (2026-10-06): five Sunday clusters + one retry --
def test_nfl_research_lane_fits_five_sunday_clusters_plus_one_retry():
    """2026-10-11 has five T-26 clusters in one ET day (13:30, 17:00, 20:05, 20:25 and
    SNF 00:20Z). With four already spent, SNF is granted; a seventh request is refused."""
    spec = lanes.LANES[lanes.LANE_NFL_RESEARCH]
    assert spec.daily_cap == 6 and spec.monthly_allocation == 60

    after_four = lanes.LaneState(spec=spec, spent=4, spent_today=4, requests=4)
    assert after_four.remaining_today == 2          # SNF (5th) plus one owed retry fit

    after_six = lanes.LaneState(spec=spec, spent=6, spent_today=6, requests=6)
    assert after_six.remaining_today == 0           # a 7th request in the ET day cannot fit

# -- unknown charge never earns a retry (Codex review: missing quota / lost response) --
@pytest.mark.asyncio
@pytest.mark.parametrize("make_failure", [
    lambda: SourceError("THE_ODDS_API", "HTTP 503", retryable=True),          # no cause: unknown
    "read_timeout",                                                          # sent, answer lost
])
async def test_an_unknown_charge_failure_owes_no_retry(world, on, provider, monkeypatch, make_failure):
    import httpx

    _measured_balance(world.session)
    _sgo(monkeypatch, SGO_PAUSED)
    if make_failure == "read_timeout":
        try:
            raise httpx.ReadTimeout("no answer")
        except httpx.ReadTimeout as cause:
            failure = SourceError("THE_ODDS_API", "Odds request failed: ReadTimeout")
            failure.__cause__ = cause
    else:
        failure = make_failure()
    provider.state["fail"] = failure

    first = await _tick(world.session, on, T26)
    assert first["odds_api_fallback"]["reason"] == "REQUEST_FAILED"
    assert first["odds_api_fallback"]["zero_charge_proven"] is False
    failed = next(r for r in _ledger(world.session) if r.status == "FAILED")
    assert failed.response_metadata["zero_charge_proven"] is False
    assert fallback.retry_is_owed(world.session, kickoff=KICK) is False
    provider.state["fail"] = None
    retry = await _tick(world.session, on, T23)
    assert retry["odds_api_fallback"]["reason"] == "NO_GAME_IN_THE_CHECKPOINT_PHASE"
    assert len(provider.calls) == 1


def test_zero_charge_proof_requires_a_connection_failure_in_the_chain():
    import httpx

    assert fallback.zero_charge_proven(httpx.ConnectError("x")) is True
    assert fallback.zero_charge_proven(httpx.ConnectTimeout("x")) is True
    assert fallback.zero_charge_proven(httpx.ReadTimeout("x")) is False
    assert fallback.zero_charge_proven(RuntimeError("missing quota header")) is False