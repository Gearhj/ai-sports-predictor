import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DiamondTheater } from './DiamondTheater'
import type { GameSimulationData, RepresentativeOutcome } from '../types/simulation'

/** A joint distribution whose counts sum to exactly 20,000. */
function joint(size = 10) {
  const counts = Array.from({ length: size }, () => Array.from({ length: size }, () => 0))
  const probabilities = Array.from({ length: size }, () => Array.from({ length: size }, () => 0))
  const cells: Array<[number, number, number]> = [
    [5, 4, 6000], [4, 5, 5200], [6, 3, 2600], [3, 6, 2400],
    [8, 7, 1900], [1, 0, 1900],
  ]
  for (const [h, a, c] of cells) counts[h][a] = c
  for (let h = 0; h < size; h++) for (let a = 0; a < size; a++) probabilities[h][a] = counts[h][a] / 20000
  const labels = Array.from({ length: size }, (_, i) => String(i))
  return { home_labels: labels, away_labels: labels, probabilities, counts, cap: size - 2, note: '' }
}

function outcome(overrides: Partial<RepresentativeOutcome> = {}): RepresentativeOutcome {
  return {
    key: 'typical', available: true, criterion: 'Most frequent exact score', reason: null,
    home_runs: 5, away_runs: 4, total_runs: 9, winner: 'home', went_extra_innings: false,
    draw_index: 0, share_of_simulations: 0.3, matching_simulations: 6000,
    share_within_criterion: 0.3, criterion_simulations: 20000, ...overrides,
  }
}

function simulation(overrides: Partial<GameSimulationData> = {}): GameSimulationData {
  return {
    data_mode: 'live', simulation_available: true, unavailable_reason: null,
    identity: {
      forecast_id: 'f1', forecast_version: 3, forecast_track: 'CURRENT_DEVELOPMENT',
      lifecycle: 'PREGAME', generated_at: '2026-09-06T17:22:47Z',
      available_as_of: '2026-09-06T17:22:47Z', model_name: 'asp_mlb_ensemble',
      model_version: '2.0.0', model_status: 'DEVELOPMENT', feature_snapshot_reference: 'sha256:x',
      development_model: true, development_note: 'Gen 1 is a development model.',
    },
    play_by_play: {
      available: false,
      reason: 'Play-by-play simulation is unavailable for this forecast.',
      what_is_shown_instead: 'Representative final outcomes.',
    },
    official_win_probability: { home: 0.479, away: 0.521 },
    simulation_win_probability: { home: 0.489, away: 0.511 },
    inputs: {
      expected_home_runs: 4.46, expected_away_runs: 4.55,
      simulations: 20000, seed: 20250601, simulator: 'independent-Poisson',
    },
    means: { home_runs: 4.46, away_runs: 4.55, total_runs: 9.02, run_difference: -0.09 },
    extra_inning_probability: 0.1324,
    sampling_uncertainty: {
      standard_error: 0.0035, confidence_95_low: 0.472, confidence_95_high: 0.486,
      simulations: 20000, note: 'Monte Carlo sampling error only.',
    },
    replay_validation: { exact: true, max_absolute_delta: 0, compared_fields: 60, tolerance: 1e-9, note: '' },
    joint_score_distribution: joint(),
    representative_outcomes: [outcome()],
    ...overrides,
  }
}

function matchMedia(reduced: boolean) {
  return vi.fn().mockImplementation((query: string) => ({
    matches: reduced && query.includes('prefers-reduced-motion'),
    media: query, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }))
}

