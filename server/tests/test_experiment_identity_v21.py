"""$1,000 Experiment — V1 control vs V2.1 policy challenger identity.

Proves the page-facing contract: both experiments resolve to the SAME
forecasting model (derived from the forecasts their decisions referenced, not
from a label), the challenger is named by its real policy version, and the
decision metrics are distinct counts that are never conflated with positions.

Read-only against the ledger: nothing here creates a settlement or alters a
position, opening balance, or decision.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.models import (
    ExperimentDecisionPhase,
    FeatureVersion,
    Forecast,
    ForecastLifecycle,
    ForecastStatus,
    ModelVersion,
    ShadowExperimentDecision,
)
from app.services.read_service import ReadService
from app.services.shadow_experiment_service import (
    V2_DECISION_POLICY,
    V2_EXPERIMENT_NAME,
    ShadowExperimentService,
)
from test_prediction_market_observer import _seed_game

NOW = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)


def _model_and_forecast(session, game, *, name="asp_mlb_ensemble", version="2.0.0-gate2-test"):
    fv = session.scalar(select(FeatureVersion))
    if fv is None:
        fv = FeatureVersion(name="fv", version="1", schema_hash="0" * 64)
        session.add(fv)
        session.flush()
    model = session.scalar(select(ModelVersion).where(ModelVersion.name == name, ModelVersion.version == version))
    if model is None:
        model = ModelVersion(name=name, version=version, algorithm="ensemble")
        session.add(model)
        session.flush()
    forecast = Forecast(
        game_id=game.id, model_version_id=model.id, feature_version_id=fv.id, version=1,
        generated_at=NOW, available_as_of=NOW, status=ForecastStatus.PUBLISHED, lifecycle=ForecastLifecycle.PREGAME,
        home_win_probability=0.56, away_win_probability=0.44,
    )
    session.add(forecast)
    session.commit()
    return model, forecast


def _decision(session, experiment, game, forecast, *, phase, recommendation, selection="New York Yankees"):
    session.add(ShadowExperimentDecision(
        experiment_id=experiment.id, game_id=game.id, slate_date=date(2026, 9, 2), phase=phase,
        market="MONEYLINE", selection=selection, forecast_id=forecast.id, forecast_generated_at=NOW,
        recommended_at=NOW, model_probability=0.56, no_vig_probability=0.50, edge=0.06,
        recommendation=recommendation, recommendation_reason="test", qualification_reasons=[],
        evidence_fingerprint=uuid.uuid4().hex,
    ))
    session.commit()


def _register_both(session, settings):
    service = ShadowExperimentService(session, settings)
    v1 = service.ensure_started(start_date=date(2026, 8, 27), now=NOW - timedelta(days=5), code_commit="abc")
    v2 = service.ensure_v2_started(effective_date=date(2026, 9, 2), now=NOW, code_commit="abc")
    return service, v1, v2


def test_v21_challenger_is_the_same_model_under_a_different_policy(session, settings):
    settings = settings.model_copy(update={"demo_mode": False})
    _, v1, v2 = _register_both(session, settings)
    game, _, _ = _seed_game(session, start=datetime(2026, 9, 2, 23, 5, tzinfo=UTC))
    model, forecast = _model_and_forecast(session, game)
    _decision(session, v1, game, forecast, phase=ExperimentDecisionPhase.MORNING, recommendation="NO_BET")
    _decision(session, v2, game, forecast, phase=ExperimentDecisionPhase.MORNING, recommendation="QUALIFIED_BET")
    rs = ReadService(session, settings)
    b1, b2 = rs.bankroll("v1"), rs.bankroll("v2")
    # Naming follows the real policy version — V2.1, not a stale "V2".
    assert v2.decision_policy == V2_DECISION_POLICY == "AISP_PRACTICAL_PREGAME_POLICY_V2_1"
    assert b2.experiment_name == V2_EXPERIMENT_NAME and "V2.1" in b2.experiment_name
    assert b2.policy_label == "Practical pregame policy challenger — V2.1"
    assert b2.experiment_key == "v2" and b2.experiment_role == "POLICY_CHALLENGER"
    assert b1.experiment_key == "v1" and b1.experiment_role == "STRICT_CONTROL"
    assert "SAME MODEL" in (b2.policy_relationship or "")
    # Same model identity on both sides, derived from decisions → forecasts → model_versions.
    assert (b1.model_name, b1.model_version) == (b2.model_name, b2.model_version) == (model.name, model.version)
    assert b1.model_identity_source.startswith("decisions") and b2.model_identity_source.startswith("decisions")
    assert b1.model_status == b2.model_status == "development"
    # Policies differ; the controlling engine label is not a model identity.
    assert b1.policy != b2.policy and b1.controlling_engine == b2.controlling_engine


def test_decision_metrics_are_distinct_and_never_conflated(session, settings):
    settings = settings.model_copy(update={"demo_mode": False})
    _, v1, v2 = _register_both(session, settings)
    g1, _, _ = _seed_game(session, start=datetime(2026, 9, 2, 23, 5, tzinfo=UTC))
    g2, _, _ = _seed_game(session, home="Los Angeles Dodgers", away="Washington Nationals",
                          start=datetime(2026, 9, 3, 2, 10, tzinfo=UTC))
    _, f1 = _model_and_forecast(session, g1)
    _, f2 = _model_and_forecast(session, g2)
    # Game 1 evaluated at three phases (qualified twice), game 2 evaluated once (qualified) — same selection twice.
    _decision(session, v2, g1, f1, phase=ExperimentDecisionPhase.MORNING, recommendation="QUALIFIED_BET")
    _decision(session, v2, g1, f1, phase=ExperimentDecisionPhase.LINEUP_CONFIRMED, recommendation="QUALIFIED_BET")
    _decision(session, v2, g1, f1, phase=ExperimentDecisionPhase.FINAL_PREGAME, recommendation="NO_BET")
    _decision(session, v2, g2, f2, phase=ExperimentDecisionPhase.MORNING, recommendation="QUALIFIED_BET",
              selection="Los Angeles Dodgers")
    rs = ReadService(session, settings)
    m = rs.bankroll("v2").decision_metrics
    assert m.candidate_evaluations == 4          # every phase snapshot
    assert m.games_evaluated == 2                # distinct games
    assert m.qualified_evaluations == 3          # QUALIFIED_BET rows (repeated phases count)
    assert m.qualified_selections == 2           # distinct (game, selection)
    assert m.frozen_positions == 0 and m.settled_positions == 0
    assert m.evidence_stage == "NO_EVIDENCE"
    # The comparison endpoint carries the same distinct metrics and identity, and keeps legacy keys.
    comp = rs.experiment_comparison()
    row = next(e for e in comp["experiments"] if e["key"] == "v2")
    assert row["decision_metrics"]["candidate_evaluations"] == 4 == row["decisions"]
    assert row["decision_metrics"]["qualified_selections"] == 2 and row["qualified_decisions"] == 3
    assert row["role"] == "POLICY_CHALLENGER" and row["policy_label"].endswith("V2.1")
    assert row["model_name"] == "asp_mlb_ensemble"
    v1_row = next(e for e in comp["experiments"] if e["key"] == "v1")
    assert v1_row["model_name"] is None and v1_row["decision_metrics"]["candidate_evaluations"] == 0
    # The superseded V2 registration (if any) is reported, never pooled, never active.
    assert all(s["status"] != "ACTIVE" for s in comp["superseded"])


def test_identity_is_honest_when_no_decision_exists(session, settings):
    settings = settings.model_copy(update={"demo_mode": False})
    _register_both(session, settings)
    b = ReadService(session, settings).bankroll("v2")
    assert b.model_name is None and b.model_version is None
    assert b.model_identity_source == "no decision references a forecast yet"
    assert b.decision_metrics.evidence_stage == "NO_EVIDENCE"
