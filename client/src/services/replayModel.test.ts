/**
 * The pure derivations behind the Diamond Theater.
 *
 * These guard the line between "read from the persisted path" and "invented":
 * base masks, runner movement, feed chronology, line score and summary must
 * all be reproducible from the stored fields alone.
 */
import { describe, expect, it } from 'vitest'
import {
  basesFromMask,
  buildFeed,
  buildLineScore,
  describeBases,
  describeEvent,
  inferRunnerMoves,
  occupiedBases,
  ordinal,
  outcomeLabel,
  outcomeTone,
  replayStatus,
  summarizePath,
} from './replayModel'
import type { ShadowPathEvent } from '../types/shadow'

function event(overrides: Partial<ShadowPathEvent> & { pa: number }): ShadowPathEvent {
  return {
    inning: 1, half: 'TOP', battingOrderPosition: 1, outcome: 'BIP_OUT',
    outsBefore: 0, outsAfter: 1, baseMaskBefore: 0, baseMaskAfter: 0,
    runsScored: 0, homeScoreAfter: 0, awayScoreAfter: 0,
    pitcherState: 'STARTER', pitchingChange: false,
    ...overrides,
  }
}

describe('base masks', () => {
  it.each([
    [0, { first: false, second: false, third: false }, []],
    [1, { first: true, second: false, third: false }, [1]],
    [2, { first: false, second: true, third: false }, [2]],
    [3, { first: true, second: true, third: false }, [1, 2]],
    [4, { first: false, second: false, third: true }, [3]],
    [5, { first: true, second: false, third: true }, [1, 3]],
    [6, { first: false, second: true, third: true }, [2, 3]],
    [7, { first: true, second: true, third: true }, [1, 2, 3]],
  ])('mask %i decodes to exactly one state', (mask, bases, occupied) => {
    expect(basesFromMask(mask)).toEqual(bases)
    expect(occupiedBases(mask)).toEqual(occupied)
  })

  it('describes occupancy in baseball language', () => {
    expect(describeBases(0)).toBeNull()
    expect(describeBases(1)).toBe('first')
    expect(describeBases(5)).toBe('first and third')
    expect(describeBases(7)).toBe('first, second and third')
  })
})

describe('runner movement is inferred, never invented', () => {
  it('walks the batter to first with the bases empty', () => {
    const moves = inferRunnerMoves(event({ pa: 1, outcome: 'WALK', baseMaskAfter: 1, outsAfter: 0 }))
    expect(moves).toEqual([{ id: 'batter', from: 0, to: 1, scored: false }])
  })

  it('never lets a runner pass another', () => {
    // First and second occupied, single leaves first and third: the lead
    // runner takes third, the trailing runner takes... nothing ahead of it
    // that it could reach without passing, so it is the one that scores.
    const moves = inferRunnerMoves(event({
      pa: 2, outcome: 'SINGLE',
      baseMaskBefore: 3, baseMaskAfter: 5, runsScored: 0, outsAfter: 0,
    }))
    const byId = Object.fromEntries(moves.map((move) => [move.id, move.to]))
    expect(byId.r2).toBe(3)
    expect(byId.batter).toBe(1)
    // No token is ever assigned a base behind where it started.
    for (const move of moves) {
      if (move.to !== null && move.to < 4) expect(move.to).toBeGreaterThanOrEqual(move.from)
    }
  })

  it('scores the lead runner when the record says a run scored', () => {
    // Runner on third, single: the run scores and the batter takes first.
    const moves = inferRunnerMoves(event({
      pa: 8, outcome: 'SINGLE',
      baseMaskBefore: 4, baseMaskAfter: 1, runsScored: 1, outsAfter: 1, outsBefore: 1,
    }))
    expect(moves).toContainEqual({ id: 'r3', from: 3, to: 4, scored: true })
    expect(moves).toContainEqual({ id: 'batter', from: 0, to: 1, scored: false })
    expect(moves.filter((move) => move.scored)).toHaveLength(1)
  })

  it('clears the bases on a home run and scores everyone', () => {
    const moves = inferRunnerMoves(event({
      pa: 39, outcome: 'HOME_RUN',
      baseMaskBefore: 5, baseMaskAfter: 0, runsScored: 3, outsAfter: 0,
    }))
    expect(moves).toHaveLength(3)
    expect(moves.every((move) => move.scored && move.to === 4)).toBe(true)
  })

  it('draws no batter token when the batter is out', () => {
    const moves = inferRunnerMoves(event({
      pa: 3, outcome: 'STRIKEOUT',
      baseMaskBefore: 1, baseMaskAfter: 1, outsBefore: 0, outsAfter: 1,
    }))
    expect(moves.find((move) => move.id === 'batter')).toBeUndefined()
    expect(moves).toContainEqual({ id: 'r1', from: 1, to: 1, scored: false })
  })

  it('never scores more runners than the record recorded', () => {
    const moves = inferRunnerMoves(event({
      pa: 4, outcome: 'BIP_OUT',
      baseMaskBefore: 7, baseMaskAfter: 0, runsScored: 1, outsBefore: 2, outsAfter: 3,
    }))
    expect(moves.filter((move) => move.scored)).toHaveLength(1)
  })
})

