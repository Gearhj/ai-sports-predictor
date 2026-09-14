"""collection lane halts

A latched stop on one collection lane, with the response that caused it.

Revision ID: 20260914_0041
Revises: 20260914_0040
Create Date: 2026-09-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260914_0041"
down_revision = "20260914_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collection_lane_halts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("lane", sa.String(80), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("halted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("lifted_at", sa.DateTime(timezone=True)),
        sa.Column("lifted_by", sa.String(120)),
        sa.Column("lift_authority", sa.Text()),
    )
    op.create_index("ix_collection_lane_halts_lane", "collection_lane_halts", ["lane"])
    # Partial: one ACTIVE halt per lane, any number of lifted ones. The history
    # is the point -- a lane that has halted twice is telling you something a
    # single overwritten row would not.
    op.create_index(
        "uq_collection_lane_halt_active", "collection_lane_halts", ["lane"],
        unique=True, postgresql_where=sa.text("active"))


def downgrade() -> None:
    op.drop_index("uq_collection_lane_halt_active", table_name="collection_lane_halts")
    op.drop_index("ix_collection_lane_halts_lane", table_name="collection_lane_halts")
    op.drop_table("collection_lane_halts")
