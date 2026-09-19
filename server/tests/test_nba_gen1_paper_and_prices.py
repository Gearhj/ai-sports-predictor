"""The NBA paper checkpoint and the NBA SportsGameOdds adapter, offline, against SYNTHETIC fixtures.

Nothing here is production evidence. The league id is ``FIXTURE_NBA`` and the
team ids are ``FIXTURE_*`` so no reader can mistake them for the provider's
real identifiers, which AISP has never observed. No test makes a request: every
provider call is an injected function that returns fixture bytes, and the lane's
shipped defaults are asserted to spend nothing.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    CollectionLaneHalt, OddsSnapshot, OfficialAspPosition, ProviderRequestLedger, ShadowExperiment,
    ShadowExperimentDecision,
)
from app.nba import gen1_paper, gen1_production, sgo_price
from app.services.edge_metrics import edge_metrics
from tests import nba_gen1_fixtures as fx
from tests.test_nba_gen1_consumer import world  # noqa: F401 -- fixture

LEAGUE = "FIXTURE_NBA"
HOME, AWAY = "FIXTURE_NBA_HOME", "FIXTURE_NBA_AWAY"
DECIDE_AT = fx.TIPOFF - timedelta(minutes=20)
BUY_AT = fx.TIPOFF - timedelta(minutes=60)


@pytest.fixture
def era(session):
    experiment = ShadowExperiment(
        name="NBA PAPER TEST", version=9101, started_at=DECIDE_AT - timedelta(days=30), start_date=date(2026, 9, 15),
        starting_bankroll=1000, sport="NBA", recommendation_policy_version="AISP_NBA_PAPER_POLICY_V1",
        staking_policy_version="AISP_QUARTER_KELLY_NBA_V1", decision_policy="AISP_NBA_PAPER_POLICY_V1",
        controlling_engine="NBA_GEN1_JOINT_BASELINE", supported_markets=["MONEYLINE"], kelly_fraction=0.25,
        unit_percentage=0.01, max_position_percentage=0.005, max_same_game_percentage=0.01,
        max_daily_percentage=0.02, code_commit="test", code_identity="test", is_active=True)
    session.add(experiment)
    session.flush()
    return experiment


def _eligible_game(session, world, *, lease_minutes=10, confirmed=True):
    """A V2 package with a live lease at DECIDE_AT, imported; orientation optionally CONFIRMED."""

    package = fx.make_package(world["root"], world["handoff"], schema="V2", created="2026-10-20T22:00:00+00:00")
    fx.write_v2_discovery(world["root"], world["handoff"], [(package, {
        "committedAtUtc": "2026-10-20T22:05:00+00:00", "currentlyEligible": True,
        "eligibilityValidUntilUtc": (DECIDE_AT + timedelta(minutes=lease_minutes)).isoformat()})],
        created="2026-10-20T22:05:00+00:00")
    gen1_production.consume(session, world["settings"], now=DECIDE_AT - timedelta(minutes=5))
    from app.models import NbaGen1Forecast

    row = session.scalar(select(NbaGen1Forecast))
    if confirmed:
        row.detail = {**(row.detail or {}), "orientation": {"state": "CONFIRMED"}}
        session.flush()
    return row


def _permit(monkeypatch, *, eligible=True):
    monkeypatch.setattr(gen1_paper, "_supplement_published", lambda *_a: DECIDE_AT - timedelta(hours=2))
    monkeypatch.setattr(gen1_paper, "eligibility_status", lambda *a, **k: (
        {"eligible": True, "state": "ELIGIBLE", "record_id": "rec", "contract": "c", "experiment_key": "k"}
        if eligible else {"eligible": False, "state": "AWAITING_PRODUCER_ELIGIBILITY", "detail": "none"}))


def _grant(tmp_path, *, until="2026-11-01T00:00:00+00:00", **override):
    document = {"schema": gen1_paper.GRANT_SCHEMA, "approvedBy": "James", "policyVersion": "AISP_NBA_PAPER_POLICY_V1",
                "realMoneyAuthorized": False, "policyAuthorization": {"mode": "VIRTUAL_RESEARCH_ONLY"},
                "modelConfiguration": fx.LATTICE, "validUntilUtc": until, "scope": "FIXTURE", **override}
    path = tmp_path / "grant.json"
    path.write_bytes(fx.canonical(document))
    return {"nba_paper_policy_grant_path": str(path), "nba_paper_policy_grant_sha256": fx.sha(path.read_bytes())}


# ------------------------------------------------------------ checkpoint ----

def test_without_an_nba_experiment_the_checkpoint_reports_it(session, settings):
    assert gen1_paper.evaluate(session, settings, now=DECIDE_AT)["reason"] == "NBA_PAPER_POLICY_IS_NOT_ACTIVE"


def test_a_v1_package_is_blocked_because_v1_discovery_has_no_lease(session, world, era):
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    result = gen1_paper.evaluate(session, world["settings"], now=DECIDE_AT)
    decision = result["decisions"][0]
    assert decision["outcome"] == "BLOCKED" and decision["reason"] == "DISCOVERY_NOT_CURRENT"
    assert "NO_LEASE_IN_SCHEMA" in decision["detail"]
    stored = session.scalar(select(ShadowExperimentDecision))
    assert stored.sport == "NBA" and stored.nba_gen1_forecast_id is not None and stored.selection == "NONE"
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 0
    # The decision records all three channels, labelled; only the joint one controls.
    assert decision["channels"]["JOINT_DISTRIBUTION"]["role"].startswith("controlling")
    assert decision["channels"]["RIDGE_POINTS"]["role"].startswith("points only")


def test_an_unconfirmed_orientation_blocks(session, world, era, monkeypatch):
    _eligible_game(session, world, confirmed=False)
    decision = gen1_paper.evaluate(session, world["settings"], now=DECIDE_AT)["decisions"][0]
    assert decision["reason"] == "ORIENTATION_NOT_CONFIRMED"


def test_no_owner_grant_blocks_before_any_price_is_read(session, world, era, monkeypatch):
    _eligible_game(session, world)
    _permit(monkeypatch)
    monkeypatch.setattr("app.services.nfl_paper_decision.paired_quotes",
                        lambda *a: pytest.fail("no price may be read without the owner's grant"))
    decision = gen1_paper.evaluate(session, world["settings"], now=DECIDE_AT)["decisions"][0]
    assert decision["reason"] == "NO_OWNER_POLICY_EXTENSION" and "NO_GRANT_PINNED" in decision["detail"]


@pytest.mark.parametrize("override, state", [
    ({"approvedBy": "Someone"}, "GRANT_DOES_NOT_AUTHORIZE_THE_UNCHANGED_VIRTUAL_POLICY"),
    ({"realMoneyAuthorized": True}, "GRANT_DOES_NOT_AUTHORIZE_THE_UNCHANGED_VIRTUAL_POLICY"),
    ({"validUntilUtc": "2026-10-01T00:00:00+00:00"}, "GRANT_EXPIRED_OR_UNBOUNDED"),
])
def test_a_grant_must_be_the_owners_unchanged_virtual_policy_and_unexpired(settings, tmp_path, override, state):
    configured = settings.model_copy(update=_grant(tmp_path, **override))
    assert gen1_paper.policy_grant(configured, now=DECIDE_AT)["state"] == state


def _price(session, game_id, *, home_ml=-150, away_ml=+130, at=BUY_AT):
    """Persist a two-sided SGO moneyline through the adapter's own persistence."""

    from app.data_sources.sgo_parser import parse_main_markets

    binding = sgo_price.Binding(league_id=LEAGUE, team_map={HOME: "nba-neil:franchise:FXH",
                                                            AWAY: "nba-neil:franchise:FXA"})
    parsed = parse_main_markets(event(home_ml=home_ml, away_ml=away_ml, updated=at - timedelta(minutes=2)),
                                received_at=at, now=at, league_id=LEAGUE)
    bound = [q for q in parsed.quotes if sgo_price.bind_quote_event(session, game_id, q, binding)[0]]
    return sgo_price.persist(session, game_id, bound, available_as_of=at)


