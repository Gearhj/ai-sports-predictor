"""Choosing what the home page leads with, on criteria that can be read.

WHY THIS IS A SERVICE AND NOT A CLIENT HEURISTIC
------------------------------------------------
"Which game do we feature?" is a decision, and a decision that picks what a
visitor sees first deserves to be inspectable rather than buried in a component.
The rule is published in the payload as `selection_criteria`, so the page can
state it and a reader can check it.

THE RULE, AND WHAT IT DELIBERATELY EXCLUDES
-------------------------------------------
Ranked, in order:

  1. The game has saved simulation evidence to explore. A featured matchup with
     nothing to interact with is a headline, not an experience.
  2. It has not kicked off yet. A forecast is only interesting while it is still
     a forecast.
  3. Soonest kickoff first.

Nothing about the RESULT enters this. No margin, no win probability, no "our
model nailed this one." Selecting on outcome would turn the front door into a
highlight reel of the model's best days, which is the specific dishonesty this
ordering exists to prevent -- and it would do it silently, because a reader
cannot see the games that were not chosen.

When every game on the operational date has started, the slate is reported as
finished and the next dated slate is offered instead. The page never leads with
"nothing left to decide".
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.forecast_exchange.nfl_play_level_v1 import KIND_PLAY
from app.models import Game, GameTeam, League, ResearchSupplement, ShadowForecast, Team

SCHEMA_VERSION = "AISP_HOME_FEATURED_V1"

SELECTION_CRITERIA = {
    "ordering": [
        "Has saved simulation evidence that can be explored interactively.",
        "Has not kicked off yet.",
        "Soonest kickoff first.",
    ],
    "excluded": (
        "Nothing about a game's result, margin or model accuracy takes part in "
        "this choice. Featuring games by outcome would present the model's best "
        "days as if they were typical."
    ),
}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class HomeFeaturedService:
    """Picks the featured matchup and the compact selector beside it."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _play_evidence_events(self) -> dict[str, set[str]]:
        """Event id -> the scenario ids that published play-level evidence.

        Two columns, not the row: a supplement carries its whole artifact
        projection in `payload`, and this needs to know only that the row
        exists and which scenario it is.
        """
        rows = self.session.execute(
            select(
                ResearchSupplement.event_id,
                ResearchSupplement.evidence["scenario_id"].as_string(),
            ).where(
                ResearchSupplement.kind == KIND_PLAY,
                ResearchSupplement.sport == "NFL",
            )
        )
        found: dict[str, set[str]] = {}
        for event_id, scenario in rows:
            if event_id and scenario:
                found.setdefault(event_id, set()).add(scenario)
        return found

    def _forecasts(self, game_ids: set[Any]) -> dict[Any, Any]:
        """Canonical game id -> its newest shadow forecast.

        Newest by `available_as_of_utc`, which is when AISP could first have
        acted on it -- not by row creation, which would let a late import of an
        older forecast win.
        """
        if not game_ids:
            return {}
        # Columns, not entities: a shadow forecast carries `distributions`,
        # `uncertainty`, `readiness_detail`, `identity_evidence` and
        # `lineage_detail` as JSON, none of which the home page renders.
        rows = self.session.execute(
            select(
                ShadowForecast.canonical_game_id,
                ShadowForecast.event_id,
                ShadowForecast.engine_id,
                ShadowForecast.status,
                ShadowForecast.full_home_win_probability,
                ShadowForecast.full_away_win_probability,
                ShadowForecast.full_expected_home_score,
                ShadowForecast.full_expected_away_score,
                ShadowForecast.available_as_of_utc,
            )
            .where(ShadowForecast.canonical_game_id.in_(game_ids))
            .order_by(ShadowForecast.available_as_of_utc.desc().nullslast())
        )
        newest: dict[Any, Any] = {}
        for row in rows:
            if row.canonical_game_id and row.canonical_game_id not in newest:
                newest[row.canonical_game_id] = row
        return newest

    def featured(self, *, now: datetime | None = None, limit: int = 12) -> dict[str, Any]:
        now = _aware(now) or datetime.now(UTC)
        evidence = self._play_evidence_events()

        # Every scheduled game from now on, nearest first. The window is
        # deliberately forward-only: a completed game is not a forecast.
        upcoming = list(self.session.execute(
            select(Game, League.code)
            .join(League, League.id == Game.league_id)
            .where(Game.starts_at >= now)
            .order_by(Game.starts_at)
            .limit(160)
        ))
        game_ids = {game.id for game, _ in upcoming}
        forecast_by_game = self._forecasts(game_ids)

        sides: dict[Any, dict[bool, Team]] = {}
        if game_ids:
            for game_id, is_home, team in self.session.execute(
                select(GameTeam.game_id, GameTeam.is_home, Team)
                .join(Team, Team.id == GameTeam.team_id)
                .where(GameTeam.game_id.in_(game_ids))
            ):
                sides.setdefault(game_id, {})[bool(is_home)] = team

        candidates: list[dict[str, Any]] = []
        for game, league_code in upcoming:
            side = sides.get(game.id) or {}
            home, away = side.get(True), side.get(False)
            if home is None or away is None:
                continue
            forecast = forecast_by_game.get(game.id)
            event_id = forecast.event_id if forecast else None
            scenarios = sorted(evidence.get(event_id or "", set()))
            candidates.append({
                "game_id": str(game.id),
                "event_id": event_id,
                "league_code": league_code,
                "starts_at_utc": _aware(game.starts_at).isoformat(),
                "status": getattr(game.status, "value", str(game.status)),
                "home": {"abbreviation": home.abbreviation, "name": home.name, "city": home.city},
                "away": {"abbreviation": away.abbreviation, "name": away.name, "city": away.city},
                "matchup": f"{away.abbreviation} @ {home.abbreviation}",
                "simulation": {
                    "kind": "NFL_PLAY_POPULATION" if scenarios else None,
                    "scenarios": scenarios,
                    "explorable": bool(scenarios),
                },
                # The engine's own numbers, carried straight through. A league
                # with no execution authority still HAS a forecast; it simply
                # cannot fund a position, which is a different fact and is said
                # separately by `research_only`.
                "forecast": None if forecast is None else {
                    "engine_id": forecast.engine_id,
                    "status": forecast.status,
                    "home_win_probability": forecast.full_home_win_probability,
                    "away_win_probability": forecast.full_away_win_probability,
                    "expected_home_score": forecast.full_expected_home_score,
                    "expected_away_score": forecast.full_expected_away_score,
                    "expected_total": round(
                        forecast.full_expected_home_score + forecast.full_expected_away_score, 2,
                    ),
                    "expected_margin_home": round(
                        forecast.full_expected_home_score - forecast.full_expected_away_score, 2,
                    ),
                    "available_as_of_utc": (
                        _aware(forecast.available_as_of_utc).isoformat()
                        if forecast.available_as_of_utc else None
                    ),
                },
                "research_only": league_code == "NFL",
                "explore_href": f"/games/{game.id}/simulation?sport={league_code}",
            })

        # The ordering the docstring promises, applied exactly once and here.
        candidates.sort(key=lambda row: (
            0 if row["simulation"]["explorable"] else 1,
            row["starts_at_utc"],
        ))

        featured = candidates[0] if candidates else None
        slate_date: date | None = None
        if featured:
            slate_date = datetime.fromisoformat(featured["starts_at_utc"]).date()

        return {
            "schema_version": SCHEMA_VERSION,
            # Every read model in this API declares its provenance, and the
            # client's resource hook requires it.
            "data_mode": "live",
            "generated_at_utc": now.isoformat(),
            "selection_criteria": SELECTION_CRITERIA,
            "featured": featured,
            # The selector beside the hero: nearest kickoffs, evidence first.
            "candidates": candidates[:limit],
            "counts": {
                "upcoming_games": len(candidates),
                "with_simulation_evidence": sum(
                    1 for row in candidates if row["simulation"]["explorable"]
                ),
            },
            "next_slate_date": slate_date.isoformat() if slate_date else None,
        }
