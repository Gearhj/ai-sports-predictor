"""NHL live (2026-10-16 simplification): the four lanes, integrated, end to end. TEST FIXTURES ONLY.

generate-nhl-forecasts (AISP in-process frozen Gen1, schedule bytes from the
producer's retained Oct 13 capture subset served by an ``httpx.MockTransport``;
a day-ahead run that must skip the back-to-back game, then the game-day run)
-> collect-nhl-odds-api (an Odds API v4-shaped fixture through the real adapter
normaliser; no request) -> the NHL paper checkpoint (AISP in-process eligibility
route) -> ingest-nhl-finals-espn (an ESPN-shaped fixture; no request) ->
``paper.settle``. Every row lands in the throwaway in-memory database. Nothing
here is production evidence; prices and the score are invented.

What it proves that the per-branch suites cannot: the generator's rows carry
exactly the values the paper eligibility route and the price lane pin, the game
and team identities the generator creates are the ones the Odds API binder and
the ESPN matcher bind to, and every clock stays honest across the hand-offs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.data_sources.base import AdapterResponse, SourceEnvelope
from app.data_sources.odds import TheOddsAPIAdapter
from app.models import Game, GameStatus, OfficialAspPosition, ShadowExperimentDecision
from app.models.nhl_research import NhlGameResult, NhlResearchForecast
from app.nhl import espn_results as er
from app.nhl import gen1_frozen as frozen
from app.nhl import inprocess_generator as gen
from app.nhl import odds_api_price as nhl_odds
from app.nhl import paper
from tests.test_nhl_espn_results import FIXTURES as ESPN_FIXTURES
from tests.test_nhl_espn_results import Fetch, _scores
from tests.test_nhl_inprocess_generator import OCT13_RUN, _run, _schedule
from tests.test_nhl_odds_api_source import ON as ODDS_ON
from tests.test_nhl_odds_api_source import _measured_balance, raw_event
from tests.test_nhl_paper_lifecycle import _activate, _settings

#: A real 2026-27 row of the Oct 13 capture: DAL at COL, 8:30 PM ET Wed Oct 14.
SOURCE_GAME = "2026020107"
START = datetime(2026, 10, 15, 0, 30, tzinfo=UTC)
FETCHED = START - timedelta(minutes=50)            # inside the T-75..T-35 buy window
BOOK_CLOCK = START - timedelta(minutes=53)
DECIDE = START - timedelta(minutes=20)             # inside the T-30..T-15 checkpoint
GAME_DAY_RUN = datetime(2026, 10, 14, 13, 20, tzinfo=UTC)   # the Oct 14 09:20 ET generator cron
HOME_NAME, AWAY_NAME = "Colorado Avalanche", "Dallas Stars"


def _american(decimal: float) -> int:
    return round((decimal - 1.0) * 100) if decimal >= 2.0 else -round(100.0 / (decimal - 1.0))


def _prices_with_home_edge(p_home: float, *, edge: float = 0.08, vig: float = 1.04) -> tuple[int, int]:
    """A two-way pair whose no-vig home probability sits ``edge`` below the model's."""

    q = min(0.9, max(0.1, p_home - edge))
    return _american(1.0 / (q * vig)), _american(1.0 / ((1.0 - q) * vig))


def _snapshot(raw: list[dict]):
    async def fake(self):
        data = [TheOddsAPIAdapter._normalize_event(r).model_dump(mode="json") for r in raw]
        return AdapterResponse(envelope=SourceEnvelope(
            source="THE_ODDS_API", fetched_at=FETCHED, data=data,
            metadata={"event_count": len(raw), "sport_key": self.sport_key, "requests_remaining": 140,
                      "requests_used": 360, "requests_last": 1}), raw_payload=raw)
    return fake


