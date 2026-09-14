"""Score, period reconciliation, staleness, corrections and settlement.

The checks here are the five §7 names, and each one is a way this feature could
lie without looking broken:

  * identity and orientation -- a score against the wrong club, or backwards;
  * stale updates -- an unchanged payload reading as fresh, or a fresh one
    reading as stale after a page reload;
  * period reconciliation -- a breakdown that does not add up, or a gap filled
    in so that it does;
  * corrections -- a revised score overwriting the reading someone acted on;
  * settlement separation -- a FINAL score implying a settled wager.

The MLB fixtures are shaped exactly like the retained captures, including the
things that make real linescores awkward: an unplayed bottom of the ninth, a
tenth inning, and a mid-inning break with no runners on.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.scoreboard.mlb import (
    classify as mlb_classify,
    periods_from as mlb_periods,
    scoreboard_from_schedule_game,
)
from app.scoreboard.nfl import (
    classify as nfl_classify,
    periods_from as nfl_periods,
    scoreboard_from_event,
)
from app.scoreboard.read_service import assess_freshness


class Row:
    """The fields `assess_freshness` reads, without a database round trip."""

    def __init__(self, **fields):
        self.status = fields.get("status", "IN_PROGRESS")
        self.checked_at = fields.get("checked_at")
        self.source_observed_at = fields.get("source_observed_at")


def mlb_game(**over):
    """A retained-capture-shaped schedule game node."""

    game = {
        "gamePk": 824225,
        "status": {"abstractGameState": "Final", "codedGameState": "F",
                   "detailedState": "Final", "statusCode": "F"},
        "teams": {"away": {"score": 1}, "home": {"score": 8}},
        "linescore": {
            "currentInning": 9, "currentInningOrdinal": "9th",
            "inningState": "Top", "inningHalf": "Top", "scheduledInnings": 9,
            "balls": 2, "strikes": 3, "outs": 3, "isTopInning": True,
            "innings": [
                {"num": 1, "ordinalNum": "1st",
                 "away": {"runs": 1, "hits": 1}, "home": {"runs": 2, "hits": 2}},
                {"num": 2, "ordinalNum": "2nd",
                 "away": {"runs": 0, "hits": 1}, "home": {"runs": 3, "hits": 3}},
                {"num": 3, "ordinalNum": "3rd",
                 "away": {"runs": 0}, "home": {"runs": 0}},
                {"num": 4, "ordinalNum": "4th",
                 "away": {"runs": 0}, "home": {"runs": 2}},
                {"num": 5, "ordinalNum": "5th",
                 "away": {"runs": 0}, "home": {"runs": 1}},
                {"num": 6, "ordinalNum": "6th",
                 "away": {"runs": 0}, "home": {"runs": 0}},
                {"num": 7, "ordinalNum": "7th",
                 "away": {"runs": 0}, "home": {"runs": 0}},
                {"num": 8, "ordinalNum": "8th",
                 "away": {"runs": 0}, "home": {"runs": 0}},
                # The home half of the ninth was not played: the home team led.
                {"num": 9, "ordinalNum": "9th",
                 "away": {"runs": 0}, "home": {}},
            ],
            "offense": {},
        },
    }
    game.update(over)
    return game


def nfl_event(**over):
    """A real SportsGameOdds event object's shape, trimmed to what is read."""

    event = {
        "eventID": "abc123",
        "status": {
            "started": True, "completed": True, "ended": True, "live": False,
            "cancelled": False, "delayed": False, "inBreak": False,
            "currentPeriodID": "", "displayShort": "F", "displayLong": "Final",
            "startsAt": "2026-09-13T17:00:00.000Z", "finalized": True,
            "periods": {"started": ["1q", "2q", "3q", "4q", "1h", "game", "reg"],
                        "ended": ["1q", "2q", "3q", "4q", "1h", "game", "reg"]},
        },
        "teams": {
            "home": {"names": {"short": "TEN"}, "score": 10},
            "away": {"names": {"short": "NYJ"}, "score": 23},
        },
        "results": {
            "1q": {"home": {"points": 3}, "away": {"points": 7}},
            "2q": {"home": {"points": 0}, "away": {"points": 3}},
            "3q": {"home": {"points": 0}, "away": {"points": 10}},
            "4q": {"home": {"points": 7}, "away": {"points": 3}},
            "game": {"home": {"points": 10}, "away": {"points": 23}},
            "reg": {"home": {"points": 10}, "away": {"points": 23}},
        },
    }
    event.update(over)
    return event


# -- identity and orientation ---------------------------------------------

