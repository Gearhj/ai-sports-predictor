"""NFL outcome scoring and production-consumer readiness.

Covers the conventions (three-way log loss and summed Brier, ties, push-aware
line scoring), the controlling-revision record, correction lineage, league and
status guards, per-game failure isolation, the paper/research/production views,
the review counter and the fail-closed production-handoff adapter.

Nothing here calls a provider, executes a model or creates a decision.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.forecast_exchange import nfl_production_handoff as handoff
from app.models import (
    ExternalIdMapping, Game, GameStatus, League, NflFinalObservation, OfficialAspPosition,
    ResearchForecastEvaluation, ResearchSupplement, Season, Sport,
)
from app.probability.adapter import ADMITTED
from app.probability.market_probabilities import JointScorePopulation
from app.services import forecast_scoring as scoring
from app.services import nfl_outcome_scoring as outcomes
from app.services.forecast_evaluation import (
    GAME_NOT_FINAL, LEAGUE_NOT_SCORABLE, PUBLISHED_AFTER_KICKOFF, SCORING_FAILED,
    SUPERSEDED_BY_CORRECTION, ForecastEvaluationService,
)
from app.services.nfl_scoring_readiness import NflScoringReadinessService
from tests.test_nfl_prospective_scoring import KICKOFF, forecast, record_final, world  # noqa: F401

M1 = ADMITTED["NFL"]


# ---------------------------------------------------------------- helpers --

def population(cells: list[tuple[int, int, int]]) -> JointScorePopulation:
    """(home, away, count) cells as a verified-shape population."""

    return JointScorePopulation.from_cells(
        sport="NFL", game_id="g", forecast_id="f", model_id=M1["engine_id"],
        configuration_id=M1["configuration_id"], package_sha256="a" * 64,
        cells=[{"homeScore": h, "awayScore": a, "count": n} for h, a, n in cells],
        draw_count=sum(n for _, _, n in cells),
    )


def admitted_forecast(world, *, published, version, cells, snapshot_type="CURRENT"):
    """An M1 forecast whose paired population verifies exactly (built whole: forecasts are append-only)."""

    from app.models import ShadowForecast

    draws = sum(n for _, _, n in cells)
    home = sum(n for h, a, n in cells if h > a) / draws
    away = sum(n for h, a, n in cells if a > h) / draws
    tie = sum(n for h, a, n in cells if h == a) / draws
    row = ShadowForecast(
        bundle_id=world["bundle"].id, forecast_id=f"forecast:{uuid.uuid4()}",
        engine_id=M1["engine_id"], engine_version=M1["configuration_id"],
        model_artifact_sha256=M1["model_artifact_sha256"],
        simulator_id="sim", simulator_version="1", simulator_seed=731001,
        event_id="2026_01_NE_SEA", source_event_id="2026_01_NE_SEA",
        event_date=KICKOFF.date(), season=2026, season_type="REG", week=1,
        canonical_game_id=world["game"].id,
        home_team_id=world["home"].abbreviation, away_team_id=world["away"].abbreviation,
        snapshot_type=snapshot_type, snapshot_version=version,
        is_ready=True, readiness_score=1.0, readiness_threshold=0.5,
        uncertainty={}, readiness_detail={}, status="RESEARCH_SHADOW",
        scheduled_start_utc=KICKOFF, available_as_of_utc=published,
        source_cutoff_utc=published - timedelta(days=1), forecast_cutoff_utc=published,
        full_home_win_probability=home, full_away_win_probability=away,
        full_expected_home_score=24.0, full_expected_away_score=21.0,
        distributions={"tie_probability": tie, "joint_score_distribution": [
            {"home_score": h, "away_score": a, "probability": n / draws} for h, a, n in cells]},
        lineage_detail={"completed_simulations": draws, "simulation_count": draws},
    )
    world["session"].add(row)
    world["session"].flush()
    return row


CELLS = [(27, 20, 4000), (20, 24, 3000), (23, 23, 1000), (31, 10, 2000)]  # 10,000 draws, includes a tie


# ------------------------------------------------------- game conventions --

@pytest.mark.parametrize("home,away,outcome", [(24, 17, "HOME"), (17, 24, "AWAY"), (20, 20, "TIE")])
def test_three_way_log_loss_and_summed_brier(home, away, outcome):
    p = scoring.OutcomeProbabilities(home=0.55, away=0.40, tie=0.05)
    assert scoring.actual_class(home, away) == outcome
    expected = {"HOME": 0.55, "AWAY": 0.40, "TIE": 0.05}[outcome]
    assert scoring.log_loss(p, outcome) == pytest.approx(-math.log(expected))
    brier = sum((q - (1.0 if k == outcome else 0.0)) ** 2 for k, q in (("HOME", .55), ("AWAY", .40), ("TIE", .05)))
    assert scoring.brier_score(p, outcome) == pytest.approx(brier)
    assert 0.0 <= scoring.brier_score(p, outcome) <= 2.0


def test_a_tie_is_scored_and_is_a_miss_for_a_side_call(world):
    forecast(world, published=KICKOFF - timedelta(hours=3), version=1)
    record_final(world, home=20, away=20)
    ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    row = world["session"].scalars(select(ResearchForecastEvaluation)).one()
    assert row.actual_outcome == "TIE" and row.winner_correct is False
    assert row.probability_of_actual == pytest.approx(0.0298 / (0.5736 + 0.3966 + 0.0298))
    assert row.detail["mass_reconciliation"]["reconciled"] is True


def test_mass_reconciliation_reports_a_residual():
    assert outcomes.mass_reconciliation(0.6, 0.3, 0.1)["reconciled"] is True
    off = outcomes.mass_reconciliation(0.6, 0.3, 0.2)
    assert off["reconciled"] is False and off["residual"] == pytest.approx(0.1)
    assert outcomes.mass_reconciliation(0.6, None, 0.1)["reconciled"] is False


# ------------------------------------------------------------ line scoring --

def test_integer_spread_push_uses_the_forecast_push_mass():
    pop = population([(24, 21, 5), (21, 21, 1), (27, 24, 2), (17, 24, 2)])  # margins 3,0,3,-7
    line = outcomes.evaluate_line(pop, "SPREAD", -3.0, 24, 21)  # home -3, won by exactly 3
    assert line["line_kind"] == "INTEGER" and line["push_possible"] is True
    assert line["actual_state"] == "PUSH"
    assert line["probabilities"] == {"win": 0.0, "loss": 0.3, "push": 0.7}
    assert line["log_loss"] == pytest.approx(-math.log(0.7))
    assert line["brier_score"] == pytest.approx(0.0 ** 2 + 0.3 ** 2 + 0.3 ** 2)


def test_half_point_spread_has_no_push():
    pop = population([(24, 21, 5), (21, 21, 1), (27, 24, 2), (17, 24, 2)])
    line = outcomes.evaluate_line(pop, "SPREAD", -2.5, 24, 21)
    assert line["line_kind"] == "HALF_POINT" and line["push_possible"] is False
    assert line["probabilities"]["push"] == 0.0
    assert line["actual_state"] == "WIN"
    assert line["probabilities"]["win"] == pytest.approx(0.7)


def test_total_push_is_neither_dropped_nor_a_loss():
    pop = population([(24, 20, 6), (30, 20, 2), (10, 10, 2)])  # totals 44, 50, 20
    line = outcomes.evaluate_line(pop, "TOTAL", 44.0, 24, 20)
    assert line["actual_state"] == "PUSH"
    assert line["probabilities"] == {"win": pytest.approx(0.2), "loss": pytest.approx(0.2),
                                     "push": pytest.approx(0.6)}
    assert line["log_loss"] == pytest.approx(-math.log(0.6))


def test_three_state_scores_refuse_incomplete_mass():
    with pytest.raises(outcomes.LineScoringError):
        outcomes.three_state_scores({"win": 0.5, "loss": 0.3, "push": 0.0}, "WIN")
    with pytest.raises(outcomes.LineScoringError):
        outcomes.three_state_scores({"win": 0.5, "loss": 0.5, "push": 0.0}, "VOID")


# ----------------------------------------------------- revisions & finals --

def test_late_forecast_is_rejected_and_the_earlier_one_controls(world):
    early = admitted_forecast(world, published=KICKOFF - timedelta(hours=30), version=1, cells=CELLS)
    admitted_forecast(world, published=KICKOFF + timedelta(minutes=1), version=2, cells=CELLS)
    record_final(world, home=27, away=20)
    result = ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    assert any(e["reason"] == PUBLISHED_AFTER_KICKOFF for e in result["excluded"])
    row = world["session"].scalars(select(ResearchForecastEvaluation)).one()
    assert row.forecast_identity == early.forecast_id
    revision = row.detail["controlling_revision"]
    assert revision["reason"] == "LATEST_PRE_KICKOFF_PUBLICATION_PER_MODEL_IDENTITY"
    assert revision["package_sha256"] == "0" * 64
    assert revision["population_verification"] == {"verified": True}
    assert len(revision["population_sha256"]) == 64 and revision["population_draws"] == 10000
    assert revision["horizon"]["hours_before_kickoff"] == pytest.approx(30.0)


def test_latest_pre_kickoff_revision_controls_and_counts_once(world):
    admitted_forecast(world, published=KICKOFF - timedelta(hours=30), version=1, cells=CELLS)
    final = admitted_forecast(world, published=KICKOFF - timedelta(minutes=40), version=1,
                              cells=CELLS, snapshot_type="FINAL_PREGAME")
    record_final(world, home=27, away=20)
    service = ForecastEvaluationService(world["session"])
    service.evaluate_game(canonical_game_id=world["game"].id)
    again = service.evaluate_game(canonical_game_id=world["game"].id)
    rows = world["session"].scalars(select(ResearchForecastEvaluation)).all()
    assert len(rows) == 1 and rows[0].forecast_identity == final.forecast_id
    assert again["reused"] == 1 and again["scored"] == 0, "duplicate ingestion is idempotent"


def test_a_corrected_final_writes_a_linked_rescoring_receipt(world):
    admitted_forecast(world, published=KICKOFF - timedelta(hours=5), version=1, cells=CELLS)
    record_final(world, home=27, away=20, checksum="first", source="NFLVERSE",
                 observed=KICKOFF + timedelta(hours=3))
    service = ForecastEvaluationService(world["session"])
    service.evaluate_game(canonical_game_id=world["game"].id)
    record_final(world, home=27, away=23, checksum="corrected", source="NFLVERSE",
                 observed=KICKOFF + timedelta(hours=9))
    service.evaluate_game(canonical_game_id=world["game"].id)
    rows = world["session"].scalars(select(ResearchForecastEvaluation)).all()
    assert len(rows) == 2, "history is kept"
    current = next(r for r in rows if r.is_current)
    old = next(r for r in rows if not r.is_current)
    assert (current.home_score, current.away_score) == (27, 23)
    assert old.superseded_by_id == current.id and old.supersede_reason == SUPERSEDED_BY_CORRECTION
    card = NflScoringReadinessService(world["session"]).scorecard(now=KICKOFF + timedelta(days=1))
    game = card["cohorts"][0]["games"][0]
    replaces = game["audit"]["scoring_receipt"]["replaces"]
    assert replaces == [{"evaluation_id": str(old.id), "reason": SUPERSEDED_BY_CORRECTION,
                         "score": "27-20", "result_identity": old.result_identity}]
    revisions = game["audit"]["final_result"]["revisions"]
    assert [r["current"] for r in revisions] == [False, True]
    assert card["cohorts"][0]["settled_games"] == 1


def test_overtime_is_retained_only_when_the_source_states_it(world):
    admitted_forecast(world, published=KICKOFF - timedelta(hours=5), version=1, cells=CELLS)
    record_final(world, home=27, away=20, checksum="ot", source="NFLVERSE")
    service = NflScoringReadinessService(world["session"])
    assert service._final_detail(world["game"])["overtime"] is None, "absent is not inferred"
    state_id = world["session"].scalar(select(func.max(ResearchForecastEvaluation.id)))  # noqa: F841
    from app.models import GameStateObservation
    observation = world["session"].scalars(select(GameStateObservation)).one()
    world["session"].add(NflFinalObservation(
        game_id=world["game"].id, source_event_id="2026_01_NE_SEA", game_state_observation_id=observation.id,
        source="NFLVERSE", result_tier=2, status="FINAL", home_score=27, away_score=20, overtime=True,
        season=2026, game_type="REG", week=1, retrieved_at=KICKOFF + timedelta(hours=4),
        payload_checksum="ot", raw_row={}))
    world["session"].flush()
    assert service._final_detail(world["game"])["overtime"] is True


@pytest.mark.parametrize("status", [GameStatus.POSTPONED, GameStatus.CANCELED, GameStatus.LIVE])
def test_a_nonfinal_game_is_not_scored_even_with_a_final_observation(world, status):
    forecast(world, published=KICKOFF - timedelta(hours=3), version=1)
    record_final(world)
    world["game"].status = status
    world["session"].flush()
    result = ForecastEvaluationService(world["session"]).evaluate_game(canonical_game_id=world["game"].id)
    assert result["skipped"] == GAME_NOT_FINAL
    assert world["session"].scalar(select(func.count()).select_from(ResearchForecastEvaluation)) == 0


def test_a_game_outside_the_registered_leagues_is_refused(world):
    session = world["session"]
    sport = session.scalars(select(Sport)).first()
    league = League(sport_id=sport.id, code="XFL", name="Other football")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026", starts_at=KICKOFF - timedelta(days=5),
                    ends_at=KICKOFF + timedelta(days=5))
    session.add(season)
    session.flush()
    world["game"].league_id = league.id
    world["game"].season_id = season.id
    forecast(world, published=KICKOFF - timedelta(hours=3), version=1)
    record_final(world)
    service = ForecastEvaluationService(session)
    assert service.evaluate_game(canonical_game_id=world["game"].id)["skipped"] == LEAGUE_NOT_SCORABLE
    assert service.evaluate_league(league_code="NFL")["final_games"] == 0, "not addressable as NFL"


def test_one_failing_game_does_not_stop_the_pass_or_touch_positions(world, monkeypatch):
    forecast(world, published=KICKOFF - timedelta(hours=3), version=1)
    record_final(world)
    session = world["session"]
    positions_before = session.scalar(select(func.count()).select_from(OfficialAspPosition))

    def boom(*args, **kwargs):
        raise RuntimeError("malformed forecast")

    monkeypatch.setattr(ForecastEvaluationService, "_score", boom)
    import app.bankroll.settlement as settlement
    monkeypatch.setattr(settlement, "determine_result",
                        lambda *a, **k: pytest.fail("scoring must never settle"))
    summary = ForecastEvaluationService(session).evaluate_league(league_code="NFL")
    assert summary["skipped"][0]["skipped"] == SCORING_FAILED
    assert "malformed forecast" in summary["skipped"][0]["detail"]
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == positions_before
    assert summary["positions_created"] == 0


# ----------------------------------------------------------------- views --

def test_paper_research_and_production_views_stay_separate(world):
    session = world["session"]
    paper = admitted_forecast(world, published=KICKOFF - timedelta(days=2), version=7, cells=CELLS)
    admitted_forecast(world, published=KICKOFF - timedelta(minutes=30), version=1,
                      cells=[(20, 27, 6000), (27, 20, 4000)], snapshot_type="FINAL_PREGAME")
    session.add(ResearchSupplement(
        supplement_sha256="e" * 64, supplement_id="paper-1", contract_version="V1",
        kind="PAPER_RESEARCH_ELIGIBILITY", sport="NFL", event_id="2026_01_NE_SEA",
        engine_id=M1["engine_id"], forecast_id=paper.forecast_id, artifact_version="ASP_NFL_PAPER_V1",
        simulation_kind="NONE", published_utc=KICKOFF - timedelta(days=1), payload={}, evidence={}))
    session.add(ExternalIdMapping(entity_type="game", entity_id=world["game"].id, source="NFLVERSE",
                                  external_id="nflverse:2026_01_NE_SEA"))
    session.flush()
    record_final(world, home=27, away=20)
    ForecastEvaluationService(session).evaluate_game(canonical_game_id=world["game"].id)

    card = NflScoringReadinessService(session).scorecard(now=KICKOFF + timedelta(days=1))
    cohort = card["cohorts"][0]
    game = cohort["games"][0]
    assert game["paper_view"]["same_revision"] is False
    assert game["paper_view"]["forecast_id"] == paper.forecast_id
    assert game["paper_view"]["authority"] == "PAPER_RESEARCH_EVIDENCE"
    research_loss = game["scores"]["log_loss"]
    assert game["paper_view"]["log_loss"] != pytest.approx(research_loss), "scored separately"
    assert cohort["settled_games"] == 1 and cohort["paper_view"]["games"] == 1
    assert card["production_status"]["status"] == handoff.NOT_RECEIVED
    assert card["production_status"]["production_label_allowed"] is False

    week = NflScoringReadinessService(session).week_readiness(season=2026, week=1,
                                                              now=KICKOFF + timedelta(days=1))
    row = week["rows"][0]
    assert row["views"]["RESEARCH_PROSPECTIVE"]["snapshot_type"] == "FINAL_PREGAME"
    assert row["views"]["PAPER_RESEARCH"][0]["forecast"]["forecast_id"] == paper.forecast_id
    assert row["views"]["PAPER_RESEARCH"][0]["same_revision_as_research_view"] is False
    assert row["views"]["PRODUCTION"]["governed"] is False
    assert row["automatic_scoring"]["state"] == "SCORED"


def test_readiness_names_a_blocker_when_no_forecast_precedes_kickoff(world):
    session = world["session"]
    session.add(ExternalIdMapping(entity_type="game", entity_id=world["game"].id, source="NFLVERSE",
                                  external_id="nflverse:2026_01_NE_SEA"))
    forecast(world, published=KICKOFF + timedelta(minutes=5), version=1)
    session.flush()
    week = NflScoringReadinessService(session).week_readiness(season=2026, week=1, now=KICKOFF - timedelta(hours=1))
    codes = [b["code"] for b in week["rows"][0]["automatic_scoring"]["blockers"]]
    assert "NO_PRE_KICKOFF_M1_FORECAST" in codes
    assert week["rows"][0]["forecasts"]["published_at_or_after_kickoff"] == 1
    assert week["rows"][0]["automatic_scoring"]["state"] == "BLOCKED"


def test_review_counter_and_calibration_gate():
    assert outcomes.review_progress(16)["next_checkpoint"] == 50
    assert outcomes.review_progress(49)["checkpoints"][0] == {"games": 50, "reached": False, "remaining": 1}
    at_fifty = outcomes.review_progress(50)
    assert at_fifty["last_reached"] == 50 and at_fifty["next_checkpoint"] == 100
    assert outcomes.review_progress(100)["next_checkpoint"] is None
    small = outcomes.calibration([(0.6, 1)] * 16)
    assert small["available"] is False and small["pairs"] == 16
    large = outcomes.calibration([(0.65, 1)] * 60 + [(0.3, 0)] * 40)
    assert large["available"] is True
    assert sum(b["games"] for b in large["bins"]) == 100


# ----------------------------------------------------- production handoff --

def _document(**overrides):
    body = {
        "contract": handoff.HANDOFF_CONTRACT, "status": handoff.PRODUCTION_BASELINE,
        "modelId": handoff.EXPECTED_MODEL_ID, "configurationId": handoff.EXPECTED_CONFIGURATION_ID,
        "modelRevision": "M1-2026.09", "modelArtifactSha256": M1["model_artifact_sha256"],
        "discoveryPath": "nfl-m1-production/current.json", "discoveryIndexSha256": "b" * 64,
        "drawCount": 10000, "tiePolicy": "DISCRETE_TIE_MASS", "forecastContract": "ASP_SPORTS_GAME_FORECAST",
        "effectiveFromUtc": "2026-09-24T00:00:00Z", "calibrationAuthority": "PROSPECTIVE_MONITORING",
        "issuedAtUtc": "2026-09-20T12:00:00Z", "issuer": "model-studio", "playLevelSubstitutionAllowed": False,
        "fixture": True,
    }
    body.update(overrides)
    raw = json.dumps(body, sort_keys=True).encode()
    return raw, hashlib.sha256(raw).hexdigest()


def test_production_status_is_not_received_without_a_pin():
    class Settings:
        nfl_production_handoff_path = ""
        nfl_production_handoff_sha256 = ""
    verdict = handoff.production_status(Settings())
    assert verdict.status == handoff.NOT_RECEIVED and verdict.production_label_allowed is False


@pytest.mark.parametrize("overrides,code", [
    ({"status": "NFL_M1_PRODUCTION_READY"}, "STATUS_UNKNOWN"),
    ({"contract": "SOMETHING_ELSE"}, "CONTRACT_UNSUPPORTED"),
    ({"modelId": "NFL_DEVELOPMENT_HIERARCHICAL_PLAY_V1"}, "SUBSTITUTION_NOT_EXCLUDED"),
    ({"playLevelSubstitutionAllowed": True}, "SUBSTITUTION_NOT_EXCLUDED"),
    ({"configurationId": "NFL_M1_OTHER"}, "CONFIGURATION_MISMATCH"),
    ({"modelArtifactSha256": "xyz"}, "HASH_INVALID"),
    ({"drawCount": 0}, "DRAW_COUNT_INVALID"),
    ({"discoveryPath": "../outside.json"}, "DISCOVERY_PATH_INVALID"),
    ({"issuer": None}, "FIELDS_MISSING"),
])
def test_handoff_documents_fail_closed(overrides, code):
    raw, sha = _document(**overrides)
    verdict = handoff.verify_handoff_bytes(raw, pinned_sha256=sha, allow_fixture=True)
    assert verdict.accepted is False and verdict.reasons[0]["code"] == code


def test_hash_mismatch_and_fixtures_are_refused():
    raw, sha = _document()
    assert handoff.verify_handoff_bytes(raw + b" ", pinned_sha256=sha).reasons[0]["code"] == "HASH_MISMATCH"
    assert handoff.verify_handoff_bytes(raw, pinned_sha256=sha).reasons[0]["code"] == "FIXTURE_REFUSED"
    fixture = handoff.verify_handoff_bytes(raw, pinned_sha256=sha, allow_fixture=True)
    assert fixture.accepted and fixture.fixture and fixture.production_label_allowed is False


def _verified(**overrides):
    raw, sha = _document(fixture=False, **overrides)
    verdict = handoff.verify_handoff_bytes(raw, pinned_sha256=sha)
    assert verdict.accepted
    assert verdict.production_label_allowed is (verdict.status == handoff.PRODUCTION_BASELINE)
    return verdict


def _check(verdict, **overrides):
    kickoff = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)
    fields = dict(forecast_id="f1", engine_id=M1["engine_id"], engine_version=M1["configuration_id"],
                  model_artifact_sha256=M1["model_artifact_sha256"], package_sha256="c" * 64,
                  population_sha256="d" * 64, draw_count=10000,
                  published_utc=kickoff - timedelta(hours=2), kickoff_utc=kickoff)
    fields.update(overrides)
    return handoff.verify_forecast(verdict, **fields)


def test_forecasts_are_governed_only_on_an_exact_match():
    verdict = _verified(packageSha256="c" * 64, forecastPopulations={"f1": "d" * 64})
    assert _check(verdict)["governed"] is True
    assert _check(verdict, package_sha256="e" * 64)["code"] == "PACKAGE_MISMATCH"
    assert _check(verdict, population_sha256="e" * 64)["code"] == "POPULATION_MISMATCH"
    assert _check(verdict, draw_count=5000)["code"] == "DRAW_COUNT_MISMATCH"
    assert _check(verdict, forecast_id="f2")["code"] == "FORECAST_NOT_LISTED"
    assert _check(verdict, engine_id="NFL_DEVELOPMENT_HIERARCHICAL_PLAY_V1")["code"] == "MODEL_MISMATCH"
    kickoff = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)
    assert _check(verdict, published_utc=kickoff)["code"] == "LATE_FORECAST"
    assert _check(verdict, published_utc=None)["code"] == "FORECAST_TIME_MISSING"
    early = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
    assert _check(verdict, kickoff_utc=early, published_utc=early - timedelta(hours=1))["code"] == "BEFORE_EFFECTIVE_FROM"


def test_non_production_statuses_never_govern_a_forecast():
    for status in (handoff.SHADOW_ONLY, handoff.PRODUCTION_BLOCKED):
        verdict = _verified(status=status)
        assert verdict.production_label_allowed is False
        assert _check(verdict)["code"] == "NOT_PRODUCTION_STATUS"


def test_status_values_serialize_plainly_for_any_enum():
    from app.models import PositionResult, PositionStatus
    from app.services.nfl_scoring_readiness import _status_value
    assert _status_value(PositionStatus.SETTLED).upper() == "SETTLED"
    assert _status_value(PositionResult.LOSS).upper() == "LOSS"
    assert _status_value(GameStatus.POSTPONED) == "postponed"
    assert _status_value("FINAL") == "final"
