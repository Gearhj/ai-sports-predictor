"""Read-only comparison of the official Gen 1 forecast and the V18 shadow.

The comparison is deliberately conservative:

* Gen 1 remains the only official forecast. The V18 row is labelled
  RESEARCH_SHADOW everywhere and carries no wagering authority.
* The two are PAIRED only when both satisfy the acceptance contract's timing
  rule -- each produced strictly before the scheduled start. Otherwise they are
  returned separately with the mismatch stated, never silently compared.
* The difference is reported as V18 minus Gen 1, the orientation the acceptance
  contract declares, with no verdict attached. A difference smaller than the
  larger Monte Carlo 95% half-width is flagged as simulation-indistinguishable
  rather than presented as an edge.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Forecast, ForecastTrack, Game, GameTeam, ModelVersion, ShadowForecast, Team
from app.shadow_import.mlb_v18 import ENGINE_ID


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _gen1(session: Session, game: Game) -> Forecast | None:
    """The newest valid current-track forecast, matching the desk's own rule."""

    return session.scalar(
        select(Forecast)
        .where(
            Forecast.game_id == game.id,
            Forecast.source_mode == game.source_mode,
            Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
        )
        .order_by(Forecast.version.desc())
        .limit(1)
    )


def _model_label(session: Session, forecast: Forecast | None) -> str | None:
    if forecast is None:
        return None
    version = session.get(ModelVersion, forecast.model_version_id)
    if version is None:
        return None
    return f"{version.name} {version.version}".strip()


def shadow_comparison(session: Session, game_id: uuid.UUID) -> dict[str, Any] | None:
    """Gen 1 and every registered V18 shadow forecast for one game."""

    game = session.get(Game, game_id)
    if game is None:
        return None

    rows = session.execute(
        select(GameTeam, Team).join(Team, Team.id == GameTeam.team_id)
        .where(GameTeam.game_id == game.id)
    ).all()
    home = next((team for link, team in rows if link.is_home), None)
    away = next((team for link, team in rows if not link.is_home), None)

    official = _gen1(session, game)
    shadows = session.scalars(
        select(ShadowForecast)
        .where(
            ShadowForecast.canonical_game_id == game.id,
            ShadowForecast.engine_id == ENGINE_ID,
        )
        .order_by(ShadowForecast.created_at)
    ).all()

    start = _aware(game.starts_at)
    official_at = _aware(official.generated_at) if official else None

    return {
        "schema_version": "AISP_MLB_V18_SHADOW_COMPARISON_V1",
        "game_id": str(game.id),
        "home_team": home.abbreviation if home else None,
        "away_team": away.abbreviation if away else None,
        "scheduled_start": start.isoformat() if start else None,
        "event_status": str(getattr(game.status, "value", game.status)).upper(),
        "official": (
            {
                "track": "GEN_1_OFFICIAL",
                "label": "Official AISP forecast",
                "model": _model_label(session, official),
                "home_win_probability": official.home_win_probability * 100,
                "generated_at": official_at.isoformat() if official_at else None,
                "status": official.status.value,
                "version": official.version,
                "wagering_authority": True,
                "pregame": bool(start and official_at and official_at < start),
            }
            if official
            else None
        ),
        "shadows": [_shadow_payload(row, official, start) for row in shadows],
        "notes": (
            "V18 is a research-shadow challenger. It has no wagering authority, "
            "does not change any official forecast or decision, and is not "
            "presented as better or worse than Gen 1."
        ),
    }


