"""Security review 11-01: preview feeds' caches, rate limits and validation; Arena store caps; House Divided drafts cap;
the pinned UFC handoff loader; ESPN ids as path segments. No network: every provider read is mocked."""
from __future__ import annotations

import io
import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import preview_feeds_routes as routes
from app.preview_feeds import arena_league, bounded_cache, court_live, feeds, ufc_archive, ufc_card
from app.preview_feeds.bounded_cache import BoundedTTLCache, cached_fetch


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _client():
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


# -- the bounded cache --------------------------------------------------------------------------------------------
def test_cache_keeps_ttl_semantics_per_lookup():
    clock = Clock()
    c = BoundedTTLCache(4, clock=clock)
    c.set("u", "v", 60)
    assert c.get("u", 60) == "v"
    clock.t += 10
    assert c.get("u", 8) is None          # a live read wants something younger than 8 s
    assert c.get("u", 60) == "v"          # a finished-game read still accepts it
    clock.t += 60
    assert c.get("u", 60) is None


def test_cache_is_bounded_lru_and_drops_expired_first():
    clock = Clock()
    c = BoundedTTLCache(3, clock=clock)
    c.set("old", 1, 5)
    c.set("a", 2, 600)
    c.set("b", 3, 600)
    clock.t += 10                          # "old" has expired
    c.get("a", 600)                        # "a" is now most recent
    c.set("c", 4, 600)
    assert "old" not in c and all(k in c for k in ("a", "b", "c"))
    c.set("d", 5, 600)                     # nothing expired: least recently used ("b") goes
    assert "b" not in c and "a" in c and len(c) == 3


def test_cache_weight_budget_bounds_memory():
    c = BoundedTTLCache(100, max_weight=1000)
    for i in range(10):
        c.set(i, "x", 600, weight=300)
    assert c.weight <= 1000 and len(c) == 3
    c.set("huge", "x", 600, weight=5000)   # heavier than the whole budget: never kept
    assert "huge" not in c and c.weight <= 1000


def test_cache_is_thread_safe():
    c = BoundedTTLCache(64)

    def work(n):
        for i in range(500):
            c.set((n, i), i, 600, weight=1)
            c.get((n, i - 1), 600)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(c) == 64 and c.weight == 64


def test_cached_fetch_calls_the_provider_once_while_fresh():
    clock = Clock()
    c = BoundedTTLCache(8, clock=clock)
    calls = []
    fetch = lambda: (calls.append(1) or {"ok": True}, 10)  # noqa: E731
    assert cached_fetch(c, "k", 30, fetch) == {"ok": True}
    assert cached_fetch(c, "k", 30, fetch) == {"ok": True}
    clock.t += 31
    cached_fetch(c, "k", 30, fetch)
    assert len(calls) == 2


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("module,getter", [(feeds, "_fetch_json"), (court_live, "_get"), (ufc_card, "_get")])
def test_every_feed_reads_through_the_one_bounded_cache(monkeypatch, module, getter):
    cache = BoundedTTLCache(5)
    monkeypatch.setattr(module, "FEED_CACHE", cache)
    hits = []

    def fake_urlopen(req, timeout=None):
        hits.append(req.full_url)
        return _Resp(json.dumps({"url": req.full_url}).encode())

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    get = getattr(module, getter)
    for day in range(40):                   # forty new dates: forty provider reads, but the cache stays at 5
        assert get(f"https://example.invalid/scoreboard?dates=202610{day:02d}", 30)["url"].endswith(f"{day:02d}")
    get("https://example.invalid/scoreboard?dates=20261039", 30)
    assert len(hits) == 40 and len(cache) == 5
    assert not hasattr(module, "_cache")


