/**
 * The bet-slip math shared by Games, My Picks and the Game Plan bar. Play money only: nothing here places,
 * records or settles a real wager; prices are the sportsbook quotes AISP already observed.
 *
 *  - pickOf(g): the side AISP's paper policy would take (its entered position, its recorded decision, else the
 *    model's lean/favourite) with model and no-vig market probability and the best observed price.
 *  - breakEven / edge / EV / Kelly: the standard moneyline arithmetic.
 *  - simulate(): Monte Carlo of a slip under one set of probabilities (model or market).
 */
import type { PulseGame } from '../pulse/pulseData'
import { addPick, type PreviewPick } from '../../services/previewPicks'
import { gameHref } from '../../services/commandCenter'

export type Pick = { side: 'HOME' | 'AWAY'; model: number; market: number | null; edge: number | null; price: number | null; book: string | null; source: 'position' | 'decision' | 'lean' | 'model' }

export function pickOf(g: PulseGame): Pick | null {
  const fromSel = (sel: string | null | undefined): 'HOME' | 'AWAY' | null => { const s = (sel ?? '').toUpperCase(); if (!s) return null; if (s.includes(g.home.abbr.toUpperCase()) || s.includes('HOME')) return 'HOME'; if (s.includes(g.away.abbr.toUpperCase()) || s.includes('AWAY')) return 'AWAY'; return null }
  const side = (g.position && fromSel(g.position.selection)) || (g.decision && fromSel(g.decision.selection)) || g.lean?.side || g.favorite?.side || null
  if (!side || g.modelHome == null) return null
  return sideView(g, side, g.position ? 'position' : g.decision && /QUALIFIED|BET/i.test(g.decision.recommendation) ? 'decision' : g.lean ? 'lean' : 'model')
}
/** Model, market and price for a chosen side of a game. */
export function sideView(g: PulseGame, side: 'HOME' | 'AWAY', source: Pick['source'] = 'model'): Pick | null {
  if (g.modelHome == null) return null
  const model = side === 'HOME' ? g.modelHome : 1 - g.modelHome
  const market = g.marketHome == null ? null : side === 'HOME' ? g.marketHome : 1 - g.marketHome
  const best = side === 'HOME' ? g.bestHome : g.bestAway
  return { side, model, market: g.marketFresh ? market : null, edge: g.marketFresh && market != null ? (model - market) * 100 : null, price: best?.american ?? null, book: best?.book ?? null, source }
}

export const decimalOf = (american: number) => (american > 0 ? 1 + american / 100 : 1 + 100 / Math.abs(american))
export const breakEven = (american: number) => 1 / decimalOf(american)
/** Expected profit per $1 staked at this price if `p` is the true win probability. */
export const evPerDollar = (p: number, american: number) => p * (decimalOf(american) - 1) - (1 - p)
/** Full-Kelly fraction of bankroll (0 when the bet has no edge at this price). */
export function kelly(p: number, american: number): number { const b = decimalOf(american) - 1; return Math.max(0, (b * p - (1 - p)) / b) }
/** Suggested play stake: quarter Kelly, capped at 5% of the bankroll, rounded to $1. */
export const suggestStake = (p: number, american: number, bankroll: number) => Math.round(Math.min(0.05, kelly(p, american) / 4) * bankroll)

export function addToSlip(g: PulseGame, side?: 'HOME' | 'AWAY'): void {
  const pk = side ? sideView(g, side) : pickOf(g)
  if (!pk) return
  const team = pk.side === 'HOME' ? g.home : g.away
  addPick({ game_id: g.id, sport: g.sport, matchup: `${g.away.abbr} @ ${g.home.abbr}`, away: g.away.abbr, home: g.home.abbr, side: pk.side, selection: team.abbr, model_home_two_way: g.modelHome, href: gameHref(g.sport, g.id), price_american: pk.price, price_book: pk.book, price_seen_at: g.marketObservedAt ?? new Date().toISOString(), market_prob: pk.market, stake: null, status: 'slip', starts_at: g.startsAt } as Omit<PreviewPick, 'id' | 'added_at'>)
}

export interface SimLeg { p: number; stake: number; american: number }
export interface SimResult { n: number; pl: Float64Array; mean: number; pProfit: number; p05: number; p50: number; p95: number; best: number; worst: number; allLose: number; allWin: number }
/** Monte Carlo of independent legs: each night every leg wins with its probability. Deterministic seed for a stable picture. */
export function simulate(legs: SimLeg[], n = 10_000, seed = 7): SimResult {
  let s = seed >>> 0
  const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296 }
  const pl = new Float64Array(n)
  let allLose = 0; let allWin = 0
  for (let i = 0; i < n; i += 1) {
    let tot = 0; let wins = 0
    for (const l of legs) { if (rnd() < l.p) { tot += l.stake * (decimalOf(l.american) - 1); wins += 1 } else tot -= l.stake }
    pl[i] = tot
    if (wins === 0) allLose += 1
    if (wins === legs.length) allWin += 1
  }
  const sorted = Float64Array.from(pl).sort()
  const q = (f: number) => sorted[Math.min(n - 1, Math.max(0, Math.floor(f * n)))]
  const mean = pl.reduce((t, v) => t + v, 0) / n
  return { n, pl, mean, pProfit: pl.filter((v) => v > 0.0001).length / n, p05: q(0.05), p50: q(0.5), p95: q(0.95), best: sorted[n - 1], worst: sorted[0], allLose: allLose / n, allWin: allWin / n }
}
