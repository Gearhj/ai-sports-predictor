"""Mission Control (``GET /mission-control``, AISP_MISSION_CONTROL_V1). TEST FIXTURES ONLY.

Every game, forecast, quote, decision and result here is invented in a throwaway
SQLite database. ``command_center.market_coverage`` and ``decision_board`` are
PostgreSQL SQL, so they are replaced by fakes that hand back rows in their own
shape (the board fake can also build items from the ORM rows a real checkpoint
wrote). Everything else -- the NHL lifecycle reads, the CFB gates, the NFL
eligibility check, the scoreboard, the shared allowance, the bankroll accounts,
job health -- runs for real.

What is pinned:
* one case per stage state for each sport (done / waiting / blocked / not applicable / unknown);
* the next-step walk and the clock it counts down to; the attention rules;
* an NFL decision whose slate_date is the next (UTC) day still attaches to its game;
* the allowance is read without ``room()`` (whose lock would serialize the writers);
* NHL's own $1,000 appears among the bankrolls;
* job health maps job_runs rows and never leaks an error message;
* one failing sport is READ_FAILED and never blanks the page;
* the route: schema, date range, default date, GET-only, no network, no commit;
* the bulk readers agree exactly with the per-game rules they replace.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.models import (
    ExternalIdMapping, Game, GameStatus, GameTeam, JobRun, League, Market, MarketOutcome, MarketType, OddsSnapshot,
    ResearchSupplement, RunStatus, Season, ShadowForecast, ShadowForecastBundle, Sport, Sportsbook, Team,
)
from app.nhl import paper
from app.services import command_center
from app.services import mission_control as mc
from app.services.research_paper_policy import CFB, NHL
from tests.test_nhl_paper_lifecycle import (
    START as NHL_START, _activate, _in_process_forecast, _quotes, _result,
)

DAY = date(2026, 10, 17)                                     # a Saturday
ET_NOON = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)         # 12:00 PM ET
NHL_DAY = date(2026, 10, 20)                                # NHL_START is 7:00 PM ET on Oct 20


# ------------------------------------------------------------------ world --

@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    for cls in (httpx.Client, httpx.AsyncClient):
        original = cls.send

        def guarded(self, request, *args, _original=original, **kwargs):
            if request.url.host != "testserver":
                raise AssertionError("a network request was attempted")
            return _original(self, request, *args, **kwargs)
        monkeypatch.setattr(cls, "send", guarded)


@pytest.fixture
def world(monkeypatch):
    """Fakes for the two PostgreSQL read helpers, the active-policy set and the CFB grant."""

    state = SimpleNamespace(coverage={}, boards=None, board_days=[], policies={"MLB", "NFL"},
                            grant={"sha256": "g" * 64, "valid_until": ET_NOON + timedelta(days=4)})

    def fake_coverage(session, day, now=None, *, as_of=None):
        return {"games": list(state.coverage.values())}

    def fake_board(session, day, now=None):
        state.board_days.append(day)
        if state.boards is not None:
            return {"items": list(state.boards.get(day, []))}
        return {"items": _orm_board(session, day)}

    monkeypatch.setattr(command_center, "market_coverage", fake_coverage)
    monkeypatch.setattr(command_center, "decision_board", fake_board)
    monkeypatch.setattr("app.services.slate_status._leagues_with_active_policy", lambda session: set(state.policies))
    monkeypatch.setattr("app.cfb.paper_decision._grant", lambda settings: dict(state.grant))
    return state


def _orm_board(session, day):
    """Board-shaped items from the decisions a real checkpoint wrote (SQLite stand-in for decision_board)."""

    from app.models import OfficialAspPosition, PositionSettlement, ShadowExperimentDecision

    rows = session.execute(
        select(ShadowExperimentDecision, OfficialAspPosition, PositionSettlement, Game)
        .join(Game, Game.id == ShadowExperimentDecision.game_id)
        .outerjoin(OfficialAspPosition, OfficialAspPosition.shadow_decision_id == ShadowExperimentDecision.id)
        .outerjoin(PositionSettlement, PositionSettlement.position_id == OfficialAspPosition.id)
        .where(ShadowExperimentDecision.slate_date == day)).all()
    items = []
    for d, p, r, g in rows:
        abbr = {is_home: a for is_home, a in session.execute(
            select(GameTeam.is_home, Team.abbreviation).join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == g.id)).all()}
        side = str(d.selection or "").upper()
        pnl = float(r.profit_loss) if r is not None else None
        items.append({
            "decision_id": str(d.id), "sport": d.sport, "game_id": str(d.game_id), "phase": d.phase,
            "selection": f"{abbr.get(side == 'HOME', side)} ML" if side in ("HOME", "AWAY") else d.selection,
            "sportsbook": d.sportsbook, "american": d.american_odds,
            "quote_observed_at": d.quote_observed_at.isoformat() if d.quote_observed_at else None,
            "model_probability": d.model_probability, "market_probability": d.no_vig_probability, "edge": d.edge,
            "recommendation": d.recommendation, "stored_recommendation": d.recommendation,
            "reason": d.recommendation_reason, "recommended_at": d.recommended_at.isoformat(),
            "plain_reasons": [], "funding_pause": None, "market_integrity": None,
            "position": None if p is None else {
                "position_id": str(p.id), "stake": float(p.stake),
                "status": "SETTLED" if pnl is not None else p.status.name,
                "result": r.result.name if r is not None else None, "profit_loss": pnl,
                "display_status": command_center._position_display(p.status.name, pnl, g.status.name),
                "awaiting_settlement": pnl is None and p.status.name == "OPEN" and g.status.name == "FINAL"}})
    return items


def _game(session, code, *, home="HOM", away="AWY", starts_at=ET_NOON + timedelta(hours=3), mode="LIVE",
          status=GameStatus.SCHEDULED, home_name=None, away_name=None):
    league = session.scalar(select(League).where(League.code == code))
    if league is None:
        sport = Sport(code=f"S_{code}", name=code)
        session.add(sport)
        session.flush()
        league = League(sport_id=sport.id, code=code, name=code)
        session.add(league)
        session.flush()
    season = Season(league_id=league.id, name=f"{code}-{uuid.uuid4().hex[:6]}",
                    starts_at=starts_at - timedelta(days=60), ends_at=starts_at + timedelta(days=60))
    session.add(season)
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=starts_at, status=status, source_mode=mode)
    session.add(game)
    session.flush()
    for abbr, name, is_home in ((home, home_name, True), (away, away_name, False)):
        team = Team(league_id=league.id, name=name or f"{abbr} {game.id.hex[:4]}", abbreviation=abbr)
        session.add(team)
        session.flush()
        session.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
    session.flush()
    return game


def _cov(game, league, **fields):
    return {"game_id": str(game.id), "league": league, "best": {"HOME": None, "AWAY": None}, "books": 0,
            "fresh_books": 0, "quotes": [], "model": None, "freshest_observed_at": None,
            "consensus_no_vig_home": None, **fields}


def _item(game, sport, **fields):
    base = {"decision_id": str(uuid.uuid4()), "sport": sport, "game_id": str(game.id), "phase": "FINAL_PREGAME",
            "selection": "HOM ML", "sportsbook": "DraftKings", "american": -150,
            "quote_observed_at": (game.starts_at - timedelta(minutes=40)).isoformat(),
            "model_probability": 0.61, "market_probability": 0.58, "edge": 0.03,
            "recommendation": "NO_BET", "stored_recommendation": "NO_BET",
            "reason": "Adjusted edge 0.8 points is below the 2-point minimum.",
            "recommended_at": (game.starts_at - timedelta(minutes=25)).isoformat(),
            "plain_reasons": [], "funding_pause": None, "position": None,
            "market_integrity": {"older_leg_age_minutes_at_decision": 12.0}}
    base.update(fields)
    if "stored_recommendation" not in fields:
        base["stored_recommendation"] = base["recommendation"]
    return base


def _by_id(payload):
    return {g["game_id"]: g for g in payload["games"]}


def _run(session, settings, day=DAY, now=ET_NOON):
    payload = mc.mission_control(session, settings, day, now=now)
    json.dumps(payload)                                         # always serializable
    return payload


# ------------------------------------------------------------ pure helpers --

def test_clock_labels_name_the_day_only_when_needed():
    now = ET_NOON
    assert mc._when(now + timedelta(hours=3), now) == "3:00 PM"
    assert mc._when(now + timedelta(days=1), now) == "Sun 12:00 PM"
    assert mc._when(now - timedelta(days=6), now) == "Oct 11 12:00 PM"
    assert mc._when(datetime(2027, 6, 30, 23, 59, tzinfo=UTC), now) == "Jun 30 2027 7:59 PM"
    assert len(mc._label("x" * 80)) == mc.LABEL_MAX


def test_forecast_freshness_bands_are_measured_at_the_start_once_started():
    start = ET_NOON
    assert mc._freshness(start - timedelta(hours=3), start - timedelta(hours=1), start)[0] == "FRESH"
    assert mc._freshness(start - timedelta(hours=30), start - timedelta(hours=1), start)[0] == "AGING"
    assert mc._freshness(start - timedelta(hours=90), start - timedelta(hours=1), start)[0] == "STALE"
    # Measured at min(now, start): a day after the game the same forecast is no older.
    assert mc._freshness(start - timedelta(hours=3), start + timedelta(days=1), start)[1] == 180.0


@pytest.mark.parametrize("fields,code,expected", [
    ({"position": {"stake": 4.0}}, None, "BET"),
    ({"recommendation": "NO_BET"}, None, "PASS"),
    ({"recommendation": "DECLINED"}, "NO_QUALIFYING_EDGE", "PASS"),
    ({"recommendation": "DECLINED"}, "NO_QUALIFYING_VALUE", "PASS"),          # NFL's priced decline
    ({"recommendation": "DECLINED"}, "SHARED_ALLOWANCE_EXHAUSTED", "PASS"),
    ({"recommendation": "DECLINED"}, "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS", "PASS"),   # MLB/NHL first
    ({"recommendation": "DECLINED"}, "NO_TWO_SIDED_QUOTE", "NO_PRICE"),
    ({"recommendation": "DECLINED"}, "ACTIVATION_CONTROL_OFF", "NOT_ACTIVATED"),
    ({"recommendation": "BLOCKED"}, "SCHEDULE_DRIFT", "BLOCKED"),
    ({"recommendation": "BLOCKED"}, "REST_FEATURES_NOT_COMPLETE_AT_GENERATION", "BLOCKED"),
    ({"recommendation": "QUALIFIED_BET", "funding_pause": {"id": "x"}}, None, "BLOCKED"),
    ({"recommendation": "NO_BET", "plain_reasons": ["Forecast unavailable"]}, None, "BLOCKED"),
    ({"recommendation": "NOT_YET_EVALUATED", "stored_recommendation": "NO_BET", "phase": "MORNING"}, None, "PENDING"),
])
def test_decision_classes(fields, code, expected):
    game = SimpleNamespace(closes=ET_NOON + timedelta(hours=1))
    item = {"phase": "FINAL_PREGAME", "recommendation": "NO_BET", **fields}
    item.setdefault("stored_recommendation", item["recommendation"])
    assert mc._decision_class(item, code, game, ET_NOON) == expected


def test_a_morning_row_after_the_window_is_not_recorded_and_a_missing_quote_is_no_price():
    game = SimpleNamespace(closes=ET_NOON - timedelta(minutes=1))
    assert mc._decision_class({"phase": "MORNING", "recommendation": "NOT_YET_EVALUATED",
                               "stored_recommendation": "NO_BET"}, None, game, ET_NOON) == "NOT_RECORDED"
    assert mc._decision_class({"phase": "FINAL_PREGAME", "recommendation": "NOT_YET_EVALUATED",
                               "stored_recommendation": "NO_BET"}, None, game, ET_NOON) == "NO_PRICE"


def _stages(**states):
    out = {}
    for name in mc.STAGES:
        state, due = states.get(name, (mc.DONE, None))
        out[name] = mc._stage(name, state, f"{name.upper()}_CODE", name, f"{name} detail", due_at=due)
    return out


def test_next_step_walks_the_pipeline_and_borrows_the_next_due_clock():
    ctx = SimpleNamespace(now=ET_NOON, next_run=lambda _id: None)
    game = mc._Game(uuid.uuid4(), "NHL", ET_NOON + timedelta(hours=3), "scheduled", "RESEARCH_SHADOW")
    opens = game.opens
    # The first BLOCKED pre-bet stage wins, with its own sentence.
    step = mc._next_step(ctx, game, _stages(approval=(mc.BLOCKED, None), prices=(mc.WAITING, opens)), "PREGAME", None)
    assert (step["stage"], step["kind"], step["text"]) == ("APPROVAL", "BLOCKED", "approval detail")
    # A WAITING stage with no clock borrows the next stage's.
    step = mc._next_step(ctx, game, _stages(prices=(mc.WAITING, None), decision=(mc.WAITING, opens)), "PREGAME", None)
    assert (step["stage"], step["kind"], step["at"]) == ("PRICES", "WAIT", opens.isoformat())
    assert step["label"] == "Prices in"
    # UNKNOWN is never done.
    step = mc._next_step(ctx, game, _stages(forecast=(mc.UNKNOWN, None)), "PREGAME", None)
    assert step["kind"] == "BLOCKED" and step["stage"] == "FORECAST"
    # A placed bet waits for puck drop.
    stages = _stages()
    stages["bet"]["position_id"] = "p"
    stages["result"] = mc._stage("result", mc.WAITING, "NOT_STARTED", "After the game", "x")
    step = mc._next_step(ctx, game, stages, "PREGAME", None)
    assert (step["stage"], step["kind"], step["label"], step["at"]) == ("BET", "WAIT", "Puck drop",
                                                                     game.start.isoformat())
    # Live is WATCH; a final open position waits for settlement.
    assert mc._next_step(ctx, game, stages, "STARTED", None)["kind"] == "WATCH"
    stages["result"] = mc._stage("result", mc.WAITING, "AWAITING_SETTLEMENT", "Settling", "x",
                                 due_at=game.start + timedelta(hours=2, minutes=30), awaiting_settlement=True)
    step = mc._next_step(ctx, game, stages, "FINAL", None)
    assert (step["stage"], step["kind"], step["label"]) == ("RESULT", "WAIT", "Settlement")
    stages["result"] = mc._stage("result", mc.DONE, "WIN", "W +$2.40", "x")
    assert mc._next_step(ctx, game, stages, "FINAL", None) == {"stage": "DONE", "kind": "DONE", "at": None,
                                                              "label": None, "text": "W +$2.40"}


@pytest.mark.parametrize(("sport", "after", "score", "code"), [
    ("CFB", timedelta(hours=1), None, "IN_PLAY"),
    ("CFB", timedelta(hours=5), None, "AWAITING_SOURCE_RESULT"),
    ("NHL", timedelta(hours=2), None, "IN_PLAY"),
    ("NHL", timedelta(hours=3), None, "AWAITING_SOURCE_RESULT"),
    ("MLB", timedelta(hours=3), None, "IN_PLAY"),
    # MLB/NFL have a live feed, but a DARK one (MLB postseason) must not read "in play" for ever ...
    ("MLB", timedelta(hours=6), None, "AWAITING_SOURCE_RESULT"),
    ("NFL", timedelta(hours=5), None, "AWAITING_SOURCE_RESULT"),
    # ... while a feed that still reads live keeps the game in play, however long it runs.
    ("MLB", timedelta(hours=6), {"available": True, "is_live": True}, "IN_PLAY"),
])
def test_an_open_bet_with_no_live_feed_awaits_its_result_once_the_game_must_be_over(sport, after, score, code):
    start = ET_NOON
    ctx = SimpleNamespace(now=start + after, positions={}, nhl_results={}, next_run=lambda _id: None)
    game = mc._Game(uuid.uuid4(), sport, start, "scheduled", "RESEARCH_SHADOW")
    item = {"decision_id": str(uuid.uuid4()), "position": {"status": "OPEN", "stake": 4.9}}
    result = mc._result(ctx, game, item, {"state": mc.DONE}, {"class": "BET"}, score, "STARTED")
    assert (result["state"], result["code"]) == (mc.WAITING, code)
    assert result["awaiting_settlement"] is (code == "AWAITING_SOURCE_RESULT")
    if code == "AWAITING_SOURCE_RESULT":
        assert "in progress" not in result["detail"] and result["label"] == "Awaiting result"
    # Without a bet the row stops reading "in play" at the same point, and has nothing left to do.
    stages = _stages()
    stages["decision"] = mc._stage("decision", mc.DONE, "NO_BET", "Passed", "x", **{"class": "PASS"})
    step = mc._next_step(ctx, game, stages, "STARTED", score)
    assert (step["kind"], step["text"]) == (("DONE", "No bet · Passed") if code == "AWAITING_SOURCE_RESULT"
                                            else ("WATCH", "In play · no bet (Passed)"))


def test_attention_flags_blockers_only_while_they_can_matter():
    game = mc._Game(uuid.uuid4(), "NHL", ET_NOON + timedelta(hours=3), "scheduled", "RESEARCH_SHADOW")
    ctx = SimpleNamespace(now=ET_NOON, jobs={}, extra={})
    assert mc._attention(ctx, game, _stages(approval=(mc.BLOCKED, None), decision=(mc.WAITING, None))) == "BLOCKED"
    assert mc._attention(SimpleNamespace(now=game.start, jobs={}, extra={}), game,
                         _stages(approval=(mc.BLOCKED, None))) == "NONE"         # the window has closed
    # Once the decision is recorded nothing upstream can stop it any more.
    assert mc._attention(ctx, game, _stages(approval=(mc.BLOCKED, None))) == "NONE"
    stages = _stages()
    stages["decision"]["class"] = "NOT_RECORDED"
    assert mc._attention(SimpleNamespace(now=game.start, jobs={}, extra={}), game, stages) == "BLOCKED"
    stages = _stages()
    stages["prices"]["code"] = "EXPIRES_BEFORE_WINDOW"
    assert mc._attention(ctx, game, stages) == "WATCH"
    failing = {"x": {"state": "FAILED", "registered": True, "sport": "NHL"}}
    assert mc._attention(SimpleNamespace(now=ET_NOON, jobs=failing, extra={}), game, _stages()) == "WATCH"
    assert mc._attention(SimpleNamespace(now=ET_NOON, jobs={}, extra={"producer_alerts": {"NHL"}}), game,
                         _stages()) == "WATCH"
    # A finished game is not re-flagged by today's failing job; an unsettled bet still is.
    after = SimpleNamespace(now=game.start + timedelta(hours=4), jobs=failing, extra={})
    assert mc._attention(after, game, _stages()) == "NONE"
    riding = _stages(result=(mc.WAITING, None))
    riding["bet"]["position_id"] = "p"
    assert mc._attention(after, game, riding) == "WATCH"


# --------------------------------------------------------------------- MLB --

def test_mlb_forecast_states_and_their_clocks(session, settings, world):
    starts = ET_NOON + timedelta(hours=3)
    scheduled = _game(session, "MLB", home="NYY", away="BOS", starts_at=starts)
    overdue = _game(session, "MLB", home="LAD", away="SD", starts_at=ET_NOON + timedelta(minutes=40))
    missed = _game(session, "MLB", home="HOU", away="SEA", starts_at=ET_NOON + timedelta(minutes=10))
    published = _game(session, "MLB", home="CHC", away="MIL", starts_at=starts)
    producer = {"blockers": ["HOME_STARTER_IDENTITY_UNAVAILABLE"],
                "blocker_text": ["the home starting pitcher is not identified"], "reason_text": None}
    world.coverage = {
        str(scheduled.id): _cov(scheduled, "MLB", model_status={"state": "WAITING_ON_INPUTS", "producer": producer}),
        str(overdue.id): _cov(overdue, "MLB", model_status={"state": "OVERDUE", "producer": producer}),
        str(missed.id): _cov(missed, "MLB", model_status={"state": "MISSED", "producer": producer}),
        str(published.id): _cov(published, "MLB", model={"engine_id": "MLB_V18_WHOLE_GAME", "home": 0.57,
                                                         "away": 0.43, "created_at": (ET_NOON - timedelta(hours=1)).isoformat(),
                                                         "forecast_id": "v18-1"},
                                model_status={"state": "PUBLISHED"}),
    }
    games = _by_id(_run(session, settings))
    f = games[str(scheduled.id)]["stages"]["forecast"]
    assert (f["state"], f["code"], f["due_at"]) == ("WAITING", "WAITING_ON_INPUTS",
                                                   (starts - timedelta(minutes=45)).isoformat())
    assert "home starting pitcher" in f["detail"]
    assert games[str(scheduled.id)]["clocks"]["forecast_due_at"] == (starts - timedelta(minutes=45)).isoformat()
    o = games[str(overdue.id)]
    assert (o["stages"]["forecast"]["state"], o["stages"]["forecast"]["code"]) == ("WAITING", "OVERDUE")
    assert o["attention"] == "WATCH"
    m = games[str(missed.id)]["stages"]["forecast"]
    assert (m["state"], m["code"]) == ("BLOCKED", "MISSED") and "home starting pitcher" in m["detail"]
    p = games[str(published.id)]["stages"]["forecast"]
    assert (p["state"], p["made_at_basis"], p["source"]) == ("DONE", "AISP_IMPORT_TRANSACTION_START", "Model Studio V18")
    assert p["favorite"] == {"side": "HOME", "team": "CHC", "prob": 0.57} and p["freshness"] == "FRESH"


def test_mlb_prices_wait_for_the_planned_buy_then_are_done_when_fresh(session, settings, world):
    from app.services import odds_collection_plan as plan

    starts = ET_NOON + timedelta(hours=8)
    waiting = _game(session, "MLB", home="NYY", away="BOS", starts_at=starts)
    priced = _game(session, "MLB", home="CHC", away="MIL", starts_at=starts)
    best = {"HOME": {"american": -140, "sportsbook": "FanDuel", "provider": "The Odds API",
                     "observed_at": (ET_NOON - timedelta(minutes=5)).isoformat()}, "AWAY": None}
    world.coverage = {
        str(waiting.id): _cov(waiting, "MLB", model_status={"state": "SCHEDULED"},
                              market_status={"state": "NO_TWO_SIDED_QUOTE"}),
        str(priced.id): _cov(priced, "MLB", model_status={"state": "SCHEDULED"}, best=best, books=3, fresh_books=3,
                             market_status={"state": "ELIGIBLE_THROUGH_WINDOW",
                                            "fresh_until": (starts - timedelta(minutes=10)).isoformat(),
                                            "newest_leg_observed_at": (ET_NOON - timedelta(minutes=5)).isoformat()}),
    }
    games = _by_id(_run(session, settings.model_copy(update={"odds_checkpoint_collection_enabled": True})))
    checkpoint = plan.plan_checkpoints([starts, starts])[0]
    w = games[str(waiting.id)]["stages"]["prices"]
    assert (w["state"], w["code"], w["due_at"]) == ("WAITING", "BUY_SCHEDULED", checkpoint.isoformat())
    assert games[str(waiting.id)]["clocks"]["buy_opens_at"] == checkpoint.isoformat()
    d = games[str(priced.id)]["stages"]["prices"]
    assert (d["state"], d["label"], d["fresh_books"]) == ("DONE", "FanDuel -140", 3)
    assert d["best"]["home"]["sportsbook"] == "FanDuel"


def test_mlb_pass_bet_and_funding_pause(session, settings, world):
    starts = ET_NOON - timedelta(minutes=5)                       # started; every decision is recorded
    passed = _game(session, "MLB", home="NYY", away="BOS", starts_at=starts)
    bet = _game(session, "MLB", home="CHC", away="MIL", starts_at=starts)
    paused = _game(session, "MLB", home="LAD", away="SD", starts_at=starts)
    for g in (passed, bet, paused):
        world.coverage[str(g.id)] = _cov(g, "MLB", model_status={"state": "PUBLISHED"},
                                         model={"engine_id": "MLB_V18_WHOLE_GAME", "home": 0.6, "away": 0.4,
                                                "created_at": (starts - timedelta(hours=1)).isoformat()})
    world.boards = {DAY: [
        _item(passed, "MLB", plain_reasons=["Adjusted edge 0.8 points is below the 2-point minimum."]),
        _item(bet, "MLB", recommendation="QUALIFIED_BET", selection="CHC ML", american=+110,
              position={"position_id": str(uuid.uuid4()), "stake": 4.0, "status": "OPEN", "result": None,
                        "profit_loss": None, "display_status": "Open — game in progress",
                        "awaiting_settlement": False}),
        _item(paused, "MLB", recommendation="QUALIFIED_BET", funding_pause={"pause": "MLB_V18_FUNDING_PAUSE_V1"}),
    ]}
    games = _by_id(_run(session, settings))
    p = games[str(passed.id)]["stages"]
    assert (p["decision"]["state"], p["decision"]["class"]) == ("DONE", "PASS")
    assert "below the 2-point minimum" in p["decision"]["detail"]
    assert p["bet"]["state"] == "NOT_APPLICABLE" and p["result"]["state"] == "NOT_APPLICABLE"
    assert p["prices"]["code"] == "PRICED_AT_DECISION"                          # judged at the decision, not now
    b = games[str(bet.id)]["stages"]
    assert (b["decision"]["class"], b["bet"]["state"], b["bet"]["stake_usd"], b["bet"]["to_win_usd"]) == (
        "BET", "DONE", 4.0, 4.4)
    assert b["result"]["state"] == "WAITING" and games[str(bet.id)]["next_step"]["kind"] == "WATCH"
    f = games[str(paused.id)]["stages"]
    assert (f["decision"]["state"], f["decision"]["code"]) == ("BLOCKED", "MLB_V18_FUNDING_PAUSE_V1")
    assert (f["bet"]["state"], f["bet"]["label"]) == ("BLOCKED", "Funding paused")


def test_a_postponed_game_is_void_and_an_inactive_policy_blocks_approval(session, settings, world):
    world.policies = set()
    void = _game(session, "MLB", home="NYY", away="BOS", status=GameStatus.POSTPONED)
    live = _game(session, "MLB", home="CHC", away="MIL")
    for g in (void, live):
        world.coverage[str(g.id)] = _cov(g, "MLB", model_status={"state": "SCHEDULED"},
                                         market_status={"state": "NO_TWO_SIDED_QUOTE"})
    games = _by_id(_run(session, settings))
    v = games[str(void.id)]
    assert v["phase"] == "VOID" and v["next_step"]["kind"] == "DONE"
    assert all(v["stages"][s]["state"] in ("NOT_APPLICABLE", "DONE") for s in mc.STAGES), v["stages"]
    a = games[str(live.id)]["stages"]["approval"]
    assert (a["state"], a["code"], a["eligible"]) == ("BLOCKED", "POLICY_INACTIVE", False)
    assert games[str(live.id)]["attention"] == "BLOCKED"


def test_a_game_with_no_data_is_shown_as_missing_never_done(session, settings, world):
    game = _game(session, "MLB", home="NYY", away="BOS")                   # no coverage row at all
    row = _by_id(_run(session, settings))[str(game.id)]
    assert row["stages"]["forecast"]["state"] == "UNKNOWN"
    assert row["stages"]["prices"]["state"] == "UNKNOWN"
    assert all(row["stages"][s]["state"] != "DONE" for s in ("forecast", "prices", "decision", "bet", "result"))


# --------------------------------------------------------------------- NFL --

def _nfl_forecast(session, game, *, snapshot="CURRENT", available, forecast_id=None, home=0.62):
    bundle = ShadowForecastBundle(
        bundle_sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), bundle_id=f"b-{uuid.uuid4().hex[:6]}",
        logical_uri="test://bundle", source_fixture_id="fixture", slate_date=game.starts_at.date(), league="NFL",
        sport="FOOTBALL", status="RESEARCH_SHADOW", generated_at=available, forecast_count=1, quote_count=0,
        candidate_count=0)
    session.add(bundle)
    session.flush()
    row = ShadowForecast(
        bundle_id=bundle.id, forecast_id=forecast_id or f"asp-sports-game-forecast:sha256:{uuid.uuid4().hex}",
        engine_id="NFL_M1_XGBOOST", engine_version="m1", model_artifact_sha256="a" * 64, simulator_id="sim",
        simulator_version="1", simulator_seed=1, event_id="2026_07_KC_DEN", source_event_id="evt",
        event_date=game.starts_at.date(), season=2026, canonical_game_id=game.id, home_team_id="KC",
        away_team_id="DEN", snapshot_type=snapshot, snapshot_version=1, is_ready=True, readiness_score=1.0,
        readiness_threshold=0.5, uncertainty={}, readiness_detail={}, status="RESEARCH_SHADOW",
        scheduled_start_utc=game.starts_at, available_as_of_utc=available, full_home_win_probability=home,
        full_away_win_probability=1 - home, full_expected_home_score=24.0, full_expected_away_score=20.0,
        distributions={}, lineage_detail={})
    session.add(row)
    session.flush()
    return row


def test_nfl_current_forecast_is_provisional_and_missing_eligibility_waits_then_blocks(session, settings, world):
    later = _game(session, "NFL", home="KC", away="DEN", starts_at=ET_NOON + timedelta(hours=4))
    soon = _game(session, "NFL", home="BUF", away="MIA", starts_at=ET_NOON + timedelta(minutes=10))
    final = _game(session, "NFL", home="PHI", away="DAL", starts_at=ET_NOON + timedelta(hours=1))
    _nfl_forecast(session, later, available=ET_NOON - timedelta(hours=6))
    _nfl_forecast(session, soon, available=ET_NOON - timedelta(hours=6))
    _nfl_forecast(session, final, available=ET_NOON - timedelta(hours=6))
    _nfl_forecast(session, final, snapshot="FINAL_PREGAME", available=ET_NOON - timedelta(minutes=2), home=0.7)
    for g in (later, soon, final):
        world.coverage[str(g.id)] = _cov(g, "NFL", market_status={"state": "NO_TWO_SIDED_QUOTE"})
    games = _by_id(_run(session, settings))
    f = games[str(later.id)]["stages"]["forecast"]
    assert (f["state"], f["code"], f["snapshot"]) == ("WAITING", "CURRENT_PROVISIONAL", "CURRENT")
    assert f["due_at"] == (later.starts_at - timedelta(minutes=60)).isoformat() and f["home_win_prob"] == 0.62
    a = games[str(later.id)]["stages"]["approval"]
    assert (a["state"], a["code"]) == ("WAITING", "NO_PAPER_RESEARCH_ELIGIBILITY_RECORD")
    s = games[str(soon.id)]["stages"]
    assert (s["forecast"]["state"], s["forecast"]["code"]) == ("BLOCKED", "NO_FINAL_PREGAME_FORECAST")
    assert (s["approval"]["state"], s["approval"]["code"]) == ("BLOCKED", "NO_PAPER_RESEARCH_ELIGIBILITY_RECORD")
    d = games[str(final.id)]["stages"]["forecast"]
    assert (d["state"], d["snapshot"], d["home_win_prob"]) == ("DONE", "FINAL_PREGAME", 0.7)


def test_an_nfl_decision_on_the_next_utc_slate_date_still_attaches(session, settings, world):
    late = _game(session, "NFL", home="SEA", away="SF", starts_at=datetime(2026, 10, 18, 0, 20, tzinfo=UTC))
    world.coverage[str(late.id)] = _cov(late, "NFL", market_status={"state": "NO_TWO_SIDED_QUOTE"})
    # nfl_paper_decision writes slate_date = the UTC date of the decision (Oct 18 here).
    world.boards = {DAY + timedelta(days=1): [_item(late, "NFL", recommendation="NO_BET")]}
    row = _by_id(_run(session, settings, now=late.starts_at + timedelta(minutes=5)))[str(late.id)]
    assert set(world.board_days) == {DAY, DAY + timedelta(days=1)}
    assert (row["stages"]["decision"]["state"], row["stages"]["decision"]["class"]) == ("DONE", "PASS")


@pytest.mark.parametrize("minutes_to_start", [5, 20])          # window closed / window open
def test_an_nfl_priced_pass_no_qualifying_value_is_a_pass_never_a_block(session, settings, world, monkeypatch,
                                                                         minutes_to_start):
    """nfl_paper_decision records a priced decline as DECLINED / NO_QUALIFYING_VALUE (2026-10-11 DET@ARI)."""

    game = _game(session, "NFL", home="ARI", away="DET", starts_at=ET_NOON + timedelta(minutes=minutes_to_start))
    world.coverage[str(game.id)] = _cov(game, "NFL", market_status={"state": "NO_TWO_SIDED_QUOTE"})
    world.boards = {DAY: [_item(game, "NFL", recommendation="DECLINED", selection="DET ML", american=-240,
                                sportsbook="BetRivers", reason="NO_QUALIFYING_VALUE",
                                recommended_at=(ET_NOON - timedelta(minutes=1)).isoformat(),
                                plain_reasons=["No side of any fresh permitted pair cleared the 2.0-point minimum."])]}
    monkeypatch.setattr(mc, "_reason_codes", lambda ctx, ids: {d: "NO_QUALIFYING_VALUE" for d in ids})
    payload = _run(session, settings)
    row = _by_id(payload)[str(game.id)]
    d = row["stages"]["decision"]
    assert (d["state"], d["class"], d["reason_code"], d["label"]) == ("DONE", "PASS", "NO_QUALIFYING_VALUE", "Passed")
    assert "2.0-point minimum" in d["detail"]
    assert row["stages"]["bet"]["state"] == "NOT_APPLICABLE" and row["stages"]["result"]["state"] == "NOT_APPLICABLE"
    assert row["attention"] != "BLOCKED" and payload["counts"]["blocked"] == 0
    assert mc._plain("NO_QUALIFYING_VALUE") == "no side cleared the policy's value test"


def _ctx_for(now, item):
    return SimpleNamespace(now=now, decisions={item["game_id"]: item}, reasons={}, errors={}, positions={},
                           nhl_results={}, next_run=lambda _id: None)


@pytest.mark.parametrize(("sport", "stored", "code", "retried"), [
    ("NFL", "DECLINED", "QUOTE_TOO_STALE", True),               # 2026-10-11 CHI@GB: 12:30 stale, 12:35 bet
    ("NFL", "DECLINED", "NO_TWO_SIDED_QUOTE", True),
    ("CFB", "DECLINED", "QUOTE_KICKOFF_MISMATCH", True),
    ("CFB", "BLOCKED", "DISCOVERY_NOT_CURRENT", True),
    ("NHL", "DECLINED", "NO_PERMITTED_PROVIDER", True),
    ("MLB", "DECLINED", "QUOTE_TOO_STALE", False),              # MLB promises no in-window retry
    # AISP_SHARED_100_PRIORITY_MLB_NHL_V1: room held for MLB/NHL frees as they decide, so it is retried.
    ("CFB", "DECLINED", "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS", True),
    ("NFL", "DECLINED", "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS", True),
    ("NHL", "DECLINED", "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS", True),
])
def test_a_retryable_decline_inside_the_window_waits_and_is_final_only_after_t15(sport, stored, code, retried):
    game = mc._Game(uuid.uuid4(), sport, ET_NOON + timedelta(minutes=20), "scheduled", "LIVE")
    item = _item(SimpleNamespace(id=game.uuid, starts_at=game.start), sport, recommendation=stored,
                 recommended_at=(ET_NOON - timedelta(minutes=5)).isoformat(), plain_reasons=[])
    ctx = _ctx_for(ET_NOON, item)
    ctx.reasons = {item["decision_id"]: code}
    decision = mc._decision(ctx, game, item, "DECISION_WINDOW")
    bet = mc._bet(ctx, game, item, decision, "DECISION_WINDOW")
    result = mc._result(ctx, game, item, bet, decision, None, "DECISION_WINDOW")
    if retried:
        assert (decision["state"], decision["class"], decision["code"], decision["label"]) == (
            "WAITING", "PENDING", code, "Re-evaluating")
        minutes = 2 if sport == "CFB" else 5                    # CFB_PAPER_CHECKPOINT_INTERVAL_MINUTES default
        assert decision["due_at"] == game.closes.isoformat() and f"re-evaluates every {minutes} minutes" in decision["detail"]
        assert (bet["state"], result["state"]) == ("WAITING", "WAITING")
    else:
        assert decision["state"] in ("DONE", "BLOCKED") and decision["class"] != "PENDING"
    # After T-15 the same record is final.
    late = _ctx_for(game.closes + timedelta(minutes=1), item)
    late.reasons = ctx.reasons
    final = mc._decision(late, game, item, "DECISION_WINDOW")
    if code in mc.NO_ROOM_REASONS:
        assert (final["state"], final["class"], final["label"]) == ("DONE", "PASS", "No room")
        assert "declined for budget, not a no-bet" in final["detail"]
        assert mc._bet(late, game, item, final, "DECISION_WINDOW")["detail"] == "No bet: the shared $100 had no room for it."
    else:
        assert final["state"] in ("DONE", "BLOCKED") and final["class"] in ("NO_PRICE", "BLOCKED")
    assert mc._bet(late, game, item, final, "DECISION_WINDOW")["state"] == "NOT_APPLICABLE"


def test_a_fully_entered_shared_100_is_a_final_budget_decline_inside_the_window():
    """SHARED_ALLOWANCE_EXHAUSTED never frees up: no 'Re-evaluating', and never read as a no-bet."""

    game = mc._Game(uuid.uuid4(), "NHL", ET_NOON + timedelta(minutes=20), "scheduled", "LIVE")
    item = _item(SimpleNamespace(id=game.uuid, starts_at=game.start), "NHL", recommendation="DECLINED",
                 recommended_at=(ET_NOON - timedelta(minutes=5)).isoformat(), plain_reasons=[],
                 reason="SHARED_ALLOWANCE_EXHAUSTED")
    ctx = _ctx_for(ET_NOON, item)
    ctx.reasons = {item["decision_id"]: "SHARED_ALLOWANCE_EXHAUSTED"}
    decision = mc._decision(ctx, game, item, "DECISION_WINDOW")
    assert (decision["state"], decision["class"], decision["label"]) == ("DONE", "PASS", "No room")
    assert decision["detail"] == ("Evaluated at 11:55 AM ET: the shared $100 for the day was already entered; "
                                  "declined for budget, not a no-bet.")
    assert mc._bet(ctx, game, item, decision, "DECISION_WINDOW")["detail"] == "No bet: the shared $100 had no room for it."


def test_the_open_window_names_each_sports_checkpoint_cadence():
    game = mc._Game(uuid.uuid4(), "CFB", ET_NOON + timedelta(minutes=20), "scheduled", "LIVE")
    ctx = SimpleNamespace(now=ET_NOON, decisions={}, reasons={}, errors={}, positions={}, extra={},
                          settings=SimpleNamespace(cfb_production_consumer_enabled=True, cfb_paper_decisions_enabled=True,
                                                   cfb_paper_checkpoint_interval_minutes=2))
    assert "the checkpoint runs every 2 minutes" in mc._decision(ctx, game, None, "DECISION_WINDOW")["detail"]
    ctx.settings.cfb_paper_checkpoint_interval_minutes = 5
    assert "the checkpoint runs every 5 minutes" in mc._decision(ctx, game, None, "DECISION_WINDOW")["detail"]
    assert mc._checkpoint_minutes(SimpleNamespace(), "NHL") == 5


def test_a_position_decided_on_an_earlier_slate_stays_on_the_rescheduled_games_row(session, settings, world):
    """2026-10-16 TB@CLE: bet placed on the 10-16 slate, game moved to 10-18. The bet must show on 10-18."""

    s = _nhl_settings(settings)
    forecast, _block = _in_process_forecast(session)
    decide = NHL_START - timedelta(minutes=20)
    _quotes(session, clock=decide - timedelta(minutes=5))
    _activate(session, s)
    session.commit()
    decision = paper.evaluate(session, s, now=decide, clock=lambda: decide)["decisions"][0]
    assert decision["outcome"] == "QUALIFIED" and decision["position_id"]
    stake = decision["selection"]["terms"]["stake"]
    game = session.get(Game, forecast.game_id)
    game.starts_at = NHL_START + timedelta(days=2)               # rescheduled two days later
    session.commit()
    now = decide + timedelta(hours=1)
    moved = _run(session, s, day=NHL_DAY + timedelta(days=2), now=now)
    row = _by_id(moved)[str(forecast.game_id)]
    st = row["stages"]
    assert (st["decision"]["state"], st["decision"]["class"]) == ("DONE", "BET")
    assert "Oct 20 slate" in st["decision"]["detail"]
    assert (st["bet"]["state"], st["bet"]["status"], st["bet"]["stake_usd"]) == ("DONE", "OPEN", stake)
    assert st["result"]["code"] == "NOT_STARTED" and row["next_step"]["stage"] == "BET"
    assert moved["counts"]["bets_open"] == 1 and moved["counts"]["staked_usd"] == round(stake, 2)
    assert NHL_DAY in world.board_days                            # the decision's own slate board was read
    # The decision's own day no longer lists the game, so the bet is counted once, on the game's day.
    assert str(forecast.game_id) not in _by_id(_run(session, s, day=NHL_DAY, now=now))


# --------------------------------------------------------------------- CFB --

def _cfb_settings(settings, **overrides):
    return settings.model_copy(update={"cfb_production_consumer_enabled": True, "cfb_paper_decisions_enabled": True,
                                       "cfb_odds_api_enabled": True,
                                       "cfb_odds_api_source_effective_from": "2026-09-26T00:00:00Z", **overrides})


def _cfb_game(session, world, *, starts_at, lease="CURRENT", lease_until=None, forecast_id="cfb-forecast-1",
              source_id="401111111"):
    game = _game(session, "CFB", home=f"CFBD{source_id[-3:]}1", away=f"CFBD{source_id[-3:]}2", starts_at=starts_at,
                 mode="RESEARCH_SHADOW", home_name=f"Georgia {source_id[-3:]}", away_name=f"Auburn {source_id[-3:]}")
    session.add(ExternalIdMapping(entity_type="game", entity_id=game.id, source="CFBD", external_id=source_id))
    session.flush()
    world.coverage[str(game.id)] = _cov(
        game, "CFB", model={"engine_id": "CFB_GEN1_JOINT_BASELINE", "home": 0.64, "away": 0.36, "home_two_way": 0.64,
                            "created_at": (starts_at - timedelta(days=2)).isoformat(), "forecast_id": forecast_id},
        model_status={"state": "ELIGIBILITY_CURRENT" if lease == "CURRENT" else "ELIGIBILITY_EXPIRED",
                      "eligibility": {"state": lease,
                                      "lease_valid_until": (lease_until or starts_at).isoformat()}})
    return game


def _cfb_record(session, forecast_id, *, published):
    from app.services.nfl_paper_eligibility import KIND, experiment_key

    frozen = {"forecastId": forecast_id, "homeWinProbability": 0.64, "awayWinProbability": 0.36, "tieProbability": 0.0,
              "executionAllowed": False, "modelPromotionAllowed": False,
              "configurationIdentity": CFB.controlling_configuration}
    sha = hashlib.sha256(forecast_id.encode() + repr(published).encode()).hexdigest()
    session.add(ResearchSupplement(
        supplement_sha256=sha, supplement_id=f"fixture-cfb-{sha[:8]}", contract_version="AISP_RESEARCH_SUPPLEMENT_V1",
        kind=KIND, sport="CFB", event_id="cfbd:401111111", forecast_id=forecast_id,
        artifact_version=CFB.eligibility_contract, simulation_kind="NONE", published_utc=published,
        imported_at=published + timedelta(minutes=1),
        evidence={"binding": {"eventId": "cfbd:401111111", "engineId": "CFB_GEN1_JOINT_BASELINE",
                              "forecastId": forecast_id, "modelArtifactSha256": "b" * 64,
                              "configurationIdentity": CFB.controlling_configuration},
                  "governance": {"executionAllowedInAisp": False, "modelPromotionAllowed": False,
                                 "wageringAuthority": False, "researchShadowOnly": True}},
        payload={"summaries": {"authorization": {
            "experimentId": experiment_key(CFB.policy_version, CFB.sport, CFB.season_id),
            "policyVersion": CFB.policy_version, "mode": "VIRTUAL_RESEARCH_ONLY",
            "consumerVirtualExperimentAuthorized": True, "realMoneyAuthorized": False},
            "frozenConfiguration": frozen, "probabilitySemantics": {"settlementMustBeExplicit": True}}}))
    session.flush()


def _odds_pair(session, game, *, home, away, observed, kickoff=None, book="draftkings"):
    sportsbook = session.scalar(select(Sportsbook).where(Sportsbook.key == book))
    if sportsbook is None:
        sportsbook = Sportsbook(key=book, name=book.title())
        session.add(sportsbook)
        session.flush()
    market = session.scalar(select(Market).where(Market.game_id == game.id, Market.market_type == MarketType.MONEYLINE))
    if market is None:
        market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
        session.add(market)
        session.flush()
    for key, american in (("HOME:None", home), ("AWAY:None", away)):
        outcome = session.scalar(select(MarketOutcome).where(MarketOutcome.market_id == market.id,
                                                             MarketOutcome.outcome_key == key))
        if outcome is None:
            outcome = MarketOutcome(market_id=market.id, outcome_key=key, label=key.split(":")[0])
            session.add(outcome)
            session.flush()
        decimal_odds = 1 + (american / 100 if american > 0 else 100 / abs(american))
        session.add(OddsSnapshot(
            market_outcome_id=outcome.id, sportsbook_id=sportsbook.id, observed_at=observed,
            available_as_of=observed + timedelta(seconds=30), american_odds=american, decimal_odds=decimal_odds,
            source="THE_ODDS_API", source_mode="LIVE",
            raw_payload={"event_commence_time": (kickoff or game.starts_at).isoformat().replace("+00:00", "Z"),
                         "quote_clock_source": "MARKET_LAST_UPDATE"}))
    session.flush()


def test_cfb_approval_done_and_prices_held_through_the_window(session, settings, world):
    s = _cfb_settings(settings)
    starts = ET_NOON + timedelta(minutes=50)                       # inside the T-75..T-35 buy window
    game = _cfb_game(session, world, starts_at=starts, lease_until=starts + timedelta(hours=1))
    _cfb_record(session, "cfb-forecast-1", published=starts - timedelta(hours=5))
    _odds_pair(session, game, home=-180, away=+150, observed=ET_NOON - timedelta(minutes=5))
    _odds_pair(session, game, home=-175, away=+155, observed=ET_NOON - timedelta(minutes=3), book="fanduel")
    _odds_pair(session, game, home=-120, away=+100, observed=ET_NOON - timedelta(minutes=1),
               kickoff=starts + timedelta(hours=2), book="betmgm")          # retained at another kickoff
    row = _by_id(_run(session, s))[str(game.id)]
    st = row["stages"]
    assert (st["forecast"]["state"], st["forecast"]["made_at_basis"]) == ("DONE", "PRODUCER_PUBLISHED")
    assert (st["approval"]["state"], st["approval"]["route"], st["approval"]["eligible"]) == (
        "DONE", "CFB_LEASE_AND_RECORD", True)
    assert (st["prices"]["state"], st["prices"]["fresh_books"], st["prices"]["collector"]) == ("DONE", 2, "ON")
    assert st["prices"]["best"]["home"] == {"american": -175, "sportsbook": "Fanduel", "provider": "The Odds API",
                                            "observed_at": (ET_NOON - timedelta(minutes=3)).isoformat()}
    assert st["live"]["state"] == "NOT_APPLICABLE" and st["live"]["code"] == "NO_LIVE_FEED"
    assert row["source_game_id"] == "401111111" and row["home"]["abbr"] == "Georgia 111"
    assert row["next_step"]["stage"] == "DECISION" and row["next_step"]["at"] == (starts - timedelta(minutes=30)).isoformat()


def test_cfb_bulk_pairs_match_the_decision_engine_rule(session, settings, world):
    from app.cfb.paper_decision import kickoff_bound_pairs

    starts = ET_NOON + timedelta(hours=2)
    game = _cfb_game(session, world, starts_at=starts)
    _odds_pair(session, game, home=-180, away=+150, observed=ET_NOON - timedelta(minutes=50))
    _odds_pair(session, game, home=-170, away=+145, observed=ET_NOON - timedelta(minutes=10))
    _odds_pair(session, game, home=-200, away=+170, observed=ET_NOON - timedelta(minutes=30), book="fanduel")
    _odds_pair(session, game, home=-110, away=-110, observed=ET_NOON, kickoff=starts - timedelta(hours=1),
               book="betmgm")
    ctx = mc._Ctx(session, settings, DAY, ET_NOON)
    bulk = mc._cfb_bound_pairs(ctx, [mc._Game(game.id, "CFB", starts, "scheduled", "RESEARCH_SHADOW")])[str(game.id)]
    engine = kickoff_bound_pairs(session, game.id, starts)
    keys = ("sportsbook", "source", "home_american", "away_american", "pair_observed_at", "home_observed_at",
            "away_observed_at")
    assert [{k: p[k] for k in keys} for p in bulk] == [{k: p[k] for k in keys} for p in engine]
    assert len(bulk) == 2


def test_cfb_expired_lease_waits_before_the_window_and_blocks_inside_it(session, settings, world):
    s = _cfb_settings(settings)
    before = _cfb_game(session, world, starts_at=ET_NOON + timedelta(hours=3), lease="EXPIRED_LEASE",
                       lease_until=ET_NOON - timedelta(minutes=20), source_id="401111112")
    inside = _cfb_game(session, world, starts_at=ET_NOON + timedelta(minutes=20), lease="EXPIRED_LEASE",
                       lease_until=ET_NOON - timedelta(minutes=20), source_id="401111113")
    games = _by_id(_run(session, s))
    a = games[str(before.id)]["stages"]["approval"]
    assert (a["state"], a["code"], a["due_at"]) == ("WAITING", "EXPIRED_LEASE",
                                                   (before.starts_at - timedelta(minutes=30)).isoformat())
    b = games[str(inside.id)]["stages"]["approval"]
    assert (b["state"], b["code"], b["label"]) == ("BLOCKED", "EXPIRED_LEASE", "Lease expired")
    assert games[str(inside.id)]["attention"] == "BLOCKED"
    # The countdown is a deadline a renewal must beat, not a time approval happens by itself.
    step = games[str(before.id)]["next_step"]
    assert (step["stage"], step["kind"], step["label"]) == ("APPROVAL", "WAIT", "Lease must renew in")


def _manifest(session, *, created, lease_until, forecast_id="cfb-forecast-1"):
    from app.models import CfbDiscoveryManifest, CfbDiscoveryObservation

    sha = hashlib.sha256(repr(created).encode()).hexdigest()
    manifest = CfbDiscoveryManifest(manifest_sha256=sha, manifest_bytes=100, producer_created_utc=created,
                                    orientation_source="fixture", first_verified_at=created + timedelta(minutes=1))
    session.add(manifest)
    session.flush()
    session.add(CfbDiscoveryObservation(
        manifest_id=manifest.id, manifest_sha256=sha, forecast_id=forecast_id, source_game_id="401111111",
        package_address="p" * 64, currently_eligible=True, eligibility_valid_until_utc=lease_until,
        lease_at_check="CURRENT", verified=True, checked_at=created + timedelta(minutes=1)))
    session.flush()


@pytest.mark.parametrize(("made_ago", "lease_left", "state"), [
    (timedelta(minutes=8), timedelta(minutes=6), "OK"),          # a current manifest, lease running
    (timedelta(minutes=20), timedelta(minutes=-5), "OK"),        # between imports: lapsed under one cadence
    (timedelta(minutes=29), timedelta(minutes=-16), "STALE"),    # lapsed over a cadence: no renewal arrived
    (timedelta(minutes=40), timedelta(minutes=30), "STALE"),     # two cadences without a newer manifest
])
def test_cfb_producer_is_stale_once_its_leases_lapse_without_renewal(session, settings, world, made_ago, lease_left,
                                                                      state):
    _cfb_game(session, world, starts_at=ET_NOON + timedelta(hours=3))
    _manifest(session, created=ET_NOON - made_ago, lease_until=ET_NOON + lease_left)
    producers = {p["sport"]: p for p in _run(session, _cfb_settings(settings))["health"]["producers"]}
    cfb = producers["CFB"]
    assert cfb["state"] == state, cfb
    if state == "STALE" and lease_left < timedelta(0):
        assert "no renewal has been imported" in cfb["detail"]


def test_cfb_grant_expiry_and_owner_switch_block(session, settings, world):
    starts = ET_NOON + timedelta(hours=3)
    game = _cfb_game(session, world, starts_at=starts, lease_until=starts)
    _cfb_record(session, "cfb-forecast-1", published=starts - timedelta(hours=5))
    world.grant = {"sha256": "g" * 64, "valid_until": ET_NOON - timedelta(hours=1)}
    a = _by_id(_run(session, _cfb_settings(settings)))[str(game.id)]["stages"]["approval"]
    assert (a["state"], a["code"]) == ("BLOCKED", "POLICY_GRANT_EXPIRED")
    off = _by_id(_run(session, _cfb_settings(settings, cfb_paper_decisions_enabled=False)))[str(game.id)]["stages"]
    assert (off["approval"]["state"], off["approval"]["code"]) == ("BLOCKED", "OWNER_INACTIVE")
    assert (off["decision"]["state"], off["decision"]["code"]) == ("BLOCKED", "OWNER_INACTIVE")


def test_cfb_price_fresh_now_but_expiring_before_the_window_waits_for_the_scheduled_buy(session, settings, world):
    starts = ET_NOON + timedelta(hours=8)
    game = _cfb_game(session, world, starts_at=starts, lease_until=starts)
    _odds_pair(session, game, home=-180, away=+150, observed=ET_NOON - timedelta(minutes=5))
    p = _by_id(_run(session, _cfb_settings(settings)))[str(game.id)]["stages"]["prices"]
    assert (p["state"], p["code"], p["due_at"]) == ("WAITING", "BUY_SCHEDULED", (starts - timedelta(minutes=75)).isoformat())
    off = _by_id(_run(session, _cfb_settings(settings, cfb_odds_api_enabled=False)))[str(game.id)]["stages"]["prices"]
    assert (off["state"], off["code"]) == ("WAITING", "EXPIRES_BEFORE_WINDOW")


# --------------------------------------------------------------------- NHL --

def _nhl_settings(settings, **overrides):
    return settings.model_copy(update={"shared_daily_allowance_effective_from": "2026-10-10T20:30:00Z",
                                       "nhl_paper_checkpoint_enabled": True, "nhl_paper_decisions_enabled": True,
                                       "nhl_odds_api_enabled": True, **overrides})


def test_nhl_in_process_game_ready_before_its_window(session, settings, world):
    s = _nhl_settings(settings)
    forecast, _block = _in_process_forecast(session)
    now = NHL_START - timedelta(minutes=50)
    _quotes(session, clock=now - timedelta(minutes=5), received=now - timedelta(minutes=4))
    _activate(session, s)
    # The generator's last run: COMPLETED, with one back-to-back game it could not forecast yet.
    session.add(JobRun(job_name="generate-nhl-forecasts", status=RunStatus.SUCCEEDED,
                       started_at=now - timedelta(hours=2), completed_at=now - timedelta(hours=2) + timedelta(seconds=9),
                       created_at=now - timedelta(hours=2),
                       result={"status": "COMPLETED", "excluded": [
                           {"sourceGameId": "2026020998", "scheduledStartUtc": "2026-10-20T16:00:00Z",
                            "reason": "ALREADY_STARTED_OR_NOT_PREGAME", "targetDate": "2026-10-20"},
                           {"sourceGameId": "2026020999", "scheduledStartUtc": "2026-10-21T00:00:00Z",
                            "reason": "REST_INCOMPLETE_EARLIER_GAME_UNSTARTED", "targetDate": "2026-10-20"},
                           {"sourceGameId": "2026021000", "scheduledStartUtc": "2026-10-22T00:00:00Z",
                            "reason": "REST_INCOMPLETE_EARLIER_GAME_UNSTARTED", "targetDate": "2026-10-21"}]}))
    session.commit()
    payload = _run(session, s, day=NHL_DAY, now=now)
    row = _by_id(payload)[str(forecast.game_id)]
    st = row["stages"]
    assert (st["forecast"]["state"], st["forecast"]["made_at_basis"], st["forecast"]["source"]) == (
        "DONE", "AISP_IN_PROCESS_GENERATED", "AISP in-process NHL Gen1")
    assert st["forecast"]["home_win_prob"] == round(forecast.final_home_probability, 4)
    assert (st["approval"]["state"], st["approval"]["route"]) == ("DONE", "NHL_IN_PROCESS")
    assert (st["prices"]["state"], st["prices"]["code"], st["prices"]["books"]) == ("DONE", "PRICE_HELD", 1)
    assert st["prices"]["best"]["home"]["american"] == -150 and st["prices"]["best"]["away"]["american"] == 135
    assert (st["decision"]["state"], st["decision"]["code"]) == ("WAITING", "WINDOW_NOT_OPEN")
    assert row["next_step"] == {"stage": "DECISION", "kind": "WAIT", "at": (NHL_START - timedelta(minutes=30)).isoformat(),
                                "label": "Decision in", "text": st["decision"]["detail"]}
    assert row["source_game_id"] == "2026020250" and row["attention"] == "NONE"
    assert row["clocks"]["buy_opens_at"] == (NHL_START - timedelta(minutes=75)).isoformat()
    sports = {x["sport"]: x for x in payload["sports"]}
    assert sports["NHL"]["games"] == 1 and sports["NHL"]["owner_active"] is True
    assert sports["NHL"]["pending_games"] == [{"source_game_id": "2026020999", "starts_at": "2026-10-21T00:00:00+00:00",
                                               "reason": "REST_INCOMPLETE_EARLIER_GAME_UNSTARTED"}]
    assert {p["sport"]: p["state"] for p in payload["health"]["producers"]}["NHL"] == "OK"


def test_nhl_rest_incomplete_blocks_approval(session, settings, world):
    s = _nhl_settings(settings)
    forecast, _block = _in_process_forecast(session)
    forecast.producer_evidence_ref = {**forecast.producer_evidence_ref, "restComplete": False}
    session.flush()
    _activate(session, s)
    row = _by_id(_run(session, s, day=NHL_DAY, now=NHL_START - timedelta(hours=2)))[str(forecast.game_id)]
    a = row["stages"]["approval"]
    assert (a["state"], a["code"]) == ("BLOCKED", "REST_FEATURES_NOT_COMPLETE_AT_GENERATION")
    assert "back-to-back" in a["detail"] and row["attention"] == "BLOCKED"
    assert (row["next_step"]["kind"], row["next_step"]["stage"]) == ("BLOCKED", "APPROVAL")


@pytest.mark.parametrize("overrides,code", [
    ({"nhl_paper_decisions_enabled": False}, "ACTIVATION_CONTROL_OFF"),
    ({"shared_daily_allowance_effective_from": None}, "SHARED_ALLOWANCE_NOT_EFFECTIVE"),
])
def test_nhl_owner_gates_block_approval(session, settings, world, overrides, code):
    forecast, _block = _in_process_forecast(session)
    _activate(session, _nhl_settings(settings))
    s = _nhl_settings(settings, **overrides)
    payload = _run(session, s, day=NHL_DAY, now=NHL_START - timedelta(hours=2))
    a = _by_id(payload)[str(forecast.game_id)]["stages"]["approval"]
    assert (a["state"], a["code"]) == ("BLOCKED", code)
    if code == "SHARED_ALLOWANCE_NOT_EFFECTIVE":
        assert payload["allowance"]["state"] == "NOT_IN_FORCE" and payload["allowance"]["room_usd"] is None


def test_nhl_without_a_price_waits_for_the_buy_window_then_blocks(session, settings, world):
    s = _nhl_settings(settings)
    forecast, _block = _in_process_forecast(session)
    _activate(session, s)
    early = _by_id(_run(session, s, day=NHL_DAY, now=NHL_START - timedelta(hours=3)))[str(forecast.game_id)]
    p = early["stages"]["prices"]
    assert (p["state"], p["code"], p["due_at"]) == ("WAITING", "BUY_SCHEDULED",
                                                   (NHL_START - timedelta(minutes=75)).isoformat())
    late = _by_id(_run(session, s, day=NHL_DAY, now=NHL_START - timedelta(minutes=33)))[str(forecast.game_id)]
    assert (late["stages"]["prices"]["state"], late["stages"]["prices"]["code"]) == ("BLOCKED", "NO_TWO_SIDED_QUOTE")
    assert late["attention"] == "BLOCKED"


def test_nhl_bulk_quotes_feed_the_lifecycle_pairing_exactly(session, settings, world):
    forecast, _block = _in_process_forecast(session)
    _quotes(session, clock=NHL_START - timedelta(minutes=50))
    _quotes(session, home=-145, away=+130, clock=NHL_START - timedelta(minutes=40))
    moment = NHL_START - timedelta(minutes=20)
    ctx = mc._Ctx(session, settings, NHL_DAY, moment)
    game = mc._Game(forecast.game_id, "NHL", NHL_START, "scheduled", "RESEARCH_SHADOW")
    held = mc._nhl_held_quotes(ctx, [game])[str(forecast.game_id)]
    bulk = paper.native_pairs(session, forecast.game_id, moment=moment, starts_at=NHL_START,
                              original_start=NHL_START, held={"quotes": held})
    direct = paper.native_pairs(session, forecast.game_id, moment=moment, starts_at=NHL_START,
                                original_start=NHL_START)
    assert bulk == direct and bulk[0] and bulk[0][0]["home_american"] == -145


def test_nhl_bet_result_and_settlement_flow_through_the_stages(session, settings, world, monkeypatch):
    from app.services import shared_daily_allowance

    s = _nhl_settings(settings)
    forecast, block = _in_process_forecast(session)
    decide = NHL_START - timedelta(minutes=20)
    _quotes(session, clock=decide - timedelta(minutes=5))
    _activate(session, s)
    session.commit()
    decision = paper.evaluate(session, s, now=decide, clock=lambda: decide)["decisions"][0]
    assert decision["outcome"] == "QUALIFIED" and decision["position_id"]
    stake = decision["selection"]["terms"]["stake"]

    def no_lock(*args, **kwargs):
        raise AssertionError("room() takes the writers' advisory lock; Mission Control must never call it")
    monkeypatch.setattr(shared_daily_allowance, "room", no_lock)

    placed = _run(session, s, day=NHL_DAY, now=decide + timedelta(minutes=2))
    row = _by_id(placed)[str(forecast.game_id)]
    st = row["stages"]
    assert (st["decision"]["class"], st["decision"]["reason_code"]) == ("BET", "ALL_GATES_PASSED")
    assert (st["bet"]["state"], st["bet"]["stake_usd"], st["bet"]["status"]) == ("DONE", stake, "OPEN")
    assert (st["prices"]["state"], row["phase"]) == ("DONE", "DECISION_WINDOW")
    assert row["next_step"]["label"] == "Puck drop" and row["next_step"]["at"] == NHL_START.isoformat()
    assert placed["allowance"]["entered_usd"] == round(stake, 2) and placed["allowance"]["entries"] == 1
    assert placed["allowance"]["room_usd"] == round(100 - stake, 2) and placed["allowance"]["state"] == "ROOM"
    assert placed["counts"]["bets_open"] == 1 and placed["counts"]["staked_usd"] == round(stake, 2)
    sweat = placed["sweating"][0]
    assert (sweat["state"], sweat["sport"], sweat["stake_usd"]) == ("UPCOMING", "NHL", stake)
    # The sport's own game id travels with the bet, so its link works from any day's board.
    assert sweat["source_game_id"] == "2026020250"
    assert _run(session, s, day=NHL_DAY + timedelta(days=1), now=decide + timedelta(minutes=2))["sweating"][0][
        "source_game_id"] == "2026020250"

    played = _run(session, s, day=NHL_DAY, now=NHL_START + timedelta(hours=1))
    p_row = _by_id(played)[str(forecast.game_id)]
    assert p_row["phase"] == "STARTED" and p_row["next_step"]["kind"] == "WATCH"
    assert played["sweating"][0]["state"] == "STARTED_NO_LIVE_FEED"

    side = decision["selection"]["side"]
    _result(session, block, reg=(2, 2), decided="OVERTIME", ot=(1, 0) if side == "HOME" else (0, 1))
    final = _run(session, s, day=NHL_DAY, now=NHL_START + timedelta(hours=3))
    f_row = _by_id(final)[str(forecast.game_id)]
    assert f_row["phase"] == "FINAL" and f_row["stages"]["result"]["code"] == "AWAITING_SETTLEMENT"
    assert f_row["stages"]["result"]["final"]["label"] == "Final OT"
    assert f_row["next_step"]["label"] == "Settlement"
    assert final["sweating"][0]["state"] == "FINAL_AWAITING_SETTLEMENT"

    paper.settle(session, s, now=NHL_START + timedelta(hours=4))
    settled = _run(session, s, day=NHL_DAY, now=NHL_START + timedelta(hours=5))
    r = _by_id(settled)[str(forecast.game_id)]
    assert (r["stages"]["result"]["state"], r["stages"]["result"]["outcome"]) == ("DONE", "WIN")
    assert r["next_step"]["kind"] == "DONE" and r["next_step"]["text"].startswith("W +$")
    assert settled["sweating"] == [] and settled["counts"]["bets_settled"] == 1
    assert settled["counts"]["profit_usd"] == r["stages"]["result"]["profit_usd"] > 0


def test_nhl_bankroll_is_its_own_thousand(session, settings, world):
    _activate(session, _nhl_settings(settings))
    banks = {b["sport"]: b for b in _run(session, _nhl_settings(settings))["bankrolls"]}
    assert set(banks) == {"MLB", "NFL", "CFB", "NBA", "NHL"}
    assert (banks["NHL"]["funded"], banks["NHL"]["starting_usd"], banks["NHL"]["bankroll_usd"]) == (True, 1000.0, 1000.0)
    assert banks["MLB"]["funded"] is False


# --------------------------------------------------------- allowance/health --

def test_allowance_before_and_after_its_clock(session, settings, world):
    unset = _run(session, settings.model_copy(update={"shared_daily_allowance_effective_from": None}))["allowance"]
    assert (unset["state"], unset["in_force"], unset["effective_from"], unset["room_usd"]) == (
        "NOT_IN_FORCE", False, None, None)
    future = settings.model_copy(update={"shared_daily_allowance_effective_from": ET_NOON + timedelta(days=1)})
    later = _run(session, future)["allowance"]
    assert later["state"] == "NOT_IN_FORCE" and later["effective_from"] == (ET_NOON + timedelta(days=1)).isoformat()
    on = _run(session, settings.model_copy(update={"shared_daily_allowance_effective_from": ET_NOON - timedelta(days=1)}))
    assert (on["allowance"]["state"], on["allowance"]["room_usd"], on["allowance"]["limit_usd"]) == ("ROOM", 100.0, 100.0)
    assert on["allowance"]["basis"] == "ALL_OFFICIAL_SPORTS_ENTERED_ON_EASTERN_SLATE_DATE_ANY_STATUS"


def _job(session, name, status, started, error=None):
    session.add(JobRun(job_name=name, status=status, started_at=started,
                       completed_at=None if status == RunStatus.RUNNING else started + timedelta(seconds=20),
                       error_message=error, created_at=started))
    session.flush()


def test_job_health_maps_runs_and_never_leaks_an_error_message(session, settings, world, monkeypatch):
    from app.jobs import scheduler as jobs_scheduler

    monkeypatch.setattr(jobs_scheduler, "scheduler_is_running", lambda: True)
    monkeypatch.setattr(jobs_scheduler, "scheduler_started_at", lambda: ET_NOON - timedelta(hours=5))
    registered = {"asp-dc-nhl-paper-checkpoint", "asp-nhl-odds-api", "asp-finalize-games", "asp-nhl-finals-espn"}
    monkeypatch.setattr(jobs_scheduler, "scheduler_next_run_time",
                        lambda job_id: ET_NOON + timedelta(minutes=3) if job_id in registered else None)
    _job(session, "evaluate-nhl-paper-checkpoint", RunStatus.SUCCEEDED, ET_NOON - timedelta(minutes=4))
    _job(session, "collect-nhl-odds-api", RunStatus.SUCCEEDED, ET_NOON - timedelta(minutes=30))
    _job(session, "collect-nhl-odds-api", RunStatus.FAILED, ET_NOON - timedelta(minutes=2),
         error="password=hunter2 SECRET-TOKEN-123")
    _job(session, "finalize-games", RunStatus.RUNNING, ET_NOON - timedelta(minutes=1))
    start = datetime(2026, 10, 17, 23, 0, tzinfo=UTC)
    _in_process_forecast(session, start=start, published=start - timedelta(hours=3))
    payload = _run(session, _nhl_settings(settings))
    jobs = {j["key"]: j for j in payload["health"]["jobs"]}
    assert (jobs["nhl-decisions"]["state"], jobs["nhl-decisions"]["last_status"]) == ("OK", "succeeded")
    assert jobs["nhl-decisions"]["next_run_at"] == (ET_NOON + timedelta(minutes=3)).isoformat()
    assert (jobs["nhl-prices"]["state"], jobs["nhl-prices"]["last_success_at"]) == (
        "FAILED", (ET_NOON - timedelta(minutes=30, seconds=-20)).isoformat())
    assert jobs["settlement"]["state"] == "RUNNING"
    assert jobs["nhl-results"]["state"] == "NEVER_RUN"
    assert jobs["mlb-live"]["state"] == "NOT_REGISTERED" and jobs["mlb-live"]["registered"] is False
    assert payload["health"]["scheduler"]["state"] == "RUNNING" and payload["health"]["overall"] == "DEGRADED"
    assert any("last run failed" in issue for issue in payload["health"]["issues"])
    text = json.dumps(payload)
    assert "hunter2" not in text and "SECRET-TOKEN" not in text
    # A FAILED NHL job puts every NHL game on WATCH.
    assert all(g["attention"] in ("WATCH", "BLOCKED") for g in payload["games"] if g["sport"] == "NHL")


def test_occurrence_review_alerts_surface_as_one_health_issue(session, settings, world):
    """An MLB position settled without occurrence verification (postponed/moved game) is never silent."""

    from app.bankroll.occurrence import REVIEW_ALERT_ACTION
    from app.models import AuditEvent

    def wording(issues):
        return [issue for issue in issues if "without occurrence verification" in issue]

    assert wording(_run(session, settings)["health"]["issues"]) == []
    for hours in (2, 5, 24 * 4):                                     # the last one is outside the window
        session.add(AuditEvent(action=REVIEW_ALERT_ACTION, entity_type="official_asp_position",
                               entity_id=uuid.uuid4(), occurred_at=ET_NOON - timedelta(hours=hours),
                               metadata_json={}))
    session.commit()
    health = _run(session, settings)["health"]
    [issue] = wording(health["issues"])
    assert issue.startswith("2 MLB paper position(s) settled without occurrence verification")
    assert health["overall"] == "DEGRADED"


def test_a_stopped_scheduler_is_down_and_said_plainly(session, settings, world):
    health = _run(session, settings.model_copy(update={"scheduler_enabled": True}))["health"]
    assert (health["overall"], health["scheduler"]["state"]) == ("DOWN", "NOT_RUNNING")
    assert "not running" in health["issues"][0]
    off = _run(session, settings)["health"]
    assert off["scheduler"]["state"] == "DISABLED"


# ------------------------------------------------------------- isolation --

def test_one_failing_sport_is_read_failed_and_never_blanks_the_page(session, settings, world, monkeypatch):
    mlb = _game(session, "MLB", home="NYY", away="BOS")
    cfb = _game(session, "CFB", home="CFBD1", away="CFBD2", home_name="Georgia", away_name="Auburn")
    world.coverage = {str(mlb.id): _cov(mlb, "MLB", model_status={"state": "SCHEDULED"},
                                        market_status={"state": "NO_TWO_SIDED_QUOTE"}),
                      str(cfb.id): _cov(cfb, "CFB")}
    session.commit()                      # a failed read rolls the (read-only) transaction back

    def boom(ctx, games):
        raise RuntimeError("secret detail that must not leak")
    monkeypatch.setitem(mc.ADAPTERS, "CFB", boom)
    payload = _run(session, settings)
    sports = {s["sport"]: s for s in payload["sports"]}
    assert (sports["CFB"]["read_state"], sports["CFB"]["error_type"]) == ("READ_FAILED", "RuntimeError")
    assert sports["MLB"]["read_state"] == "OK"
    games = _by_id(payload)
    assert {games[str(cfb.id)]["stages"][s]["state"] for s in ("forecast", "approval", "prices")} == {"UNKNOWN"}
    assert games[str(mlb.id)]["stages"]["forecast"]["state"] == "WAITING"
    assert "secret detail" not in json.dumps(payload)
    # The failure is named in read_errors and in the health strip's issue list, not only on the sport.
    assert payload["read_errors"] == {"sport:CFB": "RuntimeError"}
    assert any("College Football stages" in issue for issue in payload["health"]["issues"])
    assert payload["health"]["overall"] in ("DEGRADED", "DOWN")


def test_a_failed_open_bets_read_is_null_never_an_empty_rail(session, settings, world, monkeypatch):
    _game(session, "MLB", home="NYY", away="BOS")
    session.commit()

    def broken(ctx, scores):
        raise RuntimeError("scoreboard exploded")
    monkeypatch.setattr(mc, "_sweating", broken)
    payload = _run(session, settings)
    assert payload["sweating"] is None and payload["read_errors"]["sweating"] == "RuntimeError"
    assert any("open bets" in issue for issue in payload["health"]["issues"])
    assert "scoreboard exploded" not in json.dumps(payload)


def test_a_failed_shared_read_degrades_to_unknown(session, settings, world, monkeypatch):
    mlb = _game(session, "MLB", home="NYY", away="BOS")
    session.commit()                      # a failed read rolls the (read-only) transaction back

    def broken(session, day, now=None, *, as_of=None):
        raise ValueError("coverage exploded")
    monkeypatch.setattr(command_center, "market_coverage", broken)
    payload = _run(session, settings)
    assert payload["read_errors"] == {"coverage": "ValueError"}
    row = _by_id(payload)[str(mlb.id)]
    assert row["stages"]["forecast"]["state"] == "UNKNOWN"
    assert {s["sport"]: s["error_type"] for s in payload["sports"]}["MLB"] == "CoverageUnavailable"
    assert payload["health"]["overall"] in ("DEGRADED", "DOWN")


def test_fixture_games_never_appear(session, settings, world):
    _game(session, "MLB", home="NYY", away="BOS", mode="FIXTURE")
    _game(session, "NHL", home="TOR", away="MTL", mode="DEMO")
    assert _run(session, settings)["games"] == []


# ------------------------------------------------------------------ route --

def test_route_contract_range_default_and_read_only(client, session, settings, world, monkeypatch):
    from app.core.time import local_today

    def no_commit():
        raise AssertionError("Mission Control must never commit")
    monkeypatch.setattr(session, "commit", no_commit)
    today = local_today(settings.display_timezone)
    response = client.get(f"/api/v1/mission-control?date={today.isoformat()}")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "AISP_MISSION_CONTROL_V1" and body["provider_calls_made"] == 0
    assert body["is_today"] is True and body["refresh"]["client_poll_seconds"] == 30
    assert set(body) >= {"health", "allowance", "bankrolls", "sports", "games", "sweating", "counts", "window",
                         "generated_at", "authority"}
    assert client.get("/api/v1/mission-control").json()["date"] == today.isoformat()
    assert client.get(f"/api/v1/mission-control?date={(today + timedelta(days=3)).isoformat()}").status_code == 200
    assert client.get(f"/api/v1/mission-control?date={(today - timedelta(days=1)).isoformat()}").status_code == 200
    assert client.get(f"/api/v1/mission-control?date={(today + timedelta(days=4)).isoformat()}").status_code == 400
    assert client.get(f"/api/v1/mission-control?date={(today - timedelta(days=2)).isoformat()}").status_code == 400
    assert client.post("/api/v1/mission-control").status_code == 405
