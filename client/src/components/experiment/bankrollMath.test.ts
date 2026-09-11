/**
 * The experiment's money, and the specific ways it can be misreported.
 *
 * Three of these tests exist because the product actually got it wrong. The
 * home card divided realized profit by every dollar staked INCLUDING an open
 * position, called the result "ROI", and put it beside a bankroll that had
 * moved a third of a percent. Every number was real; the presentation was not.
 *
 * So: the two return measures are asserted against the live ledger's exact
 * figures, open money is asserted to stay out of every realized total, and the
 * equity path is asserted to begin at the opening bankroll and step once per
 * settlement.
 */
import { describe, expect, it } from 'vitest'
import {
  bankrollFacts, equityDomain, equitySeries, money, narrative, openPositions,
  settledPositions, signedMoney, signedPct,
} from './bankrollMath'
import type { BankrollData, Position } from '../../types/domain'

/** The real V2.1 ledger, as the API returns it. */
const LEDGER_CHAIN = [
  { seq: 1, pl: -2.50, after: 997.50, result: 'LOSS' },
  { seq: 2, pl: 3.58, after: 1001.08, result: 'WIN' },
  { seq: 3, pl: 4.00, after: 1005.08, result: 'WIN' },
  { seq: 4, pl: -2.50, after: 1002.58, result: 'LOSS' },
  { seq: 5, pl: -2.50, after: 1000.08, result: 'LOSS' },
  { seq: 6, pl: -2.50, after: 997.58, result: 'LOSS' },
  { seq: 7, pl: -2.50, after: 995.08, result: 'LOSS' },
  { seq: 8, pl: 4.70, after: 999.78, result: 'WIN' },
  { seq: 9, pl: -2.50, after: 997.28, result: 'LOSS' },
  { seq: 10, pl: 4.60, after: 1001.88, result: 'WIN' },
  { seq: 11, pl: -2.50, after: 999.38, result: 'LOSS' },
  { seq: 12, pl: 4.07, after: 1003.45, result: 'WIN' },
] as const

function position(over: Partial<Position> = {}): Position {
  return {
    id: `p-${Math.random()}`, date: '2026-09-10', game_id: 'g', selection: 'HOU ML',
    market: 'Moneyline', price: 163, stake: 2.5, result: 'WIN', profit_loss: 4.07,
    closing_price: null, clv: null, ...over,
  }
}

function ledger(over: Partial<BankrollData> = {}): BankrollData {
  const positions: Position[] = LEDGER_CHAIN.map((row) => position({
    id: `settled-${row.seq}`, result: row.result as Position['result'],
    profit_loss: row.pl, bankroll_after: row.after, settlement_sequence: row.seq,
  }))
  positions.push(position({
    id: 'open-1', result: 'OPEN', profit_loss: 0, bankroll_after: null,
    settlement_sequence: null, selection: 'TB ML', stake: 2.5,
  }))
  return {
    data_mode: 'live', experiment_status: 'ACTIVE',
    starting_bankroll: 1000, current_bankroll: 1003.45, peak_bankroll: 1005.08,
    lowest_bankroll: 995.08, total_return: 3.45,
    roi: 10.615384615384755,
    bankroll_return_pct: 0.345, return_on_settled_stake_pct: 11.5,
    settled_stake: 30, open_exposure: 2.5,
    max_drawdown: -1, record: '5–7–0', positions_count: 13,
    policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1', policy_detail: '',
    equity_curve: [
      {
        date: '2026-09-01', bankroll: 1000, benchmark: 1000, sequence: 0,
        position_id: null, selection: null, result: null, profit_loss: 0,
        high_water: 1000, drawdown: 0, settled_at: null,
      },
      ...LEDGER_CHAIN.map((row) => ({
        date: '2026-09-04', bankroll: row.after, benchmark: 1000, sequence: row.seq,
        position_id: `settled-${row.seq}`, selection: 'X ML', result: row.result,
        profit_loss: row.pl, high_water: 0, drawdown: 0,
        settled_at: '2026-09-04T01:35:01-04:00',
      })),
    ],
    monthly: [], positions,
    settled_positions: 12, open_positions: 1, current_drawdown: 0.162,
    experiment_name: 'ASP PRACTICAL PREGAME BANKROLL — 2026 V2.1',
    betting: {
      starting_bankroll: 1000, current_bankroll: 1003.45, net_profit_loss: 3.45,
      total_risked: 32.5, settled_stake: 30, open_exposure: 2.5, payouts: 33.45,
      roi: 10.62, yield_pct: 10.62,
      bankroll_return_pct: 0.345, return_on_settled_stake_pct: 11.5,
      peak_bankroll: 1005.08, lowest_bankroll: 995.08,
      max_drawdown_pct_of_peak: 0.995,
      streak_result: 'WIN', streak_length: 1,
      best_settled_profit: 4.7, worst_settled_profit: -2.5,
      top_two_wins_share_of_profit_pct: 269.6,
      positions: 13, settled: 12, open: 1, wins: 5, losses: 7, pushes: 0, voids: 0,
      record: '5–7–0', max_drawdown: 10, longest_losing_streak: 3,
      closing_line_value: -0.253, beat_close_percentage: 16.7,
      has_settled: true, bankroll_chain_reconciled: true, breakdowns: {},
    },
    ...over,
  } as BankrollData
}

