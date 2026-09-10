"""Verify/import the completed MLB regime and hybrid-ensemble study.

Run from server:
    python -m scripts.import_mlb_regime_study --root <publication-dir>
    python -m scripts.import_mlb_regime_study --root <publication-dir> --apply \
        --receipt outputs/mlb-regime-hybrid/import-receipt.json

The default is read-only. ``--apply`` performs the explicitly requested
append-only import and refuses to commit if any preserved table changes inside
its transaction.

Modelled on ``import_nfl_labs_supplements`` and deliberately identical in its
preservation guarantee. This one imports a *study*: it registers no forecast,
executes no model, and the joblib artifacts the publication ships are hashed
and never loaded.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

from sqlalchemy import text

from app.db.session import SessionLocal
from app.forecast_exchange.mlb_regime_study_v1 import (
    CONTRACT, MANIFEST, register_regime_study, resolve_manifest, verify_regime_study,
)
from scripts.import_nfl_labs_supplements import preservation_snapshot

DEFAULT_ROOT = Path(r"C:\Users\james\asp-model-studio\outputs\mlb-regime-hybrid-ensemble-v1")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="publication directory holding latest.json")
    parser.add_argument("--manifest", type=Path,
                        help="explicit publication-manifest.json (overrides the pointer)")
    parser.add_argument("--expect-sha256", help="refuse a manifest that does not hash to this")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)

    manifest_path = args.manifest or resolve_manifest(args.root)
    verified = verify_regime_study(manifest_path, expected_sha256=args.expect_sha256)
    summaries = verified.summaries

    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                             + ("" if args.apply else ", READ ONLY")))
        database, port = session.execute(text("SELECT current_database(), inet_server_port()")).one()
        before = preservation_snapshot(session)
        record = register_regime_study(session, verified, dry_run=not args.apply)
        after = preservation_snapshot(session)
        if before != after:
            session.rollback()
            raise RuntimeError("A protected table changed; study import rolled back")
        if args.apply:
            session.commit()
        else:
            session.rollback()

    receipt = {
        "schema_version": "AISP_MLB_REGIME_STUDY_IMPORT_RECEIPT_V1",
        "created_at": datetime.now(UTC).isoformat(),
        "applied": args.apply, "database": database, "database_port": port,
        "contract": CONTRACT, "manifest_schema": MANIFEST,
        "manifest_path": str(manifest_path),
        "manifest_sha256": verified.address,
        "study_id": verified.study_id,
        "result_sha256": verified.files["result"]["sha256"],
        "predecessor_sha256": verified.files["predecessor"]["sha256"],
        "evidence_files_verified": len(verified.files["evidence"]),
        "code_references_verified": len(verified.files["codeReferences"]),
        "bytes_hashed": (
            sum(int(f["byteCount"]) for f in verified.files["evidence"])
            + sum(int(f["byteCount"]) for f in verified.files["codeReferences"])
            + int(verified.files["result"]["byteCount"])
            + int(verified.files["predecessor"]["byteCount"])
        ),
        "authority": summaries["authority"],
        "champion": summaries["champion"]["modelId"],
        "challenger": summaries["challenger"]["modelId"],
        "champion_log_loss": summaries["champion"]["logLoss"],
        "challenger_log_loss": summaries["challenger"]["logLoss"],
        "paired_log_loss_difference": summaries["pairedDifference"]["estimate"]["logLoss"],
        "game_clustered_95_ci": [
            summaries["pairedDifference"]["gameClusterBootstrap"]["intervals"]["logLoss"]["lower"],
            summaries["pairedDifference"]["gameClusterBootstrap"]["intervals"]["logLoss"]["upper"],
        ],
        "ensemble_weights": summaries["ensembleComposition"]["weightsByBranch"],
        "specialist_weight": summaries["ensembleComposition"]["specialistWeight"],
        "imported": 0 if record["idempotent_reuse"] or not args.apply else 1,
        "reused": 1 if record["idempotent_reuse"] else 0,
        "preservation_scope": "full rows within import transaction; concurrent external jobs excluded",
        "preserved_before": before, "preserved_after": after,
        "protected_tables_unchanged": before == after,
        "model_executions": 0, "provider_calls": 0, "official_writes": 0, "record": record,
    }
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in receipt.items()
                      if k not in {"preserved_after", "preserved_before"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
