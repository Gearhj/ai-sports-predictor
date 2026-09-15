import type { DataMode } from './domain'
import type { PopulationPlay } from '../components/nfl/populationGeometry'
/**
 * NFL product read model (AISP_NFL_PRODUCT_READ_V1).
 *
 * Every number here is persisted, verified evidence: imported research-shadow
 * forecasts with their own joint score distributions, registered research
 * supplements, governed odds snapshots and the canonical schedule. Nothing is
 * generated in the browser; hypothetical market lines only re-query the same
 * persisted distribution.
 */

export type NflEventState = 'UPCOMING' | 'STARTED' | 'FINAL' | 'POSTPONED' | 'CANCELED' | 'UNSCHEDULED'

export interface NflTeam {
  team_id: string
  abbreviation: string
  name: string
}

export interface NflQuantiles {
  p05: number | null
  p25: number | null
  p50: number | null
  p75: number | null
  p95: number | null
}

export interface NflExplorer {
  simulation: { kind: string; count: number | null; seed: number | null }
  joint_score_distribution: Array<{ home: number; away: number; probability: number }>
  outcomes: { home: number; away: number; tie: number }
  expected: { home: number; away: number; margin_home: number; total: number }
  margin_distribution: Array<{ margin: number; probability: number }>
  total_distribution: Array<{ total: number; probability: number }>
  quantiles: { margin: NflQuantiles; total: NflQuantiles; home: NflQuantiles; away: NflQuantiles }
  most_likely_scores: Array<{ home: number; away: number; probability: number }>
  cells: number
  producer_prediction_interval?: Record<string, number> | null
  producer_most_likely_scores?: Array<{ home_score: number; away_score: number; probability: number }> | null
}

export interface NflQuote {
  market_type: string
  book: string
  book_key: string
  side: string
  outcome_key: string
  point: number | null
  american: number | null
  decimal: number | null
  observed_at: string | null
  snapshot_id: string
}

export interface NflMarketBlock {
  market_type: 'MONEYLINE' | 'SPREAD' | 'TOTAL'
  book?: string
  book_key?: string
  /** Who supplied the quote, and that provider's standing in AISP. */
  provider?: string | null
  provider_label?: string | null
  provider_classification?: 'GOVERNED' | 'RESEARCH' | 'UNCLASSIFIED'
  age_minutes?: number | null
  retrieved_at?: string | null
  /** A stored reference is never a current price. */
  role?: 'DECISION_ELIGIBLE_PRICE' | 'STORED_REFERENCE'
  role_label?: string
  market_identity?: { market_type: string; period: string; line: number | null; selections: string[] }
  line?: number | null
  observed_at?: string | null
  fresh?: boolean
  quote?: { a: NflQuote; b: NflQuote }
  quotes?: NflQuote[]
  books_with_two_sided_quote?: number
  comparison_available: boolean
  reason: string | null
  settlement_rule?: string
  settlement_note?: string
  three_way?: { home: number; away: number; tie: number }
  model?: { a: number | null; b: number | null; push: number }
  model_comparable?: { a: number | null; b: number | null }
  market_implied?: { a: number; b: number }
  market_no_vig?: { a: number; b: number }
  edge?: { a: number | null; b: number | null }
  expected_value?: { a: number | null; b: number | null }
  authority?: string
  selected?: null
}

export interface NflMarketComparison {
  available: boolean
  reason: string | null
  freshness_window_minutes?: number
  last_observed_utc?: string | null
  quotes_observed?: number
  markets: NflMarketBlock[]
}

