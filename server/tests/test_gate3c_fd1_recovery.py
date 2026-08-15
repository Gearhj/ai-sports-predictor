from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

import app.gen2.fd1_recovery as recovery
from app.gen2.fd1_recovery import (
    FD1_RECOVERY_FINALIZER_CODE_REVISION_INPUTS,
    FD1RecoveryError,
    FD1RecoveryOptions,
)
from app.gen2.fd1_recovery_models import RECOVERY_STAGES
from app.gen2.fd1_recovery_sql import IdentityPromotionMetrics
from app.gen2.fd1_warehouse import FD1_CODE_REVISION_INPUTS


@dataclass
class _UpdateResult:
    rowcount: int


class _QueryResult:
    def __init__(self, *, scalar: object = None, rows: object = None) -> None:
        self._scalar = scalar
        self._rows = rows

    def scalar_one(self) -> object:
        return self._scalar

    def mappings(self) -> _QueryResult:
        return self

    def all(self) -> object:
        return self._rows


class _ConnectionContext:
    def __init__(self, results: list[_QueryResult]) -> None:
        self.results = list(results)

    def __enter__(self) -> _ConnectionContext:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, *_args: object, **_kwargs: object) -> _QueryResult:
        return self.results.pop(0)


class _QueryEngine:
    def __init__(self, results: list[_QueryResult]) -> None:
        self.connection = _ConnectionContext(results)

    def connect(self) -> _ConnectionContext:
        return self.connection


class _ProgressRecorder:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []
        self.stop_checks = 0

    def update(self, **values: object) -> None:
        self.updates.append(values)

    def raise_if_stopped(self) -> None:
        self.stop_checks += 1

    def stop_safely(self, reason: str) -> None:
        raise RuntimeError(reason)


class _Transaction:
    def __init__(self) -> None:
        self.is_active = True
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1
        self.is_active = False


class _PromotionConnection:
    def __init__(self) -> None:
        self.transaction = _Transaction()
        self.closed = False
        self.statements = 0

    def begin(self) -> _Transaction:
        return self.transaction

    def execute(self, *_args: object, **_kwargs: object) -> _QueryResult:
        self.statements += 1
        return _QueryResult(scalar=1)

    def close(self) -> None:
        self.closed = True


class _PromotionEngine:
    def __init__(self) -> None:
        self.connection = _PromotionConnection()

    def connect(self) -> _PromotionConnection:
        return self.connection


