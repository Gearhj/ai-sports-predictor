from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.gen2.exchange.identity_bridge import source


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{number:012d}")


def _alias_row(**updates: Any) -> dict[str, Any]:
    authority = next(
        row
        for row in source.EXPECTED_SOURCE_VERSIONS
        if row.source_key == "CHADWICK_REGISTER_ARCHIVES"
    )
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    row: dict[str, Any] = {
        "recovery_id": source.EXPECTED_RECOVERY_ID,
        "scope": "FULL",
        "staging_ordinal": 672_341,
        "mapping_id": _uuid(1),
        "target_mapping_id": _uuid(1),
        "source_key": authority.source_key,
        "source_version_id": authority.source_version_id,
        "dataset_version": authority.dataset_version,
        "source_mode": authority.source_mode,
        "source_release_identity_checksum": authority.release_identity_checksum,
        "version_identity_checksum": authority.release_identity_checksum,
        "batch_number": 68,
        "batch_checksum": "a" * 64,
        "entity_type": "person",
        "source_namespace": "retrosheet",
        "external_id": "aardd001",
        "canonical_namespace": source.CANONICAL_PLAYER_NAMESPACE,
        "canonical_id": _uuid(101),
        "source_row_number": 250,
        "source_row_checksum": "b" * 64,
        "confidence": "EXACT",
        "valid_from": None,
        "valid_through": None,
        "evidence_logical_uri": "open-data/chadwick/people-6.csv",
        "mapping_basis": "EXPLICIT_IDENTIFIER_EXACT",
        "identity_checksum": "c" * 64,
        "mapping_identity_checksum": "c" * 64,
        "record_checksum": "d" * 64,
        "outcome": "INSERT",
        "reconciled_at": now,
        "stage_created_at": now,
        "mapping_created_at": now,
    }
    row.update(updates)
    return row


class _RowsResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _RowsResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _CapturingConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.statement: Any = None
        self.parameters: dict[str, Any] = {}

    def execute(
        self, statement: Any, parameters: dict[str, Any] | None = None
    ) -> _RowsResult:
        self.statement = statement
        self.parameters = dict(parameters or {})
        return _RowsResult(self.rows)


def _compiled(connection: _CapturingConnection) -> tuple[str, dict[str, Any]]:
    compiled = connection.statement.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


def test_raw_ids_are_exact_and_never_silently_normalized() -> None:
    assert source._validated_raw_ids(["AARDd001", "aaroh101"]) == (
        "AARDd001",
        "aaroh101",
    )
    with pytest.raises(source.IdentityBridgeInputError, match="exact printable"):
        source._validated_raw_ids([" aardd001"])
    with pytest.raises(source.IdentityBridgeInputError, match="exact printable"):
        source._validated_raw_ids(["aardd001\n"])
    with pytest.raises(source.IdentityBridgeInputError, match="must be unique"):
        source._validated_raw_ids(["aardd001", "aardd001"])
    with pytest.raises(source.IdentityBridgeInputError, match="individual IDs"):
        source._validated_raw_ids("aardd001")


def test_lookup_inputs_are_nonempty_and_hard_bounded() -> None:
    with pytest.raises(source.IdentityBridgeInputError, match="at least one"):
        source._validated_raw_ids([])
    over_bound = [f"player{index}" for index in range(source.MAX_LOOKUP_IDS + 1)]
    with pytest.raises(source.IdentityBridgeInputError, match="safety bound"):
        source._validated_raw_ids(over_bound)

    canonical_over_bound = [
        UUID(int=index + 1) for index in range(source.MAX_LOOKUP_IDS + 1)
    ]
    with pytest.raises(source.IdentityBridgeInputError, match="safety bound"):
        source._validated_canonical_ids(canonical_over_bound)


def test_canonical_uuid_inputs_normalize_only_uuid_representation() -> None:
    upper = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    normalized = source._validated_canonical_ids([upper])
    assert normalized == (UUID(upper),)
    assert str(normalized[0]) == upper.lower()

    with pytest.raises(source.IdentityBridgeInputError, match="must be unique"):
        source._validated_canonical_ids([upper, upper.lower()])
    with pytest.raises(source.IdentityBridgeInputError, match="valid UUIDs"):
        source._validated_canonical_ids(["not-a-uuid"])


