/**
 * What is the offense about to call?
 *
 * A gradient-boosted model trained offline on every NFL snap from 2015 to 2026 (nflverse play-by-play,
 * about 430,000 plays) is baked into a lookup grid: down x distance x field position x score margin x
 * time. This module looks the situation up, then tilts pass-versus-run toward how this offense has
 * actually behaved in this game. It is an estimate of the CALL, not of the result, and never a bet signal.
 *
 * Validation (train 2015-2024, test 2025-26 to date): pass-vs-run right 71% of the time against 61% for
 * always guessing the more common one; call-type log loss 0.58 against 1.01 for the base rates.
 * Passing direction and depth are much less predictable, and the page says so.
 */
import { useEffect, useState } from 'react'
import type { ParsedPlay } from './playParser'
import { parseFbPlay, type FbPlay } from './footballData'

export const CLASSES = ['PASS_SL', 'PASS_SM', 'PASS_SR', 'PASS_DL', 'PASS_DM', 'PASS_DR', 'RUN_L', 'RUN_M', 'RUN_R', 'PUNT', 'FG', 'KNEEL'] as const
export type PlayClass = (typeof CLASSES)[number]
export type Category = 'PASS' | 'RUN' | 'PUNT' | 'FG' | 'KNEEL'

export const LABELS: Record<PlayClass, { name: string; group: 'pass' | 'run' | 'kick' }> = {
  PASS_SL: { name: 'Short pass, left', group: 'pass' }, PASS_SM: { name: 'Short pass, middle', group: 'pass' }, PASS_SR: { name: 'Short pass, right', group: 'pass' },
  PASS_DL: { name: 'Deep pass, left', group: 'pass' }, PASS_DM: { name: 'Deep pass, middle', group: 'pass' }, PASS_DR: { name: 'Deep pass, right', group: 'pass' },
  RUN_L: { name: 'Run, left side', group: 'run' }, RUN_M: { name: 'Run, up the middle', group: 'run' }, RUN_R: { name: 'Run, right side', group: 'run' },
  PUNT: { name: 'Punt', group: 'kick' }, FG: { name: 'Field goal try', group: 'kick' }, KNEEL: { name: 'Kneel down', group: 'kick' },
}

export interface GridMeta { trainedOn: string; validation: { stage1_logloss_prior: number; stage1_logloss_grid: number; pass_vs_run_accuracy_grid: number; pass_vs_run_always_majority: number; test_plays: number } }
export interface Grid { data: Uint16Array; meta: GridMeta }

let cached: Promise<Grid> | null = null
export function loadGrid(): Promise<Grid> {
  if (!cached) {
    cached = Promise.all([fetch('/playcall/playcall_grid.bin').then((r) => { if (!r.ok) throw new Error('grid'); return r.arrayBuffer() }), fetch('/playcall/playcall_meta.json').then((r) => r.json() as Promise<GridMeta>)])
      .then(([buf, meta]) => ({ data: new Uint16Array(buf), meta }))
    cached.catch(() => { cached = null })
  }
  return cached
}

export function usePlaycallGrid(): { grid: Grid | null; error: boolean } {
  const [state, setState] = useState<{ grid: Grid | null; error: boolean }>({ grid: null, error: false })
  useEffect(() => {
    let live = true
    loadGrid().then((grid) => { if (live) setState({ grid, error: false }) }).catch(() => { if (live) setState({ grid: null, error: true }) })
    return () => { live = false }
  }, [])
  return state
}

export interface PlayState { down: number; distance: number; yardsToEndzone: number; scoreDiff: number; period: number; secondsRemaining: number }

