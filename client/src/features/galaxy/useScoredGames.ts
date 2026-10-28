import { useCallback } from 'react'
import { useApiResource } from '../../hooks/useApiResource'
import { apiRequest } from '../../services/api'
import { STALE_TIME, cacheKey } from '../../services/queryCache'
import type { GalaxyGame } from './galaxy'

/** Every scored game, from the Performance Overview's own default request and cache key (one response, shared). */
export function useScoredGames() {
  const loader = useCallback(async (signal: AbortSignal) => apiRequest<{ games: GalaxyGame[] }>('/research/model-performance/overview', {}, signal), [])
  return useApiResource<{ games: GalaxyGame[] }>(loader, { key: cacheKey('model-performance-overview-v2', { sport: 'ALL', model: 'ALL', type: 'ALL', lead: 'ALL', nhlTest: false }), staleTimeMs: STALE_TIME.slate })
}
