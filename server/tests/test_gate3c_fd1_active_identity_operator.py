from __future__ import annotations

from contextlib import nullcontext
import importlib.util
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest


OPERATOR_PATH = (
    Path(__file__).resolve().parents[2]
    / ".runtime"
    / "gate3c-fd1"
    / "active_identity_actual.py"
)


def _load_operator():
    spec = importlib.util.spec_from_file_location(
        "gate3c_fd1_active_identity_actual_test_module", OPERATOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_active_identity_operator_sql_boundary_is_select_only() -> None:
    operator = _load_operator()

    assert operator._statement_kind(" SET   TRANSACTION READ ONLY ") == "CONTROL"
    assert operator._statement_kind("SELECT id FROM evidence") == "READ"
    assert operator._statement_kind("SELECT id FROM evidence;") == "READ"
    assert operator._statement_kind("WITH selected AS (SELECT 1) SELECT * FROM selected") == (
        "REJECT"
    )
    assert operator._statement_kind("INSERT INTO evidence VALUES (1)") == "REJECT"
    assert operator._statement_kind("SELECT 1; DELETE FROM evidence") == "REJECT"


def test_active_identity_operator_network_guard_rejects_external_resolution() -> None:
    operator = _load_operator()

    with operator._provider_free_network_guard() as ledger:
        local = socket.getaddrinfo("localhost", 5432)
        assert local
        with pytest.raises(
            operator.ActiveIdentityOperatorError, match="external network"
        ):
            socket.getaddrinfo("example.invalid", 443)

    assert ledger.loopback_connections_permitted == 1
    assert ledger.blocked_external_attempts == 1
    assert ledger.external_requests == 0


def test_active_identity_operator_selects_exact_five_pinned_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator = _load_operator()
    raw_root = tmp_path / "raw"
    extraction_root = raw_root / "retro"
    extraction_root.mkdir(parents=True)
    monkeypatch.setattr(operator, "RAW_ROOT", raw_root)
    artifact = SimpleNamespace(
        source_key=operator.RETROSHEET_SOURCE_KEY,
        object_key="retro-object",
    )
    source = SimpleNamespace(dataset_version="retro-release")
    prepared = SimpleNamespace(
        extraction_artifacts=(artifact,),
        sources={operator.RETROSHEET_SOURCE_KEY: source},
    )
    publication = SimpleNamespace(RETROSHEET_CSV_OBJECT="retro-object")

    def member(selected_artifact, table):
        assert selected_artifact is artifact
        path = extraction_root / table
        path.write_text(f"{table}\n", encoding="utf-8")
        return SimpleNamespace(table=table, path=path)

    def source_spec(selected_artifact, selected_member, **kwargs):
        assert selected_artifact is artifact
        return operator.SourceFileSpec(
            path=selected_member.path,
            source_key=operator.RETROSHEET_SOURCE_KEY,
            source_release=kwargs["source_release"],
            logical_table=kwargs["logical_table"],
            expected_sha256="a" * 64,
            expected_content_length=1,
        )

    publication._member = member
    publication._source_spec_from_member = source_spec

    specs = operator._identity_source_specs(publication, prepared)

    assert tuple(spec.logical_table for spec in specs) == operator.IDENTITY_TABLES
    assert all(spec.expected_sha256 == "a" * 64 for spec in specs)
    assert [operator._spec_ledger(spec)["raw_logical_uri"] for spec in specs] == [
        f"retro/{table}" for table in operator.IDENTITY_TABLES
    ]


def test_active_identity_operator_run_emits_full_report_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator = _load_operator()
    publication = SimpleNamespace(_operator_code_revision=lambda: "a" * 64)
    prepared = SimpleNamespace(identity_import=SimpleNamespace(people=object()))
    database = operator.ReadOnlyDatabaseLedger(
        backend="postgresql",
        read_statements=5,
        control_statements=1,
        transaction_read_only=True,
        transaction_rolled_back=True,
    )
    network = operator.NetworkLedger()
    specs = (object(),)
    report_payload = {
        "schema_version": operator.FD1_IDENTITY_COVERAGE_SCHEMA_VERSION,
        "full": "report",
    }
    report = SimpleNamespace(
        schema_version=operator.FD1_IDENTITY_COVERAGE_SCHEMA_VERSION,
        name_based_match_count=0,
        primary_unique_meets_threshold=True,
        primary_reference_meets_threshold=False,
        primary_meets_threshold=False,
        derivation_checksum="b" * 64,
        as_dict=lambda: report_payload,
    )
    captured = {}

    monkeypatch.setattr(operator, "_load_publication_operator", lambda: publication)
    monkeypatch.setattr(operator, "_active_operator_revision", lambda value: "c" * 64)
    monkeypatch.setattr(
        operator,
        "_provider_free_network_guard",
        lambda: nullcontext(network),
    )
    monkeypatch.setattr(
        operator,
        "_read_only_prepared_inputs",
        lambda value: nullcontext((prepared, database)),
    )
    monkeypatch.setattr(operator, "_identity_source_specs", lambda *args: specs)
    monkeypatch.setattr(
        operator,
        "_input_document",
        lambda *args: {"active_identity_source_files": ["pinned"]},
    )
    monkeypatch.setattr(operator, "_verify_report_sources", lambda *args: None)
    monkeypatch.setattr(
        operator,
        "build_active_identity_coverage",
        lambda crosswalk, selected_specs, **kwargs: report,
    )

    def publish(value, document):
        assert value is publication
        captured.update(document)
        return tmp_path / "evidence.json", "d" * 64, 123, False

    monkeypatch.setattr(operator, "_publish_document", publish)
    monkeypatch.setattr(operator, "_relative", lambda path: "evidence.json")

    result = operator.run(primary_threshold=0.995)

    assert result["success"] is False
    assert result["database_mutations"] == 0
    assert result["network_requests"] == 0
    assert captured["active_identity_coverage_report"] is report_payload
    assert captured["execution_boundary"]["database"]["transaction_read_only"] is True
    assert captured["execution_boundary"]["database"]["transaction_rolled_back"] is True
    assert captured["threshold_decision"]["primary_meets_threshold"] is False


def test_active_identity_operator_artifact_is_content_addressed_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator = _load_operator()
    publication = operator._load_publication_operator()
    runtime_root = tmp_path / "runtime"
    (runtime_root / "artifacts").mkdir(parents=True)
    monkeypatch.setattr(operator, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(publication, "RUNTIME_ROOT", runtime_root)
    document = {"schema_version": "test", "report": {"exact": True}}

    first_path, first_sha, first_bytes, first_reused = operator._publish_document(
        publication, document
    )
    original = first_path.read_bytes()
    second_path, second_sha, second_bytes, second_reused = operator._publish_document(
        publication, document
    )

    assert first_reused is False
    assert second_reused is True
    assert second_path == first_path
    assert second_sha == first_sha
    assert second_bytes == first_bytes
    assert second_path.name == f"active-identity-actual-sha256-{first_sha}.json"
    assert second_path.read_bytes() == original
