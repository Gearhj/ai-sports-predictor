from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.data_sources.base import AdapterResponse, SourceEnvelope
from app.data_sources.request_policy import ConservativeRequestPolicy
from app.data_sources.statcast import (
    STATCAST_DETAIL_ENDPOINT,
    BaseballSavantStatcastAdapter,
)
from app.historical.statcast_trial import (
    StatcastOneDayTrialOptions,
    StatcastOneDayTrialRunner,
    execute_statcast_one_day_trial,
    plan_statcast_one_day_trial,
)
from app.ingestion import RawArchive


TARGET_DATE = date(2025, 6, 17)
VALID_CSV = (
    "game_date,game_pk,batter,pitcher,launch_speed\n"
    "2025-06-17,777001,101,201,98.1\n"
    "2025-06-17,777001,102,201,94.2\n"
    "2025-06-17,777002,101,202,101.0\n"
)


def _transport(
    body: str,
    *,
    status_code: int = 200,
    content_type: str = "text/csv; charset=utf-8",
    requests: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(
            status_code,
            text=body,
            headers={"content-type": content_type},
        )

    return httpx.MockTransport(handler)


def test_plan_is_provider_free_one_date_one_request_and_raw_only() -> None:
    options = StatcastOneDayTrialOptions(target_date=TARGET_DATE, dry_run=True)

    plan = plan_statcast_one_day_trial(options)

    assert plan.status == "DRY_RUN"
    assert plan.start_date == plan.end_date == TARGET_DATE
    assert plan.date_count == 1
    assert plan.planned_physical_requests == plan.maximum_physical_requests == 1
    assert plan.concurrency == 1
    assert plan.endpoint == STATCAST_DETAIL_ENDPOINT
    assert plan.query | {
        "type": "details",
        "game_date_gt": "2025-06-17",
        "game_date_lt": "2025-06-17",
    } == plan.query
    assert "leaderboard" not in plan.endpoint
    assert plan.execution_scope == "RAW_ARCHIVE_ONLY"
    assert plan.evidence_class == "PARTIALLY_RECONSTRUCTABLE"
    assert plan.research_provenance == "HISTORICAL_RECONSTRUCTION"
    assert plan.temporal_semantics == "RETROSPECTIVE_RECONSTRUCTION"
    assert plan.publication_timing_proven is False
    assert plan.eligibility_timing_proven is False
    assert plan.canonicalization_enabled is plan.training_enabled is False
    assert plan.statcast_requests == 1
    assert plan.mlb_requests == plan.odds_requests == plan.weather_requests == 0
    assert plan.adapter_constructed is False


@pytest.mark.asyncio
async def test_dry_run_does_not_construct_adapter_or_write_archive(artifact_root: Path) -> None:
    def forbidden_factory() -> Any:
        raise AssertionError("dry-run must not construct a provider adapter")

    runner = StatcastOneDayTrialRunner(
        archive=RawArchive(artifact_root / "raw"),
        adapter_factory=forbidden_factory,
    )

    result = await runner.execute(
        StatcastOneDayTrialOptions(target_date=TARGET_DATE, dry_run=True)
    )

    assert result.status == "DRY_RUN"
    assert result.accounting["attempts"] == 0
    assert result.accounting["successes"] == 0
    assert result.evidence is None
    assert not list((artifact_root / "raw").rglob("*.json.gz"))


@pytest.mark.asyncio
async def test_adapter_uses_exact_date_detail_csv_contract_without_redirects() -> None:
    requests: list[httpx.Request] = []
    transport = _transport(VALID_CSV, requests=requests)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await BaseballSavantStatcastAdapter(
            client=client
        ).get_pitch_detail_for_date(TARGET_DATE)

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url.copy_with(query=None)) == STATCAST_DETAIL_ENDPOINT
    assert request.url.params["type"] == "details"
    assert request.url.params["game_date_gt"] == request.url.params["game_date_lt"]
    assert request.url.params["game_date_gt"] == TARGET_DATE.isoformat()
    assert "year" not in request.url.params
    assert response.envelope.source_event_time is None
    assert response.envelope.metadata | {
        "research_provenance": "HISTORICAL_RECONSTRUCTION",
        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
        "evidence_class": "PARTIALLY_RECONSTRUCTABLE",
        "publication_timing_proven": False,
        "eligibility_timing_proven": False,
        "use_scope": "RAW_ARCHIVE_ONLY",
        "canonicalization_eligible": False,
        "training_eligible": False,
    } == response.envelope.metadata
    assert response.raw_payload["body"] == VALID_CSV
    assert response.raw_payload["body_encoding"] == "BASE64_EXACT_RESPONSE_BYTES"
    assert response.envelope.metadata["schema"]["schema_drift_detected"] is False


