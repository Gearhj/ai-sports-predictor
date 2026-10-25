/**
 * Ordering for the Games & Forecasts slate.
 *
 * KICKOFF   earliest start first (the default).
 * FORECAST  games holding a forecast first, then games with a current price.
 * GAP       largest current model/market difference first. A difference only
 *           counts when both sides are current: the model's two-way home
 *           probability against the CURRENT no-vig consensus. Games without a
 *           valid current comparison follow in kickoff order.
 */
import type { SlateGame } from './broadcastSlate'
import type { CoverageGame } from './commandCenter'

export type SlateSort = 'KICKOFF' | 'FORECAST' | 'GAP'

export const SLATE_SORT_LABEL: Record<SlateSort, string> = {
  KICKOFF: 'Kickoff time',
  FORECAST: 'Forecast status',
  GAP: 'Largest model/market gap',
}

export const hasForecast = (game: SlateGame) => game.homeWin != null

export function hasCurrentPrice(game: SlateGame, evidence: CoverageGame | null | undefined): boolean {
  return evidence ? evidence.price_state === 'CURRENT_PRICE' : game.priceStatus === 'CURRENT'
}

/** |model − current no-vig| in probability points (0..1), or null when not validly comparable. */
export function currentGap(game: SlateGame, evidence: CoverageGame | null | undefined): number | null {
  if (evidence) {
    const model = evidence.model?.home_two_way
    const market = evidence.consensus_no_vig_home
    if (!evidence.consensus_is_current || model == null || market == null) return null
    return Math.abs(model - market)
  }
  if (game.priceStatus !== 'CURRENT' || game.edgePoints == null) return null
  return Math.abs(game.edgePoints) / 100
}

const startOf = (game: SlateGame) => (game.startsAt ? Date.parse(game.startsAt) : Number.MAX_SAFE_INTEGER)

export function sortSlate(
  games: SlateGame[], mode: SlateSort, evidenceFor: (game: SlateGame) => CoverageGame | null | undefined,
): SlateGame[] {
  const byStart = (a: SlateGame, b: SlateGame) => startOf(a) - startOf(b) || a.key.localeCompare(b.key)
  const rows = [...games]
  if (mode === 'FORECAST') {
    const rank = (g: SlateGame) => (hasForecast(g) ? 2 : 0) + (hasCurrentPrice(g, evidenceFor(g)) ? 1 : 0)
    return rows.sort((a, b) => rank(b) - rank(a) || byStart(a, b))
  }
  if (mode === 'GAP') {
    return rows.sort((a, b) => {
      const ga = currentGap(a, evidenceFor(a))
      const gb = currentGap(b, evidenceFor(b))
      if (ga == null && gb == null) return byStart(a, b)
      if (ga == null) return 1
      if (gb == null) return -1
      return gb - ga || byStart(a, b)
    })
  }
  return rows.sort(byStart)
}
