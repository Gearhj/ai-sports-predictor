/**
 * Model vs market, at the moment AISP decided — James's model-warning rule.
 *
 *   market  = the SAME book's two prices the decision priced, vig removed:
 *             imp(sel) / (imp(sel) + imp(opp))
 *   model   = the decision's model probability for the selected side; for the
 *             NFL, which has a tie, the two-way equivalent pWin / (1 − pTie)
 *             (the NON_TIE_DERIVED basis /research/wager-results uses)
 *   gap     = (model − market) × 100 points
 *
 * A gap of more than 10 points is a MODEL WARNING: a disagreement that large
 * is more often model error than edge. Nothing here calls it an edge or a
 * value. NFL no-vig is never stored, so it is always derived from the pair; for
 * the other sports the derivation equals the stored no_vig_probability.
 */
import type { DecisionItem } from '../../services/commandCenter'
import { mulberry32 } from './stormTally'

export const GAP_WARNING_PTS = 10

/** Implied probability of an American price, vig included. */
export function impliedProb(american: number | null | undefined): number | null {
  if (american == null || !Number.isFinite(american) || american === 0) return null
  if (american > 0) return 100 / (american + 100)
  return -american / (-american + 100)
}

/** The price a bet needs to win at to break even (the same as its implied probability). */
export const breakEven = impliedProb

/** Same-book no-vig probability of the selected side, from the decision's price pair. */
export function sameBookNoVig(selected: number | null | undefined, opposite: number | null | undefined): number | null {
  const a = impliedProb(selected)
  const b = impliedProb(opposite)
  if (a == null || b == null || a + b <= 0) return null
  return a / (a + b)
}

export type ComparisonBasis = 'NON_TIE_DERIVED' | 'RAW_NO_TIE_OUTCOME' | 'TIE_PROBABILITY_NOT_RECORDED'

/** The model's two-way probability for the selected side. */
export function comparisonProbability(
  sport: string, modelProbability: number | null | undefined, tieProbability: number | null | undefined,
): { p: number; basis: ComparisonBasis } | null {
  if (modelProbability == null || !Number.isFinite(modelProbability)) return null
  if (sport === 'NFL') {
    if (tieProbability == null || !Number.isFinite(tieProbability)) return { p: modelProbability, basis: 'TIE_PROBABILITY_NOT_RECORDED' }
    if (tieProbability >= 1) return null
    return { p: modelProbability / (1 - tieProbability), basis: 'NON_TIE_DERIVED' }
  }
  return { p: modelProbability, basis: 'RAW_NO_TIE_OUTCOME' }
}

export function gapPts(comparison: number, noVig: number): number {
  return (comparison - noVig) * 100
}

/** Rounded to the tenth shown on screen, so the label and the rule never disagree. */
export function roundTenth(value: number): number {
  return Math.round(value * 10) / 10
}

/** Strictly greater than 10 points (as displayed) warns; exactly 10.0 does not. */
export function isModelWarning(gap: number | null | undefined): boolean {
  return gap != null && Number.isFinite(gap) && roundTenth(gap) > GAP_WARNING_PTS
}

