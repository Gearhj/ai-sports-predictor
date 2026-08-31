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
  display_timezone?: string
  operational_date?: string | null
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

export type LineupStatus = 'PROJECTED' | 'EXPECTED' | 'CONFIRMED' | 'MISSING'

export interface LineupStatusSummary {
  status: LineupStatus
  complete: boolean
  observed_at: string | null
  available_as_of: string | null
  source: string | null
}

export interface ReadinessComponentSummary {
  key: string
  label: string
  status: string
  score: number | null
  weight: number | null
  detail: string
  critical: boolean
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
  data_as_of?: string | null
  lifecycle_stage?: string
  time_to_first_pitch_seconds?: number | null
  last_refresh_at?: string | null
  next_scheduled_refresh_at?: string | null
  final_forecast_status?: string | null
  final_snapshot_frozen?: boolean | null
  away_lineup?: LineupStatusSummary
  home_lineup?: LineupStatusSummary
  readiness_detail?: ReadinessComponentSummary[]
  books_reporting: number
  last_update: string
}

export type RecommendationState = 'QUALIFIED_BET' | 'PROVISIONAL_EDGE' | 'EXTREME_DISAGREEMENT' | 'WATCH' | 'NO_BET'

export interface AvailabilityContext {
  lineup_status: 'PROJECTED' | 'CONFIRMED' | 'CHANGED' | 'MISSING' | 'DETAILS_MISSING'
  lineup_complete: boolean
  starting_pitcher_status: 'CONFIRMED' | 'PROBABLE' | 'MISSING'
  injury_feed_status: string
  injury_feed_authoritative: boolean
  production_injury_coverage: boolean
  unavailable_players: number | null
  last_availability_update: string | null
  last_availability_event: string | null
  rescored: boolean
  forecast_before: number | null
  forecast_after: number | null
  edge_before: number | null
  edge_after: number | null
  book_moved: boolean | null
}

export interface OpportunitySelection {
  recommendation: RecommendationState
  market_type: string
  period: string
  selection: string
  line: number | null
  sportsbook: string | null
  american_odds: number | null
  decimal_odds: number | null
  model_probability: number | null
  no_vig_probability: number | null
  consensus_probability: number | null
  fair_odds: number | null
  raw_edge: number | null
  uncertainty_adjusted_edge: number | null
  expected_value: number | null
  quote_observed_at: string | null
  quote_age_seconds: number | null
  is_stale: boolean
  virtual_stake: number
  potential_profit: number
  total_return: number
  opportunity_score: number
}

export interface GameOpportunity {
  game_id: string
  sport: string
  recommendation_state: RecommendationState
  recommendation_reason: string
  qualification_reasons: string[]
  review_required: boolean
  opportunity_score: number
  markets_available: string[]
  readiness: number | null
  uncertainty: number | null
  model_disagreement: number | null
  availability: AvailabilityContext
  best: OpportunitySelection | null
  alternates: OpportunitySelection[]
  experiment_status?: 'NOT_IN_EXPERIMENT' | 'ELIGIBLE' | 'PROVISIONAL' | 'NO_POSITION' | 'FROZEN' | 'SETTLED'
  experiment_stake?: number | null
  v2?: V2PolicyView | null
}

export interface V2PolicyView {
  policy_version: string
  tier: 'TIER_A_FULL_EVIDENCE' | 'TIER_B_PROBABLE_STABLE' | 'TIER_C_LINEUP_PENDING' | 'TIER_D_INELIGIBLE'
  recommendation: string
  reason: string
  reasons: string[]
  starter_state_home: string | null
  starter_state_away: string | null
  lineup_state: string | null
  selection: string | null
  raw_edge: number | null
  starter_penalty: number
  lineup_penalty: number
  adjusted_edge: number | null
  adjusted_expected_value: number | null
  evidence_multiplier: number
  theoretical_stake: number | null
  experiment_effective_date: string | null
  active: boolean
}

