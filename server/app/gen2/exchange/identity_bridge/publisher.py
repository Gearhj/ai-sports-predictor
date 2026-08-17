"""Resumable, atomic publisher for ``SPORTS_IDENTITY_BRIDGE_V1``."""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from typing import Any, NoReturn

from app.gen2.exchange.canonical_json import canonical_json_bytes, load_canonical_json
from app.gen2.exchange.filesystem import (
    assert_no_links,
    assert_regular_independent_file,
    atomic_write_bytes,
    governed_root,
    iter_relative_files,
    resolve_under,
)
from app.gen2.exchange.identity_bridge import trust as bridge_trust
from app.gen2.exchange.identity_bridge.builder import build_identity_bridge
from app.gen2.exchange.identity_bridge.contracts import SportsIdentityBridgeManifestV1
from app.gen2.exchange.identity_bridge.package_metadata import readme_bytes
from app.gen2.exchange.identity_bridge.parent import (
    ParentIdentityScanDeadline,
    ParentScanProgress,
    scan_parent_identity,
)
from app.gen2.exchange.identity_bridge.source import (
    IdentityBridgeSourceDeadline,
    capture_operational_snapshot,
    compare_operational_snapshots,
    load_identity_bridge_source,
)
from app.gen2.exchange.identity_bridge.validator import (
    IdentityBridgeValidationDeadline,
)


PUBLICATION_STATE_VERSION = "SPORTS_IDENTITY_BRIDGE_PUBLICATION_STATE_V1"
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_MAX_RUNTIME_MINUTES = 90
_MANIFEST_FILE = "sports-identity-bridge-manifest.json"
_STAGE_NAME = bridge_trust.CONTRACT_VERSION
_CONTENT_ADDRESS_DIRECTORY = re.compile(r"^sha256-([0-9a-f]{64})$")
_SUCCESS_STAGES = (
    "PLANNED",
    "EXTRACTING",
    "FILES_BUILT",
    "JOIN_VALIDATED",
    "PACKAGE_VALIDATED",
    "FINALIZED",
)


class IdentityBridgePublicationError(RuntimeError):
    """Publication cannot safely continue or an immutable target conflicts."""


class IdentityBridgePublicationStop(IdentityBridgePublicationError):
    """The runtime bound was reached with resumable state retained."""

    def __init__(self, *, resume_point: Mapping[str, Any]):
        super().__init__("Identity bridge publication reached its safe runtime bound")
        self.resume_point = dict(resume_point)


@dataclass(frozen=True, slots=True)
class IdentityBridgePublicationProgress:
    stage: str
    elapsed_seconds: float
    canonical_players: int
    aliases_written: int
    conflicts: int
    files_written: int
    files_total: int
    references_checked: int
    eta_seconds: float | None


@dataclass(frozen=True, slots=True)
class IdentityBridgePublicationResult:
    status: str
    package_path: Path
    bridge_manifest_id: str
    bridge_manifest_sha256: str
    bridge_package_id: str
    package_file_count: int
    package_bytes: int
    alias_rows: int
    active_player_rows: int
    files_written: int
    bytes_written: int
    validation_status: str
    idempotent_reuse: bool
    elapsed_seconds: float
    provider_network_calls: int = 0
    external_network_calls: int = 0
    database_writes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "package_path": str(self.package_path)}


