"""Prospective live shadow pilot: availability-event-triggered rescoring.

Runs the existing AISP current-forecast pipeline prospectively for a live slate
and rescopes a single affected game when an availability event arrives. Every
decision is virtual and non-executable; no odds, injury, or lineup is ever
fabricated. The sole external dependency for real availability signals is the
already-configured public MLB Stats API; a clearly labeled manual admin adapter
exists for event types MLB does not push and for testing.
"""

from app.prospective.contracts import (
    AVAILABILITY_EVENT_CONTRACT,
    AvailabilityEventInput,
    AvailabilityEventType,
    AvailabilityEventV1,
)

__all__ = [
    "AVAILABILITY_EVENT_CONTRACT",
    "AvailabilityEventInput",
    "AvailabilityEventType",
    "AvailabilityEventV1",
]
