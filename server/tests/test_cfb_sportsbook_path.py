"""CFB sportsbook path end to end, offline, against LABELLED FIXTURES (never real provider ids).

Covers: the one validation request (identities observed, nothing priced, once),
moneyline + spread + total from ONE response with per-side book clocks and
AISP's own fetch/receipt clocks, per-event receipts in the ledger, the review
artifact for unbound events, re-binding retained bytes without a request, the
joint-only comparisons, and the checkpoint's NO_BET / NO_PRICE / BLOCKED meaning.
"""
from __future__ import annotations

import json
from datetime import UTC, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.cfb import market_read, paper_decision, production_import, sgo_price
from app.cfb.paper_decision import checkpoint_semantics
from app.models import OddsSnapshot, ProviderRequestLedger
from tests.test_cfb_consumer_activation import _in_window, era, world  # noqa: F401 -- fixtures
from tests.test_cfb_production_discovery import KICK

LEAGUE = "FIXTURE_NCAAF"
CANDIDATE = "FIXTURE_NCAAF"
HOME, AWAY = "FIXTURE_HOME_10", "FIXTURE_AWAY_20"
BUY_AT = KICK - timedelta(minutes=60)
HOME_STAMP = KICK - timedelta(minutes=70)
AWAY_STAMP = KICK - timedelta(minutes=67)


def full_event(*, league=LEAGUE, home=HOME, away=AWAY, starts=KICK, home_ml=-150, away_ml=+130,
               home_line=-3.5, total=51.5) -> dict:
    """Moneyline, spread and total for one event, each side stamped with its OWN book clock."""

    def stamp(at):
        return at.isoformat().replace("+00:00", "Z")

    def odd(odd_id, opposing, bet, entity, side, price, at, **line):
        return {"oddID": odd_id, "opposingOddID": opposing, "periodID": "game", "statID": "points",
                "betTypeID": bet, "statEntityID": entity, "sideID": side,
                "byBookmaker": {"draftkings": {"odds": f"{price:+d}", "lastUpdatedAt": stamp(at), "available": True,
                                               **{k: str(v) for k, v in line.items()}}}}

    odds = {
        "points-home-game-ml-home": odd("points-home-game-ml-home", "points-away-game-ml-away", "ml", "home", "home",
                                        home_ml, HOME_STAMP),
        "points-away-game-ml-away": odd("points-away-game-ml-away", "points-home-game-ml-home", "ml", "away", "away",
                                        away_ml, AWAY_STAMP),
        "points-home-game-sp-home": odd("points-home-game-sp-home", "points-away-game-sp-away", "sp", "home", "home",
                                        -110, HOME_STAMP, spread=home_line),
        "points-away-game-sp-away": odd("points-away-game-sp-away", "points-home-game-sp-home", "sp", "away", "away",
                                        -110, AWAY_STAMP, spread=-home_line),
        "points-all-game-ou-over": odd("points-all-game-ou-over", "points-all-game-ou-under", "ou", "all", "over",
                                       -105, HOME_STAMP, overUnder=total),
        "points-all-game-ou-under": odd("points-all-game-ou-under", "points-all-game-ou-over", "ou", "all", "under",
                                        -115, AWAY_STAMP, overUnder=total),
    }
    return {"success": True, "data": [{
        "eventID": "FIXTURE_EVENT_1", "leagueID": league, "sportID": "FOOTBALL",
        "teams": {"home": {"teamID": home, "names": {"long": "Home 401 Fixture"}},
                  "away": {"teamID": away, "names": {"long": "Away 401 Fixture"}}},
        "status": {"startsAt": starts.isoformat().replace("+00:00", "Z"), "started": False, "completed": False,
                   "oddsPresent": True},
        "odds": odds}]}


def _open(allowed):
    return lambda *_a, **_k: {"state": "CAPACITY_AVAILABLE" if allowed else "NO_UNCOMMITTED_SGO_CAPACITY",
                              "objects_allowed": allowed}


@pytest.fixture
def schedule(world):
    """CFBD raw capture with kickoffs: game 401 is the only one at KICK, so it is uniquely timed."""

    path = world.tmp / "games-timed.json"
    path.write_text(json.dumps([
        {"id": 401, "homeId": 10, "awayId": 20, "homeTeam": "Home 401", "awayTeam": "Away 401",
         "startDate": KICK.isoformat().replace("+00:00", "Z"), "neutralSite": False},
        {"id": 402, "homeId": 30, "awayId": 40, "homeTeam": "Home 402", "awayTeam": "Away 402",
         "startDate": (KICK + timedelta(hours=3)).isoformat().replace("+00:00", "Z"), "neutralSite": False}]))
    return path


