/**
 * The drawable play population for one scenario.
 *
 * ~500 KB per scenario, so each one is fetched at most once and kept. Switching
 * scenarios is the primary interaction in the population view, and paying half
 * a megabyte every time a user flips back to BASE would make the comparison
 * that the view exists for feel broken.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { endpoints } from '../services/api'
import type { NflPlayPopulation } from '../types/nfl'

export interface PlayPopulationResource {
  population: NflPlayPopulation | null
  loading: boolean
  error: string | null
  /** Load and cache another scenario without disturbing the current one. */
  preload: (scenarioId: string) => Promise<NflPlayPopulation | null>
  cached: (scenarioId: string) => NflPlayPopulation | null
}

export function useNflPlayPopulation(gameId: string, scenarioId: string): PlayPopulationResource {
  const [population, setPopulation] = useState<NflPlayPopulation | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const cache = useRef(new Map<string, NflPlayPopulation>())

  useEffect(() => { cache.current.clear() }, [gameId])

  const fetchOne = useCallback(async (scenario: string, signal?: AbortSignal) => {
    const hit = cache.current.get(scenario)
    if (hit) return hit
    const result = await endpoints.nflPlayPopulation(gameId, scenario, signal)
    cache.current.set(scenario, result)
    return result
  }, [gameId])

  useEffect(() => {
    if (!gameId || !scenarioId) return
    const hit = cache.current.get(scenarioId)
    if (hit) { setPopulation(hit); setError(null); setLoading(false); return }
    const controller = new AbortController()
    setLoading(true)
    fetchOne(scenarioId, controller.signal)
      .then((result) => { if (!controller.signal.aborted) { setPopulation(result); setError(null) } })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        setPopulation(null)
        setError(reason instanceof Error ? reason.message : 'Play population unavailable')
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [gameId, scenarioId, fetchOne])

  const preload = useCallback(async (scenario: string) => {
    try { return await fetchOne(scenario) } catch { return null }
  }, [fetchOne])

  const cached = useCallback((scenario: string) => cache.current.get(scenario) ?? null, [])

  return { population, loading, error, preload, cached }
}
