"""Free ESPN in-game NFL scores: display only, matched by both clubs and kickoff.

The fixture is the real ESPN scoreboard event shape (verified against a live
response on 2026-10-11): competitors with homeAway, team.abbreviation, score and
per-quarter linescores, and a status block with clock, period and type.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.scoreboard import GameScoreboardObservation
from app.scoreboard.nfl_espn import SOURCE, collect_nfl_live_espn, state_from_event
from tests.test_scoreboard_nfl_live import nfl_game  # noqa: F401

KICKOFF = datetime(2026, 9, 15, 0, 15, tzinfo=UTC)


def espn_event(*, name="STATUS_IN_PROGRESS", state="in", period=3, clock=412.0,
               away=("KC", "17", [7, 3, 7]), home=("DEN", "10", [0, 10, 0]),
               date="2026-09-15T00:15Z", completed=False):
    def side(where, spec):
        abbr, score, lines = spec
        return {"homeAway": where, "team": {"abbreviation": abbr}, "score": score,
                "linescores": [{"value": float(v)} for v in lines]}
    return {"id": "401", "date": date, "competitions": [{
        "competitors": [side("home", home), side("away", away)],
        "status": {"clock": clock, "period": period,
                   "type": {"name": name, "state": state, "completed": completed,
                            "shortDetail": "6:52 - 3rd"}}}]}


class _Settings:
    nfl_espn_live_scores_enabled = True


def test_an_in_progress_event_carries_score_quarter_and_clock():
    s = state_from_event(espn_event(), retrieved_at=KICKOFF)
    assert (s["status"], s["away_score"], s["home_score"]) == ("IN_PROGRESS", 17, 10)
    assert (s["period_number"], s["period_label"], s["clock_seconds_remaining"]) == (3, "3Q", 412)
    assert [p["away"] for p in s["periods"]] == [7, 3, 7] and s["source"] == SOURCE


def test_a_final_reconciles_its_quarters_and_has_no_clock():
    s = state_from_event(espn_event(name="STATUS_FINAL", state="post", period=4, completed=True,
                                    away=("KC", "24", [7, 3, 7, 7]), home=("DEN", "10", [0, 10, 0, 0])),
                         retrieved_at=KICKOFF)
    assert s["status"] == "FINAL" and s["periods_reconcile"] is True
    assert s["clock_seconds_remaining"] is None


def test_halftime_is_a_break_not_a_final():
    s = state_from_event(espn_event(name="STATUS_HALFTIME", period=2, clock=0.0), retrieved_at=KICKOFF)
    assert s["status"] == "IN_PROGRESS" and s["in_break"] is True


def test_a_scheduled_game_shows_no_score():
    s = state_from_event(espn_event(name="STATUS_SCHEDULED", state="pre", period=0), retrieved_at=KICKOFF)
    assert s["status"] == "SCHEDULED" and s["home_score"] is None and s["period_label"] is None


def test_collect_records_the_matching_game_and_ignores_others(session, nfl_game):
    now = KICKOFF + timedelta(minutes=90)
    other = espn_event(away=("BUF", "3", [3]), home=("MIA", "0", [0]))
    calls = []

    def fetch(day):
        calls.append(day)
        return {"events": [espn_event(), other]}

    result = collect_nfl_live_espn(session, _Settings(), now=now, fetch=fetch)
    assert result["games_matched"] == 1 and result["records"] == 1 and calls == ["20260914"]
    row = session.scalar(select(GameScoreboardObservation))
    assert (row.game_id, row.source, row.away_score, row.home_score) == (nfl_game.id, SOURCE, 17, 10)
    # The same state again advances the check time and writes no new row.
    again = collect_nfl_live_espn(session, _Settings(), now=now + timedelta(minutes=2), fetch=fetch)
    assert again["records"] == 0 and again["states_confirmed"] == 1


def test_a_flexed_kickoff_still_matches_but_a_different_week_does_not(session, nfl_game):
    now = KICKOFF + timedelta(minutes=30)
    moved = espn_event(date="2026-09-14T21:00Z")              # 3h15m earlier: same game
    assert collect_nfl_live_espn(session, _Settings(), now=now,
                                 fetch=lambda d: {"events": [moved]})["games_matched"] == 1
    rematch = espn_event(date="2026-09-29T00:15Z")            # two weeks later: not this game
    assert collect_nfl_live_espn(session, _Settings(), now=now,
                                 fetch=lambda d: {"events": [rematch]})["games_matched"] == 0


def test_no_game_in_window_makes_no_request(session, nfl_game):
    def fetch(day):
        raise AssertionError("must not request")
    out = collect_nfl_live_espn(session, _Settings(), now=KICKOFF + timedelta(hours=6), fetch=fetch)
    assert out["requests"] == 0 and out["reason"] == "NO_NFL_GAME_IN_ITS_WATCH_WINDOW"


def test_a_failed_request_is_reported_not_raised(session, nfl_game):
    def fetch(day):
        raise TimeoutError
    out = collect_nfl_live_espn(session, _Settings(), now=KICKOFF + timedelta(minutes=10), fetch=fetch)
    assert out["reason"] == "REQUEST_FAILED" and out["records"] == 0


def test_the_kill_switch_stops_everything(session, nfl_game):
    class Off:
        nfl_espn_live_scores_enabled = False
    out = collect_nfl_live_espn(session, Off(), now=KICKOFF, fetch=lambda d: 1 / 0)
    assert out["reason"] == "NFL_ESPN_LIVE_SCORES_DISABLED"


def test_the_job_runs_on_its_own_lane():
    from app.jobs.lanes import LIVE_SCOREBOARD_LANE, lane_for
    assert lane_for("asp-ls-nfl-live-scores-espn") == LIVE_SCOREBOARD_LANE
