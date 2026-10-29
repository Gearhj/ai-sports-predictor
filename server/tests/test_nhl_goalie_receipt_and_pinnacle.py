"""NHL starting-goalie receipt + backup-goalie rule, and the Pinnacle benchmark lane (owner 2026-10-28)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.nhl import goalie_receipt as gr
from app.services import availability_board as board
from app.services import sharp_benchmark as sharp

START = datetime(2026, 10, 29, 0, 30, tzinfo=UTC)
NOW = START - timedelta(minutes=25)


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _settings(tmp_path, gate=None):
    return SimpleNamespace(nba_availability_root=str(tmp_path), nhl_backup_goalie_gate_effective_from=gate)


def _fixture(tmp_path, *, edm_starter="G1E", cgy_starter="G1C", status="confirmed", observed=NOW - timedelta(minutes=5)):
    out = tmp_path / "leagues" / "nhl"
    _write(out / "index" / "2026-10-28.json", {"401": {"name": "Calgary Flames at Edmonton Oilers", "starts_at": START.isoformat(),
                                                      "teams": {"home": {"espn_team_id": "6", "abbreviation": "EDM"},
                                                                "away": {"espn_team_id": "3", "abbreviation": "CGY"}}}})
    probable = lambda aid: [{"espn_athlete_id": aid, "name": aid, "kind": "probableStartingGoalie", "status": status.upper()}] if aid else []  # noqa: E731
    _write(out / "snapshots" / "401" / "a.json", {
        "observed_at_utc": observed.isoformat(), "starts_at": START.isoformat(), "injury_section_present": True,
        "teams": {"home": {"espn_team_id": "6", "abbreviation": "EDM"}, "away": {"espn_team_id": "3", "abbreviation": "CGY"}},
        "injuries": {"6": [], "3": []}, "probables": {"6": probable(edm_starter), "3": probable(cgy_starter)}})
    for tid, g1, g2 in (("6", "G1E", "G2E"), ("3", "G1C", "G2C")):
        events = [{"id": f"{tid}{k}", "date": (START - timedelta(days=10 - k)).isoformat(), "completed": True} for k in range(5)]
        _write(out / "schedules" / tid / "s.json", {"team": tid, "events": events})
        for k, e in enumerate(events):
            goalie = g2 if k == 4 else g1  # usual starter = 4 of the last 5
            _write(out / "box2" / f"{e['id']}.json", {"teams": {tid: [{"espn_athlete_id": goalie, "name": goalie, "goalie": True, "minutes": 0},
                                                                      {"espn_athlete_id": f"S{tid}", "name": f"S{tid}", "goalie": False, "minutes": 20, "points": 1, "appeared": True}]}})


@pytest.fixture(autouse=True)
def teams(monkeypatch):
    monkeypatch.setattr(gr, "_teams", lambda session, game_id: {"home": "EDM", "away": "CGY"})


GAME = {"game_id": "g", "starts_at": START}


def _decision(side):
    return {"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED", "selection": {"side": side, "sportsbook": "draftkings", "odds_snapshot_id": "s1"}}


def test_receipt_names_both_starters_and_usual_goalies(tmp_path):
    _fixture(tmp_path)
    r = gr.assess(None, _settings(tmp_path), GAME, NOW)
    assert r["state"] == "STARTERS_CONFIRMED" and r["funding_authority"] is False
    assert r["teams"]["home"]["starting_goalie"]["name"] == "G1E" and r["teams"]["home"]["usual_goalie"]["espn_athlete_id"] == "G1E"
    assert r["teams"]["away"]["backup_starting"] is False and r["flags"] == []


def test_backing_a_team_on_its_backup_goalie_is_record_only_when_the_rule_is_on(tmp_path):
    _fixture(tmp_path, cgy_starter="G2C")
    on = _settings(tmp_path, gate="2026-10-28T00:00:00+00:00")
    out = gr.apply(None, _decision("AWAY"), GAME, NOW, on)
    assert out["outcome"] == "RECORD_ONLY" and out["reason"] == gr.REASON
    assert out["funding_restriction"]["record_only"] is True and out["funding_restriction"]["starting_goalie"]["name"] == "G2C"
    assert "CGY_BACKUP_GOALIE_CONFIRMED" in out["goalie_injury"]["flags"]
    # betting AGAINST the backup is untouched, and so is everything while the rule is off
    assert gr.apply(None, _decision("HOME"), GAME, NOW, on)["outcome"] == "QUALIFIED"
    assert gr.apply(None, _decision("AWAY"), GAME, NOW, _settings(tmp_path))["outcome"] == "QUALIFIED"


def test_an_unlisted_or_stale_starter_never_blocks(tmp_path):
    on = _settings(tmp_path, gate="2026-10-28T00:00:00+00:00")
    _fixture(tmp_path, cgy_starter=None)
    out = gr.apply(None, _decision("AWAY"), GAME, NOW, on)
    assert out["outcome"] == "QUALIFIED" and out["goalie_injury"]["state"] == "STARTER_UNKNOWN"


def test_stale_snapshot_is_unknown_and_never_blocks(tmp_path):
    on = _settings(tmp_path, gate="2026-10-28T00:00:00+00:00")
    _fixture(tmp_path, cgy_starter="G2C", observed=NOW - timedelta(minutes=50))
    out = gr.apply(None, _decision("AWAY"), GAME, NOW, on)
    assert out["outcome"] == "QUALIFIED" and out["goalie_injury"]["state"] == "UNKNOWN"


def test_a_broken_source_is_unknown_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "assess", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    out = gr.apply(None, _decision("AWAY"), GAME, NOW, _settings(tmp_path, gate="2026-10-28T00:00:00+00:00"))
    assert out["outcome"] == "QUALIFIED" and out["goalie_injury"]["reason"] == "ASSESSMENT_FAILED:OSError"


def test_scoreboard_probables_keep_espn_status():
    c = {"probables": [{"name": "probableStartingGoalie", "athlete": {"id": "102", "displayName": "Devin Cooley"},
                        "status": {"name": "Confirmed", "type": "confirmed"}}]}
    assert board._probables(c) == [{"espn_athlete_id": "102", "name": "Devin Cooley", "kind": "probableStartingGoalie", "status": "CONFIRMED"}]


def test_pinnacle_extract_keeps_only_the_two_way_pinnacle_line(tmp_path):
    raw = [{"id": "e1", "commence_time": "2026-10-29T00:30:00Z", "home_team": "Edmonton Oilers", "away_team": "Calgary Flames",
            "bookmakers": [{"key": "betfair_ex_eu", "markets": []},
                           {"key": "pinnacle", "last_update": "2026-10-29T00:01:00Z", "markets": [{"key": "h2h", "outcomes": [
                               {"name": "Edmonton Oilers", "price": -205}, {"name": "Calgary Flames", "price": 185}]}]}]},
           {"id": "e2", "commence_time": "2026-10-29T01:00:00Z", "home_team": "A", "away_team": "B",
            "bookmakers": [{"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [{"name": "A", "price": 150}, {"name": "B", "price": 200}, {"name": "Draw", "price": 400}]}]}]}]
    received = datetime(2026, 10, 29, 0, 5, tzinfo=UTC)
    rows = sharp.extract(raw, league="NHL", received_at=received)
    assert len(rows) == 1 and rows[0]["home_american"] == -205 and rows[0]["eu_books_in_response"] == 2
    s = _settings(tmp_path)
    sharp.store(s, "NHL", rows, received)
    close = sharp.closing_lines(s, "NHL", ["2026-10-28"])
    (row,) = close.values()
    assert 0.64 < row["no_vig_home"] < 0.66


def test_pinnacle_endpoint_key_is_never_a_sport_betting_lane():
    from app.services.odds_budget_lanes import league_of_endpoint

    assert all(league_of_endpoint(sharp.endpoint_key(lg)) == "" for lg in sharp.SPORT_KEYS)
