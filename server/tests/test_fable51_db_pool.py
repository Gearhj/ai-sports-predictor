"""The PostgreSQL connection pool must be sized explicitly and configurably.

SQLAlchemy's defaults (pool_size=5, max_overflow=10, pool_timeout=30 s) are
smaller than FastAPI's 40-slot sync threadpool, so under modest concurrency
requests silently queue for a connection for up to 30 s. SQLite (tests) keeps
its StaticPool/in-memory behaviour untouched.
"""

from __future__ import annotations

from sqlalchemy.pool import StaticPool

from app.db.session import _engine_options


def test_postgres_pool_uses_configured_sizes(settings):
    tuned = settings.model_copy(update={
        "db_pool_size": 7, "db_max_overflow": 13, "db_pool_timeout_seconds": 4.5,
    })
    options = _engine_options("postgresql+psycopg://u:p@localhost:5432/db", tuned)
    assert options == {"pool_pre_ping": True, "pool_size": 7, "max_overflow": 13, "pool_timeout": 4.5}


def test_postgres_pool_defaults_cover_the_request_threadpool(settings):
    options = _engine_options("postgresql+psycopg://u:p@localhost:5432/db", settings)
    # 40 anyio threadpool slots; pool + overflow must be >= that with margin
    # and stay well under PostgreSQL's 100-connection default per replica.
    assert options["pool_size"] + options["max_overflow"] >= 30
    assert options["pool_size"] + options["max_overflow"] <= 60
    assert options["pool_timeout"] <= 10


def test_sqlite_memory_engine_options_unchanged(settings):
    options = _engine_options("sqlite://", settings)
    assert options["poolclass"] is StaticPool
    assert options["connect_args"] == {"check_same_thread": False}
    assert "pool_size" not in options
