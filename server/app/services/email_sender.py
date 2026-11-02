"""Outgoing mail with a pluggable sender (scale plan 2026-11-01, Phase 1 item 2).

Until a provider is chosen (Phase 2, James's yes), every message goes to a local
OUTBOX: one text file per message plus an append-only ``outbox.jsonl`` under
``EMAIL_OUTBOX_DIR`` (default ``<runtime>/outbox``). The owner reads the link
there and the whole verify/reset flow can be exercised end to end with no
network. A provider sender implements the same ``send`` and is picked by
``EMAIL_PROVIDER``.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

_lock = threading.Lock()


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    kind: str = "transactional"


class EmailSender(Protocol):
    name: str

    def send(self, message: EmailMessage) -> dict[str, Any]: ...


def outbox_dir(settings: Any) -> Path:
    configured = str(getattr(settings, "email_outbox_dir", "") or "").strip()
    if configured:
        return Path(configured)
    from app.nba.availability import root

    return root(settings).parent / "outbox"


class OutboxSender:
    """Writes the message to disk instead of sending it. Never raises on a full or missing disk path: a sign-up
    must not fail because the owner's inbox folder could not be written, so the failure is returned, not raised."""

    name = "outbox"

    def __init__(self, directory: Path, sender: str) -> None:
        self.directory = directory
        self.sender = sender

    def send(self, message: EmailMessage) -> dict[str, Any]:
        now = datetime.now(UTC)
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        safe_to = re.sub(r"[^a-z0-9@._-]", "_", message.to.lower())[:80]
        record = {"at": now.isoformat(), "from": self.sender, "to": message.to, "subject": message.subject,
                  "kind": message.kind, "text": message.text}
        try:
            with _lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self.directory / f"{stamp}-{message.kind}-{safe_to}.txt"
                path.write_text(
                    f"From: {self.sender}\nTo: {message.to}\nSubject: {message.subject}\nDate: {record['at']}\n\n{message.text}\n",
                    encoding="utf-8",
                )
                with (self.directory / "outbox.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            return {"sent": True, "provider": self.name, "path": str(path)}
        except OSError as exc:
            return {"sent": False, "provider": self.name, "error": str(exc)[:200]}


def get_email_sender(settings: Any) -> EmailSender:
    provider = str(getattr(settings, "email_provider", "outbox") or "outbox")
    if provider == "outbox":
        return OutboxSender(outbox_dir(settings), str(getattr(settings, "email_from", "") or "no-reply@localhost"))
    raise ValueError(f"Unknown EMAIL_PROVIDER {provider!r}")


__all__ = ["EmailMessage", "EmailSender", "OutboxSender", "get_email_sender", "outbox_dir"]
