"""Prepare immutable inputs for Gate 3C-FD1 recovery finalization.

This command is deliberately narrower than the finalizer.  It reads one trusted
dry-run ledger, one explicit newline-delimited Parquet list, and one exact JSON
envelope naming the small identity source files.  It performs no acquisition,
database access, provider construction, raw-directory discovery, or Parquet writes.

Run from the repository root or ``server`` directory::

    python server/scripts/prepare_fd1_recovery.py \
        --ledger .runtime/gate3c-fd1/artifacts/<trusted-ledger>.json \
        --parquet-list .runtime/gate3c-fd1/recovery/parquet-files.txt \
        --store-root data/curated/gen2-fd1 \
        --output-root .runtime/gate3c-fd1/recovery \
        --identity-envelope .runtime/gate3c-fd1/recovery/identity-inputs.json

The final stdout line is canonical JSON.  Heartbeats are written to stderr so a
caller may capture the result without filtering progress records.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import threading
import time
from typing import Any, TextIO


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPOSITORY_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.gen2.fd1_identity import (  # noqa: E402
    FD1IdentityPriorSourceBundle,
    SourceFileSpec,
)
from app.gen2.fd1_recovery import (  # noqa: E402
    RECOVERY_COMMAND_SCHEMA_VERSION,
    build_fd1_recovery_finalizer_revision,
)
from app.gen2.fd1_recovery_identity import (  # noqa: E402
    DEFAULT_IDENTITY_BATCH_SIZE,
    IdentitySourceRelease,
    IdentitySpoolProgress,
    build_identity_spool_from_frozen_sources,
)
from app.gen2.fd1_recovery_manifest import (  # noqa: E402
    DEFAULT_EXPECTED_PARQUET_FILES,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_MINIMUM_FREE_BYTES,
    FinalizerLineage,
    GenerationLineage,
    IdentitySpoolBinding as ManifestIdentitySpoolBinding,
    InterruptedExecutionContext,
    RecoveryProgress,
    SourceArchiveBinding,
    SourceReleaseBinding,
    TrustedDryRunLedger,
    build_recovery_manifest,
    load_recovery_hash_cache,
    parse_partition_uri,
    write_recovery_hash_cache,
    write_recovery_manifest,
)


PREPARE_INPUT_SCHEMA_VERSION = "gate3c_fd1_recovery_prepare_inputs_v1"
PREPARE_RESULT_SCHEMA_VERSION = "gate3c_fd1_recovery_prepare_result_v1"
TRUSTED_LEDGER_SCHEMA_VERSION = "gate3c_fd1_actual_publication_operator_v5"
MAX_LEDGER_BYTES = 64 * 1024**2
MAX_ENVELOPE_BYTES = 4 * 1024**2
MAX_PARQUET_LIST_BYTES = 4 * 1024**2
OPTIONAL_GOVERNANCE_SOURCE_KEY = "RETROSHEET_EVENT_ARCHIVES"
REQUIRED_LEDGER_SOURCE_KEYS = (
    "CHADWICK_REGISTER_ARCHIVES",
    "RETROSHEET_AUXILIARY_DOWNLOADS",
    "RETROSHEET_CSV_DOWNLOADS",
    "SABR_LAHMAN_BOX_DOWNLOADS",
)
IDENTITY_FILE_ROLES = (
    "chadwick_people",
    "lahman_batting",
    "lahman_fielding",
    "lahman_franchises",
    "lahman_home_games",
    "lahman_parks",
    "lahman_people",
    "lahman_pitching",
    "lahman_teams",
    "retrosheet_ballparks",
    "retrosheet_teams",
)
_SINGLE_IDENTITY_FILE_ROLES = frozenset(IDENTITY_FILE_ROLES) - {
    "chadwick_people"
}
_ROLE_SOURCE_KEY: Mapping[str, str] = {
    "chadwick_people": "CHADWICK_REGISTER_ARCHIVES",
    "lahman_batting": "SABR_LAHMAN_BOX_DOWNLOADS",
    "lahman_fielding": "SABR_LAHMAN_BOX_DOWNLOADS",
    "lahman_franchises": "SABR_LAHMAN_BOX_DOWNLOADS",
    "lahman_home_games": "SABR_LAHMAN_BOX_DOWNLOADS",
    "lahman_parks": "SABR_LAHMAN_BOX_DOWNLOADS",
    "lahman_people": "SABR_LAHMAN_BOX_DOWNLOADS",
    "lahman_pitching": "SABR_LAHMAN_BOX_DOWNLOADS",
    "lahman_teams": "SABR_LAHMAN_BOX_DOWNLOADS",
    "retrosheet_ballparks": "RETROSHEET_AUXILIARY_DOWNLOADS",
    "retrosheet_teams": "RETROSHEET_AUXILIARY_DOWNLOADS",
}
_ROLE_LOGICAL_TABLE: Mapping[str, str] = {
    "lahman_batting": "Batting.csv",
    "lahman_fielding": "Fielding.csv",
    "lahman_franchises": "TeamsFranchises.csv",
    "lahman_home_games": "HomeGames.csv",
    "lahman_parks": "Parks.csv",
    "lahman_people": "People.csv",
    "lahman_pitching": "Pitching.csv",
    "lahman_teams": "Teams.csv",
    "retrosheet_ballparks": "ballparks.csv",
    "retrosheet_teams": "teams.csv",
}
_EXPECTED_CHADWICK_TABLES = frozenset(f"people-{digit:x}.csv" for digit in range(16))
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROHIBITED_IDENTITY_PATH = re.compile(
    r"(?:^|[/\\])(?:daily[ _-]?logs?|plays\.csv)(?:$|[/\\])", re.IGNORECASE
)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class FD1RecoveryPreparationError(RuntimeError):
    """An explicit recovery-preparation input failed closed."""


@dataclass(frozen=True, slots=True)
class FrozenIdentityFile:
    role: str
    path: Path
    logical_uri: str
    source_key: str
    source_release: str
    logical_table: str
    sha256: str
    content_length: int

    def source_spec(self) -> SourceFileSpec:
        return SourceFileSpec(
            path=self.path,
            source_key=self.source_key,
            source_release=self.source_release,
            logical_table=self.logical_table,
            expected_sha256=self.sha256,
            expected_content_length=self.content_length,
        )


@dataclass(frozen=True, slots=True)
class PreparationEnvelope:
    trusted_ledger: TrustedDryRunLedger
    interrupted_execution: Mapping[str, Any]
    identity_files: tuple[FrozenIdentityFile, ...]
    published_lahman_tables: tuple[str, ...]
    expected_identity_rows: int
    additional_release: SourceReleaseBinding | None
    additional_archives: tuple[SourceArchiveBinding, ...]


class PreparationHeartbeat:
    """A small autonomous stderr heartbeat for the provider-free preparation."""

    def __init__(
        self,
        *,
        interval_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        interval = float(interval_seconds)
        if not 0 < interval <= DEFAULT_HEARTBEAT_SECONDS:
            raise ValueError("heartbeat interval must be greater than zero and at most 30")
        self._interval = interval
        self._stream = stream or sys.stderr
        self._clock = clock
        self._started = clock()
        self._stage = "PREFLIGHT"
        self._details: dict[str, object] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: BaseException | None = None

    def __enter__(self) -> PreparationHeartbeat:
        self._emit()
        self._thread = threading.Thread(
            target=self._worker,
            name="fd1-recovery-prepare-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=min(self._interval + 1.0, 31.0))

    def update(self, stage: str, **details: object) -> None:
        with self._lock:
            self._stage = _required_text(stage, "heartbeat stage")
            self._details = dict(details)
        self.raise_if_failed()

    def complete(self, **details: object) -> None:
        self.update("COMPLETE", **details)
        self._emit()

    def raise_if_failed(self) -> None:
        if self._failure is not None:
            raise FD1RecoveryPreparationError(
                "Preparation heartbeat output failed"
            ) from self._failure

    def _worker(self) -> None:
        while not self._wake.wait(self._interval):
            try:
                self._emit()
            except BaseException as exc:  # pragma: no cover - stream failure boundary
                self._failure = exc
                self._wake.set()
                return

    def _emit(self) -> None:
        with self._lock:
            record = {
                "elapsed_seconds": round(max(self._clock() - self._started, 0.0), 3),
                "event": "GATE_3C_FD1_RECOVERY_PREPARATION_HEARTBEAT",
                "stage": self._stage,
                **self._details,
            }
            self._stream.write(_canonical_json(record) + "\n")
            self._stream.flush()


def prepare_fd1_recovery(
    *,
    ledger_path: Path,
    parquet_list_path: Path,
    store_root: Path,
    output_root: Path,
    identity_envelope_path: Path,
    hash_cache_path: Path | None = None,
    repository_root: Path = REPOSITORY_ROOT,
    expected_file_count: int = DEFAULT_EXPECTED_PARQUET_FILES,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    progress_stream: TextIO | None = None,
    free_space: Callable[[Path], int] | None = None,
) -> dict[str, Any]:
    """Create the compact spool and frozen manifest from explicit local files."""

    repository = _existing_directory(repository_root, "repository root")
    store = _existing_directory(store_root, "Parquet store root")
    output = _recovery_output_root(output_root, repository)
    if isinstance(expected_file_count, bool) or expected_file_count <= 0:
        raise ValueError("expected_file_count must be positive")
    if minimum_free_bytes < DEFAULT_MINIMUM_FREE_BYTES:
        raise ValueError("preparation cannot weaken the 100-GiB reserve")

    with PreparationHeartbeat(
        interval_seconds=heartbeat_seconds,
        stream=progress_stream,
    ) as progress:
        envelope = load_preparation_envelope(
            identity_envelope_path,
            repository_root=repository,
        )
        progress.update("LEDGER_VERIFYING")
        ledger = _load_trusted_ledger(
            ledger_path,
            expected=envelope.trusted_ledger,
            repository_root=repository,
        )
        releases, archives, generation_revision, expected_identity_rows = (
            _lineage_from_ledger(ledger, envelope)
        )
        logical_uris = _load_parquet_list(
            parquet_list_path,
            expected_file_count=expected_file_count,
        )
        completed_bytes = _explicit_parquet_bytes(store, logical_uris)

        progress.update(
            "IDENTITY_SPOOL_BUILDING",
            files_total=expected_file_count,
            mappings_total=expected_identity_rows,
        )
        bundle, retrosheet_teams, retrosheet_ballparks, evidence = (
            _identity_inputs(envelope, releases)
        )

        def spool_progress(item: IdentitySpoolProgress) -> None:
            progress.update(
                "IDENTITY_SPOOL_BUILDING",
                mappings_processed=item.rows_processed,
                mappings_total=expected_identity_rows,
                spool_bytes=item.bytes_processed,
                spool_batches=item.batches_processed,
                source_key=item.current_source_key,
            )

        spool = build_identity_spool_from_frozen_sources(
            source_bundle=bundle,
            retrosheet_teams=retrosheet_teams,
            retrosheet_ballparks=retrosheet_ballparks,
            evidence_logical_uri_by_file_checksum=evidence,
            source_releases=tuple(
                IdentitySourceRelease(
                    source_key=item.source_key,
                    source_version_id=item.source_version_id,
                    source_release_identity_checksum=item.identity_checksum,
                )
                for item in releases
                if item.source_key in {entry.source_key for entry in envelope.identity_files}
            ),
            output_directory=output / "identity",
            batch_size=DEFAULT_IDENTITY_BATCH_SIZE,
            expected_row_count=expected_identity_rows,
            min_free_bytes=minimum_free_bytes,
            progress_callback=spool_progress,
        )
        spool_logical_uri = _repository_logical_uri(spool.path, repository)
        manifest_spool = ManifestIdentitySpoolBinding(
            logical_uri=spool_logical_uri,
            schema_version=spool.schema_version,
            sha256=spool.sha256,
            byte_count=spool.byte_count,
            row_count=spool.row_count,
        )

        interrupted = InterruptedExecutionContext(
            **envelope.interrupted_execution,
            completed_files=len(logical_uris),
            completed_bytes=completed_bytes,
        )
        generation_lineage = GenerationLineage(
            code_revision=generation_revision,
            trusted_dry_run_ledger=envelope.trusted_ledger,
            source_releases=releases,
            source_archives=archives,
            interrupted_execution=interrupted,
        )
        finalizer_revision = build_fd1_recovery_finalizer_revision()
        finalizer_lineage = FinalizerLineage(
            code_revision=finalizer_revision,
            command_schema_version=RECOVERY_COMMAND_SCHEMA_VERSION,
        )
        prior_cache = (
            load_recovery_hash_cache(hash_cache_path)
            if hash_cache_path is not None
            else None
        )

        def manifest_progress(item: RecoveryProgress) -> None:
            progress.update(
                item.stage,
                files_validated=item.files_validated,
                files_total=item.files_total,
                bytes_validated=item.bytes_validated,
                bytes_total=item.bytes_total,
                files_hashed=item.files_hashed,
                bytes_hashed=item.bytes_hashed,
                cache_hits=item.cache_hits,
                current_logical_uri=item.current_logical_uri,
            )

        progress.update("FILES_VALIDATING", files_total=expected_file_count)
        build = build_recovery_manifest(
            store_root=store,
            logical_uris=logical_uris,
            generation_lineage=generation_lineage,
            finalizer_lineage=finalizer_lineage,
            identity_spool=manifest_spool,
            hash_cache=prior_cache,
            expected_file_count=expected_file_count,
            minimum_free_bytes=minimum_free_bytes,
            heartbeat_seconds=min(heartbeat_seconds, DEFAULT_HEARTBEAT_SECONDS),
            progress_callback=manifest_progress,
            free_space=free_space,
        )
        progress.update("ARTIFACTS_WRITING", files_validated=expected_file_count)
        cache_artifact = write_recovery_hash_cache(
            build.hash_cache,
            output_root=output / "manifests",
            minimum_free_bytes=minimum_free_bytes,
            free_space=free_space,
        )
        manifest_artifact = write_recovery_manifest(
            build.manifest,
            output_root=output / "manifests",
            minimum_free_bytes=minimum_free_bytes,
            free_space=free_space,
        )
        progress.complete(
            files_validated=build.manifest.aggregate.file_count,
            mappings_processed=spool.row_count,
            run_id=build.manifest.recovery_id,
        )
        progress.raise_if_failed()

    return {
        "database_writes": 0,
        "finalizer_code_revision": finalizer_revision,
        "generation_code_revision": generation_revision,
        "hash_cache": {
            "bytes": cache_artifact.byte_count,
            "bytes_hashed": build.bytes_hashed,
            "cache_hits": build.cache_hits,
            "elapsed_seconds": build.elapsed_seconds,
            "files_hashed": build.files_hashed,
            "path": _repository_logical_uri(cache_artifact.path, repository),
            "rows": len(build.hash_cache.entries),
            "sha256": cache_artifact.sha256,
        },
        "identity_spool": {
            "batch_count": spool.batch_count,
            "bytes": spool.byte_count,
            "path": spool_logical_uri,
            "rows": spool.row_count,
            "sha256": spool.sha256,
        },
        "manifest": {
            "bytes": manifest_artifact.byte_count,
            "manifest_checksum": build.manifest.manifest_checksum,
            "path": _repository_logical_uri(manifest_artifact.path, repository),
            "rows": build.manifest.aggregate.row_count,
            "sha256": manifest_artifact.sha256,
        },
        "network_requests": 0,
        "parquet_files": build.manifest.aggregate.file_count,
        "provider_calls": 0,
        "raw_directory_walks": 0,
        "run_id": build.manifest.recovery_id,
        "schema_version": PREPARE_RESULT_SCHEMA_VERSION,
    }


def load_preparation_envelope(
    path: Path,
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> PreparationEnvelope:
    """Load the strict, machine-path-free preparation envelope schema."""

    document, _payload = _read_unique_json(
        path,
        label="identity input envelope",
        maximum_bytes=MAX_ENVELOPE_BYTES,
    )
    root = _object(document, "identity input envelope")
    _exact_keys(
        root,
        {
            "additional_governance_source",
            "identity",
            "interrupted_execution",
            "schema_version",
            "trusted_ledger",
        },
        "identity input envelope",
    )
    if root["schema_version"] != PREPARE_INPUT_SCHEMA_VERSION:
        raise FD1RecoveryPreparationError("Identity input envelope schema is invalid")

    trusted = TrustedDryRunLedger.from_dict(root["trusted_ledger"])
    if trusted.schema_version != TRUSTED_LEDGER_SCHEMA_VERSION:
        raise FD1RecoveryPreparationError("Only the trusted v5 dry-run ledger is accepted")

    interrupted = _object(root["interrupted_execution"], "interrupted execution")
    _exact_keys(
        interrupted,
        {
            "attempted_command",
            "final_execute_evidence_present",
            "sql_rolled_back",
            "started_at_utc",
            "stopped_at_utc",
            "suspected_slow_stage",
        },
        "interrupted execution",
    )
    if interrupted["sql_rolled_back"] is not True:
        raise FD1RecoveryPreparationError("Interrupted execution rollback is not proven")
    if interrupted["final_execute_evidence_present"] is not False:
        raise FD1RecoveryPreparationError("Final execute evidence makes recovery unsafe")

    identity = _object(root["identity"], "identity inputs")
    _exact_keys(
        identity,
        {"expected_row_count", "published_lahman_tables", "source_files"},
        "identity inputs",
    )
    expected_rows = _positive_int(identity["expected_row_count"], "identity row count")
    tables_value = _array(identity["published_lahman_tables"], "published Lahman tables")
    published_tables = tuple(
        sorted(_required_text(value, "Lahman table") for value in tables_value)
    )
    if len(published_tables) != len(set(published_tables)):
        raise FD1RecoveryPreparationError("Published Lahman tables are duplicated")
    if any(Path(name).name != name for name in published_tables):
        raise FD1RecoveryPreparationError("Published Lahman tables must be base names")

    envelope_directory = _regular_file(path, "identity input envelope").parent
    identity_files = tuple(
        _identity_file_from_dict(
            value,
            envelope_directory=envelope_directory,
        )
        for value in _array(identity["source_files"], "identity source files")
    )
    _validate_identity_file_set(identity_files)

    extra = root["additional_governance_source"]
    additional_release: SourceReleaseBinding | None = None
    additional_archives: tuple[SourceArchiveBinding, ...] = ()
    if extra is not None:
        extra_object = _object(extra, "additional governance source")
        _exact_keys(
            extra_object,
            {"source_archives", "source_release"},
            "additional governance source",
        )
        additional_release = SourceReleaseBinding.from_dict(
            extra_object["source_release"]
        )
        if additional_release.source_key != OPTIONAL_GOVERNANCE_SOURCE_KEY:
            raise FD1RecoveryPreparationError(
                "The optional fifth source must be RETROSHEET_EVENT_ARCHIVES"
            )
        additional_archives = tuple(
            sorted(
                (
                    SourceArchiveBinding.from_dict(value)
                    for value in _array(
                        extra_object["source_archives"],
                        "additional source archives",
                    )
                ),
                key=lambda item: (item.source_key, item.object_key),
            )
        )
        if not additional_archives:
            raise FD1RecoveryPreparationError(
                "The optional governance source requires exact archive bindings"
            )
        if any(
            item.source_key != additional_release.source_key
            or item.source_release != additional_release.dataset_version
            for item in additional_archives
        ):
            raise FD1RecoveryPreparationError(
                "Additional archives do not match their source release"
            )

    return PreparationEnvelope(
        trusted_ledger=trusted,
        interrupted_execution=interrupted,
        identity_files=identity_files,
        published_lahman_tables=published_tables,
        expected_identity_rows=expected_rows,
        additional_release=additional_release,
        additional_archives=additional_archives,
    )


def _load_trusted_ledger(
    path: Path,
    *,
    expected: TrustedDryRunLedger,
    repository_root: Path,
) -> Mapping[str, Any]:
    actual_path = _regular_file(path, "trusted dry-run ledger")
    expected_path = _resolve_logical_uri(repository_root, expected.logical_uri)
    if actual_path.resolve(strict=True) != expected_path.resolve(strict=True):
        raise FD1RecoveryPreparationError(
            "Trusted ledger path differs from its declared logical URI"
        )
    document, payload = _read_unique_json(
        actual_path,
        label="trusted dry-run ledger",
        maximum_bytes=MAX_LEDGER_BYTES,
    )
    digest = sha256(payload).hexdigest()
    if digest != expected.sha256:
        raise FD1RecoveryPreparationError("Trusted dry-run ledger SHA-256 mismatch")
    if not actual_path.name.endswith(f"-sha256-{digest}.json"):
        raise FD1RecoveryPreparationError("Trusted ledger filename is not content-addressed")
    ledger = _object(document, "trusted dry-run ledger")
    if ledger.get("schema_version") != expected.schema_version:
        raise FD1RecoveryPreparationError("Trusted dry-run ledger schema mismatch")
    if ledger.get("mode") != "dry-run" or ledger.get("success") is not True:
        raise FD1RecoveryPreparationError("Trusted ledger is not a successful dry run")
    if ledger.get("sanitized") is not True:
        raise FD1RecoveryPreparationError("Trusted ledger is not sanitized")
    publication = _object(ledger.get("publication"), "ledger publication")
    prohibited = _object(ledger.get("prohibited_activity"), "prohibited activity")
    if publication.get("mode") != "dry-run":
        raise FD1RecoveryPreparationError("Ledger publication mode is not dry-run")
    if publication.get("provider_transport_constructed") is not False:
        raise FD1RecoveryPreparationError("Ledger constructed a provider transport")
    if publication.get("network_requests") != 0:
        raise FD1RecoveryPreparationError("Ledger reports network requests")
    if prohibited.get("provider_transport_constructed") is not False:
        raise FD1RecoveryPreparationError("Ledger prohibited-activity evidence is unsafe")
    if prohibited.get("network_requests") != 0:
        raise FD1RecoveryPreparationError("Ledger prohibited-activity network count is nonzero")
    return ledger


def _lineage_from_ledger(
    ledger: Mapping[str, Any],
    envelope: PreparationEnvelope,
) -> tuple[
    tuple[SourceReleaseBinding, ...],
    tuple[SourceArchiveBinding, ...],
    str,
    int,
]:
    source_versions = _object(ledger.get("source_versions"), "ledger source versions")
    if set(source_versions) != set(REQUIRED_LEDGER_SOURCE_KEYS):
        raise FD1RecoveryPreparationError("Ledger source-version set differs from FD1 v5")
    releases: list[SourceReleaseBinding] = []
    for source_key in sorted(source_versions):
        value = _object(source_versions[source_key], f"source version {source_key}")
        _exact_keys(
            value,
            {"dataset_version", "id", "identity_checksum", "source_mode"},
            f"source version {source_key}",
        )
        releases.append(
            SourceReleaseBinding(
                source_key=source_key,
                source_version_id=value["id"],
                dataset_version=value["dataset_version"],
                identity_checksum=value["identity_checksum"],
                source_mode=value["source_mode"],
            )
        )
    if envelope.additional_release is not None:
        if envelope.additional_release.source_key in source_versions:
            raise FD1RecoveryPreparationError("Additional governance source is duplicated")
        releases.append(envelope.additional_release)
    releases.sort(key=lambda item: item.source_key)

    downloads = _array(ledger.get("registered_downloads_used"), "registered downloads")
    archives: list[SourceArchiveBinding] = []
    for raw in downloads:
        value = _object(raw, "registered download")
        _exact_keys(
            value,
            {
                "archive_sha256",
                "content_length",
                "download_identity_checksum",
                "object_key",
                "payload_sha256",
                "raw_logical_uri",
                "source_key",
                "source_release",
                "upstream_revision",
            },
            "registered download",
        )
        if value["source_key"] not in source_versions:
            raise FD1RecoveryPreparationError("Ledger download has an unbound source")
        archives.append(
            SourceArchiveBinding(
                source_key=value["source_key"],
                object_key=value["object_key"],
                source_release=value["source_release"],
                raw_logical_uri=value["raw_logical_uri"],
                archive_sha256=value["archive_sha256"],
                payload_sha256=value["payload_sha256"],
                content_length=value["content_length"],
                download_identity_checksum=value["download_identity_checksum"],
            )
        )
    archives.extend(envelope.additional_archives)
    archives.sort(key=lambda item: (item.source_key, item.object_key))
    if len({(item.source_key, item.object_key) for item in archives}) != len(archives):
        raise FD1RecoveryPreparationError("Source archive bindings are duplicated")

    publication = _object(ledger.get("publication"), "ledger publication")
    revision = publication.get("code_revision")
    if not isinstance(revision, str) or _SHA256.fullmatch(revision) is None:
        raise FD1RecoveryPreparationError("Ledger generation revision is invalid")
    publication_rows = _positive_int(
        publication.get("identity_mappings_seen"),
        "ledger identity mapping count",
    )
    identity = _object(ledger.get("identity"), "ledger identity")
    identity_rows = _positive_int(identity.get("mappings"), "ledger identity mappings")
    if publication_rows != identity_rows or identity_rows != envelope.expected_identity_rows:
        raise FD1RecoveryPreparationError("Identity row counts do not reconcile")
    return tuple(releases), tuple(archives), revision, identity_rows


def _identity_inputs(
    envelope: PreparationEnvelope,
    releases: Sequence[SourceReleaseBinding],
) -> tuple[
    FD1IdentityPriorSourceBundle,
    SourceFileSpec,
    SourceFileSpec,
    Mapping[str, str],
]:
    release_by_key = {item.source_key: item for item in releases}
    by_role: dict[str, list[SourceFileSpec]] = {}
    evidence: dict[str, str] = {}
    for item in envelope.identity_files:
        release = release_by_key.get(item.source_key)
        if release is None or release.dataset_version != item.source_release:
            raise FD1RecoveryPreparationError(
                f"Identity file {item.role} differs from ledger source release"
            )
        by_role.setdefault(item.role, []).append(item.source_spec())
        previous = evidence.setdefault(item.sha256, item.logical_uri)
        if previous != item.logical_uri:
            raise FD1RecoveryPreparationError(
                "One identity checksum resolves to conflicting logical URIs"
            )

    def one(role: str) -> SourceFileSpec:
        values = by_role[role]
        if len(values) != 1:  # protected again at the construction boundary
            raise FD1RecoveryPreparationError(f"Identity role {role} is not singular")
        return values[0]

    bundle = FD1IdentityPriorSourceBundle(
        chadwick_people=tuple(
            sorted(by_role["chadwick_people"], key=lambda item: item.logical_table)
        ),
        lahman_people=one("lahman_people"),
        lahman_batting=one("lahman_batting"),
        lahman_pitching=one("lahman_pitching"),
        lahman_fielding=one("lahman_fielding"),
        lahman_teams=one("lahman_teams"),
        lahman_franchises=one("lahman_franchises"),
        lahman_parks=one("lahman_parks"),
        lahman_home_games=one("lahman_home_games"),
        published_lahman_tables=envelope.published_lahman_tables,
    )
    return bundle, one("retrosheet_teams"), one("retrosheet_ballparks"), evidence


def _identity_file_from_dict(
    value: object,
    *,
    envelope_directory: Path,
) -> FrozenIdentityFile:
    item = _object(value, "identity source file")
    _exact_keys(
        item,
        {
            "content_length",
            "logical_table",
            "logical_uri",
            "path",
            "role",
            "sha256",
            "source_key",
            "source_release",
        },
        "identity source file",
    )
    role = _required_text(item["role"], "identity role")
    if role not in IDENTITY_FILE_ROLES:
        raise FD1RecoveryPreparationError(f"Unsupported identity role: {role}")
    declared_path = Path(_required_text(item["path"], "identity source path"))
    path = declared_path if declared_path.is_absolute() else envelope_directory / declared_path
    path = _regular_file(path, f"identity source {role}")
    logical_uri = _logical_uri(item["logical_uri"], "identity evidence logical URI")
    logical_table = _required_text(item["logical_table"], "identity logical table")
    if Path(logical_table).name != logical_table:
        raise FD1RecoveryPreparationError("Identity logical_table must be a base name")
    prohibited_values = (str(path), logical_uri, logical_table)
    if any(_PROHIBITED_IDENTITY_PATH.search(candidate) for candidate in prohibited_values):
        raise FD1RecoveryPreparationError("Daily Logs and plays.csv are prohibited inputs")
    expected_table = _ROLE_LOGICAL_TABLE.get(role)
    if expected_table is not None and logical_table != expected_table:
        raise FD1RecoveryPreparationError("Identity role/logical-table binding is invalid")
    if role == "chadwick_people" and logical_table not in _EXPECTED_CHADWICK_TABLES:
        raise FD1RecoveryPreparationError("Chadwick people shard name is invalid")
    source_key = _required_text(item["source_key"], "identity source key")
    if source_key != _ROLE_SOURCE_KEY[role]:
        raise FD1RecoveryPreparationError("Identity role/source binding is invalid")
    digest = _checksum(item["sha256"], "identity source SHA-256")
    length = _positive_int(item["content_length"], "identity source byte count")
    file_stat = path.stat()
    if file_stat.st_size != length:
        raise FD1RecoveryPreparationError("Identity source byte count mismatch")
    return FrozenIdentityFile(
        role=role,
        path=path,
        logical_uri=logical_uri,
        source_key=source_key,
        source_release=_required_text(item["source_release"], "identity source release"),
        logical_table=logical_table,
        sha256=digest,
        content_length=length,
    )


def _validate_identity_file_set(files: Sequence[FrozenIdentityFile]) -> None:
    if not files:
        raise FD1RecoveryPreparationError("Identity source file set is empty")
    by_role: dict[str, int] = {role: 0 for role in IDENTITY_FILE_ROLES}
    for item in files:
        by_role[item.role] += 1
    chadwick_tables = {
        item.logical_table for item in files if item.role == "chadwick_people"
    }
    if chadwick_tables != _EXPECTED_CHADWICK_TABLES:
        raise FD1RecoveryPreparationError("All 16 exact Chadwick people shards are required")
    if any(by_role[role] != 1 for role in _SINGLE_IDENTITY_FILE_ROLES):
        raise FD1RecoveryPreparationError("Every singular identity file role is required once")
    paths = [item.path.resolve(strict=True) for item in files]
    uris = [item.logical_uri for item in files]
    checksums = [item.sha256 for item in files]
    if len(paths) != len(set(paths)) or len(uris) != len(set(uris)):
        raise FD1RecoveryPreparationError("Identity source paths/URIs are duplicated")
    if len(checksums) != len(set(checksums)):
        raise FD1RecoveryPreparationError("Identity source checksums are duplicated")


def _load_parquet_list(path: Path, *, expected_file_count: int) -> tuple[str, ...]:
    source = _regular_file(path, "Parquet list")
    payload = _read_bounded(source, MAX_PARQUET_LIST_BYTES, "Parquet list")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise FD1RecoveryPreparationError("Parquet list cannot contain a UTF-8 BOM")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FD1RecoveryPreparationError("Parquet list must be UTF-8") from exc
    values = tuple(text.splitlines())
    if any(not value or value != value.strip() for value in values):
        raise FD1RecoveryPreparationError("Parquet list has blank or padded lines")
    if len(values) != expected_file_count:
        raise FD1RecoveryPreparationError(
            f"Parquet list must contain exactly {expected_file_count} paths"
        )
    if len(values) != len(set(values)):
        raise FD1RecoveryPreparationError("Parquet list contains duplicate paths")
    for logical_uri in values:
        parse_partition_uri(logical_uri)
    return tuple(sorted(values))


def _explicit_parquet_bytes(store_root: Path, logical_uris: Sequence[str]) -> int:
    total = 0
    for logical_uri in logical_uris:
        path = _resolve_logical_uri(store_root, logical_uri)
        regular = _regular_file(path, "manifested Parquet")
        size = regular.stat().st_size
        if size <= 0:
            raise FD1RecoveryPreparationError("Manifested Parquet is empty")
        total += size
    return total


def _recovery_output_root(path: Path, repository_root: Path) -> Path:
    absolute = Path(path).absolute()
    expected = repository_root / ".runtime" / "gate3c-fd1" / "recovery"
    try:
        absolute.relative_to(expected.absolute())
    except ValueError as exc:
        raise FD1RecoveryPreparationError(
            "Recovery output must remain under .runtime/gate3c-fd1/recovery"
        ) from exc
    _assert_no_link_ancestors(absolute.parent)
    return absolute


def _repository_logical_uri(path: Path, repository_root: Path) -> str:
    absolute = _regular_file(path, "recovery artifact")
    try:
        relative = absolute.resolve(strict=True).relative_to(repository_root)
    except ValueError as exc:
        raise FD1RecoveryPreparationError("Recovery artifact escaped the repository") from exc
    logical_uri = relative.as_posix()
    if not logical_uri.startswith(".runtime/gate3c-fd1/recovery/"):
        raise FD1RecoveryPreparationError("Recovery artifact escaped its ignored root")
    return logical_uri


def _read_unique_json(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> tuple[object, bytes]:
    source = _regular_file(path, label)
    payload = _read_bounded(source, maximum_bytes, label)
    try:
        document = json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FD1RecoveryPreparationError(f"{label} is not valid UTF-8 JSON") from exc
    return document, payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FD1RecoveryPreparationError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_bounded(path: Path, maximum_bytes: int, label: str) -> bytes:
    before = path.stat()
    if before.st_size <= 0 or before.st_size > maximum_bytes:
        raise FD1RecoveryPreparationError(f"{label} has an invalid byte length")
    with path.open("rb") as handle:
        payload = handle.read(maximum_bytes + 1)
    after = path.stat()
    if len(payload) != before.st_size or len(payload) > maximum_bytes:
        raise FD1RecoveryPreparationError(f"{label} changed or exceeded its byte bound")
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise FD1RecoveryPreparationError(f"{label} changed while being read")
    return payload


def _regular_file(path: Path, label: str) -> Path:
    lexical = Path(path).absolute()
    _assert_no_link_ancestors(lexical)
    try:
        metadata = lexical.lstat()
    except OSError as exc:
        raise FD1RecoveryPreparationError(f"Cannot stat {label}") from exc
    attributes = getattr(metadata, "st_file_attributes", 0)
    if lexical.is_symlink() or attributes & _REPARSE_POINT or not lexical.is_file():
        raise FD1RecoveryPreparationError(f"{label} is not a regular non-link file")
    return lexical


def _existing_directory(path: Path, label: str) -> Path:
    lexical = Path(path).absolute()
    _assert_no_link_ancestors(lexical)
    if lexical.is_symlink() or not lexical.is_dir():
        raise FD1RecoveryPreparationError(f"{label} is not an existing directory")
    return lexical.resolve(strict=True)


def _assert_no_link_ancestors(path: Path) -> None:
    current = Path(path).absolute()
    existing: list[Path] = []
    while True:
        if current.exists() or current.is_symlink():
            existing.append(current)
        if current == current.parent:
            break
        current = current.parent
    for candidate in existing:
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise FD1RecoveryPreparationError("Cannot inspect governed path") from exc
        attributes = getattr(metadata, "st_file_attributes", 0)
        if candidate.is_symlink() or attributes & _REPARSE_POINT:
            raise FD1RecoveryPreparationError("Governed paths cannot use links")


def _resolve_logical_uri(root: Path, logical_uri: str) -> Path:
    value = _logical_uri(logical_uri, "logical URI")
    pure = PurePosixPath(value)
    target = root.joinpath(*pure.parts).absolute()
    try:
        target.relative_to(root.absolute())
    except ValueError as exc:
        raise FD1RecoveryPreparationError("Logical URI escapes its root") from exc
    return target


def _logical_uri(value: object, label: str) -> str:
    text = _required_text(value, label)
    pure = PurePosixPath(text)
    lowered = text.lower()
    if (
        pure.is_absolute()
        or "\\" in text
        or "://" in text
        or "?" in text
        or "#" in text
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(token in lowered for token in ("password", "secret", "token="))
    ):
        raise FD1RecoveryPreparationError(f"{label} is not a safe root-relative path")
    return text


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FD1RecoveryPreparationError(f"{label} must be an object")
    return dict(value)


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise FD1RecoveryPreparationError(f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise FD1RecoveryPreparationError(f"{label} fields differ from the schema")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise FD1RecoveryPreparationError(f"{label} must be non-empty trimmed text")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise FD1RecoveryPreparationError(f"{label} contains prohibited characters")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FD1RecoveryPreparationError(f"{label} must be a positive integer")
    return value


def _checksum(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise FD1RecoveryPreparationError(f"{label} must be lowercase SHA-256")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare provider-free Gate 3C-FD1 recovery artifacts."
    )
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--parquet-list", required=True, type=Path)
    parser.add_argument("--store-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--identity-envelope", required=True, type=Path)
    parser.add_argument(
        "--hash-cache",
        type=Path,
        help="Optional exact prior content-addressed recovery hash cache.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = prepare_fd1_recovery(
        ledger_path=args.ledger,
        parquet_list_path=args.parquet_list,
        store_root=args.store_root,
        output_root=args.output_root,
        identity_envelope_path=args.identity_envelope,
        hash_cache_path=args.hash_cache,
    )
    sys.stdout.write(_canonical_json(result) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
