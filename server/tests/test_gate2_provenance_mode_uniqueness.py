from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.ingestion import CanonicalNormalizer
from app.models import (
    BullpenAvailabilityObservation,
    ExternalIdMapping,
    GameRefreshState,
    GameStateObservation,
    LineupObservation,
    ParkFactorVersion,
    Player,
    PlayerTalentEstimate,
    RosterAvailabilityObservation,
    StarterObservation,
    StatcastPlayerObservation,
    Team,
    TeamRatingObservation,
)


NOW = datetime(2026, 8, 13, 1, tzinfo=UTC)


def _modes(model, **values):
    return [
        model(**values, source_mode=mode)
        for mode in ("LIVE", "DEMO")
    ]


def test_all_gate2_point_in_time_unique_keys_isolate_live_and_demo(session) -> None:
    game_id = uuid4()
    team_id = uuid4()
    away_team_id = uuid4()
    player_id = uuid4()
    venue_id = uuid4()
    evidence = {
        "observed_at": NOW,
        "available_as_of": NOW,
        "source": "IDENTICAL_OFFLINE_EVIDENCE",
        "payload_checksum": "a" * 64,
    }
    rows = [
        *_modes(
            GameStateObservation,
            game_id=game_id,
            home_team_id=team_id,
            away_team_id=away_team_id,
            scheduled_at=NOW + timedelta(hours=4),
            status="SCHEDULED",
            is_postponed=False,
            home_starter_confirmation="UNKNOWN",
            away_starter_confirmation="UNKNOWN",
            **evidence,
        ),
        *_modes(
            LineupObservation,
            game_id=game_id,
            team_id=team_id,
            lineup_status="PROJECTED",
            is_complete=False,
            **evidence,
        ),
        *_modes(
            StarterObservation,
            game_id=game_id,
            team_id=team_id,
            confirmation_state="UNKNOWN",
            **evidence,
        ),
        *_modes(
            RosterAvailabilityObservation,
            team_id=team_id,
            player_id=player_id,
            availability_status="ACTIVE",
            effective_at=NOW,
            **evidence,
        ),
        *_modes(
            PlayerTalentEstimate,
            player_id=player_id,
            role="HITTER",
            metric_name="xwoba",
            posterior_value=0.333,
            evidence_cutoff=NOW,
            method="offline_test",
            version="v1",
            **evidence,
        ),
        *_modes(
            BullpenAvailabilityObservation,
            game_id=game_id,
            team_id=team_id,
            player_id=player_id,
            **evidence,
        ),
        *_modes(
            StatcastPlayerObservation,
            player_id=player_id,
            team_id=team_id,
            season=2026,
            role="HITTER",
            evidence_cutoff=NOW,
            feature_schema_version="statcast_player_v1",
            values={"xwoba": 0.333},
            **evidence,
        ),
        *_modes(
            ParkFactorVersion,
            venue_id=venue_id,
            season=2026,
            version="v1",
            **evidence,
        ),
        *_modes(
            TeamRatingObservation,
            team_id=team_id,
            rating=1510,
            method="offline_test",
            version="v1",
            evidence_cutoff=NOW,
            **evidence,
        ),
        *_modes(
            GameRefreshState,
            game_id=game_id,
            lifecycle_stage="T_MINUS_6H",
            evaluated_at=NOW,
            available_as_of=NOW,
            first_pitch_at=NOW + timedelta(hours=4),
            time_to_first_pitch_seconds=14_400,
            final_forecast_status="NOT_STARTED",
            final_snapshot_frozen=False,
        ),
    ]
    session.add_all(rows)
    session.commit()

    for model in (
        GameStateObservation,
        LineupObservation,
        StarterObservation,
        RosterAvailabilityObservation,
        PlayerTalentEstimate,
        BullpenAvailabilityObservation,
        StatcastPlayerObservation,
        ParkFactorVersion,
        TeamRatingObservation,
        GameRefreshState,
    ):
        assert session.scalar(select(func.count(model.id))) == 2
        assert set(session.scalars(select(model.source_mode))) == {"LIVE", "DEMO"}


def test_statcast_promotion_is_idempotent_within_mode_and_coexists_across_modes(session) -> None:
    normalizer = CanonicalNormalizer(session)
    _, league, _, _ = normalizer.ensure_core(2026)
    team = Team(league_id=league.id, name="Gate 2 Mode Team", abbreviation="G2MT")
    session.add(team)
    session.flush()
    player = Player(team_id=team.id, full_name="Gate 2 Mode Player", position="OF")
    session.add(player)
    session.flush()
    session.add(ExternalIdMapping(
        entity_type="player",
        entity_id=player.id,
        source="MLB_STATS_API",
        external_id="246810",
    ))
    session.commit()

    row = {
        "source_player_id": "246810",
        "player_name": "Gate 2 Mode Player",
        "player_type": "HITTER",
        "attempts": 100,
        "xwoba": 0.400,
    }
    arguments = {
        "season": 2026,
        "available_as_of": NOW,
        "raw_reference": "offline-mode-test.json",
    }
    assert normalizer.ingest_statcast(
        [row], "BASEBALL_SAVANT_STATCAST", source_mode="LIVE", **arguments,
    ) == 1
    assert normalizer.ingest_statcast(
        [row], "BASEBALL_SAVANT_STATCAST", source_mode="LIVE", **arguments,
    ) == 0
    assert normalizer.ingest_statcast(
        [row], "BASEBALL_SAVANT_STATCAST", source_mode="DEMO", **arguments,
    ) == 1
    session.commit()

    observations = list(session.scalars(select(StatcastPlayerObservation).where(
        StatcastPlayerObservation.player_id == player.id,
    )))
    estimates = list(session.scalars(select(PlayerTalentEstimate).where(
        PlayerTalentEstimate.player_id == player.id,
        PlayerTalentEstimate.metric_name == "xwoba",
    )))
    assert len(observations) == 2
    assert len(estimates) == 2
    assert {row.source_mode for row in observations} == {"LIVE", "DEMO"}
    assert {row.source_mode for row in estimates} == {"LIVE", "DEMO"}
    assert observations[0].payload_checksum == observations[1].payload_checksum
    assert estimates[0].posterior_value == estimates[1].posterior_value
