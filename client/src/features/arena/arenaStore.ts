/**
 * THE ARENA's memory: a visitor's calls against the AISP model, stored ONLY in this browser (localStorage),
 * like the preview picks. Play only: no price, no stake, no money. A call is a game, a side, a power level
 * (1-3, how sure you are) and whether it was made BLIND (before AISP's card was turned over).
 *
 * Kept apart from My Picks on purpose (owner rule 10-26: YOU and AISP are never mixed): the Arena scores
 * YOUR call against AISP's call on the same game, it never copies one into the other.
 */
import { useEffect, useState } from 'react'
import { isRehearsing } from '../rehearsal/rehearsal'
import { boothAuth } from '../../services/boothAuth'

export type Side = 'HOME' | 'AWAY'
export interface ArenaCall {
  game_id: string
  sport: string
  away: string
  home: string
  starts_at: string
  side: Side
  power: 1 | 2 | 3
  blind: boolean
  /** REWIND = a practice call on a game that already finished (half XP, never in the season fight). */
  mode: 'LIVE' | 'REWIND'
  at: string
  /** The market's favourite when you locked (THE HOUSE in the weekly Boss Fight); null when no fresh price. */
  house?: Side | null
  /** Filled once the game is final. AISP's call is frozen at that moment. */
  result?: { winner: Side | 'TIE'; aisp: Side | null; at: string }
}
interface ArenaState { calls: ArenaCall[]; revealed: string[]; seenResults: string[]; level: number }

const KEY = 'aisp-arena-v1'
const EVENT = 'aisp-arena'
const EMPTY: ArenaState = { calls: [], revealed: [], seenResults: [], level: 1 }

/** During a REHEARSAL the Arena plays in its own storage, so practice never touches the real record. */
const REHEARSAL_KEY = 'aisp-arena-rehearsal'
const keyNow = () => (isRehearsing() ? REHEARSAL_KEY : KEY)
function readKey(key: string): ArenaState {
  try { const raw = localStorage.getItem(key); return raw ? { ...EMPTY, ...(JSON.parse(raw) as ArenaState) } : EMPTY } catch { return EMPTY }
}
function read(): ArenaState { return readKey(keyNow()) }
export function clearRehearsalArena(): void { try { localStorage.removeItem(REHEARSAL_KEY) } catch { /* private window */ } window.dispatchEvent(new CustomEvent(EVENT)) }
function write(s: ArenaState): void {
  try { localStorage.setItem(keyNow(), JSON.stringify(s)) } catch { /* storage unavailable: the page still works for this visit */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(EVENT))
}
export function arenaState(): ArenaState { return read() }

