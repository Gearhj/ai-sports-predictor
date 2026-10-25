from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx

from .base import (
    AdapterResponse, BookmakerDTO, BookMarketDTO, OddsEventDTO, OddsOutcomeDTO,
    OddsScoreEventDTO, OddsScoreTeamDTO, SourceEnvelope, SourceError,
)


class TheOddsAPIAdapter:
    source = "THE_ODDS_API"
    base_url = "https://api.the-odds-api.com/v4"

    def __init__(
        self,
        api_key: str,
        markets: list[str] | None = None,
        regions: str = "us",
        sport_key: str = "baseball_mlb",
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.markets = markets or ["h2h", "spreads", "totals"]
        self.regions = regions
        # NCAAF is priced only by the CFB paper lane (app/cfb/odds_api_price.py),
        # and NHL only by the NHL paper lane (app/nhl/odds_api_price.py); neither
        # is admitted by the name-keyed ingest_odds binder (odds_league).
        allowed_sports = {"baseball_mlb", "americanfootball_nfl", "americanfootball_ncaaf", "icehockey_nhl", "basketball_nba"}
        if sport_key not in allowed_sports:
            raise ValueError(f"Unsupported Odds API sport key: {sport_key}")
        self.sport_key = sport_key
        self._client = client
        self.timeout = timeout

    async def get_quota_balance(self) -> AdapterResponse:
        """Read the quota headers from the documented zero-credit endpoint.

        ``GET /v4/sports/`` returns the same ``x-requests-*`` headers as a
        priced call but does not consume a usage credit
        (https://the-odds-api.com/liveapi/guides/v4/#get-sports). It is the
        correct way to establish the current balance without spending any.

        It returns no odds, so it can never be mistaken for a price. The key is
        sent as a parameter and is never echoed into the envelope, metadata or
        any error message.
        """

        if not self.api_key:
            raise SourceError(self.source, "THE_ODDS_API_KEY is not configured", retryable=False)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.get(
                f"{self.base_url}/sports/",
                params={"apiKey": self.api_key},
            )
            response.raise_for_status()
            raw = response.json()
            if not isinstance(raw, list):
                raise SourceError(self.source, "Odds API returned an unexpected payload", retryable=False)
            quota = {
                "requests_remaining": _header_int(response.headers, "x-requests-remaining"),
                "requests_used": _header_int(response.headers, "x-requests-used"),
                "requests_last": _header_int(response.headers, "x-requests-last"),
            }
            return AdapterResponse(
                envelope=SourceEnvelope(
                    source=self.source,
                    fetched_at=datetime.now(UTC),
                    # Deliberately empty: this endpoint carries no odds.
                    data=[],
                    metadata={
                        "endpoint": "v4/sports",
                        "credit_cost": 0,
                        "sport_count": len(raw),
                        **quota,
                    },
                ),
                raw_payload=None,
            )
        except SourceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(
                self.source, f"Quota balance request failed: {type(exc).__name__}"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

    async def get_market_snapshot(self) -> AdapterResponse:
        if not self.api_key:
            raise SourceError(self.source, "THE_ODDS_API_KEY is not configured", retryable=False)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.get(
                f"{self.base_url}/sports/{self.sport_key}/odds",
                params={
                    "apiKey": self.api_key,
                    "regions": self.regions,
                    "markets": ",".join(self.markets),
                    "oddsFormat": "american",
                    "dateFormat": "iso",
                },
            )
            response.raise_for_status()
            raw = response.json()
            if not isinstance(raw, list):
                raise SourceError(self.source, "Odds API returned an unexpected payload", retryable=False)
            clock_refusals: list[dict[str, Any]] = []
            events = [self._normalize_event(event, clock_refusals) for event in raw]
            quota = {
                "requests_remaining": _header_int(response.headers, "x-requests-remaining"),
                "requests_used": _header_int(response.headers, "x-requests-used"),
                "requests_last": _header_int(response.headers, "x-requests-last"),
            }
            # Present only when a bookmaker had no clock, so a normal response's
            # metadata is unchanged.
            extra = {"clock_refusals": clock_refusals} if clock_refusals else {}
            fetched_at = datetime.now(UTC)
            return AdapterResponse(
                envelope=SourceEnvelope(
                    source=self.source,
                    fetched_at=fetched_at,
                    data=[event.model_dump(mode="json") for event in events],
                    metadata={"event_count": len(events), "sport_key": self.sport_key, **quota, **extra},
                ),
                raw_payload=raw,
            )
        except SourceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(self.source, f"Odds request failed: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def get_event_odds(self, event_id: str, markets: list[str]) -> AdapterResponse:
        """One event's odds for explicitly listed markets (single-event endpoint).

        ``GET /v4/sports/{sport}/events/{eventId}/odds``. The provider bills
        markets x regions. The caller must reserve credits first; this method
        never loops, never widens the market list and never echoes the key --
        errors carry only the exception type, because an httpx error string
        includes the full URL.
        """

        if not self.api_key:
            raise SourceError(self.source, "THE_ODDS_API_KEY is not configured", retryable=False)
        if not _EVENT_ID.fullmatch(event_id or ""):
            raise SourceError(self.source, "Invalid provider event id", retryable=False)
        if not markets or any(not _MARKET_KEY.fullmatch(m) for m in markets):
            raise SourceError(self.source, "Markets must be listed explicitly", retryable=False)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.get(
                f"{self.base_url}/sports/{self.sport_key}/events/{event_id}/odds",
                params={
                    "apiKey": self.api_key,
                    "regions": self.regions,
                    "markets": ",".join(markets),
                    "oddsFormat": "american",
                    "dateFormat": "iso",
                },
            )
            quota = {
                "requests_remaining": _header_int(response.headers, "x-requests-remaining"),
                "requests_used": _header_int(response.headers, "x-requests-used"),
                "requests_last": _header_int(response.headers, "x-requests-last"),
            }
            if response.status_code >= 400:
                error = SourceError(self.source, f"Event odds request failed: HTTP {response.status_code}",
                                    retryable=False, status_code=response.status_code)
                error.quota = quota  # type: ignore[attr-defined]
                raise error
            raw = response.json()
            if not isinstance(raw, dict):
                raise SourceError(self.source, "Odds API returned an unexpected payload", retryable=False)
            return AdapterResponse(
                envelope=SourceEnvelope(
                    source=self.source,
                    fetched_at=datetime.now(UTC),
                    data=[],
                    metadata={"endpoint": "v4/sports/{sport}/events/{eventId}/odds",
                              "sport_key": self.sport_key, "markets": list(markets),
                              "regions": self.regions, **quota},
                ),
                raw_payload=raw,
            )
        except SourceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(self.source, f"Event odds request failed: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def get_scores(self, *, days_from: int = 3) -> AdapterResponse:
        """Fetch recent/future score state through the existing governed provider.

        This endpoint is used only by an explicit operational job. Public reads
        never instantiate or call the adapter.
        """

        if not self.api_key:
            raise SourceError(self.source, "THE_ODDS_API_KEY is not configured", retryable=False)
        if not 1 <= days_from <= 3:
            raise ValueError("Odds API score lookback must be between one and three days")
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.get(
                f"{self.base_url}/sports/{self.sport_key}/scores",
                params={
                    "apiKey": self.api_key,
                    "daysFrom": days_from,
                    "dateFormat": "iso",
                },
            )
            response.raise_for_status()
            raw = response.json()
            if not isinstance(raw, list):
                raise SourceError(self.source, "Odds API scores returned an unexpected payload", retryable=False)
            events = [self._normalize_score_event(event) for event in raw]
            quota = {
                "requests_remaining": _header_int(response.headers, "x-requests-remaining"),
                "requests_used": _header_int(response.headers, "x-requests-used"),
                "requests_last": _header_int(response.headers, "x-requests-last"),
            }
            fetched_at = datetime.now(UTC)
            return AdapterResponse(
                envelope=SourceEnvelope(
                    source=self.source,
                    fetched_at=fetched_at,
                    data=[event.model_dump(mode="json") for event in events],
                    metadata={"event_count": len(events), "sport_key": self.sport_key, **quota},
                ),
                raw_payload=raw,
            )
        except SourceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(self.source, f"Scores request failed: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def _normalize_event(item: dict[str, Any], clock_refusals: list[dict[str, Any]] | None = None) -> OddsEventDTO:
        """One provider event -> DTO.

        CLOCKS. A market's quote clock is its own ``last_update``. The documented
        ``/odds`` response shape may carry only the bookmaker ``last_update``;
        such a market takes the bookmaker clock and is marked
        ``clock_source="BOOK"`` so no consumer mistakes it for an observed market
        clock. When the market clock is present nothing changes (the DTO and its
        dump are byte-identical to before). A bookmaker with no clock of its own
        is skipped with its markets (a clockless market there has NEITHER clock;
        no time is invented) -- never the whole response -- and named in
        ``clock_refusals`` when a list is supplied.
        """

        books: list[BookmakerDTO] = []
        for book in item.get("bookmakers") or []:
            book_clock = book.get("last_update")
            if not book_clock:
                if clock_refusals is not None:
                    clock_refusals.append({"reason": "BOOK_CLOCK_MISSING", "event_id": item.get("id"),
                                           "book": book.get("key")})
                continue
            markets: list[BookMarketDTO] = []
            for market in book.get("markets") or []:
                outcomes = [OddsOutcomeDTO(**outcome) for outcome in market.get("outcomes") or []]
                if market.get("last_update"):
                    markets.append(BookMarketDTO(key=market["key"], last_update=market["last_update"],
                                                 outcomes=outcomes))
                else:
                    markets.append(BookMarketDTO(key=market["key"], last_update=book_clock,
                                                 outcomes=outcomes, clock_source="BOOK"))
            books.append(
                BookmakerDTO(
                    key=book["key"],
                    title=book["title"],
                    last_update=book_clock,
                    markets=markets,
                )
            )
        return OddsEventDTO(
            source_event_id=item["id"],
            commence_time=item["commence_time"],
            home_team=item["home_team"],
            away_team=item["away_team"],
            bookmakers=books,
        )

    @staticmethod
    def _normalize_score_event(item: dict[str, Any]) -> OddsScoreEventDTO:
        scores = [
            OddsScoreTeamDTO(name=row["name"], score=int(row["score"]))
            for row in (item.get("scores") or [])
        ]
        return OddsScoreEventDTO(
            source_event_id=item["id"],
            commence_time=item["commence_time"],
            completed=bool(item.get("completed", False)),
            home_team=item["home_team"],
            away_team=item["away_team"],
            scores=scores,
            last_update=item.get("last_update"),
        )


_EVENT_ID = re.compile(r"[A-Za-z0-9]{8,64}")
_MARKET_KEY = re.compile(r"[a-z][a-z0-9_]{1,48}")


def _header_int(headers: httpx.Headers, name: str) -> int | None:
    try:
        return int(headers[name])
    except (KeyError, TypeError, ValueError):
        return None
