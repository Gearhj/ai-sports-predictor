"""Frozen manifests for recovering an interrupted Gate 3C-FD1 publication.

This module is intentionally file-list driven.  It never discovers Parquet files or
touches raw source trees: callers must provide the complete, explicit set of logical
URIs.  The first build verifies those immutable files and creates a checksum cache;
subsequent builds may trust a cache entry only while path, size, and nanosecond mtime
remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4, uuid5

import polars as pl


GIB = 1024**3
DEFAULT_EXPECTED_PARQUET_FILES = 329
DEFAULT_MINIMUM_FREE_BYTES = 100 * GIB
DEFAULT_HEARTBEAT_SECONDS = 30.0
RECOVERY_MANIFEST_SCHEMA_VERSION = "gate3c_fd1_recovery_manifest_v1"
RECOVERY_HASH_CACHE_SCHEMA_VERSION = "gate3c_fd1_recovery_hash_cache_v1"
RECOVERY_ID_NAMESPACE = UUID("d6d44a73-0b6c-5d3b-a66d-3a536d3e0661")

EXPECTED_DECLARED_SCHEMAS: Mapping[str, str] = MappingProxyType({
    "plate_appearance": "retrosheet_fd1_plate_appearance_v5",
    "play_transition": "retrosheet_fd1_play_transition_v5",
    "pitch_sequence": "retrosheet_fd1_pitch_sequence_v5",
    "player_game_batting": "retrosheet_fd1_player_game_batting_v5",
    "player_game_pitching": "retrosheet_fd1_player_game_pitching_v5",
    "player_game_fielding": "retrosheet_fd1_player_game_fielding_v5",
    "starter_usage": "retrosheet_fd1_starter_usage_v6",
    "bullpen_usage": "retrosheet_fd1_bullpen_usage_v6",
    "weather": "retrosheet_fd1_weather_v8",
    "player_prior": "gate3c_fd1_identity_prior_v1",
})
EXPECTED_SOURCES: Mapping[str, str] = MappingProxyType(
    {
        family: (
            "sabr_lahman_box_downloads"
            if family == "player_prior"
            else "retrosheet_csv_downloads"
        )
        for family in EXPECTED_DECLARED_SCHEMAS
    }
)

_PATH_PATTERN = re.compile(
    r"^historical-events/"
    r"source=(?P<source>[a-z0-9][a-z0-9._-]{0,119})/"
    r"family=(?P<family>[a-z0-9][a-z0-9._-]{0,119})/"
    r"season=(?P<season>\d{4})/month=(?P<month>\d{2})/"
    r"schema=(?P<schema>[a-z0-9][a-z0-9._-]{0,119})/"
    r"source-version=(?P<source_version>[0-9a-f-]{36})/"
    r"checkpoint=(?P<checkpoint>[0-9a-f-]{36})/"
    r"governance=(?P<governance>[0-9a-f]{64})/"
    r"part-(?P<file_sha256>[0-9a-f]{64})\.parquet$"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_SOURCE_KEY = re.compile(r"[A-Z][A-Z0-9_]{0,119}")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class FD1RecoveryManifestError(RuntimeError):
    """Base error for recovery-manifest validation."""


class FD1RecoveryIntegrityError(FD1RecoveryManifestError):
    """Recovery evidence is missing, changed, ambiguous, or unsafe."""


class FD1RecoveryStorageError(FD1RecoveryManifestError):
    """The required free-space reserve cannot be proven."""


@dataclass(frozen=True, slots=True)
class TrustedDryRunLedger:
    logical_uri: str
    sha256: str
    schema_version: str

    def __post_init__(self) -> None:
        _logical_uri(self.logical_uri, "trusted dry-run ledger")
        _sha256(self.sha256, "trusted dry-run ledger")
        _text(self.schema_version, "trusted dry-run ledger schema version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_uri": self.logical_uri,
            "schema_version": self.schema_version,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> TrustedDryRunLedger:
        item = _object(value, "trusted_dry_run_ledger")
        _exact_keys(item, {"logical_uri", "schema_version", "sha256"}, "ledger")
        return cls(**item)


@dataclass(frozen=True, slots=True)
class SourceReleaseBinding:
    source_key: str
    source_version_id: str
    dataset_version: str
    identity_checksum: str
    source_mode: str

    def __post_init__(self) -> None:
        _source_key(self.source_key, "source release key")
        _uuid(self.source_version_id, "source release version")
        _text(self.dataset_version, "source dataset version")
        _sha256(self.identity_checksum, "source release identity")
        if self.source_mode != "LIVE":
            raise ValueError("Recovery source releases must be LIVE")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_version": self.dataset_version,
            "identity_checksum": self.identity_checksum,
            "source_key": self.source_key,
            "source_mode": self.source_mode,
            "source_version_id": self.source_version_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> SourceReleaseBinding:
        item = _object(value, "source release")
        _exact_keys(
            item,
            {
                "dataset_version",
                "identity_checksum",
                "source_key",
                "source_mode",
                "source_version_id",
            },
            "source release",
        )
        return cls(**item)


@dataclass(frozen=True, slots=True)
class SourceArchiveBinding:
    source_key: str
    object_key: str
    source_release: str
    raw_logical_uri: str
    archive_sha256: str | None
    payload_sha256: str
    content_length: int
    download_identity_checksum: str

    def __post_init__(self) -> None:
        _source_key(self.source_key, "archive source key")
        _text(self.object_key, "archive object key")
        _text(self.source_release, "archive source release")
        _logical_uri(self.raw_logical_uri, "archive raw logical URI")
        if self.archive_sha256 is not None:
            _sha256(self.archive_sha256, "archive checksum")
        _sha256(self.payload_sha256, "archive payload checksum")
        _nonnegative_int(self.content_length, "archive content length")
        _sha256(self.download_identity_checksum, "download identity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_sha256": self.archive_sha256,
            "content_length": self.content_length,
            "download_identity_checksum": self.download_identity_checksum,
            "object_key": self.object_key,
            "payload_sha256": self.payload_sha256,
            "raw_logical_uri": self.raw_logical_uri,
            "source_key": self.source_key,
            "source_release": self.source_release,
        }

    @classmethod
    def from_dict(cls, value: object) -> SourceArchiveBinding:
        item = _object(value, "source archive")
        _exact_keys(
            item,
            {
                "archive_sha256",
                "content_length",
                "download_identity_checksum",
                "object_key",
                "payload_sha256",
                "raw_logical_uri",
                "source_key",
                "source_release",
            },
            "source archive",
        )
        return cls(**item)


@dataclass(frozen=True, slots=True)
class InterruptedExecutionContext:
    attempted_command: str
    started_at_utc: str | None
    stopped_at_utc: str | None
    completed_files: int
    completed_bytes: int
    sql_rolled_back: bool
    final_execute_evidence_present: bool
    suspected_slow_stage: str

    def __post_init__(self) -> None:
        _text(self.attempted_command, "interrupted command")
        started = _optional_aware_datetime(self.started_at_utc, "interrupted start")
        stopped = _optional_aware_datetime(self.stopped_at_utc, "interrupted stop")
        if started is not None and stopped is not None and stopped < started:
            raise FD1RecoveryIntegrityError(
                "Interrupted execution stop precedes its start"
            )
        _positive_int(self.completed_files, "interrupted completed files")
        _positive_int(self.completed_bytes, "interrupted completed bytes")
        _bool(self.sql_rolled_back, "interrupted SQL rollback")
        _bool(self.final_execute_evidence_present, "final execute evidence")
        _text(self.suspected_slow_stage, "suspected slow stage")
        if not self.sql_rolled_back or self.final_execute_evidence_present:
            raise FD1RecoveryIntegrityError(
                "Recovery requires proven rollback and absence of finalized execute evidence"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted_command": self.attempted_command,
            "completed_bytes": self.completed_bytes,
            "completed_files": self.completed_files,
            "final_execute_evidence_present": self.final_execute_evidence_present,
            "sql_rolled_back": self.sql_rolled_back,
            "started_at_utc": self.started_at_utc,
            "stopped_at_utc": self.stopped_at_utc,
            "suspected_slow_stage": self.suspected_slow_stage,
        }

    @classmethod
    def from_dict(cls, value: object) -> InterruptedExecutionContext:
        item = _object(value, "interrupted execution")
        _exact_keys(
            item,
            {
                "attempted_command",
                "completed_bytes",
                "completed_files",
                "final_execute_evidence_present",
                "sql_rolled_back",
                "started_at_utc",
                "stopped_at_utc",
                "suspected_slow_stage",
            },
            "interrupted execution",
        )
        return cls(**item)


@dataclass(frozen=True, slots=True)
class GenerationLineage:
    code_revision: str
    trusted_dry_run_ledger: TrustedDryRunLedger
    source_releases: tuple[SourceReleaseBinding, ...]
    source_archives: tuple[SourceArchiveBinding, ...]
    interrupted_execution: InterruptedExecutionContext

    def __post_init__(self) -> None:
        _revision(self.code_revision, "generation code revision")
        if not isinstance(self.trusted_dry_run_ledger, TrustedDryRunLedger):
            raise TypeError("trusted_dry_run_ledger has the wrong type")
        _tuple_of(self.source_releases, SourceReleaseBinding, "source releases")
        _tuple_of(self.source_archives, SourceArchiveBinding, "source archives")
        if not isinstance(self.interrupted_execution, InterruptedExecutionContext):
            raise TypeError("interrupted_execution has the wrong type")
        release_keys = [item.source_key for item in self.source_releases]
        if release_keys != sorted(release_keys) or len(release_keys) != len(
            set(release_keys)
        ):
            raise FD1RecoveryIntegrityError(
                "Source release bindings must be sorted and unique"
            )
        archive_keys = [(item.source_key, item.object_key) for item in self.source_archives]
        if archive_keys != sorted(archive_keys) or len(archive_keys) != len(
            set(archive_keys)
        ):
            raise FD1RecoveryIntegrityError(
                "Source archive bindings must be sorted and unique"
            )
        if set(item.source_key for item in self.source_archives) - set(release_keys):
            raise FD1RecoveryIntegrityError("An archive lacks a bound source release")
        releases = {item.source_key: item.dataset_version for item in self.source_releases}
        if any(
            item.source_release != releases[item.source_key]
            for item in self.source_archives
        ):
            raise FD1RecoveryIntegrityError(
                "An archive is not bound to the matching source release"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code_revision": self.code_revision,
            "interrupted_execution": self.interrupted_execution.to_dict(),
            "source_archives": [item.to_dict() for item in self.source_archives],
            "source_releases": [item.to_dict() for item in self.source_releases],
            "trusted_dry_run_ledger": self.trusted_dry_run_ledger.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> GenerationLineage:
        item = _object(value, "generation lineage")
        _exact_keys(
            item,
            {
                "code_revision",
                "interrupted_execution",
                "source_archives",
                "source_releases",
                "trusted_dry_run_ledger",
            },
            "generation lineage",
        )
        return cls(
            code_revision=item["code_revision"],
            trusted_dry_run_ledger=TrustedDryRunLedger.from_dict(
                item["trusted_dry_run_ledger"]
            ),
            source_releases=tuple(
                SourceReleaseBinding.from_dict(row)
                for row in _array(item["source_releases"], "source releases")
            ),
            source_archives=tuple(
                SourceArchiveBinding.from_dict(row)
                for row in _array(item["source_archives"], "source archives")
            ),
            interrupted_execution=InterruptedExecutionContext.from_dict(
                item["interrupted_execution"]
            ),
        )


@dataclass(frozen=True, slots=True)
class FinalizerLineage:
    code_revision: str
    command_schema_version: str

    def __post_init__(self) -> None:
        _revision(self.code_revision, "finalizer code revision")
        _text(self.command_schema_version, "finalizer command schema version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code_revision": self.code_revision,
            "command_schema_version": self.command_schema_version,
        }

    @classmethod
    def from_dict(cls, value: object) -> FinalizerLineage:
        item = _object(value, "finalizer lineage")
        _exact_keys(item, {"code_revision", "command_schema_version"}, "finalizer lineage")
        return cls(**item)


@dataclass(frozen=True, slots=True)
class IdentitySpoolBinding:
    """Immutable identity-COPY input; deliberately contains no run identifier."""

    logical_uri: str
    schema_version: str
    sha256: str
    byte_count: int
    row_count: int

    def __post_init__(self) -> None:
        _logical_uri(self.logical_uri, "identity spool logical URI")
        if not self.logical_uri.startswith(".runtime/gate3c-fd1/recovery/"):
            raise FD1RecoveryIntegrityError(
                "Identity spool must remain under the ignored FD1 recovery root"
            )
        _text(self.schema_version, "identity spool schema version")
        _sha256(self.sha256, "identity spool checksum")
        _positive_int(self.byte_count, "identity spool byte count")
        _positive_int(self.row_count, "identity spool row count")

    def to_dict(self) -> dict[str, Any]:
        return {
            "byte_count": self.byte_count,
            "logical_uri": self.logical_uri,
            "row_count": self.row_count,
            "schema_version": self.schema_version,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> IdentitySpoolBinding:
        item = _object(value, "identity spool")
        _exact_keys(
            item,
            {"byte_count", "logical_uri", "row_count", "schema_version", "sha256"},
            "identity spool",
        )
        return cls(**item)


@dataclass(frozen=True, slots=True)
class PhysicalSchemaField:
    name: str
    dtype: str

    def __post_init__(self) -> None:
        _text(self.name, "physical schema field name")
        _text(self.dtype, "physical schema field dtype")

    def to_dict(self) -> dict[str, str]:
        return {"dtype": self.dtype, "name": self.name}

    @classmethod
    def from_dict(cls, value: object) -> PhysicalSchemaField:
        item = _object(value, "physical schema field")
        _exact_keys(item, {"dtype", "name"}, "physical schema field")
        return cls(**item)


@dataclass(frozen=True, slots=True)
class RecoveryFileEntry:
    logical_uri: str
    size_bytes: int
    mtime_ns: int
    sha256: str
    physical_schema_fingerprint: str
    physical_schema: tuple[PhysicalSchemaField, ...]
    declared_schema: str
    row_count: int
    min_date: str
    max_date: str
    source_key: str
    source_version_id: str
    checkpoint_id: str
    governance_checksum: str
    family: str
    season: int
    month: int
    partition_identity_checksum: str

    def __post_init__(self) -> None:
        parsed = parse_partition_uri(self.logical_uri)
        _positive_int(self.size_bytes, "Parquet size")
        _positive_int(self.mtime_ns, "Parquet mtime_ns")
        _sha256(self.sha256, "Parquet checksum")
        _sha256(self.physical_schema_fingerprint, "physical schema fingerprint")
        _tuple_of(self.physical_schema, PhysicalSchemaField, "physical schema")
        _positive_int(self.row_count, "Parquet row count")
        _date(self.min_date, "minimum Parquet date")
        _date(self.max_date, "maximum Parquet date")
        if self.max_date < self.min_date:
            raise ValueError("Parquet date bounds are reversed")
        _uuid(self.source_version_id, "Parquet source version")
        _uuid(self.checkpoint_id, "Parquet checkpoint")
        _sha256(self.governance_checksum, "Parquet governance checksum")
        _sha256(self.partition_identity_checksum, "Parquet partition identity")
        expected = {
            "checkpoint_id": parsed.checkpoint_id,
            "declared_schema": parsed.declared_schema,
            "family": parsed.family,
            "governance_checksum": parsed.governance_checksum,
            "month": parsed.month,
            "season": parsed.season,
            "sha256": parsed.file_sha256,
            "source_key": parsed.source_key,
            "source_version_id": parsed.source_version_id,
        }
        for name, value in expected.items():
            if getattr(self, name) != value:
                raise FD1RecoveryIntegrityError(
                    f"Recovery entry {name} disagrees with its logical URI"
                )
        if self.partition_identity_checksum != parsed.partition_identity_checksum:
            raise FD1RecoveryIntegrityError("Recovery partition identity checksum is invalid")
        actual_schema = _canonical_checksum(
            [field.to_dict() for field in self.physical_schema]
        )
        if actual_schema != self.physical_schema_fingerprint:
            raise FD1RecoveryIntegrityError("Recovery physical schema fingerprint is invalid")

    @property
    def semantic_partition_key(self) -> tuple[str, str, int, int]:
        return self.source_key, self.family, self.season, self.month

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "declared_schema": self.declared_schema,
            "family": self.family,
            "governance_checksum": self.governance_checksum,
            "logical_uri": self.logical_uri,
            "max_date": self.max_date,
            "min_date": self.min_date,
            "month": self.month,
            "mtime_ns": self.mtime_ns,
            "partition_identity_checksum": self.partition_identity_checksum,
            "physical_schema": [field.to_dict() for field in self.physical_schema],
            "physical_schema_fingerprint": self.physical_schema_fingerprint,
            "row_count": self.row_count,
            "season": self.season,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "source_key": self.source_key,
            "source_version_id": self.source_version_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> RecoveryFileEntry:
        item = _object(value, "recovery file entry")
        expected = {
            "checkpoint_id",
            "declared_schema",
            "family",
            "governance_checksum",
            "logical_uri",
            "max_date",
            "min_date",
            "month",
            "mtime_ns",
            "partition_identity_checksum",
            "physical_schema",
            "physical_schema_fingerprint",
            "row_count",
            "season",
            "sha256",
            "size_bytes",
            "source_key",
            "source_version_id",
        }
        _exact_keys(item, expected, "recovery file entry")
        return cls(
            **{
                **item,
                "physical_schema": tuple(
                    PhysicalSchemaField.from_dict(row)
                    for row in _array(item["physical_schema"], "physical schema")
                ),
            }
        )


@dataclass(frozen=True, slots=True)
class RecoveryHashCacheEntry:
    logical_uri: str
    size_bytes: int
    mtime_ns: int
    sha256: str
    physical_schema_fingerprint: str
    row_count: int

    def __post_init__(self) -> None:
        parse_partition_uri(self.logical_uri)
        _positive_int(self.size_bytes, "cache file size")
        _positive_int(self.mtime_ns, "cache file mtime_ns")
        _sha256(self.sha256, "cache file checksum")
        _sha256(self.physical_schema_fingerprint, "cache schema fingerprint")
        _positive_int(self.row_count, "cache row count")

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_uri": self.logical_uri,
            "mtime_ns": self.mtime_ns,
            "physical_schema_fingerprint": self.physical_schema_fingerprint,
            "row_count": self.row_count,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, value: object) -> RecoveryHashCacheEntry:
        item = _object(value, "hash-cache entry")
        _exact_keys(
            item,
            {
                "logical_uri",
                "mtime_ns",
                "physical_schema_fingerprint",
                "row_count",
                "sha256",
                "size_bytes",
            },
            "hash-cache entry",
        )
        return cls(**item)


@dataclass(frozen=True, slots=True)
class RecoveryHashCache:
    schema_version: str
    entries: tuple[RecoveryHashCacheEntry, ...]
    cache_checksum: str

    def __post_init__(self) -> None:
        if self.schema_version != RECOVERY_HASH_CACHE_SCHEMA_VERSION:
            raise FD1RecoveryIntegrityError("Recovery hash-cache schema version is invalid")
        _tuple_of(self.entries, RecoveryHashCacheEntry, "hash-cache entries")
        uris = [entry.logical_uri for entry in self.entries]
        if uris != sorted(uris) or len(uris) != len(set(uris)):
            raise FD1RecoveryIntegrityError("Hash-cache entries must be sorted and unique")
        _sha256(self.cache_checksum, "hash-cache checksum")
        if self.cache_checksum != _canonical_checksum(self.unsigned_dict()):
            raise FD1RecoveryIntegrityError("Recovery hash-cache checksum is invalid")

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "schema_version": self.schema_version,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "cache_checksum": self.cache_checksum}

    @classmethod
    def create(cls, entries: Sequence[RecoveryHashCacheEntry]) -> RecoveryHashCache:
        ordered = tuple(sorted(entries, key=lambda entry: entry.logical_uri))
        unsigned = {
            "entries": [entry.to_dict() for entry in ordered],
            "schema_version": RECOVERY_HASH_CACHE_SCHEMA_VERSION,
        }
        return cls(
            schema_version=RECOVERY_HASH_CACHE_SCHEMA_VERSION,
            entries=ordered,
            cache_checksum=_canonical_checksum(unsigned),
        )

    @classmethod
    def from_dict(cls, value: object) -> RecoveryHashCache:
        item = _object(value, "recovery hash cache")
        _exact_keys(item, {"cache_checksum", "entries", "schema_version"}, "hash cache")
        return cls(
            schema_version=item["schema_version"],
            entries=tuple(
                RecoveryHashCacheEntry.from_dict(row)
                for row in _array(item["entries"], "hash-cache entries")
            ),
            cache_checksum=item["cache_checksum"],
        )


@dataclass(frozen=True, slots=True)
class FamilyAggregate:
    family: str
    file_count: int
    row_count: int
    byte_count: int
    min_date: str
    max_date: str

    def __post_init__(self) -> None:
        if self.family not in EXPECTED_DECLARED_SCHEMAS:
            raise ValueError("Recovery family aggregate is outside the FD1 registry")
        _positive_int(self.file_count, "family file count")
        _positive_int(self.row_count, "family row count")
        _positive_int(self.byte_count, "family byte count")
        _date(self.min_date, "family minimum date")
        _date(self.max_date, "family maximum date")

    def to_dict(self) -> dict[str, Any]:
        return {
            "byte_count": self.byte_count,
            "family": self.family,
            "file_count": self.file_count,
            "max_date": self.max_date,
            "min_date": self.min_date,
            "row_count": self.row_count,
        }

    @classmethod
    def from_dict(cls, value: object) -> FamilyAggregate:
        item = _object(value, "family aggregate")
        _exact_keys(
            item,
            {"byte_count", "family", "file_count", "max_date", "min_date", "row_count"},
            "family aggregate",
        )
        return cls(**item)


@dataclass(frozen=True, slots=True)
class RecoveryManifestAggregate:
    file_count: int
    row_count: int
    byte_count: int
    min_date: str
    max_date: str
    families: tuple[FamilyAggregate, ...]

    def __post_init__(self) -> None:
        _positive_int(self.file_count, "manifest file count")
        _positive_int(self.row_count, "manifest row count")
        _positive_int(self.byte_count, "manifest byte count")
        _date(self.min_date, "manifest minimum date")
        _date(self.max_date, "manifest maximum date")
        _tuple_of(self.families, FamilyAggregate, "family aggregates")
        names = [family.family for family in self.families]
        if names != sorted(names) or len(names) != len(set(names)):
            raise FD1RecoveryIntegrityError("Family aggregates must be sorted and unique")
        if sum(item.file_count for item in self.families) != self.file_count:
            raise FD1RecoveryIntegrityError("Family file counts do not reconcile")
        if sum(item.row_count for item in self.families) != self.row_count:
            raise FD1RecoveryIntegrityError("Family row counts do not reconcile")
        if sum(item.byte_count for item in self.families) != self.byte_count:
            raise FD1RecoveryIntegrityError("Family byte counts do not reconcile")

    def to_dict(self) -> dict[str, Any]:
        return {
            "byte_count": self.byte_count,
            "families": [item.to_dict() for item in self.families],
            "file_count": self.file_count,
            "max_date": self.max_date,
            "min_date": self.min_date,
            "row_count": self.row_count,
        }

    @classmethod
    def from_dict(cls, value: object) -> RecoveryManifestAggregate:
        item = _object(value, "manifest aggregate")
        _exact_keys(
            item,
            {"byte_count", "families", "file_count", "max_date", "min_date", "row_count"},
            "manifest aggregate",
        )
        return cls(
            **{
                **item,
                "families": tuple(
                    FamilyAggregate.from_dict(row)
                    for row in _array(item["families"], "family aggregates")
                ),
            }
        )


@dataclass(frozen=True, slots=True)
class RecoveryManifest:
    schema_version: str
    recovery_id: str
    generation_lineage: GenerationLineage
    finalizer_lineage: FinalizerLineage
    identity_spool: IdentitySpoolBinding
    files: tuple[RecoveryFileEntry, ...]
    aggregate: RecoveryManifestAggregate
    manifest_checksum: str

    def __post_init__(self) -> None:
        if self.schema_version != RECOVERY_MANIFEST_SCHEMA_VERSION:
            raise FD1RecoveryIntegrityError("Recovery manifest schema version is invalid")
        _uuid(self.recovery_id, "recovery_id")
        if not isinstance(self.generation_lineage, GenerationLineage):
            raise TypeError("generation_lineage has the wrong type")
        if not isinstance(self.finalizer_lineage, FinalizerLineage):
            raise TypeError("finalizer_lineage has the wrong type")
        if not isinstance(self.identity_spool, IdentitySpoolBinding):
            raise TypeError("identity_spool has the wrong type")
        _tuple_of(self.files, RecoveryFileEntry, "manifest files")
        if not isinstance(self.aggregate, RecoveryManifestAggregate):
            raise TypeError("aggregate has the wrong type")
        uris = [entry.logical_uri for entry in self.files]
        semantic = [entry.semantic_partition_key for entry in self.files]
        identities = [entry.partition_identity_checksum for entry in self.files]
        if uris != sorted(uris) or len(uris) != len(set(uris)):
            raise FD1RecoveryIntegrityError("Recovery files must be sorted and unique")
        if len(semantic) != len(set(semantic)):
            raise FD1RecoveryIntegrityError("Recovery semantic partition identity is duplicated")
        if len(identities) != len(set(identities)):
            raise FD1RecoveryIntegrityError("Recovery full partition identity is duplicated")
        expected_aggregate = _aggregate(self.files)
        if self.aggregate != expected_aggregate:
            raise FD1RecoveryIntegrityError("Recovery manifest aggregate is invalid")
        releases = {
            item.source_key.lower(): item.source_version_id
            for item in self.generation_lineage.source_releases
        }
        archive_sources = {
            item.source_key.lower() for item in self.generation_lineage.source_archives
        }
        for entry in self.files:
            if releases.get(entry.source_key) != entry.source_version_id:
                raise FD1RecoveryIntegrityError(
                    "A recovery file is not bound to its generation source release"
                )
            if entry.source_key not in archive_sources:
                raise FD1RecoveryIntegrityError(
                    "A recovery file source lacks a bound archive checksum"
                )
        context = self.generation_lineage.interrupted_execution
        if context.completed_files != len(self.files):
            raise FD1RecoveryIntegrityError("Interrupted file count does not match manifest")
        if context.completed_bytes != self.aggregate.byte_count:
            raise FD1RecoveryIntegrityError("Interrupted byte count does not match manifest")
        expected_recovery_id = derive_recovery_id(
            generation_lineage=self.generation_lineage,
            finalizer_lineage=self.finalizer_lineage,
            identity_spool=self.identity_spool,
            files=self.files,
            aggregate=self.aggregate,
        )
        if self.recovery_id != expected_recovery_id:
            raise FD1RecoveryIntegrityError("Recovery UUID is not derived from canonical evidence")
        _sha256(self.manifest_checksum, "recovery manifest checksum")
        if self.manifest_checksum != _canonical_checksum(self.unsigned_dict()):
            raise FD1RecoveryIntegrityError("Recovery manifest checksum is invalid")

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "aggregate": self.aggregate.to_dict(),
            "files": [entry.to_dict() for entry in self.files],
            "finalizer_lineage": self.finalizer_lineage.to_dict(),
            "generation_lineage": self.generation_lineage.to_dict(),
            "identity_spool": self.identity_spool.to_dict(),
            "recovery_id": self.recovery_id,
            "schema_version": self.schema_version,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "manifest_checksum": self.manifest_checksum}

    @classmethod
    def create(
        cls,
        *,
        generation_lineage: GenerationLineage,
        finalizer_lineage: FinalizerLineage,
        identity_spool: IdentitySpoolBinding,
        files: Sequence[RecoveryFileEntry],
    ) -> RecoveryManifest:
        ordered = tuple(sorted(files, key=lambda entry: entry.logical_uri))
        aggregate = _aggregate(ordered)
        recovery_id = derive_recovery_id(
            generation_lineage=generation_lineage,
            finalizer_lineage=finalizer_lineage,
            identity_spool=identity_spool,
            files=ordered,
            aggregate=aggregate,
        )
        unsigned = {
            "aggregate": aggregate.to_dict(),
            "files": [entry.to_dict() for entry in ordered],
            "finalizer_lineage": finalizer_lineage.to_dict(),
            "generation_lineage": generation_lineage.to_dict(),
            "identity_spool": identity_spool.to_dict(),
            "recovery_id": recovery_id,
            "schema_version": RECOVERY_MANIFEST_SCHEMA_VERSION,
        }
        return cls(
            schema_version=RECOVERY_MANIFEST_SCHEMA_VERSION,
            recovery_id=recovery_id,
            generation_lineage=generation_lineage,
            finalizer_lineage=finalizer_lineage,
            identity_spool=identity_spool,
            files=ordered,
            aggregate=aggregate,
            manifest_checksum=_canonical_checksum(unsigned),
        )

    @classmethod
    def from_dict(cls, value: object) -> RecoveryManifest:
        item = _object(value, "recovery manifest")
        _exact_keys(
            item,
            {
                "aggregate",
                "files",
                "finalizer_lineage",
                "generation_lineage",
                "identity_spool",
                "manifest_checksum",
                "recovery_id",
                "schema_version",
            },
            "recovery manifest",
        )
        return cls(
            schema_version=item["schema_version"],
            recovery_id=item["recovery_id"],
            generation_lineage=GenerationLineage.from_dict(item["generation_lineage"]),
            finalizer_lineage=FinalizerLineage.from_dict(item["finalizer_lineage"]),
            identity_spool=IdentitySpoolBinding.from_dict(item["identity_spool"]),
            files=tuple(
                RecoveryFileEntry.from_dict(row)
                for row in _array(item["files"], "recovery files")
            ),
            aggregate=RecoveryManifestAggregate.from_dict(item["aggregate"]),
            manifest_checksum=item["manifest_checksum"],
        )


@dataclass(frozen=True, slots=True)
class ParsedPartitionUri:
    logical_uri: str
    source_key: str
    family: str
    season: int
    month: int
    declared_schema: str
    source_version_id: str
    checkpoint_id: str
    governance_checksum: str
    file_sha256: str
    partition_identity_checksum: str


@dataclass(frozen=True, slots=True)
class RecoveryProgress:
    stage: str
    files_validated: int
    files_total: int
    bytes_validated: int
    bytes_total: int
    files_hashed: int
    bytes_hashed: int
    cache_hits: int
    elapsed_seconds: float
    current_logical_uri: str | None


@dataclass(frozen=True, slots=True)
class RecoveryManifestBuildResult:
    manifest: RecoveryManifest
    hash_cache: RecoveryHashCache
    cache_hits: int
    files_hashed: int
    bytes_hashed: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class ContentAddressedJsonArtifact:
    path: Path
    sha256: str
    byte_count: int
    reused: bool


def parse_partition_uri(logical_uri: str) -> ParsedPartitionUri:
    """Parse and bind one exact, root-relative FD1 content-addressed path."""

    _logical_uri(logical_uri, "Parquet logical URI")
    match = _PATH_PATTERN.fullmatch(logical_uri)
    if match is None:
        raise FD1RecoveryIntegrityError("Parquet logical URI is outside the frozen FD1 layout")
    values = match.groupdict()
    family = values["family"]
    expected_schema = EXPECTED_DECLARED_SCHEMAS.get(family)
    if expected_schema is None or values["schema"] != expected_schema:
        raise FD1RecoveryIntegrityError("Parquet declared schema is outside the recovery contract")
    if values["source"] != EXPECTED_SOURCES[family]:
        raise FD1RecoveryIntegrityError("Parquet source/family binding is outside FD1 recovery")
    source_version = str(_uuid(values["source_version"], "source version"))
    checkpoint = str(_uuid(values["checkpoint"], "checkpoint"))
    season = int(values["season"])
    month = int(values["month"])
    if season not in range(2021, 2026) or month not in range(1, 13):
        raise FD1RecoveryIntegrityError("Parquet season/month is outside FD1 recovery scope")
    identity = _canonical_checksum(
        {
            "checkpoint_id": checkpoint,
            "declared_schema": values["schema"],
            "family": family,
            "governance_checksum": values["governance"],
            "month": month,
            "season": season,
            "source_key": values["source"],
            "source_version_id": source_version,
        }
    )
    return ParsedPartitionUri(
        logical_uri=logical_uri,
        source_key=values["source"],
        family=family,
        season=season,
        month=month,
        declared_schema=values["schema"],
        source_version_id=source_version,
        checkpoint_id=checkpoint,
        governance_checksum=values["governance"],
        file_sha256=values["file_sha256"],
        partition_identity_checksum=identity,
    )


def build_recovery_manifest(
    *,
    store_root: Path,
    logical_uris: Sequence[str],
    generation_lineage: GenerationLineage,
    finalizer_lineage: FinalizerLineage,
    identity_spool: IdentitySpoolBinding,
    hash_cache: RecoveryHashCache | None = None,
    expected_file_count: int = DEFAULT_EXPECTED_PARQUET_FILES,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    progress_callback: Callable[[RecoveryProgress], None] | None = None,
    free_space: Callable[[Path], int] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> RecoveryManifestBuildResult:
    """Validate an explicit immutable file set and freeze its recovery evidence.

    No directory enumeration occurs.  A cache hit reuses the content checksum only
    when the exact logical URI, byte size, and ``mtime_ns`` match.  Footer/schema,
    row count, partition keys, and date bounds remain independently validated.
    """

    _positive_int(expected_file_count, "expected recovery file count")
    _minimum_reserve(minimum_free_bytes)
    if (
        isinstance(heartbeat_seconds, bool)
        or not isinstance(heartbeat_seconds, (int, float))
        or heartbeat_seconds < 0
        or heartbeat_seconds > DEFAULT_HEARTBEAT_SECONDS
    ):
        raise ValueError("heartbeat_seconds must be between 0 and 30")
    if len(logical_uris) != expected_file_count:
        raise FD1RecoveryIntegrityError(
            f"Recovery requires exactly {expected_file_count} explicit Parquet paths"
        )
    if len(logical_uris) != len(set(logical_uris)):
        raise FD1RecoveryIntegrityError("Recovery logical URIs are duplicated")
    root = _governed_existing_directory(store_root, "Parquet store root")
    free = free_space or _free_bytes
    _require_free_space(root, minimum_free_bytes, free)
    parsed_paths: list[tuple[ParsedPartitionUri, Path, os.stat_result]] = []
    semantic_keys: list[tuple[str, str, int, int]] = []
    for logical_uri in sorted(logical_uris):
        parsed = parse_partition_uri(logical_uri)
        path = _resolve_under(root, logical_uri)
        if not path.is_file():
            raise FD1RecoveryIntegrityError("A manifested Parquet path is not a regular file")
        before = path.stat()
        if before.st_size <= 0 or before.st_mtime_ns <= 0:
            raise FD1RecoveryIntegrityError("A manifested Parquet stat is invalid")
        parsed_paths.append((parsed, path, before))
        semantic_keys.append((parsed.source_key, parsed.family, parsed.season, parsed.month))
    if len(semantic_keys) != len(set(semantic_keys)):
        raise FD1RecoveryIntegrityError("Recovery semantic partition identity is duplicated")
    total_bytes = sum(item[2].st_size for item in parsed_paths)
    if generation_lineage.interrupted_execution.completed_files != expected_file_count:
        raise FD1RecoveryIntegrityError("Interrupted context file count differs from recovery")
    if generation_lineage.interrupted_execution.completed_bytes != total_bytes:
        raise FD1RecoveryIntegrityError("Interrupted context byte count differs from recovery")

    cached = {entry.logical_uri: entry for entry in hash_cache.entries} if hash_cache else {}
    started = clock()
    last_emit = started
    files_validated = 0
    bytes_validated = 0
    files_hashed = 0
    bytes_hashed = 0
    cache_hits = 0
    current_uri: str | None = None

    def emit(*, stage: str = "FILES_VALIDATING", force: bool = False) -> None:
        nonlocal last_emit
        now = clock()
        if progress_callback is None or (
            not force and now - last_emit < heartbeat_seconds
        ):
            return
        progress_callback(
            RecoveryProgress(
                stage=stage,
                files_validated=files_validated,
                files_total=expected_file_count,
                bytes_validated=bytes_validated,
                bytes_total=total_bytes,
                files_hashed=files_hashed,
                bytes_hashed=bytes_hashed,
                cache_hits=cache_hits,
                elapsed_seconds=max(now - started, 0.0),
                current_logical_uri=current_uri,
            )
        )
        last_emit = now

    emit(force=True)
    entries: list[RecoveryFileEntry] = []
    cache_entries: list[RecoveryHashCacheEntry] = []
    metadata_executor = (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="fd1-recovery-metadata")
        if progress_callback is not None and heartbeat_seconds > 0
        else None
    )
    try:
        for parsed, path, before in parsed_paths:
            current_uri = parsed.logical_uri
            _require_free_space(root, minimum_free_bytes, free)
            if metadata_executor is None:
                metadata = _read_partition_metadata(path, parsed)
            else:
                future = metadata_executor.submit(_read_partition_metadata, path, parsed)
                while True:
                    since_emit = max(clock() - last_emit, 0.0)
                    remaining = max(heartbeat_seconds - since_emit, 0.001)
                    try:
                        metadata = future.result(timeout=remaining)
                        break
                    except FutureTimeoutError:
                        emit(force=True)
            schema_fields, schema_fingerprint, row_count, min_date, max_date = metadata
            prior = cached.get(parsed.logical_uri)
            cache_hit = (
                prior is not None
                and prior.size_bytes == before.st_size
                and prior.mtime_ns == before.st_mtime_ns
            )
            if cache_hit:
                if (
                    prior.sha256 != parsed.file_sha256
                    or prior.physical_schema_fingerprint != schema_fingerprint
                    or prior.row_count != row_count
                ):
                    raise FD1RecoveryIntegrityError(
                        "An unchanged-stat hash-cache entry conflicts with Parquet metadata"
                    )
                file_sha256 = prior.sha256
                cache_hits += 1
            else:

                def on_chunk(byte_count: int) -> None:
                    nonlocal bytes_hashed
                    bytes_hashed += byte_count
                    emit()

                file_sha256 = _sha256_file(path, on_chunk=on_chunk)
                files_hashed += 1
            after = path.stat()
            if (after.st_size, after.st_mtime_ns) != (
                before.st_size,
                before.st_mtime_ns,
            ):
                raise FD1RecoveryIntegrityError(
                    "A Parquet file changed during recovery validation"
                )
            if file_sha256 != parsed.file_sha256:
                raise FD1RecoveryIntegrityError(
                    "Parquet content checksum differs from its content-addressed filename"
                )
            entry = RecoveryFileEntry(
                logical_uri=parsed.logical_uri,
                size_bytes=before.st_size,
                mtime_ns=before.st_mtime_ns,
                sha256=file_sha256,
                physical_schema_fingerprint=schema_fingerprint,
                physical_schema=schema_fields,
                declared_schema=parsed.declared_schema,
                row_count=row_count,
                min_date=min_date,
                max_date=max_date,
                source_key=parsed.source_key,
                source_version_id=parsed.source_version_id,
                checkpoint_id=parsed.checkpoint_id,
                governance_checksum=parsed.governance_checksum,
                family=parsed.family,
                season=parsed.season,
                month=parsed.month,
                partition_identity_checksum=parsed.partition_identity_checksum,
            )
            entries.append(entry)
            cache_entries.append(
                RecoveryHashCacheEntry(
                    logical_uri=entry.logical_uri,
                    size_bytes=entry.size_bytes,
                    mtime_ns=entry.mtime_ns,
                    sha256=entry.sha256,
                    physical_schema_fingerprint=entry.physical_schema_fingerprint,
                    row_count=entry.row_count,
                )
            )
            files_validated += 1
            bytes_validated += before.st_size
            emit()
    finally:
        if metadata_executor is not None:
            metadata_executor.shutdown(wait=True, cancel_futures=True)
    current_uri = None
    manifest = RecoveryManifest.create(
        generation_lineage=generation_lineage,
        finalizer_lineage=finalizer_lineage,
        identity_spool=identity_spool,
        files=entries,
    )
    updated_cache = RecoveryHashCache.create(cache_entries)
    emit(stage="FILES_VALIDATED", force=True)
    elapsed = max(clock() - started, 0.0)
    return RecoveryManifestBuildResult(
        manifest=manifest,
        hash_cache=updated_cache,
        cache_hits=cache_hits,
        files_hashed=files_hashed,
        bytes_hashed=bytes_hashed,
        elapsed_seconds=elapsed,
    )


def derive_recovery_id(
    *,
    generation_lineage: GenerationLineage,
    finalizer_lineage: FinalizerLineage,
    identity_spool: IdentitySpoolBinding,
    files: Sequence[RecoveryFileEntry],
    aggregate: RecoveryManifestAggregate | None = None,
) -> str:
    """Derive the stable recovery UUID from all frozen semantic evidence."""

    ordered = tuple(sorted(files, key=lambda entry: entry.logical_uri))
    resolved_aggregate = aggregate or _aggregate(ordered)
    seed = _canonical_checksum(
        {
            "aggregate": resolved_aggregate.to_dict(),
            "files": [entry.to_dict() for entry in ordered],
            "finalizer_lineage": finalizer_lineage.to_dict(),
            "generation_lineage": generation_lineage.to_dict(),
            "identity_spool": identity_spool.to_dict(),
            "schema_version": RECOVERY_MANIFEST_SCHEMA_VERSION,
        }
    )
    return str(uuid5(RECOVERY_ID_NAMESPACE, seed))


def manifest_paths(manifest: RecoveryManifest, *, store_root: Path) -> tuple[Path, ...]:
    """Resolve only paths named by a validated manifest; never discover extras."""

    if not isinstance(manifest, RecoveryManifest):
        raise TypeError("manifest has the wrong type")
    root = _governed_existing_directory(store_root, "Parquet store root")
    return tuple(_resolve_under(root, entry.logical_uri) for entry in manifest.files)


def load_recovery_manifest(
    path: Path,
    *,
    expected_file_count: int = DEFAULT_EXPECTED_PARQUET_FILES,
) -> RecoveryManifest:
    payload = _read_content_addressed_json(
        path,
        label="recovery manifest",
        stem="fd1-recovery-manifest",
    )
    manifest = RecoveryManifest.from_dict(payload)
    if len(manifest.files) != expected_file_count:
        raise FD1RecoveryIntegrityError(
            f"Recovery manifest does not contain exactly {expected_file_count} files"
        )
    return manifest


def load_recovery_hash_cache(path: Path) -> RecoveryHashCache:
    return RecoveryHashCache.from_dict(
        _read_content_addressed_json(
            path,
            label="recovery hash cache",
            stem="fd1-recovery-hash-cache",
        )
    )


def write_recovery_manifest(
    manifest: RecoveryManifest,
    *,
    output_root: Path,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    free_space: Callable[[Path], int] | None = None,
) -> ContentAddressedJsonArtifact:
    return _write_content_addressed_json(
        manifest.to_dict(),
        output_root=output_root,
        stem="fd1-recovery-manifest",
        minimum_free_bytes=minimum_free_bytes,
        free_space=free_space,
    )


def write_recovery_hash_cache(
    cache: RecoveryHashCache,
    *,
    output_root: Path,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    free_space: Callable[[Path], int] | None = None,
) -> ContentAddressedJsonArtifact:
    return _write_content_addressed_json(
        cache.to_dict(),
        output_root=output_root,
        stem="fd1-recovery-hash-cache",
        minimum_free_bytes=minimum_free_bytes,
        free_space=free_space,
    )


def _read_partition_metadata(
    path: Path,
    parsed: ParsedPartitionUri,
) -> tuple[tuple[PhysicalSchemaField, ...], str, int, str, str]:
    try:
        schema = pl.read_parquet_schema(path)
    except Exception as exc:
        raise FD1RecoveryIntegrityError("Parquet footer/schema is unreadable") from exc
    fields = tuple(
        PhysicalSchemaField(name=name, dtype=str(dtype))
        for name, dtype in schema.items()
    )
    fingerprint = _canonical_checksum([field.to_dict() for field in fields])
    required = ["source_key", "season"]
    if parsed.family == "player_prior":
        date_column = "official_date"
    else:
        required.extend(["source_version_id", "month"])
        date_column = "game_date"
    missing = set((*required, date_column)) - set(schema)
    if missing:
        raise FD1RecoveryIntegrityError("Parquet lacks required recovery partition fields")
    expressions: list[pl.Expr] = [
        pl.len().alias("__rows"),
        pl.col(date_column).min().alias("__min_date"),
        pl.col(date_column).max().alias("__max_date"),
    ]
    for name in required:
        expressions.extend(
            [
                pl.col(name).n_unique().alias(f"{name}__count"),
                pl.col(name).first().alias(f"{name}__value"),
            ]
        )
    try:
        metadata = (
            pl.scan_parquet(path, hive_partitioning=False)
            .select(expressions)
            .collect()
            .row(0, named=True)
        )
    except Exception as exc:
        raise FD1RecoveryIntegrityError("Parquet row metadata is unreadable") from exc
    expected_values: Mapping[str, object] = {
        "source_key": parsed.source_key.upper(),
        "source_version_id": parsed.source_version_id,
        "season": parsed.season,
        "month": parsed.month,
    }
    for name in required:
        if metadata[f"{name}__count"] != 1 or metadata[f"{name}__value"] != expected_values[name]:
            raise FD1RecoveryIntegrityError(
                f"Parquet {name} does not match its manifested partition path"
            )
    row_count = int(metadata["__rows"])
    if row_count <= 0:
        raise FD1RecoveryIntegrityError("Recovery Parquet partition is empty")
    try:
        min_date = date.fromisoformat(str(metadata["__min_date"]))
        max_date = date.fromisoformat(str(metadata["__max_date"]))
    except (TypeError, ValueError) as exc:
        raise FD1RecoveryIntegrityError("Parquet recovery date bounds are invalid") from exc
    for value in (min_date, max_date):
        if (value.year, value.month) != (parsed.season, parsed.month):
            raise FD1RecoveryIntegrityError(
                "Parquet recovery date lies outside its season/month partition"
            )
    return fields, fingerprint, row_count, min_date.isoformat(), max_date.isoformat()


def _aggregate(files: Sequence[RecoveryFileEntry]) -> RecoveryManifestAggregate:
    if not files:
        raise FD1RecoveryIntegrityError("Recovery manifest cannot be empty")
    grouped: dict[str, list[RecoveryFileEntry]] = {}
    for entry in files:
        grouped.setdefault(entry.family, []).append(entry)
    families = tuple(
        FamilyAggregate(
            family=family,
            file_count=len(rows),
            row_count=sum(row.row_count for row in rows),
            byte_count=sum(row.size_bytes for row in rows),
            min_date=min(row.min_date for row in rows),
            max_date=max(row.max_date for row in rows),
        )
        for family, rows in sorted(grouped.items())
    )
    return RecoveryManifestAggregate(
        file_count=len(files),
        row_count=sum(entry.row_count for entry in files),
        byte_count=sum(entry.size_bytes for entry in files),
        min_date=min(entry.min_date for entry in files),
        max_date=max(entry.max_date for entry in files),
        families=families,
    )


def _write_content_addressed_json(
    payload: Mapping[str, Any],
    *,
    output_root: Path,
    stem: str,
    minimum_free_bytes: int,
    free_space: Callable[[Path], int] | None,
) -> ContentAddressedJsonArtifact:
    _minimum_reserve(minimum_free_bytes)
    root = _governed_output_directory(output_root)
    free = free_space or _free_bytes
    encoded = _canonical_json_bytes(payload) + b"\n"
    _require_free_space(root, minimum_free_bytes + len(encoded), free)
    digest = hashlib.sha256(encoded).hexdigest()
    final = _resolve_under(root, f"{stem}-sha256-{digest}.json")
    if final.exists() or final.is_symlink():
        _assert_no_links(final)
        if not final.is_file() or final.read_bytes() != encoded:
            raise FD1RecoveryIntegrityError("Content-addressed JSON path has conflicting bytes")
        return ContentAddressedJsonArtifact(final, digest, len(encoded), True)
    temporary = _resolve_under(root, f".{stem}-{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, final)
        except FileExistsError:
            _assert_no_links(final)
            if not final.is_file() or final.read_bytes() != encoded:
                raise FD1RecoveryIntegrityError(
                    "Content-addressed JSON path was concurrently replaced"
                )
        _assert_no_links(final)
        if final.read_bytes() != encoded:
            raise FD1RecoveryIntegrityError("Content-addressed JSON read-back failed")
        return ContentAddressedJsonArtifact(final, digest, len(encoded), False)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def _read_content_addressed_json(path: Path, *, label: str, stem: str) -> object:
    absolute = Path(path).absolute()
    _assert_no_link_ancestors(absolute)
    if not absolute.is_file():
        raise FD1RecoveryIntegrityError(f"{label} is not a regular file")
    try:
        encoded = absolute.read_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
        expected_name = f"{stem}-sha256-{digest}.json"
        if absolute.name != expected_name:
            raise FD1RecoveryIntegrityError(
                f"{label} path is not content-addressed to its exact bytes"
            )
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FD1RecoveryIntegrityError(f"{label} is not canonical JSON") from exc
    if encoded != _canonical_json_bytes(payload) + b"\n":
        raise FD1RecoveryIntegrityError(f"{label} is not canonical JSON")
    return payload


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FD1RecoveryIntegrityError("Canonical JSON contains a duplicate key")
        result[key] = value
    return result


def _sha256_file(path: Path, *, on_chunk: Callable[[int], None] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            if on_chunk is not None:
                on_chunk(len(block))
    return digest.hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_checksum(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _governed_existing_directory(path: Path, label: str) -> Path:
    lexical = Path(path).absolute()
    _assert_no_link_ancestors(lexical)
    if not lexical.is_dir():
        raise FD1RecoveryIntegrityError(f"{label} is not an existing directory")
    return lexical.resolve()


def _governed_output_directory(path: Path) -> Path:
    lexical = Path(path).absolute()
    _assert_no_link_ancestors(lexical.parent)
    if lexical.exists() or lexical.is_symlink():
        _assert_no_links(lexical)
        if not lexical.is_dir():
            raise FD1RecoveryIntegrityError("Content-addressed output root is not a directory")
    else:
        lexical.mkdir(parents=True)
    _assert_no_link_ancestors(lexical)
    return lexical.resolve()


def _resolve_under(root: Path, logical_uri: str) -> Path:
    if not isinstance(logical_uri, str) or "\\" in logical_uri:
        raise FD1RecoveryIntegrityError("Recovery logical URI is invalid")
    pure = PurePosixPath(logical_uri)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise FD1RecoveryIntegrityError("Recovery logical URI is not root-confined")
    target = root.joinpath(*pure.parts).absolute()
    try:
        target.relative_to(root.absolute())
    except ValueError as exc:
        raise FD1RecoveryIntegrityError("Recovery path escapes its governed root") from exc
    _assert_no_links_between(root, target)
    return target


def _assert_no_links_between(root: Path, target: Path) -> None:
    try:
        relative = target.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise FD1RecoveryIntegrityError("Recovery path escapes its governed root") from exc
    _assert_no_links(root)
    current = root.absolute()
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _assert_no_links(current)


def _assert_no_link_ancestors(path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        if candidate.exists() or candidate.is_symlink():
            _assert_no_links(candidate)


def _assert_no_links(path: Path) -> None:
    if path.is_symlink():
        raise FD1RecoveryIntegrityError("Recovery paths cannot use links or reparse points")
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError as exc:
        raise FD1RecoveryIntegrityError("Unable to inspect recovery path safety") from exc
    if attributes & _REPARSE_POINT:
        raise FD1RecoveryIntegrityError("Recovery paths cannot use links or reparse points")


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _require_free_space(
    root: Path,
    minimum_free_bytes: int,
    free_space: Callable[[Path], int],
) -> None:
    try:
        available = int(free_space(root))
    except (OSError, TypeError, ValueError) as exc:
        raise FD1RecoveryStorageError("Unable to prove recovery free-space reserve") from exc
    if available < minimum_free_bytes:
        raise FD1RecoveryStorageError("FD1 recovery would breach the 100-GiB reserve")


def _minimum_reserve(value: object) -> int:
    minimum = _nonnegative_int(value, "minimum free bytes")
    if minimum < DEFAULT_MINIMUM_FREE_BYTES:
        raise ValueError("FD1 recovery cannot weaken the 100-GiB reserve")
    return minimum


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FD1RecoveryIntegrityError(f"{label} must be an object")
    return dict(value)


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise FD1RecoveryIntegrityError(f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise FD1RecoveryIntegrityError(f"{label} fields are not exact")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _positive_int(value: object, label: str) -> int:
    if _nonnegative_int(value, label) <= 0:
        raise ValueError(f"{label} must be positive")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _uuid(value: object, label: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical UUID string") from exc
    if str(parsed) != value:
        raise ValueError(f"{label} must be a canonical UUID string")
    return parsed


def _date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical ISO date string") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must be a canonical ISO date string")
    return parsed


def _optional_aware_datetime(value: object, label: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical UTC timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical UTC timestamp string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{label} must use UTC")
    canonical = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if value != canonical:
        raise ValueError(f"{label} must be a canonical UTC timestamp string")
    return parsed


def _logical_uri(value: object, label: str) -> str:
    if not isinstance(value, str) or "\\" in value or "://" in value:
        raise ValueError(f"{label} must be a relative logical URI")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or not pure.parts
        or pure.as_posix() != value
        or any(":" in part or part.endswith((" ", ".")) for part in pure.parts)
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"{label} must be a relative logical URI")
    return value


def _revision(value: object, label: str) -> str:
    if not isinstance(value, str) or not _REVISION.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase 40- or 64-character revision")
    return value


def _source_key(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SOURCE_KEY.fullmatch(value):
        raise ValueError(f"{label} must be a canonical uppercase source key")
    return value


def _tuple_of(value: object, expected_type: type, label: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple) or not value or not all(
        isinstance(item, expected_type) for item in value
    ):
        raise TypeError(f"{label} must be a nonempty tuple of {expected_type.__name__}")
    return value


__all__ = [
    "DEFAULT_EXPECTED_PARQUET_FILES",
    "DEFAULT_HEARTBEAT_SECONDS",
    "DEFAULT_MINIMUM_FREE_BYTES",
    "EXPECTED_DECLARED_SCHEMAS",
    "EXPECTED_SOURCES",
    "GIB",
    "RECOVERY_HASH_CACHE_SCHEMA_VERSION",
    "RECOVERY_ID_NAMESPACE",
    "RECOVERY_MANIFEST_SCHEMA_VERSION",
    "ContentAddressedJsonArtifact",
    "FD1RecoveryIntegrityError",
    "FD1RecoveryManifestError",
    "FD1RecoveryStorageError",
    "FamilyAggregate",
    "FinalizerLineage",
    "GenerationLineage",
    "IdentitySpoolBinding",
    "InterruptedExecutionContext",
    "ParsedPartitionUri",
    "PhysicalSchemaField",
    "RecoveryFileEntry",
    "RecoveryHashCache",
    "RecoveryHashCacheEntry",
    "RecoveryManifest",
    "RecoveryManifestAggregate",
    "RecoveryManifestBuildResult",
    "RecoveryProgress",
    "SourceArchiveBinding",
    "SourceReleaseBinding",
    "TrustedDryRunLedger",
    "build_recovery_manifest",
    "derive_recovery_id",
    "load_recovery_hash_cache",
    "load_recovery_manifest",
    "manifest_paths",
    "parse_partition_uri",
    "write_recovery_hash_cache",
    "write_recovery_manifest",
]
