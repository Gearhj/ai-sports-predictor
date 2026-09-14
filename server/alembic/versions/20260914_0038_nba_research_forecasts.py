"""An imported NBA research forecast that no simulator produced.

Purely additive: one new table. Deliberately NOT rows in ``shadow_forecasts``,
whose ``simulator_id``, ``simulator_version`` and ``simulator_seed`` are all NOT
NULL because every forecast it was built for came out of a Monte Carlo run. This
package carries drawCount = 0 and every distribution null, and its handoff
forbids attaching a simulation count. Placeholders in those columns would turn
"no simulator" into "a simulator seeded zero".

Unique on ``forecast_id``, so a delivery imports once and re-imports as a no-op;
the importer compares the stored digest and rejects conflicting bytes rather
than merging them.

Revision ID: 20260914_0038
Revises: 20260914_0037
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260914_0038"
down_revision = "20260914_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nba_research_forecasts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("forecast_id", sa.String(length=200), nullable=False),
        sa.Column("forecast_sha256", sa.String(length=64), nullable=False),
        sa.Column("forecast_bytes", sa.Integer(), nullable=False),
        sa.Column("forecast_schema", sa.String(length=80), nullable=False),
        sa.Column("package_index_sha256", sa.String(length=64), nullable=False),
        sa.Column("package_index_bytes", sa.Integer(), nullable=False),
        sa.Column("envelope_schema", sa.String(length=80), nullable=False),
        sa.Column("engine_id", sa.String(length=64), nullable=False),
        sa.Column("configuration_identity", sa.String(length=160)),
        sa.Column("point_configuration_identity", sa.String(length=160)),
        sa.Column("canonical_game_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("games.id"), nullable=False),
        sa.Column("source_game_id", sa.String(length=64), nullable=False),
        sa.Column("home_franchise_id", sa.String(length=120)),
        sa.Column("away_franchise_id", sa.String(length=120)),
        sa.Column("schedule_source_revision", sa.Text()),
        sa.Column("captured_tipoff_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_game_date", sa.Date(), nullable=False),
        sa.Column("ridge_expected_points_home", sa.Float()),
        sa.Column("ridge_expected_points_away", sa.Float()),
        sa.Column("ridge_expected_total", sa.Float()),
        sa.Column("ridge_home_margin", sa.Float()),
        sa.Column("elo_win_probability_home", sa.Float()),
        sa.Column("elo_win_probability_away", sa.Float()),
        sa.Column("elo_tie_probability", sa.Float()),
        sa.Column("elo_method", sa.Text()),
        sa.Column("draw_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("distribution_status", sa.String(length=32), nullable=False,
                  server_default="UNAVAILABLE"),
        sa.Column("source_cutoff_utc", sa.DateTime(timezone=True)),
        sa.Column("forecast_created_utc", sa.DateTime(timezone=True)),
        sa.Column("local_export_utc", sa.DateTime(timezone=True)),
        sa.Column("producer_published_utc", sa.DateTime(timezone=True)),
        sa.Column("exchange_delivered_utc", sa.DateTime(timezone=True)),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_forecast_id", sa.String(length=200)),
        sa.Column("superseded_by_forecast_id", sa.String(length=200)),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("forecast_id", name="uq_nba_research_forecast_id"),
        sa.CheckConstraint("draw_count >= 0", name="ck_nba_research_draw_count"),
        sa.CheckConstraint(
            "elo_win_probability_home IS NULL OR "
            "(elo_win_probability_home >= 0 AND elo_win_probability_home <= 1)",
            name="ck_nba_research_home_probability_is_a_fraction"),
        sa.CheckConstraint(
            "elo_win_probability_away IS NULL OR "
            "(elo_win_probability_away >= 0 AND elo_win_probability_away <= 1)",
            name="ck_nba_research_away_probability_is_a_fraction"),
    )
    op.create_index("ix_nba_research_forecast_game",
                    "nba_research_forecasts", ["canonical_game_id"])
    op.create_index("ix_nba_research_forecast_source_game",
                    "nba_research_forecasts", ["source_game_id"])


def downgrade() -> None:
    op.drop_index("ix_nba_research_forecast_source_game",
                  table_name="nba_research_forecasts")
    op.drop_index("ix_nba_research_forecast_game",
                  table_name="nba_research_forecasts")
    op.drop_table("nba_research_forecasts")
