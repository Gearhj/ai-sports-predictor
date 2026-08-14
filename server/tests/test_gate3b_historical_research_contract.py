from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    FeatureVersion,
    Game,
    GameFeatureSnapshot,
    GameFinalOutcome,
    GameStatus,
    GameTeam,
    HistoricalFeatureMaterialization,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    IngestionRun,
    League,
    Player,
    PlayerGameHistoryObservation,
    RunStatus,
    Season,
    Sport,
    Team,
    TeamGameHistoryObservation,
    TrainingDatasetManifest,
)


def _readiness(*, rows: int, ready: bool = False) -> dict:
    status = (
        "READY_FOR_GATE_3B_MODEL_DEVELOPMENT"
        if ready
        else "NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT"
    )
    return {
        "status": status,
        "policy_version": "gate3a_a2_audit_only_readiness_v1",
        "checks": {
            "minimum_rows": {
                "passed": True,
                "observed": rows,
                "required": {"minimum": 200},
            },
            "required_feature_family_coverage": {
                "passed": ready,
                "observed": {"starting_pitcher": 0.0},
                "required": {"minimum_each": 0.70},
            },
        },
        "row_count": rows,
        "distinct_game_count": rows,
        "distinct_event_dates": 184,
        "development_date_groups": 147,
        "holdout_date_groups": 37,
        "holdout_rows": 505,
        "rolling_origin_folds": 16,
        "model_training_invoked": False,
        "model_fitting_invoked": False,
        "holdout_metrics_inspected": False,
        "holdout_outcomes_inspected": False,
    }


def _manifest(
    feature: FeatureVersion,
    *,
    population: str,
    rows: int,
    built_at: datetime,
    schema_hash: str | None = None,
) -> TrainingDatasetManifest:
    strict = population == "TIER_A_STRICT"
    version = "strict-public" if strict else "enhanced-public"
    coverage = {
        "team_strength": 0.997,
        "starting_pitcher": 0.0,
        "lineup": 0.0,
        "bullpen": 1.0,
        "statcast": 0.0,
        "recent_form": 0.996,
        "park_weather": 0.0,
        "rest_travel": 0.883,
        "defense_baserunning": 0.0,
        "opponent_adjustment": 0.0,
    }
    return TrainingDatasetManifest(
        name=(
            "mlb_historical_a2_tier_a_strict"
            if strict
            else "mlb_historical_a2_tier_b_enhanced"
        ),
        version=version,
        feature_version_id=feature.id,
        source_mode="LIVE",
        schema_version="mlb_historical_pregame_a2_v2",
        target="home_win",
        target_definition={"home_win": "final home score > away score"},
        cutoff_policy={
            "policy_version": "mlb_t_minus_6h_v1",
            "hours_before_first_pitch": 6,
            "population": population,
        },
        split_policy={
            "status": "GATE_3A_A2_READINESS_AUDIT_ONLY",
            "readiness": _readiness(rows=rows),
            "model_training_invoked": False,
            "model_fitting_invoked": False,
        },
        feature_names=["feature_a", "feature_a__missing", "feature_b", "feature_b__missing"],
        seasons=[2025],
        event_start_at=datetime(2025, 3, 18, tzinfo=UTC),
        event_end_at=datetime(2025, 9, 28, 23, 59, tzinfo=UTC),
        row_count=rows,
        game_count=rows,
        excluded_row_count=0,
        artifact_uri=f"C:/private/runtime/{version}/rows.jsonl",
        artifact_checksum=("a" if strict else "b") * 64,
        source_evidence=[{"lineage_version": "gate3a_a2_compact_lineage_v1"}],
        quality_summary={
            "stage": "GATE_3A_A2",
            "population": population,
            "schema_hash": schema_hash if schema_hash is not None else feature.schema_hash,
            "raw_value_count": 2,
            "ordered_output_count": 4,
            "quality_tiers": ["A" if strict else "B"],
            "leakage_checks": {
                "one_row_per_game": True,
                "pregame_cutoff": True,
                "live_source_only": True,
                "target_excluded": True,
                "no_market_or_clv": True,
            },
            "quality_score_summary": {"minimum": 51.1, "maximum": 85.7, "mean": 85.4},
            "feature_family_coverage": coverage,
            "raw_a2_feature_family_coverage": {
                "team": 0.996,
                "target_context": 0.0 if strict else 0.85,
            },
            "readiness": _readiness(rows=rows),
            "model_training_invoked": False,
            "model_fitting_invoked": False,
        },
        code_revision="gate3b-public-contract-test",
        identity_checksum=("c" if strict else "d") * 64,
        built_at=built_at,
    )


