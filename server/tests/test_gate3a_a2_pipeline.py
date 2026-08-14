from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

import pytest
from sqlalchemy import delete, event, func, select, update

from app.historical.a2_pipeline import (
    A2PipelineError,
    A2PipelinePolicy,
    build_and_persist_a2_dataset,
    load_a2_orm_inputs,
    materialize_a2_population,
    run_a2_pipeline,
)
from app.historical.a2_reconstruction import (
    A2ChronologicalReconstructor,
    A2Population,
)
from app.historical.a2_schema import A2_SCHEMA, A2_SCHEMA_VERSION
from app.models import (
    ExternalIdMapping,
    Game,
    GameFeatureSnapshot,
    GameFinalOutcome,
    GameStatus,
    GameTeam,
    HistoricalEvidenceStateAttestation,
    HistoricalFeatureMaterialization,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    IngestionRun,
    League,
    Market,
    OddsSnapshot,
    Player,
    PlayerGameHistoryObservation,
    ProviderRequestLedger,
    RunStatus,
    Season,
    Sport,
    Team,
    TeamGameHistoryObservation,
    TrainingDatasetManifest,
    Venue,
)
from app.training.readiness import DatasetReadinessPolicy, NOT_READY_STATUS


SOURCE = "MLB_STATS_API"
COMPUTED_AT = datetime(2026, 8, 13, 20, tzinfo=UTC)
TRAINING_CUTOFF = datetime(2026, 12, 31, tzinfo=UTC)


