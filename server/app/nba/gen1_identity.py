"""Exact NBA identity mapping: Codex ids <-> AISP games and teams, unique both ways.

THE ID SPACES
-------------
* Codex game:     ``nba-espn:game:<ESPN id>``  -> AISP ``games`` via
                  ``external_id_mappings(entity_type='game', source='NBA_ESPN', external_id=<ESPN id>)``
* Codex team:     ``nba-neil:franchise:<CODE>`` -> AISP ``teams`` via
                  ``external_id_mappings(entity_type='team', source='NBA_NEIL_FRANCHISE')``
* ESPN team id:   the retained source rows' numeric ``home_id``/``away_id`` -> the same
                  AISP team via ``source='NBA_ESPN_TEAM'``. Learned only from a game
                  whose ESPN id and Codex franchise ids are both known, and refused
                  if one franchise would ever meet two ESPN ids or the reverse.
* ``nba_games`` / ``nba_teams`` hold SportsDataIO FIXTURE rows only
  (data_mode='FIXTURE'). They are a different, invented id space and are never
  mapped to a Codex id -- the report counts them and says so.

WHAT "UNIQUE BOTH WAYS" MEANS HERE
----------------------------------
A Codex game maps to exactly one AISP game and that AISP game carries exactly one
NBA_ESPN id, the same one. A Codex franchise maps to exactly one AISP team and
that team carries exactly one franchise id. Anything else is a CONFLICT and is
listed; nothing is joined by team name or by date.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.nba import gen1_common as common
from app.services import event_time_reconciliation as event_time


class IdentityRefused(RuntimeError):
    """Admission refused, with the reason named."""


def espn_id(codex_game_id: str) -> str:
    prefix = "nba-espn:game:"
    text = str(codex_game_id or "")
    if not text.startswith(prefix) or not text[len(prefix):].isdigit():
        raise IdentityRefused(f"not a Codex ESPN game id: {codex_game_id!r}")
    return text[len(prefix):]


def franchise_code(franchise_id: str) -> str:
    prefix = "nba-neil:franchise:"
    text = str(franchise_id or "")
    if not text.startswith(prefix) or not text[len(prefix):].isalpha():
        raise IdentityRefused(f"not a Codex franchise id: {franchise_id!r}")
    return text[len(prefix):]


def _mapping(session: Session, entity_type: str, source: str, external_id: str):
    from app.models import ExternalIdMapping

    return session.scalar(select(ExternalIdMapping).where(
        ExternalIdMapping.entity_type == entity_type, ExternalIdMapping.source == source,
        ExternalIdMapping.external_id == str(external_id)))


def canonical_game_id(session: Session, espn_game_id: str) -> uuid.UUID | None:
    mapping = _mapping(session, "game", common.SOURCE_ESPN_GAME, espn_game_id)
    return mapping.entity_id if mapping is not None else None


def _league_and_season(session: Session, *, season_end: int):
    from app.models import League, Season, Sport

    sport = session.scalar(select(Sport).where(Sport.code == "BASKETBALL"))
    if sport is None:
        sport = Sport(name="Basketball", code="BASKETBALL")
        session.add(sport)
        session.flush()
    league = session.scalar(select(League).where(League.code == common.LEAGUE_CODE))
    if league is None:
        league = League(sport_id=sport.id, code=common.LEAGUE_CODE, name="NBA")
        session.add(league)
        session.flush()
    # Same naming the research importer already used ("2026-27"), so both land in one season.
    name = f"{season_end - 1}-{str(season_end)[-2:]}"
    season = session.scalar(select(Season).where(Season.league_id == league.id, Season.name == name))
    if season is None:
        season = Season(league_id=league.id, name=name,
                        starts_at=datetime(season_end - 1, 10, 1, tzinfo=UTC),
                        ends_at=datetime(season_end, 6, 30, tzinfo=UTC))
        session.add(season)
        session.flush()
    return league, season


def _team(session: Session, league, *, franchise_id: str, code: str, name: str | None,
          espn_team_id: str | None):
    """The AISP team for a Codex franchise, created once; ESPN team id bound exactly."""

    from app.models import ExternalIdMapping, Team

    mapping = _mapping(session, "team", common.SOURCE_FRANCHISE, franchise_id)
    if mapping is not None:
        team = session.get(Team, mapping.entity_id)
    else:
        team = Team(league_id=league.id, name=name or code, abbreviation=code)
        session.add(team)
        session.flush()
        session.add(ExternalIdMapping(entity_type="team", entity_id=team.id,
                                      source=common.SOURCE_FRANCHISE, external_id=franchise_id))
        session.flush()
    if espn_team_id:
        by_espn = _mapping(session, "team", common.SOURCE_ESPN_TEAM, espn_team_id)
        if by_espn is not None and by_espn.entity_id != team.id:
            raise IdentityRefused(f"ESPN team {espn_team_id} is already bound to another AISP team "
                                  f"than {franchise_id}")
        existing = session.scalar(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == common.SOURCE_ESPN_TEAM,
            ExternalIdMapping.entity_id == team.id))
        if existing is not None and existing.external_id != str(espn_team_id):
            raise IdentityRefused(f"{franchise_id} is already bound to ESPN team {existing.external_id}, "
                                  f"not {espn_team_id}")
        if by_espn is None and existing is None:
            session.add(ExternalIdMapping(entity_type="team", entity_id=team.id,
                                          source=common.SOURCE_ESPN_TEAM, external_id=str(espn_team_id)))
            session.flush()
    return team


def game_clock_assessment(session: Session, game_id, *, provider_start, home_franchise_id, away_franchise_id):
    """ORM identity adapter; PM owns the shared pure clock rule. Never retimes a game."""
    from app.models import ExternalIdMapping, Game, GameTeam, League
    game = session.get(Game, game_id)
    if game is None:
        raise IdentityRefused("ADMITTED_GAME_ABSENT")
    league = session.get(League, game.league_id)
    if league is None or league.code != common.LEAGUE_CODE:
        raise IdentityRefused("SPORT_IDENTITY_CONFLICT")
    sides = {row.is_home: row.team_id for row in session.scalars(select(GameTeam).where(GameTeam.game_id == game_id))}
    expected = {}
    for side, franchise in ((True, home_franchise_id), (False, away_franchise_id)):
        mapping = _mapping(session, "team", common.SOURCE_FRANCHISE, franchise)
        if mapping is None or mapping.entity_id != sides.get(side):
            raise IdentityRefused("PARTICIPANTS_DIFFER_FROM_ADMITTED_GAME")
        expected[side] = mapping.entity_id
    check = event_time.reconcile_clock_identity(common.aware(game.starts_at), provider_start)
    if not check.accepted:
        raise IdentityRefused(check.reason)
    # Exact same-day occurrence remains mandatory even when a native ID is known.
    # Query a full Eastern day, not a nearest-time subset or a UTC-date equality.
    day = check.canonical_start.astimezone(event_time.SLATE_TIMEZONE).date()
    lo = datetime.combine(day, datetime.min.time(), event_time.SLATE_TIMEZONE).astimezone(UTC)
    hi = datetime.combine(day + timedelta(days=1), datetime.min.time(), event_time.SLATE_TIMEZONE).astimezone(UTC)
    home_side, away_side = GameTeam.__table__.alias("clock_home"), GameTeam.__table__.alias("clock_away")
    occurrences = list(session.scalars(select(Game.id).join(home_side, home_side.c.game_id == Game.id)
        .join(away_side, away_side.c.game_id == Game.id).where(Game.league_id == game.league_id,
        Game.starts_at >= lo, Game.starts_at < hi, home_side.c.is_home.is_(True),
        home_side.c.team_id == expected[True], away_side.c.is_home.is_(False), away_side.c.team_id == expected[False])))
    if len(set(occurrences)) != 1 or game_id not in occurrences:
        raise IdentityRefused("AMBIGUOUS_SAME_DAY_OCCURRENCE")
    links = list(session.scalars(select(ExternalIdMapping.external_id).where(ExternalIdMapping.entity_type == "game",
        ExternalIdMapping.source == common.SOURCE_ESPN_GAME, ExternalIdMapping.entity_id == game_id)))
    if len(set(links)) != 1:
        raise IdentityRefused("NATIVE_EVENT_ID_NOT_UNIQUE")
    return check


def admit_game(session: Session, *, codex_game_id: str, home_franchise_id: str, away_franchise_id: str,
               home_code: str, away_code: str, tipoff_utc: datetime, season_end: int,
               home_espn_team_id: str | None = None, away_espn_team_id: str | None = None,
               home_name: str | None = None, away_name: str | None = None) -> dict[str, Any]:
    """Resolve -- or register -- the AISP game for one Codex game identity.

    An existing game must agree on BOTH teams (by franchise mapping) and the
    shared bounded clock identity; canonical clocks are never overwritten.
    """

    from app.models import ExternalIdMapping, Game, GameTeam

    game_espn = espn_id(codex_game_id)
    if franchise_code(home_franchise_id) == franchise_code(away_franchise_id):
        raise IdentityRefused("home and away franchise are identical")
    try:
        tipoff = event_time.utc(tipoff_utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise IdentityRefused("no aware tipoff to admit against") from exc
    league, season = _league_and_season(session, season_end=int(season_end))
    home = _team(session, league, franchise_id=home_franchise_id, code=home_code, name=home_name,
                 espn_team_id=home_espn_team_id)
    away = _team(session, league, franchise_id=away_franchise_id, code=away_code, name=away_name,
                 espn_team_id=away_espn_team_id)

    existing = canonical_game_id(session, game_espn)
    if existing is not None:
        game = session.get(Game, existing)
        sides = {row.is_home: row.team_id for row in session.scalars(
            select(GameTeam).where(GameTeam.game_id == existing))}
        if sides.get(True) != home.id or sides.get(False) != away.id:
            raise IdentityRefused(f"AISP game {existing} disagrees with {codex_game_id} on its teams")
        check = game_clock_assessment(session, existing, provider_start=tipoff,
            home_franchise_id=home_franchise_id, away_franchise_id=away_franchise_id)
        return {"result": "ALREADY_ADMITTED", "game_id": str(existing), "espn_game_id": game_espn,
                "event_clock_reconciliation": check.receipt()}

    # A new native ID must not mint a duplicate for an already-known occurrence.
    day = tipoff.astimezone(event_time.SLATE_TIMEZONE).date()
    lo = datetime.combine(day, datetime.min.time(), event_time.SLATE_TIMEZONE).astimezone(UTC)
    hi = datetime.combine(day + timedelta(days=1), datetime.min.time(), event_time.SLATE_TIMEZONE).astimezone(UTC)
    home_side, away_side = GameTeam.__table__.alias("new_home"), GameTeam.__table__.alias("new_away")
    duplicate = session.scalar(select(Game.id).join(home_side, home_side.c.game_id == Game.id)
        .join(away_side, away_side.c.game_id == Game.id).where(Game.league_id == league.id,
        Game.starts_at >= lo, Game.starts_at < hi, home_side.c.is_home.is_(True), home_side.c.team_id == home.id,
        away_side.c.is_home.is_(False), away_side.c.team_id == away.id).limit(1))
    if duplicate is not None:
        raise IdentityRefused("UNLINKED_NATIVE_ID_FOR_EXISTING_OCCURRENCE")
    game = Game(league_id=league.id, season_id=season.id, starts_at=tipoff, status="SCHEDULED",
                source_mode=common.SOURCE_MODE)
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
        ExternalIdMapping(entity_type="game", entity_id=game.id, source=common.SOURCE_ESPN_GAME,
                          external_id=game_espn),
    ])
    session.flush()
    return {"result": "ADMITTED", "game_id": str(game.id), "espn_game_id": game_espn,
            "tipoff_utc": tipoff.isoformat(), "source_mode": common.SOURCE_MODE}


def espn_team_pairs(codex_rows: list[dict[str, Any]], source_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Franchise <-> ESPN team id pairs learned from games whose both identities are known.

    Unique both ways or refused: a franchise seen with two ESPN ids, or an ESPN id
    seen with two franchises, is a conflict and neither pair is used.
    """

    by_franchise: dict[str, set[str]] = defaultdict(set)
    by_espn: dict[str, set[str]] = defaultdict(set)
    for row in codex_rows:
        source = source_rows.get(str(row["espn_game_id"]))
        if source is None:
            continue
        for side in ("home", "away"):
            franchise = row[f"{side}_franchise_id"]
            espn_team = str(source[f"{side}_id"])
            by_franchise[franchise].add(espn_team)
            by_espn[espn_team].add(franchise)
    pairs: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    for franchise, espn_ids in sorted(by_franchise.items()):
        if len(espn_ids) != 1:
            conflicts.append({"franchise": franchise, "espn_team_ids": sorted(espn_ids)})
            continue
        (espn_team,) = espn_ids
        if len(by_espn[espn_team]) != 1:
            conflicts.append({"espn_team_id": espn_team, "franchises": sorted(by_espn[espn_team])})
            continue
        pairs[franchise] = espn_team
    return {"pairs": pairs, "conflicts": conflicts}


