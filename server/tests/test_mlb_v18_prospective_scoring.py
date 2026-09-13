"""MLB V18 research scoring — the risks the repair actually changed.

The scorer itself is the NFL one and is already covered. What is new here is
that it now serves MLB, and these tests pin the four things that could go wrong
in that reuse:

  * what counts as scoreable final evidence -- a live observation, a bare
    ``games.FINAL`` flag and a score sitting on the game row with nothing
    behind it must all be refused, because the whole repair rests on the claim
    that a research score never scores against unprovenanced evidence;
  * running the job twice writes one row, not two;
  * a corrected final scores beside the original and links to it, rather than
    overwriting the verdict we published before the correction;
  * a forecast whose publication time is only its evidence cutfoff under
    another name is scored, but marked qualified rather than verified.

Nothing here calls a provider, executes a model, or creates a decision.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ExternalIdMapping, Game, GameStateObservation, GameStatus, GameTeam, League,
    ResearchForecastEvaluation, Season, ShadowForecast, ShadowForecastBundle,
    Sport, Team,
)
from app.services.forecast_evaluation import (
    CLOCK_UNRESOLVED, CLOCK_VERIFIED, ForecastEvaluationService,
    MLB_SCORING_VERSION,
)

START = datetime(2026, 9, 10, 23, 5, tzinfo=UTC)
GAME_PK = "823499"
ENGINE = "MLB_V18_WHOLE_GAME"
ENGINE_VERSION = "mlb-v18-whole-game-a6e7e99122f1b60701a4728b"

HOME_WIN, AWAY_WIN = 0.6142, 0.3858
HOME_RUNS, AWAY_RUNS = 5.11, 4.02
FINAL_HOME, FINAL_AWAY = 10, 3

#: A whole-game simulator publishes marginals, not a joint score distribution.
MARGINALS = {
    "homeRunDistribution": {"0": 0.1, "3": 0.3, "5": 0.35, "10": 0.25},
    "awayRunDistribution": {"0": 0.2, "3": 0.4, "5": 0.3, "9": 0.1},
    "totalRunDistribution": {"3": 0.2, "8": 0.4, "13": 0.4},
    "runDifferentialDistribution": {"-3": 0.25, "0": 0.25, "2": 0.25, "7": 0.25},
}


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
    home = Team(league_id=league.id, name="Colorado Rockies", abbreviation="COL")
    away = Team(league_id=league.id, name="Miami Marlins", abbreviation="MIA")
    session.add_all([season, home, away])
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=START,
                status=GameStatus.SCHEDULED, source_mode="LIVE")
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
        ExternalIdMapping(entity_type="game", entity_id=game.id,
                          source="MLB_STATS_API", external_id=GAME_PK),
    ])
    bundle = ShadowForecastBundle(
        bundle_sha256="a" * 64, bundle_id="mlb-v18-daily-handoff-test",
        logical_uri="test://bundle/v18", source_fixture_id="fixture-v18",
        slate_date=START.date(), league="MLB", sport="MLB",
        status="RESEARCH_SHADOW", research_shadow_only=True,
        generated_at=START - timedelta(hours=6),
        forecast_count=1, quote_count=0, candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    return {"session": session, "game": game, "home": home, "away": away,
            "league": league, "bundle": bundle}


def forecast(
    world, *, published: datetime, version: int = 1,
    evidence_cutoff: datetime | None = None,
    distributions: dict | None = None, identity: str | None = None,
    uncertainty: dict | None = None,
) -> ShadowForecast:
    session = world["session"]
    row = ShadowForecast(
        bundle_id=world["bundle"].id,
        forecast_id=identity or f"mlb-v18-daily-{version}",
        engine_id=ENGINE, engine_version=ENGINE_VERSION,
        model_artifact_sha256="4" * 64,
        simulator_id="MLB_V18_HGB_PA", simulator_version="1", simulator_seed=18,
        event_id="v18-event", source_event_id=GAME_PK,
        event_date=START.date(), season=2026,
        canonical_game_id=world["game"].id,
        home_team_id="COL", away_team_id="MIA",
        snapshot_type="PROSPECTIVE", snapshot_version=version,
        is_ready=True, readiness_score=1.0, readiness_threshold=1.0,
        uncertainty=uncertainty if uncertainty is not None else {"simulationCount": 5000},
        readiness_detail={}, status="PROSPECTIVE_RESEARCH_SHADOW",
        scheduled_start_utc=START,
        available_as_of_utc=published,
        source_cutoff_utc=evidence_cutoff if evidence_cutoff is not None
        else published - timedelta(minutes=8),
        forecast_cutoff_utc=published,
        full_home_win_probability=HOME_WIN, full_away_win_probability=AWAY_WIN,
        full_expected_home_score=HOME_RUNS, full_expected_away_score=AWAY_RUNS,
        distributions=distributions if distributions is not None else dict(MARGINALS),
    )
    session.add(row)
    session.flush()
    return row


def observe(
    world, *, status: str, home: int | None, away: int | None,
    checksum: str | None = "obs-a", source: str = "MLB_STATS_API",
    observed: datetime | None = None, project_onto_game: bool = True,
) -> GameStateObservation:
    session, game = world["session"], world["game"]
    observed = observed or START + timedelta(hours=3)
    row = GameStateObservation(
        game_id=game.id, home_team_id=world["home"].id, away_team_id=world["away"].id,
        scheduled_at=START, status=status, home_score=home, away_score=away,
        observed_at=observed, available_as_of=observed,
        source=source, source_mode="LIVE", payload_checksum=checksum,
    )
    session.add(row)
    if project_onto_game and status == GameStatus.FINAL.value:
        game.status = GameStatus.FINAL
        game.home_score, game.away_score = home, away
    session.flush()
    return row


def evaluations(session: Session) -> list[ResearchForecastEvaluation]:
    return list(session.scalars(select(ResearchForecastEvaluation)))


# -- what counts as scoreable final evidence --------------------------------

def test_a_live_observation_is_not_a_final(world):
    """Scores exist, the game is still being played. Nothing is scored."""

    forecast(world, published=START - timedelta(hours=2))
    observe(world, status=GameStatus.LIVE.value, home=4, away=2)

    outcome = ForecastEvaluationService(world["session"]).evaluate_game(
        canonical_game_id=world["game"].id)

    assert outcome["scored"] == 0
    assert outcome["skipped"] == "NO_AUTHORITATIVE_FINAL"
    assert evaluations(world["session"]) == []


def test_a_bare_final_flag_on_the_game_row_does_not_bypass_the_evidence_rule(world):
    """games.status FINAL with scores, and no observation behind it."""

    forecast(world, published=START - timedelta(hours=2))
    game = world["game"]
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = FINAL_HOME, FINAL_AWAY
    game.final_at = START + timedelta(hours=3)
    world["session"].flush()

    outcome = ForecastEvaluationService(world["session"]).evaluate_game(
        canonical_game_id=world["game"].id)

    assert outcome["scored"] == 0
    assert outcome["skipped"] == "NO_AUTHORITATIVE_FINAL"
    assert evaluations(world["session"]) == []


def test_a_final_observation_without_scores_is_not_scoreable(world):
    """Status says final; the payload carried no runs. Refused, not defaulted to nil."""

    forecast(world, published=START - timedelta(hours=2))
    observe(world, status=GameStatus.FINAL.value, home=None, away=None,
            project_onto_game=False)

    outcome = ForecastEvaluationService(world["session"]).evaluate_game(
        canonical_game_id=world["game"].id)

    assert outcome["scored"] == 0
    assert outcome["skipped"] == "NO_AUTHORITATIVE_FINAL"


def test_a_forecast_published_after_first_pitch_is_excluded_with_its_reason(world):
    forecast(world, published=START + timedelta(minutes=1))
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY)

    outcome = ForecastEvaluationService(world["session"]).evaluate_game(
        canonical_game_id=world["game"].id)

    assert outcome["scored"] == 0
    assert evaluations(world["session"]) == []


# -- the real scoring path ---------------------------------------------------

def test_an_eligible_v18_forecast_is_scored_against_the_live_final(world):
    session = world["session"]
    row = forecast(world, published=START - timedelta(hours=2))
    observation = observe(world, status=GameStatus.FINAL.value,
                          home=FINAL_HOME, away=FINAL_AWAY)

    outcome = ForecastEvaluationService(session).evaluate_game(
        canonical_game_id=world["game"].id)

    assert outcome["scored"] == 1
    written = evaluations(session)
    assert len(written) == 1
    score = written[0]
    # Labelled as baseball, scored by the MLB scoring version, and tied back to
    # both the forecast and the exact observation that produced the label.
    assert (score.sport, score.league_code) == ("BASEBALL", "MLB")
    assert score.scoring_version == MLB_SCORING_VERSION
    assert score.forecast_identity == row.forecast_id
    assert score.shadow_forecast_id == row.id
    assert score.result_identity == f"MLB_STATS_API:{observation.payload_checksum}"
    assert score.result_tier == 2
    assert score.is_current is True
    assert (score.home_score, score.away_score) == (FINAL_HOME, FINAL_AWAY)
    assert score.winner_correct is True
    assert score.actual_margin == FINAL_HOME - FINAL_AWAY
    # Marginals give real CRPS; the energy score needs the joint and says so.
    assert score.distribution_available is True
    assert score.crps_home is not None and score.crps_away is not None
    assert score.crps_margin is not None and score.crps_total is not None
    assert score.energy_score is None
    assert "independent" in score.interval_coverage["energy_score_unavailable_reason"]
    assert score.distribution_draws == 5000


def test_a_forecast_with_no_published_distribution_still_scores_the_outcome(world):
    session = world["session"]
    forecast(world, published=START - timedelta(hours=2), distributions={})
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY)

    ForecastEvaluationService(session).evaluate_game(canonical_game_id=world["game"].id)

    score = evaluations(session)[0]
    assert score.log_loss is not None and score.brier_score is not None
    assert score.distribution_available is False
    assert score.distribution_unavailable_reason


# -- idempotent retry --------------------------------------------------------

def test_running_the_job_twice_writes_one_row(world):
    session = world["session"]
    forecast(world, published=START - timedelta(hours=2))
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY)
    service = ForecastEvaluationService(session)

    first = service.evaluate_game(canonical_game_id=world["game"].id)
    second = service.evaluate_game(canonical_game_id=world["game"].id)

    assert first["scored"] == 1
    assert second["scored"] == 0
    assert second["reused"] == 1
    assert session.scalar(select(func.count()).select_from(ResearchForecastEvaluation)) == 1


def test_repeated_forecast_snapshots_of_one_game_are_one_score(world):
    """Re-importing the same game's forecast does not multiply the scores."""

    session = world["session"]
    forecast(world, published=START - timedelta(hours=5), version=1, identity="v18-a")
    forecast(world, published=START - timedelta(hours=2), version=2, identity="v18-b")
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY)

    outcome = ForecastEvaluationService(session).evaluate_game(
        canonical_game_id=world["game"].id)

    assert outcome["scored"] == 1
    # The one an observer would have been looking at at first pitch.
    assert evaluations(session)[0].forecast_identity == "v18-b"


