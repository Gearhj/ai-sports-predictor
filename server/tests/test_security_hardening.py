"""Authenticated-beta hardening regressions.

Covers the production boundary controls added in the hardening pass: CORS
fail-closed guards, OpenAPI docs disablement, security headers (incl. HSTS),
the registration password floor, operational-telemetry protection, and the
token-revocation / logout foundation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.config import Settings, get_settings
from app.core.middleware import SecurityHeadersMiddleware
from app.db.session import get_db
from app.main import app, create_app


_PROD_BASE = dict(
    app_env="production",
    secret_key="x" * 40,
    database_url="postgresql+psycopg://asp:asp@localhost:5432/asp",
    demo_mode=False,
    admin_password="StrongAdminPassword123!",
)


def _prod_settings(**overrides) -> Settings:
    return Settings(**{**_PROD_BASE, **overrides})


# --- CORS production guards --------------------------------------------------


def test_production_rejects_wildcard_cors() -> None:
    with pytest.raises(ValueError, match="wildcard"):
        _prod_settings(cors_origins="*")


def test_production_rejects_http_external_origin() -> None:
    with pytest.raises(ValueError, match="https"):
        _prod_settings(cors_origins="http://app.example.com")


def test_production_accepts_explicit_https_origin() -> None:
    settings = _prod_settings(cors_origins="https://app.example.com")
    assert settings.cors_origin_list == ["https://app.example.com"]


def test_development_localhost_origins_still_allowed() -> None:
    settings = Settings(
        app_env="development",
        cors_origins="http://localhost:5173,http://127.0.0.1:5173",
    )
    assert settings.cors_origin_list == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


# --- OpenAPI docs disablement -----------------------------------------------


def test_production_disables_docs() -> None:
    prod_app = create_app(_prod_settings(cors_origins="https://app.example.com"))
    assert prod_app.docs_url is None
    assert prod_app.redoc_url is None
    assert prod_app.openapi_url is None
    with TestClient(prod_app) as prod_client:
        assert prod_client.get("/docs").status_code == 404
        assert prod_client.get("/openapi.json").status_code == 404


def test_development_keeps_docs() -> None:
    dev_app = create_app(Settings(app_env="development"))
    assert dev_app.openapi_url == "/openapi.json"


# --- Security headers --------------------------------------------------------


def test_security_headers_present_on_api_response(client) -> None:
    response = client.get("/api/v1/public/config")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers


def test_hsts_only_in_production() -> None:
    async def ok(_request):
        return PlainTextResponse("ok")

    route = [Route("/", ok)]

    prod = Starlette(routes=route)
    prod.add_middleware(SecurityHeadersMiddleware, hsts=True)
    with TestClient(prod) as prod_client:
        assert "Strict-Transport-Security" in prod_client.get("/").headers

    dev = Starlette(routes=route)
    dev.add_middleware(SecurityHeadersMiddleware, hsts=False)
    with TestClient(dev) as dev_client:
        assert "Strict-Transport-Security" not in dev_client.get("/").headers


# --- Registration password policy -------------------------------------------


def test_register_rejects_password_below_twelve(client) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "shortpw@example.com", "password": "Eleven123!!", "display_name": "Shorty"},
    )
    assert response.status_code == 422  # 11 chars


def test_register_accepts_twelve_char_password(client) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "okpw@example.com", "password": "TwelveChar12", "display_name": "Okay"},
    )
    assert response.status_code == 201


def test_login_unaffected_by_stricter_register_floor(client, session, settings) -> None:
    """An account whose password predates the 12-char floor can still log in."""
    from app.core.security import hash_password
    from app.models import User, UserRole, UserStatus

    legacy = User(
        email="legacy@example.com",
        password_hash=hash_password("EightCh1"),  # 8 chars, old minimum
        display_name="Legacy",
        role=UserRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    session.add(legacy)
    session.commit()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "legacy@example.com", "password": "EightCh1"},
    )
    assert response.status_code == 200


# --- Operational telemetry protection ---------------------------------------


def test_health_sources_requires_admin(client) -> None:
    assert client.get("/api/v1/health/sources").status_code == 401


def test_health_sources_forbidden_for_member(client) -> None:
    member = client.post(
        "/api/v1/auth/register",
        json={"email": "member2@example.com", "password": "MemberPass123!", "display_name": "Member"},
    ).json()
    denied = client.get(
        "/api/v1/health/sources",
        headers={"Authorization": f"Bearer {member['access_token']}"},
    )
    assert denied.status_code == 403


def test_public_liveness_remains_open(client) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/system/ping").status_code == 200


# --- Token revocation / logout ----------------------------------------------


def test_logout_revokes_current_token(client) -> None:
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": "logout@example.com", "password": "MemberPass123!", "display_name": "Bye"},
    ).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    # The same token is rejected after logout, even though it has not expired.
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_logout_requires_authentication(client) -> None:
    assert client.post("/api/v1/auth/logout").status_code == 401


def test_issued_token_carries_jti_and_version(client, settings) -> None:
    import jwt

    token = client.post(
        "/api/v1/auth/register",
        json={"email": "claims@example.com", "password": "MemberPass123!", "display_name": "Claims"},
    ).json()["access_token"]
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    assert payload["jti"]
    assert payload["ver"] == 0
    assert payload["type"] == "access"
