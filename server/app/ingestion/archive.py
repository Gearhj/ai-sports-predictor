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
    #: Which observation of this request these bytes are. Revision 1 is the
    #: first capture; a later capture whose payload differs is revision 2, and
    #: so on. Identical bytes never create a revision.
    revision: int = 1
    #: The payload checksum this revision superseded, or None for the first.
    supersedes_payload_checksum: str | None = None

    @property
    def payload_checksum(self) -> str:
        """Checksum of canonical provider payload bytes, not archive-file bytes."""

        return self.checksum


class RawArchiveConflictError(RuntimeError):
    """Captured bytes disagree with what an existing immutable object holds.

    This is still raised, and it still fails closed. What it no longer means is
    "the same request returned something different later" -- that is an ordinary
    fact about a source whose answer changes, and it now produces a NEW
    immutable revision rather than a refusal. The error is reserved for a real
    integrity problem: an existing object that cannot be read, or whose stored
    metadata contradicts the request it claims to answer.
    """


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

        Historical paths are semantic rather than timestamp-random, and they are
        addressed by REQUEST **and** CONTENT. Repeating the same request with the
        same payload reuses the existing verified snapshot; repeating it after
        the source's answer changed writes a new immutable revision beside the
        old one and records which checksum it superseded.

        WHY THE IDENTITY HAD TO WIDEN
        -----------------------------
        The path used to be ``{date}_{request_fingerprint}``, which encodes the
        request and nothing about the observation. That is correct for a settled
        historical day, whose response never changes. It is wrong for any day
        still in progress, and wrong again whenever an official correction lands
        later: two genuinely different observations of one request mapped onto
        one immutable object, and the second was refused.

        The September 12 slate hit exactly this. A canonical pass at 19:39 EDT
        captured the schedule with nine games final; a second pass after more
        games finished captured a legitimately different response and could not
        store it, so no governed final could be written for the later finishers.
        Waiting for the day to end would not have fixed it either, because an
        official scoring correction reproduces the same collision.

        Nothing is relaxed to achieve this. Captured bytes are never rewritten,
        no original is deleted, no timestamp is reused, and a request whose
        stored object contradicts it still fails closed. The only thing that
        changed is that a different observation gets a different identity, which
        is what it always was.
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
        payload_checksum = _payload_checksum(payload)
        # Request identity, then content identity. The legacy path holds the
        # first capture ever made for this request and is still read, still
        # reused when the bytes match, and never removed.
        legacy_path = directory / f"{partition_date.isoformat()}_{fingerprint[:24]}.json.gz"
        path = (
            directory
            / f"{partition_date.isoformat()}_{fingerprint[:24]}_{payload_checksum[:16]}.json.gz"
        )
        ledger_path = directory / f"{partition_date.isoformat()}_{fingerprint[:24]}.revisions.json"
        # What must agree for an existing object to BE this observation. The
        # run that captured the bytes is deliberately not in here: checkpoint_id
        # and run_id record which pass fetched them, and identical bytes fetched
        # by a later pass are the same observation, not a contradiction. Keeping
        # checkpoint_id in this comparison made every unchanged per-game feed
        # conflict the moment the chunk it belonged to was re-keyed. Both values
        # are still written into the document as provenance.
        expected_metadata = {
            "status": status,
            "schema_version": schema_version,
            "source_mode": source_mode,
            "research_provenance": research_provenance,
            "temporal_semantics": temporal_semantics,
            "evidence_class": evidence_class,
            # A source fact about the observation, so it stays in the check.
            "source_event_timestamp": (
                source_event_timestamp.isoformat() if source_event_timestamp else None
            ),
        }
        reuse_args = {
            "expected_source": source,
            "expected_fingerprint": fingerprint,
            "expected_payload_checksum": payload_checksum,
            "expected_metadata": expected_metadata,
        }
        if path.exists():
            return self._reuse_historical(path, ledger_path=ledger_path, **reuse_args)
        # An identical payload already captured under the pre-revision path is
        # the same observation, so it is reused rather than duplicated.
        if legacy_path.exists() and _legacy_payload_checksum(legacy_path) == payload_checksum:
            return self._reuse_historical(legacy_path, ledger_path=ledger_path, **reuse_args)

        ledger = _read_revision_ledger(ledger_path)
        if not ledger["revisions"] and legacy_path.exists():
            # Adopt the pre-revision capture as revision 1 so the lineage starts
            # where the evidence actually starts. Its bytes are not touched.
            legacy = _legacy_revision_entry(legacy_path, fingerprint)
            if legacy is not None:
                ledger["revisions"].append(legacy)
        supersedes = ledger["revisions"][-1]["payload_checksum"] if ledger["revisions"] else None
        revision = len(ledger["revisions"]) + 1

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
                "revision": revision,
                "supersedes_payload_checksum": supersedes,
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
                return self._reuse_historical(path, ledger_path=ledger_path, **reuse_args)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        # The ledger is a POINTER, written after the bytes are durable. It
        # selects a current revision and records the chain; it never holds
        # evidence of its own, so losing or rebuilding it cannot alter a capture.
        _append_revision(
            ledger_path,
            ledger,
            {
                "revision": revision,
                "payload_checksum": payload_checksum,
                "supersedes_payload_checksum": supersedes,
                "raw_file_reference": str(path),
                "retrieved_at": timestamp.isoformat(),
                "source_event_timestamp": (
                    source_event_timestamp.isoformat() if source_event_timestamp else None
                ),
                "request_fingerprint": fingerprint,
                "source": source,
            },
        )
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
        ledger_path: Path | None = None,
    ) -> RawArchiveRecord:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                document = json.load(stream)
            metadata = document["metadata"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RawArchiveConflictError(
                f"Existing historical archive is unreadable: {path.name}"
            ) from exc
        # Reached only for an object addressed by THESE bytes, so a checksum
        # mismatch here means the stored object is not what its own name says --
        # a real integrity failure, not a source that changed its answer.
        if (
            metadata.get("source") != expected_source
            or metadata.get("request_fingerprint") != expected_fingerprint
            or metadata.get("checksum") != expected_payload_checksum
            or any(metadata.get(key) != value for key, value in expected_metadata.items())
        ):
            raise RawArchiveConflictError(
                "Stored historical capture contradicts the request it answers: "
                f"{path.name}"
            )
        return _record(
            path,
            document,
            file_checksum=_file_checksum(path),
            compressed=True,
            historical=True,
            idempotent_reuse=True,
        )

    def historical_revisions(
        self, source: str, *, partition_date: date, request_fingerprint: str,
    ) -> list[dict[str, Any]]:
        """Every captured observation of one request, oldest first."""

        fingerprint = _fingerprint(request_fingerprint)
        directory = (
            self.root / "historical" / _segment(source)
            / f"{partition_date:%Y}" / f"{partition_date:%m}"
        )
        ledger_path = (
            directory / f"{partition_date.isoformat()}_{fingerprint[:24]}.revisions.json"
        )
        return _read_revision_ledger(ledger_path)["revisions"]


def _read_revision_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "AISP_RAW_ARCHIVE_REVISIONS_V1", "revisions": []}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A pointer is rebuildable and holds no evidence, so an unreadable one
        # must never block a capture. The bytes on disk remain authoritative.
        return {"schema_version": "AISP_RAW_ARCHIVE_REVISIONS_V1", "revisions": []}
    revisions = document.get("revisions")
    if not isinstance(revisions, list):
        revisions = []
    return {"schema_version": "AISP_RAW_ARCHIVE_REVISIONS_V1", "revisions": revisions}


