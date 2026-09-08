"""Fail-closed import of the V18 Play Geometry trace supplement.

These guard the boundary the supplement crosses: a Model Studio artifact may be
displayed only when every checksum, identity and governance flag holds, and its
registration may never reach Gen 1 or grant authority AISP does not have.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session as OrmSession

from app.audit.immutability import ImmutableRecordError

from app.jobs import seed_demo_data
from app.models import Forecast, Game, ShadowForecast, ShadowForecastBundle
from app.shadow_import.mlb_v18 import ENGINE_ID
from app.shadow_import.mlb_v18_play_geometry import (
    LINEAGE_KEY,
    PlayGeometryImportError,
    geometry_payload,
    geometry_status,
    register_geometry,
    verify_supplement,
)

SLATE = date(2026, 8, 12)
PARENT_SHA = "a" * 64


def _canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def _event(pa: int, *, contact: bool = True, runs: int = 0) -> dict:
    geometry = {
        "contactOccurred": contact,
        "battedBallType": "LINE_DRIVE" if contact else None,
        "geometryAuthority": "RETROSHEET_LOCATION_ZONE_CENTROID" if contact else "NO_CONTACT",
        "geometryBackoffLevel": "outcome" if contact else None,
        "geometryModelVersion": "MLB_V18_PLAY_GEOMETRY_SAMPLER_V1",
        "geometrySamplingSeed": "0" * 16,
        "coordinateSystem": "ASP_NORMALIZED_DIAMOND_V1",
        "landingOrFieldingPoint": (
            {"x": 0.1, "y": 0.5, "depth": 0.5, "depthClass": "DEEP",
             "directionDegrees": 11.0, "fairTerritory": True} if contact else None
        ),
        "exitVelocityMph": None, "launchAngleDegrees": None,
        "sprayAngleDegrees": None, "projectedDistanceFeet": None,
        "landingCoordinateMeasured": None,
    }
    return {
        "pa": pa, "inning": 1, "half": "TOP", "battingTeam": "AWAY",
        "battingOrderPosition": pa, "pitcherState": "STARTER", "pitchingChange": False,
        "outcome": "SINGLE" if contact else "STRIKEOUT",
        "outsBefore": 0, "outsAfter": 0 if contact else 1,
        "baseMaskBefore": 0, "baseMaskAfter": 1 if contact else 0,
        "runsScored": runs, "homeScoreAfter": 0, "awayScoreAfter": runs,
        "geometry": geometry,
        "ballTrace": ({
            "coordinateSystem": "ASP_NORMALIZED_DIAMOND_V1",
            "start": {"x": 0.0, "y": 0.0},
            "control": [{"x": 0.05, "y": 0.25, "lift": 0.05}],
            "end": {"x": 0.1, "y": 0.5},
            "traceStyle": "LINE", "normalizedDuration": 0.55,
            "fairTerritory": True,
            "outcomeCompatibility": "COMPATIBLE_WITH_PERSISTED_OUTCOME",
            "semantics": "PROJECTED_VISUAL_TRAJECTORY_FROM_SIMULATED_GEOMETRY_NOT_A_MEASURED_FLIGHT_PATH",
        } if contact else None),
        "runnerMovements": [{
            "anonymousRunnerId": f"r0000-{pa:02d}", "role": "BATTER",
            "from": "BATTER_BOX", "to": "FIRST" if contact else "OUT",
            "scored": False, "out": not contact,
            "movementReason": "BATTER_REACHED" if contact else "BATTER_RETIRED",
            "startFraction": 0.0, "endFraction": 0.6,
            "authority": "FITTED_HISTORICAL_TEMPLATE",
        }],
        "runnerModelVersion": "MLB_V18_RUNNER_TEMPLATE_SAMPLER_V1",
        "runnerBackoffLevel": "outcome_state_outs_runs_after_bbtype",
        "runnerBackoffSupport": 100,
    }


def _write_supplement(
    root: Path, *, game_id: str, parent_sha: str = PARENT_SHA,
    events: int = 3, overrides: dict | None = None, handoff_overrides: dict | None = None,
) -> Path:
    supplement = {
        "schemaVersion": "MLB_V18_PLAY_GEOMETRY_TRACE_V1",
        "parentBundleId": "mlb-v18-test",
        "parentBundleSha256": parent_sha,
        "coordinateSystem": {"name": "ASP_NORMALIZED_DIAMOND_V1", "origin": "HOME_PLATE"},
        "geometryModelVersion": "MLB_V18_PLAY_GEOMETRY_SAMPLER_V1",
        "runnerModelVersion": "MLB_V18_RUNNER_TEMPLATE_SAMPLER_V1",
        "limitations": ["Exit velocity is not registered."],
        "wageringAuthority": False,
        "promotionStatus": "NOT_PROMOTED",
        "executionAllowedInAISP": False,
        "displayAuthority": "RESEARCH_SHADOW_PLAY_GEOMETRY",
        "finalOotOpened": False,
    }
    supplement.update(overrides or {})
    address = hashlib.sha256(_canonical(supplement)).hexdigest()

    paths = {"paths": [{
        "schemaVersion": "MLB_V18_PLAY_GEOMETRY_TRACE_V1",
        "pathIndex": 0, "gameId": game_id, "plateAppearances": events,
        "pathDigest": "d" * 64, "geometryDigest": "e" * 64,
        "backoffSummary": {"runnerBackoffRate": 0.0},
        "events": [_event(index + 1, contact=index % 2 == 0) for index in range(events)],
    }]}
    handoff = {
        "schemaVersion": "AISP_MLB_V18_PLAY_GEOMETRY_HANDOFF_V1",
        "supplementId": f"mlb-v18-play-geometry-{address[:24]}",
        "supplementSha256": address,
        "parentBundleId": "mlb-v18-test",
        "parentBundleSha256": parent_sha,
        "supplementFile": "play-geometry-supplement.json",
        "pathsFile": "representative-paths.json",
        "wageringAuthority": False,
        "promotionStatus": "NOT_PROMOTED",
        "executionAllowedInAISP": False,
        "displayAuthority": "RESEARCH_SHADOW_PLAY_GEOMETRY",
        "rendering": {"failClosed": "Render the Broadcast Replay without ball traces."},
    }
    handoff.update(handoff_overrides or {})

    directory = root / address
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        "play-geometry-supplement.json": json.dumps(supplement, indent=2, sort_keys=True).encode(),
        "representative-paths.json": json.dumps(paths, indent=2, sort_keys=True).encode(),
        "aisp-play-geometry-handoff.json": json.dumps(handoff, indent=2, sort_keys=True).encode(),
    }
    for name, payload in files.items():
        (directory / name).write_bytes(payload)
    manifest = {
        "supplementId": handoff["supplementId"],
        "supplementSha256": address,
        "parentBundleSha256": parent_sha,
        "artifacts": [
            {"relativePath": name, "bytes": len(payload),
             "sha256": hashlib.sha256(payload).hexdigest()}
            for name, payload in sorted(files.items())
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return directory


# -- verification -----------------------------------------------------------

def test_a_well_formed_supplement_verifies(tmp_path: Path) -> None:
    directory = _write_supplement(tmp_path, game_id="game-1")
    verified = verify_supplement(directory)
    assert verified.supplement_sha256 == directory.name
    assert verified.parent_bundle_sha256 == PARENT_SHA
    assert verified.artifact_count == 3
    assert len(verified.paths) == 1


def test_a_tampered_artifact_fails_closed(tmp_path: Path) -> None:
    directory = _write_supplement(tmp_path, game_id="game-1")
    (directory / "representative-paths.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PlayGeometryImportError, match="checksum mismatch"):
        verify_supplement(directory)


def test_a_missing_artifact_fails_closed(tmp_path: Path) -> None:
    directory = _write_supplement(tmp_path, game_id="game-1")
    (directory / "aisp-play-geometry-handoff.json").unlink()
    with pytest.raises(PlayGeometryImportError, match="missing"):
        verify_supplement(directory)


def test_an_edited_body_no_longer_hashes_to_its_identity(tmp_path: Path) -> None:
    directory = _write_supplement(tmp_path, game_id="game-1")
    supplement = json.loads((directory / "play-geometry-supplement.json").read_text())
    supplement["limitations"] = []
    payload = json.dumps(supplement, indent=2, sort_keys=True).encode()
    (directory / "play-geometry-supplement.json").write_bytes(payload)
    manifest = json.loads((directory / "manifest.json").read_text())
    for artifact in manifest["artifacts"]:
        if artifact["relativePath"] == "play-geometry-supplement.json":
            artifact["sha256"] = hashlib.sha256(payload).hexdigest()
            artifact["bytes"] = len(payload)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with pytest.raises(PlayGeometryImportError, match="hash to its published identity"):
        verify_supplement(directory)


@pytest.mark.parametrize("flag,value", [
    ("wageringAuthority", True),
    ("promotionStatus", "PROMOTED"),
    ("executionAllowedInAISP", True),
    ("displayAuthority", "OFFICIAL"),
])
def test_unsafe_governance_flags_are_refused(tmp_path: Path, flag: str, value) -> None:
    directory = _write_supplement(tmp_path, game_id="game-1", overrides={flag: value})
    with pytest.raises(PlayGeometryImportError):
        verify_supplement(directory)


def test_a_handoff_disagreeing_on_the_parent_is_refused(tmp_path: Path) -> None:
    directory = _write_supplement(
        tmp_path, game_id="game-1", handoff_overrides={"parentBundleSha256": "b" * 64})
    with pytest.raises(PlayGeometryImportError, match="parent bundle"):
        verify_supplement(directory)


# -- registration -----------------------------------------------------------


@pytest.fixture
def cli_session(session):
    """A session shaped like the offline importer's.

    Registration only ever runs from ``scripts/mlb_v18_play_geometry.py``, which
    does not install the ORM immutability guards; the API process installs them
    and must never register anything. The guard itself is asserted separately by
    ``test_the_api_process_can_never_register_geometry``.
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



