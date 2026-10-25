import { describe, expect, it } from 'vitest'
import { fitShare, liveHome } from './nhlModel'

describe('NHL live win estimate', () => {
  it('starts at the pregame price', () => {
    for (const prior of [0.35, 0.5, 0.62]) expect(liveHome(fitShare(prior), 0, 0, 1, false)).toBeCloseTo(prior, 3)
  })
  it('a late lead is nearly safe and a final is certain', () => {
    const s = fitShare(0.5)
    expect(liveHome(s, 3540, 1, 3, false)).toBeGreaterThan(0.9)
    expect(liveHome(s, 3540, -2, 3, false)).toBeLessThan(0.01)
    expect(liveHome(s, 3600, 1, 3, true)).toBe(1)
  })
  it('a tie with no time left goes to overtime at the scoring share', () => {
    const s = fitShare(0.6)
    expect(liveHome(s, 3600, 0, 4, false)).toBeCloseTo(s, 6)
    expect(liveHome(s, 3900, 0, 5, false)).toBe(0.5)
  })
})
