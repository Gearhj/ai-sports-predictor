/** Ledger fixtures for the Active Wagers tests. Synthetic: never real positions. */
import type { Ledger, LedgerPosition, SportCode } from '../services/commandCenter'

export function pos(id: string, over: Partial<LedgerPosition> = {}): LedgerPosition {
  return {
    position_id: id, game_id: `g-${id}`, matchup: `A${id} @ H${id}`, starts_at: '2026-09-24T23:05:00Z',
    market: 'MONEYLINE', selection: `A${id} ML`,
    entry: { american: 150, decimal: 2.5, sportsbook: 'Draftkings (SportsGameOdds, research)', sportsbook_key: 'sgo:draftkings', provider: 'SportsGameOdds', classification: 'RESEARCH', quote_observed_at: '2026-09-24T16:00:00Z' },
    stake: 5, placed_at: '2026-09-24T16:20:00Z', status: 'OPEN', result: null, profit_loss: null, settled_at: null,
    experiment: { id: 'e', name: 'E' },
    ...over,
  }
}

export function ledgerOf(bySport: Partial<Record<SportCode, LedgerPosition[]>>, totals: Partial<Ledger['totals']> = {}): Ledger {
  const sports = (['MLB', 'NFL', 'NBA', 'CFB'] as SportCode[]).map((sport) => {
    const positions = bySport[sport] ?? []
    const open = positions.filter((p) => p.status !== 'SETTLED')
    return {
      sport, label: sport, account: { funded: true, label: '' }, starting_capital: 1000, realized_bankroll: 1000, profit_loss: 0,
      open_exposure: open.reduce((s, p) => s + (p.stake ?? 0), 0), available_capital: 1000, open_count: open.length,
      settled_count: positions.length - open.length, record: { WIN: 0, LOSS: 0, PUSH: 0 }, positions, series: [], reconciled: true,
    }
  })
  const openExposure = sports.reduce((s, a) => s + a.open_exposure, 0)
  return {
    schema_version: 'AISP_COMMAND_CENTER_LEDGER_V1', generated_at: '2026-09-24T17:59:00Z', authority: 'x',
    totals: {
      starting_capital: 4000, realized_bankroll: 3969.01, profit_loss: -30.99, open_exposure: openExposure,
      available_capital: 3969.01 - openExposure, open_count: sports.reduce((s, a) => s + a.open_count, 0), settled_count: 0,
      record: { WIN: 0, LOSS: 0, PUSH: 0 }, ...totals,
    },
    combined_series: [], sports,
  }
}
