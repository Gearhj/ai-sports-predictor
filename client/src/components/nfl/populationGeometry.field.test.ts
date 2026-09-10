/**
 * The field is 120 yards and the producer's coordinates describe 100 of them.
 *
 * This is the defect these tests exist for: the population surface used to
 * paint the end zones INSIDE the producer's 0..100 span, so a snap two yards
 * from the goal line -- an ordinary play -- was drawn behind the end line. The
 * data was right and the ruler was wrong, and the result read as a model that
 * did not know where a football field ends.
 */
import { describe, expect, it } from 'vitest'
import { FIELD_OF_PLAY, outOfFieldPoints, startsInsideField, type PopulationPlay } from './populationGeometry'

function play(over: Partial<PopulationPlay> = {}): PopulationPlay {
  return {
    g: 0, n: 1, t: 'RUSH', poss: 'HOME', dir: 'LEFT', dn: 1, di: 10, yl: 75, q: 1,
    yds: 4, sc: null, scTeam: null, to: false,
    segs: [{ k: 'RUN_GROUND', i: 'LINEAR', s: [75, 0.5], c: [], e: [71, 0.52] }],
    mk: [],
    ...over,
  }
}

describe('the field of play', () => {
  it('is the goal-line-to-goal-line span, not the whole rectangle', () => {
    expect(FIELD_OF_PLAY).toEqual([0, 100])
  })

  it('accepts a snap two yards from the goal line as an ordinary play', () => {
    // The case the old geometry drew behind the end line.
    const goalLineStand = play({ segs: [{ k: 'RUN_GROUND', i: 'LINEAR', s: [2, 0.5], c: [], e: [0, 0.5] }] })
    expect(startsInsideField(goalLineStand)).toBe(true)
    expect(outOfFieldPoints([goalLineStand])).toEqual({ plays: 0, points: 0 })
  })

  it('accepts a path that terminates exactly on a goal line', () => {
    // A touchdown ends at the goal line. It is not extended into the end zone
    // with coordinates nothing measured.
    const touchdown = play({ sc: 'TD', segs: [{ k: 'RUN_GROUND', i: 'LINEAR', s: [8, 0.5], c: [], e: [0, 0.5] }] })
    expect(outOfFieldPoints([touchdown])).toEqual({ plays: 0, points: 0 })
  })

  it('reports a point behind an end line instead of hiding the clip', () => {
    const behind = play({ segs: [{ k: 'RUN_GROUND', i: 'LINEAR', s: [-4, 0.5], c: [], e: [3, 0.5] }] })
    expect(startsInsideField(behind)).toBe(false)
    expect(outOfFieldPoints([behind])).toEqual({ plays: 1, points: 1 })
  })

  it('counts every offending point of a play, control points and markers included', () => {
    const broken = play({
      segs: [{ k: 'PASS_MODELED_AIR_CURVE', i: 'CUBIC_BEZIER', s: [-1, 0.4], c: [[50, 0.2], [104, 0.3]], e: [101, 0.5] }],
      mk: [{ k: 'TURNOVER', p: [110, 0.5] }],
    })
    expect(outOfFieldPoints([broken])).toEqual({ plays: 1, points: 4 })
  })

  it('counts a play once however many of its points are out of range', () => {
    const one = play({ segs: [{ k: 'RUN_GROUND', i: 'LINEAR', s: [-2, 0.5], c: [], e: [-1, 0.5] }] })
    const clean = play({ n: 2 })
    expect(outOfFieldPoints([one, clean]).plays).toBe(1)
  })
})
