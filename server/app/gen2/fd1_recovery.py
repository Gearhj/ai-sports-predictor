"""Resumable finalization of an interrupted Gate 3C-FD1 publication.

The recovery command consumes only a frozen recovery manifest, its compact
identity spool, and the already-created Parquet files.  It cannot construct a
provider, discover raw data, materialize rows, or write Parquet.  Durable
recovery tables hold resumable work; the canonical warehouse tables change
only in the final atomic transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Engine, event, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.gen2.fd1_recovery_manifest import (
    DEFAULT_EXPECTED_PARQUET_FILES,
    RECOVERY_HASH_CACHE_SCHEMA_VERSION,
    RecoveryManifest,
    load_recovery_manifest,
    manifest_paths,
)
from app.gen2.fd1_recovery_models import (
    FD1RecoveryManifestFile,
    FD1RecoveryRun,
    FD1RecoveryStageCheckpoint,
    RECOVERY_STAGE_SEQUENCE,
    RECOVERY_STAGES,
)
from app.gen2.fd1_recovery_progress import (
    FD1RecoveryProgress,
    FD1RecoveryStop,
    SAFE_STOP_EXIT_CODE,
)
from app.gen2.fd1_recovery_sql import (
    IdentityCopyMetrics,
    IdentityPromotionMetrics,
    IdentityReconciliationMetrics,
    copy_identity_spool,
    promote_identity_mappings,
    reconcile_identity_stage,
)


RECOVERY_COMMAND_SCHEMA_VERSION = "gate3c_fd1_recovery_finalizer_v1"
RECOVERY_REPORT_SCHEMA_VERSION = "gate3c_fd1_recovery_result_v1"
RECOVERY_NAMESPACE = UUID("4b97b99f-942a-5eba-8855-fde55dd107f7")
DEFAULT_STORE_ROOT = Path(__file__).resolve().parents[3] / "data" / "curated" / "gen2-fd1"
DEFAULT_RUNTIME_ROOT = (
    Path(__file__).resolve().parents[3] / ".runtime" / "gate3c-fd1" / "recovery"
)
DEFAULT_IDENTITY_BATCH_SIZE = 100_000
MAX_IDENTITY_BATCH_SIZE = 250_000
MINIMUM_REHEARSAL_MAPPINGS = 5_000

FD1_RECOVERY_FINALIZER_CODE_REVISION_INPUTS = (
    "alembic/versions/20260814_0013_gate3c_fd1_partition_families.py",
    "alembic/versions/20260814_0014_fd1_identity_mapping_natural_key.py",
    "alembic/versions/20260815_0015_fd1_recovery_finalization.py",
    "app/cli.py",
    "app/gen2/context.py",
    "app/gen2/event_taxonomy.py",
    "app/gen2/fd1_identity.py",
    "app/gen2/fd1_materializer.py",
    "app/gen2/fd1_park_factors.py",
    "app/gen2/fd1_reconciliation.py",
    "app/gen2/fd1_recovery.py",
    "app/gen2/fd1_recovery_identity.py",
    "app/gen2/fd1_recovery_manifest.py",
    "app/gen2/fd1_recovery_models.py",
    "app/gen2/fd1_recovery_progress.py",
    "app/gen2/fd1_recovery_publication.py",
    "app/gen2/fd1_recovery_sql.py",
    "app/gen2/fd1_recovery_validation.py",
    "app/gen2/fd1_registry_sync.py",
    "scripts/prepare_fd1_recovery.py",
    "app/gen2/open_data_acquisition.py",
    "app/gen2/open_data_registry.py",
    "app/gen2/retrosheet.py",
    "app/gen2/source_authorization.py",
    "app/historical/checkpoints.py",
    # Semantic dependencies used to reconstruct the original publication.
    "app/gen2/fd1_warehouse.py",
    "app/gen2/parquet_store.py",
    "app/models/entities.py",
)


class FD1RecoveryError(RuntimeError):
    """Recovery evidence, stage state, or final promotion failed closed."""


@dataclass(frozen=True, slots=True)
class FD1RecoveryOptions:
    manifest_path: Path
    store_root: Path = DEFAULT_STORE_ROOT
    resume: bool = False
    validate_only: bool = False
    sample_rehearsal: bool = False
    max_runtime_minutes: int = 120
    heartbeat_seconds: int = 30
    identity_batch_size: int = DEFAULT_IDENTITY_BATCH_SIZE
    run_id: UUID | None = None
    active_identity_report: Path | None = None

    def __post_init__(self) -> None:
        if self.max_runtime_minutes < 1 or self.max_runtime_minutes > 120:
            raise ValueError("max_runtime_minutes must be between 1 and 120")
        if self.heartbeat_seconds < 1 or self.heartbeat_seconds > 30:
            raise ValueError("heartbeat_seconds must be between 1 and 30")
        if (
            self.identity_batch_size < 1
            or self.identity_batch_size > MAX_IDENTITY_BATCH_SIZE
        ):
            raise ValueError(
                f"identity_batch_size must be between 1 and {MAX_IDENTITY_BATCH_SIZE}"
            )
        if self.validate_only and self.sample_rehearsal:
            raise ValueError("validate_only and sample_rehearsal are mutually exclusive")


@dataclass(frozen=True, slots=True)
class FD1RecoveryResult:
    schema_version: str
    run_id: str
    recovery_manifest_checksum: str
    generation_code_revision: str
    finalizer_code_revision: str
    mode: str
    status: str
    last_completed_stage: str | None
    files_reused: int
    bytes_reused: int
    parquet_rows: int
    mappings_staged: int
    mappings_inserted: int
    mappings_reused: int
    unresolved_mappings: int
    source_declared_conflicts: int
    mapping_conflicts: int
    finalized_partitions: int
    provider_calls: int
    network_requests: int
    provider_transports_constructed: int
    elapsed_seconds: float
    peak_rss_bytes: int
    idempotent_reuse: bool
    performance: Mapping[str, Any]
    validation: Mapping[str, Any]
    result_checksum: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_fd1_recovery_finalizer_revision() -> str:
    """Hash recovery code separately from the generation content revision."""

    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for name in sorted(FD1_RECOVERY_FINALIZER_CODE_REVISION_INPUTS):
        path = root / name
        payload = path.read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def finalize_fd1_from_existing_parquet(
    engine: Engine,
    *,
    options: FD1RecoveryOptions,
) -> FD1RecoveryResult:
    """Measure and run one provider-free recovery invocation."""

    database_metrics = _RecoveryDatabaseMetrics(engine)
    database_metrics.start()
    try:
        return _finalize_fd1_from_existing_parquet(
            engine,
            options=options,
            database_metrics=database_metrics,
        )
    finally:
        database_metrics.close()


def _finalize_fd1_from_existing_parquet(
    engine: Engine,
    *,
    options: FD1RecoveryOptions,
    database_metrics: _RecoveryDatabaseMetrics,
) -> FD1RecoveryResult:
    """Run or resume the six-stage recovery without regenerating source data."""

    if engine.dialect.name != "postgresql":
        raise FD1RecoveryError("FD1 recovery finalization requires PostgreSQL")
    started = monotonic()
    deadline_at = started + options.max_runtime_minutes * 60.0
    manifest = load_recovery_manifest(options.manifest_path)
    finalizer_revision = build_fd1_recovery_finalizer_revision()
    if manifest.finalizer_lineage.code_revision != finalizer_revision:
        raise FD1RecoveryError(
            "Recovery manifest finalizer revision differs from the current code"
        )
    run_id = UUID(manifest.recovery_id)
    if options.run_id is not None and options.run_id != run_id:
        raise FD1RecoveryError("--run-id differs from the content-derived recovery ID")
    paths = manifest_paths(manifest, store_root=options.store_root)
    spool_path = _resolve_runtime_logical_uri(manifest.identity_spool.logical_uri)
    lease_owner = _lease_owner()
    _ensure_run_and_manifest_files(
        engine,
        manifest=manifest,
        manifest_path=options.manifest_path,
        finalizer_revision=finalizer_revision,
        options=options,
    )
    if not options.validate_only and not options.sample_rehearsal:
        _require_successful_rehearsal(engine, run_id=run_id)
    existing = _claim_lease(
        engine,
        run_id=run_id,
        lease_owner=lease_owner,
        options=options,
    )
    if existing["status"] == "FINALIZED":
        return _finalized_reuse_result(
            engine,
            manifest=manifest,
            finalizer_revision=finalizer_revision,
            started=started,
            database_metrics=database_metrics,
        )

    scope = "REHEARSAL" if options.sample_rehearsal else "FULL"
    log_path = DEFAULT_RUNTIME_ROOT / "runs" / f"{run_id}-{scope.lower()}.ndjson"
    progress = FD1RecoveryProgress(
        run_id=str(run_id),
        checkpoint_sink=_heartbeat_sink(
            engine,
            run_id=run_id,
            scope=scope,
            lease_owner=lease_owner,
        ),
        log_path=log_path,
        stage="FILES_VALIDATED",
        database_stage="manifest_validation",
        heartbeat_seconds=options.heartbeat_seconds,
        max_runtime_minutes=options.max_runtime_minutes,
        free_space_path=options.store_root,
    )
    copy_metrics: IdentityCopyMetrics | None = None
    reconciliation: IdentityReconciliationMetrics | None = None
    promotion: IdentityPromotionMetrics | None = None
    validation_result: Mapping[str, Any] = {}
    publication_metrics: Mapping[str, Any] = {}
    reconciliation_explain: Mapping[str, Any] = {}
    promotion_phase_seconds = 0.0
    try:
        progress.start()
        _run_files_validated_stage(
            engine,
            manifest=manifest,
            paths=paths,
            scope=scope,
            progress=progress,
        )
        if options.validate_only:
            progress.raise_if_stopped()
            validation_result = _validate_warehouse(
                manifest,
                store_root=options.store_root,
                active_identity_report=options.active_identity_report,
                full=True,
                progress=progress,
            )
            progress.raise_if_stopped()
            progress.close()
            _release_lease(
                engine,
                run_id=run_id,
                lease_owner=lease_owner,
                status="STOPPED",
                last_operation="VALIDATE_ONLY_COMPLETE",
            )
            return _build_result(
                engine,
                manifest=manifest,
                finalizer_revision=finalizer_revision,
                mode="validate-only",
                status="VALIDATED_NOT_FINALIZED",
                started=started,
                progress=progress,
                copy_metrics=None,
                reconciliation=None,
                promotion=None,
                validation=validation_result,
                publication_metrics={},
                idempotent_reuse=False,
                database_metrics=database_metrics,
                promotion_phase_seconds=0.0,
                reconciliation_explain={},
            )

        expected_mappings = (
            min(MINIMUM_REHEARSAL_MAPPINGS, manifest.identity_spool.row_count)
            if options.sample_rehearsal
            else manifest.identity_spool.row_count
        )
        checkpoint = _start_stage(
            engine,
            run_id=run_id,
            scope=scope,
            stage="IDENTITY_STAGED",
            input_checksum=manifest.identity_spool.sha256,
        )
        progress.update(
            stage="IDENTITY_STAGED",
            database_stage="postgresql_copy",
            mappings_total=expected_mappings,
            emit=True,
        )
        prior_output = _succeeded_stage_output(engine, checkpoint)
        if prior_output is not None:
            copy_metrics = IdentityCopyMetrics(**prior_output)
            progress.update(
                mappings_staged=copy_metrics.rows_staged,
                mappings_total=expected_mappings,
                emit=True,
            )
        else:
            progress.raise_if_stopped()
            try:
                copy_metrics = copy_identity_spool(
                    engine,
                    run_id=run_id,
                    scope=scope,
                    spool_path=spool_path,
                    expected_sha256=manifest.identity_spool.sha256,
                    expected_rows=expected_mappings,
                    batch_size=options.identity_batch_size,
                    checkpoint_id=checkpoint,
                    expected_size_bytes=manifest.identity_spool.byte_count,
                    row_limit=(
                        expected_mappings if options.sample_rehearsal else None
                    ),
                    progress=lambda current, total: progress.update(
                        mappings_staged=current,
                        mappings_total=total,
                    ),
                    statement_timeout_ms=lambda: _remaining_statement_timeout_ms(
                        deadline_at,
                        progress=progress,
                        maximum_ms=300_000,
                    ),
                )
            except Exception as exc:
                _stop_on_postgresql_query_cancel(
                    exc,
                    progress=progress,
                    deadline_at=deadline_at,
                )
                raise
            progress.raise_if_stopped()
            _complete_stage(
                engine,
                checkpoint_id=checkpoint,
                run_id=run_id,
                scope=scope,
                stage="IDENTITY_STAGED",
                output=_as_mapping(copy_metrics),
            )

        checkpoint = _start_stage(
            engine,
            run_id=run_id,
            scope=scope,
            stage="IDENTITY_RECONCILED",
            input_checksum=manifest.identity_spool.sha256,
        )
        progress.update(
            stage="IDENTITY_RECONCILED",
            database_stage="set_reconciliation",
            emit=True,
        )
        prior_output = _succeeded_stage_output(engine, checkpoint)
        if prior_output is not None:
            reconciliation = _reconciliation_metrics_from_output(prior_output)
            prior_explain = prior_output.get("explain_evidence", {})
            if not isinstance(prior_explain, Mapping):
                raise FD1RecoveryError(
                    "Succeeded identity reconciliation has invalid EXPLAIN evidence"
                )
            reconciliation_explain = dict(prior_explain)
        else:
            progress.update(database_stage="reconciliation_explain", emit=True)
            try:
                reconciliation_explain = _explain_reconciliation_queries(
                    engine,
                    run_id=run_id,
                    scope=scope,
                    deadline_at=deadline_at,
                    progress=progress,
                )
            except Exception as exc:
                _stop_on_postgresql_query_cancel(
                    exc,
                    progress=progress,
                    deadline_at=deadline_at,
                )
                raise
            progress.update(database_stage="set_reconciliation", emit=True)
            try:
                reconciliation = reconcile_identity_stage(
                    engine,
                    run_id=run_id,
                    scope=scope,
                    statement_timeout_ms=lambda: (
                        _remaining_statement_timeout_ms(
                            deadline_at,
                            progress=progress,
                            maximum_ms=1_200_000,
                        )
                    ),
                )
            except DBAPIError as exc:
                _stop_on_postgresql_query_cancel(
                    exc,
                    progress=progress,
                    deadline_at=deadline_at,
                )
                raise
        progress.raise_if_stopped()
        progress.update(
            inserted=reconciliation.planned_insert,
            reused=reconciliation.exact_reuse,
            unresolved=reconciliation.unresolved,
            conflicts=reconciliation.blocking_conflicts,
            emit=True,
        )
        if prior_output is None:
            _complete_stage(
                engine,
                checkpoint_id=checkpoint,
                run_id=run_id,
                scope=scope,
                stage="IDENTITY_RECONCILED",
                output={
                    **dict(_as_mapping(reconciliation)),
                    "explain_evidence": dict(reconciliation_explain),
                },
            )

        checkpoint = _start_stage(
            engine,
            run_id=run_id,
            scope=scope,
            stage="PARTITIONS_STAGED",
            input_checksum=manifest.manifest_checksum,
        )
        progress.update(
            stage="PARTITIONS_STAGED",
            database_stage="publication_plan",
            emit=True,
        )
        prior_output = _succeeded_stage_output(engine, checkpoint)
        if prior_output is not None:
            publication_metrics = prior_output
        else:
            progress.raise_if_stopped()
            try:
                publication_metrics = _stage_publication(
                    engine,
                    manifest=manifest,
                    manifest_path=options.manifest_path,
                    store_root=options.store_root,
                    scope=scope,
                    rehearsal=options.sample_rehearsal,
                    progress=progress,
                    deadline_at=deadline_at,
                )
            except Exception as exc:
                _stop_on_postgresql_query_cancel(
                    exc,
                    progress=progress,
                    deadline_at=deadline_at,
                )
                raise
            progress.raise_if_stopped()
            _complete_stage(
                engine,
                checkpoint_id=checkpoint,
                run_id=run_id,
                scope=scope,
                stage="PARTITIONS_STAGED",
                output=publication_metrics,
            )

        checkpoint = _start_stage(
            engine,
            run_id=run_id,
            scope=scope,
            stage="WAREHOUSE_VALIDATED",
            input_checksum=_checksum(publication_metrics),
        )
        progress.update(
            stage="WAREHOUSE_VALIDATED",
            database_stage="collective_validation",
            emit=True,
        )
        prior_output = _succeeded_stage_output(engine, checkpoint)
        if prior_output is not None:
            validation_result = prior_output
        else:
            progress.raise_if_stopped()
            validation_result = _validate_warehouse(
                manifest,
                store_root=options.store_root,
                active_identity_report=options.active_identity_report,
                full=not options.sample_rehearsal,
                progress=progress,
            )
            progress.raise_if_stopped()
            _complete_stage(
                engine,
                checkpoint_id=checkpoint,
                run_id=run_id,
                scope=scope,
                stage="WAREHOUSE_VALIDATED",
                output=validation_result,
            )

        checkpoint = _start_stage(
            engine,
            run_id=run_id,
            scope=scope,
            stage="FINALIZED",
            input_checksum=_checksum(
                {
                    "manifest": manifest.manifest_checksum,
                    "publication": publication_metrics,
                    "validation": validation_result,
                    "identity": _as_mapping(reconciliation),
                }
            ),
        )
        progress.update(
            stage="FINALIZED",
            database_stage=(
                "rehearsal_rollback" if options.sample_rehearsal else "atomic_promotion"
            ),
            emit=True,
        )
        prior_final_output = _succeeded_stage_output(engine, checkpoint)
        if options.sample_rehearsal:
            if prior_final_output is not None:
                promotion = _promotion_metrics_from_rehearsal_output(
                    prior_final_output
                )
                _restore_rehearsal_status(
                    engine,
                    run_id=run_id,
                    lease_owner=lease_owner,
                )
            else:
                try:
                    promotion, promotion_phase_seconds = _run_rehearsal_promotion(
                        engine,
                        run_id=run_id,
                        scope=scope,
                        progress=progress,
                        deadline_at=deadline_at,
                    )
                except DBAPIError as exc:
                    _stop_on_postgresql_query_cancel(
                        exc,
                        progress=progress,
                        deadline_at=deadline_at,
                    )
                    raise
                _complete_rehearsal(
                    engine,
                    checkpoint_id=checkpoint,
                    run_id=run_id,
                    lease_owner=lease_owner,
                    output=_as_mapping(promotion),
                )
            status = "REHEARSAL_SUCCEEDED"
            mode = "sample-rehearsal"
        else:
            if prior_final_output is not None:
                raise FD1RecoveryError(
                    "FINALIZED checkpoint exists without a finalized recovery run"
                )
            try:
                promotion, promotion_phase_seconds = _run_atomic_promotion(
                    engine,
                    run_id=run_id,
                    scope=scope,
                    checkpoint_id=checkpoint,
                    lease_owner=lease_owner,
                    manifest=manifest,
                    store_root=options.store_root,
                    validation=validation_result,
                    progress=progress,
                    deadline_at=deadline_at,
                )
            except DBAPIError as exc:
                _stop_on_postgresql_query_cancel(
                    exc,
                    progress=progress,
                    deadline_at=deadline_at,
                )
                raise
            status = "FINALIZED"
            mode = "finalize"
        progress.close()
        return _build_result(
            engine,
            manifest=manifest,
            finalizer_revision=finalizer_revision,
            mode=mode,
            status=status,
            started=started,
            progress=progress,
            copy_metrics=copy_metrics,
            reconciliation=reconciliation,
            promotion=promotion,
            validation=validation_result,
            publication_metrics=publication_metrics,
            idempotent_reuse=False,
            database_metrics=database_metrics,
            promotion_phase_seconds=promotion_phase_seconds,
            reconciliation_explain=reconciliation_explain,
        )
    except FD1RecoveryStop:
        _mark_stopped(
            engine,
            run_id=run_id,
            scope=scope,
            lease_owner=lease_owner,
            error_type="FD1RecoveryStop",
            error_message="Hard deadline or operational guard reached",
        )
        raise
    except KeyboardInterrupt:
        try:
            progress.stop_safely("KEYBOARD_INTERRUPT")
        except FD1RecoveryStop:
            _mark_stopped(
                engine,
                run_id=run_id,
                scope=scope,
                lease_owner=lease_owner,
                error_type="KeyboardInterrupt",
                error_message="Owner interrupted recovery at a durable checkpoint",
            )
            raise
    except BaseException as exc:
        _mark_stopped(
            engine,
            run_id=run_id,
            scope=scope,
            lease_owner=lease_owner,
            error_type=type(exc).__name__,
            error_message=str(exc),
            failed=True,
        )
        raise
    finally:
        progress.close()


def _ensure_run_and_manifest_files(
    engine: Engine,
    *,
    manifest: RecoveryManifest,
    manifest_path: Path,
    finalizer_revision: str,
    options: FD1RecoveryOptions,
) -> None:
    run_id = UUID(manifest.recovery_id)
    manifest_uri = _runtime_logical_uri(manifest_path)
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as session:
        row = session.get(FD1RecoveryRun, run_id)
        if row is None:
            row = FD1RecoveryRun(
                id=run_id,
                manifest_checksum=manifest.manifest_checksum,
                manifest_schema_version=manifest.schema_version,
                manifest_logical_uri=manifest_uri,
                manifest_header={
                    "aggregate": manifest.aggregate.to_dict(),
                    "generation_lineage": manifest.generation_lineage.to_dict(),
                    "finalizer_lineage": manifest.finalizer_lineage.to_dict(),
                },
                hash_cache_schema_version=RECOVERY_HASH_CACHE_SCHEMA_VERSION,
                hash_cache_checksum=_manifest_hash_cache_checksum(manifest),
                generation_code_revision=manifest.generation_lineage.code_revision,
                finalizer_code_revision=finalizer_revision,
                identity_spool_logical_uri=manifest.identity_spool.logical_uri,
                identity_spool_sha256=manifest.identity_spool.sha256,
                identity_spool_row_count=manifest.identity_spool.row_count,
                status="PLANNED",
                max_runtime_minutes=options.max_runtime_minutes,
                heartbeat_seconds=options.heartbeat_seconds,
                provider_calls=0,
                progress={},
                performance_metrics={},
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
        else:
            expected = {
                "manifest_checksum": manifest.manifest_checksum,
                "manifest_schema_version": manifest.schema_version,
                "manifest_logical_uri": manifest_uri,
                "hash_cache_schema_version": RECOVERY_HASH_CACHE_SCHEMA_VERSION,
                "hash_cache_checksum": _manifest_hash_cache_checksum(manifest),
                "generation_code_revision": manifest.generation_lineage.code_revision,
                "finalizer_code_revision": finalizer_revision,
                "identity_spool_logical_uri": manifest.identity_spool.logical_uri,
                "identity_spool_sha256": manifest.identity_spool.sha256,
                "identity_spool_row_count": manifest.identity_spool.row_count,
                "provider_calls": 0,
            }
            _assert_fields(row, expected, "recovery run")

        existing = {
            item.relative_path: item
            for item in session.scalars(
                select(FD1RecoveryManifestFile).where(
                    FD1RecoveryManifestFile.run_id == run_id
                )
            )
        }
        if existing and len(existing) != len(manifest.files):
            raise FD1RecoveryError("Persisted recovery manifest-file population differs")
        for ordinal, entry in enumerate(manifest.files, start=1):
            fields = {
                "run_id": run_id,
                "ordinal": ordinal,
                "relative_path": entry.logical_uri,
                "size_bytes": entry.size_bytes,
                "mtime_ns": entry.mtime_ns,
                "file_sha256": entry.sha256,
                "schema_version": entry.declared_schema,
                "schema_fingerprint": entry.physical_schema_fingerprint,
                "row_count": entry.row_count,
                "min_event_date": datetime.fromisoformat(entry.min_date).date(),
                "max_event_date": datetime.fromisoformat(entry.max_date).date(),
                "event_family": entry.family,
                "season": entry.season,
                "month": entry.month,
                "partition_identity_checksum": entry.partition_identity_checksum,
                "validation_mode": "CACHE_REUSE",
                "validated_at": now,
            }
            found = existing.get(entry.logical_uri)
            if found is None:
                session.add(FD1RecoveryManifestFile(**fields))
            else:
                # validated_at and validation_mode are operational observations.
                _assert_fields(
                    found,
                    {
                        key: value
                        for key, value in fields.items()
                        if key not in {"validated_at", "validation_mode"}
                    },
                    "recovery manifest file",
                )
        session.commit()


def _require_successful_rehearsal(engine: Engine, *, run_id: UUID) -> None:
    """Require complete, durable rollback evidence before a FULL recovery."""

    with engine.connect() as connection:
        rehearsal_succeeded_at = connection.execute(
            text(
                "SELECT rehearsal_succeeded_at FROM fd1_recovery_runs "
                "WHERE id=:run_id"
            ),
            {"run_id": run_id},
        ).scalar_one()
        checkpoints = connection.execute(
            text(
                "SELECT stage_name,progress FROM fd1_recovery_stage_checkpoints "
                "WHERE run_id=:run_id AND scope='REHEARSAL' "
                "AND status='SUCCEEDED' ORDER BY stage_sequence"
            ),
            {"run_id": run_id},
        ).mappings().all()
    if rehearsal_succeeded_at is None:
        raise FD1RecoveryError(
            "FULL recovery requires a successful bounded rehearsal first"
        )
    if tuple(row["stage_name"] for row in checkpoints) != RECOVERY_STAGES:
        raise FD1RecoveryError(
            "FULL recovery requires all six succeeded REHEARSAL checkpoints"
        )
    final_progress = checkpoints[-1]["progress"]
    if not isinstance(final_progress, Mapping) or (
        final_progress.get("transaction_rolled_back") is not True
    ):
        raise FD1RecoveryError(
            "FULL recovery requires durable rehearsal rollback evidence"
        )


def _claim_lease(
    engine: Engine,
    *,
    run_id: UUID,
    lease_owner: str,
    options: FD1RecoveryOptions,
) -> Mapping[str, Any]:
    now = datetime.now(UTC)
    with engine.begin() as connection:
        row = connection.execute(
            text(
                "SELECT status,lease_owner,lease_expires_at FROM fd1_recovery_runs "
                "WHERE id=:run_id FOR UPDATE"
            ),
            {"run_id": run_id},
        ).mappings().one()
        if row["status"] == "FINALIZED":
            return dict(row)
        active_owner = row["lease_owner"]
        expires = _utc(row["lease_expires_at"])
        if active_owner and active_owner != lease_owner and expires and expires > now:
            raise FD1RecoveryError("Another live recovery lease owns this manifest")
        if row["status"] != "PLANNED" and not options.resume:
            raise FD1RecoveryError("Existing recovery state requires --resume")
        connection.execute(
            text(
                "UPDATE fd1_recovery_runs SET status='RUNNING',lease_owner=:owner," 
                "lease_expires_at=:lease_expires,heartbeat_at=:now," 
                "started_at=COALESCE(started_at,:now),deadline_at=:deadline," 
                "max_runtime_minutes=:minutes,heartbeat_seconds=:seconds," 
                "error_type=NULL,error_message=NULL,updated_at=:now WHERE id=:run_id"
            ),
            {
                "owner": lease_owner,
                "lease_expires": now + timedelta(seconds=options.heartbeat_seconds * 3),
                "now": now,
                "deadline": now + timedelta(minutes=options.max_runtime_minutes),
                "minutes": options.max_runtime_minutes,
                "seconds": options.heartbeat_seconds,
                "run_id": run_id,
            },
        )
        return {**dict(row), "status": "RUNNING"}


def _run_files_validated_stage(
    engine: Engine,
    *,
    manifest: RecoveryManifest,
    paths: tuple[Path, ...],
    scope: str,
    progress: FD1RecoveryProgress,
) -> None:
    checkpoint = _start_stage(
        engine,
        run_id=UUID(manifest.recovery_id),
        scope=scope,
        stage="FILES_VALIDATED",
        input_checksum=manifest.manifest_checksum,
    )
    if _stage_succeeded(engine, checkpoint):
        progress.update(
            files_validated=len(paths),
            files_total=len(paths),
            emit=True,
        )
        return
    for index, (entry, path) in enumerate(zip(manifest.files, paths, strict=True), start=1):
        stat = path.stat()
        if stat.st_size != entry.size_bytes or stat.st_mtime_ns != entry.mtime_ns:
            raise FD1RecoveryError("A manifested Parquet file changed before recovery")
        progress.update(
            files_validated=index,
            files_total=len(paths),
        )
        progress.raise_if_stopped()
    _complete_stage(
        engine,
        checkpoint_id=checkpoint,
        run_id=UUID(manifest.recovery_id),
        scope=scope,
        stage="FILES_VALIDATED",
        output={
            "file_count": manifest.aggregate.file_count,
            "byte_count": manifest.aggregate.byte_count,
            "row_count": manifest.aggregate.row_count,
            "manifest_checksum": manifest.manifest_checksum,
        },
    )


def _start_stage(
    engine: Engine,
    *,
    run_id: UUID,
    scope: str,
    stage: str,
    input_checksum: str,
) -> UUID:
    if stage not in RECOVERY_STAGE_SEQUENCE:
        raise ValueError("Recovery stage is invalid")
    now = datetime.now(UTC)
    with engine.begin() as connection:
        sequence = RECOVERY_STAGE_SEQUENCE[stage]
        if sequence > 1:
            previous = RECOVERY_STAGES[sequence - 2]
            prior_succeeded = connection.execute(
                text(
                    "SELECT COUNT(*) FROM fd1_recovery_stage_checkpoints "
                    "WHERE run_id=:run_id AND scope=:scope AND stage_name=:previous "
                    "AND status='SUCCEEDED'"
                ),
                {"run_id": run_id, "scope": scope, "previous": previous},
            ).scalar_one()
            if int(prior_succeeded) != 1:
                raise FD1RecoveryError(
                    f"Recovery stage {stage} requires succeeded {previous}"
                )
        succeeded = connection.execute(
            text(
                "SELECT id,input_checksum FROM fd1_recovery_stage_checkpoints "
                "WHERE run_id=:run_id AND scope=:scope AND stage_name=:stage "
                "AND status='SUCCEEDED'"
            ),
            {"run_id": run_id, "scope": scope, "stage": stage},
        ).mappings().one_or_none()
        if succeeded is not None:
            if succeeded["input_checksum"] != input_checksum:
                raise FD1RecoveryError("Succeeded stage input checksum differs")
            return UUID(str(succeeded["id"]))
        latest = connection.execute(
            text(
                "SELECT id,attempt,status,input_checksum,resume_token,progress FROM "
                "fd1_recovery_stage_checkpoints WHERE run_id=:run_id "
                "AND scope=:scope AND stage_name=:stage ORDER BY attempt DESC LIMIT 1"
            ),
            {"run_id": run_id, "scope": scope, "stage": stage},
        ).mappings().one_or_none()
        if latest is not None and latest["status"] in {"RUNNING", "PENDING"}:
            if latest["input_checksum"] != input_checksum:
                raise FD1RecoveryError("Resumed stage input checksum differs")
            checkpoint_id = UUID(str(latest["id"]))
            connection.execute(
                text(
                    "UPDATE fd1_recovery_stage_checkpoints SET status='RUNNING',"
                    "started_at=COALESCE(started_at,:now),heartbeat_at=:now," 
                    "updated_at=:now WHERE id=:id"
                ),
                {"now": now, "id": checkpoint_id},
            )
        else:
            attempt = 1 if latest is None else int(latest["attempt"]) + 1
            checkpoint_id = uuid5(
                RECOVERY_NAMESPACE,
                f"stage:{run_id}:{scope}:{stage}:{attempt}:{input_checksum}",
            )
            connection.execute(
                text(
                    "INSERT INTO fd1_recovery_stage_checkpoints "
                    "(id,run_id,scope,stage_sequence,stage_name,attempt,status,"
                    "input_checksum,started_at,heartbeat_at,progress,"
                    "performance_metrics,resume_token,created_at,updated_at) VALUES "
                    "(:id,:run_id,:scope,:sequence,:stage,:attempt,'RUNNING',"
                    ":input_checksum,:now,:now,CAST(:empty AS json),"
                    "CAST(:empty AS json),CAST(:resume AS json),:now,:now)"
                ),
                {
                    "id": checkpoint_id,
                    "run_id": run_id,
                    "scope": scope,
                    "sequence": sequence,
                    "stage": stage,
                    "attempt": attempt,
                    "input_checksum": input_checksum,
                    "now": now,
                    "empty": "{}",
                    "resume": _canonical_json(
                        latest["resume_token"]
                        if latest is not None and latest["resume_token"] is not None
                        else None
                    ),
                },
            )
        connection.execute(
            text(
                "UPDATE fd1_recovery_runs SET active_stage=:stage," 
                "last_operation=:operation,updated_at=:now WHERE id=:run_id"
            ),
            {
                "stage": stage,
                "operation": f"START:{scope}:{stage}",
                "now": now,
                "run_id": run_id,
            },
        )
        return checkpoint_id


def _complete_stage(
    engine: Engine,
    *,
    checkpoint_id: UUID,
    run_id: UUID,
    scope: str,
    stage: str,
    output: Mapping[str, Any],
) -> None:
    now = datetime.now(UTC)
    output_checksum = _checksum(output)
    payload = _canonical_json(output)
    with engine.begin() as connection:
        status = connection.execute(
            text(
                "SELECT status,output_checksum FROM fd1_recovery_stage_checkpoints "
                "WHERE id=:id AND run_id=:run_id AND scope=:scope AND stage_name=:stage"
            ),
            {
                "id": checkpoint_id,
                "run_id": run_id,
                "scope": scope,
                "stage": stage,
            },
        ).mappings().one()
        if status["status"] == "SUCCEEDED":
            if status["output_checksum"] != output_checksum:
                raise FD1RecoveryError("Succeeded stage output checksum differs")
            return
        checkpoint_update = connection.execute(
            text(
                "UPDATE fd1_recovery_stage_checkpoints SET status='SUCCEEDED',"
                "output_checksum=:checksum,completed_at=:now,heartbeat_at=:now," 
                "progress=CAST(:payload AS json),updated_at=:now WHERE id=:id "
                "AND status='RUNNING'"
            ),
            {
                "checksum": output_checksum,
                "now": now,
                "payload": payload,
                "id": checkpoint_id,
            },
        )
        _expect_one_updated(checkpoint_update, "stage checkpoint completion")
        run_update = connection.execute(
            text(
                "UPDATE fd1_recovery_runs SET last_completed_stage=:stage," 
                "active_stage=NULL,last_operation=:operation,updated_at=:now "
                "WHERE id=:run_id AND status='RUNNING'"
            ),
            {
                "stage": stage,
                "operation": f"COMPLETE:{scope}:{stage}",
                "now": now,
                "run_id": run_id,
            },
        )
        _expect_one_updated(run_update, "recovery run stage completion")


def _heartbeat_sink(
    engine: Engine,
    *,
    run_id: UUID,
    scope: str,
    lease_owner: str,
) -> Callable[[Mapping[str, object]], None]:
    def persist(record: Mapping[str, object]) -> None:
        now = datetime.now(UTC)
        payload = _canonical_json(record)
        with engine.begin() as connection:
            # Every main-thread multi-row transition locks the stage checkpoint
            # before the run row.  Use the same order here so a heartbeat cannot
            # deadlock a stage completion while each holds the other's row lock.
            connection.execute(
                text(
                    "UPDATE fd1_recovery_stage_checkpoints SET heartbeat_at=:now,"
                    "progress=CAST(:payload AS json),updated_at=:now "
                    "WHERE run_id=:run_id AND scope=:scope AND status='RUNNING'"
                ),
                {
                    "now": now,
                    "payload": payload,
                    "run_id": run_id,
                    "scope": scope,
                },
            )
            run_update = connection.execute(
                text(
                    "UPDATE fd1_recovery_runs SET heartbeat_at=:now," 
                    "lease_expires_at=:expires,progress=CAST(:payload AS json),"
                    "last_operation=:operation,updated_at=:now WHERE id=:run_id "
                    "AND status='RUNNING' AND lease_owner=:owner"
                ),
                {
                    "now": now,
                    "expires": now + timedelta(seconds=90),
                    "payload": payload,
                    "operation": str(record.get("database_stage", "unknown")),
                    "run_id": run_id,
                    "owner": lease_owner,
                },
            )
            if run_update.rowcount != 1:
                terminal = connection.execute(
                    text(
                        "SELECT status,lease_owner FROM fd1_recovery_runs "
                        "WHERE id=:run_id"
                    ),
                    {"run_id": run_id},
                ).mappings().one()
                if terminal["status"] in {
                    "FINALIZED",
                    "REHEARSAL_SUCCEEDED",
                    "STOPPED",
                    "FAILED",
                } and terminal["lease_owner"] is None:
                    return
                _expect_one_updated(run_update, "recovery heartbeat lease")

    return persist


def _stage_succeeded(engine: Engine, checkpoint_id: UUID) -> bool:
    with engine.connect() as connection:
        return (
            connection.execute(
                text(
                    "SELECT status FROM fd1_recovery_stage_checkpoints WHERE id=:id"
                ),
                {"id": checkpoint_id},
            ).scalar_one()
            == "SUCCEEDED"
        )


def _succeeded_stage_output(
    engine: Engine,
    checkpoint_id: UUID,
) -> Mapping[str, Any] | None:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT status,progress FROM fd1_recovery_stage_checkpoints "
                "WHERE id=:id"
            ),
            {"id": checkpoint_id},
        ).mappings().one()
    if row["status"] != "SUCCEEDED":
        return None
    progress = row["progress"]
    if not isinstance(progress, Mapping):
        raise FD1RecoveryError("Succeeded stage has no JSON output evidence")
    return dict(progress)


def _mark_stopped(
    engine: Engine,
    *,
    run_id: UUID,
    scope: str,
    lease_owner: str,
    error_type: str,
    error_message: str,
    failed: bool = False,
) -> None:
    now = datetime.now(UTC)
    status = "FAILED" if failed else "STOPPED"
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE fd1_recovery_stage_checkpoints SET status=:status," 
                "completed_at=:now,heartbeat_at=:now,error_type=:error_type," 
                "error_message=:error_message,updated_at=:now WHERE run_id=:run_id "
                "AND scope=:scope AND status='RUNNING'"
            ),
            {
                "status": status,
                "now": now,
                "error_type": error_type[:80],
                "error_message": error_message[:4000],
                "run_id": run_id,
                "scope": scope,
            },
        )
        connection.execute(
            text(
                "UPDATE fd1_recovery_runs SET status=:status,lease_owner=NULL," 
                "lease_expires_at=NULL,active_stage=NULL,error_type=:error_type," 
                "error_message=:error_message,updated_at=:now WHERE id=:run_id "
                "AND lease_owner=:owner"
            ),
            {
                "status": status,
                "error_type": error_type[:80],
                "error_message": error_message[:4000],
                "now": now,
                "run_id": run_id,
                "owner": lease_owner,
            },
        )


def _release_lease(
    engine: Engine,
    *,
    run_id: UUID,
    lease_owner: str,
    status: str,
    last_operation: str,
) -> None:
    with engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE fd1_recovery_runs SET status=:status,lease_owner=NULL," 
                "lease_expires_at=NULL,active_stage=NULL,last_operation=:operation," 
                "updated_at=:now WHERE id=:run_id AND lease_owner=:owner"
            ),
            {
                "status": status,
                "operation": last_operation,
                "now": datetime.now(UTC),
                "run_id": run_id,
                "owner": lease_owner,
            },
        )
        _expect_one_updated(result, "recovery lease release")


def _complete_rehearsal(
    engine: Engine,
    *,
    checkpoint_id: UUID,
    run_id: UUID,
    lease_owner: str,
    output: Mapping[str, Any],
) -> None:
    now = datetime.now(UTC)
    final_output = {**dict(output), "transaction_rolled_back": True}
    output_checksum = _checksum(final_output)
    with engine.begin() as connection:
        checkpoint_update = connection.execute(
            text(
                "UPDATE fd1_recovery_stage_checkpoints SET status='SUCCEEDED',"
                "output_checksum=:checksum,completed_at=:now,heartbeat_at=:now,"
                "progress=CAST(:payload AS json),updated_at=:now "
                "WHERE id=:id AND run_id=:run_id AND scope='REHEARSAL' "
                "AND stage_name='FINALIZED' AND status='RUNNING'"
            ),
            {
                "checksum": output_checksum,
                "now": now,
                "payload": _canonical_json(final_output),
                "id": checkpoint_id,
                "run_id": run_id,
            },
        )
        _expect_one_updated(checkpoint_update, "rehearsal FINALIZED checkpoint")
        run_update = connection.execute(
            text(
                "UPDATE fd1_recovery_runs SET status='REHEARSAL_SUCCEEDED',"
                "rehearsal_succeeded_at=:now,lease_owner=NULL,lease_expires_at=NULL," 
                "active_stage=NULL,last_completed_stage='FINALIZED',"
                "last_operation='REHEARSAL_ROLLBACK_VERIFIED',"
                "updated_at=:now WHERE id=:run_id AND lease_owner=:owner "
                "AND status='RUNNING'"
            ),
            {"now": now, "run_id": run_id, "owner": lease_owner},
        )
        _expect_one_updated(run_update, "rehearsal recovery run completion")


def _restore_rehearsal_status(
    engine: Engine,
    *,
    run_id: UUID,
    lease_owner: str,
) -> None:
    """Make an exact repeated rehearsal invocation idempotent."""

    now = datetime.now(UTC)
    with engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE fd1_recovery_runs SET status='REHEARSAL_SUCCEEDED',"
                "rehearsal_succeeded_at=COALESCE(rehearsal_succeeded_at,:now),"
                "lease_owner=NULL,lease_expires_at=NULL,active_stage=NULL,"
                "last_completed_stage='FINALIZED',"
                "last_operation='EXACT_REHEARSAL_REUSE',updated_at=:now "
                "WHERE id=:run_id AND lease_owner=:owner AND status='RUNNING'"
            ),
            {"now": now, "run_id": run_id, "owner": lease_owner},
        )
        _expect_one_updated(result, "reused rehearsal recovery run")


def _promotion_metrics_from_rehearsal_output(
    output: Mapping[str, Any],
) -> IdentityPromotionMetrics:
    if output.get("transaction_rolled_back") is not True:
        raise FD1RecoveryError("Succeeded rehearsal lacks rollback evidence")
    fields = {
        "inserted",
        "reused",
        "unresolved",
        "source_declared_conflicts",
        "blocking_conflicts",
        "promotion_seconds",
    }
    if not fields.issubset(output):
        raise FD1RecoveryError("Succeeded rehearsal lacks identity metrics")
    return IdentityPromotionMetrics(**{name: output[name] for name in fields})


def _reconciliation_metrics_from_output(
    output: Mapping[str, Any],
) -> IdentityReconciliationMetrics:
    fields = {
        "total_rows",
        "planned_insert",
        "exact_reuse",
        "unresolved",
        "source_declared_conflicts",
        "blocking_conflicts",
        "quarantine_rows",
        "reconciliation_seconds",
        "conflict_query_seconds",
    }
    if not fields.issubset(output):
        raise FD1RecoveryError("Succeeded reconciliation lacks identity metrics")
    return IdentityReconciliationMetrics(**{name: output[name] for name in fields})


def _explain_reconciliation_queries(
    engine: Engine,
    *,
    run_id: UUID,
    scope: str,
    deadline_at: float,
    progress: FD1RecoveryProgress,
) -> Mapping[str, Any]:
    """Capture bounded PostgreSQL plans for both expensive set-based joins."""

    limit = 10_000
    started = monotonic()
    parameters = {"run_id": run_id, "scope": scope, "limit": limit}
    statements = {
        "natural_key": (
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
            "SELECT s.staging_ordinal,f.id FROM fd1_recovery_identity_stage s "
            "LEFT JOIN open_data_identity_mappings f ON "
            "f.source_version_id=s.source_version_id "
            "AND f.entity_type=s.entity_type "
            "AND f.source_namespace=s.source_namespace "
            "AND f.external_id=s.external_id "
            "AND f.source_row_number=s.source_row_number "
            "WHERE s.run_id=:run_id AND s.scope=:scope "
            "ORDER BY s.staging_ordinal LIMIT :limit"
        ),
        "identity_checksum": (
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
            "SELECT s.staging_ordinal,f.id FROM fd1_recovery_identity_stage s "
            "LEFT JOIN open_data_identity_mappings f "
            "ON f.identity_checksum=s.identity_checksum "
            "WHERE s.run_id=:run_id AND s.scope=:scope "
            "ORDER BY s.staging_ordinal LIMIT :limit"
        ),
        "stage_semantic_key_overlap": (
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
            "WITH sample AS MATERIALIZED ("
            "SELECT * FROM fd1_recovery_identity_stage "
            "WHERE run_id=:run_id AND scope=:scope "
            "ORDER BY staging_ordinal LIMIT :limit) "
            "SELECT a.staging_ordinal,b.staging_ordinal FROM sample a "
            "JOIN sample b ON b.staging_ordinal>a.staging_ordinal "
            "AND b.source_version_id=a.source_version_id "
            "AND b.entity_type=a.entity_type "
            "AND b.source_namespace=a.source_namespace "
            "AND b.external_id=a.external_id "
            "AND (a.canonical_namespace,a.canonical_id) IS DISTINCT FROM "
            "(b.canonical_namespace,b.canonical_id) "
            "AND COALESCE(a.valid_through,'infinity'::date) >= "
            "COALESCE(b.valid_from,'-infinity'::date) "
            "AND COALESCE(b.valid_through,'infinity'::date) >= "
            "COALESCE(a.valid_from,'-infinity'::date) LIMIT :limit"
        ),
        "final_semantic_key_overlap": (
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
            "WITH sample AS MATERIALIZED ("
            "SELECT * FROM fd1_recovery_identity_stage "
            "WHERE run_id=:run_id AND scope=:scope "
            "ORDER BY staging_ordinal LIMIT :limit) "
            "SELECT s.staging_ordinal,f.id FROM sample s "
            "JOIN open_data_identity_mappings f ON "
            "f.source_version_id=s.source_version_id "
            "AND f.entity_type=s.entity_type "
            "AND f.source_namespace=s.source_namespace "
            "AND f.external_id=s.external_id "
            "AND f.confidence IN ('EXACT','HIGH') "
            "AND (f.canonical_namespace,f.canonical_id) IS DISTINCT FROM "
            "(s.canonical_namespace,s.canonical_id) "
            "AND COALESCE(f.valid_through,'infinity'::date) >= "
            "COALESCE(s.valid_from,'-infinity'::date) "
            "AND COALESCE(s.valid_through,'infinity'::date) >= "
            "COALESCE(f.valid_from,'-infinity'::date) LIMIT :limit"
        ),
    }
    plans: dict[str, Any] = {}
    with engine.begin() as connection:
        for name, statement in statements.items():
            _set_local_statement_timeout(
                connection,
                _remaining_statement_timeout_ms(
                    deadline_at,
                    progress=progress,
                    maximum_ms=1_200_000,
                ),
            )
            plan = connection.execute(text(statement), parameters).scalar_one()
            document = _normalize_explain_document(plan)
            plans[name] = {
                "summary": _explain_plan_summary(document),
                "raw": document,
            }
            progress.raise_if_stopped()
    return {
        "population_limit": limit,
        "wall_seconds": round(monotonic() - started, 6),
        "plans": plans,
    }


def _normalize_explain_document(value: Any) -> Mapping[str, Any]:
    selected = value
    if isinstance(selected, str):
        try:
            selected = json.loads(selected)
        except json.JSONDecodeError as exc:
            raise FD1RecoveryError("PostgreSQL EXPLAIN JSON is invalid") from exc
    if isinstance(selected, list) and len(selected) == 1:
        selected = selected[0]
    if not isinstance(selected, Mapping):
        raise FD1RecoveryError("PostgreSQL EXPLAIN returned no JSON plan")
    return dict(selected)


def _explain_plan_summary(document: Mapping[str, Any]) -> Mapping[str, Any]:
    index_names: set[str] = set()
    node_types: set[str] = set()
    buffers: dict[str, float] = {}

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            index_name = value.get("Index Name")
            node_type = value.get("Node Type")
            if isinstance(index_name, str):
                index_names.add(index_name)
            if isinstance(node_type, str):
                node_types.add(node_type)
            for key, item in value.items():
                if (
                    isinstance(key, str)
                    and key.endswith(" Blocks")
                    and isinstance(item, (int, float))
                    and not isinstance(item, bool)
                ):
                    buffers[key] = buffers.get(key, 0.0) + float(item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(document)
    return {
        "planning_time_ms": document.get("Planning Time"),
        "execution_time_ms": document.get("Execution Time"),
        "index_names": sorted(index_names),
        "node_types": sorted(node_types),
        "buffer_blocks": {
            key: int(value) if value.is_integer() else value
            for key, value in sorted(buffers.items())
        },
    }


def _run_rehearsal_promotion(
    engine: Engine,
    *,
    run_id: UUID,
    scope: str,
    progress: FD1RecoveryProgress,
    deadline_at: float,
) -> tuple[IdentityPromotionMetrics, float]:
    if scope != "REHEARSAL":
        raise FD1RecoveryError("Rollback rehearsal requires REHEARSAL scope")
    connection = engine.connect()
    transaction = connection.begin()
    promotion_started = monotonic()
    try:
        _set_local_statement_timeout(
            connection,
            _remaining_statement_timeout_ms(
                deadline_at,
                progress=progress,
                maximum_ms=1_800_000,
            ),
        )
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('gate3c-fd1-recovery'))")
        )
        _promote_publication(
            connection,
            run_id=run_id,
            scope=scope,
            progress=progress,
            allow_rehearsal_rollback=True,
            deadline_at=deadline_at,
        )
        progress.raise_if_stopped()
        _set_local_statement_timeout(
            connection,
            _remaining_statement_timeout_ms(
                deadline_at,
                progress=progress,
                maximum_ms=1_800_000,
            ),
        )
        metrics = promote_identity_mappings(
            connection,
            run_id=run_id,
            scope=scope,
            statement_timeout_ms=lambda: _remaining_statement_timeout_ms(
                deadline_at,
                progress=progress,
                maximum_ms=1_800_000,
            ),
        )
        transaction.rollback()
    except BaseException:
        if transaction.is_active:
            transaction.rollback()
        raise
    finally:
        connection.close()
    _assert_rehearsal_rollback(engine, run_id=run_id)
    return metrics, round(monotonic() - promotion_started, 6)


def _assert_rehearsal_rollback(engine: Engine, *, run_id: UUID) -> None:
    """Prove the rehearsal transaction left no canonical or promotion markers."""

    with engine.connect() as connection:
        publication_markers = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM fd1_recovery_publication_stage "
                    "WHERE run_id=:run_id AND scope='REHEARSAL' "
                    "AND promoted_at IS NOT NULL"
                ),
                {"run_id": run_id},
            ).scalar_one()
        )
        identity_insert_leaks = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM fd1_recovery_identity_stage s "
                    "JOIN open_data_identity_mappings f "
                    "ON f.id=s.target_mapping_id WHERE s.run_id=:run_id "
                    "AND s.scope='REHEARSAL' AND s.outcome='INSERT'"
                ),
                {"run_id": run_id},
            ).scalar_one()
        )
        finalized_run = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM fd1_recovery_runs "
                    "WHERE id=:run_id AND status='FINALIZED'"
                ),
                {"run_id": run_id},
            ).scalar_one()
        )
    if publication_markers or identity_insert_leaks or finalized_run:
        raise FD1RecoveryError(
            "Rehearsal rollback left visible canonical or promotion evidence"
        )


def _run_atomic_promotion(
    engine: Engine,
    *,
    run_id: UUID,
    scope: str,
    checkpoint_id: UUID,
    lease_owner: str,
    manifest: RecoveryManifest,
    store_root: Path,
    validation: Mapping[str, Any],
    progress: FD1RecoveryProgress,
    deadline_at: float,
) -> tuple[IdentityPromotionMetrics, float]:
    if scope != "FULL":
        raise FD1RecoveryError("Atomic final promotion requires FULL scope")
    now = datetime.now(UTC)
    promotion_started = monotonic()
    with engine.begin() as connection:
        _set_local_statement_timeout(
            connection,
            _remaining_statement_timeout_ms(
                deadline_at,
                progress=progress,
                maximum_ms=1_800_000,
            ),
        )
        # A transaction-scoped advisory lock serializes semantic FD1 promotion.
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('gate3c-fd1-recovery'))")
        )
        _restat_manifest(manifest, store_root=store_root)
        progress.raise_if_stopped()
        _promote_publication(
            connection,
            run_id=run_id,
            scope=scope,
            progress=progress,
            deadline_at=deadline_at,
        )
        progress.raise_if_stopped()
        _set_local_statement_timeout(
            connection,
            _remaining_statement_timeout_ms(
                deadline_at,
                progress=progress,
                maximum_ms=1_800_000,
            ),
        )
        metrics = promote_identity_mappings(
            connection,
            run_id=run_id,
            scope=scope,
            statement_timeout_ms=lambda: _remaining_statement_timeout_ms(
                deadline_at,
                progress=progress,
                maximum_ms=1_800_000,
            ),
        )
        if metrics.blocking_conflicts:
            raise FD1RecoveryError("Identity conflict appeared during final promotion")
        final_output = {
            "manifest_checksum": manifest.manifest_checksum,
            "validation_checksum": _checksum(validation),
            "identity_promotion": _as_mapping(metrics),
            "provider_calls": 0,
            "network_requests": 0,
            "provider_transports_constructed": 0,
        }
        checkpoint_update = connection.execute(
            text(
                "UPDATE fd1_recovery_stage_checkpoints SET status='SUCCEEDED',"
                "output_checksum=:checksum,progress=CAST(:payload AS json),"
                "completed_at=:now,heartbeat_at=:now,updated_at=:now "
                "WHERE id=:id AND run_id=:run_id AND scope='FULL' "
                "AND stage_name='FINALIZED' AND status='RUNNING'"
            ),
            {
                "checksum": _checksum(final_output),
                "payload": _canonical_json(final_output),
                "now": now,
                "id": checkpoint_id,
                "run_id": run_id,
            },
        )
        _expect_one_updated(checkpoint_update, "atomic FINALIZED checkpoint")
        run_update = connection.execute(
            text(
                "UPDATE fd1_recovery_runs SET status='FINALIZED',"
                "active_stage=NULL,last_completed_stage='FINALIZED',"
                "finalized_at=:now,lease_owner=NULL,lease_expires_at=NULL," 
                "last_operation='ATOMIC_PROMOTION_COMMITTED',updated_at=:now "
                "WHERE id=:run_id AND lease_owner=:owner AND status='RUNNING'"
            ),
            {"now": now, "run_id": run_id, "owner": lease_owner},
        )
        _expect_one_updated(run_update, "atomic recovery run finalization")
    return metrics, round(monotonic() - promotion_started, 6)


def _restat_manifest(manifest: RecoveryManifest, *, store_root: Path) -> None:
    for entry, path in zip(
        manifest.files,
        manifest_paths(manifest, store_root=store_root),
        strict=True,
    ):
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != (entry.size_bytes, entry.mtime_ns):
            raise FD1RecoveryError("A Parquet file changed before atomic promotion")


def _stage_publication(
    engine: Engine,
    *,
    manifest: RecoveryManifest,
    manifest_path: Path,
    store_root: Path,
    scope: str,
    rehearsal: bool,
    progress: FD1RecoveryProgress,
    deadline_at: float,
) -> Mapping[str, Any]:
    from app.gen2.fd1_recovery_publication import stage_recovery_publication

    staging_started = monotonic()
    result = stage_recovery_publication(
        engine,
        manifest=manifest,
        trusted_ledger_path=_trusted_ledger_path(manifest),
        store_root=store_root,
        scope=scope,
        rehearsal=rehearsal,
        progress_callback=lambda update: _record_publication_progress(
            progress, update
        ),
        statement_timeout_ms=lambda: _remaining_statement_timeout_ms(
            deadline_at,
            progress=progress,
            maximum_ms=1_800_000,
        ),
    )
    return {
        **dict(_as_mapping(result)),
        "partition_registration_seconds": round(monotonic() - staging_started, 6),
    }


def _record_publication_progress(
    progress: FD1RecoveryProgress,
    update: object,
) -> None:
    progress.raise_if_stopped()
    stage = str(getattr(update, "stage", "")).strip()
    completed = getattr(update, "completed", None)
    total = getattr(update, "total", None)
    current = getattr(update, "current", None)
    if not stage or isinstance(completed, bool) or not isinstance(completed, int):
        raise FD1RecoveryError("Publication progress update is invalid")
    if isinstance(total, bool) or not isinstance(total, int):
        raise FD1RecoveryError("Publication progress total is invalid")
    if completed < 0 or total < 0 or completed > total:
        raise FD1RecoveryError("Publication progress bounds are invalid")
    operation = f"publication:{stage}:{completed}/{total}"
    if current is not None:
        operation = f"{operation}:{current}"
    # completed/total describe publication targets, not validated Parquet files;
    # retain the already-established 329/329 file counters on the heartbeat.
    progress.update(database_stage=operation[:180])


def _promote_publication(
    connection: Connection,
    *,
    run_id: UUID,
    scope: str,
    progress: FD1RecoveryProgress,
    allow_rehearsal_rollback: bool = False,
    deadline_at: float,
) -> Mapping[str, Any]:
    from app.gen2.fd1_recovery_publication import promote_recovery_publication

    return _as_mapping(
        promote_recovery_publication(
            connection,
            run_id=run_id,
            scope=scope,
            allow_rehearsal_rollback=allow_rehearsal_rollback,
            progress_callback=lambda update: _record_publication_progress(
                progress, update
            ),
            statement_timeout_ms=lambda: _remaining_statement_timeout_ms(
                deadline_at,
                progress=progress,
                maximum_ms=1_800_000,
            ),
        )
    )


def _validate_warehouse(
    manifest: RecoveryManifest,
    *,
    store_root: Path,
    active_identity_report: Path | None,
    full: bool,
    progress: FD1RecoveryProgress,
) -> Mapping[str, Any]:
    from app.gen2.fd1_recovery_validation import validate_recovery_warehouse

    result = validate_recovery_warehouse(
        manifest,
        store_root=store_root,
        trusted_ledger_path=_trusted_ledger_path(manifest),
        active_identity_report=active_identity_report,
        full=full,
        progress_callback=lambda update: _record_validation_progress(
            progress, update
        ),
    )
    return _as_mapping(result)


def _record_validation_progress(
    progress: FD1RecoveryProgress,
    update: object,
) -> None:
    progress.raise_if_stopped()
    progress.update(
        files_validated=getattr(update, "files_validated"),
        files_total=getattr(update, "files_total"),
    )


def _build_result(
    engine: Engine,
    *,
    manifest: RecoveryManifest,
    finalizer_revision: str,
    mode: str,
    status: str,
    started: float,
    progress: FD1RecoveryProgress,
    copy_metrics: IdentityCopyMetrics | None,
    reconciliation: IdentityReconciliationMetrics | None,
    promotion: IdentityPromotionMetrics | None,
    validation: Mapping[str, Any],
    publication_metrics: Mapping[str, Any],
    idempotent_reuse: bool,
    database_metrics: _RecoveryDatabaseMetrics,
    promotion_phase_seconds: float,
    reconciliation_explain: Mapping[str, Any],
) -> FD1RecoveryResult:
    with engine.connect() as connection:
        run = connection.execute(
            text(
                "SELECT last_completed_stage FROM fd1_recovery_runs WHERE id=:run_id"
            ),
            {"run_id": UUID(manifest.recovery_id)},
        ).mappings().one()
    mappings_staged = copy_metrics.rows_staged if copy_metrics else 0
    inserted = promotion.inserted if promotion else 0
    reused = promotion.reused if promotion else 0
    unresolved = (
        promotion.unresolved
        if promotion
        else (reconciliation.unresolved if reconciliation else 0)
    )
    source_conflicts = (
        promotion.source_declared_conflicts
        if promotion
        else (reconciliation.source_declared_conflicts if reconciliation else 0)
    )
    conflicts = (
        promotion.blocking_conflicts
        if promotion
        else (reconciliation.blocking_conflicts if reconciliation else 0)
    )
    performance = {
        "identity_copy": _as_mapping(copy_metrics),
        "identity_reconciliation": _as_mapping(reconciliation),
        "identity_reconciliation_explain": dict(reconciliation_explain),
        "identity_promotion": _as_mapping(promotion),
        "publication": dict(publication_metrics),
        "database": database_metrics.as_dict(),
        "final_atomic_promotion_seconds": (
            promotion_phase_seconds if mode == "finalize" else 0.0
        ),
        "rehearsal_rollback_promotion_seconds": (
            promotion_phase_seconds if mode == "sample-rehearsal" else 0.0
        ),
    }
    unsigned = {
        "schema_version": RECOVERY_REPORT_SCHEMA_VERSION,
        "run_id": manifest.recovery_id,
        "recovery_manifest_checksum": manifest.manifest_checksum,
        "generation_code_revision": manifest.generation_lineage.code_revision,
        "finalizer_code_revision": finalizer_revision,
        "mode": mode,
        "status": status,
        "last_completed_stage": run["last_completed_stage"],
        "files_reused": manifest.aggregate.file_count,
        "bytes_reused": manifest.aggregate.byte_count,
        "parquet_rows": manifest.aggregate.row_count,
        "mappings_staged": mappings_staged,
        "mappings_inserted": inserted,
        "mappings_reused": reused,
        "unresolved_mappings": unresolved,
        "source_declared_conflicts": source_conflicts,
        "mapping_conflicts": conflicts,
        "finalized_partitions": int(publication_metrics.get("partitions", 0)),
        "provider_calls": 0,
        "network_requests": 0,
        "provider_transports_constructed": 0,
        "elapsed_seconds": round(monotonic() - started, 6),
        "peak_rss_bytes": progress.peak_rss_bytes,
        "idempotent_reuse": idempotent_reuse,
        "performance": performance,
        "validation": dict(validation),
    }
    result = FD1RecoveryResult(
        **unsigned,
        result_checksum=_checksum(unsigned),
    )
    _write_result_artifact(result)
    return result


def _finalized_reuse_result(
    engine: Engine,
    *,
    manifest: RecoveryManifest,
    finalizer_revision: str,
    started: float,
    database_metrics: _RecoveryDatabaseMetrics,
) -> FD1RecoveryResult:
    progress = _NoopProgress()
    validation: Mapping[str, Any] = {
        "manifest_checksum": manifest.manifest_checksum,
        "status": "EXACT_FINALIZED_REUSE",
    }
    with engine.connect() as connection:
        partitions = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM fd1_recovery_publication_stage "
                    "WHERE run_id=:run_id AND scope='FULL' "
                    "AND object_kind='PARTITION'"
                ),
                {"run_id": UUID(manifest.recovery_id)},
            ).scalar_one()
        )
        outcome_rows = connection.execute(
            text(
                "SELECT outcome,reason_code,COUNT(*) AS row_count "
                "FROM fd1_recovery_identity_stage WHERE run_id=:run_id "
                "AND scope='FULL' GROUP BY outcome,reason_code"
            ),
            {"run_id": UUID(manifest.recovery_id)},
        ).mappings().all()
    outcomes: dict[str, int] = {}
    source_conflicts = 0
    for row in outcome_rows:
        outcome = str(row["outcome"])
        count = int(row["row_count"])
        outcomes[outcome] = outcomes.get(outcome, 0) + count
        if row["reason_code"] == "SOURCE_DECLARED_CONFLICT_QUARANTINED":
            source_conflicts += count
    blocking_conflicts = outcomes.get("CONFLICT", 0)
    pending = outcomes.get("PENDING", 0)
    if sum(outcomes.values()) != manifest.identity_spool.row_count:
        raise FD1RecoveryError(
            "Finalized recovery identity stage population differs from the manifest"
        )
    if blocking_conflicts or pending:
        raise FD1RecoveryError(
            "Finalized recovery has conflicting or pending identity stage rows"
        )
    resolved = outcomes.get("INSERT", 0) + outcomes.get("REUSE", 0)
    unresolved = outcomes.get("UNRESOLVED", 0) - source_conflicts
    if unresolved < 0:
        raise FD1RecoveryError("Finalized recovery identity outcomes are inconsistent")
    return _build_result(
        engine,
        manifest=manifest,
        finalizer_revision=finalizer_revision,
        mode="idempotency-reuse",
        status="FINALIZED",
        started=started,
        progress=progress,  # type: ignore[arg-type]
        copy_metrics=None,
        reconciliation=None,
        promotion=IdentityPromotionMetrics(
            inserted=0,
            reused=resolved,
            unresolved=unresolved,
            source_declared_conflicts=source_conflicts,
            blocking_conflicts=blocking_conflicts,
            promotion_seconds=0.0,
        ),
        validation=validation,
        publication_metrics={"partitions": partitions, "new_rows": 0},
        idempotent_reuse=True,
        database_metrics=database_metrics,
        promotion_phase_seconds=0.0,
        reconciliation_explain={},
    )


class _NoopProgress:
    peak_rss_bytes = 0


class _RecoveryDatabaseMetrics:
    """Invocation-scoped SQL/query and checked-out connection accounting."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._lock = Lock()
        self._query_count = 0
        self._active_connections = 0
        self._peak_active_connections = 0
        self._started = False
        self._before_listener = self._before_cursor_execute
        self._checkout_listener = self._checkout
        self._checkin_listener = self._checkin

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            event.listen(self.engine, "before_cursor_execute", self._before_listener)
            event.listen(self.engine.pool, "checkout", self._checkout_listener)
            event.listen(self.engine.pool, "checkin", self._checkin_listener)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if not self._started:
            return
        listeners = (
            (self.engine, "before_cursor_execute", self._before_listener),
            (self.engine.pool, "checkout", self._checkout_listener),
            (self.engine.pool, "checkin", self._checkin_listener),
        )
        for target, name, listener in listeners:
            if event.contains(target, name, listener):
                event.remove(target, name, listener)
        self._started = False

    def as_dict(self) -> dict[str, int]:
        with self._lock:
            return {
                "query_count": self._query_count,
                "peak_active_connections": self._peak_active_connections,
                "active_connections_at_snapshot": self._active_connections,
            }

    def _before_cursor_execute(self, *_args: object, **_kwargs: object) -> None:
        with self._lock:
            self._query_count += 1

    def _checkout(self, *_args: object, **_kwargs: object) -> None:
        with self._lock:
            self._active_connections += 1
            self._peak_active_connections = max(
                self._peak_active_connections,
                self._active_connections,
            )

    def _checkin(self, *_args: object, **_kwargs: object) -> None:
        with self._lock:
            self._active_connections = max(0, self._active_connections - 1)


