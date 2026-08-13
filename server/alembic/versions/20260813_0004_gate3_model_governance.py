"""Add Gate 3 training-dataset and model-evaluation governance.

Revision ID: 20260813_0004
Revises: 20260812_0003
Create Date: 2026-08-13

The new governance records are append-only at the application layer. Existing
Gate 2 model rows remain valid because every added model-version field is
nullable; new candidate records can opt into the complete identity contract.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260813_0004"
down_revision = "20260812_0003"
branch_labels = None
depends_on = None


_OLD_MODEL_STATUS = sa.Enum(
    "DEVELOPMENT",
    "CHALLENGER",
    "CHAMPION",
    "RETIRED",
    name="modelstatus",
    native_enum=False,
    create_constraint=True,
)
_NEW_MODEL_STATUS = sa.Enum(
    "DEVELOPMENT",
    "CANDIDATE",
    "CHALLENGER",
    "CHAMPION",
    "RETIRED",
    name="modelstatus",
    native_enum=False,
    create_constraint=True,
)


def _model_version_batch() -> object:
    # SQLite needs a table copy to alter its varchar-backed enum constraint;
    # PostgreSQL can use ordinary ALTER operations and must retain inbound FKs.
    recreate = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    return op.batch_alter_table("model_versions", recreate=recreate)


def upgrade() -> None:
    op.create_table(
        "training_dataset_manifests",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("sport_id", sa.Uuid(), nullable=True),
        sa.Column("feature_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("target", sa.String(length=80), nullable=False),
        sa.Column("target_definition", sa.JSON(), nullable=False),
        sa.Column("cutoff_policy", sa.JSON(), nullable=False),
        sa.Column("split_policy", sa.JSON(), nullable=False),
        sa.Column("feature_names", sa.JSON(), nullable=False),
        sa.Column("seasons", sa.JSON(), nullable=False),
        sa.Column("event_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("game_count", sa.Integer(), nullable=False),
        sa.Column("excluded_row_count", sa.Integer(), nullable=False),
        sa.Column("artifact_uri", sa.String(length=500), nullable=False),
        sa.Column("artifact_checksum", sa.String(length=64), nullable=False),
        sa.Column("source_evidence", sa.JSON(), nullable=False),
        sa.Column("quality_summary", sa.JSON(), nullable=True),
        sa.Column("code_revision", sa.String(length=120), nullable=False),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_training_dataset_source_mode"),
        sa.CheckConstraint("event_end_at >= event_start_at", name="ck_training_dataset_event_window"),
        sa.CheckConstraint("row_count >= 0", name="ck_training_dataset_row_count"),
        sa.CheckConstraint("game_count >= 0", name="ck_training_dataset_game_count"),
        sa.CheckConstraint("excluded_row_count >= 0", name="ck_training_dataset_excluded_count"),
        sa.ForeignKeyConstraint(["feature_version_id"], ["feature_versions.id"]),
        sa.ForeignKeyConstraint(["sport_id"], ["sports.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_training_dataset_name_version"),
        sa.UniqueConstraint("identity_checksum", name="uq_training_dataset_identity_checksum"),
    )
    op.create_index(
        "ix_training_dataset_manifests_feature_version_id",
        "training_dataset_manifests",
        ["feature_version_id"],
    )
    op.create_index(
        "ix_training_dataset_manifests_sport_id",
        "training_dataset_manifests",
        ["sport_id"],
    )
    op.create_index(
        "ix_training_dataset_event_window",
        "training_dataset_manifests",
        ["event_start_at", "event_end_at"],
    )

    with _model_version_batch() as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=_OLD_MODEL_STATUS,
            type_=_NEW_MODEL_STATUS,
            existing_nullable=False,
        )
        batch_op.add_column(sa.Column("target", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("training_manifest_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("validation_window", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("holdout_window", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("calibrator_uri", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("calibrator_checksum", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("ensemble_weights", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("code_revision", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("identity_checksum", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("feature_names", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("seed_policy", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("target_definition", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("promotion_decision", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("promotion_reason", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_model_versions_training_manifest",
            "training_dataset_manifests",
            ["training_manifest_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_model_version_identity_checksum",
            ["identity_checksum"],
        )
        batch_op.create_check_constraint(
            "ck_model_version_calibrator_reference",
            "calibrator_checksum IS NULL OR calibrator_uri IS NOT NULL",
        )
        batch_op.create_index(
            "ix_model_versions_training_manifest_id",
            ["training_manifest_id"],
        )

    op.create_table(
        "model_evaluations",
        sa.Column("model_version_id", sa.Uuid(), nullable=False),
        sa.Column("training_manifest_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_model_version_id", sa.Uuid(), nullable=True),
        sa.Column("evaluation_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("validation_window", sa.JSON(), nullable=True),
        sa.Column("holdout_window", sa.JSON(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("baseline_metrics", sa.JSON(), nullable=True),
        sa.Column("slice_metrics", sa.JSON(), nullable=True),
        sa.Column("calibration", sa.JSON(), nullable=True),
        sa.Column("prediction_artifact_uri", sa.String(length=500), nullable=True),
        sa.Column("prediction_artifact_checksum", sa.String(length=64), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("code_revision", sa.String(length=120), nullable=False),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("superiority_claim", sa.Boolean(), nullable=False),
        sa.Column("promotion_recommendation", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_model_evaluation_source_mode"),
        sa.CheckConstraint(
            "prediction_artifact_checksum IS NULL OR prediction_artifact_uri IS NOT NULL",
            name="ck_model_evaluation_prediction_reference",
        ),
        sa.ForeignKeyConstraint(["baseline_model_version_id"], ["model_versions.id"]),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
        sa.ForeignKeyConstraint(["training_manifest_id"], ["training_dataset_manifests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_checksum", name="uq_model_evaluation_identity_checksum"),
    )
    op.create_index(
        "ix_model_evaluation_model_evaluated",
        "model_evaluations",
        ["model_version_id", "evaluated_at"],
    )
    op.create_index(
        "ix_model_evaluation_manifest_evaluated",
        "model_evaluations",
        ["training_manifest_id", "evaluated_at"],
    )
    op.create_index("ix_model_evaluation_status", "model_evaluations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_model_evaluation_status", table_name="model_evaluations")
    op.drop_index("ix_model_evaluation_manifest_evaluated", table_name="model_evaluations")
    op.drop_index("ix_model_evaluation_model_evaluated", table_name="model_evaluations")
    op.drop_table("model_evaluations")

    # Candidate is a Gate 3-only lifecycle state. Preserve its pre-promotion
    # meaning as CHALLENGER before restoring the narrower Gate 2 constraint.
    op.execute(sa.text("UPDATE model_versions SET status = 'CHALLENGER' WHERE status = 'CANDIDATE'"))
    with _model_version_batch() as batch_op:
        batch_op.drop_index("ix_model_versions_training_manifest_id")
        batch_op.drop_constraint("ck_model_version_calibrator_reference", type_="check")
        batch_op.drop_constraint("uq_model_version_identity_checksum", type_="unique")
        batch_op.drop_constraint("fk_model_versions_training_manifest", type_="foreignkey")
        batch_op.alter_column(
            "status",
            existing_type=_NEW_MODEL_STATUS,
            type_=_OLD_MODEL_STATUS,
            existing_nullable=False,
        )
        batch_op.drop_column("promotion_reason")
        batch_op.drop_column("promotion_decision")
        batch_op.drop_column("target_definition")
        batch_op.drop_column("seed_policy")
        batch_op.drop_column("feature_names")
        batch_op.drop_column("identity_checksum")
        batch_op.drop_column("code_revision")
        batch_op.drop_column("ensemble_weights")
        batch_op.drop_column("calibrator_checksum")
        batch_op.drop_column("calibrator_uri")
        batch_op.drop_column("holdout_window")
        batch_op.drop_column("validation_window")
        batch_op.drop_column("training_manifest_id")
        batch_op.drop_column("target")

    op.drop_index("ix_training_dataset_event_window", table_name="training_dataset_manifests")
    op.drop_index("ix_training_dataset_manifests_sport_id", table_name="training_dataset_manifests")
    op.drop_index("ix_training_dataset_manifests_feature_version_id", table_name="training_dataset_manifests")
    op.drop_table("training_dataset_manifests")
