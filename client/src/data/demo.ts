import type {
  BankrollData,
  DashboardData,
  ForecastGame,
  GameAnalysisData,
  HealthData,
  MarketBoardData,
  PerformanceData,
  TeamSummary,
} from '../types/domain'

const team = (
  id: string,
  abbreviation: string,
  city: string,
  name: string,
  record: string,
  color: string,
): TeamSummary => ({ id, abbreviation, city, name, record, color })

const teams = {
  nym: team('nym', 'NYM', 'New York', 'Mets', '66–52', '#ff5910'),
  atl: team('atl', 'ATL', 'Atlanta', 'Braves', '59–60', '#ce1141'),
}

export const demoGames: ForecastGame[] = [
  {
    id: 'demo-nym-atl',
    start_time: '7:15 PM ET',
    status: 'Pregame',
    venue: 'Truist Park · Atlanta, GA',
    forecast_available: true,
    market_available: true,
    away_team: teams.nym,
    home_team: teams.atl,
    away_pitcher: { name: 'Sean Manaea', hand: 'LHP', confirmed: true, era: 3.61, xera: 3.74 },
    home_pitcher: { name: 'Spencer Strider', hand: 'RHP', confirmed: true, era: 3.18, xera: 3.05 },
    projected_away_runs: 3.7,
    projected_home_runs: 4.6,
    projected_total: 8.3,
    market_total: 8.5,
    away_win_probability: 42.1,
    home_win_probability: 57.9,
    fair_away_moneyline: 138,
    fair_home_moneyline: -138,
    best_away_moneyline: 132,
    best_home_moneyline: -124,
    market_home_probability: 54.1,
    model_edge: 3.8,
    edge_side: 'home',
    opportunity: 'STANDARD EDGE',
    confidence: 74,
    data_readiness: 96,
    data_as_of: 'Illustrative snapshot · 6:42 PM',
    lifecycle_stage: 'T_MINUS_60M',
    time_to_first_pitch_seconds: 1980,
    last_refresh_at: 'Illustrative source refresh · 6:40 PM',
    next_scheduled_refresh_at: 'Illustrative scheduled refresh · 6:45 PM',
    final_forecast_status: 'NOT_STARTED',
    final_snapshot_frozen: false,
    away_lineup: { status: 'PROJECTED', complete: true, observed_at: null, available_as_of: null, source: 'Illustrative lineup source' },
    home_lineup: { status: 'CONFIRMED', complete: true, observed_at: null, available_as_of: null, source: 'Illustrative lineup source' },
    readiness_detail: [
      { key: 'schedule', label: 'Schedule', status: 'ready', score: 10, weight: 10, detail: 'Illustrative schedule evidence present', critical: true },
      { key: 'lineups', label: 'Lineups', status: 'stale', score: 7.5, weight: 10, detail: 'Atlanta confirmed; New York remains projected', critical: true },
    ],
    books_reporting: 3,
    last_update: 'Illustrative snapshot',
  },
]

const demoDate = 'Illustrative date · August 12, 2026'

export const demoDashboard: DashboardData = {
  data_mode: 'demo',
  date: demoDate,
  summary: {
    games: 1,
    complete_forecasts: 1,
    qualifying_edges: 1,
    last_refresh: 'Static illustrative snapshot',
    overall_data_health: 94,
    official_bankroll: 1003.19,
    season_return: 0.32,
  },
  games: demoGames,
}

