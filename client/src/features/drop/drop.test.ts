import { describe, expect, it } from 'vitest'
import type { PulseGame } from '../pulse/pulseData'
import { chooseDrop, dropTime } from './drop'

const NOW = Date.parse('2026-10-26T17:00:00Z')
const g = (id: string, over: Partial<PulseGame>): PulseGame => ({
  id, sport: 'NBA', startsAt: '2026-10-26T23:00:00Z', day: '2026-10-26', away: { abbr: 'A', name: 'Away' }, home: { abbr: 'H', name: 'Home' },
  phase: 'UPCOMING', score: null, modelHome: 0.55, modelCreatedAt: null, engine: null, marketHome: 0.5, marketCurrent: true, marketObservedAt: null, books: 5,
  bestAway: null, bestHome: { american: -110, book: 'draftkings' }, marketFresh: true, lean: null, bettable: { ok: true, label: '' }, favorite: { side: 'HOME', prob: 0.55 },
  decision: null, position: null, windowOpensAt: null, ...over,
} as PulseGame)

describe('the drop', () => {
  it('prefers AISP’s own bet, then the biggest edge inside the 10-point rule, then the surest favourite', () => {
    const lean = (side: 'HOME' | 'AWAY', gap: number) => ({ side, gap, model: 0.5, market: 0.5 })
    const small = g('small', { modelHome: 0.56, marketHome: 0.5, lean: lean('HOME', 6) })
    const huge = g('huge', { modelHome: 0.75, marketHome: 0.5, lean: lean('HOME', 25) })
    expect(chooseDrop([small, huge], NOW)?.game.id).toBe('small') // a 25-point gap is a warning, never the headline
    expect(chooseDrop([small, huge], NOW)?.kind).toBe('BIGGEST_EDGE')
    const bet = g('bet', { modelHome: 0.52, position: { selection: 'H ML', result: null } as never })
    expect(chooseDrop([small, bet], NOW)?.kind).toBe('AISP_BET')
    const stale = g('stale', { marketFresh: false, modelHome: 0.2, lean: null, favorite: { side: 'AWAY', prob: 0.8 } })
    const d = chooseDrop([stale], NOW)
    expect(d?.kind).toBe('MODEL_SUREST'); expect(d?.side).toBe('AWAY'); expect(d?.model).toBeCloseTo(0.8)
  })
  it('skips games that already started', () => {
    expect(chooseDrop([g('old', { startsAt: '2026-10-26T16:00:00Z' })], NOW)).toBeNull()
  })
  it('drops at noon ET in daylight and standard time', () => {
    expect(new Date(dropTime('2026-10-26')).toISOString()).toBe('2026-10-26T16:00:00.000Z')
    expect(new Date(dropTime('2026-11-02')).toISOString()).toBe('2026-11-02T17:00:00.000Z')
  })
})