describe('outcome vocabulary', () => {
  it('names each persisted class and passes an unknown one through readably', () => {
    expect(outcomeLabel('STRIKEOUT')).toBe('Strikeout')
    expect(outcomeLabel('BIP_OUT')).toBe('Ball in play — out')
    expect(outcomeLabel('WALK_HBP_CI')).toBe('Walk / hit by pitch')
    expect(outcomeLabel('SOME_NEW_CLASS')).toBe('Some new class')
  })

  it('separates outs, reaches and home runs', () => {
    expect(outcomeTone('STRIKEOUT')).toBe('out')
    expect(outcomeTone('BIP_OUT')).toBe('out')
    expect(outcomeTone('SINGLE')).toBe('reach')
    expect(outcomeTone('HOME_RUN')).toBe('homer')
  })
})

describe('event descriptions stay inside the record', () => {
  it('states outs, base state and runs and nothing more', () => {
    expect(describeEvent(event({ pa: 1, outcome: 'STRIKEOUT', outsAfter: 1 })).headline)
      .toBe('Strikeout — 1 out')
    expect(describeEvent(event({
      pa: 2, outcome: 'WALK', baseMaskAfter: 1, outsAfter: 0,
    })).headline).toBe('Walk — runner on first')
    expect(describeEvent(event({
      pa: 3, outcome: 'SINGLE', baseMaskAfter: 5, outsAfter: 0,
    })).headline).toBe('Single — runners on first and third')
    expect(describeEvent(event({ pa: 4, outsBefore: 2, outsAfter: 3 })).headline)
      .toBe('Ball in play — out — side retired')
  })

  it('reports runs with the score after the play', () => {
    const described = describeEvent(event({
      pa: 8, outcome: 'SINGLE', baseMaskBefore: 4, baseMaskAfter: 1,
      runsScored: 2, outsAfter: 1, awayScoreAfter: 0, homeScoreAfter: 2,
    }))
    expect(described.detail).toBe('Two runs score. 0–2 after the play.')
  })

  it('never mentions a pitch, a fielder or a batted-ball location', () => {
    const text = [
      describeEvent(event({ pa: 1, outcome: 'BIP_OUT', outsAfter: 1 })),
      describeEvent(event({ pa: 2, outcome: 'HOME_RUN', runsScored: 1 })),
    ].map((item) => `${item.headline} ${item.detail ?? ''}`).join(' ').toLowerCase()
    for (const forbidden of ['fly', 'ground', 'left field', 'fastball', 'pitch', 'caught']) {
      expect(text).not.toContain(forbidden)
    }
  })
})

