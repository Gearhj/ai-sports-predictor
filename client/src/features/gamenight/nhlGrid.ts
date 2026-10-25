/**
 * NHL has no stored score grid. Its Gen 1 model (NHL_GEN1_REGULATION_POISSON)
 * stores each team's expected regulation goals and the regulation
 * home/tie/away probabilities they imply under independent Poisson scoring.
 *
 * This module rebuilds that grid in the browser and draws it ONLY when it
 * reproduces the stored regulation probabilities to within 1e-4. The check is
 * run on a 0–15 goal grid: truncating at 10 goals already loses enough tail
 * (home .50546 against the stored .50592) to fail it, so the grid is never
 * truncated before the check. A 0–12 window is drawn and the mass outside it
 * is stated.
 *
 * It covers regulation only. Overtime and shootouts are not modelled as scores.
 */
import type { TallyCell } from './stormTally'
import { mulberry32 } from './stormTally'

export const NHL_GRID_MAX = 15
export const NHL_DRAWN_MAX = 12
export const NHL_VERIFY_TOLERANCE = 1e-4

export interface ProbCell { home: number; away: number; p: number }

export function poissonPmf(lambda: number, max: number): number[] {
  const out: number[] = []
  let term = Math.exp(-lambda)
  for (let k = 0; k <= max; k++) {
    if (k > 0) term *= lambda / k
    out.push(term)
  }
  return out
}

/** Independent Poisson grid over 0..max goals for each side. */
export function poissonGrid(lambdaHome: number, lambdaAway: number, max = NHL_GRID_MAX): ProbCell[] {
  const h = poissonPmf(lambdaHome, max)
  const a = poissonPmf(lambdaAway, max)
  const out: ProbCell[] = []
  for (let i = 0; i <= max; i++) for (let j = 0; j <= max; j++) out.push({ home: i, away: j, p: h[i] * a[j] })
  return out
}

export function gridSums(grid: readonly ProbCell[]): { home: number; tie: number; away: number } {
  let home = 0, tie = 0, away = 0
  for (const c of grid) {
    if (c.home > c.away) home += c.p
    else if (c.home < c.away) away += c.p
    else tie += c.p
  }
  return { home, tie, away }
}

export interface GridCheck {
  ok: boolean
  sums: { home: number; tie: number; away: number }
  maxError: number
}

/** Does the derived grid reproduce the stored regulation probabilities? */
export function verifyAgainstStored(
  grid: readonly ProbCell[], regulation: { home: number; tie: number; away: number } | null | undefined, tol = NHL_VERIFY_TOLERANCE,
): GridCheck {
  const sums = gridSums(grid)
  if (!regulation) return { ok: false, sums, maxError: Number.POSITIVE_INFINITY }
  const maxError = Math.max(Math.abs(sums.home - regulation.home), Math.abs(sums.tie - regulation.tie), Math.abs(sums.away - regulation.away))
  return { ok: Number.isFinite(maxError) && maxError <= tol, sums, maxError }
}

/** Probability mass outside the drawn 0..window square. */
export function massOutside(grid: readonly ProbCell[], window = NHL_DRAWN_MAX): number {
  let s = 0
  for (const c of grid) if (c.home > window || c.away > window) s += c.p
  return s
}

/**
 * `n` draws SAMPLED WITH REPLACEMENT from the derived grid (seeded), as cell
 * indices, plus their tally. Unlike the NFL/CFB/MLB replay, this is a sample
 * of the model's distribution, not a stored population, and is labelled so.
 */
export function sampleGrid(grid: readonly ProbCell[], n: number, seed: number): { order: Int32Array; cells: TallyCell[] } {
  const cum = new Float64Array(grid.length)
  let s = 0
  grid.forEach((c, i) => { s += c.p; cum[i] = s })
  const rand = mulberry32(seed)
  const order = new Int32Array(n)
  const counts = new Int32Array(grid.length)
  for (let k = 0; k < n; k++) {
    const r = rand() * s
    let lo = 0, hi = cum.length - 1
    while (lo < hi) { const mid = (lo + hi) >> 1; if (cum[mid] < r) lo = mid + 1; else hi = mid }
    order[k] = lo
    counts[lo] += 1
  }
  return { order, cells: grid.map((c, i) => ({ home: c.home, away: c.away, count: counts[i] })) }
}

export type NhlGridResult =
  | { ok: true; grid: ProbCell[]; check: GridCheck; outside: number }
  | { ok: false; reason: string; check: GridCheck | null }

/** Build and verify the regulation grid from a stored NHL forecast. */
export function nhlRegulationGrid(
  goals: { home: number; away: number } | null | undefined,
  regulation: { home: number; tie: number; away: number } | null | undefined,
): NhlGridResult {
  if (!goals || !Number.isFinite(goals.home) || !Number.isFinite(goals.away) || goals.home <= 0 || goals.away <= 0) {
    return { ok: false, reason: 'The forecast stores no expected regulation goals.', check: null }
  }
  const grid = poissonGrid(goals.home, goals.away, NHL_GRID_MAX)
  const check = verifyAgainstStored(grid, regulation)
  if (!check.ok) {
    return { ok: false, reason: 'The derived grid does not reproduce the stored regulation probabilities, so it is not drawn.', check }
  }
  return { ok: true, grid, check, outside: massOutside(grid, NHL_DRAWN_MAX) }
}
