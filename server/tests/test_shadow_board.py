"""Model Studio forecast-exchange consumer + research-shadow board tests.

All odds in these tests are SYNTHETIC and explicitly labeled; no real provider is
called and no quota is consumed. Real-money execution is impossible by design.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.forecast_exchange.importer import ForecastBundleImportError, verify_and_load_bundle
from app.forecast_exchange.pricing import (
    ShadowMarket,
    american_to_decimal,
    expected_value,
    implied_probability,
    model_probability,
    no_vig_two_sided,
)
from app.gen2.exchange.canonical_json import canonical_json_bytes, canonical_sha256
from app.models import (
    ShadowBoardDecision,
    ShadowDecisionSettlement,
    ShadowForecast,
    ShadowForecastBundle,
)
from app.services.shadow_board_service import (
    DecisionOutcome,
    GovernedQuote,
    ShadowBoardService,
)


REAL_BUNDLE = Path(
    "c:/Users/james/asp-model-exchange/aisp-daily-forecast-bundles/sha256/"
    "784ccb36d14e4eb988c22b755d35ecee5e7d29f2f0fe1fa5fdd39c5a64d33ca4"
)

# SYNTHETIC-ODDS-LABEL: every quote below is invented for deterministic tests.


# --------------------------------------------------------------------------
# Synthetic bundle builders (produce correctly-hashed, canonical bundles)
# --------------------------------------------------------------------------


def _split(home_win, away_win, *, total_dist=None, margin_dist=None):
    return {
        "available": True,
        "awayWinProbability": away_win,
        "completedSimulations": 1000,
        "completionRate": 1.0,
        "expectedAwayScore": 3.0,
        "expectedHomeScore": 4.0,
        "expectedTotalScore": 7.0,
        "homeWinProbability": home_win,
        "jointScoreDistribution": [
            {"awayScore": 3, "homeScore": 4, "probability": 0.6},
            {"awayScore": 4, "homeScore": 3, "probability": 0.4},
        ],
        "missingReason": None,
        "scoreMarginDistribution": margin_dist or [
            {"homeMinusAway": 1, "probability": 0.6},
            {"homeMinusAway": -1, "probability": 0.4},
        ],
        "tieProbability": 0.0,
        "totalScoreDistribution": total_dist or [
            {"probability": 0.5, "totalScore": 7},
            {"probability": 0.5, "totalScore": 9},
        ],
    }


def _uncertainty():
    return {
        "awayWinProbabilityLower": 0.30, "awayWinProbabilityUpper": 0.36,
        "confidenceLevel": 0.95, "expectedTotalScoreStdError": 0.12,
        "firstFiveExpectedTotalScoreStdError": 0.10,
        "firstFiveHomeWinProbabilityLower": 0.50, "firstFiveHomeWinProbabilityUpper": 0.57,
        "homeWinProbabilityLower": 0.62, "homeWinProbabilityUpper": 0.70,
        "maxBatchProbabilityDelta": 0.02, "method": "TEST",
    }


def build_forecast(engine_id, *, event_id="game-1", home="team-h", away="team-a",
                   event_date="2024-09-16", season=2024, is_ready=False,
                   home_win=0.66, away_win=0.34, margin_dist=None,
                   source_event_id="retrosheet:game:TEST"):
    forecast = {
        "awayTeam": {"displayName": None, "teamId": away},
        "engine": {"candidateId": engine_id, "classOrder": ["A", "B"], "engineId": engine_id,
                   "engineVersion": "TEST_V1", "inputDataSha256": "0" * 64, "modelArtifactSha256": "1" * 64},
        "eventDate": event_date, "eventId": event_id, "executionAllowed": False,
        "explanations": [],
        "firstFive": _split(0.54, 0.46),
        "forecastCutoffUtc": None,
        "fullGame": _split(home_win, away_win, margin_dist=margin_dist),
        "homeTeam": {"displayName": None, "teamId": home},
        "league": "MLB", "modelPromotionAllowed": False,
        "readiness": {"isReady": is_ready, "missingInputs": [], "reasonCodes": [],
                      "score": 90 if is_ready else 80, "threshold": 85},
        "researchShadowOnly": True, "scheduledStartUtc": None,
        "schemaVersion": "AISP_GAME_FORECAST_EXPORT_V1", "season": season,
        "simulator": {"completedSimulations": 1000, "illegalStateCount": 0, "nonterminationCount": 0,
                      "seed": 1, "simulationCount": 1000, "simulatorId": "SIM", "simulatorVersion": "SIM_V1"},
        "sourceEventId": source_event_id, "sport": "BASEBALL", "status": "RESEARCH_SHADOW_NOT_READY",
        "uncertainty": _uncertainty(),
    }
    forecast["forecastId"] = f"aisp-game-forecast:sha256:{canonical_sha256(forecast)}"
    return forecast


def build_bundle(forecasts, *, slate="2024-09-16", research_shadow=True, execution=False,
                 promotion=False, parlays=False, extra_field=False):
    bundle = {
        "betCandidates": [], "executionAllowed": execution, "explanations": [],
        "forecastCutoffUtc": None, "forecasts": forecasts,
        "generatedAtUtc": "2026-08-27T01:16:33.220840Z", "league": "MLB",
        "marketQuotes": [], "modelPromotionAllowed": promotion, "parlaysAllowed": parlays,
        "readiness": {"isReady": False, "missingInputs": [], "reasonCodes": [], "score": 0, "threshold": 85},
        "researchShadowOnly": research_shadow, "schemaVersion": "AISP_DAILY_FORECAST_BUNDLE_V1",
        "slateDate": slate, "sport": "BASEBALL", "status": "RESEARCH_SHADOW_NOT_READY",
    }
    if extra_field:
        bundle["unexpectedField"] = "x"
    bundle["bundleId"] = f"aisp-daily-forecast-bundle:sha256:{canonical_sha256(bundle)}"
    return bundle


def write_bundle_dir(tmp_path, bundle, *, corrupt=False, contract_versions=None, counts=None):
    raw = canonical_json_bytes(bundle)
    declared_sha = hashlib.sha256(raw).hexdigest()
    file_bytes = raw.replace(b'"MLB"', b'"NHL"', 1) if corrupt else raw
    directory = tmp_path / declared_sha
    directory.mkdir()
    (directory / "bundle.json").write_bytes(file_bytes)
    fixture = {
        "bundleLogicalUri": f"asp-model-exchange://x/sha256/{declared_sha}/bundle.json",
        "bundleSha256": declared_sha, "executionAllowed": False,
        "expectedContractVersions": contract_versions or {
            "betCandidate": "AISP_BET_CANDIDATE_V1", "dailyForecastBundle": "AISP_DAILY_FORECAST_BUNDLE_V1",
            "gameForecast": "AISP_GAME_FORECAST_EXPORT_V1", "marketQuote": "AISP_MARKET_QUOTE_V1"},
        "expectedRecordCounts": counts or {"betCandidates": 0, "forecasts": len(bundle["forecasts"]), "marketQuotes": 0},
        "fixtureId": "aisp-consumer-import-fixture:sha256:" + "0" * 64,
        "researchShadowOnly": True, "schemaVersion": "AISP_CONSUMER_IMPORT_FIXTURE_V1",
        "validationInstructionsSha256": "0" * 64,
    }
    (directory / "consumer-import-fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
    return directory


def _default_bundle(tmp_path, **kw):
    ready = kw.pop("is_ready", False)
    margin = kw.pop("margin_dist", None)
    forecasts = [
        build_forecast("DIRECT_GOLD", is_ready=ready, margin_dist=margin),
        build_forecast("E10", home_win=0.652, away_win=0.348, is_ready=ready, margin_dist=margin),
    ]
    return write_bundle_dir(tmp_path, build_bundle(forecasts, **kw))


# --------------------------------------------------------------------------
# Import + verification
# --------------------------------------------------------------------------


def test_real_bundle_import(session, settings):
    if not REAL_BUNDLE.exists():
        pytest.skip("external model-exchange bundle not present")
    result = ShadowBoardService(session, settings).import_bundle(REAL_BUNDLE)
    assert result["bundle_sha256"] == "784ccb36d14e4eb988c22b755d35ecee5e7d29f2f0fe1fa5fdd39c5a64d33ca4"
    assert result["forecasts"] == 2
    assert sorted(result["engines"]) == ["DIRECT_GOLD", "E10"]
    assert session.query(ShadowForecast).count() == 2


def test_synthetic_valid_bundle_import(session, settings, tmp_path):
    directory = _default_bundle(tmp_path)
    result = ShadowBoardService(session, settings).import_bundle(directory)
    assert result["forecasts"] == 2
    assert result["idempotent_reuse"] is False
    assert session.query(ShadowForecastBundle).count() == 1


def test_tampered_bundle_hash_rejected(session, settings, tmp_path):
    directory = write_bundle_dir(tmp_path, build_bundle([build_forecast("DIRECT_GOLD")]), corrupt=True)
    with pytest.raises(ForecastBundleImportError, match="SHA-256"):
        ShadowBoardService(session, settings).import_bundle(directory)


def test_unknown_contract_rejected(session, settings, tmp_path):
    directory = write_bundle_dir(
        tmp_path, build_bundle([build_forecast("DIRECT_GOLD")]),
        contract_versions={"betCandidate": "X", "dailyForecastBundle": "Y", "gameForecast": "Z", "marketQuote": "Q"},
    )
    with pytest.raises(ForecastBundleImportError, match="unsupported contract"):
        ShadowBoardService(session, settings).import_bundle(directory)


def test_unknown_field_rejected(session, settings, tmp_path):
    directory = write_bundle_dir(tmp_path, build_bundle([build_forecast("DIRECT_GOLD")], extra_field=True))
    with pytest.raises(ForecastBundleImportError, match="closed daily-forecast contract"):
        ShadowBoardService(session, settings).import_bundle(directory)


def test_duplicate_key_rejected(session, settings, tmp_path):
    raw = b'{"schemaVersion":"AISP_DAILY_FORECAST_BUNDLE_V1","schemaVersion":"AISP_DAILY_FORECAST_BUNDLE_V1"}'
    sha = hashlib.sha256(raw).hexdigest()
    directory = tmp_path / sha
    directory.mkdir()
    (directory / "bundle.json").write_bytes(raw)
    (directory / "consumer-import-fixture.json").write_text(json.dumps({
        "bundleLogicalUri": f"asp-model-exchange://x/sha256/{sha}/bundle.json", "bundleSha256": sha,
        "executionAllowed": False, "expectedContractVersions": {
            "betCandidate": "AISP_BET_CANDIDATE_V1", "dailyForecastBundle": "AISP_DAILY_FORECAST_BUNDLE_V1",
            "gameForecast": "AISP_GAME_FORECAST_EXPORT_V1", "marketQuote": "AISP_MARKET_QUOTE_V1"},
        "expectedRecordCounts": {"betCandidates": 0, "forecasts": 0, "marketQuotes": 0},
        "fixtureId": "aisp-consumer-import-fixture:sha256:" + "0" * 64, "researchShadowOnly": True,
        "schemaVersion": "AISP_CONSUMER_IMPORT_FIXTURE_V1", "validationInstructionsSha256": "0" * 64,
    }), encoding="utf-8")
    with pytest.raises(ForecastBundleImportError, match="Duplicate JSON key"):
        ShadowBoardService(session, settings).import_bundle(directory)


def test_2025_event_rejected(session, settings, tmp_path):
    forecast = build_forecast("DIRECT_GOLD", season=2025, event_date="2025-04-01")
    directory = write_bundle_dir(tmp_path, build_bundle([forecast], slate="2025-04-01"))
    with pytest.raises(ForecastBundleImportError, match="2025"):
        ShadowBoardService(session, settings).import_bundle(directory)


def test_identity_mismatch_rejected(session, settings, tmp_path):
    a = build_forecast("DIRECT_GOLD", event_id="game-1", home="team-h", away="team-a")
    b = build_forecast("E10", event_id="game-1", home="team-DIFFERENT", away="team-a")
    directory = write_bundle_dir(tmp_path, build_bundle([a, b]))
    with pytest.raises(ForecastBundleImportError, match="inconsistent team/identity"):
        ShadowBoardService(session, settings).import_bundle(directory)


def test_child_hash_mismatch_rejected(session, settings, tmp_path):
    forecast = build_forecast("DIRECT_GOLD")
    forecast["fullGame"]["homeWinProbability"] = 0.99  # forecastId now stale
    directory = write_bundle_dir(tmp_path, build_bundle([forecast]))
    with pytest.raises(ForecastBundleImportError, match="does not bind"):
        ShadowBoardService(session, settings).import_bundle(directory)


def test_execution_allowed_bundle_rejected(session, settings, tmp_path):
    directory = write_bundle_dir(tmp_path, build_bundle([build_forecast("DIRECT_GOLD")], execution=True))
    with pytest.raises(ForecastBundleImportError, match="execution"):
        ShadowBoardService(session, settings).import_bundle(directory)


def test_duplicate_import_idempotent(session, settings, tmp_path):
    directory = _default_bundle(tmp_path)
    svc = ShadowBoardService(session, settings)
    first = svc.import_bundle(directory)
    second = svc.import_bundle(directory)
    assert first["idempotent_reuse"] is False
    assert second["idempotent_reuse"] is True
    assert session.query(ShadowForecastBundle).count() == 1
    assert session.query(ShadowForecast).count() == 2


def test_direct_gold_and_e10_track_separation(session, settings, tmp_path):
    ShadowBoardService(session, settings).import_bundle(_default_bundle(tmp_path))
    engines = {f.engine_id for f in session.query(ShadowForecast).all()}
    forecast_ids = {f.forecast_id for f in session.query(ShadowForecast).all()}
    assert engines == {"DIRECT_GOLD", "E10"}
    assert len(forecast_ids) == 2  # distinct sha-addressed identities per track


# --------------------------------------------------------------------------
# Pricing math
# --------------------------------------------------------------------------


def test_two_sided_no_vig():
    # -110 / -110 has a 4.76% hold; no-vig is exactly 0.5 each.
    imp = implied_probability(-110)
    assert no_vig_two_sided(imp, imp) == pytest.approx(0.5)
    assert implied_probability(+100) == pytest.approx(0.5)
    assert american_to_decimal(+100) == pytest.approx(2.0)
    assert expected_value(0.6, 2.0) == pytest.approx(0.2)


def test_moneyline_runline_total_pricing():
    split = _split(0.66, 0.34, total_dist=[{"probability": 0.4, "totalScore": 7}, {"probability": 0.6, "totalScore": 9}],
                   margin_dist=[{"homeMinusAway": 2, "probability": 0.55}, {"homeMinusAway": -1, "probability": 0.45}])
    from app.forecast_exchange.contracts import GameSplit, Uncertainty
    full = GameSplit.model_validate(split)
    unc = Uncertainty.model_validate(_uncertainty())
    ml = model_probability(ShadowMarket.FULL_GAME_MONEYLINE, "HOME", line=None, full_game=full, first_five=full, uncertainty=unc)
    assert ml.probability == pytest.approx(0.66)
    rl = model_probability(ShadowMarket.RUN_LINE_1_5, "HOME_-1.5", line=None, full_game=full, first_five=full, uncertainty=unc)
    assert rl.probability == pytest.approx(0.55)  # P(margin >= 2)
    over = model_probability(ShadowMarket.FULL_GAME_TOTAL, "OVER", line=8.5, full_game=full, first_five=full, uncertainty=unc)
    assert over.probability == pytest.approx(0.6)  # P(total > 8.5) = total 9
    under = model_probability(ShadowMarket.FULL_GAME_TOTAL, "UNDER", line=8.5, full_game=full, first_five=full, uncertainty=unc)
    assert under.probability == pytest.approx(0.4)


# --------------------------------------------------------------------------
# Board evaluation
# --------------------------------------------------------------------------


def _quote(event_id, market, selection, side, opp, *, observed_at=None, line=None, books=None):
    # SYNTHETIC-ODDS-LABEL
    return {(event_id, market, selection): GovernedQuote(
        book="TESTBOOK", side_american=side, opposite_american=opp,
        observed_at=observed_at or datetime.now(UTC), line=line,
        book_prices=tuple(books or [("TESTBOOK", side)]))}


def test_no_quote_is_no_bet(session, settings, tmp_path):
    from app.services.shadow_board_service import MappingQuoteSource
    svc = ShadowBoardService(session, settings)
    svc.import_bundle(_default_bundle(tmp_path))
    summary = svc.evaluate_board(date(2024, 9, 16), quote_source=MappingQuoteSource({}))
    assert summary["priced"] == 0
    assert summary["qualified"] == 0
    assert summary["no_bet"] == summary["decisions"]
    assert summary["total_virtual_stake"] == 0.0
    for decision in session.query(ShadowBoardDecision).all():
        assert decision.has_quote is False
        assert decision.recommendation == "NO_BET"
        assert float(decision.virtual_stake) == 0.0
        assert decision.book is None and decision.no_vig_edge is None


def test_stale_quote_is_no_bet(session, settings, tmp_path):
    from app.services.shadow_board_service import MappingQuoteSource
    svc = ShadowBoardService(session, settings)
    svc.import_bundle(_default_bundle(tmp_path, is_ready=True))
    stale = _quote("game-1", "FULL_GAME_MONEYLINE", "HOME", +100, -120,
                   observed_at=datetime.now(UTC) - timedelta(hours=10))
    svc.evaluate_board(date(2024, 9, 16), quote_source=MappingQuoteSource(stale),
                       as_of=datetime.now(UTC), max_staleness_minutes=240)
    decision = session.query(ShadowBoardDecision).filter_by(
        market="FULL_GAME_MONEYLINE", selection="HOME", engine_id="DIRECT_GOLD").one()
    assert decision.has_quote is False
    assert decision.recommendation == "NO_BET"


def test_qualified_bet_requires_readiness(session, settings, tmp_path):
    from app.services.shadow_board_service import MappingQuoteSource
    svc = ShadowBoardService(session, settings)
    # NOT ready -> at most WATCH even with a strong edge.
    svc.import_bundle(_default_bundle(tmp_path, is_ready=False))
    svc.evaluate_board(date(2024, 9, 16),
                       quote_source=MappingQuoteSource(_quote("game-1", "FULL_GAME_MONEYLINE", "HOME", +100, -120)))
    decision = session.query(ShadowBoardDecision).filter_by(
        market="FULL_GAME_MONEYLINE", selection="HOME", engine_id="DIRECT_GOLD").one()
    assert decision.has_quote is True
    assert decision.recommendation == "WATCH"
    assert float(decision.virtual_stake) == 0.0


def test_exposure_caps(session, settings, tmp_path):
    from app.services.shadow_board_service import MappingQuoteSource
    svc = ShadowBoardService(session, settings)
    # One ready game with three individually-qualified home selections.
    margin = [{"homeMinusAway": 2, "probability": 0.6}, {"homeMinusAway": -1, "probability": 0.4}]
    directory = _default_bundle(tmp_path, is_ready=True, margin_dist=margin)
    svc.import_bundle(directory)
    quotes = {}
    quotes.update(_quote("game-1", "FULL_GAME_MONEYLINE", "HOME", +100, -120))
    quotes.update(_quote("game-1", "FIRST_FIVE_MONEYLINE", "HOME", +100, -120))
    quotes.update(_quote("game-1", "RUN_LINE_1_5", "HOME_-1.5", +100, -120))
    svc.evaluate_board(date(2024, 9, 16), quote_source=MappingQuoteSource(quotes))
    # Position cap = 1% * 1000 = $10; same-game cap = 2% * 1000 = $20.
    decisions = session.query(ShadowBoardDecision).filter_by(engine_id="DIRECT_GOLD").all()
    qualified = [d for d in decisions if d.recommendation == "QUALIFIED_BET"]
    game_exposure = sum(float(d.virtual_stake) for d in decisions if d.event_id == "game-1")
    assert len(qualified) == 3  # all three qualify on edge
    assert game_exposure <= 20.0 + 1e-9  # same-game cap enforced
    assert max(float(d.virtual_stake) for d in decisions) <= 10.0 + 1e-9  # position cap
    assert any(float(d.virtual_stake) == 0.0 for d in qualified)  # third capped to zero


def test_zero_real_money_capability(session, settings, tmp_path):
    svc = ShadowBoardService(session, settings)
    svc.import_bundle(_default_bundle(tmp_path))
    board = svc.board(date(2024, 9, 16))
    assert board["executable"] is False
    assert board["status"] == "RESEARCH_SHADOW"
    # No execution/placement affordance exists on the service surface.
    assert not any(hasattr(svc, name) for name in ("place_bet", "execute", "submit_wager"))
    bundle = session.query(ShadowForecastBundle).one()
    assert bundle.research_shadow_only is True


def test_settlement_is_virtual_and_appends(session, settings, tmp_path):
    from app.services.shadow_board_service import MappingQuoteSource
    svc = ShadowBoardService(session, settings)
    svc.import_bundle(_default_bundle(tmp_path, is_ready=True))
    svc.evaluate_board(date(2024, 9, 16),
                       quote_source=MappingQuoteSource(_quote("game-1", "FULL_GAME_MONEYLINE", "HOME", +100, -120)))
    result = svc.settle_decisions(date(2024, 9, 16), [
        DecisionOutcome("game-1", "DIRECT_GOLD", "FULL_GAME_MONEYLINE", "HOME", "WIN", closing_american=-110),
    ])
    assert result["settled"] == 1
    settlement = session.query(ShadowDecisionSettlement).one()
    assert settlement.outcome == "WIN"
    assert float(settlement.virtual_profit_loss) > 0  # +100 win pays the stake
    assert settlement.closing_line_value is not None  # CLV recorded
    # Idempotent: re-settling adds nothing.
    assert svc.settle_decisions(date(2024, 9, 16), [
        DecisionOutcome("game-1", "DIRECT_GOLD", "FULL_GAME_MONEYLINE", "HOME", "WIN", closing_american=-110),
    ])["settled"] == 0


def test_decision_ledger_is_immutable(session, settings, tmp_path):
    from app.audit.immutability import ImmutableRecordError
    from app.services.shadow_board_service import MappingQuoteSource
    svc = ShadowBoardService(session, settings)
    svc.import_bundle(_default_bundle(tmp_path))
    svc.evaluate_board(date(2024, 9, 16), quote_source=MappingQuoteSource({}))
    decision = session.query(ShadowBoardDecision).first()
    with pytest.raises(ImmutableRecordError):
        session.delete(decision)
        session.flush()
    session.rollback()
    decision = session.query(ShadowBoardDecision).first()
    with pytest.raises(ImmutableRecordError):
        decision.recommendation = "QUALIFIED_BET"
        session.flush()
    session.rollback()


# --------------------------------------------------------------------------
# Daily board API
# --------------------------------------------------------------------------


def test_daily_board_api(client, session, settings, tmp_path):
    ShadowBoardService(session, settings).import_bundle(_default_bundle(tmp_path))
    ShadowBoardService(session, settings).evaluate_board(date(2024, 9, 16))
    response = client.get("/api/v1/shadow-board?date=2024-09-16")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "RESEARCH_SHADOW"
    assert body["executable"] is False
    assert len(body["games"]) == 1
    engines = {e["engine_id"] for e in body["games"][0]["engines"]}
    assert engines == {"DIRECT_GOLD", "E10"}
    assert body["games"][0]["model_disagreement"] is not None
    # Every decision is a real NO_BET (no governed quote for a 2024 event).
    recs = {d["recommendation"] for e in body["games"][0]["engines"] for d in e["decisions"]}
    assert recs == {"NO_BET"}


def test_daily_board_api_empty_slate_is_clean(client):
    response = client.get("/api/v1/shadow-board?date=2024-01-01")
    assert response.status_code == 200
    assert response.json()["games"] == []


# --------------------------------------------------------------------------
# Jobs (registered, audited, idempotent, no provider calls)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_jobs_via_orchestrator(session, settings, tmp_path):
    from app.jobs import JobOrchestrator
    from app.models import AuditEvent, JobRun, RunStatus

    directory = _default_bundle(tmp_path)
    job_settings = settings.model_copy(update={"shadow_bundle_import_dir": str(directory)})
    orch = JobOrchestrator(session, job_settings)

    imported = await orch.run("import-model-studio-forecast-bundle")
    assert imported.status == RunStatus.SUCCEEDED
    assert imported.result["records"] == 2
    # Idempotent re-import.
    assert (await orch.run("import-model-studio-forecast-bundle")).result["idempotent_reuse"] is True

    evaluated = await orch.run("evaluate-shadow-betting-board", event_date=date(2024, 9, 16))
    assert evaluated.status == RunStatus.SUCCEEDED
    assert evaluated.result["records"] == 28  # 14 selections x 2 engines
    # Idempotent re-evaluation creates no new decisions.
    assert (await orch.run("evaluate-shadow-betting-board", event_date=date(2024, 9, 16))).result["decisions"] == 0

    settled = await orch.run("settle-shadow-decisions", event_date=date(2024, 9, 16))
    assert settled.status == RunStatus.SUCCEEDED
    assert settled.result["records"] == 0  # no governed final outcomes for a historical shadow slate

    # Every job run is audited.
    assert session.query(JobRun).filter(JobRun.job_name.like("%shadow%") | JobRun.job_name.like("import-model%")).count() >= 3
    assert session.query(AuditEvent).filter(AuditEvent.action.like("job.%.succeeded")).count() >= 4
