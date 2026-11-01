from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
from pwdlib import PasswordHash


ALGORITHM = "HS256"
password_hasher = PasswordHash.recommended()


class InvalidTokenError(ValueError):
    pass


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(password, encoded)
    except Exception:
        return False


def create_access_token(
    subject: str,
    secret_key: str,
    expires_minutes: int,
    claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "type": "access",
        # Unpredictable per-token identifier: unique for logging/audit and a
        # future per-token denylist without reusing the guessable subject/iat.
        "jti": uuid4().hex,
    }
    payload.update(claims or {})
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


#: Fallback lifetime cap when no settings are reachable (security review 11-01).
DEFAULT_MAX_TOKEN_LIFETIME_MINUTES = 720


def _configured_max_lifetime_minutes() -> int:
    try:
        from app.core.config import get_settings

        settings = get_settings()
        return max(int(settings.access_token_max_minutes), int(settings.access_token_minutes))
    except Exception:  # noqa: BLE001 - fall back to the fixed cap, never to "unbounded"
        return DEFAULT_MAX_TOKEN_LIFETIME_MINUTES


def decode_access_token(
    token: str,
    secret_key: str,
    *,
    max_lifetime_minutes: int | None = None,
) -> dict[str, Any]:
    """Verify an access token and its claims.

    Security review 11-01: ``exp``, ``iat`` and ``sub`` are required, and a token whose lifetime
    (``exp - iat``) exceeds the configured cap (``access_token_max_minutes``, never below
    ``access_token_minutes``) is refused even when its signature is valid.
    """

    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("Invalid or expired access token") from exc
    if payload.get("type") != "access" or not payload.get("sub"):
        raise InvalidTokenError("Invalid access token claims")
    cap = max_lifetime_minutes if max_lifetime_minutes is not None else _configured_max_lifetime_minutes()
    try:
        lifetime_seconds = float(payload["exp"]) - float(payload["iat"])
    except (TypeError, ValueError, KeyError) as exc:
        raise InvalidTokenError("Invalid access token claims") from exc
    if lifetime_seconds <= 0 or lifetime_seconds > cap * 60:
        raise InvalidTokenError("Access token lifetime exceeds the allowed maximum")
    return payload
