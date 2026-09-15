"""One bounded import of the 16 NFL Week 2 paper-eligibility supplements.

Admits only the addresses published in the delivery receipt, and only when the
discovery index declares each of them current. Every address goes through the
existing verify_supplement / register_supplement path (via
import_named_supplements). Nothing else in the exchange is imported.

Usage:
    python scripts/import_nfl_week2_eligibility.py <delivery-receipt.json> [--out receipt.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.forecast_exchange.supplements import (  # noqa: E402
    PAPER_ELIGIBILITY_VERSIONS,
    current_supplement_addresses,
    import_named_supplements,
)

GUARDED_TABLES = (
    "research_supplements", "shadow_forecasts", "shadow_experiment_decisions",
    "official_asp_positions", "position_results", "experiment_accounts",
    "experiment_account_fundings", "experiment_account_memberships",
    "odds_snapshots", "provider_request_ledger",
)


def counts(session) -> dict[str, int]:
    return {t: int(session.execute(text(f"select count(*) from {t}")).scalar()) for t in GUARDED_TABLES}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("delivery_receipt", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    receipt = json.loads(args.delivery_receipt.read_text(encoding="utf-8"))
    addresses = [a.lower() for a in receipt["supplementAddresses"]]
    index_path = Path(receipt["consumerDiscoveryIndex"]["path"])
    exchange_root = index_path.parents[1]
    declared = current_supplement_addresses(exchange_root) or set()
    if len(addresses) != 16 or len(set(addresses)) != 16:
        raise SystemExit(f"expected 16 distinct addresses, got {len(set(addresses))}")
    undeclared = sorted(set(addresses) - declared)
    if undeclared:
        raise SystemExit(f"addresses not declared current by the discovery index: {undeclared}")

    session = SessionLocal()
    before = counts(session)
    result = import_named_supplements(session, exchange_root, addresses)
    session.commit()
    after = counts(session)
    second = import_named_supplements(session, exchange_root, addresses)
    session.commit()
    after_second = counts(session)

    rows = session.execute(text("""
        select supplement_sha256, event_id, forecast_id, engine_id, artifact_version, kind, sport
        from research_supplements where supplement_sha256 = any(:a) order by event_id"""),
        {"a": addresses}).mappings().all()
    expected = {p["supplementAddress"]: p for p in json.loads(
        (args.delivery_receipt.parent / "operational-readiness.json").read_text(encoding="utf-8"))["packages"]}
    bindings = []
    for row in rows:
        want = expected[row["supplement_sha256"]]
        bindings.append({
            **dict(row),
            "event_matches": row["event_id"] == want["eventId"],
            "forecast_matches": row["forecast_id"] == want["forecastId"],
            "version_matches": row["artifact_version"] == PAPER_ELIGIBILITY_VERSIONS["NFL"],
        })

    tally = lambda receipts, key: sum(1 for r in receipts if r["result"] == key)  # noqa: E731
    delta = {t: after[t] - before[t] for t in GUARDED_TABLES}
    out = {
        "schemaVersion": "AISP_NFL_WEEK2_PAPER_ELIGIBILITY_CONSUMER_IMPORT_V1",
        "importedAtUtc": datetime.now(UTC).isoformat(),
        "batchAddress": receipt["batchAddress"],
        "discoveryIndex": receipt["consumerDiscoveryIndex"],
        "firstRun": {k: tally(result["receipts"], k) for k in ("IMPORTED", "ALREADY_PRESENT", "REJECTED")},
        "secondRun": {k: tally(second["receipts"], k) for k in ("IMPORTED", "ALREADY_PRESENT", "REJECTED")},
        "rejections": [r for r in result["receipts"] if r["result"] == "REJECTED"],
        "tableDeltaFirstRun": delta,
        "tableDeltaSecondRun": {t: after_second[t] - after[t] for t in GUARDED_TABLES},
        "bindings": bindings,
        "allBindingsExact": len(bindings) == 16 and all(
            b["event_matches"] and b["forecast_matches"] and b["version_matches"] for b in bindings),
    }
    text_out = json.dumps(out, indent=2, sort_keys=True, default=str)
    if args.out:
        args.out.write_text(text_out + "\n", encoding="utf-8")
    print(text_out)
    return 0 if out["allBindingsExact"] and not out["rejections"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
