/**
 * THE EXPERIMENT LAB's maths (Game Night, owner 10-26: "let the user select some parameters ... default and an
 * acceptable range ... use their game knowledge"). Two simple, explainable game models:
 *
 *   GOALS (NHL, MLB): each team's score ~ Poisson(λ). Defaults: the league's typical total split so the Poisson
 *     win chance matches AISP's model probability. Ties after regulation go to a coin flip (OT / extras).
 *   MARGIN (NBA, NFL, CFB): home margin ~ Normal(μ, σ). Default σ is the league's typical game-to-game spread;
 *     default μ = σ·Φ⁻¹(model probability), so the default run reproduces AISP's number.
 *
 * The user moves the inputs inside a range; we simulate 10,000 games with a fixed seed and report the win chance,
 * the fair American price, and value against a book price. Teaching tools, not AISP's production models.
 */
export type Kind = 'GOALS' | 'MARGIN'
export const KIND: Record<string, Kind> = { NHL: 'GOALS', MLB: 'GOALS', NBA: 'MARGIN', NFL: 'MARGIN', CFB: 'MARGIN' }
export const LEAGUE: Record<string, { total: number; totalSigma: number; sigma?: number; unit: string; outPts: number; outLabel: string }> = {
  NHL: { total: 6.1, totalSigma: 0, unit: 'goals', outPts: 0.35, outLabel: 'starting goalie / top-line star out' },
  MLB: { total: 8.8, totalSigma: 0, unit: 'runs', outPts: 0.6, outLabel: 'ace starter / top bat out' },
  NBA: { total: 226, totalSigma: 18, sigma: 12, unit: 'points', outPts: 4, outLabel: 'star player out' },
  NFL: { total: 44, totalSigma: 10, sigma: 13.5, unit: 'points', outPts: 6, outLabel: 'starting QB out' },
  CFB: { total: 54, totalSigma: 14, sigma: 16, unit: 'points', outPts: 7, outLabel: 'starting QB out' },
}

/** Acklam's inverse normal CDF (|error| < 1.2e-9). */
export function invNorm(p: number): number {
  const q = Math.min(1 - 1e-12, Math.max(1e-12, p))
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239]
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572]
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783]
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416]
  const lo = 0.02425, hi = 1 - lo
  if (q < lo) { const r = Math.sqrt(-2 * Math.log(q)); return (((((c[0] * r + c[1]) * r + c[2]) * r + c[3]) * r + c[4]) * r + c[5]) / ((((d[0] * r + d[1]) * r + d[2]) * r + d[3]) * r + 1) }
  if (q > hi) { const r = Math.sqrt(-2 * Math.log(1 - q)); return -(((((c[0] * r + c[1]) * r + c[2]) * r + c[3]) * r + c[4]) * r + c[5]) / ((((d[0] * r + d[1]) * r + d[2]) * r + d[3]) * r + 1) }
  const r = q - 0.5, s = r * r
  return (((((a[0] * s + a[1]) * s + a[2]) * s + a[3]) * s + a[4]) * s + a[5]) * r / (((((b[0] * s + b[1]) * s + b[2]) * s + b[3]) * s + b[4]) * s + 1)
}

const pmf = (k: number, l: number) => Math.exp(-l + k * Math.log(l) - lgamma(k + 1))
function lgamma(n: number): number { let s = 0; for (let i = 2; i < n; i++) s += Math.log(i); return s }
/** Exact P(home wins) for independent Poissons, regulation ties split 50/50. */
export function poissonWin(lh: number, la: number): number {
  let win = 0, tie = 0
  for (let h = 0; h <= 30; h++) { const ph = pmf(h, lh); for (let a = 0; a <= 30; a++) { const p = ph * pmf(a, la); if (h > a) win += p; else if (h === a) tie += p } }
  return win + tie / 2
}
/** Split a typical total so the Poisson win chance matches p (bisection on the home share). */
export function splitTotal(total: number, p: number): { home: number; away: number } {
  let lo = 0.05, hi = 0.95
  for (let i = 0; i < 50; i++) { const m = (lo + hi) / 2; if (poissonWin(total * m, total * (1 - m)) < p) lo = m; else hi = m }
  const s = (lo + hi) / 2
  return { home: total * s, away: total * (1 - s) }
}

export interface Inputs { kind: Kind; home: number; away: number; mu: number; sigma: number; total: number; totalSigma: number; homeOut: boolean; awayOut: boolean; outPts: number }
export function defaults(sport: string, modelHome: number): Inputs {
  const L = LEAGUE[sport] ?? LEAGUE.NBA
  if (KIND[sport] === 'GOALS') { const s = splitTotal(L.total, modelHome); return { kind: 'GOALS', home: s.home, away: s.away, mu: 0, sigma: 0, total: L.total, totalSigma: 0, homeOut: false, awayOut: false, outPts: L.outPts } }
  return { kind: 'MARGIN', home: 0, away: 0, mu: L.sigma! * invNorm(modelHome), sigma: L.sigma!, total: L.total, totalSigma: L.totalSigma, homeOut: false, awayOut: false, outPts: L.outPts }
}

