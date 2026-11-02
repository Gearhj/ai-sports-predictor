"""Entitlements (scale plan 2026-11-01, Phase 1 item 3).

The ``entitlements`` table has existed since the first migration and nothing
read it. Now: ``active_kinds`` is what /auth/me reports and what the
``PremiumUser`` dependency checks; ``set_entitlement`` is how the owner grants
or revokes one by hand from the admin page (testers, a partner's comp accounts)
until a billing provider exists (Phase 2) and does the same through a webhook.

While ``PREMIUM_GATING_ENABLED`` is off (the beta), every page stays open and
the dependency admits everyone; the entitlement is still recorded and shown.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Entitlement, EntitlementKind, User

PREMIUM = EntitlementKind.PREMIUM.value


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)  # SQLite hands naive datetimes back
    return value


def active_kinds(session: Session, user_id: uuid.UUID, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    rows = session.scalars(select(Entitlement).where(Entitlement.user_id == user_id))
    kinds: list[str] = []
    for row in rows:
        expires = _aware(row.expires_at)
        if row.active and row.entitlement_status == "active" and (expires is None or expires > now):
            kinds.append(row.kind.value if hasattr(row.kind, "value") else str(row.kind))
    return sorted(kinds)


def has_premium(session: Session, user_id: uuid.UUID) -> bool:
    return PREMIUM in active_kinds(session, user_id)


def set_entitlement(
    session: Session, user: User, kind: str, *, active: bool, expires_at: datetime | None = None,
    source: str = "owner", provider: str | None = None, external_id: str | None = None,
) -> Entitlement:
    """Grant (active=True) or revoke (active=False) one kind for ``user``; one row per (user, kind)."""
    enum_kind = EntitlementKind(kind.lower())
    row = session.scalar(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.kind == enum_kind))
    now = datetime.now(UTC)
    if row is None:
        row = Entitlement(user_id=user.id, kind=enum_kind, active=active, starts_at=now, source=source)
        session.add(row)
    row.active = active
    row.entitlement_status = "active" if active else "revoked"
    row.expires_at = expires_at
    row.source = source
    if provider is not None:
        row.subscription_provider = provider
    if external_id is not None:
        row.external_membership_id = external_id
    session.flush()
    return row


def as_dict(row: Entitlement) -> dict[str, Any]:
    return {
        "kind": row.kind.value if hasattr(row.kind, "value") else str(row.kind),
        "active": bool(row.active),
        "status": row.entitlement_status,
        "starts_at": row.starts_at.isoformat() if row.starts_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "source": row.source,
        "provider": row.subscription_provider,
    }


__all__ = ["PREMIUM", "active_kinds", "as_dict", "has_premium", "set_entitlement"]