def test_phase_one_is_exact_active_finalized_and_parameterized() -> None:
    connection = _CapturingConnection([_alias_row()])
    aliases = source._phase_one_retrosheet_aliases(connection, ["aardd001"])
    sql, compiled_parameters = _compiled(connection)

    assert len(aliases) == 1
    assert aliases[0].external_id == "aardd001"
    assert connection.parameters["raw_retrosheet_ids"] == ("aardd001",)
    assert connection.parameters["scope"] == "FULL"
    assert connection.parameters["recovery_id"] == source.EXPECTED_RECOVERY_ID
    assert (
        connection.parameters["manifest_checksum"]
        == source.RECOVERY_MANIFEST_CHECKSUM
    )
    assert "JOIN historical_source_versions" in sql
    assert "JOIN fd1_recovery_runs" in sql
    assert "open_data_identity_mappings.identity_checksum = " in sql
    assert "fd1_recovery_runs.active_stage IS NULL" in sql
    assert compiled_parameters["status_1"] == "FINALIZED"
    assert compiled_parameters["last_completed_stage_1"] == "FINALIZED"
    assert compiled_parameters["source_mode_1"] == "LIVE"
    assert compiled_parameters["source_namespace_1"] == "retrosheet"
    assert compiled_parameters["confidence_1"] == "EXACT"
    assert set(compiled_parameters["outcome_1"]) == {"INSERT", "REUSE"}


def test_phase_queries_exclude_unresolved_conflict_and_quarantine_rows() -> None:
    first_connection = _CapturingConnection([_alias_row()])
    source._phase_one_retrosheet_aliases(first_connection, ["aardd001"])
    first_sql, first_parameters = _compiled(first_connection)

    assert "fd1_recovery_identity_quarantine" not in first_sql
    assert "UNRESOLVED" not in first_parameters["outcome_1"]
    assert "CONFLICT" not in first_parameters["outcome_1"]

    canonical_id = _uuid(101)
    second_connection = _CapturingConnection([_alias_row()])
    source._phase_two_person_aliases(second_connection, [canonical_id])
    second_sql, second_parameters = _compiled(second_connection)

    assert "fd1_recovery_identity_quarantine" not in second_sql
    assert set(second_parameters["outcome_1"]) == {"INSERT", "REUSE"}
    assert set(second_parameters["confidence_1"]) == {"EXACT", "HIGH"}
    assert "UNRESOLVED" not in second_parameters["confidence_1"]
    assert "CONFLICT" not in second_parameters["confidence_1"]


def test_alias_normalization_preserves_exact_mapping_lineage() -> None:
    alias = source._alias_from_row(_alias_row(external_id="AARDd001"))

    assert alias.external_id == "AARDd001"
    assert alias.canonical_player_id == _uuid(101)
    assert isinstance(alias.canonical_player_id, UUID)
    assert alias.mapping_id == alias.target_mapping_id
    assert alias.mapping_basis == "EXPLICIT_IDENTIFIER_EXACT"
    assert alias.source_row_number == 250
    assert alias.source_row_checksum == "b" * 64
    assert alias.identity_checksum == "c" * 64
    assert alias.record_checksum == "d" * 64
    assert alias.as_dict()["canonical_player_id"] == str(_uuid(101))


def test_alias_contract_has_no_name_fields_or_name_based_basis() -> None:
    alias = source._alias_from_row(_alias_row())
    field_names = {field.name for field in fields(source.IdentityAlias)}

    assert {
        "player_name",
        "display_name",
        "first_name",
        "last_name",
        "birth_date",
    }.isdisjoint(field_names)
    assert alias.mapping_basis == "EXPLICIT_IDENTIFIER_EXACT"
    assert "NAME" not in alias.mapping_basis


@pytest.mark.parametrize(
    "namespace",
    source.SUPPORTED_PERSON_NAMESPACES,
)
def test_each_supported_namespace_is_optional_per_canonical_player(
    namespace: str,
) -> None:
    row = _alias_row(source_namespace=namespace, external_id=f"{namespace}-id")
    connection = _CapturingConnection([row])

    aliases = source._phase_two_person_aliases(connection, [_uuid(101)])
    _sql, parameters = _compiled(connection)

    assert len(aliases) == 1
    assert aliases[0].source_namespace == namespace
    assert set(parameters["source_namespace_1"]) == set(
        source.SUPPORTED_PERSON_NAMESPACES
    )
    assert connection.parameters["canonical_player_ids"] == (str(_uuid(101)),)


