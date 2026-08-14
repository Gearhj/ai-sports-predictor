"""Strictly bounded Baseball Savant detail-CSV trial for raw evidence only.

This module intentionally has no canonical persistence, feature, training, or
model dependencies. Successful evidence remains partially reconstructable
because its historical publication and eligibility times are not established.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol

from app.data_sources.base import AdapterResponse, SourceError
from app.data_sources.request_policy import (
    ConservativeRequestExecutor,
    ConservativeRequestPolicy,
    RequestAccounting,
)
from app.data_sources.statcast import (
    STATCAST_DETAIL_ENDPOINT,
    STATCAST_DETAIL_SCHEMA_VERSION,
    BaseballSavantStatcastAdapter,
    parse_statcast_detail_csv,
    statcast_detail_query,
)
from app.ingestion.archive import RawArchive, RawArchiveConflictError


SOURCE = BaseballSavantStatcastAdapter.source
RESEARCH_PROVENANCE = "HISTORICAL_RECONSTRUCTION"
TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"
EVIDENCE_CLASS = "PARTIALLY_RECONSTRUCTABLE"
USE_SCOPE = "RAW_ARCHIVE_ONLY"
OPERATION_NAME = "statcast_search_csv_detail"


class StatcastDetailAdapter(Protocol):
    source: str

    async def get_pitch_detail_for_date(self, target_date: date) -> AdapterResponse: ...


@dataclass(frozen=True)
class StatcastOneDayTrialOptions:
    target_date: date
    dry_run: bool = False
    maximum_physical_requests: int = 1
    concurrency: int = 1

    def __post_init__(self) -> None:
        if self.target_date.year < 2015:
            raise ValueError("Statcast trial target_date must be in 2015 or later")
        if self.maximum_physical_requests != 1:
            raise ValueError("Statcast one-day trial physical request cap must equal 1")
        if self.concurrency != 1:
            raise ValueError("Statcast one-day trial concurrency must equal 1")


@dataclass(frozen=True)
class StatcastOneDayTrialPlan:
    status: Literal["READY", "DRY_RUN"]
    start_date: date
    end_date: date
    date_count: int
    planned_physical_requests: int
    maximum_physical_requests: int
    concurrency: int
    source: str
    endpoint: str
    query: dict[str, str]
    execution_scope: str
    evidence_class: str
    research_provenance: str
    temporal_semantics: str
    publication_timing_proven: bool
    eligibility_timing_proven: bool
    canonicalization_enabled: bool
    training_enabled: bool
    mlb_requests: int
    statcast_requests: int
    odds_requests: int
    weather_requests: int
    adapter_constructed: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StatcastTrialEvidenceReport:
    raw_file_reference: str
    retrieved_at: datetime
    archived_at: datetime
    source_event_timestamp: None
    payload_checksum: str
    csv_body_checksum: str
    archive_file_checksum: str
    archive_bytes: int
    compressed: bool
    historical: bool
    idempotent_reuse: bool
    row_count: int
    unique_game_count: int
    unique_batter_count: int
    unique_pitcher_count: int
    unique_player_count: int
    schema_version: str
    schema_fingerprint: str
    observed_field_count: int
    observed_fields: list[str]
    schema_drift_detected: bool
    schema_drift_basis: str
    missing_required_fields: list[str]
    duplicate_headers: list[str]
    inconsistent_row_count: int
    research_provenance: str
    temporal_semantics: str
    evidence_class: str
    use_scope: str
    publication_timing_proven: bool
    eligibility_timing_proven: bool
    canonicalization_status: str
    training_eligible: bool


@dataclass(frozen=True)
class StatcastOneDayTrialResult:
    status: Literal["DRY_RUN", "ARCHIVED_RAW_ONLY", "BLOCKED"]
    plan: StatcastOneDayTrialPlan
    accounting: dict[str, Any]
    evidence: StatcastTrialEvidenceReport | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_statcast_one_day_trial(
    options: StatcastOneDayTrialOptions,
) -> StatcastOneDayTrialPlan:
    """Create a provider-free plan for exactly one historical date."""

    return StatcastOneDayTrialPlan(
        status="DRY_RUN" if options.dry_run else "READY",
        start_date=options.target_date,
        end_date=options.target_date,
        date_count=1,
        planned_physical_requests=1,
        maximum_physical_requests=options.maximum_physical_requests,
        concurrency=options.concurrency,
        source=SOURCE,
        endpoint=STATCAST_DETAIL_ENDPOINT,
        query=statcast_detail_query(options.target_date),
        execution_scope=USE_SCOPE,
        evidence_class=EVIDENCE_CLASS,
        research_provenance=RESEARCH_PROVENANCE,
        temporal_semantics=TEMPORAL_SEMANTICS,
        publication_timing_proven=False,
        eligibility_timing_proven=False,
        canonicalization_enabled=False,
        training_enabled=False,
        mlb_requests=0,
        statcast_requests=1,
        odds_requests=0,
        weather_requests=0,
        adapter_constructed=False,
    )


class StatcastOneDayTrialRunner:
    """Acquire, validate, and archive one exact date with one physical request."""

    def __init__(
        self,
        *,
        archive: RawArchive,
        adapter_factory: Callable[[], StatcastDetailAdapter] = BaseballSavantStatcastAdapter,
        request_policy: ConservativeRequestPolicy | None = None,
    ) -> None:
        self.archive = archive
        self.adapter_factory = adapter_factory
        self.request_policy = request_policy or ConservativeRequestPolicy(
            max_attempts=1,
            minimum_interval_seconds=1.0,
            base_backoff_seconds=1.0,
            maximum_backoff_seconds=1.0,
        )
        if self.request_policy.max_attempts != 1:
            raise ValueError(
                "Statcast one-day trial policy must allow exactly one physical attempt"
            )

    def plan(self, options: StatcastOneDayTrialOptions) -> StatcastOneDayTrialPlan:
        return plan_statcast_one_day_trial(options)

    async def execute(
        self,
        options: StatcastOneDayTrialOptions,
        *,
        adapter: StatcastDetailAdapter | None = None,
    ) -> StatcastOneDayTrialResult:
        plan = self.plan(options)
        if options.dry_run:
            return StatcastOneDayTrialResult(
                status="DRY_RUN",
                plan=plan,
                accounting=RequestAccounting(
                    maximum_requests=options.maximum_physical_requests
                ).as_dict(),
            )

        executor = ConservativeRequestExecutor(
            SOURCE,
            maximum_requests=options.maximum_physical_requests,
            policy=self.request_policy,
        )
        selected_adapter = adapter if adapter is not None else self.adapter_factory()

        async def acquire_and_validate() -> tuple[
            AdapterResponse,
            list[dict[str, str]],
            dict[str, Any],
        ]:
            response = await selected_adapter.get_pitch_detail_for_date(options.target_date)
            rows, schema = _validate_response(response, options.target_date)
            return response, rows, schema

        try:
            response, rows, schema = await executor.execute(
                acquire_and_validate,
                operation_name=OPERATION_NAME,
            )
        except SourceError as exc:
            return StatcastOneDayTrialResult(
                status="BLOCKED",
                plan=plan,
                accounting=executor.accounting.as_dict(),
                reason=str(exc),
            )

        raw_payload = response.raw_payload
        assert isinstance(raw_payload, dict)
        fingerprint = _request_fingerprint(options.target_date)
        try:
            archived = self.archive.write_historical(
                SOURCE,
                raw_payload,
                partition_date=options.target_date,
                request_fingerprint=fingerprint,
                source_event_timestamp=None,
                status="success",
                schema_version=STATCAST_DETAIL_SCHEMA_VERSION,
                source_mode="LIVE",
                research_provenance=RESEARCH_PROVENANCE,
                temporal_semantics=TEMPORAL_SEMANTICS,
                evidence_class=EVIDENCE_CLASS,
                checkpoint_id=f"statcast-one-day-trial:{options.target_date.isoformat()}",
            )
        except (OSError, RawArchiveConflictError, ValueError) as exc:
            return StatcastOneDayTrialResult(
                status="BLOCKED",
                plan=plan,
                accounting=executor.accounting.as_dict(),
                reason=f"Statcast raw archive failed closed: {type(exc).__name__}: {exc}",
            )

        body_bytes = _raw_body_bytes(raw_payload)
        games = {row["game_pk"].strip() for row in rows}
        batters = {row["batter"].strip() for row in rows}
        pitchers = {row["pitcher"].strip() for row in rows}
        report = StatcastTrialEvidenceReport(
            raw_file_reference=archived.raw_file_reference,
            retrieved_at=response.envelope.fetched_at,
            archived_at=archived.ingestion_timestamp,
            source_event_timestamp=None,
            payload_checksum=archived.payload_checksum,
            csv_body_checksum=hashlib.sha256(body_bytes).hexdigest(),
            archive_file_checksum=_required(archived.file_checksum, "archive file checksum"),
            archive_bytes=int(_required(archived.bytes_written, "archive byte count")),
            compressed=archived.compressed,
            historical=archived.historical,
            idempotent_reuse=archived.idempotent_reuse,
            row_count=len(rows),
            unique_game_count=len(games),
            unique_batter_count=len(batters),
            unique_pitcher_count=len(pitchers),
            unique_player_count=len(batters | pitchers),
            schema_version=STATCAST_DETAIL_SCHEMA_VERSION,
            schema_fingerprint=str(schema["schema_fingerprint"]),
            observed_field_count=int(schema["observed_field_count"]),
            observed_fields=list(schema["observed_fields"]),
            schema_drift_detected=bool(schema["schema_drift_detected"]),
            schema_drift_basis=str(schema["schema_drift_basis"]),
            missing_required_fields=list(schema["missing_required_fields"]),
            duplicate_headers=list(schema["duplicate_headers"]),
            inconsistent_row_count=int(schema["inconsistent_row_count"]),
            research_provenance=RESEARCH_PROVENANCE,
            temporal_semantics=TEMPORAL_SEMANTICS,
            evidence_class=EVIDENCE_CLASS,
            use_scope=USE_SCOPE,
            publication_timing_proven=False,
            eligibility_timing_proven=False,
            canonicalization_status="NOT_ATTEMPTED",
            training_eligible=False,
        )
        return StatcastOneDayTrialResult(
            status="ARCHIVED_RAW_ONLY",
            plan=plan,
            accounting=executor.accounting.as_dict(),
            evidence=report,
        )


async def execute_statcast_one_day_trial(
    options: StatcastOneDayTrialOptions,
    *,
    archive: RawArchive,
    adapter: StatcastDetailAdapter | None = None,
    adapter_factory: Callable[[], StatcastDetailAdapter] = BaseballSavantStatcastAdapter,
    request_policy: ConservativeRequestPolicy | None = None,
) -> StatcastOneDayTrialResult:
    """Convenience callable with either an injected adapter or the default adapter."""

    runner = StatcastOneDayTrialRunner(
        archive=archive,
        adapter_factory=adapter_factory,
        request_policy=request_policy,
    )
    return await runner.execute(options, adapter=adapter)


def _validate_response(
    response: AdapterResponse,
    target_date: date,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    envelope = response.envelope
    if envelope.source != SOURCE:
        raise SourceError(SOURCE, "Statcast trial source does not match", retryable=False)
    if envelope.source_mode != "LIVE":
        raise SourceError(SOURCE, "Statcast trial source_mode must be LIVE", retryable=False)
    if envelope.source_event_time is not None:
        raise SourceError(
            SOURCE,
            "Statcast trial must not invent a source event timestamp",
            retryable=False,
        )
    if envelope.fetched_at.tzinfo is None or envelope.fetched_at.utcoffset() is None:
        raise SourceError(
            SOURCE,
            "Statcast retrieval timestamp must be timezone-aware",
            retryable=False,
        )
    retrieval_delta = datetime.now(UTC) - envelope.fetched_at.astimezone(UTC)
    if abs(retrieval_delta.total_seconds()) > 300:
        raise SourceError(
            SOURCE,
            "Statcast retrieval timestamp is not current",
            retryable=False,
        )

    expected_metadata: dict[str, Any] = {
        "target_date": target_date.isoformat(),
        "research_provenance": RESEARCH_PROVENANCE,
        "temporal_semantics": TEMPORAL_SEMANTICS,
        "evidence_class": EVIDENCE_CLASS,
        "publication_timing_proven": False,
        "eligibility_timing_proven": False,
        "use_scope": USE_SCOPE,
        "canonicalization_eligible": False,
        "training_eligible": False,
    }
    mismatches = {
        key: envelope.metadata.get(key)
        for key, expected in expected_metadata.items()
        if envelope.metadata.get(key) != expected
    }
    if mismatches:
        raise SourceError(
            SOURCE,
            f"Statcast trial provenance/envelope validation failed: {sorted(mismatches)}",
            retryable=False,
        )
    if not isinstance(response.raw_payload, dict):
        raise SourceError(
            SOURCE,
            "Statcast trial raw payload must be a CSV wrapper",
            retryable=False,
        )
    raw = response.raw_payload
    expected_raw: dict[str, Any] = {
        "format": "CSV",
        "requested_date": target_date.isoformat(),
        "query": statcast_detail_query(target_date),
    }
    raw_mismatches = {
        key: raw.get(key)
        for key, expected in expected_raw.items()
        if raw.get(key) != expected
    }
    if raw_mismatches or not isinstance(raw.get("body"), str):
        raise SourceError(
            SOURCE,
            "Statcast trial raw request/body contract validation failed",
            retryable=False,
        )
    body_bytes = _raw_body_bytes(raw)
    try:
        decoded_body = body_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceError(
            SOURCE,
            "Statcast trial exact raw body is not valid UTF-8",
            retryable=False,
        ) from exc
    if decoded_body != raw["body"]:
        raise SourceError(
            SOURCE,
            "Statcast trial decoded CSV does not match its exact raw body",
            retryable=False,
        )
    content_type = raw.get("content_type")
    if not isinstance(content_type, str):
        raise SourceError(SOURCE, "Statcast trial content type is invalid", retryable=False)
    rows, schema = parse_statcast_detail_csv(
        raw["body"],
        content_type=content_type,
        target_date=target_date,
    )
    if not isinstance(envelope.data, list) or envelope.data != rows:
        raise SourceError(
            SOURCE,
            "Statcast trial parsed rows do not match the raw CSV",
            retryable=False,
        )
    if envelope.metadata.get("row_count") != len(rows):
        raise SourceError(SOURCE, "Statcast trial row count is inconsistent", retryable=False)
    if envelope.metadata.get("schema") != schema:
        raise SourceError(SOURCE, "Statcast trial schema report is inconsistent", retryable=False)
    return rows, schema


def _request_fingerprint(target_date: date) -> str:
    identity = {
        "endpoint": STATCAST_DETAIL_ENDPOINT,
        "query": statcast_detail_query(target_date),
        "scope": USE_SCOPE,
        "schema_version": STATCAST_DETAIL_SCHEMA_VERSION,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _raw_body_bytes(raw: dict[str, Any]) -> bytes:
    if raw.get("body_encoding") != "BASE64_EXACT_RESPONSE_BYTES":
        raise SourceError(SOURCE, "Statcast trial raw body encoding is invalid", retryable=False)
    encoded = raw.get("body_base64")
    if not isinstance(encoded, str):
        raise SourceError(SOURCE, "Statcast trial exact raw body is absent", retryable=False)
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SourceError(
            SOURCE,
            "Statcast trial exact raw body is invalid",
            retryable=False,
        ) from exc


def _required(value: Any | None, description: str) -> Any:
    if value is None:
        raise RuntimeError(f"Historical archive did not return {description}")
    return value


__all__ = [
    "StatcastOneDayTrialOptions",
    "StatcastOneDayTrialPlan",
    "StatcastOneDayTrialResult",
    "StatcastOneDayTrialRunner",
    "StatcastTrialEvidenceReport",
    "execute_statcast_one_day_trial",
    "plan_statcast_one_day_trial",
]
