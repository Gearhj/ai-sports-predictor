import { describe, expect, it } from 'vitest'
import {
  FIELD,
  angleForOutcome,
  buildDurationMs,
  buildTraceField,
  depthForTotal,
  filterThresholds,
  matchesFilter,
  subsetStats,
} from './diamondTraces'

/** A small joint distribution whose counts sum to a known population. */
function joint(cells: Array<[number, number, number]>, size = 8) {
  const counts = Array.from({ length: size }, () => Array.from({ length: size }, () => 0))
  const probabilities = Array.from({ length: size }, () => Array.from({ length: size }, () => 0))
  let total = 0
  for (const [home, away, count] of cells) { counts[home][away] = count; total += count }
  for (let h = 0; h < size; h++) for (let a = 0; a < size; a++) probabilities[h][a] = counts[h][a] / total
  return { counts, probabilities, total }
}

describe('trace population comes from the authoritative distribution', () => {
  it('renders exactly one trace per simulated outcome', () => {
    const { counts, probabilities, total } = joint([[4, 5, 1038], [5, 4, 900], [3, 2, 62]])
    const field = buildTraceField(probabilities, counts)
    expect(field.totalOutcomes).toBe(total)
    expect(field.renderedPrimitives).toBe(total)
    expect(field.traces).toHaveLength(total)
    expect(field.exhaustive).toBe(true)
  })

  it('reproduces each cell count exactly, inventing no outcome', () => {
    const { counts, probabilities } = joint([[4, 5, 1038], [5, 4, 900], [7, 6, 41]])
    const field = buildTraceField(probabilities, counts)
    const tally = new Map<string, number>()
    for (const t of field.traces) {
      const key = `${t.homeRuns}-${t.awayRuns}`
      tally.set(key, (tally.get(key) ?? 0) + 1)
    }
    expect(tally.get('4-5')).toBe(1038)
    expect(tally.get('5-4')).toBe(900)
    expect(tally.get('7-6')).toBe(41)
    expect(tally.size).toBe(3)
  })

  it('scales a 20,000-outcome population without loss at the default bound', () => {
    const { counts, probabilities } = joint([[4, 5, 12000], [5, 4, 8000]])
    const field = buildTraceField(probabilities, counts)
    expect(field.totalOutcomes).toBe(20000)
    expect(field.renderedPrimitives).toBe(20000)
    expect(field.exhaustive).toBe(true)
  })

  it('reports honestly when it must downsample rather than claiming completeness', () => {
    const { counts, probabilities } = joint([[4, 5, 60000], [5, 4, 40000]])
    const field = buildTraceField(probabilities, counts, 10_000)
    expect(field.totalOutcomes).toBe(100000)
    expect(field.exhaustive).toBe(false)
    expect(field.renderedPrimitives).toBeLessThanOrEqual(10_000)
    expect(field.renderedPrimitives).toBeGreaterThan(0)
  })

  it('never emits a tie, which the simulator cannot produce', () => {
    const { counts, probabilities } = joint([[4, 4, 500], [5, 4, 100]])
    const field = buildTraceField(probabilities, counts)
    expect(field.traces.every((t) => t.homeRuns !== t.awayRuns)).toBe(true)
    expect(field.traces).toHaveLength(100)
  })

  it('is deterministic — the same distribution yields identical geometry', () => {
    const { counts, probabilities } = joint([[4, 5, 300], [6, 3, 120]])
    const a = buildTraceField(probabilities, counts)
    const b = buildTraceField(probabilities, counts)
    expect(a.traces).toEqual(b.traces)
  })
})

describe('encoding', () => {
  it('sends home-team wins right and away-team wins left of centre field', () => {
    const { counts, probabilities } = joint([[6, 2, 10], [2, 6, 10]])
    const field = buildTraceField(probabilities, counts)
    const home = field.traces.filter((t) => t.winner === 'home')
    const away = field.traces.filter((t) => t.winner === 'away')
    expect(home.every((t) => t.x > FIELD.home.x)).toBe(true)
    expect(away.every((t) => t.x < FIELD.home.x)).toBe(true)
  })

  it('marks the winner from the score, never the other way round', () => {
    const { counts, probabilities } = joint([[7, 1, 5], [1, 7, 5]])
    for (const t of buildTraceField(probabilities, counts).traces) {
      expect(t.winner).toBe(t.homeRuns > t.awayRuns ? 'home' : 'away')
      expect(t.differential).toBe(t.homeRuns - t.awayRuns)
    }
  })

  it('places higher totals deeper into the outfield', () => {
    expect(depthForTotal(3, 20)).toBeLessThan(depthForTotal(9, 20))
    expect(depthForTotal(9, 20)).toBeLessThan(depthForTotal(18, 20))
  })

  it('keeps every trace between the infield edge and the fence', () => {
    const { counts, probabilities } = joint([[1, 0, 20], [12, 11, 20], [5, 4, 20]], 14)
    for (const t of buildTraceField(probabilities, counts).traces) {
      const r = Math.hypot(t.x - FIELD.home.x, t.y - FIELD.home.y)
      expect(r).toBeGreaterThan(FIELD.infieldRadius - 20)
      expect(r).toBeLessThanOrEqual(FIELD.fenceRadius)
    }
  })

  it('opens the angle as the run differential grows', () => {
    expect(Math.abs(angleForOutcome(1))).toBeLessThan(Math.abs(angleForOutcome(4)))
    expect(Math.abs(angleForOutcome(4))).toBeLessThan(Math.abs(angleForOutcome(8)))
    expect(angleForOutcome(3)).toBeGreaterThan(0)
    expect(angleForOutcome(-3)).toBeLessThan(0)
  })

  it('keeps every trace inside fair territory', () => {
    const { counts, probabilities } = joint([[12, 0, 30], [0, 12, 30]], 14)
    for (const t of buildTraceField(probabilities, counts).traces) {
      const angle = Math.atan2(t.x - FIELD.home.x, FIELD.home.y - t.y)
      expect(Math.abs(angle)).toBeLessThan(FIELD.foulAngle)
    }
  })

  it('spreads identical scores without changing the score they encode', () => {
    const { counts, probabilities } = joint([[5, 4, 400]])
    const traces = buildTraceField(probabilities, counts).traces
    expect(new Set(traces.map((t) => `${t.x.toFixed(3)},${t.y.toFixed(3)}`)).size).toBeGreaterThan(50)
    expect(traces.every((t) => t.homeRuns === 5 && t.awayRuns === 4)).toBe(true)
  })
})

