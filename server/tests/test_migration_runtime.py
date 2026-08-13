from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import User, UserRole, UserStatus


def test_migrated_sqlite_schema_accepts_server_default_insert(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "migrated.db"
    database_url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with Session(engine) as session:
        session.add(User(
            email="migration@example.com",
            password_hash="not-a-real-login-hash",
            display_name="Migration Check",
            role=UserRole.MEMBER,
            status=UserStatus.ACTIVE,
        ))
        session.commit()
        assert session.query(User).count() == 1
    engine.dispose()