export const demoGameAnalysis: GameAnalysisData = {
  data_mode: 'demo',
  game: demoGames[0],
  data_as_of: 'Illustrative pregame snapshot · August 12, 2026',
  model_version: 'DEVELOPMENT EXAMPLE',
  forecast_version: 'ILLUSTRATIVE · v1',
  weather: {
    temperature_f: 84,
    description: 'Partly cloudy',
    wind_mph: 8,
    wind_direction: 'Out to left',
    humidity: 62,
    run_effect: 0.18,
  },
  probability_comparison: [
    { label: 'ASP ensemble', home: 57.9, away: 42.1 },
    { label: 'No-vig market', home: 54.1, away: 45.9 },
    { label: 'Record strength baseline', home: 53.8, away: 46.2 },
  ],
  run_distribution: [
    { runs: 0, away: 4, home: 2 },
    { runs: 1, away: 10, home: 7 },
    { runs: 2, away: 16, home: 12 },
    { runs: 3, away: 20, home: 17 },
    { runs: 4, away: 19, home: 19 },
    { runs: 5, away: 14, home: 18 },
    { runs: 6, away: 9, home: 13 },
    { runs: 7, away: 5, home: 7 },
    { runs: 8, away: 2, home: 4 },
    { runs: 9, away: 1, home: 1 },
  ],
  market_prices: [
    { book: 'Example Book A', away: 126, home: -148, observed_at: 'Illustrative snapshot' },
    { book: 'Example Book B', away: 124, home: -146, observed_at: 'Illustrative snapshot' },
    { book: 'Example Book C', away: 132, home: -142, observed_at: 'Illustrative snapshot', is_best_away: true, is_best_home: true },
  ],
  pitcher_metrics: [
    { metric: 'ERA', away: 3.61, home: 3.18, preferred: 'home' },
    { metric: 'xERA', away: 3.74, home: 3.05, preferred: 'home' },
    { metric: 'K rate', away: '23.8%', home: '31.2%', preferred: 'home' },
    { metric: 'BB rate', away: '7.9%', home: '7.1%', preferred: 'home' },
    { metric: 'xwOBA allowed', away: '.311', home: '.287', preferred: 'home' },
    { metric: 'Last 30d workload', away: '28.1 IP', home: '25.2 IP', preferred: 'neutral' },
  ],
  offense_metrics: [
    { metric: 'Runs / game', away: 4.62, home: 4.49, preferred: 'away' },
    { metric: 'wRC+', away: 112, home: 109, preferred: 'away' },
    { metric: 'OPS', away: '.747', home: '.754', preferred: 'home' },
    { metric: 'K rate', away: '21.1%', home: '23.4%', preferred: 'away' },
    { metric: 'vs RHP wOBA', away: '.326', home: '.337', preferred: 'home' },
  ],
  bullpen_metrics: [
    { metric: 'Season ERA', away: 3.82, home: 3.57, preferred: 'home' },
    { metric: 'Last 14d xFIP', away: 4.01, home: 3.42, preferred: 'home' },
    { metric: 'Prior 3d innings', away: 10.2, home: 7.1, preferred: 'home', context: 'Lower workload is favorable' },
    { metric: 'High-leverage arms available', away: '2 / 3', home: '3 / 3', preferred: 'home' },
  ],
  statcast_metrics: [
    { metric: 'Team xwOBA', away: '.331', home: '.340', preferred: 'home' },
    { metric: 'Barrel rate', away: '8.8%', home: '10.1%', preferred: 'home' },
    { metric: 'Hard-hit rate', away: '39.6%', home: '42.4%', preferred: 'home' },
    { metric: 'Avg. exit velocity', away: '89.1 mph', home: '90.0 mph', preferred: 'home' },
  ],
  model_components: [
    { model: 'Team-strength baseline', home_probability: 53.8, expected_margin: 0.42, weight: null },
    { model: 'Statistical run model', home_probability: 58.7, expected_margin: 0.96, weight: null },
    { model: 'Gradient boosting challenger', home_probability: 59.1, expected_margin: 1.07, weight: null },
  ],
  drivers: [
    { label: 'Starting-pitcher quality', impact: 3.2, detail: 'Strider’s strikeout and contact-quality profile raises Atlanta’s win probability.' },
    { label: 'Bullpen availability', impact: 1.4, detail: 'Atlanta projects three rested high-leverage relievers.' },
    { label: 'Home park and weather', impact: 0.8, detail: 'Warm air and a light breeze out favor right-handed power.' },
    { label: 'Mets recent offense', impact: -1.1, detail: 'New York’s 14-day contact quality remains above its season baseline.' },
    { label: 'Market prior', impact: -0.5, detail: 'Consensus market probability tempers the model-only estimate.' },
  ],
  forecast_history: [
    { time: '9:05 AM', version: 'MORNING · v1', home_probability: 55.2, market_probability: 53.3 },
    { time: '1:30 PM', version: 'EARLY · v2', home_probability: 56.1, market_probability: 53.8 },
    { time: '4:18 PM', version: 'PREGAME · v3', home_probability: 57.2, market_probability: 54.0 },
    { time: '6:42 PM', version: 'PREGAME · v4', home_probability: 57.9, market_probability: 54.1 },
  ],
  data_audit: [
    { source: 'Example schedule', status: 'ready', updated: 'Illustrative snapshot', detail: 'Example game and probable pitchers' },
    { source: 'Example Statcast', status: 'ready', updated: 'Illustrative snapshot', detail: 'Static contact-quality examples' },
    { source: 'Example market', status: 'ready', updated: 'Illustrative snapshot', detail: '3 fictionalized book observations' },
    { source: 'Example weather', status: 'ready', updated: 'Illustrative snapshot', detail: 'Static ballpark conditions' },
    { source: 'Example lineups', status: 'watch', updated: 'Illustrative snapshot', detail: 'Atlanta confirmed · New York projected' },
  ],
  simulation: {
    iterations: 20000,
    home_cover_probability: 47.8,
    over_probability: 46.1,
    most_likely_score: 'ATL 5 – NYM 3',
  },
}