def _seed_history(
    session,
    *,
    malformed_strict: bool = False,
    strict_rows: int = 2428,
    enhanced_rows: int = 2430,
) -> tuple[FeatureVersion, FeatureVersion]:
    strict_feature = FeatureVersion(
        name="mlb_historical_a2_tier_a_strict",
        version="a2-tier-a-strict-v6",
        schema_hash="e" * 64,
    )
    enhanced_feature = FeatureVersion(
        name="mlb_historical_a2_tier_b_enhanced",
        version="a2-tier-b-enhanced-v6",
        schema_hash="f" * 64,
    )
    session.add_all([strict_feature, enhanced_feature])
    session.flush()
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    session.add_all([
        _manifest(
            strict_feature,
            population="TIER_A_STRICT",
            rows=strict_rows,
            built_at=now,
            schema_hash="not-a-checksum" if malformed_strict else None,
        ),
        _manifest(
            enhanced_feature,
            population="TIER_B_ENHANCED",
            rows=enhanced_rows,
            built_at=now + timedelta(minutes=1),
        ),
    ])
    session.commit()
    return strict_feature, enhanced_feature


def _seed_explorer_graph(
    session,
    *,
    broken_strict_lineage: bool = False,
) -> None:
    strict_feature, enhanced_feature = _seed_history(
        session,
        strict_rows=1,
        enhanced_rows=1,
    )
    sport = Sport(code="MLB-HISTORY-PUBLIC", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(
        sport_id=sport.id,
        code="MLB-HISTORY-PUBLIC",
        name="Major League Baseball",
    )
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id,
        name="2025-public",
        starts_at=datetime(2025, 3, 1, tzinfo=UTC),
        ends_at=datetime(2025, 11, 1, tzinfo=UTC),
    )
    home = Team(league_id=league.id, name="New York Home", abbreviation="NYH")
    away = Team(league_id=league.id, name="Boston Away", abbreviation="BOS")
    session.add_all([season, home, away])
    session.flush()
    home_player = Player(team_id=home.id, full_name="Home Player", position="P")
    away_player = Player(team_id=away.id, full_name="Away Player", position="P")
    first_pitch = datetime(2025, 4, 5, 23, 10, tzinfo=UTC)
    final_at = first_pitch + timedelta(hours=3)
    game = Game(
        league_id=league.id,
        season_id=season.id,
        starts_at=first_pitch,
        status=GameStatus.FINAL,
        home_score=5,
        away_score=3,
        final_at=final_at,
        source_mode="LIVE",
    )
    run = IngestionRun(
        source="MLB_STATS_API",
        job_name="backfill-mlb-history",
        status=RunStatus.SUCCEEDED,
        records_processed=5,
    )
    session.add_all([home_player, away_player, game, run])
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    source = HistoricalSourceVersion(
        source_key="MLB_STATS_API",
        dataset_version="2025-public-v1",
        provider_name="MLB Stats API",
        source_mode="LIVE",
        source_classification="OFFICIAL_PRIMARY",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        quality_tier="A",
        training_eligible=True,
        evaluation_eligible=True,
        availability_policy={"kind": "completed_event"},
        policy_version="mlb-history-v1",
        effective_from=datetime(2025, 1, 1, tzinfo=UTC),
        identity_checksum="1" * 64,
    )
    session.add(source)
    session.flush()
    requested_at = datetime(2026, 8, 14, 10, tzinfo=UTC)
    checkpoint = HistoryBackfillCheckpoint(
        ingestion_run_id=run.id,
        source_version_id=source.id,
        stream_key="schedule:2025-04-05",
        sequence=0,
        season=2025,
        event_start_at=datetime(2025, 4, 5, tzinfo=UTC),
        event_end_at=datetime(2025, 4, 5, 23, 59, tzinfo=UTC),
        query_fingerprint="2" * 64,
        status="SUCCEEDED",
        requested_at=requested_at,
        completed_at=requested_at + timedelta(seconds=1),
        attempt_count=1,
        retry_count=0,
        records_received=5,
        records_accepted=5,
        records_rejected=0,
        duplicate_count=0,
        raw_references=[],
        raw_checksums=[],
        schema_version="mlb-history-v1",
        code_revision="test-revision",
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="3" * 64,
    )
    session.add(checkpoint)
    session.flush()
    outcome = GameFinalOutcome(
        game_id=game.id,
        season_id=season.id,
        home_team_id=home.id,
        away_team_id=away.id,
        source_version_id=source.id,
        checkpoint_id=checkpoint.id,
        official_date=first_pitch.date(),
        scheduled_at=first_pitch,
        final_at=final_at,
        home_score=5,
        away_score=3,
        outcome_status="FINAL",
        revision=1,
        official=True,
        source_record_id="game:public:1",
        retrieved_at=requested_at,
        label_available_at=final_at,
        availability_basis="OFFICIAL_FINAL_EVENT",
        payload_checksum="4" * 64,
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="5" * 64,
    )
    session.add(outcome)
    session.flush()
    team_rows = []
    player_rows = []
    for index, (team, opponent, player, is_home) in enumerate((
        (home, away, home_player, True),
        (away, home, away_player, False),
    )):
        team_rows.append(TeamGameHistoryObservation(
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
            retrieved_at=requested_at,
            eligible_at=final_at,
            availability_basis="OFFICIAL_COMPLETED_GAME",
            evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
            quality_tier="A",
            source_record_id=f"team:{index}",
            stat_schema_version="team-v1",
            values={"runs": 5 if is_home else 3},
            payload_checksum=str(6 + index) * 64,
            source_mode="LIVE",
            temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
            identity_checksum=str(8 + index) * 64,
        ))
        player_rows.append(PlayerGameHistoryObservation(
            game_id=game.id,
            final_outcome_id=outcome.id,
            season_id=season.id,
            player_id=player.id,
            team_id=team.id,
            opponent_team_id=opponent.id,
            role="PITCHER",
            started=True,
            source_version_id=source.id,
            checkpoint_id=checkpoint.id,
            event_at=final_at,
            evidence_end_at=final_at,
            retrieved_at=requested_at,
            eligible_at=final_at,
            availability_basis="OFFICIAL_COMPLETED_GAME",
            evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
            quality_tier="A",
            source_record_id=f"player:{index}",
            stat_schema_version="player-v1",
            values={"innings_pitched": 6},
            payload_checksum=("a" if index == 0 else "b") * 64,
            source_mode="LIVE",
            temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
            identity_checksum=("c" if index == 0 else "d") * 64,
        ))
    session.add_all([*team_rows, *player_rows])
    cutoff = first_pitch - timedelta(hours=6)
    source_bundle = [{
        "type": "CANONICAL_SOURCE_CHECKPOINT_BUNDLE",
        "source_key": "MLB_STATS_API",
        "source_version_id": str(source.id),
        "checkpoint_id": str(checkpoint.id),
        "target_label_excluded_from_feature_state": True,
    }]
    evidence = [{
        "type": "A2_COMPACT_EVIDENCE_STATE_ATTESTATION",
        "target_excluded": True,
        "temporal_valid": True,
    }]
    base_values = {
        "home_elo_rating": 1520,
        "away_elo_rating": 1490,
        "home_runs_per_game_season": 4.8,
        "away_runs_per_game_season": 4.4,
        "home_runs_allowed_per_game_season": 4.1,
        "away_runs_allowed_per_game_season": 4.6,
        "home_offense_true_talent": 4.7,
        "away_offense_true_talent": 4.45,
        "home_run_prevention_true_talent": 4.15,
        "away_run_prevention_true_talent": 4.55,
        "home_team_reliability": 0.78,
        "away_team_reliability": 0.76,
        "elo_expected_home_probability": 0.57,
    }
    for index, (tier, feature, confirmed) in enumerate((
        ("A", strict_feature, False),
        ("B", enhanced_feature, True),
    )):
        values = {
            **base_values,
            "starters_confirmed": 1 if confirmed else 0,
            "lineups_confirmed": 1 if confirmed else 0,
            "home_starter_prior_era": 3.7 if confirmed else None,
            "away_starter_prior_era": 4.1 if confirmed else None,
            "home_lineup_member_count": 9 if confirmed else None,
            "away_lineup_member_count": 9 if confirmed else None,
        }
        missing = [key for key, value in values.items() if value is None]
        snapshot = GameFeatureSnapshot(
            game_id=game.id,
            generated_at=requested_at,
            data_as_of=cutoff,
            feature_schema_version="mlb_historical_pregame_a2_v2",
            feature_version_id=feature.id,
            bullpen_observation_ids=[],
            roster_observation_ids=[],
            statcast_observation_ids=[],
            market_snapshot_ids=[],
            source_evidence=(
                [] if tier == "A" and broken_strict_lineage else evidence
            ),
            feature_values=values,
            feature_completeness=0.85 if tier == "A" else 0.95,
            missing_features=missing,
            stale_features=[],
            provisional_features=[],
            checksum=("e" if tier == "A" else "f") * 64,
            source_mode="LIVE",
            frozen_at=requested_at,
        )
        session.add(snapshot)
        session.flush()
        session.add(HistoricalFeatureMaterialization(
            game_id=game.id,
            game_feature_snapshot_id=snapshot.id,
            feature_version_id=feature.id,
            game_final_outcome_id=outcome.id,
            checkpoint_id=checkpoint.id,
            scheduled_at=first_pitch,
            cutoff_at=cutoff,
            generated_at=requested_at,
            cutoff_policy_version="mlb_t_minus_6h_v1",
            cutoff_policy={"hours_before_first_pitch": 6},
            source_bundle=source_bundle,
            source_bundle_checksum=("1" if tier == "A" else "2") * 64,
            evidence_manifest=evidence,
            evidence_manifest_checksum=("3" if tier == "A" else "4") * 64,
            quality_tier=tier,
            quality_score=85 if tier == "A" else 95,
            quality_components={"fixture": True},
            materializer_version=f"public-{tier.lower()}-v1",
            code_revision="test-revision",
            eligibility_status="ELIGIBLE",
            eligibility_reasons=[],
            training_eligible=tier == "A",
            source_mode="LIVE",
            temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
            identity_checksum=("5" if tier == "A" else "6") * 64,
        ))
    session.commit()


