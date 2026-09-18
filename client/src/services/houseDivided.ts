/**
 * A House Divided — the virtual competition's read model and write calls.
 *
 * Every number shown on the page comes from the server's persisted records.
 * A pick sends only a quote id and a stake: the server reads the price, line
 * and book from the stored quote and enforces every rule.
 */
import { apiRequest } from './api'

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
  is_admin: boolean
  drafts: WagerView[]
}

export const houseDivided = {
  board: (sport: SportView, signal?: AbortSignal) =>
    apiRequest<Board>(`/house-divided?sport=${sport}`, {}, signal),
  openGames: (sport: CompetitionSport, signal?: AbortSignal) =>
    apiRequest<{ sport: string; games: OpenGame[] }>(`/house-divided/games?sport=${sport}`, {}, signal),
  quotes: (gameId: string, signal?: AbortSignal) =>
    apiRequest<{ game: OpenGame; quotes: Quote[]; cutoff: string }>(`/house-divided/games/${gameId}/quotes`, {}, signal),
  me: (signal?: AbortSignal) => apiRequest<EntryAccess>('/house-divided/me', {}, signal),
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
