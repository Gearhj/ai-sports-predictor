/**
 * Track routing for the two-engine simulator.
 *
 * The guarantees under test: the default stays Gen 1, ?track=v18 shows the
 * research-shadow replay, a v18 request without a persisted path fails honestly
 * instead of borrowing Gen 1 evidence, and the two engines' numbers never mix.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GameSimulationPage } from './GameSimulationPage'
import { endpoints } from '../services/api'
import type { ShadowComparison, ShadowPath } from '../types/shadow'

vi.mock('echarts-for-react', () => ({
  default: ({ style }: { style?: Record<string, unknown> }) => (
    <div data-testid="echarts-placeholder" style={style} />
  ),
}))

const GAME_ID = 'game-1'

/** Gen 1: 20,000 draws, final scores only. */
const GEN1 = {
  data_mode: 'live',
  simulation_available: true,
  unavailable_reason: null,
  identity: {
    generated_at: '2026-09-07T22:06:34Z', forecast_version: '2', lifecycle: 'EARLY',
    model_name: 'asp_mlb_ensemble', model_version: '2.0.0',
    development_note: 'Development model.', feature_snapshot_reference: 'snap-1',
  },
  official_win_probability: { home: 0.4566, away: 0.5434 },
  means: { total_runs: 8.9, home_runs: 4.35, away_runs: 4.55 },
  extra_inning_probability: 0.09,
  inputs: { simulations: 20000, seed: 20250601 },
  play_by_play: { available: false, reason: 'Gen 1 draws final scores directly.' },
  score_distribution: [], total_runs_distribution: [], margin_distribution: [],
  representative_outcomes: [], outcome_frequencies: {},
} as never

/** V18: 5,000 paths, sequential. Deliberately different numbers. */
const COMPARISON: ShadowComparison = {
  schema_version: 'AISP_MLB_V18_SHADOW_COMPARISON_V1',
  game_id: GAME_ID, home_team: 'ATH', away_team: 'TOR',
  scheduled_start: '2026-09-08T02:05:00Z', event_status: 'SCHEDULED',
  official: null,
  notes: 'V18 is a research-shadow challenger.',
  shadows: [{
    track: 'RESEARCH_SHADOW', engine_id: 'MLB_V18_WHOLE_GAME',
    label: 'V18 whole-game challenger (research shadow)',
    forecast_id: 'mlb-v18-1', bundle_id: 'mlb-v18-whole-game-abc',
    bundle_sha256: 'a'.repeat(64), evaluation_contract: 'mlb-v18-acceptance-1',
    home_win_probability: 43.34, expected_home_runs: 3.4694, expected_away_runs: 4.423,
    generated_at: '2026-09-07T21:06:36Z', evidence_cutoff: '2026-09-07T21:06:36Z',
    pregame: true, simulation_count: 5000, master_seed: 20260907,
    monte_carlo_standard_error: 0.007008, monte_carlo_95_half_width: 0.013736,
    deterministic_replay_verified: true, illegal_state_count: 0, nontermination_count: 0,
    path_digest: 'd'.repeat(64), sequential_path_events: 2,
    lineup_modes: ['PROJECTED_PRIOR_COMPLETE_LINEUP'],
    sides: { AWAY: { starter: 'Test Starter' } },
    limitations: ['projected lineups are carried forward'],
    wagering_authority: false,
    population: {
      simulation_count: 5000, home_win_probability: 43.34, away_win_probability: 56.66,
      expected_home_runs: 3.4694, expected_away_runs: 4.423, expected_total_runs: 7.8924,
      expected_run_differential: -0.9536, one_run_probability: 0.3094,
      extra_inning_probability: 0.1074, pitching_change_rate_home: 1,
      pitching_change_rate_away: 0.9998, monte_carlo_standard_error: 0.007008,
      monte_carlo_95_half_width: 0.013736, total_run_distribution: null,
    },
    settlement: null, settlement_state: 'PENDING',
    settlement_note: 'The event has no persisted final outcome yet.',
    paired_with_official: false, pairing_note: null,
    difference_vs_official: null, difference_orientation: 'V18_MINUS_GEN1',
    simulation_indistinguishable: null,
  }],
}

const PATH: ShadowPath = {
  schema_version: 'AISP_MLB_V18_SEQUENTIAL_PATH_V1',
  forecast_id: 'mlb-v18-1', game_id: GAME_ID, engine_id: 'MLB_V18_WHOLE_GAME',
  bundle_sha256: 'a'.repeat(64), path_digest: 'd'.repeat(64),
  master_seed: 20260907, simulation_count: 5000, path_count: 1,
  paths: [[
    {
      pa: 1, inning: 1, half: 'TOP', battingOrderPosition: 1, outcome: 'STRIKEOUT',
      outsBefore: 0, outsAfter: 1, baseMaskBefore: 0, baseMaskAfter: 0, runsScored: 0,
      homeScoreAfter: 0, awayScoreAfter: 0, pitcherState: 'STARTER', pitchingChange: false,
    },
    {
      pa: 2, inning: 1, half: 'TOP', battingOrderPosition: 2, outcome: 'SINGLE',
      outsBefore: 1, outsAfter: 1, baseMaskBefore: 0, baseMaskAfter: 1, runsScored: 0,
      homeScoreAfter: 0, awayScoreAfter: 0, pitcherState: 'STARTER', pitchingChange: false,
    },
  ]],
}

