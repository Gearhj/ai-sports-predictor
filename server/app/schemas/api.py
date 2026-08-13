from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


DataMode = Literal["live", "empty", "demo"]


class ComponentWeights(BaseModel):
    """Safe public model weights expressed as normalized percentage points."""

    baseline: float
    run: float
    boosting: float


class BankrollPolicyContract(BaseModel):
    kelly_fraction: float
    maximum_stake_percentage: float
    standard_edge_threshold: float
    label: str


class PublicConfigData(BaseModel):
    data_mode: DataMode
    model_status: Literal["DEVELOPMENT"]
    component_weights: ComponentWeights
    bankroll_policy: BankrollPolicyContract


class TeamSummary(BaseModel):
    id: str
    abbreviation: str
    name: str
    city: str
    record: str
    color: str


class PitcherSummary(BaseModel):
    name: str
    hand: Literal["RHP", "LHP"] | None = None
    confirmed: bool
    era: float | None
    xera: float | None


class ForecastGame(BaseModel):
    id: str
    start_time: datetime
    status: str
    venue: str
    away_team: TeamSummary
    home_team: TeamSummary
    away_pitcher: PitcherSummary
    home_pitcher: PitcherSummary
    forecast_available: bool
    market_available: bool
    projected_away_runs: float | None
    projected_home_runs: float | None
    projected_total: float | None
    projected_run_difference: float | None
    uncertainty: float | None
    market_total: float | None
    away_win_probability: float | None
    home_win_probability: float | None
    fair_away_moneyline: int | None
    fair_home_moneyline: int | None
    best_away_moneyline: int | None
    best_home_moneyline: int | None
    market_home_probability: float | None
    model_edge: float | None
    edge_side: Literal["home", "away", "none"]
    opportunity: Literal["STRONG EDGE", "STANDARD EDGE", "WATCH", "NO BET"]
    confidence: float | None
    data_readiness: float | None
    books_reporting: int
    last_update: datetime


class DashboardSummary(BaseModel):
    games: int
    complete_forecasts: int
    qualifying_edges: int
    last_refresh: datetime | None
    overall_data_health: float
    official_bankroll: float
    season_return: float


class DashboardData(BaseModel):
    data_mode: DataMode
    date: date
    model_status: Literal["DEVELOPMENT"]
    component_weights: ComponentWeights
    summary: DashboardSummary
    games: list[ForecastGame]


class ComparisonMetric(BaseModel):
    metric: str
    away: float | str
    home: float | str
    preferred: Literal["away", "home", "neutral"] | None = None
    context: str | None = None


class MarketPrice(BaseModel):
    book: str
    away: int
    home: int
    observed_at: datetime
    is_best_away: bool = False
    is_best_home: bool = False


class GameAnalysisData(BaseModel):
    data_mode: DataMode
    game: ForecastGame
    data_as_of: datetime
    model_version: str
    forecast_version: str
    weather: dict
    probability_comparison: list[dict]
    run_distribution: list[dict]
    total_run_distribution: list[dict]
    run_difference_distribution: list[dict]
    market_prices: list[MarketPrice]
    pitcher_metrics: list[ComparisonMetric]
    offense_metrics: list[ComparisonMetric]
    bullpen_metrics: list[ComparisonMetric]
    statcast_metrics: list[ComparisonMetric]
    model_components: list[dict]
    drivers: list[dict]
    forecast_history: list[dict]
    data_audit: list[dict]
    simulation: dict


class MarketBookPrice(BaseModel):
    book: str
    price: int
    best: bool = False


class MarketBoardRow(BaseModel):
    id: str
    game_id: str
    matchup: str
    start_time: datetime
    market_type: Literal["Moneyline", "Run line", "Total"]
    selection: str
    line: float | None = None
    asp_fair: float | None
    consensus: float
    edge: float | None
    books: list[MarketBookPrice]
    observed_at: datetime


class MarketBoardData(BaseModel):
    data_mode: DataMode
    date: date
    books: list[str]
    quota_remaining: int | None
    rows: list[MarketBoardRow]


class PerformanceData(BaseModel):
    data_mode: DataMode
    evaluation_available: bool = False
    market_benchmark_available: bool = False
    model_version: str
    model_status: Literal["DEVELOPMENT"]
    component_weights: ComponentWeights
    evaluated_games: int
    brier_score: float | None
    log_loss: float | None
    accuracy: float | None
    run_mae: float | None
    total_mae: float | None
    versus_market_brier: float | None
    calibration: list[dict]
    rolling: list[dict]
    model_components: list[dict]
    by_confidence: list[dict]


class PositionSchema(BaseModel):
    id: str
    date: date
    game_id: str
    selection: str
    market: str
    price: int
    stake: float
    result: Literal["WIN", "LOSS", "PUSH", "VOID", "OPEN"]
    profit_loss: float
    closing_price: int | None
    clv: float | None


class BankrollData(BaseModel):
    data_mode: DataMode
    experiment_status: Literal["NOT_STARTED", "ACTIVE"]
    starting_bankroll: float
    current_bankroll: float
    peak_bankroll: float
    total_return: float
    roi: float
    max_drawdown: float
    record: str
    positions_count: int
    policy: str
    policy_detail: str
    policy_max_stake_percentage: float
    policy_kelly_fraction: float
    policy_edge_threshold_percentage: float
    equity_curve: list[dict]
    monthly: list[dict]
    positions: list[PositionSchema]


class SourceHealth(BaseModel):
    id: str
    name: str
    status: Literal["ready", "watch", "stale", "missing"]
    last_success: str
    last_failure: str | None
    records: int
    detail: str
    freshness_minutes: float


class JobRunSchema(BaseModel):
    id: str
    job: str
    status: Literal["success", "running", "failed"]
    started_at: str
    duration: str
    records: int


class HealthData(BaseModel):
    data_mode: DataMode
    database_status: Literal["ready"]
    sports_data_status: Literal["empty", "available"]
    provider_evidence_status: Literal["none", "demo", "live", "mixed"]
    overall_score: float
    model_version: str
    model_status: Literal["DEVELOPMENT"]
    component_weights: ComponentWeights
    latest_forecast: str
    odds_quota_remaining: int | None
    odds_polling_enabled: bool
    games_discovered: int
    missing_games: int | None
    sources: list[SourceHealth]
    recent_jobs: list[JobRunSchema]


class AuthUser(BaseModel):
    id: str
    email: EmailStr
    display_name: str
    role: Literal["ADMIN", "MEMBER"]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RegisterRequest(LoginRequest):
    display_name: str = Field(min_length=2, max_length=100)

    @field_validator("display_name")
    @classmethod
    def display_name_not_blank(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Display name cannot be blank")
        return normalized


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUser


class JobTriggerResponse(BaseModel):
    id: str
    job_id: str
    job: str
    status: str
    result: dict | None = None


class ErrorResponse(BaseModel):
    detail: str