def _hash(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SyntheticWarehouse:
    season: Season
    source: HistoricalSourceVersion
    checkpoint: HistoryBackfillCheckpoint
    games: tuple[Game, ...]
    outcomes: tuple[GameFinalOutcome, ...]
    home: Team
    away: Team
    home_batter: Player
    game_pks: tuple[str, ...]


def _mapping(session, entity_type: str, entity_id, external_id: str) -> None:
    session.add(
        ExternalIdMapping(
            entity_type=entity_type,
            entity_id=entity_id,
            source=SOURCE,
            external_id=external_id,
        )
    )


def _seed_warehouse(
    session,
    *,
    game_count: int = 4,
    tier_b_last: bool = False,
) -> SyntheticWarehouse:
    sport = Sport(code="baseball-a2", name="Baseball A2")
    session.add(sport)
    session.flush()
    league = League(
        sport_id=sport.id,
        code="MLB-A2",
        name="Major League Baseball A2",
        country_code="US",
    )
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id,
        name="2025",
        starts_at=datetime(2025, 1, 1, tzinfo=UTC),
        ends_at=datetime(2025, 12, 31, 23, 59, tzinfo=UTC),
    )
    home = Team(league_id=league.id, name="A2 Home", abbreviation="A2H")
    away = Team(league_id=league.id, name="A2 Away", abbreviation="A2A")
    venue = Venue(
        name="A2 Park",
        city="Testville",
        country_code="US",
        latitude=40.0,
        longitude=-75.0,
        timezone="America/New_York",
    )
    session.add_all([season, home, away, venue])
    session.flush()
    _mapping(session, "team", home.id, "100")
    _mapping(session, "team", away.id, "200")
    _mapping(session, "venue", venue.id, "300")

    home_batter = Player(team_id=home.id, full_name="Home Batter", position="DH")
    away_batter = Player(team_id=away.id, full_name="Away Batter", position="DH")
    home_pitcher = Player(team_id=home.id, full_name="Home Pitcher", position="P")
    away_pitcher = Player(team_id=away.id, full_name="Away Pitcher", position="P")
    session.add_all([home_batter, away_batter, home_pitcher, away_pitcher])
    session.flush()
    for player, external_id in (
        (home_batter, "1001"),
        (away_batter, "2001"),
        (home_pitcher, "1002"),
        (away_pitcher, "2002"),
    ):
        _mapping(session, "player", player.id, external_id)

    source = HistoricalSourceVersion(
        source_key=SOURCE,
        dataset_version="historical-core-v1",
        provider_name="MLB Stats API",
        source_mode="LIVE",
        source_classification="OFFICIAL_PRIMARY",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        quality_tier="A",
        training_eligible=True,
        evaluation_eligible=True,
        availability_policy={
            "historical_acquisition_is_not_backdated": True,
            "completion_time_bases": ["DERIVED_LAST_EVENT_COMPLETION_PROXY"],
        },
        policy_version="gate3a-a1-core-v1",
        governance={
            "approval_status": "APPROVED",
            "population": "HISTORICAL_RECONSTRUCTION",
            "scope": "LOCAL_PRIVATE_RESEARCH",
        },
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        identity_checksum=_hash("source"),
    )
    run = IngestionRun(
        source=SOURCE,
        job_name="a2-synthetic-history",
        status=RunStatus.SUCCEEDED,
        started_at=COMPUTED_AT - timedelta(hours=2),
        completed_at=COMPUTED_AT - timedelta(hours=1),
        records_processed=game_count,
    )
    session.add_all([source, run])
    session.flush()

    first_date = date(2025, 4, 1)
    last_date = first_date + timedelta(days=(game_count - 1) * 3)
    checkpoint = HistoryBackfillCheckpoint(
        ingestion_run_id=run.id,
        source_version_id=source.id,
        stream_key="a2-synthetic-2025",
        sequence=0,
        season=2025,
        event_start_at=datetime.combine(first_date, time.min, tzinfo=UTC),
        event_end_at=datetime.combine(last_date, time.max, tzinfo=UTC),
        query_fingerprint=_hash("query"),
        status="SUCCEEDED",
        requested_at=COMPUTED_AT - timedelta(hours=2),
        completed_at=COMPUTED_AT - timedelta(hours=1),
        attempt_count=1,
        retry_count=0,
        records_received=game_count * 20,
        records_accepted=game_count * 20,
        records_rejected=0,
        duplicate_count=0,
        raw_references=["ignored://synthetic-a2"],
        raw_checksums=[_hash("raw")],
        schema_version="gate3a-history-v1",
        code_revision="test-seed",
        quality_summary={"canonical_status": "COMMITTED"},
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum=_hash("checkpoint"),
    )
    session.add(checkpoint)
    session.flush()

    games: list[Game] = []
    outcomes: list[GameFinalOutcome] = []
    game_pks: list[str] = []
    for index in range(game_count):
        official_date = first_date + timedelta(days=index * 3)
        scheduled = datetime.combine(
            official_date, time(23, 5), tzinfo=UTC
        )
        final_at = scheduled + timedelta(hours=3)
        home_score = 5 if index % 2 == 0 else 2
        away_score = 2 if index % 2 == 0 else 4
        tier = "B" if tier_b_last and index == game_count - 1 else "A"
        classification = (
            "PARTIALLY_RECONSTRUCTABLE"
            if tier == "B"
            else "RECONSTRUCTABLE_POINT_IN_TIME"
        )
        game_pk = "777188" if tier == "B" else str(900000 + index)
        game = Game(
            league_id=league.id,
            season_id=season.id,
            venue_id=venue.id,
            starts_at=scheduled,
            status=GameStatus.FINAL,
            home_score=home_score,
            away_score=away_score,
            scheduled_innings=9,
            final_at=final_at,
            source_mode="LIVE",
        )
        session.add(game)
        session.flush()
        _mapping(session, "game", game.id, game_pk)
        session.add_all(
            [
                GameTeam(
                    game_id=game.id,
                    team_id=home.id,
                    is_home=True,
                    score=home_score,
                ),
                GameTeam(
                    game_id=game.id,
                    team_id=away.id,
                    is_home=False,
                    score=away_score,
                ),
            ]
        )
        outcome = GameFinalOutcome(
            game_id=game.id,
            season_id=season.id,
            home_team_id=home.id,
            away_team_id=away.id,
            venue_id=venue.id,
            source_version_id=source.id,
            checkpoint_id=checkpoint.id,
            official_date=official_date,
            scheduled_at=scheduled,
            final_at=final_at,
            completion_time_basis="DERIVED_LAST_EVENT_COMPLETION_PROXY",
            completion_time_source_field="liveData.plays.allPlays[-1].about.endTime",
            completion_time_method="synthetic exact final play completion",
            completion_time_limitation="test fixture",
            evidence_classification=classification,
            quality_tier=tier,
            reconstructed_at=COMPUTED_AT - timedelta(hours=1),
            scheduled_innings=9,
            innings_played=9,
            home_score=home_score,
            away_score=away_score,
            home_win=home_score > away_score,
            run_differential=home_score - away_score,
            total_runs=home_score + away_score,
            outcome_status="FINAL",
            revision=1,
            official=True,
            source_record_id=game_pk,
            retrieved_at=COMPUTED_AT - timedelta(hours=1),
            # Acquisition/label truth stays in 2026. Replay must still use the
            # proven 2025 completion time for prior Elo/rest eligibility.
            label_available_at=COMPUTED_AT - timedelta(hours=1),
            availability_basis="ACTUAL_ACQUISITION_TIME_NOT_BACKDATED",
            payload_checksum=_hash(f"outcome-payload-{index}"),
            source_mode="LIVE",
            temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
            identity_checksum=_hash(f"outcome-identity-{index}"),
        )
        session.add(outcome)
        session.flush()

        for is_home, team, opponent, team_pk, runs, allowed in (
            (True, home, away, "100", home_score, away_score),
            (False, away, home, "200", away_score, home_score),
        ):
            session.add(
                TeamGameHistoryObservation(
                    game_id=game.id,
                    final_outcome_id=outcome.id,
                    season_id=season.id,
                    team_id=team.id,
                    opponent_team_id=opponent.id,
                    is_home=is_home,
                    source_version_id=source.id,
                    checkpoint_id=checkpoint.id,
                    event_at=final_at,
                    evidence_end_at=final_at,
                    retrieved_at=COMPUTED_AT - timedelta(hours=1),
                    eligible_at=final_at,
                    availability_basis="PROVEN_FINAL_COMPLETION",
                    evidence_classification=classification,
                    quality_tier=tier,
                    source_record_id=f"{game_pk}:{team_pk}",
                    revision=1,
                    stat_schema_version="mlb-boxscore-team-v1",
                    values={
                        "batting": {"runs": runs},
                        "pitching": {"runs": allowed},
                    },
                    payload_checksum=_hash(f"team-payload-{index}-{team_pk}"),
                    source_mode="LIVE",
                    temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                    identity_checksum=_hash(f"team-identity-{index}-{team_pk}"),
                )
            )

        for team, opponent, team_pk, batter, batter_pk, pitcher, pitcher_pk in (
            (home, away, "100", home_batter, "1001", home_pitcher, "1002"),
            (away, home, "200", away_batter, "2001", away_pitcher, "2002"),
        ):
            session.add(
                PlayerGameHistoryObservation(
                    game_id=game.id,
                    final_outcome_id=outcome.id,
                    season_id=season.id,
                    player_id=batter.id,
                    team_id=team.id,
                    opponent_team_id=opponent.id,
                    role="BATTER",
                    started=True,
                    source_version_id=source.id,
                    checkpoint_id=checkpoint.id,
                    event_at=final_at,
                    evidence_end_at=final_at,
                    retrieved_at=COMPUTED_AT - timedelta(hours=1),
                    eligible_at=final_at,
                    availability_basis="PROVEN_FINAL_COMPLETION",
                    evidence_classification=classification,
                    quality_tier=tier,
                    source_record_id=f"{game_pk}:{batter_pk}:{team_pk}:BATTER",
                    revision=1,
                    stat_schema_version="mlb-boxscore-player-v1",
                    values={
                        "batting_order": 1,
                        "batting": {
                            "plateAppearances": 4,
                            "atBats": 3,
                            "hits": 1,
                            "baseOnBalls": 1,
                            "strikeOuts": 1,
                            "totalBases": 1,
                            "homeRuns": 0,
                        },
                        "pitching": None,
                        "appeared_as_pitcher": False,
                        "actual_starting_pitcher": False,
                    },
                    payload_checksum=_hash(f"batter-payload-{index}-{team_pk}"),
                    source_mode="LIVE",
                    temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                    identity_checksum=_hash(f"batter-identity-{index}-{team_pk}"),
                )
            )
            session.add(
                PlayerGameHistoryObservation(
                    game_id=game.id,
                    final_outcome_id=outcome.id,
                    season_id=season.id,
                    player_id=pitcher.id,
                    team_id=team.id,
                    opponent_team_id=opponent.id,
                    role="PITCHER",
                    started=True,
                    source_version_id=source.id,
                    checkpoint_id=checkpoint.id,
                    event_at=final_at,
                    evidence_end_at=final_at,
                    retrieved_at=COMPUTED_AT - timedelta(hours=1),
                    eligible_at=final_at,
                    availability_basis="PROVEN_FINAL_COMPLETION",
                    evidence_classification=classification,
                    quality_tier=tier,
                    source_record_id=f"{game_pk}:{pitcher_pk}:{team_pk}:PITCHER",
                    revision=1,
                    stat_schema_version="mlb-boxscore-player-v1",
                    values={
                        "batting_order": None,
                        "batting": None,
                        "pitching": {
                            "battersFaced": 25,
                            "hits": 5,
                            "baseOnBalls": 2,
                            "strikeOuts": 7,
                            "homeRuns": 1,
                            "numberOfPitches": 90,
                            "inningsPitched": "6.0",
                            "earnedRuns": 2,
                        },
                        "appeared_as_pitcher": True,
                        "actual_starting_pitcher": True,
                    },
                    payload_checksum=_hash(f"pitcher-payload-{index}-{team_pk}"),
                    source_mode="LIVE",
                    temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                    identity_checksum=_hash(f"pitcher-identity-{index}-{team_pk}"),
                )
            )
        games.append(game)
        outcomes.append(outcome)
        game_pks.append(game_pk)
    session.commit()
    return SyntheticWarehouse(
        season=season,
        source=source,
        checkpoint=checkpoint,
        games=tuple(games),
        outcomes=tuple(outcomes),
        home=home,
        away=away,
        home_batter=home_batter,
        game_pks=tuple(game_pks),
    )


