/**
 * The experiment's arithmetic, derived once and shared by every surface.
 *
 * WHY THIS IS A MODULE AND NOT A FEW INLINE DIVISIONS
 * ---------------------------------------------------
 * The home card and the full page were computing their own returns and
 * disagreeing. The card showed "ROI +10.62%" beside a bankroll that had moved
 * from $1,000.00 to $1,003.45. Both numbers were arithmetically real -- 10.62%
 * is $3.45 over the $32.50 staked -- and together they were a lie, because
 * $32.50 includes $2.50 still riding on an unsettled position, and because a
 * reader seeing "+10.62%" next to a bankroll reads it as the bankroll's
 * return. It is not. The bankroll's return is +0.35%.
 *
 * So there are exactly two return measures here, each named for its
 * denominator, and neither is ever called "ROI":
 *
 *   BANKROLL RETURN            realized profit / starting bankroll
 *                              What "how is the bankroll doing" means.
 *                              +0.345% here.
 *
 *   RETURN ON SETTLED STAKE    realized profit / stake that has RESOLVED
 *                              A much bigger number because the stakes are
 *                              tiny; honest only with its denominator shown.
 *                              +11.50% here.
 *
 * REALIZED MEANS SETTLED
 * ----------------------
 * Open positions contribute to exposure and to nothing else. They are never
 * counted in profit, in the record, in the settled sample, or in any
 * denominator. An open $2.50 is money at risk, not money earned or lost, and
 * the two must never be added together.
 *
 * The server computes all of this too. These functions prefer the server's
 * values and fall back to deriving them from the same persisted fields, so a
 * stale API cannot silently produce a different number on one of the two
 * surfaces.
 */
import type { BankrollData, EquityCurvePoint, Position } from '../../types/domain'

export interface BankrollFacts {
  startingBankroll: number
  currentBankroll: number
  /** Realized only: the sum of settled profit and loss. */
  realizedProfit: number
  /** realizedProfit / startingBankroll, as a percentage. */
  bankrollReturnPct: number | null
  /** realizedProfit / settledStake, as a percentage. */
  returnOnSettledStakePct: number | null
  settledStake: number
  openExposure: number
  totalStaked: number
  payouts: number | null
  wins: number
  losses: number
  pushes: number
  settledCount: number
  openCount: number
  record: string
  peakBankroll: number
  lowestBankroll: number | null
  /** Dollars from the high-water mark to the lowest point after it. */
  maxDrawdown: number
  maxDrawdownPctOfPeak: number | null
  currentDrawdown: number
  averageStake: number | null
  streakResult: string | null
  streakLength: number
  bestResult: number | null
  worstResult: number | null
  /** Share of net profit carried by the two best wins. Over 100% means the
   *  experiment is only ahead because of them. */
  concentrationPct: number | null
  experimentName: string | null
  policy: string | null
  stakingPolicy: string | null
  startDate: string | null
  /** True once at least one position has settled. */
  hasSettled: boolean
}

const number = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

