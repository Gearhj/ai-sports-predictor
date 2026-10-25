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
 * Two separate cards share this code but never their storage:
 *   - the live card (`aisp.my-card.v1`) accepts only a CURRENT quote;
 *   - the historical replay card (`aisp.my-card.replay.v1`) accepts only a
 *     stored quote that was current at the replay moment. A replay pick can
 *     never enter the live card and a live pick never enters the replay card.
 *
 * A pick freezes exactly what the reader chose: game, market, outcome, line,
 * American and decimal price, sportsbook, provider and the quote's observed
 * time (player props also keep the provider's market, outcome and line). Returns
 * are per straight pick from its stored price; picks are never combined into a
 * parlay. A pick added from a game's top opportunity also freezes AISP's
 * expectation for that exact price at that moment (and whether it was the
 * official pick or a research value); it is never recomputed from a later price.
 */
import { useSyncExternalStore } from 'react'
import type { SportCode } from './commandCenter'

export const MY_CARD_KEY = 'aisp.my-card.v1'
export const REPLAY_CARD_KEY = 'aisp.my-card.replay.v1'
export const MY_CARD_LABEL = 'Your virtual pick — not an AISP decision.'
export const REPLAY_PICK_LABEL = 'Historical virtual pick — not an AISP decision.'
export const MY_CARD_PERSISTENCE = 'BROWSER_LOCAL_DEMO' as const
export const PICK_FRESHNESS_MINUTES = 240

export interface PropDetail {
  quote_id: string
  market_key: string
  market_label: string
  player_id: string
  player_name: string
  team: string
  description: string
  provider_outcome: string
  provider_line: number | null
}

/** AISP's push-aware expectation for the pick's own price, frozen when it was added. */
export interface PickExpectation {
  expected_profit: number
  expected_return: number
  win: number
  loss: number
  push: number
  model_id: string | null
  forecast_id: string | null
}

export type PickOrigin = 'OFFICIAL_PICK' | 'MODEL_VALUE' | 'PERSONAL'

export interface VirtualPick {
  id: string
  game_id: string
  sport: SportCode
  matchup: string
  starts_at: string | null
  market: 'MONEYLINE' | 'SPREAD' | 'TOTAL' | 'PLAYER_PROP'
  outcome: 'HOME' | 'AWAY' | 'OVER' | 'UNDER' | 'YES' | 'NO'
  outcome_label: string
  line: number | null
  american: number
  decimal: number
  sportsbook: string
  provider: string | null
  quote_observed_at: string
  freshness_at_add: 'CURRENT' | 'CURRENT_AT_REPLAY_MOMENT'
  /** The replay moment the price was shown at; absent on live picks. */
  replay_as_of?: string
  prop?: PropDetail
  expectation?: PickExpectation | null
  origin?: PickOrigin
  added_at: string
  stake: number
}

export type NewPick = Omit<VirtualPick, 'id' | 'added_at' | 'stake' | 'freshness_at_add' | 'replay_as_of'>

export function pickKey(p: Pick<VirtualPick, 'game_id' | 'market' | 'outcome' | 'line' | 'sportsbook'> & { prop?: Pick<PropDetail, 'market_key' | 'player_id'> }): string {
  return [p.game_id, p.market, p.prop?.market_key ?? '', p.prop?.player_id ?? '', p.outcome, p.line ?? '', p.sportsbook].join('|')
}

/** A quote is current when observed no more than the freshness window before `at` (and not after it). */
export function isCurrentQuote(observedAt: string, now = Date.now()): boolean {
  const age = (now - Date.parse(observedAt)) / 60_000
  return age >= 0 && age <= PICK_FRESHNESS_MINUTES
}

type Mode = 'live' | 'replay'
interface AddOptions { now?: number; current?: boolean; asOf?: string }

function createCardStore(key: string, mode: Mode) {
  let memory: VirtualPick[] = []
  const listeners = new Set<() => void>()
  const read = (): VirtualPick[] => {
    try {
      const raw = window.localStorage.getItem(key)
      if (!raw) return memory
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed) ? parsed as VirtualPick[] : memory
    } catch {
      return memory
    }
  }
  let snapshot: VirtualPick[] = typeof window === 'undefined' ? [] : read()
  const write = (next: VirtualPick[]) => {
    memory = next
    snapshot = next
    try { window.localStorage.setItem(key, JSON.stringify(next)) } catch { /* private window: memory only */ }
    listeners.forEach((listener) => listener())
  }
  return {
    key,
    mode,
    list: () => snapshot,
    add(pick: NewPick, { now = Date.now(), current = false, asOf }: AddOptions): VirtualPick | null {
      if ((pick as Partial<VirtualPick>).replay_as_of) return null  // a replay pick never crosses stores
      if (mode === 'live') {
        // A stale or reference quote can never become a live pick.
        if (asOf || !current || !isCurrentQuote(pick.quote_observed_at, now)) return null
      } else {
        // Replay: the quote must have been current AT the replay moment, never judged against now.
        if (!asOf || !isCurrentQuote(pick.quote_observed_at, Date.parse(asOf))) return null
      }
      if (!(pick.decimal > 1) || !Number.isFinite(pick.american)) return null
      if (snapshot.some((p) => pickKey(p) === pickKey(pick) && p.replay_as_of === asOf)) return null
      const created: VirtualPick = {
        ...pick, id: `${now.toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
        added_at: new Date(now).toISOString(), stake: 10,
        freshness_at_add: mode === 'live' ? 'CURRENT' : 'CURRENT_AT_REPLAY_MOMENT',
        ...(mode === 'replay' ? { replay_as_of: asOf } : {}),
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
      const onStorage = (event: StorageEvent) => { if (event.key === key) { snapshot = read(); listener() } }
      window.addEventListener('storage', onStorage)
      return () => { listeners.delete(listener); window.removeEventListener('storage', onStorage) }
    },
    /** Test hook: reload from storage. */
    reload() { snapshot = read(); listeners.forEach((l) => l()) },
  }
}

export type CardStore = ReturnType<typeof createCardStore>

export const myCard = createCardStore(MY_CARD_KEY, 'live')
export const replayCard = createCardStore(REPLAY_CARD_KEY, 'replay')

const EMPTY: VirtualPick[] = []

export function useCard(store: CardStore): VirtualPick[] {
  return useSyncExternalStore(store.subscribe, store.list, () => EMPTY)
}

export function useMyCard(): VirtualPick[] {
  return useCard(myCard)
}

/** Straight-pick returns from the stored price only. */
export function pickReturn(p: Pick<VirtualPick, 'stake' | 'decimal'>) {
  const total = Math.round(p.stake * p.decimal * 100) / 100
  return { total, profit: Math.round((total - p.stake) * 100) / 100 }
}