# -- correction preserves the earlier evidence -------------------------------

def test_a_corrected_final_scores_beside_the_original_and_links_to_it(world):
    session = world["session"]
    forecast(world, published=START - timedelta(hours=2))
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY,
            checksum="obs-a")
    service = ForecastEvaluationService(session)
    service.evaluate_game(canonical_game_id=world["game"].id)

    # The provider corrects the score. New payload, new checksum, later.
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY + 1,
            checksum="obs-b", observed=START + timedelta(hours=6))
    service.evaluate_game(canonical_game_id=world["game"].id)

    written = evaluations(session)
    assert len(written) == 2, "the original verdict must survive the correction"
    current = [row for row in written if row.is_current]
    superseded = [row for row in written if not row.is_current]
    assert len(current) == 1 and len(superseded) == 1
    assert current[0].away_score == FINAL_AWAY + 1
    assert superseded[0].away_score == FINAL_AWAY
    assert superseded[0].superseded_by_id == current[0].id
    assert superseded[0].supersede_reason == "RESULT_CORRECTED"
    assert superseded[0].superseded_at is not None


def test_a_better_provenanced_confirmation_does_not_add_a_scored_game(world):
    """A stronger source confirming the same score is an upgrade, not a game."""

    session = world["session"]
    forecast(world, published=START - timedelta(hours=2))
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY,
            checksum="obs-operator", source="OPERATOR_ENTERED")
    service = ForecastEvaluationService(session)
    service.evaluate_game(canonical_game_id=world["game"].id)

    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY,
            checksum="obs-api", source="MLB_STATS_API",
            observed=START + timedelta(hours=5))
    service.evaluate_game(canonical_game_id=world["game"].id)

    written = evaluations(session)
    assert len(written) == 2
    current = [row for row in written if row.is_current]
    assert len(current) == 1
    assert current[0].result_source == "MLB_STATS_API"
    assert current[0].result_tier == 2
    superseded = [row for row in written if not row.is_current][0]
    assert superseded.supersede_reason == "PROVENANCE_UPGRADE"
    # One game, still.
    assert len({row.canonical_game_id for row in written if row.is_current}) == 1


