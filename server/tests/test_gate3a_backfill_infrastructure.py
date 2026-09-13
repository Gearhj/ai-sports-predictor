from __future__ import annotations

import copy
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
from app.data_sources.base import (
    AdapterResponse,
    HistoricalBoxscoreDTO,
    HistoricalFinalDTO,
    HistoricalGameEvidenceDTO,
    HistoricalScheduleGameDTO,
    HistoricalTeamGameEvidenceDTO,
    ScheduleGameDTO,
    SourceEnvelope,
    TeamDTO,
    VenueDTO,
)
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
        "expected_final_games": 1,
        "max_requests": 2,
        "skip_statcast": True,
        "skip_weather": True,
        "terms_status": "APPROVED",
    }
    return HistoricalBackfillOptions(**{**values, **changes})


def _schedule_response(*, source: str = "MLB_STATS_API") -> AdapterResponse:
    now = datetime.now(UTC)
    row = HistoricalScheduleGameDTO(
        game=ScheduleGameDTO(
            source_game_id="777",
            scheduled_at=datetime(2025, 4, 1, 23, 5, tzinfo=UTC),
            event_date=date(2025, 4, 1),
            status="Final",
            away_team=TeamDTO(source_id="10", name="Away"),
            home_team=TeamDTO(source_id="20", name="Home"),
            venue=VenueDTO(source_id="1", name="Test Park", latitude=25.0, longitude=-80.0),
            away_score=2,
            home_score=4,
        ),
        season=2025,
        official_date=date(2025, 4, 1),
        game_type="R",
        abstract_state="Final",
        coded_state="F",
        acquired_at=now,
    )
    return AdapterResponse(
        envelope=SourceEnvelope(
            source=source,
            source_mode="LIVE",
            fetched_at=now,
            data=[row.model_dump(mode="json")],
            metadata={
                "research_provenance": "HISTORICAL_RECONSTRUCTION",
                "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
            },
        ),
        raw_payload={"dates": [{"date": "2025-04-01", "games": [{"gamePk": 777}]}]},
    )


def _game_response(
    *,
    evidence_class: str = "RECONSTRUCTABLE_POINT_IN_TIME",
    source_game_id: str = "777",
) -> AdapterResponse:
    acquired = datetime.now(UTC)
    is_partial = evidence_class == "PARTIALLY_RECONSTRUCTABLE"
    final = HistoricalFinalDTO(
        source_game_id=source_game_id,
        official_date=date(2025, 4, 1),
        status="Completed Early: Rain" if is_partial else "Final",
        abstract_state="Final",
        home_score=4,
        away_score=2,
        innings_played=9,
        event_completed_at=datetime(2025, 4, 2, 1, 44, tzinfo=UTC),
        completion_time_basis="DERIVED_LAST_EVENT_COMPLETION_PROXY",
        completion_time_source_field=(
            "liveData.plays.allPlays[49].about.endTime"
            if is_partial else "liveData.plays.allPlays[-1].about.endTime"
        ),
        completion_time_method=(
            "last prior complete play before a terminal rain-delay advisory after "
            "FINAL/completed-early validation"
            if is_partial
            else "last complete play in provider array after FINAL-state validation"
        ),
        completion_time_limitation=(
            "proxy precedes the terminal rain decision and advisory; it is neither an "
            "explicit game-final timestamp nor the time MLB ended the game early"
            if is_partial else "not an explicit final publication timestamp"
        ),
        acquired_at=acquired,
        evidence_class=evidence_class,
    )
    boxscore = HistoricalBoxscoreDTO(
        source_game_id=source_game_id,
        acquired_at=acquired,
        players=[],
        teams=[
            HistoricalTeamGameEvidenceDTO(
                source_game_id=source_game_id, source_team_id="10", is_home=False,
                team_stats={"batting": {"runs": 2}}, acquired_at=acquired,
                evidence_class=evidence_class,
            ),
            HistoricalTeamGameEvidenceDTO(
                source_game_id=source_game_id, source_team_id="20", is_home=True,
                team_stats={"batting": {"runs": 4}}, acquired_at=acquired,
                evidence_class=evidence_class,
            ),
        ],
        evidence_class=evidence_class,
    )
    evidence = HistoricalGameEvidenceDTO(
        source_game_id=source_game_id,
        acquired_at=acquired,
        final=final,
        boxscore=boxscore,
        evidence_class=evidence_class,
    )
    return AdapterResponse(
        envelope=SourceEnvelope(
            source="MLB_STATS_API",
            source_mode="LIVE",
            fetched_at=acquired,
            source_event_time=final.event_completed_at,
            data=evidence.model_dump(mode="json"),
            metadata={
                "research_provenance": "HISTORICAL_RECONSTRUCTION",
                "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                "evidence_class": evidence_class,
            },
        ),
        raw_payload={"gamePk": int(source_game_id), "gameData": {}, "liveData": {}},
    )


