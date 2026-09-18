/**
 * A House Divided — the virtual competition's read model and write calls.
 *
 * Every number shown on the page comes from the server's persisted records.
 * A pick sends only a quote id and a stake: the server reads the price, line
 * and book from the stored quote and enforces every rule.
 */
import { API_BASE_URL, ApiError, apiRequest, getStoredToken } from './api'

export type CompetitionSport = 'MLB' | 'NFL' | 'NBA' | 'CFB'
export type SportView = CompetitionSport | 'ALL'
export type ParticipantKey = 'JAMES' | 'SCOTTY' | 'RED_LOU' | string

export interface WagerView {
  wager_id: string
  participant: ParticipantKey
  sport: string
  source: 'MODEL_MIRROR' | 'HUMAN'
  status: 'DRAFT' | 'OPEN' | 'SETTLED' | 'UNFUNDED' | 'DISCARDED'
  game_id: string
  market_type: 'MONEYLINE' | 'SPREAD' | 'TOTAL' | string | null
  market_label: string
  selection: string | null
  line: number | null
  sportsbook: string | null
  american_odds: number | null
  decimal_odds: number | null
  quote_id: string | null
  quote_observed_at: string | null
  stake: number | null
  submitted_at: string | null
  created_at: string | null
  entered_by: string | null
  entered_on_behalf: boolean
  source_decision_id: string | null
  model_version: string | null
  decision_recommended_at: string | null
  staking_detail: Record<string, unknown> | null
  unfunded_reason: string | null
  result: 'WIN' | 'LOSS' | 'PUSH' | 'VOID' | null
  profit_loss: number | null
  payout: number | null
  settled_at: string | null
  settlement_note: string | null
  final_score: { home: number; away: number } | null
  /** A human pick before its game locks: terms withheld so nobody can copy it. */
  hidden?: boolean
  reveal_at?: string
}

export interface Standing {
  participant: ParticipantKey
  display_name: string
  color: string
  is_model: boolean
  rank: number
  starting_balance: number
  bankroll: number
  settled_profit: number
  open_exposure: number
  available: number
  roi: number | null
  settled_stake: number
  record: { WIN: number; LOSS: number; PUSH: number; VOID: number }
  settled_count: number
  open_count: number
  unfunded_count: number
  volume: number
  started_at?: string | null
  status?: string
  by_sport?: Standing[]
  sport?: string
}

export interface TeamRef { name: string; abbreviation: string }

export interface GameCell {
  participant: ParticipantKey
  wagers: WagerView[]
  state: { code: string; label: string; detail?: string }
}

export interface BoardGame {
  game_id: string
  starts_at: string
  cutoff: string
  status: string
  locked: boolean
  home: TeamRef | null
  away: TeamRef | null
  home_score: number | null
  away_score: number | null
  cells: GameCell[]
  comparisons: { a: string; b: string; relation: 'DIRECTLY_OPPOSED' | 'DIFFERENT_BETS' | 'DIFFERENT_LINES' | 'SAME_SIDE' }[]
  divided: boolean
}

export interface SportStatus {
  sport: CompetitionSport
  status: 'STARTED' | 'NOT_STARTED'
  started_at: string | null
  eligible: boolean
  reason: string | null
}

export interface ChartSeries {
  participant: ParticipantKey
  color: string
  display_name: string
  points: { at: string; bankroll: number; wager_id: string | null; now?: boolean }[]
}

export interface SportBoard {
  sport: CompetitionSport
  virtual: true
  label: string
  status: 'STARTED' | 'NOT_STARTED'
  started_at: string | null
  not_started_reason: string | null
  official_pipeline: { experiment_id: string; name: string; decision_policy: string; staking_policy: string } | null
  rules: { cutoff_minutes: number; max_quote_age_minutes: number; markets: string[]; parlays: string }
  standings: Standing[]
  chart: { starting_balance: number; series: ChartSeries[] }
  games: BoardGame[]
  history: WagerView[]
  sports: SportStatus[]
  generated_at: string
}

export interface AllSportsBoard {
  sport: 'ALL'
  virtual: true
  label: string
  started_sports: CompetitionSport[]
  combined_starting_capital_each: number
  note: string
  standings: Standing[]
  history: WagerView[]
  sports: SportStatus[]
  generated_at: string
}

export type Board = SportBoard | AllSportsBoard