def _espn_board_for_the_game() -> dict:
    """The ESPN fixture's COL home event, re-pointed at DAL @ COL on Oct 14 ET (ESPN's own field shape)."""

    board = json.loads((ESPN_FIXTURES / "scoreboard_20261016.json").read_bytes())
    event = copy.deepcopy(next(e for e in board["events"]
                               if any(s["homeAway"] == "home" and s["team"]["abbreviation"] == "COL"
                                      for s in e["competitions"][0]["competitors"])))
    stamp = START.strftime("%Y-%m-%dT%H:%MZ")
    event["date"] = event["competitions"][0]["date"] = event["competitions"][0]["startDate"] = stamp
    event["name"], event["shortName"] = "Dallas Stars at Colorado Avalanche", "DAL @ COL"
    for side in event["competitions"][0]["competitors"]:
        if side["homeAway"] == "away":
            side["team"].update({"id": "25", "location": "Dallas", "name": "Stars", "abbreviation": "DAL",
                                 "displayName": "Dallas Stars", "shortDisplayName": "Stars"})
    board["events"] = [event]
    if isinstance(board.get("day"), dict):
        board["day"]["date"] = "2026-10-14"
    return board


def test_every_lane_pins_the_same_in_process_identity():
    """The generator writes, the price lane plans on, and the checkpoint decides on ONE set of values."""

    selected = frozen.SELECTED_MODEL_PATH.read_bytes()
    assert hashlib.sha256(selected).hexdigest() == paper.IN_PROCESS_CONFIGURATION_SHA256
    assert gen.CONFIGURATION_SHA256 == paper.IN_PROCESS_CONFIGURATION_SHA256 == nhl_odds.IN_PROCESS_CONFIGURATION_SHA256
    assert gen.EVIDENCE_STATUS == paper.IN_PROCESS_EVIDENCE_STATUS == nhl_odds.IN_PROCESS_EVIDENCE_STATUS
    assert gen.EVIDENCE_MODE == paper.IN_PROCESS_EVIDENCE_MODE
    assert (gen.MODEL_ID, gen.MODEL_VERSION) == (paper.MODEL_ID, paper.IN_PROCESS_MODEL_VERSION)
    assert gen.CONFIGURATION_SHA256 == "3f7981aac2bfee6f3d05b7e1d099a3342cc9057d09e13b09aaf4c81b7f720417"


