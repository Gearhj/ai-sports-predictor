"""NHL finals from ESPN's public scoreboard -> nhl_game_results -> paper.settle. TEST FIXTURES ONLY.

No request is ever made here: every scoreboard is an ESPN-shaped fixture on disk
(tests/fixtures/nhl_espn, invented scores on the real 2026-27 matchups) served by a fake
fetch, and everything is written to the throwaway in-memory database.
"""
from __future__ import annotations

import copy
import json
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Game, GameStatus, GameTeam
from app.models.nhl_research import NhlGameResult, NhlImportReceipt
from app.nhl import contract as c
from app.nhl import espn_results as er
from app.nhl import paper
from app.nhl.identity import ensure_game

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "nhl_espn"
NOW = datetime(2026, 10, 18, 3, 0, tzinfo=UTC)              # 11 PM ET Sat; Fri + Sat day games are done

#: NHL team ids (test identities; AISP binds teams by these, never by name).
TEAMS = {
    "PIT": ("5", "Pittsburgh Penguins"), "COL": ("21", "Colorado Avalanche"), "BOS": ("6", "Boston Bruins"),
    "ANA": ("24", "Anaheim Ducks"), "WSH": ("15", "Washington Capitals"), "NJD": ("1", "New Jersey Devils"),
    "WPG": ("52", "Winnipeg Jets"), "CAR": ("12", "Carolina Hurricanes"), "TBL": ("14", "Tampa Bay Lightning"),
    "VAN": ("23", "Vancouver Canucks"), "TOR": ("10", "Toronto Maple Leafs"), "MTL": ("8", "Montreal Canadiens"),
}
#: The real 2026-27 schedule rows the fixtures use: source id -> (home, away, start).
SLATE = {
    "2026020119": ("COL", "PIT", datetime(2026, 10, 17, 1, 0, tzinfo=UTC)),
    "2026020120": ("ANA", "BOS", datetime(2026, 10, 17, 2, 0, tzinfo=UTC)),
    "2026020121": ("WSH", "NJD", datetime(2026, 10, 17, 17, 0, tzinfo=UTC)),
    "2026020122": ("WPG", "CAR", datetime(2026, 10, 17, 19, 30, tzinfo=UTC)),
    "2026020123": ("TBL", "VAN", datetime(2026, 10, 17, 20, 0, tzinfo=UTC)),
}


def _team(code):
    tid, name = TEAMS[code]
    return {"canonicalTeamId": f"nhl:team:{tid}", "sourceTeamId": tid, "triCode": code, "name": name}


def _game(session, source_id, home=None, away=None, start=None, source_mode="RESEARCH_SHADOW"):
    h, a, s = (home, away, start) if start is not None else SLATE[source_id]
    block = {"canonicalGameId": f"nhl:game:{source_id}", "sourceGameId": source_id, "season": "2026-27",
             "seasonStartYear": 2026, "gameType": c.GAME_TYPES[source_id[4:6]],
             "scheduledStartUtc": s.strftime("%Y-%m-%dT%H:%M:%SZ"), "home": _team(h), "away": _team(a),
             "neutralSite": False}
    return ensure_game(session, block, source_mode=source_mode)


def _slate(session):
    ids = {sid: _game(session, sid) for sid in SLATE}
    session.commit()
    return ids


def _board(day):
    return json.loads((FIXTURES / f"scoreboard_{day}.json").read_bytes())


class Fetch:
    """A fake public GET: fixture bytes by date (or an override), recording every request."""

    def __init__(self, overrides=None):
        self.calls, self.overrides = [], overrides or {}

    def __call__(self, day):
        self.calls.append(day)
        if day in self.overrides:
            value = self.overrides[day]
            if isinstance(value, Exception):
                raise value
            return value if isinstance(value, bytes) else json.dumps(value).encode()
        path = FIXTURES / f"scoreboard_{day}.json"
        return path.read_bytes() if path.is_file() else b'{"leagues":[{"abbreviation":"NHL"}],"events":[]}'


