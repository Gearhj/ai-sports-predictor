"""$1,000 forward virtual-bankroll experiment: immutable start record, phased
decisions bound to exact prices, and idempotent settlement with bankroll
before/after and closing-line value.

Revision ID: 20260827_0020
Revises: 20260826_0019
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260827_0020"
down_revision = "20260826_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shadow_experiments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("starting_bankroll", sa.Numeric(12, 2), nullable=False),
        sa.Column("decision_policy", sa.String(80), nullable=False),
        sa.Column("controlling_engine", sa.String(40), nullable=False),
        sa.Column("supported_markets", sa.JSON(), nullable=False),
        sa.Column("kelly_fraction", sa.Float(), nullable=False),
        sa.Column("unit_percentage", sa.Float(), nullable=False),
        sa.Column("max_position_percentage", sa.Float(), nullable=False),
        sa.Column("max_same_game_percentage", sa.Float(), nullable=False),
        sa.Column("max_daily_percentage", sa.Float(), nullable=False),
        sa.Column("code_commit", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("version", name="uq_shadow_experiment_version"),
    )

    op.create_table(
        "shadow_experiment_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("shadow_experiments.id"), nullable=False),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column(
            "phase",
            sa.Enum("MORNING", "LINEUP_CONFIRMED", "FINAL_PREGAME",
                    name="experimentdecisionphase", native_enum=False,
                    create_constraint=True, validate_strings=True),
            nullable=False,
        ),
        sa.Column("sport", sa.String(24), nullable=False),
        sa.Column("market", sa.String(40), nullable=False),
        sa.Column("period", sa.String(40), nullable=False),
        sa.Column("selection", sa.String(80), nullable=False),
        sa.Column("line", sa.Float(), nullable=True),
        sa.Column("sportsbook", sa.String(80), nullable=True),
        sa.Column("american_odds", sa.Integer(), nullable=True),
        sa.Column("decimal_odds", sa.Float(), nullable=True),
        sa.Column("market_outcome_id", sa.Uuid(), sa.ForeignKey("market_outcomes.id"), nullable=True),
        sa.Column("odds_snapshot_id", sa.Uuid(), sa.ForeignKey("odds_snapshots.id"), nullable=True),
        sa.Column("quote_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("forecast_id", sa.Uuid(), sa.ForeignKey("forecasts.id"), nullable=True),
        sa.Column("forecast_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("forecast_version", sa.String(40), nullable=True),
        sa.Column("recommended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_probability", sa.Float(), nullable=True),
        sa.Column("no_vig_probability", sa.Float(), nullable=True),
        sa.Column("edge", sa.Float(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("readiness", sa.Float(), nullable=True),
        sa.Column("uncertainty", sa.Float(), nullable=True),
        sa.Column("recommendation", sa.String(24), nullable=False),
        sa.Column("virtual_stake", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("source_mode", sa.String(16), nullable=False),
        sa.UniqueConstraint("experiment_id", "game_id", "market", "phase", name="uq_experiment_decision_phase"),
    )
    op.create_index("ix_shadow_experiment_decisions_experiment_id", "shadow_experiment_decisions", ["experiment_id"])
    op.create_index("ix_shadow_experiment_decisions_game_id", "shadow_experiment_decisions", ["game_id"])
    op.create_index("ix_shadow_experiment_decisions_slate_date", "shadow_experiment_decisions", ["slate_date"])
    op.create_index("ix_experiment_decision_experiment_slate", "shadow_experiment_decisions", ["experiment_id", "slate_date"])

    op.create_table(
        "shadow_experiment_settlements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), sa.ForeignKey("shadow_experiment_decisions.id"), nullable=False),
        sa.Column("result", sa.String(8), nullable=False),
        sa.Column("profit_loss", sa.Numeric(12, 2), nullable=False),
        sa.Column("bankroll_before", sa.Numeric(12, 2), nullable=False),
        sa.Column("bankroll_after", sa.Numeric(12, 2), nullable=False),
        sa.Column("closing_american", sa.Integer(), nullable=True),
        sa.Column("closing_decimal", sa.Float(), nullable=True),
        sa.Column("closing_line_value", sa.Float(), nullable=True),
        sa.Column("beat_close", sa.Boolean(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("decision_id", name="uq_experiment_settlement_decision"),
    )


def downgrade() -> None:
    op.drop_table("shadow_experiment_settlements")
    op.drop_table("shadow_experiment_decisions")
    op.drop_table("shadow_experiments")
