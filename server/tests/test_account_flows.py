"""Self-serve accounts (scale plan Phase 1, item 2): verify, forgot/reset, change password, delete."""

import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import User, UserToken
from app.services import account_service


@pytest.fixture
def outbox(settings, tmp_path):
    settings.email_outbox_dir = str(tmp_path / "outbox")
    return tmp_path / "outbox"


def _messages(outbox):
    path = outbox / "outbox.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _link(message, param):
    m = re.search(rf"[?&]{param}=([A-Za-z0-9_\-]+)", message["text"])
    assert m, message["text"]
    return m.group(1)


def _register(client, email, password="MemberPass123!", name="Tester"):  # secret-guard: test-fixture
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password, "display_name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_registration_sends_a_verification_link_that_works_once(client, outbox):
    reg = _register(client, "new@example.com")
    assert reg["user"]["email_verified"] is False
    msgs = _messages(outbox)
    assert [m["kind"] for m in msgs] == ["verify"] and msgs[0]["to"] == "new@example.com"
    assert (outbox / "outbox.jsonl").exists() and len(list(outbox.glob("*-verify-*.txt"))) == 1
    token = _link(msgs[0], "verify")

    ok = client.post("/api/v1/auth/verify/confirm", json={"token": token})
    assert ok.status_code == 200 and ok.json()["status"] == "verified"
    assert client.get("/api/v1/auth/me", headers=_h(reg["access_token"])).json()["email_verified"] is True
    # single use
    assert client.post("/api/v1/auth/verify/confirm", json={"token": token}).status_code == 400
    assert client.post("/api/v1/auth/verify/confirm", json={"token": "x" * 40}).status_code == 400


def test_asking_again_retires_the_earlier_link(client, outbox, session):
    reg = _register(client, "again@example.com")
    first = _link(_messages(outbox)[0], "verify")
    r = client.post("/api/v1/auth/verify/request", headers=_h(reg["access_token"]))
    assert r.status_code == 202 and r.json()["status"] == "sent"
    second = _link(_messages(outbox)[1], "verify")
    assert client.post("/api/v1/auth/verify/confirm", json={"token": first}).status_code == 400
    assert client.post("/api/v1/auth/verify/confirm", json={"token": second}).status_code == 200
    assert client.post("/api/v1/auth/verify/request", headers=_h(reg["access_token"])).json()["status"] == "already_verified"
    assert session.scalar(select(User).where(User.email == "again@example.com")).email_verified_at is not None


def test_forgot_password_never_says_whether_an_account_exists(client, outbox):
    _register(client, "owner@example.com")
    a = client.post("/api/v1/auth/password/forgot", json={"email": "owner@example.com"})
    b = client.post("/api/v1/auth/password/forgot", json={"email": "nobody@example.com"})
    assert a.status_code == b.status_code == 202 and a.json() == b.json()
    kinds = [m["kind"] for m in _messages(outbox)]
    assert kinds == ["verify", "reset"]  # one reset message, for the real account only


def test_reset_link_sets_a_new_password_and_signs_out_everywhere(client, outbox):
    reg = _register(client, "reset@example.com")
    old_token = reg["access_token"]
    client.post("/api/v1/auth/password/forgot", json={"email": "reset@example.com"})
    token = _link(_messages(outbox)[-1], "reset")

    short = client.post("/api/v1/auth/password/reset", json={"token": token, "password": "short"})
    assert short.status_code == 422
    done = client.post("/api/v1/auth/password/reset", json={"token": token, "password": "BrandNewPass456!"})  # secret-guard: test-fixture
    assert done.status_code == 200, done.text
    assert done.json()["user"]["email_verified"] is True  # reading the inbox proved the email
    # the old session is gone, the old password no longer works, the new one does
    assert client.get("/api/v1/auth/me", headers=_h(old_token)).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "reset@example.com", "password": "MemberPass123!"}).status_code == 401  # secret-guard: test-fixture
    assert client.post("/api/v1/auth/login", json={"email": "reset@example.com", "password": "BrandNewPass456!"}).status_code == 200  # secret-guard: test-fixture
    # and the link is spent
    assert client.post("/api/v1/auth/password/reset", json={"token": token, "password": "AnotherPass789!"}).status_code == 400  # secret-guard: test-fixture


def test_an_expired_token_is_refused(client, outbox, session):
    reg = _register(client, "late@example.com")
    client.post("/api/v1/auth/password/forgot", json={"email": "late@example.com"})
    token = _link(_messages(outbox)[-1], "reset")
    row = session.scalar(select(UserToken).where(UserToken.kind == "reset"))
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    assert client.post("/api/v1/auth/password/reset", json={"token": token, "password": "BrandNewPass456!"}).status_code == 400  # secret-guard: test-fixture
    assert client.get("/api/v1/auth/me", headers=_h(reg["access_token"])).status_code == 200  # nothing happened to the account


def test_change_password_needs_the_current_one(client, outbox):
    reg = _register(client, "change@example.com")
    h = _h(reg["access_token"])
    wrong = client.post("/api/v1/auth/password/change", json={"current_password": "NotThePassword1!", "new_password": "BrandNewPass456!"}, headers=h)  # secret-guard: test-fixture
    assert wrong.status_code == 400
    right = client.post("/api/v1/auth/password/change", json={"current_password": "MemberPass123!", "new_password": "BrandNewPass456!"}, headers=h)  # secret-guard: test-fixture
    assert right.status_code == 200
    fresh = right.json()["access_token"]
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401  # the old session is revoked
    assert client.get("/api/v1/auth/me", headers=_h(fresh)).status_code == 200


def test_delete_account_forgets_everything(client, outbox, session):
    reg = _register(client, "gone@example.com", name="Going Away")
    h = _h(reg["access_token"])
    client.post("/api/v1/me/state/picks", json={"payload": [{"id": "g1:HOME"}]}, headers=h)
    assert client.post("/api/v1/auth/account/delete", json={"password": "wrong-password-1"}, headers=h).status_code == 400  # secret-guard: test-fixture
    assert client.post("/api/v1/auth/account/delete", json={"password": "MemberPass123!"}, headers=h).json() == {"status": "deleted"}  # secret-guard: test-fixture
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "gone@example.com", "password": "MemberPass123!"}).status_code == 401  # secret-guard: test-fixture
    user = session.scalar(select(User).where(User.id == __import__("uuid").UUID(reg["user"]["id"])))
    assert user.email.endswith("@deleted.invalid") and user.display_name == "Deleted account"
    assert session.scalars(select(UserToken).where(UserToken.user_id == user.id)).first() is None
    from app.services import user_state
    assert user_state.list_state(session, user.id) == []
    # the email is free again
    assert client.post("/api/v1/auth/register", json={"email": "gone@example.com", "password": "MemberPass123!", "display_name": "Back"}).status_code == 201  # secret-guard: test-fixture


def test_tokens_are_stored_hashed_only(session, settings):
    user = User(email="hash@example.com", password_hash="x", display_name="Hash", )
    session.add(user); session.commit()
    raw = account_service.issue_token(session, user, "verify", timedelta(hours=1))
    row = session.scalar(select(UserToken))
    assert raw not in row.token_hash and len(row.token_hash) == 64
