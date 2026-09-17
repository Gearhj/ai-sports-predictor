"""Fail-closed identity for provider player-prop outcomes.

1. The provider event binds to exactly one canonical MLB game: the stored
   THE_ODDS_API event mapping first, else the league-scoped matcher. A mapped
   game whose teams disagree with the event is a conflict, not a match.
2. The provider's display name binds to exactly one player on that game's two
   teams. A player's team is taken from governed evidence, strongest first:
   this game's lineup, this game's starter observation, the game's probable
   pitcher, then the latest season roster row available before the reference
   time. A name is never turned into a new player.

Anything else returns a quarantine reason.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.player_props import contract as c


def normalize_person(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[.'’`]", "", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    words = [w for w in text.split() if w not in {"jr", "sr", "ii", "iii", "iv"}]
    return " ".join(words)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class GameBinding:
    game_id: uuid.UUID | None
    reason: str | None
    mlb_game_ref: str | None = None
    team_ids: tuple[uuid.UUID, ...] = ()
    season_id: uuid.UUID | None = None
    starts_at: datetime | None = None


@dataclass(frozen=True)
class PlayerBinding:
    player_id: uuid.UUID | None
    team_id: uuid.UUID | None
    reason: str | None
    evidence: str | None = None
    candidates: int = 0


def resolve_game(session: Session, *, event_id: str, home_team: str, away_team: str,
                 commence_time: datetime) -> GameBinding:
    from app.ingestion.normalizer import CanonicalNormalizer
    from app.markets.odds import game_match
    from app.models import ExternalIdMapping, Game, GameTeam, League, Team
    from app.services.league_scope import in_league

    game = None
    mapped = session.scalar(select(ExternalIdMapping.entity_id).where(
        ExternalIdMapping.source == c.PROVIDER, ExternalIdMapping.entity_type == "game",
        ExternalIdMapping.external_id == str(event_id)))
    if mapped is not None:
        game = session.get(Game, mapped)
        if game is None:
            return GameBinding(None, c.GAME_UNRESOLVED)
        league = session.scalar(select(League.code).where(League.id == game.league_id))
        if league != "MLB":
            return GameBinding(None, c.GAME_NOT_MLB)
    else:
        start = _aware(commence_time)
        pool = list(session.scalars(select(Game).where(
            in_league("MLB"),
            Game.starts_at >= start - timedelta(hours=12), Game.starts_at <= start + timedelta(hours=12),
        )))
        matched, _why = CanonicalNormalizer(session)._match_game(pool, home_team, away_team, start)
        if matched is None:
            return GameBinding(None, c.GAME_UNRESOLVED)
        game = matched

    sides = {is_home: (team_id, name) for team_id, is_home, name in session.execute(
        select(GameTeam.team_id, GameTeam.is_home, Team.name)
        .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id == game.id))}
    if True not in sides or False not in sides:
        return GameBinding(None, c.GAME_UNRESOLVED)
    if not game_match(sides[True][1], sides[False][1], home_team, away_team):
        return GameBinding(None, c.PARTICIPANT_CONFLICT)
    mlb_ref = session.scalar(select(ExternalIdMapping.external_id).where(
        ExternalIdMapping.source == "MLB_STATS_API", ExternalIdMapping.entity_type == "game",
        ExternalIdMapping.entity_id == game.id))
    return GameBinding(game.id, None, mlb_ref, (sides[False][0], sides[True][0]), game.season_id,
                       _aware(game.starts_at))


def _team_evidence(session: Session, player_id: uuid.UUID, game: GameBinding,
                   reference: datetime) -> tuple[uuid.UUID | None, str | None]:
    from app.models import GameTeam, LineupEntry, LineupObservation, Roster, StarterObservation

    lineup_team = session.scalar(
        select(LineupObservation.team_id)
        .join(LineupEntry, LineupEntry.lineup_observation_id == LineupObservation.id)
        .where(LineupObservation.game_id == game.game_id, LineupEntry.player_id == player_id,
               LineupObservation.available_as_of <= reference)
        .order_by(LineupObservation.available_as_of.desc()).limit(1))
    if lineup_team is not None:
        return lineup_team, "LINEUP"
    starter_team = session.scalar(
        select(StarterObservation.team_id)
        .where(StarterObservation.game_id == game.game_id,
               (StarterObservation.probable_pitcher_id == player_id)
               | (StarterObservation.confirmed_pitcher_id == player_id),
               StarterObservation.available_as_of <= reference)
        .order_by(StarterObservation.available_as_of.desc()).limit(1))
    if starter_team is not None:
        return starter_team, "STARTER"
    probable_team = session.scalar(select(GameTeam.team_id).where(
        GameTeam.game_id == game.game_id, GameTeam.probable_pitcher_id == player_id))
    if probable_team is not None:
        return probable_team, "PROBABLE_PITCHER"
    roster_query = select(Roster.team_id).where(Roster.player_id == player_id, Roster.available_as_of <= reference)
    if game.season_id is not None:
        roster_query = roster_query.where(Roster.season_id == game.season_id)
    roster_team = session.scalar(roster_query.order_by(Roster.available_as_of.desc()).limit(1))
    if roster_team is not None:
        return roster_team, "ROSTER"
    return None, None


def name_index(session: Session) -> dict[str, list[uuid.UUID]]:
    """Normalized full name -> every player carrying it (accent- and suffix-insensitive)."""
    from app.models import Player

    index: dict[str, list[uuid.UUID]] = {}
    for pid, name in session.execute(select(Player.id, Player.full_name)):
        index.setdefault(normalize_person(name), []).append(pid)
    return index


def resolve_player(session: Session, game: GameBinding, provider_name: str,
                   *, reference: datetime | None = None,
                   names: dict[str, list[uuid.UUID]] | None = None) -> PlayerBinding:
    if game.game_id is None:
        return PlayerBinding(None, None, game.reason or c.GAME_UNRESOLVED)
    wanted = normalize_person(provider_name)
    if not wanted:
        return PlayerBinding(None, None, c.PLAYER_UNKNOWN)
    candidates = list((names if names is not None else name_index(session)).get(wanted, []))
    if not candidates:
        return PlayerBinding(None, None, c.PLAYER_UNKNOWN)
    ref = reference or datetime.now(UTC)
    if game.starts_at is not None:
        ref = min(ref, game.starts_at)
    on_game: list[tuple[uuid.UUID, uuid.UUID, str]] = []
    off_game = 0
    for pid in candidates:
        team, evidence = _team_evidence(session, pid, game, ref)
        if team is None:
            continue
        if team in game.team_ids:
            on_game.append((pid, team, evidence or ""))
        else:
            off_game += 1
    if len(on_game) == 1:
        pid, team, evidence = on_game[0]
        return PlayerBinding(pid, team, None, evidence, len(candidates))
    if len(on_game) > 1:
        return PlayerBinding(None, None, c.PLAYER_AMBIGUOUS, None, len(candidates))
    if off_game:
        return PlayerBinding(None, None, c.PLAYER_WRONG_TEAM, None, len(candidates))
    return PlayerBinding(None, None, c.PLAYER_NO_EVIDENCE, None, len(candidates))
