from __future__ import annotations

import hashlib
import gzip
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RawArchiveRecord:
    source: str
    ingestion_timestamp: datetime
    source_event_timestamp: datetime | None
    # SHA-256 of the canonical provider payload before it is wrapped in archive
    # metadata or compressed. ``checksum`` is retained as a compatibility alias.
    checksum: str
    status: str
    record_count: int
    raw_file_reference: str
    schema_version: str
    # SHA-256 of the exact bytes stored at ``raw_file_reference``.
    file_checksum: str | None = None
    bytes_written: int | None = None
    compressed: bool = False
    historical: bool = False
    idempotent_reuse: bool = False
    request_fingerprint: str | None = None

    @property
    def payload_checksum(self) -> str:
        """Checksum of canonical provider payload bytes, not archive-file bytes."""

        return self.checksum


class RawArchiveConflictError(RuntimeError):
    """A semantic historical request already exists with different bytes."""


class RawArchive:
    """Append-only raw JSON archive partitioned by source and UTC date."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(
        self,
        source: str,
        payload: dict[str, Any] | list[Any],
        *,
        source_event_timestamp: datetime | None = None,
        status: str = "success",
        schema_version: str = "1",
    ) -> RawArchiveRecord:
        """Preserve the existing live archive layout and uncompressed format."""

        timestamp = datetime.now(UTC)
        checksum = _payload_checksum(payload)
        directory = self.root / source.lower() / f"{timestamp:%Y}" / f"{timestamp:%m}" / f"{timestamp:%d}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{timestamp:%H%M%S_%f}_{uuid4().hex[:10]}.json"
        document = _document(
            source,
            payload,
            timestamp=timestamp,
            source_event_timestamp=source_event_timestamp,
            checksum=checksum,
            status=status,
            schema_version=schema_version,
            historical=False,
            request_fingerprint=None,
        )
        # Exclusive creation and a unique filename make snapshots immutable.
        with path.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        return _record(
            path,
            document,
            file_checksum=_file_checksum(path),
            compressed=False,
            historical=False,
            idempotent_reuse=False,
        )

    def write_historical(
        self,
        source: str,
        payload: dict[str, Any] | list[Any],
        *,
        partition_date: date,
        request_fingerprint: str,
        source_event_timestamp: datetime | None = None,
        status: str = "success",
        schema_version: str = "1",
        source_mode: str = "LIVE",
        research_provenance: str = "HISTORICAL_RECONSTRUCTION",
        temporal_semantics: str = "RETROSPECTIVE_RECONSTRUCTION",
        evidence_class: str = "RECONSTRUCTABLE_POINT_IN_TIME",
        checkpoint_id: str | None = None,
        run_id: str | None = None,
    ) -> RawArchiveRecord:
        """Atomically write an idempotent gzip historical snapshot.

        Historical paths are semantic rather than timestamp-random. Repeating
        the same request/payload reuses the existing verified snapshot. Reusing
        a request fingerprint for different bytes fails closed.
        """

        fingerprint = _fingerprint(request_fingerprint)
        if source_mode not in {"LIVE", "DEMO"}:
            raise ValueError("Historical archive source_mode must be LIVE or DEMO")
        if research_provenance != "HISTORICAL_RECONSTRUCTION":
            raise ValueError("Historical archive requires historical reconstruction provenance")
        if temporal_semantics != "RETROSPECTIVE_RECONSTRUCTION":
            raise ValueError("Historical archive requires retrospective temporal semantics")
        if evidence_class not in {
            "RECONSTRUCTABLE_POINT_IN_TIME",
            "PARTIALLY_RECONSTRUCTABLE",
            "FORWARD_ONLY",
        }:
            raise ValueError("Historical archive evidence_class is invalid")
        safe_source = _segment(source)
        directory = (
            self.root
            / "historical"
            / safe_source
            / f"{partition_date:%Y}"
            / f"{partition_date:%m}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{partition_date.isoformat()}_{fingerprint[:24]}.json.gz"
        payload_checksum = _payload_checksum(payload)
        expected_metadata = {
            "status": status,
            "schema_version": schema_version,
            "source_mode": source_mode,
            "research_provenance": research_provenance,
            "temporal_semantics": temporal_semantics,
            "evidence_class": evidence_class,
            "checkpoint_id": checkpoint_id,
            "source_event_timestamp": (
                source_event_timestamp.isoformat() if source_event_timestamp else None
            ),
        }
        if path.exists():
            return self._reuse_historical(
                path,
                expected_source=source,
                expected_fingerprint=fingerprint,
                expected_payload_checksum=payload_checksum,
                expected_metadata=expected_metadata,
            )

        timestamp = datetime.now(UTC)
        document = _document(
            source,
            payload,
            timestamp=timestamp,
            source_event_timestamp=source_event_timestamp,
            checksum=payload_checksum,
            status=status,
            schema_version=schema_version,
            historical=True,
            request_fingerprint=fingerprint,
            historical_metadata={
                "source_mode": source_mode,
                "research_provenance": research_provenance,
                "temporal_semantics": temporal_semantics,
                "evidence_class": evidence_class,
                "checkpoint_id": checkpoint_id,
                "run_id": run_id,
            },
        )
        encoded = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        temporary = directory / f".{path.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as raw_stream:
                with gzip.GzipFile(
                    filename="",
                    fileobj=raw_stream,
                    mode="wb",
                    mtime=0,
                ) as stream:
                    stream.write(encoded)
                raw_stream.flush()
                os.fsync(raw_stream.fileno())
            # Another single-source worker may have completed the semantic
            # request while this worker encoded it. Validate and reuse it.
            if path.exists():
                temporary.unlink()
                return self._reuse_historical(
                    path,
                    expected_source=source,
                    expected_fingerprint=fingerprint,
                    expected_payload_checksum=payload_checksum,
                    expected_metadata=expected_metadata,
                )
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return _record(
            path,
            document,
            file_checksum=_file_checksum(path),
            compressed=True,
            historical=True,
            idempotent_reuse=False,
        )

    def _reuse_historical(
        self,
        path: Path,
        *,
        expected_source: str,
        expected_fingerprint: str,
        expected_payload_checksum: str,
        expected_metadata: dict[str, Any],
    ) -> RawArchiveRecord:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                document = json.load(stream)
            metadata = document["metadata"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RawArchiveConflictError(
                f"Existing historical archive is unreadable: {path.name}"
            ) from exc
        if (
            metadata.get("source") != expected_source
            or metadata.get("request_fingerprint") != expected_fingerprint
            or metadata.get("checksum") != expected_payload_checksum
            or any(metadata.get(key) != value for key, value in expected_metadata.items())
        ):
            raise RawArchiveConflictError(
                "Historical request fingerprint already exists with different evidence"
            )
        return _record(
            path,
            document,
            file_checksum=_file_checksum(path),
            compressed=True,
            historical=True,
            idempotent_reuse=True,
        )


def _payload_checksum(payload: dict[str, Any] | list[Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _document(
    source: str,
    payload: dict[str, Any] | list[Any],
    *,
    timestamp: datetime,
    source_event_timestamp: datetime | None,
    checksum: str,
    status: str,
    schema_version: str,
    historical: bool,
    request_fingerprint: str | None,
    historical_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "metadata": {
            "source": source,
            "ingestion_timestamp": timestamp.isoformat(),
            "source_event_timestamp": (
                source_event_timestamp.isoformat() if source_event_timestamp else None
            ),
            "checksum": checksum,
            "status": status,
            "record_count": len(payload) if isinstance(payload, list) else 1,
            "schema_version": schema_version,
            "historical": historical,
            "request_fingerprint": request_fingerprint,
            **(historical_metadata or {}),
        },
        "payload": payload,
    }


def _record(
    path: Path,
    document: dict[str, Any],
    *,
    file_checksum: str,
    compressed: bool,
    historical: bool,
    idempotent_reuse: bool,
) -> RawArchiveRecord:
    metadata = document["metadata"]
    source_event = metadata.get("source_event_timestamp")
    return RawArchiveRecord(
        source=str(metadata["source"]),
        ingestion_timestamp=datetime.fromisoformat(str(metadata["ingestion_timestamp"])),
        source_event_timestamp=datetime.fromisoformat(source_event) if source_event else None,
        checksum=str(metadata["checksum"]),
        status=str(metadata["status"]),
        record_count=int(metadata["record_count"]),
        raw_file_reference=str(path),
        schema_version=str(metadata["schema_version"]),
        file_checksum=file_checksum,
        bytes_written=path.stat().st_size,
        compressed=compressed,
        historical=historical,
        idempotent_reuse=idempotent_reuse,
        request_fingerprint=metadata.get("request_fingerprint"),
    )


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(value: str) -> str:
    normalized = value.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized):
        return normalized
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _segment(value: str) -> str:
    result = re.sub(r"[^a-z0-9._-]+", "_", value.strip().lower()).strip("._")
    if not result:
        raise ValueError("Archive source must contain a safe path segment")
    return result
