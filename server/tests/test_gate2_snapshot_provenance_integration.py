from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import app.jobs.orchestrator as orchestrator_module
from app.audit import ImmutableRecordError
from app.data_sources import SourceError
from app.ingestion import CanonicalNormalizer
from app.jobs.orchestrator import JobOrchestrator
from app.models import (
    ExternalIdMapping,
    ForecastMovementEvidence,
    Game,
    GameFeatureSnapshot,
    GameStateObservation,
    GameStatus,
    GameTeam,
    Player,
    PlayerTalentEstimate,
    ProviderRequestLedger,
    StatcastPlayerObservation,
    Team,
    Venue,
    WeatherSnapshot,
)
from app.services.forecast_service import ForecastService
from app.services.forecast_service import ForecastWindowClosedError
from app.services.read_service import ReadService


FIRST_PITCH = datetime(2026, 8, 13, 1, tzinfo=UTC)
CUTOFF = FIRST_PITCH - timedelta(hours=6)
UPDATED_CUTOFF = CUTOFF + timedelta(minutes=20)


@dataclass(frozen=True)
class ForecastContext:
    game: Game
    venue: Venue


def _forecast_context(session) -> ForecastContext:
    _, league, season, _ = CanonicalNormalizer(session).ensure_core(2026)
    venue = Venue(name="Gate 2 Provenance Park", timezone="UTC")
    home = Team(league_id=league.id, name="Gate 2 Snapshot Home", abbreviation="G2SH")
    away = Team(league_id=league.id, name="Gate 2 Snapshot Away", abbreviation="G2SA")
    session.add_all([venue, home, away])
    session.flush()
    game = Game(
        league_id=league.id,
        season_id=season.id,
        venue_id=venue.id,
        starts_at=FIRST_PITCH,
        status=GameStatus.SCHEDULED,
        source_mode="LIVE",
    )
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
        GameStateObservation(
            game_id=game.id,
            scheduled_at=FIRST_PITCH,
            status="SCHEDULED",
            is_postponed=False,
            venue_id=venue.id,
            home_starter_confirmation="UNKNOWN",
            away_starter_confirmation="UNKNOWN",
            observed_at=CUTOFF,
            available_as_of=CUTOFF,
            source="GATE2_PROVENANCE_TEST",
            source_mode="LIVE",
            payload_checksum="1" * 64,
        ),
    ])
    session.commit()
    return ForecastContext(game=game, venue=venue)


