"""Owner decision A (2026-10-25): Odds API h2h captures at T-30, T-15 and in [T-5, T-2].

Offline: ``TheOddsAPIAdapter.get_market_snapshot`` is replaced by a fake that
records its call. Pinned here:

* each capture point is due only inside its window, and the offsets are settings;
* one claim per (sport, point, cluster): a second tick, a restart or a racing run
  never buys the same point twice; a FAILED request owes exactly one retry;
* a due capture spends ONE ledgered request on the sport's own capture endpoint,
  charged to the sport's lane, bound and persisted by the sport's own code, and
  it never counts as (or blocks) the sport's buy window;
* a guard refusal is returned and logged with its reason and spends nothing --
  no claim, no reservation, no request -- so the next tick may still capture;
* captures yield to today's still-to-come decision buys;
* the switch off means no request at all.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.data_sources.base import AdapterResponse, SourceEnvelope
from app.data_sources.odds import TheOddsAPIAdapter
from app.jobs.orchestrator import JobOrchestrator
from app.models import (
    Game, GameStatus, League, NflAcquisitionPhaseClaim, OddsSnapshot, ProviderRequestLedger, Sport,
)
from app.nhl import odds_api_price as nhl_odds
from app.services import odds_budget_lanes as lanes
from app.services import odds_close_captures as captures
from app.services.odds_request_window import window_requests
from tests.test_nhl_odds_api_source import _bare_game, _measured_balance, raw_event

OWNER = "close-capture-test-owner"
ON = {"odds_close_captures_enabled": True, "nhl_odds_api_enabled": True,
      "the_odds_api_key": "fixture-not-a-key",  # secret-guard: test-fixture
      "odds_collection_owner": OWNER, "odds_daily_request_budget": 10,
      "odds_nhl_lane_monthly_credits": 30, "odds_nhl_lane_daily_credits": 6,
      "nfl_odds_api_fallback_enabled": False}
NHL = captures.SPORTS_BY_CODE["NHL"]
MLB = captures.SPORTS_BY_CODE["MLB"]


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _league(session, code: str) -> League:
    league = session.scalar(select(League).where(League.code == code))
    if league is None:
        sport = Sport(code=f"S-{code}", name=code)
        session.add(sport)
        session.flush()
        league = League(sport_id=sport.id, code=code, name=code)
        session.add(league)
        session.flush()
    return league


def _game(session, code: str, start: datetime, *, source_mode: str = "LIVE",
          status: GameStatus = GameStatus.SCHEDULED) -> Game:
    game = Game(league_id=_league(session, code).id, starts_at=start, status=status, source_mode=source_mode)
    session.add(game)
    session.flush()
    return game


def _claims(session) -> list[NflAcquisitionPhaseClaim]:
    return list(session.scalars(select(NflAcquisitionPhaseClaim)))


def _ledger(session, endpoint: str = NHL.endpoint_key) -> list[ProviderRequestLedger]:
    return list(session.scalars(select(ProviderRequestLedger).where(ProviderRequestLedger.endpoint_key == endpoint)))


def _fake_snapshot(calls: list, raw: list[dict], *, fail: bool = False):
    async def fake(self):
        calls.append((self.sport_key, tuple(self.markets), self.regions))
        if fail:
            raise httpx.ConnectError("fixture: no network in tests")
        data = [TheOddsAPIAdapter._normalize_event(r).model_dump(mode="json") for r in raw]
        return AdapterResponse(envelope=SourceEnvelope(
            source="THE_ODDS_API", fetched_at=datetime.now(UTC), data=data,
            metadata={"event_count": len(raw), "sport_key": self.sport_key, "requests_remaining": 470,
                      "requests_used": 30, "requests_last": 1}), raw_payload=raw)
    return fake


# -- the plan ------------------------------------------------------------------------------------
@pytest.mark.parametrize("minutes, expected", [
    (31, []), (30, ["T30"]), (27, ["T30"]), (24.5, ["T30"]), (24, []), (16, []), (15, ["T15"]),
    (12, ["T15"]), (9.5, ["T15"]), (9, []), (6, []), (5, ["CLOSE"]), (3.5, ["CLOSE"]), (2, ["CLOSE"]),
    (1.5, []),
])
def test_each_point_is_due_only_inside_its_window(session, settings, minutes, expected):
    start = datetime(2026, 10, 26, 23, 0, tzinfo=UTC)
    _game(session, "NHL", start)
    due = captures.due_points(session, settings, NHL, now=start - timedelta(minutes=minutes))
    assert [d["point"] for d in due] == expected
    assert all(d["phase"] == captures.PHASE_PRIMARY for d in due)


def test_the_offsets_are_settings(session, settings):
    start = datetime(2026, 10, 26, 23, 0, tzinfo=UTC)
    _game(session, "NHL", start)
    moved = settings.model_copy(update={"odds_close_capture_t30_minutes": 40,
                                        "odds_close_capture_close_earliest_minutes": 4,
                                        "odds_close_capture_close_latest_minutes": 3})
    assert [d["point"] for d in captures.due_points(session, moved, NHL, now=start - timedelta(minutes=38))] == ["T30"]
    assert captures.due_points(session, moved, NHL, now=start - timedelta(minutes=30)) == []
    assert captures.due_points(session, moved, NHL, now=start - timedelta(minutes=5)) == []
    assert [d["point"] for d in captures.due_points(session, moved, NHL, now=start - timedelta(minutes=3))] == ["CLOSE"]


def test_a_cluster_is_every_game_at_one_start_and_mlb_counts_live_games_only(session, settings):
    start = datetime(2026, 10, 26, 23, 0, tzinfo=UTC)
    a, b = _game(session, "NHL", start), _game(session, "NHL", start)
    _game(session, "NHL", start, status=GameStatus.FINAL)
    later = _game(session, "NHL", start + timedelta(minutes=15))
    due = captures.due_points(session, settings, NHL, now=start - timedelta(minutes=15))
    assert [(d["point"], d["cluster_start"]) for d in due] == [
        ("T15", start.isoformat()), ("T30", (start + timedelta(minutes=15)).isoformat())]
    assert due[0]["game_ids"] == sorted([str(a.id), str(b.id)]) and due[1]["game_ids"] == [str(later.id)]
    _game(session, "MLB", start, source_mode="DEMO")
    assert captures.due_points(session, settings, MLB, now=start - timedelta(minutes=15)) == []
    _game(session, "MLB", start)
    assert len(captures.due_points(session, settings, MLB, now=start - timedelta(minutes=15))) == 1


def test_one_claim_per_cluster_and_point_and_one_retry_only_after_a_failure(session, settings):
    start = datetime(2026, 10, 26, 23, 0, tzinfo=UTC)
    _game(session, "NHL", start)
    session.commit()
    moment = start - timedelta(minutes=28)
    [entry] = captures.due_points(session, settings, NHL, now=moment)
    first = captures.claim(session, NHL, entry, invocation_id="a", invocation_path="SCHEDULED", now=moment)
    assert first is not None and first.lane == "close_capture:NHL:T30" and first.sport == "NHL"
    # A racing run (or a restart that re-planned before the claim was visible) loses at the database.
    assert captures.claim(session, NHL, entry, invocation_id="b", invocation_path="SCHEDULED", now=moment) is None
    assert captures.due_points(session, settings, NHL, now=moment) == []
    # Claimed but never reserved: no request was made, nothing is owed.
    assert not captures.retry_owed(session, sport="NHL", point="T30", cluster=start)
    reserved = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=NHL.endpoint_key, requested_at=moment,
                                     status="RESERVED", request_fingerprint="c" * 64)
    session.add(reserved)
    session.flush()
    first.reservation_id = reserved.id
    session.commit()
    assert not captures.retry_owed(session, sport="NHL", point="T30", cluster=start)     # in flight
    session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=NHL.endpoint_key, requested_at=moment,
                                      completed_at=moment, status="FAILED", request_fingerprint="c" * 64,
                                      reservation_id=reserved.id))
    session.commit()
    [retry] = captures.due_points(session, settings, NHL, now=moment + timedelta(minutes=1))
    assert retry["phase"] == captures.PHASE_RETRY
    assert captures.claim(session, NHL, retry, invocation_id="c", invocation_path="SCHEDULED", now=moment) is not None
    assert captures.due_points(session, settings, NHL, now=moment + timedelta(minutes=2)) == []


def test_capture_endpoints_charge_each_sports_own_lane_and_never_a_buy_endpoint():
    expected = {"MLB": lanes.LANE_MLB_REQUIRED, "NFL": lanes.LANE_NFL_RESEARCH, "CFB": lanes.LANE_CFB_RESEARCH,
                "NBA": lanes.LANE_NBA_RESEARCH, "NHL": lanes.LANE_NHL_RESEARCH}
    for spec in captures.SPORTS:
        assert lanes.league_of_endpoint(spec.endpoint_key) == spec.league_code
        assert lanes.lane_for_request(league_code=spec.league_code, markets=captures.MARKETS) == expected[spec.sport]
        assert spec.lane == expected[spec.sport]
        assert spec.endpoint_key not in {"baseball_mlb_odds", "americanfootball_nfl_odds",
                                         "americanfootball_nfl_fallback_odds", "americanfootball_ncaaf_odds",
                                         "basketball_nba_odds", nhl_odds.ENDPOINT_KEY}


# -- the collector -------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_switched_off_makes_no_request(session, settings, monkeypatch):
    calls: list = []
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", _fake_snapshot(calls, []))
    _bare_game(session, source_id="2026020240", home="TOR", away="MTL", start=_now() + timedelta(minutes=28))
    session.commit()
    off = settings.model_copy(update={**ON, "odds_close_captures_enabled": False})
    result = await JobOrchestrator(session, off).collect_odds_close_captures(_now().date())
    assert result == {"records": 0, "status": captures.DISABLED, "provider_called": False}
    assert calls == [] and _claims(session) == []


@pytest.mark.asyncio
async def test_a_due_capture_spends_one_ledgered_request_and_is_never_bought_twice(session, settings, monkeypatch):
    monkeypatch.setenv("ASP_ODDS_COLLECTION_OWNER", OWNER)
    now = _now()
    start = now + timedelta(minutes=28)
    game_id = _bare_game(session, source_id="2026020240", home="TOR", away="MTL", start=start)
    _measured_balance(session)
    session.commit()
    calls: list = []
    raw = [raw_event("FIXTURENHLCAP001", "Toronto Maple Leafs", "Montreal Canadiens", commence=start,
                     clock=now - timedelta(minutes=1))]
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", _fake_snapshot(calls, raw))
    orchestrator = JobOrchestrator(session, settings.model_copy(update=ON))

    result = await orchestrator.collect_odds_close_captures(now.date(), now=now)
    nhl = result["sports"]["NHL"]
    assert calls == [("icehockey_nhl", ("h2h",), "us")]
    assert nhl["status"] == captures.CAPTURED and nhl["bound_in_scope"] == 1 and nhl["created"] == 2
    assert [p["point"] for p in nhl["points"]] == ["T30"] and result["refusals"] == []
    ledger = _ledger(session)
    assert sorted(r.status for r in ledger) == ["RESERVED", "SUCCEEDED"]
    reserved = next(r for r in ledger if r.status == "RESERVED")
    shape = reserved.response_metadata["request_shape"]
    assert shape["trigger"] == "CLOSE_CAPTURE" and shape["markets"] == ["h2h"]
    assert shape["capture_points"] == [{"cluster_start": start.isoformat(), "point": "T30", "phase": "PRIMARY"}]
    assert lanes.lane_of_ledger_row(reserved, group=ledger) == lanes.LANE_NHL_RESEARCH
    [claim] = _claims(session)
    assert (claim.lane, claim.reservation_id) == ("close_capture:NHL:T30", reserved.id)
    snapshots = list(session.scalars(select(OddsSnapshot)))
    assert len(snapshots) == 2
    assert {s.raw_payload["ledger_reservation_id"] for s in snapshots} == {str(reserved.id)}
    assert {s.raw_payload["event_commence_time"] for s in snapshots} == {start.isoformat()}
    # The capture is not a buy: the NHL buy window still holds no request.
    assert window_requests(session, provider="THE_ODDS_API", endpoint_key=nhl_odds.ENDPOINT_KEY,
                           opened=start - timedelta(hours=2), now=now)["attempts"] == 0
    assert str(game_id) in shape["games"]

    # The next tick, a restart, or the same tick again: the point is claimed, nothing is bought.
    again = await JobOrchestrator(session, settings.model_copy(update=ON)).collect_odds_close_captures(
        now.date(), now=now + timedelta(seconds=40))
    assert again["sports"]["NHL"]["status"] == captures.NOT_DUE and len(calls) == 1
    assert len(_ledger(session)) == 2


@pytest.mark.asyncio
async def test_a_guard_refusal_is_recorded_with_its_reason_and_spends_nothing(session, settings, monkeypatch, caplog):
    monkeypatch.setenv("ASP_ODDS_COLLECTION_OWNER", OWNER)
    now = _now()
    start = now + timedelta(minutes=28)
    _bare_game(session, source_id="2026020240", home="TOR", away="MTL", start=start)
    _measured_balance(session)
    session.commit()
    calls: list = []
    raw = [raw_event("FIXTURENHLCAP001", "Toronto Maple Leafs", "Montreal Canadiens", commence=start,
                     clock=now - timedelta(minutes=1))]
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", _fake_snapshot(calls, raw))
    logged: list = []
    from app.jobs import orchestrator as orchestrator_module
    real_warning = orchestrator_module.logger.warning
    monkeypatch.setattr(orchestrator_module.logger, "warning",
                        lambda event, **kw: (logged.append((event, kw)), real_warning(event, **kw))[1])

    # The NHL lane unfunded: the existing lane guard refuses before any claim or request.
    unfunded = settings.model_copy(update={**ON, "odds_nhl_lane_monthly_credits": 0, "odds_nhl_lane_daily_credits": 0})
    result = await JobOrchestrator(session, unfunded).collect_odds_close_captures(now.date(), now=now)
    [refusal] = result["refusals"]
    assert refusal["sport"] == "NHL" and refusal["reason"] == captures.ODDS_LANE_BUDGET_REFUSED
    assert "NHL paper moneylines" in refusal["detail"] and refusal["points"][0]["point"] == "T30"
    assert ("odds_close_capture_refused", refusal) in logged
    assert calls == [] and _claims(session) == [] and _ledger(session) == []

    # The shared daily request budget spent: the existing quota policy refuses, by name.
    spent = settings.model_copy(update={**ON, "odds_daily_request_budget": 1})
    result = await JobOrchestrator(session, spent).collect_odds_close_captures(now.date(), now=now)
    assert result["refusals"][0]["reason"] == captures.ODDS_QUOTA_POLICY_REFUSED
    assert "Daily request budget" in result["refusals"][0]["detail"]
    assert calls == [] and _claims(session) == []

    # Nothing was spent, so the next tick inside the window still captures.
    result = await JobOrchestrator(session, settings.model_copy(update=ON)).collect_odds_close_captures(
        now.date(), now=now + timedelta(minutes=1))
    assert result["sports"]["NHL"]["status"] == captures.CAPTURED and len(calls) == 1


@pytest.mark.asyncio
async def test_a_sport_without_a_switched_on_lane_is_a_named_refusal(session, settings, monkeypatch):
    monkeypatch.setenv("ASP_ODDS_COLLECTION_OWNER", OWNER)
    calls: list = []
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", _fake_snapshot(calls, []))
    now = _now()
    _game(session, "NBA", now + timedelta(minutes=14))
    session.commit()
    result = await JobOrchestrator(session, settings.model_copy(update=ON)).collect_odds_close_captures(
        now.date(), now=now)
    assert [(r["sport"], r["reason"]) for r in result["refusals"]] == [
        ("NBA", captures.SPORT_ODDS_API_LANE_DISABLED)]
    assert calls == [] and _claims(session) == []


@pytest.mark.asyncio
async def test_a_capture_yields_to_todays_still_to_come_buys(session, settings, monkeypatch):
    monkeypatch.setenv("ASP_ODDS_COLLECTION_OWNER", OWNER)
    now = _now()
    start = now + timedelta(minutes=28)
    _bare_game(session, source_id="2026020240", home="TOR", away="MTL", start=start)
    _bare_game(session, source_id="2026020241", home="COL", away="DAL", start=now + timedelta(minutes=40))
    _bare_game(session, source_id="2026020242", home="EDM", away="CGY", start=now + timedelta(minutes=50))
    _measured_balance(session)
    session.commit()
    calls: list = []
    raw = [raw_event("FIXTURENHLCAP001", "Toronto Maple Leafs", "Montreal Canadiens", commence=start,
                     clock=now - timedelta(minutes=1))]
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", _fake_snapshot(calls, raw))
    tight = settings.model_copy(update={**ON, "odds_nhl_lane_daily_credits": 2})
    result = await JobOrchestrator(session, tight).collect_odds_close_captures(now.date(), now=now)
    [refusal] = result["refusals"]
    assert refusal["reason"] == captures.CAPTURE_YIELDS_TO_PENDING_BUYS
    assert "2 NHL start cluster(s) still to buy today" in refusal["detail"]
    assert calls == [] and _claims(session) == []
    # With room for both later buys and this capture, it captures.
    result = await JobOrchestrator(session, settings.model_copy(update=ON)).collect_odds_close_captures(
        now.date(), now=now)
    assert result["sports"]["NHL"]["status"] == captures.CAPTURED and len(calls) == 1


@pytest.mark.asyncio
async def test_a_failed_request_is_ledgered_failed_and_retried_once(session, settings, monkeypatch):
    monkeypatch.setenv("ASP_ODDS_COLLECTION_OWNER", OWNER)
    now = _now()
    _bare_game(session, source_id="2026020240", home="TOR", away="MTL", start=now + timedelta(minutes=28))
    _measured_balance(session)
    session.commit()
    calls: list = []
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", _fake_snapshot(calls, [], fail=True))
    orchestrator = JobOrchestrator(session, settings.model_copy(update=ON))
    first = await orchestrator.collect_odds_close_captures(now.date(), now=now)
    assert first["sports"]["NHL"]["status"] == captures.REQUEST_FAILED
    failed = [r for r in _ledger(session) if r.status == "FAILED"]
    assert len(failed) == 1 and failed[0].response_metadata["zero_charge_proven"] is True
    second = await orchestrator.collect_odds_close_captures(now.date(), now=now + timedelta(minutes=1))
    assert second["sports"]["NHL"]["status"] == captures.REQUEST_FAILED
    assert sorted(c.phase for c in _claims(session)) == ["PRIMARY", "RETRY"]
    third = await orchestrator.collect_odds_close_captures(now.date(), now=now + timedelta(minutes=2))
    assert third["sports"]["NHL"]["status"] == captures.NOT_DUE
    assert len(calls) == 2
    assert session.scalar(select(func.count()).select_from(ProviderRequestLedger).where(
        ProviderRequestLedger.endpoint_key == NHL.endpoint_key, ProviderRequestLedger.status == "RESERVED")) == 2


def test_the_scheduler_registers_the_capture_tick_on_its_own_lane_every_minute(settings):
    from apscheduler.triggers.interval import IntervalTrigger

    from app.jobs import lanes as job_lanes
    from app.jobs.scheduler import build_scheduler

    scheduler = build_scheduler(settings.model_copy(update={"scheduler_core_jobs_enabled": True}))
    job = scheduler.get_job("asp-oc-odds-close-captures")
    assert job is not None and job.kwargs["job_name"] == "collect-odds-close-captures"
    assert getattr(job.func, "__aisp_job_lane__") == job_lanes.ODDS_CLOSE_CAPTURE_LANE
    assert isinstance(job.trigger, IntervalTrigger) and job.trigger.interval == timedelta(minutes=1)
    assert (job.max_instances, job.coalesce) == (1, True)
    off = build_scheduler(settings.model_copy(update={"scheduler_core_jobs_enabled": True,
                                                      "odds_close_captures_enabled": False}))
    assert off.get_job("asp-oc-odds-close-captures") is None


def test_shipped_defaults_are_the_owner_decision(settings):
    assert settings.odds_close_captures_enabled is True
    assert (settings.odds_close_capture_t30_minutes, settings.odds_close_capture_t15_minutes,
            settings.odds_close_capture_close_earliest_minutes, settings.odds_close_capture_close_latest_minutes) == (
        30, 15, 5, 2)
    assert settings.last_chance_checkpoint_enabled is True
    assert settings.last_chance_checkpoint_minutes_before_start == 16
