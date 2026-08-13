import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { demoGames } from '../data/demo'
import { ForecastCard } from './ForecastCard'

describe('ForecastCard missing-data integrity', () => {
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
