"""Provider-free canonical persistence for governed MLB historical evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.data_sources.base import HistoricalBoxscoreDTO, HistoricalScheduleGameDTO
from app.models import (
    ExternalIdMapping,
    Game,
    GameFinalOutcome,
    GameStatus,
    GameTeam,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    IngestionRun,
    Player,
    PlayerGameHistoryObservation,
    RunStatus,
    Team,
    TeamGameHistoryObservation,
    Venue,
)
from app.ingestion.normalizer import CanonicalNormalizer


TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"
A1_DATASET_VERSION = "historical-core-v1"
A1_POLICY_VERSION = "gate3a-a1-core-v1"
A1_GOVERNANCE_DECISION: dict[str, Any] = {
    "approval_status": "APPROVED",
    "reviewer": "project owner",
    "reviewed_at": "2026-08-13",
    "scope": "LOCAL_PRIVATE_RESEARCH",
    "purpose": "local ASP research/model development",
    "population": "HISTORICAL_RECONSTRUCTION",
    "public_redistribution": "PROHIBITED_PENDING_REVIEW",
    "commercial_deployment": "NOT_APPROVED",
    "raw_data_resale": "PROHIBITED",
    "technical_access_restrictions": "RESPECT_REQUIRED",
    "attribution_licensing": "FINAL_REVIEW_REQUIRED",
    "disclaimer": "Owner authorization; not legal advice or a legal determination",
}


#: The result facts a final actually asserts. Everything else in the DTO is
#: metadata about the READ -- when it was fetched, by which run -- and including
#: any of it in the identity is what turned one game into a stream of
#: "corrections" that all said the same thing. The nflverse path has always
#: drawn this line; the MLB path did not, and eleven revisions on 2026-09-13 are
#: the evidence: byte-identical scores, innings and final_at, differing only in
#: retrieval clocks.
_RETRIEVAL_METADATA = frozenset({"acquired_at"})


def _result_content(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload with retrieval metadata removed, for identity purposes."""

    return {key: value for key, value in payload.items()
            if key not in _RETRIEVAL_METADATA}


class HistoricalPersistenceError(RuntimeError):
    """Raised when a chunk cannot be persisted without guessing identity."""


@dataclass(frozen=True)
class HistoricalChunkResult:
    checkpoint_id: str
    ingestion_run_id: str
    status: str
    games: int
    final_outcomes: int
    team_observations: int
    player_observations: int
    duplicates: int
    records_received: int
    records_accepted: int
    records_rejected: int
    canonical_duplicate_facts: int


