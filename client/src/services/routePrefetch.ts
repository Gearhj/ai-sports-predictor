/**
 * Warm a destination before the user commits to it.
 *
 * Hovering, focusing or touching a link is a strong signal and a weak promise:
 * it costs nothing if the user does not follow through, and it removes most of
 * the wait if they do. Two things are warmed — the route's JavaScript chunk
 * (every page is `lazy()`-loaded, so a cold navigation pays for a download
 * before it can even start fetching) and its first read.
 *
 * Everything warmed here is a GET against stored AISP data, the same request
 * the page itself would make a moment later. Prefetching cannot acquire a new
 * quote, spend an odds credit, record a decision or run a model: those happen
 * in backend jobs, and nothing on this path posts.
 */
import { apiRequest, endpoints } from './api'
import { STALE_TIME, cacheKey, prefetchQuery } from './queryCache'
import type { ExperimentFunnelData } from '../types/domain'

type Warm = () => void

interface RouteWarmer {
  /** The route chunk, so hovering also starts the code download. */
  chunk: () => Promise<unknown>
  /** The page's first read, if warming it is cheap and unambiguous. */
  data?: Warm
}

/**
 * Only the destinations a reader actually moves between are listed. Warming
 * everything would turn one hover into a slate-wide fan-out, which is how a
 * prefetch stops being free.
 */
const ROUTES: Record<string, RouteWarmer> = {
  '/': {
    chunk: () => import('../pages/HomePage'),
  },
  '/decisions': {
    chunk: () => import('../pages/MultiSportTodayPage'),
    data: () => prefetchQuery(
      'today/all-sports',
      async (signal) => {
        const warnings: string[] = []
        const [today, funnel] = await Promise.all([
          endpoints.today(signal),
          apiRequest<ExperimentFunnelData>('/experiment/funnel', {}, signal).catch(() => {
            warnings.push('Experiment funnel unavailable')
            return null
          }),
        ])
        return { data_mode: 'live' as const, today, funnel, warnings }
      },
      STALE_TIME.slate,
    ),
  },
  '/games': { chunk: () => import('../pages/UnifiedGamesPage') },
  '/markets': { chunk: () => import('../pages/UnifiedMarketsPage') },
  '/labs': { chunk: () => import('../pages/LabsPage') },
  '/performance': { chunk: () => import('../pages/UnifiedResearchPage') },
  '/research/history': { chunk: () => import('../pages/UnifiedResearchPage') },
  '/shadow-board': { chunk: () => import('../pages/ShadowBoardPage') },
  '/methodology': { chunk: () => import('../pages/MethodologyPage') },
  '/experiment': {
    chunk: () => import('../pages/BankrollPage'),
    data: () => prefetchQuery(
      cacheKey('bankroll', { experimentKey: 'v2' }),
      (signal) => endpoints.bankroll(signal, 'v2'),
      STALE_TIME.operational,
    ),
  },
  '/mlb': {
    chunk: () => import('../pages/TodayPage'),
    data: () => prefetchQuery('dashboard/today', endpoints.dashboard, STALE_TIME.slate),
  },
}

/** Chunks are warmed once; a second hover must not re-enter the import graph. */
const warmedChunks = new Set<string>()

/**
 * Warm the destination for `pathname`. Safe to call on every hover: the cache
 * refuses work that is already fresh or already running, and chunk imports are
 * remembered.
 */
export function prefetchRoute(pathname: string): void {
  const route = normalize(pathname)
  const warmer = ROUTES[route] ?? dynamicWarmer(route)
  if (!warmer) return
  if (!warmedChunks.has(route)) {
    warmedChunks.add(route)
    void warmer.chunk().catch(() => {
      // A failed warm is not a failure the reader should ever see; navigating
      // will retry the import in the foreground with its own error handling.
      warmedChunks.delete(route)
    })
  }
  warmer.data?.()
}

function normalize(pathname: string): string {
  const withoutQuery = pathname.split('?')[0].split('#')[0]
  if (withoutQuery.length > 1 && withoutQuery.endsWith('/')) return withoutQuery.slice(0, -1)
  return withoutQuery || '/'
}

/** Routes with an id in them: one game's analysis, one game's simulation. */
function dynamicWarmer(route: string): RouteWarmer | null {
  const analysis = /^\/games\/([^/]+)$/.exec(route)
  if (analysis) {
    const gameId = decodeURIComponent(analysis[1])
    return {
      chunk: () => import('../pages/GameAnalysisPage'),
      data: () => prefetchQuery(
        cacheKey('game/analysis', { gameId }),
        (signal) => endpoints.game(gameId, signal),
        STALE_TIME.game,
      ),
    }
  }
  const simulation = /^\/games\/([^/]+)\/simulation$/.exec(route)
  if (simulation) {
    const gameId = decodeURIComponent(simulation[1])
    return {
      chunk: () => import('../pages/GameSimulationPage'),
      data: () => prefetchQuery(
        cacheKey('game/simulation', { gameId }),
        (signal) => endpoints.gameSimulation(gameId, signal),
        STALE_TIME.game,
      ),
    }
  }
  return null
}
