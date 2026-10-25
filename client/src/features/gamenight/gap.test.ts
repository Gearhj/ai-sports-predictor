import { describe, expect, it } from 'vitest'
import type { DecisionBoard } from '../../services/commandCenter'
import decisions from '../../test/gamenight-captured/decisions-2026-10-18.json'
import {
  GAP_WARNING_PTS, basisCaption, basisTag, breakEven, comparisonProbability, decisionGap, gapPts, gapText, impliedProb, isDisplayWarning,
  isModelWarning, normalizeResult, sameBookNoVig, seededBootstrapRoi, zoneTally, type GapBet,
} from './gap'

const board = decisions as unknown as DecisionBoard

function gapOf(sport: string, sel: number, opp: number, p: number, tie: number | null) {
  const noVig = sameBookNoVig(sel, opp)!
  const cmp = comparisonProbability(sport, p, tie)!
  return { noVig, cmp: cmp.p, basis: cmp.basis, gap: gapPts(cmp.p, noVig) }
}

describe('same-book no-vig and the model-warning gap', () => {
  it('prices American odds', () => {
    expect(impliedProb(-130)).toBeCloseTo(130 / 230, 12)
    expect(impliedProb(110)).toBeCloseTo(100 / 210, 12)
    expect(impliedProb(100)).toBe(0.5)
    expect(impliedProb(null)).toBeNull()
    expect(impliedProb(0)).toBeNull()
  })

  it('JAX −130 / +110 at .7089 with a .0227 tie: no-vig .5427, gap +18.3, a model warning', () => {
    const g = gapOf('NFL', -130, 110, 0.7089, 0.0227)
    expect(g.noVig).toBeCloseTo(0.5427, 4)
    expect(g.basis).toBe('NON_TIE_DERIVED')
    expect(gapText(g.gap)).toBe('+18.3 pts')
    expect(isModelWarning(g.gap)).toBe(true)
  })

  it('ARI +613 / −900 at .2122 with a .0293 tie: no-vig .1348, two-way .2186, gap +8.4', () => {
    const g = gapOf('NFL', 613, -900, 0.2122, 0.0293)
    expect(g.noVig).toBeCloseTo(0.1348, 4)
    expect(g.cmp).toBeCloseTo(0.2186, 4)
    expect(gapText(g.gap)).toBe('+8.4 pts')
    expect(isModelWarning(g.gap)).toBe(false)
  })

  it('GB +100 / −120 at .5408 with a .0284 tie: +7.8', () => {
    expect(gapText(gapOf('NFL', 100, -120, 0.5408, 0.0284).gap)).toBe('+7.8 pts')
  })

  it('CBJ +165 / −195: the derived no-vig equals the stored one (NHL has no tie adjustment)', () => {
    const g = gapOf('NHL', 165, -195, 0.4090496117279836, null)
    expect(Math.abs(g.noVig - 0.36341238065906994)).toBeLessThan(1e-6)
    expect(g.basis).toBe('RAW_NO_TIE_OUTCOME')
    expect(gapText(g.gap)).toBe('+4.6 pts')
  })

  it('CGY +200 / −240 at .4240: +10.3, a model warning', () => {
    const g = gapOf('NHL', 200, -240, 0.42403550670799134, null)
    expect(gapText(g.gap)).toBe('+10.3 pts')
    expect(isModelWarning(g.gap)).toBe(true)
  })

  it('warns only strictly above 10 points as displayed; exactly 10.0 does not', () => {
    expect(GAP_WARNING_PTS).toBe(10)
    expect(isModelWarning(10)).toBe(false)
    expect(isModelWarning(10.04)).toBe(false)
    expect(isModelWarning(10.06)).toBe(true)
    expect(isModelWarning(-12)).toBe(false)
    expect(isModelWarning(null)).toBe(false)
  })

  it('an NFL decision without a tie probability is compared raw and says so', () => {
    expect(comparisonProbability('NFL', 0.6, null)).toEqual({ p: 0.6, basis: 'TIE_PROBABILITY_NOT_RECORDED' })
  })

  it('break-even for +613 is 14.0%', () => {
    expect(breakEven(613)).toBeCloseTo(0.1403, 4)
  })

  it('a decision without a two-sided pair has no gap (it is unplaced, never estimated)', () => {
    const declined = board.items.find((i) => i.selection === 'IND ML')!
    expect(declined.market_integrity?.opposite_american).toBeNull()
    expect(decisionGap(declined)).toBeNull()
  })
})

