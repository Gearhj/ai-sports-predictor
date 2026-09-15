/** Imported College Football research forecasts. Stored reads; no provider call. */
import { useCallback } from 'react'
import { useApiResource } from './useApiResource'
import { cacheKey, STALE_TIME } from '../services/queryCache'
import { apiRequest } from '../services/api'
import type { CfbResearchBoard, CfbResearchForecast, CfbTotalScenario } from '../types/cfbResearch'

export function useCfbResearch(date?: string | null) {
  const loader = useCallback(async (signal: AbortSignal) => {
    const query = date ? `?date=${encodeURIComponent(date)}` : ''
    return apiRequest<CfbResearchBoard>(`/cfb/research${query}`, {}, signal)
  }, [date])
  return useApiResource<CfbResearchBoard>(loader, {
    key: cacheKey('cfb-research', { date }),
    // Imported evidence. It changes when a package is imported, not on a clock.
    staleTimeMs: STALE_TIME.history,
  })
}

export function useCfbGame(sourceGameId: string | undefined) {
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!sourceGameId) return null
    return apiRequest<CfbResearchForecast>(
      `/cfb/games/${encodeURIComponent(sourceGameId)}`, {}, signal)
  }, [sourceGameId])
  return useApiResource<CfbResearchForecast | null>(loader, {
    key: cacheKey('cfb-game', { sourceGameId }),
    staleTimeMs: STALE_TIME.history,
  })
}

/**
 * One illustrative total line.
 *
 * Served rather than computed in the browser so the answer comes off the same
 * complete PMF the card renders — a second implementation here would be a
 * second chance to divide by the wrong denominator.
 */
export async function fetchTotalScenario(
  sourceGameId: string, line: number, signal?: AbortSignal,
): Promise<CfbTotalScenario> {
  return apiRequest<CfbTotalScenario>(
    `/cfb/games/${encodeURIComponent(sourceGameId)}/total-scenario?line=${line}`,
    {}, signal)
}
