"""NHL results (and schedule statuses) from the SAME research exchange, for settlement and scoring.

The producer publishes official finals as an ``NHL_GEN1_RESULT_MANIFEST_V1`` (and
schedule snapshots, which carry POSTPONED / CANCELLED) and names them in the
exchange pointer's ``resultManifest`` / ``scheduleManifest``. This module imports
exactly what the pointer names, through the accepted importer
(``gen1_import.import_manifest``), which re-proves each manifest's content
address and every object's bytes. A result is imported once
(``result_sha256``); a correction arrives as a NEW result that supersedes the
earlier one and is never an edit. Nothing here settles anything: that is
``app.nhl.paper.settle``, which reads only official, non-superseded results.

Read-only on the exchange; writes only the caller's session (not committed).
No provider request. Until the producer publishes a result manifest this
reports NO_RESULT_MANIFEST_PUBLISHED and changes nothing.
"""
from __future__ import annotations

import json
import pathlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.nhl import contract as c

KEYS = ("scheduleManifest", "resultManifest")


def consume_results(session: Session, exchange_root: pathlib.Path | None = None, *,
                    now: datetime | None = None) -> dict[str, Any]:
    from app.nhl.forward_delivery import PRODUCTION_INTERFACE
    from app.nhl.gen1_import import ExchangeRefused, ImportSummary, import_manifest

    root = pathlib.Path(exchange_root or PRODUCTION_INTERFACE.exchange_root)
    pointer_path = root / "current.json"
    if not pointer_path.is_file():
        return {"status": "NO_POINTER", "records": 0}
    try:
        pointer = json.loads(pointer_path.read_bytes())
    except ValueError:
        return {"status": "POINTER_UNREADABLE", "records": 0}
    if pointer.get("schema") != c.POINTER_SCHEMA:
        return {"status": "POINTER_SCHEMA_UNKNOWN", "records": 0}
    refs = {k: pointer.get(k) for k in KEYS if pointer.get(k)}
    if "resultManifest" not in refs and "scheduleManifest" not in refs:
        return {"status": "NO_RESULT_MANIFEST_PUBLISHED", "records": 0,
                "detail": "The exchange pointer names no result or schedule manifest yet."}
    summary = ImportSummary(run_id=f"nhl-results-{uuid.uuid4().hex[:12]}")
    for key in KEYS:                      # schedule first: statuses and games exist before results bind
        if key not in refs:
            continue
        try:
            import_manifest(session, root, refs[key], summary=summary, now=now or datetime.now(UTC))
        except (ExchangeRefused, ValueError) as exc:
            summary.problems.append(f"{key}:{exc}")
    return {"status": "IMPORTED" if not summary.problems else "IMPORTED_WITH_PROBLEMS",
            "records": int(summary.result_counts.get("IMPORTED", 0)),
            "manifests": {k: v.get("sha256") for k, v in refs.items() if isinstance(v, dict)},
            "results": summary.result_counts, "schedule": summary.schedule_counts, "problems": summary.problems}


__all__ = ["consume_results"]
