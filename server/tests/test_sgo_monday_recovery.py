"""Offline proof of the bounded Monday recovery price and account-use scope.

Only the provider boundary is synthetic. The successful collector test uses
real due-game discovery, allocation, independent ObjectBudget, phase claim,
reservation, parser, canonical mapping and SQLite quote ingestion. It neither
loads live credentials nor reaches any HTTP transport or production database.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.data_sources.sgo_parser import HOME_ODD_ID, AWAY_ODD_ID
from app.models import (
    Game, GameTeam, League, Market, MarketOutcome, OddsSnapshot,
    ProviderRequestLedger, ShadowForecast, ShadowForecastBundle, Sport, Team,
)
from app.services import provider_allocation as allocation
from app.services import sgo_collection, sgo_usage
from app.services import sgo_nfl_pregame as lane
from test_sgo_nfl_sunday_exception import cfg, ledger, record_usage, spend

GAME_ID = "5fdb1ee2-ea96-4c38-83e5-67a3b466f4ae"
MOMENT = datetime(2026, 10, 5, 23, 49, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 6, 0, 15, tzinfo=UTC)
READ_AT = MOMENT - timedelta(minutes=20)
DEADLINE = datetime(2026, 10, 6, 14, 45, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(self, *args, **kwargs):
        raise AssertionError("UNEXPECTED_NETWORK_CALL")
    monkeypatch.setattr(httpx.Client, "__init__", refuse)


@pytest.fixture
def game(session):
    sport = Sport(code="FOOTBALL", name="Football")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="NFL", name="NFL")
    session.add(league)
    session.flush()
    home = Team(league_id=league.id, name="New Orleans Saints", abbreviation="NO")
    away = Team(league_id=league.id, name="Atlanta Falcons", abbreviation="ATL")
    game = Game(id=uuid.UUID(GAME_ID), league_id=league.id, starts_at=KICKOFF,
                source_mode="RESEARCH_SHADOW")
    session.add_all([home, away, game])
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    bundle = ShadowForecastBundle(
        bundle_sha256="a" * 64, bundle_id="synthetic-mnf-seed",
        logical_uri="test://mnf", source_fixture_id="synthetic-only",
        slate_date=date(2026, 10, 5), league="NFL", sport="NFL",
        status="READY", generated_at=READ_AT, forecast_count=1,
        quote_count=0, candidate_count=0, evidence={},
    )
    session.add(bundle)
    session.flush()
    session.add(ShadowForecast(
        bundle_id=bundle.id, canonical_game_id=game.id,
        forecast_id="synthetic-mnf-current", snapshot_type="CURRENT",
        engine_id="NFL_M1_XGBOOST", engine_version="synthetic",
        model_artifact_sha256="b" * 64, simulator_id="synthetic",
        simulator_version="test", simulator_seed=1,
        event_id="2026_04_ATL_NO", source_event_id="2026_04_ATL_NO",
        event_date=date(2026, 10, 5), season=2026, week=4,
        home_team_id="NO", away_team_id="ATL", is_ready=True,
        readiness_score=1.0, readiness_threshold=0.5, status="READY",
        scheduled_start_utc=KICKOFF, available_as_of_utc=READ_AT,
        full_home_win_probability=0.5, full_away_win_probability=0.5,
        full_expected_home_score=20.0, full_expected_away_score=20.0,
        distributions={}, uncertainty={}, readiness_detail={},
    ))
    session.commit()
    return game


def _synthetic_event():
    updated = (MOMENT - timedelta(minutes=1)).isoformat()
    def side(odd_id, opposing, odds):
        return {
            "oddID": odd_id, "opposingOddID": opposing,
            "betTypeID": "ml", "periodID": "game", "marketName": "Moneyline",
            "byBookmaker": {"draftkings": {
                "odds": odds, "lastUpdatedAt": updated, "available": True,
            }},
        }
    return {"success": True, "data": [{
        "eventID": "synthetic-atl-no", "leagueID": "NFL",
        "sportID": "FOOTBALL", "type": "match",
        "status": {"started": False, "live": False, "completed": False,
                   "ended": False, "cancelled": False,
                   "startsAt": KICKOFF.isoformat()},
        "teams": {
            "home": {"teamID": "NO_NFL",
                     "names": {"long": "New Orleans Saints", "short": "NO"}},
            "away": {"teamID": "ATL_NFL",
                     "names": {"long": "Atlanta Falcons", "short": "ATL"}},
        },
        "odds": {
            HOME_ODD_ID: side(HOME_ODD_ID, AWAY_ODD_ID, "-118"),
            AWAY_ODD_ID: side(AWAY_ODD_ID, HOME_ODD_ID, "+102"),
        },
    }]}


def _provider_boundary(monkeypatch):
    calls = []
    def fetch(settings, **kwargs):
        calls.append(kwargs)
        parsed = lane.parse_events(_synthetic_event(), received_at=MOMENT,
                                   now=MOMENT, league_id="NFL")
        assert len(parsed.pairs) == 1
        return sgo_collection.FallbackFetch(
            requested_at=MOMENT, objects=1, pairs=parsed.pairs, parse=parsed)
    monkeypatch.setattr(lane, "fetch_nfl_pregame", fetch)
    return calls


def _nfl_reservations(session):
    return list(session.scalars(select(ProviderRequestLedger).where(
        ProviderRequestLedger.provider == allocation.PROVIDER,
        ProviderRequestLedger.endpoint_key == lane.LANE,
        ProviderRequestLedger.status == "RESERVED",
    )))


def test_current_local_shape_reaches_real_reservation_and_two_sided_quote(
        session, game, monkeypatch):
    record_usage(session, used=2141, at=READ_AT)
    before = sgo_collection.object_budget(session, now=MOMENT)
    assert (before.allowance, before.reserve, before.objects_used,
            before.spendable) == (2500, 400, 0, 2100)
    ordinary = allocation.request_objects(session, lane=lane.LANE, wanted=1, now=MOMENT)
    assert not ordinary.allowed
    calls = _provider_boundary(monkeypatch)
    result = lane.collect_nfl_pregame(session, cfg(), now=MOMENT)
    assert result["reason"] == "COLLECTED"
    assert result["objects"] == result["objects_reserved"] == 1
    assert result["games_selected"] == result["games_ingested"] == [GAME_ID]
    assert result["selection_reached_provider"] is True
    assert result["lane_halted"] is False
    assert len(calls) == 1 and calls[0]["limit"] == 1
    assert calls[0]["starts_after"] == KICKOFF - timedelta(seconds=1)
    reservation, = _nfl_reservations(session)
    assert reservation.quota_cost == 1
    meta = reservation.response_metadata
    assert meta["request_shape"]["filters"]["limit"] == "1"
    assert meta["owner_exception"]["service_date"] == "2026-10-05"
    assert meta["owner_exception"]["game_ids"] == [GAME_ID]
    assert meta["owner_exception"]["objects"] == 1
    terminal = session.scalar(select(ProviderRequestLedger).where(
        ProviderRequestLedger.reservation_id == reservation.id,
        ProviderRequestLedger.status == "SUCCEEDED"))
    assert terminal is not None and terminal.records_received == 1
    quotes = session.execute(select(Market.game_id, MarketOutcome.outcome_key,
                                    OddsSnapshot.source)
        .join(MarketOutcome, MarketOutcome.market_id == Market.id)
        .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)).all()
    assert len(quotes) == 2
    assert {row.game_id for row in quotes} == {uuid.UUID(GAME_ID)}
    assert {row.outcome_key for row in quotes} == {"HOME:None", "AWAY:None"}
    assert {row.source for row in quotes} == {"SPORTSGAMEODDS"}
    after = sgo_collection.object_budget(session, now=MOMENT)
    assert after.reserve == 400 and after.objects_used == 1
    assert not allocation.request_objects(
        session, lane=lane.LANE, wanted=1, now=MOMENT, selection=[GAME_ID]).allowed
    second = lane.collect_nfl_pregame(session, cfg(), now=MOMENT + timedelta(seconds=30))
    assert second["attempted"] is False
    assert len(calls) == len(_nfl_reservations(session)) == 1


@pytest.mark.parametrize("barrier", ["floor", "unresolved", "post_read_charge", "unknown_charge"])
def test_provider_reconciliation_refuses_without_a_fetch(session, game, monkeypatch, barrier):
    record_usage(session, used=2142 if barrier == "floor" else 2141, at=READ_AT)
    if barrier == "unresolved":
        ledger(session, lane_key="sgo_mlb_events", reserved_at=READ_AT - timedelta(days=1),
               completed_at=None, quota_cost=1)
    elif barrier in {"post_read_charge", "unknown_charge"}:
        ledger(session, lane_key="sgo_mlb_events",
               reserved_at=READ_AT - timedelta(minutes=1),
               completed_at=READ_AT + timedelta(minutes=1),
               records=1 if barrier == "post_read_charge" else None, quota_cost=1)
    calls = _provider_boundary(monkeypatch)
    result = lane.collect_nfl_pregame(session, cfg(), now=MOMENT)
    assert result["attempted"] is False
    assert result["reason"] == "ALLOCATION_BUDGET_PAUSED_UNTIL_REPLENISHMENT"
    assert calls == [] and _nfl_reservations(session) == []


@pytest.mark.parametrize("unknown", [False, True])
def test_independent_local_400_reserve_still_blocks_exception(
        session, game, monkeypatch, unknown):
    at = READ_AT - timedelta(hours=1)
    spend(session, lane_key="sgo_mlb_events",
          objects=2081 if unknown else 2100, at=at)
    if unknown:
        ledger(session, lane_key="sgo_mlb_events", reserved_at=at,
               completed_at=at, records=None, quota_cost=1)
    record_usage(session, used=2141, at=READ_AT)
    grant = allocation.request_objects(
        session, lane=lane.LANE, wanted=1, now=MOMENT, selection=[GAME_ID])
    assert grant.allowed
    budget = sgo_collection.object_budget(session, now=MOMENT)
    assert budget.reserve == 400 and budget.spendable == 0
    assert budget.objects_used == (2101 if unknown else 2100)
    calls = _provider_boundary(monkeypatch)
    result = lane.collect_nfl_pregame(session, cfg(), now=MOMENT)
    assert result["reason"] == "OBJECT_RESERVE_GUARD"
    assert calls == [] and _nfl_reservations(session) == []


def test_unchanged_october_150_cap_stops_the_collector(session, game, monkeypatch):
    spend(session, lane_key=lane.LANE, objects=150, at=READ_AT - timedelta(days=1))
    record_usage(session, used=2141, at=READ_AT)
    calls = _provider_boundary(monkeypatch)
    result = lane.collect_nfl_pregame(session, cfg(), now=MOMENT)
    assert result["reason"] == "NFL_LANE_RENEWAL_CAP_REACHED"
    assert result["renewal_cap"] == 150 and calls == []
    assert len(_nfl_reservations(session)) == 1  # the fixture's previous purchase only


@pytest.mark.parametrize("change", ["wrong_game", "other_lane", "previous_day", "next_day", "no_selection"])
def test_named_monday_scope_does_not_generalize(session, change):
    record_usage(session, used=2141, at=READ_AT)
    kwargs = dict(lane=lane.LANE, wanted=1, now=MOMENT, selection=[GAME_ID])
    if change == "wrong_game":
        kwargs["selection"] = [str(uuid.UUID(int=123))]
    elif change == "other_lane":
        kwargs["lane"] = "sgo_mlb_events"
    elif change == "previous_day":
        kwargs["now"] = MOMENT - timedelta(days=1)
    elif change == "next_day":
        kwargs["now"] = MOMENT + timedelta(days=1)
    else:
        kwargs.pop("selection")
    assert not allocation.request_objects(session, **kwargs).allowed


@pytest.mark.parametrize("offset,retime", [(-10, True), (4, True), (0, False)])
def test_phase_and_retime_guards_remain_in_force(
        session, game, monkeypatch, offset, retime):
    record_usage(session, used=2141, at=READ_AT)
    calls = _provider_boundary(monkeypatch)
    result = lane.collect_nfl_pregame(
        session, cfg(nfl_late_checkpoint_retime_enabled=retime),
        now=MOMENT + timedelta(minutes=offset))
    assert result["attempted"] is False
    assert calls == [] and _nfl_reservations(session) == []


def _set_clock(monkeypatch, moment):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is not None else moment.replace(tzinfo=None)
    monkeypatch.setattr(sgo_usage, "datetime", Clock)


def _usage_payload():
    return {"data": {"tier": "amateur", "isActive": True, "rateLimits": {
        "per-month": {"max-entities": 2500, "current-entities": 2141},
        "per-day": {"max-entities": 3000000, "current-entities": 0},
        "per-minute": {"max-requests": 10},
    }}}


def test_account_read_before_deadline_records_only_safe_zero_object_usage(session, monkeypatch):
    _set_clock(monkeypatch, DEADLINE - timedelta(seconds=1))
    calls = []
    class OfflineClient:
        def __init__(self, **kwargs):
            assert 0 < kwargs["timeout"] <= 1
            assert kwargs["follow_redirects"] is False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, headers):
            assert url == "https://sgo.invalid/v2/account/usage"
            assert headers["X-Api-Key"] == "test-key-not-real"
            calls.append(url)
            class Response:
                status_code = 200
                def json(self):
                    return _usage_payload()
            return Response()
    monkeypatch.setattr(sgo_usage.httpx, "Client", OfflineClient)
    result = sgo_usage.refresh_usage(session, cfg())
    assert result["read"] is True and result["remaining"] == 359
    assert calls == ["https://sgo.invalid/v2/account/usage"]
    rows = list(session.scalars(select(ProviderRequestLedger)))
    assert len(rows) == 2 and all(row.quota_cost == 0 for row in rows)
    assert all("test-key-not-real" not in str(row.response_metadata) for row in rows)


@pytest.mark.parametrize("actual,supplied", [
    (DEADLINE, None),
    (DEADLINE + timedelta(seconds=1), None),
    (DEADLINE, MOMENT),  # backdating the ledger timestamp cannot extend authority
    (MOMENT, DEADLINE),  # neither can a future caller timestamp qualify
])
def test_account_expiry_prevents_fetch_and_ledger_write(
        session, monkeypatch, actual, supplied):
    _set_clock(monkeypatch, actual)
    calls = []
    def forbidden(settings):
        calls.append(True)
        raise AssertionError("EXPIRED_FETCH_CALLED")
    result = sgo_usage.refresh_usage(session, cfg(), now=supplied, fetch=forbidden)
    assert result["read"] is False and result["records"] == 0
    assert "SGO_ACCOUNT_USAGE_AUTHORITY_EXPIRED" in result["reason"]
    assert calls == [] and session.query(ProviderRequestLedger).count() == 0


def test_direct_account_fetch_cannot_bypass_expiry(monkeypatch):
    _set_clock(monkeypatch, DEADLINE)
    with pytest.raises(sgo_usage.UsageUnavailableError, match="AUTHORITY_EXPIRED"):
        sgo_usage.fetch_usage(cfg())


def test_one_named_game_never_grants_more_than_one_object(session):
    record_usage(session, used=2141, at=READ_AT)
    grant = allocation.request_objects(
        session, lane=lane.LANE, wanted=2, now=MOMENT, selection=[GAME_ID])
    assert grant.allowed and grant.granted == 1


def test_account_deadline_is_rechecked_immediately_before_get(monkeypatch):
    _set_clock(monkeypatch, DEADLINE - timedelta(seconds=1))
    calls = []
    class OfflineClient:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            _set_clock(monkeypatch, DEADLINE)
            return self
        def __exit__(self, *args):
            pass
        def get(self, *args, **kwargs):
            calls.append(True)
            raise AssertionError("EXPIRED_HTTP_GET")
    monkeypatch.setattr(sgo_usage.httpx, "Client", OfflineClient)
    with pytest.raises(sgo_usage.UsageUnavailableError, match="AUTHORITY_EXPIRED"):
        sgo_usage.fetch_usage(cfg())
    assert calls == []


@pytest.mark.parametrize("provider_reset", [False, True])
def test_zero_charge_primary_consumes_request_and_blocks_existing_retry(
        session, game, monkeypatch, provider_reset):
    record_usage(session, used=2141, at=READ_AT)
    calls = []
    def failed(settings, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("synthetic zero-charge primary failure")
    monkeypatch.setattr(lane, "fetch_nfl_pregame", failed)
    primary = lane.collect_nfl_pregame(session, cfg(), now=MOMENT)
    assert primary["reason"] == "REQUEST_FAILED" and len(calls) == 1
    reservation, = _nfl_reservations(session)
    terminal = session.scalar(select(ProviderRequestLedger).where(
        ProviderRequestLedger.reservation_id == reservation.id))
    assert terminal.status == "FAILED" and terminal.records_received == 0
    retry_at = MOMENT + timedelta(minutes=3)
    assert lane._retry_is_owed(session, kickoff=KICKOFF)
    if provider_reset:
        record_usage(session, used=0, at=retry_at - timedelta(seconds=1))
    direct = allocation.request_objects(
        session, lane=lane.LANE, wanted=1, now=retry_at, selection=[GAME_ID])
    assert not direct.allowed and direct.reason == "OWNER_EXCEPTION_REQUEST_LIMIT_REACHED"
    retry = lane.collect_nfl_pregame(session, cfg(), now=retry_at)
    assert retry["attempted"] is False
    assert retry["reason"] == "ALLOCATION_OWNER_EXCEPTION_REQUEST_LIMIT_REACHED"
    assert len(calls) == len(_nfl_reservations(session)) == 1


@pytest.mark.parametrize("terminal_status", [None, "CANCELED", "FAILED", "SUCCEEDED"])
def test_any_reserved_object_stays_consumed_regardless_of_terminal_status(
        session, terminal_status):
    record_usage(session, used=2141, at=READ_AT)
    at = MOMENT - timedelta(seconds=15)
    ledger(session, lane_key=lane.LANE, reserved_at=at, quota_cost=1,
           completed_at=None if terminal_status is None else at,
           # Cancellation is a FAILED terminal in the actual three-state schema.
           status="FAILED" if terminal_status == "CANCELED" else terminal_status or "SUCCEEDED",
           records=0, metadata={"synthetic_cancellation": True} if terminal_status == "CANCELED" else None)
    grant = allocation.request_objects(
        session, lane=lane.LANE, wanted=1, now=MOMENT, selection=[GAME_ID])
    assert not grant.allowed and grant.reason == "OWNER_EXCEPTION_REQUEST_LIMIT_REACHED"


def test_committed_primary_prevents_a_second_session_from_drawing_or_fetching(
        session, game, monkeypatch):
    from sqlalchemy.orm import Session
    record_usage(session, used=2141, at=READ_AT)
    calls = []
    def fetch(settings, **kwargs):
        calls.append(kwargs)
        # This interleaving is after the first atomic claim and reservation
        # commit, before its provider response. Another session sees the unit
        # as consumed even though no quote or terminal charge yet exists.
        with Session(session.get_bind()) as contender:
            grant = allocation.request_objects(
                contender, lane=lane.LANE, wanted=1, now=MOMENT, selection=[GAME_ID])
            assert not grant.allowed
            assert grant.reason == "OWNER_EXCEPTION_REQUEST_LIMIT_REACHED"
            competing = lane.collect_nfl_pregame(contender, cfg(), now=MOMENT)
            assert competing["attempted"] is False
        parsed = lane.parse_events(_synthetic_event(), received_at=MOMENT,
                                   now=MOMENT, league_id="NFL")
        return sgo_collection.FallbackFetch(
            requested_at=MOMENT, objects=1, pairs=parsed.pairs, parse=parsed)
    monkeypatch.setattr(lane, "fetch_nfl_pregame", fetch)
    result = lane.collect_nfl_pregame(session, cfg(), now=MOMENT)
    assert result["reason"] == "COLLECTED" and result["objects"] == 1
    assert len(calls) == len(_nfl_reservations(session)) == 1
