import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BankrollData } from '../types/domain'
import { BankrollPage } from './BankrollPage'

vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts-placeholder" />,
}))

function basePayload(overrides: Partial<BankrollData>): BankrollData {
  return {
    data_mode: 'live',
    experiment_status: 'ACTIVE',
    experiment_id: 'v1-id',
    experiment_name: 'ASP MODEL BANKROLL — 2026 V1',
    experiment_start: '2026-08-27T10:15:00-04:00',
    controlling_engine: 'AISP_BASELINE',
    code_commit: 'abc',
    supported_markets: ['MONEYLINE'],
    starting_bankroll: 1000, current_bankroll: 1000, peak_bankroll: 1000,
    total_return: 0, roi: 0, max_drawdown: 0, current_drawdown: 0,
    record: '0–0–0', positions_count: 0,
    policy: 'AISP_OPPORTUNITY_POLICY_V1', policy_detail: 'Strict control.',
    policy_kelly_fraction: 0.25, policy_max_stake_percentage: 1, policy_edge_threshold_percentage: 2,
    equity_curve: [], monthly: [], daily: [], positions: [],
    open_positions: 0, settled_positions: 0, no_bet_days: 0,
    closing_quote_coverage: { positions_with_closing_quote: 0, settled_positions: 0, percentage: null },
    betting: null,
    ...overrides,
  } as BankrollData
}

const comparison = {
  experiments: [
    {
      key: 'v1', name: 'ASP MODEL BANKROLL — 2026 V1', policy: 'AISP_OPPORTUNITY_POLICY_V1',
      staking_policy: 'AISP_QUARTER_KELLY_1_2_5_V1', status: 'ACTIVE', effective_date: '2026-08-27',
      starting_bankroll: 1000, current_bankroll: 1000, games_evaluated: 58, decisions: 2954,
      qualified_decisions: 0, positions: 0, average_raw_edge: 4.1, average_adjusted_edge: null,
      average_stake: null, settled: 0, record: null, profit_loss: null, average_clv: null,
      policy_checksum: null,
    },
    {
      key: 'v2', name: 'ASP PRACTICAL PREGAME BANKROLL — 2026 V2', policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2',
      staking_policy: 'AISP_QUARTER_KELLY_EVIDENCE_V2', status: 'SCHEDULED', effective_date: '2026-09-01',
      starting_bankroll: 1000, current_bankroll: 1000, games_evaluated: 0, decisions: 0,
      qualified_decisions: 0, positions: 0, average_raw_edge: null, average_adjusted_edge: null,
      average_stake: null, settled: 0, record: null, profit_loss: null, average_clv: null,
      policy_checksum: 'deadbeef',
    },
  ],
}

const v2Funnel = {
  date: '2026-08-31', policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2',
  experiment_market_types: ['MONEYLINE'],
  steps: [
    { key: 'scheduled_games', label: 'Scheduled games', count: 12 },
    { key: 'starters_eligible', label: 'Starters CONFIRMED or PROBABLE_STABLE', count: 9 },
    { key: 'qualified', label: 'V2 qualified decision', count: 0 },
    { key: 'positions_created', label: 'Positions created', count: 0 },
  ],
  qualified_selections: 0, positions_created: 0, exposure_cap_exclusions: 0,
  top_rejection_reasons: [{ reason: 'Adjusted edge below minimum', count: 5 }],
  diagnostics: {
    blocked_only_by_confirmed_status: 9, blocked_only_by_lineup_confirmation: 0,
    blocked_only_by_unsupported_market: 2, blocked_only_by_adjusted_edge: 5,
    blocked_by_other_evidence: 4,
  },
  experiment_registered: true, effective_date: '2026-09-01', games: [],
}

function stub() {
  const calls: string[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(typeof input === 'string' ? input : (input as Request).url ?? input)
    calls.push(url)
    const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body } as Response)
    if (url.includes('/experiment/comparison')) return json(comparison)
    if (url.includes('/experiment/qualification-funnel')) return json(v2Funnel)
    if (url.includes('/bankroll') && url.includes('experiment=v2')) {
      return json(basePayload({
        experiment_status: 'SCHEDULED',
        experiment_id: 'v2-id',
        experiment_name: 'ASP PRACTICAL PREGAME BANKROLL — 2026 V2',
        policy: 'AISP_PRACTICAL_PREGAME_POLICY_V2',
      }))
    }
    if (url.includes('/bankroll')) return json(basePayload({}))
    return { ok: false, status: 404, json: async () => ({}) } as Response
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, calls }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('BankrollPage dual-policy experiments', () => {
  it('switches between the V1 strict control and the V2 challenger with separate data', async () => {
    const { calls } = stub()
    render(<MemoryRouter initialEntries={['/experiment']}><BankrollPage /></MemoryRouter>)

    expect(await screen.findAllByText('ASP MODEL BANKROLL — 2026 V1')).not.toHaveLength(0)
    const v2Tab = screen.getByRole('tab', { name: /Practical pregame challenger — V2/i })
    await userEvent.click(v2Tab)

    expect(await screen.findByText('SCHEDULED · PROSPECTIVE START')).toBeInTheDocument()
    expect(screen.getAllByText('ASP PRACTICAL PREGAME BANKROLL — 2026 V2').length).toBeGreaterThan(0)
    await waitFor(() => expect(calls.some((u) => u.includes('/bankroll?experiment=v2'))).toBe(true))
  }, 20000)

  it('renders the V1-vs-V2 comparison with separate bankrolls and settlement-gated results', async () => {
    stub()
    render(<MemoryRouter initialEntries={['/experiment']}><BankrollPage /></MemoryRouter>)

    const panel = (await screen.findByText('V1 vs V2')).closest('section, article, div') as HTMLElement
    expect(await screen.findByText(/SEPARATE LEDGERS — NEVER POOLED/i)).toBeInTheDocument()
    expect(screen.getAllByText('ASP PRACTICAL PREGAME BANKROLL — 2026 V2').length).toBeGreaterThan(0)
    expect(screen.getAllByText('No settled positions').length).toBeGreaterThan(0)
    expect(panel).toBeTruthy()
  })

  it('requests the V2 funnel with the policy parameter and shows diagnostics', async () => {
    const { calls } = stub()
    render(<MemoryRouter initialEntries={['/experiment?experiment=v2']}><BankrollPage /></MemoryRouter>)

    await screen.findByText(/Diagnostics: blocked only by CONFIRMED status 9/)
    expect(calls.some((u) => u.includes('/experiment/qualification-funnel') && u.includes('policy=v2'))).toBe(true)
    expect(screen.getByText('Starters CONFIRMED or PROBABLE_STABLE')).toBeInTheDocument()
  })
})
