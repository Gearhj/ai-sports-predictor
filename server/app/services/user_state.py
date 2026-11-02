"""Per-account copies of the browser-local stores (scale plan 2026-11-01, Phase 1 item 1).

My Picks, the Arena, XP, the model builder and the rest were born as localStorage
blobs (43 client files read them). Rather than one table per store, every store
is one row here: (user_id, store) -> JSON payload + a version counter. The
browser stays the working copy; this is the copy that follows the account
across devices and survives a cleared browser. The client (services/userState.ts)
pulls on sign-in, imports what the browser already holds when the account is
empty, and pushes each store a moment after it changes.

Conflicts: a write may carry the version it was based on; when that is no longer
current the write is refused with the current row, and the browser takes the
server copy. Without a base version the write simply wins.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import UserState

#: The stores the client mirrors. Anything else is refused: the table is not a free-form key-value bag.
STORES: frozenset[str] = frozenset({
    "picks",            # services/previewPicks.ts        aisp-preview-picks-v1 (My Picks slip)
    "play-bankroll",    # services/previewPicks.ts        aisp-play-bankroll-v1
    "my-card",          # services/myCard.ts              aisp.my-card.v1 (frozen-quote virtual picks)
    "my-card-replay",   # services/myCard.ts              aisp.my-card.replay.v1
    "arena",            # features/arena/arenaStore.ts    aisp-arena-v1 (calls, revealed, level)
    "progress",         # features/progress/progress.ts   aisp-progress-v1 (visits, days, seen badges)
    "youcall",          # livefield/football/youCallStore aisp-youcall-v1 (Live View snap calls)
    "my-model",         # features/lab/myModel.ts         aisp-my-model-v1 (the model builder)
    "my-reads",         # features/lab/myModel.ts         aisp-my-reads-v1
    "my-model-log",     # features/lab/modelLog.ts        aisp-my-model-log-v1 (its track record)
    "favorites",        # livefield/football/LiveShell    aisp.live.favorites (starred teams)
    "alerts-settings",  # features/flow/Alerts.tsx        aisp.alerts.settings.v1
})
#: One store's serialized payload (the two 2000-entry logs run to ~600 KB).
MAX_STORE_BYTES = 1_000_000
#: Everything one account holds, so a runaway client cannot fill the disk.
MAX_USER_BYTES = 8_000_000


class UnknownStore(ValueError):
    pass


class StoreTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class StoreConflict(Exception):
    """The write was based on a version that is no longer current; ``current`` is the row to take instead."""

    current: UserState


def payload_bytes(payload: Any) -> int:
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def list_state(session: Session, user_id: uuid.UUID) -> list[UserState]:
    return list(session.scalars(select(UserState).where(UserState.user_id == user_id).order_by(UserState.store)))


def get_state(session: Session, user_id: uuid.UUID, store: str) -> UserState | None:
    return session.scalar(select(UserState).where(UserState.user_id == user_id, UserState.store == store))


def put_state(session: Session, user_id: uuid.UUID, store: str, payload: Any, base_version: int | None = None) -> UserState:
    if store not in STORES:
        raise UnknownStore(store)
    size = payload_bytes(payload)
    if size > MAX_STORE_BYTES:
        raise StoreTooLarge(f"{store}: {size} bytes")
    row = get_state(session, user_id, store)
    if base_version is not None and row is not None and row.version != base_version:
        raise StoreConflict(row)
    others = sum(payload_bytes(r.payload) for r in list_state(session, user_id) if r.store != store)
    if others + size > MAX_USER_BYTES:
        raise StoreTooLarge(f"account total would be {others + size} bytes")
    now = datetime.now(UTC)
    if row is None:
        row = UserState(user_id=user_id, store=store, payload=payload, version=1, updated_at=now)
        session.add(row)
    else:
        row.payload = payload
        row.version += 1
        row.updated_at = now
    session.flush()
    return row


def delete_state(session: Session, user_id: uuid.UUID, store: str | None = None) -> int:
    rows = list_state(session, user_id) if store is None else [r for r in [get_state(session, user_id, store)] if r]
    for r in rows:
        session.delete(r)
    session.flush()
    return len(rows)


def as_dict(row: UserState) -> dict[str, Any]:
    return {"store": row.store, "payload": row.payload, "version": row.version, "updated_at": row.updated_at.isoformat()}


__all__ = [
    "MAX_STORE_BYTES", "MAX_USER_BYTES", "STORES", "StoreConflict", "StoreTooLarge", "UnknownStore",
    "as_dict", "delete_state", "get_state", "list_state", "payload_bytes", "put_state",
]
