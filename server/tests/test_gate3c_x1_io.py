from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.gen2.exchange.canonical_json import canonical_sha256
from app.gen2.exchange.contracts import PhysicalField
from app.gen2.exchange.evidence import (
    RECOVERY_MANIFEST_CHECKSUM,
    RECOVERY_MANIFEST_URI,
    load_fd1_evidence,
)
from app.gen2.exchange.filesystem import (
    ExchangePathError,
    iter_relative_files,
    resolve_under,
)
from app.gen2.exchange.publisher import (
    ExchangePublicationError,
    _copy_partition_resumable,
    _verify_copied_partition,
)


def _tiny_parquet(root: Path, *, name: str = "tiny.parquet") -> Path:
    path = root / name
    pl.DataFrame(
        {
            "game_date": ["2025-04-01", "2025-04-02", "2025-04-02"],
            "game_id": ["game-1", "game-2", "game-3"],
            "value": [1, 2, 3],
        }
    ).write_parquet(path, compression="zstd", statistics=True)
    return path


def _partition_evidence(path: Path) -> SimpleNamespace:
    schema = pl.read_parquet_schema(path)
    fields = tuple(
        PhysicalField(name=name, dtype=str(dtype)) for name, dtype in schema.items()
    )
    field_payload = [row.model_dump(mode="json") for row in fields]
    return SimpleNamespace(
        family="weather",
        byte_count=path.stat().st_size,
        file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        physical_schema_fingerprint=canonical_sha256(field_payload),
        physical_schema=fields,
        row_count=3,
        minimum_date="2025-04-01",
        maximum_date="2025-04-02",
    )


def _changed(partition: SimpleNamespace, **updates: object) -> SimpleNamespace:
    return SimpleNamespace(**{**vars(partition), **updates})


def test_evidence_loader_uses_only_fixed_artifacts_without_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_scan(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("FD1 evidence loading must not discover source files")

    monkeypatch.setattr(Path, "glob", forbidden_scan)
    monkeypatch.setattr(Path, "rglob", forbidden_scan)
    monkeypatch.setattr(os, "walk", forbidden_scan)

    evidence = load_fd1_evidence()

    assert evidence.recovery_manifest.aggregate.file_count == 329
    assert evidence.recovery_manifest.aggregate.row_count == 3_738_096
    assert evidence.recovery_manifest.aggregate.byte_count == 337_421_353
    assert len(evidence.artifacts) == 5
    assert len(evidence.closeout_documents) == 3
    recovery = evidence.artifact("recovery_manifest")
    assert recovery.logical_uri == RECOVERY_MANIFEST_URI
    assert evidence.recovery_manifest.manifest_checksum == RECOVERY_MANIFEST_CHECKSUM
    assert recovery.sha256 == hashlib.sha256(recovery.path.read_bytes()).hexdigest()
    assert recovery.path.is_relative_to(evidence.project_root)


def test_resumable_copy_reuses_a_verified_partial_prefix_and_verifies_parquet(
    artifact_root: Path,
) -> None:
    source = _tiny_parquet(artifact_root, name="source.parquet")
    destination = artifact_root / "copied.parquet"
    payload = source.read_bytes()
    prefix_length = max(1, len(payload) // 3)
    partial = destination.with_name(f".{destination.name}.partial")
    partial.write_bytes(payload[:prefix_length])
    chunks: list[int] = []

    newly_written = _copy_partition_resumable(
        source,
        destination,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_bytes=len(payload),
        on_chunk=chunks.append,
    )

    assert newly_written == len(payload) - prefix_length
    assert sum(chunks) == newly_written
    assert destination.read_bytes() == payload
    assert not partial.exists()
    _verify_copied_partition(destination, _partition_evidence(destination))


def test_resumable_copy_rejects_a_partial_prefix_from_other_content(
    artifact_root: Path,
) -> None:
    source = _tiny_parquet(artifact_root, name="source.parquet")
    destination = artifact_root / "copied.parquet"
    payload = source.read_bytes()
    mismatched_first_byte = bytes([payload[0] ^ 0xFF])
    partial = destination.with_name(f".{destination.name}.partial")
    partial.write_bytes(mismatched_first_byte + payload[1:64])

    with pytest.raises(ExchangePublicationError, match="prefix differs"):
        _copy_partition_resumable(
            source,
            destination,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_bytes=len(payload),
            on_chunk=lambda _count: None,
        )


@pytest.mark.parametrize("mismatch", ["hash", "row", "schema"])
def test_copied_partition_verifier_rejects_hash_row_and_schema_mismatch(
    artifact_root: Path, mismatch: str
) -> None:
    path = _tiny_parquet(artifact_root)
    partition = _partition_evidence(path)
    if mismatch == "hash":
        invalid = _changed(partition, file_sha256="0" * 64)
        message = "hash/byte count"
    elif mismatch == "row":
        invalid = _changed(partition, row_count=partition.row_count + 1)
        message = "row/schema evidence"
    else:
        invalid = _changed(
            partition,
            physical_schema=(PhysicalField(name="wrong", dtype="String"),),
        )
        message = "row/schema evidence"

    with pytest.raises(ExchangePublicationError, match=message):
        _verify_copied_partition(path, invalid)


def test_package_tree_rejects_symlink_escape_when_supported(
    artifact_root: Path,
) -> None:
    root = artifact_root / "package"
    root.mkdir()
    outside = artifact_root / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("This environment does not permit symlink creation")

    with pytest.raises(ExchangePathError, match="Symlinks and junctions"):
        resolve_under(root, "linked/secret.txt", require_exists=True)
    with pytest.raises(ExchangePathError, match="Symlinks and junctions"):
        list(iter_relative_files(root))


def test_package_tree_rejects_case_colliding_files_when_supported(
    artifact_root: Path,
) -> None:
    root = artifact_root / "package"
    root.mkdir()
    first = root / "Artifact.json"
    second = root / "artifact.json"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    if first.samefile(second) or len(list(root.iterdir())) != 2:
        pytest.skip("This filesystem does not support case-distinct file names")

    with pytest.raises(ExchangePathError, match="case-colliding"):
        list(iter_relative_files(root))
