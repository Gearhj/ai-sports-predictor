/**
 * MLB totals and Parlay Watch payloads.
 *
 * Probability fields all end in `_pct` and every payload carries
 * `probability_units`. That redundancy is deliberate: the one bug this family of
 * numbers keeps producing is a percent and a fraction meeting at a boundary, and
 * a name that states the scale is cheaper than remembering which side you are on.
 *
 * Absent evidence is `null`, never `0`. A line that was never quoted and a line
 * quoted at zero are different facts, and the UI has to be able to tell them apart.
 */

import type { WhyExplanation } from './explain'

export type LineSource = 'RECORDED_SPORTSBOOK_LINE' | 'USER_SELECTED_SCENARIO'

export interface TotalsBookQuote {
  sportsbook: string
  decimal_odds: number
  american_odds: number | null
  observed_at: string
  quote_age_minutes: number | null
  stale: boolean
  odds_snapshot_id: string
  market_outcome_id: string
}

/** The offered price for one side, plus what the model makes of it. */
export interface TotalsPrice extends TotalsBookQuote {
  /** Model-implied only. Never an offer and never a projection of profit. */
  model_implied_net_return_per_unit: number
  estimate_basis: string
}

export interface TotalsSelection {
  side: 'OVER' | 'UNDER'
  line: number
  line_source: LineSource
  win_probability_pct: number
  push_probability_pct: number
  loss_probability_pct: number
  probability_units: 'PERCENT'
  /** False on a half-run line, where the game cannot land on the number. */
  push_possible: boolean
  model_fair_decimal_odds: number | null
  model_fair_american_odds: number | null
  price: TotalsPrice | null
  price_unavailable_reason: string | null
  /** Built from THIS record, so the probability and the price can never come
   *  from different lines. Same shape as a wager explanation's `why`. */
  explanation: WhyExplanation
}

export interface TotalsMarket {
  line: number | null
  line_source: LineSource | null
  line_note: string | null
  books: Partial<Record<'OVER' | 'UNDER', TotalsBookQuote[]>>
  observed_at: string | null
  quote_age_minutes: number | null
  other_lines: number[]
  unavailable_reason: string | null
}

/**
 * Whether a published totals index verified this exact forecast's stored PMF.
 *
 * Advisory, never a gate. A forecast published after an index's inventory
 * freeze is priced from the same stored field; it simply postdates the snapshot,
 * and the board says so rather than withholding it.
 */
export interface TotalsVerification {
  status: 'VERIFIED_AGAINST_PUBLISHED_INDEX' | 'NOT_COVERED_BY_A_PUBLISHED_INDEX'
  detail: string
  index_sha256: string | null
  index_schema_version?: string
  attached_at_utc: string | null
  derivative_published_at_utc?: string | null
  original_simulation_count?: number
  extra_innings_included?: boolean
  tail_truncated?: boolean
  score_limited?: boolean
  mlbam_game_pk?: string
  source_field: string
}

export interface TotalsForecast {
  engine_id: string | null
  engine_version: string | null
  model_label: string | null
  forecast_id: string | null
  availability: string
  availability_detail: string
  decision_eligible: boolean
  distribution_available: boolean
  expected_total_runs: number | null
  simulation_count: number | null
  support: [number, number] | null
  unavailable_reason: string | null
  totals_verification: TotalsVerification
}

export interface TotalsRow {
  game_id: string
  matchup: string
  away_team: { id: string | null; abbreviation: string; name: string }
  home_team: { id: string | null; abbreviation: string; name: string }
  starts_at: string | null
  status: string | null
  forecast: TotalsForecast
  market: TotalsMarket
  selections: TotalsSelection[]
  hidden_by_filter?: string[]
}

export interface TotalsBoard {
  schema_version: 'AISP_MLB_TOTALS_BOARD_V1'
  event_date: string
  generated_at: string
  probability_units: 'PERCENT'
  overs_only: boolean
  /** Present only while the filter is on; says what it does and does not do. */
  filter_note: string | null
  rows: TotalsRow[]
  coverage: {
    games: number
    games_with_v18_distribution: number
    games_index_verified: number
    games_with_recorded_line: number
    games_priceable: number
    selections_with_usable_price: number
  }
  /** When MLB totals were last captured at all, and from where. */
  market_availability: {
    latest_total_quote_at: string | null
    latest_total_quote_source: string | null
    age_hours: number | null
    detail: string
  }
}

// -- Parlay Watch -----------------------------------------------------------

