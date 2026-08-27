"""Live shadow operator: mutable per-game polling bookkeeping.

Revision ID: 20260826_0019
Revises: 20260826_0018
Create Date: 2026-08-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260826_0019"
down_revision = "20260826_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_shadow_poll_state",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("poll_count", sa.Integer(), nullable=False),
        sa.Column("last_probable_pitcher", sa.String(120), nullable=True),
        sa.Column("last_home_lineup_status", sa.String(24), nullable=True),
        sa.Column("last_away_lineup_status", sa.String(24), nullable=True),
        sa.Column("last_source_payload_id", sa.String(120), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_live_shadow_poll_state_game_id", "live_shadow_poll_state", ["game_id"], unique=True)

    op.create_table(
        "shadow_quote_captures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("rescore_id", sa.Uuid(), sa.ForeignKey("shadow_rescores.id"), nullable=False),
        sa.Column("slot", sa.String(20), nullable=False),
        sa.Column("quote", sa.JSON(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rescore_id", "slot", name="uq_shadow_quote_capture_slot"),
    )
    op.create_index("ix_shadow_quote_captures_rescore_id", "shadow_quote_captures", ["rescore_id"])


def downgrade() -> None:
    op.drop_table("shadow_quote_captures")
    op.drop_table("live_shadow_poll_state")
