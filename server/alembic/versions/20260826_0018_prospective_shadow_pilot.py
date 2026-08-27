"""Prospective shadow pilot: availability events + availability-triggered rescores.

Revision ID: 20260826_0018
Revises: 20260826_0017
Create Date: 2026-08-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260826_0018"
down_revision = "20260826_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "availability_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("schema_version", sa.String(48), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_event_key", sa.String(200), nullable=False),
        sa.Column("sport", sa.String(24), nullable=False),
        sa.Column("league", sa.String(16), nullable=False),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=True),
        sa.Column("source_game_id", sa.String(120), nullable=True),
        sa.Column("team_source_id", sa.String(120), nullable=True),
        sa.Column("player_source_id", sa.String(120), nullable=True),
        sa.Column("player_name", sa.String(160), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("source", "source_event_key", name="uq_availability_event_source_key"),
    )
    op.create_index("ix_availability_events_event_type", "availability_events", ["event_type"])
    op.create_index("ix_availability_events_game_id", "availability_events", ["game_id"])

    op.create_table(
        "shadow_rescores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("availability_event_id", sa.Uuid(), sa.ForeignKey("availability_events.id"), nullable=False),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("market", sa.String(40), nullable=False),
        sa.Column("before_forecast_id", sa.Uuid(), sa.ForeignKey("forecasts.id"), nullable=True),
        sa.Column("after_forecast_id", sa.Uuid(), sa.ForeignKey("forecasts.id"), nullable=False),
        sa.Column("before_home_win_probability", sa.Float(), nullable=True),
        sa.Column("after_home_win_probability", sa.Float(), nullable=False),
        sa.Column("before_readiness", sa.Float(), nullable=True),
        sa.Column("after_readiness", sa.Float(), nullable=False),
        sa.Column("before_uncertainty", sa.Float(), nullable=True),
        sa.Column("after_uncertainty", sa.Float(), nullable=True),
        sa.Column("pre_quote", sa.JSON(), nullable=True),
        sa.Column("post_quote", sa.JSON(), nullable=True),
        sa.Column("book_moved", sa.Boolean(), nullable=False),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.String(16), nullable=False),
        sa.Column("virtual_stake", sa.Numeric(12, 2), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aisp_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rescore_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rescore_completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pre_event_quote_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("post_event_quote_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quote_timeline", sa.JSON(), nullable=False),
        sa.UniqueConstraint("availability_event_id", "game_id", name="uq_shadow_rescore_event_game"),
    )
    op.create_index("ix_shadow_rescores_availability_event_id", "shadow_rescores", ["availability_event_id"])
    op.create_index("ix_shadow_rescores_game_id", "shadow_rescores", ["game_id"])
    op.create_index("ix_shadow_rescores_slate_date", "shadow_rescores", ["slate_date"])


def downgrade() -> None:
    op.drop_table("shadow_rescores")
    op.drop_table("availability_events")
