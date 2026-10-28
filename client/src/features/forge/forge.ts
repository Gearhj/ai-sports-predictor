/**
 * BUILD A FORECAST's maths (Methodology, plan page list). A teaching version of how a forecast becomes a decision.
 * The user stacks adjustments in points of margin (goals / runs for NHL / MLB); each step converts to a home win
 * chance through the sport's game-to-game spread, p = Φ(margin / σ). Then the decision runs AISP's real moneyline
 * gates in order: the owner's sport switch (record-only sports), the >10-point model-vs-market gap guard, the
 * 2-point edge over the book's no-vig chance, the quarter-Kelly stake on the $1,000 paper bankroll capped at $5
 * (0.5%), and the $1 floor. Teaching numbers, not AISP's production models.
 */
export const SPORTS = ['NFL', 'NHL', 'NBA', 'MLB', 'CFB'] as const
export type Sport = typeof SPORTS[number]
export const SPREAD: Record<Sport, { sigma: number; home: number; out: number; unit: string; outLabel: string; rating: number }> = {
  NFL: { sigma: 13.5, home: 1.5, out: 6, unit: 'pts', outLabel: 'starting QB', rating: 14 },
  NHL: { sigma: 2.4, home: 0.15, out: 0.35, unit: 'goals', outLabel: 'starting goalie', rating: 1.5 },
  NBA: { sigma: 12, home: 2.5, out: 4, unit: 'pts', outLabel: 'star player', rating: 15 },
  MLB: { sigma: 4.3, home: 0.2, out: 0.6, unit: 'runs', outLabel: 'ace starter', rating: 2 },
  CFB: { sigma: 16, home: 3, out: 7, unit: 'pts', outLabel: 'starting QB', rating: 28 },
}
/** Owner rule since 10-25 / 10-26: only NFL and NHL are funded; the rest record every decision with no stake. */
export const FUNDED: Record<Sport, boolean> = { NFL: true, NHL: true, NBA: false, MLB: false, CFB: false }
export const BANKROLL = 1000, CAP = 5, FLOOR = 1, MIN_EDGE = 0.02, GAP_GUARD = 0.10

/** Standard normal CDF (Abramowitz–Stegun 7.1.26 via erf). */
export function phi(z: number): number {
  const t = 1 / (1 + 0.3275911 * Math.abs(z) / Math.SQRT2)
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-(z * z) / 2)
  return z >= 0 ? 0.5 * (1 + y) : 0.5 * (1 - y)
}

export interface Build { sport: Sport; strength: number; homeField: boolean; rest: number; homeOut: boolean; awayOut: boolean }
export interface Step { key: string; label: string; short: string; margin: number; p: number }

export function stepsOf(b: Build): Step[] {
  const S = SPREAD[b.sport]
  const parts: [string, string, string, number][] = [
    ['start', 'Coin flip', 'Coin flip', 0],
    ['strength', 'Team strength', 'Strength', b.strength],
    ['home', 'Home advantage', 'Home field', b.homeField ? S.home : 0],
    ['rest', 'Rest & travel', 'Rest', b.rest],
    ['homeOut', `Home ${S.outLabel} out`, 'Home out', b.homeOut ? -S.out : 0],
    ['awayOut', `Away ${S.outLabel} out`, 'Away out', b.awayOut ? S.out : 0],
  ]
  let m = 0
  return parts.map(([key, label, short, d]) => { m += d; return { key, label, short, margin: m, p: phi(m / S.sigma) } })
}

export const decimalOf = (american: number) => (american > 0 ? 1 + american / 100 : 1 + 100 / Math.abs(american))
/** The two prices' no-vig home chance. */
export function noVig(homePrice: number, awayPrice: number): number {
  const h = 1 / decimalOf(homePrice), a = 1 / decimalOf(awayPrice)
  return h / (h + a)
}

export type Verdict = 'BET' | 'PASS' | 'RECORD_ONLY'
export interface Gate { key: string; label: string; pass: boolean; detail: string }
export interface Decision { side: 'HOME' | 'AWAY'; model: number; market: number; edge: number; price: number; gates: Gate[]; stake: number; verdict: Verdict }

/** AISP's moneyline gates, in order, for whichever side the model likes more than the market. */
export function decide(sport: Sport, pHome: number, homePrice: number, awayPrice: number): Decision {
  const mHome = noVig(homePrice, awayPrice)
  const home = pHome - mHome >= 0
  const model = home ? pHome : 1 - pHome, market = home ? mHome : 1 - mHome, price = home ? homePrice : awayPrice
  const edge = model - market
  const dec = decimalOf(price), b = dec - 1
  const kelly = Math.max(0, (b * model - (1 - model)) / b)
  const stake = Math.min(CAP, Math.round(BANKROLL * kelly * 0.25 * 100) / 100)
  const gates: Gate[] = [
    { key: 'gap', label: 'Model within 10 pts of the market', pass: Math.abs(edge) <= GAP_GUARD, detail: Math.abs(edge) <= GAP_GUARD ? `${(Math.abs(edge) * 100).toFixed(1)} pts apart` : `${(Math.abs(edge) * 100).toFixed(1)} pts apart: gaps this big have been where AISP is wrong, so they are recorded, not bet` },
    { key: 'edge', label: 'Edge of 2+ pts over the no-vig price', pass: edge >= MIN_EDGE, detail: `${(edge * 100).toFixed(1)} pts (${(model * 100).toFixed(1)}% vs ${(market * 100).toFixed(1)}%)` },
    { key: 'stake', label: 'Quarter-Kelly stake of $1+ (cap $5)', pass: stake >= FLOOR, detail: `$${stake.toFixed(2)} on the $1,000 paper bankroll` },
    { key: 'funded', label: `${sport} is a funded sport`, pass: FUNDED[sport], detail: FUNDED[sport] ? 'NFL and NHL are funded at $5 measurement bets' : `${sport} records every decision with no stake (owner rule)` },
  ]
  const verdict: Verdict = !gates[0].pass ? 'RECORD_ONLY' : !gates[1].pass || !gates[2].pass ? 'PASS' : !gates[3].pass ? 'RECORD_ONLY' : 'BET'
  return { side: home ? 'HOME' : 'AWAY', model, market, edge, price, gates, stake: verdict === 'BET' ? stake : 0, verdict }
}
