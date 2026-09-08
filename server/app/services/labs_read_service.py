"""Which games an interactive lab can actually open, and with which tracks.

Eligibility is derived from persisted evidence, never from a hardcoded game:

* GEN 1 is offerable when the newest forecast for the game persists a
  simulation summary with its seed and draw count and the expected-run inputs
  the replay needs. That is exactly the contract
  ``replay_official_simulation`` enforces, so a listed game always opens.
* V18 is offerable only when a registered research-shadow forecast for that
  game persists a non-empty sequential path. Most games have none, and the
  read model says so per game rather than implying every game has a replay.

The list spans past slates as well as today, because a finished game keeps its
preserved simulation. Each entry carries its own date and event status so a
historical replay is never presented as a live one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import local_date_of
from app.models import Forecast, Game, GameTeam, ShadowForecast, Team
from app.shadow_import.mlb_v18 import ENGINE_ID

#: Cap on how far back the picker reaches. Enough to cover recent slates
#: without turning the page into an unbounded archive listing.
DEFAULT_LIMIT = 60
MAX_LIMIT = 250


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _gen1_replayable(forecast: Forecast | None) -> bool:
    """The exact preconditions the Gen 1 replay itself requires."""

    if forecast is None:
        return False
    summary = forecast.simulation_summary or {}
    if "seed" not in summary or "simulations" not in summary:
        return False
    return bool(forecast.expected_home_runs) and bool(forecast.expected_away_runs)


class LabsReadService:
    """Read-only listing of lab-eligible events for one league."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def eligible_games(
        self,
        league: str = "MLB",
        *,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), MAX_LIMIT))

        # Newest forecast per game, so eligibility matches what the simulator
        # will actually load rather than any older version.
        newest = (
            select(
                Forecast.game_id.label("game_id"),
                func.max(Forecast.generated_at).label("generated_at"),
            )
            .group_by(Forecast.game_id)
            .subquery()
        )
        rows = self.session.execute(
            select(Forecast, Game)
            .join(newest, (newest.c.game_id == Forecast.game_id)
                  & (newest.c.generated_at == Forecast.generated_at))
            .join(Game, Game.id == Forecast.game_id)
            .order_by(Game.starts_at.desc())
            .limit(limit * 2)
        ).all()

        shadow_paths = self._shadow_paths()
        sides = self._sides({game.id for _, game in rows})

        games: list[dict[str, Any]] = []
        for forecast, game in rows:
            if len(games) >= limit:
                break
            if not _gen1_replayable(forecast):
                continue
            home, away = sides.get(game.id, (None, None))
            starts_at = _aware(game.starts_at)
            status = str(getattr(game.status, "value", game.status)).upper()
            shadow = shadow_paths.get(game.id)
            games.append({
                "game_id": str(game.id),
                "label": f"{away or '—'} @ {home or '—'}",
                "home_team": home,
                "away_team": away,
                "starts_at": starts_at.isoformat() if starts_at else None,
                "slate_date": (
                    local_date_of(starts_at, self.settings.display_timezone).isoformat()
                    if starts_at else None
                ),
                "event_status": status,
                # A finished game is still explorable; it is simply labelled as
                # finished so a preserved replay is never read as a live one.
                "is_final": status in {"FINAL", "COMPLETED", "CLOSED"},
                "is_pregame": status == "SCHEDULED",
                "home_score": game.home_score,
                "away_score": game.away_score,
                "gen1_available": True,
                "gen1_href": f"/games/{game.id}/simulation?track=gen1",
                "v18_available": shadow is not None,
                "v18_href": (
                    f"/games/{game.id}/simulation?track=v18" if shadow else None
                ),
                "v18_forecast_id": shadow["forecast_id"] if shadow else None,
                "v18_path_events": shadow["path_events"] if shadow else 0,
                "analysis_href": f"/games/{game.id}",
            })

        return {
            "schema_version": "AISP_INTERACTIVE_LABS_V1",
            "league": league.upper(),
            "generated_at": datetime.now(UTC).isoformat(),
            "eligible_count": len(games),
            "v18_count": sum(1 for item in games if item["v18_available"]),
            "games": games,
            "note": (
                "A game is listed only when its own persisted simulation can be "
                "replayed. A V18 action appears only where a research-shadow path "
                "is registered for that specific game."
            ),
        }

    def _shadow_paths(self) -> dict[Any, dict[str, Any]]:
        """Registered V18 forecasts that actually carry a sequential path."""

        found: dict[Any, dict[str, Any]] = {}
        for row in self.session.scalars(
            select(ShadowForecast).where(ShadowForecast.engine_id == ENGINE_ID)
        ).all():
            if row.canonical_game_id is None:
                continue
            paths = (row.lineage_detail or {}).get("sequentialPath") or []
            events = len(paths[0]) if paths and isinstance(paths[0], list) else 0
            if events == 0:
                continue
            found[row.canonical_game_id] = {
                "forecast_id": row.forecast_id,
                "path_events": events,
            }
        return found

    def _sides(self, game_ids: set[Any]) -> dict[Any, tuple[str | None, str | None]]:
        if not game_ids:
            return {}
        rows = self.session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id.in_(game_ids))
        ).all()
        sides: dict[Any, list[str | None]] = {}
        for game_id, is_home, abbreviation in rows:
            entry = sides.setdefault(game_id, [None, None])
            entry[0 if is_home else 1] = abbreviation
        return {key: (value[0], value[1]) for key, value in sides.items()}


__all__ = ["LabsReadService", "DEFAULT_LIMIT", "MAX_LIMIT"]
