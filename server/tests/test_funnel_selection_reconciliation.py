"""The prefetched read context must select exactly what the per-game path selects.

The funnel serves ForecastSelector from one set-based context. This proves the
two paths agree, game by game and in order, on a slate that exercises every
selection branch: V18 available, V18 missing after the era boundary, a
pre-era Gen 1 game, a V18 forecast invalidated by a recorded decision, and a
game with a draft-only Gen 1 row.
"""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.models import FeatureVersion, Forecast, ForecastStatus, ModelStatus, ModelVersion
from app.services.funnel_read_context import (
    attach_context, build_funnel_read_context, detach_context,
)
from app.services.league_scope import MLB
from app.services.qualification_service import QualificationService
from app.services.read_service import ReadService
from tests.test_forecast_selection import (  # noqa: F401  (fixture re-export)
    BOUNDARY, _authority, _blocking_decision, _game, _v18, world,
)


def _gen1(world, game, home_wp, *, status=ForecastStatus.PUBLISHED, **extra):
    """One Gen 1 row per call, sharing a single model/feature version."""
    session = world["session"]
    version = session.scalar(select(ModelVersion).where(ModelVersion.name == "asp_mlb_ensemble"))
    if version is None:
        version = ModelVersion(name="asp_mlb_ensemble", version="2.0.0", status=ModelStatus.CHAMPION)
        feature = FeatureVersion(name="features", version="1.0.0", schema_hash="d" * 64)
        session.add_all([version, feature])
        session.flush()
    feature = session.scalar(select(FeatureVersion).where(FeatureVersion.name == "features"))
    row = Forecast(
        game_id=game.id, model_version_id=version.id, feature_version_id=feature.id,
        version=1, status=status,
        generated_at=game.starts_at - timedelta(hours=6),
        available_as_of=game.starts_at - timedelta(hours=6),
        home_win_probability=home_wp, away_win_probability=1 - home_wp, **extra,
    )
    session.add(row)
    session.flush()
    return row


def _mixed_slate(world):
    _authority(world)
    day_start = BOUNDARY + timedelta(hours=2)
    available = _game(world, day_start)
    _v18(world, available, published=available.starts_at - timedelta(hours=1), home_wp=0.6024)
    _gen1(world, available, home_wp=0.5889, expected_home_runs=4.1, expected_away_runs=3.9)
    missing = _game(world, day_start + timedelta(minutes=5))
    _gen1(world, missing, home_wp=0.52)
    invalidated = _game(world, day_start + timedelta(minutes=10))
    _v18(world, invalidated, published=invalidated.starts_at - timedelta(hours=1), home_wp=0.41)
    _v18(world, invalidated, published=invalidated.starts_at - timedelta(minutes=50), home_wp=0.43, version=2)
    _blocking_decision(world, invalidated,
                       "V2 TIER_D_INELIGIBLE: Lineup evidence MISSING_BLOCKING (Material lineup change).")
    draft_only = _game(world, day_start + timedelta(minutes=15))
    _gen1(world, draft_only, home_wp=0.55, status=ForecastStatus.DRAFT)
    pre_era = _game(world, BOUNDARY - timedelta(hours=1))
    _gen1(world, pre_era, home_wp=0.47)
    world["session"].flush()
    return [available, missing, invalidated, draft_only, pre_era]


def _cards(read: ReadService, games) -> list[dict]:
    return [json.loads(read.game_card(g).model_dump_json()) for g in games]


def test_context_selection_matches_the_per_game_path(world):
    session, settings = world["session"], get_settings()
    games = _mixed_slate(world)
    read = ReadService(session, settings)

    legacy = _cards(read, games)
    ctx = build_funnel_read_context(session, settings, games)
    attach_context(session, ctx)
    try:
        prefetched = _cards(read, games)
    finally:
        detach_context(session)

    assert [c["id"] for c in prefetched] == [c["id"] for c in legacy]
    for before, after in zip(legacy, prefetched):
        for volatile in ("last_update",):
            before.pop(volatile, None)
            after.pop(volatile, None)
        assert after == before
    engines = [c["forecast_selection"]["displayed_engine_id"] for c in prefetched]
    assert engines == ["MLB_V18_WHOLE_GAME", None, "MLB_V18_WHOLE_GAME", None, "AISP_BASELINE"]
    assert prefetched[2]["forecast_selection"]["primary"]["availability"] == "INVALIDATED"


def test_funnels_return_the_same_ordered_results_with_and_without_prefetch(world):
    session, settings = world["session"], get_settings().model_copy(update={"demo_mode": False})
    games = _mixed_slate(world)
    for game in games:
        game.source_mode = "LIVE"
    session.flush()
    days = sorted({g.starts_at.date() for g in games})
    qs = QualificationService(session, settings)
    compared = 0
    for day in days:
        compared += len(ReadService(session, settings)._live_games_for_date(day, MLB))
        for name in ("funnel", "funnel_v2"):
            kwargs = {} if name == "funnel" else {"now": BOUNDARY}
            with_ctx = getattr(qs, name)(day, prefetch=True, **kwargs)
            without = getattr(qs, name)(day, prefetch=False, **kwargs)
            strip = lambda payload: json.loads(json.dumps(  # noqa: E731
                {k: v for k, v in payload.items() if k != "read_model"}, default=str))
            assert strip(with_ctx) == strip(without), (name, day)
            assert [g.get("game_id") for g in with_ctx["games"]] == [g.get("game_id") for g in without["games"]]
    assert compared == len(games)
