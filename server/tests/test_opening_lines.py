"""Day-before opening prices (app/services/opening_lines.py): extract, bind, earliest-before-start, and isolation from
odds_snapshots (the lane is display only and must never feed a decision)."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.services import opening_lines as ol

ET = ZoneInfo("America/New_York")
RECEIVED = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)  # 11:00 ET


def _event(eid="e1", home="Philadelphia 76ers", away="New York Knicks", commence="2026-10-31T23:30:00Z", books=None):
    books = books if books is not None else {"draftkings": (-150, 130), "fanduel": (-140, 120), "betmgm": (-145, 125)}
    return {"id": eid, "commence_time": commence, "home_team": home, "away_team": away,
            "bookmakers": [{"key": k, "markets": [{"key": "h2h", "outcomes": [
                {"name": home, "price": h}, {"name": away, "price": a}]}]} for k, (h, a) in books.items()]}


def _settings(tmp_path):
    return SimpleNamespace(nba_availability_root=str(tmp_path / "nba-availability"))


class _FakeSession:
    """Answers bind()'s two reads; any write is a failure (the lane never writes the database)."""

    def __init__(self, games):
        self._games = games  # [(game_id, starts_at, home_team_id, away_team_id)]
        self._sides = [[SimpleNamespace(is_home=True, team_id=h), SimpleNamespace(is_home=False, team_id=a)]
                       for _, _, h, a in games]

    def execute(self, _stmt):
        return SimpleNamespace(all=lambda: [(g, s) for g, s, _, _ in self._games])

    def scalars(self, _stmt):
        return self._sides.pop(0)

    def add(self, *_a, **_k):  # pragma: no cover - reaching here is the failure
        raise AssertionError("opening lines must never write the database")

    add_all = merge = add


def test_extract_takes_best_and_median_prices_and_no_vig():
    rows = ol.extract([_event()], league="NBA", received_at=RECEIVED)
    assert len(rows) == 1
    row = rows[0]
    assert row["best_home"] == {"american": -140, "book": "fanduel"}
    assert row["best_away"] == {"american": 130, "book": "draftkings"}
    assert row["median_home_american"] == -145 and row["median_away_american"] == 125
    assert row["books"] == 3 and 0.55 < row["no_vig_home"] < 0.6
    assert row["schema"] == "AISP_OPENING_LINE_V1"


def test_extract_skips_started_events_and_broken_lines():
    started = _event(eid="old", commence="2026-10-30T14:00:00Z")
    three_way = _event(eid="draw")
    three_way["bookmakers"][0]["markets"][0]["outcomes"].append({"name": "Draw", "price": 300})
    three_way["bookmakers"] = three_way["bookmakers"][:1]
    no_books = _event(eid="none", books={})
    assert ol.extract([started, three_way, no_books], league="NBA", received_at=RECEIVED) == []


def test_bind_needs_the_same_teams_on_the_same_eastern_date(monkeypatch):
    names = {"Philadelphia 76ers": "PHI", "New York Knicks": "NYK"}
    monkeypatch.setattr(ol, "_teams", lambda session, league: names.get)
    tip = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)
    session = _FakeSession([("g-right", tip, "PHI", "NYK"), ("g-reversed", tip, "NYK", "PHI")])
    rows = ol.bind(session, "NBA", ol.extract([_event(), _event(eid="e2", home="Unknown Team")],
                                              league="NBA", received_at=RECEIVED))
    assert [r["game_id"] for r in rows] == ["g-right", None]


def test_nfl_names_resolve_by_abbreviation_like_the_nfl_lane():
    class _Teams:
        def execute(self, _stmt):
            return SimpleNamespace(all=lambda: [("t-lv", "Oakland Raiders", "LV"), ("t-nyj", "New York Jets", "NYJ")])

    team_of = ol._teams(_Teams(), "NFL")
    assert team_of("Las Vegas Raiders") == "t-lv" and team_of("New York Jets") == "t-nyj"
    assert team_of("Nowhere Team") is None


def test_openers_keep_the_earliest_line_before_the_start(tmp_path):
    settings = _settings(tmp_path)
    first = ol.extract([_event()], league="NBA", received_at=RECEIVED)
    later = ol.extract([_event(books={"draftkings": (-200, 170)})], league="NBA", received_at=RECEIVED + timedelta(days=1))
    unbound = ol.extract([_event(eid="x", home="Boston Celtics")], league="NBA", received_at=RECEIVED)
    for rows, gid in ((first, "g1"), (later, "g1"), (unbound, None)):
        for r in rows:
            r["game_id"] = gid
    ol.store(settings, "NBA", later, RECEIVED + timedelta(days=1))
    ol.store(settings, "NBA", first + unbound, RECEIVED)
    got = ol.openers(settings, now=RECEIVED + timedelta(days=1))
    assert list(got) == ["g1"]
    assert got["g1"]["best_home"]["american"] == -140  # the 10-30 line, not the 10-31 one


def test_store_writes_only_its_own_jsonl_never_odds_snapshots(tmp_path):
    path = ol.store(_settings(tmp_path), "NBA", ol.extract([_event()], league="NBA", received_at=RECEIVED), RECEIVED)
    assert path == tmp_path / "opening-lines" / "nba" / "2026-10-30.jsonl"
    source = inspect.getsource(ol)
    assert "OddsSnapshot" not in source and "ingest_odds" not in source


def test_endpoint_key_is_never_a_sport_betting_lane():
    from app.services.odds_budget_lanes import league_of_endpoint

    assert all(league_of_endpoint(ol.endpoint_key(lg)) == "" for lg in ol.SPORT_KEYS)


def test_plan_waits_until_ten_eastern():
    early = datetime(2026, 10, 30, 9, 30, tzinfo=ET)
    assert ol.plan(None, None, now=early)["due"] == []
