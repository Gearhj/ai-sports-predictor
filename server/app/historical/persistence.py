"""Provider-free canonical persistence for governed MLB historical evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any, Sequence

from sqlalchemy import select
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
        identity = _checksum({
            "source_key": self.source,
            "dataset_version": dataset_version,
            "source_mode": "LIVE",
            "temporal_semantics": TEMPORAL_SEMANTICS,
            "policy_version": policy_version,
            "training_eligible": approved,
            "evaluation_eligible": approved,
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
            availability_policy={
                "historical_acquisition_is_not_backdated": True,
                "authoritative_event_completed_at_required": True,
                "unknown_completion_action": "REJECT_NO_FINAL_OUTCOME",
            },
            policy_version=policy_version,
            governance=governance,
            effective_from=effective,
            identity_checksum=identity,
        )
        self.session.add(source_version)
        self.session.flush()
        return source_version

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
                attempt_count=1,
                retry_count=0,
                records_received=len(schedule_rows) + len(boxscores),
                records_accepted=len(schedule_rows) + len(boxscores),
                records_rejected=0,
                duplicate_count=0,
                raw_references=list(raw_references),
                raw_checksums=list(raw_checksums),
                schema_version="gate3a-history-v1",
                code_revision=code_revision,
                quality_summary={
                    "games": len(schedule_rows),
                    "final_outcomes": sum(row.final is not None for row in schedule_rows),
                    "team_observations": sum(len(row.teams) for row in boxscores),
                    "player_observations": sum(
                        sum(
                            2 if (row.batting is not None and row.pitching is not None) else 1
                            for row in boxscore.players
                        )
                        for boxscore in boxscores
                    ),
                    "research_provenance": "HISTORICAL_RECONSTRUCTION",
                    "temporal_semantics": TEMPORAL_SEMANTICS,
                },
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
            self.session.commit()
            return HistoricalChunkResult(
                checkpoint_id=str(anchor.id),
                ingestion_run_id=str(ingestion_run.id),
                status="SUCCEEDED",
                games=result[1],
                final_outcomes=result[2],
                team_observations=result[3],
                player_observations=result[4],
                duplicates=result[5],
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
                attempt_count=1,
                retry_count=0,
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
                quality_summary={"canonical_facts_committed": 0},
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
    ) -> tuple[int, int, int, int, int, int]:
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

        accepted = games_count = finals_count = team_count = player_count = duplicates = 0
        for source_game_id, row in schedules.items():
            _, league, season, _ = normalizer.ensure_core(row.season)
            if season.name != str(row.season):
                raise HistoricalPersistenceError("Canonical season resolution failed")
            game, home, away, venue, duplicate_game = self._canonical_game(
                row, league.id, season.id, acquired_by
            )
            games_count += int(not duplicate_game)
            duplicates += int(duplicate_game)
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
                accepted += 1
                continue
            finals_count += int(not duplicate_outcome)
            duplicates += int(duplicate_outcome)
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
                duplicates += duplicate_facts
            accepted += 1 + int(boxscore is not None)
        return accepted, games_count, finals_count, team_count, player_count, duplicates

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
        payload_checksum = _checksum(payload)
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
        outcome = GameFinalOutcome(
            game_id=game.id,
            season_id=season_id,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            venue_id=venue_id,
            source_version_id=source_version.id,
            checkpoint_id=checkpoint_id,
            scheduled_at=_aware(row.game.scheduled_at),
            final_at=final_at,
            scheduled_innings=row.game.scheduled_innings,
            innings_played=final.innings_played,
            home_score=final.home_score,
            away_score=final.away_score,
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
            existing = self.session.scalar(select(TeamGameHistoryObservation.id).where(
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
                availability_basis="RETROSPECTIVE_AUTHORITATIVE_FINAL_COMPLETION",
                evidence_classification=evidence.evidence_class,
                quality_tier=boxscore_tier,
                source_record_id=f"{boxscore.source_game_id}:{evidence.source_team_id}",
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
            if evidence.pitching is not None or evidence.appeared_as_pitcher:
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
                    "position": evidence.position,
                }
                checksum = _checksum({**evidence.model_dump(mode="json"), "role": role})
                source_record_id = (
                    f"{boxscore.source_game_id}:{evidence.source_player_id}:"
                    f"{evidence.source_team_id}:{role}"
                )
                existing = self.session.scalar(select(PlayerGameHistoryObservation.id).where(
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
                    availability_basis="RETROSPECTIVE_AUTHORITATIVE_FINAL_COMPLETION",
                    evidence_classification=evidence.evidence_class,
                    quality_tier=boxscore_tier,
                    source_record_id=source_record_id,
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
    ) -> Venue:
        if not external_id:
            raise HistoricalPersistenceError("Venue source ID is required")
        existing = self._exact_mapped("venue", external_id, Venue)
        if existing is not None:
            return existing
        venue = Venue(name=name, latitude=latitude, longitude=longitude, timezone="UTC")
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
    if final.research_provenance != row.research_provenance:
        raise HistoricalPersistenceError("Final research provenance conflicts with schedule")
    if final.evidence_class != row.evidence_class:
        raise HistoricalPersistenceError("Final evidence classification conflicts with schedule")
    _evidence_tier(final.evidence_class, context="Final")
    if (
        str(row.abstract_state or "").strip().lower() != "final"
        or str(final.abstract_state or "").strip().lower() != "final"
    ):
        raise HistoricalPersistenceError("Official target requires authoritative FINAL state")


def _final_time(row: HistoricalScheduleGameDTO) -> datetime:
    if row.final is None:
        raise HistoricalPersistenceError("Final outcome is missing")
    if row.final.event_completed_at is None:
        raise HistoricalPersistenceError(
            "Final outcome lacks an authoritative event_completed_at; "
            "acquisition time and end-of-day cannot be substituted"
        )
    completed = _aware(row.final.event_completed_at)
    scheduled = _aware(row.game.scheduled_at)
    if completed < scheduled:
        raise HistoricalPersistenceError("Final time cannot precede scheduled first pitch")
    return completed


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
) -> str:
    return _checksum({
        "source_version_id": source_version_id,
        "stream_key": stream_key,
        "sequence": sequence,
        "query_fingerprint": query_fingerprint,
        "status": status,
        "completed_at": _aware(completed_at).isoformat(),
        "raw_checksums": list(raw_checksums),
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
