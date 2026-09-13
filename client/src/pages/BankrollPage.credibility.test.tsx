import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BankrollData, EquityCurvePoint, ExperimentComparison, Position, QualificationFunnel } from '../types/domain'
import { BankrollPage, deriveTodayResult, formatClv, resolveExperimentKey } from './BankrollPage'

vi.mock('echarts-for-react', () => ({ default: () => <div data-testid="echarts-placeholder" /> }))

/**
 * Credibility-first public proof page: the bare route is the ACTIVE practical
 * experiment (V2.1), every bankroll/P/L/position figure on the page is a
 * bookkeeping fact that reconciles with the ledger, losses are never hidden,
 * missing closing evidence is never zero, and the funnel sits lower, collapsed.
 */

const positionsOldestFirst: Position[] = [
  { id: 'p1', date: '2026-09-01', game_id: 'g1', selection: 'TOR ML', market: 'Moneyline', price: 154, stake: 2.5, result: 'LOSS', profit_loss: -2.5, closing_price: 154, clv: 0, matchup: 'TOR @ CLE', sportsbook: 'FanDuel', bankroll_after: 997.5, settlement_sequence: 1, settled_at: '2026-09-01T21:34:49-04:00', beat_close: false, model_probability: 0.468, market_probability: 0.378, raw_edge: 0.0743, adjusted_edge: 0.048442, decision_id: 'd1', decision_phase: 'FINAL_PREGAME', recommended_at: '2026-09-01T18:50:00-04:00' },
  { id: 'p2', date: '2026-09-02', game_id: 'g2', selection: 'CIN ML', market: 'Moneyline', price: 143, stake: 2.5, result: 'WIN', profit_loss: 3.58, closing_price: 145, clv: -0.816, matchup: 'SD @ CIN', sportsbook: 'LowVig.ag', bankroll_after: 1001.08, settlement_sequence: 2, settled_at: '2026-09-02T16:10:04-04:00', beat_close: false, model_probability: 0.4674, market_probability: 0.4019, raw_edge: 0.0559, adjusted_edge: 0.027834, decision_id: 'd2', decision_phase: 'FINAL_PREGAME', recommended_at: '2026-09-02T12:30:00-04:00' },
  { id: 'p3', date: '2026-09-02', game_id: 'g3', selection: 'COL ML', market: 'Moneyline', price: 160, stake: 2.5, result: 'WIN', profit_loss: 4, closing_price: 160, clv: 0, matchup: 'BAL @ COL', sportsbook: 'BetRivers', bankroll_after: 1005.08, settlement_sequence: 3, settled_at: '2026-09-02T19:36:39-04:00', beat_close: false, model_probability: 0.4481, market_probability: 0.3743, raw_edge: 0.0635, adjusted_edge: 0.033844, decision_id: 'd3', decision_phase: 'FINAL_PREGAME', recommended_at: '2026-09-02T14:50:00-04:00' },
  { id: 'p4', date: '2026-09-02', game_id: 'g4', selection: 'CLE ML', market: 'Moneyline', price: 115, stake: 2.5, result: 'LOSS', profit_loss: -2.5, closing_price: null, clv: null, matchup: 'TOR @ CLE', sportsbook: 'BetUS', bankroll_after: 1002.58, settlement_sequence: 4, settled_at: '2026-09-03T12:17:50-04:00', beat_close: null, model_probability: 0.5372, market_probability: 0.4548, raw_edge: 0.0721, adjusted_edge: 0.040329, decision_id: 'd4', decision_phase: 'FINAL_PREGAME', recommended_at: '2026-09-02T18:00:00-04:00' },
  { id: 'p5', date: '2026-09-03', game_id: 'g5', selection: 'CWS ML', market: 'Moneyline', price: 139, stake: 2.5, result: 'LOSS', profit_loss: -2.5, closing_price: 146, clv: -2.846, matchup: 'CWS @ HOU', sportsbook: 'BetOnline.ag', bankroll_after: 1000.08, settlement_sequence: 5, settled_at: '2026-09-03T17:15:02-04:00', beat_close: false, model_probability: 0.5172, market_probability: 0.4083, raw_edge: 0.0988, adjusted_edge: 0.062562, decision_id: 'd5', decision_phase: 'FINAL_PREGAME', recommended_at: '2026-09-03T13:50:00-04:00' },
  { id: 'p6', date: '2026-09-03', game_id: 'g6', selection: 'MIA ML', market: 'Moneyline', price: 113, stake: 2.5, result: 'LOSS', profit_loss: -2.5, closing_price: 107, clv: 2.899, matchup: 'MIA @ KC', sportsbook: 'LowVig.ag', bankroll_after: 997.58, settlement_sequence: 6, settled_at: '2026-09-03T22:34:54-04:00', beat_close: true, model_probability: 0.5361, market_probability: 0.458, raw_edge: 0.0666, adjusted_edge: 0.045432, decision_id: 'd6', decision_phase: 'FINAL_PREGAME', recommended_at: '2026-09-03T19:30:00-04:00' },
  { id: 'p7', date: '2026-09-03', game_id: 'g7', selection: 'STL ML', market: 'Moneyline', price: 240, stake: 2.5, result: 'LOSS', profit_loss: -2.5, closing_price: null, clv: null, matchup: 'STL @ LAD', sportsbook: 'FanDuel', bankroll_after: 995.08, settlement_sequence: 7, settled_at: '2026-09-04T01:35:01-04:00', beat_close: null, model_probability: 0.4153, market_probability: 0.2825, raw_edge: 0.1211, adjusted_edge: 0.087642, decision_id: 'd7', decision_phase: 'FINAL_PREGAME', recommended_at: '2026-09-03T21:50:00-04:00' },
  { id: 'p8', date: '2026-09-03', game_id: 'g8', selection: 'ATH ML', market: 'Moneyline', price: 188, stake: 2.5, result: 'WIN', profit_loss: 4.7, closing_price: null, clv: null, matchup: 'ATH @ SEA', sportsbook: 'BetOnline.ag', bankroll_after: 999.78, settlement_sequence: 8, settled_at: '2026-09-04T01:35:01-04:00', beat_close: null, model_probability: 0.4412, market_probability: 0.3392, raw_edge: 0.094, adjusted_edge: 0.022082, decision_id: 'd8', decision_phase: 'FINAL_PREGAME', recommended_at: '2026-09-03T21:40:00-04:00' },
]

