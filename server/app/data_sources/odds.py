from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from .base import AdapterResponse, BookmakerDTO, BookMarketDTO, OddsEventDTO, OddsOutcomeDTO, SourceEnvelope, SourceError


class TheOddsAPIAdapter:
    source = "THE_ODDS_API"
    base_url = "https://api.the-odds-api.com/v4"

    def __init__(
        self,
        api_key: str,
        markets: list[str] | None = None,
        regions: str = "us",
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.markets = markets or ["h2h", "spreads", "totals"]
        self.regions = regions
        self._client = client
        self.timeout = timeout

    async def get_market_snapshot(self) -> AdapterResponse:
        if not self.api_key:
            raise SourceError(self.source, "THE_ODDS_API_KEY is not configured", retryable=False)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.get(
                f"{self.base_url}/sports/baseball_mlb/odds",
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
            events = [self._normalize_event(event) for event in raw]
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
                    metadata={"event_count": len(events), **quota},
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

    @staticmethod
    def _normalize_event(item: dict[str, Any]) -> OddsEventDTO:
        books: list[BookmakerDTO] = []
        for book in item.get("bookmakers") or []:
            markets = [
                BookMarketDTO(
                    key=market["key"],
                    last_update=market["last_update"],
                    outcomes=[OddsOutcomeDTO(**outcome) for outcome in market.get("outcomes") or []],
                )
                for market in book.get("markets") or []
            ]
            books.append(
                BookmakerDTO(
                    key=book["key"],
                    title=book["title"],
                    last_update=book["last_update"],
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


def _header_int(headers: httpx.Headers, name: str) -> int | None:
    try:
        return int(headers[name])
    except (KeyError, TypeError, ValueError):
        return None