def publish_identity_bridge(
    output_root: Path,
    parent_package_root: Path,
    *,
    staging_root: Path | None = None,
    resume: bool = False,
    validate_only: bool = False,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    max_runtime_minutes: float = DEFAULT_MAX_RUNTIME_MINUTES,
    project_root: Path | None = None,
    database_url: str | None = None,
    progress_callback: Callable[[IdentityBridgePublicationProgress], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> IdentityBridgePublicationResult:
    """Publish one content-addressed bridge without mutating its parent or ASP data."""

    if isinstance(heartbeat_seconds, bool) or not 1 <= heartbeat_seconds <= 30:
        raise ValueError("heartbeat_seconds must be between 1 and 30")
    if (
        isinstance(max_runtime_minutes, bool)
        or not isinstance(max_runtime_minutes, (int, float))
        or max_runtime_minutes <= 0
        or (
            isinstance(max_runtime_minutes, float)
            and not math.isfinite(max_runtime_minutes)
        )
    ):
        raise ValueError("max_runtime_minutes must be positive and finite")
    root = (
        Path(project_root).absolute()
        if project_root is not None
        else Path(__file__).resolve().parents[5]
    )
    output_lexical = Path(output_root).absolute()
    parent_lexical = Path(parent_package_root).absolute()
    staging_lexical = (
        Path(staging_root).absolute()
        if staging_root is not None
        else output_lexical.with_name(f"{output_lexical.name}.staging")
    )
    namespace_parts = (
        "identity-bridges",
        "mlb-fd1",
        f"manifest-sha256-{bridge_trust.EXPECTED_PARENT_MANIFEST_SHA256}",
    )
    final_base_lexical = output_lexical.joinpath(*namespace_parts)
    staging_base_lexical = staging_lexical.joinpath(*namespace_parts)
    staging_path_lexical = staging_base_lexical / _STAGE_NAME
    checkpoint_path_lexical = staging_base_lexical / f"{_STAGE_NAME}.checkpoint.json"
    expected_digest = bridge_trust.EXPECTED_BRIDGE_MANIFEST_SHA256
    expected_final = (
        final_base_lexical / f"sha256-{expected_digest}"
        if expected_digest is not None
        else None
    )
    resume_command = _resume_command(
        output_lexical,
        parent_lexical,
        staging_lexical,
        heartbeat_seconds,
        max_runtime_minutes,
    )
    _reject_project_tree_output(root, output_lexical, staging_lexical)
    _reject_parent_package_overlap(
        parent_lexical,
        (
            ("final bridge namespace", final_base_lexical),
            ("staging bridge package", staging_path_lexical),
            ("staging bridge checkpoint", checkpoint_path_lexical),
        ),
    )
    started = clock()
    deadline = started + float(max_runtime_minutes) * 60.0
    last_emit = started - heartbeat_seconds
    canonical_players = 0
    aliases_written = 0
    conflicts = 0
    files_written = 0
    bytes_written = 0
    references_checked = 0
    source_phase: str | None = None

    def snapshot(stage: str) -> IdentityBridgePublicationProgress:
        elapsed = max(clock() - started, 0.0)
        completed = min(files_written / bridge_trust.FINAL_PACKAGE_FILE_COUNT, 1.0)
        eta = elapsed * (1.0 - completed) / completed if completed > 0 else None
        return IdentityBridgePublicationProgress(
            stage=stage,
            elapsed_seconds=elapsed,
            canonical_players=canonical_players,
            aliases_written=aliases_written,
            conflicts=conflicts,
            files_written=files_written,
            files_total=bridge_trust.FINAL_PACKAGE_FILE_COUNT,
            references_checked=references_checked,
            eta_seconds=eta,
        )

    def emit(stage: str, *, force: bool = False) -> None:
        nonlocal last_emit
        now = clock()
        if now >= deadline:
            raise IdentityBridgePublicationStop(
                resume_point={**asdict(snapshot(stage)), "next_stage": stage}
            )
        if progress_callback is not None and (force or now - last_emit >= heartbeat_seconds):
            progress_callback(snapshot(stage))
            last_emit = now

    emit("PLANNED", force=True)
    # Reuse is confined to the exact parent-bound final namespace.  Before the
    # release digest is frozen this is the only permitted discovery operation:
    # the namespace must be empty or contain exactly one content-address child.
    existing_final = _existing_final_candidate(final_base_lexical, expected_digest)
    if existing_final is not None:
        existing_digest = existing_final.name.removeprefix("sha256-")
        try:
            validation = _validate_package(
                existing_final,
                parent_lexical,
                heartbeat_seconds=heartbeat_seconds,
                max_runtime_minutes=_remaining_minutes(deadline, clock),
                progress_callback=progress_callback,
                started=started,
            )
        except IdentityBridgeValidationDeadline as exc:
            raise IdentityBridgePublicationStop(
                resume_point={
                    **exc.progress,
                    "final_package": str(existing_final),
                    "resume_command": resume_command,
                }
            ) from exc
        if validation.manifest.bridge_manifest_sha256 != existing_digest:
            raise IdentityBridgePublicationError(
                "Existing bridge directory digest differs from its manifest"
            )
        _reconcile_promoted_checkpoint(
            staging_path_lexical,
            checkpoint_path_lexical,
            parent_lexical,
            existing_digest,
        )
        return _result(
            status="REUSED",
            package_path=existing_final.resolve(strict=True),
            validation=validation,
            files_written=0,
            bytes_written=0,
            idempotent_reuse=True,
            elapsed_seconds=max(clock() - started, 0.0),
        )
    if validate_only:
        if expected_final is None:
            raise IdentityBridgePublicationError(
                "Validate-only requires a frozen bridge release trust anchor"
            )
        raise IdentityBridgePublicationError(
            f"Expected content-addressed bridge does not exist: {expected_final}"
        )

    output = governed_root(output_lexical, create=not output_lexical.exists())
    final_base = _ensure_tree(output, namespace_parts)
    staging_governed = governed_root(
        staging_lexical, create=not staging_lexical.exists()
    )
    staging_base = _ensure_tree(staging_governed, namespace_parts)
    if staging_base.stat().st_dev != final_base.stat().st_dev:
        raise IdentityBridgePublicationError(
            "Staging and final bridge roots must share one atomic filesystem"
        )
    staging_path = resolve_under(staging_base, _STAGE_NAME)
    checkpoint_path = resolve_under(staging_base, f"{_STAGE_NAME}.checkpoint.json")

    state: dict[str, Any]
    if staging_path.exists() or staging_path.is_symlink():
        assert_no_links(staging_path)
        if not resume:
            raise IdentityBridgePublicationError(
                "Incomplete bridge staging exists; rerun with --resume"
            )
        if not staging_path.is_dir():
            raise IdentityBridgePublicationError("Bridge staging path is not a directory")
        state = _load_checkpoint(checkpoint_path)
        _validate_checkpoint_binding(state, parent_lexical)
    else:
        if resume:
            raise IdentityBridgePublicationError("No bridge staging exists to resume")
        if checkpoint_path.exists() or checkpoint_path.is_symlink():
            raise IdentityBridgePublicationError("Orphaned bridge checkpoint conflicts")
        staging_path.mkdir(exist_ok=False)
        staging_path = governed_root(staging_path)
        publication_timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        state = {
            "schema_version": PUBLICATION_STATE_VERSION,
            "contract_version": bridge_trust.CONTRACT_VERSION,
            "parent_package_root": str(parent_lexical),
            "parent_manifest_sha256": bridge_trust.EXPECTED_PARENT_MANIFEST_SHA256,
            "publication_timestamp": publication_timestamp,
            "stage": "PLANNED",
            "last_completed_stage": "PLANNED",
            "bridge_manifest_sha256": None,
            "files_written": 0,
            "bytes_written": 0,
            "stop_reason": None,
            "resume_command": resume_command,
        }
        _write_checkpoint(checkpoint_path, state)

    last_completed = str(state.get("last_completed_stage"))
    promoted = False
    try:
        if last_completed in {"PLANNED", "EXTRACTING"}:
            state.update({"stage": "EXTRACTING", "stop_reason": None})
            _write_checkpoint(checkpoint_path, state)
            emit("EXTRACTING", force=True)
            source_phase = "PRE_EXTRACTION_OPERATIONAL_SNAPSHOT"
            with _daemon_heartbeat(
                heartbeat_seconds,
                (lambda: emit("EXTRACTING", force=True))
                if progress_callback is not None
                else None,
            ):
                before = capture_operational_snapshot(
                    database_url=database_url,
                    deadline_monotonic=deadline,
                    clock=clock,
                )
            source_phase = None

            def parent_progress(row: ParentScanProgress) -> None:
                nonlocal references_checked
                references_checked = row.references_checked
                emit("EXTRACTING")

            parent = scan_parent_identity(
                parent_lexical,
                heartbeat_seconds=heartbeat_seconds,
                max_runtime_minutes=_remaining_minutes(deadline, clock),
                progress_callback=parent_progress,
                clock=clock,
            )
            references_checked = parent.governed_reference_coverage.reference_count
            prior_ids = sorted({row.canonical_player_id for row in parent.player_priors})
            source_phase = "IDENTITY_SOURCE_EXTRACTION"
            with _daemon_heartbeat(
                heartbeat_seconds,
                (lambda: emit("EXTRACTING", force=True))
                if progress_callback is not None
                else None,
            ):
                source = load_identity_bridge_source(
                    parent.all_retrosheet_player_ids,
                    prior_ids,
                    database_url=database_url,
                    deadline_monotonic=deadline,
                    clock=clock,
                )
            source_phase = None
            canonical_players = len(source.canonical_player_ids)
            conflicts = source.governed_evidence.blocking_conflict_rows
            published_at = datetime.fromisoformat(
                str(state["publication_timestamp"]).replace("Z", "+00:00")
            )
            build = build_identity_bridge(
                project_root=root,
                parent=parent,
                source=source,
                publication_timestamp=published_at,
            )
            aliases_written = build.alias_artifact.row_count
            source_phase = "POST_EXTRACTION_OPERATIONAL_SNAPSHOT"
            with _daemon_heartbeat(
                heartbeat_seconds,
                (lambda: emit("EXTRACTING", force=True))
                if progress_callback is not None
                else None,
            ):
                after = capture_operational_snapshot(
                    database_url=database_url,
                    deadline_monotonic=deadline,
                    clock=clock,
                )
            source_phase = None
            comparison = compare_operational_snapshots(before, after)
            if not comparison.unchanged:
                raise IdentityBridgePublicationError(
                    "ASP operational relations changed during bridge extraction"
                )
            if (
                expected_digest is not None
                and build.manifest.bridge_manifest_sha256 != expected_digest
            ):
                raise IdentityBridgePublicationError(
                    "Built bridge differs from the frozen release trust anchor"
                )
            semantic = {
                **build.semantic_files,
                _MANIFEST_FILE: build.manifest_bytes,
            }
            for logical_uri, payload in semantic.items():
                if _write_exact_staging(staging_path, logical_uri, payload):
                    files_written += 1
                    bytes_written += len(payload)
            state.update(
                {
                    "stage": "FILES_BUILT",
                    "last_completed_stage": "FILES_BUILT",
                    "bridge_manifest_sha256": (
                        build.manifest.bridge_manifest_sha256
                    ),
                    "files_written": int(state.get("files_written", 0))
                    + files_written,
                    "bytes_written": int(state.get("bytes_written", 0))
                    + bytes_written,
                    "operational_state_unchanged": True,
                    "stop_reason": None,
                }
            )
            _write_checkpoint(checkpoint_path, state)
            last_completed = "FILES_BUILT"
            emit("FILES_BUILT", force=True)

        manifest = _load_staging_manifest(staging_path)
        digest = manifest.bridge_manifest_sha256
        if expected_digest is not None and digest != expected_digest:
            raise IdentityBridgePublicationError(
                "Staged bridge differs from its frozen release trust anchor"
            )
        if state.get("bridge_manifest_sha256") != digest:
            raise IdentityBridgePublicationError("Bridge checkpoint digest differs")
        final_path = resolve_under(final_base, f"sha256-{digest}")

        if last_completed == "FILES_BUILT":
            validation = _validate_package(
                staging_path,
                parent_lexical,
                heartbeat_seconds=heartbeat_seconds,
                max_runtime_minutes=_remaining_minutes(deadline, clock),
                progress_callback=progress_callback,
                started=started,
                require_auxiliary=False,
            )
            state.update(
                {
                    "stage": "JOIN_VALIDATED",
                    "last_completed_stage": "JOIN_VALIDATED",
                    "stop_reason": None,
                }
            )
            _write_checkpoint(checkpoint_path, state)
            last_completed = "JOIN_VALIDATED"
            emit("JOIN_VALIDATED", force=True)
        else:
            validation = None

        if last_completed == "JOIN_VALIDATED":
            if validation is None:
                validation = _validate_package(
                    staging_path,
                    parent_lexical,
                    heartbeat_seconds=heartbeat_seconds,
                    max_runtime_minutes=_remaining_minutes(deadline, clock),
                    progress_callback=progress_callback,
                    started=started,
                    require_auxiliary=False,
                )
            report_payload = validation.report_bytes
            auxiliary = {
                "validation-report.json": report_payload,
                "README.md": readme_bytes(manifest, report_payload),
            }
            for logical_uri, payload in auxiliary.items():
                if _write_exact_staging(staging_path, logical_uri, payload):
                    files_written += 1
                    bytes_written += len(payload)
            staged_inventory = tuple(iter_relative_files(staging_path))
            state.update(
                {
                    "files_written": len(staged_inventory),
                    "bytes_written": sum(
                        path.stat().st_size for _uri, path in staged_inventory
                    ),
                }
            )

        validation = _validate_package(
            staging_path,
            parent_lexical,
            heartbeat_seconds=heartbeat_seconds,
            max_runtime_minutes=_remaining_minutes(deadline, clock),
            progress_callback=progress_callback,
            started=started,
            require_auxiliary=True,
        )
        state.update(
            {
                "stage": "PACKAGE_VALIDATED",
                "last_completed_stage": "PACKAGE_VALIDATED",
                "stop_reason": None,
            }
        )
        _write_checkpoint(checkpoint_path, state)
        emit("PACKAGE_VALIDATED", force=True)
        if final_path.exists() or final_path.is_symlink():
            raise IdentityBridgePublicationError(
                "Content-addressed final bridge appeared during publication"
            )
        os.replace(staging_path, final_path)
        # From this point onward the fully validated immutable package is the
        # source of truth.  A reporting/result-construction failure must never
        # replace its recoverable PACKAGE_VALIDATED/FINALIZED checkpoint with
        # FAILED, which would make exact idempotent reuse impossible.
        promoted = True
        state.update(
            {
                "stage": "FINALIZED",
                "last_completed_stage": "FINALIZED",
                "stop_reason": None,
            }
        )
        _write_checkpoint(checkpoint_path, state)
        _remove_checkpoint_best_effort(checkpoint_path)
        if progress_callback is not None:
            progress_callback(snapshot("FINALIZED"))
        return _result(
            status="FINALIZED",
            package_path=final_path,
            validation=validation,
            files_written=files_written,
            bytes_written=bytes_written,
            idempotent_reuse=False,
            elapsed_seconds=max(clock() - started, 0.0),
        )
    except IdentityBridgePublicationStop as exc:
        if not promoted:
            _record_failure(state, checkpoint_path, "MAX_RUNTIME_REACHED")
        raise IdentityBridgePublicationStop(
            resume_point={
                **exc.resume_point,
                "checkpoint": str(checkpoint_path),
                "staging_package": str(staging_path),
                "resume_command": resume_command,
            }
        ) from exc
    except ParentIdentityScanDeadline as exc:
        if not promoted:
            _record_failure(state, checkpoint_path, "MAX_RUNTIME_REACHED")
        raise IdentityBridgePublicationStop(
            resume_point={
                **exc.progress,
                "checkpoint": str(checkpoint_path),
                "staging_package": str(staging_path),
                "resume_command": resume_command,
            }
        ) from exc
    except IdentityBridgeValidationDeadline as exc:
        if not promoted:
            _record_failure(state, checkpoint_path, "MAX_RUNTIME_REACHED")
        raise IdentityBridgePublicationStop(
            resume_point={
                **exc.progress,
                "checkpoint": str(checkpoint_path),
                "staging_package": str(staging_path),
                "resume_command": resume_command,
            }
        ) from exc
    except IdentityBridgeSourceDeadline as exc:
        if not promoted:
            _record_failure(state, checkpoint_path, "MAX_RUNTIME_REACHED")
        raise IdentityBridgePublicationStop(
            resume_point={
                **asdict(snapshot("EXTRACTING")),
                "source_phase": source_phase,
                "checkpoint": str(checkpoint_path),
                "staging_package": str(staging_path),
                "resume_command": resume_command,
            }
        ) from exc
    except Exception as exc:
        if not promoted:
            _record_failure(state, checkpoint_path, type(exc).__name__)
        raise


def _validate_package(
    package_path: Path,
    parent_path: Path,
    *,
    heartbeat_seconds: int,
    max_runtime_minutes: float,
    progress_callback: Callable[[IdentityBridgePublicationProgress], None] | None,
    started: float,
    require_auxiliary: bool = True,
) -> Any:
    from app.gen2.exchange.identity_bridge.validator import validate_identity_bridge

    def relay(row: Any) -> None:
        if progress_callback is None:
            return
        progress_callback(
            IdentityBridgePublicationProgress(
                stage=str(row.stage),
                elapsed_seconds=max(time.monotonic() - started, 0.0),
                canonical_players=int(getattr(row, "canonical_players", 0)),
                aliases_written=int(getattr(row, "alias_rows", 0)),
                conflicts=int(getattr(row, "conflicts", 0)),
                files_written=0,
                files_total=bridge_trust.FINAL_PACKAGE_FILE_COUNT,
                references_checked=int(getattr(row, "references_checked", 0)),
                eta_seconds=getattr(row, "eta_seconds", None),
            )
        )

    return validate_identity_bridge(
        package_path,
        parent_path,
        heartbeat_seconds=heartbeat_seconds,
        max_runtime_minutes=max_runtime_minutes,
        require_auxiliary=require_auxiliary,
        progress_callback=relay,
    )


def _result(
    *,
    status: str,
    package_path: Path,
    validation: Any,
    files_written: int,
    bytes_written: int,
    idempotent_reuse: bool,
    elapsed_seconds: float,
) -> IdentityBridgePublicationResult:
    inventory = tuple(iter_relative_files(package_path))
    total_bytes = sum(path.stat().st_size for _uri, path in inventory)
    manifest = validation.manifest
    return IdentityBridgePublicationResult(
        status=status,
        package_path=package_path,
        bridge_manifest_id=manifest.bridge_manifest_id,
        bridge_manifest_sha256=manifest.bridge_manifest_sha256,
        bridge_package_id=manifest.bridge_package_id,
        package_file_count=len(inventory),
        package_bytes=total_bytes,
        alias_rows=manifest.totals.alias_rows,
        active_player_rows=manifest.totals.active_player_rows,
        files_written=files_written,
        bytes_written=bytes_written,
        validation_status=str(getattr(validation, "status", "VALID")),
        idempotent_reuse=idempotent_reuse,
        elapsed_seconds=elapsed_seconds,
    )


@contextmanager
def _daemon_heartbeat(
    interval_seconds: float, callback: Callable[[], None] | None
) -> Iterator[None]:
    if callback is None:
        yield
        return
    stopped = Event()
    failures: list[BaseException] = []

    def heartbeat() -> None:
        while not stopped.wait(interval_seconds):
            try:
                callback()
            except BaseException as exc:
                failures.append(exc)
                stopped.set()
                return

    worker = Thread(
        target=heartbeat,
        name="identity-bridge-publication-heartbeat",
        daemon=True,
    )
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=min(float(interval_seconds) + 1.0, 31.0))
    if worker.is_alive():
        raise IdentityBridgePublicationError(
            "Publication heartbeat worker did not stop safely"
        )
    if failures:
        raise failures[0]


def _existing_final_candidate(
    final_base: Path, expected_digest: str | None
) -> Path | None:
    if not (final_base.exists() or final_base.is_symlink()):
        return None
    try:
        governed = governed_root(final_base)
        entries = tuple(governed.iterdir())
    except OSError as exc:
        raise IdentityBridgePublicationError(
            "Final bridge namespace cannot be inspected safely"
        ) from exc
    if not entries:
        return None
    candidates: dict[str, Path] = {}
    for candidate in entries:
        match = _CONTENT_ADDRESS_DIRECTORY.fullmatch(candidate.name)
        if match is None:
            raise IdentityBridgePublicationError(
                "Final bridge namespace contains a malformed child"
            )
        assert_no_links(candidate)
        if not candidate.is_dir():
            raise IdentityBridgePublicationError(
                "Content-addressed bridge child is not a directory"
            )
        candidates[match.group(1)] = candidate
    if expected_digest is None and len(candidates) != 1:
        raise IdentityBridgePublicationError(
            "Final bridge namespace must contain exactly one content-addressed child"
        )
    if expected_digest is None:
        return next(iter(candidates.values()))
    return candidates.get(expected_digest)


def _reconcile_promoted_checkpoint(
    staging_path: Path,
    checkpoint_path: Path,
    parent_root: Path,
    bridge_digest: str,
) -> None:
    if staging_path.exists() or staging_path.is_symlink():
        assert_no_links(staging_path)
        raise IdentityBridgePublicationError(
            "Final bridge exists while a staging package remains"
        )
    if not (checkpoint_path.exists() or checkpoint_path.is_symlink()):
        return
    state = _load_checkpoint(checkpoint_path)
    _validate_checkpoint_binding(state, parent_root)
    completed = state.get("last_completed_stage")
    if completed not in {"PACKAGE_VALIDATED", "FINALIZED"}:
        raise IdentityBridgePublicationError(
            "Promoted bridge checkpoint is not at a recoverable stage"
        )
    if state.get("stage") != completed:
        raise IdentityBridgePublicationError(
            "Promoted bridge checkpoint stage is inconsistent"
        )
    if state.get("bridge_manifest_sha256") != bridge_digest:
        raise IdentityBridgePublicationError(
            "Promoted bridge checkpoint digest differs from the final package"
        )
    try:
        checkpoint_path.unlink()
    except OSError as exc:
        raise IdentityBridgePublicationError(
            "Validated promoted bridge checkpoint cannot be removed"
        ) from exc
    if checkpoint_path.exists() or checkpoint_path.is_symlink():
        raise IdentityBridgePublicationError(
            "Validated promoted bridge checkpoint still exists"
        )


def _reject_parent_package_overlap(
    parent_root: Path, candidates: Sequence[tuple[str, Path]]
) -> None:
    parent_paths = _comparison_paths(parent_root)
    for label, candidate in candidates:
        candidate_paths = _comparison_paths(candidate)
        if any(
            _paths_overlap(parent, target)
            for parent in parent_paths
            for target in candidate_paths
        ):
            raise IdentityBridgePublicationError(
                f"{label} overlaps the immutable parent package"
            )


def _comparison_paths(path: Path) -> tuple[Path, ...]:
    lexical = Path(path).absolute()
    try:
        resolved = lexical.resolve(strict=False)
    except (OSError, RuntimeError):
        return (lexical,)
    return (lexical,) if resolved == lexical else (lexical, resolved)


def _paths_overlap(left: Path, right: Path) -> bool:
    for child, parent in ((left, right), (right, left)):
        try:
            child.relative_to(parent)
        except ValueError:
            continue
        return True
    return False


def _reject_project_tree_output(
    project_root: Path, output_root: Path, staging_root: Path
) -> None:
    project = Path(project_root).absolute()
    for label, candidate in (("Output", output_root), ("Staging", staging_root)):
        try:
            Path(candidate).absolute().relative_to(project)
        except ValueError:
            continue
        raise IdentityBridgePublicationError(
            f"{label} root must remain outside the Git project tree"
        )


def _ensure_tree(root: Path, parts: Sequence[str]) -> Path:
    current = governed_root(root)
    for part in parts:
        candidate = resolve_under(current, part)
        if candidate.exists() or candidate.is_symlink():
            assert_no_links(candidate)
            if not candidate.is_dir():
                raise IdentityBridgePublicationError("Exchange directory path conflicts")
        else:
            candidate.mkdir(exist_ok=False)
        current = governed_root(candidate)
    return current


def _write_exact_staging(root: Path, logical_uri: str, payload: bytes) -> bool:
    path = resolve_under(root, logical_uri)
    if path.exists() or path.is_symlink():
        assert_no_links(path)
        assert_regular_independent_file(path)
        if path.read_bytes() != payload:
            raise IdentityBridgePublicationError(
                f"Staging file conflicts with canonical bytes: {logical_uri}"
            )
        return False
    atomic_write_bytes(path, payload)
    assert_regular_independent_file(path)
    return True


def _write_checkpoint(path: Path, state: Mapping[str, Any]) -> None:
    atomic_write_bytes(path, canonical_json_bytes(dict(state), trailing_newline=True))


def _load_checkpoint(path: Path) -> dict[str, Any]:
    if not (path.exists() or path.is_symlink()):
        raise IdentityBridgePublicationError("Bridge checkpoint is missing")
    assert_no_links(path)
    assert_regular_independent_file(path)
    try:
        payload = load_canonical_json(path)
    except Exception as exc:
        raise IdentityBridgePublicationError("Bridge checkpoint is invalid") from exc
    if not isinstance(payload, dict):
        raise IdentityBridgePublicationError("Bridge checkpoint must be an object")
    return payload


def _validate_checkpoint_binding(state: Mapping[str, Any], parent_root: Path) -> None:
    expected = {
        "schema_version": PUBLICATION_STATE_VERSION,
        "contract_version": bridge_trust.CONTRACT_VERSION,
        "parent_package_root": str(parent_root),
        "parent_manifest_sha256": bridge_trust.EXPECTED_PARENT_MANIFEST_SHA256,
    }
    if any(state.get(field) != value for field, value in expected.items()):
        raise IdentityBridgePublicationError("Bridge checkpoint binding differs")
    last = state.get("last_completed_stage")
    if last not in _SUCCESS_STAGES:
        raise IdentityBridgePublicationError("Bridge checkpoint stage is invalid")


def _load_staging_manifest(root: Path) -> SportsIdentityBridgeManifestV1:
    path = resolve_under(root, _MANIFEST_FILE, require_exists=True, require_file=True)
    assert_regular_independent_file(path)
    try:
        payload = load_canonical_json(path)
        return SportsIdentityBridgeManifestV1.model_validate(payload)
    except Exception as exc:
        raise IdentityBridgePublicationError("Staged bridge manifest is invalid") from exc


def _record_failure(state: dict[str, Any], path: Path, reason: str) -> None:
    state.update({"stage": "FAILED", "stop_reason": reason})
    try:
        _write_checkpoint(path, state)
    except Exception:
        pass


def _remove_checkpoint_best_effort(path: Path) -> None:
    try:
        if not (path.exists() or path.is_symlink()):
            return
        assert_no_links(path)
        assert_regular_independent_file(path)
        path.unlink()
    except OSError:
        return


def _remaining_minutes(deadline: float, clock: Callable[[], float]) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise IdentityBridgePublicationStop(
            resume_point={"next_stage": "RUNTIME_BOUNDARY", "remaining_seconds": 0}
        )
    return max(remaining / 60.0, 1.0 / 60.0)


def _resume_command(
    output_root: Path,
    parent_root: Path,
    staging_root: Path,
    heartbeat_seconds: int,
    max_runtime_minutes: float,
) -> str:
    return (
        "python -m app.gen2.exchange.identity_bridge_publisher "
        f'--output-root "{output_root}" '
        f'--parent-package-root "{parent_root}" '
        f'--staging-root "{staging_root}" --resume '
        f"--heartbeat-seconds {heartbeat_seconds} "
        f"--max-runtime-minutes {max_runtime_minutes:g}"
    )


def _print_progress(row: IdentityBridgePublicationProgress) -> None:
    eta = "unknown" if row.eta_seconds is None else f"{row.eta_seconds:.0f}s"
    print(
        f"Stage: {row.stage} | Canonical players: {row.canonical_players} | "
        f"Aliases: {row.aliases_written} | Conflicts: {row.conflicts} | "
        f"Files: {row.files_written}/{row.files_total} | "
        f"References: {row.references_checked} | "
        f"Elapsed: {row.elapsed_seconds:.1f}s | ETA: {eta}",
        flush=True,
    )


def _die(message: str, *, code: int) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish the MLB FD1 SPORTS_IDENTITY_BRIDGE_V1 companion"
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--parent-package-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--heartbeat-seconds", type=int, default=30)
    parser.add_argument("--max-runtime-minutes", type=float, default=90)
    args = parser.parse_args(argv)
    try:
        result = publish_identity_bridge(
            args.output_root,
            args.parent_package_root,
            staging_root=args.staging_root,
            resume=args.resume,
            validate_only=args.validate_only,
            heartbeat_seconds=args.heartbeat_seconds,
            max_runtime_minutes=args.max_runtime_minutes,
            progress_callback=_print_progress,
        )
    except IdentityBridgePublicationStop as exc:
        _die(
            canonical_json_bytes(exc.resume_point, trailing_newline=False).decode(
                "utf-8"
            ),
            code=75,
        )
    except Exception as exc:
        _die(f"IDENTITY_BRIDGE_PUBLICATION_FAILED: {exc}", code=2)
    print(canonical_json_bytes(result.as_dict()).decode("utf-8"))
    return 0


__all__ = [
    "DEFAULT_HEARTBEAT_SECONDS",
    "DEFAULT_MAX_RUNTIME_MINUTES",
    "IdentityBridgePublicationError",
    "IdentityBridgePublicationProgress",
    "IdentityBridgePublicationResult",
    "IdentityBridgePublicationStop",
    "main",
    "publish_identity_bridge",
]
