"""Beta feedback (10-28): signed-in readers only; the owner reads it."""
from app.api import feedback_routes


def _member(client):
    r = client.post("/api/v1/auth/register", json={"email": "friend@example.com", "password": "FriendPass123!", "display_name": "Friend"})  # secret-guard: test-fixture
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_feedback_needs_sign_in_and_reaches_the_owner(client, admin_headers, tmp_path, monkeypatch):
    monkeypatch.setattr(feedback_routes, "STORE", tmp_path / "feedback.jsonl")
    note = {"page": "/game-night", "rating": 4, "kind": "idea", "text": "Make the grid bigger."}
    assert client.post("/api/v1/feedback", json=note).status_code == 401
    assert client.post("/api/v1/feedback", json=note, headers=_member(client)).status_code == 201
    items = client.get("/api/v1/admin/feedback", headers=admin_headers).json()["items"]
    assert items[0]["text"] == "Make the grid bigger." and items[0]["email"] == "friend@example.com"


def test_only_the_admin_reads_feedback(client):
    assert client.get("/api/v1/admin/feedback", headers=_member(client)).status_code == 403