class HistoricalCanonicalPersistenceService:
    """Atomically normalizes one already-validated historical evidence chunk.

    All canonical lookups use exact provider IDs. Historical occurrence and
    eligibility timestamps remain distinct from the actual acquisition time.
    Failed chunks roll back every canonical fact and persist one terminal
    FAILED checkpoint in a fresh transaction.
    """

    def __init__(self, session: Session, *, source: str = "MLB_STATS_API") -> None:
        self.session = session
        self.source = source

    def ensure_source_version(
        self,
        *,
        dataset_version: str = "historical-schedule-v1",
        policy_version: str = "gate3a-six-hour-v1",
        effective_from: datetime | None = None,
        governance: dict[str, Any] | None = None,
    ) -> HistoricalSourceVersion:
        if dataset_version == A1_DATASET_VERSION and governance != A1_GOVERNANCE_DECISION:
            raise HistoricalPersistenceError(
                "A1 governance decision does not match the approved local research scope"
            )
        approved = _governance_is_approved(governance)
        existing = self.session.scalar(
            select(HistoricalSourceVersion).where(
                HistoricalSourceVersion.source_key == self.source,
                HistoricalSourceVersion.dataset_version == dataset_version,
                HistoricalSourceVersion.source_mode == "LIVE",
            )
        )
        if existing is not None:
            if (
                existing.policy_version != policy_version
                or bool(existing.training_eligible) != approved
                or bool(existing.evaluation_eligible) != approved
                or (existing.governance or None) != (governance or None)
            ):
                raise HistoricalPersistenceError(
                    "Existing historical source-version governance differs; "
                    "use a new dataset_version for a new immutable decision"
                )
            return existing
        effective = _aware(effective_from or datetime.now(UTC))
        availability_policy = {
            "historical_acquisition_is_not_backdated": True,
            "completion_time_bases": [
                "AUTHORITATIVE_FINAL_AT",
                "DERIVED_LAST_EVENT_COMPLETION_PROXY",
            ],
            "completion_evidence_metadata_required": True,
            "evidence_tier_mapping": {
                "RECONSTRUCTABLE_POINT_IN_TIME": "A",
                "PARTIALLY_RECONSTRUCTABLE": "B",
                "FORWARD_ONLY": "EXCLUDED",
            },
            "forbidden_completion_substitutes": [
                "FIRST_PITCH",
                "RETRIEVAL_TIME",
                "END_OF_DAY",
                "MIDNIGHT",
                "ESTIMATED_DURATION",
            ],
            "target_actual_participation_is_pregame_unknown": True,
            "population": "HISTORICAL_RECONSTRUCTION",
        }
        identity = _checksum({
            "source_key": self.source,
            "dataset_version": dataset_version,
            "source_mode": "LIVE",
            "temporal_semantics": TEMPORAL_SEMANTICS,
            "policy_version": policy_version,
            "training_eligible": approved,
            "evaluation_eligible": approved,
            "availability_policy": availability_policy,
            "governance": governance,
        })
        source_version = HistoricalSourceVersion(
            source_key=self.source,
            dataset_version=dataset_version,
            provider_name="MLB Stats API",
            source_mode="LIVE",
            source_classification="OFFICIAL_PRIMARY",
            temporal_semantics=TEMPORAL_SEMANTICS,
            quality_tier="A",
            training_eligible=approved,
            evaluation_eligible=approved,
            availability_policy=availability_policy,
            policy_version=policy_version,
            governance=governance,
            effective_from=effective,
            identity_checksum=identity,
        )
        self.session.add(source_version)
        self.session.flush()
        return source_version

    def ensure_a1_source_version(
        self,
        *,
        effective_from: datetime | None = None,
        governance: dict[str, Any] | None = None,
    ) -> HistoricalSourceVersion:
        """Register the separately governed A1 historical research population."""

        decision = governance or A1_GOVERNANCE_DECISION
        if decision != A1_GOVERNANCE_DECISION:
            raise HistoricalPersistenceError(
                "A1 governance decision does not match the approved local research scope"
            )
        return self.ensure_source_version(
            dataset_version=A1_DATASET_VERSION,
            policy_version=A1_POLICY_VERSION,
            effective_from=effective_from,
            governance=decision,
        )

    def prepare_source_version(self, **kwargs: Any) -> HistoricalSourceVersion:
        """Persist the immutable source registry before any chunk transaction.

        A failed canonical chunk deliberately rolls back all chunk facts before
        writing its terminal FAILED checkpoint. Committing the source registry
        here gives that failure checkpoint a durable, valid FK without allowing
        any chunk evidence to escape its atomic boundary.
        """

        if self.session.new or self.session.dirty or self.session.deleted:
            raise HistoricalPersistenceError(
                "Source-version preparation requires a clean transaction boundary"
            )
        try:
            source_version = self.ensure_source_version(**kwargs)
            self.session.commit()
            return source_version
        except Exception:
            self.session.rollback()
            raise

    def persist_chunk(
        self,
        *,
        schedule_rows: Sequence[HistoricalScheduleGameDTO],
        boxscores: Sequence[HistoricalBoxscoreDTO],
        source_version: HistoricalSourceVersion,
        stream_key: str,
        sequence: int,
        query_fingerprint: str,
        requested_at: datetime,
        completed_at: datetime,
        raw_references: Sequence[str] = (),
        raw_checksums: Sequence[str] = (),
        code_revision: str = "unknown",
        attempt_count: int,
        retry_count: int,
        request_accounting: dict[str, Any] | None = None,
    ) -> HistoricalChunkResult:
        requested = _aware(requested_at)
        completed = _aware(completed_at)
        if (
            source_version.source_mode != "LIVE"
            or source_version.temporal_semantics != TEMPORAL_SEMANTICS
        ):
            raise HistoricalPersistenceError("Historical source version provenance is invalid")
        if len(query_fingerprint) != 64:
            raise HistoricalPersistenceError("query_fingerprint must be a SHA-256 hex digest")
        if attempt_count < 1 or retry_count < 0 or retry_count >= attempt_count:
            raise HistoricalPersistenceError(
                "Canonical checkpoint requires exact positive attempts and retries < attempts"
            )
        accounting = _validate_request_accounting(
            request_accounting,
            attempt_count=attempt_count,
            retry_count=retry_count,
        )
        completed_checkpoint = self.session.scalar(
            select(HistoryBackfillCheckpoint).where(
                HistoryBackfillCheckpoint.source_version_id == source_version.id,
                HistoryBackfillCheckpoint.stream_key == stream_key,
                HistoryBackfillCheckpoint.query_fingerprint == query_fingerprint,
                HistoryBackfillCheckpoint.status == "SUCCEEDED",
            )
        )
        if completed_checkpoint is not None:
            summary = completed_checkpoint.quality_summary or {}
            return HistoricalChunkResult(
                checkpoint_id=str(completed_checkpoint.id),
                ingestion_run_id=str(completed_checkpoint.ingestion_run_id),
                status="SUCCEEDED",
                games=int(summary.get("games", 0)),
                final_outcomes=int(summary.get("final_outcomes", 0)),
                team_observations=int(summary.get("team_observations", 0)),
                player_observations=int(summary.get("player_observations", 0)),
                duplicates=int(completed_checkpoint.duplicate_count),
                records_received=int(completed_checkpoint.records_received),
                records_accepted=int(completed_checkpoint.records_accepted),
                records_rejected=int(completed_checkpoint.records_rejected),
                canonical_duplicate_facts=int(
                    summary.get("canonical_duplicate_facts", 0)
                ),
            )

        ingestion_run = IngestionRun(
            source=self.source,
            job_name=f"historical:{stream_key}",
            status=RunStatus.RUNNING,
            started_at=requested,
            records_processed=0,
            raw_reference=raw_references[0] if raw_references else None,
            schema_version="gate3a-history-v1",
            source_metadata={
                "source_version_id": str(source_version.id),
                "query_fingerprint": query_fingerprint,
                "temporal_semantics": TEMPORAL_SEMANTICS,
            },
        )
        self.session.add(ingestion_run)
        self.session.flush()
        try:
            event_dates = [row.official_date for row in schedule_rows]
            anchor = HistoryBackfillCheckpoint(
                ingestion_run_id=ingestion_run.id,
                source_version_id=source_version.id,
                stream_key=stream_key,
                sequence=sequence,
                season=(schedule_rows[0].season if schedule_rows else None),
                event_start_at=(
                    datetime.combine(min(event_dates), time.min, tzinfo=UTC)
                    if event_dates else None
                ),
                event_end_at=(
                    datetime.combine(max(event_dates), time.max, tzinfo=UTC)
                    if event_dates else None
                ),
                query_fingerprint=query_fingerprint,
                status="SUCCEEDED",
                requested_at=requested,
                completed_at=completed,
                attempt_count=attempt_count,
                retry_count=retry_count,
                records_received=len(schedule_rows) + len(boxscores),
                records_accepted=0,
                records_rejected=0,
                duplicate_count=0,
                raw_references=list(raw_references),
                raw_checksums=list(raw_checksums),
                schema_version="gate3a-history-v1",
                code_revision=code_revision,
                quality_summary={"canonical_status": "PENDING_IN_TRANSACTION"},
                source_mode="LIVE",
                temporal_semantics=TEMPORAL_SEMANTICS,
                identity_checksum=_checkpoint_checksum(
                    source_version_id=str(source_version.id),
                    stream_key=stream_key,
                    sequence=sequence,
                    query_fingerprint=query_fingerprint,
                    status="SUCCEEDED",
                    completed_at=completed,
                    raw_checksums=raw_checksums,
                    attempt_count=attempt_count,
                    retry_count=retry_count,
                    records_received=len(schedule_rows) + len(boxscores),
                    records_accepted=0,
                    records_rejected=0,
                    duplicate_count=0,
                    request_accounting=accounting,
                ),
            )
            self.session.add(anchor)
            self.session.flush()
            result = self._persist_validated_facts(
                schedule_rows=schedule_rows,
                boxscores=boxscores,
                source_version=source_version,
                ingestion_run=ingestion_run,
                checkpoint_id=anchor.id,
                acquired_by=completed,
            )
            ingestion_run.status = RunStatus.SUCCEEDED
            ingestion_run.completed_at = completed
            ingestion_run.records_processed = result[0]
            quality_summary = {
                "input_schedule_rows": len(schedule_rows),
                "input_boxscore_rows": len(boxscores),
                "games": result[2],
                "final_outcomes": result[3],
                "team_observations": result[4],
                "player_observations": result[5],
                "canonical_facts_committed": sum(result[2:6]),
                "canonical_duplicate_facts": result[6],
                "research_provenance": "HISTORICAL_RECONSTRUCTION",
                "temporal_semantics": TEMPORAL_SEMANTICS,
                "count_semantics": "ACTUAL_CANONICAL_INSERTS_IN_COMMITTED_TRANSACTION",
                "request_accounting": accounting,
            }
            # The checkpoint must exist before canonical rows because they carry
            # its FK. Finalize it with the observed counts in this same atomic
            # transaction; no provisional checkpoint state can commit.
            self.session.execute(
                update(HistoryBackfillCheckpoint)
                .where(HistoryBackfillCheckpoint.id == anchor.id)
                .values(
                    records_accepted=result[0],
                    duplicate_count=result[1],
                    quality_summary=quality_summary,
                    identity_checksum=_checkpoint_checksum(
                        source_version_id=str(source_version.id),
                        stream_key=stream_key,
                        sequence=sequence,
                        query_fingerprint=query_fingerprint,
                        status="SUCCEEDED",
                        completed_at=completed,
                        raw_checksums=raw_checksums,
                        attempt_count=attempt_count,
                        retry_count=retry_count,
                        records_received=len(schedule_rows) + len(boxscores),
                        records_accepted=result[0],
                        records_rejected=0,
                        duplicate_count=result[1],
                        request_accounting=accounting,
                    ),
                )
                .execution_options(synchronize_session=False)
            )
            self.session.expire(anchor)
            self.session.commit()
            return HistoricalChunkResult(
                checkpoint_id=str(anchor.id),
                ingestion_run_id=str(ingestion_run.id),
                status="SUCCEEDED",
                games=result[2],
                final_outcomes=result[3],
                team_observations=result[4],
                player_observations=result[5],
                duplicates=result[1],
                records_received=len(schedule_rows) + len(boxscores),
                records_accepted=result[0],
                records_rejected=0,
                canonical_duplicate_facts=result[6],
            )
        except Exception as exc:
            self.session.rollback()
            failed_run = IngestionRun(
                source=self.source,
                job_name=f"historical:{stream_key}",
                status=RunStatus.FAILED,
                started_at=requested,
                completed_at=completed,
                records_processed=0,
                raw_reference=raw_references[0] if raw_references else None,
                schema_version="gate3a-history-v1",
                error_message=_safe_error(exc),
                source_metadata={
                    "source_version_id": str(source_version.id),
                    "query_fingerprint": query_fingerprint,
                    "temporal_semantics": TEMPORAL_SEMANTICS,
                },
            )
            self.session.add(failed_run)
            self.session.flush()
            failed = HistoryBackfillCheckpoint(
                ingestion_run_id=failed_run.id,
                source_version_id=source_version.id,
                stream_key=stream_key,
                sequence=sequence,
                season=(schedule_rows[0].season if schedule_rows else None),
                event_start_at=None,
                event_end_at=None,
                query_fingerprint=query_fingerprint,
                status="FAILED",
                requested_at=requested,
                completed_at=completed,
                attempt_count=attempt_count,
                retry_count=retry_count,
                records_received=len(schedule_rows) + len(boxscores),
                records_accepted=0,
                records_rejected=len(schedule_rows) + len(boxscores),
                duplicate_count=0,
                raw_references=list(raw_references),
                raw_checksums=list(raw_checksums),
                schema_version="gate3a-history-v1",
                code_revision=code_revision,
                error_type=type(exc).__name__,
                error_message=_safe_error(exc),
                quality_summary={
                    "canonical_facts_committed": 0,
                    "request_accounting": accounting,
                },
                source_mode="LIVE",
                temporal_semantics=TEMPORAL_SEMANTICS,
                identity_checksum=_checkpoint_checksum(
                    source_version_id=str(source_version.id),
                    stream_key=stream_key,
                    sequence=sequence,
                    query_fingerprint=query_fingerprint,
                    status="FAILED",
                    completed_at=completed,
                    raw_checksums=raw_checksums,
                    attempt_count=attempt_count,
                    retry_count=retry_count,
                    records_received=len(schedule_rows) + len(boxscores),
                    records_accepted=0,
                    records_rejected=len(schedule_rows) + len(boxscores),
                    duplicate_count=0,
                    request_accounting=accounting,
                ),
            )
            self.session.add(failed)
            self.session.commit()
            raise HistoricalPersistenceError(_safe_error(exc)) from exc

    def _persist_validated_facts(
        self,
        *,
        schedule_rows: Sequence[HistoricalScheduleGameDTO],
        boxscores: Sequence[HistoricalBoxscoreDTO],
        source_version: HistoricalSourceVersion,
        ingestion_run: IngestionRun,
        checkpoint_id: Any,
        acquired_by: datetime,
    ) -> tuple[int, int, int, int, int, int, int]:
        normalizer = CanonicalNormalizer(self.session)
        schedules: dict[str, HistoricalScheduleGameDTO] = {}
        for row in schedule_rows:
            game_id = row.game.source_game_id
            if game_id in schedules:
                raise HistoricalPersistenceError(f"Duplicate schedule source ID {game_id}")
            _validate_schedule_contract(row)
            schedules[game_id] = row
        boxes: dict[str, HistoricalBoxscoreDTO] = {}
        for boxscore in boxscores:
            if boxscore.source_game_id in boxes:
                raise HistoricalPersistenceError(
                    f"Duplicate boxscore source ID {boxscore.source_game_id}"
                )
            if boxscore.source_game_id not in schedules:
                raise HistoricalPersistenceError(
                    f"Boxscore game {boxscore.source_game_id} has no exact schedule identity"
                )
            boxes[boxscore.source_game_id] = boxscore

        accepted = input_duplicates = 0
        games_count = finals_count = team_count = player_count = canonical_duplicates = 0
        for source_game_id, row in schedules.items():
            _, league, season, _ = normalizer.ensure_core(row.season)
            if season.name != str(row.season):
                raise HistoricalPersistenceError("Canonical season resolution failed")
            game, home, away, venue, duplicate_game = self._canonical_game(
                row, league.id, season.id, acquired_by
            )
            games_count += int(not duplicate_game)
            canonical_duplicates += int(duplicate_game)
            outcome, duplicate_outcome = self._final_outcome(
                row=row,
                game=game,
                season_id=season.id,
                home_team_id=home.id,
                away_team_id=away.id,
                venue_id=venue.id,
                source_version=source_version,
                checkpoint_id=checkpoint_id,
                acquired_by=acquired_by,
            )
            if outcome is None:
                if source_game_id in boxes:
                    raise HistoricalPersistenceError(
                        f"Boxscore game {source_game_id} has no official final outcome"
                    )
                if duplicate_game:
                    input_duplicates += 1
                else:
                    accepted += 1
                continue
            finals_count += int(not duplicate_outcome)
            canonical_duplicates += int(duplicate_outcome)
            if duplicate_game and duplicate_outcome:
                input_duplicates += 1
            else:
                accepted += 1
            boxscore = boxes.get(source_game_id)
            if boxscore is not None:
                team_added, player_added, duplicate_facts = self._boxscore_facts(
                    boxscore=boxscore,
                    row=row,
                    game=game,
                    outcome=outcome,
                    home_team=home,
                    away_team=away,
                    season_id=season.id,
                    source_version=source_version,
                    ingestion_run=ingestion_run,
                    checkpoint_id=checkpoint_id,
                )
                team_count += team_added
                player_count += player_added
                canonical_duplicates += duplicate_facts
                if team_added + player_added == 0:
                    input_duplicates += 1
                else:
                    accepted += 1
        return (
            accepted,
            input_duplicates,
            games_count,
            finals_count,
            team_count,
            player_count,
            canonical_duplicates,
        )

    def _canonical_game(
        self,
        row: HistoricalScheduleGameDTO,
        league_id: Any,
        season_id: Any,
        acquired_by: datetime,
    ) -> tuple[Game, Team, Team, Venue, bool]:
        dto = row.game
        mapping = self._mapping("game", dto.source_game_id)
        if mapping is not None:
            game = self.session.get(Game, mapping.entity_id)
            if game is None:
                raise HistoricalPersistenceError("Game mapping points to a missing entity")
            sides = list(self.session.scalars(select(GameTeam).where(GameTeam.game_id == game.id)))
            home_side = next((side for side in sides if side.is_home), None)
            away_side = next((side for side in sides if not side.is_home), None)
            home = self._exact_mapped("team", dto.home_team.source_id, Team)
            away = self._exact_mapped("team", dto.away_team.source_id, Team)
            if (
                home is None or away is None or home_side is None or away_side is None
                or home_side.team_id != home.id or away_side.team_id != away.id
            ):
                raise HistoricalPersistenceError(
                    f"Exact game identity {dto.source_game_id} conflicts with canonical sides"
                )
            venue = self._exact_mapped("venue", dto.venue.source_id, Venue)
            if venue is None:
                raise HistoricalPersistenceError("Existing game has no exact venue mapping")
            if _aware(game.starts_at) != _aware(dto.scheduled_at) or game.season_id != season_id:
                raise HistoricalPersistenceError(
                    f"Exact game identity {dto.source_game_id} conflicts with "
                    "schedule time or season"
                )
            conflict = _venue_metadata_conflict(dto.venue, venue)
            if conflict is not None:
                raise HistoricalPersistenceError(
                    f"Exact venue identity {dto.venue.source_id} conflicts with "
                    f"provider metadata: {conflict}"
                )
            if row.final is not None:
                game.status = GameStatus.FINAL
                game.home_score = row.final.home_score
                game.away_score = row.final.away_score
                game.final_at = _final_time(row)
                home_side.score = row.final.home_score
                away_side.score = row.final.away_score
            return game, home, away, venue, True

        home = self._exact_team(
            dto.home_team.source_id,
            dto.home_team.name,
            dto.home_team.abbreviation,
            league_id,
        )
        away = self._exact_team(
            dto.away_team.source_id,
            dto.away_team.name,
            dto.away_team.abbreviation,
            league_id,
        )
        if home.id == away.id:
            raise HistoricalPersistenceError("Home and away provider IDs resolve to one team")
        venue = self._exact_venue(
            dto.venue.source_id,
            dto.venue.name,
            dto.venue.latitude,
            dto.venue.longitude,
            dto.venue.timezone,
        )
        game = Game(
            league_id=league_id,
            season_id=season_id,
            venue_id=venue.id,
            starts_at=_aware(dto.scheduled_at),
            status=GameStatus.FINAL if row.final is not None else _game_status(dto.status),
            home_score=row.final.home_score if row.final else dto.home_score,
            away_score=row.final.away_score if row.final else dto.away_score,
            scheduled_innings=dto.scheduled_innings,
            final_at=(
                _final_time(row)
                if row.final is not None else dto.final_at
            ),
            source_mode="LIVE",
        )
        self.session.add(game)
        self.session.flush()
        self.session.add(ExternalIdMapping(
            entity_type="game", entity_id=game.id, source=self.source,
            external_id=dto.source_game_id,
        ))
        self.session.add_all([
            GameTeam(
                game_id=game.id,
                team_id=home.id,
                is_home=True,
                score=row.final.home_score if row.final else dto.home_score,
            ),
            GameTeam(
                game_id=game.id,
                team_id=away.id,
                is_home=False,
                score=row.final.away_score if row.final else dto.away_score,
            ),
        ])
        self.session.flush()
        return game, home, away, venue, False

    def _final_outcome(
        self,
        *,
        row: HistoricalScheduleGameDTO,
        game: Game,
        season_id: Any,
        home_team_id: Any,
        away_team_id: Any,
        venue_id: Any,
        source_version: HistoricalSourceVersion,
        checkpoint_id: Any,
        acquired_by: datetime,
    ) -> tuple[GameFinalOutcome | None, bool]:
        final = row.final
        if final is None:
            return None, False
        if final.source_game_id != row.game.source_game_id:
            raise HistoricalPersistenceError("Final source ID does not match schedule source ID")
        payload = final.model_dump(mode="json")
        payload_checksum = _checksum(_result_content(payload))
        existing = self.session.scalar(
            select(GameFinalOutcome).where(
                GameFinalOutcome.game_id == game.id,
                GameFinalOutcome.source_version_id == source_version.id,
                GameFinalOutcome.source_mode == "LIVE",
                GameFinalOutcome.payload_checksum == payload_checksum,
            )
        )
        if existing is not None:
            return existing, True

        # Revisions written before the checksum excluded acquisition time carry
        # a digest computed over content PLUS the retrieval clock, so an
        # unchanged result will not match the checksum above. Compare the result
        # columns themselves before minting a successor: a re-read of a game
        # whose score, innings and completion time are identical is the same
        # final, not a correction of it.
        same_result = self.session.scalar(
            select(GameFinalOutcome)
            .where(
                GameFinalOutcome.game_id == game.id,
                GameFinalOutcome.source_version_id == source_version.id,
                GameFinalOutcome.source_mode == "LIVE",
                GameFinalOutcome.outcome_status == "FINAL",
                GameFinalOutcome.home_score == final.home_score,
                GameFinalOutcome.away_score == final.away_score,
                GameFinalOutcome.innings_played.is_not_distinct_from(
                    final.innings_played),
                GameFinalOutcome.final_at == _final_time(row),
            )
            .order_by(GameFinalOutcome.revision.desc())
            .limit(1)
        )
        if same_result is not None:
            return same_result, True
        previous = self.session.scalar(
            select(GameFinalOutcome)
            .where(
                GameFinalOutcome.game_id == game.id,
                GameFinalOutcome.source_version_id == source_version.id,
                GameFinalOutcome.source_mode == "LIVE",
            )
            .order_by(GameFinalOutcome.revision.desc(), GameFinalOutcome.id.desc())
            .limit(1)
        )
        final_at = _final_time(row)
        retrieved_at = _aware(max(final.acquired_at, row.acquired_at))
        if retrieved_at < final_at:
            raise HistoricalPersistenceError("Final acquired_at cannot precede the final event")
        source_published_at = (
            _aware(final.source_published_at)
            if final.source_published_at is not None
            else None
        )
        if source_published_at is not None and source_published_at < final_at:
            raise HistoricalPersistenceError(
                "Final source_published_at cannot precede authoritative completion"
            )
        label_available_at = max(
            retrieved_at,
            source_published_at or retrieved_at,
        )
        completion = _completion_evidence(row)
        reconstructed_at = _aware(acquired_by)
        if reconstructed_at < retrieved_at:
            raise HistoricalPersistenceError(
                "Canonical reconstruction completed_at cannot precede evidence acquisition"
            )
        quality_tier = _evidence_tier(final.evidence_class, context="Final")
        outcome = GameFinalOutcome(
            game_id=game.id,
            season_id=season_id,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            venue_id=venue_id,
            source_version_id=source_version.id,
            checkpoint_id=checkpoint_id,
            official_date=row.official_date,
            scheduled_at=_aware(row.game.scheduled_at),
            final_at=final_at,
            completion_time_basis=completion[0],
            completion_time_source_field=completion[1],
            completion_time_method=completion[2],
            completion_time_limitation=completion[3],
            evidence_classification=final.evidence_class,
            quality_tier=quality_tier,
            reconstructed_at=reconstructed_at,
            scheduled_innings=row.game.scheduled_innings,
            innings_played=final.innings_played,
            home_score=final.home_score,
            away_score=final.away_score,
            home_win=final.home_score > final.away_score,
            run_differential=final.home_score - final.away_score,
            total_runs=final.home_score + final.away_score,
            outcome_status="FINAL",
            revision=(previous.revision + 1 if previous else 1),
            supersedes_outcome_id=previous.id if previous else None,
            official=True,
            source_record_id=final.source_game_id,
            source_published_at=source_published_at,
            retrieved_at=retrieved_at,
            label_available_at=label_available_at,
            availability_basis=(
                "MAX_ASP_ACQUISITION_AND_SOURCE_PUBLICATION"
                if source_published_at is not None
                else "ASP_ACQUISITION_TIME"
            ),
            payload_checksum=payload_checksum,
            source_mode="LIVE",
            temporal_semantics=TEMPORAL_SEMANTICS,
            identity_checksum=_checksum({
                "game_id": str(game.id),
                "source_version_id": str(source_version.id),
                "revision": previous.revision + 1 if previous else 1,
                "official_date": row.official_date.isoformat(),
                "payload_checksum": payload_checksum,
            }),
        )
        self.session.add(outcome)
        self.session.flush()
        return outcome, False

    def _boxscore_facts(
        self,
        *,
        boxscore: HistoricalBoxscoreDTO,
        row: HistoricalScheduleGameDTO,
        game: Game,
        outcome: GameFinalOutcome,
        home_team: Team,
        away_team: Team,
        season_id: Any,
        source_version: HistoricalSourceVersion,
        ingestion_run: IngestionRun,
        checkpoint_id: Any,
    ) -> tuple[int, int, int]:
        if boxscore.research_provenance != "HISTORICAL_RECONSTRUCTION":
            raise HistoricalPersistenceError("Boxscore research provenance is invalid")
        boxscore_tier = _evidence_tier(
            boxscore.evidence_class,
            context="Boxscore",
        )
        if boxscore.research_provenance != row.research_provenance:
            raise HistoricalPersistenceError("Boxscore research provenance conflicts with schedule")
        if _aware(boxscore.acquired_at) < outcome.final_at:
            raise HistoricalPersistenceError("Boxscore acquired_at cannot precede the final event")
        side_by_external = {
            row.game.home_team.source_id: (home_team, away_team, True),
            row.game.away_team.source_id: (away_team, home_team, False),
        }
        if None in side_by_external or len(side_by_external) != 2:
            raise HistoricalPersistenceError("Schedule team provider IDs are required")
        team_added = player_added = duplicates = 0
        seen_team_ids: set[str] = set()
        for evidence in boxscore.teams:
            _validate_nested_evidence_contract(
                evidence.research_provenance,
                evidence.evidence_class,
                parent_provenance=boxscore.research_provenance,
                parent_classification=boxscore.evidence_class,
                context="Team fact",
            )
            if evidence.source_game_id != boxscore.source_game_id:
                raise HistoricalPersistenceError("Team fact game ID mismatch")
            side = side_by_external.get(evidence.source_team_id)
            if side is None or evidence.source_team_id in seen_team_ids:
                raise HistoricalPersistenceError("Team fact has unknown or duplicate exact team ID")
            team, opponent, is_home = side
            if evidence.is_home != is_home:
                raise HistoricalPersistenceError(
                    "Team fact home/away identity conflicts with schedule"
                )
            seen_team_ids.add(evidence.source_team_id)
            checksum = _checksum(evidence.model_dump(mode="json"))
            existing = self.session.scalar(select(TeamGameHistoryObservation).where(
                TeamGameHistoryObservation.game_id == game.id,
                TeamGameHistoryObservation.team_id == team.id,
                TeamGameHistoryObservation.source_version_id == source_version.id,
                TeamGameHistoryObservation.source_mode == "LIVE",
                TeamGameHistoryObservation.source_record_id
                == f"{boxscore.source_game_id}:{evidence.source_team_id}",
                TeamGameHistoryObservation.payload_checksum == checksum,
            ))
            if existing is not None:
                duplicates += 1
                continue
            previous = self.session.scalar(
                select(TeamGameHistoryObservation)
                .where(
                    TeamGameHistoryObservation.game_id == game.id,
                    TeamGameHistoryObservation.team_id == team.id,
                    TeamGameHistoryObservation.source_version_id == source_version.id,
                    TeamGameHistoryObservation.source_mode == "LIVE",
                    TeamGameHistoryObservation.source_record_id
                    == f"{boxscore.source_game_id}:{evidence.source_team_id}",
                )
                .order_by(
                    TeamGameHistoryObservation.revision.desc(),
                    TeamGameHistoryObservation.id.desc(),
                )
                .limit(1)
            )
            self.session.add(TeamGameHistoryObservation(
                game_id=game.id,
                final_outcome_id=outcome.id,
                season_id=season_id,
                team_id=team.id,
                opponent_team_id=opponent.id,
                is_home=is_home,
                source_version_id=source_version.id,
                checkpoint_id=checkpoint_id,
                event_at=outcome.final_at,
                evidence_end_at=outcome.final_at,
                retrieved_at=_aware(evidence.acquired_at),
                eligible_at=outcome.final_at,
                availability_basis=_history_availability_basis(outcome),
                evidence_classification=evidence.evidence_class,
                quality_tier=boxscore_tier,
                source_record_id=f"{boxscore.source_game_id}:{evidence.source_team_id}",
                revision=(previous.revision + 1 if previous else 1),
                supersedes_observation_id=previous.id if previous else None,
                stat_schema_version="mlb-boxscore-team-v1",
                values=evidence.team_stats,
                payload_checksum=checksum,
                source_mode="LIVE",
                temporal_semantics=TEMPORAL_SEMANTICS,
                identity_checksum=_checksum({
                    "game_id": str(game.id), "team_id": str(team.id), "payload": checksum,
                }),
            ))
            team_added += 1
        if seen_team_ids != set(side_by_external):
            raise HistoricalPersistenceError("Boxscore must contain both exact schedule teams")

        seen_players: set[tuple[str, str]] = set()
        for evidence in boxscore.players:
            _validate_nested_evidence_contract(
                evidence.research_provenance,
                evidence.evidence_class,
                parent_provenance=boxscore.research_provenance,
                parent_classification=boxscore.evidence_class,
                context="Player fact",
            )
            if evidence.source_game_id != boxscore.source_game_id:
                raise HistoricalPersistenceError("Player fact game ID mismatch")
            side = side_by_external.get(evidence.source_team_id)
            if side is None:
                raise HistoricalPersistenceError("Player fact team ID is not an exact game side")
            team, opponent, is_home = side
            if evidence.is_home != is_home:
                raise HistoricalPersistenceError(
                    "Player fact home/away identity conflicts with schedule"
                )
            if evidence.actual_starting_pitcher and (
                evidence.starter_provenance
                != "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION"
            ):
                raise HistoricalPersistenceError(
                    "Actual historical starter requires explicit starter provenance"
                )
            if evidence.actual_starting_pitcher and not evidence.appeared_as_pitcher:
                raise HistoricalPersistenceError(
                    "Actual historical starter must be a pitcher participant"
                )
            if evidence.starter_provenance is not None and not evidence.actual_starting_pitcher:
                raise HistoricalPersistenceError(
                    "Starter provenance cannot label a non-starting pitcher"
                )
            if evidence.batting_order is not None and (
                evidence.lineup_provenance != "HISTORICAL_ACTUAL_LINEUP"
            ):
                raise HistoricalPersistenceError(
                    "Historical batting order requires explicit lineup provenance"
                )
            if evidence.lineup_provenance is not None and evidence.batting_order is None:
                raise HistoricalPersistenceError(
                    "Lineup provenance requires a historical batting order"
                )
            player_key = (evidence.source_player_id, evidence.source_team_id)
            if player_key in seen_players:
                raise HistoricalPersistenceError(
                    "Duplicate player participation in one team boxscore"
                )
            seen_players.add(player_key)
            player = self._exact_player(
                evidence.source_player_id,
                evidence.full_name,
                team.id,
                evidence.position,
            )
            roles: list[str] = []
            if evidence.batting is not None or evidence.batting_order is not None:
                roles.append("BATTER")
            if (
                evidence.pitching is not None
                or evidence.appeared_as_pitcher
                or evidence.actual_starting_pitcher
            ):
                roles.append("PITCHER")
            if not roles:
                roles.append("PARTICIPANT")
            for role in roles:
                values = {
                    "batting_order": evidence.batting_order,
                    "batting": evidence.batting,
                    "pitching": evidence.pitching,
                    "appeared_as_pitcher": evidence.appeared_as_pitcher,
                    "actual_starting_pitcher": evidence.actual_starting_pitcher,
                    "starter_provenance": evidence.starter_provenance,
                    "lineup_provenance": evidence.lineup_provenance,
                    "target_context_timing_risk": (
                        "PARTIALLY_RECONSTRUCTABLE_IF_USED_FOR_TARGET_GAME_CONTEXT"
                        if evidence.starter_provenance or evidence.lineup_provenance
                        else None
                    ),
                    "position": evidence.position,
                }
                checksum = _checksum({**evidence.model_dump(mode="json"), "role": role})
                source_record_id = (
                    f"{boxscore.source_game_id}:{evidence.source_player_id}:"
                    f"{evidence.source_team_id}:{role}"
                )
                existing = self.session.scalar(select(PlayerGameHistoryObservation).where(
                    PlayerGameHistoryObservation.game_id == game.id,
                    PlayerGameHistoryObservation.player_id == player.id,
                    PlayerGameHistoryObservation.team_id == team.id,
                    PlayerGameHistoryObservation.role == role,
                    PlayerGameHistoryObservation.source_version_id == source_version.id,
                    PlayerGameHistoryObservation.source_mode == "LIVE",
                    PlayerGameHistoryObservation.source_record_id == source_record_id,
                    PlayerGameHistoryObservation.payload_checksum == checksum,
                ))
                if existing is not None:
                    duplicates += 1
                    continue
                previous = self.session.scalar(
                    select(PlayerGameHistoryObservation)
                    .where(
                        PlayerGameHistoryObservation.game_id == game.id,
                        PlayerGameHistoryObservation.player_id == player.id,
                        PlayerGameHistoryObservation.team_id == team.id,
                        PlayerGameHistoryObservation.role == role,
                        PlayerGameHistoryObservation.source_version_id == source_version.id,
                        PlayerGameHistoryObservation.source_mode == "LIVE",
                        PlayerGameHistoryObservation.source_record_id == source_record_id,
                    )
                    .order_by(
                        PlayerGameHistoryObservation.revision.desc(),
                        PlayerGameHistoryObservation.id.desc(),
                    )
                    .limit(1)
                )
                self.session.add(PlayerGameHistoryObservation(
                    game_id=game.id,
                    final_outcome_id=outcome.id,
                    season_id=season_id,
                    player_id=player.id,
                    team_id=team.id,
                    opponent_team_id=opponent.id,
                    role=role,
                    started=(
                        evidence.actual_starting_pitcher
                        if role == "PITCHER"
                        else evidence.batting_order is not None
                    ),
                    source_version_id=source_version.id,
                    checkpoint_id=checkpoint_id,
                    event_at=outcome.final_at,
                    evidence_end_at=outcome.final_at,
                    retrieved_at=_aware(evidence.acquired_at),
                    eligible_at=outcome.final_at,
                    availability_basis=_history_availability_basis(outcome),
                    evidence_classification=evidence.evidence_class,
                    quality_tier=boxscore_tier,
                    source_record_id=source_record_id,
                    revision=(previous.revision + 1 if previous else 1),
                    supersedes_observation_id=previous.id if previous else None,
                    stat_schema_version="mlb-boxscore-player-v1",
                    values=values,
                    payload_checksum=checksum,
                    source_mode="LIVE",
                    temporal_semantics=TEMPORAL_SEMANTICS,
                    identity_checksum=_checksum({
                        "game_id": str(game.id), "player_id": str(player.id),
                        "team_id": str(team.id), "role": role, "payload": checksum,
                    }),
                ))
                player_added += 1
        self.session.flush()
        return team_added, player_added, duplicates

    def _exact_team(
        self,
        external_id: str,
        name: str,
        abbreviation: str | None,
        league_id: Any,
    ) -> Team:
        if not external_id:
            raise HistoricalPersistenceError("Team source ID is required")
        existing = self._exact_mapped("team", external_id, Team)
        if existing is not None:
            if existing.league_id != league_id:
                raise HistoricalPersistenceError("Mapped team belongs to another league")
            return existing
        team = Team(
            league_id=league_id,
            name=name,
            abbreviation=abbreviation or f"MLB{external_id}"[:12],
            city=name,
        )
        self.session.add(team)
        self.session.flush()
        self.session.add(ExternalIdMapping(
            entity_type="team", entity_id=team.id, source=self.source, external_id=external_id,
        ))
        self.session.flush()
        return team

    def _exact_venue(
        self,
        external_id: str | None,
        name: str,
        latitude: float | None,
        longitude: float | None,
        timezone_name: str | None,
    ) -> Venue:
        if not external_id:
            raise HistoricalPersistenceError("Venue source ID is required")
        existing = self._exact_mapped("venue", external_id, Venue)
        if existing is not None:
            return existing
        venue = Venue(
            name=name,
            latitude=latitude,
            longitude=longitude,
            # A missing provider timezone remains explicitly unknown. UTC
            # would falsely assert a local venue fact for historical evidence.
            timezone=timezone_name,
        )
        self.session.add(venue)
        self.session.flush()
        self.session.add(ExternalIdMapping(
            entity_type="venue", entity_id=venue.id, source=self.source, external_id=external_id,
        ))
        self.session.flush()
        return venue

    def _exact_player(
        self,
        external_id: str,
        full_name: str | None,
        participating_team_id: Any,
        position: str | None,
    ) -> Player:
        if not external_id:
            raise HistoricalPersistenceError("Player source ID is required")
        existing = self._exact_mapped("player", external_id, Player)
        if existing is not None:
            # Historical participation is stored on the fact row. Never rewrite
            # the mutable current-team projection while replaying old seasons.
            return existing
        player = Player(
            team_id=participating_team_id,
            full_name=full_name or f"MLB player {external_id}",
            position=position,
        )
        self.session.add(player)
        self.session.flush()
        self.session.add(ExternalIdMapping(
            entity_type="player", entity_id=player.id, source=self.source, external_id=external_id,
        ))
        self.session.flush()
        return player

    def _mapping(self, entity_type: str, external_id: str) -> ExternalIdMapping | None:
        return self.session.scalar(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == entity_type,
            ExternalIdMapping.source == self.source,
            ExternalIdMapping.external_id == external_id,
        ))

    def _exact_mapped(self, entity_type: str, external_id: str | None, model: type) -> Any | None:
        if not external_id:
            return None
        mapping = self._mapping(entity_type, external_id)
        return self.session.get(model, mapping.entity_id) if mapping else None


