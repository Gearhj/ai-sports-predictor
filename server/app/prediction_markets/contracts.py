"""Provider-neutral canonical contracts for the Prediction Market Observer.

Every provider-specific schema is isolated inside its adapter; only these
normalized shapes cross into the rest of the system. Strict models reject
unknown fields. Nothing here fabricates a price, size, or timestamp — a missing
value is ``None`` and is surfaced honestly downstream.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CANONICAL_SNAPSHOT_VERSION = "AISP_PREDICTION_MARKET_SNAPSHOT_V1"
QUALITY_VERSION = "PREDICTION_MARKET_QUALITY_V1"
CONSENSUS_VERSION = "PREDICTION_MARKET_CONSENSUS_V1"
ADAPTER_VERSION = "1.0.0"


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class PredictionMarketProvider(StrEnum):
    POLYMARKET = "POLYMARKET"
    KALSHI = "KALSHI"


class CanonicalMarketType(StrEnum):
    FULL_GAME_MONEYLINE = "FULL_GAME_MONEYLINE"
    FULL_GAME_SPREAD = "FULL_GAME_SPREAD"
    FULL_GAME_TOTAL = "FULL_GAME_TOTAL"


class CanonicalSelection(StrEnum):
    HOME = "HOME"
    AWAY = "AWAY"
    OVER = "OVER"
    UNDER = "UNDER"


class MappingStatus(StrEnum):
    EXACT = "EXACT"
    MANUAL_APPROVED = "MANUAL_APPROVED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"


ACCEPTED_MAPPING_STATUSES = frozenset({MappingStatus.EXACT, MappingStatus.MANUAL_APPROVED})


class SemanticsStatus(StrEnum):
    SEMANTICS_MATCHED = "SEMANTICS_MATCHED"
    SEMANTICS_REVIEW_REQUIRED = "SEMANTICS_REVIEW_REQUIRED"
    SEMANTICS_MISMATCH = "SEMANTICS_MISMATCH"
    SEMANTICS_UNKNOWN = "SEMANTICS_UNKNOWN"


class QualityTier(StrEnum):
    ROBUST = "ROBUST"
    USABLE = "USABLE"
    THIN = "THIN"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"
    UNMAPPED = "UNMAPPED"
    SEMANTICS_UNREVIEWED = "SEMANTICS_UNREVIEWED"


class TimelineCut(StrEnum):
    FIRST_OBSERVED = "FIRST_OBSERVED"
    T_MINUS_24_HOURS = "T_MINUS_24_HOURS"
    T_MINUS_12_HOURS = "T_MINUS_12_HOURS"
    T_MINUS_6_HOURS = "T_MINUS_6_HOURS"
    T_MINUS_4_HOURS = "T_MINUS_4_HOURS"
    T_MINUS_2_HOURS = "T_MINUS_2_HOURS"
    T_MINUS_60_MINUTES = "T_MINUS_60_MINUTES"
    T_MINUS_30_MINUTES = "T_MINUS_30_MINUTES"
    T_MINUS_10_MINUTES = "T_MINUS_10_MINUTES"
    LAST_PREGAME = "LAST_PREGAME"
    MARKET_CLOSE = "MARKET_CLOSE"
    IN_PLAY_FIRST_OBSERVED = "IN_PLAY_FIRST_OBSERVED"


# Minutes-before-start for each pregame cut (None => special handling).
TIMELINE_CUT_OFFSETS_MINUTES: dict[TimelineCut, int] = {
    TimelineCut.T_MINUS_24_HOURS: 24 * 60,
    TimelineCut.T_MINUS_12_HOURS: 12 * 60,
    TimelineCut.T_MINUS_6_HOURS: 6 * 60,
    TimelineCut.T_MINUS_4_HOURS: 4 * 60,
    TimelineCut.T_MINUS_2_HOURS: 2 * 60,
    TimelineCut.T_MINUS_60_MINUTES: 60,
    TimelineCut.T_MINUS_30_MINUTES: 30,
    TimelineCut.T_MINUS_10_MINUTES: 10,
}


class AlertType(StrEnum):
    PREDICTION_MARKET_MOVE = "PREDICTION_MARKET_MOVE"
    CROSS_VENUE_CONFIRMATION = "CROSS_VENUE_CONFIRMATION"
    CROSS_VENUE_DIVERGENCE = "CROSS_VENUE_DIVERGENCE"
    SPORTSBOOK_PREDICTION_MARKET_DIVERGENCE = "SPORTSBOOK_PREDICTION_MARKET_DIVERGENCE"
    AISP_MARKET_DISAGREEMENT = "AISP_MARKET_DISAGREEMENT"
    THIN_MARKET_WARNING = "THIN_MARKET_WARNING"
    STALE_MARKET_WARNING = "STALE_MARKET_WARNING"
    CONTRACT_RULE_MISMATCH = "CONTRACT_RULE_MISMATCH"
    MAPPING_REVIEW_REQUIRED = "MAPPING_REVIEW_REQUIRED"
    PREGAME_MARKET_CLOSED = "PREGAME_MARKET_CLOSED"
    PROVIDER_STREAM_DEGRADED = "PROVIDER_STREAM_DEGRADED"


# --------------------------------------------------------------------------
# Order book (normalized 0..1 probability scale)
# --------------------------------------------------------------------------
class OrderBookLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    price: float  # probability in [0, 1]
    size: float   # contracts / shares


class NormalizedOrderBook(BaseModel):
    """A two-sided book on the 0..1 probability scale for ONE selection.

    ``bids`` are prices someone will pay to buy the selection (best = highest);
    ``asks`` are prices someone will sell at (best = lowest). For Kalshi these
    are derived from YES/NO bids; a missing opposite side yields no ask rather
    than a fabricated value.
    """

    model_config = ConfigDict(extra="forbid")
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)
    # Kalshi provenance (raw, before ask derivation); None for Polymarket.
    raw_yes_bids: list[OrderBookLevel] | None = None
    raw_no_bids: list[OrderBookLevel] | None = None
    provider_sequence: int | None = None

    def best_bid(self) -> float | None:
        return max((lvl.price for lvl in self.bids), default=None)

    def best_ask(self) -> float | None:
        return min((lvl.price for lvl in self.asks), default=None)

    def top_bid_size(self) -> float | None:
        if not self.bids:
            return None
        return max(self.bids, key=lambda lvl: lvl.price).size

    def top_ask_size(self) -> float | None:
        if not self.asks:
            return None
        return min(self.asks, key=lambda lvl: lvl.price).size


class PriceView(BaseModel):
    """Distinct price definitions — never conflated (section 12)."""

    model_config = ConfigDict(extra="forbid")
    best_bid: float | None = None            # executable SELL price
    best_ask: float | None = None            # executable BUY price
    midpoint: float | None = None            # (bid+ask)/2 only when both exist
    microprice: float | None = None          # size-weighted top of book
    last_trade: float | None = None
    bid_ask_spread: float | None = None
    top_bid_size: float | None = None
    top_ask_size: float | None = None
    book_complete: bool = False


def price_view(book: NormalizedOrderBook, *, last_trade: float | None = None) -> PriceView:
    """Derive the distinct prices from a normalized book. No fabrication."""
    bid, ask = book.best_bid(), book.best_ask()
    tb, ta = book.top_bid_size(), book.top_ask_size()
    both = bid is not None and ask is not None
    midpoint = round((bid + ask) / 2, 6) if both else None
    microprice = None
    if both and tb is not None and ta is not None and (tb + ta) > 0:
        # Size-weighted toward the side with more resting size.
        microprice = round((bid * ta + ask * tb) / (tb + ta), 6)
    spread = round(ask - bid, 6) if both else None
    return PriceView(
        best_bid=bid, best_ask=ask, midpoint=midpoint, microprice=microprice,
        last_trade=last_trade, bid_ask_spread=spread, top_bid_size=tb, top_ask_size=ta,
        book_complete=both,
    )


def normalize_kalshi_book(
    yes_bids: list[tuple[float, float]],
    no_bids: list[tuple[float, float]],
    *,
    provider_sequence: int | None = None,
) -> NormalizedOrderBook:
    """Kalshi returns YES bids and NO bids only. Derive the implied asks on a
    0..1 scale: yes_ask = 1 - best_no_bid, no equivalent asks are fabricated.

    We express the book from the YES/HOME perspective (the canonical selection's
    price is a YES price). bids = YES bids; asks derived from NO bids as
    (1 - no_price). Validates 0 <= price <= 1 and drops malformed levels.
    """

    def clean(levels: list[tuple[float, float]]) -> list[OrderBookLevel]:
        out: list[OrderBookLevel] = []
        for price, size in levels:
            if price is None or size is None:
                continue
            if not (0.0 <= price <= 1.0) or size < 0:
                continue
            out.append(OrderBookLevel(price=round(price, 6), size=size))
        return out

    yes = clean(yes_bids)
    no = clean(no_bids)
    # A NO bid at price p is an offer to SELL YES at (1 - p) => a YES ask.
    asks = [OrderBookLevel(price=round(1.0 - lvl.price, 6), size=lvl.size) for lvl in no]
    return NormalizedOrderBook(
        bids=yes, asks=asks, raw_yes_bids=yes, raw_no_bids=no, provider_sequence=provider_sequence,
    )


# --------------------------------------------------------------------------
# Discovery / contract DTO produced by an adapter (pre-mapping identity)
# --------------------------------------------------------------------------
class ProviderContractDTO(BaseModel):
    """One governed, normalized game contract from a provider (no mapping yet)."""

    model_config = ConfigDict(extra="forbid")
    provider: PredictionMarketProvider
    provider_event_id: str
    provider_market_id: str
    provider_outcome_id: str | None = None  # token_id (Polymarket) / ticker side
    sport: str
    league: str
    provider_home_team: str | None = None
    provider_away_team: str | None = None
    scheduled_start_at: datetime | None = None
    provider_game_start_at: datetime | None = None
    market_type: CanonicalMarketType
    selection: CanonicalSelection
    line_value: float | None = None
    outcome_label: str
    # contract semantics
    market_question: str | None = None
    market_subtitle: str | None = None
    resolution_rules: str | None = None
    market_close_at: datetime | None = None
    settlement_source: str | None = None
    market_status: str | None = None
    line_source: str = "STRUCTURED"  # or TITLE_PARSED
    raw: dict[str, Any] = Field(default_factory=dict)

    def rules_sha256(self) -> str | None:
        if not self.resolution_rules:
            return None
        return hashlib.sha256(self.resolution_rules.encode("utf-8")).hexdigest()


class ProviderQuoteDTO(BaseModel):
    """A point-in-time observation for one contract: book + trade + provider meta."""

    model_config = ConfigDict(extra="forbid")
    provider: PredictionMarketProvider
    provider_market_id: str
    provider_outcome_id: str | None = None
    book: NormalizedOrderBook
    last_trade: float | None = None
    last_trade_at: datetime | None = None
    volume: float | None = None
    open_interest: float | None = None
    liquidity: float | None = None
    provider_observed_at: datetime
    provider_sequence: int | None = None
    market_status: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def payload_sha256(self) -> str:
        return sha256_payload(self.raw or {
            "m": self.provider_market_id, "o": self.provider_outcome_id,
            "bids": [(l.price, l.size) for l in self.book.bids],
            "asks": [(l.price, l.size) for l in self.book.asks],
            "lt": self.last_trade, "vol": self.volume, "oi": self.open_interest,
        })


def sha256_payload(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


# --------------------------------------------------------------------------
# Quality — PREDICTION_MARKET_QUALITY_V1 (transparent components, no opaque score)
# --------------------------------------------------------------------------
class QualityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = QUALITY_VERSION
    tier: QualityTier
    quote_fresh: bool
    book_complete: bool
    market_liquid: bool
    quote_age_seconds: float | None
    bid_ask_spread: float | None
    top_depth: float | None
    volume: float | None
    open_interest: float | None
    components: dict[str, Any]
    flags: list[str]


def assess_quality(
    *,
    price: PriceView,
    volume: float | None,
    open_interest: float | None,
    quote_age_seconds: float | None,
    mapped: bool,
    semantics_reviewed: bool,
    stale_seconds: int,
    max_spread: float,
    min_depth: float,
    in_play: bool = False,
) -> QualityAssessment:
    """Descriptive, fully transparent quality. Precedence favors honesty: an
    unmapped or semantics-unreviewed contract can never be ROBUST."""

    flags: list[str] = []
    fresh = quote_age_seconds is not None and quote_age_seconds <= stale_seconds
    if not fresh:
        flags.append("STALE_QUOTE")
    complete = price.book_complete
    if not complete:
        flags.append("INCOMPLETE_BOOK")
    top_depth = None
    if price.top_bid_size is not None and price.top_ask_size is not None:
        top_depth = price.top_bid_size + price.top_ask_size
    elif price.top_bid_size is not None:
        top_depth = price.top_bid_size
    liquid = top_depth is not None and top_depth >= min_depth
    spread_ok = price.bid_ask_spread is not None and price.bid_ask_spread <= max_spread
    if not liquid:
        flags.append("THIN_DEPTH")
    if price.bid_ask_spread is not None and not spread_ok:
        flags.append("WIDE_SPREAD")
    if in_play:
        flags.append("IN_PLAY")

    if not mapped:
        tier = QualityTier.UNMAPPED
    elif not semantics_reviewed:
        tier = QualityTier.SEMANTICS_UNREVIEWED
    elif not complete:
        tier = QualityTier.INCOMPLETE
    elif not fresh:
        tier = QualityTier.STALE
    elif liquid and spread_ok:
        tier = QualityTier.ROBUST
    elif spread_ok or liquid:
        tier = QualityTier.USABLE
    else:
        tier = QualityTier.THIN

    return QualityAssessment(
        tier=tier, quote_fresh=fresh, book_complete=complete, market_liquid=liquid,
        quote_age_seconds=quote_age_seconds, bid_ask_spread=price.bid_ask_spread,
        top_depth=top_depth, volume=volume, open_interest=open_interest,
        components={
            "stale_seconds_threshold": stale_seconds, "max_spread_threshold": max_spread,
            "min_depth_threshold": min_depth, "fresh": fresh, "complete": complete,
            "liquid": liquid, "spread_ok": spread_ok, "in_play": in_play,
            "formula": "tier = ROBUST if mapped & semantics_reviewed & complete & fresh "
                       "& liquid & spread_ok; degrades by the first failed gate",
        },
        flags=flags,
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


def timeline_cut_for(observed_at: datetime, game_starts_at: datetime | None) -> str | None:
    """The tightest pregame cut an observation qualifies for (latest at/before).

    Never returns a pregame cut for a post-start observation."""
    if game_starts_at is None:
        return None
    obs = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=UTC)
    start = game_starts_at if game_starts_at.tzinfo else game_starts_at.replace(tzinfo=UTC)
    if obs >= start:
        return TimelineCut.IN_PLAY_FIRST_OBSERVED.value
    minutes_to_start = (start - obs).total_seconds() / 60.0
    # Pick the tightest (smallest offset) cut the observation is still at/before.
    best: TimelineCut | None = None
    best_offset = None
    for cut, offset in TIMELINE_CUT_OFFSETS_MINUTES.items():
        if minutes_to_start <= offset and (best_offset is None or offset < best_offset):
            best, best_offset = cut, offset
    return best.value if best else None


__all__ = [
    "CANONICAL_SNAPSHOT_VERSION", "QUALITY_VERSION", "CONSENSUS_VERSION", "ADAPTER_VERSION",
    "PredictionMarketProvider", "CanonicalMarketType", "CanonicalSelection",
    "MappingStatus", "ACCEPTED_MAPPING_STATUSES", "SemanticsStatus", "QualityTier",
    "TimelineCut", "TIMELINE_CUT_OFFSETS_MINUTES", "AlertType",
    "OrderBookLevel", "NormalizedOrderBook", "PriceView", "price_view",
    "normalize_kalshi_book", "ProviderContractDTO", "ProviderQuoteDTO",
    "QualityAssessment", "assess_quality", "sha256_payload", "utc_now", "timeline_cut_for",
]