describe('the feed cannot reveal the future', () => {
  const path = [
    event({ pa: 1, outcome: 'STRIKEOUT', outsAfter: 1 }),
    event({ pa: 2, outcome: 'SINGLE', baseMaskAfter: 1, outsBefore: 1, outsAfter: 1 }),
    event({ pa: 3, inning: 2, outcome: 'HOME_RUN', runsScored: 1, awayScoreAfter: 1 }),
    event({ pa: 4, inning: 2, outcome: 'BIP_OUT', outsAfter: 1, pitchingChange: true }),
  ]

  it('returns only completed events, newest first', () => {
    const feed = buildFeed(path, 1)
    expect(feed.map((entry) => entry.pa)).toEqual([2, 1])
    expect(feed[0].headline).toBe('Single — runner on first')
  })

  it('never includes a plate appearance after the current index', () => {
    for (let index = 0; index < path.length; index += 1) {
      const feed = buildFeed(path, index)
      expect(Math.max(...feed.map((entry) => entry.pa))).toBeLessThanOrEqual(path[index].pa)
    }
  })

  it('adds pitching-change and inning entries in place', () => {
    const feed = buildFeed(path, 3)
    expect(feed.map((entry) => entry.headline)).toContain('Pitching change — team bullpen enters')
    expect(feed.some((entry) => entry.headline.startsWith('End of the'))).toBe(true)
  })

  it('announces extra innings rather than an ordinary inning end', () => {
    const extras = [
      event({ pa: 1, inning: 9, half: 'BOTTOM', outsAfter: 3, outsBefore: 2 }),
      event({ pa: 2, inning: 10, outcome: 'SINGLE', baseMaskAfter: 1, outsAfter: 0 }),
    ]
    expect(buildFeed(extras, 1).map((entry) => entry.headline))
      .toContain('Game moves to extra innings')
  })

  it('respects the entry limit', () => {
    expect(buildFeed(path, 3, 2)).toHaveLength(2)
  })
})

describe('line score and summary describe the one path', () => {
  const path = [
    event({ pa: 1, inning: 1, half: 'TOP', outcome: 'HOME_RUN', runsScored: 1, awayScoreAfter: 1 }),
    event({ pa: 2, inning: 1, half: 'BOTTOM', outsAfter: 3, outsBefore: 2, awayScoreAfter: 1 }),
    event({ pa: 3, inning: 2, half: 'TOP', outsAfter: 1, awayScoreAfter: 1, pitchingChange: true }),
    event({
      pa: 4, inning: 2, half: 'BOTTOM', outcome: 'SINGLE', runsScored: 2,
      awayScoreAfter: 1, homeScoreAfter: 2, baseMaskAfter: 1, outsAfter: 1,
    }),
  ]

  it('attributes runs to the half-inning they happened in', () => {
    const lines = buildLineScore(path)
    expect(lines).toHaveLength(2)
    expect(lines[0]).toMatchObject({ inning: 1, awayRuns: 1, homeRuns: 0 })
    expect(lines[1]).toMatchObject({ inning: 2, awayRuns: 0, homeRuns: 2, pitchingChanges: 1 })
  })

  it('leaves a half-inning that was never batted as null, not zero', () => {
    const lines = buildLineScore([path[0]])
    expect(lines[0].awayRuns).toBe(1)
    expect(lines[0].homeRuns).toBeNull()
  })

  it('summarises the path from its own events', () => {
    const summary = summarizePath(path)!
    expect(summary.plateAppearances).toBe(4)
    expect(summary.innings).toBe(2)
    expect(summary.extraInnings).toBe(false)
    expect(summary.pitchingChanges).toEqual([3])
    expect(summary.scoringPlays.map((item) => item.pa)).toEqual([1, 4])
    expect(summary.awayFinal).toBe(1)
    expect(summary.homeFinal).toBe(2)
  })

  it('marks extra innings from the final inning reached', () => {
    const summary = summarizePath([event({ pa: 1, inning: 10 })])!
    expect(summary.extraInnings).toBe(true)
  })

  it('returns null for an empty path rather than inventing a game', () => {
    expect(summarizePath([])).toBeNull()
    expect(buildLineScore([])).toEqual([])
  })
})

describe('replay status', () => {
  const path = [
    event({ pa: 1 }),
    event({ pa: 2, inning: 10 }),
    event({ pa: 3, inning: 10 }),
  ]

  it('reports in progress, extra innings and final from the path itself', () => {
    expect(replayStatus(path, 0)).toBe('IN_PROGRESS')
    expect(replayStatus(path, 1)).toBe('EXTRA_INNINGS')
    expect(replayStatus(path, 2)).toBe('FINAL')
    expect(replayStatus([], 0)).toBe('PREGAME')
  })
})