export interface ExperimentComparisonRow {
  key: 'v1' | 'v2'
  name: string
  policy: string
  staking_policy: string
  status: 'ACTIVE' | 'SCHEDULED'
  effective_date: string
  starting_bankroll: number
  current_bankroll: number
  games_evaluated: number
  decisions: number
  qualified_decisions: number
  positions: number
  average_raw_edge: number | null
  average_adjusted_edge: number | null
  average_stake: number | null
  settled: number
  record: string | null
  profit_loss: number | null
  average_clv: number | null
  policy_checksum: string | null
}

export interface ExperimentComparison {
  experiments: ExperimentComparisonRow[]
}

export interface BettingBreakdownRow {
  key: string
  positions: number
  settled: number
  record: string
  profit_loss: number
  staked: number
  roi: number | null
}

export interface BettingPerformance {
  starting_bankroll: number
  current_bankroll: number
  net_profit_loss: number
  total_risked: number
  roi: number | null
  yield_pct: number | null
  positions: number
  settled: number
  open: number
  wins: number
  losses: number
  pushes: number
  voids: number
  record: string
  max_drawdown: number
  longest_losing_streak: number
  closing_line_value: number | null
  beat_close_percentage: number | null
  has_settled: boolean
  breakdowns: Record<string, BettingBreakdownRow[]>
}

export interface OpportunitySummary {
  qualified: number
  provisional: number
  review_required: number
  watch: number
  no_bet: number
  best_expected_value: number | null
  virtual_daily_exposure: number
  top_opportunity: string | null
  top_v1_moneyline?: string | null
  top_v2_moneyline?: string | null
  markets_available: string[]
  sports_available: string[]
  injury_feed_status: string
  injury_feed_authoritative: boolean
  odds_last_refresh: string | null
  odds_quote_age_seconds: number | null
  odds_quota_remaining: number | null
  odds_scheduler_running: boolean
}

