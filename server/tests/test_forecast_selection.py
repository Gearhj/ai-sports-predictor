"""Which forecast a surface leads with, and what it refuses to substitute.

Each test is a way the selection goes wrong if a rule is dropped: a game
showing one engine on one page and another beside it, today's era rewriting a
game that predates it, a replay presented as a pregame forecast, or an
invalidated forecast quietly swapped for a different model's number.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    FeatureVersion, Forecast, ForecastStatus, Game, GameStatus, League,
    ModelStatus, ModelVersion,
    ShadowExperiment, ShadowExperimentDecision, ShadowForecast, ShadowForecastBundle,
    Sport, Venue,
)
from app.models.entities import ExperimentDecisionPhase, ModelChampionAuthority
from app.services.forecast_selection import Availability, ForecastSelector, V18_ENGINE

BOUNDARY = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)


# -- world ---------------------------------------------------------------


@pytest.fixture
def world(session: Session):
    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    venue = Venue(name="Park", city="City", timezone="UTC")
    session.add_all([league, venue])
    session.flush()
    return {"session": session, "league": league, "venue": venue}


def _game(world, starts_at: datetime) -> Game:
    game = Game(
        league_id=world["league"].id, venue_id=world["venue"].id,
        starts_at=starts_at, status=GameStatus.SCHEDULED,
    )
    world["session"].add(game)
    world["session"].flush()
    return game


def _authority(world, effective_at: datetime = BOUNDARY) -> None:
    world["session"].add(ModelChampionAuthority(
        engine_id=V18_ENGINE, model_era_id=f"ERA_{uuid4().hex[:8]}",
        handoff_sha256=uuid4().hex + uuid4().hex[:32],
        schema_version="AISP_MLB_V18_CHAMPION_HANDOFF_V1", sport="MLB",
        model_name="MLB_V18_HGB_PA", model_version="V18",
        model_artifact_sha256="b" * 64, champion_manifest_sha256="c" * 64,
        minimum_adjusted_edge=0.02,
        recommendation_policy_version="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
        staking_policy_version="AISP_QUARTER_KELLY_EVIDENCE_V2",
        effective_at=effective_at, state="ACTIVE",
    ))
    world["session"].flush()


def _v18(world, game: Game, *, published: datetime, home_wp: float = 0.60,
         cutoff: datetime | None = None, version: int = 1) -> ShadowForecast:
    session = world["session"]
    bundle = ShadowForecastBundle(
        bundle_sha256=uuid4().hex + uuid4().hex[:32], bundle_id=f"b-{uuid4().hex[:8]}",
        logical_uri="test://bundle", source_fixture_id="fixture",
        slate_date=game.starts_at.date(), league="MLB", sport="BASEBALL",
        status="RESEARCH_SHADOW", generated_at=published,
        forecast_count=1, quote_count=0, candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    forecast = ShadowForecast(
        bundle_id=bundle.id, canonical_game_id=game.id,
        forecast_id=f"mlb-v18-{uuid4().hex[:12]}",
        snapshot_type="PROSPECTIVE", snapshot_version=version,
        engine_id=V18_ENGINE, engine_version="mlb-v18-whole-game-test",
        model_artifact_sha256="a" * 64, simulator_id="sim", simulator_version="1",
        simulator_seed=1, event_id=f"e-{uuid4().hex[:8]}", source_event_id="824224",
        event_date=game.starts_at.date(), season=2026,
        home_team_id="DET", away_team_id="COL",
        is_ready=True, readiness_score=90.0, readiness_threshold=50.0,
        status="RESEARCH_SHADOW", scheduled_start_utc=game.starts_at,
        source_cutoff_utc=cutoff or (published - timedelta(minutes=10)),
        available_as_of_utc=published,
        full_home_win_probability=home_wp, full_away_win_probability=1 - home_wp,
        full_expected_home_score=4.8, full_expected_away_score=4.1,
        distributions={}, uncertainty={}, readiness_detail={},
    )
    session.add(forecast)
    session.flush()
    return forecast


def _gen1(world, game: Game, home_wp: float = 0.52) -> Forecast:
    session = world["session"]
    version = ModelVersion(
        name="asp_mlb_ensemble", version="2.0.0", status=ModelStatus.CHAMPION,
    )
    feature = FeatureVersion(name="features", version="1.0.0", schema_hash="d" * 64)
    session.add_all([version, feature])
    session.flush()
    forecast = Forecast(
        game_id=game.id, model_version_id=version.id, feature_version_id=feature.id,
        version=1, status=ForecastStatus.PUBLISHED,
        generated_at=game.starts_at - timedelta(hours=6),
        available_as_of=game.starts_at - timedelta(hours=6),
        home_win_probability=home_wp, away_win_probability=1 - home_wp,
    )
    session.add(forecast)
    session.flush()
    return forecast


def _blocking_decision(world, game: Game, reason: str) -> None:
    session = world["session"]
    experiment = ShadowExperiment(
        name="TEST", version=1, started_at=BOUNDARY, start_date=BOUNDARY.date(),
        starting_bankroll=1000.0, sport="MLB",
        recommendation_policy_version="P", staking_policy_version="S",
        decision_policy="P", controlling_engine=V18_ENGINE, supported_markets=["MONEYLINE"],
        kelly_fraction=0.25, unit_percentage=0.01, max_position_percentage=0.01,
        max_same_game_percentage=0.02, max_daily_percentage=0.05,
        code_commit="c", code_identity="i",
    )
    session.add(experiment)
    session.flush()
    session.add(ShadowExperimentDecision(
        experiment_id=experiment.id, game_id=game.id,
        slate_date=game.starts_at.date(), phase=ExperimentDecisionPhase.LINEUP_CONFIRMED,
        market="MONEYLINE", selection="DET ML",
        recommended_at=game.starts_at - timedelta(minutes=20),
        recommendation="NO_BET", recommendation_reason=reason,
    ))
    session.flush()


# -- the rules -----------------------------------------------------------


def test_a_current_game_leads_with_the_controlling_engine(world):
    """The whole point: every surface shows V18 for a game V18 governs."""

    _authority(world)
    game = _game(world, BOUNDARY + timedelta(hours=2))
    _v18(world, game, published=game.starts_at - timedelta(hours=1), home_wp=0.6024)
    _gen1(world, game, home_wp=0.5889)

    result = ForecastSelector(world["session"]).select(game)
    assert result.primary is not None
    assert result.primary.engine_id == V18_ENGINE
    assert result.primary.availability is Availability.AVAILABLE
    assert result.primary.home_win_probability == pytest.approx(0.6024)
    assert result.primary.decision_eligible is True
    # Gen 1 stays reachable as an explicit comparison, not as the default.
    assert [row.engine_id for row in result.comparison] == ["AISP_BASELINE"]


def test_a_game_that_predates_the_era_keeps_the_forecast_that_governed_it(world):
    """Today's era is not applied backwards over yesterday's games."""

    _authority(world)
    game = _game(world, BOUNDARY - timedelta(days=1))
    _v18(world, game, published=game.starts_at - timedelta(hours=1), home_wp=0.60)
    _gen1(world, game, home_wp=0.52)

    result = ForecastSelector(world["session"]).select(game)
    assert result.primary is not None
    assert result.primary.engine_id == "AISP_BASELINE"
    assert result.primary.home_win_probability == pytest.approx(0.52)
    # V18 is offered as the comparison, flagged as outside its era.
    assert result.comparison[0].engine_id == V18_ENGINE
    assert result.comparison[0].availability is Availability.OUTSIDE_ERA
    assert result.comparison[0].decision_eligible is False


def test_a_forecast_published_after_first_pitch_is_a_replay(world):
    """A postgame forecast is evidence, never a pregame forecast."""

    _authority(world)
    game = _game(world, BOUNDARY + timedelta(hours=2))
    _v18(world, game, published=game.starts_at + timedelta(minutes=30))

    result = ForecastSelector(world["session"]).select(game)
    assert result.primary.availability is Availability.NOT_PREGAME
    assert result.primary.decision_eligible is False
    assert "replay" in result.primary.availability_detail.lower()


def test_an_input_that_changed_after_publication_invalidates_the_forecast(world):
    """The recorded policy judgement is read, not recomputed at display time."""

    _authority(world)
    game = _game(world, BOUNDARY + timedelta(hours=2))
    _v18(world, game, published=game.starts_at - timedelta(hours=1))
    _blocking_decision(
        world, game,
        "V2 TIER_D_INELIGIBLE: Lineup evidence MISSING_BLOCKING "
        "(Material lineup change after the current forecast: LINEUP_CONFIRMED)",
    )

    result = ForecastSelector(world["session"]).select(game)
    assert result.primary.availability is Availability.INVALIDATED
    assert result.primary.decision_eligible is False
    assert "Material lineup change" in result.primary.availability_detail
    # The forecast itself stays visible with its limitation.
    assert result.primary.home_win_probability is not None


def test_the_blocking_reason_reads_as_a_sentence(world):
    """The stored reason ends ")." — a reader should see neither character.

    The policy writes a tier code first and the readable half in parentheses,
    with a full stop outside the bracket. Stripping the bracket and the stop in
    a fixed order always leaves one of them behind, which is how a stray ")"
    reached the market board.
    """

    _authority(world)
    game = _game(world, BOUNDARY + timedelta(hours=2))
    _v18(world, game, published=game.starts_at - timedelta(hours=1))
    _blocking_decision(
        world, game,
        "V2 TIER_D_INELIGIBLE: Lineup evidence MISSING_BLOCKING "
        "(Material lineup change after the current forecast: LINEUP_CONFIRMED).",
    )

    detail = ForecastSelector(world["session"]).select(game).primary.availability_detail
    assert "LINEUP_CONFIRMED." in detail
    assert ")" not in detail
    assert "TIER_D_INELIGIBLE" not in detail


def test_a_blocking_reason_without_a_parenthetical_is_kept_whole(world):
    """Not every policy reason has a bracketed half to prefer."""

    _authority(world)
    game = _game(world, BOUNDARY + timedelta(hours=2))
    _v18(world, game, published=game.starts_at - timedelta(hours=1))
    _blocking_decision(world, game, "Lineup evidence MISSING_BLOCKING for this game.")

    detail = ForecastSelector(world["session"]).select(game).primary.availability_detail
    assert "Lineup evidence MISSING_BLOCKING for this game." in detail


def test_a_missing_v18_is_reported_rather_than_silently_replaced(world):
    """Silently showing Gen 1 would hide that the governing engine said nothing.

    Gen 1 may still supply a number on a surface that needs one -- what is
    forbidden is doing it unannounced. So the selection keeps V18 as primary
    with no probability, states the condition, and leaves Gen 1 in the
    comparison list where a caller has to name it to use it.
    """

    _authority(world)
    game = _game(world, BOUNDARY + timedelta(hours=2))
    _gen1(world, game, home_wp=0.52)

    result = ForecastSelector(world["session"]).select(game)
    assert result.primary is not None
    assert result.primary.engine_id == V18_ENGINE
    assert result.primary.availability is Availability.MISSING
    assert result.primary.home_win_probability is None
    assert "published no forecast for this game" in result.primary.availability_detail
    assert "labelled with the engine that produced it" in result.primary.availability_detail
    # Gen 1 is present, labelled as the comparison.
    assert result.comparison[0].engine_id == "AISP_BASELINE"


def test_the_newest_snapshot_is_the_one_selected(world):
    """Two revisions must not both claim to be the current forecast."""

    _authority(world)
    game = _game(world, BOUNDARY + timedelta(hours=3))
    _v18(world, game, published=game.starts_at - timedelta(hours=2), home_wp=0.55, version=1)
    newest = _v18(world, game, published=game.starts_at - timedelta(hours=1),
                  home_wp=0.61, version=2)

    result = ForecastSelector(world["session"]).select(game)
    assert result.primary.forecast_id == newest.forecast_id
    assert result.primary.snapshot_version == 2


def test_identity_carries_both_clocks_and_never_the_read_time(world):
    """Evidence cutoff and publication are distinct, and both are stored values."""

    _authority(world)
    game = _game(world, BOUNDARY + timedelta(hours=2))
    cutoff = game.starts_at - timedelta(hours=3)
    published = game.starts_at - timedelta(hours=1)
    forecast = _v18(world, game, published=published, cutoff=cutoff)

    identity = ForecastSelector(world["session"]).select(game).primary
    assert identity.source_cutoff_utc == cutoff
    assert identity.published_utc == published
    assert identity.forecast_id == forecast.forecast_id
    assert identity.engine_version == "mlb-v18-whole-game-test"


def test_selecting_a_forecast_writes_nothing(world):
    """Display selection is a read. It cannot create a decision or a position."""

    from app.models import OfficialAspPosition

    _authority(world)
    game = _game(world, BOUNDARY + timedelta(hours=2))
    _v18(world, game, published=game.starts_at - timedelta(hours=1))
    session = world["session"]
    before_decisions = session.query(ShadowExperimentDecision).count()
    before_positions = session.query(OfficialAspPosition).count()

    ForecastSelector(session).select(game)

    assert session.query(ShadowExperimentDecision).count() == before_decisions
    assert session.query(OfficialAspPosition).count() == before_positions
