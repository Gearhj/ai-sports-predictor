"""Admit a College Football research forecast's game into AISP's canonical layer.

WHERE THE IDENTITY COMES FROM
-----------------------------
Only from a byte-verified research forecast already imported by
``app.cfb.research_import``: its CFBD game id, both CFBD team ids, both team
names and its captured kickoff. No provider is called to "discover" the game,
and nothing is matched by team name or date. The join is recorded as external
id mappings (source ``CFBD``), the same pattern the NBA importer uses, so a
second admission finds the same game instead of creating another.

WHY THE GAME IS STORED AS RESEARCH_SHADOW
-----------------------------------------
The MLB jobs (forecast generation, the shadow experiment, the pregame evidence
monitor, the MLB slate reads) select ``source_mode == 'LIVE'`` games without a
league filter. NFL games stay out of them because they are RESEARCH_SHADOW rows;
a College Football game is admitted the same way, so it cannot be forecast by
the MLB pipeline, evaluated by an MLB experiment or listed on an MLB slate.

WHAT ADMISSION DOES NOT DO
--------------------------
It creates no market, quote, decision, position, eligibility record or
scheduler entry, and it grants the forecast nothing: paper eligibility stays
bound to the exact forecast id a producer supplement names.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CfbResearchForecast

#: The identity space the research package speaks.
SOURCE_CFBD = "CFBD"
LEAGUE_CODE = "CFB"
#: See the module docstring: keeps the game out of the MLB LIVE paths.
SOURCE_MODE = "RESEARCH_SHADOW"


def _utc(value: datetime) -> datetime:
    """Stored timestamps are UTC; some drivers return them naive."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class CfbBindingError(RuntimeError):
    """Admission refused, with the reason named."""


def canonical_game_id(session: Session, source_game_id: str) -> uuid.UUID | None:
    """The admitted AISP game for a CFBD game id, or None."""

    from app.models import ExternalIdMapping

    mapping = session.scalar(select(ExternalIdMapping).where(
        ExternalIdMapping.entity_type == "game",
        ExternalIdMapping.source == SOURCE_CFBD,
        ExternalIdMapping.external_id == str(source_game_id),
    ))
    return mapping.entity_id if mapping is not None else None


def admit_game(session: Session, forecast: CfbResearchForecast) -> dict[str, Any]:
    """Resolve -- or register -- the canonical game for an imported forecast."""

    from app.models import ExternalIdMapping, Game, GameTeam, League, Season, Sport, Team

    if not forecast.research_only:
        raise CfbBindingError("only research-only forecasts are admitted here")
    source_id = str(forecast.source_game_id or "").strip()
    if not source_id.isdigit():
        raise CfbBindingError(f"forecast {forecast.forecast_id} carries no numeric CFBD game id")
    if forecast.captured_kickoff_utc is None:
        raise CfbBindingError(f"forecast {forecast.forecast_id} carries no captured kickoff")
    for side in ("home", "away"):
        if not str(getattr(forecast, f"{side}_source_team_id") or "").strip().isdigit():
            raise CfbBindingError(f"forecast {forecast.forecast_id} has no CFBD {side} team id")
    if forecast.home_source_team_id == forecast.away_source_team_id:
        raise CfbBindingError("home and away CFBD team ids are identical")

    existing = canonical_game_id(session, source_id)
    if existing is not None:
        _check_existing(session, existing, forecast)
        return {"result": "ALREADY_ADMITTED", "game_id": str(existing), "source_game_id": source_id}

    # American football already exists as a sport row (stored as 'NFL', see
    # app.sports.registry); College Football is a separate league under it.
    sport = session.scalar(select(Sport).where(Sport.code == "NFL"))
    if sport is None:
        sport = Sport(name="American Football", code="NFL")
        session.add(sport)
        session.flush()
    league = session.scalar(select(League).where(League.code == LEAGUE_CODE))
    if league is None:
        league = League(sport_id=sport.id, code=LEAGUE_CODE, name="College Football")
        session.add(league)
        session.flush()

    kickoff = _utc(forecast.captured_kickoff_utc)
    season_year = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
    season = session.scalar(select(Season).where(
        Season.league_id == league.id, Season.name == str(season_year)))
    if season is None:
        season = Season(league_id=league.id, name=str(season_year),
                        starts_at=datetime(season_year, 8, 1, tzinfo=UTC),
                        ends_at=datetime(season_year + 1, 1, 31, tzinfo=UTC))
        session.add(season)
        session.flush()

    def team(source_team_id: str, name: str) -> Team:
        mapping = session.scalar(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "team",
            ExternalIdMapping.source == SOURCE_CFBD,
            ExternalIdMapping.external_id == source_team_id,
        ))
        if mapping is not None:
            return session.get(Team, mapping.entity_id)
        # The package carries no team abbreviation, and one is not invented:
        # the abbreviation column holds the CFBD id, which is unique within the
        # league and cannot collide with an MLB or NFL club code.
        row = Team(league_id=league.id, name=name, abbreviation=f"CFBD{source_team_id}"[:12])
        session.add(row)
        session.flush()
        session.add(ExternalIdMapping(entity_type="team", entity_id=row.id,
                                      source=SOURCE_CFBD, external_id=source_team_id))
        return row

    home = team(str(forecast.home_source_team_id), forecast.home_team_name)
    away = team(str(forecast.away_source_team_id), forecast.away_team_name)

    game = Game(league_id=league.id, season_id=season.id, starts_at=kickoff,
                status="SCHEDULED", source_mode=SOURCE_MODE)
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
        ExternalIdMapping(entity_type="game", entity_id=game.id,
                          source=SOURCE_CFBD, external_id=source_id),
    ])
    session.flush()
    return {"result": "ADMITTED", "game_id": str(game.id), "source_game_id": source_id,
            "league": LEAGUE_CODE, "source_mode": SOURCE_MODE,
            "kickoff_utc": kickoff.isoformat(),
            "home": {"name": home.name, "cfbd_team_id": str(forecast.home_source_team_id)},
            "away": {"name": away.name, "cfbd_team_id": str(forecast.away_source_team_id)}}


def _check_existing(session: Session, game_id: uuid.UUID, forecast: CfbResearchForecast) -> None:
    """A second forecast for an admitted game must agree on teams and kickoff."""

    from app.models import ExternalIdMapping, Game, GameTeam

    game = session.get(Game, game_id)
    sides = {row.is_home: row.team_id for row in session.scalars(
        select(GameTeam).where(GameTeam.game_id == game_id))}
    for is_home, source_team in ((True, forecast.home_source_team_id),
                                 (False, forecast.away_source_team_id)):
        mapping = session.scalar(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == SOURCE_CFBD,
            ExternalIdMapping.external_id == str(source_team)))
        if mapping is None or sides.get(is_home) != mapping.entity_id:
            raise CfbBindingError(
                f"admitted game {game_id} disagrees with {forecast.forecast_id} on the "
                f"{'home' if is_home else 'away'} team")
    kickoff = _utc(forecast.captured_kickoff_utc)
    if _utc(game.starts_at) != kickoff:
        raise CfbBindingError(
            f"admitted game {game_id} kicks off {game.starts_at.isoformat()}, "
            f"forecast captured {kickoff.isoformat()}")


__all__ = ["CfbBindingError", "LEAGUE_CODE", "SOURCE_CFBD", "SOURCE_MODE",
           "admit_game", "canonical_game_id"]