def test_source_namespace_and_release_are_both_authority_bound() -> None:
    with pytest.raises(source.IdentityBridgeSourceError, match="constraints changed"):
        source._alias_from_row(_alias_row(source_namespace="player_name"))

    with pytest.raises(source.IdentityBridgeSourceError, match="source release"):
        source._alias_from_row(_alias_row(version_identity_checksum="e" * 64))

    with pytest.raises(source.IdentityBridgeSourceError, match="source release"):
        source._alias_from_row(_alias_row(dataset_version="moving-latest"))


def test_source_version_id_cannot_be_rebound_to_another_registry_row() -> None:
    other = next(
        row
        for row in source.EXPECTED_SOURCE_VERSIONS
        if row.source_key == "SABR_LAHMAN_BOX_DOWNLOADS"
    )
    row = _alias_row(
        source_version_id=other.source_version_id,
        source_key=other.source_key,
        dataset_version=other.dataset_version,
        source_release_identity_checksum=other.release_identity_checksum,
        version_identity_checksum=other.release_identity_checksum,
    )
    alias = source._alias_from_row(row)
    assert alias.source_version_id == other.source_version_id
    assert alias.source_key == other.source_key

    with pytest.raises(source.IdentityBridgeSourceError, match="source release"):
        source._alias_from_row({**row, "source_key": "CHADWICK_REGISTER_ARCHIVES"})


def test_phase_one_rejects_nonunique_raw_to_canonical_mapping() -> None:
    first = _alias_row()
    second = _alias_row(mapping_id=_uuid(2), target_mapping_id=_uuid(2))
    connection = _CapturingConnection([first, second])

    with pytest.raises(source.IdentityBridgeSourceError, match="not uniquely mapped"):
        source._phase_one_retrosheet_aliases(connection, ["aardd001"])


def test_phase_two_enforces_canonical_coverage_and_mapping_uniqueness() -> None:
    with pytest.raises(source.IdentityBridgeSourceError, match="exactly cover"):
        source._phase_two_person_aliases(_CapturingConnection([]), [_uuid(101)])

    duplicate_connection = _CapturingConnection([_alias_row(), _alias_row()])
    with pytest.raises(source.IdentityBridgeSourceError, match="returned duplicates"):
        source._phase_two_person_aliases(duplicate_connection, [_uuid(101)])


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET value = 1",
        "DELETE FROM t",
        "WITH changed AS (DELETE FROM t RETURNING *) SELECT * FROM changed",
        "SELECT * FROM t FOR UPDATE",
        "SELECT * FROM t FOR KEY SHARE",
        "SELECT * INTO copied FROM t",
        "SELECT 1; DROP TABLE t",
    ],
)
def test_sql_allowlist_rejects_mutation_and_locking_reads(statement: str) -> None:
    assert source._statement_kind(statement) == "REJECT"


@pytest.mark.parametrize(
    ("statement", "kind"),
    [
        ("SELECT 1", "READ"),
        ("WITH values AS (SELECT 1) SELECT * FROM values", "READ"),
        ("SHOW transaction_read_only", "READ"),
        (" SET   TRANSACTION   READ   ONLY ", "CONTROL"),
    ],
)
def test_sql_allowlist_accepts_only_bounded_read_shapes(
    statement: str, kind: str
) -> None:
    assert source._statement_kind(statement) == kind


class _ScalarResult:
    def __init__(self, value: str) -> None:
        self.value = value

    def scalar_one(self) -> str:
        return self.value


class _FakeTransaction:
    def __init__(self) -> None:
        self.rollback_count = 0

    def rollback(self) -> None:
        self.rollback_count += 1


class _FakeConnection:
    def __init__(self, engine: _FakeEngine) -> None:
        self.engine = engine
        self.transaction = _FakeTransaction()
        self.closed = False
        self.cursor_commands: list[str] = []

    def begin(self) -> _FakeTransaction:
        return self.transaction

    def execute(self, statement: Any) -> _ScalarResult:
        if self.engine.guard is None:
            raise AssertionError("read-only SQL guard was not installed")
        cursor = SimpleNamespace(execute=self.cursor_commands.append)
        self.engine.guard(self, cursor, str(statement), None, None, False)
        return _ScalarResult("on")

    def close(self) -> None:
        self.closed = True


