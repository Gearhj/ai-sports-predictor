/**
 * NBA Gen1 read models (`/nba/gen1/*`). Three channels, never blended:
 *
 *   RIDGE_POINTS        expected POINTS. Never a probability; never compared with a price.
 *   ELO_WINNER          winner probabilities, fractions 0–1.
 *   JOINT_DISTRIBUTION  winner / spread / total probabilities from 10,000 paired draws.
 *
 * Probabilities arrive as fractions; the page multiplies by 100 exactly once.
 */

export type NbaChannelKey = 'RIDGE_POINTS' | 'ELO_WINNER' | 'JOINT_DISTRIBUTION'
export const NBA_CHANNEL_ORDER: NbaChannelKey[] = ['RIDGE_POINTS', 'ELO_WINNER', 'JOINT_DISTRIBUTION']
export const NBA_HISTORICAL = 'A_HISTORICAL_FINALIZED_RECORD_RECONSTRUCTION'

export interface NbaInterval { level: number; lower: number; upper: number; method?: string; units?: string }

export interface NbaRidgeChannel {
  label: string; units: string; is_probability: false
  home: number | null; away: number | null; total: number | null; home_margin: number | null; note: string
}
export interface NbaEloChannel {
  label: string; units: string; is_probability: true
  home: number | null; away: number | null; tie: number | null; method: string | null; note: string
}
export interface NbaJointChannel {
  label: string; units: string; is_probability: true; draw_count: number | null
  home: number | null; away: number | null; home_mcse: number | null
  expected: { home: number | null; away: number | null; total: number | null; margin: number | null }
  intervals: Record<string, NbaInterval[]> | null
  illustrative_totals: {
    line: number; lineClassification: string; drawCount: number
    over: { win: number; push: number; loss: number }; under: { win: number; push: number; loss: number }
  }[]
  note: string
}
export interface NbaChannels {
  RIDGE_POINTS: NbaRidgeChannel
  ELO_WINNER: NbaEloChannel
  JOINT_DISTRIBUTION: NbaJointChannel
}

export interface NbaEligibility {
  now: { state: string; manifest_sha256?: string; manifest_schema?: string; lease_valid_until?: string | null } | null
  checkpoint: { decision_id: string; recorded_at: string | null; recommendation: string; reason: string | null; detail: string | null } | null
}

export interface NbaBoardGame {
  game_id: string | null
  codex_game_id: string
  espn_game_id: string
  matchup: string
  tipoff_utc: string | null
  game_date: string
  forecast_id: string
  label: string
  evidence_class: string
  revisions: number
  channels: NbaChannels
  eligibility: NbaEligibility | null
}

export interface NbaGen1Board {
  schema_version: 'AISP_NBA_GEN1_BOARD_V1'
  generated_at_utc: string
  handoff: { status: string; reason: string; production_label_allowed: boolean; handoff_sha256: string | null }
  production: NbaBoardGame[]
  historical: { rows: number; games_scored: number; label: string }
  prospective_games_scored: number
  limitations: string[]
  note: string
}

export interface NbaChannelScore {
  evidence_class: string
  channel: NbaChannelKey
  channel_label: string
  forecast_identity: string
  result_source: string
  final: { home: number; away: number; overtime: boolean | null }
  home_win_probability: number | null
  probability_of_actual: number | null
  log_loss: number | null
  two_way_brier: number | null
  winner_correct: boolean | null
  predicted: { home: number | null; away: number | null }
  home_abs_error: number | null
  away_abs_error: number | null
  margin_error: number | null
  total_error: number | null
  crps: { home: number | null; away: number | null; margin: number | null; total: number | null } | null
  interval_coverage: Record<string, Record<string, { lower: number; upper: number; contains: boolean; width: number }>> | null
}

export interface NbaProductionForecast {
  forecast_id: string
  forecast_sha256: string
  package_index_sha256: string
  envelope_schema: string
  label: string
  evidence_class: string
  handoff_sha256: string
  population_logical_sha256: string
  captured_tipoff_utc: string | null
  clocks: Record<string, string | null>
  orientation: string | null
  limitations: string[] | null
  channels: NbaChannels
  eligibility: NbaEligibility
}

export interface NbaHistoricalForecast {
  codex_game_id: string
  evidence_class: string
  label: string
  assessment_receipt_sha256: string
  joint_receipt_sha256: string | null
  joint_counts_int64_le_sha256: string | null
  point_model: { id: string; sha256: string }
  elo_model_sha256: string
  game_date: string
  qualification: string | null
  channels: NbaChannels
}

export interface NbaGen1Game {
  schema_version: 'AISP_NBA_GEN1_GAME_V1'
  game_id: string
  espn_game_id: string | null
  codex_game_id: string | null
  teams: { home?: { abbreviation: string; name: string }; away?: { abbreviation: string; name: string }; label?: string }
  tipoff_utc: string | null
  status: string
  final: { home: number; away: number } | null
  production_forecasts: NbaProductionForecast[]
  historical_forecasts: NbaHistoricalForecast[]
  finals: { source: string; home: number; away: number; periods: number | null; overtime: boolean | null;
    source_object_sha256: string; captured_at_utc: string | null; completion_clock_status: string; status: string }[]
  evaluations: NbaChannelScore[]
  price: { reason: string; detail: string; lane?: string; lane_enabled?: boolean }
  limitations: string[]
  authority: { research_only: boolean; wagering_authority: boolean; note: string }
}
