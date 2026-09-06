/**
 * Geometry for the Diamond Theater's outcome-trace field.
 *
 * WHAT A TRACE IS
 * ---------------
 * One trace = one completed simulated game outcome (a final score). It is NOT
 * a ball flight, a hit, or a baserunner path. Gen 1 draws final scores directly
 * and models no plate appearances, so nothing about *how* runs were scored
 * exists. The curve is a visual encoding of a final score, nothing more.
 *
 * WHERE THE POPULATION COMES FROM
 * -------------------------------
 * Traces are expanded from the authoritative joint final-score distribution
 * returned by the simulation endpoint, whose cell counts sum to the official
 * draw count. No random outcome is invented: cell (home=4, away=5) with count
 * 1038 produces exactly 1038 traces. Only the small within-cell *visual* spread
 * is synthesised, and it is deterministic and affects position only, never the
 * encoded score.
 *
 * ENCODING (also shown to the user as a legend)
 *   start      home plate
 *   direction  away-team wins bend toward left field, home-team wins toward right
 *   depth      total runs — low totals end near the infield, high totals deep
 *   curvature  run differential — bigger margins bend further
 *   colour     winning team
 *   density    exact-score frequency (identical scores stack, forming bands)
 */

export interface FieldGeometry {
  width: number
  height: number
  home: { x: number; y: number }
  second: { x: number; y: number }
  first: { x: number; y: number }
  third: { x: number; y: number }
  mound: { x: number; y: number }
  /** Radius of the outfield fence arc, measured from home plate. */
  fenceRadius: number
  /** Radius at which the infield dirt ends. */
  infieldRadius: number
  /** Half-angle of fair territory, in radians (45° each side of centre). */
  foulAngle: number
}

export const FIELD: FieldGeometry = {
  width: 1000,
  height: 760,
  home: { x: 500, y: 648 },
  second: { x: 500, y: 398 },
  first: { x: 625, y: 523 },
  third: { x: 375, y: 523 },
  mound: { x: 500, y: 523 },
  fenceRadius: 560,
  infieldRadius: 196,
  foulAngle: Math.PI / 4,
}

export type TraceFilter =
  | 'all' | 'typical' | 'home_win' | 'away_win'
  | 'high_scoring' | 'low_scoring' | 'extra_innings'

export interface Trace {
  /** Quadratic curve: start (home plate) → control → end (outfield arc). */
  cx: number
  cy: number
  x: number
  y: number
  winner: 'home' | 'away'
  homeRuns: number
  awayRuns: number
  totalRuns: number
  /** home − away. Never 0: a simulated game cannot end level. */
  differential: number
}

export interface TraceField {
  traces: Trace[]
  /** Outcomes represented — equals the official draw count. */
  totalOutcomes: number
  /** Curves actually drawn. Equal to totalOutcomes unless downsampled. */
  renderedPrimitives: number
  /** True when every outcome has its own curve. */
  exhaustive: boolean
  maxTotalRuns: number
}

/**
 * Deterministic spread within one exact-score cell.
 *
 * Traces for the same final score would otherwise overlap perfectly and no
 * density would be visible. The offset is a function of the trace's index
 * only — no randomness — and moves the curve slightly without changing the
 * score it encodes.
 */
function spread(index: number, count: number): { angle: number; radius: number } {
  if (count <= 1) return { angle: 0, radius: 0 }
  const t = index / (count - 1) - 0.5           // −0.5 … +0.5
  // A second, faster-cycling term breaks up banding without randomness.
  const ripple = Math.sin(index * 2.399963229728653) * 0.5
  return { angle: t * 0.052 + ripple * 0.012, radius: ripple * 9 }
}

