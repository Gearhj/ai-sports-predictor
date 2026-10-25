"""Which odds provider may reach a governed decision, and which may not.

THE PROBLEM THIS SOLVES
-----------------------
``odds_snapshots`` carries a free-text ``source`` column and, before this
module, nothing read it. Two facts made that dangerous the moment a second
provider existed:

1. ``ReadService._latest_market_snapshots`` reduces snapshots per
   ``(market, sportsbook)`` with no provider filter, and
   ``_market_inputs`` then pairs the two sides of a market by
   ``books[book.id][outcome.id]``. A second provider writing to the SAME
   ``Sportsbook`` row could therefore contribute ONE side of a "same-book
   two-sided quote" while The Odds API contributed the other.
2. ``Normalizer.ingest_odds`` resolves a sportsbook by ``Sportsbook.key``
   alone, so both providers reporting ``draftkings`` would share one row.

Either would let a fallback price masquerade as governed evidence.

THE DESIGN
----------
Two independent, redundant barriers:

* **Namespace separation.** A non-governed provider's bookmaker keys are
  prefixed (``sgo:draftkings``), so it gets its OWN ``Sportsbook`` rows. Because
  the two-sided pairing keys on ``sportsbook_id``, a mixed-provider pair is
  structurally impossible — not merely filtered out.
* **Explicit source filtering.** The governed read path additionally restricts
  ``odds_snapshots.source`` to the governed set, so even a mis-namespaced row
  cannot reach a V2.1 gate.

Neither barrier depends on the other being correct.

WHAT "GOVERNED" MEANS
---------------------
Only ``THE_ODDS_API`` is governed today. A governed provider's quotes may be
priced by ``AISP_PRACTICAL_PREGAME_POLICY_V2_1`` and can therefore reach an
official decision and a virtual position. Everything else is research evidence:
stored, attributable, displayed where it is labelled, and structurally unable to
qualify a bet. Promoting a provider is a deliberate policy act, not a config
toggle, so ``governed`` is a property of this registry and not of a setting.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from dataclasses import dataclass

#: The single provider whose prices may reach a governed V2.1 decision.
THE_ODDS_API = "THE_ODDS_API"
SPORTSGAMEODDS = "SPORTSGAMEODDS"
DEMO_FIXTURE = "ASP_DEMO_FIXTURE"


@dataclass(frozen=True)
class ProviderIdentity:
    """Immutable identity and authority of one odds source."""

    key: str
    display_name: str
    #: May its quotes be priced by the governing policy?
    governed: bool
    #: Prefix applied to every bookmaker key so provider rows never share a
    #: Sportsbook with another provider. Empty only for the governed provider,
    #: whose 222k existing rows define the un-prefixed namespace.
    bookmaker_namespace: str
    authority_note: str

    def bookmaker_key(self, provider_key: str) -> str:
        """Namespaced sportsbook key for this provider."""

        cleaned = provider_key.strip().lower()
        if not cleaned:
            raise ValueError("A bookmaker key cannot be empty")
        if not self.bookmaker_namespace:
            return cleaned
        return f"{self.bookmaker_namespace}:{cleaned}"


PROVIDERS: dict[str, ProviderIdentity] = {
    THE_ODDS_API: ProviderIdentity(
        key=THE_ODDS_API,
        display_name="The Odds API",
        governed=True,
        bookmaker_namespace="",
        authority_note=(
            "The governed odds provider. Its complete same-book two-sided moneylines "
            "are the only prices AISP_PRACTICAL_PREGAME_POLICY_V2_1 may price."
        ),
    ),
    SPORTSGAMEODDS: ProviderIdentity(
        key=SPORTSGAMEODDS,
        display_name="SportsGameOdds",
        governed=False,
        bookmaker_namespace="sgo",
        authority_note=(
            "Research fallback. Rows are stored and attributable but can never satisfy a "
            "governed gate: its bookmakers occupy a separate namespace, so a two-sided "
            "pair can never mix providers, and the governed read path filters on source."
        ),
    ),
    DEMO_FIXTURE: ProviderIdentity(
        key=DEMO_FIXTURE,
        display_name="AISP demo fixture",
        governed=False,
        # No prefix: demo rows are already separated from live ones by
        # source_mode, which every governed query filters on, and prefixing them
        # would fork the demo bookmaker identities for no additional safety.
        bookmaker_namespace="",
        authority_note=(
            "Illustrative fixture data. Never governed. Separated from live evidence "
            "by source_mode, and authorised only for DEMO reads."
        ),
    ),
}

#: The source values a governed read path may consider by permanent authority.
#: SportsGameOdds is NOT in here and never becomes so by being enabled: it is a
#: dated, scoped exception handled below.
GOVERNED_SOURCES: frozenset[str] = frozenset(
    identity.key for identity in PROVIDERS.values() if identity.governed
)

# --- the temporary governed fallback -----------------------------------------
# SportsGameOdds may price a governed MLB moneyline ONLY while the primary
# provider's September credits are exhausted. Three things bound it, and all
# three are here rather than in configuration, because a fallback that can be
# widened by editing a setting is a fallback that will be widened:
#
#   * it expires on a fixed date, so October needs no one to remember;
#   * it covers one league and one market, the only market a governed decision
#     can price;
#   * it is still never the preferred source -- see prefer_primary below.
#
# It also never stops being itself. Its rows keep source = SPORTSGAMEODDS and
# its bookmakers keep the "sgo:" namespace, so a two-sided pair can never mix
# providers and nothing is ever relabelled as THE_ODDS_API.
GOVERNED_FALLBACK = SPORTSGAMEODDS
GOVERNED_FALLBACK_UNTIL = date(2026, 9, 30)
GOVERNED_FALLBACK_LEAGUES: frozenset[str] = frozenset({"MLB"})
GOVERNED_FALLBACK_MARKETS: frozenset[str] = frozenset({"MONEYLINE", "h2h"})


def fallback_window_open(now: datetime | None = None) -> bool:
    """True only on or before the last authorized day, in UTC."""
    moment = now or datetime.now(UTC)
    return moment.date() <= GOVERNED_FALLBACK_UNTIL


# --- the NFL pregame lane's bounded October renewal ---------------------------
# A SEPARATE, narrower window for ONE lane: the NFL pregame collector
# (`sgo_nfl_events`, app/services/sgo_nfl_pregame.py). It does not reopen the
# governed MLB fallback above, does not add SportsGameOdds to
# `governed_sources()`, and does not reach the NFL live-score, Show Preview or
# MLB request paths, which all keep reading `fallback_window_open` and therefore
# stay refused from 2026-10-01T00:00Z.
#
# Like the fallback it is code, not configuration, so it expires by
# construction: nothing is eligible after the last day, and no setting can
# widen it. Its spend inside the renewal billing month is additionally capped
# (NFL_LANE_RENEWAL_OBJECT_CAP) on top of the lane's daily allowance, the
# T-26 reservation and both account-wide budget guards, none of which change.
NFL_LANE_RENEWAL_ENDPOINT = "sgo_nfl_events"
NFL_LANE_ELIGIBLE_UNTIL = date(2026, 10, 31)
#: The provider billing month (UTC calendar month) the renewal cap counts.
NFL_LANE_RENEWAL_BILLING_MONTH = "2026-10"
#: At most this many event objects on the NFL pregame lane in that month.
#: October 2026 has 60 NFL games in AISP's schedule: 60 T-26 checkpoint objects
#: plus at most 60 Show Prep preview objects is 120; the remaining 30 is retry
#: headroom (a retry is owed only after a terminal zero-charge primary failure).
NFL_LANE_RENEWAL_OBJECT_CAP = 150


def nfl_lane_window_open(now: datetime | None = None) -> bool:
    """True while the NFL pregame lane may ask SportsGameOdds, in UTC.

    Open through the general fallback's last day (so September is unchanged)
    and through the lane's own renewal day. The instant is CONVERTED to UTC
    first: the DB session hands back Eastern-aware datetimes, and `.date()` on
    one of those would read the Eastern calendar day.
    """
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    day = moment.astimezone(UTC).date()
    return day <= GOVERNED_FALLBACK_UNTIL or day <= NFL_LANE_ELIGIBLE_UNTIL


def nfl_lane_renewal_in_force(now: datetime | None = None) -> bool:
    """True when the NFL lane is eligible ONLY because of the renewal."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    day = moment.astimezone(UTC).date()
    return GOVERNED_FALLBACK_UNTIL < day <= NFL_LANE_ELIGIBLE_UNTIL


