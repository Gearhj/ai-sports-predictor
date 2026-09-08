"""Fail-closed import of the V18 Population Geometry supplement.

Focused on the boundary only: a 5,000-simulation aggregate may be displayed
just when every checksum, parent identity and governance flag holds, and its
registration may never touch Gen 1 or the accepted V18 forecast.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session as OrmSession

from app.jobs import seed_demo_data
from app.models import Forecast, Game, ShadowForecast, ShadowForecastBundle
from app.shadow_import.mlb_v18 import ENGINE_ID
from app.shadow_import.mlb_v18_play_geometry import LINEAGE_KEY as PLAY_GEOMETRY_KEY
from app.shadow_import.mlb_v18_population_geometry import (
    LINEAGE_KEY,
    SELECTORS,
    PopulationImportError,
    population_layers,
    population_payload,
    population_representatives,
    population_status,
    register_population,
    verify_population,
)

SLATE = date(2026, 8, 12)
PARENT_SHA = "a" * 64
PLAY_GEOMETRY_SHA = "b" * 64
FRAME_COUNT = 100
FRAME_SIZE = 50
SIMULATIONS = FRAME_COUNT * FRAME_SIZE
CONTACT_PLAYS = 2000
RUNNER_SEGMENTS = 5000

FINAL = {
    "homeWinProbability": 0.4334,
    "meanHomeRuns": 3.4694,
    "meanAwayRuns": 4.423,
    "meanTotalRuns": 7.8924,
    "meanRunDifferential": -0.9536,
    "oneRunRate": 0.3094,
    "extraInningRate": 0.1074,
}


def _canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def _canonical_sha(payload) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _event(pa: int) -> dict:
    contact = pa % 2 == 0
    return {
        "pa": pa, "inning": 1, "half": "TOP", "battingTeam": "AWAY",
        "battingOrderPosition": pa, "pitcherState": "STARTER", "pitchingChange": False,
        "outcome": "SINGLE" if contact else "STRIKEOUT",
        "outsBefore": 0, "outsAfter": 0 if contact else 1,
        "baseMaskBefore": 0, "baseMaskAfter": 1 if contact else 0,
        "runsScored": 0, "homeScoreAfter": 0, "awayScoreAfter": 0,
        "geometry": {
            "contactOccurred": contact,
            "geometryAuthority": "RETROSHEET_LOCATION_ZONE_CENTROID" if contact else "NO_CONTACT",
            "coordinateSystem": "ASP_NORMALIZED_DIAMOND_V1",
            "exitVelocityMph": None, "launchAngleDegrees": None,
        },
        "ballTrace": None,
        "runnerMovements": [],
        "runnerModelVersion": "MLB_V18_RUNNER_TEMPLATE_SAMPLER_V1",
        "runnerBackoffLevel": "outcome_state_outs_runs_after_bbtype",
        "runnerBackoffSupport": 120,
    }


def _frames(count: int = FRAME_COUNT, *, monotonic: bool = True) -> dict:
    frames = []
    for index in range(count):
        cumulative = (index + 1) * FRAME_SIZE
        if not monotonic and index == 4:
            cumulative = index * FRAME_SIZE  # a frame that does not advance
        share = cumulative / SIMULATIONS
        frames.append({
            "frameIndex": index,
            "simulationIndexStart": index * FRAME_SIZE,
            "simulationIndexEndInclusive": cumulative - 1,
            "cumulativeSimulationCount": cumulative,
            "cumulativeHomeWins": round(cumulative * FINAL["homeWinProbability"]),
            "cumulativeAwayWins": cumulative - round(cumulative * FINAL["homeWinProbability"]),
            "cumulativeHomeWinProbability": FINAL["homeWinProbability"],
            "cumulativeAwayWinProbability": 1 - FINAL["homeWinProbability"],
            "cumulativeExpectedHomeRuns": FINAL["meanHomeRuns"],
            "cumulativeExpectedAwayRuns": FINAL["meanAwayRuns"],
            "cumulativeExpectedTotal": FINAL["meanTotalRuns"],
            "cumulativeRunDifferential": FINAL["meanRunDifferential"],
            "cumulativeOneRunGameRate": FINAL["oneRunRate"],
            "cumulativeExtraInningRate": FINAL["extraInningRate"],
            "cumulativeContactPlayCount": round(CONTACT_PLAYS * share),
            "cumulativeGeometryDensity": round(CONTACT_PLAYS * share),
            "cumulativeRunnerPathTraffic": round(RUNNER_SEGMENTS * share),
            "cumulativeScoringPlayCount": round(300 * share),
            "frameDigest": hashlib.sha256(str(index).encode()).hexdigest(),
        })
    return {"schemaVersion": "MLB_V18_POPULATION_GEOMETRY_FRAMES_V1", "frames": frames}


def _selectors(paths: list[dict]) -> dict:
    # LOW_SCORING is simulation index 0 on purpose: zero is a selection.
    indexes = [0, 13, 15, 34, 44, 526]
    order = ["LOW_SCORING", "HOME_WIN", "TYPICAL", "AWAY_WIN", "EXTRA_INNINGS", "HIGH_SCORING"]
    return {
        selector: {
            "selector": selector,
            "simulationIndex": index,
            "pathDigest": path["pathDigest"],
            "geometryDigest": path["geometryDigest"],
            "definition": f"{selector} definition",
            "candidateCount": SIMULATIONS,
            "rank": 1,
            "distance": 0.0,
            "tieBreak": "LOWEST_SIMULATION_INDEX_AFTER_DISTANCE_AND_DIFFERENTIAL_DISTANCE",
            "selectionInputs": {
                "totalRuns": 3 + position, "runDifferential": -1,
                "homeRuns": 1, "awayRuns": 2 + position, "innings": 9,
            },
            "target": {"kind": selector},
        }
        for position, (selector, index, path) in enumerate(zip(order, indexes, paths, strict=True))
    }


def _write_population(
    root: Path,
    *,
    game_id: str,
    parent_sha: str = PARENT_SHA,
    play_geometry_sha: str = PLAY_GEOMETRY_SHA,
    frames: dict | None = None,
    representative_count: int = 6,
    contact_total: int = CONTACT_PLAYS,
    governance_overrides: dict | None = None,
    handoff_overrides: dict | None = None,
) -> Path:
    frames = frames or _frames()
    paths = [
        {
            "schemaVersion": "MLB_V18_PLAY_GEOMETRY_TRACE_V1",
            "pathIndex": index, "gameId": game_id,
            "plateAppearances": 70 + index,
            "pathDigest": hashlib.sha256(f"path-{index}".encode()).hexdigest(),
            "geometryDigest": hashlib.sha256(f"geom-{index}".encode()).hexdigest(),
            "backoffSummary": {"runnerBackoffRate": 0.06},
            "events": [_event(pa + 1) for pa in range(70 + index)],
        }
        for index in range(representative_count)
    ]
    # Six selectors always exist; when fewer paths are supplied they must
    # collide, which the importer refuses.
    selector_paths = (paths * 6)[:6]
    selectors = _selectors(selector_paths)

    governance = {
        "wageringAuthority": False,
        "promotionStatus": "NOT_PROMOTED",
        "executionAllowedInAISP": False,
        "finalOotOpened": False,
        "researchShadow": True,
        "aispWrites": 0,
        "providerCalls": 0,
        "recalibrations": 0,
        "v18PredictiveModelFits": 0,
    }
    governance.update(governance_overrides or {})

    summary = {
        "schemaVersion": "MLB_V18_POPULATION_GEOMETRY_SUMMARY_V1",
        "simulationCount": SIMULATIONS,
        "frameCount": FRAME_COUNT,
        "frameSize": FRAME_SIZE,
        "masterSeed": 20260907,
        "coordinateSystemVersion": "ASP_NORMALIZED_DIAMOND_V1",
        "parentBundleId": "mlb-v18-test",
        "parentBundleSha256": parent_sha,
        "playGeometrySupplementSha256": play_geometry_sha,
        "populationDigest": "c" * 64,
        "illegalStateCount": 0,
        "nonterminationCount": 0,
        "counts": {
            "plateAppearances": 377078,
            "contactPlays": contact_total,
            "geometryBearingPlays": contact_total,
            "scoringPlays": 300,
            "runnerSegments": RUNNER_SEGMENTS,
        },
        "finalPopulation": dict(FINAL),
        "governance": governance,
        "reconstruction": {
            "deterministicReplay": True,
            "allMetricsMatch": True,
            "firstPopulationDigest": "c" * 64,
            "replayPopulationDigest": "c" * 64,
        },
        "representatives": {
            "selectors": selectors,
            "sharedSelections": [],
            "uniquePathCount": len({item["pathDigest"] for item in selector_paths}),
        },
    }

    contact = {
        "schemaVersion": "MLB_V18_POPULATION_GEOMETRY_SUPPLEMENT_V1",
        "coordinateSystem": "ASP_NORMALIZED_DIAMOND_V1",
        "grid": {"width": 32, "height": 32, "xRange": [-1, 1], "yRange": [0, 1],
                 "orientation": "HOME_PLATE_AT_0_0_CENTER_FIELD_POSITIVE_Y"},
        "normalization": "RAW_COUNTS",
        "smoothing": {"applied": False, "method": None},
        "layers": {
            "ALL": {"count": contact_total, "maximumCellCount": contact_total // 2,
                    "sparseRawCounts": [[271, contact_total // 2], [272, contact_total // 2]]},
            "SCORING": {"count": 300, "maximumCellCount": 300, "sparseRawCounts": [[271, 300]]},
        },
    }
    path_density = {
        "schemaVersion": "MLB_V18_POPULATION_GEOMETRY_SUPPLEMENT_V1",
        "coordinateSystem": "ASP_NORMALIZED_DIAMOND_V1",
        "grid": {"width": 24, "height": 24, "cellEncoding": "row_major",
                 "tupleEncoding": "[originTimes576PlusControl,destination,count]"},
        "representation": "SPARSE_QUADRATIC_PATH_CELLS",
        "semantics": "MODELLED_GEOMETRY aggregated for display.",
        "layers": {"ALL": [[6924, 12, 2636], [6947, 59, 747]]},
    }
    runner_traffic = {
        "schemaVersion": "MLB_V18_POPULATION_GEOMETRY_SUPPLEMENT_V1",
        "representation": "ANONYMOUS_SEGMENT_TRAFFIC_COUNTS",
        "semantics": "HISTORICAL_RUNNER_TEMPLATE; never player identity.",
        "allSegments": {"BATTER_BOX_TO_FIRST": 3000, "FIRST_TO_SECOND": 1500,
                        "BATTER_BOX_TO_OUT": 500},
        "dimensions": {"TEAM_HOME": {"BATTER_BOX_TO_FIRST": 1500}},
        "requiredTrafficCounts": {"HOME_TO_FIRST": 3000},
    }
    representatives = {
        "paths": paths,
        "selectors": selectors,
        "sharedSelections": [],
        "uniquePathCount": len(paths),
    }
    authority_body = {
        "schemaVersion": "MLB_V18_EXECUTION_AUTHORITY_V1",
        "parentBundleSha256": parent_sha,
        "simulation": {"count": SIMULATIONS},
    }
    authority = dict(authority_body)
    authority["executionAuthoritySha256"] = _canonical_sha(authority_body)

    files = {
        "population-summary.json": json.dumps(summary, indent=1, sort_keys=True).encode(),
        "population-accumulation-frames.json": json.dumps(frames, indent=1, sort_keys=True).encode(),
        "population-contact-density.json": json.dumps(contact, indent=1, sort_keys=True).encode(),
        "population-path-density.json": json.dumps(path_density, indent=1, sort_keys=True).encode(),
        "population-runner-traffic.json": json.dumps(runner_traffic, indent=1, sort_keys=True).encode(),
        "representative-paths.json": json.dumps(representatives, sort_keys=True).encode(),
        "execution-authority.json.gz": gzip.compress(
            json.dumps(authority, sort_keys=True).encode(), mtime=0,
        ),
        "report.md": b"# V18 Population Geometry supplement\n",
    }

    handoff = {
        "schemaVersion": "AISP_MLB_V18_POPULATION_GEOMETRY_HANDOFF_V1",
        "parentBundleSha256": parent_sha,
        "playGeometrySupplementSha256": play_geometry_sha,
        "populationDigest": "c" * 64,
        "wageringAuthority": False,
        "promotionStatus": "NOT_PROMOTED",
        "executionAllowedInAISP": False,
        "finalOotOpened": False,
        "aispModified": False,
        "registrationState": "READY_FOR_AISP_AGENT_REGISTRATION_NOT_REGISTERED",
    }
    handoff.update(handoff_overrides or {})
    files["aisp-population-geometry-handoff.json"] = json.dumps(
        handoff, indent=1, sort_keys=True,
    ).encode()

    identity_body = {
        "schemaVersion": "MLB_V18_POPULATION_GEOMETRY_MANIFEST_V1",
        "parentBundleId": "mlb-v18-test",
        "parentBundleSha256": parent_sha,
        "playGeometrySupplementSha256": play_geometry_sha,
        "populationDigest": "c" * 64,
        "simulationCount": SIMULATIONS,
        "executionAuthoritySha256": authority["executionAuthoritySha256"],
        "artifacts": [
            {"relativePath": name, "bytes": len(payload),
             "sha256": hashlib.sha256(payload).hexdigest()}
            for name, payload in sorted(files.items())
        ],
        "verdict": "V18_POPULATION_GEOMETRY_SUPPLEMENT_READY_FOR_AISP",
    }
    address = _canonical_sha(identity_body)
    manifest = dict(identity_body)
    manifest["supplementSha256"] = address
    manifest["supplementId"] = f"mlb-v18-population-geometry-{address[:24]}"

    directory = root / address
    directory.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        (directory / name).write_bytes(payload)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return directory


# -- verification -----------------------------------------------------------


def test_a_well_formed_supplement_verifies(tmp_path: Path) -> None:
    verified = verify_population(_write_population(tmp_path, game_id="game-1"))
    assert verified.simulation_count == SIMULATIONS
    assert len(verified.frames) == FRAME_COUNT
    assert verified.frames[-1]["cumulativeSimulationCount"] == SIMULATIONS
    assert len(verified.representatives["paths"]) == len(SELECTORS)
    assert verified.parent_bundle_sha256 == PARENT_SHA
    assert verified.play_geometry_sha256 == PLAY_GEOMETRY_SHA


def test_a_tampered_object_fails_closed(tmp_path: Path) -> None:
    directory = _write_population(tmp_path, game_id="game-1")
    (directory / "population-contact-density.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PopulationImportError, match="checksum mismatch"):
        verify_population(directory)


def test_a_missing_object_fails_closed(tmp_path: Path) -> None:
    directory = _write_population(tmp_path, game_id="game-1")
    (directory / "population-runner-traffic.json").unlink()
    with pytest.raises(PopulationImportError, match="missing"):
        verify_population(directory)


def test_an_edited_manifest_no_longer_hashes_to_its_identity(tmp_path: Path) -> None:
    directory = _write_population(tmp_path, game_id="game-1")
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["simulationCount"] = 4000
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    with pytest.raises(PopulationImportError, match="hash to its published identity"):
        verify_population(directory)


def test_a_handoff_naming_another_parent_bundle_is_refused(tmp_path: Path) -> None:
    directory = _write_population(
        tmp_path, game_id="game-1", handoff_overrides={"parentBundleSha256": "f" * 64})
    with pytest.raises(PopulationImportError, match="parent V18 bundle"):
        verify_population(directory)


def test_a_handoff_naming_another_play_geometry_parent_is_refused(tmp_path: Path) -> None:
    directory = _write_population(
        tmp_path, game_id="game-1",
        handoff_overrides={"playGeometrySupplementSha256": "e" * 64})
    with pytest.raises(PopulationImportError, match="parent Play Geometry supplement"):
        verify_population(directory)


@pytest.mark.parametrize("flag,value", [
    ("wageringAuthority", True),
    ("promotionStatus", "PROMOTED"),
    ("executionAllowedInAISP", True),
    ("finalOotOpened", True),
    ("researchShadow", False),
    ("aispWrites", 3),
])
def test_unsafe_governance_flags_are_refused(tmp_path: Path, flag: str, value) -> None:
    directory = _write_population(
        tmp_path, game_id="game-1", governance_overrides={flag: value})
    with pytest.raises(PopulationImportError):
        verify_population(directory)


def test_the_frames_must_cover_exactly_the_declared_population(tmp_path: Path) -> None:
    directory = _write_population(tmp_path, game_id="game-1", frames=_frames(count=99))
    with pytest.raises(PopulationImportError, match="Expected 100 accumulation frames"):
        verify_population(directory)


def test_the_cumulative_count_must_increase(tmp_path: Path) -> None:
    directory = _write_population(
        tmp_path, game_id="game-1", frames=_frames(monotonic=False))
    with pytest.raises(PopulationImportError, match="does not increase"):
        verify_population(directory)


def test_a_density_total_that_contradicts_the_summary_is_refused(tmp_path: Path) -> None:
    directory = _write_population(tmp_path, game_id="game-1", contact_total=1999)
    with pytest.raises(PopulationImportError, match="contact density totals"):
        verify_population(directory)


def test_six_selectors_must_resolve_to_six_distinct_games(tmp_path: Path) -> None:
    directory = _write_population(tmp_path, game_id="game-1", representative_count=3)
    with pytest.raises(PopulationImportError, match="six distinct simulated games"):
        verify_population(directory)


def test_simulation_index_zero_is_a_selection_not_a_missing_value(tmp_path: Path) -> None:
    verified = verify_population(_write_population(tmp_path, game_id="game-1"))
    low = verified.summary["representatives"]["selectors"]["LOW_SCORING"]
    assert low["simulationIndex"] == 0
    digests = {
        verified.summary["representatives"]["selectors"][name]["pathDigest"]
        for name in SELECTORS
    }
    assert len(digests) == len(SELECTORS)


# -- registration -----------------------------------------------------------


@pytest.fixture
def cli_session(session):
    """A session shaped like the offline importer's.

    Registration only ever runs from ``scripts/mlb_v18_population_geometry.py``,
    which does not install the ORM immutability guards; the API process installs
    them and must never register anything.
    """

    listeners = [
        fn for fn in list(OrmSession.dispatch.before_flush._clslevel[OrmSession])
        if getattr(fn, "__module__", "") == "app.audit.immutability"
    ]
    for fn in listeners:
        event.remove(OrmSession, "before_flush", fn)
    try:
        yield session
    finally:
        for fn in listeners:
            event.listen(OrmSession, "before_flush", fn)


def _shadow_row(session, game, *, play_geometry_sha: str = PLAY_GEOMETRY_SHA):
    bundle = ShadowForecastBundle(
        bundle_sha256=PARENT_SHA, contract_version="AISP_MLB_V18_RESEARCH_SHADOW_BUNDLE_V1",
        bundle_id="mlb-v18-test", logical_uri="/tmp/bundle", source_fixture_id="contract",
        slate_date=SLATE, league="MLB", sport="MLB", status="RESEARCH_SHADOW",
        research_shadow_only=True, generated_at=game.starts_at, forecast_count=1,
        quote_count=0, candidate_count=0, imported_at=game.starts_at,
    )
    session.add(bundle)
    session.flush()
    row = ShadowForecast(
        bundle_id=bundle.id, canonical_game_id=game.id,
        forecast_id=f"mlb-v18-{PARENT_SHA[:12]}-{game.id}",
        engine_id=ENGINE_ID, engine_version="mlb-v18-test",
        scheduled_start_utc=game.starts_at,
        model_artifact_sha256="d" * 64, simulator_id="V18_WHOLE_GAME_MONTE_CARLO",
        simulator_version="v1", simulator_seed=20260907, event_id=str(game.id),
        source_event_id="1", event_date=SLATE, season=2026,
        home_team_id="home", away_team_id="away", is_ready=True,
        readiness_score=1.0, readiness_threshold=1.0, status="PROSPECTIVE_RESEARCH_SHADOW",
        full_home_win_probability=0.43, full_away_win_probability=0.57,
        full_expected_home_score=3.5, full_expected_away_score=4.4,
        distributions={}, uncertainty={"simulationCount": SIMULATIONS}, readiness_detail={},
        identity_evidence={},
        lineage_detail={
            "bundleSha256": PARENT_SHA,
            "pathDigest": "d" * 64,
            "sequentialPath": [[{"pa": 1}]],
            PLAY_GEOMETRY_KEY: {"supplementSha256": play_geometry_sha, "events": [{"pa": 1}]},
        },
    )
    session.add(row)
    session.flush()
    return row


@pytest.mark.asyncio
async def test_registration_is_idempotent_and_leaves_everything_else_alone(
    cli_session, session, settings, tmp_path,
) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    row = _shadow_row(session, game)
    directory = _write_population(tmp_path, game_id=str(game.id))
    verified = verify_population(directory)

    gen1_before = [
        (item.id, item.home_win_probability, item.version)
        for item in session.scalars(select(Forecast))
    ]
    shadow_before = (row.full_home_win_probability, row.status, row.simulator_seed)

    first = register_population(session, verified)
    assert first["registered"] == [row.forecast_id]
    session.commit()

    second = register_population(session, verified)
    assert second["registered"] == []
    assert second["alreadyRegistered"] == [row.forecast_id]

    lineage = row.lineage_detail
    # Additive: the accepted trace and the play-geometry block both survive.
    assert lineage["sequentialPath"] == [[{"pa": 1}]]
    assert lineage[PLAY_GEOMETRY_KEY]["supplementSha256"] == PLAY_GEOMETRY_SHA
    assert lineage[LINEAGE_KEY]["supplementSha256"] == verified.supplement_sha256
    assert lineage[LINEAGE_KEY]["wageringAuthority"] is False
    assert (row.full_home_win_probability, row.status, row.simulator_seed) == shadow_before
    assert [
        (item.id, item.home_win_probability, item.version)
        for item in session.scalars(select(Forecast))
    ] == gen1_before


@pytest.mark.asyncio
async def test_a_forecast_without_the_parent_play_geometry_is_skipped(
    cli_session, session, settings, tmp_path,
) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    row = _shadow_row(session, game, play_geometry_sha="9" * 64)
    directory = _write_population(tmp_path, game_id=str(game.id))
    outcome = register_population(session, verify_population(directory))
    assert outcome["registered"] == []
    assert "parent Play Geometry supplement" in outcome["skipped"][0]["reason"]
    assert LINEAGE_KEY not in (row.lineage_detail or {})


@pytest.mark.asyncio
async def test_a_representative_path_for_another_game_is_skipped(
    cli_session, session, settings, tmp_path,
) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    row = _shadow_row(session, game)
    directory = _write_population(tmp_path, game_id="a-different-game")
    outcome = register_population(session, verify_population(directory))
    assert outcome["registered"] == []
    assert "different game" in outcome["skipped"][0]["reason"]
    assert LINEAGE_KEY not in (row.lineage_detail or {})


@pytest.mark.asyncio
async def test_a_parent_bundle_mismatch_fails_closed(
    cli_session, session, settings, tmp_path,
) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    _shadow_row(session, game)
    directory = _write_population(
        tmp_path, game_id=str(game.id), parent_sha="7" * 64)
    with pytest.raises(PopulationImportError, match="No registered V18 shadow forecast"):
        register_population(session, verify_population(directory))


# -- read model -------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_three_payload_stages_carry_the_accepted_population(
    cli_session, session, settings, tmp_path,
) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    row = _shadow_row(session, game)
    register_population(
        session, verify_population(_write_population(tmp_path, game_id=str(game.id))))
    session.commit()

    payload = population_payload(session, row.forecast_id)
    assert payload is not None
    assert payload["simulation_count"] == SIMULATIONS
    assert payload["frame_count"] == FRAME_COUNT
    assert len(payload["frames"]) == FRAME_COUNT
    assert payload["frames"][-1]["cumulativeSimulationCount"] == SIMULATIONS
    assert payload["final_population"] == FINAL
    assert payload["display_authority"] == "RESEARCH_SHADOW_POPULATION_GEOMETRY"
    assert payload["wagering_authority"] is False
    assert payload["execution_allowed_in_aisp"] is False
    assert payload["selectors"]["LOW_SCORING"]["simulationIndex"] == 0

    layers = population_layers(session, row.forecast_id)
    assert layers is not None
    assert layers["contact_density"]["grid"]["width"] == 32
    assert layers["path_density"]["grid"]["width"] == 24
    assert "allSegments" in layers["runner_traffic"]

    representatives = population_representatives(session, row.forecast_id)
    assert representatives is not None
    assert len(representatives["paths"]) == len(SELECTORS)
    assert len({item["pathDigest"] for item in representatives["paths"]}) == len(SELECTORS)

    status = population_status(session)
    entry = next(item for item in status["forecasts"] if item["forecastId"] == row.forecast_id)
    assert entry["populationRegistered"] is True
    assert entry["frameCount"] == FRAME_COUNT


@pytest.mark.asyncio
async def test_an_unregistered_forecast_returns_no_population(session, settings) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    row = _shadow_row(session, game)
    assert population_payload(session, row.forecast_id) is None
    assert population_layers(session, row.forecast_id) is None
    assert population_representatives(session, "not-a-forecast") is None
