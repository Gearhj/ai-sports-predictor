"""NBA star-out gate (owner 2026-10-27): frozen star rule, fail-closed states, gate activation and the collector."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.nba import availability as av

TIP = datetime(2026, 10, 27, 23, 30, tzinfo=UTC)
DECIDE = TIP - timedelta(minutes=25)
HOME, AWAY = "2", "17"


def _settings(tmp_path, **kw):
    return SimpleNamespace(nba_availability_root=str(tmp_path), nba_availability_collector_enabled=True, **kw)


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _season(tmp_path, team, games):
    """games: list of {athlete_id: minutes} for completed games, oldest first, all before the role freeze."""

    events = []
    for k, mins in enumerate(games):
        eid = f"{team}{k:02d}"
        when = (TIP - timedelta(days=len(games) - k)).isoformat()
        events.append({"id": eid, "date": when, "completed": True})
        _write(tmp_path / "box" / f"{eid}.json", {"schema": av.SCHEMA_BOX, "espn_event_id": eid, "completed": True,
                                                  "teams": {team: [{"espn_athlete_id": a, "name": f"P{a}", "minutes": m} for a, m in mins.items()]}})
    events.append({"id": f"{team}99", "date": TIP.isoformat(), "completed": False})
    _write(tmp_path / "schedules" / team / "20261027-4.json", {"team": team, "events": events})


def _snapshot(tmp_path, *, observed=None, home_inj=(), away_inj=(), section=True):
    observed = observed or DECIDE - timedelta(minutes=5)
    doc = {"schema": av.SCHEMA_SNAPSHOT, "espn_game_id": "401", "observed_at_utc": observed.isoformat(),
           "teams": {"home": {"espn_team_id": HOME, "abbreviation": "BOS"}, "away": {"espn_team_id": AWAY, "abbreviation": "BKN"}},
           "injuries": {HOME: [{"espn_athlete_id": a, "name": f"P{a}", "raw_status": s, "status": av.normalize_status(s), "listed_at": None} for a, s in home_inj],
                        AWAY: [{"espn_athlete_id": a, "name": f"P{a}", "raw_status": s, "status": av.normalize_status(s), "listed_at": None} for a, s in away_inj]},
           "injury_section_present": section}
    _write(tmp_path / "snapshots" / "401" / f"{observed.strftime('%Y%m%dT%H%M%SZ')}_x.json", doc)


GAME = {"game_id": "g", "espn_game_id": "401", "starts_at": TIP}


@pytest.fixture
def season(tmp_path):
    # home: a(36) b(34) c(30) d(20) over 3 games; away: x(38) y(33) z(31)
    _season(tmp_path, HOME, [{"a": 36, "b": 34, "c": 30, "d": 20}] * 3)
    _season(tmp_path, AWAY, [{"x": 38, "y": 33, "z": 31}] * 3)
    return tmp_path


def test_status_normalisation():
    assert av.normalize_status("Out") == "OUT"
    assert av.normalize_status("Day-To-Day") == "QUESTIONABLE"
    assert av.normalize_status("Questionable") == "QUESTIONABLE"
    assert av.normalize_status("Doubtful") == "DOUBTFUL"
    assert av.normalize_status("Probable") == "PROBABLE"
    assert av.normalize_status("weird") == "UNKNOWN"


def test_clear_when_no_star_is_listed(season):
    _snapshot(season, home_inj=[("d", "Out")])          # a bench player out does not trigger
    r = av.assess(GAME, DECIDE, _settings(season))
    assert r["state"] == "CLEAR"
    assert [s["espn_athlete_id"] for s in r["teams"]["home"]["stars"]] == ["a", "b"]
    assert [s["espn_athlete_id"] for s in r["teams"]["away"]["stars"]] == ["x", "y"]


@pytest.mark.parametrize("status", ["Out", "Doubtful", "Questionable", "Day-To-Day"])
def test_a_star_listed_triggers(season, status):
    _snapshot(season, away_inj=[("y", status)])
    r = av.assess(GAME, DECIDE, _settings(season))
    assert r["state"] == "STAR_OUT" and "Py" in r["reason"]


def test_probable_does_not_trigger(season):
    _snapshot(season, home_inj=[("a", "Probable")])
    assert av.assess(GAME, DECIDE, _settings(season))["state"] == "CLEAR"


def test_unknown_states_fail_closed(season, tmp_path):
    s = _settings(season)
    assert av.assess(GAME, DECIDE, s)["reason"] == "NO_AVAILABILITY_SNAPSHOT_HELD"
    _snapshot(season, observed=DECIDE - timedelta(minutes=50))
    assert av.assess(GAME, DECIDE, s)["reason"] == "AVAILABILITY_SNAPSHOT_STALE"
    # a snapshot taken after the decision clock is never used
    _snapshot(season, observed=DECIDE + timedelta(minutes=1))
    assert av.assess(GAME, DECIDE, s)["state"] == "UNKNOWN"


def test_role_unknown_with_too_few_games_or_a_missing_box(tmp_path):
    _season(tmp_path, HOME, [{"a": 36, "b": 34}])
    _season(tmp_path, AWAY, [{"x": 38, "y": 33}] * 3)
    _snapshot(tmp_path)
    r = av.assess(GAME, DECIDE, _settings(tmp_path))
    assert r["state"] == "UNKNOWN" and "FEWER_THAN_2_COMPLETED_GAMES" in r["reason"]
    _season(tmp_path, HOME, [{"a": 36, "b": 34}] * 3)
    (tmp_path / "box" / f"{HOME}01.json").unlink()
    assert "BOX_SCORE_NOT_HELD" in av.assess(GAME, DECIDE, _settings(tmp_path))["reason"]


def test_traded_away_player_drops_out_but_an_injured_star_stays(tmp_path):
    # t played the first 3 games big then left; 6 more games without him. s is injured and missed the last 5.
    games = [{"t": 40, "s": 39, "a": 30, "b": 28}] * 3 + [{"s": 39, "a": 30, "b": 28}] + [{"a": 30, "b": 28}] * 5
    _season(tmp_path, HOME, games)
    _season(tmp_path, AWAY, [{"x": 38, "y": 33}] * 3)
    _snapshot(tmp_path, home_inj=[("s", "Out")])
    r = av.assess(GAME, DECIDE, _settings(tmp_path))
    stars = [x["espn_athlete_id"] for x in r["teams"]["home"]["stars"]]
    assert "t" not in stars and stars[0] == "s" and r["state"] == "STAR_OUT"


def test_apply_records_only_when_the_gate_is_in_force(season):
    _snapshot(season, home_inj=[("a", "Out")])
    qualified = {"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED", "selection": {"sportsbook": "fanduel", "odds_snapshot_id": "q"}}
    off = av.apply(qualified, GAME, DECIDE, _settings(season))
    assert off["outcome"] == "QUALIFIED" and off["availability"]["state"] == "STAR_OUT"
    on = av.apply(qualified, GAME, DECIDE, _settings(season, nba_star_out_gate_effective_from=DECIDE - timedelta(hours=1)))
    assert on["outcome"] == "RECORD_ONLY" and on["reason"] == av.REASON_OUT
    assert on["funding_restriction"]["record_only"] is True and on["selection"] == qualified["selection"]
    declined = av.apply({"outcome": "DECLINED", "reason": "NO_QUALIFYING_EDGE"}, GAME, DECIDE, _settings(season, nba_star_out_gate_effective_from=DECIDE - timedelta(hours=1)))
    assert declined["outcome"] == "DECLINED" and "availability" in declined


def test_unknown_records_only_with_its_own_reason(tmp_path):
    on = av.apply({"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED"}, GAME, DECIDE,
                  _settings(tmp_path, nba_star_out_gate_effective_from=DECIDE - timedelta(hours=1)))
    assert on["outcome"] == "RECORD_ONLY" and on["reason"] == av.REASON_UNKNOWN


def test_collect_writes_snapshots_schedules_and_boxes(tmp_path, monkeypatch):
    now = TIP - timedelta(hours=2)
    monkeypatch.setattr(av, "due_games", lambda session, now: [GAME])
    summary = {"header": {"competitions": [{"competitors": [{"homeAway": "home", "team": {"id": HOME, "abbreviation": "BOS"}},
                                                            {"homeAway": "away", "team": {"id": AWAY, "abbreviation": "BKN"}}],
                                            "status": {"type": {"completed": True}}, "date": "2026-10-20T23:00Z"}]},
               "injuries": [{"team": {"id": HOME}, "injuries": [{"status": "Out", "athlete": {"id": "a", "displayName": "A"}}]}],
               "boxscore": {"players": [{"team": {"id": HOME}, "statistics": [{"labels": ["MIN", "PTS"], "athletes": [{"athlete": {"id": "a", "displayName": "A"}, "stats": ["35", "20"]}]}]}]}}
    sched = {"season": {"year": 2027}, "events": [{"id": "e1", "date": "2026-10-20T23:00Z", "competitions": [{"status": {"type": {"completed": True}}}]}]}
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps(sched if "/schedule" in url else summary).encode()

    out = av.collect(None, _settings(tmp_path), now=now, fetch=fetch)
    assert out["status"] == "OK" and out["snapshots"] == 1 and out["box_scores_added"] == 1
    assert len(list((tmp_path / "snapshots" / "401").glob("*.json"))) == 1
    box = json.loads((tmp_path / "box" / "e1.json").read_text())
    assert box["teams"][HOME][0]["minutes"] == 35.0
    again = av.collect(None, _settings(tmp_path), now=now, fetch=fetch)
    assert again["box_scores_added"] == 0                       # boxes and today's schedule are cached
    assert av.collect(None, SimpleNamespace(nba_availability_collector_enabled=False), now=now)["status"] == "DISABLED"
