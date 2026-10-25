from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.audit import ImmutableRecordError
from app.data_sources.base import (
    BookmakerDTO,
    BookMarketDTO,
    OddsEventDTO,
    OddsOutcomeDTO,
    ScheduleGameDTO,
    TeamDTO,
    VenueDTO,
)
from app.ingestion.normalizer import CanonicalNormalizer
from app.jobs import seed_demo_data
from app.models import ExternalIdMapping, Forecast, Game, OddsSnapshot, Team, TeamFeature
from app.services.read_service import ReadService


@pytest.mark.asyncio
async def test_published_forecast_and_odds_are_immutable(session, settings) -> None:
    await seed_demo_data(session, settings, date(2026, 8, 12))
    forecast = session.scalar(select(Forecast).order_by(Forecast.version.desc()))
    forecast.confidence = 1
    with pytest.raises(ImmutableRecordError):
        session.commit()
    session.rollback()
    odds = session.scalar(select(OddsSnapshot))
    odds.american_odds = 999
    with pytest.raises(ImmutableRecordError):
        session.commit()
    session.rollback()


@pytest.mark.asyncio
async def test_odds_snapshots_append_and_use_fetch_availability(session, settings) -> None:
    await seed_demo_data(session, settings, date(2026, 8, 12))
    before = session.scalar(select(func.count()).select_from(OddsSnapshot))
    game = session.scalar(select(Game).order_by(Game.starts_at))
    card = ReadService(session, settings).game_card(game)
    fetched = datetime(2026, 8, 12, 20, tzinfo=UTC)
    observed = fetched - timedelta(minutes=30)
    event = OddsEventDTO(
        source_event_id="append-test", commence_time=game.starts_at,
        home_team=card.home_team.name, away_team=card.away_team.name,
        bookmakers=[BookmakerDTO(
            key="append_book", title="Append Book", last_update=observed,
            markets=[BookMarketDTO(key="h2h", last_update=observed, outcomes=[
                OddsOutcomeDTO(name=card.home_team.name, price=-115),
                OddsOutcomeDTO(name=card.away_team.name, price=105),
            ])],
        )],
    )
    added = CanonicalNormalizer(session).ingest_odds(
        [event], "SPORTSGAMEODDS", fetched, league_code="MLB", provider_league="MLB")
    session.commit()
    assert added == 2
    assert session.scalar(select(func.count()).select_from(OddsSnapshot)) == before + 2
    appended = list(session.scalars(select(OddsSnapshot).where(
        OddsSnapshot.source == "SPORTSGAMEODDS", OddsSnapshot.source_event_id == "append-test")))
    assert len(appended) == 2
    assert all(item.available_as_of.replace(tzinfo=UTC) == fetched for item in appended)
    assert all(item.observed_at.replace(tzinfo=UTC) == observed for item in appended)


def test_live_schedule_reconciles_only_exact_historical_team_placeholder(session) -> None:
    normalizer = CanonicalNormalizer(session)
    _, league, _, _ = normalizer.ensure_core(2026)
    placeholder = Team(
        league_id=league.id,
        name="Kansas City Royals",
        abbreviation="MLB118",
    )
    existing_non_placeholder = Team(
        league_id=league.id,
        name="Cleveland Guardians",
        abbreviation="CLV",
    )
    session.add_all([placeholder, existing_non_placeholder])
    session.flush()
    session.add_all([
        ExternalIdMapping(
            entity_type="team",
            entity_id=placeholder.id,
            source="MLB_STATS_API",
            external_id="118",
        ),
        ExternalIdMapping(
            entity_type="team",
            entity_id=existing_non_placeholder.id,
            source="MLB_STATS_API",
            external_id="114",
        ),
    ])
    session.flush()
    scheduled_at = datetime(2026, 8, 28, 23, 10, tzinfo=UTC)
    schedule = [ScheduleGameDTO(
        source_game_id="824396",
        scheduled_at=scheduled_at,
        event_date=scheduled_at.date(),
        status="Scheduled",
        away_team=TeamDTO(
            source_id="118",
            name="Kansas City Royals",
            abbreviation="KC",
        ),
        home_team=TeamDTO(
            source_id="114",
            name="Cleveland Guardians",
            abbreviation="CLE",
        ),
        venue=VenueDTO(source_id="5", name="Progressive Field"),
        source_mode="LIVE",
    )]

    first_game = normalizer.ingest_schedule(
        schedule,
        "MLB_STATS_API",
        available_as_of=scheduled_at - timedelta(hours=6),
    )[0]
    second_game = normalizer.ingest_schedule(
        schedule,
        "MLB_STATS_API",
        available_as_of=scheduled_at - timedelta(hours=5),
    )[0]

    assert first_game.id == second_game.id
    assert placeholder.abbreviation == "KC"
    assert existing_non_placeholder.abbreviation == "CLV"
    mappings = list(session.scalars(select(ExternalIdMapping).where(
        ExternalIdMapping.entity_type == "team",
        ExternalIdMapping.source == "MLB_STATS_API",
        ExternalIdMapping.external_id == "118",
    )))
    assert len(mappings) == 1
    assert mappings[0].entity_id == placeholder.id


@pytest.mark.asyncio
async def test_live_games_suppress_demo_games_and_features(session, settings) -> None:
    await seed_demo_data(session, settings, date(2026, 8, 12))
    demo_game = session.scalar(select(Game).order_by(Game.starts_at))
    live = Game(
        league_id=demo_game.league_id, season_id=demo_game.season_id, venue_id=demo_game.venue_id,
        starts_at=demo_game.starts_at + timedelta(minutes=10), status=demo_game.status, source_mode="LIVE",
    )
    session.add(live)
    session.commit()
    games = ReadService(session, settings)._games_for_date(date(2026, 8, 12), "MLB")
    assert games == [live]