export function gapText(gap: number | null | undefined): string {
  if (gap == null || !Number.isFinite(gap)) return '—'
  const r = roundTenth(gap)
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toFixed(1)} pts`
}

export interface GapReading {
  sport: string
  /** Same-book no-vig probability of the selected side at decision time. */
  noVig: number
  /** Model probability compared (two-way for the NFL). */
  model: number
  /** The raw stored model probability (three-way for the NFL). */
  rawModel: number
  basis: ComparisonBasis
  gap: number
  warning: boolean
  sportsbook: string | null
  /** The decision's price pair; null where the source does not carry it (a season star). */
  selectedAmerican: number | null
  oppositeAmerican: number | null
  observedAt: string | null
  /** False when the decision's older leg was already stale when it decided; null when not recorded. */
  freshAtDecision: boolean | null
}

/**
 * For display on a single game (the stage), a gap of more than 10 points
 * either way is the model warning: a model far BELOW the market on its own
 * selected side is as suspect as one far above it. The funded-bet tallies keep
 * the one-sided rule (gap > +10), which is how James wrote it.
 */
export function isDisplayWarning(gap: number | null | undefined): boolean {
  return gap != null && Number.isFinite(gap) && isModelWarning(Math.abs(gap))
}

/** What the model side of a gap is, in words, chosen from the basis it was computed on. */
export function basisCaption(reading: Pick<GapReading, 'sport' | 'basis'> | null | undefined): string {
  if (!reading || reading.sport !== 'NFL') return ''
  if (reading.basis === 'NON_TIE_DERIVED') return 'NFL model is tie-adjusted (pWin ÷ (1 − pTie)).'
  return 'NFL tie probability not recorded for this decision, so the raw model is shown.'
}

/** The short tag after an NFL model number: "(two-way)" only when it really is. */
export function basisTag(reading: Pick<GapReading, 'sport' | 'basis'> | null | undefined): string {
  if (!reading || reading.sport !== 'NFL') return ''
  return reading.basis === 'NON_TIE_DERIVED' ? ' (two-way)' : ' (raw; tie not recorded)'
}

/**
 * The gap for one decision, or null when it has no two-sided same-book pair
 * (then it is listed as unplaced and never estimated).
 */
export function decisionGap(item: Pick<DecisionItem, 'sport' | 'model_probability' | 'market_integrity' | 'edge_metrics' | 'sportsbook'>): GapReading | null {
  const mi = item.market_integrity
  if (!mi || mi.selected_american == null || mi.opposite_american == null) return null
  const noVig = sameBookNoVig(mi.selected_american, mi.opposite_american)
  const cmp = comparisonProbability(item.sport, item.model_probability, item.edge_metrics?.inputs?.tie_probability ?? null)
  if (noVig == null || !cmp || item.model_probability == null) return null
  const gap = gapPts(cmp.p, noVig)
  return {
    sport: item.sport, noVig, model: cmp.p, rawModel: item.model_probability, basis: cmp.basis, gap,
    warning: isModelWarning(gap), sportsbook: mi.sportsbook ?? item.sportsbook ?? null,
    selectedAmerican: mi.selected_american, oppositeAmerican: mi.opposite_american,
    observedAt: mi.older_leg_observed_at ?? null,
    freshAtDecision: typeof mi.fresh_at_decision === 'boolean' ? mi.fresh_at_decision : null,
  }
}

// ------------------------------------------------------------------ tallies --

export type BetResult = 'WIN' | 'LOSS' | 'PUSH' | 'OPEN' | 'VOID'

export interface GapBet {
  key: string
  gap: number | null
  result: BetResult
  stake: number | null
  profit: number | null
}

export interface ZoneTally {
  /** Every bet in the zone, open included. */
  n: number
  settled: number
  wins: number
  losses: number
  pushes: number
  open: number
  stake: number
  profit: number
  /** Σ profit ÷ Σ stake over settled bets; null with nothing settled. */
  roi: number | null
}

export function normalizeResult(status: string | null | undefined, result: string | null | undefined): BetResult {
  const r = (result ?? '').toUpperCase()
  if (r === 'WIN' || r === 'LOSS' || r === 'PUSH') return r
  if (r === 'VOID' || (status ?? '').toUpperCase() === 'VOID') return 'VOID'
  return 'OPEN'
}

function emptyTally(): ZoneTally {
  return { n: 0, settled: 0, wins: 0, losses: 0, pushes: 0, open: 0, stake: 0, profit: 0, roi: null }
}

function add(t: ZoneTally, bet: GapBet) {
  t.n += 1
  if (bet.result === 'OPEN') { t.open += 1; return }
  if (bet.result === 'VOID') return
  t.settled += 1
  if (bet.result === 'WIN') t.wins += 1
  else if (bet.result === 'LOSS') t.losses += 1
  else t.pushes += 1
  t.stake += bet.stake ?? 0
  t.profit += bet.profit ?? 0
}

/**
 * The two zones of the model-warning rule: gap ≤ threshold (the agreement
 * corridor, which includes negative gaps) and gap > threshold (the warning
 * zone). A bet without a price pair is counted as unplaced, never estimated.
 */
export function zoneTally(bets: readonly GapBet[], threshold = GAP_WARNING_PTS): { corridor: ZoneTally; warning: ZoneTally; unplaced: number } {
  const corridor = emptyTally()
  const warning = emptyTally()
  let unplaced = 0
  for (const bet of bets) {
    if (bet.gap == null || !Number.isFinite(bet.gap)) { unplaced += 1; continue }
    add(roundTenth(bet.gap) > threshold ? warning : corridor, bet)
  }
  for (const t of [corridor, warning]) {
    t.profit = Math.round(t.profit * 100) / 100
    t.stake = Math.round(t.stake * 100) / 100
    t.roi = t.stake > 0 ? t.profit / t.stake : null
  }
  return { corridor, warning, unplaced }
}

/**
 * A seeded percentile-bootstrap 95% range for ROI (Σ profit ÷ Σ stake) over
 * settled bets. Deterministic for a given seed, so the range does not jump on
 * every render. Null with fewer than two settled bets.
 */
export function seededBootstrapRoi(rows: readonly { stake: number; profit: number }[], iterations = 2000, seed = 20261017): [number, number] | null {
  const n = rows.length
  if (n < 2) return null
  const rand = mulberry32(seed)
  const rois = new Float64Array(iterations)
  for (let it = 0; it < iterations; it++) {
    let stake = 0, profit = 0
    for (let i = 0; i < n; i++) {
      const row = rows[Math.floor(rand() * n)]
      stake += row.stake; profit += row.profit
    }
    rois[it] = stake > 0 ? profit / stake : 0
  }
  rois.sort()
  const at = (q: number) => rois[Math.min(iterations - 1, Math.max(0, Math.floor(q * (iterations - 1))))]
  return [at(0.025), at(0.975)]
}

export function signedPct(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const v = value * 100
  const r = Number(v.toFixed(digits))
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toFixed(digits)}%`
}
