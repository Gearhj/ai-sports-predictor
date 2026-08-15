"""PostgreSQL-native identity staging and reconciliation for FD1 recovery.

The interrupted FD1 publisher spent its final phase creating hundreds of
thousands of ORM objects in small query/flush chunks.  Recovery deliberately
uses one compact CSV spool, PostgreSQL ``COPY``, and set-based reconciliation.
The final mapping table is untouched until the caller opens the short atomic
promotion transaction.

This module never imports a provider, scans source data, or writes Parquet.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import Engine, text

from app.gen2.fd1_recovery_identity import (
    FD1RecoveryIdentityError,
    load_identity_spool_binding,
)
from app.gen2.open_data_registry import canonical_sha256


IDENTITY_STAGE_SCOPE_VALUES = ("FULL", "REHEARSAL")
IDENTITY_SPOOL_COLUMNS = (
    "staging_ordinal",
    "batch_number",
    "batch_checksum",
    "source_key",
    "source_version_id",
    "source_release_identity_checksum",
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
    "mapping_basis",
    "identity_checksum",
    "record_checksum",
    "target_mapping_id",
)

_IDENTITY_NAMESPACE = UUID("418ebff6-c8f3-4a91-970d-7ec71c39c944")
_SHA256_LENGTH = 64
_QUARANTINE_BATCH_SIZE = 10_000
_DEFAULT_COPY_TIMEOUT_MS = 5 * 60 * 1_000
_DEFAULT_RECONCILIATION_TIMEOUT_MS = 20 * 60 * 1_000
_DEFAULT_PROMOTION_TIMEOUT_MS = 30 * 60 * 1_000
_StatementTimeout = int | Callable[[], int]


class _TimeoutRefreshingConnection:
    """Apply a freshly resolved transaction-local timeout before each statement."""

    def __init__(
        self,
        connection: Any,
        timeout_resolver: Callable[[], int],
    ) -> None:
        self._connection = connection
        self._timeout_resolver = timeout_resolver

    def refresh_timeout(self) -> int:
        timeout = self._timeout_resolver()
        self._connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": f"{timeout}ms"},
        )
        return timeout

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.refresh_timeout()
        return self._connection.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


class FD1RecoverySqlError(RuntimeError):
    """A COPY, reconciliation, or promotion invariant failed closed."""


@dataclass(frozen=True, slots=True)
class IdentityCopyMetrics:
    rows_staged: int
    chunks_committed: int
    elapsed_seconds: float
    rows_per_second: float
    resumed_after_ordinal: int
    final_byte_offset: int
    staging_table_bytes: int
    analyze_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class IdentityReconciliationMetrics:
    total_rows: int
    planned_insert: int
    exact_reuse: int
    unresolved: int
    source_declared_conflicts: int
    blocking_conflicts: int
    quarantine_rows: int
    reconciliation_seconds: float
    conflict_query_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class IdentityPromotionMetrics:
    inserted: int
    reused: int
    unresolved: int
    source_declared_conflicts: int
    blocking_conflicts: int
    promotion_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class IdentitySpoolRow:
    """One validated physical row from the compact recovery spool."""

    values: tuple[Any, ...]
    staging_ordinal: int
    byte_offset_after: int


def iter_identity_spool_rows(
    path: Path,
    *,
    start_ordinal: int = 1,
    start_byte_offset: int | None = None,
) -> Iterator[IdentitySpoolRow]:
    """Yield exact one-line CSV rows and their resumable byte offsets.

    The spool contract forbids embedded CR/LF characters.  That keeps every
    mapping on one physical line and makes a committed byte-offset checkpoint
    safe to resume after process interruption.
    """

    if start_ordinal < 1:
        raise ValueError("start_ordinal must be positive")
    selected = Path(path)
    with selected.open("rb") as handle:
        header_line = handle.readline()
        if not header_line:
            raise FD1RecoverySqlError("Identity spool is empty")
        header = _csv_line(header_line, line_number=1)
        if tuple(header) != IDENTITY_SPOOL_COLUMNS:
            raise FD1RecoverySqlError("Identity spool header is not exact")
        data_offset = handle.tell()
        if start_byte_offset is None:
            expected_ordinal = 1
        else:
            if (
                isinstance(start_byte_offset, bool)
                or not isinstance(start_byte_offset, int)
                or start_byte_offset < data_offset
                or start_byte_offset > selected.stat().st_size
            ):
                raise FD1RecoverySqlError("Identity spool byte offset is invalid")
            if start_byte_offset > data_offset:
                handle.seek(start_byte_offset - 1)
                if handle.read(1) != b"\n":
                    raise FD1RecoverySqlError(
                        "Identity spool byte offset is not a record boundary"
                    )
            handle.seek(start_byte_offset)
            expected_ordinal = start_ordinal
        line_number = 1
        while raw := handle.readline():
            line_number += 1
            fields = _csv_line(raw, line_number=line_number)
            if len(fields) != len(IDENTITY_SPOOL_COLUMNS):
                raise FD1RecoverySqlError(
                    f"Identity spool column count differs at line {line_number}"
                )
            record = dict(zip(IDENTITY_SPOOL_COLUMNS, fields, strict=True))
            ordinal = _positive_int(record["staging_ordinal"], "staging_ordinal")
            if ordinal != expected_ordinal:
                raise FD1RecoverySqlError(
                    "Identity spool ordinals must be contiguous and start at one"
                )
            expected_ordinal += 1
            if start_byte_offset is None and ordinal < start_ordinal:
                continue
            values = _coerce_spool_record(record)
            yield IdentitySpoolRow(
                values=values,
                staging_ordinal=ordinal,
                byte_offset_after=handle.tell(),
            )


def copy_identity_spool(
    engine: Engine,
    *,
    run_id: UUID,
    scope: str,
    spool_path: Path,
    expected_sha256: str,
    expected_rows: int,
    batch_size: int,
    checkpoint_id: UUID,
    expected_size_bytes: int | None = None,
    expected_mtime_ns: int | None = None,
    reuse_verified_hash: bool = False,
    row_limit: int | None = None,
    progress: Callable[[int, int], None] | None = None,
    statement_timeout_ms: _StatementTimeout = _DEFAULT_COPY_TIMEOUT_MS,
) -> IdentityCopyMetrics:
    """COPY an identity spool into durable run-scoped staging transactions.

    Each chunk and its high-water checkpoint commit together.  A crash cannot
    leave a durable chunk without a matching resume token.  Re-entry verifies
    the contiguous high-water mark and starts at the next physical CSV row.
    """

    _scope(scope)
    _sha256(expected_sha256, "expected_sha256")
    if expected_rows < 1:
        raise ValueError("expected_rows must be positive")
    if row_limit is not None and (
        isinstance(row_limit, bool)
        or not isinstance(row_limit, int)
        or row_limit < 1
        or row_limit != expected_rows
    ):
        raise ValueError("row_limit must equal the bounded expected_rows population")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if engine.dialect.name != "postgresql":
        raise FD1RecoverySqlError("Identity COPY requires PostgreSQL")
    selected = Path(spool_path).resolve(strict=True)
    if not selected.is_file():
        raise FD1RecoverySqlError("Identity spool is not a regular file")
    stat = selected.stat()
    if expected_size_bytes is not None and stat.st_size != expected_size_bytes:
        raise FD1RecoverySqlError("Identity spool byte count changed")
    if expected_mtime_ns is not None and stat.st_mtime_ns != expected_mtime_ns:
        raise FD1RecoverySqlError("Identity spool modification time changed")
    if reuse_verified_hash:
        if expected_size_bytes is None or expected_mtime_ns is None:
            raise ValueError(
                "Cached identity hash reuse requires exact size and modification time"
            )
    else:
        try:
            binding = load_identity_spool_binding(
                selected,
                expected_sha256=expected_sha256,
                expected_byte_count=expected_size_bytes,
                expected_row_count=(None if row_limit is not None else expected_rows),
            )
        except FD1RecoveryIdentityError as exc:
            raise FD1RecoverySqlError(
                f"Identity spool semantic validation failed: {exc}"
            ) from exc
        if row_limit is not None and binding.row_count < expected_rows:
            raise FD1RecoverySqlError(
                "Identity rehearsal population exceeds the frozen spool"
            )

    with engine.connect() as raw_connection:
        connection = _TimeoutRefreshingConnection(
            raw_connection,
            lambda: _copy_timeout(statement_timeout_ms),
        )
        state = connection.execute(
            text(
                "SELECT COALESCE(MAX(staging_ordinal), 0) AS high_water, "
                "COUNT(*) AS row_count FROM fd1_recovery_identity_stage "
                "WHERE run_id = :run_id AND scope = :scope"
            ),
            {"run_id": run_id, "scope": scope},
        ).mappings().one()
        checkpoint = connection.execute(
            text(
                "SELECT resume_token FROM fd1_recovery_stage_checkpoints "
                "WHERE id = :checkpoint_id AND run_id = :run_id "
                "AND scope = :scope AND stage_name = 'IDENTITY_STAGED'"
            ),
            {"checkpoint_id": checkpoint_id, "run_id": run_id, "scope": scope},
        ).mappings().one_or_none()
    if checkpoint is None:
        raise FD1RecoverySqlError("IDENTITY_STAGED checkpoint is missing")
    high_water = int(state["high_water"])
    if high_water != int(state["row_count"]):
        raise FD1RecoverySqlError("Identity stage high-water mark is not contiguous")
    if high_water > expected_rows:
        raise FD1RecoverySqlError("Identity stage exceeds the frozen spool population")
    token = checkpoint["resume_token"]
    resume_offset: int | None = None
    if high_water:
        if (
            not isinstance(token, Mapping)
            or int(token.get("high_water_ordinal", -1)) != high_water
        ):
            raise FD1RecoverySqlError("Identity stage and resume checkpoint disagree")
        try:
            resume_offset = int(token["byte_offset"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FD1RecoverySqlError(
                "Identity stage checkpoint has no valid byte offset"
            ) from exc
        if resume_offset <= 0:
            raise FD1RecoverySqlError(
                "Identity stage checkpoint has no valid byte offset"
            )

    started = monotonic()
    chunk_count = 0
    final_offset = int(token.get("byte_offset", 0)) if isinstance(token, Mapping) else 0
    chunk: list[IdentitySpoolRow] = []
    for row in iter_identity_spool_rows(
        selected,
        start_ordinal=high_water + 1,
        start_byte_offset=resume_offset,
    ):
        if row_limit is not None and row.staging_ordinal > row_limit:
            break
        chunk.append(row)
        if len(chunk) >= batch_size:
            _copy_chunk(
                engine,
                run_id=run_id,
                scope=scope,
                checkpoint_id=checkpoint_id,
                rows=chunk,
                expected_previous=high_water,
                total_rows=expected_rows,
                statement_timeout_ms=statement_timeout_ms,
            )
            high_water = chunk[-1].staging_ordinal
            final_offset = chunk[-1].byte_offset_after
            chunk_count += 1
            if progress is not None:
                progress(high_water, expected_rows)
            chunk.clear()
    if chunk:
        _copy_chunk(
            engine,
            run_id=run_id,
            scope=scope,
            checkpoint_id=checkpoint_id,
            rows=chunk,
            expected_previous=high_water,
            total_rows=expected_rows,
            statement_timeout_ms=statement_timeout_ms,
        )
        high_water = chunk[-1].staging_ordinal
        final_offset = chunk[-1].byte_offset_after
        chunk_count += 1
        if progress is not None:
            progress(high_water, expected_rows)
    if high_water != expected_rows:
        raise FD1RecoverySqlError(
            f"Identity spool rows differ: staged {high_water}, expected {expected_rows}"
        )

    analyze_started = monotonic()
    with engine.begin() as raw_connection:
        connection = _TimeoutRefreshingConnection(
            raw_connection,
            lambda: _copy_timeout(statement_timeout_ms),
        )
        connection.execute(text("ANALYZE fd1_recovery_identity_stage"))
        staging_bytes = int(
            connection.execute(
                text(
                    "SELECT pg_total_relation_size('fd1_recovery_identity_stage')"
                )
            ).scalar_one()
        )
    analyze_seconds = monotonic() - analyze_started
    elapsed = monotonic() - started
    staged_this_call = expected_rows - int(state["row_count"])
    return IdentityCopyMetrics(
        rows_staged=expected_rows,
        chunks_committed=chunk_count,
        elapsed_seconds=round(elapsed, 6),
        rows_per_second=round(staged_this_call / elapsed, 3) if elapsed else 0.0,
        resumed_after_ordinal=int(state["row_count"]),
        final_byte_offset=final_offset,
        staging_table_bytes=staging_bytes,
        analyze_seconds=round(analyze_seconds, 6),
    )


def _reconcile_identity_stage_transaction(
    engine: Engine,
    *,
    run_id: UUID,
    scope: str,
    statement_timeout_ms: _StatementTimeout,
) -> IdentityReconciliationMetrics:
    """Classify and durably quarantine staged rows in one bounded transaction."""

    _scope(scope)
    started = monotonic()
    conflict_started = monotonic()
    now = datetime.now(UTC)
    with engine.begin() as raw_connection:
        connection = _TimeoutRefreshingConnection(
            raw_connection,
            lambda: _bounded_reconciliation_timeout(statement_timeout_ms),
        )
        pending = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM fd1_recovery_identity_stage "
                    "WHERE run_id=:run_id AND scope=:scope AND outcome='PENDING'"
                ),
                {"run_id": run_id, "scope": scope},
            ).scalar_one()
        )
        total = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM fd1_recovery_identity_stage "
                    "WHERE run_id=:run_id AND scope=:scope"
                ),
                {"run_id": run_id, "scope": scope},
            ).scalar_one()
        )
        if pending not in {0, total}:
            raise FD1RecoverySqlError("Identity reconciliation is partially applied")
        if pending == 0:
            return _reconciliation_metrics(connection, run_id, scope, 0.0, 0.0)

        parameters = {"run_id": run_id, "scope": scope, "now": now}
        # A UUID foreign key alone does not prove that the spool's declared
        # source key and immutable release identity match that registry row.
        connection.execute(
            text(
                "UPDATE fd1_recovery_identity_stage s SET outcome='CONFLICT', "
                "reason_code='SOURCE_RELEASE_BINDING_CONFLICT', reconciled_at=:now "
                "WHERE s.run_id=:run_id AND s.scope=:scope AND s.outcome='PENDING' "
                "AND NOT EXISTS (SELECT 1 FROM historical_source_versions v "
                "WHERE v.id=s.source_version_id AND v.source_key=s.source_key "
                "AND v.identity_checksum=s.source_release_identity_checksum "
                "AND v.source_mode='LIVE')"
            ),
            parameters,
        )
        # Source-declared CONFLICT rows have no canonical assertion.  They are
        # quarantined and counted explicitly as source ambiguity, not silently
        # treated as resolved and not conflated with competing canonical IDs.
        connection.execute(
            text(
                "UPDATE fd1_recovery_identity_stage SET outcome='UNRESOLVED', "
                "reason_code=CASE confidence WHEN 'CONFLICT' THEN "
                "'SOURCE_DECLARED_CONFLICT_QUARANTINED' ELSE "
                "'SOURCE_DECLARED_UNRESOLVED' END, reconciled_at=:now "
                "WHERE run_id=:run_id AND scope=:scope AND outcome='PENDING' "
                "AND confidence IN ('UNRESOLVED','CONFLICT')"
            ),
            parameters,
        )
        # One natural key asserting multiple immutable identities is blocking.
        connection.execute(
            text(
                "WITH conflicting AS ("
                " SELECT source_version_id,entity_type,source_namespace,external_id,"
                " source_row_number FROM fd1_recovery_identity_stage "
                " WHERE run_id=:run_id AND scope=:scope AND outcome='PENDING' "
                " GROUP BY source_version_id,entity_type,source_namespace,external_id,"
                " source_row_number HAVING COUNT(DISTINCT identity_checksum)>1) "
                "UPDATE fd1_recovery_identity_stage s SET outcome='CONFLICT', "
                "reason_code='STAGE_NATURAL_KEY_CONFLICT', reconciled_at=:now "
                "FROM conflicting c WHERE s.run_id=:run_id AND s.scope=:scope "
                "AND s.outcome='PENDING' "
                "AND s.source_version_id=c.source_version_id "
                "AND s.entity_type=c.entity_type "
                "AND s.source_namespace=c.source_namespace "
                "AND s.external_id=c.external_id "
                "AND s.source_row_number=c.source_row_number"
            ),
            parameters,
        )
        # The row-bound storage key includes source_row_number, but the same
        # source-native ID cannot assert two canonical identities over an
        # overlapping validity window.  Disjoint historical reuse remains
        # legitimate (notably recurring organization and park identifiers).
        connection.execute(
            text(
                "WITH conflicting AS ("
                " SELECT a.staging_ordinal AS left_ordinal,"
                "b.staging_ordinal AS right_ordinal "
                "FROM fd1_recovery_identity_stage a "
                "JOIN fd1_recovery_identity_stage b ON "
                "b.run_id=a.run_id AND b.scope=a.scope "
                "AND b.staging_ordinal>a.staging_ordinal "
                "AND b.source_version_id=a.source_version_id "
                "AND b.entity_type=a.entity_type "
                "AND b.source_namespace=a.source_namespace "
                "AND b.external_id=a.external_id "
                "WHERE a.run_id=:run_id AND a.scope=:scope "
                "AND a.outcome='PENDING' AND b.outcome='PENDING' "
                "AND (a.canonical_namespace,a.canonical_id) IS DISTINCT FROM "
                "(b.canonical_namespace,b.canonical_id) "
                "AND COALESCE(a.valid_through,'infinity'::date) >= "
                "COALESCE(b.valid_from,'-infinity'::date) "
                "AND COALESCE(b.valid_through,'infinity'::date) >= "
                "COALESCE(a.valid_from,'-infinity'::date)) "
                "UPDATE fd1_recovery_identity_stage s SET outcome='CONFLICT', "
                "reason_code='STAGE_SEMANTIC_KEY_CONFLICT', reconciled_at=:now "
                "FROM conflicting c WHERE s.run_id=:run_id AND s.scope=:scope "
                "AND s.outcome='PENDING' AND s.staging_ordinal IN "
                "(c.left_ordinal,c.right_ordinal)"
            ),
            parameters,
        )
        # One checksum attached to different natural keys is also blocking.
        connection.execute(
            text(
                "WITH conflicting AS ("
                " SELECT identity_checksum FROM fd1_recovery_identity_stage "
                " WHERE run_id=:run_id AND scope=:scope AND outcome='PENDING' "
                " GROUP BY identity_checksum HAVING COUNT(DISTINCT "
                "(source_version_id,entity_type,source_namespace,external_id,"
                "source_row_number))>1) "
                "UPDATE fd1_recovery_identity_stage s SET outcome='CONFLICT', "
                "reason_code='STAGE_IDENTITY_CHECKSUM_CONFLICT', reconciled_at=:now "
                "FROM conflicting c WHERE s.run_id=:run_id AND s.scope=:scope "
                "AND s.outcome='PENDING' AND s.identity_checksum=c.identity_checksum"
            ),
            parameters,
        )
        # Collapse byte-identical repeats to the lowest durable ordinal.
        connection.execute(
            text(
                "WITH duplicates AS ("
                " SELECT source_version_id,entity_type,source_namespace,external_id,"
                " source_row_number,MIN(staging_ordinal) AS representative "
                " FROM fd1_recovery_identity_stage WHERE run_id=:run_id "
                " AND scope=:scope AND outcome='PENDING' GROUP BY source_version_id,"
                " entity_type,source_namespace,external_id,source_row_number "
                " HAVING COUNT(*)>1 AND COUNT(DISTINCT identity_checksum)=1) "
                "UPDATE fd1_recovery_identity_stage s SET outcome='REUSE', "
                "matched_stage_ordinal=d.representative,reconciled_at=:now "
                "FROM duplicates d WHERE s.run_id=:run_id AND s.scope=:scope "
                "AND s.outcome='PENDING' AND s.staging_ordinal<>d.representative "
                "AND s.source_version_id=d.source_version_id "
                "AND s.entity_type=d.entity_type "
                "AND s.source_namespace=d.source_namespace "
                "AND s.external_id=d.external_id "
                "AND s.source_row_number=d.source_row_number"
            ),
            parameters,
        )
        # Apply the same overlapping semantic-key rule against already-final
        # resolved mappings, including assertions anchored to another row.
        connection.execute(
            text(
                "UPDATE fd1_recovery_identity_stage s SET outcome='CONFLICT', "
                "reason_code='FINAL_SEMANTIC_KEY_CONFLICT', "
                "matched_mapping_id=f.id, reconciled_at=:now "
                "FROM open_data_identity_mappings f WHERE s.run_id=:run_id "
                "AND s.scope=:scope AND s.outcome='PENDING' "
                "AND f.confidence IN ('EXACT','HIGH') "
                "AND f.source_version_id=s.source_version_id "
                "AND f.entity_type=s.entity_type "
                "AND f.source_namespace=s.source_namespace "
                "AND f.external_id=s.external_id "
                "AND (f.canonical_namespace,f.canonical_id) IS DISTINCT FROM "
                "(s.canonical_namespace,s.canonical_id) "
                "AND COALESCE(f.valid_through,'infinity'::date) >= "
                "COALESCE(s.valid_from,'-infinity'::date) "
                "AND COALESCE(s.valid_through,'infinity'::date) >= "
                "COALESCE(f.valid_from,'-infinity'::date)"
            ),
            parameters,
        )
        # Existing natural keys are either exact immutable reuse or conflict.
        connection.execute(
            text(
                "UPDATE fd1_recovery_identity_stage s SET "
                "outcome=CASE WHEN "
                " f.canonical_namespace=s.canonical_namespace "
                " AND f.canonical_id IS NOT DISTINCT FROM s.canonical_id "
                " AND f.source_row_checksum=s.source_row_checksum "
                " AND f.confidence=s.confidence "
                " AND f.valid_from IS NOT DISTINCT FROM s.valid_from "
                " AND f.valid_through IS NOT DISTINCT FROM s.valid_through "
                " AND f.evidence_logical_uri=s.evidence_logical_uri "
                " AND f.identity_checksum=s.identity_checksum "
                " THEN 'REUSE' ELSE 'CONFLICT' END, "
                "matched_mapping_id=f.id, "
                "reason_code=CASE WHEN "
                " f.canonical_namespace=s.canonical_namespace "
                " AND f.canonical_id IS NOT DISTINCT FROM s.canonical_id "
                " AND f.source_row_checksum=s.source_row_checksum "
                " AND f.confidence=s.confidence "
                " AND f.valid_from IS NOT DISTINCT FROM s.valid_from "
                " AND f.valid_through IS NOT DISTINCT FROM s.valid_through "
                " AND f.evidence_logical_uri=s.evidence_logical_uri "
                " AND f.identity_checksum=s.identity_checksum "
                " THEN NULL ELSE 'FINAL_NATURAL_KEY_CONFLICT' END, reconciled_at=:now "
                "FROM open_data_identity_mappings f WHERE s.run_id=:run_id "
                "AND s.scope=:scope AND s.outcome='PENDING' "
                "AND f.source_version_id=s.source_version_id "
                "AND f.entity_type=s.entity_type "
                "AND f.source_namespace=s.source_namespace "
                "AND f.external_id=s.external_id "
                "AND f.source_row_number=s.source_row_number"
            ),
            parameters,
        )
        # A final checksum on another natural key cannot be inserted.
        connection.execute(
            text(
                "UPDATE fd1_recovery_identity_stage s SET outcome='CONFLICT', "
                "reason_code='FINAL_IDENTITY_CHECKSUM_CONFLICT', "
                "matched_mapping_id=f.id, reconciled_at=:now "
                "FROM open_data_identity_mappings f WHERE s.run_id=:run_id "
                "AND s.scope=:scope AND s.outcome='PENDING' "
                "AND f.identity_checksum=s.identity_checksum "
                "AND (f.source_version_id,f.entity_type,f.source_namespace,"
                "f.external_id,f.source_row_number) IS DISTINCT FROM "
                "(s.source_version_id,s.entity_type,s.source_namespace,"
                "s.external_id,s.source_row_number)"
            ),
            parameters,
        )
        connection.execute(
            text(
                "UPDATE fd1_recovery_identity_stage SET outcome='INSERT', "
                "reconciled_at=:now WHERE run_id=:run_id AND scope=:scope "
                "AND outcome='PENDING'"
            ),
            parameters,
        )
        leftovers = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM fd1_recovery_identity_stage "
                    "WHERE run_id=:run_id AND scope=:scope AND outcome='PENDING'"
                ),
                parameters,
            ).scalar_one()
        )
        if leftovers:
            raise FD1RecoverySqlError("Identity reconciliation left pending rows")
        _write_quarantine(connection, run_id=run_id, scope=scope)
        metrics = _reconciliation_metrics(
            connection,
            run_id,
            scope,
            monotonic() - started,
            monotonic() - conflict_started,
        )
        return metrics


def reconcile_identity_stage(
    engine: Engine,
    *,
    run_id: UUID,
    scope: str,
    statement_timeout_ms: _StatementTimeout = _DEFAULT_RECONCILIATION_TIMEOUT_MS,
) -> IdentityReconciliationMetrics:
    """Classify mappings, preserve evidence, then fail on semantic conflicts.

    The inner function returns normally from ``engine.begin()`` so PostgreSQL
    commits outcomes and quarantine evidence first.  Raising only afterward
    prevents a blocking conflict from rolling back the evidence needed to
    diagnose and safely resume the run.
    """

    if not callable(statement_timeout_ms):
        _bounded_reconciliation_timeout(statement_timeout_ms)
    metrics = _reconcile_identity_stage_transaction(
        engine,
        run_id=run_id,
        scope=scope,
        statement_timeout_ms=statement_timeout_ms,
    )
    if metrics.blocking_conflicts:
        raise FD1RecoverySqlError(
            "Identity reconciliation found "
            f"{metrics.blocking_conflicts} blocking conflicts"
        )
    return metrics


def promote_identity_mappings(
    connection: Any,
    *,
    run_id: UUID,
    scope: str = "FULL",
    statement_timeout_ms: _StatementTimeout | None = None,
) -> IdentityPromotionMetrics:
    """Promote resolved mappings inside the caller's atomic transaction."""

    _scope(scope)
    if statement_timeout_ms is not None and not callable(statement_timeout_ms):
        _promotion_timeout(statement_timeout_ms)
    timed_connection = (
        _TimeoutRefreshingConnection(
            connection,
            lambda: _promotion_timeout(statement_timeout_ms),
        )
        if statement_timeout_ms is not None
        else connection
    )
    started = monotonic()
    counts = _outcome_counts(timed_connection, run_id, scope)
    blocking = counts.get("CONFLICT", 0)
    if blocking:
        raise FD1RecoverySqlError(
            f"Cannot promote {blocking} conflicting identity mappings"
        )
    pending = counts.get("PENDING", 0)
    if pending:
        raise FD1RecoverySqlError(
            f"Cannot promote {pending} unreconciled identity mappings"
        )
    inserted_result = timed_connection.execute(
        text(
            "INSERT INTO open_data_identity_mappings "
            "(id,source_version_id,entity_type,source_namespace,external_id,"
            "canonical_namespace,canonical_id,source_row_number,source_row_checksum,"
            "confidence,valid_from,valid_through,evidence_logical_uri,identity_checksum) "
            "SELECT target_mapping_id,source_version_id,entity_type,source_namespace,"
            "external_id,canonical_namespace,canonical_id,source_row_number,"
            "source_row_checksum,confidence,valid_from,valid_through,"
            "evidence_logical_uri,identity_checksum "
            "FROM fd1_recovery_identity_stage WHERE run_id=:run_id AND scope=:scope "
            "AND outcome='INSERT' ON CONFLICT DO NOTHING"
        ),
        {"run_id": run_id, "scope": scope},
    )
    inserted = max(0, int(inserted_result.rowcount or 0))
    mismatch = int(
        timed_connection.execute(
            text(
                "SELECT COUNT(*) FROM fd1_recovery_identity_stage s "
                "LEFT JOIN open_data_identity_mappings f ON "
                "f.source_version_id=s.source_version_id "
                "AND f.entity_type=s.entity_type "
                "AND f.source_namespace=s.source_namespace "
                "AND f.external_id=s.external_id "
                "AND f.source_row_number=s.source_row_number "
                "WHERE s.run_id=:run_id AND s.scope=:scope "
                "AND s.outcome IN ('INSERT','REUSE') AND (f.id IS NULL "
                "OR f.canonical_namespace<>s.canonical_namespace "
                "OR f.canonical_id IS DISTINCT FROM s.canonical_id "
                "OR f.source_row_checksum<>s.source_row_checksum "
                "OR f.confidence<>s.confidence "
                "OR f.valid_from IS DISTINCT FROM s.valid_from "
                "OR f.valid_through IS DISTINCT FROM s.valid_through "
                "OR f.evidence_logical_uri<>s.evidence_logical_uri "
                "OR f.identity_checksum<>s.identity_checksum)"
            ),
            {"run_id": run_id, "scope": scope},
        ).scalar_one()
    )
    if mismatch:
        raise FD1RecoverySqlError(
            f"Atomic mapping promotion produced {mismatch} semantic mismatches"
        )
    resolved = counts.get("INSERT", 0) + counts.get("REUSE", 0)
    source_conflicts = int(
        timed_connection.execute(
            text(
                "SELECT COUNT(*) FROM fd1_recovery_identity_stage "
                "WHERE run_id=:run_id AND scope=:scope "
                "AND reason_code='SOURCE_DECLARED_CONFLICT_QUARANTINED'"
            ),
            {"run_id": run_id, "scope": scope},
        ).scalar_one()
    )
    return IdentityPromotionMetrics(
        inserted=inserted,
        reused=resolved - inserted,
        unresolved=counts.get("UNRESOLVED", 0) - source_conflicts,
        source_declared_conflicts=source_conflicts,
        blocking_conflicts=0,
        promotion_seconds=round(monotonic() - started, 6),
    )


