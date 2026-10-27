"""Every-sport availability board (owner 2026-10-27): parsing, key players per league, fail-closed states."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services import availability_board as ab

NOW = datetime(2026, 10, 27, 20, 0, tzinfo=UTC)
START = NOW + timedelta(hours=3)


def _s(tmp_path):
    return SimpleNamespace(nba_availability_root=str(tmp_path), nba_availability_collector_enabled=True)


def _w(p, doc):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(doc), encoding="utf-8")


def _summary(home_inj=(), probables=None, injuries=True):
    raw = {"header": {"competitions": [{"date": START.isoformat(), "competitors": [
        {"homeAway": "home", "team": {"id": "1", "abbreviation": "HOM"}, "probables": probables or []},
        {"homeAway": "away", "team": {"id": "2", "abbreviation": "AWY"}}]}]}}
    if injuries:
        raw["injuries"] = [{"team": {"id": "1"}, "injuries": [{"status": s, "athlete": {"id": a, "displayName": a}} for a, s in home_inj]}]
    return raw


def _snap(tmp_path, lg, raw):
    doc = ab.parse_summary(raw, league=lg, event_id="9", observed_at=NOW - timedelta(minutes=5), url="u")
    _w(tmp_path / "leagues" / lg.lower() / "snapshots" / "9" / "a.json", doc)
    return {"name": "AWY at HOM", "starts_at": START.isoformat(), "teams": doc["teams"]}


def test_long_term_lists_count_as_out_and_are_flagged():
    doc = ab.parse_summary(_summary(home_inj=[("a", "60-Day-IL"), ("b", "Injured Reserve"), ("c", "Day-To-Day")]), league="MLB", event_id="9", observed_at=NOW, url="u")
    rows = {r["espn_athlete_id"]: r for r in doc["injuries"]["1"]}
    assert rows["a"]["status"] == "OUT" and rows["a"]["long_term"] and rows["b"]["long_term"]
    assert rows["c"]["status"] == "QUESTIONABLE" and not rows["c"]["long_term"]


def test_nfl_qb1_out_is_key_out(tmp_path):
    meta = _snap(tmp_path, "NFL", _summary(home_inj=[("q1", "Out")]))
    for tid, q in (("1", "q1"), ("2", "q2")):
        _w(tmp_path / "leagues" / "nfl" / "depth" / tid / "20261027.json", {"qb1": {"espn_athlete_id": q, "name": q}})
    v = ab.game_view(_s(tmp_path), "NFL", "9", meta, NOW)
    assert v["state"] == "KEY_OUT" and "q1 OUT" in v["reason"]
    assert v["teams"]["home"]["notable"][0]["espn_athlete_id"] == "q1"


def test_cfb_without_an_injury_list_is_unknown_not_healthy(tmp_path):
    meta = _snap(tmp_path, "CFB", _summary(injuries=False))
    v = ab.game_view(_s(tmp_path), "CFB", "9", meta, NOW)
    assert v["state"] == "UNKNOWN" and v["reason"] in ("NO_PUBLIC_INJURY_LIST", "HOM KEY_PLAYERS_UNKNOWN", "AWY KEY_PLAYERS_UNKNOWN")


def test_mlb_probable_pitcher(tmp_path):
    meta = _snap(tmp_path, "MLB", _summary(probables=[{"athlete": {"id": "sp", "displayName": "Ace"}}]))
    v = ab.game_view(_s(tmp_path), "MLB", "9", meta, NOW)
    assert v["teams"]["home"]["key_players"][0]["role"] == "Probable SP"
    assert v["state"] == "UNKNOWN"                      # the away team has no probable listed


def test_nhl_ranks_by_points_and_finds_the_starting_goalie(tmp_path):
    out = tmp_path / "leagues" / "nhl"
    events = []
    for k in range(4):
        eid = f"e{k}"; events.append({"id": eid, "date": (START - timedelta(days=4 - k)).isoformat(), "completed": True})
        rows = [{"espn_athlete_id": "d", "name": "D", "minutes": 26, "points": 0, "goalie": False},
                {"espn_athlete_id": "s", "name": "S", "minutes": 19, "points": 2, "goalie": False},
                {"espn_athlete_id": "f", "name": "F", "minutes": 18, "points": 1, "goalie": False},
                {"espn_athlete_id": "g1" if k else "g2", "name": "G", "minutes": 0, "points": 0, "goalie": True}]
        _w(out / "box2" / f"{eid}.json", {"teams": {"1": rows, "2": rows}})
    for tid in ("1", "2"):
        _w(out / "schedules" / tid / "x.json", {"events": events})
    meta = _snap(tmp_path, "NHL", _summary(home_inj=[("g1", "Out")]))
    v = ab.game_view(_s(tmp_path), "NHL", "9", meta, NOW)
    keys = v["teams"]["home"]["key_players"]
    assert [k["espn_athlete_id"] for k in keys] == ["s", "f", "g1"]
    assert v["state"] == "KEY_OUT" and "G OUT" in v["reason"]


def test_board_reads_the_day_index(tmp_path):
    meta = _snap(tmp_path, "NBA", _summary())
    _w(tmp_path / "leagues" / "nba" / "index" / "2026-10-27.json", {"9": meta})
    b = ab.board(_s(tmp_path), "2026-10-27", now=NOW)
    assert b["games"][0]["league"] == "NBA" and b["games"][0]["espn_event_id"] == "9"
