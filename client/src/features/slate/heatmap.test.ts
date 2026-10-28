import { describe, expect, it } from 'vitest'
import type { PulseGame } from '../pulse/pulseData'
import { weightOf } from './SlateHeatMap'

const g = (over: Partial<PulseGame>): PulseGame => ({
  id: 'x', sport: 'NBA', startsAt: '2026-10-26T23:00:00Z', day: '2026-10-26', away: { abbr: 'A', name: 'A' }, home: { abbr: 'H', name: 'H' }, phase: 'UPCOMING', score: null,
  modelHome: 0.55, marketHome: 0.5, marketFresh: true, lean: { side: 'HOME', gap: 5, model: 0.55, market: 0.5 }, favorite: { side: 'HOME', prob: 0.55 }, position: null, decision: null, bestHome: null, bestAway: null, ...over,
} as unknown as PulseGame)

describe('slate heat map weights', () => {
  it('AISP bet biggest, then edge inside the 10-point rule; a gap over the rule is base size', () => {
    expect(weightOf(g({ position: { selection: 'H ML' } as never }))).toBe(4)
    expect(weightOf(g({}))).toBeCloseTo(1 + 5 / 3)
    expect(weightOf(g({ modelHome: 0.83, marketHome: 0.57, lean: { side: 'HOME', gap: 26, model: 0.83, market: 0.57 } }))).toBe(1)
    expect(weightOf(g({ marketFresh: false, lean: null }))).toBe(1)
  })
})
