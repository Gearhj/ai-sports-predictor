import type { PositionEntryTerms } from './slate'

/**
 * Per-sport, per-season $1,000 paper accounts.
 *
 * Every money field on a season account is `number | null`, and null means the
 * account holds no capital. That is deliberate: a planned sport has nothing to
 * report, and `null` cannot be summed into a funded total by accident the way
 * a `0` can.
 */

export type AccountStatus = 'PLANNED' | 'ACTIVE' | 'CLOSED_FOR_NEW_POSITIONS' | 'ARCHIVED'
export type MembershipRole = 'FUNDED_ERA' | 'UNFUNDED_CONTROL' | 'RESEARCH_CANDIDATE'

export interface SeasonBoundary {
  opens_on: string
  closes_on: string
  /** What the window rests on — a configured accounting boundary, not a league schedule. */
  basis: string
  /** Set only when the window is a product convention (the MMA campaign). */
  convention: string | null
}

export interface EraSummary {
  experiment_id: string
  name: string
  version: number
  model_era_id: string | null
  controlling_engine: string
  decision_policy: string
  started_on: string
  role: MembershipRole
  role_basis: string
  /** Several eras carry this at once: a predecessor stays active so its open
   *  positions keep settling. It does not mean "in control". */
  is_active: boolean
  /** Exactly one era per account. This is the one controlling decisions now. */
  is_controlling: boolean
  settled_net_pl: number
  settled_stake: number
  reserved_stake: number
  open_positions: number
  settled_positions: number
}

export interface AccountPosition {
  id: string
  placed_at: string
  stake: number
  decimal_odds: number
  /** Entry terms from the position's own snapshot and decision. */
  entry?: PositionEntryTerms | null
  status: string
  result: string
  era: string | null
  profit_loss: number | null
  settled_at: string | null
}

export interface EquityPoint {
  date: string
  bankroll: number
  settlements: number
}

export interface SeasonAccount {
  sport: string
  sport_label: string
  season_id: string
  season_label: string
  status: AccountStatus
  boundary: SeasonBoundary

  original_contribution: number | null
  realized_bankroll: number | null
  reserved_stake: number | null
  available_capital: number | null
  settled_net_pl: number | null
  settled_stake: number | null
  season_return_pct: number | null
  /** Null when nothing has settled — unavailable, not zero. */
  settled_stake_roi_pct: number | null

  tracking_started_on: string | null
  partial_season: boolean
  funded_at: string | null

  planned_contribution: number
  status_note: string | null

  record: string | null
  wins: number | null
  losses: number | null
  pushes: number | null
  open_positions: number | null
  settled_positions: number | null

  controlling_engine: string | null
  controlling_era_id: string | null

  eras: EraSummary[]
  equity_curve: EquityPoint[]
  positions: AccountPosition[]
}

export interface ProgramSummary {
  funded_accounts: number
  planned_accounts: number
  funded_contribution: number
  funded_realized_bankroll: number
  funded_settled_net_pl: number
  funded_reserved_stake: number
  funded_available_capital: number
  /** Proposed paper capital for sports that have not started. Never funded. */
  planned_allocation: number
  funded_account_keys: string[]
  planned_account_keys: string[]
  note: string
}

export interface ExperimentAccountsData {
  data_mode: 'live' | 'empty'
  as_of: string
  program_contribution: number
  summary: ProgramSummary
  accounts: SeasonAccount[]
}
