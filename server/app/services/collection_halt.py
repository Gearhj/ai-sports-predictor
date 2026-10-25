"""Latch a collection lane shut when the provider answers a different question.

THE FAULT THIS EXISTS FOR
-------------------------
On 2026-09-14 three MLB requests each selected SD at COL, each charged an
object, each wrote fifty rows, and each returned CWS at CLE. Every receipt said
``FALLBACK_COLLECTED``. Nothing was down, nothing errored, and the games that
needed a price still had none two and a half hours later.

The request now carries the selection, so the two should agree. If they ever do
not, the safe reading is not "retry and see" -- a retry spends another object to
ask a question the provider has just demonstrated it will not answer. So the
first mismatch stops the lane, and the response that caused it is kept whole.

WHAT A HALT DOES AND DOES NOT DO
--------------------------------
It stops this lane from spending further objects. It does not roll back the
rows already written -- they are real prices from a real response, and deleting
them would destroy the evidence -- and it does not touch any other lane, so a
halted MLB lane leaves the NFL reservation exactly where it was.

Nothing here lifts a halt. That is a judgement about whether the provider can
be trusted to answer the question that was asked, and it belongs to the project
owner.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CollectionLaneHalt

#: The provider returned at least one game the request did not select.
SELECTION_MISMATCH = "PROVIDER_RETURNED_UNSELECTED_GAME"

#: Objects were CHARGED and not one selected game was stored. Kept apart from
#: SELECTION_MISMATCH on purpose: "it returned someone else's game" and "it
#: returned nothing I can use" are different faults with different owners. The
#: first is a provider substituting an event; the second can equally be a
#: binding failure on this side, and a reader must not have to guess which.
PAID_STORED_NOTHING = "PAID_RESPONSE_STORED_NONE_OF_SELECTION"


def active_halt(session: Session, *, lane: str) -> CollectionLaneHalt | None:
    """The live halt on this lane, if any."""

    return session.scalar(
        select(CollectionLaneHalt).where(
            CollectionLaneHalt.lane == lane,
            CollectionLaneHalt.active.is_(True),
        )
    )


def halt_lane(
    session: Session,
    *,
    lane: str,
    reason: str,
    evidence: dict[str, Any],
    detail: str | None = None,
    now: datetime | None = None,
) -> CollectionLaneHalt:
    """Latch the lane. A second call while a halt is live keeps the first one.

    The first halt is the one that carries the evidence closest to the fault, so
    a later mismatch must not overwrite it.
    """

    existing = active_halt(session, lane=lane)
    if existing is not None:
        return existing
    halt = CollectionLaneHalt(
        lane=lane, reason=reason, active=True,
        halted_at=now or datetime.now(UTC),
        evidence=evidence, detail=detail,
    )
    session.add(halt)
    session.flush()
    return halt


def mismatch(selected: list[str], ingested: list[str]) -> dict[str, Any] | None:
    """What the provider returned that the request did not ask for, if anything.

    A request that selected three games and was answered with two of them is not
    a mismatch: a provider may have fewer events than the limit allowed, and a
    short answer costs nothing that was not already authorised. A game that was
    NEVER selected is the fault -- that is an object spent on a question nobody
    asked.
    """

    unselected = sorted(set(ingested) - set(selected))
    if not unselected:
        return None
    return {
        "unselected_games_returned": unselected,
        "games_selected": sorted(selected),
        "games_ingested": sorted(ingested),
        "selected_games_missing": sorted(set(selected) - set(ingested)),
    }


def paid_and_stored_nothing(
    selected: list[str], ingested: list[str], *, objects_charged: int,
) -> dict[str, Any] | None:
    """Did this purchase pay for objects and store none of what it asked for?

    THE GAP THIS CLOSES
    -------------------
    ``mismatch`` asks "did the provider return a game I did not select?" -- and
    it can only see games that BOUND. On 2026-09-20 the NFL requests carried a
    mislabelled window, were answered with games already in progress, and bound
    nothing at all. With an empty ``ingested`` there was no unselected game to
    find, so the divergence was ``None``, the lane did not latch, and the same
    wrong request was paid for twice more.

    So this asks the other half of the question: "did I pay, and receive nothing
    I asked for?" It is the paid-for-nothing case, and one occurrence is enough
    -- a retry spends a second object on a request that has just demonstrated it
    buys nothing.

    A ZERO-OBJECT RESPONSE IS NOT THIS FAULT. The provider bills per event
    object returned, so an answer carrying no events costs nothing; that is a
    legitimate no-op (a window with no events in it, a finalized slate) and the
    next tick may ask again for free. Only a CHARGED response that stored none
    of the selection latches the lane, which is why ``objects_charged`` is a
    required argument and not an optional hint.
    """

    if int(objects_charged or 0) <= 0:
        return None  # nothing was billed; nothing to protect against
    stored = sorted(set(selected) & set(ingested))
    if stored:
        return None  # at least one selected game was actually bought
    return {
        "reason": PAID_STORED_NOTHING,
        "objects_charged": int(objects_charged),
        "games_selected": sorted(selected),
        "games_ingested": sorted(ingested),
        "selected_games_missing": sorted(selected),
        "detail": ("Objects were charged and not one of the selected games was "
                   "stored. Either the provider answered with something else or "
                   "nothing in the response could be bound; both mean this "
                   "request bought nothing it asked for."),
    }


__all__ = ["PAID_STORED_NOTHING", "SELECTION_MISMATCH", "active_halt",
           "halt_lane", "mismatch", "paid_and_stored_nothing"]
