import { describe, expect, it } from 'vitest'
import { gameDateLabel, selectionLabel } from './SettledWagerHistory'

describe('selectionLabel', () => {
  it('names the team for a bare side', () => {
    expect(selectionLabel('AWAY', 'DEN at KC')).toBe('DEN ML')
    expect(selectionLabel('HOME', 'DEN @ KC')).toBe('KC ML')
    expect(selectionLabel('WSH ML', 'PHI at WSH')).toBe('WSH ML')
    expect(selectionLabel('AWAY', null)).toBe('AWAY')
  })
})

describe('gameDateLabel', () => {
  it('uses the Eastern date across the UTC midnight boundary', () => {
    // Sunday-night kickoff 2026-09-13 8:20 PM EDT is 2026-09-14 00:20 UTC.
    expect(gameDateLabel('2026-09-14T00:20:00Z', '2026-09-14')).toBe('2026-09-13')
    expect(gameDateLabel('2026-09-13T17:00:00Z', '2026-09-13')).toBe('2026-09-13')
  })

  it('holds across the November DST change', () => {
    // 2026-11-01 is the fall-back day. 8:20 PM EST that night is 01:20 UTC on Nov 2.
    expect(gameDateLabel('2026-11-02T01:20:00Z', '2026-11-02')).toBe('2026-11-01')
    // 8:20 PM EDT the Sunday before is 00:20 UTC on Oct 26.
    expect(gameDateLabel('2026-10-26T00:20:00Z', '2026-10-26')).toBe('2026-10-25')
    // 11:30 PM EST on Nov 1 is 04:30 UTC on Nov 2 (a -5 hour offset, not -4).
    expect(gameDateLabel('2026-11-02T04:30:00Z', '2026-11-02')).toBe('2026-11-01')
    expect(gameDateLabel('2026-11-02T05:30:00Z', '2026-11-02')).toBe('2026-11-02')
  })

  it('falls back to the slate date without a start time', () => {
    expect(gameDateLabel(null, '2026-09-14')).toBe('2026-09-14')
  })
})
