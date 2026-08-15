"""Provider-free publication planning for Gate 3C-FD1 recovery.

The interrupted operator already wrote and validated the Parquet corpus.  This
module never invokes a provider and never rewrites those files.  It reconstructs
the original generation-lineage metadata from four frozen inputs:

* the content-addressed recovery manifest;
* the trusted v5 dry-run ledger;
* already-governed registry rows and extraction-manifest metadata; and
* the exact, explicitly manifested Parquet files.

Plans are first written to the recovery-only publication staging table in
bounded transactions.  Canonical rows are promoted only by
``promote_publication_plan`` and only inside a transaction owned by its caller.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID, uuid5

import polars as pl
from sqlalchemy import Connection, Engine, and_, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.schema import Table
from sqlalchemy.sql.sqltypes import Date, DateTime
from sqlalchemy.types import Uuid

from app.gen2.fd1_identity import (
    FD1_IDENTITY_SCHEMA_VERSION,
    LAHMAN_SOURCE_KEY,
    PLAYER_PRIOR_TIMING_CLASSIFICATION,
    _canonical_checksum as _identity_payload_checksum,
)
from app.gen2.fd1_materializer import RETROSHEET_SOURCE_KEY
from app.gen2.fd1_recovery_manifest import (
    RecoveryFileEntry,
    RecoveryManifest,
    TrustedDryRunLedger,
)
from app.gen2.fd1_recovery_models import (
    FD1RecoveryIdentityStage,
    FD1RecoveryPublicationStage,
    RECOVERY_PUBLICATION_ORDER,
)
from app.gen2.fd1_registry_sync import FD1_SOURCE_DEFINITIONS
from app.gen2.fd1_warehouse import (
    FD1_INGESTION_JOB,
    FD1_INGESTION_SOURCE,
    FD1_WAREHOUSE_SCHEMA_VERSION,
    RETROSHEET_CSV_OBJECT_KEY,
    _PUBLICATION_NAMESPACE,
    _TIMING_BY_RETROSHEET_FAMILY,
    load_extraction_artifact_manifest,
    _validate_extraction_against_download,
    _validate_extraction_identity,
)
from app.gen2.open_data_acquisition import ExtractionArtifact
from app.gen2.open_data_registry import (
    build_owner_decision_identity,
    build_source_release_identity,
    canonical_sha256,
)
from app.gen2.parquet_store import (
    HistoricalEventPartitionArtifact,
    _canonical_checksum as _parquet_checksum,
    _inferred_metadata,
    _schema_document,
)
from app.gen2.retrosheet import RETROSHEET_REQUIRED_FILENAMES
from app.historical.checkpoints import HistoricalCheckpoint, _with_identity
from app.models import (
    ExistingEvidenceDerivedUseDecision,
    HistoricalEventPartition,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    IngestionRun,
    OpenDataDownloadManifest,
    RunStatus,
    SourceOwnerApproval,
)


FD1_RECOVERY_PUBLICATION_SCHEMA_VERSION = "gate3c_fd1_recovery_publication_v1"
TRUSTED_LEDGER_SCHEMA_VERSION = "gate3c_fd1_actual_publication_operator_v5"
FULL_PARTITION_COUNT = 329
RETROSHEET_PARTITION_COUNT = 324
PLAYER_PRIOR_PARTITION_COUNT = 5
FULL_IDENTITY_BATCH_COUNT = 75
DEFAULT_STAGE_BATCH_SIZE = 250
_DEFAULT_STAGE_TIMEOUT_MS = 30 * 60 * 1_000

_RECOVERY_STAGE_NAMESPACE = UUID("325d2832-e2e9-53c4-909c-2ca279ec0234")
_SHA256_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_RETROSHEET_EVENT_SOURCE_KEY = "RETROSHEET_EVENT_ARCHIVES"
_OBJECT_KINDS = tuple(RECOVERY_PUBLICATION_ORDER)
_ProgressCallback = Callable[["PublicationProgress"], None]
_StatementTimeout = int | Callable[[], int]


class FD1RecoveryPublicationError(RuntimeError):
    """Publication evidence is incomplete, inconsistent, or unsafe."""


class FD1RecoveryPublicationConflict(FD1RecoveryPublicationError):
    """A staged target collides with different canonical database evidence."""


@dataclass(frozen=True, slots=True)
class PublicationProgress:
    stage: str
    completed: int
    total: int
    current: str | None = None


@dataclass(frozen=True, slots=True)
class PublicationTarget:
    """One immutable canonical-row proposal stored as generic JSON."""

    object_kind: str
    target_id: str
    natural_key_checksum: str
    semantic_identity_checksum: str
    target_payload: Mapping[str, Any]
    payload_checksum: str

    def __post_init__(self) -> None:
        if self.object_kind not in RECOVERY_PUBLICATION_ORDER:
            raise FD1RecoveryPublicationError("Publication target kind is invalid")
        _uuid(self.target_id, "publication target ID")
        _sha256(self.natural_key_checksum, "publication natural key")
        _sha256(self.semantic_identity_checksum, "publication semantic identity")
        _sha256(self.payload_checksum, "publication payload")
        payload = _plain_json(self.target_payload)
        if set(payload).isdisjoint({"id"}) or str(payload.get("id")) != self.target_id:
            raise FD1RecoveryPublicationError("Publication payload target ID differs")
        if canonical_sha256(payload) != self.payload_checksum:
            raise FD1RecoveryPublicationError("Publication payload checksum differs")

    @classmethod
    def create(
        cls,
        *,
        object_kind: str,
        target_id: str | UUID,
        natural_key: object,
        semantic_identity_checksum: str,
        target_payload: Mapping[str, Any],
    ) -> "PublicationTarget":
        target = str(_uuid(target_id, "publication target ID"))
        payload = _plain_json({**dict(target_payload), "id": target})
        return cls(
            object_kind=object_kind,
            target_id=target,
            natural_key_checksum=canonical_sha256(natural_key),
            semantic_identity_checksum=semantic_identity_checksum,
            target_payload=MappingProxyType(payload),
            payload_checksum=canonical_sha256(payload),
        )

    @property
    def promotion_order(self) -> int:
        return RECOVERY_PUBLICATION_ORDER[self.object_kind]


@dataclass(frozen=True, slots=True)
class PublicationPlan:
    schema_version: str
    recovery_id: str
    scope: str
    targets: tuple[PublicationTarget, ...]
    plan_checksum: str

    def __post_init__(self) -> None:
        if self.schema_version != FD1_RECOVERY_PUBLICATION_SCHEMA_VERSION:
            raise FD1RecoveryPublicationError("Publication plan schema is invalid")
        _uuid(self.recovery_id, "publication recovery ID")
        _scope(self.scope)
        if not self.targets:
            raise FD1RecoveryPublicationError("Publication plan is empty")
        expected = tuple(
            sorted(
                self.targets,
                key=lambda item: (
                    item.promotion_order,
                    item.object_kind,
                    item.target_id,
                ),
            )
        )
        if expected != self.targets:
            raise FD1RecoveryPublicationError("Publication targets are not canonical")
        keys = [(item.object_kind, item.target_id) for item in self.targets]
        identities = [
            (item.object_kind, item.semantic_identity_checksum)
            for item in self.targets
        ]
        if len(keys) != len(set(keys)) or len(identities) != len(set(identities)):
            raise FD1RecoveryPublicationError("Publication targets are duplicated")
        _sha256(self.plan_checksum, "publication plan")
        if self.plan_checksum != canonical_sha256(self.unsigned_dict()):
            raise FD1RecoveryPublicationError("Publication plan checksum differs")

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "recovery_id": self.recovery_id,
            "schema_version": self.schema_version,
            "scope": self.scope,
            "targets": [
                {
                    "natural_key_checksum": item.natural_key_checksum,
                    "object_kind": item.object_kind,
                    "payload_checksum": item.payload_checksum,
                    "semantic_identity_checksum": item.semantic_identity_checksum,
                    "target_id": item.target_id,
                    "target_payload": dict(item.target_payload),
                }
                for item in self.targets
            ],
        }

    @classmethod
    def create(
        cls,
        *,
        recovery_id: str | UUID,
        scope: str,
        targets: Sequence[PublicationTarget],
    ) -> "PublicationPlan":
        selected_scope = _scope(scope)
        ordered = tuple(
            sorted(
                _deduplicate_targets(targets),
                key=lambda item: (
                    item.promotion_order,
                    item.object_kind,
                    item.target_id,
                ),
            )
        )
        unsigned = {
            "recovery_id": str(_uuid(recovery_id, "publication recovery ID")),
            "schema_version": FD1_RECOVERY_PUBLICATION_SCHEMA_VERSION,
            "scope": selected_scope,
            "targets": [
                {
                    "natural_key_checksum": item.natural_key_checksum,
                    "object_kind": item.object_kind,
                    "payload_checksum": item.payload_checksum,
                    "semantic_identity_checksum": item.semantic_identity_checksum,
                    "target_id": item.target_id,
                    "target_payload": dict(item.target_payload),
                }
                for item in ordered
            ],
        }
        return cls(
            schema_version=FD1_RECOVERY_PUBLICATION_SCHEMA_VERSION,
            recovery_id=unsigned["recovery_id"],
            scope=selected_scope,
            targets=ordered,
            plan_checksum=canonical_sha256(unsigned),
        )

    def count(self, object_kind: str) -> int:
        return sum(item.object_kind == object_kind for item in self.targets)


@dataclass(frozen=True, slots=True)
class PublicationStageSummary:
    plan_checksum: str
    targets: int
    inserted_stage_rows: int
    reused_stage_rows: int
    proposed_inserts: int
    proposed_reuses: int
    conflicts: int
    bounded_commits: int
    canonical_rows_written: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PublicationPromotionSummary:
    targets: int
    canonical_rows_inserted: int
    canonical_rows_reused: int
    stage_rows_marked_promoted: int
    transaction_committed_by_function: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RecoveryPublicationStageResult:
    scope: str
    plan_checksum: str
    partitions: int
    checkpoints: int
    ingestion_runs: int
    derived_use_decisions: int
    targets: int
    proposed_inserts: int
    proposed_reuses: int
    bounded_commits: int
    canonical_rows_written: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RecoveryPublicationPromotionResult:
    scope: str
    partitions: int
    checkpoints: int
    ingestion_runs: int
    derived_use_decisions: int
    targets: int
    canonical_rows_inserted: int
    canonical_rows_reused: int
    transaction_committed_by_function: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _SourceContext:
    source_key: str
    source_version_id: UUID
    source_identity_checksum: str
    owner_approval_id: UUID
    owner_decided_at: datetime
    owner_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class _Download:
    id: UUID
    source_version_id: UUID
    source_key: str
    object_key: str
    source_release: str
    upstream_revision: int
    downloaded_at: datetime
    content_length: int
    payload_sha256: str
    archive_sha256: str | None
    raw_logical_uri: str
    identity_checksum: str


@dataclass(frozen=True, slots=True)
class _RegistrySnapshot:
    contexts: Mapping[str, _SourceContext]
    downloads: tuple[_Download, ...]

    def context(self, source_key: str) -> _SourceContext:
        try:
            return self.contexts[source_key]
        except KeyError as exc:
            raise FD1RecoveryPublicationError(
                f"Source {source_key} is absent from frozen recovery lineage"
            ) from exc


@dataclass(frozen=True, slots=True)
class _RetrosheetLedgerUnit:
    season: int
    month: int
    family: str
    partition_checksum: str
    source_bundle_checksum: str
    quality_report: Mapping[str, Any]
    row_count: int


def load_trusted_v5_dry_run_ledger(
    project_root: Path,
    binding: TrustedDryRunLedger,
) -> Mapping[str, Any]:
    """Load the one content-addressed ledger named by the recovery manifest."""

    root = Path(project_root).resolve(strict=True)
    path = root.joinpath(*PurePosixPath(binding.logical_uri).parts).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise FD1RecoveryPublicationError("Trusted ledger escapes project root") from exc
    if not path.is_file() or _linklike(path):
        raise FD1RecoveryPublicationError("Trusted ledger is not a safe regular file")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != binding.sha256:
        raise FD1RecoveryPublicationError("Trusted ledger checksum changed")
    try:
        document = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FD1RecoveryPublicationError("Trusted ledger JSON is invalid") from exc
    if not isinstance(document, Mapping):
        raise FD1RecoveryPublicationError("Trusted ledger root is not an object")
    if document.get("schema_version") != binding.schema_version:
        raise FD1RecoveryPublicationError("Trusted ledger schema binding changed")
    return MappingProxyType(dict(document))


def build_fd1_recovery_publication_plan(
    connection: Connection,
    *,
    manifest: RecoveryManifest,
    trusted_ledger: Mapping[str, Any],
    parquet_root: Path,
    extraction_artifacts: Sequence[ExtractionArtifact],
    scope: str = "FULL",
    sample_logical_uris: Sequence[str] = (),
    include_identity_runs: bool = True,
    strict_full_counts: bool = True,
    progress: _ProgressCallback | None = None,
    statement_timeout_ms: _StatementTimeout | None = None,
) -> PublicationPlan:
    """Reconstruct canonical target rows without writing canonical state.

    ``sample_logical_uris`` selects an exact representative subset for a
    rehearsal.  Full mode rejects a subset and, by default, proves the expected
    329 partition and 75 identity-batch populations.
    """

    selected_scope = _scope(scope)
    _validate_trusted_ledger(manifest, trusted_ledger)
    registry = _load_registry_snapshot(
        connection,
        manifest,
        statement_timeout_ms=statement_timeout_ms,
    )
    extractions = _extraction_index(extraction_artifacts, registry)
    _validate_extractions_against_ledger(trusted_ledger, extractions)
    ledger_units = _retrosheet_ledger_units(trusted_ledger)
    _validate_ledger_partition_population(manifest, ledger_units)
    entries = _select_manifest_entries(
        manifest,
        scope=selected_scope,
        sample_logical_uris=sample_logical_uris,
    )
    execution_at = _generation_execution_at(manifest)
    root = Path(parquet_root).resolve(strict=True)
    retro_contributors = _retrosheet_partition_contributors(registry, extractions)
    targets: list[PublicationTarget] = []

    for ordinal, entry in enumerate(entries, start=1):
        path = _resolve_manifest_file(root, entry)
        try:
            frame = pl.read_parquet(path)
        except (OSError, pl.exceptions.PolarsError) as exc:
            raise FD1RecoveryPublicationError(
                f"Manifested Parquet is unreadable: {entry.logical_uri}"
            ) from exc
        if frame.height != entry.row_count:
            raise FD1RecoveryPublicationError("Parquet row count changed after manifest")
        if _parquet_checksum(_schema_document(frame)) != entry.physical_schema_fingerprint:
            raise FD1RecoveryPublicationError("Parquet schema changed after manifest")
        targets.extend(
            _partition_targets(
                entry=entry,
                frame=frame,
                registry=registry,
                retrosheet_unit=ledger_units.get(
                    (entry.season, entry.month, entry.family)
                ),
                retro_contributors=retro_contributors,
                code_revision=manifest.generation_lineage.code_revision,
                execution_at=execution_at,
            )
        )
        if progress is not None:
            progress(
                PublicationProgress(
                    stage="PLAN_PARTITIONS",
                    completed=ordinal,
                    total=len(entries),
                    current=entry.logical_uri,
                )
            )

    identity_batches = 0
    if include_identity_runs:
        # Local Parquet verification can consume material wall time.  Refresh
        # the transaction-local allowance immediately before the late identity
        # staging query instead of retaining the value from plan entry.
        _apply_publication_timeout(connection, statement_timeout_ms)
        identity_targets, identity_batches = _identity_targets(
            connection,
            recovery_id=UUID(manifest.recovery_id),
            scope=selected_scope,
            registry=registry,
            extractions=extractions,
            code_revision=manifest.generation_lineage.code_revision,
            execution_at=execution_at,
            statement_timeout_ms=statement_timeout_ms,
        )
        targets.extend(identity_targets)

    plan = PublicationPlan.create(
        recovery_id=manifest.recovery_id,
        scope=selected_scope,
        targets=targets,
    )
    if selected_scope == "FULL" and strict_full_counts:
        if sample_logical_uris:
            raise FD1RecoveryPublicationError("FULL publication cannot use a sample")
        if len(entries) != FULL_PARTITION_COUNT:
            raise FD1RecoveryPublicationError("Full recovery does not contain 329 files")
        if plan.count("PARTITION") != FULL_PARTITION_COUNT:
            raise FD1RecoveryPublicationError("Full recovery partition plan is incomplete")
        if plan.count("CHECKPOINT") != FULL_PARTITION_COUNT:
            raise FD1RecoveryPublicationError("Full recovery checkpoint plan is incomplete")
        if include_identity_runs and identity_batches != FULL_IDENTITY_BATCH_COUNT:
            raise FD1RecoveryPublicationError("Full recovery does not contain 75 identity batches")
        expected_runs = FULL_PARTITION_COUNT + (
            FULL_IDENTITY_BATCH_COUNT if include_identity_runs else 0
        )
        if plan.count("INGESTION_RUN") != expected_runs:
            raise FD1RecoveryPublicationError("Full recovery ingestion-run plan is incomplete")
    return plan


def stage_recovery_publication(
    engine: Engine,
    manifest: RecoveryManifest,
    trusted_ledger_path: Path,
    store_root: Path,
    *,
    scope: str = "FULL",
    rehearsal: bool | Sequence[str] = False,
    progress_callback: _ProgressCallback | None = None,
    extraction_artifacts: Sequence[ExtractionArtifact] = (),
    project_root: Path | None = None,
    batch_size: int = DEFAULT_STAGE_BATCH_SIZE,
    statement_timeout_ms: _StatementTimeout | None = None,
) -> RecoveryPublicationStageResult:
    """Convenience entry point used by the recovery orchestrator.

    When extraction artifacts are not injected, this wrapper loads only the
    exact manifest paths named by the trusted ledger, with member-byte
    verification disabled.  The prior dry run's content-addressed ledger is
    the proof that those member bytes were already verified.
    """

    selected_scope = _scope(scope)
    if rehearsal and selected_scope != "REHEARSAL":
        raise FD1RecoveryPublicationError("A rehearsal requires REHEARSAL scope")
    ledger_path = Path(trusted_ledger_path).resolve(strict=True)
    root = (
        Path(project_root).resolve(strict=True)
        if project_root is not None
        else _derive_project_root(ledger_path, manifest.generation_lineage.trusted_dry_run_ledger)
    )
    expected_ledger = root.joinpath(
        *PurePosixPath(
            manifest.generation_lineage.trusted_dry_run_ledger.logical_uri
        ).parts
    ).resolve(strict=True)
    if expected_ledger != ledger_path:
        raise FD1RecoveryPublicationError("Ledger path differs from manifest binding")
    ledger = load_trusted_v5_dry_run_ledger(
        root, manifest.generation_lineage.trusted_dry_run_ledger
    )
    artifacts = tuple(extraction_artifacts) or _load_explicit_extractions(root, ledger)
    if isinstance(rehearsal, Sequence) and not isinstance(rehearsal, (str, bytes)):
        sample = tuple(str(item) for item in rehearsal)
    elif selected_scope == "REHEARSAL":
        sample = _representative_sample(manifest)
    else:
        sample = ()
    with engine.connect() as connection:
        _apply_publication_timeout(connection, statement_timeout_ms)
        plan = build_fd1_recovery_publication_plan(
            connection,
            manifest=manifest,
            trusted_ledger=ledger,
            parquet_root=store_root,
            extraction_artifacts=artifacts,
            scope=selected_scope,
            sample_logical_uris=sample,
            progress=progress_callback,
            statement_timeout_ms=statement_timeout_ms,
        )
    staged = stage_publication_plan(
        engine,
        run_id=UUID(manifest.recovery_id),
        scope=selected_scope,
        plan=plan,
        batch_size=batch_size,
        progress=progress_callback,
        statement_timeout_ms=statement_timeout_ms,
    )
    return RecoveryPublicationStageResult(
        scope=selected_scope,
        plan_checksum=plan.plan_checksum,
        partitions=plan.count("PARTITION"),
        checkpoints=plan.count("CHECKPOINT"),
        ingestion_runs=plan.count("INGESTION_RUN"),
        derived_use_decisions=plan.count("DERIVED_USE_DECISION"),
        targets=len(plan.targets),
        proposed_inserts=staged.proposed_inserts,
        proposed_reuses=staged.proposed_reuses,
        bounded_commits=staged.bounded_commits,
    )


def promote_recovery_publication(
    connection: Connection,
    run_id: UUID,
    scope: str = "FULL",
    *,
    allow_rehearsal_rollback: bool = False,
    promoted_at: datetime | None = None,
    batch_size: int = DEFAULT_STAGE_BATCH_SIZE,
    progress_callback: _ProgressCallback | None = None,
    statement_timeout_ms: _StatementTimeout | None = None,
) -> RecoveryPublicationPromotionResult:
    """Orchestrator spelling with per-kind accounting and no commit.

    Rehearsal writes require the explicit rollback-only flag.  The caller must
    roll that transaction back after exercising validation.
    """

    selected_scope = _scope(scope)
    if not connection.in_transaction():
        raise FD1RecoveryPublicationError(
            "Promotion requires a caller-owned transaction"
        )
    _apply_publication_timeout(connection, statement_timeout_ms)
    table = FD1RecoveryPublicationStage.__table__
    rows = list(
        connection.execute(
            select(table.c.object_kind).where(
                table.c.run_id == run_id, table.c.scope == selected_scope
            )
        )
    )
    counts = {kind: 0 for kind in _OBJECT_KINDS}
    for (kind,) in rows:
        counts[str(kind)] += 1
    summary = promote_publication_plan(
        connection,
        run_id=run_id,
        scope=selected_scope,
        allow_rehearsal_rollback=allow_rehearsal_rollback,
        promoted_at=promoted_at,
        batch_size=batch_size,
        progress=progress_callback,
        statement_timeout_ms=statement_timeout_ms,
    )
    return RecoveryPublicationPromotionResult(
        scope=selected_scope,
        partitions=counts["PARTITION"],
        checkpoints=counts["CHECKPOINT"],
        ingestion_runs=counts["INGESTION_RUN"],
        derived_use_decisions=counts["DERIVED_USE_DECISION"],
        targets=summary.targets,
        canonical_rows_inserted=summary.canonical_rows_inserted,
        canonical_rows_reused=summary.canonical_rows_reused,
    )


def stage_publication_plan(
    bind: Engine | Connection,
    *,
    run_id: UUID,
    scope: str,
    plan: PublicationPlan,
    validated_at: datetime | None = None,
    batch_size: int = DEFAULT_STAGE_BATCH_SIZE,
    progress: _ProgressCallback | None = None,
    statement_timeout_ms: _StatementTimeout | None = None,
) -> PublicationStageSummary:
    """Persist an invisible plan in bounded, independently committed chunks.

    Passing an :class:`~sqlalchemy.engine.Engine` enables one commit per chunk
    and is the normal recovery path.  A ``Connection`` is supported for tests
    and caller-managed transactions but is reported as one uncommitted batch.
    """

    selected_scope = _scope(scope)
    if plan.recovery_id != str(run_id) or plan.scope != selected_scope:
        raise FD1RecoveryPublicationError("Publication plan/run binding differs")
    _positive_int(batch_size, "publication stage batch size")
    observed_at = _aware(validated_at or datetime.now(UTC), "validated_at")
    inserted = reused = proposed_inserts = proposed_reuses = conflicts = commits = 0

    for start in range(0, len(plan.targets), batch_size):
        chunk = plan.targets[start : start + batch_size]
        if isinstance(bind, Engine):
            with bind.begin() as connection:
                _apply_publication_timeout(connection, statement_timeout_ms)
                result = _stage_chunk(
                    connection,
                    run_id=run_id,
                    scope=selected_scope,
                    plan=plan,
                    targets=chunk,
                    ordinal_offset=start,
                    validated_at=observed_at,
                    statement_timeout_ms=statement_timeout_ms,
                )
            commits += 1
        else:
            _apply_publication_timeout(bind, statement_timeout_ms)
            result = _stage_chunk(
                bind,
                run_id=run_id,
                scope=selected_scope,
                plan=plan,
                targets=chunk,
                ordinal_offset=start,
                validated_at=observed_at,
                statement_timeout_ms=statement_timeout_ms,
            )
        inserted += result[0]
        reused += result[1]
        proposed_inserts += result[2]
        proposed_reuses += result[3]
        conflicts += result[4]
        if progress is not None:
            progress(
                PublicationProgress(
                    stage="STAGE_PUBLICATION",
                    completed=min(start + len(chunk), len(plan.targets)),
                    total=len(plan.targets),
                    current=chunk[-1].target_id,
                )
            )

    _verify_staged_population(
        bind,
        run_id,
        selected_scope,
        plan,
        statement_timeout_ms=statement_timeout_ms,
    )
    summary = PublicationStageSummary(
        plan_checksum=plan.plan_checksum,
        targets=len(plan.targets),
        inserted_stage_rows=inserted,
        reused_stage_rows=reused,
        proposed_inserts=proposed_inserts,
        proposed_reuses=proposed_reuses,
        conflicts=conflicts,
        bounded_commits=commits,
    )
    if conflicts:
        raise FD1RecoveryPublicationConflict(
            f"Publication staging found {conflicts} canonical conflict(s)"
        )
    return summary


def promote_publication_plan(
    connection: Connection,
    *,
    run_id: UUID,
    scope: str,
    allow_rehearsal_rollback: bool = False,
    promoted_at: datetime | None = None,
    batch_size: int = DEFAULT_STAGE_BATCH_SIZE,
    progress: _ProgressCallback | None = None,
    statement_timeout_ms: _StatementTimeout | None = None,
) -> PublicationPromotionSummary:
    """Promote atomically inside the caller's open transaction.

    This function deliberately has no commit call.  The caller must promote
    identity mappings and mark the recovery run ``FINALIZED`` in the same
    transaction before committing a FULL run.  A REHEARSAL is accepted only
    with explicit rollback-only authorization and must be rolled back by its
    caller.
    """

    selected_scope = _scope(scope)
    if selected_scope == "REHEARSAL" and not allow_rehearsal_rollback:
        raise FD1RecoveryPublicationError(
            "REHEARSAL promotion requires explicit rollback-only authorization"
        )
    if selected_scope == "FULL" and allow_rehearsal_rollback:
        raise FD1RecoveryPublicationError(
            "Rollback-only rehearsal authorization is invalid for FULL promotion"
        )
    if not connection.in_transaction():
        raise FD1RecoveryPublicationError("Promotion requires a caller-owned transaction")
    _positive_int(batch_size, "publication promotion batch size")
    observed_at = _aware(promoted_at or datetime.now(UTC), "promoted_at")
    stage = FD1RecoveryPublicationStage.__table__
    _apply_publication_timeout(connection, statement_timeout_ms)
    rows = list(
        connection.execute(
            select(stage)
            .where(stage.c.run_id == run_id, stage.c.scope == selected_scope)
            .order_by(stage.c.promotion_order, stage.c.ordinal)
        ).mappings()
    )
    if not rows:
        raise FD1RecoveryPublicationError("Publication promotion has no staged rows")
    if any(row["action"] in {"PENDING", "CONFLICT"} for row in rows):
        raise FD1RecoveryPublicationConflict("Publication stage is not conflict-free")
    if [row["ordinal"] for row in rows] != list(range(1, len(rows) + 1)):
        raise FD1RecoveryPublicationError("Publication stage ordinals are not contiguous")

    inserted = reused = marked = 0
    for kind in _OBJECT_KINDS:
        selected = [row for row in rows if row["object_kind"] == kind]
        for start in range(0, len(selected), batch_size):
            _apply_publication_timeout(connection, statement_timeout_ms)
            chunk_rows = selected[start : start + batch_size]
            targets = [_target_from_stage(row) for row in chunk_rows]
            classifications = _classify_targets(
                connection,
                targets,
                statement_timeout_ms=statement_timeout_ms,
            )
            pending_inserts: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
            stage_updates: list[dict[str, Any]] = []
            for row, target, classification in zip(
                chunk_rows, targets, classifications, strict=True
            ):
                action, matched_id, reason = classification
                if action == "CONFLICT":
                    raise FD1RecoveryPublicationConflict(
                        f"Promotion conflict for {target.object_kind} {target.target_id}: {reason}"
                    )
                if row["promoted_at"] is not None:
                    if action != "REUSE":
                        raise FD1RecoveryPublicationConflict(
                            "A promoted stage row no longer has exact canonical evidence"
                        )
                    reused += 1
                    continue
                if row["action"] == "REUSE" and action != "REUSE":
                    raise FD1RecoveryPublicationConflict(
                        "A staged canonical reuse target disappeared or changed"
                    )
                if action == "INSERT":
                    pending_inserts.append((row, target.target_payload))
                else:
                    reused += 1
                    stage_updates.append(
                        {
                            "stage_id": row["id"],
                            "action": "REUSE",
                            "matched_target_id": matched_id,
                            "promoted_at": observed_at,
                        }
                    )

            if pending_inserts:
                table = _target_table(kind)
                payloads = [
                    _coerce_payload(table, payload)
                    for _, payload in pending_inserts
                ]
                try:
                    _apply_publication_timeout(connection, statement_timeout_ms)
                    connection.execute(table.insert(), payloads)
                except IntegrityError as exc:
                    raise FD1RecoveryPublicationConflict(
                        f"Concurrent canonical collision while inserting {kind}"
                    ) from exc
                inserted_targets = [
                    _target_from_stage(row) for row, _ in pending_inserts
                ]
                verified = _classify_targets(
                    connection,
                    inserted_targets,
                    statement_timeout_ms=statement_timeout_ms,
                )
                if any(action != "REUSE" for action, _, _ in verified):
                    raise FD1RecoveryPublicationError(
                        f"Inserted {kind} rows failed exact read-back validation"
                    )
                inserted += len(pending_inserts)
                for row, _ in pending_inserts:
                    stage_updates.append(
                        {
                            "stage_id": row["id"],
                            "action": "INSERT",
                            "matched_target_id": None,
                            "promoted_at": observed_at,
                        }
                    )
            for values in stage_updates:
                _apply_publication_timeout(connection, statement_timeout_ms)
                result = connection.execute(
                    update(stage)
                    .where(stage.c.id == values.pop("stage_id"))
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise FD1RecoveryPublicationError(
                        "Publication stage promotion marker was not unique"
                    )
            marked += len(stage_updates)
            if progress is not None and chunk_rows:
                progress(
                    PublicationProgress(
                        stage=f"PROMOTE_{kind}",
                        completed=start + len(chunk_rows),
                        total=len(selected),
                        current=str(chunk_rows[-1]["target_id"]),
                    )
                )
    return PublicationPromotionSummary(
        targets=len(rows),
        canonical_rows_inserted=inserted,
        canonical_rows_reused=reused,
        stage_rows_marked_promoted=marked,
    )


def _partition_targets(
    *,
    entry: RecoveryFileEntry,
    frame: pl.DataFrame,
    registry: _RegistrySnapshot,
    retrosheet_unit: _RetrosheetLedgerUnit | None,
    retro_contributors: tuple[Mapping[str, Any], ...],
    code_revision: str,
    execution_at: datetime,
) -> tuple[PublicationTarget, ...]:
    inferred = _inferred_metadata(frame)
    if inferred["min_event_date"] != entry.min_date or inferred["max_event_date"] != entry.max_date:
        raise FD1RecoveryPublicationError("Parquet date bounds differ from manifest")
    source_key = (
        LAHMAN_SOURCE_KEY if entry.family == "player_prior" else RETROSHEET_SOURCE_KEY
    )
    context = registry.context(source_key)
    if str(context.source_version_id) != entry.source_version_id:
        raise FD1RecoveryPublicationError("Parquet source-version lineage differs")

    if entry.family == "player_prior":
        prior = _player_prior_evidence(frame, registry)
        contributors = prior["contributors"]
        unit_checksum = prior["payload_checksum"]
        batch_number = 1
        quality = {
            "population": prior["population"],
            "annual_period_end_partition": True,
            "same_season_predictor_eligible": False,
            "provider_requests": 0,
        }
        run_result = {
            "payload_checksum": unit_checksum,
            "event_family": entry.family,
            "season": entry.season,
            "month": entry.month,
            "batch_number": batch_number,
        }
        unit_kind = "partition:player_prior"
    else:
        if retrosheet_unit is None:
            raise FD1RecoveryPublicationError("Retrosheet file lacks one ledger batch")
        if retrosheet_unit.row_count != entry.row_count:
            raise FD1RecoveryPublicationError("Ledger family row count differs from Parquet")
        contributors = retro_contributors
        unit_checksum = retrosheet_unit.partition_checksum
        batch_number = 1
        quality = {
            "publication_scope": "COMPLETE_MONTH_PARTITION",
            "source_bundle_checksum": retrosheet_unit.source_bundle_checksum,
            "leakage_policy": (
                "RETROSPECTIVE_TARGETS_AND_POSTGAME_PRIORS_ONLY; CURRENT_GAME_AND_"
                "FUTURE_EXCLUDED; ACTUAL_LINEUP_STARTER_RELIEVER_FIELDS_ENHANCED_ONLY"
            ),
            "quality_report": dict(retrosheet_unit.quality_report),
            "provider_requests": 0,
        }
        run_result = {
            "partition_checksum": unit_checksum,
            "event_family": entry.family,
            "season": entry.season,
            "month": entry.month,
        }
        unit_kind = f"partition:{entry.family}"

    decision = _derived_use_target(context, contributors)
    run = _ingestion_run_target(
        code_revision=code_revision,
        execution_at=execution_at,
        unit_kind=unit_kind,
        unit_identity_checksum=unit_checksum,
        records_processed=entry.row_count,
        result=run_result,
    )
    checkpoint = _checkpoint_target(
        entry=entry,
        context=context,
        ingestion_run_id=UUID(run.target_id),
        batch_number=batch_number,
        batch_checksum=unit_checksum,
        contributors=contributors,
        code_revision=code_revision,
        execution_at=execution_at,
        quality_summary=quality,
    )
    partition = _partition_target(
        entry=entry,
        frame=frame,
        inferred=inferred,
        context=context,
        checkpoint_id=UUID(checkpoint.target_id),
        contributors=contributors,
        code_revision=code_revision,
    )
    return decision, run, checkpoint, partition


def _derived_use_target(
    context: _SourceContext,
    contributors: Sequence[Mapping[str, Any]],
) -> PublicationTarget:
    manifest_checksum = _manifest_checksum(contributors)
    retrievals = [
        _aware(datetime.fromisoformat(str(item["retrieved_at"])), "retrieved_at")
        for item in contributors
    ]
    if not retrievals:
        raise FD1RecoveryPublicationError("Derived-use contributor set is empty")
    decided_at = max(context.owner_decided_at, max(retrievals))
    fields = {
        "owner_approval_id": str(context.owner_approval_id),
        "source_version_id": str(context.source_version_id),
        "source_key": context.source_key,
        "evidence_manifest_checksum": manifest_checksum,
        "use_scope": "LOCAL_PRIVATE_RESEARCH",
        "decision": "ALLOWED",
        "allowed": True,
        "decided_by": "gate3c-fd1-provider-free-warehouse-operator",
        "decided_at": decided_at.isoformat(),
        "expires_at": (
            context.owner_expires_at.isoformat()
            if context.owner_expires_at is not None
            else None
        ),
        "decision_basis": (
            "Project-owner-approved local private research derivation from the exact "
            "registered contributor manifest; no provider request is constructed."
        ),
    }
    identity = canonical_sha256(
        {
            "record_type": "FD1_EXISTING_EVIDENCE_DERIVED_USE",
            "schema_version": FD1_WAREHOUSE_SCHEMA_VERSION,
            "source_version_identity_checksum": context.source_identity_checksum,
            **fields,
        }
    )
    target_id = uuid5(_PUBLICATION_NAMESPACE, f"derived-use:{identity}")
    return PublicationTarget.create(
        object_kind="DERIVED_USE_DECISION",
        target_id=target_id,
        natural_key={"identity_checksum": identity},
        semantic_identity_checksum=identity,
        target_payload={**fields, "identity_checksum": identity},
    )


def _ingestion_run_target(
    *,
    code_revision: str,
    execution_at: datetime,
    unit_kind: str,
    unit_identity_checksum: str,
    records_processed: int,
    result: Mapping[str, Any],
) -> PublicationTarget:
    _sha256(unit_identity_checksum, "publication unit identity")
    sources = {
        key: build_source_release_identity(value.release_spec())
        for key, value in sorted(FD1_SOURCE_DEFINITIONS.items())
    }
    identity = canonical_sha256(
        {
            "schema_version": FD1_WAREHOUSE_SCHEMA_VERSION,
            "job_name": FD1_INGESTION_JOB,
            "code_revision": code_revision,
            "unit_kind": unit_kind,
            "unit_identity_checksum": unit_identity_checksum,
            "sources": sources,
        }
    )
    target_id = uuid5(_PUBLICATION_NAMESPACE, f"ingestion:{identity}")
    metadata = {
        "publication_identity_checksum": identity,
        "code_revision": code_revision,
        "source_version_identity_checksums": sources,
        "provider_transport_constructed": False,
        "network_requests": 0,
        "transaction_committed_by_operator": False,
        "unit_kind": unit_kind,
        "unit_identity_checksum": unit_identity_checksum,
        "result": dict(result),
    }
    payload = {
        "source": FD1_INGESTION_SOURCE,
        "job_name": FD1_INGESTION_JOB,
        "status": "SUCCEEDED",
        "started_at": execution_at.isoformat(),
        "completed_at": execution_at.isoformat(),
        "records_processed": records_processed,
        "warnings": [],
        "error_message": None,
        "raw_reference": None,
        "schema_version": FD1_WAREHOUSE_SCHEMA_VERSION,
        "source_metadata": metadata,
    }
    return PublicationTarget.create(
        object_kind="INGESTION_RUN",
        target_id=target_id,
        natural_key={"publication_identity_checksum": identity},
        semantic_identity_checksum=identity,
        target_payload=payload,
    )


def _checkpoint_target(
    *,
    entry: RecoveryFileEntry,
    context: _SourceContext,
    ingestion_run_id: UUID,
    batch_number: int,
    batch_checksum: str,
    contributors: Sequence[Mapping[str, Any]],
    code_revision: str,
    execution_at: datetime,
    quality_summary: Mapping[str, Any],
) -> PublicationTarget:
    manifest_checksum = _manifest_checksum(contributors)
    query = canonical_sha256(
        {
            "schema_version": FD1_WAREHOUSE_SCHEMA_VERSION,
            "source_version_identity_checksum": context.source_identity_checksum,
            "family": entry.family,
            "season": entry.season,
            "month": entry.month,
            "batch_number": batch_number,
            "batch_checksum": batch_checksum,
            "raw_contributor_checksum": manifest_checksum,
            "code_revision": code_revision,
        }
    )
    target_id = uuid5(_PUBLICATION_NAMESPACE, f"checkpoint:{query}")
    if str(target_id) != entry.checkpoint_id:
        raise FD1RecoveryPublicationError(
            "Ledger/contributor evidence does not reproduce Parquet checkpoint ID"
        )
    governed_quality = {
        **dict(quality_summary),
        "raw_contributor_checksum": manifest_checksum,
        "batch_checksum": batch_checksum,
        "publication_operation": "LOCAL_DERIVED_PROCESSING",
        "provider_transport_constructed": False,
        "network_requests": 0,
        "attempt_semantics": "ONE_LOCAL_PUBLICATION_ATTEMPT",
    }
    checkpoint = _with_identity(
        HistoricalCheckpoint(
            checkpoint_id=str(target_id),
            ingestion_run_id=str(ingestion_run_id),
            source_version_id=str(context.source_version_id),
            stream_key=f"fd1:{entry.family}",
            sequence=entry.season * 10_000 + entry.month * 100 + batch_number,
            season=entry.season,
            event_start_at=datetime.combine(
                date.fromisoformat(entry.min_date), time.min, tzinfo=UTC
            ),
            event_end_at=datetime.combine(
                date.fromisoformat(entry.max_date), time.max, tzinfo=UTC
            ),
            query_fingerprint=query,
            status="SUCCEEDED",
            requested_at=execution_at,
            completed_at=execution_at,
            attempt_count=1,
            retry_count=0,
            records_received=entry.row_count,
            records_accepted=entry.row_count,
            records_rejected=0,
            duplicate_count=0,
            raw_references=tuple(str(item["logical_uri"]) for item in contributors),
            raw_checksums=tuple(str(item["file_sha256"]) for item in contributors),
            schema_version=FD1_WAREHOUSE_SCHEMA_VERSION,
            code_revision=code_revision,
            quality_summary=dict(governed_quality),
        )
    )
    values = asdict(checkpoint)
    values["id"] = values.pop("checkpoint_id")
    return PublicationTarget.create(
        object_kind="CHECKPOINT",
        target_id=target_id,
        natural_key={
            "ingestion_run_id": str(ingestion_run_id),
            "stream_key": checkpoint.stream_key,
            "sequence": checkpoint.sequence,
        },
        semantic_identity_checksum=checkpoint.identity_checksum,
        target_payload=_plain_json(values),
    )


def _partition_target(
    *,
    entry: RecoveryFileEntry,
    frame: pl.DataFrame,
    inferred: Mapping[str, Any],
    context: _SourceContext,
    checkpoint_id: UUID,
    contributors: Sequence[Mapping[str, Any]],
    code_revision: str,
) -> PublicationTarget:
    contributor_checksum = _manifest_checksum(contributors)
    governance = {
        "source_key": context.source_key,
        "source_version_id": str(context.source_version_id),
        "checkpoint_id": str(checkpoint_id),
        "event_family": entry.family,
        "season": entry.season,
        "month": entry.month,
        "schema_version": entry.declared_schema,
        "schema_hash": entry.physical_schema_fingerprint,
        "raw_contributor_checksum": contributor_checksum,
        "code_revision": code_revision,
        "evidence_tier": "A",
        "timing_classification": (
            PLAYER_PRIOR_TIMING_CLASSIFICATION
            if entry.family == "player_prior"
            else _TIMING_BY_RETROSHEET_FAMILY[entry.family]
        ),
    }
    if _parquet_checksum(governance) != entry.governance_checksum:
        raise FD1RecoveryPublicationError(
            "Reconstructed governance does not match immutable Parquet path"
        )
    semantic = {
        **governance,
        "logical_uri": entry.logical_uri,
        "row_count": entry.row_count,
        "game_count": int(inferred["game_count"]),
        "plate_appearance_count": int(inferred["plate_appearance_count"]),
        "player_count": int(inferred["player_count"]),
        "min_event_date": inferred["min_event_date"],
        "max_event_date": inferred["max_event_date"],
        "min_event_at": inferred["min_event_at"],
        "max_event_at": inferred["max_event_at"],
        "storage_format": "PARQUET",
        "compression": "ZSTD",
        "file_sha256": entry.sha256,
        "file_size_bytes": entry.size_bytes,
        "raw_contributor_manifest": tuple(dict(item) for item in contributors),
    }
    identity = _parquet_checksum(semantic)
    artifact = HistoricalEventPartitionArtifact(
        identity_checksum=identity,
        reused=True,
        **semantic,
    )
    # Canonical partition rows historically used a random ORM default.  The
    # interrupted transaction left no durable UUID to preserve, so recovery
    # deterministically derives the otherwise-unobservable row ID from the
    # exact artifact identity.
    target_id = uuid5(_PUBLICATION_NAMESPACE, f"partition:{identity}")
    payload = _plain_json(artifact.orm_values())
    return PublicationTarget.create(
        object_kind="PARTITION",
        target_id=target_id,
        natural_key={"logical_uri": entry.logical_uri},
        semantic_identity_checksum=identity,
        target_payload=payload,
    )


def _identity_targets(
    connection: Connection,
    *,
    recovery_id: UUID,
    scope: str,
    registry: _RegistrySnapshot,
    extractions: Mapping[str, ExtractionArtifact],
    code_revision: str,
    execution_at: datetime,
    statement_timeout_ms: _StatementTimeout | None = None,
) -> tuple[tuple[PublicationTarget, ...], int]:
    stage = FD1RecoveryIdentityStage.__table__
    _apply_publication_timeout(connection, statement_timeout_ms)
    conflict_count = connection.scalar(
        select(stage.c.staging_ordinal)
        .where(
            stage.c.run_id == recovery_id,
            stage.c.scope == scope,
            stage.c.outcome == "CONFLICT",
        )
        .limit(1)
    )
    if conflict_count is not None:
        raise FD1RecoveryPublicationConflict(
            "Identity reconciliation contains a blocking semantic conflict"
        )
    _apply_publication_timeout(connection, statement_timeout_ms)
    rows = list(
        connection.execute(
            select(
                stage.c.batch_number,
                stage.c.batch_checksum,
                stage.c.source_key,
                stage.c.evidence_logical_uri,
                stage.c.staging_ordinal,
            )
            .where(stage.c.run_id == recovery_id, stage.c.scope == scope)
            .order_by(stage.c.batch_number, stage.c.staging_ordinal)
        ).mappings()
    )
    if not rows:
        raise FD1RecoveryPublicationError("Identity publication has no staged rows")
    batches: dict[tuple[int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        number = int(row["batch_number"])
        checksum = str(row["batch_checksum"])
        batches[(number, checksum)].append(row)
    targets: list[PublicationTarget] = []
    for (number, checksum), batch_rows in sorted(batches.items()):
        source_keys = sorted({str(item["source_key"]) for item in batch_rows})
        for source_key in source_keys:
            counts: dict[str, int] = defaultdict(int)
            for item in batch_rows:
                if item["source_key"] == source_key:
                    counts[str(item["evidence_logical_uri"])] += 1
            contributors = _identity_contributors(
                source_key,
                counts,
                registry=registry,
                extractions=extractions,
            )
            targets.append(_derived_use_target(registry.context(source_key), contributors))
        targets.append(
            _ingestion_run_target(
                code_revision=code_revision,
                execution_at=execution_at,
                unit_kind="identity_mapping",
                unit_identity_checksum=checksum,
                records_processed=len(batch_rows),
                result={
                    "identity_mapping_batch_checksum": checksum,
                    "batch_number": number,
                    "source_keys": source_keys,
                },
            )
        )
    return tuple(targets), len(batches)


def _load_registry_snapshot(
    connection: Connection,
    manifest: RecoveryManifest,
    *,
    statement_timeout_ms: _StatementTimeout | None = None,
) -> _RegistrySnapshot:
    source_table = HistoricalSourceVersion.__table__
    owner_table = SourceOwnerApproval.__table__
    download_table = OpenDataDownloadManifest.__table__
    contexts: dict[str, _SourceContext] = {}
    bindings = manifest.generation_lineage.source_releases
    source_ids = [UUID(item.source_version_id) for item in bindings]
    _apply_publication_timeout(connection, statement_timeout_ms)
    source_rows = {
        row["id"]: row
        for row in connection.execute(
            select(source_table).where(source_table.c.id.in_(source_ids))
        ).mappings()
    }
    for binding in bindings:
        source_id = UUID(binding.source_version_id)
        row = source_rows.get(source_id)
        if row is None:
            raise FD1RecoveryPublicationError("Frozen source release is absent")
        expected = {
            "source_key": binding.source_key,
            "dataset_version": binding.dataset_version,
            "identity_checksum": binding.identity_checksum,
            "source_mode": binding.source_mode,
        }
        _assert_fields(row, expected, "source release")
        definition = FD1_SOURCE_DEFINITIONS.get(binding.source_key)
        if definition is None:
            raise FD1RecoveryPublicationError("Source release is outside FD1 registry")
        if build_source_release_identity(definition.release_spec()) != binding.identity_checksum:
            raise FD1RecoveryPublicationError("Source release definition changed")
        owner_identity = build_owner_decision_identity(definition.owner_spec())
        _apply_publication_timeout(connection, statement_timeout_ms)
        owners = list(
            connection.execute(
                select(owner_table).where(
                    owner_table.c.source_key == binding.source_key,
                    owner_table.c.identity_checksum == owner_identity,
                    owner_table.c.approved.is_(True),
                )
            ).mappings()
        )
        if len(owners) != 1:
            raise FD1RecoveryPublicationError("Exact approved owner decision is not unique")
        owner = owners[0]
        contexts[binding.source_key] = _SourceContext(
            source_key=binding.source_key,
            source_version_id=source_id,
            source_identity_checksum=binding.identity_checksum,
            owner_approval_id=owner["id"],
            owner_decided_at=_aware(owner["decided_at"], "owner.decided_at"),
            owner_expires_at=(
                _aware(owner["expires_at"], "owner.expires_at")
                if owner["expires_at"] is not None
                else None
            ),
        )

    archive_bindings = manifest.generation_lineage.source_archives
    identities = [item.download_identity_checksum for item in archive_bindings]
    _apply_publication_timeout(connection, statement_timeout_ms)
    download_rows = list(
        connection.execute(
            select(download_table).where(download_table.c.identity_checksum.in_(identities))
        ).mappings()
    )
    by_identity = {row["identity_checksum"]: row for row in download_rows}
    if len(by_identity) != len(archive_bindings):
        raise FD1RecoveryPublicationError("Frozen registered downloads are incomplete")
    downloads: list[_Download] = []
    for binding in archive_bindings:
        row = by_identity.get(binding.download_identity_checksum)
        if row is None:
            raise FD1RecoveryPublicationError("Frozen download identity is absent")
        context = contexts.get(binding.source_key)
        if context is None or row["source_version_id"] != context.source_version_id:
            raise FD1RecoveryPublicationError("Download/source release binding differs")
        _assert_fields(
            row,
            {
                "source_key": binding.source_key,
                "object_key": binding.object_key,
                "source_release": binding.source_release,
                "raw_logical_uri": binding.raw_logical_uri,
                "archive_sha256": binding.archive_sha256,
                "payload_sha256": binding.payload_sha256,
                "content_length": binding.content_length,
                "identity_checksum": binding.download_identity_checksum,
            },
            "registered download",
        )
        downloads.append(
            _Download(
                id=row["id"],
                source_version_id=row["source_version_id"],
                source_key=row["source_key"],
                object_key=row["object_key"],
                source_release=row["source_release"],
                upstream_revision=int(row["upstream_revision"]),
                downloaded_at=_aware(row["downloaded_at"], "download.downloaded_at"),
                content_length=int(row["content_length"]),
                payload_sha256=row["payload_sha256"],
                archive_sha256=row["archive_sha256"],
                raw_logical_uri=row["raw_logical_uri"],
                identity_checksum=row["identity_checksum"],
            )
        )
    return _RegistrySnapshot(
        contexts=MappingProxyType(contexts),
        downloads=tuple(downloads),
    )


def _extraction_index(
    artifacts: Sequence[ExtractionArtifact],
    registry: _RegistrySnapshot,
) -> Mapping[str, ExtractionArtifact]:
    result: dict[str, ExtractionArtifact] = {}
    for artifact in artifacts:
        try:
            _validate_extraction_identity(artifact)
        except Exception as exc:
            raise FD1RecoveryPublicationError(
                "Extraction-manifest metadata identity is invalid"
            ) from exc
        parents = [
            item
            for item in registry.downloads
            if item.source_key == artifact.source_key
            and item.object_key == artifact.object_key
            and item.upstream_revision == artifact.source_revision
            and item.payload_sha256 == artifact.archive_sha256
        ]
        if len(parents) != 1:
            raise FD1RecoveryPublicationError(
                "Extraction metadata lacks one frozen registered parent"
            )
        try:
            _validate_extraction_against_download(artifact, parents[0])
        except Exception as exc:
            # The helper uses attributes shared by the immutable snapshot.
            raise FD1RecoveryPublicationError(
                "Extraction metadata differs from registered download"
            ) from exc
        previous = result.setdefault(artifact.identity_checksum, artifact)
        if previous != artifact:
            raise FD1RecoveryPublicationConflict("Extraction identity collision")
    return MappingProxyType(result)


def _retrosheet_partition_contributors(
    registry: _RegistrySnapshot,
    extractions: Mapping[str, ExtractionArtifact],
) -> tuple[Mapping[str, Any], ...]:
    parents = [
        item
        for item in registry.downloads
        if item.source_key == RETROSHEET_SOURCE_KEY
        and item.object_key == RETROSHEET_CSV_OBJECT_KEY
    ]
    if len(parents) != 1:
        raise FD1RecoveryPublicationError("Retrosheet CSV archive is not unique")
    parent = parents[0]
    candidates = [
        item
        for item in extractions.values()
        if item.source_key == RETROSHEET_SOURCE_KEY
        and item.object_key == RETROSHEET_CSV_OBJECT_KEY
        and item.source_revision == parent.upstream_revision
        and item.archive_sha256 == parent.payload_sha256
    ]
    if len(candidates) != 1:
        raise FD1RecoveryPublicationError("Retrosheet extraction metadata is not unique")
    extraction = candidates[0]
    members: dict[str, Any] = {}
    for filename in sorted(RETROSHEET_REQUIRED_FILENAMES):
        matches = [
            member
            for member in extraction.members
            if PurePosixPath(member.relative_path).name == filename
        ]
        if len(matches) != 1:
            raise FD1RecoveryPublicationError(
                f"Retrosheet extraction does not contain one {filename}"
            )
        members[filename] = matches[0]
    normalized = []
    for filename, member in sorted(members.items()):
        normalized.append(
            {
                "logical_uri": (
                    PurePosixPath(extraction.extraction_logical_uri)
                    / PurePosixPath(member.relative_path)
                ).as_posix(),
                "file_sha256": member.sha256,
                "payload_checksum": member.sha256,
                "archive_checksum": parent.archive_sha256 or parent.payload_sha256,
                "retrieved_at": parent.downloaded_at.isoformat(),
                "source_record_id": (
                    f"open-data-download:{parent.identity_checksum};"
                    f"extraction:{extraction.identity_checksum};"
                    f"member:{member.relative_path}"
                ),
                "source_version_id": str(parent.source_version_id),
            }
        )
    unique = {canonical_sha256(item): item for item in normalized}
    return tuple(MappingProxyType(unique[key]) for key in sorted(unique))


def _player_prior_evidence(
    frame: pl.DataFrame,
    registry: _RegistrySnapshot,
) -> Mapping[str, Any]:
    rows = frame.to_dicts()
    if not rows:
        raise FD1RecoveryPublicationError("Player-prior Parquet is empty")
    contributors: dict[tuple[str, str], set[tuple[int, str]]] = defaultdict(set)
    populations: set[str] = set()
    for row in rows:
        populations.add(str(row.get("population", "")))
        uris = row.get("source_logical_uris")
        checksums = row.get("source_file_sha256s")
        numbers = row.get("source_row_numbers")
        row_checksums = row.get("source_row_sha256s")
        if not all(isinstance(item, list) for item in (uris, checksums, numbers, row_checksums)):
            raise FD1RecoveryPublicationError("Player-prior source evidence is malformed")
        if len(checksums) != 1 or not (len(uris) == len(numbers) == len(row_checksums)):
            raise FD1RecoveryPublicationError("Player-prior source evidence lengths differ")
        for uri, number, checksum in zip(uris, numbers, row_checksums, strict=True):
            contributors[(str(uri), str(checksums[0]))].add((int(number), str(checksum)))
    if len(populations) != 1 or "" in populations:
        raise FD1RecoveryPublicationError("Player-prior population is not unique")
    raw = [
        {
            "logical_uri": uri,
            "file_sha256": checksum,
            "record_count": len(source_rows),
        }
        for (uri, checksum), source_rows in sorted(contributors.items())
    ]
    normalized = _normalize_direct_contributors(raw, LAHMAN_SOURCE_KEY, registry)
    return MappingProxyType(
        {
            "contributors": normalized,
            "payload_checksum": _identity_payload_checksum(rows),
            "population": next(iter(populations)),
        }
    )


def _identity_contributors(
    source_key: str,
    counts: Mapping[str, int],
    *,
    registry: _RegistrySnapshot,
    extractions: Mapping[str, ExtractionArtifact],
) -> tuple[Mapping[str, Any], ...]:
    result: list[Mapping[str, Any]] = []
    source_downloads = [
        item for item in registry.downloads if item.source_key == source_key
    ]
    for uri, record_count in sorted(counts.items()):
        direct = [item for item in source_downloads if item.raw_logical_uri == uri]
        if len(direct) == 1:
            item = direct[0]
            result.append(
                MappingProxyType(
                    {
                        "logical_uri": item.raw_logical_uri,
                        "file_sha256": item.payload_sha256,
                        "payload_checksum": item.payload_sha256,
                        "archive_checksum": item.archive_sha256 or item.payload_sha256,
                        "record_count": record_count,
                        "retrieved_at": item.downloaded_at.isoformat(),
                        "source_record_id": f"open-data-download:{item.identity_checksum}",
                        "source_version_id": str(item.source_version_id),
                    }
                )
            )
            continue
        matches: list[tuple[ExtractionArtifact, Any, _Download]] = []
        for extraction in extractions.values():
            if extraction.source_key != source_key:
                continue
            for member in extraction.members:
                candidate = (
                    PurePosixPath(extraction.extraction_logical_uri)
                    / PurePosixPath(member.relative_path)
                ).as_posix()
                if candidate != uri:
                    continue
                parents = [
                    item
                    for item in source_downloads
                    if item.object_key == extraction.object_key
                    and item.upstream_revision == extraction.source_revision
                    and item.payload_sha256 == extraction.archive_sha256
                ]
                if len(parents) == 1:
                    matches.append((extraction, member, parents[0]))
        if len(matches) != 1:
            raise FD1RecoveryPublicationError(
                "Identity evidence URI is not one frozen registered file"
            )
        extraction, member, parent = matches[0]
        result.append(
            MappingProxyType(
                {
                    "logical_uri": uri,
                    "file_sha256": member.sha256,
                    "payload_checksum": member.sha256,
                    "archive_checksum": parent.payload_sha256,
                    "record_count": record_count,
                    "byte_count": member.size_bytes,
                    "retrieved_at": parent.downloaded_at.isoformat(),
                    "source_record_id": (
                        f"open-data-download:{parent.identity_checksum};"
                        f"extraction:{extraction.identity_checksum};"
                        f"member:{member.relative_path}"
                    ),
                    "source_version_id": str(parent.source_version_id),
                }
            )
        )
    return tuple(result)


def _normalize_direct_contributors(
    raw: Sequence[Mapping[str, Any]],
    source_key: str,
    registry: _RegistrySnapshot,
) -> tuple[Mapping[str, Any], ...]:
    source_downloads = [
        item for item in registry.downloads if item.source_key == source_key
    ]
    result: list[dict[str, Any]] = []
    for item in raw:
        uri = str(item["logical_uri"])
        checksum = str(item["file_sha256"])
        matches = [row for row in source_downloads if row.raw_logical_uri == uri]
        if len(matches) != 1 or matches[0].payload_sha256 != checksum:
            raise FD1RecoveryPublicationError(
                "Direct contributor does not match one frozen download"
            )
        download = matches[0]
        normalized = {
            "logical_uri": uri,
            "file_sha256": checksum,
            "payload_checksum": checksum,
            "archive_checksum": download.archive_sha256 or download.payload_sha256,
            "retrieved_at": download.downloaded_at.isoformat(),
            "source_record_id": f"open-data-download:{download.identity_checksum}",
            "source_version_id": str(download.source_version_id),
        }
        if item.get("record_count") is not None:
            normalized["record_count"] = int(item["record_count"])
        result.append(normalized)
    unique = {canonical_sha256(item): item for item in result}
    return tuple(MappingProxyType(unique[key]) for key in sorted(unique))


def _retrosheet_ledger_units(
    ledger: Mapping[str, Any],
) -> Mapping[tuple[int, int, str], _RetrosheetLedgerUnit]:
    materialization = _mapping(ledger.get("retrosheet_materialization"), "materialization")
    monthly = materialization.get("monthly")
    if not isinstance(monthly, list):
        raise FD1RecoveryPublicationError("Dry-run monthly ledger is invalid")
    units: dict[tuple[int, int, str], _RetrosheetLedgerUnit] = {}
    for raw in monthly:
        item = _mapping(raw, "monthly ledger row")
        season = _positive_int(item.get("season"), "ledger season")
        month = _positive_int(item.get("month"), "ledger month")
        checksums = _mapping(item.get("partition_checksums"), "partition checksums")
        quality = _mapping(item.get("partition_quality_reports"), "quality reports")
        family_rows = _mapping(item.get("family_rows"), "family rows")
        source_bundle = str(item.get("selected_bundle_checksum", ""))
        _sha256(source_bundle, "source bundle checksum")
        if set(checksums) != set(quality):
            raise FD1RecoveryPublicationError("Ledger checksum/quality families differ")
        for family, checksum in checksums.items():
            key = (season, month, str(family))
            if key in units:
                raise FD1RecoveryPublicationConflict("Ledger partition key is duplicated")
            _sha256(str(checksum), "partition checksum")
            row_count = _positive_int(family_rows.get(family), "ledger family rows")
            units[key] = _RetrosheetLedgerUnit(
                season=season,
                month=month,
                family=str(family),
                partition_checksum=str(checksum),
                source_bundle_checksum=source_bundle,
                quality_report=MappingProxyType(dict(_mapping(quality[family], "quality report"))),
                row_count=row_count,
            )
    return MappingProxyType(units)


def _validate_trusted_ledger(
    manifest: RecoveryManifest,
    ledger: Mapping[str, Any],
) -> None:
    binding = manifest.generation_lineage.trusted_dry_run_ledger
    if binding.schema_version != TRUSTED_LEDGER_SCHEMA_VERSION:
        raise FD1RecoveryPublicationError("Manifest does not bind the trusted v5 ledger")
    if ledger.get("schema_version") != TRUSTED_LEDGER_SCHEMA_VERSION:
        raise FD1RecoveryPublicationError("Dry-run ledger schema is not trusted v5")
    if ledger.get("mode") != "dry-run" or ledger.get("success") is not True:
        raise FD1RecoveryPublicationError("Trusted ledger is not a successful dry run")
    publication = _mapping(ledger.get("publication"), "publication summary")
    if publication.get("code_revision") != manifest.generation_lineage.code_revision:
        raise FD1RecoveryPublicationError("Dry-run generation code revision differs")
    if publication.get("batches_seen") != FULL_PARTITION_COUNT:
        raise FD1RecoveryPublicationError("Dry-run partition batch count differs")
    if publication.get("parquet_rows") != manifest.aggregate.row_count:
        raise FD1RecoveryPublicationError("Dry-run aggregate row count differs")
    if publication.get("identity_mappings_seen") != manifest.identity_spool.row_count:
        raise FD1RecoveryPublicationError("Dry-run identity row count differs")
    prohibited = _mapping(ledger.get("prohibited_activity"), "prohibited activity")
    if prohibited.get("network_requests") != 0 or prohibited.get(
        "provider_transport_constructed"
    ) is not False:
        raise FD1RecoveryPublicationError("Trusted ledger reports provider activity")
    materialization = _mapping(ledger.get("retrosheet_materialization"), "materialization")
    if materialization.get("single_pass_source_scan") is not True:
        raise FD1RecoveryPublicationError("Dry-run ledger lacks single-pass evidence")
    sources = _mapping(ledger.get("source_versions"), "source versions")
    manifest_sources = {
        item.source_key: item for item in manifest.generation_lineage.source_releases
    }
    if set(manifest_sources) != set(FD1_SOURCE_DEFINITIONS):
        raise FD1RecoveryPublicationError(
            "Recovery manifest does not bind every FD1 publication source"
        )
    ledger_source_keys = set(sources)
    if set(manifest_sources) - ledger_source_keys != {_RETROSHEET_EVENT_SOURCE_KEY}:
        raise FD1RecoveryPublicationError(
            "Only governed Retrosheet event archives may be outside the dry-run ledger"
        )
    for source_key, raw in sources.items():
        item = _mapping(raw, "ledger source version")
        binding_row = manifest_sources.get(source_key)
        if binding_row is None:
            raise FD1RecoveryPublicationError("Ledger source is absent from manifest")
        expected = {
            "id": binding_row.source_version_id,
            "dataset_version": binding_row.dataset_version,
            "identity_checksum": binding_row.identity_checksum,
            "source_mode": binding_row.source_mode,
        }
        if any(item.get(key) != value for key, value in expected.items()):
            raise FD1RecoveryPublicationError("Ledger source-version binding differs")
    downloads = ledger.get("registered_downloads_used")
    if not isinstance(downloads, list):
        raise FD1RecoveryPublicationError("Ledger registered-download evidence is invalid")
    ledger_downloads = {
        (
            str(item.get("source_key", "")),
            str(item.get("object_key", "")),
            str(item.get("download_identity_checksum", "")),
        ): item
        for item in downloads
        if isinstance(item, Mapping)
    }
    if len(ledger_downloads) != len(downloads):
        raise FD1RecoveryPublicationError(
            "Ledger registered-download evidence is duplicated or malformed"
        )
    manifest_downloads = {
        (item.source_key, item.object_key, item.download_identity_checksum): item
        for item in manifest.generation_lineage.source_archives
        if item.source_key in ledger_source_keys
    }
    if set(ledger_downloads) != set(manifest_downloads):
        raise FD1RecoveryPublicationError(
            "Ledger and recovery manifest registered downloads differ"
        )
    for key, binding_row in manifest_downloads.items():
        item = ledger_downloads[key]
        expected = {
            "archive_sha256": binding_row.archive_sha256,
            "content_length": binding_row.content_length,
            "payload_sha256": binding_row.payload_sha256,
            "raw_logical_uri": binding_row.raw_logical_uri,
            "source_release": binding_row.source_release,
        }
        if any(item.get(name) != value for name, value in expected.items()):
            raise FD1RecoveryPublicationError(
                "Ledger registered-download payload differs from manifest"
            )
    outside_ledger = [
        item
        for item in manifest.generation_lineage.source_archives
        if item.source_key not in ledger_source_keys
    ]
    if not outside_ledger or any(
        item.source_key != _RETROSHEET_EVENT_SOURCE_KEY for item in outside_ledger
    ):
        raise FD1RecoveryPublicationError(
            "Manifest lacks exact governed Retrosheet event-archive bindings"
        )


def _validate_extractions_against_ledger(
    ledger: Mapping[str, Any],
    extractions: Mapping[str, ExtractionArtifact],
) -> None:
    values = ledger.get("extractions_used")
    if not isinstance(values, list):
        raise FD1RecoveryPublicationError("Ledger extraction evidence is invalid")
    ledger_rows = {
        str(item.get("identity_checksum", "")): item
        for item in values
        if isinstance(item, Mapping)
    }
    if set(ledger_rows) != set(extractions):
        raise FD1RecoveryPublicationError(
            "Explicit extraction manifests differ from trusted ledger"
        )
    for identity, extraction in extractions.items():
        item = ledger_rows[identity]
        expected = {
            "archive_sha256": extraction.archive_sha256,
            "expanded_bytes": extraction.expanded_bytes,
            "manifest_logical_uri": extraction.manifest_logical_uri,
            "member_count": extraction.member_count,
            "object_key": extraction.object_key,
            "source_key": extraction.source_key,
            "source_revision": extraction.source_revision,
        }
        if any(item.get(name) != value for name, value in expected.items()):
            raise FD1RecoveryPublicationError(
                "Extraction manifest metadata differs from trusted ledger"
            )
        if item.get("member_bytes_verified") is not True:
            raise FD1RecoveryPublicationError(
                "Trusted dry-run ledger lacks prior extraction-member verification"
            )


def _validate_ledger_partition_population(
    manifest: RecoveryManifest,
    units: Mapping[tuple[int, int, str], _RetrosheetLedgerUnit],
) -> None:
    manifest_keys = {
        (item.season, item.month, item.family)
        for item in manifest.files
        if item.family != "player_prior"
    }
    if len(manifest_keys) != RETROSHEET_PARTITION_COUNT:
        raise FD1RecoveryPublicationError("Recovery manifest lacks 324 Retrosheet files")
    if set(units) != manifest_keys:
        raise FD1RecoveryPublicationError(
            "Dry-run ledger and immutable Retrosheet partitions do not match exactly"
        )
    priors = sum(item.family == "player_prior" for item in manifest.files)
    if priors != PLAYER_PRIOR_PARTITION_COUNT:
        raise FD1RecoveryPublicationError("Recovery manifest lacks five player-prior files")


def _select_manifest_entries(
    manifest: RecoveryManifest,
    *,
    scope: str,
    sample_logical_uris: Sequence[str],
) -> tuple[RecoveryFileEntry, ...]:
    if not sample_logical_uris:
        return manifest.files
    if scope != "REHEARSAL":
        raise FD1RecoveryPublicationError("Only REHEARSAL may select sample files")
    requested = tuple(sample_logical_uris)
    if len(requested) != len(set(requested)):
        raise FD1RecoveryPublicationError("Rehearsal sample contains duplicate URIs")
    by_uri = {item.logical_uri: item for item in manifest.files}
    try:
        selected = tuple(by_uri[uri] for uri in requested)
    except KeyError as exc:
        raise FD1RecoveryPublicationError(
            "Rehearsal sample references a file outside the manifest"
        ) from exc
    if not selected:
        raise FD1RecoveryPublicationError("Rehearsal sample is empty")
    return tuple(sorted(selected, key=lambda item: item.logical_uri))


def _generation_execution_at(manifest: RecoveryManifest) -> datetime:
    raw = manifest.generation_lineage.interrupted_execution.started_at_utc
    if raw is None:
        raise FD1RecoveryPublicationError(
            "Interrupted generation timestamp is required to preserve row lineage"
        )
    return _aware(datetime.fromisoformat(raw), "interrupted generation start")


def _resolve_manifest_file(root: Path, entry: RecoveryFileEntry) -> Path:
    path = root.joinpath(*PurePosixPath(entry.logical_uri).parts).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise FD1RecoveryPublicationError("Manifested Parquet escapes store root") from exc
    if not path.is_file() or _linklike(path):
        raise FD1RecoveryPublicationError("Manifested Parquet is not a safe file")
    observed = path.stat()
    if observed.st_size != entry.size_bytes or observed.st_mtime_ns != entry.mtime_ns:
        raise FD1RecoveryPublicationError("Manifested Parquet stat tuple changed")
    return path


def _derive_project_root(
    ledger_path: Path,
    binding: TrustedDryRunLedger,
) -> Path:
    candidate = ledger_path
    for _ in PurePosixPath(binding.logical_uri).parts:
        candidate = candidate.parent
    expected = candidate.joinpath(*PurePosixPath(binding.logical_uri).parts).resolve()
    if expected != ledger_path:
        raise FD1RecoveryPublicationError(
            "Cannot derive project root from trusted ledger binding"
        )
    return candidate


def _load_explicit_extractions(
    project_root: Path,
    ledger: Mapping[str, Any],
) -> tuple[ExtractionArtifact, ...]:
    rows = ledger.get("extractions_used")
    if not isinstance(rows, list) or not rows:
        raise FD1RecoveryPublicationError("Trusted ledger has no extraction manifests")
    raw_root = project_root / "data" / "raw"
    artifacts: list[ExtractionArtifact] = []
    for row in sorted(
        rows,
        key=lambda item: str(item.get("manifest_logical_uri", ""))
        if isinstance(item, Mapping)
        else "",
    ):
        item = _mapping(row, "ledger extraction")
        logical_uri = str(item.get("manifest_logical_uri", ""))
        path = raw_root.joinpath(*PurePosixPath(logical_uri).parts)
        try:
            artifact = load_extraction_artifact_manifest(
                path,
                raw_root=raw_root,
                verify_member_bytes=False,
            )
        except Exception as exc:
            raise FD1RecoveryPublicationError(
                "Explicit extraction manifest could not be loaded"
            ) from exc
        if artifact.identity_checksum != item.get("identity_checksum"):
            raise FD1RecoveryPublicationError(
                "Explicit extraction identity differs from trusted ledger"
            )
        artifacts.append(artifact)
    return tuple(artifacts)


def _representative_sample(manifest: RecoveryManifest) -> tuple[str, ...]:
    by_family: dict[str, list[RecoveryFileEntry]] = defaultdict(list)
    for entry in manifest.files:
        by_family[entry.family].append(entry)
    if len(by_family) != 10:
        raise FD1RecoveryPublicationError(
            "Representative rehearsal requires all ten FD1 families"
        )
    selected = []
    for family in sorted(by_family):
        entries = sorted(
            by_family[family], key=lambda item: (item.season, item.month, item.logical_uri)
        )
        selected.append(entries[len(entries) // 2].logical_uri)
    return tuple(selected)


def _stage_chunk(
    connection: Connection,
    *,
    run_id: UUID,
    scope: str,
    plan: PublicationPlan,
    targets: Sequence[PublicationTarget],
    ordinal_offset: int,
    validated_at: datetime,
    statement_timeout_ms: _StatementTimeout | None = None,
) -> tuple[int, int, int, int, int]:
    stage = FD1RecoveryPublicationStage.__table__
    target_ids = [UUID(item.target_id) for item in targets]
    kinds = [item.object_kind for item in targets]
    existing = list(
        connection.execute(
            select(stage).where(
                stage.c.run_id == run_id,
                stage.c.scope == scope,
                stage.c.target_id.in_(target_ids),
                stage.c.object_kind.in_(kinds),
            )
        ).mappings()
    )
    by_key = {(row["object_kind"], str(row["target_id"])): row for row in existing}
    classifications = _classify_targets(
        connection,
        targets,
        statement_timeout_ms=statement_timeout_ms,
    )
    inserted = reused = proposed_inserts = proposed_reuses = conflicts = 0
    pending: list[dict[str, Any]] = []
    for index, (target, classification) in enumerate(
        zip(targets, classifications, strict=True), start=ordinal_offset + 1
    ):
        action, matched_id, reason = classification
        row = by_key.get((target.object_kind, target.target_id))
        if row is not None:
            _assert_stage_row(row, target, index, plan)
            reused += 1
            if row["promoted_at"] is not None:
                if action != "REUSE":
                    raise FD1RecoveryPublicationConflict(
                        "Promoted stage target no longer has exact canonical evidence"
                    )
                observed_action = row["action"]
            else:
                observed_action = action
                if (
                    row["action"] != action
                    or _json_value(row["matched_target_id"]) != matched_id
                    or row["reason_code"] != reason
                ):
                    _apply_publication_timeout(connection, statement_timeout_ms)
                    connection.execute(
                        update(stage)
                        .where(stage.c.id == row["id"])
                        .values(
                            action=action,
                            matched_target_id=UUID(matched_id) if matched_id else None,
                            reason_code=reason,
                            validated_at=validated_at,
                        )
                    )
            if observed_action == "INSERT":
                proposed_inserts += 1
            elif observed_action == "REUSE":
                proposed_reuses += 1
            elif observed_action == "CONFLICT":
                conflicts += 1
            continue
        if action == "INSERT":
            proposed_inserts += 1
        elif action == "REUSE":
            proposed_reuses += 1
        else:
            conflicts += 1
        stage_id = uuid5(
            _RECOVERY_STAGE_NAMESPACE,
            f"{run_id}:{scope}:{target.object_kind}:{target.target_id}",
        )
        pending.append(
            {
                "id": stage_id,
                "run_id": run_id,
                "scope": scope,
                "ordinal": index,
                "object_kind": target.object_kind,
                "promotion_order": target.promotion_order,
                "target_id": UUID(target.target_id),
                "natural_key_checksum": target.natural_key_checksum,
                "semantic_identity_checksum": target.semantic_identity_checksum,
                "target_payload": dict(target.target_payload),
                "payload_checksum": target.payload_checksum,
                "action": action,
                "matched_target_id": UUID(matched_id) if matched_id else None,
                "reason_code": reason,
                "validated_at": validated_at,
            }
        )
    if pending:
        try:
            _apply_publication_timeout(connection, statement_timeout_ms)
            connection.execute(stage.insert(), pending)
        except IntegrityError as exc:
            raise FD1RecoveryPublicationConflict(
                "Publication stage has a conflicting prior plan"
            ) from exc
        inserted += len(pending)
    return inserted, reused, proposed_inserts, proposed_reuses, conflicts


def _verify_staged_population(
    bind: Engine | Connection,
    run_id: UUID,
    scope: str,
    plan: PublicationPlan,
    *,
    statement_timeout_ms: _StatementTimeout | None = None,
) -> None:
    def verify(connection: Connection) -> None:
        table = FD1RecoveryPublicationStage.__table__
        rows = list(
            connection.execute(
                select(table)
                .where(table.c.run_id == run_id, table.c.scope == scope)
                .order_by(table.c.ordinal)
            ).mappings()
        )
        if len(rows) != len(plan.targets):
            raise FD1RecoveryPublicationConflict(
                "Staged publication population differs from current plan"
            )
        for ordinal, (row, target) in enumerate(
            zip(rows, plan.targets, strict=True), start=1
        ):
            _assert_stage_row(row, target, ordinal, plan)

    if isinstance(bind, Engine):
        with bind.connect() as connection:
            _apply_publication_timeout(connection, statement_timeout_ms)
            verify(connection)
    else:
        _apply_publication_timeout(bind, statement_timeout_ms)
        verify(bind)


def _assert_stage_row(
    row: Mapping[str, Any],
    target: PublicationTarget,
    ordinal: int,
    plan: PublicationPlan,
) -> None:
    expected = {
        "ordinal": ordinal,
        "object_kind": target.object_kind,
        "promotion_order": target.promotion_order,
        "target_id": target.target_id,
        "natural_key_checksum": target.natural_key_checksum,
        "semantic_identity_checksum": target.semantic_identity_checksum,
        "target_payload": dict(target.target_payload),
        "payload_checksum": target.payload_checksum,
    }
    actual = {key: _json_value(row[key]) for key in expected}
    if actual != _plain_json(expected):
        raise FD1RecoveryPublicationConflict(
            f"Staged publication target differs from plan {plan.plan_checksum}"
        )


def _classify_targets(
    connection: Connection,
    targets: Sequence[PublicationTarget],
    *,
    statement_timeout_ms: _StatementTimeout | None = None,
) -> tuple[tuple[str, str | None, str | None], ...]:
    result: list[tuple[str, str | None, str | None]] = []
    for target in targets:
        _apply_publication_timeout(connection, statement_timeout_ms)
        table = _target_table(target.object_kind)
        conditions = [table.c.id == UUID(target.target_id)]
        identity_column = _identity_column(target.object_kind)
        if identity_column is not None:
            conditions.append(identity_column == target.semantic_identity_checksum)
        natural = _natural_condition(table, target)
        if natural is not None:
            conditions.append(natural)
        rows = list(connection.execute(select(table).where(or_(*conditions))).mappings())
        by_id = [row for row in rows if str(row["id"]) == target.target_id]
        if len(by_id) > 1:
            result.append(("CONFLICT", None, "TARGET_ID_NOT_UNIQUE"))
            continue
        if by_id:
            if _payload_matches(by_id[0], target.target_payload):
                result.append(("REUSE", target.target_id, None))
            else:
                result.append(("CONFLICT", target.target_id, "TARGET_ID_PAYLOAD_DIFFERS"))
            continue
        semantic = [
            row
            for row in rows
            if identity_column is not None
            and row[identity_column.name] == target.semantic_identity_checksum
        ]
        if semantic:
            if len(semantic) == 1 and _payload_matches(
                semantic[0], target.target_payload, ignore_id=True
            ):
                result.append(("REUSE", str(semantic[0]["id"]), None))
            else:
                result.append(("CONFLICT", str(semantic[0]["id"]), "IDENTITY_COLLISION"))
            continue
        if rows:
            result.append(("CONFLICT", str(rows[0]["id"]), "NATURAL_KEY_COLLISION"))
            continue
        result.append(("INSERT", None, None))
    return tuple(result)


def _target_from_stage(row: Mapping[str, Any]) -> PublicationTarget:
    return PublicationTarget(
        object_kind=row["object_kind"],
        target_id=str(row["target_id"]),
        natural_key_checksum=row["natural_key_checksum"],
        semantic_identity_checksum=row["semantic_identity_checksum"],
        target_payload=MappingProxyType(dict(row["target_payload"])),
        payload_checksum=row["payload_checksum"],
    )


def _target_table(object_kind: str) -> Table:
    tables = {
        "DERIVED_USE_DECISION": ExistingEvidenceDerivedUseDecision.__table__,
        "INGESTION_RUN": IngestionRun.__table__,
        "CHECKPOINT": HistoryBackfillCheckpoint.__table__,
        "PARTITION": HistoricalEventPartition.__table__,
    }
    try:
        return tables[object_kind]
    except KeyError as exc:
        raise FD1RecoveryPublicationError("Unknown publication object kind") from exc


def _identity_column(object_kind: str):
    if object_kind == "INGESTION_RUN":
        return None
    return _target_table(object_kind).c.identity_checksum


def _natural_condition(table: Table, target: PublicationTarget):
    payload = target.target_payload
    if target.object_kind == "DERIVED_USE_DECISION":
        return table.c.identity_checksum == target.semantic_identity_checksum
    if target.object_kind == "CHECKPOINT":
        return and_(
            table.c.ingestion_run_id == UUID(str(payload["ingestion_run_id"])),
            table.c.stream_key == payload["stream_key"],
            table.c.sequence == payload["sequence"],
        )
    if target.object_kind == "PARTITION":
        return or_(
            table.c.logical_uri == payload["logical_uri"],
            table.c.checkpoint_id == UUID(str(payload["checkpoint_id"])),
        )
    return None


def _payload_matches(
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    ignore_id: bool = False,
) -> bool:
    expected = dict(payload)
    if ignore_id:
        expected.pop("id", None)
    actual = {
        key: _json_value(row[key])
        for key in expected
    }
    return actual == _plain_json(expected)


def _coerce_payload(table: Table, payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in payload.items():
        column = table.c[key]
        if value is None:
            result[key] = None
        elif isinstance(column.type, Uuid):
            result[key] = UUID(str(value))
        elif isinstance(column.type, DateTime):
            result[key] = datetime.fromisoformat(str(value))
        elif isinstance(column.type, Date):
            result[key] = date.fromisoformat(str(value))
        elif table is IngestionRun.__table__ and key == "status":
            result[key] = RunStatus[str(value)]
        else:
            result[key] = value
    return result


def _deduplicate_targets(
    targets: Sequence[PublicationTarget],
) -> tuple[PublicationTarget, ...]:
    by_identity: dict[tuple[str, str], PublicationTarget] = {}
    by_target: dict[tuple[str, str], PublicationTarget] = {}
    for target in targets:
        identity_key = (target.object_kind, target.semantic_identity_checksum)
        target_key = (target.object_kind, target.target_id)
        prior_identity = by_identity.setdefault(identity_key, target)
        prior_target = by_target.setdefault(target_key, target)
        if prior_identity != target or prior_target != target:
            raise FD1RecoveryPublicationConflict("Publication target collision")
    return tuple(by_identity.values())


def _manifest_checksum(contributors: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            [dict(item) for item in contributors],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _plain_json(value: object) -> Any:
    return json.loads(
        json.dumps(
            _json_value(value),
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )
    )


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return _aware(value, "JSON timestamp").isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.name
    raise TypeError(f"Unsupported publication JSON value {type(value).__name__}")


def _json_value(value: object) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _assert_fields(
    row: Mapping[str, Any], expected: Mapping[str, Any], label: str
) -> None:
    for key, value in expected.items():
        if _json_value(row[key]) != _json_value(value):
            raise FD1RecoveryPublicationConflict(f"Existing {label} differs at {key}")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FD1RecoveryPublicationError(f"{label} must be an object")
    return value


def _scope(value: str) -> Literal["FULL", "REHEARSAL"]:
    if value not in {"FULL", "REHEARSAL"}:
        raise FD1RecoveryPublicationError("Recovery publication scope is invalid")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FD1RecoveryPublicationError(f"{label} must be a positive integer")
    return value


def _publication_timeout(value: _StatementTimeout | None) -> int | None:
    if value is None:
        return None
    selected = value() if callable(value) else value
    if (
        isinstance(selected, bool)
        or not isinstance(selected, int)
        or selected < 1
        or selected > _DEFAULT_STAGE_TIMEOUT_MS
    ):
        raise ValueError(
            "publication statement_timeout_ms must be between 1 and 1800000"
        )
    return selected


def _apply_publication_timeout(
    connection: Connection,
    value: _StatementTimeout | None,
) -> None:
    selected = _publication_timeout(value)
    if selected is None:
        return
    connection.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{selected}ms"},
    )


def _sha256(value: object, label: str) -> str:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise FD1RecoveryPublicationError(f"{label} must be a lowercase SHA-256")
    return text


def _uuid(value: object, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise FD1RecoveryPublicationError(f"{label} must be a UUID") from exc


def _aware(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise FD1RecoveryPublicationError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _linklike(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(
            getattr(path.lstat(), "st_file_attributes", 0) & _SHA256_REPARSE_POINT
        )
    except OSError as exc:
        raise FD1RecoveryPublicationError("Unable to inspect evidence path") from exc


__all__ = [
    "DEFAULT_STAGE_BATCH_SIZE",
    "FD1_RECOVERY_PUBLICATION_SCHEMA_VERSION",
    "FD1RecoveryPublicationConflict",
    "FD1RecoveryPublicationError",
    "PublicationPlan",
    "PublicationProgress",
    "PublicationPromotionSummary",
    "PublicationStageSummary",
    "PublicationTarget",
    "RecoveryPublicationPromotionResult",
    "RecoveryPublicationStageResult",
    "build_fd1_recovery_publication_plan",
    "load_trusted_v5_dry_run_ledger",
    "promote_publication_plan",
    "promote_recovery_publication",
    "stage_recovery_publication",
    "stage_publication_plan",
]