# -- publication clock, recorded but never loosened --------------------------

def test_a_distinct_publication_clock_is_recorded_as_verified(world):
    session = world["session"]
    published = START - timedelta(hours=2)
    forecast(world, published=published, evidence_cutoff=published - timedelta(minutes=8))
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY)

    ForecastEvaluationService(session).evaluate_game(canonical_game_id=world["game"].id)

    clock = evaluations(session)[0].detail["publication_clock"]
    assert clock["classification"] == CLOCK_VERIFIED
    assert clock["verified"] is True


def test_a_collapsed_publication_clock_is_scored_but_marked_unresolved(world):
    """Equal timestamps neither prove nor disprove; the row says which it is."""

    session = world["session"]
    published = START - timedelta(hours=2)
    forecast(world, published=published, evidence_cutoff=published)
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY)

    outcome = ForecastEvaluationService(session).evaluate_game(
        canonical_game_id=world["game"].id)

    assert outcome["scored"] == 1, "the eligibility rule is unchanged"
    clock = evaluations(session)[0].detail["publication_clock"]
    assert clock["classification"] == CLOCK_UNRESOLVED
    assert clock["verified"] is False
    assert outcome["records"][0]["publication_clock"] == CLOCK_UNRESOLVED


# -- bounds applied before the outcome is read -------------------------------