export interface DashboardData extends DataProvenance {
  date: string
  model_status: string
  summary: {
    games: number
    complete_forecasts: number
    qualifying_edges: number
    last_refresh: string | null
    overall_data_health: number
    official_bankroll: number
    season_return: number
  }
  games: ForecastGame[]
  opportunities?: GameOpportunity[]
  opportunity_summary?: OpportunitySummary | null
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
  forecast_history: Array<{
    time: string
    version: string
    home_probability: number
    market_probability: number | null
    lineup_status?: string
    expected_home_runs?: number | null
    expected_away_runs?: number | null
    rescore_reason?: string | null
  }>
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

export type OfflineEvaluationStatus = 'AVAILABLE' | 'INSUFFICIENT_DATA' | 'NOT_RUN' | 'FAILED'

export interface OfflineEvaluationWindow {
  start_date: string
  end_date: string
  games: number
}

export interface OfflineEvaluationMetrics {
  brier_score: number | null
  log_loss: number | null
  expected_calibration_error: number | null
  run_mae: number | null
}

export interface OfflineEvaluationBaseline extends OfflineEvaluationMetrics {
  name: string
  sample_size: number
}

export interface OfflinePromotionDecision {
  decision: 'PROMOTED' | 'NOT_PROMOTED' | 'BLOCKED' | 'PENDING'
  reason: string | null
  criteria_version?: string | null
}

export interface OfflineEvaluation {
  status: OfflineEvaluationStatus
  status_reason: string | null
  model_version: string | null
  manifest_version: string | null
  feature_version: string | null
  dataset_checksum?: string | null
  feature_set_checksum?: string | null
  research_decision?: 'RESEARCH_CANDIDATE' | 'RESEARCH_CHALLENGER' | 'REJECTED_GEN1' | null
  evaluation_policy_version?: string | null
  rolling_origin_folds: number
  train_window: OfflineEvaluationWindow | null
  validation_window: OfflineEvaluationWindow | null
  holdout_window: OfflineEvaluationWindow | null
  validation_metrics: OfflineEvaluationMetrics | null
  holdout_metrics: OfflineEvaluationMetrics | null
  baselines: OfflineEvaluationBaseline[]
  promotion: OfflinePromotionDecision | null
}

export interface HistoricalCoverageItem {
  key: string
  label: string
  coverage: number | null
  required: number
  passed: boolean
}

export interface HistoricalReadinessSummary {
  status: 'READY_FOR_GATE_3B_MODEL_DEVELOPMENT' | 'NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT' | 'UNAVAILABLE'
  policy_version: string | null
  row_count: number
  distinct_game_count: number
  distinct_event_dates: number
  development_date_groups: number
  holdout_date_groups: number
  holdout_rows: number
  rolling_origin_folds: number
  failed_checks: string[]
  model_training_invoked: boolean | null
  model_fitting_invoked: boolean | null
  holdout_metrics_inspected: boolean | null
  holdout_outcomes_inspected: boolean | null
}

export interface HistoricalPopulationSummary {
  population: 'TIER_A_STRICT' | 'TIER_B_ENHANCED'
  role: 'PRIMARY_STRICT' | 'RESEARCH_ONLY_SHADOW'
  source_mode: 'LIVE'
  quality_tiers: Array<'A' | 'B'>
  row_count: number
  game_count: number
  excluded_row_count: number
  event_start_date: string
  event_end_date: string
  schema_version: string
  schema_hash: string | null
  feature_version: string | null
  raw_value_count: number | null
  ordered_output_count: number
  mean_quality_score: number | null
  manifest_version: string
  manifest_identity_checksum: string
  rows_checksum: string
  built_at: string
  integrity_status: 'PASS' | 'FAILED'
  integrity_issues: string[]
  required_family_coverage: HistoricalCoverageItem[]
  raw_family_coverage: HistoricalCoverageItem[]
  readiness: HistoricalReadinessSummary
}

export interface HistoricalCanonicalSummary {
  availability: 'AVAILABLE' | 'INTEGRITY_WARNING' | 'UNAVAILABLE'
  integrity_status: 'PASS' | 'FAILED' | 'UNAVAILABLE'
  official_games: number | null
  team_game_rows: number | null
  player_game_rows: number | null
  strict_feature_coverage: number | null
  leakage_status: 'PASS' | 'FAILED' | 'UNAVAILABLE'
  statcast_status: 'AVAILABLE' | 'PARTIAL' | 'UNAVAILABLE'
  historical_market_status: 'UNAVAILABLE'
  source_keys: string[]
  integrity_issues: string[]
}

export interface HistoricalResearchData {
  data_mode: 'live' | 'empty'
  availability: 'AVAILABLE' | 'PARTIAL' | 'INTEGRITY_WARNING' | 'UNAVAILABLE'
  temporal_scope: 'HISTORICAL'
  temporal_semantics: 'RETROSPECTIVE_RECONSTRUCTION'
  stage: 'GATE_3A_A2'
  season: number
  cutoff_policy_version: string | null
  built_at: string | null
  verdict: string
  historical_market_status: 'UNAVAILABLE'
  model_training_invoked: boolean | null
  model_fitting_invoked: boolean | null
  holdout_metrics_inspected: boolean | null
  promotion_invoked: boolean | null
  strict_population: HistoricalPopulationSummary | null
  enhanced_population: HistoricalPopulationSummary | null
  canonical_summary: HistoricalCanonicalSummary
  limitations: string[]
}

export interface HistoricalTeamPrior {
  team_id: string
  abbreviation: string
  name: string
  elo_rating: number | null
  runs_per_game: number | null
  runs_allowed_per_game: number | null
  offense_true_talent: number | null
  run_prevention_true_talent: number | null
  reliability: number | null
}

export interface HistoricalRunStrengthSnapshot {
  home_offense: number | null
  home_run_prevention: number | null
  away_offense: number | null
  away_run_prevention: number | null
  expected_home_win_probability: number | null
}

export interface HistoricalGameExplorerRow {
  game_id: string
  event_date: string
  scheduled_at: string
  matchup: string
  cutoff_at: string
  cutoff_policy_version: string
  quality_tier: 'A' | 'B'
  source_mode: 'LIVE'
  source_keys: string[]
  feature_completeness: number
  admission_quality_score: number
  present_feature_count: number
  total_feature_count: number
  starter_state: 'CONFIRMED' | 'PARTIAL' | 'MISSING'
  lineup_state: 'CONFIRMED' | 'PARTIAL' | 'MISSING'
  home_team_prior: HistoricalTeamPrior
  away_team_prior: HistoricalTeamPrior
  run_strength: HistoricalRunStrengthSnapshot
  market_available: boolean | null
  market_status: 'UNAVAILABLE'
  target_excluded: true
}

export interface HistoricalGameExplorerPage extends DataProvenance {
  data_mode: 'live' | 'empty'
  availability: 'AVAILABLE' | 'INTEGRITY_WARNING' | 'UNAVAILABLE'
  status_reason: string
  season: number
  page: number
  page_size: number
  total_pages: number
  total_rows: number
  team: string | null
  month: number | null
  tier: 'A' | 'B'
  source: string | null
  rows: HistoricalGameExplorerRow[]
}

export interface HistoricalDatasetPerformanceSummary {
  availability: 'AVAILABLE' | 'INTEGRITY_WARNING' | 'UNAVAILABLE'
  stage: 'GATE_3A_A2'
  season: number
  population: 'TIER_A_STRICT'
  row_count: number
  distinct_event_dates: number
  holdout_rows: number
  rolling_origin_folds: number
  readiness_status: string
  failed_checks: string[]
  status_reason: string
  model_training_invoked: boolean | null
  model_fitting_invoked: boolean | null
  holdout_metrics_inspected: boolean | null
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
  /** Optional until the Gate 3 backend publishes a governed evaluation artifact. */
  betting?: BettingPerformance | null
  offline_evaluation?: OfflineEvaluation | null
  /** Historical reconstruction readiness is evidence about a dataset, not model performance. */
  historical_dataset?: HistoricalDatasetPerformanceSummary | null
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

export interface BankrollDailyPerformance {
  date: string
  positions: number
  settled: number
  amount_risked: number
  profit_loss: number
  bankroll_after: number
  no_bet: boolean
  no_bet_reasons: string[]
}

export interface ClosingQuoteCoverage {
  positions_with_closing_quote: number
  settled_positions: number
  percentage: number | null
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
  experiment_status?: 'NOT_STARTED' | 'ACTIVE' | 'SCHEDULED'
  experiment_id?: string | null
  experiment_name?: string | null
  equity_curve: Array<{ date: string; bankroll: number; benchmark: number }>
  monthly: Array<{ month: string; positions: number; record: string; profit_loss: number; roi: number }>
  daily?: BankrollDailyPerformance[]
  positions: Position[]
  experiment_start?: string | null
  controlling_engine?: string | null
  code_commit?: string | null
  supported_markets?: string[]
  virtual_label?: string
  open_positions?: number
  settled_positions?: number
  no_bet_days?: number
  current_drawdown?: number
  closing_quote_coverage?: ClosingQuoteCoverage | null
  betting?: BettingPerformance | null
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

export interface HistoricalWarehouseHealth {
  availability: 'AVAILABLE' | 'PARTIAL' | 'INTEGRITY_WARNING' | 'UNAVAILABLE'
  season: number
  temporal_semantics: 'RETROSPECTIVE_RECONSTRUCTION'
  strict_rows: number
  enhanced_rows: number
  readiness_status: string
  mean_strict_feature_coverage: number | null
  integrity_status: 'PASS' | 'FAILED' | 'UNAVAILABLE'
  leakage_status: 'PASS' | 'FAILED' | 'UNAVAILABLE'
  statcast_status: 'AVAILABLE' | 'PARTIAL' | 'UNAVAILABLE'
  historical_market_status: 'UNAVAILABLE'
  failed_checks: string[]
  built_at: string | null
  detail: string
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
  historical_warehouse?: HistoricalWarehouseHealth | null
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

export type ShadowRecommendation =
  | 'NO_BET' | 'WATCH' | 'QUALIFIED_BET'
  | 'PROVISIONAL_EDGE' | 'QUALIFIED_SHADOW' | 'REVIEW_REQUIRED'

export interface ShadowDecisionView {
  market: string
  selection: string
  model_probability: number | null
  push_probability?: number | null
  has_quote: boolean
  book: string | null
  odds_snapshot_id?: string | null
  opposite_odds_snapshot_id?: string | null
  source_quote_event_id?: string | null
  quote_available_as_of?: string | null
  line: number | null
  american_odds: number | null
  best_american_odds: number | null
  no_vig_probability: number | null
  raw_edge: number | null
  no_vig_edge: number | null
  expected_value: number | null
  uncertainty_adjusted_edge: number | null
  recommendation: ShadowRecommendation
  virtual_stake: number
}

export interface ShadowEngineView {
  engine_id: string
  forecast_id: string
  status: string
  is_ready: boolean
  readiness_score: number
  readiness_threshold: number
  home_win_probability: number
  away_win_probability: number
  expected_home_score: number
  expected_away_score: number
  first_five_home_win_probability: number | null
  first_five_away_win_probability: number | null
  snapshot_type?: string
  snapshot_version?: number
  forecast_cutoff_utc?: string | null
  uncertainty?: Record<string, unknown>
  score_margin_distribution?: Array<{ home_minus_away: number; probability: number }>
  total_score_distribution?: Array<{ total_score: number; probability: number }>
  prediction_interval?: Record<string, number> | null
  model_manifest_sha256?: string | null
  model_binary_sha256?: string | null
  feature_vector_sha256?: string | null
  lineage?: Record<string, unknown>
  decisions: ShadowDecisionView[]
}

export interface ShadowGameView {
  event_id: string
  event_date: string
  sport?: string
  scheduled_start_utc?: string | null
  home_team_id: string
  away_team_id: string
  engines: ShadowEngineView[]
  model_disagreement: number | null
}

export interface ShadowBoardData {
  slate_date: string
  status: 'RESEARCH_SHADOW'
  executable: false
  sports_available?: string[]
  active_sport?: string | null
  lineage_disclosure?: {
    model: string
    status: string
    training_evidence: string
    training_cutoff: string
    feature_tier: string
    historical_oot_win_log_loss: number
    historical_sportsbook_benchmark: number
    historical_conclusion: string
  } | null
  games: ShadowGameView[]
}

export interface RescoreQuote {
  book_home: string
  book_away: string
  home_american: number
  away_american: number
  no_vig_home: number
  no_vig_away: number
  observed_at: string
}

export interface RescoreSideDecision {
  selection: string
  model_probability: number | null
  has_quote: boolean
  american_odds?: number
  no_vig_probability: number | null
  edge: number | null
  expected_value: number | null
  recommendation: ShadowRecommendation
  virtual_stake: number
}

export interface ProspectiveRescore {
  id: string
  game_id: string
  engine: string
  availability_change: {
    event_type: string | null
    source: string | null
    player_name: string | null
    description: string | null
  }
  forecast_before: { home_win_probability: number | null; readiness: number | null; uncertainty: number | null }
  forecast_after: { home_win_probability: number | null; readiness: number | null; uncertainty: number | null }
  quote_before: RescoreQuote | null
  quote_after: RescoreQuote | null
  book_moved: boolean
  decisions: { before: Record<string, RescoreSideDecision> | null; after: Record<string, RescoreSideDecision> | null }
  recommendation: ShadowRecommendation
  virtual_stake: number
  timing: Record<string, string | null>
  quote_timeline: Record<string, unknown>
}

export interface ProspectiveRescoreBoard {
  slate_date: string
  status: 'RESEARCH_SHADOW'
  executable: false
  rescores: ProspectiveRescore[]
}

export interface LiveShadowStatus {
  operational_state: string
  availability_polling_enabled: boolean
  odds_provider_enabled: boolean
  odds_credentials_present: boolean
  required_odds_config_variable: string | null
  injury_feed_status?: string
  injury_feed?: {
    state: string
    enabled: boolean
    key_present: boolean
    configured: boolean
    authenticated: boolean
    last_poll: string | null
    records_received: number | null
    trial_data: boolean
    changes_detected: number | null
    availability_events_created: number | null
    games_rescored: number | null
    matched_players: number | null
    last_error: string | null
  }
  executable: false
  quota: { quota_remaining: number | null; quota_floor: number; daily_request_budget: number; requests_today: number }
  cadence: {
    availability_slow_minutes: number
    availability_fast_minutes: number
    pregame_fast_window_hours: number
    odds_pregame_refresh_minutes: number
    quote_burst_offsets_seconds: number[]
  }
  last_successful_schedule_refresh: string | null
  last_successful_event_poll: string | null
  last_successful_odds_refresh: string | null
  last_event_received: string | null
  last_event_source: string | null
  last_rescore_duration_seconds: number | null
  active_games_monitored: number
  circuit_state: { last_provider_failure: string | null; last_failed_job: string | null }
}

// ---- Prediction Market Observer (Crowd & Market Pulse) ----
export interface PredictionProviderProbability {
  provider: string
  midpoint: number | null
  microprice?: number | null
  best_bid?: number | null
  best_ask?: number | null
  last_trade?: number | null
  bid_ask_spread?: number | null
  volume?: number | null
  open_interest?: number | null
  top_depth?: number | null
  quality_tier: string
  quote_age_seconds?: number | null
  mapping_status: string
  semantics_status: string
  in_play?: boolean
  observed_at?: string | null
}

export interface MarketPulseRow {
  game_id: string
  sport: string
  league: string
  home_team: string | null
  away_team: string | null
  home_abbrev: string | null
  away_abbrev: string | null
  starts_at: string | null
  status: string
  aisp_home_probability: number | null
  aisp_forecast_fresh: boolean
  sportsbook_home_probability: number | null
  polymarket_home_probability: number | null
  kalshi_home_probability: number | null
  research_consensus_home_probability: number | null
  consensus_eligible: boolean
  consensus_note: string | null
  diff_aisp_sportsbook: number | null
  diff_aisp_prediction: number | null
  diff_polymarket_kalshi: number | null
  cross_market_dispersion: number | null
  providers: PredictionProviderProbability[]
  movement_5m: number | null
  movement_30m: number | null
  movement_2h: number | null
  movement_24h: number | null
  first_mover: string | null
  mapping_status: string
  semantics_status: string
  best_quality_tier: string | null
  alert_count: number
}

export interface PredictionProviderHealth {
  provider: string
  enabled: boolean
  connection_mode: string
  last_discovery_at: string | null
  last_success_at?: string | null
  last_snapshot_at: string | null
  subscribed_markets: number
  last_error: string | null
  status: string
  events_inspected: number
  markets_inspected: number
  raw_markets_inspected?: number | null
  excluded?: Record<string, number>
  eligible_game_markets: number
  mapped_markets: number
  unresolved_markets: number
  last_job_status: string | null
}

export type MarketPulseBoardState =
  | 'OBSERVER_DISABLED'
  | 'PROVIDERS_DISABLED'
  | 'DISCOVERY_PENDING'
  | 'PROVIDERS_HEALTHY_ZERO_ELIGIBLE'
  | 'ELIGIBLE_MARKETS_AVAILABLE'
  | 'PROVIDER_DEGRADED'

export interface ObserverJobStatus {
  name: string
  enabled: boolean
  cadence_minutes: number
  scheduler_running: boolean
  last_run_at: string | null
  last_status: string | null
  last_success_at: string | null
  last_error: string | null
  next_run_at: string | null
  records_inspected: number
  records_persisted: number
}

export interface MarketPulseBoard extends DataProvenance {
  date: string
  sport: string | null
  sports_available: string[]
  disclosure: string
  public_display_enabled: boolean
  observer_enabled: boolean
  board_state: MarketPulseBoardState
  state_reason: string
  providers: PredictionProviderHealth[]
  next_discovery_at: string | null
  semantics_review_count: number
  rows: MarketPulseRow[]
  summary: Record<string, unknown>
  excluded_summary?: Record<string, number>
  jobs?: ObserverJobStatus[]
}

export interface PredictionTimelinePoint {
  time: string
  aisp: number | null
  sportsbook: number | null
  polymarket: number | null
  kalshi: number | null
  consensus: number | null
}

export interface PredictionOrderBookView {
  provider: string
  best_bid: number | null
  best_ask: number | null
  midpoint: number | null
  microprice: number | null
  spread: number | null
  bids: number[][]
  asks: number[][]
  observed_at: string | null
  quality_tier: string | null
}

export interface PredictionMovementEvent {
  time: string
  provider: string
  market_type: string
  selection: string
  old_probability: number | null
  new_probability: number | null
  magnitude: number | null
  alert_type: string | null
  details: Record<string, unknown>
}

export interface PredictionContractDetail {
  provider: string
  market_type: string
  selection: string
  line_value: number | null
  market_question: string | null
  resolution_rules: string | null
  settlement_source: string | null
  market_close_at: string | null
  semantics_status: string
  line_source: string
}

export interface PredictionMappingDetail {
  provider: string
  provider_event_id: string
  mapping_status: string
  semantics_status: string
  start_time_delta_seconds: number | null
  game_time_agreement: string | null
}

export interface MarketPulseGameDetail extends DataProvenance {
  game_id: string
  home_team: string | null
  away_team: string | null
  starts_at: string | null
  status: string
  disclosure: string
  lead_lag_note: string
  timeline: PredictionTimelinePoint[]
  order_books: PredictionOrderBookView[]
  movement_events: PredictionMovementEvent[]
  contracts: PredictionContractDetail[]
  mappings: PredictionMappingDetail[]
  row: MarketPulseRow | null
}

export interface QualificationFunnelStep { key: string; label: string; count: number }
export interface QualificationFunnelGame {
  game_id: string
  matchup: string
  starts_at: string | null
  recommendation_state: string
  failed_gate: string | null
  rejection_reasons: string[]
  readiness: number | null
  uncertainty: number | null
  best_market: string | null
  best_selection: string | null
  experiment_status: string
  tier?: string
  starter_state_home?: string | null
  starter_state_away?: string | null
  lineup_state?: string | null
  adjusted_edge?: number | null
}
export interface QualificationFunnelDiagnostics {
  blocked_only_by_confirmed_status: number
  blocked_only_by_lineup_confirmation: number
  blocked_only_by_unsupported_market: number
  blocked_only_by_adjusted_edge: number
  blocked_by_other_evidence: number
}
export interface QualificationFunnel {
  date: string
  policy?: string
  experiment_market_types: string[]
  steps: QualificationFunnelStep[]
  qualified_selections: number
  positions_created: number
  exposure_cap_exclusions: number
  top_rejection_reasons: Array<{ reason: string; count: number }>
  diagnostics?: QualificationFunnelDiagnostics
  experiment_registered?: boolean
  effective_date?: string | null
  games: QualificationFunnelGame[]
}