def _trusted_ledger_path(manifest: RecoveryManifest) -> Path:
    return _resolve_runtime_logical_uri(
        manifest.generation_lineage.trusted_dry_run_ledger.logical_uri
    )


def _resolve_runtime_logical_uri(logical_uri: str) -> Path:
    root = Path(__file__).resolve().parents[3]
    selected = (root / Path(logical_uri)).resolve(strict=True)
    runtime = (root / ".runtime" / "gate3c-fd1").resolve(strict=True)
    if runtime != selected and runtime not in selected.parents:
        raise FD1RecoveryError("Recovery runtime evidence escapes the ignored root")
    return selected


def _runtime_logical_uri(path: Path) -> str:
    root = Path(__file__).resolve().parents[3]
    selected = Path(path).resolve(strict=True)
    try:
        relative = selected.relative_to(root).as_posix()
    except ValueError as exc:
        raise FD1RecoveryError("Recovery manifest is outside the repository") from exc
    if not relative.startswith(".runtime/gate3c-fd1/recovery/"):
        raise FD1RecoveryError("Recovery manifest is outside the ignored recovery root")
    return relative


def _manifest_hash_cache_checksum(manifest: RecoveryManifest) -> str:
    # The cache checksum is part of the persisted run header.  The manifest
    # binds every cache field, so this deterministic projection remains exact.
    return _checksum(
        {
            "entries": [
                {
                    "logical_uri": item.logical_uri,
                    "mtime_ns": item.mtime_ns,
                    "physical_schema_fingerprint": (
                        item.physical_schema_fingerprint
                    ),
                    "row_count": item.row_count,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in manifest.files
            ],
            "schema_version": RECOVERY_HASH_CACHE_SCHEMA_VERSION,
        }
    )


def _remaining_statement_timeout_ms(
    deadline_at: float,
    *,
    progress: FD1RecoveryProgress,
    maximum_ms: int,
) -> int:
    if isinstance(maximum_ms, bool) or not isinstance(maximum_ms, int):
        raise ValueError("maximum statement timeout must be an integer")
    if maximum_ms < 1:
        raise ValueError("maximum statement timeout must be positive")
    progress.raise_if_stopped()
    remaining_seconds = deadline_at - monotonic()
    if remaining_seconds <= 0:
        progress.stop_safely("HARD_DEADLINE_REACHED")
    return min(maximum_ms, max(1, int(remaining_seconds * 1000)))


def _set_local_statement_timeout(
    connection: Connection,
    timeout_ms: int,
) -> None:
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms < 1:
        raise ValueError("statement timeout must be a positive integer")
    connection.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{timeout_ms}ms"},
    )


