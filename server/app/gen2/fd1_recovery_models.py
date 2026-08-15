"""Recovery-only persistence models for resumable FD1 finalization.

These operational tables are intentionally isolated from ``app.models``.
The canonical entity module is part of the frozen code revision that generated
the existing Parquet corpus; recovery persistence must not rewrite that
generation lineage merely to record later validation and finalization work.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base
import app.models as _canonical_models  # noqa: F401 - resolves governed FK targets


RECOVERY_SCOPES = ("FULL", "REHEARSAL")
RECOVERY_STAGES = (
    "FILES_VALIDATED",
    "IDENTITY_STAGED",
    "IDENTITY_RECONCILED",
    "PARTITIONS_STAGED",
    "WAREHOUSE_VALIDATED",
    "FINALIZED",
)
RECOVERY_STAGE_SEQUENCE = {
    name: sequence for sequence, name in enumerate(RECOVERY_STAGES, start=1)
}
RECOVERY_PUBLICATION_ORDER = {
    "DERIVED_USE_DECISION": 10,
    "INGESTION_RUN": 20,
    "CHECKPOINT": 30,
    "PARTITION": 40,
}

_STAGES_SQL = (
    "'FILES_VALIDATED', 'IDENTITY_STAGED', 'IDENTITY_RECONCILED', "
    "'PARTITIONS_STAGED', 'WAREHOUSE_VALIDATED', 'FINALIZED'"
)
_EVENT_FAMILIES_SQL = (
    "'pitch', 'plate_appearance', 'play_transition', 'pitch_sequence', "
    "'player_game_batting', 'player_game_pitching', 'player_game_fielding', "
    "'starter_usage', 'bullpen_usage', 'weather', 'player_prior'"
)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class FD1RecoveryRun(Base):
    """One content-addressed recovery manifest and its operational lifecycle."""

    __tablename__ = "fd1_recovery_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest_logical_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    manifest_header: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    hash_cache_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    hash_cache_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_code_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    finalizer_code_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    identity_spool_logical_uri: Mapped[str] = mapped_column(
        String(1000), nullable=False
    )
    identity_spool_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_spool_row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="PLANNED", server_default="PLANNED"
    )
    active_stage: Mapped[str | None] = mapped_column(String(32))
    last_completed_stage: Mapped[str | None] = mapped_column(String(32))
    rehearsal_succeeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_runtime_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=120, server_default="120"
    )
    heartbeat_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    provider_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    performance_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    last_operation: Mapped[str | None] = mapped_column(Text)
    resume_token: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_type: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "length(manifest_checksum) = 64 "
            "AND length(hash_cache_checksum) = 64 "
            "AND length(identity_spool_sha256) = 64",
            name="ck_fd1_recovery_run_checksums",
        ),
        CheckConstraint(
            "length(generation_code_revision) IN (40, 64) "
            "AND length(finalizer_code_revision) IN (40, 64)",
            name="ck_fd1_recovery_run_revisions",
        ),
        CheckConstraint(
            "manifest_logical_uri LIKE '.runtime/gate3c-fd1/recovery/%' "
            "AND manifest_logical_uri NOT LIKE '%..%' "
            "AND replace(manifest_logical_uri, '\\', '') = manifest_logical_uri "
            "AND identity_spool_logical_uri LIKE '.runtime/gate3c-fd1/recovery/%' "
            "AND identity_spool_logical_uri NOT LIKE '%..%' "
            "AND replace(identity_spool_logical_uri, '\\', '') = "
            "identity_spool_logical_uri",
            name="ck_fd1_recovery_run_uris",
        ),
        CheckConstraint(
            "status IN ('PLANNED', 'RUNNING', 'STOPPED', 'FAILED', "
            "'REHEARSAL_SUCCEEDED', 'FINALIZED')",
            name="ck_fd1_recovery_run_status",
        ),
        CheckConstraint(
            f"(active_stage IS NULL OR active_stage IN ({_STAGES_SQL})) "
            f"AND (last_completed_stage IS NULL OR "
            f"last_completed_stage IN ({_STAGES_SQL}))",
            name="ck_fd1_recovery_run_stages",
        ),
        CheckConstraint(
            "identity_spool_row_count > 0 "
            "AND max_runtime_minutes >= 1 AND max_runtime_minutes <= 120 "
            "AND heartbeat_seconds >= 1 AND heartbeat_seconds <= 30 "
            "AND provider_calls = 0",
            name="ck_fd1_recovery_run_bounds",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_fd1_recovery_run_lease",
        ),
        CheckConstraint(
            "(status = 'RUNNING' AND started_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND deadline_at IS NOT NULL) "
            "OR status <> 'RUNNING'",
            name="ck_fd1_recovery_run_running_times",
        ),
        CheckConstraint(
            "(status = 'FINALIZED' AND finalized_at IS NOT NULL "
            "AND last_completed_stage = 'FINALIZED' AND active_stage IS NULL) "
            "OR (status <> 'FINALIZED' AND finalized_at IS NULL)",
            name="ck_fd1_recovery_run_finalization",
        ),
        UniqueConstraint(
            "manifest_checksum", name="uq_fd1_recovery_run_manifest"
        ),
        Index("ix_fd1_recovery_run_status_heartbeat", "status", "heartbeat_at"),
        Index("ix_fd1_recovery_run_stage", "last_completed_stage", "active_stage"),
    )


class FD1RecoveryStageCheckpoint(Base):
    """One durable attempt at one ordered FULL or REHEARSAL recovery stage."""

    __tablename__ = "fd1_recovery_stage_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    stage_sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    stage_name: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    output_checksum: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    performance_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    resume_token: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_type: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "scope IN ('FULL', 'REHEARSAL')", name="ck_fd1_recovery_stage_scope"
        ),
        CheckConstraint(
            "(stage_sequence = 1 AND stage_name = 'FILES_VALIDATED') OR "
            "(stage_sequence = 2 AND stage_name = 'IDENTITY_STAGED') OR "
            "(stage_sequence = 3 AND stage_name = 'IDENTITY_RECONCILED') OR "
            "(stage_sequence = 4 AND stage_name = 'PARTITIONS_STAGED') OR "
            "(stage_sequence = 5 AND stage_name = 'WAREHOUSE_VALIDATED') OR "
            "(stage_sequence = 6 AND stage_name = 'FINALIZED')",
            name="ck_fd1_recovery_stage_sequence",
        ),
        CheckConstraint(
            "attempt >= 1 AND status IN "
            "('PENDING', 'RUNNING', 'SUCCEEDED', 'STOPPED', 'FAILED')",
            name="ck_fd1_recovery_stage_attempt_status",
        ),
        CheckConstraint(
            "length(input_checksum) = 64 "
            "AND (output_checksum IS NULL OR length(output_checksum) = 64)",
            name="ck_fd1_recovery_stage_checksums",
        ),
        CheckConstraint(
            "(status = 'PENDING' AND started_at IS NULL "
            "AND heartbeat_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'RUNNING' AND started_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('SUCCEEDED', 'STOPPED', 'FAILED') "
            "AND started_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND completed_at >= started_at)",
            name="ck_fd1_recovery_stage_times",
        ),
        CheckConstraint(
            "(status = 'SUCCEEDED' AND output_checksum IS NOT NULL "
            "AND error_type IS NULL AND error_message IS NULL) "
            "OR status <> 'SUCCEEDED'",
            name="ck_fd1_recovery_stage_success",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["fd1_recovery_runs.id"],
            name="fk_fd1_recovery_stage_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "run_id",
            "scope",
            "stage_name",
            "attempt",
            name="uq_fd1_recovery_stage_attempt",
        ),
        Index(
            "uq_fd1_recovery_stage_success_once",
            "run_id",
            "scope",
            "stage_name",
            unique=True,
            postgresql_where=text("status = 'SUCCEEDED'"),
            sqlite_where=text("status = 'SUCCEEDED'"),
        ),
        Index(
            "ix_fd1_recovery_stage_progress",
            "run_id",
            "scope",
            "stage_sequence",
            "status",
        ),
        Index("ix_fd1_recovery_stage_heartbeat", "status", "heartbeat_at"),
    )


class FD1RecoveryManifestFile(Base):
    """Immutable cached validation evidence for one manifested Parquet file."""

    __tablename__ = "fd1_recovery_manifest_files"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    schema_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    min_event_date: Mapped[date | None] = mapped_column(Date)
    max_event_date: Mapped[date | None] = mapped_column(Date)
    event_family: Mapped[str] = mapped_column(String(32), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    partition_identity_checksum: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    validation_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "ordinal >= 1 AND size_bytes > 0 AND mtime_ns >= 0 AND row_count > 0",
            name="ck_fd1_recovery_file_counts",
        ),
        CheckConstraint(
            "relative_path LIKE 'historical-events/%' "
            "AND relative_path LIKE '%.parquet' "
            "AND relative_path NOT LIKE '/%' "
            "AND relative_path NOT LIKE '%..%' "
            "AND replace(relative_path, '\\', '') = relative_path",
            name="ck_fd1_recovery_file_uri",
        ),
        CheckConstraint(
            "length(file_sha256) = 64 "
            "AND length(schema_fingerprint) = 64 "
            "AND length(partition_identity_checksum) = 64",
            name="ck_fd1_recovery_file_checksums",
        ),
        CheckConstraint(
            f"event_family IN ({_EVENT_FAMILIES_SQL})",
            name="ck_fd1_recovery_file_family",
        ),
        CheckConstraint(
            "season >= 1876 AND month >= 1 AND month <= 12",
            name="ck_fd1_recovery_file_partition",
        ),
        CheckConstraint(
            "(min_event_date IS NULL AND max_event_date IS NULL) OR "
            "(min_event_date IS NOT NULL AND max_event_date IS NOT NULL "
            "AND max_event_date >= min_event_date)",
            name="ck_fd1_recovery_file_dates",
        ),
        CheckConstraint(
            "validation_mode IN ('CACHE_REUSE', 'REHASH')",
            name="ck_fd1_recovery_file_validation_mode",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["fd1_recovery_runs.id"],
            name="fk_fd1_recovery_file_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "run_id", "ordinal", name="uq_fd1_recovery_file_ordinal"
        ),
        UniqueConstraint(
            "run_id", "relative_path", name="uq_fd1_recovery_file_path"
        ),
        UniqueConstraint(
            "run_id",
            "partition_identity_checksum",
            name="uq_fd1_recovery_file_partition_identity",
        ),
        Index(
            "ix_fd1_recovery_file_partition",
            "run_id",
            "event_family",
            "season",
            "month",
        ),
    )


class FD1RecoveryIdentityStage(Base):
    """Logged run-scoped COPY target for set-based identity reconciliation."""

    __tablename__ = "fd1_recovery_identity_stage"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, nullable=False
    )
    scope: Mapped[str] = mapped_column(
        String(16), primary_key=True, nullable=False
    )
    staging_ordinal: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, nullable=False
    )
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    source_key: Mapped[str] = mapped_column(String(80), nullable=False)
    batch_number: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    source_release_identity_checksum: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_namespace: Mapped[str] = mapped_column(String(80), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_namespace: Mapped[str] = mapped_column(String(80), nullable=False)
    canonical_id: Mapped[str | None] = mapped_column(String(255))
    source_row_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_row_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[str] = mapped_column(String(24), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_through: Mapped[date | None] = mapped_column(Date)
    evidence_logical_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    mapping_basis: Mapped[str] = mapped_column(String(120), nullable=False)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    record_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    target_mapping_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    outcome: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    matched_mapping_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    matched_stage_ordinal: Mapped[int | None] = mapped_column(BigInteger)
    reason_code: Mapped[str | None] = mapped_column(String(80))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "scope IN ('FULL', 'REHEARSAL')",
            name="ck_fd1_recovery_identity_scope",
        ),
        CheckConstraint(
            "staging_ordinal >= 1 AND batch_number >= 1 AND source_row_number >= 1",
            name="ck_fd1_recovery_identity_ordinals",
        ),
        CheckConstraint(
            "entity_type IN ('person', 'team', 'franchise', 'park', 'game', 'umpire')",
            name="ck_fd1_recovery_identity_entity",
        ),
        CheckConstraint(
            "confidence IN ('EXACT', 'HIGH', 'UNRESOLVED', 'CONFLICT')",
            name="ck_fd1_recovery_identity_confidence",
        ),
        CheckConstraint(
            "(confidence IN ('EXACT', 'HIGH') AND canonical_id IS NOT NULL) "
            "OR (confidence IN ('UNRESOLVED', 'CONFLICT') "
            "AND canonical_id IS NULL)",
            name="ck_fd1_recovery_identity_resolution",
        ),
        CheckConstraint(
            "valid_through IS NULL OR valid_from IS NULL OR valid_through >= valid_from",
            name="ck_fd1_recovery_identity_window",
        ),
        CheckConstraint(
            "evidence_logical_uri NOT LIKE '/%' "
            "AND evidence_logical_uri NOT LIKE '%..%' "
            "AND replace(evidence_logical_uri, '\\', '') = evidence_logical_uri",
            name="ck_fd1_recovery_identity_uri",
        ),
        CheckConstraint(
            "length(source_release_identity_checksum) = 64 "
            "AND length(batch_checksum) = 64 "
            "AND length(source_row_checksum) = 64 "
            "AND length(identity_checksum) = 64 "
            "AND length(record_checksum) = 64",
            name="ck_fd1_recovery_identity_checksums",
        ),
        CheckConstraint(
            "outcome IN ('PENDING', 'INSERT', 'REUSE', 'UNRESOLVED', 'CONFLICT')",
            name="ck_fd1_recovery_identity_outcome",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "matched_stage_ordinal IS NULL OR matched_stage_ordinal <> staging_ordinal",
            name="ck_fd1_recovery_identity_stage_match",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["fd1_recovery_runs.id"],
            name="fk_fd1_recovery_identity_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_version_id"],
            ["historical_source_versions.id"],
            name="fk_fd1_recovery_identity_source_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["matched_mapping_id"],
            ["open_data_identity_mappings.id"],
            name="fk_fd1_recovery_identity_mapping_match",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "scope", "matched_stage_ordinal"],
            [
                "fd1_recovery_identity_stage.run_id",
                "fd1_recovery_identity_stage.scope",
                "fd1_recovery_identity_stage.staging_ordinal",
            ],
            name="fk_fd1_recovery_identity_stage_match",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_fd1_recovery_identity_batch",
            "run_id",
            "scope",
            "source_key",
            "batch_number",
            "batch_checksum",
        ),
        Index(
            "ix_fd1_recovery_identity_natural",
            "run_id",
            "scope",
            "source_version_id",
            "entity_type",
            "source_namespace",
            "external_id",
            "source_row_number",
        ),
        Index(
            "ix_fd1_recovery_identity_checksum",
            "run_id",
            "scope",
            "identity_checksum",
        ),
        Index(
            "ix_fd1_recovery_identity_outcome", "run_id", "scope", "outcome"
        ),
        Index(
            "ix_fd1_recovery_identity_coverage",
            "run_id",
            "scope",
            "entity_type",
            "confidence",
        ),
    )


class FD1RecoveryIdentityQuarantine(Base):
    """Append-only evidence for unresolved or conflicting staged identities."""

    __tablename__ = "fd1_recovery_identity_quarantine"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    staging_ordinal: Mapped[int] = mapped_column(BigInteger, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    staged_record_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    existing_mapping_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    existing_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    evidence_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "scope IN ('FULL', 'REHEARSAL')",
            name="ck_fd1_recovery_quarantine_scope",
        ),
        CheckConstraint(
            "outcome IN ('UNRESOLVED', 'CONFLICT')",
            name="ck_fd1_recovery_quarantine_outcome",
        ),
        CheckConstraint(
            "length(staged_record_checksum) = 64 "
            "AND length(evidence_checksum) = 64",
            name="ck_fd1_recovery_quarantine_checksums",
        ),
        ForeignKeyConstraint(
            ["run_id", "scope", "staging_ordinal"],
            [
                "fd1_recovery_identity_stage.run_id",
                "fd1_recovery_identity_stage.scope",
                "fd1_recovery_identity_stage.staging_ordinal",
            ],
            name="fk_fd1_recovery_quarantine_stage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["existing_mapping_id"],
            ["open_data_identity_mappings.id"],
            name="fk_fd1_recovery_quarantine_existing_mapping",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "run_id",
            "scope",
            "staging_ordinal",
            "reason_code",
            name="uq_fd1_recovery_quarantine_reason",
        ),
        Index(
            "ix_fd1_recovery_quarantine_outcome",
            "run_id",
            "scope",
            "outcome",
            "reason_code",
        ),
    )


class FD1RecoveryPublicationStage(Base):
    """Validated, invisible target payloads for the final atomic promotion."""

    __tablename__ = "fd1_recovery_publication_stage"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    object_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    promotion_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    natural_key_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_identity_checksum: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    target_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    matched_target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    reason_code: Mapped[str | None] = mapped_column(String(80))
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "scope IN ('FULL', 'REHEARSAL')",
            name="ck_fd1_recovery_publication_scope",
        ),
        CheckConstraint("ordinal >= 1", name="ck_fd1_recovery_publication_ordinal"),
        CheckConstraint(
            "(object_kind = 'DERIVED_USE_DECISION' AND promotion_order = 10) OR "
            "(object_kind = 'INGESTION_RUN' AND promotion_order = 20) OR "
            "(object_kind = 'CHECKPOINT' AND promotion_order = 30) OR "
            "(object_kind = 'PARTITION' AND promotion_order = 40)",
            name="ck_fd1_recovery_publication_order",
        ),
        CheckConstraint(
            "length(natural_key_checksum) = 64 "
            "AND length(semantic_identity_checksum) = 64 "
            "AND length(payload_checksum) = 64",
            name="ck_fd1_recovery_publication_checksums",
        ),
        CheckConstraint(
            "action IN ('PENDING', 'INSERT', 'REUSE', 'CONFLICT')",
            name="ck_fd1_recovery_publication_action",
        ),
        CheckConstraint(
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
        ForeignKeyConstraint(
            ["run_id"],
            ["fd1_recovery_runs.id"],
            name="fk_fd1_recovery_publication_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "run_id", "scope", "ordinal", name="uq_fd1_recovery_publication_ordinal"
        ),
        UniqueConstraint(
            "run_id",
            "scope",
            "object_kind",
            "target_id",
            name="uq_fd1_recovery_publication_target",
        ),
        UniqueConstraint(
            "run_id",
            "scope",
            "object_kind",
            "semantic_identity_checksum",
            name="uq_fd1_recovery_publication_identity",
        ),
        Index(
            "ix_fd1_recovery_publication_order",
            "run_id",
            "scope",
            "promotion_order",
            "action",
        ),
        Index(
            "ix_fd1_recovery_publication_payload",
            "run_id",
            "scope",
            "payload_checksum",
        ),
    )


class FD1RecoveryImmutableRecordError(RuntimeError):
    """Raised when immutable recovery evidence is updated or deleted."""


FD1_RECOVERY_APPEND_ONLY_TYPES = (
    FD1RecoveryManifestFile,
    FD1RecoveryIdentityQuarantine,
)


def _reject_recovery_evidence_mutation(
    mapper: object, connection: object, target: object
) -> None:
    del mapper, connection
    raise FD1RecoveryImmutableRecordError(
        f"{type(target).__name__} recovery evidence is append-only"
    )


for _append_only_type in FD1_RECOVERY_APPEND_ONLY_TYPES:
    event.listen(
        _append_only_type,
        "before_update",
        _reject_recovery_evidence_mutation,
        propagate=True,
    )
    event.listen(
        _append_only_type,
        "before_delete",
        _reject_recovery_evidence_mutation,
        propagate=True,
    )


__all__ = [
    "FD1_RECOVERY_APPEND_ONLY_TYPES",
    "FD1RecoveryIdentityQuarantine",
    "FD1RecoveryIdentityStage",
    "FD1RecoveryImmutableRecordError",
    "FD1RecoveryManifestFile",
    "FD1RecoveryPublicationStage",
    "FD1RecoveryRun",
    "FD1RecoveryStageCheckpoint",
    "RECOVERY_PUBLICATION_ORDER",
    "RECOVERY_SCOPES",
    "RECOVERY_STAGES",
    "RECOVERY_STAGE_SEQUENCE",
]
