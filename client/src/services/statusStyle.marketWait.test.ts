import { describe, expect, it } from 'vitest'
import { collectionOpensAt, marketWait } from './statusStyle'

const START = '2026-09-15T22:40:00Z' // 6:40 PM ET

describe('marketWait', () => {
  it('opens collection at the T-15 cutoff minus the 240-minute freshness window', () => {
    expect(collectionOpensAt(START)).toBe('2026-09-15T18:25:00.000Z')
  })

  it('never calls an unpriced game NO BET: it is awaiting its market, with a countdown', () => {
    const wait = marketWait({ hasPrice: false, startsAt: START, now: Date.parse('2026-09-15T16:25:00Z') })
    expect(wait?.state).toBe('AWAITING_MARKET')
    expect(wait?.label).toBe('Market collection scheduled')
    expect(wait?.detail).toBe('Collects 2:25 PM ET · in 2h 00m')
    expect(wait?.label).not.toMatch(/no bet/i)
  })

  it('reports an open collection window and a game that started without a price', () => {
    expect(marketWait({ hasPrice: false, startsAt: START, now: Date.parse('2026-09-15T19:00:00Z') })?.state).toBe('COLLECTING_MARKET')
    expect(marketWait({ hasPrice: false, startsAt: START, now: Date.parse('2026-09-15T23:00:00Z') })?.state).toBe('NO_PREGAME_PRICE')
  })

  it('steps aside once a price exists, so a real evaluation can show its disposition', () => {
    expect(marketWait({ hasPrice: true, startsAt: START })).toBeNull()
  })
})
