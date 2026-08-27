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


class LineupStatusSummary(BaseModel):
    status: Literal["PROJECTED", "EXPECTED", "CONFIRMED", "MISSING"]
    complete: bool
    observed_at: datetime | None = None
    available_as_of: datetime | None = None
    source: str | None = None


class ReadinessComponentSummary(BaseModel):
    key: str
    label: str
    status: str
    score: float | None = None
    weight: float | None = None
    detail: str
    critical: bool = False


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
    data_as_of: datetime | None
    lifecycle_stage: str
    time_to_first_pitch_seconds: int | None
    last_refresh_at: datetime | None
    next_scheduled_refresh_at: datetime | None
    final_forecast_status: str | None
    final_snapshot_frozen: bool | None
    away_lineup: LineupStatusSummary
    home_lineup: LineupStatusSummary
    readiness_detail: list[ReadinessComponentSummary]
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


RecommendationState = Literal["QUALIFIED_BET", "PROVISIONAL_EDGE", "WATCH", "NO_BET"]


class OpportunitySelection(BaseModel):
    """One governed, priced selection. Probabilities/edges are percentage points;
    stake, profit, and return are virtual dollars. Every value is derived from a
    real two-sided quote by the existing shadow-pricing policy."""

    recommendation: RecommendationState
    market_type: str
    period: str
    selection: str
    line: float | None = None
    sportsbook: str | None = None
    american_odds: int | None = None
    decimal_odds: float | None = None
    model_probability: float | None = None
    no_vig_probability: float | None = None
    consensus_probability: float | None = None
    fair_odds: int | None = None
    raw_edge: float | None = None
    uncertainty_adjusted_edge: float | None = None
    expected_value: float | None = None
    quote_observed_at: datetime | None = None
    quote_age_seconds: int | None = None
    is_stale: bool = False
    virtual_stake: float = 0.0
    potential_profit: float = 0.0
    total_return: float = 0.0
    opportunity_score: float = 0.0


class GameOpportunity(BaseModel):
    game_id: str
    sport: str = "MLB"
    recommendation_state: RecommendationState
    recommendation_reason: str
    opportunity_score: float = 0.0
    markets_available: list[str] = Field(default_factory=list)
    readiness: float | None = None
    uncertainty: float | None = None
    model_disagreement: float | None = None
    best: OpportunitySelection | None = None
    alternates: list[OpportunitySelection] = Field(default_factory=list)


class OpportunitySummary(BaseModel):
    qualified: int = 0
    provisional: int = 0
    watch: int = 0
    no_bet: int = 0
    best_expected_value: float | None = None
    virtual_daily_exposure: float = 0.0
    top_opportunity: str | None = None
    markets_available: list[str] = Field(default_factory=list)
    sports_available: list[str] = Field(default_factory=list)


class DashboardData(BaseModel):
    data_mode: DataMode
    date: date
    model_status: Literal["DEVELOPMENT"]
    component_weights: ComponentWeights
    summary: DashboardSummary
    games: list[ForecastGame]
    opportunities: list[GameOpportunity] = Field(default_factory=list)
    opportunity_summary: OpportunitySummary | None = None


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


class OfflineEvaluationWindow(BaseModel):
    start_date: str
    end_date: str
    games: int = Field(ge=0)


class OfflineEvaluationMetrics(BaseModel):
    brier_score: float | None = None
    log_loss: float | None = None
    expected_calibration_error: float | None = None
    run_mae: float | None = None


class OfflineEvaluationBaseline(OfflineEvaluationMetrics):
    name: str
    sample_size: int = Field(ge=0)


class OfflinePromotionDecision(BaseModel):
    decision: Literal["PROMOTED", "NOT_PROMOTED", "BLOCKED", "PENDING"]
    reason: str | None = None
    criteria_version: str | None = None


class OfflineEvaluation(BaseModel):
    status: Literal["AVAILABLE", "INSUFFICIENT_DATA", "NOT_RUN", "FAILED"]
    status_reason: str | None = None
    model_version: str | None = None
    manifest_version: str | None = None
    feature_version: str | None = None
    dataset_checksum: str | None = None
    feature_set_checksum: str | None = None
    research_decision: Literal[
        "RESEARCH_CANDIDATE",
        "RESEARCH_CHALLENGER",
        "REJECTED_GEN1",
    ] | None = None
    evaluation_policy_version: str | None = None
    rolling_origin_folds: int = Field(default=0, ge=0)
    train_window: OfflineEvaluationWindow | None = None
    validation_window: OfflineEvaluationWindow | None = None
    holdout_window: OfflineEvaluationWindow | None = None
    validation_metrics: OfflineEvaluationMetrics | None = None
    holdout_metrics: OfflineEvaluationMetrics | None = None
    baselines: list[OfflineEvaluationBaseline] = Field(default_factory=list)
    promotion: OfflinePromotionDecision | None = None


class HistoricalCoverageItem(BaseModel):
    key: str
    label: str
    coverage: float | None = Field(default=None, ge=0, le=1)
    required: float = Field(ge=0, le=1)
    passed: bool


