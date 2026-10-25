import { describe, expect, it } from 'vitest'
import { bankrollFacts, equitySeries, narrative } from '../components/experiment/bankrollMath'
import type { Ledger, LedgerPosition, SportCode } from './commandCenter'
import { SCOPES, scopeExperiment, scopedTransactions } from './experimentScope'

const pos = (id: string, sport: SportCode, pnl: number | null, settledAt: string | null, era = 'era-2'): LedgerPosition => ({
  position_id: id, game_id: `g-${id}`, matchup: sport === 'NFL' ? 'IND @ KC' : 'BOS @ NYY', starts_at: '2026-09-10T23:00:00Z',
  market: 'MONEYLINE', selection: 'Home ML',
  entry: { american: 110, decimal: 2.1, sportsbook: 'Book', sportsbook_key: 'book', provider: 'P', classification: 'GOVERNED', quote_observed_at: null },
  stake: 5, placed_at: '2026-09-10T22:00:00Z', status: pnl == null ? 'OPEN' : 'SETTLED',
  result: pnl == null ? null : pnl > 0 ? 'WIN' : pnl < 0 ? 'LOSS' : 'PUSH', profit_loss: pnl, settled_at: settledAt,
  experiment: { id: era, name: era },
})

function account(sport: SportCode, positions: LedgerPosition[]) {
  const settled = positions.filter((p) => p.status === 'SETTLED')
  const openRows = positions.filter((p) => p.status !== 'SETTLED')
  const pnl = settled.reduce((s, p) => s + (p.profit_loss ?? 0), 0)
  const exposure = openRows.reduce((s, p) => s + (p.stake ?? 0), 0)
  return {
    sport, label: sport, account: { funded: true, label: sport }, starting_capital: 1000,
    realized_bankroll: 1000 + pnl, profit_loss: pnl, open_exposure: exposure, available_capital: 1000 + pnl - exposure,
    open_count: openRows.length, settled_count: settled.length,
    record: {
      WIN: settled.filter((p) => p.result === 'WIN').length,
      LOSS: settled.filter((p) => p.result === 'LOSS').length,
      PUSH: settled.filter((p) => p.result === 'PUSH').length,
    },
    positions,
    series: [
      { at: '2026-08-01T00:00:00Z', bankroll: 1000, kind: 'FUNDING' as const, position_id: null },
      ...settled.map((p, i) => ({ at: p.settled_at, bankroll: 1000 + settled.slice(0, i + 1).reduce((s, q) => s + (q.profit_loss ?? 0), 0), kind: 'SETTLEMENT' as const, position_id: p.position_id, change: p.profit_loss })),
    ],
    reconciled: true,
  }
}

/** MLB spans two model eras; the combined ledger must include both, plus NFL. */
function fixture(): Ledger {
  const sports = [
    account('MLB', [
      pos('m1', 'MLB', 4.5, '2026-09-01T03:00:00Z', 'era-1'),
      pos('m2', 'MLB', -5, '2026-09-08T03:00:00Z', 'era-2'),
      pos('m3', 'MLB', null, null, 'era-2'),
    ]),
    account('NFL', [pos('n1', 'NFL', -5, '2026-09-05T03:00:00Z')]),
    account('NBA', []),
    account('CFB', []),
  ]
  const sum = (key: 'starting_capital' | 'realized_bankroll' | 'profit_loss' | 'open_exposure' | 'available_capital' | 'open_count' | 'settled_count') =>
    sports.reduce((s, a) => s + a[key], 0)
  return {
    schema_version: 'AISP_COMMAND_CENTER_LEDGER_V1', generated_at: '', authority: '',
    totals: {
      starting_capital: sum('starting_capital'), realized_bankroll: sum('realized_bankroll'), profit_loss: sum('profit_loss'),
      open_exposure: sum('open_exposure'), available_capital: sum('available_capital'), open_count: sum('open_count'),
      settled_count: sum('settled_count'), record: { WIN: 1, LOSS: 2, PUSH: 0 },
    },
    combined_series: [],
    sports,
  }
}

