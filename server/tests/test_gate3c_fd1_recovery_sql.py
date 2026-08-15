from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import create_engine

import app.gen2.fd1_recovery_sql as recovery_sql
from app.gen2.fd1_recovery_sql import (
    FD1RecoverySqlError,
    IDENTITY_SPOOL_COLUMNS,
    IdentityReconciliationMetrics,
    copy_identity_spool,
    iter_identity_spool_rows,
    reconcile_identity_stage,
)


def _record(ordinal: int) -> dict[str, object]:
    checksum = hashlib.sha256(f"row-{ordinal}".encode()).hexdigest()
    identity = hashlib.sha256(f"identity-{ordinal}".encode()).hexdigest()
    return {
        "staging_ordinal": ordinal,
        "batch_number": 1,
        "batch_checksum": "a" * 64,
        "source_key": "CHADWICK_REGISTER_ARCHIVES",
        "source_version_id": "11111111-1111-1111-1111-111111111111",
        "source_release_identity_checksum": "b" * 64,
        "entity_type": "person",
        "source_namespace": "retrosheet",
        "external_id": f"player-{ordinal}",
        "canonical_namespace": "chadwick_uuid",
        "canonical_id": f"canonical-{ordinal}",
        "source_row_number": ordinal,
        "source_row_checksum": checksum,
        "confidence": "EXACT",
        "valid_from": "2021-01-01",
        "valid_through": "2025-12-31",
        "evidence_logical_uri": "open-data/chadwick/people-a.csv",
        "mapping_basis": "EXACT_SOURCE_IDENTIFIER",
        "identity_checksum": identity,
        "record_checksum": hashlib.sha256(f"record-{ordinal}".encode()).hexdigest(),
        "target_mapping_id": f"00000000-0000-0000-0000-{ordinal:012d}",
    }


def _spool(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=IDENTITY_SPOOL_COLUMNS)
        writer.writeheader()
        writer.writerows(records)


def test_identity_spool_parser_is_exact_and_resumable(tmp_path: Path) -> None:
    path = tmp_path / "identity.csv"
    _spool(path, [_record(1), _record(2), _record(3)])

    rows = list(iter_identity_spool_rows(path))
    resumed = list(iter_identity_spool_rows(path, start_ordinal=3))

    assert [row.staging_ordinal for row in rows] == [1, 2, 3]
    assert [row.staging_ordinal for row in resumed] == [3]
    assert rows[-1].byte_offset_after == path.stat().st_size
    assert rows[0].values[0] == 1
    assert rows[0].values[4] == UUID("11111111-1111-1111-1111-111111111111")

    cursor_resumed = list(
        iter_identity_spool_rows(
            path,
            start_ordinal=3,
            start_byte_offset=rows[1].byte_offset_after,
        )
    )
    assert [row.staging_ordinal for row in cursor_resumed] == [3]

    with pytest.raises(FD1RecoverySqlError, match="record boundary"):
        list(
            iter_identity_spool_rows(
                path,
                start_ordinal=3,
                start_byte_offset=rows[1].byte_offset_after + 1,
            )
        )


def test_identity_spool_parser_rejects_header_and_ordinal_drift(tmp_path: Path) -> None:
    wrong_header = tmp_path / "wrong-header.csv"
    wrong_header.write_text("ordinal,external_id\n1,p1\n", encoding="utf-8")
    with pytest.raises(FD1RecoverySqlError, match="header"):
        list(iter_identity_spool_rows(wrong_header))

    wrong_ordinal = tmp_path / "wrong-ordinal.csv"
    _spool(wrong_ordinal, [_record(1), _record(3)])
    with pytest.raises(FD1RecoverySqlError, match="contiguous"):
        list(iter_identity_spool_rows(wrong_ordinal))


def test_copy_refuses_non_postgresql_and_cached_hash_without_stat(
    tmp_path: Path,
) -> None:
    path = tmp_path / "identity.csv"
    _spool(path, [_record(1)])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    engine = create_engine("sqlite://")

    with pytest.raises(FD1RecoverySqlError, match="requires PostgreSQL"):
        copy_identity_spool(
            engine,
            run_id=UUID("22222222-2222-2222-2222-222222222222"),
            scope="FULL",
            spool_path=path,
            expected_sha256=digest,
            expected_rows=1,
            batch_size=1,
            checkpoint_id=UUID("33333333-3333-3333-3333-333333333333"),
            reuse_verified_hash=True,
        )