def test_history_endpoint_is_empty_without_governed_manifests(client) -> None:
    response = client.get("/api/v1/research/history")

    assert response.status_code == 200
    assert response.json() == {
        "data_mode": "empty",
        "availability": "UNAVAILABLE",
        "temporal_scope": "HISTORICAL",
        "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
        "stage": "GATE_3A_A2",
        "season": 2025,
        "cutoff_policy_version": None,
        "built_at": None,
        "verdict": "UNAVAILABLE",
        "historical_market_status": "UNAVAILABLE",
        "model_training_invoked": None,
        "model_fitting_invoked": None,
        "holdout_metrics_inspected": None,
        "promotion_invoked": None,
        "strict_population": None,
        "enhanced_population": None,
        "canonical_summary": {
            "availability": "UNAVAILABLE",
            "integrity_status": "UNAVAILABLE",
            "official_games": None,
            "team_game_rows": None,
            "player_game_rows": None,
            "strict_feature_coverage": None,
            "leakage_status": "UNAVAILABLE",
            "statcast_status": "UNAVAILABLE",
            "historical_market_status": "UNAVAILABLE",
            "source_keys": [],
            "integrity_issues": [
                "Both governed A2 populations are required to prove canonical counts."
            ],
        },
        "limitations": [
            "Historical market and closing-line evidence is unavailable and was not imputed.",
            "The immutable A2 materialization/readiness audit itself published no "
            "model fit, backtest metric, promotion decision, or live forecast. "
            "Subsequent governed model evaluation is reported separately on Performance.",
        ],
    }