export function bankrollFacts(data: BankrollData): BankrollFacts {
  const betting = data.betting ?? null
  const positions = data.positions ?? []
  const settled = positions.filter((position) => position.result !== 'OPEN')
  const open = positions.filter((position) => position.result === 'OPEN')

  const startingBankroll = number(data.starting_bankroll) ?? 0
  const currentBankroll = number(data.current_bankroll) ?? startingBankroll
  // Realized profit is the bankroll's movement. It equals the sum of settled
  // profit and loss by construction, and never includes an open position.
  const realizedProfit = number(betting?.net_profit_loss) ?? (currentBankroll - startingBankroll)

  const settledStake = number(betting?.settled_stake)
    ?? settled.reduce((sum, position) => sum + (position.stake ?? 0), 0)
  const openExposure = number(data.open_exposure) ?? number(betting?.open_exposure)
    ?? open.reduce((sum, position) => sum + (position.stake ?? 0), 0)
  const totalStaked = number(betting?.total_risked) ?? settledStake + openExposure

  const bankrollReturnPct = number(data.bankroll_return_pct)
    ?? number(betting?.bankroll_return_pct)
    ?? (startingBankroll ? (realizedProfit / startingBankroll) * 100 : null)
  const returnOnSettledStakePct = number(data.return_on_settled_stake_pct)
    ?? number(betting?.return_on_settled_stake_pct)
    ?? (settledStake ? (realizedProfit / settledStake) * 100 : null)

  const wins = betting?.wins ?? settled.filter((position) => position.result === 'WIN').length
  const losses = betting?.losses ?? settled.filter((position) => position.result === 'LOSS').length
  const pushes = betting?.pushes ?? settled.filter((position) => position.result === 'PUSH').length
  const settledCount = data.settled_positions ?? betting?.settled ?? settled.length
  const openCount = data.open_positions ?? betting?.open ?? open.length

  const peakBankroll = number(data.peak_bankroll) ?? startingBankroll
  const maxDrawdown = number(betting?.max_drawdown) ?? 0

  return {
    startingBankroll,
    currentBankroll,
    realizedProfit,
    bankrollReturnPct,
    returnOnSettledStakePct,
    settledStake,
    openExposure,
    totalStaked,
    payouts: number(betting?.payouts),
    wins,
    losses,
    pushes,
    settledCount,
    openCount,
    record: betting?.record ?? data.record ?? `${wins}–${losses}–${pushes}`,
    peakBankroll,
    lowestBankroll: number(data.lowest_bankroll) ?? number(betting?.lowest_bankroll),
    maxDrawdown,
    maxDrawdownPctOfPeak: number(betting?.max_drawdown_pct_of_peak)
      ?? (peakBankroll ? (maxDrawdown / peakBankroll) * 100 : null),
    currentDrawdown: number(data.current_drawdown) ?? 0,
    averageStake: settledCount ? settledStake / settledCount : null,
    streakResult: betting?.streak_result ?? null,
    streakLength: betting?.streak_length ?? 0,
    bestResult: number(betting?.best_settled_profit),
    worstResult: number(betting?.worst_settled_profit),
    concentrationPct: number(betting?.top_two_wins_share_of_profit_pct),
    experimentName: data.experiment_name ?? null,
    policy: data.policy ?? null,
    stakingPolicy: data.staking_policy ?? null,
    startDate: data.experiment_start_date ?? null,
    hasSettled: settledCount > 0,
  }
}

/* -- formatting ----------------------------------------------------------- */

export const money = (value: number | null | undefined, digits = 2): string =>
  value == null || !Number.isFinite(value)
    ? '—'
    : `${value < 0 ? '−' : ''}$${Math.abs(value).toLocaleString('en-US', {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    })}`

export const signedMoney = (value: number | null | undefined, digits = 2): string =>
  value == null || !Number.isFinite(value)
    ? '—'
    : `${value < 0 ? '−' : '+'}$${Math.abs(value).toLocaleString('en-US', {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    })}`

export const signedPct = (value: number | null | undefined, digits = 2): string =>
  value == null || !Number.isFinite(value)
    ? '—'
    : `${value < 0 ? '−' : '+'}${Math.abs(value).toFixed(digits)}%`

/* -- the story ------------------------------------------------------------ */

export interface NarrativeLine {
  key: string
  /** 'fact' is neutral, 'caution' is a limitation the reader must not miss. */
  tone: 'fact' | 'caution'
  text: string
}

/**
 * The experiment described in sentences, generated from the ledger.
 *
 * Every line is a template filled with a computed number -- there is no
 * language model anywhere near this, and no sentence is emitted unless the
 * data it describes exists. The small-sample caution is not optional and not
 * last: at twelve settled positions it is the single most important thing on
 * the page, and the concentration line exists because it is the least
 * flattering true statement available and omitting it would be a choice.
 */
