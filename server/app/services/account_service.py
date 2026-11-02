"""Self-serve account flows (scale plan 2026-11-01, Phase 1 item 2).

Email verification, password reset, password change and account deletion. Every
emailed token is random (32 bytes, URL-safe), stored only as its SHA-256, single
use, short-lived, and issuing a new one retires the previous unused ones of the
same kind. Password changes and resets revoke every outstanding access token
(``revoke_user_tokens``). "Forgot password" never says whether an email has an
account. Deleting an account anonymizes the row (other tables key on users.id)
and removes the account's stores and entitlements.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import hash_password, verify_password
from app.models import Entitlement, User, UserStatus, UserToken
from app.services import user_state
from app.services.auth_service import AuthenticationError, _auth_response, revoke_user_tokens
from app.services.email_sender import EmailMessage, get_email_sender

VERIFY = "verify"
RESET = "reset"


class TokenError(ValueError):
    """Unknown, expired or already used token: one message, so nothing about the token is learned."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _base_url(settings: Settings) -> str:
    return str(getattr(settings, "public_base_url", "") or "https://aisportspredictor.app").rstrip("/")


def issue_token(session: Session, user: User, kind: str, lifetime: timedelta) -> str:
    """Retire this user's unused tokens of ``kind`` and mint a fresh one; returns the RAW token (emailed, never stored)."""
    now = datetime.now(UTC)
    for old in session.scalars(select(UserToken).where(UserToken.user_id == user.id, UserToken.kind == kind, UserToken.used_at.is_(None))):
        old.used_at = now
    raw = secrets.token_urlsafe(32)
    session.add(UserToken(user_id=user.id, kind=kind, token_hash=_hash(raw), expires_at=now + lifetime))
    session.flush()
    return raw


def consume_token(session: Session, raw: str, kind: str) -> User:
    now = datetime.now(UTC)
    row = session.scalar(select(UserToken).where(UserToken.token_hash == _hash(raw or ""), UserToken.kind == kind))
    expires = row.expires_at if row else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)  # SQLite hands naive datetimes back
    if row is None or row.used_at is not None or expires is None or expires < now:
        raise TokenError("That link is not valid any more. Request a new one.")
    user = session.get(User, row.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise TokenError("That link is not valid any more. Request a new one.")
    row.used_at = now
    session.flush()
    return user


def send_verification(session: Session, settings: Settings, user: User) -> dict[str, Any]:
    raw = issue_token(session, user, VERIFY, timedelta(hours=int(getattr(settings, "verify_token_hours", 24))))
    session.commit()
    link = f"{_base_url(settings)}/account?verify={raw}"
    text = (
        f"Hi {user.display_name},\n\n"
        "Confirm this email address for your AI Sports Predictor account by opening the link below. "
        f"It works once and expires in {int(getattr(settings, 'verify_token_hours', 24))} hours.\n\n{link}\n\n"
        "If you did not create an account, ignore this message.\n\n"
        "AI Sports Predictor is an information and entertainment service: no wagers, no prizes.\n"
    )
    return get_email_sender(settings).send(EmailMessage(to=user.email, subject="Confirm your email", text=text, kind=VERIFY))


def confirm_verification(session: Session, raw: str) -> User:
    user = consume_token(session, raw, VERIFY)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
    session.commit()
    return user


def request_password_reset(session: Session, settings: Settings, email: str) -> dict[str, Any]:
    """Always answers the same way; a message is sent only when an active account owns the email."""
    normalized = (email or "").strip().lower()
    user = session.scalar(select(User).where(func.lower(User.email) == normalized))
    if user is None or user.status != UserStatus.ACTIVE:
        return {"sent": False}
    minutes = int(getattr(settings, "reset_token_minutes", 60))
    raw = issue_token(session, user, RESET, timedelta(minutes=minutes))
    session.commit()
    link = f"{_base_url(settings)}/login?reset={raw}"
    text = (
        f"Hi {user.display_name},\n\n"
        "Someone asked to reset the password on your AI Sports Predictor account. Open the link below to choose a new "
        f"one. It works once and expires in {minutes} minutes.\n\n{link}\n\n"
        "If that was not you, ignore this message: your password has not changed.\n"
    )
    return get_email_sender(settings).send(EmailMessage(to=user.email, subject="Reset your password", text=text, kind=RESET))


def reset_password(session: Session, settings: Settings, raw: str, new_password: str):
    user = consume_token(session, raw, RESET)
    user.password_hash = hash_password(new_password)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)  # a reset link read from the inbox proves the email as well
    revoke_user_tokens(session, user)
    return _auth_response(user, settings)


def change_password(session: Session, settings: Settings, user: User, current_password: str, new_password: str):
    if not verify_password(current_password, user.password_hash):
        raise AuthenticationError("The current password is not right.")
    user.password_hash = hash_password(new_password)
    revoke_user_tokens(session, user)
    return _auth_response(user, settings)


def delete_account(session: Session, user: User, password: str) -> None:
    """Anonymize the account and forget what it held. The users row stays (other tables point at it) with no
    personal data: a random email, no name, a dead password, DISABLED, every token revoked."""
    if not verify_password(password, user.password_hash):
        raise AuthenticationError("The password is not right.")
    user_state.delete_state(session, user.id)
    for ent in session.scalars(select(Entitlement).where(Entitlement.user_id == user.id)):
        session.delete(ent)
    for tok in session.scalars(select(UserToken).where(UserToken.user_id == user.id)):
        session.delete(tok)
    user.email = f"deleted-{user.id.hex}@deleted.invalid"
    user.display_name = "Deleted account"
    user.password_hash = hash_password(secrets.token_urlsafe(32))
    user.status = UserStatus.DISABLED
    user.email_verified_at = None
    user.token_version = int(user.token_version) + 1
    session.commit()


__all__ = [
    "RESET", "VERIFY", "TokenError", "change_password", "confirm_verification", "consume_token", "delete_account",
    "issue_token", "request_password_reset", "reset_password", "send_verification",
]
