"""Record SportsGameOdds consumption that predates the provider ledger.

Twenty-four objects were spent by authorised validation fetches before object
accounting existed. That consumption is real whether or not anything recorded
it, so the budget must carry it -- otherwise the guard protects an allowance
that is 24 objects larger than the one the provider is actually counting.

It is recorded as ONE adjustment that declares itself an adjustment, not as four
reconstructed request rows. Reconstructed rows would put four fabrications in an
audit trail, and an audit trail whose entries are sometimes invented is not one.

Idempotent: the fingerprint is derived from the adjustment's own content and the
ledger holds a unique constraint over it, so running this twice is a no-op.

    python -m scripts.sgo_opening_adjustment            # show what would happen
    python -m scripts.sgo_opening_adjustment --apply    # record it
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

#: What was spent, and what proves it. Each entry is an authorised fetch whose
#: object count is the number of EVENTS the provider returned -- its own
#: billable unit -- and the two captured payloads are addressed by digest.
OPENING_EVIDENCE = {
    "total_objects": 24,
    "billable_unit": "event object (one per event returned by /events)",
    "recorded_because": (
        "these fetches predate the provider request ledger; the objects were "
        "spent and must be carried by the budget"
    ),
    "fetches": [
        {"purpose": "first authorised validation (live events)",
         "filters": "leagueID=MLB&limit=2&oddsAvailable=true",
         "objects": 2,
         "response_sha256":
             "b5b16a580fb657acd687908eac4d1796c685f396a69583d2a35668d80f13b582"},
        {"purpose": "pregame validation",
         "filters": "leagueID=MLB&limit=2&startsAfter=...&finalized=false&oddsPresent=true",
         "objects": 2,
         "response_sha256":
             "adb794c1fcba359b9b9672cbe56b92d7c3d4ed7a25038c3806596709d5dc9da0"},
        {"purpose": "slate fetch whose ingest failed on a wrong class name",
         "filters": "leagueID=MLB&limit=10&startsAfter=...&startsBefore=+12h",
         "objects": 10,
         "response_sha256": None,
         "note": "payload not retained; the request succeeded and its objects were spent"},
        {"purpose": "bounded current-slate collection",
         "filters": "leagueID=MLB&limit=10&startsAfter=...&startsBefore=+12h",
         "objects": 10,
         "response_sha256": None,
         "note": "produced the 180 snapshots recorded against source=SPORTSGAMEODDS"},
    ],
    "reports": [
        "outputs/sportsgameodds-validation/VALIDATION.md",
        "outputs/sportsgameodds-validation/FALLBACK_ACTIVATION.md",
        "outputs/sportsgameodds-validation/OPERATIONAL_CLOSURE.md",
    ],
}
#: The day the objects were actually spent, so the adjustment ages out of the
#: rolling window on the same schedule a real request would.
OCCURRED_ON = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Record the adjustment. Without it, nothing is written.")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.services.sgo_collection import (
        apply_opening_adjustment, object_budget,
    )

    engine = create_engine(os.environ["DATABASE_URL"])
    total = sum(item["objects"] for item in OPENING_EVIDENCE["fetches"])
    assert total == OPENING_EVIDENCE["total_objects"], "evidence does not sum to its total"

    with Session(engine) as session:
        before = object_budget(session)
        print("budget before:", json.dumps(before.as_dict()))
        if not args.apply:
            print(f"\nDRY RUN. Would record {total} objects dated "
                  f"{OCCURRED_ON.date().isoformat()}. Nothing was written.")
            return 0
        result = apply_opening_adjustment(
            session, objects=total, occurred_at=OCCURRED_ON, evidence=OPENING_EVIDENCE)
        session.commit()
        after = object_budget(session)
        print("\nadjustment:", json.dumps(result))
        print("budget after :", json.dumps(after.as_dict()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
