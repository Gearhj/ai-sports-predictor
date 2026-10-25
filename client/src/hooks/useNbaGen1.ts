/** NBA Gen1 reads: the board and one game. Stored evidence only; no provider call. */
import { useCallback } from 'react'
import { useApiResource } from './useApiResource'
import { cacheKey, STALE_TIME } from '../services/queryCache'
import { apiRequest } from '../services/api'
import type { NbaGen1Board, NbaGen1Game } from '../types/nbaGen1'

export function useNbaGen1Board() {
  const loader = useCallback(async (signal: AbortSignal) => {
    const data = await apiRequest<NbaGen1Board>('/nba/gen1/board', {}, signal)
    return data?.schema_version === 'AISP_NBA_GEN1_BOARD_V1' ? data : null
  }, [])
  return useApiResource<NbaGen1Board | null>(loader, {
    key: cacheKey('nba-gen1-board', {}),
    staleTimeMs: STALE_TIME.history,
  })
}

export function useNbaGen1Game(gameRef?: string) {
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!gameRef) return null
    const data = await apiRequest<NbaGen1Game>(`/nba/gen1/games/${encodeURIComponent(gameRef)}`, {}, signal)
    return data?.schema_version === 'AISP_NBA_GEN1_GAME_V1' ? data : null
  }, [gameRef])
  return useApiResource<NbaGen1Game | null>(loader, {
    key: cacheKey('nba-gen1-game', { gameRef }),
    staleTimeMs: STALE_TIME.history,
  })
}
