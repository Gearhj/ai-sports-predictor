"""Add compact append-only historical evidence-state attestations.

Revision ID: 20260814_0008
Revises: 20260814_0007
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260814_0008"
down_revision = "20260814_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("historical_feature_materializations") as batch_op:
        batch_op.drop_constraint(
            "uq_hist_materialization_semantic",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_hist_materialization_semantic",
            [
                "game_id",
                "cutoff_at",
                "feature_version_id",
                "cutoff_policy_version",
                "source_mode",
                "materializer_version",
                "evidence_manifest_checksum",
            ],
        )
    op.create_table(
        "historical_evidence_state_attestations",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("feature_version_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_version", sa.String(length=80), nullable=False),
        sa.Column("population", sa.String(length=40), nullable=False),
        sa.Column("outcome_history_count", sa.Integer(), nullable=False),
        sa.Column("team_history_count", sa.Integer(), nullable=False),
        sa.Column("player_history_count", sa.Integer(), nullable=False),
        sa.Column("statcast_event_count", sa.Integer(), nullable=False),
        sa.Column("evidence_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eligible_max_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("family_counts", sa.JSON(), nullable=False),
        sa.Column("family_checksums", sa.JSON(), nullable=False),
        sa.Column("source_bundle", sa.JSON(), nullable=False),
        sa.Column("source_bundle_checksum", sa.String(length=64), nullable=False),
        sa.Column("canonical_set_checksum", sa.String(length=64), nullable=False),
        sa.Column("feature_values_checksum", sa.String(length=64), nullable=False),
        sa.Column("target_excluded", sa.Boolean(), nullable=False),
        sa.Column("temporal_valid", sa.Boolean(), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("temporal_semantics", sa.String(length=40), nullable=False),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome_history_count >= 0 AND team_history_count >= 0 "
            "AND player_history_count >= 0 "
            "AND statcast_event_count >= 0",
            name="ck_hist_evidence_state_counts",
        ),
        sa.CheckConstraint(
            "generated_at >= cutoff_at",
            name="ck_hist_evidence_state_generation",
        ),
        sa.CheckConstraint(
            "((outcome_history_count + team_history_count + player_history_count "
            "+ statcast_event_count = 0 "
            "AND evidence_start_at IS NULL AND evidence_end_at IS NULL "
            "AND eligible_max_at IS NULL) OR "
            "(outcome_history_count + team_history_count + player_history_count "
            "+ statcast_event_count > 0 "
            "AND evidence_start_at IS NOT NULL AND evidence_end_at IS NOT NULL "
            "AND eligible_max_at IS NOT NULL))",
            name="ck_hist_evidence_state_bounds_presence",
        ),
        sa.CheckConstraint(
            "evidence_start_at IS NULL OR "
            "(evidence_end_at >= evidence_start_at AND evidence_end_at < cutoff_at "
            "AND eligible_max_at >= evidence_end_at "
            "AND eligible_max_at <= cutoff_at)",
            name="ck_hist_evidence_state_bounds_order",
        ),
        sa.CheckConstraint(
            "population IN ('TIER_A_STRICT', 'TIER_B_ENHANCED')",
            name="ck_hist_evidence_state_population",
        ),
        sa.CheckConstraint(
            "source_mode IN ('LIVE', 'DEMO')",
            name="ck_hist_evidence_state_mode",
        ),
        sa.CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_hist_evidence_state_temporal",
        ),
        sa.CheckConstraint(
            "length(source_bundle_checksum) = 64 "
            "AND length(canonical_set_checksum) = 64 "
            "AND length(feature_values_checksum) = 64 "
            "AND length(identity_checksum) = 64",
            name="ck_hist_evidence_state_checksums",
        ),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["history_backfill_checkpoints.id"]),
        sa.ForeignKeyConstraint(["feature_version_id"], ["feature_versions.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id",
            "cutoff_at",
            "feature_version_id",
            "state_version",
            "population",
            "source_mode",
            "canonical_set_checksum",
            name="uq_hist_evidence_state_semantic",
        ),
        sa.UniqueConstraint(
            "identity_checksum",
            name="uq_hist_evidence_state_identity",
        ),
    )
    op.create_index(
        "ix_hist_evidence_state_game_cutoff",
        "historical_evidence_state_attestations",
        ["game_id", "cutoff_at"],
    )
    op.create_index(
        "ix_hist_evidence_state_checkpoint",
        "historical_evidence_state_attestations",
        ["checkpoint_id"],
    )
    op.create_index(
        "ix_hist_evidence_state_season_population",
        "historical_evidence_state_attestations",
        ["season_id", "population", "generated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hist_evidence_state_season_population",
        table_name="historical_evidence_state_attestations",
    )
    op.drop_index(
        "ix_hist_evidence_state_checkpoint",
        table_name="historical_evidence_state_attestations",
    )
    op.drop_index(
        "ix_hist_evidence_state_game_cutoff",
        table_name="historical_evidence_state_attestations",
    )
    op.drop_table("historical_evidence_state_attestations")
    with op.batch_alter_table("historical_feature_materializations") as batch_op:
        batch_op.drop_constraint(
            "uq_hist_materialization_semantic",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_hist_materialization_semantic",
            [
                "game_id",
                "cutoff_at",
                "feature_version_id",
                "cutoff_policy_version",
                "source_mode",
                "materializer_version",
            ],
        )