export interface Quote {
  quote_id: string
  market_type: 'MONEYLINE' | 'SPREAD' | 'TOTAL'
  market_label: string
  selection: string
  line: number | null
  sportsbook: string
  sportsbook_name: string
  american_odds: number | null
  decimal_odds: number
  observed_at: string
  age_minutes: number
}

export interface OpenGame {
  game_id: string
  starts_at: string
  cutoff: string
  status: string
  locked: boolean
  home: TeamRef | null
  away: TeamRef | null
}

export interface EntryAccess {
  can_enter_for: ParticipantKey[]
  participants?: EntryParticipant[]
  is_admin: boolean
  drafts: WagerView[]
}

export interface BoardQuote extends Quote {
  outcome_key: string
}

export interface BoardOption {
  side: 'home' | 'away' | 'over' | 'under' | string
  selection: string
  line: number | null
  quotes: BoardQuote[]
  best: BoardQuote
  best_of: number
}

export interface SpreadRow { line: number | null; away: BoardOption | null; home: BoardOption | null }
export interface TotalRow { line: number | null; over: BoardOption | null; under: BoardOption | null }

export type GameStateCode = 'OPEN' | 'GAME_LOCKED' | 'SUSPENDED' | 'STALE' | 'EXPECTED' | 'NO_COVERAGE'

export interface PropsState {
  status: 'UNAVAILABLE' | 'AVAILABLE'
  message: string
  reason: string
  groups: { label: string; status: string }[]
  stored_current_quotes: number
}

export interface MarketGame extends OpenGame {
  human_lock_at: string
  lock_basis: 'CUTOFF' | 'MODEL_PICK_PUBLIC'
  lock_reason: string | null
  state: { code: GameStateCode; label: string; detail: string | null }
  expected_prices_at: string | null
  last_update: string | null
  books_available: number
  best_book: string | null
  moneyline: { away: BoardOption | null; home: BoardOption | null }
  spreads: SpreadRow[]
  totals: TotalRow[]
  spread_label: string
  props: PropsState
  home_score: number | null
  away_score: number | null
}

export interface MarketBoard {
  sport: CompetitionSport
  sports: { sport: CompetitionSport; status: 'STARTED' | 'NOT_STARTED'; reason: string | null }[]
  status: 'STARTED' | 'NOT_STARTED'
  date: string | null
  dates: { date: string; games: number }[]
  games: MarketGame[]
  prop_groups: string[]
  reason?: string
  generated_at?: string
}

export interface SlipWager extends WagerView {
  matchup: string
  away: TeamRef | null
  home: TeamRef | null
  starts_at: string
  human_lock_at: string
  lock_basis: string
  quote_age_minutes: number | null
  potential_profit: number | null
  potential_payout: number | null
  push_possible: boolean
  refund_rule: string
  confirmation_id: string | null
  available_before?: number
  available_after?: number
}

export interface ParticipantAccount {
  participant: string
  display_name: string
  color: string
  sport: CompetitionSport
  started_at: string | null
  bankroll: number
  available: number
  open_exposure: number
  drafts: SlipWager[]
  open_picks: SlipWager[]
}

export interface EntryParticipant { key: string; display_name: string; color: string; linked_to_you: boolean }

/** A refusal with its stable code and, for a moved price, the replacement quote. */
export class CompetitionRefusal extends ApiError {
  code: string
  replacement: BoardQuote | null
  constructor(message: string, status: number, code: string, replacement: BoardQuote | null) {
    super(message, status)
    this.code = code
    this.replacement = replacement
  }
}

async function competitionPost<T>(path: string, body?: unknown): Promise<T> {
  const headers = new Headers({ Accept: 'application/json' })
  if (body !== undefined) headers.set('Content-Type', 'application/json')
  const token = getStoredToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await response.json().catch(() => null) as
    | { detail?: { code?: string; message?: string; replacement?: BoardQuote | null } | string } | null
  if (!response.ok) {
    const detail = payload?.detail
    if (detail && typeof detail === 'object') {
      throw new CompetitionRefusal(detail.message ?? `Refused (${response.status})`, response.status,
        detail.code ?? 'REFUSED', detail.replacement ?? null)
    }
    throw new CompetitionRefusal(typeof detail === 'string' ? detail : `Request failed (${response.status})`,
      response.status, response.status === 401 ? 'SIGN_IN' : 'REFUSED', null)
  }
  return payload as T
}