export const demoMarketBoard: MarketBoardData = {
  data_mode: 'demo',
  date: demoDate,
  books: ['Example Book A', 'Example Book B', 'Example Book C'],
  quota_remaining: null,
  rows: [
    { id: 'example-market', game_id: 'demo-nym-atl', matchup: 'NYM @ ATL', start_time: '7:15 PM ET', market_type: 'Moneyline', selection: 'Atlanta', asp_fair: -138, consensus: -145, edge: 3.8, observed_at: 'Illustrative snapshot', books: [{ book: 'Example Book A', price: -148 }, { book: 'Example Book B', price: -146 }, { book: 'Example Book C', price: -144, best: true }] },
  ],
}

export const demoPerformance: PerformanceData = {
  data_mode: 'demo',
  evaluation_available: true,
  market_benchmark_available: true,
  model_version: 'DEVELOPMENT EXAMPLE',
  model_status: 'DEVELOPMENT',
  evaluated_games: 100,
  brier_score: 0.218,
  log_loss: 0.628,
  accuracy: 58.6,
  run_mae: 1.17,
  total_mae: 2.31,
  versus_market_brier: -0.006,
  calibration: [
    { predicted: 35, observed: 36, count: 12 }, { predicted: 45, observed: 43, count: 24 },
    { predicted: 55, observed: 56, count: 32 }, { predicted: 65, observed: 63, count: 22 },
    { predicted: 75, observed: 73, count: 10 },
  ],
  rolling: [
    { date: 'Example 1', asp_brier: .229, market_brier: .231, accuracy: 55.2 },
    { date: 'Example 2', asp_brier: .224, market_brier: .228, accuracy: 56.8 },
    { date: 'Example 3', asp_brier: .219, market_brier: .225, accuracy: 58.1 },
  ],
  model_components: [
    { model: 'ASP Ensemble', brier: .218, log_loss: .628, run_mae: 1.17, status: 'DEVELOPMENT' },
    { model: 'Gradient boosting', brier: .221, log_loss: .635, run_mae: 1.21, status: 'COMPONENT' },
    { model: 'Run distribution', brier: .226, log_loss: .644, run_mae: 1.25, status: 'COMPONENT' },
    { model: 'Team-strength baseline', brier: .233, log_loss: .658, run_mae: 1.38, status: 'BASELINE' },
  ],
  by_confidence: [
    { bucket: 'Low example', forecasts: 15, accuracy: 51.9, brier: .246 },
    { bucket: 'Moderate example', forecasts: 40, accuracy: 55.7, brier: .226 },
    { bucket: 'High example', forecasts: 35, accuracy: 62.8, brier: .204 },
    { bucket: 'Very high example', forecasts: 10, accuracy: 68.3, brier: .186 },
  ],
  offline_evaluation: {
    status: 'AVAILABLE',
    status_reason: null,
    model_version: 'DEVELOPMENT EXAMPLE',
    manifest_version: 'example-manifest-v1',
    feature_version: 'example-features-v1',
    evaluation_policy_version: 'example-policy-v1',
    rolling_origin_folds: 3,
    train_window: { start_date: '2022-04-07', end_date: '2023-09-30', games: 3600 },
    validation_window: { start_date: '2024-03-28', end_date: '2024-09-29', games: 2400 },
    holdout_window: { start_date: '2025-03-27', end_date: '2025-09-28', games: 2400 },
    validation_metrics: { brier_score: .220, log_loss: .632, expected_calibration_error: .031, run_mae: 1.22 },
    holdout_metrics: { brier_score: .224, log_loss: .641, expected_calibration_error: .035, run_mae: 1.26 },
    baselines: [
      { name: 'Market consensus example', sample_size: 2100, brier_score: .226, log_loss: .646, expected_calibration_error: .039, run_mae: null },
      { name: 'Team-strength baseline example', sample_size: 2400, brier_score: .233, log_loss: .658, expected_calibration_error: .047, run_mae: 1.38 },
    ],
    promotion: {
      decision: 'PENDING',
      reason: 'Illustrative evidence only; demo results can never promote a model.',
      criteria_version: 'example-promotion-v1',
    },
  },
}

