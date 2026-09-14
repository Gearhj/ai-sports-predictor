"""Observed scoreboard state: collection, storage and honest freshness.

Display-only by construction. Nothing in this package creates a decision, opens
a position, settles anything or writes a governed final; the settlement owner
and the governed final ingest are untouched and remain the only things that can
say a wager is over.
"""

from app.scoreboard.store import record_observation, record_many

__all__ = ["record_observation", "record_many"]
