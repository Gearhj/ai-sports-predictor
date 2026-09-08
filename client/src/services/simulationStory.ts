/**
 * Pure helpers for the Monte Carlo Diamond Simulator.
 *
 * Everything here is derived from supported evidence only. Two rules are
 * enforced in code rather than by convention:
 *
 *  - a probability comparison row exists only when its source actually
 *    observed a value; a missing source is "not available", never 0%
 *  - the one-sentence game story states only what the simulation and the
 *    decision authority support, and never uses persuasive language
 */
import type { MarketPulseRow, PredictionProviderProbability } from '../types/domain'
import type { GameSimulationData, RepresentativeOutcome } from '../types/simulation'

/** Language that would imply certainty or advice. Never emitted. */
export const FORBIDDEN_STORY_WORDS = [
  'lock', 'guaranteed', 'safe bet', 'sure thing', 'can’t lose', 'free money',
]

export interface ProbabilityRow {
  key: 'asp' | 'sportsbook' | 'kalshi' | 'polymarket'
  label: string
  /** Home-team win probability as a percentage, or null when unobserved. */
  probability: number | null
  kind: 'model' | 'observed_market'
  /** Raw implied vs de-vigged, where the distinction exists. */
  basis: string
  observedAgeSeconds: number | null
  quality: string | null
  unavailableReason: string | null
}

function provider(row: MarketPulseRow | null, name: string): PredictionProviderProbability | null {
  if (!row?.providers) return null
  return row.providers.find((p) => p.provider.toUpperCase() === name) ?? null
}

/**
 * Builds the one-scale probability comparison.
 *
 * Kalshi and Polymarket are labelled observed markets, never model
 * predictions. A source with no observation yields an explicit reason.
 */
export function probabilityComparison(
  simulation: GameSimulationData | null,
  row: MarketPulseRow | null,
): ProbabilityRow[] {
  // A market probability of exactly 0 or 1 is not a possible real quote: the
  // upstream read model emits 0.0 when nothing was observed. Treating that as
  // an observation would print "0.0%" and manufacture a ~39 point edge against
  // a market that never quoted. Anything outside (0, 1) is therefore unobserved.
  const pct = (value: number | null | undefined): number | null => {
    if (value == null || !Number.isFinite(value)) return null
    const scaled = value <= 1 ? value * 100 : value
    if (scaled <= 0 || scaled >= 100) return null
    return scaled
  }

  const aspProbability = simulation?.official_win_probability
    ? simulation.official_win_probability.home * 100
    : pct(row?.aisp_home_probability ?? null)

  const rows: ProbabilityRow[] = [{
    key: 'asp',
    label: 'ASP model',
    probability: aspProbability,
    kind: 'model',
    basis: 'Official forecast probability (governed blend of model components and simulation)',
    observedAgeSeconds: null,
    quality: simulation?.identity.model_status ?? null,
    unavailableReason: aspProbability == null ? 'No official forecast is available for this game.' : null,
  }]

  const book = pct(row?.sportsbook_home_probability ?? null)
  rows.push({
    key: 'sportsbook',
    label: 'Sportsbook (no-vig)',
    probability: book,
    kind: 'observed_market',
    basis: 'De-vigged from the observed two-sided sportsbook quote',
    observedAgeSeconds: null,
    quality: null,
    unavailableReason: book == null
      ? 'No complete two-sided sportsbook quote is currently persisted for this game.'
      : null,
  })

  for (const [key, name, label] of [
    ['kalshi', 'KALSHI', 'Kalshi (observed market)'],
    ['polymarket', 'POLYMARKET', 'Polymarket (observed market)'],
  ] as const) {
    const value = pct(key === 'kalshi'
      ? row?.kalshi_home_probability ?? null
      : row?.polymarket_home_probability ?? null)
    const detail = provider(row, name)
    rows.push({
      key,
      label,
      probability: value,
      kind: 'observed_market',
      basis: 'Observed contract midpoint. Not a model prediction and not decision authority.',
      observedAgeSeconds: detail?.quote_age_seconds ?? null,
      quality: detail?.quality_tier ?? null,
      unavailableReason: value == null
        ? `No mapped ${label.split(' ')[0]} contract observation is persisted for this game.`
        : null,
    })
  }
  return rows
}

