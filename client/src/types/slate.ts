/**
 * Every scheduled game on one slate, with the reason each has no position.
 *
 * `state` is the field that matters: five of its six values are NOT a rejected
 * wager, and the UI has to keep them apart. A missing quote is something AISP
 * has not captured — never a sportsbook withholding a line.
 */

export type SlateState =
  | 'POSITION_RECORDED'
  | 'NO_QUALIFYING_VALUE'
  | 'PRICE_UNAVAILABLE'
  | 'FORECAST_PENDING'
  | 'NOT_YET_EVALUATED'
  | 'POLICY_INACTIVE'

/**
 * The terms a position was actually taken at, from the position itself.
 * Never the latest quote for the game -- that is `SlateGame.quote`.
 */
export interface PositionEntryTerms {
  position_id: string
  decision_id: string | null
  side: string | null
  market: string | null
  american_odds: number | null
  american_label: string | null
  decimal_odds: number
  sportsbook_key: string | null
  sportsbook_name: string | null
  provider: { source: string | null; name: string | null; classification: string | null; label: string | null }
  entry_quote_observed_at: string | null
  stake: number
  placed_at: string
  recorded_at: string | null
  status: string
  result: string
  tie_settlement: string | null
  tie_settlement_label: string | null
  headline: string | null
}

export interface SlateGame {
  game_id: string
  league_code: string
  teams: { home?: string; away?: string }
  starts_at: string
  game_status: string
  forecast: {
    forecast_id: string
    engine_id: string
    created_at: string | null
    available_as_of: string | null
    is_ready: boolean
    status: string | null
  } | null
  quote: {
    observed_at: string | null
    age_minutes: number
    sportsbook: string
    american_odds: number | null
    source: string
    /** The team this latest price is for. */
    side?: string | null
    role?: 'LATEST_MARKET_PRICE'
    /** False means it exists but is too old to qualify — not that it is absent. */
    satisfies_policy_freshness: boolean
    freshness_limit_minutes: number
  } | null
  policy: { active_for_sport: boolean; note: string | null }
  latest_evaluation: {
    recommendation: string
    reason: string | null
    phase: string | null
    recommended_at: string | null
  } | null
  next_checkpoint: {
    name: string
    at_utc: string
    collection_window_opens_utc: string
    minutes_away: number
    window_open: boolean
  } | null
  positions: number
  recorded_positions?: PositionEntryTerms[]
  state: SlateState
  explanation: string
}

export interface SlateStatus {
  schema_version: 'AISP_SLATE_STATUS_V1'
  generated_at: string
  window: {
    local_date: string
    timezone: string
    start_utc: string
    end_utc: string
    description: string
  }
  games: SlateGame[]
  counts: Record<string, number>
  by_league: Record<string, Record<string, number>>
  legend: Record<string, string>
}