def test_identity_spool_rejects_invalid_checksum_and_date(tmp_path: Path) -> None:
    invalid_checksum = _record(1)
    invalid_checksum["record_checksum"] = "g" * 64
    path = tmp_path / "invalid-checksum.csv"
    _spool(path, [invalid_checksum])
    with pytest.raises(FD1RecoverySqlError, match="record_checksum"):
        list(iter_identity_spool_rows(path))

    invalid_date = _record(1)
    invalid_date["valid_from"] = "2025-02-30"
    path = tmp_path / "invalid-date.csv"
    _spool(path, [invalid_date])
    with pytest.raises(FD1RecoverySqlError, match="valid_from"):
        list(iter_identity_spool_rows(path))


def test_copy_semantically_validates_spool_before_database_access(
    tmp_path: Path,
) -> None:
    temporary = tmp_path / "identity.csv"
    _spool(temporary, [_record(1)])
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    path = tmp_path / f"fd1-identity-spool-sha256-{digest}.csv"
    temporary.replace(path)

    class NoConnectEngine:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self) -> Any:  # pragma: no cover - must remain unreachable
            raise AssertionError("invalid spool reached database access")

    with pytest.raises(FD1RecoverySqlError, match="semantic validation"):
        copy_identity_spool(
            NoConnectEngine(),  # type: ignore[arg-type]
            run_id=UUID("22222222-2222-2222-2222-222222222222"),
            scope="FULL",
            spool_path=path,
            expected_sha256=digest,
            expected_size_bytes=path.stat().st_size,
            expected_rows=1,
            batch_size=1,
            checkpoint_id=UUID("33333333-3333-3333-3333-333333333333"),
        )


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class _ReconciliationConnection:
    def __init__(self) -> None:
        self.pending_queries = 0
        self.statements: list[str] = []

    def execute(self, statement: Any, parameters: Any = None) -> _ScalarResult:
        del parameters
        sql = str(statement)
        self.statements.append(sql)
        if "SELECT COUNT(*)" in sql and "outcome='PENDING'" in sql:
            self.pending_queries += 1
            return _ScalarResult(1 if self.pending_queries == 1 else 0)
        if "SELECT COUNT(*)" in sql:
            return _ScalarResult(1)
        return _ScalarResult(0)


class _BeginContext:
    def __init__(self, connection: _ReconciliationConnection) -> None:
        self.connection = connection
        self.committed = False

    def __enter__(self) -> _ReconciliationConnection:
        return self.connection

    def __exit__(self, exception_type: Any, exception: Any, traceback: Any) -> None:
        del exception, traceback
        self.committed = exception_type is None


class _ReconciliationEngine:
    def __init__(self) -> None:
        self.connection = _ReconciliationConnection()
        self.context = _BeginContext(self.connection)

    def begin(self) -> _BeginContext:
        return self.context


def _metrics(*, blocking: int, source_ambiguity: int = 0) -> IdentityReconciliationMetrics:
    return IdentityReconciliationMetrics(
        total_rows=1,
        planned_insert=0,
        exact_reuse=0,
        unresolved=0,
        source_declared_conflicts=source_ambiguity,
        blocking_conflicts=blocking,
        quarantine_rows=1,
        reconciliation_seconds=0.1,
        conflict_query_seconds=0.1,
    )


def test_reconciliation_commits_quarantine_before_blocking_conflict_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _ReconciliationEngine()
    quarantined: list[bool] = []
    monkeypatch.setattr(
        recovery_sql,
        "_write_quarantine",
        lambda connection, *, run_id, scope: quarantined.append(True),
    )
    monkeypatch.setattr(
        recovery_sql,
        "_reconciliation_metrics",
        lambda *args, **kwargs: _metrics(blocking=1),
    )

    with pytest.raises(FD1RecoverySqlError, match="1 blocking conflicts"):
        reconcile_identity_stage(
            engine,  # type: ignore[arg-type]
            run_id=UUID("22222222-2222-2222-2222-222222222222"),
            scope="FULL",
        )

    assert quarantined == [True]
    assert engine.context.committed is True
    executed = "\n".join(engine.connection.statements)
    assert "historical_source_versions" in executed
    assert "STAGE_SEMANTIC_KEY_CONFLICT" in executed
    assert "'infinity'::date" in executed
    assert "matched_mapping_id=f.id" in executed


