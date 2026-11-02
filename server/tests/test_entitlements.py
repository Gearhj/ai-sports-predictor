"""PREMIUM entitlement wiring (scale plan Phase 1, item 3)."""

from datetime import UTC, datetime, timedelta


def _member(client, email, name="Member Tester"):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "MemberPass123!", "display_name": name})  # secret-guard: test-fixture
    assert r.status_code == 201, r.text
    body = r.json()
    return body["user"]["id"], {"Authorization": f"Bearer {body['access_token']}"}


def test_a_plain_member_has_no_entitlements_and_the_beta_keeps_everything_open(client):
    _, h = _member(client, "plain@example.com")
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert me["entitlements"] == []
    assert client.get("/api/v1/public/config").json()["premium_gating_enabled"] is False
    assert client.get("/api/v1/me/premium", headers=h).status_code == 200  # gating off
    assert client.get("/api/v1/me/premium").status_code == 401  # but never anonymous


def test_the_owner_grants_and_revokes_premium(client, admin_headers):
    uid, h = _member(client, "grant@example.com", "Grant Me")
    listed = client.get("/api/v1/admin/users?q=grant", headers=admin_headers).json()
    assert [u["email"] for u in listed["users"]] == ["grant@example.com"]
    assert client.get("/api/v1/admin/users", headers=h).status_code == 403  # members cannot

    granted = client.post(f"/api/v1/admin/users/{uid}/entitlements", json={"kind": "premium", "active": True}, headers=admin_headers)
    assert granted.status_code == 200, granted.text
    assert granted.json()["active"] == ["premium"]
    assert client.get("/api/v1/auth/me", headers=h).json()["entitlements"] == ["premium"]
    # a fresh sign-in carries it too
    login = client.post("/api/v1/auth/login", json={"email": "grant@example.com", "password": "MemberPass123!"}).json()  # secret-guard: test-fixture
    assert login["user"]["entitlements"] == ["premium"]

    revoked = client.post(f"/api/v1/admin/users/{uid}/entitlements", json={"kind": "premium", "active": False}, headers=admin_headers).json()
    assert revoked["active"] == [] and revoked["entitlements"][0]["status"] == "revoked"
    assert client.get("/api/v1/auth/me", headers=h).json()["entitlements"] == []


def test_gating_on_admits_premium_and_admins_only(client, admin_headers, settings):
    settings.premium_gating_enabled = True
    try:
        assert client.get("/api/v1/public/config").json()["premium_gating_enabled"] is True
        uid, h = _member(client, "gated@example.com")
        refused = client.get("/api/v1/me/premium", headers=h)
        assert refused.status_code == 403 and refused.json()["detail"]["code"] == "PREMIUM_REQUIRED"
        assert client.get("/api/v1/me/premium", headers=admin_headers).status_code == 200
        client.post(f"/api/v1/admin/users/{uid}/entitlements", json={"kind": "premium", "active": True}, headers=admin_headers)
        assert client.get("/api/v1/me/premium", headers=h).status_code == 200
        # an expired grant no longer counts
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        client.post(f"/api/v1/admin/users/{uid}/entitlements", json={"kind": "premium", "active": True, "expires_at": past}, headers=admin_headers)
        assert client.get("/api/v1/me/premium", headers=h).status_code == 403
        assert client.get("/api/v1/auth/me", headers=h).json()["entitlements"] == []
    finally:
        settings.premium_gating_enabled = False


def test_admin_is_a_role_not_an_entitlement(client, admin_headers):
    uid, _ = _member(client, "role@example.com")
    assert client.post(f"/api/v1/admin/users/{uid}/entitlements", json={"kind": "admin"}, headers=admin_headers).status_code == 403
    assert client.post(f"/api/v1/admin/users/{uid}/entitlements", json={"kind": "gold"}, headers=admin_headers).status_code == 422
    assert client.post("/api/v1/admin/users/00000000-0000-0000-0000-000000000000/entitlements", json={"kind": "premium"}, headers=admin_headers).status_code == 404