def _settings(world, *, league=LEAGUE, team_map=None, validation=False, schedule=None):
    s = world.settings
    s.cfb_sgo_league_id = league
    s.cfb_sgo_team_map_path = str(team_map) if team_map else ""
    s.cfb_sgo_lane_enabled = True
    s.cfb_sgo_validation_enabled = validation
    s.cfb_sgo_validation_league_candidate = CANDIDATE
    s.cfb_sgo_validation_max_requests = 1
    s.raw_data_root = str(world.tmp / "raw")
    if schedule is not None:
        s.cfb_independent_schedule_path = str(schedule)
    return s


@pytest.fixture
def team_map(world):
    path = world.tmp / "team-map.json"
    path.write_text(json.dumps({"schema": sgo_price.TEAM_MAP_SCHEMA, "leagueID": LEAGUE, "entries": [
        {"sgoTeamID": HOME, "cfbdTeamId": 10, "reviewed": True, "evidence": "FIXTURE"},
        {"sgoTeamID": AWAY, "cfbdTeamId": 20, "reviewed": True, "evidence": "FIXTURE"}]}))
    return path


def _terminal(session):
    return [r for r in session.scalars(select(ProviderRequestLedger)) if r.status != "RESERVED"]


# -- the one validation request ------------------------------------------------
def test_validation_observes_identities_prices_nothing_and_is_spent_once(world, era, monkeypatch, schedule):
    _in_window(world, era, monkeypatch)
    settings = _settings(world, league="", validation=True, schedule=schedule)
    planned = sgo_price.plan(world.session, settings, now=BUY_AT, capacity_fn=_open(1))
    ready = [g for g in planned["games"] if g["state"] == sgo_price.VALIDATION_READY]
    assert [g["source_game_id"] for g in ready] == ["401"]
    assert ready[0]["params"]["leagueID"] == CANDIDATE and ready[0]["params"]["limit"] == "1"

    result = sgo_price.collect(world.session, settings, now=BUY_AT, capacity_fn=_open(1),
                               fetch=lambda *_a: full_event(league=CANDIDATE))
    assert result["objects_spent"] == 1
    request = result["requests"][0]
    assert request["purpose"] == sgo_price.VALIDATION_PURPOSE
    assert request["persisted"] == {"created": 0, "existing": 0}           # nothing priced
    assert {r["reason"] for r in request["event_receipts"]} == {sgo_price.LEAGUE_NOT_PINNED}
    review = request["binding_review"]
    assert review["status"] == "WRITTEN" and review["proposals"] == 2 and review["league"]["pinnable"] == CANDIDATE
    artifact = json.loads(next((world.tmp / "raw" / "sportsgameodds-cfb-binding-review").glob("*.json")).read_text())
    assert [(p["sgo_team_id"], p["cfbd_team_id"]) for p in artifact["proposals"]] == [(HOME, "10"), (AWAY, "20")]
    [terminal] = _terminal(world.session)
    assert terminal.response_metadata["purpose"] == sgo_price.VALIDATION_PURPOSE
    assert terminal.response_metadata["event_receipts"][0]["home_team_id"] == HOME
    assert world.session.scalar(select(func.count()).select_from(OddsSnapshot)) == 0

    again = sgo_price.plan(world.session, settings, now=BUY_AT + timedelta(minutes=10), capacity_fn=_open(1))
    assert again["validation"]["requests_used"] == 1 and not again["validation"]["available"]
    assert sgo_price.VALIDATION_READY not in {g["state"] for g in again["games"]}


def test_without_the_validation_switch_an_unresolved_league_requests_nothing(world, era, monkeypatch, schedule):
    _in_window(world, era, monkeypatch)
    settings = _settings(world, league="", validation=False, schedule=schedule)
    result = sgo_price.collect(world.session, settings, now=BUY_AT, capacity_fn=_open(5),
                               fetch=lambda *_a: pytest.fail("no request"))
    assert result["objects_spent"] == 0 and result["status"] == "NO_READY_GAME"
    assert {g["state"] for g in result["games"]} <= {"SGO_BINDING_UNRESOLVED"}


def test_validation_still_spends_only_through_the_capacity_gate(world, era, monkeypatch, schedule):
    _in_window(world, era, monkeypatch)
    settings = _settings(world, league="", validation=True, schedule=schedule)
    result = sgo_price.collect(world.session, settings, now=BUY_AT, capacity_fn=_open(0),
                               fetch=lambda *_a: pytest.fail("no request"))
    assert result["objects_spent"] == 0 and result["status"] == "NO_UNCOMMITTED_SGO_CAPACITY"