class _FakeEngine:
    def __init__(self) -> None:
        self.dialect = SimpleNamespace(name="postgresql")
        self.guard: Any = None
        self.connection = _FakeConnection(self)
        self.disposed = False

    def connect(self) -> _FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def test_read_only_context_rolls_back_and_removes_guard_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeEngine()
    removed: list[Any] = []

    def listen(target: _FakeEngine, name: str, callback: Any) -> None:
        assert target is engine
        assert name == "before_cursor_execute"
        target.guard = callback

    def remove(target: _FakeEngine, name: str, callback: Any) -> None:
        assert target is engine
        assert name == "before_cursor_execute"
        assert callback is target.guard
        removed.append(callback)
        target.guard = None

    monkeypatch.setattr(source, "get_engine", lambda _url=None: engine)
    monkeypatch.setattr(source.event, "listen", listen)
    monkeypatch.setattr(source.event, "remove", remove)

    ledger: Any = None
    with pytest.raises(LookupError, match="forced extraction failure"):
        with source._read_only_connection("postgresql://redacted") as (
            _connection,
            active_ledger,
        ):
            ledger = active_ledger
            assert active_ledger.transaction_read_only is True
            raise LookupError("forced extraction failure")

    assert ledger.transaction_rolled_back is True
    assert ledger.control_statement_count == 1
    assert ledger.read_statement_count == 1
    assert ledger.rejected_statement_count == 0
    assert engine.connection.transaction.rollback_count == 1
    assert engine.connection.closed is True
    assert engine.disposed is True
    assert len(removed) == 1
    assert engine.guard is None


def test_read_only_context_enforces_absolute_statement_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeEngine()

    def listen(target: _FakeEngine, _name: str, callback: Any) -> None:
        target.guard = callback

    def remove(target: _FakeEngine, _name: str, callback: Any) -> None:
        assert target.guard is callback
        target.guard = None

    monkeypatch.setattr(source, "get_engine", lambda _url=None: engine)
    monkeypatch.setattr(source.event, "listen", listen)
    monkeypatch.setattr(source.event, "remove", remove)

    now = [10.0]
    with source._read_only_connection(
        "postgresql://redacted",
        deadline_monotonic=20.0,
        clock=lambda: now[0],
    ) as (_connection, ledger):
        assert engine.connection.cursor_commands == [
            "SET LOCAL statement_timeout = 10000"
        ]
        assert ledger.control_statement_count == 2
        assert ledger.read_statement_count == 1
        now[0] = 20.0
        cursor = SimpleNamespace(execute=engine.connection.cursor_commands.append)
        with pytest.raises(source.IdentityBridgeSourceDeadline, match="deadline"):
            engine.guard(None, cursor, "SELECT 1", None, None, False)

    assert engine.connection.transaction.rollback_count == 1
    assert engine.connection.closed is True
    assert engine.disposed is True
    assert engine.guard is None


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf")])
def test_read_only_context_rejects_invalid_deadline(deadline: Any) -> None:
    with pytest.raises(source.IdentityBridgeInputError, match="deadline_monotonic"):
        with source._read_only_connection(None, deadline_monotonic=deadline):
            raise AssertionError("invalid deadline reached the database boundary")


def _boundary() -> source.ReadOnlyDatabaseBoundary:
    return source.ReadOnlyDatabaseBoundary(
        backend="postgresql",
        transaction_read_only=True,
        transaction_rolled_back=True,
        read_statement_count=3,
        control_statement_count=1,
        rejected_statement_count=0,
    )


def _snapshot(
    *,
    digest: str = "a" * 64,
    relation: str = "forecasts",
    offset_minutes: int = 0,
) -> source.OperationalSnapshot:
    return source.OperationalSnapshot(
        captured_at=datetime(2026, 8, 15, tzinfo=timezone.utc)
        + timedelta(minutes=offset_minutes),
        relations=(
            source.OperationalRelationSnapshot(
                relation=relation,
                row_count=42,
                digest=digest,
            ),
        ),
        database_boundary=_boundary(),
    )


def test_operational_snapshot_comparison_reports_only_changed_relations() -> None:
    before = _snapshot()
    unchanged = source.compare_operational_snapshots(
        before, _snapshot(offset_minutes=1)
    )
    changed = source.compare_operational_snapshots(
        before, _snapshot(digest="b" * 64, offset_minutes=2)
    )

    assert unchanged.unchanged is True
    assert unchanged.changed_relations == ()
    assert changed.unchanged is False
    assert changed.changed_relations == ("forecasts",)


def test_operational_snapshot_comparison_rejects_relation_set_mismatch() -> None:
    with pytest.raises(source.IdentityBridgeInputError, match="relation sets differ"):
        source.compare_operational_snapshots(
            _snapshot(relation="forecasts"),
            _snapshot(relation="provider_request_ledger"),
        )
