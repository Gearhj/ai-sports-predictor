/**
 * The four-sport command center's data contracts and loaders.
 *
 * Every figure the redesigned pages chart comes from one of three read models
 * (`/command-center/ledger`, `/command-center/decisions`,
 * `/command-center/markets`) plus `/sports/readiness`. All are read-only and
 * built from persisted rows; none makes a provider call. A sportsbook quote is
 * only ever a persisted observation, and model output is never placed in a
 * price field.
 */
import type { MarketStatus, ModelStatus } from './pregameStatus'
import type { EdgeMetrics } from './decisionEdges'
import type { CheckpointSemantics, QuoteClocks } from './cfbMarket'
import { useCallback } from 'react'
import { useApiResource } from '../hooks/useApiResource'
import { apiRequest } from './api'
import { STALE_TIME, cacheKey } from './queryCache'

/**
 * A read model that is not the one asked for (an error page, an older server,
 * a proxy's JSON) is refused as a failed read, so a region shows "unable to
 * load" rather than rendering fields that do not exist.
 */
export function expectSchema<T extends { schema_version?: string }>(value: T, schema: string): T {
  if (!value || typeof value !== 'object' || value.schema_version !== schema) {
    throw new Error(`Unexpected response (expected ${schema})`)
  }
  return value
}

export type SportCode = 'MLB' | 'NFL' | 'NBA' | 'CFB'
export const SPORT_CODES: SportCode[] = ['MLB', 'NFL', 'NBA', 'CFB']
export const SPORT_NAMES: Record<SportCode, string> = {
  MLB: 'MLB', NFL: 'NFL', NBA: 'NBA', CFB: 'College Football',
}
/** Chart colours: CSS variables so both themes stay legible. */
export const SPORT_COLORS: Record<SportCode, string> = {
  MLB: 'var(--cc-mlb)', NFL: 'var(--cc-nfl)', NBA: 'var(--cc-nba)', CFB: 'var(--cc-cfb)',
}

export const asSport = (value: string | null | undefined): SportCode | null => {
  const upper = (value ?? '').toUpperCase()
  if (upper === 'BASEBALL_MLB') return 'MLB'
  return (SPORT_CODES as string[]).includes(upper) ? upper as SportCode : null
}

// ------------------------------------------------------------------ ledger --

export interface EntryTerms {
  american: number | null
  decimal: number | null
  sportsbook: string | null
  sportsbook_key: string | null
  provider: string | null
  classification: 'GOVERNED' | 'RESEARCH' | 'UNCLASSIFIED'
  quote_observed_at: string | null
}

export interface LedgerPosition {
  position_id: string
  game_id: string
  matchup: string | null
  starts_at: string | null
  market: string
  selection: string | null
  entry: EntryTerms
  stake: number | null
  placed_at: string | null
  status: string
  result: string | null
  profit_loss: number | null
  settled_at: string | null
  experiment: { id: string; name: string }
}

export interface BankrollPoint {
  at: string | null
  bankroll: number
  kind: 'FUNDING' | 'SETTLEMENT'
  position_id: string | null
  change?: number | null
  sport?: SportCode
}

export interface LedgerSport {
  sport: SportCode
  label: string
  account: { funded: boolean; label: string; effective_on?: string; season_id?: string; realized_bankroll?: number }
  starting_capital: number
  realized_bankroll: number
  profit_loss: number
  open_exposure: number
  available_capital: number
  open_count: number
  settled_count: number
  record: { WIN: number; LOSS: number; PUSH: number }
  positions: LedgerPosition[]
  series: BankrollPoint[]
  reconciled: boolean | null
}

export interface Ledger {
  schema_version: string
  generated_at: string
  authority: string
  totals: Omit<LedgerSport, 'sport' | 'label' | 'account' | 'positions' | 'series' | 'reconciled'>
  combined_series: BankrollPoint[]
  sports: LedgerSport[]
}

export const ledgerKey = () => cacheKey('command-ledger')
export const loadLedger = async (signal: AbortSignal) =>
  expectSchema(await apiRequest<Ledger>('/command-center/ledger', {}, signal), 'AISP_COMMAND_CENTER_LEDGER_V1')

export function useLedger() {
  return useApiResource<Ledger>(loadLedger, { key: ledgerKey(), staleTimeMs: STALE_TIME.operational })
}

// --------------------------------------------------------------- decisions --

export type DecisionGroup = 'READY' | 'WATCHING' | 'POSITION' | 'PASSED' | 'CLOSED'