export const houseDivided = {
  board: (sport: SportView, signal?: AbortSignal) =>
    apiRequest<Board>(`/house-divided?sport=${sport}`, {}, signal),
  openGames: (sport: CompetitionSport, signal?: AbortSignal) =>
    apiRequest<{ sport: string; games: OpenGame[] }>(`/house-divided/games?sport=${sport}`, {}, signal),
  quotes: (gameId: string, signal?: AbortSignal) =>
    apiRequest<{ game: OpenGame; quotes: Quote[]; cutoff: string }>(`/house-divided/games/${gameId}/quotes`, {}, signal),
  me: (signal?: AbortSignal) => apiRequest<EntryAccess>('/house-divided/me', {}, signal),
  marketBoard: (sport: CompetitionSport | null, date: string | null, signal?: AbortSignal) => {
    const query = [sport ? `sport=${sport}` : '', date ? `date=${date}` : ''].filter(Boolean).join('&')
    return apiRequest<MarketBoard>(`/house-divided/board${query ? `?${query}` : ''}`, {}, signal)
  },
  account: (participant: string, sport: CompetitionSport, signal?: AbortSignal) =>
    apiRequest<ParticipantAccount>(`/house-divided/account?participant=${participant}&sport=${sport}`, {}, signal),
  slipAdd: (participant: string, quoteId: string, stake = '') =>
    competitionPost<{ wager_id: string; status: string }>('/house-divided/drafts', { participant, quote_id: quoteId, stake }),
  slipUpdate: (wagerId: string, participant: string, quoteId: string, stake: string) =>
    competitionPost<{ wager_id: string; status: string }>(`/house-divided/drafts/${wagerId}`, { participant, quote_id: quoteId, stake }),
  place: (wagerId: string, stake: string, expectedQuoteId: string) =>
    competitionPost<{ wager_id: string; status: string; receipt: SlipWager }>(
      `/house-divided/drafts/${wagerId}/submit`, { stake, expected_quote_id: expectedQuoteId }),
  slipRemove: (wagerId: string) =>
    competitionPost<{ wager_id: string; status: string }>(`/house-divided/drafts/${wagerId}/discard`),
  saveDraft: (body: { participant: string; quote_id: string; stake: string }, wagerId?: string) =>
    apiRequest<{ wager_id: string; status: string }>(
      wagerId ? `/house-divided/drafts/${wagerId}` : '/house-divided/drafts',
      { method: 'POST', body: JSON.stringify(body) },
    ),
  submit: (wagerId: string) =>
    apiRequest<{ wager_id: string; status: string }>(`/house-divided/drafts/${wagerId}/submit`, { method: 'POST' }),
  discard: (wagerId: string) =>
    apiRequest<{ wager_id: string; status: string }>(`/house-divided/drafts/${wagerId}/discard`, { method: 'POST' }),
}

export const money = (value: number | null | undefined, signed = false): string => {
  if (value == null || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (!signed) return `${value < 0 ? '−' : ''}$${abs}`
  return `${value > 0 ? '+' : value < 0 ? '−' : ''}$${abs}`
}

export const americanOdds = (value: number | null | undefined): string =>
  value == null ? '—' : value > 0 ? `+${value}` : `${value}`

export const lineText = (marketType: string | null, line: number | null): string => {
  if (line == null) return ''
  if (marketType === 'TOTAL') return `${line}`
  return line > 0 ? `+${line}` : `${line}`
}

/** "Chiefs −3.5 · −110 · $20.00" — a wager's terms in one line. */
export function wagerTerms(w: Pick<WagerView, 'market_type' | 'market_label' | 'selection' | 'line' | 'american_odds'>): string {
  const line = lineText(w.market_type, w.line)
  const pick = w.market_type === 'TOTAL' ? `${w.selection ?? ''} ${line}` : `${w.selection ?? ''}${line ? ` ${line}` : ''}`
  return `${w.market_label} · ${pick.trim()} · ${americanOdds(w.american_odds)}`
}

export const RELATION_LABEL: Record<string, string> = {
  DIRECTLY_OPPOSED: 'Directly opposed',
  DIFFERENT_BETS: 'Different bets',
  DIFFERENT_LINES: 'Different lines',
  SAME_SIDE: 'Same side',
}