def _on(settings, **extra):
    return settings.model_copy(update={"nhl_espn_results_enabled": True, **extra})


def _ingest(session, settings, *, now=NOW, fetch=None, retrieved=None):
    fetch = fetch or Fetch()
    moment = retrieved or now + timedelta(seconds=7)
    return er.ingest(session, settings, now=now, fetch=fetch, clock=lambda: moment), fetch


def _result(session, game_id):
    return session.scalars(select(NhlGameResult).where(NhlGameResult.game_id == game_id)).all()


def _aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


# ------------------------------------------------------- finals -> results --
def test_regulation_overtime_and_shootout_finals_become_contract_results(session, settings):
    ids = _slate(session)
    out, fetch = _ingest(session, _on(settings))
    assert fetch.calls == ["20261016", "20261017"] and out["requests"] == 2 and out["costUsd"] == 0
    assert out["records"] == 3 and len(out["imported"]) == 3 and out["request_failures"] == {}
    received = NOW + timedelta(seconds=7)

    expected = {
        "2026020119": ("REGULATION", (4, 2), "HOME", (4, 2), None, None),
        "2026020120": ("OVERTIME", (2, 2), "TIE", (2, 3), (0, 1), None),
        "2026020121": ("SHOOTOUT", (2, 2), "TIE", (3, 2), (0, 0), (1, 0)),
    }
    for sid, (decided, reg, reg_result, final, ot, so) in expected.items():
        [row] = _result(session, ids[sid])
        assert row.decided_in == decided and row.regulation_result == reg_result
        assert (row.regulation_home_goals, row.regulation_away_goals) == reg
        assert (row.final_home_score, row.final_away_score) == final
        assert row.final_winner == ("HOME" if final[0] > final[1] else "AWAY") and row.final_official is True
        assert ((row.overtime_home_goals, row.overtime_away_goals) if ot else row.overtime_home_goals) == (ot or None)
        assert ((row.shootout_home_scored, row.shootout_away_scored) if so else row.shootout_home_scored) == (so or None)
        assert row.final_source == row.regulation_source == er.SOURCE and row.canonical_game_ref == f"nhl:game:{sid}"
        # The stored contract result re-proves itself; the identity is its content hash.
        doc = row.document
        assert doc["schema"] == er.DOCUMENT_SCHEMA and c.validate_result(doc["result"]) == []
        assert row.result_sha256 == doc["result"]["resultSha256"] == c.result_identity(doc["result"])
        # The raw ESPN event is kept verbatim and object_sha256 is its canonical hash.
        assert row.object_sha256 == doc["source"]["rawEventSha256"] == c.sha256_hex(
            c.canonical_bytes(doc["rawEvent"], trailing_newline=False))
        assert doc["source"]["url"].endswith(f"dates={doc['source']['date']}") and doc["source"]["costUsd"] == 0
        assert doc["match"]["canonicalGameId"] == f"nhl:game:{sid}" and doc["match"]["startDifferenceSeconds"] == 0
        # Honest clocks: observed = retrieved = response receipt; no invented completion; write >= receipt.
        for clock in (row.regulation_observed_utc, row.regulation_retrieved_utc, row.final_observed_utc,
                      row.final_retrieved_utc):
            assert _aware(clock) == received
        assert row.final_completed_utc is None and _aware(row.imported_at) >= received
        assert _aware(row.available_as_of) == _aware(row.imported_at)
        # Marked FINAL the way the producer result import does it.
        game = session.get(Game, ids[sid])
        assert game.status == GameStatus.FINAL and (game.home_score, game.away_score) == final
        assert game.final_at is None
        sides = {s.is_home: s.score for s in session.scalars(select(GameTeam).where(GameTeam.game_id == ids[sid]))}
        assert sides == {True: final[0], False: final[1]}
    assert _result(session, ids["2026020121"])[0].document["derivation"]["shootoutTally"] == "ESPN_LINESCORE_PERIOD_5"

    # Postponed: no result, the game is POSTPONED (paper.settle voids on it). In progress: nothing.
    assert _result(session, ids["2026020122"]) == [] and session.get(Game, ids["2026020122"]).status == \
        GameStatus.POSTPONED
    assert [s["status"] for s in out["statuses"]] == ["postponed"]
    assert _result(session, ids["2026020123"]) == [] and session.get(Game, ids["2026020123"]).status == \
        GameStatus.SCHEDULED
    assert [n["canonical_game_ref"] for n in out["not_final"]] == ["nhl:game:2026020123"]
    receipts = {(r.kind, r.disposition) for r in session.scalars(select(NhlImportReceipt))}
    assert receipts == {("RESULT", "IMPORTED"), ("SCHEDULE", "UPDATED")}
    assert session.query(NhlImportReceipt).filter_by(kind="RESULT").count() == 3

    # The next cycle re-reads the same finals: nothing new is written (substance unchanged).
    again, fetch2 = _ingest(session, _on(settings), now=NOW + timedelta(minutes=30))
    assert again["records"] == 0 and again["unchanged"] == 3 and fetch2.calls == ["20261016", "20261017"]
    assert session.query(NhlGameResult).count() == 3 and session.query(NhlImportReceipt).count() == 4