def _shadow_row(session, game, *, bundle_sha: str = PARENT_SHA, events: int = 3):
    bundle = ShadowForecastBundle(
        bundle_sha256=bundle_sha, contract_version="AISP_MLB_V18_RESEARCH_SHADOW_BUNDLE_V1",
        bundle_id="mlb-v18-test", logical_uri="/tmp/bundle", source_fixture_id="contract",
        slate_date=SLATE, league="MLB", sport="MLB", status="RESEARCH_SHADOW",
        research_shadow_only=True, generated_at=game.starts_at, forecast_count=1,
        quote_count=0, candidate_count=0, imported_at=game.starts_at,
    )
    session.add(bundle)
    session.flush()
    row = ShadowForecast(
        bundle_id=bundle.id, canonical_game_id=game.id,
        forecast_id=f"mlb-v18-{bundle_sha[:12]}-{game.id}",
        engine_id=ENGINE_ID, engine_version="mlb-v18-test",
        scheduled_start_utc=game.starts_at,
        model_artifact_sha256="c" * 64, simulator_id="V18_WHOLE_GAME_MONTE_CARLO",
        simulator_version="v1", simulator_seed=20260907, event_id=str(game.id),
        source_event_id="1", event_date=SLATE, season=2026,
        home_team_id="home", away_team_id="away", is_ready=True,
        readiness_score=1.0, readiness_threshold=1.0, status="PROSPECTIVE_RESEARCH_SHADOW",
        full_home_win_probability=0.43, full_away_win_probability=0.57,
        full_expected_home_score=3.5, full_expected_away_score=4.4,
        distributions={}, uncertainty={"simulationCount": 5000}, readiness_detail={},
        identity_evidence={},
        lineage_detail={
            "bundleSha256": bundle_sha, "pathDigest": "d" * 64,
            "sequentialPath": [[{"pa": index + 1} for index in range(events)]],
        },
    )
    session.add(row)
    session.flush()
    return row