def test_history_endpoint_separates_strict_and_research_only_evidence(
    client,
    session,
) -> None:
    _seed_history(session)

    response = client.get("/api/v1/research/history")
    assert response.status_code == 200
    body = response.json()

    assert body["data_mode"] == "live"
    assert body["availability"] == "AVAILABLE"
    assert body["temporal_scope"] == "HISTORICAL"
    assert body["verdict"] == "NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT"
    assert body["cutoff_policy_version"] == "mlb_t_minus_6h_v1"
    assert body["model_training_invoked"] is False
    assert body["model_fitting_invoked"] is False
    assert body["holdout_metrics_inspected"] is False
    strict = body["strict_population"]
    enhanced = body["enhanced_population"]
    assert strict["population"] == "TIER_A_STRICT"
    assert strict["role"] == "PRIMARY_STRICT"
    assert strict["quality_tiers"] == ["A"]
    assert strict["row_count"] == strict["game_count"] == 2428
    assert strict["readiness"]["holdout_rows"] == 505
    assert strict["readiness"]["failed_checks"] == [
        "required_feature_family_coverage"
    ]
    assert enhanced["population"] == "TIER_B_ENHANCED"
    assert enhanced["role"] == "RESEARCH_ONLY_SHADOW"
    assert enhanced["quality_tiers"] == ["B"]
    assert enhanced["row_count"] == 2430
    serialized = response.text
    assert "private/runtime" not in serialized
    assert "artifact_uri" not in serialized
    assert "home_score" not in serialized
    assert "brier_score" not in serialized


