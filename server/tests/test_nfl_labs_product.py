"""Focused checks for the NFL labs product.

1. The research-supplement container: a valid joint-score supplement imports
   once and only once; a supplement whose stated probabilities are not its own
   distribution's, or that binds to a forecast AISP does not hold, is refused
   with nothing written.
2. NFL market arithmetic: two-way moneyline conditional non-tie probability,
   spread push mass, total push mass, all read off one joint distribution.
3. The canonical-position price repair: a -115 quote whose four-place decimal
   differs from the snapshot's full-precision decimal now creates the
   position in an isolated database, while a genuinely different price still
   does not.
4. The overnight model-laboratory study imports from a byte-verified handoff.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.forecast_exchange.supplements import (
    CONTRACT_VERSION,
    INDEX_SCHEMA,
    KIND_JOINT,
    SupplementImportError,
    register_supplement,
    verify_supplement,
)
from app.gen2.exchange.canonical_json import canonical_json_bytes, canonical_sha256
from app.models import ResearchSupplement, ShadowForecast, ShadowForecastBundle
from app.services import nfl_market as arithmetic
from app.services.shadow_experiment_service import same_quoted_price
from app.shadow_import.nfl_model_lab_study import LabStudyImportError, register_lab_study

EVENT = "2026_01_NE_SEA"
ENGINE = "NFL_M1_XGBOOST"
FORECAST_ID = "nfl-m1-2026-01-NE-SEA-current-v9"

JOINT = [
    {"homeScore": 24, "awayScore": 20, "probability": 0.45},
    {"homeScore": 17, "awayScore": 20, "probability": 0.35},
    {"homeScore": 20, "awayScore": 20, "probability": 0.20},
]
GOVERNANCE = {
    "status": "RESEARCH_SHADOW", "researchShadowOnly": True, "executionAllowedInAisp": False,
    "modelPromotionAllowed": False, "wageringAuthority": False,
}


def _register_forecast(session) -> ShadowForecast:
    bundle = ShadowForecastBundle(
        bundle_sha256="b" * 64, bundle_id="bundle-1", logical_uri="asp://bundle-1",
        source_fixture_id="fixture", slate_date=date(2026, 9, 9), league="NFL", sport="NFL",
        status="RESEARCH_SHADOW", research_shadow_only=True,
        generated_at=datetime(2026, 9, 8, 22, tzinfo=UTC), forecast_count=1, quote_count=0,
        candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    forecast = ShadowForecast(
        bundle_id=bundle.id, forecast_id=FORECAST_ID, engine_id=ENGINE,
        engine_version="v1", model_artifact_sha256="a" * 64, simulator_id="sim",
        simulator_version="v1", simulator_seed=1, event_id=EVENT, source_event_id="nflverse:" + EVENT,
        event_date=date(2026, 9, 9), season=2026, week=1, home_team_id="SEA", away_team_id="NE",
        is_ready=True, readiness_score=78.0, readiness_threshold=50.0, status="RESEARCH_SHADOW",
        scheduled_start_utc=datetime(2026, 9, 10, 0, 20, tzinfo=UTC),
        full_home_win_probability=0.45, full_away_win_probability=0.35,
        full_expected_home_score=21.0, full_expected_away_score=20.0,
        distributions={}, uncertainty={}, readiness_detail={},
    )
    session.add(forecast)
    session.flush()
    return forecast


def _write_supplement(root: Path, *, summaries: dict, forecast_id: str = FORECAST_ID,
                      kind: str = KIND_JOINT) -> Path:
    body = {
        "schemaVersion": CONTRACT_VERSION, "supplementId": "supp-1", "kind": kind, "sport": "NFL",
        "binding": {"eventId": EVENT, "engineId": ENGINE, "forecastId": forecast_id},
        "artifactVersion": "v1", "sourceCutoffUtc": "2026-09-08T18:53:04+00:00",
        "publishedUtc": "2026-09-09T01:00:00+00:00",
        "simulation": {"kind": "MONTE_CARLO", "count": 10000, "seed": 7},
        "summaries": summaries, "governance": GOVERNANCE,
    }
    body_bytes = canonical_json_bytes(body)
    manifest = {
        "schemaVersion": "AISP_RESEARCH_SUPPLEMENT_MANIFEST_V1", "supplementId": "supp-1",
        "contractVersion": CONTRACT_VERSION, "kind": kind, "sport": "NFL",
        "artifacts": [{"file": "supplement.json", "sha256": hashlib.sha256(body_bytes).hexdigest(),
                       "bytes": len(body_bytes)}],
        "governance": GOVERNANCE,
    }
    address = canonical_sha256(manifest)
    manifest["supplementSha256"] = address
    directory = root / "nfl-labs-supplements" / "sha256" / address
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    (directory / "supplement.json").write_bytes(body_bytes)
    (root / "nfl-labs-supplements" / "nfl-labs-supplement-index.json").write_text(
        json.dumps({"schemaVersion": INDEX_SCHEMA, "currentSupplementSha256s": [address]}),
        encoding="utf-8",
    )
    return directory


def _valid_summaries() -> dict:
    return {"jointScoreDistribution": JOINT, "homeWinProbability": 0.45,
            "awayWinProbability": 0.35, "tieProbability": 0.20}


def test_joint_supplement_imports_once_and_binds_to_the_registered_forecast(session, tmp_path):
    _register_forecast(session)
    directory = _write_supplement(tmp_path, summaries=_valid_summaries())
    verified = verify_supplement(directory)
    first = register_supplement(session, verified)
    second = register_supplement(session, verified)
    assert first["idempotent_reuse"] is False and second["idempotent_reuse"] is True
    assert session.scalar(select(func.count()).select_from(ResearchSupplement)) == 1
    row = session.scalar(select(ResearchSupplement))
    assert row.kind == KIND_JOINT and row.event_id == EVENT and row.forecast_id == FORECAST_ID
    assert row.shadow_forecast_id is not None and row.simulation_count == 10000


def test_supplement_with_probabilities_not_its_own_is_refused(tmp_path):
    summaries = _valid_summaries()
    summaries["homeWinProbability"] = 0.60  # not what the cells say
    directory = _write_supplement(tmp_path, summaries=summaries)
    with pytest.raises(SupplementImportError, match="not the joint distribution's own"):
        verify_supplement(directory)


def test_supplement_bound_to_an_unknown_forecast_writes_nothing(session, tmp_path):
    _register_forecast(session)
    directory = _write_supplement(tmp_path, summaries=_valid_summaries(), forecast_id="not-held")
    with pytest.raises(SupplementImportError, match="not registered"):
        register_supplement(session, verify_supplement(directory))
    assert session.scalar(select(func.count()).select_from(ResearchSupplement)) == 0


def test_nfl_market_arithmetic_reads_one_distribution() -> None:
    cells = arithmetic.cells_from_distribution(JOINT)
    outcomes = arithmetic.outcome_probabilities(cells)
    assert outcomes == pytest.approx({"home": 0.45, "away": 0.35, "tie": 0.20})
    # Two-way moneyline: the tie voids, so the comparable probability is conditional.
    assert arithmetic.conditional_non_tie(0.45, 0.20) == pytest.approx(0.5625)
    assert arithmetic.conditional_non_tie(0.35, 0.20) == pytest.approx(0.4375)
    # Spread at home -3: 24-20 covers (+1), 17-20 does not, 20-20 does not; at home +3
    # the 17-20 cell pushes exactly and is neither side.
    assert arithmetic.spread_query(cells, home_line=-3.0) == pytest.approx(
        {"home_cover": 0.45, "away_cover": 0.55, "push": 0.0})
    assert arithmetic.spread_query(cells, home_line=3.0) == pytest.approx(
        {"home_cover": 0.65, "away_cover": 0.0, "push": 0.35})
    # Total 40: 20-20 pushes.
    assert arithmetic.total_query(cells, line=40.0) == pytest.approx(
        {"over": 0.45, "under": 0.35, "push": 0.20})
    assert arithmetic.total_query(cells, line=40.5) == pytest.approx(
        {"over": 0.45, "under": 0.55, "push": 0.0})


def test_same_quoted_price_accepts_canonical_rounding_and_rejects_a_different_price() -> None:
    full = SimpleNamespace(american_odds=-115, decimal_odds=1.8695652173913042)
    decision = SimpleNamespace(american_odds=-115, decimal_odds=1.8696)
    assert same_quoted_price(full, decision) is True
    # Same rounded decimal but a different American price is a different quote.
    assert same_quoted_price(SimpleNamespace(american_odds=-116, decimal_odds=1.8696), decision) is False
    # Same American field absent on one side: the decimal must still agree at four places.
    assert same_quoted_price(SimpleNamespace(american_odds=None, decimal_odds=1.8700), decision) is False
    assert same_quoted_price(SimpleNamespace(american_odds=-115, decimal_odds=None), decision) is False


@pytest.mark.asyncio
async def test_minus_115_quote_now_creates_the_canonical_position(session, settings, monkeypatch):
    """Isolated database: the exact production flow with a price needing >4 decimals."""

    from app.services.shadow_experiment_service import ShadowExperimentService
    from app.models import OfficialAspPosition, OddsSnapshot
    from tests.test_shadow_experiment import (
        _add_home_value, _append_then_position, _live_slate, _opportunities, _start,
    )

    games, event_date, now, start = await _live_slate(session, settings)
    outcome, snapshot, book = _add_home_value(session, games[0], observed_at=now, american=-115)
    assert round(float(snapshot.decimal_odds), 4) != float(snapshot.decimal_odds)
    _opportunities(monkeypatch, {games[0].id: (outcome, snapshot, book)})
    service = ShadowExperimentService(session, settings)
    experiment = _start(service, event_date, now)
    _, positioned = _append_then_position(service, event_date, now, start)
    assert positioned["primary"] == 1
    position = session.scalar(select(OfficialAspPosition).where(
        OfficialAspPosition.shadow_experiment_id == experiment.id))
    assert position is not None and position.odds_snapshot_id == snapshot.id
    assert round(float(position.decimal_odds), 4) == round(float(snapshot.decimal_odds), 4)

    # A genuinely different price on the same outcome does not bind: a later
    # -125 snapshot is not the decision's quote and creates nothing more.
    later = OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=book.id,
        observed_at=now + timedelta(minutes=1), available_as_of=now + timedelta(minutes=1),
        american_odds=-125, decimal_odds=1.8, source="SYNTHETIC_TEST_BOOK", source_mode="LIVE",
    )
    session.add(later)
    session.commit()
    decision = session.get(type(session.scalar(select(OfficialAspPosition))), position.id)
    assert same_quoted_price(later, SimpleNamespace(
        american_odds=snapshot.american_odds, decimal_odds=round(float(snapshot.decimal_odds), 4))) is False
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 1
    assert decision is not None


def test_lab_study_imports_from_a_byte_verified_handoff(session, tmp_path):
    handoff = {
        "contract": "ASP_NFL_OVERNIGHT_MODEL_LAB_HANDOFF_V1", "runId": "run-1",
        "status": "RESEARCH_SHADOW", "promotionStatus": "NOT_PROMOTED",
        "executionAllowedInAisp": False, "wageringAuthority": False, "finalOotAccessed": False,
        "developmentSelectedPipeline": "HGB_B_T2",
        "leaderboard": [{"pipelineId": "HGB_B_T2", "games": 1408, "multiclassLogLoss": 0.6748},
                        {"pipelineId": "M1_RECIPE_FOLD_REFIT", "games": 1408, "multiclassLogLoss": 0.6749}],
        "pairedUncertaintyVersusM1": {"candidateMinusM1LogLoss": -0.0032, "clusterBootstrap95Ci": [-0.0094, 0.0025]},
        "calibration": {"method": "X", "path": "secret", "folds": [1, 2]},
    }
    raw = json.dumps(handoff).encode("utf-8")
    manifest_sha = "c" * 64
    handoff["externalManifestSha256"] = manifest_sha
    raw = json.dumps(handoff).encode("utf-8")
    lab = tmp_path / "lab"
    (lab / "sha256" / manifest_sha).mkdir(parents=True)
    (lab / "sha256" / manifest_sha / "handoff.json").write_bytes(raw)
    (lab / "current.json").write_text(json.dumps({
        "contract": "ASP_NFL_OVERNIGHT_MODEL_LAB_CURRENT_V1", "manifestSha256": manifest_sha,
        "handoffSha256": hashlib.sha256(raw).hexdigest(),
    }), encoding="utf-8")

    first = register_lab_study(session, lab)
    assert first["idempotent_reuse"] is False and first["leaderboard_rows"] == 2
    assert register_lab_study(session, lab)["idempotent_reuse"] is True
    row = session.scalar(select(ResearchSupplement))
    assert row.kind == "MODEL_BENCHMARK_STUDY"
    assert row.payload["summaries"]["benchmarkTimingClass"] == "NONE"
    assert "path" not in row.payload["summaries"]["calibration"]

    # Tampered bytes are refused before parsing.
    (lab / "sha256" / manifest_sha / "handoff.json").write_bytes(raw + b" ")
    with pytest.raises(LabStudyImportError, match="do not hash"):
        register_lab_study(session, lab)
