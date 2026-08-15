"""Fail-closed progress and heartbeat service for Gate 3C-FD1 recovery.

The recovery finalizer can spend meaningful time inside PostgreSQL ``COPY`` or
set-based reconciliation.  This module keeps those operations observable
without coupling progress reporting to an ORM session.  A checkpoint sink is
injected by the caller and **must commit its own bounded transaction before
returning**.  This is important: a heartbeat written through the finalizer's
long-running transaction would disappear on interruption and is not durable
evidence of progress.

The default NDJSON path is under the repository's ignored ``.runtime`` tree.
Records are append-only, flushed, and fsynced.  No function in this module
performs network or provider access.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import shutil
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import IO, Never, Protocol


HEARTBEAT_SCHEMA_VERSION = "gate3c_fd1_recovery_heartbeat_v1"
RECOVERY_EVENT = "FD1 RECOVERY"
DEFAULT_HEARTBEAT_SECONDS = 30.0
MAX_HEARTBEAT_SECONDS = 30.0
DEFAULT_SILENCE_GUARD_SECONDS = 60.0
MAX_SILENCE_GUARD_SECONDS = 60.0
DEFAULT_MAX_RUNTIME_MINUTES = 120.0
MAX_RUNTIME_MINUTES = 120.0
MINIMUM_FREE_BYTES = 100 * 1024**3
SAFE_STOP_EXIT_CODE = 75

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_UNSET = object()


class RecoveryCheckpointSink(Protocol):
    """Persist and commit one heartbeat in an independent transaction."""

    def __call__(self, record: Mapping[str, object]) -> None:
        """Commit ``record`` before returning or raise on failure."""


@dataclass(frozen=True, slots=True)
class RecoveryHeartbeat:
    """One console, NDJSON, and database checkpoint heartbeat."""

    schema_version: str
    event: str
    status: str
    run_id: str
    resume_token: str
    stage: str
    elapsed: str
    elapsed_seconds: float
    files_validated: int
    files_total: int
    mappings_staged: int
    mappings_total: int
    mappings_per_second: float
    inserted: int
    reused: int
    unresolved: int
    conflicts: int
    rss_bytes: int
    rss_gib: float
    peak_rss_bytes: int
    peak_rss_gib: float
    database_stage: str
    eta: str
    eta_seconds: int | None
    free_bytes: int
    minimum_free_bytes: int
    emitted_at: str
    reason: str | None
    exit_code: int | None

    def as_record(self) -> dict[str, object]:
        """Return a detached record suitable for an independently committing sink."""

        return asdict(self)


class FD1RecoveryStop(RuntimeError):
    """Signal a safe, resumable process stop using ``EX_TEMPFAIL`` (75)."""

    exit_code = SAFE_STOP_EXIT_CODE

    def __init__(self, reason: str, heartbeat: RecoveryHeartbeat) -> None:
        super().__init__(
            f"FD1 recovery stopped safely: {reason}; "
            f"resume_token={heartbeat.resume_token}"
        )
        self.reason = reason
        self.resume_token = heartbeat.resume_token
        self.heartbeat = heartbeat


@dataclass(slots=True)
class _ProgressState:
    stage: str
    database_stage: str
    files_validated: int = 0
    files_total: int = 0
    mappings_staged: int = 0
    mappings_total: int = 0
    inserted: int = 0
    reused: int = 0
    unresolved: int = 0
    conflicts: int = 0
    eta_seconds: int | None = None


class FD1RecoveryProgress:
    """Emit bounded, durable Gate 3C-FD1 recovery progress.

    ``start`` launches a daemon heartbeat worker by default.  Long operations
    should still call :meth:`raise_if_stopped` at safe interruption points: a
    background deadline or guard failure cannot asynchronously raise in the
    caller's thread, so it stores an :class:`FD1RecoveryStop` for that method to
    surface.
    """

    def __init__(
        self,
        *,
        run_id: str,
        checkpoint_sink: RecoveryCheckpointSink,
        log_path: Path | None = None,
        resume_token: str | None = None,
        stage: str = "PREFLIGHT",
        database_stage: str = "preflight",
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        silence_guard_seconds: float = DEFAULT_SILENCE_GUARD_SECONDS,
        max_runtime_minutes: float = DEFAULT_MAX_RUNTIME_MINUTES,
        minimum_free_bytes: int = MINIMUM_FREE_BYTES,
        free_space_path: Path | None = None,
        clock: Callable[[], float] = monotonic,
        wall_clock: Callable[[], datetime] | None = None,
        free_space: Callable[[Path], int] | None = None,
        rss_bytes: Callable[[], int] | None = None,
        stdout: IO[str] | None = None,
    ) -> None:
        self.run_id = _required_text(run_id, "run_id")
        self.resume_token = _required_text(
            resume_token or build_resume_token(self.run_id), "resume_token"
        )
        if not callable(checkpoint_sink):
            raise TypeError("checkpoint_sink must be callable and commit before returning")
        self._checkpoint_sink = checkpoint_sink
        self.log_path = Path(log_path or _default_log_path(self.run_id))
        if self.log_path.suffix.lower() not in {".ndjson", ".jsonl"}:
            raise ValueError("Recovery log must use an NDJSON .ndjson or .jsonl suffix")

        self.heartbeat_seconds = _bounded_positive(
            heartbeat_seconds,
            "heartbeat_seconds",
            maximum=MAX_HEARTBEAT_SECONDS,
        )
        self.silence_guard_seconds = _bounded_positive(
            silence_guard_seconds,
            "silence_guard_seconds",
            maximum=MAX_SILENCE_GUARD_SECONDS,
        )
        if self.heartbeat_seconds >= self.silence_guard_seconds:
            raise ValueError("heartbeat_seconds must be below the silence guard")
        self.max_runtime_minutes = _bounded_positive(
            max_runtime_minutes,
            "max_runtime_minutes",
            maximum=MAX_RUNTIME_MINUTES,
        )
        if minimum_free_bytes < MINIMUM_FREE_BYTES:
            raise ValueError("FD1 recovery requires at least the 100-GiB free-space reserve")
        self.minimum_free_bytes = int(minimum_free_bytes)

        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._free_space = free_space or _free_bytes
        self._rss_bytes = rss_bytes or _current_rss_bytes
        self._stdout = stdout or sys.stdout
        self._free_space_path = Path(free_space_path or self.log_path.parent)
        self._state = _ProgressState(
            stage=_required_text(stage, "stage"),
            database_stage=_required_text(database_stage, "database_stage"),
        )

        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._stop_signal: FD1RecoveryStop | None = None
        self._last_record: RecoveryHeartbeat | None = None
        self._peak_rss_bytes = 0

        self._started_at = _finite_clock_value(self._clock(), "clock")
        self._stage_started_at = self._started_at
        self._stage_start_mappings = 0
        self._last_emitted_at = self._started_at

    @property
    def deadline_seconds(self) -> float:
        return self.max_runtime_minutes * 60.0

    @property
    def peak_rss_bytes(self) -> int:
        with self._lock:
            return self._peak_rss_bytes

    @property
    def last_record(self) -> RecoveryHeartbeat | None:
        with self._lock:
            return self._last_record

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stop_signal is not None

    def start(self, *, background: bool = True) -> FD1RecoveryProgress:
        """Emit immediately and optionally start the autonomous heartbeat worker."""

        with self._lock:
            if self._closed:
                raise RuntimeError("Recovery progress service is closed")
            if self._started:
                return self
            self._started = True
            self._heartbeat_locked(force=True)
            if background:
                self._thread = threading.Thread(
                    target=self._worker,
                    name=f"fd1-recovery-heartbeat-{self.run_id}",
                    daemon=True,
                )
                self._thread.start()
        return self

    def __enter__(self) -> FD1RecoveryProgress:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Stop the heartbeat worker without claiming successful finalization."""

        self._wake.set()
        thread: threading.Thread | None
        with self._lock:
            self._closed = True
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=min(self.heartbeat_seconds + 1.0, 31.0))

    def update(
        self,
        *,
        stage: str | None = None,
        database_stage: str | None = None,
        files_validated: int | None = None,
        files_total: int | None = None,
        mappings_staged: int | None = None,
        mappings_total: int | None = None,
        inserted: int | None = None,
        reused: int | None = None,
        unresolved: int | None = None,
        conflicts: int | None = None,
        eta_seconds: int | None | object = _UNSET,
        emit: bool = False,
    ) -> RecoveryHeartbeat | None:
        """Update counters and optionally force an immediate heartbeat."""

        with self._lock:
            self._raise_if_stopped_locked()
            now = _finite_clock_value(self._clock(), "clock")
            candidate = replace(self._state)
            stage_changed = False
            if stage is not None:
                next_stage = _required_text(stage, "stage")
                if next_stage != candidate.stage:
                    candidate.stage = next_stage
                    stage_changed = True
            if database_stage is not None:
                candidate.database_stage = _required_text(
                    database_stage, "database_stage"
                )

            changes = {
                "files_validated": files_validated,
                "files_total": files_total,
                "mappings_staged": mappings_staged,
                "mappings_total": mappings_total,
                "inserted": inserted,
                "reused": reused,
                "unresolved": unresolved,
                "conflicts": conflicts,
            }
            for name, value in changes.items():
                if value is not None:
                    setattr(candidate, name, _nonnegative_int(value, name))
            if candidate.files_total and (
                candidate.files_validated > candidate.files_total
            ):
                raise ValueError("files_validated cannot exceed files_total")
            if candidate.mappings_total and (
                candidate.mappings_staged > candidate.mappings_total
            ):
                raise ValueError("mappings_staged cannot exceed mappings_total")
            if eta_seconds is not _UNSET:
                candidate.eta_seconds = (
                    None
                    if eta_seconds is None
                    else _nonnegative_int(eta_seconds, "eta_seconds")
                )
            if stage_changed:
                self._stage_started_at = now
                self._stage_start_mappings = self._state.mappings_staged
            self._state = candidate
            return self._heartbeat_locked(force=emit, now=now)

    def heartbeat(self, *, force: bool = False) -> RecoveryHeartbeat | None:
        """Check all guards and emit when due (or immediately when forced)."""

        with self._lock:
            return self._heartbeat_locked(force=force)

    def raise_if_stopped(self) -> None:
        """Raise a background guard's resumable stop signal in the caller thread."""

        with self._lock:
            self._raise_if_stopped_locked()

    def stop_safely(self, reason: str) -> Never:
        """Emit a durable ``STOPPED`` checkpoint and raise exit-code 75 signal."""

        with self._lock:
            self._raise_if_stopped_locked()
            self._stop_locked(_required_text(reason, "reason"))

    def _worker(self) -> None:
        while not self._wake.wait(self.heartbeat_seconds):
            try:
                self.heartbeat()
            except FD1RecoveryStop:
                return
            except Exception as exc:  # pragma: no cover - defensive thread boundary
                with self._lock:
                    if self._stop_signal is None:
                        self._stop_locked(
                            f"HEARTBEAT_WORKER_FAILED:{type(exc).__name__}",
                            raise_signal=False,
                        )
                return

    def _heartbeat_locked(
        self,
        *,
        force: bool,
        now: float | None = None,
    ) -> RecoveryHeartbeat | None:
        self._raise_if_stopped_locked()
        current = _finite_clock_value(self._clock(), "clock") if now is None else now
        if current < self._started_at:
            self._stop_locked("MONOTONIC_CLOCK_MOVED_BACKWARD")
        if current - self._started_at >= self.deadline_seconds:
            self._stop_locked("HARD_DEADLINE_REACHED")
        if current - self._last_emitted_at > self.silence_guard_seconds:
            self._stop_locked("HEARTBEAT_SILENCE_GUARD_EXCEEDED")

        free_bytes: int
        try:
            free_bytes = int(self._free_space(self._free_space_path))
        except Exception as exc:
            self._stop_locked(
                f"FREE_SPACE_CHECK_FAILED:{type(exc).__name__}", free_bytes=0
            )
        if free_bytes < self.minimum_free_bytes:
            self._stop_locked("FREE_SPACE_RESERVE_BREACHED", free_bytes=free_bytes)

        if not force and current - self._last_emitted_at < self.heartbeat_seconds:
            return None
        record = self._build_record_locked(
            now=current,
            status="RUNNING",
            free_bytes=free_bytes,
        )
        self._emit_locked(record)
        return record

    def _stop_locked(
        self,
        reason: str,
        *,
        free_bytes: int | None = None,
        raise_signal: bool = True,
    ) -> Never | None:
        if self._stop_signal is not None:
            if raise_signal:
                raise self._stop_signal
            return None
        now = max(_finite_clock_value(self._clock(), "clock"), self._started_at)
        if free_bytes is None:
            try:
                free_bytes = int(self._free_space(self._free_space_path))
            except Exception:
                free_bytes = 0
        record = self._build_record_locked(
            now=now,
            status="STOPPED",
            free_bytes=free_bytes,
            reason=reason,
            exit_code=SAFE_STOP_EXIT_CODE,
        )
        self._best_effort_emit_locked(record)
        signal = FD1RecoveryStop(reason, record)
        self._stop_signal = signal
        self._last_record = record
        self._wake.set()
        if raise_signal:
            raise signal
        return None

    def _build_record_locked(
        self,
        *,
        now: float,
        status: str,
        free_bytes: int,
        reason: str | None = None,
        exit_code: int | None = None,
    ) -> RecoveryHeartbeat:
        try:
            rss_bytes = max(0, int(self._rss_bytes()))
        except (OSError, TypeError, ValueError):
            rss_bytes = 0
        self._peak_rss_bytes = max(self._peak_rss_bytes, rss_bytes)
        elapsed = max(0.0, now - self._started_at)
        stage_elapsed = max(0.0, now - self._stage_started_at)
        stage_mappings = max(
            0, self._state.mappings_staged - self._stage_start_mappings
        )
        rate = stage_mappings / stage_elapsed if stage_elapsed > 0.0 else 0.0
        eta_seconds = self._state.eta_seconds
        emitted_at = self._wall_clock()
        if emitted_at.tzinfo is None:
            emitted_at = emitted_at.replace(tzinfo=UTC)
        emitted_at = emitted_at.astimezone(UTC)
        return RecoveryHeartbeat(
            schema_version=HEARTBEAT_SCHEMA_VERSION,
            event=RECOVERY_EVENT,
            status=status,
            run_id=self.run_id,
            resume_token=self.resume_token,
            stage=self._state.stage,
            elapsed=_format_duration(elapsed),
            elapsed_seconds=round(elapsed, 3),
            files_validated=self._state.files_validated,
            files_total=self._state.files_total,
            mappings_staged=self._state.mappings_staged,
            mappings_total=self._state.mappings_total,
            mappings_per_second=round(rate, 3),
            inserted=self._state.inserted,
            reused=self._state.reused,
            unresolved=self._state.unresolved,
            conflicts=self._state.conflicts,
            rss_bytes=rss_bytes,
            rss_gib=round(rss_bytes / 1024**3, 3),
            peak_rss_bytes=self._peak_rss_bytes,
            peak_rss_gib=round(self._peak_rss_bytes / 1024**3, 3),
            database_stage=self._state.database_stage,
            eta=(
                _format_duration(float(eta_seconds))
                if eta_seconds is not None
                else "unavailable"
            ),
            eta_seconds=eta_seconds,
            free_bytes=max(0, free_bytes),
            minimum_free_bytes=self.minimum_free_bytes,
            emitted_at=emitted_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            reason=reason,
            exit_code=exit_code,
        )

    def _emit_locked(self, heartbeat: RecoveryHeartbeat) -> None:
        try:
            self._deliver_locked(heartbeat)
        except Exception as exc:
            reason = f"HEARTBEAT_PERSISTENCE_FAILED:{type(exc).__name__}"
            self._stop_locked(reason)
        self._last_emitted_at = self._started_at + heartbeat.elapsed_seconds
        self._last_record = heartbeat

    def _best_effort_emit_locked(self, heartbeat: RecoveryHeartbeat) -> None:
        record = heartbeat.as_record()
        console = _console_text(heartbeat)
        try:
            self._stdout.write(console)
            self._stdout.flush()
        except Exception:
            pass
        try:
            _append_ndjson(self.log_path, record)
        except Exception:
            pass
        try:
            self._checkpoint_sink(dict(record))
        except Exception:
            pass

    def _deliver_locked(self, heartbeat: RecoveryHeartbeat) -> None:
        record = heartbeat.as_record()
        self._stdout.write(_console_text(heartbeat))
        self._stdout.flush()
        _append_ndjson(self.log_path, record)
        self._checkpoint_sink(dict(record))

    def _raise_if_stopped_locked(self) -> None:
        if self._stop_signal is not None:
            raise self._stop_signal


