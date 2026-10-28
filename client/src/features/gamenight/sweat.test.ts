import { describe, expect, it } from 'vitest'
import { bpmOf, ecg, progressOf, sweatOf, tierOf } from './sweat'

describe('sweat meter', () => {
  it('reads how much game is played from period, clock and detail', () => {
    expect(progressOf('nba', 1, '12:00', '')).toBe(0)
    expect(progressOf('nba', 4, '0:00', '')).toBe(1)
    expect(progressOf('nba', 3, '6:00', '')).toBeCloseTo(30 / 48)
    expect(progressOf('nfl', 2, '0:00', 'Halftime')).toBe(0.5)
    expect(progressOf('nhl', 4, '3:00', 'OT')).toBe(1)
    expect(progressOf('nba', 4, '45.2', '')).toBeGreaterThan(0.98)
    expect(progressOf('mlb', 9, null, 'Bot 9th')).toBeCloseTo(17.5 / 18)
    expect(progressOf('mlb', 1, null, 'Top 1st')).toBeCloseTo(0.5 / 18)
  })
  it('sweats close late games and not blowouts or early games', () => {
    const late1 = sweatOf('nba', 1, 0.97), early1 = sweatOf('nba', 1, 0.05), lateBlowout = sweatOf('nba', 25, 0.97)
    expect(late1).toBeGreaterThan(0.75)
    expect(early1).toBeLessThan(0.4)
    expect(lateBlowout).toBeLessThan(0.05)
    expect(tierOf(late1).label).toBe('CARDIAC')
    expect(tierOf(sweatOf('nfl', 7, 0.97)).label).toBe('SWEATING') // one score down, two minutes left
    expect(sweatOf('nhl', 0, 0.95)).toBeGreaterThan(0.75) // tied late in the third
    expect(tierOf(sweatOf('nba', 5, 0.5)).label).toBe('WARM') // close at halftime
    expect(bpmOf(0)).toBe(60)
    expect(bpmOf(1)).toBe(170)
  })
  it('draws a beat with its spike near the middle', () => {
    const peak = Array.from({ length: 101 }, (_, i) => ecg(i / 100, 1))
    expect(peak.indexOf(Math.max(...peak))).toBe(40)
    expect(Math.abs(ecg(0.95, 1))).toBeLessThan(0.01)
  })
})
