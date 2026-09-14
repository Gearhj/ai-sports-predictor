/** One read of why each game is or is not priced. No provider call. */
import { useCallback } from 'react'
import { useApiResource } from './useApiResource'
import { cacheKey, STALE_TIME } from '../services/queryCache'
import { apiRequest } from '../services/api'
import type { PriceCoverage } from '../types/priceCoverage'

function isCoverage(value: unknown): value is PriceCoverage {
  if (!value || typeof value !== 'object') return false
  const payload = value as Partial<PriceCoverage>
  return payload.schema_version === 'AISP_PRICE_COVERAGE_V1'
    && Array.isArray(payload.games)
}

export function usePriceCoverage(day?: string) {
  const loader = useCallback(async (signal: AbortSignal) => {
    const query = day ? `?date=${encodeURIComponent(day)}` : ''
    const payload = await apiRequest<unknown>(`/price-coverage${query}`, {}, signal)
    return isCoverage(payload) ? payload : null
  }, [day])
  return useApiResource<PriceCoverage | null>(loader, {
    key: cacheKey('price-coverage', { day }),
    staleTimeMs: STALE_TIME.slate,
    refreshIntervalMs: STALE_TIME.slate,
  })
}
