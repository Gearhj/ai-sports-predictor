"""AISP_INFORMATION_EVENT_V1 — what an information event must carry to be accepted.

Every field below exists because leaving it out makes the event unusable, not
because it was easy to collect:

``published_at_utc`` vs ``ingested_at_utc``
    Two different facts. The first is when the world learned; the second is
    when we did. Their difference is the only thing that can say whether a
    market moved before or after an announcement, which is the question the
    whole idea rests on. A source that cannot supply the publication time is
    accepted with it null and its events are explicitly not usable for lead-lag.

``reliability_tier``
    Two sources will contradict each other. Without a declared tier there is no
    principled way to prefer one, and "the newest one" is not a principle.

``corroboration``
    One report is a report. Two independent reports is evidence. A single
    unconfirmed post must never be presented as the same thing as an official
    team announcement, so the state is carried explicitly rather than inferred
    from how many rows happen to exist.

``market_before`` / ``market_after``
    An event that the market had already priced is not news. These are filled
    from the market tape at ingestion time -- from real observed frames on
    either side of the publication instant, never interpolated.

``expected_effect``
    Direction and magnitude if known, and null if not. Null is a legitimate
    answer; a fabricated zero is not.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

CONTRACT_VERSION = "AISP_INFORMATION_EVENT_V1"

#: The seven classes AISP will accept. A class with no source is still listed,
#: so a reader can see the difference between "nothing happened" and "we do not
#: collect this".
CLASSES: dict[str, str] = {
    "OFFICIAL_ANNOUNCEMENT": "An announcement published by the team or the league itself.",
    "AVAILABILITY_CHANGE": "An injury, illness, suspension or other change to who can play.",
    "LINEUP_ANNOUNCEMENT": "A posted lineup, inactive list or confirmed starter.",
    "TRANSACTION": "A trade, signing, call-up, option or release.",
    "REPORTER_UPDATE": "A report from a named journalist, not the team or league.",
    "WEATHER_ALERT": "A forecast or condition change for the venue.",
    "SOCIAL_VOLUME_ANOMALY": "An abnormal spike in social volume around one side of a game.",
}

#: Ordered best to worst. The order is the point: it is what lets two sources
#: that disagree be resolved without appealing to recency.
RELIABILITY_TIERS: tuple[str, ...] = (
    "OFFICIAL",          # the team, the league, or its own data API
    "VERIFIED_PRIMARY",  # a named reporter with a public accuracy record
    "AGGREGATOR",        # a service that republishes others' reports
    "CROWD",             # unattributed or user-submitted
    "UNKNOWN",           # a source AISP has not classified
)

CORROBORATION_STATES: tuple[str, ...] = (
    "SINGLE_SOURCE",
    "CORROBORATED",       # a second, independent source at the same or better tier
    "CONTRADICTED",       # a source of equal or better tier says otherwise
    "SUPERSEDED",         # a later event from the same source replaces this one
    "OFFICIALLY_CONFIRMED",
)

EFFECT_KINDS: tuple[str, ...] = ("AVAILABILITY", "WORKLOAD", "ROLE", "CONDITIONS", "NONE_STATED")


class InformationEventError(ValueError):
    """The event does not satisfy AISP_INFORMATION_EVENT_V1."""


@dataclass(frozen=True)
class MarketState:
    """The market on one side of the publication instant.

    ``observed_at_utc`` is the instant of a REAL frame, and ``frame_offset_seconds``
    says how far that frame was from the event. A caller that cannot find a
    frame on one side reports ``None`` for that side; it never moves a frame to
    make the gap look smaller.
    """

    observed_at_utc: str | None
    consensus_no_vig_home: float | None
    usable_books: int
    frame_offset_seconds: int | None


@dataclass(frozen=True)
class InformationEvent:
    contract_version: str
    event_id: str
    classification: str
    sport: str
    league: str
    game_id: str | None
    team_id: str | None
    player_id: str | None
    player_name: str | None
    source: str
    reliability_tier: str
    published_at_utc: str | None
    ingested_at_utc: str
    corroboration: str
    headline: str
    expected_effect: dict[str, Any] | None
    market_before: MarketState | None
    market_after: MarketState | None
    #: Present only when both timestamps exist. Null is not zero.
    lead_seconds: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_event(payload: dict[str, Any]) -> InformationEvent:
    """Accept an event, or refuse it with the reason.

    This is the ingestion boundary. Nothing calls it yet -- no collector for
    these classes exists in this assignment -- but it is what a collector will
    have to satisfy, and it is tested, so the shape cannot drift into existence
    by accident.
    """

    def need(key: str) -> Any:
        if key not in payload or payload[key] in (None, ""):
            raise InformationEventError(f"information event is missing required field {key!r}")
        return payload[key]

    if payload.get("contract_version") != CONTRACT_VERSION:
        raise InformationEventError("unsupported information-event contract version")
    classification = str(need("classification"))
    if classification not in CLASSES:
        raise InformationEventError(f"unsupported classification {classification!r}")
    tier = str(need("reliability_tier"))
    if tier not in RELIABILITY_TIERS:
        raise InformationEventError(f"unsupported reliability tier {tier!r}")
    corroboration = str(need("corroboration"))
    if corroboration not in CORROBORATION_STATES:
        raise InformationEventError(f"unsupported corroboration state {corroboration!r}")

    ingested = _timestamp(need("ingested_at_utc"), label="ingested_at_utc")
    published_raw = payload.get("published_at_utc")
    published = _timestamp(published_raw, label="published_at_utc") if published_raw else None
    if published is not None and published > ingested:
        # Ingesting before publication is impossible; it means one of the two
        # clocks is wrong, and a wrong clock is exactly what breaks lead-lag.
        raise InformationEventError("published_at_utc is after ingested_at_utc")

    effect = payload.get("expected_effect")
    if effect is not None:
        kind = str(effect.get("kind", ""))
        if kind not in EFFECT_KINDS:
            raise InformationEventError(f"unsupported expected effect kind {kind!r}")

    return InformationEvent(
        contract_version=CONTRACT_VERSION,
        event_id=str(need("event_id")),
        classification=classification,
        sport=str(need("sport")), league=str(need("league")),
        game_id=_optional_str(payload.get("game_id")),
        team_id=_optional_str(payload.get("team_id")),
        player_id=_optional_str(payload.get("player_id")),
        player_name=_optional_str(payload.get("player_name")),
        source=str(need("source")), reliability_tier=tier,
        published_at_utc=published.isoformat() if published else None,
        ingested_at_utc=ingested.isoformat(),
        corroboration=corroboration,
        headline=str(need("headline")),
        expected_effect=effect,
        market_before=_market(payload.get("market_before")),
        market_after=_market(payload.get("market_after")),
        lead_seconds=(int((ingested - published).total_seconds()) if published else None),
        evidence=dict(payload.get("evidence") or {}),
    )


def describe_contract() -> dict[str, Any]:
    """The contract itself, so a producer can read it without reading this file."""

    return {
        "contract_version": CONTRACT_VERSION,
        "classes": [{"key": key, "description": description} for key, description in CLASSES.items()],
        "reliability_tiers": [
            {"tier": tier, "rank": index}
            for index, tier in enumerate(RELIABILITY_TIERS)
        ],
        "corroboration_states": list(CORROBORATION_STATES),
        "expected_effect_kinds": list(EFFECT_KINDS),
        "required_fields": [
            "contract_version", "event_id", "classification", "sport", "league",
            "source", "reliability_tier", "ingested_at_utc", "corroboration", "headline",
        ],
        "nullable_fields": [
            "published_at_utc", "game_id", "team_id", "player_id", "player_name",
            "expected_effect", "market_before", "market_after",
        ],
        "rules": [
            "published_at_utc and ingested_at_utc are different facts; the gap between them "
            "is the lead-lag signal and neither may be substituted for the other.",
            "published_at_utc may not be after ingested_at_utc.",
            "market_before and market_after must reference real observed frames. A missing "
            "frame is reported as null, never interpolated.",
            "An unknown expected effect is null. It is never zero.",
            "Nothing in this contract grants an event any authority over a forecast, a "
            "threshold, a decision or a position.",
        ],
        "collection_status": "No collector exists for any class in this assignment. Two "
                             "classes are projected from evidence AISP already stores; the "
                             "rest report their absence.",
    }


def _timestamp(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise InformationEventError(f"{label} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise InformationEventError(f"{label} must carry a timezone")
    return parsed.astimezone(UTC)


def _optional_str(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _market(payload: Any) -> MarketState | None:
    if not isinstance(payload, dict):
        return None
    return MarketState(
        observed_at_utc=_optional_str(payload.get("observed_at_utc")),
        consensus_no_vig_home=(
            None if payload.get("consensus_no_vig_home") is None
            else float(payload["consensus_no_vig_home"])
        ),
        usable_books=int(payload.get("usable_books") or 0),
        frame_offset_seconds=(
            None if payload.get("frame_offset_seconds") is None
            else int(payload["frame_offset_seconds"])
        ),
    )


__all__ = [
    "CLASSES", "CONTRACT_VERSION", "CORROBORATION_STATES", "EFFECT_KINDS",
    "InformationEvent", "InformationEventError", "MarketState", "RELIABILITY_TIERS",
    "describe_contract", "validate_event",
]
