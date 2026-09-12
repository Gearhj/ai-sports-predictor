/**
 * Which side a prediction-market book is quoted on — read, never inferred.
 *
 * WHAT THIS REPLACED, AND WHY
 * ---------------------------
 * The first version of this module compared the raw bid/ask midpoint against
 * the home-oriented probability and picked whichever it sat closer to. That
 * worked on the data in front of it and was still wrong, for a reason no test
 * of that data would have caught: **a contract's side is a property of the
 * contract, not of its price.** A Detroit contract that drifts from 0.59 to
 * 0.48 is still a Detroit contract, and a price-based test would have silently
 * relabelled it Colorado as it crossed 50%. It also had to give up entirely
 * near even money, which is exactly where a mislabelling costs most.
 *
 * Both venues in fact publish the side explicitly, and AISP already stores it:
 *
 *   Kalshi     market  KXMLBGAME-26SEP121310COLDET-DET
 *              outcome KXMLBGAME-26SEP121310COLDET-DET:YES   "Detroit wins"
 *   Polymarket market  0x923c5ee0…  outcome 527481423…       "Detroit Tigers"
 *
 * `PredictionMarketContract` records the venue's own outcome label, market and
 * outcome identifiers and question; `PredictionMarketSnapshot` records the
 * canonical selection the normalizer mapped them to. The read service now
 * carries all of that through the DTO, so this module reads a decision that
 * was already made from identity rather than re-deriving one from numbers.
 *
 * Prices are still compared — but only as a *consistency check* that can flag
 * a contradiction, never as the source of the side.
 */

export type QuotedSide = 'HOME' | 'AWAY'

/** The contract-identity fields the board publishes for one provider quote. */
export interface QuotedContract {
  quoted_selection?: string | null
  quoted_outcome_label?: string | null
  quoted_market_id?: string | null
  quoted_outcome_id?: string | null
  quoted_market_question?: string | null
  quoted_settlement_meaning?: string | null
  quoted_side_source?: 'CONTRACT_METADATA' | 'UNVERIFIED' | null
  best_bid?: number | null
  best_ask?: number | null
}

export interface Orientation {
  /** Canonical side, or null when identity could not establish one. */
  side: QuotedSide | null
  /** The venue's own label for the contract, shown when the side is unknown. */
  nativeLabel: string | null
  /** Venue market / outcome identifiers, for technical detail. */
  marketId: string | null
  outcomeId: string | null
  /** The venue's question and what a YES settlement means. */
  question: string | null
  settlementMeaning: string | null
  /** True when identity established the side. */
  verified: boolean
  /** Why the side is unverified, or why a check disagreed. */
  reason: string | null
  /** A stored price/identity contradiction worth surfacing, if any. */
  consistencyWarning: string | null
}

/**
 * Resolve one provider quote's contract.
 *
 * `homeProbability` is used only for the consistency check. It never decides
 * the side, so a price crossing 50% cannot change the answer.
 */
export function resolveQuotedContract(
  quote: QuotedContract,
  homeProbability?: number | null,
): Orientation {
  const selection = (quote.quoted_selection ?? '').toUpperCase()
  const side: QuotedSide | null =
    selection === 'HOME' ? 'HOME' : selection === 'AWAY' ? 'AWAY' : null
  const verified = quote.quoted_side_source === 'CONTRACT_METADATA' && side !== null

  const base: Orientation = {
    side: verified ? side : null,
    nativeLabel: quote.quoted_outcome_label ?? null,
    marketId: quote.quoted_market_id ?? null,
    outcomeId: quote.quoted_outcome_id ?? null,
    question: quote.quoted_market_question ?? null,
    settlementMeaning: quote.quoted_settlement_meaning ?? null,
    verified,
    reason: verified
      ? null
      : quote.quoted_selection || quote.quoted_outcome_label
        ? 'The venue published this contract without a canonical side mapping.'
        : 'No contract identity is recorded for this quote.',
    consistencyWarning: null,
  }
  if (!verified) return base
  return { ...base, consistencyWarning: consistencyWarning(quote, side, homeProbability) }
}

/**
 * Does the stored price agree with the stored identity?
 *
 * A disagreement is reported, not acted on: the identity stands, and the
 * warning says the two sources differ so someone can look. Silently trusting
 * the price here would reintroduce exactly the bug this module removed.
 */
function consistencyWarning(
  quote: QuotedContract,
  side: QuotedSide | null,
  homeProbability?: number | null,
): string | null {
  const bid = numberOrNull(quote.best_bid)
  const ask = numberOrNull(quote.best_ask)
  const home = numberOrNull(homeProbability)
  if (side === null || home === null || (bid === null && ask === null)) return null

  const raw = bid !== null && ask !== null ? (bid + ask) / 2 : ((bid ?? ask) as number)
  const expected = side === 'HOME' ? home : 1 - home
  // Wide: spreads, observation-time drift between the two figures and rounding
  // all move this legitimately. Only a gap far larger than any of those is
  // worth a reader's attention.
  if (Math.abs(raw - expected) <= 0.15) return null
  return (
    `The stored price for this contract (${(raw * 100).toFixed(1)}%) is far from the `
    + `published ${side === 'HOME' ? 'home' : 'away'} probability `
    + `(${(expected * 100).toFixed(1)}%). The side shown comes from the contract's own `
    + 'identity; the prices are reported as recorded.'
  )
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