class HistoricalReadinessSummary(BaseModel):
    status: Literal[
        "READY_FOR_GATE_3B_MODEL_DEVELOPMENT",
        "NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT",
        "UNAVAILABLE",
    ]
    policy_version: str | None = None
    row_count: int = Field(default=0, ge=0)
    distinct_game_count: int = Field(default=0, ge=0)
    distinct_event_dates: int = Field(default=0, ge=0)
    development_date_groups: int = Field(default=0, ge=0)
    holdout_date_groups: int = Field(default=0, ge=0)
    holdout_rows: int = Field(default=0, ge=0)
    rolling_origin_folds: int = Field(default=0, ge=0)
    failed_checks: list[str] = Field(default_factory=list)
    model_training_invoked: bool | None = None
    model_fitting_invoked: bool | None = None
    holdout_metrics_inspected: bool | None = None
    holdout_outcomes_inspected: bool | None = None


class HistoricalPopulationSummary(BaseModel):
    population: Literal["TIER_A_STRICT", "TIER_B_ENHANCED"]
    role: Literal["PRIMARY_STRICT", "RESEARCH_ONLY_SHADOW"]
    source_mode: Literal["LIVE"]
    quality_tiers: list[Literal["A", "B"]]
    row_count: int = Field(ge=0)
    game_count: int = Field(ge=0)
    excluded_row_count: int = Field(ge=0)
    event_start_date: date
    event_end_date: date
    schema_version: str
    schema_hash: str | None = None
    feature_version: str | None = None
    raw_value_count: int | None = Field(default=None, ge=0)
    ordered_output_count: int = Field(ge=0)
    mean_quality_score: float | None = Field(default=None, ge=0, le=100)
    manifest_version: str
    manifest_identity_checksum: str
    rows_checksum: str
    built_at: datetime
    integrity_status: Literal["PASS", "FAILED"]
    integrity_issues: list[str] = Field(default_factory=list)
    required_family_coverage: list[HistoricalCoverageItem] = Field(default_factory=list)
    raw_family_coverage: list[HistoricalCoverageItem] = Field(default_factory=list)
    readiness: HistoricalReadinessSummary


class HistoricalCanonicalSummary(BaseModel):
    availability: Literal["AVAILABLE", "INTEGRITY_WARNING", "UNAVAILABLE"]
    integrity_status: Literal["PASS", "FAILED", "UNAVAILABLE"]
    official_games: int | None = Field(default=None, ge=0)
    team_game_rows: int | None = Field(default=None, ge=0)
    player_game_rows: int | None = Field(default=None, ge=0)
    strict_feature_coverage: float | None = Field(default=None, ge=0, le=1)
    leakage_status: Literal["PASS", "FAILED", "UNAVAILABLE"]
    statcast_status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"]
    historical_market_status: Literal["UNAVAILABLE"]
    source_keys: list[str] = Field(default_factory=list)
    integrity_issues: list[str] = Field(default_factory=list)


class HistoricalResearchData(BaseModel):
    data_mode: Literal["live", "empty"]
    availability: Literal["AVAILABLE", "PARTIAL", "INTEGRITY_WARNING", "UNAVAILABLE"]
    temporal_scope: Literal["HISTORICAL"]
    temporal_semantics: Literal["RETROSPECTIVE_RECONSTRUCTION"]
    stage: Literal["GATE_3A_A2"]
    season: int
    cutoff_policy_version: str | None = None
    built_at: datetime | None = None
    verdict: str
    historical_market_status: Literal["UNAVAILABLE"]
    model_training_invoked: bool | None = None
    model_fitting_invoked: bool | None = None
    holdout_metrics_inspected: bool | None = None
    promotion_invoked: bool | None = None
    strict_population: HistoricalPopulationSummary | None = None
    enhanced_population: HistoricalPopulationSummary | None = None
    canonical_summary: HistoricalCanonicalSummary
    limitations: list[str] = Field(default_factory=list)


class HistoricalTeamPrior(BaseModel):
    team_id: str
    abbreviation: str
    name: str
    elo_rating: float | None = None
    runs_per_game: float | None = None
    runs_allowed_per_game: float | None = None
    offense_true_talent: float | None = None
    run_prevention_true_talent: float | None = None
    reliability: float | None = Field(default=None, ge=0, le=1)


class HistoricalRunStrengthSnapshot(BaseModel):
    home_offense: float | None = None
    home_run_prevention: float | None = None
    away_offense: float | None = None
    away_run_prevention: float | None = None
    expected_home_win_probability: float | None = Field(default=None, ge=0, le=1)


class HistoricalGameExplorerRow(BaseModel):
    game_id: str
    event_date: date
    scheduled_at: datetime
    matchup: str
    cutoff_at: datetime
    cutoff_policy_version: str
    quality_tier: Literal["A", "B"]
    source_mode: Literal["LIVE"]
    source_keys: list[str]
    feature_completeness: float = Field(ge=0, le=1)
    admission_quality_score: float = Field(ge=0, le=100)
    present_feature_count: int = Field(ge=0)
    total_feature_count: int = Field(ge=0)
    starter_state: Literal["CONFIRMED", "PARTIAL", "MISSING"]
    lineup_state: Literal["CONFIRMED", "PARTIAL", "MISSING"]
    home_team_prior: HistoricalTeamPrior
    away_team_prior: HistoricalTeamPrior
    run_strength: HistoricalRunStrengthSnapshot
    market_available: bool | None = None
    market_status: Literal["UNAVAILABLE"]
    target_excluded: Literal[True]