def _policy(
    warehouse: SyntheticWarehouse,
    *,
    tier_b_count: int = 0,
    start: date | None = None,
    end: date | None = None,
    selected: int | None = None,
) -> A2PipelinePolicy:
    rain = frozenset({"777188"}) if tier_b_count else frozenset()
    return A2PipelinePolicy(
        expected_tier_a_count=len(warehouse.games) - tier_b_count,
        expected_tier_b_count=tier_b_count,
        target_official_date_start=start,
        target_official_date_end=end,
        expected_selected_target_count=selected,
        rain_shortened_game_pks=rain,
        require_exact_rain_set=True,
    )


def _readiness_policy() -> DatasetReadinessPolicy:
    return DatasetReadinessPolicy(
        minimum_rows=1,
        minimum_holdout_rows=1,
        holdout_fraction=0.25,
        minimum_event_dates=2,
        minimum_development_dates=1,
        minimum_rolling_folds=1,
        initial_training_dates=1,
        validation_dates=1,
        fold_step_dates=1,
        minimum_feature_family_coverage=0.0,
    )


def test_sqlite_end_to_end_artifact_manifest_and_zero_provider_market(
    session, artifact_root: Path
) -> None:
    warehouse = _seed_warehouse(session)
    result = run_a2_pipeline(
        session,
        artifact_root=artifact_root / "datasets",
        policy=_policy(warehouse),
        generated_at=COMPUTED_AT,
        training_cutoff=TRAINING_CUTOFF,
        code_revision="a2-test",
        batch_size=2,
        readiness_policy=_readiness_policy(),
        minimum_free_bytes_after_write=0,
    )

    assert result.materialization.target_count == len(warehouse.games)
    assert result.materialization.inserted_materializations == len(warehouse.games)
    assert result.dataset.build.manifest.feature_schema_version == A2_SCHEMA_VERSION
    assert result.dataset.build.manifest.feature_names == list(A2_SCHEMA.output_names)
    assert result.dataset.build.manifest.observed_feature_names != list(
        A2_SCHEMA.output_names
    )
    assert result.dataset.build.manifest.row_count == len(warehouse.games)
    assert all(
        tuple(row.features) == A2_SCHEMA.output_names
        for row in result.dataset.build.rows
    )
    rows_path = (
        artifact_root
        / "datasets"
        / result.dataset.artifact.dataset_id
        / "rows.jsonl"
    )
    assert rows_path.is_file()
    assert session.scalar(select(func.count()).select_from(TrainingDatasetManifest)) == 1
    assert session.scalar(select(func.count()).select_from(ProviderRequestLedger)) == 0
    assert session.scalar(select(func.count()).select_from(Market)) == 0
    assert session.scalar(select(func.count()).select_from(OddsSnapshot)) == 0
    attestation = session.scalar(select(HistoricalEvidenceStateAttestation))
    assert attestation is not None
    structural = [
        item
        for item in attestation.source_bundle
        if item.get("type") == "STRUCTURAL_REFERENCE"
    ]
    assert {item["structural_kind"] for item in structural} >= {
        "VERSIONED_LEAGUE_PRIOR_POLICY",
        "EXACT_CANONICAL_VENUE_IDENTITY",
    }
    assert all(len(item["definition_checksum"]) == 64 for item in structural)


