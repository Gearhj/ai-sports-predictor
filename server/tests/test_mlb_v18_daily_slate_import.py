"""Focused checks for immutable V18 daily-slate ingestion."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.models import Forecast, Game, ShadowForecast, ShadowForecastBundle
from app.shadow_import.mlb_v18_daily import (
    DailyV18ImportError,
    forecast_identity,
    register_daily_slate,
    verify_daily_slate,
)
from app.shadow_import.mlb_v18_read import shadow_comparison

SLATE = date(2026, 8, 12)


def _bytes(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha(value) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_bytes(value))


def _daily_slate(
    tmp_path: Path,
    *,
    game_id: str,
    skip_game_id: str,
    scheduled: datetime,
    captured: datetime | None = None,
) -> tuple[Path, Path, str]:
    captured = captured or scheduled - timedelta(hours=5)
    forecast = {
        "schemaVersion": "MLB_V18_DAILY_GAME_FORECAST_V1",
        "gameId": game_id,
        "sourceGameId": "824792",
        "capturedAt": captured.isoformat(),
        "scheduledStart": scheduled.isoformat(),
        "researchShadowOnly": True,
        "wageringAuthority": False,
        "promotionStatus": "NOT_PROMOTED",
        "executionAllowedInAISP": False,
        "snapshotContentSha256": "e" * 64,
        "probabilities": {"homeWin": 0.5172, "awayWin": 0.4828},
        "expectations": {
            "homeRuns": 4.4222,
            "awayRuns": 4.5462,
            "totalRuns": 8.9684,
            "homeMinusAwayRuns": -0.124,
        },
        "rates": {"oneRunGame": 0.2856, "extraInning": 0.0946},
        "distributions": {
            "homeRuns": [{"value": 4, "probability": 1.0}],
            "awayRuns": [{"value": 3, "probability": 1.0}],
            "totalRuns": [{"value": 7, "probability": 1.0}],
            "homeMinusAwayRuns": [{"value": 1, "probability": 1.0}],
        },
        "simulation": {
            "count": 5000,
            "seed": 20260907,
            "seedPolicy": "MASTER_SEED_PLUS_SHA256_GAME_ID_SUBSTREAMS",
            "deterministicReplay": True,
            "illegalStateCount": 0,
            "nonterminationCount": 0,
            "representativePathCount": 1,
        },
        "monteCarloUncertainty": {
            "homeWinProbabilityStandardError": 0.007,
            "homeWinProbabilityInterval": [0.50348, 0.53092],
            "method": "MONTE_CARLO_NORMAL_APPROXIMATION_95_PERCENT",
        },
        "parentAuthority": {
            "bundleId": "mlb-v18-whole-game-test",
            "bundleSha256": "a" * 64,
            "paModelSha256": "b" * 64,
            "acceptanceProtocolV2Sha256": "c" * 64,
        },
        "teams": {
            "AWAY": {"uuid": "away", "abbreviation": "CLE"},
            "HOME": {"uuid": "home", "abbreviation": "BAL"},
        },
        "lineupAuthorities": {
            "AWAY": {"status": "PROJECTED_PRIOR_COMPLETE_LINEUP"},
            "HOME": {"status": "PROJECTED_PRIOR_COMPLETE_LINEUP"},
        },
        "starters": {
            "AWAY": {"name": "Away Starter", "confirmationState": "PROBABLE"},
            "HOME": {"name": "Home Starter", "confirmationState": "PROBABLE"},
        },
        "knownLimitations": ["test limitation"],
    }
    evidence = {
        "schemaVersion": "AISP_MLB_READ_ONLY_PREGAME_SNAPSHOT_V2",
        "contentSha256": "e" * 64,
        "capturedAt": captured.isoformat(),
        "captureMode": "PERSISTED_AISP_EVIDENCE_SELECT_ONLY_NO_PROVIDER_CALLS",
        "providerCalls": 0,
        "aispRowsModified": 0,
        "marketDataRead": False,
        "outcomesRead": False,
        "game": {"game_id": game_id},
    }
    path = {
        "schemaVersion": "MLB_V18_DAILY_REPRESENTATIVE_PATH_V1",
        "gameId": game_id,
        "simulationIndex": 0,
        "events": [{
            "pa": 1, "inning": 1, "half": "TOP", "outcome": "STRIKEOUT",
            "outsBefore": 0, "outsAfter": 1, "baseMaskBefore": 0,
            "baseMaskAfter": 0, "runsScored": 0, "homeScoreAfter": 0,
            "awayScoreAfter": 0, "pitcherState": "STARTER",
            "pitchingChange": False, "battingOrderPosition": 1,
        }],
    }
    execution = {"notExecutableInAisp": True}
    files = {
        "forecast.json": forecast,
        "pregame-evidence.json": evidence,
        "representative-path.json": path,
        "execution-inputs.json": execution,
    }
    artifact_records = [
        {
            "relativePath": name,
            "bytes": len(_bytes(value)),
            "sha256": hashlib.sha256(_bytes(value)).hexdigest(),
        }
        for name, value in sorted(files.items())
    ]
    manifest_body = {
        "schemaVersion": "MLB_V18_DAILY_GAME_OBJECT_MANIFEST_V1",
        "publicationKey": "d" * 64,
        "gameId": game_id,
        "parentBundleSha256": "a" * 64,
        "artifacts": artifact_records,
        "researchShadowOnly": True,
        "wageringAuthority": False,
        "promotionStatus": "NOT_PROMOTED",
        "executionAllowedInAISP": False,
    }
    object_sha = _sha(manifest_body)
    root = tmp_path / "daily"
    object_root = root / "objects" / "sha256" / object_sha
    for name, value in files.items():
        _write(object_root / name, value)
    _write(object_root / "manifest.json", {
        **manifest_body,
        "objectId": f"mlb-v18-daily-game-{object_sha[:24]}",
        "objectSha256": object_sha,
    })

    handoff_body = {
        "schemaVersion": "AISP_MLB_V18_DAILY_SLATE_HANDOFF_V1",
        "slateDate": SLATE.isoformat(),
        "generatedAt": captured.isoformat(),
        "parentBundleId": "mlb-v18-whole-game-test",
        "parentBundleSha256": "a" * 64,
        "acceptanceProtocolV2Sha256": "c" * 64,
        "forecasts": [{
            "gameId": game_id,
            "objectSha256": object_sha,
            "relativePath": f"objects/sha256/{object_sha}",
            "forecastFile": "forecast.json",
            "manifestFile": "manifest.json",
        }],
        "consumerRules": ["preserve Gen 1"],
        "researchShadowOnly": True,
        "wageringAuthority": False,
        "promotionStatus": "NOT_PROMOTED",
        "executionAllowedInAISP": False,
    }
    handoff_sha = _sha(handoff_body)
    handoff_path = root / "handoffs" / "sha256" / handoff_sha / "aisp-handoff.json"
    _write(handoff_path, {
        **handoff_body,
        "handoffId": f"mlb-v18-daily-handoff-{handoff_sha[:24]}",
        "handoffSha256": handoff_sha,
    })

    receipt_games = [
        {
            "status": "PUBLISHED", "gameId": game_id,
            "objectSha256": object_sha,
        },
        {
            "status": "SKIPPED", "gameId": skip_game_id,
            "category": "MISSING_REQUIRED_EVIDENCE",
            "reason": "starter identity and timestamp are required",
            "capturedAt": captured.isoformat(),
            "scheduledStart": scheduled.isoformat(),
        },
    ]
    receipt_body = {
        "schemaVersion": "MLB_V18_DAILY_SLATE_RECEIPT_V1",
        "slateDate": SLATE.isoformat(),
        "generatedAt": captured.isoformat(),
        "handoffSha256": handoff_sha,
        "handoffForecasts": 1,
        "games": receipt_games,
        "gamesSkipped": 1,
        "researchShadowOnly": True,
        "wageringAuthority": False,
        "promotionStatus": "NOT_PROMOTED",
        "executionAllowedInAISP": False,
    }
    receipt_sha = _sha(receipt_body)
    receipt_path = (
        root / "slates" / SLATE.isoformat() / "sha256" / receipt_sha
        / "slate-result.json"
    )
    _write(receipt_path, {
        **receipt_body,
        "receiptId": f"mlb-v18-daily-slate-{receipt_sha[:24]}",
        "receiptSha256": receipt_sha,
    })
    return handoff_path, receipt_path, object_sha


@pytest.mark.asyncio
async def test_aggregate_import_is_idempotent_and_preserves_gen1(
    session, settings, tmp_path,
):
    await seed_demo_data(session, settings, SLATE)
    forecasts = session.scalars(select(Forecast).order_by(Forecast.game_id)).all()
    assert len(forecasts) >= 2
    game = session.get(Game, forecasts[0].game_id)
    skip_game = session.get(Game, forecasts[1].game_id)
    handoff, receipt, object_sha = _daily_slate(
        tmp_path,
        game_id=str(game.id),
        skip_game_id=str(skip_game.id),
        scheduled=game.starts_at,
    )
    verified = verify_daily_slate(handoff, receipt)
    before = [(row.id, row.version, row.home_win_probability) for row in forecasts]

    first = register_daily_slate(session, verified)
    session.commit()
    second = register_daily_slate(session, verified)

    assert first.imported == [str(game.id)]
    assert first.already_present == []
    assert second.imported == []
    assert second.already_present == [str(game.id)]
    assert first.rejected == second.rejected == []
    row = session.scalar(select(ShadowForecast).where(
        ShadowForecast.forecast_id == forecast_identity(object_sha)
    ))
    assert row is not None
    assert row.forecast_cutoff_utc == game.starts_at - timedelta(hours=5)
    assert row.lineage_detail["objectSha256"] == object_sha
    assert row.lineage_detail["wageringAuthority"] is False
    assert row.lineage_detail["sequentialPath"][0]
    comparison = shadow_comparison(session, game.id)
    assert comparison["shadows"][0]["forecast_id"] == forecast_identity(object_sha)
    expected_capture = game.starts_at - timedelta(hours=5)
    if expected_capture.tzinfo is None:
        expected_capture = expected_capture.replace(tzinfo=UTC)
    assert comparison["shadows"][0]["generated_at"] == expected_capture.isoformat()
    assert comparison["shadows"][0]["available_views"] == {
        "broadcast": True, "geometry": False, "population": False,
    }
    assert comparison["shadows"][0]["population"]["total_run_distribution"] == [
        {"value": 7.0, "probability": 1.0}
    ]
    bundles = session.scalars(select(ShadowForecastBundle).where(
        ShadowForecastBundle.bundle_sha256 == verified.handoff_sha256
    )).all()
    assert len(bundles) == 1
    assert bundles[0].evidence["publisherSkips"][0]["gameId"] == str(skip_game.id)
    after = [
        (row.id, row.version, row.home_win_probability)
        for row in session.scalars(select(Forecast).order_by(Forecast.game_id)).all()
    ]
    assert after == before


@pytest.mark.asyncio
async def test_hash_rejection_occurs_before_registration(session, settings, tmp_path):
    await seed_demo_data(session, settings, SLATE)
    forecasts = session.scalars(select(Forecast).order_by(Forecast.game_id)).all()
    game = session.get(Game, forecasts[0].game_id)
    skip_game = session.get(Game, forecasts[1].game_id)
    handoff, receipt, object_sha = _daily_slate(
        tmp_path,
        game_id=str(game.id),
        skip_game_id=str(skip_game.id),
        scheduled=game.starts_at,
    )
    target = tmp_path / "daily" / "objects" / "sha256" / object_sha / "forecast.json"
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(DailyV18ImportError, match="byte count mismatch"):
        verify_daily_slate(handoff, receipt)
    assert session.scalars(select(ShadowForecast)).all() == []


@pytest.mark.asyncio
async def test_capture_at_first_pitch_is_rejected(session, settings, tmp_path):
    await seed_demo_data(session, settings, SLATE)
    forecasts = session.scalars(select(Forecast).order_by(Forecast.game_id)).all()
    game = session.get(Game, forecasts[0].game_id)
    skip_game = session.get(Game, forecasts[1].game_id)
    handoff, receipt, _ = _daily_slate(
        tmp_path,
        game_id=str(game.id),
        skip_game_id=str(skip_game.id),
        scheduled=game.starts_at,
        captured=game.starts_at,
    )
    with pytest.raises(DailyV18ImportError, match="at or after first pitch"):
        verify_daily_slate(handoff, receipt)


@pytest.mark.asyncio
async def test_consumer_fast_path_only_skips_a_package_that_is_already_fully_registered(
    session, settings, tmp_path,
):
    from app.jobs.v18_exchange_consumer import already_fully_registered

    await seed_demo_data(session, settings, SLATE)
    forecasts = session.scalars(select(Forecast).order_by(Forecast.game_id)).all()
    game = session.get(Game, forecasts[0].game_id)
    skip_game = session.get(Game, forecasts[1].game_id)
    handoff, receipt, object_sha = _daily_slate(
        tmp_path, game_id=str(game.id), skip_game_id=str(skip_game.id), scheduled=game.starts_at,
    )
    verified = verify_daily_slate(handoff, receipt)

    # Nothing registered yet: the full path must run.
    assert already_fully_registered(session, verified) is False
    register_daily_slate(session, verified)
    session.commit()
    # Same handoff, every forecast present: registration would change nothing.
    assert already_fully_registered(session, verified) is True

    # A package that names the same object for a different game is never
    # waved through (rows are append-only, so vary the incoming package).
    from dataclasses import replace
    from types import SimpleNamespace

    item = verified.forecasts[0]
    moved = replace(item, forecast={**item.forecast, "gameId": str(skip_game.id)})
    assert already_fully_registered(
        session, SimpleNamespace(forecasts=(moved,), handoff_sha256=verified.handoff_sha256)) is False

    # A new cumulative handoff (no bundle row yet) takes the full path even
    # though its forecast is already present.
    assert already_fully_registered(
        session, SimpleNamespace(forecasts=verified.forecasts, handoff_sha256="f" * 64)) is False


def test_verification_memo_is_invalidated_by_a_changed_manifest_and_by_time(tmp_path, monkeypatch):
    from app.jobs import v18_exchange_consumer as consumer

    package = tmp_path / ("a" * 64)
    package.mkdir()
    manifest = package / consumer.PACKAGE_FILE
    manifest.write_text("{}", encoding="utf-8")
    consumer._VERIFIED_MEMO.clear()
    assert consumer._memo_hit(package.name, package) is False
    consumer._memo_store(package.name, package)
    assert consumer._memo_hit(package.name, package) is True
    manifest.write_text('{"changed": true}', encoding="utf-8")
    assert consumer._memo_hit(package.name, package) is False  # any change re-verifies
    consumer._memo_store(package.name, package)
    monkeypatch.setattr(consumer, "MEMO_TTL_SECONDS", 0)
    assert consumer._memo_hit(package.name, package) is False  # the TTL forces a full re-check
    consumer._VERIFIED_MEMO.clear()