def test_source_declared_ambiguity_is_quarantined_but_not_semantic_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _ReconciliationEngine()
    monkeypatch.setattr(recovery_sql, "_write_quarantine", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        recovery_sql,
        "_reconciliation_metrics",
        lambda *args, **kwargs: _metrics(blocking=0, source_ambiguity=5),
    )

    metrics = reconcile_identity_stage(
        engine,  # type: ignore[arg-type]
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        scope="FULL",
    )

    assert metrics.source_declared_conflicts == 5
    assert metrics.blocking_conflicts == 0
    executed = "\n".join(engine.connection.statements)
    assert "SOURCE_DECLARED_CONFLICT_QUARANTINED" in executed


def test_reconciliation_recomputes_timeout_before_each_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _ReconciliationEngine()
    resolutions: list[int] = []
    monkeypatch.setattr(recovery_sql, "_write_quarantine", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        recovery_sql,
        "_reconciliation_metrics",
        lambda *args, **kwargs: _metrics(blocking=0),
    )

    def remaining() -> int:
        resolutions.append(len(resolutions))
        return 1_000

    reconcile_identity_stage(
        engine,  # type: ignore[arg-type]
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        scope="FULL",
        statement_timeout_ms=remaining,
    )

    assert len(resolutions) >= 10


def test_reconciliation_deadline_stop_propagates_and_rolls_back() -> None:
    engine = _ReconciliationEngine()
    resolutions = 0

    def remaining() -> int:
        nonlocal resolutions
        resolutions += 1
        if resolutions == 3:
            raise RuntimeError("HARD_DEADLINE_REACHED")
        return 1_000

    with pytest.raises(RuntimeError, match="HARD_DEADLINE_REACHED"):
        reconcile_identity_stage(
            engine,  # type: ignore[arg-type]
            run_id=UUID("22222222-2222-2222-2222-222222222222"),
            scope="FULL",
            statement_timeout_ms=remaining,
        )

    assert engine.context.committed is False


@pytest.mark.parametrize("timeout", [True, 0, 1_200_001])
def test_reconciliation_statement_timeout_is_positive_and_capped(timeout: Any) -> None:
    with pytest.raises(ValueError, match="statement_timeout_ms"):
        reconcile_identity_stage(
            _ReconciliationEngine(),  # type: ignore[arg-type]
            run_id=UUID("22222222-2222-2222-2222-222222222222"),
            scope="FULL",
            statement_timeout_ms=timeout,
        )


def test_copy_timeout_callback_is_recomputed_at_each_boundary() -> None:
    remaining = iter((300_000, 17))
    resolver = remaining.__next__

    assert recovery_sql._copy_timeout(resolver) == 300_000  # noqa: SLF001
    assert recovery_sql._copy_timeout(resolver) == 17  # noqa: SLF001


def test_copy_chunk_refreshes_timeout_before_each_database_boundary() -> None:
    class _Result:
        rowcount = 1

    class _Transaction:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

    class _Copy:
        def __enter__(self) -> _Copy:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def write_row(self, _values: tuple[Any, ...]) -> None:
            return None

    class _Cursor:
        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def copy(self, _statement: str) -> _Copy:
            return _Copy()

    class _Driver:
        def cursor(self) -> _Cursor:
            return _Cursor()

    class _Connection:
        def __init__(self) -> None:
            self.transaction = _Transaction()
            self.connection = SimpleNamespace(driver_connection=_Driver())
            self.timeouts: list[str] = []

        def __enter__(self) -> _Connection:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def begin(self) -> _Transaction:
            return self.transaction

        def execute(self, statement: Any, parameters: Any = None) -> _Result:
            if "set_config('statement_timeout'" in str(statement):
                self.timeouts.append(parameters["timeout"])
            return _Result()

    class _Engine:
        def __init__(self) -> None:
            self.connection = _Connection()

        def connect(self) -> _Connection:
            return self.connection

    engine = _Engine()
    remaining = iter((400, 300, 200, 100))
    row = recovery_sql.IdentitySpoolRow(
        values=(1,),
        staging_ordinal=1,
        byte_offset_after=99,
    )

    recovery_sql._copy_chunk(  # type: ignore[arg-type] # noqa: SLF001
        engine,
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        scope="FULL",
        checkpoint_id=UUID("33333333-3333-3333-3333-333333333333"),
        rows=[row],
        expected_previous=0,
        total_rows=1,
        statement_timeout_ms=remaining.__next__,
    )

    assert engine.connection.timeouts == ["400ms", "300ms", "200ms", "100ms"]
    assert engine.connection.transaction.committed is True
    assert engine.connection.transaction.rolled_back is False


