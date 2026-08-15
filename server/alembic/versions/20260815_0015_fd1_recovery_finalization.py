"""Add durable, invisible staging for resumable FD1 recovery finalization.

Revision ID: 20260815_0015
Revises: 20260814_0014
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# Register the recovery-only mappings on ``Base.metadata`` when Alembic loads
# this revision.  They deliberately do not live in ``app.models`` because the
# canonical entity module is an input to the already-frozen FD1 generation
# revision.  Importing here keeps ``alembic check`` exact without rewriting
# that generation lineage.
from app.gen2 import fd1_recovery_models as _fd1_recovery_models  # noqa: F401,E402


revision = "20260815_0015"
down_revision = "20260814_0014"
branch_labels = None
depends_on = None


_STAGES = (
    "'FILES_VALIDATED', 'IDENTITY_STAGED', 'IDENTITY_RECONCILED', "
    "'PARTITIONS_STAGED', 'WAREHOUSE_VALIDATED', 'FINALIZED'"
)
_EVENT_FAMILIES = (
    "'pitch', 'plate_appearance', 'play_transition', 'pitch_sequence', "
    "'player_game_batting', 'player_game_pitching', 'player_game_fielding', "
    "'starter_usage', 'bullpen_usage', 'weather', 'player_prior'"
)


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


def _updated_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


def _create_runs() -> None:
    op.create_table(
        "fd1_recovery_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("manifest_checksum", sa.String(length=64), nullable=False),
        sa.Column("manifest_schema_version", sa.String(length=80), nullable=False),
        sa.Column("manifest_logical_uri", sa.String(length=1000), nullable=False),
        sa.Column("manifest_header", sa.JSON(), nullable=False),
        sa.Column("hash_cache_schema_version", sa.String(length=80), nullable=False),
        sa.Column("hash_cache_checksum", sa.String(length=64), nullable=False),
        sa.Column("generation_code_revision", sa.String(length=120), nullable=False),
        sa.Column("finalizer_code_revision", sa.String(length=120), nullable=False),
        sa.Column(
            "identity_spool_logical_uri", sa.String(length=1000), nullable=False
        ),
        sa.Column("identity_spool_sha256", sa.String(length=64), nullable=False),
        sa.Column("identity_spool_row_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="PLANNED",
            nullable=False,
        ),
        sa.Column("active_stage", sa.String(length=32), nullable=True),
        sa.Column("last_completed_stage", sa.String(length=32), nullable=True),
        sa.Column("rehearsal_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "max_runtime_minutes",
            sa.Integer(),
            server_default="120",
            nullable=False,
        ),
        sa.Column(
            "heartbeat_seconds", sa.Integer(), server_default="30", nullable=False
        ),
        sa.Column("provider_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("performance_metrics", sa.JSON(), nullable=False),
        sa.Column("last_operation", sa.Text(), nullable=True),
        sa.Column("resume_token", sa.JSON(), nullable=True),
        sa.Column("error_type", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "length(manifest_checksum) = 64 "
            "AND length(hash_cache_checksum) = 64 "
            "AND length(identity_spool_sha256) = 64",
            name="ck_fd1_recovery_run_checksums",
        ),
        sa.CheckConstraint(
            "length(generation_code_revision) IN (40, 64) "
            "AND length(finalizer_code_revision) IN (40, 64)",
            name="ck_fd1_recovery_run_revisions",
        ),
        sa.CheckConstraint(
            "manifest_logical_uri LIKE '.runtime/gate3c-fd1/recovery/%' "
            "AND manifest_logical_uri NOT LIKE '%..%' "
            "AND replace(manifest_logical_uri, '\\', '') = manifest_logical_uri "
            "AND identity_spool_logical_uri LIKE '.runtime/gate3c-fd1/recovery/%' "
            "AND identity_spool_logical_uri NOT LIKE '%..%' "
            "AND replace(identity_spool_logical_uri, '\\', '') = "
            "identity_spool_logical_uri",
            name="ck_fd1_recovery_run_uris",
        ),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'RUNNING', 'STOPPED', 'FAILED', "
            "'REHEARSAL_SUCCEEDED', 'FINALIZED')",
            name="ck_fd1_recovery_run_status",
        ),
        sa.CheckConstraint(
            f"(active_stage IS NULL OR active_stage IN ({_STAGES})) "
            f"AND (last_completed_stage IS NULL OR last_completed_stage IN ({_STAGES}))",
            name="ck_fd1_recovery_run_stages",
        ),
        sa.CheckConstraint(
            "identity_spool_row_count > 0 "
            "AND max_runtime_minutes >= 1 AND max_runtime_minutes <= 120 "
            "AND heartbeat_seconds >= 1 AND heartbeat_seconds <= 30 "
            "AND provider_calls = 0",
            name="ck_fd1_recovery_run_bounds",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_fd1_recovery_run_lease",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND started_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND deadline_at IS NOT NULL) "
            "OR status <> 'RUNNING'",
            name="ck_fd1_recovery_run_running_times",
        ),
        sa.CheckConstraint(
            "(status = 'FINALIZED' AND finalized_at IS NOT NULL "
            "AND last_completed_stage = 'FINALIZED' AND active_stage IS NULL) "
            "OR (status <> 'FINALIZED' AND finalized_at IS NULL)",
            name="ck_fd1_recovery_run_finalization",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "manifest_checksum", name="uq_fd1_recovery_run_manifest"
        ),
    )
    op.create_index(
        "ix_fd1_recovery_run_status_heartbeat",
        "fd1_recovery_runs",
        ["status", "heartbeat_at"],
    )
    op.create_index(
        "ix_fd1_recovery_run_stage",
        "fd1_recovery_runs",
        ["last_completed_stage", "active_stage"],
    )


def _create_stage_checkpoints() -> None:
    op.create_table(
        "fd1_recovery_stage_checkpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("stage_sequence", sa.SmallInteger(), nullable=False),
        sa.Column("stage_name", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_checksum", sa.String(length=64), nullable=False),
        sa.Column("output_checksum", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("performance_metrics", sa.JSON(), nullable=False),
        sa.Column("resume_token", sa.JSON(), nullable=True),
        sa.Column("error_type", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "scope IN ('FULL', 'REHEARSAL')",
            name="ck_fd1_recovery_stage_scope",
        ),
        sa.CheckConstraint(
            "(stage_sequence = 1 AND stage_name = 'FILES_VALIDATED') OR "
            "(stage_sequence = 2 AND stage_name = 'IDENTITY_STAGED') OR "
            "(stage_sequence = 3 AND stage_name = 'IDENTITY_RECONCILED') OR "
            "(stage_sequence = 4 AND stage_name = 'PARTITIONS_STAGED') OR "
            "(stage_sequence = 5 AND stage_name = 'WAREHOUSE_VALIDATED') OR "
            "(stage_sequence = 6 AND stage_name = 'FINALIZED')",
            name="ck_fd1_recovery_stage_sequence",
        ),
        sa.CheckConstraint(
            "attempt >= 1 AND status IN "
            "('PENDING', 'RUNNING', 'SUCCEEDED', 'STOPPED', 'FAILED')",
            name="ck_fd1_recovery_stage_attempt_status",
        ),
        sa.CheckConstraint(
            "length(input_checksum) = 64 "
            "AND (output_checksum IS NULL OR length(output_checksum) = 64)",
            name="ck_fd1_recovery_stage_checksums",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING' AND started_at IS NULL "
            "AND heartbeat_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'RUNNING' AND started_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('SUCCEEDED', 'STOPPED', 'FAILED') "
            "AND started_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND completed_at >= started_at)",
            name="ck_fd1_recovery_stage_times",
        ),
        sa.CheckConstraint(
            "(status = 'SUCCEEDED' AND output_checksum IS NOT NULL "
            "AND error_type IS NULL AND error_message IS NULL) "
            "OR status <> 'SUCCEEDED'",
            name="ck_fd1_recovery_stage_success",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["fd1_recovery_runs.id"],
            name="fk_fd1_recovery_stage_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "scope",
            "stage_name",
            "attempt",
            name="uq_fd1_recovery_stage_attempt",
        ),
    )
    op.create_index(
        "uq_fd1_recovery_stage_success_once",
        "fd1_recovery_stage_checkpoints",
        ["run_id", "scope", "stage_name"],
        unique=True,
        postgresql_where=sa.text("status = 'SUCCEEDED'"),
        sqlite_where=sa.text("status = 'SUCCEEDED'"),
    )
    op.create_index(
        "ix_fd1_recovery_stage_progress",
        "fd1_recovery_stage_checkpoints",
        ["run_id", "scope", "stage_sequence", "status"],
    )
    op.create_index(
        "ix_fd1_recovery_stage_heartbeat",
        "fd1_recovery_stage_checkpoints",
        ["status", "heartbeat_at"],
    )


def _create_manifest_files() -> None:
    op.create_table(
        "fd1_recovery_manifest_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(length=1000), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("min_event_date", sa.Date(), nullable=True),
        sa.Column("max_event_date", sa.Date(), nullable=True),
        sa.Column("event_family", sa.String(length=32), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column(
            "partition_identity_checksum", sa.String(length=64), nullable=False
        ),
        sa.Column("validation_mode", sa.String(length=16), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "ordinal >= 1 AND size_bytes > 0 AND mtime_ns >= 0 AND row_count > 0",
            name="ck_fd1_recovery_file_counts",
        ),
        sa.CheckConstraint(
            "relative_path LIKE 'historical-events/%' "
            "AND relative_path LIKE '%.parquet' "
            "AND relative_path NOT LIKE '/%' "
            "AND relative_path NOT LIKE '%..%' "
            "AND replace(relative_path, '\\', '') = relative_path",
            name="ck_fd1_recovery_file_uri",
        ),
        sa.CheckConstraint(
            "length(file_sha256) = 64 "
            "AND length(schema_fingerprint) = 64 "
            "AND length(partition_identity_checksum) = 64",
            name="ck_fd1_recovery_file_checksums",
        ),
        sa.CheckConstraint(
            f"event_family IN ({_EVENT_FAMILIES})",
            name="ck_fd1_recovery_file_family",
        ),
        sa.CheckConstraint(
            "season >= 1876 AND month >= 1 AND month <= 12",
            name="ck_fd1_recovery_file_partition",
        ),
        sa.CheckConstraint(
            "(min_event_date IS NULL AND max_event_date IS NULL) OR "
            "(min_event_date IS NOT NULL AND max_event_date IS NOT NULL "
            "AND max_event_date >= min_event_date)",
            name="ck_fd1_recovery_file_dates",
        ),
        sa.CheckConstraint(
            "validation_mode IN ('CACHE_REUSE', 'REHASH')",
            name="ck_fd1_recovery_file_validation_mode",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["fd1_recovery_runs.id"],
            name="fk_fd1_recovery_file_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "ordinal", name="uq_fd1_recovery_file_ordinal"
        ),
        sa.UniqueConstraint(
            "run_id", "relative_path", name="uq_fd1_recovery_file_path"
        ),
        sa.UniqueConstraint(
            "run_id",
            "partition_identity_checksum",
            name="uq_fd1_recovery_file_partition_identity",
        ),
    )
    op.create_index(
        "ix_fd1_recovery_file_partition",
        "fd1_recovery_manifest_files",
        ["run_id", "event_family", "season", "month"],
    )


def _create_identity_stage() -> None:
    op.create_table(
        "fd1_recovery_identity_stage",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("staging_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column("batch_number", sa.Integer(), nullable=False),
        sa.Column("batch_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "source_release_identity_checksum", sa.String(length=64), nullable=False
        ),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("source_namespace", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("canonical_namespace", sa.String(length=80), nullable=False),
        sa.Column("canonical_id", sa.String(length=255), nullable=True),
        sa.Column("source_row_number", sa.BigInteger(), nullable=False),
        sa.Column("source_row_checksum", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.String(length=24), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_through", sa.Date(), nullable=True),
        sa.Column("evidence_logical_uri", sa.String(length=1000), nullable=False),
        sa.Column("mapping_basis", sa.String(length=120), nullable=False),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("record_checksum", sa.String(length=64), nullable=False),
        sa.Column("target_mapping_id", sa.Uuid(), nullable=False),
        sa.Column(
            "outcome",
            sa.String(length=16),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("matched_mapping_id", sa.Uuid(), nullable=True),
        sa.Column("matched_stage_ordinal", sa.BigInteger(), nullable=True),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.CheckConstraint(
            "scope IN ('FULL', 'REHEARSAL')",
            name="ck_fd1_recovery_identity_scope",
        ),
        sa.CheckConstraint(
            "staging_ordinal >= 1 AND batch_number >= 1 AND source_row_number >= 1",
            name="ck_fd1_recovery_identity_ordinals",
        ),
        sa.CheckConstraint(
            "entity_type IN ('person', 'team', 'franchise', 'park', 'game', 'umpire')",
            name="ck_fd1_recovery_identity_entity",
        ),
        sa.CheckConstraint(
            "confidence IN ('EXACT', 'HIGH', 'UNRESOLVED', 'CONFLICT')",
            name="ck_fd1_recovery_identity_confidence",
        ),
        sa.CheckConstraint(
            "(confidence IN ('EXACT', 'HIGH') AND canonical_id IS NOT NULL) "
            "OR (confidence IN ('UNRESOLVED', 'CONFLICT') "
            "AND canonical_id IS NULL)",
            name="ck_fd1_recovery_identity_resolution",
        ),
        sa.CheckConstraint(
            "valid_through IS NULL OR valid_from IS NULL OR valid_through >= valid_from",
            name="ck_fd1_recovery_identity_window",
        ),
        sa.CheckConstraint(
            "evidence_logical_uri NOT LIKE '/%' "
            "AND evidence_logical_uri NOT LIKE '%..%' "
            "AND replace(evidence_logical_uri, '\\', '') = evidence_logical_uri",
            name="ck_fd1_recovery_identity_uri",
        ),
        sa.CheckConstraint(
            "length(source_release_identity_checksum) = 64 "
            "AND length(batch_checksum) = 64 "
            "AND length(source_row_checksum) = 64 "
            "AND length(identity_checksum) = 64 "
            "AND length(record_checksum) = 64",
            name="ck_fd1_recovery_identity_checksums",
        ),
        sa.CheckConstraint(
            "outcome IN ('PENDING', 'INSERT', 'REUSE', 'UNRESOLVED', 'CONFLICT')",
            name="ck_fd1_recovery_identity_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'PENDING' AND matched_mapping_id IS NULL "
            "AND matched_stage_ordinal IS NULL AND reason_code IS NULL "
            "AND reconciled_at IS NULL) OR "
            "(outcome = 'INSERT' AND matched_mapping_id IS NULL "
            "AND matched_stage_ordinal IS NULL AND reason_code IS NULL "
            "AND reconciled_at IS NOT NULL) OR "
            "(outcome = 'REUSE' AND reconciled_at IS NOT NULL "
            "AND reason_code IS NULL AND "
            "((matched_mapping_id IS NOT NULL AND matched_stage_ordinal IS NULL) "
            "OR (matched_mapping_id IS NULL AND matched_stage_ordinal IS NOT NULL))) OR "
            "(outcome IN ('UNRESOLVED', 'CONFLICT') "
            "AND reconciled_at IS NOT NULL AND reason_code IS NOT NULL)",
            name="ck_fd1_recovery_identity_reconciliation",
        ),
        sa.CheckConstraint(
            "matched_stage_ordinal IS NULL OR matched_stage_ordinal <> staging_ordinal",
            name="ck_fd1_recovery_identity_stage_match",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["fd1_recovery_runs.id"],
            name="fk_fd1_recovery_identity_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["historical_source_versions.id"],
            name="fk_fd1_recovery_identity_source_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["matched_mapping_id"],
            ["open_data_identity_mappings.id"],
            name="fk_fd1_recovery_identity_mapping_match",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "scope", "matched_stage_ordinal"],
            [
                "fd1_recovery_identity_stage.run_id",
                "fd1_recovery_identity_stage.scope",
                "fd1_recovery_identity_stage.staging_ordinal",
            ],
            name="fk_fd1_recovery_identity_stage_match",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id", "scope", "staging_ordinal"),
    )
    op.create_index(
        "ix_fd1_recovery_identity_batch",
        "fd1_recovery_identity_stage",
        ["run_id", "scope", "source_key", "batch_number", "batch_checksum"],
    )
    op.create_index(
        "ix_fd1_recovery_identity_natural",
        "fd1_recovery_identity_stage",
        [
            "run_id",
            "scope",
            "source_version_id",
            "entity_type",
            "source_namespace",
            "external_id",
            "source_row_number",
        ],
    )
    op.create_index(
        "ix_fd1_recovery_identity_checksum",
        "fd1_recovery_identity_stage",
        ["run_id", "scope", "identity_checksum"],
    )
    op.create_index(
        "ix_fd1_recovery_identity_outcome",
        "fd1_recovery_identity_stage",
        ["run_id", "scope", "outcome"],
    )
    op.create_index(
        "ix_fd1_recovery_identity_coverage",
        "fd1_recovery_identity_stage",
        ["run_id", "scope", "entity_type", "confidence"],
    )


def _create_identity_quarantine() -> None:
    op.create_table(
        "fd1_recovery_identity_quarantine",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("staging_ordinal", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("staged_record_checksum", sa.String(length=64), nullable=False),
        sa.Column("existing_mapping_id", sa.Uuid(), nullable=True),
        sa.Column("existing_payload", sa.JSON(), nullable=True),
        sa.Column("evidence_checksum", sa.String(length=64), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "scope IN ('FULL', 'REHEARSAL')",
            name="ck_fd1_recovery_quarantine_scope",
        ),
        sa.CheckConstraint(
            "outcome IN ('UNRESOLVED', 'CONFLICT')",
            name="ck_fd1_recovery_quarantine_outcome",
        ),
        sa.CheckConstraint(
            "length(staged_record_checksum) = 64 "
            "AND length(evidence_checksum) = 64",
            name="ck_fd1_recovery_quarantine_checksums",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "scope", "staging_ordinal"],
            [
                "fd1_recovery_identity_stage.run_id",
                "fd1_recovery_identity_stage.scope",
                "fd1_recovery_identity_stage.staging_ordinal",
            ],
            name="fk_fd1_recovery_quarantine_stage",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["existing_mapping_id"],
            ["open_data_identity_mappings.id"],
            name="fk_fd1_recovery_quarantine_existing_mapping",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "scope",
            "staging_ordinal",
            "reason_code",
            name="uq_fd1_recovery_quarantine_reason",
        ),
    )
    op.create_index(
        "ix_fd1_recovery_quarantine_outcome",
        "fd1_recovery_identity_quarantine",
        ["run_id", "scope", "outcome", "reason_code"],
    )


def _create_publication_stage() -> None:
    op.create_table(
        "fd1_recovery_publication_stage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("object_kind", sa.String(length=32), nullable=False),
        sa.Column("promotion_order", sa.SmallInteger(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("natural_key_checksum", sa.String(length=64), nullable=False),
        sa.Column("semantic_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("target_payload", sa.JSON(), nullable=False),
        sa.Column("payload_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "action",
            sa.String(length=16),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("matched_target_id", sa.Uuid(), nullable=True),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "scope IN ('FULL', 'REHEARSAL')",
            name="ck_fd1_recovery_publication_scope",
        ),
        sa.CheckConstraint(
            "ordinal >= 1",
            name="ck_fd1_recovery_publication_ordinal",
        ),
        sa.CheckConstraint(
            "(object_kind = 'DERIVED_USE_DECISION' AND promotion_order = 10) OR "
            "(object_kind = 'INGESTION_RUN' AND promotion_order = 20) OR "
            "(object_kind = 'CHECKPOINT' AND promotion_order = 30) OR "
            "(object_kind = 'PARTITION' AND promotion_order = 40)",
            name="ck_fd1_recovery_publication_order",
        ),
        sa.CheckConstraint(
            "length(natural_key_checksum) = 64 "
            "AND length(semantic_identity_checksum) = 64 "
            "AND length(payload_checksum) = 64",
            name="ck_fd1_recovery_publication_checksums",
        ),
        sa.CheckConstraint(
            "action IN ('PENDING', 'INSERT', 'REUSE', 'CONFLICT')",
            name="ck_fd1_recovery_publication_action",
        ),
        sa.CheckConstraint(
            "(action = 'PENDING' AND matched_target_id IS NULL "
            "AND reason_code IS NULL AND promoted_at IS NULL) OR "
            "(action = 'INSERT' AND matched_target_id IS NULL "
            "AND reason_code IS NULL) OR "
            "(action = 'REUSE' AND matched_target_id IS NOT NULL "
            "AND reason_code IS NULL) OR "
            "(action = 'CONFLICT' AND reason_code IS NOT NULL "
            "AND promoted_at IS NULL)",
            name="ck_fd1_recovery_publication_state",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["fd1_recovery_runs.id"],
            name="fk_fd1_recovery_publication_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "scope", "ordinal", name="uq_fd1_recovery_publication_ordinal"
        ),
        sa.UniqueConstraint(
            "run_id",
            "scope",
            "object_kind",
            "target_id",
            name="uq_fd1_recovery_publication_target",
        ),
        sa.UniqueConstraint(
            "run_id",
            "scope",
            "object_kind",
            "semantic_identity_checksum",
            name="uq_fd1_recovery_publication_identity",
        ),
    )
    op.create_index(
        "ix_fd1_recovery_publication_order",
        "fd1_recovery_publication_stage",
        ["run_id", "scope", "promotion_order", "action"],
    )
    op.create_index(
        "ix_fd1_recovery_publication_payload",
        "fd1_recovery_publication_stage",
        ["run_id", "scope", "payload_checksum"],
    )


def upgrade() -> None:
    _create_runs()
    _create_stage_checkpoints()
    _create_manifest_files()
    _create_identity_stage()
    _create_identity_quarantine()
    _create_publication_stage()


def downgrade() -> None:
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM fd1_recovery_runs LIMIT 1")
    ).first() is not None:
        raise RuntimeError(
            "Cannot downgrade FD1 recovery finalization while recovery evidence exists"
        )

    op.drop_index(
        "ix_fd1_recovery_publication_payload",
        table_name="fd1_recovery_publication_stage",
    )
    op.drop_index(
        "ix_fd1_recovery_publication_order",
        table_name="fd1_recovery_publication_stage",
    )
    op.drop_table("fd1_recovery_publication_stage")

    op.drop_index(
        "ix_fd1_recovery_quarantine_outcome",
        table_name="fd1_recovery_identity_quarantine",
    )
    op.drop_table("fd1_recovery_identity_quarantine")

    op.drop_index(
        "ix_fd1_recovery_identity_coverage",
        table_name="fd1_recovery_identity_stage",
    )
    op.drop_index(
        "ix_fd1_recovery_identity_outcome",
        table_name="fd1_recovery_identity_stage",
    )
    op.drop_index(
        "ix_fd1_recovery_identity_checksum",
        table_name="fd1_recovery_identity_stage",
    )
    op.drop_index(
        "ix_fd1_recovery_identity_batch",
        table_name="fd1_recovery_identity_stage",
    )
    op.drop_index(
        "ix_fd1_recovery_identity_natural",
        table_name="fd1_recovery_identity_stage",
    )
    op.drop_table("fd1_recovery_identity_stage")

    op.drop_index(
        "ix_fd1_recovery_file_partition",
        table_name="fd1_recovery_manifest_files",
    )
    op.drop_table("fd1_recovery_manifest_files")

    op.drop_index(
        "ix_fd1_recovery_stage_heartbeat",
        table_name="fd1_recovery_stage_checkpoints",
    )
    op.drop_index(
        "ix_fd1_recovery_stage_progress",
        table_name="fd1_recovery_stage_checkpoints",
    )
    op.drop_index(
        "uq_fd1_recovery_stage_success_once",
        table_name="fd1_recovery_stage_checkpoints",
    )
    op.drop_table("fd1_recovery_stage_checkpoints")

    op.drop_index("ix_fd1_recovery_run_stage", table_name="fd1_recovery_runs")
    op.drop_index(
        "ix_fd1_recovery_run_status_heartbeat", table_name="fd1_recovery_runs"
    )
    op.drop_table("fd1_recovery_runs")
