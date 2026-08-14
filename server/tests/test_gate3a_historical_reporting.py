from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.historical.reporting import HistoricalReportPaths, build_mlb_history_report
from app.models import (
    BullpenAvailabilityObservation,
    ExternalIdMapping,
    FeatureVersion,
    Game,
    GameFeatureSnapshot,
    GameFinalOutcome,
    GameStateObservation,
    GameStatus,
    HistoricalFeatureMaterialization,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    IngestionRun,
    League,
    ParkFactorVersion,
    Player,
    PlayerGameHistoryObservation,
    RosterAvailabilityObservation,
    RunStatus,
    Season,
    Sport,
    StatcastPlayerObservation,
    Team,
    TeamGameHistoryObservation,
    Venue,
    WeatherSnapshot,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _paths(root: Path) -> HistoricalReportPaths:
    return HistoricalReportPaths(
        raw_root=root / "raw",
        curated_root=root / "curated",
        feature_root=root / "features",
        runtime_root=root / "runtime",
    )


def test_empty_report_is_json_serializable_and_never_divides_by_zero(
    session,
    artifact_root: Path,
) -> None:
    report = build_mlb_history_report(
        session,
        paths=_paths(artifact_root),
        season=2025,
        generated_at=datetime(2026, 8, 14, tzinfo=UTC),
    )

    assert report["report_execution_provider_requests"] == 0
    assert report["season_count"] == 1
    season = report["seasons"][0]
    assert season["games"] == {
        "normalized_live": 0,
        "official_live_finals": 0,
        "official_final_records_including_revisions": 0,
    }
    assert [row["family"] for row in season["coverage"]] == [
        "finals",
        "starters",
        "actual_lineups",
        "roster",
        "team_history",
        "player_history",
        "bullpen",
        "statcast",
        "park",
        "weather",
        "rest_travel",
        "platoon",
        "defense",
        "baserunning",
        "opponent_adjustment",
        "tier_a_b_eligible",
    ]
    assert all(row["status"] == "UNAVAILABLE" for row in season["coverage"])
    assert all(row["coverage"] is None for row in season["coverage"])
    assert season["validation_sample"]["status"] == "UNAVAILABLE"
    assert season["validation_sample"]["selected"] == 0
    assert report["feature_drift"]["status"] == "UNAVAILABLE"
    assert report["source_operations"] == []
    assert report["storage"]["raw"] | {
        "status": "AVAILABLE",
        "exists": False,
        "bytes": 0,
        "files": 0,
        "unreadable": 0,
    } == report["storage"]["raw"]
    json.dumps(report, allow_nan=False)


def test_multi_season_report_is_exact_deterministic_and_honest(
    session,
    artifact_root: Path,
) -> None:
    paths = _paths(artifact_root)
    raw_file = paths.raw_root / "historical" / "mlb_stats_api" / "pilot.json.gz"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_bytes(b"raw-pilot")
    parquet_file = paths.curated_root / "history" / "part.parquet"
    parquet_file.parent.mkdir(parents=True)
    parquet_file.write_bytes(b"parquet")
    feature_file = paths.feature_root / "history" / "features.parquet"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_bytes(b"feature-parquet")
    runtime_file = paths.runtime_root / "manifest.json"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_bytes(b"runtime")

    sport = Sport(code="MLB-REPORT", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="Major League Baseball")
    session.add(league)
    session.flush()
    home = Team(league_id=league.id, name="Home", abbreviation="HOM")
    away = Team(league_id=league.id, name="Away", abbreviation="AWY")
    venue = Venue(name="Report Park", timezone="America/New_York")
    players = [
        Player(team_id=None, full_name="Pitcher One", position="P"),
        Player(team_id=None, full_name="Pitcher Two", position="P"),
        Player(team_id=None, full_name="Pitcher Three", position="P"),
    ]
    session.add_all([home, away, venue, *players])
    session.flush()
    for player in players:
        player.team_id = home.id
    feature_version = FeatureVersion(
        name="gate3a-report",
        version="1",
        schema_hash=_sha("feature-version"),
        definition={"purpose": "report fixture"},
    )
    run = IngestionRun(
        source="MLB_STATS_API",
        job_name="backfill-mlb-history",
        status=RunStatus.SUCCEEDED,
        records_processed=24,
    )
    source = HistoricalSourceVersion(
        source_key="MLB_STATS_API",
        dataset_version="report-v1",
        provider_name="MLB Stats API",
        source_mode="LIVE",
        source_classification="OFFICIAL_PRIMARY",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        quality_tier="A",
        training_eligible=True,
        evaluation_eligible=True,
        availability_policy={"basis": "completed_event"},
        policy_version="report-v1",
        effective_from=datetime(2024, 1, 1, tzinfo=UTC),
        identity_checksum=_sha("source"),
    )
    session.add_all([feature_version, run, source])
    session.flush()
    mappings = [
        ExternalIdMapping(entity_type="team", entity_id=home.id, source="MLB_STATS_API", external_id="1"),
        ExternalIdMapping(entity_type="team", entity_id=away.id, source="MLB_STATS_API", external_id="2"),
        ExternalIdMapping(entity_type="venue", entity_id=venue.id, source="MLB_STATS_API", external_id="10"),
    ]
    mappings.extend(
        ExternalIdMapping(
            entity_type="player",
            entity_id=player.id,
            source="MLB_STATS_API",
            external_id=str(index + 100),
        )
        for index, player in enumerate(players)
    )
    session.add_all(mappings)

    for season_index, year in enumerate((2024, 2025)):
        season = Season(
            league_id=league.id,
            name=str(year),
            starts_at=datetime(year, 3, 1, tzinfo=UTC),
            ends_at=datetime(year, 11, 1, tzinfo=UTC),
        )
        session.add(season)
        session.flush()
        requested = datetime(2026, 8, 14, 12 + season_index, tzinfo=UTC)
        checkpoint = HistoryBackfillCheckpoint(
            ingestion_run_id=run.id,
            source_version_id=source.id,
            stream_key=f"schedule:{year}",
            sequence=0,
            season=year,
            event_start_at=datetime(year, 4, 1, tzinfo=UTC),
            event_end_at=datetime(year, 4, 12, 23, 59, tzinfo=UTC),
            query_fingerprint=_sha(f"query:{year}:success"),
            status="SUCCEEDED",
            requested_at=requested,
            completed_at=requested + timedelta(seconds=3),
            attempt_count=2,
            retry_count=1,
            records_received=120,
            records_accepted=118,
            records_rejected=1,
            duplicate_count=1,
            raw_references=[str(raw_file.resolve())],
            raw_checksums=[_sha(f"raw:{year}")],
            schema_version="report-v1",
            code_revision="test",
            source_mode="LIVE",
            temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
            identity_checksum=_sha(f"checkpoint:{year}:success"),
        )
        failed_checkpoint = HistoryBackfillCheckpoint(
            ingestion_run_id=run.id,
            source_version_id=source.id,
            stream_key=f"boxscore:{year}",
            sequence=0,
            season=year,
            event_start_at=datetime(year, 4, 1, tzinfo=UTC),
            event_end_at=datetime(year, 4, 1, 23, 59, tzinfo=UTC),
            query_fingerprint=_sha(f"query:{year}:failed"),
            status="FAILED",
            requested_at=requested,
            completed_at=requested + timedelta(seconds=5),
            attempt_count=3,
            retry_count=2,
            records_received=0,
            records_accepted=0,
            records_rejected=0,
            duplicate_count=0,
            raw_references=[],
            raw_checksums=[],
            schema_version="report-v1",
            code_revision="test",
            error_type="SourceError",
            error_message="fixture failure",
            source_mode="LIVE",
            temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
            identity_checksum=_sha(f"checkpoint:{year}:failed"),
        )
        session.add_all([checkpoint, failed_checkpoint])
        session.flush()
        park = ParkFactorVersion(
            venue_id=venue.id,
            season=year,
            version="report-v1",
            overall_run_factor=1.01,
            observed_at=datetime(year, 3, 1, tzinfo=UTC),
            available_as_of=datetime(year, 3, 1, tzinfo=UTC),
            source="fixture",
            source_mode="LIVE",
            source_record_id=f"park:{year}",
            payload_checksum=_sha(f"park:{year}"),
        )
        rosters = [
            RosterAvailabilityObservation(
                season_id=season.id,
                team_id=home.id if index != 1 else away.id,
                player_id=player.id,
                availability_status="ACTIVE",
                effective_at=datetime(year, 3, 1, tzinfo=UTC),
                observed_at=datetime(year, 3, 1, tzinfo=UTC),
                available_as_of=datetime(year, 3, 1, tzinfo=UTC),
                source="fixture",
                source_mode="LIVE",
                source_record_id=f"roster:{year}:{index}",
                payload_checksum=_sha(f"roster:{year}:{index}"),
            )
            for index, player in enumerate(players)
        ]
        statcast = [
            StatcastPlayerObservation(
                player_id=player.id,
                team_id=home.id if index != 1 else away.id,
                season=year,
                role="PITCHER",
                evidence_cutoff=datetime(year, 3, 1, tzinfo=UTC),
                sample_size=20,
                feature_schema_version="report-v1",
                values={"xera": 4.0},
                observed_at=datetime(year, 3, 1, tzinfo=UTC),
                available_as_of=datetime(year, 3, 1, tzinfo=UTC),
                source="fixture",
                source_mode="LIVE",
                source_record_id=f"statcast:{year}:{index}",
                payload_checksum=_sha(f"statcast:{year}:{index}"),
            )
            for index, player in enumerate(players)
        ]
        session.add_all([park, *rosters, *statcast])
        session.flush()

        for game_index in range(12):
            starts_at = datetime(year, 4, 1, 19, tzinfo=UTC) + timedelta(days=game_index)
            final_at = starts_at + timedelta(hours=3)
            game = Game(
                league_id=league.id,
                season_id=season.id,
                venue_id=venue.id,
                starts_at=starts_at,
                status=GameStatus.FINAL,
                home_score=5,
                away_score=3,
                final_at=final_at,
                scheduled_innings=9,
                source_mode="LIVE",
            )
            session.add(game)
            session.flush()
            if game_index != 11:
                session.add(ExternalIdMapping(
                    entity_type="game",
                    entity_id=game.id,
                    source="MLB_STATS_API",
                    external_id=f"{year}{game_index:02d}",
                ))
            outcome = GameFinalOutcome(
                game_id=game.id,
                season_id=season.id,
                home_team_id=home.id,
                away_team_id=away.id,
                venue_id=venue.id,
                source_version_id=source.id,
                checkpoint_id=checkpoint.id,
                scheduled_at=starts_at,
                final_at=final_at,
                scheduled_innings=9,
                innings_played=9,
                home_score=5,
                away_score=3,
                outcome_status="FINAL",
                revision=1,
                official=True,
                source_record_id=f"game:{year}:{game_index}",
                source_published_at=final_at,
                retrieved_at=datetime(2026, 8, 14, tzinfo=UTC),
                label_available_at=final_at,
                availability_basis="OFFICIAL_FINAL_EVENT",
                payload_checksum=_sha(f"outcome-payload:{year}:{game_index}"),
                source_mode="LIVE",
                temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                identity_checksum=_sha(f"outcome:{year}:{game_index}"),
            )
            state = GameStateObservation(
                game_id=game.id,
                home_team_id=home.id,
                away_team_id=away.id,
                scheduled_at=starts_at,
                status="FINAL",
                doubleheader_status="Y" if game_index == 0 else "N",
                game_number=2 if game_index == 0 else 1,
                observed_at=final_at,
                available_as_of=final_at,
                source="fixture",
                source_mode="LIVE",
                source_record_id=f"state:{year}:{game_index}",
                payload_checksum=_sha(f"state:{year}:{game_index}"),
            )
            weather = WeatherSnapshot(
                game_id=game.id,
                source="fixture",
                observed_at=starts_at - timedelta(hours=7),
                available_as_of=starts_at - timedelta(hours=7),
                temperature_f=70,
                wind_mph=5,
                precipitation_probability=0,
                payload_checksum=_sha(f"weather:{year}:{game_index}"),
                source_mode="LIVE",
            )
            session.add_all([outcome, state, weather])
            session.flush()

            team_facts = []
            for is_home, team, opponent in (
                (True, home, away),
                (False, away, home),
            ):
                team_facts.append(TeamGameHistoryObservation(
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
                    retrieved_at=datetime(2026, 8, 14, tzinfo=UTC),
                    eligible_at=final_at,
                    availability_basis="OFFICIAL_COMPLETED_GAME",
                    evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
                    quality_tier="A",
                    source_record_id=f"team:{year}:{game_index}:{team.id}",
                    stat_schema_version="report-v1",
                    values={"runs": 5 if is_home else 3},
                    payload_checksum=_sha(f"team-payload:{year}:{game_index}:{team.id}"),
                    source_mode="LIVE",
                    temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                    identity_checksum=_sha(f"team:{year}:{game_index}:{team.id}"),
                ))
            traded_pitcher = players[0]
            home_pitcher = traded_pitcher if game_index < 6 else players[2]
            away_pitcher = players[1] if game_index < 6 else traded_pitcher
            player_facts = []
            for team, opponent, player in (
                (home, away, home_pitcher),
                (away, home, away_pitcher),
            ):
                player_facts.append(PlayerGameHistoryObservation(
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
                    retrieved_at=datetime(2026, 8, 14, tzinfo=UTC),
                    eligible_at=final_at,
                    availability_basis="OFFICIAL_COMPLETED_GAME",
                    evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
                    quality_tier="A",
                    source_record_id=f"player:{year}:{game_index}:{team.id}:{player.id}",
                    stat_schema_version="report-v1",
                    values={"innings_pitched": 6, "is_rookie": year == 2025},
                    payload_checksum=_sha(f"player-payload:{year}:{game_index}:{team.id}:{player.id}"),
                    source_mode="LIVE",
                    temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                    identity_checksum=_sha(f"player:{year}:{game_index}:{team.id}:{player.id}"),
                ))
            bullpen = [
                BullpenAvailabilityObservation(
                    game_id=game.id,
                    team_id=team.id,
                    player_id=player.id,
                    expected_available=True,
                    workload={"known": True},
                    observed_at=starts_at - timedelta(hours=7),
                    available_as_of=starts_at - timedelta(hours=7),
                    source="fixture",
                    source_mode="LIVE",
                    source_record_id=f"bullpen:{year}:{game_index}:{team.id}",
                    payload_checksum=_sha(f"bullpen:{year}:{game_index}:{team.id}"),
                )
                for team, player in ((home, players[0]), (away, players[1]))
            ]
            session.add_all([*team_facts, *player_facts, *bullpen])
            session.flush()
            if game_index == 0:
                original_team = team_facts[0]
                original_player = player_facts[0]
                session.add_all([
                    TeamGameHistoryObservation(
                        game_id=original_team.game_id,
                        final_outcome_id=original_team.final_outcome_id,
                        season_id=original_team.season_id,
                        team_id=original_team.team_id,
                        opponent_team_id=original_team.opponent_team_id,
                        is_home=original_team.is_home,
                        source_version_id=original_team.source_version_id,
                        checkpoint_id=original_team.checkpoint_id,
                        event_at=original_team.event_at,
                        evidence_end_at=original_team.evidence_end_at,
                        retrieved_at=original_team.retrieved_at,
                        eligible_at=original_team.eligible_at,
                        availability_basis=original_team.availability_basis,
                        evidence_classification=original_team.evidence_classification,
                        quality_tier=original_team.quality_tier,
                        source_record_id=original_team.source_record_id,
                        revision=2,
                        supersedes_observation_id=original_team.id,
                        stat_schema_version=original_team.stat_schema_version,
                        values={"runs": 6},
                        payload_checksum=_sha(f"team-correction:{year}"),
                        source_mode="LIVE",
                        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                        identity_checksum=_sha(f"team-correction-identity:{year}"),
                    ),
                    PlayerGameHistoryObservation(
                        game_id=original_player.game_id,
                        final_outcome_id=original_player.final_outcome_id,
                        season_id=original_player.season_id,
                        player_id=original_player.player_id,
                        team_id=original_player.team_id,
                        opponent_team_id=original_player.opponent_team_id,
                        role=original_player.role,
                        started=original_player.started,
                        source_version_id=original_player.source_version_id,
                        checkpoint_id=original_player.checkpoint_id,
                        event_at=original_player.event_at,
                        evidence_end_at=original_player.evidence_end_at,
                        retrieved_at=original_player.retrieved_at,
                        eligible_at=original_player.eligible_at,
                        availability_basis=original_player.availability_basis,
                        evidence_classification=original_player.evidence_classification,
                        quality_tier=original_player.quality_tier,
                        source_record_id=original_player.source_record_id,
                        revision=2,
                        supersedes_observation_id=original_player.id,
                        stat_schema_version=original_player.stat_schema_version,
                        values=dict(original_player.values),
                        payload_checksum=_sha(f"player-correction:{year}"),
                        source_mode="LIVE",
                        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                        identity_checksum=_sha(f"player-correction-identity:{year}"),
                    ),
                ])
            cutoff = starts_at - timedelta(hours=6)
            snapshot = GameFeatureSnapshot(
                game_id=game.id,
                generated_at=datetime(2026, 8, 14, tzinfo=UTC),
                data_as_of=cutoff,
                feature_schema_version="report-v1",
                feature_version_id=feature_version.id,
                weather_snapshot_id=weather.id,
                park_factor_version_id=park.id,
                bullpen_observation_ids=[str(item.id) for item in bullpen],
                roster_observation_ids=[str(item.id) for item in rosters],
                statcast_observation_ids=[str(item.id) for item in statcast],
                market_snapshot_ids=[],
                source_evidence=[],
                feature_values={
                    "home_rating": 1500 + 20 * season_index + game_index,
                    "home_days_since_previous_game": 1,
                    "away_days_since_previous_game": 1,
                    "home_travel_distance_miles": 0,
                    "away_travel_distance_miles": 100,
                    "platoon_edge": 0.02,
                    "defense_value": 1.0,
                    "baserunning_value": 1.0,
                    "opponent_adjustment_value": 1.0,
                },
                feature_completeness=0.9,
                missing_features=[],
                stale_features=[],
                provisional_features=[],
                checksum=_sha(f"snapshot:{year}:{game_index}"),
                source_mode="LIVE",
                frozen_at=datetime(2026, 8, 14, tzinfo=UTC),
            )
            session.add(snapshot)
            session.flush()
            eligible = game_index != 11
            materialization = HistoricalFeatureMaterialization(
                game_id=game.id,
                game_feature_snapshot_id=snapshot.id,
                feature_version_id=feature_version.id,
                game_final_outcome_id=outcome.id,
                checkpoint_id=checkpoint.id,
                scheduled_at=starts_at,
                cutoff_at=cutoff,
                generated_at=datetime(2026, 8, 14, tzinfo=UTC),
                cutoff_policy_version="report-v1",
                cutoff_policy={"offset_hours": 6},
                source_bundle=[str(source.id)],
                source_bundle_checksum=_sha(f"bundle:{year}:{game_index}"),
                evidence_manifest=[],
                evidence_manifest_checksum=_sha(f"manifest:{year}:{game_index}"),
                quality_tier="A" if eligible else "C",
                quality_score=90 if eligible else 50,
                quality_components={"fixture": True},
                materializer_version="report-v1",
                code_revision="test",
                eligibility_status="ELIGIBLE" if eligible else "INELIGIBLE",
                eligibility_reasons=[] if eligible else ["missing_actual_lineup"],
                training_eligible=eligible,
                source_mode="LIVE",
                temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
                identity_checksum=_sha(f"materialization:{year}:{game_index}"),
            )
            session.add(materialization)
        session.flush()
    session.commit()

    generated_at = datetime(2026, 8, 14, tzinfo=UTC)
    first = build_mlb_history_report(session, paths=paths, generated_at=generated_at)
    second = build_mlb_history_report(session, paths=paths, generated_at=generated_at)

    assert first == second
    assert first["season_count"] == 2
    assert first["report_execution_provider_requests"] == 0
    for season_report in first["seasons"]:
        coverage = {row["family"]: row for row in season_report["coverage"]}
        assert season_report["games"]["normalized_live"] == 12
        assert season_report["games"]["official_live_finals"] == 12
        assert coverage["finals"]["coverage"] == 1.0
        assert coverage["starters"]["coverage"] == 1.0
        assert coverage["team_history"]["coverage"] == 1.0
        assert coverage["player_history"]["coverage"] == 1.0
        assert coverage["team_history"]["evidence_records"] == 24
        assert coverage["player_history"]["evidence_records"] == 24
        assert coverage["starters"]["evidence_records"] == 24
        assert season_report["history_revision_accounting"] == {
            "basis": (
                "Latest LIVE revision per canonical semantic key; superseded revisions "
                "are retained physically but excluded from coverage and evidence counts."
            ),
            "team_history": {
                "latest_records": 24,
                "superseded_records": 1,
                "physical_records": 25,
            },
            "player_history": {
                "latest_records": 24,
                "superseded_records": 1,
                "physical_records": 25,
            },
        }
        assert coverage["bullpen"]["coverage"] == 1.0
        assert coverage["rest_travel"]["coverage"] == 1.0
        assert coverage["tier_a_b_eligible"]["covered_games"] == 11
        assert coverage["actual_lineups"]["status"] == "MISSING"
        assert coverage["actual_lineups"]["coverage"] == 0.0
        assert season_report["exclusions"]["materializations"]["training_ineligible"] == 1
        assert season_report["exclusions"]["materializations"]["eligibility_reasons"] == {
            "missing_actual_lineup": 1,
        }
        sample = season_report["validation_sample"]
        assert sample["status"] == "COMPLETE"
        assert sample["selected"] == 10
        assert all(row["completion"]["value"] is True for row in sample["games"])
        assert sample["games"][0]["flags"]["doubleheader"]["value"] is True
        assert any(row["flags"]["trade"]["value"] is True for row in sample["games"])
        assert all(row["flags"]["rookie"]["status"] == "AVAILABLE" for row in sample["games"])

    assert first["feature_drift"]["status"] == "AVAILABLE"
    assert first["seasons"][0]["feature_drift"]["status"] == "UNAVAILABLE"
    assert first["seasons"][1]["feature_drift"]["status"] == "AVAILABLE"
    assert len(first["source_operations"]) == 2
    for operations in first["source_operations"]:
        assert operations["checkpoints"] | {"succeeded": 1, "failed": 1} == operations["checkpoints"]
        assert "does not by itself claim canonical" in operations["checkpoints"]["status_semantics"]
        assert operations["physical_requests"]["attempts"]["value"] == 5
        assert "writer" in operations["physical_requests"]["attempts"]["basis"].lower()
        assert operations["physical_requests"]["retries"]["value"] == 3
        assert operations["physical_requests"]["successes"]["status"] == "UNAVAILABLE"
        assert operations["records"] == {
            "received": 120,
            "accepted": 118,
            "rejected": 1,
            "duplicates": 1,
        }
        assert operations["raw_archive"]["bytes"] == len(b"raw-pilot")
        assert "archive-file bytes" in operations["raw_checksum_semantics"]["raw_checksums"]
    assert first["storage"]["raw"]["bytes"] == len(b"raw-pilot")
    assert first["storage"]["parquet"]["bytes"] == len(b"parquetfeature-parquet")
    assert first["storage"]["runtime"]["bytes"] == len(b"runtime")
    json.dumps(first, allow_nan=False)