def test_a_shootout_without_a_tally_column_and_a_playoff_double_overtime():
    def event(lines_home, lines_away, score, detail, period, season_type=2):
        raw = {"id": "1", "date": "2026-10-17T17:00Z", "season": {"type": season_type}, "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"abbreviation": "WSH"}, "score": str(score[0]),
                 "linescores": [{"value": v} for v in lines_home]},
                {"homeAway": "away", "team": {"abbreviation": "NJ"}, "score": str(score[1]),
                 "linescores": [{"value": v} for v in lines_away]}],
            "status": {"period": period, "type": {"name": "STATUS_FINAL", "state": "post", "completed": True,
                                                  "detail": detail}}}]}
        return er.parse_event(raw, request={"url": "fixture"}, retrieved_at=NOW)

    regular = er.Target(game_id=None, starts_at=NOW, status=GameStatus.SCHEDULED, source_mode="RESEARCH_SHADOW",
                        canonical_game_ref="nhl:game:2026020121", home_code="WSH", away_code="NJD",
                        home_ref="nhl:team:15", away_ref="nhl:team:1")
    doc, how = er.derive_result(event([1, 0, 1], [0, 2, 0], (2, 3), "Final/SO", 5), regular, supersedes=None)
    assert doc["decidedIn"] == "SHOOTOUT" and doc["shootout"] == {"played": True, "homeScored": 0, "awayScored": 1}
    assert how["shootoutTally"] == "DECISIVE_GOAL_CREDIT_ONLY" and doc["final"]["winner"] == "AWAY"
    # A 2-2 game after a scoreless overtime that ESPN does not call a shootout is refused, not guessed.
    with pytest.raises(er.Refused, match="NO_SHOOTOUT_REPORTED"):
        er.derive_result(event([1, 0, 1, 0], [0, 2, 0, 0], (2, 3), "Final", 4), regular, supersedes=None)

    playoff = er.Target(game_id=None, starts_at=NOW, status=GameStatus.SCHEDULED, source_mode="RESEARCH_SHADOW",
                        canonical_game_ref="nhl:game:2026030121", home_code="WSH", away_code="NJD",
                        home_ref="nhl:team:15", away_ref="nhl:team:1")
    doc, _ = er.derive_result(event([1, 1, 0, 0, 1], [2, 0, 0, 0, 0], (3, 2), "Final/2OT", 5, season_type=3),
                              playoff, supersedes=None)
    assert doc["decidedIn"] == "OVERTIME" and doc["overtime"] == {"played": True, "homeGoals": 1, "awayGoals": 0,
                                                                   "periods": 2}
    # ESPN's season type must agree with the game type the NHL game id carries.
    with pytest.raises(er.Refused, match="GAME_TYPE_DIFFERS"):
        er.derive_result(event([1, 1, 0], [0, 0, 0], (2, 0), "Final", 3, season_type=3), regular, supersedes=None)


