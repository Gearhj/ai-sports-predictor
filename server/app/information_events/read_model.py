"""Projecting what AISP already collects into AISP_INFORMATION_EVENT_V1.

Two classes have a real source today. ``availability_events`` carries
LINEUP_CONFIRMED and PROBABLE_PITCHER_CHANGED rows from the MLB Stats API,
with both a publication time and an ingestion time -- which is exactly the
pair the contract is built around, and the reason those two classes are usable
for lead-lag while the others are not.

The other five classes report their own absence, by name. An empty list with
no explanation reads as "nothing happened tonight"; a class that says
"no collector exists" reads as what it is.

Market state either side of an event comes from the market tape, and only from
frames that were really observed. When there is no frame on one side -- because
the event predates the first quote, or the market went quiet -- that side is
null and the offset is null with it.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.information_events.contract_v1 import (
    CLASSES, CONTRACT_VERSION, InformationEvent, MarketState, describe_contract,
    validate_event,
)
from app.models import AvailabilityEvent
from app.services.market_tape import MarketTapeService

SCHEMA_VERSION = "AISP_INFORMATION_EVENT_READ_MODEL_V1"

#: How a stored availability event maps onto the contract's classes. Only the
#: types AISP actually stores appear here; an unmapped type is reported rather
#: than forced into the nearest class.
EVENT_TYPE_CLASS: dict[str, str] = {
    "LINEUP_CONFIRMED": "LINEUP_ANNOUNCEMENT",
    "PROBABLE_PITCHER_CHANGED": "AVAILABILITY_CHANGE",
    "SCRATCH": "AVAILABILITY_CHANGE",
    "INJURY": "AVAILABILITY_CHANGE",
    "POSTPONED": "OFFICIAL_ANNOUNCEMENT",
}

#: Sources AISP has classified. An unclassified source is UNKNOWN, not assumed.
SOURCE_TIER: dict[str, str] = {
    "MLB_STATS_API": "OFFICIAL",
    "SPORTSDATAIO": "AGGREGATOR",
}

#: What each class would need before it could carry events. Stated per class so
#: an empty section is never mistaken for a quiet night.
CLASS_SOURCE_STATUS: dict[str, dict[str, Any]] = {
    "OFFICIAL_ANNOUNCEMENT": {
        "collected": False,
        "requires": "A team or league announcement feed. AISP stores postponements from the "
                    "MLB Stats API but has no general announcement collector.",
    },
    "AVAILABILITY_CHANGE": {
        "collected": True,
        "requires": None,
        "sources": ["MLB_STATS_API"],
    },
    "LINEUP_ANNOUNCEMENT": {
        "collected": True,
        "requires": None,
        "sources": ["MLB_STATS_API"],
    },
    "TRANSACTION": {
        "collected": False,
        "requires": "A transactions feed (trades, signings, call-ups, options). No collector exists.",
    },
    "REPORTER_UPDATE": {
        "collected": False,
        "requires": "A named-reporter feed with a declared reliability tier. No collector "
                    "exists, and this assignment calls no social platform.",
    },
    "WEATHER_ALERT": {
        "collected": False,
        "requires": "A venue weather feed with publication timestamps. AISP stores weather "
                    "for forecasting inputs but records no alert events.",
    },
    "SOCIAL_VOLUME_ANOMALY": {
        "collected": False,
        "requires": "A social-volume feed and a baseline to call a spike abnormal. Out of "
                    "scope: this assignment builds the boundary and calls nothing.",
    },
}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class InformationEventReadService:
    """Read-only projection. Calls no provider and writes nothing."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.tapes = MarketTapeService(session)

    def contract(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "data_mode": "live",
            **describe_contract(),
            "class_source_status": [
                {"key": key, "description": CLASSES[key], **CLASS_SOURCE_STATUS[key]}
                for key in CLASSES
            ],
        }

    def for_game(
        self, *, game_id: UUID, market_type: str = "MONEYLINE", now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        rows = list(self.session.scalars(
            select(AvailabilityEvent).where(AvailabilityEvent.game_id == game_id)
            .order_by(AvailabilityEvent.published_at, AvailabilityEvent.received_at)
        ))
        tape = self.tapes.tape(game_id=game_id, market_type=market_type, now=now)
        frames = [frame for frame in tape.get("frames") or []
                  if frame["consensus_no_vig_home"] is not None]

        events = [self._project(row, frames) for row in rows]
        # The projection is run through the ingestion validator, so a shape the
        # boundary would refuse can never reach a reader by the back door.
        for event in events:
            validate_event(_as_payload(event))
        unmapped = sorted({str(row.event_type) for row in rows
                           if str(row.event_type) not in EVENT_TYPE_CLASS})
        present = sorted({event.classification for event in events})
        return {
            "schema_version": SCHEMA_VERSION,
            "contract_version": CONTRACT_VERSION,
            "data_mode": "live",
            "generated_at_utc": now.isoformat(),
            "game_id": str(game_id),
            "event": tape["event"],
            "events": [event.as_dict() for event in events],
            "event_count": len(events),
            "classes": [
                {
                    "key": key,
                    "description": CLASSES[key],
                    "events": sum(1 for event in events if event.classification == key),
                    "state": (
                        "COLLECTED" if key in present
                        else "COLLECTED_NONE_TONIGHT" if CLASS_SOURCE_STATUS[key]["collected"]
                        else "NOT_COLLECTED"
                    ),
                    **CLASS_SOURCE_STATUS[key],
                }
                for key in CLASSES
            ],
            "unmapped_stored_types": unmapped,
            "market_reference": {
                "market_type": market_type.upper(),
                "priced_frames": len(frames),
                "first_observed_utc": tape.get("first_observed_utc"),
                "last_observed_utc": tape.get("last_observed_utc"),
            },
            "authority": {
                "affects_forecast": False,
                "creates_or_suppresses_position": False,
                "statement": "Information events are evidence for research. They are not an "
                             "input to any forecast, threshold, decision or position.",
            },
        }

    # -- internals --------------------------------------------------------
    def _project(self, row: AvailabilityEvent, frames: list[dict[str, Any]]) -> InformationEvent:
        published = _aware(row.published_at)
        received = _aware(row.received_at) or _aware(row.created_at) or datetime.now(UTC)
        classification = EVENT_TYPE_CLASS.get(str(row.event_type), "OFFICIAL_ANNOUNCEMENT")
        source = str(row.source or "UNKNOWN")
        # The MLB Stats API's publication time is stamped as the record is read,
        # so it can land a fraction of a second AFTER our ingestion time. That
        # ordering is impossible, and the contract refuses it. Rather than
        # quietly clamping it to zero -- which would assert a lead-lag of zero
        # we did not measure -- the publication time is dropped and the reason
        # is carried, so nothing downstream treats this event as timed.
        clock_note: str | None = None
        if published is not None and published > received:
            clock_note = (
                f"Source publication time is {(published - received).total_seconds():.3f}s "
                "after ingestion, which is impossible. It is stamped at read time by this "
                "source, so it is not a publication instant and is not used for lead-lag."
            )
            published = None
        anchor = published or received
        before, after = self._surrounding(frames, anchor)
        return InformationEvent(
            contract_version=CONTRACT_VERSION,
            event_id=str(row.id),
            classification=classification,
            sport=str(row.sport or ""), league=str(row.league or ""),
            game_id=str(row.game_id) if row.game_id else None,
            team_id=_str_or_none(row.team_source_id),
            player_id=_str_or_none(row.player_source_id),
            player_name=_str_or_none(row.player_name),
            source=source,
            reliability_tier=SOURCE_TIER.get(source, "UNKNOWN"),
            published_at_utc=published.isoformat() if published else None,
            ingested_at_utc=received.isoformat(),
            # AISP stores one row per observation from one source. Claiming
            # corroboration would require a second independent source, which
            # this system does not have for any class.
            corroboration="OFFICIALLY_CONFIRMED" if SOURCE_TIER.get(source) == "OFFICIAL" else "SINGLE_SOURCE",
            headline=str(row.description or str(row.event_type)),
            expected_effect=self._effect(row, classification),
            market_before=before, market_after=after,
            lead_seconds=(int((received - published).total_seconds()) if published else None),
            evidence={
                "stored_event_type": str(row.event_type),
                "stored_schema_version": str(row.schema_version or ""),
                "source_event_key": _str_or_none(row.source_event_key),
                "projection": "Projected from availability_events; not re-collected.",
                **({"clock_note": clock_note} if clock_note else {}),
            },
        )

    @staticmethod
    def _effect(row: AvailabilityEvent, classification: str) -> dict[str, Any] | None:
        """The stated effect, or null. Never a fabricated zero."""

        if classification == "LINEUP_ANNOUNCEMENT":
            return {
                "kind": "AVAILABILITY",
                "direction": "RESOLVES_UNCERTAINTY",
                "magnitude": None,
                "note": "A posted lineup removes uncertainty; its size in probability terms "
                        "is not published by the source and is not estimated here.",
            }
        if classification == "AVAILABILITY_CHANGE":
            return {
                "kind": "AVAILABILITY",
                "direction": "UNKNOWN",
                "magnitude": None,
                "note": "The source states that the probable starter changed. It does not "
                        "state the effect, and none is inferred.",
            }
        return None

    @staticmethod
    def _surrounding(
        frames: list[dict[str, Any]], anchor: datetime,
    ) -> tuple[MarketState | None, MarketState | None]:
        before: dict[str, Any] | None = None
        after: dict[str, Any] | None = None
        for frame in frames:
            at = datetime.fromisoformat(frame["t"])
            if at <= anchor:
                before = frame
            elif after is None:
                after = frame
                break
        return _state(before, anchor), _state(after, anchor)


def _state(frame: dict[str, Any] | None, anchor: datetime) -> MarketState | None:
    if frame is None:
        return None
    at = datetime.fromisoformat(frame["t"])
    return MarketState(
        observed_at_utc=frame["t"],
        consensus_no_vig_home=frame["consensus_no_vig_home"],
        usable_books=frame["usable_books"],
        frame_offset_seconds=int((at - anchor).total_seconds()),
    )


def _as_payload(event: InformationEvent) -> dict[str, Any]:
    payload = event.as_dict()
    for key in ("market_before", "market_after"):
        value = payload.get(key)
        payload[key] = dict(value) if isinstance(value, dict) else value
    return payload


def _str_or_none(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


__all__ = ["CLASS_SOURCE_STATUS", "EVENT_TYPE_CLASS", "InformationEventReadService",
           "SCHEMA_VERSION", "SOURCE_TIER"]
