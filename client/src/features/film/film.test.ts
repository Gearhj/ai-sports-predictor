import { describe, expect, it } from 'vitest'
import { clipsOf, readClip, standLine, type FilmGame } from './film'

const g = (p: number, m: number, out: string, day = '2026-10-20T20:00:00Z'): FilmGame => ({ sport: 'NFL', home: 'PHI', away: 'DAL', kickoff_utc: day, p_home_two_way: p, actual_outcome: out, decision: { market_p_home: m } })

describe('film room', () => {
  it('leans to the side the model likes more than the market and sorts into rooms', () => {
    const c = clipsOf([g(0.6, 0.5, 'HOME'), g(0.4, 0.5, 'HOME'), g(0.51, 0.5, 'AWAY'), g(0.5, 0.5, 'TIE')], 0)
    expect(c.map((x) => x.room)).toEqual(['CALLED_IT', 'FOOLED', 'RODE_LOSS'])
    expect(c[1].lean).toBe('AWAY'); expect(c[1].team).toBe('DAL'); expect(c[1].stand).toBeCloseTo(0.1)
    expect(readClip(c[0])).toContain('won')
  })
  it('respects the window and scores the stands against the market', () => {
    const c = clipsOf([g(0.6, 0.5, 'HOME', '2026-10-01T00:00:00Z'), g(0.6, 0.5, 'AWAY')], Date.parse('2026-10-10T00:00:00Z'))
    expect(c).toHaveLength(1)
    expect(standLine(c)).toMatchObject({ n: 1, wins: 0, expected: 0.5, modelExpected: 0.6 })
  })
})