def test_ufc_rankings_use_the_bounded_cache(monkeypatch):
    cache = BoundedTTLCache(5)
    monkeypatch.setattr(ufc_card, "FEED_CACHE", cache)
    page = b'<div class="view-grouping"><div class="view-grouping-header">Lightweight</div><h5><a href="/x">Champ Name</a></h5></table>'
    hits = []
    monkeypatch.setattr(ufc_card.urllib.request, "urlopen", lambda req, timeout=None: hits.append(1) or _Resp(page))
    assert ufc_card._ranks()["lightweight"]["champion"] == "champ name"
    ufc_card._ranks()
    assert hits == [1] and ufc_card.RANKINGS in cache


def test_outbound_fetches_are_capped(monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(bounded_cache, "_fetch_slots", sem)
    sem.acquire()
    with pytest.raises(TimeoutError):
        with bounded_cache.fetch_limit(timeout=0.01):
            pass
    sem.release()
    with bounded_cache.fetch_limit(timeout=0.01):
        pass


# -- route validation ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["20261027\n", "2026-10-27\n", "20261340", "2026-02-30", "20141231", "30001231",
                                 "２０２６１０２７"])
def test_dates_must_be_real_days_in_the_window(monkeypatch, bad):
    monkeypatch.setattr(court_live, "scores", lambda league, date: pytest.fail("provider must not be called"))
    assert _client().get("/preview-api/scores", params={"league": "nba", "date": bad}).status_code == 400


def test_old_finished_games_and_next_month_stay_open(monkeypatch):
    monkeypatch.setattr(court_live, "scores", lambda league, date: {"league": league, "date": date, "games": []})
    c = _client()
    soon = (datetime.now(UTC) + timedelta(days=20)).strftime("%Y%m%d")
    for ok in ("20150101", "2019-06-01", soon):
        assert c.get("/preview-api/scores", params={"league": "nba", "date": ok}).status_code == 200, ok


def test_team_values_use_fullmatch(monkeypatch):
    monkeypatch.setattr(feeds, "mlb_live", lambda *a: pytest.fail("provider must not be called"))
    r = _client().get("/preview-api/mlb/live", params={"date": "20261027", "home": "NYY\n", "away": "LAD"})
    assert r.status_code == 400


