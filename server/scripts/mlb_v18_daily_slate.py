"""Offline, idempotent importer for a Model Studio V18 daily-slate handoff.

Usage::

    python -m scripts.mlb_v18_daily_slate import \
      --handoff <aisp-handoff.json> --receipt <slate-result.json>

The command verifies every checksum before opening a database transaction. It
imports precomputed research-shadow evidence only; it cannot execute V18 or
call a data provider.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.shadow_import.mlb_v18_daily import (  # noqa: E402
    DailyV18ImportError,
    register_daily_slate,
    verify_daily_slate,
)

ADVISORY_LOCK_KEY = 0x4D4C4256313844  # "MLBV18D"


def cmd_import(args: argparse.Namespace) -> int:
    verified = verify_daily_slate(args.handoff, args.receipt)
    with SessionLocal() as session:
        acquired = session.scalar(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": ADVISORY_LOCK_KEY},
        )
        if not acquired:
            raise DailyV18ImportError(
                "Another MLB V18 daily import is running; no duplicate was started"
            )
        try:
            outcome = register_daily_slate(session, verified)
            if outcome.rejected:
                session.rollback()
            else:
                session.commit()
        finally:
            try:
                session.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": ADVISORY_LOCK_KEY},
                )
            except Exception:
                pass

    payload = {
        "command": "import",
        "handoffSha256": verified.handoff_sha256,
        "receiptSha256": verified.receipt.get("receiptSha256"),
        "slateDate": verified.handoff.get("slateDate"),
        "verifiedForecasts": len(verified.forecasts),
        "imported": outcome.imported,
        "alreadyPresent": outcome.already_present,
        "publisherSkips": outcome.publisher_skips,
        "rejected": outcome.rejected,
        "unresolvedImportFailures": len(outcome.rejected),
        "wageringAuthority": False,
        "promotionStatus": "NOT_PROMOTED",
        "v18Executed": False,
        "providerCalls": 0,
        "gen1HistoryMutated": False,
    }
    print(json.dumps(payload, indent=2))
    return 2 if outcome.rejected else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mlb_v18_daily_slate",
        description="Verify and import precomputed V18 daily research-shadow forecasts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("import", help="Import one immutable aggregate handoff.")
    command.add_argument("--handoff", required=True)
    command.add_argument("--receipt", required=True)
    command.set_defaults(func=cmd_import)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except DailyV18ImportError as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