@pytest.fixture
def rain_shortened_feed_payload() -> dict[str, Any]:
    """Relevant tail of the archived MLB game 777474 provider shape."""

    return {
        "gamePk": 777474,
        "gameData": {
            "status": {
                "abstractGameCode": "F",
                "abstractGameState": "Final",
                "codedGameState": "F",
                "detailedState": "Completed Early: Rain",
                "reason": "Rain",
                "startTimeTBD": False,
                "statusCode": "FR",
            },
            "datetime": {
                "officialDate": "2025-06-18",
                "dateTime": "2025-06-18T23:10:00Z",
            },
        },
        "liveData": {
            "linescore": {
                "currentInning": 6,
                "teams": {"away": {"runs": 2}, "home": {"runs": 4}},
            },
            "plays": {
                "allPlays": [
                    {
                        "result": {
                            "awayScore": 2,
                            "description": "Santiago Espinal strikes out swinging.",
                            "event": "Strikeout",
                            "eventType": "strikeout",
                            "homeScore": 4,
                            "isOut": True,
                            "rbi": 0,
                            "type": "atBat",
                        },
                        "about": {
                            "atBatIndex": 49,
                            "endTime": "2025-06-19T02:49:29.424Z",
                            "inning": 6,
                            "isComplete": True,
                            "startTime": "2025-06-19T02:47:18.936Z",
                        },
                    },
                    {
                        "result": {
                            "awayScore": 2,
                            "description": "Status Change - Delayed: Rain",
                            "event": "Game Advisory",
                            "eventType": "game_advisory",
                            "homeScore": 4,
                            "isOut": False,
                            "rbi": 0,
                            "type": "atBat",
                        },
                        "about": {
                            "atBatIndex": 50,
                            "endTime": "2025-06-19T02:50:32.045Z",
                            "inning": 6,
                            "isComplete": False,
                            "startTime": "2025-06-19T02:49:50.473Z",
                        },
                        "playEvents": [{
                            "type": "action",
                            "startTime": "2025-06-19T02:50:32.045Z",
                            "endTime": "2025-06-19T02:50:32.045Z",
                            "details": {
                                "description": "Status Change - Delayed: Rain",
                                "event": "Game Advisory",
                                "eventType": "game_advisory",
                            },
                        }],
                    },
                ]
            },
            "boxscore": {
                "teams": {
                    "away": {
                        "team": {"id": 142},
                        "pitchers": [],
                        "teamStats": {"batting": {"runs": 2}},
                        "players": {},
                    },
                    "home": {
                        "team": {"id": 113},
                        "pitchers": [],
                        "teamStats": {"batting": {"runs": 4}},
                        "players": {},
                    },
                }
            },
        },
    }


