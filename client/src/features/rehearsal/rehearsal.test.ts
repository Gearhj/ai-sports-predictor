import { describe, expect, it } from 'vitest'
import type { PulseGame } from '../pulse/pulseData'
import { progressOf } from '../gamenight/sweat'
import { clockAt, simNow, simulateNight } from './rehearsal'

const g = (id: string, sport: PulseGame['sport'], startsAt: string, away: number, home: number): PulseGame => ({
  id, sport, startsAt, day: '2026-10-25', away: { abbr: 'A', name: 'A' }, home: { abbr: 'H', name: 'H' }, phase: 'FINAL', score: { away, home, detail: 'Final' },
} as PulseGame)

describe('rehearsal', () => {
  it('runs a sped-up clock from t0, and holds when paused', () => {
    const s = { active: true, day: 'd', t0: 1000, startedAt: 0, speed: 72, paused: false, pausedSim: null }
    expect(simNow(s, 10_000)).toBe(1000 + 720_000)
    expect(simNow({ ...s, paused: true, pausedSim: 5 }, 99_999)).toBe(5)
    expect(simNow({ ...s, active: false }, 42)).toBe(42)
  })
  it('turns a finished night into upcoming / live / final games at the simulated time', () => {
    const night = [g('nba', 'NBA', '2026-10-25T23:00:00Z', 101, 108), g('nfl', 'NFL', '2026-10-26T00:20:00Z', 27, 24)]
    const at = (iso: string) => simulateNight(night, '2026-10-25', Date.parse(iso))
    expect(at('2026-10-25T22:00:00Z').map((x) => x.game.phase)).toEqual(['UPCOMING', 'UPCOMING'])
    const mid = at('2026-10-26T00:30:00Z')
    expect(mid.map((x) => x.game.phase)).toEqual(['LIVE', 'LIVE'])
    expect(mid[0].game.score!.home).toBeLessThanOrEqual(108)
    const end = at('2026-10-26T05:00:00Z')
    expect(end.map((x) => x.game.phase)).toEqual(['FINAL', 'FINAL'])
    expect(end[1].game.score).toMatchObject({ away: 27, home: 24 })
  })
  it('never lets a score go backwards and lands on the real final', () => {
    const night = [g('x', 'NBA', '2026-10-25T23:00:00Z', 99, 120)]
    let prev = 0
    for (let m = 0; m <= 150; m += 5) {
      const s = simulateNight(night, '2026-10-25', Date.parse('2026-10-25T23:00:00Z') + m * 60_000)[0].game.score!
      expect(s.home).toBeGreaterThanOrEqual(prev); prev = s.home
    }
    expect(prev).toBe(120)
  })
  it('writes clock text the Sweat Meter reads back', () => {
    const c = clockAt('NBA', 0.75)
    expect(c.detail).toMatch(/ - 4th$/)
    expect(progressOf('nba', c.period, c.clock, c.detail)).toBeCloseTo(0.75, 1)
    expect(clockAt('NHL', 0.5).period).toBe(2)
    expect(clockAt('MLB', 0.95).detail).toMatch(/^(Top|Bot) 9th$/)
  })
})
