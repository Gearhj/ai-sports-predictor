import { describe, expect, it } from 'vitest'
import { formatDate, formatTime, formatTimestamp, recencySortValue, timeSortValue } from './format'

describe('API timestamp formatting', () => {
  it('keeps already formatted demo labels unchanged', () => {
    expect(formatTime('7:15 PM ET')).toBe('7:15 PM ET')
    expect(formatTimestamp('4 min ago')).toBe('4 min ago')
    expect(formatDate('Tuesday, August 12, 2026')).toBe('Tuesday, August 12, 2026')
  })

  it('turns ISO values into human-readable labels', () => {
    expect(formatTime('2026-08-12T23:15:00Z')).toMatch(/7:15 PM EDT|11:15 PM UTC/)
    expect(formatTimestamp('2026-08-12T23:15:00Z')).toMatch(/Aug 12/)
    expect(formatDate('2026-08-12')).toMatch(/August 12, 2026/)
  })

  it('sorts both API timestamps and demo clock labels chronologically', () => {
    expect(timeSortValue('6:35 PM ET')).toBeLessThan(timeSortValue('8:10 PM ET'))
    expect(timeSortValue('2026-08-12T22:35:00Z')).toBeLessThan(timeSortValue('2026-08-13T00:10:00Z'))
  })

  it('ranks ISO and relative freshness labels', () => {
    const now = new Date('2026-08-12T20:00:00Z').getTime()
    expect(recencySortValue('3 min ago', now)).toBeGreaterThan(recencySortValue('9 min ago', now))
    expect(recencySortValue('2026-08-12T19:59:00Z', now)).toBeGreaterThan(recencySortValue('2026-08-12T19:55:00Z', now))
  })
})