def _governance_is_approved(governance: dict[str, Any] | None) -> bool:
    if not isinstance(governance, dict):
        return False
    return (
        str(governance.get("approval_status") or "").strip().upper() == "APPROVED"
        and bool(str(governance.get("reviewer") or "").strip())
        and bool(str(governance.get("reviewed_at") or "").strip())
    )


def _evidence_tier(value: str, *, context: str) -> str:
    normalized = str(value).strip().upper()
    if normalized == "RECONSTRUCTABLE_POINT_IN_TIME":
        return "A"
    if normalized == "PARTIALLY_RECONSTRUCTABLE":
        return "B"
    if normalized == "FORWARD_ONLY":
        raise HistoricalPersistenceError(
            f"{context} FORWARD_ONLY evidence cannot enter historical canonical facts"
        )
    raise HistoricalPersistenceError(f"{context} evidence classification is invalid")


def _validate_nested_evidence_contract(
    provenance: str,
    classification: str,
    *,
    parent_provenance: str,
    parent_classification: str,
    context: str,
) -> None:
    if provenance != "HISTORICAL_RECONSTRUCTION":
        raise HistoricalPersistenceError(f"{context} research provenance is invalid")
    if provenance != parent_provenance:
        raise HistoricalPersistenceError(f"{context} research provenance conflicts with parent")
    if classification != parent_classification:
        raise HistoricalPersistenceError(f"{context} evidence classification conflicts with parent")
    _evidence_tier(classification, context=context)


