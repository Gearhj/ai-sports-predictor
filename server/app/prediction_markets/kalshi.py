"""Kalshi adapter — official PUBLIC REST market data (no API key required).

Mandatory path is credential-free REST: series / events / markets / orderbook /
historical trades. The order book is normalized from Kalshi's YES-bid / NO-bid
representation into a two-sided 0..1 book (implied asks derived, never
fabricated). An optional authenticated read-only WebSocket exists but is NOT
required for completion; absent credentials the adapter reports REST_ONLY.
Docs retrieved 2026-08-30.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.data_sources.base import AdapterResponse, SourceEnvelope, SourceError
from app.prediction_markets.contracts import (
    ADAPTER_VERSION,
    CanonicalMarketType,
    CanonicalSelection,
    PredictionMarketProvider,
    ProviderContractDTO,
    ProviderQuoteDTO,
    normalize_kalshi_book,
)

SOURCE = "KALSHI"


def _to_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # Kalshi prices come as dollars (0.15) or cents (15). Normalize to 0..1.
    return f / 100.0 if f > 1.0 else f


def _to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value if value < 1e12 else value / 1000, tz=UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


class KalshiPredictionMarketSource:
    source = SOURCE
    adapter_version = ADAPTER_VERSION

    def __init__(
        self,
        *,
        rest_base_url: str = "https://external-api.kalshi.com/trade-api/v2",
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
        api_key_id: str = "",
        private_key_path: str = "",
        ws_enabled: bool = False,
    ) -> None:
        self.rest_base_url = rest_base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        # Credentials are only consulted for the optional WS path; never logged,
        # never serialized, never returned through an API.
        self._api_key_id = api_key_id
        self._private_key_path = private_key_path
        self._ws_enabled = ws_enabled

    @property
    def connection_mode(self) -> str:
        if self._ws_enabled and self._api_key_id and self._private_key_path:
            return "STREAMING"
        return "REST_ONLY"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            resp = await client.get(f"{self.rest_base_url}{path}", params=params or {})
            resp.raise_for_status()
            return resp.json()
        except SourceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(self.source, f"Kalshi request failed: {type(exc).__name__}") from exc
        finally:
            if self._owns_client:
                await client.aclose()

    # -- discovery -------------------------------------------------------
    async def list_series(self, *, category: str | None = None) -> AdapterResponse:
        params = {"category": category} if category else {}
        raw = await self._get("/series", params=params)
        series = raw.get("series", raw) if isinstance(raw, dict) else raw
        fetched = datetime.now(UTC)
        return AdapterResponse(
            envelope=SourceEnvelope(source=self.source, fetched_at=fetched,
                                    data=series if isinstance(series, list) else [],
                                    metadata={"mode": self.connection_mode}),
            raw_payload=raw if isinstance(raw, (dict, list)) else {"raw": raw},
        )

    async def discover_contracts(
        self, *, series_ticker: str, league: str, sport: str, limit: int = 100,
    ) -> AdapterResponse:
        raw = await self._get("/markets", params={"series_ticker": series_ticker, "status": "open", "limit": limit})
        markets = raw.get("markets", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
        contracts: list[ProviderContractDTO] = []
        for market in markets:
            dto = self._normalize_market(market, league=league, sport=sport)
            if dto is not None:
                contracts.append(dto)
        fetched = datetime.now(UTC)
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source, fetched_at=fetched,
                data=[c.model_dump(mode="json") for c in contracts],
                metadata={"markets_seen": len(markets), "contracts": len(contracts),
                          "series_ticker": series_ticker, "mode": self.connection_mode},
            ),
            raw_payload=raw if isinstance(raw, (dict, list)) else {"raw": raw},
        )

    def _normalize_market(self, market: dict[str, Any], *, league: str, sport: str) -> ProviderContractDTO | None:
        if not isinstance(market, dict):
            return None
        ticker = str(market.get("ticker") or "")
        if not ticker:
            return None
        title = str(market.get("title") or "")
        subtitle = str(market.get("subtitle") or market.get("yes_sub_title") or "")
        blob = f"{title} {subtitle}".lower()
        # Governed type inference: default game-winner => moneyline.
        if any(k in blob for k in ("spread", "handicap", "by ", "margin")):
            market_type, line = CanonicalMarketType.FULL_GAME_SPREAD, self._strike(market)
        elif any(k in blob for k in ("total", "over/under", "combined", "points scored", "runs scored")):
            market_type, line = CanonicalMarketType.FULL_GAME_TOTAL, self._strike(market)
        else:
            market_type, line = CanonicalMarketType.FULL_GAME_MONEYLINE, None
        # The YES side names the team/outcome; HOME/AWAY resolved at mapping. We
        # tentatively label the YES team and default selection to HOME, marking
        # the label so mapping can flip it to AWAY when the AISP home differs.
        yes_team = market.get("yes_sub_title") or title
        if market_type == CanonicalMarketType.FULL_GAME_TOTAL:
            selection = CanonicalSelection.OVER
        else:
            selection = CanonicalSelection.HOME
        return ProviderContractDTO(
            provider=PredictionMarketProvider.KALSHI,
            provider_event_id=str(market.get("event_ticker") or ticker),
            provider_market_id=ticker,
            provider_outcome_id=f"{ticker}:YES",
            sport=sport, league=league,
            provider_home_team=str(yes_team) if yes_team else None,
            provider_away_team=None,
            scheduled_start_at=_to_dt(market.get("open_time")),
            provider_game_start_at=_to_dt(market.get("expected_expiration_time") or market.get("close_time")),
            market_type=market_type, selection=selection, line_value=line,
            outcome_label=str(yes_team or "YES"),
            market_question=title or None, market_subtitle=subtitle or None,
            resolution_rules=str(market.get("rules_primary") or market.get("settlement_sources") or "") or None,
            market_close_at=_to_dt(market.get("close_time")),
            settlement_source=str(market.get("settlement_sources") or "") or None,
            market_status=str(market.get("status") or ""),
            line_source="STRUCTURED" if line is not None or market_type == CanonicalMarketType.FULL_GAME_MONEYLINE else "TITLE_PARSED",
            raw={"series_ticker": market.get("series_ticker"), "event_ticker": market.get("event_ticker")},
        )

    @staticmethod
    def _strike(market: dict[str, Any]) -> float | None:
        for key in ("floor_strike", "cap_strike", "strike", "line"):
            val = market.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
        return None

    # -- order book (YES/NO bids -> normalized two-sided 0..1 book) -------
    async def get_order_book(self, ticker: str, *, depth: int = 10) -> AdapterResponse:
        raw = await self._get(f"/markets/{ticker}/orderbook", params={"depth": depth})
        fetched = datetime.now(UTC)
        ob = (raw.get("orderbook") if isinstance(raw, dict) else None) or {}
        yes = self._levels(ob.get("yes"))
        no = self._levels(ob.get("no"))
        book = normalize_kalshi_book(yes, no)
        quote = ProviderQuoteDTO(
            provider=PredictionMarketProvider.KALSHI,
            provider_market_id=ticker, provider_outcome_id=f"{ticker}:YES",
            book=book, last_trade=None, provider_observed_at=fetched,
            market_status=None, raw={"depth": depth},
        )
        return AdapterResponse(
            envelope=SourceEnvelope(source=self.source, fetched_at=fetched,
                                    data=[quote.model_dump(mode="json")],
                                    metadata={"ticker": ticker, "mode": self.connection_mode}),
            raw_payload=raw if isinstance(raw, dict) else {"raw": raw},
        )

    @staticmethod
    def _levels(rows: Any) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for row in rows or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                price, size = _to_float(row[0]), _to_float2(row[1])
                if price is not None and size is not None:
                    out.append((price, size))
        return out

    async def get_trades(self, ticker: str, *, limit: int = 100, min_ts: int | None = None, max_ts: int | None = None) -> AdapterResponse:
        params: dict[str, Any] = {"ticker": ticker, "limit": limit}
        if min_ts is not None:
            params["min_ts"] = min_ts
        if max_ts is not None:
            params["max_ts"] = max_ts
        raw = await self._get("/historical/trades", params=params)
        trades = raw.get("trades", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
        norm = [{
            "trade_id": t.get("trade_id"), "ticker": t.get("ticker"),
            "yes_price": _to_float(t.get("yes_price_dollars") or t.get("yes_price")),
            "no_price": _to_float(t.get("no_price_dollars") or t.get("no_price")),
            "taker_side": t.get("taker_outcome_side"), "created_time": t.get("created_time"),
        } for t in trades if isinstance(t, dict)]
        fetched = datetime.now(UTC)
        return AdapterResponse(
            envelope=SourceEnvelope(source=self.source, fetched_at=fetched, data=norm,
                                    metadata={"ticker": ticker, "count": len(norm), "mode": self.connection_mode}),
            raw_payload=raw if isinstance(raw, (dict, list)) else {"raw": raw},
        )


def _to_float2(value: Any) -> float | None:
    """Size does not need the 0..1 normalization (it is a contract count)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["KalshiPredictionMarketSource", "SOURCE"]