# ---------------------------------------------------------------- refusals --
def test_an_event_that_fits_two_aisp_games_or_two_events_that_fit_one_game_are_refused(session, settings):
    ids = _slate(session)
    twin = _game(session, "2026020999", "COL", "PIT", SLATE["2026020119"][2] + timedelta(hours=1))
    session.commit()
    out, _ = _ingest(session, _on(settings))
    refused = {r["canonical_game_ref"]: r["reason"] for r in out["refused"]}
    assert refused["nhl:game:2026020119"].startswith("AMBIGUOUS_AISP_GAMES")
    assert refused["nhl:game:2026020999"].startswith("AMBIGUOUS_AISP_GAMES")
    assert _result(session, ids["2026020119"]) == [] and _result(session, twin) == []
    assert session.get(Game, ids["2026020119"]).status == GameStatus.SCHEDULED
    assert out["records"] == 2                                          # the unambiguous games still resolve

    # Two ESPN events fitting one game (a duplicated event under a new id) are refused too.
    board = _board("20261016")
    duplicate = copy.deepcopy(board["events"][1])
    duplicate["id"] = "401999120"
    board["events"].append(duplicate)
    session.query(NhlGameResult).delete()
    session.get(Game, ids["2026020120"]).status = GameStatus.SCHEDULED
    session.commit()
    out, _ = _ingest(session, _on(settings), fetch=Fetch({"20261016": board}))
    refused = {r["canonical_game_ref"]: r["reason"] for r in out["refused"]}
    assert refused["nhl:game:2026020120"].startswith("AMBIGUOUS_ESPN_EVENTS")
    assert _result(session, ids["2026020120"]) == []
    rejected = session.query(NhlImportReceipt).filter_by(kind="RESULT", disposition="REJECTED").all()
    assert {r.canonical_game_ref for r in rejected} >= {"nhl:game:2026020119", "nhl:game:2026020120"}


def test_a_flipped_orientation_or_an_unknown_club_spelling_is_never_matched(session, settings):
    ids = _slate(session)
    board = _board("20261016")
    flipped = board["events"][0]["competitions"][0]["competitors"]
    flipped[0]["homeAway"], flipped[1]["homeAway"] = "away", "home"         # ESPN says COL @ PIT
    board["events"][1]["competitions"][0]["competitors"][1]["team"]["abbreviation"] = "BRU"
    out, _ = _ingest(session, _on(settings), fetch=Fetch({"20261016": board}))
    refused = {r["canonical_game_ref"]: r["reason"] for r in out["refused"]}
    assert refused["nhl:game:2026020119"].startswith("ORIENTATION_DIFFERS_FROM_ESPN")
    assert [u["canonical_game_ref"] for u in out["unmatched"]] == ["nhl:game:2026020120"]
    assert [e["espn_away"] for e in out["unrecognised_events"]] == ["BRU"]
    assert _result(session, ids["2026020119"]) == [] and _result(session, ids["2026020120"]) == []


def test_a_final_that_disagrees_with_its_own_linescore_or_winner_flag_is_refused(session, settings):
    ids = _slate(session)
    board = _board("20261016")
    board["events"][0]["competitions"][0]["competitors"][0]["score"] = "5"         # periods say 4
    board["events"][1]["competitions"][0]["competitors"][0]["winner"] = True       # ANA lost 2-3 in OT
    out, _ = _ingest(session, _on(settings), fetch=Fetch({"20261016": board}))
    refused = {r["canonical_game_ref"]: r["reason"] for r in out["refused"]}
    assert refused == {"nhl:game:2026020119": "FINAL_DIFFERS_FROM_REGULATION_LINESCORE",
                       "nhl:game:2026020120": "WINNER_FLAG_DISAGREES_WITH_THE_SCORE"}
    assert _result(session, ids["2026020119"]) == [] and session.get(Game, ids["2026020119"]).status == \
        GameStatus.SCHEDULED
    reasons = [r.problems for r in session.query(NhlImportReceipt).filter_by(disposition="REJECTED")]
    assert ["FINAL_DIFFERS_FROM_REGULATION_LINESCORE"] in reasons