def _validate_schedule_contract(row: HistoricalScheduleGameDTO) -> None:
    if row.game.source_mode != "LIVE":
        raise HistoricalPersistenceError("Historical schedule source_mode must be LIVE")
    if row.research_provenance != "HISTORICAL_RECONSTRUCTION":
        raise HistoricalPersistenceError("Schedule research provenance is invalid")
    _evidence_tier(row.evidence_class, context="Schedule")
    if row.final is None:
        return
    final = row.final
    if final.official_date != row.official_date:
        raise HistoricalPersistenceError(
            "Final official_date conflicts with the canonical schedule date"
        )
    if final.research_provenance != row.research_provenance:
        raise HistoricalPersistenceError("Final research provenance conflicts with schedule")
    if final.evidence_class != row.evidence_class:
        raise HistoricalPersistenceError("Final evidence classification conflicts with schedule")
    _evidence_tier(final.evidence_class, context="Final")
    _completion_evidence(row)
    if final.home_score == final.away_score:
        raise HistoricalPersistenceError("Official MLB final outcome cannot be tied")
    if final.innings_played is None or final.innings_played <= 0:
        raise HistoricalPersistenceError("Official MLB final requires innings_played")
    if (
        str(row.abstract_state or "").strip().lower() != "final"
        or str(final.abstract_state or "").strip().lower() != "final"
    ):
        raise HistoricalPersistenceError("Official target requires authoritative FINAL state")