def test_mlb_score_stays_with_the_side_it_belongs_to():
    state = scoreboard_from_schedule_game(mlb_game())
    assert state["away_score"] == 1
    assert state["home_score"] == 8
    # And per inning: the home team scored 2 in the first, the away team 1.
    first = state["periods"][0]
    assert (first["away"], first["home"]) == (1, 2)


def test_nfl_score_and_quarters_keep_their_orientation():
    state = scoreboard_from_event(nfl_event())
    assert (state["away_score"], state["home_score"]) == (23, 10)
    assert [(p["label"], p["away"], p["home"]) for p in state["periods"]] == [
        ("1Q", 7, 3), ("2Q", 3, 0), ("3Q", 10, 0), ("4Q", 3, 7),
    ]


# -- period reconciliation -------------------------------------------------

def test_an_unplayed_half_inning_is_absent_not_zero():
    periods, complete = mlb_periods(mlb_game()["linescore"])
    ninth = periods[-1]
    assert ninth["away"] == 0        # they batted and did not score
    assert ninth["home"] is None     # they did not bat
    assert complete is True


def test_the_breakdown_reconciles_to_the_final_with_a_half_inning_missing():
    state = scoreboard_from_schedule_game(mlb_game())
    assert state["periods_reconcile"] is True
    assert sum(p["away"] or 0 for p in state["periods"]) == state["away_score"]
    assert sum(p["home"] or 0 for p in state["periods"]) == state["home_score"]


def test_extra_innings_are_periods_like_any_other():
    game = mlb_game()
    game["linescore"]["currentInning"] = 10
    game["linescore"]["innings"].append(
        {"num": 10, "ordinalNum": "10th", "away": {"runs": 2}, "home": {"runs": 3}})
    game["teams"] = {"away": {"score": 3}, "home": {"score": 11}}
    state = scoreboard_from_schedule_game(game)
    assert len(state["periods"]) == 10
    assert state["periods_reconcile"] is True


def test_a_breakdown_that_does_not_add_up_is_reported_not_adjusted():
    game = mlb_game()
    # The source says 8 but the innings total 7: a real disagreement.
    game["linescore"]["innings"][1]["home"]["runs"] = 2
    state = scoreboard_from_schedule_game(game)
    assert state["periods_reconcile"] is False
    # Neither number was changed to make them agree.
    assert state["home_score"] == 8
    assert sum(p["home"] or 0 for p in state["periods"]) == 7


def test_nothing_is_divided_out_of_a_final_when_the_breakdown_is_missing():
    game = mlb_game()
    game["linescore"]["innings"] = []
    state = scoreboard_from_schedule_game(game)
    assert state["periods"] == []
    assert state["periods_complete"] is False
    # The final survives; the breakdown is simply absent.
    assert (state["away_score"], state["home_score"]) == (1, 8)
    assert state["periods_reconcile"] is None


def test_nfl_overtime_is_a_period_and_still_reconciles():
    event = nfl_event()
    event["results"]["ot"] = {"home": {"points": 7}, "away": {"points": 6}}
    event["status"]["periods"]["started"].append("ot")
    event["teams"]["home"]["score"] = 17
    event["teams"]["away"]["score"] = 29
    state = scoreboard_from_event(event)
    assert state["periods"][-1]["label"] == "OT"
    assert state["periods_reconcile"] is True


def test_an_nfl_quarter_the_source_omitted_is_not_complete():
    event = nfl_event()
    del event["results"]["3q"]
    periods, complete = nfl_periods(event)
    assert [p["label"] for p in periods] == ["1Q", "2Q", "4Q"]
    assert complete is False


# -- status: a stopped clock is not a result -------------------------------

@pytest.mark.parametrize("code,detailed,expected", [
    ("S", "Scheduled", "SCHEDULED"),
    ("PW", "Warmup", "IN_PROGRESS"),
    ("I", "In Progress", "IN_PROGRESS"),
    ("MJ", "Player challenge", "IN_PROGRESS"),
    ("IR", "Delayed", "DELAYED"),
    ("PR", "Delayed Start", "DELAYED"),
    ("F", "Final", "FINAL"),
    ("O", "Game Over", "FINAL"),
    ("DR", "Postponed", "POSTPONED"),
    ("C", "Cancelled", "CANCELLED"),
    ("U", "Suspended", "SUSPENDED"),
])
def test_mlb_statuses_map_without_losing_the_sources_own_words(code, detailed, expected):
    status, detail = mlb_classify({"statusCode": code, "detailedState": detailed})
    assert status == expected
    assert detail == detailed


