"""Capture the immutable odds observation used by official virtual positions.

Revision ID: 20260812_0002
Revises: 20260812_0001
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_0002"
down_revision = "20260812_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("official_asp_positions") as batch_op:
        batch_op.add_column(sa.Column("odds_snapshot_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_official_position_odds_snapshot",
            "odds_snapshots",
            ["odds_snapshot_id"],
            ["id"],
        )
        batch_op.create_index("ix_official_asp_positions_odds_snapshot_id", ["odds_snapshot_id"])


def downgrade() -> None:
    with op.batch_alter_table("official_asp_positions") as batch_op:
        batch_op.drop_index("ix_official_asp_positions_odds_snapshot_id")
        batch_op.drop_constraint("fk_official_position_odds_snapshot", type_="foreignkey")
        batch_op.drop_column("odds_snapshot_id")