describe('filters', () => {
  const { counts, probabilities } = joint([
    [5, 4, 1000], [4, 5, 900], [9, 2, 100], [1, 0, 200], [8, 7, 150],
  ], 12)
  const field = buildTraceField(probabilities, counts)
  const thresholds = filterThresholds(field.traces)

  it('counts a subset and its share of the population', () => {
    const stats = subsetStats(field.traces, 'home_win', thresholds)
    expect(stats.count).toBe(1000 + 100 + 200 + 150)
    expect(stats.share).toBeCloseTo(stats.count / field.totalOutcomes, 10)
    expect(stats.homeWinShare).toBe(1)
  })

  it('splits home and away wins exhaustively', () => {
    const home = subsetStats(field.traces, 'home_win', thresholds).count
    const away = subsetStats(field.traces, 'away_win', thresholds).count
    expect(home + away).toBe(field.totalOutcomes)
  })

  it('selects the high- and low-scoring tails on total runs', () => {
    const high = field.traces.filter((t) => matchesFilter(t, 'high_scoring', thresholds))
    const low = field.traces.filter((t) => matchesFilter(t, 'low_scoring', thresholds))
    expect(Math.min(...high.map((t) => t.totalRuns)))
      .toBeGreaterThanOrEqual(Math.max(...low.map((t) => t.totalRuns)))
  })

  it('treats extra innings as one-run finishes only', () => {
    const extra = field.traces.filter((t) => matchesFilter(t, 'extra_innings', thresholds))
    expect(extra.every((t) => Math.abs(t.differential) === 1)).toBe(true)
    // 5-4, 4-5, 8-7 and 1-0 are all one-run finishes.
    expect(extra.length).toBe(1000 + 900 + 150 + 200)
  })

  it('reports the modal score of the selected subset', () => {
    const stats = subsetStats(field.traces, 'all', thresholds)
    expect(stats.modalScore).toEqual({ home: 5, away: 4, count: 1000 })
  })

  it('describes the subset, not the whole population', () => {
    const all = subsetStats(field.traces, 'all', thresholds)
    const away = subsetStats(field.traces, 'away_win', thresholds)
    expect(away.meanHomeRuns).not.toBeCloseTo(all.meanHomeRuns, 3)
    expect(away.homeWinShare).toBe(0)
  })

  it('returns an empty, non-NaN subset when nothing matches', () => {
    const empty = subsetStats([], 'all', thresholds)
    expect(empty.count).toBe(0)
    expect(empty.share).toBe(0)
    expect(Number.isNaN(empty.meanTotalRuns)).toBe(false)
    expect(empty.modalScore).toBeNull()
  })
})

describe('build animation timing', () => {
  it('completes within two seconds at normal speed', () => {
    expect(buildDurationMs(1, false)).toBeLessThanOrEqual(2000)
    expect(buildDurationMs(1, false)).toBeGreaterThan(1000)
  })

  it('completes within one second at 2x', () => {
    expect(buildDurationMs(2, false)).toBeLessThanOrEqual(1000)
    expect(buildDurationMs(2, false)).toBeGreaterThan(500)
  })

  it('renders immediately under reduced motion', () => {
    expect(buildDurationMs(1, true)).toBe(0)
    expect(buildDurationMs(2, true)).toBe(0)
  })

  it('is never a per-draw playlist — 20,000 outcomes cannot take 20,000 steps', () => {
    // A one-draw-per-tick metaphor would need >>2s. The build is a fixed
    // short duration regardless of population size.
    expect(buildDurationMs(1, false)).toBeLessThan(20000 / 10)
  })
})
