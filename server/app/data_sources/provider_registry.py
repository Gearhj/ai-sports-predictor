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
        bookmaker_namespace="demo",
        authority_note="Illustrative fixture data. Never governed, never LIVE.",
    ),
}

#: The source values a governed read path may consider.
GOVERNED_SOURCES: frozenset[str] = frozenset(
    identity.key for identity in PROVIDERS.values() if identity.governed
)


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