@pytest.fixture
def completed_early_feed_payload(
    rain_shortened_feed_payload: dict[str, Any],
) -> dict[str, Any]:
    """Relevant tail of archived MLB game 777188's provider shape."""

    payload = copy.deepcopy(rain_shortened_feed_payload)
    payload["gamePk"] = 777188
    payload["gameData"]["status"] = {
        "abstractGameCode": "F",
        "abstractGameState": "Final",
        "codedGameState": "F",
        "detailedState": "Completed Early",
        "startTimeTBD": False,
        "statusCode": "FO",
    }
    payload["gameData"]["datetime"] = {
        "officialDate": "2025-07-08",
        "dateTime": "2025-07-08T23:40:00Z",
    }
    payload["liveData"]["linescore"] = {
        "currentInning": 7,
        "teams": {"away": {"runs": 6}, "home": {"runs": 1}},
    }
    prior_play, advisory = payload["liveData"]["plays"]["allPlays"]
    prior_play["result"] = {
        "awayScore": 6,
        "description": "Davis Schneider strikes out swinging.",
        "event": "Strikeout",
        "eventType": "strikeout",
        "homeScore": 1,
        "isOut": True,
        "rbi": 0,
        "type": "atBat",
    }
    prior_play["about"] = {
        "atBatIndex": 56,
        "endTime": "2025-07-09T01:39:07.371Z",
        "inning": 7,
        "isComplete": True,
        "startTime": "2025-07-09T01:37:45.649Z",
    }
    advisory["result"] = {
        "awayScore": 6,
        "description": "Status Change - Delayed: Rain",
        "event": "Game Advisory",
        "eventType": "game_advisory",
        "homeScore": 1,
        "isOut": False,
        "rbi": 0,
        "type": "atBat",
    }
    advisory["about"] = {
        "atBatIndex": 57,
        "endTime": "2025-07-09T01:42:15.551Z",
        "inning": 7,
        "isComplete": False,
        "startTime": "2025-07-09T01:39:15.543Z",
    }
    advisory["playEvents"][-1] = {
        "type": "action",
        "startTime": "2025-07-09T01:42:15.551Z",
        "endTime": "2025-07-09T01:42:15.551Z",
        "details": {
            "awayScore": 6,
            "description": "Status Change - Delayed: Rain",
            "event": "Game Advisory",
            "eventType": "game_advisory",
            "homeScore": 1,
            "isOut": False,
            "isScoringPlay": False,
        },
    }
    payload["liveData"]["boxscore"]["teams"]["away"]["team"]["id"] = 141
    payload["liveData"]["boxscore"]["teams"]["home"]["team"]["id"] = 145
    return payload


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
    # Addressed by request AND content: the request fingerprint locates the
    # semantic request, the payload checksum distinguishes one observation of
    # it from another. Repeating the same bytes still resolves to this object.
    assert path.parts[-5:-1] == ("historical", "mlb_stats_api", "2025", "04")
    assert path.name.startswith("2025-04-01_aaaaaaaaaaaaaaaaaaaaaaaa_")
    assert path.name.endswith(".json.gz")
    assert first.revision == 1
    assert first.supersedes_payload_checksum is None
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

    # A source whose answer changed is not a conflict. It is a second
    # observation of the same request, and it gets its own immutable object
    # beside the first with the supersession recorded.
    changed = archive.write_historical(
        "MLB_STATS_API",
        {"dates": [{"games": [{"gamePk": 1}, {"gamePk": 2}]}]},
        partition_date=date(2025, 4, 1),
        request_fingerprint="a" * 64,
        source_event_timestamp=datetime(2025, 4, 1, tzinfo=UTC),
        checkpoint_id="checkpoint-1",
        run_id="run-1",
    )
    assert changed.revision == 2
    assert changed.supersedes_payload_checksum == first.payload_checksum
    assert changed.raw_file_reference != first.raw_file_reference
    # The original bytes are untouched and still readable.
    assert path.exists()
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        assert json.load(stream)["metadata"]["checksum"] == first.payload_checksum

    # An object whose stored bytes contradict the request they claim to answer
    # is still a hard conflict.
    corrupted = Path(changed.raw_file_reference)
    with gzip.open(corrupted, "rt", encoding="utf-8") as stream:
        document = json.load(stream)
    document["metadata"]["source"] = "SOMETHING_ELSE"
    with gzip.open(corrupted, "wt", encoding="utf-8") as stream:
        json.dump(document, stream)
    with pytest.raises(RawArchiveConflictError):
        archive.write_historical(
            "MLB_STATS_API",
            {"dates": [{"games": [{"gamePk": 1}, {"gamePk": 2}]}]},
            partition_date=date(2025, 4, 1),
            request_fingerprint="a" * 64,
            source_event_timestamp=datetime(2025, 4, 1, tzinfo=UTC),
            checkpoint_id="checkpoint-1",
            run_id="run-1",
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
async def test_historical_mlb_schedule_defers_final_until_completion_evidence() -> None:
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
                "venue": {
                    "id": 1,
                    "name": "Test Park",
                    "location": {"defaultCoordinates": {"latitude": 25.7, "longitude": -80.2}},
                },
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
    assert row["final"] is None
    assert row["game"]["away_score"] == 2
    assert row["game"]["home_score"] == 4
    assert row["game"]["venue"]["latitude"] == 25.7
    assert row["game"]["venue"]["longitude"] == -80.2
    assert response.envelope.metadata["completion_evidence_included"] is False
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
async def test_one_v1_1_feed_yields_final_proxy_boxscore_and_actual_provenance() -> None:
    payload = {
        "gamePk": 777,
        "metaData": {"timeStamp": "20250402_014400"},
        "gameData": {
            "status": {"abstractGameState": "Final", "detailedState": "Final"},
            "datetime": {
                "officialDate": "2025-04-01",
                "dateTime": "2025-04-01T23:05:00Z",
            },
        },
        "liveData": {
            "linescore": {
                "currentInning": 9,
                "teams": {"away": {"runs": 2}, "home": {"runs": 4}},
            },
            "plays": {"allPlays": [{
                "about": {
                    "atBatIndex": 75,
                    "isComplete": True,
                    "endTime": "2025-04-02T01:44:00.125Z",
                },
                "playEvents": [{"endTime": "2025-04-02T01:44:00.125Z"}],
            }]},
            "boxscore": {"teams": {
                "away": {
                    "team": {"id": 10},
                    "pitchers": [101],
                    "battingOrder": [101],
                    "teamStats": {"batting": {"runs": 2}},
                    "players": {"ID101": {
                        "person": {"id": 101, "fullName": "Away Starter"},
                        "battingOrder": "100",
                        "position": {"abbreviation": "P"},
                        "stats": {"pitching": {"inningsPitched": "8.0"}},
                    }},
                },
                "home": {
                    "team": {"id": 20},
                    "pitchers": [201],
                    "battingOrder": [202],
                    "teamStats": {"batting": {"runs": 4}},
                    "players": {
                        "ID201": {
                            "person": {"id": 201, "fullName": "Home Starter"},
                            "position": {"abbreviation": "P"},
                            "stats": {"pitching": {"inningsPitched": "9.0"}},
                        },
                        "ID202": {
                            "person": {"id": 202, "fullName": "Leadoff"},
                            "battingOrder": "100",
                            "position": {"abbreviation": "CF"},
                            "stats": {"batting": {"atBats": 4}},
                        },
                    },
                },
            }},
        },
    }
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, json=payload)

    executor = ConservativeRequestExecutor(
        "MLB_STATS_API",
        maximum_requests=1,
        policy=ConservativeRequestPolicy(max_attempts=1, minimum_interval_seconds=0),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await MLBStatsAPIAdapter(
            client=client, request_executor=executor
        ).get_historical_game_evidence("777")

    evidence = HistoricalGameEvidenceDTO.model_validate(response.envelope.data)
    assert requested_paths == ["/api/v1.1/game/777/feed/live"]
    assert executor.accounting.attempts == executor.accounting.successes == 1
    assert executor.accounting.attempts_by_operation == {"historical_game_feed": 1}
    assert evidence.final.event_completed_at == datetime(
        2025, 4, 2, 1, 44, 0, 125000, tzinfo=UTC
    )
    assert evidence.final.completion_time_basis == "DERIVED_LAST_EVENT_COMPLETION_PROXY"
    assert evidence.final.completion_time_source_field == (
        "liveData.plays.allPlays[-1].about.endTime"
    )
    assert "not an explicit" in (evidence.final.completion_time_limitation or "")
    assert evidence.final.source_published_at is None
    assert evidence.evidence_class == "RECONSTRUCTABLE_POINT_IN_TIME"
    starter = next(row for row in evidence.boxscore.players if row.source_player_id == "101")
    leadoff = next(row for row in evidence.boxscore.players if row.source_player_id == "202")
    assert starter.starter_provenance == "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION"
    assert starter.batting_order == 1
    assert leadoff.lineup_provenance == "HISTORICAL_ACTUAL_LINEUP"
    assert leadoff.batting_order == 1


@pytest.mark.asyncio
async def test_completed_early_rain_feed_uses_prior_complete_play_as_tier_b_proxy(
    rain_shortened_feed_payload: dict[str, Any],
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=rain_shortened_feed_payload)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await MLBStatsAPIAdapter(
            client=client
        ).get_historical_game_evidence("777474")

    evidence = HistoricalGameEvidenceDTO.model_validate(response.envelope.data)
    assert rain_shortened_feed_payload["gameData"]["status"] | {
        "abstractGameState": "Final",
        "detailedState": "Completed Early: Rain",
        "reason": "Rain",
        "statusCode": "FR",
    } == rain_shortened_feed_payload["gameData"]["status"]
    assert evidence.final.event_completed_at == datetime(
        2025, 6, 19, 2, 49, 29, 424000, tzinfo=UTC
    )
    assert evidence.final.event_completed_at != datetime(
        2025, 6, 19, 2, 50, 32, 45000, tzinfo=UTC
    )
    assert evidence.final.completion_time_basis == "DERIVED_LAST_EVENT_COMPLETION_PROXY"
    assert (
        evidence.final.completion_time_source_field
        == "liveData.plays.allPlays[0].about.endTime"
    )
    assert "terminal rain-delay advisory" in (
        evidence.final.completion_time_method or ""
    )
    assert "precedes the terminal rain decision" in (
        evidence.final.completion_time_limitation or ""
    )
    assert evidence.evidence_class == "PARTIALLY_RECONSTRUCTABLE"
    assert evidence.final.evidence_class == "PARTIALLY_RECONSTRUCTABLE"
    assert evidence.boxscore.evidence_class == "PARTIALLY_RECONSTRUCTABLE"
    assert {
        row.evidence_class for row in evidence.boxscore.teams
    } == {"PARTIALLY_RECONSTRUCTABLE"}
    assert response.envelope.metadata["evidence_class"] == "PARTIALLY_RECONSTRUCTABLE"


@pytest.mark.asyncio
async def test_completed_early_feed_without_reason_uses_explicit_rain_advisory_proxy(
    completed_early_feed_payload: dict[str, Any],
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=completed_early_feed_payload)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await MLBStatsAPIAdapter(
            client=client
        ).get_historical_game_evidence("777188")

    evidence = HistoricalGameEvidenceDTO.model_validate(response.envelope.data)
    assert completed_early_feed_payload["gameData"]["status"] == {
        "abstractGameCode": "F",
        "abstractGameState": "Final",
        "codedGameState": "F",
        "detailedState": "Completed Early",
        "startTimeTBD": False,
        "statusCode": "FO",
    }
    assert evidence.final.event_completed_at == datetime(
        2025, 7, 9, 1, 39, 7, 371000, tzinfo=UTC
    )
    assert evidence.final.event_completed_at != datetime(
        2025, 7, 9, 1, 42, 15, 551000, tzinfo=UTC
    )
    assert evidence.final.completion_time_source_field == (
        "liveData.plays.allPlays[0].about.endTime"
    )
    assert evidence.final.evidence_class == "PARTIALLY_RECONSTRUCTABLE"
    assert evidence.boxscore.evidence_class == "PARTIALLY_RECONSTRUCTABLE"
    assert evidence.evidence_class == "PARTIALLY_RECONSTRUCTABLE"


@pytest.mark.asyncio
async def test_completed_early_non_rain_advisory_remains_fail_closed(
    completed_early_feed_payload: dict[str, Any],
) -> None:
    payload = copy.deepcopy(completed_early_feed_payload)
    advisory = payload["liveData"]["plays"]["allPlays"][-1]
    advisory["result"]["description"] = "Status Change - Delayed: Power"
    advisory["playEvents"][-1]["details"]["description"] = (
        "Status Change - Delayed: Power"
    )

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SourceError, match="schema validation failed"):
            await MLBStatsAPIAdapter(
                client=client
            ).get_historical_game_evidence("777188")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_shape",
    [
        "not_final",
        "not_completed_early",
        "not_game_advisory",
        "not_delayed_rain",
        "missing_advisory_end",
        "no_prior_complete_play",
        "unparseable_prior_end",
    ],
)
async def test_other_trailing_incomplete_feed_shapes_remain_fail_closed(
    rain_shortened_feed_payload: dict[str, Any],
    invalid_shape: str,
) -> None:
    payload = copy.deepcopy(rain_shortened_feed_payload)
    status = payload["gameData"]["status"]
    prior_play, advisory = payload["liveData"]["plays"]["allPlays"]
    if invalid_shape == "not_final":
        status["abstractGameState"] = "Live"
    elif invalid_shape == "not_completed_early":
        status["detailedState"] = "Final"
    elif invalid_shape == "not_game_advisory":
        advisory["result"]["eventType"] = "pitching_change"
    elif invalid_shape == "not_delayed_rain":
        advisory["result"]["description"] = "Status Change - Delayed: Fog"
        advisory["playEvents"][-1]["details"]["description"] = (
            "Status Change - Delayed: Fog"
        )
    elif invalid_shape == "missing_advisory_end":
        advisory["about"].pop("endTime")
    elif invalid_shape == "no_prior_complete_play":
        prior_play["about"]["isComplete"] = False
    elif invalid_shape == "unparseable_prior_end":
        prior_play["about"]["endTime"] = "not-a-timestamp"

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SourceError, match="schema validation failed"):
            await MLBStatsAPIAdapter(
                client=client
            ).get_historical_game_evidence("777474")