def test_history_integrity_failure_blocks_readiness_claims(client, session) -> None:
    _seed_history(session, malformed_strict=True)

    body = client.get("/api/v1/research/history").json()
    assert body["availability"] == "INTEGRITY_WARNING"
    assert body["strict_population"]["integrity_status"] == "FAILED"
    assert "feature schema hash is invalid" in body["strict_population"]["integrity_issues"]
    performance = client.get("/api/v1/performance").json()
    assert performance["historical_dataset"]["availability"] == "INTEGRITY_WARNING"
    assert performance["offline_evaluation"]["validation_metrics"] is None
    assert performance["offline_evaluation"]["holdout_metrics"] is None


def test_performance_and_health_keep_historical_evidence_separate(
    client,
    session,
) -> None:
    health_before = client.get("/api/v1/health/sources?date=2026-08-14").json()
    _seed_history(session)

    performance = client.get("/api/v1/performance").json()
    historical = performance["historical_dataset"]
    assert historical["row_count"] == 2428
    assert historical["readiness_status"] == "NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT"
    assert historical["model_training_invoked"] is False
    assert historical["model_fitting_invoked"] is False
    assert historical["holdout_metrics_inspected"] is False
    assert performance["offline_evaluation"]["status"] == "NOT_RUN"
    assert performance["offline_evaluation"]["promotion"]["decision"] == "BLOCKED"
    assert performance["offline_evaluation"]["validation_metrics"] is None
    assert performance["offline_evaluation"]["holdout_metrics"] is None

    health_after = client.get("/api/v1/health/sources?date=2026-08-14").json()
    assert health_after["overall_score"] == health_before["overall_score"]
    assert health_after["sports_data_status"] == health_before["sports_data_status"]
    assert health_after["provider_evidence_status"] == health_before["provider_evidence_status"]
    warehouse = health_after["historical_warehouse"]
    assert warehouse["strict_rows"] == 2428
    assert warehouse["enhanced_rows"] == 2430
    assert warehouse["readiness_status"] == "NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT"
    assert "does not affect the live health score" in warehouse["detail"]