def test_forecast_snapshots_are_reused_checksum_stable_and_explain_movements(
    session,
    settings,
) -> None:
    context = _forecast_context(session)
    service = ForecastService(session, settings)

    first = service.generate_for_game(context.game, data_as_of=CUTOFF, force_draft=True)
    session.commit()
    first_snapshot = session.get(GameFeatureSnapshot, first.feature_snapshot_id)
    assert first_snapshot is not None
    stable_snapshot_id = first_snapshot.id
    stable_checksum = first_snapshot.checksum
    assert len(stable_checksum) == 64
    int(stable_checksum, 16)
    assert first.feature_snapshot_reference == f"sha256:{stable_checksum}"

    repeated = service.generate_for_game(context.game, data_as_of=CUTOFF, force_draft=True)
    session.commit()
    assert repeated.version == first.version + 1
    assert repeated.id != first.id
    assert repeated.feature_snapshot_id == stable_snapshot_id
    assert repeated.feature_snapshot_reference == f"sha256:{stable_checksum}"
    assert session.scalar(
        select(func.count(GameFeatureSnapshot.id)).where(
            GameFeatureSnapshot.game_id == context.game.id
        )
    ) == 1

    weather = WeatherSnapshot(
        game_id=context.game.id,
        source="GATE2_WEATHER_TEST",
        observed_at=UPDATED_CUTOFF,
        available_as_of=UPDATED_CUTOFF,
        temperature_f=84,
        precipitation_probability=0.15,
        source_mode="LIVE",
    )
    session.add(weather)
    session.commit()

    changed = service.generate_for_game(
        context.game,
        data_as_of=UPDATED_CUTOFF,
        force_draft=True,
    )
    session.commit()
    changed_snapshot = session.get(GameFeatureSnapshot, changed.feature_snapshot_id)
    assert changed_snapshot is not None
    assert changed.version == repeated.version + 1
    assert changed_snapshot.id != stable_snapshot_id
    assert changed_snapshot.checksum != stable_checksum
    assert changed_snapshot.weather_snapshot_id == weather.id
    assert changed.feature_snapshot_reference == f"sha256:{changed_snapshot.checksum}"

    movement = session.scalar(
        select(ForecastMovementEvidence).where(
            ForecastMovementEvidence.current_forecast_id == changed.id,
            ForecastMovementEvidence.feature_family == "weather",
        )
    )
    assert movement is not None
    assert movement.previous_forecast_id == repeated.id
    assert movement.previous_feature_snapshot_id == stable_snapshot_id
    assert movement.current_feature_snapshot_id == changed_snapshot.id
    assert movement.current_value == {
        "observation_id": str(weather.id),
        "temperature_f": 84,
        "precipitation_probability": 0.15,
    }
    assert any(
        evidence["family"] == "weather" and evidence["id"] == str(weather.id)
        for evidence in movement.associated_input_updates
    )

    changed_snapshot.feature_completeness = min(
        1.0,
        changed_snapshot.feature_completeness + 0.01,
    )
    with pytest.raises(ImmutableRecordError, match="GameFeatureSnapshot records are append-only"):
        session.flush()
    session.rollback()
    persisted = session.get(GameFeatureSnapshot, changed_snapshot.id)
    assert persisted is not None
    assert persisted.checksum == changed.feature_snapshot_reference.removeprefix("sha256:")


def test_historical_live_replay_uses_actual_timestamps_stays_draft_and_is_not_publicly_scored(
    session,
    settings,
) -> None:
    context = _forecast_context(session)
    service = ForecastService(session, settings)

    with pytest.raises(ForecastWindowClosedError, match="Historical live replays"):
        service.generate_for_game(context.game, data_as_of=CUTOFF)

    forecast = service.generate_for_game(
        context.game,
        data_as_of=CUTOFF,
        force_draft=True,
    )
    session.commit()
    snapshot = session.get(GameFeatureSnapshot, forecast.feature_snapshot_id)

    assert forecast.status.value == "draft"
    assert forecast.published_at is None
    assert forecast.generated_at.replace(tzinfo=UTC) >= FIRST_PITCH
    assert forecast.available_as_of.replace(tzinfo=UTC) >= FIRST_PITCH
    assert snapshot is not None
    assert snapshot.data_as_of.replace(tzinfo=UTC) == CUTOFF
    assert snapshot.generated_at.replace(tzinfo=UTC) >= FIRST_PITCH
    assert forecast.readiness_components["_gate2"]["historical_replay"] is True

    context.game.status = GameStatus.FINAL
    context.game.home_score = 5
    context.game.away_score = 3
    session.commit()

    performance = ReadService(session, settings).performance()
    assert performance.evaluation_available is False
    assert performance.evaluated_games == 0