def _shadow_payload(
    row: ShadowForecast,
    official: Forecast | None,
    start: datetime | None,
) -> dict[str, Any]:
    uncertainty = dict(row.uncertainty or {})
    lineage = dict(row.lineage_detail or {})
    readiness = dict(row.readiness_detail or {})
    forecast_at = _aware(row.forecast_cutoff_utc)
    shadow_probability = row.full_home_win_probability * 100

    shadow_pregame = bool(start and forecast_at and forecast_at < start)
    official_at = _aware(official.generated_at) if official else None
    official_pregame = bool(start and official_at and official_at < start)
    pairable = bool(official and shadow_pregame and official_pregame)

    difference = None
    indistinguishable = None
    if pairable and official is not None:
        difference = round(
            shadow_probability - official.home_win_probability * 100, 4
        )
        half_width = float(uncertainty.get("monteCarlo95HalfWidth") or 0.0) * 100
        # Gen 1 publishes no Monte Carlo half-width of its own, so the shadow's
        # own sampling interval is the only bound available; say so rather than
        # implying a two-sided test.
        indistinguishable = abs(difference) < half_width if half_width else None

    return {
        "track": "RESEARCH_SHADOW",
        "engine_id": row.engine_id,
        "label": "V18 whole-game challenger (research shadow)",
        "forecast_id": row.forecast_id,
        "bundle_id": lineage.get("bundleId"),
        "bundle_sha256": lineage.get("bundleSha256"),
        "evaluation_contract": (lineage.get("evaluationContract") or {}).get("contractId"),
        "home_win_probability": shadow_probability,
        "expected_home_runs": row.full_expected_home_score,
        "expected_away_runs": row.full_expected_away_score,
        "generated_at": forecast_at.isoformat() if forecast_at else None,
        "evidence_cutoff": lineage.get("evidenceCutoff"),
        "pregame": shadow_pregame,
        "simulation_count": uncertainty.get("simulationCount"),
        "master_seed": row.simulator_seed,
        "monte_carlo_standard_error": uncertainty.get("monteCarloStandardError"),
        "monte_carlo_95_half_width": uncertainty.get("monteCarlo95HalfWidth"),
        "deterministic_replay_verified": uncertainty.get("deterministicReplayVerified"),
        "illegal_state_count": uncertainty.get("illegalStateCount"),
        "nontermination_count": uncertainty.get("nonterminationCount"),
        "path_digest": lineage.get("pathDigest"),
        "sequential_path_events": len(
            (lineage.get("sequentialPath") or [[]])[0]
        ) if lineage.get("sequentialPath") else 0,
        "lineup_modes": readiness.get("lineupModes"),
        "sides": readiness.get("sides"),
        "limitations": readiness.get("limitations"),
        "wagering_authority": False,
        "paired_with_official": pairable,
        "pairing_note": (
            None
            if pairable
            else (
                "No official forecast exists for this game."
                if official is None
                else "The two forecasts do not both satisfy the contract's "
                     "pregame timing rule, so they are shown separately."
            )
        ),
        "difference_vs_official": difference,
        "difference_orientation": "V18_MINUS_GEN1",
        "simulation_indistinguishable": indistinguishable,
    }


def sequential_path(session: Session, forecast_id: str) -> dict[str, Any] | None:
    """The persisted V18 plate-appearance path for one shadow forecast."""

    row = session.scalar(
        select(ShadowForecast).where(ShadowForecast.forecast_id == forecast_id)
    )
    if row is None or row.engine_id != ENGINE_ID:
        return None
    lineage = dict(row.lineage_detail or {})
    paths = lineage.get("sequentialPath") or []
    return {
        "schema_version": "AISP_MLB_V18_SEQUENTIAL_PATH_V1",
        "forecast_id": row.forecast_id,
        "game_id": row.event_id,
        "engine_id": row.engine_id,
        "bundle_sha256": lineage.get("bundleSha256"),
        "path_digest": lineage.get("pathDigest"),
        "master_seed": row.simulator_seed,
        "simulation_count": (row.uncertainty or {}).get("simulationCount"),
        "path_count": len(paths),
        # Genuine V18 output: plate appearances with bases, outs, innings,
        # running score and pitching changes. This is NOT a Gen 1 final-score
        # curve, and the two must never be shown as the same evidence.
        "paths": paths,
    }
