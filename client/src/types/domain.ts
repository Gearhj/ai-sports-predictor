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
  league_code?: string | null
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

export type V2Lifecycle =
  | 'NOT_YET_EVALUATED'
  | 'PENDING_FINAL_EVIDENCE'
  | 'PROVISIONALLY_ELIGIBLE'
  | 'FINAL_QUALIFIED'
  | 'FINAL_NO_BET'

export interface V2FeatureDecomposition {
  version: string | null
  model_required_missing: string[]
  duplicate_components: number
  fallback_components: number
  optional_components: number
}

export interface V2PolicyView {
  policy_version: string
  tier: 'TIER_A_FULL_EVIDENCE' | 'TIER_B_PROBABLE_STABLE' | 'TIER_C_LINEUP_PENDING' | 'TIER_D_INELIGIBLE'
  lifecycle: V2Lifecycle
  recommendation: string
  reason: string
  reasons: string[]
  gates_passed: string[]
  critical_evidence_complete: boolean
  starter_state_home: string | null
  starter_state_away: string | null
  starter_stable_since_home?: string | null
  starter_stable_since_away?: string | null
  lineup_state: string | null
  pregame_evidence?: PregameEvidence | null
  selection: string | null
  feature_decomposition?: V2FeatureDecomposition | null
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
  clv_sample_size?: number
  total_risked?: number
  started_at?: string
  policy_checksum: string | null
  role?: 'STRICT_CONTROL' | 'POLICY_CHALLENGER'
  policy_label?: string
  policy_relationship?: string
  model_name?: string | null
  model_version?: string | null
  model_status?: string | null
  decision_metrics?: ExperimentDecisionMetrics
}