def test_statcast_promotion_maps_canonical_player_and_persists_talent(session) -> None:
    _, league, _, _ = CanonicalNormalizer(session).ensure_core(2026)
    team = Team(league_id=league.id, name="Gate 2 Statcast Team", abbreviation="G2ST")
    session.add(team)
    session.flush()
    player = Player(team_id=team.id, full_name="Canonical Statcast Player", position="OF")
    session.add(player)
    session.flush()
    session.add(ExternalIdMapping(
        entity_type="player",
        entity_id=player.id,
        source="MLB_STATS_API",
        external_id="987654",
    ))
    session.commit()

    available = datetime(2026, 8, 12, 18, 30, tzinfo=UTC)
    promoted = CanonicalNormalizer(session).ingest_statcast(
        [{
            "source_player_id": "987654",
            "player_name": "Provider Name Is Not Canonical Identity",
            "source_team": "provider-team",
            "player_type": "hitter",
            "attempts": 100,
            "xwoba": 0.400,
            "barrel_rate": 0.120,
            "hard_hit_rate": 0.450,
            "average_exit_velocity": 92.0,
        }],
        "BASEBALL_SAVANT_STATCAST",
        season=2026,
        available_as_of=available,
        raw_reference="local-test-fixture.json",
    )
    session.commit()

    assert promoted == 1
    observation = session.scalar(select(StatcastPlayerObservation))
    assert observation is not None
    assert observation.player_id == player.id
    assert observation.team_id == team.id
    assert observation.role == "HITTER"
    assert observation.sample_size == 100
    assert observation.feature_schema_version == "statcast_player_v1"
    assert observation.values == {
        "xwoba": 0.400,
        "barrel_rate": 0.120,
        "hard_hit_rate": 0.450,
        "average_exit_velocity": 92.0,
    }
    assert observation.raw_reference == "local-test-fixture.json"
    assert session.scalar(select(ExternalIdMapping).where(
        ExternalIdMapping.entity_type == "player",
        ExternalIdMapping.entity_id == player.id,
        ExternalIdMapping.source == "BASEBALL_SAVANT_STATCAST",
        ExternalIdMapping.external_id == "987654",
    )) is not None

    estimates = list(session.scalars(
        select(PlayerTalentEstimate)
        .where(PlayerTalentEstimate.player_id == player.id)
        .order_by(PlayerTalentEstimate.metric_name)
    ))
    assert {row.metric_name for row in estimates} == {
        "average_exit_velocity",
        "barrel_rate",
        "hard_hit_rate",
        "xwoba",
    }
    assert all(row.version == "gate2_empirical_bayes_v1" for row in estimates)
    assert all(
        row.source_mode == "LIVE"
        and row.evidence_cutoff.replace(tzinfo=UTC) == available
        for row in estimates
    )
    xwoba = next(row for row in estimates if row.metric_name == "xwoba")
    assert xwoba.prior_value < xwoba.posterior_value < xwoba.season_value
    assert xwoba.evidence_references == [{
        "source": "BASEBALL_SAVANT_STATCAST",
        "source_player_id": "987654",
        "metric": "xwoba",
        "statcast_payload_checksum": observation.payload_checksum,
    }]


@pytest.mark.asyncio
async def test_odds_daily_reservation_budget_blocks_before_adapter_network(
    session,
    settings,
    monkeypatch,
) -> None:
    live_settings = settings.model_copy(update={
        "the_odds_api_key": "offline-test-placeholder",
        "odds_daily_request_budget": 1,
        "odds_polling_enabled": False,
    })
    orchestrator = JobOrchestrator(session, live_settings)
    reservation = orchestrator._reserve_provider_request(
        "THE_ODDS_API",
        "baseball_mlb_odds",
        controlled_live_request=True,
        request_shape={"event_date": "2026-08-12", "markets": ["h2h"]},
    )
    session.commit()

    adapter_constructions = 0

    class NetworkForbiddenAdapter:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal adapter_constructions
            adapter_constructions += 1
            raise AssertionError("quota policy must run before constructing a network adapter")

    monkeypatch.setattr(orchestrator_module, "TheOddsAPIAdapter", NetworkForbiddenAdapter)
    with pytest.raises(SourceError, match=r"Daily request budget reached \(1/1\)") as exc_info:
        await orchestrator.refresh_odds(date(2026, 8, 12))

    assert exc_info.value.retryable is False
    assert adapter_constructions == 0
    ledgers = list(session.scalars(select(ProviderRequestLedger)))
    assert [row.id for row in ledgers] == [reservation.id]
    assert ledgers[0].status == "RESERVED"


