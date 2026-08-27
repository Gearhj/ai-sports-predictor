"""Sport-neutral availability-event contract: AISP_AVAILABILITY_EVENT_V1."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AVAILABILITY_EVENT_CONTRACT = "AISP_AVAILABILITY_EVENT_V1"


class AvailabilityEventType(StrEnum):
    INJURED_LIST_PLACED = "INJURED_LIST_PLACED"
    ACTIVATED = "ACTIVATED"
    DAY_TO_DAY = "DAY_TO_DAY"
    INJURY_STATUS_CHANGED = "INJURY_STATUS_CHANGED"
    CONFIRMED_OUT = "CONFIRMED_OUT"
    LATE_SCRATCH = "LATE_SCRATCH"
    PROBABLE_PITCHER_CHANGED = "PROBABLE_PITCHER_CHANGED"
    LINEUP_CONFIRMED = "LINEUP_CONFIRMED"
    LINEUP_CHANGED = "LINEUP_CHANGED"
    REHAB_ASSIGNMENT = "REHAB_ASSIGNMENT"
    RETURN_FROM_REHAB = "RETURN_FROM_REHAB"
    ROSTER_RECALL = "ROSTER_RECALL"
    ROSTER_OPTION = "ROSTER_OPTION"


# Events whose provenance is an operator, not an official push feed. They are
# real inputs but explicitly not sourced from a machine provider.
MANUAL_ADMIN_SOURCE = "MANUAL_ADMIN"
# The single configured real external provider for derivable availability
# signals (probable-pitcher / lineup state). Free, public, no credentials.
MLB_STATS_API_SOURCE = "MLB_STATS_API"
# The configured injury/availability provider (SportsDataIO MLB). Key-gated;
# events carry this provenance when derived from its InjuredPlayers feed.
SPORTSDATAIO_SOURCE = "SPORTSDATAIO"


class AvailabilityEventV1(BaseModel):
    """Immutable availability-change evidence. Never fabricated."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["AISP_AVAILABILITY_EVENT_V1"] = AVAILABILITY_EVENT_CONTRACT
    event_type: AvailabilityEventType
    source: str
    source_event_key: str
    sport: str = "BASEBALL"
    league: str = "MLB"
    game_id: str | None = None
    source_game_id: str | None = None
    team_source_id: str | None = None
    player_source_id: str | None = None
    player_name: str | None = None
    published_at: datetime | None = None
    description: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class AvailabilityEventInput(BaseModel):
    """Admin-submitted manual availability event (MANUAL_ADMIN provenance)."""

    model_config = ConfigDict(extra="forbid")

    event_type: AvailabilityEventType
    game_id: str | None = None
    source_game_id: str | None = None
    team_source_id: str | None = None
    player_source_id: str | None = None
    player_name: str | None = Field(default=None, max_length=160)
    published_at: datetime | None = None
    description: str = Field(default="", max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AVAILABILITY_EVENT_CONTRACT",
    "MANUAL_ADMIN_SOURCE",
    "MLB_STATS_API_SOURCE",
    "AvailabilityEventInput",
    "AvailabilityEventType",
    "AvailabilityEventV1",
]