def test_late_acquisition_label_does_not_suppress_prior_elo_rest_or_team_sample(
    session,
) -> None:
    warehouse = _seed_warehouse(session, game_count=3)
    inputs = load_a2_orm_inputs(session, policy=_policy(warehouse))
    rows = tuple(
        A2ChronologicalReconstructor().iter_rows(
            target_games=inputs.target_games,
            final_outcomes=inputs.final_outcomes,
            team_facts=inputs.team_facts,
            player_facts=inputs.player_facts,
            venues=inputs.venues,
            population=A2Population.TIER_A_STRICT,
        )
    )

    assert all(outcome.label_available_at.year == 2026 for outcome in warehouse.outcomes)
    assert rows[0].feature_values["home_team_sample_season"] == 0.0
    assert rows[1].feature_values["home_team_sample_season"] == 1.0
    assert rows[1].feature_values["home_team_sample_14d"] == 1.0
    assert rows[1].feature_values["home_team_sample_30d"] == 1.0
    assert rows[1].feature_values["home_batting_pa_season"] == 4.0
    assert rows[1].feature_values["home_batting_average_season"] == pytest.approx(1 / 3)
    assert rows[1].feature_values["home_batting_on_base_rate_season"] == 0.5
    assert rows[1].feature_values["home_pitching_bf_season"] == 25.0
    assert rows[1].feature_values["home_elo_games"] == 1.0
    assert rows[1].feature_values["home_days_since_previous_game"] is not None
    assert rows[1].metadata.outcome_history_count >= 1


