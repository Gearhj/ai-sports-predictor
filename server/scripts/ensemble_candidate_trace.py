"""Trace a candidate package end to end, against an isolated database.

Builds a FIXTURE package, runs it through the real verifier and the real
registrar, then renders the real comparison read model. Nothing here touches
the live database, calls a provider or executes a model: the whole trace runs
against an in-memory SQLite instance created and discarded by this script.

Its purpose is to prove the AISP side of the path works before Model Studio's
package exists. A fixture is not evidence about the candidate's quality and is
labelled as a fixture everywhere it appears.

Usage:
    python server/scripts/ensemble_candidate_trace.py [--replay] [--no-duration]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.models import (  # noqa: E402
    Game, GameStatus, GameTeam, League, Season, ShadowForecast,
    ShadowForecastBundle, Sport, Team,
)
from app.shadow_import.mlb_ensemble_candidate import (  # noqa: E402
    ENGINE_ID as CANDIDATE_ENGINE, HISTORICAL_REPLAY, PROSPECTIVE,
    register_package, verify_package,
)
from app.shadow_import.mlb_ensemble_candidate_read import candidate_comparison  # noqa: E402
from app.models.entities import ModelChampionAuthority  # noqa: E402
from app.shadow_import.mlb_v18 import ENGINE_ID as BASELINE_ENGINE  # noqa: E402

START = datetime(2026, 9, 12, 23, 10, tzinfo=UTC)
GAME_PK = "825101"
BASELINE_ARTIFACT = "44ec4f457741f1aaeff42b81af1be5fd2712e494709ff1a0a8f0742b88d71c8b"
CHAMPION_MANIFEST = "3e8dcc1c50dcdfd2d748e5e0de8e7595ad0b50a2c62eb9baadb4ffe9e5d640c2"
FROZEN_BINDINGS = "6297787b57e7e74436036e75b40cf67339115f0cbf6333406766179c9569a235"
TREATMENT_MANIFEST = "853fc330c98e56e987905980e06225190cbe8985c4c19f4c2b035c20dba2078f"
REGISTRATION_RECEIPT = "abe96ab97b3ea3215cc4c0656085a806123840968a1645a259adef002e2b11b8"
PARENT_BUNDLE_ID = "mlb-v18-whole-game-a6e7e99122f1b60701a4728b"
PARENT_BUNDLE_SHA = "a6e7e99122f1b60701a4728b97b32fcc11e03405bfd456ed6cd54f69b69adb27"
ACCEPTANCE_SHA = "291243d7abad7f972e08f7610715bf8e693a267ea37a7b6189256f98aa4a4a7b"
TRAINING_RUN = "stage3-model-development-run-8ff1e0a505da4482a0a67386754e28ff"
BASELINE_FORECAST_ID = "mlb-v18-daily-fixture-baseline"
BASELINE_NUMERICAL = "0f60c4030bc8ab611102669b4e9ce3b4c28b34c4344b7ffe1febdf4969b1c99f"
BASELINE_FEATURE_VECTOR = "016157c674d2e32d31bfde605516abce3e6726ee57f402137e76e0341ebae1a1"
BASELINE_SIMULATOR = "V18_WHOLE_GAME_MONTE_CARLO"
BASELINE_SIM_VERSION = "MLB_V18_DAILY_GAME_FORECAST_V1"
BASELINE_SEED = 20260907
BASELINE_SIM_COUNT = 5000
BASELINE_CUTOFF = START - timedelta(hours=3, minutes=8)

FIXTURE_BANNER = (
    "FIXTURE PACKAGE -- built by this script, not by Model Studio. It exercises "
    "the AISP path; it says nothing about the candidate's quality."
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canon(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def build_fixture(
    root: Path, *, artifact_type: str, duration_ran: bool, canonical_game_id: str,
) -> Path:
    published = (
        START - timedelta(hours=2) if artifact_type == PROSPECTIVE
        else START + timedelta(hours=6)
    )
    forecast = {
        "schemaVersion": "MLB_ENSEMBLE_CANDIDATE_GAME_FORECAST_V1",
        "artifactType": artifact_type,
        "forecastId": f"mlb-ensemble-fixture-{artifact_type.lower()}-{GAME_PK}",
        "eventId": f"ensemble-fixture-{GAME_PK}",
        "sourceGameId": GAME_PK,
        "canonicalGameId": canonical_game_id,
        "baselineForecastId": BASELINE_FORECAST_ID,
        "baselineBinding": {
            "parentBundleSha256": PARENT_BUNDLE_SHA,
            "acceptanceProtocolSha256": ACCEPTANCE_SHA,
            "numericalArtifactSha256": BASELINE_NUMERICAL,
            "featureVectorSha256": BASELINE_FEATURE_VECTOR,
            "simulatorId": BASELINE_SIMULATOR,
            "simulatorVersion": BASELINE_SIM_VERSION,
            "simulatorSeed": BASELINE_SEED,
            "simulationCount": BASELINE_SIM_COUNT,
            "sourceCutoffUtc": BASELINE_CUTOFF.isoformat(),
        },
        "homeTeam": "MIA", "awayTeam": "LAD", "season": 2026,
        "capturedAt": (published - timedelta(minutes=9)).isoformat(),
        "publishedAt": published.isoformat(),
        "scheduledStart": START.isoformat(),
        "snapshotVersion": 1,
        "probabilities": {"homeWin": 0.5812, "awayWin": 0.4188},
        "expectations": {
            "homeRuns": 4.91, "awayRuns": 4.02,
            "totalRuns": 8.93, "homeMinusAwayRuns": 0.89,
        },
        "distributions": {
            "homeRunDistribution": {"0": 0.10, "4": 0.50, "9": 0.40},
            "awayRunDistribution": {"0": 0.20, "4": 0.50, "8": 0.30},
            "totalRunDistribution": {"4": 0.30, "8": 0.40, "15": 0.30},
            "runDifferentialDistribution": {"-4": 0.30, "0": 0.30, "5": 0.40},
        },
        "simulation": {
            "simulatorId": "MLB_ENSEMBLE_MC", "simulatorVersion": "1",
            "seed": 4242, "count": 5000, "simulationCount": 5000,
        },
        "eligibility": {"eligible": True, "reason": None},
        "componentsApplied": [
            {"componentId": "V18_WHOLE_GAME", "ran": True, "fallbackReason": None},
            {"componentId": "CALIBRATION_ISOTONIC_V3", "ran": True, "fallbackReason": None},
            {
                "componentId": "STARTER_DURATION_ADAPTER",
                "ran": duration_ran,
                "fallbackReason": None if duration_ran
                else "No fitted starter-duration curve for either probable starter",
                "detail": None if duration_ran else "baseline bullpen template used instead",
            },
        ],
    }

    staging = root / "objects" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    forecast_bytes = _canon(forecast)
    (staging / "forecast.json").write_bytes(forecast_bytes)

    manifest = {
        "schemaVersion": "MLB_ENSEMBLE_CANDIDATE_OBJECT_MANIFEST_V1",
        "sourceGameId": GAME_PK,
        "artifacts": [{
            "relativePath": "forecast.json",
            "sha256": _sha(forecast_bytes),
            "bytes": len(forecast_bytes),
        }],
    }
    object_sha = _sha(_canon(manifest))
    manifest["objectSha256"] = object_sha
    final = root / "objects" / object_sha
    staging.rename(final)
    (final / "manifest.json").write_bytes(_canon(manifest))

    handoff = {
        "schemaVersion": "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V1",
        "bundleId": f"mlb-ensemble-candidate-fixture-{artifact_type.lower()}",
        "slateDate": START.date().isoformat(),
        "generatedAt": published.isoformat(),
        "artifactType": artifact_type,
        "executionAllowed": False, "wageringAuthority": False, "activatesModel": False,
        "candidate": {
            "engineId": CANDIDATE_ENGINE,
            "engineVersion": "mlb-ensemble-rc1-FIXTURE",
            "modelArtifactSha256": "b" * 64,
            "manifestSha256": "e" * 64,
        },
        "baseline": {
            "engineId": BASELINE_ENGINE,
            "engineVersion": "mlb-v18-whole-game-a6e7e99122f1b60701a4728b",
            "modelArtifactSha256": BASELINE_ARTIFACT,
            "championManifestSha256": CHAMPION_MANIFEST,
            "frozenBindingsSha256": FROZEN_BINDINGS,
            "treatmentManifestSha256": TREATMENT_MANIFEST,
            "registrationReceiptSha256": REGISTRATION_RECEIPT,
            "parentBundleId": PARENT_BUNDLE_ID,
            "trainingRunId": TRAINING_RUN,
        },
        "components": [
            {"componentId": "V18_WHOLE_GAME", "role": "BASE",
             "sha256": BASELINE_ARTIFACT, "status": "FROZEN"},
            {"componentId": "CALIBRATION_ISOTONIC_V3", "role": "CALIBRATION",
             "sha256": "c" * 64, "status": "TESTED"},
            {"componentId": "STARTER_DURATION_ADAPTER", "role": "DURATION",
             "sha256": "d" * 64, "status": "SUPPORTED"},
        ],
        "forecasts": [{
            "sourceGameId": GAME_PK, "objectSha256": object_sha,
            "relativePath": f"objects/{object_sha}",
            "manifestFile": "manifest.json", "forecastFile": "forecast.json",
        }],
    }
    handoff["handoffSha256"] = _sha(_canon(handoff))
    path = root / "handoff.json"
    path.write_bytes(_canon(handoff))
    return path


def seed_baseline(session: Session) -> Game:
    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=START - timedelta(days=160), ends_at=START + timedelta(days=30))
    home = Team(league_id=league.id, name="Miami Marlins", abbreviation="MIA")
    away = Team(league_id=league.id, name="Los Angeles Dodgers", abbreviation="LAD")
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
        bundle_sha256="a" * 64, bundle_id="v18-fixture-baseline",
        logical_uri="fixture://v18", source_fixture_id="fixture",
        slate_date=START.date(), league="MLB", sport="MLB",
        status="RESEARCH_SHADOW", research_shadow_only=True,
        generated_at=START - timedelta(hours=6),
        forecast_count=1, quote_count=0, candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    session.add(ShadowForecast(
        bundle_id=bundle.id, forecast_id="mlb-v18-daily-fixture-baseline",
        engine_id=BASELINE_ENGINE,
        engine_version="mlb-v18-whole-game-a6e7e99122f1b60701a4728b",
        model_artifact_sha256=BASELINE_ARTIFACT,
        model_manifest_sha256=BASELINE_NUMERICAL,
        feature_vector_sha256=BASELINE_FEATURE_VECTOR,
        simulator_id=BASELINE_SIMULATOR, simulator_version=BASELINE_SIM_VERSION,
        simulator_seed=BASELINE_SEED,
        event_id="v18-fixture", source_event_id=GAME_PK,
        event_date=START.date(), season=2026, canonical_game_id=game.id,
        home_team_id="MIA", away_team_id="LAD",
        snapshot_type="PROSPECTIVE", snapshot_version=1,
        is_ready=True, readiness_score=1.0, readiness_threshold=1.0,
        status="PROSPECTIVE_RESEARCH_SHADOW",
        scheduled_start_utc=START,
        available_as_of_utc=START - timedelta(hours=3),
        source_cutoff_utc=BASELINE_CUTOFF,
        forecast_cutoff_utc=START - timedelta(hours=3),
        full_home_win_probability=0.5512, full_away_win_probability=0.4488,
        full_expected_home_score=4.71, full_expected_away_score=4.18,
        distributions={}, uncertainty={"simulationCount": BASELINE_SIM_COUNT},
        readiness_detail={},
        lineage_detail={
            "parentBundleId": PARENT_BUNDLE_ID,
            "parentBundleSha256": PARENT_BUNDLE_SHA,
            "acceptanceProtocolV2Sha256": ACCEPTANCE_SHA,
        },
    ))
    session.add(ModelChampionAuthority(
        handoff_sha256="f" * 64,
        schema_version="AISP_MLB_V18_CONDITIONAL_CHAMPION_HANDOFF_V1",
        sport="MLB", engine_id=BASELINE_ENGINE,
        model_name="MLB_V18_HGB_PA", model_version="V18",
        model_artifact_sha256=BASELINE_ARTIFACT,
        champion_manifest_sha256=CHAMPION_MANIFEST,
        frozen_bindings_sha256=FROZEN_BINDINGS,
        parent_bundle_id=PARENT_BUNDLE_ID, training_run_id=TRAINING_RUN,
        model_era_id="ERA_TRACE_FIXTURE", effective_at=START - timedelta(days=1),
        historical_validation_under_review=True,
        execution_allowed_in_aisp=False, real_money_execution_allowed=False,
        gen1_fallback_allowed=False, position_backfill_allowed=False,
        minimum_adjusted_edge=0.02,
        recommendation_policy_version="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
        staking_policy_version="AISP_QUARTER_KELLY_V1",
        state="ARMED", imported_at=START - timedelta(days=1),
        evidence={"development_integrity_sha256": TREATMENT_MANIFEST,
                  "registration_receipt_sha256": REGISTRATION_RECEIPT},
    ))
    session.flush()
    return game


def main() -> int:
    parser = argparse.ArgumentParser(description="trace a candidate package")
    parser.add_argument("--replay", action="store_true",
                        help="build a labelled HISTORICAL_REPLAY package instead")
    parser.add_argument("--no-duration", action="store_true",
                        help="make the starter-duration adapter fall back")
    args = parser.parse_args()
    artifact_type = HISTORICAL_REPLAY if args.replay else PROSPECTIVE

    print(FIXTURE_BANNER)
    print("=" * 78)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with tempfile.TemporaryDirectory() as tmp, factory() as session:
        root = Path(tmp)
        game = seed_baseline(session)

        print("\n1. STUDIO -> PACKAGE")
        handoff_path = build_fixture(
            root, artifact_type=artifact_type,
            duration_ran=not args.no_duration,
            canonical_game_id=str(game.id),
        )
        print(f"   handoff: {handoff_path}")

        print("\n2. EXCHANGE VERIFICATION")
        verified = verify_package(handoff_path)
        print(f"   handoffSha256   {verified.handoff_sha256}")
        print(f"   artifactType    {verified.artifact_type}")
        print(f"   verified games  {len(verified.forecasts)}   rejected {len(verified.rejected)}")
        for item in verified.forecasts:
            print(f"   object          {item.object_sha256}  game {item.source_game_id}")

        print("\n3. AISP IMPORT")
        first = register_package(session, verified)
        print(f"   imported        {first.imported}")
        print(f"   bundle created  {first.bundle_created}")

        print("\n4. IDEMPOTENT RE-IMPORT")
        second = register_package(session, verified)
        print(f"   imported        {second.imported or '[] (nothing new)'}")
        print(f"   already present {second.already_present}")
        print(f"   bundle created  {second.bundle_created}")
        rows = session.query(ShadowForecast).filter(
            ShadowForecast.engine_id == CANDIDATE_ENGINE).count()
        print(f"   candidate rows  {rows}")

        print("\n5. VISIBLE COMPARISON")
        payload = candidate_comparison(session, game.id)
        if payload is None:
            print("   no candidate comparison was produced")
            return 1
        baseline = payload["baseline"]
        candidate = payload["candidate"]
        print(f"   artifact type   {payload['artifact_type']} "
              f"(prospective_eligible={payload['prospective_eligible']})")
        print(f"   baseline        {baseline['home_win_probability']}%  "
              f"{baseline['expected_away_runs']}-{baseline['expected_home_runs']} (away-home)")
        print(f"   candidate       {candidate['home_win_probability']}%  "
              f"{candidate['expected_away_runs']}-{candidate['expected_home_runs']} (away-home)")
        if payload["difference"]:
            diff = payload["difference"]
            print(f"   difference      {diff['home_win_probability_points']:+} pts home win, "
                  f"{diff['expected_home_runs']:+} home runs ({diff['convention']})")
        print("   components:")
        for component in payload["components"]:
            state = "ran" if component["ran"] else "DID NOT RUN"
            note = f"  fallback: {component['fallback_reason']}" if component["fallback_reason"] else ""
            print(f"     - {component['component_id']:30} {component['role'] or '-':12} {state}{note}")
        print(f"   attribution     {payload['component_attribution'] or 'not supplied'}")
        if payload["component_attribution_note"]:
            print(f"                   {payload['component_attribution_note'][:100]}...")
        print(f"   controlling     {payload['authority']['controlling_engine_for_official_decisions']}")
        print(f"   candidate score {candidate['score'] or 'not scored yet'}")

        print("\n6. SCORER TREATMENT")
        from app.services.forecast_evaluation import ForecastEvaluationService

        frozen = ForecastEvaluationService(session).freeze_eligible(canonical_game_id=game.id)
        eligible = sorted(frozen)
        print(f"   eligible identities: {eligible}")
        excluded = [
            (entry["forecast_identity"], entry["reason"])
            for selection in frozen.values() for entry in selection.excluded
        ]
        if artifact_type == HISTORICAL_REPLAY:
            has_candidate = any(CANDIDATE_ENGINE in key for key in eligible)
            print(f"   candidate eligible for prospective scoring: {has_candidate} "
                  "(a replay must be False)")
        for identity, reason in excluded:
            print(f"   excluded: {identity} -> {reason}")

        session.rollback()

    print("\n" + "=" * 78)
    print(FIXTURE_BANNER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
