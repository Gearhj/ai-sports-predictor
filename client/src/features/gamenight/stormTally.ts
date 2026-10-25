/**
 * "The 10,000": pure maths for replaying a model's stored score tally.
 *
 * AISP stores, per forecast, how many of the model's simulated draws landed on
 * each exact score (a multiset of cells). It does NOT store the order the draws
 * were made in. The replay therefore shuffles the exact multiset with a seeded
 * generator: the order is ours, but every cell receives exactly its stored
 * count, so the finished picture is the model's tally and nothing else.
 *
 * Nothing here models a game. It only counts, ranks and shuffles stored cells.
 */

export interface TallyCell { home: number; away: number; count: number }

export type Side = 'HOME' | 'AWAY'
export type CellTone = 'pick' | 'fade' | 'tie'

/** A small, fast, seedable PRNG (public-domain mulberry32). Returns [0, 1). */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function totalCount(cells: readonly TallyCell[]): number {
  let s = 0
  for (const c of cells) s += c.count
  return s
}

/**
 * Every draw of the stored multiset, as a cell index, in a seeded shuffled
 * order (Fisher–Yates). The result has exactly `cells[i].count` entries equal
 * to `i`, for every i, so replaying it rebuilds the stored tally exactly.
 */
export function expandShuffle(cells: readonly TallyCell[], seed: number): Int32Array {
  const n = totalCount(cells)
  const out = new Int32Array(n)
  let k = 0
  cells.forEach((c, i) => { for (let j = 0; j < c.count; j++) out[k++] = i })
  const rand = mulberry32(seed)
  for (let i = n - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    const t = out[i]; out[i] = out[j]; out[j] = t
  }
  return out
}

/** Index of the cell for an exact score, or -1 when the model never drew it. */
export function cellIndexOf(cells: readonly TallyCell[], home: number, away: number): number {
  return cells.findIndex((c) => c.home === home && c.away === away)
}

/**
 * How likely a cell was among all stored cells: 1 + the number of cells with a
 * strictly higher count (ties share a rank). `null` for a score never drawn.
 */
export function rankOf(cells: readonly TallyCell[], index: number): number | null {
  const cell = cells[index]
  if (!cell) return null
  let higher = 0
  for (const c of cells) if (c.count > cell.count) higher++
  return 1 + higher
}

/**
 * Pregame draws whose final score can still happen given a live score: both
 * teams' finals must be at least what they have now. This filters by the
 * score only and ignores the clock, which is why it is never a win probability.
 */
export function reachableCount(cells: readonly TallyCell[], live: { home: number; away: number }): number {
  let s = 0
  for (const c of cells) if (c.home >= live.home && c.away >= live.away) s += c.count
  return s
}

export function isReachable(cell: { home: number; away: number }, live: { home: number; away: number } | null): boolean {
  return !live || (cell.home >= live.home && cell.away >= live.away)
}

/** The `k` most-drawn exact scores, most first (ties broken by lower total, then home). */
export function topScores(cells: readonly TallyCell[], k = 3): TallyCell[] {
  return [...cells]
    .sort((a, b) => b.count - a.count || (a.home + a.away) - (b.home + b.away) || a.home - b.home)
    .slice(0, k)
}

/** Grid extents: both axes start at zero and end at the highest stored score. */
export function axisRange(cells: readonly TallyCell[]): { homeMax: number; awayMax: number } {
  let homeMax = 0, awayMax = 0
  for (const c of cells) { if (c.home > homeMax) homeMax = c.home; if (c.away > awayMax) awayMax = c.away }
  return { homeMax, awayMax }
}

export function maxCount(cells: readonly TallyCell[]): number {
  let m = 0
  for (const c of cells) if (c.count > m) m = c.count
  return m
}

