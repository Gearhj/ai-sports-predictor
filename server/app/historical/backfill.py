from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from app.data_sources.base import AdapterResponse, SourceError
from app.data_sources.mlb import MLBStatsAPIAdapter
from app.data_sources.request_policy import (
    ConservativeRequestExecutor,
    ConservativeRequestPolicy,
    RequestAccounting,
)
from app.historical.checkpoints import (
    CheckpointRepository,
    HistoricalCheckpoint,
    JsonCheckpointRepository,
    request_fingerprint,
)
from app.ingestion.archive import RawArchive, RawArchiveRecord


SourceKey = Literal["MLB_STATS_API"]
TermsStatus = Literal["APPROVED", "LEGAL_REVIEW_REQUIRED", "REJECTED"]


class HistoricalMLBAdapter(Protocol):
    source: str

    async def get_historical_schedule(self, event_date: date) -> AdapterResponse: ...


@dataclass(frozen=True)
class HistoricalBackfillOptions:
    start_date: date
    end_date: date
    season: int
    resume: bool = False
    source: SourceKey = "MLB_STATS_API"
    chunk_size: int = 1
    dry_run: bool = False
    max_requests: int = 0
    skip_statcast: bool = True
    skip_weather: bool = True
    terms_status: TermsStatus = "LEGAL_REVIEW_REQUIRED"
    minimum_free_bytes: int = 100_000_000_000

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.start_date.year != self.season or self.end_date.year != self.season:
            raise ValueError("start/end dates must belong to --season")
        if self.chunk_size < 1 or self.chunk_size > 31:
            raise ValueError("chunk_size must be between 1 and 31 days")
        if self.max_requests < 0:
            raise ValueError("max_requests must be non-negative")
        if self.source != "MLB_STATS_API":
            raise ValueError("Only MLB_STATS_API is implemented; Odds is never a backfill source")
        if not self.skip_statcast or not self.skip_weather:
            raise ValueError("Gate 3A A0/A1 requires --skip-statcast and --skip-weather")


@dataclass(frozen=True)
class HistoricalBackfillPlan:
    status: Literal["DRY_RUN", "BLOCKED", "READY"]
    reason: str | None
    source: str
    season: int
    start_date: str
    end_date: str
    chunk_size: int
    chunks: int
    planned_requests: int
    maximum_requests: int
    resume: bool
    terms_status: TermsStatus
    statcast_requests: int = 0
    weather_requests: int = 0
    odds_requests: int = 0
    adapter_constructed: bool = False
    execution_scope: Literal["RAW_ARCHIVE_ONLY"] = "RAW_ARCHIVE_ONLY"
    canonical_persistence_enabled: bool = False


@dataclass(frozen=True)
class HistoricalBackfillResult:
    status: Literal["ARCHIVED_CANONICAL_PENDING", "PARTIAL", "BLOCKED", "DRY_RUN"]
    reason: str | None
    plan: HistoricalBackfillPlan
    accounting: dict[str, int | float]
    chunks_completed: int
    chunks_skipped: int
    records_received: int
    raw_bytes: int
    checkpoints_written: int
    run_id: str
    completion_scope: Literal["RAW_ARCHIVE_ONLY"] = "RAW_ARCHIVE_ONLY"
    canonical_records_accepted: int = 0


