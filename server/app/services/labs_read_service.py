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
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import local_date_of
from app.models import (
    Forecast,
    Game,
    GameTeam,
    League,
    ShadowForecast,
    ShadowForecastBundle,
    Team,
)
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

    def _nfl_games(self, limit: int) -> dict[str, Any]:
        """NFL lab picker: every event whose imported forecast carries joint score evidence."""

        from datetime import UTC, datetime

        from app.services.nfl_read_service import NflReadService

        service = NflReadService(self.session, self.settings)
        weeks = service.weeks()
        current = weeks.get("current")
        games: list[dict[str, Any]] = []
        featured = None
        if current:
            overview = service.week_overview(current["season"], current["week"])
            featured = overview["events"][0]["kickoff_utc"][:10] if overview["events"] and overview["events"][0]["kickoff_utc"] else None
            for event in overview["events"][:limit]:
                games.append({
                    "game_id": event["canonical_game_id"],
                    "label": f"{event['away']['abbreviation']} @ {event['home']['abbreviation']}",
                    "home_team": event["home"]["abbreviation"],
                    "away_team": event["away"]["abbreviation"],
                    "starts_at": event["kickoff_utc"],
                    "slate_date": event["kickoff_utc"][:10] if event["kickoff_utc"] else None,
                    "event_status": event["state"],
                    "is_final": event["state"] == "FINAL",
                    "is_pregame": event["state"] == "UPCOMING",
                    "home_score": (event["final_score"] or {}).get("home"),
                    "away_score": (event["final_score"] or {}).get("away"),
                    "gen1_available": False, "gen1_href": None,
                    "v18_available": False, "v18_href": None, "v18_forecast_id": None,
                    "v18_path_events": 0, "v18_forecast_at": None, "v18_object_sha256": None,
                    "v18_views": {"broadcast": False, "geometry": False, "population": False},
                    "v18_skip_category": None, "v18_skip_reason": None,
                    "analysis_href": event["analysis_href"],
                    "lab_href": event["lab_href"] if event["lab_available"] else None,
                    "lab_label": "NFL Simulation Lab — Outcome Explorer" if event["lab_available"] else None,
                    "lab_engines": [e["engine_id"] for e in event["engines"] if e["simulation"]["joint_available"]],
                })
        return {
            "schema_version": "AISP_INTERACTIVE_LABS_V1",
            "league": "NFL",
            "generated_at": datetime.now(UTC).isoformat(),
            "eligible_count": len(games),
            "v18_count": 0, "v18_skip_count": 0,
            "featured_slate_date": featured,
            "featured_v18_count": 0, "featured_v18_skip_count": 0,
            "games": games,
            "note": ("Each NFL game opens the Outcome Explorer on its imported forecast's own "
                     "joint score distribution. Drive Theater and Scenario Comparison appear "
                     "per game only when producer evidence has been imported."),
        }

    def eligible_games(
        self,
        league: str = "MLB",
        *,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), MAX_LIMIT))
        if league.upper() == "NFL":
            return self._nfl_games(limit)

        league_game_ids = (
            select(GameTeam.game_id)
            .join(Team, Team.id == GameTeam.team_id)
            .join(League, League.id == Team.league_id)
            .where(League.code == league.upper())
            .distinct()
        )

        # Newest Gen 1 forecast per game. V18 and publisher-declared skips are
        # included independently below, so neither track hides the other.
        newest = (
            select(
                Forecast.game_id.label("game_id"),
                func.max(Forecast.generated_at).label("generated_at"),
            )
            .where(Forecast.game_id.in_(league_game_ids))
            .group_by(Forecast.game_id)
            .subquery()
        )
        rows = self.session.execute(
            select(Forecast, Game)
            .join(newest, (newest.c.game_id == Forecast.game_id)
                  & (newest.c.generated_at == Forecast.generated_at))
            .join(Game, Game.id == Forecast.game_id)
            .order_by(Game.starts_at.desc())
            .limit(MAX_LIMIT * 2)
        ).all()

        shadow_paths = self._shadow_paths()
        publisher_skips = self._publisher_skips()
        gen1 = {
            game.id: forecast
            for forecast, game in rows
            if _gen1_replayable(forecast)
        }
        candidate_ids = set(gen1) | set(shadow_paths) | set(publisher_skips)
        league_ids = set(self.session.scalars(league_game_ids).all())
        candidate_ids &= league_ids
        game_rows = self.session.scalars(
            select(Game).where(Game.id.in_(candidate_ids))
        ).all() if candidate_ids else []
        sides = self._sides(candidate_ids)

        games: list[dict[str, Any]] = []
        for game in game_rows:
            home, away = sides.get(game.id, (None, None))
            starts_at = _aware(game.starts_at)
            status = str(getattr(game.status, "value", game.status)).upper()
            shadow = shadow_paths.get(game.id)
            skip = publisher_skips.get(game.id) if shadow is None else None
            official = gen1.get(game.id)
            views = shadow["views"] if shadow else {
                "broadcast": False,
                "geometry": False,
                "population": False,
            }
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
                "gen1_available": official is not None,
                "gen1_href": (
                    f"/games/{game.id}/simulation?track=gen1" if official else None
                ),
                "v18_available": shadow is not None,
                "v18_href": (
                    f"/games/{game.id}/simulation?track=v18" if shadow else None
                ),
                "v18_forecast_id": shadow["forecast_id"] if shadow else None,
                "v18_path_events": shadow["path_events"] if shadow else 0,
                "v18_forecast_at": shadow["forecast_at"] if shadow else None,
                "v18_object_sha256": shadow["object_sha256"] if shadow else None,
                "v18_views": views,
                "v18_skip_category": skip.get("category") if skip else None,
                "v18_skip_reason": skip.get("reason") if skip else None,
                "analysis_href": f"/games/{game.id}",
            })

        # Newest slates first. Within a slate, V18-enabled games precede
        # Gen 1-only / skipped games, then games remain chronological.
        def ordering(item: dict[str, Any]) -> tuple[Any, ...]:
            slate = item.get("slate_date")
            ordinal = date.fromisoformat(slate).toordinal() if slate else 0
            return (-ordinal, not item["v18_available"], item.get("starts_at") or "")

        games.sort(key=ordering)
        games = games[:limit]
        featured_slate = games[0]["slate_date"] if games else None
        featured_games = [
            item for item in games if item["slate_date"] == featured_slate
        ]

        return {
            "schema_version": "AISP_INTERACTIVE_LABS_V1",
            "league": league.upper(),
            "generated_at": datetime.now(UTC).isoformat(),
            "eligible_count": len(games),
            "v18_count": sum(1 for item in games if item["v18_available"]),
            "v18_skip_count": sum(
                1 for item in games if item["v18_skip_reason"] is not None
            ),
            "featured_slate_date": featured_slate,
            "featured_v18_count": sum(
                1 for item in featured_games if item["v18_available"]
            ),
            "featured_v18_skip_count": sum(
                1 for item in featured_games if item["v18_skip_reason"] is not None
            ),
            "games": games,
            "note": (
                "A game is listed only when its own persisted simulation can be "
                "replayed. V18 forecasts are research-shadow evidence only; Gen 1 "
                "availability is shown independently for every game."
            ),
        }

    def _shadow_paths(self) -> dict[Any, dict[str, Any]]:
        """Registered V18 forecasts that actually carry a sequential path."""

        found: dict[Any, dict[str, Any]] = {}
        for row in self.session.scalars(
            select(ShadowForecast)
            .where(ShadowForecast.engine_id == ENGINE_ID)
            .order_by(ShadowForecast.forecast_cutoff_utc)
        ).all():
            if row.canonical_game_id is None:
                continue
            paths = (row.lineage_detail or {}).get("sequentialPath") or []
            events = len(paths[0]) if paths and isinstance(paths[0], list) else 0
            if events == 0:
                continue
            lineage = row.lineage_detail or {}
            geometry = lineage.get("playGeometry") or {}
            population = lineage.get("populationGeometry") or {}
            found[row.canonical_game_id] = {
                "forecast_id": row.forecast_id,
                "path_events": events,
                "forecast_at": (
                    _aware(row.forecast_cutoff_utc).isoformat()
                    if row.forecast_cutoff_utc else None
                ),
                "object_sha256": lineage.get("objectSha256")
                or lineage.get("bundleSha256"),
                "views": {
                    "broadcast": True,
                    "geometry": bool(geometry.get("events")),
                    "population": bool(population.get("summary")),
                },
            }
        return found

    def _publisher_skips(self) -> dict[Any, dict[str, Any]]:
        """Exact daily publisher skips retained on immutable bundle evidence."""

        found: dict[Any, dict[str, Any]] = {}
        bundles = self.session.scalars(
            select(ShadowForecastBundle)
            .where(ShadowForecastBundle.league == "MLB")
            .order_by(ShadowForecastBundle.generated_at)
        ).all()
        for bundle in bundles:
            evidence = bundle.evidence or {}
            if evidence.get("schemaVersion") != "AISP_MLB_V18_DAILY_SLATE_IMPORT_EVIDENCE_V1":
                continue
            for item in evidence.get("publisherSkips") or []:
                try:
                    game_id = uuid.UUID(str(item.get("gameId")))
                except (TypeError, ValueError):
                    continue
                found[game_id] = dict(item)
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
