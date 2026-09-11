"""Canonical MLB events, registered before anything needs to map to them.

THE FAILURE THIS FIXES
----------------------
Model Studio's V18 publisher discovers the next two days of MLB from the league
schedule and, for each game, asks AISP which canonical event it is. On
2026-09-11 it asked about 30 games and got an answer for 15. Every one of the
15 failures was a game on 2026-09-12, and every one carried the same reason:

    CANONICAL_EVENT_NOT_REGISTERED_IN_LOCAL_IDENTITY_SOURCE

It was not a namespace mismatch, not a team-identity problem, and not a
time-matching failure. AISP's `games` table simply ended at 2026-09-11. There
was nothing to map to, because nothing in the scheduler had ever created a
future event: schedule ingestion is keyed to `event_date = today`, so tomorrow
became mappable at midnight and not one minute earlier. A publisher that works
a day ahead therefore found an empty table a day ahead, every single day.

WHY THIS JOB DOES NO MATCHING OF ITS OWN
----------------------------------------
It would be easy, and wrong, to write a second matcher here. The governed
normalizer already resolves an event correctly:

  * it looks up `ExternalIdMapping(game, MLB_STATS_API, <gamePk>)` FIRST, which
    is the league's own authoritative identifier and cannot be ambiguous;
  * only when no gamePk mapping exists does it fall back to a same-league,
    same-sides, +/-2h search, and that fallback returns a game only when
    EXACTLY ONE candidate matches -- so a doubleheader resolves by gamePk or
    not at all, never by picking the nearer of two;
  * home and away are written through separate `is_home` sides, so the two
    cannot be transposed;
  * a game that is already FINAL keeps its projection.

So this job's whole contribution is *when*: it runs the existing governed path
over a horizon of days instead of one day, early enough that a pregame
publisher finds the event waiting.

WHAT IT WILL NOT DO
-------------------
It creates no forecast, no decision and no position; it reads the free MLB
Stats API schedule endpoint (one request per date, no key, no cost) and writes
only canonical events, sides and their identity mappings. It never manufactures
an event for a game the league does not list.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.data_sources.mlb import MLBStatsAPIAdapter
from app.data_sources.base import ScheduleGameDTO, SourceError
from app.ingestion.normalizer import CanonicalNormalizer
from app.models import ExternalIdMapping, Game, GameTeam, League, Season, Team

SOURCE = "MLB_STATS_API"

#: How far ahead to register events. Two days covers the publisher's own D and
#: D+1 checkpoint horizon with a day of slack; a longer horizon would buy
#: nothing and would re-ingest schedules that are still being finalised.
DEFAULT_HORIZON_DAYS = 2

#: Per-date outcomes, so a date that produced nothing says which nothing.
REGISTERED = "REGISTERED"
REUSED = "ALREADY_REGISTERED"
NO_GAMES = "LEAGUE_PUBLISHES_NO_GAMES_ON_THIS_DATE"
SOURCE_UNAVAILABLE = "SCHEDULE_SOURCE_UNAVAILABLE"

#: Per-game mapping failures, recorded rather than swallowed.
NO_SOURCE_ID = "SCHEDULE_ROW_CARRIES_NO_GAMEPK"
AMBIGUOUS = "AMBIGUOUS_WITHOUT_A_GAMEPK_MAPPING"
SIDES_INCOMPLETE = "EVENT_LACKS_EXACT_HOME_AND_AWAY_SIDES"


@dataclass
class BridgeOutcome:
    """What one bridge run registered, reused and could not resolve."""

    horizon_days: int
    dates: list[dict[str, Any]] = field(default_factory=list)
    games_seen: int = 0
    events_registered: int = 0
    events_reused: int = 0
    mapping_failures: list[dict[str, Any]] = field(default_factory=list)
    provider_requests: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon_days": self.horizon_days,
            "dates": self.dates,
            "games_seen": self.games_seen,
            "events_registered": self.events_registered,
            "events_reused": self.events_reused,
            "mapping_failures": self.mapping_failures,
            "unresolved": len(self.mapping_failures),
            # Free public schedule endpoint: no key, no metered allowance.
            "provider_requests": self.provider_requests,
            "sportsbook_quote_credits_spent": 0,
            "models_executed": 0,
            "forecasts_created": 0,
            "official_decisions_created": 0,
            "positions_created": 0,
        }


class MlbEventBridge:
    """Registers canonical MLB events across a short forward horizon."""

    def __init__(self, session: Session, settings: Settings,
                 *, adapter: Any | None = None) -> None:
        self.session = session
        self.settings = settings
        self.adapter = adapter or MLBStatsAPIAdapter()
        self.normalizer = CanonicalNormalizer(session)

    async def run(
        self, *, start: date | None = None, horizon_days: int = DEFAULT_HORIZON_DAYS,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        from app.core.time import local_today

        start = start or local_today(self.settings.display_timezone)
        outcome = BridgeOutcome(horizon_days=horizon_days)

        for offset in range(horizon_days + 1):
            event_date = start + timedelta(days=offset)
            before = self._registered_gamepks(event_date)
            # Close the read transaction BEFORE going to the network. A SELECT
            # leaves a transaction open in SQLAlchemy, and awaiting an HTTP call
            # with one open pins a Postgres backend in `idle in transaction` for
            # the whole round trip. Every other scheduled job that touches the
            # same rows then queues behind it, and a slow league response stops
            # being this job's problem and becomes the scheduler's. Nothing has
            # been written yet at this point, so there is nothing to lose.
            self.session.rollback()
            try:
                outcome.provider_requests += 1
                response = await self.adapter.get_schedule(event_date)
            except SourceError as error:
                outcome.dates.append({
                    "date": event_date.isoformat(), "status": SOURCE_UNAVAILABLE,
                    "detail": str(error), "games": 0,
                })
                continue

            rows = list(response.envelope.data)
            if not rows:
                outcome.dates.append({
                    "date": event_date.isoformat(), "status": NO_GAMES, "games": 0,
                })
                continue

            candidates: list[ScheduleGameDTO] = []
            for item in rows:
                outcome.games_seen += 1
                source_id = str(item.get("source_game_id") or "").strip()
                if not source_id:
                    outcome.mapping_failures.append({
                        "date": event_date.isoformat(), "reason": NO_SOURCE_ID,
                        "detail": "The league row carries no gamePk, so no authoritative "
                                  "identifier exists and nothing else is trusted to stand in.",
                    })
                    continue
                try:
                    candidates.append(ScheduleGameDTO.model_validate(item))
                except Exception as error:  # one malformed row never aborts a date
                    outcome.mapping_failures.append({
                        "date": event_date.isoformat(), "source_game_id": source_id,
                        "reason": "SCHEDULE_ROW_REJECTED_BY_CONTRACT", "detail": str(error)[:200],
                    })

            if dry_run or not candidates:
                outcome.dates.append({
                    "date": event_date.isoformat(), "status": "DRY_RUN" if dry_run else NO_GAMES,
                    "games": len(candidates), "already_registered": len(before),
                })
                continue

            # The governed path. Everything about correct identity resolution
            # -- gamePk first, ambiguity refused, sides never transposed -- is
            # its behaviour, not ours.
            self.normalizer.ingest_schedule(
                candidates, SOURCE, available_as_of=response.envelope.fetched_at,
            )
            self.session.flush()

            after = self._registered_gamepks(event_date)
            registered = len(after - before)
            reused = len(after & before)
            outcome.events_registered += registered
            outcome.events_reused += reused
            outcome.dates.append({
                "date": event_date.isoformat(),
                "status": REGISTERED if registered else REUSED,
                "games": len(candidates),
                "events_registered": registered,
                "events_reused": reused,
            })

            for failure in self._unmapped(candidates):
                failure["date"] = event_date.isoformat()
                outcome.mapping_failures.append(failure)

            # Commit each date as it completes, so the next date's network call
            # does not hold this date's writes open behind it.
            self.session.commit()

        return outcome.as_dict()

    # -- verification ------------------------------------------------------
    def _registered_gamepks(self, event_date: date) -> set[str]:
        """Every gamePk AISP can already resolve for one operational day.

        Bounded by the OPERATIONAL day, not by a rough window around midnight
        UTC. An approximate window overlaps its neighbours -- a 7:40pm Eastern
        game on the 11th sits inside a naive window for the 12th -- and would
        report tomorrow as already registered while tomorrow was in fact empty.
        """

        from app.core.time import local_day_bounds

        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        return {
            str(external_id)
            for external_id in self.session.scalars(
                select(ExternalIdMapping.external_id)
                .join(Game, Game.id == ExternalIdMapping.entity_id)
                .join(Season, Season.id == Game.season_id)
                .join(League, League.id == Season.league_id)
                .where(
                    ExternalIdMapping.entity_type == "game",
                    ExternalIdMapping.source == SOURCE,
                    League.code == "MLB",
                    Game.starts_at >= start,
                    Game.starts_at < end,
                )
            )
        }

    def _unmapped(self, candidates: list[ScheduleGameDTO]) -> list[dict[str, Any]]:
        """Rows that still have no canonical event after the governed pass.

        A row reaching here means the normalizer declined to resolve it --
        almost always an ambiguous doubleheader with no gamePk mapping yet.
        Recording it is the point: an unmapped game must be visible, not
        silently absent from tomorrow's coverage.
        """

        failures: list[dict[str, Any]] = []
        for dto in candidates:
            mapping = self.session.scalar(select(ExternalIdMapping).where(
                ExternalIdMapping.entity_type == "game",
                ExternalIdMapping.source == SOURCE,
                ExternalIdMapping.external_id == dto.source_game_id,
            ))
            if mapping is None:
                failures.append({
                    "source_game_id": dto.source_game_id,
                    "matchup": f"{dto.away_team.abbreviation or dto.away_team.name}"
                               f" @ {dto.home_team.abbreviation or dto.home_team.name}",
                    "scheduled_at": dto.scheduled_at.isoformat(),
                    "reason": AMBIGUOUS,
                    "detail": "No gamePk mapping existed and more than one stored event matched "
                              "the same sides within two hours, so the resolver refused rather "
                              "than guessing between the halves of a doubleheader.",
                })
                continue
            sides = list(self.session.scalars(
                select(GameTeam).where(GameTeam.game_id == mapping.entity_id)))
            if {side.is_home for side in sides} != {True, False}:
                failures.append({
                    "source_game_id": dto.source_game_id,
                    "reason": SIDES_INCOMPLETE,
                    "detail": f"The canonical event holds {len(sides)} side(s); a mapping is only "
                              "usable when exactly one home and one away side exist.",
                })
        return failures


def mapping_coverage(
    session: Session, event_date: date, *, display_timezone: str = "America/New_York",
) -> dict[str, Any]:
    """How many of one date's canonical events carry a gamePk mapping.

    Read-only. Used by the bridge's own verification and by the coverage
    funnel, both of which need the same answer and must not compute it twice.
    """

    from app.core.time import local_day_bounds

    window_start, window_end = local_day_bounds(event_date, display_timezone)
    games = list(session.scalars(
        select(Game)
        .join(Season, Season.id == Game.season_id)
        .join(League, League.id == Season.league_id)
        .where(League.code == "MLB", Game.starts_at >= window_start, Game.starts_at < window_end)
    ))
    mapped = {
        mapping.entity_id for mapping in session.scalars(
            select(ExternalIdMapping).where(
                ExternalIdMapping.entity_type == "game",
                ExternalIdMapping.source == SOURCE,
                ExternalIdMapping.entity_id.in_([game.id for game in games] or [None]),
            )
        )
    }
    return {
        "date": event_date.isoformat(),
        "canonical_events": len(games),
        "with_gamepk_mapping": len(mapped),
        "without_gamepk_mapping": len(games) - len(mapped),
        "unmapped_event_ids": [str(game.id) for game in games if game.id not in mapped],
    }


__all__ = [
    "AMBIGUOUS", "BridgeOutcome", "DEFAULT_HORIZON_DAYS", "MlbEventBridge",
    "NO_GAMES", "NO_SOURCE_ID", "REGISTERED", "REUSED", "SIDES_INCOMPLETE",
    "SOURCE", "SOURCE_UNAVAILABLE", "mapping_coverage",
]