describe('ordinals', () => {
  it('spells small innings and falls back to a suffix', () => {
    expect(ordinal(1)).toBe('first')
    expect(ordinal(6)).toBe('sixth')
    expect(ordinal(10)).toBe('tenth')
    expect(ordinal(11)).toBe('11th')
    expect(ordinal(21)).toBe('21st')
  })
})

// -- the eight-run eleventh, and what the summary is allowed to claim -------
//
// The retained COL-DET path (MLBAM 824224) scored eight in the top of the 11th
// across six scoring plate appearances. summarizePath returns LEAD CHANGES plus
// the final scoring play, which for that inning is a single row. That is correct
// behaviour and was displayed under the heading "Score changes in this path",
// where it looked like the list had silently dropped seven runs.

describe('the eleventh-inning summary', () => {
  const pa = (
    pos: number, inning: number, half: 'TOP' | 'BOTTOM',
    away: number, home: number, runs: number, outs: number,
  ) => ({
    pa: pos, inning, half, outcome: runs > 0 ? 'SINGLE' : 'BIP_OUT',
    outsBefore: Math.max(0, outs - (runs > 0 ? 0 : 1)), outsAfter: outs,
    baseMaskBefore: 0, baseMaskAfter: 0,
    awayScoreAfter: away, homeScoreAfter: home,
    runsScored: runs, battingOrderPosition: 1,
    pitcherState: 'TEAM_BULLPEN', pitchingChange: false,
  })

  // The real shape: tied 4-4 entering the 11th, away scores eight, home three.
  const events = [
    pa(78, 10, 'BOTTOM', 4, 4, 1, 3),
    pa(81, 11, 'TOP', 5, 4, 1, 0),
    pa(82, 11, 'TOP', 6, 4, 1, 1),
    pa(84, 11, 'TOP', 8, 4, 2, 1),
    pa(86, 11, 'TOP', 9, 4, 1, 1),
    pa(87, 11, 'TOP', 10, 4, 1, 2),
    pa(88, 11, 'TOP', 12, 4, 2, 2),
    pa(89, 11, 'TOP', 12, 4, 0, 3),
    pa(91, 11, 'BOTTOM', 12, 5, 1, 0),
    pa(93, 11, 'BOTTOM', 12, 6, 1, 1),
    pa(95, 11, 'BOTTOM', 12, 7, 1, 2),
    pa(96, 11, 'BOTTOM', 12, 7, 0, 3),
  ] as never[]

  it('counts every scoring plate appearance, not just the listed ones', () => {
    const summary = summarizePath(events)!
    // Ten scoring plate appearances across the tenth and eleventh; the
    // lead-change list is far shorter, which is exactly why the old heading
    // made a correct trace look like it had lost runs.
    expect(summary.scoringPlays).toHaveLength(10)
    expect(summary.turningPoints.length).toBeLessThan(summary.scoringPlays.length)
  })

  it('reaches the recorded final and stops there', () => {
    const summary = summarizePath(events)!
    expect([summary.awayFinal, summary.homeFinal]).toEqual([12, 7])
    expect(summary.innings).toBe(11)
    expect(summary.extraInnings).toBe(true)
  })

  it('never reports a run total the events do not support', () => {
    const summary = summarizePath(events)!
    const scored = summary.scoringPlays.reduce((total, event) => total + event.runsScored, 0)
    // The fixture starts at 4-3 and ends 12-7: eight away runs and four home,
    // every one of them present in the scoring plays.
    expect(scored).toBe((12 - 4) + (7 - 3))
    expect(scored).toBe(12)
  })

  it('keeps the last scoring play so the list ends at the final score', () => {
    const summary = summarizePath(events)!
    const last = summary.turningPoints[summary.turningPoints.length - 1]
    expect([last.awayScoreAfter, last.homeScoreAfter]).toEqual([12, 7])
  })
})
