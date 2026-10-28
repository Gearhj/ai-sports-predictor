import { describe, expect, it } from 'vitest'
import { defaults, evOf, fairAmerican, invNorm, poissonWin, quantiles, simulate, splitTotal, tallyOf } from './lab'

describe('experiment lab maths', () => {
  it('inverse normal is right at known points', () => {
    expect(invNorm(0.5)).toBeCloseTo(0, 6)
    expect(invNorm(0.8413447)).toBeCloseTo(1, 4)
    expect(invNorm(0.0227501)).toBeCloseTo(-2, 4)
  })
  it('defaults reproduce AISP’s probability in both game models', () => {
    for (const [sport, p] of [['NHL', 0.53], ['MLB', 0.6], ['NBA', 0.74], ['NFL', 0.42]] as const) {
      const d = defaults(sport, p)
      expect(simulate(d).pHome).toBeCloseTo(p, 1.5)
    }
    const s = splitTotal(6.1, 0.53)
    expect(s.home + s.away).toBeCloseTo(6.1, 6)
    expect(poissonWin(s.home, s.away)).toBeCloseTo(0.53, 3)
  })
  it('a key player out moves the number the right way', () => {
    const d = defaults('NBA', 0.6)
    expect(simulate({ ...d, homeOut: true }).pHome).toBeLessThan(simulate(d).pHome - 0.05)
    expect(simulate({ ...d, awayOut: true }).pHome).toBeGreaterThan(simulate(d).pHome + 0.05)
  })
  it('prices: fair odds and expected value', () => {
    expect(fairAmerican(0.6)).toBe(-150)
    expect(fairAmerican(0.4)).toBe(150)
    expect(evOf(0.5, 110)).toBeCloseTo(0.05, 6)
    expect(evOf(0.6, -150)).toBeCloseTo(0, 6)
  })
})

describe('experiment lab score grid', () => {
  it('margin sports produce score pairs whose difference is the margin and whose total sits near the input', () => {
    const d = defaults('NBA', 0.6)
    const s = simulate(d)
    let tot = 0
    for (let i = 0; i < s.margins.length; i++) { expect(s.homeScores[i] - s.awayScores[i]).toBe(s.margins[i]); tot += s.homeScores[i] + s.awayScores[i] }
    expect(tot / s.margins.length).toBeGreaterThan(215); expect(tot / s.margins.length).toBeLessThan(237)
    const cells = tallyOf(s)
    expect(cells.reduce((a, c) => a + c.count, 0)).toBe(10_000)
  })
  it('quantiles are ordered', () => {
    const q = quantiles(simulate(defaults('NFL', 0.5)).margins)
    expect(q[0]).toBeLessThan(q[1]); expect(q[1]).toBeLessThanOrEqual(q[2]); expect(q[2]).toBeLessThanOrEqual(q[3]); expect(q[3]).toBeLessThan(q[4])
    expect(Math.abs(q[2])).toBeLessThanOrEqual(1)
  })
})