export interface DecisionItem {
  decision_id: string
  sport: SportCode
  experiment: string
  game_id: string
  matchup: string | null
  starts_at: string | null
  phase: string
  market: string
  selection: string | null
  line: number | null
  sportsbook: string | null
  american: number | null
  quote_observed_at: string | null
  quote_age_minutes: number | null
  model_probability: number | null
  market_probability: number | null
  edge: number | null
  expected_value: number | null
  recommendation: string
  stored_recommendation: string
  reason: string | null
  recommended_at: string | null
  decision_window: { opens_at: string; closes_at: string } | null
  group: DecisionGroup
  position: {
    position_id: string
    stake: number | null
    status: string
    result: string | null
    profit_loss: number | null
    /** Plain wording for the board, e.g. "Final — settlement pending". */
    display_status?: string
    /** The game is over and the settlement owner has not reached it yet. */
    awaiting_settlement?: boolean
  } | null
  /** Re-derived from the stored snapshot: the same-book, same-provider pair the decision priced. */
  market_integrity?: MarketIntegrity | null
  /** The forecast the decision used, and whether it was the controlling one at the time. */
  forecast_check?: ForecastCheck | null
  /** The policy's own adjusted edge and expected value, when it recorded them. */
  adjusted?: { edge: number | null; expected_value: number | null; probability: number | null } | null
  /**
   * Every edge figure the decision recorded, each named for what it is, and the
   * policy's own tests. `edge` above is the model minus the offered price's
   * break-even probability; it is NOT what the MLB/CFB minimum is applied to.
   */
  edge_metrics?: EdgeMetrics | null
  /** Human-readable qualification reasons only (machine codes removed server-side). */
  plain_reasons?: string[]
  /** College Football: NO_BET (priced, edge below policy) vs NO_PRICE (declined) vs BLOCKED (not evaluated). */
  checkpoint_semantics?: CheckpointSemantics
}

export interface MarketIntegrity {
  two_sided_same_book: boolean
  market_matches: boolean
  provider: string | null
  provider_permitted: boolean
  classification: 'GOVERNED' | 'RESEARCH' | 'UNCLASSIFIED'
  sportsbook: string | null
  sportsbook_key: string | null
  selected_american: number | null
  opposite_american: number | null
  older_leg_observed_at: string | null
  older_leg_age_minutes_at_decision: number | null
  fresh_at_decision: boolean
}

export interface ForecastCheck {
  engine_id: string
  controlling: boolean
  latest_at_decision: boolean
  superseded_since_decision: boolean
  /** Historical name: shadow_forecasts.created_at, the START of AISP's import transaction (published_at_basis). */
  published_at: string | null
  published_at_basis?: string
}

export interface DecisionBoard {
  schema_version: string
  date: string
  generated_at: string
  counts: Record<DecisionGroup, number>
  exposure: number
  staked: number
  realized_profit_loss: number
  next_decision_deadline: string | null
  items: DecisionItem[]
}

export const decisionsKey = (day: string) => cacheKey('command-decisions', { day })
export const loadDecisions = (day: string) => async (signal: AbortSignal) =>
  expectSchema(await apiRequest<DecisionBoard>(`/command-center/decisions?date=${encodeURIComponent(day)}`, {}, signal), 'AISP_COMMAND_CENTER_DECISIONS_V1')

export function useDecisionBoard(day: string) {
  const loader = useCallback((signal: AbortSignal) => loadDecisions(day)(signal), [day])
  return useApiResource<DecisionBoard>(loader, {
    key: decisionsKey(day), staleTimeMs: STALE_TIME.slate, refreshIntervalMs: 120_000,
  })
}

// ----------------------------------------------------------------- markets --

export type PriceState = 'CURRENT_PRICE' | 'STORED_REFERENCE_ONLY' | 'NOT_COLLECTED'

export interface CoverageQuote {
  market: 'MONEYLINE' | 'SPREAD' | 'TOTAL' | string
  line: number | null
  sportsbook: string | null
  sportsbook_key: string | null
  provider: string | null
  classification: 'GOVERNED' | 'RESEARCH' | 'UNCLASSIFIED'
  observed_at: string
  age_minutes: number
  fresh: boolean
  sides: Record<string, { american: number | null; decimal: number | null }>
  no_vig_home?: number
  /** College Football only: per-side book clocks, AISP's fetch and receipt clocks. */
  clocks?: QuoteClocks
}