def test_failed_or_foreign_responses_are_reported_never_raised(session, settings):
    import httpx

    _slate(session)
    out, _ = _ingest(session, _on(settings), fetch=Fetch({"20261016": httpx.ConnectTimeout("timed out"),
                                                         "20261017": {"leagues": [{"abbreviation": "NBA"}],
                                                                      "events": []}}))
    assert out["status"] == "REQUEST_FAILED" and out["records"] == 0
    assert set(out["request_failures"]) == {"20261016", "20261017"}
    assert "RESPONSE_IS_NOT_NHL" in out["request_failures"]["20261017"]
    assert len(out["request_failed"]) == 5 and session.query(NhlGameResult).count() == 0


# ------------------------------------------------------ when it asks at all --
def test_off_by_default_and_no_request_unless_a_real_game_awaits_its_result(session, settings):
    from app.core.config import Settings

    assert Settings.model_fields["nhl_espn_results_enabled"].default is False
    ids = _slate(session)
    off, fetch = _ingest(session, settings.model_copy(update={"nhl_espn_results_enabled": False}))
    assert off["status"] == "DISABLED" and fetch.calls == [] and session.query(NhlGameResult).count() == 0

    # Not yet 2h30m after any start: no request.
    early, fetch = _ingest(session, _on(settings), now=SLATE["2026020119"][2] + timedelta(hours=2))
    assert early["status"] == "NO_NHL_GAME_AWAITING_A_RESULT" and fetch.calls == []

    # Fixture/synthetic games are never asked about, and games beyond the lookback are left alone.
    for gid in ids.values():
        session.get(Game, gid).source_mode = "SYNTHETIC"
    session.commit()
    out, fetch = _ingest(session, _on(settings))
    assert out["status"] == "NO_NHL_GAME_AWAITING_A_RESULT" and fetch.calls == []
    for gid in ids.values():
        session.get(Game, gid).source_mode = "RESEARCH_SHADOW"
    session.commit()
    late, fetch = _ingest(session, _on(settings, nhl_espn_results_lookback_days=1),
                          now=NOW + timedelta(days=2))
    assert late["status"] == "NO_NHL_GAME_AWAITING_A_RESULT" and fetch.calls == []


def test_a_game_finalised_by_the_producer_is_not_asked_about_again(session, settings):
    from app.nhl.gen1_import import ImportSummary, import_result_bytes
    from tests import nhl_fixtures as fx

    ids = _slate(session)
    home, away, start = SLATE["2026020119"]
    block = {"canonicalGameId": "nhl:game:2026020119", "gameType": "REGULAR",
             "home": _team(home), "away": _team(away)}
    doc = fx.result(block, reg=(3, 1), decided="REGULATION", observed=start + timedelta(hours=3))
    assert import_result_bytes(session, c.canonical_bytes(doc), summary=ImportSummary(run_id="producer"),
                               now=start + timedelta(hours=4)) == "IMPORTED"
    session.commit()
    out, _ = _ingest(session, _on(settings))
    assert "nhl:game:2026020119" not in {i["canonical_game_ref"] for i in out["imported"]}
    [row] = _result(session, ids["2026020119"])
    assert row.final_source == "PREVIEW_FIXTURE_BOXSCORE"                       # the producer's result stands


# ----------------------------------------- end to end: settle, correct, void --
def _scores(event, home, away, *, detail="Final", period=3):
    """Rewrite one fixture event's lines/score/winner flags (home/away = per-period goals)."""

    competition = event["competitions"][0]
    for side in competition["competitors"]:
        lines = home if side["homeAway"] == "home" else away
        side["linescores"] = [{"value": float(v), "displayValue": str(v), "period": i + 1} for i, v in enumerate(lines)]
        side["score"] = str(sum(lines))
        side["winner"] = sum(lines) > sum(away if side["homeAway"] == "home" else home)
    for status in (competition["status"], event["status"]):
        status["period"], status["type"]["detail"], status["type"]["shortDetail"] = period, detail, detail
    return event


