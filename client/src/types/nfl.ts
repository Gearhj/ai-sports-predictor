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
  paths?: unknown[]
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
  scenario_comparison: { available: boolean; supplements: NflScenarioSupplement[]; requirement: string | null }
  research_results: NflResearchResults
  data_mode?: 'live' | 'demo' | 'empty'
}
