/**
 * Situation tables: what a snap like this usually produces (league averages from nflverse 2015-2026),
 * the five-row call summary, and what a defense has allowed in this game. All computed in code; the
 * language model never invents these.
 */
import { useEffect, useState } from 'react'
import type { EpaPlay } from './ep'
import { parseFbPlay, type FbPlay } from './footballData'
import { CLASSES, type Forecast, type PlayState } from './playcall'

export interface YardsTables { grid: Record<string, [number, number, number, number]>; league2025: { pass: number; run: number; explosive: number; success: number; third: number; depth: number; ypp: number }; trainedOn: string }

let cached: Promise<YardsTables> | null = null
export function loadYards(): Promise<YardsTables> {
  if (!cached) {
    cached = fetch('/playcall/yards_tables.json').then((r) => { if (!r.ok) throw new Error('yards'); return r.json() as Promise<YardsTables> })
    cached.catch(() => { cached = null })
  }
  return cached
}
export function useYards(): YardsTables | null {
  const [t, setT] = useState<YardsTables | null>(null)
  useEffect(() => { let live = true; loadYards().then((x) => { if (live) setT(x) }).catch(() => undefined); return () => { live = false } }, [])
  return t
}

const ydb = (y: number) => (y <= 1 ? 0 : y <= 2 ? 1 : y <= 3 ? 2 : y <= 5 ? 3 : y <= 7 ? 4 : y <= 10 ? 5 : y <= 14 ? 6 : 7)

export interface Projection { yards: number; explosive: number; success: number; n: number }
/** League average outcome of a scrimmage snap from this down, distance and spot (yards to the end zone). */
export function projectSnap(t: YardsTables, s: PlayState): Projection | null {
  const z = Math.min(9, Math.max(0, Math.floor(s.yardsToEndzone / 10)))
  const row = t.grid[`${s.down}-${ydb(s.distance)}-${z}`]
  if (!row) return null
  return { yards: row[0], explosive: row[1], success: row[2], n: row[3] }
}

/** The mockup's five rows: short pass, deep pass, inside run, outside run, special teams. */
export interface CallRow { key: string; label: string; icon: 'pass' | 'deep' | 'inside' | 'outside' | 'kick'; p: number; parts: Array<{ label: string; p: number }> }
export function fiveRows(f: Forecast): CallRow[] {
  const v = (k: string) => f.probs[CLASSES.indexOf(k as (typeof CLASSES)[number])] ?? 0
  const rows: CallRow[] = [
    { key: 'short', label: 'Pass · Short (0-9 yds)', icon: 'pass', p: v('PASS_SL') + v('PASS_SM') + v('PASS_SR'), parts: [{ label: 'left', p: v('PASS_SL') }, { label: 'middle', p: v('PASS_SM') }, { label: 'right', p: v('PASS_SR') }] },
    { key: 'deep', label: 'Pass · Deep (15+ yds)', icon: 'deep', p: v('PASS_DL') + v('PASS_DM') + v('PASS_DR'), parts: [{ label: 'left', p: v('PASS_DL') }, { label: 'middle', p: v('PASS_DM') }, { label: 'right', p: v('PASS_DR') }] },
    { key: 'inside', label: 'Run · Inside', icon: 'inside', p: v('RUN_M'), parts: [] },
    { key: 'outside', label: 'Run · Outside', icon: 'outside', p: v('RUN_L') + v('RUN_R'), parts: [{ label: 'left', p: v('RUN_L') }, { label: 'right', p: v('RUN_R') }] },
    { key: 'kick', label: 'Punt / Field goal / Kneel', icon: 'kick', p: v('PUNT') + v('FG') + v('KNEEL'), parts: [{ label: 'punt', p: v('PUNT') }, { label: 'field goal', p: v('FG') }, { label: 'kneel', p: v('KNEEL') }] },
  ]
  return rows.sort((a, b) => b.p - a.p)
}

export interface DefenseFacts {
  dropbacks: number; epaPerDropback: number | null; rushes: number; epaPerRush: number | null; sacks: number; ints: number
  thirdStops: number; thirdFaced: number; explosiveAllowed: number; shortPassEpa: number | null; shortPassN: number; deepPassEpa: number | null; deepPassN: number
  ids: { dropbacks: string[]; rushes: string[]; third: string[]; explosive: string[] }
}
/** What a defense has given up this game (team on defense = the offense's opponent). */
export function defenseFacts(plays: readonly FbPlay[], epa: EpaPlay[], defenseId: string, offenseId: string): DefenseFacts {
  const e = new Map(epa.map((x) => [x.id, x.epa]))
  const f: DefenseFacts = { dropbacks: 0, epaPerDropback: null, rushes: 0, epaPerRush: null, sacks: 0, ints: 0, thirdStops: 0, thirdFaced: 0, explosiveAllowed: 0, shortPassEpa: null, shortPassN: 0, deepPassEpa: null, deepPassN: 0, ids: { dropbacks: [], rushes: [], third: [], explosive: [] } }
  let db = 0, rs = 0, sp = 0, dp = 0
  for (const p of plays) {
    if ((p.offense ?? p.start.team) !== offenseId) continue
    const k = parseFbPlay(p)
    const v = e.get(p.id) ?? 0
    const pass = k.kind === 'pass' || k.kind === 'incomplete' || k.kind === 'interception' || k.kind === 'sack'
    if (pass) { f.dropbacks += 1; db += v; f.ids.dropbacks.push(p.id); if (k.kind === 'sack') f.sacks += 1; if (k.kind === 'interception') f.ints += 1
      if (k.depth === 'short') { f.shortPassN += 1; sp += v } else if (k.depth === 'deep') { f.deepPassN += 1; dp += v } }
    else if (k.kind === 'rush') { f.rushes += 1; rs += v; f.ids.rushes.push(p.id) }
    else continue
    if (p.start.down === 3 && p.start.distance != null) { f.thirdFaced += 1; f.ids.third.push(p.id); if (!((p.yards ?? 0) >= p.start.distance || k.touchdown)) f.thirdStops += 1 }
    if ((p.yards ?? 0) >= 15) { f.explosiveAllowed += 1; f.ids.explosive.push(p.id) }
  }
  void defenseId
  f.epaPerDropback = f.dropbacks ? db / f.dropbacks : null
  f.epaPerRush = f.rushes ? rs / f.rushes : null
  f.shortPassEpa = f.shortPassN ? sp / f.shortPassN : null
  f.deepPassEpa = f.deepPassN ? dp / f.deepPassN : null
  return f
}
