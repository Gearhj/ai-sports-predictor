import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BankrollData, ExperimentComparison, ExperimentDecisionMetrics } from '../types/domain'
import { BankrollPage, policyVersionLabel } from './BankrollPage'

vi.mock('echarts-for-react', () => ({ default: () => <div data-testid="echarts-placeholder" /> }))

/**
 * V2.1 identity: the challenger is the SAME forecasting model under a
 * different decision policy, named by its real policy version, with decision
 * metrics that are never conflated with positions and early evidence labelled.
 */

const v2Metrics: ExperimentDecisionMetrics = {
  candidate_evaluations: 4083, games_evaluated: 55, qualified_evaluations: 317, qualified_selections: 15,
  frozen_positions: 8, settled_positions: 8, evidence_stage: 'EARLY_EVIDENCE',
  evidence_note: '8 settled position(s) — far too few to distinguish skill from noise; no performance claim is made.',
}

function payload(overrides: Partial<BankrollData>): BankrollData {
  return {
    data_mode: 'live', experiment_status: 'ACTIVE', experiment_id: 'v2-id',
    experiment_name: 'ASP PRACTICAL PREGAME BANKROLL — 2026 V2.1', experiment_start: '2026-09-01T10:15:00-04:00',
    controlling_engine: 'AISP_BASELINE', code_commit: 'abc', supported_markets: ['MONEYLINE'],
    starting_bankroll: 1000, current_bankroll: 999.78, peak_bankroll: 1000, total_return: -0.22, roi: -1.1,
    max_drawdown: -0.02, current_drawdown: -0.02, record: '3–5–0', positions_count: 8,
    policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1', policy_detail: 'Practical pregame policy.',
    policy_kelly_fraction: 0.25, policy_max_stake_percentage: 1, policy_edge_threshold_percentage: 2,
    equity_curve: [], monthly: [], daily: [], positions: [], open_positions: 0, settled_positions: 8, no_bet_days: 0,
    closing_quote_coverage: { positions_with_closing_quote: 5, settled_positions: 8, percentage: 62.5 },
    betting: null,
    experiment_key: 'v2', experiment_role: 'POLICY_CHALLENGER',
    policy_label: 'Practical pregame policy challenger — V2.1',
    policy_relationship: 'SAME MODEL · DIFFERENT DECISION POLICY — not a new forecasting model',
    model_name: 'asp_mlb_ensemble', model_version: '2.0.0-gate2-08cca634c81b', model_status: 'development',
    model_identity_source: 'decisions→forecasts→model_versions',
    decision_metrics: v2Metrics,
    ...overrides,
  } as BankrollData
}

const comparison: ExperimentComparison = {
  experiments: [
    {
      key: 'v1', name: 'ASP MODEL BANKROLL — 2026 V1', policy: 'AISP_OPPORTUNITY_POLICY_V1', staking_policy: 'S1', status: 'ACTIVE',
      effective_date: '2026-08-27', starting_bankroll: 1000, current_bankroll: 1000, games_evaluated: 113, decisions: 6646,
      qualified_decisions: 0, positions: 0, average_raw_edge: 4.1, average_adjusted_edge: null, average_stake: null, settled: 0,
      record: null, profit_loss: null, average_clv: null, policy_checksum: null,
      role: 'STRICT_CONTROL', policy_label: 'Strict control — V1', model_name: 'asp_mlb_ensemble', model_version: '2.0.0-gate2-08cca634c81b',
      decision_metrics: { candidate_evaluations: 6646, games_evaluated: 113, qualified_evaluations: 0, qualified_selections: 0, frozen_positions: 0, settled_positions: 0, evidence_stage: 'NO_EVIDENCE', evidence_note: 'No position has been created; nothing can be concluded.' },
    },
    {
      key: 'v2', name: 'ASP PRACTICAL PREGAME BANKROLL — 2026 V2.1', policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1', staking_policy: 'S2', status: 'ACTIVE',
      effective_date: '2026-09-01', starting_bankroll: 1000, current_bankroll: 999.78, games_evaluated: 55, decisions: 4083,
      qualified_decisions: 317, positions: 8, average_raw_edge: 3.2, average_adjusted_edge: 2.4, average_stake: 2.5, settled: 8,
      record: '3–5', profit_loss: -0.22, average_clv: -0.153, policy_checksum: 'chk',
      role: 'POLICY_CHALLENGER', policy_label: 'Practical pregame policy challenger — V2.1',
      policy_relationship: 'SAME MODEL · DIFFERENT DECISION POLICY — not a new forecasting model',
      model_name: 'asp_mlb_ensemble', model_version: '2.0.0-gate2-08cca634c81b', decision_metrics: v2Metrics,
    },
  ],
  superseded: [{ name: 'ASP PRACTICAL PREGAME BANKROLL — 2026 V2', policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2', status: 'SUPERSEDED_BEFORE_ACTIVATION', effective_date: '2026-09-01', decisions: 0, positions: 0, note: 'Preserved immutably.' }],
}

const funnel = {
  date: '2026-09-04', policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2_1', experiment_market_types: ['MONEYLINE'],
  steps: [{ key: 'scheduled_games', label: 'Scheduled games', count: 16 }, { key: 'valid_forecast', label: 'Valid forecast', count: 16 }, { key: 'qualified', label: 'V2.1 qualified decision', count: 0 }, { key: 'positions_created', label: 'Positions created', count: 0 }],
  qualified_selections: 0, positions_created: 0, exposure_cap_exclusions: 0, top_rejection_reasons: [], games: [],
  experiment_registered: true, effective_date: '2026-09-01',
}

function stubFetch() {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response
    if (url.includes('/experiment/comparison')) return json(comparison)
    if (url.includes('/experiment/qualification-funnel')) return json(funnel)
    if (url.includes('/bankroll')) return json(payload({}))
    return json({})
  }))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('policyVersionLabel', () => {
  it('derives the human version from the policy id', () => {
    expect(policyVersionLabel('AISP_PRACTICAL_PREGAME_POLICY_V2_1')).toBe('V2.1')
    expect(policyVersionLabel('AISP_PRACTICAL_PREGAME_POLICY_V2')).toBe('V2')
    expect(policyVersionLabel('AISP_OPPORTUNITY_POLICY_V1')).toBe('V1')
    expect(policyVersionLabel('NO_VERSION')).toBeNull()
    expect(policyVersionLabel(null)).toBeNull()
  })
})

