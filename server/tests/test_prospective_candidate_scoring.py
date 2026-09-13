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
from app.shadow_import.mlb_ensemble_candidate_read import (
    candidate_cohort, candidate_comparison,
)
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


def forecast(world, *, engine, version, identity, status, home_win, published,
             lineage=None, scheduled=None):
    # Built complete, never patched after the flush: ShadowForecast is
    # append-only and the immutability guard refuses a later edit.
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
        scheduled_start_utc=scheduled or START,
        available_as_of_utc=published,
        source_cutoff_utc=published - timedelta(minutes=8),
        forecast_cutoff_utc=published,
        full_home_win_probability=home_win,
        full_away_win_probability=round(1 - home_win, 4),
        full_expected_home_score=5.03, full_expected_away_score=4.03,
        distributions={}, uncertainty={"simulationCount": 5000}, readiness_detail={},
        lineage_detail=lineage or {},
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

# -- three arms on one game, and what the cohort is allowed to average ------
#
# The imported package publishes two configurations of one engine family for the
# same game: a transition arm whose component ran, and a survival arm that fell
# back and reused the baseline's own draws. The scorer must evaluate all three
# identities against ONE final, and the cohort must average only the arm that
# actually measured something.


def arm(world, *, version, identity, home_win, supported, baseline_identity,
        artifact_type="PROSPECTIVE", scheduled=None):
    """A candidate arm carrying the lineage the cohort reads."""

    return forecast(
        world, engine=CANDIDATE_ENGINE, version=version, identity=identity,
        status="PROSPECTIVE_RESEARCH_SHADOW", home_win=home_win,
        published=START - timedelta(hours=2),
        scheduled=scheduled,
        lineage={
            "artifactType": artifact_type,
            "baselineForecastId": baseline_identity,
            "componentTrialSupported": supported,
            "fallbackToOriginalDraws": not supported,
            "configurationId": version,
        },
    )


def _three_arms(world, *, survival_home_win=0.6138):
    base = forecast(
        world, engine=BASELINE_ENGINE, version="v18-a", identity="v18-1",
        status="PROSPECTIVE_RESEARCH_SHADOW", home_win=0.6138,
        published=START - timedelta(hours=3),
    )
    transition = arm(world, version="mlb-v18-three-arm-transition-v1",
                     identity="cand-transition", home_win=0.6312, supported=True,
                     baseline_identity="v18-1")
    survival = arm(world, version="mlb-v18-three-arm-duration-v1",
                   identity="cand-survival", home_win=survival_home_win,
                   supported=False, baseline_identity="v18-1")
    return base, transition, survival


def test_all_three_arms_score_against_one_final(world):
    """One result, three identities, and the two arms stay separate."""

    session = world["session"]
    _three_arms(world)
    final(world)

    outcome = ForecastEvaluationService(session).evaluate_league(
        league_code="MLB", require_shadow_forecast=True)

    assert outcome["scored"] == 3
    written = list(session.scalars(select(ResearchForecastEvaluation)))
    assert {row.forecast_identity for row in written} == {
        "v18-1", "cand-transition", "cand-survival"}
    # One recorded final behind all three, so every difference is model-only.
    assert len({row.result_identity for row in written}) == 1


def test_the_cohort_averages_the_supported_arm_and_excludes_the_fallback(world):
    session = world["session"]
    _three_arms(world)
    final(world)
    ForecastEvaluationService(session).evaluate_league(
        league_code="MLB", require_shadow_forecast=True)

    supported = candidate_cohort(session, configuration="mlb-v18-three-arm-transition-v1")
    assert supported["games"] == 1
    assert supported["metrics"], "a scored pair must produce at least one metric"
    assert all(metric["games"] == 1 for metric in supported["metrics"])

    fallback = candidate_cohort(session, configuration="mlb-v18-three-arm-duration-v1")
    assert fallback["games"] == 0
    assert [row["reason"] for row in fallback["excluded"]] == [
        "FALLBACK_NOT_A_COMPONENT_TRIAL"]


def test_a_fallback_that_scores_identically_still_never_enters_the_cohort(world):
    """The exclusion is by declared support, not by noticing the numbers match.

    A fallback arm's score equals the baseline's exactly, so every difference it
    contributes is zero. Averaging it in would drag the cohort towards "no
    difference" while looking like extra evidence.
    """

    session = world["session"]
    _three_arms(world, survival_home_win=0.6138)
    final(world)
    ForecastEvaluationService(session).evaluate_league(
        league_code="MLB", require_shadow_forecast=True)

    rows = {row.forecast_identity: row for row in session.scalars(
        select(ResearchForecastEvaluation))}
    assert rows["cand-survival"].log_loss == pytest.approx(rows["v18-1"].log_loss)

    fallback = candidate_cohort(session, configuration="mlb-v18-three-arm-duration-v1")
    assert fallback["games"] == 0


def test_a_pending_game_is_named_rather_than_dropped(world):
    session = world["session"]
    _three_arms(world)
    # No final recorded: nothing is scoreable yet.

    cohort = candidate_cohort(session, configuration="mlb-v18-three-arm-transition-v1")
    assert cohort["games"] == 0
    assert [row["reason"] for row in cohort["excluded"]] == ["PENDING_NOT_YET_SCORED"]


