import { describe, expect, it } from 'vitest'
import { tally, upsetRates, upsetsOf } from './upsets'
import type { GalaxyGame } from './galaxy'

const g = (p: number, out: string, m: number | null = null, home = 'H', away = 'A'): GalaxyGame => ({ sport: 'MLB', p_home_two_way: p, actual_outcome: out, home, away, decision: { market_p_home: m } })

describe('upset atlas', () => {
  it('finds favourites that lost at the threshold and marks who else missed', () => {
    const { upsets, eligible } = upsetsOf([g(0.7, 'AWAY', 0.65), g(0.25, 'HOME', 0.55), g(0.7, 'HOME'), g(0.55, 'AWAY'), g(0.7, 'TIE')])
    expect(eligible).toBe(3)
    expect(upsets).toHaveLength(2)
    expect(upsets[0]).toMatchObject({ winner: 'A', loser: 'H', alone: false })
    expect(upsets[1]).toMatchObject({ winner: 'H', loser: 'A', alone: true })
    expect(upsets[1].said).toBeCloseTo(0.75)
  })
  it('tallies teams and bands', () => {
    const games = [g(0.7, 'AWAY', null, 'NYY', 'BOS'), g(0.8, 'AWAY', null, 'NYY', 'TB'), g(0.65, 'HOME', null, 'NYY', 'TOR')]
    const { upsets } = upsetsOf(games)
    expect(tally(upsets, 'loser')[0]).toMatchObject({ team: 'NYY', n: 2 })
    const r = upsetRates(games)
    expect(r.map((b) => [b.n, b.upsets])).toEqual([[1, 0], [1, 1], [1, 1]])
  })
})
