from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User, UserRole, UserStatus
from app.schemas.api import AuthResponse, AuthUser


class AuthenticationError(ValueError):
    pass


class DuplicateEmailError(ValueError):
    pass


class BootstrapConflictError(RuntimeError):
    pass


_DUMMY_PASSWORD_HASH = hash_password("fixed-dummy-password-never-used")


def public_user(user: User) -> AuthUser:
    return AuthUser(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role.value.upper(),
    )


class RegistrationClosedError(Exception):
    pass


def register_user(session: Session, settings: Settings, email: str, password: str, display_name: str) -> AuthResponse:
    normalized_email = email.strip().lower()
    mode = getattr(settings, "registration_mode", "invite")
    # Security review 10-27: a shared deployment never lets strangers in. "invite" admits only an email the owner
    # listed; one account per email makes each invitation single-use.
    if mode == "closed" or (mode == "invite" and normalized_email not in settings.registration_allowlist_set):
        raise RegistrationClosedError("Registration is by invitation. Ask the owner to add your email.")
    existing = session.scalar(select(User).where(func.lower(User.email) == normalized_email))
    if existing:
        raise DuplicateEmailError("This invitation has already been used. Sign in instead.")
    user = User(
        email=normalized_email,
        password_hash=hash_password(password),
        display_name=display_name,
        role=UserRole.MEMBER,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return _auth_response(user, settings)


def authenticate_user(session: Session, settings: Settings, email: str, password: str) -> AuthResponse:
    normalized_email = email.strip().lower()
    user = session.scalar(select(User).where(func.lower(User.email) == normalized_email))
    encoded = user.password_hash if user else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(password, encoded)
    if not user or not password_valid:
        raise AuthenticationError("Invalid email or password")
    if user.status != UserStatus.ACTIVE:
        raise AuthenticationError("Account is not active")
    user.last_login_at = datetime.now(UTC)
    session.commit()
    return _auth_response(user, settings)


def ensure_admin(session: Session, settings: Settings) -> User:
    email = settings.admin_email.strip().lower()
    user = session.scalar(select(User).where(func.lower(User.email) == email))
    if user:
        if user.role != UserRole.ADMIN:
            raise BootstrapConflictError(
                "ADMIN_EMAIL already belongs to a non-admin account; refusing automatic privilege escalation"
            )
        return user
    if len(settings.admin_password or "") < 12:
        raise BootstrapConflictError(
            "ADMIN_PASSWORD must be set to a unique value of at least 12 characters before an "
            "administrator can be created; no default password is shipped"
        )
    user = User(
        email=email,
        password_hash=hash_password(settings.admin_password),
        display_name="ASP Administrator",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def revoke_user_tokens(session: Session, user: User) -> None:
    """Invalidate every outstanding access token for ``user``.

    Increments the session-generation counter so all previously issued tokens
    (whose ``ver`` claim binds the prior value) are rejected before ``exp``.
    Call after logout and any security-sensitive change (password reset, role
    change, account disable) once those flows exist.
    """

    user.token_version = int(user.token_version) + 1
    session.add(user)
    session.commit()


def _auth_response(user: User, settings: Settings) -> AuthResponse:
    token = create_access_token(
        str(user.id),
        settings.secret_key,
        settings.access_token_minutes,
        claims={"role": user.role.value, "ver": int(user.token_version)},
    )
    return AuthResponse(access_token=token, user=public_user(user))