export interface CoverageGame {
  game_id: string
  league: string
  matchup: string | null
  home?: { abbreviation: string; name: string }
  away?: { abbreviation: string; name: string }
  starts_at: string | null
  status: string
  price_state: PriceState
  books: number
  fresh_books: number
  freshest_observed_at: string | null
  stalest_usable_observed_at: string | null
  consensus_no_vig_home: number | null
  consensus_is_current: boolean
  best: Record<'HOME' | 'AWAY', { american: number | null; sportsbook: string | null; provider: string | null; observed_at: string } | null>
  /** created_at is the START of AISP's import transaction (created_at_basis), not publication, insert or commit. */
  model: { engine_id: string; home: number | null; away: number | null; home_two_way: number | null; expected_home?: number | null; expected_away?: number | null; created_at: string | null; created_at_basis?: string } | null
  quotes: CoverageQuote[]
  /** College Football has no collection checkpoint: its collection time is null. */
  checkpoints: { collection_opens_at: string | null; decision_window_opens_at: string; decision_window_closes_at: string }
  /** Why a College Football game has no price (server-stated). */
  price_unavailable?: { reason: string; detail: string; reference_lines?: string }
  /** MLB/NFL: the market and the model, each from its own evidence (services/pregameStatus). */
  market_status?: MarketStatus
  model_status?: ModelStatus
}

export interface MarketCoverage {
  schema_version: string
  date: string
  generated_at: string
  freshness_window_minutes: number
  summary: {
    games: number
    by_state: Partial<Record<PriceState, number>>
    by_league: Record<string, number>
    sportsbooks: string[]
    providers: string[]
    freshest_observed_at: string | null
    stalest_usable_observed_at: string | null
    next_collection_at: string | null
    latest_snapshot?: { date: string; observed_at: string } | null
  }
  provider_calls_made: number
  games: CoverageGame[]
}

export const marketsKey = (day: string) => cacheKey('command-markets', { day })
export const loadMarkets = (day: string) => async (signal: AbortSignal) =>
  expectSchema(await apiRequest<MarketCoverage>(`/command-center/markets?date=${encodeURIComponent(day)}`, {}, signal), 'AISP_COMMAND_CENTER_MARKET_COVERAGE_V1')

export function useMarketCoverage(day: string) {
  const loader = useCallback((signal: AbortSignal) => loadMarkets(day)(signal), [day])
  return useApiResource<MarketCoverage>(loader, {
    key: marketsKey(day), staleTimeMs: STALE_TIME.slate, refreshIntervalMs: 120_000,
  })
}

/** Coverage for several slate dates at once (an NFL week spans several), keyed by game. */
export function useCoverageForDates(dates: string[]) {
  const key = [...new Set(dates)].sort().join(',')
  const loader = useCallback(async (signal: AbortSignal) => {
    const days = key ? key.split(',') : []
    const boards = await Promise.all(days.map(async (d) =>
      expectSchema(await apiRequest<MarketCoverage>(`/command-center/markets?date=${encodeURIComponent(d)}`, {}, signal), 'AISP_COMMAND_CENTER_MARKET_COVERAGE_V1')))
    const byGame: Record<string, CoverageGame> = {}
    for (const board of boards) for (const game of board.games) byGame[game.game_id] = game
    return { data_mode: 'live' as const, byGame }
  }, [key])
  return useApiResource(loader, { key: cacheKey('command-markets-dates', { key }), staleTimeMs: STALE_TIME.slate })
}

export const etDateOf = (iso: string | null | undefined): string | null =>
  iso ? new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(iso)) : null

// ----------------------------------------------------------------- helpers --

export const money = (value: number | null | undefined, digits = 2): string =>
  value == null || !Number.isFinite(value) ? '—'
    : value.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: digits, maximumFractionDigits: digits })

export const signedMoney = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? '—' : `${value > 0 ? '+' : value < 0 ? '−' : ''}${money(Math.abs(value))}`

export function etTime(iso: string | null | undefined, withDay = false): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit',
    ...(withDay ? { weekday: 'short', month: 'short', day: 'numeric' } : {}),
  }).format(date) + ' ET'
}

/** "in 2 h 5 min" / "12 min ago". */
export function relative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '—'
  const minutes = Math.round((Date.parse(iso) - now) / 60_000)
  const abs = Math.abs(minutes)
  const text = abs < 60 ? `${abs} min` : abs < 48 * 60 ? `${Math.floor(abs / 60)} h ${abs % 60} min` : `${Math.round(abs / 1440)} days`
  return minutes >= 0 ? `in ${text}` : `${text} ago`
}

export function gameHref(sport: string, gameId: string | null | undefined): string {
  if (!gameId) return sport === 'CFB' ? '/college-football/research' : sport === 'NBA' ? '/nba/research' : '/games'
  if (sport === 'NBA') return `/nba/games/${gameId}`
  if (sport === 'CFB') return `/college-football/games/${gameId}`
  return `/games/${gameId}?sport=${sport}`
}

export const PRICE_STATE_LABEL: Record<PriceState, string> = {
  CURRENT_PRICE: 'Current sportsbook price',
  STORED_REFERENCE_ONLY: 'Stored reference only',
  NOT_COLLECTED: 'Sportsbook price not collected',
}