def test_a_permanently_excluded_game_is_never_scored(world):
    session = world["session"]
    forecast(world, published=START - timedelta(hours=2))
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY)
    # Re-point this game's canonical id at a protected gamePk.
    mapping = session.scalar(select(ExternalIdMapping).where(
        ExternalIdMapping.entity_id == world["game"].id))
    mapping.external_id = "824235"
    session.flush()

    summary = ForecastEvaluationService(session).evaluate_league(
        league_code="MLB", earliest_start=START - timedelta(days=30),
        excluded_source_event_ids=frozenset({"823343", "824235", "822693"}),
        require_shadow_forecast=True,
    )

    assert summary["final_games"] == 0
    assert summary["scored"] == 0
    assert evaluations(session) == []


def test_a_game_before_the_floor_is_not_in_the_population(world):
    session = world["session"]
    forecast(world, published=START - timedelta(hours=2))
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY)

    summary = ForecastEvaluationService(session).evaluate_league(
        league_code="MLB", earliest_start=START + timedelta(days=1),
        require_shadow_forecast=True,
    )

    assert summary["final_games"] == 0
    assert evaluations(session) == []


def test_scoring_creates_no_decision_position_or_settlement(world):
    """The authorization covers measurement only. Confirm it stays measurement."""

    from app.models import OfficialAspPosition, ShadowBoardDecision

    session = world["session"]
    forecast(world, published=START - timedelta(hours=2))
    observe(world, status=GameStatus.FINAL.value, home=FINAL_HOME, away=FINAL_AWAY)

    summary = ForecastEvaluationService(session).evaluate_league(
        league_code="MLB", earliest_start=START - timedelta(days=30),
        require_shadow_forecast=True,
    )

    assert summary["scored"] == 1
    assert summary["provider_calls"] == 0
    assert summary["models_executed"] == 0
    assert summary["positions_created"] == 0
    assert summary["official_decisions_created"] == 0
    assert session.scalar(select(func.count()).select_from(ShadowBoardDecision)) == 0
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 0
