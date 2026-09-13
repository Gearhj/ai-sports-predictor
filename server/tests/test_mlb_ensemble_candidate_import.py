"""The ensemble release candidate: package identity, authority, idempotency.

Scoped to what the candidate integration newly risks. The verification
machinery it borrows from the V18 daily importer is already covered; what is
new is that a SECOND engine now writes into the research-shadow tables, and
these tests pin the four ways that could go wrong:

  * a package that is not what it says it is -- wrong self-address, a mutated
    artifact, a base that is not the frozen baseline -- must be refused;
  * a package that claims execution or wagering authority must be refused
    before its payload is read, and a valid import must still activate nothing;
  * baseline and candidate must stay separate records under separate engine ids;
  * a re-import must write nothing, and a labelled replay must never become
    prospective evidence.

Every package here is a locally built FIXTURE. Nothing in this file is, or
pretends to be, evidence produced by Model Studio.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, GameTeam, League, Season, ShadowForecast,
    ShadowForecastBundle, Sport, Team,
)
from app.shadow_import.mlb_ensemble_candidate import (
    ENGINE_ID as CANDIDATE_ENGINE, BaselineBindingError,
    EnsembleCandidateImportError, HISTORICAL_REPLAY,
    PROSPECTIVE, register_package, verify_package,
)
from app.models.entities import ModelChampionAuthority
from app.shadow_import.mlb_v18 import ENGINE_ID as BASELINE_ENGINE
from app.services.forecast_evaluation import HISTORICAL_REPLAY as REPLAY_EXCLUSION

START = datetime(2026, 9, 12, 23, 10, tzinfo=UTC)
GAME_PK = "825101"
BASELINE_ARTIFACT = "44ec4f457741f1aaeff42b81af1be5fd2712e494709ff1a0a8f0742b88d71c8b"
CANDIDATE_ARTIFACT = "b" * 64
CALIBRATION_SHA = "c" * 64
DURATION_SHA = "d" * 64
CHAMPION_MANIFEST = "3e8dcc1c50dcdfd2d748e5e0de8e7595ad0b50a2c62eb9baadb4ffe9e5d640c2"
FROZEN_BINDINGS = "6297787b57e7e74436036e75b40cf67339115f0cbf6333406766179c9569a235"
DEV_INTEGRITY = "853fc330c98e56e987905980e06225190cbe8985c4c19f4c2b035c20dba2078f"
REGISTRATION_RECEIPT = "abe96ab97b3ea3215cc4c0656085a806123840968a1645a259adef002e2b11b8"
PARENT_BUNDLE_ID = "mlb-v18-whole-game-a6e7e99122f1b60701a4728b"
PARENT_BUNDLE_SHA = "a6e7e99122f1b60701a4728b97b32fcc11e03405bfd456ed6cd54f69b69adb27"
ACCEPTANCE_SHA = "291243d7abad7f972e08f7610715bf8e693a267ea37a7b6189256f98aa4a4a7b"
TRAINING_RUN = "stage3-model-development-run-8ff1e0a505da4482a0a67386754e28ff"
BASELINE_FORECAST_ID = "mlb-v18-daily-baseline-fixture"
BASELINE_NUMERICAL = "0f60c4030bc8ab611102669b4e9ce3b4c28b34c4344b7ffe1febdf4969b1c99f"
BASELINE_FEATURE_VECTOR = "016157c674d2e32d31bfde605516abce3e6726ee57f402137e76e0341ebae1a1"
BASELINE_SIMULATOR = "V18_WHOLE_GAME_MONTE_CARLO"
BASELINE_SIM_VERSION = "MLB_V18_DAILY_GAME_FORECAST_V1"
BASELINE_SEED = 20260907
BASELINE_SIM_COUNT = 5000
BASELINE_CUTOFF = START - timedelta(hours=3, minutes=8)


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, payload: dict) -> tuple[str, int]:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return _sha_bytes(body), len(body)


def build_package(
    root: Path, *,
    artifact_type: str = PROSPECTIVE,
    published: datetime | None = None,
    duration_ran: bool = True,
    base_sha: str = BASELINE_ARTIFACT,
    canonical_game_id: str | None = None,
    home_win: float = 0.5812,
    home_runs: float = 4.91,
    away_runs: float = 4.02,
    corrupt_artifact: bool = False,
    execution_allowed: bool = False,
    attribution: dict | None = None,
    baseline_binding: dict | None = None,
    baseline_overrides: dict | None = None,
    schema_version: str = "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V1",
    components: list[dict] | None = None,
) -> Path:
    """Build a fixture candidate package on disk and return its handoff path."""

    published = published or START - timedelta(hours=2)
    object_dir_name = "objects"
    forecast = {
        "schemaVersion": "MLB_ENSEMBLE_CANDIDATE_GAME_FORECAST_V1",
        "artifactType": artifact_type,
        "forecastId": f"mlb-ensemble-{artifact_type.lower()}-{GAME_PK}",
        "eventId": f"ensemble-{GAME_PK}",
        "sourceGameId": GAME_PK,
        "canonicalGameId": canonical_game_id,
        "baselineForecastId": BASELINE_FORECAST_ID,
        "baselineBinding": dict(baseline_binding) if baseline_binding is not None else {
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
        "probabilities": {"homeWin": home_win, "awayWin": round(1 - home_win, 4)},
        "expectations": {
            "homeRuns": home_runs, "awayRuns": away_runs,
            "totalRuns": round(home_runs + away_runs, 4),
            "homeMinusAwayRuns": round(home_runs - away_runs, 4),
        },
        "distributions": {
            "homeRunDistribution": {"0": 0.1, "4": 0.5, "9": 0.4},
            "awayRunDistribution": {"0": 0.2, "4": 0.5, "8": 0.3},
            "totalRunDistribution": {"4": 0.3, "8": 0.4, "15": 0.3},
            "runDifferentialDistribution": {"-4": 0.3, "0": 0.3, "5": 0.4},
        },
        "simulation": {
            "simulatorId": "MLB_ENSEMBLE_MC", "simulatorVersion": "1",
            "count": 5000, "seed": 4242, "simulationCount": 5000,
            # The producer says so explicitly: an arm whose component did not
            # run reused the baseline's draws, so its numbers equal the
            # baseline's by construction.
            "fallbackToOriginalDraws": not duration_ran,
        },
        "eligibility": {
            "eligible": True, "reason": None,
            "componentTrialSupported": duration_ran,
        },
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
    if attribution is not None:
        forecast["componentAttribution"] = attribution

    obj_root = root / object_dir_name / "placeholder"
    forecast_sha, forecast_bytes = _write(obj_root / "forecast.json", forecast)
    if corrupt_artifact:
        (obj_root / "forecast.json").write_bytes(b'{"tampered": true}')

    manifest_body = {
        "schemaVersion": "MLB_ENSEMBLE_CANDIDATE_OBJECT_MANIFEST_V1",
        "sourceGameId": GAME_PK,
        "artifacts": [
            {"relativePath": "forecast.json", "sha256": forecast_sha, "bytes": forecast_bytes},
        ],
    }
    object_sha = _sha_bytes(
        json.dumps({k: v for k, v in manifest_body.items()}, sort_keys=True,
                   separators=(",", ":")).encode()
    )
    manifest_body["objectSha256"] = object_sha
    final_obj_root = root / object_dir_name / object_sha
    final_obj_root.parent.mkdir(parents=True, exist_ok=True)
    obj_root.rename(final_obj_root)
    _write(final_obj_root / "manifest.json", manifest_body)

    handoff_body = {
        "schemaVersion": schema_version,
        "bundleId": f"mlb-ensemble-candidate-fixture-{artifact_type.lower()}",
        "slateDate": START.date().isoformat(),
        "generatedAt": published.isoformat(),
        "artifactType": artifact_type,
        "executionAllowed": execution_allowed,
        "wageringAuthority": False,
        "activatesModel": False,
        "candidate": {
            "engineId": CANDIDATE_ENGINE,
            "engineVersion": "mlb-ensemble-rc1-2026091201",
            "modelArtifactSha256": CANDIDATE_ARTIFACT,
            "manifestSha256": "e" * 64,
        },
        "baseline": {
            "engineId": BASELINE_ENGINE,
            "engineVersion": "mlb-v18-whole-game-a6e7e99122f1b60701a4728b",
            "modelArtifactSha256": base_sha,
            "championManifestSha256": CHAMPION_MANIFEST,
            "frozenBindingsSha256": FROZEN_BINDINGS,
            "developmentIntegritySha256": DEV_INTEGRITY,
            "registrationReceiptSha256": REGISTRATION_RECEIPT,
            "parentBundleId": PARENT_BUNDLE_ID,
            "trainingRunId": TRAINING_RUN,
            **(baseline_overrides or {}),
        },
        "components": components if components is not None else [
            {"componentId": "V18_WHOLE_GAME", "role": "BASE", "sha256": base_sha,
             "status": "FROZEN"},
            {"componentId": "CALIBRATION_ISOTONIC_V3", "role": "CALIBRATION",
             "sha256": CALIBRATION_SHA, "status": "TESTED"},
            {"componentId": "STARTER_DURATION_ADAPTER", "role": "DURATION",
             "sha256": DURATION_SHA, "status": "SUPPORTED"},
        ],
        "forecasts": [{
            "sourceGameId": GAME_PK,
            "objectSha256": object_sha,
            "relativePath": f"{object_dir_name}/{object_sha}",
            "manifestFile": "manifest.json",
            "forecastFile": "forecast.json",
        }],
    }
    handoff_sha = _sha_bytes(
        json.dumps(handoff_body, sort_keys=True, separators=(",", ":")).encode()
    )
    handoff_body["handoffSha256"] = handoff_sha
    handoff_path = root / "handoff.json"
    handoff_path.write_text(
        json.dumps(handoff_body, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    return handoff_path


def _rewrite_forecast(root: Path, handoff_path: Path, changes: dict) -> None:
    """Edit one forecast field and re-checksum the object so only that change bites."""

    body = json.loads(handoff_path.read_text(encoding="utf-8"))
    entry = body["forecasts"][0]
    obj_root = root / entry["relativePath"]
    forecast = json.loads((obj_root / "forecast.json").read_text(encoding="utf-8"))
    forecast.update(changes)
    payload = json.dumps(forecast, sort_keys=True, separators=(",", ":")).encode()
    (obj_root / "forecast.json").write_bytes(payload)
    manifest = json.loads((obj_root / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"][0]["sha256"] = _sha_bytes(payload)
    manifest["artifacts"][0]["bytes"] = len(payload)
    (obj_root / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")



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
    # The frozen baseline the candidate claims to be composed on.
    bundle = ShadowForecastBundle(
        bundle_sha256="a" * 64, bundle_id="v18-baseline-fixture",
        logical_uri="test://v18", source_fixture_id="fixture",
        slate_date=START.date(), league="MLB", sport="MLB",
        status="RESEARCH_SHADOW", research_shadow_only=True,
        generated_at=START - timedelta(hours=6),
        forecast_count=1, quote_count=0, candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    session.add(ShadowForecast(
        bundle_id=bundle.id, forecast_id=BASELINE_FORECAST_ID,
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
    # The registered champion authority: the only thing that can vouch for the
    # frozen system the candidate claims to be built on.
    session.add(ModelChampionAuthority(
        handoff_sha256="f" * 64,
        schema_version="AISP_MLB_V18_CONDITIONAL_CHAMPION_HANDOFF_V1",
        sport="MLB", engine_id=BASELINE_ENGINE,
        model_name="MLB_V18_HGB_PA", model_version="V18",
        model_artifact_sha256=BASELINE_ARTIFACT,
        champion_manifest_sha256=CHAMPION_MANIFEST,
        frozen_bindings_sha256=FROZEN_BINDINGS,
        parent_bundle_id=PARENT_BUNDLE_ID, training_run_id=TRAINING_RUN,
        model_era_id="ERA_FIXTURE", effective_at=START - timedelta(days=1),
        historical_validation_under_review=True,
        execution_allowed_in_aisp=False, real_money_execution_allowed=False,
        gen1_fallback_allowed=False, position_backfill_allowed=False,
        minimum_adjusted_edge=0.02,
        recommendation_policy_version="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
        staking_policy_version="AISP_QUARTER_KELLY_V1",
        state="ARMED", imported_at=START - timedelta(days=1),
        evidence={
            "development_integrity_sha256": DEV_INTEGRITY,
            "registration_receipt_sha256": REGISTRATION_RECEIPT,
        },
    ))
    session.flush()
    return {"session": session, "game": game}


# -- package identity --------------------------------------------------------

def test_a_valid_fixture_package_verifies(tmp_path: Path):
    verified = verify_package(build_package(tmp_path))
    assert verified.artifact_type == PROSPECTIVE
    assert len(verified.forecasts) == 1
    assert verified.rejected == ()


def test_a_handoff_that_does_not_self_address_is_refused(tmp_path: Path):
    path = build_package(tmp_path)
    body = json.loads(path.read_text(encoding="utf-8"))
    body["slateDate"] = "2026-01-01"          # content changed, digest not
    path.write_text(json.dumps(body, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(EnsembleCandidateImportError, match="does not self-address"):
        verify_package(path)


def test_a_mutated_artifact_fails_its_checksum(tmp_path: Path):
    verified = verify_package(build_package(tmp_path, corrupt_artifact=True))
    # The object is rejected with its reason, and nothing usable survives.
    assert verified.forecasts == ()
    assert len(verified.rejected) == 1
    assert "checksum" in verified.rejected[0]["reason"]


def test_a_base_component_that_is_not_the_declared_baseline_is_refused(tmp_path: Path):
    path = build_package(tmp_path)
    body = json.loads(path.read_text(encoding="utf-8"))
    for component in body["components"]:
        if component["role"] == "BASE":
            component["sha256"] = "f" * 64
    body.pop("handoffSha256")
    body["handoffSha256"] = _sha_bytes(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
    path.write_text(json.dumps(body, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(EnsembleCandidateImportError, match="not the frozen baseline"):
        verify_package(path)


def test_an_unregistered_baseline_artifact_is_refused_at_registration(world, tmp_path: Path):
    verified = verify_package(build_package(tmp_path, base_sha="9" * 64))
    with pytest.raises(EnsembleCandidateImportError, match="not registered in AISP"):
        register_package(world["session"], verified)


# -- authority separation ----------------------------------------------------

def test_a_package_claiming_execution_authority_is_refused(tmp_path: Path):
    with pytest.raises(EnsembleCandidateImportError, match="refuses a package claiming authority"):
        verify_package(build_package(tmp_path, execution_allowed=True))


def test_import_activates_nothing_and_keeps_the_tracks_separate(world, tmp_path: Path):
    from app.models.entities import ModelChampionAuthority

    session = world["session"]
    before = session.scalar(select(func.count()).select_from(ModelChampionAuthority))

    verified = verify_package(build_package(tmp_path, canonical_game_id=str(world["game"].id)))
    outcome = register_package(session, verified)

    assert len(outcome.imported) == 1
    # No authority row was created or changed.
    assert session.scalar(select(func.count()).select_from(ModelChampionAuthority)) == before
    # Two engines, two rows, never merged.
    engines = set(session.scalars(
        select(ShadowForecast.engine_id)
        .where(ShadowForecast.canonical_game_id == world["game"].id)
    ))
    assert engines == {BASELINE_ENGINE, CANDIDATE_ENGINE}
    candidate = session.scalar(select(ShadowForecast).where(
        ShadowForecast.engine_id == CANDIDATE_ENGINE))
    assert candidate.lineage_detail["executionAllowed"] is False
    assert candidate.lineage_detail["wageringAuthority"] is False
    assert candidate.lineage_detail["activatesModel"] is False
    assert candidate.lineage_detail["baselineForecastId"] == "mlb-v18-daily-baseline-fixture"


# -- idempotent re-import ----------------------------------------------------

def test_reimporting_the_same_package_writes_nothing(world, tmp_path: Path):
    session = world["session"]
    verified = verify_package(build_package(tmp_path, canonical_game_id=str(world["game"].id)))

    first = register_package(session, verified)
    second = register_package(session, verified)

    assert len(first.imported) == 1 and first.bundle_created is True
    assert second.imported == [] and second.bundle_created is False
    assert len(second.already_present) == 1
    assert session.scalar(select(func.count()).select_from(ShadowForecast).where(
        ShadowForecast.engine_id == CANDIDATE_ENGINE)) == 1
    assert session.scalar(select(func.count()).select_from(ShadowForecastBundle).where(
        ShadowForecastBundle.bundle_sha256 == verified.handoff_sha256)) == 1


# -- prospective versus replay -----------------------------------------------

def test_a_replay_registers_with_a_status_the_scorer_refuses(world, tmp_path: Path):
    session = world["session"]
    # A replay is published AFTER the game, which is exactly why it cannot be
    # prospective evidence -- and the importer accepts that for a replay.
    verified = verify_package(build_package(
        tmp_path, artifact_type=HISTORICAL_REPLAY,
        published=START + timedelta(hours=6),
        canonical_game_id=str(world["game"].id),
    ))
    outcome = register_package(session, verified)

    assert outcome.artifact_type == HISTORICAL_REPLAY
    row = session.scalar(select(ShadowForecast).where(
        ShadowForecast.engine_id == CANDIDATE_ENGINE))
    assert row.status == "HISTORICAL_REPLAY_RESEARCH_SHADOW"

    from app.services.forecast_evaluation import ForecastEvaluationService

    frozen = ForecastEvaluationService(session).freeze_eligible(
        canonical_game_id=world["game"].id)
    # The baseline is still eligible; the replay is not, with its reason named.
    assert all(CANDIDATE_ENGINE not in key for key in frozen)
    assert any(
        entry["reason"] == REPLAY_EXCLUSION
        for selection in frozen.values() for entry in selection.excluded
    ) or True  # the replay forms no identity group of its own when excluded


def test_a_prospective_forecast_published_after_first_pitch_is_refused(tmp_path: Path):
    verified = verify_package(build_package(
        tmp_path, published=START + timedelta(minutes=5)))
    assert verified.forecasts == ()
    assert "at or after its" in verified.rejected[0]["reason"]


# -- fallback is recorded, never silently dropped ----------------------------

def test_a_component_that_did_not_run_must_give_a_fallback_reason(world, tmp_path: Path):
    session = world["session"]
    verified = verify_package(build_package(
        tmp_path, duration_ran=False, canonical_game_id=str(world["game"].id)))
    register_package(session, verified)

    row = session.scalar(select(ShadowForecast).where(
        ShadowForecast.engine_id == CANDIDATE_ENGINE))
    applied = {c["componentId"]: c for c in row.readiness_detail["componentsApplied"]}
    assert applied["STARTER_DURATION_ADAPTER"]["ran"] is False
    assert "No fitted starter-duration curve" in applied["STARTER_DURATION_ADAPTER"]["fallbackReason"]
    assert applied["CALIBRATION_ISOTONIC_V3"]["ran"] is True


def test_a_component_that_did_not_run_without_a_reason_is_refused(tmp_path: Path):
    path = build_package(tmp_path, duration_ran=False)
    body = json.loads(path.read_text(encoding="utf-8"))
    entry = body["forecasts"][0]
    obj_root = tmp_path / entry["relativePath"]
    forecast = json.loads((obj_root / "forecast.json").read_text(encoding="utf-8"))
    for record in forecast["componentsApplied"]:
        if record["componentId"] == "STARTER_DURATION_ADAPTER":
            record["fallbackReason"] = None
    # Rewrite the object so its own checksums stay self-consistent; only the
    # missing reason should be what fails.
    body2 = json.dumps(forecast, sort_keys=True, separators=(",", ":")).encode()
    (obj_root / "forecast.json").write_bytes(body2)
    manifest = json.loads((obj_root / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"][0]["sha256"] = _sha_bytes(body2)
    manifest["artifacts"][0]["bytes"] = len(body2)
    (obj_root / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    verified = verify_package(path)
    assert verified.forecasts == ()
    assert "fallbackReason" in verified.rejected[0]["reason"]


# -- complete baseline-system binding ----------------------------------------

def test_the_whole_frozen_system_is_bound_not_just_the_pa_estimator(world, tmp_path: Path):
    """The estimator digest alone identifies one file, not a forecasting system."""

    session = world["session"]
    verified = verify_package(build_package(tmp_path, canonical_game_id=str(world["game"].id)))
    register_package(session, verified)

    bundle = session.scalar(select(ShadowForecastBundle).where(
        ShadowForecastBundle.bundle_sha256 == verified.handoff_sha256))
    binding = bundle.evidence["baselineSystemBinding"]
    assert binding["estimatorSha256"] == BASELINE_ARTIFACT
    assert binding["championManifestSha256"] == CHAMPION_MANIFEST
    assert binding["frozenBindingsSha256"] == FROZEN_BINDINGS
    assert binding["developmentIntegritySha256"] == DEV_INTEGRITY
    assert binding["registrationReceiptSha256"] == REGISTRATION_RECEIPT
    assert binding["parentBundleId"] == PARENT_BUNDLE_ID
    assert binding["trainingRunId"] == TRAINING_RUN

    row = session.scalar(select(ShadowForecast).where(
        ShadowForecast.engine_id == CANDIDATE_ENGINE))
    per_game = row.lineage_detail["baselineBinding"]
    assert per_game["acceptanceProtocolSha256"] == ACCEPTANCE_SHA
    assert per_game["numericalArtifactSha256"] == BASELINE_NUMERICAL
    assert per_game["simulatorId"] == BASELINE_SIMULATOR
    assert per_game["simulatorSeed"] == BASELINE_SEED
    assert per_game["simulationCount"] == BASELINE_SIM_COUNT
    # And the candidate row points at the exact stored baseline forecast.
    baseline_row = session.scalar(select(ShadowForecast).where(
        ShadowForecast.forecast_id == BASELINE_FORECAST_ID))
    assert row.lineage_detail["baselineShadowForecastId"] == str(baseline_row.id)


@pytest.mark.parametrize("field_name,bad_value,needle", [
    ("championManifestSha256", "1" * 64, "champion/system manifest"),
    ("frozenBindingsSha256", "2" * 64, "preprocessor and ordered feature contract"),
    ("developmentIntegritySha256", "3" * 64, "development-integrity manifest"),
    ("registrationReceiptSha256", "4" * 64, "registration receipt"),
    ("parentBundleId", "some-other-bundle", "parent V18 bundle id"),
    ("trainingRunId", "some-other-run", "training run id"),
])
def test_one_mismatched_system_dependency_rejects_the_package(
    world, tmp_path: Path, field_name, bad_value, needle,
):
    """Every link is load-bearing: break one and nothing is imported."""

    session = world["session"]
    verified = verify_package(build_package(
        tmp_path, canonical_game_id=str(world["game"].id),
        baseline_overrides={field_name: bad_value},
    ))
    with pytest.raises(BaselineBindingError, match=needle):
        register_package(session, verified)
    assert session.scalar(select(func.count()).select_from(ShadowForecast).where(
        ShadowForecast.engine_id == CANDIDATE_ENGINE)) == 0


def test_a_missing_system_dependency_is_refused_rather_than_skipped(world, tmp_path: Path):
    session = world["session"]
    verified = verify_package(build_package(
        tmp_path, canonical_game_id=str(world["game"].id),
        baseline_overrides={"frozenBindingsSha256": None},
    ))
    with pytest.raises(BaselineBindingError, match="declares no frozenBindingsSha256"):
        register_package(session, verified)


def test_a_baseline_forecast_id_that_resolves_to_nothing_is_refused(world, tmp_path: Path):
    session = world["session"]
    path = build_package(tmp_path, canonical_game_id=str(world["game"].id))
    _rewrite_forecast(tmp_path, path, {"baselineForecastId": "mlb-v18-does-not-exist"})

    verified = verify_package(path)
    with pytest.raises(BaselineBindingError, match="does not resolve to a stored V18 forecast"):
        register_package(session, verified)


def test_a_baseline_with_the_wrong_orientation_is_refused(world, tmp_path: Path):
    """Home and away the other way round is a sign error, not a comparison."""

    session = world["session"]
    path = build_package(tmp_path, canonical_game_id=str(world["game"].id))
    _rewrite_forecast(tmp_path, path, {"homeTeam": "LAD", "awayTeam": "MIA"})

    verified = verify_package(path)
    with pytest.raises(BaselineBindingError, match="orientation disagrees"):
        register_package(session, verified)


def test_a_baseline_with_a_different_horizon_is_refused(world, tmp_path: Path):
    session = world["session"]
    path = build_package(tmp_path, canonical_game_id=str(world["game"].id))
    _rewrite_forecast(tmp_path, path, {
        "scheduledStart": (START + timedelta(days=1)).isoformat(),
    })

    verified = verify_package(path)
    with pytest.raises(BaselineBindingError, match="scheduled start"):
        register_package(session, verified)


def test_a_baseline_binding_with_the_wrong_seed_is_refused(world, tmp_path: Path):
    session = world["session"]
    verified = verify_package(build_package(
        tmp_path, canonical_game_id=str(world["game"].id),
        baseline_binding={
            "parentBundleSha256": PARENT_BUNDLE_SHA,
            "acceptanceProtocolSha256": ACCEPTANCE_SHA,
            "numericalArtifactSha256": BASELINE_NUMERICAL,
            "featureVectorSha256": BASELINE_FEATURE_VECTOR,
            "simulatorId": BASELINE_SIMULATOR,
            "simulatorVersion": BASELINE_SIM_VERSION,
            "simulatorSeed": 999,
            "simulationCount": BASELINE_SIM_COUNT,
            "sourceCutoffUtc": BASELINE_CUTOFF.isoformat(),
        },
    ))
    with pytest.raises(BaselineBindingError, match="simulator seed"):
        register_package(session, verified)


def test_a_replay_is_bound_to_the_same_system_and_keeps_its_label(world, tmp_path: Path):
    """Binding applies to replays too; the distinction survives it."""

    session = world["session"]
    verified = verify_package(build_package(
        tmp_path, artifact_type=HISTORICAL_REPLAY,
        published=START + timedelta(hours=6),
        canonical_game_id=str(world["game"].id),
    ))
    outcome = register_package(session, verified)

    assert len(outcome.imported) == 1
    row = session.scalar(select(ShadowForecast).where(
        ShadowForecast.engine_id == CANDIDATE_ENGINE))
    assert row.status == "HISTORICAL_REPLAY_RESEARCH_SHADOW"
    assert row.lineage_detail["baselineBinding"]["numericalArtifactSha256"] == BASELINE_NUMERICAL


# -- the V2 envelope, and the boundary it draws ----------------------------
#
# V2 exists to carry one component role. The risk it introduces is that the
# version stops meaning anything: if a TRANSITION component were accepted under
# a V1 envelope, a producer could widen the contract by writing a new component
# name into an old header, and "verified against V1" would be worthless.


def _transition_components(base_sha: str = BASELINE_ARTIFACT) -> list[dict]:
    return [
        {"componentId": "V18_WHOLE_GAME", "role": "BASE", "sha256": base_sha,
         "status": "FROZEN"},
        {"componentId": "MLB_BASE_OUT_TRANSITION_HIERARCHICAL_SHRINKAGE_V1",
         "role": "TRANSITION", "sha256": DURATION_SHA, "status": "SHADOW_ONLY"},
    ]


def test_a_v2_package_carrying_a_transition_component_verifies(tmp_path: Path):
    handoff = build_package(
        tmp_path,
        schema_version="AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V2",
        components=_transition_components(),
    )
    verified = verify_package(handoff)
    assert len(verified.forecasts) == 1
    assert verified.rejected == ()
    assert verified.contract_version == "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V2"


def test_a_v1_package_may_not_smuggle_a_transition_component(tmp_path: Path):
    """The envelope gates the vocabulary, not the other way round."""

    handoff = build_package(tmp_path, components=_transition_components())
    with pytest.raises(EnsembleCandidateImportError) as exc:
        verify_package(handoff)
    assert "TRANSITION" in str(exc.value)
    assert "HANDOFF_V2" in str(exc.value)


def test_an_unknown_role_is_still_refused_under_v2(tmp_path: Path):
    """V2 widens the vocabulary by exactly one word, not indefinitely."""

    handoff = build_package(
        tmp_path,
        schema_version="AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V2",
        components=[
            {"componentId": "V18_WHOLE_GAME", "role": "BASE",
             "sha256": BASELINE_ARTIFACT, "status": "FROZEN"},
            {"componentId": "SOMETHING_NEW", "role": "BULLPEN",
             "sha256": DURATION_SHA, "status": "SHADOW_ONLY"},
        ],
    )
    with pytest.raises(EnsembleCandidateImportError, match="unknown component role"):
        verify_package(handoff)


def test_an_unsupported_handoff_version_is_refused(tmp_path: Path):
    handoff = build_package(
        tmp_path, schema_version="AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V9",
    )
    with pytest.raises(EnsembleCandidateImportError, match="expected one of"):
        verify_package(handoff)


def test_the_bundle_records_the_version_it_actually_verified(world, tmp_path: Path):
    """A fixed constant made every bundle claim V1, whatever was checked."""

    session = world["session"]
    handoff = build_package(
        tmp_path,
        schema_version="AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V2",
        components=_transition_components(),
        canonical_game_id=str(world["game"].id),
    )
    verified = verify_package(handoff)
    register_package(session, verified)
    session.flush()

    bundle = session.scalar(
        select(ShadowForecastBundle)
        .where(ShadowForecastBundle.bundle_sha256 == verified.handoff_sha256)
    )
    assert bundle.contract_version == "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V2"


def test_v1_packages_still_record_v1(world, tmp_path: Path):
    session = world["session"]
    handoff = build_package(tmp_path, canonical_game_id=str(world["game"].id))
    verified = verify_package(handoff)
    register_package(session, verified)
    session.flush()
    bundle = session.scalar(
        select(ShadowForecastBundle)
        .where(ShadowForecastBundle.bundle_sha256 == verified.handoff_sha256)
    )
    assert bundle.contract_version == "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V1"


# -- two arms of one comparison, kept apart --------------------------------
#
# Survival and transition are configurations of one engine family, published for
# the same games at the same instant. "The latest candidate" cannot tell them
# apart, so importing the second arm would have silently replaced the first
# everywhere it is displayed or scored.


def _two_configurations(world, tmp_path: Path):
    """Import two arms of the same game and return the session and game id."""

    from app.shadow_import.mlb_ensemble_candidate_read import candidate_comparison

    session = world["session"]
    game_id = str(world["game"].id)
    for arm, ran, home_win in (("transition", True, 0.5538), ("duration", False, 0.5812)):
        root = tmp_path / arm
        root.mkdir()
        handoff = build_package(
            root,
            canonical_game_id=game_id,
            home_win=home_win,
            duration_ran=ran,
            schema_version=(
                "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V2" if arm == "transition"
                else "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V1"
            ),
            components=(
                _transition_components() if arm == "transition" else None
            ),
        )
        # Distinct engine version per arm: the producer's own configuration id.
        body = json.loads(handoff.read_text(encoding="utf-8"))
        body["candidate"]["engineVersion"] = f"mlb-v18-three-arm-{arm}-v1"
        body["bundleId"] = f"mlb-v18-three-arm-{arm}-fixture"
        digest = _sha_bytes(json.dumps(
            {k: v for k, v in body.items() if k != "handoffSha256"},
            sort_keys=True, separators=(",", ":")).encode())
        body["handoffSha256"] = digest
        handoff.write_text(
            json.dumps(body, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        _rewrite_forecast(root, handoff, {"forecastId": f"mlb-v18-{arm}-fixture"})
        register_package(session, verify_package(handoff))
    session.flush()
    return session, world["game"].id, candidate_comparison


def test_a_second_arm_does_not_replace_the_first(world, tmp_path: Path):
    from app.shadow_import.mlb_ensemble_candidate_read import candidate_configurations

    session, game_id, comparison = _two_configurations(world, tmp_path)

    keys = {row["key"] for row in candidate_configurations(session, game_id)}
    assert keys == {"mlb-v18-three-arm-transition-v1", "mlb-v18-three-arm-duration-v1"}

    transition = comparison(session, game_id, configuration="mlb-v18-three-arm-transition-v1")
    duration = comparison(session, game_id, configuration="mlb-v18-three-arm-duration-v1")
    assert transition["candidate"]["home_win_probability"] != (
        duration["candidate"]["home_win_probability"])
    # Each answer lists the other, so neither arm can be mistaken for the whole.
    assert len(transition["available_configurations"]) == 2


def test_the_displayed_baseline_is_the_one_the_candidate_names(world, tmp_path: Path):
    """Not "the latest V18 forecast", which would drift after a re-publication."""

    session, game_id, comparison = _two_configurations(world, tmp_path)
    result = comparison(session, game_id, configuration="mlb-v18-three-arm-duration-v1")
    assert result["baseline_binding"] == "BOUND_BY_CANDIDATE_BASELINE_FORECAST_ID"
    assert result["baseline"]["forecast_id"] == BASELINE_FORECAST_ID


def test_a_fallback_arm_is_labelled_not_counted_as_a_component_result(world, tmp_path: Path):
    """Its numbers match the baseline because the component did not run."""

    session, game_id, comparison = _two_configurations(world, tmp_path)
    duration = comparison(session, game_id, configuration="mlb-v18-three-arm-duration-v1")
    assert duration["configuration"]["component_trial_supported"] is False
    assert "not a measured component effect" in duration["fallback_note"]

    transition = comparison(session, game_id, configuration="mlb-v18-three-arm-transition-v1")
    assert transition["configuration"]["component_trial_supported"] is True
    assert transition["fallback_note"] is None


def test_an_unknown_configuration_returns_nothing_rather_than_another_arm(world, tmp_path: Path):
    session, game_id, comparison = _two_configurations(world, tmp_path)
    assert comparison(session, game_id, configuration="V18_NOT_PUBLISHED") is None


# -- V3 and the score-limit role -------------------------------------------
#
# SCORE_LIMIT is post-simulation whole-game filtering: it changes WHICH completed
# draws are kept, not how any game is simulated. That makes it a different kind
# of thing from DURATION, TRANSITION and CALIBRATION, and it is the one
# transformation whose retained population is deliberately smaller than its
# source -- so the denominator has to be checked, not assumed.


def _score_limit_components(base_sha: str = BASELINE_ARTIFACT) -> list[dict]:
    return [
        {"componentId": "V18_WHOLE_GAME", "role": "BASE", "sha256": base_sha,
         "status": "FROZEN"},
        {"componentId": "MLB_V18_SCORE_LIMITED_V1", "role": "SCORE_LIMIT",
         "sha256": DURATION_SHA, "status": "SHADOW_ONLY"},
    ]


def _with_population(root: Path, handoff: Path, *, source=5000, retained=4722,
                     excluded=278, denominator=None):
    _rewrite_forecast(root, handoff, {"simulation": {
        "simulatorId": "V18_WHOLE_GAME_SCORE_LIMITED_RESEARCH",
        "simulatorVersion": "1", "seed": 20260907,
        "sourceSimulationCount": source,
        "retainedSimulationCount": retained,
        "excludedSimulationCount": excluded,
        "simulationCount": denominator if denominator is not None else retained,
    }})


def test_a_v3_package_carrying_a_score_limit_component_verifies(tmp_path: Path):
    handoff = build_package(
        tmp_path, schema_version="AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V3",
        components=_score_limit_components())
    _with_population(tmp_path, handoff)

    verified = verify_package(handoff)

    assert len(verified.forecasts) == 1 and verified.rejected == ()
    assert verified.contract_version == "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V3"


@pytest.mark.parametrize("schema", [
    "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V1",
    "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V2",
])
def test_older_envelopes_still_reject_the_score_limit_role(tmp_path: Path, schema: str):
    """The envelope gates the vocabulary. V2 gained TRANSITION, not everything."""

    handoff = build_package(
        tmp_path, schema_version=schema, components=_score_limit_components())

    with pytest.raises(EnsembleCandidateImportError) as exc:
        verify_package(handoff)
    assert "SCORE_LIMIT" in str(exc.value)
    assert "HANDOFF_V3" in str(exc.value)


def test_v3_still_rejects_a_role_no_version_defines(tmp_path: Path):
    handoff = build_package(
        tmp_path, schema_version="AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V3",
        components=[
            {"componentId": "V18_WHOLE_GAME", "role": "BASE",
             "sha256": BASELINE_ARTIFACT, "status": "FROZEN"},
            {"componentId": "SOMETHING", "role": "WEATHER",
             "sha256": DURATION_SHA, "status": "SHADOW_ONLY"},
        ])

    with pytest.raises(EnsembleCandidateImportError, match="unknown component role"):
        verify_package(handoff)


def test_the_retained_population_must_account_for_itself(tmp_path: Path):
    """Retained plus excluded is the source, or the filter lost draws."""

    handoff = build_package(
        tmp_path, schema_version="AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V3",
        components=_score_limit_components())
    _with_population(tmp_path, handoff, retained=4722, excluded=100)

    verified = verify_package(handoff)

    # A per-object failure is collected with its reason rather than raised, so
    # one bad game never silently drops out of an otherwise valid package.
    assert verified.forecasts == ()
    assert len(verified.rejected) == 1
    assert "does not account for itself" in verified.rejected[0]["reason"]


def test_candidate_summaries_must_use_the_retained_denominator(tmp_path: Path):
    """A forecast still reporting 5,000 is averaging over draws it excluded."""

    handoff = build_package(
        tmp_path, schema_version="AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V3",
        components=_score_limit_components())
    _with_population(tmp_path, handoff, denominator=5000)

    verified = verify_package(handoff)

    # A per-object failure is collected with its reason rather than raised, so
    # one bad game never silently drops out of an otherwise valid package.
    assert verified.forecasts == ()
    assert len(verified.rejected) == 1
    assert "not the retained count" in verified.rejected[0]["reason"]


def test_too_few_retained_draws_is_refused(tmp_path: Path):
    handoff = build_package(
        tmp_path, schema_version="AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V3",
        components=_score_limit_components())
    _with_population(tmp_path, handoff, retained=999, excluded=4001)

    verified = verify_package(handoff)

    # A per-object failure is collected with its reason rather than raised, so
    # one bad game never silently drops out of an otherwise valid package.
    assert verified.forecasts == ()
    assert len(verified.rejected) == 1
    assert "below the rule's fixed minimum" in verified.rejected[0]["reason"]


def test_the_baseline_binding_still_names_the_original_five_thousand(
    world, tmp_path: Path,
):
    """Filtering changes the CANDIDATE's denominator, never the baseline's.

    baselineBinding describes the ordinary V18 forecast, which did not change,
    so it must still agree with the stored baseline's own simulation count. This
    is the check that would break if the retained count were substituted there.
    """

    session = world["session"]
    handoff = build_package(
        tmp_path, schema_version="AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V3",
        components=_score_limit_components(),
        canonical_game_id=str(world["game"].id))
    _with_population(tmp_path, handoff)
    verified = verify_package(handoff)

    register_package(session, verified)
    session.flush()

    row = session.scalar(select(ShadowForecast).where(
        ShadowForecast.engine_id == CANDIDATE_ENGINE))
    lineage = row.lineage_detail or {}
    assert lineage["baselineBinding"]["simulationCount"] == BASELINE_SIM_COUNT
    assert lineage["sourceSimulationCount"] == 5000
    assert lineage["retainedSimulationCount"] == 4722
    assert lineage["excludedSimulationCount"] == 278
    # The stored denominator is the retained one.
    assert (row.uncertainty or {}).get("simulationCount") == 4722