def _append_revision(path: Path, ledger: dict[str, Any], entry: dict[str, Any]) -> None:
    revisions = [*ledger["revisions"], entry]
    document = {
        "schema_version": "AISP_RAW_ARCHIVE_REVISIONS_V1",
        "current_payload_checksum": entry["payload_checksum"],
        "current_raw_file_reference": entry["raw_file_reference"],
        "revisions": revisions,
    }
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _legacy_payload_checksum(path: Path) -> str | None:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return (json.load(stream).get("metadata") or {}).get("checksum")
    except (OSError, ValueError, AttributeError):
        return None


def _legacy_revision_entry(path: Path, fingerprint: str) -> dict[str, Any] | None:
    """The pre-revision capture, described as revision 1 without touching it."""

    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            metadata = json.load(stream).get("metadata") or {}
    except (OSError, ValueError, AttributeError):
        return None
    checksum = metadata.get("checksum")
    if not checksum:
        return None
    return {
        "revision": 1,
        "payload_checksum": checksum,
        "supersedes_payload_checksum": None,
        "raw_file_reference": str(path),
        # The original's own recorded retrieval time, never a fresh clock.
        "retrieved_at": metadata.get("ingestion_timestamp"),
        "source_event_timestamp": metadata.get("source_event_timestamp"),
        "request_fingerprint": fingerprint,
        "source": metadata.get("source"),
        "adopted_from_pre_revision_path": True,
    }


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
        # Present on revisioned captures; a pre-revision object is revision 1.
        revision=int(metadata.get("revision") or 1),
        supersedes_payload_checksum=metadata.get("supersedes_payload_checksum"),
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
