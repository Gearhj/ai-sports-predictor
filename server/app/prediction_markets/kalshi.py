"""Kalshi adapter — official PUBLIC REST market data (no API key required).

Baseline path is credential-free REST (verified live 2026-09-04 against
``https://api.elections.kalshi.com/trade-api/v2``): ``/series/{ticker}``,
``/events?series_ticker=&status=&with_nested_markets=true&cursor=`` (cursor
pagination), ``/markets/{ticker}/orderbook`` (``orderbook_fp`` dollars shape,
legacy ``orderbook`` cents shape also accepted) and ``/historical/trades``.
Authentication is only relevant to the optional authenticated WebSocket,
which stays behind a separate disabled read-only flag; it is never a
prerequisite and absent credentials never disable the public path. No
trading endpoint exists in this module.

Game identity comes from the event ticker, which Kalshi lays out as
``KX<LEAGUE>GAME-<YYMMMDDHHMM><AWAY><HOME>[G<n>]`` in US/Eastern local time
(``KXMLBGAME-26SEP041915DETCLEG2`` = 2026-09-04 19:15 ET, DET at CLE, game 2),
cross-checked against ``rules_primary`` ("... originally scheduled for Sep 4,
2026 at 7:15 PM EDT ..."). Team labels resolve only through the explicit alias
registry; the YES side of each market is the team named by the ticker suffix.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.data_sources.base import AdapterResponse, SourceEnvelope, SourceError
from app.prediction_markets.aliases import kalshi_name_to_aisp, kalshi_ticker_to_aisp, split_kalshi_team_pair
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
PUBLIC_REST_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_LOCAL_TZ = ZoneInfo("America/New_York")

_EVENT_TICKER = re.compile(r"^(?P<series>KX[A-Z]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hh>\d{2})(?P<mm>\d{2})(?P<teams>[A-Z]+?)(?:G(?P<game>\d))?$")
_RULES_WHEN = re.compile(r"originally scheduled for (?P<when>[A-Z][a-z]{2} \d{1,2}, \d{4} at \d{1,2}:\d{2} [AP]M) E[DS]T")
_MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}

# Series families per league prefix. Only GAME / SPREAD / TOTAL are full-game
# markets; everything else is a prop, partial-game, futures or combo family.
_FULL_GAME_FAMILIES = {"GAME": "MONEYLINE", "SPREAD": "SPREAD", "TOTAL": "TOTAL"}
_PARTIAL_FAMILIES = {"INNINGWIN", "RFI", "TEAMTOTAL", "1H", "1Q", "F5", "F5SPREAD", "F5TOTAL", "F5ML", "FIRSTINNING"}
_FUTURES_FAMILIES = {"WINS", "CHAMP", "DIV", "PLAYOFF", "MVP", "CY", "SERIES", "PENNANT", "WS", "ROY"}
_PROP_FAMILIES = {"HIT", "TB", "ERA", "SB", "K", "HR", "RBI", "RUNS", "PITCHK", "OUTS", "BB", "SO", "HITS"}


def _to_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # Kalshi prices come as dollars (0.15, "0.1500") or legacy cents (15).
    return f / 100.0 if f > 1.0 else f


def _to_size(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value if value < 1e12 else value / 1000, tz=UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def classify_series(series_ticker: str, league: str) -> str:
    """MONEYLINE / SPREAD / TOTAL / PARTIAL_GAME / PLAYER_PROP / FUTURES / COMBO /
    OTHER_LEAGUE for a Kalshi series ticker (``KXMLBGAME`` ...)."""
    head = series_ticker.upper().split("-", 1)[0]
    prefix = f"KX{league.upper()}"
    if not head.startswith(prefix):
        return "OTHER_LEAGUE"
    family = head[len(prefix):]
    if family in _FULL_GAME_FAMILIES:
        return _FULL_GAME_FAMILIES[family]
    if family in _PARTIAL_FAMILIES or family.startswith("F5") or family[:1].isdigit() or "INNING" in family:
        return "PARTIAL_GAME"
    if family in _FUTURES_FAMILIES or family.endswith("WINS"):
        return "FUTURES"
    if "COMBO" in family or "PARLAY" in family or "MULTI" in family:
        return "COMBO"
    if family in _PROP_FAMILIES:
        return "PLAYER_PROP"
    return "PLAYER_PROP"


def parse_event_ticker(event_ticker: str) -> dict[str, Any] | None:
    """Decode a game event ticker: start (ET, tz-aware), away/home ticker
    abbreviations and doubleheader game number. None when not a game ticker."""
    m = _EVENT_TICKER.match(event_ticker.strip().upper())
    if not m:
        return None
    pair = split_kalshi_team_pair(m.group("teams"))
    if pair is None:
        return None
    try:
        start = datetime(2000 + int(m.group("yy")), _MONTHS[m.group("mon")], int(m.group("dd")),
                         int(m.group("hh")), int(m.group("mm")), tzinfo=KALSHI_LOCAL_TZ)
    except (KeyError, ValueError):
        return None
    return {"series": m.group("series"), "starts_at": start, "away_ticker": pair[0], "home_ticker": pair[1],
            "game_number": int(m.group("game")) if m.group("game") else None}


def parse_rules_start(rules: str | None) -> datetime | None:
    """Start time stated in rules_primary ("Sep 4, 2026 at 7:15 PM EDT"), ET."""
    m = _RULES_WHEN.search(rules or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group("when"), "%b %d, %Y at %I:%M %p").replace(tzinfo=KALSHI_LOCAL_TZ)
    except ValueError:
        return None


class KalshiPredictionMarketSource:
    source = SOURCE
    adapter_version = ADAPTER_VERSION

    def __init__(
        self,
        *,
        rest_base_url: str = PUBLIC_REST_BASE_URL,
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
        # Credentials are only consulted for the optional WS path; never used
        # for REST, never logged, never serialized, never returned through an API.
        self._api_key_id = api_key_id
        self._private_key_path = private_key_path
        self._ws_enabled = ws_enabled
        self.request_count = 0

    @property
    def connection_mode(self) -> str:
        if self._ws_enabled and self._api_key_id and self._private_key_path:
            return "STREAMING"
        return "REST_ONLY"

    @property
    def requires_credentials(self) -> bool:
        return False

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            self.request_count += 1
            # Public market data: no Authorization header is ever attached.
            resp = await client.get(f"{self.rest_base_url}{path}", params=params or {},
                                    headers={"Accept": "application/json", "User-Agent": "aisp-research-observer/1.0"})
            resp.raise_for_status()
            return resp.json()
        except SourceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(self.source, f"Kalshi request failed: {type(exc).__name__}") from exc
        finally:
            if self._owns_client:
                await client.aclose()

    # -- series verification ----------------------------------------------
    async def get_series(self, series_ticker: str) -> AdapterResponse:
        raw = await self._get(f"/series/{series_ticker}")
        series = raw.get("series", raw) if isinstance(raw, dict) else {}
        fetched = datetime.now(UTC)
        return AdapterResponse(
            envelope=SourceEnvelope(source=self.source, fetched_at=fetched, data=[series],
                                    metadata={"series_ticker": series_ticker, "mode": self.connection_mode,
                                              "classification": classify_series(series_ticker, "MLB")}),
            raw_payload=raw if isinstance(raw, dict) else {"raw": raw},
        )

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

    # -- discovery: events with nested markets, cursor pagination -----------
    async def discover_game_events(
        self, *, series_ticker: str, league: str, sport: str, statuses: tuple[str, ...] = ("open",),
        page_limit: int = 200, max_pages: int = 10, not_before: datetime | None = None,
    ) -> AdapterResponse:
        """Enumerate every event of a game series (all pages, each requested
        status) and normalize each nested market into a contract with explicit
        home/away identity, ticker-derived start time and YES-side orientation.

        The nested market objects also carry the provider's top of book
        (``yes_bid_dollars`` / ``yes_ask_dollars`` / ``last_price_dollars``) so
        the same response yields a top-of-book quote per market without a
        per-market request; the envelope carries them under ``quotes``."""
        contracts: list[ProviderContractDTO] = []
        quotes: list[dict[str, Any]] = []
        excluded: dict[str, int] = {}
        events_seen = markets_seen = pages = 0
        queries: list[dict[str, Any]] = []
        for status in statuses:
            cursor: str | None = None
            for _ in range(max_pages):
                params: dict[str, Any] = {"series_ticker": series_ticker, "status": status,
                                          "with_nested_markets": "true", "limit": page_limit}
                if cursor:
                    params["cursor"] = cursor
                raw = await self._get("/events", params=params)
                queries.append({"path": "/events", "params": dict(params)})
                pages += 1
                events = raw.get("events", []) if isinstance(raw, dict) else []
                for event in events:
                    if not isinstance(event, dict):
                        excluded["malformed_record"] = excluded.get("malformed_record", 0) + 1
                        continue
                    events_seen += 1
                    parsed = self._normalize_event(event, league=league, sport=sport, excluded=excluded)
                    markets_seen += len(event.get("markets") or [])
                    for dto, quote in parsed:
                        contracts.append(dto)
                        if quote is not None:
                            quotes.append(quote)
                cursor = raw.get("cursor") if isinstance(raw, dict) else None
                if not cursor or not events:
                    break
                # Listings are newest-first: once a whole page predates the
                # window of interest (settled reconstruction), stop paging.
                if not_before is not None and events:
                    dates = [parse_event_ticker(str(e.get("event_ticker") or "")) for e in events if isinstance(e, dict)]
                    starts = [d["starts_at"] for d in dates if d]
                    if starts and max(starts) < not_before:
                        break
        fetched = datetime.now(UTC)
        return AdapterResponse(
            envelope=SourceEnvelope(
                source=self.source, fetched_at=fetched,
                data=[c.model_dump(mode="json") for c in contracts],
                metadata={"events_seen": events_seen, "markets_seen": markets_seen, "contracts": len(contracts),
                          "pages": pages, "series_ticker": series_ticker, "statuses": list(statuses),
                          "mode": self.connection_mode, "excluded": excluded, "quotes": quotes,
                          "query": {"path": "/events", "requests": queries}, "credentials_used": False},
            ),
            raw_payload={"pages": pages},
        )

    def _normalize_event(self, event: dict[str, Any], *, league: str, sport: str, excluded: dict[str, int]) -> list[tuple[ProviderContractDTO, dict[str, Any] | None]]:
        event_ticker = str(event.get("event_ticker") or "")
        series_ticker = str(event.get("series_ticker") or event_ticker.split("-", 1)[0])
        family = classify_series(series_ticker, league)
        if family not in _FULL_GAME_FAMILIES.values():
            excluded[_bucket(family)] = excluded.get(_bucket(family), 0) + len(event.get("markets") or [])
            return []
        identity = parse_event_ticker(event_ticker)
        if identity is None:
            excluded["unparseable_event_ticker"] = excluded.get("unparseable_event_ticker", 0) + len(event.get("markets") or [])
            return []
        home_abbr = kalshi_ticker_to_aisp(identity["home_ticker"])
        away_abbr = kalshi_ticker_to_aisp(identity["away_ticker"])
        if home_abbr is None or away_abbr is None:
            excluded["unregistered_team_alias"] = excluded.get("unregistered_team_alias", 0) + len(event.get("markets") or [])
            return []
        out: list[tuple[ProviderContractDTO, dict[str, Any] | None]] = []
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                excluded["malformed_record"] = excluded.get("malformed_record", 0) + 1
                continue
            ticker = str(market.get("ticker") or "")
            if not ticker.startswith(event_ticker + "-"):
                excluded["missing_identifiers"] = excluded.get("missing_identifiers", 0) + 1
                continue
            yes_ticker = ticker[len(event_ticker) + 1:]
            yes_abbr = kalshi_ticker_to_aisp(yes_ticker)
            yes_label = str(market.get("yes_sub_title") or market.get("title") or yes_ticker)
            if yes_abbr is None:
                yes_abbr = kalshi_name_to_aisp(yes_label)
            if family == "MONEYLINE":
                if yes_abbr == home_abbr:
                    selection = CanonicalSelection.HOME
                elif yes_abbr == away_abbr:
                    selection = CanonicalSelection.AWAY
                else:
                    excluded["unmatched_outcome_labels"] = excluded.get("unmatched_outcome_labels", 0) + 1
                    continue
                market_type, line = CanonicalMarketType.FULL_GAME_MONEYLINE, None
            elif family == "SPREAD":
                market_type, line = CanonicalMarketType.FULL_GAME_SPREAD, self._strike(market)
                selection = CanonicalSelection.HOME if yes_abbr == home_abbr else CanonicalSelection.AWAY
            else:
                market_type, line = CanonicalMarketType.FULL_GAME_TOTAL, self._strike(market)
                selection = CanonicalSelection.OVER
            rules = str(market.get("rules_primary") or "")
            rules_start = parse_rules_start(rules)
            starts_at = identity["starts_at"]
            dto = ProviderContractDTO(
                provider=PredictionMarketProvider.KALSHI,
                provider_event_id=event_ticker, provider_market_id=ticker, provider_outcome_id=f"{ticker}:YES",
                sport=sport, league=league,
                provider_home_team=home_abbr, provider_away_team=away_abbr,
                scheduled_start_at=starts_at, provider_game_start_at=rules_start or starts_at,
                market_type=market_type, selection=selection, line_value=line,
                outcome_label=yes_label,
                market_question=str(market.get("title") or "") or None,
                market_subtitle=str(event.get("sub_title") or market.get("yes_sub_title") or "") or None,
                resolution_rules=rules or None,
                market_close_at=_to_dt(market.get("close_time")),
                settlement_source=", ".join(s.get("name", "") for s in (event.get("settlement_sources") or []) if isinstance(s, dict)) or None,
                market_status=str(market.get("status") or ""),
                line_source="STRUCTURED",
                raw={
                    "series_ticker": series_ticker, "event_ticker": event_ticker, "yes_team_ticker": yes_ticker,
                    "yes_team_abbr": yes_abbr, "home_abbr": home_abbr, "away_abbr": away_abbr,
                    "doubleheader_game_number": identity["game_number"],
                    "ticker_start_local": starts_at.isoformat(),
                    "rules_start_local": rules_start.isoformat() if rules_start else None,
                    "start_time_conflict": bool(rules_start and rules_start != starts_at),
                    "event_title": event.get("title"), "credentials_used": False,
                },
            )
            out.append((dto, self._top_of_book(market, ticker)))
        return out

    @staticmethod
    def _top_of_book(market: dict[str, Any], ticker: str) -> dict[str, Any] | None:
        """Top-of-book quote from the market object. Bid/ask are preserved raw;
        the midpoint is only derived by the price view when both sides exist."""
        bid = _to_float(market.get("yes_bid_dollars") if market.get("yes_bid_dollars") is not None else market.get("yes_bid"))
        ask = _to_float(market.get("yes_ask_dollars") if market.get("yes_ask_dollars") is not None else market.get("yes_ask"))
        last = _to_float(market.get("last_price_dollars") if market.get("last_price_dollars") is not None else market.get("last_price"))
        bid_size = _to_size(market.get("yes_bid_size_fp") if market.get("yes_bid_size_fp") is not None else market.get("yes_bid_size"))
        ask_size = _to_size(market.get("yes_ask_size_fp") if market.get("yes_ask_size_fp") is not None else market.get("yes_ask_size"))
        if bid is None and ask is None and last is None:
            return None
        yes_bids = [(bid, bid_size if bid_size is not None else 0.0)] if bid is not None and bid > 0 else []
        # A YES ask at a is a NO bid at (1 - a): feed the normalizer its native shape.
        no_bids = [(round(1.0 - ask, 6), ask_size if ask_size is not None else 0.0)] if ask is not None and 0 < ask < 1 else []
        book = normalize_kalshi_book(yes_bids, no_bids)
        volume = _to_size(market.get("volume_fp") if market.get("volume_fp") is not None else market.get("volume"))
        oi = _to_size(market.get("open_interest_fp") if market.get("open_interest_fp") is not None else market.get("open_interest"))
        liquidity = _to_size(market.get("liquidity_dollars") if market.get("liquidity_dollars") is not None else market.get("liquidity"))
        quote = ProviderQuoteDTO(
            provider=PredictionMarketProvider.KALSHI, provider_market_id=ticker, provider_outcome_id=f"{ticker}:YES",
            book=book, last_trade=last, volume=volume, open_interest=oi, liquidity=liquidity,
            provider_observed_at=datetime.now(UTC), market_status=str(market.get("status") or "") or None,
            raw={"ticker": ticker, "yes_bid": bid, "yes_ask": ask, "last": last, "bid_size": bid_size, "ask_size": ask_size,
                 "source": "events.nested_markets", "one_sided": bool(bid is None) != bool(ask is None),
                 "updated_time": market.get("updated_time")},
        )
        return quote.model_dump(mode="json")

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

    # -- compatibility helpers (classification audits, single-market normalize) --
    @classmethod
    def _series_family(cls, series_ticker: str, league: str) -> str | None:
        family = classify_series(series_ticker, league)
        return family if family in {"MONEYLINE", "SPREAD", "TOTAL"} else None

    @classmethod
    def _exclusion_reason(cls, market: Any, league: str) -> str:
        if not isinstance(market, dict):
            return "malformed_record"
        ticker = str(market.get("series_ticker") or market.get("ticker") or "")
        if not ticker:
            return "missing_identifiers"
        family = classify_series(ticker, league)
        if family in {"MONEYLINE", "SPREAD", "TOTAL"}:
            return "missing_identifiers"
        return _bucket(family)

    def _normalize_market(self, market: dict[str, Any], *, league: str, sport: str) -> ProviderContractDTO | None:
        """Normalize one market object (the events path is the production path;
        this keeps the single-market contract for audits and tests)."""
        if not isinstance(market, dict):
            return None
        event_ticker = str(market.get("event_ticker") or "")
        event = {"event_ticker": event_ticker, "series_ticker": market.get("series_ticker") or event_ticker.split("-", 1)[0],
                 "markets": [market], "title": market.get("title"), "sub_title": market.get("subtitle")}
        excluded: dict[str, int] = {}
        out = self._normalize_event(event, league=league, sport=sport, excluded=excluded)
        return out[0][0] if out else None

    # -- legacy market listing kept for classification / exclusion audits ---
    async def discover_contracts(self, *, series_ticker: str, league: str, sport: str, limit: int = 100) -> AdapterResponse:
        return await self.discover_game_events(series_ticker=series_ticker, league=league, sport=sport, page_limit=min(limit, 200))

    # -- order book (YES/NO bids -> normalized two-sided 0..1 book) -------
    async def get_order_book(self, ticker: str, *, depth: int = 10) -> AdapterResponse:
        raw = await self._get(f"/markets/{ticker}/orderbook", params={"depth": depth})
        fetched = datetime.now(UTC)
        ob = {}
        if isinstance(raw, dict):
            ob = raw.get("orderbook_fp") or raw.get("orderbook") or {}
        yes = self._levels(ob.get("yes_dollars") if "yes_dollars" in ob else ob.get("yes"))
        no = self._levels(ob.get("no_dollars") if "no_dollars" in ob else ob.get("no"))
        book = normalize_kalshi_book(yes, no)
        quote = ProviderQuoteDTO(
            provider=PredictionMarketProvider.KALSHI,
            provider_market_id=ticker, provider_outcome_id=f"{ticker}:YES",
            book=book, last_trade=None, provider_observed_at=fetched,
            market_status=None, raw={"depth": depth, "source": "orderbook", "one_sided": bool(yes) != bool(no),
                                     "yes_levels": len(yes), "no_levels": len(no)},
        )
        return AdapterResponse(
            envelope=SourceEnvelope(source=self.source, fetched_at=fetched,
                                    data=[quote.model_dump(mode="json")],
                                    metadata={"ticker": ticker, "mode": self.connection_mode, "credentials_used": False}),
            raw_payload=raw if isinstance(raw, dict) else {"raw": raw},
        )

    @staticmethod
    def _levels(rows: Any) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for row in rows or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                price, size = _to_float(row[0]), _to_size(row[1])
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


def _bucket(family: str) -> str:
    return {"PARTIAL_GAME": "partial_game_or_derivative", "FUTURES": "futures_or_season", "COMBO": "combo_or_multivariate",
            "OTHER_LEAGUE": "other_league_or_sport", "PLAYER_PROP": "player_props"}.get(family, "non_governed_market_type")


__all__ = ["KalshiPredictionMarketSource", "SOURCE", "PUBLIC_REST_BASE_URL", "classify_series", "parse_event_ticker", "parse_rules_start"]
