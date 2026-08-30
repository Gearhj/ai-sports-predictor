"""Add prospective sport-neutral lineage to the existing shadow ledger.

Revision ID: 20260829_0022
Revises: 20260827_0021
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260829_0022"
down_revision = "20260827_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shadow_forecast_bundles",
        sa.Column(
            "contract_version", sa.String(64), nullable=False,
            server_default="AISP_DAILY_FORECAST_BUNDLE_V1",
        ),
    )
    for column in (
        sa.Column("canonical_game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=True),
        sa.Column("forecast_schema_version", sa.String(64), nullable=False, server_default="AISP_GAME_FORECAST_EXPORT_V1"),
        sa.Column("snapshot_type", sa.String(24), nullable=False, server_default="HISTORICAL"),
        sa.Column("snapshot_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("model_manifest_sha256", sa.String(64), nullable=True),
        sa.Column("feature_vector_sha256", sa.String(64), nullable=True),
        sa.Column("season_type", sa.String(16), nullable=True),
        sa.Column("week", sa.Integer(), nullable=True),
        sa.Column("schedule_as_of_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_cutoff_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_as_of_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("identity_evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("lineage_detail", sa.JSON(), nullable=False, server_default="{}"),
    ):
        op.add_column("shadow_forecasts", column)
    op.create_index("ix_shadow_forecasts_canonical_game_id", "shadow_forecasts", ["canonical_game_id"])
    for name in (
        "f5_home_win_probability", "f5_away_win_probability",
        "f5_expected_home_score", "f5_expected_away_score",
    ):
        op.alter_column("shadow_forecasts", name, existing_type=sa.Float(), nullable=True)
    op.add_column("shadow_board_decisions", sa.Column("push_probability", sa.Float(), nullable=True))
    op.add_column("shadow_board_decisions", sa.Column("odds_snapshot_id", sa.Uuid(), sa.ForeignKey("odds_snapshots.id"), nullable=True))
    op.add_column("shadow_board_decisions", sa.Column("opposite_odds_snapshot_id", sa.Uuid(), sa.ForeignKey("odds_snapshots.id"), nullable=True))
    op.add_column("shadow_board_decisions", sa.Column("source_quote_event_id", sa.String(255), nullable=True))
    op.add_column("shadow_board_decisions", sa.Column("quote_available_as_of", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_shadow_decisions_odds_snapshot_id", "shadow_board_decisions", ["odds_snapshot_id"])
    op.create_index("ix_shadow_decisions_opposite_odds_snapshot_id", "shadow_board_decisions", ["opposite_odds_snapshot_id"])
    op.add_column("shadow_decision_settlements", sa.Column("performance_detail", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("shadow_decision_settlements", "performance_detail")
    op.drop_index("ix_shadow_decisions_opposite_odds_snapshot_id", table_name="shadow_board_decisions")
    op.drop_index("ix_shadow_decisions_odds_snapshot_id", table_name="shadow_board_decisions")
    for name in ("quote_available_as_of", "source_quote_event_id", "opposite_odds_snapshot_id", "odds_snapshot_id"):
        op.drop_column("shadow_board_decisions", name)
    op.drop_column("shadow_board_decisions", "push_probability")
    op.drop_index("ix_shadow_forecasts_canonical_game_id", table_name="shadow_forecasts")
    for name in (
        "f5_home_win_probability", "f5_away_win_probability",
        "f5_expected_home_score", "f5_expected_away_score",
    ):
        op.alter_column("shadow_forecasts", name, existing_type=sa.Float(), nullable=False)
    for name in (
        "lineage_detail", "identity_evidence", "available_as_of_utc",
        "source_cutoff_utc", "schedule_as_of_utc", "week", "season_type",
        "feature_vector_sha256", "model_manifest_sha256", "snapshot_version",
        "snapshot_type", "forecast_schema_version", "canonical_game_id",
    ):
        op.drop_column("shadow_forecasts", name)
    op.drop_column("shadow_forecast_bundles", "contract_version")
