"""Two research arms of one NBA joint simulation.

Hand-written: autogenerate also reports unrelated index and nullability drift
on this database, and a migration that quietly alters a table nobody asked
about is how an unrelated outage gets blamed on the wrong commit.

Revision ID: 20260914_0040
Revises: 20260914_0039
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260914_0040"
down_revision = "20260914_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nba_joint_comparison_arms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("forecast_id", sa.String(length=200), nullable=False),
        sa.Column("forecast_sha256", sa.String(length=64), nullable=False),
        sa.Column("arm", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("configuration_identity", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("canonical_game_id", sa.Uuid(), nullable=False),
        sa.Column("supplements_forecast_id", sa.String(length=200), nullable=False),
        sa.Column("supplements_forecast_sha256", sa.String(length=64), nullable=False),
        sa.Column("draw_count", sa.Integer(), nullable=False),
        sa.Column("away_win_probability", sa.Float(), nullable=True),
        sa.Column("home_win_probability", sa.Float(), nullable=True),
        sa.Column("tie_probability", sa.Float(), nullable=True),
        sa.Column("expected_away", sa.Float(), nullable=True),
        sa.Column("expected_home", sa.Float(), nullable=True),
        sa.Column("expected_total", sa.Float(), nullable=True),
        sa.Column("joint_simulation", sa.JSON(), nullable=False),
        sa.Column("ridge_point_expectations", sa.JSON(), nullable=True),
        sa.Column("separate_elo_probabilities", sa.JSON(), nullable=True),
        sa.Column("limitations", sa.JSON(), nullable=True),
        sa.Column("authority", sa.JSON(), nullable=False),
        sa.Column("population", sa.JSON(), nullable=True),
        sa.Column("source_cutoff_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("research_only", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["canonical_game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
        # Idempotency: a re-run finds this row rather than adding a second.
        sa.UniqueConstraint("forecast_id", name="uq_nba_joint_arm_forecast"),
    )
    op.create_index("ix_nba_joint_arm_game", "nba_joint_comparison_arms",
                    ["canonical_game_id"])


def downgrade() -> None:
    op.drop_index("ix_nba_joint_arm_game", table_name="nba_joint_comparison_arms")
    op.drop_table("nba_joint_comparison_arms")
