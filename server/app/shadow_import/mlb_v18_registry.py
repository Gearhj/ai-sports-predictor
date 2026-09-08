"""Idempotent registration of a verified MLB V18 bundle as a shadow track.

Storage note. This deliberately reuses the existing ``shadow_forecast_bundles``
and ``shadow_forecasts`` tables rather than adding new ones: they already model
an imported, checksum-addressed research-shadow forecast with its own engine
identity, simulator seed, distributions, uncertainty and free-form lineage. No
migration is required, and no official table is touched.

Isolation is by ``engine_id``. Nothing in AISP joins Gen 1 forecasts, official
positions or the $1,000 experiment to this table, so a V18 row cannot reach a
wagering path. The registration itself asserts that before it writes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Game,
    GameTeam,
    ShadowForecast,
    ShadowForecastBundle,
    Team,
)
from app.shadow_import.mlb_v18 import (
    CONTRACT_VERSION,
    ENGINE_ID,
    FORECAST_SCHEMA,
    SHADOW_STATUS,
    RegistrationOutcome,
    V18ImportError,
    VerifiedBundle,
    VerifiedContract,
    path_digest,
    prospective_rejection,
    sampling_uncertainty,
    _aware,
)

SIMULATOR_ID = "V18_WHOLE_GAME_MONTE_CARLO"


def forecast_identity(bundle_sha256: str, game_id: str) -> str:
    """Deterministic id, so re-running the import can never duplicate a row."""

    return f"mlb-v18-{bundle_sha256[:24]}-{game_id}"


def register_bundle(
    session: Session,
    bundle: VerifiedBundle,
    contract: VerifiedContract | None,
    *,
    now: datetime | None = None,
    game_ids: set[str] | None = None,
) -> RegistrationOutcome:
    """Register (or re-confirm) the bundle and its prospective forecasts.

    Idempotent: the bundle is keyed by its checksum and each forecast by a
    deterministic identity, so a second run reports rows as already present and
    writes nothing. Never mutates an existing row -- an immutable research
    record is re-confirmed, not rewritten.
    """

    now = now or datetime.now(UTC)
    outcome = RegistrationOutcome(bundle_sha256=bundle.bundle_sha256)

    row = session.scalar(
        select(ShadowForecastBundle).where(
            ShadowForecastBundle.bundle_sha256 == bundle.bundle_sha256
        )
    )
    registrable = [
        item for item in bundle.forecasts
        if game_ids is None or str(item.get("gameId")) in game_ids
    ]
    slate = _slate_date(bundle)

    if row is None:
        row = ShadowForecastBundle(
            bundle_sha256=bundle.bundle_sha256,
            contract_version=CONTRACT_VERSION,
            bundle_id=bundle.bundle_id,
            logical_uri=str(bundle.root.as_posix()),
            source_fixture_id=(
                contract.contract_id if contract else "NO_EVALUATION_CONTRACT"
            ),
            slate_date=slate,
            league="MLB",
            sport="MLB",
            status="RESEARCH_SHADOW",
            research_shadow_only=True,
            generated_at=_bundle_generated_at(bundle, now),
            forecast_count=len(bundle.forecasts),
            # This bundle carries no sportsbook quote and no priced candidate:
            # V18 is a forecasting challenger, not a wagering policy.
            quote_count=0,
            candidate_count=0,
            imported_at=now,
        )
        session.add(row)
        session.flush()
        outcome.bundle_row_created = True

    for forecast in registrable:
        game_id = str(forecast.get("gameId") or "")
        identity = forecast_identity(bundle.bundle_sha256, game_id)
        existing = session.scalar(
            select(ShadowForecast).where(ShadowForecast.forecast_id == identity)
        )
        if existing is not None:
            outcome.already_present.append(game_id)
            continue

        reason = prospective_rejection(forecast, now=now)
        if reason is not None:
            outcome.skipped.append((game_id, reason))
            continue

        game = _resolve_game(session, game_id)
        if game is None:
            outcome.skipped.append(
                (game_id, "no AISP game matches this bundle game identity")
            )
            continue

        session.add(
            _shadow_forecast_row(session, bundle, contract, forecast, game, row.id)
        )
        outcome.registered.append(game_id)

    session.flush()
    return outcome


def _slate_date(bundle: VerifiedBundle):
    starts = [
        _aware(item.get("scheduledStart"))
        for item in bundle.forecasts
        if _aware(item.get("scheduledStart")) is not None
    ]
    if not starts:
        raise V18ImportError("Bundle carries no scheduled start to date the slate")
    # Use the event's own local calendar day as persisted in the timestamp.
    return min(starts).date()


def _bundle_generated_at(bundle: VerifiedBundle, fallback: datetime) -> datetime:
    captured = [
        _aware(item.get("capturedAt"))
        for item in bundle.forecasts
        if _aware(item.get("capturedAt")) is not None
    ]
    return max(captured) if captured else fallback


def _resolve_game(session: Session, game_id: str) -> Game | None:
    try:
        key = uuid.UUID(game_id)
    except (TypeError, ValueError):
        return None
    return session.get(Game, key)


def _sides(session: Session, game_id: uuid.UUID) -> tuple[Team | None, Team | None]:
    rows = session.execute(
        select(GameTeam, Team).join(Team, Team.id == GameTeam.team_id)
        .where(GameTeam.game_id == game_id)
    ).all()
    home = next((team for link, team in rows if link.is_home), None)
    away = next((team for link, team in rows if not link.is_home), None)
    return home, away


def _shadow_forecast_row(
    session: Session,
    bundle: VerifiedBundle,
    contract: VerifiedContract | None,
    forecast: dict[str, Any],
    game: Game,
    bundle_row_id: uuid.UUID,
) -> ShadowForecast:
    home_probability = float(forecast["homeWinProbability"])
    simulation_count = int(forecast["simulationCount"])
    captured = _aware(forecast.get("capturedAt"))
    start = _aware(forecast.get("scheduledStart"))
    home, away = _sides(session, game.id)
    snapshot = bundle.pregame_snapshot
    sides = {
        str(item.get("side")): item for item in (snapshot.get("sides") or [])
    }

    # A declared operational gate, expressed as a 0/1 readiness so the stored
    # value means exactly one thing: every prospective precondition held. It is
    # not a synthesised confidence score.
    readiness = 1.0

    return ShadowForecast(
        bundle_id=bundle_row_id,
        canonical_game_id=game.id,
        forecast_id=forecast_identity(bundle.bundle_sha256, str(game.id)),
        forecast_schema_version=FORECAST_SCHEMA,
        snapshot_type="PROSPECTIVE",
        snapshot_version=1,
        engine_id=ENGINE_ID,
        engine_version=bundle.bundle_id,
        model_artifact_sha256=_pa_model_sha(bundle),
        model_manifest_sha256=bundle.bundle_sha256,
        feature_vector_sha256=(
            bundle.feature_contract.get("manifestSha256") or None
        ),
        simulator_id=SIMULATOR_ID,
        simulator_version=str(forecast.get("schemaVersion") or FORECAST_SCHEMA),
        simulator_seed=int(forecast.get("masterSeed") or 0),
        event_id=str(game.id),
        source_event_id=str(
            (snapshot.get("game") or {}).get("source_game_id") or game.id
        ),
        event_date=(start or captured).date(),
        season=int(bundle.handoff.get("season") or (start or captured).year),
        season_type="REGULAR",
        home_team_id=str(home.id) if home else "",
        away_team_id=str(away.id) if away else "",
        is_ready=True,
        readiness_score=readiness,
        readiness_threshold=1.0,
        status=SHADOW_STATUS,
        scheduled_start_utc=start,
        schedule_as_of_utc=captured,
        source_cutoff_utc=captured,
        forecast_cutoff_utc=captured,
        available_as_of_utc=captured,
        full_home_win_probability=home_probability,
        full_away_win_probability=round(1.0 - home_probability, 10),
        full_expected_home_score=float(forecast.get("meanHomeRuns") or 0.0),
        full_expected_away_score=float(forecast.get("meanAwayRuns") or 0.0),
        # V18 is a whole-game challenger; it publishes no first-five market.
        f5_home_win_probability=None,
        f5_away_win_probability=None,
        f5_expected_home_score=None,
        f5_expected_away_score=None,
        distributions={
            "homeRunDistribution": forecast.get("homeRunDistribution"),
            "awayRunDistribution": forecast.get("awayRunDistribution"),
            "totalRunDistribution": forecast.get("totalRunDistribution"),
            "runDifferentialDistribution": forecast.get("runDifferentialDistribution"),
            "meanTotalRuns": forecast.get("meanTotalRuns"),
            "meanRunDifferential": forecast.get("meanRunDifferential"),
            "oneRunProbability": forecast.get("oneRunRate"),
            "extraInningProbability": forecast.get("extraInningRate"),
            "pitchingChangeRateHome": forecast.get("pitchingChangeRateHome"),
            "pitchingChangeRateAway": forecast.get("pitchingChangeRateAway"),
        },
        uncertainty={
            **sampling_uncertainty(home_probability, simulation_count),
            "seedPolicy": {
                "masterSeed": forecast.get("masterSeed"),
                "policy": bundle.simulation_policy.get("policy"),
            },
            "convergence": forecast.get("convergence"),
            "deterministicReplayVerified": forecast.get(
                "deterministicReplayVerified"
            ),
            "illegalStateCount": forecast.get("illegalStateCount"),
            "nonterminationCount": forecast.get("nonterminationCount"),
        },
        readiness_detail={
            "gate": "PROSPECTIVE_OPERATIONAL_PRECONDITIONS_ALL_MET",
            "capturedBeforeScheduledStart": True,
            "lineupModes": forecast.get("lineupModes"),
            "sides": {
                side: {
                    "team": (item.get("team") or {}).get("abbreviation"),
                    "lineupStatus": (item.get("lineupAuthority") or {}).get("status"),
                    "lineupObservedAt": (item.get("lineupAuthority") or {}).get(
                        "observedAt"
                    ),
                    "lineupSource": (item.get("lineupAuthority") or {}).get("source"),
                    "starter": (item.get("starter") or {}).get("name"),
                    "starterState": (item.get("starter") or {}).get(
                        "confirmationState"
                    ),
                    "starterObservedAt": (item.get("starter") or {}).get("observedAt"),
                }
                for side, item in sides.items()
            },
            "limitations": bundle.limitations,
            "authorityInventory": bundle.authority_inventory,
        },
        identity_evidence={
            "gameId": str(game.id),
            "sourceGameId": (snapshot.get("game") or {}).get("source_game_id"),
            "venue": (snapshot.get("game") or {}).get("venue"),
            "homeTeam": home.abbreviation if home else None,
            "awayTeam": away.abbreviation if away else None,
            "identityCrosswalk": bundle.manifest.get("identityCrosswalk"),
            "pregameSnapshotSha256": snapshot.get("contentSha256"),
        },
        lineage_detail={
            "bundleId": bundle.bundle_id,
            "bundleSha256": bundle.bundle_sha256,
            "artifactsVerified": bundle.artifact_count,
            "participationPolicy": {
                "executionAllowed": bundle.handoff.get("executionAllowed"),
                "wageringAuthority": bundle.handoff.get("wageringAuthority"),
                "promotionStatus": bundle.handoff.get("promotionStatus"),
                "marketDataRequired": bundle.handoff.get("marketDataRequired"),
                "status": bundle.handoff.get("status"),
            },
            "evaluationContract": (
                {
                    "contractId": contract.contract_id,
                    "contractSha256": contract.contract_sha256,
                    "schemaVersion": contract.contract.get("schemaVersion"),
                }
                if contract
                else None
            ),
            "evidenceCutoff": captured.isoformat() if captured else None,
            "inputReceipts": {
                "pregameSnapshotCapturedAt": snapshot.get("capturedAt"),
                "captureMode": snapshot.get("captureMode"),
                "marketDataRead": snapshot.get("marketDataRead"),
                "outcomesRead": snapshot.get("outcomesRead"),
                "aispRowsModified": snapshot.get("aispRowsModified"),
                "weather": snapshot.get("weather"),
            },
            "featureContract": bundle.feature_contract,
            "paFold": forecast.get("paFold"),
            "transitionFold": forecast.get("transitionFold"),
            "calibrationDecision": bundle.manifest.get("calibrationDecision"),
            "pathDigest": path_digest(forecast.get("traces")),
            # The genuine sequential path: plate appearances with bases, outs,
            # innings, running score and pitching changes. Persisted verbatim.
            "sequentialPath": forecast.get("traces"),
            "gen1HistoryMutated": False,
            "wageringAuthority": False,
        },
    )


def _pa_model_sha(bundle: VerifiedBundle) -> str:
    """SHA-256 of the exact PA model artifact this bundle scored with."""

    target = str(bundle.handoff.get("paModelFile") or "")
    for entry in bundle.manifest.get("artifacts") or []:
        if entry.get("relativePath") == target:
            return str(entry["sha256"])
    raise V18ImportError(f"Bundle does not declare its PA model artifact: {target!r}")
