/**
 * Which side a prediction-market book is actually quoted on.
 *
 * The board publishes two different things per provider and the table showed
 * them in one row under one heading:
 *
 *   `midpoint`            already oriented to the home team by the backend
 *   `best_bid`/`best_ask` the raw contract's own prices, on whichever side
 *                         that venue lists
 *
 * For COL @ DET those disagree in the way that looks like a bug and is not:
 * Polymarket's raw book is 0.40/0.41 because it lists the *Colorado* contract,
 * while its home-oriented midpoint is 0.595; Kalshi's raw book is 0.59/0.60
 * because it lists Detroit. Both are correct. Putting both under a "DET win"
 * heading is what was wrong.
 *
 * This resolves the raw side by evidence — comparing the raw book's own
 * midpoint against the oriented probability and its complement — and reports
 * `null` when neither matches. It never flips a price to make the two agree:
 * an unresolved orientation is withheld, because a silently complemented
 * price is indistinguishable from a correct one and twice as harmful.
 */

export type QuotedSide = 'HOME' | 'AWAY'

export interface OrientationInput {
  /** Home-oriented probability published by the backend. */
  homeProbability: number | null | undefined
  bestBid: number | null | undefined
  bestAsk: number | null | undefined
}

export interface Orientation {
  side: QuotedSide | null
  /** Midpoint of the raw book, or null when it has no two-sided quote. */
  rawMidpoint: number | null
  /** Why the side could not be established, when it could not. */
  reason: string | null
}

/**
 * How close the raw midpoint must sit to the oriented probability (or its
 * complement) to call the side. Wide enough for ordinary spread and
 * observation-time drift between the two figures; far too narrow to confuse
 * one side with the other on any non-degenerate market.
 */
const TOLERANCE = 0.06

export function resolveQuotedSide(input: OrientationInput): Orientation {
  const { homeProbability, bestBid, bestAsk } = input
  const bid = numberOrNull(bestBid)
  const ask = numberOrNull(bestAsk)
  const home = numberOrNull(homeProbability)

  if (bid == null && ask == null) {
    return { side: null, rawMidpoint: null, reason: 'No two-sided quote is recorded.' }
  }
  const rawMidpoint = bid != null && ask != null ? (bid + ask) / 2 : (bid ?? ask) as number
  if (home == null) {
    return {
      side: null, rawMidpoint,
      reason: 'No home-oriented probability is published to compare the book against.',
    }
  }
  const toHome = Math.abs(rawMidpoint - home)
  const toAway = Math.abs(rawMidpoint - (1 - home))

  // A market near 50/50 cannot be told apart by this test, and guessing there
  // would be a coin flip presented as a fact.
  if (Math.abs(toHome - toAway) < 0.01) {
    return {
      side: null, rawMidpoint,
      reason: 'The quote sits too near even money to identify which side it lists.',
    }
  }
  if (toHome <= TOLERANCE && toHome < toAway) return { side: 'HOME', rawMidpoint, reason: null }
  if (toAway <= TOLERANCE && toAway < toHome) return { side: 'AWAY', rawMidpoint, reason: null }
  return {
    side: null, rawMidpoint,
    reason: 'The recorded book does not match either side of the published probability.',
  }
}

function numberOrNull(value: number | null | undefined): number | null {
  return value == null || !Number.isFinite(value) ? null : value
}

/**
 * How a quote should be described, separating three facts that were collapsed
 * into one green "FRESH" badge: what the game is doing, what kind of
 * observation this is, and how long ago it was recorded.
 *
 * A pregame quote recorded before first pitch does not become "live" because
 * the page refreshed, and a finished game does not make its last pregame quote
 * stale evidence of something else — it makes it a record.
 */
export function quoteDescription(input: {
  observationKind: string | null | undefined
  inPlay: boolean | null | undefined
  ageLabel: string | null | undefined
}): { kind: string; recorded: string } {
  const kind = (input.observationKind ?? '').toUpperCase()
  const label = kind === 'FINAL_PREGAME'
    ? 'Final pregame quote'
    : kind === 'PREGAME'
      ? 'Pregame quote'
      : input.inPlay
        ? 'In-play observation'
        : kind
          ? kind.toLowerCase().replace(/_/g, ' ')
          : 'Observation'
  return {
    kind: label,
    recorded: input.ageLabel ? `last recorded ${input.ageLabel}` : 'recording time not available',
  }
}