def test_options_enforce_operational_caps_and_modes(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    with pytest.raises(ValueError, match="between 1 and 120"):
        FD1RecoveryOptions(manifest_path=manifest, max_runtime_minutes=121)
    with pytest.raises(ValueError, match="between 1 and 30"):
        FD1RecoveryOptions(manifest_path=manifest, heartbeat_seconds=31)
    with pytest.raises(ValueError, match="mutually exclusive"):
        FD1RecoveryOptions(
            manifest_path=manifest,
            validate_only=True,
            sample_rehearsal=True,
        )


def test_finalizer_revision_includes_the_frozen_preparation_boundary() -> None:
    assert {
        "alembic/versions/20260814_0013_gate3c_fd1_partition_families.py",
        "alembic/versions/20260814_0014_fd1_identity_mapping_natural_key.py",
        "alembic/versions/20260815_0015_fd1_recovery_finalization.py",
        "app/gen2/fd1_identity.py",
        "app/gen2/fd1_registry_sync.py",
        "app/gen2/open_data_acquisition.py",
        "app/gen2/open_data_registry.py",
        "app/gen2/retrosheet.py",
        "app/historical/checkpoints.py",
        "scripts/prepare_fd1_recovery.py",
    }.issubset(FD1_RECOVERY_FINALIZER_CODE_REVISION_INPUTS)
    assert set(FD1_CODE_REVISION_INPUTS).issubset(
        FD1_RECOVERY_FINALIZER_CODE_REVISION_INPUTS
    )
    assert len(recovery.build_fd1_recovery_finalizer_revision()) == 64
    assert {
        "provider_calls",
        "network_requests",
        "provider_transports_constructed",
    }.issubset(recovery.FD1RecoveryResult.__dataclass_fields__)


def test_cli_keeps_provider_capable_jobs_out_of_recovery_import_graph() -> None:
    cli_path = Path(recovery.__file__).resolve().parents[1] / "cli.py"
    module = ast.parse(cli_path.read_text(encoding="utf-8"))
    top_level = [
        node.module
        for node in module.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "app.jobs" not in top_level


def test_publication_progress_adapter_consumes_the_frozen_object_api() -> None:
    progress = _ProgressRecorder()
    update = SimpleNamespace(
        stage="PLAN_PARTITIONS",
        completed=17,
        total=329,
        current="family=batting/season=2021/part.parquet",
    )

    recovery._record_publication_progress(progress, update)  # noqa: SLF001

    assert progress.updates == [
        {
            "database_stage": (
                "publication:PLAN_PARTITIONS:17/329:"
                "family=batting/season=2021/part.parquet"
            ),
        }
    ]
    assert progress.stop_checks == 1


def test_validation_progress_adapter_checks_the_hard_stop() -> None:
    progress = _ProgressRecorder()

    recovery._record_validation_progress(  # noqa: SLF001
        progress,
        SimpleNamespace(files_validated=9, files_total=329),
    )

    assert progress.stop_checks == 1
    assert progress.updates == [{"files_validated": 9, "files_total": 329}]


def test_publication_progress_adapter_fails_closed_on_bad_bounds() -> None:
    with pytest.raises(FD1RecoveryError, match="bounds"):
        recovery._record_publication_progress(  # noqa: SLF001
            _ProgressRecorder(),
            SimpleNamespace(stage="STAGE_PUBLICATION", completed=2, total=1),
        )


def test_full_recovery_requires_all_six_rehearsal_checkpoints() -> None:
    run_id = uuid4()
    engine = _QueryEngine(
        [
            _QueryResult(scalar=object()),
            _QueryResult(
                rows=[
                    {
                        "stage_name": stage,
                        "progress": (
                            {"transaction_rolled_back": True}
                            if stage == "FINALIZED"
                            else {}
                        ),
                    }
                    for stage in RECOVERY_STAGES[:-1]
                ]
            ),
        ]
    )

    with pytest.raises(FD1RecoveryError, match="all six"):
        recovery._require_successful_rehearsal(  # type: ignore[arg-type] # noqa: SLF001
            engine, run_id=run_id
        )


def test_full_recovery_accepts_only_durable_rehearsal_rollback_evidence() -> None:
    rows = [
        {
            "stage_name": stage,
            "progress": (
                {"transaction_rolled_back": True} if stage == "FINALIZED" else {}
            ),
        }
        for stage in RECOVERY_STAGES
    ]
    engine = _QueryEngine([_QueryResult(scalar=object()), _QueryResult(rows=rows)])

    recovery._require_successful_rehearsal(  # type: ignore[arg-type] # noqa: SLF001
        engine,
        run_id=uuid4(),
    )


def test_atomic_updates_must_affect_exactly_one_row() -> None:
    recovery._expect_one_updated(  # noqa: SLF001
        _UpdateResult(rowcount=1), "atomic recovery run finalization"
    )
    with pytest.raises(FD1RecoveryError, match="0 rows instead of one"):
        recovery._expect_one_updated(  # noqa: SLF001
            _UpdateResult(rowcount=0), "atomic recovery run finalization"
        )


def test_rehearsal_promotion_uses_explicit_opt_in_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _PromotionEngine()
    progress = _ProgressRecorder()
    calls: list[dict[str, object]] = []
    identity_calls: list[dict[str, object]] = []
    metrics = IdentityPromotionMetrics(
        inserted=4,
        reused=3,
        unresolved=2,
        source_declared_conflicts=1,
        blocking_conflicts=0,
        promotion_seconds=0.1,
    )

    def promote_publication(*_args: object, **kwargs: object) -> dict[str, int]:
        calls.append(dict(kwargs))
        return {"targets": 7}

    def promote_identity(*_args: object, **kwargs: object) -> IdentityPromotionMetrics:
        identity_calls.append(dict(kwargs))
        return metrics

    monkeypatch.setattr(recovery, "_promote_publication", promote_publication)
    monkeypatch.setattr(recovery, "promote_identity_mappings", promote_identity)
    monkeypatch.setattr(recovery, "_assert_rehearsal_rollback", lambda *_a, **_k: None)

    result, elapsed = recovery._run_rehearsal_promotion(  # type: ignore[arg-type] # noqa: SLF001
        engine,
        run_id=uuid4(),
        scope="REHEARSAL",
        progress=progress,
        deadline_at=recovery.monotonic() + 60.0,
    )

    assert result == metrics
    assert elapsed >= 0.0
    assert calls[0]["allow_rehearsal_rollback"] is True
    assert calls[0]["scope"] == "REHEARSAL"
    assert callable(identity_calls[0]["statement_timeout_ms"])
    assert engine.connection.transaction.rollbacks == 1
    assert engine.connection.closed is True
    assert progress.stop_checks == 3


def test_statement_timeout_is_capped_by_remaining_recovery_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress = _ProgressRecorder()
    monkeypatch.setattr(recovery, "monotonic", lambda: 100.0)

    assert recovery._remaining_statement_timeout_ms(  # noqa: SLF001
        112.5,
        progress=progress,  # type: ignore[arg-type]
        maximum_ms=20_000,
    ) == 12_500
    assert recovery._remaining_statement_timeout_ms(  # noqa: SLF001
        200.0,
        progress=progress,  # type: ignore[arg-type]
        maximum_ms=20_000,
    ) == 20_000
    with pytest.raises(RuntimeError, match="HARD_DEADLINE_REACHED"):
        recovery._remaining_statement_timeout_ms(  # noqa: SLF001
            100.0,
            progress=progress,  # type: ignore[arg-type]
            maximum_ms=20_000,
        )


def test_postgresql_query_cancel_becomes_a_resumable_safe_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress = _ProgressRecorder()
    error = SimpleNamespace(orig=SimpleNamespace(sqlstate="57014"))
    monkeypatch.setattr(recovery, "monotonic", lambda: 120.0)

    with pytest.raises(RuntimeError, match="HARD_DEADLINE_REACHED"):
        recovery._stop_on_postgresql_query_cancel(  # type: ignore[arg-type] # noqa: SLF001
            error,
            progress=progress,  # type: ignore[arg-type]
            deadline_at=120.5,
        )

    with pytest.raises(RuntimeError, match="DATABASE_STATEMENT_TIMEOUT"):
        recovery._stop_on_postgresql_query_cancel(  # type: ignore[arg-type] # noqa: SLF001
            SimpleNamespace(sqlstate="57014"),
            progress=progress,  # type: ignore[arg-type]
            deadline_at=180.0,
        )

    with pytest.raises(RuntimeError, match="DATABASE_STATEMENT_TIMEOUT"):
        recovery._stop_on_postgresql_query_cancel(  # type: ignore[arg-type] # noqa: SLF001
            SimpleNamespace(diag=SimpleNamespace(sqlstate="57014")),
            progress=progress,  # type: ignore[arg-type]
            deadline_at=180.0,
        )

    recovery._stop_on_postgresql_query_cancel(  # type: ignore[arg-type] # noqa: SLF001
        SimpleNamespace(sqlstate="23505"),
        progress=progress,  # type: ignore[arg-type]
        deadline_at=180.0,
    )


def test_publication_staging_receives_a_remaining_deadline_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import app.gen2.fd1_recovery_publication as publication

    progress = _ProgressRecorder()
    clock = iter((100.0, 100.0, 101.0))
    observed: dict[str, object] = {}

    def stage(*_args: object, **kwargs: object) -> dict[str, int]:
        timeout = kwargs["statement_timeout_ms"]
        assert callable(timeout)
        observed["timeout_ms"] = timeout()
        return {"targets": 1}

    monkeypatch.setattr(recovery, "monotonic", clock.__next__)
    monkeypatch.setattr(recovery, "_trusted_ledger_path", lambda _manifest: tmp_path)
    monkeypatch.setattr(publication, "stage_recovery_publication", stage)

    result = recovery._stage_publication(  # type: ignore[arg-type] # noqa: SLF001
        object(),
        manifest=object(),
        manifest_path=tmp_path / "manifest.json",
        store_root=tmp_path,
        scope="FULL",
        rehearsal=False,
        progress=progress,  # type: ignore[arg-type]
        deadline_at=105.0,
    )

    assert observed["timeout_ms"] == 5_000
    assert result["targets"] == 1
    assert result["partition_registration_seconds"] == 1.0


def test_publication_promotion_receives_a_remaining_deadline_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.gen2.fd1_recovery_publication as publication

    progress = _ProgressRecorder()
    observed: dict[str, object] = {}

    def promote(*_args: object, **kwargs: object) -> dict[str, int]:
        timeout = kwargs["statement_timeout_ms"]
        assert callable(timeout)
        observed["timeout_ms"] = timeout()
        return {"targets": 2}

    monkeypatch.setattr(recovery, "monotonic", lambda: 100.0)
    monkeypatch.setattr(publication, "promote_recovery_publication", promote)

    result = recovery._promote_publication(  # type: ignore[arg-type] # noqa: SLF001
        object(),
        run_id=uuid4(),
        scope="FULL",
        progress=progress,  # type: ignore[arg-type]
        deadline_at=104.0,
    )

    assert observed["timeout_ms"] == 4_000
    assert result == {"targets": 2}


def test_database_metrics_are_scoped_and_listeners_are_removed() -> None:
    engine = create_engine("sqlite://")
    metrics = recovery._RecoveryDatabaseMetrics(engine)  # noqa: SLF001
    metrics.start()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1")).scalar_one()
    measured = metrics.as_dict()
    metrics.close()

    assert measured["query_count"] == 1
    assert measured["peak_active_connections"] == 1
    assert measured["active_connections_at_snapshot"] == 0
    with engine.connect() as connection:
        connection.execute(text("SELECT 2")).scalar_one()
    assert metrics.as_dict()["query_count"] == 1
    engine.dispose()


def test_heartbeat_locks_checkpoint_before_run_row() -> None:
    class _Result:
        rowcount = 1

    class _Connection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: object, _parameters: object) -> _Result:
            self.statements.append(str(statement))
            return _Result()

    class _Begin:
        def __init__(self, connection: _Connection) -> None:
            self.connection = connection

        def __enter__(self) -> _Connection:
            return self.connection

        def __exit__(self, *_exc: object) -> None:
            return None

    class _Engine:
        def __init__(self) -> None:
            self.connection = _Connection()

        def begin(self) -> _Begin:
            return _Begin(self.connection)

    engine = _Engine()
    sink = recovery._heartbeat_sink(  # type: ignore[arg-type] # noqa: SLF001
        engine,
        run_id=uuid4(),
        scope="FULL",
        lease_owner="worker",
    )

    sink({"database_stage": "publication"})

    assert "UPDATE fd1_recovery_stage_checkpoints" in engine.connection.statements[0]
    assert "UPDATE fd1_recovery_runs" in engine.connection.statements[1]


def test_rehearsal_output_must_bind_explicit_rollback() -> None:
    with pytest.raises(FD1RecoveryError, match="rollback evidence"):
        recovery._promotion_metrics_from_rehearsal_output(  # noqa: SLF001
            {
                "inserted": 1,
                "reused": 2,
                "unresolved": 0,
                "source_declared_conflicts": 0,
                "blocking_conflicts": 0,
                "promotion_seconds": 0.2,
                "transaction_rolled_back": False,
            }
        )


def test_reconciliation_resume_filters_persisted_explain_evidence() -> None:
    output = {
        "total_rows": 10,
        "planned_insert": 4,
        "exact_reuse": 3,
        "unresolved": 2,
        "source_declared_conflicts": 1,
        "blocking_conflicts": 0,
        "quarantine_rows": 3,
        "reconciliation_seconds": 0.4,
        "conflict_query_seconds": 0.1,
        "explain_evidence": {"population_limit": 10_000},
    }

    metrics = recovery._reconciliation_metrics_from_output(output)  # noqa: SLF001

    assert metrics.total_rows == 10
    assert metrics.blocking_conflicts == 0


def test_explain_json_normalization_is_strict() -> None:
    document = recovery._normalize_explain_document(  # noqa: SLF001
        '[{"Plan":{"Node Type":"Index Scan","Index Name":"ix_identity",'
        '"Shared Hit Blocks":3},"Planning Time":0.1,"Execution Time":0.2}]'
    )
    assert document["Planning Time"] == 0.1
    assert recovery._explain_plan_summary(document) == {  # noqa: SLF001
        "planning_time_ms": 0.1,
        "execution_time_ms": 0.2,
        "index_names": ["ix_identity"],
        "node_types": ["Index Scan"],
        "buffer_blocks": {"Shared Hit Blocks": 3},
    }
    with pytest.raises(FD1RecoveryError, match="no JSON plan"):
        recovery._normalize_explain_document([])  # noqa: SLF001