describe('tonight\'s zones from the captured 10-18 decision board', () => {
  const bets: GapBet[] = board.items.filter((i) => i.position).map((i) => ({
    key: i.position!.position_id, gap: decisionGap(i)?.gap ?? null,
    result: normalizeResult(i.position!.status, i.position!.result), stake: i.position!.stake, profit: i.position!.profit_loss,
  }))

  it('derives each zone from the stored pairs', () => {
    const z = zoneTally(bets)
    // Every settled bet lands in exactly one zone.
    const settled = bets.filter((b) => b.result !== 'OPEN')
    expect(z.corridor.settled + z.warning.settled).toBe(settled.length)
    expect(z.unplaced).toBe(0)
    // Recomputed by hand from the same items.
    const warnSettled = settled.filter((b) => Math.round(b.gap! * 10) / 10 > 10)
    expect(z.warning.settled).toBe(warnSettled.length)
    expect(z.warning.wins).toBe(warnSettled.filter((b) => b.result === 'WIN').length)
    expect(z.warning.profit).toBeCloseTo(warnSettled.reduce((s, b) => s + (b.profit ?? 0), 0), 6)
    expect(z.corridor.roi).toBeCloseTo(z.corridor.profit / z.corridor.stake, 9)
    // In this capture: 4–0 inside the corridor, 0–5 in the warning zone.
    expect([z.corridor.wins, z.corridor.losses, z.warning.wins, z.warning.losses]).toEqual([4, 0, 0, 5])
    expect(z.corridor.profit).toBeCloseTo(37.96, 2)
    expect(z.warning.profit).toBeCloseTo(-25, 2)
  })

  it('counts open bets in a zone without settling them', () => {
    const z = zoneTally(bets)
    expect(z.corridor.open + z.warning.open).toBe(bets.filter((b) => b.result === 'OPEN').length)
  })
})

describe('seeded bootstrap', () => {
  const rows = [
    { stake: 5, profit: 30.65 }, { stake: 5, profit: -5 }, { stake: 5, profit: 1.28 }, { stake: 5, profit: -5 },
    { stake: 5, profit: 4.55 }, { stake: 5, profit: -5 }, { stake: 2.5, profit: 2.6 }, { stake: 5, profit: -5 },
  ]
  it('is deterministic for a seed and brackets the observed ROI', () => {
    const a = seededBootstrapRoi(rows, 2000, 20261017)
    const b = seededBootstrapRoi(rows, 2000, 20261017)
    expect(a).toEqual(b)
    const roi = rows.reduce((s, r) => s + r.profit, 0) / rows.reduce((s, r) => s + r.stake, 0)
    expect(a![0]).toBeLessThan(roi)
    expect(a![1]).toBeGreaterThan(roi)
    expect(seededBootstrapRoi(rows, 2000, 7)).not.toEqual(a)
  })
  it('needs at least two settled bets', () => {
    expect(seededBootstrapRoi([{ stake: 5, profit: 1 }])).toBeNull()
  })
})

describe('captions follow the basis each gap was computed on', () => {
  const item = board.items.find((i) => i.position && i.sport === 'NFL' && i.market_integrity)!

  it('says tie-adjusted only when the NFL tie probability was recorded', () => {
    const g = decisionGap(item)!
    expect(g.basis).toBe('NON_TIE_DERIVED')
    expect(basisCaption(g)).toMatch(/tie-adjusted/)
    expect(basisTag(g)).toBe(' (two-way)')
    const raw = decisionGap({ ...item, edge_metrics: null })!
    expect(raw.basis).toBe('TIE_PROBABILITY_NOT_RECORDED')
    expect(basisCaption(raw)).toMatch(/not recorded/)
    expect(basisCaption(raw)).not.toMatch(/tie-adjusted/)
    expect(basisTag(raw)).toMatch(/raw/)
    expect(basisCaption({ sport: 'NHL', basis: 'RAW_NO_TIE_OUTCOME' })).toBe('')
  })

  it('reads freshness at decision as unknown when the field is missing, never as fresh', () => {
    const mi = { ...item.market_integrity! } as Record<string, unknown>
    delete mi.fresh_at_decision
    expect(decisionGap({ ...item, market_integrity: mi as never })!.freshAtDecision).toBeNull()
    expect(decisionGap({ ...item, market_integrity: { ...item.market_integrity!, fresh_at_decision: false } })!.freshAtDecision).toBe(false)
  })

  it('flags a single game more than 10 points either way, while the tallies stay one-sided', () => {
    expect(isDisplayWarning(-10.3)).toBe(true)
    expect(isDisplayWarning(10.3)).toBe(true)
    expect(isDisplayWarning(-10)).toBe(false)
    expect(isModelWarning(-10.3)).toBe(false)
  })
})
