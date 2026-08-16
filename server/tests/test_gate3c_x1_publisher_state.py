from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.gen2.exchange.publisher as publisher
from app.gen2.exchange.publisher import (
    ExchangePublicationError,
    ExchangePublicationStop,
    publish_sports_dataset_exchange,
)


TOTAL_PARQUET_BYTES = 337_421_353
PARTITION_COUNT = 329
LARGE_SOURCE_BYTES = 1_025_597
SMALL_SOURCE_BYTES = 1_025_596
LARGE_PARTITIONS = 269


@dataclass(frozen=True)
class _FakeManifest:
    partitions: tuple[SimpleNamespace, ...]
    manifest_sha256: str
    manifest_id: str
    dataset_version_id: str
    package_id: str
    published_at: datetime

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {
            "manifest_id": self.manifest_id,
            "manifest_sha256": self.manifest_sha256,
            "dataset_version_id": self.dataset_version_id,
            "package_id": self.package_id,
            "published_at": self.published_at.isoformat(),
        }


@dataclass
class _PublisherHarness:
    output: Path
    manifest: _FakeManifest
    validation_calls: list[tuple[Path, bool]]
    reuse_calls: list[Path]
    auxiliary_interrupted: bool = False

    @property
    def final_path(self) -> Path:
        return self.output / "datasets" / "mlb-fd1" / self.manifest.package_id

    @property
    def staging_path(self) -> Path:
        return self.output / ".staging" / f"publication-{self.manifest.package_id}"

    @property
    def checkpoint_path(self) -> Path:
        return self.output / ".staging" / (
            f"publication-{self.manifest.package_id}.checkpoint.json"
        )