export interface NflEngine {
  engine_id: string
  engine_version: string
  model_label: string
  forecast_id: string
  snapshot_type: string
  snapshot_version: number
  status: string
  home_win_probability: number
  away_win_probability: number
  tie_probability: number | null
  tie_note: string
  expected_home_score: number
  expected_away_score: number
  expected_margin_home: number
  expected_total_score: number
  evidence_readiness: {
    score: number
    threshold: number
    label: string
    missing_input_count: number
    explanation: string
  }
  evidence_basis: {
    model_inputs: string
    not_consumed: string[]
    note: string
    training_cutoff?: string | null
    feature_tier?: string | null
    promotion_status?: string | null
    execution_status?: string | null
  }
  timestamps: {
    source_evidence_utc: string | null
    forecast_cutoff_utc: string | null
    published_utc: string | null
    imported_at_utc: string | null
  }
  simulation: {
    simulator_id: string
    simulator_version: string
    seed: number | null
    kind: string
    count: number | null
    joint_available: boolean
  }
  uncertainty: Record<string, unknown>
  model_artifact_sha256?: string
  model_manifest_sha256?: string | null
  explorer?: NflExplorer | null
  market_comparison?: NflMarketComparison
  history?: Array<{
    forecast_id: string
    snapshot_type: string
    snapshot_version: number
    published_utc: string | null
    imported_at_utc: string | null
    home_win_probability: number
    away_win_probability: number
  }>
}

export interface NflSupplementSummary {
  supplement_sha256: string
  supplement_id: string
  kind: string
  engine_id: string | null
  forecast_id: string | null
  artifact_version: string
  simulation_kind: string
  simulation_count: number | null
  simulation_seed: number | null
  source_cutoff_utc: string | null
  published_utc: string | null
  imported_at_utc: string | null
}

export interface NflResearchCandidate extends NflSupplementSummary {
  model_label: string
  separately_calibrated_win_probabilities?: { home?: number; away?: number; tie?: number } | null
  explorer: NflExplorer
  market_comparison: NflMarketComparison
  representative_finals?: Array<{ label?: string; homeScore: number; awayScore: number; simulationIndex?: number }>
}

/** Producer V1 drive facts. Yardline100 is distance to the opponent goal. */
export interface NflRecordedDrive {
  driveNumber: number
  period: string
  periodSecondsBefore: number
  periodSecondsAfter: number
  durationSeconds: number
  possession: 'HOME' | 'AWAY'
  offenseTeam: string
  startYardline100: number
  endYardline100: number | null
  outcome: string
  homePointsAdded: number
  awayPointsAdded: number
  homeScoreAfter: number
  awayScoreAfter: number
}

export interface NflRepresentativeGame {
  label: string
  simulationIndex: number
  seed: number
  homeScore: number
  awayScore: number
  reachedOvertime: boolean
  finalTie: boolean
  drives: NflRecordedDrive[]
}

export interface NflDriveScenario {
  scenarioId: string
  paceDurationMultiplier?: number
  modelInputChange?: string | boolean
  interpretation?: string
}

export interface NflScoreSummary {
  expectedHomeScore: number
  expectedAwayScore: number
  expectedMarginHomeMinusAway: number
  expectedTotal: number
  homeWinProbability: number
  awayWinProbability: number
  tieProbability: number
}

export interface NflDrive {
  driveIndex: number
  period: number
  clockStart?: string
  clockEnd?: string
  offense: 'HOME' | 'AWAY'
  startYardLine: number
  endYardLine: number
  result: string
  homeScoreAfter: number
  awayScoreAfter: number
  plays?: Array<{ label?: string; yards?: number }>
}

export interface NflDriveSupplement extends NflSupplementSummary {
  summaries: {
    drives: NflDrive[]
    finalScore: { home: number; away: number }
    overtime?: boolean
    modelLabel?: string
  } | null
  paths?: NflRepresentativeGame[]
  scenario?: NflDriveScenario
  score_summary?: NflScoreSummary
  explorer?: NflExplorer | null
  historical_evidence_through_date?: string | null
  limitations?: string[]
  explains_m1_forecast?: boolean
}

export interface NflScenarioSupplement extends NflSupplementSummary {
  summaries: {
    inputsUsed: string[]
    baseline: Record<string, number>
    scenarios: Array<{
      scenarioId: string
      label?: string
      changedAssumptions: Array<{ input: string; from: unknown; to: unknown }>
      result: Record<string, number>
      delta?: Record<string, number>
    }>
  } | null
}

export interface NflDriveScenarioGroup {
  engine_id: string
  baseline: NflDriveSupplement
  scenarios: NflDriveSupplement[]
}