def test_espn_final_settles_the_position_then_a_correction_flips_it_beside_the_original(session, settings):
    from app.bankroll.settlement import settle_completed_positions
    from app.models import PositionSettlement, PositionSettlementAdjustment
    from app.services.research_paper_policy import NHL
    from app.services.shadow_experiment_service import ShadowExperimentService
    from tests.test_nhl_paper_lifecycle import DECIDE, START, _ready

    s, forecast, _ = _ready(session, settings)
    decision = paper.evaluate(session, s, now=DECIDE, clock=lambda: DECIDE)["decisions"][0]
    assert decision["outcome"] == "QUALIFIED", decision.get("detail")
    side = decision["selection"]["side"]
    on = _on(s)

    board = _board("20261020")
    wins, loses = [1, 1, 1], [0, 1, 0]                                   # the selected side wins 3-1
    _scores(board["events"][0], *((wins, loses) if side == "HOME" else (loses, wins)))
    first, fetch = _ingest(session, on, now=START + timedelta(hours=3), fetch=Fetch({"20261020": board}))
    assert fetch.calls == ["20261020"] and first["records"] == 1
    [result] = _result(session, forecast.game_id)

    # Only paper.settle settles NHL: the generic owners leave the FINAL game's position alone.
    assert ShadowExperimentService(session, on).settle()["experiments"][NHL.policy_version]["owner"] == \
        "app.nhl.paper.settle"
    assert settle_completed_positions(session) == [] and session.query(PositionSettlement).count() == 0
    done = paper.settle(session, on, now=START + timedelta(hours=4))
    assert done["settled"] == 1 and done["settlements"][0]["result"] == "WIN"
    assert result.result_sha256 in done["settlements"][0]["basis"]

    # ESPN later changes the final: the other side wins in overtime. A NEW result supersedes; never an edit.
    tied, scored = [1, 1, 0, 0], [1, 0, 1, 1]                           # 2-2, then the OT goal
    _scores(board["events"][0], *((tied, scored) if side == "HOME" else (scored, tied)), detail="Final/OT", period=4)
    fix, _ = _ingest(session, on, now=START + timedelta(hours=5), fetch=Fetch({"20261020": board}),
                     retrieved=START + timedelta(hours=5, seconds=9))
    assert fix["records"] == 1 and fix["corrections"][0]["supersedes_result_sha256"] == result.result_sha256
    rows = _result(session, forecast.game_id)
    assert len(rows) == 2 and session.get(NhlGameResult, result.id).decided_in == "REGULATION"   # untouched
    newest = next(r for r in rows if r.id != result.id)
    assert newest.supersedes_result_sha256 == result.result_sha256 and newest.decided_in == "OVERTIME"
    assert paper.source_result(session, forecast.game_id).id == newest.id
    corrected = paper.settle(session, on, now=START + timedelta(hours=6))
    assert corrected["corrected"] == 1 and corrected["corrections"][0]["corrected"] == "LOSS"
    assert corrected["corrections"][0]["result_sha256"] == newest.result_sha256
    assert session.query(PositionSettlement).one().result.name == "WIN"          # the original is retained
    assert session.query(PositionSettlementAdjustment).count() == 1

    # Re-reading the corrected final writes nothing; outside the correction window nothing is asked.
    same, _ = _ingest(session, on, now=START + timedelta(hours=7), fetch=Fetch({"20261020": board}))
    assert same["records"] == 0 and same["unchanged"] == 1 and session.query(NhlGameResult).count() == 2
    assert paper.settle(session, on, now=START + timedelta(hours=8))["corrected"] == 0
    quiet, fetch = _ingest(session, on, now=START + timedelta(hours=50))
    assert quiet["status"] == "NO_NHL_GAME_AWAITING_A_RESULT" and fetch.calls == []