def explain_identity_reconciliation(
    engine: Engine,
    *,
    run_id: UUID,
    scope: str,
) -> Mapping[str, Any]:
    """Return PostgreSQL JSON EXPLAIN evidence for the natural-key lookup."""

    _scope(scope)
    with engine.connect() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '1200000ms'"))
        plan = connection.execute(
            text(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                "SELECT s.staging_ordinal,f.id FROM fd1_recovery_identity_stage s "
                "LEFT JOIN open_data_identity_mappings f ON "
                "f.source_version_id=s.source_version_id "
                "AND f.entity_type=s.entity_type "
                "AND f.source_namespace=s.source_namespace "
                "AND f.external_id=s.external_id "
                "AND f.source_row_number=s.source_row_number "
                "WHERE s.run_id=:run_id AND s.scope=:scope "
                "ORDER BY s.staging_ordinal LIMIT 10000"
            ),
            {"run_id": run_id, "scope": scope},
        ).scalar_one()
    document = plan[0] if isinstance(plan, list) and plan else plan
    if not isinstance(document, Mapping):
        raise FD1RecoverySqlError("PostgreSQL EXPLAIN did not return JSON evidence")
    return dict(document)


def _copy_chunk(
    engine: Engine,
    *,
    run_id: UUID,
    scope: str,
    checkpoint_id: UUID,
    rows: list[IdentitySpoolRow],
    expected_previous: int,
    total_rows: int,
    statement_timeout_ms: _StatementTimeout,
) -> None:
    if not rows or rows[0].staging_ordinal != expected_previous + 1:
        raise FD1RecoverySqlError("Identity COPY chunk is not contiguous")
    with engine.connect() as raw_connection:
        transaction = raw_connection.begin()
        connection = _TimeoutRefreshingConnection(
            raw_connection,
            lambda: _copy_timeout(statement_timeout_ms),
        )
        try:
            connection.execute(
                text(
                    "CREATE TEMP TABLE fd1_identity_copy_chunk "
                    "(staging_ordinal bigint,batch_number integer,batch_checksum text,"
                    "source_key text,source_version_id uuid,"
                    "source_release_identity_checksum text,entity_type text,"
                    "source_namespace text,external_id text,canonical_namespace text,"
                    "canonical_id text,source_row_number bigint,source_row_checksum text,"
                    "confidence text,valid_from date,valid_through date,"
                    "evidence_logical_uri text,mapping_basis text,identity_checksum text,"
                    "record_checksum text,target_mapping_id uuid) ON COMMIT DROP"
                )
            )
            driver = connection.connection.driver_connection
            connection.refresh_timeout()
            with driver.cursor() as cursor:
                with cursor.copy(
                    "COPY fd1_identity_copy_chunk ("
                    + ",".join(IDENTITY_SPOOL_COLUMNS)
                    + ") FROM STDIN"
                ) as copy:
                    for row in rows:
                        copy.write_row(row.values)
            inserted = connection.execute(
                text(
                    "INSERT INTO fd1_recovery_identity_stage "
                    "(run_id,scope,staging_ordinal,batch_number,batch_checksum,"
                    "source_key,source_version_id,source_release_identity_checksum,"
                    "entity_type,source_namespace,external_id,canonical_namespace,"
                    "canonical_id,source_row_number,source_row_checksum,confidence,"
                    "valid_from,valid_through,evidence_logical_uri,mapping_basis,"
                    "identity_checksum,record_checksum,target_mapping_id,outcome) "
                    "SELECT :run_id,:scope,staging_ordinal,batch_number,batch_checksum,"
                    "source_key,source_version_id,source_release_identity_checksum,"
                    "entity_type,source_namespace,external_id,canonical_namespace,"
                    "canonical_id,source_row_number,source_row_checksum,confidence,"
                    "valid_from,valid_through,evidence_logical_uri,mapping_basis,"
                    "identity_checksum,record_checksum,target_mapping_id,'PENDING' "
                    "FROM fd1_identity_copy_chunk ORDER BY staging_ordinal"
                ),
                {"run_id": run_id, "scope": scope},
            )
            if int(inserted.rowcount or 0) != len(rows):
                raise FD1RecoverySqlError("Identity COPY chunk insert count differs")
            high_water = rows[-1].staging_ordinal
            progress = {
                "mappings_staged": high_water,
                "mappings_total": total_rows,
            }
            resume = {
                "high_water_ordinal": high_water,
                "byte_offset": rows[-1].byte_offset_after,
            }
            updated = connection.execute(
                text(
                    "UPDATE fd1_recovery_stage_checkpoints SET "
                    "progress=CAST(:progress AS json),"
                    "resume_token=CAST(:resume AS json),heartbeat_at=:now,updated_at=:now "
                    "WHERE id=:checkpoint_id AND run_id=:run_id AND scope=:scope "
                    "AND stage_name='IDENTITY_STAGED' AND status='RUNNING'"
                ),
                {
                    "progress": json.dumps(progress, sort_keys=True),
                    "resume": json.dumps(resume, sort_keys=True),
                    "now": datetime.now(UTC),
                    "checkpoint_id": checkpoint_id,
                    "run_id": run_id,
                    "scope": scope,
                },
            )
            if int(updated.rowcount or 0) != 1:
                raise FD1RecoverySqlError("Identity COPY checkpoint update failed")
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise


