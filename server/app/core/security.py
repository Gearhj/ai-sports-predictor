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


def decode_access_token(token: str, secret_key: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("Invalid or expired access token") from exc
    if payload.get("type") != "access" or not payload.get("sub"):
        raise InvalidTokenError("Invalid access token claims")
    return payload
