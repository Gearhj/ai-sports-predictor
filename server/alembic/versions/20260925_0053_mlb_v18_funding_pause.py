"""MLB_V18_FUNDING_PAUSE_V1: one immutable pause record per V18 decision

Additive only: one new table with its unique constraint, check constraints and
indexes. No existing table, column or row is touched, and nothing is
back-filled: the table starts empty and is written only prospectively, only
while ``MLB_V18_FUNDING_PAUSE_ENABLED`` is true.

WHY

The V18 two-point edge gate is not supported by the evidence (see the accepted
design, MLB_V18_FUNDING_PAUSE_V1_DESIGN.md, sha256 79a46aa6...). Funding stops;
the decisions do not. An exact-authority V18 FINAL_PREGAME QUALIFIED_BET is
recorded here, before any staking arithmetic, instead of becoming a paper
position. The row carries the decision, forecast, quote and authority identity
and a content hash over all of it -- and no stake, payout, profit, bankroll,
exposure, cap, settlement or outcome field in any form.

UNIQUENESS is on ``decision_id`` alone: one record per decision, ever.

ORDER

Descends from the accepted NFL T-26 migration 20260923_0052 (the production
head at af0b530) so Alembic reports exactly one head with both present.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260925_0053"
down_revision = "20260923_0052"
branch_labels = None
depends_on = None

TABLE = "mlb_v18_funding_pauses"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        # The row's own identity.
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("feature_version", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        # The decision.
        sa.Column("decision_id", sa.Uuid(),
                  sa.ForeignKey("shadow_experiment_decisions.id"), nullable=False),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("decision_phase", sa.String(24), nullable=False),
        sa.Column("decision_recommendation", sa.String(24), nullable=False),
        sa.Column("decision_source_mode", sa.String(16), nullable=False),
        sa.Column("decision_recommended_at", sa.DateTime(timezone=True), nullable=False),
        # The complete frozen authority tuple.
        sa.Column("experiment_id", sa.Uuid(),
                  sa.ForeignKey("shadow_experiments.id"), nullable=False),
        sa.Column("model_era_id", sa.String(80), nullable=False),
        sa.Column("controlling_engine", sa.String(40), nullable=False),
        sa.Column("model_id", sa.String(80), nullable=False),
        sa.Column("model_configuration_identity", sa.String(120), nullable=False),
        sa.Column("model_artifact_sha256", sa.String(64), nullable=False),
        sa.Column("parent_bundle_sha256", sa.String(64), nullable=False),
        sa.Column("recommendation_policy_version", sa.String(80), nullable=False),
        sa.Column("decision_policy_version", sa.String(80), nullable=False),
        sa.Column("staking_policy_version", sa.String(80), nullable=False),
        sa.Column("policy_configuration_sha256", sa.String(64), nullable=False),
        sa.Column("authority_mapping_sha256", sa.String(64), nullable=False),
        # The forecast.
        sa.Column("shadow_forecast_id", sa.Uuid(),
                  sa.ForeignKey("shadow_forecasts.id"), nullable=False),
        sa.Column("forecast_identity", sa.String(120), nullable=False),
        sa.Column("forecast_package_sha256", sa.String(64), nullable=False),
        # The quote, market and side.
        sa.Column("market_id", sa.Uuid(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("market_outcome_id", sa.Uuid(),
                  sa.ForeignKey("market_outcomes.id"), nullable=False),
        sa.Column("odds_snapshot_id", sa.Uuid(),
                  sa.ForeignKey("odds_snapshots.id"), nullable=False),
        sa.Column("sportsbook", sa.String(80), nullable=False),
        sa.Column("market_type", sa.String(40), nullable=False),
        sa.Column("selection", sa.String(80), nullable=False),
        sa.Column("american_odds", sa.Integer(), nullable=False),
        sa.Column("decimal_odds", sa.Float(), nullable=False),
        # What made it qualify, as recorded.
        sa.Column("model_probability", sa.Float(), nullable=False),
        sa.Column("market_probability", sa.Float(), nullable=False),
        sa.Column("edge", sa.Float(), nullable=False),
        sa.Column("policy_adjusted_edge", sa.Float(), nullable=False),
        sa.Column("qualified_edge_threshold", sa.Float(), nullable=False),
        # The clocks.
        sa.Column("quote_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checkpoint_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=False),
        # The disposition.
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("validation_status", sa.String(64), nullable=False),
        sa.UniqueConstraint("decision_id", name="uq_mlb_v18_funding_pause_decision"),
        sa.CheckConstraint("reason_code = 'FUNDING_EDGE_NOT_ESTABLISHED'",
                           name="ck_mlb_v18_funding_pause_reason"),
        sa.CheckConstraint("validation_status = 'PENDING_PROSPECTIVE_VALIDATION'",
                           name="ck_mlb_v18_funding_pause_status"),
        sa.CheckConstraint("decision_phase = 'FINAL_PREGAME'",
                           name="ck_mlb_v18_funding_pause_phase"),
        sa.CheckConstraint("decision_recommendation = 'QUALIFIED_BET'",
                           name="ck_mlb_v18_funding_pause_recommendation"),
        sa.CheckConstraint("decision_source_mode = 'LIVE'",
                           name="ck_mlb_v18_funding_pause_source_mode"),
    )
    op.create_index("ix_mlb_v18_funding_pauses_game_id", TABLE, ["game_id"])
    op.create_index("ix_mlb_v18_funding_pauses_experiment_id", TABLE, ["experiment_id"])


def downgrade() -> None:
    op.drop_index("ix_mlb_v18_funding_pauses_experiment_id", table_name=TABLE)
    op.drop_index("ix_mlb_v18_funding_pauses_game_id", table_name=TABLE)
    op.drop_table(TABLE)
