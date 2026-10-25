import { describe, expect, it } from 'vitest'
import type { BankrollPoint, Ledger } from '../../services/commandCenter'
import ledgerJson from '../../test/gamenight-captured/ledger.json'
import {
  LIVE_WINDOW_MS, allTimeHigh, etDayAt, lastCrossingAbove, lowPoint, momentsFor, nightStartOf, pointKey, settledOnDay, settledSince,
  settlementEvents, settlementRuns, splitLiveLate, sportRoi, watchLedger,
} from './events'
import { ledgerPositions } from './gameNightData'

const ledger = ledgerJson as unknown as Ledger
const series = ledger.combined_series

describe('settlement detection', () => {
  const before = series.slice(0, -1)
  const latest = series[series.length - 1]

  it('takes the first fresh ledger as the baseline and fires nothing', () => {
    const r = watchLedger(null, series, false)
    expect(r.fresh).toEqual([])
    expect(r.baseline?.size).toBe(series.filter((p) => p.kind === 'SETTLEMENT').length)
  })

  it('never baselines (or diffs) a view restored from browser storage', () => {
    const restored = watchLedger(null, before, true)
    expect(restored.baseline).toBeNull()
    expect(restored.fresh).toEqual([])
    // The first fresh read after a restore is the baseline: no burst of old "just settled" moments.
    expect(watchLedger(restored.baseline, series, false).fresh).toEqual([])
  })

  it('fires one event for one new settlement point', () => {
    const base = watchLedger(null, before, false).baseline
    const next = watchLedger(base, series, false)
    expect(next.fresh).toEqual([latest])
    expect(watchLedger(next.baseline, series, false).fresh).toEqual([])
  })

  it('fires both of two settlements that share one clock', () => {
    const shared = series.filter((p) => p.at === '2026-10-18T23:51:32.140708+00:00')
    expect(shared).toHaveLength(2)
    const cut = series.indexOf(shared[0])
    const base = watchLedger(null, series.slice(0, cut), false).baseline
    const fresh = watchLedger(base, series.slice(0, cut + 2), false).fresh
    expect(fresh.map(pointKey)).toEqual(shared.map(pointKey))
    expect(settlementEvents(series.slice(0, cut), series.slice(0, cut + 2))).toHaveLength(2)
  })

  it('sends a point detected more than 15 minutes after it settled to the replay reel', () => {
    const at = Date.parse(latest.at!)
    expect(splitLiveLate([latest], at + 60_000).live).toEqual([latest])
    const late = splitLiveLate([latest], at + LIVE_WINDOW_MS + 1)
    expect(late.live).toEqual([])
    expect(late.late).toEqual([latest])
  })
})

