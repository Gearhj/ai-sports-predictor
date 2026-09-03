"""SQLAlchemy engine and request-session lifecycle helpers."""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

DEFAULT_DATABASE_URL = "postgresql+psycopg://asp:REDACTED-CREDENTIAL@localhost:5433/asp"


def _engine_options(database_url: str, settings=None) -> dict[str, object]:
    """Return conservative options that also make in-memory SQLite reliable."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        resolved = settings or get_settings()
        return {
            "pool_pre_ping": True,
            "pool_size": resolved.db_pool_size,
            "max_overflow": resolved.db_max_overflow,
            "pool_timeout": resolved.db_pool_timeout_seconds,
        }

    options: dict[str, object] = {"connect_args": {"check_same_thread": False}}
    if url.database in (None, "", ":memory:"):
        options["poolclass"] = StaticPool
    return options


def get_engine(database_url: str | None = None) -> Engine:
    """Build an engine without coupling persistence to the application config module."""
    resolved_url = database_url or os.getenv("DATABASE_URL") or get_settings().database_url
    return create_engine(resolved_url, future=True, **_engine_options(resolved_url))


engine = get_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def create_db_and_tables(database_url: str | None = None) -> None:
    """Create all tables; primarily useful for isolated SQLite tests and local tooling."""
    from app import models  # noqa: F401 - imports model metadata before create_all
    from app.db.base import Base

    target_engine = engine if database_url is None else get_engine(database_url)
    Base.metadata.create_all(bind=target_engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI-compatible database-session dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
