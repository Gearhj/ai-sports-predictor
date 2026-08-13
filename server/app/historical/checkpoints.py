from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import HistoricalSourceVersion, HistoryBackfillCheckpoint


CheckpointStatus = Literal["SUCCEEDED", "FAILED", "SKIPPED", "BLOCKED"]


@dataclass(frozen=True)
class HistoricalCheckpoint:
    source_version_id: str
    stream_key: str
    query_fingerprint: str
    status: CheckpointStatus
    requested_at: datetime
    season: int | None
    event_start_at: datetime | None
    event_end_at: datetime | None
    sequence: int
    ingestion_run_id: str | None = None
    parent_checkpoint_id: str | None = None
    completed_at: datetime | None = None
    cursor_before: dict[str, Any] | None = None
    cursor_after: dict[str, Any] | None = None
    # Exact physical attempts observed by the request executor. JSON archive
    # checkpoints may legitimately record zero for injected/offline adapters;
    # the canonical ORM currently requires at least one physical attempt.
    attempt_count: int = 0
    retry_count: int = 0
    records_received: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    duplicate_count: int = 0
    raw_references: tuple[str, ...] = ()
    raw_checksums: tuple[str, ...] = ()
    error_type: str | None = None
    error_message: str | None = None
    next_resume_at: datetime | None = None
    schema_version: str = "gate3a-history-v1"
    code_revision: str = "unknown"
    quality_summary: dict[str, Any] = field(default_factory=dict)
    source_mode: str = "LIVE"
    temporal_semantics: str = "RETROSPECTIVE_RECONSTRUCTION"
    identity_checksum: str = ""
    checkpoint_id: str = field(default_factory=lambda: uuid4().hex)


class CheckpointRepository(Protocol):
    def latest(
        self,
        source_version_id: str,
        stream_key: str,
        query_fingerprint: str,
    ) -> HistoricalCheckpoint | None: ...

    def completed(
        self,
        source_version_id: str,
        stream_key: str,
        query_fingerprint: str,
    ) -> HistoricalCheckpoint | None: ...

    def append(self, checkpoint: HistoricalCheckpoint) -> None: ...


class MemoryCheckpointRepository:
    def __init__(self) -> None:
        self._records: list[HistoricalCheckpoint] = []

    @property
    def records(self) -> tuple[HistoricalCheckpoint, ...]:
        return tuple(self._records)

    def latest(
        self,
        source_version_id: str,
        stream_key: str,
        query_fingerprint: str,
    ) -> HistoricalCheckpoint | None:
        matches = [
            item
            for item in self._records
            if item.source_version_id == source_version_id
            and item.stream_key == stream_key
            and item.query_fingerprint == query_fingerprint
        ]
        return matches[-1] if matches else None

    def completed(
        self,
        source_version_id: str,
        stream_key: str,
        query_fingerprint: str,
    ) -> HistoricalCheckpoint | None:
        return next(
            (
                item
                for item in reversed(self._records)
                if item.source_version_id == source_version_id
                and item.stream_key == stream_key
                and item.query_fingerprint == query_fingerprint
                and item.status == "SUCCEEDED"
            ),
            None,
        )

    def append(self, checkpoint: HistoricalCheckpoint) -> None:
        self._records.append(_with_identity(checkpoint))


