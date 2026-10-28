import { describe, expect, it } from 'vitest'
import { bandsOf, honesty, starsOf, verdictOf, type GalaxyGame } from './galaxy'

const g = (p: number, out: string, m: number | null = null, sport = 'MLB'): GalaxyGame => ({ sport, p_home_two_way: p, actual_outcome: out, decision: { market_p_home: m } })

describe('calibration galaxy', () => {
  it('scores from the favourite side and skips ties', () => {
    const s = starsOf([g(0.3, 'AWAY'), g(0.7, 'AWAY'), g(0.6, 'TIE')], 'MODEL')
    expect(s).toHaveLength(2)
    expect(s[0].said).toBeCloseTo(0.7); expect(s[0].hit).toBe(true)
    expect(s[1].hit).toBe(false)
  })
  it('market stars come from the recorded market price; paired keeps the same games', () => {
    const games = [g(0.7, 'HOME', 0.6), g(0.7, 'HOME', null)]
    expect(starsOf(games, 'MARKET')).toHaveLength(1)
    expect(starsOf(games, 'MODEL', 'ALL', true)).toHaveLength(1)
    expect(starsOf(games, 'MODEL', 'NFL')).toHaveLength(0)
  })
  it('bands and the verdict read overconfidence as negative points', () => {
    const games = [...Array(10)].map((_, i) => g(0.75, i < 5 ? 'HOME' : 'AWAY'))
    const bands = bandsOf(starsOf(games, 'MODEL'))
    expect(bands).toHaveLength(1)
    expect(bands[0].happened).toBeCloseTo(0.5)
    const v = verdictOf(bands)
    expect(v.gap).toBeCloseTo(-25)
    expect(v.confident?.n).toBe(10)
    expect(honesty(v.gap)).toBe('overconfident')
    expect(honesty(1)).toBe('honest')
  })
})
