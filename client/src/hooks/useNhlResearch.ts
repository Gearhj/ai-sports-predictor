/** Imported NHL Gen1 research forecasts and results. Stored reads; no provider call. */
import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useApiResource } from './useApiResource'
import { cacheKey, STALE_TIME } from '../services/queryCache'
import { apiRequest } from '../services/api'
import type { NhlBoard, NhlGameDetail } from '../types/nhlResearch'

/**
 * "Show test data": one URL flag (``nhlTest=1``) shared by every NHL surface. Off by
 * default, so preview fixtures and Model Studio synthetic examples never appear in
 * real-game counts or Model Performance numbers unless the reader asks for them.
 */
export function useNhlTestData(): [boolean, (on: boolean) => void] {
  const [params, setParams] = useSearchParams()
  const on = params.get('nhlTest') === '1'
  const set = (next: boolean) => {
    const copy = new URLSearchParams(params)
    if (next) copy.set('nhlTest', '1'); else copy.delete('nhlTest')
    setParams(copy, { replace: true })
  }
  return [on, set]
}

export function NHL_TEST_QUERY(on: boolean): string { return on ? 'test_data=true' : '' }

export function useNhlBoard(date?: string | null, testData = false) {
  const loader = useCallback(async (signal: AbortSignal) => {
    const parts = [date ? `date=${encodeURIComponent(date)}` : '', NHL_TEST_QUERY(testData)].filter(Boolean)
    return apiRequest<NhlBoard>(`/nhl/research${parts.length ? `?${parts.join('&')}` : ''}`, {}, signal)
  }, [date, testData])
  return useApiResource<NhlBoard>(loader, {
    key: cacheKey('nhl-research', { date, testData }),
    // Imported evidence: it changes when a package is imported, not on a clock.
    staleTimeMs: STALE_TIME.history,
  })
}

export function useNhlGame(sourceGameId: string | undefined) {
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!sourceGameId) return null
    return apiRequest<NhlGameDetail>(`/nhl/games/${encodeURIComponent(sourceGameId)}`, {}, signal)
  }, [sourceGameId])
  return useApiResource<NhlGameDetail | null>(loader, {
    key: cacheKey('nhl-game', { sourceGameId }),
    staleTimeMs: STALE_TIME.history,
  })
}
