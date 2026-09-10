"""Verify/import the 60 repaired NFL historical-prior research forecasts.

Run from server:
    python -m scripts.import_nfl_current_state_repaired --handoff <handoff.json>
    python -m scripts.import_nfl_current_state_repaired --handoff <handoff.json> --apply \
        --receipt outputs/nfl-current-state-repaired/import-receipt.json

The default is read-only. ``--apply`` performs the explicitly requested
append-only import and refuses to commit if any preserved table changes inside
its transaction.

Only the forecasts are imported. The publication's model joblib, feature
states and observed contexts are hashed by the handoff and left where they
are; nothing here loads or executes a model.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import json
from pathlib import Path

from sqlalchemy import text

from app.db.session import SessionLocal
from app.forecast_exchange.nfl_current_state_v2 import (
    CONTRACT, FORECAST_TYPE, KIND_HISTORICAL_PRIOR, PRIMARY_ENGINE,
    register_current_state_forecasts, verify_current_state_handoff,
)
from scripts.import_nfl_labs_supplements import preservation_snapshot

DEFAULT_HANDOFF = Path(
    r"C:\Users\james\asp-model-studio\outputs\nfl-current-state-cold-start-v2"
    r"\uncertainty-repair-v1\current-publication\handoffs\sha256"
    r"\9c2e10c70fdb0696a4304830cc0cd38a611d946c3ad814a2017986b66923144c\handoff.json"
)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--expect-sha256")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)

    handoff_sha256 = args.expect_sha256 or args.handoff.parent.name
    handoff, verified = verify_current_state_handoff(args.handoff, expected_sha256=handoff_sha256)
    engines = Counter(item.engine_id for item in verified)
    events = sorted({item.forecast["eventId"] for item in verified})

    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                             + ("" if args.apply else ", READ ONLY")))
        database, port = session.execute(text("SELECT current_database(), inet_server_port()")).one()
        before = preservation_snapshot(session)
        records = register_current_state_forecasts(
            session, handoff, verified, handoff_sha256=handoff_sha256, dry_run=not args.apply)
        after = preservation_snapshot(session)
        if before != after:
            session.rollback()
            raise RuntimeError("A protected table changed; forecast import rolled back")
        if args.apply:
            session.commit()
        else:
            session.rollback()

    receipt = {
        "schema_version": "AISP_NFL_CURRENT_STATE_REPAIR_IMPORT_RECEIPT_V1",
        "created_at": datetime.now(UTC).isoformat(),
        "applied": args.apply, "database": database, "database_port": port,
        "contract": CONTRACT, "supplement_kind": KIND_HISTORICAL_PRIOR,
        "forecast_type": FORECAST_TYPE,
        "handoff_path": str(args.handoff), "handoff_sha256": handoff_sha256,
        "model_version": handoff["modelVersion"],
        "repair_authority": handoff["repairAuthority"],
        "publication_disposition": handoff["publicationDisposition"],
        "verified_forecasts": len(verified),
        "events": len(events), "event_ids": events,
        "engines": dict(sorted(engines.items())),
        "primary_engine": PRIMARY_ENGINE,
        "draws_per_forecast": handoff["drawsPerForecast"],
        "skips": handoff.get("skips") or [],
        "sealed_2025_read": handoff["sealed2025Read"],
        "imported": sum(not r["idempotent_reuse"] for r in records) if args.apply else 0,
        "reused": sum(r["idempotent_reuse"] for r in records),
        "preservation_scope": "full rows within import transaction; concurrent external jobs excluded",
        "preserved_before": before, "preserved_after": after,
        "protected_tables_unchanged": before == after,
        "model_executions": 0, "provider_calls": 0, "official_writes": 0, "records": records,
    }
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in receipt.items()
                      if k not in {"records", "preserved_after", "preserved_before"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