def test_an_nfl_game_that_started_but_is_not_live_is_not_final():
    # A stoppage: started, not live, not ended. Calling that FINAL would settle
    # a game that is still being played.
    status, _ = nfl_classify({
        "started": True, "live": False, "ended": False, "completed": False})
    assert status == "IN_PROGRESS"


def test_a_delayed_game_keeps_its_score_and_says_it_is_delayed():
    game = mlb_game(
        status={"abstractGameState": "Live", "codedGameState": "I",
                "detailedState": "Delayed", "statusCode": "IR"},
        teams={"away": {"score": 2}, "home": {"score": 1}},
    )
    state = scoreboard_from_schedule_game(game)
    assert state["status"] == "DELAYED"
    assert (state["away_score"], state["home_score"]) == (2, 1)


# -- no clock is invented --------------------------------------------------

def test_neither_sport_reports_a_clock_it_was_never_given():
    assert scoreboard_from_schedule_game(mlb_game())["clock_seconds_remaining"] is None
    assert scoreboard_from_event(nfl_event())["clock_seconds_remaining"] is None


def test_mlb_situation_is_only_read_while_a_game_is_in_progress():
    final = scoreboard_from_schedule_game(mlb_game())
    assert final["outs"] is None and final["bases"] is None

    live = mlb_game(status={"abstractGameState": "Live", "codedGameState": "I",
                            "detailedState": "In Progress", "statusCode": "I"})
    live["linescore"]["offense"] = {"first": {"fullName": "A Runner"}}
    state = scoreboard_from_schedule_game(live)
    assert state["outs"] == 3
    assert state["bases"] == {"first": "A Runner", "second": None, "third": None}


# -- staleness and corrections --------------------------------------------

def test_a_score_confirmed_seconds_ago_reads_as_current():
    now = datetime.now(UTC)
    freshness = assess_freshness(Row(checked_at=now - timedelta(seconds=40)), now=now)
    assert freshness.state == "CURRENT"
    assert freshness.source_timestamp_available is False


def test_updates_running_behind_are_labelled_rather_than_hidden():
    now = datetime.now(UTC)
    freshness = assess_freshness(Row(checked_at=now - timedelta(minutes=12)), now=now)
    assert freshness.state == "DELAYED"
    assert "last score AISP could confirm" in freshness.detail


def test_an_outage_says_so_and_still_reports_the_age():
    now = datetime.now(UTC)
    freshness = assess_freshness(Row(checked_at=now - timedelta(hours=2)), now=now)
    assert freshness.state == "UNAVAILABLE"
    assert "probably out of date" in freshness.detail
    assert freshness.checked_age_seconds == pytest.approx(7200, abs=2)


def test_a_final_does_not_go_stale():
    now = datetime.now(UTC)
    freshness = assess_freshness(
        Row(status="FINAL", checked_at=now - timedelta(days=3)), now=now)
    assert freshness.state == "SETTLED_STATE"
    assert "does not go stale" in freshness.detail


def test_a_source_timestamp_is_reported_as_present_only_when_it_exists():
    now = datetime.now(UTC)
    without = assess_freshness(Row(checked_at=now), now=now)
    with_time = assess_freshness(
        Row(checked_at=now, source_observed_at=now - timedelta(minutes=1)), now=now)
    assert without.source_timestamp_available is False
    assert with_time.source_timestamp_available is True


def test_a_corrected_score_is_a_different_state_not_an_edit():
    first = scoreboard_from_schedule_game(mlb_game())
    corrected = mlb_game(teams={"away": {"score": 2}, "home": {"score": 8}})
    second = scoreboard_from_schedule_game(corrected)
    # A different checksum means a new row beside the old one, so the reading
    # someone may have acted on is still there.
    assert first["payload_checksum"] != second["payload_checksum"]


def test_an_unchanged_payload_produces_the_same_state_identity():
    # This is what lets a re-check advance freshness without writing a row, so
    # a quiet inning does not look like an outage.
    one = scoreboard_from_schedule_game(mlb_game(), retrieved_at=datetime.now(UTC))
    two = scoreboard_from_schedule_game(
        mlb_game(), retrieved_at=datetime.now(UTC) + timedelta(minutes=5))
    assert one["payload_checksum"] == two["payload_checksum"]
    assert one["retrieved_at"] != two["retrieved_at"]


def test_no_source_timestamp_is_ever_filled_in_with_a_retrieval_time():
    for state in (scoreboard_from_schedule_game(mlb_game()),
                  scoreboard_from_event(nfl_event())):
        assert state["source_observed_at"] is None
        assert state["retrieved_at"] is not None
