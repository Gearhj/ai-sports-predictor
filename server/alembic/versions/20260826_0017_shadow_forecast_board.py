"""Model Studio forecast-exchange shadow board tables.

Revision ID: 20260826_0017
Revises: 20260818_0016
Create Date: 2026-08-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260826_0017"
down_revision = "20260818_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shadow_forecast_bundles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("bundle_sha256", sa.String(64), nullable=False),
        sa.Column("bundle_id", sa.String(160), nullable=False),
        sa.Column("logical_uri", sa.Text(), nullable=False),
        sa.Column("source_fixture_id", sa.String(200), nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("league", sa.String(16), nullable=False),
        sa.Column("sport", sa.String(24), nullable=False),
        sa.Column("status", sa.String(48), nullable=False),
        sa.Column("research_shadow_only", sa.Boolean(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_count", sa.Integer(), nullable=False),
        sa.Column("quote_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_shadow_forecast_bundles_bundle_sha256", "shadow_forecast_bundles", ["bundle_sha256"], unique=True)
    op.create_index("ix_shadow_forecast_bundles_slate_date", "shadow_forecast_bundles", ["slate_date"])

    op.create_table(
        "shadow_forecasts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("bundle_id", sa.Uuid(), sa.ForeignKey("shadow_forecast_bundles.id"), nullable=False),
        sa.Column("forecast_id", sa.String(120), nullable=False),
        sa.Column("engine_id", sa.String(32), nullable=False),
        sa.Column("engine_version", sa.String(64), nullable=False),
        sa.Column("model_artifact_sha256", sa.String(64), nullable=False),
        sa.Column("simulator_id", sa.String(64), nullable=False),
        sa.Column("simulator_version", sa.String(64), nullable=False),
        sa.Column("simulator_seed", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.String(120), nullable=False),
        sa.Column("source_event_id", sa.String(120), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("home_team_id", sa.String(120), nullable=False),
        sa.Column("away_team_id", sa.String(120), nullable=False),
        sa.Column("is_ready", sa.Boolean(), nullable=False),
        sa.Column("readiness_score", sa.Float(), nullable=False),
        sa.Column("readiness_threshold", sa.Float(), nullable=False),
        sa.Column("status", sa.String(48), nullable=False),
        sa.Column("scheduled_start_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("forecast_cutoff_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("full_home_win_probability", sa.Float(), nullable=False),
        sa.Column("full_away_win_probability", sa.Float(), nullable=False),
        sa.Column("full_expected_home_score", sa.Float(), nullable=False),
        sa.Column("full_expected_away_score", sa.Float(), nullable=False),
        sa.Column("f5_home_win_probability", sa.Float(), nullable=False),
        sa.Column("f5_away_win_probability", sa.Float(), nullable=False),
        sa.Column("f5_expected_home_score", sa.Float(), nullable=False),
        sa.Column("f5_expected_away_score", sa.Float(), nullable=False),
        sa.Column("distributions", sa.JSON(), nullable=False),
        sa.Column("uncertainty", sa.JSON(), nullable=False),
        sa.Column("readiness_detail", sa.JSON(), nullable=False),
    )
    op.create_index("ix_shadow_forecasts_bundle_id", "shadow_forecasts", ["bundle_id"])
    op.create_index("ix_shadow_forecasts_forecast_id", "shadow_forecasts", ["forecast_id"], unique=True)
    op.create_index("ix_shadow_forecasts_event_id", "shadow_forecasts", ["event_id"])
    op.create_index("ix_shadow_forecasts_event_engine", "shadow_forecasts", ["event_id", "engine_id"])

    op.create_table(
        "shadow_board_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("shadow_forecast_id", sa.Uuid(), sa.ForeignKey("shadow_forecasts.id"), nullable=False),
        sa.Column("bundle_id", sa.Uuid(), sa.ForeignKey("shadow_forecast_bundles.id"), nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("engine_id", sa.String(32), nullable=False),
        sa.Column("event_id", sa.String(120), nullable=False),
        sa.Column("market", sa.String(40), nullable=False),
        sa.Column("selection", sa.String(40), nullable=False),
        sa.Column("model_probability", sa.Float(), nullable=True),
        sa.Column("has_quote", sa.Boolean(), nullable=False),
        sa.Column("book", sa.String(80), nullable=True),
        sa.Column("line", sa.Float(), nullable=True),
        sa.Column("side_american", sa.Integer(), nullable=True),
        sa.Column("opposite_american", sa.Integer(), nullable=True),
        sa.Column("best_american", sa.Integer(), nullable=True),
        sa.Column("best_decimal", sa.Float(), nullable=True),
        sa.Column("quote_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("break_even_probability", sa.Float(), nullable=True),
        sa.Column("no_vig_probability", sa.Float(), nullable=True),
        sa.Column("raw_edge", sa.Float(), nullable=True),
        sa.Column("no_vig_edge", sa.Float(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("uncertainty_adjusted_edge", sa.Float(), nullable=True),
        sa.Column("recommendation", sa.String(16), nullable=False),
        sa.Column("virtual_stake", sa.Numeric(12, 2), nullable=False),
        sa.Column("quote_signature", sa.String(80), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "shadow_forecast_id", "market", "selection", "quote_signature",
            name="uq_shadow_decision_idempotent",
        ),
    )
    op.create_index("ix_shadow_board_decisions_shadow_forecast_id", "shadow_board_decisions", ["shadow_forecast_id"])
    op.create_index("ix_shadow_board_decisions_bundle_id", "shadow_board_decisions", ["bundle_id"])
    op.create_index("ix_shadow_board_decisions_slate_date", "shadow_board_decisions", ["slate_date"])
    op.create_index("ix_shadow_board_decisions_event_id", "shadow_board_decisions", ["event_id"])

    op.create_table(
        "shadow_decision_settlements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), sa.ForeignKey("shadow_board_decisions.id"), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("closing_american", sa.Integer(), nullable=True),
        sa.Column("closing_decimal", sa.Float(), nullable=True),
        sa.Column("closing_line_value", sa.Float(), nullable=True),
        sa.Column("virtual_profit_loss", sa.Numeric(12, 2), nullable=False),
        sa.Column("settlement_note", sa.String(200), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_shadow_decision_settlements_decision_id", "shadow_decision_settlements", ["decision_id"], unique=True)


def downgrade() -> None:
    op.drop_table("shadow_decision_settlements")
    op.drop_table("shadow_board_decisions")
    op.drop_table("shadow_forecasts")
    op.drop_table("shadow_forecast_bundles")
