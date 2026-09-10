"""The information-event boundary: a contract, and an honest empty state.

WHAT THIS PACKAGE IS FOR
------------------------
An RFF index that cannot see WHY a market moved is guessing at half the
question. The other half is information: a scratch, a lineup card, a trade, a
weather alert, a reporter posting forty minutes before the book moves. This
package defines the shape such an event must have before AISP will accept it,
and projects the evidence AISP already collects into that shape.

WHAT IT DOES NOT DO, AND WILL NOT DO HERE
-----------------------------------------
It calls no social platform, scrapes nothing, and adds no collector. Five of
the seven event classes have no source in this system, and this package says
so per class rather than leaving a reader to infer that an empty list means a
quiet day. The two classes that DO have a source -- lineup confirmations and
probable-pitcher changes from the MLB Stats API -- are projected from the
``availability_events`` rows that already exist.

WHY A CONTRACT FIRST
--------------------
Because the fields that make an information event useful are exactly the ones
a scraper is most likely to drop. When the announcement was PUBLISHED is a
different fact from when we INGESTED it, and the gap between them is the whole
signal; a source without a reliability tier cannot be weighed against a
contradicting one; and an event with no market state either side of it cannot
say whether the market already knew.
"""

from app.information_events.contract_v1 import (
    CLASSES, CONTRACT_VERSION, CORROBORATION_STATES, RELIABILITY_TIERS,
    InformationEvent, describe_contract, validate_event,
)
from app.information_events.read_model import InformationEventReadService

__all__ = [
    "CLASSES", "CONTRACT_VERSION", "CORROBORATION_STATES", "InformationEvent",
    "InformationEventReadService", "RELIABILITY_TIERS", "describe_contract",
    "validate_event",
]
