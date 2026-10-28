/**
 * MY MODEL'S TRACK RECORD. Every game a saved model scores is written down BEFORE it starts (first write wins per
 * game and model version; nothing is changed after the start), then settled from the final score. The record compares
 * the user's model with AISP and the market on the same games:
 *
 *   hit rate    the side each one made the favourite won
 *   log loss    how surprised each one was by the results (lower is better; the honest scoring rule)
 *   value P&L   a flat $10 play-money stake on every side YOUR model priced above the best US-book price at log time
 *
 * Kept in this browser (aisp-my-model-log-v1). Play money; never mixed with AISP's paper record.
 */
import { evOf } from './lab'

export interface LogEntry {
  game_id: string; sport: string; matchup: string; home: string; away: string; starts_at: string; logged_at: string
  version: number; you: number; aisp: number; market: number | null
  value: { side: 'HOME' | 'AWAY'; american: number; book: string; ev: number } | null
  result?: { home_won: boolean; settled_at: string } | null
}
const KEY = 'aisp-my-model-log-v1'
export const LOG_EVENT = KEY

export function readLog(): LogEntry[] {
  try { return JSON.parse(localStorage.getItem(KEY) ?? '[]') as LogEntry[] } catch { return [] }
}
function writeLog(rows: LogEntry[]): void {
  try { localStorage.setItem(KEY, JSON.stringify(rows.slice(-2000))) } catch { /* storage full or private */ }
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(KEY))
}

/** Write predictions for games that have not started; a game already logged for this version is left alone. */
export function logPredictions(entries: Omit<LogEntry, 'logged_at' | 'result'>[], now = Date.now()): number {
  const rows = readLog()
  const seen = new Set(rows.map((r) => `${r.game_id}|${r.version}`))
  let added = 0
  for (const e of entries) {
    if (Date.parse(e.starts_at) <= now || seen.has(`${e.game_id}|${e.version}`)) continue
    rows.push({ ...e, logged_at: new Date(now).toISOString(), result: null }); seen.add(`${e.game_id}|${e.version}`); added++
  }
  if (added) writeLog(rows)
  return added
}

/** Settle logged games from final scores: finals = game_id -> home won. */
export function settle(finals: Map<string, boolean>, now = Date.now()): number {
  const rows = readLog()
  let n = 0
  for (const r of rows) {
    if (r.result || !finals.has(r.game_id)) continue
    r.result = { home_won: finals.get(r.game_id)!, settled_at: new Date(now).toISOString() }; n++
  }
  if (n) writeLog(rows)
  return n
}

const ll = (p: number, y: boolean) => -Math.log(Math.min(1 - 1e-6, Math.max(1e-6, y ? p : 1 - p)))

export interface Scorecard { n: number; pending: number; you: Side; aisp: Side; market: Side & { n: number }; value: { bets: number; wins: number; pl: number; staked: number } }
interface Side { hits: number; logLoss: number }

/** The head-to-head scorecard; only the latest version's entry per game counts. */
export function scorecard(rows: LogEntry[] = readLog()): Scorecard {
  const latest = new Map<string, LogEntry>()
  for (const r of rows) { const p = latest.get(r.game_id); if (!p || r.version > p.version) latest.set(r.game_id, r) }
  const all = [...latest.values()]
  const done = all.filter((r) => r.result)
  const side = (pick: (r: LogEntry) => number | null, list: LogEntry[]) => {
    const used = list.filter((r) => pick(r) != null)
    return { hits: used.filter((r) => (pick(r)! >= 0.5) === r.result!.home_won).length, logLoss: used.length ? used.reduce((t, r) => t + ll(pick(r)!, r.result!.home_won), 0) / used.length : 0, n: used.length }
  }
  const value = { bets: 0, wins: 0, pl: 0, staked: 0 }
  for (const r of done) {
    if (!r.value) continue
    const won = (r.value.side === 'HOME') === r.result!.home_won
    const dec = r.value.american > 0 ? 1 + r.value.american / 100 : 1 + 100 / Math.abs(r.value.american)
    value.bets++; value.staked += 10; if (won) { value.wins++; value.pl += 10 * (dec - 1) } else value.pl -= 10
  }
  const you = side((r) => r.you, done), aisp = side((r) => r.aisp, done), market = side((r) => r.market, done)
  return { n: done.length, pending: all.length - done.length, you: { hits: you.hits, logLoss: you.logLoss }, aisp: { hits: aisp.hits, logLoss: aisp.logLoss }, market, value }
}

/** The value side for a logged prediction: the side YOUR number prices above the best US book, if any. */
export function valueSide(you: number, prices: { HOME: { american: number; book: string } | null; AWAY: { american: number; book: string } | null }): LogEntry['value'] {
  const opts = (['HOME', 'AWAY'] as const).map((s) => { const q = prices[s]; return q ? { side: s, american: q.american, book: q.book, ev: evOf(s === 'HOME' ? you : 1 - you, q.american) } : null })
    .filter((x): x is NonNullable<typeof x> => x != null && x.ev > 0).sort((a, b) => b.ev - a.ev)
  return opts[0] ?? null
}
