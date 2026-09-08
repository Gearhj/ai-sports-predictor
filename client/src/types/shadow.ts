/**
 * Research-shadow comparison contract (AISP_MLB_V18_SHADOW_COMPARISON_V1).
 *
 * The shadow track is deliberately a separate type from the official forecast:
 * nothing in the app can accidentally pass a shadow probability where an
 * official one is expected.
 */
export interface OfficialForecastView {
  track: 'GEN_1_OFFICIAL'
  label: string
  model: string | null
  home_win_probability: number
  generated_at: string | null
  status: string
  version: number
  wagering_authority: true
  pregame: boolean
}

/** A recorded final outcome attached to a shadow forecast, never rewritten. */
export interface ShadowSettlement {
  settlementId: string
  gameId: string
  status: string
  settledAt: string
  immutable: true
  homeRuns: number | null
  awayRuns: number | null
  extraInnings: boolean | null
  homeWin: boolean | null
  outcomeRevision: number
  official: boolean
  source: string
  lineageSha256: string
}

export interface ShadowPopulation {
  simulation_count: number | null
  home_win_probability: number | null
  away_win_probability: number | null
  expected_home_runs: number | null
  expected_away_runs: number | null
  expected_total_runs: number | null
  expected_run_differential: number | null
  one_run_probability: number | null
  extra_inning_probability: number | null
  pitching_change_rate_home: number | null
  pitching_change_rate_away: number | null
  monte_carlo_standard_error: number | null
  monte_carlo_95_half_width: number | null
  total_run_distribution: Array<{ value: number; probability: number }> | null
}

export interface ShadowForecastView {
  track: 'RESEARCH_SHADOW'
  engine_id: string
  label: string
  forecast_id: string
  bundle_id: string | null
  bundle_sha256: string | null
  evaluation_contract: string | null
  home_win_probability: number
  expected_home_runs: number | null
  expected_away_runs: number | null
  generated_at: string | null
  evidence_cutoff: string | null
  pregame: boolean
  simulation_count: number | null
  master_seed: number
  monte_carlo_standard_error: number | null
  monte_carlo_95_half_width: number | null
  deterministic_replay_verified: boolean | null
  illegal_state_count: number | null
  nontermination_count: number | null
  path_digest: string | null
  sequential_path_events: number
  lineup_modes: string[] | null
  sides: Record<string, Record<string, string | null>> | null
  limitations: string[] | null
  /** Always false. A shadow forecast can never fund or justify a position. */
  wagering_authority: false
  /** V18's own frozen results across all of its simulations. Never Gen 1's. */
  population: ShadowPopulation | null
  settlement: ShadowSettlement | null
  settlement_state: 'SETTLED' | 'PENDING'
  settlement_note: string
  paired_with_official: boolean
  pairing_note: string | null
  difference_vs_official: number | null
  difference_orientation: 'V18_MINUS_GEN1'
  simulation_indistinguishable: boolean | null
}

export interface ShadowComparison {
  schema_version: 'AISP_MLB_V18_SHADOW_COMPARISON_V1'
  game_id: string
  home_team: string | null
  away_team: string | null
  scheduled_start: string | null
  event_status: string
  official: OfficialForecastView | null
  shadows: ShadowForecastView[]
  notes: string
}

export interface ShadowPathEvent {
  pa: number
  inning: number
  half: 'TOP' | 'BOTTOM'
  battingOrderPosition: number
  outcome: string
  outsBefore: number
  outsAfter: number
  baseMaskBefore: number
  baseMaskAfter: number
  runsScored: number
  homeScoreAfter: number
  awayScoreAfter: number
  pitcherState: string
  pitchingChange: boolean
}

export interface ShadowPath {
  schema_version: 'AISP_MLB_V18_SEQUENTIAL_PATH_V1'
  forecast_id: string
  game_id: string
  engine_id: string
  bundle_sha256: string | null
  path_digest: string | null
  master_seed: number
  simulation_count: number | null
  path_count: number
  paths: ShadowPathEvent[][]
}
