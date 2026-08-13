import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { demoGames } from '../data/demo'
import { ForecastCard } from './ForecastCard'

describe('ForecastCard missing-data integrity', () => {
  it('shows the persisted lifecycle, countdown, lineups, and refresh cadence', () => {
    render(<MemoryRouter><ForecastCard game={demoGames[0]} /></MemoryRouter>)

    expect(screen.getByText('T−60m')).toBeInTheDocument()
    expect(screen.getByText('33m to first pitch')).toBeInTheDocument()
    expect(screen.getByText(/Lineups: NYM Projected · complete · ATL Confirmed · complete/)).toBeInTheDocument()
    expect(screen.getByText(/Data as of Illustrative snapshot · 6:42 PM/)).toBeInTheDocument()
    expect(screen.getByText(/Last refresh Illustrative source refresh · 6:40 PM/)).toBeInTheDocument()
  })

  it('uses explicit unavailable copy instead of fabricated zero projections', () => {
    const unavailableGame = {
      ...demoGames[0],
      status: 'SCHEDULED · FORECAST UNAVAILABLE',
      forecast_available: false,
      market_available: false,
      projected_away_runs: null,
      projected_home_runs: null,
      projected_total: null,
      market_total: null,
      away_win_probability: null,
      home_win_probability: null,
      fair_away_moneyline: null,
      fair_home_moneyline: null,
      best_away_moneyline: null,
      best_home_moneyline: null,
      market_home_probability: null,
      model_edge: null,
      edge_side: 'none' as const,
      opportunity: 'NO BET' as const,
      confidence: null,
      data_readiness: 42,
      books_reporting: 0,
    }

    render(<MemoryRouter><ForecastCard game={unavailableGame} /></MemoryRouter>)

    expect(screen.getByText('Forecast awaiting required inputs')).toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(4)
    expect(screen.queryByText('0.0')).not.toBeInTheDocument()
    expect(screen.getByText('NO BET')).toBeInTheDocument()
  })
})
