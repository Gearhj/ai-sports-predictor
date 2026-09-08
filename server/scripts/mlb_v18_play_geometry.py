"""Bounded command for the MLB V18 Play Geometry trace supplement.

    python -m scripts.mlb_v18_play_geometry register --supplement <dir>
    python -m scripts.mlb_v18_play_geometry verify   --supplement <dir>
    python -m scripts.mlb_v18_play_geometry status

Verification is fail-closed and runs before any write. Registration is
idempotent and additive: it appends one block to the parent shadow forecast's
own lineage and can reach nothing else. No Model Studio code is executed, no
provider is called, and nothing here can grant wagering authority.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.shadow_import.mlb_v18_play_geometry import (  # noqa: E402
    PlayGeometryImportError,
    geometry_status,
    register_geometry,
    verify_supplement,
)

#: Shared with the V18 shadow command, so no two provider- or evidence-touching
#: jobs can run against this track at once.
ADVISORY_LOCK_KEY = 0x4D4C42563138


class JobBusy(RuntimeError):
    """Another V18 command already holds the lock."""


def _acquire(session: Session) -> None:
    if not session.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}):
        raise JobBusy("Another V18 command is running; refusing to start a duplicate job.")


def _release(session: Session) -> None:
    try:
        session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
    except Exception:  # pragma: no cover - best effort on teardown
        pass


def cmd_verify(args: argparse.Namespace) -> int:
    verified = verify_supplement(args.supplement)
    print(json.dumps({
        "command": "verify",
        "supplementId": verified.supplement_id,
        "supplementSha256": verified.supplement_sha256,
        "parentBundleId": verified.parent_bundle_id,
        "parentBundleSha256": verified.parent_bundle_sha256,
        "artifactsVerified": verified.artifact_count,
        "renderablePaths": len(verified.paths),
        "coordinateSystem": verified.coordinate_system,
        "wageringAuthority": False,
        "executionAllowedInAISP": False,
    }, indent=2))
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    verified = verify_supplement(args.supplement)
    with SessionLocal() as session:
        _acquire(session)
        try:
            outcome = register_geometry(session, verified)
            session.commit()
        finally:
            _release(session)
    print(json.dumps({"command": "register", **outcome}, indent=2))
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    with SessionLocal() as session:
        print(json.dumps({"command": "status", **geometry_status(session)}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mlb_v18_play_geometry")
    sub = parser.add_subparsers(dest="command", required=True)
    default = os.environ.get("MLB_V18_PLAY_GEOMETRY_DIR")

    for name, handler in (("verify", cmd_verify), ("register", cmd_register)):
        item = sub.add_parser(name, help="Verify the checksum-addressed supplement.")
        item.add_argument("--supplement", default=default, required=not default)
        item.set_defaults(func=handler)

    status = sub.add_parser("status", help="Show which shadow forecasts carry geometry.")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (PlayGeometryImportError, JobBusy) as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
