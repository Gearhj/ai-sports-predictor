from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.audit import ImmutableRecordError
from app.data_sources.base import (
    AdapterResponse,
    HistoricalBoxscoreDTO,
    HistoricalFinalDTO,
    HistoricalGameEvidenceDTO,
    HistoricalPlayerGameEvidenceDTO,
    HistoricalScheduleGameDTO,
    HistoricalTeamGameEvidenceDTO,
    ScheduleGameDTO,
    SourceEnvelope,
    TeamDTO,
    VenueDTO,
)
from app.data_sources.request_policy import (
    ConservativeRequestExecutor,
    ConservativeRequestPolicy,
)
from app.historical.backfill import HistoricalBackfillOptions, HistoricalBackfillRunner
from app.historical.checkpoints import (
    HistoricalCheckpoint,
    MemoryCheckpointRepository,
    SqlAlchemyCheckpointRepository,
)
from app.historical.persistence import (
    _venue_metadata_conflict,
    A1_GOVERNANCE_DECISION,
    HistoricalCanonicalPersistenceService,
    HistoricalPersistenceError,
)
from app.models import (
    ExternalIdMapping,
    Game,
    GameFinalOutcome,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    IngestionRun,
    Player,
    PlayerGameHistoryObservation,
    RunStatus,
    TeamGameHistoryObservation,
    Venue,
)
from app.ingestion.archive import RawArchive


ACQUIRED = datetime(2026, 8, 14, 12, tzinfo=UTC)
APPROVED_GOVERNANCE = {
    "approval_status": "APPROVED",
    "reviewer": "gate3a-test-reviewer",
    "reviewed_at": "2026-08-14T11:00:00Z",
}


def _approved_source(service: HistoricalCanonicalPersistenceService, **kwargs):
    return service.ensure_source_version(
        effective_from=ACQUIRED,
        governance=APPROVED_GOVERNANCE,
        **kwargs,
    )


def _schedule(
    game_id: str,
    *,
    official_date: date = date(2025, 4, 1),
    home_id: str = "20",
    away_id: str = "10",
    home_score: int = 4,
    away_score: int = 2,
    game_number: int = 1,
    acquired_at: datetime = ACQUIRED,
) -> HistoricalScheduleGameDTO:
    first_pitch = datetime.combine(
        official_date, datetime.min.time(), tzinfo=UTC
    ) + timedelta(hours=23)
    final_at = first_pitch + timedelta(hours=3)
    return HistoricalScheduleGameDTO(
        game=ScheduleGameDTO(
            source_game_id=game_id,
            scheduled_at=first_pitch,
            event_date=official_date,
            status="Final",
            away_team=TeamDTO(
                source_id=away_id, name=f"Away {away_id}", abbreviation=f"A{away_id}"
            ),
            home_team=TeamDTO(
                source_id=home_id, name=f"Home {home_id}", abbreviation=f"H{home_id}"
            ),
            venue=VenueDTO(source_id="1", name="Exact ID Park"),
            source_mode="LIVE",
            away_score=away_score,
            home_score=home_score,
            game_number=game_number,
            scheduled_innings=9,
        ),
        season=2025,
        official_date=official_date,
        game_type="R",
        abstract_state="Final",
        coded_state="F",
        acquired_at=acquired_at,
        final=HistoricalFinalDTO(
            source_game_id=game_id,
            official_date=official_date,
            status="Final",
            abstract_state="Final",
            home_score=home_score,
            away_score=away_score,
            innings_played=9,
            event_completed_at=final_at,
            completion_time_basis="AUTHORITATIVE_FINAL_AT",
            completion_time_source_field="gameData.datetime.gameEndTime",
            completion_time_method="explicit provider final timestamp",
            completion_time_limitation="provider-defined final timestamp",
            acquired_at=acquired_at,
        ),
    )


