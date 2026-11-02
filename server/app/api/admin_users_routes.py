"""Accounts for the owner (scale plan Phase 1, item 3): list members, grant or revoke an entitlement by hand.

GET  /admin/users?q=            up to 50 accounts matching an email or name fragment (newest first)
POST /admin/users/{id}/entitlements   {"kind": "premium", "active": true, "expires_at": null}
GET  /me/premium                the gated probe: 200 for a premium member (or anyone while gating is off)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.dependencies import AdminUser, DatabaseSession, PremiumUser
from app.core.rate_limit import rate_limit
from app.models import Entitlement, EntitlementKind, User
from app.services import entitlements

router = APIRouter(tags=["administration"])


class EntitlementBody(BaseModel):
    kind: str = Field(default=entitlements.PREMIUM, max_length=16)
    active: bool = True
    expires_at: datetime | None = None
    source: str = Field(default="owner", max_length=80)


def _account(session, user: User) -> dict[str, Any]:
    rows = list(session.scalars(select(Entitlement).where(Entitlement.user_id == user.id)))
    return {
        "id": str(user.id), "email": user.email, "display_name": user.display_name,
        "role": user.role.value.upper(), "status": user.status.value.upper(),
        "email_verified": user.email_verified_at is not None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "entitlements": [entitlements.as_dict(r) for r in rows],
        "active": entitlements.active_kinds(session, user.id),
    }


@router.get("/admin/users", dependencies=[Depends(rate_limit("admin:users", category="admin"))])
def list_users(session: DatabaseSession, user: AdminUser, q: str = Query("", max_length=120)) -> dict[str, Any]:
    stmt = select(User).order_by(User.created_at.desc()).limit(50)
    needle = q.strip().lower()
    if needle:
        stmt = stmt.where(or_(func.lower(User.email).contains(needle), func.lower(User.display_name).contains(needle)))
    rows = list(session.scalars(stmt))
    return {"users": [_account(session, u) for u in rows], "count": len(rows)}


@router.post("/admin/users/{user_id}/entitlements", dependencies=[Depends(rate_limit("admin:users", category="admin"))])
def set_user_entitlement(user_id: uuid.UUID, body: EntitlementBody, session: DatabaseSession, admin: AdminUser) -> dict[str, Any]:
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such account.")
    try:
        EntitlementKind(body.kind.lower())
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown entitlement kind '{body.kind[:16]}'.") from None
    if body.kind.lower() == EntitlementKind.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator rights are not an entitlement; they are the account's role.")
    entitlements.set_entitlement(session, target, body.kind, active=body.active, expires_at=body.expires_at, source=body.source)
    session.commit()
    return _account(session, target)


@router.get("/me/premium", tags=["account"])
def premium_probe(user: PremiumUser) -> dict[str, Any]:
    return {"premium": True, "user": str(user.id)}
