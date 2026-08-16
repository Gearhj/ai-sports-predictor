"""Resumable, atomic publisher for the finalized Gate 3C-FD1 exchange."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import polars as pl

from app.gen2.exchange.canonical_json import (
    canonical_json_bytes,
    canonical_sha256,
    load_canonical_json,
)
from app.gen2.exchange.contracts import SportsDatasetManifestV1
from app.gen2.exchange.evidence import FD1AuthoritativeEvidence, load_fd1_evidence
from app.gen2.exchange.filesystem import (
    ExchangePathError,
    assert_no_links,
    assert_regular_independent_file,
    atomic_write_bytes,
    governed_root,
    resolve_under,
    sha256_file,
)
from app.gen2.exchange.manifest_builder import (
    build_manifest,
    exchange_code_revision,
)
from app.gen2.exchange.package_metadata import (
    normalized_markdown_bytes,
    readme_bytes as package_readme_bytes,
)
from app.gen2.exchange.schema_registry import build_schema_registry
from app.gen2.exchange.source_rights import (
    build_attribution_markdown,
    build_source_rights,
)
from app.gen2.exchange.validator import (
    ExchangeValidationDeadline,
    ExchangeValidationError,
    ValidationProgress,
    validate_sports_dataset_exchange,
    validation_report_bytes,
)


PUBLICATION_STATE_VERSION = "SPORTS_DATASET_EXCHANGE_PUBLICATION_STATE_V1"
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_MAX_RUNTIME_MINUTES = 90
COPY_CHUNK_BYTES = 1024 * 1024


class ExchangePublicationError(RuntimeError):
    """Publication cannot safely continue or an immutable target conflicts."""


class ExchangePublicationStop(ExchangePublicationError):
    """The safe runtime bound was reached and resumable state was retained."""

    def __init__(self, *, resume_point: Mapping[str, Any]):
        super().__init__("Exchange publication reached its safe runtime bound")
        self.resume_point = dict(resume_point)


@dataclass(frozen=True, slots=True)
class PublicationProgress:
    stage: str
    elapsed_seconds: float
    files_copied: int
    files_total: int
    bytes_copied: int
    bytes_total: int
    current_family: str | None
    hash_mismatches: int
    schema_mismatches: int
    eta_seconds: float | None


@dataclass(frozen=True, slots=True)
class PublicationResult:
    status: str
    package_path: Path
    manifest_id: str
    manifest_sha256: str
    package_id: str
    package_total_files: int
    parquet_file_count: int
    total_rows: int
    total_parquet_bytes: int
    files_copied: int
    bytes_copied: int
    source_files_verified: int
    source_bytes_verified: int
    validation_status: str
    idempotent_reuse: bool
    elapsed_seconds: float
    provider_network_calls: int = 0
    database_writes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "package_path": str(self.package_path)}


def publish_sports_dataset_exchange(
    output_root: Path,
    *,
    resume: bool = False,
    validate_only: bool = False,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    max_runtime_minutes: int = DEFAULT_MAX_RUNTIME_MINUTES,
    project_root: Path | None = None,
    progress_callback: Callable[[PublicationProgress], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> PublicationResult:
    """Publish the fixed finalized FD1 corpus to one content-derived package."""

    if isinstance(heartbeat_seconds, bool) or not 1 <= heartbeat_seconds <= 30:
        raise ValueError("heartbeat_seconds must be between 1 and 30")
    if isinstance(max_runtime_minutes, bool) or max_runtime_minutes < 1:
        raise ValueError("max_runtime_minutes must be positive")
    started = clock()
    deadline = started + max_runtime_minutes * 60
    last_emit = started - heartbeat_seconds
    files_copied = 0
    bytes_copied = 0
    current_family: str | None = None
    hash_mismatches = 0
    schema_mismatches = 0
    current_uri: str | None = None

    def progress(stage: str) -> PublicationProgress:
        elapsed = max(clock() - started, 0.0)
        rate = bytes_copied / elapsed if elapsed > 0 and bytes_copied else 0.0
        eta = (
            max((337_421_353 - bytes_copied) / rate, 0.0)
            if rate > 0 and bytes_copied < 337_421_353
            else None
        )
        return PublicationProgress(
            stage=stage,
            elapsed_seconds=elapsed,
            files_copied=files_copied,
            files_total=329,
            bytes_copied=bytes_copied,
            bytes_total=337_421_353,
            current_family=current_family,
            hash_mismatches=hash_mismatches,
            schema_mismatches=schema_mismatches,
            eta_seconds=eta,
        )

    def emit(stage: str, *, force: bool = False) -> None:
        nonlocal last_emit
        now = clock()
        if now >= deadline:
            raise ExchangePublicationStop(
                resume_point={
                    **asdict(progress(stage)),
                    "current_partition": current_uri,
                }
            )
        if progress_callback is not None and (force or now - last_emit >= heartbeat_seconds):
            progress_callback(progress(stage))
            last_emit = now

    emit("PLANNED", force=True)
    evidence = load_fd1_evidence(project_root)
    source_root = governed_root(evidence.curated_root)
    schema_registry = build_schema_registry(evidence.recovery_manifest)
    source_rights = build_source_rights()
    attribution_bytes = normalized_markdown_bytes(build_attribution_markdown())
    schema_bytes = canonical_json_bytes(schema_registry, trailing_newline=True)
    rights_bytes = canonical_json_bytes(source_rights, trailing_newline=True)
    publishing_revision = exchange_code_revision(evidence.project_root)
    source_identities = _source_artifact_identities(evidence)

    def build(published_at: datetime) -> SportsDatasetManifestV1:
        return build_manifest(
            recovery_manifest=evidence.recovery_manifest,
            readiness=evidence.readiness,
            finalized_result=evidence.final_result,
            active_identity=evidence.active_identity,
            quarantine_impact=evidence.quarantine_impact,
            source_artifact_identities=source_identities,
            schema_registry_artifact=_package_artifact(
                "schema-registry.json", schema_bytes
            ),
            source_rights_artifact=_package_artifact("source-rights.json", rights_bytes),
            attribution_artifact=_package_artifact("attribution.md", attribution_bytes),
            publishing_code_revision=publishing_revision,
            source_rights=source_rights,
            published_at=published_at,
        )

    provisional = build(datetime.now(UTC))
    output = governed_root(Path(output_root), create=True)
    datasets = _ensure_directory(output, "datasets")
    dataset_root = _ensure_directory(datasets, "mlb-fd1")
    final_path = resolve_under(dataset_root, provisional.package_id)
    staging_parent = _ensure_directory(output, ".staging")
    staging_name = f"publication-{provisional.package_id}"
    staging_path = resolve_under(staging_parent, staging_name)
    checkpoint_path = resolve_under(staging_parent, f"{staging_name}.checkpoint.json")

    if final_path.exists() or final_path.is_symlink():
        assert_no_links(final_path)
        if not final_path.is_dir():
            raise ExchangePublicationError(
                "Content-addressed package path exists but is not a directory"
            )
        try:
            validated = _validate_existing(
                final_path,
                provisional,
                heartbeat_seconds=heartbeat_seconds,
                max_runtime_minutes=_remaining_minutes(deadline, clock),
                progress_callback=progress_callback,
                started=started,
            )
        except ExchangeValidationDeadline as exc:
            raise ExchangePublicationStop(
                resume_point={
                    **exc.progress,
                    "final_package": str(final_path),
                    "next_stage": "EXISTING_PACKAGE_VALIDATION",
                }
            ) from exc
        if not validate_only:
            _reconcile_promoted_checkpoint(
                staging_path,
                checkpoint_path,
                provisional,
            )
        return _result(
            status="REUSED",
            package_path=final_path,
            manifest=validated,
            files_copied=0,
            bytes_copied=0,
            source_files_verified=0,
            source_bytes_verified=0,
            idempotent_reuse=True,
            elapsed_seconds=max(clock() - started, 0.0),
        )
    if validate_only:
        raise ExchangePublicationError(
            f"Expected content-addressed package does not exist: {final_path}"
        )

    state: dict[str, Any]
    if staging_path.exists() or staging_path.is_symlink():
        assert_no_links(staging_path)
        if not resume:
            raise ExchangePublicationError(
                "An incomplete staging package exists; rerun with --resume"
            )
        if not staging_path.is_dir() or not (
            checkpoint_path.exists() or checkpoint_path.is_symlink()
        ):
            raise ExchangePublicationError("Staging package/checkpoint is incomplete")
        assert_no_links(checkpoint_path)
        assert_regular_independent_file(checkpoint_path)
        state = _load_checkpoint(checkpoint_path, provisional)
        published_at = datetime.fromisoformat(
            str(state["published_at"]).replace("Z", "+00:00")
        )
        manifest = build(published_at)
    else:
        if checkpoint_path.exists() or checkpoint_path.is_symlink():
            raise ExchangePublicationError("Orphaned publication checkpoint conflicts")
        staging_path.mkdir(exist_ok=False)
        staging_path = governed_root(staging_path)
        manifest = provisional
        state = {
            "schema_version": PUBLICATION_STATE_VERSION,
            "manifest_sha256": manifest.manifest_sha256,
            "package_id": manifest.package_id,
            "published_at": manifest.published_at.isoformat(),
            "stage": "PLANNED",
            "completed_partitions": {},
            "files_copied": 0,
            "bytes_copied": 0,
            "started_at": datetime.now(UTC).isoformat(),
            "stop_reason": None,
            "resume_partition": None,
        }
        _write_checkpoint(checkpoint_path, state)
    if manifest.manifest_sha256 != provisional.manifest_sha256:
        raise ExchangePublicationError("Resumed manifest semantic identity changed")

    manifest_bytes = canonical_json_bytes(
        manifest.model_dump(mode="json"), trailing_newline=True
    )
    metadata = {
        "attribution.md": attribution_bytes,
        "schema-registry.json": schema_bytes,
        "source-rights.json": rights_bytes,
        "sports-dataset-manifest.json": manifest_bytes,
    }
    for uri, payload in metadata.items():
        _write_exact_staging(staging_path, uri, payload)

    completed = state["completed_partitions"]
    if not isinstance(completed, dict):
        raise ExchangePublicationError("Publication checkpoint partition map is invalid")
    try:
        files_copied = int(state["files_copied"])
        bytes_copied = int(state["bytes_copied"])
        checkpoint_bytes = sum(
            int(row["byte_count"]) for row in completed.values()
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExchangePublicationError(
            "Publication checkpoint totals are invalid"
        ) from exc
    if files_copied != len(completed) or bytes_copied != checkpoint_bytes:
        raise ExchangePublicationError(
            "Publication checkpoint counters do not reconcile"
        )
    source_files_verified = 0
    source_bytes_verified = 0
    state["stage"] = "COPYING"
    _write_checkpoint(checkpoint_path, state)
    emit("COPYING_PARTITIONS", force=True)
    try:
        entries = {
            str(entry.logical_uri): entry for entry in evidence.recovery_manifest.files
        }
        for partition in manifest.partitions:
            current_uri = partition.package_relative_uri
            current_family = partition.family
            if partition.original_source_logical_identity not in entries:
                raise ExchangePublicationError(
                    "Manifest partition is absent from the authoritative inventory"
                )
            source = resolve_under(
                source_root,
                partition.original_source_logical_identity,
                require_exists=True,
                require_file=True,
            )
            source_info = assert_regular_independent_file(source)
            if source_info.st_size != partition.byte_count:
                raise ExchangePublicationError("Authoritative source byte count changed")
            destination = resolve_under(staging_path, partition.package_relative_uri)
            _ensure_parent_tree(staging_path, destination.parent)
            checkpoint = completed.get(partition.package_relative_uri)
            if checkpoint is not None:
                _verify_checkpointed_partition(destination, partition, checkpoint)
                _verify_authoritative_source(source, partition, source_info)
                source_files_verified += 1
                source_bytes_verified += partition.byte_count
                emit("COPYING_PARTITIONS")
                continue
            if destination.exists() or destination.is_symlink():
                _verify_copied_partition(destination, partition)
                source_hash = sha256_file(source)
                if source_hash != partition.file_sha256:
                    raise ExchangePublicationError("Authoritative source checksum changed")
            else:
                newly_written = _copy_partition_resumable(
                    source,
                    destination,
                    expected_sha256=partition.file_sha256,
                    expected_bytes=partition.byte_count,
                    on_chunk=lambda count: _emit_copy_chunk(count, emit),
                )
                # Only bytes physically written by this invocation count toward the
                # operational result. Durable totals below remain checkpoint totals.
                del newly_written
                _verify_copied_partition(destination, partition)
            after = source.stat()
            if (after.st_size, after.st_mtime_ns) != (
                source_info.st_size,
                source_info.st_mtime_ns,
            ):
                raise ExchangePublicationError("Authoritative source changed during copy")
            completed[partition.package_relative_uri] = {
                "sha256": partition.file_sha256,
                "byte_count": partition.byte_count,
                "row_count": partition.row_count,
                "physical_schema_fingerprint": partition.physical_schema_fingerprint,
            }
            files_copied = len(completed)
            bytes_copied = sum(int(row["byte_count"]) for row in completed.values())
            source_files_verified += 1
            source_bytes_verified += partition.byte_count
            state.update(
                {
                    "files_copied": files_copied,
                    "bytes_copied": bytes_copied,
                    "resume_partition": partition.package_relative_uri,
                    "stop_reason": None,
                }
            )
            _write_checkpoint(checkpoint_path, state)
            emit("COPYING_PARTITIONS")
    except ExchangePublicationStop as exc:
        state["stop_reason"] = "MAX_RUNTIME_REACHED"
        state["resume_partition"] = current_uri
        _write_checkpoint(checkpoint_path, state)
        raise ExchangePublicationStop(
            resume_point={
                **exc.resume_point,
                "checkpoint": str(checkpoint_path),
                "staging_package": str(staging_path),
                "next_partition": current_uri,
                "files_completed": len(completed),
                "bytes_completed": sum(
                    int(row["byte_count"]) for row in completed.values()
                ),
            }
        ) from exc
    except Exception as exc:
        state["stage"] = "FAILED"
        state["stop_reason"] = type(exc).__name__
        state["resume_partition"] = current_uri
        _write_checkpoint(checkpoint_path, state)
        raise

    if len(completed) != 329 or sum(
        int(row["byte_count"]) for row in completed.values()
    ) != 337_421_353:
        raise ExchangePublicationError("Publication checkpoint totals do not reconcile")
    state["stage"] = "FILES_VERIFIED"
    state["stop_reason"] = None
    _write_checkpoint(checkpoint_path, state)
    emit("FILES_VERIFIED", force=True)

    report_path = resolve_under(staging_path, "validation-report.json")
    readme_path = resolve_under(staging_path, "README.md")
    report_present = _staging_file_present(report_path, "validation-report.json")
    readme_present = _staging_file_present(readme_path, "README.md")
    if readme_present and not report_present:
        raise ExchangePublicationError(
            "Staging README exists without its validation report"
        )

    if report_present:
        report_bytes = _canonical_report_bytes(report_path)
        expected_readme = _readme_bytes(manifest, report_bytes)
        if readme_present:
            if readme_path.read_bytes() != expected_readme:
                raise ExchangePublicationError("Staging README conflicts with its report")
        else:
            _write_exact_staging(staging_path, "README.md", expected_readme)
        state["stage"] = "AUXILIARY_WRITTEN"
        state["stop_reason"] = None
        _write_checkpoint(checkpoint_path, state)
    else:
        try:
            validation = validate_sports_dataset_exchange(
                staging_path,
                heartbeat_seconds=heartbeat_seconds,
                max_runtime_minutes=_remaining_minutes(deadline, clock),
                require_auxiliary_files=False,
                progress_callback=lambda row: _relay_validation_progress(
                    row, progress_callback, started, files_copied, bytes_copied
                ),
            )
        except ExchangeValidationDeadline as exc:
            _raise_validation_stop(
                exc,
                state=state,
                checkpoint_path=checkpoint_path,
                staging_path=staging_path,
                next_stage="BASE_PACKAGE_VALIDATION",
            )
        except Exception as exc:
            _record_publication_failure(state, checkpoint_path, exc)
            raise
        state["stage"] = "BASE_VALIDATED"
        state["stop_reason"] = None
        _write_checkpoint(checkpoint_path, state)
        report_bytes = validation_report_bytes(validation.report)
        _write_exact_staging(staging_path, "validation-report.json", report_bytes)
        _write_exact_staging(
            staging_path,
            "README.md",
            _readme_bytes(manifest, report_bytes),
        )
        state["stage"] = "AUXILIARY_WRITTEN"
        _write_checkpoint(checkpoint_path, state)

    try:
        validate_sports_dataset_exchange(
            staging_path,
            heartbeat_seconds=heartbeat_seconds,
            max_runtime_minutes=_remaining_minutes(deadline, clock),
            require_auxiliary_files=True,
            progress_callback=lambda row: _relay_validation_progress(
                row, progress_callback, started, files_copied, bytes_copied
            ),
        )
    except ExchangeValidationDeadline as exc:
        _raise_validation_stop(
            exc,
            state=state,
            checkpoint_path=checkpoint_path,
            staging_path=staging_path,
            next_stage="AUXILIARY_PACKAGE_VALIDATION",
        )
    except Exception as exc:
        _record_publication_failure(state, checkpoint_path, exc)
        raise
    state["stage"] = "PACKAGE_VALIDATED"
    state["stop_reason"] = None
    _write_checkpoint(checkpoint_path, state)
    try:
        emit("PACKAGE_VALIDATED", force=True)
    except ExchangePublicationStop as exc:
        state["stop_reason"] = "MAX_RUNTIME_REACHED_BEFORE_PROMOTION"
        _write_checkpoint(checkpoint_path, state)
        raise ExchangePublicationStop(
            resume_point={
                **exc.resume_point,
                "checkpoint": str(checkpoint_path),
                "staging_package": str(staging_path),
                "next_stage": "ATOMIC_PROMOTION",
            }
        ) from exc
    if final_path.exists() or final_path.is_symlink():
        raise ExchangePublicationError(
            "Content-addressed final path appeared during publication; staging retained"
        )
    state["stage"] = "PROMOTION_READY"
    _write_checkpoint(checkpoint_path, state)
    os.replace(staging_path, final_path)
    _remove_promoted_checkpoint_best_effort(checkpoint_path)
    _notify_finalized(progress_callback, progress)
    return _result(
        status="FINALIZED",
        package_path=final_path,
        manifest=manifest,
        files_copied=329,
        bytes_copied=337_421_353,
        source_files_verified=source_files_verified,
        source_bytes_verified=source_bytes_verified,
        idempotent_reuse=False,
        elapsed_seconds=max(clock() - started, 0.0),
    )


def _source_artifact_identities(
    evidence: FD1AuthoritativeEvidence,
) -> dict[str, dict[str, Any]]:
    mapping = {
        "finalized_evidence": "final_result",
        "readiness_evidence": "readiness",
        "active_identity_evidence": "active_identity",
        "quarantine_impact_evidence": "quarantine_impact",
    }
    result = {}
    for public_id, local_id in mapping.items():
        artifact = evidence.artifact(local_id)
        result[public_id] = {
            "artifact_id": public_id,
            "logical_uri": (
                f"source-evidence/{public_id}/sha256-{artifact.sha256}.json"
            ),
            "sha256": artifact.sha256,
            "byte_count": artifact.byte_count,
        }
    return result


def _package_artifact(uri: str, payload: bytes) -> dict[str, Any]:
    import hashlib

    return {
        "logical_uri": uri,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_count": len(payload),
        "semantic_digest_member": True,
    }


def _ensure_directory(root: Path, logical_uri: str) -> Path:
    target = resolve_under(root, logical_uri)
    if target.exists() or target.is_symlink():
        assert_no_links(target)
        if not target.is_dir():
            raise ExchangePublicationError("Exchange directory path conflicts with a file")
    else:
        target.mkdir(exist_ok=False)
    return governed_root(target)


def _ensure_parent_tree(root: Path, parent: Path) -> None:
    try:
        relative = parent.relative_to(root)
    except ValueError as exc:
        raise ExchangePublicationError("Destination parent escapes staging root") from exc
    current = root
    for part in relative.parts:
        current = resolve_under(root, (current / part).relative_to(root).as_posix())
        if current.exists() or current.is_symlink():
            assert_no_links(current)
            if not current.is_dir():
                raise ExchangePublicationError("Partition parent conflicts with a file")
        else:
            current.mkdir(exist_ok=False)


def _write_exact_staging(root: Path, uri: str, payload: bytes) -> None:
    path = resolve_under(root, uri)
    _ensure_parent_tree(root, path.parent)
    if path.exists() or path.is_symlink():
        assert_no_links(path)
        if not path.is_file() or path.read_bytes() != payload:
            raise ExchangePublicationError(f"Staging metadata conflicts: {uri}")
        return
    atomic_write_bytes(path, payload)
    if path.read_bytes() != payload:
        raise ExchangePublicationError(f"Staging metadata read-back failed: {uri}")


def _write_checkpoint(path: Path, state: Mapping[str, Any]) -> None:
    atomic_write_bytes(path, canonical_json_bytes(dict(state), trailing_newline=True))


def _load_checkpoint(
    path: Path, provisional: SportsDatasetManifestV1
) -> dict[str, Any]:
    try:
        value = load_canonical_json(path)
    except Exception as exc:
        raise ExchangePublicationError("Publication checkpoint is invalid") from exc
    if not isinstance(value, dict):
        raise ExchangePublicationError("Publication checkpoint must be an object")
    if (
        value.get("schema_version") != PUBLICATION_STATE_VERSION
        or value.get("manifest_sha256") != provisional.manifest_sha256
        or value.get("package_id") != provisional.package_id
    ):
        raise ExchangePublicationError("Publication checkpoint binds another package")
    return value


def _reconcile_promoted_checkpoint(
    staging_path: Path,
    checkpoint_path: Path,
    provisional: SportsDatasetManifestV1,
) -> None:
    """Remove only a checkpoint proven to be orphaned by atomic promotion."""

    if staging_path.exists() or staging_path.is_symlink():
        assert_no_links(staging_path)
        raise ExchangePublicationError(
            "A finalized package and staging package both exist; neither was changed"
        )
    if not (checkpoint_path.exists() or checkpoint_path.is_symlink()):
        return
    assert_no_links(checkpoint_path)
    assert_regular_independent_file(checkpoint_path)
    state = _load_checkpoint(checkpoint_path, provisional)
    if state.get("stage") not in {"PACKAGE_VALIDATED", "PROMOTION_READY"}:
        raise ExchangePublicationError(
            "Orphaned checkpoint does not prove a completed promotion"
        )
    completed = state.get("completed_partitions")
    if not isinstance(completed, dict) or len(completed) != 329:
        raise ExchangePublicationError(
            "Orphaned checkpoint partition count does not reconcile"
        )
    try:
        byte_count = sum(int(row["byte_count"]) for row in completed.values())
        files_copied = int(state["files_copied"])
        bytes_copied = int(state["bytes_copied"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExchangePublicationError(
            "Orphaned checkpoint totals are invalid"
        ) from exc
    if (
        byte_count != 337_421_353
        or files_copied != 329
        or bytes_copied != 337_421_353
    ):
        raise ExchangePublicationError(
            "Orphaned checkpoint totals do not reconcile"
        )
    checkpoint_path.unlink()


def _remove_promoted_checkpoint_best_effort(checkpoint_path: Path) -> None:
    """Try cleanup after the commit point without changing the committed outcome."""

    try:
        if not (checkpoint_path.exists() or checkpoint_path.is_symlink()):
            return
        assert_no_links(checkpoint_path)
        assert_regular_independent_file(checkpoint_path)
        checkpoint_path.unlink()
    except (ExchangePathError, OSError):
        # The package is already atomically promoted.  A matching orphan is
        # safely reconciled by the next idempotent publisher invocation.
        return


def _copy_partition_resumable(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    on_chunk: Callable[[int], None],
) -> int:
    import hashlib

    partial = destination.with_name(f".{destination.name}.partial")
    if partial.exists() or partial.is_symlink():
        info = assert_regular_independent_file(partial)
        if info.st_size > expected_bytes:
            raise ExchangePublicationError("Partial copy exceeds expected byte count")
        mode = "r+b"
    else:
        mode = "x+b"
    before = source.stat()
    source_digest = hashlib.sha256()
    copied_digest = hashlib.sha256()
    newly_written = 0
    with source.open("rb") as incoming, partial.open(mode) as outgoing:
        prefix_bytes = outgoing.seek(0, os.SEEK_END)
        outgoing.seek(0)
        remaining_prefix = prefix_bytes
        while remaining_prefix:
            size = min(COPY_CHUNK_BYTES, remaining_prefix)
            source_block = incoming.read(size)
            partial_block = outgoing.read(size)
            if source_block != partial_block or len(source_block) != size:
                raise ExchangePublicationError("Partial copy prefix differs from source")
            source_digest.update(source_block)
            copied_digest.update(partial_block)
            remaining_prefix -= size
        outgoing.seek(0, os.SEEK_END)
        for block in iter(lambda: incoming.read(COPY_CHUNK_BYTES), b""):
            source_digest.update(block)
            copied_digest.update(block)
            outgoing.write(block)
            newly_written += len(block)
            on_chunk(len(block))
        outgoing.flush()
        os.fsync(outgoing.fileno())
    after = source.stat()
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise ExchangePublicationError("Source changed while its bytes were copied")
    if (
        before.st_size != expected_bytes
        or partial.stat().st_size != expected_bytes
        or source_digest.hexdigest() != expected_sha256
        or copied_digest.hexdigest() != expected_sha256
    ):
        raise ExchangePublicationError("Copied partition checksum/byte count differs")
    os.replace(partial, destination)
    assert_regular_independent_file(destination)
    if sha256_file(destination) != expected_sha256:
        raise ExchangePublicationError("Copied partition read-back checksum differs")
    return newly_written


def _verify_checkpointed_partition(
    destination: Path, partition: Any, checkpoint: object
) -> None:
    if not isinstance(checkpoint, Mapping) or checkpoint != {
        "sha256": partition.file_sha256,
        "byte_count": partition.byte_count,
        "row_count": partition.row_count,
        "physical_schema_fingerprint": partition.physical_schema_fingerprint,
    }:
        raise ExchangePublicationError("Partition checkpoint conflicts with manifest")
    _verify_copied_partition(destination, partition)


def _verify_authoritative_source(
    source: Path,
    partition: Any,
    before: os.stat_result,
) -> None:
    """Rehash a resumed source before treating its checkpoint as verified."""

    actual_sha256 = sha256_file(source)
    after = source.stat()
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise ExchangePublicationError(
            "Authoritative source changed while its checkpoint was verified"
        )
    if after.st_size != partition.byte_count or actual_sha256 != partition.file_sha256:
        raise ExchangePublicationError(
            "Authoritative source checksum changed after its copy checkpoint"
        )


def _verify_copied_partition(destination: Path, partition: Any) -> None:
    info = assert_regular_independent_file(destination)
    if info.st_size != partition.byte_count or sha256_file(destination) != partition.file_sha256:
        raise ExchangePublicationError("Copied partition hash/byte count differs")
    try:
        schema = pl.read_parquet_schema(destination)
        fields = [{"name": name, "dtype": str(dtype)} for name, dtype in schema.items()]
        date_field = "official_date" if partition.family == "player_prior" else "game_date"
        row = (
            pl.scan_parquet(destination, hive_partitioning=False)
            .select(
                pl.len().alias("rows"),
                pl.col(date_field).min().alias("minimum"),
                pl.col(date_field).max().alias("maximum"),
            )
            .collect()
            .row(0, named=True)
        )
    except Exception as exc:
        raise ExchangePublicationError("Copied partition footer is unreadable") from exc
    if (
        canonical_sha256(fields) != partition.physical_schema_fingerprint
        or fields != [item.model_dump(mode="json") for item in partition.physical_schema]
        or int(row["rows"]) != partition.row_count
        or str(row["minimum"]) != partition.minimum_date
        or str(row["maximum"]) != partition.maximum_date
    ):
        raise ExchangePublicationError("Copied partition row/schema evidence differs")


def _emit_copy_chunk(count: int, emit: Callable[..., None]) -> None:
    del count
    emit("COPYING_PARTITIONS")


def _readme_bytes(manifest: SportsDatasetManifestV1, report_bytes: bytes) -> bytes:
    """Compatibility seam for focused publisher tests; implementation is pure."""

    return package_readme_bytes(manifest, report_bytes)


def _staging_file_present(path: Path, logical_name: str) -> bool:
    if not (path.exists() or path.is_symlink()):
        return False
    try:
        assert_no_links(path)
        assert_regular_independent_file(path)
    except ExchangePathError as exc:
        raise ExchangePublicationError(
            f"Staging auxiliary is unsafe: {logical_name}"
        ) from exc
    return True


def _canonical_report_bytes(path: Path) -> bytes:
    try:
        report = load_canonical_json(path)
    except Exception as exc:
        raise ExchangePublicationError(
            "Staging validation report is not canonical JSON"
        ) from exc
    if not isinstance(report, dict):
        raise ExchangePublicationError("Staging validation report must be an object")
    encoded = validation_report_bytes(report)
    if path.read_bytes() != encoded:
        raise ExchangePublicationError(
            "Staging validation report bytes are not deterministic"
        )
    return encoded


def _raise_validation_stop(
    exc: ExchangeValidationDeadline,
    *,
    state: dict[str, Any],
    checkpoint_path: Path,
    staging_path: Path,
    next_stage: str,
) -> NoReturn:
    state["stop_reason"] = "MAX_RUNTIME_REACHED_DURING_VALIDATION"
    _write_checkpoint(checkpoint_path, state)
    raise ExchangePublicationStop(
        resume_point={
            **exc.progress,
            "checkpoint": str(checkpoint_path),
            "staging_package": str(staging_path),
            "next_stage": next_stage,
        }
    ) from exc


def _record_publication_failure(
    state: dict[str, Any], checkpoint_path: Path, exc: BaseException
) -> None:
    """Persist a truthful terminal staging state before propagating a failure."""

    state["stage"] = "FAILED"
    state["stop_reason"] = type(exc).__name__
    _write_checkpoint(checkpoint_path, state)


def _notify_finalized(
    callback: Callable[[PublicationProgress], None] | None,
    snapshot: Callable[[str], PublicationProgress],
) -> None:
    """Best-effort notification after promotion; it cannot roll back the commit."""

    if callback is None:
        return
    try:
        callback(snapshot("FINALIZED"))
    except Exception:
        return


def _validate_existing(
    final_path: Path,
    provisional: SportsDatasetManifestV1,
    *,
    heartbeat_seconds: int,
    max_runtime_minutes: float,
    progress_callback: Callable[[PublicationProgress], None] | None,
    started: float,
) -> SportsDatasetManifestV1:
    try:
        validation = validate_sports_dataset_exchange(
            final_path,
            heartbeat_seconds=heartbeat_seconds,
            max_runtime_minutes=max_runtime_minutes,
            progress_callback=lambda row: _relay_validation_progress(
                row, progress_callback, started, 0, 0
            ),
        )
        payload = load_canonical_json(final_path / "sports-dataset-manifest.json")
        manifest = SportsDatasetManifestV1.model_validate(payload)
    except ExchangeValidationDeadline:
        raise
    except Exception as exc:
        raise ExchangePublicationError(
            "Existing content-addressed package is corrupt or conflicting"
        ) from exc
    if validation.manifest_sha256 != provisional.manifest_sha256:
        raise ExchangePublicationError("Existing package digest differs from expected")
    return manifest


def _relay_validation_progress(
    row: ValidationProgress,
    callback: Callable[[PublicationProgress], None] | None,
    started: float,
    copied_files: int,
    copied_bytes: int,
) -> None:
    if callback is None:
        return
    callback(
        PublicationProgress(
            stage=row.stage,
            elapsed_seconds=max(time.monotonic() - started, 0.0),
            files_copied=copied_files,
            files_total=329,
            bytes_copied=copied_bytes,
            bytes_total=337_421_353,
            current_family=row.current_family,
            hash_mismatches=row.hash_mismatches,
            schema_mismatches=row.schema_mismatches,
            eta_seconds=None,
        )
    )


def _remaining_minutes(deadline: float, clock: Callable[[], float]) -> float:
    """Pass the actual remaining wall time into nested package validation."""

    return max((deadline - clock()) / 60.0, 1e-9)


def _result(
    *,
    status: str,
    package_path: Path,
    manifest: SportsDatasetManifestV1,
    files_copied: int,
    bytes_copied: int,
    source_files_verified: int,
    source_bytes_verified: int,
    idempotent_reuse: bool,
    elapsed_seconds: float,
) -> PublicationResult:
    return PublicationResult(
        status=status,
        package_path=package_path,
        manifest_id=manifest.manifest_id,
        manifest_sha256=manifest.manifest_sha256,
        package_id=manifest.package_id,
        package_total_files=335,
        parquet_file_count=329,
        total_rows=3_738_096,
        total_parquet_bytes=337_421_353,
        files_copied=files_copied,
        bytes_copied=bytes_copied,
        source_files_verified=source_files_verified,
        source_bytes_verified=source_bytes_verified,
        validation_status="VALID",
        idempotent_reuse=idempotent_reuse,
        elapsed_seconds=elapsed_seconds,
    )


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    total = max(int(seconds), 0)
    hours, remainder = divmod(total, 3600)
    minutes, value = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{value:02d}"


def _progress_text(row: PublicationProgress) -> str:
    return "\n".join(
        (
            "SPORTS DATASET EXCHANGE",
            f"Stage: {row.stage}",
            f"Elapsed: {_format_duration(row.elapsed_seconds)}",
            f"Files copied: {row.files_copied} / {row.files_total}",
            f"Bytes copied: {row.bytes_copied} / {row.bytes_total}",
            f"Current family: {row.current_family or '-'}",
            f"Hash mismatches: {row.hash_mismatches}",
            f"Schema mismatches: {row.schema_mismatches}",
            f"ETA: {_format_duration(row.eta_seconds)}",
        )
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="publish-sports-dataset-exchange")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--heartbeat-seconds", type=int, default=30)
    parser.add_argument("--max-runtime-minutes", type=int, default=90)
    args = parser.parse_args(argv)
    try:
        result = publish_sports_dataset_exchange(
            args.output_root,
            resume=args.resume,
            validate_only=args.validate_only,
            heartbeat_seconds=args.heartbeat_seconds,
            max_runtime_minutes=args.max_runtime_minutes,
            progress_callback=lambda row: print(
                _progress_text(row), file=sys.stderr, flush=True
            ),
        )
    except ExchangePublicationStop as exc:
        print(
            json.dumps(
                {
                    "status": "STOPPED",
                    "reason": "MAX_RUNTIME_REACHED",
                    "resume_point": exc.resume_point,
                    "provider_network_calls": 0,
                    "database_writes": 0,
                },
                indent=2,
            )
        )
        raise SystemExit(75) from exc
    except (ExchangePathError, ExchangePublicationError, ExchangeValidationError) as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error": str(exc),
                    "provider_network_calls": 0,
                    "database_writes": 0,
                },
                indent=2,
            )
        )
        raise SystemExit(2) from exc
    print(json.dumps(result.as_dict(), indent=2))


if __name__ == "__main__":
    main()


__all__ = [
    "ExchangePublicationError",
    "ExchangePublicationStop",
    "PublicationProgress",
    "PublicationResult",
    "publish_sports_dataset_exchange",
]