describe('the two return measures', () => {
  it('reports the bankroll return against the starting bankroll', () => {
    const facts = bankrollFacts(ledger())
    // $3.45 on $1,000. This is what "how is the bankroll doing" means.
    expect(facts.bankrollReturnPct).toBeCloseTo(0.345, 3)
    expect(signedPct(facts.bankrollReturnPct, 2)).toBe('+0.34%')
  })

  it('reports the return on settled stake against SETTLED stake only', () => {
    const facts = bankrollFacts(ledger())
    // $3.45 on the $30.00 that has resolved — not the $32.50 staked in total.
    expect(facts.settledStake).toBe(30)
    expect(facts.returnOnSettledStakePct).toBeCloseTo(11.5, 6)
    expect(signedPct(facts.returnOnSettledStakePct, 2)).toBe('+11.50%')
  })

  it('never presents the legacy all-stake figure as either measure', () => {
    const facts = bankrollFacts(ledger())
    // 3.45 / 32.50 = 10.62%. It is neither of the two measures, and this is
    // the number the home card used to show beside the bankroll.
    expect(facts.bankrollReturnPct).not.toBeCloseTo(10.62, 1)
    expect(facts.returnOnSettledStakePct).not.toBeCloseTo(10.62, 1)
  })

  it('derives both measures itself when the API omits them', () => {
    const stale = ledger({
      bankroll_return_pct: null, return_on_settled_stake_pct: null, settled_stake: null,
      betting: { ...ledger().betting!, bankroll_return_pct: null, return_on_settled_stake_pct: null, settled_stake: undefined },
    })
    const facts = bankrollFacts(stale)
    expect(facts.settledStake).toBeCloseTo(30, 6)
    expect(facts.bankrollReturnPct).toBeCloseTo(0.345, 4)
    expect(facts.returnOnSettledStakePct).toBeCloseTo(11.5, 4)
  })
})

describe('open money stays out of realized figures', () => {
  it('excludes the open stake from the settled denominator', () => {
    const facts = bankrollFacts(ledger())
    expect(facts.totalStaked).toBe(32.5)
    expect(facts.settledStake).toBe(30)
    expect(facts.openExposure).toBe(2.5)
    expect(facts.settledStake + facts.openExposure).toBeCloseTo(facts.totalStaked, 6)
  })

  it('excludes the open position from the record and the settled count', () => {
    const facts = bankrollFacts(ledger())
    expect(facts.settledCount).toBe(12)
    expect(facts.openCount).toBe(1)
    expect(facts.wins + facts.losses + facts.pushes).toBe(facts.settledCount)
  })

  it('excludes the open position from realized profit', () => {
    const facts = bankrollFacts(ledger())
    const settledSum = LEDGER_CHAIN.reduce((sum, row) => sum + row.pl, 0)
    expect(facts.realizedProfit).toBeCloseTo(settledSum, 6)
    expect(facts.realizedProfit).toBeCloseTo(3.45, 6)
  })

  it('separates settled from open when splitting the position list', () => {
    const data = ledger()
    expect(settledPositions(data.positions)).toHaveLength(12)
    expect(openPositions(data.positions)).toHaveLength(1)
    expect(openPositions(data.positions)[0].result).toBe('OPEN')
  })
})

describe('the bankroll reconciles exactly', () => {
  it('current bankroll equals start plus realized profit', () => {
    const facts = bankrollFacts(ledger())
    expect(facts.startingBankroll + facts.realizedProfit).toBeCloseTo(facts.currentBankroll, 6)
    expect(money(facts.currentBankroll)).toBe('$1,003.45')
    expect(signedMoney(facts.realizedProfit)).toBe('+$3.45')
  })

  it('the settlement chain never skips or repeats a balance', () => {
    let running = 1000
    for (const row of LEDGER_CHAIN) {
      running = Math.round((running + row.pl) * 100) / 100
      expect(running).toBeCloseTo(row.after, 6)
    }
    expect(running).toBeCloseTo(1003.45, 6)
  })

  it('peak and trough match the chain', () => {
    const facts = bankrollFacts(ledger())
    const balances = LEDGER_CHAIN.map((row) => row.after)
    expect(facts.peakBankroll).toBeCloseTo(Math.max(1000, ...balances), 6)
    expect(facts.lowestBankroll).toBeCloseTo(Math.min(1000, ...balances), 6)
    // Peak $1,005.08 down to $995.08 is a $10.00 drawdown.
    expect(facts.maxDrawdown).toBeCloseTo(10, 6)
  })
})

