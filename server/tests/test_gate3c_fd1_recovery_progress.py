from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

import app.gen2.fd1_recovery_progress as recovery_progress
from app.gen2.fd1_recovery_progress import (
    DEFAULT_MAX_RUNTIME_MINUTES,
    FD1RecoveryProgress,
    FD1RecoveryStop,
    MAX_RUNTIME_MINUTES,
    MINIMUM_FREE_BYTES,
    SAFE_STOP_EXIT_CODE,
    build_resume_token,
)


@dataclass
class _FakeClock:
    value: float = 1_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _FlushBuffer(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


def _progress(
    artifact_root: Path,
    *,
    clock: _FakeClock | None = None,
    stdout: _FlushBuffer | None = None,
    checkpoints: list[dict[str, object]] | None = None,
    rss_bytes=lambda: 2 * 1024**3,
    free_space=lambda _path: 200 * 1024**3,
    **overrides: object,
) -> tuple[FD1RecoveryProgress, _FakeClock, _FlushBuffer, list[dict[str, object]]]:
    deterministic_clock = clock or _FakeClock()
    output = stdout or _FlushBuffer()
    persisted = checkpoints if checkpoints is not None else []
    options: dict[str, object] = {
        "run_id": "fd1-recovery-20260815",
        "checkpoint_sink": lambda record: persisted.append(dict(record)),
        "log_path": artifact_root / "runtime" / "progress.ndjson",
        "clock": deterministic_clock,
        "wall_clock": lambda: datetime(2026, 8, 15, 15, 0, tzinfo=UTC),
        "rss_bytes": rss_bytes,
        "free_space": free_space,
        "stdout": output,
    }
    options.update(overrides)
    return FD1RecoveryProgress(**options), deterministic_clock, output, persisted


def _log_records(progress: FD1RecoveryProgress) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in progress.log_path.read_text(encoding="utf-8").splitlines()
    ]


def test_heartbeat_flushes_exact_progress_to_all_three_sinks(
    artifact_root: Path,
) -> None:
    rss = [int(1.8 * 1024**3)]
    progress, clock, stdout, checkpoints = _progress(
        artifact_root, rss_bytes=lambda: rss[0]
    )

    progress.start(background=False)
    progress.update(
        stage="IDENTITY_STAGED",
        database_stage="staging",
        files_validated=329,
        files_total=329,
        mappings_total=737_259,
        emit=True,
    )
    clock.advance(10)
    rss[0] = 2 * 1024**3
    heartbeat = progress.update(
        mappings_staged=91_800,
        inserted=17,
        reused=23,
        unresolved=3,
        conflicts=0,
        eta_seconds=36,
        emit=True,
    )

    assert heartbeat is not None
    assert heartbeat.stage == "IDENTITY_STAGED"
    assert heartbeat.elapsed == "00:00:10"
    assert heartbeat.files_validated == heartbeat.files_total == 329
    assert heartbeat.mappings_staged == 91_800
    assert heartbeat.mappings_total == 737_259
    assert heartbeat.mappings_per_second == 9_180.0
    assert heartbeat.inserted == 17
    assert heartbeat.reused == 23
    assert heartbeat.unresolved == 3
    assert heartbeat.conflicts == 0
    assert heartbeat.rss_gib == 2.0
    assert heartbeat.peak_rss_gib == 2.0
    assert heartbeat.database_stage == "staging"
    assert heartbeat.eta == "00:00:36"
    assert heartbeat.eta_seconds == 36
    assert heartbeat.free_bytes == 200 * 1024**3
    assert heartbeat.minimum_free_bytes == MINIMUM_FREE_BYTES
    assert heartbeat.emitted_at == "2026-08-15T15:00:00Z"

    console = stdout.getvalue()
    for expected in (
        "FD1 RECOVERY",
        "Stage: IDENTITY_STAGED",
        "Elapsed: 00:00:10",
        "Files validated: 329 / 329",
        "Mappings staged: 91,800 / 737,259",
        "Mappings/sec: 9,180.000",
        "Inserted: 17",
        "Reused: 23",
        "Unresolved: 3",
        "Conflicts: 0",
        "RSS: 2.000 GiB",
        "Database stage: staging",
        "ETA: 00:00:36",
    ):
        assert expected in console
    assert stdout.flushes == 3

    log_records = _log_records(progress)
    assert len(log_records) == len(checkpoints) == 3
    assert log_records == checkpoints
    assert log_records[-1] == heartbeat.as_record()
    assert progress.peak_rss_bytes == 2 * 1024**3


def test_heartbeat_is_due_at_30_seconds_and_eta_defaults_to_unavailable(
    artifact_root: Path,
) -> None:
    progress, clock, _, checkpoints = _progress(artifact_root)
    progress.start(background=False)

    clock.advance(29.999)
    assert progress.heartbeat() is None
    clock.advance(0.001)
    heartbeat = progress.heartbeat()

    assert heartbeat is not None
    assert heartbeat.elapsed == "00:00:30"
    assert heartbeat.eta == "unavailable"
    assert heartbeat.eta_seconds is None
    assert len(checkpoints) == 2