def test_history_publishes_proven_canonical_counts_and_safe_game_explorer(
    client,
    session,
) -> None:
    _seed_explorer_graph(session)

    history = client.get("/api/v1/research/history").json()
    canonical = history["canonical_summary"]
    assert canonical["strict_feature_coverage"] == pytest.approx(0.854)
    assert {**canonical, "strict_feature_coverage": 0.854} == {
        "availability": "AVAILABLE",
        "integrity_status": "PASS",
        "official_games": 1,
        "team_game_rows": 2,
        "player_game_rows": 2,
        "strict_feature_coverage": 0.854,
        "leakage_status": "PASS",
        "statcast_status": "UNAVAILABLE",
        "historical_market_status": "UNAVAILABLE",
        "source_keys": ["MLB_STATS_API"],
        "integrity_issues": [],
    }

    response = client.get(
        "/api/v1/research/history/games"
        "?team=Boston&month=4&tier=A&source=MLB_STATS_API&page=1&page_size=10"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data_mode"] == "live"
    assert body["availability"] == "AVAILABLE"
    assert body["total_rows"] == body["total_pages"] == 1
    row = body["rows"][0]
    assert row["event_date"] == "2025-04-05"
    assert row["matchup"] == "BOS at NYH"
    assert row["quality_tier"] == "A"
    assert row["feature_completeness"] == 0.85
    assert row["starter_state"] == row["lineup_state"] == "MISSING"
    assert row["home_team_prior"]["elo_rating"] == 1520
    assert row["run_strength"]["expected_home_win_probability"] == 0.57
    assert row["market_available"] is None
    assert row["market_status"] == "UNAVAILABLE"
    assert row["target_excluded"] is True
    serialized = response.text
    assert "home_score" not in serialized
    assert "away_score" not in serialized
    assert "artifact_uri" not in serialized
    assert "payload_checksum" not in serialized

    enhanced = client.get(
        "/api/v1/research/history/games?tier=B&page=1&page_size=10"
    ).json()
    assert enhanced["rows"][0]["starter_state"] == "CONFIRMED"
    assert enhanced["rows"][0]["lineup_state"] == "CONFIRMED"


def test_game_explorer_scopes_integrity_and_rows_to_latest_manifest_features(
    client,
    session,
) -> None:
    _seed_explorer_graph(session)
    current_materialization = session.scalar(
        select(HistoricalFeatureMaterialization).where(
            HistoricalFeatureMaterialization.quality_tier == "A"
        )
    )
    assert current_materialization is not None
    current_snapshot = session.get(
        GameFeatureSnapshot,
        current_materialization.game_feature_snapshot_id,
    )
    assert current_snapshot is not None

    stale_feature = FeatureVersion(
        name="mlb_historical_a2_tier_a_strict",
        version="a2-tier-a-strict-v1-stale",
        schema_hash="7" * 64,
    )
    session.add(stale_feature)
    session.flush()
    stale_snapshot = GameFeatureSnapshot(
        game_id=current_snapshot.game_id,
        generated_at=current_snapshot.generated_at,
        data_as_of=current_snapshot.data_as_of,
        feature_schema_version=current_snapshot.feature_schema_version,
        feature_version_id=stale_feature.id,
        bullpen_observation_ids=list(current_snapshot.bullpen_observation_ids),
        roster_observation_ids=list(current_snapshot.roster_observation_ids),
        statcast_observation_ids=list(current_snapshot.statcast_observation_ids),
        market_snapshot_ids=list(current_snapshot.market_snapshot_ids),
        source_evidence=[],
        feature_values=dict(current_snapshot.feature_values),
        feature_completeness=current_snapshot.feature_completeness,
        missing_features=list(current_snapshot.missing_features),
        stale_features=list(current_snapshot.stale_features),
        provisional_features=list(current_snapshot.provisional_features),
        checksum="8" * 64,
        source_mode=current_snapshot.source_mode,
        frozen_at=current_snapshot.frozen_at,
    )
    session.add(stale_snapshot)
    session.flush()
    session.add(HistoricalFeatureMaterialization(
        game_id=current_materialization.game_id,
        game_feature_snapshot_id=stale_snapshot.id,
        feature_version_id=stale_feature.id,
        game_final_outcome_id=current_materialization.game_final_outcome_id,
        checkpoint_id=current_materialization.checkpoint_id,
        scheduled_at=current_materialization.scheduled_at,
        cutoff_at=current_materialization.cutoff_at,
        generated_at=current_materialization.generated_at,
        cutoff_policy_version=current_materialization.cutoff_policy_version,
        cutoff_policy=dict(current_materialization.cutoff_policy),
        source_bundle=list(current_materialization.source_bundle),
        source_bundle_checksum="9" * 64,
        evidence_manifest=list(current_materialization.evidence_manifest),
        evidence_manifest_checksum="0" * 64,
        quality_tier="A",
        quality_score=current_materialization.quality_score,
        quality_components=dict(current_materialization.quality_components),
        materializer_version="stale-public-a-v1",
        code_revision="stale-test-revision",
        eligibility_status=current_materialization.eligibility_status,
        eligibility_reasons=list(current_materialization.eligibility_reasons),
        training_eligible=True,
        source_mode="LIVE",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        identity_checksum="1" * 64,
    ))
    session.commit()

    available = client.get(
        "/api/v1/research/history/games?tier=A&page=1&page_size=10"
    ).json()
    assert available["availability"] == "AVAILABLE"
    assert available["total_rows"] == 1
    assert len(available["rows"]) == 1


def test_game_explorer_still_blocks_broken_current_feature_lineage(
    client,
    session,
) -> None:
    _seed_explorer_graph(session, broken_strict_lineage=True)

    blocked = client.get(
        "/api/v1/research/history/games?tier=A&page=1&page_size=10"
    ).json()
    assert blocked["availability"] == "INTEGRITY_WARNING"
    assert blocked["rows"] == []
    assert "target-exclusion or temporal attestation is missing" in blocked[
        "status_reason"
    ]


def test_game_explorer_fails_closed_without_canonical_integrity(
    client,
    session,
) -> None:
    _seed_history(session)

    body = client.get("/api/v1/research/history/games").json()
    assert body["data_mode"] == "empty"
    assert body["availability"] == "INTEGRITY_WARNING"
    assert body["rows"] == []
    assert "integrity could not be proven" in body["status_reason"]

    assert client.get("/api/v1/research/history/games?month=13").status_code == 422
    assert client.get("/api/v1/research/history/games?tier=C").status_code == 422