def test_every_gate_passes_but_the_activation_control_is_off(session, world, era, monkeypatch, tmp_path):
    row = _eligible_game(session, world)
    _permit(monkeypatch)
    _price(session, row.canonical_game_id, home_ml=+150, away_ml=-170)
    configured = world["settings"].model_copy(update=_grant(tmp_path))
    decision = gen1_paper.evaluate(session, configured, now=DECIDE_AT)["decisions"][0]
    assert decision["reason"] in {"ACTIVATION_CONTROL_OFF", "NO_QUALIFYING_EDGE"}
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 0


def test_with_the_owners_grant_and_switch_a_qualifying_edge_positions_within_the_caps(
        session, world, era, monkeypatch, tmp_path):
    row = _eligible_game(session, world)
    _permit(monkeypatch)
    # Price the home side far longer than the joint probability implies.
    p = row.joint_home_win_probability
    _price(session, row.canonical_game_id, home_ml=+250 if p > 0.4 else +500, away_ml=-300)
    configured = world["settings"].model_copy(update={**_grant(tmp_path), "nba_paper_decisions_enabled": True})
    decision = gen1_paper.evaluate(session, configured, now=DECIDE_AT)["decisions"][0]
    assert decision["reason"] == "ALL_GATES_PASSED", decision
    assert decision["selection"]["channel"] == "JOINT_DISTRIBUTION"
    position = session.get(OfficialAspPosition, uuid.UUID(decision["position_id"]))
    assert 0 < float(position.stake) <= 5.0
    metrics = edge_metrics("NBA", decision["selection"]["american_odds"], decision["selection"]["win_probability"],
                           None, None, None, decision)
    assert metrics["policy"] == "AISP_NBA_PAPER_POLICY_V1" and all(t["passed"] for t in metrics["tests"])