def test_silence_guard_emits_stopped_checkpoint_and_exit_75(
    artifact_root: Path,
) -> None:
    progress, clock, stdout, checkpoints = _progress(artifact_root)
    progress.start(background=False)
    clock.advance(60.001)

    with pytest.raises(FD1RecoveryStop) as captured:
        progress.heartbeat()

    signal = captured.value
    assert signal.exit_code == SAFE_STOP_EXIT_CODE == 75
    assert signal.reason == "HEARTBEAT_SILENCE_GUARD_EXCEEDED"
    assert signal.resume_token == build_resume_token("fd1-recovery-20260815")
    assert signal.heartbeat.status == "STOPPED"
    assert signal.heartbeat.exit_code == 75
    assert signal.heartbeat.resume_token == signal.resume_token
    assert progress.stopped is True
    assert checkpoints[-1]["status"] == "STOPPED"
    assert checkpoints[-1]["reason"] == "HEARTBEAT_SILENCE_GUARD_EXCEEDED"
    assert _log_records(progress)[-1] == checkpoints[-1]
    assert "Status: STOPPED" in stdout.getvalue()
    assert "Exit code: 75" in stdout.getvalue()

    with pytest.raises(FD1RecoveryStop) as repeated:
        progress.raise_if_stopped()
    assert repeated.value is signal


def test_monotonic_deadline_defaults_and_is_capped_at_120_minutes(
    artifact_root: Path,
) -> None:
    default, _, _, _ = _progress(artifact_root)
    assert default.max_runtime_minutes == DEFAULT_MAX_RUNTIME_MINUTES == 120.0
    assert default.deadline_seconds == 7_200.0

    with pytest.raises(ValueError, match="cannot exceed 120"):
        _progress(artifact_root, max_runtime_minutes=MAX_RUNTIME_MINUTES + 0.001)

    bounded, clock, _, checkpoints = _progress(
        artifact_root,
        log_path=artifact_root / "bounded" / "progress.ndjson",
        max_runtime_minutes=1.0,
    )
    bounded.start(background=False)
    clock.advance(60.0)

    with pytest.raises(FD1RecoveryStop, match="HARD_DEADLINE_REACHED") as captured:
        bounded.heartbeat()

    assert captured.value.exit_code == 75
    assert checkpoints[-1]["status"] == "STOPPED"
    assert checkpoints[-1]["elapsed"] == "00:01:00"


def test_free_space_guard_requires_100_gib_and_tracks_peak_rss(
    artifact_root: Path,
) -> None:
    free = [MINIMUM_FREE_BYTES]
    rss = [1024**3]
    progress, clock, _, checkpoints = _progress(
        artifact_root,
        free_space=lambda _path: free[0],
        rss_bytes=lambda: rss[0],
    )
    progress.start(background=False)
    clock.advance(30)
    rss[0] = 3 * 1024**3
    accepted = progress.heartbeat()
    assert accepted is not None
    assert accepted.peak_rss_gib == 3.0

    free[0] = MINIMUM_FREE_BYTES - 1
    rss[0] = 1024**3
    with pytest.raises(FD1RecoveryStop) as captured:
        progress.heartbeat(force=True)

    assert captured.value.reason == "FREE_SPACE_RESERVE_BREACHED"
    assert captured.value.heartbeat.free_bytes == MINIMUM_FREE_BYTES - 1
    assert progress.peak_rss_bytes == 3 * 1024**3
    assert checkpoints[-1]["status"] == "STOPPED"

    with pytest.raises(ValueError, match="100-GiB"):
        _progress(
            artifact_root,
            log_path=artifact_root / "invalid" / "progress.ndjson",
            minimum_free_bytes=MINIMUM_FREE_BYTES - 1,
        )


def test_checkpoint_failure_fails_closed_with_durable_stopped_signal(
    artifact_root: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def sink(record: dict[str, object]) -> None:
        calls.append(dict(record))
        if len(calls) > 1:
            raise RuntimeError("database unavailable")

    progress, _, stdout, _ = _progress(
        artifact_root,
        checkpoint_sink=sink,
    )
    progress.start(background=False)

    with pytest.raises(FD1RecoveryStop) as captured:
        progress.heartbeat(force=True)

    assert captured.value.reason == "HEARTBEAT_PERSISTENCE_FAILED:RuntimeError"
    assert captured.value.exit_code == 75
    assert _log_records(progress)[-1]["status"] == "STOPPED"
    assert "HEARTBEAT_PERSISTENCE_FAILED:RuntimeError" in stdout.getvalue()


def test_progress_contract_is_provider_free_and_validates_inputs(
    artifact_root: Path,
) -> None:
    progress, _, _, _ = _progress(artifact_root)
    assert ".runtime" in progress.log_path.parts or "runtime" in progress.log_path.parts
    default_path = FD1RecoveryProgress(
        run_id="ignored-log-contract",
        checkpoint_sink=lambda _record: None,
    ).log_path
    assert ".runtime" in default_path.parts
    assert default_path.name == "progress.ndjson"
    assert build_resume_token("same-run") == build_resume_token("same-run")
    assert build_resume_token("same-run") != build_resume_token("another-run")

    with pytest.raises(ValueError, match="heartbeat_seconds"):
        _progress(artifact_root, heartbeat_seconds=30.001)
    with pytest.raises(ValueError, match="below the silence guard"):
        _progress(
            artifact_root,
            heartbeat_seconds=30,
            silence_guard_seconds=30,
        )

    progress.start(background=False)
    with pytest.raises(ValueError, match="files_validated"):
        progress.update(files_total=1, files_validated=2)
    with pytest.raises(ValueError, match="mappings_staged"):
        progress.update(mappings_total=10, mappings_staged=11)

    module_text = Path(recovery_progress.__file__).read_text(encoding="utf-8").lower()
    assert "httpx" not in module_text
    assert "requests." not in module_text
    assert "mlb.com" not in module_text
    assert "baseballsavant" not in module_text
