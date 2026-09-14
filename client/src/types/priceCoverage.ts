/**
 * Why each game does or does not have a stored price.
 *
 * Ten blanks that render identically teach a reader the market is missing.
 * These reasons separate "no market was offered" -- which only provider
 * evidence can establish -- from "AISP never asked", which is the far more
 * common case and a completely different problem.
 */
export type PriceReason =
  | 'PRICED'
  | 'COLLECTION_WINDOW_NOT_OPEN'
  | 'DAILY_PROVIDER_ALLOCATION_EXHAUSTED'
  | 'EVENT_NOT_SELECTED_UNDER_TODAYS_ALLOCATION'
  | 'PROVIDER_RETURNED_NO_FULL_GAME_MARKET'
  | 'INCOMPLETE_TWO_SIDED_QUOTE'
  | 'IDENTITY_MAPPING_FAILED'
  | 'QUOTE_EXPIRED'
  | 'INTAKE_FAILURE'

export type ForecastState =
  | 'FORECAST_PRESENT'
  | 'FORECAST_NOT_YET_PUBLISHED'
  | 'FORECAST_AWAITING_IMPORT'
  | 'FORECAST_CUTOFF_PASSED_UNSUPPORTED'

export interface PriceCoverageGame {
  forecast_state: ForecastState
  market_pairs: { moneyline: number; total: number; run_line: number }
  game_id: string
  teams: { away: string | null; home: string | null }
  starts_at: string
  game_status: string
  pairs: Array<{
    sportsbook: string
    source: string
    away_american: number
    home_american: number
    pair_observed_at: string
  }>
  fresh_pairs: number
  stored_snapshots: number
  reason: PriceReason
  explanation: string
  window_opens_at: string
}

export interface PriceCoverage {
  schema_version: 'AISP_PRICE_COVERAGE_V1'
  window: { date: string; timezone: string; description: string }
  generated_at: string
  lane: {
    daily_allowance: number
    spent_today: number
    room: number
    failures_today: number
    objects_misattributed_today: number
    misattributed_requests_today: number
  }
  summary: {
    games: number
    with_any_stored_price: number
    with_decision_fresh_pair: number
    scheduled_for_collection: number
    excluded_by_capacity: number
    /** Reported beside prices, never merged with them. */
    with_controlling_forecast: number
    without_prices: number
    next_collection_at: string | null
    headline: string
  }
  games: PriceCoverageGame[]
}
