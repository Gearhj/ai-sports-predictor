from __future__ import annotations

import gzip
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import app.cli as cli_module
import app.historical.backfill as backfill_module
from app.data_sources import (
    ConservativeRequestExecutor,
    ConservativeRequestPolicy,
    MLBStatsAPIAdapter,
    RequestCapExceeded,
    SourceError,
)
from app.data_sources.base import AdapterResponse, SourceEnvelope
from app.historical.backfill import (
    HistoricalBackfillOptions,
    HistoricalBackfillRunner,
)
from app.historical.checkpoints import (
    JsonCheckpointRepository,
    MemoryCheckpointRepository,
)
from app.ingestion import RawArchive, RawArchiveConflictError


def _options(**changes: Any) -> HistoricalBackfillOptions:
    values: dict[str, Any] = {
        "start_date": date(2025, 4, 1),
        "end_date": date(2025, 4, 1),
        "season": 2025,
        "chunk_size": 1,
        "dry_run": False,
        "max_requests": 2,
        "skip_statcast": True,
        "skip_weather": True,
        "terms_status": "APPROVED",
    }
    return HistoricalBackfillOptions(**{**values, **changes})


def test_historical_archive_is_atomic_compressed_idempotent_and_partitioned(
    artifact_root: Path,
) -> None:
    archive = RawArchive(artifact_root / "raw")
    payload = {"dates": [{"games": [{"gamePk": 1}]}]}
    first = archive.write_historical(
        "MLB_STATS_API",
        payload,
        partition_date=date(2025, 4, 1),
        request_fingerprint="a" * 64,
        source_event_timestamp=datetime(2025, 4, 1, tzinfo=UTC),
        checkpoint_id="checkpoint-1",
        run_id="run-1",
    )
    second = archive.write_historical(
        "MLB_STATS_API",
        payload,
        partition_date=date(2025, 4, 1),
        request_fingerprint="a" * 64,
        source_event_timestamp=datetime(2025, 4, 1, tzinfo=UTC),
        checkpoint_id="checkpoint-1",
        run_id="run-1",
    )

    path = Path(first.raw_file_reference)
    assert path.parts[-5:] == (
        "historical",
        "mlb_stats_api",
        "2025",
        "04",
        "2025-04-01_aaaaaaaaaaaaaaaaaaaaaaaa.json.gz",
    )
    assert first.compressed is first.historical is True
    assert first.idempotent_reuse is False
    assert second.idempotent_reuse is True
    assert second.raw_file_reference == first.raw_file_reference
    assert len(first.file_checksum or "") == 64
    assert len(first.payload_checksum) == 64
    assert first.payload_checksum == first.checksum
    assert first.payload_checksum != first.file_checksum
    assert not list(path.parent.glob("*.tmp"))
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        document = json.load(stream)
    assert document["metadata"] | {
        "source_mode": "LIVE",
        "research_provenance": "HISTORICAL_RECONSTRUCTION",
        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
        "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
        "checkpoint_id": "checkpoint-1",
        "run_id": "run-1",
    } == document["metadata"]
    assert document["metadata"]["ingestion_timestamp"] != document["metadata"]["source_event_timestamp"]

    with pytest.raises(ValueError, match="provenance"):
        archive.write_historical(
            "MLB_STATS_API",
            payload,
            partition_date=date(2025, 4, 1),
            request_fingerprint="b" * 64,
            research_provenance="RETROSPECTIVE_RECONSTRUCTION",
        )
    with pytest.raises(ValueError, match="temporal semantics"):
        archive.write_historical(
            "MLB_STATS_API",
            payload,
            partition_date=date(2025, 4, 1),
            request_fingerprint="c" * 64,
            temporal_semantics="HISTORICAL_RECONSTRUCTION",
        )

    with pytest.raises(RawArchiveConflictError):
        archive.write_historical(
            "MLB_STATS_API",
            {"dates": []},
            partition_date=date(2025, 4, 1),
            request_fingerprint="a" * 64,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "expected_status"),
    [
        ({"dry_run": True, "max_requests": 0, "terms_status": "LEGAL_REVIEW_REQUIRED"}, "DRY_RUN"),
        ({"terms_status": "LEGAL_REVIEW_REQUIRED"}, "BLOCKED"),
        ({"max_requests": 0}, "BLOCKED"),
        ({"end_date": date(2025, 4, 2), "max_requests": 3}, "BLOCKED"),
    ],
)
async def test_preflight_blocks_before_adapter_construction(
    artifact_root: Path,
    change: dict[str, Any],
    expected_status: str,
) -> None:
    constructed = 0

    def factory(executor: ConservativeRequestExecutor):
        nonlocal constructed
        constructed += 1
        raise AssertionError("adapter must not be constructed")

    runner = HistoricalBackfillRunner(
        archive=RawArchive(artifact_root / "raw"),
        checkpoints=MemoryCheckpointRepository(),
        adapter_factory=factory,
        free_space=lambda path: 200_000_000_000,
    )
    result = await runner.run(_options(**change))

    assert result.status == expected_status
    assert result.accounting["attempts"] == 0
    assert result.plan.odds_requests == 0
    assert result.plan.statcast_requests == 0
    assert result.plan.weather_requests == 0
    assert constructed == 0


