"""database-enforced authoritative-history immutability (INT-001)

Installs PostgreSQL BEFORE UPDATE OR DELETE row triggers that reject bulk/Core/raw
SQL attempts to rewrite or delete published forecasts, model identity, experiment
records, official positions, and settlements. The triggers mirror the ORM
``before_flush`` contract in ``app.audit.immutability`` and fire for every role.

PostgreSQL only. On other dialects (e.g. the SQLite used by the fast test suite)
this migration is a no-op and the ORM guard remains the enforcement boundary.

Before installing, the migration validates that every guarded table and every
column referenced by a guard exists, and that ``position_results`` holds at most
one settlement per position. It raises with a clear report if the live schema has
drifted, so the invariant is never installed against an unexpected shape.

Revision ID: 20260905_0027
Revises: 20260904_0026
Create Date: 2026-09-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.audit.immutability_sql import (
    GUARDED_TABLES,
    _MODEL_VERSION_IMMUTABLE,
    _POSITION_IMMUTABLE,
    create_statements,
    drop_statements,
)

revision = "20260905_0027"
down_revision = "20260904_0026"
branch_labels = None
depends_on = None


# Columns each guard reads, checked for existence before install.
_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "forecasts": ("status", "forecast_track"),
    "model_versions": _MODEL_VERSION_IMMUTABLE,
    "shadow_experiments": (),
    "shadow_experiment_decisions": (),
    "official_asp_positions": ("status", "shadow_experiment_id", *_POSITION_IMMUTABLE),
    "position_results": ("position_id",),
}


def _validate_schema(bind: sa.engine.Connection) -> None:
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    problems: list[str] = []
    for table in GUARDED_TABLES:
        if table not in existing_tables:
            problems.append(f"missing guarded table: {table}")
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        for column in _REQUIRED_COLUMNS.get(table, ()):  # required by the guard body
            if column not in columns:
                problems.append(f"{table}.{column} column is missing")
    if problems:
        raise RuntimeError(
            "INT-001 immutability migration aborted: live schema does not match the "
            "guard contract; refusing to install triggers. Details: " + "; ".join(problems)
        )

    # Data invariant: at most one settlement per position (append-only, exactly once).
    duplicates = bind.execute(
        sa.text(
            "SELECT position_id, COUNT(*) AS n FROM position_results "
            "GROUP BY position_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicates:
        raise RuntimeError(
            "INT-001 immutability migration aborted: position_results already contains "
            f"{len(duplicates)} position_id(s) with multiple settlements; resolve before "
            "installing the append-only guard."
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    _validate_schema(bind)
    for statement in create_statements():
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for statement in drop_statements():
        op.execute(statement)
