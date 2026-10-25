/**
 * Historical market replay: stored sportsbook prices exactly as they stood at a
 * past instant. Read-only; the server returns quotes only -- no status, score,
 * model, edge or decision -- and every quote keeps its own observed time.
 * Picks made from it go to the separate replay card, never to My Card.
 */
import { useCallback } from 'react'
import { useApiResource } from '../hooks/useApiResource'
import { apiRequest } from './api'
import { expectSchema, type CoverageQuote } from './commandCenter'
import { STALE_TIME, cacheKey } from './queryCache'

export const REPLAY_SCHEMA = 'AISP_HISTORICAL_MARKET_REPLAY_V1'

export interface ReplayGame {
  game_id: string
  league: string
  matchup: string | null
  home?: { abbreviation: string; name: string }
  away?: { abbreviation: string; name: string }
  starts_at: string
  quotes: CoverageQuote[]
}

export interface MarketReplay {
  schema_version: string
  mode: 'HISTORICAL_REPLAY'
  available: boolean
  as_of: string | null
  as_of_label: string | null
  banner: string | null
  pick_label: string
  games: ReplayGame[]
  message?: string
  creates_decisions: false
  creates_positions: false
  provider_calls_made: number
}

/** Mount only inside replay mode: calling it is what fetches the replay. */
export function useReplay() {
  const loader = useCallback(async (signal: AbortSignal) => expectSchema(
    await apiRequest<MarketReplay>('/command-center/replay', {}, signal), REPLAY_SCHEMA), [])
  return useApiResource<MarketReplay>(loader, {
    key: cacheKey('command-replay', {}), staleTimeMs: STALE_TIME.slate,
  })
}