@pytest.mark.asyncio
async def test_request_executor_bounds_retry_backoff_and_counts_physical_attempts() -> None:
    calls = 0
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def operation() -> str:
        nonlocal calls
        calls += 1
        request = httpx.Request("GET", "https://provider.invalid/test")
        if calls < 3:
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("unavailable", request=request, response=response)
        return "ok"

    executor = ConservativeRequestExecutor(
        "MLB_STATS_API",
        maximum_requests=3,
        policy=ConservativeRequestPolicy(
            max_attempts=3,
            minimum_interval_seconds=0,
            base_backoff_seconds=0.25,
            maximum_backoff_seconds=1,
        ),
        sleep=sleep,
    )
    assert await executor.execute(operation) == "ok"
    assert executor.accounting.attempts == 3
    assert executor.accounting.retries == 2
    assert executor.accounting.successes == 1
    assert executor.accounting.failed_attempts == 2
    assert sleeps == [0.25, 0.5]

    exhausted = ConservativeRequestExecutor(
        "MLB_STATS_API",
        maximum_requests=0,
        policy=ConservativeRequestPolicy(minimum_interval_seconds=0),
    )
    with pytest.raises(RequestCapExceeded):
        await exhausted.execute(operation)
    assert exhausted.accounting.attempts == 0


@pytest.mark.asyncio
async def test_request_executor_honors_retry_after_and_opens_rejection_circuit() -> None:
    sleeps: list[float] = []
    calls = 0

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def rejected() -> None:
        nonlocal calls
        calls += 1
        request = httpx.Request("GET", "https://provider.invalid/test")
        response = httpx.Response(429, headers={"Retry-After": "2"}, request=request)
        raise httpx.HTTPStatusError("limited", request=request, response=response)

    executor = ConservativeRequestExecutor(
        "MLB_STATS_API",
        maximum_requests=3,
        policy=ConservativeRequestPolicy(
            max_attempts=3,
            minimum_interval_seconds=0,
            base_backoff_seconds=0,
            maximum_backoff_seconds=3,
            circuit_rejection_threshold=3,
        ),
        sleep=sleep,
    )
    with pytest.raises(SourceError, match="circuit is open"):
        await executor.execute(rejected)
    assert calls == 3
    assert executor.accounting.rate_limits == 3
    assert executor.accounting.circuit_trips == 1
    assert sleeps == [2.0, 2.0]


