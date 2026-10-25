"""Read SportsGameOdds' own account usage, and record it as evidence.

WHY ASK THE PROVIDER
--------------------
AISP's ledger knows what AISP spent. It cannot know the account's entitlement,
cannot know whether the window rolls or resets on a date, and cannot see a spend
made from anywhere else. Planning a month against a guess at those is how a
balance runs out two weeks early -- which is exactly what the previous local
model did, by assuming a rolling thirty-day window when the account bills by
calendar month.

WHAT IT COSTS
-------------
Nothing billable. The endpoint returns account data, not event objects, so the
monthly entity counter does not move. It is still written to the provider
ledger, at zero objects, because a balance AISP acts on should be auditable
alongside the spends it governs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models import ProviderRequestLedger
from app.services.provider_allocation import PROVIDER, USAGE_ENDPOINT_KEY

USAGE_PATH = "/account/usage"

# James's bounded current-credential recovery authority. The dated recovery
# release may read account usage until this instant; another scope requires a
# reviewed successor. A closed event-price lane alone never stopped these reads.
ACCOUNT_USAGE_AUTHORIZED_UNTIL = datetime(2026, 10, 6, 14, 45, tzinfo=UTC)


class UsageUnavailableError(RuntimeError):
    """The provider could not be asked, and the reason says which way."""


def _require_account_usage_authority(moment: datetime) -> float:
    """Refuse new account HTTP work at the actual bounded authority deadline."""
    if moment.tzinfo is None or moment >= ACCOUNT_USAGE_AUTHORIZED_UNTIL:
        raise UsageUnavailableError(
            "SGO_ACCOUNT_USAGE_AUTHORITY_EXPIRED: 2026-10-06T14:45:00Z")
    return (ACCOUNT_USAGE_AUTHORIZED_UNTIL - moment).total_seconds()


def parse_usage(payload: dict[str, Any]) -> dict[str, Any]:
    """The two numbers that matter, plus the limits they sit inside.

    Reads the monthly ENTITY cap, because entities -- event objects -- are what
    this account is actually rationed on. Requests are effectively unlimited on
    this tier, so a plan built on request counts would be planning against the
    wrong resource entirely.
    """

    data = dict(payload.get("data") or {})
    limits = dict(data.get("rateLimits") or {})
    monthly = dict(limits.get("per-month") or {})
    daily = dict(limits.get("per-day") or {})

    cap = monthly.get("max-entities")
    used = monthly.get("current-entities")
    if not isinstance(cap, int) or not isinstance(used, int):
        raise UsageUnavailableError(
            "the usage response carries no numeric monthly entity cap")

    return {
        "monthly_cap": cap,
        "used_this_month": used,
        "remaining": max(0, cap - used),
        "tier": data.get("tier"),
        "active": bool(data.get("isActive", True)),
        "daily_entities_used": daily.get("current-entities"),
        "daily_entity_cap": daily.get("max-entities"),
        "requests_per_minute_cap": (limits.get("per-minute") or {}).get("max-requests"),
        # The per-day counter tracks the UTC calendar day exactly, which is the
        # evidence that the month is a calendar month too.
        "window_basis": "UTC_CALENDAR",
    }


def fetch_usage(settings: Any) -> dict[str, Any]:
    """One request to the account endpoint. No retry, no pagination."""

    remaining = _require_account_usage_authority(datetime.now(UTC))
    base = (settings.sportsgameodds_base_url or "").rstrip("/")
    key = (settings.sportsgameodds_api_key or "").strip()
    if not base or not key:
        raise UsageUnavailableError("SportsGameOdds is not configured")
    with httpx.Client(timeout=min(25.0, remaining), follow_redirects=False) as client:
        _require_account_usage_authority(datetime.now(UTC))
        response = client.get(
            f"{base}{USAGE_PATH}",
            headers={"X-Api-Key": key, "Accept": "application/json"},
        )
    if response.status_code != 200:
        raise UsageUnavailableError(
            f"account usage returned HTTP {response.status_code}")
    return response.json()


def refresh_usage(
    session: Session, settings: Any, *, now: datetime | None = None,
    fetch: Any = None,
) -> dict[str, Any]:
    """Read the balance and record it. Returns what was read, or why it was not."""

    moment = now or datetime.now(UTC)
    try:
        # Both clocks must qualify. Backdated report timestamps cannot extend
        # actual credential use, and a future/expired test or caller clock fails.
        _require_account_usage_authority(datetime.now(UTC))
        _require_account_usage_authority(moment)
        payload = (fetch or fetch_usage)(settings)
        usage = parse_usage(payload)
    except Exception as error:
        return {"records": 0, "read": False,
                "reason": f"{type(error).__name__}: {error}"}

    fingerprint = sha256(
        f"{PROVIDER}:{USAGE_ENDPOINT_KEY}:{moment.isoformat()}".encode()).hexdigest()
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=USAGE_ENDPOINT_KEY, requested_at=moment,
        status="RESERVED", request_fingerprint=fingerprint,
        # Zero: the endpoint returns account data, not event objects.
        quota_cost=0, controlled_live_request=True, source_mode="LIVE",
    )
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=USAGE_ENDPOINT_KEY, requested_at=moment,
        status="SUCCEEDED", request_fingerprint=fingerprint,
        completed_at=moment, reservation_id=reservation.id,
        records_received=0, quota_cost=0,
        quota_remaining=usage["remaining"],
        controlled_live_request=True, source_mode="LIVE",
        response_metadata={
            "usage": usage,
            "endpoint": USAGE_PATH,
            "billable_unit": "none; account data, not event objects",
            "credential": "sent in header; never recorded",
        },
    ))
    session.commit()
    return {"records": 1, "read": True, **usage}


__all__ = [
    "USAGE_PATH",
    "UsageUnavailableError",
    "fetch_usage",
    "parse_usage",
    "refresh_usage",
]