@pytest.mark.asyncio
async def test_historical_game_feed_rejects_payload_identity_mismatch() -> None:
    payload = {
        "gamePk": 778,
        "gameData": {
            "status": {"abstractGameState": "Final", "detailedState": "Final"},
            "datetime": {"officialDate": "2025-04-01"},
        },
        "liveData": {},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SourceError, match="schema validation failed"):
            await MLBStatsAPIAdapter(client=client).get_historical_game_evidence("777")


def test_historical_boxscore_does_not_promote_substitute_to_starting_lineup() -> None:
    boxscore = MLBStatsAPIAdapter._historical_boxscore_from_node(
        {
            "teams": {
                "away": {
                    "team": {"id": 10},
                    "pitchers": [],
                    "battingOrder": [101],
                    "teamStats": {},
                    "players": {
                        "ID101": {
                            "person": {"id": 101, "fullName": "Substitute"},
                            "battingOrder": "101",
                            "position": {"abbreviation": "CF"},
                            "stats": {"batting": {"atBats": 1}},
                        }
                    },
                },
                "home": {
                    "team": {"id": 20},
                    "pitchers": [],
                    "battingOrder": [],
                    "teamStats": {},
                    "players": {},
                },
            }
        },
        source_game_id="777",
        acquired_at=datetime(2025, 4, 2, tzinfo=UTC),
        evidence_class="RECONSTRUCTABLE_POINT_IN_TIME",
    )

    substitute = next(row for row in boxscore.players if row.source_player_id == "101")
    assert substitute.batting_order is None
    assert substitute.lineup_provenance is None


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
    schedule_calls = 0
    feed_calls = 0

    class Adapter:
        source = "MLB_STATS_API"

        async def get_historical_schedule_range(
            self, start_date: date, end_date: date
        ) -> AdapterResponse:
            nonlocal schedule_calls
            schedule_calls += 1
            return _schedule_response()

        async def get_historical_game_evidence(self, source_game_id: str) -> AdapterResponse:
            nonlocal feed_calls
            feed_calls += 1
            assert source_game_id == "777"
            return _game_response()

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
    assert schedule_calls == 2
    assert feed_calls == 1
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

        async def get_historical_schedule_range(
            self, start_date: date, end_date: date
        ) -> AdapterResponse:
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
    assert payload["plan"]["execution_scope"] == "CORE_CANONICAL"
    assert payload["plan"]["canonical_persistence_enabled"] is True
    if expected_status == "BLOCKED":
        assert payload["plan"]["terms_status"] == "LEGAL_REVIEW_REQUIRED"
        assert "terms" in payload["reason"].lower()


