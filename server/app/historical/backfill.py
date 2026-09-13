from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from app.data_sources.base import (
    AdapterResponse,
    HistoricalGameEvidenceDTO,
    HistoricalScheduleGameDTO,
    SourceError,
)
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
A1_GAME_FEED_EVIDENCE_CLASSES = (
    "RECONSTRUCTABLE_POINT_IN_TIME",
    "PARTIALLY_RECONSTRUCTABLE",
)


class HistoricalMLBAdapter(Protocol):
    source: str

    async def get_historical_schedule_range(
        self, start_date: date, end_date: date
    ) -> AdapterResponse: ...

    async def get_historical_game_evidence(
        self, source_game_id: str
    ) -> AdapterResponse: ...


class HistoricalCanonicalWriter(Protocol):
    def ensure_source_version(self, **kwargs: Any) -> Any: ...

    def prepare_source_version(self, **kwargs: Any) -> Any: ...

    def persist_chunk(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class HistoricalBackfillOptions:
    start_date: date
    end_date: date
    season: int
    resume: bool = False
    source: SourceKey = "MLB_STATS_API"
    chunk_size: int = 1
    dry_run: bool = False
    discovery_only: bool = False
    expected_final_games: int | None = None
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
        if self.chunk_size < 1 or self.chunk_size > 61:
            raise ValueError("chunk_size must be between 1 and 61 days")
        if self.max_requests < 0:
            raise ValueError("max_requests must be non-negative")
        if self.expected_final_games is not None and self.expected_final_games < 0:
            raise ValueError("expected_final_games must be non-negative")
        if self.source != "MLB_STATS_API":
            raise ValueError("Only MLB_STATS_API is implemented; Odds is never a backfill source")
        if not self.skip_statcast or not self.skip_weather:
            raise ValueError("Gate 3A A1 core requires Statcast and weather to remain disabled")


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
    discovery_only: bool = False
    expected_final_games: int | None = None
    schedule_requests: int = 0
    game_feed_requests: int = 0
    statcast_requests: int = 0
    weather_requests: int = 0
    odds_requests: int = 0
    adapter_constructed: bool = False
    execution_scope: Literal[
        "CORE_CANONICAL", "DISCOVERY_ARCHIVE_ONLY", "RAW_ARCHIVE_ONLY"
    ] = "RAW_ARCHIVE_ONLY"
    canonical_persistence_enabled: bool = False


@dataclass(frozen=True)
class HistoricalBackfillResult:
    status: Literal[
        "COMPLETED",
        "ARCHIVED_CANONICAL_PENDING",
        "DISCOVERED",
        "PARTIAL",
        "BLOCKED",
        "DRY_RUN",
    ]
    reason: str | None
    plan: HistoricalBackfillPlan
    accounting: dict[str, Any]
    chunks_completed: int
    chunks_skipped: int
    records_received: int
    raw_bytes: int
    checkpoints_written: int
    run_id: str
    discovered_final_games: int | None = None
    required_full_run_requests: int | None = None
    completion_scope: Literal[
        "CORE_CANONICAL", "DISCOVERY_ARCHIVE_ONLY", "RAW_ARCHIVE_ONLY"
    ] = "RAW_ARCHIVE_ONLY"
    canonical_records_accepted: int = 0
    canonical_games: int = 0
    canonical_finals: int = 0
    canonical_team_observations: int = 0
    canonical_player_observations: int = 0


class HistoricalBackfillRunner:
    """Bounded schedule -> one-feed-per-final -> canonical A1 runner.

    A range schedule is one physical request. Each final game then uses one
    v1.1 live-feed request containing final state, play-by-play, and boxscore.
    Odds, Statcast, and weather have no dispatch path here.
    """

    STREAM_KEY = "mlb_regular_season_core_history"
    DISCOVERY_STREAM_KEY = "mlb_regular_season_core_discovery"
    SOURCE_VERSION_ID = "mlb_stats_api:historical-core-v1"

    def __init__(
        self,
        *,
        archive: RawArchive,
        checkpoints: CheckpointRepository,
        adapter_factory: Callable[[ConservativeRequestExecutor], HistoricalMLBAdapter] | None = None,
        canonical_writer: HistoricalCanonicalWriter | None = None,
        governance: dict[str, Any] | None = None,
        request_policy: ConservativeRequestPolicy | None = None,
        free_space: Callable[[Path], int] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.archive = archive
        self.checkpoints = checkpoints
        self.adapter_factory = adapter_factory or (
            lambda executor: MLBStatsAPIAdapter(request_executor=executor)
        )
        self.canonical_writer = canonical_writer
        self.governance = governance
        self.request_policy = request_policy or ConservativeRequestPolicy()
        self.free_space = free_space or _free_space
        self.now = now

    def preflight(self, options: HistoricalBackfillOptions) -> HistoricalBackfillPlan:
        chunks = _chunks(options.start_date, options.end_date, options.chunk_size)
        schedule_requests = 1 if (options.discovery_only or options.expected_final_games is not None) else 0
        game_feed_requests = 0 if options.discovery_only else (options.expected_final_games or 0)
        planned_requests = schedule_requests + game_feed_requests
        reason: str | None = None
        status: Literal["DRY_RUN", "BLOCKED", "READY"] = "READY"
        if options.dry_run:
            status = "DRY_RUN"
            reason = (
                "Dry run validated the local plan; no adapter was constructed and no provider was called."
            )
        elif options.terms_status != "APPROVED":
            status = "BLOCKED"
            reason = f"MLB source terms status is {options.terms_status}; acquisition is blocked."
        elif not options.discovery_only and options.expected_final_games is None:
            status = "BLOCKED"
            reason = (
                "Exact --expected-final-games is required before game-feed acquisition; "
                "run a one-request discovery first."
            )
        elif options.max_requests != planned_requests:
            status = "BLOCKED"
            reason = (
                f"Physical request cap must exactly equal the {planned_requests} planned calls "
                "(one range schedule plus one feed per expected final)."
            )
        elif self.free_space(self.archive.root) < options.minimum_free_bytes:
            status = "BLOCKED"
            reason = "Free disk space is below the 100 GB safety reserve."
        scope = (
            "DISCOVERY_ARCHIVE_ONLY"
            if options.discovery_only
            else ("CORE_CANONICAL" if self.canonical_writer is not None else "RAW_ARCHIVE_ONLY")
        )
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
            discovery_only=options.discovery_only,
            expected_final_games=options.expected_final_games,
            schedule_requests=schedule_requests,
            game_feed_requests=game_feed_requests,
            execution_scope=scope,
            canonical_persistence_enabled=(
                self.canonical_writer is not None and not options.discovery_only
            ),
        )

    async def run(self, options: HistoricalBackfillOptions) -> HistoricalBackfillResult:
        plan = self.preflight(options)
        run_id = uuid4().hex
        if plan.status != "READY":
            return self._empty_result(plan, options, run_id)

        executor = ConservativeRequestExecutor(
            options.source,
            maximum_requests=options.max_requests,
            policy=self.request_policy,
        )
        accounting_cursor = _accounting_snapshot(executor.accounting)
        adapter = self.adapter_factory(executor)
        plan = HistoricalBackfillPlan(**{**asdict(plan), "adapter_constructed": True})
        schedule_requested_at = self.now()
        schedule_archive: RawArchiveRecord | None = None
        try:
            schedule_response = await adapter.get_historical_schedule_range(
                options.start_date, options.end_date
            )
            _validate_response(
                schedule_response,
                expected_source=options.source,
                expected_class="RECONSTRUCTABLE_POINT_IN_TIME",
                context="schedule",
            )
            schedule_rows = [
                HistoricalScheduleGameDTO.model_validate(item)
                for item in schedule_response.envelope.data
            ]
            schedule_fingerprint = request_fingerprint(
                options.source,
                "mlb_historical_schedule_range",
                {
                    "start_date": options.start_date.isoformat(),
                    "end_date": options.end_date.isoformat(),
                    "season": options.season,
                    "game_type": "R",
                    "policy_version": "gate3a-a1-core-v1",
                },
            )
            schedule_archive = self.archive.write_historical(
                schedule_response.envelope.source,
                schedule_response.raw_payload,
                partition_date=options.start_date,
                request_fingerprint=schedule_fingerprint,
                # A date-range schedule response has no single provider event
                # instant. Preserve the requested range in the fingerprint and
                # canonical rows, but do not invent midnight as source time.
                source_event_timestamp=None,
                schema_version="mlb_statsapi_historical_schedule_range_v1",
                source_mode="LIVE",
                research_provenance="HISTORICAL_RECONSTRUCTION",
                temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                evidence_class="RECONSTRUCTABLE_POINT_IN_TIME",
                checkpoint_id=schedule_fingerprint,
                run_id=run_id,
            )
            # Persist the schema-valid provider payload before applying
            # cross-row identity rules. This keeps duplicate/correction shapes
            # auditable even when semantic validation blocks canonical work.
            schedule_rows = _validated_schedule_rows(schedule_rows, options)
            final_rows = [
                row
                for row in schedule_rows
                if _is_schedule_final(row)
            ]
        except Exception as exc:
            return self._schedule_failure(
                exc,
                plan,
                options,
                executor.accounting,
                run_id,
                schedule_requested_at,
                schedule_archive=schedule_archive,
            )

        discovered = len(final_rows)
        if options.discovery_only:
            completed_at = self.now()
            checkpoint = HistoricalCheckpoint(
                source_version_id=self.SOURCE_VERSION_ID,
                stream_key=self.DISCOVERY_STREAM_KEY,
                query_fingerprint=schedule_fingerprint,
                status="SUCCEEDED",
                requested_at=schedule_requested_at,
                completed_at=completed_at,
                season=options.season,
                event_start_at=datetime.combine(options.start_date, time.min, tzinfo=UTC),
                event_end_at=datetime.combine(options.end_date, time.max, tzinfo=UTC),
                sequence=0,
                ingestion_run_id=run_id,
                cursor_before={"start_date": options.start_date.isoformat()},
                cursor_after={
                    "end_date": options.end_date.isoformat(),
                    "discovered_final_games": discovered,
                },
                attempt_count=executor.accounting.attempts,
                retry_count=executor.accounting.retries,
                records_received=len(schedule_rows),
                raw_references=(schedule_archive.raw_file_reference,),
                raw_checksums=(_required_file_checksum(schedule_archive),),
                code_revision="gate3a-a1-core-v1",
                quality_summary={
                    "archive_status": "SUCCEEDED",
                    "canonical_status": "NOT_ATTEMPTED_DISCOVERY_ONLY",
                    "execution_scope": "DISCOVERY_ARCHIVE_ONLY",
                    "source_mode": "LIVE",
                    "research_provenance": "HISTORICAL_RECONSTRUCTION",
                    "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                    "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
                    "discovered_final_games": discovered,
                    "required_full_run_requests": discovered + 1,
                    "request_accounting": executor.accounting.as_dict(),
                    "checksum_semantics": {
                        "raw_checksums": "SHA256_ARCHIVE_FILE_BYTES",
                        "payload_checksums": [schedule_archive.payload_checksum],
                    },
                },
            )
            self.checkpoints.append(checkpoint)
            return HistoricalBackfillResult(
                status="DISCOVERED",
                reason=(
                    f"Schedule discovery found {discovered} final regular-season games; "
                    f"authorize exactly {discovered + 1} calls for the full run."
                ),
                plan=plan,
                accounting=executor.accounting.as_dict(),
                chunks_completed=0,
                chunks_skipped=0,
                records_received=len(schedule_rows),
                raw_bytes=schedule_archive.bytes_written or 0,
                checkpoints_written=1,
                run_id=run_id,
                discovered_final_games=discovered,
                required_full_run_requests=discovered + 1,
                completion_scope=plan.execution_scope,
            )
        if discovered != options.expected_final_games:
            return HistoricalBackfillResult(
                status="BLOCKED",
                reason=(
                    f"Expected {options.expected_final_games} final games but schedule returned "
                    f"{discovered}; no game feed or canonical persistence was attempted."
                ),
                plan=plan,
                accounting=executor.accounting.as_dict(),
                chunks_completed=0,
                chunks_skipped=0,
                records_received=len(schedule_rows),
                raw_bytes=schedule_archive.bytes_written or 0,
                checkpoints_written=0,
                run_id=run_id,
                discovered_final_games=discovered,
                required_full_run_requests=discovered + 1,
                completion_scope=plan.execution_scope,
            )

        source_version = None
        if self.canonical_writer is not None:
            prepare_source = getattr(
                self.canonical_writer,
                "prepare_source_version",
                self.canonical_writer.ensure_source_version,
            )
            source_version = prepare_source(
                dataset_version="historical-core-v1",
                policy_version="gate3a-a1-core-v1",
                effective_from=schedule_response.envelope.fetched_at,
                governance=self.governance,
            )

        completed = skipped = received = bytes_written = checkpoints_written = 0
        canonical_accepted = canonical_games = canonical_finals = canonical_teams = canonical_players = 0
        bytes_written += schedule_archive.bytes_written or 0
        final_by_date: dict[date, list[HistoricalScheduleGameDTO]] = {}
        for row in final_rows:
            final_by_date.setdefault(row.official_date, []).append(row)

        for sequence, (chunk_start, chunk_end) in enumerate(
            _chunks(options.start_date, options.end_date, options.chunk_size)
        ):
            fingerprint = request_fingerprint(
                options.source,
                self.STREAM_KEY,
                {
                    "season": options.season,
                    "start_date": chunk_start.isoformat(),
                    "end_date": chunk_end.isoformat(),
                    "game_type": "R",
                    "policy_version": "gate3a-a1-core-v1",
                    # The OBSERVATION this chunk canonicalises, not just the
                    # request for it. Without this the chunk key had the same
                    # flaw the archive path had: a day whose schedule response
                    # changed -- more games final, or an official correction --
                    # was treated as one completed unit, so the later
                    # observation could never be canonicalised and the games
                    # that finished after the first pass got no governed final.
                    #
                    # Re-running over UNCHANGED bytes still resolves to the same
                    # fingerprint and is still skipped as completed, so replay
                    # stays idempotent and spends no provider budget.
                    "schedule_payload_checksum": schedule_archive.payload_checksum,
                },
            )
            previous = self.checkpoints.completed(
                self.SOURCE_VERSION_ID, self.STREAM_KEY, fingerprint
            )
            if previous is not None:
                if not options.resume:
                    return HistoricalBackfillResult(
                        status="BLOCKED",
                        reason="A completed semantic chunk exists; rerun with --resume.",
                        plan=plan,
                        accounting=executor.accounting.as_dict(),
                        chunks_completed=completed,
                        chunks_skipped=skipped,
                        records_received=received,
                        raw_bytes=bytes_written,
                        checkpoints_written=checkpoints_written,
                        run_id=run_id,
                        discovered_final_games=discovered,
                        required_full_run_requests=discovered + 1,
                        completion_scope=plan.execution_scope,
                        canonical_records_accepted=canonical_accepted,
                        canonical_games=canonical_games,
                        canonical_finals=canonical_finals,
                        canonical_team_observations=canonical_teams,
                        canonical_player_observations=canonical_players,
                    )
                skipped += 1
                continue

            chunk_rows = [
                row
                for day in _dates(chunk_start, chunk_end)
                for row in final_by_date.get(day, [])
            ]
            if not chunk_rows:
                skipped += 1
                continue
            # The first persisted request-accounting delta includes the range
            # schedule call, so its checkpoint window must begin when that
            # schedule request began. Later chunks begin at their own feed
            # request boundary.
            requested_at = (
                schedule_requested_at
                if int(accounting_cursor["attempts"]) == 0
                else self.now()
            )
            attempts_before = executor.accounting.attempts
            retries_before = executor.accounting.retries
            raw_records: list[RawArchiveRecord] = [schedule_archive]
            merged_rows: list[HistoricalScheduleGameDTO] = []
            boxscores = []
            chunk_evidence_class = "RECONSTRUCTABLE_POINT_IN_TIME"
            try:
                for row in chunk_rows:
                    game_id = row.game.source_game_id
                    response: AdapterResponse | None = None
                    try:
                        response = await adapter.get_historical_game_evidence(game_id)
                        _validate_response(
                            response,
                            expected_source=options.source,
                            expected_class=A1_GAME_FEED_EVIDENCE_CLASSES,
                            context=f"game feed {game_id}",
                        )
                        evidence = HistoricalGameEvidenceDTO.model_validate(
                            response.envelope.data
                        )
                        if (
                            evidence.evidence_class
                            != response.envelope.metadata.get("evidence_class")
                        ):
                            raise SourceError(
                                options.source,
                                f"Historical game feed {game_id} evidence_class "
                                "conflicts with its payload",
                                retryable=False,
                            )
                        _validate_game_evidence(evidence, row)
                    except Exception as exc:
                        raw_payload = (
                            response.raw_payload
                            if response is not None
                            else getattr(exc, "raw_payload", None)
                        )
                        if isinstance(raw_payload, (dict, list)):
                            raw_records.append(self.archive.write_historical(
                                options.source,
                                raw_payload,
                                partition_date=row.official_date,
                                request_fingerprint=request_fingerprint(
                                    options.source,
                                    "mlb_historical_game_feed_unvalidated",
                                    {
                                        "source_game_id": game_id,
                                        "chunk_fingerprint": fingerprint,
                                        "ingestion_run_id": run_id,
                                        "policy_version": "gate3a-a1-core-v1",
                                    },
                                ),
                                source_event_timestamp=None,
                                status="unvalidated_error",
                                schema_version=(
                                    "mlb_statsapi_v1_1_historical_game_feed_unvalidated_v1"
                                ),
                                source_mode="LIVE",
                                research_provenance="HISTORICAL_RECONSTRUCTION",
                                temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                                evidence_class="PARTIALLY_RECONSTRUCTABLE",
                                checkpoint_id=fingerprint,
                                run_id=run_id,
                            ))
                        raise
                    if evidence.evidence_class == "PARTIALLY_RECONSTRUCTABLE":
                        # Evidence can only move toward the more conservative
                        # class as games are merged into a chunk; a later Tier
                        # A game must never upgrade an earlier Tier B game.
                        chunk_evidence_class = "PARTIALLY_RECONSTRUCTABLE"
                    merged_rows.append(row.model_copy(update={
                        "final": evidence.final,
                        "acquired_at": evidence.acquired_at,
                        "evidence_class": evidence.evidence_class,
                        "game": row.game.model_copy(update={
                            "away_score": evidence.final.away_score,
                            "home_score": evidence.final.home_score,
                            "final_at": evidence.final.event_completed_at,
                        }),
                    }))
                    boxscores.append(evidence.boxscore)
                    feed_fingerprint = request_fingerprint(
                        options.source,
                        "mlb_historical_game_feed",
                        {
                            "source_game_id": game_id,
                            "policy_version": "gate3a-a1-core-v1",
                        },
                    )
                    raw_records.append(self.archive.write_historical(
                        response.envelope.source,
                        response.raw_payload,
                        partition_date=row.official_date,
                        request_fingerprint=feed_fingerprint,
                        source_event_timestamp=evidence.final.event_completed_at,
                        schema_version="mlb_statsapi_v1_1_historical_game_feed_v1",
                        source_mode="LIVE",
                        research_provenance="HISTORICAL_RECONSTRUCTION",
                        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                        evidence_class=evidence.evidence_class,
                        checkpoint_id=fingerprint,
                        run_id=run_id,
                    ))

                canonical_result = None
                persisted_accounting: dict[str, Any] | None = None
                if self.canonical_writer is not None:
                    persisted_accounting = _accounting_delta(
                        accounting_cursor,
                        _accounting_snapshot(executor.accounting),
                    )
                    canonical_result = self.canonical_writer.persist_chunk(
                        schedule_rows=merged_rows,
                        boxscores=boxscores,
                        source_version=source_version,
                        stream_key=self.STREAM_KEY,
                        sequence=sequence,
                        query_fingerprint=fingerprint,
                        requested_at=requested_at,
                        completed_at=self.now(),
                        raw_references=[item.raw_file_reference for item in raw_records],
                        raw_checksums=[_required_file_checksum(item) for item in raw_records],
                        code_revision="gate3a-a1-core-v1",
                        attempt_count=int(persisted_accounting["attempts"]),
                        retry_count=int(persisted_accounting["retries"]),
                        request_accounting=persisted_accounting,
                    )
                    accounting_cursor = _accounting_snapshot(executor.accounting)
                    canonical_accepted += int(
                        getattr(canonical_result, "records_accepted", 0)
                    )
                    canonical_games += int(getattr(canonical_result, "games", 0))
                    canonical_finals += int(getattr(canonical_result, "final_outcomes", 0))
                    canonical_teams += int(getattr(canonical_result, "team_observations", 0))
                    canonical_players += int(getattr(canonical_result, "player_observations", 0))

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
                    attempt_count=(
                        int(persisted_accounting["attempts"])
                        if persisted_accounting is not None
                        else executor.accounting.attempts - attempts_before
                    ),
                    retry_count=(
                        int(persisted_accounting["retries"])
                        if persisted_accounting is not None
                        else executor.accounting.retries - retries_before
                    ),
                    records_received=len(merged_rows) + len(boxscores),
                    records_accepted=(
                        int(getattr(canonical_result, "records_accepted", 0))
                        if canonical_result else 0
                    ),
                    records_rejected=(
                        int(getattr(canonical_result, "records_rejected", 0))
                        if canonical_result else 0
                    ),
                    duplicate_count=(
                        int(getattr(canonical_result, "duplicates", 0))
                        if canonical_result else 0
                    ),
                    raw_references=tuple(item.raw_file_reference for item in raw_records),
                    raw_checksums=tuple(_required_file_checksum(item) for item in raw_records),
                    next_resume_at=datetime.combine(chunk_end + timedelta(days=1), time.min, tzinfo=UTC),
                    quality_summary={
                        "archive_status": "SUCCEEDED",
                        "canonical_status": "SUCCEEDED" if canonical_result else "PENDING",
                        "source_mode": "LIVE",
                        "research_provenance": "HISTORICAL_RECONSTRUCTION",
                        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                        "evidence_class": chunk_evidence_class,
                        "completion_time_bases": sorted({
                            row.final.completion_time_basis
                            for row in merged_rows
                            if row.final is not None and row.final.completion_time_basis is not None
                        }),
                        "provider_attempts_by_operation": executor.accounting.attempts_by_operation,
                        "checksum_semantics": {
                            "raw_checksums": "SHA256_ARCHIVE_FILE_BYTES",
                            "payload_checksums": [item.payload_checksum for item in raw_records],
                        },
                    },
                )
                self.checkpoints.append(checkpoint)
                accounting_cursor = _accounting_snapshot(executor.accounting)
                checkpoints_written += 1
                completed += 1
                received += len(merged_rows) + len(boxscores)
                bytes_written += sum(
                    item.bytes_written or 0 for item in raw_records[1:]
                )
            except Exception as exc:
                return self._chunk_failure(
                    exc=exc,
                    options=options,
                    plan=plan,
                    executor=executor,
                    run_id=run_id,
                    sequence=sequence,
                    chunk_start=chunk_start,
                    chunk_end=chunk_end,
                    fingerprint=fingerprint,
                    requested_at=requested_at,
                    attempts_before=attempts_before,
                    retries_before=retries_before,
                    raw_records=raw_records,
                    completed=completed,
                    skipped=skipped,
                    received=received,
                    bytes_written=bytes_written,
                    checkpoints_written=checkpoints_written,
                    discovered=discovered,
                    canonical_accepted=canonical_accepted,
                    canonical_games=canonical_games,
                    canonical_finals=canonical_finals,
                    canonical_teams=canonical_teams,
                    canonical_players=canonical_players,
                    accounting_cursor=accounting_cursor,
                )

        canonical = self.canonical_writer is not None
        return HistoricalBackfillResult(
            status="COMPLETED" if canonical else "ARCHIVED_CANONICAL_PENDING",
            reason=(
                "MLB core evidence was archived and canonicalized; no Statcast, Odds, "
                "weather, materialization, training, or promotion ran."
                if canonical else
                "MLB core evidence was archived; canonical persistence was not configured."
            ),
            plan=plan,
            accounting=executor.accounting.as_dict(),
            chunks_completed=completed,
            chunks_skipped=skipped,
            records_received=received,
            raw_bytes=bytes_written,
            checkpoints_written=checkpoints_written,
            run_id=run_id,
            discovered_final_games=discovered,
            required_full_run_requests=discovered + 1,
            completion_scope=plan.execution_scope,
            canonical_records_accepted=canonical_accepted,
            canonical_games=canonical_games,
            canonical_finals=canonical_finals,
            canonical_team_observations=canonical_teams,
            canonical_player_observations=canonical_players,
        )

    def _empty_result(
        self,
        plan: HistoricalBackfillPlan,
        options: HistoricalBackfillOptions,
        run_id: str,
    ) -> HistoricalBackfillResult:
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
            completion_scope=plan.execution_scope,
        )

    def _schedule_failure(
        self,
        exc: BaseException,
        plan: HistoricalBackfillPlan,
        options: HistoricalBackfillOptions,
        accounting: RequestAccounting,
        run_id: str,
        requested_at: datetime,
        schedule_archive: RawArchiveRecord | None = None,
    ) -> HistoricalBackfillResult:
        raw_records: list[RawArchiveRecord] = (
            [schedule_archive] if schedule_archive is not None else []
        )
        raw_payload = getattr(exc, "raw_payload", None)
        if not raw_records and isinstance(raw_payload, (dict, list)):
            raw_records.append(self.archive.write_historical(
                options.source,
                raw_payload,
                partition_date=options.start_date,
                request_fingerprint=request_fingerprint(
                    options.source,
                    "mlb_historical_schedule_range_schema_failure",
                    {"start": options.start_date.isoformat(), "end": options.end_date.isoformat()},
                ),
                status="schema_failure",
                schema_version="mlb_statsapi_historical_unvalidated_v1",
                source_mode="LIVE",
                research_provenance="HISTORICAL_RECONSTRUCTION",
                temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                evidence_class="PARTIALLY_RECONSTRUCTABLE",
                run_id=run_id,
            ))
        fingerprint = request_fingerprint(
            options.source,
            self.STREAM_KEY,
            {
                "start_date": options.start_date.isoformat(),
                "end_date": options.end_date.isoformat(),
                "stage": "schedule",
                "policy_version": "gate3a-a1-core-v1",
            },
        )
        checkpoint = HistoricalCheckpoint(
            source_version_id=self.SOURCE_VERSION_ID,
            stream_key=self.STREAM_KEY,
            query_fingerprint=fingerprint,
            status="FAILED",
            requested_at=requested_at,
            completed_at=self.now(),
            season=options.season,
            event_start_at=datetime.combine(options.start_date, time.min, tzinfo=UTC),
            event_end_at=datetime.combine(options.end_date, time.max, tzinfo=UTC),
            sequence=0,
            ingestion_run_id=run_id,
            attempt_count=accounting.attempts,
            retry_count=accounting.retries,
            records_received=0,
            raw_references=tuple(item.raw_file_reference for item in raw_records),
            raw_checksums=tuple(_required_file_checksum(item) for item in raw_records),
            next_resume_at=datetime.combine(options.start_date, time.min, tzinfo=UTC),
            error_type=type(exc).__name__,
            error_message=_safe_error(exc),
            quality_summary={
                "archive_status": (
                    "SUCCEEDED_SEMANTIC_REJECTION"
                    if schedule_archive is not None
                    else "UNVALIDATED_ERROR_PAYLOAD" if raw_records else "NOT_WRITTEN"
                ),
                "canonical_status": "NOT_ATTEMPTED",
                "source_mode": "LIVE",
                "research_provenance": "HISTORICAL_RECONSTRUCTION",
                "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                "evidence_class": "PARTIALLY_RECONSTRUCTABLE",
                "provider_attempts_by_operation": accounting.attempts_by_operation,
            },
        )
        self.checkpoints.append(checkpoint)
        return HistoricalBackfillResult(
            status="BLOCKED",
            reason=_safe_error(exc),
            plan=plan,
            accounting=accounting.as_dict(),
            chunks_completed=0,
            chunks_skipped=0,
            records_received=0,
            raw_bytes=sum(item.bytes_written or 0 for item in raw_records),
            checkpoints_written=1,
            run_id=run_id,
            completion_scope=plan.execution_scope,
        )

    def _chunk_failure(self, **values: Any) -> HistoricalBackfillResult:
        exc = values["exc"]
        raw_records = values["raw_records"]
        has_unvalidated_error = any(
            item.status == "unvalidated_error" for item in raw_records
        )
        failed_accounting = _accounting_delta(
            values["accounting_cursor"],
            _accounting_snapshot(values["executor"].accounting),
        )
        checkpoint = HistoricalCheckpoint(
            source_version_id=self.SOURCE_VERSION_ID,
            stream_key=self.STREAM_KEY,
            query_fingerprint=values["fingerprint"],
            status="FAILED",
            requested_at=values["requested_at"],
            completed_at=self.now(),
            season=values["options"].season,
            event_start_at=datetime.combine(values["chunk_start"], time.min, tzinfo=UTC),
            event_end_at=datetime.combine(values["chunk_end"], time.max, tzinfo=UTC),
            sequence=values["sequence"],
            ingestion_run_id=values["run_id"],
            attempt_count=int(failed_accounting["attempts"]),
            retry_count=int(failed_accounting["retries"]),
            records_received=0,
            raw_references=tuple(item.raw_file_reference for item in raw_records),
            raw_checksums=tuple(_required_file_checksum(item) for item in raw_records),
            next_resume_at=datetime.combine(values["chunk_start"], time.min, tzinfo=UTC),
            error_type=type(exc).__name__,
            error_message=_safe_error(exc),
            quality_summary={
                "archive_status": (
                    "UNVALIDATED_ERROR_PAYLOAD"
                    if has_unvalidated_error
                    else "PARTIAL" if len(raw_records) > 1 else "SCHEDULE_ONLY"
                ),
                "canonical_status": "FAILED_OR_NOT_ATTEMPTED",
                "source_mode": "LIVE",
                "research_provenance": "HISTORICAL_RECONSTRUCTION",
                "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                "evidence_class": "PARTIALLY_RECONSTRUCTABLE",
                "request_accounting": failed_accounting,
            },
        )
        self.checkpoints.append(checkpoint)
        return HistoricalBackfillResult(
            status="PARTIAL" if values["completed"] else "BLOCKED",
            reason=_safe_error(exc),
            plan=values["plan"],
            accounting=values["executor"].accounting.as_dict(),
            chunks_completed=values["completed"],
            chunks_skipped=values["skipped"],
            records_received=values["received"],
            raw_bytes=values["bytes_written"] + sum(
                item.bytes_written or 0 for item in raw_records[1:]
            ),
            checkpoints_written=values["checkpoints_written"] + 1,
            run_id=values["run_id"],
            discovered_final_games=values["discovered"],
            required_full_run_requests=values["discovered"] + 1,
            completion_scope=values["plan"].execution_scope,
            canonical_records_accepted=values["canonical_accepted"],
            canonical_games=values["canonical_games"],
            canonical_finals=values["canonical_finals"],
            canonical_team_observations=values["canonical_teams"],
            canonical_player_observations=values["canonical_players"],
        )