/** Total runs → distance from home plate, clamped to the outfield band. */
export function depthForTotal(totalRuns: number, maxTotalRuns: number): number {
  const span = FIELD.fenceRadius - FIELD.infieldRadius
  const ceiling = Math.max(maxTotalRuns, 1)
  const ratio = Math.min(totalRuns / ceiling, 1)
  // Square-root keeps the crowded low totals from bunching on the infield edge.
  return FIELD.infieldRadius + span * Math.sqrt(ratio) * 0.94
}

/** Run differential → angle from centre field. Sign selects the field side. */
export function angleForOutcome(differential: number): number {
  const magnitude = Math.min(Math.abs(differential), 8) / 8
  // 6° for a one-run game out to ~38° for a blowout, always inside fair ground.
  const sweep = (0.105 + 0.555 * magnitude) * (FIELD.foulAngle / (Math.PI / 4)) * 0.62
  return differential > 0 ? sweep : -sweep
}

/**
 * Expands the authoritative joint distribution into one trace per outcome.
 *
 * `maxTraces` bounds the work. When the population exceeds it, traces are
 * proportionally weighted down and `exhaustive` becomes false so the caller
 * can say so out loud rather than implying every outcome is drawn.
 */
export function buildTraceField(
  probabilities: number[][],
  counts: number[][],
  maxTraces = 30_000,
): TraceField {
  const cells: Array<{ home: number; away: number; count: number }> = []
  let totalOutcomes = 0
  let maxTotalRuns = 1

  counts.forEach((row, home) => {
    row.forEach((count, away) => {
      if (count <= 0) return
      totalOutcomes += count
      cells.push({ home, away, count })
      maxTotalRuns = Math.max(maxTotalRuns, home + away)
    })
  })

  const scale = totalOutcomes > maxTraces ? maxTraces / totalOutcomes : 1
  const traces: Trace[] = []

  for (const cell of cells) {
    // A tie is impossible in this simulator; the overflow bucket can produce
    // an equal-index cell, which is not a real score and is skipped.
    if (cell.home === cell.away) continue
    const drawn = scale === 1 ? cell.count : Math.max(1, Math.round(cell.count * scale))
    const differential = cell.home - cell.away
    const total = cell.home + cell.away
    const baseAngle = angleForOutcome(differential)
    const baseDepth = depthForTotal(total, maxTotalRuns)
    const winner: Trace['winner'] = differential > 0 ? 'home' : 'away'

    for (let i = 0; i < drawn; i++) {
      const jitter = spread(i, drawn)
      const angle = baseAngle + jitter.angle
      const depth = baseDepth + jitter.radius
      const x = FIELD.home.x + Math.sin(angle) * depth
      const y = FIELD.home.y - Math.cos(angle) * depth
      // Control point at ~55% of the way out, pushed sideways so the curve
      // bends the way the differential points instead of running straight.
      const midAngle = angle * 0.42
      const midDepth = depth * 0.55
      const cx = FIELD.home.x + Math.sin(midAngle) * midDepth + Math.sin(angle) * 18
      const cy = FIELD.home.y - Math.cos(midAngle) * midDepth
      traces.push({
        cx, cy, x, y, winner,
        homeRuns: cell.home, awayRuns: cell.away,
        totalRuns: total, differential,
      })
    }
  }

  return {
    traces,
    totalOutcomes,
    renderedPrimitives: traces.length,
    exhaustive: scale === 1,
    maxTotalRuns,
  }
}

export interface FilterDefinition {
  key: TraceFilter
  label: string
  description: string
}

export const TRACE_FILTERS: FilterDefinition[] = [
  { key: 'all', label: 'All outcomes', description: 'Every simulated final score' },
  { key: 'typical', label: 'Typical', description: 'Outcomes within one run of the median margin and total' },
  { key: 'home_win', label: 'Home wins', description: 'Simulations the home team won' },
  { key: 'away_win', label: 'Away wins', description: 'Simulations the away team won' },
  { key: 'high_scoring', label: 'High scoring', description: 'Highest-scoring 10% by total runs' },
  { key: 'low_scoring', label: 'Low scoring', description: 'Lowest-scoring 10% by total runs' },
  { key: 'extra_innings', label: 'Extra innings', description: 'Decided by one run after a level regulation' },
]

