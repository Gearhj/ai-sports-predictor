/**
 * Plain-language price arithmetic for the Bet Board, My Card and the replay card.
 *
 * PAYOUT is the sportsbook's contract: what a winning $1 returns at the stored
 * price. It is not a prediction and needs no model.
 *
 * EXPECTATION is AISP's: expected profit per $1 from the verified population,
 * push-aware, computed server-side as pWin × (decimal − 1) − pLoss (a push
 * returns the stake and contributes nothing). It is shown only when the server
 * supplied it for this exact quote; it is never rebuilt here from the no-vig
 * market, which would present the market's view as AISP's.
 */
import type { AssessedSide } from './probability'

const cents = (value: number) => Math.round(value * 100) / 100
const money = (value: number) => `$${cents(value).toFixed(2)}`

export function decimalFromAmerican(american: number | null | undefined): number | null {
  if (american == null || !Number.isFinite(american) || (american > -100 && american < 100)) return null
  return american > 0 ? 1 + american / 100 : 1 + 100 / -american
}

/** What $1 returns if it wins, and the profit inside that. */
export function dollarPayout(decimal: number | null | undefined): { total: number; profit: number } | null {
  if (decimal == null || !Number.isFinite(decimal) || decimal <= 1) return null
  return { total: cents(decimal), profit: cents(decimal - 1) }
}

export function payoutLine(decimal: number | null | undefined): string | null {
  const p = dollarPayout(decimal)
  return p ? `Bet $1 → ${money(p.total)} total if it wins` : null
}

export function profitLine(decimal: number | null | undefined): string | null {
  const p = dollarPayout(decimal)
  return p ? `${money(p.profit)} profit` : null
}

/** "AISP expected profit: +$0.05 per $1" or "AISP expected loss: −$0.03 per $1". */
export function expectationLine(expected: number | null | undefined): string | null {
  if (expected == null || !Number.isFinite(expected)) return null
  const rounded = cents(expected)
  return rounded >= 0
    ? `AISP expected profit: +${money(rounded)} per $1`
    : `AISP expected loss: −${money(Math.abs(rounded))} per $1`
}

/** Why no AISP expectation is shown for a quote, in plain words. */
export function expectationUnavailable(side: Pick<AssessedSide, 'model' | 'edge_reason'> | null | undefined): string {
  switch (side?.edge_reason) {
    case 'NO_VERIFIED_POPULATION':
    case undefined:
    case null:
      return side?.model ? 'AISP expectation unavailable for this price' : 'AISP expectation pending — forecast not available'
    case 'QUOTE_NOT_CURRENT':
      return 'AISP expectation shown only for a current price'
    case 'PROVIDER_NOT_PERMITTED':
      return 'AISP expectation not assessed for this provider'
    case 'LINE_MISSING':
      return 'AISP expectation unavailable — line not recorded'
    default:
      return 'AISP expectation unavailable for this price'
  }
}

/** Straight-pick return at a chosen virtual stake. */
export function stakeReturn(stake: number, decimal: number): { total: number; profit: number } {
  const total = cents(stake * decimal)
  return { total, profit: cents(total - stake) }
}