def governed_sources(
    *, now: datetime | None = None, fallback_enabled: bool | None = None,
) -> frozenset[str]:
    """Which sources may price a governed MLB moneyline at this moment.

    Fail-closed: the fallback is included only when it is switched on AND the
    dated window is still open. Either condition alone is not enough, and the
    caller never has to know the date.
    """
    if fallback_enabled is None:
        try:
            from app.core.config import get_settings

            fallback_enabled = bool(getattr(get_settings(), "sportsgameodds_enabled", False))
        except Exception:  # configuration unavailable -> primary only
            fallback_enabled = False
    if fallback_enabled and fallback_window_open(now):
        return GOVERNED_SOURCES | {GOVERNED_FALLBACK}
    return GOVERNED_SOURCES


def prefer_primary(
    rows: "list[Any]",
    source_of: "Callable[[Any], str]",
    *,
    primary_usable: "Callable[[list[Any]], bool] | None" = None,
) -> "list[Any]":
    """Drop fallback rows whenever the primary can actually answer.

    This is what makes the fallback a fallback rather than a ninth bookmaker:
    the selection downstream takes the BEST price across books, so without it a
    fallback price could outbid the governed provider on a game that provider
    had already covered.

    But PRESENCE is not the same as USABILITY. A single leftover row -- one side
    of a market, or a price from yesterday -- cannot price anything, and letting
    it suppress a complete, fresh fallback pair would turn a recoverable game
    into an uncovered one for no gain. ``primary_usable`` decides that question
    for the caller's row shape; without it, presence is taken as sufficient, so
    existing callers keep their old behaviour.
    """
    primary = [row for row in rows if source_of(row) in GOVERNED_SOURCES]
    if not primary:
        return list(rows)
    if primary_usable is not None and not primary_usable(primary):
        return list(rows)
    return primary


