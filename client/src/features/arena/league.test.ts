import { describe, expect, it } from 'vitest'
import type { ArenaCall } from './arenaStore'
import { bossOf, rivalryOf, weekStart } from './league'

const call = (id: string, over: Partial<ArenaCall>): ArenaCall => ({
  game_id: id, sport: 'NBA', away: 'A', home: 'H', starts_at: '2026-10-27T23:00:00Z', side: 'HOME', power: 1, blind: false, mode: 'LIVE', at: 'x', ...over,
})
const won = (w: 'HOME' | 'AWAY') => ({ winner: w, aisp: null, at: 'x' })

describe('weekly boss and rival mode', () => {
  it('weeks start Monday midnight ET', () => {
    expect(new Date(weekStart(Date.parse('2026-10-29T15:00:00Z'))).toISOString()).toBe('2026-10-26T04:00:00.000Z')
    expect(new Date(weekStart(Date.parse('2026-10-26T03:30:00Z'))).toISOString()).toBe('2026-10-19T04:00:00.000Z') // Sunday night ET
    expect(new Date(weekStart(Date.parse('2026-11-04T15:00:00Z'))).toISOString()).toBe('2026-11-02T05:00:00.000Z') // standard time
  })
  it('only going against The House counts; wins hit the boss, losses hit you', () => {
    const wk = Date.parse('2026-10-26T04:00:00Z')
    const calls = [
      call('a', { house: 'AWAY', side: 'HOME', result: won('HOME') }), // beat the house
      call('b', { house: 'AWAY', side: 'HOME', result: won('AWAY') }), // house wins
      call('c', { house: 'HOME', side: 'HOME', result: won('HOME') }), // agreed: no fight
      call('d', { house: 'AWAY', side: 'HOME' }), // pending
      call('e', { house: 'AWAY', side: 'HOME', result: won('HOME'), starts_at: '2026-10-20T23:00:00Z' }), // last week
      call('f', { house: 'AWAY', side: 'HOME', result: won('HOME'), mode: 'REWIND' }), // practice never counts
    ]
    expect(bossOf(calls, wk)).toEqual({ fights: 2, wins: 1, losses: 1, bossHp: 80, youHp: 80, broken: false, pending: 1 })
    const five = Array.from({ length: 5 }, (_, i) => call(`w${i}`, { house: 'AWAY', side: 'HOME', result: won('HOME') }))
    expect(bossOf(five, wk).broken).toBe(true)
  })
  it('scores shared games, finds clashes and awards the weekly belt', () => {
    const wk = Date.parse('2026-10-26T04:00:00Z')
    const mine = [call('g1', { side: 'HOME', result: won('HOME') }), call('g2', { side: 'AWAY', result: won('HOME') }), call('g3', { side: 'HOME' }), call('solo', { side: 'HOME', result: won('HOME') })]
    const theirs = [{ game_id: 'g1', side: 'AWAY' as const }, { game_id: 'g2', side: 'AWAY' as const }, { game_id: 'g3', hidden: true }]
    const r = rivalryOf(mine, theirs, wk)
    expect(r.shared.map((s) => s.game_id).sort()).toEqual(['g1', 'g2'])
    expect([r.me, r.rival, r.settled]).toEqual([1, 0, 2])
    expect(r.belt).toBe('ME')
    expect(r.clashes.map((c) => c.game_id)).toEqual(['g1'])
  })
})
