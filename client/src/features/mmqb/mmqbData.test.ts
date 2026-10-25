import { describe, expect, it } from 'vitest'
import { fixText, gapBand } from './mmqbData'

describe('Monday Morning QB helpers', () => {
  it('repairs UTF-8 text that arrived read as Windows-1252, and leaves clean text alone', () => {
    expect(fixText('The largest modelâ€“market disagreements')).toBe('The largest model–market disagreements')
    expect(fixText('Favourite (market no-vig â‰¥ 50%)')).toBe('Favourite (market no-vig ≥ 50%)')
    expect(fixText('model–market, already clean')).toBe('model–market, already clean')
    expect(fixText(null)).toBe('')
  })

  it('bands the model-market gap the way the record-only rule reads it', () => {
    expect(gapBand(10.4)).toBe('Gap over 10 pts')
    expect(gapBand(10)).toBe('Gap 5–10 pts')
    expect(gapBand(-3)).toBe('Gap under 5 pts')
    expect(gapBand(null)).toBe('Unknown gap')
  })
})
