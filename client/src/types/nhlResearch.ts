/** NHL Gen1 research read model (AISP_NHL_RESEARCH_BOARD_V1 / _GAME_V1). Research only. */

export type NhlGoalieStatus = 'UNKNOWN' | 'PROJECTED' | 'CONFIRMED'
export type NhlRecordClass = 'FORECAST' | 'PREVIEW_FIXTURE' | 'SYNTHETIC_FIXTURE'

export interface NhlGoalie {
  status: NhlGoalieStatus
  goalieId: string | null
  goalieName: string | null
  statusSource: string | null
  statusObservedAtUtc: string | null
}

export interface NhlForecast {
  forecast_id: string
  model_id: string
  model_version: string
  research_status: 'RESEARCH_ONLY'
  record_class: NhlRecordClass
  provenance: {
    evidence_mode: string
    goalie_feature_mode: 'TEAM_LEVEL' | 'GOALIE_SPECIFIC' | string
    producer_evidence_status: string
    producer_evidence_ref: { path: string; sha256: string; bytes: number }
  }
  regulation_modelled: boolean
  probabilities: {
    regulation: { home: number; tie: number; away: number } | null
    final: { home: number; away: number }
    overtime: number | null
    shootout: number | null
  }
  goals: {
    regulation: { home: number; away: number; horizon: string } | null
    final: { home: number; away: number; horizon: string } | null
    units: 'goals'
  }
  goalies: { home: NhlGoalie; away: NhlGoalie }
  missing_inputs: string[]
  limitations: string[]
  clocks: {
    source_cutoff_utc: string | null
    source_retrieved_utc: string | null
    created_utc: string | null
    published_utc: string | null
    publication_clock_source: string
    available_as_of: string | null
    imported_at: string | null
  }
  age_seconds: number
  published_before_start: boolean
  supersedes_forecast_id: string | null
  object_sha256: string
  is_current?: boolean
}

export interface NhlResult {
  result_sha256: string
  regulation: { home_goals: number; away_goals: number; result: 'HOME' | 'AWAY' | 'TIE'; source: string; observed_utc: string | null; retrieved_utc: string | null }
  decided_in: 'REGULATION' | 'OVERTIME' | 'SHOOTOUT'
  overtime: { home_goals: number; away_goals: number } | null
  shootout: { home_scored: number; away_scored: number } | null
  final: { home_score: number; away_score: number; winner: 'HOME' | 'AWAY'; official: boolean; source: string; observed_utc: string | null; retrieved_utc: string | null; completed_utc: string | null }
  status_label: 'Final' | 'Final OT' | 'Final SO'
  supersedes_result_sha256: string | null
}

export interface NhlTeamSide { name: string; abbreviation: string; score: number | null; canonical_team_id: string | null }

export interface NhlNotice {
  status?: string; enabled?: boolean; official_positions?: number; statement: string
  /** Since the NHL paper era opened (2026-10-16): VIRTUAL_PAPER, its policy and position counts. */
  mode?: string; policy?: string; open_positions?: number; settled_positions?: number; quotes_stored?: number
}

/** The recorded NHL checkpoint decision for one game and its position, exactly as stored. */
export interface NhlPaper {
  decision: {
    decision_id: string; recommendation: string; reason: string | null; detail: string | null; recorded_at: string | null
    selection: string | null; sportsbook: string | null; american_odds: number | null
    model_probability: number | null; no_vig_probability: number | null; edge: number | null; expected_return: number | null
  }
  position: { position_id: string; stake: number | null; status: string; result: string | null; profit_loss: number | null } | null
}

export interface NhlGameRow {
  canonical_game_id: string
  source_game_id: string
  aisp_game_id: string | null
  game_bound: boolean
  source_mode: string | null
  is_fixture: boolean
  record_class: NhlRecordClass | null
  models: string[]
  season: string | null
  game_type: 'PRESEASON' | 'REGULAR' | 'PLAYOFF' | null
  start_utc: string | null
  et_date: string | null
  status: string
  status_label: string
  home: NhlTeamSide | null
  away: NhlTeamSide | null
  score: { home: number | null; away: number | null; units: 'goals' } | null
  forecast: NhlForecast | null
  forecast_state: 'FORECAST_AVAILABLE' | 'FORECAST_PENDING'
  forecast_label: 'Research forecast — wagering disabled' | 'Paper forecast — decided 30–15 min before puck drop' | 'Forecast pending'
  revision_count: number
  result: NhlResult | null
  markets: NhlNotice
  wagering: NhlNotice
  /** The game's recorded paper decision and position (absent on older payloads, null until one is recorded). */
  paper?: NhlPaper | null
}

export interface NhlBoard {
  schema_version: 'AISP_NHL_RESEARCH_BOARD_V1'
  league: 'NHL'
  generated_at_utc: string
  date: string | null
  dates: string[]
  games: NhlGameRow[]
  counts: { games: number; with_forecast: number; finals: number }
  markets: NhlNotice
  wagering: NhlNotice
  outcome_definitions: Record<string, string>
  fixture_notice: string | null
  include_test_data: boolean
  test_data_rows: number
  /** False in production: the dev-only NHL_TEST_DATA_ENABLED flag is off, so no toggle is offered. */
  test_data_available?: boolean
}

export interface NhlGameDetail extends NhlGameRow {
  schema_version: 'AISP_NHL_RESEARCH_GAME_V1'
  history: NhlForecast[]
  results_history: NhlResult[]
  import_receipts: { disposition: string; kind: string; identity: string | null; problems: string[]; received_at: string | null; source: string | null }[]
  outcome_definitions: Record<string, string>
}

export interface NhlOverviewPopulation {
  scored: number
  regulation_3way: { n: number; not_modelled: number; outcome: string; brier_three_way: number | null; log_loss_three_way: number | null; accuracy: number | null; ties_observed: number; brier_range: string }
  final_2way: { n: number; outcome: string; brier_two_way: number | null; log_loss_two_way: number | null; accuracy: number | null; decided_in: Record<string, number>; brier_range: string }
}

export interface NhlOverviewBlock extends NhlOverviewPopulation {
  league: 'NHL'
  forecast_games: number
  finals: number
  preview_fixture: NhlOverviewPopulation
  synthetic_fixture_rows: number
  include_test_data?: boolean
  test_data_rows?: number
  markets: NhlNotice
  wagering: NhlNotice
  statement: string
}