def test_pilot_preflight_is_exactly_one_schedule_plus_fifteen_feeds(
    artifact_root: Path,
) -> None:
    runner = HistoricalBackfillRunner(
        archive=RawArchive(artifact_root / "raw"),
        checkpoints=MemoryCheckpointRepository(),
        free_space=lambda path: 200_000_000_000,
    )
    plan = runner.preflight(HistoricalBackfillOptions(
        start_date=date(2025, 6, 17),
        end_date=date(2025, 6, 17),
        season=2025,
        expected_final_games=15,
        max_requests=16,
        terms_status="APPROVED",
    ))

    assert plan.status == "READY"
    assert plan.planned_requests == plan.maximum_requests == 16
    assert plan.schedule_requests == 1
    assert plan.game_feed_requests == 15
    assert plan.odds_requests == plan.statcast_requests == plan.weather_requests == 0


@pytest.mark.asyncio
async def test_discovery_archives_schedule_only_and_reports_exact_full_cap(
    artifact_root: Path,
) -> None:
    feed_calls = 0
    checkpoints = MemoryCheckpointRepository()

    class Adapter:
        source = "MLB_STATS_API"

        def __init__(self, executor: ConservativeRequestExecutor) -> None:
            self.executor = executor

        async def get_historical_schedule_range(
            self, start_date: date, end_date: date
        ) -> AdapterResponse:
            async def response() -> AdapterResponse:
                return _schedule_response()
            return await self.executor.execute(response, operation_name="historical_schedule")

        async def get_historical_game_evidence(self, source_game_id: str) -> AdapterResponse:
            nonlocal feed_calls
            feed_calls += 1
            raise AssertionError("discovery must not fetch game feeds")

    runner = HistoricalBackfillRunner(
        archive=RawArchive(artifact_root / "raw"),
        checkpoints=checkpoints,
        adapter_factory=Adapter,
        request_policy=ConservativeRequestPolicy(max_attempts=1, minimum_interval_seconds=0),
        free_space=lambda path: 200_000_000_000,
    )
    result = await runner.run(_options(
        discovery_only=True,
        expected_final_games=None,
        max_requests=1,
    ))

    assert result.status == "DISCOVERED"
    assert result.discovered_final_games == 1
    assert result.required_full_run_requests == 2
    assert result.completion_scope == "DISCOVERY_ARCHIVE_ONLY"
    assert result.plan.canonical_persistence_enabled is False
    assert result.accounting["attempts_by_operation"] == {"historical_schedule": 1}
    assert result.checkpoints_written == 1
    assert feed_calls == 0
    assert len(list((artifact_root / "raw").rglob("*.json.gz"))) == 1
    assert len(checkpoints.records) == 1
    checkpoint = checkpoints.records[0]
    assert checkpoint.status == "SUCCEEDED"
    assert checkpoint.stream_key == HistoricalBackfillRunner.DISCOVERY_STREAM_KEY
    assert checkpoint.attempt_count == 1
    assert checkpoint.records_received == 1
    assert checkpoint.raw_references and checkpoint.raw_checksums
    assert checkpoint.quality_summary["execution_scope"] == "DISCOVERY_ARCHIVE_ONLY"
    assert checkpoint.quality_summary["discovered_final_games"] == 1


