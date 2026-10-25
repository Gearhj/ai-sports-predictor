"""NHL canonical identity inside AISP: the league row, teams, games and their bindings.

The contract's identities are ``nhl:game:<NHL gameId>`` and ``nhl:team:<NHL team
id>``. AISP keeps its own UUID primary keys and binds the NHL identities through
``external_id_mappings`` (``NHL_STATS_GAME`` / ``NHL_STATS_TEAM``), the same
pattern NBA uses with ``NBA_ESPN``. Display names are never join keys.

``admit_game`` creates a game from a validated forecast's identity block. It is
how CFB and NBA research games were admitted (``RESEARCH_SHADOW`` provenance):
AISP has no admitted NHL schedule source, and polling one is out of scope.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ExternalIdMapping, Game, GameStatus, GameTeam, League, Season, Sport, Team
from app.models.nhl_research import NHL_GAME_SOURCE, NHL_LEAGUE_CODE, NHL_SPORT_CODE, NHL_TEAM_SOURCE
from app.nhl import contract as c

SOURCE_MODE_ADMITTED = "RESEARCH_SHADOW"


def ensure_league(session: Session) -> League:
    league = session.scalar(select(League).where(League.code == NHL_LEAGUE_CODE))
    if league is not None:
        return league
    sport = session.scalar(select(Sport).where(Sport.code == NHL_SPORT_CODE))
    if sport is None:
        sport = Sport(code=NHL_SPORT_CODE, name="Hockey", active=True)
        session.add(sport)
        session.flush()
    league = League(sport_id=sport.id, code=NHL_LEAGUE_CODE, name="National Hockey League", country_code="US",
                    active=True)
    session.add(league)
    session.flush()
    return league


def ensure_season(session: Session, league: League, label: str, start_year: int) -> Season:
    season = session.scalar(select(Season).where(Season.league_id == league.id, Season.name == label))
    if season is None:
        season = Season(league_id=league.id, name=label, starts_at=datetime(start_year, 9, 1, tzinfo=UTC),
                        ends_at=datetime(start_year + 1, 6, 30, tzinfo=UTC))
        session.add(season)
        session.flush()
    return season


def _mapping(session: Session, entity_type: str, source: str, external_id: str) -> uuid.UUID | None:
    return session.scalar(select(ExternalIdMapping.entity_id).where(
        ExternalIdMapping.entity_type == entity_type, ExternalIdMapping.source == source,
        ExternalIdMapping.external_id == external_id))


def ensure_team(session: Session, league: League, team: dict[str, Any]) -> uuid.UUID:
    source_id = str(team["canonicalTeamId"])  # namespaced: nhl:team:<id>
    existing = _mapping(session, "team", NHL_TEAM_SOURCE, source_id)
    if existing is not None:
        return existing
    # Bound by the NHL team id ONLY. A team row that shares the tri-code but has no
    # NHL_STATS_TEAM binding is not assumed to be this team; that is a refusal.
    clash = session.scalar(select(Team).where(Team.league_id == league.id, Team.abbreviation == team["triCode"]))
    if clash is not None:
        raise ValueError(f"TEAM_TRICODE_HELD_WITHOUT_NHL_BINDING:{team['triCode']}")
    name = str(team["name"])
    row = Team(league_id=league.id, name=name, abbreviation=str(team["triCode"]),
               city=name.rsplit(" ", 1)[0] if " " in name else None, active=True)
    session.add(row)
    session.flush()
    session.add(ExternalIdMapping(entity_type="team", entity_id=row.id, source=NHL_TEAM_SOURCE, external_id=source_id))
    session.flush()
    return row.id


def ensure_game(session: Session, game: dict[str, Any], *, source_mode: str = SOURCE_MODE_ADMITTED) -> uuid.UUID:
    """Return the AISP game for a contract ``game`` block, creating it (and its teams) if absent."""

    existing = _mapping(session, "game", NHL_GAME_SOURCE, str(game["canonicalGameId"]))
    if existing is not None:
        return existing
    league = ensure_league(session)
    season = ensure_season(session, league, str(game["season"]), int(game["seasonStartYear"]))
    home = ensure_team(session, league, game["home"])
    away = ensure_team(session, league, game["away"])
    row = Game(league_id=league.id, season_id=season.id, starts_at=c.parse_utc(game["scheduledStartUtc"]),
               status=GameStatus.SCHEDULED, source_mode=source_mode)
    session.add(row)
    session.flush()
    session.add_all([GameTeam(game_id=row.id, team_id=home, is_home=True),
                     GameTeam(game_id=row.id, team_id=away, is_home=False),
                     ExternalIdMapping(entity_type="game", entity_id=row.id, source=NHL_GAME_SOURCE,
                                       external_id=str(game["canonicalGameId"]))])
    session.flush()
    return row.id


__all__ = ["SOURCE_MODE_ADMITTED", "ensure_game", "ensure_league", "ensure_season", "ensure_team"]
