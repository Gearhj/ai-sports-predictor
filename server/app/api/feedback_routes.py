"""Beta feedback (launch plan 10-28): a signed-in reader sends a note about a page; the owner reads them.

Notes are appended to <repo>/.runtime/feedback/feedback.jsonl with the account's id and email, the page, a 1-5
rating and the text (capped at 4,000 characters). Nothing is sent anywhere else. Rate limited per account.
"""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies import AdminUser, CurrentUser
from app.core.config import ASP_ROOT
from app.core.rate_limit import rate_limit

router = APIRouter(tags=["feedback"])
STORE = ASP_ROOT / ".runtime" / "feedback" / "feedback.jsonl"
_LOCK = threading.Lock()


class FeedbackRequest(BaseModel):
    page: str = Field("", max_length=200)
    rating: int | None = Field(None, ge=1, le=5)
    kind: str = Field("idea", pattern="^(bug|idea|confusing|love)$")
    text: str = Field(..., min_length=3, max_length=4000)


def _store() -> Path:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    return STORE


@router.post("/feedback", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit("feedback", category="auth"))])
def send_feedback(body: FeedbackRequest, user: CurrentUser) -> dict:
    rec = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "user_id": str(user.id), "email": user.email,
           "page": body.page.strip(), "rating": body.rating, "kind": body.kind, "text": body.text.strip()}
    with _LOCK:
        with _store().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "message": "Thanks. Your note went straight to the owner."}


@router.get("/admin/feedback")
def read_feedback(_: AdminUser, limit: int = 200) -> dict:
    path = _store()
    if not path.exists():
        return {"items": []}
    lines = path.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 2000)):]
    try:
        return {"items": [json.loads(l) for l in reversed(lines) if l.strip()]}
    except ValueError as exc:  # pragma: no cover - a hand-edited file
        raise HTTPException(status_code=500, detail="The feedback file could not be read.") from exc