def build_default_backfill_runner(
    raw_data_root: Path,
    *,
    canonical_writer: HistoricalCanonicalWriter | None = None,
    governance: dict[str, Any] | None = None,
) -> HistoricalBackfillRunner:
    return HistoricalBackfillRunner(
        archive=RawArchive(raw_data_root),
        checkpoints=JsonCheckpointRepository(
            raw_data_root / "historical" / "_checkpoints" / "mlb_history.jsonl"
        ),
        canonical_writer=canonical_writer,
        governance=governance,
    )


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


def _validate_response(
    response: AdapterResponse,
    *,
    expected_source: str,
    expected_class: str | Sequence[str],
    context: str,
) -> None:
    envelope = response.envelope
    if envelope.source != expected_source:
        raise SourceError(expected_source, f"Historical {context} source does not match", retryable=False)
    if envelope.source_mode != "LIVE":
        raise SourceError(expected_source, f"Historical {context} source_mode must be LIVE", retryable=False)
    expected_metadata = {
        "research_provenance": "HISTORICAL_RECONSTRUCTION",
        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
    }
    for key, expected in expected_metadata.items():
        if envelope.metadata.get(key) != expected:
            raise SourceError(
                expected_source,
                f"Historical {context} {key} is missing or invalid",
                retryable=False,
            )
    allowed_classes = (
        {expected_class}
        if isinstance(expected_class, str)
        else set(expected_class)
    )
    if envelope.metadata.get("evidence_class") not in allowed_classes:
        raise SourceError(
            expected_source,
            f"Historical {context} evidence_class is missing or invalid",
            retryable=False,
        )