def test_a_candidate_on_a_different_horizon_is_not_averaged_in(world):
    """Two forecasts of different first pitches are not one comparison."""

    session = world["session"]
    forecast(world, engine=BASELINE_ENGINE, version="v18-a", identity="v18-1",
             status="PROSPECTIVE_RESEARCH_SHADOW", home_win=0.6138,
             published=START - timedelta(hours=3))
    arm(world, version="mlb-v18-three-arm-transition-v1", identity="cand-transition",
        home_win=0.6312, supported=True, baseline_identity="v18-1",
        scheduled=START + timedelta(hours=1))
    final(world)
    ForecastEvaluationService(session).evaluate_league(
        league_code="MLB", require_shadow_forecast=True)

    cohort = candidate_cohort(session, configuration="mlb-v18-three-arm-transition-v1")
    assert cohort["games"] == 0
    assert [row["reason"] for row in cohort["excluded"]] == ["HORIZON_MISMATCH"]


def test_an_unbound_candidate_is_not_averaged_against_the_latest_baseline(world):
    session = world["session"]
    forecast(world, engine=BASELINE_ENGINE, version="v18-a", identity="v18-1",
             status="PROSPECTIVE_RESEARCH_SHADOW", home_win=0.6138,
             published=START - timedelta(hours=3))
    arm(world, version="mlb-v18-three-arm-transition-v1", identity="cand-transition",
        home_win=0.6312, supported=True, baseline_identity="a-baseline-aisp-never-held")
    final(world)
    ForecastEvaluationService(session).evaluate_league(
        league_code="MLB", require_shadow_forecast=True)

    cohort = candidate_cohort(session, configuration="mlb-v18-three-arm-transition-v1")
    assert cohort["games"] == 0
    assert [row["reason"] for row in cohort["excluded"]] == ["NO_BOUND_BASELINE"]


def test_rescoring_the_cohort_is_idempotent(world):
    session = world["session"]
    _three_arms(world)
    final(world)
    service = ForecastEvaluationService(session)
    service.evaluate_league(league_code="MLB", require_shadow_forecast=True)
    first = candidate_cohort(session, configuration="mlb-v18-three-arm-transition-v1")

    second_run = service.evaluate_league(league_code="MLB", require_shadow_forecast=True)
    second = candidate_cohort(session, configuration="mlb-v18-three-arm-transition-v1")

    assert second_run["scored"] == 0 and second_run["reused"] == 3
    assert first["metrics"] == second["metrics"]
    assert session.scalar(
        select(func.count()).select_from(ResearchForecastEvaluation)) == 3


def test_the_comparison_reports_the_shared_final_and_the_score_difference(world):
    session = world["session"]
    _three_arms(world)
    final(world)
    ForecastEvaluationService(session).evaluate_league(
        league_code="MLB", require_shadow_forecast=True)

    payload = candidate_comparison(
        session, world["game"].id, configuration="mlb-v18-three-arm-transition-v1")
    assert payload["shared_final"]["status"] == "SHARED"
    difference = payload["score_difference"]
    assert difference is not None
    keys = {metric["key"] for metric in difference["metrics"]}
    assert {"log_loss", "brier_score"} <= keys
    # Direction is stated, not left for the reader to infer from a sign.
    assert "negative" in difference["direction"].lower()
    for metric in difference["metrics"]:
        expected = ("CANDIDATE" if metric["delta"] < 0
                    else "BASELINE" if metric["delta"] > 0 else "NEITHER")
        assert metric["favours"] == expected


def test_a_missing_distribution_is_absent_from_the_difference_not_zero(world):
    """Subtracting against a zero CRPS would invent an improvement."""

    session = world["session"]
    _three_arms(world)
    final(world)
    ForecastEvaluationService(session).evaluate_league(
        league_code="MLB", require_shadow_forecast=True)

    payload = candidate_comparison(
        session, world["game"].id, configuration="mlb-v18-three-arm-transition-v1")
    score = payload["candidate"]["score"]
    assert score["distribution_available"] is False
    assert score["distribution_unavailable_reason"]
    assert score["crps_total"] is None
    keys = {metric["key"] for metric in payload["score_difference"]["metrics"]}
    assert "crps_total" not in keys


def test_a_signed_error_is_compared_by_magnitude_not_by_sign(world):
    """margin_error is stored SIGNED, so ranking it directly inverts the answer.

    Both arms missed low on the same game. Ranking the raw signed values makes
    the WORSE forecast -- the one further from zero -- look better, because it
    is the more negative number. The real cohort showed exactly this: a
    candidate that missed by -5.54 was reported as beating a baseline that
    missed by -5.49.
    """

    session = world["session"]
    _three_arms(world)
    final(world)
    ForecastEvaluationService(session).evaluate_league(
        league_code="MLB", require_shadow_forecast=True)

    rows = {row.forecast_identity: row for row in session.scalars(
        select(ResearchForecastEvaluation))}
    candidate, baseline = rows["cand-transition"], rows["v18-1"]
    assert candidate.margin_error is not None and baseline.margin_error is not None

    payload = candidate_comparison(
        session, world["game"].id, configuration="mlb-v18-three-arm-transition-v1")
    margin = next(m for m in payload["score_difference"]["metrics"]
                  if m["key"] == "margin_error")

    # Compared as magnitudes, and labelled as magnitudes.
    assert margin["label"].lower().startswith("absolute")
    assert margin["candidate"] == pytest.approx(abs(candidate.margin_error))
    assert margin["baseline"] == pytest.approx(abs(baseline.margin_error))
    assert margin["candidate"] >= 0 and margin["baseline"] >= 0
    expected = ("CANDIDATE" if margin["delta"] < 0
                else "BASELINE" if margin["delta"] > 0 else "NEITHER")
    assert margin["favours"] == expected