# -- one response, three markets, three clocks ----------------------------------
def _collect(world, settings, **event):
    return sgo_price.collect(world.session, settings, now=BUY_AT, capacity_fn=_open(1),
                             fetch=lambda *_a: full_event(**event))


def test_moneyline_spread_and_total_come_from_one_response_with_each_sides_own_clock(
        world, era, monkeypatch, team_map, schedule):
    _in_window(world, era, monkeypatch)
    result = _collect(world, _settings(world, team_map=team_map, schedule=schedule))
    assert result["objects_spent"] == 1 and len(result["requests"]) == 1
    request = result["requests"][0]
    assert request["markets_parsed"] == {"h2h": 1, "spreads": 1, "totals": 1}
    assert request["persisted"]["created"] == 6
    assert [r["reason"] for r in request["event_receipts"]] == ["BOUND"]
    [terminal] = _terminal(world.session)
    assert terminal.response_metadata["event_receipts"][0]["reason"] == "BOUND"

    rows = list(world.session.scalars(select(OddsSnapshot)))
    assert {r.observed_at.replace(tzinfo=UTC) for r in rows} == {HOME_STAMP, AWAY_STAMP}   # each side its own
    for row in rows:
        assert row.available_as_of.replace(tzinfo=UTC) == BUY_AT
        assert row.raw_payload["fetched_at"] == BUY_AT.isoformat()
        assert row.raw_payload["response_received_at"] == BUY_AT.isoformat()
        assert row.raw_payload["clock_semantics"] == sgo_price.CLOCK_SEMANTICS
        assert row.raw_payload["settlement_basis"] == "ASSERTED_BY_AISP_NOT_STATED_BY_PROVIDER"

    game_id = production_import.current_forecast_for_game(world.session, "401").canonical_game_id
    quotes = market_read.stored_quotes(world.session, [game_id], now=BUY_AT + timedelta(minutes=5))[str(game_id)]
    assert [q["market"] for q in quotes] == ["MONEYLINE", "SPREAD", "TOTAL"]
    moneyline = quotes[0]
    assert moneyline["clocks"]["legs"] == {"HOME": HOME_STAMP.isoformat(), "AWAY": AWAY_STAMP.isoformat()}
    assert moneyline["clocks"]["older_leg_at"] == HOME_STAMP.isoformat()
    assert moneyline["clocks"]["leg_skew_seconds"] == 180.0
    assert moneyline["clocks"]["quote_age_minutes"] == 15.0
    assert moneyline["clocks"]["fetched_at"] == BUY_AT.isoformat()
    assert moneyline["clocks"]["received_at"] == BUY_AT.isoformat()
    assert moneyline["fresh"] is True
    assert quotes[1]["line"] == -3.5 and quotes[2]["line"] == 51.5


def test_comparisons_use_the_joint_channel_only(world, era, monkeypatch, team_map, schedule):
    _in_window(world, era, monkeypatch)
    _collect(world, _settings(world, team_map=team_map, schedule=schedule))
    forecast = production_import.current_forecast_for_game(world.session, "401")
    market = market_read.game_market(world.session, forecast.canonical_game_id, forecast,
                                     now=BUY_AT + timedelta(minutes=5))
    assert market["state"] == "QUOTES_STORED"
    by_market = {c["market"]: c for c in market["comparisons"]}

    ml = by_market["MONEYLINE"]
    joint_home = forecast.joint_home_win_probability / (forecast.joint_home_win_probability
                                                        + forecast.joint_away_win_probability)
    assert ml["channel"] == market_read.JOINT_CHANNEL and ml["model"]["HOME"] == pytest.approx(joint_home)
    assert set(ml["excluded_channels"]) == {"RIDGE_DEVELOPMENT_CALIBRATED", "ELO_DIAGNOSTIC"}
    assert ml["model"]["HOME"] != pytest.approx(forecast.ridge_home_win_probability)
    assert ml["difference"]["HOME"] == pytest.approx(joint_home - ml["no_vig"]["HOME"])

    spread = by_market["SPREAD"]
    margin = forecast.pmfs["home_margin"]
    covers = sum(c for v, c in zip(margin["values"], margin["counts"]) if v > 3.5) / margin["draw_count"]
    assert spread["channel"] == market_read.DISTRIBUTION_CHANNEL and spread["model"]["HOME"] == pytest.approx(covers)
    assert spread["model"]["PUSH"] == 0.0 and spread["policy_test"] is False

    total = by_market["TOTAL"]
    pmf = forecast.pmfs["total"]
    over = sum(c for v, c in zip(pmf["values"], pmf["counts"]) if v > 51.5) / pmf["draw_count"]
    assert total["model"]["OVER"] == pytest.approx(over)