@pytest.mark.asyncio
async def test_generated_forecast_is_priced_decided_and_settled_end_to_end(session, settings, monkeypatch):
    from app.jobs.orchestrator import JobOrchestrator

    s = _settings(settings)

    # 1. GENERATE. The Oct 13 12:30 ET run cannot forecast DAL@COL a day ahead: both teams play (unstarted)
    #    on Oct 13, so its rest is not yet knowable (REST_INCOMPLETE). The Oct 14 09:20 ET cron forecasts it.
    day_ahead, _ = await _run(session, s, _schedule("20261013"), OCT13_RUN)
    assert day_ahead["status"] == "COMPLETED" and "2026-10-14" in day_ahead["target_dates"]
    skipped = {e["sourceGameId"]: e["reason"] for e in day_ahead["excluded"]}
    assert skipped[SOURCE_GAME].split(":")[0] == gen.REST_INCOMPLETE
    session.commit()
    generated, seen = await _run(session, s, _schedule("20261013"), GAME_DAY_RUN)
    assert generated["status"] == "COMPLETED", generated
    assert generated["target_dates"][0] == "2026-10-14" and len(seen) == 5
    session.commit()
    forecast = session.scalars(select(NhlResearchForecast).where(
        NhlResearchForecast.canonical_game_ref == f"nhl:game:{SOURCE_GAME}")).one()
    game = session.get(Game, forecast.game_id)
    assert game is not None and game.status == GameStatus.SCHEDULED
    assert game.starts_at.replace(tzinfo=UTC) == START == forecast.scheduled_start_utc.replace(tzinfo=UTC)
    published = forecast.published_utc.replace(tzinfo=UTC)
    assert GAME_DAY_RUN < published <= forecast.imported_at.replace(tzinfo=UTC) < DECIDE
    assert forecast.producer_evidence_ref["restComplete"] is True
    assert forecast.producer_evidence_ref["inputFeatures"]["home_rest_days"] == 1          # COL played Oct 13
    assert published <= START - paper.WINDOW_OPENS                    # admissible: well before T-30
    # The paper route accepts the generator's own row as written (no field massaged here).
    verdict = paper.in_process_eligibility(forecast, moment=DECIDE, starts_at=START)
    assert verdict["eligible"] and verdict["violations"] == [], verdict

    # 2. PRICE: the lane plans this game as due and binds the provider's names to the generator's teams.
    planned = nhl_odds.plan(session, now=FETCHED)
    assert SOURCE_GAME in {g["source_game_id"] for g in planned["due_games"]}, planned["games"]
    assert {g["route"] for g in planned["games"] if g["source_game_id"] == SOURCE_GAME} == {"AISP_IN_PROCESS"}
    home_ml, away_ml = _prices_with_home_edge(forecast.final_home_probability)
    raw = [raw_event("FIXTURENHLLIVE01", HOME_NAME, AWAY_NAME, commence=START, home_ml=home_ml, away_ml=away_ml,
                     clock=BOOK_CLOCK)]
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot", _snapshot(raw))
    real_plan = nhl_odds.plan
    monkeypatch.setattr(nhl_odds, "plan", lambda s_, now=None: real_plan(s_, now=FETCHED))
    _measured_balance(session)
    session.commit()
    collected = await JobOrchestrator(session, s.model_copy(update=ODDS_ON)).collect_nhl_odds_api(START.date())
    assert collected["provider_called"] and collected["bound"] == 1 and collected["created"] == 2, collected

    # 3. DECIDE: the era opened under the recorded standing authority; the checkpoint qualifies a position.
    _activate(session, s)
    session.commit()
    decision = paper.evaluate(session, s, now=DECIDE, clock=lambda: DECIDE)["decisions"][0]
    assert decision["outcome"] == "QUALIFIED", decision.get("detail")
    assert decision["forecast_id"] == forecast.forecast_id
    route = decision["forecast_route"]
    assert (route["route"], route["state"], route["contract"]) == (
        "AISP_IN_PROCESS", "AISP_IN_PROCESS_ELIGIBLE", "AISP_NHL_IN_PROCESS_ELIGIBILITY_V1")
    assert decision["selection"]["side"] == "HOME"
    assert decision["selection"]["provider_event_id"] == "FIXTURENHLLIVE01"
    row = session.get(ShadowExperimentDecision, uuid.UUID(decision["decision_id"]))
    assert row.recommendation == "QUALIFIED_BET" and row.controlling_forecast_identity == forecast.forecast_id
    position = session.get(OfficialAspPosition, uuid.UUID(decision["position_id"]))
    assert position is not None and 0 < float(position.stake) <= 5.0
    session.commit()

    # 4. RESULT: ESPN's final binds to the generator's game by its tri-codes; the game goes FINAL.
    board = _espn_board_for_the_game()
    _scores(board["events"][0], [1, 1, 1], [0, 1, 0])                    # COL 3-1 in regulation
    results = er.ingest(session, s.model_copy(update={"nhl_espn_results_enabled": True}),
                        now=START + timedelta(hours=3), fetch=Fetch({"20261014": board}),
                        clock=lambda: START + timedelta(hours=3, seconds=7))
    assert results["records"] == 1, results
    stored = session.scalars(select(NhlGameResult).where(NhlGameResult.game_id == game.id)).one()
    assert stored.decided_in == "REGULATION"
    session.refresh(game)
    assert game.status == GameStatus.FINAL and (game.home_score, game.away_score) == (3, 1)

    # 5. SETTLE: the one NHL settlement owner settles the HOME position as a WIN.
    done = paper.settle(session, s, now=START + timedelta(hours=4))
    assert done["settled"] == 1 and done["settlements"][0]["result"] == "WIN", done
