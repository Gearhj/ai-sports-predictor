/**
 * The population view's two load-bearing promises.
 *
 * 1. It never lets six games read as a thousand. The coverage line is not
 *    decoration — it is the difference between "here is a simulated season" and
 *    "here are 0.6% of the draws, drawn honestly".
 * 2. Its statistics move with the selection, and each rate keeps its own
 *    denominator. A completion rate computed over dropbacks, or a six-game
 *    sample wearing the thousand-game denominator, would be wrong in a way
 *    nobody would notice.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { SimulationPopulation } from './SimulationPopulation'
import type { PopulationPlay } from './populationGeometry'
import type { NflPlayPopulation, NflPlayScenario } from '../../types/nfl'

// This project does not enable vitest globals, so Testing Library's automatic
// cleanup never registers and rendered trees accumulate across tests in a file.
afterEach(cleanup)

// jsdom has no canvas; the component must still render its numbers.
beforeAll(() => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(HTMLCanvasElement.prototype as any).getContext = vi.fn(() => null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(globalThis as any).ResizeObserver ??= class {
    observe() {} unobserve() {} disconnect() {}
  }
})

function play(over: Partial<PopulationPlay> & { n: number }): PopulationPlay {
  return {
    g: 0, t: 'RUSH', poss: 'HOME', dir: 'LEFT', dn: 1, di: 10, yl: 50, q: 1,
    yds: 4, sc: null, scTeam: null, to: false,
    segs: [{ k: 'RUSH_GROUND', i: 'LINEAR', s: [50, 0.5], c: [], e: [46, 0.5] }],
    mk: [],
    ...over,
  }
}

/** Six completions, four rushes, one interception. Denominators differ on purpose. */
const PLAYS: PopulationPlay[] = [
  ...Array.from({ length: 6 }, (_, i) => play({ n: i + 1, t: 'PASS_COMPLETE', yds: 9 })),
  ...Array.from({ length: 4 }, (_, i) => play({ n: 20 + i, t: 'RUSH', yds: 3 })),
  play({ n: 40, t: 'INTERCEPTION', to: true, yds: 0, mk: [{ k: 'TURNOVER', p: [40, 0.5] }] }),
  // A sack is a dropback but NOT a pass attempt.
  play({ n: 41, t: 'SACK', yds: -7 }),
]

const POPULATION: NflPlayPopulation = {
  schema_version: 'AISP_NFL_PLAY_THEATER_READ_V1',
  event_id: '2026_01_NE_SEA',
  scenario_id: 'BASE',
  available: true,
  home_team: { abbreviation: 'SEA' },
  away_team: { abbreviation: 'NE' },
  coverage: {
    simulated_games: 1000,
    games_with_play_paths: 6,
    plays_drawable: PLAYS.length,
    plays_in_population: 152149,
    path_coverage_fraction: 0.006,
    statement: null,
  },
  population_accounting: {
    plays: 152149, games: 1000, completionRate: 0.6444, completionDenominator: 64579,
    turnoversPerGame: 2.157, scrimmageDenominator: 123313, pointsPerDrive: 1.951,
    drives: 20970, thirdDownAttempts: 25642, thirdDownConversionRate: 0.4042,
    playsPerGame: { count: 1000, mean: 152.149 },
    yardsPerScrimmagePlay: { count: 123313, mean: 5.317 },
    denominatorNotes: 'Scrimmage excludes kicks and tries.',
  },
  games: [
    { position: 0, label: 'Typical', simulation_index: 125, seed: 1, home_score: 20, away_score: 20, reached_overtime: false, play_count: PLAYS.length },
  ],
  plays: PLAYS,
  drawn_accounting: {},
  geometry_notice: 'Modeled schematic geometry, not measured tracking.',
}

const SCENARIOS = [
  { scenario_id: 'BASE', label: 'Base' },
  { scenario_id: 'PASS_HEAVY', label: 'Pass-heavy' },
] as unknown as NflPlayScenario[]

function mount(overrides: Partial<Parameters<typeof SimulationPopulation>[0]> = {}) {
  return render(
    <SimulationPopulation
      population={POPULATION}
      scenarios={SCENARIOS}
      scenarioId="BASE"
      onScenarioChange={() => {}}
      loading={false}
      error={null}
      {...overrides}
    />,
  )
}

describe('SimulationPopulation', () => {
  it('states the drawn count against the simulated count, never merging them', () => {
    mount()
    const coverage = document.querySelector('.pop__coverage')!
    expect(coverage.textContent).toMatch(/6/)
    expect(coverage.textContent).toMatch(/1,000 simulated games/)
    // The drawable count is the plays it has, not the population's play count.
    expect(coverage.textContent).toContain('12')
    expect(coverage.textContent).not.toContain('152,149')
  })

  it('keeps the population block on its own denominators', () => {
    mount()
    const block = document.querySelector('.pop-stats-block--population')!
    // 1,000-game figures live here and here only.
    expect(within(block as HTMLElement).getByText(/64,579 attempts/)).toBeInTheDocument()
    expect(within(block as HTMLElement).getByText(/over 123,313 plays/)).toBeInTheDocument()

    const drawn = document.querySelector('.pop-stats-block:not(.pop-stats-block--population)')!
    expect(drawn.textContent).not.toContain('64,579')
    expect(drawn.textContent).not.toContain('123,313')
  })

  it('computes completion rate over attempts, excluding sacks and scrambles', async () => {
    mount()
    // 6 completions of 7 attempts (6 complete + 1 interception). The sack is a
    // dropback, so counting it would give 6/8 and understate the rate.
    await vi.waitFor(() => {
      const drawn = document.querySelector('.pop-stats-block')!
      expect(drawn.textContent).toContain('6 of 7 attempts')
    })
  })

  it('moves its statistics when the play-type filter changes', async () => {
    const user = userEvent.setup()
    mount()
    await vi.waitFor(() => {
      expect(document.querySelector('.pop-stats-block')!.textContent).toContain('6 of 7 attempts')
    })

    const playType = screen.getByRole('group', { name: 'Play type' })
    await user.click(within(playType).getByRole('button', { name: 'Run' }))

    await vi.waitFor(() => {
      const drawn = document.querySelector('.pop-stats-block')!
      // No attempts in a run-only selection, so the rate is withheld rather
      // than reported as zero.
      expect(drawn.textContent).toContain('0 of 0 attempts')
      expect(drawn.textContent).toContain('0.0% pass')
    })
  })

  it('does not report a rate when its denominator is empty', async () => {
    const user = userEvent.setup()
    mount()
    const playType = screen.getByRole('group', { name: 'Play type' })
    await user.click(within(playType).getByRole('button', { name: 'Run' }))
    await vi.waitFor(() => {
      const rows = [...document.querySelectorAll('.pop-stats-block dd')].map((n) => n.textContent ?? '')
      const completion = rows.find((text) => text.includes('of 0 attempts'))
      expect(completion?.startsWith('—')).toBe(true)
    })
  })

  it('offers every scenario and reports the choice upward', async () => {
    const user = userEvent.setup()
    const onScenarioChange = vi.fn()
    mount({ onScenarioChange })
    await user.selectOptions(screen.getByLabelText(/scenario/i), 'PASS_HEAVY')
    expect(onScenarioChange).toHaveBeenCalledWith('PASS_HEAVY')
  })
})
