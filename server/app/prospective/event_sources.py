"""Availability event sources.

Two provenances, both real, neither fabricated:

* ``ManualAdminAvailabilityEventSource`` — operator-entered events (the admin
  API). Explicitly labeled MANUAL_ADMIN. This covers event types MLB does not
  publish as a machine-readable push (IL placements, late scratches, etc.) and
  is the deterministic path used by tests.
* ``MLBDerivedAvailabilityEventSource`` — derives probable-pitcher changes by
  diffing a freshly fetched MLB Stats API schedule against the last persisted
  starters. The MLB Stats API is the sole external dependency; the diff itself
  is a pure function so it is testable without any network call.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Protocol

from app.prospective.contracts import AvailabilityEventInput, AvailabilityEventType


class AvailabilityEventSource(Protocol):
    def collect(self, *, now: datetime | None = None) -> list[AvailabilityEventInput]: ...


class ManualAdminAvailabilityEventSource:
    """Operator-entered events (MANUAL_ADMIN). No fabrication; no provider call."""

    def __init__(self, events: Iterable[AvailabilityEventInput] | None = None) -> None:
        self._events = list(events or [])

    def submit(self, event: AvailabilityEventInput) -> None:
        self._events.append(event)

    def collect(self, *, now: datetime | None = None) -> list[AvailabilityEventInput]:
        pending, self._events = self._events, []
        return pending


class MLBDerivedAvailabilityEventSource:
    """Derive availability events from the configured public MLB Stats API.

    A job fetches the current schedule with ``MLBStatsAPIAdapter.get_schedule``
    and passes the fresh probable-pitcher map plus the last persisted starters
    to :meth:`diff_probable_pitchers`. Only observed differences become events;
    nothing is invented.
    """

    SOURCE = "MLB_STATS_API"

    @staticmethod
    def diff_probable_pitchers(
        fresh_probables: Mapping[str, str | None],
        persisted_probables: Mapping[str, str | None],
        *,
        published_at: datetime | None = None,
    ) -> list[AvailabilityEventInput]:
        """Emit PROBABLE_PITCHER_CHANGED for games whose starter id changed.

        Keys are internal game ids; values are the source pitcher id (or None).
        A change is emitted only when both sides are known and differ, so a
        first-time observation (persisted None) is not reported as a change.
        """

        events: list[AvailabilityEventInput] = []
        for game_id, fresh_pitcher in fresh_probables.items():
            previous = persisted_probables.get(game_id)
            if previous is None or fresh_pitcher is None or fresh_pitcher == previous:
                continue
            events.append(AvailabilityEventInput(
                event_type=AvailabilityEventType.PROBABLE_PITCHER_CHANGED,
                game_id=game_id,
                player_source_id=fresh_pitcher,
                published_at=published_at,
                description=f"Probable pitcher changed to {fresh_pitcher}",
                payload={"previous_probable_pitcher": previous, "current_probable_pitcher": fresh_pitcher},
            ))
        return events

    @staticmethod
    def diff_lineups(
        game_id: str,
        fresh_lineup_status: str,
        persisted_lineup_status: str | None,
        *,
        published_at: datetime | None = None,
    ) -> list[AvailabilityEventInput]:
        """Emit LINEUP_CONFIRMED / LINEUP_CHANGED from observed status transitions."""

        if persisted_lineup_status == fresh_lineup_status:
            return []
        if fresh_lineup_status == "CONFIRMED" and persisted_lineup_status != "CONFIRMED":
            event_type = AvailabilityEventType.LINEUP_CONFIRMED
        elif persisted_lineup_status == "CONFIRMED":
            event_type = AvailabilityEventType.LINEUP_CHANGED
        else:
            return []
        return [AvailabilityEventInput(
            event_type=event_type,
            game_id=game_id,
            published_at=published_at,
            description=f"Lineup status {persisted_lineup_status} -> {fresh_lineup_status}",
            payload={"previous_status": persisted_lineup_status, "current_status": fresh_lineup_status},
        )]


def probable_map_from_schedule(games: Sequence[Mapping]) -> dict[str, str | None]:
    """Extract {source_game_id: probable_pitcher_id} from schedule DTO dicts."""

    result: dict[str, str | None] = {}
    for game in games:
        home = (game.get("home_pitcher") or {}).get("source_id")
        away = (game.get("away_pitcher") or {}).get("source_id")
        result[str(game.get("source_game_id"))] = home or away
    return result


__all__ = [
    "AvailabilityEventSource",
    "ManualAdminAvailabilityEventSource",
    "MLBDerivedAvailabilityEventSource",
    "probable_map_from_schedule",
]
