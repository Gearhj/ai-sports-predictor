from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.audit import ImmutableRecordError
from app.data_sources.base import (
    HistoricalBoxscoreDTO,
    HistoricalFinalDTO,
    HistoricalPlayerGameEvidenceDTO,
    HistoricalScheduleGameDTO,
    HistoricalTeamGameEvidenceDTO,
    ScheduleGameDTO,
    TeamDTO,
    VenueDTO,
)
from app.historical.checkpoints import HistoricalCheckpoint, SqlAlchemyCheckpointRepository
from app.historical.persistence import (
    HistoricalCanonicalPersistenceService,
    HistoricalPersistenceError,
)
from app.models import (
    ExternalIdMapping,
    Game,
    GameFinalOutcome,
    HistoryBackfillCheckpoint,
    IngestionRun,
    Player,
    PlayerGameHistoryObservation,
    RunStatus,
    TeamGameHistoryObservation,
)


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
    completed_at=ACQUIRED,
):
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
    )


def test_canonical_chunk_is_idempotent_and_never_backdates_acquisition(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    assert source.availability_policy == {
        "historical_acquisition_is_not_backdated": True,
        "authoritative_event_completed_at_required": True,
        "unknown_completion_action": "REJECT_NO_FINAL_OUTCOME",
    }
    schedule = _schedule("777")
    boxscore = _boxscore(schedule)

    first = _persist(service, source, [schedule], [boxscore])
    second = _persist(service, source, [schedule], [boxscore])

    assert second == first
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
    game = session.scalar(select(Game))
    assert game is not None and game.home_score == 5

    outcomes[0].home_score = 99
    with pytest.raises(ImmutableRecordError, match="GameFinalOutcome records are append-only"):
        session.commit()
    session.rollback()


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
    assert checkpoint.quality_summary == {"canonical_facts_committed": 0}
    run = session.scalar(select(IngestionRun))
    assert run is not None and run.status == RunStatus.FAILED


def test_unknown_final_completion_is_rejected_without_inventing_time(session) -> None:
    service = HistoricalCanonicalPersistenceService(session)
    source = _approved_source(service)
    session.commit()
    schedule = _schedule("unknown-final-time")
    schedule = schedule.model_copy(update={
        "final": schedule.final.model_copy(update={"event_completed_at": None})
    })

    with pytest.raises(HistoricalPersistenceError, match="authoritative event_completed_at"):
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