def _validated_schedule_rows(
    rows: Sequence[HistoricalScheduleGameDTO],
    options: HistoricalBackfillOptions,
) -> list[HistoricalScheduleGameDTO]:
    """Validate identities and collapse provider reschedule duplicates.

    MLB range schedules can repeat one ``gamePk`` in its original postponed
    bucket and its later completed bucket. The raw payload is retained in full;
    canonical acquisition selects the single completed version. Conflicting
    completed versions still fail closed.
    """

    grouped: dict[str, list[HistoricalScheduleGameDTO]] = {}
    for row in rows:
        game_id = row.game.source_game_id
        source_schedule_date = row.source_schedule_date or row.official_date
        if (
            row.season != options.season
            or not options.start_date <= source_schedule_date <= options.end_date
        ):
            raise SourceError(options.source, "Schedule row is outside authorized range", retryable=False)
        if row.game_type != "R":
            raise SourceError(options.source, "Schedule row is not regular season", retryable=False)
        # A postponed representation may be returned in the requested bucket
        # with its eventual makeup ``officialDate`` outside the authorized
        # range. Preserve it in raw evidence but do not acquire that future
        # game under this run's request budget.
        if not options.start_date <= row.official_date <= options.end_date:
            if _is_schedule_final(row):
                raise SourceError(
                    options.source,
                    "Completed schedule row is outside authorized official-date range",
                    retryable=False,
                )
            continue
        grouped.setdefault(game_id, []).append(row)

    selected: list[HistoricalScheduleGameDTO] = []
    for game_id, versions in grouped.items():
        identity = {
            (
                row.game.away_team.source_id,
                row.game.home_team.source_id,
                row.season,
                row.game_type,
            )
            for row in versions
        }
        if len(identity) != 1:
            raise SourceError(
                options.source,
                f"Conflicting schedule identities for game ID {game_id}",
                retryable=False,
            )
        finals = [row for row in versions if _is_schedule_final(row)]
        if len(finals) == 1:
            if len(versions) > 1:
                completed = finals[0]
                siblings = [row for row in versions if row is not completed]
                if any(
                    row.coded_state != "D"
                    or str(row.game.status).strip().lower() != "postponed"
                    or row.game.home_score is not None
                    or row.game.away_score is not None
                    for row in siblings
                ) or not any(
                    completed.game.rescheduled_from == row.game.scheduled_at
                    for row in siblings
                ):
                    raise SourceError(
                        options.source,
                        f"Unlinked postponed/completed schedule versions for game ID {game_id}",
                        retryable=False,
                    )
            selected.append(finals[0])
            continue
        if len(finals) > 1:
            semantic = {
                _completed_schedule_semantic(row)
                for row in finals
            }
            if len(semantic) != 1:
                raise SourceError(
                    options.source,
                    f"Conflicting completed schedule versions for game ID {game_id}",
                    retryable=False,
                )
            linked_originals = [
                original
                for original in finals
                if original.game.resume_at is not None
                and any(
                    resumed is not original
                    and resumed.game.scheduled_at == original.game.resume_at
                    and resumed.game.resumed_from_date == original.official_date
                    for resumed in finals
                )
            ]
            if len(linked_originals) != 1:
                raise SourceError(
                    options.source,
                    f"Unlinked suspended/resumed schedule versions for game ID {game_id}",
                    retryable=False,
                )
            # Preserve the actual original first pitch as the pregame anchor;
            # the final feed supplies terminal completion evidence.
            selected.append(linked_originals[0])
            continue
        selected.append(max(
            versions,
            key=lambda row: (
                row.game.scheduled_at,
                row.official_date,
                row.game.game_number or 0,
                row.coded_state or "",
            ),
        ))
    return selected