def test_idempotent_rerun_across_different_chunk_sizes(session) -> None:
    warehouse = _seed_warehouse(session, game_count=3)
    policy = _policy(warehouse)
    first = materialize_a2_population(
        session,
        policy=policy,
        generated_at=COMPUTED_AT,
        code_revision="a2-test",
        batch_size=1,
    )
    second = materialize_a2_population(
        session,
        policy=policy,
        generated_at=COMPUTED_AT + timedelta(hours=1),
        code_revision="a2-test",
        batch_size=2,
    )

    assert first.inserted_attestations == 3
    assert first.committed_batches == 3
    assert second.inserted_attestations == 0
    assert second.reused_attestations == 3
    assert second.reused_snapshots == 3
    assert second.reused_materializations == 3
    assert session.scalar(
        select(func.count()).select_from(HistoricalEvidenceStateAttestation)
    ) == 3
    assert session.scalar(
        select(func.count()).select_from(HistoricalFeatureMaterialization)
    ) == 3


def test_strict_and_enhanced_populations_are_isolated_and_enhanced_is_research_only(
    session, artifact_root: Path
) -> None:
    warehouse = _seed_warehouse(session, game_count=4, tier_b_last=True)
    policy = _policy(warehouse, tier_b_count=1)
    strict_inputs = load_a2_orm_inputs(
        session, population=A2Population.TIER_A_STRICT, policy=policy
    )
    enhanced_inputs = load_a2_orm_inputs(
        session, population=A2Population.TIER_B_ENHANCED, policy=policy
    )
    assert len(strict_inputs.target_games) == 3
    assert "777188" not in strict_inputs.target_game_pks.values()
    assert len(enhanced_inputs.target_games) == 4
    assert "777188" in enhanced_inputs.target_game_pks.values()

    materialize_a2_population(
        session,
        inputs=enhanced_inputs,
        population=A2Population.TIER_B_ENHANCED,
        policy=policy,
        generated_at=COMPUTED_AT,
        code_revision="a2-enhanced-test",
        batch_size=2,
    )
    enhanced = build_and_persist_a2_dataset(
        session,
        artifact_root=artifact_root / "enhanced",
        inputs=enhanced_inputs,
        population=A2Population.TIER_B_ENHANCED,
        policy=policy,
        training_cutoff=TRAINING_CUTOFF,
        code_revision="a2-enhanced-test",
        readiness_policy=_readiness_policy(),
        minimum_free_bytes_after_write=0,
    )
    assert enhanced.readiness.status == NOT_READY_STATUS
    assert enhanced.build.manifest.quality_tiers == ["B"]
    assert all(row.historical_quality_tier == "B" for row in enhanced.build.rows)
    enhanced_materializations = tuple(
        session.scalars(
            select(HistoricalFeatureMaterialization).where(
                HistoricalFeatureMaterialization.quality_tier == "B"
            )
        )
    )
    assert len(enhanced_materializations) == 4
    assert all(not row.training_eligible for row in enhanced_materializations)


