import { describe, expect, it } from 'vitest'
import { PROSPECTS, blend, draftOrder, drillsOf, normalised, pairedOf } from './combine'
import type { GalaxyGame } from '../galaxy/galaxy'

const g = (p: number, m: number, out: string): GalaxyGame => ({ sport: 'NFL', p_home_two_way: p, actual_outcome: out, decision: { market_p_home: m } })

describe('the combine', () => {
  it('blend 0 is the market and blend 1 is the model', () => {
    expect(blend(0)(0.7, 0.6)).toBeCloseTo(0.6, 6)
    expect(blend(1)(0.7, 0.6)).toBeCloseTo(0.7, 6)
    const half = blend(0.5)(0.7, 0.6)
    expect(half).toBeGreaterThan(0.6); expect(half).toBeLessThan(0.7)
  })
  it('pairs only priced, decided games and scores the drills', () => {
    const rows = pairedOf([g(0.8, 0.6, 'HOME'), g(0.8, 0.6, 'AWAY'), g(0.6, 0.5, 'TIE'), { sport: 'NFL', p_home_two_way: 0.6, actual_outcome: 'HOME', decision: null }])
    expect(rows).toHaveLength(2)
    const model = drillsOf(rows, PROSPECTS[0]), market = drillsOf(rows, PROSPECTS[1])
    expect(model.hit).toBeCloseTo(0.5)
    expect(model.honesty).toBeCloseTo(0.3)
    expect(market.honesty).toBeCloseTo(0.1)
    expect(market.logLoss).toBeLessThan(model.logLoss)
    const order = draftOrder([{ pro: PROSPECTS[0], d: model }, { pro: PROSPECTS[1], d: market }])
    expect(order[0].pro.key).toBe('market')
    const n = normalised([{ pro: PROSPECTS[0], d: model }, { pro: PROSPECTS[1], d: market }])
    expect(n.market.logLoss).toBe(1); expect(n.model.logLoss).toBe(0)
  })
})