class HistoricalBackfillRunner:
    """Provider-specific, resumable MLB schedule archive runner.

    Dry run, terms, request-cap, optional-source, and disk checks execute before
    adapter construction. The runner has no Odds import or dispatch path.
    """

    # This stream is raw acquisition only. A SUCCEEDED terminal checkpoint means
    # the archive transaction succeeded; it is not a canonical-ingestion claim.
    STREAM_KEY = "mlb_regular_season_schedule_archive"
    SOURCE_VERSION_ID = "mlb_stats_api:historical-schedule-v1"

    def __init__(
        self,
        *,
        archive: RawArchive,
        checkpoints: CheckpointRepository,
        adapter_factory: Callable[[ConservativeRequestExecutor], HistoricalMLBAdapter] | None = None,
        request_policy: ConservativeRequestPolicy | None = None,
        free_space: Callable[[Path], int] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.archive = archive
        self.checkpoints = checkpoints
        self.adapter_factory = adapter_factory or (
            lambda executor: MLBStatsAPIAdapter(request_executor=executor)
        )
        self.request_policy = request_policy or ConservativeRequestPolicy()
        self.free_space = free_space or _free_space
        self.now = now

    def preflight(self, options: HistoricalBackfillOptions) -> HistoricalBackfillPlan:
        chunks = _chunks(options.start_date, options.end_date, options.chunk_size)
        # One schedule request plus at most one optional pitcher-hands lookup
        # per date. Chunking is a resume boundary, not a provider batch claim.
        planned_requests = 2 * ((options.end_date - options.start_date).days + 1)
        reason: str | None = None
        status: Literal["DRY_RUN", "BLOCKED", "READY"] = "READY"
        if options.dry_run:
            status = "DRY_RUN"
            reason = "Dry run validated the plan; no adapter was constructed and no provider was called."
        elif options.terms_status != "APPROVED":
            status = "BLOCKED"
            reason = f"MLB source terms status is {options.terms_status}; acquisition is blocked."
        elif options.max_requests == 0:
            status = "BLOCKED"
            reason = "The authorized request cap is zero."
        elif options.max_requests < planned_requests:
            status = "BLOCKED"
            reason = (
                f"Request cap {options.max_requests} is below the {planned_requests} planned requests; "
                "narrow the range rather than partially exceeding authorization."
            )
        elif self.free_space(self.archive.root) < options.minimum_free_bytes:
            status = "BLOCKED"
            reason = "Free disk space is below the 100 GB safety reserve."
        return HistoricalBackfillPlan(
            status=status,
            reason=reason,
            source=options.source,
            season=options.season,
            start_date=options.start_date.isoformat(),
            end_date=options.end_date.isoformat(),
            chunk_size=options.chunk_size,
            chunks=len(chunks),
            planned_requests=planned_requests,
            maximum_requests=options.max_requests,
            resume=options.resume,
            terms_status=options.terms_status,
        )

    async def run(self, options: HistoricalBackfillOptions) -> HistoricalBackfillResult:
        plan = self.preflight(options)
        run_id = uuid4().hex
        if plan.status != "READY":
            return HistoricalBackfillResult(
                status="DRY_RUN" if plan.status == "DRY_RUN" else "BLOCKED",
                reason=plan.reason,
                plan=plan,
                accounting=RequestAccounting(options.max_requests).as_dict(),
                chunks_completed=0,
                chunks_skipped=0,
                records_received=0,
                raw_bytes=0,
                checkpoints_written=0,
                run_id=run_id,
            )

        executor = ConservativeRequestExecutor(
            options.source,
            maximum_requests=options.max_requests,
            policy=self.request_policy,
        )
        adapter = self.adapter_factory(executor)
        plan = HistoricalBackfillPlan(**{**asdict(plan), "adapter_constructed": True})
        completed = skipped = received = bytes_written = checkpoint_count = 0
        for sequence, (chunk_start, chunk_end) in enumerate(
            _chunks(options.start_date, options.end_date, options.chunk_size)
        ):
            shape = {
                "season": options.season,
                "start_date": chunk_start.isoformat(),
                "end_date": chunk_end.isoformat(),
                "game_type": "R",
                "policy_version": "gate3a-backfill-v1",
            }
            fingerprint = request_fingerprint(options.source, self.STREAM_KEY, shape)
            previous = self.checkpoints.completed(
                self.SOURCE_VERSION_ID,
                self.STREAM_KEY,
                fingerprint,
            )
            if previous is not None:
                if not options.resume:
                    reason = "A completed semantic chunk exists; rerun with --resume to verify/no-op."
                    return HistoricalBackfillResult(
                        status="BLOCKED",
                        reason=reason,
                        plan=plan,
                        accounting=executor.accounting.as_dict(),
                        chunks_completed=completed,
                        chunks_skipped=skipped,
                        records_received=received,
                        raw_bytes=bytes_written,
                        checkpoints_written=checkpoint_count,
                        run_id=run_id,
                    )
                skipped += 1
                continue

            requested_at = self.now()
            attempts_before = executor.accounting.attempts
            retries_before = executor.accounting.retries
            try:
                responses: list[AdapterResponse] = []
                cursor = chunk_start
                while cursor <= chunk_end:
                    responses.append(await adapter.get_historical_schedule(cursor))
                    cursor += timedelta(days=1)
                # chunk_size > 1 requires one request per date in the current
                # adapter. The exact preflight budget reflects those calls.
                raw_records: list[RawArchiveRecord] = []
                chunk_records = 0
                for cursor, response in zip(_dates(chunk_start, chunk_end), responses, strict=True):
                    _validate_schedule_response(response, expected_source=options.source)
                    day_fingerprint = request_fingerprint(
                        options.source,
                        "mlb_historical_schedule_date",
                        {**shape, "event_date": cursor.isoformat()},
                    )
                    archive = self.archive.write_historical(
                        response.envelope.source,
                        response.raw_payload,
                        partition_date=cursor,
                        request_fingerprint=day_fingerprint,
                        source_event_timestamp=datetime.combine(cursor, time.min, tzinfo=UTC),
                        schema_version="mlb_statsapi_historical_schedule_v1",
                        source_mode="LIVE",
                        research_provenance="HISTORICAL_RECONSTRUCTION",
                        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                        evidence_class="RECONSTRUCTABLE_POINT_IN_TIME",
                        checkpoint_id=fingerprint,
                        run_id=run_id,
                    )
                    raw_records.append(archive)
                    chunk_records += len(response.envelope.data) if isinstance(response.envelope.data, list) else 1
                completed_at = self.now()
                checkpoint = HistoricalCheckpoint(
                    source_version_id=self.SOURCE_VERSION_ID,
                    stream_key=self.STREAM_KEY,
                    query_fingerprint=fingerprint,
                    status="SUCCEEDED",
                    requested_at=requested_at,
                    completed_at=completed_at,
                    season=options.season,
                    event_start_at=datetime.combine(chunk_start, time.min, tzinfo=UTC),
                    event_end_at=datetime.combine(chunk_end, time.max, tzinfo=UTC),
                    sequence=sequence,
                    ingestion_run_id=run_id,
                    cursor_before={"date": chunk_start.isoformat()},
                    cursor_after={"date": (chunk_end + timedelta(days=1)).isoformat()},
                    attempt_count=executor.accounting.attempts - attempts_before,
                    retry_count=executor.accounting.retries - retries_before,
                    records_received=chunk_records,
                    records_accepted=0,
                    records_rejected=0,
                    duplicate_count=0,
                    raw_references=tuple(item.raw_file_reference for item in raw_records),
                    # ORM field name is retained for compatibility; these are
                    # exact archive-file checksums, not provider-payload hashes.
                    raw_checksums=tuple(_required_file_checksum(item) for item in raw_records),
                    next_resume_at=datetime.combine(chunk_end + timedelta(days=1), time.min, tzinfo=UTC),
                    quality_summary={
                        "archive_only": True,
                        "archive_status": "SUCCEEDED",
                        "canonical_status": "PENDING",
                        "normalization_status": "PENDING_ORM_REPOSITORY",
                        "source_mode": "LIVE",
                        "research_provenance": "HISTORICAL_RECONSTRUCTION",
                        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                        "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
                        "checksum_semantics": {
                            "raw_checksums": "SHA256_ARCHIVE_FILE_BYTES",
                            "payload_checksums": [item.payload_checksum for item in raw_records],
                        },
                    },
                )
                self.checkpoints.append(checkpoint)
                checkpoint_count += 1
                completed += 1
                received += chunk_records
                bytes_written += sum(item.bytes_written or 0 for item in raw_records)
            except Exception as exc:
                raw_records = []
                raw_payload = getattr(exc, "raw_payload", None)
                if isinstance(raw_payload, (dict, list)):
                    raw_records.append(self.archive.write_historical(
                        options.source,
                        raw_payload,
                        partition_date=chunk_start,
                        request_fingerprint=f"{fingerprint}:schema-failure",
                        status="schema_failure",
                        schema_version="mlb_statsapi_historical_unvalidated_v1",
                        source_mode="LIVE",
                        research_provenance="HISTORICAL_RECONSTRUCTION",
                        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                        evidence_class="PARTIALLY_RECONSTRUCTABLE",
                        checkpoint_id=fingerprint,
                        run_id=run_id,
                    ))
                archive_status = (
                    "SUCCEEDED_UNVALIDATED_ERROR_PAYLOAD" if raw_records else "NOT_WRITTEN"
                )
                completed_at = self.now()
                checkpoint = HistoricalCheckpoint(
                    source_version_id=self.SOURCE_VERSION_ID,
                    stream_key=self.STREAM_KEY,
                    query_fingerprint=fingerprint,
                    status="FAILED",
                    requested_at=requested_at,
                    completed_at=completed_at,
                    season=options.season,
                    event_start_at=datetime.combine(chunk_start, time.min, tzinfo=UTC),
                    event_end_at=datetime.combine(chunk_end, time.max, tzinfo=UTC),
                    sequence=sequence,
                    ingestion_run_id=run_id,
                    attempt_count=executor.accounting.attempts - attempts_before,
                    retry_count=executor.accounting.retries - retries_before,
                    records_received=0,
                    raw_references=tuple(item.raw_file_reference for item in raw_records),
                    raw_checksums=tuple(_required_file_checksum(item) for item in raw_records),
                    next_resume_at=datetime.combine(chunk_start, time.min, tzinfo=UTC),
                    error_type=type(exc).__name__,
                    error_message=_safe_error(exc),
                    quality_summary={
                        "archive_only": True,
                        "archive_status": archive_status,
                        "canonical_status": "NOT_ATTEMPTED",
                        "normalization_status": "FAILED",
                        "source_mode": "LIVE",
                        "research_provenance": "HISTORICAL_RECONSTRUCTION",
                        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                        "evidence_class": "PARTIALLY_RECONSTRUCTABLE",
                        "checksum_semantics": {
                            "raw_checksums": "SHA256_ARCHIVE_FILE_BYTES",
                            "payload_checksums": [item.payload_checksum for item in raw_records],
                        },
                    },
                )
                self.checkpoints.append(checkpoint)
                checkpoint_count += 1
                bytes_written += sum(item.bytes_written or 0 for item in raw_records)
                return HistoricalBackfillResult(
                    status="PARTIAL" if completed else "BLOCKED",
                    reason=_safe_error(exc),
                    plan=plan,
                    accounting=executor.accounting.as_dict(),
                    chunks_completed=completed,
                    chunks_skipped=skipped,
                    records_received=received,
                    raw_bytes=bytes_written,
                    checkpoints_written=checkpoint_count,
                    run_id=run_id,
                )
        return HistoricalBackfillResult(
            status="ARCHIVED_CANONICAL_PENDING",
            reason=(
                "Raw MLB schedule evidence is archived and resumable; canonical normalization, "
                "final/boxscore persistence, materialization, and training have not run."
            ),
            plan=plan,
            accounting=executor.accounting.as_dict(),
            chunks_completed=completed,
            chunks_skipped=skipped,
            records_received=received,
            raw_bytes=bytes_written,
            checkpoints_written=checkpoint_count,
            run_id=run_id,
        )


def build_default_backfill_runner(raw_data_root: Path) -> HistoricalBackfillRunner:
    archive = RawArchive(raw_data_root)
    repository = JsonCheckpointRepository(
        raw_data_root / "historical" / "_checkpoints" / "mlb_history.jsonl"
    )
    return HistoricalBackfillRunner(archive=archive, checkpoints=repository)


def _chunks(start: date, end: date, chunk_size: int) -> list[tuple[date, date]]:
    result: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=chunk_size - 1))
        result.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return result