# -- rate limits --------------------------------------------------------------------------------------------------
def test_feed_routes_are_rate_limited_per_client(monkeypatch):
    monkeypatch.setattr(court_live, "scores", lambda league, date: {"games": []})
    # FEED_LIMITS is read when a route is declared, so mount the same handler with a budget of 3
    app = FastAPI()
    app.add_api_route("/s", routes.scores, dependencies=[routes._feed_limit("scores-test", 3)])
    c = TestClient(app)
    codes = [c.get("/s", params={"league": "nba", "date": "20261027"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_a_heavy_single_user_stays_under_every_feed_budget():
    # One user, four tabs, for one minute: Game Night (5 leagues / 20 s) + Alerts (5 leagues / 45 s) per tab.
    scores_per_min = 4 * (5 * 3 + 5 * 60 / 45)
    assert scores_per_min < routes.FEED_LIMITS["scores"]
    assert 4 * 6 < routes.FEED_LIMITS["mlb"] and 4 * 6 < routes.FEED_LIMITS["arena_live"]
    # A Sunday slate polling 14 live games every 30 s in two tabs, plus a Live View every 10 s.
    assert 2 * 14 * 2 + 6 < routes.FEED_LIMITS["football"]
    assert 4 * 6 < routes.FEED_LIMITS["ufc_bout"] and 4 * 2 < routes.FEED_LIMITS["ufc_card"]


def test_every_outbound_feed_route_declares_a_limit():
    limited = {r.path for r in routes.router.routes if getattr(r, "dependencies", None)}
    for path in ("/preview-api/mlb/live", "/preview-api/football/live", "/preview-api/scores", "/preview-api/arena/live",
                 "/preview-api/ufc/card", "/preview-api/ufc/bout", "/preview-api/logos/cfb", "/preview-api/arena/league"):
        assert path in limited, path


# -- Arena store caps ---------------------------------------------------------------------------------------------
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _call(gid, *, start=NOW + timedelta(hours=3), side="HOME"):
    return {"game_id": gid, "sport": "NBA", "away": "A", "home": "H", "starts_at": start.isoformat(), "side": side, "power": 1}


def test_arena_caps_new_games_per_player_but_keeps_stored_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(arena_league, "MAX_CALLS_PER_PLAYER", 3)
    path = tmp_path / "l.json"
    out, status = arena_league.sync(path, "u", "Ann", {"calls": [_call(f"g{i}") for i in range(5)]}, now=NOW)
    assert status == 200 and out["stored"] == 3 and out["refused_cap"] == ["g3", "g4"]
    # an existing call can still change side; settled/stored calls are never evicted
    out, _ = arena_league.sync(path, "u", "Ann", {"calls": [_call("g0", side="AWAY"), _call("g9")]}, now=NOW)
    stored = json.loads(path.read_text(encoding="utf-8"))["players"]["u"]["calls"]
    assert set(stored) == {"g0", "g1", "g2"} and stored["g0"]["side"] == "AWAY" and out["refused_cap"] == ["g9"]


def test_arena_refuses_to_write_past_the_file_cap(tmp_path, monkeypatch):
    path = tmp_path / "l.json"
    arena_league.sync(path, "u", "Ann", {"calls": [_call("g0")]}, now=NOW)
    before = path.read_bytes()
    monkeypatch.setattr(arena_league, "MAX_STORE_BYTES", len(before) + 10)
    out, status = arena_league.sync(path, "u", "Ann", {"calls": [_call(f"n{i}") for i in range(20)]}, now=NOW)
    assert status == 507 and "error" in out and path.read_bytes() == before


def test_arena_game_ids_and_start_times_are_checked(tmp_path):
    path = tmp_path / "l.json"
    calls = [_call("../../x"), _call("a" * 65), _call("ok\n"), _call("9f1c2d4e-0000-4000-8000-000000000001"),
             _call("past", start=NOW - timedelta(minutes=1)), _call("far", start=NOW + timedelta(days=400))]
    out, _ = arena_league.sync(path, "u", "Ann", {"calls": calls}, now=NOW)
    assert out["stored"] == 1 and set(out["refused_late"]) == {"past", "far"}
    naive = {**_call("naive"), "starts_at": "2026-11-01T15:00:00"}   # no zone: read as UTC, not a 500
    out, status = arena_league.sync(path, "u", "Ann", {"calls": [naive]}, now=NOW)
    assert status == 200 and out["stored"] == 2


def test_arena_league_board_is_rate_limited():
    route = next(r for r in routes.router.routes if r.path == "/preview-api/arena/league")
    assert route.dependencies


# -- House Divided drafts cap -------------------------------------------------------------------------------------
from test_house_divided import KICK, add_game, quote, start, world  # noqa: E402,F401  (fixture reuse)


def test_house_divided_caps_open_drafts_per_participant(world, monkeypatch):  # noqa: F811
    from app.services import house_divided as hd

    monkeypatch.setattr(hd, "MAX_OPEN_DRAFTS", 2)
    start(world)
    game, o = add_game(world)
    snap = quote(world, o["home_ml"], at=KICK - timedelta(hours=1))
    first = None
    for _ in range(2):
        first = hd.save_draft(world["session"], participant_key="SCOTTY", odds_snapshot_id=snap.id, stake="1",
                              actor=world["admin"], now=KICK - timedelta(minutes=40))
    with pytest.raises(hd.CompetitionError) as full:
        hd.save_draft(world["session"], participant_key="SCOTTY", odds_snapshot_id=snap.id, stake="1",
                      actor=world["admin"], now=KICK - timedelta(minutes=40))
    assert full.value.code == "TOO_MANY_DRAFTS" and full.value.status == 409
    # editing an existing draft is still allowed, and another participant has their own budget
    hd.save_draft(world["session"], participant_key="SCOTTY", odds_snapshot_id=snap.id, stake="2",
                  actor=world["admin"], wager_id=first.id, now=KICK - timedelta(minutes=39))
    hd.save_draft(world["session"], participant_key="RED_LOU", odds_snapshot_id=snap.id, stake="1",
                  actor=world["admin"], now=KICK - timedelta(minutes=39))


# -- the pinned UFC handoff loader --------------------------------------------------------------------------------
def test_ufc_handoff_loader_runs_only_the_pinned_bytes(tmp_path, monkeypatch):
    import hashlib

    loader = b"def load_handoff(root, *, include_outcomes=False):\n    return {'ran': True}\n"
    contract = b'{"schemaVersion": "x"}'
    (tmp_path / "read_handoff.py").write_bytes(loader)
    (tmp_path / "CONTRACT.json").write_bytes(contract)
    monkeypatch.setattr(ufc_archive, "HANDOFF", tmp_path)
    monkeypatch.setattr(ufc_archive, "PINNED_SHA256", {"read_handoff.py": hashlib.sha256(loader).hexdigest(),
                                                        "CONTRACT.json": hashlib.sha256(contract).hexdigest()})
    assert ufc_archive._loader() == {"ran": True}
    (tmp_path / "read_handoff.py").write_bytes(loader + b"import os\n")
    with pytest.raises(ufc_archive.HandoffPinError):
        ufc_archive._loader()
    (tmp_path / "read_handoff.py").write_bytes(loader)
    (tmp_path / "CONTRACT.json").write_bytes(contract + b" ")
    with pytest.raises(ufc_archive.HandoffPinError):
        ufc_archive._loader()


def test_a_changed_handoff_is_a_502_the_page_handles(tmp_path, monkeypatch):
    monkeypatch.setattr(ufc_archive, "HANDOFF", tmp_path)
    monkeypatch.setattr(ufc_archive, "_cache", None)
    r = _client().get("/preview-api/ufc/archive")
    assert r.status_code == 502 and "error" in r.json()


# -- ESPN ids as path segments ------------------------------------------------------------------------------------
def test_espn_ids_never_become_unsafe_path_segments(tmp_path):
    from app.nba import availability as av
    from app.services import availability_board as ab

    for bad in ("..", "../x", "1/2", "C:", "1\\2", "", " 1", "1\n", "a" * 25, "１２"):
        with pytest.raises(ValueError):
            av._espn_id(bad)
        assert not ab._safe_id(bad)
    assert av._espn_id("401585601") == "401585601"
    moment = NOW
    assert av._latest_snapshot(tmp_path, "../../etc", moment) is None
    assert av._team_roles(tmp_path, "..", moment, set())["reason"] == "BAD_TEAM_ID"
    assert ab._nhl_skaters(tmp_path, "..\\x", moment, set())["reason"] == "BAD_TEAM_ID"
    assert ab._nhl_goalie(tmp_path, "../", moment) is None
    view = ab.game_view(SimpleNamespace(nba_availability_root=str(tmp_path)), "NBA", "../../x", {"teams": {}}, moment)
    assert view["reason"] == "NO_SNAPSHOT_HELD"


def test_nba_collector_skips_a_bad_game_id_without_writing(tmp_path, monkeypatch):
    from app.nba import availability as av

    settings = SimpleNamespace(nba_availability_root=str(tmp_path), nba_availability_collector_enabled=True)
    monkeypatch.setattr(av, "due_games", lambda session, now: [{"game_id": "g", "starts_at": NOW, "espn_game_id": "../evil"}])
    out = av.collect(None, settings, now=NOW, fetch=lambda url: pytest.fail("no request for a bad id"))
    assert out["status"] == "PARTIAL" and not (tmp_path / "evil").exists() and not (tmp_path.parent / "evil").exists()
