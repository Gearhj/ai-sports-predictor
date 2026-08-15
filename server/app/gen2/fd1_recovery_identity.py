"""Deterministic, provider-free identity spools for Gate 3C-FD1 recovery.

The recovery finalizer must not repeat ORM ``SELECT``/``flush`` loops for the
hundreds of thousands of FD1 identity assertions.  This module converts the
already-governed :mod:`app.gen2.fd1_identity` batches into one compact,
line-bounded CSV suitable for PostgreSQL ``COPY``.

The spool deliberately contains no recovery/run identifier.  Its bytes are a
stable function of the frozen source releases and identity evidence, so the
same content can be reused by rehearsal, production, and resume runs.  No
function in this module discovers a raw directory, opens a network transport,
or reads Retrosheet event/daily-log data.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
import csv
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid5

from app.gen2.fd1_identity import (
    FD1IdentityPriorImport,
    FD1IdentityPriorSourceBundle,
    ImportLimits,
    RegistryIdentityMappingBatch,
    RetrosheetOrganizationCrosswalkReport,
    SourceFileSpec,
    build_retrosheet_organization_crosswalk,
    import_fd1_identity_priors,
    iter_all_registry_person_identity_mapping_batches,
    iter_registry_organization_identity_mapping_batches,
)
from app.gen2.open_data_registry import (
    OpenDataIdentityMappingSpec,
    build_identity_mapping_identity,
    canonical_sha256,
)


IDENTITY_SPOOL_SCHEMA_VERSION = "gate3c_fd1_recovery_identity_spool_v1"
IDENTITY_SPOOL_COLUMNS = (
    "staging_ordinal",
    "batch_number",
    "batch_checksum",
    "source_key",
    "source_version_id",
    "source_release_identity_checksum",
    "entity_type",
    "source_namespace",
    "external_id",
    "canonical_namespace",
    "canonical_id",
    "source_row_number",
    "source_row_checksum",
    "confidence",
    "valid_from",
    "valid_through",
    "evidence_logical_uri",
    "mapping_basis",
    "identity_checksum",
    "record_checksum",
    "target_mapping_id",
)
IDENTITY_SPOOL_SCHEMA_CHECKSUM = canonical_sha256(
    {
        "schema_version": IDENTITY_SPOOL_SCHEMA_VERSION,
        "columns": list(IDENTITY_SPOOL_COLUMNS),
        "encoding": "utf-8",
        "line_ending": "LF",
        "optional_value": "empty_field",
    }
)
DEFAULT_IDENTITY_BATCH_SIZE = 10_000
MAX_IDENTITY_BATCH_SIZE = 10_000
DEFAULT_MIN_FREE_BYTES = 100 * 1024**3
DEFAULT_MAX_ROW_BYTES = 2 * 1024**2

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_ADDRESSED_NAME = re.compile(
    r"^fd1-identity-spool-sha256-([0-9a-f]{64})\.csv$"
)
IDENTITY_TARGET_UUID_NAMESPACE = UUID("418ebff6-c8f3-4a91-970d-7ec71c39c944")
_MAPPING_BASIS = MappingProxyType(
    {
        "EXACT": "EXPLICIT_IDENTIFIER_EXACT",
        "HIGH": "EXPLICIT_IDENTIFIER_HIGH",
        "UNRESOLVED": "UNRESOLVED_IDENTIFIER_EVIDENCE",
        "CONFLICT": "CONFLICTING_IDENTIFIER_EVIDENCE",
    }
)


class FD1RecoveryIdentityError(ValueError):
    """The identity spool or its frozen source context failed closed."""


@dataclass(frozen=True, slots=True)
class IdentitySourceRelease:
    """Minimal immutable registry context required by a spool record."""

    source_key: str
    source_version_id: str
    source_release_identity_checksum: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_key, str) or not re.fullmatch(
            r"[A-Z][A-Z0-9_]{2,79}", self.source_key
        ):
            raise FD1RecoveryIdentityError("identity source_key is invalid")
        _canonical_uuid(self.source_version_id, "source_version_id")
        _checksum(
            self.source_release_identity_checksum,
            "source_release_identity_checksum",
        )

    @classmethod
    def from_registry_row(cls, row: object) -> IdentitySourceRelease:
        """Freeze the three fields used from a ``HistoricalSourceVersion``.

        The caller performs the database query explicitly.  Accepting the row
        as an object keeps this module independent of a SQL session and makes
        it impossible for spool creation to enumerate registry or raw data.
        """

        try:
            source_key = str(getattr(row, "source_key"))
            source_version_id = str(getattr(row, "id"))
            checksum = str(getattr(row, "identity_checksum"))
        except (AttributeError, TypeError) as exc:
            raise FD1RecoveryIdentityError(
                "registry source row lacks frozen identity fields"
            ) from exc
        source_mode = getattr(row, "source_mode", "LIVE")
        if source_mode != "LIVE":
            raise FD1RecoveryIdentityError(
                "FD1 recovery identity sources must be LIVE"
            )
        return cls(source_key, source_version_id, checksum)


@dataclass(frozen=True, slots=True)
class IdentitySpoolProgress:
    phase: str
    rows_processed: int
    bytes_processed: int
    batches_processed: int
    current_source_key: str | None = None


IdentitySpoolProgressCallback = Callable[[IdentitySpoolProgress], None]


@dataclass(frozen=True, slots=True)
class IdentitySpoolBinding:
    """Verified content identity and resume metadata for one complete spool."""

    path: Path
    schema_version: str
    schema_checksum: str
    sha256: str
    byte_count: int
    row_count: int
    batch_count: int
    data_byte_offset: int
    source_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != IDENTITY_SPOOL_SCHEMA_VERSION:
            raise FD1RecoveryIdentityError("identity spool schema version is invalid")
        if self.schema_checksum != IDENTITY_SPOOL_SCHEMA_CHECKSUM:
            raise FD1RecoveryIdentityError("identity spool schema checksum is invalid")
        _checksum(self.sha256, "identity spool checksum")
        if self.byte_count <= self.data_byte_offset or self.data_byte_offset <= 0:
            raise FD1RecoveryIdentityError("identity spool byte counts are invalid")
        if self.row_count <= 0 or self.batch_count <= 0:
            raise FD1RecoveryIdentityError("identity spool must contain records")
        if not self.source_keys or tuple(sorted(set(self.source_keys))) != self.source_keys:
            raise FD1RecoveryIdentityError("identity spool source keys are invalid")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "schema_checksum": self.schema_checksum,
            "relative_or_runtime_path": self.path.as_posix(),
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "row_count": self.row_count,
            "batch_count": self.batch_count,
            "data_byte_offset": self.data_byte_offset,
            "source_keys": list(self.source_keys),
        }


@dataclass(frozen=True, slots=True)
class IdentitySpoolChunk:
    """One bounded set of canonical rows plus a durable next-byte cursor."""

    records: tuple[Mapping[str, str], ...]
    start_byte_offset: int
    next_byte_offset: int
    next_ordinal: int
    eof: bool


@dataclass(frozen=True, slots=True)
class _IdentitySpoolRow:
    ordinal: int
    batch_number: int
    batch_checksum: str
    source_key: str
    source_version_id: str
    source_release_identity_checksum: str
    spec: OpenDataIdentityMappingSpec
    mapping_basis: str
    identity_checksum: str
    record_checksum: str
    target_mapping_id: str

    def __post_init__(self) -> None:
        if self.ordinal < 1 or self.batch_number < 1:
            raise FD1RecoveryIdentityError("identity spool ordinals must be positive")
        _checksum(self.batch_checksum, "batch_checksum")
        if self.source_key != self.spec.source_key:
            raise FD1RecoveryIdentityError("spool source_key differs from mapping spec")
        _canonical_uuid(self.source_version_id, "source_version_id")
        _checksum(
            self.source_release_identity_checksum,
            "source_release_identity_checksum",
        )
        expected_basis = _mapping_basis(self.spec.confidence)
        if self.mapping_basis != expected_basis:
            raise FD1RecoveryIdentityError("mapping_basis is inconsistent")
        expected_identity = build_identity_mapping_identity(
            source_version_identity_checksum=(
                self.source_release_identity_checksum
            ),
            spec=self.spec,
        )
        if self.identity_checksum != expected_identity:
            raise FD1RecoveryIdentityError("identity_checksum is inconsistent")
        expected_target = str(
            uuid5(IDENTITY_TARGET_UUID_NAMESPACE, self.identity_checksum)
        )
        if self.target_mapping_id != expected_target:
            raise FD1RecoveryIdentityError("target_mapping_id is inconsistent")
        if self.record_checksum != self.expected_record_checksum():
            raise FD1RecoveryIdentityError("record_checksum is inconsistent")

    @classmethod
    def from_spec(
        cls,
        *,
        ordinal: int,
        batch: RegistryIdentityMappingBatch,
        spec: OpenDataIdentityMappingSpec,
        source: IdentitySourceRelease,
    ) -> _IdentitySpoolRow:
        identity_checksum = build_identity_mapping_identity(
            source_version_identity_checksum=(
                source.source_release_identity_checksum
            ),
            spec=spec,
        )
        mapping_basis = _mapping_basis(spec.confidence)
        target_mapping_id = str(
            uuid5(IDENTITY_TARGET_UUID_NAMESPACE, identity_checksum)
        )
        record_checksum = canonical_sha256(
            _record_checksum_payload(
                ordinal=ordinal,
                batch_number=batch.batch_number,
                batch_checksum=batch.batch_checksum,
                source_key=spec.source_key,
                source_version_id=source.source_version_id,
                source_release_identity_checksum=(
                    source.source_release_identity_checksum
                ),
                spec=spec,
                mapping_basis=mapping_basis,
                identity_checksum=identity_checksum,
                target_mapping_id=target_mapping_id,
            )
        )
        return cls(
            ordinal=ordinal,
            batch_number=batch.batch_number,
            batch_checksum=batch.batch_checksum,
            source_key=spec.source_key,
            source_version_id=source.source_version_id,
            source_release_identity_checksum=(
                source.source_release_identity_checksum
            ),
            spec=spec,
            mapping_basis=mapping_basis,
            identity_checksum=identity_checksum,
            record_checksum=record_checksum,
            target_mapping_id=target_mapping_id,
        )

    @classmethod
    def from_serialized(cls, values: Sequence[str]) -> _IdentitySpoolRow:
        if len(values) != len(IDENTITY_SPOOL_COLUMNS):
            raise FD1RecoveryIdentityError("identity spool column count is invalid")
        row = dict(zip(IDENTITY_SPOOL_COLUMNS, values, strict=True))
        spec = OpenDataIdentityMappingSpec(
            source_key=row["source_key"],
            entity_type=row["entity_type"],
            source_namespace=row["source_namespace"],
            external_id=row["external_id"],
            canonical_namespace=row["canonical_namespace"],
            canonical_id=_optional(row["canonical_id"]),
            source_row_number=_positive_int(
                row["source_row_number"], "source_row_number"
            ),
            source_row_checksum=row["source_row_checksum"],
            confidence=row["confidence"],
            evidence_logical_uri=row["evidence_logical_uri"],
            valid_from=_optional_date(row["valid_from"], "valid_from"),
            valid_through=_optional_date(row["valid_through"], "valid_through"),
        )
        return cls(
            ordinal=_positive_int(row["staging_ordinal"], "staging_ordinal"),
            batch_number=_positive_int(row["batch_number"], "batch_number"),
            batch_checksum=row["batch_checksum"],
            source_key=row["source_key"],
            source_version_id=row["source_version_id"],
            source_release_identity_checksum=(
                row["source_release_identity_checksum"]
            ),
            spec=spec,
            mapping_basis=row["mapping_basis"],
            identity_checksum=row["identity_checksum"],
            record_checksum=row["record_checksum"],
            target_mapping_id=row["target_mapping_id"],
        )

    def checksum_payload(self) -> dict[str, Any]:
        return _record_checksum_payload(
            ordinal=self.ordinal,
            batch_number=self.batch_number,
            batch_checksum=self.batch_checksum,
            source_key=self.source_key,
            source_version_id=self.source_version_id,
            source_release_identity_checksum=(
                self.source_release_identity_checksum
            ),
            spec=self.spec,
            mapping_basis=self.mapping_basis,
            identity_checksum=self.identity_checksum,
            target_mapping_id=self.target_mapping_id,
        )

    def expected_record_checksum(self) -> str:
        return canonical_sha256(self.checksum_payload())

    def as_serialized_dict(
        self, *, include_record_checksum: bool = True
    ) -> dict[str, str]:
        values = {
            "staging_ordinal": str(self.ordinal),
            "batch_number": str(self.batch_number),
            "batch_checksum": self.batch_checksum,
            "source_key": self.source_key,
            "source_version_id": self.source_version_id,
            "source_release_identity_checksum": (
                self.source_release_identity_checksum
            ),
            "entity_type": self.spec.entity_type,
            "source_namespace": self.spec.source_namespace,
            "external_id": self.spec.external_id,
            "canonical_namespace": self.spec.canonical_namespace,
            "canonical_id": self.spec.canonical_id or "",
            "source_row_number": str(self.spec.source_row_number),
            "source_row_checksum": self.spec.source_row_checksum,
            "confidence": self.spec.confidence,
            "valid_from": self.spec.valid_from.isoformat() if self.spec.valid_from else "",
            "valid_through": (
                self.spec.valid_through.isoformat() if self.spec.valid_through else ""
            ),
            "evidence_logical_uri": self.spec.evidence_logical_uri,
            "mapping_basis": self.mapping_basis,
            "identity_checksum": self.identity_checksum,
        }
        if include_record_checksum:
            values["record_checksum"] = self.record_checksum
        values["target_mapping_id"] = self.target_mapping_id
        return values

    def csv_values(self) -> tuple[str, ...]:
        values = self.as_serialized_dict()
        return tuple(values[column] for column in IDENTITY_SPOOL_COLUMNS)


def iter_fd1_identity_mapping_batches(
    identity_import: FD1IdentityPriorImport,
    *,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
    retrosheet_report: RetrosheetOrganizationCrosswalkReport,
    batch_size: int = DEFAULT_IDENTITY_BATCH_SIZE,
) -> Iterator[RegistryIdentityMappingBatch]:
    """Yield the original deterministic person and organization batches."""

    _batch_size(batch_size)
    yield from iter_all_registry_person_identity_mapping_batches(
        identity_import.people,
        evidence_logical_uri_by_file_checksum=(
            evidence_logical_uri_by_file_checksum
        ),
        batch_size=batch_size,
    )
    yield from iter_registry_organization_identity_mapping_batches(
        identity_import.organizations,
        evidence_logical_uri_by_file_checksum=(
            evidence_logical_uri_by_file_checksum
        ),
        retrosheet_report=retrosheet_report,
        batch_size=batch_size,
    )


def build_identity_spool_from_frozen_sources(
    *,
    source_bundle: FD1IdentityPriorSourceBundle,
    retrosheet_teams: SourceFileSpec,
    retrosheet_ballparks: SourceFileSpec,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
    source_releases: Iterable[IdentitySourceRelease],
    output_directory: Path,
    batch_size: int = DEFAULT_IDENTITY_BATCH_SIZE,
    limits: ImportLimits | None = None,
    expected_row_count: int | None = None,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    progress_callback: IdentitySpoolProgressCallback | None = None,
) -> IdentitySpoolBinding:
    """Build a spool from only explicitly supplied immutable source files.

    ``source_bundle`` names every Chadwick/Lahman file and the two Retrosheet
    arguments name only the auxiliary organization catalogs.  There is no
    directory walk, event-file input, database query, or provider transport at
    this boundary.
    """

    bounds = limits or ImportLimits()
    identity_import = import_fd1_identity_priors(source_bundle, limits=bounds)
    retrosheet_report = build_retrosheet_organization_crosswalk(
        identity_import.organizations,
        retrosheet_teams=retrosheet_teams,
        retrosheet_ballparks=retrosheet_ballparks,
        limits=bounds,
    )
    return write_identity_spool(
        iter_fd1_identity_mapping_batches(
            identity_import,
            evidence_logical_uri_by_file_checksum=(
                evidence_logical_uri_by_file_checksum
            ),
            retrosheet_report=retrosheet_report,
            batch_size=batch_size,
        ),
        source_releases=source_releases,
        output_directory=output_directory,
        expected_row_count=expected_row_count,
        min_free_bytes=min_free_bytes,
        progress_callback=progress_callback,
    )


def write_identity_spool(
    batches: Iterable[RegistryIdentityMappingBatch],
    *,
    source_releases: Iterable[IdentitySourceRelease],
    output_directory: Path,
    expected_row_count: int | None = None,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    progress_callback: IdentitySpoolProgressCallback | None = None,
) -> IdentitySpoolBinding:
    """Stream validated batches to an atomic content-addressed CSV."""

    releases = _source_release_map(source_releases)
    if expected_row_count is not None and (
        isinstance(expected_row_count, bool)
        or not isinstance(expected_row_count, int)
        or expected_row_count <= 0
    ):
        raise FD1RecoveryIdentityError("expected_row_count must be positive")
    _reserve_value(min_free_bytes)
    root = _prepare_output_directory(Path(output_directory))
    _require_free_space(root, min_free_bytes)

    header = _csv_bytes(IDENTITY_SPOOL_COLUMNS)
    digest = sha256()
    row_count = 0
    byte_count = 0
    batch_count = 0
    used_sources: set[str] = set()
    previous_batch: tuple[int, str] | None = None
    temp_path: Path | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".fd1-identity-spool-", suffix=".tmp", dir=root
        )
        temp_path = Path(temp_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(header)
            digest.update(header)
            byte_count += len(header)
            _notify(progress_callback, "BUILD", 0, byte_count, 0, None)
            for batch in batches:
                if not isinstance(batch, RegistryIdentityMappingBatch):
                    raise FD1RecoveryIdentityError(
                        "identity spool accepts validated registry batches only"
                    )
                if len(batch.specs) > MAX_IDENTITY_BATCH_SIZE:
                    raise FD1RecoveryIdentityError(
                        "identity batch exceeds the 10,000-row recovery bound"
                    )
                if _batch_checksum(batch.specs) != batch.batch_checksum:
                    raise FD1RecoveryIdentityError(
                        "identity batch checksum does not match its specs"
                    )
                batch_identity = (batch.batch_number, batch.batch_checksum)
                if batch_identity == previous_batch:
                    raise FD1RecoveryIdentityError(
                        "adjacent identity batches have an ambiguous identity"
                    )
                previous_batch = batch_identity
                for spec in batch.specs:
                    try:
                        source = releases[spec.source_key]
                    except KeyError as exc:
                        raise FD1RecoveryIdentityError(
                            f"no frozen source release for {spec.source_key}"
                        ) from exc
                    used_sources.add(spec.source_key)
                    row_count += 1
                    row = _IdentitySpoolRow.from_spec(
                        ordinal=row_count,
                        batch=batch,
                        spec=spec,
                        source=source,
                    )
                    encoded = _csv_bytes(row.csv_values())
                    if len(encoded) > DEFAULT_MAX_ROW_BYTES:
                        raise FD1RecoveryIdentityError(
                            "identity spool row exceeds max_row_bytes"
                        )
                    handle.write(encoded)
                    digest.update(encoded)
                    byte_count += len(encoded)
                batch_count += 1
                _require_free_space(root, min_free_bytes)
                _notify(
                    progress_callback,
                    "BUILD",
                    row_count,
                    byte_count,
                    batch_count,
                    batch.specs[-1].source_key,
                )
            handle.flush()
            os.fsync(handle.fileno())

        if row_count == 0:
            raise FD1RecoveryIdentityError("identity spool cannot be empty")
        if expected_row_count is not None and row_count != expected_row_count:
            raise FD1RecoveryIdentityError(
                f"identity spool row count {row_count} != {expected_row_count}"
            )
        unused_sources = set(releases) - used_sources
        if unused_sources:
            raise FD1RecoveryIdentityError(
                "frozen source releases were not represented: "
                + ", ".join(sorted(unused_sources))
            )
        spool_checksum = digest.hexdigest()
        final_path = root / (
            f"fd1-identity-spool-sha256-{spool_checksum}.csv"
        )
        if final_path.exists():
            if not final_path.is_file() or final_path.is_symlink():
                raise FD1RecoveryIdentityError(
                    "content-addressed spool target is not a regular file"
                )
            stat = final_path.stat()
            if stat.st_size != byte_count or _file_sha256(final_path) != spool_checksum:
                raise FD1RecoveryIdentityError(
                    "content-addressed spool target contains different bytes"
                )
            temp_path.unlink()
            temp_path = None
        else:
            os.replace(temp_path, final_path)
            temp_path = None
        binding = IdentitySpoolBinding(
            path=final_path.resolve(strict=True),
            schema_version=IDENTITY_SPOOL_SCHEMA_VERSION,
            schema_checksum=IDENTITY_SPOOL_SCHEMA_CHECKSUM,
            sha256=spool_checksum,
            byte_count=byte_count,
            row_count=row_count,
            batch_count=batch_count,
            data_byte_offset=len(header),
            source_keys=tuple(sorted(used_sources)),
        )
        _notify(
            progress_callback,
            "COMPLETE",
            row_count,
            byte_count,
            batch_count,
            None,
        )
        return binding
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def load_identity_spool_binding(
    path: Path,
    *,
    expected_sha256: str | None = None,
    expected_byte_count: int | None = None,
    expected_row_count: int | None = None,
    source_releases: Iterable[IdentitySourceRelease] | None = None,
    max_row_bytes: int = DEFAULT_MAX_ROW_BYTES,
    progress_callback: IdentitySpoolProgressCallback | None = None,
) -> IdentitySpoolBinding:
    """Stream and fully validate a spool before COPY or resume.

    Validation checks the exact header and byte encoding, every record and
    record checksum, every original batch checksum, contiguous ordinals, the
    content-addressed filename, and an unchanged stat before/after the pass.
    """

    spool_path = _regular_file(Path(path))
    if expected_sha256 is not None:
        _checksum(expected_sha256, "expected spool checksum")
    if expected_byte_count is not None and expected_byte_count <= 0:
        raise FD1RecoveryIdentityError("expected_byte_count must be positive")
    if expected_row_count is not None and expected_row_count <= 0:
        raise FD1RecoveryIdentityError("expected_row_count must be positive")
    if max_row_bytes <= 0:
        raise FD1RecoveryIdentityError("max_row_bytes must be positive")
    releases = (
        _source_release_map(source_releases)
        if source_releases is not None
        else None
    )

    before = spool_path.stat()
    digest = sha256()
    row_count = 0
    batch_count = 0
    source_keys: set[str] = set()
    current_batch: tuple[int, str] | None = None
    current_specs: list[OpenDataIdentityMappingSpec] = []
    header = _csv_bytes(IDENTITY_SPOOL_COLUMNS)
    with spool_path.open("rb") as handle:
        actual_header = handle.readline(len(header) + 1)
        if actual_header != header:
            raise FD1RecoveryIdentityError("identity spool header/schema is invalid")
        digest.update(actual_header)
        _notify(progress_callback, "VALIDATE", 0, handle.tell(), 0, None)
        while raw := handle.readline(max_row_bytes + 1):
            if len(raw) > max_row_bytes:
                raise FD1RecoveryIdentityError(
                    "identity spool row exceeds max_row_bytes"
                )
            row = _parse_canonical_row(raw)
            if row.ordinal != row_count + 1:
                raise FD1RecoveryIdentityError(
                    "identity spool ordinals are not contiguous"
                )
            if releases is not None:
                try:
                    release = releases[row.source_key]
                except KeyError as exc:
                    raise FD1RecoveryIdentityError(
                        f"spool contains unbound source {row.source_key}"
                    ) from exc
                if (
                    row.source_version_id != release.source_version_id
                    or row.source_release_identity_checksum
                    != release.source_release_identity_checksum
                ):
                    raise FD1RecoveryIdentityError(
                        "spool source release differs from frozen registry context"
                    )
            batch_identity = (row.batch_number, row.batch_checksum)
            if current_batch is None:
                current_batch = batch_identity
            elif batch_identity != current_batch:
                _validate_spool_batch(current_batch, current_specs)
                batch_count += 1
                _notify(
                    progress_callback,
                    "VALIDATE",
                    row_count,
                    handle.tell() - len(raw),
                    batch_count,
                    row.source_key,
                )
                current_specs = []
                current_batch = batch_identity
            current_specs.append(row.spec)
            if len(current_specs) > MAX_IDENTITY_BATCH_SIZE:
                raise FD1RecoveryIdentityError(
                    "identity spool batch exceeds the 10,000-row bound"
                )
            row_count += 1
            source_keys.add(row.source_key)
            digest.update(raw)
    if current_batch is None:
        raise FD1RecoveryIdentityError("identity spool cannot be empty")
    _validate_spool_batch(current_batch, current_specs)
    batch_count += 1
    after = spool_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise FD1RecoveryIdentityError("identity spool changed during validation")
    spool_checksum = digest.hexdigest()
    match = _CONTENT_ADDRESSED_NAME.fullmatch(spool_path.name)
    if match is None or match.group(1) != spool_checksum:
        raise FD1RecoveryIdentityError(
            "identity spool filename does not match its content checksum"
        )
    if expected_sha256 is not None and spool_checksum != expected_sha256:
        raise FD1RecoveryIdentityError("identity spool checksum differs from binding")
    if expected_byte_count is not None and after.st_size != expected_byte_count:
        raise FD1RecoveryIdentityError("identity spool byte count differs from binding")
    if expected_row_count is not None and row_count != expected_row_count:
        raise FD1RecoveryIdentityError("identity spool row count differs from binding")
    if releases is not None and set(releases) != source_keys:
        raise FD1RecoveryIdentityError(
            "spool source population differs from frozen registry context"
        )
    binding = IdentitySpoolBinding(
        path=spool_path,
        schema_version=IDENTITY_SPOOL_SCHEMA_VERSION,
        schema_checksum=IDENTITY_SPOOL_SCHEMA_CHECKSUM,
        sha256=spool_checksum,
        byte_count=after.st_size,
        row_count=row_count,
        batch_count=batch_count,
        data_byte_offset=len(header),
        source_keys=tuple(sorted(source_keys)),
    )
    _notify(
        progress_callback,
        "VALIDATED",
        row_count,
        after.st_size,
        batch_count,
        None,
    )
    return binding


def iter_identity_spool_records(
    path: Path,
    *,
    start_ordinal: int = 1,
    start_byte_offset: int | None = None,
    max_row_bytes: int = DEFAULT_MAX_ROW_BYTES,
) -> Iterator[Mapping[str, str]]:
    """Yield strict canonical mappings, optionally from a durable byte cursor.

    Every yielded mapping contains exactly :data:`IDENTITY_SPOOL_COLUMNS`.
    For efficient resume, persist both the ``next_byte_offset`` and
    ``next_ordinal`` returned by :func:`read_identity_spool_chunk`.
    """

    if start_ordinal < 1:
        raise FD1RecoveryIdentityError("start_ordinal must be positive")
    spool_path = _regular_file(Path(path))
    header = _csv_bytes(IDENTITY_SPOOL_COLUMNS)
    with spool_path.open("rb") as handle:
        if handle.readline(len(header) + 1) != header:
            raise FD1RecoveryIdentityError("identity spool header/schema is invalid")
        data_offset = handle.tell()
        if start_byte_offset is not None:
            _seek_record_boundary(
                handle,
                start_byte_offset,
                minimum=data_offset,
                file_size=spool_path.stat().st_size,
            )
        expected = start_ordinal
        while raw := handle.readline(max_row_bytes + 1):
            if len(raw) > max_row_bytes:
                raise FD1RecoveryIdentityError(
                    "identity spool row exceeds max_row_bytes"
                )
            row = _parse_canonical_row(raw)
            if start_byte_offset is None and row.ordinal < start_ordinal:
                continue
            if row.ordinal != expected:
                raise FD1RecoveryIdentityError(
                    "identity spool resume ordinal does not match byte cursor"
                )
            expected += 1
            yield MappingProxyType(row.as_serialized_dict())


def read_identity_spool_chunk(
    path: Path,
    *,
    start_ordinal: int = 1,
    start_byte_offset: int | None = None,
    max_rows: int = DEFAULT_IDENTITY_BATCH_SIZE,
    max_row_bytes: int = DEFAULT_MAX_ROW_BYTES,
) -> IdentitySpoolChunk:
    """Read at most ``max_rows`` and return the exact next resume position."""

    if start_ordinal < 1:
        raise FD1RecoveryIdentityError("start_ordinal must be positive")
    if max_rows <= 0 or max_rows > MAX_IDENTITY_BATCH_SIZE:
        raise FD1RecoveryIdentityError(
            "max_rows must be between 1 and 10,000"
        )
    spool_path = _regular_file(Path(path))
    header = _csv_bytes(IDENTITY_SPOOL_COLUMNS)
    records: list[Mapping[str, str]] = []
    with spool_path.open("rb") as handle:
        if handle.readline(len(header) + 1) != header:
            raise FD1RecoveryIdentityError("identity spool header/schema is invalid")
        data_offset = handle.tell()
        if start_byte_offset is None:
            if start_ordinal != 1:
                # A missing byte cursor is safe but intentionally O(n); callers
                # should persist the returned offset for subsequent chunks.
                while True:
                    candidate_offset = handle.tell()
                    raw = handle.readline(max_row_bytes + 1)
                    if not raw:
                        raise FD1RecoveryIdentityError(
                            "start_ordinal lies beyond the identity spool"
                        )
                    row = _parse_canonical_row(raw)
                    if row.ordinal == start_ordinal:
                        handle.seek(candidate_offset)
                        break
                    if row.ordinal > start_ordinal:
                        raise FD1RecoveryIdentityError(
                            "identity spool ordinals are not contiguous"
                        )
            actual_start = handle.tell()
        else:
            _seek_record_boundary(
                handle,
                start_byte_offset,
                minimum=data_offset,
                file_size=spool_path.stat().st_size,
            )
            actual_start = start_byte_offset
        expected = start_ordinal
        while len(records) < max_rows:
            raw = handle.readline(max_row_bytes + 1)
            if not raw:
                break
            if len(raw) > max_row_bytes:
                raise FD1RecoveryIdentityError(
                    "identity spool row exceeds max_row_bytes"
                )
            row = _parse_canonical_row(raw)
            if row.ordinal != expected:
                raise FD1RecoveryIdentityError(
                    "identity spool resume ordinal does not match byte cursor"
                )
            records.append(MappingProxyType(row.as_serialized_dict()))
            expected += 1
        next_offset = handle.tell()
        marker = handle.read(1)
        eof = marker == b""
    return IdentitySpoolChunk(
        records=tuple(records),
        start_byte_offset=actual_start,
        next_byte_offset=next_offset,
        next_ordinal=expected,
        eof=eof,
    )


def _source_release_map(
    releases: Iterable[IdentitySourceRelease],
) -> dict[str, IdentitySourceRelease]:
    result: dict[str, IdentitySourceRelease] = {}
    for release in releases:
        if not isinstance(release, IdentitySourceRelease):
            raise FD1RecoveryIdentityError(
                "source_releases must contain IdentitySourceRelease values"
            )
        if release.source_key in result:
            raise FD1RecoveryIdentityError(
                f"duplicate frozen source release: {release.source_key}"
            )
        result[release.source_key] = release
    if not result:
        raise FD1RecoveryIdentityError("at least one frozen source release is required")
    return result


def _mapping_basis(confidence: str) -> str:
    try:
        return _MAPPING_BASIS[confidence]
    except KeyError as exc:
        raise FD1RecoveryIdentityError(
            "identity confidence has no explicit mapping basis"
        ) from exc


def _batch_checksum(specs: Sequence[OpenDataIdentityMappingSpec]) -> str:
    payload = [_spec_payload(spec) for spec in specs]
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _spec_payload(spec: OpenDataIdentityMappingSpec) -> dict[str, Any]:
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


def _record_checksum_payload(
    *,
    ordinal: int,
    batch_number: int,
    batch_checksum: str,
    source_key: str,
    source_version_id: str,
    source_release_identity_checksum: str,
    spec: OpenDataIdentityMappingSpec,
    mapping_basis: str,
    identity_checksum: str,
    target_mapping_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": IDENTITY_SPOOL_SCHEMA_VERSION,
        "staging_ordinal": str(ordinal),
        "batch_number": str(batch_number),
        "batch_checksum": batch_checksum,
        "source_key": source_key,
        "source_version_id": source_version_id,
        "source_release_identity_checksum": (
            source_release_identity_checksum
        ),
        "entity_type": spec.entity_type,
        "source_namespace": spec.source_namespace,
        "external_id": spec.external_id,
        "canonical_namespace": spec.canonical_namespace,
        "canonical_id": spec.canonical_id or "",
        "source_row_number": str(spec.source_row_number),
        "source_row_checksum": spec.source_row_checksum,
        "confidence": spec.confidence,
        "valid_from": spec.valid_from.isoformat() if spec.valid_from else "",
        "valid_through": spec.valid_through.isoformat() if spec.valid_through else "",
        "evidence_logical_uri": spec.evidence_logical_uri,
        "mapping_basis": mapping_basis,
        "identity_checksum": identity_checksum,
        "target_mapping_id": target_mapping_id,
    }


def _validate_spool_batch(
    identity: tuple[int, str], specs: Sequence[OpenDataIdentityMappingSpec]
) -> None:
    if not specs or len(specs) > MAX_IDENTITY_BATCH_SIZE:
        raise FD1RecoveryIdentityError("identity spool batch size is invalid")
    if _batch_checksum(specs) != identity[1]:
        raise FD1RecoveryIdentityError(
            "identity spool original batch checksum is invalid"
        )


def _parse_canonical_row(raw: bytes) -> _IdentitySpoolRow:
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise FD1RecoveryIdentityError(
            "identity spool records must be LF-terminated"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FD1RecoveryIdentityError("identity spool is not canonical UTF-8") from exc
    try:
        values = next(csv.reader([text], strict=True))
    except (csv.Error, StopIteration) as exc:
        raise FD1RecoveryIdentityError("identity spool CSV record is invalid") from exc
    try:
        row = _IdentitySpoolRow.from_serialized(values)
    except FD1RecoveryIdentityError:
        raise
    except (TypeError, ValueError) as exc:
        raise FD1RecoveryIdentityError(
            "identity spool record violates the mapping schema"
        ) from exc
    if _csv_bytes(row.csv_values()) != raw:
        raise FD1RecoveryIdentityError("identity spool record is not canonical CSV")
    return row


def _csv_bytes(values: Sequence[str]) -> bytes:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerow(values)
    return buffer.getvalue().encode("utf-8")


def _positive_int(value: str, field_name: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise FD1RecoveryIdentityError(f"{field_name} must be a positive integer")
    parsed = int(value)
    if parsed < 1 or str(parsed) != value:
        raise FD1RecoveryIdentityError(f"{field_name} is not canonical")
    return parsed


def _optional(value: str) -> str | None:
    return value if value != "" else None


def _optional_date(value: str, field_name: str) -> date | None:
    if value == "":
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise FD1RecoveryIdentityError(f"{field_name} must be ISO-8601") from exc
    if parsed.isoformat() != value:
        raise FD1RecoveryIdentityError(f"{field_name} is not canonical")
    return parsed


def _canonical_uuid(value: str, field_name: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise FD1RecoveryIdentityError(f"{field_name} must be a UUID") from exc
    if str(parsed) != value:
        raise FD1RecoveryIdentityError(f"{field_name} must be canonical")
    return value


def _checksum(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise FD1RecoveryIdentityError(
            f"{field_name} must be a lowercase SHA-256 digest"
        )
    return value


def _batch_size(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > MAX_IDENTITY_BATCH_SIZE
    ):
        raise FD1RecoveryIdentityError(
            "identity batch_size must be between 1 and 10,000"
        )
    return value


def _reserve_value(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FD1RecoveryIdentityError("min_free_bytes must be nonnegative")
    return value


def _prepare_output_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise FD1RecoveryIdentityError(
            "identity spool output must be a regular directory"
        )
    return path.resolve(strict=True)


def _regular_file(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise FD1RecoveryIdentityError("identity spool path is not a regular file")
    return path.resolve(strict=True)


def _require_free_space(path: Path, minimum: int) -> None:
    free = shutil.disk_usage(path).free
    if free < minimum:
        raise FD1RecoveryIdentityError(
            f"free-space reserve breached: {free} < {minimum} bytes"
        )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _seek_record_boundary(
    handle: Any,
    offset: int,
    *,
    minimum: int,
    file_size: int,
) -> None:
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise FD1RecoveryIdentityError("start_byte_offset must be an integer")
    if offset < minimum or offset > file_size:
        raise FD1RecoveryIdentityError("start_byte_offset is outside the spool")
    if offset > minimum:
        handle.seek(offset - 1)
        if handle.read(1) != b"\n":
            raise FD1RecoveryIdentityError(
                "start_byte_offset is not a record boundary"
            )
    handle.seek(offset)


def _notify(
    callback: IdentitySpoolProgressCallback | None,
    phase: str,
    rows: int,
    byte_count: int,
    batches: int,
    source_key: str | None,
) -> None:
    if callback is not None:
        callback(
            IdentitySpoolProgress(
                phase=phase,
                rows_processed=rows,
                bytes_processed=byte_count,
                batches_processed=batches,
                current_source_key=source_key,
            )
        )


__all__ = [
    "DEFAULT_IDENTITY_BATCH_SIZE",
    "DEFAULT_MIN_FREE_BYTES",
    "FD1RecoveryIdentityError",
    "IDENTITY_SPOOL_COLUMNS",
    "IDENTITY_SPOOL_SCHEMA_CHECKSUM",
    "IDENTITY_SPOOL_SCHEMA_VERSION",
    "IDENTITY_TARGET_UUID_NAMESPACE",
    "IdentitySourceRelease",
    "IdentitySpoolBinding",
    "IdentitySpoolChunk",
    "IdentitySpoolProgress",
    "build_identity_spool_from_frozen_sources",
    "iter_fd1_identity_mapping_batches",
    "iter_identity_spool_records",
    "load_identity_spool_binding",
    "read_identity_spool_chunk",
    "write_identity_spool",
]