const equity: EquityCurvePoint[] = [
  { date: '2026-09-01', bankroll: 1000, benchmark: 1000, sequence: 0, position_id: null, selection: null, result: null, profit_loss: 0, high_water: 1000, drawdown: 0, settled_at: null },
  ...positionsOldestFirst.map((p, i) => ({
    date: (p.settled_at ?? p.date).slice(0, 10), bankroll: p.bankroll_after!, benchmark: 1000, sequence: i + 1, position_id: p.id, selection: p.selection,
    result: p.result as EquityCurvePoint['result'], profit_loss: p.profit_loss,
    high_water: Math.max(1000, ...positionsOldestFirst.slice(0, i + 1).map((q) => q.bankroll_after!)),
    drawdown: Math.round((Math.max(1000, ...positionsOldestFirst.slice(0, i + 1).map((q) => q.bankroll_after!)) - p.bankroll_after!) * 100) / 100,
    settled_at: p.settled_at,
  })),
]

function v2Payload(overrides: Partial<BankrollData> = {}): BankrollData {
  return {
    data_mode: 'live', experiment_status: 'ACTIVE', experiment_id: '43fe1b16-bd4d-4fce-ad6e-2aad7c5ebd66',
    experiment_name: 'ASP PRACTICAL PREGAME BANKROLL — 2026 V2.1', experiment_start: '2026-08-31T11:35:00-04:00',
    experiment_start_date: '2026-09-01', controlling_engine: 'AISP_BASELINE', code_commit: 'abc123', supported_markets: ['MONEYLINE'],
    starting_bankroll: 1000, current_bankroll: 999.78, peak_bankroll: 1005.08, total_return: -0.22, roi: -1.1,
    max_drawdown: -1.0, current_drawdown: 0.527, record: '3–5–0', positions_count: 8,
    policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1', policy_detail: 'Practical pregame policy.',
    policy_kelly_fraction: 0.25, policy_max_stake_percentage: 1, policy_edge_threshold_percentage: 2,
    equity_curve: equity, monthly: [{ month: '2026-09', positions: 8, record: '3–5–0', profit_loss: -0.22, roi: -1.1 }],
    daily: [{ date: '2026-09-04', positions: 0, settled: 2, amount_risked: 0, profit_loss: 2.2, bankroll_after: 999.78, no_bet: true, no_bet_reasons: ['Policy conditions not met'] }],
    positions: [...positionsOldestFirst].reverse(), open_positions: 0, settled_positions: 8, no_bet_days: 1,
    closing_quote_coverage: { positions_with_closing_quote: 5, settled_positions: 8, percentage: 62.5 },
    betting: {
      starting_bankroll: 1000, current_bankroll: 999.78, net_profit_loss: -0.22, total_risked: 20, roi: -1.1, yield_pct: -1.1,
      positions: 8, settled: 8, open: 0, wins: 3, losses: 5, pushes: 0, voids: 0, record: '3–5–0', max_drawdown: 10,
      longest_losing_streak: 4, closing_line_value: -0.153, beat_close_percentage: 20, has_settled: true, bankroll_chain_reconciled: true, breakdowns: {},
    },
    experiment_key: 'v2', experiment_role: 'POLICY_CHALLENGER', policy_label: 'Practical pregame policy challenger — V2.1',
    policy_relationship: 'SAME MODEL · DIFFERENT DECISION POLICY — not a new forecasting model',
    model_name: 'asp_mlb_ensemble', model_version: '2.0.0-gate2-08cca634c81b', model_status: 'development',
    model_identity_source: 'decisions→forecasts→model_versions',
    staking_policy: 'AISP_QUARTER_KELLY_EVIDENCE_V2', policy_checksum: '7e439d4bed6ec67e261dfc272e3b1ced93f01f0d59a8813b867dc1b39620a8d2',
    decision_metrics: {
      candidate_evaluations: 4083, games_evaluated: 55, qualified_evaluations: 317, qualified_selections: 15, frozen_positions: 8, settled_positions: 8,
      evidence_stage: 'EARLY_EVIDENCE', evidence_note: '8 settled position(s) — far too few to distinguish skill from noise; no performance claim is made.',
    },
    ...overrides,
  } as BankrollData
}

