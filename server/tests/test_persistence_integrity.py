from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.audit import ImmutableRecordError
from app.data_sources.base import BookmakerDTO, BookMarketDTO, OddsEventDTO, OddsOutcomeDTO
from app.ingestion.normalizer import CanonicalNormalizer
from app.jobs import seed_demo_data
from app.models import Forecast, Game, OddsSnapshot, TeamFeature
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
    added = CanonicalNormalizer(session).ingest_odds([event], "TEST_SOURCE", fetched)
    session.commit()
    assert added == 2
    assert session.scalar(select(func.count()).select_from(OddsSnapshot)) == before + 2
    appended = list(session.scalars(select(OddsSnapshot).where(OddsSnapshot.source == "TEST_SOURCE")))
    assert all(item.available_as_of.replace(tzinfo=UTC) == fetched for item in appended)
    assert all(item.observed_at.replace(tzinfo=UTC) == observed for item in appended)


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
    games = ReadService(session, settings)._games_for_date(date(2026, 8, 12))
    assert games == [live]
