"""Provider-free identity resolution for one existing Statcast archive day.

This wrapper reads canonical MLB identities only.  It does not construct a
source adapter, call a provider, flush the ORM session, or write canonical
database rows; artifact promotion remains delegated to ``statcast_promotion``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.historical.statcast_promotion import (
    StatcastGameMapping,
    StatcastPlayerMapping,
    StatcastPromotionError,
    StatcastPromotionOptions,
    StatcastPromotionResult,
    promote_statcast_archive,
)
from app.ingestion.normalizer import TEAM_ABBREVIATIONS
from app.models import ExternalIdMapping, GameFinalOutcome, GameTeam, Player, Team


MLB_SOURCE = "MLB_STATS_API"
# Baseball Savant's detail CSV uses ``AZ`` for Arizona while the canonical
# MLB normalization contract uses ``ARI``.  Keep that source-specific alias at
# this mapping boundary; every row is still checked against the exact game.
_STATCAST_TEAM_CODE_ALIASES = {"ARI": "AZ"}


def promote_existing_statcast_day(
    session: Session,
    archive_path: Path,
    artifact_root: Path,
    target_date: date,
    source_version: str,
) -> StatcastPromotionResult:
    """Map an existing raw day to canonical identities and promote it locally.

    Every lookup is exact and one-to-one.  The downstream promoter independently
    validates that every archive row has one of these mappings, so an archive
    identity absent from the canonical mapping set also fails closed.
    """

    options = StatcastPromotionOptions(
        archive_path=Path(archive_path),
        artifact_root=Path(artifact_root),
        target_date=target_date,
        source_version=source_version,
    )
    with session.no_autoflush:
        outcomes = _latest_official_outcomes(session, target_date)
        game_external_ids = _exact_game_external_ids(session, outcomes)
        team_codes = _exact_game_team_codes(session, outcomes)
        player_mappings = _exact_player_mappings(session)

    game_mappings = tuple(
        StatcastGameMapping(
            source_game_id=game_external_ids[outcome.game_id],
            game_id=str(outcome.game_id),
            official_date=target_date,
            home_team_code=team_codes[outcome.game_id][0],
            away_team_code=team_codes[outcome.game_id][1],
            final_at=_database_utc(outcome.final_at),
        )
        for outcome in outcomes
    )
    return promote_statcast_archive(
        options,
        game_mappings=game_mappings,
        player_mappings=player_mappings,
    )


def _latest_official_outcomes(
    session: Session,
    target_date: date,
) -> tuple[GameFinalOutcome, ...]:
    rows = tuple(session.scalars(
        select(GameFinalOutcome)
        .where(
            GameFinalOutcome.official_date == target_date,
            GameFinalOutcome.official.is_(True),
            GameFinalOutcome.outcome_status == "FINAL",
            GameFinalOutcome.source_mode == "LIVE",
        )
        .order_by(
            GameFinalOutcome.game_id,
            GameFinalOutcome.revision.desc(),
            GameFinalOutcome.retrieved_at.desc(),
            GameFinalOutcome.id.desc(),
        )
    ))
    if not rows:
        raise StatcastPromotionError(
            f"No exact official MLB final outcomes exist for {target_date.isoformat()}"
        )

    grouped: dict[UUID, list[GameFinalOutcome]] = defaultdict(list)
    for row in rows:
        grouped[row.game_id].append(row)
    latest: list[GameFinalOutcome] = []
    for game_id in sorted(grouped, key=str):
        candidates = grouped[game_id]
        revision = max(int(row.revision) for row in candidates)
        matches = [row for row in candidates if int(row.revision) == revision]
        if len(matches) != 1:
            raise StatcastPromotionError(
                f"Ambiguous latest official outcome for canonical game {game_id}"
            )
        latest.append(matches[0])
    return tuple(latest)


def _exact_game_external_ids(
    session: Session,
    outcomes: Iterable[GameFinalOutcome],
) -> dict[UUID, str]:
    game_ids = tuple(outcome.game_id for outcome in outcomes)
    mappings = tuple(session.scalars(
        select(ExternalIdMapping)
        .where(
            ExternalIdMapping.entity_type == "game",
            ExternalIdMapping.source == MLB_SOURCE,
            ExternalIdMapping.entity_id.in_(game_ids),
        )
        .order_by(ExternalIdMapping.entity_id, ExternalIdMapping.external_id)
    ))
    grouped: dict[UUID, list[ExternalIdMapping]] = defaultdict(list)
    for mapping in mappings:
        grouped[mapping.entity_id].append(mapping)

    result: dict[UUID, str] = {}
    reverse: dict[str, UUID] = {}
    for game_id in game_ids:
        exact = grouped.get(game_id, [])
        if not exact:
            raise StatcastPromotionError(
                f"Missing exact MLB game mapping for canonical game {game_id}"
            )
        if len(exact) != 1:
            raise StatcastPromotionError(
                f"Ambiguous MLB game mappings for canonical game {game_id}"
            )
        external_id = _positive_mlb_id(exact[0].external_id, "game")
        prior = reverse.get(external_id)
        if prior is not None and prior != game_id:
            raise StatcastPromotionError(
                f"Ambiguous canonical games for MLB game ID {external_id}"
            )
        result[game_id] = external_id
        reverse[external_id] = game_id
    return result


def _exact_game_team_codes(
    session: Session,
    outcomes: Iterable[GameFinalOutcome],
) -> dict[UUID, tuple[str, str]]:
    by_game = {outcome.game_id: outcome for outcome in outcomes}
    rows = tuple(session.execute(
        select(GameTeam, Team)
        .join(Team, Team.id == GameTeam.team_id)
        .where(GameTeam.game_id.in_(tuple(by_game)))
        .order_by(GameTeam.game_id, GameTeam.is_home.desc(), GameTeam.team_id)
    ).all())
    grouped: dict[UUID, list[tuple[GameTeam, Team]]] = defaultdict(list)
    for game_team, team in rows:
        grouped[game_team.game_id].append((game_team, team))

    result: dict[UUID, tuple[str, str]] = {}
    for game_id in sorted(by_game, key=str):
        sides = grouped.get(game_id, [])
        home = [(link, team) for link, team in sides if bool(link.is_home)]
        away = [(link, team) for link, team in sides if not bool(link.is_home)]
        if len(sides) != 2 or len(home) != 1 or len(away) != 1:
            raise StatcastPromotionError(
                f"Missing or ambiguous home/away GameTeam identities for game {game_id}"
            )
        home_link, home_team = home[0]
        away_link, away_team = away[0]
        outcome = by_game[game_id]
        if (
            home_link.team_id != outcome.home_team_id
            or home_team.id != outcome.home_team_id
            or away_link.team_id != outcome.away_team_id
            or away_team.id != outcome.away_team_id
        ):
            raise StatcastPromotionError(
                f"GameTeam/official-outcome team identity mismatch for game {game_id}"
            )
        result[game_id] = (
            _team_code(home_team.abbreviation, home_team.name, "home", game_id),
            _team_code(away_team.abbreviation, away_team.name, "away", game_id),
        )
    return result


def _exact_player_mappings(session: Session) -> tuple[StatcastPlayerMapping, ...]:
    mappings = tuple(session.scalars(
        select(ExternalIdMapping)
        .where(
            ExternalIdMapping.entity_type == "player",
            ExternalIdMapping.source == MLB_SOURCE,
        )
        .order_by(ExternalIdMapping.external_id, ExternalIdMapping.entity_id)
    ))
    if not mappings:
        raise StatcastPromotionError("No exact MLB player mappings are available")

    entity_ids = tuple({mapping.entity_id for mapping in mappings})
    existing = set(session.scalars(select(Player.id).where(Player.id.in_(entity_ids))))
    missing_entities = sorted(set(entity_ids) - existing, key=str)
    if missing_entities:
        raise StatcastPromotionError(
            "MLB player mappings reference missing canonical players: "
            + ", ".join(str(value) for value in missing_entities)
        )

    by_external: dict[str, UUID] = {}
    by_canonical: dict[UUID, str] = {}
    result: list[StatcastPlayerMapping] = []
    for mapping in mappings:
        external_id = _positive_mlb_id(mapping.external_id, "player")
        canonical_id = mapping.entity_id
        if external_id in by_external:
            raise StatcastPromotionError(
                f"Ambiguous canonical players for MLB player ID {external_id}"
            )
        prior_external = by_canonical.get(canonical_id)
        if prior_external is not None:
            raise StatcastPromotionError(
                f"Ambiguous MLB player mappings for canonical player {canonical_id}"
            )
        by_external[external_id] = canonical_id
        by_canonical[canonical_id] = external_id
        result.append(StatcastPlayerMapping(
            source_player_id=external_id,
            player_id=str(canonical_id),
        ))
    return tuple(result)


def _positive_mlb_id(value: object, entity_type: str) -> str:
    parsed = str(value or "").strip()
    if not parsed.isdigit() or int(parsed) <= 0:
        raise StatcastPromotionError(
            f"Exact MLB {entity_type} mapping ID must be a positive integer"
        )
    return str(int(parsed))


def _team_code(
    value: object,
    canonical_name: object,
    side: str,
    game_id: UUID,
) -> str:
    parsed = str(value or "").strip().upper()
    if parsed.startswith("MLB") and parsed[3:].isdigit():
        parsed = TEAM_ABBREVIATIONS.get(str(canonical_name or "").strip(), "")
    parsed = _STATCAST_TEAM_CODE_ALIASES.get(parsed, parsed)
    if not 2 <= len(parsed) <= 4 or not parsed.isalnum():
        raise StatcastPromotionError(
            f"Canonical {side} team code is invalid for game {game_id}"
        )
    return parsed


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = ["MLB_SOURCE", "promote_existing_statcast_day"]