export interface NflResearchStudy extends NflSupplementSummary {
  contract_version: string
  summaries: {
    leaderboard: Array<Record<string, unknown> & { pipelineId: string; games?: number }>
    cohort: { games: number; common_cohort?: boolean; window?: string; population_note?: string }
    benchmarkTimingClass: 'HISTORICAL_UNTIMED' | 'TIMESTAMP_MATCHED' | 'NONE'
    marketBenchmark?: { available: boolean; reason?: string }
    developmentSelectedPipeline?: string
    pairedUncertaintyVersusM1?: {
      candidateMinusM1LogLoss?: number
      clusterBootstrap95Ci?: [number, number]
      games?: number
      resamples?: number
      temporalBlocks?: number
      cluster?: string
      candidateProbability?: string
      referenceProbability?: string
    }
    calibration?: Record<string, unknown>
    runId?: string
    earlySeason?: { applies: boolean; note: string }
    conclusion?: string
  } | null
  producer?: Record<string, unknown>
}

export interface NflResearchResults {
  available: boolean
  studies: NflResearchStudy[]
  requirement: string | null
}

export interface NflConsumerStatus {
  auto_import_enabled: boolean
  job_name: string
  last_success_utc: string | null
  last_success_result: Record<string, unknown> | null
  last_run_utc: string | null
  last_run_status: string | null
  last_failure_utc: string | null
  last_failure_message: string | null
  legacy_bundle_job: { job_name: string; last_success_utc: string | null }
  registered_bundles: number
  registered_supplements: number
  last_bundle_import_utc: string | null
  last_supplement_import_utc: string | null
}

export interface NflEvent {
  event_id: string
  canonical_game_id: string | null
  season: number
  week: number
  kickoff_utc: string | null
  state: NflEventState
  final_score: { home: number | null; away: number | null } | null
  home: NflTeam
  away: NflTeam
  engines: NflEngine[]
  snapshot_history_count: number
  supplements: { joint_engines: string[]; drive_engines: string[]; scenario_engines: string[] }
  lab_available: boolean
  analysis_href: string | null
  lab_href: string | null
}

export interface NflWeekOverview {
  schema_version: 'AISP_NFL_PRODUCT_READ_V1'
  sport: 'NFL'
  season: number
  week: number
  now_utc: string
  generated_at_utc: string
  authority: string
  events: NflEvent[]
  counts: { events: number; upcoming: number; started: number; final: number; with_joint_evidence: number }
  consumer: NflConsumerStatus
  data_mode?: 'live' | 'demo' | 'empty'
}

export interface NflWeeks {
  schema_version: 'AISP_NFL_PRODUCT_READ_V1'
  sport: 'NFL'
  now_utc: string
  weeks: Array<{ season: number; week: number; events: number; first_kickoff_utc: string | null; last_kickoff_utc: string | null }>
  current: { season: number; week: number } | null
  data_mode?: 'live' | 'demo' | 'empty'
}

export interface NflGameDetail {
  schema_version: 'AISP_NFL_PRODUCT_READ_V1'
  sport: 'NFL'
  now_utc: string
  authority: string
  event: {
    event_id: string
    canonical_game_id: string
    season: number
    week: number
    kickoff_utc: string | null
    state: NflEventState
    final_score: { home: number | null; away: number | null } | null
    home: NflTeam | null
    away: NflTeam | null
  }
  engines: NflEngine[]
  research_candidates: NflResearchCandidate[]
  drive_theater: { available: boolean; supplements: NflDriveSupplement[]; requirement: string | null }
  scenario_comparison: { available: boolean; supplements: Array<NflScenarioSupplement | NflDriveScenarioGroup>; requirement: string | null }
  play_scenarios?: NflPlayScenarioExplorer[]
  research_results: NflResearchResults
  data_mode?: 'live' | 'demo' | 'empty'
}


/* -- play-level simulation (AISP_NFL_PLAY_THEATER_READ_V1) ----------------
 *
 * A third, independent research engine. It does not explain M1 and does not
 * supersede the drive engine; it is the only family that carries plays, which
 * is what makes an animated theater honest rather than invented.
 */

