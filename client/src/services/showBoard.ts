/**
 * AISP DAY-BEFORE SHOW PREVIEW V1: the Show Board read model.
 *
 * `/show-board?date=` returns a compact board (complete, non-stale pairs and
 * counts) plus mode, flags, snapshot identity and any preview-versus-final
 * pairings. `/show-board/game` returns every leg for one game and is fetched
 * only when a reader opens that game's evidence. Both are read-only: no
 * provider request, no forecast generation, no wagering policy.
 */
import { useCallback } from 'react'
import { useApiResource } from '../hooks/useApiResource'
import { apiRequest } from './api'
import { expectSchema } from './commandCenter'
import { STALE_TIME, cacheKey } from './queryCache'

export type ShowMode = 'DRAFT' | 'FROZEN' | 'RECONSTRUCTED'
export type Readiness = 'READY' | 'FORECAST_ONLY' | 'MARKET_ONLY' | 'NEITHER'
export type ShowSport = 'MLB' | 'NFL' | 'NBA' | 'CFB'

export interface ShowLeg { side: string; american: number | null; point: number | null; observed_utc?: string; retrieved_utc?: string; snapshot_id?: string; decimal?: number | null }
export interface ShowPair {
  sportsbook: string; provider: string; classification: string; line: number | null; older_leg_observed_utc: string | null
  no_vig_home: number | null; settlement: string; legs: ShowLeg[]
}
export interface ShowMarketGroup { pairs: ShowPair[]; complete: number; stale: number; partial: number; latest_capture_utc: string | null }

export interface ShowForecast {
  forecast_id: string; content_sha256: string; model_id: string; model_version: string; created_utc: string; source_cutoff_utc: string
  expected_score: { away: number; home: number }; projected_margin_home: number; projected_total: number
  probabilities: { away_win: number; home_win: number; tie: number }
  lean: { side: 'HOME' | 'AWAY'; team: string; probability_two_way: number | null }
  starters: string; lineups: string
}

export interface ShowComparison {
  label: string; selection: 'HOME' | 'AWAY'; team: string
  model_probability: number; no_vig_probability: number; difference: number; expected_return: number
  american: number | null; opposite_american: number | null; sportsbook: string; provider: string; classification: string
  settlement: string; price_observed_utc: string | null; forecast_created_utc: string | null
}

export interface ShowGame {
  game_key: string; canonical_game_id: string | null; sport: ShowSport; matchup: string
  away: { abbreviation: string; name: string }; home: { abbreviation: string; name: string }
  kickoff_utc: string | null; status: string
  final_decision_window: { opens_utc: string; closes_utc: string } | null
  readiness: Readiness; readiness_label: string; forecast_status: 'AVAILABLE' | 'AWAITING_PRODUCER'
  forecast: ShowForecast | null
  markets: Record<'MONEYLINE' | 'SPREAD' | 'TOTAL', ShowMarketGroup>
  market_summary: {
    moneyline: null | { books: number; complete_books: number; stale_books: number; partial_books: number; consensus_no_vig_home: number | null
      range_no_vig_home: [number, number] | null; best: Partial<Record<'HOME' | 'AWAY', { american: number; sportsbook: string; provider: string; observed_utc: string | null }>>; captured_utc: string | null }
    spread: null | { median_line: number; range: [number, number]; books: number; captured_utc: string }
    total: null | { median_line: number; range: [number, number]; books: number; captured_utc: string }
  }
  comparison: ShowComparison | null
  rff: { supported: boolean; note: string }
  /** Consumer-side obligations the forecast must be shown with. */
  disclosures: { code: string; message: string }[]
  missing: { code: string; message: string }[]
}

export interface ShowClockStep { key: string; label: string; local_time: string | null; at_utc: string | null; provider_calls: boolean; implemented: boolean; note: string }

export interface ShowBoardPayload {
  schema_version: string; horizon: 'SHOW_PREVIEW'; status_label: string; slate_date: string; timezone: string
  planned_freeze_utc: string; evidence_as_of_utc: string
  authority: { statement: string; grants_wagering_authority: false; creates_positions: false }
  readiness: { games: number; preview_forecasts: number; complete_markets: number; both: number; missing_evidence_games: number; valid_comparisons: number; by_sport: Record<string, number> }
  games: ShowGame[]
  ranking: { rank: number; game_key: string; difference: number; expected_return: number }[]
  talking_points: { code: string; text: string; game_key: string | null }[]
  disclosures: { game_key: string | null; code: string; message: string }[]
  clock: ShowClockStep[]
}

export interface PreviewFinalRow {
  game_key: string; matchup: string; sport: ShowSport
  preview: { selection: string | null; model_probability: number | null; no_vig_probability: number | null; difference: number | null; american: number | null; sportsbook: string | null; price_observed_utc: string | null; forecast_status: string; consensus_no_vig_home: number | null }
  final: null | { selection: string | null; model_probability: number | null; no_vig_probability: number | null; american: number | null; sportsbook: string | null; price_observed_utc: string | null; disposition: string; decided_utc: string | null }
  changes: null | { selection_changed: boolean; model_probability_change: number | null; price_change_american: number | null }
  position: null | { stake: number; status: string; result: string | null; profit_loss: number | null }
  outcome: null | { away_score: number; home_score: number; winner: string }
}

export interface ShowBoardResponse {
  schema_version: string; generated_at_utc: string; slate_date: string; default_slate_date: string
  mode: ShowMode; mode_label: string; planned_freeze_utc: string
  flags: { show_preview_enabled: boolean; provider_collection_enabled: boolean; auto_freeze_enabled: boolean }
  snapshot: null | { snapshot_sha256: string; frozen_utc: string; trigger: string; verified: boolean; predecessor_sha256: string | null; supersede_reason: string | null; payload_bytes: number }
  history: { snapshot_sha256: string; frozen_utc: string; trigger: string; predecessor: boolean }[]
  board: ShowBoardPayload
  preview_vs_final: PreviewFinalRow[]
  provider_calls_made: number
}

export const showBoardKey = (date: string | null) => cacheKey('show-board', { date: date ?? 'next' })

export function useShowBoard(date: string | null) {
  const loader = useCallback(async (signal: AbortSignal) => expectSchema(
    await apiRequest<ShowBoardResponse>(`/show-board${date ? `?date=${encodeURIComponent(date)}` : ''}`, {}, signal),
    'AISP_SHOW_BOARD_RESPONSE_V1'), [date])
  return useApiResource<ShowBoardResponse>(loader, { key: showBoardKey(date), staleTimeMs: STALE_TIME.slate, refreshIntervalMs: 120_000 })
}

export interface ShowGameDetail { schema_version: string; slate_date: string; evidence_as_of_utc: string; snapshot_sha256: string | null; game: Omit<ShowGame, 'markets'> & { markets: Record<string, (ShowPair & { complete: boolean; stale: boolean; retrieved_utc: string | null; source_request: null | { endpoint_key: string; objects_in_request: number; match: string } })[]> } }

export function useShowBoardGame(date: string, gameKey: string) {
  const loader = useCallback(async (signal: AbortSignal) => expectSchema(
    await apiRequest<ShowGameDetail>(`/show-board/game?date=${encodeURIComponent(date)}&game_key=${encodeURIComponent(gameKey)}`, {}, signal),
    'AISP_SHOW_BOARD_GAME_V1'), [date, gameKey])
  return useApiResource<ShowGameDetail>(loader, { key: cacheKey('show-board-game', { date, gameKey }), staleTimeMs: STALE_TIME.slate })
}
