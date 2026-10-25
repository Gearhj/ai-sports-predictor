/**
 * "Next up" lists only games whose scheduled start is still ahead. Instants
 * are absolute (the server sends offset ISO times), so Eastern display time and
 * daylight-saving changes cannot move a game across the boundary.
 */
import { describe, expect, it } from 'vitest'
import { startsInFuture, upcomingOnly } from './nextUp'
import { nextAttention, type AgendaEvent } from './agenda'

const START = '2026-09-16T19:07:00-04:00' // 7:07 PM EDT
const at = (iso: string) => Date.parse(iso)

describe('startsInFuture', () => {
  it('is true before the start, false at and after it', () => {
    expect(startsInFuture(START, at('2026-09-16T19:06:59-04:00'))).toBe(true)
    expect(startsInFuture(START, at(START))).toBe(false)
    expect(startsInFuture(START, at('2026-09-16T19:07:01-04:00'))).toBe(false)
  })

  it('compares instants, not wall-clock text, whatever the offset', () => {
    // The same instant written in UTC.
    expect(startsInFuture('2026-09-16T23:07:00Z', at('2026-09-16T19:06:00-04:00'))).toBe(true)
    expect(startsInFuture('2026-09-16T23:07:00Z', at('2026-09-16T19:08:00-04:00'))).toBe(false)
  })

  it('holds across the November DST change', () => {
    // 2026-11-01: 1:30 AM happens twice in New York. EDT 01:30 precedes EST 01:30 by an hour.
    const estStart = '2026-11-01T01:30:00-05:00'
    expect(startsInFuture(estStart, at('2026-11-01T01:45:00-04:00'))).toBe(true)  // 45 min earlier, in EDT
    expect(startsInFuture(estStart, at('2026-11-01T01:15:00-05:00'))).toBe(true)
    expect(startsInFuture(estStart, at('2026-11-01T01:30:00-05:00'))).toBe(false)
    // March 8: 2:30 AM EST does not exist; 3:05 AM EDT is 5 minutes after 2:00 AM EST.
    expect(startsInFuture('2026-03-08T03:05:00-04:00', at('2026-03-08T01:59:00-05:00'))).toBe(true)
    expect(startsInFuture('2026-03-08T03:05:00-04:00', at('2026-03-08T03:06:00-04:00'))).toBe(false)
  })

  it('treats a missing or unreadable start as not upcoming', () => {
    expect(startsInFuture(null, 0)).toBe(false)
    expect(startsInFuture('', 0)).toBe(false)
    expect(startsInFuture('not a time', 0)).toBe(false)
  })
})

describe('upcomingOnly', () => {
  const games = [
    { id: 'started', startsAt: '2026-09-16T18:40:00-04:00' },
    { id: 'exact', startsAt: '2026-09-16T19:00:00-04:00' },
    { id: 'late', startsAt: '2026-09-16T22:10:00-04:00' },
    { id: 'soon', startsAt: '2026-09-16T19:05:00-04:00' },
    { id: 'tbd', startsAt: null },
  ]

  it('drops started games and sorts the rest by start', () => {
    expect(upcomingOnly(games, at('2026-09-16T19:00:00-04:00')).map((g) => g.id)).toEqual(['soon', 'late'])
  })

  it('respects the limit', () => {
    expect(upcomingOnly(games, at('2026-09-16T12:00:00-04:00'), 2).map((g) => g.id)).toEqual(['started', 'exact'])
  })
})

describe('agenda next attention', () => {
  const event = (key: string, kind: AgendaEvent['kind'], iso: string, status: AgendaEvent['status']): AgendaEvent => ({
    key, kind, sport: 'MLB', gameId: key, matchup: key, at: at(iso), status, label: key, detail: '',
  })

  it('never offers a game that has already started', () => {
    const now = at('2026-09-16T19:07:00-04:00')
    const events = [
      event('just-started', 'game', '2026-09-16T19:05:00-04:00', 'scheduled'),
      event('exact', 'game', '2026-09-16T19:07:00-04:00', 'scheduled'),
      event('next', 'game', '2026-09-16T19:10:00-04:00', 'scheduled'),
    ]
    expect(nextAttention(events, now, 3).map((i) => i.event.key)).toEqual(['next'])
  })
})