function v1Payload(): BankrollData {
  return v2Payload({
    experiment_id: '13604fc0-ef5b-4dee-b270-41e8ad0bc0ef', experiment_name: 'ASP MODEL BANKROLL — 2026 V1', experiment_start: '2026-08-27T23:51:06-04:00',
    experiment_start_date: '2026-08-27', current_bankroll: 1000, peak_bankroll: 1000, total_return: 0, roi: 0, max_drawdown: 0, current_drawdown: 0,
    record: '0–0–0', positions_count: 0, policy: 'AISP_OPPORTUNITY_POLICY_V1', equity_curve: [{ date: '2026-08-27', bankroll: 1000, benchmark: 1000, sequence: 0 }],
    monthly: [], daily: [], positions: [], settled_positions: 0, closing_quote_coverage: { positions_with_closing_quote: 0, settled_positions: 0, percentage: null },
    betting: null, experiment_key: 'v1', experiment_role: 'STRICT_CONTROL', policy_label: 'Strict control — V1', policy_relationship: null,
    staking_policy: 'AISP_QUARTER_KELLY_1_2_5_V1', policy_checksum: null,
    decision_metrics: { candidate_evaluations: 6646, games_evaluated: 113, qualified_evaluations: 0, qualified_selections: 0, frozen_positions: 0, settled_positions: 0, evidence_stage: 'NO_EVIDENCE', evidence_note: 'No settled positions.' },
  })
}

const comparison: ExperimentComparison = {
  experiments: [
    {
      key: 'v1', name: 'ASP MODEL BANKROLL — 2026 V1', policy: 'AISP_OPPORTUNITY_POLICY_V1', staking_policy: 'AISP_QUARTER_KELLY_1_2_5_V1', status: 'ACTIVE',
      effective_date: '2026-08-27', starting_bankroll: 1000, current_bankroll: 1000, games_evaluated: 113, decisions: 6646, qualified_decisions: 0, positions: 0,
      average_raw_edge: 4.1, average_adjusted_edge: null, average_stake: null, settled: 0, record: null, profit_loss: null, average_clv: null, clv_sample_size: 0, total_risked: 0,
      policy_checksum: null, role: 'STRICT_CONTROL', policy_label: 'Strict control — V1', model_name: 'asp_mlb_ensemble', model_version: '2.0.0-gate2-08cca634c81b',
      decision_metrics: { candidate_evaluations: 6646, games_evaluated: 113, qualified_evaluations: 0, qualified_selections: 0, frozen_positions: 0, settled_positions: 0, evidence_stage: 'NO_EVIDENCE', evidence_note: '' },
    },
    {
      key: 'v2', name: 'ASP PRACTICAL PREGAME BANKROLL — 2026 V2.1', policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1', staking_policy: 'AISP_QUARTER_KELLY_EVIDENCE_V2', status: 'ACTIVE',
      effective_date: '2026-09-01', starting_bankroll: 1000, current_bankroll: 999.78, games_evaluated: 55, decisions: 4083, qualified_decisions: 317, positions: 8,
      average_raw_edge: 3.8, average_adjusted_edge: 0.01, average_stake: 2.5, settled: 8, record: '3–5', profit_loss: -0.22, average_clv: -0.153, clv_sample_size: 5, total_risked: 20,
      policy_checksum: '7e439d4b', role: 'POLICY_CHALLENGER', policy_label: 'Practical pregame policy challenger — V2.1',
      policy_relationship: 'SAME MODEL · DIFFERENT DECISION POLICY — not a new forecasting model', model_name: 'asp_mlb_ensemble', model_version: '2.0.0-gate2-08cca634c81b',
      decision_metrics: { candidate_evaluations: 4083, games_evaluated: 55, qualified_evaluations: 317, qualified_selections: 15, frozen_positions: 8, settled_positions: 8, evidence_stage: 'EARLY_EVIDENCE', evidence_note: '8 settled position(s) — far too few to distinguish skill from noise; no performance claim is made.' },
    },
  ],
  superseded: [],
} as unknown as ExperimentComparison

function funnel(overrides: Partial<QualificationFunnel> = {}): QualificationFunnel {
  return {
    date: '2026-09-04', policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1', experiment_market_types: ['MONEYLINE'],
    steps: [
      { key: 'scheduled_games', label: 'Scheduled games', count: 16 },
      { key: 'valid_forecast', label: 'Valid forecast', count: 16 },
      { key: 'qualified', label: 'V2.1 qualified decision', count: 0 },
      { key: 'positions_created', label: 'Positions created', count: 0 },
    ],
    qualified_selections: 0, positions_created: 0, exposure_cap_exclusions: 0, top_rejection_reasons: [{ reason: 'Adjusted edge below minimum', count: 9 }],
    diagnostics: { awaiting_final_pregame_evaluation: 0, blocked_only_by_starter_instability: 0, blocked_only_by_lineup_conflict: 0, blocked_only_by_model_required_input: 0, previously_blocked_by_duplicate_feature_completeness_gate: 0, unsupported_market: 0, edge_ev_kelly_failure: 9, portfolio_cap_failure: 0 },
    experiment_registered: true, effective_date: '2026-09-01',
    games: [{ game_id: 'g9', matchup: 'DET @ CLE', starts_at: '2026-09-04T18:10:00-04:00', recommendation_state: 'NO_BET', failed_gate: 'edge', rejection_reasons: ['Adjusted edge below minimum'], readiness: 80, uncertainty: null, best_market: 'MONEYLINE', best_selection: 'CLE ML', experiment_status: 'ACTIVE' }],
    ...overrides,
  } as QualificationFunnel
}

