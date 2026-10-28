import { describe, expect, it } from 'vitest'
import { badgesOf, levelOf, titleOf, xpOf, type ArenaCall } from './arenaStore'

const call = (over: Partial<ArenaCall>): ArenaCall => ({
  game_id: 'g', sport: 'NBA', away: 'MIN', home: 'CLE', starts_at: '2026-10-26T23:00:00Z', side: 'HOME', power: 1,
  blind: false, mode: 'LIVE', at: '2026-10-26T20:00:00Z', ...over,
})

describe('arena scoring', () => {
  it('levels at 0, 250, 750, 1500 XP', () => {
    expect(levelOf(0)).toEqual({ level: 1, into: 0, need: 250 })
    expect(levelOf(249).level).toBe(1)
    expect(levelOf(250)).toEqual({ level: 2, into: 0, need: 500 })
    expect(levelOf(750).level).toBe(3)
    expect(levelOf(1500).level).toBe(4)
    expect(titleOf(5)).toBe('SHARP')
  })
  it('pays power x 100, x1.5 blind, +150 for beating AISP, half in rewind, nothing when wrong', () => {
    const won = { winner: 'HOME' as const, aisp: 'HOME' as const, at: 'x' }
    expect(xpOf(call({ result: won })).xp).toBe(100)
    expect(xpOf(call({ power: 3, blind: true, result: won })).xp).toBe(450)
    expect(xpOf(call({ power: 2, result: { ...won, aisp: 'AWAY' } })).xp).toBe(350)
    expect(xpOf(call({ mode: 'REWIND', result: won })).xp).toBe(50)
    expect(xpOf(call({ result: { ...won, winner: 'AWAY' } })).xp).toBe(0)
    expect(xpOf(call({ result: { ...won, winner: 'TIE' } })).xp).toBe(0)
    expect(xpOf(call({})).xp).toBe(0)
  })
  it('unlocks badges from the record', () => {
    const r = (i: number, side: 'HOME' | 'AWAY', aisp: 'HOME' | 'AWAY') => call({ game_id: `g${i}`, starts_at: `2026-10-2${i}T23:00:00Z`, side, result: { winner: 'HOME', aisp, at: 'x' } })
    const got = (calls: ArenaCall[]) => badgesOf(calls).filter((b) => b.got).map((b) => b.id)
    expect(got([])).toEqual([])
    expect(got([r(1, 'HOME', 'AWAY'), r(2, 'HOME', 'HOME'), r(3, 'HOME', 'HOME')])).toEqual(['first', 'breaker', 'hat'])
  })
})