def _final_time(row: HistoricalScheduleGameDTO) -> datetime:
    if row.final is None:
        raise HistoricalPersistenceError("Final outcome is missing")
    _completion_evidence(row)
    completed = _aware(row.final.event_completed_at)
    scheduled = _aware(row.game.scheduled_at)
    if completed < scheduled:
        raise HistoricalPersistenceError("Final time cannot precede scheduled first pitch")
    return completed


def _completion_evidence(
    row: HistoricalScheduleGameDTO,
) -> tuple[str, str, str, str]:
    final = row.final
    if final is None:
        raise HistoricalPersistenceError("Final outcome is missing")
    if final.event_completed_at is None:
        raise HistoricalPersistenceError(
            "Final outcome lacks governed event_completed_at completion evidence; "
            "first pitch, retrieval, end-of-day, midnight, and estimated duration "
            "cannot be substituted"
        )
    basis = str(final.completion_time_basis or "").strip().upper()
    if basis not in {
        "AUTHORITATIVE_FINAL_AT",
        "DERIVED_LAST_EVENT_COMPLETION_PROXY",
    }:
        raise HistoricalPersistenceError("Final completion_time_basis is invalid or missing")
    source_field = str(final.completion_time_source_field or "").strip()
    method = str(final.completion_time_method or "").strip()
    limitation = str(final.completion_time_limitation or "").strip()
    if not source_field or not method or not limitation:
        raise HistoricalPersistenceError(
            "Final completion evidence requires source field, method, and limitation"
        )
    forbidden = (
        "first_pitch",
        "scheduled_at",
        "retrieved_at",
        "fetched_at",
        "end_of_day",
        "midnight",
        "estimated_duration",
    )
    normalized_field = source_field.lower().replace(" ", "_").replace("-", "_")
    if any(value in normalized_field for value in forbidden):
        raise HistoricalPersistenceError(
            "Final completion evidence uses a prohibited timestamp substitute"
        )
    return basis, source_field, method, limitation


