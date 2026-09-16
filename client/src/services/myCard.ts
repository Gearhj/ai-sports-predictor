/**
 * My Card — the reader's own virtual picks, kept in THIS browser only.
 *
 * Deliberately isolated from everything official:
 *   - no request is made to the AISP API when a pick is added, staked or
 *     removed, so no decision, position, settlement, experiment bankroll or
 *     performance metric can be created or changed from here;
 *   - the store is browser-local (localStorage, with an in-memory fallback
 *     when storage is unavailable). It is a demo card, not account data.
 *
 * A pick freezes exactly what the reader chose: game, market, outcome, line,
 * American and decimal price, sportsbook, provider and the quote's observed
 * time. Only a CURRENT quote can be added. Returns are per straight pick from
 * its stored price; picks are never combined into a parlay, and no model
 * probability is attached to a pick.
 */
import { useSyncExternalStore } from 'react'
import type { SportCode } from './commandCenter'

export const MY_CARD_KEY = 'aisp.my-card.v1'
export const MY_CARD_LABEL = 'Your virtual pick — not an AISP decision.'
export const MY_CARD_PERSISTENCE = 'BROWSER_LOCAL_DEMO' as const
export const PICK_FRESHNESS_MINUTES = 240

export interface VirtualPick {
  id: string
  game_id: string
  sport: SportCode
  matchup: string
  starts_at: string | null
  market: 'MONEYLINE' | 'SPREAD' | 'TOTAL'
  outcome: 'HOME' | 'AWAY' | 'OVER' | 'UNDER'
  outcome_label: string
  line: number | null
  american: number
  decimal: number
  sportsbook: string
  provider: string | null
  quote_observed_at: string
  freshness_at_add: 'CURRENT'
  added_at: string
  stake: number
}

export type NewPick = Omit<VirtualPick, 'id' | 'added_at' | 'stake' | 'freshness_at_add'>

let memory: VirtualPick[] = []
const listeners = new Set<() => void>()

function read(): VirtualPick[] {
  try {
    const raw = window.localStorage.getItem(MY_CARD_KEY)
    if (!raw) return memory
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed as VirtualPick[] : memory
  } catch {
    return memory
  }
}

let snapshot: VirtualPick[] = typeof window === 'undefined' ? [] : read()

function write(next: VirtualPick[]) {
  memory = next
  snapshot = next
  try { window.localStorage.setItem(MY_CARD_KEY, JSON.stringify(next)) } catch { /* private window: memory only */ }
  listeners.forEach((listener) => listener())
}

export function pickKey(p: Pick<VirtualPick, 'game_id' | 'market' | 'outcome' | 'line' | 'sportsbook'>): string {
  return [p.game_id, p.market, p.outcome, p.line ?? '', p.sportsbook].join('|')
}

export function isCurrentQuote(observedAt: string, now = Date.now()): boolean {
  const age = (now - Date.parse(observedAt)) / 60_000
  return age >= 0 && age <= PICK_FRESHNESS_MINUTES
}

export const myCard = {
  list: () => snapshot,
  add(pick: NewPick, { now = Date.now(), current }: { now?: number; current: boolean }): VirtualPick | null {
    // A stale or reference quote can never become a pick.
    if (!current || !isCurrentQuote(pick.quote_observed_at, now)) return null
    if (!(pick.decimal > 1) || !Number.isFinite(pick.american)) return null
    if (snapshot.some((p) => pickKey(p) === pickKey(pick))) return null
    const created: VirtualPick = {
      ...pick, id: `${now.toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      added_at: new Date(now).toISOString(), stake: 10, freshness_at_add: 'CURRENT',
    }
    write([...snapshot, created])
    return created
  },
  setStake(id: string, stake: number) {
    const clean = Number.isFinite(stake) && stake >= 0 ? Math.round(stake * 100) / 100 : 0
    write(snapshot.map((p) => (p.id === id ? { ...p, stake: clean } : p)))
  },
  remove(id: string) { write(snapshot.filter((p) => p.id !== id)) },
  clear() { write([]) },
  subscribe(listener: () => void) {
    listeners.add(listener)
    const onStorage = (event: StorageEvent) => { if (event.key === MY_CARD_KEY) { snapshot = read(); listener() } }
    window.addEventListener('storage', onStorage)
    return () => { listeners.delete(listener); window.removeEventListener('storage', onStorage) }
  },
  /** Test hook: reload from storage. */
  reload() { snapshot = read(); listeners.forEach((l) => l()) },
}

export function useMyCard(): VirtualPick[] {
  return useSyncExternalStore(myCard.subscribe, myCard.list, () => [])
}

/** Straight-pick returns from the stored price only. */
export function pickReturn(p: Pick<VirtualPick, 'stake' | 'decimal'>) {
  const total = Math.round(p.stake * p.decimal * 100) / 100
  return { total, profit: Math.round((total - p.stake) * 100) / 100 }
}
