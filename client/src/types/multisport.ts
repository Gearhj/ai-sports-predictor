import type { DataProvenance } from './domain'

export type SportCode = 'BASEBALL' | 'FOOTBALL' | 'BASKETBALL' | 'MMA'
export type LeagueCode = 'MLB' | 'NFL' | 'NBA' | 'WNBA' | 'UFC'
export type SportAvailabilityState = 'LIVE' | 'RESEARCH_SHADOW' | 'DEVELOPMENT' | 'PLANNED' | 'UNAVAILABLE'
export type NormalizedDailyDecisionState =
  | 'AWAITING_FINAL_EVIDENCE'
  | 'PROVISIONAL_EDGE'
  | 'WATCH'
  | 'PASS'
  | 'FINAL_QUALIFIED'
  | 'FINAL_NO_BET'
  | 'NOT_EVALUATED'
export type NormalizedFundingState =
  | 'OFFICIAL_VIRTUAL_POSITION'
  | 'ELIGIBLE_NOT_FUNDED'
  | 'RESEARCH_ONLY'
  | 'MARKET_NOT_SUPPORTED'
  | 'NOT_EVALUATED'

export interface NormalizedSelection {
  sport_code: SportCode
  league_code: LeagueCode
  event_id: string
  event_label: string
  starts_at: string | null
  decision_state: NormalizedDailyDecisionState
  funding_state: NormalizedFundingState
  market_label: string | null
  selection_label: string | null
  line: number | null
  price_american: number | null
  sportsbook: string | null
  model_probability: number | null
  no_vig_probability: number | null
  adjusted_edge: number | null
  expected_value: number | null
  evidence_tier: string | null
  virtual_stake: number | null
  experiment_id: string | null
  experiment_name: string | null
  policy_version: string | null
  decision_timestamp: string | null
  final_decision_cutoff: string | null
  source_label: string | null
  model_status: string | null
  outstanding_gates: string[]
  status_note: string | null
  can_ever_fund_official_position: boolean
  sport_rank: number | null
  detail_href: string | null
}

export interface SportSection {
  sport_code: SportCode
  league_code: LeagueCode
  display_name: string
  availability_state: SportAvailabilityState
  scheduled_event_count: number
  valid_forecast_count: number
  final_qualified_count: number
  awaiting_final_evidence_count: number
  provisional_count: number
  watch_count: number
  pass_count: number
  open_position_count: number
  settled_today_count: number
  virtual_stake: number
  data_health: number | null
  latest_forecast_at: string | null
  latest_market_at: string | null
  engine_label: string | null
  can_create_official_positions: boolean
  official_positions_blocked_reason: string | null
  degraded: boolean
  degraded_reason: string | null
  selections: NormalizedSelection[]
}

export interface OverallSportSummary {
  final_qualified_position_count: number
  total_virtual_stake: number
  open_position_count: number
  settled_today_count: number
  sports_with_official_positions: string[]
  sports_with_events: string[]
  latest_complete_refresh: string | null
  stale_or_degraded_sport_count: number
}

export interface MultiSportDataFreshness {
  latest_forecast_at: string | null
  latest_market_at: string | null
  scheduler_running: boolean
  provider_calls_made: number
}

export interface SportAvailabilityEntry {
  sport_code: SportCode
  league_code: LeagueCode
  display_name: string
  availability: SportAvailabilityState
  navigable: boolean
  note: string | null
}

export interface TodayAllSportsReadModel extends DataProvenance {
  data_mode: 'live'
  schema_version: 'TODAY_ALL_SPORTS_READ_MODEL_V1'
  registry_version: 'SPORT_REGISTRY_V1'
  operational_date: string
  display_timezone: string
  generated_at: string
  overall_summary: OverallSportSummary
  qualified_positions: NormalizedSelection[]
  awaiting_final_evidence: NormalizedSelection[]
  provisional_opportunities: NormalizedSelection[]
  sport_sections: SportSection[]
  data_freshness: MultiSportDataFreshness
  availability: SportAvailabilityEntry[]
  cross_sport_ranking: 'NOT_APPLICABLE_PHASE_1'
  notes: Record<string, unknown>
}

export interface SportRegistryResponse {
  version: 'SPORT_REGISTRY_V1'
  sports: Array<{
    sport_code: SportCode
    league_code: LeagueCode
    participant_type: 'TEAM' | 'INDIVIDUAL'
    availability: SportAvailabilityState
    display_name: string
    short_label: string
    official_positions_supported: boolean
    official_positions_note: string
    engine_label: string | null
    stored_sport_aliases: string[]
    stored_league_aliases: string[]
    navigable: boolean
  }>
  supported_filters: Array<'ALL' | 'MLB' | 'NFL'>
}