def test_an_espn_postponement_voids_the_position(session, settings):
    from tests.test_nhl_paper_lifecycle import DECIDE, START, _ready

    s, forecast, _ = _ready(session, settings)
    position_id = paper.evaluate(session, s, now=DECIDE, clock=lambda: DECIDE)["decisions"][0]["position_id"]
    board = _board("20261020")
    for status in (board["events"][0]["competitions"][0]["status"], board["events"][0]["status"]):
        status["type"] = {"id": "6", "name": "STATUS_POSTPONED", "state": "post", "completed": False,
                          "description": "Postponed", "detail": "Postponed", "shortDetail": "Postponed"}
    out, _ = _ingest(session, _on(s), now=START + timedelta(hours=3), fetch=Fetch({"20261020": board}))
    assert out["records"] == 0 and out["statuses"][0]["status"] == "postponed"
    assert session.get(Game, forecast.game_id).status == GameStatus.POSTPONED
    settled = paper.settle(session, s, now=START + timedelta(hours=4))
    assert settled["settled"] == 1 and settled["settlements"][0]["result"] == "VOID"
    assert settled["settlements"][0]["position_id"] == position_id
    # A postponed game is not asked about again.
    again, fetch = _ingest(session, _on(s), now=START + timedelta(hours=5))
    assert again["status"] == "NO_NHL_GAME_AWAITING_A_RESULT" and fetch.calls == []


# ------------------------------------------------------------ crosswalk, job --
def test_the_club_crosswalk_is_explicit_and_one_to_one():
    assert len(er.NHL_CODES) == 32 and set(er.ESPN_TO_NHL.values()) == er.NHL_CODES
    assert {k: v for k, v in er.ESPN_TO_NHL.items() if k != v} == {
        "LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA"}
    # No ESPN spelling is another club's NHL code, so accepting both can never confuse two clubs.
    assert not ({"LA", "NJ", "SJ", "TB", "UTAH"} & er.NHL_CODES)
    assert er.nhl_code("utah") == "UTA" and er.nhl_code("TBL") == "TBL" and er.nhl_code("ARI") is None
    assert er.nhl_code(None) is None and er.nhl_code("NY") is None


def test_the_job_is_off_by_default_on_the_core_lane_and_a_manual_run_while_off_asks_nothing(settings):
    from app.jobs.lanes import CORE_LANE, lane_for
    from app.jobs.orchestrator import AVAILABLE_JOBS
    from app.jobs.scheduler import build_scheduler

    assert "ingest-nhl-finals-espn" in AVAILABLE_JOBS
    base = {"scheduler_core_jobs_enabled": True}
    assert build_scheduler(settings.model_copy(update={**base, "nhl_espn_results_enabled": False})).get_job(
        "asp-nhl-finals-espn") is None
    job = build_scheduler(settings.model_copy(update={**base, "nhl_espn_results_enabled": True})).get_job(
        "asp-nhl-finals-espn")
    assert job is not None and job.kwargs["job_name"] == "ingest-nhl-finals-espn"
    assert lane_for(job.id) == CORE_LANE and job.trigger.interval == timedelta(minutes=30)
    assert job.max_instances == 1


@pytest.mark.asyncio
async def test_the_orchestrator_runs_it_only_when_on(session, settings, monkeypatch):
    from app.jobs.orchestrator import JobOrchestrator

    # The job reads the real clock, so its game started four hours before now.
    _game(session, "2026020119", "COL", "PIT", datetime.now(UTC).replace(microsecond=0) - timedelta(hours=4))
    session.commit()
    calls = []
    monkeypatch.setattr(er, "fetch_scoreboard", lambda day: calls.append(day) or b'{"events":[]}')
    off = await JobOrchestrator(session, settings.model_copy(update={"nhl_espn_results_enabled": False})).run(
        "ingest-nhl-finals-espn")
    assert off.result["status"] == "DISABLED" and calls == []
    on = await JobOrchestrator(session, _on(settings)).run("ingest-nhl-finals-espn")
    assert on.result["status"] == "CHECKED" and calls and on.result["records"] == 0
    assert json.loads(json.dumps(on.result))["source"] == er.SOURCE
