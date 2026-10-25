"""Does a buy window already hold a request that counts? (CFB and NHL Odds API price lanes.)

WHY
---
Both lanes buy ONE request per kickoff cluster, inside a T-75..T-35 window reached
by a 10-minute tick. Until 2026-10-17 a cluster counted as bought as soon as ANY
reservation existed since its window opened -- including one whose request then
FAILED (a timeout, a 5xx, an exception while binding). A failed request therefore
cost the cluster its price outright: no retry was owed, so no decision could be made
for any game in it. The weekend audit (2026-10-16) named this for both lanes.

WHAT COUNTS NOW
---------------
The provider ledger is append-only: a RESERVED row is written and committed before
the call, then ONE terminal row (SUCCEEDED or FAILED, ``reservation_id`` = the
reservation) after it. A reservation since the window opened BLOCKS a new request when

  * its terminal row is SUCCEEDED -- the cluster is bought; or
  * it has no terminal row yet and is younger than ``IN_FLIGHT_GRACE`` -- a request
    is in flight (the scheduled tick and an admin-path run must not both buy).

A FAILED reservation does not block, and neither does one left without a terminal
row for longer than ``IN_FLIGHT_GRACE`` (the process died mid-request). Either is
retried on a later tick inside the same window, but only while the window holds
fewer than ``MAX_ATTEMPTS_PER_WINDOW`` reservations: a provider that keeps failing
costs a cluster at most one extra request, so it cannot drain the lane's daily cap
before the later clusters of the day. Every existing guard still applies to the
retry itself (lane monthly and daily credits, the shared daily request budget, the
provider floor): the retry is just another reservation through the same path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProviderRequestLedger

#: No Odds API call takes this long; a RESERVED row older than this with no terminal
#: row is an interrupted request, not one in flight.
IN_FLIGHT_GRACE = timedelta(minutes=15)

#: Reservations a single buy window may hold: the first request and one retry.
MAX_ATTEMPTS_PER_WINDOW = 2

ALREADY_BOUGHT = "SUCCEEDED"
IN_FLIGHT = "IN_FLIGHT"
RETRY_LIMIT = "RETRY_LIMIT_REACHED"


def window_requests(session: Session, *, provider: str, endpoint_key: str, opened: datetime,
                    now: datetime, event_complete: bool | None = None) -> dict[str, Any]:
    """Classify every reservation for ``endpoint_key`` since ``opened``.

    Returns ``{"blocking": None | ALREADY_BOUGHT | IN_FLIGHT | RETRY_LIMIT, "attempts", "succeeded",
    "in_flight", "failed", "interrupted"}``. ``blocking`` is None when a request is owed now
    (none yet, or only failed/interrupted ones and fewer than ``MAX_ATTEMPTS_PER_WINDOW``).
    """

    reservations = session.execute(
        select(ProviderRequestLedger.id, ProviderRequestLedger.requested_at).where(
            ProviderRequestLedger.provider == provider,
            ProviderRequestLedger.endpoint_key == endpoint_key,
            ProviderRequestLedger.status == "RESERVED",
            ProviderRequestLedger.requested_at >= opened,
            ProviderRequestLedger.requested_at <= now,
        )).all()
    summary: dict[str, Any] = {"blocking": None, "attempts": len(reservations), "succeeded": 0,
                               "in_flight": 0, "failed": 0, "interrupted": 0, "event_complete": event_complete}
    if not reservations:
        return summary
    terminal = {
        reservation_id: status for reservation_id, status in session.execute(
            select(ProviderRequestLedger.reservation_id, ProviderRequestLedger.status).where(
                ProviderRequestLedger.reservation_id.in_([row.id for row in reservations])))
    }
    for reservation_id, requested_at in reservations:
        status = terminal.get(reservation_id)
        if status == "SUCCEEDED":
            summary["succeeded"] += 1
        elif status == "FAILED":
            summary["failed"] += 1
        elif _aware(now) - _aware(requested_at) < IN_FLIGHT_GRACE:
            summary["in_flight"] += 1
        else:
            summary["interrupted"] += 1
    # None preserves every existing caller. An explicitly uncovered NBA event
    # may use the existing second attempt; success for another event is not coverage.
    if summary["succeeded"] and event_complete is not False:
        summary["blocking"] = ALREADY_BOUGHT
    elif summary["in_flight"]:
        summary["blocking"] = IN_FLIGHT
    elif summary["attempts"] >= MAX_ATTEMPTS_PER_WINDOW:
        summary["blocking"] = RETRY_LIMIT
    return summary


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


__all__ = ["ALREADY_BOUGHT", "IN_FLIGHT", "IN_FLIGHT_GRACE", "MAX_ATTEMPTS_PER_WINDOW", "RETRY_LIMIT",
           "window_requests"]
