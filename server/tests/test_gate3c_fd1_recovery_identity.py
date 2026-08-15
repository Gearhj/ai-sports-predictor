from __future__ import annotations

from dataclasses import replace
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid5

import pytest

from app.gen2.fd1_identity import RegistryIdentityMappingBatch
from app.gen2.fd1_recovery_identity import (
    FD1RecoveryIdentityError,
    IDENTITY_SPOOL_COLUMNS,
    IDENTITY_SPOOL_SCHEMA_CHECKSUM,
    IDENTITY_TARGET_UUID_NAMESPACE,
    IdentitySourceRelease,
    iter_fd1_identity_mapping_batches,
    iter_identity_spool_records,
    load_identity_spool_binding,
    read_identity_spool_chunk,
    write_identity_spool,
)
from app.gen2.open_data_registry import (
    OpenDataIdentityMappingSpec,
    build_identity_mapping_identity,
)


SOURCE_A = IdentitySourceRelease(
    source_key="TEST_IDENTITY_A",
    source_version_id="11111111-1111-4111-8111-111111111111",
    source_release_identity_checksum="a" * 64,
)
SOURCE_B = IdentitySourceRelease(
    source_key="TEST_IDENTITY_B",
    source_version_id="22222222-2222-4222-8222-222222222222",
    source_release_identity_checksum="b" * 64,
)


def _spec_payload(spec: OpenDataIdentityMappingSpec) -> dict[str, object]:
    return {
        "source_key": spec.source_key,
        "entity_type": spec.entity_type,
        "source_namespace": spec.source_namespace,
        "external_id": spec.external_id,
        "canonical_namespace": spec.canonical_namespace,
        "canonical_id": spec.canonical_id,
        "source_row_number": spec.source_row_number,
        "source_row_checksum": spec.source_row_checksum,
        "confidence": spec.confidence,
        "evidence_logical_uri": spec.evidence_logical_uri,
        "valid_from": spec.valid_from.isoformat() if spec.valid_from else None,
        "valid_through": spec.valid_through.isoformat() if spec.valid_through else None,
    }


def _batch(number: int, *specs: OpenDataIdentityMappingSpec) -> RegistryIdentityMappingBatch:
    payload = [_spec_payload(spec) for spec in specs]
    checksum = sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return RegistryIdentityMappingBatch(number, tuple(specs), checksum)


def _spec(
    index: int,
    *,
    source: str = SOURCE_A.source_key,
    confidence: str = "EXACT",
) -> OpenDataIdentityMappingSpec:
    canonical_id = None if confidence in {"UNRESOLVED", "CONFLICT"} else f"canon-{index}"
    return OpenDataIdentityMappingSpec(
        source_key=source,
        entity_type="person" if source == SOURCE_A.source_key else "team",
        source_namespace="test_external",
        external_id=f"external-{index}",
        canonical_namespace="test_canonical",
        canonical_id=canonical_id,
        source_row_number=index,
        source_row_checksum=f"{index % 16:x}" * 64,
        confidence=confidence,
        evidence_logical_uri=f"raw/test/source-{index}.csv",
        valid_from=date(2025, 1, 1) if source == SOURCE_B.source_key else None,
        valid_through=date(2025, 12, 31) if source == SOURCE_B.source_key else None,
    )