def _boxscore(
    schedule: HistoricalScheduleGameDTO,
    *,
    player_id: str = "42",
    player_team_id: str | None = None,
    evidence_class: str = "RECONSTRUCTABLE_POINT_IN_TIME",
) -> HistoricalBoxscoreDTO:
    game = schedule.game
    team_id = player_team_id or game.home_team.source_id
    assert team_id is not None
    is_home = team_id == game.home_team.source_id
    return HistoricalBoxscoreDTO(
        source_game_id=game.source_game_id,
        acquired_at=schedule.acquired_at,
        evidence_class=evidence_class,
        teams=[
            HistoricalTeamGameEvidenceDTO(
                source_game_id=game.source_game_id,
                source_team_id=game.home_team.source_id or "",
                is_home=True,
                team_stats={"batting": {"runs": schedule.final.home_score}},
                acquired_at=schedule.acquired_at,
                evidence_class=evidence_class,
            ),
            HistoricalTeamGameEvidenceDTO(
                source_game_id=game.source_game_id,
                source_team_id=game.away_team.source_id or "",
                is_home=False,
                team_stats={"batting": {"runs": schedule.final.away_score}},
                acquired_at=schedule.acquired_at,
                evidence_class=evidence_class,
            ),
        ],
        players=[
            HistoricalPlayerGameEvidenceDTO(
                source_game_id=game.source_game_id,
                source_player_id=player_id,
                source_team_id=team_id,
                full_name=f"Player {player_id}",
                is_home=is_home,
                batting_order=1,
                lineup_provenance="HISTORICAL_ACTUAL_LINEUP",
                appeared_as_pitcher=False,
                position="CF",
                batting={"hits": 2},
                acquired_at=schedule.acquired_at,
                evidence_class=evidence_class,
            )
        ],
    )


def _persist(
    service,
    source,
    schedules,
    boxes,
    *,
    fingerprint="f" * 64,
    sequence=0,
    requested_at=ACQUIRED - timedelta(minutes=1),
    completed_at=None,
    attempt_count=1,
    retry_count=0,
    request_accounting=None,
):
    completed_at = completed_at or max(
        [ACQUIRED]
        + [row.acquired_at for row in schedules]
        + [row.acquired_at for row in boxes]
    )
    return service.persist_chunk(
        schedule_rows=schedules,
        boxscores=boxes,
        source_version=source,
        stream_key="mlb_schedule_boxscore",
        sequence=sequence,
        query_fingerprint=fingerprint,
        requested_at=requested_at,
        completed_at=completed_at,
        raw_references=["data/raw/historical/test.json.gz"],
        raw_checksums=["a" * 64],
        code_revision="test",
        attempt_count=attempt_count,
        retry_count=retry_count,
        request_accounting=request_accounting or {
            "attempts": attempt_count,
            "retries": retry_count,
            "successes": 1,
            "failed_attempts": attempt_count - 1,
            "operations": {
                "game_feed": {
                    "attempts": attempt_count,
                    "successes": 1,
                    "failed_attempts": attempt_count - 1,
                }
            },
        },
    )


