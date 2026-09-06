import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DiamondTheater } from './DiamondTheater'
import type { GameSimulationData, RepresentativeOutcome } from '../types/simulation'

function outcome(overrides: Partial<RepresentativeOutcome> = {}): RepresentativeOutcome {
  return {
    key: 'typical', available: true, criterion: 'Most frequent exact score across all simulations',
    reason: null, home_runs: 4, away_runs: 5, total_runs: 9, winner: 'away',
    went_extra_innings: false, draw_index: 0, share_of_simulations: 0.0519,
    matching_simulations: 1038, share_within_criterion: 0.0519, criterion_simulations: 20000,
    ...overrides,
  }
}

function simulation(outcomes: RepresentativeOutcome[]): GameSimulationData {
  return {
    data_mode: 'live', simulation_available: true, unavailable_reason: null,
    identity: {
      forecast_id: 'f1', forecast_version: 2, forecast_track: 'CURRENT_DEVELOPMENT',
      lifecycle: 'PREGAME', generated_at: '2026-09-06T13:16:00Z',
      available_as_of: '2026-09-06T13:16:00Z', model_name: 'asp_mlb_ensemble',
      model_version: '2.0.0', model_status: 'DEVELOPMENT', feature_snapshot_reference: 'sha256:x',
      development_model: true, development_note: 'Gen 1 is a development model.',
    },
    play_by_play: {
      available: false,
      reason: 'Play-by-play simulation is unavailable for this forecast.',
      what_is_shown_instead: 'Representative final outcomes.',
    },
    official_win_probability: { home: 0.391, away: 0.609 },
    inputs: {
      expected_home_runs: 4.31, expected_away_runs: 4.96,
      simulations: 20000, seed: 20250601, simulator: 'independent-Poisson',
    },
    representative_outcomes: outcomes,
  }
}

function matchMedia(reduced: boolean) {
  return vi.fn().mockImplementation((query: string) => ({
    matches: reduced && query.includes('prefers-reduced-motion'),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('DiamondTheater', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', matchMedia(false))
  })

  it('carries the required representative-path disclaimer', () => {
    render(<DiamondTheater simulation={simulation([outcome()])} homeTeam="CIN" awayTeam="MIL" />)
    expect(screen.getByText(
      'Representative simulated path—not a prediction of the exact game sequence.',
    )).toBeInTheDocument()
  })

  it('shows the real simulated score and cites its draw index', () => {
    render(<DiamondTheater simulation={simulation([outcome()])} homeTeam="CIN" awayTeam="MIL" />)
    const scorebug = screen.getByRole('group', { name: /simulated final score/i })
    expect(within(scorebug).getByText('5')).toBeInTheDocument()
    expect(within(scorebug).getByText('4')).toBeInTheDocument()
    expect(screen.getByText(/Simulation draw #0 of 20,000/)).toBeInTheDocument()
  })

  it('never invents base runners, outs or batters', () => {
    render(<DiamondTheater simulation={simulation([outcome()])} homeTeam="CIN" awayTeam="MIL" />)
    expect(screen.getByText(/Base occupancy, outs and batters are not modelled/i)).toBeInTheDocument()
    expect(screen.queryByText(/runner on|bases loaded|strikeout|ground out/i)).not.toBeInTheDocument()
  })

  it('states plainly that play-by-play is unavailable', () => {
    render(<DiamondTheater simulation={simulation([outcome()])} homeTeam="CIN" awayTeam="MIL" />)
    expect(screen.getByText(/Play-by-play simulation is unavailable/i)).toBeInTheDocument()
  })

  it('selects a different representative outcome deterministically', async () => {
    const user = userEvent.setup()
    render(<DiamondTheater
      simulation={simulation([
        outcome(),
        outcome({ key: 'extra_innings', home_runs: 3, away_runs: 2, total_runs: 5, winner: 'home', went_extra_innings: true, draw_index: 111 }),
      ])}
      homeTeam="CIN" awayTeam="MIL"
    />)
    await user.click(screen.getByRole('button', { name: /Extra innings/ }))
    expect(screen.getByText(/Simulation draw #111/)).toBeInTheDocument()
    expect(screen.getByText('Final / extra innings')).toBeInTheDocument()
  })

  it('steps forward and back with keyboard-operable controls', async () => {
    const user = userEvent.setup()
    render(<DiamondTheater
      simulation={simulation([
        outcome(),
        outcome({ key: 'home_win', home_runs: 5, away_runs: 4, winner: 'home', draw_index: 7 }),
      ])}
      homeTeam="CIN" awayTeam="MIL"
    />)
    await user.click(screen.getByRole('button', { name: 'Next outcome' }))
    expect(screen.getByText(/Simulation draw #7/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Previous outcome' }))
    expect(screen.getByText(/Simulation draw #0/)).toBeInTheDocument()
  })

  it('omits an outcome the simulation could not supply rather than substituting one', () => {
    render(<DiamondTheater
      simulation={simulation([
        outcome(),
        outcome({ key: 'extra_innings', available: false, home_runs: null, away_runs: null, winner: null, draw_index: null }),
      ])}
      homeTeam="CIN" awayTeam="MIL"
    />)
    expect(screen.queryByRole('button', { name: /Extra innings/ })).not.toBeInTheDocument()
  })

  it('reports honestly when no representative outcome exists at all', () => {
    render(<DiamondTheater simulation={simulation([])} homeTeam="CIN" awayTeam="MIL" />)
    expect(screen.getByText(/No representative simulated outcome is available/i)).toBeInTheDocument()
  })

  describe('reduced motion', () => {
    beforeEach(() => { vi.stubGlobal('matchMedia', matchMedia(true)) })

    it('disables automatic playback and says so', () => {
      render(<DiamondTheater simulation={simulation([outcome()])} homeTeam="CIN" awayTeam="MIL" />)
      expect(screen.getByRole('button', { name: 'Play' })).toBeDisabled()
      expect(screen.getByText(/Reduced motion is on/i)).toBeInTheDocument()
    })

    it('keeps manual stepping available', async () => {
      const user = userEvent.setup()
      render(<DiamondTheater
        simulation={simulation([outcome(), outcome({ key: 'home_win', draw_index: 7 })])}
        homeTeam="CIN" awayTeam="MIL"
      />)
      const next = screen.getByRole('button', { name: 'Next outcome' })
      expect(next).not.toBeDisabled()
      await user.click(next)
      expect(screen.getByText(/Simulation draw #7/)).toBeInTheDocument()
    })
  })
})
