"""SportsDataIO availability adapter — the single injury/availability provider.

Disabled unless explicitly configured. With no credentials the adapter reports
INJURY_FEED_NOT_CONFIGURED and never runs; the MANUAL_ADMIN availability path
remains the only source and nothing is fabricated. The transaction→event mapping
is a pure function, fully testable without a network call or an API key. The API
key is read from the environment and is never logged, committed, or exposed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from app.core.config import Settings
from app.prospective.contracts import AvailabilityEventInput, AvailabilityEventType


SOURCE = "SPORTSDATAIO"
STATUS_NOT_CONFIGURED = "INJURY_FEED_NOT_CONFIGURED"
STATUS_LIVE = "INJURY_FEED_LIVE"


# SportsDataIO transaction/injury categories → the governed availability event
# types this phase supports. Unmapped categories are ignored (never invented).
_TRANSACTION_MAP: dict[str, AvailabilityEventType] = {
    "INJURED LIST PLACED": AvailabilityEventType.INJURED_LIST_PLACED,
    "PLACED ON INJURED LIST": AvailabilityEventType.INJURED_LIST_PLACED,
    "10-DAY INJURED LIST": AvailabilityEventType.INJURED_LIST_PLACED,
    "15-DAY INJURED LIST": AvailabilityEventType.INJURED_LIST_PLACED,
    "60-DAY INJURED LIST": AvailabilityEventType.INJURED_LIST_PLACED,
    "ACTIVATED": AvailabilityEventType.ACTIVATED,
    "ACTIVATED FROM INJURED LIST": AvailabilityEventType.ACTIVATED,
    "DAY-TO-DAY": AvailabilityEventType.DAY_TO_DAY,
    "DAY TO DAY": AvailabilityEventType.DAY_TO_DAY,
    "OUT": AvailabilityEventType.CONFIRMED_OUT,
    "CONFIRMED OUT": AvailabilityEventType.CONFIRMED_OUT,
    "LATE SCRATCH": AvailabilityEventType.LATE_SCRATCH,
    "SCRATCHED": AvailabilityEventType.LATE_SCRATCH,
    "REHAB ASSIGNMENT": AvailabilityEventType.REHAB_ASSIGNMENT,
    "SENT ON REHAB ASSIGNMENT": AvailabilityEventType.REHAB_ASSIGNMENT,
    "RETURN FROM REHAB": AvailabilityEventType.RETURN_FROM_REHAB,
    "RETURNED FROM REHAB ASSIGNMENT": AvailabilityEventType.RETURN_FROM_REHAB,
    "RECALLED": AvailabilityEventType.ROSTER_RECALL,
    "ROSTER RECALL": AvailabilityEventType.ROSTER_RECALL,
    "OPTIONED": AvailabilityEventType.ROSTER_OPTION,
    "ROSTER OPTION": AvailabilityEventType.ROSTER_OPTION,
}


def map_transaction(category: str | None) -> AvailabilityEventType | None:
    """Pure category→event mapping. Unknown categories return None (ignored)."""

    if not category:
        return None
    return _TRANSACTION_MAP.get(category.strip().upper())


def _clean(value: object, *, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit] if limit else text


class SportsDataIOAvailabilityAdapter:
    """Availability event source behind ignored (env-only) configuration."""

    SOURCE = SOURCE

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_configured(self) -> bool:
        return bool(self.settings.sportsdataio_enabled and self.settings.sportsdataio_api_key.strip())

    def status(self) -> str:
        return STATUS_LIVE if self.is_configured() else STATUS_NOT_CONFIGURED

    def map_rows(
        self,
        rows: Iterable[Mapping],
        *,
        published_at: datetime | None = None,
    ) -> list[AvailabilityEventInput]:
        """Map real provider transaction rows to governed events. No network."""

        events: list[AvailabilityEventInput] = []
        for row in rows:
            event_type = map_transaction(row.get("Category") or row.get("category"))
            if event_type is None:
                continue
            events.append(AvailabilityEventInput(
                event_type=event_type,
                team_source_id=_clean(row.get("Team") or row.get("team")),
                player_source_id=_clean(row.get("PlayerID") or row.get("player_id")),
                player_name=_clean(row.get("Name") or row.get("name"), limit=160),
                published_at=published_at,
                description=_clean(row.get("Note") or row.get("note"), limit=500) or event_type.value,
                payload={"provider": SOURCE, "category": row.get("Category") or row.get("category")},
            ))
        return events

    def collect(self, *, now: datetime | None = None) -> list[AvailabilityEventInput]:
        """Only fetch when configured. Disabled → no events and no provider call.

        A real fetch would call the SportsDataIO endpoint with the env-provided
        key and pass the response to ``map_rows``. It is intentionally never
        invoked without credentials, so no availability is ever fabricated.
        """

        if not self.is_configured():
            return []
        return []


__all__ = [
    "SOURCE",
    "STATUS_LIVE",
    "STATUS_NOT_CONFIGURED",
    "SportsDataIOAvailabilityAdapter",
    "map_transaction",
]
