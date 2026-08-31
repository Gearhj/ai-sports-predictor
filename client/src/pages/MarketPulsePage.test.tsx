import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { MarketPulsePage } from './MarketPulsePage'
import { demoMarketPulse } from '../data/demo'
import type { MarketPulseBoard } from '../types/domain'

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response
}

const emptyBoardBase: MarketPulseBoard = {
  data_mode: 'empty', date: '2026-08-30', sport: null, sports_available: [],
  disclosure: 'RESEARCH OBSERVER · NO TRADING · NOT A MODEL INPUT · NOT A GUARANTEE',
  public_display_enabled: true, observer_enabled: true, board_state: 'PROVIDERS_HEALTHY_ZERO_MARKETS',
  state_reason: 'Providers reachable but no semantically comparable per-game market is currently available.',
  providers: [
    { provider: 'POLYMARKET', enabled: true, connection_mode: 'REST_ONLY', last_discovery_at: null, last_snapshot_at: null, subscribed_markets: 0, last_error: null, status: 'ARMED', events_inspected: 12, markets_inspected: 8, eligible_game_markets: 0, mapped_markets: 0, unresolved_markets: 3, last_job_status: 'succeeded' },
    { provider: 'KALSHI', enabled: true, connection_mode: 'REST_ONLY', last_discovery_at: null, last_snapshot_at: null, subscribed_markets: 0, last_error: null, status: 'ARMED', events_inspected: 4, markets_inspected: 2, eligible_game_markets: 0, mapped_markets: 0, unresolved_markets: 1, last_job_status: 'succeeded' },
  ],
  next_discovery_at: null, semantics_review_count: 0, rows: [], summary: {},
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('MarketPulsePage', () => {
  it('renders the board and no-trading framing when eligible markets exist', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ...demoMarketPulse, data_mode: 'live' })))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await screen.findByText('Cross-market probability board')
    expect(screen.getAllByText(/NO TRADING/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText('60.0%').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/No consensus/i).length).toBeGreaterThan(0)
  })

  it('shows a HEALTHY zero-market state (not "API unavailable") with provider details', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(emptyBoardBase)))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await screen.findByText(/Providers healthy — zero eligible markets/i)
    expect(screen.queryByText(/API unavailable/i)).not.toBeInTheDocument()
    expect(screen.getByText(/No demo data is substituted/i)).toBeInTheDocument()
    expect(screen.getAllByText('POLYMARKET').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
  })

  it('shows the OBSERVER DISABLED state distinctly', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ...emptyBoardBase, observer_enabled: false, board_state: 'OBSERVER_DISABLED', state_reason: 'The observer feature flag is off.' })))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await screen.findByRole('heading', { name: /Observer disabled/i })
    expect(screen.queryByText(/API unavailable/i)).not.toBeInTheDocument()
  })

  it('shows PROVIDER DEGRADED when a provider job failed', async () => {
    const degraded = { ...emptyBoardBase, board_state: 'PROVIDER_DEGRADED' as const, state_reason: 'KALSHI last job failed', providers: [{ ...emptyBoardBase.providers[1], last_job_status: 'failed', last_error: 'HTTP 500' }] }
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(degraded)))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await screen.findByText(/Provider degraded/i)
  })

  it('shows API UNAVAILABLE only when the backend itself is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline') }))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByRole('heading', { name: /API unavailable/i })).toBeInTheDocument())
    expect(screen.getAllByRole('button', { name: /Retry/i }).length).toBeGreaterThan(0)
  })
})