def test_identity_promotion_recomputes_timeout_for_every_statement() -> None:
    class _Result:
        rowcount = 0

        def mappings(self) -> list[dict[str, object]]:
            return []

        def scalar_one(self) -> int:
            return 0

    class _Connection:
        def __init__(self) -> None:
            self.timeouts: list[str] = []

        def execute(self, statement: Any, parameters: Any = None) -> _Result:
            if "set_config('statement_timeout'" in str(statement):
                self.timeouts.append(parameters["timeout"])
            return _Result()

    connection = _Connection()
    remaining = iter((800, 700, 600, 500))

    metrics = recovery_sql.promote_identity_mappings(
        connection,
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        scope="FULL",
        statement_timeout_ms=remaining.__next__,
    )

    assert metrics.inserted == 0
    assert connection.timeouts == ["800ms", "700ms", "600ms", "500ms"]


@pytest.mark.parametrize("timeout", [True, 0, 300_001])
def test_copy_timeout_is_positive_and_capped(timeout: Any) -> None:
    with pytest.raises(ValueError, match="COPY statement_timeout_ms"):
        recovery_sql._copy_timeout(timeout)  # noqa: SLF001


@pytest.mark.parametrize("timeout", [True, 0, 1_800_001])
def test_promotion_statement_timeout_is_positive_and_capped(timeout: Any) -> None:
    with pytest.raises(ValueError, match="promotion statement_timeout_ms"):
        recovery_sql.promote_identity_mappings(
            object(),
            run_id=UUID("22222222-2222-2222-2222-222222222222"),
            scope="FULL",
            statement_timeout_ms=timeout,
        )


class _MappingResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> list[dict[str, Any]]:
        return self.rows


class _QuarantineConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.page_queries = 0
        self.batch_sizes: list[int] = []
        self.inserts: list[dict[str, Any]] = []

    def execute(self, statement: Any, parameters: Any = None) -> _MappingResult:
        sql = str(statement)
        if "FROM fd1_recovery_identity_stage s" in sql:
            self.page_queries += 1
            self.batch_sizes.append(int(parameters["batch_size"]))
            return _MappingResult(self.rows if self.page_queries == 1 else [])
        if "FROM fd1_recovery_identity_quarantine" in sql:
            return _MappingResult([])
        if "INSERT INTO fd1_recovery_identity_quarantine" in sql:
            self.inserts.extend(parameters)
            return _MappingResult([])
        raise AssertionError(sql)


def _quarantine_row(*, existing: bool) -> dict[str, Any]:
    mapping_id = UUID("44444444-4444-4444-4444-444444444444") if existing else None
    return {
        "staging_ordinal": 1 if existing else 2,
        "outcome": "CONFLICT" if existing else "UNRESOLVED",
        "reason_code": (
            "FINAL_NATURAL_KEY_CONFLICT"
            if existing
            else "SOURCE_DECLARED_UNRESOLVED"
        ),
        "record_checksum": "a" * 64,
        "matched_mapping_id": mapping_id,
        "existing_source_version_id": (
            UUID("11111111-1111-1111-1111-111111111111") if existing else None
        ),
        "existing_entity_type": "person" if existing else None,
        "existing_source_namespace": "retrosheet" if existing else None,
        "existing_external_id": "player-1" if existing else None,
        "existing_canonical_namespace": "chadwick_uuid" if existing else None,
        "existing_canonical_id": "canonical-1" if existing else None,
        "existing_source_row_number": 1 if existing else None,
        "existing_source_row_checksum": "b" * 64 if existing else None,
        "existing_confidence": "EXACT" if existing else None,
        "existing_valid_from": date(2021, 1, 1) if existing else None,
        "existing_valid_through": date(2025, 12, 31) if existing else None,
        "existing_evidence_logical_uri": (
            "open-data/chadwick/people.csv" if existing else None
        ),
        "existing_identity_checksum": "c" * 64 if existing else None,
    }


def test_quarantine_is_bounded_and_captures_existing_collision_payload() -> None:
    connection = _QuarantineConnection(
        [_quarantine_row(existing=True), _quarantine_row(existing=False)]
    )

    recovery_sql._write_quarantine(  # noqa: SLF001 - recovery invariant test
        connection,
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        scope="FULL",
    )

    assert connection.page_queries == 2
    assert connection.batch_sizes == [10_000, 10_000]
    assert len(connection.inserts) == 2
    conflict = connection.inserts[0]
    assert conflict["existing_mapping_id"] == UUID(
        "44444444-4444-4444-4444-444444444444"
    )
    assert json.loads(conflict["existing_payload"])["canonical_id"] == "canonical-1"
    assert connection.inserts[1]["existing_payload"] is None
