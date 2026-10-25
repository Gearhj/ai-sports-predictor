import { describe, expect, it } from 'vitest'
import story from '../../test/gamenight-captured/story-ari-lar.json'
import cfbStory from '../../test/gamenight-captured/story-cfb-ballst-bgsu.json'
import {
  axisRange, cellColor, cellIndexOf, expandShuffle, launchedBy, maxCount, rankOf, reachableCount, replayDuration,
  sideCounts, topScores, totalCount, type TallyCell,
} from './stormTally'

const cells = (story as { population: { cells: TallyCell[]; draw_count: number } }).population.cells

describe('the ARI @ LAR stored tally (captured 00:25Z)', () => {
  it('has 701 cells summing to the 10,000 stored draws', () => {
    expect(cells).toHaveLength(701)
    expect(totalCount(cells)).toBe(10_000)
    expect((story as { population: { draw_count: number } }).population.draw_count).toBe(10_000)
  })

  it('shuffles the exact multiset: every cell keeps exactly its stored count', () => {
    const order = expandShuffle(cells, 1)
    expect(order).toHaveLength(10_000)
    const counts = new Array(cells.length).fill(0)
    for (const i of order) counts[i] += 1
    expect(counts).toEqual(cells.map((c) => c.count))
  })

  it('is seeded: the same seed repeats, another seed reorders', () => {
    const a = expandShuffle(cells, 42)
    expect(expandShuffle(cells, 42)).toEqual(a)
    const b = expandShuffle(cells, 43)
    expect(b).not.toEqual(a)
    expect([...b].sort((x, y) => x - y)).toEqual([...a].sort((x, y) => x - y))
  })

  it('ranks the real final: ARI 18 – 16 LAR drew 3 times, 579th most likely of 701', () => {
    const i = cellIndexOf(cells, 16, 18)
    expect(cells[i].count).toBe(3)
    expect(rankOf(cells, i)).toBe(579)
    expect(cellIndexOf(cells, 99, 0)).toBe(-1)
    expect(rankOf(cells, -1)).toBeNull()
  })

  it('counts ARI wins and ties from the tally', () => {
    const s = sideCounts(cells)
    expect(s.away).toBe(2122)
    expect(s.home + s.away + s.tie).toBe(10_000)
  })

  it('filters by a live score without the clock', () => {
    expect(reachableCount(cells, { away: 3, home: 7 })).toBe(9854)
    expect(reachableCount(cells, { away: 0, home: 0 })).toBe(10_000)
  })

  it('spans home 0–56 and away 0–48', () => {
    expect(axisRange(cells)).toEqual({ homeMax: 56, awayMax: 48 })
  })

  it('lists the three most drawn scores', () => {
    const top = topScores(cells, 3)
    expect(top[0]).toEqual({ away: 17, home: 28, count: 78 })
    expect(top.map((c) => c.count)).toEqual([78, 72, 72])
  })
})

describe('the Ball State @ Bowling Green College Football tally (forecast made 10-11 for 10-17)', () => {
  const cfb = (cfbStory as { population: { cells: TallyCell[] }; game: { final: { home: number; away: number } } })
  it('replays every stored draw and finds the real 32–31 final', () => {
    const c = cfb.population.cells
    expect(totalCount(c)).toBe(10_000)
    expect(axisRange(c)).toEqual({ homeMax: 79, awayMax: 75 })
    const i = cellIndexOf(c, cfb.game.final.home, cfb.game.final.away)
    expect(c[i].count).toBe(9)
    expect(rankOf(c, i)).toBe(1 + c.filter((x) => x.count > 9).length)
  })
})

describe('colour and pacing', () => {
  it('colours from the exact count, brightest at the maximum', () => {
    const max = maxCount(cells)
    expect(cellColor(max, max, 'pick')).toBe('rgb(63,216,196)')
    expect(cellColor(3, max, 'pick')).toBe(cellColor(3, max, 'pick'))
    expect(cellColor(3, max, 'pick')).not.toBe(cellColor(4, max, 'pick'))
    expect(cellColor(0, max, 'tie')).toBe('rgb(12,24,41)')
  })

  it('launches the first 40 draws in about three seconds, then all 10,000 in about nine', () => {
    expect(launchedBy(0, 10_000)).toBe(0)
    expect(launchedBy(1, 10_000)).toBe(13)
    expect(launchedBy(40 / 13 - 0.01, 10_000)).toBeLessThanOrEqual(40)
    expect(40 / 13).toBeLessThan(3.2)
    const d = replayDuration(10_000)
    expect(d).toBeGreaterThan(8)
    expect(d).toBeLessThan(13)
    expect(launchedBy(d + 0.01, 10_000)).toBe(10_000)
    let prev = 0
    for (let t = 0; t < d; t += 0.25) { const n = launchedBy(t, 10_000); expect(n).toBeGreaterThanOrEqual(prev); prev = n }
  })
})
