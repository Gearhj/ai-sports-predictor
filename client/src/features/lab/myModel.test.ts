import { describe, expect, it } from 'vitest'
import { READS, applyModel, defaultsFor, loadModel, readEffect, saveModel } from './myModel'

describe('my model', () => {
  it('defaults reproduce AISP exactly; trust 0 is the market', () => {
    const f = defaultsFor('NHL')
    expect(applyModel('NHL', f, 0.6, 0.5).p).toBeCloseTo(0.6, 4)
    expect(applyModel('NHL', { ...f, trust: 0 }, 0.6, 0.5).p).toBeCloseTo(0.5, 4)
    const half = applyModel('NHL', { ...f, trust: 0.5 }, 0.6, 0.5).p
    expect(half).toBeGreaterThan(0.5); expect(half).toBeLessThan(0.6)
  })
  it('injuries and home edge move the number the right way', () => {
    const f = defaultsFor('NBA')
    const base = applyModel('NBA', f, 0.55, 0.55).p
    expect(applyModel('NBA', f, 0.55, 0.55, [{ side: 'HOME', status: 'OUT', name: 'x' }]).p).toBeLessThan(base - 0.05)
    expect(applyModel('NBA', f, 0.55, 0.55, [{ side: 'AWAY', status: 'QUESTIONABLE', name: 'y' }]).p).toBeGreaterThan(base)
    expect(applyModel('NBA', { ...f, homeEdge: 2 }, 0.55, 0.55).p).toBeGreaterThan(base)
  })
  it('confidence stretches favourites and shrinks them below 1', () => {
    const f = defaultsFor('NFL')
    expect(applyModel('NFL', { ...f, confidence: 0.8 }, 0.7, null).p).toBeLessThan(0.7)
    expect(applyModel('NFL', { ...f, confidence: 1.2 }, 0.7, null).p).toBeGreaterThan(0.7)
    expect(applyModel('NFL', { ...f, confidence: 0.8 }, 0.5, null).p).toBeCloseTo(0.5, 6)
  })
  it('saves per sport and bumps the version', () => {
    localStorage.removeItem('aisp-my-model-v1')
    saveModel('NHL', { ...defaultsFor('NHL'), trust: 0.4 })
    const m = saveModel('NBA', defaultsFor('NBA'))
    expect(m.version).toBe(2)
    expect(loadModel()?.sports.NHL.trust).toBe(0.4)
  })
})

describe('matchup reads (sport-specific)', () => {
  it('every sport has its own reads, each with a margin or total effect', () => {
    for (const s of ['NFL', 'CFB', 'NBA', 'NHL', 'MLB']) expect(READS[s].length).toBeGreaterThanOrEqual(4)
    expect(READS.NHL.map((d) => d.key)).toContain('goalie')
    expect(READS.MLB.map((d) => d.key)).toContain('sp')
    expect(READS.NFL.map((d) => d.key)).toContain('qb')
  })
  it('a margin read toward the home team raises the home chance; a total read leaves it alone', () => {
    const f = defaultsFor('NHL')
    const base = applyModel('NHL', f, 0.5, null).p
    expect(applyModel('NHL', f, 0.5, null, [], { goalie: 0.4 }).p).toBeGreaterThan(base + 0.05)
    expect(applyModel('NHL', f, 0.5, null, [], { goalie: -0.4 }).p).toBeLessThan(base - 0.05)
    const t = applyModel('NHL', f, 0.5, null, [], { environment: 1 })
    expect(t.p).toBeCloseTo(base, 6)
    expect(t.totalShift).toBe(1)
  })
  it('scaled reads use the sport unit (an NFL turnover is about 4 points)', () => {
    expect(readEffect('NFL', { turnovers: 1 }).margin).toBe(4)
    expect(readEffect('NBA', { pace: 2 }).total).toBeCloseTo(4.4, 6)
  })
})
