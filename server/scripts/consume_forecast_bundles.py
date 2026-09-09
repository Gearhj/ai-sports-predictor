"""Consume verified Model Studio forecast bundles from the Model Exchange.

    python -m scripts.consume_forecast_bundles --dry-run      # discover only
    python -m scripts.consume_forecast_bundles                # one-shot import
    python -m scripts.consume_forecast_bundles --scheduled    # scheduler entry

``--dry-run`` verifies and reports what WOULD be imported and writes nothing.
The one-shot form imports only bundles the exchange currently publishes and
AISP has not already registered. ``--scheduled`` is the entry a scheduler would
call; it does nothing until ``MODEL_EXCHANGE_AUTO_IMPORT_ENABLED`` is true.

No model is executed or refitted, no provider is called, and no official
decision or position can be created from here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.jobs.model_exchange_consumer import (  # noqa: E402
    consume_forecast_bundles,
    run_scheduled_import,
)

#: One consumer at a time, so two runs cannot import the same bundle twice.
ADVISORY_LOCK_KEY = 0x4D4F44454C4558  # "MODELEX"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="consume_forecast_bundles")
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    parser.add_argument("--scheduled", action="store_true", help="Scheduler entry point.")
    parser.add_argument("--root", default=None, help="Override the exchange location.")
    parser.add_argument("--limit", type=int, default=None, help="Import at most N bundles.")
    args = parser.parse_args(argv)

    settings = get_settings()
    with SessionLocal() as session:
        if not args.dry_run and not session.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        ):
            print(json.dumps({"refused": "another bundle consumer is running"}, indent=2))
            return 2
        try:
            if args.scheduled:
                payload = run_scheduled_import(session, settings)
            else:
                payload = consume_forecast_bundles(
                    session, settings,
                    dry_run=args.dry_run,
                    limit=args.limit,
                    root=Path(args.root) if args.root else None,
                )
            if not args.dry_run:
                session.commit()
        finally:
            if not args.dry_run:
                try:
                    session.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
                    )
                except Exception:  # pragma: no cover - best effort on teardown
                    pass

    print(json.dumps({"command": "consume_forecast_bundles", **payload}, indent=2))
    return 0 if not payload.get("rejected_count") else 3


if __name__ == "__main__":
    raise SystemExit(main())
