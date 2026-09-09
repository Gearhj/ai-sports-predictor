"""When, if ever, the SportsGameOdds fallback may serve a mandatory MLB quote.

WHY THIS IS A SEPARATE GATE
---------------------------
The adapter already refuses to produce a row without a verified mapping. That
protects against *wrong data*. This module protects against something else: a
research fallback quietly becoming the way prices are obtained.

A fallback that can be widened by editing a setting is a fallback that will be
widened. So the narrowing is in code, not configuration:

* MLB only, and only the moneyline. NFL, spreads and totals are never eligible,
  whatever the flags say -- they are optional research and a fallback exists for
  the one market a governed decision can price.
* It expires. The fallback is authorized for September 2026 while the primary
  provider's credits are exhausted; from 1 October The Odds API is primary again
  by construction, because this gate stops answering yes. Nobody has to remember.
* It is bounded by the free allowance, counted from the provider ledger, with
  the same fail-closed posture as the primary: an unknown balance is a refusal.

WHAT IT NEVER CHANGES
---------------------
Provenance. A row obtained this way is SPORTSGAMEODDS, its bookmakers live in
the ``sgo:`` namespace, and it can never pair with an Odds API row to form a
"same-book two-sided quote" because the two providers never share a Sportsbook
row. The governed pricing path filters on source independently of this gate.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.data_sources.provider_registry import SPORTSGAMEODDS
from app.models import ProviderRequestLedger

#: The only league and market a fallback may ever serve. Not configurable.
FALLBACK_LEAGUE = "MLB"
FALLBACK_MARKETS: tuple[str, ...] = ("h2h",)

#: The free allowance this validation was authorized against.
FREE_TIER_OBJECT_ALLOWANCE = 2500

REASON_DISABLED = "FALLBACK_DISABLED"
REASON_UNMAPPED = "FALLBACK_MAPPING_UNVERIFIED"
REASON_EXPIRED = "FALLBACK_WINDOW_CLOSED"
REASON_WRONG_LEAGUE = "FALLBACK_LEAGUE_NOT_ELIGIBLE"
REASON_WRONG_MARKET = "FALLBACK_MARKET_NOT_ELIGIBLE"
REASON_OBJECTS_EXHAUSTED = "FALLBACK_OBJECT_ALLOWANCE_EXHAUSTED"
REASON_ELIGIBLE = "FALLBACK_ELIGIBLE"

EXPLANATIONS = {
    REASON_DISABLED: "The fallback flag is off. No request is made and no row is produced.",
    REASON_UNMAPPED: "No field-mapping manifest bound to a real observed response exists, "
                     "so any row produced would rest on a guessed mapping.",
    REASON_EXPIRED: "The fallback was authorized only while the primary provider's credits "
                    "were exhausted. The Odds API is primary again.",
    REASON_WRONG_LEAGUE: "A fallback exists for mandatory MLB moneylines only. Other leagues "
                         "are optional research and wait for the primary provider.",
    REASON_WRONG_MARKET: "A fallback exists for the moneyline only -- the one market the "
                         "governing policy can price. Spreads and totals wait.",
    REASON_OBJECTS_EXHAUSTED: "The free-tier object allowance for this month is spent or "
                              "cannot be confirmed. No paid plan is authorized.",
    REASON_ELIGIBLE: "Eligible: MLB moneyline, inside the authorized window, mapping "
                     "verified, allowance remaining.",
}


@dataclass(frozen=True)
class FallbackDecision:
    eligible: bool
    reason: str
    detail: str
    objects_used: int
    objects_remaining: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "detail": self.detail,
            "provider": SPORTSGAMEODDS,
            "league": FALLBACK_LEAGUE,
            "markets": list(FALLBACK_MARKETS),
            "objects_used": self.objects_used,
            "objects_remaining": self.objects_remaining,
            "object_allowance": FREE_TIER_OBJECT_ALLOWANCE,
        }


def objects_used(session: Session, *, since: date | None = None) -> int:
    """Objects consumed on this provider, counted from our own ledger.

    ``records_received`` is what the provider actually returned, which is the
    unit the free tier is priced in. A row with no count is treated as unknown
    and charged the worst case, because undercounting an allowance is how an
    allowance gets exceeded.
    """
    query = select(
        func.coalesce(func.sum(ProviderRequestLedger.records_received), 0),
        func.count(),
        func.count(ProviderRequestLedger.records_received),
    ).where(
        ProviderRequestLedger.provider == SPORTSGAMEODDS,
        ProviderRequestLedger.status.in_(("SUCCEEDED", "FAILED")),
    )
    if since is not None:
        query = query.where(ProviderRequestLedger.requested_at >= datetime(
            since.year, since.month, since.day, tzinfo=UTC))
    counted, rows, with_counts = session.execute(query).one()
    unknown = int(rows) - int(with_counts)
    # An uncounted request is charged the whole per-request cap rather than zero.
    return int(counted) + unknown * 100


def fallback_decision(
    session: Session,
    settings: Settings,
    *,
    league_code: str,
    markets: Sequence[str],
    now: datetime | None = None,
) -> FallbackDecision:
    now = now or datetime.now(UTC)
    window_start = date(now.year, now.month, 1)
    used = objects_used(session, since=window_start)
    remaining = max(0, FREE_TIER_OBJECT_ALLOWANCE - used)

    def refuse(reason: str) -> FallbackDecision:
        return FallbackDecision(False, reason, EXPLANATIONS[reason], used, remaining)

    if not getattr(settings, "sportsgameodds_enabled", False):
        return refuse(REASON_DISABLED)
    if not (getattr(settings, "sportsgameodds_mapping_path", "") or "").strip():
        return refuse(REASON_UNMAPPED)

    expiry = _expiry(settings)
    if expiry is not None and now.date() >= expiry:
        return refuse(REASON_EXPIRED)

    if (league_code or "").upper() != FALLBACK_LEAGUE:
        return refuse(REASON_WRONG_LEAGUE)
    if tuple(sorted(m.lower() for m in markets)) != FALLBACK_MARKETS:
        return refuse(REASON_WRONG_MARKET)
    if remaining <= 0:
        return refuse(REASON_OBJECTS_EXHAUSTED)

    return FallbackDecision(True, REASON_ELIGIBLE, EXPLANATIONS[REASON_ELIGIBLE], used, remaining)


def _expiry(settings: Settings) -> date | None:
    raw = (getattr(settings, "sportsgameodds_fallback_until", "") or "").strip()
    if not raw:
        # No expiry configured means no authorized window, not an open one.
        return date(1970, 1, 1)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date(1970, 1, 1)


__all__ = [
    "EXPLANATIONS",
    "FALLBACK_LEAGUE",
    "FALLBACK_MARKETS",
    "FREE_TIER_OBJECT_ALLOWANCE",
    "FallbackDecision",
    "REASON_DISABLED",
    "REASON_ELIGIBLE",
    "REASON_EXPIRED",
    "REASON_OBJECTS_EXHAUSTED",
    "REASON_UNMAPPED",
    "REASON_WRONG_LEAGUE",
    "REASON_WRONG_MARKET",
    "fallback_decision",
    "objects_used",
]
