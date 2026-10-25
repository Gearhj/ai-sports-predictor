/**
 * College Football quotes: three clocks never collapsed into one, comparisons
 * only against the joint channel, and a checkpoint's NO_BET / NO_PRICE / BLOCKED
 * meaning stated as three different results.
 */
import { describe, expect, it } from 'vitest'
import {
  CHECKPOINT_LABEL, checkpointLabel, clocksLine, comparisonText, freshnessText, quoteText,
} from './cfbMarket'
import { fixtureClocks, fixtureMarket } from '../test/cfbQuoteFixture'

describe('clocks', () => {
  it('names the quote clock (older leg, each side), the fetch clock and the receipt clock separately', () => {
    const line = clocksLine(fixtureClocks)!
    expect(line).toContain('quote 3:10 PM ET (older leg; home 3:10 PM, away 3:13 PM) · 25 min old')
    expect(line).toContain('fetched 3:20 PM ET')
    expect(line).toContain('received 3:20 PM ET')
  })

  it('never invents a receipt time it does not have', () => {
    expect(clocksLine({ ...fixtureClocks, received_at: null })).toContain('receipt time not recorded')
  })

  it('states the 240-minute usability of the older leg', () => {
    expect(freshnessText(fixtureClocks)).toBe('usable until 7:10 PM ET (older leg ≤ 240 min)')
    expect(freshnessText({ ...fixtureClocks, fresh: false })).toContain('stored reference only')
  })
})

describe('quotes and comparisons', () => {
  it('shows what was offered, line signs per side', () => {
    const [ml, spread, total] = fixtureMarket.quotes
    expect(quoteText(ml, 'Home U', 'Away U')).toBe('Home U -150 / Away U +130')
    expect(quoteText(spread, 'Home U', 'Away U')).toBe('Home U −3.5 -110 / Away U +3.5 -110')
    expect(quoteText(total, 'Home U', 'Away U')).toBe('Over 51.5 -105 / Under 51.5 -115')
  })

  it('compares the moneyline with the joint channel and the same book no-vig', () => {
    const text = comparisonText(fixtureMarket.comparisons[0], 'Home U', 'Away U')
    expect(text).toBe('Joint model Home U 62.0% vs DraftKings (SportsGameOdds, research) no-vig 58.0% (+4.0 pts); Away U 38.0% vs 42.0% (−4.0 pts)')
    expect(text).not.toMatch(/ridge|elo/i)
  })

  it('compares a spread with the joint draws, pushes excluded', () => {
    expect(comparisonText(fixtureMarket.comparisons[1], 'Home U', 'Away U'))
      .toBe('Joint draws Home U covers 51.0%, Away U covers 49.0% · pushes excluded 51.0% vs no-vig 50.0% (+1.0 pts)')
  })

  it('says why a market is not compared rather than borrowing another channel', () => {
    expect(comparisonText(fixtureMarket.comparisons[2], 'H', 'A'))
      .toBe('Not compared: the forecast carries no joint score distribution for this market')
  })
})

describe('checkpoint meaning', () => {
  it('keeps a priced no-bet, a declined no-price and a block apart', () => {
    expect(checkpointLabel({ class: 'NO_BET', reason: 'NO_QUALIFYING_EDGE', evaluated: true, priced: true, text: '' }))
      .toBe(CHECKPOINT_LABEL.NO_BET)
    expect(checkpointLabel({ class: 'NO_PRICE', reason: 'NO_TWO_SIDED_QUOTE', evaluated: true, priced: false, text: '' }))
      .toBe('Declined · no same-book two-sided moneyline was stored')
    expect(checkpointLabel({ class: 'NO_PRICE', reason: 'QUOTE_TOO_STALE', evaluated: true, priced: false, text: '' }))
      .toContain('more than 240 minutes old')
    expect(checkpointLabel({ class: 'BLOCKED', reason: 'DISCOVERY_NOT_CURRENT', evaluated: false, priced: false, text: '' }))
      .toBe('Blocked · not evaluated')
    // A budget decline (the shared $100 had no room) is its own result, never a no-bet.
    expect(checkpointLabel({ class: 'NO_ROOM', reason: 'SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS', evaluated: true, priced: true, text: '' }))
      .toBe('Declined · the rest of the shared $100 was held for MLB/NHL games')
    expect(checkpointLabel({ class: 'NO_ROOM', reason: 'SHARED_ALLOWANCE_EXHAUSTED', evaluated: true, priced: true, text: '' }))
      .toBe('Declined · the shared $100 for the day was fully used')
    expect(checkpointLabel({ class: 'NO_ROOM', reason: 'NO_QUALIFYING_EDGE', evaluated: true, priced: true, text: '' }))
      .toBe(CHECKPOINT_LABEL.NO_ROOM)
    expect(new Set(Object.values(CHECKPOINT_LABEL)).size).toBe(Object.keys(CHECKPOINT_LABEL).length)
  })
})