#: How far two published coordinates for one venue may differ and still be the
#: same venue. About 110 m at the equator -- orders of magnitude smaller than the
#: distance between any two ball parks, and orders of magnitude larger than a
#: precision difference between provider surfaces.
#:
#: The exact-equality test this replaces could not be satisfied: MLB's schedule
#: hydrate publishes Comerica Park at 42.3391151/-83.048695, AISP had stored
#: 42.339/-83.0485 from a lower-precision path, and `!=` on two floats called
#: that an identity conflict. Every 2026 canonical pass failed on it. Rewriting
#: the stored venue instead would have been worse: this module deliberately
#: refuses to rewrite mutable dimension rows while replaying, for the same
#: reason `_exact_player` refuses to move a player's current team.
#:
#: A gap larger than this is still a conflict and still fails closed -- that is
#: the case the guard exists for, a mapping pointing at a different ground.
_VENUE_COORDINATE_TOLERANCE_DEGREES = 1e-3


def _venue_metadata_conflict(supplied: Any, stored: Venue) -> str | None:
    """Why the provider's venue metadata contradicts the stored venue, if it does.

    Coordinates compare within a tolerance; the timezone is categorical and
    still compares exactly, because two different zone names for one ground is
    a real disagreement rather than a precision artefact.
    """

    for label, given, held in (
        ("latitude", supplied.latitude, stored.latitude),
        ("longitude", supplied.longitude, stored.longitude),
    ):
        if given is None or held is None:
            continue
        if abs(float(given) - float(held)) > _VENUE_COORDINATE_TOLERANCE_DEGREES:
            return (
                f"{label} {given!r} differs from stored {held!r} by more than "
                f"{_VENUE_COORDINATE_TOLERANCE_DEGREES} degrees"
            )
    if supplied.timezone is not None and stored.timezone != supplied.timezone:
        return f"timezone {supplied.timezone!r} differs from stored {stored.timezone!r}"
    return None


