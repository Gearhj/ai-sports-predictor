export type DataMode = 'live' | 'empty' | 'demo'
export type ResourceState = DataMode | 'loading' | 'unavailable'
export interface DataProvenance {
  data_mode?: DataMode
  is_demo?: boolean
}
export interface PublicConfig extends DataProvenance {
  model_status: string
  component_weights: {
    baseline: number
    run: number
    boosting: number
  }
  bankroll_policy: {
    label: string
    kelly_fraction: number
    maximum_stake_percentage: number
    standard_edge_threshold?: number
  }
}
export type Trend = 'positive' | 'negative' | 'neutral'
export type ReadinessState = 'ready' | 'watch' | 'stale' | 'missing'

export interface TeamSummary {
  id: string
  abbreviation: string
  name: string
  city: string
  record: string
  color: string
}

export interface PitcherSummary {
  name: string
  hand: 'RHP' | 'LHP' | null
  confirmed: boolean
  era: number | null
  xera: number | null
}

export interface ForecastGame {
  id: string
  start_time: string
  status: string
  venue: string
  away_team: TeamSummary
  home_team: TeamSummary
  away_pitcher: PitcherSummary
  home_pitcher: PitcherSummary
  forecast_available?: boolean
  market_available?: boolean
  projected_away_runs: number | null
  projected_home_runs: number | null
  projected_total: number | null
  projected_run_difference?: number | null
  uncertainty?: number | null
  market_total: number | null
  away_win_probability: number | null
  home_win_probability: number | null
  fair_away_moneyline: number | null
  fair_home_moneyline: number | null
  best_away_moneyline: number | null
  best_home_moneyline: number | null
  market_home_probability: number | null
  model_edge: number | null
  edge_side: 'home' | 'away' | 'none'
  opportunity: 'STRONG EDGE' | 'STANDARD EDGE' | 'WATCH' | 'NO BET'
  confidence: number | null
  data_readiness: number | null
  books_reporting: number
  last_update: string
}

export interface DashboardData extends DataProvenance {
  date: string
  summary: {
    games: number
    complete_forecasts: number
    qualifying_edges: number
    last_refresh: string
    overall_data_health: number
    official_bankroll: number
    season_return: number
  }
  games: ForecastGame[]
}

export interface MetricPoint {
  label: string
  value: number
}

export interface ComparisonMetric {
  metric: string
  away: number | string
  home: number | string
  preferred?: 'away' | 'home' | 'neutral'
  context?: string
}

export interface MarketPrice {
  book: string
  away: number
  home: number
  observed_at: string
  is_best_away?: boolean
  is_best_home?: boolean
}

export interface GameAnalysisData extends DataProvenance {
  game: ForecastGame
  data_as_of: string
  model_version: string
  forecast_version: string
  weather: {
    temperature_f: number | null
    description: string
    wind_mph: number | null
    wind_direction: string
    humidity: number | null
    run_effect: number | null
  }
  probability_comparison: Array<{ label: string; home: number; away: number }>
  run_distribution: Array<{ runs: number; away: number; home: number }>
  total_run_distribution?: Array<{ value: number; probability: number }>
  run_difference_distribution?: Array<{ value: number; probability: number }>
  market_prices: MarketPrice[]
  pitcher_metrics: ComparisonMetric[]
  offense_metrics: ComparisonMetric[]
  bullpen_metrics: ComparisonMetric[]
  statcast_metrics: ComparisonMetric[]
  model_components: Array<{ model: string; home_probability: number; expected_margin: number; weight: number | null }>
  drivers: Array<{ label: string; impact: number; detail: string }>
  forecast_history: Array<{ time: string; version: string; home_probability: number; market_probability: number | null }>
  data_audit: Array<{ source: string; status: ReadinessState; updated: string; detail: string }>
  simulation: {
    iterations: number
    home_cover_probability: number
    over_probability: number
    most_likely_score: string
  }
}

export type MarketType = 'Moneyline' | 'Run line' | 'Total'

export interface MarketBoardRow {
  id: string
  game_id: string
  matchup: string
  start_time: string
  market_type: MarketType
  selection: string
  line?: number
  asp_fair: number | null
  consensus: number
  edge: number | null
  books: Array<{ book: string; price: number; best?: boolean }>
  observed_at: string
}

export interface MarketBoardData extends DataProvenance {
  date: string
  books: string[]
  quota_remaining: number | null
  rows: MarketBoardRow[]
}

export interface PerformanceData extends DataProvenance {
  model_version: string
  model_status?: string
  component_weights?: {
    baseline: number
    run: number
    boosting: number
  }
  evaluated_games: number
  evaluation_available?: boolean
  market_benchmark_available?: boolean
  brier_score: number | null
  log_loss: number | null
  accuracy: number | null
  run_mae: number | null
  total_mae: number | null
  versus_market_brier: number | null
  calibration: Array<{ predicted: number; observed: number; count: number }>
  rolling: Array<{ date: string; asp_brier: number; market_brier: number | null; accuracy: number }>
  model_components: Array<{ model: string; brier: number | null; log_loss: number | null; run_mae: number | null; status: string }>
  by_confidence: Array<{ bucket: string; forecasts: number; accuracy: number | null; brier: number | null }>
}

export interface Position {
  id: string
  date: string
  game_id: string
  selection: string
  market: string
  price: number
  stake: number
  result: 'WIN' | 'LOSS' | 'PUSH' | 'VOID' | 'OPEN'
  profit_loss: number
  closing_price: number | null
  clv: number | null
}

export interface BankrollData extends DataProvenance {
  starting_bankroll: number
  current_bankroll: number
  peak_bankroll: number
  total_return: number
  roi: number
  max_drawdown: number
  record: string
  positions_count: number
  policy: string
  policy_detail: string
  policy_kelly_fraction?: number
  policy_max_stake_percentage?: number
  policy_edge_threshold_percentage?: number
  experiment_status?: 'NOT_STARTED' | 'ACTIVE'
  equity_curve: Array<{ date: string; bankroll: number; benchmark: number }>
  monthly: Array<{ month: string; positions: number; record: string; profit_loss: number; roi: number }>
  positions: Position[]
}

export interface SourceHealth {
  id: string
  name: string
  status: ReadinessState
  last_success: string
  last_failure: string | null
  records: number
  detail: string
  freshness_minutes: number
}

export interface JobRun {
  id: string
  job: string
  status: 'success' | 'running' | 'failed'
  started_at: string
  duration: string
  records: number
}

export interface HealthData extends DataProvenance {
  overall_score: number
  model_version: string
  latest_forecast: string
  odds_quota_remaining: number | null
  odds_polling_enabled: boolean
  games_discovered: number
  missing_games: number | null
  database_status?: string
  sports_data_status?: 'empty' | 'available'
  provider_evidence_status?: 'none' | 'demo' | 'live' | 'mixed'
  sources: SourceHealth[]
  recent_jobs: JobRun[]
}

export interface AuthUser {
  id: string
  email: string
  display_name: string
  role: 'ADMIN' | 'MEMBER'
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user: AuthUser
}
