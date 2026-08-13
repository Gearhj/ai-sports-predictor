"""Alembic environment for the canonical ASP persistence schema."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.base import Base
import app.models  # noqa: F401 - registers every mapped table on Base.metadata
from app.core.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("DATABASE_URL") or get_settings().database_url
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata

# SQLAlchemy's portable ``Enum(native_enum=False, create_constraint=True)``
# emits type-bound CHECK constraints. Alembic's check-constraint comparison
# plugin sees the reflected constraints as standalone objects and otherwise
# proposes removing them on SQLite even though they are still present in the
# model. Enum columns remain covered by type comparison; filter only these
# generated, type-bound constraint names from standalone comparison.
_type_bound_check_names = {
    constraint.name
    for table in target_metadata.tables.values()
    for constraint in table.constraints
    if getattr(constraint, "_type_bound", False) and constraint.name
}


def include_object(object_: object, name: str | None, type_: str, reflected: bool, compare_to: object | None) -> bool:
    if type_ == "check_constraint" and name in _type_bound_check_names:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
