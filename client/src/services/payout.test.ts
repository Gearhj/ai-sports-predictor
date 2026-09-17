import { describe, expect, it } from 'vitest'
import { decimalFromAmerican, dollarPayout, expectationLine, expectationUnavailable, payoutLine, profitLine, stakeReturn } from './payout'

describe('payout arithmetic', () => {
  it('turns American prices into $1 payouts', () => {
    expect(dollarPayout(decimalFromAmerican(-120))).toEqual({ total: 1.83, profit: 0.83 })
    expect(dollarPayout(decimalFromAmerican(140))).toEqual({ total: 2.4, profit: 1.4 })
    expect(payoutLine(decimalFromAmerican(-120))).toBe('Bet $1 → $1.83 total if it wins')
    expect(profitLine(decimalFromAmerican(140))).toBe('$1.40 profit')
    expect(dollarPayout(decimalFromAmerican(100))).toEqual({ total: 2, profit: 1 })
    expect(decimalFromAmerican(50)).toBeNull()
    expect(payoutLine(null)).toBeNull()
    expect(stakeReturn(25, decimalFromAmerican(-137)!)).toEqual({ total: 43.25, profit: 18.25 })
  })

  it('states expectation as profit or loss per $1', () => {
    // Push-aware: pWin x (d - 1) - pLoss, with a push contributing nothing.
    const d = decimalFromAmerican(-110)!
    expect(expectationLine(0.55 * (d - 1) - 0.4)).toBe('AISP expected profit: +$0.10 per $1')
    expect(expectationLine(-0.0345)).toBe('AISP expected loss: −$0.03 per $1')
    expect(expectationLine(null)).toBeNull()
  })

  it('explains a missing expectation instead of inventing one', () => {
    expect(expectationUnavailable({ model: null, edge_reason: 'NO_VERIFIED_POPULATION' })).toBe('AISP expectation pending — forecast not available')
    expect(expectationUnavailable({ model: null, edge_reason: null })).toBe('AISP expectation pending — forecast not available')
    expect(expectationUnavailable(null)).toBe('AISP expectation pending — forecast not available')
  })
})
