/**
 * Verified-population probabilities (Model Studio contract v1) as AISP serves them.
 *
 * Every probability here was computed server-side from one exact, re-verified
 * paired score population. This module never combines probabilities itself:
 * same-game parlays are the server's per-draw intersection, and cross-game
 * combinations do not exist -- the server refuses them with
 * CROSS_GAME_CALIBRATION_NOT_VALIDATED and this client offers no path to one.
 */
import { useCallback } from 'react'
import { useApiResource } from '../hooks/useApiResource'
import { apiRequest } from './api'
import { expectSchema, type SportCode } from './commandCenter'
import { STALE_TIME, cacheKey } from './queryCache'

export const CROSS_GAME_CODE = 'CROSS_GAME_CALIBRATION_NOT_VALIDATED'
export const CROSS_GAME_MESSAGE = 'Cross-game combinations are not yet validated. AISP is collecting prospective calibration and dependency evidence.'
export const PARLAY_UNAVAILABLE = 'Model probability available. Sportsbook parlay price unavailable, so edge and expected return cannot be assessed.'

export interface PopulationBinding {
  sport: SportCode
  gameId: string
  forecastId: string
  modelId: string
  configurationId: string
  packageSha256: string
  populationSha256: string
  drawCount: number
}

export interface Masses { win: number; loss: number; push: number; void: number }

export interface WinnerMass { home: number | null; away: number | null; tie: number | null }

export interface Channel {
  channel: 'PAIRED_POPULATION' | 'POINT_ELO'
  status: 'SUPPORTED' | 'UNSUPPORTED' | 'WINNER_ONLY'
  binding?: PopulationBinding
  capabilities?: { moneyline: boolean; spread: boolean; total: boolean; same_game: boolean }
  authority?: string
  authority_label?: string
  tie_policy?: string
  moneyline_tie_settlement?: string
  forecast_created_at?: string | null
  source_cutoff?: string | null
  horizon?: string | null
  winner?: WinnerMass
  expected_score?: { home: number | null; away: number | null }
  forecast_id?: string
  error?: { code: string; message: string }
  unsupported?: { code: string; message: string }
}

export interface LegMasses extends Masses {
  conditional: number | null
  mcse: number
  fair_decimal: number | null
  fair_american: number | null
}

export interface AssessedSide {
  american: number | null
  decimal: number | null
  no_vig: number | null
  model: LegMasses | null
  edge: number | null
  expected_return: number | null
  edge_reason: string | null
}

export interface AssessedQuote {
  market: string
  line: number | null
  sportsbook: string | null
  sportsbook_key: string | null
  provider: string | null
  classification: 'GOVERNED' | 'RESEARCH' | 'UNCLASSIFIED'
  observed_at: string
  age_minutes: number
  fresh: boolean
  state: 'CURRENT' | 'STORED_REFERENCE_ONLY'
  sides: Record<string, AssessedSide>
}

export interface Leg {
  market: 'MONEYLINE' | 'SPREAD' | 'TOTAL'
  selection: 'HOME' | 'AWAY' | 'TIE' | 'OVER' | 'UNDER'
  line?: number
  tieSettlement?: 'PUSH' | 'LOSS' | 'VOID'
}

export interface ParlayAssessment {
  quote_status: 'SPORTSBOOK_PARLAY_PRICE_UNAVAILABLE' | 'RECORDED_SPORTSBOOK_PARLAY_PRICE'
  message: string
  quote: Record<string, unknown> | null
  implied_probability: number | null
  edge: number | null
  expected_return: number | null
}

export interface ParlayExample {
  legs: Leg[]
  probabilities: Masses
  counts: Masses
  effective_draw_count: number
  mcse: number | null
  method: string
  display_method: 'PAIRED_DRAW_INTERSECTION'
  fair_american: number | null
  population: PopulationBinding
  authority: string
  authority_label: string
  parlay: ParlayAssessment
}

export interface GameAssessment {
  game_id: string
  sport: SportCode
  binding: Channel
  quotes: AssessedQuote[]
  parlay_lab: ParlayExample[]
}