def report(session: Session, *, schedule_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Mapping coverage for every Codex identity AISP holds, plus an optional schedule inventory.

    ``schedule_rows`` are Codex-shaped identities (``codex_game_id``, franchise ids,
    ``disposition``) from a retained schedule; they are listed as UNMAPPED until a
    package admits them, never guessed into a game.
    """

    from app.models import (
        ExternalIdMapping, Game, GameTeam, NbaGame, NbaGen1Forecast, NbaHistoricalForecast,
        NbaResearchForecast, NbaTeam,
    )

    identities: dict[str, dict[str, Any]] = {}

    def note(codex_game_id: str, home: str, away: str, origin: str) -> None:
        entry = identities.setdefault(codex_game_id, {"codex_game_id": codex_game_id, "home_franchise_id": home,
                                                      "away_franchise_id": away, "origins": set()})
        entry["origins"].add(origin)

    for row in session.scalars(select(NbaGen1Forecast)):
        note(row.codex_game_id, row.home_franchise_id, row.away_franchise_id, "PRODUCTION_PACKAGE")
    for row in session.scalars(select(NbaHistoricalForecast)):
        note(row.codex_game_id, row.home_franchise_id, row.away_franchise_id, "HISTORICAL_ASSESSMENT")
    for row in session.scalars(select(NbaResearchForecast)):
        note(f"nba-espn:game:{row.source_game_id}", row.home_franchise_id or "", row.away_franchise_id or "",
             "RESEARCH_FORECAST")
    for row in schedule_rows or []:
        note(row["codex_game_id"], row.get("home_franchise_id") or "", row.get("away_franchise_id") or "",
             f"SCHEDULE:{row.get('disposition') or 'LISTED'}")

    game_maps: dict[str, list] = defaultdict(list)
    reverse: dict[Any, list[str]] = defaultdict(list)
    for mapping in session.scalars(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == common.SOURCE_ESPN_GAME)):
        game_maps[mapping.external_id].append(mapping.entity_id)
        reverse[mapping.entity_id].append(mapping.external_id)
    team_maps: dict[str, list] = defaultdict(list)
    team_reverse: dict[Any, list[str]] = defaultdict(list)
    for mapping in session.scalars(select(ExternalIdMapping).where(
            ExternalIdMapping.entity_type == "team", ExternalIdMapping.source == common.SOURCE_FRANCHISE)):
        team_maps[mapping.external_id].append(mapping.entity_id)
        team_reverse[mapping.entity_id].append(mapping.external_id)

    games: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    for codex_game_id in sorted(identities):
        entry = identities[codex_game_id]
        origins = sorted(entry["origins"])
        try:
            game_espn = espn_id(codex_game_id)
        except IdentityRefused:
            state, detail, aisp = "UNMAPPABLE_ID", "not an ESPN game identity (placeholder or unresolved)", None
        else:
            targets = game_maps.get(game_espn, [])
            if not targets:
                state, detail, aisp = "UNMAPPED", "no AISP game carries this ESPN id; awaiting a package", None
            elif len(targets) > 1:
                state, detail, aisp = "CONFLICT", f"{len(targets)} AISP games carry ESPN id {game_espn}", None
            elif len(reverse[targets[0]]) > 1:
                state, detail, aisp = ("CONFLICT", f"AISP game {targets[0]} carries several ESPN ids", None)
            else:
                aisp = targets[0]
                sides = {row.is_home: row.team_id for row in session.scalars(
                    select(GameTeam).where(GameTeam.game_id == aisp))}
                expected = {True: team_maps.get(entry["home_franchise_id"], []),
                            False: team_maps.get(entry["away_franchise_id"], [])}
                if any(len(expected[side]) != 1 or sides.get(side) != expected[side][0] for side in (True, False)):
                    state, detail = "CONFLICT", "the AISP game's teams disagree with the Codex franchise ids"
                else:
                    state, detail = "MAPPED", "unique both ways"
        counts[state] += 1
        game = session.get(Game, aisp) if aisp else None
        games.append({"codex_game_id": codex_game_id, "origins": origins, "state": state, "detail": detail,
                      "aisp_game_id": str(aisp) if aisp else None,
                      "aisp_start_utc": common.iso(game.starts_at) if game else None,
                      "home_franchise_id": entry["home_franchise_id"], "away_franchise_id": entry["away_franchise_id"]})

    teams = []
    for franchise in sorted({e["home_franchise_id"] for e in identities.values()}
                            | {e["away_franchise_id"] for e in identities.values()}):
        if not franchise:
            continue
        targets = team_maps.get(franchise, [])
        if not targets:
            state = "UNMAPPED"
        elif len(targets) > 1 or len(team_reverse[targets[0]]) > 1:
            state = "CONFLICT"
        else:
            state = "MAPPED"
        teams.append({"franchise_id": franchise, "state": state,
                      "aisp_team_id": str(targets[0]) if len(targets) == 1 else None})

    fixture_games = session.scalar(select(func.count()).select_from(NbaGame)) or 0
    fixture_teams = session.scalar(select(func.count()).select_from(NbaTeam)) or 0
    return {
        "schema_version": "AISP_NBA_IDENTITY_MAP_V1",
        "games": games,
        "game_counts": dict(counts),
        "teams": teams,
        "team_counts": {s: sum(1 for t in teams if t["state"] == s) for s in ("MAPPED", "UNMAPPED", "CONFLICT")},
        "unmapped": [g for g in games if g["state"] != "MAPPED"],
        "fixture_id_space": {
            "nba_games": fixture_games, "nba_teams": fixture_teams,
            "note": ("nba_games / nba_teams hold SportsDataIO FIXTURE rows in an invented id space. They are "
                     "never mapped to a Codex id; real NBA games live in games/teams via NBA_ESPN mappings."),
        },
        "rule": "Exact ids only, unique both ways. Nothing is joined by team name or date.",
    }


__all__ = ["IdentityRefused", "admit_game", "canonical_game_id", "espn_id", "espn_team_pairs",
           "franchise_code", "report"]