@pytest.mark.asyncio
async def test_invalid_game_feed_archives_raw_response_before_terminal_failure(
    artifact_root: Path,
) -> None:
    class Adapter:
        source = "MLB_STATS_API"

        def __init__(self, executor: ConservativeRequestExecutor) -> None:
            self.executor = executor

        async def get_historical_schedule_range(
            self, start_date: date, end_date: date
        ) -> AdapterResponse:
            async def response() -> AdapterResponse:
                return _schedule_response()

            return await self.executor.execute(
                response,
                operation_name="historical_schedule",
            )

        async def get_historical_game_evidence(
            self, source_game_id: str
        ) -> AdapterResponse:
            async def response() -> AdapterResponse:
                valid = _game_response()
                invalid_data = {
                    **valid.envelope.data,
                    "source_game_id": "wrong-game",
                }
                return AdapterResponse(
                    envelope=valid.envelope.model_copy(update={"data": invalid_data}),
                    raw_payload={
                        "gamePk": 999,
                        "malformed_identity_marker": "must-be-archived",
                    },
                )

            return await self.executor.execute(
                response,
                operation_name="historical_game_feed",
            )

    checkpoints = MemoryCheckpointRepository()
    runner = HistoricalBackfillRunner(
        archive=RawArchive(artifact_root / "raw"),
        checkpoints=checkpoints,
        adapter_factory=Adapter,
        request_policy=ConservativeRequestPolicy(
            max_attempts=1,
            minimum_interval_seconds=0,
        ),
        free_space=lambda path: 200_000_000_000,
    )

    result = await runner.run(_options())

    assert result.status == "BLOCKED"
    assert result.accounting["attempts"] == 2
    assert result.accounting["attempts_by_operation"] == {
        "historical_schedule": 1,
        "historical_game_feed": 1,
    }
    assert result.checkpoints_written == 1
    assert len(checkpoints.records) == 1
    checkpoint = checkpoints.records[0]
    assert checkpoint.status == "FAILED"
    assert checkpoint.attempt_count == 2
    assert checkpoint.quality_summary["archive_status"] == "UNVALIDATED_ERROR_PAYLOAD"
    assert len(checkpoint.raw_references) == 2
    assert len(checkpoint.raw_checksums) == 2
    with gzip.open(checkpoint.raw_references[1], "rt", encoding="utf-8") as stream:
        archived = json.load(stream)
    assert archived["metadata"]["status"] == "unvalidated_error"
    assert archived["payload"]["malformed_identity_marker"] == "must-be-archived"