def build_resume_token(run_id: str) -> str:
    """Return a stable opaque token for resuming one explicit recovery run."""

    normalized = _required_text(run_id, "run_id")
    digest = hashlib.sha256(
        f"gate3c-fd1-recovery-resume-v1:{normalized}".encode("utf-8")
    ).hexdigest()
    return f"fd1r1-{digest}"


def _default_log_path(run_id: str) -> Path:
    safe_run_id = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in run_id
    )
    return (
        _REPOSITORY_ROOT
        / ".runtime"
        / "gate3c-fd1"
        / "recovery"
        / safe_run_id
        / "progress.ndjson"
    )


def _console_text(heartbeat: RecoveryHeartbeat) -> str:
    lines = [
        RECOVERY_EVENT,
        f"Status: {heartbeat.status}",
        f"Run ID: {heartbeat.run_id}",
        f"Stage: {heartbeat.stage}",
        f"Elapsed: {heartbeat.elapsed}",
        f"Files validated: {heartbeat.files_validated} / {heartbeat.files_total}",
        f"Mappings staged: {heartbeat.mappings_staged:,} / {heartbeat.mappings_total:,}",
        f"Mappings/sec: {heartbeat.mappings_per_second:,.3f}",
        f"Inserted: {heartbeat.inserted:,}",
        f"Reused: {heartbeat.reused:,}",
        f"Unresolved: {heartbeat.unresolved:,}",
        f"Conflicts: {heartbeat.conflicts:,}",
        f"RSS: {heartbeat.rss_gib:.3f} GiB",
        f"Peak RSS: {heartbeat.peak_rss_gib:.3f} GiB",
        f"Database stage: {heartbeat.database_stage}",
        f"Free space: {heartbeat.free_bytes / 1024**3:.3f} GiB",
        f"ETA: {heartbeat.eta}",
        f"Resume token: {heartbeat.resume_token}",
    ]
    if heartbeat.reason is not None:
        lines.append(f"Reason: {heartbeat.reason}")
    if heartbeat.exit_code is not None:
        lines.append(f"Exit code: {heartbeat.exit_code}")
    return "\n".join(lines) + "\n\n"