function stub(options: { funnel?: QualificationFunnel; v2?: BankrollData; v1?: BankrollData } = {}) {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(typeof input === 'string' ? input : (input as Request).url ?? input)
    calls.push(url)
    const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body } as Response)
    if (url.includes('/experiment/comparison')) return json(comparison)
    if (url.includes('/experiment/qualification-funnel')) return json(options.funnel ?? funnel())
    if (url.includes('/bankroll') && url.includes('experiment=v1')) return json(options.v1 ?? v1Payload())
    if (url.includes('/bankroll')) return json(options.v2 ?? v2Payload())
    return json({})
  }))
  return calls
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}{location.search}</div>
}

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><BankrollPage /><LocationProbe /></MemoryRouter>)
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('experiment selection', () => {
  it('resolves the bare /experiment route to the active practical experiment (V2.1)', async () => {
    const calls = stub()
    renderAt('/experiment')
    expect(await screen.findByText('PRACTICAL PREGAME POLICY CHALLENGER — V2.1')).toBeInTheDocument()
    expect(screen.getAllByText('ASP PRACTICAL PREGAME BANKROLL — 2026 V2.1').length).toBeGreaterThan(0)
    expect(calls.some((u) => u.includes('/bankroll?experiment=v2'))).toBe(true)
    expect(calls.some((u) => u.includes('/bankroll?experiment=v1'))).toBe(false)
    expect(screen.getByRole('tab', { name: /V2\.1 — Practical Pregame Policy Challenger/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /V1 — Strict Control/ })).toHaveAttribute('aria-selected', 'false')
  })

  it('keeps ?experiment=v1 on the strict control and ?experiment=v2 as an alias of the challenger', async () => {
    const calls = stub()
    renderAt('/experiment?experiment=v1')
    expect(await screen.findByText('STRICT CONTROL — V1 · THE OFFICIAL ASP EXPERIMENT')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /V1 — Strict Control/ })).toHaveAttribute('aria-selected', 'true')
    expect(calls.some((u) => u.includes('/bankroll?experiment=v1'))).toBe(true)
    expect(resolveExperimentKey('v2')).toBe('v2')
    expect(resolveExperimentKey(null)).toBe('v2')
    expect(resolveExperimentKey('anything-else')).toBe('v2')
  })

  it('binds the selection to the URL so refresh and history preserve it', async () => {
    stub()
    renderAt('/experiment')
    await screen.findByText('PRACTICAL PREGAME POLICY CHALLENGER — V2.1')
    await userEvent.click(screen.getByRole('tab', { name: /V1 — Strict Control/ }))
    expect(await screen.findByText('STRICT CONTROL — V1 · THE OFFICIAL ASP EXPERIMENT')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/experiment?experiment=v1')
    await userEvent.click(screen.getByRole('tab', { name: /V2\.1 — Practical Pregame Policy Challenger/ }))
    expect(await screen.findByText('PRACTICAL PREGAME POLICY CHALLENGER — V2.1')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/experiment')
    expect(screen.getByTestId('location').textContent).not.toContain('experiment=')
  })

  it('never renders the other experiment’s figures under the selected tab while switching', async () => {
    let releaseV1: () => void = () => undefined
    const gate = new Promise<void>((resolve) => { releaseV1 = resolve })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(typeof input === 'string' ? input : (input as Request).url ?? input)
      const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body } as Response)
      if (url.includes('/experiment/comparison')) return json(comparison)
      if (url.includes('/experiment/qualification-funnel')) return json(funnel())
      if (url.includes('/bankroll') && url.includes('experiment=v1')) { await gate; return json(v1Payload()) }
      if (url.includes('/bankroll')) return json(v2Payload())
      return json({})
    }))
    renderAt('/experiment')
    await screen.findByText('PRACTICAL PREGAME POLICY CHALLENGER — V2.1')
    await userEvent.click(screen.getByRole('tab', { name: /V1 — Strict Control/ }))
    // V1 selected, V1 payload still in flight: no V2.1 figure or name may show.
    expect(screen.getByRole('tab', { name: /V1 — Strict Control/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByText('$999.78')).not.toBeInTheDocument()
    expect(screen.queryByText(/ASP PRACTICAL PREGAME BANKROLL/)).not.toBeInTheDocument()
    expect(screen.queryByText(/CHALLENGER — V1/)).not.toBeInTheDocument()
    releaseV1()
    expect(await screen.findByText('STRICT CONTROL — V1 · THE OFFICIAL ASP EXPERIMENT')).toBeInTheDocument()
    expect(screen.getByText('No V1 positions have settled. The opening bankroll is not a performance result.')).toBeInTheDocument()
  })

  it('labels both experiments as the same forecast model with distinct roles', async () => {
    stub()
    renderAt('/experiment')
    const tabs = await screen.findAllByRole('tab')
    expect(tabs).toHaveLength(2)
    expect(tabs[0]).toHaveTextContent('Active practical experiment · same forecast model')
    expect(tabs[1]).toHaveTextContent('Control · same forecast model')
  })
})