@pytest.mark.asyncio
async def test_controlled_live_validation_refuses_test_environment_before_network(
    session,
    settings,
    monkeypatch,
) -> None:
    live_key_settings = settings.model_copy(update={
        "the_odds_api_key": "offline-test-placeholder",
        "odds_daily_request_budget": 1,
        "scheduler_enabled": False,
        "event_relative_scheduler_enabled": False,
        "odds_polling_enabled": False,
        "statcast_enabled": False,
        "official_positions_enabled": False,
    })

    class NetworkForbiddenAdapter:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("test environment guard must run before any provider adapter")

    monkeypatch.setattr(orchestrator_module, "MLBStatsAPIAdapter", NetworkForbiddenAdapter)
    monkeypatch.setattr(orchestrator_module, "TheOddsAPIAdapter", NetworkForbiddenAdapter)

    with pytest.raises(SourceError, match="disabled in the test environment"):
        await JobOrchestrator(session, live_key_settings).controlled_live_validation(
            date(2026, 8, 12)
        )
    assert session.scalar(select(func.count(ProviderRequestLedger.id))) == 0


def test_provider_request_ledger_is_append_only(session) -> None:
    ledger = ProviderRequestLedger(
        provider="THE_ODDS_API",
        endpoint_key="baseball_mlb_odds",
        requested_at=datetime.now(UTC),
        status="RESERVED",
        request_fingerprint="f" * 64,
        controlled_live_request=True,
        source_mode="LIVE",
        response_metadata={"test": "secret-free"},
    )
    session.add(ledger)
    session.commit()

    ledger.status = "SUCCEEDED"
    with pytest.raises(ImmutableRecordError, match="ProviderRequestLedger records are append-only"):
        session.flush()
    session.rollback()

    persisted = session.get(ProviderRequestLedger, ledger.id)
    assert persisted is not None
    session.delete(persisted)
    with pytest.raises(ImmutableRecordError, match="ProviderRequestLedger records are append-only"):
        session.flush()
    session.rollback()
    assert session.get(ProviderRequestLedger, ledger.id) is not None


def test_provider_request_ledger_requires_one_terminal_row_per_reservation(session) -> None:
    requested = datetime.now(UTC)
    reservation = ProviderRequestLedger(
        provider="THE_ODDS_API",
        endpoint_key="baseball_mlb_odds",
        requested_at=requested,
        status="RESERVED",
        request_fingerprint="a" * 64,
        controlled_live_request=True,
        source_mode="LIVE",
    )
    session.add(reservation)
    session.commit()

    terminal = ProviderRequestLedger(
        provider=reservation.provider,
        endpoint_key=reservation.endpoint_key,
        requested_at=requested,
        completed_at=requested + timedelta(seconds=1),
        status="SUCCEEDED",
        request_fingerprint=reservation.request_fingerprint,
        controlled_live_request=True,
        source_mode="LIVE",
        reservation_id=reservation.id,
    )
    session.add(terminal)
    session.commit()

    duplicate_terminal = ProviderRequestLedger(
        provider=reservation.provider,
        endpoint_key=reservation.endpoint_key,
        requested_at=requested,
        completed_at=requested + timedelta(seconds=2),
        status="FAILED",
        request_fingerprint=reservation.request_fingerprint,
        controlled_live_request=True,
        source_mode="LIVE",
        reservation_id=reservation.id,
    )
    session.add(duplicate_terminal)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    orphan_terminal = ProviderRequestLedger(
        provider="THE_ODDS_API",
        endpoint_key="baseball_mlb_odds",
        requested_at=requested,
        completed_at=requested + timedelta(seconds=3),
        status="FAILED",
        request_fingerprint="b" * 64,
        controlled_live_request=True,
        source_mode="LIVE",
        reservation_id=None,
    )
    session.add(orphan_terminal)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
