"""Which model the $1,000 Experiment page says placed its positions.

The defect this covers was live and misleading rather than merely untidy. The
2026 MLB season spans two engines: a Gen 1 predecessor era whose decisions bind
``forecast_id`` into ``forecasts``, and a V18 era whose decisions bind
``shadow_forecast_id`` into ``shadow_forecasts``. The identity resolver joined
only the first path, so the V18 era reported NO model at all -- and the only
model name left on the page came from the Gen 1 comparison table beside it,
which names ``asp_mlb_ensemble``. A reader had every reason to conclude
asp_mlb_ensemble had placed V18's positions.

Nothing here changes model selection, thresholds or staking. These are read-path
assertions about attribution.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    ExperimentDecisionPhase, FeatureVersion, Forecast, ForecastStatus, Game,
    GameStatus, League, ModelStatus, ModelVersion, Season, ShadowExperiment,
    ShadowExperimentDecision, ShadowForecast, ShadowForecastBundle, Sport, Venue,
)
from app.services.read_service import ReadService

START = datetime(2026, 9, 12, 23, 10, tzinfo=UTC)
V18_ENGINE = "MLB_V18_WHOLE_GAME"
V18_VERSION = "mlb-v18-whole-game-a6e7e99122f1b60701a4728b"


@pytest.fixture
def world(session: Session):
    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    venue = Venue(name="Park", city="City", timezone="UTC")
    session.add_all([league, venue])
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=START - timedelta(days=160), ends_at=START + timedelta(days=30))
    session.add(season)
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, venue_id=venue.id,
                starts_at=START, status=GameStatus.SCHEDULED)
    session.add(game)
    session.flush()
    return {"session": session, "league": league, "game": game}


def _experiment(world, *, engine: str, policy: str, bankroll: float = 993.52):
    experiment = ShadowExperiment(
        name="ASP MLB V18 CONDITIONAL VIRTUAL - 2026 V1", version=6,
        started_at=START - timedelta(hours=12), start_date=START.date(),
        starting_bankroll=bankroll, sport="MLB",
        recommendation_policy_version="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
        staking_policy_version="AISP_QUARTER_KELLY_EVIDENCE_V2",
        decision_policy=policy, controlling_engine=engine,
        supported_markets=["MONEYLINE"], kelly_fraction=0.25, unit_percentage=0.01,
        max_position_percentage=0.01, max_same_game_percentage=0.02,
        max_daily_percentage=0.05, code_commit="c", code_identity="i",
    )
    world["session"].add(experiment)
    world["session"].flush()
    return experiment


def _v18_decision(world, experiment):
    session = world["session"]
    bundle = ShadowForecastBundle(
        bundle_sha256="a" * 64, bundle_id="b", logical_uri="test://b",
        source_fixture_id="f", slate_date=START.date(), league="MLB", sport="MLB",
        status="PROSPECTIVE_RESEARCH_SHADOW", research_shadow_only=True,
        generated_at=START - timedelta(hours=6),
        forecast_count=1, quote_count=0, candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    forecast = ShadowForecast(
        bundle_id=bundle.id, forecast_id="mlb-v18-daily-d8bc8570",
        engine_id=V18_ENGINE, engine_version=V18_VERSION,
        model_artifact_sha256="4" * 64, simulator_id="SIM", simulator_version="1",
        simulator_seed=18, event_id="e-1", source_event_id="823496",
        event_date=START.date(), season=2026, canonical_game_id=world["game"].id,
        home_team_id="NYY", away_team_id="NYM",
        snapshot_type="PROSPECTIVE", snapshot_version=1, is_ready=True,
        readiness_score=1.0, readiness_threshold=1.0,
        status="PROSPECTIVE_RESEARCH_SHADOW", scheduled_start_utc=START,
        available_as_of_utc=START - timedelta(hours=2),
        source_cutoff_utc=START - timedelta(hours=3),
        full_home_win_probability=0.6386, full_away_win_probability=0.3614,
        full_expected_home_score=4.9, full_expected_away_score=4.1,
        distributions={}, uncertainty={}, readiness_detail={},
    )
    session.add(forecast)
    session.flush()
    session.add(ShadowExperimentDecision(
        experiment_id=experiment.id, game_id=world["game"].id,
        slate_date=START.date(), phase=ExperimentDecisionPhase.FINAL_PREGAME,
        market="MONEYLINE", selection="NYY ML",
        recommended_at=START - timedelta(minutes=20),
        recommendation="QUALIFIED_BET", model_probability=0.6386,
        recommendation_reason="V2 qualified", shadow_forecast_id=forecast.id,
    ))
    session.flush()


def _gen1_decision(world, experiment):
    session = world["session"]
    version = ModelVersion(name="asp_mlb_ensemble", version="2.0.0-gate2",
                           status=ModelStatus.CHAMPION)
    feature = FeatureVersion(name="features", version="1.0.0", schema_hash="d" * 64)
    session.add_all([version, feature])
    session.flush()
    forecast = Forecast(
        game_id=world["game"].id, model_version_id=version.id,
        feature_version_id=feature.id, version=1, status=ForecastStatus.PUBLISHED,
        generated_at=START - timedelta(hours=6),
        available_as_of=START - timedelta(hours=6),
        home_win_probability=0.52, away_win_probability=0.48,
    )
    session.add(forecast)
    session.flush()
    session.add(ShadowExperimentDecision(
        experiment_id=experiment.id, game_id=world["game"].id,
        slate_date=START.date(), phase=ExperimentDecisionPhase.FINAL_PREGAME,
        market="MONEYLINE", selection="NYY ML",
        recommended_at=START - timedelta(minutes=25),
        recommendation="QUALIFIED_BET", model_probability=0.52,
        recommendation_reason="V2 qualified", forecast_id=forecast.id,
    ))
    session.flush()


def _identity(world, experiment):
    return ReadService(world["session"], get_settings())._experiment_identity(experiment)


def test_a_v18_era_names_v18_not_nothing(world):
    """The era bound its forecasts through shadow_forecast_id, and said so."""

    experiment = _experiment(world, engine=V18_ENGINE,
                             policy="AISP_PRACTICAL_PREGAME_POLICY_V2_1")
    _v18_decision(world, experiment)

    identity = _identity(world, experiment)
    assert identity["model_name"] == V18_ENGINE
    assert identity["model_version"] == V18_VERSION
    assert identity["model_identity_source"] == "decisions→shadow_forecasts"
    # The failure mode this replaces: an era holding seven V18 positions that
    # reported no model at all.
    assert identity["model_identity_source"] != "no decision references a forecast yet"


def test_a_v18_era_is_not_described_as_the_same_model(world):
    """It is a different MODEL under the same decision policy -- the opposite."""

    experiment = _experiment(world, engine=V18_ENGINE,
                             policy="AISP_PRACTICAL_PREGAME_POLICY_V2_1")
    _v18_decision(world, experiment)

    relationship = _identity(world, experiment)["policy_relationship"]
    assert "DIFFERENT FORECASTING MODEL" in relationship
    assert V18_ENGINE in relationship
    assert "SAME MODEL" not in relationship


def test_a_gen1_era_still_reports_its_model_version_and_relationship(world):
    """The predecessor's honest description is preserved exactly."""

    experiment = _experiment(world, engine="AISP_BASELINE",
                             policy="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
                             bankroll=1000.0)
    _gen1_decision(world, experiment)

    identity = _identity(world, experiment)
    assert identity["model_name"] == "asp_mlb_ensemble"
    assert identity["model_version"] == "2.0.0-gate2"
    assert identity["model_identity_source"] == "decisions→forecasts→model_versions"
    assert identity["policy_relationship"] == (
        "SAME MODEL · DIFFERENT DECISION POLICY — not a new forecasting model")


def test_the_strict_control_era_is_unchanged(world):
    experiment = _experiment(world, engine="AISP_BASELINE",
                             policy="AISP_OPPORTUNITY_POLICY_V1", bankroll=1000.0)
    _gen1_decision(world, experiment)

    identity = _identity(world, experiment)
    assert identity["experiment_key"] == "v1"
    assert identity["experiment_role"] == "STRICT_CONTROL"
    assert identity["policy_relationship"] == "Reference control policy"


def test_an_era_referencing_both_engines_says_so_rather_than_picking_one(world):
    """Silently showing the more frequent one would hide a real mixture."""

    experiment = _experiment(world, engine=V18_ENGINE,
                             policy="AISP_PRACTICAL_PREGAME_POLICY_V2_1")
    _v18_decision(world, experiment)
    _gen1_decision(world, experiment)

    identity = _identity(world, experiment)
    assert "MIXED MODEL IDENTITIES" in identity["policy_relationship"]
    assert "2 model identities referenced" in identity["model_identity_source"]
