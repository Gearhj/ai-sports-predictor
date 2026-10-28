import { describe, expect, it } from 'vitest'
import { fairOdds, leaves, route, setupFor } from './plinkoTree'
import type { SimModel } from './simData'

const cell = (home: number, away: number, p: number) => ({ home, away, p })
const model = (cells: ReturnType<typeof cell>[], spreadHome: number | null = -3.5, total: number | null = 44.5) => ({
  sport: 'NFL', home: { abbr: 'GB', name: 'GB' }, away: { abbr: 'CAR', name: 'CAR' }, cells,
  win: { home: 0.7, away: 0.28, tie: 0.02 }, bookLines: { spreadHome, total, fresh: true },
} as unknown as SimModel)

describe('decision tree', () => {
  const m = model([cell(31, 24, 0.3), cell(24, 21, 0.25), cell(17, 20, 0.2), cell(10, 31, 0.15), cell(20, 20, 0.1)])
  const s = setupFor(m)

  it('reads the favourite and the book lines', () => {
    expect(s.fav).toBe('home')
    expect(s.spread).toBe(3.5)
    expect(s.total).toBe(44.5)
    expect(s.spreadFromBook).toBe(true)
  })

  it('routes each game through the three questions into ordered leaves', () => {
    expect(route(cell(31, 24, 1), s)).toMatchObject({ group: 3, over: true, leaf: 7 })   // covers, 55 is over
    expect(route(cell(24, 21, 1), s)).toMatchObject({ group: 2, over: true, leaf: 5 })   // by 3: no cover
    expect(route(cell(17, 20, 1), s)).toMatchObject({ group: 1, over: false, leaf: 2 })  // dog by 3: close
    expect(route(cell(10, 31, 1), s)).toMatchObject({ group: 0, over: false, leaf: 0 })  // dog by 21: a rout
    expect(route(cell(20, 20, 1), s)).toMatchObject({ group: 1, leaf: 2 })               // a tie counts as close
  })

  it('leaves sum to 1 and carry their most likely score', () => {
    const l = leaves(m.cells, s)
    expect(l.reduce((a, x) => a + x.p, 0)).toBeCloseTo(1, 9)
    expect(l[7].p).toBeCloseTo(0.3, 9)
    expect(l[7].top).toEqual(cell(31, 24, 0.3))
    expect(l[2].p).toBeCloseTo(0.3, 9)
  })

  it('falls back to model lines and honours overrides', () => {
    const n = setupFor(model(m.cells, null, null))
    expect(n.spreadFromBook).toBe(false)
    expect(n.spread % 1).toBe(0.5)
    expect(setupFor(m, { spread: 7, total: 40 })).toMatchObject({ spread: 7, total: 40, spreadFromBook: false })
  })

  it('prices fair odds', () => {
    expect(fairOdds(0.5)).toBe(-100)
    expect(fairOdds(0.25)).toBe(300)
    expect(fairOdds(0)).toBeNull()
  })
})