export type PriceProvenance =
  | 'RECORDED_SPORTSBOOK_COMBINED_QUOTE'
  | 'ESTIMATED_FROM_SAME_BOOK_LEG_ODDS'

export interface ParlayLeg {
  game_id: string
  market: 'MONEYLINE' | 'TOTAL'
  selection: string
  line: number | null
  win_probability_pct: number
  push_probability_pct: number
  loss_probability_pct: number
  probability_units: 'PERCENT'
  decimal_odds: number
  american_odds: number | null
  sportsbook: string
  forecast_id: string
  engine_id: string
  quote_observed_at: string | null
  starts_at: string | null
  odds_snapshot_id: string | null
  market_outcome_id: string | null
  push_possible: boolean
}

export interface ParlayTicketPrice {
  leg_count: number
  /** Every leg wins outright. */
  all_legs_win_probability_pct: number
  /** No leg loses and at least one wins: the ticket returns more than stake. */
  ticket_pays_probability_pct: number
  /** Every leg pushes: the stake comes back. */
  stake_returned_probability_pct: number
  ticket_loses_probability_pct: number
  probability_units: 'PERCENT'
  combined_decimal_odds: number
  price_provenance: PriceProvenance
  model_fair_decimal_odds: number | null
  estimated_net_return_per_unit: number
  payout_multiple_if_all_win: number
  push_possible: boolean
  independence_note: string
  probability_definitions: Record<string, string>
}

export interface ParlayTicketCandidate {
  ticket_key: string
  sportsbook: string
  legs: ParlayLeg[]
  price: ParlayTicketPrice
  earliest_cutoff_utc: string | null
  games: string[]
  feature_labels?: ('HIGHEST_ESTIMATED_CHANCE' | 'BEST_ESTIMATED_VALUE')[]
  feature_note?: string
}

export interface ParlayWatch {
  schema_version: 'AISP_MLB_PARLAY_WATCH_V1'
  event_date: string
  generated_at: string
  leg_count: 2 | 3
  market_filter: 'ALL' | 'OVERS_ONLY' | 'TOTALS_ONLY' | 'MIXED'
  probability_units: 'PERCENT'
  independence_note: string
  estimated_price_note: string
  pregame_cutoff_minutes: number
  legs_available: number
  index_verified_forecasts: string[]
  legs_from_index_verified_forecasts: number
  candidate_count: number
  tickets: ParlayTicketCandidate[]
  truncated: boolean
  featured: ParlayTicketCandidate[]
  excluded_games: { game_id: string; matchup: string; reason: string; detail: string }[]
  leg_inventory: {
    by_sportsbook: Record<string, number>
    by_market: Record<string, number>
    games: number
  }
}

export interface RecordedParlayTicket {
  id: string
  ticket_sha256: string
  sportsbook: string
  leg_count: number
  stake: number
  funding: 'FUNDED_SEASON_ACCOUNT' | 'RESEARCH_UNFUNDED'
  combined_decimal_odds: number
  price_provenance: PriceProvenance
  price_is_an_offer: boolean
  potential_payout: number
  probability_snapshot: ParlayTicketPrice
  settlement_rules: Record<string, unknown>
  recorded_at_utc: string
  earliest_cutoff_utc: string
  status: string
  result: string
  settled_at: string | null
  payout: number | null
  profit_loss: number | null
  settled_decimal_odds: number | null
  settlement_note: string | null
  legs: (Omit<ParlayLeg, 'push_possible'> & {
    ordinal: number
    result: string
    settled_note: string | null
  })[]
}

export interface ParlayTicketBoard {
  schema_version: 'AISP_MLB_PARLAY_TICKETS_V1'
  tickets: RecordedParlayTicket[]
  performance: {
    by_price_provenance: Record<string, ParlayBucket>
    by_funding: Record<string, ParlayBucket>
    separation_note: string
  }
  shared_legs: {
    game_id: string
    selection: string
    market: string
    ticket_count: number
    total_stake_exposed: number
    ticket_ids: string[]
  }[]
  account_reconciliation: {
    account_id: string | null
    account_present: boolean
    funded_tickets: number
    parlay_reserved_stake: number
    parlay_realized_profit_loss: number
    unfunded_research_tickets: number
    note: string
  }
}

export interface ParlayBucket {
  tickets: number
  open: number
  settled: number
  wins: number
  losses: number
  pushes: number
  settled_stake: number
  net_profit_loss: number
  /** null until something has settled — never 0, which would read break-even. */
  return_on_settled_stake_pct: number | null
}