def test_schedule_reschedule_duplicate_selects_only_completed_version() -> None:
    completed = HistoricalScheduleGameDTO.model_validate(
        _schedule_response().envelope.data[0]
    )
    postponed = completed.model_copy(update={
        "game": completed.game.model_copy(update={
            "scheduled_at": datetime(2025, 3, 31, 23, 5, tzinfo=UTC),
            "status": "Postponed",
            "home_score": None,
            "away_score": None,
        }),
        "abstract_state": "Final",
        "coded_state": "D",
    })
    completed = completed.model_copy(update={
        "game": completed.game.model_copy(update={
            "rescheduled_from": postponed.game.scheduled_at,
        }),
    })

    selected = backfill_module._validated_schedule_rows(
        [postponed, completed], _options()
    )

    assert selected == [completed]


def test_schedule_duplicate_conflicting_completed_versions_fail_closed() -> None:
    first = HistoricalScheduleGameDTO.model_validate(
        _schedule_response().envelope.data[0]
    )
    conflicting = first.model_copy(update={
        "game": first.game.model_copy(update={"home_score": 5}),
    })

    with pytest.raises(SourceError, match="Conflicting completed schedule versions"):
        backfill_module._validated_schedule_rows([first, conflicting], _options())


def test_suspended_resumed_completed_pair_preserves_original_first_pitch() -> None:
    first = HistoricalScheduleGameDTO.model_validate(
        _schedule_response().envelope.data[0]
    )
    first = first.model_copy(update={
        "game": first.game.model_copy(update={
            "resume_at": datetime(2025, 4, 2, 18, 0, tzinfo=UTC),
        }),
    })
    resumed = first.model_copy(update={
        "source_schedule_date": date(2025, 4, 1),
        "game": first.game.model_copy(update={
            "scheduled_at": datetime(2025, 4, 2, 18, 0, tzinfo=UTC),
            "game_number": 2,
            "doubleheader_status": "S",
            "resume_at": None,
            "resumed_from_date": date(2025, 4, 1),
        }),
    })

    selected = backfill_module._validated_schedule_rows([first, resumed], _options())

    assert selected == [first]


def test_unlinked_equivalent_completed_versions_fail_closed() -> None:
    first = HistoricalScheduleGameDTO.model_validate(
        _schedule_response().envelope.data[0]
    )
    second = first.model_copy(update={
        "game": first.game.model_copy(update={
            "scheduled_at": datetime(2025, 4, 2, 18, 0, tzinfo=UTC),
        }),
    })

    with pytest.raises(SourceError, match="Unlinked suspended/resumed"):
        backfill_module._validated_schedule_rows([first, second], _options())


