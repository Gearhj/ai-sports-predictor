/**
 * Moneyline probability definitions shared by Markets & Crowd and Home.
 *
 * All of these read the market-coverage read model: a book's no-vig home
 * probability is 1/d_home ÷ (1/d_home + 1/d_away) from one same-book,
 * same-provider two-sided pair; the consensus is their mean; the model point is
 * the controlling forecast's two-way home probability. Nothing here prices,
 * refreshes or fills a missing value.
 */
import * as d3 from 'd3'
import type { CoverageGame } from './commandCenter'

export type Side = 'HOME' | 'AWAY'

/** The range of individual books' no-vig home probabilities (moneyline pairs only). */
export function bookRange(game: CoverageGame): [number, number] | null {
  const values = game.quotes.filter((q) => q.market === 'MONEYLINE' && q.no_vig_home != null).map((q) => q.no_vig_home as number)
  return values.length ? [d3.min(values)!, d3.max(values)!] : null
}

/** Model two-way home probability minus the no-vig consensus. */
export const gapOf = (g: CoverageGame): number | null => (g.model?.home_two_way != null && g.consensus_no_vig_home != null
  ? g.model.home_two_way - g.consensus_no_vig_home : null)

/** A home-oriented probability re-expressed for one side. */
export const forSide = (homeProbability: number | null | undefined, side: Side): number | null =>
  homeProbability == null ? null : side === 'HOME' ? homeProbability : 1 - homeProbability

/** A home-oriented range re-expressed for one side (bounds swap for the away side). */
export const rangeForSide = (range: [number, number] | null, side: Side): [number, number] | null =>
  range == null ? null : side === 'HOME' ? range : [1 - range[1], 1 - range[0]]
