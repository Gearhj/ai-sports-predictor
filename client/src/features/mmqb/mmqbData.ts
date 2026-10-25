/**
 * Monday Morning QB data: the read-only weekly report the backend already builds (`/research/mmqb`, schema
 * AISP_MMQB_REPORT_V1) and its week index. Only the fields the page reads are typed.
 */
import { useCallback } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey } from '../../services/queryCache'

export interface MmqbWeek { season: number; week: number; label: string; positions: number; first_kickoff_utc: string | null }
export interface MmqbIndex { league: string; weeks: MmqbWeek[]; latest: MmqbWeek | null }

export interface MmqbBet {
  position_id: string
  matchup: string
  kickoff_et: string
  selected_team: string
  selection_label: string
  selected_is_model_favourite: boolean
  p_win_two_way: number | null
  no_vig_selected: number | null
  model_minus_market_pts: number | null
  price_group: string
  sportsbook: string | null
  entry_american: number | null
  stake: number
  result: string | null
  net_profit: number | null
  final: { home: number; away: number } | null
  home: string
  away: string
  overtime: boolean
}
export interface MmqbTally { n: number; settled: number; wins: number; losses: number; pushes: number; settled_stake: number; net_profit: number; roi: number | null; roi_ci95?: [number, number] | null }
export interface MmqbFinding { key: string; title: string; finding: string; next_test: string; spoken?: string; experiment?: string }
export interface MmqbDeepDive {
  key: string; reason: string; matchup: string; selected_team: string; result: string; net_profit: number
  before_kickoff: { p_win_two_way: number | null; entry_american: number | null; book: string | null; no_vig_selected: number | null; model_minus_market_pts: number | null; expected_margin_home: number | null; expected_total: number | null; forecast_inputs?: string | null }
  observed: { final: { home: number; away: number } | null; margin_home: number | null; total: number | null; margin_percentile: number | null; total_percentile: number | null }
  hypotheses: Array<{ topic: string; text: string; link?: string | null }>
}
export interface MmqbReport {
  title: string
  window: { season: number; week: number; label: string }
  slate: MmqbBet[]
  games_without_position: Array<{ matchup: string; kickoff_et: string; governing_reason: string | null; note: string }>
  financial: { summary: MmqbTally & { expected_wins?: number } }
  cumulative_wagers: { week: MmqbTally; prior_weeks: MmqbTally; season_to_date: MmqbTally }
  findings: MmqbFinding[]
  deep_dives: MmqbDeepDive[]
  notes: string[]
  caveat: string
  data_cutoff: { statement: string }
}

export function useMmqbIndex(league = 'NFL') {
  const loader = useCallback((signal: AbortSignal) => apiRequest<MmqbIndex>(`/research/mmqb/index?league=${league}`, {}, signal), [league])
  return useApiResource<MmqbIndex>(loader, { key: cacheKey('research-mmqb-index', { league }), staleTimeMs: STALE_TIME.history })
}

export function useMmqbReport(league: string, season: number | null, week: number | null) {
  const loader = useCallback((signal: AbortSignal) => (season == null || week == null
    ? Promise.resolve(null)
    : apiRequest<MmqbReport>(`/research/mmqb?league=${league}&season=${season}&week=${week}`, {}, signal)), [league, season, week])
  return useApiResource<MmqbReport | null>(loader, { key: cacheKey('research-mmqb-json', { league, season, week }), staleTimeMs: STALE_TIME.history })
}

// Some report strings arrive double-encoded (UTF-8 read as Windows-1252: "â€“" for "–"). Undo it on display.
const CP1252: Record<string, number> = {
  '€': 0x80, '‚': 0x82, 'ƒ': 0x83, '„': 0x84, '…': 0x85, '†': 0x86, '‡': 0x87, 'ˆ': 0x88, '‰': 0x89, 'Š': 0x8a, '‹': 0x8b, 'Œ': 0x8c, 'Ž': 0x8e,
  '‘': 0x91, '’': 0x92, '“': 0x93, '”': 0x94, '•': 0x95, '–': 0x96, '—': 0x97, '˜': 0x98, '™': 0x99, 'š': 0x9a, '›': 0x9b, 'œ': 0x9c, 'ž': 0x9e, 'Ÿ': 0x9f,
}
export function fixText(s: string | null | undefined): string {
  if (!s) return ''
  if (!/[ÂÃâ][\u0080-ÿ€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]/.test(s)) return s
  const bytes: number[] = []
  for (const ch of s) {
    const c = ch.codePointAt(0) ?? 0
    if (c < 0x100) bytes.push(c)
    else if (CP1252[ch] != null) bytes.push(CP1252[ch])
    else return s
  }
  try { return new TextDecoder('utf-8', { fatal: true }).decode(new Uint8Array(bytes)) } catch { return s }
}

/** Gap bands used across the page: how far the model sat from the book's own no-vig price on the side taken. */
export function gapBand(pts: number | null): string {
  if (pts == null) return 'Unknown gap'
  const a = Math.abs(pts)
  return a > 10 ? 'Gap over 10 pts' : a >= 5 ? 'Gap 5–10 pts' : 'Gap under 5 pts'
}
