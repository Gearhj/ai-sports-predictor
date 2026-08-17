from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, current_thread
from types import SimpleNamespace
from typing import Any

import pytest

import app.gen2.exchange.identity_bridge.publisher as publisher
from app.gen2.exchange.filesystem import ExchangePathError
from app.gen2.exchange.identity_bridge.parent import ParentIdentityScanDeadline


DIGEST = "d" * 64
BASE_FILES = {
    "active-player-dimension.parquet": b"dimension\n",
    "attribution.md": b"# Attribution\n",
    "player-identity-aliases.parquet": b"aliases\n",
    "source-rights.json": b'{"rights":"fixture"}\n',
    "sports-identity-bridge-manifest.json": b'{"manifest":"fixture"}\n',
}
REPORT_BYTES = b'{"status":"VALID"}\n'
README_BYTES = b"# SPORTS_IDENTITY_BRIDGE_V1 fixture\n"


@dataclass
class _PublisherHarness:
    root: Path
    project: Path
    output: Path
    staging: Path
    parent: Path
    manifest: SimpleNamespace
    validation_calls: list[tuple[Path, bool]] = field(default_factory=list)
    checkpoint_states: list[tuple[str, str, str | None]] = field(
        default_factory=list
    )
    scan_calls: int = 0
    source_calls: int = 0
    build_calls: int = 0
    snapshot_calls: int = 0
    snapshot_kwargs: list[dict[str, Any]] = field(default_factory=list)
    source_kwargs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def relative_base(self) -> Path:
        return (
            Path("identity-bridges")
            / "mlb-fd1"
            / (
                "manifest-sha256-"
                f"{publisher.bridge_trust.EXPECTED_PARENT_MANIFEST_SHA256}"
            )
        )

    @property
    def final_path(self) -> Path:
        return self.output / self.relative_base / f"sha256-{DIGEST}"

    @property
    def staging_path(self) -> Path:
        return self.staging / self.relative_base / publisher._STAGE_NAME

    @property
    def checkpoint_path(self) -> Path:
        return (
            self.staging
            / self.relative_base
            / f"{publisher._STAGE_NAME}.checkpoint.json"
        )