// ------------------------------------------------------------------ league (TEST MODE) --
// Who is playing on this browser (nobody can sign in on the read-only preview, so a profile stands in for the
// account). LIVE calls go to the preview server's shared store (aisp-preview/arena_league.py), which refuses any
// call made or changed after the start and keeps rivals' sides hidden until you lock the same game.
const PROFILE_KEY = 'aisp-arena-profile'
export const PROFILES = ['JAMES', 'ELVIS', 'MOM'] as const
export function currentProfile(): string { try { return localStorage.getItem(PROFILE_KEY) || 'JAMES' } catch { return 'JAMES' } }
async function push(remove: string[] = []): Promise<void> {
  if (isRehearsing()) return // rehearsal calls never reach the shared league store
  const calls = read().calls.filter((c) => c.mode === 'LIVE')
  // Live site (10-29): a signed-in player IS their account; the server answers with the account's name.
  const res = await fetch('/preview-api/arena/sync', { method: 'POST', headers: { 'content-type': 'application/json', ...boothAuth() }, body: JSON.stringify({ profile: currentProfile(), calls, remove }) }).catch(() => null)
  const out = res && res.ok ? await res.json().catch(() => null) as { profile?: string } | null : null
  if (out?.profile && out.profile !== currentProfile()) { try { localStorage.setItem(PROFILE_KEY, out.profile); window.dispatchEvent(new Event(EVENT)) } catch { /* private window */ } }
}
/** Switch player: park this profile's record, load the other's, then merge its LIVE calls from the shared store. */
export async function switchProfile(next: string): Promise<void> {
  const name = next.toUpperCase().replace(/[^A-Z0-9_]/g, '').slice(0, 16)
  if (name.length < 2 || name === currentProfile()) return
  try {
    localStorage.setItem(`${KEY}:${currentProfile()}`, JSON.stringify(read()))
    const parked = localStorage.getItem(`${KEY}:${name}`)
    localStorage.setItem(PROFILE_KEY, name)
    write(parked ? { ...EMPTY, ...(JSON.parse(parked) as ArenaState) } : EMPTY)
  } catch { return }
  const league = await fetch(`/preview-api/arena/league?me=${name}`, { headers: boothAuth() }).then((r) => (r.ok ? r.json() : null)).catch(() => null) as LeagueResponse | null
  const remote = league?.players.find((p) => p.profile === name)?.calls ?? []
  const s = read()
  const have = new Set(s.calls.map((c) => c.game_id))
  const add = remote.filter((c): c is ArenaCall => !c.hidden && !have.has(c.game_id)).map((c) => ({ ...c, mode: 'LIVE' as const, blind: Boolean(c.blind), power: (c.power ?? 1) as 1 | 2 | 3 }))
  if (add.length) write({ ...s, calls: [...s.calls, ...add] })
}
export interface LeagueCall extends Partial<ArenaCall> { game_id: string; starts_at?: string; hidden?: boolean }
export interface LeagueResponse { players: Array<{ profile: string; calls: LeagueCall[] }>; me: string; as_of: string }
export function useLeague(enabled = true): LeagueResponse | null {
  const [data, setData] = useState<LeagueResponse | null>(null)
  const [me, setMe] = useState(currentProfile())
  useEffect(() => { const on = () => setMe(currentProfile()); window.addEventListener(EVENT, on); return () => window.removeEventListener(EVENT, on) }, [])
  useEffect(() => {
    if (!enabled) return undefined
    let alive = true
    const load = () => fetch(`/preview-api/arena/league?me=${me}`, { headers: boothAuth() }).then((r) => (r.ok ? r.json() : null)).then((d) => { if (alive && d) setData(d as LeagueResponse) }).catch(() => undefined)
    void load()
    const t = window.setInterval(load, 30_000)
    const on = () => window.setTimeout(load, 400)
    window.addEventListener(EVENT, on)
    return () => { alive = false; window.clearInterval(t); window.removeEventListener(EVENT, on) }
  }, [enabled, me])
  return data
}

export function lockCall(call: Omit<ArenaCall, 'at' | 'blind'>): void {
  const s = read()
  const blind = !s.revealed.includes(call.game_id)
  write({ ...s, calls: [...s.calls.filter((c) => c.game_id !== call.game_id), { ...call, blind, at: new Date().toISOString() }] })
  if (call.mode === 'LIVE') void push()
}
export function dropCall(gameId: string): void {
  const s = read()
  write({ ...s, calls: s.calls.filter((c) => c.game_id !== gameId || c.result) })
  void push([gameId])
}
export function reveal(gameId: string): void { const s = read(); if (!s.revealed.includes(gameId)) write({ ...s, revealed: [...s.revealed, gameId] }) }
export function settle(gameId: string, result: NonNullable<ArenaCall['result']>): void {
  const s = read()
  const was = s.calls.find((c) => c.game_id === gameId && !c.result)
  write({ ...s, calls: s.calls.map((c) => (c.game_id === gameId && !c.result ? { ...c, result } : c)) })
  if (was?.mode === 'LIVE') void push()
}
export function markSeen(ids: string[], level: number): void { const s = read(); write({ ...s, seenResults: [...new Set([...s.seenResults, ...ids])], level }) }
export function resetArena(): void { write(EMPTY) }

/** `real` reads the real record even during a rehearsal (site-wide XP never counts practice). */
export function useArena(real = false): ArenaState {
  const get = () => (real ? readKey(KEY) : read())
  const [s, set] = useState<ArenaState>(get)
  useEffect(() => {
    const on = () => set(get())
    window.addEventListener(EVENT, on); window.addEventListener('storage', on); window.addEventListener('aisp-rehearsal', on)
    on()
    return () => { window.removeEventListener(EVENT, on); window.removeEventListener('storage', on); window.removeEventListener('aisp-rehearsal', on) }
  }, [real]) // eslint-disable-line react-hooks/exhaustive-deps
  return s
}

