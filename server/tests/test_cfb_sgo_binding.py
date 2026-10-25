"""The SportsGameOdds College Football binder and its review artifact, against LABELLED FIXTURES.

The league is ``FIXTURE_NCAAF`` and every team id is ``FIXTURE_*``: AISP has
never observed a real SportsGameOdds College Football identifier, and nothing
here may be read as one.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.cfb import sgo_binding, sgo_price
from app.cfb.sgo_binding import (
    AMBIGUOUS_IN_RESPONSE, AMBIGUOUS_START, CONFLICT_WITHIN_ARTIFACT, CONFLICTS_WITH_REVIEWED_MAP,
    LEAGUE_UNEXPECTED, NO_CFBD_CANDIDATE, ORIENTATION_SUSPECT, PROPOSED, ScheduleGame,
)

LEAGUE = "FIXTURE_NCAAF"
KICK = datetime(2026, 9, 26, 17, 0, tzinfo=UTC)


def game(gid="900", *, kick=KICK, home=("11", "Fixture State"), away=("22", "Fixture Tech"), neutral=False):
    return ScheduleGame(gid, kick, home[0], away[0], home[1], away[1], neutral)


def event(eid="EV1", *, league=LEAGUE, starts=KICK, home=("FIXTURE_STATE_T", "Fixture State Owls"),
          away=("FIXTURE_TECH_T", "Fixture Tech Hawks")):
    return {"eventID": eid, "leagueID": league,
            "status": {"startsAt": starts.isoformat().replace("+00:00", "Z"), "oddsPresent": True},
            "teams": {"home": {"teamID": home[0], "names": {"long": home[1]}},
                      "away": {"teamID": away[0], "names": {"long": away[1]}}}}


def unresolved(team_map=None, league=None):
    return SimpleNamespace(league_id=league, team_map=dict(team_map or {}))


def review(events, schedule, binding=None, candidate=LEAGUE):
    return sgo_binding.review_events({"data": events}, schedule=schedule, binding=binding or unresolved(),
                                     league_candidate=candidate, source={"response_body_sha256": "f" * 64},
                                     generated_at=KICK - timedelta(hours=1))


def test_a_uniquely_timed_event_proposes_both_teams_with_orientation_and_the_observed_league():
    artifact = review([event()], [game(), game("901", kick=KICK + timedelta(minutes=30))])
    record = artifact["events"][0]
    assert record["status"] == PROPOSED and record["cfbd_game"]["game_id"] == "900"
    assert record["name_evidence"] == "NAMES_SUPPORT_ORIENTATION"
    assert [(p["side"], p["sgo_team_id"], p["cfbd_team_id"]) for p in artifact["proposals"]] == [
        ("home", "FIXTURE_STATE_T", "11"), ("away", "FIXTURE_TECH_T", "22")]
    assert artifact["league"] == {"pinned": None, "request_candidate": LEAGUE, "observed": [LEAGUE],
                                  "pinnable": LEAGUE}
    assert len(artifact["artifact_sha256"]) == 64


@pytest.mark.parametrize("schedule,events,status", [
    # Two CFBD games inside five minutes of the event: never guessed between.
    ([game(), game("901", kick=KICK + timedelta(minutes=3))], [event()], AMBIGUOUS_START),
    ([game(kick=KICK + timedelta(minutes=20))], [event()], NO_CFBD_CANDIDATE),
])
def test_start_time_must_be_unique_and_within_tolerance(schedule, events, status):
    assert review(events, schedule)["events"][0]["status"] == status
    assert review(events, schedule)["proposals"] == []


def test_unique_both_ways_two_events_for_one_game_propose_nothing():
    artifact = review([event("EV1"), event("EV2", home=("X_T", "X"), away=("Y_T", "Y"))], [game()])
    assert {e["status"] for e in artifact["events"]} == {AMBIGUOUS_IN_RESPONSE}
    assert artifact["proposals"] == []


def test_another_league_is_receipted_not_proposed():
    artifact = review([event(league="FIXTURE_OTHER")], [game()])
    assert artifact["events"][0]["status"] == LEAGUE_UNEXPECTED and artifact["league"]["pinnable"] is None


def test_names_never_bind_but_reversed_names_withhold_the_proposal():
    reversed_names = event(home=("FIXTURE_STATE_T", "Fixture Tech Hawks"), away=("FIXTURE_TECH_T", "Fixture State Owls"))
    assert review([reversed_names], [game()])["events"][0]["status"] == ORIENTATION_SUSPECT
    # Uninformative names do not block: the start time and uniqueness are the binding evidence.
    opaque = event(home=("FIXTURE_STATE_T", "QQQ"), away=("FIXTURE_TECH_T", "ZZZ"))
    record = review([opaque], [game()])["events"][0]
    assert record["status"] == PROPOSED and record["name_evidence"] == "NAMES_INCONCLUSIVE"


def test_neutral_sites_are_flagged_for_the_reviewer():
    record = review([event()], [game(neutral=True)])["events"][0]
    assert record["status"] == PROPOSED and "NEUTRAL_SITE_ORIENTATION_NEEDS_REVIEW" in record["flags"]


def test_a_proposal_that_contradicts_the_reviewed_map_is_a_conflict():
    reviewed = unresolved({"FIXTURE_STATE_T": "99"}, league=LEAGUE)
    assert review([event()], [game()], binding=reviewed)["events"][0]["status"] == CONFLICTS_WITH_REVIEWED_MAP
    school_taken = unresolved({"FIXTURE_ELSE_T": "11"}, league=LEAGUE)
    assert review([event()], [game()], binding=school_taken)["events"][0]["status"] == CONFLICTS_WITH_REVIEWED_MAP


def test_one_id_proposed_for_two_schools_inside_one_artifact_withdraws_both():
    second = game("902", kick=KICK + timedelta(hours=3), home=("33", "Other U"), away=("44", "Fourth U"))
    artifact = review([event("EV1"), event("EV2", starts=second.kickoff, home=("FIXTURE_STATE_T", "Owls"),
                                           away=("FIXTURE_FOUR_T", "Fourth"))], [game(), second])
    assert {e["status"] for e in artifact["events"]} == {CONFLICT_WITHIN_ARTIFACT}
    assert artifact["proposals"] == []


def test_apply_review_writes_a_one_to_one_map_the_price_path_accepts(tmp_path):
    artifact = review([event()], [game()])
    document = sgo_binding.apply_review(artifact, approve=["EV1:home", "EV1:away"], reviewer="Fixture Reviewer",
                                        reviewed_at=KICK)
    assert document["schema"] == sgo_price.TEAM_MAP_SCHEMA and document["leagueID"] == LEAGUE
    assert all(e["reviewed"] is True and e["evidence"]["artifactSha256"] == artifact["artifact_sha256"]
               for e in document["entries"])
    path = tmp_path / "map.json"
    path.write_text(json.dumps(document))
    binding = sgo_price.load_binding(SimpleNamespace(cfb_sgo_league_id=LEAGUE, cfb_sgo_team_map_path=str(path)))
    assert binding.resolved and binding.team_map == {"FIXTURE_STATE_T": "11", "FIXTURE_TECH_T": "22"}


def test_apply_review_refuses_what_is_not_an_exact_proposal():
    artifact = review([event()], [game(), game("901", kick=KICK + timedelta(minutes=2))])   # ambiguous
    with pytest.raises(ValueError):
        sgo_binding.apply_review(artifact, approve=["EV1:home"], reviewer="R")
    exact = review([event()], [game()])
    with pytest.raises(ValueError):
        sgo_binding.apply_review(exact, approve=["EV1:home"], reviewer="  ")


def test_a_map_that_is_not_one_to_one_binds_nothing(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"schema": sgo_price.TEAM_MAP_SCHEMA, "leagueID": LEAGUE, "entries": [
        {"sgoTeamID": "FIXTURE_A", "cfbdTeamId": 1, "reviewed": True},
        {"sgoTeamID": "FIXTURE_B", "cfbdTeamId": 1, "reviewed": True}]}))
    binding = sgo_price.load_binding(SimpleNamespace(cfb_sgo_league_id=LEAGUE, cfb_sgo_team_map_path=str(path)))
    assert not binding.resolved and "TEAM_MAP_NOT_ONE_TO_ONE" in binding.problems and binding.team_map == {}


def test_the_review_reads_the_retained_bytes_and_verifies_them(tmp_path):
    from app.ingestion.archive import RawArchive

    body = json.dumps({"success": True, "data": [event()]}).encode()
    record = RawArchive(tmp_path).write_response("SPORTSGAMEODDS", body, request_fingerprint="fp",
                                                 extra_metadata={"lane": sgo_price.LANE,
                                                                 "requested_at": KICK.isoformat()})
    raw, meta = sgo_binding.body_from_archive(record.path)
    assert raw == body and meta["requested_at"] == KICK.isoformat()
    assert sgo_binding.review_events(json.loads(raw), schedule=[game()], binding=unresolved(),
                                     league_candidate=LEAGUE)["events"][0]["status"] == PROPOSED


def test_the_review_script_reviews_and_applies_without_any_request(tmp_path, monkeypatch, capsys):
    from app.ingestion.archive import RawArchive
    from scripts import cfb_sgo_binding_review as script

    schedule = tmp_path / "games.json"
    schedule.write_text(json.dumps([{"id": 900, "homeId": 11, "awayId": 22, "homeTeam": "Fixture State",
                                     "awayTeam": "Fixture Tech", "startDate": KICK.isoformat()}]))
    settings = SimpleNamespace(cfb_independent_schedule_path=str(schedule), cfb_sgo_league_id="",
                               cfb_sgo_team_map_path="", raw_data_root=str(tmp_path / "raw"))
    monkeypatch.setattr(script, "get_settings", lambda: settings)
    record = RawArchive(tmp_path / "raw").write_response(
        "SPORTSGAMEODDS", json.dumps({"data": [event()]}).encode(), request_fingerprint="fp",
        extra_metadata={"requested_at": KICK.isoformat(), "request_filters": {"leagueID": LEAGUE}})
    assert script.main(["review", "--archive", record.path, "--out", str(tmp_path / "review")]) == 0
    reviewed = json.loads(capsys.readouterr().out)
    assert reviewed["proposals"] == ["EV1:home", "EV1:away"] and reviewed["league"]["pinnable"] == LEAGUE
    out = tmp_path / "map.json"
    assert script.main(["apply", "--artifact", reviewed["artifact"], "--approve", "EV1:home,EV1:away",
                        "--reviewer", "Fixture Reviewer", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["leagueID"] == LEAGUE
