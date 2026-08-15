from __future__ import annotations

from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex, CreateTable

from app.gen2.fd1_recovery_models import (
    FD1_RECOVERY_APPEND_ONLY_TYPES,
    FD1RecoveryIdentityQuarantine,
    FD1RecoveryIdentityStage,
    FD1RecoveryImmutableRecordError,
    FD1RecoveryManifestFile,
    FD1RecoveryPublicationStage,
    FD1RecoveryRun,
    FD1RecoveryStageCheckpoint,
    RECOVERY_SCOPES,
    RECOVERY_STAGES,
    RECOVERY_STAGE_SEQUENCE,
)


RECOVERY_TABLES = {
    "fd1_recovery_runs",
    "fd1_recovery_stage_checkpoints",
    "fd1_recovery_manifest_files",
    "fd1_recovery_identity_stage",
    "fd1_recovery_identity_quarantine",
    "fd1_recovery_publication_stage",
}
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _config(database_url: str, *, output: StringIO | None = None) -> Config:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _run_values() -> dict[str, object]:
    return {
        "id": uuid4(),
        "manifest_checksum": "a" * 64,
        "manifest_schema_version": "gate3c_fd1_recovery_manifest_v1",
        "manifest_logical_uri": (
            ".runtime/gate3c-fd1/recovery/manifests/manifest.json"
        ),
        "manifest_header": {"file_count": 329},
        "hash_cache_schema_version": "gate3c_fd1_hash_cache_v1",
        "hash_cache_checksum": "b" * 64,
        "generation_code_revision": "c" * 64,
        "finalizer_code_revision": "d" * 64,
        "identity_spool_logical_uri": (
            ".runtime/gate3c-fd1/recovery/identity/mappings.csv"
        ),
        "identity_spool_sha256": "e" * 64,
        "identity_spool_row_count": 737_259,
        "status": "PLANNED",
        "progress": {},
        "performance_metrics": {},
    }


def test_recovery_migration_is_one_linear_head() -> None:
    config = _config("sqlite://")
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "20260815_0015"
    recovery = script.get_revision("20260815_0015")
    assert recovery is not None
    assert recovery.down_revision == "20260814_0014"
    assert not recovery.branch_labels


def test_recovery_models_register_only_six_isolated_tables_with_exact_contract() -> None:
    model_tables = {
        model.__tablename__
        for model in (
            FD1RecoveryRun,
            FD1RecoveryStageCheckpoint,
            FD1RecoveryManifestFile,
            FD1RecoveryIdentityStage,
            FD1RecoveryIdentityQuarantine,
            FD1RecoveryPublicationStage,
        )
    }
    assert model_tables == RECOVERY_TABLES
    assert RECOVERY_SCOPES == ("FULL", "REHEARSAL")
    assert RECOVERY_STAGES == (
        "FILES_VALIDATED",
        "IDENTITY_STAGED",
        "IDENTITY_RECONCILED",
        "PARTITIONS_STAGED",
        "WAREHOUSE_VALIDATED",
        "FINALIZED",
    )
    assert RECOVERY_STAGE_SEQUENCE == {
        stage: sequence for sequence, stage in enumerate(RECOVERY_STAGES, start=1)
    }

    identity_columns = set(FD1RecoveryIdentityStage.__table__.columns.keys())
    assert {
        "source_key",
        "source_version_id",
        "batch_number",
        "batch_checksum",
        "entity_type",
        "source_namespace",
        "external_id",
        "canonical_namespace",
        "canonical_id",
        "source_row_number",
        "source_row_checksum",
        "confidence",
        "valid_from",
        "valid_through",
        "evidence_logical_uri",
        "identity_checksum",
        "mapping_basis",
        "record_checksum",
        "outcome",
    } <= identity_columns
    assert FD1_RECOVERY_APPEND_ONLY_TYPES == (
        FD1RecoveryManifestFile,
        FD1RecoveryIdentityQuarantine,
    )

    publication_columns = set(FD1RecoveryPublicationStage.__table__.columns.keys())
    assert {
        "object_kind",
        "promotion_order",
        "target_id",
        "natural_key_checksum",
        "semantic_identity_checksum",
        "target_payload",
        "payload_checksum",
        "action",
    } <= publication_columns