# ---------------------------------------------------------------- prices ----

def event(*, league=LEAGUE, home=HOME, away=AWAY, starts=fx.TIPOFF, updated=None, home_ml=-150, away_ml=+130,
          event_id="FIXTURE_NBA_EVENT_1", one_sided=False) -> dict:
    stamp = (updated or (fx.TIPOFF - timedelta(minutes=50))).isoformat().replace("+00:00", "Z")

    def odd(odd_id, opposing, bet, entity, side, books):
        return {"oddID": odd_id, "opposingOddID": opposing, "periodID": "game", "statID": "points",
                "betTypeID": bet, "statEntityID": entity, "sideID": side, "byBookmaker": books}

    def book(price):
        return {"draftkings": {"odds": f"{price:+d}", "lastUpdatedAt": stamp, "available": True}}

    odds = {"points-home-game-ml-home": odd("points-home-game-ml-home", "points-away-game-ml-away", "ml", "home",
                                            "home", book(home_ml)),
            "points-away-game-ml-away": odd("points-away-game-ml-away", "points-home-game-ml-home", "ml", "away",
                                            "away", {} if one_sided else book(away_ml))}
    return {"success": True, "data": [{
        "eventID": event_id, "leagueID": league, "sportID": "BASKETBALL",
        "teams": {"home": {"teamID": home, "names": {"long": "Fixture Home"}},
                  "away": {"teamID": away, "names": {"long": "Fixture Away"}}},
        "status": {"startsAt": starts.isoformat().replace("+00:00", "Z"), "started": False, "completed": False},
        "odds": odds}]}


