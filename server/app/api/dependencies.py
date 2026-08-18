from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import InvalidTokenError, decode_access_token
from app.db.session import get_db
from app.models import User, UserRole, UserStatus


bearer_scheme = HTTPBearer(auto_error=False)
DatabaseSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: DatabaseSession,
    settings: AppSettings,
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise credentials_error
    try:
        payload = decode_access_token(credentials.credentials, settings.secret_key)
        user_id = UUID(payload["sub"])
    except (InvalidTokenError, ValueError, KeyError):
        raise credentials_error
    user = session.get(User, user_id)
    if not user or user.status != UserStatus.ACTIVE:
        raise credentials_error
    # Session-generation binding: a token minted before a logout or other
    # security-sensitive revocation carries a stale ``ver`` and is rejected
    # even though its signature and expiry are still otherwise valid.
    if payload.get("ver") != user.token_version:
        raise credentials_error
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