/** Draws by outcome: home wins, away wins, ties. */
export function sideCounts(cells: readonly TallyCell[]): { home: number; away: number; tie: number } {
  const out = { home: 0, away: 0, tie: 0 }
  for (const c of cells) {
    if (c.home > c.away) out.home += c.count
    else if (c.away > c.home) out.away += c.count
    else out.tie += c.count
  }
  return out
}

/** Which side a cell is a win for, as the stage tints it. */
export function toneOf(cell: { home: number; away: number }, pick: Side): CellTone {
  if (cell.home === cell.away) return 'tie'
  const homeWins = cell.home > cell.away
  return (pick === 'HOME') === homeWins ? 'pick' : 'fade'
}

/** Does this draw count as a win for `side`? */
export function winsFor(cell: { home: number; away: number }, side: Side): boolean {
  return side === 'HOME' ? cell.home > cell.away : cell.away > cell.home
}

type Rgb = [number, number, number]

/** Stage palette (broadcast-dark in both themes): the empty grid and each tone at full count. */
export const STAGE_RGB: Record<CellTone | 'ground', Rgb> = {
  ground: [12, 24, 41],
  pick: [63, 216, 196],
  fade: [128, 150, 184],
  tie: [243, 194, 87],
}

/**
 * The colour of a cell from its EXACT current count. Brightness steps up on a
 * log scale, so a cell that holds 3 draws is visible next to one holding 78,
 * and the finished grid is a pure function of the stored counts.
 */
export function cellColor(count: number, max: number, tone: CellTone): string {
  if (count <= 0 || max <= 0) return `rgb(${STAGE_RGB.ground.join(',')})`
  const t = Math.min(1, Math.log1p(count) / Math.log1p(max))
  const mix = 0.18 + 0.82 * t
  const [g0, g1, g2] = STAGE_RGB.ground
  const [c0, c1, c2] = STAGE_RGB[tone]
  const lerp = (a: number, b: number) => Math.round(a + (b - a) * mix)
  return `rgb(${lerp(g0, c0)},${lerp(g1, c1)},${lerp(g2, c2)})`
}

/** Tick spacing for an axis that runs 0..max, so labels never crowd. */
export function tickStep(max: number): number {
  if (max <= 12) return 1
  if (max <= 30) return 5
  if (max <= 60) return 7
  return 10
}

/**
 * The 95% band a running share of `n` draws wobbles inside around `p`:
 * ±1.96·√(p(1−p)/n). It narrows onto the stored probability as draws arrive.
 */
export function funnelHalfWidth(p: number, n: number): number {
  if (n <= 0 || !(p >= 0 && p <= 1)) return 1
  return 1.96 * Math.sqrt((p * (1 - p)) / n)
}

/**
 * Launch schedule (seconds of replay time at 1×): the first 40 draws arrive in
 * about three seconds so a reader can follow them; after that the rate doubles
 * every 0.6 s, so all 10,000 land in about nine seconds.
 */
export const SLOW_DRAWS = 40
const SLOW_RATE = 13
const FAST_START = 12
const DOUBLING_S = 0.6

export function launchedBy(seconds: number, total: number): number {
  if (seconds <= 0) return 0
  const slowEnd = SLOW_DRAWS / SLOW_RATE
  if (seconds < slowEnd) return Math.min(total, Math.floor(seconds * SLOW_RATE))
  const s = seconds - slowEnd
  const k = FAST_START * DOUBLING_S / Math.LN2
  const fast = Math.floor(k * (Math.pow(2, s / DOUBLING_S) - 1))
  return Math.min(total, SLOW_DRAWS + fast)
}

/** Replay seconds (at 1×) until every one of `total` draws has launched. */
export function replayDuration(total: number): number {
  const slowEnd = SLOW_DRAWS / SLOW_RATE
  if (total <= SLOW_DRAWS) return total / SLOW_RATE
  const k = FAST_START * DOUBLING_S / Math.LN2
  return slowEnd + DOUBLING_S * Math.log2((total - SLOW_DRAWS) / k + 1)
}
