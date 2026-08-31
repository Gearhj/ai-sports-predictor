import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { MarketPulsePage } from './MarketPulsePage'
import { demoMarketPulse } from '../data/demo'

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('MarketPulsePage', () => {
  it('shows the research disclosure and no-trading framing', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ...demoMarketPulse, data_mode: 'live' })))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await screen.findByRole('heading', { name: /Crowd & Market Pulse/i })
    expect(screen.getAllByText(/NO TRADING/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/never change a forecast, ranking, recommendation, or the \$1,000 experiment/i)).toBeInTheDocument()
  })

  it('renders the cross-market probabilities for a mapped game', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ...demoMarketPulse, data_mode: 'live' })))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await screen.findByText('Cross-market probability board')
    // Polymarket 0.60 -> 60.0% ; consensus 0.595 -> 59.5%
    expect(screen.getAllByText('60.0%').length).toBeGreaterThan(0)
    // one-provider game shows honest "No consensus", never a fabricated 0
    expect(screen.getAllByText(/No consensus/i).length).toBeGreaterThan(0)
  })

  it('shows an honest empty state when no governed data exists', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ data_mode: 'empty', date: '2026-08-30', sport: null, sports_available: [], disclosure: '', public_display_enabled: false, rows: [], summary: {} })))
    render(<MemoryRouter><MarketPulsePage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText(/No governed prediction-market evidence yet/i)).toBeInTheDocument())
  })
})