/**
 * A model-versus-market difference, shown only when BOTH probabilities exist.
 * Returns null otherwise so a missing market can never look like a zero edge.
 */
export function modelMarketDifference(rows: ProbabilityRow[], against: ProbabilityRow['key']): number | null {
  const asp = rows.find((r) => r.key === 'asp')?.probability
  const other = rows.find((r) => r.key === against)?.probability
  if (asp == null || other == null) return null
  return asp - other
}

export interface StoryInput {
  simulation: GameSimulationData | null
  homeTeam: string
  awayTeam: string
  decisionStatus: string | null
  decisionReason: string | null
}

/**
 * One plain sentence describing what the simulation supports.
 *
 * Deliberately conservative: it reports the simulated win rate, the central
 * scoring picture, and the governed decision. It makes no recommendation and
 * uses none of {@link FORBIDDEN_STORY_WORDS}.
 */
export function gameStory({
  simulation, homeTeam, awayTeam, decisionStatus, decisionReason,
}: StoryInput): string {
  if (!simulation?.simulation_available || !simulation.means) {
    return 'A simulated outcome distribution is not available for this game yet.'
  }
  const homeP = simulation.official_win_probability.home * 100
  const favoured = homeP >= 50 ? homeTeam : awayTeam
  const favouredP = homeP >= 50 ? homeP : 100 - homeP
  const total = simulation.means.total_runs
  const margin = Math.abs(simulation.means.run_difference)

  const closeness = margin < 0.5 ? 'a close' : margin < 1.5 ? 'a moderately one-sided' : 'a one-sided'
  const scoring = total < 8 ? 'lower-scoring' : total > 9.5 ? 'higher-scoring' : 'average-scoring'

  const head = `ASP sees ${closeness}, ${scoring} game in which ${favoured} wins `
    + `${favouredP.toFixed(0)}% of simulations, with about ${total.toFixed(1)} total runs`

  if (!decisionStatus) return `${head}.`
  const tail = decisionReason ? ` ${decisionReason}` : ''
  return `${head}. Official decision: ${decisionStatus}.${tail}`
}

/** Human label for a representative outcome selector. */
export const OUTCOME_LABELS: Record<RepresentativeOutcome['key'], string> = {
  typical: 'Typical outcome',
  home_win: 'Home team wins',
  away_win: 'Away team wins',
  high_scoring: 'High-scoring',
  low_scoring: 'Low-scoring',
  extra_innings: 'Extra innings',
}

/**
 * Central score range: the smallest set of exact scores covering `coverage`
 * of the joint distribution, reported as a readable list.
 */
export function centralScores(
  simulation: GameSimulationData | null,
  coverage = 0.25,
): Array<{ home: number; away: number; probability: number }> {
  const joint = simulation?.joint_score_distribution
  if (!joint) return []
  const cells: Array<{ home: number; away: number; probability: number }> = []
  joint.probabilities.forEach((row, home) => {
    row.forEach((probability, away) => {
      if (probability > 0 && home < joint.home_labels.length - 1 && away < joint.away_labels.length - 1) {
        cells.push({ home, away, probability })
      }
    })
  })
  cells.sort((a, b) => b.probability - a.probability)
  const picked: typeof cells = []
  let sum = 0
  for (const cell of cells) {
    picked.push(cell)
    sum += cell.probability
    if (sum >= coverage) break
  }
  return picked
}

/** Percent formatter that renders unavailable rather than a misleading zero. */
export function percentOrUnavailable(value: number | null | undefined, digits = 1): string {
  return value == null ? 'Not available' : `${value.toFixed(digits)}%`
}
