import { describe, expect, it } from 'vitest'
import { marketLine, marketShort, modelLine, type MarketStatus, type ModelStatus } from './pregameStatus'
import { akronMarketStatus, akronModelAfter, akronModelBefore, expiredLease } from '../test/cfbAkronFixture'

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

describe('College Football: coverage and eligibility are two separate lines (Akron @ Minnesota, 2026-09-19)', () => {
  it('no quote row and no active collector: states no coverage, never "waiting" or "stored reference"', () => {
    const line = marketLine(akronMarketStatus)!
    expect(line.label).toBe('No sportsbook coverage')
    expect(line.tone).toBe('neutral')
    expect(line.detail).toBe('CFB price collection is not active (no allocation): no uncommitted SportsGameOdds capacity is left '
      + 'after MLB/NFL; the CFB lane is not switched on; 0 objects spent on it · no quote is stored for this game')
    expect(`${line.label} ${line.detail}`).not.toMatch(/waiting|stored reference|forecast/i)
    expect(marketShort(akronMarketStatus)).toBe('No sportsbook coverage')
  })

  it('an active collector with nothing stored yet is the only "yet"', () => {
    const active: MarketStatus = { ...akronMarketStatus, state: 'NO_QUOTES_YET',
      collection: { ...akronMarketStatus.collection!, active: true, lane_enabled: true, allocation_state: 'CAPACITY_AVAILABLE', objects_allowed: 4 } }
    expect(marketLine(active)).toMatchObject({ label: 'No quotes yet', tone: 'watch' })
    expect(marketLine(active)!.detail).toContain('CFB price collection is active (4 objects allowed)')
    const unknown: MarketStatus = { ...akronMarketStatus, state: 'COLLECTION_STATE_UNKNOWN' }
    expect(marketLine(unknown)!.label).toBe('Sportsbook coverage unknown')
  })

  it('after the checkpoint: the expired lease with its time, no renewal, and the blocked checkpoint', () => {
    const line = modelLine(akronModelAfter, 'CFB', Date.parse('2026-09-19T16:24:00Z'))!
    expect(line.tone).toBe('bad')
    expect(line.label).toBe('Forecast eligibility expired')
    expect(line.detail).toBe('Forecast eligibility expired at 8:20 AM ET; the publisher has not renewed it (newest manifest 8:05 AM ET); '
      + 'the 11:30 AM ET checkpoint was blocked (DISCOVERY_NOT_CURRENT)')
    expect(`${line.label} ${line.detail}`).not.toMatch(/quote|price|research only/i)
  })

  it('before the checkpoint: names what the checkpoint will need, without predicting its outcome', () => {
    const line = modelLine(akronModelBefore, 'CFB')!
    expect(line.detail).toBe('Forecast eligibility expired at 8:20 AM ET; the publisher has not renewed it (newest manifest 8:05 AM ET); '
      + 'the 11:30 AM ET checkpoint needs a current lease')
  })

  it('a renewal after the checkpoint is shown beside the record and never replaces it', () => {
    const renewed: ModelStatus = { ...akronModelAfter, renewed_since_checkpoint: true,
      eligibility_now: { ...expiredLease, state: 'CURRENT', manifest_created_utc: '2026-09-19T15:50:00Z', lease_valid_until: '2026-09-19T16:05:00Z' } }
    const line = modelLine(renewed, 'CFB')!
    expect(line.label).toBe('Forecast eligibility expired')
    expect(line.detail).toContain('expired at 8:20 AM ET')
    expect(line.detail).toContain('renewed after the checkpoint: current until 12:05 PM ET (newest manifest 11:50 AM ET); the recorded checkpoint stands')
    expect(line.detail).toContain('the 11:30 AM ET checkpoint was blocked (DISCOVERY_NOT_CURRENT)')
  })

  it('a current lease reads as eligible, and a forecast the manifest no longer lists is not', () => {
    const current: ModelStatus = { ...akronModelBefore, state: 'ELIGIBILITY_CURRENT',
      eligibility: { ...expiredLease, state: 'CURRENT', lease_valid_until: '2026-09-19T15:35:00Z', manifest_created_utc: '2026-09-19T15:20:00Z' } }
    expect(modelLine(current, 'CFB')).toMatchObject({ tone: 'good', label: 'Forecast eligible' })
    expect(modelLine(current, 'CFB')!.detail).toContain('Publisher lease current until 11:35 AM ET (manifest 11:20 AM ET)')
    const unlisted: ModelStatus = { ...akronModelBefore, state: 'ELIGIBILITY_NOT_CURRENT', eligibility: { state: 'NOT_IN_CURRENT_DISCOVERY' } }
    expect(modelLine(unlisted, 'CFB')!.detail).toContain("Forecast not eligible: the producer's newest manifest does not list this forecast")
  })
})
