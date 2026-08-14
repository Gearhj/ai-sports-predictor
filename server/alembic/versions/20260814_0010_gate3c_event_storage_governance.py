"""Add governed Gen-2 event storage and source authorization records.

Revision ID: 20260814_0010
Revises: 20260814_0009
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260814_0010"
down_revision = "20260814_0009"
branch_labels = None
depends_on = None


def _identity_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
    )


def _created_at_column() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "source_owner_approvals",
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column(
            "approval_scope",
            sa.String(length=40),
            server_default="LOCAL_PRIVATE_RESEARCH",
            nullable=False,
        ),
        sa.Column("decision", sa.String(length=16), server_default="DENIED", nullable=False),
        sa.Column("approved", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("decided_by", sa.String(length=160), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorization_reference", sa.String(length=500), nullable=True),
        sa.Column("conditions", sa.JSON(), nullable=True),
        *_identity_columns(),
        _created_at_column(),
        sa.CheckConstraint(
            "approval_scope = 'LOCAL_PRIVATE_RESEARCH'",
            name="ck_source_owner_approval_scope",
        ),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'DENIED')",
            name="ck_source_owner_approval_decision",
        ),
        sa.CheckConstraint(
            "(approved = true AND decision = 'APPROVED') OR "
            "(approved = false AND decision = 'DENIED')",
            name="ck_source_owner_approval_consistency",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > decided_at",
            name="ck_source_owner_approval_expiry",
        ),
        sa.CheckConstraint(
            "length(identity_checksum) = 64",
            name="ck_source_owner_approval_identity_length",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_checksum", name="uq_source_owner_approval_identity"),
    )
    op.create_index(
        "ix_source_owner_approval_source_decided",
        "source_owner_approvals",
        ["source_key", "decided_at"],
    )

    op.create_table(
        "provider_access_authorizations",
        sa.Column("owner_approval_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column("provider_name", sa.String(length=120), nullable=False),
        sa.Column("access_method", sa.String(length=80), nullable=False),
        sa.Column("decision", sa.String(length=16), server_default="DENIED", nullable=False),
        sa.Column("authorized", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("decided_by", sa.String(length=160), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_reference", sa.String(length=500), nullable=True),
        sa.Column("terms_checksum", sa.String(length=64), nullable=True),
        sa.Column("decision_basis", sa.Text(), nullable=False),
        *_identity_columns(),
        _created_at_column(),
        sa.CheckConstraint(
            "decision IN ('AUTHORIZED', 'DENIED', 'UNRESOLVED')",
            name="ck_provider_access_authorization_decision",
        ),
        sa.CheckConstraint(
            "(authorized = true AND decision = 'AUTHORIZED') OR "
            "(authorized = false AND decision IN ('DENIED', 'UNRESOLVED'))",
            name="ck_provider_access_authorization_consistency",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > decided_at",
            name="ck_provider_access_authorization_expiry",
        ),
        sa.CheckConstraint(
            "terms_checksum IS NULL OR length(terms_checksum) = 64",
            name="ck_provider_access_authorization_terms_checksum",
        ),
        sa.CheckConstraint(
            "length(identity_checksum) = 64",
            name="ck_provider_access_authorization_identity_length",
        ),
        sa.ForeignKeyConstraint(["owner_approval_id"], ["source_owner_approvals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "identity_checksum", name="uq_provider_access_authorization_identity"
        ),
    )
    op.create_index(
        "ix_provider_access_authorization_source_decided",
        "provider_access_authorizations",
        ["source_key", "decided_at"],
    )
    op.create_index(
        "ix_provider_access_authorization_owner",
        "provider_access_authorizations",
        ["owner_approval_id"],
    )

    op.create_table(
        "existing_evidence_derived_use_decisions",
        sa.Column("owner_approval_id", sa.Uuid(), nullable=False),
        sa.Column("source_version_id", sa.Uuid(), nullable=True),
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column("evidence_manifest_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "use_scope",
            sa.String(length=40),
            server_default="LOCAL_PRIVATE_RESEARCH",
            nullable=False,
        ),
        sa.Column("decision", sa.String(length=16), server_default="DENIED", nullable=False),
        sa.Column("allowed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("decided_by", sa.String(length=160), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_basis", sa.Text(), nullable=False),
        *_identity_columns(),
        _created_at_column(),
        sa.CheckConstraint(
            "use_scope = 'LOCAL_PRIVATE_RESEARCH'",
            name="ck_existing_evidence_use_scope",
        ),
        sa.CheckConstraint(
            "decision IN ('ALLOWED', 'DENIED', 'UNRESOLVED')",
            name="ck_existing_evidence_use_decision",
        ),
        sa.CheckConstraint(
            "(allowed = true AND decision = 'ALLOWED') OR "
            "(allowed = false AND decision IN ('DENIED', 'UNRESOLVED'))",
            name="ck_existing_evidence_use_consistency",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > decided_at",
            name="ck_existing_evidence_use_expiry",
        ),
        sa.CheckConstraint(
            "length(evidence_manifest_checksum) = 64 "
            "AND length(identity_checksum) = 64",
            name="ck_existing_evidence_use_checksum_lengths",
        ),
        sa.ForeignKeyConstraint(["owner_approval_id"], ["source_owner_approvals.id"]),
        sa.ForeignKeyConstraint(["source_version_id"], ["historical_source_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_checksum", name="uq_existing_evidence_use_identity"),
    )
    op.create_index(
        "ix_existing_evidence_use_source_decided",
        "existing_evidence_derived_use_decisions",
        ["source_key", "decided_at"],
    )
    op.create_index(
        "ix_existing_evidence_use_source_version",
        "existing_evidence_derived_use_decisions",
        ["source_version_id"],
    )

    op.create_table(
        "historical_acquisition_stages",
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("owner_approval_id", sa.Uuid(), nullable=False),
        sa.Column("provider_authorization_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_stage_id", sa.Uuid(), nullable=True),
        sa.Column("stage_key", sa.String(length=40), nullable=False),
        sa.Column("stage_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("target_family", sa.String(length=40), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=True),
        sa.Column("event_start_date", sa.Date(), nullable=True),
        sa.Column("event_end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint_identity", sa.String(length=64), nullable=True),
        sa.Column("next_resume_point", sa.JSON(), nullable=True),
        sa.Column("physical_attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rate_limit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_received", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("bytes_received", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("elapsed_ms", sa.BigInteger(), server_default="0", nullable=False),
        *_identity_columns(),
        _created_at_column(),
        sa.CheckConstraint(
            "stage_revision >= 1", name="ck_historical_acquisition_stage_revision"
        ),
        sa.CheckConstraint("season >= 1876", name="ck_historical_acquisition_stage_season"),
        sa.CheckConstraint(
            "month IS NULL OR (month >= 1 AND month <= 12)",
            name="ck_historical_acquisition_stage_month",
        ),
        sa.CheckConstraint(
            "(event_start_date IS NULL AND event_end_date IS NULL) OR "
            "(event_start_date IS NOT NULL AND event_end_date IS NOT NULL "
            "AND event_end_date >= event_start_date)",
            name="ck_historical_acquisition_stage_window",
        ),
        sa.CheckConstraint(
            "target_family IN ('MLB_CORE', 'STATCAST')",
            name="ck_historical_acquisition_stage_family",
        ),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'BLOCKED', 'AUTHORIZED', 'RUNNING', "
            "'SUCCEEDED', 'FAILED', 'SKIPPED')",
            name="ck_historical_acquisition_stage_status",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at <= recorded_at",
            name="ck_historical_acquisition_stage_start",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR (started_at IS NOT NULL "
            "AND completed_at >= started_at AND completed_at <= recorded_at)",
            name="ck_historical_acquisition_stage_completion",
        ),
        sa.CheckConstraint(
            "physical_attempt_count >= 0 AND success_count >= 0 "
            "AND failure_count >= 0 AND retry_count >= 0 AND rate_limit_count >= 0 "
            "AND success_count + failure_count + rate_limit_count <= physical_attempt_count",
            name="ck_historical_acquisition_stage_attempt_counts",
        ),
        sa.CheckConstraint(
            "records_received >= 0 AND bytes_received >= 0 AND elapsed_ms >= 0",
            name="ck_historical_acquisition_stage_volume",
        ),
        sa.CheckConstraint(
            "checkpoint_identity IS NULL OR length(checkpoint_identity) = 64",
            name="ck_historical_acquisition_stage_checkpoint",
        ),
        sa.CheckConstraint(
            "length(identity_checksum) = 64",
            name="ck_historical_acquisition_stage_identity_length",
        ),
        sa.ForeignKeyConstraint(["owner_approval_id"], ["source_owner_approvals.id"]),
        sa.ForeignKeyConstraint(
            ["provider_authorization_id"], ["provider_access_authorizations.id"]
        ),
        sa.ForeignKeyConstraint(["source_version_id"], ["historical_source_versions.id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_stage_id"], ["historical_acquisition_stages.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_version_id",
            "stage_key",
            "stage_revision",
            name="uq_historical_acquisition_stage_revision",
        ),
        sa.UniqueConstraint(
            "identity_checksum", name="uq_historical_acquisition_stage_identity"
        ),
    )
    op.create_index(
        "ix_historical_acquisition_stage_status",
        "historical_acquisition_stages",
        ["stage_key", "status"],
    )
    op.create_index(
        "ix_historical_acquisition_stage_source",
        "historical_acquisition_stages",
        ["source_version_id", "recorded_at"],
    )

    op.create_table(
        "historical_request_attempts",
        sa.Column("acquisition_stage_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=True),
        sa.Column("provider_ledger_id", sa.Uuid(), nullable=True),
        sa.Column("endpoint_key", sa.String(length=120), nullable=False),
        sa.Column("attempt_sequence", sa.Integer(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("records_received", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("bytes_received", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("elapsed_ms", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *_identity_columns(),
        _created_at_column(),
        sa.CheckConstraint(
            "attempt_sequence >= 1", name="ck_historical_request_attempt_sequence"
        ),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED', 'FAILED', 'RATE_LIMITED')",
            name="ck_historical_request_attempt_status",
        ),
        sa.CheckConstraint(
            "completed_at >= requested_at",
            name="ck_historical_request_attempt_completion",
        ),
        sa.CheckConstraint(
            "http_status_code IS NULL OR (http_status_code >= 100 AND http_status_code <= 599)",
            name="ck_historical_request_attempt_http_status",
        ),
        sa.CheckConstraint(
            "records_received >= 0 AND bytes_received >= 0 AND elapsed_ms >= 0 "
            "AND (retry_after_seconds IS NULL OR retry_after_seconds >= 0)",
            name="ck_historical_request_attempt_volume",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64 AND length(identity_checksum) = 64",
            name="ck_historical_request_attempt_checksum_lengths",
        ),
        sa.ForeignKeyConstraint(
            ["acquisition_stage_id"], ["historical_acquisition_stages.id"]
        ),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["history_backfill_checkpoints.id"]),
        sa.ForeignKeyConstraint(["provider_ledger_id"], ["provider_request_ledger.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "acquisition_stage_id",
            "attempt_sequence",
            name="uq_historical_request_attempt_sequence",
        ),
        sa.UniqueConstraint(
            "identity_checksum", name="uq_historical_request_attempt_identity"
        ),
    )
    op.create_index(
        "ix_historical_request_attempt_stage",
        "historical_request_attempts",
        ["acquisition_stage_id", "attempt_sequence"],
    )
    op.create_index(
        "ix_historical_request_attempt_status",
        "historical_request_attempts",
        ["status", "requested_at"],
    )

    op.create_table(
        "historical_event_partitions",
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("event_family", sa.String(length=32), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("logical_uri", sa.String(length=1000), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("game_count", sa.Integer(), nullable=False),
        sa.Column("plate_appearance_count", sa.BigInteger(), nullable=False),
        sa.Column("player_count", sa.Integer(), nullable=False),
        sa.Column("min_event_date", sa.Date(), nullable=True),
        sa.Column("max_event_date", sa.Date(), nullable=True),
        sa.Column("min_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_format", sa.String(length=16), server_default="PARQUET", nullable=False),
        sa.Column("compression", sa.String(length=16), server_default="ZSTD", nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("schema_hash", sa.String(length=64), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("raw_contributor_manifest", sa.JSON(), nullable=False),
        sa.Column("raw_contributor_checksum", sa.String(length=64), nullable=False),
        sa.Column("code_revision", sa.String(length=120), nullable=False),
        sa.Column("evidence_tier", sa.String(length=16), nullable=False),
        sa.Column("timing_classification", sa.String(length=80), nullable=False),
        *_identity_columns(),
        _created_at_column(),
        sa.CheckConstraint(
            "event_family IN ('pitch', 'plate_appearance')",
            name="ck_historical_event_partition_family",
        ),
        sa.CheckConstraint("season >= 1876", name="ck_historical_event_partition_season"),
        sa.CheckConstraint(
            "month >= 1 AND month <= 12", name="ck_historical_event_partition_month"
        ),
        sa.CheckConstraint(
            "logical_uri LIKE 'historical-events/%' "
            "AND logical_uri NOT LIKE '/%' "
            "AND logical_uri NOT LIKE '%..%' "
            "AND replace(logical_uri, '\\', '') = logical_uri "
            "AND logical_uri LIKE '%.parquet'",
            name="ck_historical_event_partition_logical_uri",
        ),
        sa.CheckConstraint(
            "row_count > 0 AND game_count > 0 "
            "AND plate_appearance_count > 0 AND player_count > 0",
            name="ck_historical_event_partition_counts",
        ),
        sa.CheckConstraint(
            "(min_event_date IS NULL AND max_event_date IS NULL) OR "
            "(min_event_date IS NOT NULL AND max_event_date IS NOT NULL "
            "AND max_event_date >= min_event_date)",
            name="ck_historical_event_partition_date_bounds",
        ),
        sa.CheckConstraint(
            "(min_event_at IS NULL AND max_event_at IS NULL) OR "
            "(min_event_at IS NOT NULL AND max_event_at IS NOT NULL "
            "AND max_event_at >= min_event_at)",
            name="ck_historical_event_partition_time_bounds",
        ),
        sa.CheckConstraint(
            "storage_format = 'PARQUET' AND compression = 'ZSTD'",
            name="ck_historical_event_partition_storage",
        ),
        sa.CheckConstraint(
            "file_size_bytes > 0", name="ck_historical_event_partition_file_size"
        ),
        sa.CheckConstraint(
            "evidence_tier IN ('A', 'B', 'C', 'EXCLUDED')",
            name="ck_historical_event_partition_tier",
        ),
        sa.CheckConstraint(
            "length(schema_hash) = 64 AND length(file_sha256) = 64 "
            "AND length(raw_contributor_checksum) = 64 "
            "AND length(identity_checksum) = 64",
            name="ck_historical_event_partition_checksums",
        ),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["history_backfill_checkpoints.id"]),
        sa.ForeignKeyConstraint(["source_version_id"], ["historical_source_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("logical_uri", name="uq_historical_event_partition_uri"),
        sa.UniqueConstraint(
            "identity_checksum", name="uq_historical_event_partition_identity"
        ),
    )
    op.create_index(
        "ix_historical_event_partition_source",
        "historical_event_partitions",
        ["source_version_id", "event_family", "season", "month"],
    )
    op.create_index(
        "ix_historical_event_partition_checkpoint",
        "historical_event_partitions",
        ["checkpoint_id"],
    )
    op.create_index(
        "ix_historical_event_partition_events",
        "historical_event_partitions",
        ["min_event_at", "max_event_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_historical_event_partition_events", table_name="historical_event_partitions"
    )
    op.drop_index(
        "ix_historical_event_partition_checkpoint", table_name="historical_event_partitions"
    )
    op.drop_index(
        "ix_historical_event_partition_source", table_name="historical_event_partitions"
    )
    op.drop_table("historical_event_partitions")

    op.drop_index(
        "ix_historical_request_attempt_status", table_name="historical_request_attempts"
    )
    op.drop_index(
        "ix_historical_request_attempt_stage", table_name="historical_request_attempts"
    )
    op.drop_table("historical_request_attempts")

    op.drop_index(
        "ix_historical_acquisition_stage_source", table_name="historical_acquisition_stages"
    )
    op.drop_index(
        "ix_historical_acquisition_stage_status", table_name="historical_acquisition_stages"
    )
    op.drop_table("historical_acquisition_stages")

    op.drop_index(
        "ix_existing_evidence_use_source_version",
        table_name="existing_evidence_derived_use_decisions",
    )
    op.drop_index(
        "ix_existing_evidence_use_source_decided",
        table_name="existing_evidence_derived_use_decisions",
    )
    op.drop_table("existing_evidence_derived_use_decisions")

    op.drop_index(
        "ix_provider_access_authorization_owner",
        table_name="provider_access_authorizations",
    )
    op.drop_index(
        "ix_provider_access_authorization_source_decided",
        table_name="provider_access_authorizations",
    )
    op.drop_table("provider_access_authorizations")

    op.drop_index(
        "ix_source_owner_approval_source_decided", table_name="source_owner_approvals"
    )
    op.drop_table("source_owner_approvals")
