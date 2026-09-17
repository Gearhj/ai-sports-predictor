import { describe, expect, it } from 'vitest'
import { passedLabel } from './CommandDecisions'

describe('passedLabel', () => {
  it('names the recorded reason for a pass', () => {
    expect(passedLabel('V2 TIER_D_INELIGIBLE: Latest quote is stale.')).toBe('Passed · price not current')
    expect(passedLabel('V2 TIER_B_PROBABLE_STABLE: MONEYLINE selection PIT ML does not clear the adjusted thresholds — Adjusted edge +0.9 pts')).toBe('Passed · edge below policy')
    expect(passedLabel('V2 TIER_D_INELIGIBLE: starter unconfirmed')).toBe('Passed · not eligible')
    expect(passedLabel(null)).toBe('Passed · edge below policy')
  })
})
