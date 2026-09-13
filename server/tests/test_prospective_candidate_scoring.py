"""Can the existing scheduled scorer evaluate a prospective candidate?

The replay path is settled: HISTORICAL_REPLAY is refused. What was never
exercised is the other half -- a genuinely PROSPECTIVE candidate identity
sitting beside the baseline on the same game. These tests establish whether the
scorer already evaluates both against the SAME accepted final revision, or
whether it needs a narrow extension.

Nothing here activates a model or creates a decision or a position.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStateObservation, GameStatus, GameTeam, League,
    ResearchForecastEvaluation, Season, ShadowForecast, ShadowForecastBundle,
    Sport, Team,
)
from app.services.forecast_evaluation import (
    HISTORICAL_REPLAY, ForecastEvaluationService,
)
from app.shadow_import.mlb_ensemble_candidate import ENGINE_ID as CANDIDATE_ENGINE
from app.shadow_import.mlb_v18 import ENGINE_ID as BASELINE_ENGINE

START = datetime(2026, 9, 12, 23, 10, tzinfo=UTC)
FINAL_HOME, FINAL_AWAY = 6, 2


@pytest.fixture
def world(session: Session):
    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=START - timedelta(days=160), ends_at=START + timedelta(days=30))
    home = Team(league_id=league.id, name="Detroit Tigers", abbreviation="DET")
    away = Team(league_id=league.id, name="Colorado Rockies", abbreviation="COL")
    session.add_all([season, home, away])
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=START,
                status=GameStatus.SCHEDULED, source_mode="LIVE")
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    bundle = ShadowForecastBundle(
        bundle_sha256="a" * 64, bundle_id="b", logical_uri="test://b",
        source_fixture_id="f", slate_date=START.date(), league="MLB", sport="MLB",
        status="RESEARCH_SHADOW", research_shadow_only=True,
        generated_at=START - timedelta(hours=6),
        forecast_count=2, quote_count=0, candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    return {"session": session, "game": game, "bundle": bundle,
            "home": home, "away": away}


def forecast(world, *, engine, version, identity, status, home_win, published):
    row = ShadowForecast(
        bundle_id=world["bundle"].id, forecast_id=identity,
        engine_id=engine, engine_version=version,
        model_artifact_sha256="4" * 64,
        simulator_id="SIM", simulator_version="1", simulator_seed=18,
        event_id=f"e-{identity}", source_event_id="824227",
        event_date=START.date(), season=2026,
        canonical_game_id=world["game"].id,
        home_team_id="DET", away_team_id="COL",
        snapshot_type="PROSPECTIVE", snapshot_version=1,
        is_ready=True, readiness_score=1.0, readiness_threshold=1.0,
        status=status,
        scheduled_start_utc=START,
        available_as_of_utc=published,
        source_cutoff_utc=published - timedelta(minutes=8),
        forecast_cutoff_utc=published,
        full_home_win_probability=home_win,
        full_away_win_probability=round(1 - home_win, 4),
        full_expected_home_score=5.03, full_expected_away_score=4.03,
        distributions={}, uncertainty={"simulationCount": 5000}, readiness_detail={},
    )
    world["session"].add(row)
    world["session"].flush()
    return row


def final(world):
    session = world["session"]
    session.add(GameStateObservation(
        game_id=world["game"].id, home_team_id=world["home"].id,
        away_team_id=world["away"].id, scheduled_at=START,
        status=GameStatus.FINAL.value, home_score=FINAL_HOME, away_score=FINAL_AWAY,
        observed_at=START + timedelta(hours=3),
        available_as_of=START + timedelta(hours=3),
        source="MLB_STATS_API", source_mode="LIVE", payload_checksum="obs-a",
    ))
    world["game"].status = GameStatus.FINAL
    session.flush()


def test_a_prospective_candidate_is_scored_beside_the_baseline_on_one_final(world):
    """Both identities, one accepted final revision, no duplicated evaluation."""

    session = world["session"]
    forecast(world, engine=BASELINE_ENGINE, version="v18-a", identity="v18-1",
             status="PROSPECTIVE_RESEARCH_SHADOW", home_win=0.6138,
             published=START - timedelta(hours=3))
    forecast(world, engine=CANDIDATE_ENGINE, version="rc-1", identity="cand-1",
             status="PROSPECTIVE_RESEARCH_SHADOW", home_win=0.6312,
             published=START - timedelta(hours=2))
    final(world)

    outcome = ForecastEvaluationService(session).evaluate_game(
        canonical_game_id=world["game"].id)

    assert outcome["scored"] == 2, "the existing scorer already handles both identities"
    written = list(session.scalars(select(ResearchForecastEvaluation)))
    assert {row.engine_id for row in written} == {BASELINE_ENGINE, CANDIDATE_ENGINE}
    # Same final, same result identity: a like-for-like comparison.
    assert len({row.result_identity for row in written}) == 1
    assert all(row.is_current for row in written)
    assert all(row.home_score == FINAL_HOME for row in written)


def test_rerunning_does_not_duplicate_the_candidate_evaluation(world):
    session = world["session"]
    forecast(world, engine=BASELINE_ENGINE, version="v18-a", identity="v18-1",
             status="PROSPECTIVE_RESEARCH_SHADOW", home_win=0.6138,
             published=START - timedelta(hours=3))
    forecast(world, engine=CANDIDATE_ENGINE, version="rc-1", identity="cand-1",
             status="PROSPECTIVE_RESEARCH_SHADOW", home_win=0.6312,
             published=START - timedelta(hours=2))
    final(world)
    service = ForecastEvaluationService(session)

    first = service.evaluate_game(canonical_game_id=world["game"].id)
    second = service.evaluate_game(canonical_game_id=world["game"].id)

    assert first["scored"] == 2
    assert second["scored"] == 0 and second["reused"] == 2
    assert session.scalar(select(func.count()).select_from(ResearchForecastEvaluation)) == 2


def test_a_replay_candidate_is_still_refused_beside_a_scored_baseline(world):
    """The replay exclusion must survive the candidate identity being scoreable."""

    session = world["session"]
    forecast(world, engine=BASELINE_ENGINE, version="v18-a", identity="v18-1",
             status="PROSPECTIVE_RESEARCH_SHADOW", home_win=0.6138,
             published=START - timedelta(hours=3))
    forecast(world, engine=CANDIDATE_ENGINE, version="rc-1", identity="replay-1",
             status="HISTORICAL_REPLAY_RESEARCH_SHADOW", home_win=0.6312,
             published=START + timedelta(hours=6))
    final(world)

    outcome = ForecastEvaluationService(session).evaluate_game(
        canonical_game_id=world["game"].id)

    assert outcome["scored"] == 1
    written = list(session.scalars(select(ResearchForecastEvaluation)))
    assert {row.engine_id for row in written} == {BASELINE_ENGINE}
    assert any(entry["reason"] == HISTORICAL_REPLAY for entry in outcome["excluded"])


def test_an_unfinalized_game_leaves_both_identities_pending(world):
    session = world["session"]
    forecast(world, engine=BASELINE_ENGINE, version="v18-a", identity="v18-1",
             status="PROSPECTIVE_RESEARCH_SHADOW", home_win=0.6138,
             published=START - timedelta(hours=3))
    forecast(world, engine=CANDIDATE_ENGINE, version="rc-1", identity="cand-1",
             status="PROSPECTIVE_RESEARCH_SHADOW", home_win=0.6312,
             published=START - timedelta(hours=2))

    outcome = ForecastEvaluationService(session).evaluate_game(
        canonical_game_id=world["game"].id)

    assert outcome["scored"] == 0
    assert outcome["skipped"] == "NO_AUTHORITATIVE_FINAL"
    assert session.scalar(select(func.count()).select_from(ResearchForecastEvaluation)) == 0
