import { describe, expect, it } from 'vitest'
import { decide, noVig, phi, stepsOf } from './forge'

describe('build a forecast', () => {
  it('phi is the normal CDF', () => {
    expect(phi(0)).toBeCloseTo(0.5, 6)
    expect(phi(1)).toBeCloseTo(0.8413, 3)
    expect(phi(-1.96)).toBeCloseTo(0.025, 3)
  })
  it('steps accumulate margin and convert through the sport spread', () => {
    const s = stepsOf({ sport: 'NFL', strength: 3, homeField: true, rest: 0, homeOut: true, awayOut: false })
    expect(s[0].p).toBeCloseTo(0.5)
    expect(s[2].margin).toBeCloseTo(4.5)
    expect(s[s.length - 1].margin).toBeCloseTo(-1.5)
    expect(s[s.length - 1].p).toBeLessThan(0.5)
  })
  it('no-vig removes the hold', () => {
    expect(noVig(-110, -110)).toBeCloseTo(0.5)
    expect(noVig(-150, 130)).toBeGreaterThan(0.56)
  })
  it('gates run in order: gap guard, edge, stake floor, funded sport', () => {
    expect(decide('NFL', 0.5, -110, -110).verdict).toBe('PASS')
    const bet = decide('NFL', 0.56, -110, -110)
    expect(bet.verdict).toBe('BET'); expect(bet.stake).toBeLessThanOrEqual(5); expect(bet.stake).toBeGreaterThanOrEqual(1)
    expect(decide('NBA', 0.56, -110, -110).verdict).toBe('RECORD_ONLY')
    expect(decide('NHL', 0.75, -110, -110).verdict).toBe('RECORD_ONLY')
    const away = decide('NHL', 0.4, -110, -110)
    expect(away.side).toBe('AWAY')
  })
})