@pytest.mark.asyncio
async def test_historical_mlb_schedule_normalizes_final_without_backdating_acquisition() -> None:
    payload = {
        "dates": [{
            "date": "2025-04-01",
            "games": [{
                "gamePk": 777,
                "season": "2025",
                "gameType": "R",
                "gameDate": "2025-04-01T23:05:00Z",
                "status": {
                    "abstractGameState": "Final",
                    "detailedState": "Final",
                    "codedGameState": "F",
                },
                "venue": {"id": 1, "name": "Test Park"},
                "linescore": {"currentInning": 9},
                "teams": {
                    "away": {"team": {"id": 10, "name": "Away"}, "score": 2},
                    "home": {"team": {"id": 20, "name": "Home"}, "score": 4},
                },
            }],
        }],
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await MLBStatsAPIAdapter(client=client).get_historical_schedule(date(2025, 4, 1))

    row = response.envelope.data[0]
    assert row["research_provenance"] == "HISTORICAL_RECONSTRUCTION"
    assert row["game"]["source_mode"] == "LIVE"
    assert row["final"] | {
        "home_score": 4,
        "away_score": 2,
        "abstract_state": "Final",
        "event_completed_at": None,
    } == row["final"]
    assert datetime.fromisoformat(row["acquired_at"]) > datetime(2025, 4, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_historical_final_adapter_rejects_a_nonfinal_live_score() -> None:
    payload = {
        "gameData": {
            "status": {
                "abstractGameState": "Live",
                "detailedState": "In Progress",
            },
            "datetime": {"officialDate": "2025-04-01"},
        },
        "liveData": {
            "linescore": {
                "currentInning": 7,
                "teams": {
                    "away": {"runs": 2},
                    "home": {"runs": 4},
                },
            }
        },
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SourceError, match="schema validation failed"):
            await MLBStatsAPIAdapter(client=client).get_historical_final("777")


@pytest.mark.asyncio
async def test_schema_failure_archives_unvalidated_payload_and_writes_terminal_checkpoint(
    artifact_root: Path,
) -> None:
    payload = {"dates": [{"date": "2025-04-01", "games": [{"season": "2025"}]}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    clients: list[httpx.AsyncClient] = []

    def factory(executor: ConservativeRequestExecutor) -> MLBStatsAPIAdapter:
        client = httpx.AsyncClient(transport=transport)
        clients.append(client)
        return MLBStatsAPIAdapter(client=client, request_executor=executor)

    checkpoints = MemoryCheckpointRepository()
    runner = HistoricalBackfillRunner(
        archive=RawArchive(artifact_root / "raw"),
        checkpoints=checkpoints,
        adapter_factory=factory,
        request_policy=ConservativeRequestPolicy(max_attempts=1, minimum_interval_seconds=0),
        free_space=lambda path: 200_000_000_000,
    )
    try:
        result = await runner.run(_options())
    finally:
        for client in clients:
            await client.aclose()

    for record in checkpoints.records:
        assert record.status == "FAILED"
        assert record.attempt_count == 1
        assert record.retry_count == 0
        assert record.error_type == "SourceError"
        assert record.source_mode == "LIVE"
        assert record.temporal_semantics == "RETROSPECTIVE_RECONSTRUCTION"
        assert record.quality_summary | {
            "source_mode": "LIVE",
            "research_provenance": "HISTORICAL_RECONSTRUCTION",
            "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
            "evidence_class": "PARTIALLY_RECONSTRUCTABLE",
        } == record.quality_summary
        assert record.raw_references and record.raw_checksums
        assert Path(record.raw_references[0]).suffix == ".gz"
    assert result.status == "BLOCKED"
    assert result.accounting["attempts"] == 1
    assert result.checkpoints_written == 1


@pytest.mark.asyncio
async def test_successful_backfill_resume_is_noop_and_json_checkpoint_is_readable(
    artifact_root: Path,
) -> None:
    calls = 0

    class Adapter:
        source = "MLB_STATS_API"

        async def get_historical_schedule(self, event_date: date) -> AdapterResponse:
            nonlocal calls
            calls += 1
            now = datetime.now(UTC)
            return AdapterResponse(
                envelope=SourceEnvelope(
                    source=self.source,
                    source_mode="LIVE",
                    fetched_at=now,
                    data=[{"source_game_id": "1"}],
                    metadata={
                        "research_provenance": "HISTORICAL_RECONSTRUCTION",
                        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                        "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
                    },
                ),
                raw_payload={"dates": [{"date": event_date.isoformat(), "games": []}]},
            )

    repository = JsonCheckpointRepository(artifact_root / "checkpoints" / "history.jsonl")
    runner = HistoricalBackfillRunner(
        archive=RawArchive(artifact_root / "raw"),
        checkpoints=repository,
        adapter_factory=lambda executor: Adapter(),
        request_policy=ConservativeRequestPolicy(max_attempts=1, minimum_interval_seconds=0),
        free_space=lambda path: 200_000_000_000,
    )
    first = await runner.run(_options())
    second = await runner.run(_options(resume=True))

    assert first.status == second.status == "ARCHIVED_CANONICAL_PENDING"
    assert "canonical" in (first.reason or "").lower()
    assert first.completion_scope == second.completion_scope == "RAW_ARCHIVE_ONLY"
    assert first.canonical_records_accepted == second.canonical_records_accepted == 0
    assert first.chunks_completed == 1
    assert second.chunks_completed == 0
    assert second.chunks_skipped == 1
    assert second.accounting["attempts"] == 0
    assert calls == 1
    lines = (artifact_root / "checkpoints" / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    checkpoint_document = json.loads(lines[0])["checkpoint"]
    assert checkpoint_document["status"] == "SUCCEEDED"
    assert checkpoint_document["attempt_count"] == 0
    assert len(checkpoint_document["identity_checksum"]) == 64
    assert checkpoint_document["quality_summary"] | {
        "archive_status": "SUCCEEDED",
        "canonical_status": "PENDING",
        "source_mode": "LIVE",
        "research_provenance": "HISTORICAL_RECONSTRUCTION",
        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
        "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
    } == checkpoint_document["quality_summary"]
    assert checkpoint_document["quality_summary"]["checksum_semantics"] | {
        "raw_checksums": "SHA256_ARCHIVE_FILE_BYTES",
    } == checkpoint_document["quality_summary"]["checksum_semantics"]
    assert checkpoint_document["raw_checksums"][0] != checkpoint_document["quality_summary"][
        "checksum_semantics"
    ]["payload_checksums"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("envelope_change", "message"),
    [
        ({"source": "OTHER_SOURCE"}, "source does not match"),
        ({"source_mode": "DEMO"}, "source_mode must be LIVE"),
        (
            {"metadata": {
                "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
            }},
            "research_provenance",
        ),
        (
            {"metadata": {
                "research_provenance": "HISTORICAL_RECONSTRUCTION",
                "temporal_semantics": "WRONG",
                "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
            }},
            "temporal_semantics",
        ),
        (
            {"metadata": {
                "research_provenance": "HISTORICAL_RECONSTRUCTION",
                "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                "evidence_class": "PARTIALLY_RECONSTRUCTABLE",
            }},
            "evidence_class",
        ),
    ],
)
async def test_backfill_rejects_mismatched_adapter_provenance_before_archive(
    artifact_root: Path,
    envelope_change: dict[str, Any],
    message: str,
) -> None:
    class Adapter:
        source = "MLB_STATS_API"

        async def get_historical_schedule(self, event_date: date) -> AdapterResponse:
            now = datetime.now(UTC)
            values: dict[str, Any] = {
                "source": self.source,
                "source_mode": "LIVE",
                "fetched_at": now,
                "data": [],
                "metadata": {
                    "research_provenance": "HISTORICAL_RECONSTRUCTION",
                    "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                    "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
                },
            }
            return AdapterResponse(
                envelope=SourceEnvelope(**{**values, **envelope_change}),
                raw_payload={"dates": []},
            )

    checkpoints = MemoryCheckpointRepository()
    runner = HistoricalBackfillRunner(
        archive=RawArchive(artifact_root / "raw"),
        checkpoints=checkpoints,
        adapter_factory=lambda executor: Adapter(),
        request_policy=ConservativeRequestPolicy(max_attempts=1, minimum_interval_seconds=0),
        free_space=lambda path: 200_000_000_000,
    )

    result = await runner.run(_options())

    assert result.status == "BLOCKED"
    assert message in (result.reason or "")
    assert result.accounting["attempts"] == 0
    assert checkpoints.records[0].attempt_count == 0
    assert checkpoints.records[0].quality_summary["archive_status"] == "NOT_WRITTEN"
    assert checkpoints.records[0].quality_summary["canonical_status"] == "NOT_ATTEMPTED"
    assert not list((artifact_root / "raw").rglob("*.json.gz"))


@pytest.mark.parametrize(
    ("extra_args", "expected_status"),
    [
        (["--dry-run"], "DRY_RUN"),
        (["--max-requests", "2"], "BLOCKED"),
    ],
)
def test_backfill_cli_never_constructs_adapter_without_recorded_terms_approval(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra_args: list[str],
    expected_status: str,
) -> None:
    def forbidden_adapter(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("CLI must block before adapter construction")

    monkeypatch.setattr(
        cli_module,
        "get_settings",
        lambda: SimpleNamespace(raw_data_root=artifact_root / "raw"),
    )
    monkeypatch.setattr(backfill_module, "MLBStatsAPIAdapter", forbidden_adapter)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "asp",
            "backfill-mlb-history",
            "--start-date",
            "2025-04-01",
            "--end-date",
            "2025-04-01",
            "--season",
            "2025",
            *extra_args,
        ],
    )

    cli_module.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == expected_status
    assert payload["accounting"]["attempts"] == 0
    assert payload["plan"]["adapter_constructed"] is False
    assert payload["plan"]["odds_requests"] == 0
    assert payload["plan"]["statcast_requests"] == 0
    assert payload["plan"]["weather_requests"] == 0
    assert payload["plan"]["execution_scope"] == "RAW_ARCHIVE_ONLY"
    assert payload["plan"]["canonical_persistence_enabled"] is False
    if expected_status == "BLOCKED":
        assert payload["plan"]["terms_status"] == "LEGAL_REVIEW_REQUIRED"
        assert "terms" in payload["reason"].lower()
