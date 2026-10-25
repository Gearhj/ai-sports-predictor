/**
 * Personal PREVIEW picks — a reader tracking a game and a side for themselves.
 *
 * This mirrors the community prototype's play-money semantics (no cash value,
 * following never copies picks) WITHOUT fabricating balances, prices, decisions
 * or outcomes. A preview pick is only: a game, a chosen side and the model's
 * two-way lean for context. It is stored ONLY in this browser (localStorage).
 *
 * It is NOT the official AISP $1,000 paper experiment (that lives under Record /
 * `/experiment`): it places no price, records no decision and settles no result.
 * Nothing here is an executable wager.
 */
import { useEffect, useState } from 'react'

export interface PreviewPick {
  id: string
  game_id: string
  sport: string
  matchup: string
  away: string
  home: string
  side: 'HOME' | 'AWAY'
  selection: string
  /** The model's home two-way probability at the time, for context only. */
  model_home_two_way: number | null
  /** Back to the Game Brief this pick was made from. */
  href: string
  added_at: string
  // ---- bet-slip fields (2026-10-24; all optional so older saved picks still load) ----
  /** The sportsbook quote seen when the pick was added (observed market data, not an order). */
  price_american?: number | null
  price_book?: string | null
  price_seen_at?: string | null
  /** No-vig market probability for the chosen side when added. */
  market_prob?: number | null
  /** Play-money stake the reader chose (no cash value). */
  stake?: number | null
  /** 'slip' while still being considered; 'locked' once the reader locks the play-money pick. */
  status?: 'slip' | 'locked'
  locked_at?: string | null
  starts_at?: string | null
}

const KEY = 'aisp-preview-picks-v1'
const EVENT = 'aisp-preview-picks'

function read(): PreviewPick[] {
  try { const raw = localStorage.getItem(KEY); return raw ? (JSON.parse(raw) as PreviewPick[]) : [] } catch { return [] }
}
function write(list: PreviewPick[]): void {
  try { localStorage.setItem(KEY, JSON.stringify(list)) } catch { /* storage unavailable */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(EVENT))
}

export function listPicks(): PreviewPick[] { return read() }
export function pickFor(gameId: string): PreviewPick | null { return read().find((p) => p.game_id === gameId) ?? null }

/** One tracked side per game: choosing again replaces the prior side. */
export function addPick(pick: Omit<PreviewPick, 'id' | 'added_at'>): void {
  const list = read().filter((p) => p.game_id !== pick.game_id)
  list.unshift({ ...pick, id: `${pick.game_id}:${pick.side}`, added_at: new Date().toISOString() })
  write(list)
}
export function removePick(gameId: string): void { write(read().filter((p) => p.game_id !== gameId)) }
/** Change slip fields of one pick (stake, status). Locked picks keep their captured price. */
export function updatePick(gameId: string, patch: Partial<Pick<PreviewPick, 'stake' | 'status' | 'locked_at' | 'side' | 'selection' | 'price_american' | 'price_book' | 'price_seen_at' | 'market_prob' | 'model_home_two_way'>>): void {
  write(read().map((p) => (p.game_id === gameId ? { ...p, ...patch } : p)))
}
/** The play-money bankroll the reader sizes against (browser only; default $1,000). */
const BANK = 'aisp-play-bankroll-v1'
export function playBankroll(): number { try { const v = Number(localStorage.getItem(BANK)); return Number.isFinite(v) && v > 0 ? v : 1000 } catch { return 1000 } }
export function setPlayBankroll(v: number): void { try { localStorage.setItem(BANK, String(Math.max(10, Math.round(v)))) } catch { /* storage unavailable */ } if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(EVENT)) }

export function subscribe(fn: () => void): () => void {
  if (typeof window === 'undefined') return () => {}
  const h = () => fn()
  window.addEventListener(EVENT, h)
  window.addEventListener('storage', h) // reflect changes made in another tab
  return () => { window.removeEventListener(EVENT, h); window.removeEventListener('storage', h) }
}

export function usePreviewPicks(): PreviewPick[] {
  const [list, setList] = useState<PreviewPick[]>(() => listPicks())
  useEffect(() => subscribe(() => setList(listPicks())), [])
  return list
}

export function usePreviewPick(gameId: string | null | undefined): PreviewPick | null {
  const [pick, setPick] = useState<PreviewPick | null>(() => (gameId ? pickFor(gameId) : null))
  useEffect(() => {
    if (!gameId) { setPick(null); return }
    const sync = () => setPick(pickFor(gameId))
    sync()
    return subscribe(sync)
  }, [gameId])
  return pick
}
