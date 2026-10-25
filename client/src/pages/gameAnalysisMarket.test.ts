import { describe, expect, it } from 'vitest'
import { isCurrentMarket } from './GameAnalysisPage'

const now = Date.parse('2026-09-16T16:00:00Z')
const game = { status: 'SCHEDULED', start_time: '2026-09-16T23:00:00Z', last_update: '2026-09-16T15:00:00Z', market_quote_observed_at: '2026-09-16T15:00:00Z' }

describe('isCurrentMarket', () => {
  it('is current only inside four hours and before first pitch', () => {
    expect(isCurrentMarket(game, now)).toBe(true)
    expect(isCurrentMarket({ ...game, market_quote_observed_at: '2026-09-16T11:59:00Z' }, now)).toBe(false)
    expect(isCurrentMarket({ ...game, start_time: '2026-09-16T15:30:00Z' }, now)).toBe(false)
    expect(isCurrentMarket({ ...game, status: 'FINAL' }, now)).toBe(false)
    expect(isCurrentMarket({ ...game, market_quote_observed_at: null, last_update: '' }, now)).toBe(false)
  })
})
