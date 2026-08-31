"""Public, database-only response schemas for the Prediction Market Observer.

These carry research-observation data only. There is no order, stake, execution,
credential, or model-input field anywhere in this contract.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

DataMode = Literal["live", "empty", "demo", "unavailable"]


class ProviderProbability(BaseModel):
    provider: str
    midpoint: float | None = None
    microprice: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    last_trade: float | None = None
    bid_ask_spread: float | None = None
    volume: float | None = None
    open_interest: float | None = None
    top_depth: float | None = None
    quality_tier: str
    quote_age_seconds: float | None = None
    mapping_status: str
    semantics_status: str
    in_play: bool = False
    observed_at: datetime | None = None


class MarketPulseRow(BaseModel):
    game_id: str
    sport: str
    league: str
    home_team: str | None = None
    away_team: str | None = None
    home_abbrev: str | None = None
    away_abbrev: str | None = None
    starts_at: datetime | None = None
    status: str
    aisp_home_probability: float | None = None
    aisp_forecast_fresh: bool = False
    sportsbook_home_probability: float | None = None
    polymarket_home_probability: float | None = None
    kalshi_home_probability: float | None = None
    research_consensus_home_probability: float | None = None
    consensus_eligible: bool = False
    consensus_note: str | None = None
    diff_aisp_sportsbook: float | None = None
    diff_aisp_prediction: float | None = None
    diff_polymarket_kalshi: float | None = None
    cross_market_dispersion: float | None = None
    providers: list[ProviderProbability] = []
    movement_5m: float | None = None
    movement_30m: float | None = None
    movement_2h: float | None = None
    movement_24h: float | None = None
    first_mover: str | None = None
    mapping_status: str = "UNRESOLVED"
    semantics_status: str = "SEMANTICS_UNKNOWN"
    best_quality_tier: str | None = None
    alert_count: int = 0


class MarketPulseBoard(BaseModel):
    data_mode: DataMode
    date: date
    sport: str | None = None
    sports_available: list[str] = []
    disclosure: str = "RESEARCH OBSERVER · NO TRADING · NOT A MODEL INPUT · NOT A GUARANTEE"
    public_display_enabled: bool = False
    rows: list[MarketPulseRow] = []
    summary: dict[str, Any] = {}


class TimelinePoint(BaseModel):
    time: str
    aisp: float | None = None
    sportsbook: float | None = None
    polymarket: float | None = None
    kalshi: float | None = None
    consensus: float | None = None


class OrderBookView(BaseModel):
    provider: str
    best_bid: float | None = None
    best_ask: float | None = None
    midpoint: float | None = None
    microprice: float | None = None
    spread: float | None = None
    bids: list[list[float]] = []
    asks: list[list[float]] = []
    observed_at: datetime | None = None
    quality_tier: str | None = None


class MovementEvent(BaseModel):
    time: str
    provider: str
    market_type: str
    selection: str
    old_probability: float | None = None
    new_probability: float | None = None
    magnitude: float | None = None
    alert_type: str | None = None
    details: dict[str, Any] = {}


class ContractDetail(BaseModel):
    provider: str
    market_type: str
    selection: str
    line_value: float | None = None
    market_question: str | None = None
    resolution_rules: str | None = None
    settlement_source: str | None = None
    market_close_at: datetime | None = None
    semantics_status: str
    line_source: str


class MappingDetail(BaseModel):
    provider: str
    provider_event_id: str
    mapping_status: str
    semantics_status: str
    start_time_delta_seconds: int | None = None
    game_time_agreement: str | None = None


class MarketPulseGameDetail(BaseModel):
    data_mode: DataMode
    game_id: str
    home_team: str | None = None
    away_team: str | None = None
    starts_at: datetime | None = None
    status: str
    disclosure: str = "RESEARCH OBSERVER · NO TRADING · NOT A MODEL INPUT · NOT A GUARANTEE"
    lead_lag_note: str = "COLLECTING LEAD-LAG EVIDENCE"
    timeline: list[TimelinePoint] = []
    order_books: list[OrderBookView] = []
    movement_events: list[MovementEvent] = []
    contracts: list[ContractDetail] = []
    mappings: list[MappingDetail] = []
    row: MarketPulseRow | None = None


class PredictionMarketAlertView(BaseModel):
    id: str
    alert_type: str
    game_id: str | None = None
    provider: str | None = None
    market_type: str | None = None
    selection: str | None = None
    magnitude: float | None = None
    details: dict[str, Any] = {}
    observed_at: datetime
    label: str = "OBSERVED MARKET SIGNAL — REVIEW ONLY"


class PredictionMarketAlertsData(BaseModel):
    data_mode: DataMode
    date: date
    sport: str | None = None
    alerts: list[PredictionMarketAlertView] = []


class ProviderHealth(BaseModel):
    provider: str
    enabled: bool
    connection_mode: str
    last_discovery_at: datetime | None = None
    last_snapshot_at: datetime | None = None
    subscribed_markets: int = 0
    request_count: int | None = None
    last_error: str | None = None
    status: str = "IDLE"


class PredictionMarketHealthSummary(BaseModel):
    data_mode: DataMode
    observer_enabled: bool
    public_display_enabled: bool
    classification: str = "LOCAL_PRIVATE_RESEARCH"
    providers: list[ProviderHealth] = []
    unresolved_mappings: int = 0
    review_required_mappings: int = 0
    semantics_review_count: int = 0
    stale_markets: int = 0
    total_contracts: int = 0
    total_snapshots: int = 0
