"""SportsGameOdds research fallback adapter — DISABLED, and fail-closed by design.

WHAT IS AND IS NOT ESTABLISHED
------------------------------
This adapter was written WITHOUT contacting the provider: no account was
registered, no credential was configured, no documentation was fetched and no
request was made. Nothing about SportsGameOdds' actual response shape, coverage,
bookmaker list, timestamp semantics, free-tier accounting or licence terms is
verified. Treat every field name below as a HYPOTHESIS.

Because of that, the adapter does not guess. It requires an explicit, local
**field mapping manifest** describing how a real payload maps onto the fields
AISP needs, and refuses to produce a single row until that manifest exists and
every required field resolves. The manifest can only be written by someone who
has seen a real response, which is precisely the authorization that is missing.
So the failure mode when the assumptions are wrong is "no data", never "wrong
data attributed to the wrong provider".

WHAT IT WOULD PRODUCE IF ENABLED AND MAPPED
-------------------------------------------
Research-only odds rows under ``source = SPORTSGAMEODDS`` whose bookmakers live
in the ``sgo:`` namespace (see :mod:`app.data_sources.provider_registry`). Those
rows are:

* stored and fully attributable (provider, book, market, observation time),
* visible to research and operator surfaces that ask for them explicitly,
* structurally incapable of forming a "same-book two-sided quote" with an Odds
  API row, because the two providers never share a ``Sportsbook`` row, and
* excluded by an independent source filter on the governed pricing path.

WHAT REMAINS BLOCKED
--------------------
Everything that needs the provider: coverage, bookmakers, whether prices carry
an observation timestamp, whether both sides of a moneyline are retrievable from
one book, free-tier request/object accounting, and licence compatibility. Each
is recorded in :data:`UNVERIFIED_ASSUMPTIONS` and each blocks enablement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import (
    AdapterResponse,
    BookmakerDTO,
    BookMarketDTO,
    OddsEventDTO,
    OddsOutcomeDTO,
    SourceEnvelope,
    SourceError,
)
from .provider_registry import SPORTSGAMEODDS, identity

#: Every claim that could not be checked without contacting the provider. Each
#: one must be settled by a real response before the flag may be turned on.
UNVERIFIED_ASSUMPTIONS: tuple[dict[str, str], ...] = (
    {"claim": "MLB and NFL are both covered on the free tier",
     "settled_by": "one authorised response listing available leagues"},
    {"claim": "US bookmakers are named and stable enough to map to AISP sportsbooks",
     "settled_by": "one authorised response showing the bookmaker identifiers"},
    {"claim": "each price carries its own observation timestamp",
     "settled_by": "one authorised response showing a per-price timestamp field"},
    {"claim": "both sides of a moneyline are retrievable from a single bookmaker",
     "settled_by": "one authorised response showing paired outcomes per book"},
    {"claim": "free-tier accounting (requests vs objects) permits the checkpoint cadence",
     "settled_by": "the provider's own rate-limit headers or written terms"},
    {"claim": "the licence permits this research use and retention",
     "settled_by": "the provider's terms of service, read and recorded"},
    {"claim": "event identities can be mapped to AISP canonical games",
     "settled_by": "one authorised response compared against the canonical schedule"},
)

#: The fields AISP must have for a row to be usable at all. A mapping manifest
#: must resolve every one of them; a partial mapping is refused.
REQUIRED_FIELDS: tuple[str, ...] = (
    "event_id",
    "commence_time",
    "home_team",
    "away_team",
    "bookmaker_key",
    "bookmaker_title",
    "market_key",
    "outcome_name",
    "outcome_price_american",
    "price_observed_at",
)

MANIFEST_SCHEMA = "AISP_SPORTSGAMEODDS_FIELD_MAPPING_V1"


class FallbackProviderDisabled(SourceError):
    def __init__(self) -> None:
        super().__init__(
            SPORTSGAMEODDS,
            "The SportsGameOdds fallback is disabled. It performs no request and "
            "produces no rows until it is explicitly enabled AND a verified field "
            "mapping manifest exists.",
            retryable=False,
        )


class FallbackMappingUnavailable(SourceError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            SPORTSGAMEODDS,
            f"SportsGameOdds field mapping is not established: {detail}. No row is "
            "produced; a guessed mapping would attribute prices to the wrong market.",
            retryable=False,
        )


@dataclass(frozen=True)
class FieldMapping:
    """A locally authored, explicitly versioned map from payload to AISP fields."""

    schema_version: str
    #: Dotted paths into the provider payload, one per REQUIRED_FIELDS entry.
    paths: dict[str, str]
    #: Provider market key -> AISP market key (h2h / spreads / totals).
    market_keys: dict[str, str]
    verified_against_response_sha256: str | None
    note: str

    @classmethod
    def load(cls, path: Path) -> "FieldMapping":
        if not path.is_file():
            raise FallbackMappingUnavailable(f"no manifest at {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FallbackMappingUnavailable(f"manifest is unreadable ({type(error).__name__})") from error
        if raw.get("schemaVersion") != MANIFEST_SCHEMA:
            raise FallbackMappingUnavailable("manifest schemaVersion is not supported")
        paths = raw.get("paths")
        if not isinstance(paths, dict):
            raise FallbackMappingUnavailable("manifest declares no field paths")
        missing = [field for field in REQUIRED_FIELDS if not paths.get(field)]
        if missing:
            raise FallbackMappingUnavailable(f"manifest does not map {', '.join(missing)}")
        markets = raw.get("marketKeys")
        if not isinstance(markets, dict) or not markets:
            raise FallbackMappingUnavailable("manifest declares no market key mapping")
        if not raw.get("verifiedAgainstResponseSha256"):
            raise FallbackMappingUnavailable(
                "manifest is not bound to a real observed response; a mapping that has "
                "never been checked against a payload is a guess"
            )
        return cls(
            schema_version=MANIFEST_SCHEMA,
            paths=dict(paths),
            market_keys={str(k): str(v) for k, v in markets.items()},
            verified_against_response_sha256=str(raw["verifiedAgainstResponseSha256"]),
            note=str(raw.get("note", "")),
        )


def _dig(payload: Any, dotted: str) -> Any:
    current = payload
    for part in dotted.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


class SportsGameOddsAdapter:
    """Research fallback. Disabled by default; never governed.

    The adapter deliberately has NO default base URL and NO default field
    mapping. Both must be supplied explicitly, so it cannot silently reach a
    provider or silently interpret a payload.
    """

    source = SPORTSGAMEODDS

    def __init__(
        self,
        *,
        enabled: bool,
        api_key: str = "",
        base_url: str = "",
        mapping_path: Path | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").strip().rstrip("/")
        self.mapping_path = mapping_path
        self.identity = identity(SPORTSGAMEODDS)

    # -- guards -----------------------------------------------------------
    def _require_enabled(self) -> None:
        if not self.enabled:
            raise FallbackProviderDisabled()

    def mapping(self) -> FieldMapping:
        self._require_enabled()
        if self.mapping_path is None:
            raise FallbackMappingUnavailable("no mapping manifest path is configured")
        return FieldMapping.load(self.mapping_path)

    def readiness(self) -> dict[str, Any]:
        """What still blocks this provider. Safe to call at any time; no I/O."""

        try:
            self.mapping()
            mapping_state = "MAPPED"
            mapping_detail = "A verified field mapping manifest is present."
        except SourceError as error:
            mapping_state = "UNMAPPED"
            mapping_detail = str(error)
        return {
            "provider": SPORTSGAMEODDS,
            "governed": self.identity.governed,
            "enabled": self.enabled,
            "credential_configured": bool(self.api_key),
            "base_url_configured": bool(self.base_url),
            "mapping_state": mapping_state,
            "mapping_detail": mapping_detail,
            "bookmaker_namespace": self.identity.bookmaker_namespace,
            "unverified_assumptions": [dict(item) for item in UNVERIFIED_ASSUMPTIONS],
            "blocked_on": (
                "Explicit authorization to contact the provider, then a recorded response "
                "to bind a field mapping manifest to. No account has been created, no "
                "credential configured and no request made."
            ),
            "ready_to_enable": False,
        }

    # -- transport --------------------------------------------------------
    async def get_market_snapshot(self, *_args: Any, **_kwargs: Any) -> AdapterResponse:
        """Refuses. Enabling the flag alone does not authorise a provider call."""

        self._require_enabled()
        self.mapping()
        raise SourceError(
            SPORTSGAMEODDS,
            "A live SportsGameOdds request requires explicit authorization that has not "
            "been given. The adapter is complete up to the transport boundary and stops "
            "here on purpose.",
            retryable=False,
        )

    # -- normalisation ----------------------------------------------------
    def normalize(self, payload: Any, *, fetched_at: datetime | None = None) -> AdapterResponse:
        """Map an ALREADY-OBTAINED payload onto AISP DTOs, fail-closed.

        Separated from transport so the mapping can be exercised against a
        recorded fixture without any network access. Every row that cannot be
        fully resolved is dropped rather than guessed, and every bookmaker key
        is namespaced so these rows can never share a Sportsbook with the
        governed provider.
        """

        mapping = self.mapping()
        fetched_at = fetched_at or datetime.now(UTC)
        events_raw = payload if isinstance(payload, list) else (payload or {}).get("data") or []
        if not isinstance(events_raw, list):
            raise FallbackMappingUnavailable("payload is not a list of events")

        events: list[OddsEventDTO] = []
        dropped = 0
        for raw_event in events_raw:
            event = self._event(raw_event, mapping)
            if event is None:
                dropped += 1
                continue
            events.append(event)
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source,
                fetched_at=fetched_at,
                data=[event.model_dump() for event in events],
                metadata={
                    "provider": SPORTSGAMEODDS,
                    "governed": False,
                    "research_only": True,
                    "bookmaker_namespace": self.identity.bookmaker_namespace,
                    "mapping_schema": mapping.schema_version,
                    "mapping_verified_against": mapping.verified_against_response_sha256,
                    "events_mapped": len(events),
                    "events_dropped_incomplete": dropped,
                    "credit_cost": 0,
                },
            ),
            raw_payload=payload,
        )

    def _event(self, raw: Any, mapping: FieldMapping) -> OddsEventDTO | None:
        get = lambda field: _dig(raw, mapping.paths[field])  # noqa: E731
        event_id = get("event_id")
        commence = _parse_time(get("commence_time"))
        home, away = get("home_team"), get("away_team")
        if not (event_id and commence and home and away):
            return None

        books: dict[str, BookmakerDTO] = {}
        for raw_book in _iterate(raw, mapping.paths["bookmaker_key"]):
            book_key = _dig(raw_book, _leaf(mapping.paths["bookmaker_key"]))
            book_title = _dig(raw_book, _leaf(mapping.paths["bookmaker_title"])) or book_key
            if not book_key:
                continue
            namespaced = self.identity.bookmaker_key(str(book_key))
            markets: list[BookMarketDTO] = []
            for raw_market in _iterate(raw_book, mapping.paths["market_key"]):
                provider_market = _dig(raw_market, _leaf(mapping.paths["market_key"]))
                aisp_market = mapping.market_keys.get(str(provider_market))
                if aisp_market is None:
                    continue  # an unmapped market is never guessed into a known one
                observed = _parse_time(_dig(raw_market, _leaf(mapping.paths["price_observed_at"])))
                if observed is None:
                    continue  # a price with no observation time cannot be aged
                outcomes: list[OddsOutcomeDTO] = []
                for raw_outcome in _iterate(raw_market, mapping.paths["outcome_name"]):
                    name = _dig(raw_outcome, _leaf(mapping.paths["outcome_name"]))
                    price = _dig(raw_outcome, _leaf(mapping.paths["outcome_price_american"]))
                    if name is None or price is None:
                        continue
                    outcomes.append(OddsOutcomeDTO(name=str(name), price=int(price), point=None))
                # A one-sided market can never satisfy a two-sided contract, so
                # it is dropped rather than stored as a half-quote.
                if len(outcomes) < 2:
                    continue
                markets.append(BookMarketDTO(key=aisp_market, last_update=observed, outcomes=outcomes))
            if markets:
                books[namespaced] = BookmakerDTO(
                    key=namespaced,
                    title=str(book_title),
                    # The book's own freshness is the newest market it quoted.
                    last_update=max(market.last_update for market in markets),
                    markets=markets,
                )
        if not books:
            return None
        return OddsEventDTO(
            source_event_id=str(event_id),
            commence_time=commence,
            home_team=str(home),
            away_team=str(away),
            bookmakers=list(books.values()),
            source_mode="LIVE",
        )


def _leaf(dotted: str) -> str:
    return dotted.rsplit(".", 1)[-1]


def _iterate(container: Any, dotted: str) -> list[Any]:
    """The collection a dotted path's leaf lives in, relative to ``container``.

    Manifest paths are written from the event root (``books.markets.sides.label``)
    but are consumed level by level, so only the segment immediately above the
    leaf is meaningful at each step: ``books`` inside the event, ``markets``
    inside a book, ``sides`` inside a market.
    """

    parts = dotted.split(".")
    node = container if len(parts) < 2 else _dig(container, parts[-2])
    if isinstance(node, list):
        return node
    if isinstance(node, dict):
        return [node]
    return []


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "MANIFEST_SCHEMA",
    "REQUIRED_FIELDS",
    "UNVERIFIED_ASSUMPTIONS",
    "FallbackMappingUnavailable",
    "FallbackProviderDisabled",
    "FieldMapping",
    "SportsGameOddsAdapter",
]
