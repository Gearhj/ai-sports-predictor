import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BankrollData, BettingPerformance } from '../types/domain'
import { BankrollPage } from './BankrollPage'

vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts-placeholder" />,
}))

function jsonResponse(payload: BankrollData) {
  return { ok: true, status: 200, json: async () => payload } as Response
}

const collectingBetting: BettingPerformance = {
  starting_bankroll: 1000,
  current_bankroll: 1000,
  net_profit_loss: 0,
  total_risked: 0,
  roi: null,
  yield_pct: null,
  positions: 0,
  settled: 0,
  open: 0,
  wins: 0,
  losses: 0,
  pushes: 0,
  voids: 0,
  record: '0–0–0',
  max_drawdown: 0,
  longest_losing_streak: 0,
  closing_line_value: null,
  beat_close_percentage: null,
  has_settled: false,
  breakdowns: {},
}

function activePayload(): BankrollData {
  return {
    data_mode: 'empty',
    experiment_status: 'ACTIVE',
    experiment_id: 'asp-model-bankroll-2026-v1',
    experiment_name: 'ASP MODEL BANKROLL — 2026 V1',
    experiment_start: '2026-08-27T10:15:00-04:00',
    controlling_engine: 'AISP_LIVE_V1',
    code_commit: '75041f2',
    supported_markets: ['MONEYLINE'],
    starting_bankroll: 1000,
    current_bankroll: 1000,
    peak_bankroll: 1000,
    total_return: 0,
    roi: 0,
    max_drawdown: 0,
    current_drawdown: 0,
    record: '0–0–0',
    positions_count: 0,
    policy: 'AISP_SHADOW_BANKROLL_V1',
    policy_detail: 'Quarter Kelly with frozen exposure caps.',
    policy_kelly_fraction: 0.25,
    policy_max_stake_percentage: 1,
    policy_edge_threshold_percentage: 3,
    equity_curve: [{ date: '2026-08-27', bankroll: 1000, benchmark: 1000 }],
    monthly: [],
    daily: [{
      date: '2026-08-27', positions: 0, settled: 0, amount_risked: 0,
      profit_loss: 0, bankroll_after: 1000, no_bet: true,
      no_bet_reasons: ['No complete fresh two-sided quote'],
    }],
    positions: [],
    open_positions: 0,
    settled_positions: 0,
    no_bet_days: 1,
    closing_quote_coverage: { positions_with_closing_quote: 0, settled_positions: 0, percentage: null },
    betting: collectingBetting,
  }
}