def _append_ndjson(path: Path, record: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")
    with path.open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return int(shutil.disk_usage(probe).free)


def _current_rss_bytes() -> int:
    if os.name == "nt":
        return _windows_rss_bytes()
    statm = Path("/proc/self/statm")
    try:
        resident_pages = int(statm.read_text(encoding="ascii").split()[1])
        return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, IndexError, OSError, ValueError):
        try:
            import resource

            maximum = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            return maximum if sys.platform == "darwin" else maximum * 1024
        except (ImportError, OSError, ValueError):
            return 0


def _windows_rss_bytes() -> int:
    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        succeeded = ctypes.windll.psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.WorkingSetSize) if succeeded else 0
    except (AttributeError, OSError):
        return 0


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _required_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    if any(character in value for character in "\r\n"):
        raise ValueError(f"{name} cannot contain line breaks")
    return value.strip()


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _bounded_positive(value: float, name: str, *, maximum: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(f"{name} must be positive and finite")
    if numeric > maximum:
        raise ValueError(f"{name} cannot exceed {maximum:g}")
    return numeric


def _finite_clock_value(value: float, name: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must return a finite value")
    return numeric


__all__ = [
    "DEFAULT_HEARTBEAT_SECONDS",
    "DEFAULT_MAX_RUNTIME_MINUTES",
    "DEFAULT_SILENCE_GUARD_SECONDS",
    "FD1RecoveryProgress",
    "FD1RecoveryStop",
    "HEARTBEAT_SCHEMA_VERSION",
    "MAX_HEARTBEAT_SECONDS",
    "MAX_RUNTIME_MINUTES",
    "MAX_SILENCE_GUARD_SECONDS",
    "MINIMUM_FREE_BYTES",
    "RECOVERY_EVENT",
    "RecoveryCheckpointSink",
    "RecoveryHeartbeat",
    "SAFE_STOP_EXIT_CODE",
    "build_resume_token",
]