function mount(search: string, { shadows = true, path = true } = {}) {
  vi.spyOn(endpoints, 'gameSimulation').mockResolvedValue(GEN1)
  vi.spyOn(endpoints, 'marketPulseGame').mockRejectedValue(new Error('no pulse'))
  vi.spyOn(endpoints, 'gameShadowForecasts').mockResolvedValue(
    shadows ? COMPARISON : { ...COMPARISON, shadows: [] },
  )
  vi.spyOn(endpoints, 'shadowForecastPath').mockImplementation(
    path
      ? () => Promise.resolve(PATH)
      : () => Promise.reject(new Error('no path')),
  )
  render(
    <MemoryRouter initialEntries={[`/games/${GAME_ID}/simulation${search}`]}>
      <Routes>
        <Route path="/games/:gameId/simulation" element={<GameSimulationPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('simulator track routing', () => {
  it('defaults to the official Gen 1 track and labels it as a distribution', async () => {
    mount('')
    expect(await screen.findByRole('heading', { level: 1, name: /Official Gen 1 · Final-score distribution/ }))
      .toBeInTheDocument()
    // Gen 1 is described as a distribution, never as play-by-play.
    expect(document.querySelector('.track-scope')).toHaveTextContent(
      /Gen 1 draws final scores directly\. It has no innings, plate appearances, runners, outs or pitching changes/,
    )
    expect(screen.queryByRole('heading', { name: /Sequential game replay/i })).not.toBeInTheDocument()
  })

  it('shows the V18 replay when ?track=v18 is requested', async () => {
    mount('?track=v18')
    expect(await screen.findByRole('heading', { level: 1, name: /V18 Monte Carlo Diamond Theater/ }))
      .toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByTestId('diamond-theater')).toBeInTheDocument())
    expect(screen.getByRole('group', { name: /scoreboard/i })).toHaveTextContent('PA 1 of 2')
  })

  it('keeps the two engines’ population figures apart', async () => {
    mount('?track=v18')
    await screen.findByRole('heading', { name: /Simulation population · 5,000 games/ })
    // V18's own frozen results are shown...
    expect(screen.getAllByText('43.3%').length).toBeGreaterThan(0)
    expect(screen.getByText('7.89')).toBeInTheDocument()
    // ...and Gen 1's 20,000-draw evidence appears nowhere on this track.
    expect(screen.queryByText(/20,000/)).not.toBeInTheDocument()
    expect(screen.queryByText('45.7%')).not.toBeInTheDocument()
    expect(screen.queryByText(/Seed 20250601/)).not.toBeInTheDocument()
  })

  it('offers no V18 scenario controls and explains why', async () => {
    mount('?track=v18')
    expect(await screen.findByText(/V18 what-if scenarios require a new Model Studio simulation artifact/i))
      .toBeInTheDocument()
    expect(screen.queryByLabelText(/temperature/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/park factor/i)).not.toBeInTheDocument()
  })

  it('switches tracks without claiming either forecast changed', async () => {
    const user = userEvent.setup()
    mount('')
    await screen.findByRole('heading', { level: 1, name: /Official Gen 1/ })
    const selector = await screen.findByRole('navigation', { name: /simulation track/i })
    expect(screen.getByText(/Switching tracks changes only which engine/i)).toBeInTheDocument()

    await user.click(within(selector).getByRole('button', { name: /V18 Research Shadow/ }))
    expect(await screen.findByRole('heading', { level: 1, name: /V18 Monte Carlo Diamond Theater/ }))
      .toBeInTheDocument()
  })

  it('fails honestly when a V18 track is requested with no registered shadow', async () => {
    mount('?track=v18', { shadows: false })
    expect(await screen.findByRole('heading', { name: /No research-shadow replay exists/i }))
      .toBeInTheDocument()
    expect(screen.getByText(/Nothing has been reconstructed from Gen 1/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Show the official Gen 1 distribution/i }))
      .toHaveAttribute('href', `/games/${GAME_ID}/simulation`)
  })

  it('states when a shadow exists but its path does not', async () => {
    mount('?track=v18', { path: false })
    expect(await screen.findByText(/No sequential path is persisted for this research-shadow forecast/i))
      .toBeInTheDocument()
    expect(screen.queryByTestId('diamond-theater')).not.toBeInTheDocument()
  })

  it('hides the selector when only Gen 1 exists', async () => {
    mount('', { shadows: false })
    await screen.findByRole('heading', { level: 1, name: /Official Gen 1/ })
    expect(screen.queryByRole('navigation', { name: /simulation track/i })).not.toBeInTheDocument()
  })
})