describe('primary evidence', () => {
  it('shows honest hero badges and the one-sentence contract without promotional language', async () => {
    stub()
    const { container } = renderAt('/experiment')
    await screen.findByText('PRACTICAL PREGAME POLICY CHALLENGER — V2.1')
    const badges = screen.getByLabelText('Experiment status badges')
    expect(within(badges).getByText('Active')).toBeInTheDocument()
    expect(within(badges).getByText('Virtual research experiment')).toBeInTheDocument()
    expect(within(badges).getByText('SAME MODEL · DIFFERENT DECISION POLICY')).toBeInTheDocument()
    expect(within(badges).getByText('EARLY EVIDENCE')).toBeInTheDocument()
    expect(within(badges).getByText('No real wagers')).toBeInTheDocument()
    expect(screen.getByText(/every position is recorded before first pitch, settled exactly once, and never edited or removed/)).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/guaranteed|risk-free|win big|beat the books every|proven edge/i)
  })

  it('reconciles the scoreboard with the ledger: bankroll, net P/L, ROI on risked capital, record, risked, drawdown', async () => {
    stub()
    renderAt('/experiment')
    const top = await screen.findByLabelText('Bankroll and position counts')
    const second = screen.getByLabelText('Record, risk and closing-line sample')
    const card = async (label: string) => (within(top).queryByText(label) ?? within(second).getByText(label)).closest('article') as HTMLElement
    expect(within(await card('Starting bankroll')).getByText('$1,000')).toBeInTheDocument()
    expect(within(await card('Current bankroll')).getByText('$999.78')).toBeInTheDocument()
    expect(within(await card('Net P/L')).getByText('-$0.22')).toBeInTheDocument()
    const roi = await card('ROI on risked capital')
    expect(within(roi).getByText('-1.1%')).toBeInTheDocument()
    expect(within(roi).getByText(/Net P\/L ÷ \$20\.00 settled stake — open exposure excluded, never ÷ bankroll/)).toBeInTheDocument()
    expect(within(roi).getByTitle(/The bankroll is never the denominator/)).toBeInTheDocument()
    expect(within(await card('Settled positions')).getByText('8')).toBeInTheDocument()
    expect(within(await card('Record')).getByText('3–5–0')).toBeInTheDocument()
    expect(within(await card('Amount risked')).getByText('$20.00')).toBeInTheDocument()
    expect(within(await card('Average stake')).getByText('$2.50')).toBeInTheDocument()
    const dd = await card('Max drawdown')
    expect(within(dd).getByText('$10.00')).toBeInTheDocument()
    expect(within(dd).getByText(/1\.0% of opening bankroll · peak-to-trough in settlement order/)).toBeInTheDocument()
    // Sum of ledger P/L equals net P/L and the last bankroll-after equals the current bankroll.
    const sum = positionsOldestFirst.reduce((acc, p) => acc + p.profit_loss, 0)
    expect(Math.round(sum * 100) / 100).toBe(-0.22)
    expect(positionsOldestFirst[positionsOldestFirst.length - 1].bankroll_after).toBe(999.78)
  })

  it('states the CLV sample size and never treats a missing closing quote as zero', async () => {
    stub()
    renderAt('/experiment')
    const clv = (await screen.findByText('CLV sample')).closest('article') as HTMLElement
    expect(within(clv).getByText('-0.153%')).toBeInTheDocument()
    expect(within(clv).getByText(/5 of 8 settled with a closing quote · missing quotes never count as zero/)).toBeInTheDocument()
    expect(formatClv(null)).toBe('Unavailable')
    expect(formatClv(0)).toBe('0%')
    expect(formatClv(-0.153)).toBe('-0.153%')
    expect(formatClv(1.5)).toBe('+1.5%')
  })

  it('shows CLV as unavailable when no legitimate closing sample exists', async () => {
    stub({ v2: v2Payload({ closing_quote_coverage: { positions_with_closing_quote: 0, settled_positions: 8, percentage: 0 }, betting: { ...v2Payload().betting!, closing_line_value: null } }) })
    renderAt('/experiment')
    const clv = (await screen.findByText('CLV sample')).closest('article') as HTMLElement
    expect(within(clv).getByText('Unavailable')).toBeInTheDocument()
    expect(within(clv).getByText('No legitimate closing sample')).toBeInTheDocument()
  })

  it('states the sample-size limitation and makes no profitability claim', async () => {
    stub()
    renderAt('/experiment')
    const note = await screen.findByText(/8 settled positions are far too few to distinguish skill from noise\./)
    expect(note.closest('p')).toHaveTextContent('No profitability claim is made.')
  })

  it('shows the frozen contract identity with checksums in a drawer', async () => {
    stub()
    renderAt('/experiment')
    const strip = await screen.findByLabelText('Frozen experiment contract')
    expect(within(strip).getByText(/Model asp_mlb_ensemble 2\.0\.0-gate2-08cca634c81b \(development\)/)).toBeInTheDocument()
    expect(within(strip).getByText('AISP_PRACTICAL_PREGAME_POLICY_V2_1')).toBeInTheDocument()
    expect(within(strip).getByText('Role · policy challenger')).toBeInTheDocument()
    expect(within(strip).getByText('AISP_QUARTER_KELLY_EVIDENCE_V2')).toBeInTheDocument()
    expect(within(strip).getByText('2026-09-01')).toBeInTheDocument()
    expect(within(strip).getByText('$1,000')).toBeInTheDocument()
    const drawer = within(strip).getByText('Checksums & audit identity').closest('details') as HTMLDetailsElement
    expect(drawer.open).toBe(false)
    expect(within(drawer).getByText('7e439d4bed6ec67e261dfc272e3b1ced93f01f0d59a8813b867dc1b39620a8d2')).toBeInTheDocument()
    expect(within(drawer).getByText('43fe1b16-bd4d-4fce-ad6e-2aad7c5ebd66')).toBeInTheDocument()
  })

  it('renders the V1 control with no settled evidence and no CLV', async () => {
    stub()
    renderAt('/experiment?experiment=v1')
    await screen.findByText('STRICT CONTROL — V1 · THE OFFICIAL ASP EXPERIMENT')
    expect(screen.getByText('ACTIVE · COLLECTING DATA')).toBeInTheDocument()
    const clv = screen.getByText('CLV sample').closest('article') as HTMLElement
    expect(within(clv).getByText('Unavailable')).toBeInTheDocument()
    expect(within(screen.getByText('Net P/L').closest('article') as HTMLElement).getByText('—')).toBeInTheDocument()
    expect(screen.getByText('Strict control · same forecast model as the challenger')).toBeInTheDocument()
  })
})