def test_a_forecast_without_a_score_distribution_compares_only_the_moneyline():
    forecast = SimpleNamespace(joint_home_win_probability=0.6, joint_away_win_probability=0.4, tie_probability=0.0,
                               pmfs=None)
    quote = {"market": "TOTAL", "line": 50.5, "fresh": True, "sportsbook": "Book", "provider": "SPORTSGAMEODDS",
             "sides": {"OVER": {"american": -110, "decimal": 1.909}, "UNDER": {"american": -110, "decimal": 1.909}}}
    out = market_read.compare(forecast, quote)
    assert out["available"] is False and out["reason"] == "FORECAST_PROVIDES_NO_SCORE_DISTRIBUTION"


def test_an_unmapped_team_is_receipted_reviewed_and_never_priced(world, era, monkeypatch, team_map, schedule):
    _in_window(world, era, monkeypatch)
    result = _collect(world, _settings(world, team_map=team_map, schedule=schedule), away="FIXTURE_UNKNOWN_77")
    request = result["requests"][0]
    assert {r["reason"] for r in request["event_receipts"]} == {"TEAM_NOT_IN_REVIEWED_MAP"}
    assert request["persisted"]["created"] == 0 and request["binding_review"]["status"] == "WRITTEN"
    assert world.session.scalar(select(func.count()).select_from(OddsSnapshot)) == 0
    [terminal] = _terminal(world.session)
    assert terminal.response_metadata["event_receipts"][0]["away_team_id"] == "FIXTURE_UNKNOWN_77"


def test_retained_bytes_are_rebound_after_review_without_a_request_and_never_backdated(
        world, era, monkeypatch, team_map, schedule):
    from app.ingestion.archive import RawArchive

    _in_window(world, era, monkeypatch)
    body = json.dumps(full_event()).encode()
    record = RawArchive(world.tmp / "raw").write_response(
        "SPORTSGAMEODDS", body, request_fingerprint="fp",
        extra_metadata={"lane": sgo_price.LANE, "requested_at": BUY_AT.isoformat()})
    unresolved = _settings(world, league="", schedule=schedule)
    assert sgo_price.persist_from_archive(world.session, unresolved, record.path,
                                          source_game_id="401")["status"] == "SGO_BINDING_UNRESOLVED"
    bound_at = BUY_AT + timedelta(minutes=12)
    out = sgo_price.persist_from_archive(world.session, _settings(world, team_map=team_map, schedule=schedule),
                                         record.path, source_game_id="401", now=bound_at)
    assert out["status"] == "PERSISTED" and out["persisted"]["created"] == 6
    assert world.session.scalar(select(func.count()).select_from(ProviderRequestLedger)) == 0   # no request
    for row in world.session.scalars(select(OddsSnapshot)):
        assert row.available_as_of.replace(tzinfo=UTC) == bound_at        # available from the binding on
        assert row.raw_payload["fetched_at"] == BUY_AT.isoformat() and row.raw_payload["rebound_from_archive"]


# -- the checkpoint: NO_BET vs NO_PRICE vs BLOCKED ------------------------------
@pytest.mark.parametrize("recommendation,reason,kind,evaluated,priced", [
    ("DECLINED", "NO_QUALIFYING_EDGE", "NO_BET", True, True),
    ("DECLINED", "NO_TWO_SIDED_QUOTE", "NO_PRICE", True, False),
    ("DECLINED", "QUOTE_TOO_STALE", "NO_PRICE", True, False),
    ("DECLINED", "NO_PERMITTED_PROVIDER", "NO_PRICE", True, False),
    ("BLOCKED", "DISCOVERY_NOT_CURRENT", "BLOCKED", False, False),
    ("BLOCKED", "AWAITING_PRODUCER_ELIGIBILITY", "BLOCKED", False, False),
    ("DECLINED", "ACTIVATION_CONTROL_OFF", "QUALIFIED_NOT_ACTIVATED", True, True),
    ("QUALIFIED_BET", "ALL_GATES_PASSED", "QUALIFIED", True, True),
])
def test_checkpoint_semantics(recommendation, reason, kind, evaluated, priced):
    out = checkpoint_semantics(recommendation, reason)
    assert (out["class"], out["evaluated"], out["priced"]) == (kind, evaluated, priced)