def test_postponed_makeup_outside_authorized_official_dates_is_not_acquired() -> None:
    row = HistoricalScheduleGameDTO.model_validate(
        _schedule_response().envelope.data[0]
    )
    postponed = row.model_copy(update={
        "source_schedule_date": date(2025, 4, 1),
        "official_date": date(2025, 9, 1),
        "abstract_state": "Final",
        "coded_state": "D",
        "game": row.game.model_copy(update={
            "status": "Postponed",
            "home_score": None,
            "away_score": None,
        }),
    })

    assert backfill_module._validated_schedule_rows([postponed], _options()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence_class",
    ["RECONSTRUCTABLE_POINT_IN_TIME", "PARTIALLY_RECONSTRUCTABLE"],
)
async def test_core_runner_counts_calls_and_passes_merged_evidence_to_canonical_writer(
    artifact_root: Path,
    evidence_class: str,
) -> None:
    class Adapter:
        source = "MLB_STATS_API"

        def __init__(self, executor: ConservativeRequestExecutor) -> None:
            self.executor = executor

        async def get_historical_schedule_range(
            self, start_date: date, end_date: date
        ) -> AdapterResponse:
            async def response() -> AdapterResponse:
                return _schedule_response()
            return await self.executor.execute(response, operation_name="historical_schedule")

        async def get_historical_game_evidence(self, source_game_id: str) -> AdapterResponse:
            async def response() -> AdapterResponse:
                return _game_response(evidence_class=evidence_class)
            return await self.executor.execute(response, operation_name="historical_game_feed")

    class Writer:
        def __init__(self) -> None:
            self.persisted: dict[str, Any] | None = None
            self.governance: dict[str, Any] | None = None

        def ensure_source_version(self, **kwargs: Any) -> object:
            self.governance = kwargs["governance"]
            return object()

        def persist_chunk(self, **kwargs: Any) -> SimpleNamespace:
            self.persisted = kwargs
            return SimpleNamespace(
                games=1, final_outcomes=1, team_observations=2,
                player_observations=0, duplicates=0,
            )

    writer = Writer()
    checkpoints = MemoryCheckpointRepository()
    runner = HistoricalBackfillRunner(
        archive=RawArchive(artifact_root / "raw"),
        checkpoints=checkpoints,
        adapter_factory=Adapter,
        canonical_writer=writer,
        governance={
            "approval_status": "APPROVED",
            "reviewer": "project owner",
            "reviewed_at": "2026-08-13",
        },
        request_policy=ConservativeRequestPolicy(max_attempts=1, minimum_interval_seconds=0),
        free_space=lambda path: 200_000_000_000,
    )
    result = await runner.run(_options())

    assert result.status == "COMPLETED"
    assert result.accounting["attempts"] == result.accounting["successes"] == 2
    assert result.accounting["attempts_by_operation"] == {
        "historical_schedule": 1,
        "historical_game_feed": 1,
    }
    assert result.canonical_games == result.canonical_finals == 1
    assert result.canonical_team_observations == 2
    assert writer.persisted is not None
    merged = writer.persisted["schedule_rows"][0]
    assert merged.final.completion_time_basis == "DERIVED_LAST_EVENT_COMPLETION_PROXY"
    assert merged.final.event_completed_at == datetime(2025, 4, 2, 1, 44, tzinfo=UTC)
    assert merged.evidence_class == evidence_class
    assert merged.final.evidence_class == evidence_class
    assert len(writer.persisted["boxscores"]) == 1
    assert writer.persisted["boxscores"][0].evidence_class == evidence_class
    assert len(writer.persisted["raw_references"]) == 2
    assert checkpoints.records[-1].quality_summary["evidence_class"] == evidence_class


@pytest.mark.asyncio
async def test_chunk_evidence_class_is_not_upgraded_after_a_later_tier_a_game(
    artifact_root: Path,
) -> None:
    schedule_response = _schedule_response()
    first = HistoricalScheduleGameDTO.model_validate(
        schedule_response.envelope.data[0]
    )
    second = first.model_copy(update={
        "game": first.game.model_copy(update={"source_game_id": "778"}),
    })
    schedule_response = AdapterResponse(
        envelope=schedule_response.envelope.model_copy(update={
            "data": [
                first.model_dump(mode="json"),
                second.model_dump(mode="json"),
            ],
        }),
        raw_payload={
            "dates": [{"date": "2025-04-01", "games": [{"gamePk": 777}, {"gamePk": 778}]}]
        },
    )

    class Adapter:
        source = "MLB_STATS_API"

        async def get_historical_schedule_range(
            self, start_date: date, end_date: date
        ) -> AdapterResponse:
            return schedule_response

        async def get_historical_game_evidence(self, source_game_id: str) -> AdapterResponse:
            return _game_response(
                source_game_id=source_game_id,
                evidence_class=(
                    "PARTIALLY_RECONSTRUCTABLE"
                    if source_game_id == "777" else "RECONSTRUCTABLE_POINT_IN_TIME"
                ),
            )

    checkpoints = MemoryCheckpointRepository()
    runner = HistoricalBackfillRunner(
        archive=RawArchive(artifact_root / "raw"),
        checkpoints=checkpoints,
        adapter_factory=lambda executor: Adapter(),
        request_policy=ConservativeRequestPolicy(max_attempts=1, minimum_interval_seconds=0),
        free_space=lambda path: 200_000_000_000,
    )

    result = await runner.run(_options(expected_final_games=2, max_requests=3))

    assert result.status == "ARCHIVED_CANONICAL_PENDING"
    assert result.chunks_completed == 1
    assert checkpoints.records[-1].quality_summary["evidence_class"] == (
        "PARTIALLY_RECONSTRUCTABLE"
    )


@pytest.mark.asyncio
async def test_core_runner_rejects_forward_only_game_feed_evidence(
    artifact_root: Path,
) -> None:
    class Adapter:
        source = "MLB_STATS_API"

        async def get_historical_schedule_range(
            self, start_date: date, end_date: date
        ) -> AdapterResponse:
            return _schedule_response()

        async def get_historical_game_evidence(self, source_game_id: str) -> AdapterResponse:
            return _game_response(evidence_class="FORWARD_ONLY")

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
    assert result.chunks_completed == 0
    assert checkpoints.records[-1].status == "FAILED"
    assert "evidence_class" in (checkpoints.records[-1].error_message or "")
