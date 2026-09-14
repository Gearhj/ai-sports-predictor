/**
 * One read of the whole slate, shared by the board and its filters.
 *
 * The shape is checked before it is handed over. A page that renders whatever
 * came back will crash the entire route on the first response that is not a
 * slate -- a stubbed fetch, a proxy error page, an older backend without the
 * endpoint -- and take the rest of Daily Decisions down with it. An unexpected
 * payload is a slate AISP does not have, which is exactly `null`.
 */
import { useCallback } from 'react'
import { useApiResource } from './useApiResource'
import { cacheKey, STALE_TIME } from '../services/queryCache'
import { apiRequest } from '../services/api'
import type { SlateStatus } from '../types/slate'

function isSlateStatus(value: unknown): value is SlateStatus {
  if (typeof value !== 'object' || value === null) return false
  const payload = value as Partial<SlateStatus>
  return Array.isArray(payload.games)
    && typeof payload.window === 'object' && payload.window !== null
}

export function useSlateStatus(day?: string, enabled = true) {
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!enabled) return null
    const query = new URLSearchParams()
    if (day) query.set('date', day)
    const payload = await apiRequest<unknown>(
      `/slate-status?${query.toString()}`, {}, signal)
    return isSlateStatus(payload) ? payload : null
  }, [day, enabled])
  return useApiResource<SlateStatus | null>(loader, {
    key: cacheKey('slate-status', { day, enabled }),
    staleTimeMs: STALE_TIME.slate,
    refreshIntervalMs: STALE_TIME.slate,
  })
}
