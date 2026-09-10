"""Verify/import only current NFL play-level supplements; no model or job execution.

Run from server: python -m scripts.import_nfl_play_level_supplements --root <exchange>
The default is read-only. --apply performs the explicitly requested append-only
import and refuses to commit if any preserved table changes in its transaction.

Modelled on `import_nfl_labs_supplements` and deliberately identical in its
preservation guarantee: a supplement import may add research evidence and must
never touch a forecast, a position, a settlement or a provider ledger row.
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
from app.forecast_exchange.nfl_play_level_v1 import (
    ARTIFACT, CONTRACT, ROOT_NAME, register_play_level_current, verify_play_level_current,
)
from scripts.import_nfl_labs_supplements import preservation_snapshot


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)

    verified = verify_play_level_current(args.root)
    versions = Counter(item.artifact.get("schemaVersion") for item in verified)
    with_spatial = sum(1 for item in verified if item.artifact.get("populationSpatial") is not None)
    spatial_plays = sum(item.artifact["populationSpatial"]["playCount"]
                        for item in verified if item.artifact.get("populationSpatial"))

    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                             + ("" if args.apply else ", READ ONLY")))
        database, port = session.execute(text("SELECT current_database(), inet_server_port()")).one()
        before = preservation_snapshot(session)
        records = register_play_level_current(session, verified, dry_run=not args.apply)
        after = preservation_snapshot(session)
        if before != after:
            session.rollback()
            raise RuntimeError("A protected table changed; supplement import rolled back")
        if args.apply:
            session.commit()
        else:
            session.rollback()

    receipt = {
        "schema_version": "AISP_NFL_PLAY_LEVEL_IMPORT_RECEIPT_V2",
        "created_at": datetime.now(UTC).isoformat(),
        "applied": args.apply, "database": database, "database_port": port,
        "contract": CONTRACT, "newest_artifact_schema": ARTIFACT,
        "pointer_sha256": hashlib.sha256((args.root / ROOT_NAME / "current.json").read_bytes()).hexdigest(),
        "verified_artifacts": len(verified),
        # Both versions are expected. An event past kickoff cannot be
        # regenerated, so its V1 artifact is carried forward unchanged.
        "artifact_schema_versions": dict(versions),
        "artifacts_with_population_spatial": with_spatial,
        "artifacts_without_population_spatial": len(verified) - with_spatial,
        "plays_summarised_by_population_spatial": spatial_plays,
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
