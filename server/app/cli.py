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
        ],
    )
    parser.add_argument("--date", dest="event_date", type=date.fromisoformat, help="Event date (YYYY-MM-DD)")
    parser.add_argument(
        "--as-of",
        dest="training_cutoff",
        type=_parse_datetime,
        help="Inclusive evidence cutoff for training data (ISO-8601; defaults to now)",
    )
    args = parser.parse_args()
    result = (
        _run_training(args.training_cutoff)
        if args.command == "train-mlb-models"
        else asyncio.run(_run(args.command, args.event_date))
    )
    print(json.dumps(result, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
