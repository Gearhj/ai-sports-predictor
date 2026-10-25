import { describe, expect, it } from 'vitest'
import type { NhlGameDetail } from '../../types/nhlResearch'
import game from '../../test/gamenight-captured/nhl-cbj-min.json'
import { gridSums, massOutside, nhlRegulationGrid, poissonGrid, sampleGrid, verifyAgainstStored } from './nhlGrid'
import { stormFromNhl } from './gameNightData'

const LAMBDA_HOME = 3.1707671616724293
const LAMBDA_AWAY = 2.6187652493121525
const STORED = { home: 0.5059226757037447, tie: 0.16579811019376997, away: 0.3282792141024853 }

describe('NHL regulation grid derived from the stored expected goals', () => {
  it('reproduces the stored regulation probabilities over 0–15 goals', () => {
    const grid = poissonGrid(LAMBDA_HOME, LAMBDA_AWAY, 15)
    const sums = gridSums(grid)
    expect(Math.abs(sums.home - 0.50592)).toBeLessThan(1e-4)
    expect(Math.abs(sums.tie - 0.16580)).toBeLessThan(1e-4)
    expect(Math.abs(sums.away - 0.32828)).toBeLessThan(1e-4)
    expect(verifyAgainstStored(grid, STORED).ok).toBe(true)
  })

  it('fails the check when truncated at 10 goals (the truncation trap)', () => {
    const grid = poissonGrid(LAMBDA_HOME, LAMBDA_AWAY, 10)
    expect(gridSums(grid).home).toBeCloseTo(0.50546, 5)
    expect(verifyAgainstStored(grid, STORED).ok).toBe(false)
  })

  it('is unavailable when a stored value does not match', () => {
    const r = nhlRegulationGrid({ home: LAMBDA_HOME, away: LAMBDA_AWAY }, { ...STORED, home: 0.52 })
    expect(r.ok).toBe(false)
    expect(nhlRegulationGrid(null, STORED).ok).toBe(false)
  })

  it('states the mass outside the drawn 0–12 window', () => {
    const outside = massOutside(poissonGrid(LAMBDA_HOME, LAMBDA_AWAY, 15), 12)
    expect(outside).toBeGreaterThan(0)
    expect(outside).toBeLessThan(1e-4)
  })

  it('samples 10,000 draws deterministically for a seed', () => {
    const grid = poissonGrid(LAMBDA_HOME, LAMBDA_AWAY, 15)
    const a = sampleGrid(grid, 10_000, 5)
    expect(a.order).toHaveLength(10_000)
    expect(a.cells.reduce((s, c) => s + c.count, 0)).toBe(10_000)
    expect(sampleGrid(grid, 10_000, 5).order).toEqual(a.order)
    expect(sampleGrid(grid, 10_000, 6).order).not.toEqual(a.order)
  })

  it('builds a sampled stage source from the captured CBJ @ MIN record', () => {
    const source = stormFromNhl('e3cfbae6-febc-474e-824e-dba3a08c35ca', game as unknown as NhlGameDetail)
    expect(source.population?.mode).toBe('sampled')
    expect(source.population?.drawCount).toBe(10_000)
    expect(source.population?.window).toEqual({ homeMax: 12, awayMax: 12 })
    expect(source.population?.nhl?.lambdaHome).toBeCloseTo(3.1708, 4)
    expect(source.split.away).toBeCloseTo(0.40905, 5)
    expect(source.regulation?.tie).toBeCloseTo(0.1658, 4)
    expect(source.final).toBeNull()
  })
})
