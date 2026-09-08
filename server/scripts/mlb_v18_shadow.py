"""Bounded command for the MLB V18 research-shadow track.

This is the ONLY way V18 evidence enters AISP. Nothing here runs on a page
load, on a schedule, or as a side effect of a read; a human or a job runner
invokes it explicitly and it exits.

    python -m scripts.mlb_v18_shadow register --bundle <dir> [--contract <dir>]
    python -m scripts.mlb_v18_shadow forecast --bundle <dir> [--contract <dir>]
                                              [--game <uuid>] [--limit N]
    python -m scripts.mlb_v18_shadow settle   [--date YYYY-MM-DD]
    python -m scripts.mlb_v18_shadow status   [--date YYYY-MM-DD]

``register`` and ``forecast`` are the same idempotent operation seen from two
angles: ``register`` records the bundle and every eligible forecast in it,
``forecast`` does the same but lets you take one game first and then the rest.
Re-running either writes nothing new.

Concurrency: every mutating subcommand holds a PostgreSQL advisory lock, so two
invocations can never register or settle the same evidence at once. The lock is
session-scoped and released when the process exits, including on failure.

This command makes NO provider request and spends NO provider credit. It reads
only bundle files on disk and evidence AISP already persisted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Game,
    GameFinalOutcome,
    ShadowForecast,
    ShadowForecastBundle,
)
from app.shadow_import.mlb_v18 import (  # noqa: E402
    ENGINE_ID,
    V18ImportError,
    verify_bundle,
    verify_contract,
)
from app.shadow_import.mlb_v18_registry import register_bundle  # noqa: E402
from app.shadow_import.mlb_v18_settlement import settle_shadow_forecasts  # noqa: E402

#: Arbitrary but fixed 64-bit key. One namespace for the whole V18 track, so
#: register/forecast/settle are mutually exclusive with each other too.
ADVISORY_LOCK_KEY = 0x4D4C42563138  # "MLBV18"


class JobBusy(RuntimeError):
    """Another V18 command already holds the lock."""


def _acquire(session: Session) -> None:
    acquired = session.scalar(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
    )
    if not acquired:
        raise JobBusy(
            "Another mlb_v18_shadow command is running; refusing to start a "
            "duplicate job."
        )


def _release(session: Session) -> None:
    try:
        session.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
        )
    except Exception:  # pragma: no cover - best effort on teardown
        pass


def _load(bundle_dir: str, contract_dir: str | None):
    bundle = verify_bundle(bundle_dir)
    contract = (
        verify_contract(contract_dir, bundle_sha256=bundle.bundle_sha256)
        if contract_dir
        else None
    )
    return bundle, contract


def cmd_register(args: argparse.Namespace) -> int:
    bundle, contract = _load(args.bundle, args.contract)
    game_ids = {args.game} if args.game else None
    if game_ids is None and args.limit:
        eligible = [str(item.get("gameId")) for item in bundle.forecasts]
        game_ids = set(eligible[: args.limit])

    with SessionLocal() as session:
        _acquire(session)
        try:
            outcome = register_bundle(
                session, bundle, contract, now=datetime.now(UTC), game_ids=game_ids
            )
            session.commit()
        finally:
            _release(session)

    with SessionLocal() as session:
        uncovered = _uncovered_pregame_games(session, bundle)

    print(json.dumps({
        "command": "register",
        "bundleId": bundle.bundle_id,
        "bundleSha256": bundle.bundle_sha256,
        "artifactsVerified": bundle.artifact_count,
        "evaluationContract": contract.contract_id if contract else None,
        "evaluationContractSha256": contract.contract_sha256 if contract else None,
        "bundleRowCreated": outcome.bundle_row_created,
        "registered": outcome.registered,
        "alreadyPresent": outcome.already_present,
        "skipped": [
            {"gameId": game, "reason": reason} for game, reason in outcome.skipped
        ],
        "persistedForecasts": outcome.persisted_count,
        "uncoveredPregameGames": uncovered,
        "wageringAuthority": False,
        "gen1HistoryMutated": False,
    }, indent=2))
    return 0


def _uncovered_pregame_games(session: Session, bundle) -> list[dict[str, str]]:
    """Eligible pregame games this bundle produced no forecast for.

    Reported rather than filled in. AISP does not execute the V18 bundle
    (its handoff sets executionAllowed=false), so a game Model Studio did not
    score simply has no V18 forecast -- it is never inferred from another game
    or from Gen 1.
    """

    covered = {str(item.get("gameId")) for item in bundle.forecasts}
    slate = bundle.forecasts[0].get("scheduledStart") if bundle.forecasts else None
    if not slate:
        return []
    reference = datetime.fromisoformat(str(slate).replace(" ", "T", 1))
    day = reference.date()
    offset = reference.tzinfo
    lower = datetime.combine(day, datetime.min.time()).replace(tzinfo=offset)
    upper = lower + timedelta(days=1)
    rows = session.scalars(
        select(Game)
        .where(Game.starts_at >= lower, Game.starts_at < upper)
        .order_by(Game.starts_at)
    ).all()
    uncovered: list[dict[str, str]] = []
    for game in rows:
        if str(game.id) in covered:
            continue
        # Only a game that is still genuinely pregame could have carried a
        # prospective forecast; anything started or finished is not "missing" one.
        if str(getattr(game.status, "value", game.status)).upper() != "SCHEDULED":
            continue
        uncovered.append({
            "gameId": str(game.id),
            "startsAt": game.starts_at.isoformat(),
            "reason": (
                "the verified bundle contains no V18 forecast for this game, and "
                "this consumer may not execute the bundle (executionAllowed=false)"
            ),
        })
    return uncovered


def cmd_settle(args: argparse.Namespace) -> int:
    slate = date.fromisoformat(args.date) if args.date else None
    with SessionLocal() as session:
        _acquire(session)
        try:
            result = settle_shadow_forecasts(session, slate_date=slate)
            session.commit()
        finally:
            _release(session)
    print(json.dumps({"command": "settle", **result}, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with SessionLocal() as session:
        bundles = session.scalars(
            select(ShadowForecastBundle).where(
                ShadowForecastBundle.league == "MLB"
            ).order_by(ShadowForecastBundle.imported_at.desc())
        ).all()
        rows = session.scalars(
            select(ShadowForecast).where(ShadowForecast.engine_id == ENGINE_ID)
            .order_by(ShadowForecast.scheduled_start_utc)
        ).all()
        payload = {
            "command": "status",
            "engineId": ENGINE_ID,
            "bundles": [
                {
                    "bundleId": item.bundle_id,
                    "bundleSha256": item.bundle_sha256,
                    "slateDate": item.slate_date.isoformat(),
                    "status": item.status,
                    "researchShadowOnly": item.research_shadow_only,
                    "forecastCount": item.forecast_count,
                }
                for item in bundles
            ],
            "forecasts": [],
        }
        for row in rows:
            game = session.get(Game, row.canonical_game_id) if row.canonical_game_id else None
            outcome = session.scalar(
                select(GameFinalOutcome).where(
                    GameFinalOutcome.game_id == row.canonical_game_id
                ).order_by(GameFinalOutcome.revision.desc()).limit(1)
            ) if row.canonical_game_id else None
            payload["forecasts"].append({
                "forecastId": row.forecast_id,
                "gameId": row.event_id,
                "scheduledStart": (
                    row.scheduled_start_utc.isoformat()
                    if row.scheduled_start_utc else None
                ),
                "forecastAt": (
                    row.forecast_cutoff_utc.isoformat()
                    if row.forecast_cutoff_utc else None
                ),
                "homeWinProbability": row.full_home_win_probability,
                "gameStatus": game.status.value if game else None,
                "settled": outcome is not None,
            })
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mlb_v18_shadow",
        description="Bounded MLB V18 research-shadow import and settlement.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    default_bundle = os.environ.get("MLB_V18_BUNDLE_DIR")
    default_contract = os.environ.get("MLB_V18_CONTRACT_DIR")

    for name in ("register", "forecast"):
        item = sub.add_parser(
            name,
            help="Verify a bundle and persist its prospective forecasts.",
        )
        item.add_argument("--bundle", default=default_bundle, required=not default_bundle)
        item.add_argument("--contract", default=default_contract)
        item.add_argument("--game", help="Only this game id.")
        item.add_argument("--limit", type=int, help="Only the first N forecasts.")
        item.set_defaults(func=cmd_register)

    settle = sub.add_parser(
        "settle", help="Attach final outcomes to settled shadow forecasts."
    )
    settle.add_argument("--date", help="Slate date (YYYY-MM-DD).")
    settle.set_defaults(func=cmd_settle)

    status = sub.add_parser("status", help="Show registered V18 evidence.")
    status.add_argument("--date", help="Slate date (YYYY-MM-DD).")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (V18ImportError, JobBusy) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