describe('experiment scope', () => {
  it('Combined reconciles to the complete four-account ledger', () => {
    const ledger = fixture()
    const view = scopeExperiment(ledger, 'COMBINED')!
    expect(view.starting).toBe(4000)
    expect(view.realized).toBeCloseTo(3994.5)
    expect(view.profitLoss).toBeCloseTo(-5.5)
    expect(view.settledCount).toBe(3)
    expect(view.openCount).toBe(1)
    expect(view.rows).toHaveLength(4)
    const facts = bankrollFacts(view.data)
    expect(facts.currentBankroll).toBeCloseTo(ledger.totals.realized_bankroll)
    expect(facts.startingBankroll).toBe(ledger.totals.starting_capital)
    expect(facts.settledCount).toBe(ledger.totals.settled_count)
    expect(facts.record).toBe('1–2–0')
    // The chart walks every settlement in order and ends at the realized bankroll.
    const points = equitySeries(view.data.equity_curve, facts.startingBankroll)
    expect(points.map((p) => p.positionId)).toEqual([undefined, 'm1', 'n1', 'm2'].map((v) => v ?? null))
    expect(points.at(-1)!.bankroll).toBeCloseTo(ledger.totals.realized_bankroll)
    expect(facts.maxDrawdown).toBeCloseTo(10)
    // The narrative describes the same scope and keeps the small-sample caution.
    const lines = narrative(facts, 50)
    expect(lines.find((l) => l.key === 'sample')?.text).toMatch(/3 positions have settled .*far too few/)
    expect(lines.find((l) => l.key === 'position')?.text).toContain('$3,994.50')
  })

  it('a sport scope is exactly that account', () => {
    const ledger = fixture()
    for (const code of ['MLB', 'NFL', 'NBA', 'CFB'] as const) {
      const account = ledger.sports.find((s) => s.sport === code)!
      const view = scopeExperiment(ledger, code)!
      const facts = bankrollFacts(view.data)
      expect(facts.startingBankroll).toBe(account.starting_capital)
      expect(facts.currentBankroll).toBeCloseTo(account.realized_bankroll)
      expect(facts.settledCount).toBe(account.settled_count)
      expect(facts.openCount).toBe(account.open_count)
      expect(view.rows.every((r) => r.sport === code)).toBe(true)
      expect(view.data.positions.every((p) => p.league === code)).toBe(true)
      const points = equitySeries(view.data.equity_curve, facts.startingBankroll)
      expect(points.at(-1)!.bankroll).toBeCloseTo(account.realized_bankroll)
      expect(scopedTransactions(ledger, code).every((t) => t.sport === code)).toBe(true)
    }
    expect(scopeExperiment(ledger, 'NBA')!.data.positions).toHaveLength(0)
  })

  it('a single model era never stands in for Combined', () => {
    const ledger = fixture()
    const combined = scopeExperiment(ledger, 'COMBINED')!
    const mlb = scopeExperiment(ledger, 'MLB')!
    // Both MLB eras are in the MLB account, and the MLB account is not Combined.
    expect(mlb.rows.map((r) => r.experiment.id).sort()).toEqual(['era-1', 'era-2', 'era-2'])
    expect(combined.starting).not.toBe(mlb.starting)
    expect(combined.settledCount).toBeGreaterThan(mlb.settledCount)
    expect(combined.data.experiment_name).toMatch(/every sport account/)
    const eraOnly = mlb.rows.filter((r) => r.experiment.id === 'era-2' && r.status === 'SETTLED')
    expect(combined.settledCount).not.toBe(eraOnly.length)
    expect(scopedTransactions(ledger, 'COMBINED').filter((t) => t.kind === 'FUNDING')).toHaveLength(4)
  })

  it('offers Combined plus every sport account (NHL since 2026-10-16) and tolerates a missing ledger', () => {
    expect(SCOPES).toEqual(['COMBINED', 'MLB', 'NFL', 'NBA', 'CFB', 'NHL'])
    expect(scopeExperiment(null, 'COMBINED')).toBeNull()
  })

  it('the served five-account ledger counts the NHL account once in Combined and alone in its own scope', () => {
    const base = fixture()
    const nhl = account('NHL', [pos('h1', 'NHL', 3.2, '2026-10-17T03:30:00Z', 'era-nhl'), pos('h2', 'NHL', null, null, 'era-nhl')])
    const ledger: Ledger = {
      ...base,
      sports: [...base.sports, nhl],
      totals: {
        ...base.totals,
        starting_capital: base.totals.starting_capital + nhl.starting_capital,
        realized_bankroll: base.totals.realized_bankroll + nhl.realized_bankroll,
        profit_loss: base.totals.profit_loss + nhl.profit_loss,
        open_exposure: base.totals.open_exposure + nhl.open_exposure,
        available_capital: base.totals.available_capital + nhl.available_capital,
        open_count: base.totals.open_count + nhl.open_count,
        settled_count: base.totals.settled_count + nhl.settled_count,
        record: { WIN: base.totals.record.WIN + 1, LOSS: base.totals.record.LOSS, PUSH: 0 },
      },
    }
    const combined = scopeExperiment(ledger, 'COMBINED')!
    expect(combined.starting).toBe(5000)
    expect(combined.rows.filter((r) => r.sport === 'NHL').map((r) => r.position_id)).toEqual(['h1', 'h2'])
    expect(scopedTransactions(ledger, 'COMBINED').filter((t) => t.kind === 'FUNDING')).toHaveLength(5)
    const hockey = scopeExperiment(ledger, 'NHL')!
    expect(hockey.label).toBe('NHL')
    expect(hockey.starting).toBe(1000)
    expect(hockey.realized).toBeCloseTo(1003.2)
    expect([hockey.settledCount, hockey.openCount]).toEqual([1, 1])
    expect(hockey.data.positions.every((p) => p.league === 'NHL')).toBe(true)
    expect(hockey.data.experiment_name).toBe('NHL account')
  })
})