def _stop_on_postgresql_query_cancel(
    error: BaseException,
    *,
    progress: FD1RecoveryProgress,
    deadline_at: float,
) -> None:
    if _postgresql_sqlstate(error) != "57014":
        return
    reason = (
        "HARD_DEADLINE_REACHED"
        if monotonic() >= deadline_at - 1.0
        else "DATABASE_STATEMENT_TIMEOUT"
    )
    progress.stop_safely(reason)


def _postgresql_sqlstate(error: BaseException) -> str | None:
    original = getattr(error, "orig", None)
    for candidate in (error, original):
        if candidate is None:
            continue
        sqlstate = getattr(candidate, "sqlstate", None) or getattr(
            candidate, "pgcode", None
        )
        if sqlstate:
            return str(sqlstate)
        diagnostic = getattr(candidate, "diag", None)
        sqlstate = getattr(diagnostic, "sqlstate", None)
        if sqlstate:
            return str(sqlstate)
    return None


def _lease_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _assert_fields(row: object, expected: Mapping[str, Any], label: str) -> None:
    for field, value in expected.items():
        actual = getattr(row, field)
        if _json_safe(actual) != _json_safe(value):
            raise FD1RecoveryError(f"Persisted {label} differs at {field}")


def _expect_one_updated(result: object, label: str) -> None:
    rowcount = getattr(result, "rowcount", None)
    if rowcount != 1:
        raise FD1RecoveryError(f"{label} updated {rowcount!r} rows instead of one")


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return dict(as_dict())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    try:
        return asdict(value)
    except TypeError as exc:
        raise TypeError("Recovery result is not JSON-ready") from exc


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (UUID, datetime, Path)):
        return str(value)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
    )


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_result_artifact(result: FD1RecoveryResult) -> Path:
    output = DEFAULT_RUNTIME_ROOT / "reports"
    output.mkdir(parents=True, exist_ok=True)
    payload = (_canonical_json(result.as_dict()) + "\n").encode("utf-8")
    checksum = hashlib.sha256(payload).hexdigest()
    target = output / f"fd1-recovery-result-sha256-{checksum}.json"
    if target.exists():
        if target.read_bytes() != payload:
            raise FD1RecoveryError("Recovery result artifact has conflicting bytes")
        return target
    temporary = output / f".{target.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
    except FileExistsError:
        if target.read_bytes() != payload:
            raise FD1RecoveryError("Recovery result publication raced with other bytes")
    finally:
        temporary.unlink(missing_ok=True)
    return target


__all__ = [
    "DEFAULT_IDENTITY_BATCH_SIZE",
    "FD1_RECOVERY_FINALIZER_CODE_REVISION_INPUTS",
    "FD1RecoveryError",
    "FD1RecoveryOptions",
    "FD1RecoveryResult",
    "RECOVERY_COMMAND_SCHEMA_VERSION",
    "SAFE_STOP_EXIT_CODE",
    "build_fd1_recovery_finalizer_revision",
    "finalize_fd1_from_existing_parquet",
]