export interface NflPlayRepresentativeIndex {
  position: number
  label: string
  simulationIndex: number
  seed: number
  homeScore: number
  awayScore: number
  regulationHomeScore: number | null
  regulationAwayScore: number | null
  reachedOvertime: boolean | null
  finalTie: boolean | null
  playCount: number
  driveCount: number | null
  turnovers: number | null
  sacks: number | null
  playTypeCounts: Record<string, number> | null
}

export interface NflPlayPopulationStats {
  draws: number
  mean_home_score: number
  mean_away_score: number
  mean_total: number
  mean_drives: number | null
  mean_plays: number | null
  plays_per_drive: number | null
  points_per_drive: number | null
  reached_overtime: number
}

export interface NflPlayScenario {
  scenario_id: string
  label: string
  explanation: string | null
  parameters: Record<string, unknown> | null
  interpretation: string | null
  turnover_sensitivity_meaning: string | null
  model_input_change: boolean | null
  score_summary: {
    homeWinProbability: number; awayWinProbability: number; tieProbability: number
    expectedHomeScore: number; expectedAwayScore: number
    expectedTotal: number; expectedMarginHomeMinusAway: number
  }
  scenario_delta: Record<string, number> | null
  quantiles: Record<string, Record<string, number>> | null
  reaching_overtime_probability: number | null
  final_tie_probability: number | null
  draw_count: number | null
  seed: number | null
  population_accounting: Record<string, unknown> | null
  representatives: NflPlayRepresentativeIndex[]
  supplement_sha256: string
  play_count: number | null
  population_drive_stats: NflPlayPopulationStats | null
}

export interface NflSeedPairedGame {
  simulation_index: number
  seed: number
  base: { label: string; position: number; home_score: number; away_score: number }
  challenger: { label: string; position: number; home_score: number; away_score: number }
}

export interface NflPlayTheaterIndex {
  schema_version: string
  event_id: string
  engine: {
    engine_id: string | null
    label: string
    version: string | null
    training_cutoff: string | null
    generating_law: Record<string, string> | null
    historical_evidence_through_date: string | null
    historical_evidence_precision: string | null
    input_treatment: Record<string, unknown> | null
    explains_m1_forecast: boolean
    related_m1_forecast_id: string | null
  }
  governance: {
    status: string | null; promotion_status: string | null
    execution_allowed_in_aisp: boolean | null; wagering_authority: boolean | null
  }
  limitations: string[]
  m1_comparison: {
    interpretation?: string
    m1ScoreSummary?: Record<string, number>
    playEngineMinusM1?: Record<string, number>
  } | null
  home_team: { teamId: string; abbreviation: string; displayName: string } | null
  away_team: { teamId: string; abbreviation: string; displayName: string } | null
  scenarios: NflPlayScenario[]
  pairing: {
    population_is_seed_paired: boolean
    population_note: string
    representatives_are_seed_paired: boolean
    representative_note: string
    seed_paired_games: Record<string, NflSeedPairedGame[]>
  }
  geometry_notice: string
}

export interface NflPlayRepresentative {
  schema_version: string
  event_id: string
  scenario_id: string
  position: number
  available: boolean
  cause?: string
  detail?: string
  supplement_sha256?: string
  label?: string
  simulation_index?: number
  seed?: number
  home_team?: string
  away_team?: string
  home_score?: number
  away_score?: number
  regulation_home_score?: number | null
  regulation_away_score?: number | null
  reached_overtime?: boolean | null
  final_tie?: boolean | null
  diagnostics?: Record<string, number | Record<string, number> | null>
  plays?: import('../components/nfl/playGeometry').Play[]
}

export interface NflPlayScenarioExplorer {
  supplement_sha256: string
  engine_id: string | null
  scenario_id: string
  label: string
  explanation: string | null
  score_summary: Record<string, number> | null
  limitations: string[]
  explains_m1_forecast: boolean
  explorer: NflExplorer
}

/**
 * One scenario's drawable play population.
 *
 * `coverage` is the load-bearing part: `simulated_games` is the whole
 * population and `games_with_play_paths` is how many of them were published
 * with paths. The two are never equal, and no view may imply they are.
 */