def _write_quarantine(connection: Any, *, run_id: UUID, scope: str) -> None:
    after_ordinal = 0
    while True:
        rows = list(
            connection.execute(
                text(
                    "SELECT s.staging_ordinal,s.outcome,s.reason_code,"
                    "s.record_checksum,s.matched_mapping_id,f.source_version_id "
                    "AS existing_source_version_id,f.entity_type AS existing_entity_type,"
                    "f.source_namespace AS existing_source_namespace,"
                    "f.external_id AS existing_external_id,"
                    "f.canonical_namespace AS existing_canonical_namespace,"
                    "f.canonical_id AS existing_canonical_id,"
                    "f.source_row_number AS existing_source_row_number,"
                    "f.source_row_checksum AS existing_source_row_checksum,"
                    "f.confidence AS existing_confidence,"
                    "f.valid_from AS existing_valid_from,"
                    "f.valid_through AS existing_valid_through,"
                    "f.evidence_logical_uri AS existing_evidence_logical_uri,"
                    "f.identity_checksum AS existing_identity_checksum "
                    "FROM fd1_recovery_identity_stage s "
                    "LEFT JOIN open_data_identity_mappings f "
                    "ON f.id=s.matched_mapping_id "
                    "WHERE s.run_id=:run_id AND s.scope=:scope "
                    "AND s.outcome IN ('UNRESOLVED','CONFLICT') "
                    "AND s.staging_ordinal>:after_ordinal "
                    "ORDER BY s.staging_ordinal LIMIT :batch_size"
                ),
                {
                    "run_id": run_id,
                    "scope": scope,
                    "after_ordinal": after_ordinal,
                    "batch_size": _QUARANTINE_BATCH_SIZE,
                },
            ).mappings()
        )
        if not rows:
            return
        through_ordinal = int(rows[-1]["staging_ordinal"])
        existing_rows = {
            (int(row["staging_ordinal"]), str(row["reason_code"])): str(
                row["evidence_checksum"]
            )
            for row in connection.execute(
                text(
                    "SELECT staging_ordinal,reason_code,evidence_checksum "
                    "FROM fd1_recovery_identity_quarantine "
                    "WHERE run_id=:run_id AND scope=:scope "
                    "AND staging_ordinal>:after_ordinal "
                    "AND staging_ordinal<=:through_ordinal"
                ),
                {
                    "run_id": run_id,
                    "scope": scope,
                    "after_ordinal": after_ordinal,
                    "through_ordinal": through_ordinal,
                },
            ).mappings()
        }
        inserts: list[dict[str, Any]] = []
        for row in rows:
            existing_payload = _quarantine_existing_payload(row)
            evidence = canonical_sha256(
                {
                    "run_id": str(run_id),
                    "scope": scope,
                    "staging_ordinal": int(row["staging_ordinal"]),
                    "outcome": row["outcome"],
                    "reason_code": row["reason_code"],
                    "record_checksum": row["record_checksum"],
                    "existing_mapping_id": (
                        str(row["matched_mapping_id"])
                        if row["matched_mapping_id"] is not None
                        else None
                    ),
                    "existing_payload": existing_payload,
                }
            )
            payload = {
                "id": uuid5(
                    _IDENTITY_NAMESPACE,
                    f"recovery-quarantine:{run_id}:{scope}:"
                    f"{row['staging_ordinal']}:{row['reason_code']}",
                ),
                "run_id": run_id,
                "scope": scope,
                "staging_ordinal": int(row["staging_ordinal"]),
                "outcome": row["outcome"],
                "reason_code": row["reason_code"],
                "staged_record_checksum": row["record_checksum"],
                "existing_mapping_id": row["matched_mapping_id"],
                "existing_payload": (
                    json.dumps(existing_payload, sort_keys=True, separators=(",", ":"))
                    if existing_payload is not None
                    else None
                ),
                "evidence_checksum": evidence,
            }
            existing = existing_rows.get(
                (int(payload["staging_ordinal"]), str(payload["reason_code"]))
            )
            if existing is None:
                inserts.append(payload)
            elif existing != payload["evidence_checksum"]:
                raise FD1RecoverySqlError(
                    "Quarantine evidence changed across resume"
                )
        if inserts:
            connection.execute(
                text(
                    "INSERT INTO fd1_recovery_identity_quarantine "
                    "(id,run_id,scope,staging_ordinal,outcome,reason_code,"
                    "staged_record_checksum,existing_mapping_id,existing_payload,"
                    "evidence_checksum) VALUES (:id,:run_id,:scope,:staging_ordinal,"
                    ":outcome,:reason_code,:staged_record_checksum,"
                    ":existing_mapping_id,CAST(:existing_payload AS json),"
                    ":evidence_checksum)"
                ),
                inserts,
            )
        after_ordinal = through_ordinal


