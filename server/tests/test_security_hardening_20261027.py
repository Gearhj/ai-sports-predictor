"""Security review 2026-10-27: write routes that could touch the paper bankroll require a signed-in user."""

TICKET = {"sportsbook": "draftkings", "stake": 1.0, "funded": False,
          "legs": [{"game_id": "00000000-0000-0000-0000-000000000001", "market": "MONEYLINE", "selection": "NYY ML"},
                   {"game_id": "00000000-0000-0000-0000-000000000002", "market": "MONEYLINE", "selection": "BOS ML"}]}


def _member(client):
    r = client.post("/api/v1/auth/register", json={"email": "member@example.com", "password": "MemberPass123!", "display_name": "Member"})  # secret-guard: test-fixture
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_anonymous_parlay_ticket_is_refused(client):
    assert client.post("/api/v1/mlb/parlays/tickets", json=TICKET).status_code == 401


def test_member_cannot_fund_a_ticket_from_the_aisp_bankroll(client):
    r = client.post("/api/v1/mlb/parlays/tickets", json={**TICKET, "funded": True}, headers=_member(client))
    assert r.status_code == 403


# ---- launch hardening (10-28) ----------------------------------------------------------------------------------
def _settings(settings, **kw):
    return settings.model_copy(update=kw)


def test_invite_only_registration_admits_listed_emails_once(client, settings):
    from app.core.config import get_settings
    from app.main import app

    s = _settings(settings, registration_mode="invite", registration_allowlist="friend@example.com")
    app.dependency_overrides[get_settings] = lambda: s
    body = {"password": "FriendPass123!", "display_name": "Friend"}  # secret-guard: test-fixture
    assert client.post("/api/v1/auth/register", json={**body, "email": "stranger@example.com"}).status_code == 403
    assert client.post("/api/v1/auth/register", json={**body, "email": "Friend@Example.com"}).status_code == 201
    assert client.post("/api/v1/auth/register", json={**body, "email": "friend@example.com"}).status_code == 409


def test_closed_registration_admits_nobody(client, settings):
    from app.core.config import get_settings
    from app.main import app

    s = _settings(settings, registration_mode="closed", registration_allowlist="friend@example.com")
    app.dependency_overrides[get_settings] = lambda: s
    r = client.post("/api/v1/auth/register", json={"email": "friend@example.com", "password": "FriendPass123!", "display_name": "Friend"})  # secret-guard: test-fixture
    assert r.status_code == 403


def test_an_account_locks_after_repeated_wrong_passwords(client, settings):
    from app.core.config import get_settings
    from app.main import app

    s = _settings(settings, login_failure_limit=3, auth_rate_limit_per_minute=1000)
    app.dependency_overrides[get_settings] = lambda: s
    _member(client)
    bad = {"email": "member@example.com", "password": "WrongPass123!"}  # secret-guard: test-fixture
    assert [client.post("/api/v1/auth/login", json=bad).status_code for _ in range(3)] == [401, 401, 401]
    assert client.post("/api/v1/auth/login", json={**bad, "password": "MemberPass123!"}).status_code == 429  # secret-guard: test-fixture


def test_the_booth_requires_sign_in_and_enforces_the_daily_cap(client, settings, monkeypatch):
    from app.api import preview_feeds_routes as routes
    from app.core.config import get_settings
    from app.main import app
    from app.preview_feeds import feeds

    assert client.post("/preview-api/booth", json={"question": "why?"}).status_code == 401
    monkeypatch.setattr(feeds, "booth", lambda payload: {"answer": "because"})
    routes._BOOTH_DAY.update(day=None, site=0, users={})
    s = _settings(settings, booth_per_user_per_day=2, booth_site_per_day=100)
    app.dependency_overrides[get_settings] = lambda: s
    h = _member(client)
    codes = [client.post("/preview-api/booth", json={"question": "why?"}, headers=h).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_preview_feeds_reject_malformed_dates_and_hide_booth_config(client):
    assert client.get("/preview-api/scores?league=nba&date=x;y").status_code == 400
    assert client.get("/preview-api/mlb/live?date=20261027&home=a/b&away=NYY").status_code == 400
    assert client.get("/preview-api/health").json() == {"ok": True}


def test_html_pages_carry_a_content_security_policy():
    from app.core.middleware import APP_PAGE_CSP

    assert "script-src 'self'" in APP_PAGE_CSP and "frame-ancestors 'none'" in APP_PAGE_CSP and "object-src 'none'" in APP_PAGE_CSP
