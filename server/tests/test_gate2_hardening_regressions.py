"""Focused regressions for Gate 2 evidence hardening.

These tests exercise service integration rather than only the pure feature
helpers: lineup precedence, evidence coverage, source-mode isolation, and the
monotonic relationship between usable evidence, readiness, and confidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.data_sources.base import ScheduleGameDTO, TeamDTO, VenueDTO
from app.ingestion import CanonicalNormalizer
from app.models import (
    BullpenAvailabilityObservation,
    ExternalIdMapping,
    GameFeatureSnapshot,
    GameTeam,
    LineupEntry,
    LineupObservation,
    Player,
    PlayerTalentEstimate,
    StatcastPlayerObservation,
    Team,
)
from app.services.forecast_service import ForecastService
from app.services.game_state_service import GameStateService


FIRST_PITCH = datetime(2026, 8, 13, 1, tzinfo=UTC)
CUTOFF = FIRST_PITCH - timedelta(hours=6)
UPDATED_CUTOFF = CUTOFF + timedelta(minutes=20)


def _context(session) -> SimpleNamespace:
    game = CanonicalNormalizer(session).ingest_schedule(
        [ScheduleGameDTO(
            source_game_id="gate2-hardening-game",
            scheduled_at=FIRST_PITCH,
            event_date=FIRST_PITCH.date(),
            status="scheduled",
            away_team=TeamDTO(
                source_id="gate2-hardening-away",
                name="Gate 2 Hardening Away",
                abbreviation="G2HA",
            ),
            home_team=TeamDTO(
                source_id="gate2-hardening-home",
                name="Gate 2 Hardening Home",
                abbreviation="G2HH",
            ),
            venue=VenueDTO(source_id="gate2-hardening-park", name="Gate 2 Hardening Park"),
            source_mode="LIVE",
        )],
        "MLB_STATS_API",
        available_as_of=CUTOFF,
    )[0]
    sides = list(session.scalars(select(GameTeam).where(GameTeam.game_id == game.id)))
    context = SimpleNamespace(
        game=game,
        home_team_id=next(side.team_id for side in sides if side.is_home),
        away_team_id=next(side.team_id for side in sides if not side.is_home),
    )
    session.commit()
    return context


def _add_lineup(
    session,
    context: SimpleNamespace,
    *,
    team_id,
    status: str,
    available_as_of: datetime,
    prefix: str,
    player_count: int = 9,
) -> tuple[LineupObservation, list[Player]]:
    players = [
        Player(team_id=team_id, full_name=f"{prefix} {order}", position="OF")
        for order in range(1, player_count + 1)
    ]
    session.add_all(players)
    session.flush()
    lineup = LineupObservation(
        game_id=context.game.id,
        team_id=team_id,
        lineup_status=status,
        is_complete=player_count == 9,
        observed_at=available_as_of,
        available_as_of=available_as_of,
        source="GATE2_HARDENING_TEST",
        source_mode="LIVE",
        payload_checksum=f"{prefix}:{status}:{available_as_of.isoformat()}".encode().hex()[:64].ljust(64, "0"),
    )
    session.add(lineup)
    session.flush()
    session.add_all([
        LineupEntry(
            lineup_observation_id=lineup.id,
            player_id=player.id,
            batting_order=order,
            defensive_position=player.position,
            is_starter=True,
            expected_plate_appearances=4.5,
            expected_plate_appearance_weight=1.0,
        )
        for order, player in enumerate(players, start=1)
    ])
    return lineup, players


def test_confirmed_lineup_outranks_newer_projected_at_service_boundary(session) -> None:
    context = _context(session)
    confirmed, confirmed_players = _add_lineup(
        session,
        context,
        team_id=context.home_team_id,
        status="CONFIRMED",
        available_as_of=CUTOFF - timedelta(minutes=20),
        prefix="Confirmed",
        player_count=1,
    )
    projected, _ = _add_lineup(
        session,
        context,
        team_id=context.home_team_id,
        status="PROJECTED",
        available_as_of=CUTOFF - timedelta(minutes=1),
        prefix="Projected",
        player_count=1,
    )
    session.commit()

    state = GameStateService(session).get_game_state(context.game.id, CUTOFF)

    assert projected.available_as_of > confirmed.available_as_of
    assert state.home.lineup.id == confirmed.id
    assert [row.player_id for row in state.home.lineup_entries] == [confirmed_players[0].id]


def test_sparse_unusable_optional_evidence_cannot_improve_readiness_or_confidence(
    session,
    settings,
) -> None:
    context = _context(session)
    players_by_team = {}
    for team_id, prefix in (
        (context.home_team_id, "Sparse Home"),
        (context.away_team_id, "Sparse Away"),
    ):
        _, players_by_team[team_id] = _add_lineup(
            session,
            context,
            team_id=team_id,
            status="CONFIRMED",
            available_as_of=CUTOFF - timedelta(minutes=1),
            prefix=prefix,
        )
    session.commit()

    service = ForecastService(session, settings)
    missing = service.generate_for_game(context.game, data_as_of=CUTOFF, force_draft=True)
    session.commit()
    missing_snapshot = session.get(GameFeatureSnapshot, missing.feature_snapshot_id)
    assert missing_snapshot is not None

    for team_id, marker in ((context.home_team_id, "c"), (context.away_team_id, "d")):
        player = players_by_team[team_id][0]
        session.add_all([
            StatcastPlayerObservation(
                player_id=player.id,
                team_id=team_id,
                season=2026,
                role="HITTER",
                evidence_cutoff=UPDATED_CUTOFF,
                sample_size=100,
                feature_schema_version="statcast_player_v1",
                values={
                    "xwoba": 0.390,
                    "barrel_rate": 0.12,
                    "hard_hit_rate": 0.46,
                    "average_exit_velocity": 92.0,
                },
                observed_at=UPDATED_CUTOFF,
                available_as_of=UPDATED_CUTOFF,
                source="GATE2_HARDENING_TEST",
                source_mode="LIVE",
                payload_checksum=marker * 64,
            ),
            BullpenAvailabilityObservation(
                game_id=context.game.id,
                team_id=team_id,
                player_id=player.id,
                role="RELIEF",
                pitches_previous_day=0,
                workload={
                    "pitches_last_2d": 0,
                    "pitches_last_3d": 0,
                    "expected_usage_weight": 1.0,
                },
                expected_available=None,
                participation_probability=None,
                observed_at=UPDATED_CUTOFF,
                available_as_of=UPDATED_CUTOFF,
                source="GATE2_HARDENING_TEST",
                source_mode="LIVE",
                payload_checksum=("e" if team_id == context.home_team_id else "f") * 64,
            ),
        ])
    session.commit()

    sparse = service.generate_for_game(context.game, data_as_of=UPDATED_CUTOFF, force_draft=True)
    session.commit()
    sparse_snapshot = session.get(GameFeatureSnapshot, sparse.feature_snapshot_id)
    assert sparse_snapshot is not None

    assert sparse_snapshot.bayesian_metadata["home_statcast"]["player_coverage"] == pytest.approx(1 / 9)
    assert sparse_snapshot.bayesian_metadata["away_statcast"]["player_coverage"] == pytest.approx(1 / 9)
    assert sparse_snapshot.bayesian_metadata["home_bullpen"]["coverage"] == 0
    assert sparse_snapshot.bayesian_metadata["away_bullpen"]["coverage"] == 0
    assert missing.readiness_components["statcast"]["status"] == "missing"
    assert missing.readiness_components["bullpen"]["status"] == "missing"
    assert sparse.readiness_components["statcast"]["status"] == "missing"
    assert sparse.readiness_components["bullpen"]["status"] == "missing"
    assert sparse_snapshot.feature_completeness == missing_snapshot.feature_completeness
    assert sparse.readiness_score <= missing.readiness_score
    assert sparse.confidence <= missing.confidence


def test_live_demo_duplicate_isolation_and_invalid_source_modes_fail_closed(session) -> None:
    _, league, _, _ = CanonicalNormalizer(session).ensure_core(2026)
    team = Team(league_id=league.id, name="Gate 2 Dual Mode Team", abbreviation="G2DM")
    session.add(team)
    session.flush()
    player = Player(team_id=team.id, full_name="Gate 2 Dual Mode Player", position="OF")
    session.add(player)
    session.flush()
    session.add(ExternalIdMapping(
        entity_type="player",
        entity_id=player.id,
        source="MLB_STATS_API",
        external_id="dual-mode-987654",
    ))
    session.commit()

    available = datetime(2026, 8, 12, 18, 45, tzinfo=UTC)
    evidence = [{
        "source_player_id": "dual-mode-987654",
        "player_name": "Dual Mode Provider Name",
        "source_team": "provider-team",
        "player_type": "hitter",
        "attempts": 120,
        "xwoba": 0.360,
        "barrel_rate": 0.100,
        "hard_hit_rate": 0.420,
        "average_exit_velocity": 90.0,
    }]
    normalizer = CanonicalNormalizer(session)
    assert normalizer.ingest_statcast(
        evidence,
        "BASEBALL_SAVANT_STATCAST",
        season=2026,
        available_as_of=available,
        source_mode="LIVE",
    ) == 1
    assert normalizer.ingest_statcast(
        evidence,
        "BASEBALL_SAVANT_STATCAST",
        season=2026,
        available_as_of=available,
        source_mode="DEMO",
    ) == 1
    with pytest.raises(ValueError, match="Unsupported Statcast source mode"):
        normalizer.ingest_statcast(
            evidence,
            "BASEBALL_SAVANT_STATCAST",
            season=2026,
            available_as_of=available,
            source_mode="MIXED",
        )
    session.commit()

    observations = list(session.scalars(select(StatcastPlayerObservation).where(
        StatcastPlayerObservation.player_id == player.id,
    )))
    xwoba_estimates = list(session.scalars(select(PlayerTalentEstimate).where(
        PlayerTalentEstimate.player_id == player.id,
        PlayerTalentEstimate.metric_name == "xwoba",
    )))
    assert len(observations) == 2
    assert {row.source_mode for row in observations} == {"LIVE", "DEMO"}
    assert len(xwoba_estimates) == 2
    assert {row.source_mode for row in xwoba_estimates} == {"LIVE", "DEMO"}

    context = _context(session)
    context.game.source_mode = "MIXED"
    session.commit()
    with pytest.raises(ValueError, match="Unsupported game source mode"):
        GameStateService(session).get_game_state(context.game.id, CUTOFF)
