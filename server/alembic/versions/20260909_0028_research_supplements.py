"""append-only research supplements (AISP_RESEARCH_SUPPLEMENT_V1)

Revision ID: 20260909_0028
Revises: 20260908_0027
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_0028"
down_revision = "20260908_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_supplements",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("supplement_sha256", sa.String(64), nullable=False),
        sa.Column("supplement_id", sa.String(200), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("sport", sa.String(24), nullable=False),
        sa.Column("event_id", sa.String(120), nullable=True),
        sa.Column("engine_id", sa.String(64), nullable=True),
        sa.Column("forecast_id", sa.String(120), nullable=True),
        sa.Column("shadow_forecast_id", sa.Uuid(), sa.ForeignKey("shadow_forecasts.id"), nullable=True),
        sa.Column("artifact_version", sa.String(64), nullable=False),
        sa.Column("simulation_kind", sa.String(32), nullable=False),
        sa.Column("simulation_count", sa.Integer(), nullable=True),
        sa.Column("simulation_seed", sa.BigInteger(), nullable=True),
        sa.Column("source_cutoff_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", sa.String(48), server_default="RESEARCH_SHADOW", nullable=False),
        sa.Column("research_shadow_only", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.create_index("ix_research_supplements_supplement_sha256", "research_supplements", ["supplement_sha256"], unique=True)
    op.create_index("ix_research_supplements_kind", "research_supplements", ["kind"])
    op.create_index("ix_research_supplements_sport", "research_supplements", ["sport"])
    op.create_index("ix_research_supplements_event_id", "research_supplements", ["event_id"])
    op.create_index("ix_research_supplements_engine_id", "research_supplements", ["engine_id"])
    op.create_index("ix_research_supplements_forecast_id", "research_supplements", ["forecast_id"])
    op.create_index("ix_research_supplements_shadow_forecast_id", "research_supplements", ["shadow_forecast_id"])
    op.create_index("ix_research_supplements_event_kind", "research_supplements", ["event_id", "kind"])


def downgrade() -> None:
    op.drop_table("research_supplements")