/** Must match bucket() in train_playcall.py exactly (a test pins it against the trained table). */
export function bucketIndex(s: PlayState): number {
  const yd = s.distance
  const ydb = yd <= 1 ? 0 : yd <= 2 ? 1 : yd <= 3 ? 2 : yd <= 5 ? 3 : yd <= 7 ? 4 : yd <= 10 ? 5 : yd <= 14 ? 6 : 7
  const yl = s.yardsToEndzone
  const ylb = yl <= 3 ? 0 : yl <= 9 ? 1 : Math.min(10, 2 + Math.floor((yl - 10) / 10))
  const sc = s.scoreDiff
  const scb = sc <= -17 ? 0 : sc <= -9 ? 1 : sc <= -4 ? 2 : sc <= -1 ? 3 : sc === 0 ? 4 : sc <= 3 ? 5 : sc <= 8 ? 6 : sc <= 16 ? 7 : 8
  const q = s.period
  const gs = s.secondsRemaining
  const tm = q >= 5 ? 7 : q === 1 ? 0 : q === 2 ? (gs - 1800 <= 120 ? 2 : 1) : q === 3 ? 3 : gs <= 120 ? 6 : gs <= 300 ? 5 : 4
  return ((((s.down - 1) * 8 + ydb) * 11 + ylb) * 9 + scb) * 8 + tm
}

