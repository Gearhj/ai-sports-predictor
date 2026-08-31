"""Polymarket adapter — official Gamma (discovery) + CLOB (book/history) APIs.

Provider-specific field names are isolated here and never leak past the
normalized ProviderContractDTO / ProviderQuoteDTO. Public REST only; no wallet,
no order entry, no credential. Docs retrieved 2026-08-30.
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
    NormalizedOrderBook,
    OrderBookLevel,
    PredictionMarketProvider,
    ProviderContractDTO,
    ProviderQuoteDTO,
)

SOURCE = "POLYMARKET"

# Polymarket sportsMarketType -> canonical. Defensive: many synonyms map in.
_MARKET_TYPE_MAP = {
    "moneyline": CanonicalMarketType.FULL_GAME_MONEYLINE,
    "winner": CanonicalMarketType.FULL_GAME_MONEYLINE,
    "h2h": CanonicalMarketType.FULL_GAME_MONEYLINE,
    "spread": CanonicalMarketType.FULL_GAME_SPREAD,
    "spreads": CanonicalMarketType.FULL_GAME_SPREAD,
    "total": CanonicalMarketType.FULL_GAME_TOTAL,
    "totals": CanonicalMarketType.FULL_GAME_TOTAL,
    "over_under": CanonicalMarketType.FULL_GAME_TOTAL,
}

# Polymarket league label -> (sport, league). Extend as governed sports grow.
_LEAGUE_MAP = {
    "mlb": ("BASEBALL", "MLB"),
    "nfl": ("FOOTBALL", "NFL"),
    "nba": ("BASKETBALL", "NBA"),
    "nhl": ("HOCKEY", "NHL"),
}


def _to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value if value < 1e12 else value / 1000, tz=UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class PolymarketPredictionMarketSource:
    source = SOURCE
    adapter_version = ADAPTER_VERSION

    def __init__(
        self,
        *,
        gamma_base_url: str = "https://gamma-api.polymarket.com",
        clob_base_url: str = "https://clob.polymarket.com",
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.gamma_base_url = gamma_base_url.rstrip("/")
        self.clob_base_url = clob_base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    async def _get(self, base: str, path: str, params: dict[str, Any] | None = None) -> Any:
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            resp = await client.get(f"{base}{path}", params=params or {})
            resp.raise_for_status()
            return resp.json()
        except SourceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(self.source, f"Polymarket request failed: {type(exc).__name__}") from exc
        finally:
            if self._owns_client:
                await client.aclose()

    # -- discovery -------------------------------------------------------
    async def list_sports(self) -> AdapterResponse:
        raw = await self._get(self.gamma_base_url, "/sports")
        fetched = datetime.now(UTC)
        sports = raw if isinstance(raw, list) else raw.get("sports", []) if isinstance(raw, dict) else []
        return AdapterResponse(
            envelope=SourceEnvelope(source=self.source, fetched_at=fetched, data=sports,
                                    metadata={"count": len(sports)}),
            raw_payload=raw if isinstance(raw, (dict, list)) else {"raw": raw},
        )

    async def discover_contracts(self, *, league: str, limit: int = 100) -> AdapterResponse:
        """Discover active governed game contracts for a league.

        Returns normalized ProviderContractDTO rows (one per selection). Only the
        governed full-game moneyline/spread/total types are emitted; props,
        futures, same-game and non-game markets are skipped.
        """
        params = {"closed": "false", "limit": limit}
        raw = await self._get(self.gamma_base_url, "/markets/keyset", params=params)
        markets = raw.get("data", raw) if isinstance(raw, dict) else raw
        if not isinstance(markets, list):
            markets = []
        contracts: list[ProviderContractDTO] = []
        for market in markets:
            contracts.extend(self._normalize_market(market, league_hint=league))
        fetched = datetime.now(UTC)
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source, fetched_at=fetched,
                data=[c.model_dump(mode="json") for c in contracts],
                metadata={"markets_seen": len(markets), "contracts": len(contracts), "league": league},
            ),
            raw_payload=raw if isinstance(raw, (dict, list)) else {"raw": raw},
        )

    def _normalize_market(self, market: dict[str, Any], *, league_hint: str) -> list[ProviderContractDTO]:
        if not isinstance(market, dict):
            return []
        smt = str(market.get("sportsMarketType") or market.get("sports_market_type") or "").lower()
        market_type = _MARKET_TYPE_MAP.get(smt)
        if market_type is None:
            return []  # non-governed / non-sports market — skip
        league_label = str(market.get("league") or market.get("leagueAbbreviation") or league_hint or "").lower()
        sport, league = _LEAGUE_MAP.get(league_label, ("", league_hint.upper()))
        if not sport:
            sport, league = _LEAGUE_MAP.get(league_hint.lower(), ("", league_hint.upper()))
        event_id = str(market.get("gameId") or market.get("eventId") or market.get("conditionId") or market.get("id") or "")
        market_id = str(market.get("conditionId") or market.get("id") or "")
        if not market_id:
            return []
        home = market.get("homeTeam") or market.get("home_team")
        away = market.get("awayTeam") or market.get("away_team")
        start = _to_dt(market.get("gameStartTime") or market.get("startTime") or market.get("startDate"))
        close = _to_dt(market.get("endDate") or market.get("closedTime"))
        question = market.get("question") or market.get("title")
        rules = market.get("description") or market.get("resolutionSource") or market.get("rules")
        status = "closed" if market.get("closed") else ("active" if market.get("active", True) else "inactive")
        line_value = _to_float(market.get("line") or market.get("spread") or market.get("points"))
        # outcome tokens
        outcomes = self._outcomes(market)
        results: list[ProviderContractDTO] = []
        for token_id, label in outcomes:
            selection = self._selection(market_type, label, home, away)
            if selection is None:
                continue
            results.append(ProviderContractDTO(
                provider=PredictionMarketProvider.POLYMARKET,
                provider_event_id=event_id or market_id,
                provider_market_id=market_id,
                provider_outcome_id=token_id,
                sport=sport, league=league,
                provider_home_team=str(home) if home else None,
                provider_away_team=str(away) if away else None,
                scheduled_start_at=start, provider_game_start_at=start,
                market_type=market_type, selection=selection,
                line_value=line_value if market_type != CanonicalMarketType.FULL_GAME_MONEYLINE else None,
                outcome_label=str(label),
                market_question=str(question) if question else None,
                resolution_rules=str(rules) if rules else None,
                market_close_at=close, settlement_source=market.get("resolutionSource"),
                market_status=status, line_source="STRUCTURED",
                raw={"id": market.get("id"), "slug": market.get("slug"), "sportsMarketType": smt},
            ))
        return results

    @staticmethod
    def _outcomes(market: dict[str, Any]) -> list[tuple[str, str]]:
        """(token_id, label) pairs. Handles both clobTokenIds+outcomes arrays and
        nested outcomes.{yes,no}.tokenId shapes."""
        pairs: list[tuple[str, str]] = []
        tokens = market.get("clobTokenIds") or market.get("clob_token_ids")
        labels = market.get("outcomes")
        if isinstance(tokens, str):
            import json
            try:
                tokens = json.loads(tokens)
            except ValueError:
                tokens = None
        if isinstance(labels, str):
            import json
            try:
                labels = json.loads(labels)
            except ValueError:
                labels = None
        if isinstance(tokens, list) and isinstance(labels, list) and len(tokens) == len(labels):
            return [(str(t), str(l)) for t, l in zip(tokens, labels)]
        nested = market.get("outcomes") if isinstance(market.get("outcomes"), dict) else None
        if nested:
            for key in ("yes", "no"):
                node = nested.get(key) or {}
                tok = node.get("tokenId") or node.get("token_id")
                if tok:
                    pairs.append((str(tok), key.upper()))
        return pairs

    @staticmethod
    def _selection(
        market_type: CanonicalMarketType, label: str, home: Any, away: Any
    ) -> CanonicalSelection | None:
        from app.markets.odds import team_name_match

        text = str(label).strip().lower()
        if market_type == CanonicalMarketType.FULL_GAME_TOTAL:
            if "over" in text:
                return CanonicalSelection.OVER
            if "under" in text:
                return CanonicalSelection.UNDER
            return None
        # moneyline / spread: match to home/away
        if home and team_name_match(str(home), str(label)):
            return CanonicalSelection.HOME
        if away and team_name_match(str(away), str(label)):
            return CanonicalSelection.AWAY
        if text in {"yes", "home"}:
            return CanonicalSelection.HOME
        if text in {"no", "away"}:
            return CanonicalSelection.AWAY
        return None

    # -- order book ------------------------------------------------------
    async def get_order_book(self, token_id: str) -> AdapterResponse:
        raw = await self._get(self.clob_base_url, "/book", params={"token_id": token_id})
        fetched = datetime.now(UTC)
        book = self._normalize_book(raw)
        quote = ProviderQuoteDTO(
            provider=PredictionMarketProvider.POLYMARKET,
            provider_market_id=str(raw.get("market") or ""),
            provider_outcome_id=str(raw.get("asset_id") or token_id),
            book=book, last_trade=_to_float(raw.get("last_trade_price")),
            provider_observed_at=_to_dt(raw.get("timestamp")) or fetched,
            market_status=None, raw={"hash": raw.get("hash"), "tick_size": raw.get("tick_size")},
        )
        return AdapterResponse(
            envelope=SourceEnvelope(source=self.source, fetched_at=fetched,
                                    data=[quote.model_dump(mode="json")], metadata={"token_id": token_id}),
            raw_payload=raw if isinstance(raw, dict) else {"raw": raw},
        )

    @staticmethod
    def _normalize_book(raw: dict[str, Any]) -> NormalizedOrderBook:
        def levels(rows: Any) -> list[OrderBookLevel]:
            out: list[OrderBookLevel] = []
            for row in rows or []:
                price = _to_float(row.get("price") if isinstance(row, dict) else None)
                size = _to_float(row.get("size") if isinstance(row, dict) else None)
                if price is None or size is None or not (0.0 <= price <= 1.0):
                    continue
                out.append(OrderBookLevel(price=round(price, 6), size=size))
            return out

        return NormalizedOrderBook(bids=levels(raw.get("bids")), asks=levels(raw.get("asks")))

    # -- price history (bounded backfill) --------------------------------
    async def get_prices_history(
        self, token_id: str, *, interval: str = "1h", start_ts: int | None = None, end_ts: int | None = None,
        fidelity: int | None = None,
    ) -> AdapterResponse:
        params: dict[str, Any] = {"market": token_id, "interval": interval}
        if start_ts is not None:
            params["startTs"] = start_ts
        if end_ts is not None:
            params["endTs"] = end_ts
        if fidelity is not None:
            params["fidelity"] = fidelity
        raw = await self._get(self.clob_base_url, "/prices-history", params=params)
        history = raw.get("history", []) if isinstance(raw, dict) else []
        points = [{"t": int(p.get("t")), "p": _to_float(p.get("p"))} for p in history if isinstance(p, dict) and p.get("t") is not None]
        fetched = datetime.now(UTC)
        return AdapterResponse(
            envelope=SourceEnvelope(source=self.source, fetched_at=fetched, data=points,
                                    metadata={"token_id": token_id, "points": len(points), "interval": interval}),
            raw_payload=raw if isinstance(raw, dict) else {"raw": raw},
        )


__all__ = ["PolymarketPredictionMarketSource", "SOURCE"]