@pytest.mark.asyncio
async def test_execute_archives_validated_raw_csv_and_reports_integrity(
    artifact_root: Path,
) -> None:
    requests: list[httpx.Request] = []
    async with httpx.AsyncClient(
        transport=_transport(VALID_CSV, requests=requests)
    ) as client:
        result = await execute_statcast_one_day_trial(
            StatcastOneDayTrialOptions(target_date=TARGET_DATE),
            archive=RawArchive(artifact_root / "raw"),
            adapter=BaseballSavantStatcastAdapter(client=client),
            request_policy=ConservativeRequestPolicy(
                max_attempts=1,
                minimum_interval_seconds=0,
                base_backoff_seconds=0,
                maximum_backoff_seconds=0,
            ),
        )

    assert len(requests) == 1
    assert result.status == "ARCHIVED_RAW_ONLY"
    assert result.reason is None
    assert result.accounting | {
        "maximum_requests": 1,
        "attempts": 1,
        "successes": 1,
        "failed_attempts": 0,
        "retries": 0,
        "rate_limits": 0,
        "provider_rejections": 0,
    } == result.accounting
    assert result.accounting["attempts_by_operation"] == {
        "statcast_search_csv_detail": 1
    }

    evidence = result.evidence
    assert evidence is not None
    assert evidence.source_event_timestamp is None
    assert evidence.retrieved_at.tzinfo is not None
    assert evidence.archived_at.tzinfo is not None
    assert evidence.row_count == 3
    assert evidence.unique_game_count == 2
    assert evidence.unique_batter_count == 2
    assert evidence.unique_pitcher_count == 2
    assert evidence.unique_player_count == 4
    assert evidence.compressed is evidence.historical is True
    assert evidence.schema_drift_detected is False
    assert evidence.schema_drift_basis == "REQUIRED_FIELDS_AND_RECTANGULAR_ROWS_ONLY"
    assert evidence.missing_required_fields == []
    assert evidence.duplicate_headers == []
    assert evidence.inconsistent_row_count == 0
    assert evidence.evidence_class == "PARTIALLY_RECONSTRUCTABLE"
    assert evidence.use_scope == "RAW_ARCHIVE_ONLY"
    assert evidence.canonicalization_status == "NOT_ATTEMPTED"
    assert evidence.training_eligible is False
    assert evidence.publication_timing_proven is False
    assert evidence.eligibility_timing_proven is False
    assert evidence.csv_body_checksum == hashlib.sha256(
        VALID_CSV.encode("utf-8")
    ).hexdigest()

    archive_path = Path(evidence.raw_file_reference)
    assert archive_path.suffix == ".gz"
    assert archive_path.stat().st_size == evidence.archive_bytes
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == (
        evidence.archive_file_checksum
    )
    with gzip.open(archive_path, "rt", encoding="utf-8") as stream:
        document = json.load(stream)
    assert document["metadata"] | {
        "source_event_timestamp": None,
        "historical": True,
        "research_provenance": "HISTORICAL_RECONSTRUCTION",
        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
        "evidence_class": "PARTIALLY_RECONSTRUCTABLE",
    } == document["metadata"]
    assert document["payload"]["body"] == VALID_CSV
    assert document["metadata"]["checksum"] == evidence.payload_checksum


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "content_type", "reason"),
    [
        (
            VALID_CSV.replace("2025-06-17,777002", "2025-06-18,777002"),
            "text/csv",
            "outside the exact requested date",
        ),
        ("<html><body>challenge</body></html>", "text/html", "HTML, not CSV"),
        ("game_date,game_pk,batter\n2025-06-17,1,2\n", "text/csv", "schema"),
        (
            "game_date,game_pk,batter,pitcher\n",
            "text/csv",
            "no data rows",
        ),
        ("", "text/csv", "empty"),
    ],
)
async def test_invalid_provider_payload_fails_before_archive(
    artifact_root: Path,
    body: str,
    content_type: str,
    reason: str,
) -> None:
    requests: list[httpx.Request] = []
    async with httpx.AsyncClient(
        transport=_transport(body, content_type=content_type, requests=requests)
    ) as client:
        result = await execute_statcast_one_day_trial(
            StatcastOneDayTrialOptions(target_date=TARGET_DATE),
            archive=RawArchive(artifact_root / "raw"),
            adapter=BaseballSavantStatcastAdapter(client=client),
            request_policy=ConservativeRequestPolicy(
                max_attempts=1,
                minimum_interval_seconds=0,
                base_backoff_seconds=0,
                maximum_backoff_seconds=0,
            ),
        )

    assert len(requests) == 1
    assert result.status == "BLOCKED"
    assert reason in (result.reason or "")
    assert result.accounting["attempts"] == 1
    assert result.accounting["successes"] == 0
    assert result.accounting["failed_attempts"] == 1
    assert result.accounting["retries"] == 0
    assert result.evidence is None
    assert not list((artifact_root / "raw").rglob("*.json.gz"))


