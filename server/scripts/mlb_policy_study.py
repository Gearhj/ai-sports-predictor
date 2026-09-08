"""Offline receipt for a completed Model Studio selection-policy study.

    python -m scripts.mlb_policy_study verify --study <study.json> [--sha256 <digest>]
    python -m scripts.mlb_policy_study record --study <study.json> [--sha256 <digest>]
    python -m scripts.mlb_policy_study status

Verification is fail-closed and runs before any write. Recording appends one
immutable audit event and nothing else: it activates no policy, arms no
challenger, evaluates no game, and cannot reach a decision, position or
settlement. It calls no provider and executes no model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.shadow_import.mlb_policy_study import (  # noqa: E402
    PolicyStudyImportError,
    record_policy_study,
    recorded_policy_studies,
    verify_policy_study,
)


def _verified(args: argparse.Namespace):
    return verify_policy_study(
        args.study, settings=get_settings(), expected_sha256=args.sha256
    )


def cmd_verify(args: argparse.Namespace) -> int:
    verified = _verified(args)
    print(json.dumps({
        "command": "verify",
        "studyId": verified.study_id,
        "studySha256": verified.study_sha256,
        "verdict": verified.verdict,
        "retainedPolicyId": verified.retained_rules.get("policyId"),
        "policyActivated": False,
        "challengerCreated": False,
        "wageringAuthority": False,
    }, indent=2))
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    verified = _verified(args)
    with SessionLocal() as session:
        outcome = record_policy_study(session, verified)
        session.commit()
    print(json.dumps({"command": "record", **outcome}, indent=2))
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    with SessionLocal() as session:
        studies = recorded_policy_studies(session)
    print(json.dumps({
        "command": "status",
        "recordedStudies": len(studies),
        "studies": studies,
    }, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mlb_policy_study")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler, help_text in (
        ("verify", cmd_verify, "Verify a study without writing anything."),
        ("record", cmd_record, "Verify and record the read-only receipt."),
    ):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--study", required=True)
        item.add_argument("--sha256", default=None)
        item.set_defaults(func=handler)
    status = sub.add_parser("status", help="Show recorded study receipts.")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PolicyStudyImportError as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
