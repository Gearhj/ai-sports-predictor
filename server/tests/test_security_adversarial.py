"""Adversarial security regressions for the public-facing API surface.

These lock in the authentication, authorization, and abuse-control behaviour
examined in the deep security audit. They must fail loudly if a future change
weakens token validation, cross-role enforcement, or the rate-limit brake.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.security import ALGORITHM, create_access_token


def _register_member(client, email: str = "adversary@example.com") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "MemberPass123!", "display_name": "Adversary"},
    )
    assert response.status_code == 201
    return response.json()


# --- Authentication: token validation ---------------------------------------


def test_missing_token_is_rejected(client) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_malformed_bearer_is_rejected(client) -> None:
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-jwt"}
    ).status_code == 401


def test_tampered_signature_is_rejected(client) -> None:
    token = _register_member(client)["access_token"]
    forged = token[:-2] + ("aa" if token[-2:] != "aa" else "bb")
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}
    ).status_code == 401


def test_alg_none_token_is_rejected(client, settings) -> None:
    member = _register_member(client)["user"]
    unsigned = jwt.encode(
        {"sub": member["id"], "type": "access"}, key="", algorithm="none"
    )
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {unsigned}"}
    ).status_code == 401


def test_wrong_secret_signature_is_rejected(client, settings) -> None:
    member = _register_member(client)["user"]
    forged = create_access_token(member["id"], "an-entirely-different-secret-value", 60)
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}
    ).status_code == 401


def test_expired_token_is_rejected(client, settings) -> None:
    member = _register_member(client)["user"]
    expired = jwt.encode(
        {
            "sub": member["id"],
            "type": "access",
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
        },
        settings.secret_key,
        algorithm=ALGORITHM,
    )
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
    ).status_code == 401


def test_non_access_token_type_is_rejected(client, settings) -> None:
    member = _register_member(client)["user"]
    refresh_shaped = jwt.encode(
        {
            "sub": member["id"],
            "type": "refresh",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        settings.secret_key,
        algorithm=ALGORITHM,
    )
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh_shaped}"}
    ).status_code == 401


# --- Authorization: server-side role is authoritative -----------------------


def test_unauthenticated_admin_route_is_rejected(client) -> None:
    assert client.post("/api/v1/admin/jobs/rebuild-features").status_code == 401


def test_member_cannot_reach_admin_route(client) -> None:
    token = _register_member(client)["access_token"]
    assert client.post(
        "/api/v1/admin/jobs/rebuild-features",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 403


def test_forged_admin_role_claim_does_not_escalate(client, settings) -> None:
    """A validly-signed token with a spoofed role must not grant admin.

    Authorization re-reads the role from the database, so an attacker who can
    mint a role claim (they cannot, without the secret) still gains nothing.
    """
    member = _register_member(client)["user"]
    # ver=0 matches the freshly registered member's session generation, so the
    # token authenticates as that member and is blocked purely by the DB role.
    escalated = create_access_token(
        member["id"], settings.secret_key, 60, claims={"role": "admin", "ver": 0}
    )
    denied = client.post(
        "/api/v1/admin/jobs/rebuild-features",
        headers={"Authorization": f"Bearer {escalated}"},
    )
    assert denied.status_code == 403


# --- Abuse control: login rate limiting -------------------------------------


def test_login_is_rate_limited(client, settings) -> None:
    limit = settings.auth_rate_limit_per_minute
    statuses = [
        client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "WrongPassword1!"},
        ).status_code
        for _ in range(limit + 1)
    ]
    assert 401 in statuses  # attempts before the cap are processed normally
    assert statuses[-1] == 429  # the attempt past the cap is throttled


def test_rate_limit_can_be_disabled(session, settings) -> None:
    """Operators can disable the brake for a trusted single-tenant context."""
    from app.core.config import get_settings
    from app.db.session import get_db
    from app.main import app
    from fastapi.testclient import TestClient

    open_settings = settings.model_copy(update={"rate_limit_enabled": False})

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: open_settings
    try:
        with TestClient(app) as unthrottled:
            statuses = [
                unthrottled.post(
                    "/api/v1/auth/login",
                    json={"email": "nobody@example.com", "password": "WrongPassword1!"},
                ).status_code
                for _ in range(open_settings.auth_rate_limit_per_minute + 3)
            ]
        assert 429 not in statuses
    finally:
        app.dependency_overrides.clear()