export function predict(grid: Grid, s: PlayState): number[] {
  const i = bucketIndex(s) * 12
  const raw = Array.from(grid.data.slice(i, i + 12), (v) => v / 10000)
  const sum = raw.reduce((a, b) => a + b, 0) || 1
  return raw.map((v) => v / sum)
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/** "10:37" in period 2 -> seconds left in the game (regulation) or the period (overtime). */
export function secondsLeft(period: number | null, clock: string | null): number {
  const m = String(clock ?? '').match(/^(\d+):(\d{2})$/)
  const inPeriod = m ? Number(m[1]) * 60 + Number(m[2]) : 0
  return period != null && period <= 4 ? (4 - period) * 900 + inPeriod : inPeriod
}

/** The situation at the snap of plays[idx]: from its own start state, the score before it and the clock. */
export function stateForPlay(plays: readonly FbPlay[], idx: number, homeId: string): PlayState | null {
  const p = plays[idx]
  if (!p) return null
  const { down, distance, yardLine } = p.start
  if (!down || down < 1 || down > 4 || distance == null || yardLine == null) return null
  const prev = idx > 0 ? plays[idx - 1] : null
  const away = prev?.awayScore ?? 0
  const home = prev?.homeScore ?? 0
  const offenseId = p.offense ?? p.start.team
  const diff = offenseId === homeId ? home - away : away - home
  // ESPN's yardLine counts from the HOME team's goal line, so the away team's own-goal distance is its mirror image.
  const own = offenseId === homeId ? yardLine : 100 - yardLine
  return { down, distance: Math.max(1, distance), yardsToEndzone: clamp(100 - own, 1, 99), scoreDiff: diff, period: p.period ?? 1, secondsRemaining: secondsLeft(p.period, p.clock) }
}

export function categoryProbs(p: number[]): Record<Category, number> {
  return { PASS: p.slice(0, 6).reduce((a, b) => a + b, 0), RUN: p.slice(6, 9).reduce((a, b) => a + b, 0), PUNT: p[9], FG: p[10], KNEEL: p[11] }
}

export function classOfPlay(parsed: ParsedPlay | null, text: string | null | undefined): { cls: PlayClass | null; category: Category | null } {
  if (!parsed) return { cls: null, category: null }
  switch (parsed.kind) {
    case 'pass': case 'incomplete': case 'interception': {
      if (parsed.depth && parsed.side) return { cls: `PASS_${parsed.depth === 'deep' ? 'D' : 'S'}${parsed.side[0].toUpperCase()}` as PlayClass, category: 'PASS' }
      return { cls: null, category: 'PASS' }
    }
    case 'sack': return { cls: null, category: 'PASS' }
    case 'rush': {
      if (/kneel/i.test(text ?? '')) return { cls: 'KNEEL', category: 'KNEEL' }
      const g = parsed.gap ?? ''
      const cls: PlayClass | null = /left/.test(g) ? 'RUN_L' : /right/.test(g) ? 'RUN_R' : /middle/.test(g) ? 'RUN_M' : null
      return { cls, category: 'RUN' }
    }
    case 'punt': return { cls: 'PUNT', category: 'PUNT' }
    case 'fieldgoal': return { cls: 'FG', category: 'FG' }
    default: return { cls: null, category: null }
  }
}

/** How far this offense has run above or below the model's expected pass share this game (in probability points), and over how many snaps. */
export function teamTilt(grid: Grid, plays: readonly FbPlay[], uptoIdx: number, offenseId: string, homeId: string): { n: number; delta: number } {
  let n = 0
  let obs = 0
  let exp = 0
  for (let i = 0; i < uptoIdx; i += 1) {
    const p = plays[i]
    if ((p.offense ?? p.start.team) !== offenseId) continue
    const parsed = parseFbPlay(p)
    const { category } = classOfPlay(parsed, p.text)
    if (category !== 'PASS' && category !== 'RUN') continue
    const st = stateForPlay(plays, i, homeId)
    if (!st) continue
    const cp = categoryProbs(predict(grid, st))
    exp += cp.PASS / Math.max(1e-6, cp.PASS + cp.RUN)
    obs += category === 'PASS' ? 1 : 0
    n += 1
  }
  return { n, delta: n ? (obs - exp) / n : 0 }
}

export function applyTilt(p: number[], tilt: { n: number; delta: number }): number[] {
  if (tilt.n < 6) return p
  const pass = p.slice(0, 6).reduce((a, b) => a + b, 0)
  const run = p.slice(6, 9).reduce((a, b) => a + b, 0)
  if (pass <= 0 || run <= 0) return p
  const share = pass / (pass + run)
  const w = tilt.n / (tilt.n + 12)
  const next = clamp(share + w * tilt.delta, 0.02, 0.98)
  const kp = (pass + run) * next / pass
  const kr = (pass + run) * (1 - next) / run
  return p.map((v, i) => (i < 6 ? v * kp : i < 9 ? v * kr : v))
}

export interface Forecast { probs: number[]; base: number[]; tilt: { n: number; delta: number }; state: PlayState }

/** The forecast for the snap of plays[idx] (or, with idx === plays.length, for the snap after the last play). */
export function forecastFor(grid: Grid, plays: readonly FbPlay[], idx: number, homeId: string): Forecast | null {
  const st = stateForPlay(plays, idx, homeId)
  if (!st) return null
  const offenseId = plays[idx].offense ?? plays[idx].start.team
  if (!offenseId) return null
  const base = predict(grid, st)
  const tilt = teamTilt(grid, plays, idx, offenseId, homeId)
  return { base, probs: applyTilt(base, tilt), tilt, state: st }
}

/** The state after the latest play, for "up next": the next snap's down, distance and field position. */
export function nextState(plays: readonly FbPlay[], homeId: string): { state: PlayState; offenseId: string } | null {
  const last = plays[plays.length - 1]
  if (!last || last.end.down == null || last.end.down < 1 || last.end.down > 4 || last.end.distance == null || last.end.yardLine == null || !last.end.team) return null
  const diffHome = (last.homeScore ?? 0) - (last.awayScore ?? 0)
  const offenseId = last.end.team
  return {
    offenseId,
    state: { down: last.end.down, distance: Math.max(1, last.end.distance), yardsToEndzone: clamp(100 - (offenseId === homeId ? last.end.yardLine : 100 - last.end.yardLine), 1, 99), scoreDiff: offenseId === homeId ? diffHome : -diffHome, period: last.period ?? 1, secondsRemaining: secondsLeft(last.period, last.clock) },
  }
}

/** Win probability added by a play for the offense, in points of win probability, from ESPN's per-play series. */
export function wpaFor(series: readonly { playId: string; home: number }[] | undefined, playId: string, offenseIsHome: boolean): number | null {
  if (!series?.length) return null
  const i = series.findIndex((w) => w.playId === playId)
  if (i <= 0) return null
  const d = (series[i].home - series[i - 1].home) * 100
  return offenseIsHome ? d : -d
}