export interface ExperimentComparison {
  experiments: ExperimentComparisonRow[]
  superseded?: Array<{
    name: string
    policy: string
    status: string
    effective_date: string
    decisions: number
    positions: number
    note: string
  }>
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
  settled_stake?: number
  open_exposure?: number
  payouts?: number
  roi: number | null
  yield_pct: number | null
  bankroll_return_pct?: number | null
  return_on_settled_stake_pct?: number | null
  peak_bankroll?: number | null
  lowest_bankroll?: number | null
  max_drawdown_pct_of_peak?: number | null
  streak_result?: string | null
  streak_length?: number
  best_settled_profit?: number | null
  worst_settled_profit?: number | null
  top_two_wins_share_of_profit_pct?: number | null
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
  bankroll_chain_reconciled?: boolean
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

/**
 * Why sportsbook data is or is not current. Four separate facts, deliberately
 * not collapsed: ingestion state, provider credit, the last real observation,
 * and whether that observation is historical relative to the slate shown.
 */
export interface MarketFeedStatus {
  ingestion_blocked: boolean
  reason: string
  provider: string
  quota_remaining: number | null
  quota_floor: number
  quota_observed_at: string | null
  latest_observation_at: string | null
  latest_observation_is_historical: boolean
  recovery_probe_available: boolean
  recovery_blocked_reason: string | null
}

export interface MarketBoardData extends DataProvenance {
  date: string
  books: string[]
  quota_remaining: number | null
  rows: MarketBoardRow[]
  feed_status?: MarketFeedStatus | null
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
  // Additive public-ledger evidence (persisted at decision or settlement
  // time). null means "not recorded" — never zero.
  matchup?: string | null
  sportsbook?: string | null
  bankroll_after?: number | null
  settlement_sequence?: number | null
  settled_at?: string | null
  beat_close?: boolean | null
  model_probability?: number | null
  market_probability?: number | null
  raw_edge?: number | null
  adjusted_edge?: number | null
  decision_id?: string | null
  decision_phase?: string | null
  recommended_at?: string | null
}

/** One point of the settlement-ordered equity curve (sequence 0 = opening bankroll). */
export interface EquityCurvePoint {
  date: string
  bankroll: number
  benchmark: number
  sequence?: number
  position_id?: string | null
  selection?: string | null
  result?: 'WIN' | 'LOSS' | 'PUSH' | 'VOID' | null
  profit_loss?: number
  high_water?: number
  drawdown?: number
  settled_at?: string | null
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
  lowest_bankroll?: number | null
  total_return: number
  /**
   * Legacy: realized profit over ALL stake, open exposure included. Never
   * display this as a return -- use the two explicit measures below, each of
   * which names its own denominator.
   */
  roi: number
  /** Realized profit / starting bankroll. +0.345% on this ledger. */
  bankroll_return_pct?: number | null
  /** Realized profit / settled stake. +11.50% on this ledger. */
  return_on_settled_stake_pct?: number | null
  settled_stake?: number | null
  open_exposure?: number | null
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
  equity_curve: EquityCurvePoint[]
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
  // Honest experiment identity: same model, different decision policy.
  experiment_key?: 'v1' | 'v2' | null
  experiment_role?: 'STRICT_CONTROL' | 'POLICY_CHALLENGER' | null
  policy_label?: string | null
  policy_relationship?: string | null
  model_name?: string | null
  model_version?: string | null
  model_status?: string | null
  model_identity_source?: string | null
  decision_metrics?: ExperimentDecisionMetrics | null
  staking_policy?: string | null
  policy_checksum?: string | null
  experiment_start_date?: string | null
}

export interface ExperimentDecisionMetrics {
  candidate_evaluations: number
  games_evaluated: number
  qualified_evaluations: number
  qualified_selections: number
  frozen_positions: number
  settled_positions: number
  evidence_stage: 'NO_EVIDENCE' | 'EARLY_EVIDENCE' | 'ACCUMULATING' | 'MATURE'
  evidence_note: string
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
  /** Stated separately; never folded into the two win probabilities. */
  tie_probability?: number | null
  expected_margin_home?: number | null
  expected_total_score?: number | null
  source_evidence_utc?: string | null
  forecast_published_utc?: string | null
  imported_at_utc?: string | null
  evidence_basis?: {
    missing_input_count?: number
    treatment?: string
    note?: string
    excluded_evidence?: string[]
  } | null
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
  /** Canonical AISP game, so a card can link straight to its analysis. */
  canonical_game_id?: string | null
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
  // Point-in-time honesty (which observation this price is).
  observation_kind?: 'FINAL_PREGAME' | 'PREGAME' | 'IN_PLAY' | 'FINAL' | 'UNKNOWN'
  seconds_before_start?: number | null
  timeline_cut?: string | null
  latest_observed_at?: string | null
  latest_in_play?: boolean
  mapping_authority?: string | null
  semantics_authority?: string | null
  freshness_seconds?: number | null
  decision_eligible?: boolean
}

export type GameEvidenceState =
  | 'REVIEWED_PREGAME'
  | 'PREGAME_UNREVIEWED'
  | 'EXCLUDED_IN_PLAY'
  | 'LISTED_NOT_QUOTED'
  | 'NO_MAPPED_MARKET'
  | 'PROVIDER_UNAVAILABLE'

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
  // Canonical-slate coverage (why a provider column is blank).
  coverage_state?: ProviderCoverageState | 'NO_PROVIDER_ENABLED'
  provider_coverage?: ProviderCoverage[]
  decision_eligible_quote?: boolean
  unreviewed_observation?: boolean
  diff_aisp_prediction_unreviewed?: number | null
  display_note?: string | null
  // Disjoint game-level evidence state (headline authority).
  evidence_state?: GameEvidenceState
  reviewed_probability_min?: number | null
  reviewed_probability_max?: number | null
  market_disagreement?: number | null
}

export type ProviderCoverageState =
  | 'PROVIDER_DISABLED'
  | 'PROVIDER_ERROR'
  | 'NO_MAPPED_MARKET'
  | 'MAPPING_REVIEW_REQUIRED'
  | 'MAPPING_REJECTED'
  | 'MAPPED_NOT_QUOTED'
  | 'QUOTED_STRUCTURALLY_EXCLUDED'
  | 'QUOTED_UNREVIEWED'
  | 'QUOTED_REVIEWED'

export interface ProviderCoverage {
  provider: string
  state: ProviderCoverageState
  reason: string
  contracts_listed: number
  mapping_status: string | null
  semantics_status: string | null
  quality_tier: string | null
  last_snapshot_at: string | null
  pregame_observed_at?: string | null
  pregame_timeline_cut?: string | null
  latest_in_play?: boolean
  semantics_authority?: string | null
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
  | 'RESEARCH_OBSERVATIONS_UNREVIEWED'
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
  canonical_games?: number
  coverage?: Record<string, number>
  coverage_by_provider?: Record<string, Record<string, number>>
  semantic_template_decisions_pending?: number | null
}

export interface PredictionTimelinePoint {
  time: string
  aisp: number | null
  sportsbook: number | null
  polymarket: number | null
  kalshi: number | null
  consensus: number | null
  polymarket_pregame?: boolean | null
  kalshi_pregame?: boolean | null
}