/** Percentile of total runs across the population, for the scoring filters. */
function totalsQuantile(traces: Trace[], q: number): number {
  if (traces.length === 0) return 0
  const totals = traces.map((t) => t.totalRuns).sort((a, b) => a - b)
  const index = Math.min(totals.length - 1, Math.max(0, Math.floor(q * (totals.length - 1))))
  return totals[index]
}

export interface FilterThresholds {
  highCut: number
  lowCut: number
  medianTotal: number
}

export function filterThresholds(traces: Trace[]): FilterThresholds {
  return {
    highCut: totalsQuantile(traces, 0.9),
    lowCut: totalsQuantile(traces, 0.1),
    medianTotal: totalsQuantile(traces, 0.5),
  }
}

/**
 * Whether one outcome belongs to a filter.
 *
 * `extra_innings` is deliberately conservative. The simulator resolves a level
 * regulation by adding exactly one run, so a one-run margin is the only
 * signature extra innings leave in a final score. That is a superset — some
 * one-run games were decided in regulation — and the UI says so rather than
 * claiming an exact subset.
 */
export function matchesFilter(
  trace: Trace,
  filter: TraceFilter,
  thresholds: FilterThresholds,
): boolean {
  switch (filter) {
    case 'all': return true
    case 'home_win': return trace.winner === 'home'
    case 'away_win': return trace.winner === 'away'
    case 'high_scoring': return trace.totalRuns >= thresholds.highCut
    case 'low_scoring': return trace.totalRuns <= thresholds.lowCut
    case 'extra_innings': return Math.abs(trace.differential) === 1
    case 'typical':
      return Math.abs(trace.differential) <= 2
        && Math.abs(trace.totalRuns - thresholds.medianTotal) <= 2
    default: return true
  }
}

export interface SubsetStats {
  count: number
  share: number
  homeWins: number
  awayWins: number
  homeWinShare: number
  meanTotalRuns: number
  meanHomeRuns: number
  meanAwayRuns: number
  modalScore: { home: number; away: number; count: number } | null
}

/** Descriptive statistics for whatever subset is currently highlighted. */
export function subsetStats(
  traces: Trace[],
  filter: TraceFilter,
  thresholds: FilterThresholds,
): SubsetStats {
  let count = 0
  let homeWins = 0
  let totalRuns = 0
  let homeRuns = 0
  let awayRuns = 0
  const tally = new Map<string, number>()

  for (const trace of traces) {
    if (!matchesFilter(trace, filter, thresholds)) continue
    count++
    if (trace.winner === 'home') homeWins++
    totalRuns += trace.totalRuns
    homeRuns += trace.homeRuns
    awayRuns += trace.awayRuns
    const key = `${trace.homeRuns}-${trace.awayRuns}`
    tally.set(key, (tally.get(key) ?? 0) + 1)
  }

  let modalScore: SubsetStats['modalScore'] = null
  let best = 0
  for (const [key, value] of tally) {
    if (value > best) {
      best = value
      const [home, away] = key.split('-').map(Number)
      modalScore = { home, away, count: value }
    }
  }

  return {
    count,
    share: traces.length ? count / traces.length : 0,
    homeWins,
    awayWins: count - homeWins,
    homeWinShare: count ? homeWins / count : 0,
    meanTotalRuns: count ? totalRuns / count : 0,
    meanHomeRuns: count ? homeRuns / count : 0,
    meanAwayRuns: count ? awayRuns / count : 0,
    modalScore,
  }
}

/** Build-animation duration. Reduced motion renders the completed field at once. */
export function buildDurationMs(speed: number, reducedMotion: boolean): number {
  if (reducedMotion) return 0
  return Math.round(1700 / Math.max(speed, 0.25))
}