def _install_publisher_harness(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    interrupt_first_auxiliary_validation: bool = False,
) -> _PublisherHarness:
    source_root = artifact_root / "curated"
    source_root.mkdir()
    large_source = source_root / "source-large.parquet"
    small_source = source_root / "source-small.parquet"
    large_source.write_bytes(b"L" * LARGE_SOURCE_BYTES)
    small_source.write_bytes(b"S" * SMALL_SOURCE_BYTES)
    large_sha256 = hashlib.sha256(large_source.read_bytes()).hexdigest()
    small_sha256 = hashlib.sha256(small_source.read_bytes()).hexdigest()

    digest = "a" * 64
    partitions: list[SimpleNamespace] = []
    for index in range(PARTITION_COUNT):
        large = index < LARGE_PARTITIONS
        partitions.append(
            SimpleNamespace(
                package_relative_uri=f"data/weather/part-{index:03d}.parquet",
                original_source_logical_identity=(
                    "source-large.parquet" if large else "source-small.parquet"
                ),
                family="weather",
                byte_count=LARGE_SOURCE_BYTES if large else SMALL_SOURCE_BYTES,
                row_count=1,
                file_sha256=large_sha256 if large else small_sha256,
                physical_schema_fingerprint="d" * 64,
            )
        )
    assert sum(row.byte_count for row in partitions) == TOTAL_PARQUET_BYTES
    manifest = _FakeManifest(
        partitions=tuple(partitions),
        manifest_sha256=digest,
        manifest_id=f"sports-dataset-manifest:sha256:{digest}",
        dataset_version_id=f"asp.mlb.open-data.fd1:sha256:{digest}",
        package_id=f"sha256-{digest}",
        published_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    recovery_files = (
        SimpleNamespace(logical_uri="source-large.parquet"),
        SimpleNamespace(logical_uri="source-small.parquet"),
    )
    evidence = SimpleNamespace(
        project_root=artifact_root,
        curated_root=source_root,
        recovery_manifest=SimpleNamespace(files=recovery_files),
        readiness={},
        final_result={},
        active_identity={},
        quarantine_impact={},
    )
    harness = _PublisherHarness(
        output=(artifact_root / "exchange-output").absolute(),
        manifest=manifest,
        validation_calls=[],
        reuse_calls=[],
    )

    monkeypatch.setattr(publisher, "load_fd1_evidence", lambda _root=None: evidence)
    monkeypatch.setattr(
        publisher,
        "build_schema_registry",
        lambda _manifest: {"schema_version": "fixture-registry-v1"},
    )
    monkeypatch.setattr(
        publisher,
        "build_source_rights",
        lambda: {"schema_version": "fixture-rights-v1"},
    )
    monkeypatch.setattr(
        publisher,
        "build_attribution_markdown",
        lambda: "# Fixture attribution\n",
    )
    monkeypatch.setattr(publisher, "exchange_code_revision", lambda _root: "e" * 64)
    monkeypatch.setattr(
        publisher, "_source_artifact_identities", lambda _evidence: {}
    )
    monkeypatch.setattr(publisher, "build_manifest", lambda **_kwargs: manifest)

    def fake_copy(
        _source: Path,
        destination: Path,
        *,
        expected_sha256: str,
        expected_bytes: int,
        on_chunk: Any,
    ) -> int:
        assert len(expected_sha256) == 64
        assert expected_bytes in {LARGE_SOURCE_BYTES, SMALL_SOURCE_BYTES}
        destination.write_bytes(b"fixture")
        on_chunk(len(b"fixture"))
        return len(b"fixture")

    monkeypatch.setattr(publisher, "_copy_partition_resumable", fake_copy)
    monkeypatch.setattr(
        publisher, "_verify_copied_partition", lambda _path, _partition: None
    )
    monkeypatch.setattr(
        publisher,
        "_verify_authoritative_source",
        lambda _source, _partition, _before: None,
    )
    monkeypatch.setattr(
        publisher, "_readme_bytes", lambda _manifest, _report: b"# Fixture README\n"
    )

    def fake_validate(
        package_root: Path, *, require_auxiliary_files: bool = True, **_kwargs: Any
    ) -> SimpleNamespace:
        package_path = Path(package_root)
        harness.validation_calls.append((package_path, require_auxiliary_files))
        auxiliary_paths = (
            package_path / "README.md",
            package_path / "validation-report.json",
        )
        if not require_auxiliary_files and any(path.exists() for path in auxiliary_paths):
            raise publisher.ExchangeValidationError(
                "Package file inventory differs: auxiliary files are unexpected"
            )
        if require_auxiliary_files:
            assert all(path.is_file() for path in auxiliary_paths)
        if (
            interrupt_first_auxiliary_validation
            and require_auxiliary_files
            and not harness.auxiliary_interrupted
        ):
            harness.auxiliary_interrupted = True
            raise publisher.ExchangeValidationDeadline(
                {
                    "stage": "AUXILIARY_VALIDATION",
                    "files_checked": PARTITION_COUNT,
                }
            )
        return SimpleNamespace(
            manifest_sha256=manifest.manifest_sha256,
            report={
                "schema_version": "SPORTS_DATASET_VALIDATION_REPORT_V1",
                "status": "VALID",
                "manifest_sha256": manifest.manifest_sha256,
            },
        )

    monkeypatch.setattr(publisher, "validate_sports_dataset_exchange", fake_validate)

    def fake_validate_existing(
        final_path: Path, _provisional: _FakeManifest, **_kwargs: Any
    ) -> _FakeManifest:
        harness.reuse_calls.append(Path(final_path))
        return manifest

    monkeypatch.setattr(publisher, "_validate_existing", fake_validate_existing)
    return harness


def _snapshot_files(root: Path) -> dict[str, tuple[str, int]]:
    return {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def test_publisher_validates_staging_before_atomic_promotion(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_publisher_harness(artifact_root, monkeypatch)
    progress = []

    result = publish_sports_dataset_exchange(
        harness.output,
        heartbeat_seconds=1,
        max_runtime_minutes=1,
        progress_callback=progress.append,
    )

    assert result.status == "FINALIZED"
    assert result.package_path == harness.final_path
    assert result.files_copied == PARTITION_COUNT
    assert result.bytes_copied == TOTAL_PARQUET_BYTES
    assert harness.final_path.is_dir()
    assert not harness.staging_path.exists()
    assert not harness.checkpoint_path.exists()
    assert [auxiliary for _path, auxiliary in harness.validation_calls] == [
        False,
        True,
    ]
    assert harness.validation_calls[0][0] == harness.staging_path
    assert harness.validation_calls[1][0] == harness.staging_path
    assert progress[-1].stage == "FINALIZED"


def test_publisher_exact_reuse_does_not_rewrite_or_copy(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_publisher_harness(artifact_root, monkeypatch)
    monkeypatch.setattr(
        publisher, "_remove_promoted_checkpoint_best_effort", lambda _path: None
    )
    publish_sports_dataset_exchange(
        harness.output, heartbeat_seconds=1, max_runtime_minutes=1
    )
    assert harness.checkpoint_path.is_file()
    before = _snapshot_files(harness.final_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Exact content-addressed reuse must not write or copy")

    monkeypatch.setattr(publisher, "_copy_partition_resumable", forbidden)
    monkeypatch.setattr(publisher, "_write_exact_staging", forbidden)
    result = publish_sports_dataset_exchange(
        harness.output, heartbeat_seconds=1, max_runtime_minutes=1
    )

    assert result.status == "REUSED"
    assert result.idempotent_reuse is True
    assert result.files_copied == result.bytes_copied == 0
    assert harness.reuse_calls == [harness.final_path]
    assert not harness.checkpoint_path.exists()
    assert _snapshot_files(harness.final_path) == before


@pytest.mark.parametrize("conflict", ["final_file", "staging_without_resume"])
def test_publisher_rejects_conflicting_final_and_staging_state(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    conflict: str,
) -> None:
    harness = _install_publisher_harness(artifact_root, monkeypatch)
    if conflict == "final_file":
        harness.final_path.parent.mkdir(parents=True)
        harness.final_path.write_text("conflict", encoding="utf-8")
        message = "not a directory"
    else:
        harness.staging_path.mkdir(parents=True)
        message = "rerun with --resume"

    with pytest.raises(ExchangePublicationError, match=message):
        publish_sports_dataset_exchange(
            harness.output, heartbeat_seconds=1, max_runtime_minutes=1
        )


def test_publisher_resumes_after_auxiliary_deadline_without_rewriting_them(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_publisher_harness(
        artifact_root,
        monkeypatch,
        interrupt_first_auxiliary_validation=True,
    )
    with pytest.raises(ExchangePublicationStop) as stopped:
        publish_sports_dataset_exchange(
            harness.output, heartbeat_seconds=1, max_runtime_minutes=1
        )
    assert stopped.value.resume_point["next_stage"] == (
        "AUXILIARY_PACKAGE_VALIDATION"
    )

    assert harness.staging_path.is_dir()
    assert harness.checkpoint_path.is_file()
    auxiliary_paths = [
        harness.staging_path / "README.md",
        harness.staging_path / "validation-report.json",
    ]
    before = {
        path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in auxiliary_paths
    }

    result = publish_sports_dataset_exchange(
        harness.output,
        resume=True,
        heartbeat_seconds=1,
        max_runtime_minutes=1,
    )

    assert result.status == "FINALIZED"
    assert result.package_path == harness.final_path
    assert not harness.checkpoint_path.exists()
    after = {
        path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in (
            harness.final_path / "README.md",
            harness.final_path / "validation-report.json",
        )
    }
    assert after == before
    assert [auxiliary for _path, auxiliary in harness.validation_calls] == [
        False,
        True,
        True,
    ]


def test_publisher_rejects_hardlinked_resume_checkpoint_before_read(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_publisher_harness(
        artifact_root,
        monkeypatch,
        interrupt_first_auxiliary_validation=True,
    )
    with pytest.raises(ExchangePublicationStop):
        publish_sports_dataset_exchange(
            harness.output, heartbeat_seconds=1, max_runtime_minutes=1
        )

    outside = artifact_root / "outside-checkpoint.json"
    harness.checkpoint_path.replace(outside)
    os.link(outside, harness.checkpoint_path)

    with pytest.raises(publisher.ExchangePathError, match="hardlinked"):
        publish_sports_dataset_exchange(
            harness.output,
            resume=True,
            heartbeat_seconds=1,
            max_runtime_minutes=1,
        )


def test_nested_validation_receives_exact_remaining_runtime() -> None:
    assert publisher._remaining_minutes(130.0, lambda: 100.0) == pytest.approx(0.5)
    assert publisher._remaining_minutes(99.0, lambda: 100.0) == pytest.approx(1e-9)


def test_validation_failure_is_durably_marked_failed(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_publisher_harness(artifact_root, monkeypatch)

    def fail_validation(*_args: object, **_kwargs: object) -> object:
        raise publisher.ExchangeValidationError("fixture validation failure")

    monkeypatch.setattr(
        publisher, "validate_sports_dataset_exchange", fail_validation
    )
    with pytest.raises(
        publisher.ExchangeValidationError, match="fixture validation failure"
    ):
        publish_sports_dataset_exchange(
            harness.output, heartbeat_seconds=1, max_runtime_minutes=1
        )

    state = json.loads(harness.checkpoint_path.read_text(encoding="utf-8"))
    assert state["stage"] == "FAILED"
    assert state["stop_reason"] == "ExchangeValidationError"
    assert harness.staging_path.is_dir()
    assert not harness.final_path.exists()