export interface PredictionTimelineMarker {
  key: string
  label: string
  time: string
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

export interface PregameCaptureCell {
  milestone: string
  state: string
  reason: string
  snapshot_id: string | null
  observed_at: string | null
  received_at: string | null
  timeline_cut: string | null
  midpoint: number | null
  best_bid: number | null
  best_ask: number | null
  seconds_before_start: number | null
  observed_before_start: boolean | null
  late_ingestion: boolean | null
}

export interface PregameCaptureProvider {
  mapping_status: string | null
  provider_event_id: string | null
  cells: PregameCaptureCell[]
}

export interface PregameCaptureRow {
  game_id: string
  label: string
  home: string
  away: string
  starts_at: string | null
  game_status: string
  doubleheader_status: string | null
  providers: Record<string, PregameCaptureProvider>
}

export interface PregameCaptureCoverage {
  date: string
  generated_at: string
  milestones: string[]
  providers: string[]
  states: string[]
  canonical_games: number
  rows: PregameCaptureRow[]
  counts: Record<string, Record<string, Record<string, number>>>
  totals: Record<string, Record<string, number>>
  capture_cadence_minutes: number
  disclosure: string
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
  timeline_markers?: PredictionTimelineMarker[]
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
  lifecycle?: V2Lifecycle
  gates_passed?: string[]
  critical_evidence_complete?: boolean
  starter_state_home?: string | null
  starter_state_away?: string | null
  starter_stable_since_home?: string | null
  starter_stable_since_away?: string | null
  lineup_state?: string | null
  model_required_missing?: string[]
  adjusted_edge?: number | null
  cutoff_at?: string
  reference_at?: string
  pregame_evidence?: PregameEvidence | null
}

/** Concise pregame evidence: source, observation time, age at the evaluation reference, freshness, exact block. */
export interface PregameStarterEvidence {
  state: string | null
  status: string | null
  source: string | null
  observed_at: string | null
  age_minutes_at_reference: number | null
  freshness: 'FRESH' | 'STALE' | 'MISSING' | 'INVALID_FUTURE_DATED' | string
  stable_since?: string | null
  label: string
  reasons: string[]
}

export interface PregameLineupTeamEvidence {
  source_state: string
  label: string
  observed_at: string | null
}

export interface PregameEvidence {
  reference_at: string
  cutoff_at: string
  reference_is_cutoff: boolean
  starter_home: PregameStarterEvidence
  starter_away: PregameStarterEvidence
  starter_block_category: 'PASS' | 'STALE' | 'MISSING' | 'UNSTABLE' | 'CONFLICT' | 'EVENT' | 'NOT_EVALUATED' | string
  starter_block_reason: string | null
  lineup: { state: string | null; label: string; home: PregameLineupTeamEvidence | null; away: PregameLineupTeamEvidence | null; source: string | null; reasons?: string[] }
  source_note: string
  any_fresh: boolean
}
export interface QualificationFunnelDiagnostics {
  awaiting_final_pregame_evaluation: number
  blocked_only_by_starter_instability: number
  blocked_only_by_lineup_conflict: number
  blocked_only_by_model_required_input: number
  previously_blocked_by_duplicate_feature_completeness_gate: number
  unsupported_market: number
  edge_ev_kelly_failure: number
  portfolio_cap_failure: number
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
  starter_evidence_reconciliation?: Record<string, number>
  evidence_reference_policy?: string
}

/** Admin-only odds credit budget and collection plan (AISP_ODDS_OPERATOR_STATUS_V1). */
/** One game's coverage state and, if it is not covered, the one exact reason. */
export interface QuoteCoverageEvent {
  game_id: string
  matchup: string
  starts_at_utc: string
  cutoff_utc: string
  state: string
  covered: boolean
  at_risk: boolean
  cause: string | null
  cause_detail: string | null
  quote_age_minutes_at_cutoff?: number | null
  next_checkpoint_utc?: string | null
  last_successful_request_utc?: string | null
}

export interface QuoteCoverageAlert {
  severity: 'CUTOFF_MISSED' | 'NO_QUOTE_INSIDE_90_MINUTES' | 'CLEAR'
  active: boolean
  headline: string
  threshold_minutes_before_cutoff: number
  games: Array<{
    game_id: string; matchup: string; cutoff_utc: string
    minutes_to_cutoff: number; state: string
    cause: string | null; cause_detail: string | null
  }>
}

export interface QuoteCoverageReport {
  alert?: QuoteCoverageAlert
  schema_version: string
  league: string
  slate_date: string | null
  generated_at_utc: string
  contract: {
    cutoff_minutes_before_start: number
    freshness_window_minutes: number
    freshness_target_minutes: number
    quote_definition: string
  }
  totals: {
    events: number; covered: number; within_freshness_target: number
    at_risk: number; missed: number; coverage_rate: number
  }
  quote_age_at_cutoff_minutes: { median: number | null; max: number | null; min: number | null }
  by_state: Record<string, number>
  by_cause: Record<string, number>
  cause_explanations: Record<string, string>
  events: QuoteCoverageEvent[]
}

export interface BudgetLane {
  lane: string
  label: string
  precedence: number
  protected: boolean
  purpose: string
  monthly_allocation: number
  spent: number
  remaining: number
  daily_cap: number
  spent_today: number
  remaining_today: number
  requests: number
}

export interface BudgetLanes {
  schema_version: string
  lanes: BudgetLane[]
  total_allocated: number
  protected_unspent: number
  unattributed_spend: number
  reconciliation: {
    state: string; optional_locked: boolean; detail: string
    provider_used: number | null; lane_total: number; delta: number | null
  }
}

export interface OddsCollectionStatus {
  quote_coverage?: QuoteCoverageReport
  budget_lanes?: BudgetLanes
  schema_version: 'AISP_ODDS_OPERATOR_STATUS_V1'
  generated_at_utc: string
  collection_mode: string
  collection_reason: string
  collection_detail: string
  legacy_blanket_polling_enabled: boolean
  data_mode?: DataMode
  budget: {
    provider: string
    billing_window_start_utc: string
    billing_window_end_utc: string
    billing_window_basis: string
    monthly_cap: number
    reserve: number
    reserve_intact: boolean
    spent: number
    ledger_spent: number
    budgeted_remaining: number
    provider_used: number | null
    provider_remaining: number | null
    provider_reading_at_utc: string | null
    balance_state: string
    balance_note: string
    daily_cap: number
    daily_spent: number
    daily_remaining: number
    requests_this_window: number
    floor: number
    floor_latched: boolean
  }
  projection: {
    rate_credits_per_day: number
    observed_rate_credits_per_day: number
    days_remaining?: number
    exhaustion_date: string | null
    window_ends?: string
    survives_the_month: boolean
    note: string
  }
  planned_credits_remaining_today: number
  plans: Array<{
    league_code: string
    sport_key: string
    markets: string[]
    regions: string[]
    credit_cost_per_request: number
    open_games: number
    checkpoints: string[]
    projected_credits: number
    rationale: string
  }>
  next_checkpoint: Record<string, unknown> | null
  provider_floor_guard: {
    latched: boolean
    remaining: number | null
    floor: number
    probe_blocked_reason?: string | null
  } & Record<string, unknown>
  spend_by_day: Array<{ day: string; endpoint_key: string; status: string; requests: number; credits: number }>
  providers: Array<{
    provider: string
    display_name: string
    governed: boolean
    bookmaker_namespace: string
    authority: string
  }>
  governed_sources: string[]
  fallback_provider: {
    provider: string
    governed: boolean
    enabled: boolean
    mapping_state: string
    mapping_detail: string
    ready_to_enable: boolean
    blocked_on: string
    unverified_assumptions: Array<{ claim: string; settled_by: string }>
  }
}


/** AISP_EXPERIMENT_FUNNEL_V1 — where the experiment's games go, and why. */
export interface ExperimentFunnelReason {
  reason: string
  label: string
  explanation: string
  count: number
}

export interface ExperimentFunnelStage {
  key: string
  label: string
  count: number
  dropped: number
  explanation: string
  reasons: ExperimentFunnelReason[]
}

export interface ExperimentFunnelData extends DataProvenance {
  schema_version: string
  available: boolean
  requirement?: string
  experiment?: {
    id: string; name: string; policy_version: string
    recommendation_policy: string; starting_bankroll: number; start_date: string | null
  }
  window_days: number | null
  generated_at_utc: string
  cutoff_minutes: number
  min_adjusted_edge: number
  stages: ExperimentFunnelStage[]
  blocked_totals: ExperimentFunnelReason[]
  price_restoration: {
    games_without_a_usable_price: number
    games_with_a_usable_price: number
    cleared_when_priced: number
    clear_rate_when_priced: number | null
    projected_additional_clearing: number | null
    statement: string
    method: string
  }
  thresholds_unchanged: string
}