function mount(data = simulation()) {
  return render(<DiamondTheater simulation={data} homeTeam="BAL" awayTeam="BOS" />)
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('DiamondTheater — population, not a playlist', () => {
  beforeEach(() => { vi.stubGlobal('matchMedia', matchMedia(false)) })

  it('states that the outcomes are visualized together', () => {
    mount()
    expect(screen.getByText('20,000 completed game outcomes visualized together.')).toBeInTheDocument()
  })

  it('never presents a per-draw playlist', () => {
    mount()
    expect(screen.queryByText(/draw #?\d+ of 20,000/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /next outcome|previous outcome/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^play$/i })).not.toBeInTheDocument()
  })

  it('says the animation reveals an already-computed result', () => {
    mount()
    expect(screen.getByText(/performs\s+no simulation/i)).toBeInTheDocument()
    expect(screen.getByText(/completed and verified before rendering/i)).toBeInTheDocument()
  })

  it('describes traces as final scores, never ball flights or baserunners', () => {
    mount()
    expect(screen.getByText(/not a ball\s*flight, hit or baserunner path/i)).toBeInTheDocument()
    expect(screen.queryByText(/runner on|bases loaded|plate appearance/i)).not.toBeInTheDocument()
  })

  it('claims individual drawing only when every outcome is drawn', () => {
    mount()
    expect(screen.getByText(/All 20,000 outcomes are drawn individually/)).toBeInTheDocument()
  })

  it('offers a replay control rather than step controls', async () => {
    const user = userEvent.setup()
    mount()
    const replay = screen.getByRole('button', { name: /replay build/i })
    await user.click(replay)
    expect(replay).toBeInTheDocument()
  })

  it('exposes 1x and 2x build speeds only', () => {
    mount()
    const select = screen.getByLabelText(/speed/i)
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual(['1×', '2×'])
  })
})

describe('DiamondTheater — statistics around the field', () => {
  beforeEach(() => { vi.stubGlobal('matchMedia', matchMedia(false)) })

  it('shows both teams, their win probabilities and expected runs', () => {
    mount()
    expect(screen.getByText('BAL')).toBeInTheDocument()
    expect(screen.getByText('BOS')).toBeInTheDocument()
    expect(screen.getByText('47.9%')).toBeInTheDocument()
    expect(screen.getByText('52.1%')).toBeInTheDocument()
    expect(screen.getByText('4.46 expected runs')).toBeInTheDocument()
  })

  it('shows the extra-innings probability and the most likely score', () => {
    mount()
    expect(screen.getByText('13.2%')).toBeInTheDocument()
    expect(screen.getByText('BOS 4 — BAL 5')).toBeInTheDocument()
  })

  it('reports the outcome counter and the verified replay indicator', () => {
    mount()
    expect(screen.getByText(/\/ 20,000/)).toBeInTheDocument()
    expect(screen.getByText(/replay verified/)).toBeInTheDocument()
  })

  it('renders a legend for every encoding channel', () => {
    mount()
    const legend = screen.getByLabelText('How to read the field')
    expect(within(legend).getByText(/BOS wins — traces bend toward left field/)).toBeInTheDocument()
    expect(within(legend).getByText(/BAL wins — traces bend toward right field/)).toBeInTheDocument()
    expect(within(legend).getByText('Depth = total runs')).toBeInTheDocument()
    expect(within(legend).getByText('Angle = run differential')).toBeInTheDocument()
    expect(within(legend).getByText(/Density =/)).toBeInTheDocument()
  })
})

describe('DiamondTheater — filtering', () => {
  beforeEach(() => { vi.stubGlobal('matchMedia', matchMedia(false)) })

  it('offers the outcome subsets with their counts', () => {
    mount()
    const group = screen.getByRole('group', { name: 'Filter outcomes' })
    for (const label of ['All outcomes', 'Typical', 'Home wins', 'Away wins', 'High scoring', 'Low scoring', 'Extra innings']) {
      expect(within(group).getByRole('button', { name: new RegExp(label) })).toBeInTheDocument()
    }
  })

  it('reports the subset count and percentage when a filter is chosen', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: /Home wins/ }))
    const summary = screen.getByRole('status', { name: '' }) ?? screen.getByText(/Home wins:/)
    expect(screen.getByText(/Home wins:/)).toBeInTheDocument()
    // 6000 + 2600 + 1900 + 1900 home-team wins out of 20,000
    expect(screen.getByText(/12,400 of 20,000 outcomes \(62\.0%\)/)).toBeInTheDocument()
    expect(summary).toBeTruthy()
  })

  it('describes the selected subset, not the whole population', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: /Away wins/ }))
    expect(screen.getByText(/BAL wins 0\.0% of this subset/)).toBeInTheDocument()
  })

  it('qualifies the extra-innings subset honestly', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(screen.getByRole('button', { name: /Extra innings/ }))
    expect(screen.getByText(/also includes one-run games decided in regulation/i)).toBeInTheDocument()
  })

  it('marks the active filter with aria-pressed', async () => {
    const user = userEvent.setup()
    mount()
    const button = screen.getByRole('button', { name: /High scoring/ })
    await user.click(button)
    expect(button).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /All outcomes/ })).toHaveAttribute('aria-pressed', 'false')
  })

  it('keeps filters keyboard operable', async () => {
    const user = userEvent.setup()
    mount()
    const button = screen.getByRole('button', { name: /Low scoring/ })
    button.focus()
    await user.keyboard('{Enter}')
    expect(button).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('DiamondTheater — reduced motion', () => {
  beforeEach(() => { vi.stubGlobal('matchMedia', matchMedia(true)) })

  it('renders the completed field immediately and says so', async () => {
    mount()
    await waitFor(() => {
      expect(screen.getByText(/simulated outcomes shown/)).toBeInTheDocument()
    })
    expect(screen.getByText(/completed field is rendered immediately/i)).toBeInTheDocument()
  })

  it('disables the build-speed control', () => {
    mount()
    expect(screen.getByLabelText(/speed/i)).toBeDisabled()
  })
})

describe('DiamondTheater — missing evidence', () => {
  beforeEach(() => { vi.stubGlobal('matchMedia', matchMedia(false)) })

  it('reports honestly when no joint distribution exists', () => {
    mount(simulation({ joint_score_distribution: undefined }))
    expect(screen.getByText(/No joint outcome distribution is available/i)).toBeInTheDocument()
    expect(screen.queryByLabelText('Filter outcomes')).not.toBeInTheDocument()
  })

  it('reports honestly when the distribution is empty', () => {
    const zeros = Array.from({ length: 4 }, () => [0, 0, 0, 0])
    mount(simulation({
      joint_score_distribution: {
        home_labels: ['0', '1', '2', '>2'], away_labels: ['0', '1', '2', '>2'],
        probabilities: zeros, counts: zeros, cap: 2, note: '',
      },
    }))
    expect(screen.getByText(/No joint outcome distribution is available/i)).toBeInTheDocument()
  })
})
