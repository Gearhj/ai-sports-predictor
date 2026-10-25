/** Test-mode insight feeds (preview server, read-only queries on AISP's own tables) and the price maths they share. */
import { useEffect, useState } from 'react'

export interface ClvRow { id: string; sport: string; selection: string | null; book: string | null; starts_at: string; placed_at: string; stake: number; result: string | null; status: string; entry_decimal: number; close_fair_prob: number; close_fair_decimal: number | null; clv_pct: number; same_book_close_decimal: number | null; beat_same_book: boolean | null; close_books: number; final_close: boolean; close_after_bet: boolean; close_minutes_before_start: number | null }
export interface MoverGame { game_id: string; league: string; starts_at: string; open_home: number; now_home: number; move: number; books: number; steam: Array<{ from: string; to: string; move: number; books_agree: number; books: number }>; series: Array<{ t: string; home: number; books: number }> }
export interface LabRow { sport: string; date: string; model_home: number; market_home: number; home_won: boolean; close_home_dec: number | null; close_away_dec: number | null }

export function usePreviewJson<T>(path: string | null, refreshMs = 0): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!path) return undefined
    let alive = true
    const load = () => fetch(path, { headers: { accept: 'application/json' } })
      .then((r) => (r.ok && (r.headers.get('content-type') ?? '').includes('json') ? r.json() : Promise.reject(new Error(r.status === 404 ? 'only on the test-mode preview' : `HTTP ${r.status}`))))
      .then((d: T) => { if (alive) { setData(d); setError(null) } })
      .catch((e: Error) => { if (alive) setError(e.message) })
    load()
    const t = refreshMs ? window.setInterval(load, refreshMs) : 0
    return () => { alive = false; if (t) window.clearInterval(t) }
  }, [path, refreshMs])
  return { data, error }
}

export const decFromAmerican = (a: number) => (a > 0 ? 1 + a / 100 : 1 + 100 / -a)
export const americanFromDec = (d: number) => (d >= 2 ? Math.round((d - 1) * 100) : Math.round(-100 / (d - 1)))
export const am = (a: number | null | undefined) => (a == null ? '—' : a > 0 ? `+${a}` : `${a}`)
export const pct = (p: number | null | undefined, d = 0) => (p == null ? '—' : `${(p * 100).toFixed(d)}%`)
export const signed = (v: number | null | undefined, d = 1, unit = '') => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}${unit}`)
export const SPORT_COLOR: Record<string, string> = { MLB: '#ff6b57', NFL: '#6ea4ff', CFB: '#b88cff', NHL: '#7cc4e4', NBA: '#ffb14a' }
