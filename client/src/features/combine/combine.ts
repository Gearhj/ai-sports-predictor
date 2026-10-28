/**
 * THE COMBINE's maths (Shadow Board, plan page list). Forecasting "prospects" run the same drills on the same scored
 * games, every one with a recorded market price, so nobody gets an easier schedule:
 *
 *   AISP MODEL       the model's two-way home chance
 *   THE MARKET       the no-vig market chance recorded at decision time
 *   BLEND w          the market-anchored rule AISP is shadowing: logit(q) = logit(m) + w·(logit(model) − logit(m))
 *   HOME TEAM        a flat 55% on the home side (a fan's baseline)
 *
 * Drills: log loss (the truth report's scoring rule; lower is better), Brier, hit rate (the favourite won), and
 * honesty (|average said − happened| from the favourite's side). Ties are left out.
 */
import type { GalaxyGame } from '../galaxy/galaxy'

export interface Prospect { key: string; name: string; blurb: string; p: (model: number, market: number) => number }
const logit = (p: number) => Math.log(p / (1 - p))
const sig = (z: number) => 1 / (1 + Math.exp(-z))
const clamp = (p: number) => Math.min(1 - 1e-6, Math.max(1e-6, p))
export const blend = (w: number) => (model: number, market: number) => sig(logit(clamp(market)) + w * (logit(clamp(model)) - logit(clamp(market))))

export const PROSPECTS: Prospect[] = [
  { key: 'model', name: 'AISP MODEL', blurb: 'The model on its own', p: (m) => m },
  { key: 'market', name: 'THE MARKET', blurb: 'No-vig price at decision time', p: (_, k) => k },
  { key: 'blend50', name: 'BLEND ½', blurb: 'Halfway from the market to the model', p: blend(0.5) },
  { key: 'blend20', name: 'BLEND ⅕', blurb: 'The market, nudged a fifth toward the model', p: blend(0.2) },
  { key: 'home', name: 'HOME TEAM', blurb: 'Always 55% on the home side', p: () => 0.55 },
]

export interface Drills { n: number; logLoss: number; brier: number; hit: number; honesty: number }
export const DRILLS: { key: keyof Omit<Drills, 'n'>; label: string; better: 'low' | 'high'; fmt: (v: number) => string; help: string }[] = [
  { key: 'logLoss', label: 'LOG LOSS', better: 'low', fmt: (v) => v.toFixed(4), help: 'How surprised it was by the results. The scoring rule that decides who has information.' },
  { key: 'brier', label: 'BRIER', better: 'low', fmt: (v) => v.toFixed(4), help: 'Squared miss on every game.' },
  { key: 'hit', label: 'HIT RATE', better: 'high', fmt: (v) => `${(v * 100).toFixed(1)}%`, help: 'How often its favourite won.' },
  { key: 'honesty', label: 'HONESTY GAP', better: 'low', fmt: (v) => `${(v * 100).toFixed(1)} pts`, help: 'How far its stated chances sat from what happened.' },
]

export interface Paired { model: number; market: number; homeWon: boolean; sport: string }
export function pairedOf(games: GalaxyGame[], sport = 'ALL'): Paired[] {
  const out: Paired[] = []
  for (const g of games) {
    if (sport !== 'ALL' && g.sport !== sport) continue
    const m = g.decision?.market_p_home, p = g.p_home_two_way
    if (m == null || p == null || (g.actual_outcome !== 'HOME' && g.actual_outcome !== 'AWAY')) continue
    out.push({ model: p, market: m, homeWon: g.actual_outcome === 'HOME', sport: g.sport })
  }
  return out
}

export function drillsOf(rows: Paired[], pro: Prospect): Drills {
  let ll = 0, br = 0, hit = 0, said = 0, happened = 0
  for (const r of rows) {
    const q = clamp(pro.p(r.model, r.market)), y = r.homeWon ? 1 : 0
    ll += -(y * Math.log(q) + (1 - y) * Math.log(1 - q))
    br += (q - y) ** 2
    const favHome = q >= 0.5
    hit += (favHome === r.homeWon ? 1 : 0)
    said += favHome ? q : 1 - q
    happened += favHome === r.homeWon ? 1 : 0
  }
  const n = rows.length || 1
  return { n: rows.length, logLoss: ll / n, brier: br / n, hit: hit / n, honesty: Math.abs(said - happened) / n }
}

/** Draft order: by log loss (lower first). */
export function draftOrder(results: { pro: Prospect; d: Drills }[]): { pro: Prospect; d: Drills }[] {
  return [...results].sort((a, b) => a.d.logLoss - b.d.logLoss)
}

/** Per drill, 0..1 where 1 = the best prospect on that drill and 0 = the worst (for the radar). */
export function normalised(results: { pro: Prospect; d: Drills }[]): Record<string, Record<string, number>> {
  const out: Record<string, Record<string, number>> = {}
  for (const dr of DRILLS) {
    const vals = results.map((r) => r.d[dr.key])
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1
    for (const r of results) {
      out[r.pro.key] ??= {}
      const t = (r.d[dr.key] - lo) / span
      out[r.pro.key][dr.key] = dr.better === 'low' ? 1 - t : t
    }
  }
  return out
}