class JsonCheckpointRepository:
    """Append-only archive JSONL repository; success means raw archive success."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def latest(
        self,
        source_version_id: str,
        stream_key: str,
        query_fingerprint: str,
    ) -> HistoricalCheckpoint | None:
        matches = [
            item
            for item in self._read()
            if item.source_version_id == source_version_id
            and item.stream_key == stream_key
            and item.query_fingerprint == query_fingerprint
        ]
        return matches[-1] if matches else None

    def completed(
        self,
        source_version_id: str,
        stream_key: str,
        query_fingerprint: str,
    ) -> HistoricalCheckpoint | None:
        return next(
            (
                item
                for item in reversed(self._read())
                if item.source_version_id == source_version_id
                and item.stream_key == stream_key
                and item.query_fingerprint == query_fingerprint
                and item.status == "SUCCEEDED"
            ),
            None,
        )

    def append(self, checkpoint: HistoricalCheckpoint) -> None:
        checkpoint = _with_identity(checkpoint)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(
                _checkpoint_document(checkpoint),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        # O_APPEND keeps independently completed chunks intact. Each JSON line
        # is immutable and carries its own checksum for corruption detection.
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _read(self) -> list[HistoricalCheckpoint]:
        if not self.path.exists():
            return []
        records: list[HistoricalCheckpoint] = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, raw in enumerate(stream, start=1):
                if not raw.strip():
                    continue
                try:
                    document = json.loads(raw)
                    payload = document["checkpoint"]
                    canonical = json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    if hashlib.sha256(canonical).hexdigest() != document["checksum"]:
                        raise ValueError("checksum mismatch")
                    records.append(_checkpoint_from_document(payload))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"Checkpoint repository is corrupt at line {line_number}"
                    ) from exc
        return records


class SqlAlchemyCheckpointRepository:
    """Session-bound append-only repository for terminal Gate 3A checkpoints.

    Canonical UUID source-version IDs are accepted directly. The runner's
    ``source_key:dataset_version`` identifier is resolved exactly against the
    immutable source registry; no fuzzy provider matching is performed.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def latest(
        self,
        source_version_id: str,
        stream_key: str,
        query_fingerprint: str,
    ) -> HistoricalCheckpoint | None:
        record = self.session.scalar(
            select(HistoryBackfillCheckpoint)
            .where(
                HistoryBackfillCheckpoint.source_version_id
                == self._source_version_id(source_version_id),
                HistoryBackfillCheckpoint.stream_key == stream_key,
                HistoryBackfillCheckpoint.query_fingerprint == query_fingerprint,
            )
            .order_by(
                HistoryBackfillCheckpoint.completed_at.desc(),
                HistoryBackfillCheckpoint.created_at.desc(),
                HistoryBackfillCheckpoint.id.desc(),
            )
            .limit(1)
        )
        return _checkpoint_from_record(record) if record is not None else None

    def completed(
        self,
        source_version_id: str,
        stream_key: str,
        query_fingerprint: str,
    ) -> HistoricalCheckpoint | None:
        record = self.session.scalar(
            select(HistoryBackfillCheckpoint)
            .where(
                HistoryBackfillCheckpoint.source_version_id
                == self._source_version_id(source_version_id),
                HistoryBackfillCheckpoint.stream_key == stream_key,
                HistoryBackfillCheckpoint.query_fingerprint == query_fingerprint,
                HistoryBackfillCheckpoint.status == "SUCCEEDED",
            )
            .order_by(
                HistoryBackfillCheckpoint.completed_at.desc(),
                HistoryBackfillCheckpoint.created_at.desc(),
                HistoryBackfillCheckpoint.id.desc(),
            )
            .limit(1)
        )
        return _checkpoint_from_record(record) if record is not None else None

    def append(self, checkpoint: HistoricalCheckpoint) -> None:
        checkpoint = _with_identity(checkpoint)
        if checkpoint.completed_at is None:
            raise ValueError("SQLAlchemy checkpoints must be terminal and include completed_at")
        if checkpoint.ingestion_run_id is None:
            raise ValueError("SQLAlchemy checkpoints require a canonical ingestion_run_id")
        if checkpoint.status == "SUCCEEDED":
            completed = self.completed(
                checkpoint.source_version_id,
                checkpoint.stream_key,
                checkpoint.query_fingerprint,
            )
            if completed is not None:
                return
        existing = self.session.scalar(
            select(HistoryBackfillCheckpoint).where(
                HistoryBackfillCheckpoint.identity_checksum == checkpoint.identity_checksum
            )
        )
        if existing is not None:
            return
        record = HistoryBackfillCheckpoint(
            id=_uuid_value(checkpoint.checkpoint_id),
            ingestion_run_id=_uuid_value(checkpoint.ingestion_run_id),
            source_version_id=self._source_version_id(checkpoint.source_version_id),
            parent_checkpoint_id=(
                _uuid_value(checkpoint.parent_checkpoint_id)
                if checkpoint.parent_checkpoint_id else None
            ),
            stream_key=checkpoint.stream_key,
            sequence=checkpoint.sequence,
            season=checkpoint.season,
            event_start_at=checkpoint.event_start_at,
            event_end_at=checkpoint.event_end_at,
            cursor_before=checkpoint.cursor_before,
            cursor_after=checkpoint.cursor_after,
            query_fingerprint=checkpoint.query_fingerprint,
            status=checkpoint.status,
            requested_at=checkpoint.requested_at,
            completed_at=checkpoint.completed_at,
            attempt_count=checkpoint.attempt_count,
            retry_count=checkpoint.retry_count,
            records_received=checkpoint.records_received,
            records_accepted=checkpoint.records_accepted,
            records_rejected=checkpoint.records_rejected,
            duplicate_count=checkpoint.duplicate_count,
            raw_references=list(checkpoint.raw_references),
            raw_checksums=list(checkpoint.raw_checksums),
            next_resume_at=checkpoint.next_resume_at,
            schema_version=checkpoint.schema_version,
            code_revision=checkpoint.code_revision,
            quality_summary=checkpoint.quality_summary,
            error_type=checkpoint.error_type,
            error_message=checkpoint.error_message,
            source_mode=checkpoint.source_mode,
            temporal_semantics=checkpoint.temporal_semantics,
            identity_checksum=checkpoint.identity_checksum,
        )
        self.session.add(record)
        self.session.flush()

    def _source_version_id(self, value: str):
        try:
            return _uuid_value(value)
        except ValueError:
            if ":" not in value:
                raise
            source_key, dataset_version = value.split(":", 1)
            record = self.session.scalar(
                select(HistoricalSourceVersion).where(
                    func.lower(HistoricalSourceVersion.source_key) == source_key.lower(),
                    HistoricalSourceVersion.dataset_version == dataset_version,
                )
            )
            if record is None:
                raise ValueError(f"Unknown historical source version {value!r}")
            return record.id


