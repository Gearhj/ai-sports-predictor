import { describe, expect, it } from 'vitest'
import { binsOf } from './GaltonBoard'

describe('galton board bins', () => {
  it('shares sum to 1 and certain legs land in the top bin with the full payout', () => {
    const b = binsOf([{ p: 1, stake: 10, american: 100 }, { p: 1, stake: 10, american: -200 }])
    expect(b.map((x) => x.share)).toEqual([0, 0, 1])
    expect(b[2].avg).toBeCloseTo(10 + 5)
  })
  it('a coin-flip pair splits about 25 / 50 / 25 with the right sign per bin', () => {
    const b = binsOf([{ p: 0.5, stake: 10, american: 100 }, { p: 0.5, stake: 10, american: 100 }])
    expect(b.reduce((t, x) => t + x.share, 0)).toBeCloseTo(1)
    expect(b[1].share).toBeGreaterThan(0.47); expect(b[1].share).toBeLessThan(0.53)
    expect(b[0].avg).toBe(-20); expect(b[1].avg).toBe(0); expect(b[2].avg).toBe(20)
  })
})