describe('equity curve', () => {
  it('starts at the opening bankroll and advances once per settlement in chronological order', async () => {
    stub()
    renderAt('/experiment')
    const path = await screen.findByRole('region', { name: 'Settlement path' })
    const rows = within(path).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(9)
    expect(rows[0]).toHaveTextContent('Open')
    expect(rows[0]).toHaveTextContent('Opening bankroll')
    expect(rows[0]).toHaveTextContent('$1,000.00')
    expect(rows[1]).toHaveTextContent('#1')
    expect(rows[1]).toHaveTextContent('TOR ML')
    expect(rows[1]).toHaveTextContent('$997.50')
    expect(rows[8]).toHaveTextContent('#8')
    expect(rows[8]).toHaveTextContent('$999.78')
  })

  it('shows the high-water mark and drawdown per settlement', async () => {
    stub()
    renderAt('/experiment')
    const path = await screen.findByRole('region', { name: 'Settlement path' })
    const rows = within(path).getAllByRole('row').slice(1)
    // #3 sets the high-water mark at $1,005.08; #7 is the trough ($995.08, drawdown $10.00).
    expect(rows[3]).toHaveTextContent('$1,005.08')
    expect(rows[7].textContent).toContain('$995.08')
    expect(rows[7].textContent).toContain('$10.00')
    expect(rows[7].textContent).toContain('$1,005.08')
  })

  it('renders every settlement of the fixture (nothing dropped) and includes losses', async () => {
    stub()
    renderAt('/experiment')
    const path = await screen.findByRole('region', { name: 'Settlement path' })
    expect(within(path).getAllByText('LOSS')).toHaveLength(5)
    expect(within(path).getAllByText('WIN')).toHaveLength(3)
  })

  it('shows the honest V1 empty state instead of a flat line', async () => {
    stub()
    renderAt('/experiment?experiment=v1')
    expect(await screen.findByText('No V1 positions have settled. The opening bankroll is not a performance result.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Settlement path' })).not.toBeInTheDocument()
  })

  it('does not draw a curve for a settled-less challenger either', async () => {
    stub({ v2: v2Payload({ equity_curve: [equity[0]], positions: [], settled_positions: 0, positions_count: 0, betting: { ...v2Payload().betting!, has_settled: false, settled: 0, positions: 0 } }) })
    renderAt('/experiment')
    expect(await screen.findByText('No positions have settled. The opening bankroll is not a performance result.')).toBeInTheDocument()
  })
})

