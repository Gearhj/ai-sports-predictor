/**
 * The interval on a calibration bucket is a claim about evidence, so it has to
 * be right at the sample sizes this experiment actually has -- which are small.
 */
import { describe, expect, it } from 'vitest'
import { coversDiagonal, wilsonInterval } from './ReliabilityDiagram'

describe('wilsonInterval', () => {
  it('stays inside 0-100 where the normal approximation would not', () => {
    // 2 of 3 games. The normal interval here is 66.7% +/- 53.3pts, which runs
    // past 100 and reports something impossible; Wilson does not.
    const [low, high] = wilsonInterval(66.6667, 3)
    expect(low).toBeGreaterThanOrEqual(0)
    expect(high).toBeLessThanOrEqual(100)
    expect(high).toBeLessThan(100)
  })

  it('never leaves the unit interval even when every game went one way', () => {
    for (const [pct, n] of [[100, 1], [100, 8], [0, 1], [0, 8]] as const) {
      const [low, high] = wilsonInterval(pct, n)
      expect(low).toBeGreaterThanOrEqual(0)
      expect(high).toBeLessThanOrEqual(100)
      expect(low).toBeLessThanOrEqual(high)
    }
  })

  it('matches the published value for a textbook case', () => {
    // Wilson 95% for 50/100 is 40.38% - 59.62%.
    const [low, high] = wilsonInterval(50, 100)
    expect(low).toBeCloseTo(40.38, 1)
    expect(high).toBeCloseTo(59.62, 1)
  })

  it('narrows as evidence accumulates', () => {
    const widths = [5, 25, 100, 500].map((n) => {
      const [low, high] = wilsonInterval(60, n)
      return high - low
    })
    for (let i = 1; i < widths.length; i += 1) {
      expect(widths[i]).toBeLessThan(widths[i - 1])
    }
  })

  it('degenerates safely rather than dividing by zero on an empty bucket', () => {
    expect(wilsonInterval(42, 0)).toEqual([42, 42])
    expect(wilsonInterval(42, Number.NaN)).toEqual([42, 42])
  })
})

describe('coversDiagonal', () => {
  it('calls a three-game bucket consistent with its own forecast', () => {
    // This is the live bucket that reads as a 27-point miss: 39.6% predicted,
    // 66.7% observed. On three games it is not evidence of anything, and the
    // chart has to say so rather than let the distance speak.
    expect(coversDiagonal({ predicted: 39.59, observed: 66.67, count: 3 })).toBe(true)
  })

  it('calls the same gap a real miss once the sample is large', () => {
    expect(coversDiagonal({ predicted: 39.59, observed: 66.67, count: 400 })).toBe(false)
  })
})