def test_one_day_target_selector_keeps_full_prior_history(session) -> None:
    warehouse = _seed_warehouse(session, game_count=4)
    selected_date = warehouse.outcomes[2].official_date
    policy = _policy(
        warehouse,
        start=selected_date,
        end=selected_date,
        selected=1,
    )
    inputs = load_a2_orm_inputs(session, policy=policy)
    row = next(
        A2ChronologicalReconstructor().iter_rows(
            target_games=inputs.target_games,
            final_outcomes=inputs.final_outcomes,
            team_facts=inputs.team_facts,
            player_facts=inputs.player_facts,
            venues=inputs.venues,
        )
    )
    assert len(inputs.target_games) == 1
    assert len(inputs.final_outcomes) == 4
    assert row.feature_values["home_team_sample_season"] == 2.0


def test_seven_date_target_selector_is_bounded_but_keeps_full_inventory(session) -> None:
    warehouse = _seed_warehouse(session, game_count=10)
    start = warehouse.outcomes[0].official_date
    end = warehouse.outcomes[6].official_date
    inputs = load_a2_orm_inputs(
        session,
        policy=_policy(warehouse, start=start, end=end, selected=7),
    )

    assert len(inputs.target_games) == 7
    assert len({target.official_date for target in inputs.target_games}) == 7
    assert len(inputs.final_outcomes) == 10


def test_loader_fails_closed_on_missing_mapping_and_unresolved_structural_lineage(
    session,
) -> None:
    warehouse = _seed_warehouse(session, game_count=2)
    policy = _policy(warehouse)
    mapping_id = session.scalar(
        select(ExternalIdMapping.id).where(
            ExternalIdMapping.entity_type == "player",
            ExternalIdMapping.entity_id == warehouse.home_batter.id,
            ExternalIdMapping.source == SOURCE,
        )
    )
    session.execute(delete(ExternalIdMapping).where(ExternalIdMapping.id == mapping_id))
    session.commit()
    with pytest.raises(A2PipelineError, match="no exact MLB mapping"):
        load_a2_orm_inputs(session, policy=policy)

    _mapping(session, "player", warehouse.home_batter.id, "1001")
    session.commit()
    inputs = load_a2_orm_inputs(session, policy=policy)
    unresolved = replace(inputs, structural_references=MappingProxyType({}))
    with pytest.raises(A2PipelineError, match="unresolved contributing identities"):
        materialize_a2_population(
            session,
            inputs=unresolved,
            policy=policy,
            generated_at=COMPUTED_AT,
            code_revision="a2-test",
            batch_size=1,
        )