def _install_harness(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    frozen: bool = True,
) -> _PublisherHarness:
    root = artifact_root.absolute()
    project = root / "project"
    parent_root = root / "frozen-parent"
    project.mkdir()
    parent_root.mkdir()
    manifest = SimpleNamespace(
        bridge_manifest_id=f"sports-identity-bridge-manifest:sha256:{DIGEST}",
        bridge_manifest_sha256=DIGEST,
        bridge_package_id=f"sha256-{DIGEST}",
        totals=SimpleNamespace(alias_rows=2, active_player_rows=2),
    )
    harness = _PublisherHarness(
        root=root,
        project=project,
        output=root / "exchange-output",
        staging=root / "exchange-staging",
        parent=parent_root,
        manifest=manifest,
    )
    monkeypatch.setattr(
        publisher.bridge_trust,
        "EXPECTED_BRIDGE_MANIFEST_SHA256",
        DIGEST if frozen else None,
    )

    parent_evidence = SimpleNamespace(
        governed_reference_coverage=SimpleNamespace(reference_count=12_411_678),
        player_priors=(
            SimpleNamespace(canonical_player_id="11111111-1111-4111-8111-111111111111"),
            SimpleNamespace(canonical_player_id="22222222-2222-4222-8222-222222222222"),
        ),
        all_retrosheet_player_ids=("alpha001", "bravo002"),
    )
    source_evidence = SimpleNamespace(
        canonical_player_ids=(
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        ),
        governed_evidence=SimpleNamespace(blocking_conflict_rows=0),
    )
    build = SimpleNamespace(
        manifest=manifest,
        manifest_bytes=BASE_FILES["sports-identity-bridge-manifest.json"],
        semantic_files={
            key: value
            for key, value in BASE_FILES.items()
            if key != "sports-identity-bridge-manifest.json"
        },
        alias_artifact=SimpleNamespace(row_count=2),
    )

    def scan(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        harness.scan_calls += 1
        return parent_evidence

    def load_source(*_args: Any, **kwargs: Any) -> SimpleNamespace:
        harness.source_calls += 1
        harness.source_kwargs.append(dict(kwargs))
        return source_evidence

    def build_bridge(**_kwargs: Any) -> SimpleNamespace:
        harness.build_calls += 1
        return build

    def snapshot(**kwargs: Any) -> SimpleNamespace:
        harness.snapshot_calls += 1
        harness.snapshot_kwargs.append(dict(kwargs))
        return SimpleNamespace(sequence=harness.snapshot_calls)

    monkeypatch.setattr(publisher, "scan_parent_identity", scan)
    monkeypatch.setattr(publisher, "load_identity_bridge_source", load_source)
    monkeypatch.setattr(publisher, "build_identity_bridge", build_bridge)
    monkeypatch.setattr(publisher, "capture_operational_snapshot", snapshot)
    monkeypatch.setattr(
        publisher,
        "compare_operational_snapshots",
        lambda _before, _after: SimpleNamespace(unchanged=True),
    )
    monkeypatch.setattr(publisher, "readme_bytes", lambda *_args: README_BYTES)

    def load_manifest(package_root: Path) -> SimpleNamespace:
        path = Path(package_root) / "sports-identity-bridge-manifest.json"
        assert path.read_bytes() == BASE_FILES[path.name]
        return manifest

    monkeypatch.setattr(publisher, "_load_staging_manifest", load_manifest)

    base_names = set(BASE_FILES)
    final_names = {*base_names, "README.md", "validation-report.json"}

    def validate(
        package_path: Path,
        parent_path: Path,
        *,
        require_auxiliary: bool = True,
        **_kwargs: Any,
    ) -> SimpleNamespace:
        package = Path(package_path).absolute()
        assert Path(parent_path).absolute() == harness.parent
        names = {path.name for path in package.iterdir() if path.is_file()}
        inventory_valid = (
            names == final_names
            if require_auxiliary
            else base_names.issubset(names) and names.issubset(final_names)
        )
        if not inventory_valid:
            raise AssertionError(
                f"validation inventory differs: actual={sorted(names)} "
                f"base={sorted(base_names)} final={sorted(final_names)}"
            )
        report = package / "validation-report.json"
        readme = package / "README.md"
        if report.exists() and report.read_bytes() != REPORT_BYTES:
            raise AssertionError("staged validation report is not deterministic")
        if readme.exists() and readme.read_bytes() != README_BYTES:
            raise AssertionError("staged README is not deterministic")
        harness.validation_calls.append((package, require_auxiliary))
        return SimpleNamespace(
            manifest=manifest,
            report_bytes=REPORT_BYTES,
            status="VALID",
        )

    monkeypatch.setattr(publisher, "_validate_package", validate)
    real_checkpoint = publisher._write_checkpoint

    def tracked_checkpoint(path: Path, state: dict[str, Any]) -> None:
        harness.checkpoint_states.append(
            (
                str(state.get("stage")),
                str(state.get("last_completed_stage")),
                state.get("stop_reason"),
            )
        )
        real_checkpoint(path, state)

    monkeypatch.setattr(publisher, "_write_checkpoint", tracked_checkpoint)
    return harness


def _publish(
    harness: _PublisherHarness,
    **kwargs: Any,
) -> publisher.IdentityBridgePublicationResult:
    return publisher.publish_identity_bridge(
        harness.output,
        harness.parent,
        staging_root=harness.staging,
        project_root=harness.project,
        heartbeat_seconds=1,
        max_runtime_minutes=5,
        **kwargs,
    )


def _write_package_files(root: Path, *, auxiliary: bool) -> None:
    root.mkdir(parents=True, exist_ok=False)
    for name, payload in BASE_FILES.items():
        (root / name).write_bytes(payload)
    if auxiliary:
        (root / "validation-report.json").write_bytes(REPORT_BYTES)
        (root / "README.md").write_bytes(README_BYTES)


def _resume_state(harness: _PublisherHarness, stage: str, *, auxiliary: bool) -> None:
    _write_package_files(harness.staging_path, auxiliary=auxiliary)
    state = _checkpoint_state(harness, stage)
    state.update(
        {
            "files_written": 7 if auxiliary else 5,
            "bytes_written": sum(len(value) for value in BASE_FILES.values()),
        }
    )
    publisher._write_checkpoint(harness.checkpoint_path, state)
    harness.checkpoint_states.clear()


def _checkpoint_state(
    harness: _PublisherHarness, stage: str, *, digest: str = DIGEST
) -> dict[str, Any]:
    return {
        "schema_version": publisher.PUBLICATION_STATE_VERSION,
        "contract_version": publisher.bridge_trust.CONTRACT_VERSION,
        "parent_package_root": str(harness.parent),
        "parent_manifest_sha256": (
            publisher.bridge_trust.EXPECTED_PARENT_MANIFEST_SHA256
        ),
        "publication_timestamp": "2026-08-16T12:00:00Z",
        "stage": stage,
        "last_completed_stage": stage,
        "bridge_manifest_sha256": digest,
        "files_written": 7,
        "bytes_written": sum(len(value) for value in BASE_FILES.values()),
        "stop_reason": None,
        "resume_command": "fixture-command",
    }


def _forbid(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("forbidden publisher side effect")


def test_required_state_progression_validates_then_atomically_promotes(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    progress: list[publisher.IdentityBridgePublicationProgress] = []
    real_replace = os.replace
    promotions: list[tuple[Path, Path]] = []

    def track_replace(source: os.PathLike[str], target: os.PathLike[str]) -> None:
        source_path = Path(source).absolute()
        target_path = Path(target).absolute()
        if source_path == harness.staging_path and target_path == harness.final_path:
            promotions.append((source_path, target_path))
        real_replace(source, target)

    monkeypatch.setattr(publisher.os, "replace", track_replace)
    result = _publish(harness, progress_callback=progress.append)

    assert result.status == "FINALIZED"
    assert result.package_path == harness.final_path
    assert result.package_file_count == publisher.bridge_trust.FINAL_PACKAGE_FILE_COUNT
    assert result.files_written == publisher.bridge_trust.FINAL_PACKAGE_FILE_COUNT
    assert result.idempotent_reuse is False
    assert promotions == [(harness.staging_path, harness.final_path)]
    assert not harness.staging_path.exists()
    assert not harness.checkpoint_path.exists()
    assert [auxiliary for _path, auxiliary in harness.validation_calls] == [
        False,
        True,
    ]
    assert [row.stage for row in progress] == [
        "PLANNED",
        "EXTRACTING",
        "FILES_BUILT",
        "JOIN_VALIDATED",
        "PACKAGE_VALIDATED",
        "FINALIZED",
    ]
    assert [last for _stage, last, _reason in harness.checkpoint_states] == [
        "PLANNED",
        "PLANNED",
        "FILES_BUILT",
        "JOIN_VALIDATED",
        "PACKAGE_VALIDATED",
        "FINALIZED",
    ]
    assert harness.scan_calls == harness.source_calls == harness.build_calls == 1
    assert len(harness.snapshot_kwargs) == 2
    assert len(harness.source_kwargs) == 1
    database_reads = [*harness.snapshot_kwargs, *harness.source_kwargs]
    assert all(row["database_url"] is None for row in database_reads)
    assert len({row["deadline_monotonic"] for row in database_reads}) == 1
    assert all(callable(row["clock"]) for row in database_reads)
    assert len({id(row["clock"]) for row in database_reads}) == 1
    assert not (harness.project / "identity-bridges").exists()


@pytest.mark.parametrize(
    ("stage", "auxiliary", "validation_modes", "files_written"),
    (
        ("FILES_BUILT", False, [False, True], 2),
        ("JOIN_VALIDATED", False, [False, True], 2),
        ("JOIN_VALIDATED", True, [False, True], 0),
        ("PACKAGE_VALIDATED", True, [True], 0),
    ),
)
def test_resume_from_completed_staging_stage_without_reextraction(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    auxiliary: bool,
    validation_modes: list[bool],
    files_written: int,
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    _resume_state(harness, stage, auxiliary=auxiliary)

    result = _publish(harness, resume=True)

    assert result.status == "FINALIZED"
    assert result.files_written == files_written
    assert [mode for _path, mode in harness.validation_calls] == validation_modes
    assert harness.scan_calls == harness.source_calls == harness.build_calls == 0
    assert harness.snapshot_calls == 0
    assert harness.final_path.is_dir()
    assert not harness.checkpoint_path.exists()


def test_deadline_records_checkpoint_and_exact_resume_command(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)

    def deadline(*_args: Any, **_kwargs: Any) -> Any:
        raise ParentIdentityScanDeadline(
            {
                "stage": "SCANNING_RETROSHEET_IDENTITIES",
                "files_checked": 17,
                "references_checked": 123,
            }
        )

    monkeypatch.setattr(publisher, "scan_parent_identity", deadline)
    expected_command = (
        "python -m app.gen2.exchange.identity_bridge_publisher "
        f'--output-root "{harness.output}" '
        f'--parent-package-root "{harness.parent}" '
        f'--staging-root "{harness.staging}" --resume '
        "--heartbeat-seconds 5 --max-runtime-minutes 2.5"
    )
    with pytest.raises(publisher.IdentityBridgePublicationStop) as stopped:
        publisher.publish_identity_bridge(
            harness.output,
            harness.parent,
            staging_root=harness.staging,
            project_root=harness.project,
            heartbeat_seconds=5,
            max_runtime_minutes=2.5,
        )

    assert stopped.value.resume_point["resume_command"] == expected_command
    assert stopped.value.resume_point["checkpoint"] == str(harness.checkpoint_path)
    assert stopped.value.resume_point["staging_package"] == str(harness.staging_path)
    state = json.loads(harness.checkpoint_path.read_text(encoding="utf-8"))
    assert state["stage"] == "FAILED"
    assert state["last_completed_stage"] == "PLANNED"
    assert state["stop_reason"] == "MAX_RUNTIME_REACHED"
    assert state["resume_command"] == expected_command
    assert not harness.final_path.exists()


def test_validation_deadline_becomes_resumable_exit_75_with_checkpoint(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)

    def validation_deadline(*_args: Any, **_kwargs: Any) -> Any:
        raise publisher.IdentityBridgeValidationDeadline(
            {
                "stage": "RECOMPUTING_JOINS",
                "files_checked": 5,
                "rows_checked": 2,
            }
        )

    monkeypatch.setattr(publisher, "_validate_package", validation_deadline)
    expected_command = (
        "python -m app.gen2.exchange.identity_bridge_publisher "
        f'--output-root "{harness.output}" '
        f'--parent-package-root "{harness.parent}" '
        f'--staging-root "{harness.staging}" --resume '
        "--heartbeat-seconds 1 --max-runtime-minutes 5"
    )
    with pytest.raises(publisher.IdentityBridgePublicationStop) as stopped:
        _publish(harness)

    resume_point = stopped.value.resume_point
    assert resume_point["stage"] == "RECOMPUTING_JOINS"
    assert resume_point["files_checked"] == 5
    assert resume_point["checkpoint"] == str(harness.checkpoint_path)
    assert resume_point["staging_package"] == str(harness.staging_path)
    assert resume_point["resume_command"] == expected_command
    state = json.loads(harness.checkpoint_path.read_text(encoding="utf-8"))
    assert state["stage"] == "FAILED"
    assert state["last_completed_stage"] == "FILES_BUILT"
    assert state["stop_reason"] == "MAX_RUNTIME_REACHED"
    assert harness.staging_path.is_dir()
    assert not harness.final_path.exists()

    def stop_for_cli(*_args: Any, **_kwargs: Any) -> Any:
        raise stopped.value

    monkeypatch.setattr(publisher, "publish_identity_bridge", stop_for_cli)
    with pytest.raises(SystemExit) as exited:
        publisher.main(
            [
                "--output-root",
                str(harness.output),
                "--parent-package-root",
                str(harness.parent),
            ]
        )
    assert exited.value.code == 75
    emitted = json.loads(capsys.readouterr().err)
    assert emitted["resume_command"] == expected_command


@pytest.mark.parametrize(
    "source_phase",
    (
        "PRE_EXTRACTION_OPERATIONAL_SNAPSHOT",
        "IDENTITY_SOURCE_EXTRACTION",
        "POST_EXTRACTION_OPERATIONAL_SNAPSHOT",
    ),
)
def test_database_deadline_becomes_resumable_max_runtime_checkpoint(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_phase: str,
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)

    snapshot_calls = 0

    def snapshot_deadline(**_kwargs: Any) -> SimpleNamespace:
        nonlocal snapshot_calls
        snapshot_calls += 1
        if (
            source_phase == "PRE_EXTRACTION_OPERATIONAL_SNAPSHOT"
            or (
                source_phase == "POST_EXTRACTION_OPERATIONAL_SNAPSHOT"
                and snapshot_calls == 2
            )
        ):
            raise publisher.IdentityBridgeSourceDeadline("deadline")
        return SimpleNamespace(sequence=snapshot_calls)

    def source_deadline(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        if source_phase == "IDENTITY_SOURCE_EXTRACTION":
            raise publisher.IdentityBridgeSourceDeadline("deadline")
        return SimpleNamespace(
            canonical_player_ids=(
                "11111111-1111-4111-8111-111111111111",
                "22222222-2222-4222-8222-222222222222",
            ),
            governed_evidence=SimpleNamespace(blocking_conflict_rows=0),
        )

    monkeypatch.setattr(publisher, "capture_operational_snapshot", snapshot_deadline)
    monkeypatch.setattr(publisher, "load_identity_bridge_source", source_deadline)

    with pytest.raises(publisher.IdentityBridgePublicationStop) as stopped:
        _publish(harness)

    resume_point = stopped.value.resume_point
    assert resume_point["stage"] == "EXTRACTING"
    assert resume_point["source_phase"] == source_phase
    assert resume_point["checkpoint"] == str(harness.checkpoint_path)
    assert resume_point["staging_package"] == str(harness.staging_path)
    assert resume_point["resume_command"].endswith(
        "--heartbeat-seconds 1 --max-runtime-minutes 5"
    )
    state = json.loads(harness.checkpoint_path.read_text(encoding="utf-8"))
    assert state["stage"] == "FAILED"
    assert state["last_completed_stage"] == "PLANNED"
    assert state["stop_reason"] == "MAX_RUNTIME_REACHED"
    assert harness.staging_path.is_dir()
    assert not harness.final_path.exists()


def test_database_heartbeat_worker_is_daemon_and_stops_with_phase() -> None:
    emitted = Event()
    worker_is_daemon: list[bool] = []

    def heartbeat() -> None:
        worker_is_daemon.append(current_thread().daemon)
        emitted.set()

    with publisher._daemon_heartbeat(0.01, heartbeat):
        assert emitted.wait(1.0)

    assert worker_is_daemon
    assert all(worker_is_daemon)


def test_final_target_appearing_during_publication_is_an_immutable_conflict(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    base_validate = publisher._validate_package

    def conflict_after_validation(*args: Any, **kwargs: Any) -> SimpleNamespace:
        result = base_validate(*args, **kwargs)
        if kwargs.get("require_auxiliary", True):
            harness.final_path.mkdir(parents=True, exist_ok=False)
        return result

    monkeypatch.setattr(publisher, "_validate_package", conflict_after_validation)
    with pytest.raises(
        publisher.IdentityBridgePublicationError,
        match="final bridge appeared during publication",
    ):
        _publish(harness)

    assert harness.staging_path.is_dir()
    assert harness.final_path.is_dir()
    state = json.loads(harness.checkpoint_path.read_text(encoding="utf-8"))
    assert state["stage"] == "FAILED"
    assert state["last_completed_stage"] == "PACKAGE_VALIDATED"


def test_validate_only_requires_the_frozen_final_package(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    with pytest.raises(
        publisher.IdentityBridgePublicationError,
        match="Expected content-addressed bridge does not exist",
    ):
        _publish(harness, validate_only=True)
    assert not harness.output.exists()
    assert harness.scan_calls == harness.source_calls == harness.build_calls == 0


def test_validate_only_existing_final_is_read_only(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    _write_package_files(harness.final_path, auxiliary=True)
    monkeypatch.setattr(publisher, "_write_checkpoint", _forbid)
    monkeypatch.setattr(publisher, "scan_parent_identity", _forbid)
    monkeypatch.setattr(publisher, "load_identity_bridge_source", _forbid)
    monkeypatch.setattr(publisher, "build_identity_bridge", _forbid)
    monkeypatch.setattr(publisher, "capture_operational_snapshot", _forbid)

    result = _publish(harness, validate_only=True)

    assert result.status == "REUSED"
    assert result.idempotent_reuse is True
    assert result.files_written == result.bytes_written == 0
    assert harness.validation_calls == [(harness.final_path, True)]
    assert not harness.staging.exists()


def test_existing_final_reuse_performs_zero_writes_or_database_extraction(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    _write_package_files(harness.final_path, auxiliary=True)
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in harness.final_path.iterdir()
    }
    monkeypatch.setattr(publisher, "atomic_write_bytes", _forbid)
    monkeypatch.setattr(publisher, "scan_parent_identity", _forbid)
    monkeypatch.setattr(publisher, "load_identity_bridge_source", _forbid)
    monkeypatch.setattr(publisher, "build_identity_bridge", _forbid)
    monkeypatch.setattr(publisher, "capture_operational_snapshot", _forbid)

    result = _publish(harness, database_url="postgresql://must-not-be-opened")

    after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in harness.final_path.iterdir()
    }
    assert result.status == "REUSED"
    assert result.idempotent_reuse is True
    assert result.files_written == result.bytes_written == 0
    assert before == after
    assert not harness.staging.exists()


def test_prefreeze_second_run_reuses_the_sole_content_addressed_final(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch, frozen=False)
    first = _publish(harness)
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in harness.final_path.iterdir()
    }
    assert first.status == "FINALIZED"
    assert harness.scan_calls == harness.source_calls == harness.build_calls == 1

    monkeypatch.setattr(publisher, "atomic_write_bytes", _forbid)
    monkeypatch.setattr(publisher, "scan_parent_identity", _forbid)
    monkeypatch.setattr(publisher, "load_identity_bridge_source", _forbid)
    monkeypatch.setattr(publisher, "build_identity_bridge", _forbid)
    monkeypatch.setattr(publisher, "capture_operational_snapshot", _forbid)
    second = _publish(harness)

    after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in harness.final_path.iterdir()
    }
    assert second.status == "REUSED"
    assert second.idempotent_reuse is True
    assert second.files_written == second.bytes_written == 0
    assert before == after
    assert [mode for _path, mode in harness.validation_calls] == [False, True, True]
    assert not harness.staging_path.exists()
    assert not harness.checkpoint_path.exists()


def test_frozen_reuse_selects_exact_digest_among_well_formed_coexisting_bridges(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    _write_package_files(harness.final_path, auxiliary=True)
    changed_bridge = harness.final_path.with_name(f"sha256-{'e' * 64}")
    changed_bridge.mkdir()
    monkeypatch.setattr(publisher, "atomic_write_bytes", _forbid)
    monkeypatch.setattr(publisher, "scan_parent_identity", _forbid)
    monkeypatch.setattr(publisher, "load_identity_bridge_source", _forbid)
    monkeypatch.setattr(publisher, "build_identity_bridge", _forbid)

    result = _publish(harness)

    assert result.status == "REUSED"
    assert result.package_path == harness.final_path
    assert harness.validation_calls == [(harness.final_path, True)]
    assert changed_bridge.is_dir()


@pytest.mark.parametrize(
    "namespace_shape",
    ("malformed", "multiple", "file-child", "extra-file"),
)
def test_prefreeze_discovery_fails_closed_on_nonexact_final_namespace(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    namespace_shape: str,
) -> None:
    harness = _install_harness(artifact_root, monkeypatch, frozen=False)
    final_base = harness.output / harness.relative_base
    final_base.mkdir(parents=True)
    if namespace_shape == "malformed":
        (final_base / "latest").mkdir()
    elif namespace_shape == "multiple":
        (final_base / f"sha256-{DIGEST}").mkdir()
        (final_base / f"sha256-{'e' * 64}").mkdir()
    elif namespace_shape == "file-child":
        (final_base / f"sha256-{DIGEST}").write_bytes(b"not a package")
    else:
        (final_base / "unexpected.txt").write_bytes(b"extra")

    monkeypatch.setattr(publisher, "atomic_write_bytes", _forbid)
    monkeypatch.setattr(publisher, "scan_parent_identity", _forbid)
    with pytest.raises(publisher.IdentityBridgePublicationError):
        _publish(harness)
    assert harness.scan_calls == harness.source_calls == harness.build_calls == 0


@pytest.mark.parametrize("completed_stage", ["PACKAGE_VALIDATED", "FINALIZED"])
def test_post_rename_crash_checkpoint_is_reconciled_before_resume_requirement(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed_stage: str,
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    _write_package_files(harness.final_path, auxiliary=True)
    harness.checkpoint_path.parent.mkdir(parents=True)
    publisher._write_checkpoint(
        harness.checkpoint_path,
        _checkpoint_state(harness, completed_stage),
    )
    harness.checkpoint_states.clear()
    monkeypatch.setattr(publisher, "atomic_write_bytes", _forbid)
    monkeypatch.setattr(publisher, "scan_parent_identity", _forbid)
    monkeypatch.setattr(publisher, "load_identity_bridge_source", _forbid)
    monkeypatch.setattr(publisher, "build_identity_bridge", _forbid)
    monkeypatch.setattr(publisher, "capture_operational_snapshot", _forbid)

    result = _publish(harness, resume=True)

    assert result.status == "REUSED"
    assert result.idempotent_reuse is True
    assert result.files_written == result.bytes_written == 0
    assert not harness.staging_path.exists()
    assert not harness.checkpoint_path.exists()
    assert harness.validation_calls == [(harness.final_path, True)]


def test_post_rename_checkpoint_must_match_the_final_digest(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    _write_package_files(harness.final_path, auxiliary=True)
    harness.checkpoint_path.parent.mkdir(parents=True)
    publisher._write_checkpoint(
        harness.checkpoint_path,
        _checkpoint_state(harness, "PACKAGE_VALIDATED", digest="e" * 64),
    )

    with pytest.raises(
        publisher.IdentityBridgePublicationError, match="checkpoint digest differs"
    ):
        _publish(harness, resume=True)
    assert harness.checkpoint_path.is_file()


def test_post_rename_callback_failure_preserves_exact_reusable_checkpoint(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    monkeypatch.setattr(publisher, "_remove_checkpoint_best_effort", lambda _path: None)

    def fail_after_promotion(
        row: publisher.IdentityBridgePublicationProgress,
    ) -> None:
        if row.stage == "FINALIZED":
            raise RuntimeError("post-promotion reporting failed")

    with pytest.raises(RuntimeError, match="post-promotion reporting failed"):
        _publish(harness, progress_callback=fail_after_promotion)

    assert harness.final_path.is_dir()
    assert not harness.staging_path.exists()
    state = json.loads(harness.checkpoint_path.read_text(encoding="utf-8"))
    assert state["stage"] == "FINALIZED"
    assert state["last_completed_stage"] == "FINALIZED"
    assert state["bridge_manifest_sha256"] == DIGEST
    assert state["stop_reason"] is None

    monkeypatch.setattr(publisher, "atomic_write_bytes", _forbid)
    monkeypatch.setattr(publisher, "scan_parent_identity", _forbid)
    monkeypatch.setattr(publisher, "load_identity_bridge_source", _forbid)
    monkeypatch.setattr(publisher, "build_identity_bridge", _forbid)
    monkeypatch.setattr(publisher, "capture_operational_snapshot", _forbid)
    reused = _publish(harness)

    assert reused.status == "REUSED"
    assert reused.idempotent_reuse is True
    assert reused.files_written == reused.bytes_written == 0
    assert not harness.checkpoint_path.exists()


@pytest.mark.parametrize("interval_name", ["max_runtime_minutes", "heartbeat_seconds"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_publication_intervals_are_rejected_before_writes(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    interval_name: str,
    value: float,
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    monkeypatch.setattr(publisher, "governed_root", _forbid)
    arguments: dict[str, Any] = {
        "staging_root": harness.staging,
        "project_root": harness.project,
        "heartbeat_seconds": 1,
        "max_runtime_minutes": 5,
    }
    arguments[interval_name] = value

    with pytest.raises(ValueError):
        publisher.publish_identity_bridge(
            harness.output,
            harness.parent,
            **arguments,
        )

    assert not harness.output.exists()
    assert not harness.staging.exists()


@pytest.mark.parametrize(
    "logical_uri",
    ("../escaped.json", "/absolute.json", "C:/absolute.json"),
)
def test_staging_writer_rejects_traversal_and_absolute_logical_uris(
    artifact_root: Path,
    logical_uri: str,
) -> None:
    safe_root = artifact_root / "safe-staging"
    safe_root.mkdir()
    with pytest.raises(ExchangePathError):
        publisher._write_exact_staging(safe_root, logical_uri, b"escape")
    assert not (artifact_root / "escaped.json").exists()
    assert not any(safe_root.iterdir())


def test_resume_rejects_symlinked_staging_tree(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    outside = harness.root / "outside-staging"
    outside.mkdir()
    harness.staging_path.parent.mkdir(parents=True)
    try:
        harness.staging_path.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows environment does not permit symlink creation")

    with pytest.raises(ExchangePathError, match="Symlinks and junctions"):
        _publish(harness, resume=True)


def test_resume_rejects_hardlinked_checkpoint(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    harness.staging_path.mkdir(parents=True)
    outside = harness.root / "outside-checkpoint.json"
    outside.write_text("{}", encoding="utf-8")
    os.link(outside, harness.checkpoint_path)

    with pytest.raises(ExchangePathError, match="hardlinked"):
        _publish(harness, resume=True)


@pytest.mark.parametrize(
    "overlap",
    (
        "final-namespace-equal",
        "parent-contains-final",
        "final-contains-parent",
        "staging-package-equal",
        "parent-contains-checkpoint",
        "checkpoint-equal",
    ),
)
def test_parent_package_overlap_is_rejected_before_any_publisher_write(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    overlap: str,
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    final_base = harness.output / harness.relative_base
    if overlap == "final-namespace-equal":
        parent_root = final_base
    elif overlap == "parent-contains-final":
        parent_root = harness.output / "identity-bridges"
    elif overlap == "final-contains-parent":
        parent_root = harness.final_path / "immutable-parent"
    elif overlap == "staging-package-equal":
        parent_root = harness.staging_path
    elif overlap == "parent-contains-checkpoint":
        parent_root = harness.checkpoint_path.parent
    else:
        parent_root = harness.checkpoint_path
    parent_root.mkdir(parents=True)

    monkeypatch.setattr(publisher, "governed_root", _forbid)
    monkeypatch.setattr(publisher, "atomic_write_bytes", _forbid)
    monkeypatch.setattr(publisher, "scan_parent_identity", _forbid)
    with pytest.raises(
        publisher.IdentityBridgePublicationError,
        match="overlaps the immutable parent package",
    ):
        publisher.publish_identity_bridge(
            harness.output,
            parent_root,
            staging_root=harness.staging,
            project_root=harness.project,
            heartbeat_seconds=1,
            max_runtime_minutes=5,
        )
    assert harness.scan_calls == harness.source_calls == harness.build_calls == 0


def test_parent_ancestor_output_root_is_allowed_when_concrete_namespaces_are_siblings(
    artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    neutral_parent = (
        harness.output
        / "datasets"
        / "mlb-fd1"
        / f"sha256-{publisher.bridge_trust.EXPECTED_PARENT_MANIFEST_SHA256}"
    )
    neutral_parent.mkdir(parents=True)
    harness.parent = neutral_parent.absolute()

    result = _publish(harness)

    assert result.status == "FINALIZED"
    assert result.package_path == harness.final_path
    assert neutral_parent.is_dir()


@pytest.mark.parametrize("inside_root", ["output", "staging"])
def test_publisher_rejects_package_or_staging_output_inside_git_project(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    inside_root: str,
) -> None:
    harness = _install_harness(artifact_root, monkeypatch)
    output = harness.project / "generated-output" if inside_root == "output" else harness.output
    staging = (
        harness.project / "generated-staging"
        if inside_root == "staging"
        else harness.staging
    )
    with pytest.raises(
        publisher.IdentityBridgePublicationError, match="Git project tree"
    ):
        publisher.publish_identity_bridge(
            output,
            harness.parent,
            staging_root=staging,
            project_root=harness.project,
            heartbeat_seconds=1,
            max_runtime_minutes=5,
        )
    assert not output.exists()
    assert not staging.exists()