class HistoricalGameExplorerPage(BaseModel):
    data_mode: Literal["live", "empty"]
    availability: Literal["AVAILABLE", "INTEGRITY_WARNING", "UNAVAILABLE"]
    status_reason: str
    season: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)
    total_rows: int = Field(ge=0)
    team: str | None = None
    month: int | None = Field(default=None, ge=1, le=12)
    tier: Literal["A", "B"]
    source: str | None = None
    rows: list[HistoricalGameExplorerRow] = Field(default_factory=list)


class HistoricalDatasetPerformanceSummary(BaseModel):
    availability: Literal["AVAILABLE", "INTEGRITY_WARNING", "UNAVAILABLE"]
    stage: Literal["GATE_3A_A2"]
    season: int
    population: Literal["TIER_A_STRICT"]
    row_count: int = Field(default=0, ge=0)
    distinct_event_dates: int = Field(default=0, ge=0)
    holdout_rows: int = Field(default=0, ge=0)
    rolling_origin_folds: int = Field(default=0, ge=0)
    readiness_status: str
    failed_checks: list[str] = Field(default_factory=list)
    status_reason: str
    model_training_invoked: bool | None = None
    model_fitting_invoked: bool | None = None
    holdout_metrics_inspected: bool | None = None


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
    offline_evaluation: OfflineEvaluation | None = None
    historical_dataset: HistoricalDatasetPerformanceSummary | None = None


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


class HistoricalWarehouseHealth(BaseModel):
    availability: Literal["AVAILABLE", "PARTIAL", "INTEGRITY_WARNING", "UNAVAILABLE"]
    season: int
    temporal_semantics: Literal["RETROSPECTIVE_RECONSTRUCTION"]
    strict_rows: int = Field(default=0, ge=0)
    enhanced_rows: int = Field(default=0, ge=0)
    readiness_status: str
    mean_strict_feature_coverage: float | None = Field(default=None, ge=0, le=1)
    integrity_status: Literal["PASS", "FAILED", "UNAVAILABLE"]
    leakage_status: Literal["PASS", "FAILED", "UNAVAILABLE"]
    statcast_status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"]
    historical_market_status: Literal["UNAVAILABLE"]
    failed_checks: list[str] = Field(default_factory=list)
    built_at: datetime | None = None
    detail: str


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
    historical_warehouse: HistoricalWarehouseHealth | None = None


class AuthUser(BaseModel):
    id: str
    email: EmailStr
    display_name: str
    role: Literal["ADMIN", "MEMBER"]


class LoginRequest(BaseModel):
    email: EmailStr
    # Login keeps the historical lower bound so accounts created under the
    # earlier 8-character minimum can still authenticate. New/reset passwords
    # are governed by the stricter RegisterRequest policy below.
    password: str = Field(min_length=8, max_length=128)


class RegisterRequest(LoginRequest):
    # Length is the primary control (no mandatory composition rules).
    # BREACHED_PASSWORD_CHECK_REQUIRED_BEFORE_PUBLIC_PRODUCTION — add an
    # offline / k-anonymity breached-password check before public launch
    # (no external network call is introduced here).
    password: str = Field(min_length=12, max_length=128)
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


class ShadowDecisionView(BaseModel):
    market: str
    selection: str
    model_probability: float | None
    has_quote: bool
    book: str | None
    line: float | None
    american_odds: int | None
    best_american_odds: int | None
    no_vig_probability: float | None
    raw_edge: float | None
    no_vig_edge: float | None
    expected_value: float | None
    uncertainty_adjusted_edge: float | None
    recommendation: Literal["NO_BET", "WATCH", "QUALIFIED_BET"]
    virtual_stake: float


class ShadowEngineView(BaseModel):
    engine_id: str
    forecast_id: str
    status: str
    is_ready: bool
    readiness_score: float
    readiness_threshold: float
    home_win_probability: float
    away_win_probability: float
    expected_home_score: float
    expected_away_score: float
    first_five_home_win_probability: float
    first_five_away_win_probability: float
    decisions: list[ShadowDecisionView]


class ShadowGameView(BaseModel):
    event_id: str
    event_date: date
    home_team_id: str
    away_team_id: str
    engines: list[ShadowEngineView]
    model_disagreement: float | None = None


class ShadowBoardData(BaseModel):
    slate_date: date
    status: Literal["RESEARCH_SHADOW"]
    executable: Literal[False]
    games: list[ShadowGameView]


class ProspectiveRescoreBoard(BaseModel):
    slate_date: date
    status: Literal["RESEARCH_SHADOW"]
    executable: Literal[False]
    rescores: list[dict]


class ErrorResponse(BaseModel):
    detail: str