def _checkpoint_from_record(record: HistoryBackfillCheckpoint) -> HistoricalCheckpoint:
    return HistoricalCheckpoint(
        checkpoint_id=str(record.id),
        ingestion_run_id=str(record.ingestion_run_id),
        source_version_id=str(record.source_version_id),
        parent_checkpoint_id=(
            str(record.parent_checkpoint_id) if record.parent_checkpoint_id else None
        ),
        stream_key=record.stream_key,
        sequence=record.sequence,
        season=record.season,
        event_start_at=record.event_start_at,
        event_end_at=record.event_end_at,
        cursor_before=record.cursor_before,
        cursor_after=record.cursor_after,
        query_fingerprint=record.query_fingerprint,
        status=record.status,
        requested_at=record.requested_at,
        completed_at=record.completed_at,
        attempt_count=record.attempt_count,
        retry_count=record.retry_count,
        records_received=record.records_received,
        records_accepted=record.records_accepted,
        records_rejected=record.records_rejected,
        duplicate_count=record.duplicate_count,
        raw_references=tuple(record.raw_references or ()),
        raw_checksums=tuple(record.raw_checksums or ()),
        next_resume_at=record.next_resume_at,
        schema_version=record.schema_version,
        code_revision=record.code_revision,
        quality_summary=record.quality_summary or {},
        error_type=record.error_type,
        error_message=record.error_message,
        source_mode=record.source_mode,
        temporal_semantics=record.temporal_semantics,
        identity_checksum=record.identity_checksum,
    )


def _uuid_value(value: str):
    from uuid import UUID

    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected canonical UUID, got {value!r}") from exc


def request_fingerprint(
    source: str,
    endpoint_key: str,
    request_shape: dict[str, Any],
) -> str:
    payload = {
        "source": source,
        "endpoint_key": endpoint_key,
        "request_shape": request_shape,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _checkpoint_document(checkpoint: HistoricalCheckpoint) -> dict[str, Any]:
    payload = _json_safe(asdict(checkpoint))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"checkpoint": payload, "checksum": hashlib.sha256(canonical).hexdigest()}


def _with_identity(checkpoint: HistoricalCheckpoint) -> HistoricalCheckpoint:
    if checkpoint.identity_checksum:
        return checkpoint
    payload = _json_safe({**asdict(checkpoint), "identity_checksum": ""})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return HistoricalCheckpoint(
        **{**asdict(checkpoint), "identity_checksum": hashlib.sha256(canonical).hexdigest()}
    )


def _checkpoint_from_document(payload: dict[str, Any]) -> HistoricalCheckpoint:
    return HistoricalCheckpoint(
        **{
            **payload,
            "requested_at": datetime.fromisoformat(payload["requested_at"]),
            "event_start_at": (
                datetime.fromisoformat(payload["event_start_at"])
                if payload.get("event_start_at")
                else None
            ),
            "event_end_at": (
                datetime.fromisoformat(payload["event_end_at"])
                if payload.get("event_end_at")
                else None
            ),
            "completed_at": (
                datetime.fromisoformat(payload["completed_at"])
                if payload.get("completed_at")
                else None
            ),
            "next_resume_at": (
                datetime.fromisoformat(payload["next_resume_at"])
                if payload.get("next_resume_at")
                else None
            ),
            "raw_references": tuple(payload.get("raw_references") or ()),
            "raw_checksums": tuple(payload.get("raw_checksums") or ()),
        }
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value
