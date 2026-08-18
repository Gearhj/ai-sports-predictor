from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.models import Game, UserRole
from app.services.auth_service import BootstrapConflictError, ensure_admin, register_user


@pytest.mark.asyncio
async def test_demo_vertical_slice_and_typed_endpoints(client, session, settings, admin_headers) -> None:
    result = await seed_demo_data(session, settings, date(2026, 8, 12))
    assert result["mode"] == "DEMO"
    dashboard = client.get("/api/v1/dashboard/today")
    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert payload["data_mode"] == "empty"
    assert payload["summary"]["games"] == 0
    assert payload["summary"]["complete_forecasts"] == 0
    assert payload["games"] == []
    demo_game = session.scalar(select(Game).order_by(Game.starts_at).limit(1))
    assert demo_game is not None
    game_id = str(demo_game.id)
    assert client.get(f"/api/v1/games/{game_id}").status_code == 200
    markets = client.get("/api/v1/markets/board?date=2026-08-12")
    assert markets.status_code == 200
    assert markets.json()["data_mode"] == "demo"
    assert len(markets.json()["books"]) == 3
    assert client.get("/api/v1/performance").json()["brier_score"] is None
    assert client.get("/api/v1/bankroll").status_code == 200
    assert client.get("/api/v1/health/sources?date=2026-08-12", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/health", headers=admin_headers).json()["database"] == "reachable"
    # Operational telemetry is admin-only: unauthenticated callers are rejected.
    assert client.get("/api/v1/health/sources?date=2026-08-12").status_code == 401
    assert client.get("/api/v1/health").status_code == 401


def test_authentication_and_admin_role_authorization(client, session, settings) -> None:
    admin = ensure_admin(session, settings)
    login = client.post("/api/v1/auth/login", json={"email": settings.admin_email, "password": settings.admin_password})
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "ADMIN"
    token = login.json()["access_token"]
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    member = client.post("/api/v1/auth/register", json={"email": "member@example.com", "password": "MemberPass123!", "display_name": "Member"})
    assert member.status_code == 201
    assert member.json()["user"]["role"] == "MEMBER"
    denied = client.post("/api/v1/admin/jobs/rebuild-features", headers={"Authorization": f"Bearer {member.json()['access_token']}"})
    assert denied.status_code == 403
    allowed = client.post("/api/v1/admin/jobs/rebuild-features", headers={"Authorization": f"Bearer {token}"})
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "succeeded"


def test_admin_bootstrap_fails_closed_on_existing_member(session, settings) -> None:
    register_user(session, settings, settings.admin_email, "MemberPass123!", "Not Admin")
    with pytest.raises(BootstrapConflictError):
        ensure_admin(session, settings)
