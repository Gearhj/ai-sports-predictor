/**
 * YOU CALL IT (owner 10-26, plan item #3): before each snap the visitor calls RUN, SHORT PASS, DEEP PASS or KICK
 * against the NFL play-call model already in Live View (nflverse 2015-2026; 70.5% pass-vs-run on held-out
 * seasons). Stored ONLY in this browser; play only, no money. XP feeds the site-wide level (features/progress).
 *
 * Scoring: a right call pays more the less likely the model thought it was (15 / p, capped 100), plus 20 when
 * the model's own top call was wrong. FILM SESSION calls on already-played snaps pay half.
 */
import { useEffect, useState } from 'react'
import { CLASSES, type Category, type PlayClass } from './playcall'

export const OPTIONS = ['RUN', 'SHORT', 'DEEP', 'KICK'] as const
export type Option = (typeof OPTIONS)[number]
export const OPTION_LABEL: Record<Option, string> = { RUN: 'Run', SHORT: 'Short pass', DEEP: 'Deep pass', KICK: 'Punt / FG' }

export interface YouCall {
  key: string // `${event}:${playId}` of the snap being called (LIVE: the play before it; FILM: the play itself)
  event: string
  mode: 'LIVE' | 'FILM'
  pick: Option
  modelPick: Option
  pModel: number // the model's probability for the visitor's pick
  at: string
  result?: { actual: Option | 'PASS'; right: boolean; modelRight: boolean; text: string | null }
}

const KEY = 'aisp-youcall-v1'
const EVENT = 'aisp-youcall'
function read(): YouCall[] {
  try { const raw = localStorage.getItem(KEY); return raw ? (JSON.parse(raw) as YouCall[]) : [] } catch { return [] }
}
function write(list: YouCall[]): void {
  try { localStorage.setItem(KEY, JSON.stringify(list.slice(-2000))) } catch { /* storage unavailable */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(EVENT))
}
export function listCalls(): YouCall[] { return read() }
export function saveCall(c: YouCall): void { write([...read().filter((x) => x.key !== c.key), c]) }
export function resolveCall(key: string, result: NonNullable<YouCall['result']>): void { write(read().map((c) => (c.key === key && !c.result ? { ...c, result } : c))) }

export function useYouCalls(): YouCall[] {
  const [s, set] = useState<YouCall[]>(read)
  useEffect(() => {
    const on = () => set(read())
    window.addEventListener(EVENT, on); window.addEventListener('storage', on)
    on()
    return () => { window.removeEventListener(EVENT, on); window.removeEventListener('storage', on) }
  }, [])
  return s
}

/** The model's 12 class probabilities folded into the four calls a viewer can make. */
export function optionProbs(probs: readonly number[]): Record<Option, number> {
  const at = (c: PlayClass) => probs[CLASSES.indexOf(c)] ?? 0
  return {
    RUN: at('RUN_L') + at('RUN_M') + at('RUN_R') + at('KNEEL'),
    SHORT: at('PASS_SL') + at('PASS_SM') + at('PASS_SR'),
    DEEP: at('PASS_DL') + at('PASS_DM') + at('PASS_DR'),
    KICK: at('PUNT') + at('FG'),
  }
}
export const topOption = (p: Record<Option, number>): Option => OPTIONS.reduce((a, b) => (p[b] > p[a] ? b : a), OPTIONS[0])

/** What actually happened, as a call. A pass whose depth the play text does not give is just 'PASS' (either pass call counts). */
export function actualOption(cls: PlayClass | null, category: Category | null): Option | 'PASS' | null {
  if (cls) {
    if (cls.startsWith('PASS_S')) return 'SHORT'
    if (cls.startsWith('PASS_D')) return 'DEEP'
    if (cls.startsWith('RUN') || cls === 'KNEEL') return 'RUN'
    return 'KICK'
  }
  if (category === 'PASS') return 'PASS'
  if (category === 'RUN' || category === 'KNEEL') return 'RUN'
  if (category === 'PUNT' || category === 'FG') return 'KICK'
  return null
}
export const isRight = (pick: Option, actual: Option | 'PASS') => pick === actual || (actual === 'PASS' && (pick === 'SHORT' || pick === 'DEEP'))

export function xpOfCall(c: YouCall): number {
  if (!c.result?.right) return 0
  let xp = Math.min(100, Math.round(15 / Math.max(0.01, c.pModel)))
  if (!c.result.modelRight) xp += 20
  return c.mode === 'FILM' ? Math.round(xp / 2) : xp
}