def _dates(start: date, end: date) -> Sequence[date]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _free_space(path: Path) -> int:
    path = Path(path)
    existing = path
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    return shutil.disk_usage(existing).free


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, SourceError):
        return str(exc)[:500]
    return f"Historical backfill failed: {type(exc).__name__}"


def _validate_schedule_response(response: AdapterResponse, *, expected_source: str) -> None:
    """Fail closed before archive metadata is asserted on an adapter payload."""

    envelope = response.envelope
    if envelope.source != expected_source:
        raise SourceError(
            expected_source,
            "Historical schedule response source does not match the authorized source",
            retryable=False,
        )
    if envelope.source_mode != "LIVE":
        raise SourceError(
            expected_source,
            "Historical schedule response source_mode must be LIVE",
            retryable=False,
        )
    expected_metadata = {
        "research_provenance": "HISTORICAL_RECONSTRUCTION",
        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
        "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
    }
    for key, expected in expected_metadata.items():
        if envelope.metadata.get(key) != expected:
            raise SourceError(
                expected_source,
                f"Historical schedule response {key} is missing or invalid",
                retryable=False,
            )


def _required_file_checksum(record: RawArchiveRecord) -> str:
    if record.file_checksum is None:
        raise RuntimeError("Historical archive record lacks an archive-file checksum")
    return record.file_checksum
