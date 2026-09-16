/**
 * One scope for the whole Experiment page: Combined or a single sport account.
 *
 * The four-account ledger (`/command-center/ledger`) is the only input. The
 * Combined scope is every funded account together -- its starting capital,
 * realized bankroll and positions are the ledger totals, never one model
 * era's subset. A sport scope is exactly that sport's account.
 *
 * The result is shaped like the per-era `BankrollData` the Model-era panels
 * already draw (hero, narrative, settlement-order chart, tape), so every
 * metric, chart point, sentence and tape row on the page comes from the same
 * scoped rows. Nothing here reads a model-era endpoint.
 */
import type { BankrollData, EquityCurvePoint, Position } from '../types/domain'
import { SPORT_NAMES, type Ledger, type LedgerPosition, type SportCode } from './commandCenter'

export type ExperimentScope = 'COMBINED' | SportCode

export const SCOPES: ExperimentScope[] = ['COMBINED', 'MLB', 'NFL', 'NBA', 'CFB']
export const SCOPE_LABEL: Record<ExperimentScope, string> = { COMBINED: 'Combined', ...SPORT_NAMES }
export const REVIEW_CHECKPOINT = 50

export interface ScopedRow extends LedgerPosition { sport: SportCode }

export interface ScopedExperiment {
  scope: ExperimentScope
  label: string
  starting: number
  realized: number
  profitLoss: number
  openExposure: number
  available: number
  openCount: number
  settledCount: number
  record: { WIN: number; LOSS: number; PUSH: number }
  rows: ScopedRow[]
  open: ScopedRow[]
  settled: ScopedRow[]
  /** Per-era-shaped view for the Model-era panels. */
  data: BankrollData
}

const round2 = (v: number) => Math.round(v * 100) / 100
const etDate = (iso: string | null) => (iso ? new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(iso)) : '')
const settledOrder = (a: ScopedRow, b: ScopedRow) =>
  (a.settled_at ?? '').localeCompare(b.settled_at ?? '') || a.position_id.localeCompare(b.position_id)

export function scopeExperiment(ledger: Ledger | null, scope: ExperimentScope): ScopedExperiment | null {
  if (!ledger) return null
  const accounts = scope === 'COMBINED' ? ledger.sports : ledger.sports.filter((s) => s.sport === scope)
  const summary = scope === 'COMBINED' ? ledger.totals : accounts[0] ?? null
  const rows: ScopedRow[] = accounts.flatMap((s) => s.positions.map((p) => ({ ...p, sport: s.sport })))
  const open = rows.filter((p) => p.status !== 'SETTLED')
  const settled = rows.filter((p) => p.status === 'SETTLED').sort(settledOrder)

  const starting = summary?.starting_capital ?? 0
  const profitLoss = summary?.profit_loss ?? 0
  const realized = summary?.realized_bankroll ?? starting
  const record = summary?.record ?? { WIN: 0, LOSS: 0, PUSH: 0 }

  // Settlement-order path: opening capital plus cumulative settled P&L.
  let bankroll = starting
  let peak = starting
  let maxDrawdown = 0
  const curve: EquityCurvePoint[] = [{ date: '', bankroll: starting, benchmark: starting, sequence: 0 }]
  const sequenceById = new Map<string, number>()
  const afterById = new Map<string, number>()
  settled.forEach((p, index) => {
    bankroll = round2(bankroll + (p.profit_loss ?? 0))
    peak = Math.max(peak, bankroll)
    maxDrawdown = Math.max(maxDrawdown, peak - bankroll)
    sequenceById.set(p.position_id, index + 1)
    afterById.set(p.position_id, bankroll)
    curve.push({
      date: etDate(p.settled_at), bankroll, benchmark: starting, sequence: index + 1,
      position_id: p.position_id, selection: p.selection,
      result: (p.result as EquityCurvePoint['result']) ?? null, profit_loss: p.profit_loss ?? 0,
      high_water: peak, drawdown: round2(peak - bankroll), settled_at: p.settled_at,
    } as EquityCurvePoint)
  })

  const settledStake = settled.reduce((s, p) => s + (p.stake ?? 0), 0)
  const openExposure = summary?.open_exposure ?? open.reduce((s, p) => s + (p.stake ?? 0), 0)
  const results = settled.map((p) => p.profit_loss ?? 0)
  const wins = results.filter((v) => v > 0).sort((a, b) => b - a)
  const concentration = profitLoss > 0 && wins.length ? ((wins[0] + (wins[1] ?? 0)) / profitLoss) * 100 : null

  const positions: Position[] = rows.map((p) => ({
    id: p.position_id,
    date: etDate(p.starts_at ?? p.placed_at),
    game_id: p.game_id,
    selection: p.selection ?? p.market,
    market: p.market,
    price: p.entry.american as number,
    stake: p.stake ?? 0,
    result: (p.status === 'SETTLED' ? (p.result ?? 'VOID') : 'OPEN') as Position['result'],
    profit_loss: p.profit_loss ?? 0,
    closing_price: null,
    clv: null,
    matchup: p.matchup,
    sportsbook: p.entry.sportsbook,
    bankroll_after: afterById.get(p.position_id) ?? null,
    settlement_sequence: sequenceById.get(p.position_id) ?? null,
    settled_at: p.settled_at,
    league: p.sport,
  }))

  const data = {
    experiment_name: scope === 'COMBINED' ? 'Four-account virtual experiment' : `${SPORT_NAMES[scope]} account`,
    starting_bankroll: starting,
    current_bankroll: realized,
    peak_bankroll: peak,
    current_drawdown: peak ? ((peak - realized) / peak) * 100 : 0,
    open_exposure: openExposure,
    settled_positions: settled.length,
    open_positions: open.length,
    positions,
    equity_curve: curve,
    record: `${record.WIN}–${record.LOSS}–${record.PUSH}`,
    betting: {
      net_profit_loss: profitLoss,
      settled_stake: round2(settledStake),
      open_exposure: openExposure,
      total_risked: round2(settledStake + openExposure),
      wins: record.WIN, losses: record.LOSS, pushes: record.PUSH,
      settled: settled.length, open: open.length,
      record: `${record.WIN}–${record.LOSS}–${record.PUSH}`,
      max_drawdown: round2(maxDrawdown),
      best_settled_profit: results.length ? Math.max(...results) : null,
      worst_settled_profit: results.length ? Math.min(...results) : null,
      top_two_wins_share_of_profit_pct: concentration,
    },
  } as unknown as BankrollData

  return {
    scope, label: SCOPE_LABEL[scope], starting, realized, profitLoss, openExposure,
    available: summary?.available_capital ?? realized - openExposure,
    openCount: open.length, settledCount: settled.length, record, rows, open, settled, data,
  }
}

/** Every transaction (fundings and settlements) of the accounts in scope, in time order. */
export function scopedTransactions(ledger: Ledger | null, scope: ExperimentScope) {
  const accounts = (ledger?.sports ?? []).filter((s) => scope === 'COMBINED' || s.sport === scope)
  return accounts.flatMap((s) => s.series.map((p) => ({ ...p, sport: s.sport })))
    .sort((a, b) => (a.at ?? '').localeCompare(b.at ?? ''))
}