describe('the equity series', () => {
  it('starts at the opening bankroll', () => {
    const points = equitySeries(ledger().equity_curve, 1000)
    expect(points[0].bankroll).toBe(1000)
    expect(points[0].result).toBeNull()
  })

  it('has exactly one point per settlement after the opening', () => {
    const points = equitySeries(ledger().equity_curve, 1000)
    expect(points).toHaveLength(13)
    expect(points.filter((point) => point.result != null)).toHaveLength(12)
  })

  it('recomputes the high-water mark rather than trusting the payload', () => {
    // The fixture ships high_water: 0 on every settlement, which would make
    // every drawdown negative if the series took it at face value.
    const points = equitySeries(ledger().equity_curve, 1000)
    expect(points[3].highWater).toBeCloseTo(1005.08, 6)
    expect(points.every((point) => point.drawdown >= 0)).toBe(true)
    expect(Math.max(...points.map((point) => point.drawdown))).toBeCloseTo(10, 6)
  })

  it('survives an empty curve by holding the opening balance', () => {
    const points = equitySeries([], 1000)
    expect(points).toHaveLength(1)
    expect(points[0].bankroll).toBe(1000)
  })
})

describe('the y-domain', () => {
  it('always contains the opening bankroll', () => {
    const points = equitySeries(ledger().equity_curve, 1000)
    const [low, high] = equityDomain(points, 1000)
    expect(low).toBeLessThan(1000)
    expect(high).toBeGreaterThan(1000)
  })

  it('contains the whole path', () => {
    const points = equitySeries(ledger().equity_curve, 1000)
    const [low, high] = equityDomain(points, 1000)
    expect(low).toBeLessThanOrEqual(995.08)
    expect(high).toBeGreaterThanOrEqual(1005.08)
  })

  it('does not exaggerate a tiny move into a mountain range', () => {
    // A $3.45 move must not fill the panel. The drawn range is padded well
    // beyond the data, so the visible slope stays modest.
    const points = equitySeries(ledger().equity_curve, 1000)
    const [low, high] = equityDomain(points, 1000)
    expect(high - low).toBeGreaterThan(20)
  })

  it('still shows movement on a perfectly flat ledger', () => {
    const flat = equitySeries([], 1000)
    const [low, high] = equityDomain(flat, 1000)
    expect(high - low).toBeGreaterThanOrEqual(24)
    expect(low).toBeLessThan(1000)
  })
})

describe('the narrative', () => {
  it('states both returns with their denominators', () => {
    const text = narrative(bankrollFacts(ledger())).map((line) => line.text).join(' ')
    expect(text).toContain('$1,003.45')
    expect(text).toContain('+0.34% on the bankroll')
    expect(text).toContain('+11.50% on the $30.00 of stake that has settled')
  })

  it('leads the caution with the sample size, not the profit', () => {
    const lines = narrative(bankrollFacts(ledger()))
    const caution = lines.find((line) => line.tone === 'caution')
    expect(caution?.text).toContain('12 positions have settled')
    expect(caution?.text).toContain('too few to distinguish skill from luck')
  })

  it('says plainly when the profit rests on two wagers', () => {
    const text = narrative(bankrollFacts(ledger())).map((line) => line.text).join(' ')
    expect(text).toContain('270% of the net profit')
    expect(text).toContain('Remove them and this experiment is behind')
  })

  it('keeps open exposure out of the figures and says so', () => {
    const text = narrative(bankrollFacts(ledger())).map((line) => line.text).join(' ')
    expect(text).toContain('$2.50 at risk')
    expect(text).toContain('nothing is counted until it settles')
  })

  it('makes no claim at all before anything settles', () => {
    const empty = ledger({
      positions: [], equity_curve: [], settled_positions: 0, open_positions: 0,
      current_bankroll: 1000, total_return: 0,
      betting: { ...ledger().betting!, settled: 0, open: 0, net_profit_loss: 0, has_settled: false },
    })
    const lines = narrative(bankrollFacts(empty))
    expect(lines).toHaveLength(1)
    expect(lines[0].tone).toBe('caution')
    expect(lines[0].text).toContain('No position has settled yet')
  })
})

describe('formatting never wraps or misleads', () => {
  it('uses a minus sign rather than a hyphen for negative money', () => {
    expect(money(-2.5)).toBe('−$2.50')
    expect(signedMoney(-2.5)).toBe('−$2.50')
    expect(signedMoney(4.07)).toBe('+$4.07')
  })

  it('renders a missing value as an explicit absence, never as zero', () => {
    expect(money(null)).toBe('—')
    expect(signedPct(undefined)).toBe('—')
    expect(money(Number.NaN)).toBe('—')
  })

  it('groups thousands so the hero value stays one short token', () => {
    expect(money(1003.45)).toBe('$1,003.45')
    expect(money(1000, 0)).toBe('$1,000')
  })
})