@pytest.mark.asyncio
async def test_registration_is_idempotent_and_additive(
    cli_session, session, settings, tmp_path,
) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    row = _shadow_row(session, game)
    directory = _write_supplement(tmp_path, game_id=str(game.id))
    verified = verify_supplement(directory)

    gen1_before = [
        (item.id, item.home_win_probability, item.version)
        for item in session.scalars(select(Forecast))
    ]
    shadow_before = (row.full_home_win_probability, row.status, row.simulator_seed)

    first = register_geometry(session, verified)
    assert first["registered"] == [row.forecast_id]
    session.commit()

    second = register_geometry(session, verified)
    assert second["registered"] == []
    assert second["alreadyRegistered"] == [row.forecast_id]

    # Additive: the accepted sequential path and every other field survive.
    lineage = row.lineage_detail
    assert lineage["sequentialPath"] == [[{"pa": index + 1} for index in range(3)]]
    assert lineage["pathDigest"] == "d" * 64
    assert lineage[LINEAGE_KEY]["supplementSha256"] == verified.supplement_sha256
    assert lineage[LINEAGE_KEY]["wageringAuthority"] is False
    assert (row.full_home_win_probability, row.status, row.simulator_seed) == shadow_before

    # Gen 1 is untouched.
    assert [
        (item.id, item.home_win_probability, item.version)
        for item in session.scalars(select(Forecast))
    ] == gen1_before