export interface SimOut { pHome: number; margins: Int16Array; homeScores: Int16Array; awayScores: Int16Array }
/** 10,000 seeded games. Key-player toggles take `outPts` off that side (goals/runs, or points of margin). */
export function simulate(x: Inputs, n = 10_000, seed = 17): SimOut {
  let s = seed >>> 0
  const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return (s + 0.5) / 4294967296 }
  const margins = new Int16Array(n)
  let wins = 0
  if (x.kind === 'GOALS') {
    const lh = Math.max(0.2, x.home - (x.homeOut ? x.outPts : 0)), la = Math.max(0.2, x.away - (x.awayOut ? x.outPts : 0))
    const pois = (l: number) => { const L = Math.exp(-l); let k = 0, p = 1; do { k++; p *= rnd() } while (p > L); return k - 1 }
    const hs = new Int16Array(n), as = new Int16Array(n)
    for (let i = 0; i < n; i++) {
      const h = pois(lh), a = pois(la)
      hs[i] = h; as[i] = a; margins[i] = h - a
      if (h > a || (h === a && rnd() < 0.5)) wins++
    }
    return { pHome: wins / n, margins, homeScores: hs, awayScores: as }
  }
  // MARGIN: the margin decides the winner; a total (Normal around the expected total) turns it into a score pair
  // for the score grid. Scores are clamped at zero, never re-deciding the winner.
  const mu = x.mu - (x.homeOut ? x.outPts : 0) + (x.awayOut ? x.outPts : 0)
  const hs = new Int16Array(n), as = new Int16Array(n)
  for (let i = 0; i < n; i++) {
    const z = Math.sqrt(-2 * Math.log(rnd())) * Math.cos(2 * Math.PI * rnd())
    const zt = Math.sqrt(-2 * Math.log(rnd())) * Math.cos(2 * Math.PI * rnd())
    const m = Math.round(mu + x.sigma * z)
    margins[i] = m
    const t = Math.max(Math.abs(m), Math.round(x.total + x.totalSigma * zt))
    hs[i] = Math.max(0, Math.round((t + m) / 2)); as[i] = Math.max(0, hs[i] - m)
    if (m > 0 || (m === 0 && rnd() < 0.5)) wins++
  }
  return { pHome: wins / n, margins, homeScores: hs, awayScores: as }
}

/** Fair American odds for a probability. */
export const fairAmerican = (p: number) => (p <= 0 || p >= 1 ? null : p >= 0.5 ? -Math.round((100 * p) / (1 - p)) : Math.round((100 * (1 - p)) / p))
/** Expected value per $1 of taking `american` when the true chance is p. */
export function evOf(p: number, american: number): number {
  const dec = american > 0 ? 1 + american / 100 : 1 + 100 / Math.abs(american)
  return p * (dec - 1) - (1 - p)
}

/** The simulated games as a score grid: how many of the n draws landed on each final score. */
export function tallyOf(sim: SimOut): { home: number; away: number; count: number }[] {
  const m = new Map<number, number>()
  for (let i = 0; i < sim.homeScores.length; i++) { const k = sim.homeScores[i] * 1000 + sim.awayScores[i]; m.set(k, (m.get(k) ?? 0) + 1) }
  return [...m.entries()].map(([k, count]) => ({ home: Math.floor(k / 1000), away: k % 1000, count }))
}

/** Box-plot numbers for a margin sample: 5th, 25th, 50th, 75th, 95th percentiles. */
export function quantiles(margins: Int16Array): [number, number, number, number, number] {
  const a = Array.from(margins).sort((x, y) => x - y)
  const q = (p: number) => a[Math.min(a.length - 1, Math.max(0, Math.round(p * (a.length - 1))))]
  return [q(0.05), q(0.25), q(0.5), q(0.75), q(0.95)]
}

/** Expected final score (the centre of the 10,000 games) for a home win chance, with chaos and a total shift. */
export function projectedScore(sport: string, p: number, chaos = 1, totalShift = 0): { home: number; away: number } {
  const L = LEAGUE[sport] ?? LEAGUE.NBA
  if (KIND[sport] === 'GOALS') { const s = splitTotal(Math.max(1, L.total * chaos + totalShift), p); return { home: s.home, away: s.away } }
  const mu = (L.sigma ?? 12) * invNorm(Math.min(0.999, Math.max(0.001, p)))
  const total = L.total + totalShift
  return { home: (total + mu) / 2, away: (total - mu) / 2 }
}