def _history_availability_basis(outcome: GameFinalOutcome) -> str:
    if outcome.completion_time_basis == "AUTHORITATIVE_FINAL_AT":
        return "RETROSPECTIVE_AUTHORITATIVE_FINAL_COMPLETION"
    if outcome.completion_time_basis == "DERIVED_LAST_EVENT_COMPLETION_PROXY":
        return "RETROSPECTIVE_DERIVED_LAST_EVENT_COMPLETION_PROXY"
    raise HistoricalPersistenceError("Outcome completion basis is not governed")


def _validate_request_accounting(
    accounting: dict[str, Any] | None,
    *,
    attempt_count: int,
    retry_count: int,
) -> dict[str, Any]:
    if not isinstance(accounting, dict):
        raise HistoricalPersistenceError(
            "Canonical checkpoint requires exact request_accounting"
        )
    normalized = json.loads(json.dumps(accounting))
    declared_attempts = normalized.get("attempts")
    declared_retries = normalized.get("retries")
    if declared_attempts != attempt_count or declared_retries != retry_count:
        raise HistoricalPersistenceError(
            "Request accounting totals conflict with checkpoint attempt counts"
        )
    for key in ("successes", "failed_attempts"):
        value = normalized.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise HistoricalPersistenceError(
                f"Request accounting {key} must be a non-negative integer"
            )
    if normalized["successes"] + normalized["failed_attempts"] != attempt_count:
        raise HistoricalPersistenceError(
            "Request accounting successes plus failed_attempts must equal attempts"
        )
    operations = normalized.get("operations")
    if not isinstance(operations, dict) or not operations:
        raise HistoricalPersistenceError(
            "Request accounting requires a non-empty operations map"
        )
    operation_attempts = 0
    for operation, values in operations.items():
        if not str(operation).strip() or not isinstance(values, dict):
            raise HistoricalPersistenceError("Request accounting operation is invalid")
        attempts = values.get("attempts")
        successes = values.get("successes")
        failures = values.get("failed_attempts")
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (attempts, successes, failures)
        ):
            raise HistoricalPersistenceError(
                "Request accounting operation counts must be non-negative integers"
            )
        if successes + failures != attempts:
            raise HistoricalPersistenceError(
                "Request accounting operation counts do not balance"
            )
        operation_attempts += attempts
    if operation_attempts != attempt_count:
        raise HistoricalPersistenceError(
            "Request accounting operation attempts do not equal total attempts"
        )
    return normalized


