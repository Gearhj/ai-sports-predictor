"""/me/state: the account's copies of the browser-local stores (scale plan Phase 1, 2026-11-02)."""

from app.services import user_state


def _user(client, email, name="Tester"):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "MemberPass123!", "display_name": name})  # secret-guard: test-fixture
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_state_needs_sign_in(client):
    assert client.get("/api/v1/me/state").status_code == 401
    assert client.post("/api/v1/me/state/picks", json={"payload": []}).status_code == 401


def test_round_trip_and_versions(client):
    h = _user(client, "picks@example.com")
    empty = client.get("/api/v1/me/state", headers=h).json()
    assert empty["stores"] == {} and "picks" in empty["known"]

    first = client.post("/api/v1/me/state/picks", json={"payload": [{"id": "g1:HOME", "game_id": "g1"}]}, headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1

    second = client.post("/api/v1/me/state/picks", json={"payload": [], "base_version": 1}, headers=h)
    assert second.status_code == 200 and second.json()["version"] == 2

    got = client.get("/api/v1/me/state", headers=h).json()["stores"]
    assert got["picks"]["payload"] == [] and got["picks"]["version"] == 2
    assert got["picks"]["updated_at"]


def test_a_stale_write_is_refused_with_the_current_copy(client):
    h = _user(client, "stale@example.com")
    client.post("/api/v1/me/state/arena", json={"payload": {"calls": [1]}}, headers=h)
    client.post("/api/v1/me/state/arena", json={"payload": {"calls": [1, 2]}, "base_version": 1}, headers=h)
    stale = client.post("/api/v1/me/state/arena", json={"payload": {"calls": []}, "base_version": 1}, headers=h)
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "STORE_VERSION_CONFLICT"
    assert detail["current"]["version"] == 2 and detail["current"]["payload"] == {"calls": [1, 2]}
    # an unconditional write still wins
    assert client.post("/api/v1/me/state/arena", json={"payload": {"calls": []}}, headers=h).json()["version"] == 3


def test_only_known_stores_and_only_so_much(client, monkeypatch):
    h = _user(client, "limits@example.com")
    assert client.post("/api/v1/me/state/anything-goes", json={"payload": 1}, headers=h).status_code == 404
    monkeypatch.setattr(user_state, "MAX_STORE_BYTES", 40)
    assert client.post("/api/v1/me/state/youcall", json={"payload": list(range(100))}, headers=h).status_code == 413
    assert client.post("/api/v1/me/state/youcall", json={"payload": [1]}, headers=h).status_code == 200


def test_accounts_never_see_each_other(client):
    a = _user(client, "a@example.com", "Ann")
    b = _user(client, "b@example.com", "Bob")
    client.post("/api/v1/me/state/progress", json={"payload": {"days": ["2026-11-02"]}}, headers=a)
    assert client.get("/api/v1/me/state", headers=b).json()["stores"] == {}
    assert client.get("/api/v1/me/state", headers=a).json()["stores"]["progress"]["payload"] == {"days": ["2026-11-02"]}


def test_forgetting_a_store(client):
    h = _user(client, "forget@example.com")
    client.post("/api/v1/me/state/favorites", json={"payload": ["BUF", "KC"]}, headers=h)
    assert client.post("/api/v1/me/state/favorites/delete", headers=h).json() == {"store": "favorites", "removed": 1}
    assert client.get("/api/v1/me/state", headers=h).json()["stores"] == {}
    assert client.post("/api/v1/me/state/nope/delete", headers=h).status_code == 404
