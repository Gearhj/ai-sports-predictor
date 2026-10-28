/**
 * REHEARSAL (owner 10-26: "a lot of it I can't test until an event happens"). Replays a finished night as if it
 * were live, sped up, so the Arena, Boss Fight, Rival Mode, the Sweat Meter and You Call It can be exercised at
 * any hour.
 *
 * A simulated clock runs from 30 minutes before the night's first start at `speed`× real time. Each game is
 * UPCOMING before its start, LIVE while the clock is inside its typical length (score climbing toward the REAL
 * final along a fixed, seeded path), then FINAL with the real final score. The path between start and final is
 * invented and labelled so; the finals are real. Rehearsal calls live in their own browser storage and never
 * reach the shared league store or the real XP.
 */
import { useEffect, useState } from 'react'
import type { PulseGame } from '../pulse/pulseData'

const KEY = 'aisp-rehearsal-v1'
const EVENT = 'aisp-rehearsal'
export interface RehearsalState { active: boolean; day: string; t0: number; startedAt: number; speed: number; paused: boolean; pausedSim: number | null }
const OFF: RehearsalState = { active: false, day: '', t0: 0, startedAt: 0, speed: 72, paused: false, pausedSim: null }

// RETIRED 10-27 (owner: "the REHEARSAL section is confusing ... get rid of it"): always off, and any rehearsal a
// browser still has stored is cleared on first read, so the bar and the simulated clock never come back.
let cleared = false
export function rehearsal(): RehearsalState {
  if (!cleared) { cleared = true; try { localStorage.removeItem(KEY) } catch { /* private window */ } }
  return OFF
}
function save(s: RehearsalState): void {
  try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* private window */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(EVENT))
}
export const isRehearsing = () => rehearsal().active

/** The simulated "now" (epoch ms). Real time when no rehearsal is running. */
export function simNow(s = rehearsal(), real = Date.now()): number {
  if (!s.active) return real
  if (s.paused && s.pausedSim != null) return s.pausedSim
  return s.t0 + (real - s.startedAt) * s.speed
}
export function startRehearsal(_day: string, _firstStart: number, _speed = 72): void { /* retired 10-27 */ }
export function stopRehearsal(): void { save(OFF) }
export function setSpeed(speed: number): void { const s = rehearsal(); const at = simNow(s); save({ ...s, t0: at, startedAt: Date.now(), speed }) }
export function jump(minutes: number): void { const s = rehearsal(); const at = simNow(s) + minutes * 60_000; save({ ...s, t0: at, startedAt: Date.now(), pausedSim: s.paused ? at : null }) }
export function togglePause(): void {
  const s = rehearsal()
  if (s.paused) save({ ...s, paused: false, pausedSim: null, t0: s.pausedSim ?? simNow(s), startedAt: Date.now() })
  else save({ ...s, paused: true, pausedSim: simNow(s) })
}

export function useRehearsal(): RehearsalState & { now: number } {
  const [s, set] = useState(rehearsal)
  const [, tick] = useState(0)
  useEffect(() => {
    const on = () => set(rehearsal())
    window.addEventListener(EVENT, on); window.addEventListener('storage', on)
    const t = window.setInterval(() => tick((n) => n + 1), 1000)
    return () => { window.removeEventListener(EVENT, on); window.removeEventListener('storage', on); window.clearInterval(t) }
  }, [])
  return { ...s, now: simNow(s) }
}

// ------------------------------------------------------------------ the simulated night --

/** Typical wall-clock length of a game, in minutes. */
export const LENGTH_MIN: Record<string, number> = { NFL: 190, CFB: 200, NBA: 140, NHL: 150, MLB: 180 }
const PERIODS: Record<string, { n: number; min: number; word: string[] }> = {
  NBA: { n: 4, min: 12, word: ['1st', '2nd', '3rd', '4th'] }, NFL: { n: 4, min: 15, word: ['1st', '2nd', '3rd', '4th'] },
  CFB: { n: 4, min: 15, word: ['1st', '2nd', '3rd', '4th'] }, NHL: { n: 3, min: 20, word: ['1st', '2nd', '3rd'] },
}

/** Deterministic 0..1 noise from a string and an index, so a rehearsal replays the same way every time. */
function noise(seed: string, i: number): number {
  let h = 2166136261 ^ i
  for (let k = 0; k < seed.length; k++) h = Math.imul(h ^ seed.charCodeAt(k), 16777619)
  return ((h >>> 0) % 10_000) / 10_000
}
/** Share of the final score reached at progress f, wobbling but never going backwards. */
function scoreAt(final: number, f: number, seed: string): number {
  if (f <= 0) return 0
  if (f >= 1) return final
  const steps = 24
  let best = 0
  for (let i = 1; i <= Math.floor(f * steps); i++) best = Math.max(best, Math.round(final * Math.min(1, (i / steps) * (0.8 + 0.4 * noise(seed, i)))))
  return Math.min(final, best)
}

export interface SimClock { period: number; clock: string; detail: string }
export function clockAt(sport: string, f: number): SimClock {
  const p = PERIODS[sport]
  if (!p) { // MLB: half-innings
    const half = Math.min(17, Math.floor(f * 18))
    const inning = Math.floor(half / 2) + 1
    return { period: inning, clock: '', detail: `${half % 2 ? 'Bot' : 'Top'} ${inning}${inning === 1 ? 'st' : inning === 2 ? 'nd' : inning === 3 ? 'rd' : 'th'}` }
  }
  const total = p.n * p.min
  const played = Math.min(total - 0.05, f * total)
  const period = Math.floor(played / p.min) + 1
  const left = p.min - (played - (period - 1) * p.min)
  const mm = Math.floor(left), ss = Math.floor((left - mm) * 60)
  const clock = `${mm}:${String(ss).padStart(2, '0')}`
  return { period, clock, detail: `${clock} - ${p.word[period - 1]}` }
}

export interface SimGame { game: PulseGame; f: number; clock: SimClock | null }
/** The night's games as they stand at `now`: phase and score rewritten from the real finals. */
export function simulateNight(games: readonly PulseGame[], day: string, now: number): SimGame[] {
  return games.filter((g) => g.day === day && g.score).map((g): SimGame => {
    const start = Date.parse(g.startsAt)
    const f = (now - start) / ((LENGTH_MIN[g.sport] ?? 160) * 60_000)
    const fin = g.score!
    if (f < 0) return { game: { ...g, phase: 'UPCOMING' as const, score: null }, f, clock: null }
    if (f >= 1) return { game: { ...g, phase: 'FINAL' as const, score: { ...fin, detail: 'Final' } }, f: 1, clock: null }
    const clock = clockAt(g.sport, f)
    return { game: { ...g, phase: 'LIVE' as const, score: { away: scoreAt(fin.away, f, `${g.id}a`), home: scoreAt(fin.home, f, `${g.id}h`), detail: clock.detail } }, f, clock }
  }).sort((a, b) => Date.parse(a.game.startsAt) - Date.parse(b.game.startsAt))
}
