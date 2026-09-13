/**
 * Fetching an explanation, lazily and only when a panel is opened.
 *
 * The server side touches no provider and spends no quota, but a slate of
 * fifteen cards should still not issue fifteen requests before anyone asks a
 * question. `enabled` is what keeps a collapsed disclosure free.
 */
import { useCallback } from 'react'
import { useApiResource } from './useApiResource'
import { STALE_TIME, cacheKey } from '../services/queryCache'
import { apiRequest } from '../services/api'
import type { DecisionExplanation, SlateExplanations } from '../types/explain'

export function usePositionExplanation(positionId: string | null, enabled: boolean) {
  const loader = useCallback(
    async (signal: AbortSignal): Promise<DecisionExplanation | null> => {
      if (!enabled || !positionId) return null
      return apiRequest<DecisionExplanation>(
        `/explanations/positions/${encodeURIComponent(positionId)}`, {}, signal)
    },
    [positionId, enabled],
  )
  return useApiResource(loader, {
    key: cacheKey('explain/position', { positionId, enabled }),
    staleTimeMs: STALE_TIME.slate,
  })
}

export function useSlateExplanations(day: string, enabled: boolean) {
  const loader = useCallback(
    async (signal: AbortSignal): Promise<SlateExplanations | null> => {
      if (!enabled) return null
      return apiRequest<SlateExplanations>(
        `/explanations/slate?date=${encodeURIComponent(day)}`, {}, signal)
    },
    [day, enabled],
  )
  return useApiResource(loader, {
    key: cacheKey('explain/slate', { day, enabled }),
    staleTimeMs: STALE_TIME.slate,
  })
}