def _game_status(value: str) -> GameStatus:
    status = value.lower()
    if "postpon" in status:
        return GameStatus.POSTPONED
    if "cancel" in status:
        return GameStatus.CANCELED
    if "final" in status or "completed" in status:
        return GameStatus.FINAL
    if "live" in status or "progress" in status:
        return GameStatus.LIVE
    return GameStatus.SCHEDULED


def _checkpoint_checksum(
    *,
    source_version_id: str,
    stream_key: str,
    sequence: int,
    query_fingerprint: str,
    status: str,
    completed_at: datetime,
    raw_checksums: Sequence[str],
    attempt_count: int,
    retry_count: int,
    records_received: int,
    records_accepted: int,
    records_rejected: int,
    duplicate_count: int,
    request_accounting: dict[str, Any],
) -> str:
    return _checksum({
        "source_version_id": source_version_id,
        "stream_key": stream_key,
        "sequence": sequence,
        "query_fingerprint": query_fingerprint,
        "status": status,
        "completed_at": _aware(completed_at).isoformat(),
        "raw_checksums": list(raw_checksums),
        "attempt_count": attempt_count,
        "retry_count": retry_count,
        "records_received": records_received,
        "records_accepted": records_accepted,
        "records_rejected": records_rejected,
        "duplicate_count": duplicate_count,
        "request_accounting": request_accounting,
    })


def _checksum(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, HistoricalPersistenceError):
        return str(exc)[:500]
    return f"Historical canonical persistence failed: {type(exc).__name__}"