def test_canonical_chunk_is_idempotent_and_never_backdates_acquisition(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    assert source.availability_policy["historical_acquisition_is_not_backdated"] is True
    assert set(source.availability_policy["completion_time_bases"]) == {
        "AUTHORITATIVE_FINAL_AT",
        "DERIVED_LAST_EVENT_COMPLETION_PROXY",
    }
    schedule = _schedule("777")
    boxscore = _boxscore(schedule)

    first = _persist(service, source, [schedule], [boxscore])
    second = _persist(service, source, [schedule], [boxscore])

    assert second == first
    assert first.records_received == 2
    assert first.records_accepted == 2
    assert first.records_rejected == 0
    assert first.duplicates == 0
    assert first.canonical_duplicate_facts == 0
    assert session.scalar(select(func.count()).select_from(Game)) == 1
    assert session.scalar(select(func.count()).select_from(GameFinalOutcome)) == 1
    assert session.scalar(select(func.count()).select_from(TeamGameHistoryObservation)) == 2
    assert session.scalar(select(func.count()).select_from(PlayerGameHistoryObservation)) == 1
    assert session.scalar(select(func.count()).select_from(HistoryBackfillCheckpoint)) == 1
    outcome = session.scalar(select(GameFinalOutcome))
    assert outcome is not None
    assert outcome.retrieved_at.replace(tzinfo=UTC) == ACQUIRED
    assert outcome.final_at < outcome.retrieved_at
    assert outcome.label_available_at.replace(tzinfo=UTC) == ACQUIRED
    assert outcome.availability_basis == "ASP_ACQUISITION_TIME"
    assert outcome.temporal_semantics == "RETROSPECTIVE_RECONSTRUCTION"
    assert outcome.completion_time_basis == "AUTHORITATIVE_FINAL_AT"
    assert outcome.completion_time_source_field == "gameData.datetime.gameEndTime"
    assert outcome.evidence_classification == "RECONSTRUCTABLE_POINT_IN_TIME"
    assert outcome.quality_tier == "A"
    assert outcome.home_win is True
    assert outcome.run_differential == 2
    assert outcome.total_runs == 6
    assert session.scalar(
        select(GameFinalOutcome.id).where(
            GameFinalOutcome.label_available_at < ACQUIRED
        )
    ) is None

    prior_cutoff = schedule.final.event_completed_at + timedelta(days=1)
    team_fact = session.scalar(select(TeamGameHistoryObservation))
    player_fact = session.scalar(select(PlayerGameHistoryObservation))
    assert team_fact is not None and player_fact is not None
    for fact in (team_fact, player_fact):
        assert fact.retrieved_at.replace(tzinfo=UTC) == ACQUIRED
        assert fact.eligible_at == outcome.final_at
        assert fact.eligible_at.replace(tzinfo=UTC) < prior_cutoff < fact.retrieved_at.replace(tzinfo=UTC)
        assert fact.availability_basis == "RETROSPECTIVE_AUTHORITATIVE_FINAL_COMPLETION"


def test_final_correction_appends_revision_and_supersedes(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    first = _schedule("777", home_score=4)
    _persist(service, source, [first], [_boxscore(first)], fingerprint="1" * 64)
    corrected = _schedule("777", home_score=5, acquired_at=ACQUIRED + timedelta(minutes=1))
    _persist(
        service,
        source,
        [corrected],
        [_boxscore(corrected)],
        fingerprint="2" * 64,
        sequence=1,
    )

    outcomes = list(session.scalars(select(GameFinalOutcome).order_by(GameFinalOutcome.revision)))
    assert [(row.revision, row.home_score) for row in outcomes] == [(1, 4), (2, 5)]
    assert outcomes[1].supersedes_outcome_id == outcomes[0].id
    team_facts = list(session.scalars(
        select(TeamGameHistoryObservation).order_by(
            TeamGameHistoryObservation.team_id,
            TeamGameHistoryObservation.revision,
        )
    ))
    assert sorted(row.revision for row in team_facts) == [1, 1, 2, 2]
    assert all(
        row.supersedes_observation_id is None for row in team_facts if row.revision == 1
    )
    assert all(
        row.supersedes_observation_id is not None for row in team_facts if row.revision == 2
    )
    player_facts = list(session.scalars(
        select(PlayerGameHistoryObservation).order_by(PlayerGameHistoryObservation.revision)
    ))
    assert [row.revision for row in player_facts] == [1, 2]
    assert player_facts[1].supersedes_observation_id == player_facts[0].id
    game = session.scalar(select(Game))
    assert game is not None and game.home_score == 5

    outcomes[0].home_score = 99
    with pytest.raises(ImmutableRecordError, match="GameFinalOutcome records are append-only"):
        session.commit()
    session.rollback()


def test_derived_last_event_completion_is_honest_tier_a_history(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    schedule = _schedule("derived-completion")
    schedule = schedule.model_copy(update={
        "game": schedule.game.model_copy(update={
            "venue": schedule.game.venue.model_copy(update={
                "latitude": 40.8296,
                "longitude": -73.9262,
                "timezone": "America/New_York",
            })
        }),
        "final": schedule.final.model_copy(update={
            "completion_time_basis": "DERIVED_LAST_EVENT_COMPLETION_PROXY",
            "completion_time_source_field": "liveData.plays.allPlays[-1].about.endTime",
            "completion_time_method": (
                "last complete play in provider array after final-state validation"
            ),
            "completion_time_limitation": (
                "proxy for final recorded play completion; not an explicit MLB "
                "game-final publication timestamp"
            ),
        }),
    })

    _persist(
        service,
        source,
        [schedule],
        [_boxscore(schedule)],
        attempt_count=2,
        retry_count=1,
        request_accounting={
            "attempts": 2,
            "retries": 1,
            "successes": 1,
            "failed_attempts": 1,
            "operations": {
                "game_feed": {
                    "attempts": 2,
                    "successes": 1,
                    "failed_attempts": 1,
                }
            },
        },
    )

    outcome = session.scalar(select(GameFinalOutcome))
    assert outcome is not None
    assert outcome.completion_time_basis == "DERIVED_LAST_EVENT_COMPLETION_PROXY"
    assert outcome.quality_tier == "A"
    assert outcome.evidence_classification == "RECONSTRUCTABLE_POINT_IN_TIME"
    assert outcome.completion_time_source_field.endswith("about.endTime")
    assert "not an explicit" in outcome.completion_time_limitation
    assert {
        row.availability_basis
        for row in session.scalars(select(TeamGameHistoryObservation))
    } == {"RETROSPECTIVE_DERIVED_LAST_EVENT_COMPLETION_PROXY"}
    player_fact = session.scalar(select(PlayerGameHistoryObservation))
    assert player_fact is not None
    assert player_fact.quality_tier == "A"
    assert player_fact.values["lineup_provenance"] == "HISTORICAL_ACTUAL_LINEUP"
    assert (
        player_fact.values["target_context_timing_risk"]
        == "PARTIALLY_RECONSTRUCTABLE_IF_USED_FOR_TARGET_GAME_CONTEXT"
    )
    venue = session.scalar(select(Venue))
    assert venue is not None and venue.timezone == "America/New_York"
    checkpoint = session.scalar(select(HistoryBackfillCheckpoint))
    assert checkpoint is not None
    assert (checkpoint.attempt_count, checkpoint.retry_count) == (2, 1)
    assert checkpoint.quality_summary["request_accounting"]["operations"] == {
        "game_feed": {"attempts": 2, "successes": 1, "failed_attempts": 1}
    }


def test_completion_evidence_rejects_prohibited_timestamp_substitute(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    schedule = _schedule("bad-completion-field")
    schedule = schedule.model_copy(update={
        "final": schedule.final.model_copy(update={
            "completion_time_source_field": "retrieved_at",
        })
    })

    with pytest.raises(HistoricalPersistenceError, match="prohibited timestamp substitute"):
        _persist(service, source, [schedule], [])
    assert session.scalar(select(func.count()).select_from(GameFinalOutcome)) == 0


def test_actual_starter_requires_and_persists_historical_provenance(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    schedule = _schedule("starter-provenance")
    boxscore = _boxscore(schedule)
    pitcher = boxscore.players[0].model_copy(update={
        "batting_order": None,
        "lineup_provenance": None,
        "batting": None,
        "appeared_as_pitcher": True,
        "actual_starting_pitcher": True,
        "starter_provenance": "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION",
        "position": "P",
        "pitching": {"inningsPitched": "6.0", "numberOfPitches": 91},
    })
    boxscore = boxscore.model_copy(update={"players": [pitcher]})

    _persist(service, source, [schedule], [boxscore])

    fact = session.scalar(select(PlayerGameHistoryObservation))
    assert fact is not None and fact.role == "PITCHER" and fact.started is True
    assert (
        fact.values["starter_provenance"]
        == "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION"
    )
    assert fact.quality_tier == "A"
    assert (
        fact.values["target_context_timing_risk"]
        == "PARTIALLY_RECONSTRUCTABLE_IF_USED_FOR_TARGET_GAME_CONTEXT"
    )

    invalid_schedule = _schedule(
        "starter-missing-provenance",
        official_date=date(2025, 4, 2),
        acquired_at=ACQUIRED + timedelta(minutes=1),
    )
    invalid_box = _boxscore(invalid_schedule)
    invalid_pitcher = invalid_box.players[0].model_copy(update={
        "batting_order": None,
        "lineup_provenance": None,
        "batting": None,
        "appeared_as_pitcher": True,
        "actual_starting_pitcher": True,
        "starter_provenance": None,
        "pitching": {"inningsPitched": "5.0"},
    })
    invalid_box = invalid_box.model_copy(update={"players": [invalid_pitcher]})
    with pytest.raises(HistoricalPersistenceError, match="starter provenance"):
        _persist(
            service,
            source,
            [invalid_schedule],
            [invalid_box],
            fingerprint="e" * 64,
            sequence=1,
        )


def test_a1_source_version_requires_exact_owner_governance(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = service.ensure_a1_source_version(effective_from=ACQUIRED)
    assert source.training_eligible is True
    assert source.evaluation_eligible is True
    assert source.quality_tier == "A"
    assert source.governance == A1_GOVERNANCE_DECISION
    assert source.governance["commercial_deployment"] == "NOT_APPROVED"
    assert source.governance["public_redistribution"] == "PROHIBITED_PENDING_REVIEW"

    with pytest.raises(HistoricalPersistenceError, match="approved local research scope"):
        service.ensure_source_version(
            dataset_version="historical-core-v1",
            policy_version="gate3a-a1-core-v1",
            effective_from=ACQUIRED,
            governance={**A1_GOVERNANCE_DECISION, "commercial_deployment": "APPROVED"},
        )


def test_request_accounting_must_exactly_balance(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    schedule = _schedule("bad-accounting")

    with pytest.raises(HistoricalPersistenceError, match="conflict"):
        _persist(
            service,
            source,
            [schedule],
            [],
            attempt_count=2,
            retry_count=1,
            request_accounting={
                "attempts": 1,
                "retries": 1,
                "successes": 1,
                "failed_attempts": 0,
                "operations": {
                    "game_feed": {
                        "attempts": 1,
                        "successes": 1,
                        "failed_attempts": 0,
                    }
                },
            },
        )
    assert session.scalar(select(func.count()).select_from(HistoryBackfillCheckpoint)) == 0


def test_label_availability_uses_later_proven_publication_time(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    schedule = _schedule("published-after-acquisition")
    published_at = ACQUIRED + timedelta(minutes=2)
    schedule = schedule.model_copy(update={
        "final": schedule.final.model_copy(update={"source_published_at": published_at})
    })
    _persist(
        service,
        source,
        [schedule],
        [],
        completed_at=published_at + timedelta(minutes=1),
    )

    outcome = session.scalar(select(GameFinalOutcome))
    assert outcome is not None
    assert outcome.retrieved_at.replace(tzinfo=UTC) == ACQUIRED
    assert outcome.source_published_at.replace(tzinfo=UTC) == published_at
    assert outcome.label_available_at.replace(tzinfo=UTC) == published_at
    assert outcome.availability_basis == "MAX_ASP_ACQUISITION_AND_SOURCE_PUBLICATION"


def test_doubleheader_provider_ids_remain_distinct(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    game_one = _schedule("dh-1", game_number=1)
    game_two = _schedule("dh-2", game_number=2)
    _persist(
        service,
        source,
        [game_one, game_two],
        [_boxscore(game_one), _boxscore(game_two, player_id="43")],
    )

    game_mappings = list(session.scalars(select(ExternalIdMapping).where(
        ExternalIdMapping.entity_type == "game"
    )))
    assert {row.external_id for row in game_mappings} == {"dh-1", "dh-2"}
    assert len({row.entity_id for row in game_mappings}) == 2
    assert session.scalar(select(func.count()).select_from(GameFinalOutcome)) == 2


def test_historical_trade_participation_does_not_rewrite_current_player_team(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    before = _schedule("before-trade", home_id="20", away_id="10")
    _persist(service, source, [before], [_boxscore(before, player_id="42")], fingerprint="3" * 64)
    player = session.scalar(select(Player).join(
        ExternalIdMapping, ExternalIdMapping.entity_id == Player.id
    ).where(ExternalIdMapping.entity_type == "player", ExternalIdMapping.external_id == "42"))
    assert player is not None
    original_current_team = player.team_id

    after = _schedule(
        "after-trade",
        official_date=date(2025, 5, 1),
        home_id="30",
        away_id="20",
        acquired_at=ACQUIRED + timedelta(minutes=2),
    )
    _persist(
        service,
        source,
        [after],
        [_boxscore(after, player_id="42", player_team_id="30")],
        fingerprint="4" * 64,
        sequence=1,
    )
    session.refresh(player)
    facts = list(session.scalars(select(PlayerGameHistoryObservation).where(
        PlayerGameHistoryObservation.player_id == player.id
    ).order_by(PlayerGameHistoryObservation.event_at)))
    assert len(facts) == 2
    assert facts[0].team_id != facts[1].team_id
    assert player.team_id == original_current_team


def test_failed_chunk_commits_only_failed_run_and_checkpoint(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    schedule = _schedule("bad-box")
    bad_box = _boxscore(schedule)
    bad_box = bad_box.model_copy(update={"source_game_id": "missing-schedule"})

    with pytest.raises(HistoricalPersistenceError, match="no exact schedule identity"):
        _persist(service, source, [schedule], [bad_box])

    assert session.scalar(select(func.count()).select_from(Game)) == 0
    assert session.scalar(select(func.count()).select_from(GameFinalOutcome)) == 0
    assert session.scalar(select(func.count()).select_from(TeamGameHistoryObservation)) == 0
    assert session.scalar(select(func.count()).select_from(PlayerGameHistoryObservation)) == 0
    checkpoint = session.scalar(select(HistoryBackfillCheckpoint))
    assert checkpoint is not None and checkpoint.status == "FAILED"
    assert checkpoint.records_accepted == 0
    assert checkpoint.quality_summary["canonical_facts_committed"] == 0
    assert checkpoint.quality_summary["request_accounting"]["attempts"] == 1
    run = session.scalar(select(IngestionRun))
    assert run is not None and run.status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_backfill_failure_keeps_prepared_source_and_valid_failed_checkpoint_fk(
    session,
    artifact_root,
) -> None:
    schedule = _schedule("runner-failure")
    schedule_without_final = schedule.model_copy(update={"final": None})
    invalid_boxscore = _boxscore(schedule).model_copy(update={
        "teams": [_boxscore(schedule).teams[0]],
    })
    evidence = HistoricalGameEvidenceDTO(
        source_game_id=schedule.game.source_game_id,
        acquired_at=ACQUIRED,
        final=schedule.final,
        boxscore=invalid_boxscore,
    )

    class Adapter:
        source = "MLB_STATS_API"

        def __init__(self, executor: ConservativeRequestExecutor) -> None:
            self.executor = executor

        async def get_historical_schedule_range(self, start_date, end_date):
            async def response():
                return AdapterResponse(
                    envelope=SourceEnvelope(
                        source=self.source,
                        source_mode="LIVE",
                        fetched_at=ACQUIRED,
                        data=[schedule_without_final.model_dump(mode="json")],
                        metadata={
                            "research_provenance": "HISTORICAL_RECONSTRUCTION",
                            "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                            "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
                        },
                    ),
                    raw_payload={"dates": [{"games": [{"gamePk": "runner-failure"}]}]},
                )
            return await self.executor.execute(
                response, operation_name="historical_schedule"
            )

        async def get_historical_game_evidence(self, source_game_id):
            async def response():
                return AdapterResponse(
                    envelope=SourceEnvelope(
                        source=self.source,
                        source_mode="LIVE",
                        fetched_at=ACQUIRED,
                        source_event_time=schedule.final.event_completed_at,
                        data=evidence.model_dump(mode="json"),
                        metadata={
                            "research_provenance": "HISTORICAL_RECONSTRUCTION",
                            "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
                            "evidence_class": "RECONSTRUCTABLE_POINT_IN_TIME",
                        },
                    ),
                    raw_payload={"gamePk": "runner-failure", "liveData": {}},
                )
            return await self.executor.execute(
                response, operation_name="historical_game_feed"
            )

    service = HistoricalCanonicalPersistenceService(session)
    runner = HistoricalBackfillRunner(
        archive=RawArchive(artifact_root / "raw"),
        checkpoints=MemoryCheckpointRepository(),
        adapter_factory=Adapter,
        canonical_writer=service,
        governance=A1_GOVERNANCE_DECISION,
        request_policy=ConservativeRequestPolicy(
            max_attempts=1,
            minimum_interval_seconds=0,
        ),
        free_space=lambda path: 200_000_000_000,
        now=lambda: ACQUIRED,
    )

    result = await runner.run(HistoricalBackfillOptions(
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 1),
        season=2025,
        expected_final_games=1,
        max_requests=2,
        terms_status="APPROVED",
    ))

    assert result.status == "BLOCKED", result.reason
    assert "HistoricalPersistenceError" in result.reason
    source = session.scalar(select(HistoricalSourceVersion))
    checkpoint = session.scalar(select(HistoryBackfillCheckpoint))
    assert source is not None and checkpoint is not None
    assert checkpoint.status == "FAILED"
    assert checkpoint.source_version_id == source.id
    assert (checkpoint.attempt_count, checkpoint.retry_count) == (2, 0)
    assert checkpoint.quality_summary["canonical_facts_committed"] == 0
    assert session.get(HistoricalSourceVersion, checkpoint.source_version_id) is not None
    assert session.scalar(select(func.count()).select_from(Game)) == 0
    assert session.scalar(select(func.count()).select_from(GameFinalOutcome)) == 0


def test_unknown_final_completion_is_rejected_without_inventing_time(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    schedule = _schedule("unknown-final-time")
    schedule = schedule.model_copy(update={
        "final": schedule.final.model_copy(update={"event_completed_at": None})
    })

    with pytest.raises(HistoricalPersistenceError, match="completion evidence"):
        _persist(service, source, [schedule], [_boxscore(schedule)])

    assert session.scalar(select(func.count()).select_from(Game)) == 0
    assert session.scalar(select(func.count()).select_from(GameFinalOutcome)) == 0
    failed = session.scalar(select(HistoryBackfillCheckpoint))
    assert failed is not None and failed.status == "FAILED"


def test_source_version_is_ineligible_without_explicit_approved_governance(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    unapproved = service.ensure_source_version(
        dataset_version="unapproved-v1",
        effective_from=ACQUIRED,
    )
    assert unapproved.training_eligible is False
    assert unapproved.evaluation_eligible is False
    assert unapproved.governance is None
    session.commit()

    approved = _approved_source(service, dataset_version="approved-v1")
    assert approved.training_eligible is True
    assert approved.evaluation_eligible is True
    assert approved.governance == APPROVED_GOVERNANCE

    with pytest.raises(HistoricalPersistenceError, match="immutable decision"):
        service.ensure_source_version(
            dataset_version="unapproved-v1",
            effective_from=ACQUIRED,
            governance=APPROVED_GOVERNANCE,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("demo", "source_mode must be LIVE"),
        ("non_final", "authoritative FINAL state"),
        ("classification_mismatch", "classification conflicts with schedule"),
        ("provenance_mismatch", "provenance conflicts with schedule"),
    ],
)
def test_schedule_provenance_and_final_state_fail_closed(session, mutation, message) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    schedule = _schedule(f"invalid-{mutation}")
    if mutation == "demo":
        schedule = schedule.model_copy(update={
            "game": schedule.game.model_copy(update={"source_mode": "DEMO"})
        })
    elif mutation == "non_final":
        schedule = schedule.model_copy(update={
            "final": schedule.final.model_copy(update={"abstract_state": "Live"})
        })
    elif mutation == "classification_mismatch":
        schedule = schedule.model_copy(update={
            "final": schedule.final.model_copy(
                update={"evidence_class": "PARTIALLY_RECONSTRUCTABLE"}
            )
        })
    else:
        schedule = schedule.model_copy(update={
            "final": schedule.final.model_copy(update={"research_provenance": "BACKTEST"})
        })

    with pytest.raises(HistoricalPersistenceError, match=message):
        _persist(service, source, [schedule], [])
    assert session.scalar(select(func.count()).select_from(GameFinalOutcome)) == 0


def test_partial_boxscore_maps_to_tier_b_and_forward_only_is_rejected(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    partial = _schedule("partial-boxscore")
    _persist(
        service,
        source,
        [partial],
        [_boxscore(partial, evidence_class="PARTIALLY_RECONSTRUCTABLE")],
        fingerprint="7" * 64,
    )
    assert {
        row.quality_tier for row in session.scalars(select(TeamGameHistoryObservation))
    } == {"B"}
    assert {
        row.quality_tier for row in session.scalars(select(PlayerGameHistoryObservation))
    } == {"B"}

    forward = _schedule(
        "forward-boxscore",
        official_date=date(2025, 4, 3),
        acquired_at=ACQUIRED + timedelta(minutes=1),
    )
    with pytest.raises(HistoricalPersistenceError, match="FORWARD_ONLY"):
        _persist(
            service,
            source,
            [forward],
            [_boxscore(forward, evidence_class="FORWARD_ONLY")],
            fingerprint="8" * 64,
            sequence=1,
        )
    assert session.scalar(
        select(func.count()).select_from(GameFinalOutcome).where(
            GameFinalOutcome.source_record_id == "forward-boxscore"
        )
    ) == 0


def test_sqlalchemy_checkpoint_repository_failed_then_success_and_resume_noop(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    run = IngestionRun(
        source="MLB_STATS_API",
        job_name="checkpoint-test",
        status=RunStatus.SUCCEEDED,
        records_processed=0,
    )
    session.add(run)
    session.commit()
    repository = SqlAlchemyCheckpointRepository(session)
    base = HistoricalCheckpoint(
        checkpoint_id="10000000-0000-0000-0000-000000000001",
        ingestion_run_id=str(run.id),
        source_version_id="mlb_stats_api:historical-schedule-v1",
        stream_key="schedule",
        query_fingerprint="9" * 64,
        status="FAILED",
        requested_at=ACQUIRED,
        completed_at=ACQUIRED + timedelta(seconds=1),
        season=2025,
        event_start_at=None,
        event_end_at=None,
        sequence=0,
        attempt_count=1,
        records_received=1,
        records_rejected=1,
    )
    repository.append(base)
    success = replace(
        base,
        checkpoint_id="10000000-0000-0000-0000-000000000002",
        status="SUCCEEDED",
        sequence=1,
        completed_at=ACQUIRED + timedelta(seconds=2),
        records_accepted=1,
        records_rejected=0,
        identity_checksum="",
    )
    repository.append(success)
    session.commit()
    repository.append(replace(
        success,
        checkpoint_id="10000000-0000-0000-0000-000000000003",
        completed_at=ACQUIRED + timedelta(seconds=3),
        identity_checksum="",
    ))
    session.commit()

    assert session.scalar(select(func.count()).select_from(HistoryBackfillCheckpoint)) == 2
    assert repository.latest(str(source.id), "schedule", "9" * 64).status == "SUCCEEDED"
    assert repository.completed(str(source.id), "schedule", "9" * 64).status == "SUCCEEDED"
    assert repository.completed(
        "mlb_stats_api:historical-schedule-v1", "schedule", "9" * 64
    ).status == "SUCCEEDED"

    completed = session.scalar(select(HistoryBackfillCheckpoint).where(
        HistoryBackfillCheckpoint.status == "SUCCEEDED"
    ))
    assert completed is not None
    completed.status = "FAILED"
    with pytest.raises(ImmutableRecordError, match="append-only"):
        session.commit()
    session.rollback()
    SourceEnvelope,


# -- one venue, two publication precisions ---------------------------------
#
# MLB's schedule hydrate publishes Comerica Park at 42.3391151/-83.048695. AISP
# had it stored at 42.339/-83.0485 from a lower-precision path. Exact float
# equality called that an identity conflict, and every 2026 canonical pass
# failed on it before a single final was written. The guard has to keep catching
# a mapping that points at a different ground without failing on the fourth
# decimal place.


def _with_venue(schedule, **venue_fields):
    return schedule.model_copy(update={
        "game": schedule.game.model_copy(update={
            "venue": schedule.game.venue.model_copy(update=venue_fields)
        })
    })


def test_a_more_precise_coordinate_for_the_same_venue_is_not_a_conflict() -> None:
    """42.3391151 and 42.339 are one ball park published at two precisions.

    Exercised at the comparison rather than through a second persisted chunk:
    re-persisting one game hits an unrelated naive/aware datetime path that only
    appears under SQLite, and this test is about the coordinate rule.
    """

    stored = Venue(name="Comerica Park", latitude=42.339, longitude=-83.0485,
                   timezone="America/Detroit")
    supplied = VenueDTO(source_id="2394", name="Comerica Park",
                        latitude=42.3391151, longitude=-83.048695,
                        timezone="America/Detroit")
    assert _venue_metadata_conflict(supplied, stored) is None


def test_a_missing_provider_coordinate_is_not_read_as_a_conflict() -> None:
    """A feed that publishes no coordinate contradicts nothing."""

    stored = Venue(name="Comerica Park", latitude=42.339, longitude=-83.0485,
                   timezone="America/Detroit")
    supplied = VenueDTO(source_id="2394", name="Comerica Park")
    assert _venue_metadata_conflict(supplied, stored) is None


def test_the_comparison_names_the_field_that_disagrees() -> None:
    stored = Venue(name="Comerica Park", latitude=42.339, longitude=-83.0485,
                   timezone="America/Detroit")
    moved = VenueDTO(source_id="2394", name="Comerica Park",
                     latitude=41.829, longitude=-87.6339, timezone="America/Detroit")
    assert "latitude" in (_venue_metadata_conflict(moved, stored) or "")


def test_a_coordinate_far_enough_to_be_another_ground_still_fails(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()

    first = _with_venue(
        _schedule("venue-conflict"),
        latitude=42.339, longitude=-83.0485, timezone="America/Detroit",
    )
    _persist(service, source, [first], [_boxscore(first)])

    moved = _with_venue(
        _schedule("venue-conflict"),
        latitude=41.829, longitude=-87.6339, timezone="America/Detroit",
    )
    with pytest.raises(HistoricalPersistenceError, match="conflicts with provider metadata"):
        _persist(service, source, [moved], [_boxscore(moved)],
                 sequence=1, fingerprint="b" * 64)


def test_a_different_timezone_for_one_venue_is_still_a_conflict(session) -> None:
    """A zone name is categorical: two of them for one ground is a real disagreement."""

    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()

    first = _with_venue(
        _schedule("venue-timezone"),
        latitude=42.339, longitude=-83.0485, timezone="America/Detroit",
    )
    _persist(service, source, [first], [_boxscore(first)])

    rezoned = _with_venue(
        _schedule("venue-timezone"),
        latitude=42.339, longitude=-83.0485, timezone="America/Chicago",
    )
    with pytest.raises(HistoricalPersistenceError, match="conflicts with provider metadata"):
        _persist(service, source, [rezoned], [_boxscore(rezoned)],
                 sequence=1, fingerprint="b" * 64)