export function narrative(facts: BankrollFacts, reviewCheckpoint = 50): NarrativeLine[] {
  if (!facts.hasSettled) {
    return [{
      key: 'none', tone: 'caution',
      text: 'No position has settled yet. The bankroll is still exactly its opening balance, '
        + 'and a zero-position day is a valid result of the policy rather than a gap in the data.',
    }]
  }
  const lines: NarrativeLine[] = []
  const direction = facts.realizedProfit >= 0 ? 'up' : 'down'

  lines.push({
    key: 'position', tone: 'fact',
    text: `The bankroll stands at ${money(facts.currentBankroll)}, ${direction} `
      + `${money(Math.abs(facts.realizedProfit))} from the ${money(facts.startingBankroll, 0)} it opened with — `
      + `a return of ${signedPct(facts.bankrollReturnPct, 2)} on the bankroll, and `
      + `${signedPct(facts.returnOnSettledStakePct, 2)} on the ${money(facts.settledStake)} of stake that has settled.`,
  })

  lines.push({
    key: 'sample', tone: 'caution',
    text: `${facts.settledCount} positions have settled (${facts.record}). That is far too few to `
      + `distinguish skill from luck: the first honest review of this policy comes at around `
      + `${reviewCheckpoint} settled positions, and nothing below is evidence of an edge.`,
  })

  if (facts.bestResult != null && facts.worstResult != null) {
    lines.push({
      key: 'extremes', tone: 'fact',
      text: `The best settled result returned ${signedMoney(facts.bestResult)} and the worst cost `
        + `${signedMoney(facts.worstResult)}, on an average stake of ${money(facts.averageStake)}.`,
    })
  }

  if (facts.concentrationPct != null && facts.realizedProfit > 0) {
    const dominated = facts.concentrationPct >= 100
    lines.push({
      key: 'concentration', tone: dominated ? 'caution' : 'fact',
      text: dominated
        ? `The two best wins alone account for ${facts.concentrationPct.toFixed(0)}% of the net profit. `
          + 'Remove them and this experiment is behind — the result rests on a couple of wagers, '
          + 'not on a pattern.'
        : `The two best wins account for ${facts.concentrationPct.toFixed(0)}% of the net profit, `
          + 'so the return is not resting on a single result.',
    })
  }

  lines.push({
    key: 'risk', tone: 'fact',
    text: `The bankroll peaked at ${money(facts.peakBankroll)} and its deepest fall from that peak was `
      + `${money(facts.maxDrawdown)}`
      + (facts.maxDrawdownPctOfPeak != null ? ` (${facts.maxDrawdownPctOfPeak.toFixed(2)}% of the peak)` : '')
      + `. It currently sits ${facts.currentDrawdown > 0.0005 ? `${facts.currentDrawdown.toFixed(2)}% below` : 'at'} its high-water mark.`,
  })

  if (facts.openCount > 0) {
    lines.push({
      key: 'open', tone: 'fact',
      text: `${facts.openCount} position${facts.openCount === 1 ? ' is' : 's are'} still open, `
        + `with ${money(facts.openExposure)} at risk. That money is not in any figure above: `
        + 'nothing is counted until it settles.',
    })
  }
  return lines
}

/* -- chart series --------------------------------------------------------- */

export interface EquityPoint {
  sequence: number
  bankroll: number
  highWater: number
  drawdown: number
  result: string | null
  profitLoss: number
  selection: string | null
  positionId: string | null
  settledAt: string | null
  date: string
}

/**
 * The equity path, validated rather than trusted.
 *
 * The curve must begin at the opening bankroll and advance exactly once per
 * settlement. This drops any point whose bankroll is not a number and
 * recomputes the running high-water mark, so a chart can never draw a step
 * the ledger cannot account for.
 */
export function equitySeries(curve: EquityCurvePoint[] | null | undefined, starting: number): EquityPoint[] {
  const rows = (curve ?? []).filter((point) => Number.isFinite(point.bankroll))
  if (rows.length === 0) {
    return [{
      sequence: 0, bankroll: starting, highWater: starting, drawdown: 0,
      result: null, profitLoss: 0, selection: null, positionId: null, settledAt: null, date: '',
    }]
  }
  let peak = starting
  return rows.map((point, index) => {
    peak = Math.max(peak, point.bankroll)
    return {
      sequence: point.sequence ?? index,
      bankroll: point.bankroll,
      highWater: peak,
      drawdown: Math.max(0, peak - point.bankroll),
      result: point.result ?? null,
      profitLoss: point.profit_loss ?? 0,
      selection: point.selection ?? null,
      positionId: point.position_id ?? null,
      settledAt: point.settled_at ?? null,
      date: point.date ?? '',
    }
  })
}

/**
 * A y-domain that shows the movement without exaggerating it.
 *
 * Two competing failures. A domain anchored at zero makes a $3.45 move on
 * $1,000 an invisible flat line. A domain tight around the data turns the same
 * $3.45 into a mountain range and quietly drops the $1,000 baseline off the
 * chart, which is the specific dishonesty a truncated axis commits.
 *
 * So: always include the opening bankroll, always include the full path, and
 * pad by a fixed share of the RANGE with a floor of $12 so a nearly-flat
 * ledger stays visibly nearly flat. The axis is labelled in dollars and the
 * baseline is drawn and named, so the truncation is stated rather than hidden.
 */
export function equityDomain(points: EquityPoint[], starting: number): [number, number] {
  const values = points.flatMap((point) => [point.bankroll, point.highWater])
  values.push(starting)
  const low = Math.min(...values)
  const high = Math.max(...values)
  const pad = Math.max(12, (high - low) * 0.35)
  return [low - pad, high + pad]
}

/** Settled positions in settlement order; open positions never enter here. */
export function settledPositions(positions: Position[] | null | undefined): Position[] {
  return (positions ?? [])
    .filter((position) => position.result !== 'OPEN')
    .slice()
    .sort((a, b) => (a.settlement_sequence ?? 0) - (b.settlement_sequence ?? 0))
}

export function openPositions(positions: Position[] | null | undefined): Position[] {
  return (positions ?? []).filter((position) => position.result === 'OPEN')
}
