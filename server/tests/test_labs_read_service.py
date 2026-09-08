"""Which games an interactive lab may offer, and with which tracks.

The rules under test: a game is listed only when its own persisted simulation
can actually be replayed, a V18 action is per game rather than per league, and
a finished game keeps its preserved simulation while being labelled final.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.models import Forecast, Game, GameStatus, GameTeam, ShadowForecast, Team
from app.services.labs_read_service import MAX_LIMIT, LabsReadService

SLATE = date(2026, 8, 12)


async def _seeded(session, settings):
    await seed_demo_data(session, settings, SLATE)
    return session.scalar(select(Forecast))


def _service(session, settings) -> LabsReadService:
    return LabsReadService(session, settings)


@pytest.mark.asyncio
async def test_lists_only_games_whose_simulation_can_be_replayed(session, settings):
    template = await _seeded(session, settings)
    payload = _service(session, settings).eligible_games("MLB")

    assert payload["schema_version"] == "AISP_INTERACTIVE_LABS_V1"
    assert payload["league"] == "MLB"
    assert payload["eligible_count"] == len(payload["games"])
    assert payload["games"], "the seeded slate should offer at least one game"
    for item in payload["games"]:
        assert item["gen1_available"] is True
        assert item["gen1_href"] == f"/games/{item['game_id']}/simulation?track=gen1"
        assert item["analysis_href"] == f"/games/{item['game_id']}"
    assert str(template.game_id) in {item["game_id"] for item in payload["games"]}


@pytest.mark.asyncio
async def test_a_forecast_without_a_simulation_summary_is_not_listed(session, settings):
    template = await _seeded(session, settings)
    teams = session.scalars(select(Team).limit(2)).all()
    seeded = session.get(Game, template.game_id)

    game = Game(
        league_id=seeded.league_id, season_id=seeded.season_id, venue_id=seeded.venue_id,
        starts_at=seeded.starts_at + timedelta(minutes=5), status=GameStatus.SCHEDULED,
        source_mode=seeded.source_mode,
    )
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=teams[0].id, is_home=False),
        GameTeam(game_id=game.id, team_id=teams[1].id, is_home=True),
    ])
    # A forecast with no persisted simulation summary cannot be replayed, so the
    # picker must not offer it and then fail on arrival.
    session.add(Forecast(
        game_id=game.id, model_version_id=template.model_version_id,
        feature_version_id=template.feature_version_id, version=1,
        generated_at=template.generated_at, available_as_of=template.available_as_of,
        status=template.status, home_win_probability=0.55, away_win_probability=0.45,
        source_mode=seeded.source_mode, simulation_summary=None,
    ))
    session.flush()

    listed = {item["game_id"] for item in _service(session, settings).eligible_games("MLB")["games"]}
    assert str(game.id) not in listed


@pytest.mark.asyncio
async def test_a_v18_action_appears_only_for_the_game_that_has_a_path(session, settings):
    template = await _seeded(session, settings)
    before = _service(session, settings).eligible_games("MLB")
    assert before["v18_count"] == 0
    assert all(item["v18_available"] is False for item in before["games"])
    assert all(item["v18_href"] is None for item in before["games"])

    bundle_row = _register_shadow(session, template.game_id)
    after = _service(session, settings).eligible_games("MLB")
    assert after["v18_count"] == 1
    target = next(item for item in after["games"] if item["game_id"] == str(template.game_id))
    assert target["v18_available"] is True
    assert target["v18_href"] == f"/games/{template.game_id}/simulation?track=v18"
    assert target["v18_path_events"] == 2
    assert target["v18_forecast_id"] == bundle_row.forecast_id
    # Every other game still says no.
    others = [item for item in after["games"] if item["game_id"] != str(template.game_id)]
    assert all(item["v18_available"] is False for item in others)


@pytest.mark.asyncio
async def test_a_registered_shadow_with_no_path_offers_no_replay(session, settings):
    template = await _seeded(session, settings)
    _register_shadow(session, template.game_id, events=0)

    payload = _service(session, settings).eligible_games("MLB")
    target = next(item for item in payload["games"] if item["game_id"] == str(template.game_id))
    assert target["v18_available"] is False
    assert payload["v18_count"] == 0


@pytest.mark.asyncio
async def test_a_finished_game_stays_explorable_and_is_labelled_final(session, settings):
    template = await _seeded(session, settings)
    game = session.get(Game, template.game_id)
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = 4, 2
    session.flush()

    payload = _service(session, settings).eligible_games("MLB")
    target = next(item for item in payload["games"] if item["game_id"] == str(game.id))
    assert target["is_final"] is True
    assert target["is_pregame"] is False
    assert target["event_status"] == "FINAL"
    assert (target["home_score"], target["away_score"]) == (4, 2)
    assert target["slate_date"] is not None
    # Still openable: a preserved simulation does not stop existing.
    assert target["gen1_available"] is True


@pytest.mark.asyncio
async def test_the_limit_is_bounded(session, settings):
    await _seeded(session, settings)
    service = _service(session, settings)
    assert len(service.eligible_games("MLB", limit=1)["games"]) <= 1
    assert len(service.eligible_games("MLB", limit=10_000)["games"]) <= MAX_LIMIT


def _register_shadow(session, game_id, *, events: int = 2) -> ShadowForecast:
    """A minimal registered V18 row, with or without a sequential path."""

    from app.models import ShadowForecastBundle
    from app.shadow_import.mlb_v18 import ENGINE_ID

    bundle = ShadowForecastBundle(
        bundle_sha256="b" * 64, contract_version="AISP_MLB_V18_RESEARCH_SHADOW_BUNDLE_V1",
        bundle_id="mlb-v18-test", logical_uri="/tmp/bundle", source_fixture_id="contract",
        slate_date=SLATE, league="MLB", sport="MLB", status="RESEARCH_SHADOW",
        research_shadow_only=True, generated_at=session.get(Game, game_id).starts_at,
        forecast_count=1, quote_count=0, candidate_count=0,
        imported_at=session.get(Game, game_id).starts_at,
    )
    session.add(bundle)
    session.flush()

    path = [[{"pa": index + 1} for index in range(events)]] if events else []
    row = ShadowForecast(
        bundle_id=bundle.id, canonical_game_id=game_id, forecast_id="mlb-v18-test-1",
        engine_id=ENGINE_ID, engine_version="mlb-v18-test",
        model_artifact_sha256="c" * 64, simulator_id="V18_WHOLE_GAME_MONTE_CARLO",
        simulator_version="v1", simulator_seed=20260907,
        event_id=str(game_id), source_event_id="1", event_date=SLATE, season=2026,
        home_team_id="home", away_team_id="away", is_ready=True,
        readiness_score=1.0, readiness_threshold=1.0, status="PROSPECTIVE_RESEARCH_SHADOW",
        full_home_win_probability=0.43, full_away_win_probability=0.57,
        full_expected_home_score=3.5, full_expected_away_score=4.4,
        distributions={}, uncertainty={}, readiness_detail={},
        identity_evidence={}, lineage_detail={"sequentialPath": path},
    )
    session.add(row)
    session.flush()
    return row
