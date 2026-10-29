"""The Arena league on the production backend: a player is their account (owner 2026-10-29)."""

from datetime import UTC, datetime, timedelta

from app.preview_feeds import arena_league


def _user(client, email, name):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "MemberPass123!", "display_name": name})  # secret-guard: test-fixture
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_arena_needs_sign_in(client):
    assert client.post("/preview-api/arena/sync", json={"calls": []}).status_code == 401
    assert client.get("/preview-api/arena/league").status_code == 401


def test_a_player_is_their_account_and_rivals_stay_hidden_until_you_lock(client, monkeypatch, tmp_path):
    monkeypatch.setattr(arena_league, "store_path", lambda settings: tmp_path / "league.json")
    later = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    james, elvis = _user(client, "j@example.com", "James G"), _user(client, "e@example.com", "Elvis")
    call = {"game_id": "g1", "sport": "NHL", "away": "CGY", "home": "EDM", "starts_at": later, "side": "AWAY", "power": 1}
    r = client.post("/preview-api/arena/sync", headers=elvis, json={"profile": "JAMES", "calls": [call]})
    assert r.status_code == 200 and r.json()["profile"] == "ELVIS"       # the browser's profile is ignored
    board = client.get("/preview-api/arena/league", headers=james).json()
    (rival,) = [p for p in board["players"] if p["profile"] == "ELVIS"]
    assert rival["calls"] == [{"game_id": "g1", "starts_at": later, "hidden": True}]
    client.post("/preview-api/arena/sync", headers=james, json={"calls": [{**call, "side": "HOME"}]})
    board = client.get("/preview-api/arena/league", headers=james).json()
    assert board["me"] == "JAMES_G"
    assert [c["side"] for p in board["players"] if p["profile"] == "ELVIS" for c in p["calls"]] == ["AWAY"]


def test_no_change_after_the_start(tmp_path):
    path = tmp_path / "l.json"
    start = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
    call = {"game_id": "g", "starts_at": start.isoformat(), "side": "HOME", "power": 1}
    assert arena_league.sync(path, "u1", "Ann", {"calls": [call]}, now=start - timedelta(minutes=5))[0]["stored"] == 1
    out, _ = arena_league.sync(path, "u1", "Ann", {"calls": [{**call, "side": "AWAY", "starts_at": (start + timedelta(days=1)).isoformat()}]}, now=start + timedelta(minutes=1))
    assert out["refused_late"] == ["g"]
    assert arena_league.league(path, "u1", now=start)["players"][0]["calls"][0]["side"] == "HOME"