describe('bankroll landmarks from the captured ledger', () => {
  it('finds the all-time high and the record that stood before its run', () => {
    const ath = allTimeHigh(series)!
    expect(ath.bankroll).toBe(5006.71)
    expect(ath.previous?.bankroll).toBe(5005.08)
    expect(ath.previous?.at?.startsWith('2026-09-02')).toBe(true)
    expect(Math.max(...series.map((p) => p.bankroll))).toBe(ath.bankroll)
  })

  it('finds the low', () => {
    const low = lowPoint(series)!
    expect(low.bankroll).toBe(4936.61)
    expect(Math.min(...series.map((p) => p.bankroll))).toBe(low.bankroll)
  })

  it('finds the last time above $5,000 before the latest dip', () => {
    const c = lastCrossingAbove(series, 5000)!
    expect(c.bankroll).toBeGreaterThan(5000)
    // Every point after it, up to the latest dip, sits at or below the level.
    const dip = series.map((p) => p.bankroll <= 5000).lastIndexOf(true)
    expect(series.slice(c.index + 1, dip + 1).every((p) => p.bankroll <= 5000)).toBe(true)
    expect(c.at).toBe('2026-09-11T21:28:12.435889+00:00')
    expect(lastCrossingAbove([{ at: 'x', bankroll: 5001, kind: 'FUNDING', position_id: null }] as BankrollPoint[])).toBeNull()
  })

  it('computes per-sport ROI over settled positions only', () => {
    const nhl = ledger.sports.find((s) => s.sport === 'NHL')!
    const settled = nhl.positions.filter((p) => p.status === 'SETTLED')
    const r = sportRoi(nhl.positions)
    expect(r.n).toBe(settled.length)
    expect(r.stake).toBeCloseTo(settled.reduce((s, p) => s + (p.stake ?? 0), 0), 6)
    expect(r.profit).toBeCloseTo(settled.reduce((s, p) => s + (p.profit_loss ?? 0), 0), 6)
    expect(r.roi).toBeCloseTo(r.profit / r.stake, 9)
    expect(sportRoi([]).roi).toBeNull()
  })

  it('starts tonight at 6 AM ET, so an after-midnight final belongs to the night before', () => {
    expect(new Date(etDayAt('2026-10-18', 6)).toISOString()).toBe('2026-10-18T10:00:00.000Z')
    // 8:26 PM and 12:30 AM ET both belong to the night that began at 6 AM ET on the 18th.
    expect(new Date(nightStartOf(Date.parse('2026-10-19T00:26:00Z'))).toISOString()).toBe('2026-10-18T10:00:00.000Z')
    expect(new Date(nightStartOf(Date.parse('2026-10-19T04:30:00Z'))).toISOString()).toBe('2026-10-18T10:00:00.000Z')
    expect(new Date(nightStartOf(Date.parse('2026-10-19T10:00:00Z'))).toISOString()).toBe('2026-10-19T10:00:00.000Z')
    // After the clocks go back (Nov 1), 6 AM ET is 11:00 UTC.
    expect(new Date(etDayAt('2026-11-02', 6)).toISOString()).toBe('2026-11-02T11:00:00.000Z')
    const tonight = settledSince(series, nightStartOf(Date.parse('2026-10-19T04:30:00Z')))
    expect(tonight.length).toBeGreaterThan(0)
    expect(tonight.every((p) => p.kind === 'SETTLEMENT' && Date.parse(p.at!) >= Date.parse('2026-10-18T10:00:00Z'))).toBe(true)
    // Last night's 12:56 AM NHL finals are not part of tonight's replay.
    expect(tonight.some((p) => Date.parse(p.at!) < Date.parse('2026-10-18T10:00:00Z'))).toBe(false)
  })

  it('lists the settlements of one ET day', () => {
    const day = settledOnDay(series, '2026-10-18')
    expect(day.length).toBeGreaterThan(0)
    expect(day.every((p) => p.kind === 'SETTLEMENT')).toBe(true)
  })

  it('splits a day into settlement runs, keeping points that share a clock together', () => {
    const day = settledOnDay(series, '2026-10-18')
    const runs = settlementRuns(day)
    expect(runs.flat()).toEqual(day)
    const pair = runs.find((r) => r.some((p) => p.at === '2026-10-18T23:51:32.140708+00:00'))!
    expect(pair.filter((p) => p.at === '2026-10-18T23:51:32.140708+00:00')).toHaveLength(2)
    expect(runs.find((r) => r.some((p) => p.at === '2026-10-18T21:51:30.998697+00:00'))).not.toBe(pair)
  })
})

describe('moments', () => {
  const positions = ledgerPositions(ledger)
  const ctx = { series, positions, gaps: new Map() }

  it('marks a settlement that beat every earlier point as a new high, against the record before the run', () => {
    const shared = series.filter((p) => p.at === '2026-10-18T23:51:32.140708+00:00')
    const moments = momentsFor(shared, ctx, false)
    expect(moments.map((m) => m.kind)).toEqual(['WIN', 'WIN'])
    expect(moments[0].change).toBe(30.65)
    expect(moments[0].newHigh?.previous?.bankroll).toBe(5005.08)
    expect(moments[1].newHigh?.bankroll).toBe(5006.71)
    expect(moments[1].newHigh?.previous?.bankroll).toBe(5005.08)
    expect(moments[0].selection).toBe('ARI ML')
  })

  it('names the peak of the run, the figure the headline shows, on every card of a run that set a high', () => {
    const shared = series.filter((p) => p.at === '2026-10-18T23:51:32.140708+00:00')
    const [ari, kc] = momentsFor(shared, ctx, true)
    // ARI alone reached $5,005.43; the run (ARI then KC, one clock) peaked at $5,006.71.
    expect(ari.bankroll).toBeLessThan(5006.71)
    expect(ari.newHigh).toEqual(expect.objectContaining({ bankroll: 5006.71, partOfRun: true }))
    expect(kc.newHigh).toEqual(expect.objectContaining({ bankroll: 5006.71, partOfRun: false }))
    expect(allTimeHigh(series)!.bankroll).toBe(kc.newHigh!.bankroll)
  })

  it('puts a header card first when three or more settle together, and never calls a loss a high', () => {
    const run = series.slice(-3)
    const moments = momentsFor(run, ctx, true)
    expect(moments[0].kind).toBe('BATCH')
    expect(moments[0].batch?.count).toBe(3)
    expect(moments.every((m) => m.replay)).toBe(true)
    const loss = moments.find((m) => m.kind === 'LOSS')!
    expect(loss.newHigh).toBeNull()
  })
})
