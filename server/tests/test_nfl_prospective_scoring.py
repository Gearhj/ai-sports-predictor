"""Prospective NFL scoring — the rules that decide whether a number means anything.

The arithmetic is checked against the worked NE @ SEA example, but the tests
that matter more are the ones about WHICH forecast gets scored and WHEN a
second evaluation is a duplicate:

  * a forecast published after kickoff is not a prospective forecast, and no
    amount of correctness makes it one;
  * ten stored snapshots of one game's forecast are one game;
  * two models are never averaged into one number;
  * a corrected final scores beside the original rather than erasing it;
  * a market quote observed after kickoff is not a pre-kickoff quote.

Nothing here calls a provider, executes a model, or creates a decision.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStateObservation, GameStatus, GameTeam, League, Market, MarketOutcome,
    MarketType, OddsSnapshot, ResearchForecastEvaluation, Season, ShadowForecast,
    ShadowForecastBundle, Sport, Sportsbook, Team,
)
from app.services import forecast_scoring as scoring
from app.services.forecast_evaluation import (
    ForecastEvaluationService, PUBLISHED_AFTER_KICKOFF, SUPERSEDED,
)

KICKOFF = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)

# The published NE @ SEA CURRENT v9 forecast, and the final it is scored against.
M1_HOME_WIN = 0.5736      # Seattle, the home side
M1_AWAY_WIN = 0.3966      # New England
M1_TIE = 0.0298
M1_HOME_SCORE = 22.7482
M1_AWAY_SCORE = 19.9418
FINAL_HOME, FINAL_AWAY = 13, 10


@pytest.fixture
def world(session: Session):
    sport = Sport(code="FOOTBALL", name="Football")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="NFL", name="NFL")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=KICKOFF - timedelta(days=30), ends_at=KICKOFF + timedelta(days=150))
    home = Team(league_id=league.id, name="Seattle Seahawks", abbreviation="SEA")
    away = Team(league_id=league.id, name="New England Patriots", abbreviation="NE")
    session.add_all([season, home, away])
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=KICKOFF,
                status=GameStatus.SCHEDULED, source_mode="RESEARCH_SHADOW")
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    bundle = ShadowForecastBundle(
        bundle_sha256="0" * 64, bundle_id="b1", logical_uri="test://bundle/1",
        source_fixture_id="fixture-1", slate_date=KICKOFF.date(), league="NFL",
        sport="FOOTBALL", status="RESEARCH_SHADOW", generated_at=KICKOFF - timedelta(days=2),
        forecast_count=1, quote_count=0, candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    return {"session": session, "game": game, "home": home, "away": away,
            "league": league, "bundle": bundle}


def forecast(
    world, *, published: datetime, version: int, snapshot_type: str = "CURRENT",
    engine: str = "NFL_M1_XGBOOST", engine_version: str = "NFL_M1_2026_RESEARCH_SHADOW_V1",
    home_win: float = M1_HOME_WIN, away_win: float = M1_AWAY_WIN,
    home_score: float | None = M1_HOME_SCORE, away_score: float | None = M1_AWAY_SCORE,
    distributions: dict | None = None, identity: str | None = None,
) -> ShadowForecast:
    session = world["session"]
    row = ShadowForecast(
        bundle_id=world["bundle"].id,
        forecast_id=identity or f"forecast:{engine}:{snapshot_type}:{version}",
        engine_id=engine, engine_version=engine_version,
        model_artifact_sha256="1" * 64,
        simulator_id="sim", simulator_version="1", simulator_seed=731001,
        event_id="2026_01_NE_SEA", source_event_id="2026_01_NE_SEA",
        event_date=KICKOFF.date(), season=2026, season_type="REG", week=1,
        canonical_game_id=world["game"].id,
        home_team_id=world["home"].abbreviation, away_team_id=world["away"].abbreviation,
        snapshot_type=snapshot_type, snapshot_version=version,
        is_ready=True, readiness_score=1.0, readiness_threshold=0.5,
        uncertainty={}, readiness_detail={},
        status="RESEARCH_SHADOW",
        scheduled_start_utc=KICKOFF,
        available_as_of_utc=published, source_cutoff_utc=published - timedelta(days=10),
        forecast_cutoff_utc=published,
        full_home_win_probability=home_win, full_away_win_probability=away_win,
        full_expected_home_score=home_score, full_expected_away_score=away_score,
        distributions=distributions or {"tie_probability": M1_TIE},
    )
    session.add(row)
    session.flush()
    return row


def record_final(world, *, home: int = FINAL_HOME, away: int = FINAL_AWAY,
                 checksum: str = "final-a", source: str = "OPERATOR_ENTERED",
                 observed: datetime | None = None) -> None:
    session, game = world["session"], world["game"]
    observed = observed or KICKOFF + timedelta(hours=3)
    session.add(GameStateObservation(
        game_id=game.id, home_team_id=world["home"].id, away_team_id=world["away"].id,
        scheduled_at=KICKOFF, status=GameStatus.FINAL.value,
        home_score=home, away_score=away, final_at=observed,
        observed_at=observed, available_as_of=observed,
        source=source, source_mode="RESEARCH_SHADOW", payload_checksum=checksum,
    ))
    game.status = GameStatus.FINAL
    game.home_score = home
    game.away_score = away
    game.final_at = observed
    session.flush()


# -- the worked example -----------------------------------------------------

def test_the_first_result_reproduces_every_published_number(world):
    """NE @ SEA, recomputed from the stored forecast and the stored final."""

    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world)

    ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    row = world["session"].scalars(select(ResearchForecastEvaluation)).one()

    assert row.actual_outcome == "HOME" and row.predicted_outcome == "HOME"
    assert row.winner_correct is True
    assert row.probability_of_actual == pytest.approx(0.5736, abs=1e-9)
    assert row.log_loss == pytest.approx(0.555823, abs=1e-6)
    assert row.brier_score == pytest.approx(0.339997, abs=1e-6)
    assert row.team_score_mae == pytest.approx(9.845, abs=1e-9)
    assert row.predicted_margin == pytest.approx(2.8064, abs=1e-9)
    assert row.actual_margin == 3
    assert abs(row.margin_error) == pytest.approx(0.1936, abs=1e-9)
    assert row.predicted_total == pytest.approx(42.69, abs=1e-9)
    assert row.actual_total == 23
    assert row.total_error == pytest.approx(19.69, abs=1e-9), "an overprediction, positive"


def test_the_signed_error_convention_is_predicted_minus_actual(world):
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world)
    ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    row = world["session"].scalars(select(ResearchForecastEvaluation)).one()
    # 22.75 predicted against 13 actual is an overprediction, so positive.
    assert row.home_score_error > 0
    # 2.81 predicted against a 3-point actual margin is an underprediction.
    assert row.margin_error < 0
    assert (row.detail or {})["signed_error_convention"] == "predicted minus actual"


# -- cutoff selection -------------------------------------------------------

def test_a_forecast_published_after_kickoff_is_never_scored(world):
    """The rule the whole measure rests on."""

    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    forecast(world, published=KICKOFF + timedelta(seconds=1), version=10)
    record_final(world)

    service = ForecastEvaluationService(world["session"])
    frozen = service.freeze_eligible(canonical_game_id=world["game"].id)
    selection = next(iter(frozen.values()))
    assert selection.row.snapshot_version == 9
    reasons = {entry["reason"] for entry in selection.excluded}
    assert PUBLISHED_AFTER_KICKOFF in reasons


def test_a_forecast_published_exactly_at_kickoff_is_excluded(world):
    forecast(world, published=KICKOFF - timedelta(hours=1), version=1)
    forecast(world, published=KICKOFF, version=2)
    frozen = ForecastEvaluationService(world["session"]).freeze_eligible(
        canonical_game_id=world["game"].id)
    selection = next(iter(frozen.values()))
    assert selection.row.snapshot_version == 1


def test_ten_snapshots_of_one_forecast_are_one_game(world):
    """Repeated snapshots are versions of one opinion, not ten observations."""

    for version in range(1, 11):
        forecast(world, published=KICKOFF - timedelta(days=11) + timedelta(hours=version), version=version)
    record_final(world)

    ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    rows = list(world["session"].scalars(select(ResearchForecastEvaluation)))
    assert len(rows) == 1
    assert rows[0].snapshot_version == 10
    assert (rows[0].detail or {})["snapshots_considered"] == 10
    superseded = [entry for entry in (rows[0].detail or {})["excluded"]
                  if entry["reason"] == SUPERSEDED]
    assert len(superseded) == 9


def test_the_latest_pre_kickoff_snapshot_wins_even_out_of_version_order(world):
    forecast(world, published=KICKOFF - timedelta(hours=2), version=3)
    forecast(world, published=KICKOFF - timedelta(hours=40), version=99)
    frozen = ForecastEvaluationService(world["session"]).freeze_eligible(
        canonical_game_id=world["game"].id)
    assert next(iter(frozen.values())).row.snapshot_version == 3


def test_the_result_cannot_influence_which_forecast_is_frozen(world):
    """Selection that can see the score is retrodiction, not forecasting."""

    forecast(world, published=KICKOFF - timedelta(hours=25), version=9, home_win=0.90, away_win=0.08)
    forecast(world, published=KICKOFF - timedelta(hours=2), version=10, home_win=0.20, away_win=0.78)
    record_final(world)
    service = ForecastEvaluationService(world["session"])
    frozen = service.freeze_eligible(canonical_game_id=world["game"].id)
    # v10 is later and much WORSE against this result; it is still the one an
    # observer would have seen at kickoff, so it is the one scored.
    assert next(iter(frozen.values())).row.snapshot_version == 10


# -- idempotence and identity ----------------------------------------------

def test_rerunning_creates_no_duplicate(world):
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world)
    service = ForecastEvaluationService(world["session"])
    first = service.evaluate_game(canonical_game_id=world["game"].id)
    second = service.evaluate_game(canonical_game_id=world["game"].id)
    third = service.evaluate_game(canonical_game_id=world["game"].id)
    assert (first["scored"], second["scored"], third["scored"]) == (1, 0, 0)
    assert (second["reused"], third["reused"]) == (1, 1)
    assert world["session"].scalar(
        select(func.count()).select_from(ResearchForecastEvaluation)) == 1


def test_a_corrected_final_scores_beside_the_original_rather_than_erasing_it(world):
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world, checksum="final-a")
    service = ForecastEvaluationService(world["session"])
    service.evaluate_game(canonical_game_id=world["game"].id)

    # A correction: same game, different score, known to us LATER. The
    # knowledge time is what makes it a correction rather than a second
    # contradictory claim about the same instant.
    record_final(world, home=14, away=10, checksum="final-b",
                 observed=KICKOFF + timedelta(days=2))
    service.evaluate_game(canonical_game_id=world["game"].id)

    rows = list(world["session"].scalars(
        select(ResearchForecastEvaluation).order_by(ResearchForecastEvaluation.home_score)))
    assert len(rows) == 2, "the first verdict is still what we said at the time"
    assert [row.home_score for row in rows] == [13, 14]
    assert len({row.result_identity for row in rows}) == 2
    # ...but only the corrected one counts, and the original says why it does not.
    original, corrected = rows
    assert corrected.is_current is True
    assert original.is_current is False
    assert original.supersede_reason == "RESULT_CORRECTED"
    assert original.superseded_by_id == corrected.id
    assert original.superseded_at is not None


def test_a_score_with_no_observation_behind_it_is_not_scoreable(world):
    """A number on the game row with no provenance has no identity to score."""

    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    game = world["game"]
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = 13, 10
    world["session"].flush()

    outcome = ForecastEvaluationService(world["session"]).evaluate_game(
        canonical_game_id=game.id)
    assert outcome["skipped"] == "NO_AUTHORITATIVE_FINAL"
    assert world["session"].scalar(
        select(func.count()).select_from(ResearchForecastEvaluation)) == 0


def test_the_result_source_travels_into_the_evaluation(world):
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world, source="OPERATOR_ENTERED", checksum="op-1")
    ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    row = world["session"].scalars(select(ResearchForecastEvaluation)).one()
    assert row.result_source == "OPERATOR_ENTERED"
    assert row.result_identity.startswith("OPERATOR_ENTERED:")


# -- model separation -------------------------------------------------------

def test_two_models_are_scored_separately_and_never_pooled(world):
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9,
             engine="NFL_M1_XGBOOST", identity="m1-v9")
    forecast(world, published=KICKOFF - timedelta(hours=20), version=1,
             engine="NFL_CURRENT_STATE_RESIDUAL_V2_REPAIRED",
             engine_version="NFL_UNCERTAINTY_MEAN_SEPARATION_REPAIR_V1",
             home_win=0.42, away_win=0.55, identity="v2-v1")
    record_final(world)

    ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    rows = {row.engine_id: row for row in world["session"].scalars(select(ResearchForecastEvaluation))}
    assert set(rows) == {"NFL_M1_XGBOOST", "NFL_CURRENT_STATE_RESIDUAL_V2_REPAIRED"}
    assert rows["NFL_M1_XGBOOST"].winner_correct is True
    assert rows["NFL_CURRENT_STATE_RESIDUAL_V2_REPAIRED"].winner_correct is False
    assert rows["NFL_M1_XGBOOST"].log_loss != rows["NFL_CURRENT_STATE_RESIDUAL_V2_REPAIRED"].log_loss


def test_one_model_at_two_versions_is_two_identities(world):
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9,
             engine_version="V1", identity="a")
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9,
             engine_version="V2", identity="b")
    record_final(world)
    ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    versions = {row.engine_version for row in
                world["session"].scalars(select(ResearchForecastEvaluation))}
    assert versions == {"V1", "V2"}


# -- distributions ----------------------------------------------------------

def test_a_forecast_with_no_distribution_reports_the_metric_unavailable(world):
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world)
    ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    row = world["session"].scalars(select(ResearchForecastEvaluation)).one()
    assert row.distribution_available is False
    assert row.crps_margin is None and row.energy_score is None
    assert "measure nothing" in row.distribution_unavailable_reason


def test_an_authentic_joint_distribution_is_scored_exactly(world):
    """A two-cell joint whose CRPS can be worked out on paper."""

    joint = {
        "tie_probability": 0.0,
        "joint_score_distribution": [
            {"home_score": 10, "away_score": 10, "probability": 0.5},
            {"home_score": 20, "away_score": 10, "probability": 0.5},
        ],
    }
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9,
             home_win=0.5, away_win=0.5, distributions=joint)
    record_final(world, home=20, away=10, checksum="exact")
    ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    row = world["session"].scalars(select(ResearchForecastEvaluation)).one()
    assert row.distribution_available is True
    # Home CDF is 0 below 10, 0.5 on [10, 19], 1 from 20. Observed 20, so the
    # ten integer steps 10..19 each contribute 0.25.
    assert row.crps_home == pytest.approx(2.5, abs=1e-9)
    # The away marginal is a point mass on the observed value.
    assert row.crps_away == pytest.approx(0.0, abs=1e-12)
    # Energy: 0.5*|(10,10)-(20,10)| + 0.5*0 - 0.5*(2*0.25*10) = 5 - 2.5.
    assert row.energy_score == pytest.approx(2.5, abs=1e-9)


def test_interval_containment_is_reported_per_game_not_as_a_rate(world):
    joint = {
        "tie_probability": 0.0,
        "joint_score_distribution": [
            {"home_score": home, "away_score": 10, "probability": 0.1}
            for home in range(10, 20)
        ],
    }
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9,
             home_win=0.5, away_win=0.5, distributions=joint)
    record_final(world, home=15, away=10, checksum="cover")
    ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    row = world["session"].scalars(select(ResearchForecastEvaluation)).one()
    coverage = row.interval_coverage
    assert coverage["margin"]["90"]["contains"] is True
    assert "hits and misses" in coverage["note"]


# -- market point-in-time ---------------------------------------------------

def quote(world, *, book: Sportsbook, outcome: MarketOutcome, american: int, at: datetime,
          point: float | None = None) -> None:
    world["session"].add(OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=at, available_as_of=at,
        american_odds=american, point=point,
        decimal_odds=1.9, implied_probability=0.52, source="THE_ODDS_API", source_mode="LIVE",
    ))
    world["session"].flush()


def moneyline(world) -> tuple[MarketOutcome, MarketOutcome]:
    session = world["session"]
    market = Market(game_id=world["game"].id, market_type=MarketType.MONEYLINE, period="full_game")
    session.add(market)
    session.flush()
    home = MarketOutcome(market_id=market.id, outcome_key="HOME:None", label="Seattle Seahawks",
                         participant_team_id=world["home"].id)
    away = MarketOutcome(market_id=market.id, outcome_key="AWAY:None", label="New England Patriots",
                         participant_team_id=world["away"].id)
    session.add_all([home, away])
    session.flush()
    return home, away


def test_a_game_with_no_persisted_quote_reports_the_market_unavailable(world):
    """NE @ SEA in the real database. No quote was ever collected for it."""

    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world)
    ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    row = world["session"].scalars(select(ResearchForecastEvaluation)).one()
    assert row.market_available is False
    assert "None is reconstructed" in row.market_unavailable_reason
    assert row.market_evidence is None


def test_a_quote_observed_after_kickoff_is_not_a_pre_kickoff_quote(world):
    session = world["session"]
    book = Sportsbook(name="DraftKings", key="draftkings")
    session.add(book)
    session.flush()
    home_outcome, away_outcome = moneyline(world)
    quote(world, book=book, outcome=home_outcome, american=-150, at=KICKOFF + timedelta(minutes=5))
    quote(world, book=book, outcome=away_outcome, american=130, at=KICKOFF + timedelta(minutes=5))

    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world)
    ForecastEvaluationService(session).evaluate_game(canonical_game_id=world["game"].id)
    row = session.scalars(select(ResearchForecastEvaluation)).one()
    assert row.market_available is False


def test_the_last_pre_kickoff_quote_is_used_and_is_not_called_a_closing_line(world):
    session = world["session"]
    book = Sportsbook(name="DraftKings", key="draftkings")
    session.add(book)
    session.flush()
    home_outcome, away_outcome = moneyline(world)
    # Early quote, then a later one; the later one wins.
    quote(world, book=book, outcome=home_outcome, american=-200, at=KICKOFF - timedelta(days=5))
    quote(world, book=book, outcome=away_outcome, american=170, at=KICKOFF - timedelta(days=5))
    quote(world, book=book, outcome=home_outcome, american=-150, at=KICKOFF - timedelta(hours=2))
    quote(world, book=book, outcome=away_outcome, american=130, at=KICKOFF - timedelta(hours=2))

    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world)
    ForecastEvaluationService(session).evaluate_game(canonical_game_id=world["game"].id)
    row = session.scalars(select(ResearchForecastEvaluation)).one()

    assert row.market_available is True
    evidence = row.market_evidence
    assert evidence["basis"] == "LAST_ELIGIBLE_RECORDED_PRE_KICKOFF_QUOTE"
    assert "Not a closing line" in evidence["basis_note"]
    market = evidence["markets"]["MONEYLINE"]
    assert market["home_american"] == -150 and market["away_american"] == 130
    expected_home, _ = scoring.no_vig_two_way(-150, 130)
    assert market["no_vig_home_probability"] == pytest.approx(expected_home, abs=1e-12)
    assert market["scored"] is True
    assert market["log_loss"] == pytest.approx(scoring.two_way_log_loss(expected_home, "HOME"), abs=1e-12)


def test_two_books_never_form_one_quote(world):
    session = world["session"]
    first = Sportsbook(name="DraftKings", key="draftkings")
    second = Sportsbook(name="FanDuel", key="fanduel")
    session.add_all([first, second])
    session.flush()
    home_outcome, away_outcome = moneyline(world)
    quote(world, book=first, outcome=home_outcome, american=-150, at=KICKOFF - timedelta(hours=2))
    quote(world, book=second, outcome=away_outcome, american=130, at=KICKOFF - timedelta(hours=2))

    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world)
    ForecastEvaluationService(session).evaluate_game(canonical_game_id=world["game"].id)
    row = session.scalars(select(ResearchForecastEvaluation)).one()
    assert row.market_available is False
    assert "two-sided price at one book" in row.market_unavailable_reason


def test_a_tie_leaves_a_two_way_market_unscored_rather_than_forced(world):
    session = world["session"]
    book = Sportsbook(name="DraftKings", key="draftkings")
    session.add(book)
    session.flush()
    home_outcome, away_outcome = moneyline(world)
    quote(world, book=book, outcome=home_outcome, american=-150, at=KICKOFF - timedelta(hours=2))
    quote(world, book=book, outcome=away_outcome, american=130, at=KICKOFF - timedelta(hours=2))

    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world, home=17, away=17, checksum="tie")
    ForecastEvaluationService(session).evaluate_game(canonical_game_id=world["game"].id)
    row = session.scalars(select(ResearchForecastEvaluation)).one()
    assert row.actual_outcome == "TIE"
    market = row.market_evidence["markets"]["MONEYLINE"]
    assert market["scored"] is False
    assert "no tie price" in market["reason"]


# -- the job's own guarantees ----------------------------------------------

def test_the_league_run_reports_zero_side_effects(world):
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world)
    summary = ForecastEvaluationService(world["session"]).evaluate_league(league_code="NFL")
    assert summary["provider_calls"] == 0
    assert summary["models_executed"] == 0
    assert summary["positions_created"] == 0
    assert summary["official_decisions_created"] == 0
    assert summary["scored"] == 1


def test_a_game_without_a_final_is_skipped_with_a_reason(world):
    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    summary = ForecastEvaluationService(world["session"]).evaluate_league(league_code="NFL")
    assert summary["final_games"] == 0
    assert summary["scored"] == 0


def handicap(world, *, market_type: MarketType, home_key: str, away_key: str):
    session = world["session"]
    market = Market(game_id=world["game"].id, market_type=market_type, period="full_game")
    session.add(market)
    session.flush()
    home = MarketOutcome(market_id=market.id, outcome_key=home_key, label=home_key)
    away = MarketOutcome(market_id=market.id, outcome_key=away_key, label=away_key)
    session.add_all([home, away])
    session.flush()
    return home, away


def test_a_spread_and_a_total_are_graded_separately_from_the_moneyline(world):
    """The provider stores these as 'spreads'/'totals'; both must still resolve."""

    session = world["session"]
    book = Sportsbook(name="DraftKings", key="draftkings")
    session.add(book)
    session.flush()
    spread_home, spread_away = handicap(
        world, market_type=MarketType.SPREAD, home_key="HOME:-2.5", away_key="AWAY:2.5")
    quote(world, book=book, outcome=spread_home, american=-110,
          at=KICKOFF - timedelta(hours=3), point=-2.5)
    quote(world, book=book, outcome=spread_away, american=-110,
          at=KICKOFF - timedelta(hours=3), point=2.5)
    total_over, total_under = handicap(
        world, market_type=MarketType.TOTAL, home_key="OVER:44.5", away_key="UNDER:44.5")
    quote(world, book=book, outcome=total_over, american=-110,
          at=KICKOFF - timedelta(hours=3), point=44.5)
    quote(world, book=book, outcome=total_under, american=-110,
          at=KICKOFF - timedelta(hours=3), point=44.5)

    forecast(world, published=KICKOFF - timedelta(hours=25), version=9)
    record_final(world)
    ForecastEvaluationService(session).evaluate_game(canonical_game_id=world["game"].id)
    row = session.scalars(select(ResearchForecastEvaluation)).one()

    markets = row.market_evidence["markets"]
    # Seattle by 3 against a 2.5-point home handicap: the home side covers.
    assert markets["SPREAD"]["home_handicap"] == -2.5
    assert markets["SPREAD"]["grade"] == "HOME_COVER"
    # 23 points against a 44.5 total: under, by a distance.
    assert markets["TOTAL"]["line"] == 44.5
    assert markets["TOTAL"]["grade"] == "UNDER"
    # And each market keeps its own identity rather than collapsing together.
    assert "MONEYLINE" not in markets
