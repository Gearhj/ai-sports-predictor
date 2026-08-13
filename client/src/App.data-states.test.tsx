import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { demoBankroll, demoDashboard, demoHealth, demoPerformance } from './data/demo'

vi.mock('echarts-for-react', () => ({
  default: ({ style }: { style?: Record<string, unknown> }) => <div data-testid="echarts-placeholder" style={style} />,
}))

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  } as Response
}

describe('route-level data provenance and availability', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    window.history.pushState({}, '', '/')
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('shows LIVE DATA only when the API explicitly reports live provenance', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ...demoDashboard, data_mode: 'live' })))
    render(<App />)

    expect(await screen.findByText('LIVE DATA')).toBeInTheDocument()
    expect(screen.queryByText(/DEMO DATA/i)).not.toBeInTheDocument()
  })

  it('keeps an explicitly demo API unmistakably labeled', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ...demoDashboard, data_mode: 'demo' })))
    render(<App />)

    expect((await screen.findAllByText('ILLUSTRATIVE DEMO — NOT LIVE RESULTS')).length).toBeGreaterThan(0)
    expect(screen.getByText(/Static examples for product exploration/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry/i })).not.toBeInTheDocument()
  })

  it('shows unavailable and never falls back when the API is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    render(<App />)

    expect(await screen.findByText(/LIVE DATA UNAVAILABLE/i)).toBeInTheDocument()
    expect(screen.queryByText('ILLUSTRATIVE DEMO — NOT LIVE RESULTS')).not.toBeInTheDocument()
    expect(screen.queryByText(/Mets/i)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
  })

  it('renders unavailable performance metrics as N/A instead of perfect zeros', async () => {
    window.history.pushState({}, '', '/performance')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      ...demoPerformance,
      data_mode: 'live',
      evaluated_games: 0,
      brier_score: null,
      log_loss: null,
      accuracy: null,
      run_mae: null,
      total_mae: null,
      versus_market_brier: null,
      calibration: [],
      rolling: [],
      model_components: [],
      by_confidence: [],
    })))
    render(<App />)

    expect(await screen.findByRole('heading', { level: 1, name: /Model Performance/i })).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText('N/A').length).toBeGreaterThanOrEqual(5))
    expect(screen.getAllByText(/Awaiting settled sample/i).length).toBeGreaterThan(0)
  })

  it('shows the exact unopened $1,000 experiment state', async () => {
    window.history.pushState({}, '', '/experiment')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      ...demoBankroll,
      data_mode: 'empty',
      starting_bankroll: 1000,
      current_bankroll: 1000,
      positions_count: 0,
      experiment_status: 'NOT_STARTED',
      positions: [],
      equity_curve: [],
      monthly: [],
    })))
    render(<App />)

    expect((await screen.findAllByText('$1,000')).length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('Official positions')).toBeInTheDocument()
    expect(screen.getByText('No positions published')).toBeInTheDocument()
    expect(screen.getByText('The official experiment has not started')).toBeInTheDocument()
  })

  it('retains database and provider evidence when sports data is empty', async () => {
    window.history.pushState({}, '', '/admin')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      ...demoHealth,
      data_mode: 'empty',
      database_status: 'ready',
      sports_data_status: 'empty',
      provider_evidence_status: 'none',
      games_discovered: 0,
      missing_games: null,
      recent_jobs: [],
    })))
    render(<App />)

    expect(await screen.findByText('LIVE DATA · NO PERSISTED RESULTS')).toBeInTheDocument()
    expect(screen.getByText('READY')).toBeInTheDocument()
    expect(screen.getByText('EMPTY')).toBeInTheDocument()
    expect(screen.getByText('NONE')).toBeInTheDocument()
    expect(screen.getByText('No pipeline jobs recorded.')).toBeInTheDocument()
    expect(screen.getByText('Unmatched-game count unavailable')).toBeInTheDocument()
  })
})