export interface NflPlayPopulation {
  schema_version: string
  event_id: string
  scenario_id: string
  available: boolean
  cause?: string
  detail?: string
  supplement_sha256?: string
  home_team?: { abbreviation: string; displayName?: string } | null
  away_team?: { abbreviation: string; displayName?: string } | null
  coverage: {
    simulated_games: number | null
    games_with_play_paths: number
    plays_drawable: number
    plays_in_population: number | null
    path_coverage_fraction: number | null
    statement: string | null
  }
  population_accounting: NflPopulationAccounting | null
  /**
   * Binned counts over EVERY simulated play, added by contract V2.
   * Null on an artifact still at V1: an event past kickoff cannot be
   * regenerated, so its V1 artifact is carried forward unchanged.
   */
  population_spatial: NflPopulationSpatial | null
  artifact_schema_version?: string
  population_finals?: Array<{ homeScore: number; awayScore: number }> | null
  score_summary?: Record<string, number> | null
  games: Array<{
    position: number
    label: string | null
    simulation_index: number | null
    seed: number | null
    home_score: number | null
    away_score: number | null
    reached_overtime: boolean | null
    play_count: number
  }>
  plays: PopulationPlay[]
  drawn_accounting: Record<string, unknown>
  geometry_notice: string
}

/** The producer's own aggregates over every simulated game in the population. */
export interface NflPopulationAccounting {
  plays?: number
  games?: number
  drives?: number
  completionRate?: number
  completionDenominator?: number
  dropbackDenominator?: number
  scrimmageDenominator?: number
  turnoversPerGame?: number
  pointsPerDrive?: number
  thirdDownAttempts?: number
  thirdDownConversionRate?: number
  fourthDownAttempts?: number
  fourthDownConversionRate?: number
  sackRatePerDropback?: number
  fumblesPerGame?: number
  playTypeCounts?: Record<string, number>
  playsPerGame?: { count?: number; mean?: number; standardDeviation?: number; quantiles?: Record<string, number> }
  drivesPerGame?: { count?: number; mean?: number; standardDeviation?: number; quantiles?: Record<string, number> }
  yardsPerScrimmagePlay?: { count?: number; mean?: number; standardDeviation?: number; quantiles?: Record<string, number> }
  denominatorNotes?: string
}

/** The home page's featured matchup, and the rule that chose it. */
export interface HomeFeatured {
  schema_version: string
  data_mode: DataMode
  generated_at_utc: string
  selection_criteria: { ordering: string[]; excluded: string }
  featured: HomeCandidate | null
  candidates: HomeCandidate[]
  counts: { upcoming_games: number; with_simulation_evidence: number }
  next_slate_date: string | null
}

export interface HomeCandidate {
  game_id: string
  event_id: string | null
  league_code: string
  starts_at_utc: string
  status: string
  home: { abbreviation: string; name: string; city: string | null }
  away: { abbreviation: string; name: string; city: string | null }
  matchup: string
  simulation: { kind: string | null; scenarios: string[]; explorable: boolean }
  forecast: {
    engine_id: string
    status: string
    home_win_probability: number
    away_win_probability: number
    expected_home_score: number
    expected_away_score: number
    expected_total: number
    expected_margin_home: number
    available_as_of_utc: string | null
  } | null
  research_only: boolean
  explore_href: string
}

/** The producer's binned summary of every simulated play in the population. */
export interface NflPopulationSpatial {
  schemaVersion: string
  basis: string
  games: number
  playCount: number
  reconciliation: {
    populationAccountingPlays: number
    binnedPlayCount: number
    reconciles: boolean
    excludedPlayTypes: string[]
    excludedPlayCount: number
    statement: string
  }
  fieldDirection: { convention: string; statement: string }
  bins: Record<string, unknown>
  byPlayType: Record<string, { plays: number; netYards: { count: number; mean: number | null } }>
  cells: Array<{ playType: string; start: number | string; yards: number | string; count: number }>
  cellCount: number
  limitations: string[]
}
