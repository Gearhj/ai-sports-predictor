/**
 * The acceptance criterion, as tests: a reader can tell the model's average
 * apart from the animated example without opening anything.
 *
 * The real COL-DET case is the fixture. V18's forecast was 4.08 to 4.81 runs;
 * the one retained simulated game ended 12-7 after an eight-run 11th. Both were
 * on screen and only the 12-7 moved, so the animation read as the forecast.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SimulationAverageHeader, centralTotalRunRange } from './SimulationAverageHeader'
import type { ShadowForecastView } from '../../types/shadow'

const DISTRIBUTION = [
  { value: 4, probability: 0.05 },
  { value: 6, probability: 0.15 },
  { value: 8, probability: 0.30 },
  { value: 10, probability: 0.30 },
  { value: 12, probability: 0.15 },
  { value: 16, probability: 0.05 },
]

const SHADOW = {
  track: 'RESEARCH_SHADOW',
  engine_id: 'MLB_V18_WHOLE_GAME',
  label: 'V18 whole-game challenger (research shadow)',
  forecast_id: 'mlb-v18-daily-21239787e7773ffa160e98251cd999d427f1b9d99875f7f29b07d68f425c81f4',
  bundle_id: 'b', bundle_sha256: null, evaluation_contract: null,
  home_win_probability: 60.24,
  expected_home_runs: 4.8148, expected_away_runs: 4.08,
  generated_at: '2026-09-12T15:25:25Z',
  evidence_cutoff: '2026-09-12T15:18:06Z',
  pregame: true, simulation_count: 5000, master_seed: 20260907,
  monte_carlo_standard_error: null, monte_carlo_95_half_width: null,
  deterministic_replay_verified: true, illegal_state_count: 0,
  nontermination_count: 0, path_digest: 'ac1f1d84', sequential_path_events: 96,
  lineup_modes: null, sides: null, limitations: null, wagering_authority: false,
  population: {
    simulation_count: 5000,
    // Percent units, exactly as the population payload sends them.
    home_win_probability: 60.24, away_win_probability: 39.76,
    expected_home_runs: 4.8148, expected_away_runs: 4.08,
    expected_total_runs: 8.89, expected_run_differential: 0.73,
    one_run_probability: 0.286, extra_inning_probability: 0.097,
    pitching_change_rate_home: null, pitching_change_rate_away: null,
    monte_carlo_standard_error: 0.0069, monte_carlo_95_half_width: null,
    total_run_distribution: DISTRIBUTION,
  },
  settlement: null, settlement_state: 'PENDING', settlement_note: '',
  paired_with_official: false, pairing_note: null, difference_vs_official: null,
  difference_orientation: 'V18_MINUS_GEN1', simulation_indistinguishable: null,
} as unknown as ShadowForecastView

function mount(overrides: Partial<Parameters<typeof SimulationAverageHeader>[0]> = {}) {
  return render(
    <SimulationAverageHeader
      shadow={SHADOW} awayTeam="COL" homeTeam="DET"
      readAt="2026-09-13T16:00:00Z" {...overrides}
    />,
  )
}

describe('the forecast comes first', () => {
  afterEach(cleanup)

  it('labels the expected runs as the simulation average, not a score', () => {
    mount()
    const header = screen.getByTestId('simulation-average-header')
    expect(header).toHaveTextContent('Simulation average')
    expect(header).toHaveTextContent('4.08')
    expect(header).toHaveTextContent('4.81')
    expect(screen.getAllByText('Simulation average runs')).toHaveLength(2)
  })

  it('shows win probabilities and the actual simulation count', () => {
    mount()
    const header = screen.getByTestId('simulation-average-header')
    expect(header).toHaveTextContent('39.8%')
    expect(header).toHaveTextContent('60.2%')
    expect(header).toHaveTextContent('5,000')
    expect(header).toHaveTextContent('mean of 5,000 simulated games')
  })

  it('publishes a score range only from the retained distribution', () => {
    mount()
    expect(screen.getByTestId('simulation-average-header')).toHaveTextContent('6–12')
  })

  it('says the range is not published when no distribution was retained', () => {
    mount({
      shadow: {
        ...SHADOW,
        population: { ...SHADOW.population!, total_run_distribution: null },
      } as ShadowForecastView,
    })
    // Absent, never invented from the mean or a standard error.
    expect(screen.getByTestId('simulation-average-header')).toHaveTextContent('not published')
  })

  it('binds to the same forecast identity as the replay', () => {
    mount()
    expect(screen.getByTestId('simulation-average-header')).toHaveTextContent(
      'mlb-v18-daily-21239787e7773ffa160e98251cd999d427f1b9d99875f7f29b07d68f425c81f4')
  })

  it('keeps the forecast clock and the page clock apart', () => {
    mount()
    const header = screen.getByTestId('simulation-average-header')
    expect(header).toHaveTextContent(/evidence to/)
    expect(header).toHaveTextContent(/page read/)
  })

  it('says the averages hold still while the example plays', () => {
    mount({ playing: true })
    expect(screen.getByTestId('simulation-average-header'))
      .toHaveTextContent('They do not change while the animation below plays')
  })
})

describe('the central range reads the mass function', () => {
  it('returns the middle 80% of retained total runs', () => {
    expect(centralTotalRunRange(DISTRIBUTION)).toEqual({ low: 6, high: 12, coverage: 0.8 })
  })

  it('returns nothing when no distribution was retained', () => {
    expect(centralTotalRunRange(null)).toBeNull()
    expect(centralTotalRunRange([])).toBeNull()
  })

  it('normalises a distribution whose mass does not sum to one', () => {
    const half = DISTRIBUTION.map((bin) => ({ ...bin, probability: bin.probability / 2 }))
    expect(centralTotalRunRange(half)).toEqual({ low: 6, high: 12, coverage: 0.8 })
  })
})