describe('position history', () => {
  it('lists every position chronologically with the full evidence columns', async () => {
    stub()
    renderAt('/experiment')
    const ledger = await screen.findByRole('region', { name: 'Complete position ledger' })
    const headers = within(ledger).getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toEqual(['#', 'Date', 'Matchup', 'Selection', 'Market', 'Decision price', 'Stake', 'Result', 'P/L', 'Bankroll after', 'Closing price', 'CLV', 'Model prob.', 'Market prob.', 'Adjusted edge', 'Decision identity'])
    const rows = within(ledger).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(8)
    expect(rows[0]).toHaveTextContent('#1')
    expect(rows[0]).toHaveTextContent('TOR @ CLE')
    expect(rows[0]).toHaveTextContent('+154')
    expect(rows[0]).toHaveTextContent('$997.50')
    expect(rows[0]).toHaveTextContent('46.8%')
    expect(rows[0]).toHaveTextContent('37.8%')
    expect(rows[0]).toHaveTextContent('+4.84 pts')
    expect(rows[7]).toHaveTextContent('#8')
    expect(rows[7]).toHaveTextContent('ATH ML')
  })

  it('never hides losses and labels results as text, not colour alone', async () => {
    stub()
    renderAt('/experiment')
    const ledger = await screen.findByRole('region', { name: 'Complete position ledger' })
    expect(within(ledger).getAllByText('LOSS')).toHaveLength(5)
    expect(within(ledger).getAllByText('WIN')).toHaveLength(3)
    expect(screen.getByText('Every position as a table')).toBeInTheDocument()
    expect(screen.getByText('Losses are never removed')).toBeInTheDocument()
  })

  it('shows missing closing evidence as Unavailable, never as zero', async () => {
    stub()
    renderAt('/experiment')
    const ledger = await screen.findByRole('region', { name: 'Complete position ledger' })
    const rows = within(ledger).getAllByRole('row').slice(1)
    // #4 has no closing quote: closing price and CLV are both Unavailable.
    expect(within(rows[3]).getAllByText('Unavailable')).toHaveLength(2)
    // #1 closed at the decision price: CLV is exactly 0%, which is a real value.
    expect(within(rows[0]).getByText('0%')).toBeInTheDocument()
    expect(within(rows[0]).queryByText('Unavailable')).not.toBeInTheDocument()
  })

  it('expands a row to reveal the decision identity and settlement audit fields', async () => {
    stub()
    renderAt('/experiment')
    const ledger = await screen.findByRole('region', { name: 'Complete position ledger' })
    const buttons = within(ledger).getAllByRole('button', { name: 'Details' })
    expect(buttons).toHaveLength(8)
    expect(buttons[0]).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(buttons[0])
    expect(within(ledger).getByRole('button', { name: 'Hide' })).toHaveAttribute('aria-expanded', 'true')
    expect(within(ledger).getByText('d1')).toBeInTheDocument()
    expect(within(ledger).getByText('FINAL_PREGAME')).toBeInTheDocument()
    expect(within(ledger).getByText('2026-09-01T21:34:49-04:00')).toBeInTheDocument()
    expect(within(ledger).getByText('FanDuel')).toBeInTheDocument()
    expect(within(ledger).getByText('+7.43 pts')).toBeInTheDocument()
  })

  it('renders OPEN positions with no P/L, closing price or CLV', async () => {
    const open: Position = { ...positionsOldestFirst[0], id: 'p9', result: 'OPEN', profit_loss: 0, closing_price: null, clv: null, bankroll_after: null, settlement_sequence: null, settled_at: null, beat_close: null, date: '2026-09-04' }
    stub({ v2: v2Payload({ positions: [open, ...[...positionsOldestFirst].reverse()], open_positions: 1, positions_count: 9 }) })
    renderAt('/experiment')
    const openTable = await screen.findByRole('region', { name: 'Open positions awaiting settlement' })
    const row = within(openTable).getAllByRole('row')[1]
    expect(row).toHaveTextContent('OPEN')
    expect(within(row).queryByText('Unavailable')).not.toBeInTheDocument()
    const ledger = screen.getByRole('region', { name: 'Complete position ledger' })
    const rows = within(ledger).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(9)
    expect(rows[8]).toHaveTextContent('OPEN') // unsettled rows trail the settlement sequence
  })

  it('keeps ROI, record and amount risked on settled positions only when a position is open', async () => {
    const open: Position = { ...positionsOldestFirst[0], id: 'p9', result: 'OPEN', profit_loss: 0, closing_price: null, clv: null, bankroll_after: null, settlement_sequence: null, settled_at: null, beat_close: null, date: '2026-09-04', selection: 'SF ML', matchup: 'SF @ NYM', price: 172 }
    const betting = { ...v2Payload().betting!, total_risked: 22.5, roi: -0.98, yield_pct: -0.98, positions: 9, open: 1 }
    stub({ v2: v2Payload({ positions: [open, ...[...positionsOldestFirst].reverse()], open_positions: 1, positions_count: 9, betting }) })
    renderAt('/experiment')
    const top = await screen.findByLabelText('Bankroll and position counts')
    const second = screen.getByLabelText('Record, risk and closing-line sample')
    const roi = within(top).getByText('ROI on risked capital').closest('article') as HTMLElement
    expect(within(roi).getByText('-1.1%')).toBeInTheDocument() // -0.22 ÷ 20.00, not ÷ 22.50
    expect(within(roi).getByText(/÷ \$20\.00 settled stake — open exposure excluded/)).toBeInTheDocument()
    const openCard = within(top).getByText('Open positions').closest('article') as HTMLElement
    expect(within(openCard).getByText('1')).toBeInTheDocument()
    expect(within(openCard).getByText(/\$2\.50 open exposure · not in P\/L, record or ROI/)).toBeInTheDocument()
    expect(within(within(second).getByText('Amount risked').closest('article') as HTMLElement).getByText('$20.00')).toBeInTheDocument()
    expect(within(within(second).getByText('Record').closest('article') as HTMLElement).getByText('3–5–0')).toBeInTheDocument()
    expect(within(within(top).getByText('Net P/L').closest('article') as HTMLElement).getByText('-$0.22')).toBeInTheDocument()
    expect(within(within(second).getByText('Average stake').closest('article') as HTMLElement).getByText(/Total stake \$22\.50 ÷ 9 frozen positions/)).toBeInTheDocument()
  })

  it('keeps the honest empty ledger copy when nothing has been frozen', async () => {
    stub()
    renderAt('/experiment?experiment=v1')
    expect(await screen.findByText('No virtual positions have been frozen. A zero-position day is a valid experiment result.')).toBeInTheDocument()
  })

  it('compares V1 and V2.1 with distinct evaluation, selection and position counts', async () => {
    stub()
    renderAt('/experiment')
    const table = (await screen.findByRole('region', { name: 'Experiment comparison' })).querySelector('table') as HTMLTableElement
    const rowOf = (label: string) => within(table).getByText(label).closest('tr') as HTMLElement
    expect(within(rowOf('Qualified evaluations')).getByText('317')).toBeInTheDocument()
    expect(within(rowOf('Qualified selections (distinct)')).getByText('15')).toBeInTheDocument()
    expect(within(rowOf('Frozen positions')).getByText('8')).toBeInTheDocument()
    expect(within(rowOf('Settled positions')).getByText('8')).toBeInTheDocument()
    expect(within(rowOf('Total stake (settled + open)')).getByText('$20.00')).toBeInTheDocument()
    expect(within(rowOf('Net P/L (settled only)')).getByText('-$0.22')).toBeInTheDocument()
    expect(within(rowOf('CLV sample (settled only)')).getByText('-0.153% · 5 of 8 settled')).toBeInTheDocument()
    expect(within(rowOf('Start date')).getByText('2026-08-27')).toBeInTheDocument()
    expect(within(rowOf('Staking policy')).getByText('AISP_QUARTER_KELLY_EVIDENCE_V2')).toBeInTheDocument()
    expect(within(rowOf('Record')).getByText('No settled positions')).toBeInTheDocument()
  })
})

