"""V18 conditional champion authority and the forecast-to-decision link.

Three separate things, deliberately in one revision because none of them is
useful alone:

1. ``model_champion_authorities`` — where prospective forecasting authority
   arrives. It arrives here and nowhere else, so a forecast payload can never
   promote its own engine.
2. ``shadow_forecast_id`` on decisions and positions — the existing columns
   point at ``forecasts`` (Gen1). A decision made from a producer forecast had
   nowhere to record which forecast it used, which is exactly the gap that
   would have forced either a silent Gen1 fallback or an unlinked decision.
3. Model-era columns on ``shadow_experiments`` — the era identity, the
   predecessor, and the atomic capital snapshot taken at the boundary.

Nothing here backfills. Every column is nullable, and every existing row keeps
the meaning it already had.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260911_0032"
down_revision = "20260911_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_champion_authorities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("handoff_sha256", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("sport", sa.String(24), nullable=False),
        sa.Column("engine_id", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(80), nullable=False),
        sa.Column("model_version", sa.String(40), nullable=False),
        sa.Column("model_artifact_sha256", sa.String(64), nullable=False),
        sa.Column("champion_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("frozen_bindings_sha256", sa.String(64)),
        sa.Column("parent_bundle_id", sa.String(120)),
        sa.Column("training_run_id", sa.String(120)),
        sa.Column("model_era_id", sa.String(80), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("historical_validation_under_review", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("historical_integrity_note", sa.Text()),
        sa.Column("execution_allowed_in_aisp", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("real_money_execution_allowed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("gen1_fallback_allowed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("position_backfill_allowed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("minimum_adjusted_edge", sa.Float(), nullable=False),
        sa.Column("recommendation_policy_version", sa.String(80), nullable=False),
        sa.Column("staking_policy_version", sa.String(80), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="ARMED"),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("shadow_experiments.id")),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("handoff_sha256", name="uq_champion_authority_handoff"),
    )
    op.create_index("ix_champion_authority_sport", "model_champion_authorities", ["sport"])
    op.create_index("ix_champion_authority_engine", "model_champion_authorities", ["engine_id"])
    op.create_index("ix_champion_authority_era", "model_champion_authorities", ["model_era_id"])
    op.create_index("ix_champion_authority_experiment", "model_champion_authorities", ["experiment_id"])

    op.add_column("shadow_experiment_decisions",
                  sa.Column("shadow_forecast_id", postgresql.UUID(as_uuid=True),
                            sa.ForeignKey("shadow_forecasts.id")))
    op.create_index("ix_experiment_decision_shadow_forecast",
                    "shadow_experiment_decisions", ["shadow_forecast_id"])

    op.add_column("official_asp_positions",
                  sa.Column("shadow_forecast_id", postgresql.UUID(as_uuid=True),
                            sa.ForeignKey("shadow_forecasts.id")))
    op.create_index("ix_official_position_shadow_forecast",
                    "official_asp_positions", ["shadow_forecast_id"])

    op.add_column("shadow_experiments", sa.Column("model_era_id", sa.String(80)))
    op.create_unique_constraint("uq_shadow_experiment_model_era", "shadow_experiments", ["model_era_id"])
    op.add_column("shadow_experiments",
                  sa.Column("predecessor_experiment_id", postgresql.UUID(as_uuid=True),
                            sa.ForeignKey("shadow_experiments.id")))
    op.create_index("ix_shadow_experiment_predecessor", "shadow_experiments", ["predecessor_experiment_id"])
    op.add_column("shadow_experiments", sa.Column("boundary_capital", sa.JSON()))


def downgrade() -> None:
    op.drop_column("shadow_experiments", "boundary_capital")
    op.drop_index("ix_shadow_experiment_predecessor", table_name="shadow_experiments")
    op.drop_column("shadow_experiments", "predecessor_experiment_id")
    op.drop_constraint("uq_shadow_experiment_model_era", "shadow_experiments", type_="unique")
    op.drop_column("shadow_experiments", "model_era_id")
    op.drop_index("ix_official_position_shadow_forecast", table_name="official_asp_positions")
    op.drop_column("official_asp_positions", "shadow_forecast_id")
    op.drop_index("ix_experiment_decision_shadow_forecast", table_name="shadow_experiment_decisions")
    op.drop_column("shadow_experiment_decisions", "shadow_forecast_id")
    op.drop_index("ix_champion_authority_experiment", table_name="model_champion_authorities")
    op.drop_index("ix_champion_authority_era", table_name="model_champion_authorities")
    op.drop_index("ix_champion_authority_engine", table_name="model_champion_authorities")
    op.drop_index("ix_champion_authority_sport", table_name="model_champion_authorities")
    op.drop_table("model_champion_authorities")
