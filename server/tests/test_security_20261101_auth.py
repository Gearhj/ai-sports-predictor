"""Security review 2026-11-01: login lockout keying, Access-bound registration, token lifetime, admin-only
provider budget, and the inactive-account password oracle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core import access_identity
from app.core.security import ALGORITHM, InvalidTokenError, create_access_token, decode_access_token

OWNER = {"email": "owner@example.com", "password": "OwnerPass123!", "display_name": "Owner"}  # secret-guard: test-fixture
AUD = "test-aud-tag"
ISS = "https://team.cloudflareaccess.com"
HS_KEY = "jwks-test-key-with-at-least-thirty-two-chars"


def _use(settings, **kw):
    from app.core.config import get_settings
    from app.main import app

    s = settings.model_copy(update=kw)
    app.dependency_overrides[get_settings] = lambda: s
    return s


def _login(client, password, ip):
    return client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": password},
                       headers={"X-Forwarded-For": ip})


# ---- 1. admin-lockout-dos -----------------------------------------------------------------------------------
def test_another_client_cannot_lock_the_owner_out(client, settings):
    _use(settings, login_failure_limit=3, auth_rate_limit_per_minute=1000, rate_limit_trust_forwarded_for=True)
    assert client.post("/api/v1/auth/register", json=OWNER).status_code == 201
    codes = [_login(client, "WrongPass123!", "10.0.0.66").status_code for _ in range(3)]  # secret-guard: test-fixture
    assert codes == [401, 401, 401]
    locked = _login(client, OWNER["password"], "10.0.0.66")
    assert locked.status_code == 429 and "Too many failed" in locked.json()["detail"]
    assert _login(client, OWNER["password"], "10.0.0.1").status_code == 200


def test_account_cap_slows_distributed_guessing_but_never_refuses_the_right_password(client, settings):
    _use(settings, login_failure_limit=100, login_account_failure_limit=4, auth_rate_limit_per_minute=1000,
         rate_limit_trust_forwarded_for=True)
    assert client.post("/api/v1/auth/register", json=OWNER).status_code == 201
    codes = [_login(client, "WrongPass123!", f"10.0.1.{i}").status_code for i in range(6)]  # secret-guard: test-fixture
    assert codes == [401, 401, 401, 401, 429, 429]
    assert _login(client, OWNER["password"], "10.0.2.9").status_code == 200


def test_success_clears_only_this_clients_count(client, settings):
    from app.core.rate_limit import get_rate_limiter

    _use(settings, login_failure_limit=3, auth_rate_limit_per_minute=1000, rate_limit_trust_forwarded_for=True)
    assert client.post("/api/v1/auth/register", json=OWNER).status_code == 201
    _login(client, "WrongPass123!", "10.0.0.66")  # secret-guard: test-fixture
    _login(client, "WrongPass123!", "10.0.0.1")  # secret-guard: test-fixture
    assert _login(client, OWNER["password"], "10.0.0.1").status_code == 200
    limiter = get_rate_limiter()
    assert limiter.count(f"login-fail:{OWNER['email']}:10.0.0.1", 900) == 0
    assert limiter.count(f"login-fail:{OWNER['email']}:10.0.0.66", 900) == 1
    assert limiter.count(f"login-fail-account:{OWNER['email']}", 900) == 2


# ---- 5. inactive-account-password-oracle ----------------------------------------------------------------------
def test_inactive_account_gets_the_generic_failure_whatever_the_password(client, session):
    from app.models import User, UserStatus

    assert client.post("/api/v1/auth/register", json=OWNER).status_code == 201
    user = session.query(User).filter_by(email=OWNER["email"]).one()
    user.status = UserStatus.DISABLED
    session.commit()
    right = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    wrong = client.post("/api/v1/auth/login", json={"email": OWNER["email"], "password": "WrongPass123!"})  # secret-guard: test-fixture
    assert right.status_code == wrong.status_code == 401
    assert right.json() == wrong.json() == {"detail": "Invalid email or password"}


# ---- 2. invite squatting / Access identity ----------------------------------------------------------------------
class _FakeJWKS:
    def __init__(self, fail=False):
        self.fail = fail

    def get_signing_key_from_jwt(self, token):
        if self.fail:
            raise jwt.PyJWKClientError("network down")
        return type("K", (), {"key": HS_KEY})()


@pytest.fixture
def fake_jwks(monkeypatch):
    """Mock the JWKS client. The venv has no crypto backend for RS256, so the mocked key is an HMAC secret
    and the accepted algorithm list is widened to HS256 for these tests only; aud/iss/exp checks are real."""
    fake = _FakeJWKS()
    monkeypatch.setattr(access_identity, "_jwks_client", lambda url: fake)
    monkeypatch.setattr(access_identity, "ALGORITHMS", ["HS256"])
    return fake


def _assertion(email, aud=AUD, iss=ISS, exp_minutes=10):
    now = datetime.now(UTC)
    return jwt.encode({"email": email, "aud": aud, "iss": iss, "iat": now, "exp": now + timedelta(minutes=exp_minutes),
                       "sub": "cf-user"}, HS_KEY, algorithm="HS256")


def _gated(settings, on=True):
    return _use(settings, registration_mode="invite", registration_allowlist="friend@example.com",
                registration_require_access_identity=on, cf_access_aud=AUD, cf_access_issuer=ISS)


BODY = {"email": "Friend@Example.com", "password": "FriendPass123!", "display_name": "Friend"}  # secret-guard: test-fixture


def _register(client, token=None, extra=None):
    headers = dict(extra or {})
    if token is not None:
        headers["Cf-Access-Jwt-Assertion"] = token
    return client.post("/api/v1/auth/register", json=BODY, headers=headers)


def test_register_with_a_matching_access_identity_succeeds(client, settings, fake_jwks):
    _gated(settings)
    assert _register(client, _assertion("FRIEND@example.com")).status_code == 201


def test_register_refuses_a_mismatched_identity_with_the_generic_message(client, settings, fake_jwks):
    _gated(settings)
    mismatch = _register(client, _assertion("someone-else@example.com"))
    stranger = client.post("/api/v1/auth/register", json={**BODY, "email": "stranger@example.com"},
                           headers={"Cf-Access-Jwt-Assertion": _assertion("stranger@example.com")})
    assert mismatch.status_code == stranger.status_code == 403
    assert mismatch.json() == stranger.json()


def test_register_refuses_a_missing_assertion_even_with_the_plain_email_header(client, settings, fake_jwks):
    _gated(settings)
    assert _register(client).status_code == 403
    assert _register(client, extra={"Cf-Access-Authenticated-User-Email": "friend@example.com"}).status_code == 403


@pytest.mark.parametrize("kw", [{"aud": "another-app"}, {"iss": "https://evil.cloudflareaccess.com"},
                                {"exp_minutes": -10}])
def test_register_refuses_bad_audience_issuer_or_expiry(client, settings, fake_jwks, kw):
    _gated(settings)
    assert _register(client, _assertion("friend@example.com", **kw)).status_code == 403


def test_register_fails_closed_when_the_keys_cannot_be_fetched(client, settings, fake_jwks):
    fake_jwks.fail = True
    _gated(settings)
    assert _register(client, _assertion("friend@example.com")).status_code == 403


def test_register_refuses_a_forged_signature(client, settings, fake_jwks):
    _gated(settings)
    now = datetime.now(UTC)
    forged = jwt.encode({"email": "friend@example.com", "aud": AUD, "iss": ISS, "iat": now,
                         "exp": now + timedelta(minutes=5)}, "a-different-key-of-thirty-two-chars!!", algorithm="HS256")
    assert _register(client, forged).status_code == 403


def test_register_without_the_setting_needs_no_assertion(client, settings):
    _gated(settings, on=False)
    assert _register(client).status_code == 201


def test_production_algorithm_list_is_rs256_only():
    assert access_identity.ALGORITHMS == ["RS256"]


def test_real_rs256_assertion_round_trip(monkeypatch):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(access_identity, "_signing_key", lambda token, settings: private.public_key())
    s = type("S", (), {"cf_access_aud": AUD, "cf_access_issuer": ISS, "cf_access_certs_url": "https://x"})()
    now = datetime.now(UTC)
    claims = {"email": "Friend@Example.com", "aud": AUD, "iss": ISS, "iat": now, "exp": now + timedelta(minutes=5)}
    assert access_identity.verified_access_email(jwt.encode(claims, private, algorithm="RS256"), s) == "friend@example.com"
    assert access_identity.verified_access_email(jwt.encode({**claims, "aud": "x"}, private, algorithm="RS256"), s) is None


# ---- 3. token lifetime bounded on decode ------------------------------------------------------------------------
SECRET = "test-secret-with-at-least-thirty-two-characters"  # secret-guard: test-fixture


def test_operator_length_tokens_still_decode():
    token = create_access_token("user-1", SECRET, 360)
    assert decode_access_token(token, SECRET, max_lifetime_minutes=720)["sub"] == "user-1"


def test_overlong_tokens_are_refused():
    token = create_access_token("user-1", SECRET, 721)
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, SECRET, max_lifetime_minutes=720)


@pytest.mark.parametrize("drop", ["exp", "iat", "sub"])
def test_tokens_missing_required_claims_are_refused(drop):
    now = datetime.now(UTC)
    claims = {"sub": "user-1", "type": "access", "iat": now, "exp": now + timedelta(minutes=30)}
    claims.pop(drop)
    with pytest.raises(InvalidTokenError):
        decode_access_token(jwt.encode(claims, SECRET, algorithm=ALGORITHM), SECRET, max_lifetime_minutes=720)


def test_overlong_token_is_refused_by_the_api(client, settings):
    member = client.post("/api/v1/auth/register", json=OWNER).json()["user"]
    long_lived = create_access_token(member["id"], settings.secret_key, 60 * 24 * 30, claims={"ver": 0})
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {long_lived}"}).status_code == 401


# ---- 4. provider budget is admin only ---------------------------------------------------------------------------
def test_provider_budget_requires_an_administrator(client):
    assert client.get("/api/v1/provider-budget").status_code == 401
    token = client.post("/api/v1/auth/register", json=OWNER).json()["access_token"]
    assert client.get("/api/v1/provider-budget", headers={"Authorization": f"Bearer {token}"}).status_code == 403
