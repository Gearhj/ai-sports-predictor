"""Persist MLB official dates for exact historical population bounds.

Revision ID: 20260814_0007
Revises: 20260814_0006
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260814_0007"
down_revision = "20260814_0006"
branch_labels = None
depends_on = None


def _backfill_unambiguous_checkpoint_dates() -> None:
    """Recover only exact one-day dates already persisted by the A1 writer.

    Checkpoint event bounds were constructed from the minimum and maximum DTO
    ``official_date`` values. Therefore a checkpoint whose UTC start and end
    fall on one calendar date proves that date for every linked outcome. A
    multi-day checkpoint does not prove any individual outcome date and is
    deliberately left NULL. Static dialect-specific SQL keeps this operation
    usable in Alembic offline mode without first-pitch or provider inference.
    """

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(sa.text("""
            UPDATE game_final_outcomes AS outcomes
            SET official_date =
                (checkpoints.event_start_at AT TIME ZONE 'UTC')::date
            FROM history_backfill_checkpoints AS checkpoints
            WHERE outcomes.checkpoint_id = checkpoints.id
              AND outcomes.official_date IS NULL
              AND checkpoints.event_start_at IS NOT NULL
              AND checkpoints.event_end_at IS NOT NULL
              AND (checkpoints.event_start_at AT TIME ZONE 'UTC')::date =
                  (checkpoints.event_end_at AT TIME ZONE 'UTC')::date
        """))
        return
    if dialect == "sqlite":
        op.execute(sa.text("""
            UPDATE game_final_outcomes
            SET official_date = (
                SELECT date(checkpoints.event_start_at)
                FROM history_backfill_checkpoints AS checkpoints
                WHERE checkpoints.id = game_final_outcomes.checkpoint_id
            )
            WHERE official_date IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM history_backfill_checkpoints AS checkpoints
                  WHERE checkpoints.id = game_final_outcomes.checkpoint_id
                    AND checkpoints.event_start_at IS NOT NULL
                    AND checkpoints.event_end_at IS NOT NULL
                    AND date(checkpoints.event_start_at) =
                        date(checkpoints.event_end_at)
              )
        """))
        return
    raise RuntimeError(
        "Gate 3A official-date migration supports only PostgreSQL and SQLite"
    )


def upgrade() -> None:
    # A0 and already-persisted early A1 rows did not retain MLB officialDate.
    # Exact one-day linked checkpoints can recover it because their bounds came
    # from DTO official_date; ambiguous/multi-day rows remain NULL. Never infer
    # this value from a UTC first pitch. The A1 writer requires it for new rows.
    with op.batch_alter_table("game_final_outcomes") as batch:
        batch.add_column(sa.Column("official_date", sa.Date(), nullable=True))
    _backfill_unambiguous_checkpoint_dates()
    op.create_index(
        "ix_game_final_official_date",
        "game_final_outcomes",
        ["official_date", "game_id", "revision"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_game_final_official_date",
        table_name="game_final_outcomes",
    )
    with op.batch_alter_table("game_final_outcomes") as batch:
        batch.drop_column("official_date")
