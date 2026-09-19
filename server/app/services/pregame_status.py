"""Two independent pregame readings for a game card: its market and its model.

WHY
---
A card that says "Awaiting first forecast and quote" while nine fresh
two-sided quotes are stored is merging two different questions. The market
question is whether a same-book pair exists that the decision policy can use
in the decision window. The model question is whether the controlling forecast
has arrived, and if not, when the producer is scheduled to deliver it and what
the producer itself last reported. Each is answered here from its own
evidence, and neither answer borrows from the other.

MARKET
------
The policy's rule, unchanged: a same-book, same-provider moneyline pair whose
OLDER leg is at most ``QUOTE_FRESHNESS_MINUTES`` (240) old at the decision.
So each stored pair is usable until older-leg time + 240 minutes, and the card
can say whether that covers the T-30..T-15 window.

MODEL (MLB V18)
---------------
The producer contract (Model Studio ``mlb_v18_unattended.checkpoint``, frozen
at the 4757da timing repair and carried in every slate receipt as
``consumerReadyTargetUtc``): five-minute input checks from T-60, a
consumer-ready publication TARGET at T-45 -- a target, not a guaranteed
arrival -- and AISP's final-pregame window T-30..T-15. When no forecast is
bound, the producer's latest slate receipt is read (read-only; it is the
producer's own record, not an exchange object) for that game's actual reason.

COLLEGE FOOTBALL
----------------
Two facts that are independent of each other, and neither is the MLB reading:

* Market coverage. A CFB game can have no collector at all: the SportsGameOdds
  CFB lane grants objects only from genuinely uncommitted capacity and only when
  the owner switches it on. With no stored quote, the state says whether any
  collector is active -- ``COLLECTION_INACTIVE_NO_ALLOCATION`` (nothing will
  fetch a price) versus ``NO_QUOTES_YET`` (a collector is active) -- so a card
  never says "waiting for" a price nobody will fetch, nor "stored reference"
  when no quote row exists.
* Forecast eligibility. The paper checkpoint needs the producer's discovery
  lease to be current (``production_import.present_eligibility``). The state
  names the lease -- ``ELIGIBILITY_EXPIRED`` with its expiry time -- and, once
  the checkpoint has run, reads the lease AS THE CHECKPOINT RECORDED IT. A later
  manifest is shown beside that record ("renewed since") and never replaces it.

Nothing here requests a price, evaluates a policy or writes a row.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

QUOTE_FRESHNESS_MINUTES = 240
INPUT_CHECKS_FROM = timedelta(minutes=60)
PUBLICATION_TARGET = timedelta(minutes=45)
WINDOW_OPENS = timedelta(minutes=30)
WINDOW_CLOSES = timedelta(minutes=15)
RECEIPT_SCHEMA = "MLB_V18_DAILY_SLATE_RECEIPT_V1"
RECEIPT_CACHE_SECONDS = 60

#: Producer reason codes in plain words. An unknown code is shown as itself.
REASON_TEXT = {
    "CHECKPOINT_NOT_DUE": "the producer's next input check for this game was not yet due",
    "PREGAME_SOURCE_CHECKPOINT": "the producer was checking this game's inputs",
    "BOUNDED_FAILURE_COOLDOWN": "the producer is waiting to retry after a failed attempt",
    "BOUNDED_FAILURE_RETRY_DUE": "the producer is retrying after a failed attempt",
    "FINAL_PUBLICATION_SAFETY_CUTOFF_T_MINUS_5": "the producer's T-5 publication cutoff has passed",
    "FUTURE_GAME_BEYOND_24H_CAPTURE_WINDOW": "the game is more than 24 hours away",
    "ALREADY_STARTED_OR_CLOSED": "the game has started or closed",
    "POSTPONED_CANCELED_OR_SUSPENDED": "the game is postponed, canceled or suspended",
    "AWAY_CONFIRMED_ORDERED_NINE_PLAYER_LINEUP_UNAVAILABLE": "the away team's confirmed batting order is not available",
    "HOME_CONFIRMED_ORDERED_NINE_PLAYER_LINEUP_UNAVAILABLE": "the home team's confirmed batting order is not available",
    "AWAY_LINEUP_SOURCE_CLOCK_UNAVAILABLE": "the away lineup's source time is not available",
    "HOME_LINEUP_SOURCE_CLOCK_UNAVAILABLE": "the home lineup's source time is not available",
    "AWAY_STARTER_IDENTITY_UNAVAILABLE": "the away starting pitcher is not identified",
    "HOME_STARTER_IDENTITY_UNAVAILABLE": "the home starting pitcher is not identified",
}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(str(value).replace(" ", "T").replace("Z", "+00:00")))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def reason_text(code: str | None) -> str | None:
    if not code:
        return None
    return REASON_TEXT.get(code, code.replace("_", " ").lower())


# -- market ------------------------------------------------------------------

def market_status(moneylines: list[dict[str, Any]], permitted: set[str], *, now: datetime,
                  opens: datetime, closes: datetime) -> dict[str, Any]:
    """Whether a policy-usable same-book pair exists now and through the window.

    ``moneylines`` are coverage quote rows: one per book, both sides present,
    ``observed_at`` already the OLDER leg.
    """

    pairs = []
    for q in moneylines:
        if q.get("provider") not in permitted:
            continue
        older = _parse(q.get("observed_at"))
        if older is None:
            continue
        pairs.append((older, older + timedelta(minutes=QUOTE_FRESHNESS_MINUTES), q))
    fresh_now = [p for p in pairs if 0 <= (now - p[0]).total_seconds() / 60 <= QUOTE_FRESHNESS_MINUTES]
    through_close = [p for p in fresh_now if p[1] >= closes]
    at_open = [p for p in fresh_now if p[1] >= opens]
    latest_until = max((p[1] for p in fresh_now), default=None)
    oldest = min((p[0] for p in fresh_now), default=None)
    newest = max((p[0] for p in fresh_now), default=None)
    if not pairs:
        state = "NO_TWO_SIDED_QUOTE"
    elif not fresh_now:
        state = "STORED_REFERENCE_ONLY"
    elif now >= closes:
        state = "WINDOW_CLOSED"
    elif through_close:
        state = "ELIGIBLE_THROUGH_WINDOW"
    elif at_open:
        state = "EXPIRES_DURING_WINDOW"
    else:
        state = "EXPIRES_BEFORE_WINDOW"
    return {
        "state": state,
        "rule": f"same-book two-sided moneyline, older leg at most {QUOTE_FRESHNESS_MINUTES} minutes old at the decision",
        "books_two_sided": len(pairs),
        "books_fresh_now": len(fresh_now),
        "books_fresh_through_window": len(through_close),
        "oldest_leg_observed_at": _iso(oldest),
        "newest_leg_observed_at": _iso(newest),
        "oldest_leg_age_minutes": round((now - oldest).total_seconds() / 60, 1) if oldest else None,
        "fresh_until": _iso(latest_until),
        "earliest_expiry": _iso(min((p[1] for p in fresh_now), default=None)),
        "window_opens_at": _iso(opens), "window_closes_at": _iso(closes),
    }


# -- model -------------------------------------------------------------------

_cache: dict[str, tuple[float, float, dict[str, Any] | None]] = {}
_lock = threading.Lock()


def _receipts_root() -> Path | None:
    from app.core.config import get_settings

    raw = str(getattr(get_settings(), "mlb_v18_slate_receipts_root", "") or "")
    return Path(raw) if raw else None


def latest_receipt(day: date, root: Path | None = None) -> dict[str, Any] | None:
    """The producer's newest slate receipt for ``day``, or None. Cached briefly."""

    root = root or _receipts_root()
    if root is None:
        return None
    folder = root / day.isoformat() / "sha256"
    key = str(folder)
    try:
        stamp = folder.stat().st_mtime
    except OSError:
        return None
    with _lock:
        hit = _cache.get(key)
        if hit and hit[1] == stamp and time.monotonic() - hit[0] < RECEIPT_CACHE_SECONDS:
            return hit[2]
    best: dict[str, Any] | None = None
    try:
        # Receipt folders are content addresses; newest by mtime first, then the
        # receipt's own generatedAt decides among the few most recent.
        entries = sorted(os.scandir(folder), key=lambda e: e.stat().st_mtime, reverse=True)[:4]
    except OSError:
        entries = []
    for entry in entries:
        try:
            data = json.loads(Path(entry.path, "slate-result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("schemaVersion") != RECEIPT_SCHEMA or data.get("receiptSha256") != entry.name:
            continue
        if best is None or str(data.get("generatedAt")) > str(best.get("generatedAt")):
            best = data
    with _lock:
        _cache[key] = (time.monotonic(), stamp, best)
    return best


def _producer_entry(receipt: dict[str, Any] | None, game_id: str, now: datetime) -> dict[str, Any] | None:
    if not receipt:
        return None
    generated = _parse(receipt.get("generatedAt"))
    entry = next((g for g in receipt.get("games") or [] if str(g.get("gameId")) == game_id), None)
    base = {
        "receipt_sha256": receipt.get("receiptSha256"),
        "receipt_generated_at": _iso(generated),
        "receipt_age_minutes": round((now - generated).total_seconds() / 60, 1) if generated else None,
        "source": "Model Studio V18 daily slate receipt (read-only)",
    }
    if entry is None:
        return {**base, "game_status": "NOT_IN_RECEIPT", "reason": None, "reason_text": None,
                "blockers": [], "blocker_text": []}
    checkpoint = entry.get("checkpoint") or {}
    blockers = [b.get("reason") for b in entry.get("blockers") or [] if b.get("reason")]
    status = entry.get("status") or ("BLOCKED" if blockers else "SKIPPED")
    return {
        **base,
        "game_status": status,
        "reason": checkpoint.get("reason"),
        "reason_text": reason_text(checkpoint.get("reason")),
        "blockers": blockers,
        "blocker_text": [reason_text(b) for b in blockers],
        "checked_as_of": entry.get("availableAsOf") or entry.get("capturedAt"),
        "producer_ready_target": checkpoint.get("consumerReadyTargetUtc"),
        "check_interval_minutes": checkpoint.get("intervalMinutes"),
    }


def model_status(league: str, game_id: str, starts_at: datetime, model: dict[str, Any] | None,
                 *, now: datetime, receipt: dict[str, Any] | None) -> dict[str, Any]:
    start = _aware(starts_at)
    schedule = {
        "input_checks_from": _iso(start - INPUT_CHECKS_FROM),
        "publication_target": _iso(start - PUBLICATION_TARGET),
        "window_opens_at": _iso(start - WINDOW_OPENS),
        "window_closes_at": _iso(start - WINDOW_CLOSES),
        "target_is_guaranteed": False,
    }
    if model is not None:
        # shadow_forecasts.created_at is the START of AISP's import transaction
        # (PostgreSQL now()), not the producer's publication, the insert or the commit.
        return {"state": "PUBLISHED", "engine_id": model.get("engine_id"),
                "import_transaction_started_at": model.get("created_at"),
                "clock_basis": "AISP_IMPORT_TRANSACTION_START",
                "schedule": schedule, "producer": None}
    if league != "MLB":
        # NFL keeps its own final-pregame freeze; no producer receipt is read.
        return {"state": "NOT_PUBLISHED", "schedule": {**schedule, "input_checks_from": None,
                                                        "publication_target": None},
                "producer": None}
    producer = _producer_entry(receipt, game_id, now)
    if producer and producer["game_status"] == "PUBLISHED":
        state = "PUBLISHED_NOT_IMPORTED"
    elif now >= start - WINDOW_CLOSES:
        state = "MISSED"
    elif now >= start - PUBLICATION_TARGET:
        state = "OVERDUE"
    elif producer and producer["blockers"]:
        state = "WAITING_ON_INPUTS"
    else:
        state = "SCHEDULED"
    return {"state": state, "engine_id": "MLB_V18_WHOLE_GAME", "schedule": schedule, "producer": producer}


# -- College Football --------------------------------------------------------

COLLECTION_INACTIVE = "COLLECTION_INACTIVE_NO_ALLOCATION"
NO_QUOTES_YET = "NO_QUOTES_YET"
COLLECTION_STATE_UNKNOWN = "COLLECTION_STATE_UNKNOWN"
ELIGIBILITY_CURRENT = "ELIGIBILITY_CURRENT"
ELIGIBILITY_EXPIRED = "ELIGIBILITY_EXPIRED"
ELIGIBILITY_NOT_CURRENT = "ELIGIBILITY_NOT_CURRENT"
#: The SportsGameOdds CFB lane's own capacity state that grants objects.
CFB_CAPACITY_AVAILABLE = "CAPACITY_AVAILABLE"


def cfb_collection(note: dict[str, Any] | None) -> dict[str, Any]:
    """Whether any CFB price collector is active, from the capacity gate's note.

    Active needs both: the owner's lane switch on AND the gate granting at
    least one object. A switched-off lane is inactive even when the gate cannot
    be read; an unreadable gate on a switched-on lane is unknown, never active.
    """

    note = note or {}
    enabled = note.get("lane_enabled")
    allocation = note.get("reason")
    allowed = int(note.get("objects_allowed") or 0)
    if enabled is False:
        active: bool | None = False
    elif enabled is None or allocation in (None, "PRICE_STATE_UNAVAILABLE"):
        active = None
    else:
        active = allocation == CFB_CAPACITY_AVAILABLE and allowed > 0
    return {"lane": note.get("lane"), "active": active, "lane_enabled": enabled,
            "allocation_state": allocation, "objects_allowed": allowed if active else 0,
            "spent_on_lane": note.get("spent_on_lane"), "explanation": note.get("detail")}


def cfb_market_status(moneylines: list[dict[str, Any]], quotes_stored: int, collection: dict[str, Any],
                      permitted: set[str], *, now: datetime, opens: datetime, closes: datetime) -> dict[str, Any]:
    """A CFB game's market coverage. Stored quotes get the ordinary reading;
    no stored quote gets a state that says whether anything will fetch one."""

    status = market_status(moneylines, permitted, now=now, opens=opens, closes=closes)
    if quotes_stored == 0:
        active = collection.get("active")
        status["state"] = (COLLECTION_STATE_UNKNOWN if active is None
                           else NO_QUOTES_YET if active else COLLECTION_INACTIVE)
    return {**status, "quotes_stored": quotes_stored, "collection": collection}


def _lease_reading(reading: dict[str, Any] | None) -> str | None:
    return (reading or {}).get("state")


def _eligibility_state(lease: str | None) -> str:
    if lease == "CURRENT":
        return ELIGIBILITY_CURRENT
    if lease == "EXPIRED_LEASE":
        return ELIGIBILITY_EXPIRED
    return ELIGIBILITY_NOT_CURRENT


def cfb_model_status(model: dict[str, Any] | None, *, eligibility_now: dict[str, Any] | None,
                     checkpoint: dict[str, Any] | None, starts_at: datetime,
                     engine_id: str = "CFB_GEN1_JOINT_BASELINE") -> dict[str, Any]:
    """A CFB forecast's decision eligibility: the producer's discovery lease.

    ``eligibility_now`` is the newest verified manifest's reading of the
    forecast (``state``: CURRENT / EXPIRED_LEASE / HISTORICAL / REJECTED /
    NOT_IN_CURRENT_DISCOVERY / NO_VERIFIED_DISCOVERY, ``lease_valid_until``,
    ``manifest_created_utc``). ``checkpoint`` is the recorded FINAL_PREGAME
    decision, with the lease its evaluation recorded (``discovery``). Once a
    checkpoint recorded a lease, that recorded lease is the state; the present
    reading is reported beside it and never rewrites it.
    """

    start = _aware(starts_at)
    schedule = {"input_checks_from": None, "publication_target": None,
                "window_opens_at": _iso(start - WINDOW_OPENS), "window_closes_at": _iso(start - WINDOW_CLOSES),
                "target_is_guaranteed": False}
    base = {"engine_id": engine_id, "schedule": schedule, "producer": None,
            "eligibility_now": eligibility_now, "checkpoint": checkpoint}
    if model is None:
        return {**base, "state": "NOT_PUBLISHED", "basis": None, "eligibility": None}
    recorded = (checkpoint or {}).get("discovery")
    basis, reading = ("RECORDED_AT_CHECKPOINT", recorded) if recorded else ("PRESENT_DISCOVERY", eligibility_now)
    renewed = bool(recorded and _lease_reading(recorded) != "CURRENT"
                   and _lease_reading(eligibility_now) == "CURRENT")
    return {**base, "state": _eligibility_state(_lease_reading(reading)), "basis": basis,
            "forecast_id": model.get("forecast_id"), "eligibility": reading,
            "renewed_since_checkpoint": renewed}


__all__ = ["COLLECTION_INACTIVE", "COLLECTION_STATE_UNKNOWN", "ELIGIBILITY_CURRENT", "ELIGIBILITY_EXPIRED",
           "ELIGIBILITY_NOT_CURRENT", "NO_QUOTES_YET", "QUOTE_FRESHNESS_MINUTES", "cfb_collection",
           "cfb_market_status", "cfb_model_status", "latest_receipt", "market_status", "model_status",
           "reason_text"]
