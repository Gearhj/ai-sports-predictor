"""Scoreboard situation detail the feeds already deliver (hotfix 2026-10-24, step 9; display only).

KEEP what the sources send and the collectors used to drop, inside the existing
``bases`` JSON of the observation row (no migration, no new column), and expose
it through ``/api/v1/scoreboard``'s existing ``situation`` field as ``detail``:

* MLB (schedule hydrate linescore): ``offense.batter`` / ``onDeck`` / ``inHole``
  and ``defense.pitcher`` -- id + full name each, a pitch count only when the
  node itself carries one;
* NFL (ESPN ``competitions[0].situation``): down, distance, yardLine, possession
  (team id + abbreviation), downDistanceText, isRedZone, lastPlay.text and
  lastPlay.probability (home/away win percentage), labelled ``source: ESPN``.

Nothing here is read by settlement, and a payload without the detail produces
exactly the row it produced before. Parser fixtures only; no network.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.models import GameScoreboardObservation
from app.scoreboard import mlb, nfl_espn
from app.scoreboard.read_service import _situation
from tests.test_scoreboard import mlb_game
from tests.test_scoreboard_nfl_espn import espn_event

NOW = datetime(2026, 10, 24, 23, 40, tzinfo=UTC)


# ------------------------------------------------------------------- MLB ----
def _live_mlb(**linescore_over):
    game = mlb_game(status={"abstractGameState": "Live", "codedGameState": "I",
                            "detailedState": "In Progress", "statusCode": "I"})
    game["linescore"].update({
        "currentInning": 6, "currentInningOrdinal": "6th", "inningState": "Bottom", "outs": 1,
        "offense": {
            "batter": {"id": 660271, "fullName": "Shohei Ohtani", "link": "/api/v1/people/660271"},
            "onDeck": {"id": 605141, "fullName": "Mookie Betts"},
            "inHole": {"id": 518692, "fullName": "Freddie Freeman"},
            "first": {"id": 1, "fullName": "Runner One"},
            "team": {"id": 119, "name": "Los Angeles Dodgers"},
        },
        "defense": {
            "pitcher": {"id": 543037, "fullName": "Gerrit Cole", "pitchCount": 87},
            "catcher": {"id": 2, "fullName": "Somebody Else"},
            "team": {"id": 147, "name": "New York Yankees"},
        },
        **linescore_over,
    })
    return game


def test_mlb_keeps_batter_on_deck_in_hole_and_pitcher_beside_the_runners():
    state = mlb.scoreboard_from_schedule_game(_live_mlb(), retrieved_at=NOW)
    assert state["status"] == "IN_PROGRESS"
    # Regression (WS Game 2, 2026-10-24): the status text must stay a string; the
    # situation dict once overwrote it and the VARCHAR insert failed every refresh.
    assert state["status_detail"] == "In Progress"
    bases = state["bases"]
    assert bases["first"] == "Runner One" and bases["second"] is None and bases["third"] is None
    assert bases["detail"] == {
        "source": "MLB_STATS_API",
        "batter": {"id": 660271, "full_name": "Shohei Ohtani"},
        "on_deck": {"id": 605141, "full_name": "Mookie Betts"},
        "in_hole": {"id": 518692, "full_name": "Freddie Freeman"},
        "pitcher": {"id": 543037, "full_name": "Gerrit Cole", "pitch_count": 87},
    }
    # The innings already kept are untouched by the addition.
    assert len(state["periods"]) == 9 and state["outs"] == 1


def test_mlb_detail_is_absent_when_the_feed_names_nobody_and_before_first_pitch():
    quiet = mlb.scoreboard_from_schedule_game(_live_mlb(offense={"first": {"fullName": "Runner One"}}, defense={}),
                                              retrieved_at=NOW)
    assert "detail" not in quiet["bases"] and quiet["bases"]["first"] == "Runner One"
    scheduled = mlb_game(status={"abstractGameState": "Preview", "codedGameState": "S",
                                 "detailedState": "Scheduled", "statusCode": "S"})
    scheduled["linescore"]["defense"] = {"pitcher": {"id": 1, "fullName": "Probable Starter"}}
    assert mlb.scoreboard_from_schedule_game(scheduled, retrieved_at=NOW)["bases"] is None


def test_mlb_rows_without_detail_hash_exactly_as_before():
    """A payload the feed sends without these nodes yields the same checksum as before this change."""

    plain = mlb_game(status={"abstractGameState": "Live", "codedGameState": "I",
                             "detailedState": "In Progress", "statusCode": "I"})
    state = mlb.scoreboard_from_schedule_game(plain, retrieved_at=NOW)
    assert state["bases"] == {"first": None, "second": None, "third": None}
    assert state["payload_checksum"] == mlb._sha({k: v for k, v in state.items() if k in {
        "status", "status_detail", "away_score", "home_score", "period_number", "period_label", "period_state",
        "in_break", "clock_seconds_remaining", "outs", "balls", "strikes", "bases", "periods", "periods_complete",
        "periods_reconcile"}})


# ------------------------------------------------------------------- NFL ----
def _live_espn(**situation):
    event = espn_event()
    competition = event["competitions"][0]
    for side in competition["competitors"]:
        side["team"]["id"] = {"DEN": "7", "KC": "12"}[side["team"]["abbreviation"]]
    competition["situation"] = {
        "$ref": "http://sports.core.api.espn.com/...",
        "lastPlay": {"id": "4016872100", "type": {"id": "5", "text": "Rush"},
                     "text": "(6:52 - 3rd) P.Mahomes pass short right to T.Kelce to DEN 18 for 11 yards",
                     "probability": {"tiePercentage": 0.0, "homeWinPercentage": 0.2743,
                                     "awayWinPercentage": 0.7257, "secondsLeft": 0}},
        "down": 1, "yardLine": 18, "distance": 10, "downDistanceText": "1st & 10 at DEN 18",
        "shortDownDistanceText": "1st & 10", "possessionText": "DEN 18", "isRedZone": True,
        "homeTimeouts": 3, "awayTimeouts": 2, "possession": "12",
        **situation,
    }
    return event


def test_espn_keeps_down_distance_possession_last_play_and_its_win_probability():
    state = nfl_espn.state_from_event(_live_espn(), retrieved_at=NOW)
    assert state["status"] == "IN_PROGRESS" and state["clock_seconds_remaining"] == 412
    assert state["bases"] == {"detail": {
        "source": "ESPN",
        "down": 1, "distance": 10, "yard_line": 18,
        "down_distance_text": "1st & 10 at DEN 18",
        "is_red_zone": True,
        "possession": {"team_id": "12", "abbreviation": "KC", "home_away": "away"},
        "last_play": {
            "text": "(6:52 - 3rd) P.Mahomes pass short right to T.Kelce to DEN 18 for 11 yards",
            "home_win_percentage": 0.2743, "away_win_percentage": 0.7257, "probability_source": "ESPN",
        },
    }}
    # The clock and quarters already kept are untouched.
    assert [p["number"] for p in state["periods"]] == [1, 2, 3]


def test_espn_detail_degrades_field_by_field_and_is_absent_when_espn_sends_none():
    partial = nfl_espn.state_from_event(_live_espn(possession="99", lastPlay={"text": "Timeout"}, isRedZone=None),
                                        retrieved_at=NOW)
    detail = partial["bases"]["detail"]
    assert detail["possession"] == {"team_id": "99", "abbreviation": None, "home_away": None}   # unknown team: no guess
    assert detail["last_play"] == {"text": "Timeout", "home_win_percentage": None, "away_win_percentage": None,
                                   "probability_source": None}
    assert detail["is_red_zone"] is None
    # No situation node at all: the row is exactly what it was before (bases None).
    assert nfl_espn.state_from_event(espn_event(), retrieved_at=NOW)["bases"] is None
    # A final carries no live situation even if ESPN leaves the node in place.
    final = _live_espn()
    final["competitions"][0]["status"] = {"clock": 0.0, "period": 4,
                                          "type": {"name": "STATUS_FINAL", "state": "post", "completed": True,
                                                   "shortDetail": "Final"}}
    assert nfl_espn.state_from_event(final, retrieved_at=NOW)["bases"] is None


def test_espn_rows_without_detail_hash_exactly_as_before():
    before = nfl_espn.state_from_event(espn_event(), retrieved_at=NOW)
    assert before["bases"] is None
    again = nfl_espn.state_from_event(espn_event(), retrieved_at=NOW)
    assert again["payload_checksum"] == before["payload_checksum"]


# ---------------------------------------------------------- the read surface --
def _row(**fields):
    base = dict(league_code="NFL", source="ESPN_NFL_SCOREBOARD", source_mode="LIVE", status="IN_PROGRESS",
                retrieved_at=NOW, checked_at=NOW, payload_checksum="x" * 64, periods_complete=False)
    return GameScoreboardObservation(**{**base, **fields})


def test_the_read_surface_exposes_detail_inside_the_existing_situation_field():
    nfl_row = _row(bases={"detail": {"source": "ESPN", "down": 3, "distance": 2}})
    situation = _situation(nfl_row)
    assert situation["detail"] == {"source": "ESPN", "down": 3, "distance": 2}
    assert situation["bases_occupied"] == [] and situation["runners"] == {"first": None, "second": None, "third": None}
    assert situation["outs"] is None
    mlb_row = _row(league_code="MLB", source="MLB_STATS_API", outs=2, balls=1, strikes=2,
                   bases={"first": "Runner One", "second": None, "third": None,
                          "detail": {"source": "MLB_STATS_API", "batter": {"id": 1, "full_name": "A"}}})
    situation = _situation(mlb_row)
    assert situation["bases_occupied"] == ["first"] and situation["runners"]["first"] == "Runner One"
    assert situation["detail"]["batter"] == {"id": 1, "full_name": "A"}


def test_rows_without_detail_read_exactly_as_before():
    assert _situation(_row()) is None
    legacy = _situation(_row(league_code="MLB", outs=1, bases={"first": None, "second": "B", "third": None}))
    assert legacy == {"outs": 1, "balls": None, "strikes": None, "bases_occupied": ["second"],
                      "runners": {"first": None, "second": "B", "third": None}, "detail": None}
    assert _situation(_row(bases={"detail": "not a mapping"}))["detail"] is None