class ProviderAuthorityError(RuntimeError):
    """Raised when a non-governed provider is used where governance is required."""


def identity(source: str) -> ProviderIdentity:
    """The registered identity for a source, or a fail-closed unknown provider.

    An unregistered source is treated as NON-governed. A provider cannot become
    governed by being absent from the registry.
    """

    known = PROVIDERS.get(source)
    if known is not None:
        return known
    cleaned = (source or "UNKNOWN").strip().upper() or "UNKNOWN"
    return ProviderIdentity(
        key=cleaned,
        display_name=cleaned.replace("_", " ").title(),
        governed=False,
        bookmaker_namespace=cleaned.lower().replace(" ", "-")[:16],
        authority_note="Unregistered provider. Not governed; research evidence only.",
    )


def is_governed(source: str) -> bool:
    return identity(source).governed


def require_governed(source: str) -> None:
    if not is_governed(source):
        raise ProviderAuthorityError(
            f"{source} is not a governed odds provider; its prices cannot reach a "
            "policy decision or a position"
        )


def authorised_sources_for_mode(source_mode: str | None) -> frozenset[str] | None:
    """Which providers may price a decision for rows of this mode.

    Governance is a LIVE concern. Only a LIVE row can reach an official decision
    or a virtual position, so that is where the provider restriction belongs and
    it returns the governed set. For any other mode it returns ``None``, meaning
    "no provider restriction": DEMO and synthetic rows are already fenced off by
    ``source_mode``, which every one of these queries filters on, and narrowing
    them further would break illustrative and test surfaces for no added safety.

    Callers apply the predicate only when a set is returned.
    """

    if (source_mode or "LIVE").upper() == "LIVE":
        # Includes the dated MLB-moneyline fallback while it is open, and
        # collapses back to the primary alone the moment it is not.
        return governed_sources()
    return None


def describe() -> list[dict[str, object]]:
    return [
        {
            "provider": item.key,
            "display_name": item.display_name,
            "governed": item.governed,
            "bookmaker_namespace": item.bookmaker_namespace or "(none — governed provider)",
            "authority": item.authority_note,
        }
        for item in PROVIDERS.values()
    ]


__all__ = [
    "DEMO_FIXTURE",
    "GOVERNED_SOURCES",
    "authorised_sources_for_mode",
    "PROVIDERS",
    "ProviderAuthorityError",
    "ProviderIdentity",
    "SPORTSGAMEODDS",
    "THE_ODDS_API",
    "describe",
    "identity",
    "is_governed",
    "require_governed",
]
