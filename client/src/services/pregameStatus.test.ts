import { describe, expect, it } from 'vitest'
import { marketLine, marketShort, modelLine, type MarketStatus, type ModelStatus } from './pregameStatus'

const market: MarketStatus = {
  state: 'ELIGIBLE_THROUGH_WINDOW', rule: '', books_two_sided: 9, books_fresh_now: 9, books_fresh_through_window: 9,
  oldest_leg_observed_at: '2026-09-18T18:27:04Z', newest_leg_observed_at: '2026-09-18T18:32:40Z', oldest_leg_age_minutes: 152,
  fresh_until: '2026-09-18T22:32:40Z', earliest_expiry: '2026-09-18T22:27:04Z',
  window_opens_at: '2026-09-18T22:10:00Z', window_closes_at: '2026-09-18T22:25:00Z',
}
const schedule = {
  input_checks_from: '2026-09-18T21:40:00Z', publication_target: '2026-09-18T21:55:00Z',
  window_opens_at: '2026-09-18T22:10:00Z', window_closes_at: '2026-09-18T22:25:00Z', target_is_guaranteed: false as const,
}
const producer = {
  receipt_sha256: 'b08c', receipt_generated_at: '2026-09-18T20:17:58Z', receipt_age_minutes: 41, source: 'receipt',
  game_status: 'SKIPPED', reason: 'CHECKPOINT_NOT_DUE', reason_text: "the producer's next input check for this game was not yet due",
  blockers: [], blocker_text: [],
}

describe('pregame status', () => {
  it('states a fresh two-sided market on its own terms, with quote age and expiry', () => {
    const line = marketLine(market)!
    expect(line.label).toBe('Market ready')
    expect(line.detail).toContain('9 books')
    expect(line.detail).toContain('6:10 PM–6:25 PM ET decision window')
    expect(line.detail).toContain('oldest leg 2:27 PM (152 min old)')
    expect(line.detail).toContain('usable until 6:32 PM')
    expect(marketShort(market)).toBe('9 books current · usable until 6:32 PM')
  })

  it('labels the T-45 publication time as a target, with the input checks and window', () => {
    const line = modelLine({ state: 'SCHEDULED', schedule, producer }, 'V18', Date.parse('2026-09-18T21:00:00Z'))!
    expect(line.label).toBe('V18 due by 5:55 PM (target)')
    expect(line.detail).toContain('input checks from 5:40 PM')
    expect(line.detail).toContain('publication target 5:55 PM (a target, not a guaranteed arrival)')
    expect(line.detail).toContain('decision window 6:10 PM–6:25 PM ET')
    expect(line.detail).toContain("Producer receipt 4:17 PM (41 min ago): the producer's next input check")
  })

  it('says so when the producer receipt itself is stale', () => {
    const line = modelLine({ state: 'OVERDUE', schedule, producer: { ...producer, receipt_age_minutes: 98 } }, 'V18', Date.parse('2026-09-18T21:56:00Z'))!
    expect(line.detail).toContain('No producer receipt since 4:17 PM (98 min); its last reason for this game:')
  })

  it('names the producer reason once the target passes', () => {
    const status: ModelStatus = { state: 'OVERDUE', schedule, producer: { ...producer, blocker_text: ["the away team's confirmed batting order is not available"] } }
    const line = modelLine(status, 'V18', Date.parse('2026-09-18T21:56:00Z'))!
    expect(line.tone).toBe('bad')
    expect(line.label).toBe('V18 overdue')
    expect(line.detail).toContain('Publication target 5:55 PM passed')
    expect(line.detail).toContain("the away team's confirmed batting order is not available")
  })

  it('never says "quote" in the model line or "forecast" in the market line', () => {
    expect(marketLine(market)!.detail).not.toMatch(/forecast/i)
    expect(modelLine({ state: 'SCHEDULED', schedule, producer }, 'V18', Date.parse('2026-09-18T21:00:00Z'))!.label).not.toMatch(/quote/i)
  })
})
