"""/me/state: the signed-in account's copies of the browser stores (see services/user_state.py).

GET  /me/state            every store the account holds
POST /me/state/{store}    replace one store; ``base_version`` makes the write conditional
POST /me/state/{store}/delete   forget one store (account deletion forgets them all)

POST rather than PUT/DELETE so the dev stack's CORS allow-list (GET/POST) and the
body-size middleware cover it unchanged.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, DatabaseSession
from app.core.rate_limit import rate_limit
from app.services import user_state

router = APIRouter(prefix="/me/state", tags=["account"])


class StateWrite(BaseModel):
    payload: Any
    #: The version this write was based on; when it is no longer current the server answers 409 with its row.
    base_version: int | None = Field(default=None, ge=1)


@router.get("", dependencies=[Depends(rate_limit("me:state:read", category="sync"))])
def read_state(user: CurrentUser, session: DatabaseSession) -> dict[str, Any]:
    rows = user_state.list_state(session, user.id)
    return {
        "stores": {r.store: user_state.as_dict(r) for r in rows},
        "known": sorted(user_state.STORES),
        "as_of": datetime.now(UTC).isoformat(),
    }


@router.post("/{store}", dependencies=[Depends(rate_limit("me:state:write", category="sync"))])
def write_state(store: str, body: StateWrite, user: CurrentUser, session: DatabaseSession) -> dict[str, Any]:
    try:
        row = user_state.put_state(session, user.id, store, body.payload, body.base_version)
    except user_state.UnknownStore:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown store '{store[:64]}'.") from None
    except user_state.StoreTooLarge:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="That store is too large to keep.") from None
    except user_state.StoreConflict as conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "STORE_VERSION_CONFLICT", "message": "A newer copy exists.", "current": user_state.as_dict(conflict.current)},
        ) from None
    session.commit()
    return user_state.as_dict(row)


@router.post("/{store}/delete", dependencies=[Depends(rate_limit("me:state:write", category="sync"))])
def forget_state(store: str, user: CurrentUser, session: DatabaseSession) -> dict[str, Any]:
    if store not in user_state.STORES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown store '{store[:64]}'.")
    removed = user_state.delete_state(session, user.id, store)
    session.commit()
    return {"store": store, "removed": removed}