@pytest.mark.asyncio
async def test_a_parent_bundle_mismatch_fails_closed(session, settings, tmp_path) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    _shadow_row(session, game, bundle_sha="f" * 64)
    directory = _write_supplement(tmp_path, game_id=str(game.id))
    with pytest.raises(PlayGeometryImportError, match="No registered V18 shadow forecast"):
        register_geometry(session, verify_supplement(directory))


@pytest.mark.asyncio
async def test_a_geometry_path_for_another_game_is_skipped(session, settings, tmp_path) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    row = _shadow_row(session, game)
    directory = _write_supplement(tmp_path, game_id="a-different-game")
    outcome = register_geometry(session, verify_supplement(directory))
    assert outcome["registered"] == []
    assert "no geometry path for this game" in outcome["skipped"][0]["reason"]
    assert LINEAGE_KEY not in (row.lineage_detail or {})


@pytest.mark.asyncio
async def test_a_path_length_mismatch_with_the_accepted_trace_is_skipped(
    session, settings, tmp_path,
) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    row = _shadow_row(session, game, events=3)
    directory = _write_supplement(tmp_path, game_id=str(game.id), events=5)
    outcome = register_geometry(session, verify_supplement(directory))
    assert outcome["registered"] == []
    assert "differs from the accepted V18 trace" in outcome["skipped"][0]["reason"]
    assert LINEAGE_KEY not in (row.lineage_detail or {})


@pytest.mark.asyncio
async def test_the_api_process_can_never_register_geometry(session, settings, tmp_path) -> None:
    """With the immutability guards installed, no shadow forecast may be written.

    Registration is an offline, checksum-verified import; the serving process
    has no path to it.
    """

    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    _shadow_row(session, game)
    directory = _write_supplement(tmp_path, game_id=str(game.id))
    with pytest.raises(ImmutableRecordError):
        register_geometry(session, verify_supplement(directory))



# -- read model -------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_payload_reports_counts_and_a_pending_population(
    cli_session, session, settings, tmp_path,
) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    row = _shadow_row(session, game)
    directory = _write_supplement(tmp_path, game_id=str(game.id))
    register_geometry(session, verify_supplement(directory))
    session.commit()

    payload = geometry_payload(session, row.forecast_id)
    assert payload is not None
    assert payload["plate_appearances"] == 3
    assert payload["contact_plays"] == 2
    assert payload["ball_traces"] == 2
    assert payload["runner_movements"] == 3
    assert payload["wagering_authority"] is False
    assert payload["execution_allowed_in_aisp"] is False
    assert payload["display_authority"] == "RESEARCH_SHADOW_PLAY_GEOMETRY"
    # The population is a separate fact and stays honestly unavailable.
    assert payload["population"]["simulationCount"] == 5000
    assert payload["population"]["populationGeometryAvailable"] is False

    status = geometry_status(session)
    entry = next(item for item in status["forecasts"] if item["forecastId"] == row.forecast_id)
    assert entry["geometryRegistered"] is True
    assert entry["geometryEvents"] == 3


@pytest.mark.asyncio
async def test_an_unregistered_forecast_returns_no_payload(session, settings) -> None:
    await seed_demo_data(session, settings, SLATE)
    game = session.get(Game, session.scalar(select(Forecast)).game_id)
    row = _shadow_row(session, game)
    assert geometry_payload(session, row.forecast_id) is None
    assert geometry_payload(session, "not-a-forecast") is None