export interface ResearchComparison {
  game_id: string
  sport: SportCode
  matchup: string | null
  home?: { abbreviation: string; name: string }
  away?: { abbreviation: string; name: string }
  starts_at: string | null
  channels: Channel[]
  price_state: 'NO_GOVERNED_PRICE'
  price_note: string
}

export interface DailyAssessments {
  schema_version: string
  date: string
  generated_at: string
  moneyline_tie_settlement: string
  games: GameAssessment[]
  research: ResearchComparison[]
  parlay_policy: { sportsbook_parlay_quotes_stored: number; unavailable_message: string; creates_decisions: false; creates_positions: false }
  cross_game: { status: 'UNSUPPORTED'; code: string; message: string }
  /** Player-prop availability. Quotes appear only with complete canonical identity. */
  player_props?: {
    contract: string
    required_identity: string[]
    status: 'NOT_STORED' | 'AVAILABLE'
    quotes: PlayerPropQuote[]
    assessment: string
    message: string
  }
  provider_calls_made: number
}

export interface PlayerPropQuote {
  game_id: string
  player_id: string
  player_name: string
  market_key: string
  outcome: string
  line: number | null
  american: number
  sportsbook: string
  observed_at: string
}

export interface ProbabilityResult {
  schema_version: string
  kind: 'SINGLE_MARKET' | 'SAME_GAME_PARLAY'
  display_method: string
  authority: string
  authority_label: string
  fair_american: number | null
  creates_decision: false
  creates_position: false
  contract: {
    method: string
    legs: Leg[]
    counts: Masses
    probabilities: Masses
    effectiveDrawCount: number
    winProbabilityMonteCarloStandardError: number
    population: PopulationBinding
  }
  parlay?: ParlayAssessment
}

export const assessmentsKey = (day: string) => cacheKey('command-assessments', { day })

export function useAssessments(day: string) {
  const loader = useCallback(async (signal: AbortSignal) => expectSchema(
    await apiRequest<DailyAssessments>(`/command-center/assessments?date=${encodeURIComponent(day)}`, {}, signal),
    'AISP_DAILY_ASSESSMENTS_V1'), [day])
  return useApiResource<DailyAssessments>(loader, {
    key: assessmentsKey(day), staleTimeMs: STALE_TIME.slate, refreshIntervalMs: 300_000,
  })
}

/** One market or a same-game intersection, against the exact binding the server issued. */
export function evaluateSameGame(binding: PopulationBinding, legs: Leg[], signal?: AbortSignal) {
  return apiRequest<ProbabilityResult>('/probability/evaluate', {
    method: 'POST',
    body: JSON.stringify({ schemaVersion: 'ASP_MARKET_PROBABILITY_REQUEST_V1', populationBinding: binding, legs, crossGameMode: 'REJECT' }),
  }, signal)
}

export const pct = (v: number | null | undefined, digits = 1) => (v == null ? '—' : `${(v * 100).toFixed(digits)}%`)
export const american = (v: number | null | undefined) => (v == null ? '—' : v > 0 ? `+${v}` : `${v}`)

export function legLabel(leg: Leg, home: string, away: string): string {
  const team = leg.selection === 'HOME' ? home : leg.selection === 'AWAY' ? away : leg.selection
  if (leg.market === 'MONEYLINE') return leg.selection === 'TIE' ? 'Tie' : `${team} to win`
  if (leg.market === 'SPREAD') return `${team} ${leg.line != null && leg.line > 0 ? '+' : ''}${leg.line}`
  return `${leg.selection === 'OVER' ? 'Over' : 'Under'} ${leg.line}`
}

export const AUTHORITY_TEXT: Record<string, string> = {
  CONTROLLING_VIRTUAL_RESEARCH: 'Controlling forecast · virtual research',
  PAPER_RESEARCH_EVIDENCE: 'Research / paper evidence',
  RESEARCH_ONLY: 'Research only',
}

export const EDGE_REASON_TEXT: Record<string, string> = {
  QUOTE_NOT_CURRENT: 'Price is not current — no edge assessed',
  PROVIDER_NOT_PERMITTED: 'Provider not permitted by the decision policy — no edge assessed',
  NO_VIG_UNAVAILABLE: 'No two-sided price — no edge assessed',
  NO_VERIFIED_POPULATION: 'No verified simulation population',
  LINE_MISSING: 'Line missing — not assessed',
}