def _is_schedule_final(row: HistoricalScheduleGameDTO) -> bool:
    return (
        str(row.abstract_state or "").strip().lower() == "final"
        and str(row.coded_state or "").strip().upper() == "F"
        and row.game.home_score is not None
        and row.game.away_score is not None
    )


def _completed_schedule_semantic(row: HistoricalScheduleGameDTO) -> str:
    return json.dumps(
        {
            "source_game_id": row.game.source_game_id,
            "season": row.season,
            "official_date": row.official_date.isoformat(),
            "game_type": row.game_type,
            "away_team_id": row.game.away_team.source_id,
            "home_team_id": row.game.home_team.source_id,
            "away_score": row.game.away_score,
            "home_score": row.game.home_score,
            "venue_id": row.game.venue.source_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_game_evidence(
    evidence: HistoricalGameEvidenceDTO,
    schedule: HistoricalScheduleGameDTO,
) -> None:
    game_id = schedule.game.source_game_id
    if evidence.source_game_id != game_id:
        raise SourceError("MLB_STATS_API", "Game-feed identity does not match schedule", retryable=False)
    if evidence.final.source_game_id != game_id or evidence.boxscore.source_game_id != game_id:
        raise SourceError("MLB_STATS_API", "Nested game-feed identity does not match", retryable=False)
    if evidence.final.official_date != schedule.official_date:
        raise SourceError("MLB_STATS_API", "Game-feed official date does not match schedule", retryable=False)
    if evidence.evidence_class not in A1_GAME_FEED_EVIDENCE_CLASSES:
        raise SourceError("MLB_STATS_API", "A1 completed-game evidence class is invalid", retryable=False)
    nested_classes = {
        evidence.final.evidence_class,
        evidence.boxscore.evidence_class,
        *(item.evidence_class for item in evidence.boxscore.teams),
        *(item.evidence_class for item in evidence.boxscore.players),
    }
    if nested_classes != {evidence.evidence_class}:
        raise SourceError(
            "MLB_STATS_API",
            "A1 nested game-feed evidence classes conflict",
            retryable=False,
        )
    basis = evidence.final.completion_time_basis
    source_field = str(evidence.final.completion_time_source_field or "")
    if evidence.evidence_class == "RECONSTRUCTABLE_POINT_IN_TIME":
        expected_source_fields = {
            "AUTHORITATIVE_FINAL_AT": "gameData.datetime.gameEndTime",
            "DERIVED_LAST_EVENT_COMPLETION_PROXY": (
                "liveData.plays.allPlays[-1].about.endTime"
            ),
        }
        if basis not in expected_source_fields:
            raise SourceError("MLB_STATS_API", "A1 completion basis is invalid", retryable=False)
        if source_field != expected_source_fields[basis]:
            raise SourceError("MLB_STATS_API", "A1 completion source field is invalid", retryable=False)
    else:
        prefix = "liveData.plays.allPlays["
        suffix = "].about.endTime"
        play_index = (
            source_field[len(prefix):-len(suffix)]
            if source_field.startswith(prefix) and source_field.endswith(suffix)
            else ""
        )
        if basis != "DERIVED_LAST_EVENT_COMPLETION_PROXY" or not play_index.isdigit():
            raise SourceError(
                "MLB_STATS_API",
                "A1 partial completion source field is invalid",
                retryable=False,
            )
        method = str(evidence.final.completion_time_method or "").casefold()
        limitation = str(evidence.final.completion_time_limitation or "").casefold()
        if (
            "terminal rain-delay advisory" not in method
            or "precedes the terminal rain decision" not in limitation
        ):
            raise SourceError(
                "MLB_STATS_API",
                "A1 partial completion provenance is invalid",
                retryable=False,
            )
    if evidence.final.event_completed_at is None:
        raise SourceError("MLB_STATS_API", "A1 completion timestamp is missing", retryable=False)


def _free_space(path: Path) -> int:
    existing = Path(path)
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    return shutil.disk_usage(existing).free


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, SourceError):
        return str(exc)[:500]
    return f"Historical backfill failed: {type(exc).__name__}"


def _required_file_checksum(record: RawArchiveRecord) -> str:
    if record.file_checksum is None:
        raise RuntimeError("Historical archive record lacks an archive-file checksum")
    return record.file_checksum


def _accounting_snapshot(accounting: RequestAccounting) -> dict[str, Any]:
    """Capture cumulative physical-request counters for exact chunk deltas."""

    attempts = dict(accounting.attempts_by_operation or {})
    successes = dict(accounting.successes_by_operation or {})
    failures = dict(accounting.failed_attempts_by_operation or {})
    operation_names = sorted(set(attempts) | set(successes) | set(failures))
    return {
        "attempts": int(accounting.attempts),
        "retries": int(accounting.retries),
        "successes": int(accounting.successes),
        "failed_attempts": int(accounting.failed_attempts),
        "operations": {
            name: {
                "attempts": int(attempts.get(name, 0)),
                "successes": int(successes.get(name, 0)),
                "failed_attempts": int(failures.get(name, 0)),
            }
            for name in operation_names
        },
    }


def _accounting_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Subtract two monotonic request snapshots without inventing activity."""

    operation_names = sorted(
        set(before.get("operations", {})) | set(after.get("operations", {}))
    )
    operations: dict[str, dict[str, int]] = {}
    for name in operation_names:
        previous = before.get("operations", {}).get(name, {})
        current = after.get("operations", {}).get(name, {})
        values = {
            key: int(current.get(key, 0)) - int(previous.get(key, 0))
            for key in ("attempts", "successes", "failed_attempts")
        }
        if any(value < 0 for value in values.values()):
            raise ValueError("Request accounting counters must be monotonic")
        if values["attempts"]:
            operations[name] = values
    result: dict[str, Any] = {
        key: int(after.get(key, 0)) - int(before.get(key, 0))
        for key in ("attempts", "retries", "successes", "failed_attempts")
    }
    if any(value < 0 for value in result.values()):
        raise ValueError("Request accounting totals must be monotonic")
    result["operations"] = operations
    return result