export const demoBankroll: BankrollData = {
  data_mode: 'demo',
  starting_bankroll: 1000,
  current_bankroll: 1003.19,
  peak_bankroll: 1005.8,
  total_return: 3.19,
  roi: 4.06,
  max_drawdown: -1.63,
  record: '2–2–1',
  positions_count: 5,
  experiment_status: 'ACTIVE',
  policy: 'PROVISIONAL PHASE 1 BANKROLL POLICY',
  policy_detail: 'Policy values are loaded from the public API configuration. This is an illustrative forecasting simulation—not a live result.',
  equity_curve: [
    { date: 'Start', bankroll: 1000, benchmark: 1000 }, { date: 'Example 1', bankroll: 987.5, benchmark: 1000 },
    { date: 'Example 2', bankroll: 1005.8, benchmark: 1000 }, { date: 'Example 3', bankroll: 1005.8, benchmark: 1000 },
    { date: 'Example 4', bankroll: 989.4, benchmark: 1000 }, { date: 'Example 5', bankroll: 1003.19, benchmark: 1000 },
  ],
  monthly: [
    { month: 'Illustrative sample', positions: 5, record: '2–2–1', profit_loss: 3.19, roi: 4.06 },
  ],
  positions: [
    { id: 'p58', date: 'Example 5', game_id: 'demo-nym-atl', selection: 'Atlanta ML example', market: 'Moneyline', price: -132, stake: 18.2, result: 'WIN', profit_loss: 13.79, closing_price: -144, clv: 1.8 },
    { id: 'p57', date: 'Example 4', game_id: 'demo-nym-atl', selection: 'New York +1.5 example', market: 'Run line', price: -110, stake: 16.4, result: 'LOSS', profit_loss: -16.4, closing_price: -118, clv: 1.7 },
    { id: 'p56', date: 'Example 3', game_id: 'demo-nym-atl', selection: 'Under 8.5 example', market: 'Total', price: -105, stake: 13.8, result: 'PUSH', profit_loss: 0, closing_price: -112, clv: 1.5 },
    { id: 'p55', date: 'Example 2', game_id: 'demo-nym-atl', selection: 'Atlanta ML example', market: 'Moneyline', price: 104, stake: 17.6, result: 'WIN', profit_loss: 18.3, closing_price: -102, clv: 1.4 },
    { id: 'p54', date: 'Example 1', game_id: 'demo-nym-atl', selection: 'Over 7.5 example', market: 'Total', price: -115, stake: 12.5, result: 'LOSS', profit_loss: -12.5, closing_price: -122, clv: 1.2 },
  ],
}

export const demoHealth: HealthData = {
  data_mode: 'demo',
  overall_score: 94,
  model_version: 'DEVELOPMENT EXAMPLE',
  latest_forecast: 'Illustrative snapshot',
  odds_quota_remaining: null,
  odds_polling_enabled: false,
  games_discovered: 1,
  missing_games: 0,
  database_status: 'ready',
  sports_data_status: 'available',
  provider_evidence_status: 'demo',
  sources: [
    { id: 'mlb', name: 'Example schedule feed', status: 'ready', last_success: 'Illustrative snapshot', last_failure: null, records: 1, detail: 'Static example source state', freshness_minutes: 0 },
    { id: 'statcast', name: 'Example Statcast feed', status: 'ready', last_success: 'Illustrative snapshot', last_failure: null, records: 24, detail: 'Static example feature state', freshness_minutes: 0 },
    { id: 'odds', name: 'Example market feed', status: 'ready', last_success: 'Illustrative snapshot', last_failure: null, records: 6, detail: 'Static example observations', freshness_minutes: 0 },
    { id: 'weather', name: 'Example weather feed', status: 'ready', last_success: 'Illustrative snapshot', last_failure: null, records: 1, detail: 'Static example venue conditions', freshness_minutes: 0 },
    { id: 'database', name: 'Example canonical database', status: 'ready', last_success: 'Illustrative snapshot', last_failure: null, records: 32, detail: 'Static example persistence state', freshness_minutes: 0 },
  ],
  recent_jobs: [
    { id: 'example-job', job: 'Example forecast generation', status: 'success', started_at: 'Illustrative snapshot', duration: 'Example duration', records: 1 },
  ],
}

export function getDemoGame(gameId?: string): GameAnalysisData {
  const selected = demoGames.find((game) => game.id === gameId)
  if (!selected) throw new Error('Illustrative game not found')
  return { ...demoGameAnalysis, game: selected }
}
