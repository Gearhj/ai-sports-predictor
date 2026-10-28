/**
 * The Game Simulator's decision tree (owner 10-27: "a decision tree analysis where 10K balls go down the decision
 * tree Plinko chart and shows how the distribution is formed"). Pure, tested.
 *
 * Every simulated game (a stored cell) answers three questions on its way down, in the order a bettor reads a game:
 *   1. Does the model's favourite win?
 *   2. Favourite won  -> did it cover the spread?      Favourite didn't -> was it close (the dog by <= one score, or a tie)?
 *   3. Did the game go over the total?
 * That gives eight leaves, laid out left to right from "dog wins big" to "favourite covers", each split under/over,
 * so the piles at the bottom are the outcome distribution read through the bets you can actually make.
 */
import type { Cell, SimModel } from './simData'

export const CLOSE: Record<string, number> = { NFL: 8, CFB: 8, NBA: 6, NHL: 1, MLB: 2 }

export interface TreeSetup {
  fav: 'home' | 'away'
  favAbbr: string
  dogAbbr: string
  /** points the favourite gives (> 0): covers when its margin is strictly greater */
  spread: number
  spreadFromBook: boolean
  total: number
  totalFromBook: boolean
  close: number
}

const half = (x: number) => Math.round(x * 2) / 2

/** The favourite's margin and the total for one cell. */
export const favMargin = (c: Cell, fav: 'home' | 'away') => (fav === 'home' ? c.home - c.away : c.away - c.home)

function quantile(cells: Cell[], f: (c: Cell) => number, q: number): number {
  const pts = cells.map((c) => ({ v: f(c), p: c.p })).sort((a, b) => a.v - b.v)
  const tot = pts.reduce((s, x) => s + x.p, 0)
  let acc = 0
  for (const x of pts) { acc += x.p; if (acc >= q * tot) return x.v }
  return pts[pts.length - 1]?.v ?? 0
}

export function setupFor(m: SimModel, overrides: { spread?: number; total?: number } = {}): TreeSetup {
  const fav: 'home' | 'away' = m.win.home >= m.win.away ? 'home' : 'away'
  const book = m.bookLines.spreadHome
  const favLine = book == null ? null : fav === 'home' ? book : -book
  let spread = favLine != null && favLine < 0 ? -favLine : NaN
  const spreadFromBook = Number.isFinite(spread)
  if (!spreadFromBook) spread = Math.max(0.5, Math.floor(quantile(m.cells, (c) => favMargin(c, fav), 0.5)) + 0.5)
  let total = m.bookLines.total ?? NaN
  const totalFromBook = Number.isFinite(total)
  if (!totalFromBook) total = Math.floor(quantile(m.cells, (c) => c.home + c.away, 0.5)) + 0.5
  return {
    fav, favAbbr: fav === 'home' ? m.home.abbr : m.away.abbr, dogAbbr: fav === 'home' ? m.away.abbr : m.home.abbr,
    spread: overrides.spread != null ? Math.max(0.5, half(overrides.spread)) : spread, spreadFromBook: overrides.spread == null && spreadFromBook,
    total: overrides.total != null ? Math.max(0.5, half(overrides.total)) : total, totalFromBook: overrides.total == null && totalFromBook,
    close: CLOSE[m.sport] ?? 6,
  }
}

/** The margin group (0 dog big, 1 dog close/tie, 2 fav without cover, 3 fav covers) and the leaf (group * 2 + over). */
export function route(c: Cell, s: TreeSetup): { group: number; leaf: number; favWins: boolean; covers: boolean | null; close: boolean | null; over: boolean; margin: number; total: number } {
  const margin = favMargin(c, s.fav)
  const total = c.home + c.away
  const favWins = margin > 0
  const covers = favWins ? margin > s.spread : null
  const close = favWins ? null : -margin <= s.close
  const group = favWins ? (covers ? 3 : 2) : (close ? 1 : 0)
  const over = total > s.total
  return { group, leaf: group * 2 + (over ? 1 : 0), favWins, covers, close, over, margin, total }
}

export interface Leaf { leaf: number; group: number; over: boolean; p: number; top: Cell | null; label: string; short: string }

export function leaves(cells: Cell[], s: TreeSetup): Leaf[] {
  const out: Leaf[] = Array.from({ length: 8 }, (_, i) => ({ leaf: i, group: i >> 1, over: (i & 1) === 1, p: 0, top: null, label: '', short: '' }))
  const best: number[] = new Array(8).fill(-1)
  for (const c of cells) {
    const r = route(c, s)
    out[r.leaf].p += c.p
    if (c.p > best[r.leaf]) { best[r.leaf] = c.p; out[r.leaf].top = c }
  }
  const groupLabel = [
    `${s.dogAbbr} wins by ${s.close + 1}+`,
    `${s.dogAbbr} by ≤${s.close} or tie`,
    `${s.favAbbr} wins, no cover`,
    `${s.favAbbr} covers −${s.spread}`,
  ]
  for (const l of out) {
    const ou = `${l.over ? 'OVER' : 'UNDER'} ${s.total}`
    l.label = `${groupLabel[l.group]} · ${ou}`
    l.short = ou
  }
  const tot = out.reduce((a, l) => a + l.p, 0) || 1
  for (const l of out) l.p /= tot
  return out
}

/** Fair American odds for a probability (null outside (0, 1)). */
export const fairOdds = (p: number): number | null => (p <= 0 || p >= 1 ? null : p >= 0.5 ? -Math.round((100 * p) / (1 - p)) : Math.round((100 * (1 - p)) / p))
