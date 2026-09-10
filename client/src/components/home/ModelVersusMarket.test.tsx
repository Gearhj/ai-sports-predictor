/**
 * A disagreement panel has two ways to mislead, and both are cheap to make.
 *
 * It can invent a comparison from a row that has only one of the two prices,
 * and it can double-count a two-sided market by listing both sides of the same
 * disagreement. Neither would look wrong on screen.
 */
import { describe, expect, it } from 'vitest'
import { disagreements, impliedProbability } from './ModelVersusMarket'
import type { MarketBoardRow } from '../../types/domain'

function row(over: Partial<MarketBoardRow>): MarketBoardRow {
  return {
    id: 'r1', game_id: 'g1', matchup: 'CLE @ BAL', start_time: '2026-09-09T18:35:00-04:00',
    market_type: 'Moneyline', selection: 'Baltimore Orioles', line: null,
    asp_fair: -108, consensus: 107, edge: 3.65, books: [], observed_at: null,
    ...over,
  } as MarketBoardRow
}

describe('impliedProbability', () => {
  it('converts both signs the standard way', () => {
    expect(impliedProbability(-110)).toBeCloseTo(0.5238, 4)
    expect(impliedProbability(+110)).toBeCloseTo(0.4762, 4)
    expect(impliedProbability(+100)).toBeCloseTo(0.5, 4)
    expect(impliedProbability(-100)).toBeCloseTo(0.5, 4)
  })

  it('includes the bookmaker margin, so a two-sided market sums above one', () => {
    // -110 both sides is the canonical 4.76% hold; the panel's note says so
    // rather than presenting the gap as a clean edge.
    const both = impliedProbability(-110) + impliedProbability(-110)
    expect(both).toBeGreaterThan(1)
    expect(both).toBeCloseTo(1.0476, 3)
  })
})

describe('disagreements', () => {
  it('drops a row with no model price', () => {
    // `asp_fair` is the nullable half of the pair in the API contract
    // (`asp_fair: float | None`, `consensus: float`), and a row without it is
    // not a small disagreement — it is no comparison at all.
    expect(disagreements([row({ asp_fair: null })])).toHaveLength(0)
  })

  it('drops a row whose market price goes missing anyway', () => {
    // The contract says `consensus` is always present. The guard exists so that
    // a future nullable market price degrades to "no comparison" rather than to
    // a disagreement measured against NaN.
    const missing = { ...row({}), consensus: null } as unknown as MarketBoardRow
    expect(disagreements([missing])).toHaveLength(0)
  })

  it('keeps one side of a two-sided market, not both', () => {
    // The real board publishes the mirror of every moneyline. Listing both
    // would show the same disagreement twice under two names.
    const pair = [
      row({ id: 'a', selection: 'Baltimore Orioles', asp_fair: -108, consensus: 107 }),
      row({ id: 'b', selection: 'Cleveland Guardians', asp_fair: 108, consensus: -107 }),
    ]
    const found = disagreements(pair)
    expect(found).toHaveLength(1)
    expect(found[0].selection).toBe('Baltimore Orioles')
  })

  it('measures the gap in probability points, model minus market', () => {
    const [found] = disagreements([row({ asp_fair: -108, consensus: 107 })])
    expect(found.modelProbability).toBeCloseTo(impliedProbability(-108), 6)
    expect(found.marketProbability).toBeCloseTo(impliedProbability(107), 6)
    expect(found.gap).toBeCloseTo(found.modelProbability - found.marketProbability, 6)
    expect(found.gap).toBeGreaterThan(0)
  })

  it('orders by the size of the disagreement', () => {
    const found = disagreements([
      row({ id: 'small', asp_fair: -105, consensus: -100 }),
      row({ id: 'big', asp_fair: -200, consensus: +120 }),
    ])
    expect(found.map((entry) => entry.id)).toEqual(['big', 'small'])
  })
})
