"""prediction market semantic templates (versioned provider-template authority)

Revision ID: 20260904_0025
Revises: 20260831_0024
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_0025"
down_revision = "20260831_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prediction_market_semantic_templates",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("provider", sa.String(24), nullable=False, index=True),
        sa.Column("template_key", sa.String(80), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sport", sa.String(24), nullable=False),
        sa.Column("league", sa.String(24), nullable=False),
        sa.Column("market_type", sa.String(32), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("decision_source", sa.String(16), nullable=False, server_default="SEED"),
        sa.Column("decided_by_user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(as_uuid=True), sa.ForeignKey("prediction_market_semantic_templates.id"), nullable=True),
        sa.UniqueConstraint("provider", "template_key", "version", name="uq_pm_semantic_template_version"),
    )


def downgrade() -> None:
    op.drop_table("prediction_market_semantic_templates")