def test_correction_appends_new_state_and_dataset_selects_exact_current_identity(
    session, artifact_root: Path
) -> None:
    warehouse = _seed_warehouse(session, game_count=3)
    policy = _policy(warehouse)
    materialize_a2_population(
        session,
        policy=policy,
        generated_at=COMPUTED_AT,
        code_revision="a2-test",
        batch_size=2,
    )
    original = session.scalar(
        select(PlayerGameHistoryObservation).where(
            PlayerGameHistoryObservation.game_id == warehouse.games[0].id,
            PlayerGameHistoryObservation.player_id == warehouse.home_batter.id,
            PlayerGameHistoryObservation.role == "BATTER",
        )
    )
    assert original is not None
    corrected_values = dict(original.values)
    corrected_values["batting"] = {
        **corrected_values["batting"],
        "hits": 2,
        "totalBases": 2,
    }
    correction = PlayerGameHistoryObservation(
        game_id=original.game_id,
        final_outcome_id=original.final_outcome_id,
        season_id=original.season_id,
        player_id=original.player_id,
        team_id=original.team_id,
        opponent_team_id=original.opponent_team_id,
        role=original.role,
        started=original.started,
        source_version_id=original.source_version_id,
        checkpoint_id=original.checkpoint_id,
        event_at=original.event_at,
        evidence_end_at=original.evidence_end_at,
        retrieved_at=COMPUTED_AT,
        eligible_at=original.eligible_at,
        availability_basis=original.availability_basis,
        evidence_classification=original.evidence_classification,
        quality_tier=original.quality_tier,
        source_record_id=original.source_record_id,
        revision=2,
        supersedes_observation_id=original.id,
        stat_schema_version=original.stat_schema_version,
        values=corrected_values,
        payload_checksum=_hash("corrected-player-payload"),
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum=_hash("corrected-player-identity"),
    )
    session.add(correction)
    session.commit()

    corrected_inputs = load_a2_orm_inputs(session, policy=policy)
    rerun = materialize_a2_population(
        session,
        inputs=corrected_inputs,
        policy=policy,
        generated_at=COMPUTED_AT + timedelta(hours=1),
        code_revision="a2-test",
        batch_size=2,
    )
    assert rerun.inserted_materializations >= 1
    assert session.scalar(
        select(func.count()).select_from(HistoricalFeatureMaterialization)
    ) > len(warehouse.games)

    dataset = build_and_persist_a2_dataset(
        session,
        artifact_root=artifact_root / "corrected",
        inputs=corrected_inputs,
        policy=policy,
        training_cutoff=TRAINING_CUTOFF,
        code_revision="a2-test",
        readiness_policy=_readiness_policy(),
        minimum_free_bytes_after_write=0,
    )
    assert dataset.build.manifest.row_count == len(warehouse.games)
    assert len({row.game_id for row in dataset.build.rows}) == len(warehouse.games)


def test_snapshot_tamper_is_rejected_by_attestation_checksum(session, artifact_root: Path) -> None:
    warehouse = _seed_warehouse(session, game_count=2)
    policy = _policy(warehouse)
    materialize_a2_population(
        session,
        policy=policy,
        generated_at=COMPUTED_AT,
        code_revision="a2-test",
        batch_size=2,
    )
    snapshot = session.scalar(select(GameFeatureSnapshot).order_by(GameFeatureSnapshot.id))
    assert snapshot is not None
    tampered = dict(snapshot.feature_values)
    tampered["home_elo_rating"] = 9999.0
    session.execute(
        update(GameFeatureSnapshot)
        .where(GameFeatureSnapshot.id == snapshot.id)
        .values(feature_values=tampered)
    )
    session.commit()
    session.expunge_all()

    with pytest.raises(A2PipelineError, match="attestation checksum"):
        build_and_persist_a2_dataset(
            session,
            artifact_root=artifact_root / "tampered",
            policy=policy,
            training_cutoff=TRAINING_CUTOFF,
            code_revision="a2-test",
            readiness_policy=_readiness_policy(),
            minimum_free_bytes_after_write=0,
        )


def test_loader_query_count_is_constant_bounded(session) -> None:
    warehouse = _seed_warehouse(session, game_count=12)
    policy = _policy(warehouse)
    session.expunge_all()
    statements: list[str] = []

    def count_statement(_conn, _cursor, statement, _params, _context, _many) -> None:
        statements.append(statement)

    event.listen(session.bind, "before_cursor_execute", count_statement)
    try:
        inputs = load_a2_orm_inputs(session, policy=policy)
    finally:
        event.remove(session.bind, "before_cursor_execute", count_statement)

    assert len(inputs.target_games) == 12
    assert len(statements) <= 12
