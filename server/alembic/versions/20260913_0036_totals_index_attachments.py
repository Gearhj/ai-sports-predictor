"""Consumer acceptance records for published V18 totals-distribution indexes.

Purely additive: one new table. No distribution is copied here -- the PMF stays
the one the ordinary daily import already wrote to shadow_forecasts, and a
second copy would be a second source of truth free to drift from the forecast it
describes. What is stored is the acceptance event: which index, which exact
forecast identity, what was checked, and at whose clock.

Unique on (index_sha256, forecast_identity), so re-attaching the same index is
idempotent in the schema and a different index records its own acceptance
instead of overwriting the first.

Revision ID: 20260913_0036
Revises: 20260913_0035
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260913_0036"
down_revision = "20260913_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "totals_index_attachments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("index_sha256", sa.String(length=64), nullable=False),
        sa.Column("index_schema_version", sa.String(length=64), nullable=False),
        sa.Column("derivative_published_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attached_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_identity", sa.String(length=120), nullable=False),
        sa.Column("baseline_object_sha256", sa.String(length=64), nullable=False),
        sa.Column("engine_id", sa.String(length=32), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("simulator_id", sa.String(length=64), nullable=True),
        sa.Column("simulator_seed", sa.Integer(), nullable=True),
        sa.Column("canonical_game_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("games.id"), nullable=True),
        sa.Column("mlbam_game_pk", sa.String(length=32), nullable=False),
        sa.Column("game_date", sa.String(length=16), nullable=False),
        sa.Column("original_simulation_count", sa.Integer(), nullable=False),
        sa.Column("valid_original_draws_used", sa.Integer(), nullable=False),
        sa.Column("excluded_draw_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("extra_innings_included", sa.Boolean(), nullable=False),
        sa.Column("tail_truncated", sa.Boolean(), nullable=False),
        sa.Column("score_limited", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("pmf_matches_stored", sa.Boolean(), nullable=False),
        sa.Column("pmf_bin_count", sa.Integer(), nullable=False),
        sa.Column("recomputed_mean_total_runs", sa.Float(), nullable=False),
        sa.Column("declared_mean_total_runs", sa.Float(), nullable=True),
        sa.Column("verification_detail", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("index_sha256", "forecast_identity",
                            name="uq_totals_attachment_index_forecast"),
    )
    op.create_index("ix_totals_index_attachments_index_sha256",
                    "totals_index_attachments", ["index_sha256"])
    op.create_index("ix_totals_index_attachments_forecast_identity",
                    "totals_index_attachments", ["forecast_identity"])
    op.create_index("ix_totals_index_attachments_canonical_game_id",
                    "totals_index_attachments", ["canonical_game_id"])
    op.create_index("ix_totals_index_attachments_mlbam_game_pk",
                    "totals_index_attachments", ["mlbam_game_pk"])
    op.create_index("ix_totals_attachment_game", "totals_index_attachments",
                    ["canonical_game_id", "index_sha256"])


def downgrade() -> None:
    op.drop_table("totals_index_attachments")
