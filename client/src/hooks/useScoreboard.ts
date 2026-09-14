/**
 * One scoreboard read per page, shared by every card on it.
 *
 * NO TAB EVER POLLS A PROVIDER
 * ----------------------------
 * This reads AISP's own cache. Collection is the supervised scheduler's job and
 * happens once for everybody; ten open tabs cost the provider nothing extra and
 * a refresh button costs it nothing at all. That is why the manual cooldown
 * here is short: it exists to stop a component re-rendering itself into a loop
 * against AISP, not to ration a provider.
 *
 * ONE REQUEST FOR A WHOLE PAGE
 * ----------------------------
 * Cards ask for scores by game id and the hook batches them behind the existing
 * query cache, keyed on the sorted id set. Eight cards on Daily Decisions are
 * one request, and two components asking for overlapping games share the answer
 * rather than racing for it.
 *
 * IT SPEEDS UP ONLY WHEN SOMETHING IS LIVE
 * ----------------------------------------
 * The payload states its own auto-refresh interval, which the server sets from
 * whether anything is actually in progress. A page of finished games settles to
 * a slow tick by itself, and nothing in the browser has to guess.
 */
import { useCallback, useMemo, useRef } from 'react'
import { useApiResource } from './useApiResource'
import { cacheKey } from '../services/queryCache'
import { apiRequest } from '../services/api'
import type { ScoreboardCoverage, ScoreboardPayload } from '../types/scoreboard'

/** Shared across every component in the tab: one cooldown, one budget. */
const lastManualRefresh = { at: 0 }

function isScoreboardPayload(value: unknown): value is ScoreboardPayload {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<ScoreboardPayload>
  return candidate.schema_version === 'AISP_GAME_SCOREBOARD_V1'
    && typeof candidate.games === 'object' && candidate.games !== null
}

export function useScoreboard({
  gameIds, day, league, enabled = true,
}: {
  gameIds?: string[]
  day?: string
  league?: string
  enabled?: boolean
}) {
  // Sorted and de-duplicated so that two components asking for the same games
  // in a different order share one cache entry instead of opening two.
  const ids = useMemo(
    () => Array.from(new Set(gameIds ?? [])).sort(),
    [gameIds],
  )
  const identity = ids.join(',')

  const loader = useCallback(async (signal: AbortSignal) => {
    if (!enabled) return null
    const query = new URLSearchParams()
    if (identity) query.set('game_ids', identity)
    if (day) query.set('date', day)
    if (league) query.set('league', league)
    return apiRequest<ScoreboardPayload>(`/scoreboard?${query.toString()}`, {}, signal)
  }, [enabled, identity, day, league])

  const resource = useApiResource<ScoreboardPayload | null>(loader, {
    key: cacheKey('scoreboard', { ids: identity, day, league, enabled }),
    // Short, because the underlying collection is two minutes and a reader
    // watching a game should not be looking at a four-minute-old cache entry.
    staleTimeMs: 20_000,
    refreshIntervalMs: 30_000,
  })

  // Shape-checked, not just null-checked. This hook renders inside pages whose
  // other content is fine; a scoreboard payload that arrives malformed must
  // cost the scores and nothing else.
  const payload = isScoreboardPayload(resource.data) ? resource.data : null
  const cooldownSeconds = payload?.refresh?.manual_cooldown_seconds ?? 15
  const cooldownRef = useRef(cooldownSeconds)
  cooldownRef.current = cooldownSeconds

  const refresh = useCallback(() => {
    const now = Date.now()
    if (now - lastManualRefresh.at < cooldownRef.current * 1000) return false
    lastManualRefresh.at = now
    resource.refresh()
    return true
  }, [resource])

  const canRefresh = useCallback(
    () => Date.now() - lastManualRefresh.at >= cooldownRef.current * 1000,
    [],
  )

  return {
    ...resource,
    /** Keyed by game id. Missing means no score has been collected for it. */
    games: payload?.games ?? {},
    live: payload?.live ?? 0,
    refresh,
    canRefresh,
    cooldownSeconds,
  }
}

/** What each sport's feed does and does not supply. Effectively static. */
export function useScoreboardCoverage(enabled = true) {
  const loader = useCallback(async (signal: AbortSignal) => {
    if (!enabled) return null
    return apiRequest<ScoreboardCoverage>('/scoreboard/coverage', {}, signal)
  }, [enabled])
  return useApiResource<ScoreboardCoverage | null>(loader, {
    key: cacheKey('scoreboard/coverage', { enabled }),
    staleTimeMs: 10 * 60_000,
  })
}