@pytest.fixture
def team_map(tmp_path):
    path = tmp_path / "nba-team-map.json"
    path.write_text(json.dumps({"schema": sgo_price.TEAM_MAP_SCHEMA, "leagueID": LEAGUE, "entries": [
        {"sgoTeamID": HOME, "franchiseId": "nba-neil:franchise:FXH", "reviewed": True, "evidence": "FIXTURE"},
        {"sgoTeamID": AWAY, "franchiseId": "nba-neil:franchise:FXA", "reviewed": True, "evidence": "FIXTURE"}]}))
    return path


def _lane(world, team_map=None, *, league=LEAGUE, enabled=True, cap=2):
    return world["settings"].model_copy(update={
        "nba_sgo_lane_enabled": enabled, "nba_sgo_league_id": league,
        "nba_sgo_team_map_path": str(team_map) if team_map else "", "nba_sgo_lane_object_cap": cap,
        "nba_sgo_lane_authorized_until": "2026-11-01T00:00:00+00:00"})


def _open(allowed):
    return lambda *_a, **_k: {"state": "CAPACITY_AVAILABLE" if allowed else "NO_GUARD_CAPACITY",
                              "objects_allowed": allowed}


def test_the_shipped_defaults_spend_nothing_and_say_why(session, settings):
    assert settings.nba_sgo_lane_enabled is False and settings.nba_sgo_lane_object_cap == 0
    assert sgo_price.capacity(session, settings)["state"] == "LANE_DISABLED"
    result = sgo_price.collect(session, settings, fetch=lambda *a: pytest.fail("no request"))
    assert result["status"] == "LANE_DISABLED" and result["provider_requests"] == 0
    enabled = settings.model_copy(update={"nba_sgo_lane_enabled": True})
    assert sgo_price.capacity(session, enabled)["state"] == "NO_AUTHORITY_WINDOW"
    windowed = enabled.model_copy(update={"nba_sgo_lane_authorized_until": "2026-11-01T00:00:00+00:00"})
    assert sgo_price.capacity(session, windowed, now=BUY_AT)["state"] == "NO_OBJECTS_ALLOCATED"
    assert sgo_price.load_binding(settings).problems == ["NBA_SGO_LEAGUE_ID_UNRESOLVED"]


def test_an_unresolved_league_plans_nothing(session, world):
    _eligible_game(session, world, lease_minutes=90)
    planned = sgo_price.plan(session, _lane(world, league=""), now=BUY_AT, capacity_fn=_open(5))
    assert {g["state"] for g in planned["games"]} <= {"SGO_BINDING_UNRESOLVED", "UNTARGETABLE_SHARED_TIPOFF"}
    assert planned["will_request"] == []


def test_a_reviewed_team_map_targets_one_event_with_exact_params(session, world, team_map):
    _eligible_game(session, world, lease_minutes=90)
    planned = sgo_price.plan(session, _lane(world, team_map), now=BUY_AT, capacity_fn=_open(5))
    ready = [g for g in planned["games"] if g["state"] == "READY"]
    assert len(ready) == 1 and ready[0]["params"]["teamID"] == HOME and ready[0]["params"]["limit"] == "1"


def test_collect_reserves_archives_binds_and_keeps_each_legs_own_clock(session, world, team_map, monkeypatch):
    row = _eligible_game(session, world, lease_minutes=90)
    archived = []
    monkeypatch.setattr("app.services.sgo_raw_archive.archive_response",
                        lambda settings, body, **kw: archived.append((body, kw)) or {"status": "ARCHIVED",
                                                                                     "sha256": fx.sha(body)})
    home_clock = BUY_AT - timedelta(minutes=3)
    payload = event()
    payload["data"][0]["odds"]["points-home-game-ml-home"]["byBookmaker"]["draftkings"]["lastUpdatedAt"] = \
        home_clock.isoformat().replace("+00:00", "Z")
    body = json.dumps(payload).encode()
    result = sgo_price.collect(session, _lane(world, team_map), now=BUY_AT, capacity_fn=_open(1),
                               fetch=lambda *_a: body)
    assert result["status"] == "COLLECTED" and result["objects_spent"] == 1 and result["provider_requests"] == 1
    assert archived and archived[0][0] == body and archived[0][1]["lane"] == sgo_price.LANE
    statuses = sorted(r.status for r in session.scalars(select(ProviderRequestLedger)))
    assert statuses == ["RESERVED", "SUCCEEDED"]
    clocks = {s.raw_payload["side_id"]: s.observed_at for s in session.scalars(select(OddsSnapshot))}
    assert clocks["home"].replace(tzinfo=UTC) == home_clock
    assert clocks["away"].replace(tzinfo=UTC) != home_clock  # each leg keeps its own book clock
    assert session.scalar(select(CollectionLaneHalt)) is None
    from app.services.nfl_paper_decision import paired_quotes

    pair = paired_quotes(session, row.canonical_game_id)[0]
    assert pair["source"] == "SPORTSGAMEODDS"