describe('BankrollPage V2.1 identity', () => {
  it('names the challenger by its real policy version and states SAME MODEL · DIFFERENT DECISION POLICY', async () => {
    stubFetch()
    render(<MemoryRouter initialEntries={['/experiment?experiment=v2']}><BankrollPage /></MemoryRouter>)
    expect(await screen.findByText('PRACTICAL PREGAME POLICY CHALLENGER — V2.1')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /V2\.1 — Practical Pregame Policy Challenger/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('SAME MODEL · DIFFERENT DECISION POLICY')).toBeInTheDocument()
    expect(screen.getByText(/Model asp_mlb_ensemble 2\.0\.0-gate2-08cca634c81b \(development\)/)).toBeInTheDocument()
    expect(screen.getByText(/identity from decisions→forecasts→model_versions/)).toBeInTheDocument()
    expect(screen.queryByText(/CHALLENGER — V2\b(?!\.)/)).not.toBeInTheDocument()
  })

  it('shows distinct decision metrics and the early-evidence label instead of conflating evaluations with positions', async () => {
    stubFetch()
    render(<MemoryRouter initialEntries={['/experiment?experiment=v2']}><BankrollPage /></MemoryRouter>)
    const metrics = await screen.findByRole('region', { name: /Decision metrics/i })
    expect(within(metrics).getByText('Candidate evaluations')).toBeInTheDocument()
    expect(within(metrics).getByText('4083')).toBeInTheDocument()
    expect(within(metrics).getByText('55 distinct games · one per pregame phase')).toBeInTheDocument()
    expect(within(metrics).getByText('317')).toBeInTheDocument()
    expect(within(metrics).getByText('15 distinct qualified selections')).toBeInTheDocument()
    expect(within(metrics).getByText('Frozen positions')).toBeInTheDocument()
    expect(within(metrics).getByText('8')).toBeInTheDocument()
    expect(within(metrics).getByText('EARLY EVIDENCE')).toBeInTheDocument()
    expect(within(metrics).getByText(/far too few to distinguish skill from noise/)).toBeInTheDocument()
  })

  it('renders the V1 vs V2.1 comparison with model, policy and distinct metric rows', async () => {
    stubFetch()
    render(<MemoryRouter initialEntries={['/experiment?experiment=v2']}><BankrollPage /></MemoryRouter>)
    const table = await screen.findByRole('region', { name: /Experiment comparison/i })
    expect(screen.getByText('V1 vs V2.1')).toBeInTheDocument()
    expect(screen.getByText(/STRICT CONTROL VS PRACTICAL POLICY CHALLENGER · SAME MODEL/)).toBeInTheDocument()
    const rowOf = (label: string) => within(table).getByText(label).closest('tr') as HTMLElement
    expect(within(rowOf('Forecasting model')).getAllByText('asp_mlb_ensemble 2.0.0-gate2-08cca634c81b')).toHaveLength(2)
    expect(within(rowOf('Decision policy')).getByText('AISP_OPPORTUNITY_POLICY_V1')).toBeInTheDocument()
    expect(within(rowOf('Decision policy')).getByText('AISP_PRACTICAL_PREGAME_POLICY_V2_1')).toBeInTheDocument()
    expect(within(rowOf('Candidate evaluations (per phase)')).getByText('4083')).toBeInTheDocument()
    expect(within(rowOf('Qualified evaluations')).getByText('317')).toBeInTheDocument()
    expect(within(rowOf('Qualified selections (distinct)')).getByText('15')).toBeInTheDocument()
    expect(within(rowOf('Frozen positions')).getByText('8')).toBeInTheDocument()
    expect(screen.getByText(/Candidate evaluations count every pregame phase snapshot and are not positions/)).toBeInTheDocument()
    expect(screen.getByText(/V2\.1: 8 settled position\(s\)/)).toBeInTheDocument()
  })

  it('labels the V2.1 qualification funnel by the policy version the backend evaluated', async () => {
    stubFetch()
    render(<MemoryRouter initialEntries={['/experiment?experiment=v2']}><BankrollPage /></MemoryRouter>)
    expect(await screen.findByText(/V2\.1 PRACTICAL POLICY CHALLENGER · SAME MODEL/)).toBeInTheDocument()
    expect(screen.getByText(/V2\.1 effective from/)).toBeInTheDocument()
    expect(screen.getByText(/Same forecasting model as V1; only the decision policy differs/)).toBeInTheDocument()
  })
})