// ------------------------------------------------------------------ scoring --

/** XP for one settled call. Wrong calls cost nothing (this is a game of nerve, not a penalty box). */
export function xpOf(c: ArenaCall): { xp: number; tags: string[] } {
  const r = c.result
  if (!r || r.winner === 'TIE' || r.winner !== c.side) return { xp: 0, tags: [] }
  const tags: string[] = []
  let xp = 100 * c.power
  if (c.blind) { xp *= 1.5; tags.push('BLIND ×1.5') }
  if (r.aisp && r.aisp !== c.side) { xp += 150; tags.push('MACHINE BREAKER +150') }
  if (c.mode === 'REWIND') { xp /= 2; tags.push('REWIND ½') }
  return { xp: Math.round(xp), tags }
}

export const LEVEL_STEP = 250
/** Level n needs LEVEL_STEP·n(n−1)/2 XP: 0, 250, 750, 1500, 2500 ... */
export function levelOf(xp: number): { level: number; into: number; need: number } {
  const level = Math.floor((1 + Math.sqrt(1 + (8 * xp) / LEVEL_STEP)) / 2)
  const base = (LEVEL_STEP * level * (level - 1)) / 2
  return { level, into: xp - base, need: LEVEL_STEP * level }
}
const TITLES: Array<[number, string]> = [[12, 'LEGEND'], [8, 'ORACLE'], [5, 'SHARP'], [3, 'ANALYST'], [2, 'SCOUT'], [1, 'ROOKIE']]
export const titleOf = (level: number) => TITLES.find(([l]) => level >= l)?.[1] ?? 'ROOKIE'

export interface Badge { id: string; icon: string; name: string; how: string; got: boolean }
export function badgesOf(calls: ArenaCall[]): Badge[] {
  const done = calls.filter((c) => c.result && c.result.winner !== 'TIE').sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at))
  const right = done.filter((c) => c.result!.winner === c.side)
  let best = 0, run = 0
  for (const c of done) { run = c.result!.winner === c.side ? run + 1 : 0; best = Math.max(best, run) }
  const nights = new Map<string, ArenaCall[]>()
  calls.filter((c) => c.mode === 'LIVE').forEach((c) => { const d = c.starts_at.slice(0, 10); nights.set(d, [...(nights.get(d) ?? []), c]) })
  const b = (id: string, icon: string, name: string, how: string, got: boolean): Badge => ({ id, icon, name, how, got })
  return [
    b('first', '🩸', 'First Blood', 'Your first correct call', right.length >= 1),
    b('breaker', '🤖', 'Machine Breaker', 'Right when AISP was wrong', right.some((c) => c.result!.aisp && c.result!.aisp !== c.side)),
    b('hat', '🎩', 'Hat Trick', '3 correct calls in a row', best >= 3),
    b('blind', '🙈', 'Blind Faith', '5 correct BLIND calls', right.filter((c) => c.blind).length >= 5),
    b('max', '⚡', 'All In', 'A correct power-3 call', right.some((c) => c.power === 3)),
    b('contrarian', '🦅', 'Contrarian', '5 calls against AISP', calls.filter((c) => c.result?.aisp && c.result.aisp !== c.side).length >= 5),
    b('owl', '🦉', 'Night Owl', 'Call a game starting 10 PM ET or later', calls.some((c) => new Date(c.starts_at).getUTCHours() >= 2 && new Date(c.starts_at).getUTCHours() < 8)),
    b('card', '🃏', 'Full Card', 'Call 6+ live games in one night', [...nights.values()].some((n) => n.length >= 6)),
    b('oracle', '🔮', 'Oracle', '10 correct calls', right.length >= 10),
    b('streak7', '🔥', 'On Fire', '7 correct in a row', best >= 7),
    b('house', '♠', 'House Breaker', 'Beat the market 5 times going against it', right.filter((c) => c.house && c.house !== c.side).length >= 5),
  ]
}