def test_an_unselected_event_in_the_response_latches_the_lane_halt(session, world, team_map, monkeypatch):
    _eligible_game(session, world, lease_minutes=90)
    monkeypatch.setattr("app.services.sgo_raw_archive.archive_response", lambda *a, **k: {"status": "ARCHIVED"})
    stranger = event(home="FIXTURE_OTHER_HOME", away="FIXTURE_OTHER_AWAY", event_id="FIXTURE_NBA_EVENT_9")
    result = sgo_price.collect(session, _lane(world, team_map), now=BUY_AT, capacity_fn=_open(1),
                               fetch=lambda *_a: json.dumps(stranger).encode())
    assert result["requests"][0]["halted"]["unselected_games_returned"] == ["FIXTURE_NBA_EVENT_9"]
    halt = session.scalar(select(CollectionLaneHalt))
    assert halt.lane == sgo_price.LANE and halt.active
    again = sgo_price.collect(session, _lane(world, team_map), now=BUY_AT, capacity_fn=_open(1),
                              fetch=lambda *a: pytest.fail("a halted lane never requests"))
    assert again["status"] == "LANE_HALTED"


@pytest.mark.parametrize("kwargs, reason", [
    ({"league": "FIXTURE_OTHER"}, "LEAGUE_ID_MISMATCH"),
    ({"starts": fx.TIPOFF + timedelta(minutes=30)}, "START_TIME_DISAGREES_WITH_ADMITTED_TIPOFF"),
    ({"home": AWAY, "away": HOME}, "ORIENTATION_REVERSED"),
    ({"home": "FIXTURE_UNMAPPED"}, "TEAM_NOT_IN_REVIEWED_MAP"),
])
def test_binding_is_exact(session, world, team_map, kwargs, reason):
    from app.data_sources.sgo_parser import parse_main_markets

    row = _eligible_game(session, world, lease_minutes=90)
    binding = sgo_price.load_binding(_lane(world, team_map))
    parsed = parse_main_markets(event(**kwargs), received_at=BUY_AT, now=BUY_AT,
                                league_id=kwargs.get("league", LEAGUE))
    ok, why = sgo_price.bind_quote_event(session, row.canonical_game_id, parsed.quotes[0], binding)
    assert (ok, why) == (False, reason)


def test_a_team_map_that_is_not_one_to_one_is_refused(tmp_path, settings):
    path = tmp_path / "bad-map.json"
    path.write_text(json.dumps({"schema": sgo_price.TEAM_MAP_SCHEMA, "leagueID": LEAGUE, "entries": [
        {"sgoTeamID": HOME, "franchiseId": "nba-neil:franchise:FXH", "reviewed": True},
        {"sgoTeamID": AWAY, "franchiseId": "nba-neil:franchise:FXH", "reviewed": True}]}))
    binding = sgo_price.load_binding(settings.model_copy(update={"nba_sgo_league_id": LEAGUE,
                                                                 "nba_sgo_team_map_path": str(path)}))
    assert not binding.resolved and binding.problems[0].startswith("TEAM_MAP_NOT_ONE_TO_ONE")
