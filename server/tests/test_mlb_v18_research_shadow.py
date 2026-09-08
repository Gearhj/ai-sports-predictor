"""Focused checks for the MLB V18 research-shadow track.

Covered, and nothing else:

* bundle verification fails closed on a tampered artifact or an unsafe handoff
* registration is idempotent and isolated from Gen 1 / wagering
* a forecast captured at or after first pitch is never registered as prospective
* the sequential path survives a round trip and keeps its digest
* the quota-recovery probe respects staleness, cooldown and accounting
* a model forecast stays visible when no sportsbook quote exists
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.jobs.odds_quota_recovery import (
    PROBE_ENDPOINT_KEY,
    missing_market_reason,
    quota_state,
)
from app.models import (
    Forecast,
    Game,
    IngestionRun,
    Market,
    MarketType,
    ProviderRequestLedger,
    RunStatus,
    ShadowForecast,
)
from app.shadow_import.mlb_v18 import (
    ENGINE_ID,
    V18ImportError,
    prospective_rejection,
    sampling_uncertainty,
    verify_bundle,
    verify_contract,
)
from app.shadow_import.mlb_v18_read import sequential_path, shadow_comparison
from app.shadow_import.mlb_v18_registry import register_bundle

SLATE = date(2026, 8, 12)


# -- fixtures -------------------------------------------------------------


def _write_bundle(root: Path, *, forecast_overrides: dict | None = None,
                  handoff_overrides: dict | None = None) -> Path:
    """A minimal but structurally faithful V18 bundle, addressed by its hash."""

    forecast = {
        "schemaVersion": "MLB_V18_PROSPECTIVE_WHOLE_GAME_SIMULATION_V1",
        "track": "PROSPECTIVE_RESEARCH_SHADOW",
        "gameId": "00000000-0000-0000-0000-000000000000",
        "capturedAt": "2026-08-12T17:00:00+00:00",
        "scheduledStart": "2026-08-12T23:05:00+00:00",
        "homeWinProbability": 0.4334,
        "meanHomeRuns": 3.47,
        "meanAwayRuns": 4.42,
        "simulationCount": 5000,
        "masterSeed": 20260907,
        "paFold": 5,
        "transitionFold": 4,
        "illegalStateCount": 0,
        "nonterminationCount": 0,
        "deterministicReplayVerified": True,
        "lineupModes": ["PROJECTED_PRIOR_COMPLETE_LINEUP"] * 2,
        "traces": [[{"pa": 1, "inning": 1, "half": "TOP", "outcome": "STRIKEOUT",
                     "outsBefore": 0, "outsAfter": 1, "baseMaskBefore": 0,
                     "baseMaskAfter": 0, "runsScored": 0, "homeScoreAfter": 0,
                     "awayScoreAfter": 0, "pitcherState": "STARTER",
                     "pitchingChange": False, "battingOrderPosition": 1}]],
    }
    forecast.update(forecast_overrides or {})

    handoff = {
        "schemaVersion": "AISP_MLB_V18_RESEARCH_SHADOW_HANDOFF_V1",
        "sport": "MLB",
        "season": 2026,
        "status": "RESEARCH_SHADOW",
        "promotionStatus": "NOT_PROMOTED",
        "wageringAuthority": False,
        "executionAllowed": False,
        "marketDataRequired": False,
        "forecastFile": "prospective-forecast.json",
        "pregameEvidenceFile": "aisp-pregame-snapshot.json",
        "paModelFile": "executable/v18-pa-fold5.joblib",
    }
    handoff.update(handoff_overrides or {})

    snapshot = {
        "schemaVersion": "AISP_MLB_READ_ONLY_PREGAME_SNAPSHOT_V1",
        "capturedAt": "2026-08-12T17:00:00+00:00",
        "captureMode": "PERSISTED_AISP_EVIDENCE_SELECT_ONLY_NO_PROVIDER_CALLS",
        "contentSha256": "0" * 64,
        "marketDataRead": False,
        "outcomesRead": False,
        "aispRowsModified": 0,
        "game": {"source_game_id": "824958", "venue": "Test Park"},
        "sides": [
            {"side": "AWAY", "team": {"abbreviation": "TOR"},
             "lineupAuthority": {"status": "PROJECTED_PRIOR_COMPLETE_LINEUP",
                                 "observedAt": "2026-08-11T18:00:00+00:00",
                                 "source": "MLB_STATS_API"},
             "starter": {"name": "Test Starter", "confirmationState": "PROBABLE",
                         "observedAt": "2026-08-12T16:00:00+00:00"}},
        ],
    }

    files = {
        "prospective-forecast.json": json.dumps(forecast).encode(),
        "aisp-handoff.json": json.dumps(handoff).encode(),
        "aisp-pregame-snapshot.json": json.dumps(snapshot).encode(),
        "authority-inventory.json": b"{}",
        "calibration.json": b"{}",
        "executable/v18-pa-fold5.joblib": b"not-a-real-model",
    }

    manifest = {
        "schemaVersion": "MLB_V18_WHOLE_GAME_CHALLENGER_BUNDLE_V1",
        "bundleId": "mlb-v18-test",
        "bundleSha256": "",
        "calibrationDecision": "RETAIN_RAW_V18",
        "limitations": ["projected lineups are carried forward"],
        "simulation": {"count": 5000, "masterSeed": 20260907, "policy": "TEST"},
        "featureContract": {"manifestSha256": "a" * 64, "rawInputs": 246,
                            "treatedInputs": 273},
        "artifacts": [
            {"relativePath": name, "bytes": len(payload),
             "sha256": hashlib.sha256(payload).hexdigest()}
            for name, payload in files.items()
        ],
        "executionPayload": [
            {"relativePath": "executable/v18-pa-fold5.joblib",
             "bytes": len(files["executable/v18-pa-fold5.joblib"]),
             "sha256": hashlib.sha256(
                 files["executable/v18-pa-fold5.joblib"]).hexdigest()},
        ],
    }
    address = hashlib.sha256(
        json.dumps(manifest["artifacts"], sort_keys=True).encode()
    ).hexdigest()
    manifest["bundleSha256"] = address

    directory = root / address
    for name, payload in files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    return _write_bundle(tmp_path)


# -- bundle verification --------------------------------------------------


def test_verified_bundle_reports_its_identity(bundle_dir: Path):
    bundle = verify_bundle(bundle_dir)
    assert bundle.bundle_id == "mlb-v18-test"
    assert bundle.bundle_sha256 == bundle_dir.name
    assert bundle.artifact_count == 6
    assert len(bundle.forecasts) == 1


def test_a_tampered_artifact_fails_closed(bundle_dir: Path):
    # One byte changed in the serialized model is enough.
    target = bundle_dir / "executable" / "v18-pa-fold5.joblib"
    target.write_bytes(b"tampered-model!!")
    with pytest.raises(V18ImportError, match="checksum mismatch"):
        verify_bundle(bundle_dir)


def test_a_missing_artifact_fails_closed(bundle_dir: Path):
    (bundle_dir / "calibration.json").unlink()
    with pytest.raises(V18ImportError, match="missing"):
        verify_bundle(bundle_dir)


def test_a_bundle_claiming_wagering_authority_is_refused(tmp_path: Path):
    directory = _write_bundle(tmp_path, handoff_overrides={"wageringAuthority": True})
    with pytest.raises(V18ImportError, match="wageringAuthority"):
        verify_bundle(directory)


def test_a_bundle_claiming_execution_is_refused(tmp_path: Path):
    directory = _write_bundle(tmp_path, handoff_overrides={"executionAllowed": True})
    with pytest.raises(V18ImportError, match="executionAllowed"):
        verify_bundle(directory)


def test_a_promoted_bundle_is_refused(tmp_path: Path):
    directory = _write_bundle(tmp_path, handoff_overrides={"promotionStatus": "PROMOTED"})
    with pytest.raises(V18ImportError, match="promotionStatus"):
        verify_bundle(directory)


def test_contract_must_reference_this_bundle(tmp_path: Path, bundle_dir: Path):
    contract = {"schemaVersion": "MLB_V18_EVALUATION_CONTRACT_SUPPLEMENT_MANIFEST_V1",
                "contractId": "other", "contractSha256": "",
                "referencedBundleSha256": "b" * 64}
    address = "c" * 64
    contract["contractSha256"] = address
    directory = tmp_path / "contracts" / address
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps(contract), encoding="utf-8")
    (directory / "evaluation-contract.json").write_text("{}", encoding="utf-8")
    with pytest.raises(V18ImportError, match="different bundle"):
        verify_contract(directory, bundle_sha256=bundle_dir.name)


# -- pregame timing -------------------------------------------------------


def test_a_capture_at_or_after_first_pitch_is_not_prospective():
    now = datetime(2026, 8, 13, tzinfo=UTC)
    late = {"capturedAt": "2026-08-12T23:30:00+00:00",
            "scheduledStart": "2026-08-12T23:05:00+00:00",
            "illegalStateCount": 0, "nonterminationCount": 0,
            "simulationCount": 5000, "deterministicReplayVerified": True,
            "track": "PROSPECTIVE_RESEARCH_SHADOW", "homeWinProbability": 0.5}
    assert "not before the scheduled start" in (prospective_rejection(late, now=now) or "")

    exact = {**late, "capturedAt": "2026-08-12T23:05:00+00:00"}
    assert prospective_rejection(exact, now=now) is not None


def test_operational_gates_reject_unsound_simulations():
    base = {"capturedAt": "2026-08-12T17:00:00+00:00",
            "scheduledStart": "2026-08-12T23:05:00+00:00",
            "illegalStateCount": 0, "nonterminationCount": 0,
            "simulationCount": 5000, "deterministicReplayVerified": True,
            "track": "PROSPECTIVE_RESEARCH_SHADOW", "homeWinProbability": 0.5}
    now = datetime(2026, 8, 12, 18, tzinfo=UTC)
    assert prospective_rejection(base, now=now) is None
    assert "illegal states" in prospective_rejection({**base, "illegalStateCount": 1}, now=now)
    assert "nonterminating" in prospective_rejection({**base, "nonterminationCount": 2}, now=now)
    assert "below the contract minimum" in prospective_rejection({**base, "simulationCount": 100}, now=now)
    assert "deterministic replay" in prospective_rejection(
        {**base, "deterministicReplayVerified": False}, now=now)


def test_sampling_uncertainty_is_monte_carlo_only():
    result = sampling_uncertainty(0.4334, 5000)
    assert result["simulationCount"] == 5000
    assert result["excludes"] == "MODEL_UNCERTAINTY"
    assert result["monteCarlo95HalfWidth"] == pytest.approx(0.013736, abs=1e-5)


# -- registration ---------------------------------------------------------


@pytest.mark.asyncio
async def test_registration_is_idempotent_and_isolated(session, settings, tmp_path):
    await seed_demo_data(session, settings, SLATE)
    market = session.scalar(select(Market).where(Market.market_type == MarketType.MONEYLINE))
    game = session.get(Game, market.game_id)

    directory = _write_bundle(tmp_path, forecast_overrides={
        "gameId": str(game.id),
        "scheduledStart": game.starts_at.isoformat(),
        "capturedAt": (game.starts_at - timedelta(hours=5)).isoformat(),
    })
    bundle = verify_bundle(directory)

    gen1_before = [
        (row.id, row.home_win_probability, row.version)
        for row in session.scalars(select(Forecast).where(Forecast.game_id == game.id))
    ]

    first = register_bundle(session, bundle, None)
    assert first.registered == [str(game.id)]
    assert first.bundle_row_created is True
    session.commit()

    second = register_bundle(session, bundle, None)
    assert second.registered == []
    assert second.already_present == [str(game.id)]
    assert second.bundle_row_created is False
    assert second.persisted_count == 1

    rows = session.scalars(select(ShadowForecast).where(
        ShadowForecast.engine_id == ENGINE_ID)).all()
    assert len(rows) == 1
    row = rows[0]
    # Shadow isolation: a distinct engine track, no wagering authority anywhere.
    assert row.engine_id == ENGINE_ID != "AISP_BASELINE"
    assert row.status == "PROSPECTIVE_RESEARCH_SHADOW"
    assert row.snapshot_type == "PROSPECTIVE"
    assert row.lineage_detail["wageringAuthority"] is False
    assert row.lineage_detail["gen1HistoryMutated"] is False
    assert row.lineage_detail["participationPolicy"]["executionAllowed"] is False

    # Gen 1 is byte-for-byte what it was.
    gen1_after = [
        (r.id, r.home_win_probability, r.version)
        for r in session.scalars(select(Forecast).where(Forecast.game_id == game.id))
    ]
    assert gen1_after == gen1_before


@pytest.mark.asyncio
async def test_a_post_start_forecast_is_skipped_with_a_reason(session, settings, tmp_path):
    await seed_demo_data(session, settings, SLATE)
    market = session.scalar(select(Market).where(Market.market_type == MarketType.MONEYLINE))
    game = session.get(Game, market.game_id)

    directory = _write_bundle(tmp_path, forecast_overrides={
        "gameId": str(game.id),
        "scheduledStart": game.starts_at.isoformat(),
        "capturedAt": (game.starts_at + timedelta(minutes=1)).isoformat(),
    })
    outcome = register_bundle(session, verify_bundle(directory), None)
    assert outcome.registered == []
    assert outcome.skipped and "not before the scheduled start" in outcome.skipped[0][1]
    assert session.scalars(select(ShadowForecast)).all() == []


@pytest.mark.asyncio
async def test_an_unknown_game_is_skipped_not_invented(session, settings, tmp_path):
    await seed_demo_data(session, settings, SLATE)
    directory = _write_bundle(tmp_path, forecast_overrides={
        "gameId": "11111111-2222-3333-4444-555555555555",
    })
    outcome = register_bundle(session, verify_bundle(directory), None)
    assert outcome.registered == []
    assert "no AISP game matches" in outcome.skipped[0][1]


# -- path persistence and reload -----------------------------------------


@pytest.mark.asyncio
async def test_sequential_path_reloads_with_a_stable_digest(session, settings, tmp_path):
    await seed_demo_data(session, settings, SLATE)
    market = session.scalar(select(Market).where(Market.market_type == MarketType.MONEYLINE))
    game = session.get(Game, market.game_id)
    directory = _write_bundle(tmp_path, forecast_overrides={
        "gameId": str(game.id),
        "scheduledStart": game.starts_at.isoformat(),
        "capturedAt": (game.starts_at - timedelta(hours=5)).isoformat(),
    })
    bundle = verify_bundle(directory)
    register_bundle(session, bundle, None)
    session.commit()

    row = session.scalar(select(ShadowForecast).where(ShadowForecast.engine_id == ENGINE_ID))
    payload = sequential_path(session, row.forecast_id)
    assert payload is not None
    assert payload["path_count"] == 1
    event = payload["paths"][0][0]
    # Genuine V18 play-by-play fields, not a Gen 1 final-score curve.
    for key in ("pa", "inning", "half", "outsBefore", "baseMaskAfter",
                "homeScoreAfter", "pitchingChange"):
        assert key in event
    assert payload["path_digest"] == row.lineage_detail["pathDigest"]
    # Re-verifying the same bundle recomputes the same digest.
    assert verify_bundle(directory).forecasts[0]["traces"] == payload["paths"]


@pytest.mark.asyncio
async def test_comparison_pairs_two_pregame_forecasts_without_ranking_them(
    session, settings, tmp_path,
):
    await seed_demo_data(session, settings, SLATE)
    market = session.scalar(select(Market).where(Market.market_type == MarketType.MONEYLINE))
    game = session.get(Game, market.game_id)
    directory = _write_bundle(tmp_path, forecast_overrides={
        "gameId": str(game.id),
        "scheduledStart": game.starts_at.isoformat(),
        "capturedAt": (game.starts_at - timedelta(hours=5)).isoformat(),
    })
    register_bundle(session, verify_bundle(directory), None)
    session.commit()

    payload = shadow_comparison(session, game.id)
    assert payload is not None
    shadow = payload["shadows"][0]
    assert shadow["wagering_authority"] is False
    assert payload["official"]["wagering_authority"] is True
    assert shadow["difference_orientation"] == "V18_MINUS_GEN1"
    if shadow["paired_with_official"]:
        assert shadow["difference_vs_official"] == pytest.approx(
            shadow["home_win_probability"]
            - payload["official"]["home_win_probability"],
            abs=1e-6,
        )
    # No verdict is attached to the shadow itself; the only mention of
    # "better/worse" is the note that explicitly disclaims one.
    assert "better" not in json.dumps(shadow).lower()
    assert "not presented as better or worse" in payload["notes"]


# -- quota recovery -------------------------------------------------------


def _record_run(session, *, remaining: int, completed_at: datetime) -> None:
    session.add(IngestionRun(
        source="THE_ODDS_API", job_name="refresh-odds", status=RunStatus.SUCCEEDED,
        started_at=completed_at - timedelta(seconds=5), completed_at=completed_at,
        records_processed=1, warnings=[], schema_version="1",
        source_metadata={"requests_remaining": remaining, "requests_used": 492},
    ))
    session.flush()


def test_a_fresh_low_reading_is_respected(session):
    now = datetime(2026, 9, 7, 20, tzinfo=UTC)
    _record_run(session, remaining=8, completed_at=now - timedelta(hours=2))
    state = quota_state(session, floor=10, probe_after_hours=24,
                        probe_cooldown_hours=24, now=now)
    assert state.latched is True
    assert state.probe_allowed is False
    assert "trust window" in state.probe_blocked_reason


def test_a_stale_low_reading_permits_exactly_one_probe(session):
    now = datetime(2026, 9, 7, 20, tzinfo=UTC)
    _record_run(session, remaining=8, completed_at=now - timedelta(hours=56))
    state = quota_state(session, floor=10, probe_after_hours=24,
                        probe_cooldown_hours=24, now=now)
    assert state.latched is True and state.probe_allowed is True
    assert state.remaining == 8 and state.floor == 10

    # A ledgered probe attempt starts the cooldown, whatever its outcome.
    session.add(ProviderRequestLedger(
        provider="THE_ODDS_API", endpoint_key=PROBE_ENDPOINT_KEY,
        requested_at=now - timedelta(hours=1), status="RESERVED",
        request_fingerprint="f" * 64, source_mode="LIVE",
    ))
    session.flush()
    after = quota_state(session, floor=10, probe_after_hours=24,
                        probe_cooldown_hours=24, now=now)
    assert after.probe_allowed is False
    assert "cooldown" in after.probe_blocked_reason


def test_the_probe_does_not_lower_the_floor(session):
    now = datetime(2026, 9, 7, 20, tzinfo=UTC)
    # A recovered reading above the floor releases the latch by itself.
    _record_run(session, remaining=40, completed_at=now - timedelta(hours=56))
    state = quota_state(session, floor=10, probe_after_hours=24,
                        probe_cooldown_hours=24, now=now)
    assert state.latched is False
    assert state.probe_allowed is False
    assert state.floor == 10


def test_the_missing_market_reason_never_cites_api_reachability(session):
    now = datetime(2026, 9, 7, 20, tzinfo=UTC)
    _record_run(session, remaining=8, completed_at=now - timedelta(hours=56))
    state = quota_state(session, floor=10, probe_after_hours=24,
                        probe_cooldown_hours=24, now=now)
    reason = missing_market_reason(state, now - timedelta(hours=56))
    assert "8 requests remaining" in reason
    assert "floor of 10" in reason
    assert "api is connected" not in reason.lower()


# -- forecast visibility is independent of the market ---------------------


@pytest.mark.asyncio
async def test_a_model_forecast_is_visible_without_any_sportsbook_quote(
    session, settings,
):
    """The defect this guards: a quote-less game showed no probability at all.

    The decision-frozen ``model_probability`` may legitimately be absent, but
    the independently persisted current forecast must still reach the consumer,
    with its own timestamp and model identity.
    """

    from app.models import ForecastStatus, GameStatus, GameTeam, Team
    from app.services.read_service import ReadService

    await seed_demo_data(session, settings, SLATE)
    template = session.scalar(select(Forecast))
    seeded = session.get(Game, template.game_id)
    teams = session.scalars(select(Team).limit(2)).all()

    # A game that never received a sportsbook quote at all. Odds snapshots are
    # append-only, so the case is built rather than deleted into existence.
    quoteless = Game(
        league_id=seeded.league_id, season_id=seeded.season_id,
        venue_id=seeded.venue_id, starts_at=seeded.starts_at,
        status=GameStatus.SCHEDULED, source_mode=seeded.source_mode,
    )
    session.add(quoteless)
    session.flush()
    session.add_all([
        GameTeam(game_id=quoteless.id, team_id=teams[0].id, is_home=False),
        GameTeam(game_id=quoteless.id, team_id=teams[1].id, is_home=True),
    ])
    session.add(Forecast(
        game_id=quoteless.id, model_version_id=template.model_version_id,
        feature_version_id=template.feature_version_id, version=1,
        generated_at=template.generated_at, available_as_of=template.available_as_of,
        status=ForecastStatus.PUBLISHED, home_win_probability=0.611,
        away_win_probability=0.389, source_mode=seeded.source_mode,
    ))
    session.flush()

    card = ReadService(session, settings).game_card(quoteless)
    # The market is genuinely absent...
    assert card.market_available is False
    assert card.market_home_probability is None
    # ...and the model forecast is still there, with its own identity and time.
    assert card.forecast_available is True
    assert card.home_win_probability == pytest.approx(61.1, abs=1e-6)
    assert card.forecast_generated_at is not None
    assert card.forecast_model_label