def _decision_for(world, now):
    return next(d for d in paper_decision.evaluate(world.session, world.settings, now=now)["decisions"]
                if d["source_game_id"] == "401")


def test_the_checkpoint_prices_real_quotes_and_reads_as_no_bet_or_qualified(world, era, monkeypatch, team_map,
                                                                            schedule):
    from app.cfb.read_service import game_detail

    _in_window(world, era, monkeypatch)
    _collect(world, _settings(world, team_map=team_map, schedule=schedule))
    decision = _decision_for(world, KICK - timedelta(minutes=20))
    assert decision["reason"] in {"NO_QUALIFYING_EDGE", "ALL_GATES_PASSED"}
    assert decision["best" if decision["reason"] == "NO_QUALIFYING_EDGE" else "selection"]["source"] == "SPORTSGAMEODDS"
    detail = game_detail(world.session, source_game_id="401")
    semantics = detail["pipeline"]["decision"]["semantics"]
    assert semantics["class"] in {"NO_BET", "QUALIFIED"} and semantics["priced"] is True
    # The page reads at the real clock, a week before this fixture's quotes: stored, shown, not current.
    market = detail["pipeline"]["market"]
    assert [q["market"] for q in market["quotes"]] == ["MONEYLINE", "SPREAD", "TOTAL"]
    assert market["state"] in {"QUOTES_STORED", "STORED_REFERENCE_ONLY"}
    assert all(c["channel"] in (market_read.JOINT_CHANNEL, market_read.DISTRIBUTION_CHANNEL)
               for c in market["comparisons"])


def test_no_quote_is_a_declined_no_price_not_a_no_bet(world, era, monkeypatch):
    from app.cfb.read_service import game_detail

    _in_window(world, era, monkeypatch)
    decision = _decision_for(world, KICK - timedelta(minutes=20))
    assert decision["outcome"] == "DECLINED" and decision["reason"] == "NO_TWO_SIDED_QUOTE"
    pipeline = game_detail(world.session, source_game_id="401")["pipeline"]
    assert pipeline["decision"]["semantics"]["class"] == "NO_PRICE"
    assert pipeline["decision"]["semantics"]["evaluated"] is True and pipeline["decision"]["semantics"]["priced"] is False
    assert pipeline["market"]["state"] == "NO_QUOTE_ROWS"


def test_an_expired_lease_is_blocked_not_evaluated(world, era, monkeypatch):
    from app.cfb.read_service import game_detail

    _in_window(world, era, monkeypatch, lease_minutes=-5)   # the lease lapsed before the checkpoint
    decision = _decision_for(world, KICK - timedelta(minutes=20))
    assert decision["outcome"] == "BLOCKED" and decision["reason"] == "DISCOVERY_NOT_CURRENT"
    semantics = game_detail(world.session, source_game_id="401")["pipeline"]["decision"]["semantics"]
    assert semantics["class"] == "BLOCKED" and semantics["evaluated"] is False


def test_the_total_scenario_says_when_a_sportsbook_offered_that_line(world, era, monkeypatch, team_map, schedule):
    from app.cfb.read_service import total_scenario

    _in_window(world, era, monkeypatch)
    assert total_scenario(world.session, source_game_id="401", line=51.5)["market_recorded"] is False
    _collect(world, _settings(world, team_map=team_map, schedule=schedule))
    stored = total_scenario(world.session, source_game_id="401", line=51.5)
    assert stored["market_recorded"] is True and stored["label"].startswith("Stored sportsbook line")
    assert total_scenario(world.session, source_game_id="401", line=48.5)["market_recorded"] is False


# -- the slate's CFB model reading: joint numbers only ----------------------------
def test_the_slate_projected_score_is_the_joint_mean_not_ridge():
    from app.services.command_center import cfb_model_entry

    pmfs = {"home_score": {"values": [20, 30], "counts": [1, 3], "draw_count": 4},
            "away_score": {"values": [10, 20], "counts": [2, 2], "draw_count": 4}}
    entry = cfb_model_entry(0.7, 0.3, 99.0, 1.0, None, "f", 0.65, 0.55, "PRODUCTION_DISCOVERY", "X", pmfs)
    assert entry["expected_home"] == 27.5 and entry["expected_away"] == 15.0
    assert entry["expected_points_channel"] == "JOINT_SCORE_DISTRIBUTION_MEAN"
    assert entry["separate_channels"]["ridge_expected_home_points"] == 99.0
    assert cfb_model_entry(0.7, 0.3, 99.0, 1.0, None, "f", 0.65, 0.55, "P", "X", None)["expected_home"] is None
