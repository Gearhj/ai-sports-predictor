"""cfb research forecasts

The first College Football forecast. Its own table: the NBA research table
exists because its package had no distributions, and this one carries ten
thousand draws, four complete PMFs and twelve retained intervals. No foreign key
to games -- College Football is not a registered sport and has no admitted
schedule source, so the producer's own CFBD identifiers are retained instead.

Revision ID: 20260914_0042
Revises: 20260914_0041
Create Date: 2026-09-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260914_0042"
down_revision = "20260914_0041"
branch_labels = None
depends_on = None

FRACTIONS = (
    "ridge_home_win_probability", "ridge_away_win_probability",
    "elo_home_win_probability", "elo_away_win_probability",
    "joint_home_win_probability", "joint_away_win_probability",
    "tie_probability",
)


def upgrade() -> None:
    op.create_table(
        "cfb_research_forecasts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("forecast_id", sa.String(200), nullable=False),
        sa.Column("forecast_sha256", sa.String(64), nullable=False),
        sa.Column("forecast_bytes", sa.Integer(), nullable=False),
        sa.Column("forecast_schema", sa.String(80), nullable=False),
        sa.Column("package_index_sha256", sa.String(64), nullable=False),
        sa.Column("package_index_bytes", sa.Integer(), nullable=False),
        sa.Column("publication_receipt_sha256", sa.String(64)),
        sa.Column("joint_draws_sha256", sa.String(64)),
        sa.Column("pmf_sha256", sa.String(64)),
        sa.Column("baseline_configuration", sa.String(160), nullable=False),
        sa.Column("source_provider", sa.String(40), nullable=False,
                  server_default="CFBD"),
        sa.Column("source_game_id", sa.String(64), nullable=False),
        sa.Column("home_source_team_id", sa.String(64), nullable=False),
        sa.Column("away_source_team_id", sa.String(64), nullable=False),
        sa.Column("home_team_name", sa.String(120), nullable=False),
        sa.Column("away_team_name", sa.String(120), nullable=False),
        sa.Column("neutral_site", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("conference_competition", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("captured_kickoff_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schedule_revision_captured_utc", sa.DateTime(timezone=True)),
        sa.Column("expected_away_points", sa.Float(), nullable=False),
        sa.Column("expected_home_points", sa.Float(), nullable=False),
        sa.Column("expected_total", sa.Float(), nullable=False),
        sa.Column("expected_home_margin", sa.Float(), nullable=False),
        *[sa.Column(name, sa.Float(), nullable=False) for name in FRACTIONS],
        sa.Column("draw_count", sa.Integer(), nullable=False),
        sa.Column("pmfs", sa.JSON(), nullable=False),
        sa.Column("intervals", sa.JSON(), nullable=False),
        sa.Column("illustrative_totals", sa.JSON(), nullable=False),
        sa.Column("simulation_mcse", sa.JSON()),
        sa.Column("transformation_accounting", sa.JSON()),
        sa.Column("feature_values", sa.JSON()),
        sa.Column("limitations", sa.JSON()),
        sa.Column("challenger", sa.JSON()),
        sa.Column("model_bindings", sa.JSON()),
        sa.Column("source_bindings", sa.JSON()),
        sa.Column("source_usage", sa.JSON()),
        sa.Column("source_cutoff_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_created_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exchange_published_utc", sa.DateTime(timezone=True)),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("research_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("forecast_id", name="uq_cfb_research_forecast_id"),
        sa.CheckConstraint("draw_count >= 0", name="ck_cfb_research_draw_count"),
        sa.CheckConstraint("research_only", name="ck_cfb_research_only"),
        *[sa.CheckConstraint(f"{name} >= 0 AND {name} <= 1",
                             name=f"ck_cfb_research_{name}_fraction")
          for name in FRACTIONS],
    )
    op.create_index("ix_cfb_research_source_game", "cfb_research_forecasts",
                    ["source_game_id"])
    op.create_index("ix_cfb_research_kickoff", "cfb_research_forecasts",
                    ["captured_kickoff_utc"])


def downgrade() -> None:
    op.drop_index("ix_cfb_research_kickoff", table_name="cfb_research_forecasts")
    op.drop_index("ix_cfb_research_source_game", table_name="cfb_research_forecasts")
    op.drop_table("cfb_research_forecasts")
