import { describe, expect, it } from 'vitest'
import { describeError, domainWithZero, errorDirection, errorTone, signedNumber } from './errorWording'

describe('error wording: predicted minus actual, everywhere', () => {
  it('reads predicted 43.47 vs actual 49 as an UNDERestimate', () => {
    const error = 43.47 - 49
    expect(errorDirection(error)).toBe('under')
    expect(describeError(error)).toBe('underestimate by 5.5')
    expect(describeError(19.69, 2)).toBe('overestimate by 19.69')
    expect(signedNumber(error, 2)).toBe('−5.53')
  })

  it('tones by the size of the miss, never by its sign', () => {
    expect(errorTone(26.6, 'margin', 'NFL')).toBe('large')
    expect(errorTone(-26.6, 'margin', 'NFL')).toBe('large')
    expect(errorTone(0.19, 'margin', 'NFL')).toBe('small')
    expect(errorTone(-5.53, 'total', 'NFL')).toBe('medium')
    expect(errorTone(2.5, 'total', 'MLB')).toBe('medium')
  })

  it('builds chart domains that always reach below zero for a negative margin', () => {
    const [lo, hi] = domainWithZero([5.6, -21, 18.9, 35, 24.6, 14, 43.5, 49], 50)
    expect(lo).toBeLessThan(-21)
    expect(hi).toBeGreaterThan(49)
    const [plo] = domainWithZero([2, 3], 10)
    expect(plo).toBeLessThanOrEqual(0)
  })
})
