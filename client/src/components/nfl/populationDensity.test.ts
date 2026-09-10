/**
 * The density layer describes 151,000 plays nobody can inspect, so the ways it
 * can mislead are the ways worth testing: quietly dropping the plays it cannot
 * place, and describing a wider population than the paths drawn over it.
 */
import { describe, expect, it } from 'vitest'
import { buildDensity } from './populationDensity'
import type { NflPopulationSpatial } from '../../types/nfl'

function spatial(cells: NflPopulationSpatial['cells']): NflPopulationSpatial {
  const playCount = cells.reduce((sum, cell) => sum + cell.count, 0)
  return {
    schemaVersion: 'NFL_POPULATION_SPATIAL_V1',
    basis: 'ALL_SIMULATED_PLAYS_IN_POPULATION',
    games: 1000,
    playCount,
    reconciliation: {
      populationAccountingPlays: playCount, binnedPlayCount: playCount, reconciles: true,
      excludedPlayTypes: ['PERIOD_END'], excludedPlayCount: 4031, statement: '',
    },
    fieldDirection: { convention: 'OFFENSE_RELATIVE_YARDLINE100_NO_MIRRORING_REQUIRED', statement: '' },
    bins: {},
    byPlayType: {},
    cells,
    cellCount: cells.length,
    limitations: [],
  }
}

describe('buildDensity', () => {
  it('returns nothing when the artifact carries no summary', () => {
    // A V1 artifact is carried forward unchanged for a game past kickoff. It
    // must render as "no band", never as an empty one.
    expect(buildDensity(null, null)).toBeNull()
    expect(buildDensity(spatial([]), null)).toBeNull()
  })

  it('folds cells of the same family and yard bin together', () => {
    const layer = buildDensity(spatial([
      { playType: 'RUSH', start: 50, yards: 0, count: 10 },
      { playType: 'QB_SCRAMBLE', start: 50, yards: 5, count: 4 },
      { playType: 'PASS_COMPLETE', start: 50, yards: 10, count: 7 },
    ]), null)!
    // RUSH and QB_SCRAMBLE are both the RUN family, so they share a cell.
    expect(layer.cells).toHaveLength(2)
    expect(layer.cells.find((c) => c.family === 'RUN')!.count).toBe(14)
    expect(layer.cells.find((c) => c.family === 'PASS')!.count).toBe(7)
    expect(layer.plays).toBe(21)
  })

  it('counts unplaceable plays instead of dropping or clipping them', () => {
    // UNDERFLOW / OVERFLOW / NA carry no field position. Dropping them would
    // make the band describe fewer plays than it claims; clipping them to an
    // edge would put them somewhere they never were.
    const layer = buildDensity(spatial([
      { playType: 'RUSH', start: 50, yards: 0, count: 10 },
      { playType: 'PUNT', start: 'UNDERFLOW', yards: 'NA', count: 3 },
      { playType: 'KICKOFF', start: 'OVERFLOW', yards: 'NA', count: 2 },
    ]), null)!
    expect(layer.plays).toBe(15)
    expect(layer.unplaced).toBe(5)
    expect(layer.cells).toHaveLength(1)
    expect(layer.cells[0].count).toBe(10)
  })

  it('narrows to the same families as the drawn paths', () => {
    const cells: NflPopulationSpatial['cells'] = [
      { playType: 'RUSH', start: 50, yards: 0, count: 10 },
      { playType: 'PASS_COMPLETE', start: 50, yards: 10, count: 7 },
    ]
    const passOnly = buildDensity(spatial(cells), new Set(['PASS'] as const))!
    expect(passOnly.plays).toBe(7)
    expect(passOnly.cells).toHaveLength(1)
    expect(passOnly.cells[0].family).toBe('PASS')
  })

  it('mirrors an offense-relative bin onto both absolute field positions', () => {
    // start is yards to the goal the offense attacks, so a bin at 20 describes
    // plays at absolute x=20 AND x=80. The layer keeps one cell and the painter
    // mirrors it; what matters here is that the bin is kept as supplied.
    const layer = buildDensity(spatial([
      { playType: 'RUSH', start: 20, yards: 3, count: 9 },
    ]), null)!
    expect(layer.cells[0].x).toBe(20)
    expect(layer.cells[0].width).toBe(5)
  })

  it('scales opacity against the largest cell, not the total', () => {
    const layer = buildDensity(spatial([
      { playType: 'RUSH', start: 50, yards: 0, count: 100 },
      { playType: 'RUSH', start: 10, yards: 0, count: 5 },
    ]), null)!
    expect(layer.peak).toBe(100)
  })
})
