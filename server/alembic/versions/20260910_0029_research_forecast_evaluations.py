"""append-only research forecast evaluations (prospective outcome scoring)

Revision ID: 20260910_0029
Revises: 20260909_0028
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260910_0029"
down_revision = "20260909_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_forecast_evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sport", sa.String(24), nullable=False),
        sa.Column("league_code", sa.String(24), nullable=False),
        sa.Column("event_id", sa.String(120), nullable=False),
        sa.Column("canonical_game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=True),
        sa.Column("engine_id", sa.String(64), nullable=False),
        sa.Column("engine_version", sa.String(120), nullable=False),
        sa.Column("model_label", sa.String(160), nullable=True),
        sa.Column("forecast_identity", sa.String(200), nullable=False),
        sa.Column("shadow_forecast_id", sa.Uuid(), sa.ForeignKey("shadow_forecasts.id"), nullable=True),
        sa.Column("snapshot_type", sa.String(32), nullable=True),
        sa.Column("snapshot_version", sa.Integer(), nullable=True),
        sa.Column("forecast_published_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("forecast_evidence_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("forecast_imported_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kickoff_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_before_kickoff_seconds", sa.Integer(), nullable=True),
        sa.Column("result_identity", sa.String(200), nullable=False),
        sa.Column("result_source", sa.String(64), nullable=False),
        sa.Column("result_observed_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("home_score", sa.Integer(), nullable=False),
        sa.Column("away_score", sa.Integer(), nullable=False),
        sa.Column("actual_outcome", sa.String(8), nullable=False),
        sa.Column("predicted_outcome", sa.String(8), nullable=False),
        sa.Column("winner_correct", sa.Boolean(), nullable=False),
        sa.Column("probability_of_actual", sa.Float(), nullable=False),
        sa.Column("log_loss", sa.Float(), nullable=False),
        sa.Column("brier_score", sa.Float(), nullable=False),
        sa.Column("predicted_home_score", sa.Float(), nullable=True),
        sa.Column("predicted_away_score", sa.Float(), nullable=True),
        sa.Column("home_score_error", sa.Float(), nullable=True),
        sa.Column("away_score_error", sa.Float(), nullable=True),
        sa.Column("team_score_mae", sa.Float(), nullable=True),
        sa.Column("predicted_margin", sa.Float(), nullable=True),
        sa.Column("actual_margin", sa.Integer(), nullable=False),
        sa.Column("margin_error", sa.Float(), nullable=True),
        sa.Column("predicted_total", sa.Float(), nullable=True),
        sa.Column("actual_total", sa.Integer(), nullable=False),
        sa.Column("total_error", sa.Float(), nullable=True),
        sa.Column("distribution_available", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("distribution_unavailable_reason", sa.Text(), nullable=True),
        sa.Column("distribution_draws", sa.Integer(), nullable=True),
        sa.Column("crps_home", sa.Float(), nullable=True),
        sa.Column("crps_away", sa.Float(), nullable=True),
        sa.Column("crps_margin", sa.Float(), nullable=True),
        sa.Column("crps_total", sa.Float(), nullable=True),
        sa.Column("energy_score", sa.Float(), nullable=True),
        sa.Column("interval_coverage", sa.JSON(), nullable=True),
        sa.Column("market_available", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("market_unavailable_reason", sa.Text(), nullable=True),
        sa.Column("market_evidence", sa.JSON(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("scoring_version", sa.String(48), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "event_id", "engine_id", "engine_version", "forecast_identity", "result_identity",
            name="uq_research_forecast_evaluation_identity",
        ),
        sa.CheckConstraint(
            "published_before_kickoff_seconds IS NULL OR published_before_kickoff_seconds >= 0",
            name="ck_research_forecast_eval_prospective",
        ),
    )
    op.create_index("ix_research_forecast_evaluations_sport", "research_forecast_evaluations", ["sport"])
    op.create_index("ix_research_forecast_evaluations_league_code", "research_forecast_evaluations", ["league_code"])
    op.create_index("ix_research_forecast_evaluations_event_id", "research_forecast_evaluations", ["event_id"])
    op.create_index("ix_research_forecast_evaluations_engine_id", "research_forecast_evaluations", ["engine_id"])
    op.create_index("ix_research_forecast_evaluations_canonical_game_id", "research_forecast_evaluations", ["canonical_game_id"])
    op.create_index("ix_research_forecast_evaluations_shadow_forecast_id", "research_forecast_evaluations", ["shadow_forecast_id"])
    op.create_index(
        "ix_research_forecast_eval_league_engine",
        "research_forecast_evaluations", ["league_code", "engine_id"],
    )


def downgrade() -> None:
    op.drop_table("research_forecast_evaluations")