def _quarantine_existing_payload(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if row["matched_mapping_id"] is None:
        return None
    return {
        "id": str(row["matched_mapping_id"]),
        "source_version_id": str(row["existing_source_version_id"]),
        "entity_type": row["existing_entity_type"],
        "source_namespace": row["existing_source_namespace"],
        "external_id": row["existing_external_id"],
        "canonical_namespace": row["existing_canonical_namespace"],
        "canonical_id": row["existing_canonical_id"],
        "source_row_number": int(row["existing_source_row_number"]),
        "source_row_checksum": row["existing_source_row_checksum"],
        "confidence": row["existing_confidence"],
        "valid_from": (
            row["existing_valid_from"].isoformat()
            if row["existing_valid_from"] is not None
            else None
        ),
        "valid_through": (
            row["existing_valid_through"].isoformat()
            if row["existing_valid_through"] is not None
            else None
        ),
        "evidence_logical_uri": row["existing_evidence_logical_uri"],
        "identity_checksum": row["existing_identity_checksum"],
    }


def _reconciliation_metrics(
    connection: Any,
    run_id: UUID,
    scope: str,
    elapsed: float,
    conflict_elapsed: float,
) -> IdentityReconciliationMetrics:
    counts = _outcome_counts(connection, run_id, scope)
    source_conflicts = int(
        connection.execute(
            text(
                "SELECT COUNT(*) FROM fd1_recovery_identity_stage "
                "WHERE run_id=:run_id AND scope=:scope "
                "AND reason_code='SOURCE_DECLARED_CONFLICT_QUARANTINED'"
            ),
            {"run_id": run_id, "scope": scope},
        ).scalar_one()
    )
    quarantine = int(
        connection.execute(
            text(
                "SELECT COUNT(*) FROM fd1_recovery_identity_quarantine "
                "WHERE run_id=:run_id AND scope=:scope"
            ),
            {"run_id": run_id, "scope": scope},
        ).scalar_one()
    )
    return IdentityReconciliationMetrics(
        total_rows=sum(counts.values()),
        planned_insert=counts.get("INSERT", 0),
        exact_reuse=counts.get("REUSE", 0),
        unresolved=counts.get("UNRESOLVED", 0) - source_conflicts,
        source_declared_conflicts=source_conflicts,
        blocking_conflicts=counts.get("CONFLICT", 0),
        quarantine_rows=quarantine,
        reconciliation_seconds=round(elapsed, 6),
        conflict_query_seconds=round(conflict_elapsed, 6),
    )


def _outcome_counts(connection: Any, run_id: UUID, scope: str) -> dict[str, int]:
    return {
        str(row["outcome"]): int(row["row_count"])
        for row in connection.execute(
            text(
                "SELECT outcome,COUNT(*) AS row_count "
                "FROM fd1_recovery_identity_stage WHERE run_id=:run_id "
                "AND scope=:scope GROUP BY outcome"
            ),
            {"run_id": run_id, "scope": scope},
        ).mappings()
    }


def _coerce_spool_record(record: Mapping[str, str]) -> tuple[Any, ...]:
    values: dict[str, Any] = dict(record)
    for field in ("staging_ordinal", "batch_number", "source_row_number"):
        values[field] = _positive_int(values[field], field)
    for field in ("source_version_id", "target_mapping_id"):
        try:
            values[field] = UUID(values[field])
        except (TypeError, ValueError) as exc:
            raise FD1RecoverySqlError(f"Identity spool {field} is invalid") from exc
    for field in (
        "batch_checksum",
        "source_release_identity_checksum",
        "source_row_checksum",
        "identity_checksum",
        "record_checksum",
    ):
        _sha256(values[field], field)
    for field in ("valid_from", "valid_through"):
        try:
            values[field] = date.fromisoformat(values[field]) if values[field] else None
        except ValueError as exc:
            raise FD1RecoverySqlError(f"Identity spool {field} is invalid") from exc
    values["canonical_id"] = values["canonical_id"] or None
    if any("\r" in str(value) or "\n" in str(value) for value in values.values()):
        raise FD1RecoverySqlError("Identity spool fields cannot contain line breaks")
    return tuple(values[name] for name in IDENTITY_SPOOL_COLUMNS)


def _csv_line(raw: bytes, *, line_number: int) -> list[str]:
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FD1RecoverySqlError(
            f"Identity spool is not UTF-8 at line {line_number}"
        ) from exc
    if "\r" in decoded.rstrip("\r\n") or "\n" in decoded.rstrip("\r\n"):
        raise FD1RecoverySqlError("Identity spool contains embedded line breaks")
    try:
        rows = list(csv.reader([decoded]))
    except csv.Error as exc:
        raise FD1RecoverySqlError(
            f"Identity spool CSV is malformed at line {line_number}"
        ) from exc
    if len(rows) != 1:
        raise FD1RecoverySqlError("Identity spool row is not physical-line bounded")
    return rows[0]


def _positive_int(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise FD1RecoverySqlError(f"{name} must be a positive integer") from exc
    if result < 1:
        raise FD1RecoverySqlError(f"{name} must be a positive integer")
    return result


def _sha256(value: Any, name: str) -> str:
    selected = str(value)
    if len(selected) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in selected
    ):
        raise FD1RecoverySqlError(f"{name} must be lowercase SHA-256")
    return selected


def _bounded_reconciliation_timeout(value: _StatementTimeout) -> int:
    selected = value() if callable(value) else value
    if (
        isinstance(selected, bool)
        or not isinstance(selected, int)
        or selected < 1
        or selected > _DEFAULT_RECONCILIATION_TIMEOUT_MS
    ):
        raise ValueError(
            "statement_timeout_ms must be between 1 and 1200000"
        )
    return selected


def _copy_timeout(value: _StatementTimeout) -> int:
    selected = value() if callable(value) else value
    if (
        isinstance(selected, bool)
        or not isinstance(selected, int)
        or selected < 1
        or selected > _DEFAULT_COPY_TIMEOUT_MS
    ):
        raise ValueError("COPY statement_timeout_ms must be between 1 and 300000")
    return selected


def _promotion_timeout(value: _StatementTimeout | None) -> int:
    if value is None:
        raise ValueError("promotion statement_timeout_ms is required")
    selected = value() if callable(value) else value
    if (
        isinstance(selected, bool)
        or not isinstance(selected, int)
        or selected < 1
        or selected > _DEFAULT_PROMOTION_TIMEOUT_MS
    ):
        raise ValueError(
            "promotion statement_timeout_ms must be between 1 and 1800000"
        )
    return selected


def _scope(value: str) -> str:
    if value not in IDENTITY_STAGE_SCOPE_VALUES:
        raise ValueError("scope must be FULL or REHEARSAL")
    return value


__all__ = [
    "FD1RecoverySqlError",
    "IDENTITY_SPOOL_COLUMNS",
    "IDENTITY_STAGE_SCOPE_VALUES",
    "IdentityCopyMetrics",
    "IdentityPromotionMetrics",
    "IdentityReconciliationMetrics",
    "IdentitySpoolRow",
    "copy_identity_spool",
    "explain_identity_reconciliation",
    "iter_identity_spool_rows",
    "promote_identity_mappings",
    "reconcile_identity_stage",
]
