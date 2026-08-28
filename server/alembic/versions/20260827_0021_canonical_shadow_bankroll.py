"""Bind the forward shadow experiment to the canonical position ledger.

Revision ID: 20260827_0021
Revises: 20260827_0020
Create Date: 2026-08-27

The 0020 revision has not been changed so an installation that observed it can
still advance normally. Its unused settlement table is removed only when empty;
all authority is moved to official_asp_positions and position_results.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260827_0021"
down_revision = "20260827_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0020 created a parallel settlement table, but no code may migrate records
    # out of it because that would make them compete with the canonical ledger.
    # Fail closed if an installation ever wrote to it; otherwise remove it.
    legacy_count = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM shadow_experiment_settlements")
    ).scalar_one()
    if legacy_count:
        raise RuntimeError(
            "shadow_experiment_settlements is non-empty; canonical-ledger migration refused"
        )
    legacy_decision_authority = op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM shadow_experiment_decisions "
        "WHERE is_primary = TRUE OR virtual_stake <> 0"
    )).scalar_one()
    if legacy_decision_authority:
        raise RuntimeError(
            "shadow_experiment_decisions contains position authority; canonical migration refused"
        )
    op.drop_table("shadow_experiment_settlements")

    op.add_column("shadow_experiments", sa.Column(
        "name", sa.String(120), nullable=False,
        server_default="ASP MODEL BANKROLL — 2026 V1",
    ))
    op.add_column("shadow_experiments", sa.Column(
        "sport", sa.String(24), nullable=False, server_default="MLB",
    ))
    op.add_column("shadow_experiments", sa.Column(
        "recommendation_policy_version", sa.String(80), nullable=False,
        server_default="AISP_OPPORTUNITY_POLICY_V1",
    ))
    op.add_column("shadow_experiments", sa.Column(
        "staking_policy_version", sa.String(80), nullable=False,
        server_default="AISP_QUARTER_KELLY_1_2_5_V1",
    ))
    op.add_column("shadow_experiments", sa.Column(
        "code_identity", sa.String(128), nullable=False, server_default="git:unknown",
    ))

    op.add_column("shadow_experiment_decisions", sa.Column("forecast_cutoff", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shadow_experiment_decisions", sa.Column("recommendation_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column("shadow_experiment_decisions", sa.Column("qualification_reasons", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("shadow_experiment_decisions", sa.Column("evidence_fingerprint", sa.String(64), nullable=True))
    op.drop_constraint("uq_experiment_decision_phase", "shadow_experiment_decisions", type_="unique")
    op.drop_column("shadow_experiment_decisions", "is_primary")
    op.drop_column("shadow_experiment_decisions", "virtual_stake")
    op.create_index(
        "ix_shadow_experiment_decision_fingerprint",
        "shadow_experiment_decisions", ["evidence_fingerprint"], unique=True,
    )

    op.add_column("official_asp_positions", sa.Column("shadow_experiment_id", sa.Uuid(), nullable=True))
    op.add_column("official_asp_positions", sa.Column("shadow_decision_id", sa.Uuid(), nullable=True))
    op.add_column("official_asp_positions", sa.Column("bankroll_before_position", sa.Numeric(12, 2), nullable=True))
    op.add_column("official_asp_positions", sa.Column("recommendation_policy_version", sa.String(80), nullable=True))
    op.add_column("official_asp_positions", sa.Column("staking_policy_version", sa.String(80), nullable=True))
    op.create_foreign_key(
        "fk_official_position_shadow_experiment", "official_asp_positions",
        "shadow_experiments", ["shadow_experiment_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_official_position_shadow_decision", "official_asp_positions",
        "shadow_experiment_decisions", ["shadow_decision_id"], ["id"],
    )
    op.create_index("ix_official_position_shadow_experiment_id", "official_asp_positions", ["shadow_experiment_id"])
    op.create_index("ix_official_position_shadow_decision_id", "official_asp_positions", ["shadow_decision_id"], unique=True)
    op.create_unique_constraint(
        "uq_official_position_experiment_outcome", "official_asp_positions",
        ["shadow_experiment_id", "market_outcome_id"],
    )

    op.add_column("position_results", sa.Column("bankroll_before", sa.Numeric(12, 2), nullable=True))
    op.add_column("position_results", sa.Column("bankroll_after", sa.Numeric(12, 2), nullable=True))
    op.add_column("position_results", sa.Column("closing_odds_snapshot_id", sa.Uuid(), nullable=True))
    op.add_column("position_results", sa.Column("closing_american_odds", sa.Integer(), nullable=True))
    op.add_column("position_results", sa.Column("closing_decimal_odds", sa.Float(), nullable=True))
    op.add_column("position_results", sa.Column("implied_probability_movement", sa.Float(), nullable=True))
    op.add_column("position_results", sa.Column("closing_line_value", sa.Float(), nullable=True))
    op.add_column("position_results", sa.Column("beat_close", sa.Boolean(), nullable=True))
    op.create_foreign_key(
        "fk_position_result_closing_snapshot", "position_results",
        "odds_snapshots", ["closing_odds_snapshot_id"], ["id"],
    )


def downgrade() -> None:
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
    op.drop_constraint("fk_position_result_closing_snapshot", "position_results", type_="foreignkey")
    for column in (
        "beat_close", "closing_line_value", "implied_probability_movement", "closing_decimal_odds",
        "closing_american_odds", "closing_odds_snapshot_id",
        "bankroll_after", "bankroll_before",
    ):
        op.drop_column("position_results", column)

    op.drop_constraint("uq_official_position_experiment_outcome", "official_asp_positions", type_="unique")
    op.drop_index("ix_official_position_shadow_decision_id", table_name="official_asp_positions")
    op.drop_index("ix_official_position_shadow_experiment_id", table_name="official_asp_positions")
    op.drop_constraint("fk_official_position_shadow_decision", "official_asp_positions", type_="foreignkey")
    op.drop_constraint("fk_official_position_shadow_experiment", "official_asp_positions", type_="foreignkey")
    for column in (
        "staking_policy_version", "recommendation_policy_version",
        "bankroll_before_position", "shadow_decision_id", "shadow_experiment_id",
    ):
        op.drop_column("official_asp_positions", column)

    op.drop_index("ix_shadow_experiment_decision_fingerprint", table_name="shadow_experiment_decisions")
    op.add_column("shadow_experiment_decisions", sa.Column(
        "virtual_stake", sa.Numeric(12, 2), nullable=False, server_default="0",
    ))
    op.add_column("shadow_experiment_decisions", sa.Column(
        "is_primary", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))
    op.create_unique_constraint(
        "uq_experiment_decision_phase", "shadow_experiment_decisions",
        ["experiment_id", "game_id", "market", "phase"],
    )
    for column in (
        "evidence_fingerprint", "qualification_reasons",
        "recommendation_reason", "forecast_cutoff",
    ):
        op.drop_column("shadow_experiment_decisions", column)
    for column in (
        "code_identity", "staking_policy_version",
        "recommendation_policy_version", "sport", "name",
    ):
        op.drop_column("shadow_experiments", column)
