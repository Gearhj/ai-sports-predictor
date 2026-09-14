/** Imported NBA research forecasts. One stored read; no provider call. */
import { useCallback } from 'react'
import { useApiResource } from './useApiResource'
import { cacheKey, STALE_TIME } from '../services/queryCache'
import { apiRequest } from '../services/api'
import type { NbaResearchBoard } from '../types/nbaResearch'

export function useNbaResearch(gameId?: string) {
  const loader = useCallback(async (signal: AbortSignal) => {
    const query = gameId ? `?game_id=${encodeURIComponent(gameId)}` : ''
    return apiRequest<NbaResearchBoard>(`/nba/research-forecasts${query}`, {}, signal)
  }, [gameId])
  return useApiResource<NbaResearchBoard>(loader, {
    key: cacheKey('nba-research', { gameId }),
    // Imported evidence. It changes when a package is imported, not on a clock.
    staleTimeMs: STALE_TIME.history,
  })
}