@pytest.mark.asyncio
async def test_retryable_failure_still_consumes_only_the_one_attempt_cap(
    artifact_root: Path,
) -> None:
    requests: list[httpx.Request] = []
    async with httpx.AsyncClient(
        transport=_transport("unavailable", status_code=503, requests=requests)
    ) as client:
        result = await execute_statcast_one_day_trial(
            StatcastOneDayTrialOptions(target_date=TARGET_DATE),
            archive=RawArchive(artifact_root / "raw"),
            adapter=BaseballSavantStatcastAdapter(client=client),
            request_policy=ConservativeRequestPolicy(
                max_attempts=1,
                minimum_interval_seconds=0,
                base_backoff_seconds=0,
                maximum_backoff_seconds=0,
            ),
        )

    assert len(requests) == 1
    assert result.status == "BLOCKED"
    assert result.accounting["attempts"] == 1
    assert result.accounting["failed_attempts"] == 1
    assert result.accounting["successes"] == 0
    assert result.accounting["retries"] == 0
    assert not list((artifact_root / "raw").rglob("*.json.gz"))


@pytest.mark.asyncio
async def test_redirect_is_not_followed_as_an_unaccounted_physical_request(
    artifact_root: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "https://baseballsavant.mlb.com/another-request"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as client:
        result = await execute_statcast_one_day_trial(
            StatcastOneDayTrialOptions(target_date=TARGET_DATE),
            archive=RawArchive(artifact_root / "raw"),
            adapter=BaseballSavantStatcastAdapter(client=client),
            request_policy=ConservativeRequestPolicy(
                max_attempts=1,
                minimum_interval_seconds=0,
                base_backoff_seconds=0,
                maximum_backoff_seconds=0,
            ),
        )

    assert len(requests) == 1
    assert result.status == "BLOCKED"
    assert result.accounting["attempts"] == result.accounting["failed_attempts"] == 1
    assert result.accounting["retries"] == 0
    assert not list((artifact_root / "raw").rglob("*.json.gz"))


@pytest.mark.asyncio
async def test_injected_envelope_mismatch_fails_before_archive(artifact_root: Path) -> None:
    class SpoofedAdapter:
        source = "BASEBALL_SAVANT_STATCAST"

        async def get_pitch_detail_for_date(self, target_date: date) -> AdapterResponse:
            return AdapterResponse(
                envelope=SourceEnvelope(
                    source="WRONG_SOURCE",
                    source_mode="LIVE",
                    fetched_at=datetime.now(UTC),
                    source_event_time=None,
                    data=[],
                    metadata={},
                ),
                raw_payload={
                    "format": "CSV",
                    "content_type": "text/csv",
                    "requested_date": target_date.isoformat(),
                    "query": {},
                    "body": VALID_CSV,
                },
            )

    result = await execute_statcast_one_day_trial(
        StatcastOneDayTrialOptions(target_date=TARGET_DATE),
        archive=RawArchive(artifact_root / "raw"),
        adapter=SpoofedAdapter(),
        request_policy=ConservativeRequestPolicy(
            max_attempts=1,
            minimum_interval_seconds=0,
            base_backoff_seconds=0,
            maximum_backoff_seconds=0,
        ),
    )

    assert result.status == "BLOCKED"
    assert "source does not match" in (result.reason or "")
    assert result.accounting["attempts"] == result.accounting["failed_attempts"] == 1
    assert result.accounting["successes"] == result.accounting["retries"] == 0
    assert not list((artifact_root / "raw").rglob("*.json.gz"))


def test_cap_and_concurrency_cannot_be_expanded(artifact_root: Path) -> None:
    with pytest.raises(ValueError, match="request cap must equal 1"):
        StatcastOneDayTrialOptions(
            target_date=TARGET_DATE,
            maximum_physical_requests=2,
        )
    with pytest.raises(ValueError, match="concurrency must equal 1"):
        StatcastOneDayTrialOptions(target_date=TARGET_DATE, concurrency=2)
    with pytest.raises(ValueError, match="exactly one physical attempt"):
        StatcastOneDayTrialRunner(
            archive=RawArchive(artifact_root / "raw"),
            request_policy=ConservativeRequestPolicy(max_attempts=2),
        )
