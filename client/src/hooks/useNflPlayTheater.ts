/**
 * Loading play-level evidence in two steps.
 *
 * The index is small and describes every scenario, so it is fetched once. A
 * representative game is ~170 KB of plays and geometry, so exactly one is
 * fetched at a time -- the one being watched -- and previously loaded games are
 * kept so switching back is instant and costs nothing.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { endpoints } from '../services/api'
import type { NflPlayRepresentative, NflPlayTheaterIndex } from '../types/nfl'

export interface PlayTheaterResource {
  index: NflPlayTheaterIndex | null
  indexError: string | null
  indexLoading: boolean
  game: NflPlayRepresentative | null
  gameLoading: boolean
  gameError: string | null
  /** Load (and cache) any scenario/representative pair, e.g. the challenger side. */
  load: (scenarioId: string, position: number) => Promise<NflPlayRepresentative | null>
  cached: (scenarioId: string, position: number) => NflPlayRepresentative | null
}

const key = (scenarioId: string, position: number) => `${scenarioId}#${position}`

export function useNflPlayTheater(
  gameId: string, scenarioId: string, position: number,
): PlayTheaterResource {
  const [index, setIndex] = useState<NflPlayTheaterIndex | null>(null)
  const [indexError, setIndexError] = useState<string | null>(null)
  const [indexLoading, setIndexLoading] = useState(true)
  const [game, setGame] = useState<NflPlayRepresentative | null>(null)
  const [gameLoading, setGameLoading] = useState(false)
  const [gameError, setGameError] = useState<string | null>(null)
  const cache = useRef(new Map<string, NflPlayRepresentative>())

  useEffect(() => {
    cache.current.clear()
    const controller = new AbortController()
    setIndexLoading(true)
    endpoints.nflPlayTheater(gameId, controller.signal)
      .then((result) => { setIndex(result); setIndexError(null) })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        setIndex(null)
        setIndexError(reason instanceof Error ? reason.message : 'Play-level evidence unavailable')
      })
      .finally(() => { if (!controller.signal.aborted) setIndexLoading(false) })
    return () => controller.abort()
  }, [gameId])

  const load = useCallback(async (scenario: string, at: number) => {
    const hit = cache.current.get(key(scenario, at))
    if (hit) return hit
    try {
      const result = await endpoints.nflPlayRepresentative(gameId, scenario, at)
      cache.current.set(key(scenario, at), result)
      return result
    } catch {
      return null
    }
  }, [gameId])

  const cached = useCallback(
    (scenario: string, at: number) => cache.current.get(key(scenario, at)) ?? null, [])

  useEffect(() => {
    if (!index) return
    let cancelled = false
    const hit = cache.current.get(key(scenarioId, position))
    if (hit) { setGame(hit); setGameError(null); setGameLoading(false); return }
    setGameLoading(true)
    endpoints.nflPlayRepresentative(gameId, scenarioId, position)
      .then((result) => {
        if (cancelled) return
        cache.current.set(key(scenarioId, position), result)
        setGame(result)
        setGameError(null)
      })
      .catch((reason: unknown) => {
        if (cancelled) return
        setGame(null)
        setGameError(reason instanceof Error ? reason.message : 'Could not load this simulated game')
      })
      .finally(() => { if (!cancelled) setGameLoading(false) })
    return () => { cancelled = true }
  }, [gameId, scenarioId, position, index])

  return { index, indexError, indexLoading, game, gameLoading, gameError, load, cached }
}
