/** The imported NBA joint comparison. One stored read; no provider call. */
import { useCallback } from 'react'
import { useApiResource } from './useApiResource'
import { cacheKey, STALE_TIME } from '../services/queryCache'
import { apiRequest } from '../services/api'
import type { NbaJointComparison } from '../types/nbaJoint'

function isJoint(value: unknown): value is NbaJointComparison {
  if (!value || typeof value !== 'object') return false
  const payload = value as Partial<NbaJointComparison>
  return payload.schema_version === 'AISP_NBA_JOINT_COMPARISON_V1'
    && Array.isArray(payload.arms)
}

export function useNbaJoint(gameId?: string) {
  const loader = useCallback(async (signal: AbortSignal) => {
    const query = gameId ? `?game_id=${encodeURIComponent(gameId)}` : ''
    const payload = await apiRequest<unknown>(
      `/nba/joint-comparison${query}`, {}, signal)
    return isJoint(payload) ? payload : null
  }, [gameId])
  return useApiResource<NbaJointComparison | null>(loader, {
    key: cacheKey('nba-joint', { gameId }),
    staleTimeMs: STALE_TIME.history,
  })
}
