"""The CFB SportsGameOdds adapter, offline, against LABELLED FIXTURES.

Nothing here is production evidence. The league id is ``FIXTURE_NCAAF`` and the
team ids are ``FIXTURE_*`` precisely so no reader can mistake them for the
provider's real identifiers, which AISP has never observed. The payload follows
the provider contract the MLB/NFL parser already enforces (exact oddIDs,
per-book ``lastUpdatedAt``, ``available``).
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.cfb import paper_decision, sgo_price
from app.data_sources.sgo_parser import parse_main_markets
from app.models import OddsSnapshot, OfficialAspPosition, ProviderRequestLedger
from tests.test_cfb_consumer_activation import _in_window, era, world  # noqa: F401 -- fixtures
from tests.test_cfb_production_discovery import KICK

FIXTURE_LEAGUE = "FIXTURE_NCAAF"
HOME, AWAY = "FIXTURE_HOME_10", "FIXTURE_AWAY_20"


def fixture_event(*, league=FIXTURE_LEAGUE, home=HOME, away=AWAY, starts=KICK, updated=None,
                  home_ml=+200, away_ml=-260, one_sided=False) -> dict:
    stamp = (updated or (KICK - timedelta(minutes=50))).isoformat().replace("+00:00", "Z")

    def odd(odd_id, opposing, bet, entity, side, books):
        return {"oddID": odd_id, "opposingOddID": opposing, "periodID": "game", "statID": "points",
                "betTypeID": bet, "statEntityID": entity, "sideID": side, "byBookmaker": books}

    def book(price, **extra):
        return {"draftkings": {"odds": f"{price:+d}", "lastUpdatedAt": stamp, "available": True, **extra}}

    odds = {
        "points-home-game-ml-home": odd("points-home-game-ml-home", "points-away-game-ml-away", "ml", "home", "home",
                                        book(home_ml)),
        "points-away-game-ml-away": odd("points-away-game-ml-away", "points-home-game-ml-home", "ml", "away", "away",
                                        {} if one_sided else book(away_ml)),
    }
    return {"success": True, "data": [{
        "eventID": "FIXTURE_EVENT_1", "leagueID": league, "sportID": "FOOTBALL",
        "teams": {"home": {"teamID": home, "names": {"long": "Fixture Home"}},
                  "away": {"teamID": away, "names": {"long": "Fixture Away"}}},
        "status": {"startsAt": starts.isoformat().replace("+00:00", "Z"), "started": False, "completed": False},
        "odds": odds}]}


@pytest.fixture
def team_map(tmp_path):
    path = tmp_path / "team-map.json"
    path.write_text(json.dumps({"schema": sgo_price.TEAM_MAP_SCHEMA, "leagueID": FIXTURE_LEAGUE, "entries": [
        {"sgoTeamID": HOME, "cfbdTeamId": 10, "reviewed": True, "evidence": "FIXTURE"},
        {"sgoTeamID": AWAY, "cfbdTeamId": 20, "reviewed": True, "evidence": "FIXTURE"}]}))
    return path


def _settings(world, team_map=None, league=FIXTURE_LEAGUE, enabled=True):
    world.settings.cfb_sgo_league_id = league
    world.settings.cfb_sgo_team_map_path = str(team_map) if team_map else ""
    world.settings.cfb_sgo_lane_enabled = enabled
    return world.settings


def _open(allowed):
    return lambda *_a, **_k: {"state": "CAPACITY_AVAILABLE" if allowed else "NO_UNCOMMITTED_SGO_CAPACITY",
                              "objects_allowed": allowed}


BUY_AT = KICK - timedelta(minutes=60)


def test_unresolved_league_identity_plans_nothing(world, era, monkeypatch):
    _in_window(world, era, monkeypatch)
    settings = _settings(world, league="")
    planned = sgo_price.plan(world.session, settings, now=BUY_AT, capacity_fn=_open(10))
    assert {g["state"] for g in planned["games"]} <= {"SGO_BINDING_UNRESOLVED", "UNTARGETABLE_SHARED_KICKOFF"}
    assert planned["will_request"] == []


def test_a_shared_kickoff_is_never_bought_without_a_team_target(world, era, monkeypatch):
    # Both fixture games kick off together, and nothing maps their teams.
    _in_window(world, era, monkeypatch)
    planned = sgo_price.plan(world.session, _settings(world), now=BUY_AT, capacity_fn=_open(10))
    assert {g["state"] for g in planned["games"]} == {"UNTARGETABLE_SHARED_KICKOFF"}


def test_a_reviewed_team_map_targets_one_event_with_exact_params(world, era, monkeypatch, team_map):
    _in_window(world, era, monkeypatch)
    planned = sgo_price.plan(world.session, _settings(world, team_map), now=BUY_AT, capacity_fn=_open(10))
    ready = [g for g in planned["games"] if g["state"] == "READY"]
    assert [g["source_game_id"] for g in ready] == ["401"]
    params = ready[0]["params"]
    assert params["leagueID"] == FIXTURE_LEAGUE and params["limit"] == "1" and params["teamID"] == HOME
    assert params["startsAfter"] < KICK.strftime("%Y-%m-%dT%H:%M:%SZ") < params["startsBefore"]


def test_zero_capacity_spends_nothing_and_never_calls_the_provider(world, era, monkeypatch, team_map):
    _in_window(world, era, monkeypatch)
    calls = []
    result = sgo_price.collect(world.session, _settings(world, team_map), now=BUY_AT, capacity_fn=_open(0),
                               fetch=lambda *a: calls.append(a) or fixture_event())
    assert result["status"] == "NO_UNCOMMITTED_SGO_CAPACITY" and result["objects_spent"] == 0 and calls == []
    assert world.session.scalar(select(func.count()).select_from(ProviderRequestLedger)) == 0


def test_a_disabled_lane_spends_nothing(world, era, monkeypatch, team_map):
    _in_window(world, era, monkeypatch)
    result = sgo_price.collect(world.session, _settings(world, team_map, enabled=False), now=BUY_AT,
                               capacity_fn=_open(10), fetch=lambda *a: pytest.fail("no request"))
    assert result == {"status": "LANE_DISABLED", "objects_spent": 0}


def _guards(monkeypatch):
    """The REAL guards: on the empty test ledger both have room, so they pass."""


def test_collect_reserves_parses_binds_persists_and_feeds_the_checkpoint(world, era, monkeypatch, team_map):
    _in_window(world, era, monkeypatch)
    _guards(monkeypatch)
    settings = _settings(world, team_map)
    result = sgo_price.collect(world.session, settings, now=BUY_AT, capacity_fn=_open(1),
                               fetch=lambda *_a: fixture_event())
    assert result["status"] == "COLLECTED" and result["objects_spent"] == 1
    request = result["requests"][0]
    assert request["persisted"]["created"] == 2 and "binding_refusals" not in request
    statuses = sorted(r.status for r in world.session.scalars(select(ProviderRequestLedger)))
    assert statuses == ["RESERVED", "SUCCEEDED"]
    assert {r.endpoint_key for r in world.session.scalars(select(ProviderRequestLedger))} == {sgo_price.LANE}
    # The checkpoint reads the stored pair exactly as it reads NFL's.
    decision = next(d for d in paper_decision.evaluate(world.session, world.settings,
                                                       now=KICK - timedelta(minutes=20))["decisions"]
                    if d["source_game_id"] == "401")
    assert decision["reason"] in {"ALL_GATES_PASSED", "NO_QUALIFYING_EDGE"}
    if decision["reason"] == "ALL_GATES_PASSED":
        position = world.session.get(OfficialAspPosition, uuid.UUID(decision["position_id"]))
        assert 0 < float(position.stake) <= 5


def test_a_second_run_does_not_buy_a_game_that_already_holds_a_price(world, era, monkeypatch, team_map):
    _in_window(world, era, monkeypatch)
    _guards(monkeypatch)
    settings = _settings(world, team_map)
    sgo_price.collect(world.session, settings, now=BUY_AT, capacity_fn=_open(1), fetch=lambda *_a: fixture_event())
    again = sgo_price.plan(world.session, settings, now=BUY_AT + timedelta(minutes=5), capacity_fn=_open(1))
    assert next(g for g in again["games"] if g["source_game_id"] == "401")["state"] == "PRICE_ALREADY_HELD"


@pytest.mark.parametrize("event,reason", [
    (dict(home=AWAY, away=HOME), "ORIENTATION_REVERSED"),
    (dict(home="FIXTURE_UNKNOWN_99"), "TEAM_NOT_IN_REVIEWED_MAP"),
    (dict(starts=KICK + timedelta(minutes=30)), "START_TIME_DISAGREES_WITH_ADMITTED_KICKOFF"),
])
def test_binding_refuses_anything_but_exact_identity(world, era, monkeypatch, team_map, event, reason):
    _in_window(world, era, monkeypatch)
    _guards(monkeypatch)
    result = sgo_price.collect(world.session, _settings(world, team_map), now=BUY_AT, capacity_fn=_open(1),
                               fetch=lambda *_a: fixture_event(**event))
    request = result["requests"][0]
    assert request["binding_refusals"][0]["reason"] == reason
    assert request["persisted"]["created"] == 0
    assert result["objects_spent"] == 1   # a charged object is still recorded
    assert world.session.scalar(select(func.count()).select_from(OddsSnapshot)) == 0


def test_the_parser_refuses_another_league_and_a_one_sided_market():
    now = KICK - timedelta(hours=1)
    wrong = parse_main_markets(fixture_event(league="FIXTURE_OTHER"), received_at=now, league_id=FIXTURE_LEAGUE)
    assert wrong.quotes == [] and wrong.rejected[0]["reason"] == "MARKET_IDENTITY_MISMATCH"
    one = parse_main_markets(fixture_event(one_sided=True), received_at=now, league_id=FIXTURE_LEAGUE)
    assert one.quotes == [] and one.rejected[0]["reason"] == "BOOKMAKER_PRICED_ONLY_ONE_SIDE"


def test_a_stale_older_leg_is_declined_at_the_checkpoint(world, era, monkeypatch, team_map):
    _in_window(world, era, monkeypatch)
    _guards(monkeypatch)
    sgo_price.collect(world.session, _settings(world, team_map), now=BUY_AT, capacity_fn=_open(1),
                      fetch=lambda *_a: fixture_event(updated=KICK - timedelta(hours=6)))
    decision = next(d for d in paper_decision.evaluate(world.session, world.settings,
                                                       now=KICK - timedelta(minutes=20))["decisions"]
                    if d["source_game_id"] == "401")
    assert decision["reason"] == "QUOTE_TOO_STALE"


def test_the_capacity_gate_honours_the_cap_and_the_authorization_window(session, monkeypatch):
    from app.cfb import sgo_lane
    from app.services import provider_allocation as pa
    from app.services import sgo_collection

    budget = SimpleNamespace(spendable=300, blocked_reason=None, reserve=400, objects_used=1800)
    monkeypatch.setattr(sgo_collection, "object_budget", lambda *a, **k: budget)
    monkeypatch.setattr(pa, "build_plan", lambda *a, **k: SimpleNamespace(as_dict=lambda: {
        "spendable": 400, "lanes": {}, "reserve_never_spent": 300, "used_this_month": 1800, "monthly_cap": 2500}))
    monkeypatch.setattr(pa, "daily_snapshot", lambda *a, **k: SimpleNamespace(
        demand={"shaping": {"month_projected_need": 250}}))
    settings = SimpleNamespace(cfb_sgo_lane_object_cap=10, cfb_sgo_lane_enabled=True, cfb_sgo_protected_headroom=10,
                               cfb_sgo_lane_authorized_until="2026-10-01T00:00:00+00:00")
    open_gate = sgo_lane.capacity(session, settings, now=KICK)
    # min(guards) 300 - MLB/NFL 250 - protected 10 = 40; the smaller of ten and 40.
    assert open_gate["uncommitted"] == 40 and open_gate["objects_allowed"] == 10
    # The owner's September case: four spare beyond MLB/NFL stays with MLB/NFL.
    monkeypatch.setattr(pa, "daily_snapshot", lambda *a, **k: SimpleNamespace(
        demand={"shaping": {"month_projected_need": 296}}))
    tight = sgo_lane.capacity(session, settings, now=KICK)
    assert tight["uncommitted"] == 0 and tight["objects_allowed"] == 0 and tight["state"] == "NO_UNCOMMITTED_SGO_CAPACITY"
    monkeypatch.setattr(pa, "daily_snapshot", lambda *a, **k: SimpleNamespace(
        demand={"shaping": {"month_projected_need": 250}}))
    monkeypatch.setattr(sgo_lane, "_lane_spent", lambda *_a: 10)
    assert sgo_lane.capacity(session, settings, now=KICK)["state"] == "ASSIGNMENT_CAP_REACHED"
    monkeypatch.setattr(sgo_lane, "_lane_spent", lambda *_a: 0)
    from datetime import datetime, UTC
    late = sgo_lane.capacity(session, settings, now=datetime(2026, 10, 1, 1, tzinfo=UTC))
    assert late["state"] == "AUTHORIZATION_EXPIRED" and late["objects_allowed"] == 0
