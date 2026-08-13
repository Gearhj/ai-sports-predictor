from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, is_dataclass
from datetime import date
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
        elif command == "create-admin":
            result = {"admin_id": str(admin.id), "email": admin.email, "password": "configured via ADMIN_PASSWORD"}
        else:
            result = {"status": "database initialized"}
    return result


def _upgrade_database(database_url: str) -> None:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Sports Predictor backend operations")
    parser.add_argument("command", choices=["init-db", "seed-demo", "run-pipeline", "create-admin"])
    parser.add_argument("--date", dest="event_date", type=date.fromisoformat, help="Event date (YYYY-MM-DD)")
    args = parser.parse_args()
    result = asyncio.run(_run(args.command, args.event_date))
    print(json.dumps(result, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