def test_spool_is_deterministic_content_addressed_and_source_bound(
    tmp_path: Path,
) -> None:
    batches = (
        _batch(1, _spec(1), _spec(2, confidence="UNRESOLVED")),
        _batch(
            1,
            _spec(3, source=SOURCE_B.source_key),
            _spec(4, source=SOURCE_B.source_key, confidence="CONFLICT"),
        ),
    )
    progress = []
    first = write_identity_spool(
        batches,
        source_releases=(SOURCE_A, SOURCE_B),
        output_directory=tmp_path / "first",
        expected_row_count=4,
        min_free_bytes=0,
        progress_callback=progress.append,
    )
    second = write_identity_spool(
        batches,
        source_releases=(SOURCE_A, SOURCE_B),
        output_directory=tmp_path / "second",
        expected_row_count=4,
        min_free_bytes=0,
    )

    assert first.sha256 == second.sha256
    assert first.path.read_bytes() == second.path.read_bytes()
    assert first.path.name == f"fd1-identity-spool-sha256-{first.sha256}.csv"
    assert first.schema_checksum == IDENTITY_SPOOL_SCHEMA_CHECKSUM
    assert first.row_count == 4
    assert first.batch_count == 2
    assert first.source_keys == (SOURCE_A.source_key, SOURCE_B.source_key)
    assert progress[0].phase == "BUILD"
    assert progress[-1].phase == "COMPLETE"
    assert progress[-1].rows_processed == 4

    verified = load_identity_spool_binding(
        first.path,
        expected_sha256=first.sha256,
        expected_byte_count=first.byte_count,
        expected_row_count=first.row_count,
        source_releases=(SOURCE_A, SOURCE_B),
    )
    assert verified == first

    rows = list(iter_identity_spool_records(first.path))
    assert all(tuple(row) == IDENTITY_SPOOL_COLUMNS for row in rows)
    assert "run_id" not in rows[0]
    assert rows[0]["staging_ordinal"] == "1"
    assert rows[0]["mapping_basis"] == "EXPLICIT_IDENTIFIER_EXACT"
    assert rows[1]["canonical_id"] == ""
    assert rows[1]["mapping_basis"] == "UNRESOLVED_IDENTIFIER_EVIDENCE"
    assert rows[3]["canonical_id"] == ""
    assert rows[3]["mapping_basis"] == "CONFLICTING_IDENTIFIER_EVIDENCE"
    assert rows[0]["identity_checksum"] == build_identity_mapping_identity(
        source_version_identity_checksum=(
            SOURCE_A.source_release_identity_checksum
        ),
        spec=batches[0].specs[0],
    )
    assert rows[0]["target_mapping_id"] == str(
        uuid5(IDENTITY_TARGET_UUID_NAMESPACE, rows[0]["identity_checksum"])
    )


def test_chunk_cursor_resumes_at_an_exact_line_boundary(tmp_path: Path) -> None:
    batches = (
        _batch(1, _spec(1), _spec(2)),
        _batch(2, _spec(3), _spec(4)),
        _batch(3, _spec(5)),
    )
    binding = write_identity_spool(
        batches,
        source_releases=(SOURCE_A,),
        output_directory=tmp_path,
        min_free_bytes=0,
    )

    first = read_identity_spool_chunk(binding.path, max_rows=2)
    assert [row["staging_ordinal"] for row in first.records] == ["1", "2"]
    assert first.start_byte_offset == binding.data_byte_offset
    assert first.next_ordinal == 3
    assert not first.eof

    second = read_identity_spool_chunk(
        binding.path,
        start_ordinal=first.next_ordinal,
        start_byte_offset=first.next_byte_offset,
        max_rows=2,
    )
    third = read_identity_spool_chunk(
        binding.path,
        start_ordinal=second.next_ordinal,
        start_byte_offset=second.next_byte_offset,
        max_rows=2,
    )
    assert [row["staging_ordinal"] for row in second.records] == ["3", "4"]
    assert [row["staging_ordinal"] for row in third.records] == ["5"]
    assert third.eof
    assert third.next_byte_offset == binding.byte_count

    resumed = list(
        iter_identity_spool_records(
            binding.path,
            start_ordinal=3,
            start_byte_offset=first.next_byte_offset,
        )
    )
    assert [row["staging_ordinal"] for row in resumed] == ["3", "4", "5"]
    with pytest.raises(FD1RecoveryIdentityError, match="record boundary"):
        read_identity_spool_chunk(
            binding.path,
            start_ordinal=3,
            start_byte_offset=first.next_byte_offset + 1,
        )