def test_sqlite_migration_round_trip_check_and_fail_closed_downgrade(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = (artifact_root / "fd1-recovery-schema.db").resolve()
    database_url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = _config(database_url)

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert RECOVERY_TABLES <= set(inspector.get_table_names())

    identity_indexes = {
        row["name"]: tuple(row["column_names"])
        for row in inspector.get_indexes("fd1_recovery_identity_stage")
    }
    assert identity_indexes["ix_fd1_recovery_identity_batch"] == (
        "run_id",
        "scope",
        "source_key",
        "batch_number",
        "batch_checksum",
    )
    assert identity_indexes["ix_fd1_recovery_identity_natural"] == (
        "run_id",
        "scope",
        "source_version_id",
        "entity_type",
        "source_namespace",
        "external_id",
        "source_row_number",
    )
    assert identity_indexes["ix_fd1_recovery_identity_checksum"] == (
        "run_id",
        "scope",
        "identity_checksum",
    )

    stage_indexes = {
        row["name"]: row
        for row in inspector.get_indexes("fd1_recovery_stage_checkpoints")
    }
    assert stage_indexes["uq_fd1_recovery_stage_success_once"]["unique"] == 1

    quarantine_fks = {
        row["name"]: row
        for row in inspector.get_foreign_keys(
            "fd1_recovery_identity_quarantine"
        )
    }
    assert quarantine_fks["fk_fd1_recovery_quarantine_stage"][
        "referred_table"
    ] == "fd1_recovery_identity_stage"
    assert tuple(
        quarantine_fks["fk_fd1_recovery_quarantine_stage"]["constrained_columns"]
    ) == ("run_id", "scope", "staging_ordinal")
    engine.dispose()

    command.check(config)
    command.downgrade(config, "20260814_0014")
    downgraded = create_engine(database_url)
    assert RECOVERY_TABLES.isdisjoint(inspect(downgraded).get_table_names())
    downgraded.dispose()

    command.upgrade(config, "head")
    command.check(config)
    populated = create_engine(database_url)
    values = _run_values()
    statement = text(
        "INSERT INTO fd1_recovery_runs ("
        "id, manifest_checksum, manifest_schema_version, manifest_logical_uri, "
        "manifest_header, hash_cache_schema_version, hash_cache_checksum, "
        "generation_code_revision, finalizer_code_revision, "
        "identity_spool_logical_uri, identity_spool_sha256, "
        "identity_spool_row_count, status, progress, performance_metrics"
        ") VALUES ("
        ":id, :manifest_checksum, :manifest_schema_version, "
        ":manifest_logical_uri, :manifest_header, :hash_cache_schema_version, "
        ":hash_cache_checksum, :generation_code_revision, "
        ":finalizer_code_revision, :identity_spool_logical_uri, "
        ":identity_spool_sha256, :identity_spool_row_count, :status, "
        ":progress, :performance_metrics)"
    )
    with populated.begin() as connection:
        connection.execute(
            statement,
            {
                **values,
                "id": str(values["id"]),
                "manifest_header": '{"file_count":329}',
                "progress": "{}",
                "performance_metrics": "{}",
            },
        )
    populated.dispose()

    with pytest.raises(
        RuntimeError,
        match="Cannot downgrade FD1 recovery finalization while recovery evidence exists",
    ):
        command.downgrade(config, "20260814_0014")


def test_sqlite_partial_success_index_separates_full_and_rehearsal(
    session,
) -> None:
    run = FD1RecoveryRun(**_run_values())
    session.add(run)
    session.commit()

    def checkpoint(scope: str, attempt: int) -> FD1RecoveryStageCheckpoint:
        return FD1RecoveryStageCheckpoint(
            run_id=run.id,
            scope=scope,
            stage_sequence=1,
            stage_name="FILES_VALIDATED",
            attempt=attempt,
            status="SUCCEEDED",
            input_checksum="1" * 64,
            output_checksum="2" * 64,
            started_at=NOW,
            heartbeat_at=NOW,
            completed_at=NOW,
            progress={},
            performance_metrics={},
        )

    session.add_all([checkpoint("FULL", 1), checkpoint("REHEARSAL", 1)])
    session.commit()
    session.add(checkpoint("FULL", 2))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_manifest_file_recovery_evidence_is_append_only(session) -> None:
    run = FD1RecoveryRun(**_run_values())
    session.add(run)
    session.commit()
    row = FD1RecoveryManifestFile(
        run_id=run.id,
        ordinal=1,
        relative_path=(
            "historical-events/source=retrosheet/family=weather/season=2025/"
            "month=07/part-" + "1" * 64 + ".parquet"
        ),
        size_bytes=1024,
        mtime_ns=1,
        file_sha256="1" * 64,
        schema_version="weather-v1",
        schema_fingerprint="2" * 64,
        row_count=1,
        min_event_date=date(2025, 7, 1),
        max_event_date=date(2025, 7, 1),
        event_family="weather",
        season=2025,
        month=7,
        partition_identity_checksum="3" * 64,
        validation_mode="CACHE_REUSE",
        validated_at=NOW,
    )
    session.add(row)
    session.commit()

    row.row_count = 2
    with pytest.raises(FD1RecoveryImmutableRecordError, match="append-only"):
        session.commit()
    session.rollback()

    persisted = session.get(FD1RecoveryManifestFile, row.id)
    assert persisted is not None
    session.delete(persisted)
    with pytest.raises(FD1RecoveryImmutableRecordError, match="append-only"):
        session.commit()
    session.rollback()


def test_postgresql_offline_sql_has_copy_reconciliation_keys_and_atomic_fks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    database_url = "postgresql+psycopg://asp:asp@localhost/asp"
    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(
        _config(database_url, output=output),
        "20260814_0014:20260815_0015",
        sql=True,
    )
    generated = " ".join(output.getvalue().split())
    for table_name in sorted(RECOVERY_TABLES):
        assert f"CREATE TABLE {table_name}" in generated
    assert "scope VARCHAR(16) NOT NULL" in generated
    assert "batch_number INTEGER NOT NULL" in generated
    assert "batch_checksum VARCHAR(64) NOT NULL" in generated
    assert "provider_calls INTEGER DEFAULT '0' NOT NULL" in generated
    assert (
        "FOREIGN KEY(run_id, scope, matched_stage_ordinal) REFERENCES "
        "fd1_recovery_identity_stage (run_id, scope, staging_ordinal) "
        "ON DELETE RESTRICT"
    ) in generated
    assert (
        "CREATE UNIQUE INDEX uq_fd1_recovery_stage_success_once ON "
        "fd1_recovery_stage_checkpoints (run_id, scope, stage_name) "
        "WHERE status = 'SUCCEEDED'"
    ) in generated
    assert (
        "CREATE INDEX ix_fd1_recovery_identity_natural ON "
        "fd1_recovery_identity_stage (run_id, scope, source_version_id, "
        "entity_type, source_namespace, external_id, source_row_number)"
    ) in generated
    assert (
        "OR (confidence IN ('UNRESOLVED', 'CONFLICT') "
        "AND canonical_id IS NULL)"
    ) in generated


def test_postgresql_model_ddl_carries_named_checks_fks_and_partial_index() -> None:
    dialect = postgresql.dialect()
    identity_table = FD1RecoveryIdentityStage.__table__
    identity_ddl = str(CreateTable(identity_table).compile(dialect=dialect))
    normalized_identity_ddl = " ".join(identity_ddl.split())
    assert "ck_fd1_recovery_identity_reconciliation" in identity_ddl
    assert "fk_fd1_recovery_identity_stage_match" in identity_ddl
    assert "PRIMARY KEY (run_id, scope, staging_ordinal)" in identity_ddl
    assert (
        "OR (confidence IN ('UNRESOLVED', 'CONFLICT') "
        "AND canonical_id IS NULL)"
    ) in normalized_identity_ddl

    checks = {
        constraint.name
        for constraint in identity_table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_fd1_recovery_identity_checksums",
        "ck_fd1_recovery_identity_outcome",
        "ck_fd1_recovery_identity_resolution",
        "ck_fd1_recovery_identity_reconciliation",
    } <= checks
    foreign_keys = {
        constraint.name: constraint
        for constraint in identity_table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert foreign_keys["fk_fd1_recovery_identity_stage_match"].ondelete == "RESTRICT"
    assert tuple(
        element.parent.name
        for element in foreign_keys["fk_fd1_recovery_identity_stage_match"].elements
    ) == ("run_id", "scope", "matched_stage_ordinal")

    success_index = next(
        index
        for index in FD1RecoveryStageCheckpoint.__table__.indexes
        if index.name == "uq_fd1_recovery_stage_success_once"
    )
    success_ddl = str(CreateIndex(success_index).compile(dialect=dialect))
    assert success_index.unique is True
    assert "WHERE status = 'SUCCEEDED'" in success_ddl
