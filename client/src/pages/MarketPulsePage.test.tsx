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
  public_display_enabled: true, observer_enabled: true, board_state: 'PROVIDERS_HEALTHY_ZERO_ELIGIBLE',
  state_reason: 'Discovery succeeded and inspected 240 raw provider market(s), but no governed per-game market qualified.',
  providers: [
    { provider: 'POLYMARKET', enabled: true, connection_mode: 'REST_ONLY', last_discovery_at: '2026-08-30T20:00:00Z', last_success_at: '2026-08-30T20:00:00Z', last_snapshot_at: null, subscribed_markets: 0, last_error: null, status: 'ARMED', events_inspected: 12, markets_inspected: 8, raw_markets_inspected: 200, excluded: { player_props: 120, futures_or_season: 40 }, eligible_game_markets: 0, mapped_markets: 0, unresolved_markets: 3, last_job_status: 'succeeded' },
    { provider: 'KALSHI', enabled: true, connection_mode: 'REST_ONLY', last_discovery_at: '2026-08-30T20:00:00Z', last_success_at: '2026-08-30T20:00:00Z', last_snapshot_at: null, subscribed_markets: 0, last_error: null, status: 'ARMED', events_inspected: 4, markets_inspected: 2, raw_markets_inspected: 40, excluded: { missing_identifiers: 2 }, eligible_game_markets: 0, mapped_markets: 0, unresolved_markets: 1, last_job_status: 'succeeded' },
  ],
  next_discovery_at: '2026-08-30T21:00:00Z', semantics_review_count: 0, rows: [], summary: {},
  excluded_summary: { player_props: 120, futures_or_season: 40, missing_identifiers: 2, unresolved_mappings: 4 },
  jobs: [{
    name: 'discover-prediction-markets', enabled: true, cadence_minutes: 60, scheduler_running: true,
    last_run_at: '2026-08-30T20:00:00Z', last_status: 'succeeded', last_success_at: '2026-08-30T20:00:00Z',
    last_error: null, next_run_at: '2026-08-30T21:00:00Z', records_inspected: 240, records_persisted: 12,
  }],
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
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

  it('requests the Eastern operational date, not the UTC calendar date', async () => {
    // 2026-08-30 22:45 America/New_York == 2026-08-31 02:45 UTC.
    vi.useFakeTimers({ now: new Date('2026-08-31T02:45:00Z'), toFake: ['Date'] })
    const fetchMock = vi.fn(async () => jsonResponse(emptyBoardBase))
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const requested = String((fetchMock.mock.calls[0] as unknown[])[0])
    expect(requested).toContain('date=2026-08-30')
    expect(requested).not.toContain('date=2026-08-31')
  })

  it('shows a HEALTHY zero-eligible state (not "API unavailable") with raw inspection evidence', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(emptyBoardBase)))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await screen.findByText(/Providers healthy — zero eligible markets/i)
    expect(screen.queryByText(/API unavailable/i)).not.toBeInTheDocument()
    expect(screen.getByText(/No demo data is substituted/i)).toBeInTheDocument()
    expect(screen.getAllByText('POLYMARKET').length).toBeGreaterThan(0)
    // Raw inspected counts and excluded-market activity are visible evidence.
    expect(screen.getByText('200')).toBeInTheDocument()
    expect(screen.getByText(/Excluded market activity/i)).toBeInTheDocument()
    expect(screen.getByText('Player props')).toBeInTheDocument()
    // Registered-job evidence with a next run time.
    expect(screen.getByText('discover-prediction-markets')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
    // The date selector stays reachable in a zero-market state.
    expect(screen.getByRole('group', { name: /operating date/i })).toBeInTheDocument()
  })

  it('shows the OBSERVER DISABLED state distinctly', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ...emptyBoardBase, observer_enabled: false, board_state: 'OBSERVER_DISABLED', state_reason: 'The observer feature flag is off.' })))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await screen.findByRole('heading', { name: /Observer disabled/i })
    expect(screen.queryByText(/API unavailable/i)).not.toBeInTheDocument()
  })

  it('shows PROVIDERS DISABLED when the observer is on but no provider is enabled', async () => {
    const disabled = {
      ...emptyBoardBase,
      board_state: 'PROVIDERS_DISABLED' as const,
      state_reason: 'The observer is enabled but every provider is disabled.',
      providers: emptyBoardBase.providers.map((p) => ({ ...p, enabled: false })),
    }
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(disabled)))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await screen.findByRole('heading', { name: /Providers disabled/i })
    expect(screen.queryByText(/Providers healthy/i)).not.toBeInTheDocument()
  })

  it('shows DISCOVERY PENDING before the first completed discovery job', async () => {
    const pending = {
      ...emptyBoardBase,
      board_state: 'DISCOVERY_PENDING' as const,
      state_reason: 'Providers are enabled but no discovery job has completed yet.',
      providers: emptyBoardBase.providers.map((p) => ({ ...p, last_discovery_at: null, last_success_at: null, raw_markets_inspected: null, excluded: {} })),
    }
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(pending)))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await screen.findByRole('heading', { name: /Discovery pending/i })
    expect(screen.queryByText(/Providers healthy — zero eligible markets/i)).not.toBeInTheDocument()
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
