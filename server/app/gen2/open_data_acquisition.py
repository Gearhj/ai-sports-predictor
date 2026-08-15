"""Fail-closed, provider-independent acquisition for explicitly approved open data.

The module intentionally provides no HTTP client. Callers must inject a transport,
which keeps normal tests offline and prevents this infrastructure from silently
becoming another MLB/Savant adapter. Every network-eligible source needs an exact
policy, authorization identity, official-host allowlist, and path allowlist.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import tarfile
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from uuid import uuid4


DOWNLOAD_MANIFEST_SCHEMA_VERSION = "gen2_open_data_download_manifest_v1"
CHECKPOINT_SCHEMA_VERSION = "gen2_open_data_download_checkpoint_v1"
EXTRACTION_MANIFEST_SCHEMA_VERSION = "gen2_open_data_extraction_manifest_v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{2,79}$")
_OBJECT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "signature",
    "signed_url",
    "token",
)

PROHIBITED_HOST_SUFFIXES = frozenset(
    {
        "mlb.com",
        "baseballsavant.com",
    }
)
PROHIBITED_SOURCE_MARKERS = frozenset(
    {
        "MLB",
        "BASEBALL_SAVANT",
        "SAVANT",
        "STATCAST",
        "PYBASEBALL",
        "BASEBALLR",
        "SCRAPER",
    }
)
PROHIBITED_PATH_MARKERS = frozenset(
    {"baseball-savant", "baseballr", "pybaseball", "statcast"}
)


class OpenDataAcquisitionError(RuntimeError):
    """Base error for policy, transport, storage, or archive failures."""


class OpenDataPolicyError(OpenDataAcquisitionError):
    """A source or URL is not authorized by the exact open-data policy."""


class OpenDataIntegrityError(OpenDataAcquisitionError):
    """Downloaded or archived bytes do not match their declared identity."""


class OpenDataStorageLimitError(OpenDataAcquisitionError):
    """A byte cap or free-space reserve would be violated."""


class OpenDataInterruptedError(OpenDataAcquisitionError):
    """A transport stopped before completion; verified staging is resumable."""


class UnsafeArchiveError(OpenDataAcquisitionError):
    """An archive contains an unsafe path, type, duplicate, or excessive size."""


class SourcePolicyState(StrEnum):
    AUTHORIZED_OPEN_DATA = "AUTHORIZED_OPEN_DATA"
    BLOCKED_SOURCE_AUTHORIZATION = "BLOCKED_SOURCE_AUTHORIZATION"
    PROHIBITED = "PROHIBITED"


class ArchiveFormat(StrEnum):
    NONE = "NONE"
    ZIP = "ZIP"
    TAR_GZ = "TAR_GZ"


class DownloadDisposition(StrEnum):
    CREATED_REVISION = "CREATED_REVISION"
    UNCHANGED_BYTES = "UNCHANGED_BYTES"


@dataclass(frozen=True, slots=True)
class OpenDataSourcePolicy:
    source_key: str
    state: SourcePolicyState
    official_hosts: tuple[str, ...]
    allowed_path_prefixes: tuple[str, ...]
    allowed_mime_types: tuple[str, ...]
    allowed_archive_formats: tuple[ArchiveFormat, ...]
    maximum_file_bytes: int
    policy_reference: str
    decision_basis: str
    allowed_query_keys: tuple[str, ...] = ()
    authorization_identity_checksum: str | None = None
    allow_same_policy_redirects: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.state, SourcePolicyState):
            raise ValueError("state must be a SourcePolicyState")
        if not _SOURCE_KEY.fullmatch(self.source_key):
            raise ValueError("source_key must be an uppercase governed identifier")
        if (
            not isinstance(self.maximum_file_bytes, int)
            or isinstance(self.maximum_file_bytes, bool)
            or self.maximum_file_bytes <= 0
        ):
            raise ValueError("maximum_file_bytes must be positive")
        if not self.policy_reference.strip() or not self.decision_basis.strip():
            raise ValueError("policy_reference and decision_basis are required")
        _reject_secret_text(self.policy_reference, "policy_reference")
        _reject_secret_text(self.decision_basis, "decision_basis")
        if not self.allowed_archive_formats or any(
            not isinstance(item, ArchiveFormat) for item in self.allowed_archive_formats
        ):
            raise ValueError("At least one archive format policy is required")
        if len(set(self.allowed_archive_formats)) != len(self.allowed_archive_formats):
            raise ValueError("Archive formats must be unique")

        normalized_hosts = tuple(_normalize_policy_host(item) for item in self.official_hosts)
        if normalized_hosts != self.official_hosts or len(set(normalized_hosts)) != len(
            normalized_hosts
        ):
            raise ValueError("official_hosts must be normalized, lowercase, and unique")
        for prefix in self.allowed_path_prefixes:
            _validate_path_prefix(prefix)
        normalized_mimes = tuple(_normalize_mime(item) for item in self.allowed_mime_types)
        if normalized_mimes != self.allowed_mime_types or len(set(normalized_mimes)) != len(
            normalized_mimes
        ):
            raise ValueError("allowed_mime_types must be normalized and unique")
        if tuple(sorted(self.allowed_query_keys)) != self.allowed_query_keys or any(
            not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item)
            for item in self.allowed_query_keys
        ):
            raise ValueError("allowed_query_keys must be normalized, sorted, and unique")
        if len(set(self.allowed_query_keys)) != len(self.allowed_query_keys):
            raise ValueError("allowed_query_keys must be unique")
        if any(_contains_secret_marker(item) for item in self.allowed_query_keys):
            raise ValueError("Secret-like query keys cannot be allowlisted")

        if self.state is SourcePolicyState.AUTHORIZED_OPEN_DATA:
            if _source_is_prohibited(self.source_key):
                raise OpenDataPolicyError("MLB/Savant and rejected scraper sources are prohibited")
            if not self.official_hosts or not self.allowed_path_prefixes:
                raise ValueError("Authorized sources require host and path allowlists")
            if not self.allowed_mime_types:
                raise ValueError("Authorized sources require an exact MIME allowlist")
            if not self.authorization_identity_checksum or not _SHA256.fullmatch(
                self.authorization_identity_checksum
            ):
                raise ValueError("Authorized sources require a SHA-256 authorization identity")
            if any(_host_is_prohibited(host) for host in self.official_hosts):
                raise OpenDataPolicyError("MLB/Savant hosts cannot be authorized")
        elif self.authorization_identity_checksum is not None:
            if not _SHA256.fullmatch(self.authorization_identity_checksum):
                raise ValueError("authorization_identity_checksum must be SHA-256")

    @property
    def identity_checksum(self) -> str:
        return _canonical_checksum(
            {
                "source_key": self.source_key,
                "state": self.state.value,
                "official_hosts": self.official_hosts,
                "allowed_path_prefixes": self.allowed_path_prefixes,
                "allowed_mime_types": self.allowed_mime_types,
                "allowed_archive_formats": tuple(
                    item.value for item in self.allowed_archive_formats
                ),
                "maximum_file_bytes": self.maximum_file_bytes,
                "policy_reference": self.policy_reference,
                "decision_basis": self.decision_basis,
                "allowed_query_keys": self.allowed_query_keys,
                "authorization_identity_checksum": self.authorization_identity_checksum,
                "allow_same_policy_redirects": self.allow_same_policy_redirects,
            }
        )


@dataclass(frozen=True, slots=True)
class DownloadRequest:
    source_key: str
    object_key: str
    url: str
    archive_format: ArchiveFormat = ArchiveFormat.NONE
    expected_sha256: str | None = None
    expected_content_length: int | None = None
    expected_mime_type: str | None = None

    def __post_init__(self) -> None:
        if not _SOURCE_KEY.fullmatch(self.source_key):
            raise ValueError("source_key is invalid")
        if not _OBJECT_KEY.fullmatch(self.object_key) or self.object_key in {".", ".."}:
            raise ValueError("object_key is not a safe storage segment")
        if not isinstance(self.archive_format, ArchiveFormat):
            raise ValueError("archive_format must be an ArchiveFormat")
        if self.expected_sha256 is not None and not _SHA256.fullmatch(self.expected_sha256):
            raise ValueError("expected_sha256 must be lowercase SHA-256")
        if self.expected_content_length is not None and self.expected_content_length < 0:
            raise ValueError("expected_content_length cannot be negative")
        if self.expected_mime_type is not None:
            normalized = _normalize_mime(self.expected_mime_type)
            if normalized != self.expected_mime_type:
                raise ValueError("expected_mime_type must be normalized")


@dataclass(frozen=True, slots=True)
class TransportRequest:
    url: str
    range_start: int

    @property
    def headers(self) -> Mapping[str, str]:
        if self.range_start <= 0:
            return MappingProxyType({})
        return MappingProxyType({"Range": f"bytes={self.range_start}-"})


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status_code: int
    headers: Mapping[str, str]
    body: Iterable[bytes]
    final_url: str | None = None


class DownloadTransport(Protocol):
    def fetch(self, request: TransportRequest) -> TransportResponse: ...


@dataclass(frozen=True, slots=True)
class AttemptAccounting:
    attempt_number: int
    started_at: str
    completed_at: str
    requested_range_start: int
    status_code: int | None
    response_content_length: int | None
    received_bytes: int
    outcome: str

    def __post_init__(self) -> None:
        if self.attempt_number < 1 or self.requested_range_start < 0:
            raise ValueError("Attempt number and range start are invalid")
        if self.status_code is not None and not 100 <= self.status_code <= 599:
            raise ValueError("Attempt HTTP status is invalid")
        if self.response_content_length is not None and self.response_content_length < 0:
            raise ValueError("Attempt Content-Length is invalid")
        if self.received_bytes < 0 or self.outcome not in {"INTERRUPTED", "SUCCEEDED"}:
            raise ValueError("Attempt byte accounting or outcome is invalid")


@dataclass(frozen=True, slots=True)
class DownloadArtifact:
    schema_version: str
    source_key: str
    object_key: str
    sanitized_url: str
    policy_identity_checksum: str
    authorization_identity_checksum: str
    disposition: DownloadDisposition
    revision: int
    content_logical_uri: str
    manifest_logical_uri: str
    sha256: str
    content_length: int
    mime_type: str
    archive_format: ArchiveFormat
    http_status: int
    started_at: str
    completed_at: str
    physical_attempt_count: int
    transport_bytes_received: int
    attempts: tuple[AttemptAccounting, ...]

    def __post_init__(self) -> None:
        if self.schema_version != DOWNLOAD_MANIFEST_SCHEMA_VERSION:
            raise ValueError("Download artifact schema version is invalid")
        if not _SHA256.fullmatch(self.policy_identity_checksum):
            raise ValueError("Download policy identity is invalid")
        if not _SHA256.fullmatch(self.authorization_identity_checksum):
            raise ValueError("Download authorization identity is invalid")
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("Download content SHA-256 is invalid")
        if self.revision < 1 or self.content_length < 0 or not 100 <= self.http_status <= 599:
            raise ValueError("Download revision, length, or HTTP status is invalid")
        if self.physical_attempt_count != len(self.attempts) or not self.attempts:
            raise ValueError("Download attempt count is inconsistent")
        if self.transport_bytes_received != sum(item.received_bytes for item in self.attempts):
            raise ValueError("Download transport byte accounting is inconsistent")

    @property
    def reused(self) -> bool:
        return self.disposition is DownloadDisposition.UNCHANGED_BYTES

    def manifest(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["disposition"] = self.disposition.value
        payload["archive_format"] = self.archive_format.value
        payload["attempts"] = [asdict(item) for item in self.attempts]
        return payload


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    maximum_members: int = 25_000
    maximum_member_bytes: int = 2 * 1024**3
    maximum_expanded_bytes: int = 20 * 1024**3
    maximum_expansion_ratio: float = 200.0

    def __post_init__(self) -> None:
        if (
            self.maximum_members <= 0
            or self.maximum_member_bytes <= 0
            or self.maximum_expanded_bytes <= 0
            or self.maximum_expansion_ratio <= 0
        ):
            raise ValueError("Extraction limits must be positive")


@dataclass(frozen=True, slots=True)
class ExtractedMember:
    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExtractionArtifact:
    schema_version: str
    source_key: str
    object_key: str
    source_revision: int
    archive_sha256: str
    archive_format: ArchiveFormat
    extraction_logical_uri: str
    manifest_logical_uri: str
    extracted_at: str
    member_count: int
    expanded_bytes: int
    members: tuple[ExtractedMember, ...]
    identity_checksum: str
    reused: bool = False


class OpenDataAcquirer:
    """Atomic downloader and archive extractor with no built-in network client."""

    def __init__(
        self,
        root: Path,
        *,
        transport: DownloadTransport,
        policies: Mapping[str, OpenDataSourcePolicy] | None = None,
        maximum_run_bytes: int = 32 * 1024**3,
        minimum_free_bytes_after_write: int = 100 * 1024**3,
        free_space: Callable[[Path], int] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not isinstance(maximum_run_bytes, int)
            or isinstance(maximum_run_bytes, bool)
            or maximum_run_bytes <= 0
        ):
            raise ValueError("maximum_run_bytes must be positive")
        if (
            not isinstance(minimum_free_bytes_after_write, int)
            or isinstance(minimum_free_bytes_after_write, bool)
            or minimum_free_bytes_after_write < 0
        ):
            raise ValueError("minimum_free_bytes_after_write cannot be negative")
        lexical_root = Path(root).absolute()
        _assert_no_link_ancestors(lexical_root)
        self.root = lexical_root.resolve()
        self.transport = transport
        self.policies = dict(policies or DEFAULT_SOURCE_POLICIES)
        for key, policy in self.policies.items():
            if key != policy.source_key:
                raise ValueError("Policy mapping key must equal policy.source_key")
        self.maximum_run_bytes = maximum_run_bytes
        self.minimum_free_bytes_after_write = minimum_free_bytes_after_write
        self._free_space = free_space or (lambda path: shutil.disk_usage(path).free)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._run_received_bytes = 0

    @property
    def run_received_bytes(self) -> int:
        return self._run_received_bytes

    def download(self, request: DownloadRequest) -> DownloadArtifact:
        policy = self._authorized_policy(request)
        transport_url = _validate_url(request.url, policy)
        sanitized_url = _redacted_manifest_url(transport_url)
        url_identity_checksum = hashlib.sha256(transport_url.encode("utf-8")).hexdigest()
        if request.archive_format not in policy.allowed_archive_formats:
            raise OpenDataPolicyError("Requested archive format is not allowed for this source")

        object_dir = self._object_directory(request)
        staging_dir = self._contained(object_dir / "staging")
        revision_dir = self._contained(object_dir / "revisions")
        manifest_dir = self._contained(object_dir / "manifests")
        self.root.mkdir(parents=True, exist_ok=True)
        _assert_no_links(self.root)
        for directory in (object_dir, staging_dir, revision_dir, manifest_dir):
            directory.mkdir(parents=True, exist_ok=True)
            _assert_no_links_between(self.root, directory)

        staging_identity = _canonical_checksum(
            {
                "source_key": request.source_key,
                "object_key": request.object_key,
                "url_identity_checksum": url_identity_checksum,
                "policy_identity_checksum": policy.identity_checksum,
            }
        )
        partial = self._contained(staging_dir / f"{staging_identity}.part")
        checkpoint_path = self._contained(staging_dir / f"{staging_identity}.checkpoint.json")
        attempts, offset = self._load_checkpoint(
            checkpoint_path,
            partial,
            request=request,
            sanitized_url=sanitized_url,
            url_identity_checksum=url_identity_checksum,
            policy=policy,
        )
        started = _iso_utc(self._clock())
        attempt_number = len(attempts) + 1
        transport_request = TransportRequest(url=transport_url, range_start=offset)
        try:
            response = self.transport.fetch(transport_request)
        except Exception as exc:
            if partial.exists() and (partial.is_symlink() or _is_reparse(partial)):
                raise OpenDataIntegrityError("Staging path cannot be a link") from exc
            partial.touch(exist_ok=True)
            attempt = AttemptAccounting(
                attempt_number=attempt_number,
                started_at=started,
                completed_at=_iso_utc(self._clock()),
                requested_range_start=offset,
                status_code=None,
                response_content_length=None,
                received_bytes=0,
                outcome="INTERRUPTED",
            )
            self._write_checkpoint(
                checkpoint_path,
                partial,
                request,
                sanitized_url,
                url_identity_checksum,
                policy,
                (*attempts, attempt),
            )
            raise OpenDataInterruptedError("Transport interrupted before response") from exc

        headers = _normalized_headers(response.headers)
        final_url = _validate_url(response.final_url or transport_url, policy)
        if final_url != transport_url and not policy.allow_same_policy_redirects:
            self._clear_staging(partial, checkpoint_path)
            raise OpenDataPolicyError("Redirects are not authorized by this source policy")
        try:
            response_length, total_length = _validate_response(
                response,
                headers,
                offset=offset,
                policy=policy,
                request=request,
            )
            self._guard_capacity(policy, total_length, response_length)
        except OpenDataAcquisitionError:
            self._clear_staging(partial, checkpoint_path)
            raise

        if partial.exists() and partial.is_symlink():
            raise OpenDataIntegrityError("Staging path cannot be a link")
        hasher = hashlib.sha256()
        if offset:
            _hash_file_into(partial, hasher)
        received = 0
        # An interruption before the response creates a verified zero-byte
        # checkpoint. Reopening that staging file must resume at byte zero
        # without using exclusive-create again.
        mode = "ab" if partial.exists() else "xb"
        try:
            with partial.open(mode) as stream:
                for chunk in response.body:
                    if not isinstance(chunk, bytes):
                        raise OpenDataIntegrityError("Transport chunks must be bytes")
                    if not chunk:
                        continue
                    if (
                        response_length is not None
                        and received + len(chunk) > response_length
                    ):
                        raise OpenDataIntegrityError("Response exceeded declared Content-Length")
                    if offset + received + len(chunk) > policy.maximum_file_bytes:
                        raise OpenDataStorageLimitError("Download exceeded the source file cap")
                    if self._run_received_bytes + len(chunk) > self.maximum_run_bytes:
                        raise OpenDataStorageLimitError("Download exceeded the run byte cap")
                    if (
                        self._free_space(self.root) - len(chunk)
                        < self.minimum_free_bytes_after_write
                    ):
                        raise OpenDataStorageLimitError(
                            "Download would violate the free-space reserve"
                        )
                    stream.write(chunk)
                    hasher.update(chunk)
                    received += len(chunk)
                    self._run_received_bytes += len(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        except OpenDataAcquisitionError:
            self._clear_staging(partial, checkpoint_path)
            raise
        except Exception as exc:
            _sync_file(partial)
            attempt = AttemptAccounting(
                attempt_number=attempt_number,
                started_at=started,
                completed_at=_iso_utc(self._clock()),
                requested_range_start=offset,
                status_code=response.status_code,
                response_content_length=response_length,
                received_bytes=received,
                outcome="INTERRUPTED",
            )
            self._write_checkpoint(
                checkpoint_path,
                partial,
                request,
                sanitized_url,
                url_identity_checksum,
                policy,
                (*attempts, attempt),
            )
            raise OpenDataInterruptedError(
                "Transport body interrupted; staging is resumable"
            ) from exc

        completed = _iso_utc(self._clock())
        actual_total_length = offset + received
        if response_length is not None and received != response_length:
            self._clear_staging(partial, checkpoint_path)
            raise OpenDataIntegrityError("Received bytes do not match declared Content-Length")
        if total_length is not None and actual_total_length != total_length:
            self._clear_staging(partial, checkpoint_path)
            raise OpenDataIntegrityError("Received bytes do not match declared total length")
        if partial.stat().st_size != actual_total_length or actual_total_length <= 0:
            self._clear_staging(partial, checkpoint_path)
            raise OpenDataIntegrityError("Downloaded object is empty or has inconsistent bytes")
        if (
            request.expected_content_length is not None
            and request.expected_content_length != actual_total_length
        ):
            self._clear_staging(partial, checkpoint_path)
            raise OpenDataIntegrityError("Downloaded bytes do not match the expected length")
        total_length = actual_total_length
        sha256 = hasher.hexdigest()
        declared_sha = _declared_sha256(headers)
        expected_hashes = tuple(
            value for value in (request.expected_sha256, declared_sha) if value is not None
        )
        if any(value != sha256 for value in expected_hashes):
            self._clear_staging(partial, checkpoint_path)
            raise OpenDataIntegrityError("Downloaded SHA-256 does not match declared identity")

        attempt = AttemptAccounting(
            attempt_number=attempt_number,
            started_at=started,
            completed_at=completed,
            requested_range_start=offset,
            status_code=response.status_code,
            response_content_length=response_length,
            received_bytes=received,
            outcome="SUCCEEDED",
        )
        all_attempts = (*attempts, attempt)
        latest_path = self._contained(object_dir / "latest.json")
        latest = _read_json(latest_path) if latest_path.exists() else None
        previous_revision = _latest_revision(latest, request)
        if latest is not None and latest.get("sha256") == sha256:
            revision = previous_revision
            content_uri = _required_text(latest, "content_logical_uri")
            content_path = self.resolve(content_uri)
            _validate_file_identity(content_path, sha256, total_length)
            disposition = DownloadDisposition.UNCHANGED_BYTES
            partial.unlink()
        else:
            revision = previous_revision + 1
            target = self._contained(
                revision_dir / f"r{revision:06d}-{sha256}.blob"
            )
            if target.exists() or target.is_symlink():
                self._clear_staging(partial, checkpoint_path)
                raise OpenDataIntegrityError("Revision target already exists unexpectedly")
            os.replace(partial, target)
            _validate_file_identity(target, sha256, total_length)
            content_uri = target.relative_to(self.root).as_posix()
            disposition = DownloadDisposition.CREATED_REVISION
        checkpoint_path.unlink(missing_ok=True)

        observation_name = (
            f"acquisition-{completed.replace(':', '').replace('+', '_')}-{uuid4().hex}.json"
        )
        observation_path = self._contained(manifest_dir / observation_name)
        artifact = DownloadArtifact(
            schema_version=DOWNLOAD_MANIFEST_SCHEMA_VERSION,
            source_key=request.source_key,
            object_key=request.object_key,
            sanitized_url=sanitized_url,
            policy_identity_checksum=policy.identity_checksum,
            authorization_identity_checksum=policy.authorization_identity_checksum or "",
            disposition=disposition,
            revision=revision,
            content_logical_uri=content_uri,
            manifest_logical_uri=observation_path.relative_to(self.root).as_posix(),
            sha256=sha256,
            content_length=total_length,
            mime_type=_normalize_mime(headers["content-type"]),
            archive_format=request.archive_format,
            http_status=response.status_code,
            started_at=all_attempts[0].started_at,
            completed_at=completed,
            physical_attempt_count=len(all_attempts),
            transport_bytes_received=sum(item.received_bytes for item in all_attempts),
            attempts=all_attempts,
        )
        manifest = artifact.manifest()
        _assert_secret_free(manifest)
        _write_json_atomic(observation_path, manifest)
        if disposition is DownloadDisposition.CREATED_REVISION:
            revision_manifest = self._contained(
                manifest_dir / f"revision-r{revision:06d}-{sha256}.json"
            )
            _write_json_atomic(revision_manifest, manifest)
        _write_json_atomic(
            latest_path,
            {
                "schema_version": DOWNLOAD_MANIFEST_SCHEMA_VERSION,
                "source_key": request.source_key,
                "object_key": request.object_key,
                "revision": revision,
                "sha256": sha256,
                "content_length": total_length,
                "content_logical_uri": content_uri,
                "manifest_logical_uri": artifact.manifest_logical_uri,
                "completed_at": completed,
            },
        )
        return artifact

    def extract_archive(
        self,
        artifact: DownloadArtifact,
        *,
        limits: ExtractionLimits | None = None,
    ) -> ExtractionArtifact:
        limits = limits or ExtractionLimits()
        if artifact.archive_format is ArchiveFormat.NONE:
            raise OpenDataPolicyError("A non-archive artifact cannot be extracted")
        policy = self.policies.get(artifact.source_key)
        if (
            policy is None
            or policy.state is not SourcePolicyState.AUTHORIZED_OPEN_DATA
            or policy.identity_checksum != artifact.policy_identity_checksum
            or policy.authorization_identity_checksum
            != artifact.authorization_identity_checksum
            or artifact.archive_format not in policy.allowed_archive_formats
        ):
            raise OpenDataPolicyError("Archive format is not allowed by the source policy")
        archive = self.resolve(artifact.content_logical_uri)
        _validate_file_identity(archive, artifact.sha256, artifact.content_length)
        if artifact.content_length <= 0:
            raise UnsafeArchiveError("Empty archives cannot be extracted")

        object_dir = self._object_directory(
            DownloadRequest(
                source_key=artifact.source_key,
                object_key=artifact.object_key,
                url=artifact.sanitized_url,
                archive_format=artifact.archive_format,
            )
        )
        extraction_root = self._contained(object_dir / "extracted")
        extraction_root.mkdir(parents=True, exist_ok=True)
        destination = self._contained(
            extraction_root / f"r{artifact.revision:06d}-{artifact.sha256}"
        )
        manifest_name = "extraction-manifest.json"
        if destination.exists():
            existing = _read_json(destination / manifest_name)
            return _validate_existing_extraction(
                destination,
                existing,
                artifact,
                manifest_name=manifest_name,
                root=self.root,
            )

        temporary = self._contained(
            extraction_root / f".extract-{artifact.sha256}-{uuid4().hex}.tmp"
        )
        temporary.mkdir(parents=False, exist_ok=False)
        try:
            if artifact.archive_format is ArchiveFormat.ZIP:
                members = _extract_zip(archive, temporary, limits)
            elif artifact.archive_format is ArchiveFormat.TAR_GZ:
                members = _extract_tar(archive, temporary, limits)
            else:  # pragma: no cover - exhaustive enum guard
                raise OpenDataPolicyError("Unsupported archive format")
            expanded = sum(item.size_bytes for item in members)
            extracted_at = _iso_utc(self._clock())
            semantic = {
                "schema_version": EXTRACTION_MANIFEST_SCHEMA_VERSION,
                "source_key": artifact.source_key,
                "object_key": artifact.object_key,
                "source_revision": artifact.revision,
                "archive_sha256": artifact.sha256,
                "archive_format": artifact.archive_format.value,
                "extraction_logical_uri": destination.relative_to(self.root).as_posix(),
                "manifest_logical_uri": (
                    destination / manifest_name
                ).relative_to(self.root).as_posix(),
                "extracted_at": extracted_at,
                "member_count": len(members),
                "expanded_bytes": expanded,
                "members": [asdict(item) for item in members],
            }
            identity = _canonical_checksum(semantic)
            payload = {**semantic, "identity_checksum": identity, "reused": False}
            _assert_secret_free(payload)
            _write_json_atomic(temporary / manifest_name, payload)
            os.replace(temporary, destination)
            return _extraction_from_json(payload, reused=False)
        except Exception:
            if temporary.exists() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            raise

    def resolve(self, logical_uri: str) -> Path:
        pure = PurePosixPath(logical_uri)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise OpenDataIntegrityError("Logical URI is not root-confined")
        resolved = self._contained(self.root.joinpath(*pure.parts))
        _assert_no_links_between(self.root, resolved.parent)
        if resolved.is_symlink() or _is_reparse(resolved):
            raise OpenDataIntegrityError("Logical URI cannot resolve through a link")
        return resolved

    def _authorized_policy(self, request: DownloadRequest) -> OpenDataSourcePolicy:
        policy = self.policies.get(request.source_key)
        if policy is None:
            raise OpenDataPolicyError("No explicit source policy exists")
        if _source_is_prohibited(request.source_key):
            raise OpenDataPolicyError("MLB/Savant and rejected scraper sources are prohibited")
        if policy.state is not SourcePolicyState.AUTHORIZED_OPEN_DATA:
            raise OpenDataPolicyError(
                f"Source is not authorized: {policy.state.value}"
            )
        return policy

    def _object_directory(self, request: DownloadRequest) -> Path:
        source = request.source_key.lower().replace("_", "-")
        return self._contained(self.root / "open-data" / source / request.object_key)

    def _contained(self, path: Path) -> Path:
        candidate = Path(path).absolute()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise OpenDataIntegrityError("Storage path escaped the configured root") from exc
        return candidate

    def _guard_capacity(
        self,
        policy: OpenDataSourcePolicy,
        total_length: int | None,
        response_length: int | None,
    ) -> None:
        if total_length is not None and total_length > policy.maximum_file_bytes:
            raise OpenDataStorageLimitError("Declared file exceeds the source file cap")
        if (
            response_length is not None
            and self._run_received_bytes + response_length > self.maximum_run_bytes
        ):
            raise OpenDataStorageLimitError("Declared response exceeds the run byte cap")
        if (
            response_length is not None
            and self._free_space(self.root) - response_length
                < self.minimum_free_bytes_after_write
        ):
            raise OpenDataStorageLimitError(
                "Declared response would violate the free-space reserve"
            )

    def _load_checkpoint(
        self,
        checkpoint_path: Path,
        partial: Path,
        *,
        request: DownloadRequest,
        sanitized_url: str,
        url_identity_checksum: str,
        policy: OpenDataSourcePolicy,
    ) -> tuple[tuple[AttemptAccounting, ...], int]:
        if not checkpoint_path.exists() and not partial.exists():
            return (), 0
        if not checkpoint_path.exists() or not partial.exists():
            raise OpenDataIntegrityError("Staging and checkpoint must exist together")
        if checkpoint_path.is_symlink() or partial.is_symlink():
            raise OpenDataIntegrityError("Staging state cannot use links")
        payload = _read_json(checkpoint_path)
        expected = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "source_key": request.source_key,
            "object_key": request.object_key,
            "sanitized_url": sanitized_url,
            "url_identity_checksum": url_identity_checksum,
            "policy_identity_checksum": policy.identity_checksum,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise OpenDataIntegrityError("Checkpoint identity does not match this request")
        partial_size = partial.stat().st_size
        if payload.get("partial_bytes") != partial_size:
            raise OpenDataIntegrityError("Checkpoint byte count does not match staging")
        if payload.get("partial_sha256") != _file_sha256(partial):
            raise OpenDataIntegrityError("Checkpoint SHA-256 does not match staging")
        attempts_raw = payload.get("attempts")
        if not isinstance(attempts_raw, list):
            raise OpenDataIntegrityError("Checkpoint attempts are malformed")
        attempts = tuple(_attempt_from_json(item) for item in attempts_raw)
        if any(item.outcome != "INTERRUPTED" for item in attempts):
            raise OpenDataIntegrityError("Checkpoint contains a non-interrupted attempt")
        return attempts, partial_size

    def _write_checkpoint(
        self,
        checkpoint_path: Path,
        partial: Path,
        request: DownloadRequest,
        sanitized_url: str,
        url_identity_checksum: str,
        policy: OpenDataSourcePolicy,
        attempts: Sequence[AttemptAccounting],
    ) -> None:
        payload = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "source_key": request.source_key,
            "object_key": request.object_key,
            "sanitized_url": sanitized_url,
            "url_identity_checksum": url_identity_checksum,
            "policy_identity_checksum": policy.identity_checksum,
            "partial_bytes": partial.stat().st_size,
            "partial_sha256": _file_sha256(partial),
            "attempts": [asdict(item) for item in attempts],
        }
        _assert_secret_free(payload)
        _write_json_atomic(checkpoint_path, payload)

    @staticmethod
    def _clear_staging(partial: Path, checkpoint_path: Path) -> None:
        if partial.exists() and not partial.is_symlink():
            partial.unlink()
        if checkpoint_path.exists() and not checkpoint_path.is_symlink():
            checkpoint_path.unlink()


def _validate_response(
    response: TransportResponse,
    headers: Mapping[str, str],
    *,
    offset: int,
    policy: OpenDataSourcePolicy,
    request: DownloadRequest,
) -> tuple[int | None, int | None]:
    expected_status = 206 if offset else 200
    if response.status_code != expected_status:
        raise OpenDataIntegrityError(
            f"Expected HTTP {expected_status}, received {response.status_code}"
        )
    raw_length = headers.get("content-length")
    if raw_length is not None and not raw_length.isdigit():
        raise OpenDataIntegrityError("Content-Length header is malformed")
    response_length = int(raw_length) if raw_length is not None else None
    mime = _normalize_mime(headers.get("content-type", ""))
    if mime not in policy.allowed_mime_types:
        raise OpenDataIntegrityError("Response MIME type is not allowed by source policy")
    if request.expected_mime_type is not None and mime != request.expected_mime_type:
        raise OpenDataIntegrityError("Response MIME type does not match the request")
    if offset:
        match = _CONTENT_RANGE.fullmatch(headers.get("content-range", ""))
        if match is None:
            raise OpenDataIntegrityError("Resumed response requires an exact Content-Range")
        start, end, total = (int(item) for item in match.groups())
        ranged_length = end - start + 1
        if (
            start != offset
            or end < start
            or (response_length is not None and ranged_length != response_length)
        ):
            raise OpenDataIntegrityError("Content-Range does not match the resume request")
        if end + 1 != total:
            raise OpenDataIntegrityError("Resume response must complete the requested object")
        total_length = total
        response_length = ranged_length
    else:
        total_length = response_length
    if (
        request.expected_content_length is not None
        and total_length is not None
        and request.expected_content_length != total_length
    ):
        raise OpenDataIntegrityError("Content-Length does not match the request")
    return response_length, total_length


def _validate_url(url: str, policy: OpenDataSourcePolicy) -> str:
    if not isinstance(url, str) or not url:
        raise OpenDataPolicyError("A source URL is required")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise OpenDataPolicyError("Only HTTPS open-data URLs are allowed")
    if parsed.username or parsed.password or parsed.fragment:
        raise OpenDataPolicyError("Open-data URLs cannot contain credentials or fragments")
    try:
        port = parsed.port
    except ValueError as exc:
        raise OpenDataPolicyError("Source URL port is invalid") from exc
    if port not in (None, 443):
        raise OpenDataPolicyError("Only the default HTTPS port is allowed")
    host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    if not host or _host_is_prohibited(host):
        raise OpenDataPolicyError("MLB/Savant or missing hosts are prohibited")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise OpenDataPolicyError("IP-literal source URLs are prohibited")
    if host not in policy.official_hosts:
        raise OpenDataPolicyError("URL host is not on the source's official-host allowlist")
    decoded_path = unquote(parsed.path)
    if "\\" in decoded_path or any(part == ".." for part in PurePosixPath(decoded_path).parts):
        raise OpenDataPolicyError("URL path traversal is prohibited")
    if any(marker in decoded_path.casefold() for marker in PROHIBITED_PATH_MARKERS):
        raise OpenDataPolicyError("Rejected scraper and Savant/Statcast paths are prohibited")
    if not any(
        _path_matches_prefix(decoded_path, prefix)
        for prefix in policy.allowed_path_prefixes
    ):
        raise OpenDataPolicyError("URL path is outside the source policy allowlist")
    canonical_query = _canonical_safe_query(parsed.query, policy.allowed_query_keys)
    return urlunsplit(("https", host, parsed.path, canonical_query, ""))


def _canonical_safe_query(query: str, allowed_keys: tuple[str, ...]) -> str:
    if not query:
        return ""
    if not allowed_keys:
        raise OpenDataPolicyError("This source policy does not allow URL query parameters")
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise OpenDataPolicyError("URL query is malformed") from exc
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise OpenDataPolicyError("Duplicate URL query keys are prohibited")
    normalized: list[tuple[str, str]] = []
    for key, value in pairs:
        if not key or not value.strip():
            raise OpenDataPolicyError("Blank URL query keys or values are prohibited")
        lowered = key.lower()
        if key != lowered or lowered not in allowed_keys:
            raise OpenDataPolicyError("URL query key is not explicitly allowlisted")
        if _contains_secret_marker(lowered) or _contains_secret_marker(value.lower()):
            raise OpenDataPolicyError("Secret-like URL query material is prohibited")
        if any(ord(character) < 0x20 for character in value) or len(value) > 512:
            raise OpenDataPolicyError("URL query value is malformed")
        normalized.append((lowered, value))
    return urlencode(sorted(normalized), doseq=False)


def _redacted_manifest_url(canonical_url: str) -> str:
    parsed = urlsplit(canonical_url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _path_matches_prefix(path: str, prefix: str) -> bool:
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or path.startswith(f"{prefix}/")


def _extract_zip(
    archive: Path,
    destination: Path,
    limits: ExtractionLimits,
) -> tuple[ExtractedMember, ...]:
    try:
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            _guard_member_population(
                infos,
                archive.stat().st_size,
                limits,
                lambda item: item.file_size,
            )
            seen: set[str] = set()
            extracted: list[ExtractedMember] = []
            for info in infos:
                relative = _safe_member_path(info.filename, seen)
                mode = info.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise UnsafeArchiveError("Archive links and special files are prohibited")
                if info.flag_bits & 0x1:
                    raise UnsafeArchiveError("Encrypted archive members are prohibited")
                target = destination.joinpath(*relative.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source:
                    extracted.append(
                        _copy_archive_member(
                            source,
                            target,
                            info.file_size,
                            limits,
                            relative.as_posix(),
                        )
                    )
            return tuple(extracted)
    except UnsafeArchiveError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise UnsafeArchiveError("ZIP archive is malformed or unreadable") from exc


def _extract_tar(
    archive: Path,
    destination: Path,
    limits: ExtractionLimits,
) -> tuple[ExtractedMember, ...]:
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            infos = bundle.getmembers()
            _guard_member_population(
                infos,
                archive.stat().st_size,
                limits,
                lambda item: item.size,
            )
            seen: set[str] = set()
            extracted: list[ExtractedMember] = []
            for info in infos:
                relative = _safe_member_path(info.name, seen)
                target = destination.joinpath(*relative.parts)
                if info.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not info.isfile() or info.issparse():
                    raise UnsafeArchiveError(
                        "Only regular archive files and directories are allowed"
                    )
                source = bundle.extractfile(info)
                if source is None:
                    raise UnsafeArchiveError("Regular archive member has no readable body")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source:
                    extracted.append(
                        _copy_archive_member(
                            source,
                            target,
                            info.size,
                            limits,
                            relative.as_posix(),
                        )
                    )
            return tuple(extracted)
    except UnsafeArchiveError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise UnsafeArchiveError("TAR archive is malformed or unreadable") from exc


def _guard_member_population(
    infos: Sequence[Any],
    archive_bytes: int,
    limits: ExtractionLimits,
    size: Callable[[Any], int],
) -> None:
    if len(infos) > limits.maximum_members:
        raise UnsafeArchiveError("Archive member count exceeds the configured cap")
    sizes = [int(size(item)) for item in infos]
    if any(value < 0 or value > limits.maximum_member_bytes for value in sizes):
        raise UnsafeArchiveError("Archive member exceeds the configured size cap")
    total = sum(sizes)
    if total > limits.maximum_expanded_bytes:
        raise UnsafeArchiveError("Archive expanded bytes exceed the configured cap")
    if total / max(archive_bytes, 1) > limits.maximum_expansion_ratio:
        raise UnsafeArchiveError("Archive expansion ratio exceeds the configured cap")


def _safe_member_path(name: str, seen: set[str]) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise UnsafeArchiveError("Archive member path is invalid")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and ":" in path.parts[0])
    ):
        raise UnsafeArchiveError("Archive path traversal is prohibited")
    identity = "/".join(path.parts).casefold()
    if identity in seen:
        raise UnsafeArchiveError("Duplicate archive member paths are prohibited")
    seen.add(identity)
    return path


def _copy_archive_member(
    source: Any,
    target: Path,
    declared_size: int,
    limits: ExtractionLimits,
    relative_path: str,
) -> ExtractedMember:
    digest = hashlib.sha256()
    written = 0
    with target.open("xb") as output:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > declared_size or written > limits.maximum_member_bytes:
                raise UnsafeArchiveError("Archive member exceeded its declared size")
            output.write(chunk)
            digest.update(chunk)
        output.flush()
        os.fsync(output.fileno())
    if written != declared_size:
        raise UnsafeArchiveError("Archive member size does not match its declaration")
    return ExtractedMember(
        relative_path=relative_path,
        size_bytes=written,
        sha256=digest.hexdigest(),
    )


def _extraction_from_json(payload: Mapping[str, Any], *, reused: bool) -> ExtractionArtifact:
    members_raw = payload.get("members")
    if not isinstance(members_raw, list):
        raise OpenDataIntegrityError("Extraction manifest members are malformed")
    members = tuple(
        ExtractedMember(
            relative_path=_mapping_text(item, "relative_path"),
            size_bytes=_mapping_int(item, "size_bytes"),
            sha256=_mapping_text(item, "sha256"),
        )
        for item in members_raw
        if isinstance(item, Mapping)
    )
    if len(members) != len(members_raw):
        raise OpenDataIntegrityError("Extraction manifest member is malformed")
    return ExtractionArtifact(
        schema_version=_required_text(payload, "schema_version"),
        source_key=_required_text(payload, "source_key"),
        object_key=_required_text(payload, "object_key"),
        source_revision=_required_int(payload, "source_revision"),
        archive_sha256=_required_text(payload, "archive_sha256"),
        archive_format=ArchiveFormat(_required_text(payload, "archive_format")),
        extraction_logical_uri=_required_text(payload, "extraction_logical_uri"),
        manifest_logical_uri=_required_text(payload, "manifest_logical_uri"),
        extracted_at=_required_text(payload, "extracted_at"),
        member_count=_required_int(payload, "member_count"),
        expanded_bytes=_required_int(payload, "expanded_bytes"),
        members=members,
        identity_checksum=_required_text(payload, "identity_checksum"),
        reused=reused,
    )


def _validate_existing_extraction(
    destination: Path,
    payload: Mapping[str, Any],
    source: DownloadArtifact,
    *,
    manifest_name: str,
    root: Path,
) -> ExtractionArtifact:
    artifact = _extraction_from_json(payload, reused=True)
    if (
        artifact.schema_version != EXTRACTION_MANIFEST_SCHEMA_VERSION
        or artifact.source_key != source.source_key
        or artifact.object_key != source.object_key
        or artifact.source_revision != source.revision
        or artifact.archive_sha256 != source.sha256
        or artifact.archive_format is not source.archive_format
        or artifact.extraction_logical_uri != destination.relative_to(root).as_posix()
        or artifact.manifest_logical_uri
        != (destination / manifest_name).relative_to(root).as_posix()
        or artifact.member_count != len(artifact.members)
        or artifact.expanded_bytes != sum(item.size_bytes for item in artifact.members)
    ):
        raise OpenDataIntegrityError("Existing extraction has a conflicting identity")
    semantic = {
        key: value
        for key, value in payload.items()
        if key not in {"identity_checksum", "reused"}
    }
    if _canonical_checksum(semantic) != artifact.identity_checksum:
        raise OpenDataIntegrityError("Existing extraction manifest checksum is invalid")
    seen: set[str] = set()
    expected_files = {manifest_name.casefold()}
    for member in artifact.members:
        relative = _safe_member_path(member.relative_path, seen)
        target = destination.joinpath(*relative.parts)
        _validate_file_identity(target, member.sha256, member.size_bytes)
        expected_files.add(relative.as_posix().casefold())
    actual_files: set[str] = set()
    for path in destination.rglob("*"):
        if path.is_symlink() or _is_reparse(path):
            raise OpenDataIntegrityError("Existing extraction contains a link")
        if path.is_file():
            actual_files.add(path.relative_to(destination).as_posix().casefold())
    if actual_files != expected_files:
        raise OpenDataIntegrityError("Existing extraction file population is inconsistent")
    return artifact


def _attempt_from_json(payload: object) -> AttemptAccounting:
    if not isinstance(payload, Mapping):
        raise OpenDataIntegrityError("Checkpoint attempt is malformed")
    status = payload.get("status_code")
    length = payload.get("response_content_length")
    return AttemptAccounting(
        attempt_number=_required_int(payload, "attempt_number"),
        started_at=_required_text(payload, "started_at"),
        completed_at=_required_text(payload, "completed_at"),
        requested_range_start=_required_int(payload, "requested_range_start"),
        status_code=None if status is None else _strict_int(status, "status_code"),
        response_content_length=(
            None if length is None else _strict_int(length, "response_content_length")
        ),
        received_bytes=_required_int(payload, "received_bytes"),
        outcome=_required_text(payload, "outcome"),
    )


def _latest_revision(latest: Mapping[str, Any] | None, request: DownloadRequest) -> int:
    if latest is None:
        return 0
    if (
        latest.get("source_key") != request.source_key
        or latest.get("object_key") != request.object_key
    ):
        raise OpenDataIntegrityError("Latest pointer belongs to a different source object")
    revision = latest.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise OpenDataIntegrityError("Latest revision pointer is malformed")
    return revision


def _declared_sha256(headers: Mapping[str, str]) -> str | None:
    direct = headers.get("x-checksum-sha256")
    if direct is not None:
        normalized = direct.strip().lower()
        if not _SHA256.fullmatch(normalized):
            raise OpenDataIntegrityError("X-Checksum-Sha256 is malformed")
        return normalized
    digest = headers.get("digest")
    if digest is None:
        return None
    for item in digest.split(","):
        algorithm, separator, encoded = item.strip().partition("=")
        if separator and algorithm.lower() in {"sha-256", "sha256"}:
            try:
                raw = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise OpenDataIntegrityError("Digest SHA-256 is malformed") from exc
            if len(raw) != 32:
                raise OpenDataIntegrityError("Digest SHA-256 has the wrong length")
            return raw.hex()
    return None


def _normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        name = str(key).strip().lower()
        if name in normalized:
            raise OpenDataIntegrityError("Duplicate response header")
        normalized[name] = str(value).strip()
    return normalized


def _validate_file_identity(path: Path, sha256: str, size: int) -> None:
    if not path.is_file() or path.is_symlink() or _is_reparse(path):
        raise OpenDataIntegrityError("Expected immutable content file is missing or unsafe")
    if path.stat().st_size != size or _file_sha256(path) != sha256:
        raise OpenDataIntegrityError("Immutable content bytes do not match their manifest")


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _assert_secret_free(payload)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink() or _is_reparse(path):
        raise OpenDataIntegrityError("Manifest/checkpoint is missing or unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OpenDataIntegrityError("Manifest/checkpoint JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise OpenDataIntegrityError("Manifest/checkpoint must be a JSON object")
    _assert_secret_free(payload)
    return payload


def _assert_secret_free(value: object, path: str = "manifest") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in _SECRET_MARKERS) and not key_text.endswith(
                ("_identity_checksum", "_checksum")
            ):
                raise OpenDataIntegrityError(f"Secret-like manifest field at {path}.{key}")
            _assert_secret_free(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_secret_free(item, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if "?" in value or any(f"{marker}=" in lowered for marker in _SECRET_MARKERS):
            raise OpenDataIntegrityError(f"Secret-like manifest value at {path}")


def _canonical_checksum(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    _hash_file_into(path, digest)
    return digest.hexdigest()


def _hash_file_into(path: Path, digest: Any) -> None:
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)


def _sync_file(path: Path) -> None:
    with path.open("rb+") as stream:
        os.fsync(stream.fileno())


def _normalize_policy_host(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    if not host or ":" in host or "/" in host:
        raise ValueError("Policy host must be a bare DNS name")
    return host.encode("idna").decode("ascii")


def _validate_path_prefix(value: str) -> None:
    if not value.startswith("/") or "\\" in value:
        raise ValueError("Path allowlist prefixes must be absolute URL paths")
    if any(part in {".", ".."} for part in PurePosixPath(unquote(value)).parts):
        raise ValueError("Path allowlist prefix contains traversal")


def _normalize_mime(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _source_is_prohibited(source_key: str) -> bool:
    return any(marker in source_key.upper() for marker in PROHIBITED_SOURCE_MARKERS)


def _host_is_prohibited(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    return any(
        lowered == suffix or lowered.endswith(f".{suffix}")
        for suffix in PROHIBITED_HOST_SUFFIXES
    )


def _reject_secret_text(value: str, field: str) -> None:
    lowered = value.lower()
    if "?" in value or any(f"{marker}=" in lowered for marker in _SECRET_MARKERS):
        raise ValueError(f"{field} cannot contain secret-like material")


def _contains_secret_marker(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    return any(marker in normalized for marker in _SECRET_MARKERS)


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise OpenDataIntegrityError(f"{key} is missing or malformed")
    return value


def _required_int(payload: Mapping[str, Any], key: str) -> int:
    return _strict_int(payload.get(key), key)


def _mapping_text(payload: Mapping[str, Any], key: str) -> str:
    return _required_text(payload, key)


def _mapping_int(payload: Mapping[str, Any], key: str) -> int:
    return _required_int(payload, key)


def _strict_int(value: object, key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OpenDataIntegrityError(f"{key} is missing or malformed")
    return value


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Clock must return timezone-aware timestamps")
    return value.astimezone(UTC).isoformat()


def _is_reparse(path: Path) -> bool:
    try:
        return bool(path.lstat().st_file_attributes & _REPARSE_POINT)  # type: ignore[attr-defined]
    except (AttributeError, FileNotFoundError):
        return False


def _assert_no_links(path: Path) -> None:
    if path.is_symlink() or _is_reparse(path):
        raise OpenDataIntegrityError("Storage paths cannot be links or reparse points")


def _assert_no_link_ancestors(path: Path) -> None:
    current = path
    while True:
        if current.exists() or current.is_symlink():
            _assert_no_links(current)
        if current.parent == current:
            break
        current = current.parent


def _assert_no_links_between(root: Path, target: Path) -> None:
    try:
        relative = target.absolute().relative_to(root)
    except ValueError as exc:
        raise OpenDataIntegrityError("Storage path escaped its root") from exc
    current = root
    _assert_no_links(current)
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _assert_no_links(current)


# Candidate open-data sources stay blocked until the project owner records an
# exact authorization. Prohibited sources document routes this module will never
# execute, even if a caller attempts to construct a permissive replacement policy.
DEFAULT_SOURCE_POLICIES: Mapping[str, OpenDataSourcePolicy] = MappingProxyType(
    {
        "RETROSHEET_EVENT_ARCHIVES": OpenDataSourcePolicy(
            source_key="RETROSHEET_EVENT_ARCHIVES",
            state=SourcePolicyState.BLOCKED_SOURCE_AUTHORIZATION,
            official_hosts=("www.retrosheet.org",),
            allowed_path_prefixes=("/events/",),
            allowed_mime_types=("application/octet-stream", "application/zip"),
            allowed_archive_formats=(ArchiveFormat.ZIP,),
            maximum_file_bytes=8 * 1024**3,
            policy_reference="project-policy://open-data/retrosheet",
            decision_basis="Candidate official-host source; owner authorization is not recorded.",
        ),
        "CHADWICK_REGISTER_ARCHIVES": OpenDataSourcePolicy(
            source_key="CHADWICK_REGISTER_ARCHIVES",
            state=SourcePolicyState.BLOCKED_SOURCE_AUTHORIZATION,
            official_hosts=(
                "codeload.github.com",
                "github.com",
                "raw.githubusercontent.com",
            ),
            allowed_path_prefixes=(
                "/chadwickbureau/register/",
                "/chadwickbureau/register/archive/",
            ),
            allowed_mime_types=(
                "application/gzip",
                "application/octet-stream",
                "application/zip",
            ),
            allowed_archive_formats=(ArchiveFormat.ZIP, ArchiveFormat.TAR_GZ),
            maximum_file_bytes=8 * 1024**3,
            policy_reference="project-policy://open-data/chadwick-register",
            decision_basis="Candidate official-host source; owner authorization is not recorded.",
        ),
        "SABR_LAHMAN_BOX_DOWNLOADS": OpenDataSourcePolicy(
            source_key="SABR_LAHMAN_BOX_DOWNLOADS",
            state=SourcePolicyState.BLOCKED_SOURCE_AUTHORIZATION,
            official_hosts=("app.box.com", "box.com", "sabr.app"),
            allowed_path_prefixes=("/",),
            allowed_mime_types=("application/octet-stream", "application/zip"),
            allowed_archive_formats=(ArchiveFormat.ZIP,),
            maximum_file_bytes=8 * 1024**3,
            policy_reference="project-policy://open-data/sabr-lahman",
            decision_basis=(
                "Candidate SABR/Lahman official-host source; owner authorization is not recorded."
            ),
            allowed_query_keys=("file_id", "rm", "shared_name"),
        ),
        "MLB_STATS_API": OpenDataSourcePolicy(
            source_key="MLB_STATS_API",
            state=SourcePolicyState.PROHIBITED,
            official_hosts=(),
            allowed_path_prefixes=(),
            allowed_mime_types=(),
            allowed_archive_formats=(ArchiveFormat.NONE,),
            maximum_file_bytes=1,
            policy_reference="project-policy://prohibited/mlb",
            decision_basis="MLB automated acquisition is outside this open-data infrastructure.",
        ),
        "BASEBALL_SAVANT_STATCAST": OpenDataSourcePolicy(
            source_key="BASEBALL_SAVANT_STATCAST",
            state=SourcePolicyState.PROHIBITED,
            official_hosts=(),
            allowed_path_prefixes=(),
            allowed_mime_types=(),
            allowed_archive_formats=(ArchiveFormat.NONE,),
            maximum_file_bytes=1,
            policy_reference="project-policy://prohibited/savant",
            decision_basis="Baseball Savant automation is prohibited by this infrastructure.",
        ),
        "PYBASEBALL_SCRAPER": OpenDataSourcePolicy(
            source_key="PYBASEBALL_SCRAPER",
            state=SourcePolicyState.PROHIBITED,
            official_hosts=(),
            allowed_path_prefixes=(),
            allowed_mime_types=(),
            allowed_archive_formats=(ArchiveFormat.NONE,),
            maximum_file_bytes=1,
            policy_reference="project-policy://prohibited/scrapers",
            decision_basis="Rejected scraper wrappers cannot bypass source authorization.",
        ),
    }
)


__all__ = [
    "ArchiveFormat",
    "AttemptAccounting",
    "DEFAULT_SOURCE_POLICIES",
    "DownloadArtifact",
    "DownloadDisposition",
    "DownloadRequest",
    "DownloadTransport",
    "ExtractionArtifact",
    "ExtractionLimits",
    "OpenDataAcquirer",
    "OpenDataAcquisitionError",
    "OpenDataIntegrityError",
    "OpenDataInterruptedError",
    "OpenDataPolicyError",
    "OpenDataSourcePolicy",
    "OpenDataStorageLimitError",
    "SourcePolicyState",
    "TransportRequest",
    "TransportResponse",
    "UnsafeArchiveError",
]