describe('funnel presentation and today’s result', () => {
  it('moves the funnel lower, collapsed by default, with full evidence when expanded', async () => {
    stub()
    const { container } = renderAt('/experiment')
    await screen.findByRole('region', { name: 'Complete position ledger' })
    const summary = await screen.findByText(/How today's slate was screened/)
    const details = summary.closest('details') as HTMLDetailsElement
    expect(details.open).toBe(false)
    expect(within(details).getByText('Qualification funnel')).toBeInTheDocument()
    expect(within(details).getByText(/DET @ CLE/)).toBeInTheDocument() // per-game evidence still present
    const text = container.textContent ?? ''
    // Order of the primary reading flow: the chart, then the full tape, then
    // the screening funnel further down.
    expect(text.indexOf('The tape')).toBeLessThan(text.indexOf("How today's slate was screened"))
    expect(text.indexOf('Every dollar, in settlement order')).toBeLessThan(text.indexOf('The tape'))
    // ...and the audit material stays after all of it: the bookkeeping grids
    // and the settlement-by-settlement table are complete and one click away,
    // not the first thing a reader meets.
    expect(text.indexOf('What it risked')).toBeLessThan(text.indexOf('Full bookkeeping detail'))
    expect(text.indexOf('The tape')).toBeLessThan(text.indexOf('Every position as a table'))
  })

  it('reports a zero-position day as a valid NO BET result', async () => {
    stub()
    renderAt('/experiment')
    const card = await screen.findByRole('status', { name: 'Experiment result for the selected date' })
    expect(card).toHaveTextContent('NO BET — POLICY CONDITIONS NOT MET')
    expect(card).toHaveTextContent('No position today. A zero-position day is a valid experiment result.')
  })

  it('derives every result state from the funnel without inventing positions', () => {
    expect(deriveTodayResult(funnel({ steps: [{ key: 'scheduled_games', label: 'Scheduled games', count: 0 }] }), 0).state).toBe('NO_GAMES')
    expect(deriveTodayResult(funnel({ positions_created: 2 }), 0).state).toBe('POSITIONS_FROZEN')
    expect(deriveTodayResult(funnel(), 1).state).toBe('POSITIONS_FROZEN')
    expect(deriveTodayResult(funnel({ qualified_selections: 3 }), 0).state).toBe('PROVISIONAL_CANDIDATES')
    expect(deriveTodayResult(funnel({ diagnostics: { ...funnel().diagnostics!, awaiting_final_pregame_evaluation: 4 } }), 0).state).toBe('AWAITING_FINAL_EVIDENCE')
    expect(deriveTodayResult(funnel(), 0).state).toBe('NO_BET')
    expect(deriveTodayResult(null, 0).state).toBe('UNAVAILABLE')
    expect(deriveTodayResult(undefined, 0).state).toBe('LOADING')
  })

  it('shows PROVISIONAL CANDIDATES when selections qualified but no position was frozen', async () => {
    stub({ funnel: funnel({ qualified_selections: 2 }) })
    renderAt('/experiment')
    const card = await screen.findByRole('status', { name: 'Experiment result for the selected date' })
    expect(card).toHaveTextContent('PROVISIONAL CANDIDATES')
    expect(card).toHaveTextContent('Nothing is a position until the final pregame evaluation confirms it')
  })

  it('keeps the funnel request bound to the selected policy', async () => {
    const calls = stub()
    renderAt('/experiment')
    await screen.findByText(/How today's slate was screened/)
    expect(calls.some((u) => u.includes('/experiment/qualification-funnel') && u.includes('policy=v2'))).toBe(true)
  })
})

describe('safety', () => {
  it('reads only public read endpoints and issues no writes', async () => {
    const calls = stub()
    renderAt('/experiment')
    await screen.findByRole('region', { name: 'Complete position ledger' })
    await screen.findByText(/How today's slate was screened/)
    const paths = new Set(calls.map((u) => u
      .replace(/^https?:\/\/[^/]+/, '')
      .split('?')[0]
      // The per-sport account read carries the sport in its path; the sport
      // itself is not what this test is pinning.
      .replace(/^\/api\/v1\/experiment\/accounts\/[^/]+$/, '/api/v1/experiment/accounts/:sport')))
    expect([...paths].sort()).toEqual([
      '/api/v1/bankroll', '/api/v1/experiment/comparison',
      '/api/v1/experiment/funnel', '/api/v1/experiment/qualification-funnel',
      // The season-account surface. Both are reads of stored ledger rows.
      '/api/v1/experiment/accounts', '/api/v1/experiment/accounts/:sport',
    ].sort())
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit | undefined
      expect((init?.method ?? 'GET').toUpperCase()).toBe('GET')
    }
  })

  it('warns when the persisted bankroll chain does not reconcile instead of hiding it', async () => {
    stub({ v2: v2Payload({ betting: { ...v2Payload().betting!, bankroll_chain_reconciled: false } }) })
    renderAt('/experiment')
    expect(await screen.findByRole('alert')).toHaveTextContent('Ledger reconciliation warning.')
  })

  it('exposes accessible structure: tabs, captions, expandable rows and status regions', async () => {
    stub()
    renderAt('/experiment')
    expect(await screen.findByRole('tablist', { name: 'Experiment selector' })).toBeInTheDocument()
    const ledger = screen.getByRole('region', { name: 'Complete position ledger' })
    expect(ledger).toHaveAttribute('tabindex', '0')
    expect(ledger.querySelector('caption')).not.toBeNull()
    expect(screen.getByRole('status', { name: 'Experiment result for the selected date' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Details' })[0]).toHaveAttribute('aria-controls', 'ledger-detail-p1')
  })
})
