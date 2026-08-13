from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config

from app.audit import install_immutability_guards
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.jobs import JobOrchestrator, seed_demo_data
from app.services.auth_service import ensure_admin


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return str(value)


async def _run(command: str, event_date: date | None) -> dict[str, Any]:
    settings = get_settings()
    _upgrade_database(settings.database_url)
    install_immutability_guards()
    with SessionLocal() as session:
        admin = ensure_admin(session, settings)
        if command == "seed-demo":
            result = await seed_demo_data(session, settings, event_date)
        elif command == "run-pipeline":
            run = await JobOrchestrator(session, settings).run("daily-pipeline", requested_by_user_id=admin.id, event_date=event_date)
            result = {"job_id": str(run.id), "status": run.status.value, "result": run.result}
        elif command == "controlled-live-validation":
            run = await JobOrchestrator(session, settings).run(
                "controlled-live-validation",
                requested_by_user_id=admin.id,
                event_date=event_date,
            )
            result = {"job_id": str(run.id), "status": run.status.value, "result": run.result}
        elif command == "create-admin":
            result = {"admin_id": str(admin.id), "email": admin.email, "password": "configured via ADMIN_PASSWORD"}
        else:
            result = {"status": "database initialized"}
    return result


def _run_training(training_cutoff: datetime | None) -> dict[str, Any]:
    """Run provider-free model development against an already-migrated database.

    Unlike operational commands, this path deliberately does not upgrade the
    database, create an administrator, start jobs, or construct data adapters.
    """

    from app.training.pipeline import Gate3TrainingPipeline

    settings = get_settings()
    install_immutability_guards()
    with SessionLocal() as session:
        result = Gate3TrainingPipeline(session, settings).run(training_cutoff=training_cutoff)
    return asdict(result)


async def _run_historical_backfill(args: argparse.Namespace) -> dict[str, Any]:
    """Run the standalone Gate 3A raw-archive path without canonical persistence."""

    from app.historical.backfill import (
        HistoricalBackfillOptions,
        build_default_backfill_runner,
    )

    settings = get_settings()
    options = HistoricalBackfillOptions(
        start_date=args.start_date,
        end_date=args.end_date,
        season=args.season,
        resume=args.resume,
        source=args.source,
        chunk_size=args.chunk_size,
        dry_run=args.dry_run,
        max_requests=args.max_requests,
        # Optional historical sources are deliberately disabled in this A0
        # command. There are no CLI toggles that imply they can be enabled.
        skip_statcast=True,
        skip_weather=True,
        # There is intentionally no environment/config override yet. Until a
        # reviewed governance record is wired to the ORM repository, non-dry
        # provider acquisition is blocked before adapter construction.
        terms_status="LEGAL_REVIEW_REQUIRED",
    )
    result = await build_default_backfill_runner(settings.raw_data_root).run(options)
    return asdict(result)


def _run_historical_report(season: int | None) -> dict[str, Any]:
    """Read local Gate 3A state without migrations, jobs, or source adapters."""

    from app.historical.reporting import HistoricalReportPaths, build_mlb_history_report

    settings = get_settings()
    paths = HistoricalReportPaths(
        raw_root=settings.raw_data_root,
        curated_root=settings.curated_data_root,
        feature_root=settings.feature_data_root,
        runtime_root=settings.gate3_artifact_root,
    )
    with SessionLocal() as session:
        return build_mlb_history_report(session, paths=paths, season=season)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _upgrade_database(database_url: str) -> None:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Sports Predictor backend operations")
    parser.add_argument(
        "command",
        choices=[
            "init-db",
            "seed-demo",
            "run-pipeline",
            "controlled-live-validation",
            "create-admin",
            "train-mlb-models",
            "backfill-mlb-history",
            "report-mlb-history",
        ],
    )
    parser.add_argument("--date", dest="event_date", type=date.fromisoformat, help="Event date (YYYY-MM-DD)")
    parser.add_argument(
        "--as-of",
        dest="training_cutoff",
        type=_parse_datetime,
        help="Inclusive evidence cutoff for training data (ISO-8601; defaults to now)",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, help="Historical start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=date.fromisoformat, help="Historical end date (YYYY-MM-DD)")
    parser.add_argument("--season", type=int, help="MLB season for the requested range")
    parser.add_argument("--resume", action="store_true", help="Skip already-completed semantic chunks")
    parser.add_argument("--source", choices=["MLB_STATS_API"], default="MLB_STATS_API")
    parser.add_argument("--chunk-size", type=int, default=1, help="Checkpoint chunk size in days")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; construct no adapter")
    parser.add_argument("--max-requests", type=int, default=0, help="Hard cap on physical HTTP attempts")
    args = parser.parse_args()
    if args.command == "backfill-mlb-history":
        missing = [
            option
            for option in ("start_date", "end_date", "season")
            if getattr(args, option) is None
        ]
        if missing:
            parser.error(
                "backfill-mlb-history requires "
                + ", ".join("--" + value.replace("_", "-") for value in missing)
            )
        result = asyncio.run(_run_historical_backfill(args))
    elif args.command == "report-mlb-history":
        result = _run_historical_report(args.season)
    elif args.command == "train-mlb-models":
        result = _run_training(args.training_cutoff)
    else:
        result = asyncio.run(_run(args.command, args.event_date))
    print(json.dumps(result, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
