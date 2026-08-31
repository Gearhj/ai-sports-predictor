import { describe, expect, it } from 'vitest'
import { isValidIsoDate, operationalToday, resolveOperationalDate, shiftDate } from './operationalDate'

describe('operationalToday', () => {
  it('stays on the Eastern date during UTC-next-day evening hours', () => {
    // 2026-08-30 22:45 America/New_York (EDT, UTC-4) == 2026-08-31 02:45 UTC.
    expect(operationalToday(new Date('2026-08-31T02:45:00Z'))).toBe('2026-08-30')
  })

  it('holds the Eastern date at 11:59 PM Eastern', () => {
    // 2026-08-30 23:59 EDT == 2026-08-31 03:59 UTC.
    expect(operationalToday(new Date('2026-08-31T03:59:00Z'))).toBe('2026-08-30')
  })

  it('rolls at midnight Eastern, not midnight UTC', () => {
    // 2026-08-31 00:00 EDT == 2026-08-31 04:00 UTC.
    expect(operationalToday(new Date('2026-08-31T04:00:00Z'))).toBe('2026-08-31')
    expect(operationalToday(new Date('2026-08-31T03:59:59Z'))).toBe('2026-08-30')
  })

  it('handles the spring-forward DST transition (2026-03-08, EST→EDT)', () => {
    // 2026-03-08 01:59 EST == 06:59 UTC — still March 8.
    expect(operationalToday(new Date('2026-03-08T06:59:00Z'))).toBe('2026-03-08')
    // 11:30 PM EDT that night == 03:30 UTC March 9 — still March 8 locally.
    expect(operationalToday(new Date('2026-03-09T03:30:00Z'))).toBe('2026-03-08')
  })

  it('handles the fall-back DST transition (2026-11-01, EDT→EST)', () => {
    // 11:30 PM EST on Nov 1 == 04:30 UTC Nov 2 — still November 1 locally.
    expect(operationalToday(new Date('2026-11-02T04:30:00Z'))).toBe('2026-11-01')
  })

  it('falls back to America/New_York when no timezone is supplied', () => {
    expect(operationalToday(new Date('2026-08-31T02:45:00Z'))).toBe('2026-08-30')
  })

  it('respects an explicitly configured display timezone', () => {
    expect(operationalToday(new Date('2026-08-31T02:45:00Z'), 'UTC')).toBe('2026-08-31')
  })
})

describe('shiftDate', () => {
  it('shifts across month boundaries', () => {
    expect(shiftDate('2026-08-31', 1)).toBe('2026-09-01')
    expect(shiftDate('2026-09-01', -1)).toBe('2026-08-31')
  })
})

describe('resolveOperationalDate', () => {
  it('prefers a valid URL date', () => {
    expect(resolveOperationalDate('2026-08-15', new Date('2026-08-31T02:45:00Z'))).toBe('2026-08-15')
  })

  it('rejects invalid URL values and uses the Eastern today', () => {
    expect(resolveOperationalDate('not-a-date', new Date('2026-08-31T02:45:00Z'))).toBe('2026-08-30')
    expect(resolveOperationalDate('2026-02-30', new Date('2026-08-31T02:45:00Z'))).toBe('2026-08-30')
    expect(resolveOperationalDate(null, new Date('2026-08-31T02:45:00Z'))).toBe('2026-08-30')
  })
})

describe('isValidIsoDate', () => {
  it('accepts real calendar dates only', () => {
    expect(isValidIsoDate('2026-08-30')).toBe(true)
    expect(isValidIsoDate('2026-13-01')).toBe(false)
    expect(isValidIsoDate('')).toBe(false)
    expect(isValidIsoDate(undefined)).toBe(false)
  })
})
