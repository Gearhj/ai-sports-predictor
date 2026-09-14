"""Reading imported NBA research forecasts for display.

Two outputs, kept under separate names all the way to the screen. Probabilities
leave here as the delivered fractions; the view multiplies by 100 exactly once,
so a percentage cannot be computed twice on its way to a reader.

Everything the package left unavailable leaves here unavailable. drawCount is
zero, the distributions are null, and no Over/Under probability or market fair
price is derived from the expected total -- a total of 225.72 points is not a
distribution.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

SCHEMA_VERSION = "AISP_NBA_RESEARCH_FORECASTS_V1"


def nba_research_board(
    session: Session, *, game_id: str | None = None,
) -> dict[str, Any]:
    """Every imported NBA research forecast, newest capture first."""

    from app.models import Game, GameTeam, NbaResearchForecast, Team

    query = select(NbaResearchForecast).order_by(
        NbaResearchForecast.captured_tipoff_utc)
    if game_id:
        query = query.where(NbaResearchForecast.canonical_game_id == game_id)
    rows = session.scalars(query).all()

    out: list[dict[str, Any]] = []
    for row in rows:
        sides = {
            ("home" if is_home else "away"): {"abbreviation": abbreviation}
            for is_home, abbreviation in session.execute(
                select(GameTeam.is_home, Team.abbreviation)
                .join(Team, Team.id == GameTeam.team_id)
                .where(GameTeam.game_id == row.canonical_game_id)
            ).all()
        }
        game = session.get(Game, row.canonical_game_id)
        current_start = None
        if game is not None and game.starts_at is not None:
            aware = (game.starts_at if game.starts_at.tzinfo
                     else game.starts_at.replace(tzinfo=UTC))
            current_start = aware.isoformat()

        captured = row.captured_tipoff_utc
        captured_iso = (captured if captured.tzinfo
                        else captured.replace(tzinfo=UTC)).isoformat()

        out.append({
            "forecast_id": row.forecast_id,
            "forecast_sha256": row.forecast_sha256,
            "engine_id": row.engine_id,
            "configuration_identity": row.configuration_identity,
            "game_id": str(row.canonical_game_id),
            "source_game_id": row.source_game_id,
            "teams": sides,
            # The forecast's own captured schedule and AISP's current one, both
            # kept. A later authoritative revision never rewrites the snapshot
            # the forecast was computed against.
            "captured_schedule": {
                "tipoff_utc": captured_iso,
                "game_date": row.captured_game_date.isoformat(),
                "source_revision": row.schedule_source_revision,
            },
            "current_schedule": {
                "tipoff_utc": current_start,
                "differs_from_captured": (
                    current_start is not None and current_start != captured_iso),
            },
            "ridge_expected_points": {
                "away": row.ridge_expected_points_away,
                "home": row.ridge_expected_points_home,
                "total": row.ridge_expected_total,
                "home_margin": row.ridge_home_margin,
                "model": "Ridge alpha100",
                "units": "points including overtime",
            },
            "elo_win_probability": {
                # Fractions. Multiplied by 100 once, at display.
                "away": row.elo_win_probability_away,
                "home": row.elo_win_probability_home,
                "tie": row.elo_tie_probability,
                "model": "Elo — separate model output",
                "method": row.elo_method,
                "units": "fractions 0 to 1",
            },
            "distributions": {
                "draw_count": row.draw_count,
                "status": row.distribution_status,
                "note": (
                    "No simulated draws were retained, so run distributions, "
                    "prediction intervals, Monte Carlo error, Over/Under "
                    "probabilities and market fair prices are unavailable. None "
                    "is derived from the expected total."
                ),
            },
            "clocks": {
                "source_cutoff_utc": _iso(row.source_cutoff_utc),
                "forecast_created_utc": _iso(row.forecast_created_utc),
                "local_export_utc": _iso(row.local_export_utc),
                "producer_published_utc": _iso(row.producer_published_utc),
                "exchange_delivered_utc": _iso(row.exchange_delivered_utc),
            },
            "label": (row.detail or {}).get("display", {}).get("label"),
            "limitations": (row.detail or {}).get("limitations"),
            "rest_support": (row.detail or {}).get("restSupportAudit"),
            "bindings": (row.detail or {}).get("bindings"),
            "authority": (row.detail or {}).get("authority"),
            "superseded_by": row.superseded_by_forecast_id,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "forecasts": out,
        "note": (
            "NBA is research-only and unfunded. These forecasts create no "
            "account, wager, bankroll, staking policy or scheduler entry."
        ),
    }


def _iso(value):
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


__all__ = ["SCHEMA_VERSION", "nba_research_board"]
