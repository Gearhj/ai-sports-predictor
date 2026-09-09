"""Verify/import only current NFL Labs supplements; no forecast/model/job execution.

Run from server: python -m scripts.import_nfl_labs_supplements --root <exchange>
The default is read-only. --apply performs the explicitly requested append-only
import and refuses to commit if any preserved table changes in its transaction.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from sqlalchemy import text

from app.db.session import SessionLocal
from app.forecast_exchange.nfl_labs_v1 import ROOT_NAME, register_labs_current, verify_labs_current

PRESERVED_TABLES = (
    "model_versions", "forecasts", "forecast_components", "official_asp_positions",
    "position_results", "user_virtual_bankrolls", "shadow_experiments",
    "shadow_experiment_decisions", "shadow_forecasts", "shadow_forecast_bundles",
    "shadow_decision_settlements", "provider_request_ledger",
)


def preservation_snapshot(session):
    result = {}
    for table in PRESERVED_TABLES:
        # Names are a code-owned allowlist. Hash full persisted rows; no private
        # values leave the database. Repeatable read excludes concurrent jobs.
        count, digest = session.execute(text(
            f"SELECT count(*), md5(coalesce(string_agg(md5(to_jsonb(t)::text), '' ORDER BY id), '')) FROM {table} t"
        )).one()
        result[table] = {"rows": count, "full_row_md5": digest}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    verified = verify_labs_current(args.root)
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" + ("" if args.apply else ", READ ONLY")))
        database, port = session.execute(text("SELECT current_database(), inet_server_port()")).one()
        before = preservation_snapshot(session)
        records = register_labs_current(session, verified, dry_run=not args.apply)
        after = preservation_snapshot(session)
        if before != after:
            session.rollback()
            raise RuntimeError("A protected table changed; supplement import rolled back")
        if args.apply:
            session.commit()
        else:
            session.rollback()
    receipt = {
        "schema_version": "AISP_NFL_LABS_V1_IMPORT_RECEIPT", "created_at": datetime.now(UTC).isoformat(),
        "applied": args.apply, "database": database, "database_port": port,
        "pointer_sha256": hashlib.sha256((args.root / ROOT_NAME / "current.json").read_bytes()).hexdigest(),
        "verified_artifacts": len(verified),
        "by_engine": dict(Counter(r["engine_id"] for r in records)),
        "imported": sum(not r["idempotent_reuse"] for r in records) if args.apply else 0,
        "reused": sum(r["idempotent_reuse"] for r in records),
        "preservation_scope": "full rows within import transaction; concurrent external jobs excluded",
        "preserved_before": before, "preserved_after": after, "protected_tables_unchanged": before == after,
        "model_executions": 0, "provider_calls": 0, "official_writes": 0, "records": records,
    }
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in receipt.items() if k not in {"records", "preserved_after"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
