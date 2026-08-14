"""Preserve complete forecast-to-market snapshot lineage.

Revision ID: 20260814_0009
Revises: 20260814_0008
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260814_0009"
down_revision = "20260814_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("forecasts") as batch_op:
        batch_op.alter_column(
            "market_snapshot_reference",
            existing_type=sa.String(length=500),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("forecasts") as batch_op:
        batch_op.alter_column(
            "market_snapshot_reference",
            existing_type=sa.Text(),
            type_=sa.String(length=500),
            existing_nullable=True,
        )