def test_strict_validation_rejects_changed_rows_headers_and_registry_context(
    tmp_path: Path,
) -> None:
    binding = write_identity_spool(
        (_batch(1, _spec(1)),),
        source_releases=(SOURCE_A,),
        output_directory=tmp_path,
        min_free_bytes=0,
    )
    original = binding.path.read_bytes()
    binding.path.write_bytes(original.replace(b"external-1", b"external-x"))
    with pytest.raises(FD1RecoveryIdentityError, match="checksum is inconsistent"):
        load_identity_spool_binding(binding.path)

    binding.path.write_bytes(original.replace(b"staging_ordinal", b"wrong___ordinal"))
    with pytest.raises(FD1RecoveryIdentityError, match="header/schema"):
        load_identity_spool_binding(binding.path)

    binding.path.write_bytes(original)
    wrong_release = replace(SOURCE_A, source_release_identity_checksum="c" * 64)
    with pytest.raises(FD1RecoveryIdentityError, match="registry context"):
        load_identity_spool_binding(
            binding.path,
            source_releases=(wrong_release,),
        )


def test_spool_fails_before_iteration_when_free_space_reserve_is_breached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    consumed = False

    def batches():
        nonlocal consumed
        consumed = True
        yield _batch(1, _spec(1))

    monkeypatch.setattr(
        "app.gen2.fd1_recovery_identity.shutil.disk_usage",
        lambda path: SimpleNamespace(total=10, used=9, free=1),
    )
    with pytest.raises(FD1RecoveryIdentityError, match="reserve breached"):
        write_identity_spool(
            batches(),
            source_releases=(SOURCE_A,),
            output_directory=tmp_path,
            min_free_bytes=2,
        )
    assert not consumed
    assert not list(tmp_path.glob("*.tmp"))


def test_unbound_or_unused_frozen_sources_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(FD1RecoveryIdentityError, match="no frozen source"):
        write_identity_spool(
            (_batch(1, _spec(1)),),
            source_releases=(SOURCE_B,),
            output_directory=tmp_path / "missing",
            min_free_bytes=0,
        )
    with pytest.raises(FD1RecoveryIdentityError, match="not represented"):
        write_identity_spool(
            (_batch(1, _spec(1)),),
            source_releases=(SOURCE_A, SOURCE_B),
            output_directory=tmp_path / "unused",
            min_free_bytes=0,
        )


def test_fd1_batch_adapter_preserves_upstream_batch_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person = _batch(1, _spec(1))
    organization = _batch(1, _spec(2, source=SOURCE_B.source_key))
    calls: list[tuple[str, int]] = []

    def people(report, **kwargs):
        calls.append(("people", kwargs["batch_size"]))
        yield person

    def organizations(report, **kwargs):
        calls.append(("organizations", kwargs["batch_size"]))
        assert kwargs["retrosheet_report"] == "retro-report"
        yield organization

    monkeypatch.setattr(
        "app.gen2.fd1_recovery_identity.iter_all_registry_person_identity_mapping_batches",
        people,
    )
    monkeypatch.setattr(
        "app.gen2.fd1_recovery_identity.iter_registry_organization_identity_mapping_batches",
        organizations,
    )
    identity_import = SimpleNamespace(people="people-report", organizations="org-report")
    actual = tuple(
        iter_fd1_identity_mapping_batches(
            identity_import,
            evidence_logical_uri_by_file_checksum={"a" * 64: "raw/a.csv"},
            retrosheet_report="retro-report",
            batch_size=10_000,
        )
    )
    assert actual == (person, organization)
    assert calls == [("people", 10_000), ("organizations", 10_000)]
    with pytest.raises(FD1RecoveryIdentityError, match="10,000"):
        tuple(
            iter_fd1_identity_mapping_batches(
                identity_import,
                evidence_logical_uri_by_file_checksum={},
                retrosheet_report="retro-report",
                batch_size=10_001,
            )
        )


def test_registry_row_freeze_rejects_non_live_source() -> None:
    row = SimpleNamespace(
        source_key=SOURCE_A.source_key,
        id=SOURCE_A.source_version_id,
        identity_checksum=SOURCE_A.source_release_identity_checksum,
        source_mode="DEMO",
    )
    with pytest.raises(FD1RecoveryIdentityError, match="must be LIVE"):
        IdentitySourceRelease.from_registry_row(row)