describe('BankrollPage prospective experiment states', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders an active zero-position experiment as collecting data, not as unopened', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(activePayload())))

    render(<MemoryRouter><BankrollPage /></MemoryRouter>)

    expect(await screen.findByText('VIRTUAL RESEARCH EXPERIMENT')).toBeInTheDocument()
    expect(screen.getByText('NO REAL WAGERS')).toBeInTheDocument()
    expect(screen.getAllByText('ASP MODEL BANKROLL — 2026 V1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('asp-model-bankroll-2026-v1').length).toBeGreaterThan(0)
    expect(screen.getByText('ACTIVE · COLLECTING DATA')).toBeInTheDocument()
    expect(screen.queryByText('The official experiment has not started')).not.toBeInTheDocument()
    expect(screen.getByText('No complete fresh two-sided quote')).toBeInTheDocument()
    expect(screen.getByText('No virtual positions have been frozen. A zero-position day is a valid experiment result.')).toBeInTheDocument()
    expect(screen.getByText('No legitimate closing sample')).toBeInTheDocument()
  })

  it('shows honest empty open/settled states in the top KPI section', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(activePayload())))

    render(<MemoryRouter><BankrollPage /></MemoryRouter>)

    const openCard = (await screen.findByText('Open positions')).closest('article') as HTMLElement
    expect(within(openCard).getByText('0')).toBeInTheDocument()
    expect(within(openCard).getByText('No open virtual positions')).toBeInTheDocument()
    const settledCard = screen.getByText('Settled positions').closest('article') as HTMLElement
    expect(within(settledCard).getByText('0')).toBeInTheDocument()
    expect(within(settledCard).getByText('No settled positions yet')).toBeInTheDocument()
  })

  it('puts bankroll, open, and settled positions above the qualification funnel and policy sections', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(activePayload())))

    const { container } = render(<MemoryRouter><BankrollPage /></MemoryRouter>)
    await screen.findByText('Current bankroll')

    const text = container.textContent ?? ''
    const bankrollAt = text.indexOf('Current bankroll')
    const openAt = text.indexOf('Open positions')
    const settledAt = text.indexOf('Settled positions')
    const funnelAt = text.indexOf('Qualification funnel')
    const policyAt = text.indexOf('Staking policy')
    const identityAt = text.indexOf('Experiment identity')

    expect(bankrollAt).toBeGreaterThanOrEqual(0)
    expect(openAt).toBeGreaterThanOrEqual(0)
    expect(settledAt).toBeGreaterThanOrEqual(0)
    // Funnel may be absent when its endpoint returns no funnel payload, but the
    // policy/identity sections must always trail the operational results.
    if (funnelAt >= 0) {
      expect(bankrollAt).toBeLessThan(funnelAt)
      expect(openAt).toBeLessThan(funnelAt)
      expect(settledAt).toBeLessThan(funnelAt)
      expect(funnelAt).toBeLessThan(policyAt)
    }
    expect(bankrollAt).toBeLessThan(policyAt)
    expect(settledAt).toBeLessThan(policyAt)
    expect(policyAt).toBeLessThan(identityAt)
  })

  it('shows ROI and CLV only after a legitimately closed settlement exists', async () => {
    const payload = activePayload()
    payload.data_mode = 'live'
    payload.current_bankroll = 1008
    payload.peak_bankroll = 1008
    payload.total_return = 8
    payload.roi = 80
    payload.positions_count = 1
    payload.open_positions = 0
    payload.settled_positions = 1
    payload.record = '1–0–0'
    payload.monthly = [{ month: '2026-08', positions: 1, record: '1–0–0', profit_loss: 8, roi: 80 }]
    payload.positions = [{ id: 'p1', date: '2026-08-27', game_id: 'g1', selection: 'Home ML', market: 'Moneyline', price: -125, stake: 10, result: 'WIN', profit_loss: 8, closing_price: -135, clv: 1.5 }]
    payload.closing_quote_coverage = { positions_with_closing_quote: 1, settled_positions: 1, percentage: 100 }
    payload.betting = { ...collectingBetting, current_bankroll: 1008, net_profit_loss: 8, total_risked: 10, roi: 80, yield_pct: 80, positions: 1, settled: 1, wins: 1, record: '1–0–0', closing_line_value: 1.5, beat_close_percentage: 100, has_settled: true }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)))

    render(<MemoryRouter><BankrollPage /></MemoryRouter>)

    const returnCard = (await screen.findByText('ROI on risked capital')).closest('article') as HTMLElement
    expect(within(returnCard).getByText('+80.0%')).toBeInTheDocument()
    expect(within(returnCard).getByText(/Net P\/L ÷ \$10\.00 settled stake — open exposure excluded, never ÷ bankroll/)).toBeInTheDocument()
    const clvCard = screen.getByText('CLV sample').closest('article') as HTMLElement
    expect(within(clvCard).getByText('+1.5%')).toBeInTheDocument()
    expect(within(clvCard).getByText(/1 of 1 settled with a closing quote/)).toBeInTheDocument()
    expect(screen.queryByText('ACTIVE · COLLECTING DATA')).not.toBeInTheDocument()
    // The settled position renders in the immutable ledger.
    expect(screen.getByText('Every position')).toBeInTheDocument()
    expect(screen.getByText('NO CHERRY-PICKING · IMMUTABLE POSITION HISTORY')).toBeInTheDocument()
  })
})
